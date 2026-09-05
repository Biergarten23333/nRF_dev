#!/usr/bin/env python3
"""Fit held-node pair biases on C2 00 and blind-test them on C2 02."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any
from collections.abc import Callable

import numpy as np

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_uwb_calibration.antenna_los import (
    outward_facing_reliability,
    outward_facing_score,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import (
    NODE_TO_PROXY_POINT,
    body_proxy_at_fraction,
    frozen_world_alignment,
)
from biospur_fusion.c2_uwb_calibration.pair_bias import (
    PairBiasEstimate,
    estimate_pair_bias,
    load_pair_bias_table,
    separate_fixed_bias_from_initial_pose_nlos,
    validate_complete_pair_bias_table,
)
from biospur_fusion.c2_uwb_calibration.shared_root import (
    SharedRangeLink,
    evaluate_shared_root_residuals,
    solve_shared_root,
)
from biospur_fusion.c2_uwb_root_world.calibration import CALIBRATION_ORDER
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    DATASET,
    LAYOUT,
    PHYSICAL_DIRECTORY,
    _action_bounds_global_ns,
    _beacon_boundary_bridges,
    _clock_models,
)
from biospur_fusion.c2_uwb_root_world.u0 import decode_uwb_only


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CLOCK = (
    ROOT / "logs/c2_uwb_beacon_clock_20260903_141552/"
    "CLOCK_TABLE_CALIBRATION_ONLY.json"
)
CALIBRATION_EPISODE = "00_initial_still"
BLIND_EPISODE = "02_t_pose"
EPOCH_NS = 120_000_000
POLICIES = (
    "raw_all", "orientation_soft", "bias_all", "bias_variance", "bias_soft_v2",
    "bounded_bias_variance", "fixed_bias_only",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def _load_layout() -> tuple[np.ndarray, np.ndarray, float, float]:
    document = json.loads(LAYOUT.read_text())
    rows = sorted(document["anchors"], key=lambda row: int(row["id"]))
    if [int(row["id"]) for row in rows] != list(range(8)):
        raise ValueError("layout is not the exact A-H bijection")
    anchors = np.asarray(
        [[row["x_mm"], row["y_mm"], row["z_mm"]] for row in rows], dtype=float
    ) / 1000.0
    delays = np.asarray([row["d_anchor_mm"] for row in rows], dtype=float) / 1000.0
    tag_delay = float(document.get("tag_delay_mm", 0.0)) / 1000.0
    layout_sigma = float(document["stats"]["inter_anchor_pair_rms_mm"]) / 1000.0
    return anchors, delays, tag_delay, layout_sigma


def _global_ns(row: Any, clocks: dict[str, Any]) -> int:
    clock = clocks[row.node]
    return int(round(clock.a_ns_per_us * row.strobe_us + clock.b_ns))


def _valid_slots(row: Any) -> tuple[int, ...]:
    return tuple(
        slot for slot in range(8)
        if row.valid_mask & (1 << slot)
        and int(row.anchor_ids[slot]) == slot
        and 0 < int(row.ranges_mm[slot]) < 0xFFFF
        and math.isfinite(float(row.t_round_us[slot]))
    )


def _load_episode(
    episode: str,
    clocks: dict[str, Any],
    bridges: list[tuple[float, float]],
) -> dict[str, Any]:
    raw = (
        DATASET / "actions" / PHYSICAL_DIRECTORY[episode]
        / "rep_01/raw/fusion_host_raw.cobs.bin"
    )
    rows, decode = decode_uwb_only(raw)
    lo, hi, events = _action_bounds_global_ns(PHYSICAL_DIRECTORY[episode], bridges)
    retained = [
        row for row in rows
        if row.node in clocks and lo <= _global_ns(row, clocks) < hi
    ]
    grouped: dict[int, list[Any]] = defaultdict(list)
    for row in retained:
        grouped[int(round(_global_ns(row, clocks) / EPOCH_NS))].append(row)
    groups = [
        sorted(values, key=lambda row: row.node)
        for _, values in sorted(grouped.items())
        if len(values) == len(NODE_TO_PROXY_POINT)
        and len({row.node for row in values}) == len(NODE_TO_PROXY_POINT)
    ]
    if len(groups) < 10:
        raise RuntimeError(f"{episode} has fewer than ten complete body epochs")
    return {
        "episode": episode,
        "episode_key": f"{CALIBRATION_ORDER.index(episode):02d}",
        "raw": raw,
        "events": events,
        "lo": lo,
        "hi": hi,
        "rows": rows,
        "retained": retained,
        "groups": groups,
        "partial_groups": len(grouped) - len(groups),
        "decode_errors": decode.decode_errors,
    }


def _reference_time(group: list[Any], clocks: dict[str, Any]) -> float:
    values = [
        clocks[row.node].seconds(row.strobe_us + 0.5 * row.t_round_us[slot])
        for row in group for slot in _valid_slots(row)
    ]
    return float(np.median(values))


def _base_sigma(layout_sigma_m: float, quality: int) -> float:
    return math.sqrt(
        (layout_sigma_m * math.sqrt(100.0 / max(1.0, float(quality)))) ** 2
        + 0.10 ** 2
    )


def _tracker(room_initial: np.ndarray) -> dict[str, Any]:
    return {"root": room_initial.copy(), "velocity": np.zeros(3), "time_s": None}


def _prediction(tracker: dict[str, Any], time_s: float) -> tuple[np.ndarray, float]:
    dt = 0.0 if tracker["time_s"] is None else max(0.0, time_s - tracker["time_s"])
    return tracker["root"] + dt * tracker["velocity"], dt


def _update_tracker(
    tracker: dict[str, Any], root: np.ndarray, time_s: float, dt: float
) -> None:
    if tracker["time_s"] is not None and dt > 0.0:
        velocity = (root - tracker["root"]) / dt
        speed = float(np.linalg.norm(velocity))
        if speed > 3.0:
            velocity *= 3.0 / speed
        tracker["velocity"] = 0.75 * tracker["velocity"] + 0.25 * velocity
    tracker["root"] = root.copy()
    tracker["time_s"] = time_s


def _build_links(
    group: list[Any],
    *,
    offsets: dict[str, np.ndarray],
    normals: dict[str, np.ndarray],
    anchors: np.ndarray,
    delays: np.ndarray,
    tag_delay: float,
    layout_sigma: float,
    clocks: dict[str, Any],
    reference_time_s: float,
    predicted_root: np.ndarray,
    velocity: np.ndarray,
    policy: str,
    biases: dict[tuple[str, int], PairBiasEstimate] | None,
) -> list[SharedRangeLink]:
    links = []
    for row in group:
        for slot in _valid_slots(row):
            link_time = clocks[row.node].seconds(
                row.strobe_us + 0.5 * row.t_round_us[slot]
            )
            raw_range = (
                float(row.ranges_mm[slot]) / 1000.0 - delays[slot] - tag_delay
            )
            sigma = _base_sigma(layout_sigma, row.quality[slot])
            corrected = raw_range
            estimate = None if biases is None else biases[(row.node, slot)]
            if estimate is not None and policy not in {"raw_all", "orientation_soft"}:
                if policy in {"bounded_bias_variance", "fixed_bias_only"}:
                    use = separate_fixed_bias_from_initial_pose_nlos(
                        estimate, layout_sigma_m=layout_sigma
                    )
                    corrected -= use.fixed_correction_m
                    if policy == "bounded_bias_variance":
                        sigma = math.hypot(sigma, use.additional_sigma_m)
                    elif not use.initial_pose_nlos_state:
                        sigma = math.hypot(sigma, estimate.robust_sigma_m)
                else:
                    corrected -= estimate.bias_m
                if policy in {"bias_variance", "bias_soft_v2"}:
                    sigma = math.sqrt(sigma ** 2 + estimate.robust_sigma_m ** 2)
            score = outward_facing_score(
                predicted_root + offsets[row.node], anchors[slot], normals[row.node]
            )
            if policy == "orientation_soft":
                sigma /= math.sqrt(outward_facing_reliability(score))
            if policy == "bias_soft_v2":
                predicted_tag = (
                    predicted_root + offsets[row.node]
                    + (link_time - reference_time_s) * velocity
                )
                innovation = corrected - float(
                    np.linalg.norm(anchors[slot] - predicted_tag)
                )
                excess = max(0.0, abs(innovation) - 2.0 * sigma)
                facing_reliability = outward_facing_reliability(score)
                innovation_reliability = 1.0 / (
                    1.0 + (excess / (2.0 * sigma)) ** 2
                )
                reliability = max(
                    0.05, facing_reliability * innovation_reliability
                )
                sigma /= math.sqrt(reliability)
            links.append(
                SharedRangeLink(
                    node=row.node,
                    anchor=slot,
                    range_m=corrected,
                    tag_offset_world_m=offsets[row.node],
                    link_dt_s=link_time - reference_time_s,
                    sigma_m=sigma,
                    facing_score=score,
                )
            )
    return links


def _fit_biases(
    episode: dict[str, Any],
    *,
    kinematics: Any,
    alignment: np.ndarray,
    anchors: np.ndarray,
    delays: np.ndarray,
    tag_delay: float,
    layout_sigma: float,
    clocks: dict[str, Any],
    room_initial: np.ndarray,
) -> tuple[dict[tuple[str, int], PairBiasEstimate], dict[str, Any]]:
    trackers = {node: _tracker(room_initial) for node in NODE_TO_PROXY_POINT}
    residuals: dict[tuple[str, int], list[float]] = defaultdict(list)
    failures = Counter()
    for group in episode["groups"]:
        epoch_ns = int(np.median([_global_ns(row, clocks) for row in group]))
        fraction = (epoch_ns - episode["lo"]) / (episode["hi"] - episode["lo"])
        offsets, normals, _ = body_proxy_at_fraction(
            kinematics, episode["episode_key"], fraction, alignment
        )
        reference_time = _reference_time(group, clocks)
        for held_node, tracker in trackers.items():
            predicted, dt = _prediction(tracker, reference_time)
            links = _build_links(
                group, offsets=offsets, normals=normals, anchors=anchors,
                delays=delays, tag_delay=tag_delay, layout_sigma=layout_sigma,
                clocks=clocks, reference_time_s=reference_time,
                predicted_root=predicted, velocity=tracker["velocity"],
                policy="raw_all", biases=None,
            )
            training = [link for link in links if link.node != held_node]
            validation = [link for link in links if link.node == held_node]
            result = solve_shared_root(
                training, anchors_m=anchors, initial_root_m=predicted,
                root_velocity_mps=tracker["velocity"],
            )
            failures[result.reason] += 1
            if not result.success:
                continue
            held_residual = evaluate_shared_root_residuals(
                validation, anchors_m=anchors,
                root_position_m=result.root_position_m,
                root_velocity_mps=tracker["velocity"],
            )
            for link, residual in zip(validation, held_residual):
                residuals[(held_node, link.anchor)].append(float(residual))
            _update_tracker(tracker, result.root_position_m, reference_time, dt)
    estimates = {
        (node, anchor): estimate_pair_bias(node, anchor, residuals[(node, anchor)])
        for node in NODE_TO_PROXY_POINT for anchor in range(8)
    }
    validate_complete_pair_bias_table(estimates, nodes=NODE_TO_PROXY_POINT)
    audit = {
        "expected_held_node_solves": len(episode["groups"]) * len(NODE_TO_PROXY_POINT),
        "solve_reasons": dict(failures),
        "bias_min_m": float(min(item.bias_m for item in estimates.values())),
        "bias_median_m": float(np.median([item.bias_m for item in estimates.values()])),
        "bias_max_m": float(max(item.bias_m for item in estimates.values())),
        "robust_sigma_median_m": float(
            np.median([item.robust_sigma_m for item in estimates.values()])
        ),
        "minimum_pair_support": min(item.sample_count for item in estimates.values()),
        "maximum_pair_support": max(item.sample_count for item in estimates.values()),
    }
    return estimates, audit


def _summarize_main(records: list[dict[str, Any]]) -> dict[str, Any]:
    accepted = [row for row in records if row["success"]]
    position = np.asarray([row["root"] for row in accepted], dtype=float)
    centre = np.median(position, axis=0)
    step = np.linalg.norm(np.diff(position, axis=0), axis=1)
    return {
        "accepted_epochs": len(accepted),
        "expected_epochs": len(records),
        "acceptance_fraction": float(len(accepted) / len(records)),
        "failure_reasons": dict(Counter(row["reason"] for row in records)),
        "root_radial_mad_m": float(
            1.4826 * np.median(np.linalg.norm(position - centre, axis=1))
        ),
        "root_first_to_last_m": float(np.linalg.norm(position[-1] - position[0])),
        "root_step_p95_m": float(np.quantile(step, 0.95)),
        "root_median_m": centre.tolist(),
        "median_abs_selected_residual_m": float(np.median(np.abs(np.concatenate(
            [np.asarray(row["residuals"], dtype=float) for row in accepted]
        )))),
    }


def _summarize_cv(records: list[dict[str, Any]], expected: int) -> dict[str, Any]:
    accepted = [row for row in records if row["success"]]
    residual = np.concatenate([
        np.asarray(row["residuals"], dtype=float) for row in accepted
    ])
    by_node = {}
    by_pair: dict[str, dict[str, float | int]] = {}
    for node in NODE_TO_PROXY_POINT:
        node_records = [row for row in accepted if row["held_node"] == node]
        node_residual = np.concatenate([
            np.asarray(row["residuals"], dtype=float) for row in node_records
        ])
        by_node[node] = {
            "samples": int(len(node_residual)),
            "median_abs_residual_m": float(np.median(np.abs(node_residual))),
            "p95_abs_residual_m": float(np.quantile(np.abs(node_residual), 0.95)),
        }
        pair_values: dict[int, list[float]] = defaultdict(list)
        for row in node_records:
            for anchor, value in zip(row["anchors"], row["residuals"]):
                pair_values[int(anchor)].append(float(value))
        for anchor in range(8):
            values = np.asarray(pair_values[anchor], dtype=float)
            by_pair[f"{node}:anchor_{anchor}"] = {
                "samples": int(len(values)),
                "median_residual_m": float(np.median(values)),
                "median_abs_residual_m": float(np.median(np.abs(values))),
                "p95_abs_residual_m": float(np.quantile(np.abs(values), 0.95)),
            }
    return {
        "accepted_solves": len(accepted),
        "expected_solves": expected,
        "acceptance_fraction": float(len(accepted) / expected),
        "failure_reasons": dict(Counter(row["reason"] for row in records)),
        "median_abs_held_range_residual_m": float(np.median(np.abs(residual))),
        "p95_abs_held_range_residual_m": float(np.quantile(np.abs(residual), 0.95)),
        "by_held_node": by_node,
        "by_node_anchor_pair": by_pair,
    }


def _blind_test(
    episode: dict[str, Any],
    *,
    biases: dict[tuple[str, int], PairBiasEstimate],
    kinematics: Any,
    alignment: np.ndarray,
    anchors: np.ndarray,
    delays: np.ndarray,
    tag_delay: float,
    layout_sigma: float,
    clocks: dict[str, Any],
    room_initial: np.ndarray,
    policies: tuple[str, ...] = POLICIES,
    cv_policies: tuple[str, ...] = ("raw_all", "bias_soft_v2"),
    proxy_at_fraction: Callable[
        [Any, str, float, np.ndarray],
        tuple[dict[str, np.ndarray], dict[str, np.ndarray], int],
    ] = body_proxy_at_fraction,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if "raw_all" not in policies or set(cv_policies) - set(policies):
        raise ValueError("blind policy ownership is inconsistent")
    trackers = {policy: _tracker(room_initial) for policy in policies}
    main: dict[str, list[dict[str, Any]]] = {policy: [] for policy in policies}
    cv: dict[str, list[dict[str, Any]]] = {policy: [] for policy in cv_policies}
    for group in episode["groups"]:
        epoch_ns = int(np.median([_global_ns(row, clocks) for row in group]))
        fraction = (epoch_ns - episode["lo"]) / (episode["hi"] - episode["lo"])
        offsets, normals, _ = proxy_at_fraction(
            kinematics, episode["episode_key"], fraction, alignment
        )
        reference_time = _reference_time(group, clocks)
        for policy in policies:
            tracker = trackers[policy]
            predicted, dt = _prediction(tracker, reference_time)
            links = _build_links(
                group, offsets=offsets, normals=normals, anchors=anchors,
                delays=delays, tag_delay=tag_delay, layout_sigma=layout_sigma,
                clocks=clocks, reference_time_s=reference_time,
                predicted_root=predicted, velocity=tracker["velocity"], policy=policy,
                biases=None if policy == "raw_all" else biases,
            )
            result = solve_shared_root(
                links, anchors_m=anchors, initial_root_m=predicted,
                root_velocity_mps=tracker["velocity"],
            )
            main[policy].append({
                "success": result.success, "reason": result.reason,
                "root": result.root_position_m.tolist(),
                "residuals": result.residuals_m.tolist(),
            })
            if policy in cv:
                for held_node in NODE_TO_PROXY_POINT:
                    held = solve_shared_root(
                        [link for link in links if link.node != held_node],
                        anchors_m=anchors, initial_root_m=predicted,
                        root_velocity_mps=tracker["velocity"],
                    )
                    validation = [link for link in links if link.node == held_node]
                    held_residual = evaluate_shared_root_residuals(
                        validation, anchors_m=anchors,
                        root_position_m=held.root_position_m,
                        root_velocity_mps=tracker["velocity"],
                    ) if held.success else np.empty(0)
                    cv[policy].append({
                        "success": held.success, "reason": held.reason,
                        "held_node": held_node,
                        "anchors": [link.anchor for link in validation],
                        "residuals": held_residual.tolist(),
                    })
            if result.success:
                _update_tracker(tracker, result.root_position_m, reference_time, dt)
    main_summary = {policy: _summarize_main(rows) for policy, rows in main.items()}
    expected_cv = len(episode["groups"]) * len(NODE_TO_PROXY_POINT)
    cv_summary = {policy: _summarize_cv(rows, expected_cv) for policy, rows in cv.items()}
    return main_summary, cv_summary


def _gate(
    main: dict[str, Any],
    cv: dict[str, Any],
    *,
    candidate_policy: str,
    dynamic_motion_gate: bool,
) -> dict[str, Any]:
    baseline = main["raw_all"]
    candidate = main[candidate_policy]
    base_cv = cv["raw_all"]
    candidate_cv = cv[candidate_policy]
    checks = {
        "all_epochs_accepted": candidate["acceptance_fraction"] == 1.0,
        "root_step_p95_not_worse": (
            candidate["root_step_p95_m"] <= baseline["root_step_p95_m"]
        ),
        "held_node_median_not_worse": (
            candidate_cv["median_abs_held_range_residual_m"]
            <= base_cv["median_abs_held_range_residual_m"]
        ),
        "held_node_p95_not_worse": (
            candidate_cv["p95_abs_held_range_residual_m"]
            <= base_cv["p95_abs_held_range_residual_m"]
        ),
    }
    if not dynamic_motion_gate:
        checks["root_radial_mad_not_worse"] = (
            candidate["root_radial_mad_m"] <= baseline["root_radial_mad_m"]
        )
        checks["first_last_increase_at_most_0p02m"] = (
            candidate["root_first_to_last_m"]
            <= baseline["root_first_to_last_m"] + 0.02
        )
    return {"checks": checks, "pass": all(checks.values())}


def run(
    output: Path,
    clock_path: Path,
    *,
    bias_table_path: Path | None = None,
    blind_episode: str = BLIND_EPISODE,
    candidate_policy: str = "bias_soft_v2",
) -> dict[str, Any]:
    started = time.perf_counter()
    if blind_episode not in CALIBRATION_ORDER or blind_episode == CALIBRATION_EPISODE:
        raise ValueError("blind episode must be a non-00 frozen calibration action")
    if candidate_policy not in POLICIES or candidate_policy == "raw_all":
        raise ValueError("candidate policy is unavailable")
    output.mkdir(parents=True, exist_ok=False)
    clocks = _clock_models(clock_path)
    bridges = _beacon_boundary_bridges(clock_path)
    anchors, delays, tag_delay, layout_sigma = _load_layout()
    kinematics = load_frozen_c2_3a()
    alignment, frozen_forward = frozen_world_alignment(kinematics)
    room_initial = np.array([
        float(np.mean(anchors[:, 0])), float(np.mean(anchors[:, 1])), 0.95
    ])
    calibration = None
    if bias_table_path is None:
        calibration = _load_episode(CALIBRATION_EPISODE, clocks, bridges)
        biases, bias_audit = _fit_biases(
            calibration, kinematics=kinematics, alignment=alignment,
            anchors=anchors, delays=delays, tag_delay=tag_delay,
            layout_sigma=layout_sigma, clocks=clocks, room_initial=room_initial,
        )
        policies = POLICIES
        cv_policies = ("raw_all", candidate_policy)
    else:
        biases = load_pair_bias_table(
            bias_table_path, nodes=NODE_TO_PROXY_POINT
        )
        bias_document = json.loads(bias_table_path.read_text())
        bias_audit = dict(bias_document.get("audit", {}))
        policies = ("raw_all", candidate_policy)
        cv_policies = policies
    blind = _load_episode(blind_episode, clocks, bridges)
    main, cv = _blind_test(
        blind, biases=biases, kinematics=kinematics, alignment=alignment,
        anchors=anchors, delays=delays, tag_delay=tag_delay,
        layout_sigma=layout_sigma, clocks=clocks, room_initial=room_initial,
        policies=policies, cv_policies=cv_policies,
    )
    gate = _gate(
        main, cv, candidate_policy=candidate_policy,
        dynamic_motion_gate=blind_episode != BLIND_EPISODE,
    )
    separated = [
        separate_fixed_bias_from_initial_pose_nlos(
            estimate, layout_sigma_m=layout_sigma
        )
        for estimate in biases.values()
    ]
    table_document = {
        "schema": "biospur-c2-held-node-pair-bias-v1",
        "source_episode": CALIBRATION_EPISODE,
        "method": "single_median_of_residuals_from_other_nine_node_root",
        "estimates": [
            {
                "node": item.node, "anchor": item.anchor,
                "bias_m": item.bias_m, "robust_sigma_m": item.robust_sigma_m,
                "sample_count": item.sample_count,
            }
            for _, item in sorted(biases.items())
        ],
        "audit": bias_audit,
    }
    if bias_table_path is None:
        _write_json(output / "PAIR_BIAS_TABLE.json", table_document)
    else:
        _write_json(output / "PAIR_BIAS_REFERENCE.json", {
            "path": str(bias_table_path),
            "sha256": _sha256(bias_table_path),
            "source_episode": CALIBRATION_EPISODE,
        })
    result = {
        "schema": "biospur-c2-pair-bias-blind-gate-v1",
        "status": "PAIR_BIAS_MECHANISM_PASS" if gate["pass"] else "PAIR_BIAS_MECHANISM_REJECT",
        "scientific_pass": False,
        "calibration_episode": CALIBRATION_EPISODE,
        "blind_episode": blind_episode,
        "candidate_policy": candidate_policy,
        "bias_table_reused_without_refit": bias_table_path is not None,
        "clock_contract": "BEACON_LBD_GLOBAL_TDMA_PLUS_NODE_B306_TIMER2",
        "uwb_cadence_hz": 1000.0 / 120.0,
        "calibration": bias_audit,
        "blind_main": main,
        "blind_held_node_cv": cv,
        "preregistered_gate": gate,
        "soft_nlos_v2_contract": {
            "bias_owner": "frozen_00_held-node_pair_median",
            "bias_refit_on_blind_episode": False,
            "innovation": "symmetric_absolute_bias_corrected_previous-root_innovation",
            "facing_reliability": "0.5 + 0.25*cosine_outward_score",
            "innovation_reliability": "1/(1+(max(0,abs(innovation)-2sigma)/(2sigma))^2)",
            "minimum_combined_reliability": 0.05,
            "hard_link_deletion": False,
        },
        "bounded_bias_variance_contract": {
            "fixed_bias_limit_rule": "max(3*layout_pair_rms,0.20m)",
            "fixed_bias_limit_m": separated[0].fixed_bias_limit_m,
            "fixed_bias_pair_count": int(sum(
                not item.initial_pose_nlos_state for item in separated
            )),
            "initial_pose_nlos_pair_count": int(sum(
                item.initial_pose_nlos_state for item in separated
            )),
            "initial_pose_nlos_correction_m": 0.0,
            "initial_pose_nlos_sigma_rule": (
                "hypot(pair_robust_sigma,min(abs(initial_offset),1.0m))"
            ),
            "hard_link_deletion": False,
        },
        "proxy_boundary": (
            "FROZEN_3A_DISPLAY_PROXY_NOT_MEASURED_ANTENNA_PHASE_CENTRES"
        ),
        "alignment": {
            "target_forward_v4": [0.0, -1.0, 0.0],
            "frozen_forward": frozen_forward.tolist(),
            "determinant": float(np.linalg.det(alignment)),
        },
        "episode_inputs": {
            episode["episode"]: {
                "complete_ten_node_epochs": len(episode["groups"]),
                "partial_groups_discarded": episode["partial_groups"],
                "decoded_rows": len(episode["rows"]),
                "retained_rows": len(episode["retained"]),
                "decode_errors": episode["decode_errors"],
            }
            for episode in (calibration, blind) if episode is not None
        },
        "wall_s": time.perf_counter() - started,
    }
    _write_json(output / "RESULT.json", result)
    lines = [
        "# C2 held-node pair-bias blind gate", "",
        f"Status: **{result['status']}** (scientific pass remains false).", "",
        f"Biases were fitted once on 00 from roots that excluded the biased node, then frozen for {blind_episode}.",
        "", f"| {blind_episode} policy | root MAD m | p95 step m | first-last m |", "|---|---:|---:|---:|",
    ]
    for policy in policies:
        row = main[policy]
        lines.append(
            f"| {policy} | {row['root_radial_mad_m']:.6f} | "
            f"{row['root_step_p95_m']:.6f} | {row['root_first_to_last_m']:.6f} |"
        )
    lines.extend(["", "Preregistered checks:"])
    lines.extend(f"- {name}: {'PASS' if passed else 'FAIL'}" for name, passed in gate["checks"].items())
    (output / "REPORT.md").write_text("\n".join(lines) + "\n")
    manifest = {
        "tool": str(Path(__file__).resolve()),
        "tool_sha256": _sha256(Path(__file__).resolve()),
        "clock": str(clock_path), "clock_sha256": _sha256(clock_path),
        "layout": str(LAYOUT), "layout_sha256": _sha256(LAYOUT),
        "blind_raw": str(blind["raw"]),
        "blind_raw_sha256": _sha256(blind["raw"]),
        "pair_bias_source_sha256": _sha256(
            ROOT / "src/biospur_fusion/c2_uwb_calibration/pair_bias.py"
        ),
        "shared_root_source_sha256": _sha256(
            ROOT / "src/biospur_fusion/c2_uwb_calibration/shared_root.py"
        ),
    }
    if calibration is not None:
        manifest["calibration_raw"] = str(calibration["raw"])
        manifest["calibration_raw_sha256"] = _sha256(calibration["raw"])
    else:
        manifest["pair_bias_table"] = str(bias_table_path)
        manifest["pair_bias_table_sha256"] = _sha256(bias_table_path)
    _write_json(output / "EVIDENCE_MANIFEST.json", manifest)
    sealed = sorted(path for path in output.iterdir() if path.name != "SHA256SUMS")
    (output / "SHA256SUMS").write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in sealed)
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clock", type=Path, default=DEFAULT_CLOCK)
    parser.add_argument("--bias-table", type=Path)
    parser.add_argument("--blind-episode", choices=CALIBRATION_ORDER, default=BLIND_EPISODE)
    parser.add_argument(
        "--candidate-policy",
        choices=tuple(policy for policy in POLICIES if policy != "raw_all"),
        default="bias_soft_v2",
    )
    args = parser.parse_args()
    print(json.dumps(run(
        args.output.resolve(), args.clock.resolve(),
        bias_table_path=(args.bias_table.resolve() if args.bias_table else None),
        blind_episode=args.blind_episode,
        candidate_policy=args.candidate_policy,
    ), indent=2))


if __name__ == "__main__":
    main()
