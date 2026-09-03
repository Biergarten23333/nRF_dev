#!/usr/bin/env python3
"""Render frozen initial/final/squat with factorized distal S1 support."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Mapping

sys.dont_write_bytecode = True

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
from scipy.spatial.transform import Rotation


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
SPRINT = RUN / "CONTINUATION_SPRINT"
MANIFEST = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.json"
NPZ = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.npz"
REPLAY = (
    SPRINT / "RUN013_POSTFREEZE_RETROSPECTIVE_QMT_REPLAY_002"
    / "POSTFREEZE_RETROSPECTIVE_QMT_STATE.npz"
)
R006_AUDIT = (
    SPRINT / "C2_FRESH_CAUSAL_CONTINUOUS_SQUAT_MULTIBRANCH_TRIVIEW_006"
    / "FRESH_CAUSAL_CONTINUOUS_SQUAT_TRIVIEW_AUDIT.json"
)
SETTINGS = RUN / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
AUTHORITY = RUN / "USER_DISTAL_LONGITUDINAL_SUPPORT_AMENDMENT_004.json"
MAP_HELPER = WORKSPACE / "tools/render_c2_fresh_continuous_squat.py"
OUT = SPRINT / "C2_FRESH_DISTAL_LONGITUDINAL_SUPPORT_TRIVIEWS_001"
DISTAL_EDGE = {
    "forearm_left": "elbow_left",
    "forearm_right": "elbow_right",
    "shank_left": "knee_left",
    "shank_right": "knee_right",
}
DISTAL_LINE = {
    "forearm_left": "forearm_left",
    "forearm_right": "forearm_right",
    "shank_left": "shank_left",
    "shank_right": "shank_right",
}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _array_sha(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    header = json.dumps(
        {"dtype": str(array.dtype), "shape": list(array.shape)}, sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(header + array.tobytes()).hexdigest()


def _write_new(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def _load_map_helper() -> Any:
    spec = importlib.util.spec_from_file_location("fresh_exact_map_helper", MAP_HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load exact rooted-map helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _connections(arrays: Mapping[str, np.ndarray], prefix: str):
    from biospur_fusion.v0.c2_progressive.functional_geometry import EDGE_SPECS
    from biospur_fusion.v0.c2_progressive.segment_frames import EdgeConnectionVectors

    return {
        edge: EdgeConnectionVectors(
            edge=edge,
            parent=parent,
            child=child,
            parent_sensor_to_joint_m=np.asarray(
                arrays[f"{prefix}/connection/{edge}/parent"], dtype=float,
            ),
            child_sensor_to_joint_m=np.asarray(
                arrays[f"{prefix}/connection/{edge}/child"], dtype=float,
            ),
            covariance_m2=np.asarray(
                arrays[f"{prefix}/connection/{edge}/covariance"], dtype=float,
            ),
        )
        for edge, parent, child in EDGE_SPECS
    }


def _frame_prefix(chronological_index: int, branch_id: str) -> str:
    if chronological_index == 0:
        return f"frames/{branch_id}"
    return f"physical_trajectory/{chronological_index}/{branch_id}"


def _axis_prefix(chronological_index: int) -> str:
    if chronological_index == 0:
        return "geometry/axis"
    return f"geometry_checkpoint/{chronological_index}/axis"


def _corrected_support(
    arrays: Mapping[str, np.ndarray],
    *,
    settings: Mapping[str, Any],
    frame_prefix: str,
    axis_prefix: str,
    axis_sign_by_edge: Mapping[str, int],
) -> tuple[
    dict[str, np.ndarray],
    dict[str, list[dict[str, Any]]],
    dict[str, np.ndarray],
    dict[str, Any],
]:
    from biospur_fusion.v0.c2_progressive.functional_geometry import EDGE_SPECS
    from biospur_fusion.v0.c2_progressive.segment_frames import (
        SINGLE_JOINT_DISTAL_SEGMENTS,
        _factorized_distal_longitudinal_support,
        _frame_from_z_y,
        _nominal_wear_segment_from_sensor,
        _validated_wear_authority,
    )

    segments = tuple(sorted({name for _, parent, child in EDGE_SPECS for name in (parent, child)}))
    old_segment_from_sensor = {
        segment: np.asarray(
            arrays[f"{frame_prefix}/segment_from_sensor/{segment}"], dtype=float,
        )
        for segment in segments
    }
    sensor_from_segment = {
        segment: value.T.copy() for segment, value in old_segment_from_sensor.items()
    }
    wear = _validated_wear_authority(settings["segment_frames"])
    nominal = _nominal_wear_segment_from_sensor(wear)
    axis_audit: dict[str, Any] = {}
    for segment in sorted(SINGLE_JOINT_DISTAL_SEGMENTS):
        edge = DISTAL_EDGE[segment]
        unsigned_axis = np.asarray(
            arrays[f"{axis_prefix}/{edge}/child"], dtype=float,
        )
        signed_axis = unsigned_axis * int(axis_sign_by_edge[edge])
        corrected, flags = _frame_from_z_y(
            nominal[segment].T[:, 2], signed_axis,
            label=f"{segment}:DERIVED_FACTORIZED_S1_BASE",
        )
        sensor_from_segment[segment] = corrected
        axis_audit[segment] = {
            "edge": edge,
            "unsigned_axis_sensor": unsigned_axis.tolist(),
            "axis_sign": int(axis_sign_by_edge[edge]),
            "signed_axis_sensor": signed_axis.tolist(),
            "axis_array_sha256": _array_sha(unsigned_axis),
            "corrected_frame_y_equals_signed_axis": bool(np.allclose(
                corrected[:, 1], signed_axis, atol=1e-12,
            )),
            "construction_flags": flags,
            "sole_joint_lever_used": False,
        }
        if not axis_audit[segment]["corrected_frame_y_equals_signed_axis"]:
            raise RuntimeError(f"{segment}: corrected frame did not preserve hinge axis")
    weak = float(settings["segment_frames"]["unidentified_direction_sigma_rad"])
    broad_covariance = {segment: np.eye(3) * weak**2 for segment in segments}
    support, second_moment = _factorized_distal_longitudinal_support(
        sensor_from_segment, broad_covariance, wear,
    )
    if any(len(rows) != 8 for rows in support.values()):
        raise RuntimeError("derived distal support did not retain exact eight-point S1")
    return old_segment_from_sensor, support, second_moment, axis_audit


def _fk_result(
    *,
    heading_world_from_sensor: Mapping[str, np.ndarray],
    sensor_from_segment: Mapping[str, np.ndarray],
    connections: Mapping[str, Any],
    profile: Mapping[str, Any],
):
    from biospur_fusion.v0.c2_progressive.scientific_fk import landmark_proxy_fk_points

    return landmark_proxy_fk_points(
        root_sensor_position_m=np.zeros(3, dtype=float),
        world_from_segment={
            segment: np.asarray(heading_world_from_sensor[segment])
            @ np.asarray(sensor_from_segment[segment])
            for segment in heading_world_from_sensor
        },
        segment_from_sensor={
            segment: np.asarray(value).T.copy()
            for segment, value in sensor_from_segment.items()
        },
        connection_vectors_by_edge=connections,
        profile=profile,
    )


def _line_map(result: Any) -> dict[str, np.ndarray]:
    """Expose the explicit viewer graph without inventing extra joint nodes."""

    joints = result.shared_joint_positions_m
    distal = result.distal_landmark_positions_m
    shoulder_mid = 0.5 * (
        np.asarray(joints["shoulder_left"], dtype=float)
        + np.asarray(joints["shoulder_right"], dtype=float)
    )
    hip_mid = 0.5 * (
        np.asarray(joints["hip_left"], dtype=float)
        + np.asarray(joints["hip_right"], dtype=float)
    )
    endpoints = {
        "spine": (hip_mid, shoulder_mid),
        "shoulder_crossbar": (joints["shoulder_right"], joints["shoulder_left"]),
        "hip_crossbar": (joints["hip_right"], joints["hip_left"]),
        "upper_arm_left": (joints["shoulder_left"], joints["elbow_left"]),
        "upper_arm_right": (joints["shoulder_right"], joints["elbow_right"]),
        "thigh_left": (joints["hip_left"], joints["knee_left"]),
        "thigh_right": (joints["hip_right"], joints["knee_right"]),
        "forearm_left": (joints["elbow_left"], distal["wrist_left"]),
        "forearm_right": (joints["elbow_right"], distal["wrist_right"]),
        "shank_left": (joints["knee_left"], distal["ankle_left"]),
        "shank_right": (joints["knee_right"], distal["ankle_right"]),
    }
    return {
        name: np.vstack((np.asarray(start, dtype=float), np.asarray(end, dtype=float)))
        for name, (start, end) in endpoints.items()
    }


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("distal support viewer requires canonical Fusion_Part")

    from biospur_fusion.v0.c2_progressive.functional_geometry import EDGE_SPECS
    from biospur_fusion.v0.c2_progressive.quaternion_contract import qmt_wxyz_to_scipy_active
    from biospur_fusion.v0.c2_progressive.scientific_fk import (
        landmark_proxy_sensitivity_profiles,
    )
    from biospur_fusion.v0.c2_progressive.segment_frames import (
        SINGLE_JOINT_DISTAL_SEGMENTS,
    )

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))["effective_settings"]
    authority = json.loads(AUTHORITY.read_text(encoding="utf-8"))
    r006 = json.loads(R006_AUDIT.read_text(encoding="utf-8"))
    if (
        manifest["fresh_verification"].get("pass") is not True
        or manifest.get("heldout_opened") is not False
        or authority["scientific_acceptance_pass"] is not False
        or authority["owner_correction"]["longitudinal_support"]["quadrature_count_per_segment"] != 8
    ):
        raise RuntimeError("fresh distal-support viewer authority is inconsistent")
    profiles = landmark_proxy_sensitivity_profiles(settings["anthropometric_proxy"])
    if len(profiles) != 5:
        raise RuntimeError("viewer must preserve exact five raw profile sensitivities")
    renderer = settings["scientific_renderer"]
    segments = tuple(sorted({name for _, parent, child in EDGE_SPECS for name in (parent, child)}))
    node_by_segment = {
        str(row["body_segment"]): str(row["hardware_id"])
        for row in settings["segment_frames"]["wear_authority"]["rows"]
    }
    canonical_rows = {
        str(row["branch_id"]): row for row in manifest["structure"]["frame_branches"]
    }
    weights_by_branch: dict[str, float]
    map_helper = _load_map_helper()
    npz_before = _sha(NPZ)
    replay_before = _sha(REPLAY)
    action_specs = (
        (0, "00_initial_still", "RETROSPECTIVE_FINAL_FROZEN", 0.0),
        (15, "16_squat", "CAUSAL_PREFIX15_DERIVED", None),
        (16, "17_final_still", "CAUSAL_PREFIX16_DERIVED", 1.0),
    )
    action_payloads: list[dict[str, Any]] = []
    OUT.mkdir(parents=True, exist_ok=False)
    artifacts: list[dict[str, Any]] = []
    view_specs = (
        ("FRONT", tuple(renderer["front_axes"])),
        ("SIDE", tuple(renderer["side_axes"])),
        ("TOP", tuple(renderer["top_axes"])),
    )
    coordinate = {"x": 0, "y": 1, "z": 2}
    profile_styles = ("-", "--", ":", "-.", (0, (3, 1, 1, 1)))
    profile_labels = (
        "Observer A L/R forearm 245 mm",
        "Observer B low L/R 260 mm",
        "Observer B high L/R 265 mm",
        "cross-side L-low/R-high",
        "cross-side L-high/R-low",
    )
    distal_colors = {
        "forearm_left": "#2563eb",
        "forearm_right": "#0ea5e9",
        "shank_left": "#dc2626",
        "shank_right": "#f97316",
    }

    with np.load(NPZ, allow_pickle=False) as arrays, np.load(REPLAY, allow_pickle=False) as replay:
        frozen_weights = np.asarray(arrays["frozen/branch_weights"], dtype=float)
        canonical_order = tuple(canonical_rows)
        weights_by_branch = {
            branch_id: float(frozen_weights[canonical_order.index(branch_id)])
            for branch_id in canonical_order
        }
        for chronological_index, action, role, quantile in action_specs:
            support_index = 18 if chronological_index == 0 else chronological_index
            support_rows = manifest["structure"]["physical_trajectory_support"][str(support_index)]
            branch_ids = tuple(sorted(
                branch_id for branch_id, row in support_rows.items()
                if row.get("physically_legal") is True
            ))
            if len(branch_ids) != 4:
                raise RuntimeError(f"{action}: exact four-branch support changed")
            selection_branch = max(
                branch_ids, key=lambda value: (weights_by_branch[value], value),
            )
            selected_root: int | None = None
            selected_time_s: float
            source_by_root: dict[str, dict[int, int]] | None = None
            root_rows: np.ndarray | None = None
            edge_map_audit: Mapping[str, Any] | None = None
            if chronological_index in (0, 15):
                pelvis_time = np.asarray(
                    arrays[f"orientation/{chronological_index:02d}/{node_by_segment['pelvis']}/time_us"],
                    dtype=np.int64,
                )
                root_rows, source_by_root, edge_map_audit = map_helper._compose_exact_root_maps(
                    replay,
                    chronological_index=chronological_index,
                    selection_branch_id=selection_branch,
                    branch_ids=branch_ids,
                    pelvis_time_us=pelvis_time,
                    edge_specs=EDGE_SPECS,
                    require_contiguous_root_rows=(chronological_index == 15),
                )
                if chronological_index == 0:
                    assert quantile is not None
                    selected_root = int(root_rows[int(np.rint(quantile * (len(root_rows) - 1)))])
                else:
                    selected_root = int(r006["selected_pelvis_source_row"])
                    if selected_root not in set(int(value) for value in root_rows):
                        raise RuntimeError("prior nonvisual squat row left exact rooted support")
                selected_time_s = float(pelvis_time[selected_root]) * 1e-6
            else:
                selected_time_s = float("nan")

            branch_payloads: list[dict[str, Any]] = []
            for branch_id in sorted(branch_ids, key=lambda value: (-weights_by_branch[value], value)):
                frame_prefix = _frame_prefix(chronological_index, branch_id)
                old_frames, support, second_moment, axis_audit = _corrected_support(
                    arrays,
                    settings=settings,
                    frame_prefix=frame_prefix,
                    axis_prefix=_axis_prefix(chronological_index),
                    axis_sign_by_edge=canonical_rows[branch_id]["axis_sign_by_edge"],
                )
                connections = _connections(arrays, frame_prefix)
                old_world_from_segment: dict[str, np.ndarray] = {}
                if chronological_index in (0, 15):
                    assert selected_root is not None and source_by_root is not None
                    for segment in segments:
                        source_index = int(source_by_root[segment][selected_root])
                        quaternion = np.asarray(
                            arrays[
                                f"orientation/{chronological_index:02d}/{node_by_segment[segment]}/"
                                "quat_world_sensor_wxyz"
                            ][source_index], dtype=float,
                        )
                        world_from_sensor = qmt_wxyz_to_scipy_active(quaternion).as_matrix()
                        delta = float(np.asarray(
                            arrays[
                                f"trajectories/{chronological_index:02d}/{branch_id}/"
                                f"segment_global_delta/{segment}"
                            ], dtype=float,
                        )[selected_root])
                        yaw = Rotation.from_rotvec([0.0, 0.0, delta]).as_matrix()
                        old_world_from_segment[segment] = (
                            yaw @ world_from_sensor @ old_frames[segment].T
                        )
                else:
                    common_time = np.asarray(
                        arrays[f"{frame_prefix}/common_physical_time_s"], dtype=float,
                    )
                    assert quantile is not None
                    sample_index = int(np.rint(quantile * (len(common_time) - 1)))
                    selected_time_s = float(common_time[sample_index])
                    old_world_from_segment = {
                        segment: np.asarray(
                            arrays[f"{frame_prefix}/world_from_segment/{segment}"][sample_index],
                            dtype=float,
                        )
                        for segment in segments
                    }
                heading_world_from_sensor = {
                    segment: old_world_from_segment[segment] @ old_frames[segment]
                    for segment in segments
                }
                base_sensor_frames = {
                    segment: old_frames[segment].T.copy() for segment in segments
                }
                for segment in sorted(SINGLE_JOINT_DISTAL_SEGMENTS):
                    base_sensor_frames[segment] = np.asarray(
                        support[segment][0]["sensor_from_segment"], dtype=float,
                    )
                base_results = [
                    _fk_result(
                        heading_world_from_sensor=heading_world_from_sensor,
                        sensor_from_segment=base_sensor_frames,
                        connections=connections,
                        profile=profile,
                    )
                    for profile in profiles
                ]
                candidate_results: dict[str, list[list[Any]]] = {}
                for segment in sorted(SINGLE_JOINT_DISTAL_SEGMENTS):
                    candidate_results[segment] = []
                    for candidate in support[segment]:
                        candidate_frames = {
                            name: value.copy() for name, value in base_sensor_frames.items()
                        }
                        candidate_frames[segment] = np.asarray(
                            candidate["sensor_from_segment"], dtype=float,
                        )
                        candidate_results[segment].append([
                            _fk_result(
                                heading_world_from_sensor=heading_world_from_sensor,
                                sensor_from_segment=candidate_frames,
                                connections=connections,
                                profile=profile,
                            )
                            for profile in profiles
                        ])
                branch_payloads.append({
                    "branch_id": branch_id,
                    "posterior_weight": weights_by_branch[branch_id],
                    "frame_prefix": frame_prefix,
                    "base_results": base_results,
                    "candidate_results": candidate_results,
                    "support": support,
                    "axis_audit": axis_audit,
                    "second_moment": second_moment,
                    "old_frame_sha256_by_segment": {
                        segment: _array_sha(old_frames[segment]) for segment in segments
                    },
                    "heading_world_from_sensor_sha256_by_segment": {
                        segment: _array_sha(heading_world_from_sensor[segment]) for segment in segments
                    },
                })

            figure, axes = plt.subplots(
                4, 3, figsize=(22.5, 16.8), dpi=int(renderer["dpi"]), squeeze=False,
            )
            core_exclusions = set(DISTAL_LINE.values())
            for branch_row, payload in enumerate(branch_payloads):
                for view_column, (view_name, (horizontal_name, vertical_name)) in enumerate(view_specs):
                    axis = axes[branch_row, view_column]
                    horizontal = coordinate[horizontal_name]
                    vertical = coordinate[vertical_name]
                    for profile_index, result in enumerate(payload["base_results"]):
                        for line_name, line in _line_map(result).items():
                            if line_name in core_exclusions:
                                continue
                            axis.plot(
                                line[:, horizontal], line[:, vertical], color="#111827",
                                linestyle=profile_styles[profile_index],
                                linewidth=1.0 if profile_index == 0 else 0.65,
                                alpha=0.86 if profile_index == 0 else 0.35, zorder=2,
                            )
                    for segment in sorted(SINGLE_JOINT_DISTAL_SEGMENTS):
                        rows = payload["support"][segment]
                        maximum_weight = max(
                            float(row["normalized_factorized_weight"]) for row in rows
                        )
                        for candidate_index, candidate in enumerate(rows):
                            relative_weight = (
                                float(candidate["normalized_factorized_weight"])
                                / maximum_weight
                            )
                            for profile_index, result in enumerate(
                                payload["candidate_results"][segment][candidate_index]
                            ):
                                line = _line_map(result)[DISTAL_LINE[segment]]
                                axis.plot(
                                    line[:, horizontal], line[:, vertical],
                                    color=distal_colors[segment],
                                    linestyle=profile_styles[profile_index],
                                    linewidth=0.75 if profile_index == 0 else 0.45,
                                    alpha=(0.12 + 0.50 * relative_weight)
                                    * (1.0 if profile_index == 0 else 0.55),
                                    zorder=3,
                                )
                            sensor_position = payload["candidate_results"][segment][candidate_index][0].segment_sensor_positions_m[segment]
                            axis.scatter(
                                [sensor_position[horizontal]], [sensor_position[vertical]],
                                marker="s", s=7, color="#991b1b", alpha=0.22 + 0.45 * relative_weight,
                                zorder=4,
                            )
                    base = payload["base_results"][0]
                    joint_points = np.vstack(list(base.shared_joint_positions_m.values()))
                    nondistal_sensors = np.vstack([
                        value for name, value in base.segment_sensor_positions_m.items()
                        if name not in SINGLE_JOINT_DISTAL_SEGMENTS
                    ])
                    axis.scatter(
                        joint_points[:, horizontal], joint_points[:, vertical],
                        marker="o", s=10, color="#1d4ed8", zorder=5,
                    )
                    axis.scatter(
                        nondistal_sensors[:, horizontal], nondistal_sensors[:, vertical],
                        marker="s", s=10, color="#b91c1c", zorder=5,
                    )
                    axis.set_xlim(*map(float, renderer["horizontal_limits_m"]))
                    axis.set_ylim(*map(float, renderer["vertical_limits_m"]))
                    axis.set_aspect("equal", adjustable="box")
                    axis.grid(alpha=0.18)
                    if branch_row == 0:
                        axis.set_title(view_name, fontsize=10)
                    axis.set_xlabel(f"replay-world/gauge {horizontal_name} (m)", fontsize=7)
                    axis.set_ylabel(f"replay-world/gauge {vertical_name} (m)", fontsize=7)
                    if view_column == 0:
                        short = payload["branch_id"].removeprefix("HINGE_SIGN_").replace("_", " ")
                        axis.text(
                            0.01, 0.98, f"{short}\nw={payload['posterior_weight']:.6f}",
                            transform=axis.transAxes, va="top", ha="left", fontsize=6.4,
                            bbox={"facecolor": "white", "alpha": 0.78, "edgecolor": "none"},
                        )
            legend = [
                Line2D([0], [0], color="#111827", linestyle=profile_styles[index],
                       linewidth=1.0, label=label)
                for index, label in enumerate(profile_labels)
            ] + [
                Line2D([0], [0], color=color, linewidth=1.1,
                       label=f"{segment} 8-point weighted S1 support")
                for segment, color in distal_colors.items()
            ] + [
                Line2D([0], [0], color="#1d4ed8", marker="o", linestyle="None", label="functional joint"),
                Line2D([0], [0], color="#b91c1c", marker="s", linestyle="None", label="surface sensor origin"),
            ]
            figure.legend(
                legend, [handle.get_label() for handle in legend], loc="lower center",
                bbox_to_anchor=(0.5, 0.055), ncol=4, fontsize=7.0, framealpha=0.94,
            )
            role_title = (
                "RETROSPECTIVE FINAL-FROZEN VIEWER / NOT CAUSAL PROGRESS"
                if chronological_index == 0 else
                f"{role.replace('_', ' ')} / DERIVED OWNER HYPOTHESIS"
            )
            figure.suptitle(
                f"ATTEMPT003 {action} — FACTORIZED DISTAL LONGITUDINAL S1 SUPPORT\n"
                f"{role_title} / NOT POSE TRUTH / NOT SCIENCE PASS",
                color="#991b1b", fontsize=13, fontweight="bold",
            )
            figure.text(
                0.5, 0.018,
                f"t={selected_time_s:.6f} s; 4 retained hinge-sign branches × 8 per-segment S1 candidates; "
                "sole elbow/knee lever excluded; weights from broad wear likelihood; wrist/ankle tuned mean disabled\n"
                "Observer A 245 mm and Observer B [260,265] mm remain separate; old QMT delta reused, "
                "candidate invariance unproven; sensitivity only; no pixel selection, IK/rebase/repair/payload/heldout",
                ha="center", fontsize=8.2,
            )
            figure.tight_layout(rect=(0.01, 0.12, 0.99, 0.92))
            png = OUT / f"FRESH_{chronological_index:02d}_{action}_DISTAL_S1_SUPPORT_TRIVIEW.png"
            figure.savefig(png)
            plt.close(figure)
            shape = tuple(int(value) for value in plt.imread(png).shape[:2])
            png.chmod(0o444)
            artifacts.append({
                "chronological_index": chronological_index,
                "action": action,
                "path": str(png.relative_to(WORKSPACE)),
                "sha256": _sha(png),
                "pixel_dimensions": [shape[1], shape[0]],
            })
            action_payloads.append({
                "chronological_index": chronological_index,
                "action": action,
                "role": role,
                "selected_time_s": selected_time_s,
                "selected_pelvis_source_row": selected_root,
                "selection_rule": (
                    "REGISTERED_INITIAL_QUANTILE_0"
                    if chronological_index == 0 else
                    "PRESERVED_PRE_SUPPORT_R006_BILATERAL_FLEXION_TIMESTAMP"
                    if chronological_index == 15 else
                    "REGISTERED_FINAL_STILL_QUANTILE_1_ON_CAUSAL_CHECKPOINTS"
                ),
                "branch_rows": [
                    {
                        "branch_id": payload["branch_id"],
                        "posterior_weight": payload["posterior_weight"],
                        "frame_prefix": payload["frame_prefix"],
                        "axis_audit": payload["axis_audit"],
                        "old_frame_sha256_by_segment": payload["old_frame_sha256_by_segment"],
                        "heading_world_from_sensor_sha256_by_segment": payload["heading_world_from_sensor_sha256_by_segment"],
                        "factorized_support_by_segment": payload["support"],
                        "support_tangent_second_moment_rad2": {
                            segment: value.tolist()
                            for segment, value in payload["second_moment"].items()
                        },
                    }
                    for payload in branch_payloads
                ],
                "exact_root_map_audit": edge_map_audit,
            })

    if _sha(NPZ) != npz_before or _sha(REPLAY) != replay_before:
        raise RuntimeError("immutable frozen arrays changed during derived rendering")
    audit = {
        "schema": "biospur-c2-fresh-factorized-distal-longitudinal-support-render-v1",
        "authority": {"path": str(AUTHORITY.relative_to(WORKSPACE)), "sha256": _sha(AUTHORITY)},
        "fresh_manifest": {"path": str(MANIFEST.relative_to(WORKSPACE)), "sha256": _sha(MANIFEST)},
        "fresh_npz": {"path": str(NPZ.relative_to(WORKSPACE)), "sha256": npz_before},
        "replay_exact_source_maps": {"path": str(REPLAY.relative_to(WORKSPACE)), "sha256": replay_before},
        "source": {"path": str(Path(__file__).resolve().relative_to(WORKSPACE)), "sha256": _sha(Path(__file__).resolve())},
        "owner_source_hashes": {
            path: _sha(WORKSPACE / path)
            for path in (
                "src/biospur_fusion/v0/c2_progressive/segment_frames.py",
                "src/biospur_fusion/v0/c2_progressive/pipeline_runtime.py",
                "src/biospur_fusion/v0/c2_progressive/scientific_fk.py",
                "tests/v0/test_c2_p2_prefit_owners.py",
            )
        },
        "actions": action_payloads,
        "artifacts": artifacts,
        "all_four_hinge_sign_branches_preserved": True,
        "eight_positive_weight_s1_candidates_per_distal_segment": True,
        "support_factorized_not_collapsed_to_global_pose": True,
        "old_qmt_delta_reused_for_each_distal_s1_candidate": True,
        "qmt_delta_invariance_to_distal_s1_candidate_proven": False,
        "qmt_delta_reuse_is_sensitivity_only_and_uncertainty_widened": True,
        "single_distal_mean_rendered_as_tuned_pose": False,
        "fit_qmt_or_19_prefix_rerun": False,
        "payload_or_heldout_access": False,
        "causal_progressive_metrics_modified": False,
        "ik_rebase_retarget_repair": False,
        "nonhinge_heading_zero_observation_capability_gap_remains": True,
        "scientific_acceptance_pass": False,
        "tuned_human_pose_pass": False,
    }
    audit_path = OUT / "AUDIT.json"
    _write_new(audit_path, audit)
    print(json.dumps({
        "audit": str(audit_path),
        "audit_sha256": _sha(audit_path),
        "artifacts": artifacts,
        "scientific_acceptance_pass": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
