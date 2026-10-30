"""Local document-corner models and bounded OpenCV line refinement.
Ported from the validated project prototype; model provenance is bundled.
"""
from pathlib import Path
import sys
import threading
import cv2
import numpy as np
import onnxruntime as ort
from .imaging import order_corners, valid_corners

MODELS = (Path(sys._MEIPASS) / "models" if getattr(sys,"frozen",False) else Path(__file__).resolve().parents[2] / "deps/models") / "book_boundary"
def session(path):
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    opts.inter_op_num_threads = 1
    opts.log_severity_level = 3
    return ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])

def polygon_iou(first, second):
    a, b = order_corners(first), order_corners(second)
    intersection, _ = cv2.intersectConvexConvex(a, b)
    union = abs(cv2.contourArea(a)) + abs(cv2.contourArea(b)) - intersection
    return float(intersection / union) if union > 0 else 0.0

class DocAligner:
    """Reimplementation of upstream BGR/255 + resized heatmap/Otsu centroids.

    Reference and Apache-2.0 license are saved next to the downloaded weights.
    No center crop; preserve every part of the photograph.
    """
    def __init__(self, model):
        self.model = model
        self.session = session(MODELS / f"{model}_h_e_bifpn_256_fp32.onnx")

    def __call__(self, frame):
        h, w = frame.shape[:2]
        x = cv2.resize(frame, (256, 256)).transpose(2, 0, 1)[None].astype(np.float32) / 255
        heatmap = self.session.run(["heatmap"], {"img": x})[0][0]
        points, peaks = [], []
        for heat in heatmap:
            peaks.append(float(heat.max()))
            heat = cv2.resize(heat, (w, h))
            heat[heat < .3] = 0
            mask = (heat * 255).astype(np.uint8)
            _, mask = cv2.threshold(mask, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                return None, {"heatmap_peaks": peaks, "reason": "missing corner"}
            m = cv2.moments(max(contours, key=cv2.contourArea))
            if m["m00"] <= 0:
                return None, {"heatmap_peaks": peaks, "reason": "empty corner"}
            points.append([m["m10"] / m["m00"], m["m01"] / m["m00"]])
        points = order_corners(points)
        if not valid_corners(points):
            return None, {"heatmap_peaks": peaks, "reason": "invalid quadrilateral"}
        return points, {"heatmap_peaks": peaks}

class Cascade:
    """Recall-oriented alternative: first plausible prediction, then retry.

    This deliberately lacks Hybrid's two-model agreement requirement. Always
    expose the overlay for user confirmation; a found quad is not proof of a book.
    """
    def __init__(self, t8=None, lcnet=None, refine=False):
        self.models = {"fastvit_t8": t8 or DocAligner("fastvit_t8"),
                       "lcnet100": lcnet or DocAligner("lcnet100")}
        self.refine = refine

    def __call__(self, frame):
        h, w = frame.shape[:2]
        attempts = []
        for rotation in (0, 1, 2, 3):
            sample = np.ascontiguousarray(np.rot90(frame, rotation)) if rotation else frame
            for name, model in self.models.items():
                points, extra = model(sample)
                attempts.append({"model": name, "rotation_ccw": rotation * 90, "found": points is not None})
                if points is None:
                    continue
                x, y = points[:, 0].copy(), points[:, 1].copy()
                if rotation == 1:
                    points = np.column_stack((w - 1 - y, x))
                elif rotation == 2:
                    points = np.column_stack((w - 1 - x, h - 1 - y))
                elif rotation == 3:
                    points = np.column_stack((y, h - 1 - x))
                points = order_corners(points)
                if not .04 < abs(cv2.contourArea(points)) / (h * w) < .90:
                    continue
                details = {"attempts": attempts, "selected_model": name,
                           "rotation_ccw": rotation * 90, "confirmation_required": True}
                if self.refine:
                    # A second pass sees more cover pixels and fewer distractors.
                    lo, hi = points.min(axis=0), points.max(axis=0)
                    margin = (hi - lo) * .08
                    lo = np.maximum(np.floor(lo - margin), 0).astype(int)
                    hi = np.minimum(np.ceil(hi + margin), [w, h]).astype(int)
                    region = frame[lo[1]:hi[1], lo[0]:hi[0]]
                    local, _ = self.models["fastvit_t8"](region)
                    details["local_second_pass"] = False
                    if local is not None:
                        local += lo
                        if polygon_iou(local, points) >= .90:
                            points = local
                            details["local_second_pass"] = True
                    refined, refinement = refine_lines(frame, points)
                    details.update(refinement)
                    return refined, details
                return points, details
        return None, {"attempts": attempts, "reason": "no plausible quadrilateral"}

def refine_lines(frame, points):
    """Fit local line segments near each learned side; bounded safe fallback.

    No image-wide contour competition and no annotation-derived search region.
    """
    h, w = frame.shape[:2]
    scale = min(1., 1200 / max(h, w))
    small = cv2.resize(frame, None, fx=scale, fy=scale)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    segments = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD).detect(gray)[0]
    if segments is None:
        return points, {"refined": False, "refinement_reason": "no local lines"}
    segments = segments.reshape(-1, 2, 2)
    delta = segments[:, 1] - segments[:, 0]
    length = np.linalg.norm(delta, axis=1)
    direction = delta / np.maximum(length[:, None], 1e-6)
    middle = segments.mean(axis=1)
    p = points * scale
    shortest = min(np.linalg.norm(p[(i+1) % 4] - p[i]) for i in range(4))
    band = max(8., .04 * shortest)
    lines, support = [], []
    for i in range(4):
        a, b = p[i], p[(i+1) % 4]
        vector = b - a
        side_length = np.linalg.norm(vector)
        unit = vector / side_length
        normal = np.array([-unit[1], unit[0]])
        distance = np.abs((middle - a) @ normal)
        position = (middle - a) @ unit / side_length
        parallel = np.abs(direction @ unit)
        ok = (distance < band) & (position > .03) & (position < .97) & (parallel > np.cos(np.deg2rad(8))) & (length > .08 * side_length)
        indices = np.flatnonzero(ok)
        if not len(indices):
            return points, {"refined": False, "refinement_reason": "side lacks line support"}
        best = max(indices, key=lambda j: length[j] * np.exp(-distance[j] / band))
        anchor, axis = middle[best], direction[best]
        n = np.array([-axis[1], axis[0]])
        consistent = ok & (np.abs((middle-anchor) @ n) < 3) & (np.abs(direction @ axis) > np.cos(np.deg2rad(3)))
        cloud = []
        for j in np.flatnonzero(consistent):
            cloud.extend(np.linspace(segments[j, 0], segments[j, 1], max(2, round(float(length[j] / 4)))))
        coverage = float(length[consistent].sum() / side_length)
        support.append(coverage)
        if coverage < .25:
            return points, {"refined": False, "refinement_reason": "insufficient side coverage"}
        vx, vy, x, y = cv2.fitLine(np.array(cloud, np.float32), cv2.DIST_HUBER, 0, .01, .01).ravel()
        normal = np.array([-vy, vx])
        lines.append((normal, float(normal @ [x, y])))
    refined = []
    for i in range(4):
        n0, d0 = lines[(i-1) % 4]
        n1, d1 = lines[i]
        matrix = np.array([n0, n1])
        if abs(np.linalg.det(matrix)) < .1:
            return points, {"refined": False, "refinement_reason": "parallel sides"}
        refined.append(np.linalg.solve(matrix, [d0, d1]) / scale)
    refined = order_corners(refined)
    max_shift = float(np.linalg.norm(refined - points, axis=1).max())
    if (not valid_corners(refined) or max_shift > 2 * band / scale or polygon_iou(refined, points) < .9 or
            (refined < 0).any() or (refined[:, 0] >= w).any() or (refined[:, 1] >= h).any()):
        return points, {"refined": False, "refinement_reason": "bounded change rejected"}
    return refined, {"refined": True, "max_refinement_px": max_shift, "side_coverage": support}
_detector = None
_lock = threading.Lock()

def detect(frame, *, fast=False):
    global _detector
    if min(frame.shape[:2]) < 32 or float(frame.std()) < 2:
        return None
    with _lock:
        if _detector is None:
            _detector = Cascade(refine=True)
        if fast:
            points, _ = _detector.models["fastvit_t8"](frame)
        else:
            points, _ = _detector(frame)
        if points is not None:
            h,w=frame.shape[:2]
            if not .04 < abs(cv2.contourArea(points))/(h*w) < .90 or (points < 0).any() or (points[:,0]>=w).any() or (points[:,1]>=h).any():
                return None
        return points
