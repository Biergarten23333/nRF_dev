#!/usr/bin/env python3
"""One-shot U7E4 owner-bound action04 first-five-second replay."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import sys
import time
import traceback

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_uwb_calibration.adaptive_nodes import AdaptiveNodeTrustConfig
from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectNodeLinkClock
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import frozen_world_alignment
from biospur_fusion.c2_timing_contract import canonical_clock_global_ns
from biospur_fusion.c2_uwb_root_world.async_root_worker import RootWorkerEvent, THREAD_ENV
from biospur_fusion.c2_uwb_root_world.causal_update_guard import ReachabilityClass, ReachabilityEnvelope
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import group_epoch_times_ns, validate_epoch_cadence
from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import (
    A_WEIGHT_POLICY, AsyncOwnerWorker, BoundGroupPacket, U3SigmaOwner, U5BSigmaOwner,
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
from test_c2_owner_bound_async_worker import PublicReference


ACTION = "04_shoulder_left"
PREFIX_NS = 5_000_000_000
RSS_CAP_KIB = 300_000
EVIDENCE_CAP_BYTES = 50_000_000
SEALS = {
    ROOT / "logs/c2_offline_unified_u3_20260906T160900Z": "938862a8c8d6daacd2c3c894865e3373eec2ae159a2a1f8f27e5c8d5559b2640",
    ROOT / "logs/c2_offline_unified_u3_20260906T160900Z_provenance_correction": "3faa2fe70872818877a2545a828a55580d3276a53de908d19cb337bdaca9035b",
    ROOT / "logs/c2_tight_range_u5b_revision_002_action04_first5s_20260906T144646Z": "cb0da6312eefb1d249d216c1560282d1fa221f4132fd9a9f2fa64e56ec5a5574",
    ROOT / "logs/c2_offline_uncertainty_u6_action04_first5s_20260906T151100Z": "64730cd279f46d6c7ec6e03114c7a96471a66bf5eb0a98e4ead7fe44627452ab",
    ROOT / "logs/c2_owner_bound_async_worker_u7e3_20260906T214500Z": "93f189b9c393d6c1cde4c62783e707ca4255462d943a1037b78c6f2d3f9ca5af",
    ROOT / "logs/c2_owner_bound_async_worker_u7e4_20260906T171600Z": "8ab5db9acd663f38df6a62e417467250250409afbc46ffec18bf458e5e90ed1b",
}
EXPECTED_RAW_SHA256 = "8b855a577f07bdc8725afe25c0e6720348ff479af2954b7649967e15c19f26ee"
EXPECTED_CLOCK_SHA256 = "b3c18d2d0ece3826498d2adc3cd41f3e4412794557f8525adc2f73bfa4ae3a66"
U7E_FILES = (
    ROOT / "src/biospur_fusion/c2_uwb_root_world/owner_bound_async_worker.py",
    ROOT / "src/biospur_fusion/c2_uwb_root_world/root_worker_owner_wiring.py",
    ROOT / "src/biospur_fusion/c2_uwb_root_world/offline_unified_wiring.py",
    ROOT / "src/biospur_fusion/c2_uwb_root_world/tight_range.py",
    ROOT / "tests/test_c2_owner_bound_async_worker.py",
    Path(__file__).resolve(),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_seal(path: Path, expected: str) -> None:
    seal = path / "SHA256SUMS"
    if sha256(seal) != expected:
        raise RuntimeError(f"seal digest mismatch: {path}")
    for line in seal.read_text().splitlines():
        digest, relative = line.split("  ", 1)
        if sha256(path / relative) != digest:
            raise RuntimeError(f"sealed member mismatch: {path / relative}")


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def seal(path: Path) -> str:
    members = sorted(p for p in path.iterdir() if p.is_file() and p.name != "SHA256SUMS")
    (path / "SHA256SUMS").write_text("".join(f"{sha256(item)}  {item.name}\n" for item in members))
    return sha256(path / "SHA256SUMS")


def stats(values) -> dict[str, float | int | None]:
    values = np.asarray(tuple(values), dtype=float)
    if not len(values):
        return {"count": 0, "p50_ms": None, "p99_ms": None, "maximum_ms": None}
    return {"count": len(values), "p50_ms": float(np.quantile(values, .5)),
            "p99_ms": float(np.quantile(values, .99)), "maximum_ms": float(np.max(values))}


def maximum_error(left, right) -> float:
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    if a.shape != b.shape:
        return math.inf
    return float(np.max(np.abs(a - b))) if a.size else 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    started = time.perf_counter()
    command = (
        "timeout --signal=TERM --kill-after=5s 300s env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 "
        "MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 PYTHONPATH=src:tools:. .venv-v0/bin/python "
        f"tools/run_c2_owner_bound_async_worker_u7e4_action04.py --output {args.output}"
    )
    before = {str(path.relative_to(ROOT)): sha256(path) for path in U7E_FILES}
    raw = DATASET / "actions" / PHYSICAL_DIRECTORY[ACTION] / "rep_01/raw/fusion_host_raw.cobs.bin"
    base = {
        "execution_class": "OFFLINE_REFERENCE_PARITY_AND_REALTIME_REPLAY_DIAGNOSTIC",
        "status": "FROZEN_BEFORE_DECODE", "action": ACTION, "no_retry": True,
        "channel_a": {"weight_policy": A_WEIGHT_POLICY, "sigma_policy": "U3_LAYOUT_PLUS_FLOOR",
                      "layout_sigma_m": .0564866166214546, "floor_sigma_m": .10},
        "channel_b": {"sigma_policy": "U5B_QUALITY_ONLY", "feeds_channel_a": False,
                      "uncertainty": "PROVISIONAL_UNCALIBRATED_OBSERVATION_ONLY"},
        "calibrated_R": False, "scientific_pass": False, "production_ready": False,
        "product_ready": False, "online_ready": False, "HXX_opened": False,
        "limits": {"wall_s": 300, "rss_kib_per_process": RSS_CAP_KIB,
                   "evidence_bytes": EVIDENCE_CAP_BYTES, "queue_capacity": 64},
        "expected": {"imu": 1000, "groups": 41, "sweeps": 410, "valid_links": 3266},
        "bound_seals": {str(path.relative_to(ROOT)): digest for path, digest in SEALS.items()},
        "command": command,
    }
    try:
        for path, digest in SEALS.items():
            verify_seal(path, digest)
        if sha256(CLOCK_TABLE) != EXPECTED_CLOCK_SHA256:
            raise RuntimeError("clock owner hash mismatch")
        raw_hash = sha256(raw)
        if raw_hash != EXPECTED_RAW_SHA256:
            raise RuntimeError("action04 raw hash mismatch")
        config = RawRangeUpdateConfig(
            nominal_sigma_m=.12, huber_threshold_sigma=2.5, maximum_iterations=8,
            convergence_tolerance=1e-7, covariance_floor=1e-12,
            positive_nlos_cauchy_scale_m=.12,
            uncertainty_provenance="PROVISIONAL_UNCALIBRATED_DIAGNOSTIC_U5B_ACTION04_FIRST5S",
        )
        a_sigma = U3SigmaOwner(.0564866166214546, .10,
                               "SEALED_U3_LAYOUT_PLUS_FLOOR_ACTION04_REFERENCE")
        b_sigma = U5BSigmaOwner(config, config.uncertainty_provenance)
        write_json(args.output / "CONTRACT.json", {**base, "raw": {
            "path": str(raw.relative_to(ROOT)), "sha256": raw_hash,
            "scope": "COMPLETE_ACTION04_CAPTURE_DECODED; METRICS_FIRST_5S_GROUP_REFERENCE",
        }, "clock_sha256": EXPECTED_CLOCK_SHA256, "source_hashes": before,
            "a_sigma_owner": a_sigma._manifest(), "b_sigma_owner": b_sigma._manifest()})
        (args.output / "COMMAND.txt").write_text(command + "\n")

        clocks = _clock_models(CLOCK_TABLE)
        bridges = _beacon_boundary_bridges(CLOCK_TABLE)
        lo_ns, hi_ns, _ = _action_bounds_global_ns(PHYSICAL_DIRECTORY[ACTION], bridges)
        stop_ns = lo_ns + PREFIX_NS
        if stop_ns > hi_ns:
            raise RuntimeError("prefix exceeds action support")
        clock_doc = json.loads(CLOCK_TABLE.read_text())
        node_clocks = {node: DirectNodeLinkClock(node, value.a_ns_per_us, value.b_ns, value.boot_epoch,
            int(clock_doc["models"][node]["first_timer_us"]), int(clock_doc["models"][node]["last_timer_us"]))
            for node, value in clocks.items()}
        anchors, delays, tag_delay, layout_sigma = _load_layout()
        if layout_sigma != a_sigma.layout_sigma_m:
            raise RuntimeError("U3 layout sigma mismatch")
        trajectory, pose_clocks, pose_audit = _verified_pose_inputs()
        alignment, _ = frozen_world_alignment(load_frozen_c2_3a())
        provider = _PoseProvider(trajectory=trajectory, clocks=pose_clocks, alignment=alignment)
        episode = _load_episode(ACTION, clocks, bridges)
        groups = [group for group in episode["groups"] if lo_ns <= _reference_time(group, clocks) * 1e9 < stop_ns]
        validate_epoch_cadence([_reference_time(group, clocks) * 1e9 for group in groups])
        events, decode_audit = decode_measurements(raw)
        imu_rows, orientation_audit = _pelvis_imu(events, clocks[PELVIS_NODE], lo_ns, 0.0)
        imu_rows = [row for row in imu_rows if lo_ns * 1e-9 < row["time_s"] < stop_ns * 1e-9]
        if len(imu_rows) != 1000 or len(groups) != 41:
            raise RuntimeError("frozen action04 count mismatch")

        sealed_u5b = [json.loads(line) for line in
                      (next(path for path in SEALS if "tight_range_u5b_revision" in str(path)) / "SWEEPS.jsonl").read_text().splitlines()]
        if len(sealed_u5b) != 410:
            raise RuntimeError("sealed U5B sweep count mismatch")
        u5b_by_identity = {(int(row["group_index"]), str(row["node"])): row for row in sealed_u5b}

        def build_pose_links(group):
            values = []
            for row in sorted(group, key=lambda item: str(item.node)):
                clock = node_clocks[row.node]
                for anchor in range(8):
                    query = clock.link_time_ns(event_boot_epoch=row.boot, strobe_us=row.strobe_us,
                                               t_round_us=float(row.t_round_us[anchor]))
                    snapshot = provider.snapshot(action=ACTION, sweep_query_ns=query, root_world_m=np.zeros(3))
                    values.append(PoseTagLinkOwner(str(row.node), anchor, query, snapshot.pose_global_ns,
                        snapshot.offsets_world_m[row.node], np.zeros(3), snapshot.frame, snapshot.frame,
                        pose_audit["accepted_sha256"]))
            return tuple(values)

        def build_weights(index, group):
            values = []
            for row in group:
                sealed = u5b_by_identity[(index, str(row.node))]
                vector = np.ones(8)
                for anchor, value in zip(sealed["anchors"], sealed["weights"]):
                    vector[int(anchor)] = float(value)
                slots = tuple(_valid_slots(row))
                query = min(node_clocks[row.node].link_time_ns(event_boot_epoch=row.boot,
                    strobe_us=row.strobe_us, t_round_us=float(row.t_round_us[anchor])) for anchor in slots)
                snapshot = provider.snapshot(action=ACTION, sweep_query_ns=query, root_world_m=np.zeros(3))
                values.append(ExternalRangeInformationWeights(str(row.node), snapshot.pose_global_ns * 1e-9,
                    vector, "STRICT_PRE_EPOCH_DIRECT_BODY_SHADOW_DISPLAY_PROXY"))
            return tuple(values)

        all_pose = [build_pose_links(group) for group in groups]
        all_weights = [build_weights(index, group) for index, group in enumerate(groups)]
        initial_position = np.array([np.mean(anchors[:, 0]), np.mean(anchors[:, 1]), .95])
        initial_state = RootState(lo_ns * 1e-9, np.r_[initial_position, np.zeros(6)],
                                  np.diag([1.] * 6 + [.04] * 3))
        envelope = ReachabilityEnvelope(ReachabilityClass.NOMINAL, 20., 100., 1000., 1., 100., 1000.,
            1., 1., 1., .01, 2, 20., 1e8, "U3_OFFLINE_FUNCTIONAL_FIXTURE_NOT_HUMAN_OR_PRODUCT_QUALIFICATION")
        static_range = RangeInformationOwner(.12, .12,
            {item.node: item.weights for item in all_weights[0]}, "STRICT_PRE_EPOCH_DIRECT_BODY_SHADOW_DISPLAY_PROXY")
        owner = ReferenceOwnerBundle(RootFilterConfig(fixed_lag_s=.10), True, initial_state, anchors, node_clocks,
            delays, tag_delay, all_pose[0], static_range, envelope, AdaptiveNodeTrustConfig(),
            "SEALED_U3_ROOT_FILTER_CONFIG", "SEALED_U3_ACTION04_INITIAL_STATE", "SEALED_LAYOUT",
            f"CLOCK_TABLE_SHA256:{EXPECTED_CLOCK_SHA256}", "U1_NOMINAL_ROOT_POSITION_POLICY")
        packets = []
        for index, group in enumerate(groups):
            _, _, availability_ns = group_epoch_times_ns(group, clocks=node_clocks)
            availability_ns = canonical_clock_global_ns(availability_ns)
            event = RootWorkerEvent(index, availability_ns * 1e-9, "UWB", tuple(group))
            packets.append(BoundGroupPacket(owner.digest, event, all_pose[index], all_weights[index], a_sigma, b_sigma, availability_global_ns=availability_ns))
        imu_events = [RootWorkerEvent(int(row["sequence"]), float(row["time_s"]), "IMU",
            ImuSample(float(row["time_s"]), float(row["time_s"]), row["acceleration"], row["rotation_world"],
                      int(row["sequence"]))) for row in imu_rows]
        timeline = [(item.availability_time_s, 0, item) for item in imu_events]
        timeline.extend((packet.event.availability_time_s, 1, packet) for packet in packets)
        timeline.sort(key=lambda row: (row[0], row[1]))
        items = [row[2] for row in timeline]

        reference = PublicReference(owner)
        expected = [reference.process(item) for item in items]
        expected_groups = [item for item in expected if item.kind == "UWB"]
        u3_rows = [json.loads(line) for line in
                   (next(path for path in SEALS if path.name == "c2_offline_unified_u3_20260906T160900Z") / "GROUPS.jsonl").read_text().splitlines()]
        sealed_errors = {"u3_state": 0., "u5b_nis": 0., "u5b_condition": 0., "u5b_weights": 0.}
        for index, (actual, sealed) in enumerate(zip(expected_groups, u3_rows)):
            sealed_errors["u3_state"] = max(sealed_errors["u3_state"], maximum_error(actual.state[:6],
                np.r_[sealed["root_position_m"], sealed["root_velocity_mps"]]))
            if actual.decision != sealed["transaction_reason"] or actual.link_count != sealed["links"]:
                raise RuntimeError(f"U3 sealed decision/identity mismatch group {index}")
            rows = sorted((u5b_by_identity[(index, node)] for node, _, _ in actual.diagnostic_decisions),
                          key=lambda row: (row["reference_time_s"], row["node"]))
            for local, (decision, row) in enumerate(zip(actual.diagnostic_decisions, rows)):
                if decision != (row["node"], row["accepted"], row["update_reason"]):
                    raise RuntimeError(f"U5B sealed decision mismatch group {index}")
                sealed_errors["u5b_nis"] = max(sealed_errors["u5b_nis"], abs(actual.nis[local] - row["prior_nis"]))
                sealed_errors["u5b_condition"] = max(sealed_errors["u5b_condition"], abs(actual.condition[local] - row["condition"]))
                sealed_errors["u5b_weights"] = max(sealed_errors["u5b_weights"], maximum_error(actual.diagnostic_weights[local][1],
                    np.asarray([dict(zip(row["anchors"], row["weights"])).get(anchor, 1.) for anchor in range(8)])))
                if actual.rank[local] != row["rank"]:
                    raise RuntimeError(f"U5B sealed rank mismatch group {index}")
        if any(value > 1e-12 for value in sealed_errors.values()):
            raise RuntimeError(f"sealed reference mismatch: {sealed_errors}")

        worker = AsyncOwnerWorker(owner, capacity=64)
        cold_start_ms = worker.cold_start_ms
        capture_started = time.perf_counter()
        first_time = timeline[0][0]
        for when, _, item in timeline:
            wait = capture_started + when - first_time - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            worker.submit(item)
        queue_hwm = worker.hwm
        submit_ms = tuple(worker.submit_ms)
        actual, final = worker.close_and_collect(len(items), timeout=30)
        errors = {key: 0. for key in ("state", "covariance", "h", "sensor_r", "total_r", "s", "nis",
                                             "condition", "prediction", "weights", "bias_mean", "bias_variance",
                                             "node_state", "diagnostic_state", "diagnostic_covariance")}
        exact = True
        for candidate, target in zip(actual, expected):
            exact &= (candidate.kind, candidate.sequence, candidate.decision, candidate.root_reason,
                      candidate.link_identities, candidate.link_count, candidate.guard_calls,
                      candidate.diagnostic_decisions, candidate.rank) == (
                      target.kind, target.sequence, target.decision, target.root_reason,
                      target.link_identities, target.link_count, target.guard_calls,
                      target.diagnostic_decisions, target.rank)
            errors["state"] = max(errors["state"], maximum_error(candidate.state, target.state))
            errors["covariance"] = max(errors["covariance"], maximum_error(candidate.covariance, target.covariance))
            for field in ("h", "sensor_r", "total_r", "s"):
                for left, right in zip(getattr(candidate, field), getattr(target, field)):
                    errors[field] = max(errors[field], maximum_error(left, right))
            errors["nis"] = max(errors["nis"], maximum_error(candidate.nis, target.nis))
            errors["condition"] = max(errors["condition"], maximum_error(candidate.condition, target.condition))
            for left, right in zip(candidate.diagnostic_predicted, target.diagnostic_predicted):
                errors["prediction"] = max(errors["prediction"], maximum_error(left, right))
            for (_, left), (_, right) in zip(candidate.diagnostic_weights, target.diagnostic_weights):
                errors["weights"] = max(errors["weights"], maximum_error(left, right))
            for (_, lm, lv, lt), (_, rm, rv, rt) in zip(candidate.bias_snapshots, target.bias_snapshots):
                errors["bias_mean"] = max(errors["bias_mean"], maximum_error(lm, rm))
                errors["bias_variance"] = max(errors["bias_variance"], maximum_error(lv, rv))
                exact &= np.array_equal(lt, rt, equal_nan=True)
            for left, right in zip(candidate.diagnostic_node_states, target.diagnostic_node_states):
                exact &= left[0] == right[0]
                for lvalue, rvalue in zip(left[1:], right[1:]):
                    errors["node_state"] = max(errors["node_state"], maximum_error(lvalue, rvalue))
            if candidate.diagnostic_state is not None:
                errors["diagnostic_state"] = max(errors["diagnostic_state"], maximum_error(candidate.diagnostic_state, target.diagnostic_state))
                errors["diagnostic_covariance"] = max(errors["diagnostic_covariance"], maximum_error(candidate.diagnostic_covariance, target.diagnostic_covariance))
        actual_groups = [item for item in actual if item.kind == "UWB"]
        imu_service = [item.service_ms for item in actual if item.kind == "IMU"]
        group_service = [item.service_ms for item in actual_groups]
        publication = [item.publication_lag_ms for item in actual]
        utilization = float(np.mean(imu_service) / .005 + np.mean(group_service) / .120048) / 1000.
        total_links = sum(item.link_count for item in actual_groups)
        diagnostic_links = sum(sum(value.shape[0] for value in item.h) for item in actual_groups)
        drain = bool(final["sentinel"] and final["count"] == len(items) and final["qsize"] == 0
                     and final["exitcode"] == 0 and not final["alive"])
        parent_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        after = {str(path.relative_to(ROOT)): sha256(path) for path in U7E_FILES}
        gates = {
            "counts": len(imu_events) == 1000 and len(actual_groups) == 41 and len(sealed_u5b) == 410
                      and total_links == 3266 and diagnostic_links == 3266,
            "reference_parity": exact and all(value <= 1e-12 for value in errors.values())
                                and all(value <= 1e-12 for value in sealed_errors.values()),
            "u1_exactly_41": sum(item.guard_calls for item in actual_groups) == 41,
            "no_deletion_loss_future_overflow_stale": total_links == diagnostic_links == 3266,
            "queue_and_drain": queue_hwm < 64 and drain,
            "submit_p99": stats(submit_ms)["p99_ms"] < 5.,
            "publication": stats(publication)["p99_ms"] < 150. and stats(publication)["maximum_ms"] < 200.,
            "utilization": utilization < 1.,
            "wall": time.perf_counter() - started < 300.,
            "rss": parent_rss < RSS_CAP_KIB and int(final["rss"]) < RSS_CAP_KIB,
            "raw_unchanged": sha256(raw) == raw_hash,
            "source_unchanged": before == after,
        }
        status = "OFFLINE_REFERENCE_PARITY_AND_REALTIME_REPLAY_DIAGNOSTIC" if all(gates.values()) else "BLOCKED_U7E4_ACTION04_REPLAY"
        result = {**base, "status": status, "raw_opened": True, "raw_sha256": raw_hash,
            "metric_scope": "GROUP_REFERENCE_EPOCH_FIRST_5S_WITH_MEASURED_LINK_OVERHANG",
            "counts": {"imu": len(imu_events), "groups": len(actual_groups), "sweeps": len(sealed_u5b),
                       "authoritative_links": total_links, "diagnostic_links": diagnostic_links},
            "gates": gates, "parity_exact": exact, "maximum_absolute_errors": errors,
            "sealed_reference_errors": sealed_errors, "u1_calls": sum(item.guard_calls for item in actual_groups),
            "queue_hwm": queue_hwm, "literal_drain": drain, "cold_start_ms": cold_start_ms,
            "submit_ms": stats(submit_ms), "publication_ms": stats(publication),
            "imu_service_ms": stats(imu_service), "uwb_service_ms": stats(group_service),
            "service_utilization": utilization,
            "service_utilization_formula": "mean(IMU_ms)/5ms + mean(UWB_ms)/120.048ms",
            "resources": {"parent_rss_kib": parent_rss, "worker_rss_kib": int(final["rss"])},
            "decode": asdict(decode_audit), "orientation": orientation_audit, "pose_owner": pose_audit,
            "wall_s": time.perf_counter() - started, "raw_unchanged": sha256(raw) == raw_hash,
            "source_hashes_before": before, "source_hashes_after": after,
            "channel_b_observation_only_not_promoted_to_a": True,
        }
        write_json(args.output / "RESULT.json", result)
        write_json(args.output / "GROUPS.json", [{"sequence": item.sequence, "decision": item.decision,
            "root_reason": item.root_reason, "links": item.link_count, "u1_calls": item.guard_calls,
            "publication_lag_ms": item.publication_lag_ms, "service_ms": item.service_ms}
            for item in actual_groups])
        (args.output / "REPORT.md").write_text(
            f"# U7E4 action04 first-five-second replay\n\nStatus: `{status}`. Processed {len(imu_events)} IMUs, "
            f"{len(actual_groups)} groups, {len(sealed_u5b)} node sweeps, and {diagnostic_links} valid links.\n\n"
            "Channel A is exact unit-weight U3. Channel B is observation-only U5B/U6 and never feeds A. "
            "This is not an online, product, calibrated-R, scientific, accuracy, or production claim.\n")
        size = sum(path.stat().st_size for path in args.output.iterdir() if path.is_file())
        if size >= EVIDENCE_CAP_BYTES:
            raise RuntimeError("evidence cap exceeded")
        digest = seal(args.output)
        print(json.dumps({"status": status, "seal_sha256": digest, "wall_s": result["wall_s"],
                          "rss_kib": parent_rss, "bytes": size}, sort_keys=True))
        return 0 if all(gates.values()) else 2
    except BaseException as exc:
        failure = {**base, "status": "BLOCKED_U7E4_ACTION04_REPLAY", "failure_type": type(exc).__name__,
            "failure_message": str(exc), "traceback": traceback.format_exc(), "wall_s": time.perf_counter() - started,
            "maximum_rss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
            "raw_opened": (args.output / "CONTRACT.json").exists(), "HXX_opened": False}
        write_json(args.output / "FAILURE.json", failure)
        if not (args.output / "COMMAND.txt").exists():
            (args.output / "COMMAND.txt").write_text(command + "\n")
        digest = seal(args.output)
        print(json.dumps({"status": failure["status"], "failure": str(exc), "seal_sha256": digest}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
