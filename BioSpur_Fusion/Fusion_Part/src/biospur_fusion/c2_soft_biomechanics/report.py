"""Metrics and same-proxy A/B/C rendering for the bounded pilot."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from biospur_fusion.c2_fk_ik_diagnostic_biomechanics.pipeline import (
    _quat_conjugate,
    _quat_geodesic,
    _quat_multiply,
    generalized_relative_quaternions,
)
from biospur_fusion.c2_fk_to_opensim_ik.adapter import SEGMENTS, _quat_wxyz_matrix, _table_columns, sha256_file
from biospur_fusion.c2_fk_to_opensim_ik.render import LINKS, VIEWS, a_points, b_points, replay_model

from .pipeline import DISTAL_JOINTS, _body_xyz


def _summary(values: list[float] | np.ndarray, suffix: str) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    return {
        f"mean{suffix}": float(np.mean(array)),
        f"median{suffix}": float(np.median(array)),
        f"p95{suffix}": float(np.quantile(array, 0.95)),
        f"max{suffix}": float(np.max(array)),
    }


def _wrap_pi(values: np.ndarray) -> np.ndarray:
    return np.arctan2(np.sin(values), np.cos(values))


def _rotation_error(a: np.ndarray, b: np.ndarray) -> float:
    relative = a.T @ b
    cosine = float(np.clip((np.trace(relative) - 1.0) * 0.5, -1.0, 1.0))
    sine = 0.5 * float(
        np.linalg.norm(
            [
                relative[2, 1] - relative[1, 2],
                relative[0, 2] - relative[2, 0],
                relative[1, 0] - relative[0, 1],
            ]
        )
    )
    return math.atan2(sine, cosine)


def _plot(ax, points, horizontal, vertical, color, label, linestyle, linewidth):
    first = True
    for start, end in LINKS:
        values = np.stack((points[start], points[end]))
        ax.plot(
            values[:, horizontal],
            values[:, vertical],
            color=color,
            linestyle=linestyle,
            linewidth=linewidth,
            label=label if first else None,
        )
        first = False
    values = np.stack(list(points.values()))
    ax.scatter(values[:, horizontal], values[:, vertical], color=color, s=7)


def render_abc(frozen, episode, label: str, b_rows, c_rows, output: Path) -> dict[str, object]:
    frames = [0, episode.frame_count // 4, episode.frame_count // 2, 3 * episode.frame_count // 4, episode.frame_count - 1]
    rows = []
    for frame in frames:
        rows.append(
            (
                a_points(frozen, episode, frame),
                b_points(frozen, b_rows[frame]),
                b_points(frozen, c_rows[frame]),
            )
        )
    pooled = np.concatenate([np.stack(list(points.values())) for triple in rows for points in triple])
    center = 0.5 * (pooled.min(axis=0) + pooled.max(axis=0))
    radius = max(float(np.max(pooled.max(axis=0) - pooled.min(axis=0))) * 0.56, 0.25)
    fig, axes = plt.subplots(5, 3, figsize=(10.5, 15), constrained_layout=True)
    for row_index, (frame, triple) in enumerate(zip(frames, rows)):
        for column_index, (horizontal, vertical, view) in enumerate(VIEWS):
            ax = axes[row_index, column_index]
            _plot(ax, triple[0], horizontal, vertical, "#444444", "A frozen FK", "--", 2.0)
            _plot(ax, triple[1], horizontal, vertical, "#1f77b4", "B exact official IK", ":", 1.6)
            _plot(ax, triple[2], horizontal, vertical, "#d62728", "C soft official IK", "-", 1.2)
            ax.set_xlim(center[horizontal] - radius, center[horizontal] + radius)
            ax.set_ylim(center[vertical] - radius, center[vertical] + radius)
            ax.set_aspect("equal", adjustable="box")
            ax.set_axis_off()
            ax.set_title(f"frame {frame} | {view}")
            if row_index == 0 and column_index == 0:
                ax.legend(fontsize=7, loc="upper left")
    fig.suptitle(
        f"{label}: A/B/C on identical frozen 3A display-proxy geometry\n"
        "proxy endpoints are not anatomical joint centres; common scale"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=180)
    plt.close(fig)
    return {
        "path": str(output.resolve()),
        "sha256": sha256_file(output),
        "frames": frames,
        "common_radius_m": radius,
    }


def _temporal_steps(matrices: list[np.ndarray]) -> np.ndarray:
    return np.asarray([_rotation_error(a, b) for a, b in zip(matrices[:-1], matrices[1:])])


def _physical_gates(frozen, episode, c_rows) -> dict[str, object]:
    paired_points = ("shoulder", "hip", "knee", "ankle", "wrist")
    limb_segments = (
        ("shoulder_left", "elbow_left"),
        ("elbow_left", "wrist_left"),
        ("shoulder_right", "elbow_right"),
        ("elbow_right", "wrist_right"),
        ("hip_left", "knee_left"),
        ("knee_left", "ankle_left"),
        ("hip_right", "knee_right"),
        ("knee_right", "ankle_right"),
    )
    minimum_side_dot = math.inf
    minimum_limb_direction_dot = math.inf
    maximum_length_error = 0.0
    maximum_det_error = 0.0
    finite = True
    for frame, row in enumerate(c_rows):
        a = a_points(frozen, episode, frame)
        c = b_points(frozen, row)
        for prefix in paired_points:
            av = a[f"{prefix}_right"] - a[f"{prefix}_left"]
            cv = c[f"{prefix}_right"] - c[f"{prefix}_left"]
            minimum_side_dot = min(minimum_side_dot, float(av @ cv))
        for start, end in limb_segments:
            av = a[end] - a[start]
            cv = c[end] - c[start]
            minimum_limb_direction_dot = min(minimum_limb_direction_dot, float(av @ cv))
            maximum_length_error = max(maximum_length_error, abs(float(np.linalg.norm(av) - np.linalg.norm(cv))))
        for segment in SEGMENTS:
            maximum_det_error = max(maximum_det_error, abs(float(np.linalg.det(row[segment]["R"])) - 1.0))
        finite = bool(finite and all(np.all(np.isfinite(value)) for value in c.values()))
    numeric = math.sqrt(np.finfo(float).eps)
    gates = {
        "all_finite": finite,
        "proper_rotation_max_abs_det_minus_one": maximum_det_error,
        "fixed_proxy_segment_length_max_error_m": maximum_length_error,
        "minimum_a_to_c_left_right_separation_dot_m2": minimum_side_dot,
        "minimum_a_to_c_limb_direction_dot_m2": minimum_limb_direction_dot,
        "connectedness": "guaranteed by unchanged OpenSim Joint topology; independently checked by fixed segment lengths",
        "mirror_or_crossing_detected": minimum_side_dot <= 0.0,
        "fold_or_front_back_inversion_detected": minimum_limb_direction_dot <= 0.0,
        "collapse_or_disconnection_detected": maximum_length_error > numeric,
    }
    gates["passed"] = bool(
        finite
        and maximum_det_error <= numeric
        and maximum_length_error <= numeric
        and minimum_side_dot > 0.0
        and minimum_limb_direction_dot > 0.0
    )
    return gates


def _segment_distance_3d(a0: np.ndarray, a1: np.ndarray, b0: np.ndarray, b1: np.ndarray) -> float:
    """Shortest distance between two closed 3-D line segments."""

    u = a1 - a0
    v = b1 - b0
    w = a0 - b0
    aa = float(u @ u)
    bb = float(u @ v)
    cc = float(v @ v)
    dd = float(u @ w)
    ee = float(v @ w)
    denominator = aa * cc - bb * bb
    small = 64.0 * np.finfo(float).eps
    if denominator < small:
        s_numerator, s_denominator = 0.0, 1.0
        t_numerator, t_denominator = ee, cc
    else:
        s_numerator = bb * ee - cc * dd
        t_numerator = aa * ee - bb * dd
        s_denominator = t_denominator = denominator
        if s_numerator < 0.0:
            s_numerator, t_numerator, t_denominator = 0.0, ee, cc
        elif s_numerator > s_denominator:
            s_numerator, t_numerator, t_denominator = s_denominator, ee + bb, cc
    if t_numerator < 0.0:
        t_numerator = 0.0
        if -dd < 0.0:
            s_numerator = 0.0
        elif -dd > aa:
            s_numerator = s_denominator
        else:
            s_numerator, s_denominator = -dd, aa
    elif t_numerator > t_denominator:
        t_numerator = t_denominator
        if -dd + bb < 0.0:
            s_numerator = 0.0
        elif -dd + bb > aa:
            s_numerator = s_denominator
        else:
            s_numerator, s_denominator = -dd + bb, aa
    sc = 0.0 if abs(s_numerator) < small else s_numerator / s_denominator
    tc = 0.0 if abs(t_numerator) < small else t_numerator / t_denominator
    return float(np.linalg.norm(w + sc * u - tc * v))


def knee_topology_audit(frozen, episode, c_rows) -> dict[str, object]:
    """Report true 3-D bilateral shank clearance on fixed equal row blocks."""

    a_distances = []
    c_distances = []
    knee_separation = []
    ankle_separation = []
    for frame, row in enumerate(c_rows):
        a = a_points(frozen, episode, frame)
        c = b_points(frozen, row)
        a_distances.append(
            _segment_distance_3d(
                a["knee_left"], a["ankle_left"], a["knee_right"], a["ankle_right"]
            )
        )
        c_distances.append(
            _segment_distance_3d(
                c["knee_left"], c["ankle_left"], c["knee_right"], c["ankle_right"]
            )
        )
        knee_separation.append(float(np.linalg.norm(c["knee_right"] - c["knee_left"])))
        ankle_separation.append(float(np.linalg.norm(c["ankle_right"] - c["ankle_left"])))
    blocks = []
    for block, indices in enumerate(np.array_split(np.arange(episode.frame_count), 5)):
        values = np.asarray(c_distances)[indices]
        local = int(np.argmin(values))
        blocks.append(
            {
                "block": block,
                "row_start_inclusive": int(indices[0]),
                "row_stop_exclusive": int(indices[-1] + 1),
                "minimum_contralateral_shank_distance_m": float(values[local]),
                "minimum_row": int(indices[local]),
            }
        )
    minimum = float(np.min(c_distances))
    return {
        "geometry": "frozen 3A display proxy; not anatomical joint centres",
        "segmentation": "five fixed contiguous equal-count blocks; no action semantics",
        "a_minimum_contralateral_shank_distance_m": float(np.min(a_distances)),
        "c_minimum_contralateral_shank_distance_m": minimum,
        "c_minimum_row": int(np.argmin(c_distances)),
        "c_minimum_knee_separation_m": float(np.min(knee_separation)),
        "c_minimum_ankle_separation_m": float(np.min(ankle_separation)),
        "true_3d_shank_intersection_detected": bool(
            minimum <= 64.0 * np.finfo(float).eps
        ),
        "blocks": blocks,
        "caveat": "Projected line overlap in a montage is not a 3-D segment intersection.",
    }


def _candidate_chart_from_frozen(episode, ownership: Mapping[str, object], joint: str) -> np.ndarray:
    edge_map = {
        "elbow_left": ("upper_arm_left", "forearm_left"),
        "elbow_right": ("upper_arm_right", "forearm_right"),
        "knee_left": ("thigh_left", "shank_left"),
        "knee_right": ("thigh_right", "shank_right"),
    }
    parent, child = edge_map[joint]
    relative = generalized_relative_quaternions(episode, parent, child)
    matrices = np.asarray([_quat_wxyz_matrix(row) for row in relative])
    proper = np.asarray(ownership["joints"][joint]["proper_chart_rotation_parent_and_child"])
    return np.asarray([_body_xyz(proper.T @ row @ proper) for row in matrices])


def analyze_episode(
    frozen,
    episode,
    label: str,
    ownership: Mapping[str, object],
    baseline_model: Path,
    baseline_motion: Path,
    candidate_model: Path,
    profile_dirs: Mapping[str, Path],
    output_dir: Path,
    *,
    render: bool = True,
) -> dict[str, object]:
    _, b_rows = replay_model(baseline_model, baseline_motion)
    profile_rows = {}
    profiles = {}
    for profile, directory in profile_dirs.items():
        _, rows = replay_model(candidate_model, directory / "soft_ik.sto")
        profile_rows[profile] = rows
        record = json.loads((directory / "EPISODE_RESULT.json").read_text(encoding="utf-8"))
        profiles[profile] = {
            "native_orientation_errors_rad": record["orientation_errors"],
            "solver_wall_s": record["wall_s"],
            "motion_sha256": record["motion_sha256"],
            "errors_sha256": record["errors_sha256"],
        }
    c_rows = profile_rows["central"]
    segment_errors: dict[str, list[float]] = {segment: [] for segment in SEGMENTS}
    b_c_errors: dict[str, list[float]] = {segment: [] for segment in SEGMENTS}
    point_errors = []
    non_target_errors = []
    a_steps = []
    c_steps = []
    for segment in SEGMENTS:
        a_matrices = [
            _quat_wxyz_matrix(row)
            for row in episode.segments[segment].quat_world_segment_wxyz
        ]
        c_matrices = [row[segment]["R"] for row in c_rows]
        b_matrices = [row[segment]["R"] for row in b_rows]
        segment_errors[segment] = [_rotation_error(a, c) for a, c in zip(a_matrices, c_matrices)]
        b_c_errors[segment] = [_rotation_error(b, c) for b, c in zip(b_matrices, c_matrices)]
        a_steps.extend(_temporal_steps(a_matrices))
        c_steps.extend(_temporal_steps(c_matrices))
        if segment in ("pelvis", "torso", "upper_arm_left", "upper_arm_right", "thigh_left", "thigh_right"):
            non_target_errors.extend(segment_errors[segment])
    for frame, row in enumerate(c_rows):
        a = a_points(frozen, episode, frame)
        c = b_points(frozen, row)
        point_errors.extend(float(np.linalg.norm(a[name] - c[name])) for name in a)

    soft = {}
    pooled_before = []
    pooled_after = []
    _, central_columns_deg = _table_columns(profile_dirs["central"] / "soft_ik.sto")
    for joint in DISTAL_JOINTS:
        record = ownership["joints"][joint]
        if not record["eligible"]:
            continue
        before = _candidate_chart_from_frozen(episode, ownership, joint)
        row = {}
        for index, suffix in ((1, "ry"), (2, "rz")):
            target = float(record["coordinates"][suffix]["target_circular_mean_rad"])
            after = np.deg2rad(central_columns_deg[f"{joint}_{suffix}"])
            before_residual = _wrap_pi(before[:, index] - target)
            after_residual = _wrap_pi(after - target)
            pooled_before.extend(before_residual)
            pooled_after.extend(after_residual)
            row[suffix] = {
                "target_rad": target,
                "before": _summary(np.abs(before_residual), "_rad"),
                "after": _summary(np.abs(after_residual), "_rad"),
            }
        soft[joint] = row
    before_rms = float(np.sqrt(np.mean(np.square(pooled_before))))
    after_rms = float(np.sqrt(np.mean(np.square(pooled_after))))
    orientation_scale = float(ownership["orientation_roughness_block_rms_rad"]["p50"])
    temporal_a_max = float(np.max(a_steps))
    temporal_c_max = float(np.max(c_steps))
    montage = (
        render_abc(
            frozen,
            episode,
            label,
            b_rows,
            c_rows,
            output_dir / f"{label}_a_b_c_front_side_top.png",
        )
        if render
        else None
    )
    physical = _physical_gates(frozen, episode, c_rows)
    result = {
        "schema": "c2-soft-biomechanics-pilot-episode-analysis-v1",
        "label": label,
        "frozen_source_key": episode.key,
        "rows": episode.frame_count,
        "profiles": profiles,
        "central_segment_a_to_c_orientation_error_rad": {
            segment: _summary(values, "_rad") for segment, values in segment_errors.items()
        },
        "central_b_to_c_orientation_error_rad": {
            segment: _summary(values, "_rad") for segment, values in b_c_errors.items()
        },
        "central_same_proxy_point_change_m": _summary(point_errors, "_m"),
        "central_non_target_orientation_change_rad": _summary(non_target_errors, "_rad"),
        "soft_coordinate_dispersion": {
            "per_joint": soft,
            "pooled_before_rms_rad": before_rms,
            "pooled_after_rms_rad": after_rms,
            "strictly_reduced": after_rms < before_rms,
        },
        "temporal_so3_step": {
            "a_max_rad": temporal_a_max,
            "c_max_rad": temporal_c_max,
            "increase_rad": temporal_c_max - temporal_a_max,
        },
        "registered_central_orientation_roughness_rad": orientation_scale,
        "numeric_non_regression": {
            "native_orientation_p95_within_registered_scale": profiles["central"]["native_orientation_errors_rad"]["overall_p95_rad"] <= orientation_scale,
            "non_target_max_within_registered_scale": float(np.max(non_target_errors)) <= orientation_scale,
            "temporal_step_increase_within_registered_scale": temporal_c_max - temporal_a_max <= orientation_scale,
        },
        "physical_gates": physical,
        "knee_topology_3d": (
            knee_topology_audit(frozen, episode, c_rows)
            if label in ("10_knee_left", "11_knee_right", "primary_10", "primary_11")
            else None
        ),
        "montage": montage,
    }
    result["numeric_pass"] = bool(
        physical["passed"]
        and result["soft_coordinate_dispersion"]["strictly_reduced"]
        and all(result["numeric_non_regression"].values())
        and all(profile["native_orientation_errors_rad"]["all_finite"] for profile in profiles.values())
    )
    return result
