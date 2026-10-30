"""Fetch fixed upstream source archives; never execute downloaded source code."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import hashlib
import json
import os
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
CACHE = Path(os.environ.get("PERSONAL_LIBRARY_COMPLIANCE_CACHE", ROOT / "deps/compliance-cache"))
MANIFEST = ROOT / "deps/compliance/SOURCES.json"
QT = "https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/"
SOURCES = [
    {"id": component, "version": "6.12.0", "url": QT + component + "-everywhere-src-6.12.0.tar.xz"}
    for component in ("qtbase", "qtsvg", "qtimageformats")
] + [
    {"id": "pyside-setup", "version": "6.12.0", "url": "https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.12.0-src/pyside-setup-everywhere-src-6.12.0.tar.xz"},
    {"id": "cpython", "version": "3.13.12", "url": "https://www.python.org/ftp/python/3.13.12/Python-3.13.12.tar.xz"},
    {"id": "onnxruntime", "version": "1.31.0", "url": "https://github.com/microsoft/onnxruntime/archive/refs/tags/v1.31.0.tar.gz"},
    {"id": "opencv", "version": "5.0.0", "url": "https://github.com/opencv/opencv/archive/refs/tags/5.0.0.tar.gz"},
    {"id": "zxing-cpp", "version": "3.1.1", "url": "https://github.com/zxing-cpp/zxing-cpp/archive/refs/tags/v3.1.1.tar.gz"},
    {"id": "libzint", "version": "55541e139e62b9209b71cd9b0ba9010cec28b1d9", "url": "https://github.com/zint/zint/archive/55541e139e62b9209b71cd9b0ba9010cec28b1d9.tar.gz"},
    {"id": "nanobind", "version": "2.11.0", "url": "https://github.com/wjakob/nanobind/archive/refs/tags/v2.11.0.tar.gz"},
    {"id": "openssl-python", "version": "3.0.18", "url": "https://github.com/openssl/openssl/archive/refs/tags/openssl-3.0.18.tar.gz"},
    {"id": "openssl-qt", "version": "3.6.1", "url": "https://github.com/openssl/openssl/archive/refs/tags/openssl-3.6.1.tar.gz"},
    {"id": "eigen", "version": "1d8b82b0740839c0de7f1242a3585e3390ff5f33", "url": "https://github.com/eigen-mirror/eigen/archive/1d8b82b0740839c0de7f1242a3585e3390ff5f33/eigen-1d8b82b0740839c0de7f1242a3585e3390ff5f33.zip", "upstream_sha1": "05b19b49e6fbb91246be711d801160528c135e34"},
    {"id": "ippicv", "version": "2026.0.0", "url": "https://raw.githubusercontent.com/opencv/opencv_3rdparty/406d398c436d0465c8e53dd432d9ecd9301d5f4a/ippicv/ippicv_2026.0.0_win_intel64_20260327_general.zip", "upstream_md5": "73bc67cd5e4c8da706fa88fe84630231", "kind": "binary SDK; acquired to preserve original license notices"},
    {"id": "robin-map", "version": "4ec1bf19c6a96125ea22062f38c2cf5b958e448e", "url": "https://github.com/Tessil/robin-map/archive/4ec1bf19c6a96125ea22062f38c2cf5b958e448e.tar.gz"},
    {"id": "opencv-python", "version": "5.0.0.93", "url": "https://files.pythonhosted.org/packages/1d/99/76b7c80252aa83c1af16393454aafd125a0287101afe8deb0a6821af0e30/opencv_python_headless-5.0.0.93.tar.gz", "upstream_sha256": "b82f9831daab90b725c7c1ee1b36cb5732c367096ac76d119e64e14eb70d5f3c"},
    {"id": "certifi", "version": "2026.10.23", "url": "https://github.com/certifi/python-certifi/archive/refs/tags/2026.10.23.tar.gz"},
] + [
    {"id": "python-" + name, "version": version, "url": f"https://github.com/python/cpython-source-deps/archive/refs/tags/{name}-{version}.tar.gz"}
    for name, version in (("bzip2", "1.0.8"), ("libffi", "3.4.4"), ("mpdecimal", "4.0.0"),
                          ("sqlite", "3.50.4.0"), ("xz", "5.2.5"), ("zlib", "1.3.1"))
]


def fetch(source, existing):
    record = dict(source)
    suffix = ".zip" if source["url"].endswith(".zip") else ".tar.xz" if source["url"].endswith(".xz") else ".tar.gz"
    name = source["id"] + "-" + source["version"] + suffix
    target = CACHE / name
    old = existing.get(source["id"], {})
    if target.exists() and old.get("sha256") == hashlib.sha256(target.read_bytes()).hexdigest():
        return old
    request = urllib.request.Request(source["url"], headers={"User-Agent": "PersonalLibrary-Compliance/1.0"})
    partial = target.with_suffix(target.suffix + ".part")
    try:
        digest = hashlib.sha256()
        size = 0
        with urllib.request.urlopen(request, timeout=40) as response, partial.open("wb") as output:
            record["resolved_url"] = response.url
            while block := response.read(1024 * 1024):
                digest.update(block)
                output.write(block)
                size += len(block)
        for algorithm in ("sha1", "md5", "sha256"):
            if source.get("upstream_" + algorithm):
                actual = hashlib.new(algorithm, partial.read_bytes()).hexdigest()
                if actual != source["upstream_" + algorithm]:
                    raise ValueError(f"Upstream {algorithm} mismatch for {source['id']}")
                record["verified_" + algorithm] = actual
        partial.replace(target)
        record.update(file=name, bytes=size, sha256=digest.hexdigest(), status="downloaded",
                      acquired_date="2026-10-25", modifications="none; upstream archive retained verbatim")
    except Exception as error:
        record.update(status="unavailable", error=str(error))
    return record


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    existing = {s["id"]: s for s in json.loads(MANIFEST.read_text("utf-8"))} if MANIFEST.exists() else {}
    records = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch, source, existing): source for source in SOURCES}
        for future in as_completed(futures):
            record = future.result()
            records.append(record)
            print(json.dumps({k: record[k] for k in ("id", "status", "bytes", "error") if k in record}), flush=True)
            MANIFEST.write_text(json.dumps(sorted(records, key=lambda r: r["id"]), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if any(s["status"] != "downloaded" for s in records):
        sys.exit(1)


if __name__ == "__main__":
    main()
