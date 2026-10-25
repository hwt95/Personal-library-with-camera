from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QDialog

from app.database import LibraryDB
from app.domain import accession
from app.window import MainWindow
from app.dialogs import CoverDialog
from app.widgets import STYLE
from app.typography import install_fonts


@pytest.fixture(scope="module")
def application():
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)
    instance = QApplication.instance() or QApplication([])
    install_fonts(instance)
    instance.setStyleSheet(STYLE)
    return instance


@pytest.fixture
def window(tmp_path, application, monkeypatch):
    path = tmp_path / "library.sqlite3"
    db = LibraryDB(path, create=True)
    db.close()
    result = MainWindow(tmp_path / "settings.json", database=path, smoke=True)
    def fail(message):
        raise AssertionError(str(message))
    monkeypatch.setattr(result, "error", fail)
    result.show()
    result.activateWindow()
    application.processEvents()
    yield result
    result.entry.dirty.clear()
    result.close()
    for _ in range(40):
        application.processEvents()
        if not result.jobs:
            break
        QTest.qWait(50)


def test_save_double_press_reset_and_reopen(window, application):
    window.entry.set_text("title", "无 ISBN 的旧书")
    window.entry.set_text("publication_year", "1980")
    window.save_entry()
    window.save_entry()
    assert window.db.stats() == (1, 1)
    assert window.entry.text("title") == ""
    assert window.entry.last_saved_book == 1
    window.repeat_copy()
    assert window.entry.book_id == 1
    assert window.entry.text("publication_year") == ""
    assert window.entry.text("title") == "无 ISBN 的旧书"


def test_stale_result_and_manual_field_protection(window):
    window.entry.set_text("isbn", "9787111544937")
    window.pending_isbn = "9787111544937"
    window.entry.set_text("title", "手工书名")
    window.entry.dirty.add("title")
    window.entry.set_cover(b"manual-cover", "camera")
    token = window.request_token
    record = {"isbn": "9787111544937", "title": "远端书名", "authors": "远端作者", "reference_pubdate": "2016", "cover_blob": b"remote-cover"}
    window.lookup_result(token, [record], "")
    assert window.entry.text("title") == "手工书名"
    assert window.entry.text("authors") == "远端作者"
    assert window.entry.text("publication_year") == ""
    assert window.entry.cover_blob == b"manual-cover"
    window.reset_entry()
    window.lookup_result(token, [record], "")
    assert window.entry.text("title") == ""
    assert window.entry.cover_blob is None


def test_failed_switch_preserves_original_database_and_draft(window, tmp_path):
    window.entry.set_text("title", "未保存草稿")
    old = window.db
    bad = tmp_path / "bad.sqlite3"
    bad.write_bytes(b"bad database")
    with pytest.raises(Exception):
        window.switch_database(bad)
    assert window.db is old
    assert old.stats() == (0, 0)
    assert window.entry.text("title") == "未保存草稿"


def test_one_database_cannot_be_opened_twice(window, tmp_path):
    other = MainWindow(tmp_path / "other-settings.json", smoke=True)
    try:
        with pytest.raises(ValueError, match="另一个窗口"):
            other.switch_database(window.db.path)
    finally:
        other.close()


def test_edit_single_copy_preserves_other_copy_year_and_updates_clc(window, monkeypatch):
    data = {"title": "测试", "clc_code": "I"}
    first, _ = window.db.save(data, 2000)
    second, _ = window.db.save(data, 2020, book_id=1)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    window.edit_copy(first)
    window.entry.set_text("clc_code", "TP303")
    window.entry.set_text("publication_year", "2001")
    window.save_entry()
    assert window.db.copy(first)["publication_year"] == 2001
    assert window.db.copy(second)["publication_year"] == 2020
    assert [r["code"] for r in window.db.search()] == ["TP303-000001", "TP303-000001(1)"]


def test_text_edit_shortcuts_are_not_global(window, application):
    window.entry.set_text("title", "test")
    year = window.entry.fields["publication_year"]
    year.setFocus()
    year.setText("2020")
    QTest.keyClick(year, Qt.Key.Key_Backspace)
    assert year.text() == "202"
    assert window.entry.text("title") == "test"
    summary = window.entry.fields["summary"]
    summary.setFocus()
    QTest.keyClicks(summary, "first")
    QTest.keyClick(summary, Qt.Key.Key_Return)
    QTest.keyClicks(summary, "second")
    assert summary.toPlainText() == "first\nsecond"
    assert window.db.stats() == (0, 0)
    modal = QDialog(window)
    modal.setModal(True)
    modal.show()
    application.processEvents()
    QTest.keyClick(modal, Qt.Key.Key_Escape)
    assert window.entry.text("title") == "test"
    modal.close()


def test_cover_cancel_preserves_draft(window, application):
    import numpy as np
    window.entry.set_text("title", "封面取消测试")
    window.entry.set_cover(b"existing", "manual")
    dialog = CoverDialog(window)
    dialog.original = np.zeros((600, 400, 3), np.uint8)
    dialog.result_image = dialog.original.copy()
    dialog.original_view.set_frame(dialog.original)
    dialog.show()
    application.processEvents()
    dialog.reject()
    assert window.entry.text("title") == "封面取消测试"
    assert window.entry.cover_blob == b"existing"
    assert dialog.original is None
    assert dialog.result_image is None
    assert dialog.original_view.image.isNull()


def test_summary_search_result_preview_and_full_details(window):
    summary = "前言内容。" * 40 + "特别命中词：南极生态学" + "后续内容。" * 40
    window.db.save({"title": "新书", "summary": summary}, 2020)
    page = window.search_page
    page.quick.setText("南极生态学")
    page.refresh()
    assert len(page.rows) == 1
    column = next(i for i, (key, _) in enumerate(page.COLUMNS) if key == "summary")
    assert "南极生态学" in page.table.item(0, column).text()
    assert page.table.item(0, column).toolTip() == summary
    page.table.selectRow(0)
    assert summary in page.details.toPlainText()
    page.quick.clear()
    page.filters["summary"].setText("不存在的词")
    page.refresh()
    assert not page.rows


def test_camera_controls_stay_in_sync_and_disable_unsupported_focus(window):
    dialog = CoverDialog(window)
    try:
        dialog.camera_controls.zoom.setValue(250)
        assert window.camera_zoom == 2.5
        assert window.entry.camera_controls.zoom.value() == 250
        window.entry.camera_controls.zoom.setValue(100)
        assert dialog.camera_controls.zoom.value() == 100
        window.camera_focus_changed({"supported": False, "enabled": False, "note": "不支持自动对焦"})
        assert not dialog.camera_controls.refocus.isEnabled()
        assert not window.entry.camera_controls.autofocus.isEnabled()
    finally:
        dialog.reject()


def test_live_border_overlay_and_redetect_preserve_rotation(window, application):
    import cv2
    import numpy as np
    frame = np.full((600, 800, 3), 30, np.uint8)
    points = np.array([[250, 60], [570, 90], [610, 520], [210, 500]], np.int32)
    cv2.fillConvexPoly(frame, points, (235, 235, 235))
    class PreviewCamera:
        def latest(self):
            return frame.copy()
    window.camera = PreviewCamera()
    dialog = CoverDialog(window)
    try:
        dialog.preview()
        for _ in range(100):
            application.processEvents()
            if dialog.original_view.points is not None:
                break
            QTest.qWait(20)
        assert dialog.original_view.points is not None
        assert "已自动识别" in dialog.border_status.text()
        old_token = dialog.border_token
        dialog.original = frame.copy()
        dialog.rotation = 1
        dialog.set_busy(False)
        dialog.redetect()
        for _ in range(100):
            application.processEvents()
            if dialog.result_image is not None:
                break
            QTest.qWait(20)
        assert dialog.result_image is not None
        assert dialog.rotation == 1
        assert dialog.result_image.shape[1] > dialog.result_image.shape[0]
        actual = dialog.original_view.points.copy()
        dialog.border_result(old_token, {"points": None, "found": False, "shape": frame.shape[:2]})
        assert np.array_equal(dialog.original_view.points, actual)
    finally:
        dialog.reject()
        window.camera = None


def test_preset_location_selection_saves_and_survives_restart(window, application):
    import json
    page = window.settings_page
    for name in (" 书房 A 架 ", "客厅书柜上层"):
        page.location_input.setText(name)
        page.add_location()
    assert window.location_presets() == ["书房 A 架", "客厅书柜上层"]
    window.entry.refresh_location_menu()
    action = next(a for a in window.entry.location_menu.actions() if a.data() == "客厅书柜上层")
    action.trigger()
    window.entry.set_text("title", "地点测试图书")
    window.entry.set_text("publication_year", "2020")
    window.save_entry()
    assert window.db.search()[0]["location"] == "客厅书柜上层"
    assert window.entry.text("location") == ""
    settings_path = window.settings_path
    assert json.loads(settings_path.read_text(encoding="utf-8"))["location_presets"] == window.location_presets()
    window.close()
    application.processEvents()
    reopened = MainWindow(settings_path, smoke=True)
    try:
        assert reopened.location_presets() == ["书房 A 架", "客厅书柜上层"]
        assert reopened.db.search()[0]["location"] == "客厅书柜上层"
    finally:
        reopened.close()


def test_rename_delete_presets_do_not_change_copy_or_draft(window):
    window.set_location_presets(["书房 A", "阁楼"])
    copy_id, _ = window.db.save({"title": "旧书"}, 2000, "书房 A")
    window.entry.set_text("location", "书房 A")
    page = window.settings_page
    page.location_list.setCurrentRow(0)
    page.location_input.setText("书房 B")
    page.rename_location()
    assert window.location_presets() == ["书房 B", "阁楼"]
    page.location_list.setCurrentRow(0)
    page.delete_location()
    assert window.location_presets() == ["阁楼"]
    assert window.db.copy(copy_id)["location"] == "书房 A"
    assert window.entry.text("location") == "书房 A"
    window.entry.refresh_location_menu()
    assert not any(a.isChecked() for a in window.entry.location_menu.actions())


def test_blank_duplicate_and_failed_settings_write_keep_presets(window, monkeypatch):
    window.set_location_presets(["Shelf A"])
    original = window.settings_path.read_bytes()
    errors = []
    monkeypatch.setattr(window, "error", lambda message: errors.append(str(message)))
    for invalid in ("  ", " shelf a "):
        window.settings_page.location_input.setText(invalid)
        window.settings_page.add_location()
    assert len(errors) == 2
    assert window.location_presets() == ["Shelf A"]
    assert window.settings_path.read_bytes() == original
    monkeypatch.setattr(window, "persist_settings", lambda: False)
    assert not window.set_location_presets(["书房 B"])
    assert window.location_presets() == ["Shelf A"]
    assert window.settings_page.location_list.item(0).text() == "Shelf A"


def test_location_menu_keyboard_does_not_save_or_reset_draft(window, application):
    window.set_location_presets(["客厅 A&B", "书房"])
    window.entry.set_text("title", "保留草稿")
    menu = window.entry.location_menu
    position = window.entry.location_button.mapToGlobal(window.entry.location_button.rect().bottomLeft())
    menu.popup(position)
    application.processEvents()
    menu.setActiveAction(next(a for a in menu.actions() if a.data() == "客厅 A&B"))
    QTest.keyClick(menu, Qt.Key.Key_Return)
    application.processEvents()
    assert window.entry.text("location") == "客厅 A&B"
    assert window.entry.text("title") == "保留草稿"
    assert window.db.stats() == (0, 0)
    menu.popup(position)
    application.processEvents()
    assert len([a for a in menu.actions() if a.isChecked()]) == 1
    QTest.keyClick(menu, Qt.Key.Key_Escape)
    assert window.entry.text("title") == "保留草稿"
    window.entry.refresh_location_menu()
    next(a for a in menu.actions() if a.isCheckable() and a.data() == "").trigger()
    assert window.entry.text("location") == ""


def test_version_and_author_are_explicitly_user_requested(window):
    from app import __version__, __author__
    assert __version__ == "1.0"
    assert __author__ == "Gimi.H with Chatgpt model"
    assert window.windowTitle().endswith("1.0")
    assert "Gimi.H with Chatgpt model" in window.settings_page.about.text()
