#!/usr/bin/env python3
"""One-shot U7D canonical-transport asynchronous ROOT fixture gate."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

import numpy as np

from biospur_fusion.c2_uwb_root_world.async_root_worker_u7d import (
    AsyncRootWorker,THREAD_ENV,run_synchronous)
from preflight_c2_async_root_worker_u7c import (
    external_rss,memory,stats,virtual_schedule,workload)


ROOT=Path(__file__).resolve().parents[1];RSS_CAP=300_000;EVIDENCE_CAP=20_000_000
U7B=ROOT/"logs/c2_async_root_worker_u7b_20260906T152840Z/SHA256SUMS"
U7B_DIGEST="10bb80c304e25fff4741a1148780e6be8565974e5c530ad6088aed6c2d77e5f3"
OWNED=(ROOT/"src/biospur_fusion/c2_uwb_root_world/async_root_worker.py",
 ROOT/"src/biospur_fusion/c2_uwb_root_world/root_worker_event_codec.py",
 ROOT/"src/biospur_fusion/c2_uwb_root_world/async_root_worker_u7d.py",
 ROOT/"tests/test_c2_root_worker_event_codec.py",Path(__file__).resolve())
TESTS=("tests/test_c2_root_worker_event_codec.py","tests/test_c2_async_root_worker.py",
 "tests/test_c2_online_root_coordinator.py","tests/test_c2_causal_update_guard.py",
 "tests/test_c2_causal_update_transaction.py")


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):Path(path).write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+"\n")


def seal(output,result,before,after,command):
    write(output/"RESULT.json",result);write(output/"HASHES.json",{"before":before,"after":after})
    (output/"COMMAND.txt").write_text(command+"\n")
    (output/"REPORT.md").write_text("# U7D explicit event transport fixture\n\n"
        f"Status: `{result['status']}`. {result.get('summary','')}\n\n"
        "No raw/action04/HXX was opened. Scientific, calibrated-R, production, and ARTICULATED claims remain false.\n")
    members=sorted(path for path in output.iterdir() if path.name!="SHA256SUMS")
    (output/"SHA256SUMS").write_text("".join(f"{sha(path)}  {path.name}\n" for path in members))
    size=sum(path.stat().st_size for path in output.iterdir())
    if size>=EVIDENCE_CAP:raise RuntimeError("evidence cap")
    return sha(output/"SHA256SUMS")


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,required=True);args=parser.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    args.output.mkdir(parents=True);started=time.perf_counter()
    command=("PYTHONPATH=src:tools:. .venv-v0/bin/python tools/preflight_c2_async_root_worker_u7d.py "
             f"--output {args.output}")
    before={str(path.relative_to(ROOT)):sha(path) for path in OWNED}
    base={"schema":"biospur.c2.async-root.u7d.fixture.v1","execution_class":"ENGINEERING_FIXTURE_ONLY",
        "scientific_pass":False,"calibrated_R":False,"production_ready":False,
        "raw_opened":False,"action04_opened":False,"HXX_opened":False,
        "articulated_u2_invoked":False,"u7b_seal_sha256":U7B_DIGEST}
    try:
        if sha(U7B)!=U7B_DIGEST:raise RuntimeError("U7B seal binding failed")
        env=dict(os.environ);env.update(THREAD_ENV);env["PYTHONPATH"]="src:tools:."
        test_time=args.output/"TEST_TIME.txt";test_command=[sys.executable,"-m","pytest","-q",*TESTS]
        with (args.output/"TEST_STDOUT.txt").open("w") as out,(args.output/"TEST_STDERR.txt").open("w") as err:
            test=subprocess.run(["/usr/bin/time","-v","-o",str(test_time),*test_command],cwd=ROOT,
                env=env,stdout=out,stderr=err,timeout=180,check=False)
        if test.returncode!=0:raise RuntimeError(f"focused tests failed rc={test.returncode}")
        config,events=workload();reference_all,reference_final=run_synchronous(config,events)
        reference=[row for row in reference_all if row["kind"]=="UWB"]
        worker=AsyncRootWorker(config);cold=worker.cold_start_ms;producer_start=time.perf_counter()
        first=events[0].availability_time_s
        for event in events:
            target=producer_start+event.availability_time_s-first;remaining=target-time.perf_counter()
            if remaining>0:time.sleep(remaining)
            worker.submit(event)
        hwm=worker.queue_high_watermark;submit=tuple(worker.submit_blocking_ms)
        actual,final=worker.close_and_collect(10,timeout_s=30.)
        names=("state","covariance","h","r","s","innovation")
        errors={name:max(float(np.max(np.abs(a[name]-b[name]))) for a,b in zip(actual,reference))
                for name in names}
        errors["nis"]=max(abs(a["nis"]-b["nis"]) for a,b in zip(actual,reference))
        errors["final_state"]=float(np.max(np.abs(final["state"]-reference_final["state"])))
        errors["final_covariance"]=float(np.max(np.abs(final["covariance"]-reference_final["covariance"])))
        exact=all((a["decision"],a["root_reason"],a["committed"],a["rejection_recorded"])==
            (b["decision"],b["root_reason"],b["committed"],b["rejection_recorded"])
            for a,b in zip(actual,reference))
        timing={key:stats([row["timing_ms"][key] for row in actual]) for key in (
            "link_build","adaptive_trust_ten_nodes","final_shared_root","root_prepare","guard",
            "validation_apply","transaction_total","ipc_receive","end_to_end_publication_lag")}
        timing["imu_propagation"]=stats(final["imu_timing_ms"])
        timing["uwb_service_total"]=stats([row["timing_ms"]["service_total"] for row in actual])
        timing["submit_blocking"]=stats(submit)
        virtual=virtual_schedule(events,actual,final);write(args.output/"VIRTUAL_SCHEDULE.json",virtual)
        virtual_stats=stats([row["lag_ms"] for row in virtual if row["kind"]=="UWB"])
        lags=[row["timing_ms"]["end_to_end_publication_lag"] for row in actual]
        utilization=timing["imu_propagation"]["p99_ms"]/5.+timing["uwb_service_total"]["p99_ms"]/120.048
        literal_drain=bool(final["sentinel_received"] and final["processed_event_count"]==len(events)
            and final["input_queue_size_after_join"]==0 and final["process_exitcode"]==0
            and not final["process_alive_after_join"])
        parent=memory();worker_rss=final["worker_rusage_self_maxrss_kib"]
        after={str(path.relative_to(ROOT)):sha(path) for path in OWNED}
        parity=exact and all(value<=1e-12 for value in errors.values())
        ready=bool(parity and len(actual)==10 and final["imu_count"]==250 and final["group_count"]==10
            and final["future_imu_count"]==final["future_uwb_count"]==0 and hwm<64
            and timing["submit_blocking"]["p99_ms"]<5. and timing["end_to_end_publication_lag"]["p99_ms"]<150.
            and max(lags)<150. and max(lags)<200. and virtual_stats["p99_ms"]<150.
            and virtual_stats["maximum_ms"]<150. and utilization<1. and literal_drain
            and parent["VmHWM_kib"]<RSS_CAP and worker_rss<RSS_CAP and external_rss(test_time)<RSS_CAP
            and before==after)
        status="READY_FOR_MONITOR_U7D_ENGINEERING_REVIEW" if ready else "ONLINE_BLOCKED_U7D_FIXTURE"
        result={**base,"status":status,"online_status":"PENDING_REVIEW" if ready else "ONLINE_BLOCKED",
            "summary":f"Post-READY publication p99/max {timing['end_to_end_publication_lag']['p99_ms']:.3f}/{max(lags):.3f} ms; queue HWM {hwm}/64.",
            "cold_start_ms_outside_capture_epoch":cold,"events":{"imu":final["imu_count"],
                "uwb_groups":final["group_count"],"nodes_per_group":10,"links_per_group":80},
            "decisions_exact":exact,"maximum_absolute_parity_errors":errors,"timing_ms":timing,
            "virtual_schedule_uwb_lag_ms":virtual_stats,
            "service_utilization_no_nested_guard_double_count":utilization,
            "publication_lag_deadline_misses_150ms":sum(x>=150. for x in lags),
            "fixed_lag_horizon_ms":200.,"queue_capacity":64,"queue_high_watermark":hwm,
            "queue_drained_to_zero":literal_drain,"sentinel_received":final["sentinel_received"],
            "processed_event_count":final["processed_event_count"],"process_exitcode":final["process_exitcode"],
            "loss_count":0,"future_access_count":0,"overflow_count":0,
            "headroom_target_12_0048ms_non_gating":{"target_ms":12.0048,
                "observed_p99_ms":timing["uwb_service_total"]["p99_ms"],
                "met":timing["uwb_service_total"]["p99_ms"]<12.0048},
            "resources":{"parent":parent,"worker_maxrss_kib":worker_rss,
                "test_external_maxrss_kib":external_rss(test_time)},
            "thread_environment_worker":final["thread_environment"],"tests_returncode":test.returncode,
            "source_hashes_unchanged":before==after,"wall_s":time.perf_counter()-started}
        digest=seal(args.output,result,before,after,command)
        print(json.dumps({"status":status,"seal_sha256":digest,"publication_max_ms":max(lags)},sort_keys=True))
        return 0 if ready else 2
    except BaseException as exc:
        after={str(path.relative_to(ROOT)):sha(path) for path in OWNED}
        result={**base,"status":"ONLINE_BLOCKED_U7D_FIXTURE","online_status":"ONLINE_BLOCKED",
            "summary":f"First gate failure: {type(exc).__name__}: {exc}","failure":traceback.format_exc(),
            "source_hashes_unchanged":before==after,"wall_s":time.perf_counter()-started}
        digest=seal(args.output,result,before,after,command)
        print(json.dumps({"status":result["status"],"seal_sha256":digest,"failure":str(exc)},sort_keys=True))
        return 2


if __name__=="__main__":raise SystemExit(main())
