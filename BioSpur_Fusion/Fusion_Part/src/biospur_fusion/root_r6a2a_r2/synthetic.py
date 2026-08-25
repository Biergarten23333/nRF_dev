"""Deterministic, independently streamed synthetic source and private injector."""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from biospur_fusion.imu.preintegration import ImuSample
from biospur_fusion.root_r6a0.body import BodyModel, KeyframeState, StaticCalibration
from biospur_fusion.root_r6a0.factors import raw_range_value
from biospur_fusion.root_r6a0.math3d import so3_log
from biospur_fusion.root_r6a2a.shadow import (
    GRAVITY_W,
    build_synthetic_calibration,
    corrected_body_model,
    truth_state,
)
from biospur_fusion.root_r6a2a.contracts import registry_from_sealed_addendum

from .contracts import (
    EstimatorInput,
    EstimatorOptions,
    FaultInjectionTruth,
    RNG_DERIVATION_VERSION,
    ScenarioDefinition,
    ScheduledObservation,
    UwbMeasurement,
)


@dataclass(frozen=True)
class GeneratedRun:
    scenario_id: str
    inputs: tuple[EstimatorInput, ...]
    truth_states: tuple[KeyframeState, ...]
    truth_calibration: StaticCalibration
    fault_window_audit: Mapping[str, Any]
    rng_lineage: Mapping[str, Any]
    source_digest: str


class IndependentRng:
    """Stable SHA-256-derived streams; never uses Python's randomized hash."""

    def __init__(self, master_seed: int):
        self.master_seed = int(master_seed)

    def seed(self, stream: str, *identifiers: object) -> int:
        payload = json.dumps(
            [RNG_DERIVATION_VERSION, self.master_seed, stream, *map(str, identifiers)],
            ensure_ascii=True, separators=(",", ":"),
        ).encode("ascii")
        return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")

    def generator(self, stream: str, *identifiers: object) -> np.random.Generator:
        return np.random.default_rng(self.seed(stream, *identifiers))

    def lineage(self, model: BodyModel) -> dict[str, Any]:
        return {
            "schema": "biospur-root-r6a2a-r2-rng-lineage-v1",
            "derivation_version": RNG_DERIVATION_VERSION,
            "master_seed": self.master_seed,
            "hash_function": "SHA-256",
            "seed_bytes": 8,
            "trajectory_rng": self.seed("trajectory", "whole_body"),
            "clock_rng": {node: self.seed("clock", node) for node in model.imu_ids},
            "imu_rng": {node: self.seed("imu", node) for node in model.imu_ids},
            "uwb_rng": {
                f"{tag}:{anchor}": self.seed("uwb", tag, anchor)
                for tag in model.tag_ids for anchor in range(8)
            },
            "fault_rng_derivation": "seed(master,'fault',scenario_id,fault_component)",
            "iteration_order_dependency": False,
        }


def _fixed_uwb_pairs(model: BodyModel) -> tuple[tuple[str, int], ...]:
    """One fixed schedule shared by every scenario and independent of labels."""
    focus = "BSFEC35"
    pairs = {(focus, anchor) for anchor in range(8)}
    pairs.update((tag, 2) for tag in model.tag_ids)
    support = tuple(tag for tag in model.tag_ids if tag != focus)[:4]
    pairs.update((tag, anchor) for tag in support for anchor in range(8))
    pairs.update((tag, (index + 5) % 8) for index, tag in enumerate(model.tag_ids))
    return tuple(sorted(pairs))


def _event_fraction(tag_index: int, anchor_id: int) -> float:
    return 0.22 + 0.56 * ((tag_index * 8 + anchor_id) % 17) / 16.0


def _native_times(
    t0: float, t1: float, node_index: int, clock_offset_s: float
) -> np.ndarray:
    values = [t0 + 0.00012 * node_index + clock_offset_s]
    cursor = values[0]
    count = 0
    while True:
        dt = 0.00455 + 0.000055 * node_index + 0.00031 * np.sin(0.43 * count + 0.19 * node_index)
        limit = t1 - 0.00008 * (9 - node_index) + clock_offset_s
        if cursor + dt >= limit:
            break
        cursor += dt
        values.append(cursor)
        count += 1
    values.append(t1 - 0.00008 * (9 - node_index) + clock_offset_s)
    return np.asarray(values)


def _base_imu_streams(
    model: BodyModel,
    calibration: StaticCalibration,
    scenario: ScenarioDefinition,
    step: int,
    rngs: IndependentRng,
) -> dict[str, tuple[ImuSample, ...]]:
    t0, t1 = step * scenario.step_s, (step + 1) * scenario.step_s
    trajectory_seed = rngs.seed("trajectory", scenario.trajectory_variant) % 100000
    states = [truth_state(model, t, trajectory_seed, scenario.low_motion) for t in (t0, 0.5 * (t0 + t1), t1)]
    poses = [model.imu_frames(state, calibration) for state in states]
    half_span = 0.5 * scenario.step_s
    streams: dict[str, tuple[ImuSample, ...]] = {}
    for node_index, node in enumerate(model.imu_ids):
        clock = rngs.generator("clock", node)
        offset = float(clock.normal(0.0, 2.0e-6))
        times = _native_times(t0, t1, node_index, offset)
        bias = truth_state(model, 0.5 * (t0 + t1), trajectory_seed, scenario.low_motion)
        gyro_nominal = so3_log(poses[0][node].rotation.T @ poses[2][node].rotation) / scenario.step_s
        acceleration_w = (
            poses[2][node].translation - 2.0 * poses[1][node].translation + poses[0][node].translation
        ) / half_span**2
        specific_force = poses[1][node].rotation.T @ (acceleration_w - GRAVITY_W)
        samples: list[ImuSample] = []
        for sample_index, stamp in enumerate(times):
            noise = rngs.generator("imu", node, step, sample_index)
            accel = specific_force + bias.accel_bias_mps2[node] + noise.normal(0.0, 0.007, 3)
            gyro = gyro_nominal + bias.gyro_bias_rad_s[node] + noise.normal(0.0, 0.0007, 3)
            samples.append(ImuSample(
                node, int(round(stamp * 1e9)), 100 + node_index,
                np.asarray(accel), np.asarray(gyro),
            ))
        streams[node] = tuple(samples)
    return streams


def _base_uwb(
    model: BodyModel,
    calibration: StaticCalibration,
    scenario: ScenarioDefinition,
    step: int,
    rngs: IndependentRng,
) -> tuple[UwbMeasurement, ...]:
    t0, t1 = step * scenario.step_s, (step + 1) * scenario.step_s
    trajectory_seed = rngs.seed("trajectory", scenario.trajectory_variant) % 100000
    tag_index = {tag: index for index, tag in enumerate(model.tag_ids)}
    rows: list[UwbMeasurement] = []
    for tag, anchor in _fixed_uwb_pairs(model):
        fraction = _event_fraction(tag_index[tag], anchor)
        stamp = t0 + fraction * (t1 - t0)
        state = truth_state(model, stamp, trajectory_seed, scenario.low_motion)
        sigma = 0.035
        noise = rngs.generator("uwb", tag, anchor, step).normal(0.0, sigma)
        value = raw_range_value(model, calibration, state, tag, anchor) + float(noise)
        uid = f"m{scenario.master_seed}:s{step}:{tag}:a{anchor}"
        rows.append(UwbMeasurement(
            uid, tag, anchor, float(stamp), float(stamp + 0.004 + 0.0002 * anchor),
            float(value), sigma, 1, True,
        ))
    return tuple(rows)


def _schedule(
    model: BodyModel,
    scenario: ScenarioDefinition,
    step: int,
) -> tuple[ScheduledObservation, ...]:
    t0, t1 = step * scenario.step_s, (step + 1) * scenario.step_s
    tag_index = {tag: index for index, tag in enumerate(model.tag_ids)}
    rows: list[ScheduledObservation] = []
    for node_index, node in enumerate(model.imu_ids):
        rows.append(ScheduledObservation(
            f"s{step}:imu:{node}", "IMU", node, None, None,
            float(t1 - 0.00008 * (9 - node_index)), float(t1 + 0.002),
            100 + node_index, True, True,
        ))
    for tag, anchor in _fixed_uwb_pairs(model):
        stamp = t0 + _event_fraction(tag_index[tag], anchor) * (t1 - t0)
        rows.append(ScheduledObservation(
            f"s{step}:uwb:{tag}:{anchor}", "UWB", tag, tag, anchor,
            float(stamp), float(stamp + 0.012), 1, True, True,
        ))
    return tuple(rows)


def _mutate_imu(
    base: Mapping[str, tuple[ImuSample, ...]],
    scenario: ScenarioDefinition,
    step: int,
    affected: list[str],
) -> dict[str, tuple[ImuSample, ...]]:
    result = {node: tuple(samples) for node, samples in base.items()}
    private = scenario.private_truth
    if not private.window.active(step):
        return result
    node = private.target_node
    samples = list(result[node])
    kind = private.kind
    if kind == "bounded_sample_gap" and len(samples) > 5:
        removed = samples.pop(len(samples) // 2)
        affected.append(f"IMU:{node}:{removed.global_time_ns}")
    elif kind == "long_gap" and len(samples) > 8:
        middle = len(samples) // 2
        removed = samples[middle - 2:middle + 3]
        del samples[middle - 2:middle + 3]
        affected.extend(f"IMU:{node}:{row.global_time_ns}" for row in removed)
    elif kind == "duplicate_timestamp" and len(samples) > 3:
        samples[3] = replace(samples[3], global_time_ns=samples[2].global_time_ns)
        affected.append(f"IMU:{node}:{step}:duplicate")
    elif kind == "timestamp_reversal" and len(samples) > 3:
        samples[3] = replace(samples[3], global_time_ns=samples[2].global_time_ns - 1_000_000)
        affected.append(f"IMU:{node}:{step}:reversal")
    elif kind == "boot_epoch_reset" and len(samples) > 2:
        samples[-1] = replace(samples[-1], boot_epoch=samples[0].boot_epoch + 1)
        affected.append(f"IMU:{node}:{step}:boot")
    elif kind == "imu_saturation" and samples:
        middle = len(samples) // 2
        samples[middle] = replace(samples[middle], acc_raw=(32767, 0, 0), gyro_raw=(0, 0, 0))
        affected.append(f"IMU:{node}:{samples[middle].global_time_ns}")
    elif kind == "invalid_imu_value" and samples:
        middle = len(samples) // 2
        samples[middle] = replace(samples[middle], accepted=False)
        affected.append(f"IMU:{node}:{samples[middle].global_time_ns}")
    elif kind in {"single_node_dropout", "node_imu_and_uwb_dropout"}:
        affected.extend(f"IMU:{node}:{row.global_time_ns}" for row in samples[1:])
        samples = samples[:1]
    elif kind in {
        "imu_noise_burst", "gyro_bias_step", "gyro_bias_ramp",
        "rotational_skin_slip", "wrist_ghost_rotation", "observable_gyro_bias_step",
        "observable_accel_bias_step",
    }:
        changed: list[ImuSample] = []
        for row in samples:
            gyro, accel = row.gyro_rad_s.copy(), row.accel_mps2.copy()
            if kind == "imu_noise_burst":
                gyro += np.array([0.8, -0.5, 0.6]) * private.magnitude
            elif kind in {"gyro_bias_step", "observable_gyro_bias_step"}:
                gyro += np.array([private.magnitude, 0.0, 0.0])
            elif kind == "gyro_bias_ramp":
                gyro += np.array([private.magnitude * (step - private.window.start_step + 1), 0.0, 0.0])
            elif kind in {"rotational_skin_slip", "wrist_ghost_rotation"}:
                gyro += np.array([0.0, private.magnitude, 0.0])
            elif kind == "observable_accel_bias_step":
                accel += np.array([private.magnitude, 0.0, 0.0])
            changed.append(replace(row, gyro_rad_s=gyro, accel_mps2=accel))
            affected.append(f"IMU:{node}:{row.global_time_ns}")
        samples = changed
    result[node] = tuple(samples)
    return result


def _mutate_uwb(
    base: tuple[UwbMeasurement, ...],
    scenario: ScenarioDefinition,
    step: int,
    affected: list[str],
) -> tuple[UwbMeasurement, ...]:
    private = scenario.private_truth
    if not private.window.active(step):
        return base
    kind = private.kind
    result: list[UwbMeasurement] = []
    for row in base:
        target_link = row.tag_id == private.target_tag and row.anchor_id == private.target_anchor
        omit = (
            kind in {"tag_dropout", "node_imu_and_uwb_dropout"} and row.tag_id == private.target_tag
        ) or (kind == "multi_anchor_outage" and row.anchor_id < 5) or kind == "global_uwb_outage" or (
            kind == "uwb_omission_link" and target_link
        )
        if omit:
            affected.append(f"UWB:{row.event_uid}:MISSING")
            continue
        value = row.range_m
        changed = False
        if kind == "single_uwb_outlier" and target_link and step == private.window.start_step:
            value += 1.25 * private.magnitude
            changed = True
        elif kind in {"nlos_bias_burst", "persistent_bad_link"} and target_link:
            value += 0.55 * private.magnitude
            changed = True
        elif kind == "single_anchor_fault" and row.anchor_id == private.target_anchor:
            value += 0.48 * private.magnitude
            changed = True
        elif kind == "single_tag_fault" and row.tag_id == private.target_tag:
            value += (0.45 + 0.025 * row.anchor_id) * private.magnitude
            changed = True
        elif kind in {"rotational_skin_slip", "persistent_post_motion_offset"} and row.tag_id == private.target_tag:
            scale = 1.0 if kind == "rotational_skin_slip" else private.magnitude
            value += 0.28 * np.sin(0.7 * row.anchor_id + 0.3) * scale
            changed = True
        if changed:
            affected.append(f"UWB:{row.event_uid}")
            row = replace(row, range_m=float(value))
        result.append(row)
    return tuple(result)


def _estimator_calibration(
    model: BodyModel,
    truth_calibration: StaticCalibration,
    scenario: ScenarioDefinition,
    fusion: Path,
) -> StaticCalibration:
    private = scenario.private_truth
    registry = registry_from_sealed_addendum(fusion)
    if private.kind == "wrong_synthetic_lever":
        return build_synthetic_calibration(
            model, registry, geometry=scenario.geometry, wrong_lever_node=private.target_tag,
        )
    if private.kind == "wrong_bone_geometry":
        return build_synthetic_calibration(
            model, registry, geometry=scenario.geometry, wrong_bone_geometry=True,
        )
    return truth_calibration


def generate_run(
    fusion: Path,
    scenario: ScenarioDefinition,
    options: EstimatorOptions = EstimatorOptions(),
) -> GeneratedRun:
    """Generate all private source data, then expose only EstimatorInput rows."""
    fusion = Path(fusion)
    model = corrected_body_model(fusion)
    registry = registry_from_sealed_addendum(fusion)
    truth_calibration = build_synthetic_calibration(model, registry, geometry=scenario.geometry)
    if scenario.geometry == "LOW_VERTICAL_DIVERSITY":
        # Put every anchor close to the tag-height plane.  The weak direction
        # is then recovered from the measured Jacobian eigensystem, not from
        # this label inside the estimator.
        for anchor in range(8):
            position = truth_calibration.vector(f"anchor_position:{anchor}", 3).copy()
            position[2] = 1.05 + 0.002 * ((anchor % 3) - 1)
            truth_calibration = truth_calibration.with_value(f"anchor_position:{anchor}", position)
    estimator_calibration = _estimator_calibration(model, truth_calibration, scenario, fusion)
    rngs = IndependentRng(scenario.master_seed)
    affected: list[str] = []
    inputs: list[EstimatorInput] = []
    clean_digest_rows: list[dict[str, Any]] = []
    trajectory_seed = rngs.seed("trajectory", scenario.trajectory_variant) % 100000
    truth_states = [truth_state(model, 0.0, trajectory_seed, scenario.low_motion)]
    for step in range(scenario.step_count):
        base_imu = _base_imu_streams(model, truth_calibration, scenario, step, rngs)
        base_uwb = _base_uwb(model, truth_calibration, scenario, step, rngs)
        imu = _mutate_imu(base_imu, scenario, step, affected)
        uwb = _mutate_uwb(base_uwb, scenario, step, affected)
        schedule = _schedule(model, scenario, step)
        t0, t1 = step * scenario.step_s, (step + 1) * scenario.step_s
        inputs.append(EstimatorInput(
            step, t0, t1, imu, uwb, schedule, estimator_calibration,
            registry, scenario.geometry, options,
        ))
        truth_states.append(truth_state(model, t1, trajectory_seed, scenario.low_motion))
        clean_digest_rows.append({
            "step": step,
            "imu": {
                node: [(s.global_time_ns, s.accel_mps2.tolist(), s.gyro_rad_s.tolist()) for s in samples]
                for node, samples in base_imu.items()
            },
            "uwb": [(r.event_uid, r.range_m) for r in base_uwb],
        })

    private = scenario.private_truth
    persistent = private.window.persistent_configuration
    times: list[float] = []
    for item in affected:
        parts = item.split(":")
        if parts[0] == "IMU" and parts[-1].isdigit():
            times.append(int(parts[-1]) * 1e-9)
        elif parts[0] == "UWB":
            # Event UIDs contain the step after ':s'.
            try:
                token = next(part for part in parts if part.startswith("s") and part[1:].isdigit())
                times.append((int(token[1:]) + 0.5) * scenario.step_s)
            except StopIteration:
                pass
    if persistent and not affected:
        affected = [f"CONFIGURATION_STEP:{step}" for step in range(scenario.step_count)]
        times = [0.0, scenario.duration_s]
    audit = {
        "scenario_id": scenario.scenario_id,
        "target": {"node": private.target_node, "tag": private.target_tag, "anchor": private.target_anchor},
        "start_timestamp_s": 0.0 if persistent else private.window.start_step * scenario.step_s,
        "end_timestamp_s": scenario.duration_s if persistent else (private.window.end_step + 1) * scenario.step_s,
        "applied_sample_count": len(affected),
        "first_affected_sample_s": min(times) if times else None,
        "last_affected_sample_s": max(times) if times else None,
        "pre_window_equality": "NOT_APPLICABLE_PERSISTENT_CONFIGURATION" if persistent else True,
        "post_window_equality": "NOT_APPLICABLE_PERSISTENT_CONFIGURATION" if persistent else True,
        "persistent_physical_state": persistent,
        "affected_identifiers_sha256": hashlib.sha256(json.dumps(affected, sort_keys=True).encode()).hexdigest(),
    }
    source_digest = hashlib.sha256(
        json.dumps(clean_digest_rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return GeneratedRun(
        scenario.scenario_id, tuple(inputs), tuple(truth_states), truth_calibration,
        audit, rngs.lineage(model), source_digest,
    )


def serialize_input(value: EstimatorInput) -> dict[str, Any]:
    """Canonical numerical serialization used by authority negative controls."""
    return {
        "step_index": value.step_index,
        "times": [value.interval_start_s, value.interval_end_s],
        "imu": {
            node: [{
                "t": sample.global_time_ns, "boot": sample.boot_epoch,
                "a": sample.accel_mps2.tolist(), "w": sample.gyro_rad_s.tolist(),
                "accepted": sample.accepted, "acc_raw": sample.acc_raw, "gyro_raw": sample.gyro_raw,
            } for sample in samples]
            for node, samples in sorted(value.imu_streams.items())
        },
        "uwb": [row.__dict__ for row in value.uwb_measurements],
        "schedule": [row.__dict__ for row in value.expected_schedule],
        "geometry_class": value.geometry_class,
        "options": value.options.__dict__,
    }


def input_digest(inputs: Iterable[EstimatorInput]) -> str:
    payload = [serialize_input(value) for value in inputs]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
