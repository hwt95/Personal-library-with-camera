import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QDialogButtonBox, QLineEdit, QTreeWidget, QTreeWidgetItem, QMessageBox)

from .widgets import ImageView, CameraControls, row, button
from .imaging import encode_cover
from .workers import CoverWorker, BorderWorker


class CandidateDialog(QDialog):
    def __init__(self, candidates, parent, title="选择书目"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(740, 380)
        self.candidates = candidates
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("找到多条候选，请核对版本与出版社："))
        self.items = QListWidget()
        for item in candidates:
            self.items.addItem("  ·  ".join(str(item.get(k, "")) for k in ("title", "authors", "publisher", "reference_pubdate") if item.get(k)))
        self.items.setCurrentRow(0)
        layout.addWidget(self.items)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        controls.accepted.connect(self.accept)
        controls.rejected.connect(self.reject)
        self.items.itemDoubleClicked.connect(self.accept)
        layout.addWidget(controls)

    def selected(self):
        return self.candidates[self.items.currentRow()] if self.items.currentRow() >= 0 else None


class ClassificationDialog(QDialog):
    def __init__(self, db, parent):
        super().__init__(parent)
        self.db, self.selected_code = db, ""
        self.setWindowTitle("中图分类字典")
        self.resize(650, 580)
        layout = QVBoxLayout(self)
        self.query = QLineEdit()
        self.query.setPlaceholderText("搜索分类号或名称；字典未收录的细分类可直接在录入页输入")
        layout.addWidget(self.query)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["分类号", "名称"])
        layout.addWidget(self.tree)
        layout.addWidget(button("采用所选分类", self.choose, primary=True))
        self.query.textChanged.connect(self.populate)
        self.tree.itemDoubleClicked.connect(self.choose)
        self.populate()

    def populate(self):
        self.tree.clear()
        records = self.db.classifications(self.query.text().strip())
        nodes = {r["code"]: QTreeWidgetItem([r["code"], r["name"]]) for r in records}
        for record in records:
            item = nodes[record["code"]]
            parent = nodes.get(record["parent_code"])
            if parent is not None:
                parent.addChild(item)
            else:
                self.tree.addTopLevelItem(item)
        if self.query.text():
            self.tree.expandAll()
        self.tree.resizeColumnToContents(0)

    def choose(self, *_):
        if self.tree.currentItem():
            self.selected_code = self.tree.currentItem().text(0)
            self.accept()


class CoverDialog(QDialog):
    def __init__(self, owner, frame=None):
        super().__init__(owner)
        self.owner = owner
        self.original, self.result_image, self.blob = frame, None, None
        self.rotation, self.worker, self.closed = 0, None, False
        self.border_busy, self.border_token, self.last_border_time = False, 0, 0.0
        self.preview_shape = None
        self.preview_zoom = owner.camera_zoom
        self.setWindowTitle("拍摄与校正封面 · 照片仅在本机处理")
        self.resize(1080, 740)
        layout = QVBoxLayout(self)
        self.status = QLabel("将封面四角放入画面，选择与封面颜色有区别的背景。空格拍摄，Esc 取消。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.camera_controls = CameraControls(owner)
        layout.addWidget(self.camera_controls)
        self.border_status = QLabel("OpenCV 自动识别边框：将封面完整放入取景范围")
        self.border_status.setObjectName("subtle")
        layout.addWidget(self.border_status)
        self.original_view = ImageView("正在等待摄像头画面…", editable=True, guide=True)
        self.result_view = ImageView("拍摄后显示校正预览")
        layout.addWidget(row(self.original_view, self.result_view), 1)
        self.capture_button = button("拍摄（空格）", self.capture, primary=True)
        self.retake_button = button("重拍", self.retake)
        self.redetect_button = button("自动识别边框", self.redetect)
        self.left_button = button("左转 90°", lambda: self.rotate(1))
        self.right_button = button("右转 90°", lambda: self.rotate(-1))
        self.use_button = button("采用封面（Enter）", self.use, primary=True)
        layout.addWidget(row(self.capture_button, self.retake_button, self.left_button,
                             self.right_button, self.use_button, button("取消", self.reject)))
        layout.addWidget(self.redetect_button)
        self.original_view.corners_changed.connect(lambda points: self.process(points, False))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.preview)
        self.timer.start(80)
        self.set_busy(False)
        if frame is not None:
            self.process()

    def set_busy(self, busy):
        self.capture_button.setEnabled(not busy and self.original is None)
        self.retake_button.setEnabled(not busy and self.original is not None)
        self.redetect_button.setEnabled(not busy and self.original is not None)
        self.camera_controls.setEnabled(not busy and self.original is None)
        self.left_button.setEnabled(not busy and self.result_image is not None)
        self.right_button.setEnabled(not busy and self.result_image is not None)
        self.use_button.setEnabled(not busy and self.result_image is not None)
        self.original_view.editable = not busy and self.original is not None

    def preview(self):
        if self.closed:
            return
        if self.original is None and self.owner.camera:
            frame = self.owner.camera.latest()
            if frame is not None:
                shape = frame.shape[:2]
                if self.preview_zoom != self.owner.camera_zoom or shape != self.preview_shape:
                    self.border_token += 1
                    self.original_view.points = None
                    self.preview_zoom = self.owner.camera_zoom
                    self.preview_shape = shape
                self.original_view.set_frame(frame)
                if not self.border_busy and time.monotonic() - self.last_border_time >= .45:
                    self.last_border_time = time.monotonic()
                    self.border_busy = True
                    job = BorderWorker(frame, self.border_token, self.owner)
                    job.result.connect(self.border_result)
                    job.finished.connect(self.border_finished)
                    self.owner.keep_job(job)
                    job.start()

    def border_finished(self):
        self.border_busy = False

    def border_result(self, token, result):
        if self.closed or self.original is not None or token != self.border_token:
            return
        if self.preview_shape != result["shape"] or self.preview_zoom != self.owner.camera_zoom:
            return
        self.original_view.points = result["points"]
        self.original_view.update()
        self.border_status.setText("已自动识别边框，绿色四边形为封面范围；拍摄后自动裁切" if result["found"] else
                                   "尚未找到完整边框；可调整距离/缩放，拍摄后仍可手动调整")

    def capture(self):
        if not self.capture_button.isEnabled():
            return
        frame = self.owner.camera.latest() if self.owner.camera else None
        if frame is None:
            self.status.setText("暂无摄像头画面，请稍候，或取消后从文件导入封面")
            return
        self.original = frame
        self.border_token += 1
        self.original_view.points = None
        self.process()

    def redetect(self):
        if self.redetect_button.isEnabled():
            # Re-detect geometry from the original photo; preserve manual rotation.
            self.original_view.points = None
            self.process(points=None, auto=False)

    def process(self, points=None, auto=True):
        if self.original is None or (self.worker and self.worker.isRunning()):
            return
        self.set_busy(True)
        self.original_view.set_frame(self.original)
        self.status.setText("正在本机识别边缘、校正透视和朝向…")
        self.worker = CoverWorker(self.original, points, auto, self.rotation, self.owner)
        self.worker.result.connect(self.processed)
        self.worker.error.connect(self.failed)
        self.owner.keep_job(self.worker)
        self.worker.start()

    def processed(self, result):
        if self.closed:
            return
        self.worker = None
        self.result_image, self.rotation = result["image"], result["rotation"]
        self.original_view.points = result["points"]
        self.original_view.update()
        self.border_status.setText("封面边框已识别 / 可拖动四角或再次自动识别" if result["detected"] else
                                   "未确定封面边框，已保留完整照片；可拖动四角")
        self.result_view.set_frame(self.result_image)
        self.status.setText(result["note"] + "。拖动左侧四角可重新裁切，Enter 采用。")
        self.set_busy(False)

    def failed(self, message):
        self.worker = None
        if not self.closed:
            self.status.setText(message)
            self.set_busy(False)

    def retake(self):
        self.original, self.result_image = None, None
        self.original_view.points = None
        self.result_view.set_frame(None)
        self.rotation = 0
        self.border_token += 1
        self.last_border_time = 0.0
        self.border_status.setText("OpenCV 自动识别边框：将封面完整放入取景范围")
        self.set_busy(False)

    def rotate(self, direction):
        if self.result_image is not None:
            self.rotation = (self.rotation + direction) % 4
            self.process(self.original_view.points, False)

    def use(self):
        if self.use_button.isEnabled():
            self.blob = encode_cover(self.result_image)
            self.accept()

    def done(self, result):
        self.closed = True
        self.border_token += 1
        self.timer.stop()
        self.original = None
        self.result_image = None
        self.original_view.points = None
        self.original_view.set_frame(None)
        self.result_view.set_frame(None)
        super().done(result)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self.capture()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.use()
        else:
            super().keyPressEvent(event)
