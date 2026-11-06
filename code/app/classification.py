"""Classification identity is stored per title, independently of UI language."""
import re
from .i18n import tr

SYSTEMS = ("clc", "ddc", "lcc")
FIELDS = {"clc": "clc_code", "ddc": "ddc_code", "lcc": "lcc_code"}


def resolve_system(mode="auto", language="zh_CN"):
    if mode == "auto":
        return "clc" if language == "zh_CN" else "ddc"
    if mode not in SYSTEMS:
        raise ValueError(tr("分类规则无效"))
    return mode


def system_label(system):
    return {"clc": tr("CLC · 中国图书馆分类法"), "ddc": tr("DDC · 杜威十进分类法"),
            "lcc": tr("LCC · 美国国会图书馆分类法"), "auto": tr("自动：简体中文 CLC，其他语言 DDC")}[system]


def normalize_code(system, value):
    if system not in SYSTEMS:
        raise ValueError(tr("分类规则无效"))
    code = str(value or "").strip().upper()
    if not code:
        return ""
    if len(code) > 80 or any(c in code for c in "\n\r\x00"):
        raise ValueError(tr("分类号应为不超过 80 字符的单行文字"))
    if system == "ddc":
        # MARC/Open Library may use slashes or apostrophes as segmentation marks.
        code = code.replace("/", "").replace("'", "")
        if not re.fullmatch(r"[0-9]{3}(?:\.[0-9]+)?", code):
            raise ValueError(tr("DDC 分类号应为三位数字及可选小数，例如 005.133"))
    elif system == "lcc":
        code = " ".join(code.split())
        if not re.fullmatch(r"[A-Z]{1,3}(?:[0-9][A-Z0-9. /:+()\-]*)?", code):
            raise ValueError(tr("LCC 分类号应以一至三个英文字母开头，例如 QA76.73.P98"))
    elif any(c.isspace() for c in code):
        raise ValueError(tr("主分类号请填单个分类号，不能含空格"))
    return code


def active_code(data):
    system = data.get("classification_system", "clc")
    return data.get(FIELDS[system], "")


def number_scope(data):
    if data.get("media_type") == "magazine":
        return "MAG"
    system = data.get("classification_system", "clc")
    code = normalize_code(system, active_code(data)) or "PENDING"
    return code if system == "clc" else system.upper() + "-" + code


def metadata_codes(record):
    """Use explicit source fields only; subjects/categories are not class numbers."""
    result = {}
    nested = record.get("classifications")
    if not isinstance(nested, dict):
        nested = {}
    for system, source_key in (("ddc", "dewey_decimal_class"), ("lcc", "lc_classifications")):
        values = record.get(source_key) or nested.get(source_key) or []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, (list, tuple)):
            values = []
        codes = []
        for value in values:
            try:
                code = normalize_code(system, value)
                if code and code not in codes:
                    codes.append(code)
            except ValueError:
                continue
        result[FIELDS[system]] = codes[0] if len(codes) == 1 else ""
    return result
