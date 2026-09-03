"""R6A2B-R3 session-relative joint calibration on the canonical shared FK.

The joint reference is a Capture-1 coordinate convention.  It is deliberately
not presented as a physiological zero.  Dynamic joint states are nuisance
coordinates, and the neutral-reference factor removes only their constant
SO(3) gauge against ``joint_rest``.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix

from biospur_fusion.root_r6a0.body import BodyModel, KeyframeState
from biospur_fusion.root_r6a0.math3d import so3_exp, so3_log
from biospur_fusion.root_r6a2b.layered_calibration import (
    NODES, CanonicalCalibrationAdapter, coordinate_label,
    _state_from_segment_rotations,
)


SESSION_REFERENCE_NAME = "SESSION_RELATIVE_JOINT_REFERENCE"
REFERENCE_WINDOW = "initial_still2"

JOINT_ACTIONS: dict[str, tuple[str, ...]] = {
    "pelvis_torso": (REFERENCE_WINDOW, "trunk"),
    "shoulder_left": (REFERENCE_WINDOW, "t_pose", "arms"),
    "elbow_left": (REFERENCE_WINDOW, "t_pose", "arms", "left_elbow"),
    "shoulder_right": (REFERENCE_WINDOW, "t_pose", "arms"),
    "elbow_right": (REFERENCE_WINDOW, "t_pose", "arms", "right_elbow2"),
    "hip_left": (REFERENCE_WINDOW, "left_knee", "left_heel", "squats"),
    "knee_left": (REFERENCE_WINDOW, "left_knee", "left_heel", "squats"),
    "hip_right": (REFERENCE_WINDOW, "right_knee", "right_heel", "squats"),
    "knee_right": (REFERENCE_WINDOW, "right_knee", "right_heel", "squats"),
}

HINGE_ACTION = {
    "elbow_left": "left_elbow",
    "elbow_right": "right_elbow2",
    "knee_left": "left_knee",
    "knee_right": "right_knee",
}

REPORTING_LABEL = {
    "pelvis_torso": "SESSION_RELATIVE_PELVIS_TORSO_ROTATION",
    "shoulder_left": "SESSION_RELATIVE_SHOULDER_ROTATION_LEFT",
    "elbow_left": "SESSION_RELATIVE_ELBOW_FLEXION_LEFT",
    "shoulder_right": "SESSION_RELATIVE_SHOULDER_ROTATION_RIGHT",
    "elbow_right": "SESSION_RELATIVE_ELBOW_FLEXION_RIGHT",
    "hip_left": "SESSION_RELATIVE_HIP_ROTATION_LEFT",
    "knee_left": "SESSION_RELATIVE_KNEE_FLEXION_LEFT",
    "hip_right": "SESSION_RELATIVE_HIP_ROTATION_RIGHT",
    "knee_right": "SESSION_RELATIVE_KNEE_FLEXION_RIGHT",
}

RESIDUAL_FAMILIES = (
    "reference_pose", "functional_axis", "off_axis_soft",
    "joint_closure", "temporal_consistency", "prior",
)


def _quantiles(values: Sequence[float]) -> dict[str, float | int | None]:
    value = np.asarray(values, float)
    if not len(value):
        return {"count": 0, "min": None, "q05": None, "median": None,
                "q95": None, "max": None, "rms": None,
                "half_squared_norm": 0.0}
    return {
        "count": int(len(value)), "min": float(np.min(value)),
        "q05": float(np.quantile(value, .05)), "median": float(np.median(value)),
        "q95": float(np.quantile(value, .95)), "max": float(np.max(value)),
        "rms": float(np.sqrt(np.mean(value ** 2))),
        "half_squared_norm": 0.5 * float(value @ value),
    }


def _proper_mean(rotations: np.ndarray, iterations: int = 30) -> np.ndarray:
    """Deterministic intrinsic SO(3) mean for a compact session window."""
    value = np.asarray(rotations, float)
    mean = value[0].copy()
    for _ in range(iterations):
        delta = np.mean([so3_log(mean.T @ rotation) for rotation in value], axis=0)
        mean = mean @ so3_exp(delta)
        if float(np.linalg.norm(delta)) <= 32.0 * np.finfo(float).eps:
            break
    return mean


def relative_joint_rotations(
    model: BodyModel,
    segment_rotation: np.ndarray,
    segment_names: Sequence[str],
) -> dict[str, np.ndarray]:
    """Return measured parent-to-child rotations without a second topology."""
    index = {str(segment): number for number, segment in enumerate(segment_names)}
    rotations = np.asarray(segment_rotation, float)
    result = {}
    for joint in model.joints:
        parent = rotations[:, index[joint.parent]]
        child = rotations[:, index[joint.child]]
        result[joint.joint_id] = np.einsum("nji,njk->nik", parent, child)
    return result


def session_joint_coordinates(reference: np.ndarray, relative: np.ndarray) -> np.ndarray:
    """q(t)=Log(R_reference^T R_parent^T R_child)."""
    return np.asarray([so3_log(reference.T @ rotation) for rotation in relative])


def _selected_indices(window: np.ndarray, actions: Sequence[str], per_action: int = 5) -> np.ndarray:
    selected: list[int] = []
    for action in actions:
        candidates = np.flatnonzero(window == action)
        if not len(candidates):
            raise ValueError(f"missing authorized action {action}")
        count = min(per_action, len(candidates))
        selected.extend(int(candidates[item]) for item in np.unique(
            np.linspace(0, len(candidates) - 1, count, dtype=int)
        ))
    return np.asarray(sorted(set(selected)), dtype=int)


def _axis_from_window(relative: np.ndarray, window: np.ndarray, action: str) -> dict[str, Any]:
    indices = np.flatnonzero(window == action)
    increments = np.asarray([
        so3_log(relative[left].T @ relative[right])
        for left, right in zip(indices[:-1], indices[1:])
    ])
    second = increments.T @ increments
    eigenvalues, eigenvectors = np.linalg.eigh(0.5 * (second + second.T))
    axis = eigenvectors[:, -1]
    pivot = int(np.argmax(np.abs(axis)))
    if axis[pivot] < 0.0:
        axis = -axis
    magnitudes = np.linalg.norm(increments, axis=1)
    nonzero = magnitudes > np.finfo(float).eps
    angles = np.arccos(np.clip(
        np.abs(increments[nonzero] @ axis) / magnitudes[nonzero], 0.0, 1.0
    )) if np.any(nonzero) else np.zeros(0)
    perpendicular = increments - np.outer(increments @ axis, axis)
    dispersion = float(np.sqrt(np.mean(np.sum(perpendicular ** 2, axis=1))))
    return {
        "direction_parent_reference_unoriented": axis,
        "increment_count": int(len(increments)),
        "second_moment_eigenvalues": eigenvalues,
        "angular_dispersion_rad": float(np.sqrt(np.mean(angles ** 2))) if len(angles) else 0.0,
        "off_axis_increment_rms_rad": dispersion,
        "increments": increments,
    }


def _motion_scale(relative: np.ndarray, indices: np.ndarray) -> float:
    increments = np.asarray([
        so3_log(relative[left].T @ relative[right])
        for left, right in zip(indices[:-1], indices[1:])
    ])
    if not len(increments):
        return float(np.sqrt(np.finfo(float).eps))
    centred = increments - np.mean(increments, axis=0)
    empirical = float(np.sqrt(np.mean(np.sum(centred ** 2, axis=1))))
    return max(empirical, float(np.sqrt(np.finfo(float).eps)))


@dataclass(frozen=True)
class JointSolve:
    joint_id: str
    reference_rotvec: np.ndarray
    reference_covariance: np.ndarray
    selected_indices: np.ndarray
    selected_q: np.ndarray
    objective_initial: float
    objective_final: float
    residuals: Mapping[str, np.ndarray]
    residual_actions: Mapping[str, tuple[str, ...]]
    optimizer: Mapping[str, Any]
    uncertainty: Mapping[str, Any]
    functional_axis: Mapping[str, Any] | None
    convention_rank: int


def solve_one_joint(
    joint_id: str,
    relative_all: np.ndarray,
    window_all: np.ndarray,
    parent_sensor_sigma_rad: float,
    child_sensor_sigma_rad: float,
    *,
    include_biomechanics: bool,
    initial: np.ndarray | None = None,
    functional_axis_override: np.ndarray | None = None,
    off_axis_sigma_override_rad: float | None = None,
    consistent_parent_frame: bool = False,
) -> JointSolve:
    """Solve one separable reference/nuisance-state problem."""
    actions = JOINT_ACTIONS[joint_id]
    selected = _selected_indices(window_all, actions)
    relative = np.asarray(relative_all[selected], float)
    window = np.asarray(window_all[selected], dtype="U32")
    neutral = np.flatnonzero(window == REFERENCE_WINDOW)
    reference_initial = _proper_mean(relative[neutral])
    r0 = so3_log(reference_initial)
    q0 = np.asarray([so3_log(reference_initial.T @ rotation) for rotation in relative])
    x0 = np.concatenate((r0, q0.reshape(-1))) if initial is None else np.asarray(initial, float).copy()

    neutral_logs = np.asarray([so3_log(reference_initial.T @ relative[item]) for item in neutral])
    neutral_variability = float(np.sqrt(np.mean(np.sum(neutral_logs ** 2, axis=1))))
    neutral_steps = np.asarray([
        so3_log(relative[left].T @ relative[right])
        for left, right in zip(neutral[:-1], neutral[1:])
    ])
    strap_motion = float(np.sqrt(np.mean(np.sum(neutral_steps ** 2, axis=1)))) if len(neutral_steps) else 0.0
    extrinsic_component = float(np.hypot(parent_sensor_sigma_rad, child_sensor_sigma_rad))
    reference_sigma = max(
        float(np.sqrt(extrinsic_component ** 2 + neutral_variability ** 2 + strap_motion ** 2)),
        float(np.sqrt(np.finfo(float).eps)),
    )
    closure_sigma = reference_sigma

    action_positions = {
        action: np.flatnonzero(window == action) for action in actions
    }
    temporal_sigma = {
        action: _motion_scale(relative, positions)
        for action, positions in action_positions.items()
    }
    axis_report = None
    if joint_id in HINGE_ACTION:
        axis_report = _axis_from_window(relative_all, window_all, HINGE_ACTION[joint_id])
        if functional_axis_override is None:
            axis = np.asarray(axis_report["direction_parent_reference_unoriented"], float)
        else:
            axis = np.asarray(functional_axis_override, float)
            axis /= np.linalg.norm(axis)
            axis_report["direction_parent_reference_unoriented"] = axis
            axis_report["source"] = "R4_NATIVE_TIME_PARENT_FRAME_OVERRIDE"
        off_axis_sigma = max(
            float(axis_report["off_axis_increment_rms_rad"])
            if off_axis_sigma_override_rad is None else float(off_axis_sigma_override_rad),
            float(np.sqrt(np.finfo(float).eps)),
        )
    else:
        axis = None
        off_axis_sigma = None

    descriptors: list[tuple[str, str]] = []

    def append(rows: list[float], values: np.ndarray, family: str, action: str) -> None:
        flat = np.asarray(values, float).reshape(-1)
        rows.extend(float(value) for value in flat)
        descriptors.extend((family, action) for _ in flat)

    def residual(vector: np.ndarray) -> np.ndarray:
        r = np.asarray(vector[:3], float)
        q = np.asarray(vector[3:], float).reshape(len(relative), 3)
        reference = so3_exp(r)
        rows: list[float] = []
        descriptors.clear()
        for item, observed in enumerate(relative):
            predicted = reference @ so3_exp(q[item])
            append(rows, so3_log(predicted.T @ observed) / closure_sigma,
                   "joint_closure", str(window[item]))

        if include_biomechanics:
            # Exactly three gauge-defining rows: the mean neutral q is zero.
            append(rows, np.mean(q[neutral], axis=0) / reference_sigma,
                   "reference_pose", REFERENCE_WINDOW)
            for action, positions in action_positions.items():
                if len(positions) < 2:
                    continue
                observed_delta = [
                    so3_log(relative[left].T @ relative[right])
                    for left, right in zip(positions[:-1], positions[1:])
                ]
                predicted_delta = [
                    so3_log(so3_exp(q[left]).T @ so3_exp(q[right]))
                    for left, right in zip(positions[:-1], positions[1:])
                ]
                if consistent_parent_frame:
                    observed_delta = [
                        relative[left] @ value
                        for left, value in zip(positions[:-1], observed_delta)
                    ]
                    predicted_delta = [
                        (reference @ so3_exp(q[left])) @ value
                        for left, value in zip(positions[:-1], predicted_delta)
                    ]
                scale = temporal_sigma[action]
                append(rows, (np.asarray(predicted_delta) - np.asarray(observed_delta)) / scale,
                       "functional_axis", action)
                if axis is not None and action == HINGE_ACTION[joint_id]:
                    projector = np.eye(3) - np.outer(axis, axis)
                    cumulative = np.asarray([
                        so3_log(so3_exp(q[positions[0]]).T @ so3_exp(q[item]))
                        for item in positions[1:]
                    ])
                    if consistent_parent_frame:
                        start_rotation = reference @ so3_exp(q[positions[0]])
                        cumulative = (start_rotation @ cumulative.T).T
                    append(rows, (cumulative @ projector.T) / off_axis_sigma,
                           "off_axis_soft", action)

        for action, positions in action_positions.items():
            if len(positions) < 3:
                continue
            delta = np.asarray([
                so3_log(so3_exp(q[left]).T @ so3_exp(q[right]))
                for left, right in zip(positions[:-1], positions[1:])
            ])
            if consistent_parent_frame:
                delta = np.asarray([
                    (so3_exp(r) @ so3_exp(q[left])) @ value
                    for left, value in zip(positions[:-1], delta)
                ])
            append(rows, np.diff(delta, axis=0) / temporal_sigma[action],
                   "temporal_consistency", action)
        append(rows, r / np.pi, "prior", "R6A1B_BROAD_PRIOR")
        return np.asarray(rows, float)

    initial_residual = residual(x0)
    sparsity = lil_matrix((len(initial_residual), len(x0)), dtype=int)
    cursor = 0
    for item in range(len(relative)):
        sparsity[cursor:cursor + 3, 0:3] = 1
        sparsity[cursor:cursor + 3, 3 + 3 * item:6 + 3 * item] = 1
        cursor += 3
    if include_biomechanics:
        for item in neutral:
            sparsity[cursor:cursor + 3, 3 + 3 * item:6 + 3 * item] = 1
        cursor += 3
        for action, positions in action_positions.items():
            for left, right in zip(positions[:-1], positions[1:]):
                if consistent_parent_frame:
                    sparsity[cursor:cursor + 3, 0:3] = 1
                sparsity[cursor:cursor + 3, 3 + 3 * left:6 + 3 * left] = 1
                sparsity[cursor:cursor + 3, 3 + 3 * right:6 + 3 * right] = 1
                cursor += 3
            if axis is not None and action == HINGE_ACTION[joint_id]:
                first = int(positions[0])
                for item in positions[1:]:
                    if consistent_parent_frame:
                        sparsity[cursor:cursor + 3, 0:3] = 1
                    sparsity[cursor:cursor + 3, 3 + 3 * first:6 + 3 * first] = 1
                    sparsity[cursor:cursor + 3, 3 + 3 * item:6 + 3 * item] = 1
                    cursor += 3
    for action, positions in action_positions.items():
        for first, middle, last in zip(positions[:-2], positions[1:-1], positions[2:]):
            if consistent_parent_frame:
                sparsity[cursor:cursor + 3, 0:3] = 1
            for item in (first, middle, last):
                sparsity[cursor:cursor + 3, 3 + 3 * item:6 + 3 * item] = 1
            cursor += 3
    sparsity[cursor:cursor + 3, 0:3] = 1
    cursor += 3
    if cursor != len(initial_residual):
        raise RuntimeError("joint objective sparsity accounting changed")
    result = least_squares(
        residual, x0, method="trf", jac="2-point", x_scale="jac",
        jac_sparsity=sparsity.tocsr(), tr_solver="lsmr",
        ftol=1e-8, xtol=1e-8, gtol=1e-8, max_nfev=60,
    )
    final_residual = residual(result.x)
    final_descriptors = tuple(descriptors)
    by_family = {
        family: final_residual[[name == family for name, _ in final_descriptors]]
        for family in RESIDUAL_FAMILIES
    }
    by_action = {
        family: tuple(action for (name, action) in final_descriptors if name == family)
        for family in RESIDUAL_FAMILIES
    }
    jacobian = result.jac.toarray() if hasattr(result.jac, "toarray") else np.asarray(result.jac, float)
    hessian = jacobian.T @ jacobian
    covariance = np.linalg.pinv(hessian, rcond=max(hessian.shape) * np.finfo(float).eps)
    covariance = 0.5 * (covariance + covariance.T)
    reference_covariance = covariance[:3, :3]
    return JointSolve(
        joint_id=joint_id,
        reference_rotvec=result.x[:3].copy(),
        reference_covariance=reference_covariance,
        selected_indices=selected,
        selected_q=result.x[3:].reshape(len(relative), 3).copy(),
        objective_initial=0.5 * float(initial_residual @ initial_residual),
        objective_final=float(result.cost),
        residuals=by_family,
        residual_actions=by_action,
        optimizer={
            "success": bool(result.success), "status": int(result.status),
            "message": str(result.message), "function_evaluations": int(result.nfev),
            "jacobian_evaluations": None if result.njev is None else int(result.njev),
            "optimality_inf_norm": float(result.optimality),
            "parameter_dimension_including_dynamic_nuisance": int(len(result.x)),
            "selected_keyframes": int(len(relative)),
            "functional_axis_frame": (
                "CALIBRATED_PARENT_SEGMENT_SESSION_REFERENCE" if consistent_parent_frame
                else "R3_RIGHT_LOCAL_CHILD_FRAME_HISTORICAL"
            ),
        },
        uncertainty={
            "sensor_extrinsic_parent_one_sigma_rad": float(parent_sensor_sigma_rad),
            "sensor_extrinsic_child_one_sigma_rad": float(child_sensor_sigma_rad),
            "combined_sensor_extrinsic_one_sigma_rad": extrinsic_component,
            "neutral_pose_variability_rms_rad": neutral_variability,
            "skin_strap_motion_proxy_rms_rad": strap_motion,
            "reference_factor_one_sigma_rad": reference_sigma,
            "limited_excitation": {
                action: {
                    "increment_scale_rad": float(temporal_sigma[action]),
                    "sample_count": int(len(action_positions[action])),
                } for action in actions if action != REFERENCE_WINDOW
            },
            "signed_axis_gauge": (
                "non-Gaussian 24-member family gauge retained; covariance is conditional on the "
                "canonical combined register-to-segment representative"
            ),
        },
        functional_axis=None if axis_report is None else {
            key: (value.tolist() if isinstance(value, np.ndarray) else value)
            for key, value in axis_report.items() if key != "increments"
        },
        convention_rank=3 if include_biomechanics else 0,
    )


def solve_session_references(
    model: BodyModel,
    relative: Mapping[str, np.ndarray],
    window: np.ndarray,
    segment_sensor_sigma: Mapping[str, float],
    *,
    include_biomechanics: bool,
    identical_initialization: Mapping[str, np.ndarray] | None = None,
    functional_axis_overrides: Mapping[str, np.ndarray] | None = None,
    off_axis_sigma_overrides_rad: Mapping[str, float] | None = None,
    consistent_parent_frame: bool = False,
) -> dict[str, JointSolve]:
    result = {}
    for joint in model.joints:
        result[joint.joint_id] = solve_one_joint(
            joint.joint_id, relative[joint.joint_id], window,
            segment_sensor_sigma[joint.parent], segment_sensor_sigma[joint.child],
            include_biomechanics=include_biomechanics,
            initial=None if identical_initialization is None else identical_initialization[joint.joint_id],
            functional_axis_override=(
                None if functional_axis_overrides is None
                else functional_axis_overrides.get(joint.joint_id)
            ),
            off_axis_sigma_override_rad=(
                None if off_axis_sigma_overrides_rad is None
                else off_axis_sigma_overrides_rad.get(joint.joint_id)
            ),
            consistent_parent_frame=consistent_parent_frame,
        )
    return result


def objective_summary(solves: Mapping[str, JointSolve]) -> dict[str, Any]:
    family_values = {family: [] for family in RESIDUAL_FAMILIES}
    family_actions: dict[str, dict[str, list[float]]] = {
        family: {} for family in RESIDUAL_FAMILIES
    }
    for solve in solves.values():
        for family in RESIDUAL_FAMILIES:
            values = np.asarray(solve.residuals[family], float)
            family_values[family].extend(float(value) for value in values)
            for action, value in zip(solve.residual_actions[family], values):
                family_actions[family].setdefault(action, []).append(float(value))
    return {
        "initial_half_squared_norm": float(sum(row.objective_initial for row in solves.values())),
        "final_half_squared_norm": float(sum(row.objective_final for row in solves.values())),
        "by_residual_family": {
            family: {
                **_quantiles(values),
                "by_action": {
                    action: _quantiles(action_values)
                    for action, action_values in sorted(family_actions[family].items())
                },
            } for family, values in family_values.items()
        },
        "per_joint_optimizer": {joint: dict(solve.optimizer) for joint, solve in solves.items()},
    }


def apply_reference_solution(
    adapter: CanonicalCalibrationAdapter,
    base_vector: np.ndarray,
    solves: Mapping[str, JointSolve],
    base_covariance: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    vector = np.asarray(base_vector, float).copy()
    covariance = np.asarray(base_covariance, float).copy()
    for joint in adapter.model.joints:
        block = adapter.by_slot[joint.rest_rotation_slot]
        vector[block.start:block.stop] = solves[joint.joint_id].reference_rotvec
        covariance[block.start:block.stop, :] = 0.0
        covariance[:, block.start:block.stop] = 0.0
        covariance[block.start:block.stop, block.start:block.stop] = solves[joint.joint_id].reference_covariance
    return vector, covariance


def _prediction_vector(model: BodyModel, state: KeyframeState, static) -> np.ndarray:
    poses = model.segment_poses(state, static)
    imus = model.imu_frames(state, static)
    tags = model.tag_phase_centres(state, static)
    values = []
    for segment in model.segments:
        values.extend(poses[segment].rotation.reshape(-1)); values.extend(poses[segment].translation)
    for node in model.imu_ids:
        values.extend(imus[node].rotation.reshape(-1)); values.extend(imus[node].translation)
    for tag in model.tag_ids:
        values.extend(tags[tag])
    return np.asarray(values, float)


def joint_rest_gauge_causal_trace(
    model: BodyModel,
    adapter: CanonicalCalibrationAdapter,
    base_vector: np.ndarray,
    segment_rotations: Mapping[str, np.ndarray],
    time_ns: int,
    internal_levers: Mapping[str, np.ndarray],
) -> dict[str, Any]:
    """Recompute the requested +/-1 degree fixed and compensated tests."""
    base_static = adapter.materialize_static(base_vector, internal_levers=internal_levers)
    state = _state_from_segment_rotations(model, segment_rotations, base_static, time_ns)
    baseline = _prediction_vector(model, state, base_static)
    step = np.deg2rad(1.0)
    slots = []
    all_compensated = []
    for joint in model.joints:
        block = adapter.by_slot[joint.rest_rotation_slot]
        coordinate_rows = []
        for axis_index, axis_name in enumerate("xyz"):
            sign_rows = []
            for sign in (-1.0, 1.0):
                delta = np.zeros(3); delta[axis_index] = sign * step
                perturbed = np.asarray(base_vector, float).copy()
                current_rest = so3_exp(base_vector[block.start:block.stop])
                perturbed[block.start:block.stop] = so3_log(current_rest @ so3_exp(delta))
                perturbed_static = adapter.materialize_static(perturbed, internal_levers=internal_levers)
                fixed = _prediction_vector(model, state, perturbed_static)
                updated_q = dict(state.joint_rotvec)
                updated_q[joint.joint_id] = so3_log(
                    so3_exp(-delta) @ so3_exp(state.joint_rotvec[joint.joint_id])
                )
                compensated_state = replace(state, joint_rotvec=updated_q)
                compensated = _prediction_vector(model, compensated_state, perturbed_static)
                compensated_change = float(np.max(np.abs(compensated - baseline)))
                all_compensated.append(compensated_change)
                sign_rows.append({
                    "sign": int(sign), "perturbation_deg": float(sign),
                    "r2_rotation_data_residual_change_l2": 0.0,
                    "r2_geometry_data_residual_change_l2": 0.0,
                    "r2_complete_data_jacobian_column_norm": 0.0,
                    "prior_residual_change_l2": float(step / np.pi),
                    "shared_fk_fixed_dynamic_state_output_change_linf": float(np.max(np.abs(fixed - baseline))),
                    "shared_fk_compensated_output_change_linf": compensated_change,
                })
            coordinate_rows.append({"axis": axis_name, "tests": sign_rows})
        slots.append({
            "slot_id": joint.rest_rotation_slot,
            "joint": joint.joint_id,
            "classification": "MIXED_OBJECTIVE_DISCONNECTED_AND_EXACT_DYNAMIC_GAUGE",
            "coordinates": coordinate_rows,
            "r2_objective_path": {
                "rotation_layer": "absent from data_residual; prior only",
                "geometry_layer": "joint_rest coordinates excluded from the local optimization vector",
                "shared_fk": "present when dynamic q is fixed",
                "dynamic_state_construction": "q was re-derived from observed parent/child rotation and exactly cancelled rest",
            },
        })
    return {
        "schema": "biospur-root-r6a2b-r3-joint-rest-gauge-causal-trace-v1",
        "cause": "MIXED",
        "before_data_rank": 87, "before_data_nullity": 27,
        "tested_slots": slots,
        "gauge_transformation": {
            "reference": "R_rest' = R_rest Exp(delta)",
            "dynamic": "Exp(q'(t)) = Exp(-delta) Exp(q(t))",
            "composition": "R_rest' Exp(q'(t)) = R_rest Exp(q(t))",
        },
        "maximum_compensated_complete_output_change_linf": float(max(all_compensated)),
        "interpretation": (
            "R2 did not evaluate joint_rest in either data-layer parameter vector.  The canonical FK does use it, "
            "but re-deriving every dynamic q from the same observed relative rotations realizes the exact constant-rotation gauge."
        ),
    }


def geometry_causal_trace(
    model: BodyModel,
    adapter: CanonicalCalibrationAdapter,
    vector: np.ndarray,
    covariance: np.ndarray,
    geometry_jacobian: np.ndarray,
    replay_segment_rotation: np.ndarray,
    replay_windows: np.ndarray,
) -> dict[str, Any]:
    values = adapter.vector_to_slots(vector)
    geometry_coordinates = adapter.geometry_coordinates()
    coordinate_to_local = {int(global_index): local for local, global_index in enumerate(geometry_coordinates)}
    fitted = {
        "upper_arm_left": "elbow_left", "upper_arm_right": "elbow_right",
        "thigh_left": "knee_left", "thigh_right": "knee_right",
    }
    diagnostic = {}
    for name, joint_id in fitted.items():
        joint = next(item for item in model.joints if item.joint_id == joint_id)
        block = adapter.by_slot[joint.parent_offset_slot]
        coordinates = values[joint.parent_offset_slot]
        data_norms = [
            float(np.linalg.norm(geometry_jacobian[:, coordinate_to_local[index]]))
            for index in range(block.start, block.stop)
        ]
        correlations = []
        std = np.sqrt(np.maximum(np.diag(covariance), 0.0))
        for own in range(block.start, block.stop):
            denominator = std[own] * std
            correlation = np.divide(covariance[own], denominator,
                                    out=np.zeros_like(denominator), where=denominator > 0.0)
            for other in np.argsort(np.abs(correlation))[::-1]:
                if block.start <= other < block.stop:
                    continue
                other_block = next(item for item in adapter.blocks if item.start <= other < item.stop)
                if other_block.kind not in ("imu_extrinsic", "joint_parent"):
                    continue
                correlations.append({
                    "coordinate": coordinate_label(adapter, int(own)),
                    "with": coordinate_label(adapter, int(other)),
                    "correlation": float(correlation[other]),
                })
                break
        diagnostic[name] = {
            "value_m": float(np.linalg.norm(coordinates)),
            "status": "DIAGNOSTIC_UNQUALIFIED_CONFUNDED_OFFSET_NORM",
            "parent_joint": joint_id, "parent_segment": joint.parent,
            "child_segment_or_anatomical_endpoint": joint.child,
            "exact_source_coordinates": coordinates.tolist(),
            "coordinate_frame": f"{joint.parent} local segment frame",
            "transform_chain": (
                f"world_T_{joint.parent} * joint_parent:{joint_id}; child origin is the zero-DOF "
                f"joint_child:{joint_id} view"
            ),
            "units": "metres", "owning_calibration_slot": joint.parent_offset_slot,
            "prior_value_m": [0.0, 0.0, 0.0],
            "prior_one_sigma_m": [0.2, 0.2, 0.2],
            "fitted_coordinates_m": coordinates.tolist(),
            "residual_influence": {
                "UWB_pairwise_distance_column_norms": data_norms,
                "IMU_shared_FK": "frozen segment rotations define the transform coefficients; no independent metric residual",
                "geometry_prior_standardized_coordinates": (coordinates / 0.2).tolist(),
            },
            "largest_posterior_correlations_showing_geometry_translation_confounding": correlations,
        }
    first_frame_angles = []
    for action in dict.fromkeys(str(value) for value in replay_windows):
        first = int(np.flatnonzero(replay_windows == action)[0])
        first_frame_angles.extend(
            float(np.linalg.norm(so3_log(rotation))) for rotation in replay_segment_rotation[first]
        )
    singular = np.linalg.svd(geometry_jacobian, compute_uv=False)
    return {
        "schema": "biospur-root-r6a2b-r3-metric-geometry-causal-trace-v1",
        "status": "DIAGNOSED_PENDING_MEASUREMENTS",
        "reported_bone_length_definitions": diagnostic,
        "causal_finding": {
            "primary": (
                "R2 reset every node/segment orientation independently to identity at the first replay frame of every action. "
                "Layer C therefore had no measured cross-segment reference-pose attitude and fitted static joint-parent and "
                "IMU-translation coordinates against UWB node separations; arm offsets absorbed unmodelled pose/lever structure."
            ),
            "maximum_first_frame_segment_rotation_angle_rad": float(max(first_frame_angles)),
            "all_action_first_frames_identity": bool(max(first_frame_angles) <= 64.0 * np.finfo(float).eps),
            "geometry_jacobian_largest_singular_value": float(singular[0]),
            "geometry_jacobian_smallest_singular_value": float(singular[-1]),
            "geometry_jacobian_condition_number": float(singular[0] / singular[-1]),
        },
        "explicit_checks": {
            "wrong_parent_child_ownership": {"found": False, "evidence": "canonical BodyModel joint ownership used"},
            "frame_inversion": {"found": False, "evidence": "parent-local offset transformed once by parent pose"},
            "duplicated_translation": {
                "found": False,
                "evidence": "no algebraic duplicate slot; strong practical confounding remains between joint-parent and IMU translations",
            },
            "device_centre_versus_imu_origin_confusion": {
                "found": False,
                "evidence": "component-reference lever is composed through the IMU extrinsic once",
            },
            "tag_lever_absorption": {
                "found": True,
                "evidence": "RF phase centres are bounded covariance nuisances, not estimated physical phase centres",
            },
            "metres_millimetres_conversion": {
                "found": False,
                "evidence": "canonical T4 frontend returns xyz_m and Layer C consumes metres",
            },
            "surface_landmark_used_as_joint_centre": {
                "found": False,
                "evidence": "no operator surface landmark entered R2; this absence prevents qualification",
            },
            "optimizer_compensation_for_unresolved_rf_phase_centres": {
                "found": True,
                "evidence": "only bounded component-reference uncertainty was present; exact RF phase centres remain null",
            },
            "derived_geometry_freedom": {
                "found": False,
                "evidence": "length is already derived as norm(joint_parent), not independently optimized",
            },
        },
        "repair": {
            "model": "session-relative orientations remain executable through shared FK",
            "serialization": "bone-length slots are null/pending in R3 profile; raw fitted norms live only in this diagnostic",
            "metric_skeleton_qualified": False,
            "synthetic_anthropometry_used": False,
        },
    }


def deferred_anthropometry_schema() -> dict[str, Any]:
    scalar_names = (
        "body_height", "shoulder_width", "ASIS_width", "greater_trochanter_width",
        "shoulder_to_hip_surface_landmark_distance_left",
        "shoulder_to_hip_surface_landmark_distance_right",
        "upper_arm_landmark_length_left", "upper_arm_landmark_length_right",
        "forearm_landmark_length_left", "forearm_landmark_length_right",
        "thigh_landmark_length_left", "thigh_landmark_length_right",
        "shank_landmark_length_left", "shank_landmark_length_right",
        "barefoot_conditioned_standing_height", "footwear_conditioned_standing_height",
    )
    measurement_template = {
        "value_m": None, "measurement_uncertainty_one_sigma_m": None,
        "landmark_definition": None, "repeat_measurements_m": [],
        "operator_notes": None, "photographs": [], "status": "PENDING_OPERATOR_MEASUREMENT",
    }
    pending = {name: dict(measurement_template) for name in scalar_names}
    pending["device_centre_to_nearest_joint_distances"] = {
        node: {**measurement_template, "nearest_joint": None} for node in NODES
    }
    pending["limb_circumferences_at_device_height"] = {
        node: dict(measurement_template) for node in NODES
    }
    pending["footwear"] = {
        "same_footwear_for_all_capture1_actions": True,
        "heel_height_m": None, "sole_thickness_m": None,
        "measurement_uncertainty_one_sigma_m": None,
        "operator_notes": None, "photographs": [], "status": "PENDING_OPERATOR_MEASUREMENT",
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "schema": "biospur-root-r6a2b-r3-deferred-anthropometry-input-v1",
        "purpose": "future measured inputs; nulls do not block orientation/session-reference calibration",
        "units": "SI metres",
        "measurement_object_contract": {
            "value_m": "number or null", "measurement_uncertainty_one_sigma_m": "nonnegative number or null",
            "landmark_definition": "string or null", "repeat_measurements_m": "array of numbers",
            "operator_notes": "string or null", "photographs": "array of repository-relative evidence paths",
        },
        "pending_input_template": pending,
        "all_measurement_values_pending": True,
        "synthetic_or_population_average_values_permitted": False,
    }
