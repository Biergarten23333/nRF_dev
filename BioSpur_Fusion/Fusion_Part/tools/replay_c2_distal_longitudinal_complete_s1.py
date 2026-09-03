#!/usr/bin/env python3
"""Evaluate the unresolved distal longitudinal gauge on saved C2 arrays.

This is a training-only, derived calibration diagnostic.  It does not rerun
QMT, fitting, progressive state, physical ranking, or the viewer.  The exact
gap-safe pair rows are used to evaluate complete-motion residuals, while the
candidate coordinate is the complete S1 orthogonal to each signed hinge axis.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

sys.dont_write_bytecode = True

import numpy as np

from biospur_fusion.v0.c2_progressive.distal_longitudinal import (
    CompleteS1DistalLongitudinalOwner,
    complete_s1_sensor_from_segment_candidates,
    complete_s1_prior_binding,
    recompute_distal_motion_through_candidate_coordinates,
)
from biospur_fusion.v0.c2_progressive.functional_geometry import _center_terms
from biospur_fusion.v0.c2_progressive.quaternion_contract import (
    qmt_wxyz_to_scipy_active,
)
from biospur_fusion.v0.c2_progressive.segment_frames import (
    _soft_full_support_angular_log_likelihood,
    _validated_wear_authority,
)


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
SPRINT = RUN / "CONTINUATION_SPRINT"
SETTINGS = RUN / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
SOURCE = SPRINT / "C2_NONHINGE_TRAINING_REPLAY_001/POSTFREEZE_RETROSPECTIVE_QMT_STATE.npz"
SOURCE_MANIFEST = SPRINT / "C2_NONHINGE_TRAINING_REPLAY_001/POSTFREEZE_RETROSPECTIVE_QMT_STATE.json"
GRID_SOURCE = SPRINT / "C2_NONHINGE_JOINT_RAO_REPLAY_002/CORRECTED_PREFIX_NONHINGE_STATE.npz"
PARENT_ABLATION = SPRINT / "C2_SAVED_ARRAY_SEGMENT_FRAME_QMT_ABLATION_001/AUDIT.json"
FAILED_ATTEMPT = SPRINT / "C2_DISTAL_LONGITUDINAL_COMPLETE_S1_REPLAY_001/FAILURE.json"
OUT = SPRINT / "C2_DISTAL_LONGITUDINAL_COMPLETE_S1_REPLAY_002"

EDGE_ROWS = {
    "forearm_left": ("elbow_left", "upper_arm_left", "forearm_left", "child"),
    "forearm_right": ("elbow_right", "upper_arm_right", "forearm_right", "child"),
    "shank_left": ("knee_left", "thigh_left", "shank_left", "child"),
    "shank_right": ("knee_right", "thigh_right", "shank_right", "child"),
}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _array_sha(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    return hashlib.sha256(array.view(np.uint8)).hexdigest()


def _semantic_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
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


def _write_new_npz(path: Path, arrays: Mapping[str, np.ndarray]) -> None:
    with path.open("xb") as handle:
        np.savez_compressed(
            handle, **{key: np.asarray(value) for key, value in sorted(arrays.items())}
        )
    path.chmod(0o444)


def _branch_sign(branch_id: str, edge: str) -> int:
    marker = f"{edge}:"
    if marker not in branch_id:
        raise ValueError(f"missing hinge sign in {branch_id}")
    value = branch_id.split(marker, 1)[1].split("_", 1)[0]
    if value not in {"pos", "neg"}:
        raise ValueError(f"invalid hinge sign in {branch_id}")
    return 1 if value == "pos" else -1


def _broad_wear_prior(
    *,
    candidate_sensor_from_segment: np.ndarray,
    segment: str,
    wear: Mapping[str, Any],
) -> tuple[np.ndarray, Mapping[str, Any]]:
    row = wear["rows_by_segment"][segment]
    nominal_minus_z = np.asarray(
        row["nominal_body_vector_for_cone_evaluation"], dtype=float,
    )
    nominal_minus_y = np.asarray(wear["common_nominal"], dtype=float)
    sigma = float(wear["near_sigma"])
    logs = []
    for sensor_from_segment in candidate_sensor_from_segment:
        segment_from_sensor = np.asarray(sensor_from_segment, dtype=float).T
        minus_y = segment_from_sensor @ np.array([0.0, -1.0, 0.0])
        minus_z = segment_from_sensor @ np.array([0.0, 0.0, -1.0])
        y_angle = float(np.arccos(np.clip(minus_y @ nominal_minus_y, -1.0, 1.0)))
        z_angle = float(np.arccos(np.clip(minus_z @ nominal_minus_z, -1.0, 1.0)))
        logs.append(
            _soft_full_support_angular_log_likelihood(y_angle, sigma)
            + _soft_full_support_angular_log_likelihood(z_angle, sigma)
        )
    log_values = np.asarray(logs, dtype=float)
    weights = np.exp(log_values - float(np.max(log_values)))
    weights /= float(np.sum(weights))
    return weights, {
        "model": "REGISTERED_NEAR_UNINFORMATIVE_HEMISPHERE_FULL_SUPPORT_WEAR_PRIOR",
        "sigma_rad": sigma,
        "candidate_log_likelihood_sha256": _array_sha(log_values),
        "legacy_40_55_70_degree_profiles_used_as_posterior_or_gate": False,
        "old_fixed_eight_point_support_used": False,
        "complete_s1_cell_count": int(len(weights)),
        "all_cells_strictly_positive": bool(np.all(weights > 0.0)),
    }


def _span_prefixes(
    source: Mapping[str, np.ndarray], *, action_index: int, branch_id: str, edge: str,
) -> list[str]:
    base = f"heading/{action_index:02d}/{branch_id}/{edge}/"
    return sorted({
        key.rsplit("/", 1)[0]
        for key in source
        if key.startswith(base) and key.endswith("/selected_parent_source_indices")
    })


def _complete_motion_rows(
    source: Mapping[str, np.ndarray],
    *,
    action_index: int,
    branch_id: str,
    edge: str,
    parent_node: str,
    child_node: str,
    parent_lever: np.ndarray,
    child_lever: np.ndarray,
    signed_parent_axis: np.ndarray,
    signed_child_axis: np.ndarray,
    sample_period_s: float,
    savgol_window_samples: int,
    savgol_polynomial: int,
    candidate_sensor_from_segment: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, Mapping[str, Any]]:
    joint_rows: list[np.ndarray] = []
    transverse_rows: list[np.ndarray] = []
    span_audits = []
    half = savgol_window_samples // 2
    for prefix in _span_prefixes(
        source, action_index=action_index, branch_id=branch_id, edge=edge,
    ):
        parent_indices = np.asarray(
            source[f"{prefix}/selected_parent_source_indices"], dtype=np.int64,
        )
        child_indices = np.asarray(
            source[f"{prefix}/selected_child_source_indices"], dtype=np.int64,
        )
        parent_acc = np.asarray(
            source[f"orientation/{action_index:02d}/{parent_node}/acc_mps2"], dtype=float,
        )[parent_indices]
        child_acc = np.asarray(
            source[f"orientation/{action_index:02d}/{child_node}/acc_mps2"], dtype=float,
        )[child_indices]
        parent_gyro = np.asarray(
            source[f"orientation/{action_index:02d}/{parent_node}/gyro_rads"], dtype=float,
        )[parent_indices]
        child_gyro = np.asarray(
            source[f"orientation/{action_index:02d}/{child_node}/gyro_rads"], dtype=float,
        )[child_indices]
        parent_quat = np.asarray(
            source[f"orientation/{action_index:02d}/{parent_node}/quat_world_sensor_wxyz"],
            dtype=float,
        )[parent_indices]
        child_quat = np.asarray(
            source[f"orientation/{action_index:02d}/{child_node}/quat_world_sensor_wxyz"],
            dtype=float,
        )[child_indices]
        count = len(parent_indices)
        if count <= 2 * half:
            raise RuntimeError(f"{prefix}: span is too short for registered derivative owner")
        parent_terms, parent_alpha, parent_smoothed = _center_terms(
            parent_gyro, dt=sample_period_s,
            window=savgol_window_samples, polynomial=savgol_polynomial,
        )
        child_terms, child_alpha, child_smoothed = _center_terms(
            child_gyro, dt=sample_period_s,
            window=savgol_window_samples, polynomial=savgol_polynomial,
        )
        local = np.arange(half, count - half, dtype=np.int64)
        parent_joint_sensor = (
            parent_acc + np.einsum("nij,j->ni", parent_terms, parent_lever)
        )[local]
        child_joint_sensor = (
            child_acc + np.einsum("nij,j->ni", child_terms, child_lever)
        )[local]
        parent_rotation = qmt_wxyz_to_scipy_active(parent_quat[local]).as_matrix()
        child_rotation = qmt_wxyz_to_scipy_active(child_quat[local]).as_matrix()
        parent_joint_world = np.einsum("nij,nj->ni", parent_rotation, parent_joint_sensor)
        child_joint_world = np.einsum("nij,nj->ni", child_rotation, child_joint_sensor)
        joint_residual = parent_joint_world - child_joint_world

        parent_omega_world = np.einsum(
            "nij,nj->ni", parent_rotation, parent_smoothed[local],
        )
        child_omega_world = np.einsum(
            "nij,nj->ni", child_rotation, child_smoothed[local],
        )
        relative_omega = child_omega_world - parent_omega_world
        parent_axis_world = np.einsum("nij,j->ni", parent_rotation, signed_parent_axis)
        child_axis_world = np.einsum("nij,j->ni", child_rotation, signed_child_axis)
        axis_world = parent_axis_world + child_axis_world
        norms = np.linalg.norm(axis_world, axis=1)
        fallback = norms <= 1e-10
        axis_world[fallback] = parent_axis_world[fallback]
        norms[fallback] = np.linalg.norm(axis_world[fallback], axis=1)
        axis_world /= norms[:, None]
        perpendicular = relative_omega - axis_world * np.sum(
            relative_omega * axis_world, axis=1,
        )[:, None]
        transverse = np.linalg.norm(perpendicular, axis=1)

        # Result-independent rows (first/middle/last) exercise every complete
        # S1 cell by re-expressing the raw child motion in that candidate frame
        # and transforming it back to world.  This proves the constant
        # likelihood numerically rather than merely assigning a zero vector.
        mutation_local = np.unique(np.array([local[0], local[len(local) // 2], local[-1]]))
        candidate_joint, candidate_omega, candidate_axis = (
            recompute_distal_motion_through_candidate_coordinates(
                candidate_sensor_from_segment=candidate_sensor_from_segment,
                world_from_sensor=qmt_wxyz_to_scipy_active(
                    child_quat[mutation_local]
                ).as_matrix(),
                accelerometer_sensor_mps2=child_acc[mutation_local],
                angular_velocity_sensor_rads=child_smoothed[mutation_local],
                angular_acceleration_sensor_rads2=child_alpha[mutation_local],
                sensor_to_joint_lever_m=child_lever,
                signed_hinge_axis_sensor=signed_child_axis,
            )
        )
        expected_child_joint = child_joint_world[
            np.searchsorted(local, mutation_local)
        ]
        expected_child_omega = child_omega_world[
            np.searchsorted(local, mutation_local)
        ]
        expected_child_axis = child_axis_world[
            np.searchsorted(local, mutation_local)
        ]
        mutation_error = max(
            float(np.max(np.abs(candidate_joint - expected_child_joint[None, :, :]))),
            float(np.max(np.abs(candidate_omega - expected_child_omega[None, :, :]))),
            float(np.max(np.abs(candidate_axis - expected_child_axis[None, :, :]))),
        )
        if mutation_error > 1e-10:
            raise RuntimeError(f"{prefix}: distal coordinate mutation changed motion residual")
        joint_rows.append(joint_residual)
        transverse_rows.append(transverse)
        span_audits.append({
            "prefix": prefix,
            "source_row_count": count,
            "differentiated_row_count": int(len(local)),
            "parent_source_indices_sha256": _array_sha(parent_indices),
            "child_source_indices_sha256": _array_sha(child_indices),
            "joint_residual_sha256": _array_sha(joint_residual),
            "transverse_hinge_motion_residual_sha256": _array_sha(transverse),
            "complete_s1_coordinate_mutation_cell_count": int(
                len(candidate_sensor_from_segment)
            ),
            "coordinate_mutation_source_row_count": int(len(mutation_local)),
            "coordinate_mutation_max_abs_world_reexpression_error": mutation_error,
            "gap_or_boundary_rows_differentiated_across": 0,
        })
    if not joint_rows:
        raise RuntimeError(f"{action_index}:{branch_id}:{edge}: no exact gap-safe pair span")
    joint = np.concatenate(joint_rows, axis=0)
    transverse = np.concatenate(transverse_rows, axis=0)
    return joint, transverse, {"span_count": len(span_audits), "spans": span_audits}


def _load_grid() -> np.ndarray:
    with np.load(GRID_SOURCE, allow_pickle=False) as grid_source:
        keys = sorted(key for key in grid_source.files if key.endswith("/delta_grid_rad"))
        if not keys:
            raise RuntimeError("saved replay-002 complete-S1 grid is absent")
        grid = np.asarray(grid_source[keys[0]], dtype=float)
        if any(not np.array_equal(grid, np.asarray(grid_source[key], dtype=float)) for key in keys):
            raise RuntimeError("saved replay-002 S1 grids differ")
    return grid


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=False)
    settings_document = json.loads(SETTINGS.read_text(encoding="utf-8"))
    settings = settings_document["effective_settings"]
    chronology = tuple(settings["execution_contract"]["chronological_actions"])
    node_by_segment = {
        str(row["body_segment"]): str(row["hardware_id"])
        for row in settings["segment_frames"]["wear_authority"]["rows"]
    }
    wear = _validated_wear_authority(settings["segment_frames"])
    grid = _load_grid()
    source_hash = _sha(SOURCE)
    source_manifest_hash = _sha(SOURCE_MANIFEST)
    settings_hash = _sha(SETTINGS)
    parent_ablation_hash = _sha(PARENT_ABLATION)
    arrays: dict[str, np.ndarray] = {}
    owner_reports: dict[str, Any] = {}
    with np.load(SOURCE, allow_pickle=False) as source:
        branch_ids = sorted({
            key.split("/")[2]
            for key in source.files
            if key.startswith("heading/15/")
            and "/knee_left/" in key
            and key.endswith("/selected_parent_source_indices")
        })
        if len(branch_ids) != 4:
            raise RuntimeError("expected exact four retained saved hinge branches")
        for branch_id in branch_ids:
            for segment, (edge, parent_segment, child_segment, endpoint) in EDGE_ROWS.items():
                if endpoint != "child":
                    raise RuntimeError("distal complete-S1 owner requires the child endpoint")
                connection_prefix = f"frames/{branch_id}/connection/{edge}"
                parent_lever = np.asarray(source[f"{connection_prefix}/parent"], dtype=float)
                child_lever = np.asarray(source[f"{connection_prefix}/child"], dtype=float)
                sign = _branch_sign(branch_id, edge)
                parent_axis = sign * np.asarray(
                    source[f"geometry_checkpoint/15/axis/{edge}/parent"], dtype=float,
                )
                child_axis = sign * np.asarray(
                    source[f"geometry_checkpoint/15/axis/{edge}/child"], dtype=float,
                )
                parent_axis /= np.linalg.norm(parent_axis)
                child_axis /= np.linalg.norm(child_axis)
                candidates = complete_s1_sensor_from_segment_candidates(
                    signed_hinge_axis_sensor=child_axis,
                    delta_grid_rad=grid,
                )
                prior, prior_audit = _broad_wear_prior(
                    candidate_sensor_from_segment=candidates,
                    segment=segment,
                    wear=wear,
                )
                authority = {
                    "settings_sha256": settings_hash,
                    "saved_qmt_state_sha256": source_hash,
                    "branch_id": branch_id,
                    "segment": segment,
                    "edge": edge,
                    "full_r3_parent_lever_sha256": _array_sha(parent_lever),
                    "full_r3_child_lever_sha256": _array_sha(child_lever),
                    "signed_parent_hinge_axis_sha256": _array_sha(parent_axis),
                    "signed_child_hinge_axis_sha256": _array_sha(child_axis),
                    "wear_authority_hashes": wear["hashes"],
                    "prior_audit": prior_audit,
                }
                owner = CompleteS1DistalLongitudinalOwner(
                    segment=segment,
                    delta_grid_rad=grid,
                    registered_prior_weights=prior,
                    prior_binding=complete_s1_prior_binding(
                        segment=segment,
                        delta_grid_rad=grid,
                        registered_prior_weights=prior,
                        source_authority_sha256=_semantic_sha(authority),
                    ),
                )
                action_evidence = []
                for action_index, action in enumerate(chronology):
                    joint, transverse, pair_audit = _complete_motion_rows(
                        source,
                        action_index=action_index,
                        branch_id=branch_id,
                        edge=edge,
                        parent_node=node_by_segment[parent_segment],
                        child_node=node_by_segment[child_segment],
                        parent_lever=parent_lever,
                        child_lever=child_lever,
                        signed_parent_axis=parent_axis,
                        signed_child_axis=child_axis,
                        sample_period_s=float(settings["orientation"]["sample_period_s"]),
                        savgol_window_samples=int(
                            settings["joint_center"]["savgol_window_samples"]
                        ),
                        savgol_polynomial=int(
                            settings["joint_center"]["savgol_polynomial"]
                        ),
                        candidate_sensor_from_segment=candidates,
                    )
                    input_binding = {
                        "schema": "biospur-c2-distal-longitudinal-complete-motion-input-v1",
                        "segment": segment,
                        "chronological_index": action_index,
                        "action": action,
                        "joint_acceleration_residual_world_mps2_sha256": _array_sha(joint),
                        "relative_angular_velocity_perpendicular_rads_sha256": _array_sha(
                            transverse
                        ),
                        "action_pose_truth_used": False,
                        "pixel_or_manual_axis_selection_used": False,
                        "pair_audit": pair_audit,
                    }
                    action_evidence.append(owner.process_gauge_invariant_action(
                        chronological_index=action_index,
                        action=action,
                        joint_acceleration_residual_world_mps2=joint,
                        relative_angular_velocity_perpendicular_rads=transverse,
                        owner_input_binding=input_binding,
                    ))
                posterior = owner.posterior()
                if not np.array_equal(posterior.prior_weights, posterior.posterior_weights):
                    raise RuntimeError("gauge-invariant complete motion changed distal posterior")
                prefix = f"{branch_id}/{segment}"
                arrays[f"{prefix}/delta_grid_rad"] = posterior.delta_grid_rad
                arrays[f"{prefix}/candidate_sensor_from_segment"] = candidates
                arrays[f"{prefix}/prior_weights"] = posterior.prior_weights
                arrays[f"{prefix}/posterior_weights"] = posterior.posterior_weights
                arrays[f"{prefix}/signed_parent_hinge_axis_sensor"] = parent_axis
                arrays[f"{prefix}/signed_child_hinge_axis_sensor"] = child_axis
                arrays[f"{prefix}/full_r3_parent_sensor_to_joint_m"] = parent_lever
                arrays[f"{prefix}/full_r3_child_sensor_to_joint_m"] = child_lever
                owner_reports[prefix] = {
                    "authority": authority,
                    "posterior": posterior.report,
                    "action_evidence": action_evidence,
                }

    npz_path = OUT / "COMPLETE_S1_DISTAL_LONGITUDINAL_STATE.npz"
    _write_new_npz(npz_path, arrays)
    audit = {
        "schema": "biospur-c2-distal-longitudinal-complete-s1-replay-audit-v1",
        "created_local": _now(),
        "inputs": {
            "ordinary_flow_failure_attempt_001": {
                "path": str(FAILED_ATTEMPT), "sha256": _sha(FAILED_ATTEMPT),
                "scientific_arrays_written": False,
                "cause": "CALLER_USED_GRID_INSTEAD_OF_DELTA_GRID_RAD_KEYWORD",
            },
            "saved_training_qmt_state": {"path": str(SOURCE), "sha256": source_hash},
            "saved_training_qmt_manifest": {
                "path": str(SOURCE_MANIFEST), "sha256": source_manifest_hash,
            },
            "complete_s1_grid_source": {
                "path": str(GRID_SOURCE), "sha256": _sha(GRID_SOURCE),
            },
            "settings": {"path": str(SETTINGS), "sha256": settings_hash},
            "parent_segment_frame_qmt_ablation": {
                "path": str(PARENT_ABLATION), "sha256": parent_ablation_hash,
            },
        },
        "output_npz": {"path": str(npz_path), "sha256": _sha(npz_path)},
        "chronological_actions": list(chronology),
        "retained_hinge_branch_count": 4,
        "distal_segment_count": 4,
        "owner_reports": owner_reports,
        "conclusions": {
            "complete_motion_likelihood_evaluated_on_all_19_actions": True,
            "joint_acceleration_and_hinge_transverse_motion_are_distal_s1_gauge_invariant": True,
            "candidate_dependent_motion_information_gain_nats": 0.0,
            "complete_motion_identifies_distal_longitudinal_direction": False,
            "broad_complete_s1_wear_prior_retained_without_collapse": True,
            "old_fixed_eight_point_support_used_or_selected": False,
            "sole_sensor_to_joint_lever_used_as_longitudinal_axis": False,
            "hard_map_argmax_action_truth_or_pixel_selection_used": False,
            "distal_endpoint_pose_ready": False,
            "qmt_rerun": False,
            "viewer_or_pixels_generated": False,
            "scientific_acceptance_pass": False,
        },
        "coordinate_history_boundary": {
            "debug004_fixed_prefix15_frame_history_is_not_saved_official_progressive_frame_history": True,
            "saved_ablation_records_left_debug004_vs_official_delta_rad_approximately": [2.03, 1.86],
            "saved_ablation_records_right_debug004_vs_official_delta_rad_approximately": [0.91, -0.51],
            "future_qmt_requires_one_coherent_corrected_frame_history_or_explicit_state_coordinate_migration": True,
        },
        "execution": {
            "raw_payload_reread": False,
            "fit_or_progressive_rerun": False,
            "qmt_or_tree_rerun": False,
            "heldout_opened": False,
            "final_frozen_full_r3_center_and_axis_evidence_used_for_training_only_calibration_diagnostic": True,
            "causal_prefix_metric_claimed": False,
        },
    }
    audit_path = OUT / "AUDIT.json"
    _write_new_json(audit_path, audit)
    print(json.dumps({
        "audit": str(audit_path), "audit_sha256": _sha(audit_path),
        "npz": str(npz_path), "npz_sha256": _sha(npz_path),
        "owner_count": len(owner_reports),
        "execution_complete": True,
        "scientific_pass": False,
    }, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        if OUT.is_dir():
            failure = OUT / "FAILURE.json"
            if not failure.exists():
                _write_new_json(failure, {
                    "schema": "biospur-c2-distal-longitudinal-complete-s1-replay-failure-v1",
                    "created_local": _now(),
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                    "raw_payload_reread": False,
                    "qmt_fit_or_progressive_rerun": False,
                })
        raise
