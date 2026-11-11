"""Conservative, offline interpretation of CLC compound numbers.

Only complete, known components and explicitly sourced attachment rules are
resolved. A synthesized meaning is never represented as a printed main-table
entry, and the stored accession/classification number is never rewritten.
"""
from functools import lru_cache
import gzip
import json
from pathlib import Path
import re
import sys
import unicodedata


def resource_directory():
    root = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    return root / 'resources/classification'


@lru_cache(maxsize=1)
def supplement():
    with gzip.open(resource_directory() / 'supplement.json.gz', 'rt', encoding='utf-8') as stream:
        return json.load(stream)


@lru_cache(maxsize=8)
def auxiliary_index(table):
    return {row['code']: row for row in supplement()['tables'][table]['entries']}


def active_auxiliary(table, code):
    row = auxiliary_index(table).get(code)
    return row if row and row['status'] in ('active', 'historical') else None


def interpretation_code(code):
    # Interpret common typographic equivalents without changing saved values.
    return unicodedata.normalize('NFKC', code).translate(str.maketrans({
        '“': '"', '”': '"', '〈': '<', '〉': '>', '−': '-', '–': '-'}))


def append_digits(base, suffix):
    match = re.fullmatch(r'([A-Z]+)([0-9.]*)', base)
    if not match:
        return ''
    digits = (match[2] + suffix).replace('.', '')
    return match[1] + '.'.join(digits[i:i+3] for i in range(0, len(digits), 3))


def _blocked(index, code):
    # Alternate and withdrawn branches must not be silently reactivated by a
    # compound interpretation. Exact user-imported entries are handled first.
    for end in range(len(code), 0, -1):
        stem = code[:end]
        if stem in index:
            return False  # An explicitly retained descendant can borrow an old prefix.
        if '[' + stem + ']' in index or '{' + stem + '}' in index:
            return True
        if any(stem == row['code'] for row in supplement()['withdrawn']):
            return True
    return False


def _component(table, row):
    return {'table': table, 'table_name': supplement()['tables'][table]['name'],
            'code': row['code'], 'name': row['name'], 'status': row['status'],
            'source_id': 'clc-v5-auxiliary', 'pdf_page': row['pdf_page']}


def _result(index, code, base, name, components, *, china=False, rule=''):
    parent = base
    while parent and parent not in index:
        parent = parent[:-1]
    return {'code': code, 'name': name, 'parent_code': parent, 'base_code': base,
            'components': components, 'source': '中国图书馆分类法第5版 · 复分组合',
            'edition': '第5版（2010）', 'original_code': '', 'china': china, 'rule': rule}


def _chinese_context(index, term):
    if term.get('china'):
        return True
    seen = set()
    while term and term['code'] not in seen:
        seen.add(term['code'])
        if term['name'].startswith('中国') or term['code'] in ('I2', 'K2'):
            return True
        term = index.get(term.get('parent_code', ''))
    return False


# Main table pp. 174-175; each entry is a local subdivision, not a universal
# suffix. Period subdivisions explicitly imitate the 09 column in this table.
LITERATURE = {
    '06': '评论和研究', '062': '古代文学评论和研究', '063': '中世纪文学评论和研究',
    '064': '近代文学评论和研究', '065': '现代文学评论和研究', '07': '各体文学的评论和研究',
    '072': '诗歌评论和研究', '073': '戏剧文学评论和研究', '074': '小说评论和研究',
    '075': '报告文学评论和研究', '076': '散文、杂著评论和研究', '077': '民间文学评论和研究',
    '078': '儿童文学评论和研究', '079': '少数民族文学评论和研究',
    '0799': '宗教文学评论和研究', '07999': '其他文学评论和研究',
    '09': '文学史、文学思想史', '092': '古代文学史', '093': '中世纪文学史',
    '094': '近代文学史', '095': '现代文学史', '099': '文学流派及其研究',
    '1': '作品集', '11': '作品综合集', '2': '诗歌', '3': '戏剧文学', '4': '小说',
    '5': '报告文学', '6': '散文、杂著', '7': '民间文学', '72': '歌谣',
    '73': '故事、传说、神话', '74': '寓言', '77': '谚语', '78': '谜语、笑话',
    '8': '儿童文学', '82': '儿童诗歌、童谣', '83': '儿童戏剧、歌舞剧', '84': '儿童小说',
    '85': '儿童故事', '859': '儿童报告文学', '86': '儿童散文', '88': '童话、寓言',
    '9': '少数民族文学', '99': '宗教文学',
}
for _genre in '123456':
    for _era, _label in (('2', '古代'), ('3', '中世纪'), ('4', '近代'), ('5', '现代')):
        LITERATURE[_genre + _era] = _label + LITERATURE[_genre]
for _era in '45':
    for _topic, _label in enumerate(('革命斗争小说', '军事小说', '史传小说', '经济、政治小说',
                                     '科学小说、科幻小说', '惊险小说、推理小说', '社会、言情小说',
                                     '武侠小说', '其他题材小说'), 1):
        LITERATURE['4' + _era + str(_topic)] = ('近代' if _era == '4' else '现代') + _label


def _direct_term(index, code):
    # The range I3/7 authorizes world-area subdivision and the literature table.
    # A country's code is exact; broad areas require padding before a genre.
    if code.startswith('I') and 'I' in index:
        matches = []
        for region in auxiliary_index('fb2').values():
            rcode = region['code']
            if region['status'] != 'active' or not rcode[0] in '34567':
                continue
            base = 'I' + rcode
            components = [_component('fb2', region)]
            if code == base:
                matches.append(_result(index, code, 'I', region['name'] + '文学', components,
                                       rule='I3/7（原书第174-175页）'))
                continue
            padded = base if len(rcode.replace('.', '')) >= 3 else base + '0' * (3-len(rcode))
            digits, prefix = code[1:].replace('.', ''), padded[1:].replace('.', '')
            suffix = digits[len(prefix):] if digits.startswith(prefix) else ''
            if suffix in LITERATURE and append_digits(padded, suffix) == code:
                name = LITERATURE[suffix]
                matches.append(_result(index, code, base, region['name'] + ' · ' + name,
                    components + [{'table': 'literature', 'code': suffix, 'name': name,
                                   'source_id': 'clc-v5-auxiliary', 'pdf_page': 196}],
                    rule='I3/7（原书第174-175页）'))
        if len(matches) == 1:
            return matches[0]
    # Explicitly documented direct attachments only. Never split arbitrary
    # numerical tails just because they resemble a region or a dynasty.
    rules = [('J905', 'fb2'), ('G759', 'fb2'), ('F512.7', 'fb3'),
             ('I209.9', 'fb3'), ('I277.2', 'fb3'), ('K928.6', 'fb5'),
             ('I222.7', 'fb5'), ('C955', 'fb6'), ('R79', 'fb6'),
             ('I207.9', 'fb7'), ('I29', 'fb7'), ('I277.29', 'fb7')]
    matches = []
    for base, table in rules:
        if base not in index or not code.startswith(base):
            continue
        for row in auxiliary_index(table).values():
            if row['status'] not in ('active', 'historical'):
                continue
            if append_digits(base, row['code']) == code:
                matches.append(_result(index, code, base, index[base]['name'] + ' · ' + row['name'],
                    [_component(table, row)], china=table in ('fb3', 'fb5', 'fb7'),
                    rule=base + ' · ' + supplement()['tables'][table]['name']))
    return matches[0] if len(matches) == 1 else None


def _base_term(index, code):
    if code in index:
        return index[code]
    if _blocked(index, code):
        return None
    direct = _direct_term(index, code)
    if direct:
        return direct
    # Exactly one general subdivision, with no unconsumed numerical tail.
    match = re.fullmatch(r'(.+?)(-\d+(?:\.\d+)*)', code)
    if not match:
        return None
    base, suffix = match.groups()
    if '-' in base:
        return None
    term = index.get(base) or _direct_term(index, base)
    row = active_auxiliary('fb1', suffix)
    if not term or not row or _blocked(index, base):
        return None
    # These dedicated main-table exceptions are explicitly documented by the
    # fifth-edition training notes (pp. 38-41). Do not synthesize duplicate forms.
    if base in ('C', 'N') or (base.startswith('H19') and suffix.startswith('-4')):
        return None
    if base in ('B3', 'B4') and suffix == '-09':
        return None
    return _result(index, code, base, term['name'] + ' · ' + row['name'],
                   list(term.get('components', [])) + [_component('fb1', row)],
                   china=_chinese_context(index, term), rule='总论复分表')


TOKEN = re.compile(r'\((\d+(?:\.\d+)*)\)|"(\d+(?:\.\d+)*)"|=(\d+(?:\.\d+)*)|<(\d+(?:\.\d+)*)>')


def compose_term(index, raw_code):
    code = interpretation_code(raw_code)
    if len(code) > 80 or _blocked(index, code):
        return None
    split = re.search(r'[\(="<]', code)
    if not split:
        return _base_term(index, code) if code not in index else None
    base, rest = code[:split.start()], code[split.start():]
    term = _base_term(index, base)
    if not term:
        return None
    components, names = list(term.get('components', [])), [term['name']]
    china, position, previous = _chinese_context(index, term), 0, -1
    while position < len(rest):
        match = TOKEN.match(rest, position)
        if not match:
            return None
        kind = next(i for i, value in enumerate(match.groups()) if value is not None)
        if kind <= previous:  # Reject duplicate or reordered facets.
            return None
        previous, position = kind, match.end()
        number = match[kind + 1]
        if kind == 0:
            china = number.startswith('2')
            table, value = ('fb3', number[1:]) if china and len(number) > 1 else ('fb2', number)
        elif kind == 1:
            table, value = ('fb7', number[1:]) if number.startswith('2') and len(number) > 1 else ('fb6', number)
        elif kind == 2:
            table, value = ('fb5' if china else 'fb4'), number
        else:
            table, value = 'fb8', number
        row = active_auxiliary(table, value)
        if not row:
            return None
        components.append(_component(table, row))
        names.append(row['name'])
    return _result(index, raw_code, base, ' · '.join(names), components, china=china,
                   rule='通用复分表（原书第991-1014页）')
