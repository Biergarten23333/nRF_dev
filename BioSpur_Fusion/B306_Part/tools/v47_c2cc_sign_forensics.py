#!/usr/bin/env python3
"""Convention-explicit primitives for the C2CC sign forensic replay.

The functions in this module are deliberately small and analytic.  They do
not know about action labels or held-out ranges, and they never canonicalize a
measured displacement sign.  Column vectors and active rotations are used
throughout.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from v47_q1_eskf import G_MPS2, quaternion_to_matrix


@dataclass(frozen=True)
class SignForensicConfig:
    schema: str = "biospur-c2cc-sign-forensic-config-v1"
    time_offset_bound_s: float = 0.080
    time_offset_step_s: float = 0.005
    endpoint_uncertainty_floor_m: float = 0.075
    endpoint_continuity_sigma_multiplier: float = 2.0
    paired_closure_fraction_limit: float = 0.35
    axis_unsigned_p95_limit_deg: float = 75.0
    cross_mount_up_limit_deg: float = 10.0
    minimum_imu_displacement_m: float = 0.10
    lever_radius_m: float = 0.050


FROZEN_SIGN_FORENSIC_CONFIG = SignForensicConfig()


def unit(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=float)
    norm = float(np.linalg.norm(value))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError("zero or non-finite vector")
    return value / norm


def angle_deg(left: np.ndarray, right: np.ndarray) -> float:
    return math.degrees(math.acos(float(np.clip(unit(left) @ unit(right), -1.0, 1.0))))


def rotation_angle_deg(left: np.ndarray, right: np.ndarray) -> float:
    relative = np.asarray(left, float) @ np.asarray(right, float).T
    return math.degrees(math.acos(float(np.clip((np.trace(relative)-1.0)/2.0, -1.0, 1.0))))


def chronological_displacements(centers: np.ndarray) -> np.ndarray:
    """Return p[i+1]-p[i] without any geometric reordering or sign choice."""
    points = np.asarray(centers, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 2:
        raise ValueError("at least two 3D chronological centers are required")
    return np.diff(points, axis=0)


def specific_force_to_navigation_acceleration(
    rotation_N_S: np.ndarray,
    specific_force_S_mps2: np.ndarray,
    gravity_N_mps2: np.ndarray = np.array([0.0, 0.0, -G_MPS2]),
) -> np.ndarray:
    """Active column-vector map: a_N = R_N_S f_S + g_N."""
    rotation = np.asarray(rotation_N_S, dtype=float)
    force = np.asarray(specific_force_S_mps2, dtype=float)
    return np.einsum("...ij,...j->...i", rotation, force) + np.asarray(gravity_N_mps2, float)


def preintegrate_endpoint_zupt(t_s: np.ndarray, acceleration_N_mps2: np.ndarray) -> dict:
    """Trapezoidal preintegration and the analytic linear endpoint-ZUPT drift."""
    t = np.asarray(t_s, dtype=float); acceleration = np.asarray(acceleration_N_mps2, dtype=float)
    if t.ndim != 1 or acceleration.shape != (len(t), 3) or len(t) < 3 or np.any(np.diff(t) <= 0):
        raise ValueError("invalid preintegration series")
    velocity = np.zeros_like(acceleration); displacement = np.zeros_like(acceleration)
    for index, dt in enumerate(np.diff(t), 1):
        velocity[index] = velocity[index-1] + 0.5*(acceleration[index-1]+acceleration[index])*dt
        displacement[index] = displacement[index-1] + 0.5*(velocity[index-1]+velocity[index])*dt
    duration = float(t[-1]-t[0]); correction = ((t-t[0])/duration)[:, None]*velocity[-1]
    corrected_velocity = velocity-correction; corrected = np.zeros_like(acceleration)
    for index, dt in enumerate(np.diff(t), 1):
        corrected[index] = corrected[index-1] + 0.5*(corrected_velocity[index-1]+corrected_velocity[index])*dt
    return {
        "raw_delta_v_mps": velocity[-1],
        "raw_displacement_m": displacement[-1],
        "zupt_displacement_m": corrected[-1],
        "corrected_end_velocity_mps": corrected_velocity[-1],
        "duration_s": duration,
    }


def wahba_diagnostic(source: np.ndarray, target: np.ndarray) -> dict:
    """Fit source->target and expose both unconstrained and proper solutions."""
    source = np.asarray(source, float); target = np.asarray(target, float)
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 3 or len(source) < 3:
        raise ValueError("Wahba inputs must be matching Nx3 arrays")
    source = np.asarray([unit(row) for row in source]); target = np.asarray([unit(row) for row in target])
    u, singular, vt = np.linalg.svd(target.T @ source)
    orthogonal = u @ vt
    correction = np.eye(3); correction[-1, -1] = np.sign(np.linalg.det(orthogonal))
    proper = u @ correction @ vt
    def metrics(matrix):
        errors = np.asarray([angle_deg(matrix @ s, t) for s, t in zip(source, target)])
        return {
            "determinant": float(np.linalg.det(matrix)),
            "median_deg": float(np.median(errors)),
            "p95_deg": float(np.quantile(errors, 0.95)),
            "errors_deg": errors,
        }
    return {"singular_values": singular, "orthogonal": orthogonal,
            "orthogonal_metrics": metrics(orthogonal), "proper": proper,
            "proper_metrics": metrics(proper)}


def quaternion_active_rotate(q_scalar_first: np.ndarray, vector: np.ndarray) -> np.ndarray:
    """Hamilton scalar-first q v q* active rotation, implemented as R(q)@v."""
    return quaternion_to_matrix(np.asarray(q_scalar_first, float)) @ np.asarray(vector, float)


def estimate_constant_offset(t_reference: np.ndarray, reference: np.ndarray,
                             t_shifted: np.ndarray, shifted: np.ndarray,
                             bound_s: float, step_s: float) -> dict:
    """Deterministic bounded linear-interpolation offset diagnostic.

    The returned offset is added to ``t_shifted``.  It is diagnostic only and
    does not authorize changing the frozen zero-offset production policy.
    """
    tr = np.asarray(t_reference, float); reference = np.asarray(reference, float)
    ts = np.asarray(t_shifted, float); shifted = np.asarray(shifted, float)
    offsets = np.arange(-bound_s, bound_s+step_s/2, step_s); scores=[]
    for offset in offsets:
        shifted_time = ts+offset; lo=max(tr[0], shifted_time[0]); hi=min(tr[-1], shifted_time[-1])
        use=(tr>=lo)&(tr<=hi)
        predicted=np.interp(tr[use], shifted_time, shifted)
        scores.append(float(np.mean((reference[use]-predicted)**2)))
    index=int(np.argmin(scores))
    return {"offset_added_to_shifted_s": float(offsets[index]), "score": scores[index],
            "grid_offsets_s": offsets, "grid_scores": np.asarray(scores)}


def lever_direction_bound_deg(displacement_m: np.ndarray, rotation_start: np.ndarray,
                              rotation_end: np.ndarray, radius_m: float) -> float:
    gain=float(np.linalg.svd(np.asarray(rotation_end)-np.asarray(rotation_start), compute_uv=False)[0])
    return math.degrees(math.atan2(radius_m*gain, float(np.linalg.norm(displacement_m))))
