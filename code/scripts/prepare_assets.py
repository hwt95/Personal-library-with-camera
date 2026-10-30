"""Fetch only the pinned selected fonts and verify hashes before packaging."""
from pathlib import Path
import hashlib
import json
import urllib.request

root = Path(__file__).resolve().parents[2]
folder = root / "code" / "resources" / "fonts"
folder.mkdir(parents=True, exist_ok=True)
records = json.loads((folder / "SOURCE.json").read_text(encoding="utf-8"))
for record in records:
    destination = folder / record["file"]
    if not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != record["sha256"]:
        with urllib.request.urlopen(record["url"], timeout=120) as response:
            blob = response.read()
        if hashlib.sha256(blob).hexdigest() != record["sha256"]:
            raise RuntimeError(f"字体校验失败：{record['file']}")
        destination.write_bytes(blob)
    print(f"Verified {record['file']}: {record['sha256']}")
license_dir = root / "code" / "resources" / "licenses"
license_dir.mkdir(parents=True, exist_ok=True)
for name in ("LGPL-3.0-only.txt", "GPL-3.0-only.txt", "GPL-2.0-only.txt"):
    destination = license_dir / name
    if not destination.is_file():
        url = "https://raw.githubusercontent.com/qt/qtbase/dev/LICENSES/" + name
        with urllib.request.urlopen(url, timeout=60) as response:
            destination.write_bytes(response.read())
    print(f"{name}: {destination.stat().st_size} bytes")
