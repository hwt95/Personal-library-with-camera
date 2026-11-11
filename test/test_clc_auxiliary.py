import gzip
import hashlib
import json
from pathlib import Path
import sys

import pytest

from app.classification_auxiliary import auxiliary_index, supplement
from app.classification_dictionary import bundled_clc, find_term, term_description
from app.database import LibraryDB
from app.dialogs import ClassificationDialog
from app.i18n import set_language
from test_ui import application, window

ROOT = Path(__file__).resolve().parents[1]


def test_sources_integrity_and_original_dictionary_are_preserved():
    folder = ROOT / 'code/resources/classification'
    manifest = json.loads((folder / 'SUPPLEMENT_SOURCE.json').read_text('utf-8'))
    assert hashlib.sha256((folder / 'supplement.json.gz').read_bytes()).hexdigest() == manifest['payload_sha256']
    assert hashlib.sha256((folder / 'clc.json.gz').read_bytes()).hexdigest() == manifest['base_sha256']
    index = bundled_clc()
    assert len(index) == manifest['total_entries']
    for code, name, parent, original in json.loads(gzip.decompress((folder / 'clc.json.gz').read_bytes())):
        assert (index[code]['name'], index[code]['parent_code'], index[code]['original_code']) == (name, parent, original)
    for item in index.values():
        seen = {item['code']}
        parent = item['parent_code']
        while parent:
            assert parent in index and parent not in seen
            seen.add(parent)
            parent = index[parent]['parent_code']
    assert set(manifest['unavailable_wikipedia_subpages']) == {'O4', 'TF', 'TN', 'TS'}
    assert index['I516.4']['source_id'] == 'wiki-I-earlier'
    assert index['I313.74']['name'] == '寓言'
    assert index['I313.74']['verification']['original_name'] == '语言'
    assert index['I421.9']['name'] == '厄立特里亚'
    assert index['I421.9']['verification']['original_code'] == 'I421.99'
    assert 'I421.99' not in index


def test_all_eight_tables_and_fifth_edition_changes():
    assert [len(auxiliary_index('fb' + str(i))) for i in range(1, 9)] == [74, 394, 49, 33, 57, 440, 59, 105]
    for key, table in supplement()['tables'].items():
        assert table['edition'] == '第5版（2010）'
        rows = auxiliary_index(key)
        assert len(rows) == len(table['entries'])
        assert all(not r['parent_code'] or r['parent_code'] in rows for r in rows.values())
        assert all(1012 <= r['pdf_page'] <= 1035 for r in rows.values())
    assert auxiliary_index('fb2')['613']['name'] == '巴布亚新几内亚'
    assert auxiliary_index('fb2')['421.9']['name'] == '厄立特里亚'
    assert auxiliary_index('fb5')['26']['name'].startswith('战国')
    assert auxiliary_index('fb5')['{31}']['status'] == 'withdrawn'
    assert auxiliary_index('fb4')['{44}']['status'] == 'withdrawn'
    assert auxiliary_index('fb8')['712']['name'] == '女人'
    assert auxiliary_index('fb8')['51']['name'] == '物理环境'
    assert auxiliary_index('fb6')['[371.5]']['name'] == '黎巴嫩人'
    assert auxiliary_index('fb6')['[371.8]']['name'] == '科威特人'


@pytest.mark.parametrize('code,expected', [
    ('B-61', ['哲学', '词典']), ('TP303-43', ['总体结构', '教材']),
    ('TP24(313)', ['机器人', '日本']), ('U231(251)', ['地下铁路', '上海']),
    ('G254.12(2)=6', ['分类表', '中国', '1912']),
    ('G649.21=75', ['教育改革', '1990']),
    ('TS959.2“216”', ['竹', '苗族']), ('P457.3〈114〉', ['温度', '冬']),
    ('TP24<51>', ['机器人', '物理环境']), ('B844<788>', ['心理', '军人']),
    ('I712.45', ['美国', '现代小说']), ('I561.44', ['英国', '近代小说']),
    ('I500.45', ['欧洲', '现代小说']), ('I511.345', ['乌克兰', '现代小说']),
    ('I712.45-43', ['美国', '小说', '教材']),
    ('C955.712.1', ['民族心理', '美利坚人']), ('R793.51', ['医学', '印度民族']),
    ('I291.2', ['文学', '蒙古族']), ('I277.291.1', ['歌谣', '汉族']),
    ('K928.626', ['历史地理', '战国']),
    ('ＴＰ２４（３１３）', ['机器人', '日本']),
])
def test_compound_meanings_are_explicitly_distinguished(code, expected):
    set_language('zh_CN')
    index = bundled_clc()
    result, kind = find_term(index, code)
    assert kind == 'composed', (code, kind)
    assert all(word in result['name'] for word in expected), result
    assert '复分组合' in term_description(index, code)
    assert result['components']


@pytest.mark.parametrize('code', [
    'TP303-43-61', 'TP303-08', 'TP303-123456', 'TP303(999)', 'TP303(313)junk',
    'TP303(313)(712)', 'TP303=44', 'TP303<999>', 'TP303<114><788>',
    'G254.12(2)=31', 'G254.12=75', 'B019.13-43', 'I234.7-43', 'C-61', 'N-54',
    'H19-43', 'B3-09', 'I712.99999', 'I71245', 'I999.45',
    'TP303=5(313)', 'TP303()', 'TP303"999"', 'TP303<114',
])
def test_unknown_deleted_and_invalid_combinations_are_not_claimed_known(code):
    assert find_term(bundled_clc(), code)[1] != 'composed'


def test_main_table_and_user_dictionary_take_precedence_without_writing_books(tmp_path):
    db = LibraryDB(tmp_path / 'library.sqlite3', create=True)
    try:
        code = 'I712.45'
        db.save({'title': 'Original', 'clc_code': code}, 2020)
        before = list(db.conn.iterdump())
        assert '美国' in db.classification_description(code)
        assert db.code_for(1) == 'I712.45-001'
        assert list(db.conn.iterdump()) == before
        assert find_term(db.classification_index(), 'TH-39')[1] == 'exact'
        custom = tmp_path / 'terms.csv'
        custom.write_text('code,name,parent_code,source,edition\nI712.45,Local meaning,I,User,1\n', 'utf-8')
        db.import_classifications(custom)
        result, kind = find_term(db.classification_index(), code)
        assert kind == 'exact' and result['name'] == 'Local meaning'
        assert db.code_for(1) == 'I712.45-001'
    finally:
        db.close()


def test_auxiliary_browsing_cannot_select_a_standalone_number(window, application):
    dialog = ClassificationDialog(window.db, window)
    a = dialog.tree.topLevelItem(0)
    dialog.expand_node(a)
    a2 = next(a.child(i) for i in range(a.childCount()) if a.child(i).text(0) == 'A2')
    dialog.expand_node(a2)
    codes = [a2.child(i).text(0) for i in range(a2.childCount())]
    assert codes.index('A23') < codes.index('A24') < codes.index('A25')
    dialog.table_selector.setCurrentIndex(dialog.table_selector.findData('fb2'))
    dialog.query.setText('712')
    dialog.populate()
    result = dialog.tree.topLevelItem(0)
    assert result.text(1) == '美国'
    assert '998' in result.toolTip(1)
    dialog.tree.setCurrentItem(result)
    dialog.choose()
    assert dialog.selected_code == '' and not dialog.choose_button.isEnabled()
    dialog.table_selector.setCurrentIndex(0)
    assert [r['code'] for r in dialog.children_by_parent['A2']] == codes
    dialog.query.setText('I712.45')
    dialog.populate()
    assert dialog.tree.topLevelItem(0).text(0) == 'I712.45'
    assert '复分组合' in dialog.tree.topLevelItem(0).toolTip(1)
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
    dialog.choose()
    assert dialog.selected_code == 'I712.45'
    dialog.close()


def test_archive_parser_and_withdrawn_prefix_reuse(tmp_path):
    sys.path.insert(0, str(ROOT / 'code/scripts'))
    from prepare_clc_supplement import wiki_entries, blocked_branch
    path = tmp_path / 'article.html'
    path.write_text('''<div class="mw-parser-output"><img></div>
      <div class="mw-parser-output"><ul><li>I247.57 社会、<a>言情小说</a> （生活题材入此。）
      <ul><li>I247.571 子类</li></ul></li><li>I234.7 当代（第四版删除）</li></ul></div>''', 'utf-8')
    rows = wiki_entries(path)
    assert rows[0]['name'] == '社会、言情小说'
    assert rows[-1]['withdrawn']
    assert not blocked_branch('G649.219', {'G64': []}, {'G6'})
    assert blocked_branch('G619', {'G': []}, {'G6'})


def test_removed_source_code_stays_historical():
    index = bundled_clc()
    assert 'I234.7' not in index
    assert find_term(index, 'I234.7')[1] == 'withdrawn'
    assert find_term(index, 'B019.13')[1] == 'marked'
