"""Direct raw accelerometer/gyroscope frontend with capture-wide bias state."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation
from vqf import VQF

from biospur_fusion.v0.data import G, si_samples
from biospur_fusion.v0.math3d import matrix_to_quat_wxyz
from biospur_fusion.v0.raw6_heading import PHASES, Raw6Episode


@dataclass(frozen=True)
class NodeStillness:
    gyro_bias_rad_s: np.ndarray
    gyro_bias_cov_rad2_s2: np.ndarray
    gyro_cov_rad2_s2: np.ndarray
    gyro_quantization_variance_rad2_s2: float
    accelerometer_mean_mps2: np.ndarray
    accelerometer_cov_m2_s4: np.ndarray
    accelerometer_quantization_variance_m2_s4: float
    gravity_sensor_unit: np.ndarray
    effective_sample_size: float
    row_count: int


@dataclass(frozen=True)
class StillnessCalibration:
    by_node: Mapping[str, NodeStillness]
    source_action: str
    information_scope: tuple[str, ...]
    forbidden_unique_claims: tuple[str, ...]


class ChronologicalOrientationFrontend:
    """One capture-level VQF6D/bias/uncertainty state for all ten nodes.

    Episode boundaries are labels only.  A gap in the sealed slices advances
    orientation uncertainty but never creates a new VQF instance or yaw gauge.
    """

    def __init__(
        self, stillness: StillnessCalibration, *, rate_hz: int, capture_id: str,
    ) -> None:
        self.stillness = stillness
        self.rate_hz = int(rate_hz)
        self.capture_id = str(capture_id)
        if self.rate_hz <= 0:
            raise ValueError("orientation frontend rate must be positive")
        self._estimators = {
            node: VQF(1.0 / self.rate_hz, magDistRejectionEnabled=False)
            for node in stillness.by_node
        }
        self._last_time_ns: dict[str, int | None] = {
            node: None for node in stillness.by_node
        }
        self._gap_covariance_rad2 = {
            node: np.zeros((3, 3), dtype=float) for node in stillness.by_node
        }
        self._events: list[dict[str, Any]] = []
        self._episode_order: list[str] = []

    def begin_episode(self, action: str, first_time_ns: int) -> None:
        if action in self._episode_order:
            raise ValueError(f"orientation frontend episode repeated: {action}")
        self._episode_order.append(str(action))
        self._events.append({
            "event": "EPISODE_LABEL_ENTERED_WITHOUT_FILTER_RESET",
            "action": str(action),
            "first_time_ns": int(first_time_ns),
            "vQF_instances_created": 0,
            "new_yaw_gauges_created": 0,
        })

    def process_node(
        self,
        *,
        node: str,
        action: str,
        time_ns: np.ndarray,
        gyro_bias_corrected_rad_s: np.ndarray,
        accelerometer_mps2: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, Mapping[str, Any]]:
        if node not in self._estimators:
            raise ValueError(f"orientation frontend received unknown node: {node}")
        time_ns = np.asarray(time_ns, dtype=np.int64)
        if len(time_ns) < 2 or np.any(np.diff(time_ns) <= 0):
            raise ValueError(f"{action}:{node}: nonmonotone orientation time")
        previous = self._last_time_ns[node]
        expected_step_ns = int(round(1e9 / self.rate_hz))
        gap_ns = 0 if previous is None else int(time_ns[0]) - int(previous) - expected_step_ns
        if gap_ns < 0:
            raise ValueError(f"{action}:{node}: chronological orientation state overlapped")
        if gap_ns > 0:
            gap_s = gap_ns * 1e-9
            still = self.stillness.by_node[node]
            # Missing angular increments are not fabricated.  Bias uncertainty
            # is common-mode (quadratic in time); per-sample noise contributes
            # a random-walk term.  The orientation branch itself remains the
            # same VQF instance and is subsequently constrained by QMT/graph
            # evidence.
            self._gap_covariance_rad2[node] += (
                still.gyro_bias_cov_rad2_s2 * gap_s**2
                + still.gyro_cov_rad2_s2 * gap_s / self.rate_hz
            )
            self._events.append({
                "event": "SEALED_SLICE_GAP_UNCERTAINTY_PROPAGATED_WITHOUT_RESET",
                "action": str(action),
                "node": node,
                "gap_ns": gap_ns,
                "gap_orientation_covariance_rad2": (
                    self._gap_covariance_rad2[node].tolist()
                ),
                "vQF_reset": False,
                "new_yaw_gauge": False,
            })
        result = self._estimators[node].updateBatch(
            np.ascontiguousarray(gyro_bias_corrected_rad_s),
            np.ascontiguousarray(accelerometer_mps2),
        )
        quaternion = np.asarray(result["quat6D"], dtype=float)
        rotation = Rotation.from_quat(quaternion[:, [1, 2, 3, 0]]).as_matrix()
        self._last_time_ns[node] = int(time_ns[-1])
        return rotation, quaternion, {
            "capture_level_frontend": True,
            "episode_boundary_reset": False,
            "independent_episode_yaw_gauge": False,
            "gap_ns_before_episode": gap_ns,
            "gap_orientation_covariance_rad2": self._gap_covariance_rad2[node].tolist(),
        }

    def audit(self) -> dict[str, Any]:
        return {
            "schema": "biospur-c2-chronological-orientation-frontend-v1",
            "capture_id": self.capture_id,
            "node_count": len(self._estimators),
            "vQF_instance_count_total": len(self._estimators),
            "vQF_instances_per_node": 1,
            "episode_order": list(self._episode_order),
            "episode_boundary_reset_count": 0,
            "independent_episode_yaw_gauge_count": 0,
            "gap_uncertainty_propagated": any(
                row["event"] == "SEALED_SLICE_GAP_UNCERTAINTY_PROPAGATED_WITHOUT_RESET"
                for row in self._events
            ),
            "events": list(self._events),
        }


def _effective_sample_size(values: np.ndarray, maximum_lag: int = 100) -> float:
    values = np.asarray(values, dtype=float)
    n = len(values)
    if n < 4:
        return 1.0
    centered = values - np.mean(values, axis=0, keepdims=True)
    variance = np.sum(centered * centered, axis=0)
    correlations = []
    for lag in range(1, min(maximum_lag, n - 1) + 1):
        numerator = np.sum(centered[:-lag] * centered[lag:], axis=0)
        rho = np.divide(numerator, variance, out=np.zeros_like(numerator), where=variance > 0)
        value = float(np.mean(rho))
        if value <= 0.0:
            break
        correlations.append(value)
    tau = 1.0 + 2.0 * float(np.sum(correlations))
    return float(max(1.0, min(n, n / max(tau, 1.0))))


def estimate_stillness(
    rows_by_node: Mapping[str, np.ndarray], *, source_action: str = "00_initial_still",
) -> StillnessCalibration:
    output: dict[str, NodeStillness] = {}
    for node, rows in rows_by_node.items():
        accepted = rows[rows["status"] == 1]
        if len(accepted) < 20:
            raise ValueError(f"{node}: insufficient initial-still rows")
        acc, gyro = si_samples(accepted)
        bias = np.median(gyro, axis=0)
        gyro_centered = gyro - bias
        acc_mean = np.mean(acc, axis=0)
        norm = float(np.linalg.norm(acc_mean))
        if not 7.0 <= norm <= 12.5:
            raise ValueError(f"{node}: initial-still acceleration magnitude is implausible")
        effective_rows = _effective_sample_size(
            np.c_[gyro_centered, acc - acc_mean]
        )
        # The source values are integer sensor codes.  A constant or lightly
        # jittering still record must therefore retain the uncertainty of one
        # quantization cell instead of acquiring a singular, falsely exact
        # covariance.  These terms are fixed by the raw-to-SI conversion, not
        # by a fitted residual or a real-capture outcome.
        gyro_step_rad_s = float(np.deg2rad(1.0 / 16.384))
        acc_step_mps2 = float(G / 2048.0)
        gyro_quantization_variance = gyro_step_rad_s**2 / 12.0
        acc_quantization_variance = acc_step_mps2**2 / 12.0
        gyro_covariance = (
            np.asarray(np.cov(gyro_centered.T, ddof=1), dtype=float)
            + np.eye(3) * gyro_quantization_variance
        )
        accelerometer_covariance = (
            np.asarray(np.cov(acc.T, ddof=1), dtype=float)
            + np.eye(3) * acc_quantization_variance
        )
        # Gaussian median asymptotics, reduced by the correlation-aware
        # effective row count, represent the common capture-wide bias error.
        gyro_bias_covariance = (
            (np.pi / 2.0) * gyro_covariance / effective_rows
        )
        output[node] = NodeStillness(
            gyro_bias_rad_s=bias,
            gyro_bias_cov_rad2_s2=gyro_bias_covariance,
            gyro_cov_rad2_s2=gyro_covariance,
            gyro_quantization_variance_rad2_s2=gyro_quantization_variance,
            accelerometer_mean_mps2=acc_mean,
            accelerometer_cov_m2_s4=accelerometer_covariance,
            accelerometer_quantization_variance_m2_s4=acc_quantization_variance,
            gravity_sensor_unit=acc_mean / norm,
            effective_sample_size=effective_rows,
            row_count=int(len(accepted)),
        )
    return StillnessCalibration(
        by_node=output,
        source_action=source_action,
        information_scope=("gravity_tilt", "gyro_bias", "gyro_noise", "accelerometer_noise", "impossible_branch_rejection"),
        forbidden_unique_claims=("yaw", "joint_axis", "joint_center", "bone_length", "axial_mount_twist", "sagittal_branch", "calibration_complete"),
    )


def _phase_vector(time_ns: np.ndarray, diagnostic: Mapping[str, Any]) -> np.ndarray:
    phase = np.full(len(time_ns), "UNCLASSIFIED_COMPLETE_EPISODE", dtype="U40")
    for row in diagnostic.get("phases", []):
        mask = (
            (time_ns >= int(row["start_global_time_ns"]))
            & (time_ns < int(row["stop_global_time_ns_exclusive"]))
        )
        phase[mask] = str(row["phase"])
    return phase


def raw_episode_from_rows(
    *,
    action: str,
    rows_by_node: Mapping[str, np.ndarray],
    identity: Mapping[str, str],
    diagnostic: Mapping[str, Any],
    stillness: StillnessCalibration,
    orientation_frontend: ChronologicalOrientationFrontend,
    rate_hz: int = 50,
) -> Raw6Episode:
    """Build one episode from only raw acc/gyr and the capture time field."""

    if set(rows_by_node) != set(identity) or set(stillness.by_node) != set(identity):
        raise ValueError("C2 episode node set differs from sealed authority")
    accepted = {node: rows[rows["status"] == 1] for node, rows in rows_by_node.items()}
    start = max(int(rows["global_time_ns"][0]) for rows in accepted.values())
    stop = min(int(rows["global_time_ns"][-1]) for rows in accepted.values())
    step = int(round(1e9 / rate_hz))
    first = ((start + step - 1) // step) * step
    last = (stop // step) * step
    if last - first < 10 * step:
        raise ValueError(f"{action}: no common ten-node interval")
    grid = np.arange(first, last + 1, step, dtype=np.int64)
    acc_by_segment: dict[str, np.ndarray] = {}
    gyro_by_segment: dict[str, np.ndarray] = {}
    rotation_by_segment: dict[str, np.ndarray] = {}
    quaternion_by_segment: dict[str, np.ndarray] = {}
    rest_by_segment: dict[str, np.ndarray] = {}
    bias_by_segment: dict[str, np.ndarray] = {}
    audit_nodes: dict[str, Any] = {}
    target_s = (grid - grid[0]).astype(float) * 1e-9
    if orientation_frontend.stillness is not stillness:
        raise ValueError("episode attempted to use a different orientation/bias state")
    orientation_frontend.begin_episode(action, int(grid[0]))
    for node, rows in accepted.items():
        source_time = rows["global_time_ns"].astype(np.int64)
        source_s = (source_time - grid[0]).astype(float) * 1e-9
        acc, gyro = si_samples(rows)
        acc_i = np.column_stack([
            np.interp(target_s, source_s, acc[:, axis]) for axis in range(3)
        ])
        gyro_i = np.column_stack([
            np.interp(target_s, source_s, gyro[:, axis]) for axis in range(3)
        ]) - stillness.by_node[node].gyro_bias_rad_s
        rotation, quaternion_xyzw, orientation_audit = (
            orientation_frontend.process_node(
                node=node,
                action=action,
                time_ns=grid,
                gyro_bias_corrected_rad_s=gyro_i,
                accelerometer_mps2=acc_i,
            )
        )
        segment = identity[node]
        acc_by_segment[segment] = acc_i
        gyro_by_segment[segment] = gyro_i
        rotation_by_segment[segment] = rotation
        quaternion_by_segment[segment] = matrix_to_quat_wxyz(rotation)
        # VQF's rest state is not exposed by the capture-level wrapper because
        # episode slicing must not alter filter ownership.  Rest/phase evidence
        # remains the signal-verified episode diagnostic.
        rest_by_segment[segment] = np.isin(
            _phase_vector(grid, diagnostic),
            ("VERIFIED_PRE_REST", "VERIFIED_POST_REST"),
        )
        bias_by_segment[segment] = np.repeat(
            stillness.by_node[node].gyro_bias_rad_s[None, :], len(grid), axis=0,
        )
        audit_nodes[node] = {
            "segment": segment,
            "source_rows": int(len(rows)),
            "first_time_ns": int(source_time[0]),
            "last_time_ns": int(source_time[-1]),
            "capture_wide_bias_rad_s": stillness.by_node[node].gyro_bias_rad_s.tolist(),
            "capture_wide_bias_cov_rad2_s2": (
                stillness.by_node[node].gyro_bias_cov_rad2_s2.tolist()
            ),
            "gyro_noise_cov_rad2_s2": stillness.by_node[node].gyro_cov_rad2_s2.tolist(),
            "gyro_quantization_variance_rad2_s2": (
                stillness.by_node[node].gyro_quantization_variance_rad2_s2
            ),
            "accelerometer_noise_cov_m2_s4": stillness.by_node[node].accelerometer_cov_m2_s4.tolist(),
            "accelerometer_quantization_variance_m2_s4": (
                stillness.by_node[node].accelerometer_quantization_variance_m2_s4
            ),
            "noise_covariance_source": (
                "CAPTURE_WIDE_INITIAL_STILL_PLUS_RAW_CODE_QUANTIZATION"
            ),
            "bias_covariance_source": (
                "CORRELATION_REDUCED_INITIAL_STILL_MEDIAN_ASYMPTOTIC"
            ),
            "gyro_bias_subtracted_before_qmt": True,
            "orientation_frontend": dict(orientation_audit),
            "fields_read": ["status", "global_time_ns", "acc_raw", "gyro_raw"],
        }
    phase = _phase_vector(grid, diagnostic)
    if diagnostic.get("EPISODE_COMPLETENESS") != "PASS":
        raise ValueError(f"{action}: incomplete episode")
    missing = [name for name in PHASES if not np.any(phase == name)]
    if missing:
        raise ValueError(f"{action}: missing complete episode phases {missing}")
    return Raw6Episode(
        capture="CAPTURE2",
        action=action,
        partition="CUMULATIVE_PROFILE",
        time_ns=grid,
        phase=phase,
        acc=acc_by_segment,
        gyro=gyro_by_segment,
        rotation_world_sensor=rotation_by_segment,
        quat_world_sensor_wxyz=quaternion_by_segment,
        rest_detected=rest_by_segment,
        bias_rad_s=bias_by_segment,
        audit={
            "schema": "biospur-c2-direct-raw6-episode-v1",
            "action": action,
            "input": "RAW_ACCELEROMETER_GYROSCOPE_ONLY",
            "nodes": audit_nodes,
            "magnetometer_used": False,
            "vendor_or_locked_quaternion_used": False,
            "manual_pose_truth_used": False,
            "old_profile_or_shared_ik_used": False,
            "capture_level_orientation_frontend": True,
            "per_episode_vqf_reset": False,
            "orientation_frontend_audit": orientation_frontend.audit(),
            "phase_counts": {name: int(np.count_nonzero(phase == name)) for name in (*PHASES, "UNCLASSIFIED_COMPLETE_EPISODE")},
        },
    )


def replace_episode_gyro_bias(
    episode: Raw6Episode, correction_by_segment: Mapping[str, np.ndarray],
) -> Raw6Episode:
    """Synthetic/test helper for a bounded capture-wide bias correction."""

    gyro = {
        segment: values - np.asarray(correction_by_segment.get(segment, np.zeros(3)))
        for segment, values in episode.gyro.items()
    }
    return replace(episode, gyro=gyro)
