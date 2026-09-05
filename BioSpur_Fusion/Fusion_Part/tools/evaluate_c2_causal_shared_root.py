#!/usr/bin/env python3
"""Bounded causal shared-root pilot on C2 ``00_initial_still``.

The pilot jointly solves one pelvis root from all body-worn UWB raw ranges and
frozen 3A relative tag proxies.  Link ranking may use only the preceding root,
the current frozen IMU/FK orientation, the known anchor layout, and measured
link availability.  It never uses a current-epoch per-tag UWB position.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any

import numpy as np

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_uwb_calibration.antenna_los import (
    outward_facing_score,
    select_best_geometry,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import (
    NODE_TO_PROXY_POINT,
    body_proxy_at_fraction,
    frozen_world_alignment,
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
    ROOT
    / "logs/c2_uwb_beacon_clock_20260903_141552/"
    "CLOCK_TABLE_CALIBRATION_ONLY.json"
)
DEFAULT_EPISODE = "00_initial_still"
EPOCH_NS = 120_000_000
POLICIES: tuple[tuple[str, int | None], ...] = (
    ("all", None),
    ("top4", 4),
    ("top5", 5),
    ("top6", 6),
    ("soft_nlos_v1", None),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def _load_layout(path: Path) -> tuple[np.ndarray, np.ndarray, float, dict[str, Any]]:
    document = json.loads(path.read_text())
    rows = sorted(document["anchors"], key=lambda row: int(row["id"]))
    if [int(row["id"]) for row in rows] != list(range(8)):
        raise ValueError("layout anchor identity is not exactly A-H / 0-7")
    anchors = np.asarray(
        [[row["x_mm"], row["y_mm"], row["z_mm"]] for row in rows], dtype=float
    ) / 1000.0
    delays = np.asarray([row["d_anchor_mm"] for row in rows], dtype=float) / 1000.0
    tag_delay = float(document.get("tag_delay_mm", 0.0)) / 1000.0
    return anchors, delays, tag_delay, document


def _global_ns(row: Any, clocks: dict[str, Any]) -> int:
    clock = clocks[row.node]
    return int(round(clock.a_ns_per_us * row.strobe_us + clock.b_ns))


def _valid_slots(row: Any) -> list[int]:
    return [
        slot
        for slot in range(8)
        if row.valid_mask & (1 << slot)
        and int(row.anchor_ids[slot]) == slot
        and 0 < int(row.ranges_mm[slot]) < 0xFFFF
        and math.isfinite(float(row.t_round_us[slot]))
    ]


def _radial_mad(values: np.ndarray) -> float:
    centre = np.median(values, axis=0)
    return float(1.4826 * np.median(np.linalg.norm(values - centre, axis=1)))


def _summarize(records: list[dict[str, Any]], expected_epochs: int) -> dict[str, Any]:
    accepted = [record for record in records if record["success"]]
    if not accepted:
        return {
            "accepted_epochs": 0,
            "expected_epochs": expected_epochs,
            "acceptance_fraction": 0.0,
            "failure_reasons": dict(Counter(record["reason"] for record in records)),
        }
    position = np.asarray([record["root_position_m"] for record in accepted], dtype=float)
    residual = np.concatenate(
        [np.asarray(record["residuals_m"], dtype=float) for record in accepted]
    )
    standardized = np.concatenate(
        [np.asarray(record["standardized_residuals"], dtype=float) for record in accepted]
    )
    step = np.linalg.norm(np.diff(position, axis=0), axis=1)
    displacement = np.linalg.norm(position - position[0], axis=1)
    coordinate_mad = 1.4826 * np.median(
        np.abs(position - np.median(position, axis=0)), axis=0
    )
    return {
        "accepted_epochs": len(accepted),
        "expected_epochs": expected_epochs,
        "acceptance_fraction": float(len(accepted) / expected_epochs),
        "failure_reasons": dict(Counter(record["reason"] for record in records)),
        "root_median_m": np.median(position, axis=0).tolist(),
        "root_coordinate_mad_m": coordinate_mad.tolist(),
        "root_radial_mad_m": _radial_mad(position),
        "root_first_to_last_m": float(np.linalg.norm(position[-1] - position[0])),
        "root_max_displacement_from_first_m": float(np.max(displacement)),
        "root_step_median_m": float(np.median(step)) if len(step) else 0.0,
        "root_step_p95_m": float(np.quantile(step, 0.95)) if len(step) else 0.0,
        "median_abs_range_residual_m": float(np.median(np.abs(residual))),
        "p95_abs_range_residual_m": float(np.quantile(np.abs(residual), 0.95)),
        "median_abs_standardized_residual": float(np.median(np.abs(standardized))),
        "condition_median": float(np.median([row["condition"] for row in accepted])),
        "condition_max": float(np.max([row["condition"] for row in accepted])),
        "nfev_median": float(np.median([row["nfev"] for row in accepted])),
        "selected_link_count_median": float(
            np.median([row["link_count"] for row in accepted])
        ),
        "anchor_selection_counts": {
            str(anchor): int(
                sum(record["anchors_selected"].count(anchor) for record in accepted)
            )
            for anchor in range(8)
        },
    }


def _summarize_held_node_cv(
    records: list[dict[str, Any]], expected_epochs: int
) -> dict[str, Any]:
    accepted = [record for record in records if record["success"]]
    residuals = np.concatenate(
        [np.asarray(record["residuals_m"], dtype=float) for record in accepted]
    ) if accepted else np.empty(0)
    by_node = {}
    for node in NODE_TO_PROXY_POINT:
        node_records = [record for record in accepted if record["held_node"] == node]
        node_residuals = np.concatenate(
            [np.asarray(record["residuals_m"], dtype=float) for record in node_records]
        ) if node_records else np.empty(0)
        by_node[node] = {
            "accepted_epochs": len(node_records),
            "median_abs_held_range_residual_m": (
                float(np.median(np.abs(node_residuals))) if len(node_residuals) else None
            ),
            "p95_abs_held_range_residual_m": (
                float(np.quantile(np.abs(node_residuals), 0.95)) if len(node_residuals) else None
            ),
        }
    return {
        "expected_solves": expected_epochs * len(NODE_TO_PROXY_POINT),
        "accepted_solves": len(accepted),
        "acceptance_fraction": float(
            len(accepted) / (expected_epochs * len(NODE_TO_PROXY_POINT))
        ),
        "failure_reasons": dict(Counter(record["reason"] for record in records)),
        "median_abs_held_range_residual_m": (
            float(np.median(np.abs(residuals))) if len(residuals) else None
        ),
        "p95_abs_held_range_residual_m": (
            float(np.quantile(np.abs(residuals), 0.95)) if len(residuals) else None
        ),
        "by_held_node": by_node,
    }


def run(output: Path, clock_path: Path, episode: str = DEFAULT_EPISODE) -> dict[str, Any]:
    started = time.perf_counter()
    if episode not in CALIBRATION_ORDER:
        raise ValueError(f"episode is outside the frozen 00-19 calibration set: {episode}")
    episode_key = f"{CALIBRATION_ORDER.index(episode):02d}"
    output.mkdir(parents=True, exist_ok=False)
    clocks = _clock_models(clock_path)
    bridges = _beacon_boundary_bridges(clock_path)
    anchors, anchor_delays, tag_delay, layout_document = _load_layout(LAYOUT)
    kinematics = load_frozen_c2_3a()
    alignment, frozen_forward = frozen_world_alignment(kinematics)

    raw_path = (
        DATASET / "actions" / PHYSICAL_DIRECTORY[episode]
        / "rep_01/raw/fusion_host_raw.cobs.bin"
    )
    rows, decode = decode_uwb_only(raw_path)
    lo, hi, events_path = _action_bounds_global_ns(PHYSICAL_DIRECTORY[episode], bridges)
    retained = [
        row for row in rows
        if row.node in clocks and lo <= _global_ns(row, clocks) < hi
    ]
    groups: dict[int, list[Any]] = defaultdict(list)
    for row in retained:
        groups[int(round(_global_ns(row, clocks) / EPOCH_NS))].append(row)
    complete_groups = [
        sorted(values, key=lambda row: row.node)
        for _, values in sorted(groups.items())
        if len(values) == len(NODE_TO_PROXY_POINT)
        and len({row.node for row in values}) == len(NODE_TO_PROXY_POINT)
    ]
    if len(complete_groups) < 10:
        raise RuntimeError("fewer than ten complete ten-node epochs")

    room_initial = np.array(
        [float(np.mean(anchors[:, 0])), float(np.mean(anchors[:, 1])), 0.95]
    )
    history = {
        name: {
            "root": room_initial.copy(),
            "velocity": np.zeros(3),
            "time_s": None,
            "records": [],
        }
        for name, _ in POLICIES
    }
    held_node_cv: dict[str, list[dict[str, Any]]] = {
        "all": [], "top4": [], "soft_nlos_v1": []
    }
    interval_ns = hi - lo
    layout_sigma_m = float(
        layout_document["stats"]["inter_anchor_pair_rms_mm"]
    ) / 1000.0

    for epoch_index, group in enumerate(complete_groups):
        base_ns = np.asarray([_global_ns(row, clocks) for row in group], dtype=np.int64)
        epoch_ns = int(np.median(base_ns))
        fraction = (epoch_ns - lo) / interval_ns
        offsets, normals, frozen_frame = body_proxy_at_fraction(
            kinematics, episode_key, fraction, alignment
        )
        link_times = []
        for row in group:
            clock = clocks[row.node]
            for slot in _valid_slots(row):
                link_times.append(clock.seconds(row.strobe_us + 0.5 * row.t_round_us[slot]))
        reference_time_s = float(np.median(link_times))

        for policy, target_count in POLICIES:
            state = history[policy]
            previous_time = state["time_s"]
            dt = 0.0 if previous_time is None else max(0.0, reference_time_s - previous_time)
            predicted_root = state["root"] + dt * state["velocity"]
            links: list[SharedRangeLink] = []
            candidate_links: list[SharedRangeLink] = []
            selected_flat: list[int] = []
            for row in group:
                slots = _valid_slots(row)
                score = {
                    slot: outward_facing_score(
                        predicted_root + offsets[row.node],
                        anchors[slot],
                        normals[row.node],
                    )
                    for slot in slots
                }
                if target_count is None:
                    selected = tuple(slots)
                else:
                    selected = select_best_geometry(
                        slots, score, target_count=target_count
                    )
                clock = clocks[row.node]
                for slot in slots:
                    link_time = clock.seconds(
                        row.strobe_us + 0.5 * row.t_round_us[slot]
                    )
                    quality = max(1.0, float(row.quality[slot]))
                    sigma = math.sqrt(
                        (layout_sigma_m * math.sqrt(100.0 / quality)) ** 2
                        + 0.10 ** 2
                    )
                    corrected_range = (
                        float(row.ranges_mm[slot]) / 1000.0
                        - anchor_delays[slot]
                        - tag_delay
                    )
                    if policy == "soft_nlos_v1":
                        predicted_tag = (
                            predicted_root
                            + offsets[row.node]
                            + (link_time - reference_time_s) * state["velocity"]
                        )
                        predicted_range = float(
                            np.linalg.norm(anchors[slot] - predicted_tag)
                        )
                        positive_excess = max(
                            0.0, corrected_range - predicted_range - 2.0 * sigma
                        )
                        facing_reliability = 0.5 + 0.25 * score[slot]
                        innovation_reliability = 1.0 / (
                            1.0 + (positive_excess / (2.0 * sigma)) ** 2
                        )
                        reliability = max(
                            0.05, facing_reliability * innovation_reliability
                        )
                        sigma /= math.sqrt(reliability)
                    candidate = SharedRangeLink(
                        node=row.node,
                        anchor=slot,
                        range_m=corrected_range,
                        tag_offset_world_m=offsets[row.node],
                        link_dt_s=link_time - reference_time_s,
                        sigma_m=sigma,
                        facing_score=score[slot],
                    )
                    candidate_links.append(candidate)
                    if slot in selected:
                        links.append(candidate)
                        selected_flat.append(slot)
            result = solve_shared_root(
                links,
                anchors_m=anchors,
                initial_root_m=predicted_root,
                root_velocity_mps=state["velocity"],
            )
            record = {
                "epoch_index": epoch_index,
                "global_time_ns": epoch_ns,
                "fraction": float(fraction),
                "frozen_frame": frozen_frame,
                "success": result.success,
                "reason": result.reason,
                "root_position_m": result.root_position_m.tolist(),
                "residuals_m": result.residuals_m.tolist(),
                "standardized_residuals": result.standardized_residuals.tolist(),
                "condition": result.condition,
                "rank": result.rank,
                "nfev": result.nfev,
                "link_count": len(links),
                "anchors_selected": selected_flat,
            }
            state["records"].append(record)
            if policy in held_node_cv:
                for held_node in NODE_TO_PROXY_POINT:
                    training = [link for link in links if link.node != held_node]
                    validation = [
                        link for link in candidate_links if link.node == held_node
                    ]
                    held_result = solve_shared_root(
                        training,
                        anchors_m=anchors,
                        initial_root_m=predicted_root,
                        root_velocity_mps=state["velocity"],
                    )
                    held_residuals = (
                        evaluate_shared_root_residuals(
                            validation,
                            anchors_m=anchors,
                            root_position_m=held_result.root_position_m,
                            root_velocity_mps=state["velocity"],
                        )
                        if held_result.success else np.empty(0)
                    )
                    held_node_cv[policy].append(
                        {
                            "epoch_index": epoch_index,
                            "held_node": held_node,
                            "success": held_result.success,
                            "reason": held_result.reason,
                            "residuals_m": held_residuals.tolist(),
                        }
                    )
            if result.success:
                if previous_time is not None and dt > 0.0:
                    measured_velocity = (result.root_position_m - state["root"]) / dt
                    speed = float(np.linalg.norm(measured_velocity))
                    if speed > 3.0:
                        measured_velocity *= 3.0 / speed
                    state["velocity"] = 0.75 * state["velocity"] + 0.25 * measured_velocity
                state["root"] = result.root_position_m.copy()
                state["time_s"] = reference_time_s

    summaries = {
        policy: _summarize(state["records"], len(complete_groups))
        for policy, state in history.items()
    }
    held_node_summaries = {
        policy: _summarize_held_node_cv(records, len(complete_groups))
        for policy, records in held_node_cv.items()
    }
    all_cv_residual = held_node_summaries["all"]["median_abs_held_range_residual_m"]
    for policy in ("top4", "soft_nlos_v1"):
        policy_cv_residual = held_node_summaries[policy][
            "median_abs_held_range_residual_m"
        ]
        if all_cv_residual is not None and policy_cv_residual is not None:
            held_node_summaries[policy][
                "median_abs_residual_change_from_all_fraction"
            ] = float(policy_cv_residual / all_cv_residual - 1.0)
    baseline_jitter = summaries["all"].get("root_radial_mad_m")
    baseline_residual = summaries["all"].get("median_abs_range_residual_m")
    for policy, summary in summaries.items():
        if policy == "all" or baseline_jitter is None or baseline_residual is None:
            continue
        summary["root_radial_mad_change_from_all_fraction"] = float(
            summary["root_radial_mad_m"] / baseline_jitter - 1.0
        )
        summary["median_abs_residual_change_from_all_fraction"] = float(
            summary["median_abs_range_residual_m"] / baseline_residual - 1.0
        )

    trajectories_path = output / "ROOT_TRAJECTORIES.csv"
    with trajectories_path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            ["policy", "epoch_index", "global_time_ns", "success", "x_m", "y_m", "z_m",
             "links", "rank", "condition", "nfev"]
        )
        for policy, state in history.items():
            for record in state["records"]:
                writer.writerow(
                    [policy, record["epoch_index"], record["global_time_ns"],
                     int(record["success"]), *record["root_position_m"],
                     record["link_count"], record["rank"], record["condition"],
                     record["nfev"]]
                )

    result = {
        "schema": "biospur-c2-causal-shared-root-pilot-v2",
        "status": "DIAGNOSTIC_MECHANISM_PILOT",
        "scientific_pass": False,
        "episode": episode,
        "frozen_episode_key": episode_key,
        "complete_ten_node_epochs": len(complete_groups),
        "discarded_partial_epoch_count": len(groups) - len(complete_groups),
        "decoded_uwb_rows": len(rows),
        "retained_formal_uwb_rows": len(retained),
        "decode_errors": decode.decode_errors,
        "clock_contract": "BEACON_LBD_GLOBAL_TDMA_PLUS_NODE_B306_TIMER2",
        "uwb_cadence_hz": 1000.0 / 120.0,
        "causal_contract": {
            "current_epoch_per_tag_t4_position_consumed": False,
            "current_epoch_ranges_consumed_for_hard_link_ranking": False,
            "current_epoch_ranges_consumed_for_soft_nlos_v1_weight": True,
            "ranking_inputs": [
                "previous_policy_root_and_velocity",
                "current_frozen_imu_fk_proxy",
                "known_v4_anchor_layout",
                "current_link_availability",
            ],
            "root_solve_inputs": "selected_corrected_raw_ranges_jointly_across_ten_nodes",
            "bootstrap": "fixed_room_centre_xy_and_0.95_m_pelvis_height",
        },
        "proxy_boundary": {
            "source": "frozen_C2_3A_zero_pose_change_display_proxy",
            "node_point_map": NODE_TO_PROXY_POINT,
            "not_claimed": [
                "measured_antenna_phase_centres",
                "qualified_anatomical_joint_centres",
                "completed_biomechanical_model",
            ],
        },
        "range_model": {
            "anchor_delay_correction_applied": True,
            "tag_delay_correction_applied": True,
            "sigma_rule": "sqrt((layout_pair_rms*sqrt(100/quality))^2 + 0.10m^2)",
            "loss": "Huber_f_scale_1.5_standardized",
            "per_link_epoch": "B306_TIMER2(strobe_us + t_round_us/2)",
            "soft_nlos_v1": {
                "hard_link_deletion": False,
                "facing_reliability": "0.5 + 0.25*cosine_outward_score",
                "positive_innovation_excess": "max(0, measured-predicted-2*sigma)",
                "innovation_reliability": "1/(1+(positive_excess/(2*sigma))^2)",
                "combined_reliability_floor": 0.05,
                "effective_sigma": "base_sigma/sqrt(combined_reliability)",
                "prediction_owner": "preceding_policy_root_velocity_plus_current_frozen_fk",
            },
        },
        "alignment": {
            "operator_initial_heading": "approximately_faces_ABEF",
            "target_forward_v4": [0.0, -1.0, 0.0],
            "frozen_forward_before_alignment": frozen_forward.tolist(),
            "proper_rotation_determinant": float(np.linalg.det(alignment)),
        },
        "policies": summaries,
        "held_one_body_node_out_cross_validation": {
            "contract": (
                "solve_root_from_other_nine_nodes_then_evaluate_all_available_raw_ranges_"
                "of_the_unseen_tenth_node"
            ),
            "policies": held_node_summaries,
        },
        "wall_s": time.perf_counter() - started,
        "boundary": (
            "DIAGNOSTIC_ONLY_UNTIL_BODY_TAG_PHASE_CENTRES_AND_RANGE_NOISE_OWNERSHIP_QUALIFY"
        ),
    }
    _write_json(output / "RESULT.json", result)
    report_lines = [
        "# C2 causal shared-root pilot",
        "",
        f"- Episode: `{episode}`; complete ten-node epochs: {len(complete_groups)}.",
        "- The ranker used no current-epoch T4/tag position and no current range value.",
        "- Ten frozen relative tag proxies were held to one root and selected raw ranges jointly solved that root.",
        "- This is diagnostic: the 3A points are display proxies, not measured antenna phase centres.",
        "",
        "| Policy | accepted | radial root MAD (m) | median |range residual| (m) | p95 step (m) |",
        "|---|---:|---:|---:|---:|",
    ]
    for policy, _ in POLICIES:
        row = summaries[policy]
        report_lines.append(
            f"| {policy} | {row['accepted_epochs']}/{len(complete_groups)} | "
            f"{row.get('root_radial_mad_m', math.nan):.6f} | "
            f"{row.get('median_abs_range_residual_m', math.nan):.6f} | "
            f"{row.get('root_step_p95_m', math.nan):.6f} |"
        )
    (output / "REPORT.md").write_text("\n".join(report_lines) + "\n")
    manifest = {
        "tool": str(Path(__file__).resolve()),
        "tool_sha256": _sha256(Path(__file__).resolve()),
        "raw": str(raw_path),
        "raw_sha256": _sha256(raw_path),
        "events": str(events_path),
        "events_sha256": _sha256(events_path),
        "clock": str(clock_path),
        "clock_sha256": _sha256(clock_path),
        "layout": str(LAYOUT),
        "layout_sha256": _sha256(LAYOUT),
        "shared_root_source_sha256": _sha256(
            ROOT / "src/biospur_fusion/c2_uwb_calibration/shared_root.py"
        ),
        "body_proxy_source_sha256": _sha256(
            ROOT / "src/biospur_fusion/c2_uwb_calibration/frozen_body_proxy.py"
        ),
    }
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
    parser.add_argument(
        "--episode", choices=CALIBRATION_ORDER, default=DEFAULT_EPISODE
    )
    args = parser.parse_args()
    result = run(args.output.resolve(), args.clock.resolve(), args.episode)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
