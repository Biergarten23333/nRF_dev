#!/usr/bin/env python3
"""Fresh-child resource qualification for the correctness-qualified U7D.1 owner."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time
import traceback


ROOT=Path(__file__).resolve().parents[1]
RSS_CAP=300_000
MARGINAL_CAP=20_480
EVIDENCE_CAP=20_000_000
THREAD_ENV={"OMP_NUM_THREADS":"1","OPENBLAS_NUM_THREADS":"1","MKL_NUM_THREADS":"1","NUMEXPR_NUM_THREADS":"1"}
SEALS={
 ROOT/"logs/c2_root_worker_owner_wiring_correctness_u7d1_aliasfix_20260906T190000Z/SHA256SUMS":"56e823a6f1c2d2d7736d8b370345fb5da0f4408c545baf1affae624d1deb668f",
 ROOT/"logs/c2_root_worker_owner_wiring_u7d1_20260906T182000Z/SHA256SUMS":"29ba9717bcffabe141cd73b7d5602e0f76079ec29c26293f7b4b8ab6d8b5e1cd",
 ROOT/"logs/c2_root_worker_owner_wiring_correctness_u7d1_20260906T184000Z/SHA256SUMS":"bb0b2a82d4527918978294e243f1c9bbab8a5524f44a82aab5bcd667cb27030b",
}
FROZEN=(ROOT/"src/biospur_fusion/c2_uwb_root_world/root_worker_owner_wiring.py",
 ROOT/"tests/test_c2_root_worker_owner_wiring.py",Path(__file__).resolve())


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):Path(path).write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+"\n")
def proc_memory():
    out={}
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(("VmRSS:","VmHWM:")):
            key,value,_=line.split();out[key[:-1]+"_kib"]=int(value)
    return out
def usage(which):
    value=resource.getrusage(which)
    return {"maxrss_kib":int(value.ru_maxrss),"user_s":value.ru_utime,"system_s":value.ru_stime}
def external_rss(path):
    for line in path.read_text().splitlines():
        if "Maximum resident set size (kbytes):" in line:return int(line.rsplit(":",1)[1])
    raise RuntimeError("external RSS missing")


def child(path):
    checkpoints={"pre_import":proc_memory()}
    import numpy as np
    sys.path.insert(0,str(ROOT/"tests"))
    from test_c2_root_worker_owner_wiring import fixture
    from biospur_fusion.c2_uwb_root_world.root_worker_owner_wiring import OwnerBoundCoordinator,execute_direct_reference
    checkpoints["post_import"]=proc_memory();owner,packet=fixture();checkpoints["post_fixture"]=proc_memory()
    candidate=OwnerBoundCoordinator(owner).process(packet);reference=execute_direct_reference(owner,packet)
    checkpoints["post_candidate_reference"]=proc_memory()
    errors={"state":float(np.max(np.abs(candidate.state-reference.state))),
      "covariance":float(np.max(np.abs(candidate.covariance-reference.covariance))),
      "condition":abs(candidate.candidate_condition-reference.candidate_condition)}
    for field in ("state_jacobian","sensor_r_m2","r_prior_m2","s_prior_m2"):
        errors[field]=max(float(np.max(np.abs(getattr(a,field)-getattr(b,field)))) for a,b in zip(candidate.factors,reference.factors))
    errors["nis"]=max(abs(a.prior_nis-b.prior_nis) for a,b in zip(candidate.factors,reference.factors))
    exact=(candidate.decision,candidate.root_reason,candidate.link_count,candidate.guard_calls)==(
        reference.decision,reference.root_reason,reference.link_count,reference.guard_calls)
    tamper=[]
    for field in ("vector","covariance","anchors","weights","pose"):
        item,pkt=fixture();coordinator=OwnerBoundCoordinator(item);before=coordinator.root.publication_token()
        if field=="vector":object.__setattr__(item.initial_state,"vector",item.initial_state.vector+1)
        elif field=="covariance":object.__setattr__(item.initial_state,"covariance",item.initial_state.covariance*2)
        elif field=="anchors":object.__setattr__(item,"anchors_m",item.anchors_m+1e-3)
        elif field=="weights":
            changed=dict(item.range_information.weights_by_node);changed["N0"]=changed["N0"]*.9
            object.__setattr__(item.range_information,"weights_by_node",changed)
        else:object.__setattr__(item.pose_links[0],"source_revision",999)
        rejected=False
        try:coordinator.process(pkt)
        except ValueError:rejected=True
        after=coordinator.root.publication_token()
        tamper.append(rejected and before.revision==after.revision and before.digest==after.digest
            and np.array_equal(before.state.vector,after.state.vector) and np.array_equal(before.state.covariance,after.state.covariance))
    before_create,_=fixture();object.__setattr__(before_create.initial_state,"vector",before_create.initial_state.vector+1)
    precreate=False
    try:before_create.make_root()
    except ValueError:precreate=True
    checkpoints["final"]=proc_memory()
    result={"status":"PASS" if exact and all(v<=1e-12 for v in errors.values()) and all(tamper) and precreate
            and candidate.link_count==80 and candidate.guard_calls==1 else "FAIL",
      "parity_exact":exact,"maximum_absolute_errors":errors,"link_count":candidate.link_count,
      "u1_guard_calls":candidate.guard_calls,"tamper_gates":tamper,"preconstruction_tamper_rejected":precreate,
      "checkpoints":checkpoints,"rusage_self":usage(resource.RUSAGE_SELF),
      "rusage_children":usage(resource.RUSAGE_CHILDREN),"thread_environment":{k:os.environ.get(k) for k in THREAD_ENV}}
    write(path,result)
    return 0 if result["status"]=="PASS" else 2


def seal(output,result,before,after,command):
    write(output/"RESULT.json",result);write(output/"HASHES.json",{"before":before,"after":after})
    (output/"COMMAND.txt").write_text(command+"\n");(output/"REPORT.md").write_text(
      "# U7D.1 fresh-child resource qualification\n\n"+f"Status: `{result['status']}`. {result['summary']}\n\n"
      "This is resource qualification only. Raw/action04/HXX, online, scientific, calibrated-R, and production claims remain false.\n")
    members=sorted(p for p in output.iterdir() if p.name!="SHA256SUMS")
    (output/"SHA256SUMS").write_text("".join(f"{sha(p)}  {p.name}\n" for p in members))
    if sum(p.stat().st_size for p in output.iterdir())>=EVIDENCE_CAP:raise RuntimeError("evidence cap")
    return sha(output/"SHA256SUMS")


def wrapper(output):
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True);started=time.perf_counter();before={str(p.relative_to(ROOT)):sha(p) for p in FROZEN}
    command=("env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 "
      "PYTHONPATH=src:tools:. .venv-v0/bin/python tools/qualify_c2_root_worker_owner_wiring_resource_u7d1.py "
      f"--output {output}")
    base={"schema":"biospur.c2.u7d1.resource.v1","execution_class":"RESOURCE_QUALIFICATION_ONLY",
      "scientific_pass":False,"calibrated_R":False,"online_ready":False,"production_ready":False,
      "raw_opened":False,"action04_opened":False,"HXX_opened":False,"bound_seals":list(SEALS.values())}
    try:
        if any(sha(path)!=digest for path,digest in SEALS.items()):raise RuntimeError("bound seal mismatch")
        env=dict(os.environ);env.update(THREAD_ENV);env["PYTHONPATH"]="src:tools:."
        child_json=output/"CHILD_RESULT.json";child_time=output/"CHILD_TIME.txt"
        child_cmd=["/usr/bin/time","-v","-o",str(child_time),sys.executable,str(Path(__file__).resolve()),"--child",str(child_json)]
        (output/"CHILD_COMMAND.txt").write_text(" ".join(child_cmd)+"\n")
        with (output/"CHILD_STDOUT.txt").open("w") as out,(output/"CHILD_STDERR.txt").open("w") as err:
            process=subprocess.Popen(child_cmd,cwd=ROOT,env=env,stdout=out,stderr=err)
            pid=process.pid;returncode=process.wait(timeout=120)
        residual=True
        try:os.kill(pid,0)
        except ProcessLookupError:residual=False
        data=json.loads(child_json.read_text());external=external_rss(child_time);wrapper_memory=proc_memory()
        marginal=max(0,data["checkpoints"]["post_candidate_reference"]["VmHWM_kib"]-
            data["checkpoints"]["post_import"]["VmHWM_kib"])
        after={str(p.relative_to(ROOT)):sha(p) for p in FROZEN}
        peaks={"child_internal_rusage_self_kib":data["rusage_self"]["maxrss_kib"],
          "child_external_time_kib":external,"wrapper_vmhwm_kib":wrapper_memory["VmHWM_kib"],
          "wrapper_rusage_children_kib":int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss),
          "child_rusage_children_kib":data["rusage_children"]["maxrss_kib"],"owner_workload_marginal_hwm_kib":marginal}
        ready=(returncode==0 and data["status"]=="PASS" and not residual and all(v<RSS_CAP for k,v in peaks.items()
            if k!="owner_workload_marginal_hwm_kib") and marginal<=MARGINAL_CAP and before==after
            and all(data["thread_environment"][k]=="1" for k in THREAD_ENV))
        status="READY_FOR_MONITOR_U7D1_RESOURCE_REVIEW" if ready else "BLOCKED_U7D1_RESOURCE"
        result={**base,"status":status,"summary":f"child external/internal {external}/{data['rusage_self']['maxrss_kib']} KiB; marginal {marginal} KiB.",
          "child_pid":pid,"child_returncode":returncode,"no_residual_child":not residual,"resource_peaks":peaks,
          "parity_exact":data["parity_exact"],"maximum_absolute_errors":data["maximum_absolute_errors"],
          "link_count":data["link_count"],"u1_guard_calls":data["u1_guard_calls"],"tamper_gates":data["tamper_gates"],
          "checkpoints":data["checkpoints"],"source_hashes_unchanged":before==after,"wall_s":time.perf_counter()-started}
        digest=seal(output,result,before,after,command);print(json.dumps({"status":status,"seal_sha256":digest,"peaks":peaks},sort_keys=True));return 0 if ready else 2
    except BaseException as exc:
        after={str(p.relative_to(ROOT)):sha(p) for p in FROZEN};result={**base,"status":"BLOCKED_U7D1_RESOURCE",
          "summary":f"First gate failed: {type(exc).__name__}: {exc}","failure":traceback.format_exc(),"wall_s":time.perf_counter()-started}
        digest=seal(output,result,before,after,command);print(json.dumps({"status":result["status"],"seal_sha256":digest,"failure":str(exc)},sort_keys=True));return 2


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path);parser.add_argument("--child",type=Path)
    args=parser.parse_args()
    if args.child is not None:return child(args.child)
    if args.output is None:parser.error("--output required")
    return wrapper(args.output)


if __name__=="__main__":raise SystemExit(main())
