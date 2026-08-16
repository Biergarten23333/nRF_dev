#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,gzip,hashlib,json,pathlib,subprocess,sys

NODES={"BSF31CC","BSFC2CC","BSFAA61","BSF1120","BSFB165","BSFEC35","BSF44AD","BSF3C79","BSF6C53","BSF8BC4"}
EXPECTED={"D1":800196,"D2":74142}
PASS="PASS_PHASE0_IMU_INPUT_CONTEXT_COMPLETION"

class ValidationError(RuntimeError):pass
def sha(p):
 h=hashlib.sha256()
 with open(p,"rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
def validate_records(records,models,split,expected_rows,source_hashes,expected_common_times=None):
 seen=set();nodes=set();last=-1
 for r in records:
  if int(r["view_row_index"])!=last+1:raise ValidationError("row reorder/missing")
  last+=1
  if r["split_class"]!=split or r["hardware_node_id"] not in NODES:raise ValidationError("scope")
  key=(r["hardware_node_id"],int(r["raw_record_index"]),int(r["node_timer_us"]),int(r.get("sequence",0)))
  if key in seen:raise ValidationError("duplicate join")
  seen.add(key);nodes.add(r["hardware_node_id"])
  if not r.get("boot_epoch","").isdigit():raise ValidationError("boot epoch")
  ref=f"{r['hardware_node_id']}/boot-{r['boot_epoch']}/segment-0";m=models.get(ref)
  if not m:raise ValidationError("missing uncertainty reference")
  if not m.get("mapping_valid") or r.get("clock_mapping_valid")!="true":raise ValidationError("mapping invalid")
  timer=int(r["node_timer_us"]);lo,hi=m["valid_timer_domain_us"]
  if not lo<=timer<=hi:raise ValidationError("timer domain")
  age=m.get("sample_age_model",{})
  if age.get("support_us")!=[0,5000] or age.get("distribution")!="UNKNOWN_BOUNDED" or not age.get("fixed_delay_forbidden"):raise ValidationError("fixed/invalid sample age")
  if not r.get("clock_uncertainty_model_ref") or not r.get("sample_age_model_ref"):raise ValidationError("missing uncertainty reference")
  if expected_common_times is not None and int(r["common_time_ns"])!=next(expected_common_times):raise ValidationError("common time difference nonzero")
  for k,v in source_hashes.items():
   if r.get(k)!=v:raise ValidationError("source hash mismatch")
  if "D3" in json.dumps(r):raise ValidationError("D3 path/class")
 if len(seen)!=expected_rows or last+1!=expected_rows:raise ValidationError("row count/missing join")
 if nodes!=NODES:raise ValidationError("node coverage")
 return True
def read_context(path):
 with gzip.open(path,"rt",newline="") as f:yield from csv.DictReader(f)
def read_view_common(path):
 with gzip.open(path,"rb") as f:
  f.readline()
  for line in f:
   start=0;idx=0
   for pos,b in enumerate(line):
    if b in (44,10,13):
     if idx==24:
      yield int(line[start:pos]);break
     if b==44:idx+=1;start=pos+1
def validate_external(manifest,models_path):
 m=json.load(open(manifest));models=json.load(open(models_path))["models"]
 for split in ("D1","D2"):
  c=m["contexts"][split]
  if sha(c["realpath"])!=c["sha256"] or c["rows"]!=EXPECTED[split]:raise ValidationError("context identity")
  view=m["views"][split]
  if sha(view["realpath"])!=view["sha256"]:raise ValidationError("view identity")
  hashes={"source_view_sha256":m["views"][split]["sha256"],"source_time_sidecar_sha256":m["sources"]["time_sidecar_sha256"],"source_time_ledger_sha256":m["sources"]["time_ledger_sha256"]}
  validate_records(read_context(c["realpath"]),models,split,EXPECTED[split],hashes,iter(read_view_common(view["realpath"])))
 return True
def git(repo,*a,binary=False):
 p=subprocess.run(["git","-C",repo,*a],capture_output=True)
 if p.returncode:raise ValidationError(p.stderr.decode())
 return p.stdout if binary else p.stdout.decode()
def blob(repo,source,path):
 spec=":"+path if source=="index" else source+":"+path
 p=subprocess.run(["git","-C",repo,"show",spec],capture_output=True)
 if p.returncode:raise ValidationError("missing blob "+path)
 return p.stdout
def allow(repo,impl,path):return [x for x in blob(repo,impl,path).decode().splitlines() if x]
def changed(repo,source):
 raw=git(repo,"diff","--cached","--name-status","-z",binary=True) if source=="index" else git(repo,"diff-tree","--no-commit-id","--name-status","-r","-z",source,binary=True)
 p=raw.split(b"\0");return [(p[i].decode(),p[i+1].decode()) for i in range(0,len(p)-1,2)]
def validate_git(repo,source,impl,base,att=None):
 al="BioSpur_Fusion/Fusion_Part/config/fusion_v2/phase0_input_v1/STAGING_ALLOWLIST_INPUT_ATTESTATION.txt";paths=allow(repo,impl,al);actual=changed(repo,source)
 if any(s!="A" for s,_ in actual) or sorted(p for _,p in actual)!=sorted(paths):raise ValidationError("allowlist/status mismatch")
 if att:
  parents=git(repo,"rev-list","--parents","-n","1",att).split()
  if parents!=[att,impl]:raise ValidationError("parent mismatch")
 return {"status":"PASS","paths":len(paths)}
def main():
 ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest="mode",required=True)
 x=sub.add_parser("external");x.add_argument("--manifest",required=True);x.add_argument("--models",required=True)
 for mode in ("index","commit"):
  x=sub.add_parser(mode);x.add_argument("--repo",required=True);x.add_argument("--implementation-sha",required=True);x.add_argument("--base-sha",required=True);x.add_argument("--attestation-sha")
 a=ap.parse_args()
 try:
  result={"status":"PASS"} if a.mode=="external" and validate_external(a.manifest,a.models) else validate_git(a.repo,a.mode,a.implementation_sha,a.base_sha,a.attestation_sha)
  print(json.dumps(result,sort_keys=True))
 except Exception as e:print(json.dumps({"status":"FAIL","error":str(e)},sort_keys=True));sys.exit(1)
if __name__=="__main__":main()
