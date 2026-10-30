import json
from pathlib import Path
import time

from PySide6.QtCore import Qt, QEvent, QTimer, QLockFile, QStandardPaths, Signal
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QLabel, QTabWidget,
    QMessageBox, QFileDialog, QInputDialog, QDialog, QLineEdit, QTextEdit, QAbstractSpinBox, QComboBox)

from .database import LibraryDB
from .domain import ScanGate, accession, normalize_isbn, normalize_issn, publication_barcode, validate_year, normalize_location_presets
from . import APP_NAME, __version__
from .imaging import decode_image
from .widgets import row
from .pages import EntryPage, CatalogPage, SettingsPage
from .dialogs import CandidateDialog, ClassificationDialog, CoverDialog
from .workers import CameraWorker, LookupWorker
from .chrome import TitleBar, app_icon
from .i18n import set_language, translate_tree, translated, tr, default_language, LANGUAGES


class MainWindow(QMainWindow):
    camera_controls_changed = Signal()
    location_presets_changed = Signal()

    def __init__(self, settings_path, *, database=None, smoke=False):
        super().__init__()
        self.settings_path = Path(settings_path)
        try:
            self.settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.settings = {}
        self.language = self.settings.get("language", default_language())
        if self.language not in LANGUAGES:
            self.language = default_language()
        set_language(self.language)
        self.db = self.db_lock = None
        self.camera = self.network = None
        self.camera_zoom = 1.0
        self.camera_autofocus = True
        self.camera_focus_state = {}
        self.camera_mode = "scan"
        self.camera_stopping = False
        self.jobs = set()
        self.scan_gate = ScanGate()
        self.request_token = 0
        self.queried_isbn = self.pending_isbn = ""
        self.lookup_started = 0.0
        self.lookup_stage = ""
        self.saving, self.cover_open, self.closing, self.smoke = False, False, False, smoke
        self.setWindowTitle(tr(APP_NAME) + " " + __version__)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setWindowIcon(app_icon())
        self.resize(1260, 880)
        self.setMinimumSize(980, 720)
        size = self.settings.get("window_size", [])
        if isinstance(size, list) and len(size) == 2 and all(isinstance(v, int) for v in size):
            self.resize(max(980, min(size[0], 2400)), max(720, min(size[1], 1600)))
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 8, 12, 12)
        self.title_bar = TitleBar(self)
        layout.addWidget(self.title_bar)
        title = QLabel(tr('我的藏书'))
        title.setObjectName("heading")
        self.counter = QLabel(tr('本地保存 · 随时查找'))
        self.counter.setObjectName("subtle")
        layout.addWidget(row(title, self.counter))
        self.tabs = QTabWidget()
        self.entry = EntryPage(self)
        self.catalog = CatalogPage(self)
        self.search_page = CatalogPage(self, advanced=True)
        self.settings_page = SettingsPage(self)
        for page, label in ((self.entry, tr('录入')), (self.catalog, tr('藏书列表')), (self.search_page, tr('组合检索')), (self.settings_page, tr('设置'))):
            self.tabs.addTab(page, label)
        layout.addWidget(self.tabs)
        self.setCentralWidget(container)
        translate_tree(self)
        self.tabs.currentChanged.connect(self.tab_changed)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        QApplication.instance().installEventFilter(self)
        if database:
            self.switch_database(database)
        elif self.settings.get("database"):
            path = Path(self.settings["database"])
            if not path.is_absolute():
                path = self.settings_path.parent / path
            try:
                self.switch_database(path)
            except Exception as exc:
                QTimer.singleShot(0, lambda message=str(exc): self.error(message))
        if not self.db and not smoke:
            QTimer.singleShot(0, self.first_run)

    def first_run(self):
        choice = QMessageBox(self)
        choice.setWindowTitle(tr('选择藏书数据库'))
        choice.setText(tr('请选择书籍与封面的保存位置。已有数据库可直接打开。'))
        new = choice.addButton(tr('新建数据库'), QMessageBox.ButtonRole.AcceptRole)
        existing = choice.addButton(tr('打开现有库'), QMessageBox.ButtonRole.ActionRole)
        choice.addButton(tr('稍后选择'), QMessageBox.ButtonRole.RejectRole)
        choice.exec()
        if choice.clickedButton() == new:
            self.new_database()
        elif choice.clickedButton() == existing:
            self.open_database()
        else:
            self.tabs.setCurrentWidget(self.settings_page)

    def error(self, error):
        QMessageBox.warning(self, tr('未能完成操作'), translated(str(error)))

    def notice(self, message):
        self.statusBar().showMessage(message, 12000)

    def persist_settings(self):
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.settings_path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.settings_path)
            return True
        except OSError as exc:
            self.error(tr('设置未能保存：{v0}\n书目数据不受影响；下次启动可重新选择数据库。', v0=(exc)))
            return False

    def location_presets(self):
        try:
            return normalize_location_presets(self.settings.get("location_presets", []))
        except ValueError:
            return []

    def set_location_presets(self, values):
        clean = normalize_location_presets(values)
        previous = dict(self.settings)
        self.settings["location_presets"] = clean
        if not self.persist_settings():
            self.settings = previous
            return False
        self.location_presets_changed.emit()
        self.notice(tr('存放地点已保存，录入页可直接勾选'))
        return True

    def show_location_settings(self):
        self.tabs.setCurrentWidget(self.settings_page)
        self.settings_page.location_input.setFocus()

    def set_language(self, language):
        old = self.language
        self.settings["language"] = language
        if not self.persist_settings():
            self.settings["language"] = old
            return False
        self.cancel_lookup(silent=True)
        self.language = language
        set_language(language)
        self.setWindowTitle(tr(APP_NAME) + " " + __version__)
        translate_tree(self)
        self.catalog.refresh()
        self.search_page.refresh()
        return True

    def keep_job(self, job):
        self.jobs.add(job)
        def finished():
            self.jobs.discard(job)
            if self.network is job:
                self.network = None
                self.entry.cancel_button.setEnabled(False)
            job.deleteLater()
        job.finished.connect(finished)

    def tick(self):
        if self.camera and self.camera.isRunning() and not self.cover_open and not self.camera_stopping:
            frame = self.camera.latest()
            if frame is not None and self.tabs.currentIndex() == 0:
                self.entry.camera_view.set_frame(frame)
        if self.network:
            elapsed = int(time.monotonic() - self.lookup_started)
            self.entry.status.setText(tr('正在查询 {v0} · 已等待 {v1} 秒 · 可取消并手工补充', v0=(self.lookup_stage), v1=(elapsed)))

    def tab_changed(self, index):
        if index in (1, 2):
            self.tabs.widget(index).refresh()
        self.update_scanning()

    def update_scanning(self):
        if self.camera:
            self.camera.scanning = (self.camera_mode == "scan" and not self.camera_stopping and
                                    self.tabs.currentIndex() == 0 and not self.cover_open)

    def cover_mode(self):
        self.camera_mode = "cover"
        self.entry.camera_view.guide = False
        self.update_scanning()
        self.entry.camera_status.setText(tr('封面拍摄模式 · 条码识别已暂停，可直接拍摄封面'))

    def refresh_library(self):
        if self.db:
            books, copies = self.db.stats()
            self.counter.setText(tr('{v0} 种书目  ·  {v1} 册馆藏  ·  本地保存', v0=(books), v1=(copies)))
            self.settings_page.path.setText(str(self.db.path))
        self.catalog.refresh()
        self.search_page.refresh()

    def suggested_database(self):
        return str(Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)) / tr('个人藏书') / "library.sqlite3")

    def switch_database(self, path, *, create=False):
        path = Path(path).resolve()
        if self.db and path == self.db.path:
            if create:
                raise ValueError(tr('不能用新库覆盖当前数据库'))
            return
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(path) + ".app-lock")
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            raise ValueError(tr('数据库已由另一个窗口打开，或所在目录不可写'))
        try:
            replacement = LibraryDB(path, create=create)
        except Exception:
            lock.unlock()
            raise
        self.cancel_lookup(silent=True)
        previous, previous_lock = self.db, self.db_lock
        self.db, self.db_lock = replacement, lock
        if previous:
            previous.close()
        if previous_lock:
            previous_lock.unlock()
        try:
            saved_path = str(path.relative_to(self.settings_path.parent))
        except ValueError:
            saved_path = str(path)
        self.settings["database"] = saved_path
        if not self.smoke:
            self.persist_settings()
        self.reset_entry()
        self.entry.last_saved_book = None
        self.entry.repeat_button.setEnabled(False)
        self.refresh_library()
        self.notice(tr('当前数据库：{v0}', v0=(path)))
        if self.db.migration_backup:
            self.notice(tr('编号已升级为分类内序号，升级前备份：{path}', path=self.db.migration_backup))

    def open_database(self):
        path, _ = QFileDialog.getOpenFileName(self, tr('打开藏书数据库'), "", tr('SQLite (*.sqlite3 *.db *.sqlite);;所有文件 (*)'))
        if path and self.discard_draft():
            try:
                self.switch_database(path)
            except Exception as exc:
                self.error(exc)

    def new_database(self):
        path, _ = QFileDialog.getSaveFileName(self, tr('新建数据库（请选择尚不存在的文件）'), self.suggested_database(), "SQLite (*.sqlite3)")
        if path and self.discard_draft():
            try:
                self.switch_database(path, create=True)
            except Exception as exc:
                self.error(exc)

    def backup_database(self, checked=False, *, switch=False):
        if not self.db:
            self.error(tr('请先选择数据库'))
            return
        suggested = str(self.db.path.with_name(self.db.path.stem + tr('-副本.sqlite3')))
        path, _ = QFileDialog.getSaveFileName(self, tr('保存完整数据库副本（不能覆盖已有文件）'), suggested, "SQLite (*.sqlite3)")
        if path:
            try:
                self.db.backup(path)
                if switch and self.discard_draft():
                    self.switch_database(path)
                self.notice(tr('完整备份已保存：{v0}', v0=(path)))
            except Exception as exc:
                self.error(exc)

    def reset_database(self):
        if not self.db:
            return
        count = self.db.stats()[1]
        answer = QMessageBox.question(self, tr('重置当前库'), tr('当前数据库：\n{v0}\n\n将清除 {v1} 册馆藏、书目和编号计数。分类字典保留。\n程序将先自动创建完整备份，备份失败则取消重置。\n确定继续？', v0=(self.db.path), v1=(count)), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            backup = self.db.reset_with_backup()
            self.reset_entry()
            self.entry.last_saved_book = None
            self.entry.repeat_button.setEnabled(False)
            self.refresh_library()
            QMessageBox.information(self, tr('重置完成'), tr('备份已保存：\n{v0}\n\n可用“打开现有库”恢复查看。', v0=(backup)))
        except Exception as exc:
            self.error(exc)

    def import_classifications(self):
        if not self.db:
            self.error(tr('请先选择数据库'))
            return
        path, _ = QFileDialog.getOpenFileName(self, tr('导入分类字典'), "", "CSV (*.csv)")
        if path:
            try:
                count = self.db.import_classifications(path)
                self.update_classification_hint()
                self.notice(tr('已导入 {v0} 个分类条目', v0=(count)))
            except Exception as exc:
                self.error(exc)

    def update_classification_hint(self, *_):
        if not hasattr(self, "entry"):
            return
        code = self.entry.text("clc_code").upper()
        if not code:
            label = tr('待分类 · 保存后生成临时编号')
        else:
            match = next((r for r in self.db.classifications(code) if r["code"] == code), None) if self.db else None
            label = match["name"] if match else tr('名称未收录 · 此分类号仍可保存')
        self.entry.clc_hint.setText(label)

    def choose_classification(self):
        if not self.db:
            self.error(tr('请先选择数据库'))
            return
        dialog = ClassificationDialog(self.db, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.entry.set_text("clc_code", dialog.selected_code)
            self.entry.dirty.add("clc_code")
            self.update_classification_hint()
        dialog.deleteLater()

    def toggle_camera(self):
        if self.camera:
            self.camera_stopping = True
            self.update_scanning()
            self.camera.requestInterruption()
            self.entry.camera_button.setEnabled(False)
            self.entry.camera_view.set_frame(None)
            self.entry.camera_status.setText(tr('正在关闭摄像头…'))
            QTimer.singleShot(3000, self.camera_stop_timeout)
            return
        camera = CameraWorker(self.settings.get("camera_index", -1), self)
        self.camera = camera
        self.camera_focus_state = {"note": tr('正在打开摄像头并请求自动对焦…')}
        camera.set_zoom(self.camera_zoom)
        camera.set_autofocus(self.camera_autofocus)
        self.camera_stopping = False
        self.update_scanning()
        camera.barcode.connect(self.on_barcode)
        def camera_message(message):
            if self.camera is not camera or self.camera_stopping:
                return
            if self.camera_mode == "cover" and message == tr('摄像头已开启，请将条码置于画面中央'):
                self.cover_mode()
            else:
                self.entry.camera_status.setText(message)
        camera.message.connect(camera_message)
        camera.focus_changed.connect(self.camera_focus_changed)
        def stopped():
            if self.camera is camera:
                self.camera = None
                self.camera_stopping = False
                self.entry.camera_button.setText(tr('开启摄像头'))
                self.entry.camera_button.setEnabled(True)
                self.entry.camera_status.setText(tr('摄像头已关闭，可手工录入或导入封面'))
                self.entry.camera_view.set_frame(None)
                self.camera_focus_state = {}
                self.camera_controls_changed.emit()
            camera.deleteLater()
        camera.finished.connect(stopped)
        self.entry.camera_button.setText(tr('关闭摄像头'))
        self.camera_controls_changed.emit()
        camera.start()

    def camera_stop_timeout(self):
        if self.camera and self.camera_stopping:
            self.entry.camera_status.setText(tr('预览已停止；摄像头驱动尚未释放设备，请重新连接摄像头'))

    def camera_focus_changed(self, state):
        self.camera_focus_state = state
        if state.get("supported") is False:
            self.camera_autofocus = state.get("enabled", False)
        self.camera_controls_changed.emit()

    def set_camera_zoom(self, factor):
        self.camera_zoom = max(1.0, min(4.0, float(factor)))
        if self.camera:
            self.camera.set_zoom(self.camera_zoom)
        self.camera_controls_changed.emit()

    def set_camera_autofocus(self, enabled):
        self.camera_autofocus = bool(enabled)
        if self.camera:
            self.camera.set_autofocus(enabled)
        self.camera_controls_changed.emit()

    def refocus_camera(self):
        self.camera_autofocus = True
        if self.camera:
            self.camera.set_autofocus(True, refocus=True)
        self.camera_controls_changed.emit()

    def on_barcode(self, isbn):
        if self.camera_mode != "scan" or self.cover_open or self.tabs.currentIndex() != 0 or QApplication.activeModalWidget() or QApplication.activePopupWidget():
            return
        hit = self.scan_gate.observe(isbn)
        if hit:
            record = publication_barcode(hit)
            self.entry.apply_barcode(record)
            self.cover_mode()
            if record["media_type"] == "book":
                self.lookup_isbn()
            else:
                self.queried_isbn = record["issn"]
                self.entry.status.setText(tr('已读取期刊条码；请核对刊名、年度与期数。可直接拍摄封面。'))

    def capture_cover(self):
        self.cover_mode()
        if not self.camera:
            self.toggle_camera()
        self.show_cover_dialog()

    def import_cover(self):
        path, _ = QFileDialog.getOpenFileName(self, tr('选择封面照片'), "", tr('图片 (*.jpg *.jpeg *.png *.bmp *.webp)'))
        if path:
            try:
                self.show_cover_dialog(decode_image(Path(path).read_bytes()))
            except Exception as exc:
                self.error(exc)

    def show_cover_dialog(self, frame=None):
        self.cover_open = True
        dialog = None
        if self.camera:
            self.camera.scanning = False
        try:
            dialog = CoverDialog(self, frame)
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.blob:
                self.entry.set_cover(dialog.blob, "camera" if frame is None else "file")
        finally:
            if dialog is not None:
                dialog.deleteLater()
            self.cover_open = False
            self.update_scanning()

    def cancel_lookup(self, checked=False, *, silent=False):
        self.request_token += 1
        if self.network:
            self.network.cancel()
            self.network = None
        if hasattr(self, "entry"):
            self.entry.cancel_button.setEnabled(False)
            if not silent:
                self.entry.status.setText(tr('查询已取消，可继续手工填写；再次点击查询可重试'))

    def lookup_isbn(self, checked=False, *, detail_url=""):
        if self.entry.media_type.currentData() == "magazine":
            try:
                value = self.entry.text("isbn")
                if value.startswith("977"):
                    self.entry.apply_barcode(publication_barcode(value))
                else:
                    self.entry.set_text("isbn", normalize_issn(value))
                self.queried_isbn = self.entry.text("isbn")
                self.cover_mode()
                self.entry.status.setText(tr('期刊条码已读取；请核对年度与期数，未包含的资料可手工填写'))
            except ValueError as exc:
                self.error(exc)
            return
        try:
            isbn = normalize_isbn(self.entry.text("isbn"))
            if not isbn:
                raise ValueError(tr('请先输入 ISBN；无 ISBN 的旧书可直接填写书名与年份保存'))
        except ValueError as exc:
            self.error(exc)
            return
        self.cancel_lookup(silent=True)
        self.entry.set_text("isbn", isbn)
        self.scan_gate.locked = True
        self.cover_mode()
        self.queried_isbn = self.pending_isbn = isbn
        if self.db and not detail_url and not self.entry.new_edition.isChecked() and self.entry.copy_id is None:
            candidates = self.db.find_isbn(isbn)
            if candidates:
                selected = self.pick_candidate(candidates, tr('选择本地书目，继续录入副本'))
                if selected:
                    self.entry.book_id = selected["id"]
                    self.entry.fill(self.db.book(selected["id"]))
                    self.entry.status.setText(tr('已复用本地书目；请填写本册年份与位置，保存时新增副本'))
                    self.entry.fields["publication_year"].setFocus()
                return
        source = "nlc" if detail_url else self.settings_page.source.currentData()
        self.lookup_started = time.monotonic()
        self.lookup_stage = source
        job = LookupWorker(self.request_token, isbn, source, self.settings_page.key.text().strip(), detail_url, self, language=self.language)
        self.network = job
        job.progress.connect(self.lookup_progress)
        job.result.connect(self.lookup_result)
        self.keep_job(job)
        self.entry.cancel_button.setEnabled(True)
        job.start()

    def lookup_progress(self, token, message):
        if token == self.request_token:
            self.lookup_stage = message

    def pick_candidate(self, candidates, title=tr('选择匹配书目')):
        if len(candidates) == 1:
            return candidates[0]
        dialog = CandidateDialog(candidates, self, title)
        selected = dialog.selected() if dialog.exec() == QDialog.DialogCode.Accepted else None
        dialog.deleteLater()
        return selected

    def lookup_result(self, token, records, note):
        if token != self.request_token:
            return
        try:
            if normalize_isbn(self.entry.text("isbn")) != self.pending_isbn:
                return
        except ValueError:
            return
        self.network = None
        self.entry.cancel_button.setEnabled(False)
        if not records:
            self.entry.status.setText((note or tr('未查到书目')) + tr('；可直接手工补充'))
            return
        selected = self.pick_candidate(records)
        if token != self.request_token or not selected:
            return
        if selected.get("detail_url"):
            self.lookup_isbn(detail_url=selected["detail_url"])
            return
        self.entry.fill(selected)
        codes = selected.get("source_info", {}).get("clc_candidates", [])
        if len(codes) > 1 and "clc_code" not in self.entry.dirty:
            code, ok = QInputDialog.getItem(self, tr('选择主分类号'), tr('来源提供多个中图分类号，请选一个生成馆藏编码：'), codes, 0, False)
            if ok and token == self.request_token:
                self.entry.set_text("clc_code", code)
                self.update_classification_hint()
        self.entry.status.setText(tr('资料已就绪，请核对并填写本册年份。') + (tr('可拍摄或导入封面。') if not self.entry.cover_blob else "") + (" " + note if note else ""))
        self.entry.fields["publication_year"].setFocus()

    def reset_entry(self, *_):
        self.cancel_lookup(silent=True)
        self.entry.clear()
        self.queried_isbn = self.pending_isbn = ""
        self.scan_gate.release()
        self.camera_mode = "scan"
        self.entry.camera_view.guide = True
        self.update_scanning()
        if self.camera and not self.camera_stopping:
            self.entry.camera_status.setText(tr('条码识别模式 · 请扫描下一册'))
        self.entry.status.setText(tr('草稿已清空 · 可以录入下一册'))
        self.entry.fields["isbn"].setFocus()

    def discard_draft(self):
        if not self.entry.dirty and not self.entry.text("title"):
            return True
        return QMessageBox.question(self, tr('未保存的草稿'), tr('当前草稿尚未保存，放弃并继续？'), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def load_book(self, book_id, *, copy_id=None):
        if not self.discard_draft():
            return
        self.reset_entry()
        data = self.db.book(book_id)
        self.entry.fill(data, protect=False)
        self.entry.book_id = book_id
        self.entry.media_type.setEnabled(False)
        self.entry.copy_id = copy_id
        self.scan_gate.locked = True
        self.cover_mode()
        self.queried_isbn = data["isbn"]
        self.entry.new_edition.setEnabled(copy_id is None)
        if copy_id is not None:
            copy = self.db.copy(copy_id)
            for key in ("publication_year", "location", "notes"):
                self.entry.set_text(key, copy[key])
            self.entry.reading_status.setCurrentIndex(self.entry.reading_status.findData(copy["reading_status"]))
            code = self.db.code_for(book_id, copy["copy_no"])
            self.entry.edit_hint.setText(tr('编辑 {v0} · 书目资料与封面由该书全部副本共享；年份、位置和备注仅修改本册', v0=(code)))
            self.entry.save_button.setText(tr('保存修改（Ctrl+Enter）'))
        else:
            self.entry.edit_hint.setText(tr('正在新增同书副本 · 请填写本册年份与位置'))
        self.tabs.setCurrentIndex(0)
        self.entry.fields["publication_year"].setFocus()

    def edit_copy(self, copy_id):
        self.load_book(self.db.copy(copy_id)["book_id"], copy_id=copy_id)

    def repeat_copy(self):
        if self.entry.last_saved_book and self.db:
            self.load_book(self.entry.last_saved_book)

    def save_entry(self, *_):
        if self.saving:
            return
        if not self.db:
            self.error(tr('请先在设置页新建或打开数据库'))
            return
        self.saving = True
        self.entry.save_button.setEnabled(False)
        try:
            data = self.entry.data()
            LibraryDB._book_values(data)
            year = validate_year(self.entry.text("publication_year"))
            book_id, copy_id = self.entry.book_id, self.entry.copy_id
            if self.entry.new_edition.isChecked() and copy_id is None:
                book_id = None
            elif book_id is None:
                candidates = self.db.find_isbn(data["isbn"]) if data["isbn"] else self.db.possible_duplicates(data)
                if candidates:
                    answer = QMessageBox.question(self, tr('可能已存在该书目'), tr('找到相同 ISBN 或相同书名、作者、出版社的书目。\n是否复用现有书目并新增副本？\n选择“否”将登记独立的新版本。'), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Yes)
                    if answer == QMessageBox.StandardButton.Cancel:
                        return
                    if answer == QMessageBox.StandardButton.Yes:
                        selected = self.pick_candidate(candidates)
                        if not selected:
                            return
                        book_id = selected["id"]
                        existing = self.db.book(book_id)
                        # Reuse unedited shared fields instead of blanking metadata on a duplicate.
                        for key in ("isbn", "title", "authors", "translators", "publisher", "clc_code", "summary", "cover_blob", "cover_mime", "reference_pubdate"):
                            if key not in self.entry.dirty:
                                data[key] = existing[key]
                        merged_info = dict(existing["source_info"])
                        merged_info.update(data["source_info"])
                        data["source_info"] = merged_info
            if book_id is not None:
                existing = self.db.book(book_id)
                shared = ("isbn", "title", "authors", "translators", "publisher", "clc_code", "summary", "cover_blob")
                if data["media_type"] == "magazine":
                    shared += ("issn", "issue_year", "issue_number")
                changed = [k for k in shared if data.get(k) != existing.get(k)]
                if changed:
                    before = self.db.code_for(book_id)
                    after = self.db.preview_code(data, book_id)
                    message = tr('将更新此书目所有副本共享的资料与封面。')
                    if "clc_code" in changed:
                        message += tr('\n基本编码：{v0} → {v1}\n所有副本的后缀不变。', v0=(before), v1=(after))
                    if QMessageBox.question(self, tr('确认书目变更'), message + tr('\n继续保存？'), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes:
                        return
            saved_copy, code = self.db.save(data, year, self.entry.text("location"), self.entry.text("notes"), book_id=book_id, copy_id=copy_id,
                                          reading_status=self.entry.reading_status.currentData())
            last_book = self.db.copy(saved_copy)["book_id"]
            self.reset_entry()
            self.entry.last_saved_book = last_book
            self.entry.repeat_button.setEnabled(True)
            self.entry.status.setText(tr('已保存：{v0} · 可录入下一册，或继续登记同书副本', v0=(code)))
            self.refresh_library()
        except Exception as exc:
            self.error(exc)
        finally:
            QTimer.singleShot(400, self.release_save)

    def release_save(self):
        self.saving = False
        self.entry.save_button.setEnabled(True)

    def delete_copy(self, item):
        if QMessageBox.question(self, tr('删除单册'), tr('删除 {v0}《{v1}》？\n其他副本保留，已分配的编号不再复用。', v0=(item['code']), v1=(item['title'])), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            try:
                self.db.delete_copy(item["id"])
                if self.entry.copy_id == item["id"]:
                    self.reset_entry()
                self.refresh_library()
            except Exception as exc:
                self.error(exc)

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.KeyPress or self.tabs.currentIndex() != 0 or QApplication.activeModalWidget() or QApplication.activePopupWidget() or not self.isActiveWindow():
            return super().eventFilter(watched, event)
        key = event.key()
        if key not in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Backspace, Qt.Key.Key_Escape):
            return super().eventFilter(watched, event)
        if event.isAutoRepeat():
            return key != Qt.Key.Key_Backspace
        focus = QApplication.focusWidget()
        if key == Qt.Key.Key_Escape:
            self.reset_entry()
            return True
        if key == Qt.Key.Key_Backspace:
            if not isinstance(focus, (QLineEdit, QTextEdit, QAbstractSpinBox, QComboBox)):
                self.reset_entry()
                return True
            return False
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.save_entry()
            return True
        if isinstance(focus, QTextEdit):
            return False
        if self.entry.text("isbn") and not self.queried_isbn and self.entry.copy_id is None:
            self.lookup_isbn()
        else:
            self.save_entry()
        return True

    def closeEvent(self, event):
        if not self.closing:
            if not self.smoke and not self.discard_draft():
                event.ignore()
                return
            self.closing = True
            self.cancel_lookup(silent=True)
            if self.camera:
                self.camera.requestInterruption()
            for job in self.jobs:
                if isinstance(job, LookupWorker):
                    job.cancel()
        if (self.camera and self.camera.isRunning()) or any(job.isRunning() for job in self.jobs):
            self.statusBar().showMessage(tr('正在关闭后台任务与摄像头…'))
            event.ignore()
            QTimer.singleShot(100, self.close)
            return
        self.timer.stop()
        QApplication.instance().removeEventFilter(self)
        if not self.smoke:
            self.settings["window_size"] = [self.width(), self.height()]
            self.persist_settings()
        if self.db:
            self.db.close()
            self.db = None
        if self.db_lock:
            self.db_lock.unlock()
        event.accept()
