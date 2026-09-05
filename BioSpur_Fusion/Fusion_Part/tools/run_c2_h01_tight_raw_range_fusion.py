#!/usr/bin/env python3
"""Bounded H01 pelvis-first fusion from 200 Hz IMU and 8.333 Hz raw ranges.

This diagnostic never consumes T4 or another solved UWB position.  It uses the
Beacon-only TIMER2 clock, the canonical A--H layout, one continuous VQF state,
the existing Root-R3 inertial propagation, and joint raw-range updates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np
import qmt
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_uwb_root_world.run_calibration import (
    LAYOUT,
    _beacon_boundary_bridges,
    _clock_models,
)
from biospur_fusion.c2_uwb_root_world.tight_range import (
    RawRangeUpdateConfig,
    UWB_SWEEP_PERIOD_US,
    UWB_SWEEP_RATE_HZ,
    update_raw_ranges,
)
from biospur_fusion.c2_uwb_root_world.u0 import UwbRow, solve_u0_row
from biospur_fusion.ingest.events import RecordType
from biospur_fusion.ingest.v47 import decode_measurements
from biospur_fusion.root_r3.estimator import RootFilterConfig, propagate_inertial
from biospur_fusion.root_r3.models import RootState


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets/phase2_calibration/phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2"
H01 = DATASET / "holdout/H01_boxing/rep_01"
RAW = H01 / "raw/fusion_host_raw.cobs.bin"
EVENTS = H01 / "events/ACTION_EVENTS.jsonl"
CLOCK_TABLE = ROOT / "logs/c2_uwb_beacon_clock_20260903_141552/CLOCK_TABLE_CALIBRATION_ONLY.json"
PELVIS_NODE = "BSFC2CC"
G = 9.80665


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 << 20):
            digest.update(block)
    return digest.hexdigest()


def _action_bounds_ns() -> tuple[int, int]:
    rows = [json.loads(line) for line in EVENTS.read_text().splitlines() if line.strip()]
    by_name = {row["event"]: row for row in rows}
    bridges = _beacon_boundary_bridges(CLOCK_TABLE)
    result = []
    for name in ("ACTION_START", "ACTION_STOP"):
        host_s = int(by_name[name]["host_monotonic_ns"]) * 1e-9
        global_us = np.median([slope * host_s + intercept for slope, intercept in bridges])
        result.append(int(round(global_us * 1000.0)))
    if result[1] <= result[0]:
        raise ValueError("invalid LBD-selected H01 action boundary")
    return result[0], result[1]


def _anchors() -> np.ndarray:
    document = json.loads(LAYOUT.read_text())
    rows = sorted(document["anchors"], key=lambda row: int(row["id"]))
    if [row["id"] for row in rows] != list(range(8)):
        raise ValueError("layout is not canonical A--H")
    return np.asarray([[row["x_mm"], row["y_mm"], row["z_mm"]] for row in rows], float) / 1000.0


def _to_uwb_row(event) -> UwbRow:
    value = event.payload
    return UwbRow(
        node=event.node_id,
        boot=event.boot_epoch,
        sequence=int(value["packet_sequence"]),
        sweep=int(value["sweep"]),
        strobe_us=int(value["strobe_us"]),
        frame_us=int(value["frame_us"]),
        anchor_ids=tuple(int(x) for x in value["anchor_id"]),
        ranges_mm=tuple(int(x) for x in value["range_mm"]),
        t_round_us=tuple(int(x) for x in value["t_round_us"]),
        quality=tuple(int(x) for x in value["quality_percent"]),
        valid_mask=int(value["valid_mask"]),
        identity=int(value["identity"]),
        node_ms=int(value["node_ms"]),
    )


def _yaw_rotation(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def _pelvis_imu(events, clock, start_ns: int, yaw_offset_deg: float) -> tuple[list[dict], dict]:
    rows = sorted(
        (event for event in events if event.node_id == PELVIS_NODE and event.record_type is RecordType.IMU),
        key=lambda event: int(event.node_timer_us),
    )
    if len(rows) < 100:
        raise RuntimeError("insufficient pelvis IMU")
    block = qmt.OriEstVQFBlock(0.005)
    decoded = []
    for event in rows:
        acceleration = np.asarray(event.payload["acc_raw"], float) / 2048.0 * G
        gyroscope = np.deg2rad(np.asarray(event.payload["gyro_raw"], float) / 16.384)
        quaternion = np.asarray(block.step(gyroscope, acceleration, None), float)
        rotation = Rotation.from_quat(quaternion[[1, 2, 3, 0]]).as_matrix()
        global_s = clock.seconds(int(event.node_timer_us))
        decoded.append({
            "time_s": global_s,
            "acceleration": acceleration,
            "rotation_vqf": rotation,
            "sequence": int(event.sequence),
        })

    # VQF owns gravity, but not navigation yaw.  The world binding provides a
    # soft initial body-forward prior toward -Y; pelvis sensor -Z is the sealed
    # qualitative forward direction.  Apply only the yaw needed to align the
    # median pre-action horizontal projection with -Y and preserve this as an
    # explicit diagnostic gauge rather than calibration truth.
    preparation = [row for row in decoded if start_ns * 1e-9 - 2.0 <= row["time_s"] < start_ns * 1e-9]
    if len(preparation) < 100:
        raise RuntimeError("insufficient pre-action IMU for the yaw gauge")
    forward = np.median(
        np.stack([row["rotation_vqf"] @ np.array([0.0, 0.0, -1.0]) for row in preparation]),
        axis=0,
    )
    if np.linalg.norm(forward[:2]) < 0.25:
        raise RuntimeError("pelvis -Z lacks a stable horizontal heading projection")
    measured_angle = math.atan2(forward[1], forward[0])
    target_angle = -math.pi / 2.0
    yaw_delta = target_angle - measured_angle + math.radians(float(yaw_offset_deg))
    navigation_from_vqf = _yaw_rotation(yaw_delta)
    for row in decoded:
        row["rotation_world"] = navigation_from_vqf @ row.pop("rotation_vqf")
    return decoded, {
        "orientation_filter": "qmt.OriEstVQFBlock",
        "vqf_instances": 1,
        "vqf_resets": 0,
        "sample_period_argument_s": 0.005,
        "initial_yaw_role": "SOFT_GAUGE_NOT_ABSOLUTE_YAW_CALIBRATION",
        "initial_body_forward_target_world": [0.0, -1.0, 0.0],
        "pelvis_sensor_forward_proxy": "sensor_minus_Z_qualitative",
        "yaw_delta_rad": yaw_delta,
        "yaw_sensitivity_offset_deg": float(yaw_offset_deg),
    }


def _initialize(first: UwbRow, anchors: np.ndarray, clock) -> RootState:
    epochs = np.asarray([
        clock.seconds(first.strobe_us + 0.5 * first.t_round_us[slot])
        for slot in range(8) if first.valid_mask & (1 << slot) and 0 < first.ranges_mm[slot] < 0xFFFF
    ])
    t0 = float(np.median(epochs))
    centre = np.mean(anchors, axis=0)
    prior = np.r_[centre, np.zeros(3)]
    covariance = np.diag([4.0, 4.0, 2.0, 1.0, 1.0, 1.0])
    solved = solve_u0_row(
        first,
        anchors_m=anchors,
        clock=clock,
        predicted_state=prior,
        predicted_covariance=covariance,
        bias_m={},
        sigma_history_m={},
        sigma_bias_m={},
        calibration_zero_uncertainty=True,
    )
    if not solved.success:
        raise RuntimeError(f"raw-range initialization failed: {solved.reason}")
    root_covariance = np.zeros((9, 9), dtype=float)
    root_covariance[:6, :6] = solved.covariance
    root_covariance[6:, 6:] = np.eye(3) * 0.25
    eigenvalue = float(np.linalg.eigvalsh(root_covariance).min())
    if eigenvalue <= 1e-10:
        root_covariance += np.eye(9) * (1e-10 - eigenvalue + 1e-12)
    return RootState(t0, np.r_[solved.state, np.zeros(3)], root_covariance)


def run(output: Path, *, yaw_offset_deg: float = 0.0,
        nominal_range_sigma_m: float = 0.12) -> dict:
    started = time.perf_counter()
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    clocks = _clock_models(CLOCK_TABLE)
    clock = clocks[PELVIS_NODE]
    start_ns, stop_ns = _action_bounds_ns()
    events, decode_audit = decode_measurements(RAW)
    imu, orientation_audit = _pelvis_imu(events, clock, start_ns, yaw_offset_deg)
    uwb = sorted(
        (_to_uwb_row(event) for event in events
         if event.node_id == PELVIS_NODE and event.record_type is RecordType.UWB),
        key=lambda row: row.strobe_us,
    )
    uwb = [row for row in uwb if start_ns <= int(round(clock.a_ns_per_us * row.strobe_us + clock.b_ns)) < stop_ns]
    if not uwb:
        raise RuntimeError("no pelvis UWB in H01 action")
    anchors = _anchors()
    initial = _initialize(uwb[0], anchors, clock)
    fused = initial
    inertial_only = initial
    imu = [row for row in imu if initial.time_s < row["time_s"] < stop_ns * 1e-9]
    range_rows = uwb[1:]

    # A time-ordered offline diagnostic.  It still uses measurement epochs,
    # never UART/host receipt time.  Each UWB state is propagated to the median
    # link epoch before its joint update.
    timeline = [(row["time_s"], 0, row) for row in imu]
    for row in range_rows:
        valid = [slot for slot in range(8) if row.valid_mask & (1 << slot) and 0 < row.ranges_mm[slot] < 0xFFFF]
        epoch = float(np.median([clock.seconds(row.strobe_us + 0.5 * row.t_round_us[slot]) for slot in valid]))
        if initial.time_s < epoch < stop_ns * 1e-9:
            timeline.append((epoch, 1, row))
    timeline.sort(key=lambda item: (item[0], item[1]))

    filter_config = RootFilterConfig()
    range_config = RawRangeUpdateConfig(nominal_sigma_m=float(nominal_range_sigma_m))
    last_force = np.array([0.0, 0.0, G])
    last_rotation = np.eye(3)
    output_time = []
    fused_position = []
    fused_velocity = []
    inertial_position = []
    covariance_diagonal = []
    decisions = []
    accepted = rejected = 0
    link_histogram: dict[int, int] = {}
    for event_time, kind, payload in timeline:
        if event_time <= fused.time_s + 1e-12:
            continue
        fused, _ = propagate_inertial(fused, event_time, last_force, last_rotation, filter_config)
        inertial_only, _ = propagate_inertial(
            inertial_only, event_time, last_force, last_rotation, filter_config
        )
        if kind == 0:
            last_force = payload["acceleration"]
            last_rotation = payload["rotation_world"]
            output_time.append(event_time)
            fused_position.append(fused.position_m.copy())
            fused_velocity.append(fused.velocity_mps.copy())
            inertial_position.append(inertial_only.position_m.copy())
            covariance_diagonal.append(np.diag(fused.covariance).copy())
        else:
            updated, decision = update_raw_ranges(
                fused, payload, anchors_m=anchors, clock=clock, config=range_config
            )
            if decision.accepted:
                fused = updated
                accepted += 1
            else:
                rejected += 1
            link_histogram[len(decision.anchors)] = link_histogram.get(len(decision.anchors), 0) + 1
            padded_residual = np.full(8, np.nan)
            padded_weight = np.full(8, np.nan)
            for index, anchor in enumerate(decision.anchors):
                padded_residual[anchor] = decision.innovations_m[index]
                padded_weight[anchor] = decision.robust_weights[index]
            decisions.append((event_time, decision.accepted, decision.reason, padded_residual, padded_weight))

    if len(output_time) < 100 or not decisions:
        raise RuntimeError("fusion produced insufficient output")
    time_array = np.asarray(output_time) - initial.time_s
    fused_array = np.stack(fused_position)
    imu_array = np.stack(inertial_position)
    decision_time = np.asarray([row[0] for row in decisions]) - initial.time_s
    decision_accepted = np.asarray([row[1] for row in decisions], dtype=bool)
    decision_reason = np.asarray([row[2] for row in decisions], dtype="U64")
    residual = np.stack([row[3] for row in decisions])
    weight = np.stack([row[4] for row in decisions])
    np.savez_compressed(
        output / "H01_PELVIS_TIGHT_RAW_RANGE_FUSION.npz",
        time_s=time_array,
        fused_position_world_m=fused_array,
        fused_velocity_world_mps=np.stack(fused_velocity),
        imu_only_position_world_m=imu_array,
        fused_state_covariance_diagonal=np.stack(covariance_diagonal),
        uwb_time_s=decision_time,
        uwb_accepted=decision_accepted,
        uwb_reason=decision_reason,
        raw_range_postfit_residual_m=residual,
        raw_range_robust_weight=weight,
        anchors_world_m=anchors,
    )
    sweep_delta_us = np.diff([row.strobe_us for row in uwb])
    valid_link_counts = [int(row.valid_mask).bit_count() for row in uwb]
    audit = {
        "schema": "biospur.c2.h01.pelvis_tight_raw_range_fusion.v1",
        "status": "DIAGNOSTIC_COMPLETE_NOT_SCIENTIFIC_PASS",
        "scientific_pass": False,
        "action": "H01_boxing",
        "pelvis_node": PELVIS_NODE,
        "fusion_architecture": {
            "state": "Root-R3 [position, velocity, accelerometer_bias]",
            "imu_propagation_hz": 200.0,
            "uwb_period_us": UWB_SWEEP_PERIOD_US,
            "uwb_rate_hz": UWB_SWEEP_RATE_HZ,
            "uwb_measurement": "7_OR_8_INDIVIDUAL_RAW_RANGE_FACTORS_PER_SWEEP",
            "per_link_epoch": "Beacon_clock(strobe_us + t_round_us[anchor]/2)",
            "old_T4_or_PositionObservation_consumed": False,
            "nlos_handling": "PER_LINK_HUBER_WEIGHT; WHOLE_SWEEP_NOT_DROPPED",
            "missing_link_handling": "FACTOR_ABSENT",
        },
        "real_capture": {
            "decoded_measurements": decode_audit.emitted_measurements,
            "decode_errors": decode_audit.decode_errors,
            "action_start_global_ns": start_ns,
            "action_stop_global_ns_exclusive": stop_ns,
            "pelvis_uwb_sweeps": len(uwb),
            "pelvis_sweep_delta_us_median": float(np.median(sweep_delta_us)),
            "pelvis_sweep_delta_us_p01_p99": np.percentile(sweep_delta_us, [1, 99]).tolist(),
            "valid_link_histogram": {str(key): int(value) for key, value in zip(*np.unique(valid_link_counts, return_counts=True))},
            "range_updates_accepted": accepted,
            "range_updates_rejected": rejected,
            "update_link_histogram": {str(key): value for key, value in sorted(link_histogram.items())},
        },
        "trajectory_diagnostics": {
            "duration_s": float(time_array[-1]),
            "imu_only_end_displacement_m": float(np.linalg.norm(imu_array[-1] - imu_array[0])),
            "fused_end_displacement_m": float(np.linalg.norm(fused_array[-1] - fused_array[0])),
            "imu_only_max_distance_from_anchor_volume_centre_m": float(np.max(np.linalg.norm(imu_array - np.mean(anchors, axis=0), axis=1))),
            "fused_max_distance_from_anchor_volume_centre_m": float(np.max(np.linalg.norm(fused_array - np.mean(anchors, axis=0), axis=1))),
            "fused_min_xyz_m": np.min(fused_array, axis=0).tolist(),
            "fused_max_xyz_m": np.max(fused_array, axis=0).tolist(),
            "median_abs_postfit_range_residual_m": float(np.nanmedian(np.abs(residual))),
            "fraction_links_downweighted": float(np.nanmean(weight < 0.999999)),
            "fraction_positions_outside_anchor_axis_bounds": np.mean(
                (fused_array < np.min(anchors, axis=0))
                | (fused_array > np.max(anchors, axis=0)), axis=0
            ).tolist(),
        },
        "clock": {
            "source": str(CLOCK_TABLE),
            "source_sha256": _sha256(CLOCK_TABLE),
            "accepted_listener_kind": "LBD",
            "forbidden_listener_kinds": ["LPD", "LRD"],
            "measurement_clock": "B306_TIMER2",
        },
        "orientation": orientation_audit,
        "uncertainty": {
            "range_sigma_m": range_config.nominal_sigma_m,
            "huber_threshold_sigma": range_config.huber_threshold_sigma,
            "provenance": range_config.uncertainty_provenance,
        },
        "known_unresolved": [
            "per-anchor range bias and measured R matrix are not yet qualified",
            "initial IMU navigation yaw is a soft -Y gauge, not an absolute yaw observation",
            "pelvis IMU origin to UWB phase-centre lever arm is pending metrology",
            "offline time-ordered diagnostic does not yet model transport availability latency",
        ],
        "inputs": {
            "raw": str(RAW), "raw_sha256": _sha256(RAW),
            "events": str(EVENTS), "events_sha256": _sha256(EVENTS),
            "layout": str(LAYOUT), "layout_sha256": _sha256(LAYOUT),
        },
        "output": "H01_PELVIS_TIGHT_RAW_RANGE_FUSION.npz",
        "wall_s": time.perf_counter() - started,
    }
    (output / "AUDIT.json").write_text(json.dumps(audit, indent=2) + "\n")
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--yaw-offset-deg", type=float, default=0.0)
    parser.add_argument("--nominal-range-sigma-m", type=float, default=0.12)
    args = parser.parse_args()
    audit = run(
        args.output,
        yaw_offset_deg=args.yaw_offset_deg,
        nominal_range_sigma_m=args.nominal_range_sigma_m,
    )
    print(json.dumps({
        "status": audit["status"],
        "real_capture": audit["real_capture"],
        "trajectory_diagnostics": audit["trajectory_diagnostics"],
        "wall_s": audit["wall_s"],
    }, indent=2))


if __name__ == "__main__":
    main()
