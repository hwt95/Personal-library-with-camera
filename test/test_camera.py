import threading

import cv2
import numpy as np

from app.imaging import zoom_frame
from app.workers import CameraWorker


def test_zoom_is_centered_and_used_for_capture():
    frame = np.arange(120 * 160 * 3, dtype=np.uint16).reshape(120, 160, 3)
    expected = frame[30:90, 40:120]
    assert np.array_equal(zoom_frame(frame, 2), expected)
    assert zoom_frame(frame, 4).shape == (30, 40, 3)
    assert np.array_equal(zoom_frame(frame, .5), frame)
    camera = CameraWorker()
    camera._latest = frame
    camera.set_zoom(2)
    assert np.array_equal(camera.latest(), expected)
    # Changing the UI's copy must not damage the acquisition frame.
    result = camera.latest()
    result[:] = 0
    assert frame.any()
    camera.set_zoom(1)
    assert np.array_equal(camera.latest(), frame)


class FakeCamera:
    def __init__(self, *, supported=True):
        self.supported = supported
        self.calls = []
        self.properties = {}
        self.reads = 0
        self.released = False

    def isOpened(self):
        return True

    def set(self, prop, value):
        self.calls.append((prop, value, threading.get_ident()))
        if prop == cv2.CAP_PROP_AUTOFOCUS and not self.supported:
            return False
        self.properties[prop] = value
        return True

    def get(self, prop):
        return self.properties.get(prop, 0)

    def read(self):
        self.reads += 1
        if self.reads == 3:
            self.worker.requestInterruption()
        return True, np.zeros((120, 160, 3), np.uint8)

    def release(self):
        self.released = True


def test_focus_runs_on_capture_thread_and_refocus_reenables(monkeypatch):
    fake = FakeCamera()
    worker = CameraWorker()
    fake.worker = worker
    worker.scanning = False
    worker.set_autofocus(True, refocus=True)
    monkeypatch.setattr(cv2, "VideoCapture", lambda *args: fake)
    worker.start()
    assert worker.wait(3000)
    focus_calls = [call for call in fake.calls if call[0] == cv2.CAP_PROP_AUTOFOCUS]
    assert [call[1] for call in focus_calls] == [0, 1]
    assert all(call[2] != threading.get_ident() for call in fake.calls)
    assert fake.released


def test_unsupported_focus_is_reported_and_does_not_stop_capture(monkeypatch):
    fake = FakeCamera(supported=False)
    worker = CameraWorker()
    states = []
    worker.focus_changed.connect(states.append)
    worker.apply_focus(fake)
    assert states[-1]["supported"] is False
    assert "不支持" in states[-1]["note"]
    fake.worker = worker
    worker.scanning = False
    monkeypatch.setattr(cv2, "VideoCapture", lambda *args: fake)
    worker.start()
    assert worker.wait(3000)
    assert fake.reads == 3
    assert fake.released
