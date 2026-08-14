"""V4.1 centerline quotient with bounded sensor-placement nuisance states."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from biospur_fusion.calibration.anthropometry_v4_1 import (
    AnthropometryV41,
    NODE_TO_SEGMENT,
    SEGMENT_TO_NODE,
)
from biospur_fusion.calibration.articulated_batch import CalibrationSamples, SEGMENTS, SEGMENT_INDEX


LIMB_SEGMENTS = (
    "UpperArm_L", "Forearm_L", "UpperArm_R", "Forearm_R",
    "Thigh_L", "Shank_L", "Thigh_R", "Shank_R",
)
BASE_PARAMETER_COUNT = 2 + 3 + 3 + 2 * len(LIMB_SEGMENTS)


def axis_from_angles(values: np.ndarray) -> np.ndarray:
    azimuth, elevation = np.asarray(values, float)
    cosine = np.cos(elevation)
    return np.array([cosine * np.cos(azimuth), cosine * np.sin(azimuth), np.sin(elevation)])


def angles_from_axis(axis: np.ndarray) -> np.ndarray:
    value = np.asarray(axis, float)
    value /= np.linalg.norm(value)
    return np.array([np.arctan2(value[1], value[0]), np.arcsin(np.clip(value[2], -1.0, 1.0))])


def _r_nv(two_axis: np.ndarray) -> np.ndarray:
    return Rotation.from_euler("yx", [float(two_axis[1]), float(two_axis[0])]).as_matrix()


@dataclass(frozen=True)
class QuotientStaticV41:
    R_N_from_V4: np.ndarray
    R_pelvis_from_sensor: np.ndarray
    R_torso_from_sensor: np.ndarray
    limb_axis_sensor: Mapping[str, np.ndarray]
    capture_enclosure_to_landmark_m: Mapping[str, np.ndarray]


def nuisance_nodes(anthropometry: AnthropometryV41) -> tuple[str, ...]:
    return tuple(
        SEGMENT_TO_NODE[segment]
        for segment in SEGMENTS
        if anthropometry.placements[SEGMENT_TO_NODE[segment]].estimate_as_nuisance
    )


def unpack(vector: np.ndarray, anthropometry: AnthropometryV41,
           estimated_nodes: tuple[str, ...] | None = None) -> QuotientStaticV41:
    estimated_nodes = nuisance_nodes(anthropometry) if estimated_nodes is None else estimated_nodes
    vector = np.asarray(vector, float)
    expected = BASE_PARAMETER_COUNT + 3 * len(estimated_nodes)
    if vector.shape != (expected,):
        raise ValueError(f"V4.1 quotient parameter shape mismatch: {vector.shape} != {(expected,)}")
    axes: dict[str, np.ndarray] = {}
    offset = 8
    for index, segment in enumerate(LIMB_SEGMENTS):
        axes[segment] = axis_from_angles(vector[offset + 2 * index:offset + 2 * index + 2])
    capture = {
        node: np.asarray(placement.capture_prior_m, float).copy()
        for node, placement in anthropometry.placements.items()
    }
    start = BASE_PARAMETER_COUNT
    for index, node in enumerate(estimated_nodes):
        capture[node] = vector[start + 3 * index:start + 3 * index + 3].copy()
    return QuotientStaticV41(
        R_N_from_V4=_r_nv(vector[:2]),
        R_pelvis_from_sensor=Rotation.from_rotvec(vector[2:5]).as_matrix(),
        R_torso_from_sensor=Rotation.from_rotvec(vector[5:8]).as_matrix(),
        limb_axis_sensor=axes,
        capture_enclosure_to_landmark_m=capture,
    )


def pack(value: QuotientStaticV41, anthropometry: AnthropometryV41,
         estimated_nodes: tuple[str, ...] | None = None) -> np.ndarray:
    estimated_nodes = nuisance_nodes(anthropometry) if estimated_nodes is None else estimated_nodes
    euler = Rotation.from_matrix(value.R_N_from_V4).as_euler("yxz")
    rows = [
        np.array([euler[1], euler[0]]),
        Rotation.from_matrix(value.R_pelvis_from_sensor).as_rotvec(),
        Rotation.from_matrix(value.R_torso_from_sensor).as_rotvec(),
    ]
    rows.extend(angles_from_axis(value.limb_axis_sensor[segment]) for segment in LIMB_SEGMENTS)
    rows.extend(np.asarray(value.capture_enclosure_to_landmark_m[node], float)
                for node in estimated_nodes)
    return np.concatenate(rows)


def segment_axes(samples: CalibrationSamples, static: QuotientStaticV41) -> tuple[np.ndarray, np.ndarray]:
    count = len(samples.time_ns)
    p = SEGMENT_INDEX
    q = samples.orientation_N_from_B
    segment_rotations = np.empty((count, 2, 3, 3))
    segment_rotations[:, 0] = q[:, p["Pelvis"]] @ static.R_pelvis_from_sensor.T
    segment_rotations[:, 1] = q[:, p["Torso"]] @ static.R_torso_from_sensor.T
    axes = np.empty((count, len(SEGMENTS), 3))
    axes[:, p["Pelvis"]] = segment_rotations[:, 0, :, 2]
    axes[:, p["Torso"]] = segment_rotations[:, 1, :, 2]
    for segment in LIMB_SEGMENTS:
        axes[:, p[segment]] = np.einsum(
            "nij,j->ni", q[:, p[segment]], static.limb_axis_sensor[segment])
    return axes, segment_rotations


def predict_joint_centres(samples: CalibrationSamples, static: QuotientStaticV41,
                          anthropometry: AnthropometryV41) -> tuple[np.ndarray, np.ndarray]:
    """Return anatomical centerline landmarks relative to the pelvis landmark.

    These locations do not depend on any antenna/enclosure placement parameter.
    """
    axes, rotations = segment_axes(samples, static)
    p = SEGMENT_INDEX
    geometry = anthropometry.geometry()
    output = np.zeros((len(samples.time_ns), len(SEGMENTS), 3), float)
    c7 = axes[:, p["Torso"]] * geometry.torso_separation_m
    output[:, p["Torso"]] = c7
    for side, sign in (("L", -1.0), ("R", 1.0)):
        upper = f"UpperArm_{side}"
        forearm = f"Forearm_{side}"
        thigh = f"Thigh_{side}"
        shank = f"Shank_{side}"
        shoulder = c7 + np.einsum(
            "nij,j->ni", rotations[:, 1], [sign * geometry.shoulder_half_width_m, 0.0, 0.0])
        elbow = shoulder - axes[:, p[upper]] * getattr(geometry, f"upper_arm_{side}_m")
        wrist = elbow - axes[:, p[forearm]] * getattr(geometry, f"forearm_{side}_m")
        output[:, p[upper]] = elbow
        output[:, p[forearm]] = wrist
        hip = np.einsum(
            "nij,j->ni", rotations[:, 0],
            [sign * geometry.hip_half_width_m, 0.0, geometry.hip_vertical_m])
        knee = hip - axes[:, p[thigh]] * getattr(geometry, f"thigh_{side}_m")
        ankle = knee - axes[:, p[shank]] * getattr(geometry, f"shank_{side}_m")
        output[:, p[thigh]] = knee
        output[:, p[shank]] = ankle
    return output, axes


def predict_antennas(samples: CalibrationSamples, static: QuotientStaticV41,
                     anthropometry: AnthropometryV41) -> tuple[np.ndarray, np.ndarray]:
    """Return antenna phase centres relative to the pelvis antenna."""
    joints, axes = predict_joint_centres(samples, static, anthropometry)
    q = samples.orientation_N_from_B
    absolute = np.empty_like(joints)
    for segment_index, segment in enumerate(SEGMENTS):
        node = SEGMENT_TO_NODE[segment]
        placement = anthropometry.placements[node]
        offset = (placement.pcb_phase_centre_to_enclosure_m
                  + static.capture_enclosure_to_landmark_m[node])
        absolute[:, segment_index] = (
            joints[:, segment_index]
            - np.einsum("nij,j->ni", q[:, segment_index], offset)
        )
    pelvis = SEGMENT_INDEX["Pelvis"]
    return absolute - absolute[:, pelvis:pelvis + 1], axes


def physical_difference(problem: "CenterlineQuotientProblemV41",
                        left: np.ndarray, right: np.ndarray) -> dict:
    left_static = problem.unpack(left)
    right_static = problem.unpack(right)
    left_joints, left_axes = predict_joint_centres(problem.samples, left_static, problem.anthropometry)
    right_joints, right_axes = predict_joint_centres(problem.samples, right_static, problem.anthropometry)
    left_antennas, _ = predict_antennas(problem.samples, left_static, problem.anthropometry)
    right_antennas, _ = predict_antennas(problem.samples, right_static, problem.anthropometry)
    dots = np.clip(np.sum(left_axes * right_axes, axis=2), -1.0, 1.0)
    angle = float(np.max(np.arccos(dots)))
    joint_displacement = float(np.max(np.linalg.norm(right_joints - left_joints, axis=2)))
    antenna_displacement = float(np.max(np.linalg.norm(right_antennas - left_antennas, axis=2)))
    return {
        "maximum_segment_axis_angular_change_rad": angle,
        "maximum_segment_axis_angular_change_deg": float(np.degrees(angle)),
        "maximum_joint_centre_displacement_mm": joint_displacement * 1000.0,
        "maximum_antenna_displacement_mm": antenna_displacement * 1000.0,
    }


class CenterlineQuotientProblemV41:
    """Centerline state plus every evidence-bounded capture placement offset."""

    def __init__(self, samples: CalibrationSamples, anthropometry: AnthropometryV41):
        self.samples = samples
        self.anthropometry = anthropometry
        self.estimated_nodes = nuisance_nodes(anthropometry)
        self.parameter_names = (
            "R_N_from_V4.roll", "R_N_from_V4.pitch",
            *(f"R_Pelvis_from_sensor.{axis}" for axis in "xyz"),
            *(f"R_Torso_from_sensor.{axis}" for axis in "xyz"),
            *(f"axis_{segment}.{angle}" for segment in LIMB_SEGMENTS
              for angle in ("azimuth", "elevation")),
            *(f"capture_offset.{node}.{axis}" for node in self.estimated_nodes for axis in "xyz"),
        )

    def unpack(self, vector: np.ndarray) -> QuotientStaticV41:
        return unpack(vector, self.anthropometry, self.estimated_nodes)

    def initial_vector(self, base: QuotientStaticV41) -> np.ndarray:
        return pack(base, self.anthropometry, self.estimated_nodes)

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        lower = np.full(BASE_PARAMETER_COUNT, -np.pi, float)
        upper = np.full(BASE_PARAMETER_COUNT, np.pi, float)
        for node in self.estimated_nodes:
            placement = self.anthropometry.placements[node]
            lower = np.r_[lower, placement.capture_lower_m]
            upper = np.r_[upper, placement.capture_upper_m]
        return lower, upper

    def measurement_residual(self, vector: np.ndarray,
                             include_actions: frozenset[str] | None = None) -> np.ndarray:
        static = self.unpack(vector)
        prediction, _ = predict_antennas(self.samples, static, self.anthropometry)
        observed = np.einsum("ij,ntj->nti", static.R_N_from_V4, self.samples.position_v4_m)
        pelvis = SEGMENT_INDEX["Pelvis"]
        observed -= observed[:, pelvis:pelvis + 1]
        terms: list[np.ndarray] = []
        for knot in range(len(self.samples.time_ns)):
            if include_actions is not None and str(self.samples.action[knot]) not in include_actions:
                continue
            for segment_index in range(len(SEGMENTS)):
                if segment_index == pelvis or not self.samples.valid_position[knot, segment_index]:
                    continue
                covariance = (self.samples.covariance_v4_m2[knot, segment_index]
                              + self.samples.covariance_v4_m2[knot, pelvis])
                covariance = static.R_N_from_V4 @ covariance @ static.R_N_from_V4.T
                delta = prediction[knot, segment_index] - observed[knot, segment_index]
                terms.append(np.linalg.solve(np.linalg.cholesky(covariance), delta))
        return np.concatenate(terms) if terms else np.empty(0)

    def placement_prior_residual(self, vector: np.ndarray) -> np.ndarray:
        static = self.unpack(vector)
        terms = []
        for node in self.estimated_nodes:
            placement = self.anthropometry.placements[node]
            terms.append((static.capture_enclosure_to_landmark_m[node]
                          - placement.capture_prior_m) / placement.capture_sigma_m)
        return np.concatenate(terms) if terms else np.empty(0)

    def residual(self, vector: np.ndarray,
                 include_actions: frozenset[str] | None = None) -> np.ndarray:
        return np.r_[self.measurement_residual(vector, include_actions),
                     self.placement_prior_residual(vector)]

    def solve(self, initial: np.ndarray, include_actions: frozenset[str] | None = None,
              max_nfev: int = 250):
        lower, upper = self.bounds()
        seed = np.clip(np.asarray(initial, float), lower + 1e-10, upper - 1e-10)
        result = least_squares(
            lambda value: self.residual(value, include_actions),
            seed,
            bounds=(lower, upper),
            method="trf",
            loss="soft_l1",
            f_scale=2.0,
            x_scale="jac",
            max_nfev=max_nfev,
        )
        return self.unpack(result.x), result

    def numerical_jacobian(self, vector: np.ndarray, *, include_priors: bool,
                           relative_step: float) -> np.ndarray:
        function = self.residual if include_priors else self.measurement_residual
        baseline = function(vector)
        jacobian = np.empty((len(baseline), len(vector)), float)
        lower, upper = self.bounds()
        for column in range(len(vector)):
            step = relative_step * max(1.0, abs(float(vector[column])))
            plus = vector.copy()
            minus = vector.copy()
            plus[column] = min(upper[column] - 1e-12, plus[column] + step)
            minus[column] = max(lower[column] + 1e-12, minus[column] - step)
            denominator = plus[column] - minus[column]
            jacobian[:, column] = (function(plus) - function(minus)) / denominator
        return jacobian


def rank_from_singular_values(singular_values: np.ndarray, gates: dict) -> tuple[int, float]:
    singular = np.asarray(singular_values, float)
    relative = float(gates["execution_gates"]["observability_relative_singular_value_threshold"])
    threshold = float(singular[0] * relative) if len(singular) else 0.0
    return int(np.sum(singular > threshold)), threshold


def _physical_invariant(row: dict, gate: dict, prefix: str = "") -> bool:
    key = (lambda suffix: f"{prefix}{suffix}")
    return bool(
        row["maximum_segment_axis_angular_change_rad"]
        <= gate[key("maximum_segment_axis_angular_change_rad")]
        and row["maximum_joint_centre_displacement_mm"]
        <= gate[key("maximum_joint_centre_displacement_m")] * 1000.0
        and row["maximum_antenna_displacement_mm"]
        <= gate[key("maximum_antenna_displacement_m")] * 1000.0
    )


def quotient_observability(problem: CenterlineQuotientProblemV41,
                           vector: np.ndarray, gates: dict) -> dict:
    settings = gates["analysis_settings"]
    execution = gates["execution_gates"]
    jacobian = problem.numerical_jacobian(
        vector, include_priors=False,
        relative_step=float(settings["finite_difference_relative_step"]))
    _, singular, vt = np.linalg.svd(jacobian, full_matrices=True)
    rank, threshold = rank_from_singular_values(singular, gates)
    nullity = len(vector) - rank
    step = float(settings["finite_null_perturbation_norm"])
    rows = []
    offset_start = BASE_PARAMETER_COUNT
    for index, direction in enumerate(vt[rank:rank + nullity]):
        physical = physical_difference(problem, vector, vector + step * direction)
        offset_energy = float(np.sum(direction[offset_start:] ** 2))
        invariant = _physical_invariant(physical, execution)
        rows.append({
            "index": index,
            **physical,
            "sensor_offset_parameter_energy": offset_energy,
            "involves_sensor_placement": bool(offset_energy > 1e-10),
            "centerline_invariant": invariant,
            "sensor_offset_trade_failure": bool(offset_energy > 1e-10 and not invariant),
            "normalized_direction": direction.tolist(),
        })
    posterior_jacobian = problem.numerical_jacobian(
        vector, include_priors=True,
        relative_step=float(settings["finite_difference_relative_step"]))
    posterior_singular = np.linalg.svd(posterior_jacobian, compute_uv=False)
    return {
        "quotient_state": "eight per-limb axial twists removed; capture placements retained",
        "measurement_jacobian_shape": list(jacobian.shape),
        "posterior_jacobian_shape": list(posterior_jacobian.shape),
        "estimated_sensor_placement_parameters": list(problem.parameter_names[offset_start:]),
        "all_estimated_sensor_placements_in_jacobian": (
            len(problem.parameter_names[offset_start:]) == 3 * len(problem.estimated_nodes)),
        "rank": rank,
        "nullity": nullity,
        "singular_values": singular.tolist(),
        "posterior_singular_values": posterior_singular.tolist(),
        "relative_rank_threshold": execution["observability_relative_singular_value_threshold"],
        "absolute_rank_threshold": threshold,
        "null_directions": rows,
        "centerline_observable": all(row["centerline_invariant"] for row in rows),
        "sensor_offset_trade_pass": not any(row["sensor_offset_trade_failure"] for row in rows),
        "full_segment_pose_observable": False,
        "unavailable_full_pose_dofs": [f"{segment}.axial_twist" for segment in LIMB_SEGMENTS],
    }


def evaluate_gate_decisions(metrics: dict, gates: dict) -> dict:
    """Pure gate evaluator used by execution and gate-effect regression tests."""
    g = gates["execution_gates"]
    decisions = {
        "null_axis": metrics["null_axis_rad"] <= g["maximum_segment_axis_angular_change_rad"],
        "null_joint": metrics["null_joint_m"] <= g["maximum_joint_centre_displacement_m"],
        "null_antenna": metrics["null_antenna_m"] <= g["maximum_antenna_displacement_m"],
        "repeat_axis": metrics["repeat_axis_rad"] <= g["repeatability_maximum_segment_axis_angular_change_rad"],
        "repeat_joint": metrics["repeat_joint_m"] <= g["repeatability_maximum_joint_centre_displacement_m"],
        "repeat_antenna": metrics["repeat_antenna_m"] <= g["repeatability_maximum_antenna_displacement_m"],
        "optimizer_cost": metrics["optimizer_relative_cost"] <= g["optimizer_maximum_relative_cost_difference"],
        "model_median": metrics["model_median"] <= g["model_mismatch_maximum_normalized_residual_median"],
        "model_p95": metrics["model_p95"] <= g["model_mismatch_maximum_normalized_residual_p95"],
        "offset_shift": metrics["offset_shift_sigma"] <= g["sensor_offset_maximum_posterior_shift_sigma"],
        "offset_clearance": metrics["offset_bound_clearance_fraction"] >= g["sensor_offset_minimum_bound_clearance_fraction"],
        "offset_profile_axis": metrics["offset_profile_axis_rad"] <= g["sensor_offset_profile_maximum_segment_axis_angular_change_rad"],
        "offset_profile_joint": metrics["offset_profile_joint_m"] <= g["sensor_offset_profile_maximum_joint_centre_displacement_m"],
        "offset_profile_antenna": metrics["offset_profile_antenna_m"] <= g["sensor_offset_profile_maximum_antenna_displacement_m"],
    }
    decisions["pass"] = all(decisions.values())
    return decisions


def sensor_offset_posterior(problem: CenterlineQuotientProblemV41,
                            vector: np.ndarray, gates: dict) -> dict:
    static = problem.unpack(vector)
    rows = []
    for node in problem.estimated_nodes:
        placement = problem.anthropometry.placements[node]
        value = static.capture_enclosure_to_landmark_m[node]
        width = placement.capture_upper_m - placement.capture_lower_m
        clearance = np.minimum(value - placement.capture_lower_m,
                               placement.capture_upper_m - value) / width
        shift = np.abs(value - placement.capture_prior_m) / placement.capture_sigma_m
        rows.append({
            "node": node,
            "posterior_m": value.tolist(),
            "prior_m": placement.capture_prior_m.tolist(),
            "prior_sigma_m": placement.capture_sigma_m.tolist(),
            "lower_bound_m": placement.capture_lower_m.tolist(),
            "upper_bound_m": placement.capture_upper_m.tolist(),
            "maximum_posterior_shift_sigma": float(np.max(shift)),
            "minimum_bound_clearance_fraction": float(np.min(clearance)),
            "source": placement.capture_source,
            "provenance_status": placement.capture_status,
        })
    execution = gates["execution_gates"]
    passed = all(
        row["maximum_posterior_shift_sigma"]
        <= execution["sensor_offset_maximum_posterior_shift_sigma"]
        and row["minimum_bound_clearance_fraction"]
        >= execution["sensor_offset_minimum_bound_clearance_fraction"]
        for row in rows)
    return {"pass": passed, "rows": rows, "all_estimated_offsets_reported": len(rows) == len(problem.estimated_nodes)}


def sensor_offset_profile(problem: CenterlineQuotientProblemV41,
                          optimum: np.ndarray, gates: dict, *, max_nfev: int = 80) -> dict:
    """Profile each fitted placement scalar at +/- one prior sigma.

    Every refit uses the identical residual and weighting.  One placement
    coordinate is held at its profile value while all other coordinates are
    optimized inside the original bounds.
    """
    lower, upper = problem.bounds()
    rows = []
    start = BASE_PARAMETER_COUNT
    for node_index, node in enumerate(problem.estimated_nodes):
        placement = problem.anthropometry.placements[node]
        for axis_index, axis in enumerate("xyz"):
            column = start + 3 * node_index + axis_index
            free = np.ones(len(optimum), dtype=bool)
            free[column] = False
            for sign in (-1.0, 1.0):
                fixed = float(np.clip(
                    placement.capture_prior_m[axis_index] + sign * placement.capture_sigma_m[axis_index],
                    lower[column] + 1e-10, upper[column] - 1e-10))
                seed = optimum.copy()
                seed[column] = fixed

                def residual(reduced):
                    full = seed.copy()
                    full[free] = reduced
                    return problem.residual(full)

                result = least_squares(
                    residual,
                    seed[free],
                    bounds=(lower[free], upper[free]),
                    method="trf",
                    loss="soft_l1",
                    f_scale=2.0,
                    x_scale="jac",
                    max_nfev=max_nfev,
                )
                profiled = seed.copy()
                profiled[free] = result.x
                physical = physical_difference(problem, optimum, profiled)
                invariant = _physical_invariant(
                    physical, gates["execution_gates"], prefix="sensor_offset_profile_")
                rows.append({
                    "node": node,
                    "axis": axis,
                    "profile_sign_sigma": int(sign),
                    "fixed_value_m": fixed,
                    "success": bool(result.success),
                    "cost": float(result.cost),
                    **physical,
                    "physical_pass": invariant,
                })
    return {
        "pass": all(row["success"] and row["physical_pass"] for row in rows),
        "rows": rows,
        "profiled_parameter_count": 3 * len(problem.estimated_nodes),
        "expected_rows": 6 * len(problem.estimated_nodes),
        "identical_residual_and_weighting": True,
    }
