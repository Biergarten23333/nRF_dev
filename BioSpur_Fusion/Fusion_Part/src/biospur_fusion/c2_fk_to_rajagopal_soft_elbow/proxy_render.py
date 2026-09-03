"""Attributable A/B rendering through the same frozen 3A display geometry.

The official Rajagopal model remains the IK/coordinate owner.  This module
uses only its solved IMU-frame rotations, transforms them back to C2 internal
coordinates, and propagates them through the exact frozen 3A display-proxy
topology.  It never treats the proxy points as anatomical joint centres.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_3b_official_opensense.adapter import COUPLED_COORDINATES
from biospur_fusion.c2_fk_to_opensim_ik.adapter import (
    C2_FROM_OPENSIM,
    _quat_wxyz_matrix,
)
from biospur_fusion.c2_fk_to_scaled_opensense.pipeline import configure_opensim_log
from biospur_fusion.c2_fk_to_scaled_opensense.render import LINKS, VIEWS

from .candidate import CANDIDATE_BODY_BY_SEGMENT, CANDIDATE_FRAME_BY_SEGMENT, _matrix


PROTOCOL_BINDING = {
    "02": {"frozen_source_key": "01", "display_label": "02_t_pose"},
    "06": {"frozen_source_key": "06", "display_label": "06_elbow_left"},
    "07": {"frozen_source_key": "07", "display_label": "07_elbow_right"},
}
BRANCHES = {
    "B0_no_prior": {"directory": "no_prior", "weight": 0.0, "color": "#1f77b4"},
    "B001_weak_prior": {
        "directory": "weak_prior",
        "weight": 0.001,
        "color": "#d62728",
    },
}


def _proxy_points_internal(rotations: dict[str, np.ndarray], geometry):
    root = np.zeros(3)
    torso = rotations["torso"]
    pelvis = rotations["pelvis"]
    shoulder_mid = torso @ np.array([0.0, 0.0, geometry.torso_height_m])
    points = {"pelvis_center": root, "shoulder_mid": shoulder_mid}
    points["shoulder_left"] = shoulder_mid + torso @ np.array(
        [-0.5 * geometry.shoulder_span_m, 0.0, 0.0]
    )
    points["shoulder_right"] = shoulder_mid + torso @ np.array(
        [0.5 * geometry.shoulder_span_m, 0.0, 0.0]
    )
    points["hip_left"] = pelvis @ np.array(
        [-0.5 * geometry.hip_span_m, 0.0, 0.0]
    )
    points["hip_right"] = pelvis @ np.array(
        [0.5 * geometry.hip_span_m, 0.0, 0.0]
    )
    for side in ("left", "right"):
        points[f"elbow_{side}"] = points[f"shoulder_{side}"] + rotations[
            f"upper_arm_{side}"
        ] @ np.array([0.0, 0.0, -geometry.segment_length_m[f"upper_arm_{side}"]])
        points[f"wrist_{side}"] = points[f"elbow_{side}"] + rotations[
            f"forearm_{side}"
        ] @ np.array([0.0, 0.0, -geometry.segment_length_m[f"forearm_{side}"]])
        points[f"knee_{side}"] = points[f"hip_{side}"] + rotations[
            f"thigh_{side}"
        ] @ np.array([0.0, 0.0, -geometry.segment_length_m[f"thigh_{side}"]])
        points[f"ankle_{side}"] = points[f"knee_{side}"] + rotations[
            f"shank_{side}"
        ] @ np.array([0.0, 0.0, -geometry.segment_length_m[f"shank_{side}"]])
    return points


def _pose(ax, points, x, y, color, style, label):
    first = True
    for start, stop in LINKS:
        pair = np.stack([points[start], points[stop]])
        ax.plot(
            pair[:, x],
            pair[:, y],
            color=color,
            linestyle=style,
            linewidth=1.45,
            label=label if first else None,
        )
        first = False
    array = np.stack(list(points.values()))
    ax.scatter(array[:, x], array[:, y], color=color, s=6)


def _replay_segment_rotations(model_path: Path, motion_path: Path, log_path: Path):
    import opensim as osim

    configure_opensim_log(log_path)
    model = osim.Model(str(model_path.resolve()))
    state = model.initSystem()
    table = osim.TimeSeriesTable(str(motion_path.resolve()))
    times = np.asarray(table.getIndependentColumn(), dtype=float)
    labels = list(table.getColumnLabels())
    values = {
        label: np.asarray(table.getDependentColumn(label).to_numpy(), dtype=float)
        for label in labels
    }
    coordinates = model.updCoordinateSet()
    by_name = {
        coordinates.get(index).getName(): coordinates.get(index)
        for index in range(coordinates.getSize())
    }
    rows = []
    for row, time_s in enumerate(times):
        state.setTime(float(time_s))
        for name in labels:
            coordinate = by_name[name]
            if coordinate.getLocked(state):
                continue
            value = (
                math.radians(float(values[name][row]))
                if int(coordinate.getMotionType()) == 1 or name in COUPLED_COORDINATES
                else float(values[name][row])
            )
            coordinate.setValue(state, value, False)
        model.realizePosition(state)
        rows.append(
            {
                segment: C2_FROM_OPENSIM
                @ _matrix(
                    model.getComponent(
                        f"/bodyset/{CANDIDATE_BODY_BY_SEGMENT[segment]}/"
                        f"{CANDIDATE_FRAME_BY_SEGMENT[segment]}"
                    ).getRotationInGround(state)
                )
                for segment in CANDIDATE_BODY_BY_SEGMENT
            }
        )
    return times, rows


def _rotation_distance(first: np.ndarray, second: np.ndarray) -> float:
    cosine = np.clip((np.trace(first.T @ second) - 1.0) / 2.0, -1.0, 1.0)
    return float(np.arccos(cosine))


def _summary(values):
    array = np.asarray(values, dtype=float)
    return {
        "mean": float(np.mean(array)),
        "p95": float(np.quantile(array, 0.95)),
        "max": float(np.max(array)),
    }


def analyze_and_render_dual_proxy(
    workspace: Path,
    model_path: Path,
    output_root: Path,
    requested_protocol_label: str,
) -> dict[str, object]:
    if requested_protocol_label not in PROTOCOL_BINDING:
        raise ValueError("unsupported protocol label")
    binding = PROTOCOL_BINDING[requested_protocol_label]
    frozen = load_frozen_c2_3a(workspace=workspace)
    episode = frozen.episodes[binding["frozen_source_key"]]
    if episode.key != binding["frozen_source_key"]:
        raise RuntimeError("frozen episode identity mismatch")

    branch_rotations = {}
    episode_results = {}
    for branch, policy in BRANCHES.items():
        directory = output_root / "branches" / policy["directory"]
        episode_dir = directory / "episodes" / binding["display_label"]
        episode_result = json.loads((episode_dir / "EPISODE_RESULT.json").read_text())
        if episode_result["episode"] != binding["frozen_source_key"]:
            raise RuntimeError("episode result source-key mismatch")
        if float(episode_result["out_of_plane_weight"]) != policy["weight"]:
            raise RuntimeError("episode result branch-weight mismatch")
        times, rotations = _replay_segment_rotations(
            model_path,
            episode_dir / "official_ik.sto",
            episode_dir / "proxy_replay_opensim.log",
        )
        if len(times) != episode.frame_count:
            raise RuntimeError("official motion row count differs from frozen episode")
        branch_rotations[branch] = rotations
        episode_results[branch] = episode_result

    frames = [0, episode.frame_count // 4, episode.frame_count // 2,
              3 * episode.frame_count // 4, episode.frame_count - 1]
    figure, axes = plt.subplots(5, 3, figsize=(11.5, 15.5), constrained_layout=True)
    output_matrix = frozen.output_matrix_world_display_from_internal
    point_changes = {branch: [] for branch in BRANCHES}
    orientation_changes = {
        branch: {segment: [] for segment in CANDIDATE_BODY_BY_SEGMENT}
        for branch in BRANCHES
    }
    all_a = []
    all_b = {branch: [] for branch in BRANCHES}
    for row in range(episode.frame_count):
        a = dict(frozen.forward_kinematics(episode.key, row, coordinates="display"))
        all_a.append(a)
        for branch in BRANCHES:
            internal = _proxy_points_internal(branch_rotations[branch][row], frozen.geometry)
            b = {name: output_matrix @ point for name, point in internal.items()}
            all_b[branch].append(b)
            point_changes[branch].extend(
                float(np.linalg.norm(a[name] - b[name])) for name in a
            )
            for segment in CANDIDATE_BODY_BY_SEGMENT:
                source = episode.segments[segment].quat_world_segment_wxyz[row]
                source_matrix = _quat_wxyz_matrix(source)
                orientation_changes[branch][segment].append(
                    _rotation_distance(source_matrix, branch_rotations[branch][row][segment])
                )

    for plot_row, frame in enumerate(frames):
        a = all_a[frame]
        plotted = [np.stack(list(a.values()))]
        plotted.extend(np.stack(list(all_b[branch][frame].values())) for branch in BRANCHES)
        common = np.concatenate(plotted)
        for column, (x_axis, y_axis, view_name) in enumerate(VIEWS):
            ax = axes[plot_row, column]
            _pose(ax, a, x_axis, y_axis, "#444444", "--", "A frozen display FK")
            for branch, policy in BRANCHES.items():
                _pose(
                    ax,
                    all_b[branch][frame],
                    x_axis,
                    y_axis,
                    policy["color"],
                    "-",
                    branch,
                )
            extent = common[:, [x_axis, y_axis]]
            low, high = extent.min(0), extent.max(0)
            center = 0.5 * (low + high)
            radius = max(float((high - low).max()) * 0.58, 0.25)
            ax.set_xlim(center[0] - radius, center[0] + radius)
            ax.set_ylim(center[1] - radius, center[1] + radius)
            ax.set_aspect("equal")
            ax.set_axis_off()
            ax.set_title(f"frame {frame} | {view_name}")
            if plot_row == 0 and column == 0:
                ax.legend(fontsize=7)
    figure.suptitle(
        f"{binding['display_label']}: A / B0 / B.001 through identical frozen 3A display geometry\n"
        "display-proxy points only; official Rajagopal coordinates remain the IK owner"
    )
    png = output_root / "attributable_rendering" / (
        f"{binding['display_label']}_a_b0_b001_front_side_top.png"
    )
    png.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(png, dpi=170)
    plt.close(figure)

    result = {
        "schema": "biospur.c2.rajagopal_soft_elbow.dual_proxy_analysis.v1",
        "requested_protocol_label": requested_protocol_label,
        "frozen_source_key": binding["frozen_source_key"],
        "display_label": binding["display_label"],
        "episode_identity_asserted": True,
        "geometry_owner": "identical frozen 3A ZERO_POSE_CHANGE_DISPLAY_PROXY for A, B0, and B.001; not anatomical joint centres",
        "ik_owner": "official Rajagopal/OpenSim model coordinates and InverseKinematicsSolver",
        "frames": frames,
        "png": str(png.resolve()),
        "branches": {
            branch: {
                "out_of_plane_weight": BRANCHES[branch]["weight"],
                "official_orientation_errors": episode_results[branch]["orientation_errors"],
                "display_proxy_point_change_m": _summary(point_changes[branch]),
                "segment_orientation_change_rad": {
                    segment: _summary(values)
                    for segment, values in orientation_changes[branch].items()
                },
            }
            for branch in BRANCHES
        },
        "generic_joint_centre_rendering_is_non_acceptance_diagnostic": True,
    }
    json_path = output_root / f"ATTRIBUTABLE_ANALYSIS_{requested_protocol_label}.json"
    json_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result
