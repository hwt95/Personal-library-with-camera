from pathlib import Path
import pytest

from scripts.build import deploy_program


@pytest.fixture(autouse=True)
def isolated_project_root(tmp_path, monkeypatch):
    # Exercise deployment inside a temporary project, regardless of OS temp drive.
    import scripts.build as build
    monkeypatch.setattr(build, "ROOT", tmp_path)


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
    (old / "sources").mkdir()
    (old / "sources/qt-source.tar.xz").write_bytes(b"old source archive")
    deploy_program(stage, old, backup)
    assert (old / "PersonalLibrary.exe").read_bytes() == b"new"
    assert (old / "_internal/runtime.dll").read_bytes() == b"new"
    assert (backup / "PersonalLibrary.exe").read_bytes() == b"old"
    assert (old / "library.sqlite3").read_bytes() == b"valuable database"
    assert (old / "settings.json").read_bytes() == b"private settings"
    assert (old / "my-note.txt").read_bytes() == b"personal file"
    assert not (old / "sources").exists()
    assert (backup / "sources/qt-source.tar.xz").read_bytes() == b"old source archive"


def test_failed_update_restores_previous_program(tmp_path, monkeypatch):
    import scripts.build as build
    stage, old, backup = (tmp_path / name for name in ("stage", "old", "backup"))
    stage.mkdir()
    old.mkdir()
    for folder, version in ((stage, b"new"), (old, b"old")):
        (folder / "PersonalLibrary.exe").write_bytes(version)
    (old / "library.sqlite3").write_bytes(b"valuable database")
    (old / "sources").mkdir()
    (old / "sources/qt-source.tar.xz").write_bytes(b"old source archive")
    def fail(*args, **kwargs):
        raise OSError("write failure")
    monkeypatch.setattr(build.shutil, "copy2", fail)
    with pytest.raises(OSError):
        deploy_program(stage, old, backup)
    assert (old / "PersonalLibrary.exe").read_bytes() == b"old"
    assert (old / "library.sqlite3").read_bytes() == b"valuable database"
    assert (old / "sources/qt-source.tar.xz").read_bytes() == b"old source archive"


def test_retiring_sources_refuses_to_move_personal_database(tmp_path):
    stage, old, backup = (tmp_path / name for name in ("stage", "old", "backup"))
    stage.mkdir()
    (stage / "PersonalLibrary.exe").write_bytes(b"new")
    (old / "sources").mkdir(parents=True)
    (old / "sources/library.sqlite3").write_bytes(b"personal data")
    with pytest.raises(ValueError, match="个人数据"):
        deploy_program(stage, old, backup)
    assert (old / "sources/library.sqlite3").read_bytes() == b"personal data"
    assert not backup.exists()
