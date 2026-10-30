"""Build-selected functionality; offline distributions contain no catalog adapter."""
import json
from pathlib import Path
import sys


def _read_offline():
    if not getattr(sys, "frozen", False):
        return False
    try:
        document = json.loads((Path(sys._MEIPASS) / "distribution.json").read_text("utf-8"))
        return document["data_mode"] != "online"
    except (OSError, ValueError, KeyError):
        return True  # An absent or damaged build descriptor cannot enable networking.


OFFLINE = _read_offline()


async def lookup(isbn, source="auto", key="", detail_url="", status=None, language="zh_CN", **kwargs):
    if OFFLINE or source == "manual":
        if status:
            status("仅手工 / 离线")
        return [], "离线模式：可复用本地书目或手工填写资料"
    from .providers.catalog import lookup as online_lookup
    if language != "zh_CN":
        if source in ("nlc", "auto"):
            source = "auto_global"
        detail_url = ""
    return await online_lookup(isbn, source, key, detail_url, status=status, **kwargs)
