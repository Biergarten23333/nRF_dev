"""Viewer-only posterior summaries for the non-anatomical C2 landmark proxy.

Nothing in this module is a fit, a segment-frame owner, or pose truth.  The
functions retain the complete circular support and expose one deterministic
Fréchet/medoid representative solely so the fixed LANDMARK_PROXY viewer can
draw a clear sensitivity product instead of an uncertainty fan.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation
from scipy.stats import qmc


def _array_sha256(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    return sha256(array.view(np.uint8)).hexdigest()


def _normalized_positive_weights(value: np.ndarray) -> np.ndarray:
    weights = np.asarray(value, dtype=float)
    if (
        weights.ndim != 1
        or len(weights) < 3
        or not np.isfinite(weights).all()
        or np.any(weights <= 0.0)
    ):
        raise ValueError("viewer circular support requires finite positive weights")
    return weights / float(np.sum(weights))


def _circular_distance(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    return np.angle(np.exp(1j * (left - right)))


@dataclass(frozen=True)
class CircularDisplayRepresentative:
    coordinate_rad: float
    grid_index: int
    expected_squared_geodesic_loss_rad2: float
    tangent_second_moment_rad2: float
    report: Mapping[str, Any]


def circular_frechet_medoid_display_summary(
    *,
    delta_grid_rad: np.ndarray,
    posterior_weights: np.ndarray,
    owner_binding_sha256: str,
) -> CircularDisplayRepresentative:
    """Return a grid-supported Bayes representative without a MAP operation."""

    grid = np.asarray(delta_grid_rad, dtype=float)
    weights = _normalized_positive_weights(posterior_weights)
    if (
        grid.ndim != 1
        or grid.shape != weights.shape
        or not np.isfinite(grid).all()
        or not owner_binding_sha256
    ):
        raise ValueError("viewer circular grid/binding is invalid")
    distance = _circular_distance(grid[:, None], grid[None, :])
    expected_loss = np.square(distance) @ weights
    minimum = float(np.min(expected_loss))
    ties = np.flatnonzero(np.isclose(expected_loss, minimum, atol=1e-14, rtol=1e-12))
    index = int(ties[0])
    coordinate = float(grid[index])
    displacement = _circular_distance(grid, coordinate)
    second_moment = float(weights @ np.square(displacement))
    resultant = float(abs(np.sum(weights * np.exp(1j * grid))))
    entropy = float(-np.sum(weights * np.log(weights)))
    map_index = int(np.argmax(weights))
    return CircularDisplayRepresentative(
        coordinate_rad=coordinate,
        grid_index=index,
        expected_squared_geodesic_loss_rad2=minimum,
        tangent_second_moment_rad2=second_moment,
        report={
            "schema": "biospur-c2-viewer-circular-frechet-medoid-summary-v1",
            "owner_binding_sha256": str(owner_binding_sha256),
            "delta_grid_rad_sha256": _array_sha256(grid),
            "posterior_weights_sha256": _array_sha256(weights),
            "support_cell_count": int(len(grid)),
            "all_support_cells_retained": True,
            "posterior_resultant": resultant,
            "posterior_entropy_nats": entropy,
            "representative_grid_index": index,
            "minimum_loss_tie_count": int(len(ties)),
            "minimum_loss_tie_break_rule": (
                "LOWEST_REGISTERED_GRID_INDEX_COORDINATE_SEAM_DETERMINISTIC_ONLY"
            ),
            "minimum_loss_tie_break_implies_unique_physical_solution": False,
            "representative_coordinate_rad": coordinate,
            "expected_squared_geodesic_loss_rad2": minimum,
            "tangent_second_moment_rad2": second_moment,
            "posterior_map_grid_index_diagnostic_only": map_index,
            "representative_is_posterior_map_or_argmax": False,
            "representative_is_pose_truth_or_calibration_evidence": False,
            "full_distribution_may_be_discarded_after_summary": False,
        },
    )


@dataclass(frozen=True)
class JointPosteriorQuantileSupport:
    factor_names: tuple[str, ...]
    support_indices: np.ndarray
    report: Mapping[str, Any]


@dataclass(frozen=True)
class JointPhysicalDisplayRepresentative:
    support_row: int
    expected_squared_geodesic_loss_rad2: float
    report: Mapping[str, Any]


@dataclass(frozen=True)
class JointFactorIdentifierBijection:
    authority_factor_names: tuple[str, ...]
    produced_column_by_authority: np.ndarray
    report: Mapping[str, Any]


@dataclass(frozen=True)
class ViewerProxyTrajectoryAssessment:
    display_legal: bool
    report: Mapping[str, Any]


def assess_fixed_landmark_proxy_trajectory(
    *,
    landmark_positions_m: Mapping[str, np.ndarray],
    world_from_pelvis_segment: np.ndarray,
    proximal_functional_direction_dot_viewer_minus_z: Mapping[str, float],
    physical_settings: Mapping[str, Any],
) -> ViewerProxyTrajectoryAssessment:
    """Apply registered gross gates to the graph that is actually displayed.

    This is a viewer-display admissibility check, not a new scientific gate.
    It uses no action target.  The fixed FK owner has already asserted graph
    adjacency and lengths; here we check sustained bilateral crossing, a
    reversed graphical spine hemisphere, and the sign of the qualified
    two-center proximal limb direction.
    """

    positions = {name: np.asarray(value, dtype=float) for name, value in landmark_positions_m.items()}
    pelvis = np.asarray(world_from_pelvis_segment, dtype=float)
    expected_nodes = {
        "hip_mid_landmark_proxy", "shoulder_mid_landmark_proxy",
        "elbow_left_landmark_proxy", "elbow_right_landmark_proxy",
        "wrist_left_landmark_proxy", "wrist_right_landmark_proxy",
        "knee_left_landmark_proxy", "knee_right_landmark_proxy",
        "ankle_left_landmark_proxy", "ankle_right_landmark_proxy",
    }
    if not expected_nodes.issubset(positions):
        raise ValueError("viewer display gate lacks required fixed-graph nodes")
    counts = {len(value) for value in positions.values()}
    if len(counts) != 1 or not counts or next(iter(counts)) < 1:
        raise ValueError("viewer display gate nodes do not share a trajectory")
    count = next(iter(counts))
    if (
        pelvis.shape != (count, 3, 3)
        or not np.isfinite(pelvis).all()
        or any(value.shape != (count, 3) or not np.isfinite(value).all() for value in positions.values())
    ):
        raise ValueError("viewer display gate trajectory shape is invalid")
    crossing = physical_settings["bilateral_crossing"]
    margin = float(crossing["gross_crossing_margin_m"])
    required_fraction = float(crossing["gross_sustained_evidence_fraction"])
    if margin < 0.0 or not 0.0 < required_fraction <= 1.0:
        raise ValueError("viewer display gate bilateral settings are invalid")
    root = positions["hip_mid_landmark_proxy"]
    pelvis_frame = {
        name: np.einsum("nji,nj->ni", pelvis, value - root)
        for name, value in positions.items()
    }
    crossing_rows: dict[str, Any] = {}
    rejection_codes: list[str] = []
    for label in ("elbow", "wrist", "knee", "ankle"):
        signed = (
            pelvis_frame[f"{label}_left_landmark_proxy"][:, 1]
            - pelvis_frame[f"{label}_right_landmark_proxy"][:, 1]
        )
        confirmed = signed < -margin
        fraction = float(np.mean(confirmed))
        crossing_rows[label] = {
            "left_minus_right_pelvis_y_m": signed.tolist(),
            "left_minus_right_pelvis_y_m_sha256": _array_sha256(signed),
            "gross_crossing_margin_m": margin,
            "confirmed_fraction": fraction,
            "required_fraction": required_fraction,
        }
        if fraction >= required_fraction:
            rejection_codes.append(f"VIEWER_GROSS_SUSTAINED_{label.upper()}_CROSSING")
    gravity_margin = float(physical_settings["gross_gravity_wrong_hemisphere_margin_rad"])
    gravity_required = float(
        physical_settings["gravity_evidence"]["gross_sustained_evidence_fraction"],
    )
    spine = (
        positions["shoulder_mid_landmark_proxy"]
        - positions["hip_mid_landmark_proxy"]
    )
    spine /= np.linalg.norm(spine, axis=1, keepdims=True)
    up_dot = spine[:, 2]
    wrong_gravity = up_dot < -float(np.sin(gravity_margin))
    wrong_gravity_fraction = float(np.mean(wrong_gravity))
    if wrong_gravity_fraction >= gravity_required:
        rejection_codes.append("VIEWER_GRAPHICAL_SPINE_WRONG_GRAVITY_HEMISPHERE")
    direction_rows: dict[str, Any] = {}
    expected_proximal = {
        "upper_arm_left", "upper_arm_right", "thigh_left", "thigh_right",
    }
    if set(proximal_functional_direction_dot_viewer_minus_z) != expected_proximal:
        raise ValueError("viewer proximal direction gate requires exact two-center segments")
    for segment in sorted(expected_proximal):
        dot = float(proximal_functional_direction_dot_viewer_minus_z[segment])
        if not np.isfinite(dot) or not -1.0 <= dot <= 1.0:
            raise ValueError("viewer proximal direction dot is invalid")
        direction_rows[segment] = {
            "functional_proximal_to_distal_dot_viewer_minus_z": dot,
            "hemisphere_consistent": dot > 0.0,
        }
        if dot <= 0.0:
            rejection_codes.append(f"VIEWER_{segment.upper()}_LONGITUDINAL_HEMISPHERE_REVERSED")
    unique = tuple(dict.fromkeys(rejection_codes))
    return ViewerProxyTrajectoryAssessment(
        display_legal=not unique,
        report={
            "schema": "biospur-c2-fixed-landmark-proxy-trajectory-display-gate-v1",
            "role": "VIEWER_DISPLAY_ADMISSIBILITY_NOT_SCIENTIFIC_ACCEPTANCE",
            "action_pose_target_or_label_used": False,
            "rendered_graph_nodes_consumed": True,
            "functional_sensor_fk_substituted_for_rendered_graph": False,
            "bilateral_crossing": crossing_rows,
            "graphical_spine_up_dot_world": up_dot.tolist(),
            "graphical_spine_up_dot_world_sha256": _array_sha256(up_dot),
            "wrong_gravity_confirmed_fraction": wrong_gravity_fraction,
            "wrong_gravity_required_fraction": gravity_required,
            "proximal_two_center_direction_consistency": direction_rows,
            "distal_single_center_direction_hard_gate_applied": False,
            "hard_rejection_codes": list(unique),
            "display_legal": not unique,
            "scientific_acceptance_pass": False,
        },
    )


def bind_joint_support_factor_identifiers(
    *,
    authority_factor_names: tuple[str, ...],
    produced_factor_names: tuple[str, ...],
    produced_to_authority_alias: Mapping[str, str],
) -> JointFactorIdentifierBijection:
    """Bind produced support columns to stable authority identifiers."""

    authority = tuple(str(value) for value in authority_factor_names)
    produced = tuple(str(value) for value in produced_factor_names)
    aliases = {str(key): str(value) for key, value in produced_to_authority_alias.items()}
    if (
        not authority
        or len(set(authority)) != len(authority)
        or len(set(produced)) != len(produced)
        or set(aliases) - set(produced)
    ):
        raise ValueError("viewer factor identifiers contain missing/duplicate alias owners")
    resolved = tuple(aliases.get(name, name) for name in produced)
    if len(set(resolved)) != len(resolved) or set(resolved) != set(authority):
        raise ValueError("viewer factor identifiers do not form an exact authority bijection")
    column_by_authority = np.asarray(
        [resolved.index(name) for name in authority], dtype=np.int64,
    )
    return JointFactorIdentifierBijection(
        authority_factor_names=authority,
        produced_column_by_authority=column_by_authority,
        report={
            "schema": "biospur-c2-viewer-joint-factor-identifier-bijection-v1",
            "authority_factor_names": list(authority),
            "produced_factor_names": list(produced),
            "produced_to_authority_alias": aliases,
            "produced_resolved_authority_names": list(resolved),
            "produced_column_by_authority": column_by_authority.tolist(),
            "authority_identifier_sha256": _array_sha256(
                np.frombuffer("\0".join(authority).encode(), dtype=np.uint8),
            ),
            "missing_duplicate_or_extra_factor_allowed": False,
            "column_order_chosen_by_result_or_weight": False,
            "exact_bijection": True,
        },
    )


def deterministic_joint_posterior_quantile_support(
    *,
    posterior_weights_by_factor: Mapping[str, np.ndarray],
    sample_power: int,
    owner_binding_sha256: str,
) -> JointPosteriorQuantileSupport:
    """Build fixed Sobol inverse-CDF rows from a factorized posterior.

    One row owns one complete body candidate.  No factor is summarized here:
    callers must physically gate each joint row before choosing a display
    representative.
    """

    names = tuple(str(name) for name in posterior_weights_by_factor)
    if not names or not 1 <= int(sample_power) <= 16 or not owner_binding_sha256:
        raise ValueError("viewer joint posterior support authority is invalid")
    normalized: list[np.ndarray] = []
    for name in names:
        weights = np.asarray(posterior_weights_by_factor[name], dtype=float)
        if (
            weights.ndim != 1
            or len(weights) < 2
            or not np.isfinite(weights).all()
            or np.any(weights < 0.0)
            or float(np.sum(weights)) <= 0.0
        ):
            raise ValueError(f"{name}: viewer joint factor weights are invalid")
        normalized.append(weights / float(np.sum(weights)))
    unit_rows = qmc.Sobol(d=len(names), scramble=False).random_base2(
        m=int(sample_power),
    )
    indices = np.empty(unit_rows.shape, dtype=np.int64)
    for column, weights in enumerate(normalized):
        cdf = np.cumsum(weights)
        cdf[-1] = 1.0
        indices[:, column] = np.searchsorted(
            cdf, unit_rows[:, column], side="right",
        )
        np.minimum(indices[:, column], len(weights) - 1, out=indices[:, column])
    return JointPosteriorQuantileSupport(
        factor_names=names,
        support_indices=indices,
        report={
            "schema": "biospur-c2-viewer-joint-posterior-quantile-support-v1",
            "owner_binding_sha256": str(owner_binding_sha256),
            "factor_names": list(names),
            "factor_weight_sha256": {
                name: _array_sha256(weights)
                for name, weights in zip(names, normalized, strict=True)
            },
            "sobol_dimension": int(len(names)),
            "sobol_sample_power": int(sample_power),
            "joint_support_row_count": int(len(indices)),
            "sobol_scramble": False,
            "inverse_cdf_product_posterior_support": True,
            "marginal_representatives_chosen_before_physical_gate": False,
            "support_indices_sha256": _array_sha256(indices),
            "support_is_exhaustive_cartesian_enumeration": False,
            "support_is_pose_truth_or_scientific_acceptance": False,
        },
    )


def deterministic_joint_conditional_quantile_support(
    *,
    branch_weights: np.ndarray,
    conditional_weights_by_factor: Mapping[str, np.ndarray],
    sample_power: int,
    owner_binding_sha256: str,
) -> JointPosteriorQuantileSupport:
    """Build joint rows for ``p(branch) product p(factor | branch)``.

    The first support column is the retained hinge branch.  Every later S1
    cell is inverse-CDF sampled from the posterior owned by that same branch;
    this avoids silently replacing branch-conditional distal wear support by
    an independent marginal.
    """

    branches = np.asarray(branch_weights, dtype=float)
    names = tuple(str(name) for name in conditional_weights_by_factor)
    if (
        branches.ndim != 1
        or len(branches) < 2
        or not np.isfinite(branches).all()
        or np.any(branches < 0.0)
        or float(np.sum(branches)) <= 0.0
        or not names
        or not 1 <= int(sample_power) <= 16
        or not owner_binding_sha256
    ):
        raise ValueError("viewer conditional joint posterior authority is invalid")
    branches = branches / float(np.sum(branches))
    conditional: list[np.ndarray] = []
    for name in names:
        weights = np.asarray(conditional_weights_by_factor[name], dtype=float)
        if (
            weights.ndim != 2
            or weights.shape[0] != len(branches)
            or weights.shape[1] < 2
            or not np.isfinite(weights).all()
            or np.any(weights < 0.0)
            or np.any(np.sum(weights, axis=1) <= 0.0)
        ):
            raise ValueError(f"{name}: branch-conditional viewer weights are invalid")
        conditional.append(weights / np.sum(weights, axis=1, keepdims=True))
    unit_rows = qmc.Sobol(d=1 + len(names), scramble=False).random_base2(
        m=int(sample_power),
    )
    indices = np.empty(unit_rows.shape, dtype=np.int64)
    branch_cdf = np.cumsum(branches)
    branch_cdf[-1] = 1.0
    indices[:, 0] = np.searchsorted(branch_cdf, unit_rows[:, 0], side="right")
    np.minimum(indices[:, 0], len(branches) - 1, out=indices[:, 0])
    for column, weights in enumerate(conditional, start=1):
        for row, branch_index in enumerate(indices[:, 0]):
            cdf = np.cumsum(weights[int(branch_index)])
            cdf[-1] = 1.0
            indices[row, column] = min(
                int(np.searchsorted(cdf, unit_rows[row, column], side="right")),
                weights.shape[1] - 1,
            )
    return JointPosteriorQuantileSupport(
        factor_names=("hinge_branch",) + names,
        support_indices=indices,
        report={
            "schema": "biospur-c2-viewer-joint-conditional-quantile-support-v1",
            "owner_binding_sha256": str(owner_binding_sha256),
            "factor_names": ["hinge_branch", *names],
            "branch_weights_sha256": _array_sha256(branches),
            "conditional_factor_weight_sha256": {
                name: _array_sha256(weights)
                for name, weights in zip(names, conditional, strict=True)
            },
            "sobol_dimension": int(1 + len(names)),
            "sobol_sample_power": int(sample_power),
            "joint_support_row_count": int(len(indices)),
            "sobol_scramble": False,
            "inverse_cdf_branch_conditional_product_posterior_support": True,
            "branch_conditional_support_replaced_by_independent_marginal": False,
            "marginal_representatives_chosen_before_physical_gate": False,
            "support_indices_sha256": _array_sha256(indices),
            "support_is_exhaustive_cartesian_enumeration": False,
            "support_is_pose_truth_or_scientific_acceptance": False,
        },
    )
def joint_physical_legal_frechet_medoid_display_summary(
    *,
    pairwise_squared_geodesic_loss_rad2: np.ndarray,
    physical_legal_mask: np.ndarray,
    physical_gate_binding_sha256: str,
    owner_binding_sha256: str,
) -> JointPhysicalDisplayRepresentative:
    """Choose one joint-body medoid only inside already gated legal support."""

    loss = np.asarray(pairwise_squared_geodesic_loss_rad2, dtype=float)
    legal = np.asarray(physical_legal_mask, dtype=bool)
    if (
        loss.ndim != 2
        or loss.shape[0] != loss.shape[1]
        or loss.shape[0] != len(legal)
        or len(legal) < 2
        or not np.isfinite(loss).all()
        or np.any(loss < -1e-12)
        or not np.allclose(loss, loss.T, atol=1e-10, rtol=1e-10)
        or not np.allclose(np.diag(loss), 0.0, atol=1e-10, rtol=0.0)
        or not physical_gate_binding_sha256
        or not owner_binding_sha256
    ):
        raise ValueError("viewer joint physical/Frechet support is invalid")
    legal_rows = np.flatnonzero(legal)
    if len(legal_rows) == 0:
        raise RuntimeError("viewer joint support has no physical-legal candidate")
    conditional = loss[np.ix_(legal_rows, legal_rows)]
    expected = np.mean(conditional, axis=1)
    minimum = float(np.min(expected))
    local_ties = np.flatnonzero(
        np.isclose(expected, minimum, atol=1e-14, rtol=1e-12),
    )
    selected = int(legal_rows[int(local_ties[0])])
    return JointPhysicalDisplayRepresentative(
        support_row=selected,
        expected_squared_geodesic_loss_rad2=minimum,
        report={
            "schema": "biospur-c2-viewer-joint-physical-frechet-medoid-v1",
            "owner_binding_sha256": str(owner_binding_sha256),
            "physical_gate_binding_sha256": str(physical_gate_binding_sha256),
            "pairwise_squared_geodesic_loss_rad2_sha256": _array_sha256(loss),
            "physical_legal_mask_sha256": _array_sha256(legal),
            "joint_support_row_count": int(len(legal)),
            "physical_legal_row_count": int(len(legal_rows)),
            "physical_legal_fraction_of_deterministic_support": float(np.mean(legal)),
            "physical_gate_applied_before_representative_selection": True,
            "selected_support_row": selected,
            "expected_squared_geodesic_loss_rad2": minimum,
            "minimum_loss_tie_count": int(len(local_ties)),
            "minimum_loss_tie_break_rule": (
                "LOWEST_SOBOL_SUPPORT_ROW_COORDINATE_SEAM_DETERMINISTIC_ONLY"
            ),
            "minimum_loss_tie_break_implies_unique_physical_solution": False,
            "marginal_medoid_then_body_gate_used": False,
            "map_argmax_or_pixel_selection_used": False,
            "representative_is_pose_truth_or_scientific_acceptance": False,
        },
    )


def candidate_rotation_tangent_second_moment_rad2(
    *,
    candidate_sensor_from_segment: np.ndarray,
    posterior_weights: np.ndarray,
    representative_index: int,
) -> tuple[np.ndarray, Mapping[str, Any]]:
    """Push complete-S1 branch spread into the representative tangent space."""

    candidates = np.asarray(candidate_sensor_from_segment, dtype=float)
    weights = _normalized_positive_weights(posterior_weights)
    if (
        candidates.ndim != 3
        or candidates.shape[1:] != (3, 3)
        or len(candidates) != len(weights)
        or not np.isfinite(candidates).all()
        or not 0 <= representative_index < len(candidates)
    ):
        raise ValueError("viewer candidate rotation support is invalid")
    representative = candidates[int(representative_index)]
    relative = np.einsum("ij,njk->nik", representative.T, candidates)
    rotvec = Rotation.from_matrix(relative).as_rotvec()
    second_moment = np.einsum("n,ni,nj->ij", weights, rotvec, rotvec)
    second_moment = 0.5 * (second_moment + second_moment.T)
    if float(np.min(np.linalg.eigvalsh(second_moment))) < -1e-12:
        raise RuntimeError("viewer candidate tangent second moment is not PSD")
    return second_moment, {
        "schema": "biospur-c2-viewer-candidate-tangent-second-moment-v1",
        "candidate_sensor_from_segment_sha256": _array_sha256(candidates),
        "posterior_weights_sha256": _array_sha256(weights),
        "representative_index": int(representative_index),
        "tangent_rotvec_sha256": _array_sha256(rotvec),
        "tangent_second_moment_rad2": second_moment.tolist(),
        "tangent_second_moment_rad2_sha256": _array_sha256(second_moment),
        "raw_fraction_squared_relabelled_as_tangent_rad2": False,
        "full_distribution_retained": True,
    }


def _proper_rotation(value: np.ndarray, *, name: str) -> np.ndarray:
    rotation = np.asarray(value, dtype=float)
    if (
        rotation.shape != (3, 3)
        or not np.isfinite(rotation).all()
        or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-8, rtol=0.0)
        or not np.isclose(np.linalg.det(rotation), 1.0, atol=1e-8, rtol=0.0)
    ):
        raise ValueError(f"{name}: viewer proxy requires one proper SO(3) rotation")
    return rotation


def viewer_graphical_spine_proxy_vector(
    *,
    world_from_pelvis_segment: np.ndarray,
    world_from_torso_segment: np.ndarray,
    graphical_display_scale_m: float,
    mapping_authority: Mapping[str, Any],
) -> tuple[np.ndarray, Mapping[str, Any]]:
    """Map broad pelvis/torso orientation support to one display-only vector.

    The measured surface-path scalars own only the magnitude sensitivity.  The
    direction is the intrinsic two-direction Fréchet representative of pelvis
    and torso segment +Z.  Consequently the 0.280 m surface observation is not
    promoted to a vector and is not constrained to torso +Z.
    """

    pelvis = _proper_rotation(world_from_pelvis_segment, name="pelvis")
    torso = _proper_rotation(world_from_torso_segment, name="torso")
    scale = float(graphical_display_scale_m)
    authority = dict(mapping_authority)
    if (
        not np.isfinite(scale)
        or scale <= 0.0
        or authority.get("owner")
        != "PREREGISTERED_VIEWER_ONLY_GRAPHICAL_SPINE_MAPPING"
        or authority.get("surface_measurements_are_internal_truth") is not False
        or authority.get("surface_scalar_is_3d_vector") is not False
        or authority.get("surface_scalar_constrained_to_torso_plus_z") is not False
        or not authority.get("uncertainty_or_sensitivity")
    ):
        raise ValueError("viewer graphical-spine authority is incomplete")
    pelvis_up = pelvis @ np.array([0.0, 0.0, 1.0])
    torso_up = torso @ np.array([0.0, 0.0, 1.0])
    summed = pelvis_up + torso_up
    if float(np.linalg.norm(summed)) <= 1e-9:
        raise ValueError("pelvis/torso graphical-spine direction support is antipodal")
    representative = summed / np.linalg.norm(summed)
    tangent_rows = []
    angles = []
    for candidate in (pelvis_up, torso_up):
        cosine = float(np.clip(representative @ candidate, -1.0, 1.0))
        angle = float(np.arccos(cosine))
        cross = np.cross(representative, candidate)
        norm = float(np.linalg.norm(cross))
        tangent = np.zeros(3) if norm <= 1e-12 else cross / norm * angle
        tangent_rows.append(tangent)
        angles.append(angle)
    tangent_rows_array = np.asarray(tangent_rows, dtype=float)
    second_moment = 0.5 * np.einsum(
        "ni,nj->ij", tangent_rows_array, tangent_rows_array,
    )
    vector = scale * representative
    return vector, {
        "schema": "biospur-c2-viewer-graphical-spine-proxy-vector-v1",
        "owner": authority["owner"],
        "mapping_authority_sha256": str(authority.get("authority_sha256", "")),
        "graphical_display_scale_m": scale,
        "pelvis_plus_z_world": pelvis_up.tolist(),
        "torso_plus_z_world": torso_up.tolist(),
        "direction_representative": representative.tolist(),
        "direction_support_angular_offsets_rad": angles,
        "direction_tangent_second_moment_rad2": second_moment.tolist(),
        "direction_tangent_second_moment_rad2_sha256": _array_sha256(second_moment),
        "surface_scalar_is_3d_vector": False,
        "surface_scalar_constrained_to_torso_plus_z": False,
        "graphical_scale_called_anatomical_spine_or_torso_length": False,
        "viewer_only_non_anatomical": True,
        "uncertainty_or_sensitivity": authority["uncertainty_or_sensitivity"],
    }


def reexpress_world_segment_with_candidate_frame(
    *,
    world_from_segment: np.ndarray,
    old_segment_from_sensor: np.ndarray,
    candidate_sensor_from_segment: np.ndarray,
) -> np.ndarray:
    """Replace only the viewer segment coordinate while retaining sensor motion."""

    world_segment = np.asarray(world_from_segment, dtype=float)
    old_segment_sensor = _proper_rotation(
        old_segment_from_sensor, name="old_segment_from_sensor",
    )
    candidate_sensor_segment = _proper_rotation(
        candidate_sensor_from_segment, name="candidate_sensor_from_segment",
    )
    if (
        world_segment.ndim != 3
        or world_segment.shape[1:] != (3, 3)
        or not np.isfinite(world_segment).all()
    ):
        raise ValueError("viewer world segment trajectory must be finite Nx3x3")
    world_from_sensor = np.einsum(
        "nij,jk->nik", world_segment, old_segment_sensor,
    )
    return np.einsum(
        "nij,jk->nik", world_from_sensor, candidate_sensor_segment,
    )
