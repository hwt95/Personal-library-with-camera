from pathlib import Path
import pytest

from scripts.build import deploy_program


def test_update_keeps_library_settings_and_unmanaged_files(tmp_path):
    stage, old, backup = (tmp_path / name for name in ("stage", "old", "backup"))
    stage.mkdir()
    old.mkdir()
    for folder, version in ((stage, b"new"), (old, b"old")):
        (folder / "PersonalLibrary.exe").write_bytes(version)
        (folder / "_internal").mkdir()
        (folder / "_internal" / "runtime.dll").write_bytes(version)
    (old / "library.sqlite3").write_bytes(b"valuable database")
    (old / "settings.json").write_bytes(b"private settings")
    (old / "my-note.txt").write_bytes(b"personal file")
    deploy_program(stage, old, backup)
    assert (old / "PersonalLibrary.exe").read_bytes() == b"new"
    assert (old / "_internal/runtime.dll").read_bytes() == b"new"
    assert (backup / "PersonalLibrary.exe").read_bytes() == b"old"
    assert (old / "library.sqlite3").read_bytes() == b"valuable database"
    assert (old / "settings.json").read_bytes() == b"private settings"
    assert (old / "my-note.txt").read_bytes() == b"personal file"


def test_failed_update_restores_previous_program(tmp_path, monkeypatch):
    import scripts.build as build
    stage, old, backup = (tmp_path / name for name in ("stage", "old", "backup"))
    stage.mkdir()
    old.mkdir()
    for folder, version in ((stage, b"new"), (old, b"old")):
        (folder / "PersonalLibrary.exe").write_bytes(version)
    (old / "library.sqlite3").write_bytes(b"valuable database")
    def fail(*args, **kwargs):
        raise OSError("write failure")
    monkeypatch.setattr(build.shutil, "copy2", fail)
    with pytest.raises(OSError):
        deploy_program(stage, old, backup)
    assert (old / "PersonalLibrary.exe").read_bytes() == b"old"
    assert (old / "library.sqlite3").read_bytes() == b"valuable database"
