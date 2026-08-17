"""Non-role H9 mounting-orientation diagnostic on S2."""

from __future__ import annotations

import numpy as np

H9 = (
    "BSF6C53", "BSF8BC4", "BSF1120", "BSF3C79", "BSF44AD",
    "BSF31CC", "BSFAA61", "BSFB165", "BSFEC35",
)
DISTINCT_LAYOUT = ("BSFC2CC",)


def validate_model_config(config: dict) -> None:
    if set(config.get("H9", ())) != set(H9):
        raise ValueError("H9 identity mismatch")
    if set(config.get("distinct_layout", ())) != set(DISTINCT_LAYOUT):
        raise ValueError("distinct layout must contain exactly BSFC2CC")
    if set(config["H9"]) & set(config["distinct_layout"]):
        raise ValueError("H9 and distinct layout overlap")
    if config.get("hard_equality"):
        raise ValueError("hard equality is forbidden")
    if config.get("named_sensor_axis") not in (None, "UNRESOLVED"):
        raise ValueError("physical edge-to-sensor axis has not been proved")
    if config.get("per_node_sigma_rad", 0) <= 0:
        raise ValueError("nonzero per-node mounting uncertainty required")


def _unit(v):
    a = np.asarray(v, float)
    n = np.linalg.norm(a)
    if not np.isfinite(n) or n < 1e-9:
        raise ValueError("invalid direction")
    return a / n


def antipodal_cluster(directions: dict[str, np.ndarray], iterations: int = 20) -> dict:
    """Fit a broad axial S2 cluster while retaining both global sign hypotheses."""
    if set(directions) != set(H9):
        raise ValueError("cluster input must be exactly H9; distinct layout pooling rejected")
    vectors = np.stack([_unit(directions[node]) for node in H9])
    mu = _unit(vectors[0])
    signs = np.ones(len(vectors))
    for _ in range(iterations):
        signs = np.where(vectors @ mu >= 0, 1.0, -1.0)
        mu = _unit(np.sum(vectors * signs[:, None], axis=0))
    aligned = vectors * signs[:, None]
    angles = np.arccos(np.clip(aligned @ mu, -1.0, 1.0))
    return {
        "mu_anonymous_sensor_frame": mu.tolist(),
        "antipodal_mu": (-mu).tolist(),
        "node_sign_hypotheses": {node: [int(signs[i]), int(-signs[i])] for i, node in enumerate(H9)},
        "per_node_geodesic_deviation_rad": {node: float(angles[i]) for i, node in enumerate(H9)},
        "angular_rms_rad": float(np.sqrt(np.mean(angles ** 2))),
        "edge_to_imu_axis": "PCB_EDGE_TO_IMU_AXIS_UNRESOLVED",
        "directed_edge_identity": "DIRECTED_EDGE_ID_UNRESOLVED",
        "role_information": False,
        "production_factor_count": 0,
        "use": "INITIALIZER_AND_DIAGNOSTIC_ONLY_NO_DOUBLE_COUNT",
    }


def standing_direction(imu_node: dict[str, np.ndarray]) -> tuple[np.ndarray, dict]:
    gyro = np.linalg.norm(imu_node["gyro_raw"], axis=1)
    acc = imu_node["acc_raw"].astype(float)
    acc_norm = np.linalg.norm(acc, axis=1)
    gyro_gate = gyro <= np.quantile(gyro, .35)
    norm_gate = np.abs(acc_norm - np.median(acc_norm)) <= 2.5 * (np.median(np.abs(acc_norm - np.median(acc_norm))) + 1e-6)
    gate = gyro_gate & norm_gate
    if gate.sum() < 20:
        gate = np.ones(len(acc), dtype=bool)
    samples = acc[gate]
    direction = _unit(np.median(samples, axis=0))
    unit = samples / np.maximum(np.linalg.norm(samples, axis=1, keepdims=True), 1e-9)
    scatter = np.arccos(np.clip(unit @ direction, -1.0, 1.0))
    return direction, {
        "selected_samples": int(gate.sum()),
        "total_samples": int(len(gate)),
        "window_selection_probability_proxy": float(gate.mean()),
        "angular_mad_rad": float(np.median(np.abs(scatter - np.median(scatter)))),
        "accelerometer_bias_uncertainty": "BROAD_PHASE1_WEAK_OBSERVABILITY_PROPAGATED_AS_STATUS",
        "sample_age_sensitivity": "REPORTED_SEPARATELY",
    }
