#!/usr/bin/env python3
"""One-shot U8 robust-authoritative action04 first-five-second replay."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import gc
import json
import math
from pathlib import Path
import pickle
import queue
import resource
import sys
import threading
import time
import traceback
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import run_c2_direct_body_shadow_ab_pilot as pose
import run_c2_owner_bound_async_worker_u7e4_action04 as legacy
from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_uwb_calibration.adaptive_nodes import AdaptiveNodeTrustConfig
from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectNodeLinkClock
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import frozen_world_alignment
from biospur_fusion.c2_uwb_root_world.async_root_worker import RootWorkerEvent
from biospur_fusion.c2_uwb_root_world.causal_update_guard import ReachabilityClass, ReachabilityEnvelope
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import group_epoch_times_ns, validate_epoch_cadence
from biospur_fusion.c2_timing_contract import canonical_clock_global_ns
from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import (
    A_WEIGHT_POLICY, AsyncOwnerWorker, BShadowGeometryOwner, BShadowSnapshotOwner,
    BoundGroupPacket, DIAGNOSTIC_HORIZON_S, DIAGNOSTIC_MAXIMUM_IMU_GAP_S,
    DirectOwnerSequence, U3SigmaOwner, U5BSigmaOwner, decode_group, encode_group,
)
from biospur_fusion.c2_uwb_root_world.root_worker_owner_wiring import (
    PoseTagLinkOwner, RangeInformationOwner, ReferenceOwnerBundle,
)
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    DATASET, PHYSICAL_DIRECTORY, _action_bounds_global_ns, _beacon_boundary_bridges, _clock_models,
)
from biospur_fusion.c2_uwb_root_world.tight_range import ExternalRangeInformationWeights, RawRangeUpdateConfig
from biospur_fusion.ingest.v47 import decode_measurements
from biospur_fusion.root_r3.estimator import RootFilterConfig
from biospur_fusion.root_r3.models import ImuSample, RootState
from evaluate_c2_pair_bias_gate import _load_episode, _load_layout, _reference_time, _valid_slots
from run_c2_h01_tight_raw_range_fusion import _pelvis_imu
from test_c2_owner_bound_async_worker import PublicReference, packets as fixture_packets


ACTION = "04_shoulder_left"
PREFIX_NS = 5_000_000_000
GROUP_PERIOD_S = .120048
RSS_CAP_KIB = 300_000
EVIDENCE_CAP_BYTES = 10_000_000
REVISION_002 = ROOT / "logs/c2_robust_authoritative_root_u8_revision_002_20260906T205539Z"
REVISION_002_SEAL = "9e24eba8b1d5b6369709ac42fdef288cb271d1d02bf4161583011364f9adcfd2"
TRANSPORT_REVISION = ROOT / "logs/c2_robust_authoritative_root_u8_transport_20260906T220000Z"
TRANSPORT_REVISION_SEAL = "e2fca0e42acb5cc772aff163bd29679a07471d0dabb0c5e8fa87c6a236a72ba8"
EXPECTED_SOURCE_SHA256 = {
    "src/biospur_fusion/c2_uwb_root_world/owner_bound_async_worker.py":
        "85c016c87a7a29a1214e0d02d750cdbe41963f216e8edc1b60fdb3902296efda",
    "tests/test_c2_owner_bound_async_worker.py":
        "d35d478c8d82fac007d933756550c0125d191f6960064e7b0229e1b48333da37",
    "src/biospur_fusion/c2_uwb_root_world/tight_range.py":
        "6a20a5c2fae4d0da6d824cc7764c195efa553f5881589f6f72f7a0081c1da891",
}
SOURCE_FILES = tuple(ROOT / path for path in EXPECTED_SOURCE_SHA256) + (
    ROOT / "src/biospur_fusion/c2_uwb_root_world/root_worker_owner_wiring.py",
    ROOT / "src/biospur_fusion/c2_uwb_root_world/offline_unified_wiring.py",
    ROOT / "src/biospur_fusion/c2_uwb_calibration/direct_body_shadow_ab.py",
    ROOT / "tests/test_c2_robust_authoritative_root_u8_action04_runner.py",
    Path(__file__).resolve(),
)


class _LazyActionTrajectory(dict):
    def __init__(self, archive: Path) -> None:
        super().__init__(); self._archive = archive

    def __getitem__(self, key: str) -> Any:
        if key not in self:
            expected = f"{pose.EPISODES.index(ACTION):02d}"
            if key != expected: raise KeyError(key)
            value = {}
            with np.load(self._archive, allow_pickle=False) as source:
                for segment in pose.SEGMENTS:
                    prefix = f"trajectory/{key}/{segment}"
                    value[segment] = {
                        "time_root_s": np.array(source[f"{prefix}/time_root_s"]),
                        "quat_world_segment_wxyz": np.array(source[f"{prefix}/quat_world_segment_wxyz"]),
                        "mask": np.array(source[f"{prefix}/mask"], dtype=bool),
                    }
            self[key] = value
        return super().__getitem__(key)


def _verified_action_pose_inputs():
    accepted_result = json.loads(pose.ACCEPTED_RESULT.read_text())
    base_report = json.loads(pose.BASE_REPORT.read_text())
    clock_document = json.loads(pose.CLOCK_TABLE.read_text())
    frontend_manifest = json.loads(pose.FRONTEND_MANIFEST.read_text())
    accepted_path = ROOT / accepted_result["calibration_trajectory"]["path"]
    expected = {
        accepted_path: accepted_result["calibration_trajectory"]["sha256"],
        ROOT / base_report["trajectory"]["path"]: base_report["trajectory"]["sha256"],
        pose.FRONTEND_ARCHIVE: base_report["frontend"]["archive_sha256"],
        pose.FRONTEND_MANIFEST: base_report["frontend"]["manifest_sha256"],
    }
    for path, digest in expected.items():
        if pose._sha256(path) != digest: raise RuntimeError(f"sealed pose input hash mismatch: {path}")
    if (accepted_result.get("sample_rate_hz") != 200.0
            or accepted_result.get("pose_interpolation") is not False
            or accepted_result.get("mechanism_pass") is not True):
        raise RuntimeError("accepted native200 pose qualification changed")
    if base_report.get("physical_time_windows_unchanged") is not True or base_report.get("grid_period_ns") != 5_000_000:
        raise RuntimeError("native200 source clock contract changed")
    clock_source = ROOT / "src/biospur_fusion/c2_uwb_root_world/beacon_clock.py"
    if clock_document.get("source_sha256") != pose._sha256(clock_source):
        raise RuntimeError("sealed clock source binding changed")
    pelvis_clock = _clock_models(pose.CLOCK_TABLE)[pose.PELVIS_NODE]
    key = f"{pose.EPISODES.index(ACTION):02d}"
    with np.load(pose.FRONTEND_ARCHIVE, allow_pickle=False) as frontend:
        values = {}
        for suffix in ("time_us", "derived_boot_epoch", "contiguous_span_id"):
            member = f"orientation/{key}/{pose.PELVIS_NODE}/{suffix}"
            value = np.array(frontend[member], copy=True); binding = frontend_manifest["array_bindings"][member]
            if (list(value.shape) != binding["shape"] or str(value.dtype) != binding["dtype"]
                    or pose._array_sha256(value) != binding["sha256"]):
                raise RuntimeError(f"frontend pose binding failed: {member}")
            values[suffix] = value
    if not np.all(values["derived_boot_epoch"].astype(np.int64) == int(pelvis_clock.boot_epoch)):
        raise RuntimeError("pelvis boot differs from clock owner")
    lazy = _LazyActionTrajectory(accepted_path)
    with np.load(accepted_path, allow_pickle=False) as source:
        times = np.array(source[f"trajectory/{key}/pelvis/time_root_s"], dtype=float)
        valid = np.logical_and.reduce([
            np.array(source[f"trajectory/{key}/{segment}/mask"], dtype=bool) for segment in pose.SEGMENTS])
    owner = pose.DirectNative200Clock(action=ACTION,time_root_s=times,
        source_pelvis_timer_us=values["time_us"].astype(np.int64),
        source_contiguous_span_id=values["contiguous_span_id"].astype(np.int64),
        common_clock_a_ns_per_us=pelvis_clock.a_ns_per_us,
        common_clock_b_ns=pelvis_clock.b_ns,valid_mask=valid)
    support = clock_document["models"][pose.PELVIS_NODE]
    if int(owner.timer_us[0]) < int(support["first_timer_us"]) or int(owner.timer_us[-1]) > int(support["last_timer_us"]):
        raise RuntimeError("pose lies outside clock support")
    return {"trajectory": lazy}, {ACTION: owner}, {
        "actions": {ACTION: {"samples": len(times), "first_timer_us": int(owner.timer_us[0]),
            "last_timer_us": int(owner.timer_us[-1]), "strict_floor": True}},
        "accepted_path": str(accepted_path.relative_to(ROOT)), "accepted_sha256": pose._sha256(accepted_path),
        "frontend_sha256": pose._sha256(pose.FRONTEND_ARCHIVE), "clock_table_sha256": pose._sha256(pose.CLOCK_TABLE),
        "raw_uwb_opened": False, "H01_H02_opened_or_hashed": False,
        "loaded_actions": [ACTION], "deferred_trajectory_materialization": True,
    }


def _shadow_owner(group, node_clocks, provider, geometry, source_sha):
    snapshots = []
    for row in sorted(group, key=lambda value: str(value.node)):
        queries = [node_clocks[row.node].link_time_ns(event_boot_epoch=row.boot,
            strobe_us=row.strobe_us, t_round_us=float(row.t_round_us[a])) for a in _valid_slots(row)]
        snap = provider.snapshot(action=ACTION, sweep_query_ns=min(queries), root_world_m=np.zeros(3))
        snapshots.append(BShadowSnapshotOwner(str(row.node), ACTION, snap.frame, snap.pose_global_ns,
            snap.query_global_ns, snap.offsets_world_m, snap.normals_world,
            snap.joints_relative_world_m, source_sha))
    return BShadowGeometryOwner(geometry, tuple(snapshots),
        "FROZEN_C2_DISPLAY_PROXY_STRICT_PRE_LINK_POSE_ACTION04")


def _select_metric_and_context(rows, start_s, metric_stop_s, final_availability_s):
    if final_availability_s - metric_stop_s > DIAGNOSTIC_HORIZON_S + 1e-12:
        raise ValueError("diagnostic context horizon exceeded")
    metric = [row for row in rows if start_s < float(row["time_s"]) < metric_stop_s]
    submitted = [row for row in rows if start_s < float(row["time_s"]) <= final_availability_s]
    context = [row for row in submitted if float(row["time_s"]) >= metric_stop_s]
    if (not submitted
            or final_availability_s-float(submitted[-1]["time_s"]) > DIAGNOSTIC_MAXIMUM_IMU_GAP_S+1e-12):
        raise RuntimeError("diagnostic context IMU coverage incomplete")
    return metric, context, submitted


def _submit_and_collect(worker, timeline, timeout_s=30.):
    results=[]; final_box=[]; errors=[]
    def drain():
        while True:
            try: kind,value=worker._out.get(timeout=.1)
            except queue.Empty: continue
            if kind=="RESULT": results.append(pickle.loads(value))
            elif kind=="FINAL": final_box.append(value); return
            else: errors.append(RuntimeError(value)); return
    thread=threading.Thread(target=drain,name="u8-action04-output-drain",daemon=False);thread.start()
    replay_start=time.perf_counter(); first_time=timeline[0][0]
    try:
        for when,_,item in timeline:
            if errors: raise errors[0]
            wait=replay_start+when-first_time-time.perf_counter()
            if wait>0: time.sleep(wait)
            worker.submit(item)
        worker._in.put(None,timeout=5);thread.join(timeout_s)
        if thread.is_alive(): raise TimeoutError("output drain did not finish")
        if errors: raise errors[0]
        if len(final_box)!=1: raise RuntimeError("worker final record missing")
        worker._p.join(1.5)
        if worker._p.is_alive() or worker._p.exitcode!=0 or len(results)!=len(timeline):
            raise RuntimeError("worker drain failure")
        final=final_box[0];final.update({"exitcode":worker._p.exitcode,"alive":worker._p.is_alive(),
            "qsize":worker._in.qsize(),"drain_thread_alive":thread.is_alive()})
        worker._close_queues();worker._closed=True
        return results,final
    except BaseException:
        worker.abort();thread.join(2.)
        if thread.is_alive(): raise RuntimeError("output drain thread survived cleanup")
        raise


def _same_array(left, right):
    return np.array_equal(np.asarray(left),np.asarray(right),equal_nan=True)


def _exact_result(left, right):
    discrete=("kind","sequence","decision","root_reason","link_identities","link_count","guard_calls",
        "rank","authoritative_weights","authoritative_mode","authoritative_nodes","cross_covariance_status")
    if any(getattr(left,name)!=getattr(right,name) for name in discrete): return False
    for name in ("state","covariance","diagnostic_state","diagnostic_covariance",
                 "diagnostic_committed_state","diagnostic_committed_covariance"):
        a=getattr(left,name);b=getattr(right,name)
        if (a is None)!=(b is None) or (a is not None and not _same_array(a,b)): return False
    for name in ("h","sensor_r","total_r","s","effective_r","effective_s","diagnostic_predicted"):
        a=getattr(left,name);b=getattr(right,name)
        if len(a)!=len(b) or any(not _same_array(x,y) for x,y in zip(a,b)): return False
    for name in ("nis","condition","robust_nis","diagnostic_decisions"):
        if getattr(left,name)!=getattr(right,name): return False
    for name in ("diagnostic_weights","external_information_weights"):
        a=getattr(left,name);b=getattr(right,name)
        if len(a)!=len(b) or any(x[0]!=y[0] or not _same_array(x[1],y[1]) for x,y in zip(a,b)): return False
    for name in ("robust_influence_weights","irls_information_weights"):
        a=getattr(left,name);b=getattr(right,name)
        if len(a)!=len(b) or any(x[:2]!=y[:2] or not _same_array(x[2],y[2]) for x,y in zip(a,b)): return False
    if len(left.bias_snapshots)!=len(right.bias_snapshots): return False
    for a,b in zip(left.bias_snapshots,right.bias_snapshots):
        if a[0]!=b[0] or any(not _same_array(x,y) for x,y in zip(a[1:],b[1:])): return False
    return True


def _bias_audit(groups, nodes):
    previous={node:(np.zeros(8),np.full(8,np.nan)) for node in nodes};rows=[];valid=True
    for group in groups:
        selected=set(group.authoritative_nodes);changed=[]
        for node,mean,_,last in group.bias_snapshots:
            prior_mean,prior_last=previous[node]
            if not (_same_array(mean,prior_mean) and _same_array(last,prior_last)): changed.append(node)
            previous[node]=(mean,last)
        valid &= set(changed).issubset(selected)
        rows.append({"sequence":group.sequence,"selected_nodes":sorted(selected),
            "selected_count":len(selected),"changed_bias_nodes":sorted(changed)})
    return valid,rows


def _stats(values):
    values=np.asarray(tuple(values),float)
    return {"count":len(values),"mean_ms":float(np.mean(values)),"p50_ms":float(np.quantile(values,.5)),
        "p99_ms":float(np.quantile(values,.99)),"maximum_ms":float(np.max(values))}


def _command(output):
    return ("/usr/bin/time -v timeout --signal=TERM --kill-after=5s 300s env OMP_NUM_THREADS=1 "
        "OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 PYTHONPATH=src:tools:. "
        f".venv-v0/bin/python tools/run_c2_robust_authoritative_root_u8_action04.py --output {output}")


def _dry_check(output=None):
    owner,packet,_=fixture_packets();legacy.verify_seal(REVISION_002,REVISION_002_SEAL);legacy.verify_seal(TRANSPORT_REVISION,TRANSPORT_REVISION_SEAL)
    current={relative:legacy.sha256(ROOT/relative) for relative in EXPECTED_SOURCE_SHA256}
    if current!=EXPECTED_SOURCE_SHA256:raise RuntimeError("current transport source binding mismatch")
    if encode_group(decode_group(encode_group(packet)))!=encode_group(packet): raise RuntimeError("codec mismatch")
    fake=ExternalRangeInformationWeights(packet.event.payload[0].node,.01,np.ones(8),"dry")
    try: packet.__class__(owner.digest,packet.event,packet.pose_links,(fake,),packet.a_sigma_owner,packet.b_sigma_owner,packet.b_shadow_owner)
    except ValueError: pass
    else: raise RuntimeError("copied external weights accepted")
    result={"status":"U8_DRY_CHECK_PASS","raw_opened":False,"action04_opened":False,"HXX_opened":False,
        "revision_002_seal":REVISION_002_SEAL,"transport_revision_seal":TRANSPORT_REVISION_SEAL,"source_sha256":current}
    if output is not None:
        if output.exists():raise FileExistsError(output)
        output.mkdir(parents=True);legacy.write_json(output/"DRY_CHECK.json",result)
    print(json.dumps(result,sort_keys=True))
    return 0


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path);parser.add_argument("--dry-check",action="store_true");args=parser.parse_args()
    if args.dry_check:return _dry_check(args.output)
    if args.output is None:parser.error("--output is required")
    if args.output.exists():raise FileExistsError(args.output)
    args.output.mkdir(parents=True);started=time.perf_counter();command=_command(args.output);raw_opened=False
    base={"schema":"biospur.c2.robust-authoritative-root.u8.action04.v1","status":"FROZEN_BEFORE_DECODE",
        "action":ACTION,"attempt":1,"no_retry":True,"command":command,"revision_002_seal":REVISION_002_SEAL,"transport_revision_seal":TRANSPORT_REVISION_SEAL,
        "calibrated_R":False,"scientific_pass":False,"product_ready":False,"production_ready":False,
        "HXX_opened":False,"limits":{"wall_s":300,"rss_kib":RSS_CAP_KIB,"evidence_bytes":EVIDENCE_CAP_BYTES,"queue_capacity":64},
        "expected":{"imu_metric":1000,"imu_context":6,"groups":41,"sweeps":410,"links":3266}}
    try:
        legacy.verify_seal(REVISION_002,REVISION_002_SEAL);legacy.verify_seal(TRANSPORT_REVISION,TRANSPORT_REVISION_SEAL)
        before={str(path.relative_to(ROOT)):legacy.sha256(path) for path in SOURCE_FILES}
        for relative,expected in EXPECTED_SOURCE_SHA256.items():
            if before[relative]!=expected:raise RuntimeError(f"revision002 source mismatch: {relative}")
        raw=DATASET/"actions"/PHYSICAL_DIRECTORY[ACTION]/"rep_01/raw/fusion_host_raw.cobs.bin"
        raw_hash=legacy.sha256(raw);raw_opened=True
        if raw_hash!=legacy.EXPECTED_RAW_SHA256:raise RuntimeError("action04 raw hash mismatch")
        if legacy.sha256(pose.CLOCK_TABLE)!=legacy.EXPECTED_CLOCK_SHA256:raise RuntimeError("clock owner hash mismatch")
        config=RawRangeUpdateConfig(nominal_sigma_m=.12,huber_threshold_sigma=2.5,maximum_iterations=8,
            convergence_tolerance=1e-7,covariance_floor=1e-12,positive_nlos_cauchy_scale_m=.12,
            uncertainty_provenance="PROVISIONAL_UNCALIBRATED_DIAGNOSTIC_U8_ACTION04_FIRST5S")
        a_sigma=U3SigmaOwner(.0564866166214546,.10,"SEALED_U3_LAYOUT_PLUS_FLOOR_ACTION04_REFERENCE")
        b_sigma=U5BSigmaOwner(config,config.uncertainty_provenance)
        legacy.write_json(args.output/"CONTRACT.json",{**base,"source_hashes":before,"raw":{"path":str(raw.relative_to(ROOT)),"sha256":raw_hash},
            "clock_sha256":legacy.EXPECTED_CLOCK_SHA256,"channel_authority":"ROBUST_SHARED_ROOT_U8",
            "unit_comparator":"DIAGNOSTIC_ONLY_NOT_AUTHORITATIVE","a_sigma_owner":a_sigma._manifest(),"b_sigma_owner":b_sigma._manifest()})
        (args.output/"COMMAND.txt").write_text(command+"\n")

        clocks=_clock_models(pose.CLOCK_TABLE);bridges=_beacon_boundary_bridges(pose.CLOCK_TABLE)
        lo_ns,hi_ns,_=_action_bounds_global_ns(PHYSICAL_DIRECTORY[ACTION],bridges);stop_ns=lo_ns+PREFIX_NS
        if stop_ns>hi_ns:raise RuntimeError("prefix exceeds action support")
        clock_doc=json.loads(pose.CLOCK_TABLE.read_text())
        node_clocks={node:DirectNodeLinkClock(node,value.a_ns_per_us,value.b_ns,value.boot_epoch,
            int(clock_doc["models"][node]["first_timer_us"]),int(clock_doc["models"][node]["last_timer_us"])) for node,value in clocks.items()}
        anchors,delays,tag_delay,layout_sigma=_load_layout()
        if layout_sigma!=a_sigma.layout_sigma_m:raise RuntimeError("layout sigma mismatch")
        calibration=load_frozen_c2_3a();alignment,_=frozen_world_alignment(calibration)
        trajectory,pose_clocks,pose_audit=_verified_action_pose_inputs();provider=pose._PoseProvider(trajectory=trajectory,clocks=pose_clocks,alignment=alignment)
        episode=_load_episode(ACTION,clocks,bridges)
        groups=[group for group in episode["groups"] if lo_ns<=_reference_time(group,clocks)*1e9<stop_ns]
        validate_epoch_cadence([_reference_time(group,clocks)*1e9 for group in groups])
        events,decode_audit=decode_measurements(raw);imu_rows,orientation_audit=_pelvis_imu(events,clocks[pose.PELVIS_NODE],lo_ns,0.0)
        del events,episode;gc.collect()
        final_availability=max(group_epoch_times_ns(group,clocks=node_clocks)[2] for group in groups)*1e-9
        metric_rows,context_rows,imu_rows=_select_metric_and_context(imu_rows,lo_ns*1e-9,stop_ns*1e-9,final_availability)
        if (len(metric_rows),len(context_rows),len(groups))!=(1000,6,41):raise RuntimeError("frozen action04 count mismatch")

        def pose_links(group):
            values=[]
            for row in sorted(group,key=lambda value:str(value.node)):
                for anchor in range(8):
                    query=node_clocks[row.node].link_time_ns(event_boot_epoch=row.boot,strobe_us=row.strobe_us,t_round_us=float(row.t_round_us[anchor]))
                    snap=provider.snapshot(action=ACTION,sweep_query_ns=query,root_world_m=np.zeros(3))
                    if not snap.pose_global_ns<query:raise RuntimeError("pose is not strict pre-link")
                    values.append(PoseTagLinkOwner(str(row.node),anchor,query,snap.pose_global_ns,
                        snap.offsets_world_m[row.node],np.zeros(3),snap.frame,snap.frame,pose_audit["accepted_sha256"]))
            return tuple(values)
        all_pose=[pose_links(group) for group in groups]
        all_shadow=[_shadow_owner(group,node_clocks,provider,calibration.geometry,pose_audit["accepted_sha256"]) for group in groups]
        initial=RootState(lo_ns*1e-9,np.r_[np.array([np.mean(anchors[:,0]),np.mean(anchors[:,1]),.95]),np.zeros(6)],np.diag([1.]*6+[.04]*3))
        envelope=ReachabilityEnvelope(ReachabilityClass.NOMINAL,20.,100.,1000.,1.,100.,1000.,1.,1.,1.,.01,2,20.,1e8,
            "U3_OFFLINE_FUNCTIONAL_FIXTURE_NOT_HUMAN_OR_PRODUCT_QUALIFICATION")
        static_range=RangeInformationOwner(.12,.12,{node:np.ones(8) for node in sorted(node_clocks)},"UNIT_INFORMATION_WEIGHT_EXACT_U3")
        owner=ReferenceOwnerBundle(RootFilterConfig(fixed_lag_s=.10),True,initial,anchors,node_clocks,delays,tag_delay,
            all_pose[0],static_range,envelope,AdaptiveNodeTrustConfig(),"SEALED_U3_ROOT_FILTER_CONFIG","SEALED_U3_ACTION04_INITIAL_STATE",
            "SEALED_LAYOUT",f"CLOCK_TABLE_SHA256:{legacy.EXPECTED_CLOCK_SHA256}","U1_NOMINAL_ROOT_POSITION_POLICY")
        packets=[]
        for index,group in enumerate(groups):
            _,_,availability_ns=group_epoch_times_ns(group,clocks=node_clocks)
            availability_ns=canonical_clock_global_ns(availability_ns)
            packets.append(BoundGroupPacket(owner.digest,RootWorkerEvent(index,availability_ns*1e-9,"UWB",tuple(group)),
                all_pose[index],(),a_sigma,b_sigma,all_shadow[index],availability_global_ns=availability_ns))
        imu_events=[RootWorkerEvent(int(row["sequence"]),float(row["time_s"]),"IMU",ImuSample(float(row["time_s"]),
            float(row["time_s"]),row["acceleration"],row["rotation_world"],int(row["sequence"]))) for row in imu_rows]
        timeline=[(event.availability_time_s,0,event) for event in imu_events]
        timeline.extend((packet.event.availability_time_s,1,packet) for packet in packets);timeline.sort(key=lambda value:(value[0],value[1]))
        items=[value[2] for value in timeline]

        direct_engine=DirectOwnerSequence(owner);unit_engine=PublicReference(owner);direct=[];unit_effect=[]
        for item in items:
            robust=direct_engine.process(item);unit=unit_engine.process(item);direct.append(robust)
            if robust.kind=="UWB":unit_effect.append(float(np.linalg.norm(robust.state-unit.state)))
        del unit_engine;gc.collect()
        direct_groups=[value for value in direct if value.kind=="UWB"]
        bias_selected_only,bias_rows=_bias_audit(direct_groups,sorted(node_clocks))
        rejected_no_state_change=True;previous=None;rejected=0
        for value in direct:
            if value.kind=="UWB" and (not str(value.decision).startswith("ACCEPT") or value.root_reason!="ACCEPTED"):
                rejected+=1;rejected_no_state_change &= previous is not None and _same_array(value.state,previous.state) and _same_array(value.covariance,previous.covariance)
            previous=value

        worker=AsyncOwnerWorker(owner,capacity=64);cold_start_ms=worker.cold_start_ms
        actual,final=_submit_and_collect(worker,timeline);submit=tuple(worker.submit_ms);hwm=worker.hwm
        parity=len(actual)==len(direct) and all(_exact_result(left,right) for left,right in zip(actual,direct))
        actual_groups=[value for value in actual if value.kind=="UWB"]
        imu_service=[value.service_ms for value in actual if value.kind=="IMU"]
        uwb_service=[value.service_ms for value in actual_groups];publication=[value.publication_lag_ms for value in actual]
        utilization=float(np.mean(imu_service)/5.+np.mean(uwb_service)/(GROUP_PERIOD_S*1000.))
        total_links=sum(value.link_count for value in actual_groups);sweeps=sum(len(packet.event.payload) for packet in packets)
        drain=bool(final["sentinel"] and final["count"]==len(items) and final["qsize"]==0 and final["exitcode"]==0
            and not final["alive"] and not final["drain_thread_alive"])
        uncertainty_complete=all(len(value.sensor_r)==len(value.total_r)==len(value.s)==len(value.nis)
            ==len(value.effective_r)==len(value.effective_s)==len(value.robust_nis)==10
            and len(value.external_information_weights)==len(value.robust_influence_weights)==len(value.irls_information_weights)==10
            and set(value.cross_covariance_status)=={"UNAVAILABLE_NOT_PROPAGATED"} for value in actual_groups)
        source_after={str(path.relative_to(ROOT)):legacy.sha256(path) for path in SOURCE_FILES}
        parent_rss=int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss);worker_rss=int(final["rss"])
        submit_stats=_stats(submit);publication_stats=_stats(publication);imu_stats=_stats(imu_service);uwb_stats=_stats(uwb_service)
        gates={"counts":len(metric_rows)==1000 and len(context_rows)==6 and len(actual_groups)==41 and sweeps==410 and total_links==3266,
            "direct_async_exact":parity,"u1_exactly_41":sum(value.guard_calls for value in actual_groups)==41,
            "uncertainty_complete":uncertainty_complete,"unit_comparator_nonzero_not_authoritative":max(unit_effect)>0.,
            "bias_selected_only":bias_selected_only,"rejected_no_state_change":rejected_no_state_change,
            "exact_clocks_prelink_pose":all(link.pose_time_ns<link.query_time_ns for packet in packets for link in packet.pose_links),
            "no_future_loss_overflow_stale":len(actual)==len(items) and total_links==3266,
            "queue_and_drain":hwm<64 and drain,"submit_p99":submit_stats["p99_ms"]<5.,
            "publication":publication_stats["p99_ms"]<150. and publication_stats["maximum_ms"]<200.,
            "utilization":utilization<1.,"wall":time.perf_counter()-started<300.,
            "rss":parent_rss<RSS_CAP_KIB and worker_rss<RSS_CAP_KIB,"evidence_pending":True,
            "raw_unchanged":legacy.sha256(raw)==raw_hash,"source_unchanged":before==source_after}
        status="U8_ACTION04_FIRST5S_DIAGNOSTIC_PASS" if all(value for key,value in gates.items() if key!="evidence_pending") else "BLOCKED_U8_ACTION04_FIRST5S"
        result={**base,"status":status,"raw_opened":True,"raw_sha256":raw_hash,"source_hashes_before":before,"source_hashes_after":source_after,
            "raw_file_scope":"COMPLETE_ACTION04_CAPTURE_DECODED_ONCE; PROCESSING_LIMITED_TO_FIRST5S_PLUS_6_CONTEXT_IMUS",
            "metric_scope":"GROUP_REFERENCE_EPOCH_FIRST_5S_WITH_MEASURED_LINK_OVERHANG",
            "counts":{"imu_metric":len(metric_rows),"imu_context":len(context_rows),"imu_submitted":len(imu_events),"groups":len(actual_groups),
                "sweeps":sweeps,"links":total_links,"events":len(actual)},"gates":gates,"u1_calls":sum(value.guard_calls for value in actual_groups),
            "x_of_10":[len(value.authoritative_nodes) for value in actual_groups],"bias_audit":bias_rows,"rejected_groups":rejected,
            "unit_comparator_root_effect_m":{"maximum":max(unit_effect),"minimum":min(unit_effect)},"direct_async_exact":parity,
            "queue_hwm":hwm,"literal_drain":drain,"cold_start_ms":cold_start_ms,"submit_ms":submit_stats,
            "publication_ms":publication_stats,"imu_service_ms":imu_stats,"uwb_service_ms":uwb_stats,"service_utilization":utilization,
            "resources":{"parent_rss_kib":parent_rss,"worker_rss_kib":worker_rss},"decode":asdict(decode_audit),
            "orientation":orientation_audit,"pose_owner":pose_audit,"wall_s":time.perf_counter()-started,
            "calibrated_R":False,"scientific_pass":False,"product_ready":False}
        gates["evidence_pending"]=False
        legacy.write_json(args.output/"RESULT.json",result)
        legacy.write_json(args.output/"GROUPS.json",[{"sequence":value.sequence,"decision":value.decision,"root_reason":value.root_reason,
            "links":value.link_count,"x_of_10":len(value.authoritative_nodes),"nodes":list(value.authoritative_nodes),
            "u1_calls":value.guard_calls,"service_ms":value.service_ms,"publication_lag_ms":value.publication_lag_ms} for value in actual_groups])
        (args.output/"REPORT.md").write_text(f"# U8 robust-authoritative action04 first-five-second replay\n\nStatus: `{status}`. "
            f"Processed 1000 metric IMUs, 6 context IMUs, 41 groups, 410 node sweeps, and {total_links} links. "
            "The robust shared root is authoritative; the unit comparator is isolated and diagnostic only. "
            "Effective robust uncertainty remains provisional and uncalibrated. `scientific_pass=false`; `product_ready=false`.\n")
        size=sum(path.stat().st_size for path in args.output.iterdir() if path.is_file())
        if size>=EVIDENCE_CAP_BYTES:raise RuntimeError("evidence cap exceeded")
        if not all(value for key,value in gates.items() if key!="evidence_pending"):
            raise RuntimeError(f"action04 gates failed: {[key for key,value in gates.items() if key!='evidence_pending' and not value]}")
        seal=legacy.seal(args.output);print(json.dumps({"status":status,"seal_sha256":seal,"wall_s":result["wall_s"],"rss_kib":parent_rss,"bytes":size},sort_keys=True));return 0
    except BaseException as exc:
        failure={**base,"status":"BLOCKED_U8_ACTION04_FIRST5S","failure":f"{type(exc).__name__}: {exc}","traceback":traceback.format_exc(),
            "raw_opened":raw_opened,"wall_s":time.perf_counter()-started,"rss_kib":int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
            "calibrated_R":False,"scientific_pass":False,"product_ready":False}
        legacy.write_json(args.output/"FAILURE.json",failure)
        if not (args.output/"COMMAND.txt").exists():(args.output/"COMMAND.txt").write_text(command+"\n")
        seal=legacy.seal(args.output);print(json.dumps({"status":failure["status"],"failure":failure["failure"],"seal_sha256":seal},sort_keys=True));return 2


if __name__=="__main__":raise SystemExit(main())
