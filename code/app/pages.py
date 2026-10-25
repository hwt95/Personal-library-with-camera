import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLabel, QLineEdit, QTextEdit, QComboBox, QCheckBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QSplitter, QGroupBox, QFileDialog, QSpinBox, QScrollArea,
    QMenu, QListWidget)

from .widgets import ImageView, CameraControls, row, button
from . import APP_NAME, __version__, __author__


FIELD_LABELS = {"isbn": "ISBN", "title": "书名 *", "authors": "作者 / 编者", "translators": "译者",
                "publisher": "出版社", "clc_code": "主分类号", "publication_year": "本册年份 *",
                "location": "藏书位置", "notes": "备注", "summary": "简介"}


class EntryPage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner, self.fields = owner, {}
        self.cover_blob, self.cover_mime, self.source_info = None, "", {}
        self.dirty = set()
        self.book_id = self.copy_id = None
        self.reference_pubdate = ""
        self.last_saved_book = None
        layout = QVBoxLayout(self)
        split = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.camera_view = ImageView("开启摄像头后可扫描图书条码\n也可直接手工输入资料", guide=True)
        self.camera_view.setMinimumSize(260, 180)
        left_layout.addWidget(self.camera_view, 2)
        self.camera_button = button("开启摄像头", owner.toggle_camera)
        left_layout.addWidget(self.camera_button)
        self.camera_status = QLabel("支持 EAN-13 图书条码，画面不镜像")
        self.camera_status.setWordWrap(True)
        self.camera_status.setObjectName("subtle")
        left_layout.addWidget(self.camera_status)
        self.camera_controls = CameraControls(owner)
        left_layout.addWidget(self.camera_controls)
        self.cover_view = ImageView("暂无封面\n可以拍摄或从文件导入")
        self.cover_view.setMinimumHeight(130)
        self.cover_view.setMaximumHeight(220)
        left_layout.addWidget(self.cover_view, 2)
        left_layout.addWidget(row(button("拍摄封面", owner.capture_cover), button("导入图片", owner.import_cover)))
        left_layout.addWidget(button("清除封面", lambda: self.set_cover(None, "manual")))
        split.addWidget(left)
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setVerticalSpacing(10)
        for key, label in FIELD_LABELS.items():
            widget = QTextEdit() if key == "summary" else QLineEdit()
            if key == "summary":
                widget.setAcceptRichText(False)
                widget.setFixedHeight(105)
                widget.textChanged.connect(lambda k=key: self.mark_dirty(k))
            else:
                widget.textEdited.connect(lambda _, k=key: self.mark_dirty(k))
            self.fields[key] = widget
            if key == "isbn":
                widget.setPlaceholderText("手工输入 ISBN-10/13，回车查询")
                self.lookup_button = button("查询资料", owner.lookup_isbn, primary=True)
                self.cancel_button = button("取消查询", owner.cancel_lookup)
                self.cancel_button.setEnabled(False)
                form.addRow(label, row(widget, self.lookup_button, self.cancel_button))
            elif key == "clc_code":
                widget.setPlaceholderText("例如 TP303；留空表示待分类")
                form.addRow(label, row(widget, button("分类字典", owner.choose_classification)))
                self.clc_hint = QLabel("待分类 · 保存后生成临时编号")
                self.clc_hint.setObjectName("subtle")
                form.addRow("", self.clc_hint)
                widget.textChanged.connect(owner.update_classification_hint)
            elif key == "publication_year":
                widget.setPlaceholderText("填写手中这一册的出版年份")
                self.year_reference = QLabel("网络参考年份：—")
                self.year_reference.setObjectName("subtle")
                form.addRow(label, row(widget, self.year_reference))
            elif key == "location":
                widget.setPlaceholderText("勾选预设地点，也可直接填写")
                self.location_button = button("勾选预设地点", lambda: None)
                self.location_menu = QMenu(self)
                self.location_group = QActionGroup(self.location_menu)
                self.location_group.setExclusive(True)
                self.location_button.setMenu(self.location_menu)
                self.location_menu.aboutToShow.connect(self.refresh_location_menu)
                owner.location_presets_changed.connect(self.refresh_location_menu)
                form.addRow(label, row(widget, self.location_button))
            else:
                form.addRow(label, widget)
        self.new_edition = QCheckBox("作为新版本登记（即使 ISBN 相同也建立独立书目）")
        form.addRow("", self.new_edition)
        self.edit_hint = QLabel("新书录入 · 书名和本册年份必填，其他资料可稍后补充")
        self.edit_hint.setWordWrap(True)
        form.addRow("", self.edit_hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_widget)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        split.addWidget(scroll)
        split.setSizes([330, 780])
        layout.addWidget(split, 1)
        self.status = QLabel("离线可用 · 扫码、手工查询或直接填写资料")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.save_button = button("确认保存（Ctrl+Enter）", owner.save_entry, primary=True)
        self.repeat_button = button("继续录入同书副本", owner.repeat_copy)
        self.repeat_button.setEnabled(False)
        layout.addWidget(row(self.save_button, button("重置草稿（Esc）", owner.reset_entry), self.repeat_button))

    def refresh_location_menu(self):
        self.location_menu.clear()
        current = self.text("location")
        names = self.owner.location_presets()
        for name in ["", *names]:
            action = self.location_menu.addAction(name.replace("&", "&&") if name else "不设置地点")
            action.setCheckable(True)
            action.setData(name)
            self.location_group.addAction(action)
            action.setChecked(current == name)
            action.triggered.connect(lambda checked, selected=name: self.select_location(selected))
        if not names:
            self.location_menu.addAction("请先在设置页添加存放地点").setEnabled(False)
        self.location_menu.addSeparator()
        self.location_menu.addAction("管理预设地点…", self.owner.show_location_settings)

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
        return data

    def fill(self, data, *, protect=True):
        protected = self.dirty.copy() if protect else set()
        for key in ("isbn", "title", "authors", "translators", "publisher", "clc_code", "summary"):
            if key not in protected and data.get(key):
                self.set_text(key, data[key])
        self.reference_pubdate = data.get("reference_pubdate", "")
        self.year_reference.setText("网络参考年份：" + (self.reference_pubdate or "—"))
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
        self.year_reference.setText("网络参考年份：—")
        self.book_id = self.copy_id = None
        self.dirty.clear()
        self.new_edition.setChecked(False)
        self.new_edition.setEnabled(True)
        self.edit_hint.setText("新书录入 · 书名和本册年份必填，其他资料可稍后补充")
        self.save_button.setText("确认保存（Ctrl+Enter）")
        self.owner.update_classification_hint()


class CatalogPage(QWidget):
    COLUMNS = [("code", "馆藏编码"), ("title", "书名"), ("summary", "简介"), ("authors", "作者"), ("translators", "译者"),
               ("publisher", "出版社"), ("publication_year", "年份"), ("clc_code", "分类号"),
               ("location", "位置"), ("created_at", "录入时间（UTC）"), ("copy_count", "册数")]

    def __init__(self, owner, *, advanced=False):
        super().__init__()
        self.owner, self.advanced = owner, advanced
        self.rows, self.filters = [], {}
        self.sort_key, self.descending = "default", False
        layout = QVBoxLayout(self)
        self.quick = QLineEdit()
        self.quick.setPlaceholderText("搜索书名、作者、简介、出版社、ISBN 或馆藏编码")
        self.quick.returnPressed.connect(self.refresh)
        layout.addWidget(row(self.quick, button("搜索", self.refresh, primary=True), button("清空条件", self.clear)))
        grid = QGridLayout()
        keys = [("clc_code", "分类号前缀"), ("authors", "作者包含"), ("publisher", "出版社包含"), ("location", "位置包含")]
        if advanced:
            keys = [("title", "书名包含"), ("authors", "作者包含"), ("translators", "译者包含"),
                    ("publisher", "出版社包含"), ("isbn", "ISBN"), ("code", "馆藏编码"),
                    ("clc_code", "分类号前缀"), ("location", "位置包含"), ("summary", "简介包含")]
        for i, (key, label) in enumerate(keys):
            edit = QLineEdit()
            edit.setPlaceholderText(label)
            edit.returnPressed.connect(self.refresh)
            self.filters[key] = edit
            grid.addWidget(edit, i // 4, i % 4, 1, 4 if key == "summary" else 1)
        layout.addLayout(grid)
        self.year_min, self.year_max = QLineEdit(), QLineEdit()
        self.year_min.setPlaceholderText("起始年份")
        self.year_max.setPlaceholderText("结束年份")
        self.mode = QComboBox()
        self.mode.addItem("全部条件满足（AND）", "AND")
        if advanced:
            self.mode.addItem("任一条件满足（OR）", "OR")
        self.exact_isbn, self.exact_code = QCheckBox("ISBN 精确"), QCheckBox("编码精确")
        self.exact_isbn.setChecked(True)
        self.exact_code.setChecked(True)
        layout.addWidget(row(self.year_min, self.year_max, self.mode, self.exact_isbn, self.exact_code))
        self.exact_isbn.setVisible(advanced)
        self.exact_code.setVisible(advanced)
        self.flags = {"pending": QCheckBox("待分类"), "no_cover": QCheckBox("缺封面"), "duplicates": QCheckBox("有副本")}
        self.sort = QComboBox()
        self.sort.addItem("默认：完整编码 ↑ / 年份 ↓", "default")
        self.sort.addItem("同书目：年份从新到旧", "edition_year")
        self.sort.currentIndexChanged.connect(self.change_preset)
        for control in self.flags.values():
            control.toggled.connect(self.refresh)
        layout.addWidget(row(*self.flags.values(), self.sort, button("恢复默认排序", self.default_sort)))
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
        self.cover = ImageView("选择记录查看封面")
        self.cover.setMinimumWidth(190)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        detail_layout.addWidget(self.cover, 2)
        detail_layout.addWidget(self.details, 3)
        split.addWidget(detail)
        split.setSizes([900, 240])
        layout.addWidget(split, 1)
        self.count = QLabel("0 册")
        layout.addWidget(row(self.count, button("编辑所选", self.edit), button("新增同书副本", self.add_copy),
                             button("删除所选单册", self.delete), button("导出当前结果 CSV", self.export)))

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
                raise ValueError("起始年份不能大于结束年份")
            self.rows = self.owner.db.search(self.quick.text(), {k: w.text().strip() for k, w in self.filters.items()},
                mode=self.mode.currentData(), year_min=low or None, year_max=high or None,
                exact_isbn=self.exact_isbn.isChecked(), exact_code=self.exact_code.isChecked(),
                flags=[k for k, w in self.flags.items() if w.isChecked()], sort=self.sort_key, descending=self.descending)
        except Exception as exc:
            self.owner.error(exc)
            return
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        for i, item in enumerate(self.rows):
            for j, (key, _) in enumerate(self.COLUMNS):
                value = str(item[key])
                if key == "summary":
                    full_text = " ".join(value.split())
                    summary_filter = self.filters.get("summary")
                    query = (summary_filter.text().strip() if summary_filter else "") or self.quick.text().strip()
                    match = full_text.casefold().find(query.casefold()) if query else 0
                    start = max(0, match - 25)
                    value = ("…" if start else "") + full_text[start:start+120] + ("…" if len(full_text) > start+120 else "")
                    value = value or "暂无简介"
                if key == "code" and not item["clc_code"]:
                    value += " · 临时"
                cell = QTableWidgetItem(value)
                cell.setToolTip(str(item[key]) if key == "summary" else value)
                self.table.setItem(i, j, cell)
        self.table.blockSignals(False)
        self.count.setText(f"找到 {len(self.rows)} 册 / {len({r['book_id'] for r in self.rows})} 种书目")
        self.cover.set_blob(None)
        self.details.clear()

    def clear(self):
        self.quick.clear()
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
        self.details.setPlainText(f"{data['title']}\n{item['code']}\n\nISBN：{data['isbn'] or '无'}\n"
            f"本册年份：{item['publication_year']}\n网络参考年份：{data['reference_pubdate'] or '无'}\n"
            f"位置：{item['location'] or '未设置'}\n来源：{info.get('provider','手工')}\n"
            f"原始分类：{info.get('raw_classification','')}\n原始署名：{info.get('responsibility','')}\n\n"
            f"简介：\n{data['summary'] or '暂无简介'}\n\n备注：{item['notes']}")

    def edit(self):
        if self.selected():
            self.owner.edit_copy(self.selected()["id"])

    def add_copy(self):
        if self.selected():
            self.owner.load_book(self.selected()["book_id"])

    def delete(self):
        if self.selected():
            self.owner.delete_copy(self.selected())

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出当前结果", "藏书清单.csv", "CSV (*.csv)")
        if path:
            try:
                self.owner.db.export_csv(path, self.rows)
                self.owner.notice(f"已导出 {len(self.rows)} 册：{path}")
            except Exception as exc:
                self.owner.error(exc)


class SettingsPage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        outer = QVBoxLayout(self)
        content = QWidget()
        layout = QVBoxLayout(content)
        self.about = QLabel(f"{APP_NAME} {__version__} · Windows x64 便携版\n作者：{__author__}")
        self.about.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.about.setWordWrap(True)
        layout.addWidget(self.about)
        locations = QGroupBox("预设存放地点")
        location_layout = QVBoxLayout(locations)
        self.location_list = QListWidget()
        self.location_list.setFixedHeight(100)
        location_layout.addWidget(self.location_list)
        self.location_input = QLineEdit()
        self.location_input.setPlaceholderText("例如：书房 A 架 / 客厅书柜上层")
        self.location_input.setMaxLength(120)
        self.location_rename = button("改名", self.rename_location)
        self.location_delete = button("删除预设", self.delete_location)
        location_layout.addWidget(row(self.location_input, button("添加地点", self.add_location, primary=True),
                                     self.location_rename, self.location_delete))
        note = QLabel("添加后自动保存，录入时可直接勾选一个地点。改名或删除预设不会改写已登记书籍的位置。")
        note.setWordWrap(True)
        note.setObjectName("subtle")
        location_layout.addWidget(note)
        layout.addWidget(locations)
        self.location_list.currentRowChanged.connect(self.location_selected)
        self.location_input.returnPressed.connect(self.add_location)
        owner.location_presets_changed.connect(self.refresh_locations)
        self.refresh_locations()
        database = QGroupBox("数据库与备份")
        db_layout = QVBoxLayout(database)
        self.path = QLabel("尚未选择数据库")
        self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path.setWordWrap(True)
        db_layout.addWidget(self.path)
        db_layout.addWidget(row(button("打开现有库", owner.open_database), button("新建数据库", owner.new_database),
                                 button("另存副本并切换", lambda: owner.backup_database(switch=True)),
                                 button("完整备份", owner.backup_database)))
        db_layout.addWidget(QLabel("书目、封面和分类字典均保存在同一个 SQLite 文件中。请将数据库放在便于备份的位置。"))
        db_layout.addWidget(row(button("导入分类 CSV", owner.import_classifications), button("重置当前数据库…", owner.reset_database)))
        layout.addWidget(database)
        sources = QGroupBox("书目来源与摄像头")
        form = QFormLayout(sources)
        self.source = QComboBox()
        for value, name in (("auto", "自动：国图 → Open Library → 已配置的 Google Books"), ("nlc", "国家图书馆（无应用层超时，可取消）"),
                            ("openlibrary", "Open Library"), ("google", "Google Books（需免费可用 Key）"), ("manual", "仅手工 / 离线")):
            self.source.addItem(name, value)
        self.source.setCurrentIndex(max(0, self.source.findData(owner.settings.get("source", "auto"))))
        form.addRow("书目来源", self.source)
        self.key = QLineEdit(owner.settings.get("google_key", ""))
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("可选；只保存在本机设置中，不写入书目库")
        form.addRow("Google Books Key", self.key)
        self.camera_index = QSpinBox()
        self.camera_index.setRange(-1, 20)
        self.camera_index.setSpecialValueText("自动选择首个可用设备")
        self.camera_index.setValue(owner.settings.get("camera_index", -1))
        form.addRow("摄像头序号", self.camera_index)
        form.addRow("", button("保存设置", self.save, primary=True))
        layout.addWidget(sources)
        help_label = QLabel("无需安装 Python、Qt、OpenCV 或模型运行环境；保持 exe 与 _internal 文件夹在一起。\n"
            "离线可录入、编辑、检索、拍摄封面。联网仅用于您发起的书目查询和封面下载。\n"
            "自动分类依赖来源提供的中图分类号；未命中时可手填或暂存为待分类。\n"
            "录入页：ISBN 回车查询；资料就绪后回车保存；Ctrl+Enter 保存；Esc 重置。\n"
            "简介中的回车保留换行；输入框中的 Backspace 正常删除字符。")
        help_label.setWordWrap(True)
        help_label.setObjectName("subtle")
        layout.addWidget(help_label)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)
        outer.addWidget(scroll)

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
            self.owner.notice("设置已保存；摄像头序号将在下次开启摄像头时生效")
