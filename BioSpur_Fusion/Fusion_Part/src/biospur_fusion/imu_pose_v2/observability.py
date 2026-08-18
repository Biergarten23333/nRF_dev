from __future__ import annotations

from typing import Mapping
import hashlib

import numpy as np


TOLERANCES = (1e-4, 1e-5, 1e-6, 1e-7, 1e-8)


def matrix_sha256(matrix: np.ndarray) -> str:
    value = np.ascontiguousarray(matrix, dtype="<f8")
    return hashlib.sha256(value.tobytes()).hexdigest()


def observability_report(components: Mapping[str, np.ndarray]) -> dict:
    if not components:
        raise ValueError("actual accepted runtime matrices required")
    ordered = {key: np.asarray(components[key], float) for key in sorted(components)}
    shape = next(iter(ordered.values())).shape
    if shape != (30, 30) or any(value.shape != shape for value in ordered.values()):
        raise ValueError("all runtime information components must be 30x30")
    combined = np.zeros(shape)
    for value in ordered.values():
        combined += value
    eigenvalues = np.linalg.eigvalsh(0.5 * (combined + combined.T))
    scale = max(float(eigenvalues[-1]), 1e-300)
    sweep = []
    for tolerance in TOLERANCES:
        rank = int(np.sum(eigenvalues > tolerance * scale))
        sweep.append({"relative_tolerance": tolerance, "rank": rank, "nullity": 30 - rank})
    return {
        "matrix_source": "ACTUAL_ACCEPTED_RUNTIME_FACTORS",
        "components": {
            key: {"sha256": matrix_sha256(value), "trace": float(np.trace(value))}
            for key, value in ordered.items()
        },
        "combined_sha256": matrix_sha256(combined),
        "svd_relative_tolerance_sweep": sweep,
        "gauge_statement": "L0_YAW_CONVENTION_FIXED; DATA_IDENTIFIED_GLOBAL_YAW=false",
    }
