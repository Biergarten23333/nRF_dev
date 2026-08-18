from __future__ import annotations

from typing import Mapping
import hashlib

import numpy as np


TOLERANCES = (1e-4, 1e-5, 1e-6, 1e-7, 1e-8)


def matrix_sha256(matrix: np.ndarray) -> str:
    value = np.ascontiguousarray(matrix, dtype="<f8")
    return hashlib.sha256(value.tobytes()).hexdigest()


def _sweep(matrix: np.ndarray) -> list[dict]:
    eigenvalues = np.linalg.eigvalsh(0.5 * (matrix + matrix.T))
    scale = max(float(eigenvalues[-1]), 1e-300)
    return [{"relative_tolerance": tolerance,
             "rank": (rank := int(np.sum(eigenvalues > tolerance * scale))),
             "nullity": matrix.shape[0] - rank} for tolerance in TOLERANCES]


def observability_report(components: Mapping[str, np.ndarray],
                         global_yaw_gauge_vector: np.ndarray | None = None) -> dict:
    if not components:
        raise ValueError("actual accepted runtime matrices required")
    ordered = {key: np.asarray(components[key], float) for key in sorted(components)}
    shape = next(iter(ordered.values())).shape
    if shape != (30, 30) or any(value.shape != shape for value in ordered.values()):
        raise ValueError("all runtime information components must be 30x30")
    combined = np.zeros(shape)
    for value in ordered.values():
        combined += value
    if global_yaw_gauge_vector is None:
        # Identity-orientation synthetic convention. Real replay must pass the
        # current right-local representation of one common world-yaw rotation.
        global_yaw_gauge_vector = np.tile([0.0, 0.0, 1.0], 10)
    gauge = np.asarray(global_yaw_gauge_vector, float).reshape(30)
    gauge /= np.linalg.norm(gauge)
    projector = np.eye(30) - np.outer(gauge, gauge)
    gauge_free = projector @ combined @ projector
    categories = {
        "raw_measurement_information": ("raw_imu_orientation_likelihood",),
        "data_derived_calibration_information": ("neutral_relative_pose_reference", "functional_axis_soft_constraint"),
        "process_model_information": ("temporal_relative_motion",),
        "anatomy_soft_joint_prior_information": ("soft_rom_compliance",),
    }
    return {
        "matrix_source": "ACTUAL_ACCEPTED_RUNTIME_FACTORS",
        "components": {
            key: {"sha256": matrix_sha256(value), "trace": float(np.trace(value))}
            for key, value in ordered.items()
        },
        "combined_sha256": matrix_sha256(combined),
        "combined_convention_fixed_svd_relative_tolerance_sweep": _sweep(combined),
        "gauge_free_sha256": matrix_sha256(gauge_free),
        "gauge_free_svd_relative_tolerance_sweep": _sweep(gauge_free),
        "global_yaw_gauge_response_norm": float(np.linalg.norm(gauge_free @ gauge)),
        "information_categories": {
            category: {
                "factor_names": list(names),
                "trace": float(sum(np.trace(ordered[name]) for name in names if name in ordered)),
            } for category, names in categories.items()
        } | {
            "gauge_convention_prior_information": {
                "factor_names": [], "trace": 0.0,
                "status": "CONVENTION_APPLIED_OUTSIDE_GAUGE_FREE_INFORMATION",
            }
        },
        "gauge_statement": "L0_YAW_CONVENTION_FIXED; DATA_IDENTIFIED_GLOBAL_YAW=false",
    }
