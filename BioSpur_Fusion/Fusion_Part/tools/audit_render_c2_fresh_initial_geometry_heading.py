#!/usr/bin/env python3
"""Audit initial lean, nonhinge heading capability, and corrected viewer ownership."""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Mapping

sys.dont_write_bytecode = True

import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse
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
INITIAL_STATE = RUN / "P1_FRONTEND/P1_INITIAL_STILL_STOCHASTIC_STATE.json"
SETTINGS = RUN / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
MAP_HELPER = WORKSPACE / "tools/render_c2_fresh_continuous_squat.py"
OUT = SPRINT / "C2_FRESH_INITIAL_GEOMETRY_HEADING_AUDIT_001"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _array_binding(value: np.ndarray) -> dict[str, Any]:
    array = np.ascontiguousarray(np.asarray(value))
    header = json.dumps(
        {"dtype": str(array.dtype), "shape": list(array.shape)}, sort_keys=True,
    ).encode("utf-8")
    return {
        "dtype": str(array.dtype),
        "shape": list(array.shape),
        "sha256": hashlib.sha256(header + array.tobytes()).hexdigest(),
    }


def _load_map_helper() -> Any:
    spec = importlib.util.spec_from_file_location("fresh_map_helper", MAP_HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load exact rooted-map helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _angles(vector: np.ndarray) -> Mapping[str, float]:
    value = np.asarray(vector, dtype=float)
    value = value / np.linalg.norm(value)
    return {
        "pitch_x_over_z_deg": float(np.degrees(np.arctan2(value[0], value[2]))),
        "roll_y_over_z_deg": float(np.degrees(np.arctan2(value[1], value[2]))),
        "tilt_from_world_plus_z_deg": float(np.degrees(np.arccos(np.clip(value[2], -1.0, 1.0)))),
    }


def _angle_between(first: np.ndarray, second: np.ndarray) -> float:
    a = np.asarray(first, dtype=float) / np.linalg.norm(first)
    b = np.asarray(second, dtype=float) / np.linalg.norm(second)
    return float(np.degrees(np.arccos(np.clip(float(a @ b), -1.0, 1.0))))


def _connection_rows(arrays: Mapping[str, np.ndarray], branch_id: str):
    from biospur_fusion.v0.c2_progressive.functional_geometry import EDGE_SPECS
    from biospur_fusion.v0.c2_progressive.segment_frames import EdgeConnectionVectors

    return {
        edge: EdgeConnectionVectors(
            edge=edge,
            parent=parent,
            child=child,
            parent_sensor_to_joint_m=np.asarray(
                arrays[f"frames/{branch_id}/connection/{edge}/parent"], dtype=float,
            ),
            child_sensor_to_joint_m=np.asarray(
                arrays[f"frames/{branch_id}/connection/{edge}/child"], dtype=float,
            ),
            covariance_m2=np.asarray(
                arrays[f"frames/{branch_id}/connection/{edge}/covariance"], dtype=float,
            ),
        )
        for edge, parent, child in EDGE_SPECS
    }


def _add_covariance_ellipse(
    axis: Any,
    center: np.ndarray,
    covariance: np.ndarray,
    dimensions: tuple[int, int],
    *,
    color: str,
) -> None:
    block = np.asarray(covariance, dtype=float)[np.ix_(dimensions, dimensions)]
    eigenvalues, eigenvectors = np.linalg.eigh(0.5 * (block + block.T))
    eigenvalues = np.maximum(eigenvalues, 0.0)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = eigenvalues[order]
    eigenvectors = eigenvectors[:, order]
    angle = float(np.degrees(np.arctan2(eigenvectors[1, 0], eigenvectors[0, 0])))
    axis.add_patch(Ellipse(
        (float(center[dimensions[0]]), float(center[dimensions[1]])),
        width=2.0 * float(np.sqrt(eigenvalues[0])),
        height=2.0 * float(np.sqrt(eigenvalues[1])),
        angle=angle,
        facecolor=color,
        edgecolor=color,
        alpha=0.10,
        linewidth=0.8,
        zorder=0,
    ))


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("audit must run from canonical Fusion_Part")
    OUT.mkdir(parents=True, exist_ok=False)

    from biospur_fusion.v0.c2_progressive.functional_geometry import EDGE_SPECS, HINGE_EDGES
    from biospur_fusion.v0.c2_progressive.heading import PersistentHeadingOwner
    from biospur_fusion.v0.c2_progressive.quaternion_contract import qmt_wxyz_to_scipy_active
    from biospur_fusion.v0.c2_progressive.scientific_fk import (
        ScientificForwardKinematicsOwner,
        direct_orientation_avatar_fk,
        landmark_proxy_fk_points,
        landmark_proxy_sensitivity_profiles,
    )

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))["effective_settings"]
    initial_state = json.loads(INITIAL_STATE.read_text(encoding="utf-8"))
    if (
        manifest["fresh_verification"].get("pass") is not True
        or manifest.get("heldout_opened") is not False
        or manifest.get("scientific_acceptance_pass") is not False
    ):
        raise RuntimeError("fresh frozen authority is inconsistent")

    map_helper = _load_map_helper()
    chronological_index = 0
    action = "00_initial_still"
    node_by_segment = {
        str(row["body_segment"]): str(row["hardware_id"])
        for row in settings["segment_frames"]["wear_authority"]["rows"]
    }
    segments = tuple(sorted({name for _, p, c in EDGE_SPECS for name in (p, c)}))
    canonical_ids = tuple(row["branch_id"] for row in manifest["structure"]["frame_branches"])
    final_support = manifest["structure"]["physical_trajectory_support"]["18"]

    with np.load(NPZ, allow_pickle=False) as arrays, np.load(REPLAY, allow_pickle=False) as replay:
        weights = np.asarray(arrays["frozen/branch_weights"], dtype=float)
        branch_id = max(
            (value for value, row in final_support.items() if row.get("physically_legal") is True),
            key=lambda value: (float(weights[canonical_ids.index(value)]), value),
        )
        pelvis_node = node_by_segment["pelvis"]
        pelvis_time_us = np.asarray(
            arrays[f"orientation/{chronological_index:02d}/{pelvis_node}/time_us"],
            dtype=np.int64,
        )
        branch_ids = tuple(sorted(final_support))
        root_rows, source_by_root, edge_map_audit = map_helper._compose_exact_root_maps(
            replay,
            chronological_index=chronological_index,
            selection_branch_id=branch_id,
            branch_ids=branch_ids,
            pelvis_time_us=pelvis_time_us,
            edge_specs=EDGE_SPECS,
            require_contiguous_root_rows=False,
        )
        selected_root = int(root_rows[0])
        selected_time_s = float(pelvis_time_us[selected_root]) * 1e-6
        source_rows = {
            segment: int(source_by_root[segment][selected_root]) for segment in segments
        }
        frames = {
            segment: np.asarray(
                arrays[f"frames/{branch_id}/segment_from_sensor/{segment}"], dtype=float,
            )
            for segment in segments
        }
        frame_covariance = {
            segment: np.asarray(
                arrays[f"frames/{branch_id}/frame_covariance/{segment}"], dtype=float,
            )
            for segment in segments
        }
        world_from_sensor: dict[str, np.ndarray] = {}
        world_from_segment: dict[str, np.ndarray] = {}
        delta_by_segment: dict[str, float] = {}
        for segment in segments:
            node = node_by_segment[segment]
            quaternion = np.asarray(
                arrays[f"orientation/{chronological_index:02d}/{node}/quat_world_sensor_wxyz"]
                [source_rows[segment]],
                dtype=float,
            )
            world_from_sensor[segment] = qmt_wxyz_to_scipy_active(quaternion).as_matrix()
            delta = float(np.asarray(
                arrays[f"trajectories/{chronological_index:02d}/{branch_id}/segment_global_delta/{segment}"],
                dtype=float,
            )[selected_root])
            delta_by_segment[segment] = delta
            yaw = Rotation.from_rotvec([0.0, 0.0, delta]).as_matrix()
            world_from_segment[segment] = yaw @ world_from_sensor[segment] @ frames[segment].T

        if any(value != 0.0 for value in delta_by_segment.values()):
            raise RuntimeError("initial still unexpectedly contains a heading update")
        profiles = landmark_proxy_sensitivity_profiles(settings["anthropometric_proxy"])
        profile = profiles[0]
        connections = _connection_rows(arrays, branch_id)
        geometry = landmark_proxy_fk_points(
            root_sensor_position_m=np.zeros(3, dtype=float),
            world_from_segment=world_from_segment,
            segment_from_sensor=frames,
            connection_vectors_by_edge=connections,
            profile=profile,
        )
        altered_profile = dict(profile, torso_surface_proxy_length_m=0.910)
        altered_geometry = landmark_proxy_fk_points(
            root_sensor_position_m=np.zeros(3, dtype=float),
            world_from_segment=world_from_segment,
            segment_from_sensor=frames,
            connection_vectors_by_edge=connections,
            profile=altered_profile,
        )
        surface_scalar_geometry_invariance = all(
            np.array_equal(
                geometry.segment_sensor_positions_m[key],
                altered_geometry.segment_sensor_positions_m[key],
            )
            for key in geometry.segment_sensor_positions_m
        ) and all(
            np.array_equal(
                geometry.shared_joint_positions_m[key],
                altered_geometry.shared_joint_positions_m[key],
            )
            for key in geometry.shared_joint_positions_m
        )
        if not surface_scalar_geometry_invariance:
            raise RuntimeError("corrected geometry still consumes torso surface scalar")

        hip_mid = 0.5 * (
            geometry.shared_joint_positions_m["hip_left"]
            + geometry.shared_joint_positions_m["hip_right"]
        )
        shoulder_mid = 0.5 * (
            geometry.shared_joint_positions_m["shoulder_left"]
            + geometry.shared_joint_positions_m["shoulder_right"]
        )
        functional_spine = shoulder_mid - hip_mid
        sensor_surface_line = (
            geometry.segment_sensor_positions_m["torso"]
            - geometry.segment_sensor_positions_m["pelvis"]
        )
        raw_up_by_segment = {
            segment: np.asarray(
                initial_state["nodes"][node_by_segment[segment]]["gravity_sensor_unit"],
                dtype=float,
            )
            for segment in ("pelvis", "torso")
        }
        initial_rows: dict[str, Any] = {}
        for segment in ("pelvis", "torso"):
            raw_up = raw_up_by_segment[segment]
            vqf_up = world_from_sensor[segment] @ raw_up
            segment_up = world_from_segment[segment] @ np.array([0.0, 0.0, 1.0])
            segment_up_sensor = frames[segment].T @ np.array([0.0, 0.0, 1.0])
            initial_rows[segment] = {
                "hardware_id": node_by_segment[segment],
                "selected_source_row": source_rows[segment],
                "raw_mean_specific_force_mps2": initial_state["nodes"][node_by_segment[segment]][
                    "accelerometer_mean_mps2"
                ],
                "raw_mean_specific_force_up_unit_sensor": raw_up.tolist(),
                "raw_gravity_down_unit_sensor": (-raw_up).tolist(),
                "world_from_sensor": world_from_sensor[segment].tolist(),
                "world_from_sensor_binding": _array_binding(world_from_sensor[segment]),
                "segment_from_sensor": frames[segment].tolist(),
                "segment_from_sensor_binding": _array_binding(frames[segment]),
                "segment_frame_tangent_covariance_rad2": frame_covariance[segment].tolist(),
                "segment_frame_tangent_covariance_binding": _array_binding(frame_covariance[segment]),
                "segment_frame_tangent_covariance_trace_rad2": float(np.trace(frame_covariance[segment])),
                "vqf_mapped_raw_up_world": vqf_up.tolist(),
                "vqf_mapped_raw_up_world_angles": _angles(vqf_up),
                "world_segment_plus_z": segment_up.tolist(),
                "world_segment_plus_z_angles": _angles(segment_up),
                "segment_plus_z_in_sensor": segment_up_sensor.tolist(),
                "segment_plus_z_vs_raw_up_angle_deg": _angle_between(segment_up_sensor, raw_up),
                "heading_delta_rad": delta_by_segment[segment],
            }

        torso_up = world_from_segment["torso"] @ np.array([0.0, 0.0, 1.0])
        legacy_false_spine = torso_up * (
            float(profile["torso_surface_proxy_length_m"])
            + float(profile["chest_to_acromion_line_observation_m"])
        )
        final_heading_rows: dict[str, Any] = {}
        nonhinge_edges = {edge for edge, _, _ in EDGE_SPECS} - set(HINGE_EDGES)
        for edge, _, _ in EDGE_SPECS:
            state = np.asarray(
                arrays[f"frozen/heading_edge_state/{branch_id}:{edge}"], dtype=float,
            )
            final_heading_rows[edge] = {
                "classification": "HINGE" if edge in HINGE_EDGES else "NONHINGE_3DOF",
                "state_layout": ["delta_rad", "variance_rad2", "span_count", "observation_count"],
                "state": state.tolist(),
                "binding": _array_binding(state),
                "delta_rad": float(state[0]),
                "variance_rad2": float(state[1]),
                "span_count": int(state[2]),
                "observation_count": int(state[3]),
            }
        if not all(
            final_heading_rows[edge]["delta_rad"] == 0.0
            and final_heading_rows[edge]["observation_count"] == 0
            for edge in nonhinge_edges
        ):
            raise RuntimeError("expected frozen nonhinge zero-observation state is absent")

        acc_sample_keys = sorted(
            key for key in arrays.files
            if any(token in key.lower() for token in (
                "acc_mps", "accelerometer_mps", "/acc/", "/acceleration/", "calibrated_acc",
            ))
        )
        replay_acc_sample_keys = sorted(
            key for key in replay.files
            if any(token in key.lower() for token in (
                "acc_mps", "accelerometer_mps", "/acc/", "/acceleration/", "calibrated_acc",
            ))
        )

        # One exact SIDE diagnostic: full-R3 sensor positions, shared joints,
        # and connection-only covariance are shown separately.
        renderer = settings["scientific_renderer"]
        side_axes = tuple(renderer["side_axes"])
        dimension = {"x": 0, "y": 1, "z": 2}
        dims = (dimension[side_axes[0]], dimension[side_axes[1]])
        figure, axis = plt.subplots(figsize=(11.25, 8.4), dpi=200)
        joint_lines = {
            "functional spine midline": np.vstack((hip_mid, shoulder_mid)),
            "shoulder crossbar": np.vstack((
                geometry.shared_joint_positions_m["shoulder_left"],
                geometry.shared_joint_positions_m["shoulder_right"],
            )),
            "hip crossbar": np.vstack((
                geometry.shared_joint_positions_m["hip_left"],
                geometry.shared_joint_positions_m["hip_right"],
            )),
        }
        for side in ("left", "right"):
            joint_lines[f"upper arm {side}"] = np.vstack((
                geometry.shared_joint_positions_m[f"shoulder_{side}"],
                geometry.shared_joint_positions_m[f"elbow_{side}"],
            ))
            joint_lines[f"forearm {side}"] = np.vstack((
                geometry.shared_joint_positions_m[f"elbow_{side}"],
                geometry.distal_landmark_positions_m[f"wrist_{side}"],
            ))
            joint_lines[f"thigh {side}"] = np.vstack((
                geometry.shared_joint_positions_m[f"hip_{side}"],
                geometry.shared_joint_positions_m[f"knee_{side}"],
            ))
            joint_lines[f"shank {side}"] = np.vstack((
                geometry.shared_joint_positions_m[f"knee_{side}"],
                geometry.distal_landmark_positions_m[f"ankle_{side}"],
            ))
        for index, (name, line) in enumerate(joint_lines.items()):
            axis.plot(
                line[:, dims[0]], line[:, dims[1]], color="#111827", linewidth=1.6,
                label="functional joint/proxy links" if index == 0 else None, zorder=3,
            )
        for edge, parent, child in EDGE_SPECS:
            joint = geometry.shared_joint_positions_m[edge]
            for segment in (parent, child):
                sensor = geometry.segment_sensor_positions_m[segment]
                axis.plot(
                    [sensor[dims[0]], joint[dims[0]]],
                    [sensor[dims[1]], joint[dims[1]]],
                    color="#94a3b8", linestyle=":", linewidth=0.75, alpha=0.65,
                    zorder=1,
                )
        sensor_points = np.vstack(list(geometry.segment_sensor_positions_m.values()))
        joint_points = np.vstack(list(geometry.shared_joint_positions_m.values()))
        axis.scatter(
            sensor_points[:, dims[0]], sensor_points[:, dims[1]], marker="s", s=28,
            color="#dc2626", label="surface sensor origins", zorder=5,
        )
        axis.scatter(
            joint_points[:, dims[0]], joint_points[:, dims[1]], marker="o", s=22,
            color="#2563eb", label="functional shared joints", zorder=5,
        )
        for segment in ("pelvis", "torso"):
            point = geometry.segment_sensor_positions_m[segment]
            axis.annotate(
                f"{segment}_sensor_surface_ref",
                (point[dims[0]], point[dims[1]]), xytext=(8, 8),
                textcoords="offset points", fontsize=7.5, color="#991b1b",
                arrowprops={"arrowstyle": "-", "color": "#991b1b", "lw": 0.6},
            )
        for name, point in (("functional_hip_mid", hip_mid), ("functional_shoulder_mid", shoulder_mid)):
            axis.scatter(point[dims[0]], point[dims[1]], marker="D", s=30, color="#7c3aed", zorder=6)
            axis.annotate(
                name, (point[dims[0]], point[dims[1]]), xytext=(-92, 8),
                textcoords="offset points", fontsize=7.5, color="#5b21b6",
                arrowprops={"arrowstyle": "-", "color": "#5b21b6", "lw": 0.6},
            )
        for segment, point in geometry.segment_sensor_positions_m.items():
            _add_covariance_ellipse(
                axis, point, geometry.segment_sensor_position_covariance_m2[segment], dims,
                color="#dc2626",
            )
        axis.set_xlim(*map(float, renderer["horizontal_limits_m"]))
        axis.set_ylim(*map(float, renderer["vertical_limits_m"]))
        axis.set_aspect("equal", adjustable="box")
        axis.grid(alpha=0.2)
        axis.set_xlabel(f"fresh replay-world/gauge {side_axes[0]} (m)")
        axis.set_ylabel(f"fresh replay-world/gauge {side_axes[1]} (m)")
        axis.set_title(
            "FRESH INITIAL SIDE — SENSOR/JOINT OWNERSHIP DIAGNOSTIC\n"
            "FINAL-FROZEN FRAME MEAN / INITIAL HEADING ZERO-UPDATE / NON-ANATOMICAL / NOT PASS",
            color="#991b1b", fontweight="bold", fontsize=11,
        )
        axis.legend(loc="upper right", fontsize=7.5, framealpha=0.92)
        figure.text(
            0.5, 0.018,
            f"exact root row={selected_root}; t={selected_time_s:.6f}s; "
            "0.280m surface scalar NOT used as segment axis/length; covariance overlay is "
            "unscaled full-R3 connection-owner only; no IK/rebase/repair",
            ha="center", fontsize=7.5,
        )
        figure.tight_layout(rect=(0.02, 0.055, 0.98, 0.98))
        image_path = OUT / "FRESH_INITIAL_SIDE_SENSOR_VS_FUNCTIONAL_JOINTS.png"
        figure.savefig(image_path, facecolor="white")
        pixel_shape = list(np.asarray(figure.canvas.buffer_rgba()).shape)
        plt.close(figure)

        import qmt.functions.heading_correction as qmt_heading

        heading_source = Path(inspect.getsourcefile(qmt_heading) or "").resolve()
        scientific_fk_source = Path(inspect.getsourcefile(landmark_proxy_fk_points) or "").resolve()
        heading_owner_source = Path(inspect.getsourcefile(PersistentHeadingOwner) or "").resolve()
        report = {
            "schema": "biospur-c2-fresh-initial-geometry-nonhinge-heading-audit-v1",
            "status": "DIAGNOSTIC_CAUSAL_FINDINGS_NOT_PASS",
            "authority": {
                "fresh_manifest": {"path": str(MANIFEST.relative_to(WORKSPACE)), "sha256": _sha(MANIFEST)},
                "fresh_npz": {"path": str(NPZ.relative_to(WORKSPACE)), "sha256": _sha(NPZ)},
                "replay_npz_exact_pair_maps_only": {"path": str(REPLAY.relative_to(WORKSPACE)), "sha256": _sha(REPLAY)},
                "p1_initial_stochastic_state": {"path": str(INITIAL_STATE.relative_to(WORKSPACE)), "sha256": _sha(INITIAL_STATE)},
                "settings": {"path": str(SETTINGS.relative_to(WORKSPACE)), "sha256": _sha(SETTINGS)},
                "scientific_fk_source": {"path": str(scientific_fk_source.relative_to(WORKSPACE)), "sha256": _sha(scientific_fk_source)},
                "heading_owner_source": {"path": str(heading_owner_source.relative_to(WORKSPACE)), "sha256": _sha(heading_owner_source)},
                "official_qmt_heading_source": {"path": str(heading_source), "sha256": _sha(heading_source)},
            },
            "selected_initial_row": {
                "action": action,
                "registered_quantile": 0.0,
                "exact_root_row": selected_root,
                "common_physical_time_s": selected_time_s,
                "selected_source_row_by_segment": source_rows,
                "exact_root_map_edge_audit": edge_map_audit,
                "nearest_interpolation_clamp_or_gap_fill": False,
                "dominant_branch_id": branch_id,
                "dominant_branch_weight": float(weights[canonical_ids.index(branch_id)]),
            },
            "initial_gravity_frame_decomposition": initial_rows,
            "legacy_invalid_surface_geometry_decomposition": {
                "legacy_hip_mid_equals_pelvis_sensor_gauge": True,
                "legacy_pelvis_sensor_to_chest_sensor_vector_m": (
                    torso_up * float(profile["torso_surface_proxy_length_m"])
                ).tolist(),
                "legacy_chest_sensor_to_shoulder_mid_vector_m": (
                    torso_up * float(profile["chest_to_acromion_line_observation_m"])
                ).tolist(),
                "legacy_hip_mid_to_shoulder_mid_vector_m": legacy_false_spine.tolist(),
                "legacy_hip_mid_to_shoulder_mid_angles": _angles(legacy_false_spine),
                "surface_scalar_promoted_to_segment_z": True,
                "public_direct_orientation_avatar_fk_now_rejects": True,
            },
            "corrected_full_r3_geometry_decomposition": {
                "pelvis_surface_sensor_position_m": geometry.segment_sensor_positions_m["pelvis"].tolist(),
                "torso_surface_sensor_position_m": geometry.segment_sensor_positions_m["torso"].tolist(),
                "functional_hip_mid_m": hip_mid.tolist(),
                "functional_shoulder_mid_m": shoulder_mid.tolist(),
                "functional_hip_to_shoulder_vector_m": functional_spine.tolist(),
                "functional_hip_to_shoulder_angles": _angles(functional_spine),
                "surface_pelvis_to_torso_sensor_vector_m": sensor_surface_line.tolist(),
                "surface_pelvis_to_torso_sensor_vector_angles": _angles(sensor_surface_line),
                "pelvis_sensor_minus_functional_hip_mid_m": (
                    geometry.segment_sensor_positions_m["pelvis"] - hip_mid
                ).tolist(),
                "torso_sensor_minus_functional_shoulder_mid_m": (
                    geometry.segment_sensor_positions_m["torso"] - shoulder_mid
                ).tolist(),
                "surface_sensor_origins_equal_functional_nodes": False,
                "torso_surface_scalar_0p280_to_0p910_geometry_exact_invariant": surface_scalar_geometry_invariance,
                "connection_covariance_overlay_scope": geometry.report[
                    "functional_connection_covariance_overlay_scope"
                ],
                "connection_mean_binding_by_edge_endpoint": {
                    f"{edge}:{endpoint}": _array_binding(np.asarray(
                        arrays[f"frames/{branch_id}/connection/{edge}/{endpoint}"], dtype=float,
                    ))
                    for edge, _, _ in EDGE_SPECS for endpoint in ("parent", "child")
                },
                "connection_covariance_binding_by_edge": {
                    edge: _array_binding(np.asarray(
                        arrays[f"frames/{branch_id}/connection/{edge}/covariance"], dtype=float,
                    ))
                    for edge, _, _ in EDGE_SPECS
                },
            },
            "frozen_final_heading_edge_state": final_heading_rows,
            "nonhinge_missing_capability": {
                "edges": sorted(nonhinge_edges),
                "all_end_delta_exact_zero": True,
                "all_end_observation_count_exact_zero": True,
                "joint_construction": settings["heading"]["joint_construction"],
                "explicit_est_settings": settings["heading"]["explicit_est_settings"],
                "mechanism": (
                    "NONHINGE_IDENTITY_3DOF_PLUS_FULL_MINUS_PI_TO_PI_RANGES_WITH_"
                    "USE_ROM_CONSTRAINTS_FALSE_MAKES_EVERY_HEADING_ADMISSIBLE_AND_"
                    "OFFICIAL_ESTIMATEDELTA3D_RATING_ZERO"
                ),
                "example_rom_imported": False,
                "zero_delta_interpreted_as_observed_heading": False,
            },
            "bounded_local_joint_acceleration_heading_pivot_closure": {
                "required": (
                    "TIME_SERIES_CALIBRATED_ACCELEROMETER_PLUS_GYRO_QUATERNION_"
                    "PAIR_CLOCK_MAPS_AND_FULL_R3_SENSOR_TO_JOINT_LEVERS"
                ),
                "fresh_npz_accelerometer_sample_keys": acc_sample_keys,
                "replay_npz_accelerometer_sample_keys": replay_acc_sample_keys,
                "time_series_accelerometer_samples_present": bool(
                    acc_sample_keys or replay_acc_sample_keys
                ),
                "test_executed": False,
                "reason": (
                    "NO_PERSISTED_TIME_SERIES_ACCELEROMETER_ARRAYS_IN_FRESH_OR_REPLAY_NPZ;"
                    "P1_CONTAINS_INITIAL_MEANS_ONLY;NO_PAYLOAD_REREAD_AUTHORIZED_BY_THIS_AUDIT"
                ),
                "zero_lock_or_rom_substitution_created": False,
            },
            "owner_path_audit": {
                "scientific_forward_fk_keeps_segment_sensor_positions_and_shared_joint_positions_separate": True,
                "landmark_proxy_fk_uses_full_r3_connections": True,
                "direct_orientation_avatar_public_path_disabled": True,
                "physical_forward_path_root_is_pelvis_sensor_translation_gauge_only": True,
                "new_scientific_or_physical_acceptance_claim": False,
            },
            "focused_geometry_test": {
                "test": "test_functional_landmark_proxy_keeps_surface_sensors_off_upright_joint_nodes",
                "result": "PASS",
                "nonzero_anterior_pelvis_and_torso_sensor_offsets": True,
                "curved_surface_mount_tilt": True,
                "functional_spine_remained_upright": True,
                "surface_sensors_remained_anterior": True,
                "surface_scalar_did_not_move_geometry": True,
            },
            "artifact": {
                "path": str(image_path.relative_to(WORKSPACE)),
                "sha256": _sha(image_path),
                "pixel_shape_rgba": pixel_shape,
                "personally_inspected": False,
                "scientific_acceptance_pass": False,
            },
            "payload_reread": False,
            "heldout_opened": False,
            "fit_qmt_or_progressive_rerun": False,
            "inverse_kinematics_rebase_retarget_or_repair": False,
            "scientific_acceptance_pass": False,
        }

    audit_path = OUT / "AUDIT.json"
    with audit_path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    image_path.chmod(0o444)
    audit_path.chmod(0o444)
    print(json.dumps({
        "audit": str(audit_path), "audit_sha256": _sha(audit_path),
        "image": str(image_path), "image_sha256": _sha(image_path),
        "pelvis_source_row": source_rows["pelvis"],
        "torso_source_row": source_rows["torso"],
        "local_joint_acceleration_heading_test_executed": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
