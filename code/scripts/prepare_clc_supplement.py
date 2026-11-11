"""Rebuild the additive CLC supplement from locally cached, attributed sources.

The original dictionary is never rewritten. Network acquisition is separate from
this deterministic build; ambiguous, marked and withdrawn entries are audited.
"""
import argparse
from collections import Counter, defaultdict
from copy import copy
import gzip
import hashlib
import json
from pathlib import Path
import re

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
CHAPTERS = 'ABCDEFGHIJKNOPQRSTUVXZ'
SUBCHAPTERS = [*('O' + c for c in '1234567'), 'T-general',
               *('T' + c for c in 'BDEFGHJKLMNPQSUV'), *('TQ' + c for c in '01234569')]
CODE = r'[A-Z]{1,2}[0-9]*(?:[.\-+][0-9]+)*(?:/[A-Z0-9.]*)?'
ENTRY = re.compile(r'^([\[{]?' + CODE + r'[\]}]?)(?:\s+|(?=[\u3400-\u9fff]))(.+)$')
ACTIVE = re.compile(r'[A-Z]{1,2}[0-9]*(?:[.\-+][0-9]+)*$')
AUX_NAMES = ['', '总论复分表', '世界地区表', '中国地区表', '国际时代表',
             '中国时代表', '世界种族与民族表', '中国民族表', '通用时间、地点表']
SZU = 'https://www.lib.szu.edu.cn/nkosapp/ProjectForCLC/'


def read_html(path):
    return BeautifulSoup(path.read_bytes(), 'html.parser')


def wiki_entries(path):
    soup = read_html(path)
    contents = soup.select('.mw-parser-output')
    if not contents:
        raise ValueError(f'Not an article: {path}')
    content = max(contents, key=lambda tag: len(tag.get_text()))
    for tag in content.select('.toc,.navbox,.mw-editsection,sup.reference,.reflist,table.ambox'):
        tag.decompose()
    entries = []
    # Headings are navigation summaries and sometimes contradict the actual list.
    for element in content.select('li,p,dt,dd'):
        fragment = copy(element)
        for nested in fragment.select('ul,ol,dl,li,p,table'):
            nested.decompose()
        for line in fragment.get_text('', strip=False).splitlines():
            line = re.sub(r'\s+', ' ', line).strip().translate(str.maketrans('［］｛｝', '[]{}'))
            match = ENTRY.match(line)
            if not match:
                continue
            code, text = match.groups()
            # Keep explanatory scope separately from the label; date/name
            # parentheses remain part of the label unless they contain a rule.
            cut = re.search(r'\s*[（(](?=[^）)]*(?:入此|入[A-Z]|依|仿|删[除去]|停用|包括|综合集|如有必要))', text)
            name, note = (text[:cut.start()].strip(), text[cut.start():].strip()) if cut else (text, '')
            name = name.rstrip('。')
            if not name:
                continue
            withdrawn = bool(re.search(r'删[除去]|停用|不再使用', text)) or bool(element.find(['s', 'del', 'strike']))
            entries.append({'code': code, 'name': name, 'note': note, 'withdrawn': withdrawn})
    return entries


def szu_table(path, number):
    soup = read_html(path)
    rows = []
    for anchor in soup.select('a[onclick]'):
        match = re.search(r"openrecord\('fb" + str(number) + r"_([^']+)'", anchor['onclick'])
        if not match:
            continue
        code = match[1]
        if '/' in code:
            continue
        name = anchor.get_text(' ', strip=True).removeprefix(code).strip()
        parent = ''
        for ancestor in anchor.find_parent('li').find_parents('li'):
            a = ancestor.find('a', recursive=False)
            if a:
                m = re.search(r"openrecord\('fb\d_([^']+)'", a.get('onclick', ''))
                if m and '/' not in m[1]:
                    parent = m[1]
                    break
        status = 'marked' if code.startswith(('[', '{')) else 'active'
        rows.append({'code': code, 'name': name, 'parent_code': parent, 'status': status})
    if not rows:
        raise ValueError(f'Empty auxiliary table: {path}')
    return {'name': AUX_NAMES[number], 'edition': '第4版', 'source_id': f'szu-fb{number}', 'entries': rows}


def nearest_parent(code, index):
    for end in range(len(code) - 1, 0, -1):
        parent = code[:end]
        if parent in index and parent[-1].isalnum():
            return parent
    return ''


def blocked_branch(code, original, blocked):
    for end in range(len(code), 0, -1):
        stem = code[:end]
        if stem in original:
            return False
        if stem in blocked:
            return True
    return False


def write_gzip(path, data):
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    compressed = gzip.compress(payload, compresslevel=9, mtime=0)
    path.write_bytes(compressed)
    return hashlib.sha256(compressed).hexdigest()


def prepare(cache, destination):
    original_path = destination / 'clc.json.gz'
    original = {row[0]: row for row in json.loads(gzip.decompress(original_path.read_bytes()))}
    records, audit, sources = defaultdict(list), [], {}
    downloads = json.loads((cache / 'downloads.json').read_text('utf-8'))
    chapter_counts, unavailable = {}, []
    for chapter in [*CHAPTERS, *SUBCHAPTERS, 'I-earlier']:
        path = cache / f'{chapter}.html'
        if not path.is_file():
            if chapter in ('O4', 'TF', 'TN', 'TS'):
                unavailable.append(chapter)
                continue
            raise ValueError(f'Missing chapter {chapter}; do not silently build a partial supplement')
        page_id = f'wiki-{chapter}'
        html = path.read_text('utf-8')
        revision = re.search(r'"wgRevisionId":(\d+)', html)
        sources[page_id] = {'title': f'维基百科：中国图书馆分类法 ({chapter})',
            'url': downloads[chapter]['url'].replace('id_/', '/'),
            'revision_id': revision[1] if revision else '',
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'attribution': '中文维基百科贡献者', 'license': 'CC BY-SA 3.0（快照页脚）；附加条款可能适用'}
        entries = wiki_entries(path)
        chapter_counts[chapter] = len(entries)
        for item in entries:
            if chapter.endswith('-earlier') and item['code'] in records:
                continue
            item['source_id'] = page_id
            records[item['code']].append(item)

    # Explicit checks against the fifth-edition printed tables, rather than
    # silently accepting the two Wikipedia transcription errors.
    for item in records.get('I313.74', []):
        if item['name'] == '语言':
            item['name'] = '寓言'
            item['verification'] = {'source_id': 'clc-v5-auxiliary', 'pdf_page': 196,
                                    'original_name': '语言'}
    for item in records.pop('I421.99', []):
        if item['name'] == '厄立特里亚':
            item['code'] = 'I421.9'
            item['verification'] = {'source_id': 'clc-v5-auxiliary', 'pdf_page': 1017,
                                    'original_code': 'I421.99'}
            records['I421.9'].append(item)
        else:
            records['I421.99'].append(item)

    blocked = {code.strip('[]{}') for code in original if code.startswith(('[', '{'))}
    withdrawn = {}
    for code, variants in records.items():
        if any(item['withdrawn'] for item in variants):
            withdrawn[code] = variants[0]
            blocked.add(code.strip('[]{}'))

    candidates = {}
    for code, variants in sorted(records.items()):
        names = {item['name'] for item in variants}
        reason = ''
        if code in original:
            if original[code][1] not in names:
                audit.append({'code': code, 'reason': 'existing_name_preserved', 'existing': original[code][1],
                              'source_names': sorted(names)})
            continue
        if not ACTIVE.fullmatch(code):
            reason = 'marked_range_or_invalid'
        elif blocked_branch(code, original, blocked):
            reason = 'marked_or_withdrawn_branch'
        elif len(names) != 1:
            reason = 'conflicting_source_names'
        elif not any(c.isdigit() for c in code):
            reason = 'not_a_new_root'
        if reason:
            audit.append({'code': code, 'reason': reason, 'variants': variants})
        else:
            candidates[code] = dict(variants[0])

    all_codes = original.keys() | candidates.keys()
    for code, item in candidates.items():
        parent = nearest_parent(code, all_codes)
        if not parent:
            raise ValueError(f'Orphan supplemental entry: {code}')
        item['parent_code'] = parent
        item.pop('withdrawn')

    tables = {}
    for number in range(1, 9):
        path = cache / ('szu.html' if number == 1 else f'aux{number}.html')
        szu_table(path, number)  # Validate the independent crosscheck source.
        sources[f'szu-fb{number}'] = {'title': f'深圳大学图书馆 CLSS：{AUX_NAMES[number]}',
            'url': SZU + f'clcmain.jsp?lang=chi&tag={number}', 'edition': '第4版',
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'attribution': '深圳大学图书馆 NKOS 研究室；中国图书馆分类法',
            'license': '网站声明保留权利；数据权利不由本项目软件许可覆盖'}
    # Validate and attribute the requested Wikipedia table as a crosscheck;
    # runtime entries come only from the reviewed fifth-edition source below.
    soup = read_html(cache / 'auxiliary.html')
    general = []
    for li in soup.select('.mw-parser-output li'):
        fragment = copy(li)
        for nested in fragment.select('ul,ol,dl'):
            nested.decompose()
        match = re.match(r'^(-\d+)\s+(.+)', fragment.get_text(' ', strip=True))
        if match:
            general.append({'code': match[1], 'name': match[2], 'status': 'active'})
    seen = {row['code'] for row in general}
    for row in general:
        row['parent_code'] = nearest_parent(row['code'], seen)
    if len(seen) != len(general) or len(general) < 60:
        raise ValueError('General table is incomplete or duplicated')
    sources['wiki-general'] = {'title': '维基百科：中国图书馆分类法 (通用复分表)',
        'url': downloads['auxiliary']['url'].replace('id_/', '/'),
        'sha256': hashlib.sha256((cache / 'auxiliary.html').read_bytes()).hexdigest(),
        'attribution': '中文维基百科贡献者', 'license': 'CC BY-SA 3.0（快照页脚）；附加条款可能适用'}

    reviewed_path = ROOT / 'deps/classification/auxiliary-v5.json'
    reviewed = json.loads(reviewed_path.read_text('utf-8'))
    tables = reviewed['tables']
    sources['clc-v5-auxiliary'] = reviewed['source']
    sources['clc-v5-auxiliary']['reviewed_data_sha256'] = hashlib.sha256(reviewed_path.read_bytes()).hexdigest()
    sources['clc-v5-training'] = {
        'title': '《中图法》标记符号系统和复分表的修订和使用',
        'attribution': '西安交通大学图书馆 雒虹，2011年5月',
        'url': 'https://lib.xdxd.cn/uploads/ftp1213/zhongtu5.9.pdf',
        'use': '复分连接符、顺序和适用范围；总论复分例外；主表已有专类优先。',
        'rights': '原资料权利归原权利人；仅作规则核对，不随程序分发原PDF。'}
    result = {'schema': 1, 'revision': '2026-11-11', 'entries': list(candidates.values()),
              'withdrawn': list(withdrawn.values()), 'tables': tables}
    digest = write_gzip(destination / 'supplement.json.gz', result)
    manifest = {'revision': result['revision'], 'base_sha256': hashlib.sha256(original_path.read_bytes()).hexdigest(),
        'payload_sha256': digest, 'base_entries': len(original), 'added_entries': len(candidates),
        'total_entries': len(original) + len(candidates), 'chapter_rows': chapter_counts,
        'auxiliary_counts': {key: len(table['entries']) for key, table in tables.items()},
        'unavailable_wikipedia_subpages': unavailable,
        'audit_counts': dict(Counter(row['reason'] for row in audit)), 'sources': sources,
        'merge_policy': '原有条目和父子关系优先；新增项取最长有效上级；排除标记/停用分支、范围和冲突，不覆盖用户词典。',
        'limitations': ['附表依据第5版原书，编号和名称保留原版历史范围，不作为现行政区或族群学标准。',
                       'O4、TF、TN、TS 的多个日期/地址未找到可读快照；相应原有字典完整保留，不声称已从快照补齐。']}
    (destination / 'SUPPLEMENT_SOURCE.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    (cache / 'merge-audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    return {key: manifest[key] for key in ('added_entries', 'total_entries', 'chapter_rows', 'auxiliary_counts', 'audit_counts')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cache', type=Path)
    parser.add_argument('--destination', type=Path, default=ROOT / 'code/resources/classification')
    args = parser.parse_args()
    print(json.dumps(prepare(args.cache, args.destination), ensure_ascii=False, indent=2))
