"""One-state-per-node continuous six-axis orientation frontend for C2."""
from __future__ import annotations

from dataclasses import InitVar, dataclass, field
import hashlib
from importlib.metadata import version as package_version
import json
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np
from vqf import VQF

from .architecture_guard import C2ExecutionGuard
from .calibration_posterior import CaptureWideCalibrationPosterior
from .range_reader import DecodedAction, EXPECTED_STEP_US


ACC_SCALE = 9.80665 / 2048.0
GYRO_SCALE = np.deg2rad(1.0 / 16.384)
RAW_SATURATION_GUARD = 32760
_RUNTIME_VQF_TILT_PROVENANCE_CAPABILITY = object()


def _semantic_digest(value: Any) -> str:
    def canonical(item: Any) -> Any:
        if isinstance(item, np.ndarray):
            array = np.ascontiguousarray(item)
            return {
                "dtype": array.dtype.str, "shape": list(array.shape),
                "sha256": hashlib.sha256(array.view(np.uint8)).hexdigest(),
            }
        if isinstance(item, Mapping):
            return {str(key): canonical(val) for key, val in sorted(item.items())}
        if isinstance(item, (tuple, list)):
            return [canonical(val) for val in item]
        if isinstance(item, np.generic):
            return item.item()
        return item
    return hashlib.sha256(json.dumps(
        canonical(value), sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _readonly_array_map(values: Mapping[str, np.ndarray]) -> Mapping[str, np.ndarray]:
    frozen = {}
    for key, value in values.items():
        array = np.asarray(value).copy()
        array.setflags(write=False)
        frozen[str(key)] = array
    return MappingProxyType(frozen)


def contiguous_span_ids(
    time_us: np.ndarray,
    boot_epoch: np.ndarray,
    *,
    expected_step_us: int = EXPECTED_STEP_US,
) -> np.ndarray:
    """Assign spans without bridging gaps, duplicates, jitter, or boot changes."""

    time = np.asarray(time_us, dtype=np.int64)
    boot = np.asarray(boot_epoch, dtype=np.int64)
    if time.shape != boot.shape or time.ndim != 1:
        raise ValueError("time and boot epoch must be equal one-dimensional arrays")
    output = np.zeros(len(time), dtype=np.int32)
    if len(time) > 1:
        output[1:] = np.cumsum(
            (np.diff(boot) != 0) | (np.diff(time) != int(expected_step_us))
        )
    return output


@dataclass(frozen=True)
class FactorRowQuality:
    """Rows and contiguous spans allowed to reach orientation/factor owners."""

    retained_indices: np.ndarray
    contiguous_spans: tuple[slice, ...]
    retained_span_ids: np.ndarray
    covariance_inflation: float
    report: Mapping[str, Any]


def assess_factor_rows(
    time_us: np.ndarray,
    boot_epoch: np.ndarray,
    acc_raw: np.ndarray,
    gyro_raw: np.ndarray,
    *,
    expected_step_us: int = EXPECTED_STEP_US,
) -> FactorRowQuality:
    """Own duplicate/clipping exclusion and gap/jitter span splitting.

    Ordinary anomalies are diagnosed and increase uncertainty. They do not
    terminate the capture and no replacement samples are fabricated.
    """

    time = np.asarray(time_us, dtype=np.int64)
    boot = np.asarray(boot_epoch, dtype=np.int64)
    acc = np.asarray(acc_raw)
    gyro = np.asarray(gyro_raw)
    if time.ndim != 1 or boot.shape != time.shape or acc.shape != (len(time), 3) or gyro.shape != (len(time), 3):
        raise ValueError("factor-row quality requires aligned time/boot/Nx3 raw arrays")
    clipped = np.any(np.abs(acc.astype(np.int64)) >= RAW_SATURATION_GUARD, axis=1) | np.any(
        np.abs(gyro.astype(np.int64)) >= RAW_SATURATION_GUARD, axis=1,
    )
    nonmonotonic = np.zeros(len(time), dtype=bool)
    if len(time) > 1:
        nonmonotonic[1:] = (np.diff(boot) == 0) & (np.diff(time) <= 0)
    valid = ~(clipped | nonmonotonic)
    retained = np.flatnonzero(valid)
    retained_time = time[retained]
    retained_boot = boot[retained]
    breaks = (
        (np.diff(retained_boot) != 0) | (np.diff(retained_time) != int(expected_step_us))
        if len(retained) > 1 else np.empty(0, dtype=bool)
    )
    boundaries = np.r_[0, np.flatnonzero(breaks) + 1, len(retained)] if len(retained) else np.array([0])
    spans = tuple(
        slice(int(left), int(right)) for left, right in zip(boundaries[:-1], boundaries[1:])
        if right > left
    )
    span_ids = np.empty(len(retained), dtype=np.int32)
    for span_id, span in enumerate(spans):
        span_ids[span] = span_id
    anomaly_fraction = float((np.count_nonzero(clipped) + np.count_nonzero(nonmonotonic) + np.count_nonzero(breaks)) / max(1, len(time)))
    covariance_inflation = float(1.0 + 4.0 * anomaly_fraction)
    return FactorRowQuality(
        retained_indices=retained,
        contiguous_spans=spans,
        retained_span_ids=span_ids,
        covariance_inflation=covariance_inflation,
        report={
            "schema": "biospur-c2-factor-row-quality-v1",
            "input_rows": int(len(time)),
            "retained_rows": int(len(retained)),
            "clipped_rows_excluded": int(np.count_nonzero(clipped)),
            "duplicate_or_nonmonotonic_rows_excluded": int(np.count_nonzero(nonmonotonic)),
            "contiguous_span_count": len(spans),
            "contiguous_span_lengths": [int(span.stop - span.start) for span in spans],
            "gap_jitter_or_boot_boundaries": int(np.count_nonzero(breaks)),
            "covariance_inflation": covariance_inflation,
            "rows_fabricated": 0,
            "anomaly_terminates_capture": False,
            "cross_boundary_orientation_or_factor_update": False,
            "local_no_update": len(retained) < 2,
        },
    )


@dataclass(frozen=True)
class OrientedAction:
    action: str
    chronological_index: int
    time_us_by_node: Mapping[str, np.ndarray]
    derived_boot_epoch_by_node: Mapping[str, np.ndarray]
    contiguous_span_id_by_node: Mapping[str, np.ndarray]
    acc_mps2_by_node: Mapping[str, np.ndarray]
    gyro_rads_by_node: Mapping[str, np.ndarray]
    quat_world_sensor_wxyz_by_node: Mapping[str, np.ndarray]
    gap_only_orientation_covariance_rad2_by_node: Mapping[str, np.ndarray]
    vqf_residual_bias_rad_s_by_node: Mapping[str, np.ndarray]
    vqf_residual_bias_sigma_rad_s_by_node: Mapping[str, np.ndarray]
    vqf_rest_detected_by_node: Mapping[str, np.ndarray]
    audit: Mapping[str, Any]
    calibration_posterior_by_node: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict
    )
    imu_sample_sequence_by_node: Mapping[str, np.ndarray] = field(default_factory=dict)
    raw_start_offset_by_node: Mapping[str, np.ndarray] = field(default_factory=dict)
    raw_end_offset_by_node: Mapping[str, np.ndarray] = field(default_factory=dict)
    raw_sample_index_by_node: Mapping[str, np.ndarray] = field(default_factory=dict)
    vqf_relative_rest_deviation_by_node: Mapping[str, np.ndarray] = field(default_factory=dict)
    acceleration_norm_residual_mps2_by_node: Mapping[str, np.ndarray] = field(default_factory=dict)
    world_tilt_innovation_rad_by_node: Mapping[str, np.ndarray] = field(default_factory=dict)
    vqf_tilt_diagnostic_provenance_digest: str | None = None
    vqf_tilt_source_binding_digest_by_node: Mapping[str, str] = field(default_factory=dict)
    vqf_tilt_diagnostic_provenance: "VQFTiltDiagnosticProvenance | None" = None
    vqf_tilt_source_binding_payload_by_node: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class VQFTiltDiagnosticProvenance:
    """Runtime-seal-derived owner; it intentionally does not claim global time."""

    prefit_seal_sha256: str
    qualified_source_closure_digest: str
    initial_stochastic_state_semantic_sha256: str
    initial_stochastic_state_source_status: str
    initial_stochastic_state_source_sha256: str | None
    settings_semantic_sha256: str
    timer_domain: str
    vqf_version: str
    vqf_parameters_digest: str
    action00_source_authority_sha256: str | None = None
    action00_source_authority_role: str | None = None
    initial_stochastic_state_source_relative_path: str | None = None
    initial_stochastic_state_source_size: int | None = None
    initial_stochastic_state_source_mtime_ns: int | None = None
    digest: str = ""
    _issuance: InitVar[object | None] = None

    def __post_init__(self, _issuance: object | None) -> None:
        digests = (
            self.prefit_seal_sha256, self.qualified_source_closure_digest,
            self.initial_stochastic_state_semantic_sha256,
            self.settings_semantic_sha256, self.vqf_parameters_digest,
        )
        if (
            _issuance is not _RUNTIME_VQF_TILT_PROVENANCE_CAPABILITY
            or any(len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
                for value in digests)
            or self.timer_domain != "B306_TIMER2_US_NODE_LOCAL"
            or self.vqf_version != package_version("vqf")
            or ((self.action00_source_authority_sha256 is None)
                != (self.action00_source_authority_role is None))
            or (self.action00_source_authority_sha256 is not None and (
                len(self.action00_source_authority_sha256) != 64
                or self.action00_source_authority_role != "ACTION00_ENGINEERING_POLICY"
            ))
            or self.initial_stochastic_state_source_status not in {
                "SEAL_OWNED", "SEALED_SETTINGS_PATH_AND_SEMANTIC_BOUND",
                "MISSING_FROM_QUALIFIED_SOURCE_CLOSURE",
            }
            or (self.initial_stochastic_state_source_status in {
                "SEAL_OWNED", "SEALED_SETTINGS_PATH_AND_SEMANTIC_BOUND",
            } and (
                self.initial_stochastic_state_source_sha256 is None
                or len(self.initial_stochastic_state_source_sha256) != 64
            ))
            or (self.initial_stochastic_state_source_status == "MISSING_FROM_QUALIFIED_SOURCE_CLOSURE"
                and any(value is not None for value in (
                    self.initial_stochastic_state_source_sha256,
                    self.initial_stochastic_state_source_relative_path,
                    self.initial_stochastic_state_source_size,
                    self.initial_stochastic_state_source_mtime_ns,
                )))
            or (self.initial_stochastic_state_source_status == "SEALED_SETTINGS_PATH_AND_SEMANTIC_BOUND"
                and (not self.initial_stochastic_state_source_relative_path
                     or type(self.initial_stochastic_state_source_size) is not int
                     or self.initial_stochastic_state_source_size <= 0
                     or type(self.initial_stochastic_state_source_mtime_ns) is not int
                     or self.initial_stochastic_state_source_mtime_ns <= 0))
        ):
            raise ValueError("invalid VQF tilt-diagnostic provenance")
        expected = _semantic_digest({
            "schema": "biospur-c2-vqf-tilt-diagnostic-provenance-v1",
            "prefit_seal_sha256": self.prefit_seal_sha256,
            "qualified_source_closure_digest": self.qualified_source_closure_digest,
            "initial_stochastic_state_semantic_sha256": self.initial_stochastic_state_semantic_sha256,
            "initial_stochastic_state_source_status": self.initial_stochastic_state_source_status,
            "initial_stochastic_state_source_sha256": self.initial_stochastic_state_source_sha256,
            "initial_stochastic_state_source_relative_path": self.initial_stochastic_state_source_relative_path,
            "initial_stochastic_state_source_size": self.initial_stochastic_state_source_size,
            "initial_stochastic_state_source_mtime_ns": self.initial_stochastic_state_source_mtime_ns,
            "settings_semantic_sha256": self.settings_semantic_sha256,
            "timer_domain": self.timer_domain,
            "vqf_version": self.vqf_version,
            "vqf_parameters_digest": self.vqf_parameters_digest,
            "action00_source_authority_sha256": self.action00_source_authority_sha256,
            "action00_source_authority_role": self.action00_source_authority_role,
            "global_clock_binding": "NOT_OWNED_HERE",
        })
        if self.digest and self.digest != expected:
            raise ValueError("VQF tilt-diagnostic provenance digest mismatch")
        object.__setattr__(self, "digest", expected)

    @property
    def product_ready(self) -> bool:
        return False


def verify_vqf_tilt_source_binding(oriented: OrientedAction, node: str) -> str:
    """Recompute the immutable row-identity portion before downstream use."""
    payload = dict(oriented.vqf_tilt_source_binding_payload_by_node[node])
    current = {
        "retained_time_us_digest": _semantic_digest(oriented.time_us_by_node[node]),
        "retained_boot_digest": _semantic_digest(oriented.derived_boot_epoch_by_node[node]),
        "retained_span_digest": _semantic_digest(oriented.contiguous_span_id_by_node[node]),
        "retained_sequence_digest": _semantic_digest(oriented.imu_sample_sequence_by_node[node]),
        "retained_raw_start_digest": _semantic_digest(oriented.raw_start_offset_by_node[node]),
        "retained_raw_end_digest": _semantic_digest(oriented.raw_end_offset_by_node[node]),
        "retained_raw_sample_index_digest": _semantic_digest(oriented.raw_sample_index_by_node[node]),
    }
    if any(payload.get(key) != value for key, value in current.items()):
        raise ValueError("VQF tilt source arrays changed after provenance issuance")
    digest = _semantic_digest(payload)
    if digest != oriented.vqf_tilt_source_binding_digest_by_node[node]:
        raise ValueError("VQF tilt source-binding digest mismatch")
    return digest


class ContinuousVQFState:
    """Persistent VQF and covariance state across all sealed action labels."""

    def __init__(
        self,
        initial_stochastic_state: Mapping[str, Any],
        *,
        execution_guard: C2ExecutionGuard,
        sample_period_s: float = 0.005,
        unknown_boot_orientation_sigma_rad: float,
        unknown_unusable_episode_orientation_sigma_rad: float,
        calibration_settings: Mapping[str, Any] | None = None,
        tilt_diagnostic_runtime_authority: Mapping[str, Any] | None = None,
        _tilt_provenance_capability: object | None = None,
    ) -> None:
        self.sample_period_s = float(sample_period_s)
        self.initial = initial_stochastic_state
        self._tilt_diagnostic_provenance: VQFTiltDiagnosticProvenance | None = None
        self.execution_guard = execution_guard
        self.unknown_boot_orientation_sigma_rad = float(unknown_boot_orientation_sigma_rad)
        self.unknown_unusable_episode_orientation_sigma_rad = float(
            unknown_unusable_episode_orientation_sigma_rad
        )
        if not 0.0 < self.unknown_boot_orientation_sigma_rad <= np.pi:
            raise ValueError("unknown boot orientation sigma must be in (0, pi]")
        if not 0.0 < self.unknown_unusable_episode_orientation_sigma_rad <= self.unknown_boot_orientation_sigma_rad:
            raise ValueError("unknown unusable-episode sigma must be positive and no larger than boot sigma")
        if self.execution_guard.capture_id is None:
            self.execution_guard.begin_capture("C2")
        self.nodes = tuple(initial_stochastic_state["nodes"])
        self._vqf = {
            node: VQF(self.sample_period_s, magDistRejectionEnabled=False)
            for node in self.nodes
        }
        if tilt_diagnostic_runtime_authority is not None:
            if _tilt_provenance_capability is not _RUNTIME_VQF_TILT_PROVENANCE_CAPABILITY:
                raise ValueError("VQF diagnostic provenance requires validated runtime issuance")
            parameter_digests = {
                _semantic_digest(dict(instance.params)) for instance in self._vqf.values()
            }
            if len(parameter_digests) != 1:
                raise RuntimeError("persistent VQF nodes disagree on exact parameters")
            self._tilt_diagnostic_provenance = VQFTiltDiagnosticProvenance(
                **dict(tilt_diagnostic_runtime_authority),
                vqf_parameters_digest=next(iter(parameter_digests)),
                _issuance=_RUNTIME_VQF_TILT_PROVENANCE_CAPABILITY,
            )
        self._last_timer_us: dict[str, int | None] = {node: None for node in self.nodes}
        self._last_boot: dict[str, int | None] = {node: None for node in self.nodes}
        self._covariance = {node: np.zeros((3, 3), dtype=float) for node in self.nodes}
        self._action_order: list[str] = []
        self._events: list[dict[str, Any]] = []
        self._calibration_owner = (
            None
            if calibration_settings is None
            else CaptureWideCalibrationPosterior(
                initial_stochastic_state,
                calibration_settings,
                sample_period_s=self.sample_period_s,
            )
        )
        for node, instance in self._vqf.items():
            self.execution_guard.bind_vqf_instance(node, instance)

    def _grow_gap(self, node: str, gap_s: float, *, action: str, cause: str) -> None:
        if gap_s <= 0:
            return
        row = self.initial["nodes"][node]
        bias_cov = np.asarray(row["gyro_bias_covariance_rad2_s2"], dtype=float)
        observation_cov = np.asarray(row["gyro_observation_covariance_rad2_s2"], dtype=float)
        increment = bias_cov * gap_s**2 + observation_cov * self.sample_period_s * gap_s
        self._covariance[node] += increment
        self._events.append({
            "event": "NO_UPDATE_COVARIANCE_GROWTH",
            "node": node,
            "action": action,
            "cause": cause,
            "gap_s": float(gap_s),
            "increment_rad2": increment.tolist(),
            "cumulative_rad2": self._covariance[node].tolist(),
            "vqf_reset": False,
            "samples_fabricated": 0,
        })

    def process(self, action: DecodedAction) -> OrientedAction:
        if action.chronological_index != len(self._action_order):
            raise ValueError("orientation actions must be processed in sealed chronology")
        self.execution_guard.begin_episode(action.chronological_index, action.action)
        self._action_order.append(action.action)
        acc_out: dict[str, np.ndarray] = {}
        gyro_out: dict[str, np.ndarray] = {}
        quat_out: dict[str, np.ndarray] = {}
        time_out: dict[str, np.ndarray] = {}
        boot_out: dict[str, np.ndarray] = {}
        span_out: dict[str, np.ndarray] = {}
        cov_out: dict[str, np.ndarray] = {}
        residual_bias_out: dict[str, np.ndarray] = {}
        residual_bias_sigma_out: dict[str, np.ndarray] = {}
        rest_out: dict[str, np.ndarray] = {}
        sequence_out: dict[str, np.ndarray] = {}
        raw_start_out: dict[str, np.ndarray] = {}
        raw_end_out: dict[str, np.ndarray] = {}
        raw_sample_out: dict[str, np.ndarray] = {}
        relative_rest_out: dict[str, np.ndarray] = {}
        acceleration_norm_residual_out: dict[str, np.ndarray] = {}
        world_tilt_innovation_out: dict[str, np.ndarray] = {}
        tilt_source_binding_out: dict[str, str] = {}
        tilt_source_payload_out: dict[str, Mapping[str, Any]] = {}
        calibration_out: dict[str, Mapping[str, Any]] = {}
        node_audit: dict[str, Any] = {}
        for node in self.nodes:
            source_rows = action.rows_by_node[node]
            quality = assess_factor_rows(
                source_rows["node_timer_us"], source_rows["derived_boot_epoch"],
                source_rows["acc_raw"], source_rows["gyro_raw"],
            )
            if len(quality.retained_indices) < 2:
                covariance_before = self._covariance[node].copy()
                boot_values = np.unique(source_rows["derived_boot_epoch"]) if len(source_rows) else np.empty(0)
                timer_span_positive = bool(
                    len(source_rows) > 1
                    and int(source_rows["node_timer_us"][-1]) > int(source_rows["node_timer_us"][0])
                )
                unknown_unusable_floor_applied = False
                if timer_span_positive and len(boot_values) == 1:
                    observed_duration_s = float(
                        int(source_rows["node_timer_us"][-1]) - int(source_rows["node_timer_us"][0])
                    ) * 1e-6
                    self._grow_gap(
                        node, observed_duration_s, action=action.action,
                        cause="LOCAL_EPISODE_UNUSABLE_ROWS_NO_UPDATE",
                    )
                elif len(boot_values) > 1:
                    increment = np.eye(3) * self.unknown_boot_orientation_sigma_rad**2
                    self._covariance[node] += increment
                    self._events.append({
                        "event": "UNUSABLE_BOOT_TRANSITION_UNKNOWN_DURATION_COVARIANCE_FLOOR",
                        "node": node,
                        "action": action.action,
                        "duration_invented": False,
                        "unknown_boot_orientation_sigma_rad": self.unknown_boot_orientation_sigma_rad,
                        "increment_rad2": increment.tolist(),
                        "cumulative_rad2": self._covariance[node].tolist(),
                        "vqf_reset": False,
                    })
                else:
                    increment = np.eye(3) * self.unknown_unusable_episode_orientation_sigma_rad**2
                    self._covariance[node] += increment
                    unknown_unusable_floor_applied = True
                    self._events.append({
                        "event": "UNUSABLE_EPISODE_UNKNOWN_DURATION_COVARIANCE_FLOOR",
                        "node": node,
                        "action": action.action,
                        "duration_invented": False,
                        "unknown_unusable_episode_orientation_sigma_rad": self.unknown_unusable_episode_orientation_sigma_rad,
                        "increment_rad2": increment.tolist(),
                        "cumulative_rad2": self._covariance[node].tolist(),
                        "vqf_reset": False,
                    })
                acc_out[node] = np.empty((0, 3), dtype=float)
                gyro_out[node] = np.empty((0, 3), dtype=float)
                quat_out[node] = np.empty((0, 4), dtype=float)
                time_out[node] = np.empty(0, dtype=np.int64)
                boot_out[node] = np.empty(0, dtype=np.int64)
                span_out[node] = np.empty(0, dtype=np.int32)
                cov_out[node] = np.empty((0, 3, 3), dtype=float)
                residual_bias_out[node] = np.empty((0, 3), dtype=float)
                residual_bias_sigma_out[node] = np.empty(0, dtype=float)
                rest_out[node] = np.empty(0, dtype=bool)
                sequence_out[node] = np.empty(0, dtype=np.int64)
                raw_start_out[node] = np.empty(0, dtype=np.uint64)
                raw_end_out[node] = np.empty(0, dtype=np.uint64)
                raw_sample_out[node] = np.empty(0, dtype=np.uint8)
                relative_rest_out[node] = np.empty((0, 2), dtype=float)
                acceleration_norm_residual_out[node] = np.empty(0, dtype=float)
                world_tilt_innovation_out[node] = np.empty(0, dtype=float)
                tilt_source_binding_out[node] = ""
                tilt_source_payload_out[node] = MappingProxyType({})
                if self._calibration_owner is not None:
                    calibration_out[node] = self._calibration_owner.snapshot(node)
                node_audit[node] = {
                    "rows": 0,
                    "source_rows": int(len(source_rows)),
                    "factor_row_quality": dict(quality.report),
                    "status": "LOCAL_NO_UPDATE_INSUFFICIENT_USABLE_ROWS_CONTINUE_CAPTURE",
                    "one_capture_wide_vqf_instance": True,
                    "vqf_reset": False,
                    "samples_fabricated": 0,
                    "capture_terminated": False,
                    "no_update_covariance_grown": bool(np.trace(self._covariance[node] - covariance_before) > 0.0),
                    "uncertainty_increment_trace_rad2": float(np.trace(self._covariance[node] - covariance_before)),
                    "final_gap_covariance_rad2": self._covariance[node].tolist(),
                    "unknown_boot_duration_invented": False,
                    "boot_transition_conservative_floor_applied": len(boot_values) > 1,
                    "unknown_unusable_episode_floor_applied": unknown_unusable_floor_applied,
                }
                self._events.append({
                    "event": "LOCAL_EPISODE_NO_UPDATE",
                    "node": node,
                    "action": action.action,
                    "cause": "FEWER_THAN_TWO_NONCLIPPED_MONOTONIC_ROWS",
                    "vqf_reset": False,
                    "capture_terminated": False,
                })
                continue
            rows = source_rows[quality.retained_indices]
            time_us = rows["node_timer_us"].astype(np.int64)
            boot = rows["derived_boot_epoch"].astype(np.int64)
            acc = rows["acc_raw"].astype(float) * ACC_SCALE
            bias = np.asarray(self.initial["nodes"][node]["gyro_bias_rad_s"], dtype=float)
            gyro = rows["gyro_raw"].astype(float) * GYRO_SCALE - bias
            calibration_prediction = None
            if self._calibration_owner is not None:
                acc, gyro, calibration_prediction = (
                    self._calibration_owner.predict_and_correct(
                        node,
                        action=action.action,
                        time_us=time_us,
                        boot_epoch=boot,
                        accelerometer_mps2=acc,
                        gyroscope_rad_s=gyro,
                    )
                )
            previous = self._last_timer_us[node]
            previous_boot = self._last_boot[node]
            if previous is not None:
                if int(boot[0]) == int(previous_boot):
                    gap_us = int(time_us[0]) - int(previous) - EXPECTED_STEP_US
                    self._grow_gap(
                        node, max(0, gap_us) * 1e-6,
                        action=action.action, cause="SEALED_INTER_ACTION_UNOBSERVED_INTERVAL",
                    )
                else:
                    increment = np.eye(3) * self.unknown_boot_orientation_sigma_rad**2
                    self._covariance[node] += increment
                    self._events.append({
                        "event": "DERIVED_BOOT_TRANSITION_UNKNOWN_DURATION",
                        "node": node,
                        "action": action.action,
                        "vqf_reset": False,
                        "duration_invented": False,
                        "unknown_boot_orientation_sigma_rad": self.unknown_boot_orientation_sigma_rad,
                        "increment_rad2": increment.tolist(),
                        "cumulative_rad2": self._covariance[node].tolist(),
                    })
            gap_trace = np.empty((len(rows), 3, 3), dtype=float)
            gap_trace[0] = self._covariance[node]
            gaps = np.diff(time_us)
            same_boot = np.diff(boot) == 0
            span_id = quality.retained_span_ids
            for sample_index, (value, contiguous_boot) in enumerate(zip(gaps, same_boot), start=1):
                if contiguous_boot and value > EXPECTED_STEP_US:
                    self._grow_gap(
                        node, float(value - EXPECTED_STEP_US) * 1e-6,
                        action=action.action, cause="WITHIN_RANGE_MISSING_INTERVAL",
                    )
                elif not contiguous_boot:
                    increment = np.eye(3) * self.unknown_boot_orientation_sigma_rad**2
                    self._covariance[node] += increment
                    self._events.append({
                        "event": "WITHIN_EPISODE_DERIVED_BOOT_TRANSITION_UNKNOWN_DURATION",
                        "node": node,
                        "action": action.action,
                        "sample_index": sample_index,
                        "duration_invented": False,
                        "unknown_boot_orientation_sigma_rad": self.unknown_boot_orientation_sigma_rad,
                        "increment_rad2": increment.tolist(),
                        "cumulative_rad2": self._covariance[node].tolist(),
                        "vqf_reset": False,
                    })
                gap_trace[sample_index] = self._covariance[node]
            span_results = [
                self._vqf[node].updateBatchFullState(
                    np.ascontiguousarray(gyro[span]), np.ascontiguousarray(acc[span]),
                )
                for span in quality.contiguous_spans
            ]
            quat = np.concatenate([np.asarray(result["quat6D"], dtype=float) for result in span_results])
            residual_bias = np.concatenate([np.asarray(result["bias"], dtype=float) for result in span_results])
            residual_bias_sigma = np.concatenate([np.asarray(result["biasSigma"], dtype=float) for result in span_results])
            rest_detected = np.concatenate([np.asarray(result["restDetected"], dtype=bool) for result in span_results])
            relative_rest = np.concatenate([
                np.asarray(result["relativeRestDeviations"], dtype=float)
                for result in span_results
            ])
            if quat.shape != (len(rows), 4) or not np.isfinite(quat).all():
                raise RuntimeError(f"{action.action}:{node}: invalid VQF output")
            if residual_bias.shape != gyro.shape or residual_bias_sigma.shape != (len(rows),):
                raise RuntimeError(f"{action.action}:{node}: invalid VQF bias output")
            self._last_timer_us[node] = int(time_us[-1])
            self._last_boot[node] = int(boot[-1])
            acc_out[node] = acc
            # The capture-wide median removes the static hardware-bias point
            # estimate before VQF. VQF then estimates only the residual bias
            # on that corrected signal. Its quaternion already consumes that
            # residual; QMT/center factors receive the matching corrected gyro.
            gyro_out[node] = gyro - residual_bias
            quat_out[node] = quat
            time_out[node] = time_us
            boot_out[node] = boot
            span_out[node] = span_id
            cov_out[node] = gap_trace
            residual_bias_out[node] = residual_bias
            residual_bias_sigma_out[node] = residual_bias_sigma
            rest_out[node] = rest_detected
            sequence_out[node] = rows["imu_sample_sequence"].astype(np.int64)
            raw_start_out[node] = rows["raw_start_offset"].astype(np.uint64)
            raw_end_out[node] = rows["raw_end_offset"].astype(np.uint64)
            raw_sample_out[node] = rows["raw_sample_index"].astype(np.uint8)
            relative_rest_out[node] = relative_rest
            quaternion_xyzw = quat[:, [1, 2, 3, 0]]
            vector = quaternion_xyzw[:, :3]
            scalar = quaternion_xyzw[:, 3:4]
            world_acceleration = (
                acc
                + 2.0 * np.cross(vector, np.cross(vector, acc) + scalar * acc)
            )
            acceleration_norm = np.linalg.norm(acc, axis=1)
            initial_row = self.initial["nodes"][node]
            gravity_norm = float(initial_row.get("accelerometer_norm_mps2", 9.80665))
            acceleration_norm_residual = np.abs(acceleration_norm - gravity_norm)
            world_tilt_innovation = np.arccos(np.clip(
                world_acceleration[:, 2] / np.maximum(acceleration_norm, np.finfo(float).eps),
                -1.0, 1.0,
            ))
            parameters = self._vqf[node].params
            acceleration_norm_residual_out[node] = acceleration_norm_residual
            world_tilt_innovation_out[node] = world_tilt_innovation
            source_binding_payload = {
                "schema": "biospur-c2-vqf-tilt-diagnostic-source-binding-candidate-v1",
                "runtime_provenance_digest": (
                    None if self._tilt_diagnostic_provenance is None
                    else self._tilt_diagnostic_provenance.digest
                ),
                "action": action.action,
                "chronological_index": action.chronological_index,
                "interval": action.interval,
                "access_audit_digest": _semantic_digest(action.access_audit),
                "decode_audit_digest": _semantic_digest(action.decode_audit),
                "node": node,
                "retained_time_us_digest": _semantic_digest(time_us),
                "retained_boot_digest": _semantic_digest(boot),
                "retained_span_digest": _semantic_digest(span_id),
                "retained_sequence_digest": _semantic_digest(sequence_out[node]),
                "retained_raw_start_digest": _semantic_digest(raw_start_out[node]),
                "retained_raw_end_digest": _semantic_digest(raw_end_out[node]),
                "retained_raw_sample_index_digest": _semantic_digest(raw_sample_out[node]),
                "vqf_parameters": parameters,
            }
            source_binding_digest = _semantic_digest(source_binding_payload)
            tilt_source_binding_out[node] = source_binding_digest
            tilt_source_payload_out[node] = MappingProxyType(source_binding_payload)
            if self._calibration_owner is not None:
                calibration_out[node] = self._calibration_owner.update_episode(
                    node,
                    action=action.action,
                    corrected_accelerometer_mps2=acc,
                    vqf_residual_bias_rad_s=residual_bias,
                    vqf_bias_sigma_rad_s=residual_bias_sigma,
                    vqf_rest_detected=rest_detected,
                )
            node_audit[node] = {
                "rows": int(len(rows)),
                "source_rows": int(len(source_rows)),
                "factor_row_quality": dict(quality.report),
                "one_capture_wide_vqf_instance": True,
                "magnetometer_used": False,
                "capture_wide_initial_bias_subtracted": True,
                "capture_wide_initial_bias_is_exact": False,
                "capture_wide_initial_bias_covariance_rad2_s2": self.initial["nodes"][node][
                    "gyro_bias_covariance_rad2_s2"
                ],
                "vqf_residual_bias_estimator_enabled": True,
                "vqf_bias_interaction": "VQF_ESTIMATES_RESIDUAL_AFTER_MEDIAN_SUBTRACTION;NOT_TWO_ESTIMATES_OF_THE_SAME_UNCORRECTED_SIGNAL",
                "vqf_residual_bias_consumed_by_factor_gyro": True,
                "vqf_bias_sigma_preserved": True,
                "final_gap_covariance_rad2": self._covariance[node].tolist(),
                "gap_covariance_is_total_orientation_covariance": False,
                "observed_span_uncertainty_inflation": quality.covariance_inflation,
                "boot_transition_uncertainty_floor_events": int(np.count_nonzero(np.diff(boot) != 0)),
                "capture_wide_calibration_posterior_live": (
                    self._calibration_owner is not None
                ),
                "calibration_prediction": calibration_prediction,
                "vqf_tilt_diagnostic": {
                    "schema": "biospur-c2-vqf-tilt-raw-diagnostic-v1",
                    "source": "PERSISTENT_VQF_FULL_STATE_PLUS_ACTION00_GRAVITY_NORM",
                    "trust_policy_issued": False,
                    "source_binding_digest": source_binding_digest,
                    "common_clock_binding": "REQUIRED_DOWNSTREAM_NOT_PRESENT_IN_ORIENTATION_OWNER",
                    "production_ready": False,
                    "runtime_provenance_digest": (
                        None if self._tilt_diagnostic_provenance is None
                        else self._tilt_diagnostic_provenance.digest
                    ),
                },
                "calibration_snapshot_semantic_sha256": (
                    None
                    if self._calibration_owner is None
                    else calibration_out[node]["semantic_sha256"]
                ),
            }
        return OrientedAction(
            action=action.action,
            chronological_index=action.chronological_index,
            time_us_by_node=time_out,
            derived_boot_epoch_by_node=boot_out,
            contiguous_span_id_by_node=span_out,
            acc_mps2_by_node=acc_out,
            gyro_rads_by_node=gyro_out,
            quat_world_sensor_wxyz_by_node=quat_out,
            gap_only_orientation_covariance_rad2_by_node=cov_out,
            vqf_residual_bias_rad_s_by_node=residual_bias_out,
            vqf_residual_bias_sigma_rad_s_by_node=residual_bias_sigma_out,
            vqf_rest_detected_by_node=rest_out,
            audit={
                "schema": "biospur-c2-continuous-vqf-action-v1",
                "action": action.action,
                "chronological_index": action.chronological_index,
                "nodes": node_audit,
                "episode_boundary_reset": False,
                "new_yaw_gauge": False,
            },
            calibration_posterior_by_node=calibration_out,
            imu_sample_sequence_by_node=_readonly_array_map(sequence_out),
            raw_start_offset_by_node=_readonly_array_map(raw_start_out),
            raw_end_offset_by_node=_readonly_array_map(raw_end_out),
            raw_sample_index_by_node=_readonly_array_map(raw_sample_out),
            vqf_relative_rest_deviation_by_node=_readonly_array_map(relative_rest_out),
            acceleration_norm_residual_mps2_by_node=_readonly_array_map(acceleration_norm_residual_out),
            world_tilt_innovation_rad_by_node=_readonly_array_map(world_tilt_innovation_out),
            vqf_tilt_diagnostic_provenance_digest=(
                None if self._tilt_diagnostic_provenance is None
                else self._tilt_diagnostic_provenance.digest
            ),
            vqf_tilt_source_binding_digest_by_node=MappingProxyType(dict(tilt_source_binding_out)),
            vqf_tilt_diagnostic_provenance=self._tilt_diagnostic_provenance,
            vqf_tilt_source_binding_payload_by_node=MappingProxyType(dict(tilt_source_payload_out)),
        )

    def audit(self) -> dict[str, Any]:
        return {
            "schema": "biospur-c2-continuous-vqf-capture-state-v1",
            "vqf_version": "2.0.1",
            "vqf_instances_total": len(self.nodes),
            "vqf_instances_per_node": 1,
            "magnetometer_used": False,
            "action_order": list(self._action_order),
            "episode_reset_count": 0,
            "new_yaw_gauge_count": 0,
            "gap_policy": "NO_UPDATE_PLUS_COVARIANCE_GROWTH;NO_GAP_CONCATENATION",
            "gap_covariance_semantics": "GAP_ONLY_DIAGNOSTIC;MUST_NOT_BE_USED_AS_TOTAL_FIT_WEIGHT",
            "bias_interaction": "INITIAL_MEDIAN_WITH_NONZERO_COVARIANCE_THEN_VQF_RESIDUAL_BIAS;VQF_BIAS_AND_BIASSIGMA_PRESERVED",
            "events": list(self._events),
            "capture_wide_calibration_posterior": (
                None
                if self._calibration_owner is None
                else self._calibration_owner.audit()
            ),
        }


def _continuous_vqf_state_from_validated_action00_authority(
    initial_stochastic_state: Mapping[str, Any], *,
    execution_guard: Any,
    sample_period_s: float,
    unknown_boot_orientation_sigma_rad: float,
    unknown_unusable_episode_orientation_sigma_rad: float,
    calibration_settings: Mapping[str, Any],
    validated_runtime_authority: Mapping[str, Any],
) -> ContinuousVQFState:
    """Issue diagnostics only after the Action00 runtime validates its pinned authority.

    The provenance capability remains private to this module; the role-scoped
    runtime can provide only the already validated immutable payload.
    """
    return ContinuousVQFState(
        initial_stochastic_state,
        execution_guard=execution_guard,
        sample_period_s=sample_period_s,
        unknown_boot_orientation_sigma_rad=unknown_boot_orientation_sigma_rad,
        unknown_unusable_episode_orientation_sigma_rad=(
            unknown_unusable_episode_orientation_sigma_rad
        ),
        calibration_settings=calibration_settings,
        tilt_diagnostic_runtime_authority=validated_runtime_authority,
        _tilt_provenance_capability=_RUNTIME_VQF_TILT_PROVENANCE_CAPABILITY,
    )
