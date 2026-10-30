from .i18n import tr
"""Application-local open fonts; never require installation into Windows."""
from pathlib import Path
import sys

from PySide6.QtGui import QFont, QFontDatabase


FONT_FAMILIES = ("Tinos", "LXGW WenKai", "Noto Sans Arabic")
FONT_FILES = ("Tinos-Regular.ttf", "LXGWWenKai-Regular.ttf", "NotoSansArabic.ttf")
FONT_SIZE = 11


def font_directory():
    assets = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    return assets / "resources" / "fonts"


def install_fonts(application):
    loaded = set()
    for filename in FONT_FILES:
        font_id = QFontDatabase.addApplicationFont(str(font_directory() / filename))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if font_id < 0 or not families:
            raise RuntimeError(tr('无法加载内置字体 {v0}，请使用完整便携包。', v0=(filename)))
        loaded.update(families)
    if not set(FONT_FAMILIES).issubset(loaded):
        raise RuntimeError(tr('内置字体名称不符，请使用完整便携包。'))
    font = QFont(FONT_FAMILIES[0], FONT_SIZE)
    font.setFamilies(list(FONT_FAMILIES))
    font.setWeight(QFont.Weight.Normal)
    font.setItalic(False)
    application.setFont(font)
    application.setProperty("bundled_font_families", list(FONT_FAMILIES))
