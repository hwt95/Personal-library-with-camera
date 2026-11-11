from .i18n import tr
import time

from PySide6.QtCore import Qt, QTimer, QEvent, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QPixmap
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QDialogButtonBox, QLineEdit, QTreeWidget, QTreeWidgetItem, QMessageBox, QApplication,
    QScrollArea, QSlider, QWidget, QComboBox, QRadioButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView)

from .widgets import ImageView, CameraControls, row, button
from .imaging import encode_cover
from .workers import CoverWorker, BorderWorker
from .chrome import AppDialog
from .classification import system_label
from .classification_auxiliary import supplement, auxiliary_index
from .classification_dictionary import find_term, term_description


class CornerLoupe(QWidget):
    def __init__(self, view):
        super().__init__()
        self.view, self.index = view, 0
        self.setFixedSize(300, 300)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#202a35"))
        if self.view.points is None:
            return
        x,y = self.view.points[self.index]
        painter.drawImage(QRectF(self.rect()), self.view.image, QRectF(float(x)-32, float(y)-32, 64, 64))
        for color, width in (("#111827", 3), ("#ffffff", 1)):
            painter.setPen(QPen(QColor(color), width))
            painter.drawLine(0, 150, 300, 150)
            painter.drawLine(150, 0, 150, 300)
        painter.setPen(QPen(QColor("#e6007e"), 1))
        painter.drawEllipse(146, 146, 8, 8)


class CornerEditor(AppDialog):
    def __init__(self, frame, points, parent):
        super().__init__(parent)
        self.setWindowTitle(tr('放大调整封面四角'))
        self.resize(1280, 860)
        layout = QVBoxLayout(self)
        instruction = QLabel(tr('拖动顶点；方向键移动 1 像素，Shift + 方向键移动 10 像素。右侧实时放大当前顶点。'))
        instruction.setWordWrap(True)
        layout.addWidget(instruction)
        self.view = ImageView(editable=True)
        self.view.set_frame(frame)
        self.view.points = points.copy()
        self.loupe = CornerLoupe(self.view)
        self.coordinates = QLabel()
        self.coordinates.setWordWrap(True)
        self.corner = QComboBox()
        for i in range(4):
            self.corner.addItem(tr('顶点 {v0}', v0=(i+1)), i)
        self.corner.currentIndexChanged.connect(self.select_corner)
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.addWidget(self.corner)
        side_layout.addWidget(self.loupe)
        side_layout.addWidget(self.coordinates)
        side_layout.addStretch()
        content = QHBoxLayout()
        content.addWidget(self.view, 1)
        content.addWidget(side)
        layout.addLayout(content, 1)
        layout.addWidget(row(button(tr('应用四角'), self.accept, primary=True), button(tr('取消'), self.reject)))
        self.view.corners_moving.connect(self.updated)
        self.updated((0, self.view.points))

    def select_corner(self, index):
        self.view.selected_index = index
        self.updated((index, self.view.points))
        self.view.setFocus()

    def updated(self, value):
        index, points = value
        self.corner.blockSignals(True)
        self.corner.setCurrentIndex(index)
        self.corner.blockSignals(False)
        self.loupe.index = index
        self.loupe.update()
        self.coordinates.setText("\n".join(f"{i+1}:  x={point[0]:.1f}   y={point[1]:.1f}" for i,point in enumerate(points)))


class CoverViewer(AppDialog):
    def __init__(self, blob, title, parent):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 850)
        layout = QVBoxLayout(self)
        self.pixmap = QPixmap()
        self.pixmap.loadFromData(blob)
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll = QScrollArea()
        scroll.setWidget(self.picture)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(scroll, 1)
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(25, 300)
        self.zoom.valueChanged.connect(self.scale_image)
        self.zoom.setValue(min(100, max(25, round(700/max(1,self.pixmap.height())*100))))
        layout.addWidget(row(QLabel(tr('缩放')), self.zoom, button(tr('关闭'), self.accept)))
        self.scale_image(self.zoom.value())

    def scale_image(self, value):
        size = self.pixmap.size() * (value/100)
        self.picture.setPixmap(self.pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.picture.resize(size)


class CandidateDialog(AppDialog):
    def __init__(self, candidates, parent, title=tr('选择书目')):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(740, 380)
        self.candidates = candidates
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr('找到多条候选，请核对版本与出版社：')))
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


class ClassificationPolicyDialog(AppDialog):
    def __init__(self, target, plan, parent):
        super().__init__(parent)
        self.setWindowTitle(tr('应用分类规则'))
        self.resize(860, 610)
        layout = QVBoxLayout(self)
        summary = QLabel(system_label(target) + '\n' + tr(
            '若同步：{books} 种书目、{copies} 册将变更编号，其中 {pending} 种缺少目标分类号。',
            books=len(plan), copies=sum(len(item['copies']) for item in plan),
            pending=sum(item['pending'] for item in plan)))
        summary.setWordWrap(True)
        layout.addWidget(summary)
        note = QLabel(tr('不同体系不能直接互换。同步使用已有目标分类号，缺失时标为待分类；原分类资料保留。杂志编号不变。'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.future_only = QRadioButton(tr('仅用于新录入（保留已有编码）'))
        self.synchronize = QRadioButton(tr('同步已有书目（先备份）'))
        self.future_only.setChecked(True)
        layout.addWidget(self.future_only)
        layout.addWidget(self.synchronize)
        caption = QLabel(tr('同步预览（最多显示 200 种书目；副本后缀不变）'))
        caption.setWordWrap(True)
        layout.addWidget(caption)
        table = QTableWidget(min(len(plan), 200), 3)
        table.setHorizontalHeaderLabels([tr('书名'), tr('原编码'), tr('同步后编码')])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row_index, item in enumerate(plan[:200]):
            for column, key in enumerate(('title', 'old_code', 'new_code')):
                cell = QTableWidgetItem(item[key])
                cell.setToolTip(item[key])
                table.setItem(row_index, column, cell)
        layout.addWidget(table, 1)
        layout.addWidget(row(button(tr('应用'), self.accept, primary=True), button(tr('取消'), self.reject)))


class ClassificationDialog(AppDialog):
    def __init__(self, db, parent, *, system='clc'):
        super().__init__(parent)
        self.system = system
        self.db, self.selected_code = db, ""
        self.setWindowTitle(system_label(system) + ' · ' + tr('分类字典'))
        self.resize(650, 580)
        layout = QVBoxLayout(self)
        self.table_selector = QComboBox()
        self.table_selector.addItem(tr('主分类表'), '')
        if system == 'clc':
            for key, table in supplement()['tables'].items():
                self.table_selector.addItem(table['name'] + ' · ' + table['edition'], key)
            layout.addWidget(self.table_selector)
        self.query = QLineEdit()
        self.query.setPlaceholderText(tr('搜索分类号或名称；字典未收录的细分类可直接在录入页输入'))
        layout.addWidget(self.query)
        note = QLabel(tr('字典按体系独立保存；可在设置中导入有权使用的 CSV，或直接填写分类号。未内置完整 DDC / LCC 类目表。'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr('分类号'), tr('名称')])
        # A one-time fit only measures the collapsed root codes. Keep fitting as
        # lazy children become visible, including their indentation and full code.
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setResizeContentsPrecision(-1)
        layout.addWidget(self.tree)
        self.result_note = QLabel()
        self.result_note.setWordWrap(True)
        layout.addWidget(self.result_note)
        self.choose_button = button(tr('采用所选分类'), self.choose, primary=True)
        layout.addWidget(self.choose_button)
        self.main_index = db.classification_index(system)
        self.index = self.main_index
        self.children_by_parent = {}
        for item in self.index.values():
            parent = item['parent_code'] if item['parent_code'] in self.index else ''
            self.children_by_parent.setdefault(parent, []).append(item)
        self.order_main_children()
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(180)
        self.search_timer.timeout.connect(self.populate)
        self.query.textChanged.connect(lambda: self.search_timer.start())
        self.tree.itemExpanded.connect(self.expand_node)
        self.tree.itemDoubleClicked.connect(self.choose)
        self.table_selector.currentIndexChanged.connect(self.change_table)
        self.populate()

    def change_table(self):
        key = self.table_selector.currentData()
        self.index = auxiliary_index(key) if key else self.main_index
        self.choose_button.setEnabled(not key)
        self.children_by_parent = {}
        for record in self.index.values():
            parent = record['parent_code'] if record['parent_code'] in self.index else ''
            self.children_by_parent.setdefault(parent, []).append(record)
        self.order_main_children()
        self.populate()

    def order_main_children(self):
        if self.system == 'clc' and not self.table_selector.currentData():
            for children in self.children_by_parent.values():
                children.sort(key=lambda record: record['code'].strip('[]{}'))

    def make_node(self, record, *, expandable):
        item = QTreeWidgetItem([record['code'], record['name']])
        item.setData(0, Qt.ItemDataRole.UserRole, record['code'])
        item.setToolTip(0, record['code'])
        if self.table_selector.currentData():
            item.setToolTip(1, record['name'] + '\n' + tr('第5版原书第 {page} 页；复分号须与主分类号组合使用。', page=record['printed_page']))
        else:
            item.setToolTip(1, term_description(self.main_index, record['code'], self.system))
        if expandable and self.children_by_parent.get(record['code']):
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        return item

    def expand_node(self, item):
        if self.query.text().strip() or item.childCount():
            return
        for record in self.children_by_parent.get(item.data(0, Qt.ItemDataRole.UserRole), []):
            item.addChild(self.make_node(record, expandable=True))

    def populate(self):
        self.tree.clear()
        query = self.query.text().strip().casefold()
        if query:
            matches = [r for r in self.index.values() if query in r['code'].casefold() or query in r['name'].casefold()]
            if self.system == 'clc' and not self.table_selector.currentData():
                term, kind = find_term(self.main_index, self.query.text(), 'clc')
                if kind == 'composed':
                    matches.insert(0, term)
            self.result_note.setText(tr('匹配 {count} 项；最多显示 300 项，请输入更具体的编码或名称。', count=len(matches)))
            for record in matches[:300]:
                self.tree.addTopLevelItem(self.make_node(record, expandable=False))
        else:
            self.result_note.setText(tr('共 {count} 项；展开类目查看下级，或输入编码、名称搜索。', count=len(self.index)))
            for record in self.children_by_parent.get('', []):
                self.tree.addTopLevelItem(self.make_node(record, expandable=True))
        if self.table_selector.currentData():
            self.result_note.setText(self.result_note.text() + '\n' + tr('附表供组合查阅，不能单独作为主分类号。'))
        self.tree.resizeColumnToContents(0)

    def choose(self, *_):
        if self.tree.currentItem() and not self.table_selector.currentData():
            self.selected_code = self.tree.currentItem().text(0)
            self.accept()


class CoverDialog(AppDialog):
    def __init__(self, owner, frame=None):
        super().__init__(owner)
        self.owner = owner
        self.original, self.result_image, self.blob = frame, None, None
        self.rotation, self.worker, self.closed = 0, None, False
        self.border_busy, self.border_token, self.last_border_time = False, 0, 0.0
        self.preview_shape = None
        self.preview_zoom = owner.camera_zoom
        self.setWindowTitle(tr('拍摄与校正封面 · 照片仅在本机处理'))
        self.resize(1080, 740)
        layout = QVBoxLayout(self)
        self.status = QLabel(tr('将封面四角放入画面，选择与封面颜色有区别的背景。空格拍摄，Esc 取消。'))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.camera_controls = CameraControls(owner)
        layout.addWidget(self.camera_controls)
        self.border_status = QLabel(tr('本地模型 + OpenCV：将封面完整放入取景范围'))
        self.border_status.setObjectName("subtle")
        layout.addWidget(self.border_status)
        self.original_view = ImageView(tr('正在等待摄像头画面…'), editable=True, guide=True)
        self.result_view = ImageView(tr('拍摄后显示校正预览'))
        layout.addWidget(row(self.original_view, self.result_view), 1)
        self.capture_button = button(tr('拍摄（空格）'), self.capture, primary=True)
        self.retake_button = button(tr('重拍'), self.retake)
        self.redetect_button = button(tr('自动识别边框'), self.redetect)
        self.corner_button = button(tr('放大调整四角'), self.edit_corners)
        self.left_button = button(tr('左转 90°'), lambda: self.rotate(1))
        self.right_button = button(tr('右转 90°'), lambda: self.rotate(-1))
        self.use_button = button(tr('采用封面（Enter）'), self.use, primary=True)
        layout.addWidget(row(self.capture_button, self.retake_button, self.left_button,
                             self.right_button, self.use_button, button(tr('取消'), self.reject)))
        layout.addWidget(row(self.redetect_button, self.corner_button))
        QApplication.instance().installEventFilter(self)
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
        self.corner_button.setEnabled(not busy and self.original is not None)
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
        self.border_status.setText(tr('已自动识别边框，细线表示封面范围；拍摄后自动裁切') if result["found"] else
                                   tr('尚未找到完整边框；可调整距离/缩放，拍摄后仍可手动调整'))

    def capture(self):
        if not self.capture_button.isEnabled():
            return
        frame = self.owner.camera.latest() if self.owner.camera else None
        if frame is None:
            self.status.setText(tr('暂无摄像头画面，请稍候，或取消后从文件导入封面'))
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

    def edit_corners(self):
        if self.original is not None and self.original_view.points is not None:
            editor = CornerEditor(self.original, self.original_view.points, self)
            if editor.exec() == QDialog.DialogCode.Accepted:
                self.process(editor.view.points.copy(), False)
            editor.deleteLater()

    def process(self, points=None, auto=True):
        if self.original is None or (self.worker and self.worker.isRunning()):
            return
        self.set_busy(True)
        self.original_view.set_frame(self.original)
        self.status.setText(tr('正在本机识别边缘、校正透视和朝向…'))
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
        self.border_status.setText(tr('封面边框已识别 / 可拖动四角或再次自动识别') if result["detected"] else
                                   tr('未确定封面边框，已保留完整照片；可拖动四角'))
        self.result_view.set_frame(self.result_image)
        self.status.setText(result["note"] + tr('。拖动左侧四角可重新裁切，Enter 采用。'))
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
        self.border_status.setText(tr('本地模型 + OpenCV：将封面完整放入取景范围'))
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
        QApplication.instance().removeEventFilter(self)
        self.original = None
        self.result_image = None
        self.original_view.points = None
        self.original_view.set_frame(None)
        self.result_view.set_frame(None)
        super().done(result)

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Space and
                (watched is self or (isinstance(watched, QWidget) and self.isAncestorOf(watched))) and
                QApplication.activeModalWidget() in (None, self)):
            if not event.isAutoRepeat():
                self.capture()
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self.capture()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.use()
        else:
            super().keyPressEvent(event)
