#!/usr/bin/env python3
"""One-shot transport-and-owner-wiring fixture for U7D.1."""
from __future__ import annotations

import argparse,hashlib,json,os,resource,subprocess,sys,time,traceback
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"tests"))
from test_c2_root_worker_owner_wiring import fixture
from biospur_fusion.c2_uwb_root_world.root_worker_owner_wiring import OwnerBoundCoordinator,execute_direct_reference

CAP=300_000;ECAP=20_000_000
FILES=(ROOT/"src/biospur_fusion/c2_uwb_root_world/root_worker_owner_wiring.py",
 ROOT/"tests/test_c2_root_worker_owner_wiring.py",Path(__file__).resolve(),
 ROOT/"src/biospur_fusion/c2_uwb_root_world/root_worker_event_codec.py",
 ROOT/"src/biospur_fusion/c2_uwb_root_world/async_root_worker_u7d.py")
TESTS=("tests/test_c2_root_worker_owner_wiring.py","tests/test_c2_root_worker_event_codec.py",
 "tests/test_c2_async_root_worker.py","tests/test_c2_online_root_coordinator.py",
 "tests/test_c2_causal_update_guard.py","tests/test_c2_causal_update_transaction.py")


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):Path(path).write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+"\n")
def memory():return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
def ext(path):
    for line in path.read_text().splitlines():
        if "Maximum resident set size (kbytes):" in line:return int(line.rsplit(":",1)[1])
    raise RuntimeError("missing external RSS")
def seal(out,result,before,after,command):
    write(out/"RESULT.json",result);write(out/"HASHES.json",{"before":before,"after":after})
    (out/"COMMAND.txt").write_text(command+"\n");(out/"REPORT.md").write_text(
      "# U7D.1 owner wiring fixture\n\n"+f"Status: `{result['status']}`. {result['summary']}\n\n"
      "This qualifies transport and explicit owner wiring only. Online, action, production, scientific, and calibrated-R claims remain false.\n")
    members=sorted(x for x in out.iterdir() if x.name!="SHA256SUMS")
    (out/"SHA256SUMS").write_text("".join(f"{sha(x)}  {x.name}\n" for x in members))
    if sum(x.stat().st_size for x in out.iterdir())>=ECAP:raise RuntimeError("evidence cap")
    return sha(out/"SHA256SUMS")


def main():
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    if a.output.exists():raise FileExistsError(a.output)
    a.output.mkdir(parents=True);start=time.perf_counter();before={str(x.relative_to(ROOT)):sha(x) for x in FILES}
    command=f"PYTHONPATH=src:tools:. .venv-v0/bin/python tools/preflight_c2_root_worker_owner_wiring_u7d1.py --output {a.output}"
    base={"schema":"biospur.c2.u7d1.owner-wiring.fixture.v1","execution_class":"TRANSPORT_AND_OWNER_WIRING_FIXTURE_ONLY",
      "scientific_pass":False,"calibrated_R":False,"production_ready":False,"online_ready":False,
      "raw_opened":False,"action04_opened":False,"HXX_opened":False}
    try:
      env=dict(os.environ);env.update({"OMP_NUM_THREADS":"1","OPENBLAS_NUM_THREADS":"1","MKL_NUM_THREADS":"1","NUMEXPR_NUM_THREADS":"1","PYTHONPATH":"src:tools:."})
      tt=a.output/"TEST_TIME.txt"
      with (a.output/"TEST_STDOUT.txt").open("w") as o,(a.output/"TEST_STDERR.txt").open("w") as e:
        test=subprocess.run(["/usr/bin/time","-v","-o",str(tt),sys.executable,"-m","pytest","-q",*TESTS],cwd=ROOT,env=env,stdout=o,stderr=e,timeout=180)
      if test.returncode:raise RuntimeError(f"tests failed rc={test.returncode}")
      owner,packet=fixture();candidate=OwnerBoundCoordinator(owner).process(packet);reference=execute_direct_reference(owner,packet)
      errors={"state":float(np.max(np.abs(candidate.state-reference.state))),
        "covariance":float(np.max(np.abs(candidate.covariance-reference.covariance))),
        "candidate_condition":abs(candidate.candidate_condition-reference.candidate_condition)}
      for field in ("state_jacobian","sensor_r_m2","r_prior_m2","s_prior_m2"):
        errors[field]=max(float(np.max(np.abs(getattr(x,field)-getattr(y,field)))) for x,y in zip(candidate.factors,reference.factors))
      errors["prior_nis"]=max(abs(x.prior_nis-y.prior_nis) for x,y in zip(candidate.factors,reference.factors))
      exact=(candidate.decision,candidate.root_reason,candidate.candidate_rank,candidate.link_count)==(
        reference.decision,reference.root_reason,reference.candidate_rank,reference.link_count)
      after={str(x.relative_to(ROOT)):sha(x) for x in FILES};parent=memory();test_rss=ext(tt)
      ready=exact and all(x<=1e-12 for x in errors.values()) and candidate.guard_calls==reference.guard_calls==1 and candidate.link_count==80 and before==after and parent<CAP and test_rss<CAP
      status="READY_FOR_MONITOR_U7D1_OWNER_WIRING_REVIEW" if ready else "ONLINE_BLOCKED_U7D1_OWNER_WIRING"
      result={**base,"status":status,"summary":f"owner digest {owner.digest}; link count {candidate.link_count}; exact parity {exact}.",
        "owner_digest":owner.digest,"decision_reason_exact":exact,"maximum_absolute_errors":errors,
        "u1_guard_calls":{"candidate":candidate.guard_calls,"reference":reference.guard_calls},
        "same_link_count_no_deletion":candidate.link_count==reference.link_count==80,
        "candidate_rank":candidate.candidate_rank,"candidate_condition":candidate.candidate_condition,
        "resources":{"parent_maxrss_kib":parent,"test_maxrss_kib":test_rss},"source_hashes_unchanged":before==after,
        "wall_s":time.perf_counter()-start}
      digest=seal(a.output,result,before,after,command);print(json.dumps({"status":status,"seal_sha256":digest},sort_keys=True));return 0 if ready else 2
    except BaseException as exc:
      after={str(x.relative_to(ROOT)):sha(x) for x in FILES};result={**base,"status":"ONLINE_BLOCKED_U7D1_OWNER_WIRING",
        "summary":f"First gate failed: {type(exc).__name__}: {exc}","failure":traceback.format_exc(),"wall_s":time.perf_counter()-start}
      digest=seal(a.output,result,before,after,command);print(json.dumps({"status":result["status"],"seal_sha256":digest,"failure":str(exc)},sort_keys=True));return 2


if __name__=="__main__":raise SystemExit(main())
