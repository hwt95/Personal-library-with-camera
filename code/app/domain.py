from dataclasses import dataclass, field
import re


def normalize_location_presets(values):
    if not isinstance(values, (list, tuple)):
        raise ValueError("地点预设应为地点名称列表")
    locations, seen = [], set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("地点名称不能为空")
        name = value.strip()
        if len(name) > 120 or any(c in name for c in ("\n", "\r", "\x00")):
            raise ValueError("地点名称请使用不超过 120 字的单行文字")
        if name.casefold() in seen:
            raise ValueError(f"地点已存在：{name}")
        seen.add(name.casefold())
        locations.append(name)
    return locations


def normalize_isbn(value: str) -> str:
    text = re.sub(r"[\s\-‐‑–—]", "", value).upper()
    if not text:
        return ""
    if re.fullmatch(r"\d{9}[\dX]", text):
        digits = [10 if c == "X" else int(c) for c in text]
        if sum((10 - i) * d for i, d in enumerate(digits)) % 11:
            raise ValueError("ISBN-10 校验位不正确")
        text = "978" + text[:9]
        return text + str((-sum(int(c) * (1 if i % 2 == 0 else 3)
                                for i, c in enumerate(text))) % 10)
    if not re.fullmatch(r"97[89]\d{10}", text):
        raise ValueError("请输入有效的图书 ISBN-10 或 978/979 开头的 ISBN-13")
    if sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(text)) % 10:
        raise ValueError("ISBN-13 校验位不正确")
    return text


def accession(clc_code: str, book_id: int, copy_no: int = 0) -> str:
    base = f"{clc_code or 'PENDING'}-{book_id:06d}"
    return base + (f"({copy_no})" if copy_no else "")


def validate_year(value) -> int:
    if not re.fullmatch(r"\d{1,4}", str(value).strip()):
        raise ValueError("请填写本册出版年份（1—9999 的整数）")
    year = int(value)
    if not 1 <= year <= 9999:
        raise ValueError("本册年份应在 1—9999 之间")
    return year


@dataclass
class Metadata:
    isbn: str = ""
    title: str = ""
    authors: str = ""
    translators: str = ""
    publisher: str = ""
    clc_code: str = ""
    summary: str = ""
    reference_pubdate: str = ""
    cover_blob: bytes | None = None
    cover_mime: str = ""
    source_info: dict = field(default_factory=dict)
    detail_url: str = ""


BOOK_FIELDS = ("isbn", "title", "authors", "translators", "publisher", "clc_code",
               "summary", "reference_pubdate", "cover_blob", "cover_mime", "source_info")


class ScanGate:
    """A draft owns the scanner until reset/save; the old barcode must then leave."""

    def __init__(self):
        self.locked = False
        self.blocked = ""
        self.absent = 0

    def observe(self, isbn: str) -> str | None:
        if not isbn:
            self.absent += 1
            if self.absent >= 3:
                self.blocked = ""
            return None
        self.absent = 0
        if self.locked or isbn == self.blocked:
            return None
        self.locked, self.blocked = True, isbn
        return isbn

    def release(self):
        self.locked = False
