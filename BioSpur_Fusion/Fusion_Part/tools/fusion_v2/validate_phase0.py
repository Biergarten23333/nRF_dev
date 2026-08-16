#!/usr/bin/env python3
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]).resolve(); errors=[]
for p in sys.argv[2:]:
 q=root/p
 try:
  if q.suffix==".json": json.loads(q.read_text())
  if not q.exists() or q.stat().st_size==0: errors.append(p)
 except Exception as e: errors.append(f"{p}:{e}")
print(json.dumps({"validated":len(sys.argv)-2,"errors":errors,"status":"PASS" if not errors else "FAIL"}))
raise SystemExit(bool(errors))
