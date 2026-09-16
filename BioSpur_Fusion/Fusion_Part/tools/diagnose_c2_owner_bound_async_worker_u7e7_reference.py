#!/usr/bin/env python3
"""One-shot scalar/batch reference diagnosis for the blocked U7E7 action04 replay."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import resource
import time
import traceback

import numpy as np

import run_c2_owner_bound_async_worker_u7e4_action04 as legacy
import run_c2_owner_bound_async_worker_u7e6_action04 as replay
import run_c2_owner_bound_async_worker_u7e7_action04 as u7e7
from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_uwb_calibration.adaptive_nodes import AdaptiveNodeTrustConfig
from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectNodeLinkClock
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import frozen_world_alignment
from biospur_fusion.c2_uwb_root_world.async_root_worker import RootWorkerEvent
from biospur_fusion.c2_uwb_root_world.causal_update_guard import ReachabilityClass, ReachabilityEnvelope
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import group_epoch_times_ns, validate_epoch_cadence
from biospur_fusion.c2_timing_contract import canonical_clock_global_ns
from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import (
    A_WEIGHT_POLICY, BShadowGeometryOwner, BoundGroupPacket, DirectOwnerSequence,
    U3SigmaOwner, U5BSigmaOwner, _execute_group,
)
from biospur_fusion.c2_uwb_root_world.root_worker_owner_wiring import (
    PoseTagLinkOwner, RangeInformationOwner, ReferenceOwnerBundle,
)
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    DATASET, PHYSICAL_DIRECTORY, _action_bounds_global_ns, _beacon_boundary_bridges, _clock_models,
)
from biospur_fusion.c2_uwb_root_world.tight_range import RawRangeUpdateConfig
from biospur_fusion.ingest.v47 import decode_measurements
from biospur_fusion.root_r3.estimator import RootFilterConfig
from biospur_fusion.root_r3.models import ImuSample, RootState
from evaluate_c2_pair_bias_gate import _load_episode, _load_layout, _reference_time, _valid_slots
from run_c2_direct_body_shadow_ab_pilot import CLOCK_TABLE, PELVIS_NODE, _PoseProvider
from test_c2_owner_bound_async_worker import PublicReference


ROOT = Path(__file__).resolve().parents[1]
ACTION = "04_shoulder_left"
PREFIX_NS = 5_000_000_000
RSS_CAP_KIB = 300_000
EVIDENCE_CAP_BYTES = 20_000_000
REVISION_004 = ROOT / "logs/c2_owner_bound_async_worker_u7e7_action04_revision_004_20260906T233000Z"
REVISION_004_SHA256 = "8cb643c049620c250fe1bf1adedf706089de7b63ec9945cdd220e4acef873098"


def _maximum_error(left, right) -> float:
    a = np.asarray(left, dtype=float); b = np.asarray(right, dtype=float)
    if a.shape != b.shape:
        return float("inf")
    if a.size == 0:
        return 0.0
    return float(np.max(np.abs(a - b)))


def _update_maximum(current: dict[str, float], key: str, left, right) -> float:
    value = _maximum_error(left, right)
    current[key] = max(current.get(key, 0.0), value)
    return value


def _seal(path: Path) -> str:
    members = sorted(item for item in path.iterdir() if item.is_file() and item.name != "SHA256SUMS")
    (path / "SHA256SUMS").write_text("".join(f"{legacy.sha256(item)}  {item.name}\n" for item in members))
    return legacy.sha256(path / "SHA256SUMS")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True); started = time.perf_counter()
    command = ("timeout --signal=TERM --kill-after=5s 120s env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 "
        "MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 PYTHONPATH=src:tools:. .venv-v0/bin/python "
        f"tools/diagnose_c2_owner_bound_async_worker_u7e7_reference.py --output {args.output}")
    (args.output / "COMMAND.txt").write_text(command + "\n")
    seals = {**replay.SEALS, u7e7.BLOCKED: u7e7.BLOCKED_SHA256,
        u7e7.BLOCKED_REVISION_003: u7e7.BLOCKED_REVISION_003_SHA256,
        REVISION_004: REVISION_004_SHA256}
    base = {"schema": "biospur.c2.u7e7.reference-diagnostic.v1", "action": ACTION,
        "command": command, "prior_blocked_non_promoted": True, "raw_read_count": 1,
        "async_worker_used": False, "HXX_opened": False, "fitting": False, "threshold_change": False,
        "calibrated_R": False, "scientific_pass": False, "production_ready": False,
        "bound_seals": {str(path.relative_to(ROOT)): digest for path, digest in seals.items()},
        "limits": {"wall_s": 120, "rss_kib": RSS_CAP_KIB, "evidence_bytes": EVIDENCE_CAP_BYTES}}
    try:
        for path, digest in seals.items(): legacy.verify_seal(path, digest)
        raw = DATASET / "actions" / PHYSICAL_DIRECTORY[ACTION] / "rep_01/raw/fusion_host_raw.cobs.bin"
        raw_hash = legacy.sha256(raw)
        if raw_hash != legacy.EXPECTED_RAW_SHA256:
            raise RuntimeError("action04 raw hash mismatch")
        clocks = _clock_models(CLOCK_TABLE); bridges = _beacon_boundary_bridges(CLOCK_TABLE)
        lo_ns, hi_ns, _ = _action_bounds_global_ns(PHYSICAL_DIRECTORY[ACTION], bridges)
        stop_ns = lo_ns + PREFIX_NS
        if stop_ns > hi_ns: raise RuntimeError("prefix exceeds action support")
        clock_doc = json.loads(CLOCK_TABLE.read_text())
        node_clocks = {node: DirectNodeLinkClock(node, value.a_ns_per_us, value.b_ns, value.boot_epoch,
            int(clock_doc["models"][node]["first_timer_us"]), int(clock_doc["models"][node]["last_timer_us"]))
            for node, value in clocks.items()}
        anchors, delays, tag_delay, layout_sigma = _load_layout()
        calibration = load_frozen_c2_3a(); alignment, _ = frozen_world_alignment(calibration)
        trajectory, pose_clocks, pose_audit = u7e7._verified_action_pose_inputs()
        provider = _PoseProvider(trajectory=trajectory, clocks=pose_clocks, alignment=alignment)
        episode = _load_episode(ACTION, clocks, bridges)
        groups = [group for group in episode["groups"] if lo_ns <= _reference_time(group, clocks)*1e9 < stop_ns]
        validate_epoch_cadence([_reference_time(group, clocks)*1e9 for group in groups])
        events, decode_audit = decode_measurements(raw)
        imu_rows, orientation_audit = u7e7._pelvis_imu_release(events, clocks[PELVIS_NODE], lo_ns, 0.0)
        final_availability_s = max(group_epoch_times_ns(group, clocks=node_clocks)[2] for group in groups)*1e-9
        metric_rows, context_rows, submitted_rows = replay._select_metric_and_context_imu_rows(
            imu_rows, start_s=lo_ns*1e-9, metric_stop_s=stop_ns*1e-9,
            final_group_availability_s=final_availability_s)
        if len(metric_rows) != 1000 or len(groups) != 41:
            raise RuntimeError("frozen action04 inventory mismatch")
        config = RawRangeUpdateConfig(nominal_sigma_m=.12, huber_threshold_sigma=2.5,
            maximum_iterations=8, convergence_tolerance=1e-7, covariance_floor=1e-12,
            positive_nlos_cauchy_scale_m=.12,
            uncertainty_provenance="PROVISIONAL_UNCALIBRATED_DIAGNOSTIC_U5B_ACTION04_FIRST5S")
        a_sigma = U3SigmaOwner(.0564866166214546, .10, "SEALED_U3_LAYOUT_PLUS_FLOOR_ACTION04_REFERENCE")
        b_sigma = U5BSigmaOwner(config, config.uncertainty_provenance)
        if layout_sigma != a_sigma.layout_sigma_m: raise RuntimeError("U3 layout sigma mismatch")

        def pose_links(group):
            values = []
            for row in sorted(group, key=lambda item: str(item.node)):
                clock = node_clocks[row.node]
                for anchor in range(8):
                    query = clock.link_time_ns(event_boot_epoch=row.boot, strobe_us=row.strobe_us,
                        t_round_us=float(row.t_round_us[anchor]))
                    snap = provider.snapshot(action=ACTION, sweep_query_ns=query, root_world_m=np.zeros(3))
                    values.append(PoseTagLinkOwner(str(row.node), anchor, query, snap.pose_global_ns,
                        snap.offsets_world_m[row.node], np.zeros(3), snap.frame, snap.frame,
                        pose_audit["accepted_sha256"]))
            return tuple(values)

        all_pose = [pose_links(group) for group in groups]
        all_shadow = [replay._shadow_owner(group, node_clocks, provider, calibration.geometry,
            pose_audit["accepted_sha256"]) for group in groups]
        initial_position = np.array([np.mean(anchors[:,0]), np.mean(anchors[:,1]), .95])
        initial_state = RootState(lo_ns*1e-9, np.r_[initial_position,np.zeros(6)], np.diag([1.]*6+[.04]*3))
        envelope = ReachabilityEnvelope(ReachabilityClass.NOMINAL,20.,100.,1000.,1.,100.,1000.,1.,1.,1.,.01,2,20.,1e8,
            "U3_OFFLINE_FUNCTIONAL_FIXTURE_NOT_HUMAN_OR_PRODUCT_QUALIFICATION")
        static_range = RangeInformationOwner(.12,.12,{node:np.ones(8) for node in sorted(node_clocks)},
            "UNIT_INFORMATION_WEIGHT_EXACT_U3")
        owner = ReferenceOwnerBundle(RootFilterConfig(fixed_lag_s=.10),True,initial_state,anchors,node_clocks,
            delays,tag_delay,all_pose[0],static_range,envelope,AdaptiveNodeTrustConfig(),
            "SEALED_U3_ROOT_FILTER_CONFIG","SEALED_U3_ACTION04_INITIAL_STATE","SEALED_LAYOUT",
            f"CLOCK_TABLE_SHA256:{legacy.EXPECTED_CLOCK_SHA256}","U1_NOMINAL_ROOT_POSITION_POLICY")
        packets = []
        for index, group in enumerate(groups):
            _,_,availability_ns = group_epoch_times_ns(group, clocks=node_clocks)
            availability_ns = canonical_clock_global_ns(availability_ns)
            event = RootWorkerEvent(index, availability_ns*1e-9, "UWB", tuple(group))
            packets.append(BoundGroupPacket(owner.digest,event,all_pose[index],(),a_sigma,b_sigma,all_shadow[index],availability_global_ns=availability_ns))
        imu_events = [RootWorkerEvent(int(row["sequence"]),float(row["time_s"]),"IMU",
            ImuSample(float(row["time_s"]),float(row["time_s"]),row["acceleration"],row["rotation_world"],int(row["sequence"])))
            for row in submitted_rows]
        metric_sequences = {int(row["sequence"]) for row in metric_rows}
        timeline = [(event.availability_time_s,0,event) for event in imu_events]
        timeline.extend((packet.event.availability_time_s,1,packet) for packet in packets)
        timeline.sort(key=lambda item:(item[0],item[1]))
        u3_path = next(path for path in seals if path.name == "c2_offline_unified_u3_20260906T160900Z")
        old_u3 = [json.loads(line) for line in (u3_path/"GROUPS.jsonl").read_text().splitlines()]
        u5_path = next(path for path in seals if "tight_range_u5b_revision" in path.name)
        old_u5_rows = [json.loads(line) for line in (u5_path/"SWEEPS.jsonl").read_text().splitlines()]
        old_u5 = {(int(row["group_index"]),str(row["node"])):row for row in old_u5_rows}
        scalar = PublicReference(owner); batch = DirectOwnerSequence(owner); no_context_root = owner.make_root()
        scalar_batch_max: dict[str,float] = {}; old_u3_max_0_39 = 0.0; old_u3_group40 = 0.0
        old_u5_max = {"prior_nis":0.0,"condition":0.0,"weights":0.0}; old_mismatch_groups = set()
        first_old_mismatch = None; context_delta = None; group_index = 0
        candidate_file = (args.output/"CURRENT_REFERENCE_CANDIDATE_NON_PROMOTED.jsonl").open("w")
        delta_file = (args.output/"DELTAS.jsonl").open("w")
        for _,_,item in timeline:
            scalar_result = scalar.process(item); batch_result = batch.process(item)
            if isinstance(item, RootWorkerEvent):
                if item.sequence in metric_sequences:
                    if not no_context_root.add_imu(item.payload): raise RuntimeError("no-context metric IMU rejected")
                continue
            no_context_result = _execute_group(owner,no_context_root,item)
            old_state = np.r_[old_u3[group_index]["root_position_m"],old_u3[group_index]["root_velocity_mps"]]
            u3_delta = scalar_result.state[:6] - old_state
            old_error = _maximum_error(scalar_result.state[:6],old_state)
            if group_index < 40: old_u3_max_0_39=max(old_u3_max_0_39,old_error)
            else:
                old_u3_group40=old_error
                context_delta=scalar_result.state[:6]-no_context_result.state[:6]
            for field in ("state","covariance","nis","condition","diagnostic_state","diagnostic_covariance",
                          "diagnostic_committed_state","diagnostic_committed_covariance"):
                _update_maximum(scalar_batch_max,field,getattr(scalar_result,field),getattr(batch_result,field))
            for field in ("h","sensor_r","total_r","s","diagnostic_predicted"):
                for left,right in zip(getattr(scalar_result,field),getattr(batch_result,field)):
                    _update_maximum(scalar_batch_max,field,left,right)
            scalar_weights={node:values for node,values in scalar_result.diagnostic_weights}
            batch_weights={node:values for node,values in batch_result.diagnostic_weights}
            nodes=[]; candidate_nodes=[]
            for local,(node,scalar_vector) in enumerate(scalar_result.diagnostic_weights):
                batch_vector=batch_weights[node]; old=old_u5[(group_index,node)]
                old_weight_map=dict(zip(old["anchors"],old["weights"])); anchors=[]
                for anchor in range(8):
                    old_weight=old_weight_map.get(anchor)
                    sb=float(scalar_vector[anchor]-batch_vector[anchor])
                    so=None if old_weight is None else float(scalar_vector[anchor]-old_weight)
                    bo=None if old_weight is None else float(batch_vector[anchor]-old_weight)
                    _update_maximum(scalar_batch_max,"weights",scalar_vector[anchor],batch_vector[anchor])
                    if so is not None:
                        old_u5_max["weights"]=max(old_u5_max["weights"],abs(so))
                        if abs(so)>1e-12:
                            old_mismatch_groups.add(group_index)
                            if first_old_mismatch is None:first_old_mismatch={"group":group_index,"node":node,"field":"weights","anchor":anchor,"delta":so}
                    anchors.append({"anchor":anchor,"valid_in_old":old_weight is not None,"scalar":float(scalar_vector[anchor]),
                        "batch":float(batch_vector[anchor]),"old":old_weight,"scalar_minus_batch":sb,
                        "scalar_minus_old":so,"batch_minus_old":bo})
                nis_delta=float(scalar_result.nis[local]-old["prior_nis"])
                condition_delta=float(scalar_result.condition[local]-old["condition"])
                old_u5_max["prior_nis"]=max(old_u5_max["prior_nis"],abs(nis_delta))
                old_u5_max["condition"]=max(old_u5_max["condition"],abs(condition_delta))
                for field,value in (("prior_nis",nis_delta),("condition",condition_delta)):
                    if abs(value)>1e-12:
                        old_mismatch_groups.add(group_index)
                        if first_old_mismatch is None:first_old_mismatch={"group":group_index,"node":node,"field":field,"delta":value}
                nodes.append({"node":node,"prior_nis":{"scalar":float(scalar_result.nis[local]),"batch":float(batch_result.nis[local]),
                    "old":float(old["prior_nis"]),"scalar_minus_batch":float(scalar_result.nis[local]-batch_result.nis[local]),"scalar_minus_old":nis_delta},
                    "condition":{"scalar":float(scalar_result.condition[local]),"batch":float(batch_result.condition[local]),
                    "old":float(old["condition"]),"scalar_minus_batch":float(scalar_result.condition[local]-batch_result.condition[local]),"scalar_minus_old":condition_delta},
                    "rank":{"scalar":int(scalar_result.rank[local]),"batch":int(batch_result.rank[local]),"old":int(old["rank"])},
                    "decision":{"scalar":scalar_result.diagnostic_decisions[local],"batch":batch_result.diagnostic_decisions[local],
                    "old":[node,bool(old["accepted"]),old["update_reason"]]},"weights":anchors})
                candidate_nodes.append({"node":node,"prior_nis":float(scalar_result.nis[local]),
                    "condition":float(scalar_result.condition[local]),"rank":int(scalar_result.rank[local]),
                    "decision":scalar_result.diagnostic_decisions[local],"weights":[float(value) for value in scalar_vector]})
            exact_discrete=(scalar_result.decision==batch_result.decision and scalar_result.root_reason==batch_result.root_reason
                and scalar_result.diagnostic_decisions==batch_result.diagnostic_decisions and scalar_result.rank==batch_result.rank)
            record={"group":group_index,"availability_time_s":item.event.availability_time_s,
                "u3":{"fields":["x","y","z","vx","vy","vz"],"scalar":[float(x) for x in scalar_result.state[:6]],
                    "old":[float(x) for x in old_state],"scalar_minus_old":[float(x) for x in u3_delta],
                    "no_context":[float(x) for x in no_context_result.state[:6]],
                    "with_context_minus_without":[float(x) for x in scalar_result.state[:6]-no_context_result.state[:6]]},
                "scalar_batch_discrete_exact":exact_discrete,"nodes":nodes,
                "operation_attribution":{"scalar":"direct_shadow_evidence per valid row-local anchor",
                    "batch":"direct_shadow_evidence_batch then valid row-local anchor selection",
                    "old":"sealed U5B revision_002 output"}}
            delta_file.write(json.dumps(record,sort_keys=True,separators=(",",":"))+"\n");delta_file.flush()
            candidate_file.write(json.dumps({"group":group_index,"root_state":record["u3"]["scalar"],"nodes":candidate_nodes},
                sort_keys=True,separators=(",",":"))+"\n");candidate_file.flush()
            group_index+=1
        delta_file.close();candidate_file.close()
        scalar_batch_error=max(scalar_batch_max.values(),default=0.0)
        mismatches_only_group40=bool(old_mismatch_groups) and old_mismatch_groups=={40}
        gates={"groups_0_39_u3_exact":old_u3_max_0_39<=1e-12,"scalar_batch_exact":scalar_batch_error<=1e-12,
            "group40_context_is_nonzero":context_delta is not None and bool(np.any(np.abs(context_delta)>0)),
            "all_groups_persisted":group_index==41,"counts":len(old_u5_rows)==410 and len(metric_rows)==1000,
            "rss":int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)<RSS_CAP_KIB,
            "wall":time.perf_counter()-started<120.}
        candidate_non_promoted=all(gates.values()) and mismatches_only_group40
        status="CURRENT_CONTRACT_REFERENCE_CANDIDATE_NON_PROMOTED" if candidate_non_promoted else "BLOCKED_REFERENCE_CAUSE_UNRESOLVED"
        attribution=("OLD_SEALS_DIFFER_ONLY_AT_FINAL_GROUP_DUE_TO_POST_WINDOW_CAUSAL_CONTEXT; "
            "SCALAR_EQUALS_BATCH_SO_BATCH_OPERATION_IS_NOT_THE_CAUSE" if candidate_non_promoted else
            "CAUSE_NOT_CLOSED; NO SOURCE OR THRESHOLD CHANGE AUTHORIZED")
        result={**base,"status":status,"raw_sha256":raw_hash,"raw_opened":True,
            "counts":{"metric_imu":len(metric_rows),"context_imu":len(context_rows),"groups":group_index,"u5_rows":len(old_u5_rows)},
            "gates":gates,"old_u3_max_0_39":old_u3_max_0_39,"old_u3_group40":old_u3_group40,
            "group40_context_delta":{"fields":["x","y","z","vx","vy","vz"],"values":[float(x) for x in context_delta]},
            "scalar_batch_maximum_errors":scalar_batch_max,"scalar_batch_global_max":scalar_batch_error,
            "old_u5_maximum_errors":old_u5_max,"old_mismatch_groups":sorted(old_mismatch_groups),
            "first_old_mismatch":first_old_mismatch,"source_operation_attribution":attribution,
            "candidate_promoted":False,"async_worker_started":False,"decode":asdict(decode_audit),
            "orientation":orientation_audit,"pose_owner":pose_audit,"wall_s":time.perf_counter()-started,
            "maximum_rss_kib":int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)}
        legacy.write_json(args.output/"RESULT.json",result)
        legacy.write_json(args.output/"CONTRACT.json",{**base,"raw":{"path":str(raw.relative_to(ROOT)),"sha256":raw_hash},
            "claim":"DIAGNOSTIC_ONLY_CURRENT_REFERENCE_CANDIDATE_NEVER_PROMOTED"})
        (args.output/"REPORT.md").write_text(f"# U7E7 scalar-reference causal diagnosis\n\nStatus: `{status}`. "
            f"Groups 0-39 U3 max delta: {old_u3_max_0_39:.17g}; group 40: {old_u3_group40:.17g}. "
            f"Scalar/batch global max: {scalar_batch_error:.17g}. {attribution}.\n\n"
            "The generated reference is a non-promoted candidate. No async worker, HXX, fitting, threshold change, calibrated-R, scientific, product, or production claim was made.\n")
        if sum(item.stat().st_size for item in args.output.iterdir() if item.is_file())>=EVIDENCE_CAP_BYTES:
            raise RuntimeError("evidence cap exceeded")
        digest=_seal(args.output);print(json.dumps({"status":status,"seal_sha256":digest,"wall_s":result["wall_s"],
            "rss_kib":result["maximum_rss_kib"]},sort_keys=True));return 0 if all(gates.values()) else 2
    except BaseException as exc:
        failure={**base,"status":"BLOCKED_REFERENCE_DIAGNOSTIC","failure":f"{type(exc).__name__}: {exc}",
            "traceback":traceback.format_exc(),"wall_s":time.perf_counter()-started,
            "maximum_rss_kib":int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)}
        legacy.write_json(args.output/"FAILURE.json",failure);digest=_seal(args.output)
        print(json.dumps({"status":failure["status"],"seal_sha256":digest,"failure":str(exc)},sort_keys=True));return 2


if __name__ == "__main__":
    raise SystemExit(main())
