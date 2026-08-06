#!/usr/bin/env python3
import hashlib,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve();target=root/'EVIDENCE_SHA256.txt';rows=[]
for path in sorted(p for p in root.rglob('*') if p.is_file() and p != target):
 h=hashlib.sha256()
 with path.open('rb') as f:
  while block:=f.read(1024*1024):h.update(block)
 rows.append(f'{h.hexdigest()}  {path.relative_to(root)}')
target.write_text('\n'.join(rows)+'\n',encoding='utf-8')
print(f'{len(rows)} files indexed: {target}')
