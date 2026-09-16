#!/usr/bin/env python3
"""Synthetic U7A ROOT-only real-time engineering gate."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
RSS_CAP_KIB = 300_000
EVIDENCE_CAP_BYTES = 20_000_000
THREAD_ENV = {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
              "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"}
OWNED = (
    ROOT / "src/biospur_fusion/c2_uwb_root_world/online_root_coordinator.py",
    ROOT / "tests/test_c2_online_root_coordinator.py",
    Path(__file__).resolve(),
)
TESTS = (
    "tests/test_c2_online_root_coordinator.py",
    "tests/test_c2_offline_unified_wiring.py",
    "tests/test_c2_causal_update_guard.py",
    "tests/test_c2_causal_update_transaction.py",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _memory() -> dict[str, int]:
    out = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(("VmRSS:", "VmHWM:")):
            key, value, _unit = line.split(); out[key[:-1] + "_kib"] = int(value)
    return out


def _stats(values):
    import numpy as np
    row = np.asarray(values, dtype=float)
    return {"count": len(row), "p50_ms": float(np.quantile(row, .5)),
            "p99_ms": float(np.quantile(row, .99)), "maximum_ms": float(np.max(row))}


def _benchmark_child(path: Path) -> int:
    if any(os.environ.get(k) != v for k, v in THREAD_ENV.items()):
        raise RuntimeError("thread environment not fixed before NumPy import")
    import numpy as np
    from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectNodeLinkClock
    from biospur_fusion.c2_uwb_root_world.causal_update_guard import ReachabilityClass, ReachabilityEnvelope
    from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import StrictFloorOffset
    from biospur_fusion.c2_uwb_root_world.online_root_coordinator import (
        ROOT_UWB_SERVICE_INTERVAL_MS, RootOnlyRealtimeCoordinator,
        simulate_single_thread_service)
    from biospur_fusion.c2_uwb_root_world.u0 import UwbRow
    from biospur_fusion.root_r3.estimator import CausalDelayedRootFilter, RootFilterConfig
    from biospur_fusion.root_r3.models import ImuSample, RootState

    anchors = np.array([[1,0,0],[0,1,0],[0,0,1],[-1,-1,-1],
                        [2,0,0],[0,2,0],[0,0,2],[-2,-2,-2]], float)
    def root():
        return CausalDelayedRootFilter(RootState(.05,np.zeros(9),np.eye(9)*.1),
            RootFilterConfig(fixed_lag_s=.2,nis_limit_3d=1e12),inertial=False)
    def pose(_node, query):
        value=int(query//5_000_000*5_000_000)
        if value==query: value-=5_000_000
        return StrictFloorOffset(np.zeros(3),value,query,query-value,value//5_000_000)
    envelope=ReachabilityEnvelope(ReachabilityClass.NOMINAL,1.,10.,100.,1.,10.,100.,
        .2,.2,1.,.02,2,1.,1e8,"explicit U7A performance fixture")
    clocks={f"N{i}":DirectNodeLinkClock(f"N{i}",1000.,0.,0,0,1_000_000) for i in range(10)}
    ranges=tuple(int(round(np.linalg.norm(anchors[i])*1000)) for i in range(8))
    rows=tuple(UwbRow(f"N{i}",0,1,1,60_000,100_000,tuple(range(8)),ranges,
        (100,200,300,400,500,600,700,800),(100,)*8,0xff) for i in range(10))
    group_args=dict(rows=rows,clocks=clocks,strict_floor_offset=pose,anchors_m=anchors,
        anchor_delay_m=np.zeros(8),tag_delay_m=0.,sigma_for_quality=lambda _:.1,
        nominal_envelope=envelope)

    imu_owner=RootOnlyRealtimeCoordinator(root())
    imu=[]
    for index in range(1,1001):
        sample=ImuSample(.05+.005*index,.05+.005*index,
            np.array([0.,0.,9.80665]),np.eye(3),index)
        imu.append(imu_owner.add_imu(sample))
    candidate=[]; guard=[]; transaction=[]; total=[]
    for _ in range(50):
        result=RootOnlyRealtimeCoordinator(root()).process_group(**group_args)
        candidate.append(result.candidate_ms); guard.append(result.guard_ms)
        transaction.append(result.transaction_ms); total.append(result.root_uwb_total_ms)
        if (result.candidate_calls,result.guard_calls,result.transaction_calls)!=(1,1,1):
            raise RuntimeError("call cardinality changed")
    imu_stats=_stats(imu); candidate_stats=_stats(candidate); guard_stats=_stats(guard)
    transaction_stats=_stats(transaction); total_stats=_stats(total)
    utilization=imu_stats["p99_ms"]/5.0+total_stats["p99_ms"]/120.0
    arrivals=[]; services=[]; deadlines=[]
    for index in range(400):
        arrival=index*5.; arrivals.append(arrival); services.append(imu_stats["p99_ms"])
        deadlines.append(arrival+5.)
        if index%24==0:
            arrivals.append(arrival); services.append(total_stats["p99_ms"])
            deadlines.append(arrival+ROOT_UWB_SERVICE_INTERVAL_MS)
    order=np.argsort(np.asarray(arrivals),kind="stable")
    simulation=simulate_single_thread_service(np.asarray(arrivals)[order],
        np.asarray(services)[order],np.asarray(deadlines)[order],capacity=64)
    result={"imu":imu_stats,"uwb_candidate":candidate_stats,"guard":guard_stats,
        "transaction_including_guard":transaction_stats,"root_uwb_total":total_stats,
        "service_utilization":utilization,"simulation":simulation.__dict__,
        "root_uwb_interval_ms":ROOT_UWB_SERVICE_INTERVAL_MS,"memory":_memory(),
        "rusage_self_maxrss_kib":int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        "thread_environment":{k:os.environ.get(k) for k in THREAD_ENV},
        "iterations":{"imu":1000,"uwb_groups":50,"nodes_per_group":10,"links_per_group":80}}
    _write(path,result); return 0


def _rss(path: Path) -> int:
    for line in path.read_text().splitlines():
        if "Maximum resident set size (kbytes):" in line:
            return int(line.rsplit(":",1)[1])
    raise RuntimeError("external RSS missing")


def _run(command, output, stem, env):
    timing=output/f"{stem}_TIME.txt"; stdout=output/f"{stem}_STDOUT.txt"; stderr=output/f"{stem}_STDERR.txt"
    started=time.perf_counter()
    with stdout.open("w") as out,stderr.open("w") as err:
        done=subprocess.run(["/usr/bin/time","-v","-o",str(timing),*command],cwd=ROOT,
            env=env,stdout=out,stderr=err,timeout=180,check=False)
    return {"returncode":done.returncode,"wall_s":time.perf_counter()-started,
            "external_maximum_rss_kib":_rss(timing),"command":command}


def _parent(output: Path, literal: str) -> int:
    if output.exists(): raise FileExistsError(output)
    output.mkdir(parents=True); started=time.perf_counter()
    before={str(p.relative_to(ROOT)):_sha(p) for p in OWNED}
    env=dict(os.environ); env.update(THREAD_ENV); env["PYTHONPATH"]="src:tools:."
    test=_run([sys.executable,"-m","pytest","-q",*TESTS],output,"TEST",env)
    bench_path=output/"BENCHMARK.json"
    bench_process=_run([sys.executable,str(Path(__file__).resolve()),"--benchmark-child",
        "--telemetry",str(bench_path)],output,"BENCHMARK",env) if test["returncode"]==0 else {"returncode":-1}
    bench=json.loads(bench_path.read_text()) if bench_path.exists() else {}
    after={str(p.relative_to(ROOT)):_sha(p) for p in OWNED}
    fixture_pass=bool(test["returncode"]==0 and bench_process["returncode"]==0 and before==after)
    timing_pass=bool(fixture_pass and bench["imu"]["p99_ms"]<5.
        and bench["root_uwb_total"]["p99_ms"]<12.0048
        and bench["service_utilization"]<1.
        and bench["simulation"]["deadline_misses"]==0
        and not bench["simulation"]["overflow"] and bench["simulation"]["drained_to_zero"])
    rss_pass=bool(fixture_pass and test["external_maximum_rss_kib"]<RSS_CAP_KIB
        and bench_process["external_maximum_rss_kib"]<RSS_CAP_KIB
        and bench["rusage_self_maxrss_kib"]<RSS_CAP_KIB)
    status="READY_FOR_ONE_ACTION04_FIRST5S_U7A_REPLAY" if timing_pass and rss_pass else "ONLINE_BLOCKED_U7A_FIXTURE"
    bottleneck=None
    if fixture_pass and bench["root_uwb_total"]["p99_ms"]>=12.0048:
        bottleneck="ROOT_UWB_TOTAL_P99_EXCEEDS_12_0048_MS"
    result={"schema":"biospur.c2.online_root.u7a.fixture.v1","status":status,
        "fixture_functional_pass":fixture_pass,"timing_pass":timing_pass,"rss_pass":rss_pass,
        "bottleneck":bottleneck,"benchmark":bench,"test_process":test,
        "benchmark_process":bench_process,"source_hashes_unchanged":before==after,
        "action04_opened":False,"HXX_opened":False,"articulated_u2_invoked":False,
        "online_status":"ONLINE_BLOCKED" if not timing_pass else "PENDING_ACTION04_REPLAY",
        "scientific_pass":False,"calibrated_R":False,"production_ready":False,
        "wall_s":time.perf_counter()-started}
    _write(output/"RESULT.json",result); _write(output/"HASHES.json",{"before":before,"after":after})
    (output/"COMMAND.txt").write_text(literal+"\n")
    (output/"REPORT.md").write_text("# U7A ROOT-only engineering fixture\n\n"
        f"Status: `{status}`. Functional fixture pass: {fixture_pass}; timing pass: "
        f"{timing_pass}. IMU p99 {bench.get('imu',{}).get('p99_ms')} ms; ROOT-UWB p99 "
        f"{bench.get('root_uwb_total',{}).get('p99_ms')} ms. Bottleneck: `{bottleneck}`.\n\n"
        "No raw/action04/HXX was opened. ARTICULATED U2 remains OFFLINE_ONLY/ONLINE_BLOCKED; "
        "scientific, calibrated-R, production, and promotion claims are false.\n")
    members=sorted(p for p in output.iterdir() if p.name!="SHA256SUMS")
    (output/"SHA256SUMS").write_text("".join(f"{_sha(p)}  {p.name}\n" for p in members))
    size=sum(p.stat().st_size for p in output.iterdir())
    if size>=EVIDENCE_CAP_BYTES: raise RuntimeError("evidence cap exceeded")
    return 0 if timing_pass and rss_pass else 2


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--output",type=Path)
    parser.add_argument("--benchmark-child",action="store_true"); parser.add_argument("--telemetry",type=Path)
    args=parser.parse_args()
    if args.benchmark_child:
        if args.telemetry is None:return 1
        return _benchmark_child(args.telemetry)
    if args.output is None:return 1
    literal=("PYTHONPATH=src:tools:. .venv-v0/bin/python tools/preflight_c2_online_root_u7a.py "
             f"--output {args.output}")
    return _parent(args.output.resolve(),literal)


if __name__=="__main__": raise SystemExit(main())
