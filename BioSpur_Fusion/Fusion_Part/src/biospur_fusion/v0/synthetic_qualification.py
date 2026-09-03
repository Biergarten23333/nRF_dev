"""Auditable Milestone-B qualification helpers for unified calibration."""
from __future__ import annotations

import hashlib
import math
from typing import Any, Callable, Iterable, Mapping

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_multi_action_revision_d.d0b_r1_generator import ACTIONS, SEGMENTS
from biospur_fusion.imu_multi_action_revision_d.d0b_r1_model import angles_from_axis

from .unified_calibration import (
    B5_LEVER_ENDPOINTS,
    FULL_DIMENSION,
    FUNCTIONAL_JOINTS,
    PRODUCT_DIMENSION,
    PRODUCT_LAYOUT,
    POSE_NUISANCE_DIMENSION,
    ZERO_JOINTS,
    UnifiedCalibrationObjective,
    decode_product,
    profiled_product_observability,
    production_jacobian,
    wrap_angle,
)


B3_HIP_CIRCUMDUCTION = "b3_bilateral_hip_circumduction_two_axis"
B3_KNEE_LEFT = "b3_left_seated_knee_flexion_tibial_axial"
B3_KNEE_RIGHT = "b3_right_seated_knee_flexion_tibial_axial"
B3_TRUNK_LATERAL = "b3_trunk_labelled_left_right_lateral_bend"
B3_ACTIONS = {
    B3_HIP_CIRCUMDUCTION,
    B3_KNEE_LEFT,
    B3_KNEE_RIGHT,
    B3_TRUNK_LATERAL,
}
B4_EN_BLOC = "b4_supported_braced_en_bloc_two_axis"


def array_hash(value: np.ndarray) -> str:
    array = np.ascontiguousarray(value)
    digest = hashlib.sha256()
    digest.update(array.dtype.str.encode())
    digest.update(np.asarray(array.shape, dtype="<i8").tobytes())
    digest.update(array.tobytes())
    return digest.hexdigest()


def _rank(values: np.ndarray, relative: float = 1e-7, absolute: float = 1e-8) -> tuple[int, np.ndarray]:
    singular = np.linalg.svd(np.asarray(values, dtype=float), compute_uv=False)
    threshold = max(absolute, float(singular[0]) * relative if len(singular) else 0.0)
    return int(np.sum(singular > threshold)), singular


def profiled_subspace(jacobian: np.ndarray, target: np.ndarray, profile: np.ndarray) -> dict[str, Any]:
    matrix = np.asarray(jacobian, dtype=float)
    interest = matrix[:, target]
    nuisance = matrix[:, profile]
    if nuisance.shape[1]:
        u, singular, _ = np.linalg.svd(nuisance, full_matrices=False)
        nuisance_rank, _ = _rank(nuisance)
        basis = u[:, :nuisance_rank]
        interest = interest - basis @ (basis.T @ interest)
    else:
        singular = np.empty(0)
        nuisance_rank = 0
    rank, spectrum = _rank(interest)
    return {
        "rank": rank,
        "nullity": len(target) - rank,
        "singular_values": spectrum,
        "nuisance_rank": nuisance_rank,
        "nuisance_singular_values": singular,
    }


def block_row_indices(blocks: Iterable[Any], predicate: Callable[[Any], bool]) -> np.ndarray:
    blocks = list(blocks)
    offsets = np.cumsum([0] + [len(block.values) for block in blocks])
    selected = [np.arange(offsets[index], offsets[index + 1]) for index, block in enumerate(blocks) if predicate(block)]
    return np.concatenate(selected) if selected else np.empty(0, dtype=int)


def rank_record(jacobian: np.ndarray, rows: np.ndarray, bottom: int) -> dict[str, Any]:
    selected = jacobian[rows]
    profile = profiled_product_observability(selected)
    heading_target = np.arange(20, 29)
    heading_other = np.r_[np.arange(0, 20), np.arange(29, FULL_DIMENSION)]
    heading_nuisance = np.arange(PRODUCT_DIMENSION, FULL_DIMENSION)
    conditional = profiled_subspace(selected, heading_target, heading_other)
    nuisance_only = profiled_subspace(selected, heading_target, heading_nuisance)
    bottom_directions = []
    for singular_index in range(max(0, len(profile["singular_values"]) - bottom), len(profile["singular_values"])):
        vector = profile["right_vectors"][singular_index]
        energy = [
            {
                "coordinate_block": entry["name"],
                "state_family": entry["block"],
                "energy_fraction": float(np.sum(vector[entry["start"]:entry["stop"]] ** 2)),
            }
            for entry in PRODUCT_LAYOUT
        ]
        bottom_directions.append({
            "singular_value": float(profile["singular_values"][singular_index]),
            "identified_by_stage": bool(profile["singular_values"][singular_index] > profile["threshold"]),
            "dominant_coordinate_blocks": sorted(
                energy, key=lambda item: item["energy_fraction"], reverse=True,
            )[:8],
        })
    return {
        "row_count": int(len(rows)),
        "profiled_product_rank": int(profile["rank"]),
        "profiled_product_nullity": int(profile["nullity"]),
        "nuisance_rank": int(profile["nuisance_rank"]),
        "bottom_product_singular_values": profile["singular_values"][-bottom:],
        "bottom_product_direction_lineage": bottom_directions,
        "heading_conditioned_on_all_other_coordinates": {
            "rank": conditional["rank"], "nullity": conditional["nullity"],
            "singular_values": conditional["singular_values"],
        },
        "heading_profiled_only_against_pose_nuisance": {
            "rank": nuisance_only["rank"], "nullity": nuisance_only["nullity"],
            "singular_values": nuisance_only["singular_values"],
        },
    }


def _vector_angle_deg(first: np.ndarray, second: np.ndarray) -> float:
    return math.degrees(math.acos(float(np.clip(np.asarray(first) @ np.asarray(second), -1.0, 1.0))))


def product_state_error(truth: np.ndarray, candidate: np.ndarray) -> dict[str, Any]:
    """Truth-side qualification only; never exposed to the estimator API."""

    reference = decode_product(truth)
    fitted = decode_product(candidate)
    return {
        "sensor_axis_error_deg": {
            segment: _vector_angle_deg(reference["axes"][segment], fitted["axes"][segment])
            for segment in SEGMENTS
        },
        "relative_heading_error_deg": {
            segment: math.degrees(float(wrap_angle(
                fitted["headings"][segment] - reference["headings"][segment]
            )))
            for segment in SEGMENTS[1:]
        },
        "functional_axis_error_deg": {
            joint: _vector_angle_deg(reference["functional"][joint], fitted["functional"][joint])
            for joint in FUNCTIONAL_JOINTS
        },
        "trunk_frame_geodesic_error_deg": math.degrees(float(np.linalg.norm(
            Rotation.from_matrix(reference["trunk_frame"].T @ fitted["trunk_frame"]).as_rotvec()
        ))),
        "joint_zero_error_deg": {
            joint: math.degrees(float(wrap_angle(
                fitted["zeros"][joint] - reference["zeros"][joint]
            )))
            for joint in ZERO_JOINTS
        },
    }


def rank_lineage(objective: UnifiedCalibrationObjective, x: np.ndarray, bottom: int = 12) -> dict[str, Any]:
    blocks = objective.blocks(x, True)
    jacobian = production_jacobian(objective, x, True)
    raw = lambda block: block.classification == "MEASURED_OBSERVATION"
    zero = lambda block: block.factor.startswith("capture_defined_neutral_zero")
    static = lambda block: block.factor.startswith("articulated_static_direction")
    semantic = lambda block: block.factor.startswith("soft_")
    prior = lambda block: block.classification == "PARAMETER_ONLY_PRIOR"
    stages = (
        ("RAW_TIME_RESOLVED_MEASUREMENT", raw),
        ("RAW_PLUS_MEASURED_NEUTRAL_ZERO_DEFINITIONS", lambda b: raw(b) or zero(b)),
        ("RAW_ZERO_PLUS_SOFT_LATENT_STATIC_POSES", lambda b: raw(b) or zero(b) or static(b)),
        ("ALL_MEASUREMENT_AND_SOFT_PROTOCOL", lambda b: not prior(b)),
        ("ALL_PLUS_PARAMETER_ONLY_POSE_PRIORS", lambda b: True),
    )
    records = {name: rank_record(jacobian, block_row_indices(blocks, predicate), bottom) for name, predicate in stages}
    base_rows = block_row_indices(blocks, lambda b: raw(b) or zero(b))
    base_rank = rank_record(jacobian, base_rows, bottom)["profiled_product_rank"]
    soft_lineage = []
    all_measurement_rows = block_row_indices(blocks, lambda b: not prior(b))
    all_measurement_rank = rank_record(jacobian, all_measurement_rows, bottom)["profiled_product_rank"]
    for index, block in enumerate(blocks):
        if block.classification == "MEASURED_OBSERVATION" or zero(block):
            continue
        offsets = np.cumsum([0] + [len(item.values) for item in blocks])
        rows = np.r_[base_rows, np.arange(offsets[index], offsets[index + 1])]
        rank = rank_record(jacobian, rows, bottom)["profiled_product_rank"]
        leave_out_rows = block_row_indices(
            blocks,
            lambda candidate, omitted=block: not prior(candidate) and candidate is not omitted,
        )
        leave_out_rank = rank_record(jacobian, leave_out_rows, bottom)["profiled_product_rank"]
        soft_lineage.append({
            "classification": block.classification,
            "action": block.action,
            "factor": block.factor,
            "row_count": len(block.values),
            "rank_with_raw_and_zero_base": rank,
            "incremental_rank": rank - base_rank,
            "all_measurement_leave_one_block_out_rank": leave_out_rank,
            "leave_one_block_out_rank_loss": all_measurement_rank - leave_out_rank,
        })
    soft_groups = {
        "SOFT_STATIC_INITIAL": lambda b: static(b) and b.action == "initial_still_attempt2",
        "SOFT_STATIC_TPOSE": lambda b: static(b) and b.action == "t_pose",
        "SOFT_FUNCTIONAL_AND_HANDEDNESS_SEMANTICS": semantic,
        "PARAMETER_ONLY_POSE_PRIORS": prior,
    }
    soft_group_lineage = []
    for name, group in soft_groups.items():
        rows = block_row_indices(blocks, lambda b, group=group: raw(b) or zero(b) or group(b))
        addition = rank_record(jacobian, rows, bottom)["profiled_product_rank"]
        leave_out = block_row_indices(blocks, lambda b, group=group: not prior(b) and not group(b))
        leave_out_rank = rank_record(jacobian, leave_out, bottom)["profiled_product_rank"]
        soft_group_lineage.append({
            "group": name,
            "rank_with_raw_and_zero_base": addition,
            "incremental_rank": addition - base_rank,
            "all_measurement_leave_group_out_rank": leave_out_rank,
            "leave_group_out_rank_loss": all_measurement_rank - leave_out_rank,
        })
    b5_rows = block_row_indices(
        blocks, lambda block: block.factor.startswith("b5_joint_center_specific_force_closure"),
    )
    lever_target = np.arange(
        PRODUCT_DIMENSION + POSE_NUISANCE_DIMENSION,
        FULL_DIMENSION,
    )
    lever_profile = np.arange(0, PRODUCT_DIMENSION + POSE_NUISANCE_DIMENSION)
    b5_lever = profiled_subspace(jacobian[b5_rows], lever_target, lever_profile)
    return {
        "stages": records,
        "soft_block_lineage": soft_lineage,
        "soft_group_lineage": soft_group_lineage,
        "parameter_only_priors_add_rank": (
            records["ALL_PLUS_PARAMETER_ONLY_POSE_PRIORS"]["profiled_product_rank"]
            > records["ALL_MEASUREMENT_AND_SOFT_PROTOCOL"]["profiled_product_rank"]
        ),
        "b5_lever_arm_measurement_subspace": {
            "coordinate_count": 3 * len(B5_LEVER_ENDPOINTS),
            "rank": b5_lever["rank"],
            "nullity": b5_lever["nullity"],
            "singular_values": b5_lever["singular_values"],
            "bounds_or_priors_entered_rows": False,
        },
        "jacobian": jacobian,
        "blocks": blocks,
    }


def optimal_common_yaw(predicted: np.ndarray, truth: np.ndarray) -> float:
    a = float(np.sum(predicted[..., :2] * truth[..., :2]))
    b = float(np.sum(predicted[..., 0] * truth[..., 1] - predicted[..., 1] * truth[..., 0]))
    return math.atan2(b, a)


def apply_yaw(vectors: np.ndarray, angle: float) -> np.ndarray:
    result = np.asarray(vectors, dtype=float).copy()
    cosine, sine = math.cos(angle), math.sin(angle)
    x, y = result[..., 0].copy(), result[..., 1].copy()
    result[..., 0] = cosine * x - sine * y
    result[..., 1] = sine * x + cosine * y
    return result


def corrected_directions(objective: UnifiedCalibrationObjective, x: np.ndarray) -> np.ndarray:
    product = decode_product(x)
    rows = np.arange(len(objective.obs.time_ns))
    return np.stack([objective.corrected_direction(product, segment, rows) for segment in SEGMENTS], axis=1)


def recovery_record(objective: UnifiedCalibrationObjective, x: np.ndarray, truth_directions: np.ndarray) -> dict[str, Any]:
    predicted = corrected_directions(objective, x)
    gauge = optimal_common_yaw(predicted, truth_directions)
    aligned = apply_yaw(predicted, gauge)
    angles = np.degrees(np.arccos(np.clip(np.sum(aligned * truth_directions, axis=-1), -1.0, 1.0)))
    return {
        "legal_common_yaw_alignment_rad": gauge,
        "segment_direction_rmse_deg": float(np.sqrt(np.mean(angles * angles))),
        "segment_direction_q90_deg": float(np.quantile(angles, 0.90)),
        "segment_direction_max_deg": float(np.max(angles)),
        "per_segment_rmse_deg": {
            segment: float(np.sqrt(np.mean(angles[:, index] ** 2)))
            for index, segment in enumerate(SEGMENTS)
        },
        "aligned_direction_sha256": array_hash(aligned),
    }


def maximum_profile_disagreement(objective: UnifiedCalibrationObjective, values: list[np.ndarray]) -> float:
    reference = corrected_directions(objective, values[0])
    maximum = 0.0
    for value in values[1:]:
        candidate = corrected_directions(objective, value)
        aligned = apply_yaw(candidate, optimal_common_yaw(candidate, reference))
        angle = np.degrees(np.arccos(np.clip(np.sum(aligned * reference, axis=-1), -1.0, 1.0)))
        maximum = max(maximum, float(np.max(angle)))
    return maximum


def directional_jv_check(objective: UnifiedCalibrationObjective, x: np.ndarray, jacobian: np.ndarray) -> dict[str, float]:
    direction = np.sin(np.arange(FULL_DIMENSION, dtype=float) + 0.37)
    direction /= np.linalg.norm(direction)
    step = 2e-4
    finite = (
        -objective.residual(x + 2 * step * direction, False)
        + 8 * objective.residual(x + step * direction, False)
        - 8 * objective.residual(x - step * direction, False)
        + objective.residual(x - 2 * step * direction, False)
    ) / (12 * step)
    analytic = jacobian @ direction
    relative = float(np.linalg.norm(finite - analytic) / max(np.linalg.norm(finite), 1e-12))
    return {"relative_error": relative, "finite_norm": float(np.linalg.norm(finite)), "jacobian_norm": float(np.linalg.norm(analytic))}


def action_ablations(objective: UnifiedCalibrationObjective, lineage: Mapping[str, Any], bottom: int = 12) -> list[dict[str, Any]]:
    blocks = lineage["blocks"]
    jacobian = lineage["jacobian"]
    included = lambda block: block.classification != "PARAMETER_ONLY_PRIOR"
    families = {
        "STATIC_INITIAL": {"initial_still_attempt2"},
        "STATIC_TPOSE": {"t_pose"},
        "BILATERAL_ARMS": {"arms"},
        "COMPOUND_ELBOWS": {"left_elbow", "right_elbow_attempt2"},
        "UNILATERAL_HIPS": {"left_knee", "right_knee", B3_HIP_CIRCUMDUCTION},
        "UNILATERAL_KNEES": {"left_heel", "right_heel", B3_KNEE_LEFT, B3_KNEE_RIGHT},
        "BILATERAL_SQUAT": {"squats"},
        "TRUNK_NONCOLLINEAR": {"trunk", B3_TRUNK_LATERAL},
        "B3_HIP_CIRCUMDUCTION_ONLY": {B3_HIP_CIRCUMDUCTION},
        "B3_KNEE_FLEXION_AXIAL_ONLY": {B3_KNEE_LEFT, B3_KNEE_RIGHT},
        "B3_TRUNK_LATERAL_LABELLED_ONLY": {B3_TRUNK_LATERAL},
        "B4_EN_BLOC_COMMON_RATE": {B4_EN_BLOC},
    }
    baseline_rows = block_row_indices(blocks, included)
    baseline = rank_record(jacobian, baseline_rows, bottom)
    baseline_singular = np.linalg.svd(
        profiled_product_observability(jacobian[baseline_rows])["effective_jacobian"], compute_uv=False,
    )
    baseline_log_information = float(2.0 * np.sum(np.log(baseline_singular[:baseline["profiled_product_rank"]])))
    records = []
    for family, actions in families.items():
        rows = block_row_indices(blocks, lambda block: included(block) and block.action not in actions)
        record = rank_record(jacobian, rows, bottom)
        removed_profile = profiled_product_observability(jacobian[rows])
        removed_singular = np.asarray(removed_profile["singular_values"])
        removed_rank = int(removed_profile["rank"])
        removed_log_information = float(2.0 * np.sum(np.log(removed_singular[:removed_rank])))
        baseline_minimum = float(baseline_singular[baseline["profiled_product_rank"] - 1])
        removed_minimum = float(removed_singular[removed_rank - 1]) if removed_rank else 0.0
        records.append({
            "family": family,
            "removed_actions": sorted(actions),
            "profiled_product_rank": record["profiled_product_rank"],
            "rank_loss": baseline["profiled_product_rank"] - record["profiled_product_rank"],
            "heading_rank": record["heading_conditioned_on_all_other_coordinates"]["rank"],
            "heading_rank_loss": baseline["heading_conditioned_on_all_other_coordinates"]["rank"] - record["heading_conditioned_on_all_other_coordinates"]["rank"],
            "bottom_product_singular_values": record["bottom_product_singular_values"],
            "smallest_identifiable_singular_value": removed_minimum,
            "smallest_singular_loss_fraction": (
                1.0 if removed_rank < baseline["profiled_product_rank"]
                else 1.0 - removed_minimum / max(baseline_minimum, 1e-15)
            ),
            "log_information_loss": baseline_log_information - removed_log_information,
        })
    rows = block_row_indices(
        blocks,
        lambda block: included(block)
        and not block.factor.startswith("b5_joint_center_specific_force_closure"),
    )
    record = rank_record(jacobian, rows, bottom)
    removed_profile = profiled_product_observability(jacobian[rows])
    removed_singular = np.asarray(removed_profile["singular_values"])
    removed_rank = int(removed_profile["rank"])
    removed_minimum = float(removed_singular[removed_rank - 1]) if removed_rank else 0.0
    baseline_minimum = float(baseline_singular[baseline["profiled_product_rank"] - 1])
    records.append({
        "family": "B5_JOINT_CENTER_SPECIFIC_FORCE",
        "removed_actions": [],
        "removed_factor_prefixes": ["b5_joint_center_specific_force_closure"],
        "profiled_product_rank": record["profiled_product_rank"],
        "rank_loss": baseline["profiled_product_rank"] - record["profiled_product_rank"],
        "heading_rank": record["heading_conditioned_on_all_other_coordinates"]["rank"],
        "heading_rank_loss": baseline["heading_conditioned_on_all_other_coordinates"]["rank"] - record["heading_conditioned_on_all_other_coordinates"]["rank"],
        "bottom_product_singular_values": record["bottom_product_singular_values"],
        "smallest_identifiable_singular_value": removed_minimum,
        "smallest_singular_loss_fraction": (
            1.0 if removed_rank < baseline["profiled_product_rank"]
            else 1.0 - removed_minimum / max(baseline_minimum, 1e-15)
        ),
        "log_information_loss": baseline_log_information - float(
            2.0 * np.sum(np.log(removed_singular[:removed_rank]))
        ),
    })
    return records


def _product_target_indices(family: Mapping[str, Any]) -> np.ndarray:
    names = set(family.get("target_coordinate_names", ()))
    blocks = set(family.get("target_coordinate_blocks", ()))
    selected: list[int] = []
    for entry in PRODUCT_LAYOUT:
        if entry["name"] in names or entry["block"] in blocks:
            selected.extend(range(int(entry["start"]), int(entry["stop"])))
    missing = names - {entry["name"] for entry in PRODUCT_LAYOUT}
    if missing:
        raise ValueError(f"unknown product coordinates in causality contract: {sorted(missing)}")
    return np.asarray(sorted(set(selected)), dtype=int)


def _family_block_predicate(family: Mapping[str, Any]) -> Callable[[Any], bool]:
    removed_actions = set(family.get("remove_actions", ()))
    removed_prefixes = tuple(family.get("remove_factor_prefixes", ()))

    def retained(block: Any) -> bool:
        return (
            block.classification != "PARAMETER_ONLY_PRIOR"
            and block.action not in removed_actions
            and not any(block.factor.startswith(prefix) for prefix in removed_prefixes)
        )

    return retained


def _sign_alternative_states(x: np.ndarray) -> dict[str, np.ndarray]:
    alternatives: dict[str, np.ndarray] = {}
    for joint_index, joint in enumerate(FUNCTIONAL_JOINTS):
        candidate = x.copy()
        start = 29 + 2 * joint_index
        axis = -decode_product(candidate)["functional"][joint]
        candidate[start:start + 2] = angles_from_axis(axis)
        if joint in ZERO_JOINTS:
            zero_index = ZERO_JOINTS.index(joint)
            candidate[48 + zero_index] = -candidate[48 + zero_index]
        alternatives[f"flip_functional_axis:{joint}"] = candidate
    product = decode_product(x)
    for label, signs in (
        ("flip_trunk_flex_and_lateral", np.diag([-1.0, -1.0, 1.0])),
        ("flip_trunk_lateral_and_axial", np.diag([1.0, -1.0, -1.0])),
    ):
        candidate = x.copy()
        candidate[45:48] = Rotation.from_matrix(product["trunk_frame"] @ signs).as_rotvec()
        alternatives[label] = candidate
    return alternatives


def _selected_block_cost(
    objective: UnifiedCalibrationObjective,
    x: np.ndarray,
    predicate: Callable[[Any], bool],
) -> float:
    return float(0.5 * sum(
        np.sum(block.values * block.values)
        for block in objective.blocks(x, True)
        if predicate(block)
    ))


def multidimensional_action_causality(
    objective: UnifiedCalibrationObjective,
    lineage: Mapping[str, Any],
    contract: Mapping[str, Any],
    reference: np.ndarray,
    bottom: int = 12,
) -> dict[str, Any]:
    """Evaluate the predeclared action classes without a universal-minimum test.

    Residual rows have already been covariance- and action-normalized by the
    objective.  Product spectra are computed after profiling all nuisance
    coordinates.  A target spectrum then conditions the declared product
    coordinates on every other product coordinate in that nuisance-free row
    space.  Bounds and parameter-only priors never enter these calculations.
    """

    blocks = lineage["blocks"]
    jacobian = lineage["jacobian"]
    included = lambda block: block.classification != "PARAMETER_ONLY_PRIOR"
    baseline_rows = block_row_indices(blocks, included)
    baseline_record = rank_record(jacobian, baseline_rows, bottom)
    baseline_profile = profiled_product_observability(jacobian[baseline_rows])
    baseline_effective = np.asarray(baseline_profile["effective_jacobian"])
    baseline_singular = np.asarray(baseline_profile["singular_values"])
    baseline_rank = int(baseline_profile["rank"])
    baseline_log_information = float(2.0 * np.sum(np.log(baseline_singular[:baseline_rank])))
    alternatives = _sign_alternative_states(reference)
    baseline_cost = _selected_block_cost(objective, reference, included)
    thresholds = contract["thresholds"]
    records: list[dict[str, Any]] = []

    for family in contract["families"]:
        retained = _family_block_predicate(family)
        rows = block_row_indices(blocks, retained)
        removed_record = rank_record(jacobian, rows, bottom)
        removed_profile = profiled_product_observability(jacobian[rows])
        removed_effective = np.asarray(removed_profile["effective_jacobian"])
        removed_singular = np.asarray(removed_profile["singular_values"])
        removed_rank = int(removed_profile["rank"])
        removed_log_information = float(2.0 * np.sum(np.log(removed_singular[:removed_rank])))

        target = _product_target_indices(family)
        if len(target):
            other = np.setdiff1d(np.arange(PRODUCT_DIMENSION), target)
            baseline_target = profiled_subspace(baseline_effective, target, other)
            removed_target = profiled_subspace(removed_effective, target, other)
            baseline_target_rank = int(baseline_target["rank"])
            removed_target_rank = int(removed_target["rank"])
            baseline_target_minimum = (
                float(baseline_target["singular_values"][baseline_target_rank - 1])
                if baseline_target_rank else 0.0
            )
            removed_target_minimum = (
                float(removed_target["singular_values"][removed_target_rank - 1])
                if removed_target_rank else 0.0
            )
            targeted_loss = (
                1.0
                if removed_target_rank < baseline_target_rank
                else 1.0 - removed_target_minimum / max(baseline_target_minimum, 1e-15)
            )
            target_record: dict[str, Any] = {
                "coordinate_indices": target,
                "coordinate_count": len(target),
                "baseline_conditional_rank": baseline_target_rank,
                "removed_conditional_rank": removed_target_rank,
                "conditional_rank_loss": baseline_target_rank - removed_target_rank,
                "baseline_weakest_identifiable_singular_value": baseline_target_minimum,
                "removed_weakest_identifiable_singular_value": removed_target_minimum,
                "weakest_identifiable_singular_loss_fraction": targeted_loss,
                "baseline_bottom_singular_values": baseline_target["singular_values"][-bottom:],
                "removed_bottom_singular_values": removed_target["singular_values"][-bottom:],
            }
        else:
            target_record = {
                "coordinate_indices": [],
                "coordinate_count": 0,
                "not_evaluated": "DIAGNOSTIC_ONLY_NO_PRODUCT_TARGET",
            }

        sign_records = []
        for label in family.get("sign_alternatives", ()):
            if label not in alternatives:
                raise ValueError(f"unknown sign alternative in causality contract: {label}")
            removed_cost = _selected_block_cost(objective, alternatives[label], retained)
            sign_records.append({
                "alternative": label,
                "retained_row_delta_cost": removed_cost - _selected_block_cost(objective, reference, retained),
            })
        minimum_removed_sign_margin = (
            min(item["retained_row_delta_cost"] for item in sign_records)
            if sign_records else None
        )
        sign_margin_failure = bool(
            minimum_removed_sign_margin is not None
            and minimum_removed_sign_margin
            < float(thresholds["minimum_sign_handedness_margin_cost"])
        )

        rank_loss = baseline_rank - removed_rank
        heading_rank_loss = (
            baseline_record["heading_conditioned_on_all_other_coordinates"]["rank"]
            - removed_record["heading_conditioned_on_all_other_coordinates"]["rank"]
        )
        log_information_loss = (
            float("inf") if removed_rank < baseline_rank
            else baseline_log_information - removed_log_information
        )
        classification = family["classification"]
        core_reasons = {
            "global_rank_loss": rank_loss > 0,
            "nine_heading_conditional_rank_loss": heading_rank_loss > 0,
            "target_conditional_rank_loss": bool(
                len(target) and target_record["conditional_rank_loss"] > 0
            ),
            "targeted_weak_subspace_loss_at_least_2pct": bool(
                len(target)
                and target_record["weakest_identifiable_singular_loss_fraction"]
                >= float(thresholds["minimum_targeted_weak_subspace_loss_fraction"])
            ),
            "declared_sign_margin_failure": sign_margin_failure,
            "ablated_recovery_or_global_agreement_failure": False,
        }
        redundant_reasons = {
            "covariance_normalized_log_information_gain": bool(
                log_information_loss
                >= float(thresholds["minimum_redundant_log_information_gain_nats"])
            ),
            "held_out_noise_or_weak_motion_degradation": False,
        }
        if classification == "CORE_IDENTIFICATION":
            passed = any(core_reasons.values())
            necessity_claim = True
        elif classification == "REDUNDANT_ROBUSTNESS":
            passed = any(redundant_reasons.values())
            necessity_claim = True
        elif classification == "DIAGNOSTIC_ONLY":
            passed = True
            necessity_claim = False
        else:
            raise ValueError(f"unknown causality classification {classification}")

        records.append({
            "family": family["family"],
            "classification": classification,
            "residual_target": family["residual_target"],
            "removed_actions": sorted(family.get("remove_actions", ())),
            "removed_factor_prefixes": list(family.get("remove_factor_prefixes", ())),
            "profiled_product_rank": removed_rank,
            "rank_loss": rank_loss,
            "nine_heading_conditional_rank": removed_record["heading_conditioned_on_all_other_coordinates"]["rank"],
            "nine_heading_conditional_rank_loss": heading_rank_loss,
            "bottom_product_singular_values": removed_record["bottom_product_singular_values"],
            "covariance_normalized_profiled_product_log_information_loss_nats": log_information_loss,
            "targeted_product_subspace": target_record,
            "sign_margin_after_removal": {
                "minimum_delta_cost": minimum_removed_sign_margin,
                "alternatives": sign_records,
            },
            "core_identification_reasons": core_reasons,
            "redundant_robustness_reasons": redundant_reasons,
            "necessity_claim": necessity_claim,
            "pass": passed,
        })

    acceptance_records = [
        record for record in records if record["classification"] != "DIAGNOSTIC_ONLY"
    ]
    return {
        "schema": "biospur-pure-imu-v0-multidimensional-action-causality-v1",
        "legacy_universal_single_minimum_is_acceptance_gate": False,
        "parameter_only_priors_or_bounds_entered": False,
        "covariance_and_action_normalized_rows": True,
        "baseline_profiled_product_rank": baseline_rank,
        "baseline_nine_heading_conditional_rank": baseline_record[
            "heading_conditioned_on_all_other_coordinates"
        ]["rank"],
        "baseline_covariance_normalized_log_information": baseline_log_information,
        "baseline_selected_row_cost": baseline_cost,
        "records": records,
        "pass": all(record["pass"] for record in acceptance_records),
    }


def b3_information_gain(
    objective: UnifiedCalibrationObjective,
    lineage: Mapping[str, Any],
    bottom: int = 12,
) -> dict[str, Any]:
    """Separate new B3 measured information from the preserved B2 suite."""

    blocks = lineage["blocks"]
    jacobian = lineage["jacobian"]

    def stage(include_soft: bool, include_b3: bool) -> tuple[np.ndarray, dict[str, Any]]:
        rows = block_row_indices(
            blocks,
            lambda block: (
                block.classification != "PARAMETER_ONLY_PRIOR"
                and (include_soft or block.classification == "MEASURED_OBSERVATION")
                and (include_b3 or block.action not in B3_ACTIONS)
            ),
        )
        return rows, rank_record(jacobian, rows, bottom)

    output: dict[str, Any] = {}
    for label, include_soft in (("RAW_MEASURED", False), ("ALL_MEASUREMENT_AND_SOFT_PROTOCOL", True)):
        full_rows, full = stage(include_soft, True)
        b2_rows, b2 = stage(include_soft, False)
        full_profile = profiled_product_observability(jacobian[full_rows])
        b2_profile = profiled_product_observability(jacobian[b2_rows])
        full_singular = np.asarray(full_profile["singular_values"])
        b2_singular = np.asarray(b2_profile["singular_values"])
        full_rank = int(full_profile["rank"])
        b2_rank = int(b2_profile["rank"])
        full_minimum = float(full_singular[full_rank - 1]) if full_rank else 0.0
        b2_minimum = float(b2_singular[b2_rank - 1]) if b2_rank else 0.0
        output[label] = {
            "with_b3": full,
            "without_b3": b2,
            "profiled_product_rank_gain": full_rank - b2_rank,
            "heading_rank_gain": (
                full["heading_conditioned_on_all_other_coordinates"]["rank"]
                - b2["heading_conditioned_on_all_other_coordinates"]["rank"]
            ),
            "smallest_identifiable_singular_with_b3": full_minimum,
            "smallest_identifiable_singular_without_b3": b2_minimum,
            "smallest_singular_information_gain_fraction": (
                full_minimum / max(b2_minimum, 1e-15) - 1.0
                if full_rank == b2_rank else float("inf")
            ),
        }

    offsets = np.cumsum([0] + [len(block.values) for block in blocks])
    output["factor_lineage"] = [
        {
            "action": block.action,
            "factor": block.factor,
            "classification": block.classification,
            "row_count": len(block.values),
            "parameter_blocks": list(block.parameter_blocks),
            "robust_cost_at_reference": float(np.sum(np.sqrt(1.0 + block.values * block.values) - 1.0)),
            "jacobian_frobenius_norm": float(np.linalg.norm(jacobian[offsets[index]:offsets[index + 1]])),
        }
        for index, block in enumerate(blocks)
        if block.action in B3_ACTIONS
    ]
    return output


def b4_information_gain(
    objective: UnifiedCalibrationObjective,
    lineage: Mapping[str, Any],
    bottom: int = 12,
) -> dict[str, Any]:
    """Separate the B4 measurement-only common-rate contribution."""

    blocks = lineage["blocks"]
    jacobian = lineage["jacobian"]

    def stage(include_soft: bool, include_b4: bool) -> tuple[np.ndarray, dict[str, Any]]:
        rows = block_row_indices(
            blocks,
            lambda block: (
                block.classification != "PARAMETER_ONLY_PRIOR"
                and (include_soft or block.classification == "MEASURED_OBSERVATION")
                and (include_b4 or block.action != B4_EN_BLOC)
            ),
        )
        return rows, rank_record(jacobian, rows, bottom)

    output: dict[str, Any] = {}
    for label, include_soft in (("RAW_MEASURED", False), ("ALL_MEASUREMENT_AND_SOFT_PROTOCOL", True)):
        full_rows, full = stage(include_soft, True)
        prior_rows, prior = stage(include_soft, False)
        full_profile = profiled_product_observability(jacobian[full_rows])
        prior_profile = profiled_product_observability(jacobian[prior_rows])
        full_singular = np.asarray(full_profile["singular_values"])
        prior_singular = np.asarray(prior_profile["singular_values"])
        full_rank = int(full_profile["rank"])
        prior_rank = int(prior_profile["rank"])
        full_minimum = float(full_singular[full_rank - 1]) if full_rank else 0.0
        prior_minimum = float(prior_singular[prior_rank - 1]) if prior_rank else 0.0
        output[label] = {
            "with_b4": full,
            "without_b4": prior,
            "profiled_product_rank_gain": full_rank - prior_rank,
            "heading_rank_gain": (
                full["heading_conditioned_on_all_other_coordinates"]["rank"]
                - prior["heading_conditioned_on_all_other_coordinates"]["rank"]
            ),
            "smallest_identifiable_singular_with_b4": full_minimum,
            "smallest_identifiable_singular_without_b4": prior_minimum,
            "smallest_singular_information_gain_fraction": (
                full_minimum / max(prior_minimum, 1e-15) - 1.0
                if full_rank == prior_rank else float("inf")
            ),
        }

    offsets = np.cumsum([0] + [len(block.values) for block in blocks])
    output["factor_lineage"] = [
        {
            "action": block.action,
            "factor": block.factor,
            "classification": block.classification,
            "row_count": len(block.values),
            "parameter_blocks": list(block.parameter_blocks),
            "robust_cost_at_reference": float(np.sum(np.sqrt(1.0 + block.values * block.values) - 1.0)),
            "jacobian_frobenius_norm": float(np.linalg.norm(jacobian[offsets[index]:offsets[index + 1]])),
        }
        for index, block in enumerate(blocks)
        if block.action == B4_EN_BLOC
    ]
    return output


def b5_information_gain(
    objective: UnifiedCalibrationObjective,
    lineage: Mapping[str, Any],
    bottom: int = 12,
) -> dict[str, Any]:
    """Separate B5 six-axis information after profiling all lever nuisances."""

    blocks = lineage["blocks"]
    jacobian = lineage["jacobian"]
    is_b5 = lambda block: block.factor.startswith("b5_joint_center_specific_force_closure")
    output: dict[str, Any] = {}
    for label, include_soft in (("RAW_MEASURED", False), ("ALL_MEASUREMENT_AND_SOFT_PROTOCOL", True)):
        full_rows = block_row_indices(
            blocks,
            lambda block: block.classification != "PARAMETER_ONLY_PRIOR"
            and (include_soft or block.classification == "MEASURED_OBSERVATION"),
        )
        prior_rows = block_row_indices(
            blocks,
            lambda block: block.classification != "PARAMETER_ONLY_PRIOR"
            and (include_soft or block.classification == "MEASURED_OBSERVATION")
            and not is_b5(block),
        )
        full = rank_record(jacobian, full_rows, bottom)
        prior = rank_record(jacobian, prior_rows, bottom)
        full_profile = profiled_product_observability(jacobian[full_rows])
        prior_profile = profiled_product_observability(jacobian[prior_rows])
        full_rank, prior_rank = int(full_profile["rank"]), int(prior_profile["rank"])
        full_minimum = float(full_profile["singular_values"][full_rank - 1]) if full_rank else 0.0
        prior_minimum = float(prior_profile["singular_values"][prior_rank - 1]) if prior_rank else 0.0
        output[label] = {
            "with_b5": full,
            "without_b5": prior,
            "profiled_product_rank_gain": full_rank - prior_rank,
            "heading_rank_gain": (
                full["heading_conditioned_on_all_other_coordinates"]["rank"]
                - prior["heading_conditioned_on_all_other_coordinates"]["rank"]
            ),
            "smallest_identifiable_singular_with_b5": full_minimum,
            "smallest_identifiable_singular_without_b5": prior_minimum,
            "smallest_singular_information_gain_fraction": (
                full_minimum / max(prior_minimum, 1e-15) - 1.0
                if full_rank == prior_rank else float("inf")
            ),
        }
    offsets = np.cumsum([0] + [len(block.values) for block in blocks])
    output["factor_lineage"] = [
        {
            "action": block.action,
            "factor": block.factor,
            "classification": block.classification,
            "row_count": len(block.values),
            "parameter_blocks": list(block.parameter_blocks),
            "robust_cost_at_reference": float(np.sum(np.sqrt(1.0 + block.values * block.values) - 1.0)),
            "jacobian_frobenius_norm": float(np.linalg.norm(jacobian[offsets[index]:offsets[index + 1]])),
        }
        for index, block in enumerate(blocks) if is_b5(block)
    ]
    return output


def sign_handedness_alternatives(objective: UnifiedCalibrationObjective, x: np.ndarray) -> list[dict[str, Any]]:
    baseline = float(0.5 * np.sum(objective.residual(x, True) ** 2))
    records = []
    for label, candidate in _sign_alternative_states(x).items():
        cost = float(0.5 * np.sum(objective.residual(candidate, True) ** 2))
        records.append({"alternative": label, "cost": cost, "delta_cost": cost - baseline})
    return records
