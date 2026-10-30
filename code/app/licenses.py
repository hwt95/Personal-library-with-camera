from .i18n import tr
"""A local, searchable viewer for the exact license texts shipped in the bundle."""
from pathlib import Path
import html
import json
import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QVBoxLayout, QLineEdit, QSplitter, QListWidget, QTextBrowser, QLabel

from .chrome import AppDialog
from .widgets import button, row

SOURCE_IDS = {"qtbase", "qtsvg", "qtimageformats", "pyside-setup", "eigen", "certifi"}


def source_document():
    root = package_directory()
    path = root / "SOURCE_DOWNLOADS.json"
    return path if path.is_file() else root / "deps/compliance/SOURCES.json"


def source_records(path=None):
    return [record for record in json.loads((path or source_document()).read_text("utf-8"))
            if record["id"] in SOURCE_IDS]


def source_download_html(records):
    escape = html.escape
    parts = ["<h2>" + escape(tr('第三方源码下载')) + "</h2>",
             "<p>" + escape(tr('下列链接提供本版本所用组件的原始源码。日常运行无需下载；点击链接将在浏览器打开。')) + "</p>"]
    for record in records:
        url = record["url"]
        if QUrl(url).scheme() != "https":
            continue
        parts += ["<h3>" + escape(record["id"]) + " — " + escape(record["version"]) + "</h3>",
                  '<p><a href="' + escape(url, quote=True) + '">' + escape(record["file"]) + "</a></p>",
                  "<p>SHA-256: <small>" + escape(record["sha256"]) + "</small></p>"]
    parts.append("<p>" + escape(tr('动态库替换与重新分发说明见左侧 SOURCE_CODE.md。')) + "</p>")
    return "".join(parts)


def package_directory():
    return Path(sys.executable).parent if getattr(sys,"frozen",False) else Path(__file__).resolve().parents[2]


def license_documents():
    root = package_directory()
    documents = []
    for name in ("LICENSE", "NOTICE", "LICENSING.md", "COMMERCIAL_LICENSING.md", "DATA_SOURCES.md", "SOURCE_CODE.md", "THIRD_PARTY_NOTICES.txt", "THIRD_PARTY.json"):
        path = root/name
        if path.is_file():
            documents.append((name, path))
    if source_document().is_file():
        documents.insert(1, (tr('第三方源码下载'), source_document()))
    if not (root / "SOURCE_CODE.md").is_file() and (root / "deps/compliance/SOURCE_CODE.md").is_file():
        documents.append(("SOURCE_CODE.md", root / "deps/compliance/SOURCE_CODE.md"))
    folders = [(tr('第三方组件'), root/"licenses")]
    if not getattr(sys,"frozen",False):
        folders += [(tr('第三方组件'), root/"build/dist/PersonalLibrary/licenses"),
                    (tr('字体'), root/"code/resources/fonts"), ("Qt", root/"code/resources/licenses"),
                    (tr('封面模型'), root/"deps/models/book_boundary"), (tr('方向模型'), root/"deps/models/document_orientation")]
    for label, folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.suffix.lower() not in (".ttf", ".otf", ".onnx", ".zip", ".gz", ".xz"):
                documents.append((label+" / "+str(path.relative_to(folder)), path))
    return documents


class LicenseDialog(AppDialog):
    def __init__(self, parent, *, show_sources=False):
        super().__init__(parent)
        self.setWindowTitle(tr('软件许可与第三方协议'))
        self.resize(1120, 800)
        layout = QVBoxLayout(self)
        note = QLabel(tr('原创代码采用 PolyForm Noncommercial 1.0.0（源码可见、非商业许可）。第三方软件、模型、字体和运行库分别保留原许可；以下显示随包提供的原文。'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.query = QLineEdit()
        self.query.setPlaceholderText(tr('搜索组件或文件名'))
        layout.addWidget(self.query)
        split = QSplitter()
        self.list = QListWidget()
        self.text = QTextBrowser()
        self.text.setOpenExternalLinks(False)
        self.text.document().setDefaultStyleSheet("body, p, h1, h2, h3, h4, b, strong { font-weight: normal; font-style: normal; }")
        self.text.setOpenLinks(False)
        self.text.anchorClicked.connect(self.open_link)
        self.text.setReadOnly(True)
        split.addWidget(self.list)
        split.addWidget(self.text)
        split.setSizes([330, 770])
        layout.addWidget(split, 1)
        self.documents = license_documents()
        self.query.textChanged.connect(self.filter)
        self.list.currentRowChanged.connect(self.display)
        layout.addWidget(row(button(tr('第三方源码下载'), self.show_sources), button(tr('打开许可证目录'), self.open_folder), button(tr('关闭'), self.accept)))
        self.filter()
        if show_sources:
            self.show_sources()

    def show_sources(self):
        self.query.clear()
        index = next((i for i, (_, path) in enumerate(self.visible) if path == source_document()), -1)
        self.list.setCurrentRow(index)

    def open_link(self, url):
        if url.scheme() == "https":
            if not QDesktopServices.openUrl(url):
                self.parent().error(tr('未能打开浏览器，请复制链接后打开：{url}', url=url.toString()))

    def filter(self):
        query = self.query.text().casefold()
        self.visible = [(label,path) for label,path in self.documents if query in label.casefold()]
        self.list.clear()
        self.list.addItems([label for label,_ in self.visible])
        if self.visible:
            self.list.setCurrentRow(0)
        else:
            self.text.clear()

    def display(self, index):
        if 0 <= index < len(self.visible):
            label,path = self.visible[index]
            if path == source_document():
                self.text.setHtml(source_download_html(source_records(path)))
            elif path.name == "SOURCE_CODE.md":
                self.text.setMarkdown(path.read_text(encoding="utf-8-sig"))
            else:
                self.text.setPlainText(label+"\n\n"+path.read_text(encoding="utf-8-sig", errors="replace"))

    def open_folder(self):
        root = package_directory()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root/"licenses" if (root/"licenses").is_dir() else root)))
