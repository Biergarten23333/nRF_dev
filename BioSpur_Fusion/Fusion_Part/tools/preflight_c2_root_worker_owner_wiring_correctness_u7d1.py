#!/usr/bin/env python3
"""Correctness-only U7D.1 immutable owner-boundary gate."""
from __future__ import annotations

import argparse,hashlib,json,os,resource,subprocess,sys,time,traceback
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"tests"))
from test_c2_root_worker_owner_wiring import fixture
from biospur_fusion.c2_uwb_root_world.root_worker_owner_wiring import OwnerBoundCoordinator,execute_direct_reference

PRIORS={
 ROOT/"logs/c2_root_worker_owner_wiring_u7d1_20260906T182000Z/SHA256SUMS":"29ba9717bcffabe141cd73b7d5602e0f76079ec29c26293f7b4b8ab6d8b5e1cd",
 ROOT/"logs/c2_root_worker_owner_wiring_correctness_u7d1_20260906T184000Z/SHA256SUMS":"bb0b2a82d4527918978294e243f1c9bbab8a5524f44a82aab5bcd667cb27030b",
}
CAP=20_000_000
FILES=(ROOT/"src/biospur_fusion/c2_uwb_root_world/root_worker_owner_wiring.py",
 ROOT/"tests/test_c2_root_worker_owner_wiring.py",Path(__file__).resolve())


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):Path(path).write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+"\n")
def seal(out,result,before,after,command):
    write(out/"RESULT.json",result);write(out/"HASHES.json",{"before":before,"after":after})
    (out/"COMMAND.txt").write_text(command+"\n");(out/"REPORT.md").write_text(
      "# U7D.1 immutable owner correctness revision\n\n"+f"Status: `{result['status']}`. {result['summary']}\n\n"
      "The prior RSS-blocked evidence remains non-promoted. This fixture makes no resource, online, scientific, calibrated-R, or production readiness claim.\n")
    members=sorted(x for x in out.iterdir() if x.name!="SHA256SUMS")
    (out/"SHA256SUMS").write_text("".join(f"{sha(x)}  {x.name}\n" for x in members))
    if sum(x.stat().st_size for x in out.iterdir())>=CAP:raise RuntimeError("evidence cap")
    return sha(out/"SHA256SUMS")


def main():
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    if a.output.exists():raise FileExistsError(a.output)
    a.output.mkdir(parents=True);started=time.perf_counter();before={str(x.relative_to(ROOT)):sha(x) for x in FILES}
    command=f"PYTHONPATH=src:tools:. .venv-v0/bin/python tools/preflight_c2_root_worker_owner_wiring_correctness_u7d1.py --output {a.output}"
    base={"schema":"biospur.c2.u7d1.owner-correctness.v1","execution_class":"TRANSPORT_AND_OWNER_WIRING_CORRECTNESS_FIXTURE_ONLY",
      "prior_blocked_seal_sha256":list(PRIORS.values()),"prior_blocked_non_promoted":True,"scientific_pass":False,
      "calibrated_R":False,"online_ready":False,"production_ready":False,"resource_readiness_claimed":False,
      "raw_opened":False,"action04_opened":False,"HXX_opened":False}
    try:
      if any(sha(path)!=digest for path,digest in PRIORS.items()):raise RuntimeError("prior blocked seal binding failed")
      env=dict(os.environ);env["PYTHONPATH"]="src:tools:.";tt=a.output/"TEST_TIME.txt"
      with (a.output/"TEST_STDOUT.txt").open("w") as out,(a.output/"TEST_STDERR.txt").open("w") as err:
        test=subprocess.run(["/usr/bin/time","-v","-o",str(tt),sys.executable,"-m","pytest","-q",
          "tests/test_c2_root_worker_owner_wiring.py"],cwd=ROOT,env=env,stdout=out,stderr=err,timeout=120)
      if test.returncode:raise RuntimeError(f"focused tests failed rc={test.returncode}")
      owner,packet=fixture();candidate=OwnerBoundCoordinator(owner).process(packet);reference=execute_direct_reference(owner,packet)
      errors={"state":float(np.max(np.abs(candidate.state-reference.state))),
        "covariance":float(np.max(np.abs(candidate.covariance-reference.covariance))),
        "condition":abs(candidate.candidate_condition-reference.candidate_condition)}
      for field in ("state_jacobian","sensor_r_m2","r_prior_m2","s_prior_m2"):
        errors[field]=max(float(np.max(np.abs(getattr(x,field)-getattr(y,field)))) for x,y in zip(candidate.factors,reference.factors))
      errors["nis"]=max(abs(x.prior_nis-y.prior_nis) for x,y in zip(candidate.factors,reference.factors))
      exact=(candidate.decision,candidate.root_reason,candidate.link_count,candidate.guard_calls)==(
        reference.decision,reference.root_reason,reference.link_count,reference.guard_calls)
      after={str(x.relative_to(ROOT)):sha(x) for x in FILES};ready=exact and all(x<=1e-12 for x in errors.values()) and candidate.link_count==80 and candidate.guard_calls==1 and before==after
      status="READY_FOR_MONITOR_U7D1_CORRECTNESS_REVIEW" if ready else "BLOCKED_U7D1_OWNER_CORRECTNESS"
      result={**base,"status":status,"summary":f"immutable owner digest {owner.digest}; parity {exact}; links {candidate.link_count}.",
        "owner_digest":owner.digest,"parity_exact":exact,"maximum_absolute_errors":errors,
        "same_link_count_no_deletion":candidate.link_count==80,"u1_guard_calls":candidate.guard_calls,
        "source_hashes_unchanged":before==after,"diagnostic_process_maxrss_kib":int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        "wall_s":time.perf_counter()-started}
      digest=seal(a.output,result,before,after,command);print(json.dumps({"status":status,"seal_sha256":digest},sort_keys=True));return 0 if ready else 2
    except BaseException as exc:
      after={str(x.relative_to(ROOT)):sha(x) for x in FILES};result={**base,"status":"BLOCKED_U7D1_OWNER_CORRECTNESS",
        "summary":f"First gate failed: {type(exc).__name__}: {exc}","failure":traceback.format_exc(),"wall_s":time.perf_counter()-started}
      digest=seal(a.output,result,before,after,command);print(json.dumps({"status":result["status"],"seal_sha256":digest,"failure":str(exc)},sort_keys=True));return 2


if __name__=="__main__":raise SystemExit(main())
