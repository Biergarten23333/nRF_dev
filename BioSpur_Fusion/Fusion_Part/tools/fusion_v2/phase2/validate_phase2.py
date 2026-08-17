#!/usr/bin/env python3
import argparse,hashlib,json,subprocess,sys
from pathlib import Path
VERDICT="STAGE_COMPLETE_NEEDS_USER_CAPTURE"
class ValidationError(RuntimeError):pass
def git(repo,*args,binary=False):
 p=subprocess.run(["git","-C",repo,*args],capture_output=True)
 if p.returncode:raise ValidationError(p.stderr.decode())
 return p.stdout if binary else p.stdout.decode()
def blob(repo,source,path):
 spec=":"+path if source=="index" else source+":"+path;p=subprocess.run(["git","-C",repo,"show",spec],capture_output=True)
 if p.returncode:raise ValidationError("missing blob "+path)
 return p.stdout
def changed(repo,source):
 raw=git(repo,"diff","--cached","--name-status","-z",binary=True) if source=="index" else git(repo,"diff-tree","--no-commit-id","--name-status","-r","-z",source,binary=True)
 x=raw.split(b"\0");return [(x[i].decode(),x[i+1].decode()) for i in range(0,len(x)-1,2)]
def validate_artifacts(repo,source,implementation,attestation=None):
 al="BioSpur_Fusion/Fusion_Part/config/fusion_v2/phase2/STAGING_ALLOWLIST_PHASE2_ATTESTATION.txt"
 paths=[x for x in blob(repo,implementation,al).decode().splitlines() if x];actual=changed(repo,source)
 if sorted(p for _,p in actual)!=sorted(paths) or any(s not in ("A","M") for s,_ in actual):raise ValidationError("allowlist/status")
 base="BioSpur_Fusion/Fusion_Part/reports/fusion_v2/phase2/"
 result=json.loads(blob(repo,source,base+"PHASE2_RESULT.json"));handoff=json.loads(blob(repo,source,base+"PHASE_HANDOFF_PREPUBLICATION.json"));bundle=json.loads(blob(repo,source,base+"CONDITIONAL_CALIBRATION_BUNDLE_MANIFEST.json"))
 if result.get("stage_verdict")!=VERDICT or result.get("implementation_sha")!=implementation or result.get("phase3_started") is not False:raise ValidationError("result")
 if result.get("authoritative_mapping_frozen") is not False or result.get("authoritative_calibration_bundle") is not False:raise ValidationError("false freeze")
 if handoff.get("implementation_sha")!=implementation or handoff.get("attestation_sha")!="PENDING" or handoff.get("publication_status")!="PENDING":raise ValidationError("handoff")
 if bundle.get("authoritative") is not False or bundle.get("mapping_status") not in ("INSUFFICIENT_EVIDENCE","PROVISIONAL_TOPK"):raise ValidationError("bundle")
 tree=set(git(repo,"ls-tree","-r","--name-only",source if source!="index" else "HEAD").splitlines()) if source!="index" else set()
 for forbidden in (base+"NODE_ASSOCIATION_FREEZE.json",base+"CALIBRATION_BUNDLE_MANIFEST.json"):
  if forbidden in tree or (source=="index" and subprocess.run(["git","-C",repo,"cat-file","-e",":"+forbidden],capture_output=True).returncode==0):raise ValidationError("authoritative artifact forbidden")
 sums=blob(repo,source,base+"SHA256SUMS.txt").decode().splitlines();seen={}
 for line in sums:
  if not line:continue
  h,p=line.split("  ",1);seen[p]=h
 if set(seen)!=set(paths)-{base+"SHA256SUMS.txt"}:raise ValidationError("SHA coverage")
 for p,h in seen.items():
  if hashlib.sha256(blob(repo,source,p)).hexdigest()!=h:raise ValidationError("SHA mismatch "+p)
 if attestation and git(repo,"rev-list","--parents","-n","1",attestation).split()!=[attestation,implementation]:raise ValidationError("parent")
 return {"status":"PASS","paths":len(paths),"verdict":VERDICT}
def validate_publication_objects(report,envelope,expected):
 for obj in (report,envelope):
  text=json.dumps(obj,sort_keys=True).lower()
  if "self_hash" in text or "future_sha" in text:raise ValidationError("self/future")
 req={"schema","stage","implementation_sha","attestation_sha","branch","remote","live_remote_sha","publication_status","stage_verdict","tests","D1_access","D2_access","D3_current","D3_cumulative","protected_start","protected_end","prepublication_handoff_sha256","P3_probe_sha256","target_capture_package_sha256","phase3_started"}
 if set(report)<req:raise ValidationError("report fields")
 if report["schema"]!="biospur-phase2-publication-v1" or report["stage"]!="PHASE2" or report["stage_verdict"]!=VERDICT:raise ValidationError("identity/verdict")
 for k in ("implementation_sha","attestation_sha","branch","remote","prepublication_handoff_sha256","P3_probe_sha256","target_capture_package_sha256"):
  if report[k]!=expected[k]:raise ValidationError(k)
 if report["live_remote_sha"]!=expected["attestation_sha"] or report["publication_status"]!="SUCCESS" or report["phase3_started"] is not False:raise ValidationError("publication")
 if not isinstance(report["D3_current"],dict) or any(report["D3_current"].values()):raise ValidationError("D3 current")
 if report["D3_cumulative"]!={"known_incident_count":1,"D3_pristine_claim":False,"D3_status":"ACTION_LEVEL_LIMITED_HOLDOUT"}:raise ValidationError("D3 cumulative")
 if report["protected_start"]!=report["protected_end"]:raise ValidationError("protected")
 er={"schema","stage","attestation_sha","live_remote_sha","publication_report_realpath","publication_report_sha256","prepublication_handoff_sha256","protected_start","protected_end","publication_status"}
 if set(envelope)<er or envelope["schema"]!="biospur-phase-handoff-envelope-v1" or envelope["stage"]!="PHASE2":raise ValidationError("envelope")
 if envelope["attestation_sha"]!=expected["attestation_sha"] or envelope["live_remote_sha"]!=expected["attestation_sha"] or envelope["publication_report_realpath"]!=expected["publication_report_realpath"] or envelope["publication_report_sha256"]!=expected["publication_report_sha256"]:raise ValidationError("envelope binding")
 if envelope["protected_start"]!=envelope["protected_end"] or envelope["publication_status"]!="SUCCESS":raise ValidationError("envelope state")
 return True
def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest="mode",required=True)
 for m in ("index","commit"):
  q=sub.add_parser(m);q.add_argument("--repo",required=True);q.add_argument("--implementation-sha",required=True);q.add_argument("--attestation-sha")
 q=sub.add_parser("publication");q.add_argument("--report",required=True);q.add_argument("--envelope",required=True);q.add_argument("--expected",required=True)
 a=p.parse_args()
 try:
  if a.mode=="publication":
   raw=Path(a.report).read_bytes();e=json.load(open(a.expected))
   if hashlib.sha256(raw).hexdigest()!=e["publication_report_sha256"]:raise ValidationError("report hash")
   validate_publication_objects(json.loads(raw),json.load(open(a.envelope)),e);out={"status":"PASS"}
  else:out=validate_artifacts(a.repo,"index" if a.mode=="index" else a.attestation_sha,a.implementation_sha,a.attestation_sha if a.mode=="commit" else None)
  print(json.dumps(out,sort_keys=True))
 except Exception as exc:print(json.dumps({"status":"FAIL","error":str(exc)},sort_keys=True));raise SystemExit(1)
if __name__=="__main__":main()

