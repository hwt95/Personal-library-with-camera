import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QActionGroup, QDesktopServices
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLabel, QLineEdit, QTextEdit, QComboBox, QCheckBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QSplitter, QGroupBox, QFileDialog, QSpinBox, QScrollArea,
    QMenu, QListWidget)

from .widgets import ImageView, CameraControls, row, button
from . import APP_NAME, __version__, __author__, PROJECT_URL, ISSUES_URL
from .domain import normalize_issn, publication_barcode, validate_year
from .i18n import tr
from .edition import OFFLINE


FIELD_LABELS = {"isbn": "ISBN", "title": tr('书名 *'), "authors": tr('作者 / 编者'), "translators": tr('译者'),
                "publisher": tr('出版社'), "clc_code": tr('主分类号'), "publication_year": tr('本册年份 *'),
                "issue_number": tr('期数 *'), "location": tr('藏书位置'), "notes": tr('备注'), "summary": tr('简介')}


class EntryPage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner, self.fields = owner, {}
        self.cover_blob, self.cover_mime, self.source_info = None, "", {}
        self.dirty = set()
        self.book_id = self.copy_id = None
        self.reference_pubdate = ""
        self.barcode_details = {}
        self.last_saved_book = None
        layout = QVBoxLayout(self)
        split = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.camera_view = ImageView(tr('开启摄像头后可扫描图书条码\n也可直接手工输入资料'), guide=True)
        self.camera_view.setMinimumSize(260, 180)
        left_layout.addWidget(self.camera_view, 2)
        self.camera_button = button(tr('开启摄像头'), owner.toggle_camera)
        left_layout.addWidget(self.camera_button)
        self.camera_status = QLabel(tr('支持 EAN-13 图书条码，画面不镜像'))
        self.camera_status.setWordWrap(True)
        self.camera_status.setObjectName("subtle")
        left_layout.addWidget(self.camera_status)
        self.camera_controls = CameraControls(owner)
        left_layout.addWidget(self.camera_controls)
        self.cover_view = ImageView(tr('暂无封面\n可以拍摄或从文件导入'))
        self.cover_view.setMinimumHeight(130)
        self.cover_view.setMaximumHeight(220)
        left_layout.addWidget(self.cover_view, 2)
        left_layout.addWidget(row(button(tr('拍摄封面'), owner.capture_cover), button(tr('导入图片'), owner.import_cover)))
        left_layout.addWidget(button(tr('清除封面'), lambda: self.set_cover(None, "manual")))
        split.addWidget(left)
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        self.form, self.form_rows = form, {}
        form.setVerticalSpacing(10)
        self.media_type = QComboBox()
        self.media_type.addItem(tr('图书'), "book")
        self.media_type.addItem(tr('杂志 / 期刊'), "magazine")
        form.addRow(tr('类型'), self.media_type)
        self.reading_status = QComboBox()
        for name, value in ((tr('未阅读'), "unread"), (tr('阅读中'), "reading"), (tr('已阅读'), "read")):
            self.reading_status.addItem(name, value)
        self.reading_status.currentIndexChanged.connect(lambda: self.mark_dirty("reading_status"))
        form.addRow(tr('阅读状态'), self.reading_status)
        for key, label in FIELD_LABELS.items():
            self.form_rows[key] = form.rowCount()
            widget = QTextEdit() if key == "summary" else QLineEdit()
            if key == "summary":
                widget.setAcceptRichText(False)
                widget.setFixedHeight(105)
                widget.textChanged.connect(lambda k=key: self.mark_dirty(k))
            else:
                widget.textEdited.connect(lambda _, k=key: self.mark_dirty(k))
            self.fields[key] = widget
            if key == "isbn":
                widget.setPlaceholderText(tr('手工输入 ISBN-10/13，回车查询'))
                self.lookup_button = button(tr('查询资料'), owner.lookup_isbn, primary=True)
                self.cancel_button = button(tr('取消查询'), owner.cancel_lookup)
                self.cancel_button.setEnabled(False)
                form.addRow(label, row(widget, self.lookup_button, self.cancel_button))
            elif key == "clc_code":
                widget.setPlaceholderText(tr('例如 TP303；留空表示待分类'))
                form.addRow(label, row(widget, button(tr('分类字典'), owner.choose_classification)))
                self.clc_hint = QLabel(tr('待分类 · 保存后生成临时编号'))
                self.clc_hint.setObjectName("subtle")
                form.addRow("", self.clc_hint)
                widget.textChanged.connect(owner.update_classification_hint)
            elif key == "publication_year":
                widget.setPlaceholderText(tr('填写手中这一册的出版年份'))
                self.year_reference = QLabel(tr('网络参考年份：—'))
                self.year_reference.setObjectName("subtle")
                form.addRow(label, row(widget, self.year_reference))
            elif key == "issue_number":
                widget.setPlaceholderText(tr('可从两位附加条码读取；请核对封面期数'))
                form.addRow(label, widget)
            elif key == "location":
                widget.setPlaceholderText(tr('勾选预设地点，也可直接填写'))
                self.location_button = button(tr('勾选预设地点'), lambda: None)
                self.location_menu = QMenu(self)
                self.location_group = QActionGroup(self.location_menu)
                self.location_group.setExclusive(True)
                self.location_button.setMenu(self.location_menu)
                self.location_menu.aboutToShow.connect(self.refresh_location_menu)
                owner.location_presets_changed.connect(self.refresh_location_menu)
                form.addRow(label, row(widget, self.location_button))
            else:
                form.addRow(label, widget)
        self.media_type.currentIndexChanged.connect(self.media_changed)
        self.media_changed()
        self.new_edition = QCheckBox(tr('作为新版本登记（即使 ISBN 相同也建立独立书目）'))
        form.addRow("", self.new_edition)
        self.edit_hint = QLabel(tr('新书录入 · 书名和本册年份必填，其他资料可稍后补充'))
        self.edit_hint.setWordWrap(True)
        form.addRow("", self.edit_hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_widget)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        split.addWidget(scroll)
        split.setSizes([330, 780])
        layout.addWidget(split, 1)
        self.status = QLabel(tr('离线可用 · 扫码、手工查询或直接填写资料'))
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.save_button = button(tr('确认保存（Ctrl+Enter）'), owner.save_entry, primary=True)
        self.repeat_button = button(tr('继续录入同书副本'), owner.repeat_copy)
        self.repeat_button.setEnabled(False)
        layout.addWidget(row(self.save_button, button(tr('重置草稿（Esc）'), owner.reset_entry), self.repeat_button))

    def media_changed(self):
        magazine = self.media_type.currentData() == "magazine"
        for key in ("authors", "translators", "clc_code", "summary"):
            self.form.setRowVisible(self.form_rows[key], not magazine)
        self.form.setRowVisible(self.form_rows["clc_code"] + 1, not magazine)
        self.form.setRowVisible(self.form_rows["issue_number"], magazine)
        for key, label in (("isbn", tr('ISSN / 条码') if magazine else "ISBN"),
                           ("title", tr('刊名 *') if magazine else tr('书名 *')),
                           ("publication_year", tr('年度 *') if magazine else tr('本册年份 *'))):
            self.form.itemAt(self.form_rows[key], QFormLayout.ItemRole.LabelRole).widget().setText(label)
        self.fields["isbn"].setPlaceholderText(tr('ISSN 或 977 条码，可附两位期数码') if magazine else tr('手工输入 ISBN-10/13，回车查询'))
        self.lookup_button.setText(tr('读取条码') if magazine else tr('查询资料'))
        self.year_reference.setVisible(not magazine)

    def apply_barcode(self, record):
        self.barcode_details = dict(record)
        self.media_type.setCurrentIndex(1 if record["media_type"] == "magazine" else 0)
        self.set_text("isbn", record.get("issn") or record.get("isbn", ""))
        if record.get("issue_number"):
            self.set_text("issue_number", record["issue_number"])
        self.update_barcode_hint()
        self.mark_dirty("isbn")

    def update_barcode_hint(self):
        addon = self.barcode_details.get("barcode_addon", "")
        self.fields["issue_number"].setToolTip(tr('原始附加码：{addon}；五位附加码请按封面核对期数', addon=addon) if addon else "")

    def refresh_location_menu(self):
        self.location_menu.clear()
        current = self.text("location")
        names = self.owner.location_presets()
        for name in ["", *names]:
            action = self.location_menu.addAction(name.replace("&", "&&") if name else tr('不设置地点'))
            action.setCheckable(True)
            action.setData(name)
            self.location_group.addAction(action)
            action.setChecked(current == name)
            action.triggered.connect(lambda checked, selected=name: self.select_location(selected))
        if not names:
            self.location_menu.addAction(tr('请先在设置页添加存放地点')).setEnabled(False)
        self.location_menu.addSeparator()
        self.location_menu.addAction(tr('管理预设地点…'), self.owner.show_location_settings)

    def select_location(self, name):
        self.set_text("location", name)
        self.mark_dirty("location")

    def mark_dirty(self, key):
        self.dirty.add(key)
        if key == "isbn":
            self.owner.cancel_lookup(silent=True)
            self.owner.queried_isbn = ""
            if self.copy_id is None:
                self.book_id = None
        if self.text("title") or self.text("isbn"):
            self.owner.scan_gate.locked = True

    def text(self, key):
        widget = self.fields[key]
        return (widget.toPlainText() if isinstance(widget, QTextEdit) else widget.text()).strip()

    def set_text(self, key, value):
        widget = self.fields[key]
        widget.blockSignals(True)
        if isinstance(widget, QTextEdit):
            widget.setPlainText(str(value or ""))
        else:
            widget.setText(str(value or ""))
        widget.blockSignals(False)

    def set_cover(self, blob, source):
        self.cover_blob, self.cover_mime = blob, "image/jpeg" if blob else ""
        self.cover_view.set_blob(blob)
        self.dirty.add("cover_blob")
        self.source_info["cover"] = source

    def data(self):
        data = {k: self.text(k) for k in ("isbn", "title", "authors", "translators", "publisher", "clc_code", "summary")}
        info = dict(self.source_info)
        provenance = dict(info.get("fields", {}))
        for key in self.dirty:
            provenance[key] = "manual"
        info["fields"] = provenance
        data.update(cover_blob=self.cover_blob, cover_mime=self.cover_mime,
                    reference_pubdate=self.reference_pubdate, source_info=info)
        data["media_type"] = self.media_type.currentData()
        if data["media_type"] == "magazine":
            identifier = self.text("isbn")
            record = publication_barcode(identifier) if identifier.startswith("977") else self.barcode_details
            data.update(issn=record.get("issn", "") if identifier.startswith("977") else normalize_issn(identifier),
                        issue_year=validate_year(self.text("publication_year")), issue_number=self.text("issue_number"),
                        barcode=record.get("barcode", ""), barcode_addon=record.get("barcode_addon", ""))
            for key in ("isbn", "authors", "translators", "clc_code", "summary"):
                data[key] = ""
        return data

    def fill(self, data, *, protect=True):
        self.media_type.setCurrentIndex(1 if data.get("media_type") == "magazine" else 0)
        if data.get("media_type") == "magazine":
            self.barcode_details = {key: data.get(key, "") for key in ("barcode", "barcode_addon", "issn")}
            self.set_text("isbn", data.get("issn", ""))
            self.set_text("publication_year", data.get("issue_year", ""))
            self.set_text("issue_number", data.get("issue_number", ""))
            self.update_barcode_hint()
        protected = self.dirty.copy() if protect else set()
        for key in ("isbn", "title", "authors", "translators", "publisher", "clc_code", "summary"):
            if key not in protected and data.get(key):
                self.set_text(key, data[key])
        self.reference_pubdate = data.get("reference_pubdate", "")
        self.year_reference.setText(tr('网络参考年份：') + (self.reference_pubdate or "—"))
        old_source = self.source_info
        self.source_info = dict(data.get("source_info", {}))
        provenance = dict(self.source_info.get("fields", {}))
        provider = self.source_info.get("provider", "local")
        for key in ("isbn", "title", "authors", "translators", "publisher", "clc_code", "summary"):
            if data.get(key) and key not in protected:
                provenance.setdefault(key, provider)
        self.source_info["fields"] = provenance
        if "cover_blob" not in protected and data.get("cover_blob"):
            self.cover_blob, self.cover_mime = data["cover_blob"], data.get("cover_mime", "image/jpeg")
            self.cover_view.set_blob(self.cover_blob)
        elif "cover_blob" in protected:
            self.source_info["cover"] = old_source.get("cover", "manual")
        self.owner.update_classification_hint()

    def clear(self):
        for key in self.fields:
            self.set_text(key, "")
        self.cover_blob, self.cover_mime, self.source_info = None, "", {}
        self.cover_view.set_blob(None)
        self.reference_pubdate = ""
        self.year_reference.setText(tr('网络参考年份：—'))
        self.book_id = self.copy_id = None
        self.dirty.clear()
        self.barcode_details = {}
        self.update_barcode_hint()
        self.media_type.setEnabled(True)
        self.reading_status.blockSignals(True)
        self.reading_status.setCurrentIndex(0)
        self.reading_status.blockSignals(False)
        self.new_edition.setChecked(False)
        self.new_edition.setEnabled(True)
        self.edit_hint.setText(tr('新书录入 · 书名和本册年份必填，其他资料可稍后补充'))
        self.save_button.setText(tr('确认保存（Ctrl+Enter）'))
        self.owner.update_classification_hint()


class CatalogPage(QWidget):
    COLUMNS = [("code", tr('馆藏编码')), ("title", tr('书名')), ("summary", tr('简介')), ("authors", tr('作者')), ("translators", tr('译者')),
               ("publisher", tr('出版社')), ("publication_year", tr('年份')), ("clc_code", tr('分类号')),
               ("location", tr('位置')), ("created_at", tr('录入时间（UTC）')), ("copy_count", tr('册数')),
               ("media_type", tr('类型')), ("issue_number", tr('期数')), ("reading_status", tr('阅读状态'))]

    def __init__(self, owner, *, advanced=False):
        super().__init__()
        self.owner, self.advanced = owner, advanced
        self.rows, self.filters = [], {}
        self.sort_key, self.descending = "default", False
        layout = QVBoxLayout(self)
        self.quick = QLineEdit()
        self.quick.setPlaceholderText(tr('搜索书名、作者、简介、出版社、ISBN 或馆藏编码'))
        self.quick.returnPressed.connect(self.refresh)
        layout.addWidget(row(self.quick, button(tr('搜索'), self.refresh, primary=True), button(tr('清空条件'), self.clear)))
        self.media_filter, self.reading_filter = QComboBox(), QComboBox()
        for label, value in ((tr('所有类型'), ""), (tr('图书'), "book"), (tr('杂志 / 期刊'), "magazine")):
            self.media_filter.addItem(label, value)
        for label, value in ((tr('所有阅读状态'), ""), (tr('未阅读'), "unread"), (tr('阅读中'), "reading"), (tr('已阅读'), "read")):
            self.reading_filter.addItem(label, value)
        layout.addWidget(row(self.media_filter, self.reading_filter))
        self.media_filter.currentIndexChanged.connect(self.refresh)
        self.reading_filter.currentIndexChanged.connect(self.refresh)
        grid = QGridLayout()
        keys = [("clc_code", tr('分类号前缀')), ("authors", tr('作者包含')), ("publisher", tr('出版社包含')), ("location", tr('位置包含'))]
        if advanced:
            keys = [("title", tr('书名包含')), ("authors", tr('作者包含')), ("translators", tr('译者包含')),
                    ("publisher", tr('出版社包含')), ("isbn", "ISBN"), ("code", tr('馆藏编码')),
                    ("clc_code", tr('分类号前缀')), ("location", tr('位置包含')), ("summary", tr('简介包含'))]
        for i, (key, label) in enumerate(keys):
            edit = QLineEdit()
            edit.setPlaceholderText(label)
            edit.returnPressed.connect(self.refresh)
            self.filters[key] = edit
            grid.addWidget(edit, i // 4, i % 4, 1, 4 if key == "summary" else 1)
        layout.addLayout(grid)
        self.year_min, self.year_max = QLineEdit(), QLineEdit()
        self.year_min.setPlaceholderText(tr('起始年份'))
        self.year_max.setPlaceholderText(tr('结束年份'))
        self.mode = QComboBox()
        self.mode.addItem(tr('全部条件满足（AND）'), "AND")
        if advanced:
            self.mode.addItem(tr('任一条件满足（OR）'), "OR")
        self.exact_isbn, self.exact_code = QCheckBox(tr('ISBN 精确')), QCheckBox(tr('编码精确'))
        self.exact_isbn.setChecked(True)
        self.exact_code.setChecked(True)
        layout.addWidget(row(self.year_min, self.year_max, self.mode, self.exact_isbn, self.exact_code))
        self.exact_isbn.setVisible(advanced)
        self.exact_code.setVisible(advanced)
        self.flags = {"pending": QCheckBox(tr('待分类')), "no_cover": QCheckBox(tr('缺封面')), "duplicates": QCheckBox(tr('有副本'))}
        self.sort = QComboBox()
        self.sort.addItem(tr('默认：完整编码 ↑ / 年份 ↓'), "default")
        self.sort.addItem(tr('同书目：年份从新到旧'), "edition_year")
        self.sort.currentIndexChanged.connect(self.change_preset)
        for control in self.flags.values():
            control.toggled.connect(self.refresh)
        layout.addWidget(row(*self.flags.values(), self.sort, button(tr('恢复默认排序'), self.default_sort)))
        split = QSplitter()
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([label for _, label in self.COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.setColumnWidth(0, 190)
        self.table.setColumnWidth(1, 220)
        self.table.setColumnWidth(2, 280)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().sectionClicked.connect(self.header_sort)
        self.table.itemSelectionChanged.connect(self.show_detail)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit())
        split.addWidget(self.table)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        self.cover = ImageView(tr('选择记录查看封面'))
        self.cover.clicked.connect(self.enlarge_cover)
        self.cover.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cover.setToolTip(tr('点击放大封面'))
        self.cover.setMinimumWidth(190)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        detail_layout.addWidget(self.cover, 2)
        detail_layout.addWidget(self.details, 3)
        split.addWidget(detail)
        split.setSizes([900, 240])
        layout.addWidget(split, 1)
        self.count = QLabel(tr('0 册'))
        layout.addWidget(row(self.count, button(tr('编辑所选'), self.edit), button(tr('新增同书副本'), self.add_copy),
                             button(tr('删除所选单册'), self.delete), button(tr('导出当前结果 CSV'), self.export)))

    def selected(self):
        index = self.table.currentRow()
        return self.rows[index] if 0 <= index < len(self.rows) else None

    def refresh(self, *_):
        if not self.owner.db:
            self.rows = []
            self.table.setRowCount(0)
            return
        try:
            low, high = self.year_min.text().strip(), self.year_max.text().strip()
            if low and high and int(low) > int(high):
                raise ValueError(tr('起始年份不能大于结束年份'))
            self.rows = self.owner.db.search(self.quick.text(), {k: w.text().strip() for k, w in self.filters.items()},
                mode=self.mode.currentData(), year_min=low or None, year_max=high or None,
                exact_isbn=self.exact_isbn.isChecked(), exact_code=self.exact_code.isChecked(),
                flags=[k for k, w in self.flags.items() if w.isChecked()], sort=self.sort_key, descending=self.descending,
                media_type=self.media_filter.currentData(), reading_status=self.reading_filter.currentData())
        except Exception as exc:
            self.owner.error(exc)
            return
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        for i, item in enumerate(self.rows):
            for j, (key, _) in enumerate(self.COLUMNS):
                value = str(item[key])
                if key in ("reading_status", "media_type"):
                    value = {"unread": tr('未阅读'), "reading": tr('阅读中'), "read": tr('已阅读'), "book": tr('图书'), "magazine": tr('杂志 / 期刊')}.get(value, value)
                if key == "summary":
                    full_text = " ".join(value.split())
                    summary_filter = self.filters.get("summary")
                    query = (summary_filter.text().strip() if summary_filter else "") or self.quick.text().strip()
                    match = full_text.casefold().find(query.casefold()) if query else 0
                    start = max(0, match - 25)
                    value = ("…" if start else "") + full_text[start:start+120] + ("…" if len(full_text) > start+120 else "")
                    value = value or tr('暂无简介')
                if key == "code" and not item["clc_code"] and item["media_type"] == "book":
                    value += tr(' · 临时')
                cell = QTableWidgetItem(value)
                cell.setToolTip(str(item[key]) if key == "summary" else value)
                self.table.setItem(i, j, cell)
        self.table.blockSignals(False)
        self.count.setText(tr('找到 {v0} 册 / {v1} 种书目', v0=(len(self.rows)), v1=(len({r['book_id'] for r in self.rows}))))
        self.cover.set_blob(None)
        self.details.clear()

    def clear(self):
        self.quick.clear()
        for combo in (self.media_filter, self.reading_filter):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        for widget in self.filters.values():
            widget.clear()
        self.year_min.clear()
        self.year_max.clear()
        self.mode.setCurrentIndex(0)
        for widget in self.flags.values():
            widget.blockSignals(True)
            widget.setChecked(False)
            widget.blockSignals(False)
        self.refresh()

    def change_preset(self, *_):
        self.sort_key, self.descending = self.sort.currentData(), False
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self.refresh()

    def default_sort(self):
        self.sort.setCurrentIndex(0)
        self.change_preset()

    def header_sort(self, column):
        key = self.COLUMNS[column][0]
        self.descending = not self.descending if self.sort_key == key else False
        self.sort_key = key
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSortIndicator(column, Qt.SortOrder.DescendingOrder if self.descending else Qt.SortOrder.AscendingOrder)
        self.refresh()

    def show_detail(self):
        item = self.selected()
        if not item:
            return
        data = self.owner.db.book(item["book_id"])
        self.cover.set_blob(data["cover_blob"])
        info = data["source_info"]
        state = {"unread": tr("未阅读"), "reading": tr("阅读中"), "read": tr("已阅读")}[item["reading_status"]]
        common = f"{data['title']}\n{item['code']}\n" + tr("阅读状态") + ": " + state
        if data["media_type"] == "magazine":
            self.details.setPlainText(common + f"\nISSN: {data['issn']}\n" + tr("年份") + f": {data['issue_year']}\n" +
                tr("期数") + f": {data['issue_number']}\n" + tr("位置") + f": {item['location']}\n" + tr("备注") + f": {item['notes']}")
        else:
            self.details.setPlainText(tr('{v0}\n{v1}\n\nISBN：{v2}\n本册年份：{v3}\n网络参考年份：{v4}\n位置：{v5}\n来源：{v6}\n原始分类：{v7}\n原始署名：{v8}\n\n简介：\n{v9}\n\n备注：{v10}', v0=(data['title']), v1=(item['code']), v2=(data['isbn'] or '—'), v3=(item['publication_year']), v4=(data['reference_pubdate'] or '—'), v5=(item['location'] or '—'), v6=(info.get('source_fullname',info.get('provider','—'))), v7=(info.get('raw_classification','')), v8=(info.get('responsibility','')), v9=(data['summary'] or tr('暂无简介')), v10=(item['notes'])) + "\n" + tr("阅读状态") + ": " + state)
            if info.get("provider") == "国家图书馆":
                self.details.append("\n" + tr("国图数据源仅供测试，不能用于商业用途。来源全称：中国国家图书馆·中国国家数字图书馆网站（https://www.nlc.cn/）。引用时保留作者与出处；其他使用须遵守该网站版权声明并取得所需许可。"))

    def edit(self):
        if self.selected():
            self.owner.edit_copy(self.selected()["id"])

    def enlarge_cover(self):
        if self.selected():
            from .dialogs import CoverViewer
            book = self.owner.db.book(self.selected()["book_id"])
            if book["cover_blob"]:
                dialog = CoverViewer(book["cover_blob"], book["title"], self.owner)
                dialog.exec()
                dialog.deleteLater()

    def add_copy(self):
        if self.selected():
            self.owner.load_book(self.selected()["book_id"])

    def delete(self):
        if self.selected():
            self.owner.delete_copy(self.selected())

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, tr('导出当前结果'), tr('藏书清单.csv'), "CSV (*.csv)")
        if path:
            try:
                self.owner.db.export_csv(path, self.rows)
                self.owner.notice(tr('已导出 {v0} 册：{v1}', v0=(len(self.rows)), v1=(path)))
            except Exception as exc:
                self.owner.error(exc)


class SettingsPage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        outer = QVBoxLayout(self)
        content = QWidget()
        layout = QVBoxLayout(content)
        self.about = QLabel(tr('{v0} {v1} · Windows x64 便携版\n作者：{v2}', v0=(APP_NAME), v1=(__version__), v2=(__author__)))
        self.about.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.about.setWordWrap(True)
        if OFFLINE:
            self.about.setText(self.about.text() + tr('\n离线版：不接入联网书目来源'))
        layout.addWidget(self.about)
        self.license_notice = QLabel(tr('原创代码：PolyForm Noncommercial 1.0.0；超出其允许用途的商用须另获书面授权。\n本应用动态使用 LGPLv3 的 Qt / PySide6 / Shiboken；您有权替换这些库并调试其修改。'))
        self.license_notice.setWordWrap(True)
        layout.addWidget(self.license_notice)
        layout.addWidget(row(button(tr('软件许可与第三方协议'), self.show_licenses),
                             button(tr('对应源码说明'), self.open_licenses)))
        layout.addWidget(row(button(tr('GitHub 项目页面'), lambda: QDesktopServices.openUrl(QUrl(PROJECT_URL))),
                             button(tr('问题反馈 / GitHub Issues'), lambda: QDesktopServices.openUrl(QUrl(ISSUES_URL)))))
        from .i18n import LANGUAGES
        self.language = QComboBox()
        for code, name in LANGUAGES.items():
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(0, self.language.findData(owner.language)))
        self.language.currentIndexChanged.connect(self.change_language)
        layout.addWidget(row(QLabel(tr('界面语言')), self.language))
        locations = QGroupBox(tr('预设存放地点'))
        location_layout = QVBoxLayout(locations)
        self.location_list = QListWidget()
        self.location_list.setFixedHeight(100)
        location_layout.addWidget(self.location_list)
        self.location_input = QLineEdit()
        self.location_input.setPlaceholderText(tr('例如：书房 A 架 / 客厅书柜上层'))
        self.location_input.setMaxLength(120)
        self.location_rename = button(tr('改名'), self.rename_location)
        self.location_delete = button(tr('删除预设'), self.delete_location)
        location_layout.addWidget(row(self.location_input, button(tr('添加地点'), self.add_location, primary=True),
                                     self.location_rename, self.location_delete))
        note = QLabel(tr('添加后自动保存，录入时可直接勾选一个地点。改名或删除预设不会改写已登记书籍的位置。'))
        note.setWordWrap(True)
        note.setObjectName("subtle")
        location_layout.addWidget(note)
        layout.addWidget(locations)
        self.location_list.currentRowChanged.connect(self.location_selected)
        self.location_input.returnPressed.connect(self.add_location)
        owner.location_presets_changed.connect(self.refresh_locations)
        self.refresh_locations()
        database = QGroupBox(tr('数据库与备份'))
        db_layout = QVBoxLayout(database)
        self.path = QLabel(tr('尚未选择数据库'))
        self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path.setWordWrap(True)
        db_layout.addWidget(self.path)
        db_layout.addWidget(row(button(tr('打开现有库'), owner.open_database), button(tr('新建数据库'), owner.new_database),
                                 button(tr('另存副本并切换'), lambda: owner.backup_database(switch=True)),
                                 button(tr('完整备份'), owner.backup_database)))
        db_layout.addWidget(QLabel(tr('书目、封面和分类字典均保存在同一个 SQLite 文件中。请将数据库放在便于备份的位置。')))
        db_layout.addWidget(row(button(tr('导入分类 CSV'), owner.import_classifications), button(tr('重置当前数据库…'), owner.reset_database)))
        layout.addWidget(database)
        sources = QGroupBox(tr('书目来源与摄像头'))
        form = QFormLayout(sources)
        self.source = QComboBox()
        self.populate_sources(owner.settings.get("source", "auto"))
        form.addRow(tr('书目来源'), self.source)
        self.nlc_notice = QLabel(tr('国图数据源仅供测试，不能用于商业用途。来源全称：中国国家图书馆·中国国家数字图书馆网站（https://www.nlc.cn/）。引用时保留作者与出处；其他使用须遵守该网站版权声明并取得所需许可。'))
        self.nlc_notice.setWordWrap(True)
        self.nlc_notice.setVisible(owner.language == "zh_CN" and not OFFLINE)
        form.addRow("", self.nlc_notice)
        self.nlc_terms = button(tr('查看国图版权声明'), lambda: QDesktopServices.openUrl(QUrl("https://www.nlc.cn/web/dsb_footer/bqsm/index.shtml")))
        self.nlc_terms.setVisible(owner.language == "zh_CN" and not OFFLINE)
        form.addRow("", self.nlc_terms)
        self.key = QLineEdit(owner.settings.get("google_key", ""))
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText(tr('可选；只保存在本机设置中，不写入书目库'))
        if OFFLINE:
            self.key.clear()
            self.key.setEnabled(False)
            self.key.setPlaceholderText(tr('离线版不使用 API Key'))
        form.addRow("Google Books Key", self.key)
        self.camera_index = QSpinBox()
        self.camera_index.setRange(-1, 20)
        self.camera_index.setSpecialValueText(tr('自动选择首个可用设备'))
        self.camera_index.setValue(owner.settings.get("camera_index", -1))
        form.addRow(tr('摄像头序号'), self.camera_index)
        form.addRow("", button(tr('保存设置'), self.save, primary=True))
        layout.addWidget(sources)
        help_label = QLabel(tr('无需安装 Python、Qt、OpenCV 或模型运行环境；保持 exe 与 _internal 文件夹在一起。\n离线可录入、编辑、检索、拍摄封面。联网仅用于您发起的书目查询和封面下载。\n自动分类依赖来源提供的中图分类号；未命中时可手填或暂存为待分类。\n录入页：ISBN 回车查询；资料就绪后回车保存；Ctrl+Enter 保存；Esc 重置。\n简介中的回车保留换行；输入框中的 Backspace 正常删除字符。'))
        help_label.setWordWrap(True)
        if OFFLINE:
            help_label.setText(help_label.text().replace(tr('联网仅用于您发起的书目查询和封面下载。'), tr('此版本不进行联网书目查询或封面下载。')))
        help_label.setObjectName("subtle")
        layout.addWidget(help_label)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def open_licenses(self):
        from .licenses import LicenseDialog
        dialog = LicenseDialog(self.owner, show_sources=True)
        dialog.exec()
        dialog.deleteLater()

    def show_licenses(self):
        from .licenses import LicenseDialog
        dialog = LicenseDialog(self.owner)
        dialog.exec()
        dialog.deleteLater()

    def populate_sources(self, selected="auto"):
        self.source.clear()
        if OFFLINE:
            choices = [("manual", tr('仅手工 / 离线'))]
        else:
            choices = [("auto", tr('自动：Open Library → Google Books'))]
            if self.owner.language == "zh_CN":
                choices = [("auto", tr('自动：国图 → Open Library → Google Books')), ("nlc", tr('国图（仅测试、非商用）'))]
            choices += [("openlibrary", "Open Library"), ("google", tr('Google Books（需 API Key，受服务条款约束）')), ("manual", tr('仅手工 / 离线'))]
        for value, name in choices:
            self.source.addItem(name, value)
        self.source.setCurrentIndex(max(0, self.source.findData(selected)))

    def change_language(self):
        if self.owner.set_language(self.language.currentData()):
            selected = self.source.currentData()
            self.populate_sources(selected)
            self.nlc_notice.setVisible(self.owner.language == "zh_CN" and not OFFLINE)
            self.nlc_terms.setVisible(self.owner.language == "zh_CN" and not OFFLINE)

    def refresh_locations(self):
        selected = self.location_list.currentItem()
        selected_name = selected.text() if selected else None
        names = self.owner.location_presets()
        self.location_list.clear()
        self.location_list.addItems(names)
        self.location_list.setCurrentRow(names.index(selected_name) if selected_name in names else -1)
        self.location_selected(self.location_list.currentRow())

    def location_selected(self, index):
        self.location_rename.setEnabled(index >= 0)
        self.location_delete.setEnabled(index >= 0)
        if index >= 0:
            self.location_input.setText(self.location_list.item(index).text())

    def commit_locations(self, names):
        try:
            if self.owner.set_location_presets(names):
                self.location_input.clear()
                return True
        except ValueError as exc:
            self.owner.error(exc)
        return False

    def add_location(self):
        self.commit_locations([*self.owner.location_presets(), self.location_input.text()])

    def rename_location(self):
        index = self.location_list.currentRow()
        if index >= 0:
            names = self.owner.location_presets()
            names[index] = self.location_input.text()
            self.commit_locations(names)

    def delete_location(self):
        index = self.location_list.currentRow()
        if index >= 0:
            names = self.owner.location_presets()
            del names[index]
            self.commit_locations(names)

    def save(self):
        self.owner.settings.update(source=self.source.currentData(), google_key=self.key.text().strip(),
                                   camera_index=self.camera_index.value())
        if self.owner.persist_settings():
            self.owner.notice(tr('设置已保存；摄像头序号将在下次开启摄像头时生效'))
