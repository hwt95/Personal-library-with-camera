from .i18n import tr
"""Original book mark and application-colored window controls."""
from pathlib import Path
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton, QDialog, QSizeGrip


def app_icon():
    root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    return QIcon(str(root / "resources" / "app-icon.svg"))


class TitleBar(QWidget):
    def __init__(self, window, *, minimize=True):
        super().__init__(window)
        self.window = window
        self.setObjectName("appTitleBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(44)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 2, 4, 2)
        icon = QLabel()
        icon.setPixmap(app_icon().pixmap(28, 28))
        layout.addWidget(icon)
        self.title = QLabel(window.windowTitle())
        self.title.setObjectName("windowCaption")
        window.windowTitleChanged.connect(self.title.setText)
        layout.addWidget(self.title, 1)
        for text, callback, hint in (("−", window.showMinimized, tr('最小化')),
                                     ("□", self.toggle_maximize, tr('最大化 / 还原')),
                                     ("×", window.close, tr('关闭'))):
            if text == "−" and not minimize:
                continue
            control = QPushButton(text)
            control.setObjectName("windowControl")
            control.setFixedSize(40, 34)
            control.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            control.setAutoDefault(False)
            control.setToolTip(hint)
            control.clicked.connect(callback)
            layout.addWidget(control)
        self._drag = None

    def toggle_maximize(self):
        self.window.showNormal() if self.window.isMaximized() else self.window.showMaximized()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.window.windowHandle() and self.window.windowHandle().startSystemMove():
                return
            self._drag = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event):
        self._drag = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximize()


class AppDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setWindowIcon(app_icon())
        self.setSizeGripEnabled(True)
        self._bar = None

    def showEvent(self, event):
        if self._bar is None and self.layout() is not None:
            self._bar = TitleBar(self, minimize=False)
            self.layout().insertWidget(0, self._bar)
        from .i18n import translate_tree
        translate_tree(self)
        super().showEvent(event)
