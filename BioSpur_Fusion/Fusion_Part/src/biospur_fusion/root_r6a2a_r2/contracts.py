"""Root-R6A2A-R2 authority, accounting, health, and state contracts.

This module contains the only public objects allowed to cross the synthetic
injector/estimator boundary.  The estimator consumes :class:`EstimatorInput`
and returns :class:`EstimatorOutput`; labels used to score a run live in a
separate object and are never reachable from those two objects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

import numpy as np

from biospur_fusion.imu.preintegration import ImuSample
from biospur_fusion.root_r6a0.body import KeyframeState, StaticCalibration
from biospur_fusion.root_r6a2a.contracts import ALL_NODES, UnifiedHardwareRegistry


AUTHORITY_SCHEMA = "biospur-root-r6a2a-r2-authority-v1"
RNG_DERIVATION_VERSION = "sha256-seedsequence-v1"
SYNTHETIC_PROVENANCE = "ROOT_R6A2A_R2_SYNTHETIC_TEST_ONLY"


class ObservationStatus(str, Enum):
    RECEIVED_ACCEPTED = "RECEIVED_ACCEPTED"
    RECEIVED_REJECTED = "RECEIVED_REJECTED"
    EXPECTED_BUT_MISSING = "EXPECTED_BUT_MISSING"
    LATE = "LATE"
    NOT_SCHEDULED = "NOT_SCHEDULED"
    BOOT_EPOCH_INVALID = "BOOT_EPOCH_INVALID"
    CLOCK_INVALID = "CLOCK_INVALID"
    NODE_OFFLINE = "NODE_OFFLINE"


class HealthState(str, Enum):
    HEALTHY = "HEALTHY"
    SUSPECT = "SUSPECT"
    DEGRADED = "DEGRADED"
    ISOLATED = "ISOLATED"
    RECOVERING = "RECOVERING"
    REQUALIFYING = "REQUALIFYING"
    CONTROLLED_REENTRY = "CONTROLLED_REENTRY"


class DegradedMode(str, Enum):
    NORMAL = "NORMAL"
    SUSPECT = "SUSPECT"
    SINGLE_UWB_LINK_DEGRADED = "SINGLE_UWB_LINK_DEGRADED"
    SINGLE_ANCHOR_ISOLATED = "SINGLE_ANCHOR_ISOLATED"
    SINGLE_TAG_UWB_DEGRADED = "SINGLE_TAG_UWB_DEGRADED"
    SINGLE_IMU_DEGRADED = "SINGLE_IMU_DEGRADED"
    SINGLE_NODE_IMU_AND_UWB_DEGRADED = "SINGLE_NODE_IMU_AND_UWB_DEGRADED"
    MULTI_ANCHOR_GEOMETRY_DEGRADED = "MULTI_ANCHOR_GEOMETRY_DEGRADED"
    GLOBAL_UWB_OUTAGE = "GLOBAL_UWB_OUTAGE"
    AMBIGUOUS_MODEL_OR_SLIP_MISMATCH = "AMBIGUOUS_MODEL_OR_SLIP_MISMATCH"
    RECOVERING = "RECOVERING"
    REQUALIFYING = "REQUALIFYING"
    CONTROLLED_REENTRY = "CONTROLLED_REENTRY"


@dataclass(frozen=True)
class FaultWindow:
    start_step: int
    end_step: int
    persistent_configuration: bool = False

    def active(self, step: int) -> bool:
        return self.start_step <= step <= self.end_step


@dataclass(frozen=True)
class FaultInjectionTruth:
    """Private injector/evaluator data.  Never put this in EstimatorInput."""

    kind: str
    target_node: str = "BSFEC35"
    target_tag: str = "BSFEC35"
    target_anchor: int = 2
    window: FaultWindow = FaultWindow(3, 7)
    magnitude: float = 1.0
    expected_scope: str = "none"
    allowed_attributions: tuple[str, ...] = ("NO_FAULT",)
    expected_modes: tuple[str, ...] = ("NORMAL",)
    forbidden_actions: tuple[str, ...] = ("REAL_BODY_UPDATE",)

    def __post_init__(self) -> None:
        if self.target_node not in ALL_NODES or self.target_tag not in ALL_NODES:
            raise ValueError("private injection target is not a canonical node")
        if self.target_anchor not in range(8):
            raise ValueError("private anchor target must be 0..7")


@dataclass(frozen=True)
class ScenarioDefinition:
    scenario_id: str
    category: str
    master_seed: int
    duration_s: float
    step_s: float
    geometry: str
    low_motion: bool
    private_truth: FaultInjectionTruth
    observable_evidence: tuple[str, ...]
    acceptance_metric: str
    trajectory_variant: int = 0

    @property
    def step_count(self) -> int:
        return int(round(self.duration_s / self.step_s))


@dataclass(frozen=True)
class UwbMeasurement:
    event_uid: str
    tag_id: str
    anchor_id: int
    measurement_time_s: float
    availability_time_s: float
    range_m: float
    sigma_m: float
    boot_epoch: int = 1
    clock_valid: bool = True

    @property
    def link_id(self) -> str:
        return f"{self.tag_id}:{self.anchor_id}"


@dataclass(frozen=True)
class ScheduledObservation:
    schedule_uid: str
    modality: str
    node_id: str
    tag_id: str | None
    anchor_id: int | None
    expected_time_s: float
    deadline_s: float
    boot_epoch: int
    clock_valid: bool
    node_online: bool
    configured: bool = True

    @property
    def persistent_id(self) -> str:
        if self.modality == "IMU":
            return self.node_id
        return f"{self.tag_id}:{self.anchor_id}"


@dataclass(frozen=True)
class ObservationAccounting:
    schedule_uid: str
    modality: str
    persistent_id: str
    status: ObservationStatus
    event_uid: str | None
    evidence: str


@dataclass(frozen=True)
class EstimatorOptions:
    uwb_enabled: bool = True
    health_accommodation_enabled: bool = True
    bias_updates_enabled: bool = True
    bias_jacobians_enabled: bool = True
    recovery_ramp_enabled: bool = True
    directional_geometry_enabled: bool = True
    # Diagnostic controls default to the repaired estimator.  The legacy
    # switch exists solely to reproduce the failed R2 covariance equation.
    legacy_explicit_weak_inflation_enabled: bool = False
    missing_observation_covariance_enabled: bool = True
    robust_weighting_enabled: bool = True
    recovery_covariance_accommodation_enabled: bool = True
    preintegration_covariance_enabled: bool = True
    bias_random_walk_covariance_enabled: bool = True
    invalid_pelvis_covariance_enabled: bool = True
    numerical_covariance_floor_enabled: bool = True
    independent_directional_projector: bool = False
    covariance_update_form: str = "JOSEPH"
    excluded_preintegration_covariance_node: str | None = None


@dataclass(frozen=True)
class EstimatorInput:
    """Complete input authority for one step; contains no scoring labels."""

    step_index: int
    interval_start_s: float
    interval_end_s: float
    imu_streams: Mapping[str, tuple[ImuSample, ...]]
    uwb_measurements: tuple[UwbMeasurement, ...]
    expected_schedule: tuple[ScheduledObservation, ...]
    calibration: StaticCalibration
    registry: UnifiedHardwareRegistry
    geometry_class: str
    options: EstimatorOptions


@dataclass(frozen=True)
class AffectedScope:
    modality: str
    scope: str
    entities: tuple[str, ...]
    confidence: str


@dataclass(frozen=True)
class EstimatorOutput:
    state: KeyframeState
    accounting: tuple[ObservationAccounting, ...]
    health_snapshot: Mapping[str, Mapping[str, str]]
    health_transitions: tuple[Mapping[str, Any], ...]
    affected_scope: AffectedScope
    mode: DegradedMode
    attribution: str
    residual_evidence: Mapping[str, Any]
    covariance_evidence: Mapping[str, Any]
    bias_evidence: Mapping[str, Any]


@dataclass(frozen=True)
class EvaluationResult:
    """Post-run comparison; this is the sole object joining output and labels."""

    scenario_id: str
    output_digest: str
    attribution_allowed: bool
    expected_modes_seen: bool
    metrics: Mapping[str, Any]


@dataclass
class HealthChannel:
    modality: str
    entity_id: str
    state: HealthState = HealthState.HEALTHY
    bad_streak: int = 0
    good_streak: int = 0
    had_degraded_history: bool = False
    transitions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def weight(self) -> float:
        return {
            HealthState.HEALTHY: 1.0,
            HealthState.SUSPECT: 0.50,
            HealthState.DEGRADED: 0.20,
            HealthState.ISOLATED: 0.0,
            HealthState.RECOVERING: 0.15,
            HealthState.REQUALIFYING: 0.35,
            HealthState.CONTROLLED_REENTRY: 0.65,
        }[self.state]

    def update(self, bad: bool, time_s: float, evidence: str, hard: bool = False) -> None:
        before = self.state
        if bad:
            self.good_streak = 0
            self.bad_streak += 1
            if hard or self.bad_streak >= 3:
                self.state = HealthState.ISOLATED
                self.had_degraded_history = True
            elif self.bad_streak >= 2:
                self.state = HealthState.DEGRADED
                self.had_degraded_history = True
            else:
                self.state = HealthState.SUSPECT
        else:
            self.bad_streak = 0
            self.good_streak += 1
            if self.state is HealthState.SUSPECT and not self.had_degraded_history:
                if self.good_streak >= 2:
                    self.state = HealthState.HEALTHY
                    self.good_streak = 0
            elif self.state in (HealthState.DEGRADED, HealthState.ISOLATED):
                if self.good_streak >= 2:
                    self.state = HealthState.RECOVERING
                    self.good_streak = 0
            elif self.state is HealthState.RECOVERING and self.good_streak >= 2:
                self.state = HealthState.REQUALIFYING
                self.good_streak = 0
            elif self.state is HealthState.REQUALIFYING and self.good_streak >= 2:
                self.state = HealthState.CONTROLLED_REENTRY
                self.good_streak = 0
            elif self.state is HealthState.CONTROLLED_REENTRY and self.good_streak >= 2:
                self.state = HealthState.HEALTHY
                self.good_streak = 0
                self.had_degraded_history = False
        if before is not self.state:
            self.transitions.append({
                "time_s": float(time_s), "modality": self.modality,
                "entity_id": self.entity_id, "from": before.value,
                "to": self.state.value, "evidence": evidence,
                "bad_streak": self.bad_streak, "good_streak": self.good_streak,
                "measurement_weight": self.weight,
            })


class HealthManager:
    """Persistent modality channels followed by one deterministic composition."""

    CHANNELS = (
        "imu_health", "uwb_tag_health", "uwb_anchor_health", "uwb_link_health",
        "clock_health", "model_consistency_health", "composite_node_health",
        "global_observability_health",
    )

    def __init__(self) -> None:
        self.channels: dict[tuple[str, str], HealthChannel] = {}

    def channel(self, modality: str, entity_id: str) -> HealthChannel:
        key = (modality, str(entity_id))
        if key not in self.channels:
            self.channels[key] = HealthChannel(*key)
        return self.channels[key]

    def update_modalities(
        self, evidence_rows: Sequence[tuple[str, str, bool, str, bool]], time_s: float
    ) -> None:
        # Sorting makes composition invariant to caller iteration order.
        for modality, entity, bad, evidence, hard in sorted(evidence_rows):
            self.channel(modality, entity).update(bool(bad), time_s, evidence, bool(hard))
        self._compose_nodes(time_s)

    def _compose_nodes(self, time_s: float) -> None:
        severity = {state: index for index, state in enumerate((
            HealthState.HEALTHY, HealthState.SUSPECT, HealthState.RECOVERING,
            HealthState.REQUALIFYING, HealthState.CONTROLLED_REENTRY,
            HealthState.DEGRADED, HealthState.ISOLATED,
        ))}
        for node in ALL_NODES:
            imu = self.channel("imu_health", node).state
            tag = self.channel("uwb_tag_health", node).state
            worst = max((imu, tag), key=severity.get)
            composite = self.channel("composite_node_health", node)
            before = composite.state
            composite.state = worst
            if before is not worst:
                composite.transitions.append({
                    "time_s": float(time_s), "modality": "composite_node_health",
                    "entity_id": node, "from": before.value, "to": worst.value,
                    "evidence": f"COMPOSED_IMU={imu.value};UWB={tag.value}",
                    "bad_streak": 0, "good_streak": 0,
                    "measurement_weight": composite.weight,
                })

    def snapshot(self) -> dict[str, dict[str, str]]:
        return {
            modality: {
                entity: channel.state.value
                for (kind, entity), channel in sorted(self.channels.items()) if kind == modality
            }
            for modality in self.CHANNELS
        }

    def transitions(self) -> tuple[Mapping[str, Any], ...]:
        rows = [row for channel in self.channels.values() for row in channel.transitions]
        return tuple(sorted(rows, key=lambda row: (row["time_s"], row["modality"], row["entity_id"], row["to"])))


class StateLayout:
    """Audited 123-state ordering and state-block lookup helpers."""

    def __init__(self, joint_ids: Sequence[str], node_ids: Sequence[str]):
        self.joint_ids = tuple(joint_ids)
        self.node_ids = tuple(node_ids)
        if len(self.joint_ids) != 9 or len(self.node_ids) != 10:
            raise ValueError("R6A2A-R2 requires nine joints and ten nodes")

    @property
    def dimension(self) -> int:
        return 123

    def joint_orientation(self, joint: str) -> slice:
        i = self.joint_ids.index(joint)
        return slice(9 + 3 * i, 12 + 3 * i)

    def joint_rate(self, joint: str) -> slice:
        i = self.joint_ids.index(joint)
        return slice(36 + 3 * i, 39 + 3 * i)

    def gyro_bias(self, node: str) -> slice:
        i = self.node_ids.index(node)
        return slice(63 + 3 * i, 66 + 3 * i)

    def accel_bias(self, node: str) -> slice:
        i = self.node_ids.index(node)
        return slice(93 + 3 * i, 96 + 3 * i)


def mixed_unit_initial_covariance(
    layout: StateLayout,
    root_position_axis_standard_deviations_m: Sequence[float] = (0.060, 0.060, 0.060),
) -> tuple[np.ndarray, dict[str, Any]]:
    root_axes = np.asarray(root_position_axis_standard_deviations_m, float)
    if root_axes.shape != (3,) or not np.isfinite(root_axes).all() or np.any(root_axes <= 0.0):
        raise ValueError("root-position axis standard deviations must be three positive finite values")
    standard_deviations = {
        "root_position_component_rms_m": float(np.sqrt(np.mean(np.square(root_axes)))),
        "root_orientation_rad": 0.010,
        "root_velocity_mps": 0.080,
        "joint_orientation_rad": 0.050,
        "joint_rate_rad_s": 0.120,
        "gyro_bias_rad_s": 0.005,
        "accelerometer_bias_mps2": 0.040,
    }
    p = np.zeros((layout.dimension, layout.dimension))
    p[0:3, 0:3] = np.diag(np.square(root_axes))
    blocks = (
        (slice(3, 6), "root_orientation_rad"),
        (slice(6, 9), "root_velocity_mps"), (slice(9, 36), "joint_orientation_rad"),
        (slice(36, 63), "joint_rate_rad_s"), (slice(63, 93), "gyro_bias_rad_s"),
        (slice(93, 123), "accelerometer_bias_mps2"),
    )
    for block, name in blocks:
        p[block, block] = np.eye(block.stop - block.start) * standard_deviations[name] ** 2
    return p, {
        "schema": "biospur-root-r6a2a-r2-mixed-unit-covariance-v2",
        "synthetic_only": True,
        "standard_deviations": standard_deviations,
        "root_position_axis_standard_deviations_m": root_axes.tolist(),
        "root_position_isotropic": bool(np.all(root_axes == root_axes[0])),
        "root_position_covariance_source": "declared synthetic initializer axis-error second moments",
        "one_scalar_reused_across_mixed_units": False,
    }
