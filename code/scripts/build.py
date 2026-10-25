"""Build a portable Windows folder and ZIP; never reads personal databases/settings."""
from pathlib import Path
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
             "THIRD_PARTY.json", "THIRD_PARTY_NOTICES.txt"}
    items = list(staged.iterdir())
    if {p.name for p in items} - names:
        raise ValueError("暂存目录中有非程序文件，已取消发布")
    # A database inside a managed runtime directory is unusual but still user data.
    for name in ("_internal", "licenses"):
        folder = destination / name
        if folder.exists() and any(p.suffix.lower() in (".sqlite3", ".sqlite", ".db") or p.name == "settings.json"
                                   for p in folder.rglob("*") if p.is_file()):
            raise ValueError(f"{folder} 内含个人数据，无法整体更新该运行库目录")
    destination.mkdir(parents=True, exist_ok=True)
    backup.mkdir(parents=True, exist_ok=False)
    moved, copied = [], []
    try:
        for item in items:
            target = destination / item.name
            if target.exists():
                target.resolve().relative_to(destination.resolve())
                saved = backup / item.name
                saved.resolve().relative_to(backup.resolve())
                target.rename(saved)
                moved.append(item.name)
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
    if sys.platform != "win32":
        raise SystemExit("Windows 便携包请在 Windows x64 上构建")
    subprocess.run([sys.executable, str(Path(__file__).with_name("prepare_assets.py"))], check=True)
    WORK.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)
    STAGED_DIST.mkdir(parents=True, exist_ok=True)
    PACKAGE.resolve().relative_to((ROOT / "build").resolve())
    if PACKAGE.exists() and (list(PACKAGE.rglob("*.sqlite3")) or (PACKAGE / "settings.json").exists()):
        raise SystemExit("临时构建目录存在个人数据，已取消构建")
    version_file = write_version_info(WORK / "version-info.txt")
    from PySide6 import __file__ as qt_file
    qt_root = Path(qt_file).parent
    # Collect the C/C++ runtime shipped in Qt's Windows wheel, not the system's DLLs.
    runtime = [p for p in qt_root.glob("*140*.dll") if p.name.startswith(("msvcp", "vcruntime", "concrt", "vcomp"))]
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed", "--noupx",
            "--name", "PersonalLibrary", "--distpath", str(STAGED_DIST), "--workpath", str(WORK / "pyinstaller"),
            "--specpath", str(WORK), "--paths", str(ROOT / "code"),
            "--version-file", str(version_file),
            "--add-data", f"{RESOURCES};resources",
            "--add-data", f"{ROOT / 'deps' / 'models' / 'document_orientation'};models/document_orientation",
            "--collect-binaries", "onnxruntime", "--collect-data", "certifi",
            "--hidden-import", "numpy", "--hidden-import", "zxingcpp",
            "--exclude-module", "pytest", "--exclude-module", "tkinter", "--exclude-module", "PySide6.QtQml",
            "--exclude-module", "PySide6.QtQuick", "--exclude-module", "PySide6.QtOpenGLWidgets"]
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
    shutil.copytree(RESOURCES / "fonts", licenses / "font", dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.otf", "*.ttf"))
    font_sources = json.loads((RESOURCES / "fonts" / "SOURCE.json").read_text(encoding="utf-8"))
    for name, license_file in (("LXGW WenKai TC", "WenKai-OFL.txt"), ("Noto Sans SC", "NotoSansSC-OFL.txt")):
        source = next(item for item in font_sources if item["file"] == license_file)
        inventory.append({"name": name, "version": source["commit"], "license": "OFL-1.1",
                          "files": ["licenses/font/" + license_file], "source": source["url"]})
    (PACKAGE / "THIRD_PARTY.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    (PACKAGE / "THIRD_PARTY_NOTICES.txt").write_text(
        "Personal Library third-party notices\n\n"
        "Qt / PySide6 / Shiboken are dynamically linked under their applicable LGPLv3/GPL/commercial terms. "
        "This distribution uses the open-source LGPL components. License texts accompany this package. "
        "Users may replace compatible Qt/PySide libraries and debug modifications as permitted by the LGPL. "
        "Upstream source: https://code.qt.io/cgit/pyside/pyside-setup.git/ and https://code.qt.io/cgit/qt/ .\n\n"
        "OpenCV, NumPy, ZXing, ONNX Runtime and other dependencies retain their original licenses; see licenses/ and THIRD_PARTY.json.\n"
        "LXGW WenKai TC and Noto Sans SC are from Google Fonts under SIL Open Font License 1.1. "
        "The original, unmodified fonts may be used commercially and bundled with software; "
        "see licenses/font/WenKai-OFL.txt, NotoSansSC-OFL.txt and SOURCE.json for the full terms and pinned sources.\n"
        "PP-LCNet_x1_0_doc_ori: PaddlePaddle model, Apache-2.0; exact revision and checksums in licenses/document_orientation/SOURCE.json.\n"
        "NLC parsing is independently implemented against catalog HTML; no calibre plugin code is included.\n", encoding="utf-8")
    # Packaging uses only the staging tree; an existing library or API key cannot leak into the ZIP.
    archive = shutil.make_archive(str(DIST / "PersonalLibrary-Windows-x64"), "zip", STAGED_DIST, PACKAGE.name)
    archive_path = Path(archive)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    archive_path.with_suffix(".zip.sha256").write_text(f"{digest}  {archive_path.name}\n", encoding="ascii")
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
