import importlib.metadata
import json
from pathlib import Path
import sys
import tempfile
import traceback

import cv2
import numpy as np
from PySide6.QtCore import qVersion, Qt
from PySide6.QtGui import QRawFont, QTextLayout

from .database import LibraryDB
from .imaging import Orientation, encode_cover, model_path, process_cover, detect_corners, zoom_frame
from . import __version__, __author__
from .window import MainWindow
from .typography import FONT_FAMILIES, FONT_FILES, font_directory


def run(application, report_path):
    report_path = report_path.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"frozen": bool(getattr(sys, "frozen", False)), "executable": sys.executable,
              "app_version": __version__,
              "author": __author__,
              "python": sys.version, "qt": qVersion(), "opencv": cv2.__version__,
              "qt_platform": application.platformName(), "sys_path": sys.path,
              "checks": {}, "camera_tested": False, "live_network_tested": False}
    window = None
    try:
        import httpx
        import bs4
        import zxingcpp
        import onnxruntime
        report["checks"]["bundled_imports"] = True
        report["checks"]["bundled_chinese_font"] = QRawFont.fromFont(application.font()).supportsCharacter(ord("藏"))
        sample = "个人藏书管理 预设存放地点 图书简介 攝像頭 Gimi.H with Chatgpt model ISBN 9787111544937"
        shaped = QTextLayout(sample, application.font())
        shaped.beginLayout()
        shaped.createLine()
        shaped.endLayout()
        runs = shaped.glyphRuns()
        report["font_families"] = sorted({run.rawFont().familyName() for run in runs})
        report["checks"]["bundled_google_fonts"] = bool(runs) and all(
            run.rawFont().familyName() in FONT_FAMILIES and all(run.glyphIndexes()) for run in runs)
        report["checks"]["local_font_files_and_licenses"] = all((font_directory() / name).is_file()
            for name in (*FONT_FILES, "WenKai-OFL.txt", "NotoSansSC-OFL.txt", "SOURCE.json"))
        report["checks"]["styled_file_dialogs"] = application.testAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
        report["onnxruntime"] = onnxruntime.__version__
        report["model_exists"] = model_path().is_file()
        frame = np.full((640, 420, 3), 245, np.uint8)
        for i, line in enumerate(("PERSONAL LIBRARY", "A book for your shelf", "Computer Systems", "Reading and learning", "A portable collection", "2026 EDITION")):
            cv2.putText(frame, line, (20, 80+i*75), cv2.FONT_HERSHEY_SIMPLEX, .65, (20, 30, 50), 2)
        angle, confidence = Orientation.predict(frame)
        report["orientation"] = {"angle": angle, "confidence": confidence}
        report["checks"]["local_model_inference"] = True
        result = process_cover(frame, auto_orientation=False)
        cover = encode_cover(result["image"])
        report["checks"]["cover_processing"] = len(cover) > 1000
        border_frame = np.full((600, 800, 3), 110, np.uint8)
        expected = np.array([[250, 60], [570, 90], [610, 520], [210, 500]], np.int32)
        cv2.fillConvexPoly(border_frame, expected, (120, 120, 120))
        points, found = detect_corners(border_frame)
        report["checks"]["opencv_auto_border"] = bool(found and np.linalg.norm(points-expected, axis=1).max() < 15)
        report["checks"]["camera_digital_zoom"] = bool(np.array_equal(zoom_frame(border_frame, 2), border_frame[150:450, 200:600]))
        barcode = zxingcpp.create_barcode("9787111544937", zxingcpp.BarcodeFormat.EAN13)
        pixels = np.asarray(zxingcpp.write_barcode_to_image(barcode, scale=4, add_hrt=False))
        decoded = zxingcpp.read_barcodes(pixels)
        assert any(r.text == "9787111544937" for r in decoded)
        report["checks"]["barcode_decode"] = True
        with tempfile.TemporaryDirectory(prefix="library-smoke-", dir=report_path.parent) as temp:
            root = Path(temp)
            path = root / "library.sqlite3"
            db = LibraryDB(path, create=True)
            copy_id, code = db.save({"title": "深入理解计算机系统", "authors": "Randal E. Bryant; David R. O’Hallaron",
                                    "translators": "龚奕利; 贺莲", "publisher": "机械工业出版社", "isbn": "9787111544937",
                                    "clc_code": "TP303", "cover_blob": cover, "cover_mime": "image/jpeg",
                                    "summary": "自检示例：书目与封面保存在同一数据库；此测试库退出后自动移除。"}, 2016, "书房 A1")
            db.save(db.book(1), 2023, "书房 A2", book_id=1)
            backup = db.backup(root / "backup.sqlite3")
            db.close()
            restored = LibraryDB(backup)
            assert restored.book(1)["cover_blob"] == cover
            assert restored.stats() == (1, 2)
            restored.close()
            report["checks"]["database_cover_backup"] = True
            window = MainWindow(root / "settings.json", database=path, smoke=True)
            assert window.set_location_presets(["书房 A 架", "客厅书柜上层"])
            assert json.loads((root / "settings.json").read_text(encoding="utf-8"))["location_presets"] == ["书房 A 架", "客厅书柜上层"]
            assert __version__ in window.windowTitle() and __author__ in window.settings_page.about.text()
            report["checks"]["version_and_author"] = True
            window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            window.show()
            application.processEvents()
            assert window.tabs.count() == 4
            for index in range(4):
                window.tabs.setCurrentIndex(index)
                application.processEvents()
            window.tabs.setCurrentIndex(2)
            window.search_page.filters["summary"].setText("自检示例")
            window.search_page.refresh()
            assert len(window.search_page.rows) == 2
            assert "summary" in dict(window.search_page.COLUMNS)
            report["checks"]["summary_search_and_results"] = True
            window.search_page.table.selectRow(0)
            application.processEvents()
            screenshot = report_path.with_suffix(".png")
            assert window.grab().save(str(screenshot))
            report["screenshot"] = str(screenshot)
            report["checks"]["four_pages_render"] = True
            window.tabs.setCurrentIndex(3)
            application.processEvents()
            settings_screenshot = report_path.with_name(report_path.stem + "-settings.png")
            assert window.grab().save(str(settings_screenshot))
            report["settings_screenshot"] = str(settings_screenshot)
            window.tabs.setCurrentIndex(0)
            window.entry.set_text("title", "离线手工旧书")
            window.entry.set_text("publication_year", "1984")
            window.entry.refresh_location_menu()
            action = next(a for a in window.entry.location_menu.actions() if a.data() == "书房 A 架")
            action.trigger()
            assert window.entry.text("location") == "书房 A 架"
            entry_screenshot = report_path.with_name(report_path.stem + "-entry.png")
            assert window.grab().save(str(entry_screenshot))
            report["entry_screenshot"] = str(entry_screenshot)
            window.save_entry()
            assert window.db.stats() == (2, 3)
            assert window.db.search(fields={"title": "离线手工旧书"})[0]["code"].startswith("PENDING-")
            assert window.db.search(fields={"title": "离线手工旧书"})[0]["location"] == "书房 A 架"
            report["checks"]["preset_location_selection_and_save"] = True
            report["checks"]["ui_manual_save"] = True
            window.close()
            application.processEvents()
            window = None
        report["passed"] = all(report["checks"].values())
    except Exception:
        report["passed"] = False
        report["error"] = traceback.format_exc()
        if window:
            window.close()
            application.processEvents()
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1
