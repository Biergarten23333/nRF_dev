#!/usr/bin/env python3
"""Qualify the direct A/B-only 75-evaluation owner on a sealed prefix."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import resource
import struct
import time
from typing import Any, Callable

import numpy as np

import biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab as direct_owner
from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_coupled_progressive.contracts import NODE_TO_SEGMENT
from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import (
    DirectNodeLinkClock,
    DirectShadowPolicy,
    PoseUnavailableError,
    commit_a_after_paired_results,
    direct_shadow_evidence,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import frozen_world_alignment
from biospur_fusion.c2_uwb_calibration.shared_root import SharedRangeLink, SharedRootResult
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    _beacon_boundary_bridges,
    _clock_models,
)
from evaluate_c2_pair_bias_gate import (
    _base_sigma,
    _load_episode,
    _load_layout,
    _prediction,
    _reference_time,
    _tracker,
    _update_tracker,
    _valid_slots,
)
import run_c2_direct_body_shadow_ab_pilot as runner


ROOT = runner.ROOT
PILOT = ROOT / "logs/c2_direct_body_shadow_ab_pilot_v2_20260906T102400Z"
PILOT_SHA256 = "f9af62e1134e459cde5b13ab7077b440082df215787551e820d3c8c41d6f35cc"
DIAGNOSTIC = ROOT / "logs/c2_direct_body_shadow_ab_solver_diagnostic_20260906T102754Z"
DIAGNOSTIC_SHA256 = "511a19f6efcdeafbc11c71ed88e68541d487f925d603ca2f129116f479cd0ef9"
TARGET = ("06_elbow_left", 36, "BSF44AD")
EXPECTED_PREFIX_ROWS = 5_352
SERVICE_INTERVAL_MS = 1000.0 / (10.0 * 8.33)
FEATURE_P99_GATE_MS = 5.0
HARD_WALL_S = 300.0
DISK_CAP_BYTES = 50_000_000
RSS_CAP_KB = 1_500_000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _directory_bytes(path: Path) -> int:
    return sum(row.stat().st_size for row in path.rglob("*") if row.is_file())


def _seal(path: Path) -> str:
    rows = []
    for item in sorted(row for row in path.rglob("*") if row.is_file()):
        if item.name == "SHA256SUMS":
            continue
        rows.append(f"{_sha256(item)}  {item.relative_to(path)}")
    (path / "SHA256SUMS").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return _sha256(path / "SHA256SUMS")


def _value_bytes(value: Any) -> bytes:
    if isinstance(value, np.ndarray):
        array = np.ascontiguousarray(value)
        return (
            str(array.dtype).encode() + b"\0"
            + json.dumps(list(array.shape)).encode() + b"\0" + array.tobytes()
        )
    if isinstance(value, float):
        return struct.pack("!d", value)
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _assert_result_bitwise_equal(left: SharedRootResult, right: SharedRootResult) -> None:
    for name in SharedRootResult.__dataclass_fields__:
        if _value_bytes(getattr(left, name)) != _value_bytes(getattr(right, name)):
            raise RuntimeError(f"SharedRootResult changed at field {name}")


def _assert_float_exact(actual: float | None, expected: float | None, label: str) -> None:
    if actual is None or expected is None:
        if actual is not expected:
            raise RuntimeError(f"sealed derived field changed: {label}")
    elif _value_bytes(float(actual)) != _value_bytes(float(expected)):
        raise RuntimeError(f"sealed derived field changed: {label}")


def _quantile(values: list[float], q: float) -> float | None:
    return None if not values else float(np.quantile(np.asarray(values), q))


def _timed_pair(
    links: tuple[SharedRangeLink, ...], *, evidence_by_identity: dict,
    anchors_m: np.ndarray, initial_root_m: np.ndarray,
    root_velocity_mps: np.ndarray, policy: DirectShadowPolicy,
) -> tuple[Any, float, tuple[float, float], float]:
    original = direct_owner.solve_shared_root
    calls: list[float] = []

    def timed(*args: Any, **kwargs: Any) -> SharedRootResult:
        started = time.perf_counter()
        result = original(*args, **kwargs)
        calls.append((time.perf_counter() - started) * 1000.0)
        return result

    direct_owner.solve_shared_root = timed
    started = time.perf_counter()
    try:
        result = direct_owner.solve_direct_ab(
            links,
            evidence_by_identity=evidence_by_identity,
            anchors_m=anchors_m,
            initial_root_m=initial_root_m,
            root_velocity_mps=root_velocity_mps,
            policy=policy,
        )
    finally:
        direct_owner.solve_shared_root = original
    paired_ms = (time.perf_counter() - started) * 1000.0
    if len(calls) != 2:
        raise RuntimeError("direct owner did not call shared solver exactly twice")
    prepare_ms = paired_ms - sum(calls)
    if prepare_ms < -1e-9:
        raise RuntimeError("invalid direct preparation timing")
    return result, paired_ms, (calls[0], calls[1]), max(0.0, prepare_ms)


def _assert_sealed_row(
    row: dict[str, Any], paired: Any, *, action: str, group_index: int,
    node: str, snapshot: Any, links: tuple[SharedRangeLink, ...],
    previous_outputs: dict[str, tuple[float, np.ndarray, np.ndarray]],
    reference_s: float,
) -> None:
    if (row["action"], row["source_group_index"], row["node"]) != (
        action, group_index, node
    ):
        raise RuntimeError("sealed row order/identity changed")
    if row["identities"] != [[link.node, link.anchor] for link in links]:
        raise RuntimeError("sealed physical link identity changed")
    for label, actual, expected in (
        ("reference_time_s", reference_s, row["reference_time_s"]),
        ("pose_time_ns", float(snapshot.pose_global_ns), float(row["pose_time_ns"])),
        ("sweep_query_time_ns", snapshot.query_global_ns, row["sweep_query_time_ns"]),
        ("pose_age_ms", snapshot.pose_age_ns * 1e-6, row["pose_age_ms"]),
        ("preoutcome_condition", paired.prepared.geometry.condition, row["preoutcome_condition"]),
        ("a_condition", paired.a_result.condition, row["a_condition"]),
        ("b_condition", paired.b_result.condition, row["b_condition"]),
        ("a_weighted_solver_cost", paired.a_result.cost, row["a_weighted_solver_cost"]),
        ("b_weighted_solver_cost", paired.b_result.cost, row["b_weighted_solver_cost"]),
    ):
        _assert_float_exact(float(actual), float(expected), label)
    if snapshot.frame != row["pose_frame"] or paired.prepared.geometry.rank != row["preoutcome_rank"]:
        raise RuntimeError("sealed pose/rank changed")
    for label, actual, expected in (
        ("a_weights", [link.information_weight for link in paired.prepared.a_links], row["a_weights"]),
        ("b_weights", [link.information_weight for link in paired.prepared.b_links], row["b_weights"]),
        ("a_root_m", paired.a_result.root_position_m.tolist(), row["a_root_m"]),
        ("b_root_m", paired.b_result.root_position_m.tolist(), row["b_root_m"]),
        ("a_material_prefix", [list(x) for x in paired.prepared.a_material.support_prefix_identities], row["a_material_prefix"]),
        ("b_material_prefix", [list(x) for x in paired.prepared.b_material.support_prefix_identities], row["b_material_prefix"]),
        ("a_reliability_order", [list(x) for x in paired.prepared.a_material.ordered_identities], row["a_reliability_order"]),
        ("b_reliability_order", [list(x) for x in paired.prepared.b_material.ordered_identities], row["b_reliability_order"]),
    ):
        if _value_bytes(actual) != _value_bytes(expected):
            raise RuntimeError(f"sealed derived field changed: {label}")
    a_residual = np.asarray(paired.a_result.residuals_m)
    b_residual = np.asarray(paired.b_result.residuals_m)
    derived = {
        "a_unweighted_physical_residual_rms_m": float(np.sqrt(np.mean(a_residual**2))),
        "b_unweighted_physical_residual_rms_m": float(np.sqrt(np.mean(b_residual**2))),
        "a_positive_tail_q90_m": float(np.quantile(np.maximum(a_residual, 0.0), 0.90)),
        "b_positive_tail_q90_m": float(np.quantile(np.maximum(b_residual, 0.0), 0.90)),
        "a_b_displacement_m": float(np.linalg.norm(paired.b_result.root_position_m - paired.a_result.root_position_m)),
    }
    if node in previous_outputs:
        previous_time, previous_a, previous_b = previous_outputs[node]
        output_dt = reference_s - previous_time
        a_step = float(np.linalg.norm(paired.a_result.root_position_m - previous_a))
        b_step = float(np.linalg.norm(paired.b_result.root_position_m - previous_b))
        derived.update({
            "output_dt_s": output_dt,
            "a_root_step_m": a_step,
            "b_root_step_m": b_step,
            "a_root_speed_mps": a_step / output_dt,
            "b_root_speed_mps": b_step / output_dt,
        })
    else:
        derived.update({
            "output_dt_s": None, "a_root_step_m": None, "b_root_step_m": None,
            "a_root_speed_mps": None, "b_root_speed_mps": None,
        })
    for label, actual in derived.items():
        _assert_float_exact(actual, row[label], label)


def _qualify(output: Path) -> dict[str, Any]:
    started = time.perf_counter()
    runner._verify_seal(PILOT, PILOT_SHA256)
    runner._verify_seal(DIAGNOSTIC, DIAGNOSTIC_SHA256)
    diagnostic = json.loads((DIAGNOSTIC / "RESULT.json").read_text())
    failure = json.loads((PILOT / "FAILURE.json").read_text())
    if tuple(failure["failure_context"][key] for key in ("action", "source_group_index", "node")) != TARGET:
        raise RuntimeError("sealed blocker identity changed")
    trajectory, pose_clocks, pose_audit = runner._verified_pose_inputs()
    clocks = _clock_models(runner.CLOCK_TABLE)
    clock_document = json.loads(runner.CLOCK_TABLE.read_text())
    bridges = _beacon_boundary_bridges(runner.CLOCK_TABLE)
    anchors, delays, tag_delay, layout_sigma = _load_layout()
    calibration = load_frozen_c2_3a()
    alignment, _ = frozen_world_alignment(calibration)
    provider = runner._PoseProvider(trajectory=trajectory, clocks=pose_clocks, alignment=alignment)
    node_clocks = {
        node: DirectNodeLinkClock(
            node=node, a_ns_per_us=clock.a_ns_per_us, b_ns=clock.b_ns,
            boot_epoch=clock.boot_epoch,
            first_timer_us=int(clock_document["models"][node]["first_timer_us"]),
            last_timer_us=int(clock_document["models"][node]["last_timer_us"]),
        ) for node, clock in clocks.items()
    }
    room_initial = np.array([float(np.mean(anchors[:, 0])), float(np.mean(anchors[:, 1])), 0.95])
    policy50 = DirectShadowPolicy(maximum_nfev=50)
    policy75 = DirectShadowPolicy()
    feature_ms: list[float] = []
    link_prep_ms: list[float] = []
    a75_ms: list[float] = []
    b75_ms: list[float] = []
    paired75_ms: list[float] = []
    online_b_ms: list[float] = []
    prefix_digest = hashlib.sha256()
    compared = 0
    pose_unavailable = 0
    blocker_result: dict[str, Any] | None = None
    rows_stream = (PILOT / "AB_SWEEPS.jsonl").open("r", encoding="utf-8")
    try:
        for action in runner.PILOT_ACTIONS:
            trackers = {node: _tracker(room_initial) for node in NODE_TO_SEGMENT}
            previous_outputs: dict[str, tuple[float, np.ndarray, np.ndarray]] = {}
            with runner._accelerated_transport_crc():
                episode = _load_episode(action, clocks, bridges)
            for group_index, group in enumerate(episode["groups"]):
                ordered = sorted(group, key=lambda raw: (
                    clocks[raw.node].a_ns_per_us * raw.strobe_us + clocks[raw.node].b_ns,
                    raw.node,
                ))
                for raw in ordered:
                    if time.perf_counter() - started > HARD_WALL_S - 15.0:
                        raise TimeoutError("qualification exhausted seal reserve")
                    node = str(raw.node)
                    slots = tuple(_valid_slots(raw))
                    reference_s = _reference_time([raw], clocks)
                    tracker = trackers[node]
                    predicted, dt = _prediction(tracker, reference_s)
                    velocity = np.asarray(tracker["velocity"], dtype=float).copy()
                    link_times = {
                        anchor: node_clocks[node].link_time_ns(
                            event_boot_epoch=int(raw.boot), strobe_us=int(raw.strobe_us),
                            t_round_us=float(raw.t_round_us[anchor]),
                        ) for anchor in slots
                    }
                    query = min(link_times.values())
                    root_snapshot = predicted + (query * 1e-9 - reference_s) * velocity
                    feature_started = time.perf_counter()
                    try:
                        snapshot = provider.snapshot(
                            action=action, sweep_query_ns=query, root_world_m=root_snapshot
                        )
                    except PoseUnavailableError:
                        pose_unavailable += 1
                        continue
                    evidence = {
                        (node, anchor): direct_shadow_evidence(
                            node=node, anchor_position_world_m=anchors[anchor],
                            snapshot=snapshot, geometry=calibration.geometry,
                        ) for anchor in slots
                    }
                    feature_elapsed = (time.perf_counter() - feature_started) * 1000.0
                    link_started = time.perf_counter()
                    links = tuple(SharedRangeLink(
                        node=node, anchor=anchor,
                        range_m=float(raw.ranges_mm[anchor]) / 1000.0 - float(delays[anchor]) - float(tag_delay),
                        tag_offset_world_m=snapshot.offsets_world_m[node],
                        link_dt_s=link_times[anchor] * 1e-9 - reference_s,
                        sigma_m=_base_sigma(layout_sigma, int(raw.quality[anchor])),
                        facing_score=evidence[(node, anchor)].own_facing_score,
                    ) for anchor in slots)
                    link_elapsed = (time.perf_counter() - link_started) * 1000.0
                    result50, _pair50_ms, calls50, _prepare50 = _timed_pair(
                        links, evidence_by_identity=evidence, anchors_m=anchors,
                        initial_root_m=predicted, root_velocity_mps=velocity, policy=policy50,
                    )
                    result75, pair75_elapsed, calls75, prepare75 = _timed_pair(
                        links, evidence_by_identity=evidence, anchors_m=anchors,
                        initial_root_m=predicted, root_velocity_mps=velocity, policy=policy75,
                    )
                    is_target = (action, group_index, node) == TARGET
                    if is_target:
                        sealed = failure["failure_context"]
                        if not (
                            result50.a_result.reason == "ACCEPTED" and result50.a_result.nfev == 16
                            and result50.b_result.reason == "OPTIMIZER_FAILURE" and result50.b_result.nfev == 50
                            and result75.a_result.reason == "ACCEPTED"
                            and result75.b_result.reason == "ACCEPTED" and result75.b_result.nfev == 57
                            and np.array_equal(np.asarray(sealed["a_weights"]), np.asarray([x.information_weight for x in result50.prepared.a_links]))
                            and np.array_equal(np.asarray(sealed["b_weights"]), np.asarray([x.information_weight for x in result50.prepared.b_links]))
                        ):
                            raise RuntimeError("sealed blocker cap50/cap75 outcome changed")
                        cap150 = diagnostic["cap150_diagnostic_only"]
                        if not (
                            np.array_equal(result75.b_result.root_position_m, np.asarray(cap150["final_x_m"]))
                            and result75.b_result.cost == cap150["cost"]
                            and np.array_equal(result75.b_result.residuals_m, np.asarray(cap150["physical_residuals_m"]))
                            and np.array_equal(result75.b_result.standardized_residuals, np.asarray(cap150["standardized_residuals"]))
                            and result75.b_result.condition == cap150["condition"]
                        ):
                            raise RuntimeError("cap75 blocker endpoint differs from sealed cap150")
                        blocker_result = {
                            "cap50": {"A_reason": result50.a_result.reason, "A_nfev": result50.a_result.nfev, "B_reason": result50.b_result.reason, "B_nfev": result50.b_result.nfev},
                            "cap75": {"A_reason": result75.a_result.reason, "A_nfev": result75.a_result.nfev, "B_reason": result75.b_result.reason, "B_nfev": result75.b_result.nfev, "B_root_m": result75.b_result.root_position_m.tolist(), "B_cost": result75.b_result.cost},
                            "equals_sealed_cap150_endpoint": True,
                        }
                        feature_ms.append(feature_elapsed)
                        link_prep_ms.append(link_elapsed)
                        a75_ms.append(calls75[0]); b75_ms.append(calls75[1]); paired75_ms.append(feature_elapsed + link_elapsed + pair75_elapsed)
                        online_b_ms.append(feature_elapsed + link_elapsed + prepare75 + calls75[1])
                        break
                    line = rows_stream.readline()
                    if not line:
                        raise RuntimeError("sealed AB rows ended before blocker")
                    row = json.loads(line)
                    _assert_result_bitwise_equal(result50.a_result, result75.a_result)
                    _assert_result_bitwise_equal(result50.b_result, result75.b_result)
                    if not (result50.a_result.success and result50.b_result.success):
                        raise RuntimeError("prior sealed success no longer succeeds")
                    if result50.a_result.nfev >= 50 or result50.b_result.nfev >= 50:
                        raise RuntimeError("prior success reached the old cap")
                    _assert_sealed_row(
                        row, result50, action=action, group_index=group_index,
                        node=node, snapshot=snapshot, links=links,
                        previous_outputs=previous_outputs, reference_s=reference_s,
                    )
                    prefix_digest.update(line.encode("utf-8"))
                    compared += 1
                    feature_ms.append(feature_elapsed); link_prep_ms.append(link_elapsed)
                    a75_ms.append(calls75[0]); b75_ms.append(calls75[1])
                    paired75_ms.append(feature_elapsed + link_elapsed + pair75_elapsed)
                    online_b_ms.append(feature_elapsed + link_elapsed + prepare75 + calls75[1])
                    previous_outputs[node] = (
                        reference_s, result50.a_result.root_position_m.copy(),
                        result50.b_result.root_position_m.copy(),
                    )
                    commit_a_after_paired_results(
                        result50,
                        lambda root, tracker=tracker, reference_s=reference_s, dt=dt:
                            _update_tracker(tracker, root, reference_s, dt),
                    )
                    if compared % 500 == 0:
                        _write_json(output / "CHECKPOINT.json", {
                            "compared": compared, "action": action,
                            "source_group_index": group_index, "node": node,
                            "wall_s": time.perf_counter() - started,
                        })
                if blocker_result is not None:
                    break
            if blocker_result is not None:
                break
        if rows_stream.readline():
            raise RuntimeError("sealed AB prefix contains unexpected rows after blocker")
    finally:
        rows_stream.close()
    if compared != EXPECTED_PREFIX_ROWS or blocker_result is None:
        raise RuntimeError("sealed prefix/blocker count changed")

    feature_p99 = _quantile(feature_ms, 0.99)
    online_p99 = _quantile(online_b_ms, 0.99)
    gates = {
        "prefix_rows_exact": compared == EXPECTED_PREFIX_ROWS,
        "cap50_cap75_prior_results_bitwise_equal": True,
        "all_prior_nfev_below_50": True,
        "blocker_cap75_equals_sealed_cap150": True,
        "feature_p99_below_5ms": feature_p99 is not None and feature_p99 < FEATURE_P99_GATE_MS,
        "online_B_core_p99_below_service_interval": online_p99 is not None and online_p99 < SERVICE_INTERVAL_MS,
        "online_B_core_max_below_service_interval": max(online_b_ms) < SERVICE_INTERVAL_MS,
        "rss_below_cap": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss < RSS_CAP_KB,
    }
    status = "READY_FOR_ONE_04_07_PILOT_REVIEW" if all(gates.values()) else "BLOCKED_CAP75_QUALIFICATION_GATE"
    return {
        "status": status,
        "qualification_ceiling": "READY_FOR_ONE_04_07_PILOT_REVIEW",
        "scientific_pass": False,
        "pilot_rerun": False,
        "HXX_opened": False,
        "prefix_rows_compared": compared,
        "pose_unavailable_skipped_per_sealed_policy": pose_unavailable,
        "prefix_jsonl_sha256": prefix_digest.hexdigest(),
        "blocker": blocker_result,
        "timing_ms": {
            "feature": {"p50": _quantile(feature_ms, .5), "p99": feature_p99, "max": max(feature_ms)},
            "link_prep": {"p50": _quantile(link_prep_ms, .5), "p99": _quantile(link_prep_ms, .99), "max": max(link_prep_ms)},
            "A75_solve": {"p50": _quantile(a75_ms, .5), "p99": _quantile(a75_ms, .99), "max": max(a75_ms)},
            "B75_solve": {"p50": _quantile(b75_ms, .5), "p99": _quantile(b75_ms, .99), "max": max(b75_ms)},
            "single_branch_online_B_core": {"definition": "strict-floor/cache/FK/features+link prep+shared material prep+B75 solve", "p50": _quantile(online_b_ms, .5), "p99": online_p99, "max": max(online_b_ms), "gate_ms": SERVICE_INTERVAL_MS},
            "paired_A_B_diagnostic": {"definition": "strict-floor/cache/FK/features+link prep+shared material prep+A75+B75", "p50": _quantile(paired75_ms, .5), "p99": _quantile(paired75_ms, .99), "max": max(paired75_ms), "acceptance_gate": False},
        },
        "gates": gates,
        "pose_input_audit": pose_audit,
        "policy": {"maximum_nfev": policy75.maximum_nfev, "service_interval_ms": SERVICE_INTERVAL_MS, "cache_cap": runner.POSE_CACHE_MAXIMUM},
        "source_hashes": {str(path.relative_to(ROOT)): _sha256(path) for path in (*runner._source_paths(), Path(__file__).resolve())},
        "input_seals": {"pilot": PILOT_SHA256, "diagnostic": DIAGNOSTIC_SHA256},
        "wall_s": time.perf_counter() - started,
        "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    try:
        result = _qualify(args.output)
        _write_json(args.output / "RESULT.json", result)
        (args.output / "REPORT.md").write_text(
            "# Direct A/B cap-75 owner qualification\n\n"
            f"Status: `{result['status']}`. This is a sealed-prefix qualification, "
            "not a pilot rerun or scientific result.\n",
            encoding="utf-8",
        )
        if _directory_bytes(args.output) >= DISK_CAP_BYTES:
            raise RuntimeError("qualification output exceeded disk cap")
        _seal(args.output)
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 0 if not result["status"].startswith("BLOCKED") else 2
    except BaseException as exc:
        failure = {
            "status": "BLOCKED_CAP75_QUALIFICATION",
            "reason": f"{type(exc).__name__}: {exc}",
            "wall_s": None,
            "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "pilot_rerun": False,
            "HXX_opened": False,
        }
        _write_json(args.output / "FAILURE.json", failure)
        _seal(args.output)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
