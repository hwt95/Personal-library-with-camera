from .i18n import tr
from pathlib import Path
import sys
import threading

import cv2
import numpy as np


def model_path():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "models" / "document_orientation" / "inference.onnx"
    return Path(__file__).resolve().parents[2] / "deps" / "models" / "document_orientation" / "inference.onnx"


def decode_image(blob):
    frame = cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_COLOR)
    if frame is None or not frame.size:
        raise ValueError(tr('无法读取图片，请选择 JPEG 或 PNG 图片'))
    return frame


def encode_cover(frame):
    if max(frame.shape[:2]) > 1280:
        ratio = 1280 / max(frame.shape[:2])
        frame = cv2.resize(frame, None, fx=ratio, fy=ratio, interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise ValueError(tr('封面图片编码失败'))
    return encoded.tobytes()


def zoom_frame(frame, zoom=1.0):
    """Center crop without inventing pixels; views scale this same crop to fit.

    Preview, scanning and the captured photo all use the identical field of view.
    """
    factor = max(1.0, min(4.0, float(zoom)))
    if factor == 1.0:
        return frame
    h, w = frame.shape[:2]
    ch, cw = max(1, round(h / factor)), max(1, round(w / factor))
    top, left = (h - ch) // 2, (w - cw) // 2
    return frame[top:top+ch, left:left+cw].copy()


def order_corners(points):
    points = np.asarray(points, np.float32).reshape(4, 2)
    # Angular order works for diamond-shaped covers where sum/difference ties fail.
    center = points.mean(axis=0)
    points = points[np.argsort(np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0]))]
    return np.roll(points, -int(np.argmin(points.sum(axis=1))), axis=0)


def valid_corners(points):
    points = np.asarray(points, np.float32).reshape(4, 2)
    return bool(cv2.isContourConvex(points) and abs(cv2.contourArea(points)) > 100 and
                min(np.linalg.norm(points[i] - points[(i+1) % 4]) for i in range(4)) >= 10)


def detect_corners(frame):
    h, w = frame.shape[:2]
    ratio = min(1, 900 / max(h, w))
    small = cv2.resize(frame, None, fx=ratio, fy=ratio)
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    # Include color channels: different cover/background colors can have the same
    # luminance. A single grayscale threshold misses their otherwise clear border.
    channels = [gray, *cv2.split(cv2.GaussianBlur(small, (5, 5), 0))]
    edge = cv2.Canny(gray, 30, 100)
    masks = []
    for channel in channels:
        channel_edge = cv2.Canny(channel, 25, 85)
        low, high = np.percentile(channel, (2, 98))
        if high - low >= 6:
            enhanced = np.clip((channel.astype(np.float32) - low) * (255 / (high-low)), 0, 255).astype(np.uint8)
            channel_edge = cv2.bitwise_or(channel_edge, cv2.Canny(enhanced, 40, 120))
        edge = cv2.bitwise_or(edge, channel_edge)
        _, binary = cv2.threshold(channel, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        masks.extend((binary, cv2.bitwise_not(binary)))
    masks.insert(0, cv2.morphologyEx(edge, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)))
    supported_edge = cv2.dilate(edge, np.ones((5, 5), np.uint8))
    candidates = []
    sh, sw = gray.shape
    seen = set()
    for mask in masks:
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            contour_area = abs(cv2.contourArea(contour)) / (sh * sw)
            if not .07 < contour_area < .96:
                continue
            for tolerance in (.015, .025, .04):
                polygon = cv2.approxPolyDP(contour, tolerance * cv2.arcLength(contour, True), True)
                if len(polygon) != 4 or not cv2.isContourConvex(polygon):
                    continue
                points = order_corners(polygon)
                identity = tuple((points / 5).round().astype(int).ravel())
                if identity in seen:
                    continue
                seen.add(identity)
                area = abs(cv2.contourArea(points)) / (sh * sw)
                sides = [np.linalg.norm(points[i] - points[(i+1) % 4]) for i in range(4)]
                if not .07 < area < .94 or min(sides) / max(sides) < .18:
                    continue
                border = np.zeros_like(gray)
                cv2.polylines(border, [points.astype(np.int32)], True, 255, 2)
                support = float(np.count_nonzero(supported_edge[border > 0])) / max(1, np.count_nonzero(border))
                # A real cover has an edge on all sides; reject unsupported guesses.
                if support < .5:
                    continue
                margins = np.minimum(points / [sw, sh], 1 - points / [sw, sh])
                near_frame = margins.min(axis=1) < .035
                border_penalty = .55 * float(near_frame.mean())
                center_distance = np.linalg.norm((points.mean(axis=0) - [sw/2, sh/2]) / [sw, sh])
                score = .45 * min(area / .65, 1) + .4 * support - .7 * center_distance - border_penalty
                candidates.append((score, points / ratio))
    if candidates:
        score, points = max(candidates, key=lambda pair: pair[0])
        if score > .38:
            return points, True
    # Uncertain detection preserves the entire frame for manual adjustment.
    return np.array([[0, 0], [w-1, 0], [w-1, h-1], [0, h-1]], np.float32), False


def perspective_crop(frame, points):
    points = np.asarray(points, np.float32).reshape(4, 2)
    if not valid_corners(points):
        raise ValueError(tr('四角不能交叉或重合，请重新调整'))
    tl, top_right, br, bl = points
    width = int(max(np.linalg.norm(top_right-tl), np.linalg.norm(br-bl)))
    height = int(max(np.linalg.norm(bl-tl), np.linalg.norm(br-top_right)))
    width, height = max(10, width), max(10, height)
    target = np.array([[0, 0], [width-1, 0], [width-1, height-1], [0, height-1]], np.float32)
    return cv2.warpPerspective(frame, cv2.getPerspectiveTransform(points, target), (width, height))


class Orientation:
    _session = None
    _lock = threading.Lock()

    @classmethod
    def predict(cls, frame):
        import onnxruntime as ort
        with cls._lock:
            if cls._session is None:
                options = ort.SessionOptions()
                options.intra_op_num_threads = 2
                options.log_severity_level = 3
                cls._session = ort.InferenceSession(str(model_path()), sess_options=options,
                                                   providers=["CPUExecutionProvider"])
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        factor = 256 / min(h, w)
        rgb = cv2.resize(rgb, (round(w * factor), round(h * factor)))
        y, x = (rgb.shape[0]-224)//2, (rgb.shape[1]-224)//2
        sample = rgb[y:y+224, x:x+224].astype(np.float32) / 255
        sample = (sample - np.array([.485, .456, .406], np.float32)) / np.array([.229, .224, .225], np.float32)
        sample = np.ascontiguousarray(sample.transpose(2, 0, 1)[None])
        probabilities = np.asarray(cls._session.run(None, {cls._session.get_inputs()[0].name: sample})[0]).reshape(-1)
        if not np.isclose(probabilities.sum(), 1, atol=.01) or probabilities.min() < 0:
            probabilities = np.exp(probabilities - probabilities.max())
            probabilities /= probabilities.sum()
        best = int(probabilities.argmax())
        return best * 90, float(probabilities[best])


def detect_cover_corners(frame, *, fast=False):
    try:
        from .boundary import detect
        points = detect(frame, fast=fast)
        if points is not None:
            return points, True
    except Exception:
        # The classical detector remains available if a model is damaged.
        pass
    return detect_corners(frame)


def process_cover(frame, points=None, auto_orientation=True, rotation=0):
    detected = True
    if points is None:
        points, detected = detect_cover_corners(frame)
    cropped = perspective_crop(frame, points)
    angle, confidence = 0, 0.0
    note = tr('已识别封面边框；可拖动四角调整') if detected else tr('边框不确定，已保留完整照片，可重新识别或拖动四角')
    if auto_orientation:
        try:
            angle, confidence = Orientation.predict(cropped)
            if confidence >= .85:
                # Paddle labels describe clockwise input rotation; undo it CCW.
                rotation = angle // 90
                note += tr('；方向 {v0}°，置信度 {v1:.0%}', v0=(angle), v1=(confidence))
            else:
                note += tr('；朝向不确定，请手动旋转')
        except Exception:
            note += tr('；本地方向校正不可用，可手动旋转')
    result = np.ascontiguousarray(np.rot90(cropped, rotation % 4))
    return {"points": points, "image": result, "rotation": rotation % 4, "note": note, "detected": detected,
            "orientation_angle": angle, "confidence": confidence}
