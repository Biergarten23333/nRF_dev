"""Raw-six-axis edgewise and graph relative-heading calibration.

This path is deliberately independent of the historical V0 orientation/IK
profile.  The only signal inputs are accepted accelerometer and gyroscope
rows.  Each node runs a fresh magnetometer-free VQF instance.  Edge factors
then estimate relative world-z heading; the pelvis heading is the sole fixed
gauge in the graph solve.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass
import io
import math
from typing import Any, Callable, Iterable, Mapping, Sequence
import warnings

import numpy as np
import qmt
from scipy.optimize import least_squares, minimize_scalar
from scipy.spatial.transform import Rotation
from scipy.stats import chi2, norm as normal_distribution
from vqf import VQF

from .data import si_samples
from .math3d import matrix_to_quat_wxyz, proper_mean, rotation_angle, rz


SEGMENTS = (
    "pelvis",
    "torso",
    "upper_arm_left",
    "forearm_left",
    "upper_arm_right",
    "forearm_right",
    "thigh_left",
    "shank_left",
    "thigh_right",
    "shank_right",
)
EDGES = (
    ("pelvis_torso", "pelvis", "torso", "THREE_DOF"),
    ("shoulder_left", "torso", "upper_arm_left", "THREE_DOF"),
    ("elbow_left", "upper_arm_left", "forearm_left", "TWO_DOF_HINGE_DOMINANT"),
    ("shoulder_right", "torso", "upper_arm_right", "THREE_DOF"),
    ("elbow_right", "upper_arm_right", "forearm_right", "TWO_DOF_HINGE_DOMINANT"),
    ("hip_left", "pelvis", "thigh_left", "THREE_DOF"),
    ("knee_left", "thigh_left", "shank_left", "TWO_DOF_HINGE_DOMINANT"),
    ("hip_right", "pelvis", "thigh_right", "THREE_DOF"),
    ("knee_right", "thigh_right", "shank_right", "TWO_DOF_HINGE_DOMINANT"),
)
EDGE_BY_NAME = {name: (parent, child, kind) for name, parent, child, kind in EDGES}
EDGE_BY_CHILD = {child: (name, parent, kind) for name, parent, child, kind in EDGES}
HINGE_EDGES = {"elbow_left", "elbow_right", "knee_left", "knee_right"}
PHASES = (
    "VERIFIED_PRE_REST",
    "REST_TO_ACTION_TRANSITION",
    "FORMAL_ACTION_OR_HOLD",
    "ACTION_TO_REST_TRANSITION",
    "VERIFIED_POST_REST",
)
ROM_LIMIT_DEG = {
    "pelvis_torso": 70.0,
    "shoulder_left": 170.0,
    "shoulder_right": 170.0,
    "hip_left": 130.0,
    "hip_right": 130.0,
    "elbow_left": 165.0,
    "elbow_right": 165.0,
    "knee_left": 155.0,
    "knee_right": 155.0,
}
QMT_RATING_MIN = 0.25
QMT_MIN_INFORMATIVE_ROWS = 25
QMT_ACTIVE_RELATIVE_AXIS_RATE_MIN_RAD_S = math.radians(10.0)
QMT_RELATIVE_AXIS_RATE_Q90_MIN_RAD_S = math.radians(15.0)
QMT_MIN_ACTIVE_AXIS_ROWS = 25
QMT_AXIS_MULTISTART_MAX_SPREAD_DEG = 15.0
QMT_RELEVANT_WINDOW_HEADING_MAX_SPREAD_DEG = 15.0


def wrap(value: np.ndarray | float) -> np.ndarray:
    return (np.asarray(value, float) + np.pi) % (2.0 * np.pi) - np.pi


def circular_mean(values: Sequence[float], weights: Sequence[float] | None = None) -> float:
    x = np.asarray(values, float)
    if not len(x):
        return float("nan")
    w = np.ones(len(x)) if weights is None else np.asarray(weights, float)
    return float(math.atan2(float(np.sum(w * np.sin(x))), float(np.sum(w * np.cos(x)))))


def circular_spread_deg(values: Sequence[float]) -> float:
    x = np.asarray(values, float)
    if len(x) < 2:
        return 0.0
    center = circular_mean(x)
    return float(np.degrees(np.max(np.abs(wrap(x - center)))))


def _unit(value: np.ndarray, fallback: np.ndarray | None = None) -> np.ndarray:
    value = np.asarray(value, float)
    norm = float(np.linalg.norm(value))
    if norm <= np.finfo(float).eps:
        if fallback is None:
            raise ValueError("zero vector has no direction")
        return _unit(np.asarray(fallback, float))
    return value / norm


def _skew(value: np.ndarray) -> np.ndarray:
    x, y, z = np.asarray(value, float)
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])


def _skew_batch(values: np.ndarray) -> np.ndarray:
    """Return cross-product matrices without a Python loop over samples."""

    values = np.asarray(values, float)
    if values.ndim != 2 or values.shape[1] != 3:
        raise ValueError("batched cross-product input must have shape (n, 3)")
    output = np.zeros((len(values), 3, 3), dtype=float)
    x, y, z = values.T
    output[:, 0, 1] = -z
    output[:, 0, 2] = y
    output[:, 1, 0] = z
    output[:, 1, 2] = -x
    output[:, 2, 0] = -y
    output[:, 2, 1] = x
    return output


def _quat_wxyz_to_rotation(quat: np.ndarray) -> np.ndarray:
    quat = np.asarray(quat, float)
    return Rotation.from_quat(quat[:, [1, 2, 3, 0]]).as_matrix()


def _rotation_to_quat_wxyz(rotation: np.ndarray) -> np.ndarray:
    return matrix_to_quat_wxyz(np.asarray(rotation, float))


@dataclass(frozen=True)
class Raw6Episode:
    capture: str
    action: str
    partition: str
    time_ns: np.ndarray
    phase: np.ndarray
    acc: Mapping[str, np.ndarray]
    gyro: Mapping[str, np.ndarray]
    rotation_world_sensor: Mapping[str, np.ndarray]
    quat_world_sensor_wxyz: Mapping[str, np.ndarray]
    rest_detected: Mapping[str, np.ndarray]
    bias_rad_s: Mapping[str, np.ndarray]
    audit: Mapping[str, Any]


@dataclass(frozen=True)
class B5Block:
    action: str
    partition: str
    phase: np.ndarray
    parent_rotation: np.ndarray
    child_rotation: np.ndarray
    parent_force: np.ndarray
    child_force: np.ndarray
    parent_kinematic: np.ndarray
    child_kinematic: np.ndarray
    sample_weight: np.ndarray
    # Non-null only when every edge block came from the same synchronized
    # Raw6Episode rows.  Independent edgewise generators must leave it unset.
    sample_time_ns: np.ndarray | None = None
    # C2 covariance-block metadata. Legacy callers leave these unset and keep
    # their established sample-weight semantics.
    information_sigma_mps2: float | None = None
    effective_sample_size: float | None = None
    covariance_block_id: str | None = None
    # C2 noise-only propagation inputs. These are estimated from verified-rest
    # samples (or declared independent synthetic sensor noise), never from the
    # block's dynamic residual or jerk.
    parent_gyro: np.ndarray | None = None
    child_gyro: np.ndarray | None = None
    parent_acc_noise_cov: np.ndarray | None = None
    child_acc_noise_cov: np.ndarray | None = None
    parent_gyro_noise_cov: np.ndarray | None = None
    child_gyro_noise_cov: np.ndarray | None = None
    parent_noise_cov_source: str | None = None
    child_noise_cov_source: str | None = None


@dataclass(frozen=True)
class EdgeFactors:
    name: str
    parent: str
    child: str
    kind: str
    b5_train: tuple[B5Block, ...]
    b5_held_out: tuple[B5Block, ...]
    hinge_axis_parent: np.ndarray | None
    hinge_axis_child: np.ndarray | None
    qmt_report: Mapping[str, Any] | None
    axis_report: Mapping[str, Any] | None


@dataclass(frozen=True)
class _PreparedB5:
    parent_kinematic_world: np.ndarray
    child_kinematic_base: np.ndarray
    parent_force_world: np.ndarray
    child_force_base: np.ndarray
    sample_weight: np.ndarray
    action_ranges: tuple[tuple[int, int, str], ...]


def _phase_vector(time_ns: np.ndarray, episode_diagnostic: Mapping[str, Any]) -> np.ndarray:
    labels = np.full(len(time_ns), "UNCLASSIFIED_COMPLETE_EPISODE", dtype="U40")
    for row in episode_diagnostic.get("phases", []):
        selected = (
            (time_ns >= int(row["start_global_time_ns"]))
            & (time_ns < int(row["stop_global_time_ns_exclusive"]))
        )
        labels[selected] = str(row["phase"])
    return labels


def raw6_episode_from_rows(
    *,
    capture: str,
    action: str,
    partition: str,
    rows_by_node: Mapping[str, np.ndarray],
    identity: Mapping[str, str],
    episode_diagnostic: Mapping[str, Any],
    rate_hz: int = 50,
) -> Raw6Episode:
    """Resample raw SI acc/gyr and execute independent 6D VQF per node."""

    if set(rows_by_node) != set(identity):
        raise ValueError("raw-six-axis episode lacks the capture-local ten-node identity")
    accepted = {node: rows[rows["status"] == 1] for node, rows in rows_by_node.items()}
    start = max(int(rows["global_time_ns"][0]) for rows in accepted.values())
    stop = min(int(rows["global_time_ns"][-1]) for rows in accepted.values())
    step = int(round(1e9 / int(rate_hz)))
    first = ((start + step - 1) // step) * step
    last = (stop // step) * step
    if last - first < 4 * step:
        raise RuntimeError(f"{capture}:{action}: no common raw-six-axis interval")
    grid = np.arange(first, last + 1, step, dtype=np.int64)
    acc_out: dict[str, np.ndarray] = {}
    gyro_out: dict[str, np.ndarray] = {}
    rot_out: dict[str, np.ndarray] = {}
    quat_out: dict[str, np.ndarray] = {}
    rest_out: dict[str, np.ndarray] = {}
    bias_out: dict[str, np.ndarray] = {}
    node_audit: dict[str, Any] = {}
    for node, rows in accepted.items():
        source_time = rows["global_time_ns"].astype(np.int64)
        acc, gyro = si_samples(rows)
        target_s = (grid - grid[0]).astype(float) * 1e-9
        source_s = (source_time - grid[0]).astype(float) * 1e-9
        acc_i = np.column_stack([
            np.interp(target_s, source_s, acc[:, axis]) for axis in range(3)
        ])
        gyro_i = np.column_stack([
            np.interp(target_s, source_s, gyro[:, axis]) for axis in range(3)
        ])
        vqf = VQF(1.0 / rate_hz, magDistRejectionEnabled=False)
        result = vqf.updateBatch(
            np.ascontiguousarray(gyro_i), np.ascontiguousarray(acc_i),
        )
        segment = identity[node]
        quaternion = np.asarray(result["quat6D"], float)
        rotation = _quat_wxyz_to_rotation(quaternion)
        acc_out[segment] = acc_i
        gyro_out[segment] = gyro_i
        quat_out[segment] = quaternion
        rot_out[segment] = rotation
        rest_out[segment] = np.asarray(result["restDetected"], bool)
        bias_out[segment] = np.asarray(result["bias"], float)
        node_audit[node] = {
            "segment": segment,
            "source_rows": int(len(rows)),
            "source_first_time_ns": int(source_time[0]),
            "source_last_time_ns": int(source_time[-1]),
            "source_median_dt_ms": float(np.median(np.diff(source_time)) * 1e-6),
            "resampled_rows": int(len(grid)),
            "vqf_input_fields": ["acc_raw", "gyro_raw", "global_time_ns"],
            "magnetometer_argument_supplied": False,
            "quat6d_output_used": True,
            "vendor_orientation_fields_read": [],
        }
    if set(rot_out) != set(SEGMENTS):
        raise ValueError("capture-local identity is not the exact body graph")
    phase = _phase_vector(grid, episode_diagnostic)
    return Raw6Episode(
        capture=capture,
        action=action,
        partition=partition,
        time_ns=grid,
        phase=phase,
        acc=acc_out,
        gyro=gyro_out,
        rotation_world_sensor=rot_out,
        quat_world_sensor_wxyz=quat_out,
        rest_detected=rest_out,
        bias_rad_s=bias_out,
        audit={
            "schema": "biospur-pure-imu-v0-raw6-episode-v1",
            "capture": capture,
            "action": action,
            "partition": partition,
            "rate_hz": int(rate_hz),
            "nodes": node_audit,
            "input_authority": "RAW_ACCELEROMETER_PLUS_GYROSCOPE_ONLY",
            "magnetometer_used": False,
            "vendor_orientation_truth_used": False,
            "old_profile_or_ik_state_used": False,
            "action_label_pose_truth_used": False,
            "complete_episode_rows_used": True,
            "five_phase_status": episode_diagnostic.get("EPISODE_COMPLETENESS"),
            "phase_row_counts": {
                phase_name: int(np.count_nonzero(phase == phase_name))
                for phase_name in (*PHASES, "UNCLASSIFIED_COMPLETE_EPISODE")
            },
        },
    )


def _joint_frame(axis_sensor: np.ndarray) -> np.ndarray:
    """Return sensor-from-joint, with joint +z mapped to the measured axis."""

    z = _unit(axis_sensor)
    seed = np.array([1.0, 0.0, 0.0])
    if abs(float(seed @ z)) > 0.85:
        seed = np.array([0.0, 1.0, 0.0])
    x = _unit(seed - z * float(seed @ z))
    y = _unit(np.cross(z, x))
    return np.column_stack((x, y, z))


def _segment_gyro_noise_covariance(
    episodes: Sequence[Raw6Episode], segment: str, *, context: str,
) -> tuple[np.ndarray, str]:
    """Resolve bias-corrected gyro observation covariance for significance tests.

    Real C2 rows carry both per-sample still noise and the common uncertainty
    of the capture-wide median bias.  The latter is correlated across rows and
    is kept explicit in the provenance; adding it here prevents a fitted bias
    from being treated as an exact known constant in observability gates.
    """

    candidates = []
    sources = []
    for episode in episodes:
        for row in episode.audit.get("nodes", {}).values():
            if row.get("segment") == segment and "gyro_noise_cov_rad2_s2" in row:
                covariance = np.asarray(row["gyro_noise_cov_rad2_s2"], dtype=float)
                if "capture_wide_bias_cov_rad2_s2" in row:
                    covariance = covariance + np.asarray(
                        row["capture_wide_bias_cov_rad2_s2"], dtype=float,
                    )
                candidates.append(covariance)
                sources.append(str(row.get(
                    "noise_covariance_source", "CAPTURE_WIDE_INITIAL_STILL_ONLY",
                )) + "+" + str(row.get(
                    "bias_covariance_source", "BIAS_UNCERTAINTY_UNAVAILABLE_LEGACY",
                )))
                break
        else:
            if episode.audit.get("generator") == "INDEPENDENT_ANALYTIC_TRUTH":
                sigma = float(episode.audit["noise_rad_s"])
                candidates.append(np.eye(3) * sigma**2)
                sources.append("INDEPENDENT_SYNTHETIC_SENSOR_NOISE_DECLARATION")
    if not candidates:
        raise ValueError(f"{context}:{segment}: gyro-noise covariance is unavailable")
    reference = candidates[0]
    if any(
        value.shape != (3, 3)
        or not np.allclose(value, reference, rtol=1e-9, atol=1e-15)
        for value in candidates
    ):
        raise ValueError(f"{context}:{segment}: capture-wide gyro covariance changed")
    if np.min(np.linalg.eigvalsh(reference)) <= 0.0:
        raise ValueError(f"{context}:{segment}: gyro covariance is not positive definite")
    return reference, "+".join(sorted(set(sources)))


def _segment_gyro_bias_report(
    episodes: Sequence[Raw6Episode], segment: str, *, context: str,
) -> dict[str, Any]:
    """Audit the already-applied capture-wide gyro bias for one segment."""

    values = []
    sources = []
    for episode in episodes:
        if segment not in episode.bias_rad_s:
            raise ValueError(f"{context}:{segment}: gyro-bias state is unavailable")
        rows = np.asarray(episode.bias_rad_s[segment], dtype=float)
        if rows.ndim != 2 or rows.shape[1] != 3 or not len(rows):
            raise ValueError(f"{context}:{segment}: gyro-bias state has invalid shape")
        if not np.allclose(rows, rows[0], rtol=0.0, atol=1e-15):
            raise ValueError(f"{context}:{segment}: capture-wide gyro bias changed within episode")
        values.append(rows[0])
        sources.append(
            "INDEPENDENT_SYNTHETIC_DECLARATION"
            if episode.audit.get("generator") == "INDEPENDENT_ANALYTIC_TRUTH"
            else "CAPTURE_WIDE_INITIAL_STILL_MEDIAN"
        )
    reference = np.asarray(values[0], dtype=float)
    if any(not np.allclose(value, reference, rtol=0.0, atol=1e-15) for value in values):
        raise ValueError(f"{context}:{segment}: capture-wide gyro bias changed across episodes")
    return {
        "bias_rad_s": reference.tolist(),
        "source": "+".join(sorted(set(sources))),
        "subtracted_before_qmt": True,
        "treated_as_exact_known": False,
    }


def qmt_selected_cross_denominator_report(
    gyro: np.ndarray,
    initial_axis: np.ndarray,
    selected_indices: np.ndarray,
    gyro_noise_cov_rad2_s2: np.ndarray,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Noise-standardized observability on QMT's actual selected gyro rows."""

    gyro = np.asarray(gyro, dtype=float)
    axis = _unit(np.asarray(initial_axis, dtype=float))
    selected = np.asarray(selected_indices, dtype=int).reshape(-1)
    covariance = np.asarray(gyro_noise_cov_rad2_s2, dtype=float)
    if gyro.ndim != 2 or gyro.shape[1] != 3 or len(selected) == 0:
        raise ValueError("QMT selected cross-denominator inputs are invalid")
    if covariance.shape != (3, 3) or not np.isfinite(covariance).all():
        raise ValueError("QMT gyro-noise covariance must be finite 3x3")
    eigenvalues = np.linalg.eigvalsh(covariance)
    if np.min(eigenvalues) <= 0.0:
        raise ValueError("QMT gyro-noise covariance must be positive definite")
    if np.any(selected < 0) or np.any(selected >= len(gyro)):
        raise ValueError("QMT selected cross-denominator index is out of range")
    rows = gyro[selected]
    norm = np.linalg.norm(rows, axis=1)
    cross_vector = np.cross(rows, axis)
    cross = np.linalg.norm(cross_vector, axis=1)
    sin_theta = np.divide(
        cross,
        norm,
        out=np.zeros_like(cross),
        where=norm > np.finfo(float).eps,
    )
    sin_theta = np.clip(sin_theta, 0.0, 1.0)
    inverse_covariance = np.linalg.inv(covariance)
    excitation_significance = np.sqrt(np.maximum(0.0, np.einsum(
        "ni,ij,nj->n", rows, inverse_covariance, rows,
    )))
    excitation_alpha = float(contract["excitation_false_positive_probability"])
    excitation_limit = float(np.sqrt(chi2.ppf(1.0 - excitation_alpha, df=3)))
    declared_excitation_limit = float(contract["minimum_excitation_mahalanobis_norm"])
    if not np.isclose(excitation_limit, declared_excitation_limit, rtol=0.0, atol=1e-12):
        raise ValueError("QMT excitation threshold does not match preregistered chi-square level")
    excited = excitation_significance >= excitation_limit
    excited_fraction = float(np.mean(excited))
    projection = -_skew(axis)
    cross_covariance = projection @ covariance @ projection.T
    cross_covariance_rank = int(np.linalg.matrix_rank(cross_covariance))
    if cross_covariance_rank != 2:
        raise ValueError("QMT propagated cross-product covariance is not rank two")
    inverse_cross_covariance = np.linalg.pinv(cross_covariance, hermitian=True)
    cross_significance = np.sqrt(np.maximum(0.0, np.einsum(
        "ni,ij,nj->n", cross_vector, inverse_cross_covariance, cross_vector,
    )))
    alignment_alpha = float(contract["alignment_false_positive_probability"])
    cross_limit = float(np.sqrt(chi2.ppf(1.0 - alignment_alpha, df=2)))
    declared_cross_limit = float(contract[
        "maximum_uninformative_cross_noise_significance"
    ])
    if not np.isclose(cross_limit, declared_cross_limit, rtol=0.0, atol=1e-12):
        raise ValueError("QMT cross-axis threshold does not match preregistered chi-square level")
    material_fraction = float(contract[
        "material_uninformative_fraction_among_excited"
    ])
    quantile_probability = float(contract["quantile_probability"])
    minimum_excited_fraction = float(contract["minimum_excited_selected_fraction"])
    minimum_effective_excited_rows = float(contract["minimum_effective_excited_rows"])
    if excitation_limit <= 0.0 or cross_limit <= 0.0 or minimum_effective_excited_rows <= 0.0:
        raise ValueError("QMT noise-significance thresholds are invalid")
    if (
        not 0.0 < material_fraction < 0.5
        or not 0.0 < quantile_probability < 0.5
        or not 0.0 < minimum_excited_fraction < 1.0
    ):
        raise ValueError("QMT selected-row fraction/quantile threshold is invalid")
    excited_cross = cross_significance[excited]
    excited_sin = sin_theta[excited]
    if len(excited_cross):
        uninformative = excited_cross <= cross_limit
        fraction = float(np.mean(uninformative))
        quantile = float(np.quantile(excited_cross, quantile_probability))
        sin_quantiles = {
            "q01": float(np.quantile(excited_sin, 0.01)),
            "q05": float(np.quantile(excited_sin, 0.05)),
            "q10": float(np.quantile(excited_sin, 0.10)),
            "q50": float(np.quantile(excited_sin, 0.50)),
        }
    else:
        uninformative = np.zeros(0, dtype=bool)
        fraction = 0.0
        quantile = 0.0
        sin_quantiles = {name: None for name in ("q01", "q05", "q10", "q50")}
    selected_order = np.argsort(selected)
    effective_selected_rows, selected_lag1 = _lag1_effective_rows(
        excitation_significance[selected_order], 0.98,
    )
    excited_selected_indices = selected[excited]
    if len(excited_selected_indices):
        excited_order = np.argsort(excited_selected_indices)
        effective_excited_rows, excited_lag1 = _lag1_effective_rows(
            excited_cross[excited_order], 0.98,
        )
    else:
        effective_excited_rows, excited_lag1 = 0.0, 0.0
    fraction_gate = fraction >= material_fraction
    quantile_gate = quantile <= cross_limit
    widespread_low_excitation = bool(
        excited_fraction < minimum_excited_fraction
        or effective_excited_rows < minimum_effective_excited_rows
    )
    material_alignment = bool(
        effective_excited_rows >= minimum_effective_excited_rows
        and fraction_gate and quantile_gate
    )
    material = bool(widespread_low_excitation or material_alignment)
    return {
        "domain": "QMT_SELECTED_300",
        "selected_rows": int(len(selected)),
        "gyro_noise_covariance_rad2_s2": covariance.tolist(),
        "excitation_statistic": contract["excitation_statistic"],
        "excitation_false_positive_probability": excitation_alpha,
        "minimum_excitation_mahalanobis_norm": excitation_limit,
        "low_excitation_selected_rows": int(np.count_nonzero(~excited)),
        "sufficiently_excited_selected_rows": int(np.count_nonzero(excited)),
        "sufficiently_excited_selected_fraction": excited_fraction,
        "minimum_excited_selected_fraction": minimum_excited_fraction,
        "effective_selected_rows": effective_selected_rows,
        "selected_significance_lag1_correlation": selected_lag1,
        "effective_sufficiently_excited_rows": effective_excited_rows,
        "excited_cross_significance_lag1_correlation": excited_lag1,
        "minimum_effective_excited_rows": minimum_effective_excited_rows,
        "widespread_selected_low_excitation": bool(widespread_low_excitation),
        "alignment_statistic": contract["alignment_statistic"],
        "alignment_false_positive_probability": alignment_alpha,
        "propagated_cross_product_noise_covariance_rad2_s2": cross_covariance.tolist(),
        "propagated_cross_product_noise_covariance_rank": cross_covariance_rank,
        "propagated_cross_product_noise_rms_rad_s": float(
            np.sqrt(np.trace(cross_covariance))
        ),
        "maximum_uninformative_cross_noise_significance": cross_limit,
        "uninformative_near_axis_excited_rows": int(np.count_nonzero(uninformative)),
        "uninformative_near_axis_fraction_among_excited": fraction,
        "material_uninformative_fraction_threshold": material_fraction,
        "quantile_probability": quantile_probability,
        "cross_noise_significance_quantile": quantile,
        "sin_theta_quantiles_sufficiently_excited": sin_quantiles,
        "exact_zero_selected_rows": int(np.count_nonzero(cross == 0.0)),
        "exact_zero_semantics": "NUMERICAL_SENTINEL_ONLY_NOT_PHYSICAL_GATE",
        "fraction_gate": bool(fraction_gate),
        "quantile_gate": bool(quantile_gate),
        "material_near_axis_distribution": material_alignment,
        "material_selected_degeneracy": material,
        "isolated_near_axis_row_can_gate": False,
        "threshold_provenance": contract["threshold_provenance"],
        "uses_preselection_600_rows_outside_actual_qmt_selection": False,
    }


def estimate_hinge_axes_qmt(
    edge: str,
    episodes: Sequence[Raw6Episode],
    *,
    maximum_samples: int = 600,
    qmt_settings: Mapping[str, Any] | None = None,
    bounded_call: Callable[[str, Callable[[], Any]], Any] | None = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Execute qmt's Olsson raw acc/gyr hinge-axis estimator deterministically."""

    parent, child, _ = EDGE_BY_NAME[edge]
    acc_parent = np.concatenate([ep.acc[parent] for ep in episodes])
    acc_child = np.concatenate([ep.acc[child] for ep in episodes])
    gyro_parent = np.concatenate([ep.gyro[parent] for ep in episodes])
    gyro_child = np.concatenate([ep.gyro[child] for ep in episodes])
    source_action = np.concatenate([
        np.full(len(ep.time_ns), ep.action, dtype=object) for ep in episodes
    ])
    source_index = np.concatenate([
        np.arange(len(ep.time_ns), dtype=np.int64) for ep in episodes
    ])
    source_time_ns = np.concatenate([ep.time_ns for ep in episodes])
    source_phase = np.concatenate([ep.phase for ep in episodes])
    if len(acc_parent) > maximum_samples:
        keep = np.unique(np.rint(
            np.linspace(0, len(acc_parent) - 1, maximum_samples)
        ).astype(int))
        acc_parent, acc_child = acc_parent[keep], acc_child[keep]
        gyro_parent, gyro_child = gyro_parent[keep], gyro_child[keep]
        source_action = source_action[keep]
        source_index = source_index[keep]
        source_time_ns = source_time_ns[keep]
        source_phase = source_phase[keep]

    parent_gyro_noise_cov, parent_gyro_noise_source = _segment_gyro_noise_covariance(
        episodes, parent, context=edge,
    )
    child_gyro_noise_cov, child_gyro_noise_source = _segment_gyro_noise_covariance(
        episodes, child, context=edge,
    )
    parent_gyro_bias_report = _segment_gyro_bias_report(
        episodes, parent, context=edge,
    )
    child_gyro_bias_report = _segment_gyro_bias_report(
        episodes, child, context=edge,
    )
    starts = (
        np.array([0.0, 0.0, 0.0, 0.0]),
        np.array([0.45, -0.7, -0.35, 0.8]),
        np.array([-0.55, 1.1, 0.50, -1.0]),
    )
    settings = dict(qmt_settings or {})
    use_sample_selection = bool(settings.get("use_official_qmt_sample_selection", False))
    selected_data_size = int(settings.get("selected_data_size", len(acc_parent)))
    maximum_iterations = int(settings.get("maximum_gauss_newton_iterations", 299))
    selection_window = int(settings.get("selection_window_size", 21))
    energy_threshold = float(settings.get("angular_rate_energy_threshold_rad2_s2", 1.0))
    selected_degeneracy_contract = settings.get("selection_aware_cross_denominator")
    if maximum_iterations < 2 or selected_data_size < 8:
        raise ValueError("QMT hinge-axis iteration/sample limits are invalid")
    if not isinstance(selected_degeneracy_contract, Mapping):
        raise ValueError("QMT selection-aware degeneracy contract is missing")

    def source_rows(
        indices: np.ndarray,
        *,
        gyro: np.ndarray | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        selected = np.asarray(indices, dtype=int).reshape(-1)
        if limit is not None:
            selected = selected[: int(limit)]
        output = []
        for index in selected:
            row = {
                "preselection_600_index": int(index),
                "action": str(source_action[index]),
                "episode_sample_index": int(source_index[index]),
                "global_time_ns": int(source_time_ns[index]),
                "phase": str(source_phase[index]),
            }
            if gyro is not None:
                row.update({
                    "gyro_rad_s": np.asarray(gyro[index], dtype=float).tolist(),
                    "gyro_norm_rad_s": float(np.linalg.norm(gyro[index])),
                })
            output.append(row)
        return output

    def selected_indices(sample_selection: Mapping[str, Any], key: str) -> np.ndarray:
        if not use_sample_selection:
            return np.arange(len(acc_parent), dtype=int)
        indices = np.asarray(sample_selection.get(key, []), dtype=int).reshape(-1)
        if len(indices) == 0:
            raise RuntimeError(f"QMT official selection returned no {key}")
        if np.any(indices < 0) or np.any(indices >= len(acc_parent)):
            raise RuntimeError(f"QMT official selection returned out-of-range {key}")
        if len(np.unique(indices)) != len(indices):
            raise RuntimeError(f"QMT official selection returned duplicate {key}")
        return indices

    def selection_contribution(
        gyro_indices: np.ndarray, accelerometer_indices: np.ndarray,
    ) -> dict[str, Any]:
        parent_energy = np.sum(gyro_parent[gyro_indices] ** 2, axis=1)
        child_energy = np.sum(gyro_child[gyro_indices] ** 2, axis=1)
        combined_energy = parent_energy + child_energy
        total_energy = float(np.sum(combined_energy))
        parent_inverse_cov = np.linalg.inv(parent_gyro_noise_cov)
        child_inverse_cov = np.linalg.inv(child_gyro_noise_cov)
        parent_significance = np.sqrt(np.maximum(0.0, np.einsum(
            "ni,ij,nj->n", gyro_parent[gyro_indices], parent_inverse_cov,
            gyro_parent[gyro_indices],
        )))
        child_significance = np.sqrt(np.maximum(0.0, np.einsum(
            "ni,ij,nj->n", gyro_child[gyro_indices], child_inverse_cov,
            gyro_child[gyro_indices],
        )))
        excitation_limit = float(
            selected_degeneracy_contract["minimum_excitation_mahalanobis_norm"]
        )
        gyro_rows = []
        keys = sorted({
            (str(source_action[index]), str(source_phase[index]))
            for index in gyro_indices
        })
        for action, phase in keys:
            mask = np.asarray([
                str(source_action[index]) == action
                and str(source_phase[index]) == phase
                for index in gyro_indices
            ], dtype=bool)
            energy = float(np.sum(combined_energy[mask]))
            gyro_rows.append({
                "action": action,
                "phase": phase,
                "selected_rows": int(np.count_nonzero(mask)),
                "selected_row_fraction": float(np.mean(mask)),
                "parent_gyro_energy_sum_rad2_s2": float(np.sum(parent_energy[mask])),
                "child_gyro_energy_sum_rad2_s2": float(np.sum(child_energy[mask])),
                "combined_gyro_energy_sum_rad2_s2": energy,
                "combined_gyro_energy_fraction": (
                    energy / total_energy if total_energy > 0.0 else 0.0
                ),
                "parent_gyro_mahalanobis_q50": float(np.quantile(
                    parent_significance[mask], 0.50,
                )),
                "child_gyro_mahalanobis_q50": float(np.quantile(
                    child_significance[mask], 0.50,
                )),
                "both_endpoints_above_excitation_threshold_rows": int(np.count_nonzero(
                    mask
                    & (parent_significance >= excitation_limit)
                    & (child_significance >= excitation_limit)
                )),
            })
        accelerometer_rows = []
        accelerometer_keys = sorted({
            (str(source_action[index]), str(source_phase[index]))
            for index in accelerometer_indices
        })
        for action, phase in accelerometer_keys:
            count = int(np.count_nonzero([
                str(source_action[index]) == action
                and str(source_phase[index]) == phase
                for index in accelerometer_indices
            ]))
            accelerometer_rows.append({
                "action": action,
                "phase": phase,
                "selected_rows": count,
                "selected_row_fraction": count / float(len(accelerometer_indices)),
            })
        return {
            "gyro_by_action_phase": gyro_rows,
            "accelerometer_by_action_phase": accelerometer_rows,
            "all_predeclared_actions_retained_before_official_selection": sorted(set(
                str(value) for value in source_action
            )),
            "gyro_noise_covariance_sources": {
                "parent": parent_gyro_noise_source,
                "child": child_gyro_noise_source,
            },
        }

    input_degeneracy: dict[str, Any] = {
        "schema": "biospur-qmt-hinge-input-degeneracy-v2",
        "input_block_id": (
            f"{edge}:" + "+".join(ep.action for ep in episodes)
            + f":QMT_COMPLETE_EPISODE_RESAMPLED_{len(acc_parent)}"
        ),
        "row_domains": {
            "PRESELECTION_600": (
                "uniform capture-wide bounded input before QMT official sample selection; "
                "diagnostic only and cannot create a named conflict"
            ),
            "QMT_SELECTED_300": (
                "actual optimizer rows from debug.sampleSelectionVars; may create a "
                "named denominator conflict"
            ),
        },
        "named_conflict_gate": (
            "ACTUAL_QMT_SELECTED_GYRO_ROW_OR_CAPTURED_RUNTIME_WARNING_ONLY"
        ),
        "complete_episodes_retained_in_other_factors": True,
        "qmt_observability_selection_is_not_episode_deletion": True,
        "gyro_preprocessing": {
            "parent": {
                **parent_gyro_bias_report,
                "observation_covariance_rad2_s2": parent_gyro_noise_cov.tolist(),
                "observation_covariance_source": parent_gyro_noise_source,
            },
            "child": {
                **child_gyro_bias_report,
                "observation_covariance_rad2_s2": child_gyro_noise_cov.tolist(),
                "observation_covariance_source": child_gyro_noise_source,
            },
        },
        "selected_row_dependence": (
            "DISTRIBUTIONS_REPORT_LAG1_EFFECTIVE_ROWS; CAPTURE_WIDE_BIAS_"
            "UNCERTAINTY_IS_COMMON_MODE_AND_NOT_COUNTED_AS_INDEPENDENT_ROWS"
        ),
        "endpoints": {},
        "named_numerical_physical_conflicts": [],
    }
    for role, gyro, pair in (
        ("parent", gyro_parent, (0, 1)),
        ("child", gyro_child, (2, 3)),
    ):
        norm = np.linalg.norm(gyro, axis=1)
        zero = np.flatnonzero(norm == 0.0)
        start_rows = []
        exact_union: set[int] = set()
        for start_index, x0 in enumerate(starts):
            theta, phi = x0[list(pair)]
            axis = np.array([
                np.cos(theta) * np.cos(phi),
                np.cos(theta) * np.sin(phi),
                np.sin(theta),
            ])
            denominator = np.linalg.norm(np.cross(gyro, axis), axis=1)
            affected = np.flatnonzero(denominator == 0.0)
            exact_union.update(int(index) for index in affected)
            start_rows.append({
                "start_index": start_index,
                "initial_axis_sensor": axis.tolist(),
                "preselection_600_exact_zero_cross_denominator_count": int(len(affected)),
                "preselection_600_indices": affected[:12].tolist(),
                "named_conflict_from_preselection_scan": False,
            })
        affected = np.asarray(sorted(exact_union), dtype=int)
        input_degeneracy["endpoints"][role] = {
            "preselection_600_exact_zero_gyro_count": int(len(zero)),
            "preselection_600_minimum_gyro_norm_rad_s": float(np.min(norm)),
            "initial_start_denominators": start_rows,
            "affected_preselection_600_rows_diagnostic_only": source_rows(
                affected, gyro=gyro, limit=12,
            ),
            "preselection_scan_can_create_named_conflict": False,
            "exact_comparison_semantics": (
                "NUMERICAL_SENTINEL_AND_QUANTIZED_CODE_DUPLICATE_DIAGNOSTIC_ONLY"
            ),
        }
    candidates = []
    for start_index, x0 in enumerate(starts):
        upstream_stdout = io.StringIO()
        def run_qmt():
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", RuntimeWarning)
                with redirect_stdout(upstream_stdout):
                    result = qmt.jointAxisEstHingeOlsson(
                        acc_parent,
                        acc_child,
                        gyro_parent,
                        gyro_child,
                        estSettings={
                            "x0": x0,
                            "useSampleSelection": use_sample_selection,
                            "dataSize": selected_data_size,
                            "winSize": selection_window,
                            "angRateEnergyThreshold": energy_threshold,
                            "maxSteps": maximum_iterations,
                            "quiet": True,
                            "w0": 50.0,
                        },
                        debug=True,
                    )
            return result, tuple(caught)

        (axis_parent, axis_child, debug), caught = (
            bounded_call(f"{edge}:qmt_start_{start_index}", run_qmt)
            if bounded_call is not None else run_qmt()
        )
        axis_parent = _unit(np.asarray(axis_parent, float).reshape(3))
        axis_child = _unit(np.asarray(axis_child, float).reshape(3))
        trajectory = np.asarray(debug["optimVarsAxis"]["ftraj"], float).ravel()
        runtime_warnings = [{
            "category": warning.category.__name__,
            "message": str(warning.message),
            "filename": str(warning.filename),
            "lineno": int(warning.lineno),
        } for warning in caught]
        if runtime_warnings:
            input_degeneracy["named_numerical_physical_conflicts"].append(
                f"{edge}:QMT_RUNTIME_NUMERICAL_WARNING_START_{start_index}"
            )
        sample_selection = debug["sampleSelectionVars"]
        gyro_indices = selected_indices(sample_selection, "gyrSamples")
        accelerometer_indices = selected_indices(sample_selection, "accSamples")
        selected_denominator_audit = {}
        for role, gyro, pair in (
            ("parent", gyro_parent, (0, 1)),
            ("child", gyro_child, (2, 3)),
        ):
            theta, phi = x0[list(pair)]
            initial_axis = np.array([
                np.cos(theta) * np.cos(phi),
                np.cos(theta) * np.sin(phi),
                np.sin(theta),
            ])
            distribution = qmt_selected_cross_denominator_report(
                gyro,
                initial_axis,
                gyro_indices,
                (
                    parent_gyro_noise_cov if role == "parent"
                    else child_gyro_noise_cov
                ),
                selected_degeneracy_contract,
            )
            denominator = np.linalg.norm(np.cross(gyro, initial_axis), axis=1)
            affected_preselection = np.flatnonzero(denominator == 0.0)
            affected_selected = gyro_indices[np.isin(gyro_indices, affected_preselection)]
            selected_denominator_audit[role] = {
                "initial_axis_sensor": initial_axis.tolist(),
                "qmt_selected_300_affected_count": int(len(affected_selected)),
                "affected_qmt_selected_300_rows": source_rows(
                    affected_selected, gyro=gyro,
                ),
                "distribution": distribution,
                "named_conflict": bool(distribution["material_selected_degeneracy"]),
                "historical_few_exact_rows_causal_claim": (
                    "INVALID_POST_HOC_DESCRIPTIVE_ONLY"
                ),
            }
        candidates.append({
            "start": start_index,
            "axis_parent": axis_parent,
            "axis_child": axis_child,
            "final_cost": float(trajectory[-1]),
            "iterations": int(len(trajectory)),
            "upstream_stdout_line_count": len(
                upstream_stdout.getvalue().splitlines()
            ),
            "upstream_stdout_tail": upstream_stdout.getvalue().splitlines()[-3:],
            "runtime_warnings": runtime_warnings,
            "qmt_selected_gyro_rows": int(len(gyro_indices)),
            "qmt_selected_accelerometer_rows": int(len(accelerometer_indices)),
            "qmt_selected_gyro_preselection_600_indices": gyro_indices.tolist(),
            "qmt_selected_accelerometer_preselection_600_indices": (
                accelerometer_indices.tolist()
            ),
            "qmt_selected_gyro_source_rows": source_rows(gyro_indices),
            "qmt_selected_accelerometer_source_rows": source_rows(
                accelerometer_indices,
            ),
            "qmt_selected_denominator_audit": selected_denominator_audit,
            "qmt_selected_action_phase_excitation_contribution": (
                selection_contribution(gyro_indices, accelerometer_indices)
            ),
        })
    best = min(candidates, key=lambda row: row["final_cost"])
    # Resolve the unavoidable joint-axis sign so the vertical components agree
    # before any heading correction (world-z yaw cannot change that sign).
    vertical_parent = np.concatenate([
        np.einsum("nij,j->ni", ep.rotation_world_sensor[parent], best["axis_parent"])[:, 2]
        for ep in episodes
    ])
    vertical_child = np.concatenate([
        np.einsum("nij,j->ni", ep.rotation_world_sensor[child], best["axis_child"])[:, 2]
        for ep in episodes
    ])
    child_axis = best["axis_child"].copy()
    sign_flipped = float(np.median(vertical_parent)) * float(np.median(vertical_child)) < 0.0
    if sign_flipped:
        child_axis *= -1.0
    parent_axis = best["axis_parent"]
    start_axes = np.asarray([row["axis_parent"] for row in candidates])
    axis_spread = np.degrees(np.arccos(np.clip(np.abs(start_axes @ parent_axis), 0.0, 1.0)))
    input_degeneracy["qmt_selected_distribution_by_start"] = [{
        "start": row["start"],
        "final_cost": row["final_cost"],
        "runtime_warnings": row["runtime_warnings"],
        "qmt_selected_gyro_preselection_600_indices": row[
            "qmt_selected_gyro_preselection_600_indices"
        ],
        "qmt_selected_accelerometer_preselection_600_indices": row[
            "qmt_selected_accelerometer_preselection_600_indices"
        ],
        "qmt_selected_gyro_source_rows": row["qmt_selected_gyro_source_rows"],
        "qmt_selected_accelerometer_source_rows": row[
            "qmt_selected_accelerometer_source_rows"
        ],
        "selected_denominator_audit": row["qmt_selected_denominator_audit"],
        "action_phase_excitation_contribution": row[
            "qmt_selected_action_phase_excitation_contribution"
        ],
    } for row in candidates]
    input_degeneracy["selected_distribution_multistart_gate"] = {}
    for role in ("parent", "child"):
        material_by_start = [
            bool(row["qmt_selected_denominator_audit"][role]["distribution"][
                "material_selected_degeneracy"
            ])
            for row in candidates
        ]
        all_starts_material = bool(material_by_start and all(material_by_start))
        input_degeneracy["selected_distribution_multistart_gate"][role] = {
            "material_by_start": material_by_start,
            "all_multistarts_material": all_starts_material,
            "isolated_or_single_start_material_can_gate": False,
        }
        if all_starts_material:
            input_degeneracy["named_numerical_physical_conflicts"].append(
                f"{edge}:QMT_{role.upper()}_MATERIAL_SELECTED_NOISE_STANDARDIZED_DEGENERACY"
            )
    input_degeneracy["predeclared_relevant_actions"] = [
        episode.action for episode in episodes
    ]
    input_degeneracy["multistart_parent_axis_max_spread_deg"] = float(np.max(axis_spread))
    input_degeneracy["multistart_final_costs"] = [
        float(row["final_cost"]) for row in candidates
    ]
    input_degeneracy["historical_preselection_exact_parallel_attribution"] = (
        "INVALID_POST_HOC_DESCRIPTIVE_ONLY"
    )
    return parent_axis, child_axis, {
        "schema": "biospur-pure-imu-v0-qmt-olsson-hinge-axis-v1",
        "edge": edge,
        "upstream_function": "qmt.jointAxisEstHingeOlsson",
        "qmt_version": "0.2.4",
        "input_fields": ["raw_accelerometer_mps2", "raw_gyroscope_rad_s"],
        "sample_count": int(len(acc_parent)),
        "qmt_input_rows_before_official_selection": int(len(acc_parent)),
        "qmt_official_sample_selection": {
            "enabled": use_sample_selection,
            "selected_data_size": selected_data_size,
            "selection_window_size": selection_window,
            "angular_rate_energy_threshold_rad2_s2": energy_threshold,
            "episode_deleted_or_downweighted_outside_qmt_axis_estimation": False,
        },
        "maximum_gauss_newton_iterations": maximum_iterations,
        "input_degeneracy": {
            **input_degeneracy,
            "named_numerical_physical_conflicts": sorted(set(
                input_degeneracy["named_numerical_physical_conflicts"]
            )),
        },
        "predeclared_relevant_actions": [episode.action for episode in episodes],
        "multistart": [
            {
                "start": row["start"],
                "final_cost": row["final_cost"],
                "iterations": row["iterations"],
                "upstream_stdout_line_count": row["upstream_stdout_line_count"],
                "upstream_stdout_tail": row["upstream_stdout_tail"],
                "axis_parent": row["axis_parent"].tolist(),
                "axis_child": row["axis_child"].tolist(),
                "runtime_warnings": row["runtime_warnings"],
                "qmt_selected_gyro_rows": row["qmt_selected_gyro_rows"],
                "qmt_selected_accelerometer_rows": row["qmt_selected_accelerometer_rows"],
                "qmt_selected_gyro_preselection_600_indices": row[
                    "qmt_selected_gyro_preselection_600_indices"
                ],
                "qmt_selected_accelerometer_preselection_600_indices": row[
                    "qmt_selected_accelerometer_preselection_600_indices"
                ],
                "qmt_selected_gyro_source_rows": row["qmt_selected_gyro_source_rows"],
                "qmt_selected_accelerometer_source_rows": row[
                    "qmt_selected_accelerometer_source_rows"
                ],
                "qmt_selected_denominator_audit": row[
                    "qmt_selected_denominator_audit"
                ],
                "qmt_selected_action_phase_excitation_contribution": row[
                    "qmt_selected_action_phase_excitation_contribution"
                ],
            }
            for row in candidates
        ],
        "selected_start": int(best["start"]),
        "parent_axis_sensor": parent_axis.tolist(),
        "child_axis_sensor": child_axis.tolist(),
        "child_sign_flipped": bool(sign_flipped),
        "multistart_parent_axis_max_spread_deg": float(np.max(axis_spread)),
        "vendor_orientation_or_pose_truth_used": False,
    }


def _lag1_effective_rows(values: np.ndarray, maximum_rho: float) -> tuple[float, float]:
    values = np.asarray(values, dtype=float).reshape(-1)
    if len(values) < 3:
        return float(max(1, len(values))), 0.0
    centered = values - np.mean(values)
    denominator = float(centered @ centered)
    rho = (
        float(centered[:-1] @ centered[1:] / denominator)
        if denominator > np.finfo(float).eps else float(maximum_rho)
    )
    rho = float(np.clip(rho, 0.0, maximum_rho))
    effective = float(np.clip(
        len(values) * (1.0 - rho) / (1.0 + rho), 1.0, len(values),
    ))
    return effective, rho


def qmt_edge_heading(
    edge: str,
    episodes: Sequence[Raw6Episode],
    axis_parent: np.ndarray,
    axis_child: np.ndarray,
    *, intended_actions: Iterable[str] | None = None,
    axis_multistart_spread_deg: float | None = None,
    noise_significance_contract: Mapping[str, Any] | None = None,
    bounded_call: Callable[[str, Callable[[], Any]], Any] | None = None,
) -> dict[str, Any]:
    """Run official qmt headingCorrection after axis-frame normalization."""

    parent, child, _ = EDGE_BY_NAME[edge]
    parent_from_joint = _joint_frame(axis_parent)
    child_from_joint = _joint_frame(axis_child)
    intended = set(intended_actions) if intended_actions is not None else {
        episode.action for episode in episodes
    }
    if noise_significance_contract is not None:
        parent_noise_cov, parent_noise_source = _segment_gyro_noise_covariance(
            episodes, parent, context=f"{edge}:heading",
        )
        child_noise_cov, child_noise_source = _segment_gyro_noise_covariance(
            episodes, child, context=f"{edge}:heading",
        )
        parent_axis_variance = float(
            np.asarray(axis_parent) @ parent_noise_cov @ np.asarray(axis_parent)
        )
        child_axis_variance = float(
            np.asarray(axis_child) @ child_noise_cov @ np.asarray(axis_child)
        )
        relative_axis_rate_sigma = float(np.sqrt(
            parent_axis_variance + child_axis_variance
        ))
        if relative_axis_rate_sigma <= 0.0 or not np.isfinite(relative_axis_rate_sigma):
            raise ValueError(f"{edge}: invalid propagated relative-axis gyro noise")
    else:
        parent_noise_cov = child_noise_cov = None
        parent_noise_source = child_noise_source = "LEGACY_UNSTANDARDIZED_PATH"
        relative_axis_rate_sigma = None
    records = []
    for episode in episodes:
        n = len(episode.time_ns)
        if n < 220:
            continue
        parent_rotation = np.einsum(
            "nij,jk->nik", episode.rotation_world_sensor[parent], parent_from_joint,
        )
        child_rotation = np.einsum(
            "nij,jk->nik", episode.rotation_world_sensor[child], child_from_joint,
        )
        parent_gyro = np.einsum(
            "ji,nj->ni", parent_from_joint, episode.gyro[parent],
        )
        child_gyro = np.einsum(
            "ji,nj->ni", child_from_joint, episode.gyro[child],
        )
        t = np.arange(n, dtype=float) / 50.0
        try:
            def run_heading_correction():
                return qmt.headingCorrection(
                    parent_gyro,
                    child_gyro,
                    _rotation_to_quat_wxyz(parent_rotation),
                    _rotation_to_quat_wxyz(child_rotation),
                    t,
                    np.array([0.0, 0.0, 1.0]),
                    {},
                    estSettings={
                        "windowTime": min(4.0, max(2.0, (n / 50.0) / 3.0)),
                        "estimationRate": 1.0,
                        "dataRate": 10.0,
                        "tauDelta": 2.0,
                        "tauBias": 5.0,
                        "ratingMin": 0.25,
                        "constraint": "proj",
                        "enableStillness": True,
                    },
                )

            quat2_corrected, delta, filtered, rating, state = (
                bounded_call(
                    f"{edge}:{episode.action}:qmt_heading_correction",
                    run_heading_correction,
                )
                if bounded_call is not None else run_heading_correction()
            )
            delta = np.asarray(delta, float).ravel()
            filtered = np.asarray(filtered, float).ravel()
            rating = np.clip(np.asarray(rating, float).ravel(), 0.0, 1.0)
            state = np.asarray(state, int).ravel()
            corrected_child_joint = _quat_wxyz_to_rotation(
                np.asarray(quat2_corrected, dtype=float)
            )
            corrected_child_sensor = np.einsum(
                "nij,jk->nik", corrected_child_joint, child_from_joint.T,
            )
            motion_rows = (
                (episode.phase != "VERIFIED_PRE_REST")
                & (episode.phase != "VERIFIED_POST_REST")
            )
            if not np.any(motion_rows):
                motion_rows = np.ones(n, dtype=bool)
            parent_axis_rate = parent_gyro[motion_rows, 2]
            child_axis_rate = child_gyro[motion_rows, 2]
            relative_axis_rate = parent_axis_rate - child_axis_rate
            relative_axis_abs = np.abs(relative_axis_rate)
            parent_q90 = float(np.quantile(np.abs(parent_axis_rate), 0.90))
            child_q90 = float(np.quantile(np.abs(child_axis_rate), 0.90))
            relative_q90 = float(np.quantile(relative_axis_abs, 0.90))
            if noise_significance_contract is not None:
                relative_significance = relative_axis_abs / relative_axis_rate_sigma
                significance_alpha = float(noise_significance_contract[
                    "relative_axis_rate_two_sided_false_positive_probability"
                ])
                significance_limit = float(normal_distribution.ppf(
                    1.0 - significance_alpha / 2.0
                ))
                declared_significance_limit = float(noise_significance_contract[
                    "minimum_relative_axis_rate_mahalanobis_q90"
                ])
                if not np.isclose(
                    significance_limit, declared_significance_limit,
                    rtol=0.0, atol=1e-12,
                ):
                    raise ValueError(
                        f"{edge}: relative-axis threshold does not match "
                        "preregistered Gaussian level"
                    )
                active = relative_significance >= significance_limit
                active_axis_rows = int(np.count_nonzero(active))
                active_fraction = float(np.mean(active))
                significance_q90 = float(np.quantile(relative_significance, 0.90))
                effective_motion_rows, motion_lag1 = _lag1_effective_rows(
                    relative_axis_rate,
                    float(noise_significance_contract["maximum_lag1_correlation"]),
                )
            else:
                relative_significance = None
                significance_limit = None
                active_axis_rows = int(np.count_nonzero(
                    relative_axis_abs >= QMT_ACTIVE_RELATIVE_AXIS_RATE_MIN_RAD_S
                ))
                active_fraction = active_axis_rows / float(len(relative_axis_abs))
                significance_q90 = None
                effective_motion_rows, motion_lag1 = float(len(relative_axis_rate)), 0.0
            informative = (
                np.isfinite(filtered)
                & np.isfinite(rating)
                & (rating >= QMT_RATING_MIN)
                & (episode.phase != "VERIFIED_PRE_REST")
                & (episode.phase != "VERIFIED_POST_REST")
            )
            informative_fallback = np.count_nonzero(informative) < 5
            if informative_fallback:
                informative = np.isfinite(filtered) & np.isfinite(rating)
            estimate = circular_mean(filtered[informative], rating[informative])
            informative_rows = int(np.count_nonzero(informative))
            effective_informative_rows, informative_lag1 = _lag1_effective_rows(
                filtered[informative],
                (
                    float(noise_significance_contract["maximum_lag1_correlation"])
                    if noise_significance_contract is not None else 0.98
                ),
            )
            rating_median = float(np.median(rating[informative]))
            mapped = episode.action in intended
            training_candidate = mapped and episode.partition == "IDENTIFICATION_TRAIN"
            heldout_candidate = mapped and episode.partition == "HELD_OUT_VALIDATION"
            if noise_significance_contract is not None:
                signal_gates = {
                    "qmt_official_rating_median": rating_median >= float(
                        noise_significance_contract["qmt_official_rating_minimum"]
                    ),
                    "correlation_reduced_informative_rows": (
                        effective_informative_rows >= float(
                            noise_significance_contract[
                                "minimum_effective_informative_rows"
                            ]
                        )
                    ),
                    "no_low_information_fallback": not informative_fallback,
                    "relative_axis_rate_noise_significance_q90": (
                        significance_q90 >= significance_limit
                    ),
                    "relative_axis_active_motion_fraction": (
                        active_fraction >= float(noise_significance_contract[
                            "minimum_active_motion_fraction"
                        ])
                    ),
                    "parent_child_axis_rate_streams_finite": bool(
                        np.isfinite(parent_axis_rate).all()
                        and np.isfinite(child_axis_rate).all()
                    ),
                }
            else:
                signal_gates = {
                    "qmt_rating_median_ge_0p25": rating_median >= QMT_RATING_MIN,
                    "informative_rows_ge_25": informative_rows >= QMT_MIN_INFORMATIVE_ROWS,
                    "no_low_information_fallback": not informative_fallback,
                    "relative_axis_rate_q90_ge_15deg_s": (
                        relative_q90 >= QMT_RELATIVE_AXIS_RATE_Q90_MIN_RAD_S
                    ),
                    "relative_axis_active_rows_ge_25_at_10deg_s": (
                        active_axis_rows >= QMT_MIN_ACTIVE_AXIS_ROWS
                    ),
                    "parent_child_axis_rate_streams_finite": bool(
                        np.isfinite(parent_axis_rate).all()
                        and np.isfinite(child_axis_rate).all()
                    ),
                }
            signal_qualified = all(signal_gates.values())
            records.append({
                "action": episode.action,
                "partition": episode.partition,
                "heading_rad": estimate,
                "heading_deg": float(np.degrees(estimate)),
                "rating_median": rating_median,
                "rating_q10": float(np.quantile(rating[informative], 0.10)),
                "informative_rows": informative_rows,
                "effective_informative_rows": effective_informative_rows,
                "delta_raw_spread_deg": circular_spread_deg(delta[informative]),
                "delta_filtered_spread_deg": circular_spread_deg(filtered[informative]),
                "startup_rows": int(np.count_nonzero(state == 2)),
                "stillness_rows": int(np.count_nonzero(state == 3)),
                "predeclared_edge_factor_relevant": mapped,
                "qualification_training_candidate": training_candidate,
                "held_out_comparison_candidate": heldout_candidate,
                "signal_excitation": {
                    "parent_axis_rate_q90_deg_s": float(np.degrees(parent_q90)),
                    "child_axis_rate_q90_deg_s": float(np.degrees(child_q90)),
                    "relative_axis_rate_q90_deg_s": float(np.degrees(relative_q90)),
                    "relative_axis_active_rows_ge_10deg_s": active_axis_rows,
                    "motion_rows": int(np.count_nonzero(motion_rows)),
                    "relative_axis_rate_noise_sigma_rad_s": relative_axis_rate_sigma,
                    "relative_axis_rate_two_sided_false_positive_probability": (
                        significance_alpha
                        if noise_significance_contract is not None else None
                    ),
                    "relative_axis_rate_significance_q90": significance_q90,
                    "noise_significance_active_rows": active_axis_rows,
                    "noise_significance_active_fraction": active_fraction,
                    "effective_motion_rows": effective_motion_rows,
                    "motion_lag1_correlation": motion_lag1,
                    "informative_lag1_correlation": informative_lag1,
                    "parent_gyro_noise_covariance_rad2_s2": (
                        parent_noise_cov.tolist() if parent_noise_cov is not None else None
                    ),
                    "child_gyro_noise_covariance_rad2_s2": (
                        child_noise_cov.tolist() if child_noise_cov is not None else None
                    ),
                    "noise_covariance_sources": {
                        "parent": parent_noise_source,
                        "child": child_noise_source,
                    },
                    "raw_rate_threshold_is_gate": noise_significance_contract is None,
                },
                "signal_qualification_gates": signal_gates,
                "signal_qualified": signal_qualified,
                "informative_fallback_used": informative_fallback,
                "_orientation_trajectory": {
                    "delta_filtered_rad": filtered,
                    "rating": rating,
                    "state": state,
                    "corrected_child_rotation_world_sensor": corrected_child_sensor,
                },
            })
        except (AssertionError, ValueError, IndexError, FloatingPointError) as exc:
            records.append({
                "action": episode.action,
                "partition": episode.partition,
                "error": f"{type(exc).__name__}:{exc}",
            })
    valid = [row for row in records if "heading_rad" in row]
    valid_intended = [
        row for row in valid if row["predeclared_edge_factor_relevant"]
    ]
    relevant_train = [
        row for row in valid
        if row["qualification_training_candidate"] and row["signal_qualified"]
    ]
    relevant_heldout = [
        row for row in valid
        if row["held_out_comparison_candidate"] and row["signal_qualified"]
    ]
    estimate_value = circular_mean(
        [row["heading_rad"] for row in relevant_train],
        [
            max(0.05, row["rating_median"]) * row["effective_informative_rows"]
            for row in relevant_train
        ],
    )
    estimate = float(estimate_value) if np.isfinite(estimate_value) else None
    relevant_spread = circular_spread_deg([
        row["heading_rad"] for row in relevant_train
    ])
    heldout_estimate_value = circular_mean(
        [row["heading_rad"] for row in relevant_heldout],
        [
            max(0.05, row["rating_median"]) * row["effective_informative_rows"]
            for row in relevant_heldout
        ],
    )
    heldout_estimate = (
        float(heldout_estimate_value) if np.isfinite(heldout_estimate_value) else None
    )
    intended_train = [
        row for row in valid if row["qualification_training_candidate"]
    ]
    axis_spread_ok = (
        axis_multistart_spread_deg is not None
        and axis_multistart_spread_deg <= QMT_AXIS_MULTISTART_MAX_SPREAD_DEG
    )
    qualification_gates = {
        "predeclared_relevant_training_window_exists": bool(intended_train),
        "signal_qualified_relevant_training_window_exists": bool(relevant_train),
        "pooled_correlation_reduced_informative_support": (
            sum(row["effective_informative_rows"] for row in relevant_train)
            >= (
                float(noise_significance_contract["minimum_effective_informative_rows"])
                if noise_significance_contract is not None
                else QMT_MIN_INFORMATIVE_ROWS
            )
        ),
        "relevant_window_heading_spread_le_15deg": (
            len(relevant_train) <= 1
            or relevant_spread <= QMT_RELEVANT_WINDOW_HEADING_MAX_SPREAD_DEG
        ),
        "hinge_axis_multistart_spread_le_15deg": axis_spread_ok,
    }
    return {
        "schema": "biospur-pure-imu-v0-qmt-edge-heading-v1",
        "edge": edge,
        "upstream_function": "qmt.headingCorrection",
        "upstream_constraint": "proj",
        "axis_frame_normalization": (
            "sensor-from-joint rotations map independently estimated parent/child "
            "axes to the common +z expected by qmt's 1D projection constraint"
        ),
        "input_origin": "raw acc/gyr -> independent VQF quat6D plus raw gyro",
        "heading_rad": estimate,
        "heading_deg": float(np.degrees(estimate)) if estimate is not None else None,
        "action_estimate_spread_deg": circular_spread_deg([
            row["heading_rad"] for row in valid_intended
        ]),
        "action_estimate_spread_semantics": (
            "PREDECLARED_EDGE_ACTIONS_ONLY_NO_UNRELATED_ACTION_DIAGNOSTIC"
        ),
        "successful_action_count": len(valid),
        "successful_predeclared_action_count": len(valid_intended),
        "time_varying_orientation_trajectory_count": sum(
            "_orientation_trajectory" in row for row in valid
        ),
        "quat2corr_and_deltafilt_discarded": False,
        "pooled_episode_heading_is_summary_seed_only": True,
        "predeclared_relevant_actions": sorted(intended),
        "qualification": {
            "schema": "biospur-pure-imu-v0-qmt-excitation-aware-qualification-v1",
            "selection_semantics": (
                "IMMUTABLE_INTENDED_EDGE_PLUS_QMT_PROJ_HEADING_AND_HINGE_AXIS_FACTORS;"
                "THEN_SIGNAL_ONLY_EXCITATION_GATES;TRAIN_ONLY_POOL"
            ),
            "thresholds": {
                "rating_median_min": QMT_RATING_MIN,
                "noise_standardized_signal_contract": (
                    dict(noise_significance_contract)
                    if noise_significance_contract is not None else None
                ),
                "legacy_minimum_informative_rows": (
                    QMT_MIN_INFORMATIVE_ROWS
                    if noise_significance_contract is None else None
                ),
                "legacy_relative_axis_active_rate_min_deg_s": (
                    10.0 if noise_significance_contract is None else None
                ),
                "legacy_relative_axis_rate_q90_min_deg_s": (
                    15.0 if noise_significance_contract is None else None
                ),
                "legacy_minimum_active_axis_rows": (
                    QMT_MIN_ACTIVE_AXIS_ROWS
                    if noise_significance_contract is None else None
                ),
                "axis_multistart_max_spread_deg": QMT_AXIS_MULTISTART_MAX_SPREAD_DEG,
                "relevant_window_heading_max_spread_deg": (
                    QMT_RELEVANT_WINDOW_HEADING_MAX_SPREAD_DEG
                ),
            },
            "intended_training_candidate_count": len(intended_train),
            "signal_qualified_training_window_count": len(relevant_train),
            "signal_qualified_training_actions": [row["action"] for row in relevant_train],
            "pooled_informative_rows_raw_secondary": int(sum(
                row["informative_rows"] for row in relevant_train
            )),
            "pooled_informative_rows": int(sum(
                row["informative_rows"] for row in relevant_train
            )),
            "pooled_effective_informative_rows": float(sum(
                row["effective_informative_rows"] for row in relevant_train
            )),
            "raw_rate_or_row_count_is_c2_gate": noise_significance_contract is None,
            "pooled_heading_rad": estimate,
            "pooled_heading_deg": (
                float(np.degrees(estimate)) if estimate is not None else None
            ),
            "relevant_window_heading_spread_deg": relevant_spread,
            "single_window_cross_window_spread_claimed": len(relevant_train) > 1,
            "axis_multistart_spread_deg": axis_multistart_spread_deg,
            "gates": qualification_gates,
            "pass": all(qualification_gates.values()),
        },
        "held_out_comparison": {
            "signal_qualified_action_count": len(relevant_heldout),
            "signal_qualified_actions": [row["action"] for row in relevant_heldout],
            "pooled_heading_rad": heldout_estimate,
            "pooled_heading_deg": (
                float(np.degrees(heldout_estimate)) if heldout_estimate is not None else None
            ),
            "train_to_heldout_heading_difference_deg": (
                float(np.degrees(wrap(heldout_estimate - estimate)))
                if heldout_estimate is not None and estimate is not None else None
            ),
            "role": "DIAGNOSTIC_ONLY_NOT_USED_TO_FIT_CAPTURE_HEADING",
        },
        "records": records,
        "absolute_heading_claim": False,
    }


def _selected_rows(
    episode: Raw6Episode, include_transitions: bool, maximum_rows: int | None = 90,
) -> np.ndarray:
    allowed = PHASES if include_transitions else ("FORMAL_ACTION_OR_HOLD",)
    # The bounded payload can contain administrative slack before/after the
    # signal-verified episode.  Those rows are explicitly labelled
    # UNCLASSIFIED_COMPLETE_EPISODE and must never become calibration factors.
    # In particular, ``include_transitions`` means the two verified rests plus
    # the two transitions and formal action -- not the whole raw buffer.
    selected = np.isin(episode.phase, allowed)
    selected[0] = False
    selected[-1] = False
    indices = np.flatnonzero(selected)
    if maximum_rows is not None and len(indices) > int(maximum_rows):
        # Deterministic stratification retains both ends of every present
        # phase.  A single global linspace could otherwise erase a short but
        # crucial rest/transition when the formal action is much longer.
        phase_rows = [
            np.flatnonzero(selected & (episode.phase == phase))
            for phase in allowed
        ]
        phase_rows = [rows for rows in phase_rows if len(rows)]
        minimum = np.asarray([min(2, len(rows)) for rows in phase_rows], dtype=int)
        remaining = int(maximum_rows) - int(np.sum(minimum))
        capacity = np.asarray([
            max(0, len(rows) - base) for rows, base in zip(phase_rows, minimum)
        ], dtype=int)
        quota = minimum.copy()
        if remaining > 0 and int(np.sum(capacity)):
            exact = remaining * capacity / float(np.sum(capacity))
            extra = np.minimum(capacity, np.floor(exact).astype(int))
            quota += extra
            leftover = remaining - int(np.sum(extra))
            order = np.argsort(-(exact - np.floor(exact)))
            for slot in order:
                if leftover <= 0:
                    break
                if quota[slot] < len(phase_rows[slot]):
                    quota[slot] += 1
                    leftover -= 1
        indices = np.unique(np.concatenate([
            rows[np.unique(np.rint(np.linspace(
                0, len(rows) - 1, int(count),
            )).astype(int))]
            for rows, count in zip(phase_rows, quota) if count
        ]))
    return indices


def b5_blocks(
    edge: str,
    episodes: Sequence[Raw6Episode],
    *,
    include_transitions: bool = True,
    maximum_rows: int | None = 90,
) -> tuple[B5Block, ...]:
    parent, child, _ = EDGE_BY_NAME[edge]
    blocks = []

    def noise_covariances(
        episode: Raw6Episode, segment: str,
    ) -> tuple[np.ndarray, np.ndarray, str]:
        nodes = episode.audit.get("nodes", {})
        for row in nodes.values():
            if row.get("segment") == segment and "accelerometer_noise_cov_m2_s4" in row:
                return (
                    np.asarray(row["accelerometer_noise_cov_m2_s4"], dtype=float),
                    np.asarray(row["gyro_noise_cov_rad2_s2"], dtype=float),
                    "CAPTURE_WIDE_INITIAL_STILL",
                )
        if episode.audit.get("generator") == "INDEPENDENT_ANALYTIC_TRUTH":
            acc_sigma = float(episode.audit["noise_mps2"])
            gyro_sigma = float(episode.audit["noise_rad_s"])
            return (
                np.eye(3) * acc_sigma**2,
                np.eye(3) * gyro_sigma**2,
                "INDEPENDENT_SYNTHETIC_SENSOR_NOISE_DECLARATION",
            )
        rest = np.isin(
            episode.phase, ("VERIFIED_PRE_REST", "VERIFIED_POST_REST"),
        )
        if np.count_nonzero(rest) < 12:
            raise ValueError(f"{episode.action}:{segment}: no verified-rest noise estimate")
        acc_diff = np.diff(episode.acc[segment][rest], axis=0)
        gyro_diff = np.diff(episode.gyro[segment][rest], axis=0)
        return (
            np.cov(acc_diff.T, ddof=1) / 2.0,
            np.cov(gyro_diff.T, ddof=1) / 2.0,
            "VERIFIED_REST_FIRST_DIFFERENCE_FALLBACK",
        )

    for episode in episodes:
        rows = _selected_rows(episode, include_transitions, maximum_rows)
        if len(rows) < 6:
            continue
        dt = 1.0 / 50.0
        parent_gyro = episode.gyro[parent]
        child_gyro = episode.gyro[child]
        parent_alpha = np.gradient(parent_gyro, dt, axis=0, edge_order=2)
        child_alpha = np.gradient(child_gyro, dt, axis=0, edge_order=2)
        parent_alpha_skew = _skew_batch(parent_alpha[rows])
        parent_gyro_skew = _skew_batch(parent_gyro[rows])
        child_alpha_skew = _skew_batch(child_alpha[rows])
        child_gyro_skew = _skew_batch(child_gyro[rows])
        kp = parent_alpha_skew + parent_gyro_skew @ parent_gyro_skew
        kc = child_alpha_skew + child_gyro_skew @ child_gyro_skew
        excitation = np.maximum(
            np.linalg.norm(parent_alpha[rows], axis=1)
            + np.linalg.norm(parent_gyro[rows], axis=1) ** 2,
            np.linalg.norm(child_alpha[rows], axis=1)
            + np.linalg.norm(child_gyro[rows], axis=1) ** 2,
        )
        # Rest remains in the complete-episode objective with finite weight;
        # dynamic rows receive more influence but do not erase transitions.
        weight = 0.15 + 0.85 * np.tanh(excitation / 3.0)
        weight /= math.sqrt(len(rows))
        parent_acc_cov, parent_gyro_cov, parent_noise_source = noise_covariances(
            episode, parent,
        )
        child_acc_cov, child_gyro_cov, child_noise_source = noise_covariances(
            episode, child,
        )
        blocks.append(B5Block(
            action=episode.action,
            partition=episode.partition,
            phase=episode.phase[rows],
            parent_rotation=episode.rotation_world_sensor[parent][rows],
            child_rotation=episode.rotation_world_sensor[child][rows],
            parent_force=episode.acc[parent][rows],
            child_force=episode.acc[child][rows],
            parent_kinematic=kp,
            child_kinematic=kc,
            sample_weight=weight,
            sample_time_ns=episode.time_ns[rows],
            parent_gyro=parent_gyro[rows],
            child_gyro=child_gyro[rows],
            parent_acc_noise_cov=parent_acc_cov,
            child_acc_noise_cov=child_acc_cov,
            parent_gyro_noise_cov=parent_gyro_cov,
            child_gyro_noise_cov=child_gyro_cov,
            parent_noise_cov_source=parent_noise_source,
            child_noise_cov_source=child_noise_source,
        ))
    return tuple(blocks)


def _b5_system(
    blocks: Sequence[B5Block],
    delta: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[tuple[int, int, str]]]:
    transform = rz(float(delta))
    matrices = []
    targets = []
    weights = []
    ranges = []
    cursor = 0
    for block in blocks:
        rp = block.parent_rotation
        rc = np.einsum("ij,njk->nik", transform, block.child_rotation)
        ap = np.einsum("nij,njk->nik", rp, block.parent_kinematic)
        ac = np.einsum("nij,njk->nik", rc, block.child_kinematic)
        fp = np.einsum("nij,nj->ni", rp, block.parent_force)
        fc = np.einsum("nij,nj->ni", rc, block.child_force)
        matrix = np.concatenate((ap, -ac), axis=2).reshape(-1, 6)
        target = (fc - fp).reshape(-1)
        weight = np.repeat(block.sample_weight, 3)
        matrices.append(matrix)
        targets.append(target)
        weights.append(weight)
        stop = cursor + len(target)
        ranges.append((cursor, stop, block.action))
        cursor = stop
    if not matrices:
        return np.empty((0, 6)), np.empty(0), np.empty(0), []
    return (
        np.concatenate(matrices),
        np.concatenate(targets),
        np.concatenate(weights),
        ranges,
    )


def profile_b5(
    blocks: Sequence[B5Block],
    delta: float,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    matrix, target, weight, ranges = _b5_system(blocks, delta)
    if not len(target):
        return np.zeros(6), np.empty(0), {
            "rows": 0, "lever_rank": 0, "lever_condition": None,
        }
    weighted_matrix = matrix * weight[:, None]
    weighted_target = target * weight
    lever, _, rank, singular = np.linalg.lstsq(
        weighted_matrix, weighted_target, rcond=1e-8,
    )
    physical = matrix @ lever - target
    residual = weight * physical
    return lever, residual, {
        "rows": int(len(target) // 3),
        "lever_rank": int(rank),
        "lever_singular_values": singular.tolist(),
        "lever_condition": (
            float(singular[0] / singular[-1])
            if len(singular) and singular[-1] > 0 else None
        ),
        "physical_rms_mps2": float(np.sqrt(np.mean(physical * physical))),
        "weighted_rms": float(np.sqrt(np.mean(residual * residual))),
        "lever_parent_m": lever[:3].tolist(),
        "lever_child_m": lever[3:].tolist(),
        "lever_max_abs_m": float(np.max(np.abs(lever))),
        "action_ranges": [
            {"start": start, "stop": stop, "action": action}
            for start, stop, action in ranges
        ],
    }


def _prepare_b5(blocks: Sequence[B5Block]) -> _PreparedB5:
    """Cache all delta-independent joint-center terms for graph profiling."""

    ap_rows = []
    ac_rows = []
    fp_rows = []
    fc_rows = []
    weights = []
    ranges = []
    cursor = 0
    for block in blocks:
        ap = np.einsum(
            "nij,njk->nik", block.parent_rotation, block.parent_kinematic,
        )
        ac = np.einsum(
            "nij,njk->nik", block.child_rotation, block.child_kinematic,
        )
        fp = np.einsum(
            "nij,nj->ni", block.parent_rotation, block.parent_force,
        )
        fc = np.einsum(
            "nij,nj->ni", block.child_rotation, block.child_force,
        )
        ap_rows.append(ap)
        ac_rows.append(ac)
        fp_rows.append(fp)
        fc_rows.append(fc)
        weights.append(block.sample_weight)
        stop = cursor + 3 * len(ap)
        ranges.append((cursor, stop, block.action))
        cursor = stop
    if not ap_rows:
        return _PreparedB5(
            np.empty((0, 3, 3)), np.empty((0, 3, 3)),
            np.empty((0, 3)), np.empty((0, 3)), np.empty(0), tuple(),
        )
    return _PreparedB5(
        np.concatenate(ap_rows),
        np.concatenate(ac_rows),
        np.concatenate(fp_rows),
        np.concatenate(fc_rows),
        np.concatenate(weights),
        tuple(ranges),
    )


def _prepared_b5_system(
    prepared: _PreparedB5,
    delta: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    transform = rz(float(delta))
    child_kinematic = np.einsum(
        "ij,njk->nik", transform, prepared.child_kinematic_base,
    )
    child_force = np.einsum(
        "ij,nj->ni", transform, prepared.child_force_base,
    )
    matrix = np.concatenate((
        prepared.parent_kinematic_world, -child_kinematic,
    ), axis=2).reshape(-1, 6)
    target = (child_force - prepared.parent_force_world).reshape(-1)
    weight = np.repeat(prepared.sample_weight, 3)
    return matrix, target, weight


def _profile_prepared_b5(
    prepared: _PreparedB5,
    delta: float,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    matrix, target, weight = _prepared_b5_system(prepared, delta)
    if not len(target):
        return np.zeros(6), np.empty(0), {
            "rows": 0, "lever_rank": 0, "lever_condition": None,
        }
    weighted_matrix = matrix * weight[:, None]
    weighted_target = target * weight
    lever, _, rank, singular = np.linalg.lstsq(
        weighted_matrix, weighted_target, rcond=1e-8,
    )
    physical = matrix @ lever - target
    residual = weight * physical
    return lever, residual, {
        "rows": int(len(target) // 3),
        "lever_rank": int(rank),
        "lever_singular_values": singular.tolist(),
        "lever_condition": (
            float(singular[0] / singular[-1])
            if len(singular) and singular[-1] > 0 else None
        ),
        "physical_rms_mps2": float(np.sqrt(np.mean(physical * physical))),
        "weighted_rms": float(np.sqrt(np.mean(residual * residual))),
        "lever_parent_m": lever[:3].tolist(),
        "lever_child_m": lever[3:].tolist(),
        "lever_max_abs_m": float(np.max(np.abs(lever))),
        "action_ranges": [
            {"start": start, "stop": stop, "action": action}
            for start, stop, action in prepared.action_ranges
        ],
    }


def _b5_cost(blocks: Sequence[B5Block], delta: float) -> float:
    _, residual, _ = profile_b5(blocks, float(wrap(delta)))
    return float(np.mean(residual * residual)) if len(residual) else float("inf")


def fit_b5_edge(blocks: Sequence[B5Block]) -> dict[str, Any]:
    grid = np.linspace(-np.pi, np.pi, 73)[:-1]
    costs = np.asarray([_b5_cost(blocks, value) for value in grid])
    best_grid = float(grid[int(np.argmin(costs))])
    step = 2.0 * np.pi / len(grid)
    refined = minimize_scalar(
        lambda value: _b5_cost(blocks, value),
        bounds=(best_grid - step, best_grid + step),
        method="bounded",
        options={"xatol": 1e-8},
    )
    estimate = float(wrap(refined.x))
    lever, residual, profile = profile_b5(blocks, estimate)
    starts = np.linspace(-np.pi, np.pi, 9)[:-1]
    multistart = []
    for seed in starts:
        result = least_squares(
            lambda value: profile_b5(blocks, float(wrap(value[0])))[1],
            np.array([seed]),
            max_nfev=80,
            xtol=1e-10,
            ftol=1e-10,
            gtol=1e-10,
            loss="soft_l1",
            f_scale=0.25,
        )
        multistart.append(float(wrap(result.x[0])))
    eps = 1e-4
    _, base_residual, _ = profile_b5(blocks, estimate)
    _, plus_residual, _ = profile_b5(blocks, estimate + eps)
    _, minus_residual, _ = profile_b5(blocks, estimate - eps)
    profile_derivative = (plus_residual - minus_residual) / (2.0 * eps)
    curvature = float(profile_derivative @ profile_derivative)
    matrix, target, weight, _ = _b5_system(blocks, estimate)
    weighted_matrix = matrix * weight[:, None]
    # Measurement-only heading Jacobian with lever arms fixed, then projected
    # off the measured lever-arm column space.
    def fixed_lever_residual(value: float) -> np.ndarray:
        m, y, w, _ = _b5_system(blocks, value)
        return w * (m @ lever - y)
    raw_heading_jac = (
        fixed_lever_residual(estimate + eps)
        - fixed_lever_residual(estimate - eps)
    ) / (2.0 * eps)
    if len(weighted_matrix):
        q, _ = np.linalg.qr(weighted_matrix, mode="reduced")
        projected = raw_heading_jac - q @ (q.T @ raw_heading_jac)
    else:
        projected = raw_heading_jac
    full_jacobian = np.column_stack((raw_heading_jac, weighted_matrix))
    singular = np.linalg.svd(full_jacobian, compute_uv=False) if len(full_jacobian) else np.empty(0)
    cutoff = max(1e-8, float(singular[0]) * 1e-7) if len(singular) else 1e-8
    rank = int(np.sum(singular > cutoff))
    return {
        "schema": "biospur-pure-imu-v0-b5-edge-profile-v1",
        "heading_rad": estimate,
        "heading_deg": float(np.degrees(estimate)),
        "profile_cost": float(np.mean(residual * residual)),
        "profile_curvature": curvature,
        "measurement_full_rank": rank,
        "measurement_full_dimension": 7,
        "measurement_singular_values": singular.tolist(),
        "projected_heading_information": float(projected @ projected),
        "projected_to_raw_heading_information_ratio": float(
            (projected @ projected)
            / max(float(raw_heading_jac @ raw_heading_jac), np.finfo(float).eps)
        ),
        "multistart_headings_deg": np.degrees(multistart).tolist(),
        "multistart_max_spread_deg": circular_spread_deg(multistart),
        "grid_second_to_first_cost_ratio": float(
            np.partition(costs, 1)[1] / max(np.min(costs), np.finfo(float).eps)
        ),
        "lever_profile": profile,
        "bounds_or_priors_used_for_rank": False,
    }


def evaluate_b5(
    blocks: Sequence[B5Block],
    delta: float,
    lever: np.ndarray,
) -> dict[str, Any]:
    matrix, target, _, ranges = _b5_system(blocks, delta)
    if not len(target):
        return {"rows": 0, "physical_rms_mps2": None, "per_action": {}}
    physical = matrix @ np.asarray(lever, float) - target
    per_action = {}
    for start, stop, action in ranges:
        values = physical[start:stop]
        per_action[action] = {
            "scalar_rows": int(len(values)),
            "physical_rms_mps2": float(np.sqrt(np.mean(values * values))),
        }
    return {
        "rows": int(len(target) // 3),
        "physical_rms_mps2": float(np.sqrt(np.mean(physical * physical))),
        "per_action": per_action,
    }


def build_edge_factors(
    episodes: Sequence[Raw6Episode],
    *,
    include_transitions: bool = True,
    qmt_intended_actions: Mapping[str, Iterable[str]] | None = None,
) -> tuple[dict[str, EdgeFactors], dict[str, Any]]:
    train = [ep for ep in episodes if ep.partition == "IDENTIFICATION_TRAIN"]
    held = [ep for ep in episodes if ep.partition == "HELD_OUT_VALIDATION"]
    factors = {}
    audit = {}
    for edge, parent, child, kind in EDGES:
        axis_parent = axis_child = None
        axis_report = qmt_report = None
        if edge in HINGE_EDGES:
            intended = set(
                (qmt_intended_actions or {}).get(
                    edge, [episode.action for episode in train],
                )
            )
            relevant_train = [episode for episode in train if episode.action in intended]
            if not relevant_train:
                raise RuntimeError(
                    f"{edge}: immutable factor mapping has no QMT training episode"
                )
            axis_parent, axis_child, axis_report = estimate_hinge_axes_qmt(
                edge, relevant_train,
            )
            qmt_report = qmt_edge_heading(
                edge, episodes, axis_parent, axis_child,
                intended_actions=intended,
                axis_multistart_spread_deg=axis_report[
                    "multistart_parent_axis_max_spread_deg"
                ],
            )
        train_blocks = b5_blocks(
            edge, train, include_transitions=include_transitions,
        )
        held_blocks = b5_blocks(
            edge, held, include_transitions=include_transitions,
        )
        factors[edge] = EdgeFactors(
            edge,
            parent,
            child,
            kind,
            train_blocks,
            held_blocks,
            axis_parent,
            axis_child,
            qmt_report,
            axis_report,
        )
        audit[edge] = {
            "parent": parent,
            "child": child,
            "kind": kind,
            "train_b5_action_count": len(train_blocks),
            "held_out_b5_action_count": len(held_blocks),
            "qmt_hinge_axis_executed": axis_report is not None,
            "qmt_heading_executed": qmt_report is not None,
            "qmt_predeclared_relevant_actions": (
                sorted((qmt_intended_actions or {}).get(edge, []))
                if edge in HINGE_EDGES else []
            ),
            "qmt_excitation_aware_qualification_pass": (
                qmt_report["qualification"]["pass"]
                if qmt_report is not None else None
            ),
        }
    return factors, audit


def _axis_residual(factor: EdgeFactors, delta: float) -> np.ndarray:
    if factor.hinge_axis_parent is None or factor.hinge_axis_child is None:
        return np.empty(0)
    values = []
    transform = rz(delta)
    for block in factor.b5_train:
        parent_axis = np.einsum(
            "nij,j->ni", block.parent_rotation, factor.hinge_axis_parent,
        )
        child_axis = np.einsum(
            "ij,njk,k->ni", transform, block.child_rotation, factor.hinge_axis_child,
        )
        values.append(
            (parent_axis - child_axis).ravel() / 0.15 / math.sqrt(len(parent_axis))
        )
    return np.concatenate(values) if values else np.empty(0)


def _rom_residual(factor: EdgeFactors, delta: float) -> np.ndarray:
    limit = math.radians(ROM_LIMIT_DEG[factor.name])
    transform = rz(delta)
    values = []
    for block in factor.b5_train:
        relative = np.einsum(
            "nji,njk->nik",
            block.parent_rotation,
            np.einsum("ij,njk->nik", transform, block.child_rotation),
        )
        # A capture-shared first-row connection frame is a nuisance display
        # zero, not pose truth.  The trace form avoids an iterative SO(3) mean
        # inside every optimizer evaluation.
        neutral = relative[0]
        delta_rotation = np.einsum("ji,njk->nik", neutral, relative[::6])
        cosine = np.clip(
            (np.trace(delta_rotation, axis1=1, axis2=2) - 1.0) / 2.0,
            -1.0,
            1.0,
        )
        excursion = np.arccos(cosine)
        excess = np.maximum(0.0, excursion - limit)
        # Keep residual dimension fixed for every candidate, including when
        # all ROM inequalities are inactive.
        values.append(excess / math.radians(20.0) / math.sqrt(len(excess)))
    return np.concatenate(values) if values else np.empty(0)


def _edge_residual(factor: EdgeFactors, delta: float) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    lever, b5, profile = profile_b5(factor.b5_train, delta)
    pieces = [b5 / 0.35]
    axis = _axis_residual(factor, delta)
    if len(axis):
        pieces.append(axis)
    rom = _rom_residual(factor, delta)
    if len(rom):
        pieces.append(rom)
    return lever, np.concatenate(pieces), {
        "b5_scalar_rows": int(len(b5)),
        "axis_scalar_rows": int(len(axis)),
        # Equality of two unit axes has two independent tangent-plane
        # components.  Keeping the three chordal coordinates is numerically
        # convenient but does not manufacture a third constraint.
        "two_dof_axis_constraint_effective_dimension_per_sample": (
            2 if len(axis) else 0
        ),
        "rom_scalar_rows": int(len(rom)),
        "b5_profile": profile,
    }


def _edge_residual_prepared(
    factor: EdgeFactors,
    prepared: _PreparedB5,
    delta: float,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    lever, b5, profile = _profile_prepared_b5(prepared, delta)
    pieces = [b5 / 0.35]
    axis = _axis_residual(factor, delta)
    if len(axis):
        pieces.append(axis)
    rom = _rom_residual(factor, delta)
    if len(rom):
        pieces.append(rom)
    return lever, np.concatenate(pieces), {
        "b5_scalar_rows": int(len(b5)),
        "axis_scalar_rows": int(len(axis)),
        "two_dof_axis_constraint_effective_dimension_per_sample": (
            2 if len(axis) else 0
        ),
        "rom_scalar_rows": int(len(rom)),
        "b5_profile": profile,
        "delta_independent_b5_terms_cached": True,
    }


def headings_to_edges(headings: np.ndarray) -> dict[str, float]:
    node = {"pelvis": 0.0}
    node.update({segment: float(headings[index]) for index, segment in enumerate(SEGMENTS[1:])})
    return {
        edge: float(wrap(node[child] - node[parent]))
        for edge, parent, child, _ in EDGES
    }


def edges_to_headings(edge_delta: Mapping[str, float]) -> np.ndarray:
    node = {"pelvis": 0.0}
    remaining = list(EDGES)
    while remaining:
        progressed = False
        for row in remaining[:]:
            edge, parent, child, _ = row
            if parent in node:
                node[child] = float(wrap(node[parent] + edge_delta[edge]))
                remaining.remove(row)
                progressed = True
        if not progressed:
            raise RuntimeError("body graph is disconnected")
    return np.asarray([node[segment] for segment in SEGMENTS[1:]], float)


def fit_edgewise(factors: Mapping[str, EdgeFactors]) -> dict[str, Any]:
    rows = {}
    edge_delta = {}
    for edge, factor in factors.items():
        b5 = fit_b5_edge(factor.b5_train)
        qmt_heading = (
            factor.qmt_report.get("heading_rad")
            if factor.qmt_report is not None else None
        )
        baseline = (
            float(qmt_heading)
            if qmt_heading is not None and np.isfinite(qmt_heading)
            else float(b5["heading_rad"])
        )
        edge_delta[edge] = baseline
        lever, _, _ = profile_b5(factor.b5_train, baseline)
        rows[edge] = {
            "parent": factor.parent,
            "child": factor.child,
            "joint_kind": factor.kind,
            "baseline_source": (
                "QMT_HEADING_CORRECTION_PROJ"
                if qmt_heading is not None and np.isfinite(qmt_heading)
                else "B5_PROFILED_JOINT_CENTER"
            ),
            "baseline_heading_rad": baseline,
            "baseline_heading_deg": float(np.degrees(baseline)),
            "qmt": factor.qmt_report,
            "qmt_axis": factor.axis_report,
            "b5_independent": b5,
            "b5_at_baseline_train": evaluate_b5(factor.b5_train, baseline, lever),
            "b5_at_baseline_held_out": evaluate_b5(factor.b5_held_out, baseline, lever),
        }
    headings = edges_to_headings(edge_delta)
    return {
        "schema": "biospur-pure-imu-v0-edgewise-baseline-v1",
        "root_heading": {"pelvis": 0.0, "role": "SOLE_GLOBAL_YAW_GAUGE"},
        "edges": rows,
        "accumulated_headings_rad": {
            segment: (0.0 if segment == "pelvis" else float(headings[SEGMENTS[1:].index(segment)]))
            for segment in SEGMENTS
        },
        "accumulated_headings_deg": {
            segment: (
                0.0 if segment == "pelvis"
                else float(np.degrees(headings[SEGMENTS[1:].index(segment)]))
            )
            for segment in SEGMENTS
        },
        "old_profile_or_shared_ik_used": False,
    }


def fit_unified_graph(
    factors: Mapping[str, EdgeFactors],
    initial_headings: np.ndarray,
    *,
    starts: int = 7,
    seed: int = 20260828,
) -> dict[str, Any]:
    """Fit one capture-wide objective over the nine pelvis-gauged headings."""

    ordered = [factors[name] for name, _, _, _ in EDGES]
    prepared = {
        factor.name: _prepare_b5(factor.b5_train) for factor in ordered
    }

    def residual(x: np.ndarray) -> np.ndarray:
        deltas = headings_to_edges(wrap(x))
        return np.concatenate([
            _edge_residual_prepared(
                factor, prepared[factor.name], deltas[factor.name],
            )[1]
            for factor in ordered
        ])

    def full_circle_profile(raw_seed: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
        """Globally profile all tree-edge headings before joint refinement.

        The BioSpur body graph is a tree, so its nine pelvis-gauged node
        headings are bijective with its nine edge differences.  Conditional
        nuisance levers are edge-local.  A full 2π search on every edge is
        therefore a principled global profile, not a local replacement for
        broad multistart.  The raw seed only phase-shifts each circular grid.
        """

        seed_edges = headings_to_edges(raw_seed)
        grid_count = 72

        def profile_one(factor: EdgeFactors) -> tuple[str, float, dict[str, Any]]:
            seed_delta = float(seed_edges[factor.name])
            grid = wrap(seed_delta + np.arange(grid_count) * (2.0 * np.pi / grid_count))
            costs = np.asarray([
                float(np.mean(_edge_residual_prepared(
                    factor, prepared[factor.name], value,
                )[1] ** 2))
                for value in grid
            ])
            best_index = int(np.argmin(costs))
            best_grid = float(grid[best_index])
            step = 2.0 * np.pi / grid_count
            refined = minimize_scalar(
                lambda value: float(np.mean(
                    _edge_residual_prepared(
                        factor, prepared[factor.name], float(wrap(value)),
                    )[1] ** 2
                )),
                bounds=(best_grid - step, best_grid + step),
                method="bounded",
                options={"xatol": 1e-8, "maxiter": 120},
            )
            solution = float(wrap(refined.x))
            return factor.name, solution, {
                "raw_seed_edge_heading_deg": float(np.degrees(seed_delta)),
                "grid_count": grid_count,
                "full_circle_coverage_rad": 2.0 * np.pi,
                "grid_phase_rad": seed_delta,
                "best_grid_heading_deg": float(np.degrees(best_grid)),
                "profiled_heading_deg": float(np.degrees(solution)),
                "profiled_mean_square_cost": float(refined.fun),
                "optimizer_success": bool(refined.success),
                "optimizer_evaluations": int(refined.nfev),
            }
        profiled = [profile_one(factor) for factor in ordered]
        edge_solution = {name: value for name, value, _ in profiled}
        report = {name: row for name, _, row in profiled}
        return edges_to_headings(edge_solution), report

    rng = np.random.default_rng(seed)
    raw_seeds = [np.asarray(initial_headings, float)]
    raw_seeds.extend(
        rng.uniform(-np.pi, np.pi, size=9) for _ in range(starts - 1)
    )
    fits = []
    for index, raw_seed in enumerate(raw_seeds):
        x0, profile_report = full_circle_profile(np.asarray(raw_seed, float))
        result = least_squares(
            residual,
            x0,
            bounds=(-np.pi * np.ones(9), np.pi * np.ones(9)),
            loss="soft_l1",
            f_scale=1.0,
            max_nfev=100,
            xtol=1e-9,
            ftol=1e-9,
            gtol=1e-9,
        )
        fits.append({
            "start": index,
            "raw_broad_start": wrap(np.asarray(raw_seed, float)),
            "profiled_full_circle_start": wrap(x0),
            "full_circle_profile": profile_report,
            "x": wrap(result.x),
            "cost": float(result.cost),
            "optimality": float(result.optimality),
            "nfev": int(result.nfev),
            "success": bool(result.success),
            "message": str(result.message),
        })
    best = min(fits, key=lambda row: row["cost"])
    x = np.asarray(best["x"], float)
    base = residual(x)
    eps = 1e-5
    jacobian = np.column_stack([
        (
            residual(wrap(x + eps * np.eye(9)[column]))
            - residual(wrap(x - eps * np.eye(9)[column]))
        ) / (2.0 * eps)
        for column in range(9)
    ])
    singular = np.linalg.svd(jacobian, compute_uv=False)
    cutoff = max(1e-8, float(singular[0]) * 1e-6) if len(singular) else 1e-8
    rank = int(np.sum(singular > cutoff))
    headings_by_start = np.asarray([row["x"] for row in fits])
    spreads = {
        segment: circular_spread_deg(headings_by_start[:, index])
        for index, segment in enumerate(SEGMENTS[1:])
    }
    deltas = headings_to_edges(x)
    edge_rows = {}
    lever_by_edge = {}
    for factor in ordered:
        delta = deltas[factor.name]
        lever, _, accounting = _edge_residual_prepared(
            factor, prepared[factor.name], delta,
        )
        lever_by_edge[factor.name] = lever
        train = evaluate_b5(factor.b5_train, delta, lever)
        held = evaluate_b5(factor.b5_held_out, delta, lever)
        edge_rows[factor.name] = {
            "parent": factor.parent,
            "child": factor.child,
            "relative_heading_rad": delta,
            "relative_heading_deg": float(np.degrees(delta)),
            "lever_parent_m": lever[:3].tolist(),
            "lever_child_m": lever[3:].tolist(),
            "lever_max_abs_m": float(np.max(np.abs(lever))),
            "factor_accounting": accounting,
            "train": train,
            "held_out": held,
        }
    return {
        "schema": "biospur-pure-imu-v0-unified-nine-heading-graph-v1",
        "headings_rad": {
            "pelvis": 0.0,
            **{segment: float(x[index]) for index, segment in enumerate(SEGMENTS[1:])},
        },
        "headings_deg": {
            "pelvis": 0.0,
            **{segment: float(np.degrees(x[index])) for index, segment in enumerate(SEGMENTS[1:])},
        },
        "edges": edge_rows,
        "relative_edge_headings_rad": deltas,
        "root_yaw_gauge_count": 1,
        "root_yaw_gauge_segment": "pelvis",
        "publishable_heading_dimension": 9,
        "numeric_jacobian_shape": list(jacobian.shape),
        "numeric_rank_after_gauge": rank,
        "numeric_nullity_after_gauge": int(9 - rank),
        "numeric_singular_values": singular.tolist(),
        "numeric_rank_threshold": cutoff,
        "condition_number": (
            float(singular[0] / singular[-1])
            if len(singular) and singular[-1] > cutoff else None
        ),
        "multistart": [
            {
                **{
                    key: value
                    for key, value in row.items()
                    if key not in {
                        "x", "raw_broad_start", "profiled_full_circle_start",
                    }
                },
                "raw_broad_start_headings_deg": np.degrees(
                    row["raw_broad_start"]
                ).tolist(),
                "profiled_full_circle_start_headings_deg": np.degrees(
                    row["profiled_full_circle_start"]
                ).tolist(),
                "headings_deg": np.degrees(row["x"]).tolist(),
            }
            for row in fits
        ],
        "multistart_spread_deg": spreads,
        "multistart_max_spread_deg": float(max(spreads.values())),
        "best_cost": float(best["cost"]),
        "multistart_contract": {
            "raw_start_distribution": "INDEPENDENT_UNIFORM_MINUS_PI_TO_PI_IN_ALL_NINE_HEADINGS",
            "first_start_role": "EDGEWISE_BASELINE_CONTROL",
            "broad_independent_start_count": int(max(0, starts - 1)),
            "staging": (
                "TREE_BIJECTION_TO_NINE_EDGE_DIFFERENCES;EACH_EDGE_FULL_2PI_"
                "PROFILE_WITH_PROFILED_B5_LEVERS;JOINT_NINE_HEADING_REFINEMENT"
            ),
            "full_circle_grid_points_per_edge_per_start": 72,
            "all_nine_coordinates_receive_full_circle_coverage_per_start": True,
            "local_basin_only": False,
        },
        "residual_scalar_count": int(len(base)),
        "lever_by_edge": {key: value.tolist() for key, value in lever_by_edge.items()},
        "one_time_resolved_multi_action_objective": True,
        "per_action_recalibration": False,
        "cross_capture_parameters": False,
        "bounds_or_priors_counted_as_rank": False,
    }


def transition_ablation(
    episodes: Sequence[Raw6Episode],
    full_result: Mapping[str, Any],
    *, qmt_intended_actions: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, Any]:
    plateau_factors, _ = build_edge_factors(
        episodes, include_transitions=False,
        qmt_intended_actions=qmt_intended_actions,
    )
    initial = np.asarray([
        full_result["headings_rad"][segment] for segment in SEGMENTS[1:]
    ])
    plateau = fit_unified_graph(
        plateau_factors, initial, starts=3, seed=20260829,
    )
    full_singular = np.asarray(full_result["numeric_singular_values"], float)
    plateau_singular = np.asarray(plateau["numeric_singular_values"], float)
    return {
        "schema": "biospur-pure-imu-v0-transition-removal-ablation-v1",
        "full_complete_episode_rank": int(full_result["numeric_rank_after_gauge"]),
        "formal_action_only_rank": int(plateau["numeric_rank_after_gauge"]),
        "full_smallest_singular": float(full_singular[-1]),
        "formal_action_only_smallest_singular": float(plateau_singular[-1]),
        "smallest_singular_information_retained_ratio": float(
            plateau_singular[-1] / max(full_singular[-1], np.finfo(float).eps)
        ),
        "heading_change_deg": {
            segment: float(np.degrees(wrap(
                plateau["headings_rad"][segment] - full_result["headings_rad"][segment]
            )))
            for segment in SEGMENTS[1:]
        },
        "maximum_heading_change_deg": float(max(abs(np.degrees(wrap(
            plateau["headings_rad"][segment] - full_result["headings_rad"][segment]
        ))) for segment in SEGMENTS[1:])),
        "formal_action_only_result": plateau,
        "interpretation": (
            "Formal-action-only removes both rest-to-action and action-to-rest "
            "transition rows; it is an ablation, never the production fit."
        ),
    }


def drift_stillness(episodes: Sequence[Raw6Episode]) -> dict[str, Any]:
    rows = {}
    for episode in episodes:
        per_segment = {}
        for segment in SEGMENTS:
            pre = episode.phase == "VERIFIED_PRE_REST"
            post = episode.phase == "VERIFIED_POST_REST"
            if np.count_nonzero(pre) < 2 or np.count_nonzero(post) < 2:
                continue
            pre_rotation = proper_mean(episode.rotation_world_sensor[segment][pre])
            post_rotation = proper_mean(episode.rotation_world_sensor[segment][post])
            per_segment[segment] = {
                "pre_post_rotation_distance_deg": float(np.degrees(
                    rotation_angle(pre_rotation[None], post_rotation[None])[0]
                )),
                "pre_rest_fraction": float(np.mean(episode.rest_detected[segment][pre])),
                "post_rest_fraction": float(np.mean(episode.rest_detected[segment][post])),
                "pre_bias_norm_deg_s": float(np.degrees(np.linalg.norm(
                    np.median(episode.bias_rad_s[segment][pre], axis=0)
                ))),
                "post_bias_norm_deg_s": float(np.degrees(np.linalg.norm(
                    np.median(episode.bias_rad_s[segment][post], axis=0)
                ))),
            }
        rows[episode.action] = per_segment
    return {
        "schema": "biospur-pure-imu-v0-drift-stillness-v1",
        "actions": rows,
        "external_orientation_truth_used": False,
        "pre_post_rotation_is_return_diagnostic_not_accuracy": True,
    }


def graph_consistency(
    edgewise: Mapping[str, Any],
    unified: Mapping[str, Any],
) -> dict[str, Any]:
    accumulated = edges_to_headings(unified["relative_edge_headings_rad"])
    direct = np.asarray([
        unified["headings_rad"][segment] for segment in SEGMENTS[1:]
    ])
    closure = np.degrees(np.abs(wrap(accumulated - direct)))
    baseline = np.asarray([
        edgewise["accumulated_headings_rad"][segment] for segment in SEGMENTS[1:]
    ])
    change = np.degrees(np.abs(wrap(direct - baseline)))
    return {
        "schema": "biospur-pure-imu-v0-tree-graph-consistency-v1",
        "graph_is_tree": True,
        "independent_cycle_count": 0,
        "cycle_closure_claim_made": False,
        "accumulation_vs_unified_max_deg": float(np.max(closure)),
        "edgewise_to_unified_change_deg": {
            segment: float(change[index])
            for index, segment in enumerate(SEGMENTS[1:])
        },
        "edgewise_to_unified_max_change_deg": float(np.max(change)),
        "honest_scope": (
            "A tree has no independent cycle-closure test. Consistency means "
            "the nine edge differences reconstruct the nine pelvis-gauged nodes."
        ),
    }


def sensor_display_frames(
    unified: Mapping[str, Any],
    factors: Mapping[str, EdgeFactors],
) -> dict[str, np.ndarray]:
    """Derive a fixed display frame from fitted joint levers/axes only."""

    levers = {
        edge: np.asarray(value, float)
        for edge, value in unified["lever_by_edge"].items()
    }
    proximal: dict[str, list[np.ndarray]] = {segment: [] for segment in SEGMENTS}
    distal: dict[str, list[np.ndarray]] = {segment: [] for segment in SEGMENTS}
    for edge, parent, child, _ in EDGES:
        distal[parent].append(levers[edge][:3])
        proximal[child].append(levers[edge][3:])
    def frame_from_z_x(z_seed: np.ndarray, x_seed: np.ndarray) -> np.ndarray:
        z = _unit(z_seed, np.array([0.0, 0.0, 1.0]))
        x_projected = np.asarray(x_seed, float) - z * float(np.asarray(x_seed, float) @ z)
        if np.linalg.norm(x_projected) < 1e-6:
            alternate = np.array([1.0, 0.0, 0.0])
            if abs(float(alternate @ z)) > 0.85:
                alternate = np.array([0.0, 1.0, 0.0])
            x_projected = alternate - z * float(alternate @ z)
        x = _unit(x_projected)
        y = _unit(np.cross(z, x))
        x = _unit(np.cross(y, z))
        return np.column_stack((x, y, z))

    frames = {}
    for segment in SEGMENTS:
        # The trunk frames have several graph connections.  Use their fitted
        # bilateral joint-center geometry to define anatomical lateral and
        # superior directions.  The former generic proximal-minus-distal rule
        # inverted the torso and let the arbitrary sensor x-axis define trunk
        # twist, producing a visually folded skeleton even when edge lengths
        # were numerically constant.
        if segment == "pelvis":
            torso = levers["pelvis_torso"][:3]
            hip_left = levers["hip_left"][:3]
            hip_right = levers["hip_right"][:3]
            frames[segment] = frame_from_z_x(
                torso - 0.5 * (hip_left + hip_right),
                hip_right - hip_left,
            )
            continue
        if segment == "torso":
            pelvis = levers["pelvis_torso"][3:]
            shoulder_left = levers["shoulder_left"][:3]
            shoulder_right = levers["shoulder_right"][:3]
            frames[segment] = frame_from_z_x(
                0.5 * (shoulder_left + shoulder_right) - pelvis,
                shoulder_right - shoulder_left,
            )
            continue
        if proximal[segment] and distal[segment]:
            z = _unit(
                np.mean(proximal[segment], axis=0) - np.mean(distal[segment], axis=0),
                np.array([0.0, 0.0, 1.0]),
            )
        elif proximal[segment]:
            z = _unit(np.mean(proximal[segment], axis=0), np.array([0.0, 0.0, 1.0]))
        elif distal[segment]:
            z = _unit(-np.mean(distal[segment], axis=0), np.array([0.0, 0.0, 1.0]))
        else:
            z = np.array([0.0, 0.0, 1.0])
        hinge = EDGE_BY_CHILD.get(segment)
        axis = None
        if hinge is not None:
            factor = factors[hinge[0]]
            axis = factor.hinge_axis_child
        if axis is None:
            for edge, parent, _, _ in EDGES:
                if parent == segment and factors[edge].hinge_axis_parent is not None:
                    axis = factors[edge].hinge_axis_parent
                    break
        seed = np.asarray(axis, float) if axis is not None else np.array([1.0, 0.0, 0.0])
        frames[segment] = frame_from_z_x(z, seed)
    return frames
