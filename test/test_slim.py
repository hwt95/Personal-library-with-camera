import json
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QTextLayout

from app.licenses import LicenseDialog, source_records
from app.typography import FONT_FILES, font_directory
from test_ui import application, window


def test_source_download_page_opens_verified_https_link(window, monkeypatch):
    import app.licenses as licenses
    opened = []
    monkeypatch.setattr(licenses.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    dialog = LicenseDialog(window, show_sources=True)
    records = source_records()
    assert len(records) == 6
    for record in records:
        assert record["url"] in dialog.text.toHtml()
        assert record["version"] in dialog.text.toPlainText()
        assert record["sha256"] in dialog.text.toPlainText()
    url = records[0]["url"]
    dialog.text.anchorClicked.emit(QUrl(url))
    assert opened == [url]
    dialog.text.anchorClicked.emit(QUrl("file:///C:/Windows/system.ini"))
    assert opened == [url]
    dialog.reject()


def test_regular_fonts_cover_their_scripts(application):
    assert {p.name for p in font_directory().glob("*.ttf")} == set(FONT_FILES)
    manifest = json.loads((font_directory() / "SOURCE.json").read_text("utf-8"))
    wenkai = next(record for record in manifest if record["file"] == "LXGWWenKai-Regular.ttf")
    assert wenkai["version"] == "1.530"
    assert application.font().weight() == 400
    for text, expected in (("ABC 0123456789", "Tinos"), ("中文藏书管理", "LXGW WenKai"), ("العربية", "Noto Sans Arabic")):
        layout = QTextLayout(text, application.font())
        layout.beginLayout()
        layout.createLine()
        layout.endLayout()
        runs = layout.glyphRuns()
        assert runs and all(run.rawFont().familyName() == expected and all(run.glyphIndexes()) for run in runs)
