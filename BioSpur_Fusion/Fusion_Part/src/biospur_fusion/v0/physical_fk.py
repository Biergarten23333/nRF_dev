"""Direct fixed-profile forward kinematics for the V0 physical graph."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.spatial.transform import Rotation

from .math3d import rz
from .physical_graph import PhysicalGraphSpec, decode_state
from .raw6_heading import EDGES, ROM_LIMIT_DEG, SEGMENTS, EdgeFactors, Raw6Episode


JOINT_NAMES = (
    "pelvis_center",
    "pelvis_torso",
    "shoulder_left",
    "elbow_left",
    "wrist_left",
    "shoulder_right",
    "elbow_right",
    "wrist_right",
    "hip_left",
    "knee_left",
    "ankle_left",
    "hip_right",
    "knee_right",
    "ankle_right",
)
DISPLAY_BONES = (
    ("pelvis_center", "pelvis_torso", "pelvis_height_half"),
    ("pelvis_torso", "shoulder_left", "torso_left_diagonal"),
    ("pelvis_torso", "shoulder_right", "torso_right_diagonal"),
    ("shoulder_left", "shoulder_right", "shoulder_width"),
    ("shoulder_left", "elbow_left", "upper_arm_left"),
    ("elbow_left", "wrist_left", "forearm_left"),
    ("shoulder_right", "elbow_right", "upper_arm_right"),
    ("elbow_right", "wrist_right", "forearm_right"),
    ("pelvis_center", "hip_left", "pelvis_left_diagonal"),
    ("pelvis_center", "hip_right", "pelvis_right_diagonal"),
    ("hip_left", "hip_right", "pelvis_width"),
    ("hip_left", "knee_left", "thigh_left"),
    ("knee_left", "ankle_left", "shank_left"),
    ("hip_right", "knee_right", "thigh_right"),
    ("knee_right", "ankle_right", "shank_right"),
)


def representative_actions(capture: str) -> tuple[str, ...]:
    if capture == "CAPTURE1":
        return (
            "initial_still", "t_pose", "left_elbow", "right_elbow",
            "left_knee", "right_knee", "squats", "trunk",
        )
    return (
        "00_initial_still", "02_t_pose", "06_elbow_left", "07_elbow_right",
        "10_knee_left_seated", "11_knee_right_seated", "16_squat",
        "14_trunk_flex_extend", "15_trunk_axial_rotation",
    )


def _segment_frame(axis: np.ndarray, transverse: np.ndarray | None) -> np.ndarray:
    z = np.asarray(axis, dtype=float)
    z /= np.linalg.norm(z)
    if transverse is None:
        transverse = np.array([1.0, 0.0, 0.0])
        if abs(float(transverse @ z)) > 0.85:
            transverse = np.array([0.0, 1.0, 0.0])
    x = np.asarray(transverse, dtype=float) - z * float(np.asarray(transverse) @ z)
    if np.linalg.norm(x) < 1e-8:
        x = np.cross(z, np.array([0.0, 0.0, 1.0]))
        if np.linalg.norm(x) < 1e-8:
            x = np.cross(z, np.array([0.0, 1.0, 0.0]))
    x /= np.linalg.norm(x)
    y = np.cross(z, x); y /= np.linalg.norm(y)
    x = np.cross(y, z); x /= np.linalg.norm(x)
    return np.column_stack((x, y, z))


def anatomical_frames(
    state: np.ndarray,
    spec: PhysicalGraphSpec,
    factors: Mapping[str, EdgeFactors],
) -> dict[str, np.ndarray]:
    decoded = decode_state(state, spec)
    frames = {
        "pelvis": decoded["segment_geometry"]["pelvis"]["frame"],
        "torso": decoded["segment_geometry"]["torso"]["frame"],
    }
    hinge_child = {
        "forearm_left": factors["elbow_left"].hinge_axis_child,
        "forearm_right": factors["elbow_right"].hinge_axis_child,
        "shank_left": factors["knee_left"].hinge_axis_child,
        "shank_right": factors["knee_right"].hinge_axis_child,
    }
    hinge_parent = {
        "upper_arm_left": factors["elbow_left"].hinge_axis_parent,
        "upper_arm_right": factors["elbow_right"].hinge_axis_parent,
        "thigh_left": factors["knee_left"].hinge_axis_parent,
        "thigh_right": factors["knee_right"].hinge_axis_parent,
    }
    for segment in SEGMENTS[2:]:
        row = decoded["segment_geometry"][segment]
        frames[segment] = _segment_frame(
            row.get("axis", row.get("axis_proxy")),
            hinge_child.get(segment, hinge_parent.get(segment)),
        )
    return frames


def direct_fk_episode(
    episode: Raw6Episode,
    state: np.ndarray,
    spec: PhysicalGraphSpec,
    factors: Mapping[str, EdgeFactors],
    *,
    stride: int = 5,
    distal_endpoint_override: Mapping[str, np.ndarray] | None = None,
) -> dict[str, Any]:
    decoded = decode_state(state, spec)
    keep = np.arange(0, len(episode.time_ns), stride)
    count = len(keep)
    index = {segment: value for value, segment in enumerate(SEGMENTS)}
    corrected = np.empty((count, len(SEGMENTS), 3, 3))
    for segment in SEGMENTS:
        corrected[:, index[segment]] = np.einsum(
            "ij,njk->nik",
            rz(decoded["headings"][segment]),
            episode.rotation_world_sensor[segment][keep],
        )
    origin = np.zeros((count, len(SEGMENTS), 3), dtype=float)
    connection = {}
    closure_error = {}
    for edge, parent, child, _ in EDGES:
        p = index[parent]; c = index[child]
        parent_lever, child_lever = decoded["edge_levers"][edge]
        joint_parent = origin[:, p] + np.einsum(
            "nij,j->ni", corrected[:, p], parent_lever,
        )
        origin[:, c] = joint_parent - np.einsum(
            "nij,j->ni", corrected[:, c], child_lever,
        )
        joint_child = origin[:, c] + np.einsum(
            "nij,j->ni", corrected[:, c], child_lever,
        )
        connection[edge] = joint_parent
        closure_error[edge] = np.linalg.norm(joint_parent - joint_child, axis=1)

    geometry = decoded["segment_geometry"]
    distal_endpoint_override = dict(distal_endpoint_override or {})
    distal_local = {
        segment: np.asarray(
            distal_endpoint_override.get(
                segment, geometry[segment]["distal_proxy"],
            ),
            dtype=float,
        )
        for segment in ("forearm_left", "forearm_right", "shank_left", "shank_right")
    }
    joints = {
        "pelvis_center": origin[:, index["pelvis"]] + np.einsum(
            "nij,j->ni", corrected[:, index["pelvis"]], geometry["pelvis"]["center"],
        ),
        **connection,
        "wrist_left": origin[:, index["forearm_left"]] + np.einsum(
            "nij,j->ni", corrected[:, index["forearm_left"]], distal_local["forearm_left"],
        ),
        "wrist_right": origin[:, index["forearm_right"]] + np.einsum(
            "nij,j->ni", corrected[:, index["forearm_right"]], distal_local["forearm_right"],
        ),
        "ankle_left": origin[:, index["shank_left"]] + np.einsum(
            "nij,j->ni", corrected[:, index["shank_left"]], distal_local["shank_left"],
        ),
        "ankle_right": origin[:, index["shank_right"]] + np.einsum(
            "nij,j->ni", corrected[:, index["shank_right"]], distal_local["shank_right"],
        ),
    }
    joint_array = np.stack([joints[name] for name in JOINT_NAMES], axis=1)

    frames = anatomical_frames(state, spec, factors)
    anatomical = np.empty_like(corrected)
    for segment in SEGMENTS:
        anatomical[:, index[segment]] = np.einsum(
            "nij,jk->nik", corrected[:, index[segment]], frames[segment],
        )
    excursions = {}
    for edge, parent, child, _ in EDGES:
        relative = np.einsum(
            "nji,njk->nik", anatomical[:, index[parent]], anatomical[:, index[child]],
        )
        neutral = relative[0]
        change = np.einsum("ji,njk->nik", neutral, relative)
        excursions[edge] = np.degrees(Rotation.from_matrix(change).magnitude())

    return {
        "action": episode.action,
        "time_ns": episode.time_ns[keep],
        "phase": episode.phase[keep],
        "joint_position": joint_array,
        "sensor_origin": origin,
        "corrected_sensor_rotation": corrected,
        "anatomical_rotation": anatomical,
        "closure_error": closure_error,
        "joint_excursion_deg": excursions,
        "distal_endpoint_sensor_local_m": distal_local,
    }


def _line_distance(
    a0: np.ndarray, a1: np.ndarray, b0: np.ndarray, b1: np.ndarray,
) -> np.ndarray:
    """Vectorized closest distance between finite 3-D line segments."""

    u = a1 - a0; v = b1 - b0; w = a0 - b0
    aa = np.sum(u * u, axis=1); bb = np.sum(u * v, axis=1)
    cc = np.sum(v * v, axis=1); dd = np.sum(u * w, axis=1)
    ee = np.sum(v * w, axis=1)
    denom = aa * cc - bb * bb
    s = np.divide(bb * ee - cc * dd, denom, out=np.zeros_like(denom), where=np.abs(denom) > 1e-12)
    t = np.divide(aa * ee - bb * dd, denom, out=np.zeros_like(denom), where=np.abs(denom) > 1e-12)
    s = np.clip(s, 0.0, 1.0); t = np.clip(t, 0.0, 1.0)
    return np.linalg.norm(w + s[:, None] * u - t[:, None] * v, axis=1)


def numeric_fk_qa(
    rows: Sequence[Mapping[str, Any]],
    spec: PhysicalGraphSpec,
    bone_evidence_class: Mapping[str, str],
) -> dict[str, Any]:
    joint_index = {name: index for index, name in enumerate(JOINT_NAMES)}
    length_rows: dict[str, list[np.ndarray]] = {name: [] for *_, name in DISPLAY_BONES}
    closure_rows: dict[str, list[np.ndarray]] = {edge: [] for edge, *_ in EDGES}
    rom_rows: dict[str, list[np.ndarray]] = {edge: [] for edge, *_ in EDGES}
    crossing_rows = []
    for row in rows:
        points = row["joint_position"]
        for first, second, name in DISPLAY_BONES:
            length_rows[name].append(np.linalg.norm(
                points[:, joint_index[first]] - points[:, joint_index[second]], axis=1,
            ))
        for edge, values in row["closure_error"].items():
            closure_rows[edge].append(values)
        for edge, values in row["joint_excursion_deg"].items():
            rom_rows[edge].append(values)
        # Non-adjacent left/right long bones are a conservative self-crossing
        # diagnostic.  A near approach is retained for visual review rather
        # than silently converted to pose truth.
        crossing_rows.append(_line_distance(
            points[:, joint_index["shoulder_left"]],
            points[:, joint_index["elbow_left"]],
            points[:, joint_index["shoulder_right"]],
            points[:, joint_index["elbow_right"]],
        ))
        crossing_rows.append(_line_distance(
            points[:, joint_index["hip_left"]],
            points[:, joint_index["knee_left"]],
            points[:, joint_index["hip_right"]],
            points[:, joint_index["knee_right"]],
        ))
    lengths = {}
    for name, arrays in length_rows.items():
        values = np.concatenate(arrays)
        lengths[name] = {
            "minimum_m": float(np.min(values)),
            "maximum_m": float(np.max(values)),
            "median_m": float(np.median(values)),
            "max_deviation_from_median_m": float(np.max(np.abs(values - np.median(values)))),
        }
    closure = {
        edge: float(np.max(np.concatenate(arrays)))
        for edge, arrays in closure_rows.items()
    }
    rom = {}
    for edge, arrays in rom_rows.items():
        values = np.concatenate(arrays)
        limit = ROM_LIMIT_DEG[edge]
        rom[edge] = {
            "excursion_max_deg": float(np.max(values)),
            "excursion_p95_deg": float(np.quantile(values, 0.95)),
            "predeclared_limit_deg": limit,
            "pass": bool(np.max(values) <= limit + 20.0),
        }
    all_points = np.concatenate([row["joint_position"] for row in rows])
    determinants = np.concatenate([
        np.linalg.det(row["anatomical_rotation"].reshape(-1, 3, 3)) for row in rows
    ])
    closest = np.concatenate(crossing_rows)
    constant_bones = {
        key: lengths[key] for key in (
            "upper_arm_left", "upper_arm_right", "thigh_left", "thigh_right",
            "forearm_left", "forearm_right", "shank_left", "shank_right",
        )
    }
    return {
        "schema": "biospur-pure-imu-v0-direct-physical-fk-numeric-qa-v1",
        "finite": bool(np.isfinite(all_points).all()),
        "rotation_determinant_min": float(np.min(determinants)),
        "rotation_determinant_max": float(np.max(determinants)),
        "joint_connection_max_closure_error_m": closure,
        "maximum_joint_connection_closure_error_m": float(max(closure.values())),
        "bone_lengths": lengths,
        "bone_evidence_class": dict(bone_evidence_class),
        "constant_length_is_not_dynamic_identification": True,
        "maximum_constant_bone_deviation_m": float(max(
            row["max_deviation_from_median_m"] for row in constant_bones.values()
        )),
        "joint_excursion": rom,
        "rom_coherence_pass": all(row["pass"] for row in rom.values()),
        "nonadjacent_bilateral_bone_minimum_distance_m": float(np.min(closest)),
        "near_crossing_fraction_below_0p02m": float(np.mean(closest < 0.02)),
        "near_crossing_role": "DIAGNOSTIC_REQUIRES_VISUAL_REVIEW_NOT_ESTIMATOR_FACTOR",
        "shared_ik_used": False,
        "pose_labels_used_as_pose_truth": False,
        "viewer_pose_rescue_used": False,
        "pass": bool(
            np.isfinite(all_points).all()
            and abs(np.min(determinants) - 1.0) <= 1e-6
            and abs(np.max(determinants) - 1.0) <= 1e-6
            and max(closure.values()) <= 1e-9
            and max(row["max_deviation_from_median_m"] for row in constant_bones.values()) <= 1e-9
            and all(row["pass"] for row in rom.values())
        ),
    }


def write_contact_sheet(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    bone_evidence_class: Mapping[str, str],
) -> None:
    import matplotlib.pyplot as plt

    columns = 4
    count = len(rows)
    figure = plt.figure(figsize=(16, 4.2 * math.ceil(count / columns)))
    joint_index = {name: index for index, name in enumerate(JOINT_NAMES)}
    for panel, row in enumerate(rows, start=1):
        axis = figure.add_subplot(math.ceil(count / columns), columns, panel, projection="3d")
        points = row["joint_position"][len(row["joint_position"]) // 2]
        colors = {"A": "#138a72", "B": "#d18b00", "C": "#8b7bb8"}
        for first, second, bone in DISPLAY_BONES:
            values = points[[joint_index[first], joint_index[second]]]
            evidence = bone_evidence_class[bone]
            axis.plot(
                values[:, 0], values[:, 1], values[:, 2], "o-", linewidth=2,
                color=colors[evidence], alpha=1.0 if evidence == "A" else 0.75,
            )
        axis.set_title(row["action"])
        axis.set_xlabel("x m"); axis.set_ylabel("y m"); axis.set_zlabel("z m")
        span = np.max(np.ptp(points, axis=0))
        center = np.mean(points, axis=0)
        span = max(span, 0.5)
        axis.set_xlim(center[0] - span / 2, center[0] + span / 2)
        axis.set_ylim(center[1] - span / 2, center[1] + span / 2)
        axis.set_zlim(center[2] - span / 2, center[2] + span / 2)
        axis.view_init(elev=15, azim=-70)
    figure.suptitle(
        "Direct raw-path fixed-profile physical FK · A dynamic / B weak / C proxy · no IK"
    )
    figure.tight_layout()
    figure.savefig(path, dpi=150)
    plt.close(figure)


def write_html_viewer(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    metadata: Mapping[str, Any],
    bone_evidence_class: Mapping[str, str],
) -> None:
    import plotly.graph_objects as go

    joint_index = {name: index for index, name in enumerate(JOINT_NAMES)}
    flattened = []
    labels = []
    for row in rows:
        # Keep the interactive viewer bounded while retaining every selected
        # episode and all five phases.
        take = np.unique(np.rint(np.linspace(
            0, len(row["joint_position"]) - 1, min(90, len(row["joint_position"])),
        )).astype(int))
        for index in take:
            flattened.append(row["joint_position"][index])
            labels.append(f"{row['action']} · {row['phase'][index]}")
    colors = {"A": "#36c2cf", "B": "#f8b84e", "C": "#9a87c7"}
    def trace(points: np.ndarray, label: str, evidence: str) -> go.Scatter3d:
        x = []; y = []; z = []
        for first, second, bone in DISPLAY_BONES:
            if bone_evidence_class[bone] != evidence:
                continue
            a = points[joint_index[first]]; b = points[joint_index[second]]
            x.extend((a[0], b[0], None)); y.extend((a[1], b[1], None)); z.extend((a[2], b[2], None))
        return go.Scatter3d(
            x=x, y=y, z=z, mode="lines+markers",
            line={"width": 6, "color": colors[evidence]},
            marker={"size": 4, "color": colors[evidence]},
            name=f"{evidence} · {label}",
        )
    classes = ("A", "B", "C")
    frames = [go.Frame(data=[
        trace(points, label, evidence) for evidence in classes
    ], name=str(index))
              for index, (points, label) in enumerate(zip(flattened, labels))]
    figure = go.Figure(data=[
        trace(flattened[0], labels[0], evidence) for evidence in classes
    ], frames=frames)
    figure.update_layout(
        title=(
            "Direct raw accelerometer+gyroscope physical FK · fixed capture profile · "
            "A dynamic / B weak / C proxy · no IK / pose truth / viewer rescue"
        ),
        scene={"aspectmode": "data", "xaxis_title": "x m", "yaxis_title": "y m", "zaxis_title": "z m"},
        updatemenus=[{
            "type": "buttons",
            "buttons": [
                {"label": "Play", "method": "animate", "args": [None, {"frame": {"duration": 80, "redraw": True}, "fromcurrent": True}]},
                {"label": "Pause", "method": "animate", "args": [[None], {"frame": {"duration": 0}, "mode": "immediate"}]},
            ],
        }],
        sliders=[{
            "steps": [
                {"label": labels[index], "method": "animate", "args": [[str(index)], {"mode": "immediate", "frame": {"duration": 0, "redraw": True}}]}
                for index in range(len(frames))
            ],
        }],
        annotations=[{
            "text": json.dumps(dict(metadata), sort_keys=True),
            "xref": "paper", "yref": "paper", "x": 0.0, "y": -0.18,
            "showarrow": False, "align": "left", "font": {"size": 9},
        }],
        margin={"b": 150},
    )
    figure.write_html(path, include_plotlyjs="inline", full_html=True)


def generate_direct_fk_artifacts(
    *,
    capture: str,
    episodes: Sequence[Raw6Episode],
    result: Mapping[str, Any],
    spec: PhysicalGraphSpec,
    factors: Mapping[str, EdgeFactors],
    html_path: Path,
    contact_sheet_path: Path,
) -> dict[str, Any]:
    required = representative_actions(capture)
    by_action = {episode.action: episode for episode in episodes}
    missing = sorted(set(required) - set(by_action))
    if missing:
        raise RuntimeError(f"{capture}: required FK inspection actions missing: {missing}")
    state = np.asarray(result["state_coordinates"], dtype=float)
    endpoint_rows = result["endpoint_evidence"]["endpoints"]
    distal_override = {
        segment: np.asarray(
            endpoint_rows[f"{segment}:distal_proxy"]["estimate_sensor_local_m"],
            dtype=float,
        )
        for segment in ("forearm_left", "forearm_right", "shank_left", "shank_right")
    }
    bone_evidence_class = {
        "pelvis_height_half": "B",
        "torso_left_diagonal": "B",
        "torso_right_diagonal": "B",
        "shoulder_width": "B",
        "pelvis_left_diagonal": "B",
        "pelvis_right_diagonal": "B",
        "pelvis_width": "B",
        **{
            segment: result["length_identifiability"][segment]["evidence_class"]
            for segment in ("upper_arm_left", "upper_arm_right", "thigh_left", "thigh_right")
        },
        **{
            segment: endpoint_rows[f"{segment}:distal_proxy"]["evidence_class"]
            for segment in ("forearm_left", "forearm_right", "shank_left", "shank_right")
        },
    }
    rows = [direct_fk_episode(
        by_action[action], state, spec, factors,
        distal_endpoint_override=distal_override,
    ) for action in required]
    qa = numeric_fk_qa(rows, spec, bone_evidence_class)
    write_contact_sheet(contact_sheet_path, rows, bone_evidence_class)
    write_html_viewer(
        html_path, rows,
        metadata={
            "capture": capture,
            "profile": f"PROFILE_{capture}",
            "input": "RAW_ACCELEROMETER_PLUS_GYROSCOPE",
            "shared_ik": False,
            "root_yaw_gauge": "pelvis",
            "evidence_classes": "A_DYNAMICALLY_IDENTIFIED;B_WEAK;C_PRIOR_PROXY",
            "static_t_pose_metric_factor": False,
            "action_label_metric_factor": False,
        },
        bone_evidence_class=bone_evidence_class,
    )
    return {
        "schema": "biospur-pure-imu-v0-direct-physical-fk-artifacts-v1",
        "capture": capture,
        "profile_id": f"PROFILE_{capture}",
        "html_viewer": str(html_path.resolve()),
        "contact_sheet": str(contact_sheet_path.resolve()),
        "actions": list(required),
        "numeric_qa": qa,
        "endpoint_evidence": result["endpoint_evidence"],
        "bone_evidence_class": bone_evidence_class,
        "visual_inspection": {
            "status": "PENDING_INDEPENDENT_REVIEW",
            "natural_standing_role": "QUALITATIVE_EXTERNAL_SANITY_ONLY_NOT_ESTIMATOR_FACTOR",
        },
        "fixed_profile": True,
        "shared_ik_used": False,
        "pose_truth_used": False,
        "manual_quaternion_used": False,
        "viewer_rescue_used": False,
        "all_modeled_endpoints_rendered": True,
        "full_forearm_shank_length_raw_identification_claimed": False,
    }
