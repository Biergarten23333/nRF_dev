#!/usr/bin/env python3
"""No-raw production-shape performance preflight for the U7E7 worker."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import resource
import time
import traceback

import numpy as np

import biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab as shadow_module
import biospur_fusion.c2_uwb_root_world.owner_bound_async_worker as worker_module
import biospur_fusion.c2_uwb_root_world.tight_range as tight_range
import run_c2_owner_bound_async_worker_u7e4_action04 as legacy
import run_c2_owner_bound_async_worker_u7e6_action04 as replay
import run_c2_owner_bound_async_worker_u7e7_action04 as u7e7
from biospur_fusion.c2_uwb_root_world.async_root_worker import RootWorkerEvent
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import group_epoch_times_ns
from biospur_fusion.c2_timing_contract import canonical_clock_global_ns
from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import (
    AsyncOwnerWorker, BShadowGeometryOwner, BShadowSnapshotOwner, BoundGroupPacket,
    DirectOwnerSequence,
)
from biospur_fusion.c2_uwb_root_world.root_worker_owner_wiring import PoseTagLinkOwner
from biospur_fusion.c2_uwb_root_world.u0 import ClockModel
from biospur_fusion.root_r3.models import ImuSample
from test_c2_owner_bound_async_worker import packets


ROOT = Path(__file__).resolve().parents[1]
REVISION_005 = ROOT / "logs/c2_owner_bound_async_worker_u7e7_action04_revision_005_20260907T003000Z"
REVISION_005_SEAL = "f663cb99344611b9cad6a0e1d43293b8bd3146c57134e64d21a660f1d1b35490"
PROMOTION = ROOT / "logs/c2_owner_bound_async_worker_u7e7_reference_promotion_20260907T002500Z"
PROMOTION_SEAL = "f12f2cf5abbf9388b4cca0d9c48e48e12ae5fdc125eddbd3cba28df349c77049"
REVISION_002 = ROOT / "logs/c2_owner_bound_async_worker_u7e8_performance_preflight_revision_002_20260907T020000Z"
REVISION_002_SEAL = "23b6849210a929834a0ae47ec4b7a935ed0559a937ccf2273c0012eaa74fdad5"
REVISION_003 = ROOT / "logs/c2_owner_bound_async_worker_u7e8_performance_preflight_revision_003_20260907T023000Z"
REVISION_003_SEAL = "f12312a5fa42ffd97513131ac64ffd37247003a8df59a999f4bc627963b33bd4"
RSS_CAP_KIB = 300_000
EVIDENCE_CAP_BYTES = 10_000_000
GROUP_COUNT = 16
GROUP_PERIOD_S = .120048
CPU_SET = "0-7"
SOURCE_FILES = (
    ROOT / "src/biospur_fusion/c2_uwb_calibration/direct_body_shadow_ab.py",
    ROOT / "src/biospur_fusion/c2_uwb_root_world/owner_bound_async_worker.py",
    ROOT / "src/biospur_fusion/c2_uwb_root_world/tight_range.py",
    ROOT / "tests/test_c2_direct_body_shadow_ab.py",
    ROOT / "tests/test_c2_owner_bound_async_worker.py",
    Path(__file__).resolve(),
)


def _seal(path: Path) -> str:
    members = sorted(item for item in path.iterdir() if item.is_file() and item.name != "SHA256SUMS")
    (path/"SHA256SUMS").write_text("".join(f"{legacy.sha256(item)}  {item.name}\n" for item in members))
    return legacy.sha256(path/"SHA256SUMS")


def _host_provenance():
    processes=[]
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():continue
        try:command=(entry/"cmdline").read_bytes().replace(b"\0",b" ").decode(errors="replace").strip()
        except (FileNotFoundError,PermissionError,ProcessLookupError):continue
        if (entry.name in {"81139","75049","86307"}
                or "run_c2_five_calibration.py" in command
                or "audit_c2_five_physics.py" in command):
            processes.append({"pid":int(entry.name),"command":command})
    return {"time_ns":time.time_ns(),"uptime_s":float(Path("/proc/uptime").read_text().split()[0]),
        "load_average":list(os.getloadavg()),"logical_cpus":os.cpu_count(),
        "process_affinity":sorted(os.sched_getaffinity(0)),"observed_unrelated_processes":sorted(processes,key=lambda row:row["pid"])}


def _production_shape_fixture(audit_path=None):
    owner, first, _ = packets()
    base_rows = first.event.payload
    base_pose = {(link.node,link.anchor):link for link in first.pose_links}
    base_shadow = {snapshot.node:snapshot for snapshot in first.b_shadow_owner.snapshots}
    row_groups = []
    for group_index in range(GROUP_COUNT):
        delta_us = group_index * 120_048
        row_groups.append(tuple(replace(row, sequence=group_index+1, sweep=group_index+1,
            strobe_us=row.strobe_us+delta_us, frame_us=row.frame_us+delta_us)
            for row in base_rows))
    required_last = {node:0 for node in owner.clocks}
    for rows in row_groups:
        for row in rows:
            required_last[row.node] = max(required_last[row.node], int(row.frame_us),
                math.ceil(max(float(row.strobe_us)+.5*float(value) for value in row.t_round_us)))
    original_clocks = owner.clocks
    extended_clocks = {node:replace(clock,last_timer_us=required_last[node])
        for node,clock in original_clocks.items()}
    owner = replace(owner,clocks=extended_clocks,digest="")
    clock_audit = {}
    for node,clock in owner.clocks.items():
        original = original_clocks[node]
        if (clock.a_ns_per_us,clock.b_ns,clock.boot_epoch,clock.first_timer_us) != (
                original.a_ns_per_us,original.b_ns,original.boot_epoch,original.first_timer_us):
            raise RuntimeError("fixture clock changed a non-support field")
        maximum_query = max(float(row.strobe_us)+.5*float(value)
            for rows in row_groups for row in rows if row.node==node for value in row.t_round_us)
        maximum_frame = max(int(row.frame_us)
            for rows in row_groups for row in rows if row.node==node)
        maximum_required = max(float(maximum_frame),maximum_query)
        all_inside = True
        for rows in row_groups:
            for row in rows:
                if row.node != node:continue
                all_inside &= int(row.frame_us)<=clock.last_timer_us
                for value in row.t_round_us:
                    clock.link_time_ns(event_boot_epoch=row.boot,strobe_us=row.strobe_us,t_round_us=value)
        nextafter_rejected = one_us_rejected = False
        epsilon = float(np.nextafter(float(clock.last_timer_us),np.inf)-float(clock.last_timer_us))
        try:clock.link_time_ns(event_boot_epoch=clock.boot_epoch,strobe_us=clock.last_timer_us,t_round_us=2.*epsilon)
        except ValueError as exc:nextafter_rejected = str(exc)=="UWB link time is outside sealed node clock support"
        try:clock.link_time_ns(event_boot_epoch=clock.boot_epoch,strobe_us=clock.last_timer_us,t_round_us=2.)
        except ValueError as exc:one_us_rejected = str(exc)=="UWB link time is outside sealed node clock support"
        if not (all_inside and nextafter_rejected and one_us_rejected
                and clock.last_timer_us==math.ceil(maximum_required)):
            raise RuntimeError(f"fixture clock support proof failed: {node}")
        clock_audit[node]={"a_ns_per_us":clock.a_ns_per_us,"b_ns":clock.b_ns,
            "boot_epoch":clock.boot_epoch,"first_timer_us":clock.first_timer_us,
            "original_last_timer_us":original.last_timer_us,"extended_last_timer_us":clock.last_timer_us,
            "maximum_query_timer_us":maximum_query,"maximum_frame_timer_us":maximum_frame,
            "maximum_required_timer_us":maximum_required,"all_queries_inside":all_inside,
            "nextafter_over_bound_rejected":nextafter_rejected,"one_us_over_bound_rejected":one_us_rejected}
    groups = []
    for group_index,rows in enumerate(row_groups):
        poses = []
        snapshots = []
        for row in rows:
            queries = []
            for anchor in range(8):
                query = owner.clocks[row.node].link_time_ns(event_boot_epoch=row.boot,
                    strobe_us=row.strobe_us,t_round_us=float(row.t_round_us[anchor]))
                pose_time = int(query//5_000_000*5_000_000)
                original = base_pose[(row.node,anchor)]
                dt = (pose_time-original.pose_time_ns)*1e-9
                poses.append(PoseTagLinkOwner(row.node,anchor,query,pose_time,
                    original.offset_world_m+dt*original.offset_velocity_world_mps,
                    original.offset_velocity_world_mps,pose_time//5_000_000,
                    original.source_revision+group_index,original.source_sha256))
                queries.append(query)
            original_shadow = base_shadow[row.node]
            shadow_query = min(queries)
            shadow_pose = int(shadow_query//5_000_000*5_000_000)
            snapshots.append(BShadowSnapshotOwner(row.node,original_shadow.action,
                shadow_pose//5_000_000,shadow_pose,shadow_query,
                original_shadow.offsets_world_m,original_shadow.normals_world,
                original_shadow.joints_relative_world_m,original_shadow.source_sha256))
        _,_,availability_ns = group_epoch_times_ns(rows,clocks=owner.clocks)
        availability_ns = canonical_clock_global_ns(availability_ns)
        groups.append(BoundGroupPacket(owner.digest,
            RootWorkerEvent(group_index,float(availability_ns)*1e-9,"UWB",rows),
            tuple(poses),(),first.a_sigma_owner,first.b_sigma_owner,
            BShadowGeometryOwner(first.b_shadow_owner.geometry,tuple(snapshots),first.b_shadow_owner.provenance),
            availability_global_ns=availability_ns))
    exact_query_count = 0
    for packet in groups:
        by_key={(pose.node,pose.anchor):pose for pose in packet.pose_links}
        for row in packet.event.payload:
            for anchor in range(8):
                canonical=owner.clocks[row.node].link_time_ns(event_boot_epoch=row.boot,
                    strobe_us=row.strobe_us,t_round_us=float(row.t_round_us[anchor]))
                if by_key[(row.node,anchor)].query_time_ns != canonical:
                    raise RuntimeError("fixture pose query is not canonical")
                exact_query_count += 1
    if exact_query_count != 1280:
        raise RuntimeError("fixture canonical pose query count mismatch")

    proof=DirectOwnerSequence(owner)
    proof_time=owner.initial_state.time_s+.005;proof_sequence=9000
    while proof_time <= groups[0].event.availability_time_s+1e-12:
        proof.process(RootWorkerEvent(proof_sequence,proof_time,"IMU",
            ImuSample(proof_time,proof_time,np.array([0.,0.,9.80665]),np.eye(3),proof_sequence)))
        proof_time+=.005;proof_sequence+=1
    def proof_signature():
        state=proof.root.current_state;diagnostic=proof.diagnostic
        return (state.time_s,state.vector.tobytes(),state.covariance.tobytes(),
            diagnostic.state.time_s,diagnostic.state.vector.tobytes(),diagnostic.state.covariance.tobytes(),
            diagnostic.force.tobytes(),diagnostic.rotation.tobytes(),len(diagnostic.buffer),
            json.dumps({node:tracker.snapshot() for node,tracker in diagnostic.trackers.items()},sort_keys=True))
    before_bad=proof_signature();bad_poses=list(groups[0].pose_links)
    bad_poses[0]=replace(bad_poses[0],query_time_ns=bad_poses[0].query_time_ns+1.)
    bad_packet=replace(groups[0],pose_links=tuple(bad_poses),digest="")
    plus_one_rejected=False
    try:proof.process(bad_packet)
    except ValueError as exc:plus_one_rejected=str(exc)=="missing/stale exact pose link owner"
    if not plus_one_rejected or proof_signature()!=before_bad:
        raise RuntimeError("fixture +1ns pose query did not fail before mutation")
    if audit_path is not None:
        Path(audit_path).write_text(json.dumps({"schema":"biospur.c2.u7e8.fixture-clock-support.v2",
            "nodes":clock_audit,"canonical_pose_query_count":exact_query_count,
            "plus_one_ns_pose_query_rejected_before_mutation":plus_one_rejected},indent=2,sort_keys=True)+"\n")
    imu = []
    sequence = 1000
    stop = groups[-1].event.availability_time_s
    value = owner.initial_state.time_s + .005
    while value <= stop + 1e-12:
        imu.append(RootWorkerEvent(sequence,value,"IMU",
            ImuSample(value,value,np.array([0.,0.,9.80665]),np.eye(3),sequence)))
        sequence += 1; value += .005
    timeline = [(event.availability_time_s,0,event) for event in imu]
    timeline.extend((packet.event.availability_time_s,1,packet) for packet in groups)
    timeline.sort(key=lambda item:(item[0],item[1]))
    if len(timeline)<256 or len(groups)!=16 or any(len(packet.pose_links)!=80 for packet in groups):
        raise RuntimeError("production-shape fixture inventory mismatch")
    return owner, timeline


class _Instrumentation:
    def __init__(self): self.values = {}
    def wrapper(self, name, function):
        def measured(*args,**kwargs):
            started=time.perf_counter_ns()
            try:return function(*args,**kwargs)
            finally:
                row=self.values.setdefault(name,{"calls":0,"wall_ms":0.})
                row["calls"]+=1;row["wall_ms"]+=(time.perf_counter_ns()-started)*1e-6
        return measured


def _component_means(values):
    result={}
    for name,row in values.items():
        copy=dict(row);copy["mean_ms"]=float(copy["wall_ms"])/int(copy["calls"]) if copy["calls"] else None
        result[name]=copy
    return result


def _stats_with_mean(values):
    frozen=tuple(float(value) for value in values);result=legacy.stats(frozen)
    result["mean_ms"]=float(np.mean(frozen)) if frozen else None
    return result


def _instrumented_run(owner, items, *, baseline):
    instrumentation = _Instrumentation(); saved = {}
    targets = [
        (worker_module,"_dynamic_owner","dynamic_owner_validation_prep"),
        (worker_module,"solve_shared_root","a_root_solve"),
        (worker_module.tx,"execute_causal_update_transaction","a_root_transaction"),
        (worker_module,"direct_shadow_weights_batch","shadow_weights_batch"),
        (shadow_module,"direct_shadow_evidence_batch","shadow_full_evidence_batch"),
        (worker_module.CausalRawRangeDiagnosticJournal,"_targets","b_targets"),
        (worker_module.CausalRawRangeDiagnosticJournal,"_replay","b_replay"),
        (worker_module,"prepare_raw_range_update","linearization_prepare"),
        (worker_module,"update_raw_ranges","raw_range_update"),
        (worker_module,"PublishedResult","result_construction"),
        (tight_range,"linearize_raw_range_factors","raw_linearization"),
    ]
    for target,name,label in targets:
        saved[(target,name)]=getattr(target,name);setattr(target,name,instrumentation.wrapper(label,getattr(target,name)))
    original_execute=worker_module._execute_group;original_update=worker_module.update_raw_ranges
    original_shadow_weights=worker_module.direct_shadow_weights_batch
    if baseline:
        def duplicate_owner(static,root,packet,prepared_dynamic_owner=None):
            return original_execute(static,root,packet,None)
        def duplicate_linearization(*args,prepared=None,**kwargs):
            return original_update(*args,**kwargs)
        def full_evidence_weights(**kwargs):
            evidence=shadow_module.direct_shadow_evidence_batch(**kwargs)
            result=np.asarray([row.b_combined_weight for row in evidence],dtype=float)
            result.setflags(write=False);return result
        worker_module._execute_group=duplicate_owner
        worker_module.update_raw_ranges=instrumentation.wrapper("raw_range_update",duplicate_linearization)
        worker_module.direct_shadow_weights_batch=instrumentation.wrapper("shadow_weights_batch",full_evidence_weights)
    try:
        engine=DirectOwnerSequence(owner);outputs=[];started=time.perf_counter()
        for _,_,item in items:
            codec_started=time.perf_counter_ns()
            if isinstance(item,RootWorkerEvent):worker_module.encode_imu(item)
            else:worker_module.encode_group(item)
            codec=instrumentation.values.setdefault("codec",{"calls":0,"wall_ms":0.})
            codec["calls"]+=1;codec["wall_ms"]+=(time.perf_counter_ns()-codec_started)*1e-6
            outputs.append(engine.process(item))
        wall=time.perf_counter()-started
    finally:
        worker_module._execute_group=original_execute
        worker_module.direct_shadow_weights_batch=original_shadow_weights
        for (target,name),function in saved.items():setattr(target,name,function)
    return outputs,wall,_component_means(instrumentation.values)


def _parity(left,right):
    maximum=0.;exact=True
    fields=("state","covariance","nis","condition","diagnostic_state","diagnostic_covariance",
        "diagnostic_committed_state","diagnostic_committed_covariance")
    for a,b in zip(left,right):
        exact &= ((a.kind,a.sequence,a.decision,a.root_reason,a.link_identities,a.link_count,a.guard_calls,
            a.diagnostic_decisions,a.rank)==(b.kind,b.sequence,b.decision,b.root_reason,b.link_identities,
            b.link_count,b.guard_calls,b.diagnostic_decisions,b.rank))
        for field in fields:
            x=getattr(a,field);y=getattr(b,field)
            if x is not None:maximum=max(maximum,legacy.maximum_error(x,y))
        for field in ("h","sensor_r","total_r","s","diagnostic_predicted"):
            for x,y in zip(getattr(a,field),getattr(b,field)):maximum=max(maximum,legacy.maximum_error(x,y))
        for (_,x),(_,y) in zip(a.diagnostic_weights,b.diagnostic_weights):maximum=max(maximum,legacy.maximum_error(x,y))
    return exact,maximum


def main() -> int:
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,required=True);args=parser.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    args.output.mkdir(parents=True);started=time.perf_counter()
    command=(f"taskset -c {CPU_SET} timeout --signal=TERM --kill-after=5s 120s env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 "
        "MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 PYTHONPATH=src:tools:tests:. .venv-v0/bin/python "
        f"tools/preflight_c2_owner_bound_async_worker_u7e8_performance.py --output {args.output}")
    (args.output/"COMMAND.txt").write_text(command+"\n")
    base={"schema":"biospur.c2.u7e8.no-raw-performance-preflight.v1","command":command,
        "raw_opened":False,"action04_opened":False,"HXX_opened":False,"one_attempt":True,
        "revision_005_non_promoted":True,"revision_003_failure_non_promoted":True,
        "calibrated_R":False,"scientific_pass":False,
        "product_ready":False,"production_ready":False,"host_provenance":_host_provenance(),
        "limits":{"wall_s":120,"rss_kib":RSS_CAP_KIB,"evidence_bytes":EVIDENCE_CAP_BYTES}}
    try:
        legacy.verify_seal(REVISION_005,REVISION_005_SEAL);legacy.verify_seal(PROMOTION,PROMOTION_SEAL);legacy.verify_seal(REVISION_002,REVISION_002_SEAL);legacy.verify_seal(REVISION_003,REVISION_003_SEAL)
        source_hashes={str(path.relative_to(ROOT)):legacy.sha256(path) for path in SOURCE_FILES}
        owner,timeline=_production_shape_fixture(args.output/"FIXTURE_CLOCK_SUPPORT.json");items=[item for _,_,item in timeline]
        baseline,baseline_wall,baseline_stages=_instrumented_run(owner,timeline,baseline=True)
        optimized,optimized_wall,optimized_stages=_instrumented_run(owner,timeline,baseline=False)
        exact,error=_parity(baseline,optimized)
        worker=AsyncOwnerWorker(owner,capacity=64);cold=worker.cold_start_ms
        actual,final=replay._submit_and_collect_interleaved(worker,timeline,timeout_s=30.,wall_paced=True)
        async_exact,async_error=_parity(optimized,actual)
        imu=[result for result in actual if result.kind=="IMU"];uwb=[result for result in actual if result.kind=="UWB"]
        submit=_stats_with_mean(worker.submit_ms);imu_service=_stats_with_mean([value.service_ms for value in imu])
        uwb_service=_stats_with_mean([value.service_ms for value in uwb]);publication=_stats_with_mean([value.publication_lag_ms for value in actual])
        utilization=float(np.mean([value.service_ms for value in imu])/.005+np.mean([value.service_ms for value in uwb])/GROUP_PERIOD_S)/1000.
        drain=bool(final["sentinel"] and final["count"]==len(items) and final["qsize"]==0 and final["exitcode"]==0 and not final["alive"])
        baseline_linearizations=baseline_stages["raw_linearization"]["calls"]
        optimized_linearizations=optimized_stages["raw_linearization"]["calls"]
        gates={"inventory":len(items)>=256 and len(uwb)==16 and all(result.link_count==80 for result in uwb),
            "linearization_reuse":baseline_linearizations==20*GROUP_COUNT and optimized_linearizations==10*GROUP_COUNT,
            "dynamic_owner_once":baseline_stages["dynamic_owner_validation_prep"]["calls"]==2*GROUP_COUNT and optimized_stages["dynamic_owner_validation_prep"]["calls"]==GROUP_COUNT,
            "exact_parity":exact and async_exact and error<=1e-12 and async_error<=1e-12,
            "same_u1":sum(value.guard_calls for value in uwb)==GROUP_COUNT,
            "ordered_lossless_drain":drain and [(x.kind,x.sequence) for x in actual]==[(x.kind,x.sequence) for x in optimized],
            "uwb_service":uwb_service["p99_ms"]<=80. and uwb_service["maximum_ms"]<=100.,
            "utilization":utilization<=.75,"imu_service":imu_service["p99_ms"]<=1.,
            "submit":submit["p99_ms"]<5.,"publication":publication["p99_ms"]<150. and publication["maximum_ms"]<200.,
            "queue":worker.hwm<64,"source_unchanged":source_hashes=={str(path.relative_to(ROOT)):legacy.sha256(path) for path in SOURCE_FILES},"wall":time.perf_counter()-started<120,
            "rss":int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)<RSS_CAP_KIB and int(final["rss"])<RSS_CAP_KIB}
        status="U7E8_NO_RAW_PERFORMANCE_PREFLIGHT_PASS" if all(gates.values()) else "BLOCKED_U7E8_NO_RAW_PERFORMANCE_PREFLIGHT"
        result={**base,"status":status,"bound_seals":{"revision_005":REVISION_005_SEAL,"promotion":PROMOTION_SEAL,"revision_002_failure":REVISION_002_SEAL,"revision_003_failure":REVISION_003_SEAL},
            "source_sha256":source_hashes,
            "counts":{"events":len(items),"imus":len(imu),"groups":len(uwb),"nodes":len(uwb)*10,"links":sum(x.link_count for x in uwb)},
            "gates":gates,"baseline":{"wall_s":baseline_wall,"substages":baseline_stages},
            "optimized":{"wall_s":optimized_wall,"substages":optimized_stages},"parity":{"exact":exact,"maximum_error":error,"async_exact":async_exact,"async_maximum_error":async_error},
            "cold_start_ms":cold,"queue_hwm":worker.hwm,"literal_drain":drain,"submit_ms":submit,
            "async_codec_submit":{"calls":len(worker.submit_ms),"wall_ms":float(sum(worker.submit_ms))},
            "imu_service_ms":imu_service,"uwb_service_ms":uwb_service,"publication_ms":publication,
            "service_utilization":utilization,"wall_s":time.perf_counter()-started,
            "resources":{"parent_rss_kib":int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),"worker_rss_kib":int(final["rss"])}}
        (args.output/"RESULT.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
        (args.output/"REPORT.md").write_text(f"# U7E8 no-raw performance preflight\n\nStatus: `{status}`. The production-shape fixture contains {len(items)} chronological events and 16 complete 10-node/80-link groups. The optimized path used {optimized_linearizations} rather than {baseline_linearizations} initial raw linearizations, with maximum output difference {error:.3e}. Revision 005 remains non-promoted.\n")
        if sum(path.stat().st_size for path in args.output.iterdir() if path.is_file())>=EVIDENCE_CAP_BYTES:raise RuntimeError("evidence cap exceeded")
        digest=_seal(args.output);print(json.dumps({"status":status,"seal_sha256":digest,"gates":gates,"wall_s":result["wall_s"]},sort_keys=True));return 0 if all(gates.values()) else 2
    except BaseException as exc:
        failure={**base,"status":"BLOCKED_U7E8_NO_RAW_PERFORMANCE_PREFLIGHT","failure":f"{type(exc).__name__}: {exc}","traceback":traceback.format_exc(),"wall_s":time.perf_counter()-started,"rss_kib":int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)}
        (args.output/"FAILURE.json").write_text(json.dumps(failure,indent=2,sort_keys=True)+"\n");digest=_seal(args.output);print(json.dumps({"status":failure["status"],"failure":failure["failure"],"seal_sha256":digest},sort_keys=True));return 2


if __name__=="__main__":raise SystemExit(main())
