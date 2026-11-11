"""Read-only bundled CLC data. Library-specific imported terms take precedence."""
from functools import lru_cache
import gzip
import json
import re
from pathlib import Path
import sys

from .i18n import tr
from .classification_auxiliary import supplement, compose_term, interpretation_code


@lru_cache(maxsize=1)
def bundled_clc():
    root = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    with gzip.open(root / 'resources/classification/clc.json.gz', 'rt', encoding='utf-8') as stream:
        rows = json.load(stream)
    index = {code: {'code': code, 'name': name, 'parent_code': parent,
                   'source': '用户提供的中图法 JSON（仅限非商业使用）', 'edition': '2026-11-07',
                   'original_code': original} for code, name, parent, original in rows}
    for row in supplement()['entries']:
        index.setdefault(row['code'], {**row, 'source': '中文维基百科贡献者 · 存档补充',
                                     'edition': '快照补充（2026-11-11）', 'original_code': ''})
    return index


def find_term(index, code, system='clc'):
    """Return a known term plus match kind; never present a prefix match as exact."""
    code = str(code or '').strip().upper()
    if system == 'clc':
        code = interpretation_code(code)
    if not code:
        return None, 'missing'
    if code in index:
        return index[code], 'exact'
    if system == 'clc':
        # Preserve the printed marker in the result. Do not silently assign that code.
        for marked in ('[' + code + ']', '{' + code + '}'):
            if marked in index:
                return index[marked], 'marked'
        # These five original spellings were checked against the supplied PDF.
        if code.startswith('U653.923.+'):
            corrected = code.replace('U653.923.+', 'U653.923+.')
            item = index.get(corrected)
            if item and item.get('original_code') == code:
                return item, 'corrected'
        for row in supplement()['withdrawn']:
            if row['code'] == code:
                return {**row, 'parent_code': code.rsplit('.', 1)[0]}, 'withdrawn'
        composed = compose_term(index, code)
        if composed:
            return composed, 'composed'
        letters = re.match(r'^[A-Z]+', code)
        if not letters or letters.group() not in index:
            return None, 'missing'
        for length in range(len(code) - 1, 0, -1):
            prefix = code[:length]
            if prefix in index and prefix[-1].isalnum() and not any(c in prefix for c in '/[{}]'):
                return index[prefix], 'parent'
    return None, 'missing'


def term_description(index, code, system='clc'):
    term, kind = find_term(index, code, system)
    if not term:
        return tr('名称未收录 · 此分类号仍可保存')
    if kind == 'parent':
        label = tr('未精确收录 {code}；上级参考：{parent} {name}', code=code, parent=term['code'], name=term['name'])
    elif kind == 'marked':
        label = tr('字典标记条目：{code} {name}；请核对适用性', code=term['code'], name=term['name'])
    elif kind == 'corrected':
        label = tr('原文件编码有误；参考更正：{code} {name}', code=term['code'], name=term['name'])
    elif kind == 'withdrawn':
        label = tr('已停用的分类号：{code} {name}；请核对新版类目', code=term['code'], name=term['name'])
    elif kind == 'composed':
        label = tr('复分组合（第5版）：{name}', name=term['name'])
        label += '\n' + tr('按复分规则解析；主表已有专类时应采用专类。')
    else:
        label = term['name']
    chain, seen = [], {term['code']}
    parent = term['parent_code']
    while parent in index and parent not in seen:
        seen.add(parent)
        ancestor = index[parent]
        chain.append(ancestor['code'] + ' ' + ancestor['name'])
        parent = ancestor['parent_code']
    if chain:
        label += '\n' + ' → '.join(reversed(chain))
    return label
