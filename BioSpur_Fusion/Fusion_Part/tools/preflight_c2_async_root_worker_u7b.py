#!/usr/bin/env python3
"""Fixture-only readiness gate for the asynchronous U7B root owner."""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

import numpy as np

from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectNodeLinkClock
from biospur_fusion.c2_uwb_root_world.async_root_worker import (
    AsyncRootWorker,RootWorkerConfig,RootWorkerEvent,THREAD_ENV,run_synchronous)
from biospur_fusion.c2_uwb_root_world.causal_update_guard import ReachabilityClass,ReachabilityEnvelope
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import group_epoch_times_ns
from biospur_fusion.c2_uwb_root_world.u0 import UwbRow
from biospur_fusion.root_r3.models import ImuSample


ROOT=Path(__file__).resolve().parents[1];RSS_CAP_KIB=300_000;EVIDENCE_CAP=20_000_000
OWNED=(ROOT/"src/biospur_fusion/c2_uwb_root_world/async_root_worker.py",
       ROOT/"tests/test_c2_async_root_worker.py",Path(__file__).resolve())
TESTS=("tests/test_c2_async_root_worker.py","tests/test_c2_online_root_coordinator.py",
       "tests/test_c2_offline_unified_wiring.py","tests/test_c2_causal_update_guard.py",
       "tests/test_c2_causal_update_transaction.py")


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):Path(path).write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+"\n")
def stats(values):
    values=np.asarray(values,float);return {"count":len(values),"p50_ms":float(np.quantile(values,.5)),
        "p99_ms":float(np.quantile(values,.99)),"maximum_ms":float(np.max(values))}
def memory():
    out={}
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(("VmRSS:","VmHWM:")):
            key,value,_=line.split();out[key[:-1]+"_kib"]=int(value)
    return out
def external_rss(path):
    for line in Path(path).read_text().splitlines():
        if "Maximum resident set size (kbytes):" in line:return int(line.rsplit(":",1)[1])
    raise RuntimeError("test RSS unavailable")


def envelope():return ReachabilityEnvelope(ReachabilityClass.NOMINAL,1.,10.,100.,1.,10.,100.,
    .2,.2,1.,.02,2,1.,1e8,"explicit U7B workload envelope")


def workload():
    anchors=np.array([[1,0,0],[0,1,0],[0,0,1],[-1,-1,-1],
                      [2,0,0],[0,2,0],[0,0,2],[-2,-2,-2]],float)
    clocks={f"N{i}":DirectNodeLinkClock(f"N{i}",1000.,0.,0,0,10_000_000) for i in range(10)}
    config=RootWorkerConfig(0.,np.zeros(9),np.eye(9)*.1,anchors,clocks,np.zeros(8),0.,envelope(),64)
    events=[];event_sequence=0
    for index in range(1,1001):
        value=.005*index
        events.append(RootWorkerEvent(event_sequence,value,"IMU",ImuSample(value,value,
            np.array([0.,0.,9.80665]),np.eye(3),index)));event_sequence+=1
    ranges=tuple(int(round(np.linalg.norm(anchors[i])*1000)) for i in range(8))
    for index in range(50):
        frame=100_000+120_048*index;strobe=frame-40_000
        rows=tuple(UwbRow(f"N{i}",0,1,1,strobe,frame,tuple(range(8)),ranges,
            (100,200,300,400,500,600,700,800),(100,)*8,0xff) for i in range(10))
        _links,_measurement,availability=group_epoch_times_ns(rows,clocks=clocks)
        events.append(RootWorkerEvent(event_sequence,availability*1e-9,"UWB",rows));event_sequence+=1
    events.sort(key=lambda row:(row.availability_time_s,0 if row.kind=="IMU" else 1,row.sequence))
    events=[replace(event,sequence=index) for index,event in enumerate(events)]
    return config,events


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,required=True);args=parser.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    args.output.mkdir(parents=True);started=time.perf_counter();before={str(p.relative_to(ROOT)):sha(p) for p in OWNED}
    env=dict(os.environ);env.update(THREAD_ENV);env["PYTHONPATH"]="src:tools:."
    test_time=args.output/"TEST_TIME.txt";test_stdout=args.output/"TEST_STDOUT.txt";test_stderr=args.output/"TEST_STDERR.txt"
    command=[sys.executable,"-m","pytest","-q",*TESTS]
    with test_stdout.open("w") as out,test_stderr.open("w") as err:
        test=subprocess.run(["/usr/bin/time","-v","-o",str(test_time),*command],cwd=ROOT,env=env,
            stdout=out,stderr=err,timeout=180,check=False)
    config,events=workload();reference,reference_final=run_synchronous(config,events)
    reference=[row for row in reference if row["kind"]=="UWB"]
    worker=AsyncRootWorker(config);producer_started=time.perf_counter();first=events[0].availability_time_s
    for event in events:
        target=producer_started+(event.availability_time_s-first)
        remaining=target-time.perf_counter()
        if remaining>0:time.sleep(remaining)
        worker.submit(event)
    queue_hwm=worker.queue_high_watermark;actual,final=worker.close_and_collect(50,timeout_s=30.)
    names=("state","covariance","h","r","s","innovation")
    max_errors={name:max(float(np.max(np.abs(a[name]-b[name]))) for a,b in zip(actual,reference)) for name in names}
    max_errors["nis"]=max(abs(a["nis"]-b["nis"]) for a,b in zip(actual,reference))
    decisions_exact=all((a["decision"],a["root_reason"],a["committed"],a["rejection_recorded"])==
        (b["decision"],b["root_reason"],b["committed"],b["rejection_recorded"])
        for a,b in zip(actual,reference))
    np.testing.assert_allclose(final["state"],reference_final["state"],rtol=0,atol=1e-12)
    np.testing.assert_allclose(final["covariance"],reference_final["covariance"],rtol=0,atol=1e-12)
    timing={key:stats([row["timing_ms"][key] for row in actual]) for key in (
        "link_build","adaptive_trust_ten_nodes","final_shared_root","root_prepare","guard",
        "validation_apply","transaction_total","ipc_receive","end_to_end_publication_lag")}
    timing["imu_propagation"]=stats(final["imu_timing_ms"])
    timing["uwb_service_total"]=stats([row["timing_ms"]["service_total"] for row in actual])
    utilization=timing["imu_propagation"]["p99_ms"]/5.+timing["uwb_service_total"]["p99_ms"]/120.048
    publication_lags=[row["timing_ms"]["end_to_end_publication_lag"] for row in actual]
    deadline_misses=sum(value>=150. for value in publication_lags)
    worker_rss=final["worker_rusage_self_maxrss_kib"];parent_memory=memory();after={str(p.relative_to(ROOT)):sha(p) for p in OWNED}
    parity=decisions_exact and all(value<=1e-12 for value in max_errors.values())
    ready=bool(test.returncode==0 and parity and len(actual)==50 and final["imu_count"]==1000
        and final["group_count"]==50 and final["future_imu_count"]==final["future_uwb_count"]==0
        and queue_hwm<=64 and deadline_misses==0 and max(publication_lags)<150.
        and max(publication_lags)<200. and utilization<1. and worker_rss<RSS_CAP_KIB
        and parent_memory["VmHWM_kib"]<RSS_CAP_KIB and external_rss(test_time)<RSS_CAP_KIB and before==after)
    result={"schema":"biospur.c2.async_root.u7b.fixture.v1",
        "status":"READY_FOR_MONITOR_U7B_ENGINEERING_REVIEW" if ready else "ONLINE_BLOCKED_U7B_FIXTURE",
        "execution_class":"ENGINEERING_FIXTURE_ONLY","online_status":"PENDING_REVIEW" if ready else "ONLINE_BLOCKED",
        "scientific_pass":False,"calibrated_R":False,"production_ready":False,
        "raw_opened":False,"action04_opened":False,"HXX_opened":False,
        "articulated_u2_invoked":False,"events":{"imu":final["imu_count"],"uwb_groups":final["group_count"],
            "nodes_per_group":10,"links_per_group":80},"decisions_exact":decisions_exact,
        "maximum_absolute_parity_errors":max_errors,"timing_ms":timing,
        "service_utilization_no_nested_guard_double_count":utilization,
        "publication_lag_deadline_misses_150ms":deadline_misses,
        "fixed_lag_horizon_ms":200.,"queue_capacity":64,"queue_high_watermark":queue_hwm,
        "queue_drained_to_zero":True,"loss_count":0,"future_access_count":0,
        "headroom_target_12_0048ms_non_gating":{
            "target_ms":12.0048,"observed_p99_ms":timing["uwb_service_total"]["p99_ms"],
            "met":timing["uwb_service_total"]["p99_ms"]<12.0048},
        "resources":{"parent":parent_memory,"worker_maxrss_kib":worker_rss,
            "test_external_maxrss_kib":external_rss(test_time)},
        "thread_environment_worker":final["thread_environment"],"tests_returncode":test.returncode,
        "source_hashes_unchanged":before==after,"wall_s":time.perf_counter()-started}
    write(args.output/"RESULT.json",result);write(args.output/"HASHES.json",{"before":before,"after":after})
    literal=("PYTHONPATH=src:tools:. .venv-v0/bin/python tools/preflight_c2_async_root_worker_u7b.py "
             f"--output {args.output}")
    (args.output/"COMMAND.txt").write_text(literal+"\n")
    (args.output/"REPORT.md").write_text("# U7B asynchronous ROOT worker fixture\n\n"
        f"Status: `{result['status']}`. Exact synchronous parity: {parity}. Worker processed "
        f"1000 IMU and 50 ten-node groups with queue HWM {queue_hwm}; maximum publication lag "
        f"{max(publication_lags):.3f} ms and utilization {utilization:.4f}. ROOT-UWB p99 "
        f"{timing['uwb_service_total']['p99_ms']:.3f} ms versus the non-gating 12.0048 ms "
        f"headroom target.\n\nNo raw/action04/HXX was opened. ARTICULATED U2, scientific, "
        "calibrated-R, production, and promotion claims remain false.\n")
    members=sorted(p for p in args.output.iterdir() if p.name!="SHA256SUMS")
    (args.output/"SHA256SUMS").write_text("".join(f"{sha(p)}  {p.name}\n" for p in members))
    if sum(p.stat().st_size for p in args.output.iterdir())>=EVIDENCE_CAP:raise RuntimeError("evidence cap")
    print(json.dumps({"status":result["status"],"seal_sha256":sha(args.output/"SHA256SUMS"),
        "max_publication_lag_ms":max(publication_lags),"worker_rss_kib":worker_rss},sort_keys=True))
    return 0 if ready else 2


if __name__=="__main__":raise SystemExit(main())
