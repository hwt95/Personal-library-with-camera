import asyncio
import threading
import time

import cv2
from PySide6.QtCore import QThread, Signal
import zxingcpp

from .domain import normalize_isbn
from .providers.catalog import lookup
from .imaging import process_cover, detect_corners, zoom_frame


class LookupWorker(QThread):
    result = Signal(int, object, str)
    progress = Signal(int, str)

    def __init__(self, token, isbn, source, key="", detail_url="", parent=None, lookup_func=lookup):
        super().__init__(parent)
        self.token, self.isbn, self.source, self.key, self.detail_url = token, isbn, source, key, detail_url
        self.lookup_func = lookup_func
        self._loop = self._task = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with self._lock:
                self._loop = loop
                self._task = loop.create_task(self.lookup_func(
                    self.isbn, self.source, self.key, self.detail_url,
                    status=lambda message: self.progress.emit(self.token, message)))
                if self._cancel.is_set():
                    self._task.cancel()
            records, note = loop.run_until_complete(self._task)
            if not self._cancel.is_set():
                self.result.emit(self.token, records, note)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            if not self._cancel.is_set():
                self.result.emit(self.token, [], f"查询未完成（{type(exc).__name__}），可手工录入")
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            with self._lock:
                self._loop = self._task = None
                loop.close()

    def cancel(self):
        self._cancel.set()
        with self._lock:
            if self._loop is not None and self._task is not None:
                self._loop.call_soon_threadsafe(self._task.cancel)


class CameraWorker(QThread):
    barcode = Signal(str)
    message = Signal(str)
    focus_changed = Signal(object)

    def __init__(self, index=0, parent=None):
        super().__init__(parent)
        self.index = index
        self.scanning = True
        self._latest = None
        self._lock = threading.Lock()
        self._zoom = 1.0
        self._autofocus = True
        self._focus_pending = True
        self._refocus = False

    def set_zoom(self, factor):
        with self._lock:
            self._zoom = max(1.0, min(4.0, float(factor)))

    def set_autofocus(self, enabled, *, refocus=False):
        # Only the capture thread may touch the native camera handle.
        with self._lock:
            self._autofocus = bool(enabled)
            self._focus_pending = True
            self._refocus = bool(refocus)

    def apply_focus(self, camera):
        with self._lock:
            if not self._focus_pending:
                return
            enabled, refocus = self._autofocus, self._refocus
            self._focus_pending = self._refocus = False
        try:
            if refocus:
                camera.set(cv2.CAP_PROP_AUTOFOCUS, 0)
            accepted = bool(camera.set(cv2.CAP_PROP_AUTOFOCUS, int(enabled)))
            actual = camera.get(cv2.CAP_PROP_AUTOFOCUS)
            confirmed = actual >= .5 if enabled else actual == 0
            if accepted and confirmed:
                note = "自动对焦已开启" if enabled else "自动对焦已关闭，保持当前焦点"
            elif accepted:
                note = "已请求自动对焦，设备未确认状态" if enabled else "已请求关闭自动对焦"
            elif actual >= .5:
                note = "自动对焦由设备控制"
            else:
                note = "此摄像头不支持软件自动对焦，请调整拍摄距离"
            self.focus_changed.emit({"supported": accepted, "enabled": bool(actual >= .5), "note": note})
        except cv2.error:
            self.focus_changed.emit({"supported": False, "enabled": False, "note": "此设备未提供自动对焦控制，请调整拍摄距离"})

    def latest(self):
        with self._lock:
            frame = self._latest.copy() if self._latest is not None else None
            zoom = self._zoom
        return zoom_frame(frame, zoom) if frame is not None else None

    def run(self):
        camera = None
        try:
            indices = [self.index] if self.index >= 0 else [0, 1, 2, 3]
            for index in indices:
                if self.isInterruptionRequested():
                    return
                candidate = cv2.VideoCapture(index, cv2.CAP_DSHOW)
                if candidate.isOpened():
                    camera = candidate
                    break
                candidate.release()
            if camera is None:
                self.message.emit("摄像头不可用；可以手工输入 ISBN、导入封面")
                return
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 960)
            self.message.emit("摄像头已开启，请将条码置于画面中央")
            last_scan, failures = 0.0, 0
            while not self.isInterruptionRequested():
                self.apply_focus(camera)
                ok, frame = camera.read()
                if not ok:
                    failures += 1
                    if failures > 30:
                        self.message.emit("摄像头连接中断，可以重新开启")
                        break
                    self.msleep(30)
                    continue
                failures = 0
                with self._lock:
                    self._latest = frame
                    zoom = self._zoom
                if self.scanning and time.monotonic() - last_scan > .45:
                    last_scan = time.monotonic()
                    found = ""
                    for code in zxingcpp.read_barcodes(zoom_frame(frame, zoom), formats=zxingcpp.BarcodeFormat.EAN13):
                        try:
                            found = normalize_isbn(code.text)
                            if found:
                                break
                        except ValueError:
                            pass
                    self.barcode.emit(found)
                self.msleep(20)
        except Exception as exc:
            self.message.emit(f"摄像头暂不可用（{type(exc).__name__}），仍可手工录入")
        finally:
            if camera:
                camera.release()
            with self._lock:
                self._latest = None


class CoverWorker(QThread):
    result = Signal(object)
    error = Signal(str)

    def __init__(self, frame, points=None, auto_orientation=True, rotation=0, parent=None):
        super().__init__(parent)
        self.arguments = (frame.copy(), None if points is None else points.copy(), auto_orientation, rotation)

    def run(self):
        try:
            self.result.emit(process_cover(*self.arguments))
        except Exception as exc:
            self.error.emit(f"图像处理失败：{exc}")


class BorderWorker(QThread):
    """Lightweight live preview detection; never loads the orientation model."""
    result = Signal(int, object)

    def __init__(self, frame, token, parent=None):
        super().__init__(parent)
        self.frame, self.token = frame.copy(), token

    def run(self):
        try:
            points, found = detect_corners(self.frame)
            self.result.emit(self.token, {"points": points if found else None, "found": found,
                                          "shape": self.frame.shape[:2]})
        except Exception:
            self.result.emit(self.token, {"points": None, "found": False, "shape": self.frame.shape[:2]})
        finally:
            self.frame = None
