"""Numerical primitives for IMU_ONLY_MULTI_ACTION_CENTERLINE_CALIBRATION_V1.

Rotation names follow R_DST_from_SRC.  All rotations are active.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping

import numpy as np
from scipy.optimize import least_squares
from scipy.signal import butter, sosfiltfilt
from scipy.spatial.transform import Rotation, Slerp


SEGMENTS = (
    "pelvis", "torso", "upper_arm_L", "upper_arm_R", "forearm_L",
    "forearm_R", "thigh_L", "thigh_R", "shank_L", "shank_R",
)
JOINTS = ("elbow_L", "elbow_R", "knee_L", "knee_R")


@dataclass(frozen=True)
class NodeSeries:
    """One node's native-rate IMU/Q2 stream.

    R_N_i_from_B_i is active board-to-node-navigation orientation.
    """

    time_ns: np.ndarray
    accel_B_mps2: np.ndarray
    gyro_B_rad_s: np.ndarray
    R_N_i_from_B_i: np.ndarray
    orientation_sigma_rad: np.ndarray
    gyro_bias_sigma_rad_s: np.ndarray


@dataclass(frozen=True)
class CalibrationDataset:
    nodes: Mapping[str, NodeSeries]
    action_windows: Mapping[str, tuple[int, int]]
    node_to_segment: Mapping[str, str]
    source_hashes: Mapping[str, str]


def canonical_json_bytes(value: Any) -> bytes:
    """Canonical finite JSON used for the pre-replay freeze."""
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize(vector: np.ndarray, fallback: np.ndarray | None = None) -> np.ndarray:
    value = np.asarray(vector, float)
    norm = float(np.linalg.norm(value))
    if np.isfinite(norm) and norm > 1e-12:
        return value / norm
    if fallback is None:
        raise ValueError("cannot normalize zero/non-finite vector")
    return normalize(fallback)


def tangent_basis(axis: np.ndarray) -> np.ndarray:
    """Deterministic 3x2 orthonormal tangent basis at a unit vector."""
    axis = normalize(axis)
    reference = np.array([1.0, 0.0, 0.0])
    if abs(float(axis @ reference)) > 0.8:
        reference = np.array([0.0, 1.0, 0.0])
    first = normalize(np.cross(axis, reference))
    second = normalize(np.cross(axis, first))
    return np.column_stack((first, second))


def tangent_update(base: np.ndarray, coordinates: np.ndarray) -> np.ndarray:
    base = normalize(base)
    rotvec = tangent_basis(base) @ np.asarray(coordinates, float)
    return normalize(Rotation.from_rotvec(rotvec).apply(base))


def axis_angle_rad(left: np.ndarray, right: np.ndarray, *, signed_axis: bool = True) -> float:
    dot = float(np.clip(normalize(left) @ normalize(right), -1.0, 1.0))
    if not signed_axis:
        dot = abs(dot)
    return math.acos(dot)


def interpolate_rotations_so3(
    source_time_ns: np.ndarray,
    R_DST_from_SRC: np.ndarray,
    target_time_ns: np.ndarray,
) -> np.ndarray:
    """Timestamp-based SO(3) interpolation; never nearest-neighbour."""
    source = np.asarray(source_time_ns, np.int64)
    target = np.asarray(target_time_ns, np.int64)
    if len(source) < 2 or np.any(np.diff(source) <= 0):
        raise ValueError("SO(3) interpolation requires increasing source timestamps")
    if len(target) and (target[0] < source[0] or target[-1] > source[-1]):
        raise ValueError("SO(3) interpolation target outside source support")
    origin = int(source[0])
    s = (source - origin).astype(float) / 1e9
    t = (target - origin).astype(float) / 1e9
    return Slerp(s, Rotation.from_matrix(np.asarray(R_DST_from_SRC, float)))(t).as_matrix()


def interpolate_vectors(source_time_ns: np.ndarray, values: np.ndarray,
                        target_time_ns: np.ndarray) -> np.ndarray:
    source = np.asarray(source_time_ns, np.int64).astype(float)
    target = np.asarray(target_time_ns, np.int64).astype(float)
    values = np.asarray(values, float)
    return np.column_stack([
        np.interp(target, source, values[:, column]) for column in range(values.shape[1])
    ])


def anti_alias_vectors(values: np.ndarray, sample_rate_hz: float,
                       cutoff_hz: float, order: int) -> np.ndarray:
    values = np.asarray(values, float)
    if len(values) < max(15, 3 * (order + 1)):
        raise ValueError("insufficient samples for zero-phase anti-alias filter")
    if not 0.0 < cutoff_hz < 0.5 * sample_rate_hz:
        raise ValueError("anti-alias cutoff outside Nyquist interval")
    sos = butter(order, cutoff_hz, btype="low", fs=sample_rate_hz, output="sos")
    return sosfiltfilt(sos, values, axis=0)


def robust_axis_sigma(values: np.ndarray, floor: float) -> float:
    values = np.asarray(values, float)
    centre = np.median(values, axis=0)
    sigma_axis = 1.4826 * np.median(np.abs(values - centre), axis=0)
    return max(float(floor), float(np.max(sigma_axis)))


def estimate_initial_noise(dataset: CalibrationDataset, gates: Mapping[str, Any]) -> dict:
    start, stop = dataset.action_windows["initial_still_attempt2"]
    gyro_rows = []
    accel_rows = []
    per_node = {}
    for node, stream in sorted(dataset.nodes.items()):
        mask = (stream.time_ns >= start) & (stream.time_ns <= stop)
        gyro = stream.gyro_B_rad_s[mask]
        accel = stream.accel_B_mps2[mask]
        if len(gyro) < 125:
            raise ValueError(f"initial-still noise samples insufficient for {node}")
        gyro_rows.append(gyro - np.median(gyro, axis=0))
        accel_rows.append(accel - np.median(accel, axis=0))
        per_node[node] = {
            "samples": int(len(gyro)),
            "gyro_sigma_rad_s": robust_axis_sigma(gyro, 0.0),
            "accel_sigma_mps2": robust_axis_sigma(accel, 0.0),
        }
    floors = gates["noise_floors"]
    gyro_sigma = robust_axis_sigma(np.concatenate(gyro_rows), float(floors["gyro_sigma_rad_s"]))
    accel_sigma = robust_axis_sigma(np.concatenate(accel_rows), float(floors["accel_sigma_mps2"]))
    return {
        "method": "INITIAL_STILL_MEDIAN_MAD_MAX_AXIS_WITH_NONZERO_FLOOR",
        "gyro_sigma_rad_s": gyro_sigma,
        "accel_sigma_mps2": accel_sigma,
        "gyro_floor_rad_s": float(floors["gyro_sigma_rad_s"]),
        "accel_floor_mps2": float(floors["accel_sigma_mps2"]),
        "per_node": per_node,
    }


def olsson_weighted_residual(
    h_parent_B: np.ndarray,
    h_child_B: np.ndarray,
    omega_parent_B_rad_s: np.ndarray,
    omega_child_B_rad_s: np.ndarray,
    accel_parent_B_mps2: np.ndarray,
    accel_child_B_mps2: np.ndarray,
    gyro_sigma_rad_s: float,
    accel_sigma_mps2: float,
) -> np.ndarray:
    """Olsson 2019 equations (20),(21) with equations (22x) variance weights.

    Rows alternate whitened angular-rate and acceleration residuals.
    """
    hp = normalize(h_parent_B)
    hc = normalize(h_child_B)
    wp = np.asarray(omega_parent_B_rad_s, float)
    wc = np.asarray(omega_child_B_rad_s, float)
    ap = np.asarray(accel_parent_B_mps2, float)
    ac = np.asarray(accel_child_B_mps2, float)
    angular = (np.linalg.norm(np.cross(wp, hp), axis=1)
               - np.linalg.norm(np.cross(wc, hc), axis=1))
    acceleration = ap @ hp - ac @ hc
    w_accel = 1.0 / np.sqrt(1.0 + (np.linalg.norm(ap, axis=1)
                                  - np.linalg.norm(ac, axis=1)) ** 2)
    residual = np.empty(2 * len(wp), float)
    residual[0::2] = angular / (math.sqrt(2.0) * float(gyro_sigma_rad_s))
    residual[1::2] = w_accel * acceleration / (math.sqrt(2.0) * float(accel_sigma_mps2))
    return residual


def _functional_common_samples(parent: NodeSeries, child: NodeSeries,
                               window: tuple[int, int]) -> tuple[np.ndarray, ...]:
    start = max(int(window[0]), int(parent.time_ns[0]), int(child.time_ns[0]))
    stop = min(int(window[1]), int(parent.time_ns[-1]), int(child.time_ns[-1]))
    pdt = float(np.median(np.diff(parent.time_ns)))
    cdt = float(np.median(np.diff(child.time_ns)))
    step = int(round(max(pdt, cdt)))
    times = np.arange(start, stop + 1, step, dtype=np.int64)
    return (
        times,
        interpolate_vectors(parent.time_ns, parent.gyro_B_rad_s, times),
        interpolate_vectors(child.time_ns, child.gyro_B_rad_s, times),
        interpolate_vectors(parent.time_ns, parent.accel_B_mps2, times),
        interpolate_vectors(child.time_ns, child.accel_B_mps2, times),
        interpolate_rotations_so3(parent.time_ns, parent.R_N_i_from_B_i, times),
        interpolate_rotations_so3(child.time_ns, child.R_N_i_from_B_i, times),
    )


def deterministic_information_subset(score: np.ndarray, minimum: int, maximum: int) -> np.ndarray:
    score = np.asarray(score, float)
    finite = np.flatnonzero(np.isfinite(score))
    if len(finite) < minimum:
        raise ValueError(f"only {len(finite)} finite informative candidates; require {minimum}")
    threshold = float(np.percentile(score[finite], 35.0))
    informative = finite[score[finite] > max(threshold, 1e-12)]
    if len(informative) < minimum:
        informative = finite[np.argsort(-score[finite], kind="stable")[:minimum]]
    order = informative[np.argsort(-score[informative], kind="stable")]
    selected = np.sort(order[:maximum])
    if len(selected) < minimum:
        raise ValueError(f"only {len(selected)} informative samples; require {minimum}")
    return selected


def _pca_functional_initializer(omega_parent: np.ndarray, omega_child: np.ndarray,
                                R_Np_from_Bp: np.ndarray,
                                R_Nc_from_Bc: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict]:
    # Q2 node headings are arbitrary.  This diagnostic initializer uses their
    # current deterministic frames; the final Olsson residual is frame-local.
    rel_N = (np.einsum("nij,nj->ni", R_Nc_from_Bc, omega_child)
             - np.einsum("nij,nj->ni", R_Np_from_Bp, omega_parent))
    parent_samples = np.einsum("nji,nj->ni", R_Np_from_Bp, rel_N)
    child_samples = np.einsum("nji,nj->ni", R_Nc_from_Bc, rel_N)
    def principal(rows: np.ndarray) -> tuple[np.ndarray, float]:
        values, vectors = np.linalg.eigh(rows.T @ rows / max(1, len(rows)))
        ratio = float(values[-1] / max(values[-2], 1e-12))
        return normalize(vectors[:, -1]), ratio
    hp, rp = principal(parent_samples)
    hc, rc = principal(child_samples)
    if np.mean(np.einsum("nij,j->ni", R_Np_from_Bp, hp), axis=0) @ np.mean(
            np.einsum("nij,j->ni", R_Nc_from_Bc, hc), axis=0) < 0:
        hc = -hc
    return hp, hc, {"parent_eigen_ratio": rp, "child_eigen_ratio": rc}


def fit_functional_axis(
    parent: NodeSeries,
    child: NodeSeries,
    window: tuple[int, int],
    noise: Mapping[str, float],
    sampling: Mapping[str, Any],
) -> tuple[np.ndarray, np.ndarray, dict]:
    times, wp, wc, ap, ac, Rp, Rc = _functional_common_samples(parent, child, window)
    hp0, hc0, pca = _pca_functional_initializer(wp, wc, Rp, Rc)
    score = (np.linalg.norm(wp, axis=1) + np.linalg.norm(wc, axis=1)
             + 0.1 * np.abs(np.linalg.norm(ap, axis=1) - np.linalg.norm(ac, axis=1)))
    selected = deterministic_information_subset(
        score,
        int(sampling["minimum_mandatory_informative_samples"]),
        int(sampling["max_samples_per_action_factor"]),
    )
    wp = wp[selected]; wc = wc[selected]; ap = ap[selected]; ac = ac[selected]
    def residual(value: np.ndarray) -> np.ndarray:
        return olsson_weighted_residual(
            tangent_update(hp0, value[:2]), tangent_update(hc0, value[2:]),
            wp, wc, ap, ac, float(noise["gyro_sigma_rad_s"]),
            float(noise["accel_sigma_mps2"]),
        )
    starts = [np.zeros(4)]
    for degrees in (15.0, -15.0, 30.0, -30.0):
        radians = math.radians(degrees)
        starts.append(np.array([radians, -0.5*radians, -radians, 0.5*radians]))
    fits = []
    for start in starts:
        fit = least_squares(residual, start, method="trf", loss="huber", f_scale=1.5,
                            x_scale=1.0, ftol=1e-11, xtol=1e-11, gtol=1e-11,
                            max_nfev=300)
        fits.append(fit)
    best = min(fits, key=lambda item: (float(item.cost), tuple(item.x)))
    hp = tangent_update(hp0, best.x[:2]); hc = tangent_update(hc0, best.x[2:])
    jac = np.asarray(best.jac, float)
    singular = np.linalg.svd(jac, compute_uv=False)
    threshold = float(singular[0] * 1e-6) if len(singular) else 0.0
    rank = int(np.sum(singular > threshold))
    covariance = np.linalg.pinv(jac.T @ jac) if jac.size else np.full((4, 4), np.nan)
    report = {
        "method": "OLSSON_2019_WEIGHTED_GYROSCOPE_ACCELERATION",
        "pca_initializer_only": pca,
        "candidate_samples": int(len(times)),
        "selected_samples": int(len(selected)),
        "selected_first_ns": int(times[selected[0]]),
        "selected_last_ns": int(times[selected[-1]]),
        "objective_cost": float(best.cost),
        "jacobian_rank": rank,
        "jacobian_parameter_count": 4,
        "relative_singular_value_threshold": 1e-6,
        "singular_values": singular.tolist(),
        "covariance_local_tangent_approximation": covariance.tolist(),
        "multistart_costs": [float(item.cost) for item in fits],
        "observable": rank == 4,
    }
    return hp, hc, report
