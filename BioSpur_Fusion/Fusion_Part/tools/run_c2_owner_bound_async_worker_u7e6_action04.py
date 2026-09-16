#!/usr/bin/env python3
"""One-shot U7E6 owner-bound action04 first-five-second diagnostic replay."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hmac
import json
import math
from pathlib import Path
import queue
import resource
import sys
import threading
import time
import traceback

import numpy as np

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
    U3SigmaOwner, U5BSigmaOwner, decode_group, encode_group,
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
from run_c2_direct_body_shadow_ab_pilot import CLOCK_TABLE, PELVIS_NODE, _PoseProvider, _verified_pose_inputs
from run_c2_h01_tight_raw_range_fusion import _pelvis_imu
from test_c2_owner_bound_async_worker import PublicReference, packets as fixture_packets


ROOT = Path(__file__).resolve().parents[1]
ACTION = "04_shoulder_left"
PREFIX_NS = 5_000_000_000
RSS_CAP_KIB = 300_000
EVIDENCE_CAP_BYTES = 50_000_000
U7E7_RUNNER = ROOT / "tools/run_c2_owner_bound_async_worker_u7e7_action04.py"
PRIOR_BLOCKED_NON_PROMOTED = False
CURRENT_CONTRACT_REFERENCE = None
CURRENT_CONTRACT_REFERENCE_SHA256 = None
CURRENT_CONTRACT_REFERENCE_CLASS = None
CURRENT_CONTRACT_REFERENCE_PROMOTED = False
HISTORICAL_NUMERIC_REFERENCE_STATUS = None
SEALS = {
    **legacy.SEALS,
    ROOT / "logs/c2_owner_bound_async_worker_u7e5_revision_002_20260906T194500Z":
        "3d3a5d8b70de957b31826c7b6ade8049f3d207896f8054249471dc1873b32db6",
    ROOT / "logs/c2_owner_bound_async_worker_u7e6_20260906T201000Z":
        "77c92b86d7e294398df94fe3114a0c912be2fb439aec6b7b134beda540b7ef55",
}
FILES = tuple(dict.fromkeys((*legacy.U7E_FILES,
    ROOT / "src/biospur_fusion/c2_uwb_calibration/direct_body_shadow_ab.py",
    Path(__file__).resolve())))


def _load_current_contract_reference():
    if CURRENT_CONTRACT_REFERENCE is None or CURRENT_CONTRACT_REFERENCE_SHA256 is None:
        raise RuntimeError("current-contract diagnostic reference is not bound")
    path = Path(CURRENT_CONTRACT_REFERENCE)
    if legacy.sha256(path) != CURRENT_CONTRACT_REFERENCE_SHA256:
        raise RuntimeError("current-contract diagnostic reference hash mismatch")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if len(rows) != 41:
        raise RuntimeError("current-contract diagnostic reference group count mismatch")
    node_count = 0
    for index, row in enumerate(rows):
        if (row.get("schema") != "biospur.c2.u7e7.current-contract-reference.v1"
                or row.get("group") != index
                or row.get("root_state_fields") != ["x", "y", "z", "vx", "vy", "vz"]
                or len(row.get("root_state", ())) != 6
                or not isinstance(row.get("transaction_reason"), str)
                or isinstance(row.get("link_count"), bool)
                or not isinstance(row.get("link_count"), int)):
            raise RuntimeError(f"current-contract diagnostic reference group schema mismatch: {index}")
        nodes = row.get("nodes", ())
        if len(nodes) != 10 or len({node.get("node") for node in nodes}) != 10:
            raise RuntimeError(f"current-contract diagnostic reference node inventory mismatch: {index}")
        for node in nodes:
            if (set(node) != {"node", "decision", "rank", "prior_nis", "condition", "weights"}
                    or len(node["decision"]) != 3 or node["decision"][0] != node["node"]
                    or len(node["weights"]) != 8):
                raise RuntimeError(f"current-contract diagnostic reference node schema mismatch: {index}")
        node_count += len(nodes)
    if node_count != 410:
        raise RuntimeError("current-contract diagnostic reference node count mismatch")
    return tuple(rows)


def _compare_current_contract_reference(actual_groups, reference_rows):
    if len(actual_groups) != 41 or len(reference_rows) != 41:
        raise RuntimeError("current-contract diagnostic reference comparison count mismatch")
    errors = {"root_first6": 0., "nis": 0., "condition": 0., "weights": 0.}
    for index, (actual, reference) in enumerate(zip(actual_groups, reference_rows)):
        errors["root_first6"] = max(errors["root_first6"],
            legacy.maximum_error(actual.state[:6], reference["root_state"]))
        if actual.decision != reference["transaction_reason"] or actual.link_count != reference["link_count"]:
            raise RuntimeError(f"current-contract diagnostic group discrete mismatch: {index}")
        decisions = {decision[0]: tuple(decision) for decision in actual.diagnostic_decisions}
        weights = {node: values for node, values in actual.diagnostic_weights}
        nis = {decision[0]: actual.nis[local] for local, decision in enumerate(actual.diagnostic_decisions)}
        condition = {decision[0]: actual.condition[local] for local, decision in enumerate(actual.diagnostic_decisions)}
        rank = {decision[0]: actual.rank[local] for local, decision in enumerate(actual.diagnostic_decisions)}
        expected_nodes = {node["node"]: node for node in reference["nodes"]}
        if not all(set(values) == set(expected_nodes) for values in (decisions, weights, nis, condition, rank)):
            raise RuntimeError(f"current-contract diagnostic node inventory mismatch: {index}")
        for node, expected in expected_nodes.items():
            if decisions[node] != tuple(expected["decision"]) or rank[node] != expected["rank"]:
                raise RuntimeError(f"current-contract diagnostic node discrete mismatch: {index}/{node}")
            errors["nis"] = max(errors["nis"], abs(float(nis[node]) - float(expected["prior_nis"])))
            errors["condition"] = max(errors["condition"],
                abs(float(condition[node]) - float(expected["condition"])))
            errors["weights"] = max(errors["weights"],
                legacy.maximum_error(weights[node], expected["weights"]))
    if any(value > 1e-12 for value in errors.values()):
        raise RuntimeError(f"current-contract diagnostic numeric mismatch: {errors}")
    return errors


def _select_metric_and_context_imu_rows(rows, *, start_s, metric_stop_s, final_group_availability_s):
    if not (np.isfinite(start_s) and np.isfinite(metric_stop_s) and np.isfinite(final_group_availability_s)
            and start_s < metric_stop_s <= final_group_availability_s):
        raise ValueError("diagnostic context domain invalid")
    if final_group_availability_s - metric_stop_s > DIAGNOSTIC_HORIZON_S + 1e-12:
        raise ValueError("diagnostic context horizon exceeded")
    metric = [row for row in rows if start_s < float(row["time_s"]) < metric_stop_s]
    submitted = [row for row in rows if start_s < float(row["time_s"]) <= final_group_availability_s]
    context = [row for row in submitted if float(row["time_s"]) >= metric_stop_s]
    if not submitted or final_group_availability_s - float(submitted[-1]["time_s"]) > DIAGNOSTIC_MAXIMUM_IMU_GAP_S + 1e-12:
        raise RuntimeError("diagnostic context IMU coverage incomplete")
    if any(float(row["time_s"]) > final_group_availability_s for row in submitted):
        raise RuntimeError("future diagnostic context IMU selected")
    return metric, context, submitted


def _partition_scored_and_context_results(actual, expected, *, metric_imu_sequences):
    if len(actual) != len(expected):
        raise RuntimeError("actual/reference result count mismatch")
    metric_sequences = frozenset(int(value) for value in metric_imu_sequences)
    scored = []
    context = []
    observed_metric = set()
    for candidate, target in zip(actual, expected):
        if (candidate.kind, candidate.sequence) != (target.kind, target.sequence):
            raise RuntimeError("actual/reference result identity mismatch")
        if candidate.kind == "UWB":
            scored.append((candidate, target))
        elif candidate.kind == "IMU" and candidate.sequence in metric_sequences:
            scored.append((candidate, target)); observed_metric.add(candidate.sequence)
        elif candidate.kind == "IMU":
            context.append((candidate, target))
        else:
            raise RuntimeError("unknown replay result kind")
    if observed_metric != set(metric_sequences):
        raise RuntimeError("metric IMU result inventory mismatch")
    return tuple(scored), tuple(context)


def _dry_check() -> int:
    owner, packet, _ = fixture_packets()
    assert len(packet.pose_links) == 80 and len(packet.b_shadow_owner.snapshots) == 10
    encoded = encode_group(packet)
    assert packet.information_weights == () and encode_group(decode_group(encoded)) == encoded
    try:
        replace(packet, b_shadow_owner=None, digest="")
    except ValueError:
        pass
    else:
        raise AssertionError("null B shadow owner accepted")
    fake = ExternalRangeInformationWeights(packet.event.payload[0].node, .01, np.ones(8), "dry")
    try:
        replace(packet, information_weights=(fake,), digest="")
    except ValueError:
        pass
    else:
        raise AssertionError("copied B weights accepted")
    owner.validate_integrity()
    print(json.dumps({"dry_check": "PASS", "pose_links": 80, "shadow_snapshots": 10,
                      "empty_weights": True, "codec_byte_exact": True}, sort_keys=True))
    return 0


def _shadow_owner(group, node_clocks, provider, geometry, source_sha):
    snapshots = []
    for row in sorted(group, key=lambda value: str(value.node)):
        slots = tuple(_valid_slots(row))
        queries = [node_clocks[row.node].link_time_ns(event_boot_epoch=row.boot,
            strobe_us=row.strobe_us, t_round_us=float(row.t_round_us[a])) for a in slots]
        snap = provider.snapshot(action=ACTION, sweep_query_ns=min(queries), root_world_m=np.zeros(3))
        snapshots.append(BShadowSnapshotOwner(str(row.node), ACTION, snap.frame, snap.pose_global_ns,
            snap.query_global_ns, snap.offsets_world_m, snap.normals_world,
            snap.joints_relative_world_m, source_sha))
    return BShadowGeometryOwner(geometry, tuple(snapshots),
        "FROZEN_C2_DISPLAY_PROXY_STRICT_PRE_LINK_POSE_ACTION04")


def _build_command(runner_identity: Path, output: Path) -> str:
    relative = runner_identity.resolve().relative_to(ROOT)
    return (
        "timeout --signal=TERM --kill-after=5s 300s env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 "
        "MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 PYTHONPATH=src:tools:. .venv-v0/bin/python "
        f"{relative} --output {output}"
    )


def _resolve_command(output: Path, authoritative_command=None, runner_identity=None) -> str:
    if (authoritative_command is None) != (runner_identity is None):
        raise ValueError("authoritative command and runner identity must be supplied together")
    if authoritative_command is None:
        return _build_command(Path(__file__), output)
    if not isinstance(authoritative_command, str) or not isinstance(runner_identity, Path):
        raise TypeError("invalid authoritative command ownership")
    identity = runner_identity.resolve()
    if identity != U7E7_RUNNER.resolve() or identity != Path(sys.argv[0]).resolve():
        raise ValueError("runner identity does not match executed U7E7 path")
    expected = _build_command(identity, output)
    if not hmac.compare_digest(authoritative_command, expected):
        raise ValueError("authoritative command is not deterministic")
    return authoritative_command


def _submit_and_collect_interleaved(worker, timeline, *, timeout_s=30.0, wall_paced=True):
    """Drain the bounded output queue while preserving single-producer order."""
    results = []
    final_box = []
    errors = []

    def drain():
        while True:
            try:
                kind, value = worker._out.get(timeout=.1)
            except queue.Empty:
                continue
            if kind == "RESULT":
                results.append(value)
            elif kind == "FINAL":
                final_box.append(value)
                return
            else:
                errors.append(RuntimeError(value))
                return

    thread = threading.Thread(target=drain, name="u7e7-output-drain", daemon=False)
    thread.start()
    capture_started = time.perf_counter()
    first_time = timeline[0][0] if timeline else 0.0
    try:
        for when, _, item in timeline:
            if errors:
                raise errors[0]
            if wall_paced:
                wait = capture_started + when - first_time - time.perf_counter()
                if wait > 0:
                    time.sleep(wait)
            worker.submit(item)
        worker._in.put(None, timeout=5)
        thread.join(timeout_s)
        if thread.is_alive():
            raise TimeoutError("output drain did not finish")
        if errors:
            raise errors[0]
        if len(final_box) != 1:
            raise RuntimeError("worker final record missing")
        worker._p.join(1.5)
        if worker._p.is_alive() or worker._p.exitcode != 0 or len(results) != len(timeline):
            raise RuntimeError("worker drain failure")
        qsize = worker._in.qsize()
        final = final_box[0]
        final.update({"exitcode": worker._p.exitcode, "alive": worker._p.is_alive(),
                      "qsize": qsize, "drain_thread_alive": thread.is_alive()})
        worker._close_queues(); worker._closed = True
        return results, final
    except BaseException:
        worker.abort()
        thread.join(2.0)
        if thread.is_alive():
            raise RuntimeError("output drain thread survived cleanup")
        raise


def main(*, authoritative_command=None, runner_identity=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-check", action="store_true")
    args = parser.parse_args()
    if args.dry_check:
        return _dry_check()
    if args.output is None:
        parser.error("--output is required")
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    started = time.perf_counter()
    command = _resolve_command(args.output, authoritative_command, runner_identity)
    before = {str(path.relative_to(ROOT)): legacy.sha256(path) for path in FILES}
    raw = DATASET / "actions" / PHYSICAL_DIRECTORY[ACTION] / "rep_01/raw/fusion_host_raw.cobs.bin"
    base = {
        "execution_class": "OFFLINE_REFERENCE_PARITY_AND_REALTIME_REPLAY_DIAGNOSTIC",
        "status": "FROZEN_BEFORE_DECODE", "action": ACTION, "no_retry": True,
        "channel_a": {"weight_policy": A_WEIGHT_POLICY, "sigma_policy": "U3_LAYOUT_PLUS_FLOOR",
                      "layout_sigma_m": .0564866166214546, "floor_sigma_m": .10},
        "channel_b": {"sigma_policy": "U5B_QUALITY_ONLY", "feeds_channel_a": False,
                      "shadow_owner": "CAUSALLY_RECONSTRUCTED_EXACT_BATCH",
                      "uncertainty": "PROVISIONAL_UNCALIBRATED_OBSERVATION_ONLY"},
        "calibrated_R": False, "scientific_pass": False, "production_ready": False,
        "product_ready": False, "online_ready": False, "HXX_opened": False,
        "prior_blocked_non_promoted": bool(PRIOR_BLOCKED_NON_PROMOTED),
        "current_contract_reference": {
            "path": None if CURRENT_CONTRACT_REFERENCE is None else str(Path(CURRENT_CONTRACT_REFERENCE).relative_to(ROOT)),
            "sha256": CURRENT_CONTRACT_REFERENCE_SHA256,
            "class": CURRENT_CONTRACT_REFERENCE_CLASS,
            "candidate_promoted": bool(CURRENT_CONTRACT_REFERENCE_PROMOTED),
            "historical_numeric_references": HISTORICAL_NUMERIC_REFERENCE_STATUS,
        },
        "limits": {"wall_s": 300, "rss_kib_per_process": RSS_CAP_KIB,
                   "evidence_bytes": EVIDENCE_CAP_BYTES, "queue_capacity": 64},
        "expected": {"imu": 1000, "groups": 41, "sweeps": 410, "valid_links": 3266},
        "bound_seals": {str(path.relative_to(ROOT)): digest for path, digest in SEALS.items()},
        "command": command,
    }
    try:
        for path, digest in SEALS.items():
            legacy.verify_seal(path, digest)
        if legacy.sha256(CLOCK_TABLE) != legacy.EXPECTED_CLOCK_SHA256:
            raise RuntimeError("clock owner hash mismatch")
        raw_hash = legacy.sha256(raw)
        if raw_hash != legacy.EXPECTED_RAW_SHA256:
            raise RuntimeError("action04 raw hash mismatch")
        config = RawRangeUpdateConfig(nominal_sigma_m=.12, huber_threshold_sigma=2.5,
            maximum_iterations=8, convergence_tolerance=1e-7, covariance_floor=1e-12,
            positive_nlos_cauchy_scale_m=.12,
            uncertainty_provenance="PROVISIONAL_UNCALIBRATED_DIAGNOSTIC_U5B_ACTION04_FIRST5S")
        a_sigma = U3SigmaOwner(.0564866166214546, .10, "SEALED_U3_LAYOUT_PLUS_FLOOR_ACTION04_REFERENCE")
        b_sigma = U5BSigmaOwner(config, config.uncertainty_provenance)
        legacy.write_json(args.output / "CONTRACT.json", {**base, "raw": {
            "path": str(raw.relative_to(ROOT)), "sha256": raw_hash,
            "scope": "COMPLETE_ACTION04_CAPTURE_DECODED; METRICS_FIRST_5S_GROUP_REFERENCE"},
            "clock_sha256": legacy.EXPECTED_CLOCK_SHA256, "source_hashes": before,
            "a_sigma_owner": a_sigma._manifest(), "b_sigma_owner": b_sigma._manifest()})
        (args.output / "COMMAND.txt").write_text(command + "\n")

        clocks = _clock_models(CLOCK_TABLE); bridges = _beacon_boundary_bridges(CLOCK_TABLE)
        lo_ns, hi_ns, _ = _action_bounds_global_ns(PHYSICAL_DIRECTORY[ACTION], bridges)
        stop_ns = lo_ns + PREFIX_NS
        if stop_ns > hi_ns: raise RuntimeError("prefix exceeds action support")
        clock_doc = json.loads(CLOCK_TABLE.read_text())
        node_clocks = {node: DirectNodeLinkClock(node, value.a_ns_per_us, value.b_ns, value.boot_epoch,
            int(clock_doc["models"][node]["first_timer_us"]), int(clock_doc["models"][node]["last_timer_us"]))
            for node, value in clocks.items()}
        anchors, delays, tag_delay, layout_sigma = _load_layout()
        if layout_sigma != a_sigma.layout_sigma_m: raise RuntimeError("U3 layout sigma mismatch")
        calibration = load_frozen_c2_3a(); alignment, _ = frozen_world_alignment(calibration)
        trajectory, pose_clocks, pose_audit = _verified_pose_inputs()
        provider = _PoseProvider(trajectory=trajectory, clocks=pose_clocks, alignment=alignment)
        episode = _load_episode(ACTION, clocks, bridges)
        groups = [g for g in episode["groups"] if lo_ns <= _reference_time(g, clocks)*1e9 < stop_ns]
        validate_epoch_cadence([_reference_time(g, clocks)*1e9 for g in groups])
        events, decode_audit = decode_measurements(raw)
        imu_rows, orientation_audit = _pelvis_imu(events, clocks[PELVIS_NODE], lo_ns, 0.0)
        final_group_availability_s = max(group_epoch_times_ns(group, clocks=node_clocks)[2] for group in groups) * 1e-9
        metric_imu_rows, context_imu_rows, imu_rows = _select_metric_and_context_imu_rows(
            imu_rows, start_s=lo_ns*1e-9, metric_stop_s=stop_ns*1e-9,
            final_group_availability_s=final_group_availability_s)
        if len(metric_imu_rows) != 1000 or len(groups) != 41: raise RuntimeError("frozen action04 count mismatch")

        def pose_links(group):
            values=[]
            for row in sorted(group,key=lambda x:str(x.node)):
                clock=node_clocks[row.node]
                for anchor in range(8):
                    query=clock.link_time_ns(event_boot_epoch=row.boot,strobe_us=row.strobe_us,t_round_us=float(row.t_round_us[anchor]))
                    snap=provider.snapshot(action=ACTION,sweep_query_ns=query,root_world_m=np.zeros(3))
                    values.append(PoseTagLinkOwner(str(row.node),anchor,query,snap.pose_global_ns,
                        snap.offsets_world_m[row.node],np.zeros(3),snap.frame,snap.frame,pose_audit["accepted_sha256"]))
            return tuple(values)

        all_pose=[pose_links(g) for g in groups]
        all_shadow=[_shadow_owner(g,node_clocks,provider,calibration.geometry,pose_audit["accepted_sha256"]) for g in groups]
        initial_position=np.array([np.mean(anchors[:,0]),np.mean(anchors[:,1]),.95])
        initial_state=RootState(lo_ns*1e-9,np.r_[initial_position,np.zeros(6)],np.diag([1.]*6+[.04]*3))
        envelope=ReachabilityEnvelope(ReachabilityClass.NOMINAL,20.,100.,1000.,1.,100.,1000.,1.,1.,1.,.01,2,20.,1e8,
            "U3_OFFLINE_FUNCTIONAL_FIXTURE_NOT_HUMAN_OR_PRODUCT_QUALIFICATION")
        static_range=RangeInformationOwner(.12,.12,{node:np.ones(8) for node in sorted(node_clocks)},
            "UNIT_INFORMATION_WEIGHT_EXACT_U3")
        owner=ReferenceOwnerBundle(RootFilterConfig(fixed_lag_s=.10),True,initial_state,anchors,node_clocks,
            delays,tag_delay,all_pose[0],static_range,envelope,AdaptiveNodeTrustConfig(),
            "SEALED_U3_ROOT_FILTER_CONFIG","SEALED_U3_ACTION04_INITIAL_STATE","SEALED_LAYOUT",
            f"CLOCK_TABLE_SHA256:{legacy.EXPECTED_CLOCK_SHA256}","U1_NOMINAL_ROOT_POSITION_POLICY")
        packets=[]
        for index,group in enumerate(groups):
            _,_,availability_ns=group_epoch_times_ns(group,clocks=node_clocks)
            availability_ns=canonical_clock_global_ns(availability_ns)
            event=RootWorkerEvent(index,availability_ns*1e-9,"UWB",tuple(group))
            packets.append(BoundGroupPacket(owner.digest,event,all_pose[index],(),a_sigma,b_sigma,all_shadow[index],availability_global_ns=availability_ns))
        imu_events=[RootWorkerEvent(int(r["sequence"]),float(r["time_s"]),"IMU",
            ImuSample(float(r["time_s"]),float(r["time_s"]),r["acceleration"],r["rotation_world"],int(r["sequence"]))) for r in imu_rows]
        timeline=[(x.availability_time_s,0,x) for x in imu_events]
        timeline.extend((p.event.availability_time_s,1,p) for p in packets);timeline.sort(key=lambda x:(x[0],x[1]))
        items=[x[2] for x in timeline]

        reference=PublicReference(owner); expected=[reference.process(item) for item in items]
        expected_groups=[x for x in expected if x.kind=="UWB"]
        # This immutable reference belongs to the current context-tail contract.
        # Older U3/U5 numeric artifacts remain bound as history but are non-gating.
        reference_rows = _load_current_contract_reference()
        reference_errors = _compare_current_contract_reference(expected_groups, reference_rows)

        worker=AsyncOwnerWorker(owner,capacity=64);cold=worker.cold_start_ms
        actual,final=_submit_and_collect_interleaved(worker,timeline,timeout_s=30.,wall_paced=True)
        hwm=worker.hwm;submit=tuple(worker.submit_ms)
        metric_imu_sequences = {int(row["sequence"]) for row in metric_imu_rows}
        scored_pairs, context_pairs = _partition_scored_and_context_results(
            actual, expected, metric_imu_sequences=metric_imu_sequences)
        keys=("state","covariance","h","sensor_r","total_r","s","nis","condition","prediction","weights","bias_mean","bias_variance","node_state","diagnostic_state","diagnostic_covariance")
        errors={k:0. for k in keys};exact=True
        for candidate,target in scored_pairs:
            exact &= (candidate.kind,candidate.sequence,candidate.decision,candidate.root_reason,candidate.link_identities,candidate.link_count,candidate.guard_calls,candidate.diagnostic_decisions,candidate.rank)==(target.kind,target.sequence,target.decision,target.root_reason,target.link_identities,target.link_count,target.guard_calls,target.diagnostic_decisions,target.rank)
            for field in ("state","covariance","nis","condition"):
                errors[field]=max(errors[field],legacy.maximum_error(getattr(candidate,field),getattr(target,field)))
            for field in ("h","sensor_r","total_r","s"):
                for left,right in zip(getattr(candidate,field),getattr(target,field)): errors[field]=max(errors[field],legacy.maximum_error(left,right))
            for left,right in zip(candidate.diagnostic_predicted,target.diagnostic_predicted): errors["prediction"]=max(errors["prediction"],legacy.maximum_error(left,right))
            for (_,left),(_,right) in zip(candidate.diagnostic_weights,target.diagnostic_weights): errors["weights"]=max(errors["weights"],legacy.maximum_error(left,right))
            for (_,lm,lv,lt),(_,rm,rv,rt) in zip(candidate.bias_snapshots,target.bias_snapshots):
                errors["bias_mean"]=max(errors["bias_mean"],legacy.maximum_error(lm,rm));errors["bias_variance"]=max(errors["bias_variance"],legacy.maximum_error(lv,rv));exact &= np.array_equal(lt,rt,equal_nan=True)
            for left,right in zip(candidate.diagnostic_node_states,target.diagnostic_node_states):
                exact &= left[0]==right[0]
                for lv,rv in zip(left[1:],right[1:]): errors["node_state"]=max(errors["node_state"],legacy.maximum_error(lv,rv))
            if candidate.diagnostic_state is not None:
                errors["diagnostic_state"]=max(errors["diagnostic_state"],legacy.maximum_error(candidate.diagnostic_state,target.diagnostic_state));errors["diagnostic_covariance"]=max(errors["diagnostic_covariance"],legacy.maximum_error(candidate.diagnostic_covariance,target.diagnostic_covariance))
        context_exact=all(
            candidate.kind==target.kind and candidate.sequence==target.sequence
            and candidate.decision==target.decision and candidate.root_reason==target.root_reason
            and np.array_equal(candidate.state,target.state,equal_nan=True)
            and np.array_equal(candidate.covariance,target.covariance,equal_nan=True)
            for candidate,target in context_pairs)
        groups_out=[x for x in actual if x.kind=="UWB"]
        scored_results=[candidate for candidate,_ in scored_pairs]
        imu_service=[x.service_ms for x in scored_results if x.kind=="IMU"]
        group_service=[x.service_ms for x in groups_out]
        publication=[x.publication_lag_ms for x in scored_results]
        utilization=float(np.mean(imu_service)/.005+np.mean(group_service)/.120048)/1000.
        total_links=sum(x.link_count for x in groups_out);diagnostic_links=sum(sum(v.shape[0] for v in x.h) for x in groups_out)
        drain=bool(final["sentinel"] and final["count"]==len(items) and final["qsize"]==0 and final["exitcode"]==0 and not final["alive"])
        parent_rss=int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss);after={str(p.relative_to(ROOT)):legacy.sha256(p) for p in FILES}
        link_epochs=[float(value) for item in groups_out for factor in item.h for value in []]
        factor_epochs=[float(value) for item in groups_out for factor in item.h for value in []]
        all_factor_epochs=[float(value) for item in expected_groups for factor in item.h for value in []]
        # Link overhang is measured independently from canonical rows/clocks.
        link_times=[node_clocks[row.node].link_time_ns(event_boot_epoch=row.boot,strobe_us=row.strobe_us,t_round_us=float(row.t_round_us[a]))*1e-9 for group in groups for row in group for a in _valid_slots(row)]
        gates={"counts":len(metric_imu_rows)==1000 and len(context_imu_rows)==6 and len(groups_out)==41 and len(reference_rows)*10==410 and total_links==diagnostic_links==3266,
            "reference_parity":exact and all(v<=1e-12 for v in errors.values()) and all(v<=1e-12 for v in reference_errors.values()),
            "context_transport_parity":context_exact and len(context_pairs)==len(context_imu_rows),
            "u1_exactly_41":sum(x.guard_calls for x in groups_out)==41,"no_deletion_loss_future_overflow_stale":total_links==diagnostic_links==3266,
            "queue_and_drain":hwm<64 and drain,"submit_p99":legacy.stats(submit)["p99_ms"]<5.,
            "publication":legacy.stats(publication)["p99_ms"]<150. and legacy.stats(publication)["maximum_ms"]<200.,
            "utilization":utilization<1.,"wall":time.perf_counter()-started<300.,
            "rss":parent_rss<RSS_CAP_KIB and int(final["rss"])<RSS_CAP_KIB,
            "raw_unchanged":legacy.sha256(raw)==raw_hash,"source_unchanged":before==after}
        status="OFFLINE_REFERENCE_PARITY_AND_REALTIME_REPLAY_DIAGNOSTIC" if all(gates.values()) else "BLOCKED_U7E6_ACTION04_REPLAY"
        result={**base,"status":status,"raw_opened":True,"raw_sha256":raw_hash,
            "raw_file_scope":"COMPLETE_ACTION04_CAPTURE_DECODED","metric_scope":"GROUP_REFERENCE_EPOCH_FIRST_5S_WITH_MEASURED_LINK_OVERHANG",
            "link_epoch_scope":{"minimum_s":min(link_times),"maximum_s":max(link_times),"maximum_overhang_past_metric_stop_s":max(0.,max(link_times)-stop_ns*1e-9)},
            "counts":{"imu_metric":len(metric_imu_rows),"imu_context":len(context_imu_rows),"imu_submitted":len(imu_events),"scored_results":len(scored_pairs),"context_results":len(context_pairs),"groups":len(groups_out),"sweeps":len(reference_rows)*10,"authoritative_links":total_links,"diagnostic_links":diagnostic_links},
            "gates":gates,"parity_exact":exact,"maximum_absolute_errors":errors,"current_contract_reference_errors":reference_errors,
            "u1_calls":sum(x.guard_calls for x in groups_out),"queue_hwm":hwm,"literal_drain":drain,"cold_start_ms":cold,
            "submit_ms":legacy.stats(submit),"publication_ms":legacy.stats(publication),"imu_service_ms":legacy.stats(imu_service),"uwb_service_ms":legacy.stats(group_service),
            "service_utilization":utilization,"service_utilization_formula":"mean(IMU_ms)/5ms + mean(UWB_ms)/120.048ms",
            "scoring_scope":"FIRST_5S_METRIC_IMUS_PLUS_41_SELECTED_UWB_GROUPS",
            "context_scope":"POST_WINDOW_IMUS_THROUGH_FINAL_SELECTED_GROUP_AVAILABILITY_TRANSPORT_ONLY_NOT_SCORED",
            "resources":{"parent_rss_kib":parent_rss,"worker_rss_kib":int(final["rss"])},"decode":asdict(decode_audit),"orientation":orientation_audit,
            "pose_owner":pose_audit,"wall_s":time.perf_counter()-started,"source_hashes_before":before,"source_hashes_after":after,
            "channel_b_observation_only_not_promoted_to_a":True}
        legacy.write_json(args.output/"RESULT.json",result)
        legacy.write_json(args.output/"GROUPS.json",[{"sequence":x.sequence,"decision":x.decision,"root_reason":x.root_reason,"links":x.link_count,"u1_calls":x.guard_calls,"publication_lag_ms":x.publication_lag_ms,"service_ms":x.service_ms} for x in groups_out])
        (args.output/"REPORT.md").write_text(f"# U7E7 action04 first-five-second replay\n\nStatus: `{status}`. Scored {len(metric_imu_rows)} metric-window IMUs and {len(groups_out)} groups; submitted {len(context_imu_rows)} post-window context-only IMUs, {len(reference_rows)*10} sweeps, and {diagnostic_links} valid links.\n\nChannel B is observation-only and never feeds A. This is not an online, product, calibrated-R, scientific, accuracy, or production claim.\n")
        size=sum(p.stat().st_size for p in args.output.iterdir() if p.is_file())
        if size>=EVIDENCE_CAP_BYTES: raise RuntimeError("evidence cap exceeded")
        digest=legacy.seal(args.output);print(json.dumps({"status":status,"seal_sha256":digest,"wall_s":result["wall_s"],"rss_kib":parent_rss,"bytes":size},sort_keys=True))
        return 0 if all(gates.values()) else 2
    except BaseException as exc:
        failure={**base,"status":"BLOCKED_U7E6_ACTION04_REPLAY","failure_type":type(exc).__name__,"failure_message":str(exc),
            "traceback":traceback.format_exc(),"wall_s":time.perf_counter()-started,"maximum_rss_kib":int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
            "raw_opened":(args.output/"CONTRACT.json").exists(),"HXX_opened":False}
        legacy.write_json(args.output/"FAILURE.json",failure)
        if not (args.output/"COMMAND.txt").exists():(args.output/"COMMAND.txt").write_text(command+"\n")
        digest=legacy.seal(args.output);print(json.dumps({"status":failure["status"],"failure":str(exc),"seal_sha256":digest},sort_keys=True));return 2


if __name__ == "__main__":
    raise SystemExit(main())
