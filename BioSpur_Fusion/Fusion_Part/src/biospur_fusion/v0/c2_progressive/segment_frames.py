"""Posterior functional plus qualitative-wear SO(3) branch owner for C2.

Frame means originate in pair-local functional centers and sign-ambiguous QMT
axes. The sealed qualitative sensor ``-Y``/``-Z`` wear directions then supply
a broad, everywhere-positive soft branch likelihood. They are not duplicated
as a nonshrinking covariance floor; only genuinely unidentified tangent
directions remain broad, while the separate human-worn migration/systematic
floor remains nonshrinking. They are never exact extrinsics or pose truth. Anthropometry,
action-label pose truth, and example-subject alignment remain absent.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.spatial.transform import Rotation

from .architecture_guard import C2ExecutionGuard, ClassAGuardViolation
from .functional_geometry import (
    EDGE_SPECS,
    HINGE_EDGES,
    AxisEstimate,
    CenterEstimate,
    enumerate_hinge_sign_branches,
    hinge_sign_branch_descriptors,
)


SEGMENTS = tuple(dict.fromkeys(name for _, parent, child in EDGE_SPECS for name in (parent, child)))
EDGE_BY_NAME = {edge: (parent, child) for edge, parent, child in EDGE_SPECS}
WEAR_AUTHORITY_SHA256 = "25464b91b7d77f1cf14df9e0e00c98a8de6f1854edb99b7c7828f0f59815b26e"
IDENTITY_AUTHORITY_SHA256 = "8f744eee31ff505b58ee24e88c75f22c6f75dccce7ad4719a2476a42d72a0524"
FRAME_AUTHORITY_SHA256 = "989215949e7d33b6939258067d4dd3b9fc18826ca210efb3ad0f6a1d212e175d"
UPPER_ARM_SEGMENTS = frozenset({"upper_arm_left", "upper_arm_right"})
SINGLE_JOINT_DISTAL_SEGMENTS = frozenset({
    "forearm_left", "forearm_right", "shank_left", "shank_right",
})
DISTAL_LONGITUDINAL_S1_QUADRATURE_COUNT = 8
EXPECTED_WEAR_ROWS = (
    ("BSFEC35", "forearm_left", "approximately_anatomical_left", (0.0, 1.0, 0.0), None),
    ("BSFB165", "forearm_right", "approximately_anatomical_right", (0.0, -1.0, 0.0), None),
    ("BSFAA61", "upper_arm_left", "left_and_posterior_with_posterior_component_dominant", None, None),
    ("BSF1120", "upper_arm_right", "right_and_posterior_with_posterior_component_dominant", None, None),
    ("BSF31CC", "torso", "approximately_forward", (1.0, 0.0, 0.0), None),
    ("BSFC2CC", "pelvis", "approximately_forward", (1.0, 0.0, 0.0), None),
    ("BSF44AD", "thigh_left", "approximately_forward", (1.0, 0.0, 0.0), None),
    ("BSF3C79", "thigh_right", "approximately_forward", (1.0, 0.0, 0.0), None),
    ("BSF6C53", "shank_left", "approximately_anatomical_left", (0.0, 1.0, 0.0), "lateral_shank_not_front"),
    ("BSF8BC4", "shank_right", "approximately_anatomical_right", (0.0, -1.0, 0.0), "lateral_shank_not_front"),
)


@dataclass(frozen=True)
class EdgeConnectionVectors:
    """The two full R3 vectors needed to close one shared joint."""

    edge: str
    parent: str
    child: str
    parent_sensor_to_joint_m: np.ndarray
    child_sensor_to_joint_m: np.ndarray
    covariance_m2: np.ndarray


@dataclass(frozen=True)
class SegmentFrameBranch:
    branch_id: str
    axis_sign_by_edge: Mapping[str, int]
    segment_from_sensor: Mapping[str, np.ndarray]
    sensor_from_segment: Mapping[str, np.ndarray]
    joint_frame_tangent_covariance_rad2: np.ndarray
    frame_tangent_covariance_rad2: Mapping[str, np.ndarray]
    paired_hinge_frame_tangent_covariance_rad2: Mapping[str, np.ndarray]
    connection_vectors_by_edge: Mapping[str, EdgeConnectionVectors]
    prior_weight: float
    wear_log_likelihood: float
    wear_profile_log_likelihood: Mapping[str, float]
    wear_gross_wrong_hemisphere: bool
    retained: bool
    report: Mapping[str, Any]


@dataclass(frozen=True)
class PhysicalCandidateAssessment:
    branch_id: str
    left_knee_direction: str
    right_knee_direction: str
    physically_legal: bool
    evidence: Mapping[str, Any]


def _unit(value: np.ndarray, *, label: str) -> tuple[np.ndarray, list[str]]:
    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,) or not np.isfinite(vector).all():
        raise ValueError(f"{label} must be one finite R3 vector")
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-9:
        # A zero functional direction is ordinary low information, not license
        # to invent anatomy.  The deterministic basis keeps the branch
        # renderable while its audit/validity prevents a readiness claim.
        return np.array([0.0, 0.0, 1.0]), [f"{label}:ZERO_DIRECTION_DETERMINISTIC_SO3_FALLBACK"]
    return vector / norm, []


def _least_aligned_basis(direction: np.ndarray) -> np.ndarray:
    return np.eye(3)[int(np.argmin(np.abs(np.asarray(direction, dtype=float))))]


def _frame_from_z_x(z_hint: np.ndarray, x_hint: np.ndarray, *, label: str) -> tuple[np.ndarray, list[str]]:
    """Return sensor<-segment with columns [x,y,z]."""

    z, flags = _unit(z_hint, label=f"{label}:z")
    raw_x = np.asarray(x_hint, dtype=float) - z * float(z @ np.asarray(x_hint, dtype=float))
    if float(np.linalg.norm(raw_x)) <= 1e-9:
        raw_x = _least_aligned_basis(z) - z * float(z @ _least_aligned_basis(z))
        flags.append(f"{label}:COLLINEAR_XZ_DETERMINISTIC_SO3_FALLBACK")
    x = raw_x / np.linalg.norm(raw_x)
    y = np.cross(z, x)
    y /= np.linalg.norm(y)
    x = np.cross(y, z)
    rotation = np.column_stack((x, y, z))
    return rotation, flags


def _frame_from_z_y(z_hint: np.ndarray, y_hint: np.ndarray, *, label: str) -> tuple[np.ndarray, list[str]]:
    """Return sensor<-segment with the signed hinge direction as +y."""

    # The hinge direction is the qualified input; z can be weak or entirely
    # unresolved.  Preserve +Y exactly and project/fallback only z.  The old
    # ordering silently replaced the hinge axis when a wear placeholder was
    # collinear with it.
    y, flags = _unit(y_hint, label=f"{label}:y")
    raw_z = np.asarray(z_hint, dtype=float) - y * float(y @ np.asarray(z_hint, dtype=float))
    if float(np.linalg.norm(raw_z)) <= 1e-9:
        seed = _least_aligned_basis(y)
        raw_z = seed - y * float(y @ seed)
        flags.append(f"{label}:COLLINEAR_LONG_AXIS_REPLACED_NOT_QUALIFIED_HINGE_AXIS")
    z = raw_z / np.linalg.norm(raw_z)
    x = np.cross(y, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    rotation = np.column_stack((x, y, z))
    return rotation, flags


def _rotation_audit(rotation: np.ndarray) -> dict[str, float]:
    value = np.asarray(rotation, dtype=float)
    return {
        "orthogonality_frobenius_error": float(np.linalg.norm(value.T @ value - np.eye(3))),
        "determinant": float(np.linalg.det(value)),
    }


def _angle(first: np.ndarray, second: np.ndarray) -> float:
    return float(np.arccos(np.clip(float(np.asarray(first) @ np.asarray(second)), -1.0, 1.0)))


def _soft_full_support_angular_log_likelihood(angle_rad: float, sigma_rad: float) -> float:
    """Unrobustified smooth loss with positive density over the whole sphere."""

    if not np.isfinite(angle_rad) or not np.isfinite(sigma_rad) or sigma_rad <= 0.0:
        raise ValueError("wear angular likelihood inputs must be finite and sigma positive")
    scaled = float(angle_rad / sigma_rad)
    if scaled <= 1.0:
        loss = 0.5 * scaled**2
    else:
        # Smooth continuation beyond the uncertainty-report angle. This is a
        # soft likelihood, never a compact cone or robust residual cap.
        outside = scaled - 1.0
        loss = 0.5 + outside + 0.5 * outside**2
    return -float(loss)


def _azimuth_wedge_distance(
    direction: np.ndarray,
    *,
    side_sign: int,
    posterior_dominant: bool,
) -> float:
    """Spherical distance to the qualitative upper-arm horizontal wedge."""

    value = np.asarray(direction, dtype=float)
    posterior = -float(value[0])
    lateral = float(side_sign * value[1])
    horizontal = float(np.hypot(posterior, lateral))
    if horizontal <= 1e-12:
        # A vertical direction is uninformative about horizontal azimuth; it
        # is not silently converted into exact posterior/lateral truth.
        return 0.0
    azimuth = float(np.arctan2(lateral, posterior))
    upper = np.pi / 4.0 if posterior_dominant else np.pi / 2.0
    closest = float(np.clip(azimuth, 0.0, upper))
    delta = azimuth - closest
    closest_dot = float(value[2] ** 2 + horizontal**2 * np.cos(delta))
    return float(np.arccos(np.clip(closest_dot, -1.0, 1.0)))


def _nominal_body_from_sensor(minus_y_body: np.ndarray, minus_z_body: np.ndarray) -> np.ndarray:
    """One right-handed nominal frame; it is an audit quadrature point only."""

    plus_y = -np.asarray(minus_y_body, dtype=float)
    plus_y /= np.linalg.norm(plus_y)
    plus_z = -np.asarray(minus_z_body, dtype=float)
    plus_z -= plus_y * float(plus_y @ plus_z)
    if np.linalg.norm(plus_z) <= 1e-12:
        raise ValueError("nominal qualitative -Y and -Z directions are collinear")
    plus_z /= np.linalg.norm(plus_z)
    plus_x = np.cross(plus_y, plus_z)
    plus_x /= np.linalg.norm(plus_x)
    plus_z = np.cross(plus_x, plus_y)
    result = np.column_stack((plus_x, plus_y, plus_z))
    if not np.isclose(np.linalg.det(result), 1.0, atol=1e-12):
        raise RuntimeError("nominal qualitative frame is not right handed")
    return result


def _validated_wear_authority(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize the exact hash-bound ten-node wear contract."""

    authority = dict(settings["wear_authority"])
    expected_hashes = {
        "sealed_identity_sha256": IDENTITY_AUTHORITY_SHA256,
        "wear_amendment_sha256": WEAR_AUTHORITY_SHA256,
        "frame_amendment_sha256": FRAME_AUTHORITY_SHA256,
    }
    observed_hashes = {key: str(authority.get(key, "")) for key in expected_hashes}
    if observed_hashes != expected_hashes:
        raise ValueError("segment-frame wear authority hashes do not match the sealed C2 authorities")
    body_frame = authority.get("body_frame")
    if body_frame != {"x": "forward", "y": "anatomical_left", "z": "up", "right_handed": True}:
        raise ValueError("wear authority must use the sealed right-handed C2 body frame")
    common = dict(authority.get("common_direction", {}))
    if common.get("sensor_axis") != "-Y" or common.get("approximately_body_direction") != "ground":
        raise ValueError("wear authority must bind the common sensor -Y approximately-ground direction")
    common_nominal = np.asarray(common.get("nominal_body_vector_for_cone_evaluation"), dtype=float)
    if common_nominal.shape != (3,) or not np.array_equal(common_nominal, np.array([0.0, 0.0, -1.0])):
        raise ValueError("common wear direction differs from the sealed C2 amendment")
    rows = tuple(dict(row) for row in authority.get("rows", ()))
    if len(rows) != len(SEGMENTS):
        raise ValueError("wear authority must contain exactly ten node-to-segment rows")
    observed_rows = tuple(
        (
            str(row.get("hardware_id", "")),
            str(row.get("body_segment", "")),
            str(row.get("sensor_minus_z_region", "")),
            (
                None if row.get("nominal_body_vector_for_cone_evaluation") is None
                else tuple(float(value) for value in row["nominal_body_vector_for_cone_evaluation"])
            ),
            row.get("mount_surface"),
        )
        for row in rows
    )
    if observed_rows != EXPECTED_WEAR_ROWS:
        raise ValueError("wear authority rows differ from the exact sealed hardware-to-segment/direction mapping")
    by_segment: dict[str, dict[str, Any]] = {}
    hardware_ids: set[str] = set()
    for row in rows:
        segment = str(row.get("body_segment", ""))
        hardware_id = str(row.get("hardware_id", ""))
        if segment in by_segment or segment not in SEGMENTS or hardware_id in hardware_ids:
            raise ValueError("wear authority has a duplicate or unknown hardware/segment row")
        nominal = row.get("nominal_body_vector_for_cone_evaluation")
        if segment in UPPER_ARM_SEGMENTS:
            expected_region = (
                "left_and_posterior_with_posterior_component_dominant"
                if segment.endswith("left") else
                "right_and_posterior_with_posterior_component_dominant"
            )
            if row.get("sensor_minus_z_region") != expected_region or nominal is not None:
                raise ValueError(f"{segment}: upper-arm region/undefined-azimuth contract changed")
        else:
            vector = np.asarray(nominal, dtype=float)
            if vector.shape != (3,) or not np.isfinite(vector).all() or not np.isclose(np.linalg.norm(vector), 1.0):
                raise ValueError(f"{segment}: simple qualitative -Z nominal must be one unit R3 vector")
        by_segment[segment] = row
        hardware_ids.add(hardware_id)
    if set(by_segment) != set(SEGMENTS):
        raise ValueError("wear authority does not cover all ten segment-frame nodes")
    uncertainty = dict(authority.get("uncertainty_contract", {}))
    if float(uncertainty.get("primary_simple_direction_cone_half_angle_deg", -1.0)) != 55.0:
        raise ValueError("primary qualitative direction uncertainty must remain the sealed 55 degrees")
    if [float(value) for value in uncertainty.get("sensitivity_cone_half_angles_deg", ())] != [40.0, 55.0, 70.0]:
        raise ValueError("qualitative direction sensitivities must remain sealed at 40/55/70 degrees")
    if float(uncertainty.get("gross_sign_guard_margin_deg", -1.0)) != 10.0:
        raise ValueError("gross wrong-hemisphere guard must remain the sealed 10 degrees")
    upper = dict(uncertainty.get("upper_arm_rear_dominant_semantics", {}))
    if upper.get("exact_azimuth_defined") is not False or tuple(upper.get("sensitivity", ())) != (
        "HEMISPHERE_ONLY", "POSTERIOR_DOMINANT", "POSTERIOR_DOMINANT_WITH_55_DEG_DOWN_AXIS_CONE",
    ):
        raise ValueError("upper-arm region sensitivities differ from the sealed amendment")
    model = dict(settings["wear_uncertainty"])
    primary_sigma = float(model["primary_direction_sigma_rad"])
    sensitivity_sigmas = [float(value) for value in model["sensitivity_direction_sigmas_rad"]]
    if not np.isclose(primary_sigma, np.deg2rad(55.0)) or not np.allclose(
        sensitivity_sigmas, np.deg2rad([40.0, 55.0, 70.0]), atol=0.0, rtol=1e-15,
    ):
        raise ValueError("registered wear angular sigmas must be exact radian forms of 40/55/70 degrees")
    near_sigma = float(model["near_uninformative_hemisphere_sigma_rad"])
    margin = float(model["gross_wrong_hemisphere_margin_rad"])
    gross_guard_sigma_multiplier = float(model["gross_guard_sigma_multiplier"])
    if near_sigma < np.deg2rad(89.0) or near_sigma > np.pi:
        raise ValueError("near-uninformative wear sensitivity must span approximately a hemisphere")
    if not np.isclose(margin, np.deg2rad(10.0)):
        raise ValueError("registered gross wrong-hemisphere margin differs from authority")
    if not 0.0 < gross_guard_sigma_multiplier <= 5.0:
        raise ValueError("gross wear guard confidence multiplier must be registered in (0,5]")
    return {
        "authority": authority,
        "rows_by_segment": by_segment,
        "common_nominal": common_nominal,
        "primary_sigma": primary_sigma,
        "sensitivity_sigmas": sensitivity_sigmas,
        "near_sigma": near_sigma,
        "gross_margin": margin,
        "gross_guard_sigma_multiplier": gross_guard_sigma_multiplier,
        "hashes": observed_hashes,
    }


def _wear_evidence(
    segment_from_sensor: Mapping[str, np.ndarray],
    frame_tangent_covariance_rad2: Mapping[str, np.ndarray],
    wear: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate all ten qualitative directions for one functional branch."""

    profile_totals = {
        "CONE_40_DEG": 0.0,
        "PRIMARY_55_DEG": 0.0,
        "CONE_70_DEG": 0.0,
        "NEAR_UNINFORMATIVE_HEMISPHERE": 0.0,
        "UPPER_ARM_HEMISPHERE_ONLY": 0.0,
        "UPPER_ARM_POSTERIOR_DOMINANT": 0.0,
        "UPPER_ARM_POSTERIOR_DOMINANT_WITH_55_DEG_DOWN_AXIS_CONE": 0.0,
    }
    per_segment: dict[str, Any] = {}
    nominal_alternatives: dict[str, list[list[list[float]]]] = {}
    for segment_index, segment in enumerate(SEGMENTS):
        row = wear["rows_by_segment"][segment]
        transform = np.asarray(segment_from_sensor[segment], dtype=float)
        minus_y = transform @ np.array([0.0, -1.0, 0.0])
        minus_z = transform @ np.array([0.0, 0.0, -1.0])
        minus_y /= np.linalg.norm(minus_y)
        minus_z /= np.linalg.norm(minus_z)
        y_angle = _angle(minus_y, wear["common_nominal"])
        y_dot = float(minus_y @ wear["common_nominal"])
        y_profiles = [
            _soft_full_support_angular_log_likelihood(y_angle, sigma)
            for sigma in wear["sensitivity_sigmas"]
        ]
        y_near = _soft_full_support_angular_log_likelihood(y_angle, float(wear["near_sigma"]))
        covariance = np.asarray(frame_tangent_covariance_rad2[segment], dtype=float)
        if covariance.shape != (3, 3) or float(np.min(np.linalg.eigvalsh(covariance))) < -1e-9:
            raise ValueError(f"{segment}: wear guard requires one valid 3x3 frame tangent covariance")
        directional_sigma = float(np.sqrt(max(0.0, float(np.max(np.linalg.eigvalsh(covariance))))))
        base_margin = float(wear["gross_margin"])
        confidence_margin = base_margin + float(wear["gross_guard_sigma_multiplier"]) * directional_sigma
        mean_gross_reasons: list[str] = []
        gross_reasons: list[str] = []

        def assess_gross(dot: float, reason: str) -> None:
            if dot < -float(np.sin(base_margin)):
                mean_gross_reasons.append(reason)
            if confidence_margin < np.pi / 2.0 and dot < -float(np.sin(confidence_margin)):
                gross_reasons.append(reason)

        assess_gross(y_dot, "SENSOR_MINUS_Y_GROSS_OPPOSITE_GROUND")
        if segment in UPPER_ARM_SEGMENTS:
            side_sign = 1 if segment.endswith("left") else -1
            z_hemisphere_distance = _azimuth_wedge_distance(
                minus_z, side_sign=side_sign, posterior_dominant=False,
            )
            z_region_distance = _azimuth_wedge_distance(
                minus_z, side_sign=side_sign, posterior_dominant=True,
            )
            posterior = -float(minus_z[0])
            correct_lateral = float(side_sign * minus_z[1])
            assess_gross(posterior, "SENSOR_MINUS_Z_GROSS_WRONG_POSTERIOR_HEMISPHERE")
            assess_gross(correct_lateral, "SENSOR_MINUS_Z_GROSS_WRONG_LATERAL_HEMISPHERE")
            z_profiles = [
                _soft_full_support_angular_log_likelihood(z_region_distance, sigma)
                for sigma in wear["sensitivity_sigmas"]
            ]
            z_near = _soft_full_support_angular_log_likelihood(
                z_hemisphere_distance, float(wear["near_sigma"]),
            )
            for name, z_value, y_value in zip(
                ("CONE_40_DEG", "PRIMARY_55_DEG", "CONE_70_DEG"),
                z_profiles,
                y_profiles,
            ):
                profile_totals[name] += z_value + y_value
            profile_totals["NEAR_UNINFORMATIVE_HEMISPHERE"] += z_near + y_near
            profile_totals["UPPER_ARM_HEMISPHERE_ONLY"] += z_near + y_near
            profile_totals["UPPER_ARM_POSTERIOR_DOMINANT"] += z_profiles[1] + y_near
            profile_totals["UPPER_ARM_POSTERIOR_DOMINANT_WITH_55_DEG_DOWN_AXIS_CONE"] += (
                z_profiles[1] + y_profiles[1]
            )
            azimuths = (0.0, np.pi / 8.0, np.pi / 4.0)
            nominal_minus_z = [
                np.array([-np.cos(azimuth), side_sign * np.sin(azimuth), 0.0])
                for azimuth in azimuths
            ]
            nominal_alternatives[segment] = [
                _nominal_body_from_sensor(wear["common_nominal"], value).tolist()
                for value in nominal_minus_z
            ]
            z_audit = {
                "model": "DISTANCE_TO_POSTERIOR_CORRECT_LATERAL_POSTERIOR_DOMINANT_REGION",
                "hemisphere_region_distance_rad": z_hemisphere_distance,
                "posterior_dominant_region_distance_rad": z_region_distance,
                "posterior_component": posterior,
                "correct_lateral_component": correct_lateral,
                "exact_azimuth_used": False,
            }
        else:
            nominal_z = np.asarray(row["nominal_body_vector_for_cone_evaluation"], dtype=float)
            z_angle = _angle(minus_z, nominal_z)
            z_dot = float(minus_z @ nominal_z)
            z_profiles = [
                _soft_full_support_angular_log_likelihood(z_angle, sigma)
                for sigma in wear["sensitivity_sigmas"]
            ]
            z_near = _soft_full_support_angular_log_likelihood(z_angle, float(wear["near_sigma"]))
            assess_gross(z_dot, "SENSOR_MINUS_Z_GROSS_OPPOSITE_NOMINAL_HEMISPHERE")
            for name, z_value, y_value in zip(
                ("CONE_40_DEG", "PRIMARY_55_DEG", "CONE_70_DEG"),
                z_profiles,
                y_profiles,
            ):
                profile_totals[name] += z_value + y_value
            profile_totals["NEAR_UNINFORMATIVE_HEMISPHERE"] += z_near + y_near
            # Upper-arm semantic sensitivity totals remain comparable by
            # carrying every non-upper observation under the 55-degree model.
            non_upper_primary = z_profiles[1] + y_profiles[1]
            profile_totals["UPPER_ARM_HEMISPHERE_ONLY"] += non_upper_primary
            profile_totals["UPPER_ARM_POSTERIOR_DOMINANT"] += non_upper_primary
            profile_totals["UPPER_ARM_POSTERIOR_DOMINANT_WITH_55_DEG_DOWN_AXIS_CONE"] += non_upper_primary
            nominal_alternatives[segment] = [
                _nominal_body_from_sensor(wear["common_nominal"], nominal_z).tolist()
            ]
            z_audit = {
                "model": "SOFT_FULL_SUPPORT_ANGULAR_DISTRIBUTION_AROUND_QUALITATIVE_NOMINAL",
                "angle_rad": z_angle,
                "dot_nominal": z_dot,
            }
        per_segment[segment] = {
            "hardware_id": row["hardware_id"],
            "sensor_minus_y_in_segment": minus_y.tolist(),
            "sensor_minus_z_in_segment": minus_z.tolist(),
            "minus_y_angle_to_ground_rad": y_angle,
            "minus_y_dot_ground": y_dot,
            "minus_z_evidence": z_audit,
            "functional_frame_max_tangent_sigma_rad": directional_sigma,
            "gross_guard_sigma_multiplier": float(wear["gross_guard_sigma_multiplier"]),
            "gross_guard_effective_margin_rad": confidence_margin,
            "mean_enters_gross_wrong_hemisphere_reasons": mean_gross_reasons,
            "gross_wrong_hemisphere_reasons": gross_reasons,
            "retained_by_wear_guard": not gross_reasons,
            "broad_wear_prior_added_as_irreducible_covariance": False,
        }
    primary_log_likelihood = float(profile_totals["PRIMARY_55_DEG"])
    gross = any(row["gross_wrong_hemisphere_reasons"] for row in per_segment.values())
    return {
        "primary_log_likelihood": primary_log_likelihood,
        "profile_log_likelihood": profile_totals,
        "gross_wrong_hemisphere": gross,
        "per_segment": per_segment,
        "nominal_body_from_sensor_alternatives": nominal_alternatives,
    }


def _factorized_distal_longitudinal_support(
    sensor_from_segment: Mapping[str, np.ndarray],
    frame_tangent_covariance_rad2: Mapping[str, np.ndarray],
    wear: Mapping[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, np.ndarray]]:
    """Materialize a legacy eight-cell diagnostic quadrature of distal S1.

    A functional hinge axis fixes segment +Y but one surface sensor-to-joint
    lever cannot fix the longitudinal +Z in its orthogonal plane.  Eight
    deterministic, result-independent angles sample that complete S1.  Their
    positive weights use only the existing broad wear likelihood.  This table
    is a conservative covariance/sensitivity diagnostic, not the active
    complete-S1 posterior, an equal-weight global pose mixture, or permission
    to collapse 8**4 combinations to one pose.
    """

    support: dict[str, list[dict[str, Any]]] = {}
    covariance: dict[str, np.ndarray] = {}
    angles = np.arange(DISTAL_LONGITUDINAL_S1_QUADRATURE_COUNT, dtype=float)
    angles *= 2.0 * np.pi / float(DISTAL_LONGITUDINAL_S1_QUADRATURE_COUNT)
    base_segment_from_sensor = {
        segment: np.asarray(value, dtype=float).T.copy()
        for segment, value in sensor_from_segment.items()
    }
    for segment in sorted(SINGLE_JOINT_DISTAL_SEGMENTS):
        base = np.asarray(sensor_from_segment[segment], dtype=float)
        hinge_axis_sensor = base[:, 1]
        base_longitudinal_sensor = base[:, 2]
        rows: list[dict[str, Any]] = []
        raw_logs: list[float] = []
        tangents: list[np.ndarray] = []
        for index, angle in enumerate(angles):
            candidate_z = Rotation.from_rotvec(
                hinge_axis_sensor * float(angle)
            ).apply(base_longitudinal_sensor)
            candidate_sensor_from_segment, local_flags = _frame_from_z_y(
                candidate_z, hinge_axis_sensor,
                label=f"{segment}:LONGITUDINAL_S1_Q{index:02d}",
            )
            candidate_segment_from_sensor = candidate_sensor_from_segment.T
            candidate_map = {
                name: value.copy()
                for name, value in base_segment_from_sensor.items()
            }
            candidate_map[segment] = candidate_segment_from_sensor
            evidence = _wear_evidence(
                candidate_map, frame_tangent_covariance_rad2, wear,
            )
            log_weight = float(evidence["primary_log_likelihood"])
            raw_logs.append(log_weight)
            tangent = Rotation.from_matrix(
                base.T @ candidate_sensor_from_segment
            ).as_rotvec()
            tangents.append(tangent)
            local_gross = bool(
                evidence["per_segment"][segment]["gross_wrong_hemisphere_reasons"]
            )
            rows.append({
                "candidate_id": f"{segment}:LONGITUDINAL_S1_Q{index:02d}",
                "azimuth_from_placeholder_rad": float(angle),
                "sensor_from_segment": candidate_sensor_from_segment.tolist(),
                "segment_from_sensor": candidate_segment_from_sensor.tolist(),
                "hinge_axis_sensor": hinge_axis_sensor.tolist(),
                "hinge_axis_coordinate": [0.0, 1.0, 0.0],
                "primary_wear_log_likelihood": log_weight,
                "gross_wear_contradiction": local_gross,
                "retained": not local_gross,
                "construction_flags": local_flags,
                "sole_joint_lever_used": False,
            })
        retained = [index for index, row in enumerate(rows) if row["retained"]]
        if len(retained) != DISTAL_LONGITUDINAL_S1_QUADRATURE_COUNT:
            raise RuntimeError(
                f"{segment}: broad unresolved S1 support was hard-truncated by wear evidence"
            )
        log_values = np.asarray([raw_logs[index] for index in retained], dtype=float)
        log_values -= np.max(log_values)
        weights = np.exp(log_values)
        weights /= np.sum(weights)
        second_moment = np.zeros((3, 3), dtype=float)
        for local_index, row_index in enumerate(retained):
            rows[row_index]["normalized_factorized_weight"] = float(weights[local_index])
            tangent = tangents[row_index]
            second_moment += float(weights[local_index]) * np.outer(tangent, tangent)
        support[segment] = rows
        covariance[segment] = 0.5 * (second_moment + second_moment.T)
    return support, covariance


def _axis_tangent_basis(axis: np.ndarray) -> np.ndarray:
    direction = np.asarray(axis, dtype=float)
    direction /= np.linalg.norm(direction)
    seed = _least_aligned_basis(direction)
    first = seed - direction * float(direction @ seed)
    first /= np.linalg.norm(first)
    return np.column_stack((first, np.cross(direction, first)))


def _construct_sensor_from_segment(
    center_vectors: Mapping[str, tuple[np.ndarray, np.ndarray]],
    axis_vectors: Mapping[str, tuple[np.ndarray, np.ndarray]],
    signs: Mapping[str, int],
    nominal_segment_from_sensor: Mapping[str, np.ndarray],
) -> tuple[dict[str, np.ndarray], dict[str, list[str]]]:
    """Deterministic representative used by the posterior push-forward.

    A segment with two fitted joints owns a functional longitudinal direction
    through their difference.  A forearm or shank has only its proximal joint;
    its full-R3 sensor-to-joint lever includes the unknown surface/radial
    offset and therefore cannot be normalized into a bone axis.  For those
    four segments the placeholder mean comes from one broad registered wear
    quadrature, while flags force the unidentified-direction covariance.  It
    is deliberately *not* a recovered direction or a materialized posterior
    support set.  The lever remains available for joint placement but never
    defines +Z.  Until a complete-motion likelihood or explicit broad
    longitudinal candidate set exists, these distal axes stay unresolved and
    cannot authorize a tuned wrist/ankle pose.
    """

    by_segment: dict[str, dict[str, np.ndarray]] = {segment: {} for segment in SEGMENTS}
    for edge, (parent, child) in EDGE_BY_NAME.items():
        parent_vector, child_vector = center_vectors[edge]
        by_segment[parent][edge] = np.asarray(parent_vector, dtype=float)
        by_segment[child][edge] = np.asarray(child_vector, dtype=float)
    output: dict[str, np.ndarray] = {}
    flags: dict[str, list[str]] = {}
    pelvis_joints = by_segment["pelvis"]
    pelvis_hip_mid = 0.5 * (pelvis_joints["hip_left"] + pelvis_joints["hip_right"])
    output["pelvis"], flags["pelvis"] = _frame_from_z_y(
        pelvis_joints["pelvis_torso"] - pelvis_hip_mid,
        pelvis_joints["hip_left"] - pelvis_joints["hip_right"], label="pelvis",
    )
    torso_joints = by_segment["torso"]
    shoulder_mid = 0.5 * (torso_joints["shoulder_left"] + torso_joints["shoulder_right"])
    output["torso"], flags["torso"] = _frame_from_z_y(
        shoulder_mid - torso_joints["pelvis_torso"],
        torso_joints["shoulder_left"] - torso_joints["shoulder_right"], label="torso",
    )
    limb_specs = (
        ("upper_arm_left", "shoulder_left", "elbow_left", "elbow_left", 0),
        ("forearm_left", None, "elbow_left", "elbow_left", 1),
        ("upper_arm_right", "shoulder_right", "elbow_right", "elbow_right", 0),
        ("forearm_right", None, "elbow_right", "elbow_right", 1),
        ("thigh_left", "hip_left", "knee_left", "knee_left", 0),
        ("shank_left", None, "knee_left", "knee_left", 1),
        ("thigh_right", "hip_right", "knee_right", "knee_right", 0),
        ("shank_right", None, "knee_right", "knee_right", 1),
    )
    for segment, proximal_edge, distal_edge, hinge_edge, endpoint in limb_specs:
        axis = np.asarray(axis_vectors[hinge_edge][endpoint], dtype=float) * int(signs[hinge_edge])
        if proximal_edge is None:
            nominal = np.asarray(nominal_segment_from_sensor[segment], dtype=float)
            if (
                nominal.shape != (3, 3)
                or not np.allclose(nominal.T @ nominal, np.eye(3), atol=1e-8)
                or np.linalg.det(nominal) <= 0.0
            ):
                raise ValueError(f"{segment}: broad wear quadrature must be proper SO(3)")
            z_hint = nominal.T[:, 2]
            output[segment], local_flags = _frame_from_z_y(z_hint, axis, label=segment)
            flags[segment] = [
                f"{segment}:SINGLE_PROXIMAL_JOINT_LEVER_EXCLUDED_FROM_LONGITUDINAL_Z",
                f"{segment}:LONGITUDINAL_DIRECTION_UNRESOLVED_BROAD_WEAR_QUADRATURE_PLACEHOLDER",
                f"{segment}:DISTAL_ENDPOINT_TUNED_POSE_NOT_AUTHORIZED",
                *local_flags,
            ]
            continue
        z_hint = by_segment[segment][proximal_edge] - by_segment[segment][distal_edge]
        output[segment], flags[segment] = _frame_from_z_y(z_hint, axis, label=segment)
    return output, flags


def _nominal_wear_segment_from_sensor(
    wear: Mapping[str, Any],
) -> dict[str, np.ndarray]:
    """One registered wear quadrature frame per segment, never an exact mount."""

    output: dict[str, np.ndarray] = {}
    for segment in SEGMENTS:
        row = wear["rows_by_segment"][segment]
        if segment in UPPER_ARM_SEGMENTS:
            side_sign = 1.0 if segment.endswith("left") else -1.0
            azimuth = np.pi / 8.0
            nominal_minus_z = np.array([
                -np.cos(azimuth), side_sign * np.sin(azimuth), 0.0,
            ])
        else:
            nominal_minus_z = np.asarray(
                row["nominal_body_vector_for_cone_evaluation"], dtype=float,
            )
        output[segment] = _nominal_body_from_sensor(
            np.asarray(wear["common_nominal"], dtype=float), nominal_minus_z,
        )
    return output


def _construct_online_partial_sensor_from_segment(
    center_vectors: Mapping[str, tuple[np.ndarray, np.ndarray]],
    axis_vectors: Mapping[str, tuple[np.ndarray, np.ndarray]],
    signs: Mapping[str, int],
    nominal_segment_from_sensor: Mapping[str, np.ndarray],
) -> tuple[dict[str, np.ndarray], dict[str, list[str]], set[str]]:
    """Construct only identifiable functional frames; retain wear-wide others."""

    sensor_from_segment = {
        segment: np.asarray(nominal_segment_from_sensor[segment], dtype=float).T.copy()
        for segment in SEGMENTS
    }
    flags = {
        segment: ["ONLINE_FUNCTIONAL_FRAME_UNRESOLVED_WEAR_QUADRATURE_ONLY"]
        for segment in SEGMENTS
    }
    mature: set[str] = set()

    if {"pelvis_torso", "hip_left", "hip_right"}.issubset(center_vectors):
        pelvis_torso = center_vectors["pelvis_torso"][0]
        hip_left = center_vectors["hip_left"][0]
        hip_right = center_vectors["hip_right"][0]
        hip_mid = 0.5 * (hip_left + hip_right)
        sensor_from_segment["pelvis"], flags["pelvis"] = _frame_from_z_y(
            pelvis_torso - hip_mid, hip_left - hip_right, label="pelvis",
        )
        mature.add("pelvis")
    if {"pelvis_torso", "shoulder_left", "shoulder_right"}.issubset(center_vectors):
        pelvis_torso = center_vectors["pelvis_torso"][1]
        shoulder_left = center_vectors["shoulder_left"][0]
        shoulder_right = center_vectors["shoulder_right"][0]
        shoulder_mid = 0.5 * (shoulder_left + shoulder_right)
        sensor_from_segment["torso"], flags["torso"] = _frame_from_z_y(
            shoulder_mid - pelvis_torso,
            shoulder_left - shoulder_right,
            label="torso",
        )
        mature.add("torso")

    limb_specs = (
        ("upper_arm_left", "shoulder_left", "elbow_left", "elbow_left", 0),
        ("forearm_left", None, "elbow_left", "elbow_left", 1),
        ("upper_arm_right", "shoulder_right", "elbow_right", "elbow_right", 0),
        ("forearm_right", None, "elbow_right", "elbow_right", 1),
        ("thigh_left", "hip_left", "knee_left", "knee_left", 0),
        ("shank_left", None, "knee_left", "knee_left", 1),
        ("thigh_right", "hip_right", "knee_right", "knee_right", 0),
        ("shank_right", None, "knee_right", "knee_right", 1),
    )
    for segment, proximal_edge, distal_edge, hinge_edge, endpoint in limb_specs:
        required_centers = {distal_edge} | (
            set() if proximal_edge is None else {proximal_edge}
        )
        if hinge_edge not in axis_vectors or not required_centers.issubset(center_vectors):
            continue
        # One proximal joint plus a surface-mounted sensor does not identify a
        # distal longitudinal axis.  Keep the broad registered quadrature and
        # its unidentified covariance until a distinct motion/longitudinal
        # owner is available; never promote the oblique lever to a mature
        # functional frame.
        if proximal_edge is None:
            nominal = np.asarray(nominal_segment_from_sensor[segment], dtype=float)
            signed_axis = (
                np.asarray(axis_vectors[hinge_edge][endpoint], dtype=float)
                * int(signs[hinge_edge])
            )
            sensor_from_segment[segment], local_flags = _frame_from_z_y(
                nominal.T[:, 2], signed_axis, label=segment,
            )
            flags[segment] = [
                "ONLINE_SINGLE_PROXIMAL_JOINT_LEVER_EXCLUDED_FROM_LONGITUDINAL_Z",
                "ONLINE_LONGITUDINAL_DIRECTION_UNRESOLVED_BROAD_WEAR_QUADRATURE_ONLY",
                "ONLINE_HINGE_AXIS_ALIGNED_FOR_QMT_WITH_UNRESOLVED_LONGITUDINAL_COVARIANCE",
                *local_flags,
            ]
            continue
        distal = center_vectors[distal_edge][endpoint]
        z_hint = center_vectors[proximal_edge][1] - distal
        signed_axis = (
            np.asarray(axis_vectors[hinge_edge][endpoint], dtype=float)
            * int(signs[hinge_edge])
        )
        sensor_from_segment[segment], flags[segment] = _frame_from_z_y(
            z_hint, signed_axis, label=segment,
        )
        mature.add(segment)
    return sensor_from_segment, flags, mature


class SegmentFrameBranchOwner:
    """Construct and retain all functional-frame branches before heading."""

    def __init__(
        self,
        settings: Mapping[str, Any],
        *,
        execution_guard: C2ExecutionGuard,
    ) -> None:
        self.settings = settings
        self.execution_guard = execution_guard
        self.validate_wear_prior(self.settings["wear_prior"])
        self.validate_bilateral_model(hard_mirror=bool(self.settings["bilateral_hard_mirror"]))
        self._wear_authority = _validated_wear_authority(self.settings)
        self._branches: tuple[SegmentFrameBranch, ...] = ()
        self._posterior_weights = np.empty(0, dtype=float)
        self._prefix_audits: list[dict[str, Any]] = []

    @property
    def branches(self) -> tuple[SegmentFrameBranch, ...]:
        return self._branches

    @property
    def segment_names(self) -> tuple[str, ...]:
        """Exact hash-validated segment order owned by the wear authority."""

        return SEGMENTS

    def validate_bilateral_knee_direction(self, left: str, right: str) -> None:
        self.execution_guard.validate_knee_branch(left, right)

    def validate_wear_prior(self, prior: Mapping[str, Any]) -> None:
        self.execution_guard.validate_wear_prior(prior)

    def validate_bilateral_model(self, *, hard_mirror: bool) -> None:
        self.execution_guard.validate_bilateral_model(hard_mirror=hard_mirror)

    def hardware_node_for_segment(self, segment: str) -> str:
        if segment not in self._wear_authority["rows_by_segment"]:
            raise KeyError(f"unknown sealed C2 segment {segment}")
        return str(self._wear_authority["rows_by_segment"][segment]["hardware_id"])

    def wear_log_likelihood_vector(self) -> np.ndarray:
        if not self._branches:
            raise RuntimeError("wear evidence is unavailable before functional frame construction")
        return np.asarray([branch.wear_log_likelihood for branch in self._branches], dtype=float)

    def evaluate_wear_mount_distribution(
        self,
        segment_from_sensor: Mapping[str, np.ndarray],
        frame_tangent_covariance_rad2: Mapping[str, np.ndarray],
    ) -> Mapping[str, Any]:
        """Public owner boundary shared by real build and synthetic mutations."""

        self.validate_wear_prior(self.settings["wear_prior"])
        wear = self._wear_authority
        if set(segment_from_sensor) != set(SEGMENTS) or set(frame_tangent_covariance_rad2) != set(SEGMENTS):
            raise ValueError("wear owner requires ten SO(3) mounts and ten tangent covariances")
        for segment, rotation in segment_from_sensor.items():
            audit = _rotation_audit(rotation)
            if audit["determinant"] < 1.0 - 1e-10 or audit["orthogonality_frobenius_error"] > 1e-10:
                raise ValueError(f"{segment}: wear owner received a non-SO(3) mount")
        return _wear_evidence(segment_from_sensor, frame_tangent_covariance_rad2, wear)

    def update_posterior(self, weights: Sequence[float], *, explicit_lock_requested: bool) -> None:
        if explicit_lock_requested:
            self.execution_guard.update_branch_weights(weights, lock_requested=True)
        self.execution_guard.reject_second_branch_posterior_owner()

    def bind_authoritative_progressive_posterior(
        self,
        branch_ids: Sequence[str],
        weights: Sequence[float],
        *,
        chronological_index: int,
    ) -> None:
        if tuple(branch_ids) != tuple(branch.branch_id for branch in self._branches):
            raise ValueError("progressive branch IDs/order differ from segment-frame branches")
        values = np.asarray(weights, dtype=float)
        if values.shape != (len(self._branches),) or np.any(values < 0.0):
            raise ValueError("authoritative progressive branch posterior shape/support is invalid")
        if not np.isclose(np.sum(values), 1.0) or not np.any(values > 0.0):
            raise ValueError("authoritative progressive branch posterior must be normalized")
        self._posterior_weights = values.copy()
        self._prefix_audits.append({
            "chronological_index": int(chronological_index),
            "authoritative_progressive_branch_ids": list(branch_ids),
            "authoritative_progressive_weights": np.asarray(weights, dtype=float).tolist(),
            "branch_posterior_source": "PROGRESSIVE_CALIBRATION_STATE_IMMUTABLE_SNAPSHOT",
            "second_branch_weight_owner": False,
        })

    def consider_candidate(
        self,
        branch: SegmentFrameBranch,
        assessment: PhysicalCandidateAssessment,
        *,
        residual: float,
    ) -> None:
        if assessment.branch_id != branch.branch_id:
            raise ValueError("physical assessment belongs to a different branch")
        if assessment.evidence.get("rejection_code") == "ONE_KNEE_FORWARD_ONE_KNEE_BACK":
            # Preserve the owner-derived hard physical reason instead of
            # collapsing it into the later generic invalid-candidate guard.
            self.execution_guard.validate_knee_branch(
                assessment.left_knee_direction,
                assessment.right_knee_direction,
            )
        self.execution_guard.consider_candidate(
            physically_legal=bool(branch.retained and assessment.physically_legal),
            residual=float(residual),
        )

    def assess_initial_trajectory_candidate(
        self,
        branch: SegmentFrameBranch,
        initial_world_from_segment: Mapping[str, np.ndarray],
    ) -> PhysicalCandidateAssessment:
        """Evaluate bilateral knee direction before any residual ranking."""

        required = {"thigh_left", "shank_left", "thigh_right", "shank_right"}
        if not required.issubset(initial_world_from_segment):
            raise ValueError("initial trajectory lacks bilateral thigh/shank orientations")
        deadband = float(self.settings["knee_direction_deadband_rad"])

        def direction(side: str) -> tuple[str, float]:
            thigh = np.asarray(initial_world_from_segment[f"thigh_{side}"], dtype=float)
            shank = np.asarray(initial_world_from_segment[f"shank_{side}"], dtype=float)
            if thigh.shape != (3, 3) or shank.shape != (3, 3):
                raise ValueError("initial trajectory rotations must be 3x3")
            hinge = thigh @ np.array([0.0, 1.0, 0.0])
            thigh_long = thigh @ np.array([0.0, 0.0, 1.0])
            shank_long = shank @ np.array([0.0, 0.0, 1.0])
            angle = float(np.arctan2(hinge @ np.cross(shank_long, thigh_long), shank_long @ thigh_long))
            if angle > deadband:
                return "FORWARD", angle
            if angle < -deadband:
                return "BACKWARD", angle
            return "UNRESOLVED_WITHIN_DEADBAND", angle

        left, left_angle = direction("left")
        right, right_angle = direction("right")
        # This real owner call rejects only a verified opposing pair. Straight
        # or low-information standing remains unresolved and all branches live.
        physically_legal = True
        rejection_code = None
        try:
            self.execution_guard.validate_knee_branch(left, right)
        except ClassAGuardViolation as exc:
            if exc.code != "ONE_KNEE_FORWARD_ONE_KNEE_BACK":
                raise
            physically_legal = False
            rejection_code = exc.code
        return PhysicalCandidateAssessment(
            branch_id=branch.branch_id,
            left_knee_direction=left,
            right_knee_direction=right,
            physically_legal=physically_legal,
            evidence={
                "schema": "biospur-c2-initial-trajectory-knee-direction-assessment-v1",
                "derived_from_initial_world_segment_rotations": True,
                "performed_before_residual_ranking": True,
                "deadband_rad": deadband,
                "left_signed_flexion_rad": left_angle,
                "right_signed_flexion_rad": right_angle,
                "unresolved_does_not_eliminate_branch": True,
                "rejection_code": rejection_code,
            },
        )

    def assess_low_information_initial_still_candidate(
        self,
        branch: SegmentFrameBranch,
        sealed_orientation_evidence_by_segment: Mapping[str, Mapping[str, Any]],
    ) -> PhysicalCandidateAssessment:
        """Bind initial-still pixels/arrays as low information, never pose truth."""

        if set(sealed_orientation_evidence_by_segment) != set(SEGMENTS):
            raise ValueError("initial-still evidence must cover the ten exact segment nodes")
        expected_hardware = {
            segment: row[0]
            for row in EXPECTED_WEAR_ROWS
            for segment in (row[1],)
        }
        for segment, evidence in sealed_orientation_evidence_by_segment.items():
            if evidence.get("hardware_id") != expected_hardware[segment]:
                raise ValueError("initial-still evidence hardware/segment mapping differs from sealed identity")
            if evidence.get("source_action") != "00_initial_still" or evidence.get("source_chronological_index") != 0:
                raise ValueError("initial-still physical evidence was relabeled or taken from a future episode")
            if evidence.get("orientation_array_sha256") is None:
                raise ValueError("initial-still orientation evidence lacks an owner-bound array hash")
        return PhysicalCandidateAssessment(
            branch_id=branch.branch_id,
            left_knee_direction="UNRESOLVED_LOW_INFORMATION_INITIAL_STILL",
            right_knee_direction="UNRESOLVED_LOW_INFORMATION_INITIAL_STILL",
            physically_legal=True,
            evidence={
                "schema": "biospur-c2-low-information-initial-still-branch-assessment-v1",
                "source": "RUNTIME_OWNED_SEALED_ORIENTED_ACTION_00_INITIAL_STILL",
                "source_evidence_by_segment": {
                    segment: dict(value)
                    for segment, value in sealed_orientation_evidence_by_segment.items()
                },
                "initial_still_is_pose_truth": False,
                "initial_still_can_hard_eliminate_knee_branch": False,
                "future_episode_or_caller_trajectory_used": False,
                "branch_status": "UNRESOLVED_LOW_INFORMATION_CONTINUE",
            },
        )

    def _connections(self, centers: Mapping[str, CenterEstimate]) -> dict[str, EdgeConnectionVectors]:
        expected = set(EDGE_BY_NAME)
        if set(centers) != expected:
            raise ValueError(f"segment frames require all nine posterior centers; missing={sorted(expected-set(centers))}")
        output: dict[str, EdgeConnectionVectors] = {}
        for edge, (parent, child) in EDGE_BY_NAME.items():
            estimate = centers[edge]
            if estimate.edge != edge or estimate.parent != parent or estimate.child != child:
                raise ValueError(f"{edge}: center endpoint ownership differs from rooted contract")
            parent_vector = -np.asarray(estimate.joint_to_parent_sensor_m, dtype=float)
            child_vector = -np.asarray(estimate.joint_to_child_sensor_m, dtype=float)
            self.execution_guard.validate_connection_vector(parent_vector)
            self.execution_guard.validate_connection_vector(child_vector)
            covariance = np.asarray(estimate.covariance_m2, dtype=float)
            if covariance.shape != (6, 6):
                raise ValueError(f"{edge}: center covariance must remain 6x6")
            output[edge] = EdgeConnectionVectors(
                edge=edge,
                parent=parent,
                child=child,
                parent_sensor_to_joint_m=parent_vector.copy(),
                child_sensor_to_joint_m=child_vector.copy(),
                covariance_m2=covariance.copy(),
            )
        return output

    @staticmethod
    def _vectors_by_segment(
        connections: Mapping[str, EdgeConnectionVectors],
    ) -> dict[str, dict[str, np.ndarray]]:
        output: dict[str, dict[str, np.ndarray]] = {segment: {} for segment in SEGMENTS}
        for edge, value in connections.items():
            output[value.parent][edge] = value.parent_sensor_to_joint_m
            output[value.child][edge] = value.child_sensor_to_joint_m
        return output

    def build_online(
        self,
        axes: Mapping[str, AxisEstimate],
        centers: Mapping[str, CenterEstimate],
        *,
        chronological_index: int,
        action: str,
    ) -> tuple[SegmentFrameBranch, ...]:
        """Build causal edge-local frames, retaining broad unresolved mounts.

        Once all nine centers and four hinge axes exist this delegates to the
        fully qualified frame builder.  Before then, only segments supported
        by their exact functional inputs receive a functional frame; every
        other segment remains a broad wear-quadrature placeholder and is
        explicitly excluded from QMT evidence.
        """

        if set(centers) == set(EDGE_BY_NAME) and set(axes) == set(HINGE_EDGES):
            return self.build(
                axes, centers,
                chronological_index=chronological_index,
                action=action,
            )
        if not set(centers).issubset(EDGE_BY_NAME) or not set(axes).issubset(HINGE_EDGES):
            raise ValueError("online frame inputs contain a noncanonical edge")
        for edge, estimate in centers.items():
            parent, child = EDGE_BY_NAME[edge]
            if (estimate.edge, estimate.parent, estimate.child) != (edge, parent, child):
                raise ValueError(f"{edge}: online center endpoint ownership mismatch")
            if np.asarray(estimate.covariance_m2).shape != (6, 6):
                raise ValueError(f"{edge}: online center covariance must remain 6x6")
        for edge, estimate in axes.items():
            if estimate.edge != edge or np.asarray(estimate.tangent_covariance_rad2).shape != (4, 4):
                raise ValueError(f"{edge}: online axis ownership/covariance mismatch")

        wear = self._wear_authority
        nominal = _nominal_wear_segment_from_sensor(wear)
        center_means = {
            edge: (
                -np.asarray(value.joint_to_parent_sensor_m, dtype=float),
                -np.asarray(value.joint_to_child_sensor_m, dtype=float),
            ) for edge, value in centers.items()
        }
        axis_means = {
            edge: (
                np.asarray(value.parent_axis_sensor, dtype=float),
                np.asarray(value.child_axis_sensor, dtype=float),
            ) for edge, value in axes.items()
        }
        axis_bases: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for edge, estimate in axes.items():
            parent_basis = np.asarray(
                estimate.report["parent_tangent_basis_sensor"], dtype=float,
            )
            child_basis = np.asarray(
                estimate.report["child_tangent_basis_sensor"], dtype=float,
            )
            if parent_basis.shape != (3, 2) or child_basis.shape != (3, 2):
                raise ValueError(f"{edge}: online axis tangent bases must remain 3x2")
            axis_bases[edge] = (parent_basis, child_basis)

        center_slices: dict[str, slice] = {}
        axis_slices: dict[str, slice] = {}
        cursor = 0
        for edge, _, _ in EDGE_SPECS:
            if edge in centers:
                center_slices[edge] = slice(cursor, cursor + 6)
                cursor += 6
        for edge in HINGE_EDGES:
            if edge in axes:
                axis_slices[edge] = slice(cursor, cursor + 4)
                cursor += 4
        source_covariance = np.zeros((cursor, cursor), dtype=float)
        for edge, source_slice in center_slices.items():
            source_covariance[source_slice, source_slice] = np.asarray(
                centers[edge].covariance_m2, dtype=float,
            )
        for edge, source_slice in axis_slices.items():
            source_covariance[source_slice, source_slice] = np.asarray(
                axes[edge].tangent_covariance_rad2, dtype=float,
            )

        connections = {
            edge: EdgeConnectionVectors(
                edge=edge,
                parent=EDGE_BY_NAME[edge][0],
                child=EDGE_BY_NAME[edge][1],
                parent_sensor_to_joint_m=center_means[edge][0].copy(),
                child_sensor_to_joint_m=center_means[edge][1].copy(),
                covariance_m2=np.asarray(centers[edge].covariance_m2, dtype=float).copy(),
            ) for edge in center_means
        }
        segment_index = {segment: index for index, segment in enumerate(SEGMENTS)}
        registered_floor = float(self.settings["nonshrinking_soft_tissue_strap_frame_sigma_rad"])
        registered_weak = float(self.settings["unidentified_direction_sigma_rad"])
        center_step = float(self.settings["numerical_jacobian_center_step_m"])
        axis_step = float(self.settings["numerical_jacobian_axis_step_rad"])
        steps = np.full(cursor, axis_step, dtype=float)
        steps[:6 * len(center_slices)] = center_step
        branches: list[SegmentFrameBranch] = []
        descriptors = hinge_sign_branch_descriptors()
        for descriptor in descriptors:
            signs = {
                str(key): int(value)
                for key, value in descriptor["axis_sign_by_edge"].items()
            }
            sensor_from_segment, frame_flags, mature_segments = (
                _construct_online_partial_sensor_from_segment(
                    center_means, axis_means, signs, nominal,
                )
            )
            segment_from_sensor = {
                segment: value.T.copy()
                for segment, value in sensor_from_segment.items()
            }
            jacobian = np.zeros((3 * len(SEGMENTS), cursor), dtype=float)
            for source_index, step in enumerate(steps):
                positive_centers = {
                    edge: (first.copy(), second.copy())
                    for edge, (first, second) in center_means.items()
                }
                negative_centers = {
                    edge: (first.copy(), second.copy())
                    for edge, (first, second) in center_means.items()
                }
                positive_axes = {
                    edge: (first.copy(), second.copy())
                    for edge, (first, second) in axis_means.items()
                }
                negative_axes = {
                    edge: (first.copy(), second.copy())
                    for edge, (first, second) in axis_means.items()
                }
                for edge, source_slice in center_slices.items():
                    if source_slice.start <= source_index < source_slice.stop:
                        local = source_index - source_slice.start
                        endpoint, coordinate = divmod(local, 3)
                        positive_centers[edge][endpoint][coordinate] += step
                        negative_centers[edge][endpoint][coordinate] -= step
                        break
                else:
                    for edge, source_slice in axis_slices.items():
                        if source_slice.start <= source_index < source_slice.stop:
                            local = source_index - source_slice.start
                            endpoint, coordinate = divmod(local, 2)
                            basis = axis_bases[edge][endpoint]
                            positive = axis_means[edge][endpoint] + basis[:, coordinate] * step
                            negative = axis_means[edge][endpoint] - basis[:, coordinate] * step
                            positive_axes[edge] = list(positive_axes[edge])
                            negative_axes[edge] = list(negative_axes[edge])
                            positive_axes[edge][endpoint] = positive / np.linalg.norm(positive)
                            negative_axes[edge][endpoint] = negative / np.linalg.norm(negative)
                            positive_axes[edge] = tuple(positive_axes[edge])
                            negative_axes[edge] = tuple(negative_axes[edge])
                            break
                positive_frames, _, _ = _construct_online_partial_sensor_from_segment(
                    positive_centers, positive_axes, signs, nominal,
                )
                negative_frames, _, _ = _construct_online_partial_sensor_from_segment(
                    negative_centers, negative_axes, signs, nominal,
                )
                for segment in mature_segments:
                    block = slice(3 * segment_index[segment], 3 * segment_index[segment] + 3)
                    positive_tangent = Rotation.from_matrix(
                        sensor_from_segment[segment].T @ positive_frames[segment]
                    ).as_rotvec()
                    negative_tangent = Rotation.from_matrix(
                        sensor_from_segment[segment].T @ negative_frames[segment]
                    ).as_rotvec()
                    jacobian[block, source_index] = (
                        positive_tangent - negative_tangent
                    ) / (2.0 * step)
            functional_covariance = jacobian @ source_covariance @ jacobian.T
            functional_covariance = 0.5 * (
                functional_covariance + functional_covariance.T
            )
            systematic = np.eye(3 * len(SEGMENTS)) * registered_floor**2
            unidentified = np.zeros_like(systematic)
            for segment in set(SEGMENTS) - mature_segments:
                block = slice(3 * segment_index[segment], 3 * segment_index[segment] + 3)
                unidentified[block, block] = np.eye(3) * registered_weak**2
            joint_covariance = functional_covariance + systematic + unidentified
            frame_covariance = {
                segment: joint_covariance[
                    3 * index:3 * index + 3, 3 * index:3 * index + 3,
                ].copy() for segment, index in segment_index.items()
            }
            paired_covariance = {}
            for edge in HINGE_EDGES:
                parent, child = EDGE_BY_NAME[edge]
                indices = np.r_[
                    np.arange(3 * segment_index[parent], 3 * segment_index[parent] + 3),
                    np.arange(3 * segment_index[child], 3 * segment_index[child] + 3),
                ]
                paired_covariance[edge] = joint_covariance[np.ix_(indices, indices)].copy()
            ready_edges = tuple(
                edge for edge, parent, child in EDGE_SPECS
                if (
                    parent in mature_segments
                    and (
                        child in mature_segments
                        or edge in axis_means
                    )
                )
            )
            wear_evidence = self.evaluate_wear_mount_distribution(
                segment_from_sensor, frame_covariance,
            )
            branches.append(SegmentFrameBranch(
                branch_id=str(descriptor["branch_id"]),
                axis_sign_by_edge=signs,
                segment_from_sensor=segment_from_sensor,
                sensor_from_segment=sensor_from_segment,
                joint_frame_tangent_covariance_rad2=joint_covariance,
                frame_tangent_covariance_rad2=frame_covariance,
                paired_hinge_frame_tangent_covariance_rad2=paired_covariance,
                connection_vectors_by_edge=connections,
                prior_weight=float(descriptor["prior_weight"]),
                wear_log_likelihood=0.0,
                wear_profile_log_likelihood=dict(wear_evidence["profile_log_likelihood"]),
                wear_gross_wrong_hemisphere=False,
                retained=True,
                report={
                    "schema": "biospur-c2-online-partial-functional-wear-segment-frame-branch-v1",
                    "chronological_index": int(chronological_index),
                    "action": str(action),
                    "mature_functional_segments": sorted(mature_segments),
                    "unresolved_wear_quadrature_segments": sorted(set(SEGMENTS) - mature_segments),
                    "qmt_ready_edges": list(ready_edges),
                    "direct_fk_connection_ready_edges": [
                        edge for edge in ready_edges if edge in connections
                    ],
                    "complete_nine_edge_frame_geometry": False,
                    "complete_nine_edge_heading_input": False,
                    "unresolved_distal_longitudinal_mean_enters_qmt_as_truth": False,
                    "broad_quadrature_coordinate_representative_used_with_unidentified_covariance": True,
                    "qualified_hinge_axis_allowed_with_unresolved_distal_longitudinal_covariance": True,
                    "unresolved_frames_allowed_to_enter_direct_fk": False,
                    "wear_quadrature_is_exact_mount": False,
                    "wear_distribution_diagnostic_primary_log_likelihood": float(
                        wear_evidence["primary_log_likelihood"]
                    ),
                    "wear_distribution_diagnostic_gross_wrong_hemisphere": bool(
                        wear_evidence["gross_wrong_hemisphere"]
                    ),
                    "partial_hard_wear_rejection_disabled_until_full_geometry": True,
                    "partial_soft_wear_likelihood_suppressed_until_full_geometry": True,
                    "unresolved_placeholder_wear_used_as_branch_evidence": False,
                    "functional_covariance_pushforward_source_dimension": int(cursor),
                    "broad_unresolved_frame_covariance_nonshrinking": True,
                    "anthropometry_or_action_pose_truth_used": False,
                },
            ))
        normalized = np.asarray(
            [branch.prior_weight for branch in branches], dtype=float,
        )
        normalized /= np.sum(normalized)
        branches = [
            replace(branch, prior_weight=float(normalized[index]))
            for index, branch in enumerate(branches)
        ]
        if self._branches and tuple(branch.branch_id for branch in branches) != tuple(
            branch.branch_id for branch in self._branches
        ):
            raise RuntimeError("online partial frame branch identities changed")
        self._branches = tuple(branches)
        self._posterior_weights = normalized.copy()
        self._prefix_audits.append({
            "chronological_index": int(chronological_index),
            "action": str(action),
            "frame_owner_mode": "EDGE_LOCAL_MATURE_FUNCTIONAL_PLUS_BROAD_WEAR_UNRESOLVED",
            "qmt_ready_edges": list(branches[0].report["qmt_ready_edges"]),
            "complete_nine_edge_frame_geometry": False,
        })
        return self._branches

    def build(
        self,
        axes: Mapping[str, AxisEstimate],
        centers: Mapping[str, CenterEstimate],
        *,
        chronological_index: int | None = None,
        action: str | None = None,
    ) -> tuple[SegmentFrameBranch, ...]:
        """Build frame means/covariance for each hinge-sign branch.

        The four single-joint distal segment means remain explicitly
        unresolved wear-quadrature placeholders.  Ten proper SO(3) matrices
        therefore do not imply ten qualified anatomical segment axes.
        """

        first_build = not self._branches
        if set(axes) != set(HINGE_EDGES):
            raise ValueError("segment frames require all four posterior hinge-axis estimates")
        for edge, estimate in axes.items():
            if estimate.edge != edge:
                raise ValueError(f"{edge}: axis ownership mismatch")
            if np.asarray(estimate.parent_axis_sensor).shape != (3,) or np.asarray(estimate.child_axis_sensor).shape != (3,):
                raise ValueError(f"{edge}: both sign-ambiguous endpoint axes must remain R3")
            if np.asarray(estimate.tangent_covariance_rad2).shape != (4, 4):
                raise ValueError(f"{edge}: axis uncertainty must remain joint 4D tangent covariance")

        # These policies are immutable registry inputs consumed by the real
        # frame-build path, so architecture mutations cannot bypass the owner
        # through a standalone guard call.
        wear = self._wear_authority
        nominal_segment_from_sensor = _nominal_wear_segment_from_sensor(wear)
        connections = self._connections(centers)
        center_means = {
            edge: (
                connection.parent_sensor_to_joint_m.copy(),
                connection.child_sensor_to_joint_m.copy(),
            ) for edge, connection in connections.items()
        }
        axis_means = {
            edge: (
                np.asarray(axes[edge].parent_axis_sensor, dtype=float).copy(),
                np.asarray(axes[edge].child_axis_sensor, dtype=float).copy(),
            ) for edge in HINGE_EDGES
        }
        axis_bases: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for edge in HINGE_EDGES:
            parent_basis = np.asarray(
                axes[edge].report["parent_tangent_basis_sensor"], dtype=float,
            )
            child_basis = np.asarray(
                axes[edge].report["child_tangent_basis_sensor"], dtype=float,
            )
            for endpoint, axis, basis in (
                ("parent", axis_means[edge][0], parent_basis),
                ("child", axis_means[edge][1], child_basis),
            ):
                if basis.shape != (3, 2) or not np.isfinite(basis).all():
                    raise ValueError(f"{edge}:{endpoint}: reported axis tangent basis must be finite 3x2")
                if not np.allclose(basis.T @ basis, np.eye(2), atol=1e-8):
                    raise ValueError(f"{edge}:{endpoint}: reported axis tangent basis is not orthonormal")
                if not np.allclose(basis.T @ axis, np.zeros(2), atol=1e-8):
                    raise ValueError(f"{edge}:{endpoint}: reported axis tangent basis is not tangent to its axis")
            axis_bases[edge] = (parent_basis.copy(), child_basis.copy())
        source_dimension = 6 * len(EDGE_SPECS) + 4 * len(HINGE_EDGES)
        source_covariance = np.zeros((source_dimension, source_dimension), dtype=float)
        source_cursor = 0
        center_source_slices: dict[str, slice] = {}
        for edge, _, _ in EDGE_SPECS:
            center_source_slices[edge] = slice(source_cursor, source_cursor + 6)
            source_covariance[source_cursor:source_cursor + 6, source_cursor:source_cursor + 6] = np.asarray(
                centers[edge].covariance_m2, dtype=float,
            )
            source_cursor += 6
        axis_source_slices: dict[str, slice] = {}
        for edge in HINGE_EDGES:
            axis_source_slices[edge] = slice(source_cursor, source_cursor + 4)
            source_covariance[source_cursor:source_cursor + 4, source_cursor:source_cursor + 4] = np.asarray(
                axes[edge].tangent_covariance_rad2, dtype=float,
            )
            source_cursor += 4
        if source_cursor != source_dimension:
            raise RuntimeError("segment-frame uncertainty source layout mismatch")

        def perturbed_sources(delta: np.ndarray) -> tuple[
            dict[str, tuple[np.ndarray, np.ndarray]],
            dict[str, tuple[np.ndarray, np.ndarray]],
        ]:
            center_values: dict[str, tuple[np.ndarray, np.ndarray]] = {}
            for edge, source_slice in center_source_slices.items():
                change = np.asarray(delta[source_slice], dtype=float)
                center_values[edge] = (
                    center_means[edge][0] + change[:3],
                    center_means[edge][1] + change[3:],
                )
            axis_values: dict[str, tuple[np.ndarray, np.ndarray]] = {}
            for edge, source_slice in axis_source_slices.items():
                change = np.asarray(delta[source_slice], dtype=float)
                parent = axis_means[edge][0] + axis_bases[edge][0] @ change[:2]
                child = axis_means[edge][1] + axis_bases[edge][1] @ change[2:]
                axis_values[edge] = (parent / np.linalg.norm(parent), child / np.linalg.norm(child))
            return center_values, axis_values

        descriptors = enumerate_hinge_sign_branches(
            axes, execution_guard=self.execution_guard,
            register_initial_weights=False,
        )
        branches: list[SegmentFrameBranch] = []
        for descriptor in descriptors:
            signs = {str(key): int(value) for key, value in descriptor["axis_sign_by_edge"].items()}
            sensor_from_segment, frame_flags = _construct_sensor_from_segment(
                center_means, axis_means, signs, nominal_segment_from_sensor,
            )

            segment_from_sensor = {key: value.T.copy() for key, value in sensor_from_segment.items()}
            audits = {key: _rotation_audit(value) for key, value in sensor_from_segment.items()}
            if set(sensor_from_segment) != set(SEGMENTS):
                raise RuntimeError("internal segment-frame construction omitted a segment")
            if any(row["determinant"] < 1.0 - 1e-10 or row["orthogonality_frobenius_error"] > 1e-10 for row in audits.values()):
                raise RuntimeError("segment-frame SO(3) construction failed")
            flags = [flag for segment_flags in frame_flags.values() for flag in segment_flags]
            registered_floor = float(self.settings["nonshrinking_soft_tissue_strap_frame_sigma_rad"])
            registered_weak = float(self.settings["unidentified_direction_sigma_rad"])
            center_step = float(self.settings["numerical_jacobian_center_step_m"])
            axis_step = float(self.settings["numerical_jacobian_axis_step_rad"])
            source_steps = np.full(source_dimension, axis_step, dtype=float)
            source_steps[:6 * len(EDGE_SPECS)] = center_step
            jacobian = np.zeros((3 * len(SEGMENTS), source_dimension), dtype=float)
            for source_index, step in enumerate(source_steps):
                positive = np.zeros(source_dimension, dtype=float)
                negative = np.zeros(source_dimension, dtype=float)
                positive[source_index] = step
                negative[source_index] = -step
                center_positive, axis_positive = perturbed_sources(positive)
                center_negative, axis_negative = perturbed_sources(negative)
                frames_positive, _ = _construct_sensor_from_segment(
                    center_positive, axis_positive, signs, nominal_segment_from_sensor,
                )
                frames_negative, _ = _construct_sensor_from_segment(
                    center_negative, axis_negative, signs, nominal_segment_from_sensor,
                )
                for segment_index, segment in enumerate(SEGMENTS):
                    # Local SO(3) central difference around the nominal frame.
                    tangent_positive = Rotation.from_matrix(
                        sensor_from_segment[segment].T @ frames_positive[segment]
                    ).as_rotvec()
                    tangent_negative = Rotation.from_matrix(
                        sensor_from_segment[segment].T @ frames_negative[segment]
                    ).as_rotvec()
                    jacobian[3 * segment_index:3 * segment_index + 3, source_index] = (
                        tangent_positive - tangent_negative
                    ) / (2.0 * step)
            functional_pushforward_covariance = jacobian @ source_covariance @ jacobian.T
            functional_pushforward_covariance = 0.5 * (
                functional_pushforward_covariance + functional_pushforward_covariance.T
            )
            segment_index_by_name = {
                segment: index for index, segment in enumerate(SEGMENTS)
            }
            nonshrinking_frame_systematic_covariance = np.zeros_like(functional_pushforward_covariance)
            unidentified_direction_covariance = np.zeros_like(functional_pushforward_covariance)
            for local_segment_index, segment in enumerate(SEGMENTS):
                block = slice(3 * local_segment_index, 3 * local_segment_index + 3)
                nonshrinking_frame_systematic_covariance[block, block] = np.eye(3) * registered_floor**2
                if frame_flags[segment]:
                    unidentified_direction_covariance[block, block] = np.eye(3) * registered_weak**2
            preliminary_joint_frame_covariance = (
                functional_pushforward_covariance
                + nonshrinking_frame_systematic_covariance
                + unidentified_direction_covariance
            )
            preliminary_frame_covariance = {
                segment: preliminary_joint_frame_covariance[
                    3 * index:3 * index + 3, 3 * index:3 * index + 3,
                ].copy() for segment, index in segment_index_by_name.items()
            }
            distal_longitudinal_support, distal_support_second_moment = (
                _factorized_distal_longitudinal_support(
                    sensor_from_segment, preliminary_frame_covariance, wear,
                )
            )
            # The explicit S1 support and the registered 60-degree weak floor
            # describe the same unidentified coordinate.  Use a PSD upper
            # envelope rather than summing/double-counting them.  Because the
            # floor is isotropic, clipping support eigenvalues at the floor is
            # the exact shared envelope.
            for segment in sorted(SINGLE_JOINT_DISTAL_SEGMENTS):
                block_index = segment_index_by_name[segment]
                block = slice(3 * block_index, 3 * block_index + 3)
                values, vectors = np.linalg.eigh(distal_support_second_moment[segment])
                envelope = vectors @ np.diag(
                    np.maximum(values, registered_weak**2)
                ) @ vectors.T
                unidentified_direction_covariance[block, block] = 0.5 * (
                    envelope + envelope.T
                )
            joint_frame_covariance = (
                functional_pushforward_covariance
                + nonshrinking_frame_systematic_covariance
                + unidentified_direction_covariance
            )
            minimum_covariance_eigenvalue = float(np.min(np.linalg.eigvalsh(joint_frame_covariance)))
            if minimum_covariance_eigenvalue < -1e-9:
                raise RuntimeError("numerical segment-frame covariance push-forward is not positive semidefinite")
            axis_uncertainty = {
                edge: float(np.trace(np.asarray(axes[edge].tangent_covariance_rad2, dtype=float)))
                for edge in HINGE_EDGES
            }
            center_uncertainty = {
                edge: float(np.trace(np.asarray(centers[edge].covariance_m2, dtype=float)))
                for edge in EDGE_BY_NAME
            }
            frame_covariance = {
                segment: joint_frame_covariance[
                    3 * index:3 * index + 3, 3 * index:3 * index + 3,
                ].copy() for index, segment in enumerate(SEGMENTS)
            }
            wear_evidence = self.evaluate_wear_mount_distribution(segment_from_sensor, frame_covariance)
            paired_hinge_covariance: dict[str, np.ndarray] = {}
            for hinge_edge in HINGE_EDGES:
                hinge_parent, hinge_child = EDGE_BY_NAME[hinge_edge]
                indices = np.r_[
                    np.arange(
                        3 * segment_index_by_name[hinge_parent],
                        3 * segment_index_by_name[hinge_parent] + 3,
                    ),
                    np.arange(
                        3 * segment_index_by_name[hinge_child],
                        3 * segment_index_by_name[hinge_child] + 3,
                    ),
                ]
                paired_hinge_covariance[hinge_edge] = joint_frame_covariance[np.ix_(indices, indices)].copy()
            resolved_longitudinal_segments = tuple(
                segment for segment in SEGMENTS
                if segment not in SINGLE_JOINT_DISTAL_SEGMENTS
            )
            # Heading and endpoint-pose capability are distinct.  A qualified
            # hinge axis remains a valid official one-DoF QMT input while the
            # unknown distal longitudinal/twist direction is represented by
            # the paired-frame covariance.  It must not authorize a tuned
            # wrist/ankle endpoint.
            qmt_ready_edges = tuple(edge for edge, _, _ in EDGE_SPECS)
            distal_longitudinal_status = {
                segment: {
                    "status": "UNRESOLVED_SINGLE_JOINT_PLUS_HINGE_INSUFFICIENT",
                    "sole_joint_lever_used_as_longitudinal_axis": False,
                    "representative_source": "REGISTERED_BROAD_WEAR_QUADRATURE_PLACEHOLDER_ONLY",
                    "complete_motion_likelihood_evaluated": False,
                    "explicit_longitudinal_candidate_support_materialized": True,
                    "candidate_support_type": (
                        "LEGACY_DIAGNOSTIC_EIGHT_POINT_QUADRATURE_OF_COMPLETE_S1_"
                        "NOT_ACTIVE_POSTERIOR"
                    ),
                    "candidate_count": DISTAL_LONGITUDINAL_S1_QUADRATURE_COUNT,
                    "active_complete_s1_owner_required": True,
                    "diagnostic_quadrature_may_select_map_mean_medoid_or_endpoint_pose": False,
                    "tuned_endpoint_render_authorized": False,
                }
                for segment in sorted(SINGLE_JOINT_DISTAL_SEGMENTS)
            }
            minimum_covariance_eigenvalue = float(np.min(np.linalg.eigvalsh(joint_frame_covariance)))
            if minimum_covariance_eigenvalue < -1e-9:
                raise RuntimeError("functional frame posterior covariance is not positive semidefinite")
            branches.append(SegmentFrameBranch(
                branch_id=str(descriptor["branch_id"]),
                axis_sign_by_edge=signs,
                segment_from_sensor=segment_from_sensor,
                sensor_from_segment={key: value.copy() for key, value in sensor_from_segment.items()},
                joint_frame_tangent_covariance_rad2=joint_frame_covariance.copy(),
                frame_tangent_covariance_rad2={key: value.copy() for key, value in frame_covariance.items()},
                paired_hinge_frame_tangent_covariance_rad2={
                    key: value.copy() for key, value in paired_hinge_covariance.items()
                },
                connection_vectors_by_edge=connections,
                prior_weight=float(descriptor["prior_weight"]),
                wear_log_likelihood=float(wear_evidence["primary_log_likelihood"]),
                wear_profile_log_likelihood=dict(wear_evidence["profile_log_likelihood"]),
                wear_gross_wrong_hemisphere=bool(wear_evidence["gross_wrong_hemisphere"]),
                retained=not bool(wear_evidence["gross_wrong_hemisphere"]),
                report={
                    "schema": "biospur-c2-posterior-functional-wear-segment-frame-branch-v2",
                    "segments": list(SEGMENTS),
                    "full_so3_frame_count": len(sensor_from_segment),
                    "full_so3_frame_count_implies_longitudinal_axis_qualification": False,
                    "complete_nine_edge_frame_geometry": False,
                    "complete_nine_edge_heading_input": True,
                    "qmt_ready_edges": list(qmt_ready_edges),
                    "heading_ready_edges": list(qmt_ready_edges),
                    "heading_readiness_is_distal_endpoint_pose_readiness": False,
                    "qualified_hinge_axis_qmt_preserved_for_unresolved_distal_segments": list(HINGE_EDGES),
                    "unresolved_distal_longitudinal_or_axial_twist_enters_paired_frame_covariance": True,
                    "hinge_joint_axis_coordinate_invariant_to_distal_longitudinal_placeholder": True,
                    "official_qmt_delta_invariance_to_distal_longitudinal_placeholder_claimed": False,
                    "resolved_longitudinal_segments": list(resolved_longitudinal_segments),
                    "distal_longitudinal_axis_status_by_segment": distal_longitudinal_status,
                    "distal_longitudinal_factorized_support_by_segment": distal_longitudinal_support,
                    "distal_longitudinal_support_tangent_second_moment_rad2": {
                        segment: value.tolist()
                        for segment, value in distal_support_second_moment.items()
                    },
                    "distal_longitudinal_support_factorized_not_global_pose_combinations": True,
                    "distal_longitudinal_support_selected_by_pixels_or_pose_truth": False,
                    "distal_longitudinal_eight_point_quadrature_role": (
                        "LEGACY_DIAGNOSTIC_AND_CONSERVATIVE_COVARIANCE_ENVELOPE_ONLY"
                    ),
                    "distal_longitudinal_eight_point_quadrature_is_active_complete_s1_posterior": False,
                    "distal_longitudinal_eight_point_quadrature_authorizes_qmt_or_endpoint_pose": False,
                    "distal_longitudinal_support_weights_from_existing_broad_wear_likelihood": True,
                    "distal_longitudinal_support_covariance_psd_upper_envelope_not_double_counted": True,
                    "distal_longitudinal_axis_qualification_complete": False,
                    "distal_endpoint_tuned_pose_render_authorized": False,
                    "frame_convention": "SENSOR_FROM_SEGMENT_COLUMNS_[X,Y,Z];SEGMENT_FROM_SENSOR_IS_TRANSPOSE",
                    "frame_sources": "QUALIFIED_TWO_CENTER_OR_MULTI_CENTER_FUNCTIONAL_DIRECTIONS_WHERE_AVAILABLE;SINGLE_JOINT_DISTAL_AXES_REMAIN_BROAD_WEAR_PLACEHOLDERS",
                    "anthropometry_or_action_pose_truth_used": False,
                    "connection_vectors_per_edge": 2,
                    "connection_vector_dimension": 3,
                    "axis_tangent_covariance_trace_rad2": axis_uncertainty,
                    "center_covariance_trace_m2": center_uncertainty,
                    "frame_distribution": "LOCAL_SO3_TANGENT_GAUSSIAN_WITH_CROSS_ENDPOINT_HINGE_COVARIANCE",
                    "joint_frame_tangent_state_order": [
                        f"{segment}:[rx,ry,rz]" for segment in SEGMENTS
                    ],
                    "joint_frame_tangent_covariance_shape": list(joint_frame_covariance.shape),
                    "joint_frame_tangent_covariance_minimum_eigenvalue_rad2": minimum_covariance_eigenvalue,
                    "frame_tangent_covariance_shape_by_segment": {
                        key: list(value.shape) for key, value in frame_covariance.items()
                    },
                    "paired_hinge_frame_tangent_covariance_shape": {
                        key: list(value.shape) for key, value in paired_hinge_covariance.items()
                    },
                    "covariance_push_forward": "NUMERICAL_CENTRAL_JACOBIAN_FROM_54D_CENTER_PLUS_16D_ACTUAL_AXIS_TANGENT_BASES_TO_CORRELATED_30D_SO3_TANGENTS",
                    "functional_pushforward_joint_frame_tangent_covariance_rad2": functional_pushforward_covariance.tolist(),
                    "nonshrinking_soft_tissue_strap_joint_frame_tangent_covariance_rad2": nonshrinking_frame_systematic_covariance.tolist(),
                    "unidentified_direction_joint_frame_tangent_covariance_rad2": unidentified_direction_covariance.tolist(),
                    "broad_wear_prior_added_as_irreducible_covariance": False,
                    "wear_prior_and_systematic_uncertainty_are_separate_owners": True,
                    "soft_tissue_strap_systematic_added_to_repeated_data_information": False,
                    "source_covariance_shape": list(source_covariance.shape),
                    "source_center_dimension": 6 * len(EDGE_SPECS),
                    "source_axis_tangent_dimension": 4 * len(HINGE_EDGES),
                    "full_joint_4d_axis_covariance_and_cross_endpoint_terms_consumed": True,
                    "axis_tangent_coordinate_bases_source": "ACTUAL_AXIS_ESTIMATE_REPORTED_PARENT_AND_CHILD_BASES",
                    "numerical_jacobian_center_step_m": center_step,
                    "numerical_jacobian_axis_step_rad": axis_step,
                    "nonshrinking_soft_tissue_strap_frame_sigma_rad": registered_floor,
                    "unidentified_direction_sigma_rad": registered_weak,
                    "so3_audit": audits,
                    "low_information_flags": flags,
                    "low_information_widens_or_reduces_validity_without_terminating": True,
                    "deterministic_so3_is_representative_not_exact_posterior": True,
                    "wear_authority_hashes": dict(wear["hashes"]),
                    "wear_body_frame": dict(wear["authority"]["body_frame"]),
                    "wear_direction_support": "BROAD_EVERYWHERE_POSITIVE_SOFT_SPHERICAL_LIKELIHOOD;NO_HARD_NUMERIC_CONE",
                    "wear_primary_log_likelihood": float(wear_evidence["primary_log_likelihood"]),
                    "wear_sensitivity_profile_log_likelihood": dict(wear_evidence["profile_log_likelihood"]),
                    "wear_per_segment_evidence": dict(wear_evidence["per_segment"]),
                    "wear_nominal_right_handed_body_from_sensor_frame_alternatives": dict(
                        wear_evidence["nominal_body_from_sensor_alternatives"]
                    ),
                    "wear_nominal_frames_are_quadrature_audit_points_not_exact_extrinsics": True,
                    "wear_weak_twist_retained_as_soft_distribution_not_irreducible_covariance": True,
                    "wear_gross_wrong_hemisphere": bool(wear_evidence["gross_wrong_hemisphere"]),
                    "wear_only_gross_wrong_hemisphere_can_reject": True,
                },
            ))
        retained_indices = [index for index, branch in enumerate(branches) if branch.retained]
        if not retained_indices:
            raise RuntimeError("gross qualitative wear evidence eliminated every functional frame branch")
        retained_logs = np.asarray([branches[index].wear_log_likelihood for index in retained_indices])
        retained_logs -= np.max(retained_logs)
        retained_weights = np.exp(retained_logs)
        retained_weights /= np.sum(retained_weights)
        normalized = np.zeros(len(branches), dtype=float)
        normalized[retained_indices] = retained_weights
        branches = [
            replace(branch, prior_weight=float(normalized[index]))
            for index, branch in enumerate(branches)
        ]
        if not first_build and [branch.branch_id for branch in branches] != [branch.branch_id for branch in self._branches]:
            raise RuntimeError("progressive frame update changed the retained branch identity set")
        self._branches = tuple(branches)
        if first_build:
            # The progressive state owns the sole posterior. Descriptor priors
            # remain inside branch evidence but are not a second live vector.
            self._posterior_weights = np.empty(0, dtype=float)
        self._prefix_audits.append({
            "chronological_index": chronological_index,
            "action": action,
            "first_build": first_build,
            "branch_count": len(branches),
            "future_episode_geometry_used": False,
            "posterior_weights_reset": False if not first_build else None,
            "descriptor_prior_registered_as_second_live_posterior": False,
            "wear_authority_hashes": dict(wear["hashes"]),
            "wear_gross_rejected_branch_ids": [branch.branch_id for branch in branches if not branch.retained],
            "wear_retained_branch_ids": [branch.branch_id for branch in branches if branch.retained],
            "wear_soft_prior_weight_by_branch": {
                branch.branch_id: branch.prior_weight for branch in branches
            },
            "wear_likelihood_committed_to_progressive_state_here": False,
        })
        return self._branches

    def get(self, branch_id: str) -> SegmentFrameBranch:
        matches = [branch for branch in self._branches if branch.branch_id == branch_id]
        if len(matches) != 1:
            raise KeyError(f"unknown retained segment-frame branch {branch_id}")
        return matches[0]

    def audit(self) -> dict[str, Any]:
        return {
            "schema": "biospur-c2-segment-frame-branch-owner-v2",
            "branch_count": len(self._branches),
            "full_so3_frames_per_branch": 10 if self._branches else 0,
            "two_r3_connection_vectors_per_edge": bool(self._branches),
            "branch_ids": [branch.branch_id for branch in self._branches],
            "posterior_weights": self._posterior_weights.tolist(),
            "branch_posterior_source": (
                "PROGRESSIVE_CALIBRATION_STATE_IMMUTABLE_SNAPSHOT"
                if len(self._posterior_weights) else "NOT_YET_BOUND_BEFORE_FIRST_CAUSAL_COMMIT"
            ),
            "prefix_updates": list(self._prefix_audits),
            "anthropometry_used": False,
            "candidate_lock": False,
            "wear_authority_consumed": bool(self._branches),
            "wear_authority_hashes": (
                {} if not self._branches else dict(self._branches[0].report["wear_authority_hashes"])
            ),
            "wear_soft_log_likelihood_by_branch": {
                branch.branch_id: branch.wear_log_likelihood for branch in self._branches
            },
            "wear_gross_rejected_branch_ids": [
                branch.branch_id for branch in self._branches if not branch.retained
            ],
            "wear_noncompact_soft_support": True,
            "wear_prior_is_not_irreducible_covariance": True,
            "weak_twist_prior_remains_soft_and_functional_information_can_narrow_it": True,
        }

    def checkpoint(self) -> Mapping[str, Any]:
        return {
            "branches": deepcopy(self._branches),
            "posterior_weights": self._posterior_weights.copy(),
            "prefix_audits": deepcopy(self._prefix_audits),
        }

    def restore(self, checkpoint: Mapping[str, Any]) -> None:
        self._branches = deepcopy(checkpoint["branches"])
        self._posterior_weights = np.asarray(checkpoint["posterior_weights"], dtype=float).copy()
        self._prefix_audits = deepcopy(checkpoint["prefix_audits"])


def numeric_wear_direction_owner_gate(
    settings: Mapping[str, Any],
    *,
    execution_guard: C2ExecutionGuard,
) -> Mapping[str, Any]:
    """Executed positive, sensitivity, identity, and gross-guard wear oracle."""

    owner = SegmentFrameBranchOwner(settings, execution_guard=execution_guard)
    wear = _validated_wear_authority(settings)

    def nominal_mounts() -> dict[str, np.ndarray]:
        mounts: dict[str, np.ndarray] = {}
        for segment in SEGMENTS:
            row = wear["rows_by_segment"][segment]
            if segment in UPPER_ARM_SEGMENTS:
                side_sign = 1 if segment.endswith("left") else -1
                azimuth = np.pi / 8.0
                minus_z = np.array([-np.cos(azimuth), side_sign * np.sin(azimuth), 0.0])
            else:
                minus_z = np.asarray(row["nominal_body_vector_for_cone_evaluation"], dtype=float)
            mounts[segment] = _nominal_body_from_sensor(wear["common_nominal"], minus_z)
        return mounts

    primary_mounts = nominal_mounts()
    narrow_covariance = {
        segment: np.eye(3) * np.deg2rad(3.0) ** 2 for segment in SEGMENTS
    }
    primary = owner.evaluate_wear_mount_distribution(primary_mounts, narrow_covariance)

    near_mounts = {segment: value.copy() for segment, value in primary_mounts.items()}
    near_mounts["torso"] = _nominal_body_from_sensor(
        wear["common_nominal"], np.array([0.0, 1.0, 0.0]),
    )
    near = owner.evaluate_wear_mount_distribution(near_mounts, narrow_covariance)

    gross_mounts = {segment: value.copy() for segment, value in primary_mounts.items()}
    gross_mounts["torso"] = _nominal_body_from_sensor(
        wear["common_nominal"], np.array([-1.0, 0.0, 0.0]),
    )
    gross = owner.evaluate_wear_mount_distribution(gross_mounts, narrow_covariance)
    uncertain_covariance = {segment: value.copy() for segment, value in narrow_covariance.items()}
    uncertain_covariance["torso"] = np.eye(3) * np.deg2rad(40.0) ** 2
    uncertain_gross_mean = owner.evaluate_wear_mount_distribution(gross_mounts, uncertain_covariance)

    mapping_mutation_rejected = False
    mapping_mutation_message = None
    mutated = deepcopy(settings)
    mutated_rows = mutated["wear_authority"]["rows"]
    mutated_rows[0]["hardware_id"], mutated_rows[1]["hardware_id"] = (
        mutated_rows[1]["hardware_id"], mutated_rows[0]["hardware_id"],
    )
    try:
        SegmentFrameBranchOwner(
            mutated, execution_guard=execution_guard,
        ).evaluate_wear_mount_distribution(primary_mounts, narrow_covariance)
    except ValueError as exc:
        mapping_mutation_rejected = True
        mapping_mutation_message = str(exc)

    primary_profiles = dict(primary["profile_log_likelihood"])
    comparable_profiles = all(np.isfinite(value) for value in primary_profiles.values())
    sensitivity_changes = len({round(float(value), 12) for value in near["profile_log_likelihood"].values()}) > 1
    pass_gate = bool(
        not primary["gross_wrong_hemisphere"]
        and not near["gross_wrong_hemisphere"]
        and gross["gross_wrong_hemisphere"]
        and not uncertain_gross_mean["gross_wrong_hemisphere"]
        and mapping_mutation_rejected
        and comparable_profiles
        and sensitivity_changes
    )
    return {
        "schema": "biospur-c2-wear-direction-owner-numeric-gate-v1",
        "coverage_class": "EXECUTED_OWNER_LEVEL",
        "owner": "SegmentFrameBranchOwner.evaluate_wear_mount_distribution",
        "authority_hashes": dict(wear["hashes"]),
        "positive_primary": {
            "injected": "TEN_SEALED_NOMINAL_RIGHT_HANDED_MOUNTS",
            "expected": "ALL_RETAINED_WITH_FINITE_BROAD_SOFT_LIKELIHOODS",
            "observed_gross_rejection": bool(primary["gross_wrong_hemisphere"]),
            "profile_log_likelihood": primary_profiles,
        },
        "near_uninformative_hemisphere": {
            "injected": "TORSO_MINUS_Z_ORTHOGONAL_TO_FORWARD_WITH_MINUS_Y_GROUND",
            "expected": "RETAINED_AND_SENSITIVITY_PROFILE_CHANGES_WITHOUT_HARD_CONE",
            "observed_gross_rejection": bool(near["gross_wrong_hemisphere"]),
            "profile_log_likelihood": dict(near["profile_log_likelihood"]),
        },
        "gross_wrong_hemisphere": {
            "injected": "TORSO_MINUS_Z_EXACTLY_BACKWARD_WITH_3_DEG_FUNCTIONAL_FRAME_SIGMA",
            "expected": "REJECT",
            "observed_rejection": bool(gross["gross_wrong_hemisphere"]),
        },
        "uncertainty_qualified_gross_guard": {
            "injected": "SAME_BACKWARD_MEAN_WITH_40_DEG_FUNCTIONAL_FRAME_SIGMA",
            "expected": "NO_HARD_REJECTION_BECAUSE_GROSS_SIGN_IS_NOT_CONFIDENT",
            "observed_rejection": bool(uncertain_gross_mean["gross_wrong_hemisphere"]),
        },
        "swapped_hardware_identity_mutation": {
            "injected": "SWAP_BSFEC35_AND_BSFB165_WITH_DIRECTIONS_UNCHANGED",
            "expected": "REJECT_BEFORE_WEAR_EVALUATION",
            "observed_rejection": mapping_mutation_rejected,
            "message": mapping_mutation_message,
        },
        "every_profile_composes_one_minus_y_and_one_minus_z_term_per_node": True,
        "broad_wear_prior_added_as_irreducible_covariance": False,
        "pass": pass_gate,
    }


def numeric_frame_covariance_rotation_gate(
    settings: Mapping[str, Any],
    axes: Mapping[str, AxisEstimate],
    centers: Mapping[str, CenterEstimate],
    *,
    execution_guard: C2ExecutionGuard,
) -> Mapping[str, Any]:
    """Known sensor-coordinate rotation oracle for the nonlinear covariance push-forward."""

    rotations = {
        segment: Rotation.from_rotvec(
            np.array([0.11 + 0.013 * index, -0.07 + 0.009 * index, 0.05 - 0.004 * index])
        ).as_matrix()
        for index, segment in enumerate(SEGMENTS)
    }
    transformed_centers: dict[str, CenterEstimate] = {}
    for edge, parent, child in EDGE_SPECS:
        estimate = centers[edge]
        coordinate = np.zeros((6, 6), dtype=float)
        coordinate[:3, :3] = rotations[parent]
        coordinate[3:, 3:] = rotations[child]
        transformed_centers[edge] = CenterEstimate(
            edge=edge, parent=parent, child=child,
            joint_to_parent_sensor_m=rotations[parent] @ np.asarray(estimate.joint_to_parent_sensor_m),
            joint_to_child_sensor_m=rotations[child] @ np.asarray(estimate.joint_to_child_sensor_m),
            covariance_m2=coordinate @ np.asarray(estimate.covariance_m2) @ coordinate.T,
            report=dict(estimate.report),
        )
    transformed_axes: dict[str, AxisEstimate] = {}
    endpoints = {edge: (parent, child) for edge, parent, child in EDGE_SPECS}
    for edge in HINGE_EDGES:
        estimate = axes[edge]
        parent, child = endpoints[edge]
        report = dict(estimate.report)
        report["parent_tangent_basis_sensor"] = (
            rotations[parent] @ np.asarray(estimate.report["parent_tangent_basis_sensor"])
        ).tolist()
        report["child_tangent_basis_sensor"] = (
            rotations[child] @ np.asarray(estimate.report["child_tangent_basis_sensor"])
        ).tolist()
        transformed_axes[edge] = AxisEstimate(
            edge=edge,
            parent_axis_sensor=rotations[parent] @ np.asarray(estimate.parent_axis_sensor),
            child_axis_sensor=rotations[child] @ np.asarray(estimate.child_axis_sensor),
            tangent_covariance_rad2=np.asarray(estimate.tangent_covariance_rad2).copy(),
            report=report,
        )
    baseline_owner = SegmentFrameBranchOwner(settings, execution_guard=execution_guard)
    transformed_owner = SegmentFrameBranchOwner(settings, execution_guard=execution_guard)
    baseline = baseline_owner.build(axes, centers, chronological_index=0, action="KNOWN_ROTATION_BASE")
    transformed = transformed_owner.build(
        transformed_axes, transformed_centers,
        chronological_index=0, action="KNOWN_ROTATION_TRANSFORMED",
    )
    tolerance = float(settings["known_rotation_covariance_absolute_tolerance"])
    frame_errors: list[float] = []
    covariance_errors: list[float] = []
    for left, right in zip(baseline, transformed, strict=True):
        if left.branch_id != right.branch_id:
            raise RuntimeError("known-rotation gate changed branch identity/order")
        frame_errors.extend(
            float(np.max(np.abs(
                right.sensor_from_segment[segment]
                - rotations[segment] @ left.sensor_from_segment[segment]
            )))
            for segment in SEGMENTS
        )
        covariance_errors.append(float(np.max(np.abs(
            right.joint_frame_tangent_covariance_rad2
            - left.joint_frame_tangent_covariance_rad2
        ))))
    return {
        "schema": "biospur-c2-segment-frame-known-sensor-coordinate-rotation-gate-v1",
        "coverage_class": "EXECUTED_OWNER_LEVEL",
        "branch_count": len(baseline),
        "actual_axis_tangent_bases_rotated_and_consumed": True,
        "center_cross_endpoint_covariance_rotated": True,
        "maximum_frame_equivariance_error": max(frame_errors, default=np.inf),
        "maximum_joint_frame_covariance_invariance_error_rad2": max(covariance_errors, default=np.inf),
        "absolute_tolerance": tolerance,
        "pass": bool(
            len(baseline) == 16
            and max(frame_errors, default=np.inf) <= tolerance
            and max(covariance_errors, default=np.inf) <= tolerance
        ),
    }
