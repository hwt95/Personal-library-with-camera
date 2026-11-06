import asyncio
import csv
import json
from pathlib import Path
import sqlite3

import httpx
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox, QLabel, QAbstractButton, QGroupBox, QWidget

from app.classification import resolve_system, metadata_codes, normalize_code
from app.database import LibraryDB, SCHEMA_VERSION
from app.dialogs import ClassificationPolicyDialog
from app.i18n import LANGUAGES, set_language, catalogs
from app.providers.catalog import lookup
from test_ui import application, window


@pytest.fixture
def db(tmp_path):
    result = LibraryDB(tmp_path / 'library.sqlite3', create=True)
    yield result
    result.close()


def populate(db):
    db.save({'title': 'Known code', 'clc_code': 'TP303', 'ddc_code': '005.133',
             'lcc_code': 'QA76.73.P98', 'cover_blob': b'cover'}, 2020, 'Shelf', 'Note')
    db.save(db.book(1), 2021, book_id=1, reading_status='read')
    db.save({'title': 'No target', 'clc_code': 'I247.57'}, 2001)
    db.save({'title': 'Journal', 'media_type': 'magazine', 'issue_year': 2026,
             'issue_number': '03'}, 2026)


@pytest.mark.parametrize('language', LANGUAGES)
def test_auto_default_does_not_migrate_existing_titles(db, language):
    db.save({'title': 'Old', 'clc_code': 'TP303'}, 2020)
    db.language = language
    system = resolve_system('auto', language)
    assert system == ('clc' if language == 'zh_CN' else 'ddc')
    _, code = db.save({'title': 'New', 'clc_code': 'TP303', 'ddc_code': '005.133'}, 2021)
    assert code == ('TP303-002' if system == 'clc' else 'DDC-005.133-001')
    assert db.code_for(1) == 'TP303-001'


def test_policy_is_per_database_and_new_copies_keep_existing_system(db):
    populate(db)
    before = db.search()
    db.set_classification_policy('lcc', 'en')
    assert db.search() == before
    assert db.save({'title': 'Known code'}, 2022, book_id=1)[1] == 'TP303-001(2)'
    assert db.save({'title': 'New LCC', 'lcc_code': 'QA76.73.P98'}, 2026)[1] == 'LCC-QA76.73.P98-001'
    reopened = LibraryDB(db.path, language='zh_CN')
    assert reopened.classification_mode == 'lcc'
    assert reopened.save({'title': 'New unclassified'}, 2026)[1] == 'LCC-PENDING-001'
    reopened.close()


def test_sync_backs_up_preserves_fields_and_copy_suffixes_and_exports_history(db, tmp_path):
    populate(db)
    before = list(db.conn.iterdump())
    plan = db.classification_plan('ddc')
    assert [(p['new_code'], p['pending']) for p in plan] == [('DDC-005.133-001', False), ('DDC-PENDING-001', True)]
    result = db.set_classification_policy('ddc', 'en', synchronize=True)
    assert result['books'] == 2 and result['copies'] == 3
    with sqlite3.connect(result['backup']) as original:
        assert list(original.iterdump()) == before
    assert {r['code'] for r in db.search()} == {'DDC-005.133-001', 'DDC-005.133-001(1)', 'DDC-PENDING-001', 'MAG-001'}
    assert db.book(1)['clc_code'] == 'TP303' and db.book(1)['cover_blob'] == b'cover'
    assert db.copy(1)['location'] == 'Shelf' and db.copy(1)['notes'] == 'Note'
    assert db.copy(2)['reading_status'] == 'read'
    assert db.search(flags=['pending'])[0]['title'] == 'No target'
    assert len(db.search(fields={'clc_code': '005'})) == 2
    history = tmp_path / 'history.csv'
    db.export_numbering_history(history)
    rows = list(csv.DictReader(history.open(encoding='utf-8-sig')))
    assert len(rows) == 3
    assert (rows[1]['old_code'], rows[1]['new_code']) == ('TP303-001(1)', 'DDC-005.133-001(1)')
    db.set_classification_policy('clc', 'en', synchronize=True)
    assert db.code_for(1) == 'TP303-002'
    db.set_classification_policy('ddc', 'en', synchronize=True)
    assert db.code_for(1) == 'DDC-005.133-002'
    assert db.classification_plan('ddc') == []


@pytest.mark.parametrize('failure', ['backup', 'mid_transaction'])
def test_failed_sync_leaves_policy_records_counters_and_history_intact(db, monkeypatch, failure):
    populate(db)
    before = list(db.conn.iterdump())
    if failure == 'backup':
        def fail(*args):
            raise OSError('disk full')
        monkeypatch.setattr(db, 'backup', fail)
    else:
        allocate = db._allocate_number
        def fail(scope):
            value = allocate(scope)
            if scope.endswith('PENDING'):
                raise OSError('simulated write failure')
            return value
        monkeypatch.setattr(db, '_allocate_number', fail)
    with pytest.raises(OSError):
        db.set_classification_policy('ddc', 'en', synchronize=True)
    assert list(db.conn.iterdump()) == before


def version2_fixture(path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(LibraryDB, '_upgrade_classification', lambda self, **kwargs: None)
        old = LibraryDB(path, create=True)
    with old.conn:
        old.conn.execute("INSERT INTO books(title,clc_code,cover_blob,number_scope,class_no,next_copy_no) VALUES('Existing','TP303',X'1234','TP303',19,2)")
        old.conn.execute("INSERT INTO copies(book_id,copy_no,publication_year,location,reading_status) VALUES(1,1,2020,'Shelf','reading')")
        old.conn.execute("INSERT INTO class_counters VALUES('TP303',22)")
    dump = list(old.conn.iterdump())
    old.close()
    return dump


def test_v11_database_upgrade_preserves_numbers_and_preupgrade_backup(tmp_path, monkeypatch):
    path = tmp_path / 'v11.sqlite3'
    original = version2_fixture(path, monkeypatch)
    upgraded = LibraryDB(path, language='en')
    try:
        assert upgraded.conn.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION == 3
        assert upgraded.search()[0]['code'] == 'TP303-019(1)'
        assert upgraded.book(1)['classification_system'] == 'clc'
        assert upgraded.book(1)['cover_blob'] == bytes.fromhex('1234')
        assert upgraded.copy(1)['reading_status'] == 'reading'
        assert upgraded.save({'title': 'Later', 'classification_system': 'clc', 'clc_code': 'TP303'}, 2026)[1] == 'TP303-022'
        with sqlite3.connect(upgraded.migration_backup) as backup:
            assert backup.execute('PRAGMA user_version').fetchone()[0] == 2
            assert list(backup.iterdump()) == original
    finally:
        upgraded.close()


def test_v11_backup_failure_does_not_upgrade_database(tmp_path, monkeypatch):
    path = tmp_path / 'v11.sqlite3'
    version2_fixture(path, monkeypatch)
    before = path.read_bytes()
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(LibraryDB, 'backup', fail)
    with pytest.raises(OSError):
        LibraryDB(path)
    assert path.read_bytes() == before


def test_dictionary_import_and_exports_are_system_specific(db, tmp_path):
    dictionary = tmp_path / 'dictionary.csv'
    dictionary.write_text('code,name,parent_code,source,edition\n005.133,My local term,,,User\n', 'utf-8')
    db.import_classifications(dictionary, system='ddc')
    assert db.classifications(system='ddc')[0]['code'] == '005.133'
    assert db.classifications(system='lcc') == [] and len(db.classifications()) == 22
    populate(db)
    db.set_classification_policy('ddc', 'en', synchronize=True)
    output = tmp_path / 'books.csv'
    db.export_csv(output, db.search())
    rows = list(csv.DictReader(output.open(encoding='utf-8-sig')))
    row = next(r for r in rows if r['title'] == 'Known code')
    assert (row['classification_system'], row['classification_code'], row['clc_code']) == ('ddc', '005.133', 'TP303')


def test_explicit_metadata_codes_only():
    assert normalize_code('ddc', "028/.9") == '028.9'
    assert normalize_code('lcc', 'Z1003  .M58 2009') == 'Z1003 .M58 2009'
    assert metadata_codes({'dewey_decimal_class': ['005.13', '005.133']})['ddc_code'] == ''
    assert metadata_codes({'classifications': {'lc_classifications': ['QA76.73.P98']}})['lcc_code'] == 'QA76.73.P98'
    for record in ({'categories': ['Computers']}, {'classifications': ['invalid'], 'dewey_decimal_class': 123}):
        assert metadata_codes(record) == {'ddc_code': '', 'lcc_code': ''}
    with pytest.raises(ValueError):
        normalize_code('ddc', 'TP303')
    with pytest.raises(ValueError):
        normalize_code('auto', '005.133')


def test_openlibrary_explicit_edition_and_work_classification():
    async def handler(request):
        if request.url.path.startswith('/isbn/'):
            return httpx.Response(200, json={'title': 'Example', 'dewey_decimal_class': ['005.133'],
                'works': [{'key': '/works/W1'}]})
        if request.url.path == '/works/W1.json':
            return httpx.Response(200, json={'lc_classifications': ['QA76.73.P98'], 'dewey_decimal_class': ['000'],
                'subjects': ['Not a classification number'], 'description': 'Description'})
        return httpx.Response(404)
    records, _ = asyncio.run(lookup('9780306406157', 'openlibrary', transport=httpx.MockTransport(handler)))
    assert (records[0]['clc_code'], records[0]['ddc_code'], records[0]['lcc_code']) == ('', '005.133', 'QA76.73.P98')


def test_language_changes_new_default_but_preserves_draft_and_existing_code(window):
    window.set_language('zh_CN')
    window.db.save({'title': 'Existing', 'clc_code': 'TP303'}, 2020)
    window.set_language('en')
    assert window.entry.active_system == 'ddc'
    assert window.db.code_for(1) == 'TP303-001'
    window.entry.set_text('title', 'Draft')
    window.entry.set_text('clc_code', '005.133')
    window.entry.mark_dirty('clc_code')
    window.set_language('zh_CN')
    assert window.entry.active_system == 'ddc' and window.entry.data()['ddc_code'] == '005.133'
    window.reset_entry()
    assert window.entry.active_system == 'clc'


def test_entry_can_switch_codes_without_losing_manual_values(window):
    window.set_language('zh_CN')
    entry = window.entry
    entry.fill({'clc_code': 'TP303', 'ddc_code': '005.133', 'lcc_code': 'QA76.73.P98'})
    entry.classification_system.setCurrentIndex(entry.classification_system.findData('ddc'))
    assert entry.text('clc_code') == '005.133'
    entry.set_text('clc_code', '005.12')
    entry.mark_dirty('clc_code')
    entry.classification_system.setCurrentIndex(entry.classification_system.findData('lcc'))
    entry.fill({'ddc_code': '000', 'lcc_code': 'QA76'})
    assert entry.data()['ddc_code'] == '005.12' and entry.data()['clc_code'] == 'TP303'
    entry.classification_system.setCurrentIndex(entry.classification_system.findData('ddc'))
    assert entry.text('clc_code') == '005.12'


def test_settings_cancel_default_only_and_sync_are_explicit(window, monkeypatch):
    populate(window.db)
    window.set_language('zh_CN')
    before = list(window.db.conn.iterdump())
    combo = window.settings_page.classification_mode
    combo.setCurrentIndex(combo.findData('ddc'))
    assert list(window.db.conn.iterdump()) == before
    monkeypatch.setattr(ClassificationPolicyDialog, 'exec', lambda self: QDialog.DialogCode.Rejected)
    window.apply_classification_policy()
    assert list(window.db.conn.iterdump()) == before
    monkeypatch.setattr(ClassificationPolicyDialog, 'exec', lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    window.apply_classification_policy()
    assert window.db.classification_mode == 'ddc' and window.db.code_for(1) == 'TP303-001'
    def synchronize(dialog):
        assert dialog.future_only.isChecked()
        dialog.synchronize.setChecked(True)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ClassificationPolicyDialog, 'exec', synchronize)
    monkeypatch.setattr(window, 'discard_draft', lambda: False)
    window.apply_classification_policy()
    assert window.db.code_for(1) == 'TP303-001'
    monkeypatch.setattr(window, 'discard_draft', lambda: True)
    window.apply_classification_policy()
    assert window.db.code_for(1) == 'DDC-005.133-001'


@pytest.mark.parametrize('language', [lang for lang in LANGUAGES if lang != 'zh_CN'])
def test_new_controls_have_complete_translations(window, language):
    window.set_language(language)
    dialog = ClassificationPolicyDialog('lcc', window.db.classification_plan('lcc'), window)
    for root in (dialog, window.settings_page.classification_mode.parentWidget()):
        for widget in root.findChildren(QWidget):
            if not isinstance(widget, (QLabel, QAbstractButton, QGroupBox)):
                continue
            text = widget.title() if isinstance(widget, QGroupBox) else widget.text()
            assert not any('\u4e00' <= c <= '\u9fff' for c in text), text
    dialog.deleteLater()
    window.set_language('zh_CN')
