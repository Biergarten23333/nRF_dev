#!/usr/bin/env python3
"""Audit saved C2 frame/QMT boundaries without rendering or refitting."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

sys.dont_write_bytecode = True

import numpy as np
from scipy.spatial.transform import Rotation

from replay_c2_postfreeze_heading import _reconstruct_frame_branches
from render_c2_fresh_continuous_squat import _compose_exact_root_maps
from render_c2_nonhinge_s1_qmt_rooted_debug import _branch_at_prefix


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
SPRINT = RUN / "CONTINUATION_SPRINT"
SETTINGS = RUN / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
FROZEN_MANIFEST = RUN / "C2_REAL_DIAGNOSTIC_FROZEN_STATE_013.json"
FRESH_STATE = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.npz"
SOURCE_STATE = SPRINT / "C2_NONHINGE_TRAINING_REPLAY_001/POSTFREEZE_RETROSPECTIVE_QMT_STATE.npz"
POSTERIOR_STATE = SPRINT / "C2_NONHINGE_JOINT_RAO_REPLAY_002/CORRECTED_PREFIX_NONHINGE_STATE.npz"
DEBUG_DIR = SPRINT / "C2_NONHINGE_S1_QMT_ROOTED_DEBUG_004"
DEBUG_STATE = DEBUG_DIR / "DERIVED_QMT_ROOTED_STATE.npz"
DEBUG_AUDIT = DEBUG_DIR / "AUDIT.json"
CLOSURE_AUDIT = SPRINT / "C2_FIXED_LANDMARK_PROXY_SPINE_OWNER_CLOSURE_001/AUDIT.json"
OUT = SPRINT / "C2_SAVED_ARRAY_FRAME_HEADING_PHYSICAL_BOUNDARY_AUDIT_001"

ACTION_NAMES = {
    0: "00_initial_still",
    1: "02_t_pose",
    15: "16_squat",
}
HINGE_ENDPOINTS = {
    "elbow_left": ("upper_arm_left", "forearm_left"),
    "elbow_right": ("upper_arm_right", "forearm_right"),
    "knee_left": ("thigh_left", "shank_left"),
    "knee_right": ("thigh_right", "shank_right"),
}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _array_sha(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    return hashlib.sha256(array.view(np.uint8)).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return value


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(_jsonable(value), handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def _summary(value: np.ndarray) -> Mapping[str, Any]:
    array = np.asarray(value, dtype=float)
    return {
        "minimum": float(np.min(array)),
        "q10": float(np.quantile(array, 0.1)),
        "q50": float(np.quantile(array, 0.5)),
        "q90": float(np.quantile(array, 0.9)),
        "maximum": float(np.max(array)),
        "mean": float(np.mean(array)),
        "sha256": _array_sha(array),
    }


def _longitudinal_separation_deg(
    parent_world_from_segment: np.ndarray,
    child_world_from_segment: np.ndarray,
) -> np.ndarray:
    return np.degrees(np.arccos(np.clip(np.einsum(
        "ni,ni->n",
        np.asarray(parent_world_from_segment)[:, :, 2],
        np.asarray(child_world_from_segment)[:, :, 2],
    ), -1.0, 1.0)))


def _signed_hinge_angle_deg(
    parent_world_from_segment: np.ndarray,
    child_world_from_segment: np.ndarray,
) -> np.ndarray:
    parent = np.asarray(parent_world_from_segment, dtype=float)
    child = np.asarray(child_world_from_segment, dtype=float)
    hinge = parent[:, :, 1]
    parent_long = parent[:, :, 2]
    child_long = child[:, :, 2]
    return np.degrees(np.arctan2(
        np.einsum("ni,ni->n", hinge, np.cross(child_long, parent_long)),
        np.einsum("ni,ni->n", child_long, parent_long),
    ))


def _yaw_decomposition(
    corrected_world_from_segment: np.ndarray,
    uncorrected_world_from_segment: np.ndarray,
) -> Mapping[str, Any]:
    correction = np.einsum(
        "nij,nkj->nik",
        np.asarray(corrected_world_from_segment, dtype=float),
        np.asarray(uncorrected_world_from_segment, dtype=float),
    )
    rotvec = Rotation.from_matrix(correction).as_rotvec()
    return {
        "world_z_rotation_rad": _summary(rotvec[:, 2]),
        "transverse_rotation_norm_rad": _summary(np.linalg.norm(rotvec[:, :2], axis=1)),
    }


def _branch_sign(branch_id: str, edge: str) -> str:
    marker = f"{edge}:"
    suffix = branch_id.split(marker, 1)[1]
    return suffix.split("_", 1)[0]


def _world_stages(
    *,
    action_index: int,
    branch: Any,
    branch_ids: tuple[str, ...],
    settings: Mapping[str, Any],
    source: Mapping[str, np.ndarray],
    debug: Mapping[str, np.ndarray],
) -> tuple[np.ndarray, Mapping[str, Mapping[str, np.ndarray]], Mapping[str, Any]]:
    from biospur_fusion.v0.c2_progressive.functional_geometry import EDGE_SPECS
    from biospur_fusion.v0.c2_progressive.quaternion_contract import qmt_wxyz_to_scipy_active

    node_by_segment = {
        str(row["body_segment"]): str(row["hardware_id"])
        for row in settings["segment_frames"]["wear_authority"]["rows"]
    }
    pelvis_time_us = np.asarray(
        source[f"orientation/{action_index:02d}/{node_by_segment['pelvis']}/time_us"],
        dtype=np.int64,
    )
    root_rows, segment_maps, map_audit = _compose_exact_root_maps(
        source,
        chronological_index=action_index,
        selection_branch_id=branch.branch_id,
        branch_ids=branch_ids,
        pelvis_time_us=pelvis_time_us,
        edge_specs=EDGE_SPECS,
        require_contiguous_root_rows=False,
    )
    persisted_root_rows = np.asarray(
        debug[f"exact_root_source_rows/{action_index:02d}/{branch.branch_id}"],
        dtype=np.int64,
    )
    if not np.array_equal(root_rows, persisted_root_rows):
        raise RuntimeError("recomposed exact root rows differ from DEBUG_004")

    raw_sensor: dict[str, np.ndarray] = {}
    frame_only: dict[str, np.ndarray] = {}
    qmt_rooted: dict[str, np.ndarray] = {}
    for segment, node in node_by_segment.items():
        source_rows = np.asarray(
            [segment_maps[segment][int(row)] for row in root_rows], dtype=np.int64,
        )
        world_from_sensor = qmt_wxyz_to_scipy_active(np.asarray(
            source[f"orientation/{action_index:02d}/{node}/quat_world_sensor_wxyz"],
            dtype=float,
        )[source_rows]).as_matrix()
        raw_sensor[segment] = world_from_sensor
        frame_only[segment] = np.einsum(
            "nij,jk->nik", world_from_sensor,
            np.asarray(branch.sensor_from_segment[segment], dtype=float),
        )
        qmt_rooted[segment] = np.asarray(
            debug[f"world_from_segment/{action_index:02d}/{branch.branch_id}/{segment}"],
            dtype=float,
        )
        if len(qmt_rooted[segment]) != len(root_rows):
            raise RuntimeError("DEBUG_004 world rows differ from exact rooted rows")
    return root_rows, {
        "raw_world_from_sensor": raw_sensor,
        "frame_only_world_from_segment": frame_only,
        "qmt_rooted_world_from_segment": qmt_rooted,
    }, map_audit


def _hinge_axis_audit(
    *,
    branch: Any,
    source: Mapping[str, np.ndarray],
    edge: str,
) -> Mapping[str, Any]:
    parent, child = HINGE_ENDPOINTS[edge]
    output: dict[str, Any] = {}
    for endpoint, segment in (("parent", parent), ("child", child)):
        sensor_axis = np.asarray(
            source[f"geometry_checkpoint/15/axis/{edge}/{endpoint}"], dtype=float,
        )
        segment_axis = np.asarray(branch.segment_from_sensor[segment], dtype=float) @ sensor_axis
        output[endpoint] = {
            "segment": segment,
            "sensor_frame_axis": sensor_axis,
            "segment_frame_axis": segment_axis,
            "segment_frame_axis_sha256": _array_sha(segment_axis),
            "absolute_dot_with_qmt_positive_y": float(abs(segment_axis[1])),
            "angular_deviation_from_unsigned_qmt_y_deg": float(np.degrees(np.arccos(
                np.clip(abs(segment_axis[1]), -1.0, 1.0),
            ))),
        }
    return {
        "edge": edge,
        "retained_branch_sign": _branch_sign(branch.branch_id, edge),
        "official_qmt_joint_coordinate": [0.0, 1.0, 0.0],
        "axis": output,
    }


def _trajectory_assessment_summary(assessment: Any) -> Mapping[str, Any]:
    report = assessment.report
    topology = Counter(row["status"] for row in report["topology_samples"])
    gravity = report["gravity_evidence"]
    knee = report["bilateral_knee_evidence"]
    return {
        "branch_id": assessment.branch_id,
        "physically_legal": bool(assessment.physically_legal),
        "hard_rejection_codes": list(report["hard_rejection_codes"]),
        "soft_total_log_likelihood": float(assessment.soft_total_log_likelihood),
        "rom_log_likelihood": float(assessment.rom_log_likelihood),
        "bilateral_log_likelihood": float(assessment.bilateral_log_likelihood),
        "gravity_log_likelihood": float(assessment.gravity_log_likelihood),
        "topology_status_counts": dict(sorted(topology.items())),
        "bilateral_confirmed_fraction_by_pair": report[
            "bilateral_crossing_or_mirror_evidence"
        ]["gross_uncertainty_confirmed_fraction_by_pair"],
        "pelvis_gravity_wrong_fraction": gravity["pelvis"]["confirmed_gross_wrong_fraction"],
        "torso_gravity_wrong_fraction": gravity["torso"]["confirmed_gross_wrong_fraction"],
        "opposite_informed_knee_fraction": knee["opposite_informed_fraction"],
        "left_signed_knee_flexion_deg": _summary(np.degrees(np.asarray(
            knee["left_signed_flexion_rad"], dtype=float,
        ))),
        "right_signed_knee_flexion_deg": _summary(np.degrees(np.asarray(
            knee["right_signed_flexion_rad"], dtype=float,
        ))),
    }


def _whole_trajectory_gate(
    *,
    settings: Mapping[str, Any],
    branch: Any,
    action_index: int,
    action: str,
    world: Mapping[str, np.ndarray],
    covariance: Mapping[str, np.ndarray],
    debug_state_sha256: str,
) -> Mapping[str, Any]:
    from biospur_fusion.v0.c2_progressive.architecture_guard import C2ExecutionGuard
    from biospur_fusion.v0.c2_progressive.scientific_fk import (
        ScientificForwardKinematicsOwner,
        physical_input_binding_token,
    )

    guard = C2ExecutionGuard(settings)
    guard.begin_capture("C2")
    secret = b"c2-saved-array-full-trajectory-physical-audit"
    owner_id = "C2_SAVED_ARRAY_FULL_TRAJECTORY_PHYSICAL_AUDIT_OWNER"
    owner = ScientificForwardKinematicsOwner(
        execution_guard=guard,
        physical_settings=settings["physical_candidates"],
        expected_runtime_owner_id=owner_id,
        runtime_binding_secret=secret,
    )
    binding = {
        "schema": "biospur-c2-runtime-owned-physical-prefix-input-v1",
        "runtime_owner_id": owner_id,
        "branch_id": branch.branch_id,
        "chronological_index": action_index,
        "action": action,
        "source": "OFFICIAL_QMT_ROOTED_PARENT_PLUS_CHILD_WITH_HASH_BOUND_NONHINGE_S1_PRIOR_TRAINING_ONLY",
        "raw_unqmt_orientation_allowed_to_drive_physical_gate": False,
        "qmt_branch_evidence_ingested_before_physical_gate": False,
        "rooted_qmt_trajectory_report": {
            "tree_semantics": "child_global = parent_global + time_varying_edge_deltaFilt",
            "saved_derived_state_sha256": debug_state_sha256,
            "all_exact_root_rows_consumed": True,
        },
        "world_from_segment_sha256": {
            segment: _array_sha(value) for segment, value in world.items()
        },
        "orientation_covariance_sha256": {
            segment: _array_sha(value) for segment, value in covariance.items()
        },
        "future_episode_or_caller_pose_truth_used": False,
    }
    payload_sha, token = physical_input_binding_token(secret, binding)
    binding.update({
        "runtime_owner_binding_payload_sha256": payload_sha,
        "runtime_owner_token": token,
    })
    assessment = owner.assess_prefix_trajectory(
        frame_branch=branch,
        world_from_segment_trajectory=world,
        orientation_tangent_covariance_rad2=covariance,
        owner_input_binding=binding,
    )
    return {
        "all_exact_root_rows_consumed": True,
        "row_count": int(len(next(iter(world.values())))),
        "owner_binding_payload_sha256": payload_sha,
        "assessment": _trajectory_assessment_summary(assessment),
    }


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE or OUT.exists():
        raise RuntimeError("saved-array boundary audit requires canonical workspace/new output")
    OUT.mkdir(parents=True, exist_ok=False)
    settings_document = json.loads(SETTINGS.read_text(encoding="utf-8"))
    settings = settings_document["effective_settings"]
    frozen = json.loads(FROZEN_MANIFEST.read_text(encoding="utf-8"))
    debug_audit = json.loads(DEBUG_AUDIT.read_text(encoding="utf-8"))
    closure = json.loads(CLOSURE_AUDIT.read_text(encoding="utf-8"))
    if (
        closure["fixed_viewer_spine_owner_closure"]
        ["registered_three_dimensional_graphical_spine_vector_exists"] is not False
        or closure["artifact_count"] != 0
    ):
        raise RuntimeError("graphical-spine closure no longer guards pixels")

    debug_sha = _sha(DEBUG_STATE)
    with np.load(SOURCE_STATE, allow_pickle=False) as source, np.load(
        DEBUG_STATE, allow_pickle=False,
    ) as debug, np.load(POSTERIOR_STATE, allow_pickle=False) as posterior:
        terminal_branches = _reconstruct_frame_branches(frozen=frozen, arrays=source)
        prefix15_branches = tuple(
            _branch_at_prefix(
                branch, source, f"physical_trajectory/15/{branch.branch_id}",
            )
            for branch in terminal_branches
        )
        branch_ids = tuple(branch.branch_id for branch in prefix15_branches)

        frame_ownership: dict[str, Any] = {}
        for terminal, prefix15 in zip(terminal_branches, prefix15_branches, strict=True):
            differences = {
                segment: float(np.max(np.abs(
                    np.asarray(terminal.segment_from_sensor[segment])
                    - np.asarray(prefix15.segment_from_sensor[segment])
                )))
                for segment in terminal.segment_from_sensor
            }
            frame_ownership[terminal.branch_id] = {
                "terminal_vs_prefix15_max_abs_by_segment": differences,
                "terminal_and_prefix15_are_identical": all(
                    value <= 1e-12 for value in differences.values()
                ),
            }

        stages_by_action_branch: dict[int, dict[str, Mapping[str, Mapping[str, np.ndarray]]]] = {}
        covariance_by_action_branch: dict[int, dict[str, Mapping[str, np.ndarray]]] = {}
        map_audits: dict[int, dict[str, Any]] = {}
        for action_index in ACTION_NAMES:
            stages_by_action_branch[action_index] = {}
            covariance_by_action_branch[action_index] = {}
            for branch in prefix15_branches:
                root_rows, stages, map_audit = _world_stages(
                    action_index=action_index,
                    branch=branch,
                    branch_ids=branch_ids,
                    settings=settings,
                    source=source,
                    debug=debug,
                )
                stages_by_action_branch[action_index][branch.branch_id] = stages
                covariance_by_action_branch[action_index][branch.branch_id] = {
                    segment: np.asarray(
                        debug[
                            f"orientation_covariance/{action_index:02d}/"
                            f"{branch.branch_id}/{segment}"
                        ], dtype=float,
                    )
                    for segment in stages["qmt_rooted_world_from_segment"]
                }
                map_audits.setdefault(action_index, {})[branch.branch_id] = {
                    "exact_root_row_count": int(len(root_rows)),
                    "exact_root_rows_sha256": _array_sha(root_rows),
                    "map_audit": map_audit,
                }

        squat: dict[str, Any] = {}
        for branch in prefix15_branches:
            stages = stages_by_action_branch[15][branch.branch_id]
            by_stage = {}
            for stage_name, rotations in stages.items():
                by_stage[stage_name] = {}
                for edge in ("knee_left", "knee_right"):
                    parent, child = HINGE_ENDPOINTS[edge]
                    by_stage[stage_name][edge] = {
                        "unsigned_longitudinal_separation_deg": _summary(
                            _longitudinal_separation_deg(rotations[parent], rotations[child])
                        ),
                        "signed_hinge_angle_deg": _summary(
                            _signed_hinge_angle_deg(rotations[parent], rotations[child])
                        ),
                    }
            qmt_impact = {
                segment: _yaw_decomposition(
                    stages["qmt_rooted_world_from_segment"][segment],
                    stages["frame_only_world_from_segment"][segment],
                )
                for segment in ("thigh_left", "shank_left", "thigh_right", "shank_right")
            }
            edge_delta = {
                edge: _summary(np.asarray(
                    debug[f"edge_delta_filt_rad/15/{branch.branch_id}/{edge}"],
                    dtype=float,
                ))
                for edge in ("knee_left", "knee_right")
            }
            squat[branch.branch_id] = {
                "retained_knee_signs": {
                    edge: _branch_sign(branch.branch_id, edge)
                    for edge in ("knee_left", "knee_right")
                },
                "hinge_axis_ownership": {
                    edge: _hinge_axis_audit(branch=branch, source=source, edge=edge)
                    for edge in ("knee_left", "knee_right")
                },
                "angles_by_owner_stage": by_stage,
                "qmt_world_yaw_impact_by_segment": qmt_impact,
                "edge_delta_filt_rad": edge_delta,
            }

        upper_limb: dict[str, Any] = {}
        for action_index in (0, 1):
            upper_limb[str(action_index)] = {}
            for branch in prefix15_branches:
                stages = stages_by_action_branch[action_index][branch.branch_id]
                by_stage: dict[str, Any] = {}
                for stage_name, rotations in stages.items():
                    by_stage[stage_name] = {}
                    for edge in ("elbow_left", "elbow_right"):
                        parent, child = HINGE_ENDPOINTS[edge]
                        by_stage[stage_name][edge] = {
                            "unsigned_longitudinal_separation_deg": _summary(
                                _longitudinal_separation_deg(rotations[parent], rotations[child])
                            ),
                            "signed_hinge_angle_deg": _summary(
                                _signed_hinge_angle_deg(rotations[parent], rotations[child])
                            ),
                        }
                    for edge, parent, child in (
                        ("shoulder_left", "torso", "upper_arm_left"),
                        ("shoulder_right", "torso", "upper_arm_right"),
                    ):
                        by_stage[stage_name][edge] = {
                            "unsigned_longitudinal_separation_deg": _summary(
                                _longitudinal_separation_deg(rotations[parent], rotations[child])
                            ),
                        }
                heading_trace = {
                    edge: {
                        "delta_filt_rad": _summary(np.asarray(
                            debug[
                                f"edge_delta_filt_rad/{action_index:02d}/"
                                f"{branch.branch_id}/{edge}"
                            ], dtype=float,
                        )),
                        "variance_rad2": _summary(np.asarray(
                            debug[
                                f"edge_variance_rad2/{action_index:02d}/"
                                f"{branch.branch_id}/{edge}"
                            ], dtype=float,
                        )),
                    }
                    for edge in ("shoulder_left", "shoulder_right", "elbow_left", "elbow_right")
                }
                posterior_resultants = {}
                for edge in ("shoulder_left", "shoulder_right"):
                    prefix = f"nonhinge_heading/{action_index:02d}/{branch.branch_id}/{edge}"
                    grid = np.asarray(posterior[f"{prefix}/delta_grid_rad"], dtype=float)
                    weights = np.asarray(posterior[f"{prefix}/posterior_weights"], dtype=float)
                    resultant = abs(np.sum(weights * np.exp(1j * grid)))
                    posterior_resultants[edge] = {
                        "resultant": float(resultant),
                        "grid_sha256": _array_sha(grid),
                        "weights_sha256": _array_sha(weights),
                    }
                upper_limb[str(action_index)][branch.branch_id] = {
                    "role": (
                        "FINAL_PREFIX15_FRAME_AND_EXTERNAL_POSTERIOR_RETROSPECTIVE_VIEWER_DIAGNOSTIC"
                    ),
                    "causal_progress_metric": False,
                    "retained_elbow_signs": {
                        edge: _branch_sign(branch.branch_id, edge)
                        for edge in ("elbow_left", "elbow_right")
                    },
                    "hinge_axis_ownership": {
                        edge: _hinge_axis_audit(branch=branch, source=source, edge=edge)
                        for edge in ("elbow_left", "elbow_right")
                    },
                    "angles_by_owner_stage": by_stage,
                    "heading_trace": heading_trace,
                    "prefix_local_nonhinge_posterior_resultant": posterior_resultants,
                }

        whole_trajectory_gate: dict[str, Any] = {}
        for action_index, action in ACTION_NAMES.items():
            whole_trajectory_gate[str(action_index)] = {}
            for branch in prefix15_branches:
                world = stages_by_action_branch[action_index][branch.branch_id][
                    "qmt_rooted_world_from_segment"
                ]
                covariance = covariance_by_action_branch[action_index][branch.branch_id]
                whole_trajectory_gate[str(action_index)][branch.branch_id] = _whole_trajectory_gate(
                    settings=settings,
                    branch=branch,
                    action_index=action_index,
                    action=action,
                    world=world,
                    covariance=covariance,
                    debug_state_sha256=debug_sha,
                )
                print(json.dumps({
                    "action_index": action_index,
                    "branch_id": branch.branch_id,
                    "all_rows_physical_gate_complete": True,
                }), flush=True)

    knee_sign_pairs = {
        (
            _branch_sign(branch_id, "knee_left"),
            _branch_sign(branch_id, "knee_right"),
        )
        for branch_id in branch_ids
    }
    knee_trace_hash_pairs = {
        (
            row["angles_by_owner_stage"]["qmt_rooted_world_from_segment"]["knee_left"]
            ["unsigned_longitudinal_separation_deg"]["sha256"],
            row["angles_by_owner_stage"]["qmt_rooted_world_from_segment"]["knee_right"]
            ["unsigned_longitudinal_separation_deg"]["sha256"],
        )
        for row in squat.values()
    }
    audit = {
        "schema": "biospur-c2-saved-array-frame-heading-physical-boundary-audit-v1",
        "created_local": datetime.now().astimezone().isoformat(),
        "inputs": {
            "settings": {"path": str(SETTINGS), "sha256": _sha(SETTINGS)},
            "frozen_manifest": {"path": str(FROZEN_MANIFEST), "sha256": _sha(FROZEN_MANIFEST)},
            "fresh_state": {"path": str(FRESH_STATE), "sha256": _sha(FRESH_STATE)},
            "training_replay_state": {"path": str(SOURCE_STATE), "sha256": _sha(SOURCE_STATE)},
            "joint_rao_posterior": {"path": str(POSTERIOR_STATE), "sha256": _sha(POSTERIOR_STATE)},
            "debug004_state": {"path": str(DEBUG_STATE), "sha256": debug_sha},
            "debug004_audit": {"path": str(DEBUG_AUDIT), "sha256": _sha(DEBUG_AUDIT)},
            "graphical_spine_closure": {"path": str(CLOSURE_AUDIT), "sha256": _sha(CLOSURE_AUDIT)},
        },
        "frame_ownership": {
            "debug004_uses_one_prefix15_frame_history": debug_audit[
                "one_coherent_segment_frame_history"
            ],
            "terminal_frames_differ_from_prefix15_for_at_least_one_segment": any(
                not row["terminal_and_prefix15_are_identical"]
                for row in frame_ownership.values()
            ),
            "by_branch": frame_ownership,
        },
        "bilateral_squat_asymmetry": {
            "by_branch": squat,
            "retained_knee_sign_pair_count": len(knee_sign_pairs),
            "retained_knee_sign_pairs": sorted([list(row) for row in knee_sign_pairs]),
            "qmt_rooted_knee_trace_pair_count": len(knee_trace_hash_pairs),
            "all_four_retained_branches_have_identical_knee_traces": len(knee_trace_hash_pairs) == 1,
            "branch_choice_can_explain_left_right_asymmetry": False,
        },
        "initial_and_t_upper_limb": upper_limb,
        "whole_trajectory_physical_gate": {
            "selected_frame_only_gate_used": False,
            "all_exact_root_rows_consumed": True,
            "by_action_and_branch": whole_trajectory_gate,
            "gate_geometry_role": "FULL_R3_SENSOR_ORIGIN_AND_SHARED_JOINT_SCIENTIFIC_FK_DIAGNOSTIC",
            "fixed_landmark_proxy_viewer_geometry_qualified_by_this_gate": False,
            "distal_longitudinal_axis_qualified_by_this_gate": False,
        },
        "map_bindings": map_audits,
        "viewer_guard": {
            "graphical_spine_mapping_exists": False,
            "viewer_called": False,
            "new_pixels": 0,
            "surface_scalar_as_segment_axis_used": False,
        },
        "payload_reread": False,
        "fit_or_qmt_replay_rerun": False,
        "heldout_opened": False,
        "scientific_acceptance_pass": False,
        "tuned_pose_pass": False,
    }
    _write_new_json(OUT / "AUDIT.json", audit)
    print(json.dumps({
        "audit": str(OUT / "AUDIT.json"),
        "audit_sha256": _sha(OUT / "AUDIT.json"),
        "new_pixels": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
