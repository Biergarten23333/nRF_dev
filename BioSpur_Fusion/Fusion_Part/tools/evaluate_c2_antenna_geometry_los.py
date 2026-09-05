#!/usr/bin/env python3
"""Bounded diagnostic of C2 antenna-facing geometry against held-link errors.

This is deliberately a mechanism test, not a body-aware positioning result.
It asks whether links ranked by the operator-defined sensor ``-Z`` outward
direction have cleaner leave-one-anchor residuals than the unranked set.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any

import numpy as np
from scipy.stats import spearmanr

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_uwb_calibration.antenna_los import (
    horizontal_yaw_alignment,
    outward_facing_score,
    outward_normal_world,
    rotation_from_wxyz,
    select_best_geometry,
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
from biospur_fusion.uwb.frontend import CanonicalT4Frontend


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CLOCK = (
    ROOT
    / "logs/c2_uwb_beacon_clock_20260903_141552/"
    "CLOCK_TABLE_CALIBRATION_ONLY.json"
)
DEFAULT_HELD = ROOT / "logs/c2_uwb_calibration_held_link_20260903_180908"
TARGET_FORWARD_V4 = np.array([0.0, -1.0, 0.0])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def _frozen_world_alignment(kinematics: Any) -> tuple[np.ndarray, np.ndarray]:
    series = kinematics.series("00", "pelvis")
    forward = []
    for quaternion in series.quat_world_segment_wxyz[series.mask]:
        forward.append(rotation_from_wxyz(quaternion) @ np.array([1.0, 0.0, 0.0]))
    frozen_forward = np.median(np.asarray(forward), axis=0)
    alignment = horizontal_yaw_alignment(frozen_forward, TARGET_FORWARD_V4)
    return alignment, frozen_forward


def _held_residuals(
    held_root: Path,
    episode: str,
) -> dict[tuple[str, int, int, int], float]:
    output: dict[tuple[str, int, int, int], float] = {}
    for path in sorted((held_root / "HELD_LINK_CHECKPOINTS").glob("*_anchor_*.json")):
        document = json.loads(path.read_text())
        events = document.get("event_ids", {}).get(episode, [])
        residuals = document.get("episode_t4", {}).get(episode, [])
        if len(events) != len(residuals):
            raise ValueError(f"held event/residual mismatch: {path}")
        for event, residual in zip(events, residuals):
            event_episode, node, boot, sweep, anchor = event
            if event_episode != episode:
                raise ValueError(f"held event episode mismatch: {path}")
            key = (str(node), int(boot), int(sweep), int(anchor))
            if key in output:
                raise ValueError(f"duplicate held residual: {key}")
            output[key] = float(residual)
    return output


def _nearest_quaternion(
    kinematics: Any,
    episode_key: str,
    segment: str,
    fraction: float,
) -> np.ndarray:
    series = kinematics.series(episode_key, segment)
    indices = np.flatnonzero(series.mask)
    if not len(indices):
        raise ValueError(f"empty frozen orientation series: {episode_key}/{segment}")
    relative = min(1.0, max(0.0, float(fraction)))
    target = float(series.time_root_s[indices[0]]) + relative * float(
        series.time_root_s[indices[-1]] - series.time_root_s[indices[0]]
    )
    local = int(np.searchsorted(series.time_root_s[indices], target))
    local = min(local, len(indices) - 1)
    if local and abs(series.time_root_s[indices[local - 1]] - target) < abs(
        series.time_root_s[indices[local]] - target
    ):
        local -= 1
    return series.quat_world_segment_wxyz[indices[local]]


def _geometry_condition(position: np.ndarray, anchors: np.ndarray) -> tuple[int, float]:
    direction = anchors - position
    direction /= np.linalg.norm(direction, axis=1)[:, None]
    singular = np.linalg.svd(direction, compute_uv=False)
    tolerance = max(direction.shape) * np.finfo(float).eps * singular[0]
    rank = int(np.sum(singular > tolerance))
    information = direction.T @ direction
    condition = float(np.linalg.cond(information)) if rank == 3 else math.inf
    return rank, condition


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("no geometry/residual rows were produced")
    score = np.asarray([row["score"] for row in rows], dtype=float)
    signed = np.asarray([row["held_residual_m"] for row in rows], dtype=float)
    absolute = np.abs(signed)
    inward = score < 0.0
    outward = ~inward
    if not inward.any() or not outward.any():
        raise ValueError("pilot did not observe both PCB-front and PCB-back links")

    grouped: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["node"], row["boot"], row["sweep"])].append(row)

    selections = {}
    for count in (4, 5, 6, 7):
        selected_rows: list[dict[str, Any]] = []
        rank_pass = 0
        eligible_groups = 0
        for values in grouped.values():
            scores = {int(value["anchor"]): float(value["score"]) for value in values}
            selected = select_best_geometry(scores, scores, target_count=count)
            if not selected:
                continue
            eligible_groups += 1
            chosen = [value for value in values if int(value["anchor"]) in selected]
            selected_rows.extend(chosen)
            position = np.asarray(values[0]["tag_position_v4_m"], dtype=float)
            anchor_positions = np.asarray(
                [value["anchor_position_v4_m"] for value in chosen], dtype=float
            )
            rank, condition = _geometry_condition(position, anchor_positions)
            rank_pass += int(rank == 3 and condition <= 1e8)
        selected_abs = np.abs(
            np.asarray([row["held_residual_m"] for row in selected_rows], dtype=float)
        )
        selections[str(count)] = {
            "selected_link_samples": int(len(selected_rows)),
            "eligible_sweeps": int(eligible_groups),
            "rank3_condition_pass_fraction": (
                float(rank_pass / eligible_groups) if eligible_groups else 0.0
            ),
            "median_abs_held_residual_m": float(np.median(selected_abs)),
            "change_from_all_fraction": float(
                np.median(selected_abs) / np.median(absolute) - 1.0
            ),
        }
    correlation_signed = spearmanr(score, signed, nan_policy="raise")
    correlation_absolute = spearmanr(score, absolute, nan_policy="raise")
    return {
        "samples": int(len(rows)),
        "sweeps": int(len(grouped)),
        "all_median_abs_held_residual_m": float(np.median(absolute)),
        "pcb_back_halfspace": {
            "samples": int(inward.sum()),
            "median_signed_held_residual_m": float(np.median(signed[inward])),
            "median_abs_held_residual_m": float(np.median(absolute[inward])),
        },
        "antenna_outward_halfspace": {
            "samples": int(outward.sum()),
            "median_signed_held_residual_m": float(np.median(signed[outward])),
            "median_abs_held_residual_m": float(np.median(absolute[outward])),
        },
        "spearman_score_vs_signed_residual": {
            "rho": float(correlation_signed.statistic),
            "pvalue": float(correlation_signed.pvalue),
        },
        "spearman_score_vs_abs_residual": {
            "rho": float(correlation_absolute.statistic),
            "pvalue": float(correlation_absolute.pvalue),
        },
        "top_geometry_link_count": selections,
    }


def _position_stability(
    rows: list[dict[str, Any]],
    attempts: dict[str, int],
) -> dict[str, Any]:
    """Summarize only the known-still episode without claiming position truth."""

    still = [row for row in rows if row["episode"] == "00_initial_still"]
    if not still:
        raise ValueError("selected-link solve requires the initial-still episode")
    policies = sorted({str(row["policy"]) for row in still})
    by_policy_node: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in still:
        by_policy_node[(str(row["policy"]), str(row["node"]))].append(row)

    output = {}
    for policy in policies:
        node_jitter = []
        node_step = []
        for (candidate, _node), values in by_policy_node.items():
            if candidate != policy:
                continue
            values.sort(key=lambda row: int(row["sample_index"]))
            positions = np.asarray([row["position_v4_m"] for row in values], dtype=float)
            centre = np.median(positions, axis=0)
            node_jitter.append(float(np.median(np.linalg.norm(positions - centre, axis=1))))
            if len(positions) > 1:
                node_step.append(float(np.median(np.linalg.norm(np.diff(positions, axis=0), axis=1))))

        by_sample: dict[int, dict[str, np.ndarray]] = defaultdict(dict)
        for row in still:
            if row["policy"] == policy:
                by_sample[int(row["sample_index"])][str(row["node"])] = np.asarray(
                    row["position_v4_m"], dtype=float
                )
        pair_series: dict[tuple[str, str], list[float]] = defaultdict(list)
        for sample in by_sample.values():
            nodes = sorted(sample)
            for left_index, left in enumerate(nodes):
                for right in nodes[left_index + 1:]:
                    pair_series[(left, right)].append(
                        float(np.linalg.norm(sample[left] - sample[right]))
                    )
        pair_mad = []
        for values in pair_series.values():
            if len(values) < 10:
                continue
            array = np.asarray(values, dtype=float)
            pair_mad.append(float(1.4826 * np.median(np.abs(array - np.median(array)))))
        accepted = sum(row["policy"] == policy for row in still)
        attempted = int(attempts.get(policy, 0))
        output[policy] = {
            "accepted_rows": int(accepted),
            "attempted_rows": attempted,
            "accepted_fraction": float(accepted / attempted) if attempted else 0.0,
            "median_node_position_jitter_m": float(np.median(node_jitter)),
            "median_node_step_m": float(np.median(node_step)),
            "median_pair_distance_mad_m": float(np.median(pair_mad)),
            "qualified_pair_count": int(len(pair_mad)),
        }
    baseline = output.get("all")
    if baseline is None:
        raise ValueError("selected-link solve lacks an all-link baseline")
    for policy, values in output.items():
        values["jitter_change_from_all_fraction"] = float(
            values["median_node_position_jitter_m"]
            / baseline["median_node_position_jitter_m"]
            - 1.0
        )
        values["pair_mad_change_from_all_fraction"] = float(
            values["median_pair_distance_mad_m"]
            / baseline["median_pair_distance_mad_m"]
            - 1.0
        )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clock-table", type=Path, default=DEFAULT_CLOCK)
    parser.add_argument("--held-root", type=Path, default=DEFAULT_HELD)
    parser.add_argument(
        "--episodes",
        default="00_initial_still,02_t_pose",
        help="comma-separated canonical calibration episode names",
    )
    parser.add_argument("--stride", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--selected-solve", action="store_true")
    args = parser.parse_args()
    started = time.perf_counter()
    if args.stride < 1 or args.timeout <= 0:
        raise ValueError("stride and timeout must be positive")
    episodes = tuple(value.strip() for value in args.episodes.split(",") if value.strip())
    if not episodes or any(value not in CALIBRATION_ORDER for value in episodes):
        raise ValueError("unknown calibration episode")
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True)

    clock_table = args.clock_table.resolve()
    held_root = args.held_root.resolve()
    clocks = _clock_models(clock_table)
    bridges = _beacon_boundary_bridges(clock_table)
    kinematics = load_frozen_c2_3a()
    alignment, frozen_forward = _frozen_world_alignment(kinematics)
    frontend = CanonicalT4Frontend(LAYOUT)
    selected_frontends = (
        {count: CanonicalT4Frontend(LAYOUT) for count in (4, 5, 6, 7)}
        if args.selected_solve
        else {}
    )
    anchors = np.asarray(
        [
            [
                frontend.layout.anchors[index].x_mm,
                frontend.layout.anchors[index].y_mm,
                frontend.layout.anchors[index].z_mm,
            ]
            for index in range(8)
        ],
        dtype=float,
    ) / 1000.0

    output_rows = []
    position_rows = []
    position_attempts: dict[str, int] = defaultdict(int)
    episode_audit = {}
    for episode in episodes:
        if time.perf_counter() - started > args.timeout:
            raise TimeoutError("antenna geometry pilot exceeded its wall limit")
        index = CALIBRATION_ORDER.index(episode)
        frozen_episode = f"{index:02d}"
        physical = PHYSICAL_DIRECTORY[episode]
        path = DATASET / "actions" / physical / "rep_01/raw/fusion_host_raw.cobs.bin"
        decoded, summary = decode_uwb_only(path)
        lo, hi, events_path = _action_bounds_global_ns(physical, bridges)
        residuals = _held_residuals(held_root, episode)
        by_node_seen: dict[str, int] = defaultdict(int)
        retained = solved = 0
        for row in decoded:
            if row.node not in clocks:
                continue
            global_ns = int(round(
                clocks[row.node].a_ns_per_us * row.strobe_us + clocks[row.node].b_ns
            ))
            if not lo <= global_ns < hi:
                continue
            occurrence = by_node_seen[row.node]
            by_node_seen[row.node] += 1
            if occurrence % args.stride:
                continue
            retained += 1
            observation = frontend.solve(
                node_id=row.node,
                sweep=row.sweep,
                global_time_ns=global_ns,
                global_time_sigma_ns=int(round(clocks[row.node].sigma_ns)),
                anchor_ids=row.anchor_ids,
                ranges_mm=row.ranges_mm,
                quality=row.quality,
                valid_mask=row.valid_mask,
                t_round_us=row.t_round_us,
            )
            if observation is None or observation.acceptability != "ACCEPTED":
                continue
            solved += 1
            sample_index = occurrence // args.stride
            fraction = (global_ns - lo) / (hi - lo)
            segment = kinematics.node_to_segment[row.node]
            quaternion = _nearest_quaternion(
                kinematics, frozen_episode, segment, fraction
            )
            normal = outward_normal_world(row.node, quaternion, alignment)
            valid_anchors = [
                anchor
                for anchor in range(8)
                if row.valid_mask & (1 << anchor)
                and 0 < row.ranges_mm[anchor] < 0xFFFF
            ]
            geometry_scores = {
                anchor: outward_facing_score(
                    observation.xyz_m, anchors[anchor], normal
                )
                for anchor in valid_anchors
            }
            if args.selected_solve:
                position_attempts["all"] += 1
                position_rows.append({
                    "episode": episode,
                    "node": row.node,
                    "sweep": int(row.sweep),
                    "sample_index": int(sample_index),
                    "policy": "all",
                    "position_v4_m": observation.xyz_m.tolist(),
                })
                for count, selected_frontend in selected_frontends.items():
                    selected = select_best_geometry(
                        valid_anchors,
                        geometry_scores,
                        target_count=count,
                    )
                    if not selected:
                        continue
                    policy = f"top_{count}"
                    position_attempts[policy] += 1
                    selected_mask = sum(1 << anchor for anchor in selected)
                    selected_observation = selected_frontend.solve(
                        node_id=row.node,
                        sweep=row.sweep,
                        global_time_ns=global_ns,
                        global_time_sigma_ns=int(round(clocks[row.node].sigma_ns)),
                        anchor_ids=row.anchor_ids,
                        ranges_mm=row.ranges_mm,
                        quality=row.quality,
                        valid_mask=selected_mask,
                        t_round_us=row.t_round_us,
                    )
                    if (
                        selected_observation is None
                        or selected_observation.acceptability != "ACCEPTED"
                    ):
                        continue
                    position_rows.append({
                        "episode": episode,
                        "node": row.node,
                        "sweep": int(row.sweep),
                        "sample_index": int(sample_index),
                        "policy": policy,
                        "position_v4_m": selected_observation.xyz_m.tolist(),
                    })
            for anchor in observation.anchors_used:
                key = (row.node, row.boot, row.sweep, int(anchor))
                if key not in residuals:
                    continue
                score = outward_facing_score(
                    observation.xyz_m, anchors[anchor], normal
                )
                output_rows.append({
                    "episode": episode,
                    "node": row.node,
                    "boot": int(row.boot),
                    "sweep": int(row.sweep),
                    "anchor": int(anchor),
                    "score": score,
                    "held_residual_m": residuals[key],
                    "tag_position_v4_m": observation.xyz_m.tolist(),
                    "anchor_position_v4_m": anchors[anchor].tolist(),
                })
        episode_audit[episode] = {
            "raw_path": str(path),
            "raw_sha256": _sha256(path),
            "events_path": str(events_path),
            "events_sha256": _sha256(events_path),
            "decoded_uwb": int(summary.uwb_rows),
            "stride_retained_rows": int(retained),
            "all_link_t4_accepted_rows": int(solved),
            "held_rows_joined": int(sum(row["episode"] == episode for row in output_rows)),
        }

    summary = _summarize(output_rows)
    best_count, best = min(
        summary["top_geometry_link_count"].items(),
        key=lambda item: item[1]["median_abs_held_residual_m"],
    )
    mechanism_support = bool(
        best["change_from_all_fraction"] < 0.0
        and best["rank3_condition_pass_fraction"] >= 0.95
    )
    selected_stability = (
        _position_stability(position_rows, position_attempts)
        if args.selected_solve
        else None
    )
    selected_candidates = []
    if selected_stability is not None:
        selected_candidates = [
            policy
            for policy, values in selected_stability.items()
            if policy != "all"
            and values["accepted_fraction"] >= 0.99
            and values["jitter_change_from_all_fraction"] < 0.0
            and values["pair_mad_change_from_all_fraction"] < 0.0
        ]
    selected_solve_support = bool(selected_candidates)
    if args.selected_solve:
        status = (
            "SELECTED_LINK_SOLVE_STATIC_MECHANISM_SUPPORTED"
            if selected_solve_support
            else "SELECTED_LINK_SOLVE_STATIC_MECHANISM_NOT_SUPPORTED"
        )
    else:
        status = (
            "MECHANISM_SUPPORTED_FOR_SELECTED_LINK_SOLVE_PILOT"
            if mechanism_support
            else "MECHANISM_NOT_SUPPORTED_STOP_BEFORE_SELECTED_LINK_SOLVE"
        )
    result = {
        "schema": "biospur.c2.uwb.antenna_geometry_los_pilot.v1",
        "status": status,
        "scientific_pass": False,
        "mechanism_support": mechanism_support,
        "clock": {
            "source": str(clock_table),
            "sha256": _sha256(clock_table),
            "measurement_time": "B306_TIMER2",
            "beacon_epoch": True,
            "listener_lpd_lrd_consumed": False,
        },
        "geometry_contract": {
            "sensor_minus_z": "UWB_ANTENNA_OUTWARD",
            "sensor_plus_z": "PCB_BACK_BODY_FACING",
            "initial_body_forward_v4": TARGET_FORWARD_V4.tolist(),
            "initial_body_forward_semantics": "APPROXIMATELY_FACING_ABEF",
            "frozen_forward_before_yaw_alignment": frozen_forward.tolist(),
            "world_from_frozen_world": alignment.tolist(),
            "proper_rotation_determinant": float(np.linalg.det(alignment)),
            "score_is_soft_prior_not_los_truth": True,
            "body_volume_intersection_included": False,
            "antenna_phase_centre_geometry_included": False,
        },
        "scope": {
            "episodes": list(episodes),
            "stride": int(args.stride),
            "elapsed_wall_s": float(time.perf_counter() - started),
            "selected_link_solve_executed": bool(args.selected_solve),
        },
        "episode_audit": episode_audit,
        "diagnostic": summary,
        "selected_link_static_stability": selected_stability,
        "selected_link_supported_policies": selected_candidates,
        "selected_link_solve_support": selected_solve_support,
        "best_link_count": int(best_count),
        "interpretation_boundary": (
            "HELD_RESIDUALS_ARE_T4_LEAVE_ONE_ANCHOR_DIAGNOSTICS;"
            "ALL_LINK_T4_POSITION_IS_USED_ONLY_FOR_APPROXIMATE_ANCHOR_DIRECTION;"
            "NO_BODY_VOLUME_OR_PHASE_CENTRE_METROLOGY"
        ),
    }
    _write_json(args.output / "PILOT_RESULT.json", result)
    with (args.output / "LINK_ROWS.jsonl").open("w", encoding="utf-8") as stream:
        for row in output_rows:
            stream.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
    if args.selected_solve:
        with (args.output / "POSITION_ROWS.jsonl").open("w", encoding="utf-8") as stream:
            for row in position_rows:
                stream.write(
                    json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n"
                )
    files = (
        ("PILOT_RESULT.json", "LINK_ROWS.jsonl", "POSITION_ROWS.jsonl")
        if args.selected_solve
        else ("PILOT_RESULT.json", "LINK_ROWS.jsonl")
    )
    (args.output / "SHA256SUMS").write_text(
        "".join(f"{_sha256(args.output / name)}  {name}\n" for name in files)
    )
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
