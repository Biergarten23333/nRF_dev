"""Traceable A/B rendering; 3A geometry is display-only in this module."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .adapter import BODY_BY_SEGMENT, COUPLED_COORDINATES, configure_opensim_log


LINKS = (
    ("pelvis_center", "shoulder_mid"),
    ("shoulder_mid", "shoulder_left"),
    ("shoulder_mid", "shoulder_right"),
    ("pelvis_center", "hip_left"),
    ("pelvis_center", "hip_right"),
    ("shoulder_left", "elbow_left"),
    ("elbow_left", "wrist_left"),
    ("shoulder_right", "elbow_right"),
    ("elbow_right", "wrist_right"),
    ("hip_left", "knee_left"),
    ("knee_left", "ankle_left"),
    ("hip_right", "knee_right"),
    ("knee_right", "ankle_right"),
)
VIEWS = ((0, 2, "front"), (1, 2, "side"), (0, 1, "top"))


def _rotation_matrix(rotation) -> np.ndarray:
    return np.array(
        [[rotation.get(row, column) for column in range(3)] for row in range(3)],
        dtype=float,
    )


def official_body_orientations(configured_model: Path, motion_sto: Path):
    """Replay official output coordinates only to query official body frames."""

    import opensim as osim

    model = osim.Model(str(configured_model.resolve()))
    state = model.initSystem()
    table = osim.TimeSeriesTable(str(motion_sto.resolve()))
    labels = list(table.getColumnLabels())
    values = {
        label: table.getDependentColumn(label).to_numpy() for label in labels
    }
    times = np.asarray(table.getIndependentColumn(), dtype=float)
    coordinates = model.updCoordinateSet()
    coordinate_by_name = {
        coordinates.get(index).getName(): coordinates.get(index)
        for index in range(coordinates.getSize())
    }
    rows: list[dict[str, np.ndarray]] = []
    for row_index, time_s in enumerate(times):
        state.setTime(float(time_s))
        for label in labels:
            coordinate = coordinate_by_name[label]
            if coordinate.getLocked(state):
                continue
            value = float(values[label][row_index])
            if int(coordinate.getMotionType()) == 1 or label in COUPLED_COORDINATES:
                value = float(np.deg2rad(value))
            coordinate.setValue(state, value, False)
        model.realizePosition(state)
        rows.append(
            {
                segment: _rotation_matrix(
                    model.getBodySet().get(body).getRotationInGround(state)
                )
                for segment, body in BODY_BY_SEGMENT.items()
            }
        )
    return times, rows


def display_proxy_points(frozen, matrices: dict[str, np.ndarray]):
    """Apply the frozen proxy only after IK, solely for comparable pixels."""

    geometry = frozen.geometry
    length = geometry.segment_length_m
    root = np.zeros(3)
    pelvis = matrices["pelvis"]
    torso = matrices["torso"]
    shoulder_mid = root + torso @ np.array([0.0, 0.0, geometry.torso_height_m])
    shoulder_left = shoulder_mid + torso @ np.array(
        [-0.5 * geometry.shoulder_span_m, 0.0, 0.0]
    )
    shoulder_right = shoulder_mid + torso @ np.array(
        [0.5 * geometry.shoulder_span_m, 0.0, 0.0]
    )
    hip_left = root + pelvis @ np.array([-0.5 * geometry.hip_span_m, 0.0, 0.0])
    hip_right = root + pelvis @ np.array([0.5 * geometry.hip_span_m, 0.0, 0.0])

    points = {
        "pelvis_center": root,
        "shoulder_mid": shoulder_mid,
        "shoulder_left": shoulder_left,
        "shoulder_right": shoulder_right,
        "hip_left": hip_left,
        "hip_right": hip_right,
    }
    for side in ("left", "right"):
        elbow = points[f"shoulder_{side}"] + matrices[f"upper_arm_{side}"] @ np.array(
            [0.0, 0.0, -length[f"upper_arm_{side}"]]
        )
        points[f"elbow_{side}"] = elbow
        points[f"wrist_{side}"] = elbow + matrices[f"forearm_{side}"] @ np.array(
            [0.0, 0.0, -length[f"forearm_{side}"]]
        )
        knee = points[f"hip_{side}"] + matrices[f"thigh_{side}"] @ np.array(
            [0.0, 0.0, -length[f"thigh_{side}"]]
        )
        points[f"knee_{side}"] = knee
        points[f"ankle_{side}"] = knee + matrices[f"shank_{side}"] @ np.array(
            [0.0, 0.0, -length[f"shank_{side}"]]
        )
    output = frozen.output_matrix_world_display_from_internal
    return {name: output @ point for name, point in points.items()}


def _plot_pose(axis, points, horizontal, vertical, *, color, label, linestyle):
    first = True
    for start, stop in LINKS:
        values = np.stack([points[start], points[stop]])
        axis.plot(
            values[:, horizontal],
            values[:, vertical],
            color=color,
            linestyle=linestyle,
            linewidth=1.7,
            label=label if first else None,
        )
        first = False
    values = np.stack(list(points.values()))
    axis.scatter(values[:, horizontal], values[:, vertical], color=color, s=8)


def render_episode(frozen, episode_key: str, capture_label: str, b_rows, output_path: Path):
    frame_count = frozen.episodes[episode_key].frame_count
    frame_indices = [0, frame_count // 4, frame_count // 2, 3 * frame_count // 4, frame_count - 1]
    figure, axes = plt.subplots(
        len(frame_indices), 3, figsize=(11.5, 15.5), constrained_layout=True
    )
    for row_index, frame in enumerate(frame_indices):
        points_a = frozen.forward_kinematics(episode_key, frame, coordinates="display")
        points_b = display_proxy_points(frozen, b_rows[frame])
        all_points = np.concatenate(
            [np.stack(list(points_a.values())), np.stack(list(points_b.values()))]
        )
        for column_index, (horizontal, vertical, view_name) in enumerate(VIEWS):
            axis = axes[row_index, column_index]
            _plot_pose(
                axis,
                points_a,
                horizontal,
                vertical,
                color="#4c4c4c",
                label="A frozen direct FK",
                linestyle="--",
            )
            _plot_pose(
                axis,
                points_b,
                horizontal,
                vertical,
                color="#d62728",
                label="B official OpenSense IK",
                linestyle="-",
            )
            projected = all_points[:, [horizontal, vertical]]
            minimum = np.min(projected, axis=0)
            maximum = np.max(projected, axis=0)
            center = 0.5 * (minimum + maximum)
            radius = max(float(np.max(maximum - minimum)) * 0.56, 0.2)
            axis.set_xlim(center[0] - radius, center[0] + radius)
            axis.set_ylim(center[1] - radius, center[1] + radius)
            axis.set_aspect("equal", adjustable="box")
            axis.set_axis_off()
            axis.set_title(f"frame {frame} | {view_name}")
            if row_index == 0 and column_index == 0:
                axis.legend(loc="upper left", fontsize=7)
    figure.suptitle(
        f"{capture_label}: A unchanged vs B official OpenSense\n"
        "Common 3A geometry is display-only; frames fixed at 0/25/50/75/100%",
        fontsize=12,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=180)
    plt.close(figure)
    return frame_indices


def render_error_plot(error_sto: Path, output_path: Path, capture_label: str):
    import opensim as osim

    table = osim.TimeSeriesTable(str(error_sto.resolve()))
    times = np.asarray(table.getIndependentColumn(), dtype=float)
    labels = list(table.getColumnLabels())
    figure, axis = plt.subplots(figsize=(11, 5.5), constrained_layout=True)
    for label in labels:
        axis.plot(
            times,
            np.degrees(table.getDependentColumn(label).to_numpy()),
            label=label,
            linewidth=1.0,
        )
    axis.set_title(f"{capture_label}: official OpenSim orientation errors")
    axis.set_xlabel("elapsed time (s)")
    axis.set_ylabel("official orientation error (degrees)")
    axis.grid(alpha=0.25)
    axis.legend(ncol=2, fontsize=7)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=180)
    plt.close(figure)


def render_pilot(frozen, pilot_root: Path) -> dict[str, object]:
    render_root = pilot_root / "rendering"
    configure_opensim_log(render_root / "opensim.log")
    model_path = pilot_root / "model/c2_official_configured.osim"
    episodes = {"00_initial_still": "00", "02_t_pose": "01"}
    result: dict[str, object] = {
        "geometry_scope": "ZERO_POSE_CHANGE_DISPLAY_PROXY_REGRESSION_ONLY",
        "model_geometry_used_for_render": False,
        "deterministic_frame_rule": "0, floor(N/4), floor(N/2), floor(3N/4), N-1",
        "episodes": {},
    }
    for capture_label, episode_key in episodes.items():
        episode_root = pilot_root / capture_label
        motion_path = episode_root / "official_output/official_ik.sto"
        times, b_rows = official_body_orientations(model_path, motion_path)
        montage_path = render_root / f"{capture_label}_ab_front_side_top.png"
        frame_indices = render_episode(
            frozen, episode_key, capture_label, b_rows, montage_path
        )
        error_plot_path = render_root / f"{capture_label}_official_orientation_errors.png"
        render_error_plot(
            episode_root / "official_output/official_ik.sto_orientationErrors.sto",
            error_plot_path,
            capture_label,
        )
        result["episodes"][capture_label] = {
            "frozen_episode": episode_key,
            "motion_rows": len(b_rows),
            "first_time_s": float(times[0]),
            "last_time_s": float(times[-1]),
            "frame_indices": frame_indices,
            "ab_montage": str(montage_path.resolve()),
            "orientation_error_plot": str(error_plot_path.resolve()),
        }
    (render_root / "RENDER_MANIFEST.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result
