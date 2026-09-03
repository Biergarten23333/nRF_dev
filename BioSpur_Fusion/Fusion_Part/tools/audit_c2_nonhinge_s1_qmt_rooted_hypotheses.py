#!/usr/bin/env python3
"""Bounded H1-H3 ablation and physical-first S1 representative from saved state."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

sys.dont_write_bytecode = True

import matplotlib

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
import numpy as np
from scipy.spatial.transform import Rotation

import render_c2_fresh_continuous_squat as exact_maps
import render_c2_fresh_distal_longitudinal_support as viewer_helpers
from render_c2_nonhinge_s1_qmt_rooted_debug import (
    _array_sha,
    _branch_at_prefix,
    _physical_gate,
    _sha,
    _write_json,
)
from replay_c2_postfreeze_heading import _reconstruct_frame_branches


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
SPRINT = RUN / "CONTINUATION_SPRINT"
SETTINGS = RUN / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
FROZEN_MANIFEST = RUN / "C2_REAL_DIAGNOSTIC_FROZEN_STATE_013.json"
SOURCE_NPZ = SPRINT / "C2_NONHINGE_TRAINING_REPLAY_001/POSTFREEZE_RETROSPECTIVE_QMT_STATE.npz"
POSTERIOR_NPZ = SPRINT / "C2_NONHINGE_JOINT_RAO_REPLAY_002/CORRECTED_PREFIX_NONHINGE_STATE.npz"
STATE_DIR = SPRINT / "C2_NONHINGE_S1_QMT_ROOTED_DEBUG_004"
STATE_NPZ = STATE_DIR / "DERIVED_QMT_ROOTED_STATE.npz"
STATE_AUDIT = STATE_DIR / "AUDIT.json"
OUT = SPRINT / "C2_NONHINGE_S1_QMT_ROOTED_HYPOTHESIS_ABLATION_002"

NONHINGE_EDGES = (
    "pelvis_torso", "shoulder_left", "shoulder_right", "hip_left", "hip_right",
)
ACTION_ROWS = (
    (0, "00_initial_still", "REGISTERED_INITIAL_QUANTILE_0"),
    (1, "02_t_pose", "REGISTERED_RESULT_INDEPENDENT_SAME_ACTION_QUANTILE_0P5"),
    (15, "16_squat", "GENERIC_ALL_NODE_GYRO_ENERGY_P90_FIRST_CROSSING"),
)
SUPPORT_COUNT = 16
LATIN_MULTIPLIERS = (1, 3, 5, 7, 11)


def _circular_center(value: np.ndarray) -> float:
    moment = np.mean(np.exp(1j * np.asarray(value, dtype=float)))
    return 0.0 if abs(moment) <= 32.0 * np.finfo(float).eps else float(np.angle(moment))


def _wrap_delta(value: float) -> float:
    return float((value + np.pi) % (2.0 * np.pi) - np.pi)


def _posterior_quantile(grid: np.ndarray, weights: np.ndarray, quantile: float) -> float:
    normalized = np.asarray(weights, dtype=float) / float(np.sum(weights))
    index = int(np.searchsorted(np.cumsum(normalized), quantile, side="left"))
    return float(np.asarray(grid, dtype=float)[min(index, len(grid) - 1)])


def _fixed_joint_s1_support(
    posterior: Mapping[str, np.ndarray], branch_id: str,
) -> tuple[list[dict[str, float]], Mapping[str, Any]]:
    arrays: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for edge in NONHINGE_EDGES:
        prefix = f"nonhinge_heading/15/{branch_id}/{edge}"
        grid = np.asarray(posterior[f"{prefix}/delta_grid_rad"], dtype=float)
        weights = np.asarray(posterior[f"{prefix}/posterior_weights"], dtype=float)
        arrays[edge] = (grid, weights)
    rows: list[dict[str, float]] = []
    for sample_index in range(SUPPORT_COUNT):
        rows.append({
            edge: _posterior_quantile(
                *arrays[edge],
                (((sample_index * multiplier) % SUPPORT_COUNT) + 0.5)
                / SUPPORT_COUNT,
            )
            for edge, multiplier in zip(
                NONHINGE_EDGES, LATIN_MULTIPLIERS, strict=True,
            )
        })
    return rows, {
        "rule": "FIXED_16_POINT_FACTORIZED_POSTERIOR_STRATIFIED_CIRCULAR_QUANTILE_SUPPORT",
        "sample_count": SUPPORT_COUNT,
        "latin_multipliers": list(LATIN_MULTIPLIERS),
        "result_or_pixel_selected": False,
        "full_cartesian_support_enumerated": False,
        "candidate_weight": 1.0 / SUPPORT_COUNT,
        "grid_sha256_by_edge": {
            edge: _array_sha(arrays[edge][0]) for edge in NONHINGE_EDGES
        },
        "posterior_weight_sha256_by_edge": {
            edge: _array_sha(arrays[edge][1]) for edge in NONHINGE_EDGES
        },
    }


def _apply_nonhinge_candidate(
    base_world: Mapping[str, np.ndarray],
    base_edge_delta: Mapping[str, np.ndarray],
    candidate: Mapping[str, float],
) -> tuple[dict[str, np.ndarray], Mapping[str, Any]]:
    from biospur_fusion.v0.c2_progressive.architecture_guard import ROOTED_EDGES
    from biospur_fusion.v0.c2_progressive.functional_geometry import EDGE_SPECS

    edge_name = {(parent, child): edge for edge, parent, child in EDGE_SPECS}
    edge_offset = {
        edge: _wrap_delta(float(candidate[edge]) - _circular_center(base_edge_delta[edge]))
        for edge in NONHINGE_EDGES
    }
    global_offset = {"pelvis": 0.0}
    for parent, child in ROOTED_EDGES:
        edge = edge_name[(parent, child)]
        global_offset[child] = global_offset[parent] + edge_offset.get(edge, 0.0)
    result = {}
    for segment, value in base_world.items():
        count = len(value)
        yaw = Rotation.from_rotvec(np.column_stack((
            np.zeros(count), np.zeros(count),
            np.full(count, global_offset[segment]),
        ))).as_matrix()
        result[segment] = np.einsum("nij,njk->nik", yaw, np.asarray(value))
    return result, {
        "edge_constant_offset_rad": edge_offset,
        "segment_global_constant_offset_rad": global_offset,
        "official_filtered_time_variation_replaced": False,
    }


def _rotate_candidate_covariance(
    base_covariance: Mapping[str, np.ndarray],
    segment_global_offset_rad: Mapping[str, float],
) -> dict[str, np.ndarray]:
    result = {}
    for segment, value in base_covariance.items():
        yaw = Rotation.from_rotvec(
            np.array([0.0, 0.0, float(segment_global_offset_rad[segment])])
        ).as_matrix()
        result[segment] = np.einsum(
            "ij,njk,lk->nil", yaw, np.asarray(value, dtype=float), yaw,
        )
    return result


def _generic_motion_row(
    *,
    source: Mapping[str, np.ndarray],
    settings: Mapping[str, Any],
    branch_id: str,
    branch_ids: tuple[str, ...],
    exact_root_rows: np.ndarray,
) -> tuple[int, Mapping[str, Any]]:
    from biospur_fusion.v0.c2_progressive.functional_geometry import EDGE_SPECS

    node_by_segment = {
        str(row["body_segment"]): str(row["hardware_id"])
        for row in settings["segment_frames"]["wear_authority"]["rows"]
    }
    pelvis_node = node_by_segment["pelvis"]
    pelvis_time_us = np.asarray(source[f"orientation/15/{pelvis_node}/time_us"], dtype=np.int64)
    root_rows, maps, map_audit = exact_maps._compose_exact_root_maps(
        source,
        chronological_index=15,
        selection_branch_id=branch_id,
        branch_ids=branch_ids,
        pelvis_time_us=pelvis_time_us,
        edge_specs=EDGE_SPECS,
        require_contiguous_root_rows=False,
    )
    if not np.array_equal(root_rows, exact_root_rows):
        raise RuntimeError("generic event rule exact-root rows differ from persisted state")
    energy_rows = []
    for segment, node in node_by_segment.items():
        source_rows = np.asarray(
            [maps[segment][int(row)] for row in root_rows], dtype=np.int64,
        )
        gyro = np.asarray(source[f"orientation/15/{node}/gyro_rads"], dtype=float)[source_rows]
        energy_rows.append(np.sum(gyro * gyro, axis=1))
    energy = np.median(np.vstack(energy_rows), axis=0)
    threshold = float(np.quantile(energy, 0.9, method="higher"))
    above = energy >= threshold
    crossings = np.flatnonzero(above & np.concatenate(([True], ~above[:-1])))
    if len(crossings) == 0:
        raise RuntimeError("generic gyro-energy p90 rule has no crossing")
    row = int(crossings[0])
    return row, {
        "rule": "GENERIC_ALL_NODE_MEDIAN_GYRO_NORM_SQUARED_ACTION_LOCAL_P90_FIRST_CROSSING",
        "result_or_action_label_pose_metric_used": False,
        "argmax_used": False,
        "threshold": threshold,
        "selected_local_row": row,
        "selected_root_source_row": int(root_rows[row]),
        "energy_sha256": _array_sha(energy),
        "exact_root_rows_sha256": _array_sha(root_rows),
        "map_audit": map_audit,
    }


def _knee_angles(world: Mapping[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    left = np.degrees(np.arccos(np.clip(np.einsum(
        "ni,ni->n", world["thigh_left"][:, :, 2], world["shank_left"][:, :, 2],
    ), -1.0, 1.0)))
    right = np.degrees(np.arccos(np.clip(np.einsum(
        "ni,ni->n", world["thigh_right"][:, :, 2], world["shank_right"][:, :, 2],
    ), -1.0, 1.0)))
    return left, right


def _geometry_metrics(
    *, settings: Mapping[str, Any], branch: Any,
    world_row: Mapping[str, np.ndarray],
) -> Mapping[str, Any]:
    from biospur_fusion.v0.c2_progressive.scientific_fk import (
        landmark_proxy_fk_points, landmark_proxy_sensitivity_profiles,
    )

    profiles = landmark_proxy_sensitivity_profiles(settings["anthropometric_proxy"])
    result = landmark_proxy_fk_points(
        root_sensor_position_m=np.zeros(3),
        world_from_segment=world_row,
        segment_from_sensor=branch.segment_from_sensor,
        connection_vectors_by_edge=branch.connection_vectors_by_edge,
        profile=profiles[0],
    )
    lines = viewer_helpers._line_map(result)
    points = np.vstack((
        *result.shared_joint_positions_m.values(),
        *result.distal_landmark_positions_m.values(),
    ))
    return {
        "displayed_link_length_m": {
            name: float(np.linalg.norm(value[1] - value[0]))
            for name, value in lines.items()
        },
        "shoulder_mid_to_hip_mid_m": float(
            np.linalg.norm(lines["spine"][1] - lines["spine"][0])
        ),
        "world_z_extent_m": float(np.ptp(points[:, 2])),
        "viewer_profile_id": str(profiles[0]["profile_id"]),
        "all_five_profile_ids_preserved": [str(row["profile_id"]) for row in profiles],
        "torso_surface_0p280_used_as_internal_spine_length": False,
        "functional_full_r3_connection_owner_used": True,
    }


def _geodesic_loss(first: Mapping[int, Mapping[str, np.ndarray]], second: Mapping[int, Mapping[str, np.ndarray]]) -> float:
    values = []
    for action_index in sorted(first):
        for segment in sorted(first[action_index]):
            relative = first[action_index][segment].T @ second[action_index][segment]
            values.append(float(np.dot(
                Rotation.from_matrix(relative).as_rotvec(),
                Rotation.from_matrix(relative).as_rotvec(),
            )))
    return float(np.mean(values))


def _render_candidate(
    *, settings: Mapping[str, Any], branch: Any,
    candidate_world: Mapping[int, Mapping[str, np.ndarray]],
    selection_audit: Mapping[int, Mapping[str, Any]],
    candidate_id: str,
) -> list[Mapping[str, Any]]:
    from biospur_fusion.v0.c2_progressive.scientific_fk import (
        landmark_proxy_fk_points, landmark_proxy_sensitivity_profiles,
    )

    profile = landmark_proxy_sensitivity_profiles(settings["anthropometric_proxy"])[0]
    renderer = settings["scientific_renderer"]
    views = (
        ("FRONT", tuple(renderer["front_axes"])),
        ("SIDE", tuple(renderer["side_axes"])),
        ("TOP", tuple(renderer["top_axes"])),
    )
    coordinate = {"x": 0, "y": 1, "z": 2}
    artifacts = []
    for action_index, action, _ in ACTION_ROWS:
        result = landmark_proxy_fk_points(
            root_sensor_position_m=np.zeros(3),
            world_from_segment=candidate_world[action_index],
            segment_from_sensor=branch.segment_from_sensor,
            connection_vectors_by_edge=branch.connection_vectors_by_edge,
            profile=profile,
        )
        lines = viewer_helpers._line_map(result)
        figure, axes = plt.subplots(1, 3, figsize=(22.5, 8.4), dpi=int(renderer["dpi"]))
        for axis, (view, (horizontal_name, vertical_name)) in zip(axes, views, strict=True):
            horizontal = coordinate[horizontal_name]
            vertical = coordinate[vertical_name]
            for line in lines.values():
                axis.plot(line[:, horizontal], line[:, vertical], color="#111827", linewidth=3.0)
            joints = np.vstack(list(result.shared_joint_positions_m.values()))
            distal = np.vstack(list(result.distal_landmark_positions_m.values()))
            axis.scatter(joints[:, horizontal], joints[:, vertical], color="#2563eb", s=30, zorder=3)
            axis.scatter(distal[:, horizontal], distal[:, vertical], color="#dc2626", marker="x", s=38, zorder=3)
            axis.set_xlim(*map(float, renderer["horizontal_limits_m"]))
            axis.set_ylim(*map(float, renderer["vertical_limits_m"]))
            axis.set_aspect("equal", adjustable="box")
            axis.grid(alpha=0.18)
            axis.set_title(view, fontsize=13, fontweight="bold")
            axis.set_xlabel(f"replay-world {horizontal_name} (m)")
            axis.set_ylabel(f"replay-world {vertical_name} (m)")
        causal_role = (
            "CAUSAL_PREFIX15" if action_index == 15
            else "RETROSPECTIVE_PREFIX15_VIEWER_ONLY_NOT_CAUSAL_METRIC_OR_POSE_TRUTH"
        )
        figure.suptitle(
            f"{action} — BOUNDED JOINT-S1 PHYSICAL-FIRST FRECHET REPRESENTATIVE\n"
            f"{causal_role} / NOT FULL-S1 QUALIFICATION / NOT SCIENCE PASS",
            color="#991b1b", fontsize=14, fontweight="bold",
        )
        figure.text(
            0.5, 0.025,
            "One deterministic 16-point posterior-support representative after physical gating; no MAP/argmax, pixel/action-label selection, IK, rebase, or repair.\n"
            f"candidate={candidate_id}; row rule={selection_audit[action_index]['rule']}; functional full-R3 core + RAW Observer-A viewer length profile; all profile provenance retained in AUDIT.json.",
            ha="center", va="bottom", fontsize=8.2,
        )
        figure.tight_layout(rect=(0.02, 0.10, 0.98, 0.90))
        path = OUT / f"{action_index:02d}_{action}_BOUNDED_S1_FRECHET_TRIVIEW.png"
        figure.savefig(path)
        plt.close(figure)
        pixels = plt.imread(path)
        path.chmod(0o444)
        artifacts.append({
            "action_index": action_index, "action": action,
            "path": str(path), "sha256": _sha(path),
            "pixel_dimensions": [int(pixels.shape[1]), int(pixels.shape[0])],
        })
    return artifacts


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE or OUT.exists():
        raise RuntimeError("bounded H1-H3 ablation requires canonical workspace and new output")
    OUT.mkdir(parents=True, exist_ok=False)
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))["effective_settings"]
    frozen = json.loads(FROZEN_MANIFEST.read_text(encoding="utf-8"))
    state_audit = json.loads(STATE_AUDIT.read_text(encoding="utf-8"))
    branch_ids = tuple(state_audit["moment_representative_physical_check_passed_hinge_branch_ids"])
    selected_branch_id = str(state_audit["selected_branch_id"])

    with np.load(SOURCE_NPZ, allow_pickle=False) as source, np.load(
        POSTERIOR_NPZ, allow_pickle=False,
    ) as posterior, np.load(STATE_NPZ, allow_pickle=False) as state:
        frozen_branches = _reconstruct_frame_branches(frozen=frozen, arrays=source)
        branch_map = {
            branch.branch_id: _branch_at_prefix(
                branch, source, f"physical_trajectory/15/{branch.branch_id}",
            )
            for branch in frozen_branches
        }
        base_world: dict[int, dict[str, dict[str, np.ndarray]]] = {}
        base_cov: dict[int, dict[str, dict[str, np.ndarray]]] = {}
        base_edge: dict[int, dict[str, dict[str, np.ndarray]]] = {}
        root_rows: dict[int, dict[str, np.ndarray]] = {}
        for action_index, _, _ in ACTION_ROWS:
            base_world[action_index] = {}
            base_cov[action_index] = {}
            base_edge[action_index] = {}
            root_rows[action_index] = {}
            for branch_id in branch_ids:
                base_world[action_index][branch_id] = {
                    segment: np.asarray(
                        state[f"world_from_segment/{action_index:02d}/{branch_id}/{segment}"],
                        dtype=float,
                    )
                    for segment in branch_map[branch_id].segment_from_sensor
                }
                base_cov[action_index][branch_id] = {
                    segment: np.asarray(
                        state[f"orientation_covariance/{action_index:02d}/{branch_id}/{segment}"],
                        dtype=float,
                    )
                    for segment in branch_map[branch_id].segment_from_sensor
                }
                base_edge[action_index][branch_id] = {
                    edge: np.asarray(
                        state[f"edge_delta_filt_rad/{action_index:02d}/{branch_id}/{edge}"],
                        dtype=float,
                    )
                    for edge in NONHINGE_EDGES
                }
                root_rows[action_index][branch_id] = np.asarray(
                    state[f"exact_root_source_rows/{action_index:02d}/{branch_id}"],
                    dtype=np.int64,
                )

        motion_row, motion_audit = _generic_motion_row(
            source=source, settings=settings,
            branch_id=selected_branch_id, branch_ids=branch_ids,
            exact_root_rows=root_rows[15][selected_branch_id],
        )
        selected_row = {
            0: 0,
            1: int(np.floor(0.5 * (len(root_rows[1][selected_branch_id]) - 1))),
            15: motion_row,
        }
        fixed_mid_row = int(np.floor(0.5 * (len(root_rows[15][selected_branch_id]) - 1)))
        left_knee, right_knee = _knee_angles(base_world[15][selected_branch_id])
        h3 = {
            "fixed_quantile_0p5": {
                "local_row": fixed_mid_row,
                "left_knee_longitudinal_separation_deg": float(left_knee[fixed_mid_row]),
                "right_knee_longitudinal_separation_deg": float(right_knee[fixed_mid_row]),
            },
            "generic_motion_event": {
                **motion_audit,
                "left_knee_longitudinal_separation_deg": float(left_knee[motion_row]),
                "right_knee_longitudinal_separation_deg": float(right_knee[motion_row]),
            },
            "generic_rule_predeclared_before_pixel_view": True,
        }

        h1 = {}
        for action_index, action, _ in ACTION_ROWS:
            branch = branch_map[selected_branch_id]
            h1[action] = _geometry_metrics(
                settings=settings, branch=branch,
                world_row={
                    segment: value[selected_row[action_index]]
                    for segment, value in base_world[action_index][selected_branch_id].items()
                },
            )

        support, support_audit = _fixed_joint_s1_support(posterior, selected_branch_id)
        frozen_ids = tuple(frozen["structure"]["frozen_evaluation_authority"]["branch_ids"])
        prefix15_weights = np.asarray(source["progressive_prefix/15/branch_weights"], dtype=float)
        support_rows = []
        legal_states: list[dict[str, Any]] = []
        for sample_index, candidate in enumerate(support):
            for branch_id in branch_ids:
                candidate_full: dict[int, dict[str, np.ndarray]] = {}
                legal_by_action = {}
                for action_index, action, _ in ACTION_ROWS:
                    candidate_trajectory, composition = _apply_nonhinge_candidate(
                        base_world[action_index][branch_id],
                        base_edge[action_index][branch_id], candidate,
                    )
                    candidate_covariance = _rotate_candidate_covariance(
                        base_cov[action_index][branch_id],
                        composition["segment_global_constant_offset_rad"],
                    )
                    assessment, _ = _physical_gate(
                        settings=settings,
                        branch=branch_map[branch_id],
                        world=candidate_trajectory,
                        covariance=candidate_covariance,
                        selected_row=selected_row[action_index],
                        action_index=action_index,
                        action=action,
                        trajectory_report={
                            "schema": "biospur-c2-bounded-joint-s1-hypothesis-trajectory-v1",
                            "tree_semantics": (
                                "child_global = parent_global + time_varying_edge_deltaFilt"
                            ),
                            "sample_index": sample_index,
                            "candidate_sha256": hashlib.sha256(json.dumps(
                                candidate, sort_keys=True, separators=(",", ":"),
                            ).encode()).hexdigest(),
                            "composition": composition,
                            "full_s1_cartesian_qualification": False,
                        },
                    )
                    legal_by_action[action_index] = bool(assessment.physically_legal)
                    candidate_full[action_index] = {
                        segment: value[selected_row[action_index]].copy()
                        for segment, value in candidate_trajectory.items()
                    }
                hinge_weight = float(prefix15_weights[frozen_ids.index(branch_id)])
                row = {
                    "sample_index": sample_index,
                    "branch_id": branch_id,
                    "factorized_sample_weight_times_prefix15_hinge_weight": (
                        hinge_weight / SUPPORT_COUNT
                    ),
                    "physical_legal_by_action": legal_by_action,
                    "physical_legal_all_three": all(legal_by_action.values()),
                    "candidate_coordinate_rad": candidate,
                }
                support_rows.append(row)
                if row["physical_legal_all_three"] and row[
                    "factorized_sample_weight_times_prefix15_hinge_weight"
                ] > 0.0:
                    legal_states.append({**row, "world": candidate_full})

        representative = None
        artifacts: list[Mapping[str, Any]] = []
        if legal_states:
            total = float(sum(
                row["factorized_sample_weight_times_prefix15_hinge_weight"]
                for row in legal_states
            ))
            weights = np.asarray([
                row["factorized_sample_weight_times_prefix15_hinge_weight"] / total
                for row in legal_states
            ])
            expected_loss = np.asarray([
                sum(
                    weight * _geodesic_loss(candidate["world"], other["world"])
                    for weight, other in zip(weights, legal_states, strict=True)
                )
                for candidate in legal_states
            ])
            representative_index = int(np.flatnonzero(
                expected_loss == np.min(expected_loss)
            )[0])
            chosen = legal_states[representative_index]
            representative = {
                "rule": "PHYSICAL_FIRST_FIXED_SUPPORT_POSTERIOR_FRECHET_MEDOID",
                "sample_index": int(chosen["sample_index"]),
                "branch_id": str(chosen["branch_id"]),
                "expected_mean_squared_so3_geodesic_loss": float(
                    expected_loss[representative_index]
                ),
                "legal_support_count": len(legal_states),
                "legal_support_posterior_mass_before_renormalization": total,
                "hard_map_or_argmax_heading_used": False,
                "pixel_or_action_label_selected": False,
                "full_s1_qualified": False,
            }
            selection_audit = {
                0: {"rule": "REGISTERED_INITIAL_QUANTILE_0"},
                1: {"rule": "REGISTERED_RESULT_INDEPENDENT_SAME_ACTION_QUANTILE_0P5"},
                15: motion_audit,
            }
            artifacts = _render_candidate(
                settings=settings,
                branch=branch_map[str(chosen["branch_id"])],
                candidate_world=chosen["world"],
                selection_audit=selection_audit,
                candidate_id=(
                    f"S1_{chosen['sample_index']:02d}__{chosen['branch_id']}"
                ),
            )

        qmt_zero = {edge: 0.0 for edge in NONHINGE_EDGES}
        qmt_zero_world, _ = _apply_nonhinge_candidate(
            base_world[1][selected_branch_id], base_edge[1][selected_branch_id], qmt_zero,
        )
        h2 = {
            "hypothesis": "weak S1 moment collapse versus carried-zero QMT and bounded physical S1 support",
            "moment_resultant_range_prefix15": state_audit["action_owner_audits"][15]["nonhinge_resultant_range"],
            "qmt_only_carried_zero_diagnostic_world_sha256": {
                segment: _array_sha(value) for segment, value in qmt_zero_world.items()
            },
            "bounded_support": support_audit,
            "bounded_support_rows": support_rows,
            "representative": representative,
            "full_s1_cartesian_physical_qualification": False,
        }

        provenance = {
            "same_prefix15_frame_sha256_by_branch_segment": {
                branch_id: {
                    segment: _array_sha(value)
                    for segment, value in branch_map[branch_id].segment_from_sensor.items()
                }
                for branch_id in branch_ids
            },
            "selected_branch_world_sha256_by_action_segment": {
                f"{action_index:02d}": {
                    segment: _array_sha(value)
                    for segment, value in base_world[action_index][selected_branch_id].items()
                }
                for action_index, _, _ in ACTION_ROWS
            },
            "selected_branch_nonhinge_trace_sha256_by_action_edge": {
                f"{action_index:02d}": {
                    edge: _array_sha(value)
                    for edge, value in base_edge[action_index][selected_branch_id].items()
                }
                for action_index, _, _ in ACTION_ROWS
            },
        }

    audit = {
        "schema": "biospur-c2-nonhinge-s1-qmt-rooted-h1-h2-h3-ablation-v1",
        "created_local": datetime.now().astimezone().isoformat(),
        "inputs": {
            "derived_state_npz": {"path": str(STATE_NPZ), "sha256": _sha(STATE_NPZ)},
            "derived_state_audit": {"path": str(STATE_AUDIT), "sha256": _sha(STATE_AUDIT)},
            "source_replay_npz": {"path": str(SOURCE_NPZ), "sha256": _sha(SOURCE_NPZ)},
            "replay002_posterior_npz": {"path": str(POSTERIOR_NPZ), "sha256": _sha(POSTERIOR_NPZ)},
        },
        "hypotheses_predeclared_before_ablation": [
            "H1_FUNCTIONAL_MEAN_TORSO_SHOULDER_HIP_ANCHORS_COMPRESS_SPINE",
            "H2_WEAK_S1_MOMENT_COLLAPSE_CAUSES_ASYMMETRY_VERSUS_BOUNDED_PHYSICAL_SUPPORT",
            "H3_FIXED_QUANTILE_0P5_MISSES_MOTION_VERSUS_GENERIC_TIME_EVIDENCE_RULE",
        ],
        "h1_geometry": h1,
        "h2_heading": h2,
        "h3_time": h3,
        "same_node_frame_heading_provenance": provenance,
        "artifacts": artifacts,
        "artifact_count": len(artifacts),
        "payload_reread": False,
        "raw_fit_or_qmt_replay_rerun": False,
        "heldout_opened": False,
        "inverse_kinematics": False,
        "rebase": False,
        "repair": False,
        "pixel_or_action_label_selection": False,
        "scientific_acceptance_pass": False,
        "tuned_pose_pass": False,
    }
    _write_json(OUT / "AUDIT.json", audit)
    print(json.dumps({
        "audit": str(OUT / "AUDIT.json"),
        "audit_sha256": _sha(OUT / "AUDIT.json"),
        "artifact_count": len(artifacts),
    }), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
