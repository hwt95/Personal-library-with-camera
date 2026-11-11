"""Cache the requested Wikipedia snapshots; save resolved dates and hashes."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import httpx

from prepare_clc_supplement import CHAPTERS, SUBCHAPTERS


def fetch(cache, key, timestamp):
    title = '中国图书馆分类法_(' + key.replace('-general', '*').removesuffix('-earlier') + ')'
    original = 'https://zh.wikipedia.org/wiki/' + quote(title, safe='')
    with httpx.Client(timeout=40, follow_redirects=True) as client:
        snapshot = None
        for address in (original, 'http://zh.wikipedia.org/wiki/' + title,
                        'https://zh.wikipedia.org/wiki/' + title):
            available = client.get('https://archive.org/wayback/available',
                                   params={'url': address, 'timestamp': timestamp})
            available.raise_for_status()
            snapshot = available.json().get('archived_snapshots', {}).get('closest')
            if snapshot:
                break
        if not snapshot:
            raise ValueError(f'No snapshot located for {key}')
        if not snapshot['available'] or snapshot['status'] != '200':
            raise ValueError(f'No usable snapshot: {key}')
        snapshot_url = snapshot['url'].replace('http://web.archive.org/', 'https://web.archive.org/')
        response = client.get(snapshot_url.replace('/' + snapshot['timestamp'] + '/', '/' + snapshot['timestamp'] + 'id_/'))
        response.raise_for_status()
    (cache / (key + '.html')).write_bytes(response.content)
    return {'url': str(response.url), 'bytes': len(response.content),
            'sha256': hashlib.sha256(response.content).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cache', type=Path)
    parser.add_argument('keys', nargs='*')
    parser.add_argument('--timestamp', default='20220829')
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    path = args.cache / 'downloads.json'
    manifest = json.loads(path.read_text('utf-8')) if path.exists() else {}
    keys = args.keys or [*CHAPTERS, *SUBCHAPTERS]
    failures = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(fetch, args.cache, key, args.timestamp): key for key in keys
                   if key not in manifest or not (args.cache / (key + '.html')).exists()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                manifest[key] = future.result()
                path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', 'utf-8')
                print(key, manifest[key]['bytes'], flush=True)
            except Exception as error:
                failures.append(key)
                print(key, str(error), flush=True)
    if failures:
        raise SystemExit('Missing sources: ' + ', '.join(failures))


if __name__ == '__main__':
    main()
