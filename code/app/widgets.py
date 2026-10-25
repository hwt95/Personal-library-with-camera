import numpy as np
import cv2
from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QCheckBox, QSlider

from .imaging import valid_corners


def button(text, callback, *, primary=False):
    widget = QPushButton(text)
    widget.setAutoDefault(False)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(callback)
    return widget


def row(*widgets):
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        layout.addWidget(widget)
    return box


def qimage(frame):
    rgb = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888).copy()


class CameraControls(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.autofocus = QCheckBox("自动对焦")
        self.refocus = button("重新对焦", owner.refocus_camera)
        layout.addWidget(row(self.autofocus, self.refocus))
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(100, 400)
        self.zoom.setSingleStep(10)
        self.zoom.setPageStep(25)
        self.zoom.setToolTip("1–4 倍数字缩放；取景、扫码与拍摄使用相同范围")
        self.zoom_label = QLabel()
        layout.addWidget(row(self.zoom_label, self.zoom, button("复位", lambda: owner.set_camera_zoom(1.0))))
        self.focus_note = QLabel()
        self.focus_note.setWordWrap(True)
        self.focus_note.setObjectName("subtle")
        layout.addWidget(self.focus_note)
        self.autofocus.toggled.connect(owner.set_camera_autofocus)
        self.zoom.valueChanged.connect(lambda value: owner.set_camera_zoom(value / 100))
        owner.camera_controls_changed.connect(self.sync)
        self.sync()

    def sync(self):
        self.zoom.blockSignals(True)
        self.zoom.setValue(round(self.owner.camera_zoom * 100))
        self.zoom.blockSignals(False)
        self.zoom_label.setText(f"缩放 {self.owner.camera_zoom:.1f}×")
        self.autofocus.blockSignals(True)
        self.autofocus.setChecked(self.owner.camera_autofocus)
        self.autofocus.blockSignals(False)
        supported = self.owner.camera_focus_state.get("supported")
        self.autofocus.setEnabled(supported is not False)
        self.refocus.setEnabled(self.owner.camera is not None and supported is not False)
        self.focus_note.setText(self.owner.camera_focus_state.get("note", "开启摄像头后自动对焦；缩放为数字裁切"))


class ImageView(QWidget):
    corners_changed = Signal(object)

    def __init__(self, placeholder="暂无封面", *, editable=False, guide=False):
        super().__init__()
        self.image = QImage()
        self.placeholder, self.editable, self.guide = placeholder, editable, guide
        self.points = None
        self.drag_index = None
        self.setMinimumSize(160, 200)

    def set_frame(self, frame):
        self.image = qimage(frame) if frame is not None else QImage()
        self.update()

    def set_blob(self, blob):
        self.image = QImage.fromData(blob) if blob else QImage()
        self.points = None
        self.update()

    def rect_image(self):
        if self.image.isNull():
            return QRectF()
        factor = min((self.width()-16)/self.image.width(), (self.height()-16)/self.image.height())
        width, height = self.image.width()*factor, self.image.height()*factor
        return QRectF((self.width()-width)/2, (self.height()-height)/2, width, height)

    def map_point(self, point):
        rect = self.rect_image()
        return QPointF(rect.x() + point[0]*rect.width()/self.image.width(),
                       rect.y() + point[1]*rect.height()/self.image.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#eef2f6"))
        if self.image.isNull():
            painter.setPen(QColor("#65758b"))
            painter.drawText(self.rect().adjusted(15, 15, -15, -15), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.placeholder)
            return
        painter.drawImage(self.rect_image(), self.image)
        if self.points is not None:
            painter.setPen(QPen(QColor("#18b8a6"), 3))
            painter.drawPolygon(QPolygonF([self.map_point(p) for p in self.points]))
            painter.setBrush(QColor("#ffffff"))
            for i, point in enumerate(self.points):
                mapped = self.map_point(point)
                painter.drawEllipse(mapped, 7, 7)
                painter.drawText(mapped + QPointF(10, -8), str(i+1))
        elif self.guide:
            rect = self.rect_image()
            painter.setPen(QPen(QColor("#18b8a6"), 2, Qt.PenStyle.DashLine))
            painter.drawRect(rect.adjusted(rect.width()*.15, rect.height()*.2, -rect.width()*.15, -rect.height()*.2))

    def mousePressEvent(self, event):
        if not self.editable or self.points is None:
            return
        distances = [(self.map_point(p) - event.position()).manhattanLength() for p in self.points]
        selected = int(np.argmin(distances))
        if distances[selected] < 35:
            self.drag_index = selected

    def mouseMoveEvent(self, event):
        if self.drag_index is None:
            return
        rect = self.rect_image()
        point = [(event.position().x()-rect.x())/rect.width()*self.image.width(),
                 (event.position().y()-rect.y())/rect.height()*self.image.height()]
        candidate = self.points.copy()
        candidate[self.drag_index] = np.clip(point, [0, 0], [self.image.width()-1, self.image.height()-1])
        if valid_corners(candidate):
            self.points = candidate
            self.update()

    def mouseReleaseEvent(self, event):
        if self.drag_index is not None:
            self.drag_index = None
            self.corners_changed.emit(self.points.copy())


STYLE = """
QWidget { font-family: 'LXGW WenKai TC', 'Noto Sans SC'; font-size: 11pt; color: #243449; }
QMainWindow, QDialog { background: #f6f8fb; }
QLabel#heading { font-size: 22pt; font-weight: 700; color: #17314d; }
QLabel#subtle { color: #68788b; }
QLabel#status { padding: 9px; background: #eaf4f3; color: #165c54; border-radius: 5px; }
QTabWidget::pane { border: 1px solid #dbe3ec; background: white; }
QTabBar::tab { padding: 12px 29px; background: #edf1f6; }
QTabBar::tab:selected { background: white; color: #087e73; border-top: 3px solid #0c9386; }
QLineEdit, QTextEdit, QComboBox, QSpinBox { background: white; border: 1px solid #ccd7e3; border-radius: 4px; padding: 6px; selection-background-color: #087e73; }
QLineEdit:focus, QTextEdit:focus { border: 1px solid #0c9386; }
QPushButton { background: #f7f9fc; border: 1px solid #cbd7e4; border-radius: 4px; padding: 7px 13px; }
QPushButton:hover { background: #e8f2f2; border-color: #80bdb7; }
QPushButton#primary { background: #087e73; color: white; border: 1px solid #087e73; }
QPushButton:disabled { color: #9aa7b5; background: #edf1f6; }
QTableWidget { background: white; alternate-background-color: #f5f8fc; gridline-color: #e4eaf1; border: 1px solid #dbe3ec; }
QHeaderView::section { background: #edf2f7; border: 0; padding: 9px; font-weight: 700; }
QTableWidget::item:selected { background: #d4ebe7; color: #17314d; }
QGroupBox { border: 1px solid #dbe3ec; border-radius: 5px; margin-top: 12px; padding-top: 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
"""
