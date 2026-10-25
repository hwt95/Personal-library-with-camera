"""Exercise the actual ZIP from a new Unicode path with a stripped environment."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument("--platform", choices=("offscreen", "windows"), default="offscreen")
parser.add_argument("--report", help="Report filename under test/reports")
args = parser.parse_args()
archive = ROOT / "build" / "dist" / "PersonalLibrary-Windows-x64.zip"
report_path = ROOT / "test" / "reports" / (args.report or ("portable-smoke.json" if args.platform == "offscreen" else "portable-native-smoke.json"))
report_path.resolve().relative_to((ROOT / "test" / "reports").resolve())
test_root = ROOT / "test" / "data"
test_root.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix="独立运行验证-", dir=test_root) as directory:
    directory = Path(directory).resolve()
    directory.relative_to(test_root.resolve())
    with zipfile.ZipFile(archive) as stream:
        assert not any(Path(name).name == "settings.json" or Path(name).suffix.lower() in (".sqlite3", ".db", ".sqlite") for name in stream.namelist()), "Release ZIP contains personal data"
        for name in stream.namelist():
            (directory / name).resolve().relative_to(directory)
        stream.extractall(directory)
    executable = directory / "PersonalLibrary" / "PersonalLibrary.exe"
    clean = {key: value for key, value in os.environ.items()
             if key.upper() not in ("PATH", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH")}
    windows = Path(os.environ.get("SystemRoot", "C:/Windows"))
    clean["PATH"] = str(windows / "System32") + os.pathsep + str(windows)
    clean["PYTHONHOME"] = str(directory / "不存在的Python")
    clean["PYTHONPATH"] = str(directory / "不存在的依赖")
    clean["QT_PLUGIN_PATH"] = str(directory / "不存在的Qt")
    clean["QT_QPA_PLATFORM_PLUGIN_PATH"] = clean["QT_PLUGIN_PATH"]
    report_path.unlink(missing_ok=True)
    result = subprocess.run([str(executable), "--self-test", str(report_path), "--self-test-platform", args.platform], cwd=directory, env=clean,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if not report_path.is_file():
        raise RuntimeError(f"Packaged process exited {result.returncode}: {result.stderr[:3000]!r}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["isolated_verification"] = {"zip_extracted_to_unicode_path": True, "path_contains_only_windows": True,
        "no_database_or_settings_in_zip": True,
        "pythonhome_points_to_missing_directory": True, "pythonpath_points_to_missing_directory": True,
        "qt_environment_points_to_missing_directory": True, "exit_code": result.returncode,
        "imports_only_from_portable_directory": all(Path(p).resolve().is_relative_to(executable.parent) for p in report["sys_path"]),
        "package_contains_python313_dll": (executable.parent / "_internal" / "python313.dll").is_file(),
        "package_contains_vcruntime": (executable.parent / "_internal" / "vcruntime140.dll").is_file(),
        "package_contains_msvcp": (executable.parent / "_internal" / "msvcp140.dll").is_file()}
    required = ("imports_only_from_portable_directory", "package_contains_python313_dll", "package_contains_vcruntime", "package_contains_msvcp")
    report["passed"] = report["passed"] and result.returncode == 0 and all(report["isolated_verification"][key] for key in required)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "exit_code": result.returncode}))
    if not report["passed"]:
        raise SystemExit(1)
