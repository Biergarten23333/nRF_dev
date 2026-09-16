#!/usr/bin/env python3
"""Fetch pinned upstream IMUCoCo sources and weights, without changing Python.

SMPL is separately distributed by its authors. Supply that existing asset to
the inference command; this tool never substitutes an unofficial body model.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = 'd7dd17c70abccfc63935ce9e25452c6d312df630'
REPOSITORY = 'cmusmashlab/IMUCoCo'
DESTINATION = ROOT / 'third_party' / 'imucoco'
WEIGHTS = {
    'imucoco_best.pth': 90256731,
    'all_error_loss_map.pth': 663094,
    'poser_dtp_best.pth': 134464122,
}


def download(url: str, target: Path, limit: int, *, previous=None, git_blob=None) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        partial = target.with_suffix(target.suffix + '.partial')
        size = 0
        with urllib.request.urlopen(url, timeout=30) as response, partial.open('wb') as f:
            while block := response.read(1 << 20):
                size += len(block)
                if size > limit:
                    raise ValueError(f'download exceeds declared bound: {url}')
                f.write(block)
        partial.rename(target)
    raw = target.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if len(raw) > limit:
        raise ValueError(f'asset exceeds declared bound: {target}')
    if previous is not None and (previous['sha256'] != digest or previous['url'] != url):
        raise ValueError(f'existing pinned asset changed: {target}')
    if git_blob is not None:
        actual = hashlib.sha1(f'blob {len(raw)}\0'.encode() + raw).hexdigest()
        if actual != git_blob:
            raise ValueError(f'source differs from pinned Git blob: {target}')
    return dict(url=url, bytes=len(raw), sha256=digest)


def main():
    if shutil.disk_usage('/mnt/nrf_ssd').free < 100 * 10**9 or shutil.disk_usage('/').free < 40 * 10**9:
        raise RuntimeError('Fusion disk gate failed')
    tree_url = f'https://api.github.com/repos/{REPOSITORY}/git/trees/{REVISION}?recursive=1'
    with urllib.request.urlopen(tree_url, timeout=30) as response:
        tree = json.load(response)
    manifest_path = DESTINATION / 'UPSTREAM_MANIFEST.json'
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    if previous and previous['revision'] != REVISION:
        raise ValueError('destination contains another upstream revision')
    previous_files = previous['files'] if previous else {}
    files = {}
    for entry in tree['tree']:
        path = entry['path']
        if entry['type'] != 'blob' or not (path.endswith('.py') or path in ('LICENSE', 'README.md', 'requirements.txt')):
            continue
        if '..' in Path(path).parts or Path(path).is_absolute():
            raise ValueError('unsafe source path')
        url = f'https://raw.githubusercontent.com/{REPOSITORY}/{REVISION}/{path}'
        files[path] = download(url, DESTINATION / path, max(entry['size'], 1),
                               previous=previous_files.get(path), git_blob=entry['sha'])
    for name, size in WEIGHTS.items():
        print('Fetching', name, flush=True)
        key = 'saved_checkpoints/' + name
        if (DESTINATION / key).exists() and key not in previous_files:
            raise ValueError(f'existing weight has no trusted local manifest: {key}')
        files[key] = download('https://synergylabs.org/haozhe/' + name, DESTINATION / key, size,
                              previous=previous_files.get(key))
        if files[key]['bytes'] != size:
            raise ValueError(f'weight length changed: {name}')
    manifest = dict(repository=REPOSITORY, revision=REVISION, files=files,
        source_modifications=[], weights_modified=False, smpl_included=False)
    (DESTINATION / 'UPSTREAM_MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(DESTINATION, flush=True)


if __name__ == '__main__':
    main()
