"""Axial-twist quotient calibration for a measurement-conditioned centerline."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from biospur_fusion.calibration.anthropometry import Anthropometry
from biospur_fusion.calibration.articulated_batch import CalibrationSamples, SEGMENTS, SEGMENT_INDEX


LIMB_SEGMENTS = (
    "UpperArm_L", "Forearm_L", "UpperArm_R", "Forearm_R",
    "Thigh_L", "Shank_L", "Thigh_R", "Shank_R",
)
SEGMENT_OFFSET_KEYS = {
    "Pelvis": "BSFC2CC_PELVIS", "Torso": "BSF31CC_C7",
    "UpperArm_L": "BSFAA61_ELBOW_L", "Forearm_L": "BSFB165_WRIST_L",
    "UpperArm_R": "BSF1120_ELBOW_R", "Forearm_R": "BSFEC35_WRIST_R",
    "Thigh_L": "BSF44AD_KNEE_L", "Shank_L": "BSF6C53_ANKLE_L",
    "Thigh_R": "BSF3C79_KNEE_R", "Shank_R": "BSF8BC4_ANKLE_R",
}


def axis_from_angles(values: np.ndarray) -> np.ndarray:
    azimuth, elevation = np.asarray(values, float)
    cosine = np.cos(elevation)
    return np.array([cosine * np.cos(azimuth), cosine * np.sin(azimuth), np.sin(elevation)])


def angles_from_axis(axis: np.ndarray) -> np.ndarray:
    axis = np.asarray(axis, float); axis /= np.linalg.norm(axis)
    return np.array([np.arctan2(axis[1], axis[0]), np.arcsin(np.clip(axis[2], -1., 1.))])


def _r_nv(two_axis: np.ndarray) -> np.ndarray:
    return Rotation.from_euler("yx", [float(two_axis[1]), float(two_axis[0])]).as_matrix()


@dataclass(frozen=True)
class QuotientStatic:
    R_N_from_V4: np.ndarray
    R_pelvis_from_sensor: np.ndarray
    R_torso_from_sensor: np.ndarray
    limb_axis_sensor: Mapping[str, np.ndarray]


def unpack(vector: np.ndarray) -> QuotientStatic:
    vector = np.asarray(vector, float)
    expected = 2 + 3 + 3 + 2 * len(LIMB_SEGMENTS)
    if vector.shape != (expected,):
        raise ValueError("centerline quotient parameter shape mismatch")
    axes = {}; offset = 8
    for index, segment in enumerate(LIMB_SEGMENTS):
        axes[segment] = axis_from_angles(vector[offset + 2*index:offset + 2*index + 2])
    return QuotientStatic(
        _r_nv(vector[:2]), Rotation.from_rotvec(vector[2:5]).as_matrix(),
        Rotation.from_rotvec(vector[5:8]).as_matrix(), axes,
    )


def pack(value: QuotientStatic) -> np.ndarray:
    euler = Rotation.from_matrix(value.R_N_from_V4).as_euler("yxz")
    rows = [np.array([euler[1], euler[0]]),
            Rotation.from_matrix(value.R_pelvis_from_sensor).as_rotvec(),
            Rotation.from_matrix(value.R_torso_from_sensor).as_rotvec()]
    rows.extend(angles_from_axis(value.limb_axis_sensor[segment]) for segment in LIMB_SEGMENTS)
    return np.concatenate(rows)


def predict_antennas(samples: CalibrationSamples, static: QuotientStatic,
                     anthropometry: Anthropometry) -> tuple[np.ndarray, np.ndarray]:
    """Return antenna positions and segment +Z axes relative to pelvis antenna."""
    count = len(samples.time_ns); p = SEGMENT_INDEX; q = samples.orientation_N_from_B
    rotations = np.empty((count, 2, 3, 3))
    rotations[:, 0] = q[:, p["Pelvis"]] @ static.R_pelvis_from_sensor.T
    rotations[:, 1] = q[:, p["Torso"]] @ static.R_torso_from_sensor.T
    axes = np.empty((count, len(SEGMENTS), 3))
    axes[:, p["Pelvis"]] = rotations[:, 0, :, 2]
    axes[:, p["Torso"]] = rotations[:, 1, :, 2]
    for segment in LIMB_SEGMENTS:
        axes[:, p[segment]] = np.einsum("nij,j->ni", q[:, p[segment]],
                                         static.limb_axis_sensor[segment])
    v = anthropometry.scalars_m; offsets = anthropometry.offsets_segment_m
    out = np.zeros((count, len(SEGMENTS), 3))
    pelvis_landmark = np.einsum("nij,j->ni", q[:, p["Pelvis"]], offsets["BSFC2CC_PELVIS"])
    c7 = pelvis_landmark + axes[:, p["Torso"]] * v["c7_to_pelvis_m"]
    out[:, p["Torso"]] = c7 - np.einsum("nij,j->ni", q[:, p["Torso"]], offsets["BSF31CC_C7"])
    for side, sign in (("L", -1.0), ("R", 1.0)):
        upper = f"UpperArm_{side}"; fore = f"Forearm_{side}"
        thigh = f"Thigh_{side}"; shank = f"Shank_{side}"
        shoulder = c7 + np.einsum("nij,j->ni", rotations[:, 1],
                                   [sign * v["biacromial_width_m"] / 2., 0., 0.])
        elbow = shoulder - axes[:, p[upper]] * v[f"upper_arm_{side}_m"]
        wrist = elbow - axes[:, p[fore]] * v[f"forearm_{side}_m"]
        out[:, p[upper]] = elbow - np.einsum("nij,j->ni", q[:, p[upper]], offsets[SEGMENT_OFFSET_KEYS[upper]])
        out[:, p[fore]] = wrist - np.einsum("nij,j->ni", q[:, p[fore]], offsets[SEGMENT_OFFSET_KEYS[fore]])
        hip = pelvis_landmark + np.einsum("nij,j->ni", rotations[:, 0],
                                          [sign * v["hip_width_m"] / 2., 0., v["hip_vertical_offset_m"]])
        knee = hip - axes[:, p[thigh]] * v[f"thigh_{side}_m"]
        ankle = knee - axes[:, p[shank]] * v[f"shank_{side}_m"]
        out[:, p[thigh]] = knee - np.einsum("nij,j->ni", q[:, p[thigh]], offsets[SEGMENT_OFFSET_KEYS[thigh]])
        out[:, p[shank]] = ankle - np.einsum("nij,j->ni", q[:, p[shank]], offsets[SEGMENT_OFFSET_KEYS[shank]])
    return out, axes


class CenterlineQuotientProblem:
    """Only centerline-relevant DOFs; eight axial twists are absent by design."""
    def __init__(self, samples: CalibrationSamples, anthropometry: Anthropometry):
        self.samples = samples; self.anthropometry = anthropometry
        self.parameter_names = (
            "R_N_from_V4.roll", "R_N_from_V4.pitch",
            *(f"R_Pelvis_from_sensor.{axis}" for axis in "xyz"),
            *(f"R_Torso_from_sensor.{axis}" for axis in "xyz"),
            *(f"axis_{segment}.{angle}" for segment in LIMB_SEGMENTS for angle in ("azimuth", "elevation")),
        )

    def residual(self, vector: np.ndarray, include_actions: frozenset[str] | None = None) -> np.ndarray:
        static = unpack(vector); prediction, _ = predict_antennas(self.samples, static, self.anthropometry)
        observed = np.einsum("ij,ntj->nti", static.R_N_from_V4, self.samples.position_v4_m)
        pelvis = SEGMENT_INDEX["Pelvis"]; observed -= observed[:, pelvis:pelvis+1]
        terms = []
        pelvis_offset_sigma = self.anthropometry.offset_sigma_m["BSFC2CC_PELVIS"]
        for knot in range(len(self.samples.time_ns)):
            if include_actions is not None and str(self.samples.action[knot]) not in include_actions:
                continue
            for segment_index, segment in enumerate(SEGMENTS):
                if segment_index == pelvis or not self.samples.valid_position[knot, segment_index]:
                    continue
                covariance = (self.samples.covariance_v4_m2[knot, segment_index]
                              + self.samples.covariance_v4_m2[knot, pelvis])
                offset_sigma = self.anthropometry.offset_sigma_m[SEGMENT_OFFSET_KEYS[segment]]
                covariance = static.R_N_from_V4 @ covariance @ static.R_N_from_V4.T
                covariance += np.eye(3) * (offset_sigma**2 + pelvis_offset_sigma**2)
                terms.append(np.linalg.solve(np.linalg.cholesky(covariance),
                                             prediction[knot, segment_index] - observed[knot, segment_index]))
        return np.concatenate(terms) if terms else np.empty(0)

    def solve(self, initial: np.ndarray, include_actions: frozenset[str] | None = None,
              max_nfev: int = 250):
        result = least_squares(lambda x: self.residual(x, include_actions), np.asarray(initial, float),
                               method="trf", loss="soft_l1", f_scale=2., x_scale="jac",
                               max_nfev=max_nfev)
        return unpack(result.x), result

    def numerical_jacobian(self, vector: np.ndarray, step: float = 2e-6) -> np.ndarray:
        base = self.residual(vector); jacobian = np.empty((len(base), len(vector)))
        for column in range(len(vector)):
            delta = step * max(1., abs(float(vector[column])))
            plus = vector.copy(); minus = vector.copy(); plus[column] += delta; minus[column] -= delta
            jacobian[:, column] = (self.residual(plus) - self.residual(minus)) / (2*delta)
        return jacobian


def quotient_observability(problem: CenterlineQuotientProblem, vector: np.ndarray, gates: dict) -> dict:
    jacobian = problem.numerical_jacobian(vector)
    _, singular, vt = np.linalg.svd(jacobian, full_matrices=False)
    threshold = float(singular[0] * 1e-6) if len(singular) else 0.
    rank = int(np.sum(singular > threshold)); step = float(gates["null_perturbation_norm"])
    base_static = unpack(vector); base_prediction, base_axes = predict_antennas(
        problem.samples, base_static, problem.anthropometry)
    rows = []
    for index, direction in enumerate(vt[rank:]):
        moved_static = unpack(vector + step * direction)
        moved_prediction, moved_axes = predict_antennas(problem.samples, moved_static, problem.anthropometry)
        dots = np.clip(np.sum(base_axes * moved_axes, axis=2), -1., 1.)
        angle = float(np.max(np.arccos(dots)))
        displacement = float(np.max(np.linalg.norm(moved_prediction - base_prediction, axis=2)))
        rows.append({
            "index": index, "maximum_segment_axis_angular_change_rad": angle,
            "maximum_segment_axis_angular_change_deg": float(np.degrees(angle)),
            "maximum_joint_centre_displacement_mm": displacement * 1000.,
            "maximum_antenna_displacement_mm": displacement * 1000.,
            "centerline_invariant": bool(
                angle <= gates["maximum_segment_axis_angular_change_rad"]
                and displacement <= gates["maximum_joint_centre_displacement_m"]
                and displacement <= gates["maximum_antenna_displacement_m"]),
            "normalized_direction": direction.tolist(),
        })
    return {
        "quotient_state": "eight per-limb axial twists removed",
        "jacobian_shape": list(jacobian.shape), "rank": rank, "nullity": len(vector)-rank,
        "singular_values": singular.tolist(), "absolute_rank_threshold": threshold,
        "physical_invariance_gates": gates, "null_directions": rows,
        "centerline_observable": all(row["centerline_invariant"] for row in rows),
        "full_segment_pose_observable": False,
        "unavailable_full_pose_dofs": [f"{segment}.axial_twist" for segment in LIMB_SEGMENTS],
    }
