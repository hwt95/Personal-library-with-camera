"""Prepare redistributable source/notices from verified archives, without extraction."""
from pathlib import Path, PurePosixPath
import hashlib
import importlib.metadata as metadata
import json
import os
import posixpath
import shutil
import platform
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
CACHE = Path(os.environ.get("PERSONAL_LIBRARY_COMPLIANCE_CACHE", ROOT / "deps/compliance-cache"))
SOURCE_IDS = {"qtbase", "qtsvg", "qtimageformats", "pyside-setup", "eigen", "certifi"}
REMOVED = {
    "opencv_videoio_ffmpeg500_64.dll": "No video-file/FFmpeg capture; camera uses CAP_DSHOW.",
    "opengl32sw.dll": "Unused Mesa/LLVM software OpenGL fallback; app uses raster Widgets.",
    "Qt6Network.dll": "App networking uses httpx/Python, not QtNetwork.",
    "onnxruntime.dll": "Standalone C API is unused; Python inference uses onnxruntime_pybind11_state.pyd.",
    "qdirect2d.dll": "Windows raster Widgets use qwindows; the alternate Direct2D platform is unused.",
    "qminimal.dll": "The minimal platform is unused; qwindows and the offscreen self-test platform are retained.",
}


def verified_sources():
    records = json.loads((ROOT / "deps/compliance/SOURCES.json").read_text("utf-8"))
    from scripts.fetch_compliance_sources import SOURCES
    expected = {record["id"] for record in SOURCES}
    if {record["id"] for record in records} != expected:
        raise ValueError("Incomplete compliance source manifest; run fetch_compliance_sources.py")
    versions = {record["id"]: record["version"] for record in records}
    for distribution, source in (("PySide6-Essentials", "pyside-setup"), ("shiboken6", "pyside-setup"),
                                 ("opencv-python-headless", "opencv-python"), ("onnxruntime", "onnxruntime"),
                                 ("zxing-cpp", "zxing-cpp"), ("certifi", "certifi")):
        if metadata.version(distribution) != versions[source]:
            raise ValueError(f"Installed {distribution} does not match recorded source version")
    from PySide6.QtCore import qVersion
    if qVersion() != versions["qtbase"] or platform.python_version() != versions["cpython"]:
        raise ValueError("Installed Qt/Python version does not match recorded source version")
    for record in records:
        archive = CACHE / record.get("file", "missing")
        if record.get("status") != "downloaded" or not archive.is_file():
            raise ValueError(f"Missing source/evidence archive: {record['id']}")
        with archive.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != record["sha256"]:
                raise ValueError(f"Source SHA-256 mismatch: {record['id']}")
        for algorithm in ("sha1", "md5", "sha256"):
            if record.get("upstream_" + algorithm):
                with archive.open("rb") as stream:
                    if hashlib.file_digest(stream, algorithm).hexdigest() != record["upstream_" + algorithm]:
                        raise ValueError(f"Upstream hash mismatch: {record['id']}")
    return records


def archive_members(path):
    """Yield regular files only; never follow archive links or unpack executable code."""
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            members = {m.filename: m for m in archive.infolist() if not m.is_dir()}
            yield members, lambda name: archive.read(members[name])
    else:
        with tarfile.open(path) as archive:
            members = {m.name: m for m in archive.getmembers() if m.isfile()}
            yield members, lambda name: archive.extractfile(members[name]).read()


def collect_notices(record, package):
    destination = package / "licenses/native" / record["id"]
    manifest, attributions = [], []
    for members, read in archive_members(CACHE / record["file"]):
        selected = {name for name in members if
                    any(word in PurePosixPath(name).name.lower() for word in
                        ("license", "licence", "copying", "copyright", "notice", "patents", "eula", "third-party-programs"))
                    or "/LICENSES/" in name or name.endswith("qt_attribution.json")}
        if record["id"] in ("python-sqlite", "python-zlib"):
            header = "sqlite3.h" if record["id"] == "python-sqlite" else "zlib.h"
            selected.update(name for name in members if PurePosixPath(name).name == header)
        if record["id"] == "llvm":
            selected.update(name for name in members if name.endswith(("lib/Support/MD5.cpp", "include/llvm/Support/MD5.h")))
        for name in members:
            if (not name.endswith("qt_attribution.json") or "/tests/" in name or "/test/" in name):
                continue
            value = json.loads(read(name), strict=False)
            for item in value if isinstance(value, list) else [value]:
                attributions.append({"source_file": name, **item})
                files = item.get("LicenseFile", [])
                for filename in files if isinstance(files, list) else [files]:
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(name), filename))
                    if target not in members:
                        raise ValueError(f"Missing Qt attribution license: {target}")
                    selected.add(target)
        for name in sorted(selected):
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or any(":" in part for part in relative.parts):
                raise ValueError(f"Unsafe archive member: {name}")
            target = destination.joinpath(*relative.parts)
            target.resolve().relative_to(destination.resolve())
            data = read(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            manifest.append({"upstream_path": name, "file": target.relative_to(package).as_posix(),
                             "sha256": hashlib.sha256(data).hexdigest()})
    if not manifest:
        raise ValueError(f"No notices found: {record['id']}")
    if attributions:
        (destination / "ATTRIBUTIONS.json").write_text(json.dumps(attributions, ensure_ascii=False, indent=2), "utf-8")
    return {"component": record["id"], "version": record["version"], "origin": record["url"],
            "archive_sha256": record["sha256"], "files": manifest,
            "scope": "Conservative upstream notice collection, including optional/build/test components; not a claim that every component is linked."}


def trim_unused_runtime(package):
    removed = []
    for path in list((package / "_internal").rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(package).as_posix()
        reason = REMOVED.get(path.name)
        if "/PySide6/" in relative and ("/plugins/tls/" in relative or "/plugins/networkinformation/" in relative
                or path.name in ("qtuiotouchplugin.dll", "QtNetwork.pyd", "libssl-3-x64.dll", "libcrypto-3-x64.dll")):
            reason = "QtNetwork and its plugins are unused; Python's separate SSL libraries are retained."
        if reason:
            path.resolve().relative_to(package.resolve())
            removed.append({"file": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "reason": reason})
            path.unlink()
    return removed


def install_compliance(package, records):
    removed = trim_unused_runtime(package)
    included, notices = [], []
    for record in records:
        if record["id"] == "openssl-qt":
            continue  # QtNetwork/Qt TLS binaries have been excluded.
        notices.append(collect_notices(record, package))
        if record["id"] in SOURCE_IDS:
            included.append(record)
    (package / "SOURCE_DOWNLOADS.json").write_text(json.dumps(included, ensure_ascii=False, indent=2) + "\n", "utf-8")
    (package / "licenses/native/INDEX.json").write_text(json.dumps(notices, ensure_ascii=False, indent=2) + "\n", "utf-8")
    source_guide = (ROOT / "deps/compliance/SOURCE_CODE.md").read_text("utf-8")
    download_table = ["| 组件 / 版本 | 对应源码下载 | SHA-256 |", "| --- | --- | --- |"]
    download_table += [f"| {r['id']} {r['version']} | [{r['file']}]({r['url']}) | `{r['sha256']}` |" for r in included]
    (package / "SOURCE_CODE.md").write_text(source_guide.replace("<!-- SOURCE_DOWNLOAD_TABLE -->", "\n".join(download_table)), "utf-8")
    return {
        "removed_runtime_files": removed, "bundled_source_archives": [],
        "external_source_archives": sorted(SOURCE_IDS),
        "source_delivery": "Exact-version external downloads in SOURCE_DOWNLOADS.json and SOURCE_CODE.md; no source archives installed.",
        "source_versions": {r["id"]: r["version"] for r in included},
        "source_and_notice_collection_only": True,
    }
