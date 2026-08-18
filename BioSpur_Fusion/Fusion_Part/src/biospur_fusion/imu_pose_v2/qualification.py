from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from . import so3
from .joints import JOINTS


def _angle_deg(vectors: np.ndarray, reference: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, float)
    vectors = vectors / np.linalg.norm(vectors, axis=-1, keepdims=True)
    reference = np.asarray(reference, float) / np.linalg.norm(reference)
    return np.rad2deg(np.arccos(np.clip(vectors @ reference, -1.0, 1.0)))


def directional_gate(vectors: np.ndarray, reference: np.ndarray,
                     median_max_deg: float, p95_max_deg: float) -> dict:
    errors = _angle_deg(vectors, reference)
    median = float(np.median(errors)); p95 = float(np.percentile(errors, 95))
    return {"median_deg": median, "p95_deg": p95,
            "pass": median <= median_max_deg and p95 <= p95_max_deg}


def orientation_speed_rad_s(quaternions: np.ndarray, time_ns: np.ndarray) -> np.ndarray:
    q = np.asarray(quaternions, float); t = np.asarray(time_ns, np.int64)
    if len(q) != len(t) or len(q) < 2 or np.any(np.diff(t) <= 0):
        raise ValueError("ordered orientation timeline required")
    delta = np.stack([so3.log(so3.between(q[index], q[index+1])) for index in range(len(q)-1)])
    return np.linalg.norm(delta, axis=1) / (np.diff(t) * 1e-9)


def static_wobble_gate(raw_gyro_norm: np.ndarray, b0_speed: np.ndarray,
                       production_speed: np.ndarray, predictive_p99: float,
                       *, rest_established: bool) -> dict:
    if not rest_established:
        return {"classification": "REST_NOT_ESTABLISHED", "pass": False}
    b0_p95 = float(np.percentile(b0_speed, 95)); p_p95 = float(np.percentile(production_speed, 95))
    ratio = p_p95 / max(b0_p95, 1e-12)
    passed = p_p95 <= predictive_p99 and ratio <= 1.25
    if not passed and float(np.percentile(raw_gyro_norm, 95)) <= predictive_p99:
        classification = "COUPLED_SOLVER_STATIC_MOTION_INJECTION"
    elif not passed:
        classification = "HUMAN_STRAP_OR_SENSOR_MOTION"
    else:
        classification = "PASS"
    return {"b0_p95_rad_s": b0_p95, "production_p95_rad_s": p_p95,
            "production_over_b0": ratio, "predictive_p99_rad_s": predictive_p99,
            "classification": classification, "pass": passed}


def threshold_sensitivity(primary_value: float, threshold: float) -> dict:
    verdicts = {str(multiplier): primary_value <= multiplier * threshold for multiplier in (.5, 1., 2.)}
    return {"multipliers": verdicts, "threshold_sensitive_conditional": len(set(verdicts.values())) > 1}


def joint_relative_quaternions(segment_q: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {joint.name: so3.between(segment_q[joint.parent], segment_q[joint.child]) for joint in JOINTS}
