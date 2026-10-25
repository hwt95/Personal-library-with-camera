import json
from pathlib import Path
import time

from PySide6.QtCore import Qt, QEvent, QTimer, QLockFile, QStandardPaths, Signal
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QLabel, QTabWidget,
    QMessageBox, QFileDialog, QInputDialog, QDialog, QLineEdit, QTextEdit, QAbstractSpinBox, QComboBox)

from .database import LibraryDB
from .domain import ScanGate, accession, normalize_isbn, validate_year, normalize_location_presets
from . import APP_NAME, __version__
from .imaging import decode_image
from .widgets import row
from .pages import EntryPage, CatalogPage, SettingsPage
from .dialogs import CandidateDialog, ClassificationDialog, CoverDialog
from .workers import CameraWorker, LookupWorker


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
        self.db = self.db_lock = None
        self.camera = self.network = None
        self.camera_zoom = 1.0
        self.camera_autofocus = True
        self.camera_focus_state = {}
        self.jobs = set()
        self.scan_gate = ScanGate()
        self.request_token = 0
        self.queried_isbn = self.pending_isbn = ""
        self.lookup_started = 0.0
        self.lookup_stage = ""
        self.saving, self.cover_open, self.closing, self.smoke = False, False, False, smoke
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1260, 880)
        self.setMinimumSize(980, 720)
        size = self.settings.get("window_size", [])
        if isinstance(size, list) and len(size) == 2 and all(isinstance(v, int) for v in size):
            self.resize(max(980, min(size[0], 2400)), max(720, min(size[1], 1600)))
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 18, 24, 18)
        title = QLabel("我的藏书")
        title.setObjectName("heading")
        self.counter = QLabel("本地保存 · 随时查找")
        self.counter.setObjectName("subtle")
        layout.addWidget(row(title, self.counter))
        self.tabs = QTabWidget()
        self.entry = EntryPage(self)
        self.catalog = CatalogPage(self)
        self.search_page = CatalogPage(self, advanced=True)
        self.settings_page = SettingsPage(self)
        for page, label in ((self.entry, "录入"), (self.catalog, "藏书列表"), (self.search_page, "组合检索"), (self.settings_page, "设置")):
            self.tabs.addTab(page, label)
        layout.addWidget(self.tabs)
        self.setCentralWidget(container)
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
        choice.setWindowTitle("选择藏书数据库")
        choice.setText("请选择书籍与封面的保存位置。已有数据库可直接打开。")
        new = choice.addButton("新建数据库", QMessageBox.ButtonRole.AcceptRole)
        existing = choice.addButton("打开现有库", QMessageBox.ButtonRole.ActionRole)
        choice.addButton("稍后选择", QMessageBox.ButtonRole.RejectRole)
        choice.exec()
        if choice.clickedButton() == new:
            self.new_database()
        elif choice.clickedButton() == existing:
            self.open_database()
        else:
            self.tabs.setCurrentWidget(self.settings_page)

    def error(self, error):
        QMessageBox.warning(self, "未能完成操作", str(error))

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
            self.error(f"设置未能保存：{exc}\n书目数据不受影响；下次启动可重新选择数据库。")
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
        self.notice("存放地点已保存，录入页可直接勾选")
        return True

    def show_location_settings(self):
        self.tabs.setCurrentWidget(self.settings_page)
        self.settings_page.location_input.setFocus()

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
        if self.camera and self.camera.isRunning() and not self.cover_open:
            frame = self.camera.latest()
            if frame is not None and self.tabs.currentIndex() == 0:
                self.entry.camera_view.set_frame(frame)
        if self.network:
            elapsed = int(time.monotonic() - self.lookup_started)
            self.entry.status.setText(f"正在查询 {self.lookup_stage} · 已等待 {elapsed} 秒 · 可取消并手工补充")

    def tab_changed(self, index):
        if index in (1, 2):
            self.tabs.widget(index).refresh()
        if self.camera:
            self.camera.scanning = index == 0 and not self.cover_open

    def refresh_library(self):
        if self.db:
            books, copies = self.db.stats()
            self.counter.setText(f"{books} 种书目  ·  {copies} 册馆藏  ·  本地保存")
            self.settings_page.path.setText(str(self.db.path))
        self.catalog.refresh()
        self.search_page.refresh()

    def suggested_database(self):
        return str(Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)) / "个人藏书" / "library.sqlite3")

    def switch_database(self, path, *, create=False):
        path = Path(path).resolve()
        if self.db and path == self.db.path:
            if create:
                raise ValueError("不能用新库覆盖当前数据库")
            return
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(path) + ".app-lock")
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            raise ValueError("数据库已由另一个窗口打开，或所在目录不可写")
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
        self.notice(f"当前数据库：{path}")

    def open_database(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开藏书数据库", "", "SQLite (*.sqlite3 *.db *.sqlite);;所有文件 (*)")
        if path and self.discard_draft():
            try:
                self.switch_database(path)
            except Exception as exc:
                self.error(exc)

    def new_database(self):
        path, _ = QFileDialog.getSaveFileName(self, "新建数据库（请选择尚不存在的文件）", self.suggested_database(), "SQLite (*.sqlite3)")
        if path and self.discard_draft():
            try:
                self.switch_database(path, create=True)
            except Exception as exc:
                self.error(exc)

    def backup_database(self, checked=False, *, switch=False):
        if not self.db:
            self.error("请先选择数据库")
            return
        suggested = str(self.db.path.with_name(self.db.path.stem + "-副本.sqlite3"))
        path, _ = QFileDialog.getSaveFileName(self, "保存完整数据库副本（不能覆盖已有文件）", suggested, "SQLite (*.sqlite3)")
        if path:
            try:
                self.db.backup(path)
                if switch and self.discard_draft():
                    self.switch_database(path)
                self.notice(f"完整备份已保存：{path}")
            except Exception as exc:
                self.error(exc)

    def reset_database(self):
        if not self.db:
            return
        count = self.db.stats()[1]
        answer = QMessageBox.question(self, "重置当前库", f"当前数据库：\n{self.db.path}\n\n将清除 {count} 册馆藏、书目和编号计数。分类字典保留。\n程序将先自动创建完整备份，备份失败则取消重置。\n确定继续？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            backup = self.db.reset_with_backup()
            self.reset_entry()
            self.entry.last_saved_book = None
            self.entry.repeat_button.setEnabled(False)
            self.refresh_library()
            QMessageBox.information(self, "重置完成", f"备份已保存：\n{backup}\n\n可用“打开现有库”恢复查看。")
        except Exception as exc:
            self.error(exc)

    def import_classifications(self):
        if not self.db:
            self.error("请先选择数据库")
            return
        path, _ = QFileDialog.getOpenFileName(self, "导入分类字典", "", "CSV (*.csv)")
        if path:
            try:
                count = self.db.import_classifications(path)
                self.update_classification_hint()
                self.notice(f"已导入 {count} 个分类条目")
            except Exception as exc:
                self.error(exc)

    def update_classification_hint(self, *_):
        if not hasattr(self, "entry"):
            return
        code = self.entry.text("clc_code").upper()
        if not code:
            label = "待分类 · 保存后生成临时编号"
        else:
            match = next((r for r in self.db.classifications(code) if r["code"] == code), None) if self.db else None
            label = match["name"] if match else "名称未收录 · 此分类号仍可保存"
        self.entry.clc_hint.setText(label)

    def choose_classification(self):
        if not self.db:
            self.error("请先选择数据库")
            return
        dialog = ClassificationDialog(self.db, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.entry.set_text("clc_code", dialog.selected_code)
            self.entry.dirty.add("clc_code")
            self.update_classification_hint()
        dialog.deleteLater()

    def toggle_camera(self):
        if self.camera:
            self.camera.requestInterruption()
            self.entry.camera_button.setEnabled(False)
            self.entry.camera_status.setText("正在关闭摄像头…")
            return
        camera = CameraWorker(self.settings.get("camera_index", -1), self)
        self.camera = camera
        self.camera_focus_state = {"note": "正在打开摄像头并请求自动对焦…"}
        camera.set_zoom(self.camera_zoom)
        camera.set_autofocus(self.camera_autofocus)
        camera.scanning = self.tabs.currentIndex() == 0 and not self.cover_open
        camera.barcode.connect(self.on_barcode)
        camera.message.connect(self.entry.camera_status.setText)
        camera.focus_changed.connect(self.camera_focus_changed)
        def stopped():
            if self.camera is camera:
                self.camera = None
                self.entry.camera_button.setText("开启摄像头")
                self.entry.camera_button.setEnabled(True)
                self.entry.camera_view.set_frame(None)
                self.camera_focus_state = {}
                self.camera_controls_changed.emit()
            camera.deleteLater()
        camera.finished.connect(stopped)
        self.entry.camera_button.setText("关闭摄像头")
        self.camera_controls_changed.emit()
        camera.start()

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
        if self.cover_open or self.tabs.currentIndex() != 0 or QApplication.activeModalWidget() or QApplication.activePopupWidget():
            return
        hit = self.scan_gate.observe(isbn)
        if hit:
            self.entry.set_text("isbn", hit)
            self.lookup_isbn()

    def capture_cover(self):
        if not self.camera:
            self.toggle_camera()
        self.show_cover_dialog()

    def import_cover(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择封面照片", "", "图片 (*.jpg *.jpeg *.png *.bmp *.webp)")
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
            if self.camera:
                self.camera.scanning = self.tabs.currentIndex() == 0

    def cancel_lookup(self, checked=False, *, silent=False):
        self.request_token += 1
        if self.network:
            self.network.cancel()
            self.network = None
        if hasattr(self, "entry"):
            self.entry.cancel_button.setEnabled(False)
            if not silent:
                self.entry.status.setText("查询已取消，可继续手工填写；再次点击查询可重试")

    def lookup_isbn(self, checked=False, *, detail_url=""):
        try:
            isbn = normalize_isbn(self.entry.text("isbn"))
            if not isbn:
                raise ValueError("请先输入 ISBN；无 ISBN 的旧书可直接填写书名与年份保存")
        except ValueError as exc:
            self.error(exc)
            return
        self.cancel_lookup(silent=True)
        self.entry.set_text("isbn", isbn)
        self.scan_gate.locked = True
        self.queried_isbn = self.pending_isbn = isbn
        if self.db and not detail_url and not self.entry.new_edition.isChecked() and self.entry.copy_id is None:
            candidates = self.db.find_isbn(isbn)
            if candidates:
                selected = self.pick_candidate(candidates, "选择本地书目，继续录入副本")
                if selected:
                    self.entry.book_id = selected["id"]
                    self.entry.fill(self.db.book(selected["id"]))
                    self.entry.status.setText("已复用本地书目；请填写本册年份与位置，保存时新增副本")
                    self.entry.fields["publication_year"].setFocus()
                return
        source = "nlc" if detail_url else self.settings_page.source.currentData()
        self.lookup_started = time.monotonic()
        self.lookup_stage = source
        job = LookupWorker(self.request_token, isbn, source, self.settings_page.key.text().strip(), detail_url, self)
        self.network = job
        job.progress.connect(self.lookup_progress)
        job.result.connect(self.lookup_result)
        self.keep_job(job)
        self.entry.cancel_button.setEnabled(True)
        job.start()

    def lookup_progress(self, token, message):
        if token == self.request_token:
            self.lookup_stage = message

    def pick_candidate(self, candidates, title="选择匹配书目"):
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
            self.entry.status.setText((note or "未查到书目") + "；可直接手工补充")
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
            code, ok = QInputDialog.getItem(self, "选择主分类号", "来源提供多个中图分类号，请选一个生成馆藏编码：", codes, 0, False)
            if ok and token == self.request_token:
                self.entry.set_text("clc_code", code)
                self.update_classification_hint()
        self.entry.status.setText("资料已就绪，请核对并填写本册年份。" + ("可拍摄或导入封面。" if not self.entry.cover_blob else "") + (" " + note if note else ""))
        self.entry.fields["publication_year"].setFocus()

    def reset_entry(self, *_):
        self.cancel_lookup(silent=True)
        self.entry.clear()
        self.queried_isbn = self.pending_isbn = ""
        self.scan_gate.release()
        self.entry.status.setText("草稿已清空 · 可以录入下一册")
        self.entry.fields["isbn"].setFocus()

    def discard_draft(self):
        if not self.entry.dirty and not self.entry.text("title"):
            return True
        return QMessageBox.question(self, "未保存的草稿", "当前草稿尚未保存，放弃并继续？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def load_book(self, book_id, *, copy_id=None):
        if not self.discard_draft():
            return
        self.reset_entry()
        data = self.db.book(book_id)
        self.entry.fill(data, protect=False)
        self.entry.book_id = book_id
        self.entry.copy_id = copy_id
        self.scan_gate.locked = True
        self.queried_isbn = data["isbn"]
        self.entry.new_edition.setEnabled(copy_id is None)
        if copy_id is not None:
            copy = self.db.copy(copy_id)
            for key in ("publication_year", "location", "notes"):
                self.entry.set_text(key, copy[key])
            code = accession(data["clc_code"], book_id, copy["copy_no"])
            self.entry.edit_hint.setText(f"编辑 {code} · 书目资料与封面由该书全部副本共享；年份、位置和备注仅修改本册")
            self.entry.save_button.setText("保存修改（Ctrl+Enter）")
        else:
            self.entry.edit_hint.setText("正在新增同书副本 · 请填写本册年份与位置")
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
            self.error("请先在设置页新建或打开数据库")
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
                    answer = QMessageBox.question(self, "可能已存在该书目", "找到相同 ISBN 或相同书名、作者、出版社的书目。\n是否复用现有书目并新增副本？\n选择“否”将登记独立的新版本。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Yes)
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
                changed = [k for k in ("isbn", "title", "authors", "translators", "publisher", "clc_code", "summary", "cover_blob") if data.get(k) != existing.get(k)]
                if changed:
                    before = accession(existing["clc_code"], book_id)
                    after = accession(data["clc_code"], book_id)
                    message = "将更新此书目所有副本共享的资料与封面。"
                    if "clc_code" in changed:
                        message += f"\n基本编码：{before} → {after}\n所有副本的后缀不变。"
                    if QMessageBox.question(self, "确认书目变更", message + "\n继续保存？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes:
                        return
            saved_copy, code = self.db.save(data, year, self.entry.text("location"), self.entry.text("notes"), book_id=book_id, copy_id=copy_id)
            last_book = self.db.copy(saved_copy)["book_id"]
            self.reset_entry()
            self.entry.last_saved_book = last_book
            self.entry.repeat_button.setEnabled(True)
            self.entry.status.setText(f"已保存：{code} · 可录入下一册，或继续登记同书副本")
            self.refresh_library()
        except Exception as exc:
            self.error(exc)
        finally:
            QTimer.singleShot(400, self.release_save)

    def release_save(self):
        self.saving = False
        self.entry.save_button.setEnabled(True)

    def delete_copy(self, item):
        if QMessageBox.question(self, "删除单册", f"删除 {item['code']}《{item['title']}》？\n其他副本保留，已分配的编号不再复用。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
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
            self.statusBar().showMessage("正在关闭后台任务与摄像头…")
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
