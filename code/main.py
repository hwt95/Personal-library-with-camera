"""Source entry point; also the only entry point of the frozen Windows executable."""
import argparse
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description="个人藏书管理")
    parser.add_argument("--db", help="打开指定数据库")
    parser.add_argument("--self-test", metavar="REPORT_JSON", help="离线自检并输出报告，不访问摄像头或真实藏书")
    parser.add_argument("--self-test-platform", choices=("offscreen", "windows"), default="offscreen", help="自检使用的 Qt 平台")
    args = parser.parse_args()
    if getattr(sys, "frozen", False):
        # Use the bundled Qt plugins, independent of another installed Qt/Python.
        plugins = Path(sys._MEIPASS) / "PySide6" / "plugins"
        os.environ["QT_PLUGIN_PATH"] = str(plugins)
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(plugins / "platforms")
        os.environ.pop("QML2_IMPORT_PATH", None)
    if args.self_test:
        os.environ["QT_QPA_PLATFORM"] = args.self_test_platform
    from PySide6.QtCore import QStandardPaths, Qt
    from PySide6.QtWidgets import QApplication, QMessageBox
    from app.widgets import STYLE
    from app import __version__
    from app.typography import install_fonts
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)
    application = QApplication(sys.argv[:1])
    install_fonts(application)
    application.setApplicationName("PersonalLibrary")
    application.setApplicationVersion(__version__)
    application.setOrganizationName("PersonalLibrary")
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE)
    if args.self_test:
        from app.selftest import run
        return run(application, Path(args.self_test))
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent / "deps"
    # Prefer portable settings. A read-only deployment falls back to an OS user directory.
    settings = base / "settings.json"
    if not os.access(base, os.W_OK):
        settings = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)) / "settings.json"
    from app.window import MainWindow
    try:
        window = MainWindow(settings, database=args.db)
    except Exception as exc:
        QMessageBox.critical(None, "无法打开数据库", str(exc))
        return 1
    def exception_hook(kind, value, traceback):
        QMessageBox.critical(window, "操作未完成", f"{kind.__name__}: {value}\n当前资料尚未提交的部分可修改后重试。")
    sys.excepthook = exception_hook
    window.show()
    return application.exec()


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception:
        if "--self-test" not in sys.argv:
            raise
        import json
        import traceback
        report_path = Path(sys.argv[sys.argv.index("--self-test") + 1]).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps({"passed": False, "checks": {}, "sys_path": sys.path,
            "error": traceback.format_exc()}, ensure_ascii=False, indent=2), encoding="utf-8")
        exit_code = 1
    raise SystemExit(exit_code)
