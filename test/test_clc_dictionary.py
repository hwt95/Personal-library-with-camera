import gzip
import hashlib
import json
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from app.classification_dictionary import bundled_clc, find_term
from app.classification_auxiliary import supplement
from app.dialogs import ClassificationDialog
from app.database import LibraryDB
from test_ui import application, window


def test_bundled_dictionary_integrity_and_verified_corrections():
    root = Path(__file__).resolve().parents[1] / 'code/resources/classification'
    manifest = json.loads((root / 'SOURCE.json').read_text('utf-8'))
    assert hashlib.sha256((root / 'clc.json.gz').read_bytes()).hexdigest() == manifest['payload_sha256']
    index = bundled_clc()
    assert len(index) == 45785 + len(supplement()['entries'])
    assert len([row for row in index.values() if not row['parent_code']]) == 22
    assert all(not row['parent_code'] or row['parent_code'] in index for row in index.values())
    for code, name in [('TS253.8', '蛋品加工厂'), ('S888.72+3', '桑木虱'), ('TH-39', '机电一体化'), ('E0-059', '其他')]:
        assert index[code]['name'] == name
    assert index['U653.923+.5']['name'] == '平板车'
    assert index['U653.923+.5']['original_code'] == 'U653.923.+5'
    assert 'U653.923.+5' not in index


def test_existing_library_uses_dictionary_without_database_writes(tmp_path):
    db = LibraryDB(tmp_path / 'old.sqlite3', create=True)
    db.save({'title': 'Stored book', 'clc_code': 'TP303'}, 2020)
    before = list(db.conn.iterdump())
    assert '总体结构、系统结构' in db.classification_description('TP303')
    assert 'TP3 计算技术、计算机技术' in db.classification_description('TP303')
    assert len(db.classifications()) == len(bundled_clc())
    assert db.code_for(1) == 'TP303-001'
    assert list(db.conn.iterdump()) == before
    db.close()


def test_exact_parent_marker_and_mistyped_code_are_distinct():
    index = bundled_clc()
    assert find_term(index, 'TP303')[1] == 'exact'
    assert find_term(index, 'I247.57')[1] == 'exact'
    parent, kind = find_term(index, 'I247.59999')
    assert kind == 'parent' and parent['code'] == 'I247.59'
    marked, kind = find_term(index, 'B019.13')
    assert kind == 'marked' and marked['code'] == '[B019.13]'
    assert find_term(index, 'U653.923.+5')[1] == 'corrected'
    assert find_term(index, 'UNKNOWN')[1] == 'missing'
    assert find_term(index, '005.133', 'ddc') == (None, 'missing')
    assert find_term({}, 'TP303')[1] == 'missing'


def test_custom_terms_override_bundled_and_refresh_cache(window, tmp_path):
    db = window.db
    assert '总体结构、系统结构' in db.classification_description('TP303')
    before = list(db.conn.execute('SELECT code,name FROM classifications'))
    csv = tmp_path / 'custom.csv'
    csv.write_text('code,name,parent_code,source,edition\nTP303,Custom label,TP,Local,1\n', 'utf-8')
    db.import_classifications(csv)
    assert db.classification_description('TP303').startswith('Custom label')
    assert bundled_clc()['TP303']['name'] == '总体结构、系统结构'
    assert db.classifications('Custom label')[0]['parent_code'] == 'TP'
    assert db.classifications('TP303', 'ddc') == []


def test_entry_and_both_catalog_pages_show_meaning_on_both_code_columns(window, application):
    window.set_language('zh_CN')
    window.entry.fields['clc_code'].setText('TP303')
    assert '总体结构、系统结构' in window.entry.clc_hint.text()
    assert window.entry.clc_hint.textFormat() == Qt.TextFormat.PlainText
    window.entry.fields['clc_code'].setText('I247.57')
    assert '社会、言情小说' in window.entry.clc_hint.text()
    window.db.save({'title': 'Example', 'clc_code': 'TP303'}, 2020)
    for page in (window.catalog, window.search_page):
        page.refresh()
        columns = dict((name, i) for i, (name, _) in enumerate(page.COLUMNS))
        for name in ('code', 'classification_code'):
            tooltip = page.table.item(0, columns[name]).toolTip()
            assert '总体结构、系统结构' in tooltip and 'TP3' in tooltip
    window.entry.classification_system.setCurrentIndex(window.entry.classification_system.findData('ddc'))
    window.entry.fields['clc_code'].setText('TP303')
    assert '总体结构、系统结构' not in window.entry.clc_hint.text()


def test_dictionary_browses_lazily_and_caps_search(window, application):
    dialog = ClassificationDialog(window.db, window)
    assert dialog.tree.topLevelItemCount() == 22
    item = next(dialog.tree.topLevelItem(i) for i in range(22) if dialog.tree.topLevelItem(i).text(0) == 'T')
    assert item.childCount() == 0
    item.setExpanded(True)
    application.processEvents()
    assert item.childCount() > 0
    dialog.query.setText('TP303')
    dialog.populate()
    assert dialog.tree.topLevelItem(0).text(1) == '总体结构、系统结构'
    dialog.query.setText('技术')
    dialog.populate()
    assert 0 < dialog.tree.topLevelItemCount() <= 300
    dialog.query.clear()
    dialog.populate()
    assert dialog.tree.topLevelItemCount() == 22
    dialog.close()


def test_dictionary_tooltips_escape_custom_html(window, tmp_path):
    csv = tmp_path / 'custom.csv'
    csv.write_text('code,name,parent_code,source,edition\nTP303,<b>Literal</b>,TP,Local,1\n', 'utf-8')
    window.db.import_classifications(csv)
    window.db.save({'title': 'Example', 'clc_code': 'TP303'}, 2020)
    window.catalog.refresh()
    assert '&lt;b&gt;Literal&lt;/b&gt;' in window.catalog.table.item(0, 0).toolTip()


def test_expanded_nested_codes_have_room_for_full_text(window, application):
    dialog = ClassificationDialog(window.db, window)
    dialog.show()
    application.processEvents()
    initial_width = dialog.tree.columnWidth(0)
    current = dialog.tree.invisibleRootItem()
    for code in ('B', 'B0', 'B01', 'B019.1'):
        current = next(current.child(i) for i in range(current.childCount()) if current.child(i).text(0) == code)
        current.setExpanded(True)
        application.processEvents()
        for i in range(current.childCount()):
            child = current.child(i)
            rect = dialog.tree.visualRect(dialog.tree.indexFromItem(child, 0))
            assert rect.width() >= dialog.tree.fontMetrics().horizontalAdvance(child.text(0)) + 4, child.text(0)
    assert any(current.child(i).text(0) == '[B019.13]' for i in range(current.childCount()))
    assert dialog.tree.columnWidth(0) > initial_width
    # Repainting at a larger user-selected font must also account for indentation.
    dialog.tree.setStyleSheet('font-size: 23px;')
    application.processEvents()
    for i in range(current.childCount()):
        child = current.child(i)
        rect = dialog.tree.visualRect(dialog.tree.indexFromItem(child, 0))
        assert rect.width() >= dialog.tree.fontMetrics().horizontalAdvance(child.text(0)) + 4, child.text(0)
    dialog.close()
