"""Build a portable Windows folder and ZIP; never reads personal databases/settings."""
from pathlib import Path
import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "build" / "dist"
WORK = ROOT / "build" / "work"
STAGED_DIST = WORK / "staged-dist"
PACKAGE = STAGED_DIST / "PersonalLibrary"
DEPLOYED_PACKAGE = DIST / "PersonalLibrary"
RESOURCES = ROOT / "code" / "resources"
sys.path.insert(0, str(ROOT / "code"))
from app import APP_NAME, __version__, __author__


def write_version_info(destination):
    from PyInstaller.utils.win32.versioninfo import (VSVersionInfo, FixedFileInfo, StringFileInfo,
                                                    StringTable, StringStruct, VarFileInfo, VarStruct)
    parts = tuple(int(value) for value in __version__.split("."))
    numeric = (parts + (0, 0, 0, 0))[:4]
    strings = {"CompanyName": __author__, "FileDescription": APP_NAME,
               "FileVersion": ".".join(map(str, numeric)), "InternalName": "PersonalLibrary",
               "OriginalFilename": "PersonalLibrary.exe", "ProductName": APP_NAME,
               "ProductVersion": __version__, "Comments": "作者：" + __author__, "Author": __author__}
    resource = VSVersionInfo(ffi=FixedFileInfo(filevers=numeric, prodvers=numeric), kids=[
        StringFileInfo([StringTable("080404B0", [StringStruct(key, value) for key, value in strings.items()])]),
        VarFileInfo([VarStruct("Translation", [0x0804, 1200])])])
    destination.write_text(str(resource), encoding="utf-8")
    return destination


def deploy_program(staged, destination, backup):
    """Replace only managed program artifacts, with rollback. Never move user data."""
    for path in (staged, destination, backup):
        path.resolve().relative_to(ROOT.resolve())
    names = {"PersonalLibrary.exe", "_internal", "licenses", "使用说明.md", "DEPENDENCIES.txt",
             "THIRD_PARTY.json", "THIRD_PARTY_NOTICES.txt", "LICENSE", "NOTICE", "LICENSING.md",
             "COMMERCIAL_LICENSING.md", "DATA_SOURCES.md", "sources", "SOURCE_CODE.md", "SOURCE_DOWNLOADS.json", "COMPLIANCE_BUILD.json", "CHANGELOG.md"}
    items = list(staged.iterdir())
    if {p.name for p in items} - names:
        raise ValueError("暂存目录中有非程序文件，已取消发布")
    # A database inside a managed runtime directory is unusual but still user data.
    for name in ("_internal", "licenses", "sources"):
        folder = destination / name
        if folder.exists() and any(p.suffix.lower() in (".sqlite3", ".sqlite", ".db") or p.name == "settings.json"
                                   for p in folder.rglob("*") if p.is_file()):
            raise ValueError(f"{folder} 内含个人数据，无法整体更新该运行库目录")
    destination.mkdir(parents=True, exist_ok=True)
    backup.mkdir(parents=True, exist_ok=False)
    moved, copied = [], []
    try:
        # Retire the old source-archive folder even though new builds omit it.
        for name in sorted({item.name for item in items} | {"sources"}):
            target = destination / name
            if target.exists():
                target.resolve().relative_to(destination.resolve())
                saved = backup / name
                saved.resolve().relative_to(backup.resolve())
                target.rename(saved)
                moved.append(name)
        # Install the executable last, once its complete runtime is in place.
        for item in sorted(items, key=lambda p: p.name == "PersonalLibrary.exe"):
            target = destination / item.name
            copied.append(target)
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
    except Exception:
        for target in reversed(copied):
            target.resolve().relative_to(destination.resolve())
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        for name in reversed(moved):
            (backup / name).rename(destination / name)
        raise
    return backup


def user_file_hashes(folder):
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob("*") if p.is_file() and
            (p.suffix.lower() in (".sqlite3", ".sqlite", ".db") or p.name == "settings.json")}


def main():
    global DIST, WORK, STAGED_DIST, PACKAGE
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", action="store_true", help="Build a separate compliance candidate without deploying over an existing program")
    parser.add_argument("--offline", action="store_true", help="Exclude all network catalog adapters; implies --candidate")
    options = parser.parse_args()
    if options.offline:
        options.candidate = True
    if options.candidate:
        DIST = ROOT / ("build/compliance-offline-candidate" if options.offline else "build/compliance-candidate")
        WORK = ROOT / ("build/compliance-offline-work" if options.offline else "build/compliance-work")
        STAGED_DIST = WORK / "staged-dist"
        PACKAGE = STAGED_DIST / "PersonalLibrary"
    if sys.platform != "win32":
        raise SystemExit("Windows 便携包请在 Windows x64 上构建")
    from scripts.package_compliance import verified_sources, install_compliance
    sources = verified_sources()
    model_folder = ROOT / "deps" / "models" / "book_boundary"
    for asset in json.loads((model_folder / "SOURCE.json").read_text(encoding="utf-8"))["assets"]:
        if hashlib.sha256((model_folder / asset["file"]).read_bytes()).hexdigest() != asset["sha256"]:
            raise RuntimeError(f"Boundary model asset checksum mismatch: {asset['file']}")
    subprocess.run([sys.executable, str(Path(__file__).with_name("prepare_assets.py"))], check=True)
    WORK.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)
    STAGED_DIST.mkdir(parents=True, exist_ok=True)
    PACKAGE.resolve().relative_to((ROOT / "build").resolve())
    if PACKAGE.exists() and (list(PACKAGE.rglob("*.sqlite3")) or (PACKAGE / "settings.json").exists()):
        raise SystemExit("临时构建目录存在个人数据，已取消构建")
    version_file = write_version_info(WORK / "version-info.txt")
    icon_path = RESOURCES / "app-icon.ico"
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    icon_app = QGuiApplication.instance() or QGuiApplication([])
    icon_image = QImage(256, 256, QImage.Format.Format_ARGB32)
    icon_image.fill(0)
    icon_painter = QPainter(icon_image)
    QSvgRenderer(str(RESOURCES / "app-icon.svg")).render(icon_painter)
    icon_painter.end()
    if not icon_image.save(str(icon_path), "ICO"):
        raise RuntimeError("Cannot write application icon")
    edition_file = WORK / "distribution.json"
    edition_file.write_text(json.dumps({"data_mode": "offline" if options.offline else "online"}), encoding="utf-8")
    from PySide6 import __file__ as qt_file
    qt_root = Path(qt_file).parent
    # Collect the C/C++ runtime shipped in Qt's Windows wheel, not the system's DLLs.
    runtime = [p for p in qt_root.glob("*140*.dll") if p.name.startswith(("msvcp", "vcruntime", "concrt", "vcomp"))]
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed", "--noupx",
            "--name", "PersonalLibrary", "--distpath", str(STAGED_DIST), "--workpath", str(WORK / "pyinstaller"),
            "--specpath", str(WORK), "--paths", str(ROOT / "code"),
            "--version-file", str(version_file),
            "--icon", str(icon_path),
            "--add-data", f"{RESOURCES};resources",
            "--add-data", f"{edition_file};.",
            "--add-data", f"{ROOT / 'deps' / 'models' / 'document_orientation'};models/document_orientation",
            "--add-data", f"{ROOT / 'deps' / 'models' / 'book_boundary'};models/book_boundary",
            "--collect-binaries", "onnxruntime", "--collect-data", "certifi",
            "--hidden-import", "numpy", "--hidden-import", "zxingcpp",
            "--exclude-module", "pytest", "--exclude-module", "_pytest", "--exclude-module", "tkinter", "--exclude-module", "PySide6.QtQml",
            "--exclude-module", "PySide6.QtNetwork",
            "--exclude-module", "PySide6.QtQuick", "--exclude-module", "PySide6.QtOpenGLWidgets"]
    if options.offline:
        args += ["--exclude-module", "app.providers.catalog"]
    for dll in runtime:
        if (Path(sys.base_prefix) / dll.name).is_file():
            dll = Path(sys.base_prefix) / dll.name
        args += ["--add-binary", f"{dll};."]
    args.append(str(ROOT / "code" / "main.py"))
    log = WORK / "build.log"
    build_environment = os.environ.copy()
    build_environment["PYTHONNOUSERSITE"] = "1"
    build_environment["PYTHONUSERBASE"] = str(WORK / "python-userbase")
    build_environment["PYINSTALLER_CONFIG_DIR"] = str(WORK / "pyinstaller-cache")
    windows = Path(os.environ.get("SystemRoot", "C:/Windows"))
    # Foreign tools on PATH may supply an incompatible DLL with the same name
    # (notably Poppler's ICU versus the Windows ICU used by Qt).
    build_environment["PATH"] = os.pathsep.join(map(str, (windows / "System32", windows, Path(sys.executable).parent, Path(sys.base_prefix))))
    with log.open("w", encoding="utf-8") as output:
        subprocess.run(args, cwd=ROOT, env=build_environment, stdout=output, stderr=subprocess.STDOUT, check=True)
    shutil.copy2(ROOT / "code" / "USER_GUIDE.md", PACKAGE / "使用说明.md")
    for name in ("LICENSE", "NOTICE", "LICENSING.md", "COMMERCIAL_LICENSING.md", "DATA_SOURCES.md", "CHANGELOG.md"):
        shutil.copy2(ROOT / name, PACKAGE / name)
    shutil.copy2(ROOT / "deps" / "requirements.lock.txt", PACKAGE / "DEPENDENCIES.txt")
    licenses = PACKAGE / "licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copytree(RESOURCES / "licenses", licenses / "Qt", dirs_exist_ok=True)
    inventory = []
    # Carry upstream notices/license texts, including bundled third-party notices.
    for name in ("PySide6-Essentials", "shiboken6", "opencv-python-headless", "zxing-cpp", "httpx", "httpcore", "h11", "anyio", "certifi", "idna", "beautifulsoup4", "soupsieve", "typing-extensions", "numpy", "onnxruntime", "flatbuffers", "packaging", "protobuf", "PyInstaller"):
        dist = metadata.distribution(name)
        paths = []
        for item in dist.files or []:
            if any(word in Path(str(item)).name.lower() for word in ("license", "licence", "copying", "notice")):
                source = Path(dist.locate_file(item))
                if source.is_file():
                    safe = str(item).replace("../", "").replace("..\\", "").replace("/", "__").replace("\\", "__")
                    target = licenses / name / safe
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    paths.append(str(target.relative_to(PACKAGE)))
        inventory.append({"name": name, "version": dist.version, "license": dist.metadata.get("License-Expression") or dist.metadata.get("License", "See bundled license files"), "files": paths})
    for name in ("LICENSE.txt", "LICENSE"):
        python_license = Path(sys.base_prefix) / name
        if python_license.is_file():
            shutil.copy2(python_license, licenses / "Python-LICENSE.txt")
            break
    shutil.copytree(ROOT / "deps" / "models" / "document_orientation", licenses / "document_orientation", dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.onnx"))
    shutil.copytree(ROOT / "deps" / "models" / "book_boundary", licenses / "book_boundary", dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.onnx"))
    shutil.copy2(RESOURCES / "ICON_NOTICE.txt", licenses / "ICON_NOTICE.txt")
    shutil.copytree(RESOURCES / "fonts", licenses / "font", dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.otf", "*.ttf"))
    font_sources = json.loads((RESOURCES / "fonts" / "SOURCE.json").read_text(encoding="utf-8"))
    for name, license_file in (("LXGW WenKai", "WenKai-OFL.txt"), ("Tinos", "Tinos-LICENSE.txt"), ("Noto Sans Arabic", "NotoSansArabic-OFL.txt")):
        source = next(item for item in font_sources if item["file"] == license_file)
        inventory.append({"name": name, "version": source.get("version", source["commit"]), "license": "OFL-1.1",
                          "files": ["licenses/font/" + license_file], "source": source["url"]})
    inventory.append({"name": "DocAligner T8 / LCNet", "version": "1.1.1", "license": "Apache-2.0",
                      "files": ["licenses/book_boundary/docaligner-LICENSE", "licenses/book_boundary/SOURCE.json"]})
    (PACKAGE / "THIRD_PARTY.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    (PACKAGE / "THIRD_PARTY_NOTICES.txt").write_text(
        "Personal Library third-party notices\n\n"
        "Qt / PySide6 / Shiboken are dynamically linked under their applicable LGPLv3/GPL/commercial terms. "
        "This distribution uses the open-source LGPL components. License texts accompany this package. "
        "Users may replace compatible Qt/PySide libraries and debug modifications as permitted by the LGPL. "
        "Exact-version source downloads are listed in SOURCE_DOWNLOADS.json and the in-app license viewer; "
        "see SOURCE_CODE.md for download links, checksums and replacement instructions.\n\n"
        "OpenCV, NumPy, ZXing, ONNX Runtime and other dependencies retain their original licenses; see licenses/ and THIRD_PARTY.json.\n"
        "LXGW WenKai 1.530 Regular is from lxgw/LxgwWenKai; Tinos Regular is from googlefonts/tinos; "
        "Noto Sans Arabic is from Google Fonts and used at regular weight. All retain SIL Open Font License 1.1. "
        "The original, unmodified fonts may be used commercially and bundled with software; "
        "see licenses/font/WenKai-OFL.txt, Tinos-LICENSE.txt, NotoSansArabic-OFL.txt and SOURCE.json for terms and pinned sources.\n"
        "PP-LCNet_x1_0_doc_ori: PaddlePaddle model, Apache-2.0; exact revision and checksums in licenses/document_orientation/SOURCE.json.\n"
        "DocAligner 1.1.1 T8 / LCNet: Apache-2.0; model sources and checksums in licenses/book_boundary/SOURCE.json.\n"
        "NLC parsing is independently implemented against catalog HTML; no calibre plugin code is included.\n", encoding="utf-8")
    compliance = install_compliance(PACKAGE, sources)
    (WORK / "compliance-build.json").write_text(json.dumps(compliance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Keep the source directions beside the downloadable binary as well as in its UI.
    shutil.copy2(PACKAGE / "SOURCE_CODE.md", DIST / "SOURCE_DOWNLOADS.md")
    # Packaging uses only the staging tree; an existing library or API key cannot leak into the ZIP.
    archive = shutil.make_archive(str(DIST / "PersonalLibrary-Windows-x64"), "zip", STAGED_DIST, PACKAGE.name)
    archive_path = Path(archive)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    archive_path.with_suffix(".zip.sha256").write_text(f"{digest}  {archive_path.name}\n", encoding="ascii")
    if options.candidate:
        result = {"zip": archive, "sha256": digest, "zip_bytes": archive_path.stat().st_size,
                  "candidate_only": True, "data_mode": "offline" if options.offline else "online", "deployed": False, "published": False,
                  "app_version": __version__, "release_authorization": "pending"}
        (DIST / "candidate.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
        return
    before = user_file_hashes(DEPLOYED_PACKAGE) if DEPLOYED_PACKAGE.exists() else {}
    backup = WORK / "previous-program" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    deploy_program(PACKAGE, DEPLOYED_PACKAGE, backup)
    after = user_file_hashes(DEPLOYED_PACKAGE)
    preserved = before == after
    deployment = {"exe": str(DEPLOYED_PACKAGE / "PersonalLibrary.exe"), "zip": archive,
                  "app_version": __version__, "author": __author__,
                  "zip_bytes": archive_path.stat().st_size, "sha256": digest,
                  "previous_program_backup": str(backup), "user_files_preserved": preserved,
                  "user_files_before": before, "user_files_after": after}
    (WORK / "deployment.json").write_text(json.dumps(deployment, ensure_ascii=False, indent=2), encoding="utf-8")
    if not preserved:
        raise RuntimeError("检测到构建期间数据库或设置发生变化，请查看 deployment.json")
    print(json.dumps({key: value for key, value in deployment.items() if not key.startswith("user_files_") or key == "user_files_preserved"}, indent=2))


if __name__ == "__main__":
    main()
