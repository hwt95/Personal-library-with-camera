"""Local UI catalogs for the six UN languages; catalog content is never translated."""
from pathlib import Path
import json
import sys

LANGUAGES = {"zh_CN": "简体中文", "en": "English", "fr": "Français", "es": "Español",
             "ru": "Русский", "ar": "العربية"}
_language = "zh_CN"
_catalogs = None
_rendered = {}
_qt_translator = None


def install_standard_translator(application):
    """Translate common Qt controls without depending on system language packs."""
    global _qt_translator
    from PySide6.QtCore import QTranslator
    if _qt_translator is None:
        class StandardTranslator(QTranslator):
            def isEmpty(self):
                return False

            def translate(self, context, sourceText, disambiguation=None, n=-1):
                labels = {"Yes": "是", "No": "否", "OK": "确定", "Cancel": "取消",
                          "Open": "打开", "Save": "保存", "Apply": "应用", "Discard": "放弃",
                          "Retry": "重试", "Ignore": "忽略", "Close": "关闭", "Help": "帮助",
                          "Yes to All": "全部是", "No to All": "全部否",
                          "File name:": "文件名：", "Files of type:": "文件类型：", "Look in:": "查找范围："}
                key = labels.get(sourceText.replace("&", ""))
                return tr(key) if key else ""
        _qt_translator = StandardTranslator(application)
    else:
        application.removeTranslator(_qt_translator)
    application.installTranslator(_qt_translator)


def default_language(system_languages=None):
    if system_languages is None:
        from PySide6.QtCore import QLocale
        locale = QLocale.system()
        system_languages = locale.uiLanguages() or [locale.name()]
    primary = (system_languages[0] if system_languages else "en").replace("_", "-").lower()
    simplified = primary in ("zh", "zh-cn", "zh-sg") or primary.startswith("zh-hans")
    return "zh_CN" if simplified else "en"


def catalogs():
    global _catalogs
    if _catalogs is None:
        root = Path(sys._MEIPASS) if getattr(sys,"frozen",False) else Path(__file__).resolve().parents[1]
        folder = root / "resources" / "translations"
        _catalogs = {code: json.loads((folder/(code+".json")).read_text("utf-8"))
                     for code in LANGUAGES if code != "zh_CN" and (folder/(code+".json")).is_file()}
    return _catalogs


def tr(source, **values):
    template = source if _language == "zh_CN" else catalogs().get(_language, {}).get(source, catalogs().get("en", {}).get(source, source))
    try:
        result = template.format(**values) if values else template
    except (KeyError, ValueError):
        result = source.format(**values) if values else source
    if len(_rendered) > 4096:
        _rendered.clear()
    _rendered[result] = (source, values)
    return result


def translated(text):
    if text in _rendered:
        source, values = _rendered[text]
        return tr(source, **values)
    if text in catalogs().get("en", {}):
        return tr(text)
    for catalog in catalogs().values():
        source = next((source for source,target in catalog.items() if target == text), None)
        if source is not None:
            return tr(source)
    return text


def set_language(language):
    global _language
    _language = language if language in LANGUAGES else "zh_CN"
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    application = QApplication.instance()
    if application:
        install_standard_translator(application)
        application.setLayoutDirection(Qt.LayoutDirection.RightToLeft if _language == "ar" else Qt.LayoutDirection.LeftToRight)
        from .typography import FONT_FAMILIES
        from PySide6.QtGui import QFont
        families = list(FONT_FAMILIES)
        font = application.font()
        font.setFamilies(families)
        font.setWeight(QFont.Weight.Normal)
        font.setItalic(False)
        application.setFont(font)
        base = application.property("base_style")
        if base is None:
            base = application.styleSheet()
            application.setProperty("base_style", base)
        application.setStyleSheet(base + "\nQWidget { font-family: " + ", ".join("'"+name+"'" for name in families) + "; font-weight: 400; font-style: normal; }")


def translate_tree(root):
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import (QWidget, QLabel, QAbstractButton, QLineEdit, QTextEdit,
        QGroupBox, QTabWidget, QComboBox, QTableWidget, QSpinBox)
    objects = [root, *root.findChildren(QWidget)]
    for widget in objects:
        widget.setWindowTitle(translated(widget.windowTitle()))
        widget.setToolTip(translated(widget.toolTip()))
        if isinstance(widget, (QLabel, QAbstractButton)):
            widget.setText(translated(widget.text()))
        if isinstance(widget, QGroupBox):
            widget.setTitle(translated(widget.title()))
        if isinstance(widget, (QLineEdit, QTextEdit)):
            widget.setPlaceholderText(translated(widget.placeholderText()))
        if isinstance(widget, QSpinBox):
            widget.setSpecialValueText(translated(widget.specialValueText()))
        if isinstance(widget, QTabWidget):
            for index in range(widget.count()):
                widget.setTabText(index, translated(widget.tabText(index)))
        if isinstance(widget, QComboBox) and widget is not getattr(getattr(root,"settings_page",None),"language",None):
            widget.blockSignals(True)
            for index in range(widget.count()):
                widget.setItemText(index, translated(widget.itemText(index)))
            widget.blockSignals(False)
        if isinstance(widget, QTableWidget):
            for index in range(widget.columnCount()):
                header = widget.horizontalHeaderItem(index)
                if header:
                    header.setText(translated(header.text()))
    for action in root.findChildren(QAction):
        # Preset locations are user data and must keep their original text.
        if action.data() is None:
            action.setText(translated(action.text()))
