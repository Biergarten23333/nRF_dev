#!/usr/bin/env python3
"""One bounded action-04 first-five-second U5B engineering diagnostic."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import resource
import sys
import time
import traceback

import numpy as np

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import (
    DirectNodeLinkClock, direct_shadow_evidence,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import frozen_world_alignment
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    DATASET, PHYSICAL_DIRECTORY, _action_bounds_global_ns,
    _beacon_boundary_bridges, _clock_models,
)
from biospur_fusion.c2_uwb_root_world.tight_range import (
    ExternalRangeInformationWeights, PersistentRangeBiasTracker,
    RangeBiasPriorSnapshot, RawRangeUpdateConfig, linearize_raw_range_factors,
    update_raw_ranges,
)
from biospur_fusion.ingest.v47 import decode_measurements
from biospur_fusion.root_r3.estimator import RootFilterConfig, propagate_inertial
from biospur_fusion.root_r3.models import RootState

from evaluate_c2_pair_bias_gate import _load_episode, _load_layout, _reference_time, _valid_slots
from run_c2_direct_body_shadow_ab_pilot import (
    CLOCK_TABLE, PELVIS_NODE, _PoseProvider, _verified_pose_inputs,
)
from run_c2_h01_tight_raw_range_fusion import _pelvis_imu


ROOT = Path(__file__).resolve().parents[1]
ACTION = "04_shoulder_left"
PREFIX_NS = 5_000_000_000
RSS_CAP_KIB = 300_000
DISK_CAP_BYTES = 50_000_000
U5A = ROOT / "logs/c2_tight_range_u5a_revision_002_20260906T143413Z"
U5A_SEAL = "1b12acef002da1bb720c8777abc248db4820e0edd2c2f203d4c84bf460494eff"
U3 = ROOT / "logs/c2_offline_unified_u3_20260906T160900Z"
U3_SEAL = "938862a8c8d6daacd2c3c894865e3373eec2ae159a2a1f8f27e5c8d5559b2640"
U3_CORRECTION = ROOT / "logs/c2_offline_unified_u3_20260906T160900Z_provenance_correction"
U3_CORRECTION_SEAL = "3faa2fe70872818877a2545a828a55580d3276a53de908d19cb337bdaca9035b"
PRIOR_BLOCKED = ROOT / "logs/c2_tight_range_u5b_action04_first5s_20260906T144101Z"
PRIOR_BLOCKED_SEAL = "ee3d5347aaceda1977195c81b0cfa77a24d93a1ae0d8879b2642a3d570cba059"
PROCESS_STARTED = time.perf_counter()


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


def stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "p50": None, "p90": None, "p99": None, "max": None}
    row = np.asarray(values, dtype=float)
    return {"count": len(values), "min": float(np.min(row)),
            "p50": float(np.quantile(row, .5)), "p90": float(np.quantile(row, .9)),
            "p99": float(np.quantile(row, .99)), "max": float(np.max(row))}


def seal(path: Path) -> str:
    rows = []
    for item in sorted(p for p in path.iterdir() if p.is_file() and p.name != "SHA256SUMS"):
        rows.append(f"{sha256(item)}  {item.name}")
    target = path / "SHA256SUMS"
    target.write_text("\n".join(rows) + "\n")
    return sha256(target)


def final_bias_snapshot_epoch(maximum_accepted_link_epoch_s: float | None) -> float | None:
    """Return the first float strictly after all accepted link evidence."""
    if maximum_accepted_link_epoch_s is None:
        return None
    value = float(maximum_accepted_link_epoch_s)
    if not math.isfinite(value):
        raise ValueError("maximum accepted link epoch must be finite")
    query = float(np.nextafter(value, math.inf))
    if not math.isfinite(query) or not query > value:
        raise ValueError("no finite summary epoch follows accepted link evidence")
    return query


def seal_failure_from_argv(exc: BaseException) -> None:
    """Best-effort append-only failure seal for every runner exception."""
    try:
        marker = sys.argv.index("--output")
        output = Path(sys.argv[marker + 1])
        output.mkdir(parents=True, exist_ok=True)
        prior = output / "SHA256SUMS"
        if prior.exists():
            return
        write_json(output / "FAILURE.json", {
            "status": "BLOCKED_U5B_EXCEPTION",
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "traceback": traceback.format_exc(),
            "wall_s": time.perf_counter() - PROCESS_STARTED,
            "maximum_rss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
            "output_bytes_before_failure_seal": sum(
                path.stat().st_size for path in output.iterdir() if path.is_file()),
            "raw_opened": True,
            "HXX_opened": False,
            "calibrated_R": False,
            "scientific_pass": False,
            "production_ready": False,
            "no_retry": True,
            "prior_blocked_seal_sha256": PRIOR_BLOCKED_SEAL,
        })
        seal(output)
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    verify_seal(U5A, U5A_SEAL)
    verify_seal(U3, U3_SEAL)
    verify_seal(U3_CORRECTION, U3_CORRECTION_SEAL)
    verify_seal(PRIOR_BLOCKED, PRIOR_BLOCKED_SEAL)
    args.output.mkdir(parents=True)
    started = time.perf_counter()
    raw = DATASET / "actions" / PHYSICAL_DIRECTORY[ACTION] / "rep_01/raw/fusion_host_raw.cobs.bin"
    raw_hash = sha256(raw)
    config = RawRangeUpdateConfig(
        nominal_sigma_m=0.12, huber_threshold_sigma=2.5,
        maximum_iterations=8, convergence_tolerance=1e-7,
        covariance_floor=1e-12, positive_nlos_cauchy_scale_m=0.12,
        uncertainty_provenance="PROVISIONAL_UNCALIBRATED_DIAGNOSTIC_U5B_ACTION04_FIRST5S",
    )
    command = (
        "timeout --signal=TERM --kill-after=5s 300s env PYTHONPATH=src:tools:. "
        f".venv-v0/bin/python tools/run_c2_tight_range_u5b.py --output {args.output}"
    )
    write_json(args.output / "CONTRACT.json", {
        "status": "FROZEN_BEFORE_DECODE", "command": command, "action": ACTION,
        "metric_scope": "GROUPS_WITH_REFERENCE_EPOCH_IN_ACTION04_FIRST_5S_WITH_MEASURED_PER_LINK_EPOCH_OVERHANG",
        "raw_scope": "COMPLETE_ACTION04_CAPTURE_DECODED",
        "raw_path": str(raw.relative_to(ROOT)), "raw_sha256": raw_hash,
        "parameters": config.__dict__,
        "timing": "per-link common clock(strobe_us+t_round_us/2); no second motion subtraction",
        "weights": "strictly pre-epoch antenna+torso+other-limb display-proxy combined weight; (0,1]",
        "all_structurally_valid_ranges_retained": True, "range_deletion": False,
        "calibrated_R": False, "scientific_pass": False, "production_ready": False,
        "seals": {"u5a": U5A_SEAL, "u3": U3_SEAL, "u3_correction": U3_CORRECTION_SEAL,
                  "prior_blocked_nonpromoted": PRIOR_BLOCKED_SEAL},
        "limits": {"wall_s": 300, "rss_kib": RSS_CAP_KIB, "evidence_bytes": DISK_CAP_BYTES},
    })

    clocks = _clock_models(CLOCK_TABLE)
    bridges = _beacon_boundary_bridges(CLOCK_TABLE)
    lo_ns, hi_ns, _ = _action_bounds_global_ns(PHYSICAL_DIRECTORY[ACTION], bridges)
    stop_ns = lo_ns + PREFIX_NS
    if stop_ns > hi_ns:
        raise RuntimeError("five-second prefix exceeds action")
    clock_doc = json.loads(CLOCK_TABLE.read_text())
    node_clocks = {
        node: DirectNodeLinkClock(node, value.a_ns_per_us, value.b_ns, value.boot_epoch,
            int(clock_doc["models"][node]["first_timer_us"]),
            int(clock_doc["models"][node]["last_timer_us"]))
        for node, value in clocks.items()
    }
    anchors, delays, tag_delay, _layout_sigma = _load_layout()
    trajectory, pose_clocks, pose_audit = _verified_pose_inputs()
    calibration = load_frozen_c2_3a()
    alignment, _ = frozen_world_alignment(calibration)
    provider = _PoseProvider(trajectory=trajectory, clocks=pose_clocks, alignment=alignment)
    episode = _load_episode(ACTION, clocks, bridges)
    groups = [g for g in episode["groups"] if lo_ns <= _reference_time(g, clocks) * 1e9 < stop_ns]
    group_ids = {id(raw_row): index for index, group in enumerate(groups) for raw_row in group}
    uwb_rows = [(float(_reference_time([row], clocks)), row) for group in groups for row in group]
    uwb_rows.sort(key=lambda item: (item[0], str(item[1].node)))
    events, decode_audit = decode_measurements(raw)
    imu, orientation_audit = _pelvis_imu(events, clocks[PELVIS_NODE], lo_ns, 0.0)
    imu = [row for row in imu if lo_ns * 1e-9 < row["time_s"] < stop_ns * 1e-9]
    timeline = [(row["time_s"], 0, row) for row in imu]
    timeline.extend((epoch, 1, row) for epoch, row in uwb_rows)
    timeline.sort(key=lambda item: (item[0], item[1], str(getattr(item[2], "node", ""))))

    initial = np.array([np.mean(anchors[:, 0]), np.mean(anchors[:, 1]), .95])
    vector = np.r_[initial, np.zeros(6)]
    state = RootState(lo_ns * 1e-9, vector, np.diag([1.] * 6 + [.04] * 3))
    root_config = RootFilterConfig()
    last_force = np.array([0., 0., 9.80665])
    last_rotation = np.eye(3)
    trackers: dict[str, PersistentRangeBiasTracker] = {}
    output_rows = []
    weights_by_anchor: dict[int, list[float]] = defaultdict(list)
    all_weights: list[float] = []
    half_round_us: list[float] = []
    motion_proxy_m: list[float] = []
    innovations: list[float] = []
    standardized: list[float] = []
    prior_nis: list[float] = []
    effective_nis: list[float] = []
    conditions: list[float] = []
    reasons: dict[str, int] = defaultdict(int)
    accepted_updates = 0
    allocated_nodes: set[str] = set()
    maximum_accepted_link_epoch_s: float | None = None

    for event_time, kind, payload in timeline:
        if time.perf_counter() - started > 285:
            raise TimeoutError("U5B exhausted finalization reserve")
        if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss >= RSS_CAP_KIB:
            raise MemoryError("U5B RSS cap exceeded")
        state, _ = propagate_inertial(state, event_time, last_force, last_rotation, root_config)
        if kind == 0:
            last_force = np.asarray(payload["acceleration"], dtype=float)
            last_rotation = np.asarray(payload["rotation_world"], dtype=float)
            continue
        row = payload
        slots = tuple(_valid_slots(row))
        if len(slots) < 4:
            reasons["FEWER_THAN_FOUR_STRUCTURALLY_VALID_LINKS"] += 1
            continue
        node = str(row.node)
        link_ns = {anchor: node_clocks[node].link_time_ns(
            event_boot_epoch=row.boot, strobe_us=row.strobe_us,
            t_round_us=float(row.t_round_us[anchor])) for anchor in slots}
        query_ns = min(link_ns.values())
        root_at_query = state.position_m + (query_ns * 1e-9 - state.time_s) * state.velocity_mps
        snapshot = provider.snapshot(action=ACTION, sweep_query_ns=query_ns, root_world_m=root_at_query)
        evidence = {anchor: direct_shadow_evidence(
            node=node, anchor_position_world_m=anchors[anchor], snapshot=snapshot,
            geometry=calibration.geometry) for anchor in slots}
        weight_array = np.ones(8)
        for anchor in slots:
            weight_array[anchor] = evidence[anchor].b_combined_weight
        external = ExternalRangeInformationWeights(
            node, snapshot.pose_global_ns * 1e-9, weight_array,
            "STRICT_PRE_EPOCH_DIRECT_BODY_SHADOW_DISPLAY_PROXY")
        tracker = trackers.setdefault(node, PersistentRangeBiasTracker())
        allocated_nodes.add(node)
        bias = tracker.prior_snapshot(
            node, snapshot_time_s=snapshot.pose_global_ns * 1e-9)
        combined_bias = RangeBiasPriorSnapshot(
            node, bias.snapshot_time_s,
            bias.mean_m + np.asarray(delays) + float(tag_delay),
            bias.variance_m2, bias.last_accepted_time_s)
        factor = linearize_raw_range_factors(
            state, row, anchors_m=anchors, clock=clocks[node],
            bias_prior=combined_bias, information_weights=external,
            tag_offset_world_m=snapshot.offsets_world_m[node], config=config)
        effective_s = (
            factor.state_jacobian @ state.covariance @ factor.state_jacobian.T
            + np.diag(np.diag(factor.r_prior_m2) / factor.robust_weights)
        )
        eff_nis = float(factor.innovations_m @ np.linalg.solve(effective_s, factor.innovations_m))
        prior_velocity = state.velocity_mps.copy()
        updated, decision = update_raw_ranges(
            state, row, anchors_m=anchors, clock=clocks[node],
            bias_prior=combined_bias, information_weights=external,
            tag_offset_world_m=snapshot.offsets_world_m[node], config=config)
        reasons[decision.reason] += 1
        if decision.accepted:
            state = updated
            tracker.update(node, decision)
            accepted_updates += 1
            accepted_maximum = float(np.max(decision.link_epochs_s))
            maximum_accepted_link_epoch_s = (
                accepted_maximum if maximum_accepted_link_epoch_s is None
                else max(maximum_accepted_link_epoch_s, accepted_maximum)
            )
        for local, anchor in enumerate(factor.anchors):
            weight = float(factor.information_weights[local])
            all_weights.append(weight); weights_by_anchor[anchor].append(weight)
            half = 0.5 * float(row.t_round_us[anchor])
            half_round_us.append(half)
            unit = factor.state_jacobian[local, :3]
            motion_proxy_m.append(abs(float(unit @ prior_velocity)) * half * 1e-6)
            innovations.append(float(factor.innovations_m[local]))
            standardized.append(float(
                factor.innovations_m[local] / math.sqrt(factor.r_prior_m2[local, local])))
        prior_nis.append(factor.prior_nis); effective_nis.append(eff_nis)
        conditions.append(factor.condition)
        output_rows.append({
            "group_index": group_ids.get(id(row)), "node": node,
            "reference_time_s": factor.reference_epoch_s, "links": len(factor.anchors),
            "anchors": list(factor.anchors), "weights": factor.information_weights.tolist(),
            "half_t_round_us": [0.5 * float(row.t_round_us[a]) for a in factor.anchors],
            "prior_nis": factor.prior_nis, "irls_effective_nis": eff_nis,
            "rank": factor.rank, "condition": factor.condition,
            "update_reason": decision.reason, "accepted": decision.accepted,
        })

    if sha256(raw) != raw_hash:
        raise RuntimeError("raw action04 input changed")
    with (args.output / "SWEEPS.jsonl").open("w") as stream:
        for row in output_rows:
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    positive = [x for x in innovations if x >= 0]
    negative = [x for x in innovations if x < 0]
    positive_z = [x for x in standardized if x >= 0]
    negative_z = [x for x in standardized if x < 0]
    final_bias_query_epoch_s = final_bias_snapshot_epoch(maximum_accepted_link_epoch_s)
    pre_summary_resource = {
        "wall_s": time.perf_counter() - started,
        "maximum_rss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        "output_bytes": sum(path.stat().st_size for path in args.output.iterdir() if path.is_file()),
        "maximum_accepted_link_epoch_s": maximum_accepted_link_epoch_s,
        "final_bias_snapshot_query_epoch_s": final_bias_query_epoch_s,
    }
    write_json(args.output / "RESOURCE_BEFORE_SUMMARY.json", pre_summary_resource)
    if (
        pre_summary_resource["wall_s"] > 300
        or pre_summary_resource["maximum_rss_kib"] >= RSS_CAP_KIB
        or pre_summary_resource["output_bytes"] >= DISK_CAP_BYTES
    ):
        raise RuntimeError("U5B pre-summary resource gate failed")
    end_bias = {}
    for node, tracker in sorted(trackers.items()):
        if final_bias_query_epoch_s is None:
            raise RuntimeError("no accepted decision exists for persistent-bias summary")
        prior = tracker.prior_snapshot(node, snapshot_time_s=final_bias_query_epoch_s)
        end_bias[node] = {"mean_m": prior.mean_m.tolist(),
                          "variance_m2": prior.variance_m2.tolist(),
                          "last_accepted_time_s": prior.last_accepted_time_s.tolist()}
    rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    result = {
        "status": "U5B_ACTION04_FIRST5S_ENGINEERING_DIAGNOSTIC_COMPLETE",
        "calibrated_R": False, "scientific_pass": False, "production_ready": False,
        "transaction_promotion": False, "raw_opened": True, "HXX_opened": False,
        "action": ACTION, "prefix_start_common_ns": lo_ns,
        "prefix_stop_common_ns_exclusive": stop_ns,
        "metric_scope": "GROUPS_WITH_REFERENCE_EPOCH_IN_ACTION04_FIRST_5S_WITH_MEASURED_PER_LINK_EPOCH_OVERHANG",
        "maximum_accepted_link_epoch_s": maximum_accepted_link_epoch_s,
        "final_bias_snapshot_query_epoch_s": final_bias_query_epoch_s,
        "decoded": decode_audit.__dict__, "imu_samples_in_prefix": len(imu),
        "uwb_groups": len(groups), "node_sweeps": len(output_rows),
        "structurally_valid_links": len(all_weights), "range_deletions": 0,
        "weights": stats(all_weights),
        "weights_by_anchor": {str(k): stats(v) for k, v in sorted(weights_by_anchor.items())},
        "half_t_round_us": stats(half_round_us),
        "root_radial_motion_risk_proxy_m": stats(motion_proxy_m),
        "innovation_m": {"positive": stats(positive), "negative": stats(negative)},
        "standardized_by_total_prior_sigma": {"positive": stats(positive_z), "negative": stats(negative_z)},
        "prior_nis": stats(prior_nis), "irls_effective_nis": stats(effective_nis),
        "rank": {"minimum": min(row["rank"] for row in output_rows),
                 "all_rank3": all(row["rank"] == 3 for row in output_rows)},
        "condition": stats(conditions), "update_reasons": dict(sorted(reasons.items())),
        "accepted_bias_updates": accepted_updates,
        "persistent_bias_allocated_nodes": len(allocated_nodes), "persistent_bias_end": end_bias,
        "pose_owner": pose_audit, "orientation": orientation_audit,
        "uncertainty": "PROVISIONAL_UNCALIBRATED_DIAGNOSTIC;ROOT_BIAS_CROSS_COVARIANCE_UNAVAILABLE",
        "wall_s": time.perf_counter() - started, "maximum_rss_kib": rss,
        "source_hashes": {
            "tight_range.py": sha256(ROOT / "src/biospur_fusion/c2_uwb_root_world/tight_range.py"),
            "direct_body_shadow_ab.py": sha256(ROOT / "src/biospur_fusion/c2_uwb_calibration/direct_body_shadow_ab.py"),
            "offline_unified_wiring.py": sha256(ROOT / "src/biospur_fusion/c2_uwb_root_world/offline_unified_wiring.py"),
            "tool": sha256(Path(__file__)),
        },
        "input_hashes": {str(raw.relative_to(ROOT)): raw_hash,
                         str(CLOCK_TABLE.relative_to(ROOT)): sha256(CLOCK_TABLE)},
        "bound_seals": {"u5a": U5A_SEAL, "u3": U3_SEAL, "u3_correction": U3_CORRECTION_SEAL,
                        "prior_blocked_nonpromoted": PRIOR_BLOCKED_SEAL},
        "pre_summary_resource": pre_summary_resource,
    }
    write_json(args.output / "RESULT.json", result)
    (args.output / "COMMAND.txt").write_text(command + "\n")
    (args.output / "REPORT.md").write_text(
        "# U5B action04 first-five-second engineering diagnostic\n\n"
        f"Processed {len(groups)} groups, {len(output_rows)} node sweeps, and "
        f"{len(all_weights)} structurally valid links without range deletion. "
        f"Accepted bias updates: {accepted_updates}.\n\n"
        "The per-link epochs use measured strobe+t_round/2 and body weights use only "
        "strictly prior display-proxy geometry. R, tail scale, body proxy, bias process "
        "noise, and root/bias cross-covariance remain uncalibrated. This is not an "
        "accuracy, scientific, production, or transaction-promotion result.\n")
    size = sum(p.stat().st_size for p in args.output.iterdir() if p.is_file())
    if rss >= RSS_CAP_KIB or size >= DISK_CAP_BYTES or result["wall_s"] > 300:
        raise RuntimeError("U5B resource gate failed")
    digest = seal(args.output)
    print(json.dumps({"status": result["status"], "seal_sha256": digest,
                      "wall_s": result["wall_s"], "rss_kib": rss,
                      "bytes": size}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException as exc:
        seal_failure_from_argv(exc)
        raise
