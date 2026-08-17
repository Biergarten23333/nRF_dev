#!/usr/bin/env python3
import argparse,hashlib,json,subprocess,sys
PASS="PASS_TEN_NODE_RAW_IMU_ORIENTATION_AND_BIAS_BASELINE"
class ValidationError(RuntimeError):pass
def git(repo,*a,binary=False):
 p=subprocess.run(["git","-C",repo,*a],capture_output=True)
 if p.returncode:raise ValidationError(p.stderr.decode())
 return p.stdout if binary else p.stdout.decode()
def blob(repo,source,path):
 spec=":"+path if source=="index" else source+":"+path;p=subprocess.run(["git","-C",repo,"show",spec],capture_output=True)
 if p.returncode:raise ValidationError("missing blob "+path)
 return p.stdout
def changed(repo,source):
 raw=git(repo,"diff","--cached","--name-status","-z",binary=True) if source=="index" else git(repo,"diff-tree","--no-commit-id","--name-status","-r","-z",source,binary=True);p=raw.split(b"\0");return [(p[i].decode(),p[i+1].decode()) for i in range(0,len(p)-1,2)]
def validate_artifacts(repo,source,impl,att=None):
 al="BioSpur_Fusion/Fusion_Part/config/fusion_v2/imu_frontend/STAGING_ALLOWLIST_PHASE1_ATTESTATION.txt";paths=[x for x in blob(repo,impl,al).decode().splitlines() if x];actual=changed(repo,source)
 if sorted(p for _,p in actual)!=sorted(paths) or any(s not in ("A","M") for s,_ in actual):raise ValidationError("allowlist/status")
 base="BioSpur_Fusion/Fusion_Part/reports/fusion_v2/phase1/";result=json.loads(blob(repo,source,base+"PHASE1_RESULT.json"));handoff=json.loads(blob(repo,source,base+"PHASE_HANDOFF_PREPUBLICATION.json"))
 if result.get("qualification_verdict")!="PHASE1_PREPUBLICATION_QUALIFICATION_PASSED" or result.get("publication_status")!="PENDING" or result.get("implementation_sha")!=impl:raise ValidationError("tracked result")
 if handoff.get("attestation_sha")!="PENDING" or handoff.get("publication_status")!="PENDING" or handoff.get("implementation_sha")!=impl:raise ValidationError("handoff")
 sums=blob(repo,source,base+"SHA256SUMS.txt").decode().splitlines();seen={}
 for line in sums:
  if not line or line.startswith('#'):continue
  h,p=line.split('  ',1);seen[p]=h
 if set(seen)!=set(paths)-{base+"SHA256SUMS.txt"}:raise ValidationError("SHA coverage")
 for p,h in seen.items():
  if hashlib.sha256(blob(repo,source,p)).hexdigest()!=h:raise ValidationError("SHA mismatch")
 if att:
  parents=git(repo,"rev-list","--parents","-n","1",att).split()
  if parents!=[att,impl]:raise ValidationError("parent")
 return {"status":"PASS","paths":len(paths)}
def validate_publication_objects(report,envelope,expected):
 for obj in (report,envelope):
  if "self_hash" in json.dumps(obj,sort_keys=True).lower() or "future_sha" in json.dumps(obj,sort_keys=True).lower():raise ValidationError("self/future")
 req={"schema","stage","implementation_sha","attestation_sha","branch","remote","publication_status","research_verdict","production_intrinsic_status","tests","D1","D2","forbidden_counts","D3_current","D3_cumulative","compatibility_sha256","prepublication_handoff_sha256","protected_start","protected_end"}
 if set(report)<req:raise ValidationError("report fields")
 if report["schema"]!="biospur-phase1-publication-v1" or report["stage"]!="PHASE1":raise ValidationError("schema/stage")
 for k in ("implementation_sha","attestation_sha","branch","remote","prepublication_handoff_sha256"):
  if report[k]!=expected[k]:raise ValidationError(k)
 if report["research_verdict"]!=PASS or report["publication_status"]!="SUCCESS" or report["production_intrinsic_status"]!="PRODUCTION_INTRINSIC_NOT_YET_QUALIFIED":raise ValidationError("verdict/status")
 forbidden=report["forbidden_counts"];current=report["D3_current"]
 if not isinstance(forbidden,dict) or not isinstance(current,dict):raise ValidationError("forbidden/D3 type")
 if any(forbidden.values()) or any(current.values()):raise ValidationError("forbidden/D3")
 if report["D3_cumulative"]!={"known_incident_count":1,"D3_pristine_claim":False,"D3_status":"ACTION_LEVEL_LIMITED_HOLDOUT"}:raise ValidationError("D3 history")
 if report["protected_start"]!=report["protected_end"]:raise ValidationError("protected")
 if report["compatibility_sha256"]!=expected["compatibility_sha256"]:raise ValidationError("compatibility")
 er={"schema","stage","attestation_sha","live_remote_sha","publication_report_realpath","publication_report_sha256","prepublication_handoff_sha256","protected_start","protected_end","publication_status"}
 if set(envelope)<er or envelope["schema"]!="biospur-phase-handoff-envelope-v1" or envelope["stage"]!="PHASE1":raise ValidationError("envelope")
 if envelope["attestation_sha"]!=expected["attestation_sha"] or envelope["live_remote_sha"]!=expected["attestation_sha"] or envelope["publication_report_sha256"]!=expected["publication_report_sha256"] or envelope["publication_report_realpath"]!=expected["publication_report_realpath"]:raise ValidationError("envelope identity")
 if envelope["protected_start"]!=envelope["protected_end"] or envelope["publication_status"]!="SUCCESS":raise ValidationError("envelope status")
 return True
def main():
 p=argparse.ArgumentParser();s=p.add_subparsers(dest="mode",required=True)
 for m in ("index","commit"):
  x=s.add_parser(m);x.add_argument("--repo",required=True);x.add_argument("--implementation-sha",required=True);x.add_argument("--attestation-sha")
 x=s.add_parser("publication");x.add_argument("--report",required=True);x.add_argument("--envelope",required=True);x.add_argument("--expected",required=True)
 a=p.parse_args()
 try:
  if a.mode=="publication":
   raw=open(a.report,"rb").read();e=json.load(open(a.expected))
   if hashlib.sha256(raw).hexdigest()!=e["publication_report_sha256"]:raise ValidationError("report hash")
   validate_publication_objects(json.loads(raw),json.load(open(a.envelope)),e);r={"status":"PASS"}
  else:r=validate_artifacts(a.repo,"index" if a.mode=="index" else a.attestation_sha,a.implementation_sha,a.attestation_sha if a.mode=="commit" else None)
  print(json.dumps(r,sort_keys=True))
 except Exception as e:print(json.dumps({"status":"FAIL","error":str(e)},sort_keys=True));sys.exit(1)
if __name__=="__main__":main()
