"""Build the bundled dictionary from the user-supplied tree, without PDF dependencies."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORRECTIONS = {f'U653.923.+{i}': f'U653.923+.{i}' for i in range(1, 6)}


def prepare(source, destination):
    raw = source.read_bytes()
    tree = json.loads(raw.decode('utf-8-sig'))
    rows, seen = [], set()
    def walk(nodes, parent=''):
        for node in nodes:
            original, name = node['id'], node['desc']
            if not isinstance(original, str) or not isinstance(name, str) or not original or not name:
                raise ValueError('Empty or non-text classification entry')
            code = CORRECTIONS.get(original, original)
            if code in seen:
                raise ValueError(f'Duplicate code: {code}')
            seen.add(code)
            rows.append([code, name, parent, original if original != code else ''])
            walk(node.get('children', []), code)
    walk(tree)
    payload = json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    destination.mkdir(parents=True, exist_ok=True)
    compressed = gzip.compress(payload, compresslevel=9, mtime=0)
    (destination / 'clc.json.gz').write_bytes(compressed)
    manifest = {'name': '用户提供的中图法分类字典', 'revision': '2026-11-07',
        'original_file': source.name, 'original_sha256': hashlib.sha256(raw).hexdigest(),
        'payload_sha256': hashlib.sha256(compressed).hexdigest(), 'entries': len(rows),
        'rights': '用户说明仅限非商业使用；未随文件提供原作者、项目链接或具体许可文本。数据权利独立于软件许可。',
        'reference': '中国图书馆分类法（第5版）.pdf，仅用于少量抽查，不随程序分发。',
        'corrections': [{'original': old, 'corrected': new, 'pdf_page': 966, 'printed_page': 946}
                        for old, new in CORRECTIONS.items()]}
    (destination / 'SOURCE.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    return {'entries': len(rows), 'compressed_bytes': len(compressed), 'original_sha256': manifest['original_sha256']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, ROOT / 'code/resources/classification'), ensure_ascii=False))
