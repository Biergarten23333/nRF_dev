#!/usr/bin/env python3
"""One authorized five-second action-04 offline U3 owner preflight."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import resource
import sys
import time
from typing import Any

import numpy as np

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectNodeLinkClock
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import frozen_world_alignment
from biospur_fusion.c2_uwb_root_world.causal_update_guard import ReachabilityClass, ReachabilityEnvelope
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import (
    ARTICULATED_TRANSACTION_P99_DEBT_MS,
    ROOT_TRANSACTION_P99_DEBT_MS,
    StrictFloorOffset,
    U2_QUALIFICATION_SEAL_SHA256,
    execute_offline_root_group,
    require_offline_mode,
    validate_epoch_cadence,
)
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    DATASET, PHYSICAL_DIRECTORY, _action_bounds_global_ns,
    _beacon_boundary_bridges, _clock_models,
)
from biospur_fusion.ingest.v47 import decode_measurements
from biospur_fusion.root_r3.estimator import CausalDelayedRootFilter, RootFilterConfig
from biospur_fusion.root_r3.models import ImuSample, RootState

from evaluate_c2_pair_bias_gate import _base_sigma, _load_episode, _load_layout, _reference_time
from run_c2_direct_body_shadow_ab_pilot import (
    CLOCK_TABLE, PELVIS_NODE, _PoseProvider, _verified_pose_inputs,
)
from run_c2_h01_tight_raw_range_fusion import _pelvis_imu

ROOT = Path(__file__).resolve().parents[1]
ACTION = "04_shoulder_left"
PREFIX_DURATION_NS = 5_000_000_000
MAXIMUM_RSS_KIB = 500_000
MAXIMUM_OUTPUT_BYTES = 50_000_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def seal(path: Path) -> str:
    lines = []
    for item in sorted(p for p in path.rglob("*") if p.is_file() and p.name != "SHA256SUMS"):
        lines.append(f"{sha256(item)}  {item.relative_to(path)}")
    target = path / "SHA256SUMS"
    target.write_text("\n".join(lines) + "\n")
    return sha256(target)


def percentile(values: list[float], q: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=float), q)) if values else math.nan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require_offline_mode(offline=True, production=False)
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    started = time.perf_counter()
    raw = DATASET / "actions" / PHYSICAL_DIRECTORY[ACTION] / "rep_01/raw/fusion_host_raw.cobs.bin"
    command = (
        "timeout --signal=TERM --kill-after=5s 300s env PYTHONPATH=src:tools:. "
        f".venv-v0/bin/python tools/preflight_c2_offline_unified_u3.py --output {args.output}"
    )
    source_paths = [
        ROOT / "src/biospur_fusion/c2_uwb_root_world/offline_unified_wiring.py",
        ROOT / "tests/test_c2_offline_unified_wiring.py",
        Path(__file__),
        ROOT / "src/biospur_fusion/c2_uwb_calibration/adaptive_nodes.py",
        ROOT / "src/biospur_fusion/c2_uwb_calibration/shared_root.py",
        ROOT / "src/biospur_fusion/c2_uwb_root_world/causal_update_transaction.py",
        ROOT / "src/biospur_fusion/c2_uwb_calibration/direct_body_shadow_ab.py",
    ]
    clocks = _clock_models(CLOCK_TABLE)
    bridges = _beacon_boundary_bridges(CLOCK_TABLE)
    lo_ns, hi_ns, _events = _action_bounds_global_ns(PHYSICAL_DIRECTORY[ACTION], bridges)
    prefix_stop_ns = lo_ns + PREFIX_DURATION_NS
    if prefix_stop_ns > hi_ns:
        raise RuntimeError("frozen five-second prefix exceeds action bounds")
    raw_before = sha256(raw)
    write_json(args.output / "CONTRACT.json", {
        "status": "FROZEN_BEFORE_RANGE_DECODE",
        "action": ACTION,
        "prefix": {"start_common_ns": lo_ns, "stop_common_ns_exclusive": prefix_stop_ns,
                   "duration_s": 5.0, "downsampling": False},
        "raw": {"path": str(raw.relative_to(ROOT)), "sha256": raw_before},
        "owners": {
            "link_time": "sealed per-node clock(strobe_us+t_round_us/2)",
            "pose": "strict-floor native200 source TIMER2; 0<age<=5.005ms",
            "trust": "adaptive x/10 select_trusted_body_nodes",
            "candidate": "exactly one solve_shared_root per group",
            "covariance": "DIAGNOSTIC_NODE_COUNT_FLOOR_ONLY; diagonal; CALIBRATED_R=false",
            "transaction": "exactly one CandidateKind.ROOT_POSITION U2 transaction per group",
        },
        "execution_class": "OFFLINE_ONLY", "online_status": "ONLINE_BLOCKED",
        "scientific_pass": False, "production_ready": False, "calibrated_R": False,
        "u2_seal_sha256": U2_QUALIFICATION_SEAL_SHA256,
        "latency_debt_ms": {"root_p99": ROOT_TRANSACTION_P99_DEBT_MS,
                            "articulated_p99": ARTICULATED_TRANSACTION_P99_DEBT_MS},
        "command": command,
    })

    trajectory, pose_clocks, pose_audit = _verified_pose_inputs()
    calibration = load_frozen_c2_3a()
    alignment, _ = frozen_world_alignment(calibration)
    provider = _PoseProvider(trajectory=trajectory, clocks=pose_clocks, alignment=alignment)
    clock_document = json.loads(CLOCK_TABLE.read_text())
    node_clocks = {
        node: DirectNodeLinkClock(node, clock.a_ns_per_us, clock.b_ns, clock.boot_epoch,
            int(clock_document["models"][node]["first_timer_us"]),
            int(clock_document["models"][node]["last_timer_us"]))
        for node, clock in clocks.items()
    }
    anchors, delays, tag_delay, layout_sigma = _load_layout()
    episode = _load_episode(ACTION, clocks, bridges)
    groups = [group for group in episode["groups"] if lo_ns <= _reference_time(group, clocks)*1e9 < prefix_stop_ns]
    measurement_ns = [_reference_time(group, clocks)*1e9 for group in groups]
    validate_epoch_cadence(measurement_ns)
    if not groups:
        raise RuntimeError("five-second prefix contains no canonical UWB groups")

    events, decode_audit = decode_measurements(raw)
    imu, orientation_audit = _pelvis_imu(events, clocks[PELVIS_NODE], lo_ns, 0.0)
    imu = [row for row in imu if lo_ns*1e-9 < row["time_s"] < prefix_stop_ns*1e-9]
    if len(imu) < 990:
        raise RuntimeError("five-second prefix lacks gap-safe native200 pelvis samples")
    dt = np.diff([row["time_s"] for row in imu])
    if np.any(dt <= 0) or np.count_nonzero(dt <= .005005) < 990:
        raise RuntimeError("native200 IMU timing is not gap-safe")

    initial_position = np.array([np.mean(anchors[:,0]), np.mean(anchors[:,1]), .95])
    covariance = np.diag([1.,1.,1.,1.,1.,1.,.04,.04,.04])
    root = CausalDelayedRootFilter(
        RootState(lo_ns*1e-9, np.r_[initial_position, np.zeros(6)], covariance),
        RootFilterConfig(fixed_lag_s=.10), inertial=True)
    envelope = ReachabilityEnvelope(
        ReachabilityClass.NOMINAL, 20., 100., 1000., 1., 100., 1000.,
        1., 1., 1., .01, 2, 20., 1e8,
        "U3_OFFLINE_FUNCTIONAL_FIXTURE_NOT_HUMAN_OR_PRODUCT_QUALIFICATION")
    imu_cursor = 0
    rows_out = []
    timing = {name: [] for name in ("imu_advance_ms", "group_total_ms")}
    accepted = rejected = 0
    for sequence, group in enumerate(groups):
        availability_s = max(node_clocks[row.node].link_time_ns(
            event_boot_epoch=row.boot, strobe_us=row.frame_us, t_round_us=0.) for row in group) * 1e-9
        imu_started = time.perf_counter()
        while imu_cursor < len(imu) and imu[imu_cursor]["time_s"] <= availability_s:
            sample = imu[imu_cursor]
            if not root.add_imu(ImuSample(sample["time_s"], sample["time_s"],
                    sample["acceleration"], sample["rotation_world"], sample["sequence"])):
                raise RuntimeError("in-order native200 IMU sample rejected")
            imu_cursor += 1
        timing["imu_advance_ms"].append((time.perf_counter()-imu_started)*1000.)
        root_at_group = root.publication_token().state.vector[:3].copy()
        def strict_offset(node: str, query_ns: float) -> StrictFloorOffset:
            snap = provider.snapshot(action=ACTION, sweep_query_ns=query_ns, root_world_m=root_at_group)
            return StrictFloorOffset(snap.offsets_world_m[node], snap.pose_global_ns,
                                     snap.query_global_ns, snap.pose_age_ns, snap.frame)
        group_started = time.perf_counter()
        result = execute_offline_root_group(
            root=root, rows=group, clocks=node_clocks, strict_floor_offset=strict_offset,
            anchors_m=anchors, anchor_delay_m=delays, tag_delay_m=tag_delay,
            sigma_for_quality=lambda quality: _base_sigma(layout_sigma, quality),
            nominal_envelope=envelope, source_sequence=sequence)
        timing["group_total_ms"].append((time.perf_counter()-group_started)*1000.)
        accepted += int(result.transaction.root_committed)
        rejected += int(result.transaction.rejection_recorded)
        rows_out.append({
            "sequence": sequence, "measurement_time_s": result.measurement_time_s,
            "availability_time_s": result.availability_time_s,
            "links": len(result.link_audit), "trusted_nodes": list(result.selection.trusted_nodes),
            "x_over_10": len(result.selection.trusted_nodes), "rank": result.candidate.rank,
            "condition": result.candidate.condition, "candidate_reason": result.candidate.reason,
            "transaction_reason": result.transaction.decision.reason.value,
            "root_committed": result.transaction.root_committed,
            "pose_age_max_ms": max(row.pose_age_ns for row in result.link_audit)*1e-6,
            "candidate_solver_calls": result.candidate_solver_calls,
            "transaction_calls": result.transaction_calls,
            "covariance_minimum_std_m": result.covariance_minimum_std_m,
            "root_position_m": root.current_state.vector[:3].tolist(),
            "root_velocity_mps": root.current_state.vector[3:6].tolist(),
        })
    if any(row["candidate_solver_calls"] != 1 or row["transaction_calls"] != 1 for row in rows_out):
        raise RuntimeError("solver/transaction call cardinality changed")
    if sha256(raw) != raw_before:
        raise RuntimeError("raw input changed")
    with (args.output / "GROUPS.jsonl").open("w") as stream:
        for row in rows_out:
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    result_doc = {
        "status": "OFFLINE_U3_ROOT_WIRING_COMPLETE_ONLINE_BLOCKED",
        "execution_class": "OFFLINE_ONLY", "online_status": "ONLINE_BLOCKED",
        "scientific_pass": False, "production_ready": False, "calibrated_R": False,
        "covariance_owner": "DIAGNOSTIC_NODE_COUNT_FLOOR_ONLY",
        "action": ACTION, "prefix_start_common_ns": lo_ns,
        "prefix_stop_common_ns_exclusive": prefix_stop_ns,
        "raw_sha256_before_after_equal": True,
        "uwb_groups": len(groups), "imu_samples_streamed": imu_cursor,
        "accepted": accepted, "rejected": rejected,
        "x_over_10_histogram": {str(x): sum(row["x_over_10"] == x for row in rows_out)
                                for x in sorted({row["x_over_10"] for row in rows_out})},
        "candidate_condition_max": max(row["condition"] for row in rows_out),
        "pose_age_max_ms": max(row["pose_age_max_ms"] for row in rows_out),
        "latency_ms": {name: {"p50": percentile(values,50), "p99": percentile(values,99),
                               "max": max(values)} for name,values in timing.items()},
        "u2_qualification_seal_sha256": U2_QUALIFICATION_SEAL_SHA256,
        "frozen_latency_debt_ms": {"root_p99": ROOT_TRANSACTION_P99_DEBT_MS,
                                   "articulated_p99": ARTICULATED_TRANSACTION_P99_DEBT_MS},
        "pose_owner": pose_audit, "decode": decode_audit.__dict__,
        "orientation": orientation_audit, "wall_s": time.perf_counter()-started,
        "maximum_rss_kib": rss, "raw_or_hxx_opened": False,
    }
    write_json(args.output / "RESULT.json", result_doc)
    write_json(args.output / "HASHES.json", {
        "sources": {str(path.relative_to(ROOT)): sha256(path) for path in source_paths},
        "inputs": {str(raw.relative_to(ROOT)): raw_before,
                   str(CLOCK_TABLE.relative_to(ROOT)): sha256(CLOCK_TABLE)},
    })
    output_bytes = sum(p.stat().st_size for p in args.output.rglob("*") if p.is_file())
    if rss >= MAXIMUM_RSS_KIB or output_bytes >= MAXIMUM_OUTPUT_BYTES:
        raise RuntimeError("resource gate failed")
    digest = seal(args.output)
    print(json.dumps({"status": result_doc["status"], "groups": len(groups),
                      "rss_kib": rss, "wall_s": result_doc["wall_s"],
                      "seal_sha256": digest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
