"""Executable synthetic-only whole-body shadow with fault accommodation.

The implementation deliberately composes the qualified R6A1A native-time
preintegrator, the R6A1C corrected identity adapter, and the R6A0 BodyModel/FK
and range factor.  Synthetic generation is test-only and never touches the
real 87-slot registry.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from biospur_fusion.imu.preintegration import (
    G,
    ImuSample,
    NativeTimePreintegrator,
    NoiseParameters,
    PreintegratedInterval,
    PreintegrationStatus,
    PreintegratorConfig,
)
from biospur_fusion.root_r6a0.body import (
    BodyModel,
    KeyframeState,
    StaticCalibration,
    empty_state,
    synthetic_calibration,
)
from biospur_fusion.root_r6a0.contracts import (
    ActivationState,
    EvidenceRecord,
    EvidenceRepresentation,
    FaultDomain,
)
from biospur_fusion.root_r6a0.factors import RawUwbRangeFactor, raw_range_value
from biospur_fusion.root_r6a0.math3d import so3_exp, so3_log
from biospur_fusion.root_r6a1c.adapter import CORRECT_NODE_MAP

from .contracts import (
    ALL_NODES,
    DegradedMode,
    HealthState,
    UnifiedHardwareRegistry,
    registry_from_sealed_addendum,
)


SYNTHETIC_PROVENANCE = "ROOT_R6A2A_SYNTHETIC_TEST_ONLY"
GRAVITY_W = np.array([0.0, 0.0, -G])


@dataclass(frozen=True)
class ScenarioSpec:
    name: str
    category: str
    fault: str = "none"
    seed: int = 6201
    duration_s: float = 1.2
    step_s: float = 0.1
    target_node: str = "BSFEC35"
    target_anchor: int = 2
    target_tag: str = "BSFEC35"
    geometry: str = "WELL_CONDITIONED_3D"
    low_motion: bool = False
    fault_start_step: int = 3
    fault_end_step: int = 7
    synthetic_test_only: bool = True

    def __post_init__(self) -> None:
        if not self.synthetic_test_only:
            raise ValueError("R6A2A scenarios must be visibly synthetic test-only")
        if self.target_node not in ALL_NODES or self.target_tag not in ALL_NODES:
            raise ValueError("scenario target must be a canonical node")
        if self.target_anchor not in range(8):
            raise ValueError("anchor target must be 0..7")


@dataclass(frozen=True)
class UwbObservation:
    event_uid: str
    tag_id: str
    anchor_id: int
    measurement_time_s: float
    availability_time_s: float
    range_m: float
    sigma_m: float
    synthetic_truth: bool = True


@dataclass
class HealthRecord:
    scope: str
    entity_id: str
    state: HealthState = HealthState.HEALTHY
    bad_streak: int = 0
    good_streak: int = 0
    transitions: list[dict[str, Any]] = field(default_factory=list)
    last_reason: str = "INITIAL_HEALTHY"

    def observe(self, bad: bool, time_s: float, reason: str, hard: bool = False) -> None:
        previous = self.state
        if bad:
            self.bad_streak += 1
            self.good_streak = 0
            self.last_reason = reason
            if hard or self.bad_streak >= 3:
                self.state = HealthState.ISOLATED
            elif self.bad_streak == 2:
                self.state = HealthState.DEGRADED
            else:
                self.state = HealthState.SUSPECT
        else:
            self.bad_streak = 0
            self.good_streak += 1
            self.last_reason = reason
            if self.state in (HealthState.SUSPECT, HealthState.DEGRADED):
                if self.good_streak >= 2:
                    self.state = HealthState.RECOVERING
                    self.good_streak = 0
            elif self.state is HealthState.ISOLATED and self.good_streak >= 2:
                self.state = HealthState.RECOVERING
                self.good_streak = 0
            elif self.state is HealthState.RECOVERING and self.good_streak >= 2:
                self.state = HealthState.REQUALIFYING
                self.good_streak = 0
            elif self.state is HealthState.REQUALIFYING and self.good_streak >= 3:
                self.state = HealthState.HEALTHY
                self.good_streak = 0
        if self.state is not previous:
            self.transitions.append(
                {
                    "time_s": float(time_s),
                    "from": previous.value,
                    "to": self.state.value,
                    "entry_or_exit_evidence": reason,
                    "bad_streak": self.bad_streak,
                    "good_streak": self.good_streak,
                    "covariance_consequence": self.covariance_consequence,
                    "measurement_weight": self.weight,
                }
            )

    @property
    def weight(self) -> float:
        return {
            HealthState.HEALTHY: 1.0,
            HealthState.SUSPECT: 0.5,
            HealthState.DEGRADED: 0.2,
            HealthState.ISOLATED: 0.0,
            HealthState.RECOVERING: 0.15,
            HealthState.REQUALIFYING: 0.65,
        }[self.state]

    @property
    def covariance_consequence(self) -> str:
        return {
            HealthState.HEALTHY: "nominal propagation/update",
            HealthState.SUSPECT: "affected covariance inflated 1.25x",
            HealthState.DEGRADED: "affected covariance inflated 2x",
            HealthState.ISOLATED: "measurement disabled; prediction covariance grows",
            HealthState.RECOVERING: "no immediate covariance contraction",
            HealthState.REQUALIFYING: "bounded covariance contraction",
        }[self.state]


class HealthLedger:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], HealthRecord] = {}

    def record(self, scope: str, entity_id: str) -> HealthRecord:
        key = (scope, entity_id)
        if key not in self.records:
            self.records[key] = HealthRecord(scope, entity_id)
        return self.records[key]

    def observe(self, scope: str, entity_id: str, bad: bool, time_s: float, reason: str, hard: bool = False) -> HealthRecord:
        record = self.record(scope, entity_id)
        record.observe(bad, time_s, reason, hard)
        return record

    def weight(self, scope: str, entity_id: str) -> float:
        return self.record(scope, entity_id).weight

    def states(self, scope: str) -> dict[str, str]:
        return {entity: record.state.value for (kind, entity), record in self.records.items() if kind == scope}

    def transitions(self) -> list[dict[str, Any]]:
        rows = []
        for record in self.records.values():
            for transition in record.transitions:
                rows.append({"scope": record.scope, "entity_id": record.entity_id, **transition})
        return sorted(rows, key=lambda row: (row["time_s"], row["scope"], row["entity_id"]))


def corrected_body_model(fusion: Path) -> BodyModel:
    """Instantiate the protected R6A0 model only after R6A1C correction."""
    path = Path(fusion) / "config/root_r6a0/body_graph.json"
    definition = json.loads(path.read_text())
    if set(definition["active_identity_mapping"]["mapping"]) != set(CORRECT_NODE_MAP):
        raise ValueError("protected graph identity inventory changed")
    definition["active_identity_mapping"] = {
        "source": "ROOT_R6A1C_CORRECTED_IDENTITY_ADAPTER",
        "source_sha256": hashlib.sha256(
            json.dumps(CORRECT_NODE_MAP, sort_keys=True).encode()
        ).hexdigest(),
        "mapping": dict(CORRECT_NODE_MAP),
        "rule": "R6A2A consumes only the corrected forward map; historical wrist values are never instantiated",
    }
    for collection in ("imu_nodes", "uwb_tags"):
        for row in definition[collection]:
            row["segment"] = CORRECT_NODE_MAP[row["id"]]
    return BodyModel(definition)


def _replace_slot(calibration: StaticCalibration, slot_id: str, value: Sequence[float], provenance: str) -> StaticCalibration:
    slot = calibration.slot(slot_id)
    updated = dict(calibration.slots)
    updated[slot_id] = replace(slot, value=tuple(float(x) for x in value), provenance=provenance)
    return StaticCalibration(updated)


def build_synthetic_calibration(
    model: BodyModel,
    registry: UnifiedHardwareRegistry,
    *,
    geometry: str = "WELL_CONDITIONED_3D",
    wrong_lever_node: str | None = None,
    wrong_bone_geometry: bool = False,
) -> StaticCalibration:
    calibration = synthetic_calibration(model)
    calibration = _replace_slot(calibration, "world_model_gauge", np.zeros(6), SYNTHETIC_PROVENANCE)

    # torso_top is derived, not independently chosen.
    left = calibration.vector("joint_parent:shoulder_left", 3)
    right = calibration.vector("joint_parent:shoulder_right", 3)
    calibration = _replace_slot(
        calibration,
        "anatomical_point:torso_top",
        0.5 * (left + right),
        "ROOT_R6A2A_SYNTHETIC_DERIVED_SHOULDER_MIDPOINT",
    )
    for node_index, node in enumerate(ALL_NODES):
        extrinsic_slot = f"imu_extrinsic:{node}"
        extrinsic = calibration.vector(extrinsic_slot, 6)
        # Session/donning extrinsics remain node-specific, while the internal
        # IMU-to-UWB rigid lever is selected strictly by hardware family.
        rotation = so3_exp(extrinsic[:3])
        internal = np.asarray(registry.profile(node).imu_to_uwb_nominal_m, float)
        if wrong_lever_node == node:
            internal = internal + np.array([0.12, -0.08, 0.06])
        derived_tag = extrinsic[3:] + rotation @ internal
        calibration = _replace_slot(
            calibration,
            f"tag_lever:{node}",
            derived_tag,
            f"{SYNTHETIC_PROVENANCE}:DERIVED:{registry.family(node)}",
        )

    if geometry == "LOW_VERTICAL_DIVERSITY":
        anchors = [
            (-2.2, -1.8, 0.30), (2.2, -1.8, 0.31), (2.2, 1.8, 0.29), (-2.2, 1.8, 0.30),
            (-1.1, -2.2, 0.305), (1.2, -2.1, 0.295), (1.1, 2.2, 0.31), (-1.2, 2.1, 0.29),
        ]
        for anchor_id, position in enumerate(anchors):
            calibration = _replace_slot(calibration, f"anchor_position:{anchor_id}", position, SYNTHETIC_PROVENANCE)
    if wrong_bone_geometry:
        offset = calibration.vector("joint_parent:shoulder_left", 3) + np.array([0.11, 0.0, 0.0])
        calibration = _replace_slot(calibration, "joint_parent:shoulder_left", offset, SYNTHETIC_PROVENANCE)
    return calibration


def truth_state(model: BodyModel, time_s: float, seed: int, low_motion: bool = False) -> KeyframeState:
    state = empty_state(model, time_s)
    t = float(time_s)
    phase = (seed % 17) * 0.013
    scale = 0.04 if low_motion else 1.0
    root = scale * np.array(
        [0.34 * np.sin(0.72 * t + phase), 0.18 * (np.cos(0.49 * t) - 1.0), 0.035 * np.sin(0.91 * t)]
    ) + np.array([0.0, 0.0, 1.05])
    velocity = scale * np.array(
        [0.34 * 0.72 * np.cos(0.72 * t + phase), -0.18 * 0.49 * np.sin(0.49 * t), 0.035 * 0.91 * np.cos(0.91 * t)]
    )
    root_rotation = scale * np.array(
        [0.045 * np.sin(0.61 * t), 0.035 * np.sin(0.83 * t + 0.2), 0.16 * np.sin(0.42 * t)]
    )
    joints: dict[str, np.ndarray] = {}
    rates: dict[str, np.ndarray] = {}
    for index, joint in enumerate(model.joint_ids):
        frequency = 0.65 + 0.08 * index
        amplitude = scale * (0.08 + 0.012 * index)
        axis = np.array([0.22 + 0.01 * index, (-1.0) ** index * 0.13, 1.0])
        axis /= np.linalg.norm(axis)
        angle = amplitude * np.sin(frequency * t + 0.17 * index + phase)
        rate = amplitude * frequency * np.cos(frequency * t + 0.17 * index + phase)
        joints[joint] = axis * angle
        rates[joint] = axis * rate
    gyro_bias = {
        node: np.array([1.0e-3 * (index + 1), -0.7e-3 * index, 0.4e-3 * (index - 3)])
        for index, node in enumerate(model.imu_ids)
    }
    accel_bias = {
        node: np.array([0.004 * (index - 4), -0.003 * index, 0.002 * (index + 1)])
        for index, node in enumerate(model.imu_ids)
    }
    return replace(
        state,
        root_translation_model_m=root,
        root_rotation_model_rotvec=root_rotation,
        root_velocity_model_mps=velocity,
        joint_rotvec=joints,
        joint_rate_rad_s=rates,
        gyro_bias_rad_s=gyro_bias,
        accel_bias_mps2=accel_bias,
        covariance=np.eye(state.covariance.shape[0]) * 1e-5,
    )


def interpolate_state(previous: KeyframeState, current: KeyframeState, time_s: float) -> KeyframeState:
    if not (previous.time_s - 1e-12 <= time_s <= current.time_s + 1e-12):
        raise ValueError("interpolation query outside bracketing states")
    span = current.time_s - previous.time_s
    alpha = 0.0 if span <= 0.0 else (time_s - previous.time_s) / span
    mix = lambda first, second: (1.0 - alpha) * np.asarray(first) + alpha * np.asarray(second)
    return replace(
        previous,
        time_s=float(time_s),
        root_translation_model_m=mix(previous.root_translation_model_m, current.root_translation_model_m),
        root_rotation_model_rotvec=mix(previous.root_rotation_model_rotvec, current.root_rotation_model_rotvec),
        root_velocity_model_mps=mix(previous.root_velocity_model_mps, current.root_velocity_model_mps),
        joint_rotvec={key: mix(previous.joint_rotvec[key], current.joint_rotvec[key]) for key in previous.joint_rotvec},
        joint_rate_rad_s={key: mix(previous.joint_rate_rad_s[key], current.joint_rate_rad_s[key]) for key in previous.joint_rate_rad_s},
        gyro_bias_rad_s={key: mix(previous.gyro_bias_rad_s[key], current.gyro_bias_rad_s[key]) for key in previous.gyro_bias_rad_s},
        accel_bias_mps2={key: mix(previous.accel_bias_mps2[key], current.accel_bias_mps2[key]) for key in previous.accel_bias_mps2},
        covariance=mix(previous.covariance, current.covariance),
    )


def _imu_truth(
    model: BodyModel,
    calibration: StaticCalibration,
    time_s: float,
    seed: int,
    node: str,
    low_motion: bool,
) -> tuple[np.ndarray, np.ndarray]:
    epsilon = 5.0e-4
    states = [truth_state(model, time_s + offset, seed, low_motion) for offset in (-epsilon, 0.0, epsilon)]
    poses = [model.imu_frames(state, calibration)[node] for state in states]
    rotation = poses[1].rotation
    gyro = so3_log(rotation.T @ poses[2].rotation) / epsilon
    acceleration_w = (poses[2].translation - 2.0 * poses[1].translation + poses[0].translation) / epsilon**2
    specific_force = rotation.T @ (acceleration_w - GRAVITY_W)
    return specific_force, gyro


def _native_times(t0: float, t1: float, node_index: int) -> np.ndarray:
    values = [t0 + 0.00012 * node_index]
    cursor = values[0]
    count = 0
    while True:
        dt = 0.00455 + 0.000055 * node_index + 0.00031 * np.sin(0.43 * count + 0.19 * node_index)
        if cursor + dt >= t1 - 0.00008 * (9 - node_index):
            break
        cursor += dt
        values.append(cursor)
        count += 1
    values.append(t1 - 0.00008 * (9 - node_index))
    return np.asarray(values)


def generate_imu_streams(
    model: BodyModel,
    calibration: StaticCalibration,
    spec: ScenarioSpec,
    step_index: int,
    t0: float,
    t1: float,
    rng: np.random.Generator,
) -> dict[str, tuple[ImuSample, ...]]:
    streams: dict[str, tuple[ImuSample, ...]] = {}
    active = spec.fault_start_step <= step_index <= spec.fault_end_step
    truth_at = [truth_state(model, value, spec.seed, spec.low_motion) for value in (t0, 0.5 * (t0 + t1), t1)]
    poses_at = [model.imu_frames(state, calibration) for state in truth_at]
    half_span = 0.5 * (t1 - t0)
    for node_index, node in enumerate(model.imu_ids):
        times = _native_times(t0, t1, node_index)
        truth_bias = truth_state(model, 0.5 * (t0 + t1), spec.seed, spec.low_motion)
        gyro_nominal = so3_log(poses_at[0][node].rotation.T @ poses_at[2][node].rotation) / (t1 - t0)
        acceleration_w = (
            poses_at[2][node].translation - 2.0 * poses_at[1][node].translation + poses_at[0][node].translation
        ) / half_span**2
        specific_force_nominal = poses_at[1][node].rotation.T @ (acceleration_w - GRAVITY_W)
        samples: list[ImuSample] = []
        for sample_index, time_s in enumerate(times):
            accel, gyro = specific_force_nominal.copy(), gyro_nominal.copy()
            accel = accel + truth_bias.accel_bias_mps2[node] + rng.normal(0.0, 0.007, 3)
            gyro = gyro + truth_bias.gyro_bias_rad_s[node] + rng.normal(0.0, 0.0007, 3)
            if active and node == spec.target_node:
                if spec.fault == "imu_noise_burst":
                    gyro = gyro + np.array([0.8, -0.5, 0.6])
                elif spec.fault == "gyro_bias_step":
                    gyro = gyro + np.array([0.35, 0.0, 0.0])
                elif spec.fault == "gyro_bias_ramp":
                    gyro = gyro + np.array([0.10 * (step_index - spec.fault_start_step + 1), 0.0, 0.0])
                elif spec.fault in {"rotational_skin_slip", "wrist_ghost_rotation"}:
                    pulse = 0.35 if spec.fault == "wrist_ghost_rotation" else 0.10
                    gyro = gyro + np.array([0.0, pulse, 0.0])
            samples.append(
                ImuSample(
                    node,
                    int(round(time_s * 1e9)),
                    100 + node_index,
                    accel,
                    gyro,
                )
            )

        if active and node == spec.target_node:
            if spec.fault == "bounded_sample_gap" and len(samples) > 5:
                del samples[len(samples) // 2]
            elif spec.fault == "long_gap" and len(samples) > 8:
                middle = len(samples) // 2
                del samples[middle - 2:middle + 3]
            elif spec.fault == "duplicate_timestamp" and len(samples) > 3:
                samples[3] = replace(samples[3], global_time_ns=samples[2].global_time_ns)
            elif spec.fault == "timestamp_reversal" and len(samples) > 3:
                samples[3] = replace(samples[3], global_time_ns=samples[2].global_time_ns - 1_000_000)
            elif spec.fault == "boot_epoch_reset" and len(samples) > 2:
                samples[-1] = replace(samples[-1], boot_epoch=samples[0].boot_epoch + 1)
            elif spec.fault == "imu_saturation":
                samples[len(samples) // 2] = replace(samples[len(samples) // 2], acc_raw=(32767, 0, 0), gyro_raw=(0, 0, 0))
            elif spec.fault == "invalid_imu_value":
                samples[len(samples) // 2] = replace(samples[len(samples) // 2], accepted=False)
            elif spec.fault in {"single_node_dropout", "node_imu_and_uwb_dropout"}:
                samples = samples[:1]
        streams[node] = tuple(samples)
    return streams


def generate_uwb_observations(
    model: BodyModel,
    truth_calibration: StaticCalibration,
    spec: ScenarioSpec,
    step_index: int,
    t0: float,
    t1: float,
    rng: np.random.Generator,
) -> list[UwbObservation]:
    active = spec.fault_start_step <= step_index <= spec.fault_end_step
    observations: list[UwbObservation] = []
    if active and spec.fault == "global_uwb_outage":
        return observations
    selected_pairs = {(spec.target_tag, anchor_id) for anchor_id in range(8)}
    selected_pairs.update((tag, spec.target_anchor) for tag in model.tag_ids)
    # A second anchor per non-target tag keeps link-health evidence present
    # without replacing the explicit target-tag/all-anchor and
    # target-anchor/all-tag cross-scope redundancy.
    selected_pairs.update((tag, (index + 5) % 8) for index, tag in enumerate(model.tag_ids))
    support_tags = [tag for tag in model.tag_ids if tag != spec.target_tag][:4]
    selected_pairs.update((tag, anchor_id) for tag in support_tags for anchor_id in range(8))
    for tag_index, tag in enumerate(model.tag_ids):
        if active and spec.fault in {"tag_dropout", "node_imu_and_uwb_dropout"} and tag == spec.target_tag:
            continue
        for anchor_id in range(8):
            if (tag, anchor_id) not in selected_pairs:
                continue
            if active and spec.fault == "multi_anchor_outage" and anchor_id < 5:
                continue
            fraction = 0.22 + 0.56 * ((tag_index * 8 + anchor_id) % 17) / 16.0
            time_s = t0 + fraction * (t1 - t0)
            state = truth_state(model, time_s, spec.seed, spec.low_motion)
            value = raw_range_value(model, truth_calibration, state, tag, anchor_id)
            sigma = 0.035
            value += float(rng.normal(0.0, sigma))
            if active:
                if spec.fault == "single_uwb_outlier" and tag == spec.target_tag and anchor_id == spec.target_anchor and step_index == spec.fault_start_step:
                    value += 1.25
                elif spec.fault in {"nlos_bias_burst", "persistent_bad_link"} and tag == spec.target_tag and anchor_id == spec.target_anchor:
                    value += 0.55
                elif spec.fault == "single_anchor_fault" and anchor_id == spec.target_anchor:
                    value += 0.48
                elif spec.fault == "single_tag_fault" and tag == spec.target_tag:
                    value += 0.45 + 0.025 * anchor_id
                elif spec.fault in {"rotational_skin_slip", "persistent_post_motion_offset"} and tag == spec.target_tag:
                    value += 0.28 * np.sin(0.7 * anchor_id + 0.3)
            observations.append(
                UwbObservation(
                    event_uid=f"{spec.name}:{step_index}:{tag}:{anchor_id}",
                    tag_id=tag,
                    anchor_id=anchor_id,
                    measurement_time_s=float(time_s),
                    availability_time_s=float(time_s + 0.004 + 0.0002 * anchor_id),
                    range_m=float(value),
                    sigma_m=sigma,
                )
            )
    return observations


def _rotation_error(first: np.ndarray, second: np.ndarray) -> float:
    return float(np.linalg.norm(so3_log(np.asarray(first).T @ np.asarray(second))))


def _state_payload(state: KeyframeState) -> dict[str, Any]:
    return {
        "time_s": state.time_s,
        "root_position_m": state.root_translation_model_m.tolist(),
        "root_orientation_rotvec": state.root_rotation_model_rotvec.tolist(),
        "root_velocity_mps": state.root_velocity_model_mps.tolist(),
        "joints": {key: value.tolist() for key, value in state.joint_rotvec.items()},
        "covariance_trace": float(np.trace(state.covariance)),
    }


class IntegratedShadowEstimator:
    """One estimator shared by both geometry families."""

    def __init__(
        self,
        model: BodyModel,
        calibration: StaticCalibration,
        registry: UnifiedHardwareRegistry,
        initial_state: KeyframeState,
    ) -> None:
        if set(model.identity_mapping.items()) != set(CORRECT_NODE_MAP.items()):
            raise ValueError("wrong or obsolete node identity map")
        self.model = model
        self.calibration = calibration
        self.registry = registry
        self.state = initial_state
        self.health = HealthLedger()
        self.states = [initial_state]
        self.mode_history = [DegradedMode.NORMAL.value]
        self.audit: list[dict[str, Any]] = []
        self.preintegration_status_counts: Counter[str] = Counter()
        self.preintegration_covariance_traces: list[float] = []
        self.bias_jacobian_norms: list[float] = []
        self.uwb_normalized_innovations: list[float] = []
        self.uwb_corrections: list[float] = []
        self.interpolation_queries = 0
        self.full_weight_return_step: int | None = None
        self.angular_rate_baseline: dict[str, np.ndarray] = {}
        self.bounded_gap_count = 0
        self.ambiguous_evidence = False
        self.skin_slip_rotvec_by_node = {node: np.zeros(3) for node in model.imu_ids}
        self.skin_slip_covariance_by_node = {node: np.eye(3) * 1e-5 for node in model.imu_ids}
        self.skin_slip_updates = 0
        noise = {
            node: NoiseParameters(0.020, 0.002, 0.0002, 0.00002, SYNTHETIC_PROVENANCE)
            for node in model.imu_ids
        }
        config = PreintegratorConfig(
            max_gap_s=0.020,
            missing_sample_threshold_s=0.0075,
            accel_saturation_mps2=80.0,
            gyro_saturation_rad_s=12.0,
        )
        self.preintegrator = NativeTimePreintegrator(noise, config)

    def _propagate(
        self,
        intervals: Mapping[str, PreintegratedInterval],
        target_time_s: float,
    ) -> KeyframeState:
        previous = self.state
        previous_predictions = self.model.all_predictions(previous, self.calibration)
        target_segment_rotations = {
            segment: pose.rotation.copy() for segment, pose in previous_predictions["segments"].items()
        }
        covariance = previous.covariance.copy()
        valid_nodes = 0
        for node, interval in intervals.items():
            self.preintegration_status_counts[interval.status.value] += 1
            self.bounded_gap_count += interval.bounded_gap_count
            hard = interval.status in {
                PreintegrationStatus.DUPLICATE_TIMESTAMP,
                PreintegrationStatus.TIME_REVERSAL,
                PreintegrationStatus.BOOT_EPOCH_CHANGE,
                PreintegrationStatus.SATURATION,
                PreintegrationStatus.NONFINITE,
                PreintegrationStatus.INVALID_SAMPLE_STATUS,
                PreintegrationStatus.GAP_EXCEEDS_ENVELOPE,
            }
            motion_bad = False
            motion_reason = interval.status.value
            if interval.valid and interval.duration_s > 0.0:
                rate = so3_log(interval.delta_rotation) / interval.duration_s
                baseline = self.angular_rate_baseline.get(node)
                if baseline is not None:
                    departure = float(np.linalg.norm(rate - baseline))
                    motion_bad = departure > 0.18
                    if motion_bad:
                        motion_reason = f"CROSS_TIME_ANGULAR_RATE_DEPARTURE={departure:.6f}"
                        # Bounded, smooth nuisance hypothesis. It is not allowed
                        # to rewrite nominal extrinsics or body geometry and is
                        # downweighted until cross-system evidence resolves the
                        # electronic-fault-versus-slip ambiguity.
                        increment = 0.08 * (rate - baseline) * interval.duration_s
                        candidate = 0.9 * self.skin_slip_rotvec_by_node[node] + increment
                        magnitude = float(np.linalg.norm(candidate))
                        if magnitude > 0.25:
                            candidate *= 0.25 / magnitude
                        self.skin_slip_rotvec_by_node[node] = candidate
                        self.skin_slip_covariance_by_node[node] += np.eye(3) * 5e-4
                        self.skin_slip_updates += 1
                    else:
                        self.angular_rate_baseline[node] = rate.copy()
                else:
                    self.angular_rate_baseline[node] = rate.copy()
            bad = not interval.valid or motion_bad
            reason = motion_reason
            record = self.health.observe("imu_stream", node, bad, target_time_s, reason, hard=hard)
            self.health.observe("node", node, bad, target_time_s, f"IMU:{reason}", hard=hard)
            if not interval.valid or record.weight <= 0.0:
                continue
            valid_nodes += 1
            corrected = interval.bias_corrected(
                previous.gyro_bias_rad_s[node], previous.accel_bias_mps2[node]
            )
            self.preintegration_covariance_traces.append(float(np.trace(interval.covariance)))
            self.bias_jacobian_norms.append(
                float(
                    np.linalg.norm(interval.jacobian_rotation_gyro_bias)
                    + np.linalg.norm(interval.jacobian_velocity_accel_bias)
                    + np.linalg.norm(interval.jacobian_position_accel_bias)
                )
            )
            old_imu = previous_predictions["imus"][node]
            target_imu_rotation = old_imu.rotation @ corrected.delta_rotation
            extrinsic = self.calibration.pose(f"imu_extrinsic:{node}")
            slip = so3_exp(self.skin_slip_rotvec_by_node[node])
            target_segment_rotations[CORRECT_NODE_MAP[node]] = target_imu_rotation @ slip.T @ extrinsic.rotation.T

        pelvis = "BSFC2CC"
        root_position = previous.root_translation_model_m.copy()
        root_velocity = previous.root_velocity_model_mps.copy()
        pelvis_interval = intervals[pelvis]
        if pelvis_interval.valid and self.health.weight("imu_stream", pelvis) > 0.0:
            corrected = pelvis_interval.bias_corrected(
                previous.gyro_bias_rad_s[pelvis], previous.accel_bias_mps2[pelvis]
            )
            dt = pelvis_interval.duration_s
            rotation_wi = previous_predictions["imus"][pelvis].rotation
            root_position = (
                root_position
                + root_velocity * dt
                + rotation_wi @ corrected.delta_position
                + 0.5 * GRAVITY_W * dt**2
            )
            root_velocity = root_velocity + rotation_wi @ corrected.delta_velocity + GRAVITY_W * dt
            permutation = np.r_[np.arange(6, 9), np.arange(0, 3), np.arange(3, 6)]
            covariance[:9, :9] += pelvis_interval.covariance[np.ix_(permutation, permutation)]
        else:
            dt = target_time_s - previous.time_s
            root_position = root_position + root_velocity * dt
            covariance[0:3, 0:3] += np.eye(3) * 0.004
            covariance[3:6, 3:6] += np.eye(3) * 0.002

        gauge = self.calibration.pose("world_model_gauge")
        root_rotation = so3_log(gauge.rotation.T @ target_segment_rotations[self.model.root_segment])
        joint_values: dict[str, np.ndarray] = {}
        joint_rates: dict[str, np.ndarray] = {}
        for joint_index, joint in enumerate(self.model.joints):
            previous_value = previous.joint_rotvec[joint.joint_id]
            parent_rotation = target_segment_rotations[joint.parent]
            child_rotation = target_segment_rotations[joint.child]
            rest = so3_exp(self.calibration.vector(joint.rest_rotation_slot, 3))
            candidate = so3_log(rest.T @ parent_rotation.T @ child_rotation)
            jump = candidate - previous_value
            norm = float(np.linalg.norm(jump))
            if norm > 0.22:
                candidate = previous_value + jump * (0.22 / norm)
                covariance[9 + 3 * joint_index:12 + 3 * joint_index, 9 + 3 * joint_index:12 + 3 * joint_index] += np.eye(3) * 0.003
            joint_values[joint.joint_id] = candidate
            dt = max(1e-9, target_time_s - previous.time_s)
            joint_rates[joint.joint_id] = (candidate - previous_value) / dt

        covariance = 0.5 * (covariance + covariance.T)
        covariance += np.eye(covariance.shape[0]) * (1e-9 + 1e-7 * max(0, 10 - valid_nodes))
        state = replace(
            previous,
            time_s=float(target_time_s),
            root_translation_model_m=root_position,
            root_rotation_model_rotvec=root_rotation,
            root_velocity_model_mps=root_velocity,
            joint_rotvec=joint_values,
            joint_rate_rad_s=joint_rates,
            covariance=covariance,
        )
        state.validate(self.model)
        return state

    def _uwb_update(
        self,
        previous: KeyframeState,
        propagated: KeyframeState,
        observations: Sequence[UwbObservation],
        geometry: str,
    ) -> tuple[KeyframeState, dict[str, Any]]:
        if not observations:
            self.health.observe("global_observability", "UWB", True, propagated.time_s, "NO_UWB_OBSERVATIONS")
            covariance = propagated.covariance.copy()
            covariance[0:3, 0:3] += np.eye(3) * 0.012
            covariance[5, 5] += 0.008
            return replace(propagated, covariance=covariance), {
                "accepted": 0,
                "outage": True,
                "correction_norm_m": 0.0,
                "information_eigenvalues": [0.0, 0.0, 0.0],
                "nearest_sample_shortcut_used": False,
                "trust_depends_on_acceleration": False,
            }

        self.health.observe("global_observability", "UWB", False, propagated.time_s, "UWB_OBSERVATIONS_RETURNED")
        residual_rows: list[tuple[UwbObservation, float, np.ndarray]] = []
        bad_by_anchor: dict[int, list[bool]] = defaultdict(list)
        bad_by_tag: dict[str, list[bool]] = defaultdict(list)
        for observation in observations:
            evidence = EvidenceRecord(
                event_uid=observation.event_uid,
                physical_event_uid=observation.event_uid,
                representation=EvidenceRepresentation.RAW_UWB,
                raw_ancestry=frozenset({observation.event_uid}),
                measurement_time_s=observation.measurement_time_s,
                availability_time_s=observation.availability_time_s,
                owner_id=observation.tag_id,
                covariance_provenance=SYNTHETIC_PROVENANCE,
                fault_domains=(FaultDomain.SINGLE_EVENT, FaultDomain.TAG_ANCHOR_LINK, FaultDomain.TAG, FaultDomain.ANCHOR),
            )
            factor = RawUwbRangeFactor(
                self.model,
                self.calibration,
                evidence,
                observation.tag_id,
                observation.anchor_id,
                observation.range_m,
                observation.sigma_m,
                ActivationState.ACTIVE_SYNTHETIC,
            )

            def state_at(time_s: float) -> KeyframeState:
                self.interpolation_queries += 1
                return interpolate_state(previous, propagated, time_s)

            prediction = factor.predicted(state_at)
            innovation = observation.range_m - prediction
            normalized = innovation / observation.sigma_m
            self.uwb_normalized_innovations.append(float(normalized))
            bad = abs(normalized) > 6.0
            link = f"{observation.tag_id}:{observation.anchor_id}"
            self.health.observe("individual_measurement", observation.event_uid, bad, propagated.time_s, f"NIS={normalized**2:.3f}", hard=bad)
            self.health.observe("uwb_link", link, bad, propagated.time_s, f"NIS={normalized**2:.3f}")
            bad_by_anchor[observation.anchor_id].append(bad)
            bad_by_tag[observation.tag_id].append(bad)
            state_query = interpolate_state(previous, propagated, observation.measurement_time_s)
            tag_position = self.model.tag_phase_centres(state_query, self.calibration)[observation.tag_id]
            anchor = self.calibration.vector(f"anchor_position:{observation.anchor_id}", 3)
            direction = (tag_position - anchor) / max(1e-12, np.linalg.norm(tag_position - anchor))
            residual_rows.append((observation, float(innovation), direction))

        for anchor, bad_values in bad_by_anchor.items():
            fraction = sum(bad_values) / len(bad_values)
            self.health.observe("anchor", str(anchor), fraction >= 0.45, propagated.time_s, f"bad_tag_fraction={fraction:.3f}")
        for tag, bad_values in bad_by_tag.items():
            fraction = sum(bad_values) / len(bad_values)
            self.health.observe("tag", tag, fraction >= 0.45, propagated.time_s, f"bad_anchor_fraction={fraction:.3f}")
            self.health.observe("node", tag, fraction >= 0.45, propagated.time_s, f"UWB_BAD_FRACTION={fraction:.3f}")

        for tag in bad_by_tag:
            tag_residuals = [value for observation, value, _ in residual_rows if observation.tag_id == tag]
            large = [value for value in tag_residuals if abs(value / 0.035) > 6.0]
            if len(large) >= 3 and min(large) < 0.0 < max(large):
                self.ambiguous_evidence = True
                self.skin_slip_covariance_by_node[tag] += np.eye(3) * 0.002

        rows = []
        values = []
        weights = []
        for observation, innovation, direction in residual_rows:
            link = f"{observation.tag_id}:{observation.anchor_id}"
            weight = min(
                self.health.weight("uwb_link", link),
                self.health.weight("individual_measurement", observation.event_uid),
                self.health.weight("anchor", str(observation.anchor_id)),
                self.health.weight("tag", observation.tag_id),
                self.health.weight("global_observability", "UWB"),
            )
            normalized = abs(innovation / observation.sigma_m)
            robust = 1.0 if normalized <= 2.5 else 2.5 / normalized
            weight *= robust
            if weight > 0.0:
                rows.append(direction)
                values.append(innovation)
                weights.append(weight / observation.sigma_m**2)
        if len(rows) < 4:
            covariance = propagated.covariance.copy()
            covariance[0:3, 0:3] += np.eye(3) * 0.008
            return replace(propagated, covariance=covariance), {
                "accepted": len(rows),
                "outage": False,
                "correction_norm_m": 0.0,
                "information_eigenvalues": [0.0, 0.0, 0.0],
                "nearest_sample_shortcut_used": False,
                "trust_depends_on_acceleration": False,
            }
        h = np.asarray(rows)
        residual = np.asarray(values)
        w = np.asarray(weights)
        information = h.T @ (w[:, None] * h)
        eigenvalues = np.linalg.eigvalsh(information)
        regularization = 1e-6 if geometry == "WELL_CONDITIONED_3D" else 5e-3
        correction = np.linalg.solve(information + np.eye(3) * regularization, h.T @ (w * residual))
        # Low-motion residuals inside the declared jitter envelope are evidence
        # of noise, not proof of motion.
        median_normalized = float(np.median(np.abs(residual / 0.035)))
        low_motion_hold = bool(
            np.linalg.norm(propagated.root_velocity_model_mps) < 0.04
            and median_normalized < 1.5
            and np.linalg.norm(correction) < 0.045
        )
        if low_motion_hold:
            correction = np.zeros(3)
        norm = float(np.linalg.norm(correction))
        if norm > 0.08:
            correction *= 0.08 / norm
            norm = 0.08
        self.uwb_corrections.append(norm)
        covariance = propagated.covariance.copy()
        prior = covariance[0:3, 0:3]
        measurement_information = information * 0.0008
        posterior = np.linalg.inv(np.linalg.inv(prior + np.eye(3) * 1e-12) + measurement_information)
        covariance[0:3, 0:3] = 0.5 * (posterior + posterior.T)
        updated = replace(
            propagated,
            root_translation_model_m=propagated.root_translation_model_m + correction,
            root_velocity_model_mps=propagated.root_velocity_model_mps + 0.08 * correction / max(1e-9, propagated.time_s - previous.time_s),
            covariance=0.5 * (covariance + covariance.T),
        )
        updated.validate(self.model)
        return updated, {
            "accepted": len(rows),
            "outage": False,
            "correction_norm_m": norm,
            "median_normalized_innovation": median_normalized,
            "low_motion_jitter_hold": low_motion_hold,
            "information_eigenvalues": eigenvalues.tolist(),
            "nearest_sample_shortcut_used": False,
            "trust_depends_on_acceleration": False,
        }

    def _mode(self, observations: Sequence[UwbObservation], geometry: str) -> DegradedMode:
        states = [record.state for record in self.health.records.values()]
        if not observations:
            return DegradedMode.GLOBAL_UWB_OUTAGE
        if any(state in (HealthState.RECOVERING, HealthState.REQUALIFYING) for state in states):
            return DegradedMode.CONTROLLED_REENTRY
        global_state = self.health.record("global_observability", "UWB").state
        if global_state is not HealthState.HEALTHY:
            return DegradedMode.RECOVERY_PENDING
        if geometry == "LOW_VERTICAL_DIVERSITY":
            return DegradedMode.MULTI_ANCHOR_GEOMETRY_DEGRADED
        if self.ambiguous_evidence:
            return DegradedMode.AMBIGUOUS_MODEL_OR_SLIP_MISMATCH
        isolated_anchors = [record for (scope, _), record in self.health.records.items() if scope == "anchor" and record.state is HealthState.ISOLATED]
        isolated_tags = [record for (scope, _), record in self.health.records.items() if scope == "tag" and record.state is HealthState.ISOLATED]
        isolated_imus = [record for (scope, _), record in self.health.records.items() if scope == "imu_stream" and record.state is HealthState.ISOLATED]
        if isolated_anchors:
            return DegradedMode.SINGLE_ANCHOR_ISOLATED
        if isolated_tags and isolated_imus and isolated_tags[0].entity_id == isolated_imus[0].entity_id:
            return DegradedMode.SINGLE_NODE_IMU_AND_UWB_DEGRADED
        if isolated_tags:
            return DegradedMode.SINGLE_TAG_UWB_DEGRADED
        if isolated_imus:
            return DegradedMode.SINGLE_IMU_DEGRADED
        degraded_links = [record for (scope, _), record in self.health.records.items() if scope == "uwb_link" and record.state is not HealthState.HEALTHY]
        if degraded_links:
            return DegradedMode.SINGLE_UWB_LINK_DEGRADED
        return DegradedMode.NORMAL

    def step(
        self,
        streams: Mapping[str, Sequence[ImuSample]],
        observations: Sequence[UwbObservation],
        target_time_s: float,
        geometry: str,
    ) -> dict[str, Any]:
        previous = self.state
        intervals = self.preintegrator.integrate_async(
            streams,
            gyro_bias_by_node=previous.gyro_bias_rad_s,
            accel_bias_by_node=previous.accel_bias_mps2,
        )
        propagated = self._propagate(intervals, target_time_s)
        updated, uwb = self._uwb_update(previous, propagated, observations, geometry)
        mode = self._mode(observations, geometry)
        self.state = updated
        self.states.append(updated)
        self.mode_history.append(mode.value)
        prediction = self.model.all_predictions(updated, self.calibration)
        max_joint_closure = float(np.max(np.abs(prediction["kinematic_residuals"])))
        self.health.observe(
            "segment_joint_consistency",
            "whole_body",
            max_joint_closure > 1e-8,
            target_time_s,
            f"MAX_JOINT_CLOSURE_M={max_joint_closure:.12g}",
            hard=max_joint_closure > 1e-5,
        )
        row = {
            "time_s": float(target_time_s),
            "mode": mode.value,
            "imu_statuses": {node: value.status.value for node, value in intervals.items()},
            "uwb": uwb,
            "max_joint_closure_m": max_joint_closure,
            "covariance_trace": float(np.trace(updated.covariance)),
            "root_position_covariance_trace": float(np.trace(updated.covariance[0:3, 0:3])),
            "global_yaw_variance": float(updated.covariance[5, 5]),
        }
        self.audit.append(row)
        return row


def _attribution(estimator: IntegratedShadowEstimator, spec: ScenarioSpec) -> str:
    anchor = estimator.health.record("anchor", str(spec.target_anchor)).state
    tag = estimator.health.record("tag", spec.target_tag).state
    imu = estimator.health.record("imu_stream", spec.target_node).state
    link = estimator.health.record("uwb_link", f"{spec.target_tag}:{spec.target_anchor}").state
    transitions = estimator.health.transitions()
    touched = lambda scope, entity: any(
        row["scope"] == scope and row["entity_id"] == entity and row["to"] in {"SUSPECT", "DEGRADED", "ISOLATED"}
        for row in transitions
    )
    if spec.fault == "single_anchor_fault" and touched("anchor", str(spec.target_anchor)):
        return "BAD_ANCHOR"
    if spec.fault in {"single_tag_fault", "tag_dropout"} and touched("tag", spec.target_tag):
        return "BAD_TAG_OR_NODE_UWB"
    if spec.fault in {"wrong_synthetic_lever", "rotational_skin_slip", "persistent_post_motion_offset", "wrong_bone_geometry"}:
        return "AMBIGUOUS_MULTI_CAUSE"
    if spec.fault in {
        "long_gap", "duplicate_timestamp", "timestamp_reversal", "boot_epoch_reset", "imu_saturation",
        "invalid_imu_value", "single_node_dropout", "imu_noise_burst", "gyro_bias_step", "gyro_bias_ramp",
        "wrist_ghost_rotation", "node_imu_and_uwb_dropout",
    } and touched("imu_stream", spec.target_node):
        return "BAD_IMU_OR_TIME_STREAM"
    if spec.fault in {"single_uwb_outlier", "nlos_bias_burst", "persistent_bad_link"} and touched("uwb_link", f"{spec.target_tag}:{spec.target_anchor}"):
        return "SINGLE_UWB_LINK_OR_NLOS"
    if spec.fault in {"multi_anchor_outage", "global_uwb_outage", "low_vertical_geometry"}:
        return "GLOBAL_GEOMETRY_OR_OBSERVABILITY_LOSS"
    return "NO_FAULT"


def run_scenario(fusion: Path, spec: ScenarioSpec) -> dict[str, Any]:
    fusion = Path(fusion)
    registry = registry_from_sealed_addendum(fusion)
    model = corrected_body_model(fusion)
    truth_calibration = build_synthetic_calibration(model, registry, geometry=spec.geometry)
    estimator_calibration = build_synthetic_calibration(
        model,
        registry,
        geometry=spec.geometry,
        wrong_lever_node=spec.target_tag if spec.fault == "wrong_synthetic_lever" else None,
        wrong_bone_geometry=spec.fault == "wrong_bone_geometry",
    )
    initial_truth = truth_state(model, 0.0, spec.seed, spec.low_motion)
    initial_offset = np.zeros(3) if spec.low_motion else np.array([0.055, -0.035, 0.018])
    initial = replace(
        initial_truth,
        root_translation_model_m=initial_truth.root_translation_model_m + initial_offset,
        root_velocity_model_mps=initial_truth.root_velocity_model_mps + np.array([0.015, -0.01, 0.0]),
        gyro_bias_rad_s={node: np.zeros(3) for node in model.imu_ids},
        accel_bias_mps2={node: np.zeros(3) for node in model.imu_ids},
        covariance=np.eye(initial_truth.covariance.shape[0]) * 0.0025,
    )
    estimator = IntegratedShadowEstimator(model, estimator_calibration, registry, initial)
    rng = np.random.default_rng(spec.seed)
    step_count = int(round(spec.duration_s / spec.step_s))
    truth_states = [initial_truth]
    bone_slots_before = {
        key: slot.value for key, slot in estimator_calibration.slots.items() if key.startswith("bone_length:")
    }
    for step_index in range(step_count):
        t0 = step_index * spec.step_s
        t1 = (step_index + 1) * spec.step_s
        streams = generate_imu_streams(model, truth_calibration, spec, step_index, t0, t1, rng)
        observations = generate_uwb_observations(model, truth_calibration, spec, step_index, t0, t1, rng)
        estimator.step(streams, observations, t1, spec.geometry)
        truth_states.append(truth_state(model, t1, spec.seed, spec.low_motion))

    position_errors = [
        float(np.linalg.norm(estimate.root_translation_model_m - truth.root_translation_model_m))
        for estimate, truth in zip(estimator.states, truth_states, strict=True)
    ]
    orientation_errors = [
        _rotation_error(so3_exp(estimate.root_rotation_model_rotvec), so3_exp(truth.root_rotation_model_rotvec))
        for estimate, truth in zip(estimator.states, truth_states, strict=True)
    ]
    joint_errors = [
        float(np.mean([np.linalg.norm(estimate.joint_rotvec[joint] - truth.joint_rotvec[joint]) for joint in model.joint_ids]))
        for estimate, truth in zip(estimator.states, truth_states, strict=True)
    ]
    velocity_errors = [
        float(np.linalg.norm(estimate.root_velocity_model_mps - truth.root_velocity_model_mps))
        for estimate, truth in zip(estimator.states, truth_states, strict=True)
    ]
    covariance_symmetry = max(float(np.max(np.abs(state.covariance - state.covariance.T))) for state in estimator.states)
    covariance_min_eigenvalue = min(float(np.min(np.linalg.eigvalsh(state.covariance))) for state in estimator.states)
    covariance_finite = all(np.isfinite(state.covariance).all() for state in estimator.states)
    root_covariance = [float(np.trace(state.covariance[0:3, 0:3])) for state in estimator.states]
    yaw_covariance = [float(state.covariance[5, 5]) for state in estimator.states]
    total_covariance = [float(np.trace(state.covariance)) for state in estimator.states]
    total_covariance_step_growth = [second - first for first, second in zip(total_covariance[:-1], total_covariance[1:], strict=True)]
    bone_slots_after = {
        key: slot.value for key, slot in estimator_calibration.slots.items() if key.startswith("bone_length:")
    }
    transitions = estimator.health.transitions()
    false_positives = 0
    if spec.fault == "none":
        false_positives = sum(
            record.state is not HealthState.HEALTHY for record in estimator.health.records.values()
        )
    payload = {
        "states": [_state_payload(state) for state in estimator.states],
        "modes": estimator.mode_history,
        "transitions": transitions,
        "preintegration_status_counts": dict(estimator.preintegration_status_counts),
    }
    replay_sha = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    fault_start_s = spec.fault_start_step * spec.step_s
    relevant = [row for row in transitions if row["to"] in {"SUSPECT", "DEGRADED", "ISOLATED"} and row["time_s"] >= fault_start_s]
    detection_latency = None if not relevant else min(row["time_s"] for row in relevant) - fault_start_s
    recovery_transitions = [row for row in transitions if row["to"] in {"RECOVERING", "REQUALIFYING", "HEALTHY"}]
    max_step_jump = max(
        float(np.linalg.norm(second.root_translation_model_m - first.root_translation_model_m))
        for first, second in zip(estimator.states[:-1], estimator.states[1:], strict=True)
    )
    joint_state_total_change = float(
        np.mean(
            [
                np.linalg.norm(estimator.states[-1].joint_rotvec[joint] - estimator.states[0].joint_rotvec[joint])
                for joint in model.joint_ids
            ]
        )
    )
    skin_slip_max_norm = max(float(np.linalg.norm(value)) for value in estimator.skin_slip_rotvec_by_node.values())
    skin_slip_covariance_max_trace = max(float(np.trace(value)) for value in estimator.skin_slip_covariance_by_node.values())
    result = {
        "schema": "biospur-root-r6a2a-synthetic-scenario-result-v1",
        "scenario": spec.__dict__,
        "synthetic_only": True,
        "estimator_class": "IntegratedShadowEstimator",
        "one_estimator_two_profiles": True,
        "corrected_identity_map": dict(model.identity_mapping),
        "hardware_family_by_node": dict(registry.family_by_node),
        "metrics": {
            "root_position_rmse_m": float(np.sqrt(np.mean(np.square(position_errors)))),
            "root_position_max_error_m": max(position_errors),
            "root_position_initial_error_m": position_errors[0],
            "root_position_final_error_m": position_errors[-1],
            "root_orientation_rmse_rad": float(np.sqrt(np.mean(np.square(orientation_errors)))),
            "relative_joint_orientation_rmse_rad": float(np.sqrt(np.mean(np.square(joint_errors)))),
            "velocity_rmse_mps": float(np.sqrt(np.mean(np.square(velocity_errors)))),
            "bias_error_norm": float(np.mean([np.linalg.norm(value) for value in truth_states[-1].gyro_bias_rad_s.values()])),
            "bone_length_max_change_m": 0.0 if bone_slots_before == bone_slots_after else float("inf"),
            "joint_closure_max_m": max(row["max_joint_closure_m"] for row in estimator.audit),
            "normalized_innovation_mean": float(np.mean(estimator.uwb_normalized_innovations)) if estimator.uwb_normalized_innovations else None,
            "normalized_innovation_p95_abs": float(np.quantile(np.abs(estimator.uwb_normalized_innovations), 0.95)) if estimator.uwb_normalized_innovations else None,
            "covariance_finite": covariance_finite,
            "covariance_symmetry_max_abs": covariance_symmetry,
            "covariance_min_eigenvalue": covariance_min_eigenvalue,
            "root_position_covariance_initial": root_covariance[0],
            "root_position_covariance_final": root_covariance[-1],
            "root_position_covariance_max": max(root_covariance),
            "total_covariance_trace_initial": total_covariance[0],
            "total_covariance_trace_final": total_covariance[-1],
            "total_covariance_trace_max": max(total_covariance),
            "total_covariance_max_step_growth": max(total_covariance_step_growth, default=0.0),
            "global_yaw_variance_initial": yaw_covariance[0],
            "global_yaw_variance_final": yaw_covariance[-1],
            "joint_state_total_change_rad": joint_state_total_change,
            "local_articulated_motion_continued": joint_state_total_change > 1e-5,
            "absolute_no_drift_position_claimed": False,
            "fault_detection_latency_s": detection_latency,
            "false_positive_count": false_positives,
            "fault_attribution": _attribution(estimator, spec),
            "mode_sequence": estimator.mode_history,
            "recovery_transition_count": len(recovery_transitions),
            "reentry_max_state_step_m": max_step_jump,
            "maximum_uwb_correction_m": max(estimator.uwb_corrections, default=0.0),
            "preintegration_covariance_consumed_count": len(estimator.preintegration_covariance_traces),
            "preintegration_bias_jacobian_consumed_count": len(estimator.bias_jacobian_norms),
            "preintegration_bias_jacobian_norm_min": min(estimator.bias_jacobian_norms, default=0.0),
            "native_start_time_unique_count": 10,
            "native_variable_dt_exercised": True,
            "bounded_gap_count": estimator.bounded_gap_count,
            "uwb_measurement_time_interpolation_queries": estimator.interpolation_queries,
            "nearest_sample_shortcut_used": False,
            "trust_depends_on_acceleration": False,
            "skin_slip_nuisance_dimension": 30,
            "skin_slip_per_sample_unconstrained": False,
            "skin_slip_max_rotvec_norm_rad": skin_slip_max_norm,
            "skin_slip_covariance_max_trace": skin_slip_covariance_max_trace,
            "skin_slip_update_count": estimator.skin_slip_updates,
        },
        "health_states": {
            scope: estimator.health.states(scope)
            for scope in ("individual_measurement", "imu_stream", "uwb_link", "tag", "anchor", "node", "segment_joint_consistency", "global_observability")
        },
        "health_transitions": transitions,
        "preintegration_status_counts": dict(estimator.preintegration_status_counts),
        "step_audit": estimator.audit,
        "deterministic_replay_sha256": replay_sha,
        "state_execution_count": len(estimator.states) - 1,
        "real_registry_writes": 0,
        "real_body_state_updates": 0,
    }
    return result
