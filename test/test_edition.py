import asyncio
import builtins
import json

import pytest

from app import edition


@pytest.mark.parametrize("source", ["auto", "nlc", "openlibrary", "google"])
def test_offline_lookup_never_imports_network_adapter(monkeypatch, source):
    monkeypatch.setattr(edition, "OFFLINE", True)
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.endswith("providers.catalog"):
            raise AssertionError("Offline lookup tried to load a network adapter")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    records, note = asyncio.run(edition.lookup("9787111544937", source, "saved-api-key", "https://opac.nlc.cn/record"))
    assert not records and "离线" in note


def test_frozen_missing_or_invalid_descriptor_stays_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(edition.sys, "frozen", True, raising=False)
    monkeypatch.setattr(edition.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert edition._read_offline()
    for value in ("broken", "{}", '{"data_mode":"unknown"}'):
        (tmp_path / "distribution.json").write_text(value, "utf-8")
        assert edition._read_offline()
    (tmp_path / "distribution.json").write_text('{"data_mode":"online"}', "utf-8")
    assert not edition._read_offline()


def test_offline_ui_overrides_old_network_settings(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from app import pages
    from app.window import MainWindow
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(pages, "OFFLINE", True)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"source": "google", "google_key": "old-key"}), "utf-8")
    window = MainWindow(path, smoke=True)
    try:
        assert window.settings_page.source.count() == 1
        assert window.settings_page.source.currentData() == "manual"
        assert not window.settings_page.key.isEnabled()
        assert not window.settings_page.key.text()
        assert "离线版" in window.settings_page.about.text()
    finally:
        window.close()
        app.processEvents()
