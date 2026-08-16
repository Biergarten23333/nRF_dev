#!/usr/bin/env python3
import datetime,hashlib,json,os,pathlib
class AccessDenied(RuntimeError):pass
class AccessGate:
 def __init__(self,allowlist,ledger):
  self.allowlist_path=pathlib.Path(allowlist);self.ledger=pathlib.Path(ledger);self.ledger.parent.mkdir(parents=True,exist_ok=True)
  cfg=json.loads(self.allowlist_path.read_text());self.entries={os.path.realpath(x["path"]):x for x in cfg["entries"]}
 def authorize(self,path,action,program):
  real=os.path.realpath(path)
  if real!=path or real not in self.entries:raise AccessDenied("DENIED_NOT_EXACT_ALLOWLIST_REALPATH")
  e=self.entries[real];rec={"time":datetime.datetime.now(datetime.timezone.utc).isoformat(),"realpath":real,"action":action,"program":program,"access_class":e["access_class"],"modality":e["modality"],"d_class":e["d_class"],"sealed":e["sealed"]}
  with self.ledger.open("a",encoding="utf-8") as f:f.write(json.dumps(rec,sort_keys=True)+"\n");f.flush();os.fsync(f.fileno())
  return real,e
 def hash(self,path,program):
  real,e=self.authorize(path,"BYTE_IDENTITY",program);h=hashlib.sha256();size=0
  with open(real,"rb",buffering=1<<20) as f:
   for b in iter(lambda:f.read(1<<20),b''):h.update(b);size+=len(b)
  return {"realpath":real,"size":size,"sha256":h.hexdigest(),"expected_sha256":e.get("expected_sha256"),"match":e.get("expected_sha256") in (None,h.hexdigest())}
