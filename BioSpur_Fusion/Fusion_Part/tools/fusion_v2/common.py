#!/usr/bin/env python3
import hashlib,json,pathlib,subprocess
def sha(path):
 h=hashlib.sha256()
 with open(path,"rb") as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()
def dump(path,obj):pathlib.Path(path).write_text(json.dumps(obj,indent=2,sort_keys=True,allow_nan=False)+"\n")
def tool_identity(path):return {"realpath":str(pathlib.Path(path).resolve()),"sha256":sha(path)}
def git(*args,cwd):return subprocess.check_output(["git","-C",str(cwd),*args],text=True)
