#!/usr/bin/env python3
"""Ten-node H01 raw-range/root diagnostic using the frozen display FK proxy.

All node UWB rows update one pelvis root directly at range level.  The tag
offsets are derived from the already frozen H01 display skeleton and are
explicitly not promoted to physical antenna phase-centre metrology.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import numpy as np

from biospur_fusion.c2_coupled_progressive.contracts import (
    NODE_TO_SEGMENT,
    load_effective_config,
)
from biospur_fusion.c2_coupled_progressive.estimator import SEGMENTS
from biospur_fusion.c2_coupled_progressive.renderer import display_models, joints_for_frame
from biospur_fusion.c2_uwb_root_world.run_calibration import _clock_models
from biospur_fusion.c2_uwb_root_world.tight_range import (
    PersistentRangeBiasConfig,
    PersistentRangeBiasTracker,
    RawRangeUpdateConfig,
    update_raw_ranges,
)
from biospur_fusion.c2_uwb_root_world.split_fusion import (
    FixedLagDriftConfig,
    FixedLagRangeDriftCorrector,
    SingleFootContactConfig,
    SingleFootVelocityCorrector,
)
from biospur_fusion.ingest.events import RecordType
from biospur_fusion.ingest.v47 import decode_measurements
from biospur_fusion.root_r3.estimator import RootFilterConfig, propagate_inertial

from run_c2_h01_tight_raw_range_fusion import (
    CLOCK_TABLE,
    PELVIS_NODE,
    RAW,
    ROOT,
    _action_bounds_ns,
    _anchors,
    _initialize,
    _pelvis_imu,
    _to_uwb_row,
)


FROZEN_TRAJECTORY = (
    ROOT / "logs/c2_hxx_frozen_replay_20260831_220900/"
    "HXX_FROZEN_C2_REPLAY_TRAJECTORY.npz"
)


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-9:
        raise ValueError("degenerate body basis")
    return np.asarray(vector, float) / norm


def _proxy_offsets() -> tuple[
    np.ndarray,
    dict[str, np.ndarray],
    dict[str, np.ndarray],
    dict[str, np.ndarray],
    dict[str, np.ndarray],
    dict,
]:
    with np.load(FROZEN_TRAJECTORY, allow_pickle=False) as archive:
        trajectory = {"trajectory": {"H01_boxing": {}}, "output_coordinate_convention": {}}
        for segment in SEGMENTS:
            base = f"trajectory/H01_boxing/{segment}"
            trajectory["trajectory"]["H01_boxing"][segment] = {
                "time_root_s": np.array(archive[f"{base}/time_root_s"]),
                "quat_world_segment_wxyz": np.array(archive[f"{base}/quat_world_segment_wxyz"]),
                "mask": np.array(archive[f"{base}/mask"]),
            }
        trajectory["output_coordinate_convention"] = {
            "schema": "biospur-c2-capture-wide-output-coordinates-v1",
            "matrix_world_output_from_internal": np.array(
                archive["output_coordinates/matrix_world_output_from_internal"]
            ),
            "plane_normal_world_internal": np.array(
                archive["output_coordinates/plane_normal_world_internal"]
            ),
        }
    time_s = trajectory["trajectory"]["H01_boxing"]["pelvis"]["time_root_s"]
    config = load_effective_config()
    model = display_models(config)[1]
    by_segment = {segment: [] for segment in NODE_TO_SEGMENT.values()}
    ankles = {"left": [], "right": []}
    first_joints = None
    for frame in range(len(time_s)):
        joints = joints_for_frame(trajectory, "H01_boxing", frame, model, config)
        if first_joints is None:
            first_joints = joints
        centres = {
            "pelvis": joints["pelvis_center"],
            "torso": 0.5 * (joints["pelvis_center"] + joints["shoulder_mid"]),
            "upper_arm_left": 0.5 * (joints["shoulder_left"] + joints["elbow_left"]),
            # The frozen identity ledger names the distal nodes as left/right
            # wrist and left/right ankle.  A segment midpoint is therefore the
            # wrong observation location even for a display-proxy diagnostic.
            "forearm_left": joints["wrist_left"],
            "upper_arm_right": 0.5 * (joints["shoulder_right"] + joints["elbow_right"]),
            "forearm_right": joints["wrist_right"],
            "thigh_left": 0.5 * (joints["hip_left"] + joints["knee_left"]),
            "shank_left": joints["ankle_left"],
            "thigh_right": 0.5 * (joints["hip_right"] + joints["knee_right"]),
            "shank_right": joints["ankle_right"],
        }
        pelvis = joints["pelvis_center"]
        for segment, centre in centres.items():
            by_segment[segment].append(np.asarray(centre) - pelvis)
        ankles["left"].append(np.asarray(joints["ankle_left"]) - pelvis)
        ankles["right"].append(np.asarray(joints["ankle_right"]) - pelvis)

    # Bind the frozen display body's initial proper basis to the operator-bound
    # UWB world basis: body right=-X, forward=-Y, up=+Z.
    right = _normalize(first_joints["hip_right"] - first_joints["hip_left"])
    up_seed = first_joints["shoulder_mid"] - first_joints["pelvis_center"]
    up = _normalize(up_seed - right * float(np.dot(up_seed, right)))
    forward = _normalize(np.cross(up, right))
    up = _normalize(np.cross(right, forward))
    observed = np.column_stack((right, forward, up))
    desired = np.column_stack(([-1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, 1.0]))
    rotation = desired @ observed.T
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-8) or np.linalg.det(rotation) < 0.999:
        raise ValueError("display-to-UWB binding is not a proper rotation")
    offsets = {
        node: np.stack(by_segment[segment]) @ rotation.T
        for node, segment in NODE_TO_SEGMENT.items()
    }
    velocities = {node: np.gradient(values, time_s, axis=0) for node, values in offsets.items()}
    ankle_offsets = {
        side: np.stack(values) @ rotation.T for side, values in ankles.items()
    }
    ankle_velocities = {
        side: np.gradient(values, time_s, axis=0)
        for side, values in ankle_offsets.items()
    }
    return time_s, offsets, velocities, ankle_offsets, ankle_velocities, {
        "source": str(FROZEN_TRAJECTORY),
        "role": "DISPLAY_PROXY_ONLY_NOT_PHASE_CENTRE_METROLOGY",
        "segment_proxy": (
            "frozen wrist/ankle distal landmarks for distal nodes; display "
            "segment midpoint for upper-arm/thigh; torso midpoint; pelvis root"
        ),
        "root_proxy": "frozen_display_pelvis_center",
        "proper_rotation_output_to_uwb": rotation.tolist(),
        "initial_world_body_right": [-1.0, 0.0, 0.0],
        "initial_world_body_forward": [0.0, -1.0, 0.0],
        "initial_world_body_up": [0.0, 0.0, 1.0],
    }


def _interp(time_s: np.ndarray, values: np.ndarray, query_s: float) -> np.ndarray:
    return np.asarray([np.interp(query_s, time_s, values[:, axis]) for axis in range(3)])


def run(
    output: Path,
    *,
    nlos_scale_m: float = 0.12,
    contact_enabled: bool = False,
    drift_enabled: bool = True,
    drift_max_velocity_step_mps: float = 0.020,
    absolute_position_gain: float = 0.20,
    maximum_absolute_position_step_m: float = 0.020,
) -> dict:
    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=False)
    clocks = _clock_models(CLOCK_TABLE)
    start_ns, stop_ns = _action_bounds_ns()
    start_s = start_ns * 1e-9
    stop_s = stop_ns * 1e-9
    anchors = _anchors()
    events, decode_audit = decode_measurements(RAW)
    imu, orientation_audit = _pelvis_imu(events, clocks[PELVIS_NODE], start_ns, 0.0)
    (
        proxy_time,
        offsets,
        offset_velocities,
        ankle_offsets,
        ankle_velocities,
        proxy_audit,
    ) = _proxy_offsets()

    uwb_events = []
    for event in events:
        if event.record_type is not RecordType.UWB or event.node_id not in clocks:
            continue
        row = _to_uwb_row(event)
        clock = clocks[event.node_id]
        valid = [slot for slot in range(8) if row.valid_mask & (1 << slot) and 0 < row.ranges_mm[slot] < 0xFFFF]
        if len(valid) < 4:
            continue
        epoch = float(np.median([
            clock.seconds(row.strobe_us + 0.5 * row.t_round_us[slot]) for slot in valid
        ]))
        if start_s <= epoch < stop_s:
            uwb_events.append((epoch, row, clock))
    uwb_events.sort(key=lambda item: item[0])
    first_pelvis = next(row for _, row, _ in uwb_events if row.node == PELVIS_NODE)
    initial = _initialize(first_pelvis, anchors, clocks[PELVIS_NODE])
    state = initial
    inertial_only = initial
    imu = [row for row in imu if initial.time_s < row["time_s"] < stop_s]
    timeline = [(row["time_s"], 0, row) for row in imu]
    timeline.extend((epoch, 1, (row, clock)) for epoch, row, clock in uwb_events if epoch > initial.time_s)
    timeline.sort(key=lambda item: (item[0], item[1]))

    filter_config = RootFilterConfig()
    # 25 cm includes unmeasured strap/phase-centre versus display-midpoint
    # mismatch. It is deliberately wider than the pelvis-only diagnostic.
    range_config = RawRangeUpdateConfig(
        nominal_sigma_m=0.25,
        positive_nlos_cauchy_scale_m=float(nlos_scale_m),
        uncertainty_provenance=(
            "DISPLAY_PROXY_PLUS_ONE_SIDED_NLOS_DIAGNOSTIC;POSITIVE_CAUCHY_"
            f"SCALE_{float(nlos_scale_m):.3F}M_WITH_0.08_0.20M_STATIC_SENSITIVITY"
        ),
    )
    bias_tracker = PersistentRangeBiasTracker(PersistentRangeBiasConfig())
    drift_config = FixedLagDriftConfig(
        maximum_velocity_step_mps=float(drift_max_velocity_step_mps)
    )
    drift_corrector = FixedLagRangeDriftCorrector(drift_config)
    contact_config = SingleFootContactConfig(enabled=bool(contact_enabled))
    contact_corrector = SingleFootVelocityCorrector(contact_config)
    last_force = np.array([0.0, 0.0, 9.80665])
    last_rotation = np.eye(3)
    cumulative_absolute_correction = np.zeros(3)
    rows = []
    decisions = []
    drift_decisions = []
    contact_decisions = []
    node_counts = {node: {"accepted": 0, "rejected": 0} for node in NODE_TO_SEGMENT}
    for event_time, kind, payload in timeline:
        if event_time <= state.time_s + 1e-12:
            continue
        state, _ = propagate_inertial(state, event_time, last_force, last_rotation, filter_config)
        inertial_only, _ = propagate_inertial(
            inertial_only, event_time, last_force, last_rotation, filter_config
        )
        if kind == 0:
            last_force = payload["acceleration"]
            last_rotation = payload["rotation_world"]
            rel = float(np.clip(event_time - start_s, proxy_time[0], proxy_time[-1]))
            ankle_offset = {
                side: _interp(proxy_time, ankle_offsets[side], rel)
                for side in ("left", "right")
            }
            ankle_velocity = {
                side: _interp(proxy_time, ankle_velocities[side], rel)
                for side in ("left", "right")
            }
            state, contact_decision = contact_corrector.update(
                state,
                ankle_offset_world_m=ankle_offset,
                ankle_offset_velocity_world_mps=ankle_velocity,
            )
            if contact_enabled and contact_decision.reason != "UPDATE_PERIOD_NOT_REACHED":
                contact_decisions.append((event_time, contact_decision))
            left_ankle = state.position_m + ankle_offset["left"]
            right_ankle = state.position_m + ankle_offset["right"]
            rows.append((event_time, state.vector.copy(), inertial_only.vector.copy(), left_ankle, right_ankle))
        else:
            row, clock = payload
            rel = float(np.clip(event_time - start_s, proxy_time[0], proxy_time[-1]))
            offset = _interp(proxy_time, offsets[row.node], rel)
            velocity = _interp(proxy_time, offset_velocities[row.node], rel)
            valid = [
                slot for slot in range(8)
                if row.valid_mask & (1 << slot) and 0 < row.ranges_mm[slot] < 0xFFFF
            ]
            link_epochs = np.asarray([
                clock.seconds(row.strobe_us + 0.5 * row.t_round_us[slot])
                for slot in valid
            ])
            link_dt = link_epochs - float(np.median(link_epochs))
            prior_tag_positions = (
                state.position_m + offset
                + link_dt[:, None] * (state.velocity_mps + velocity)
            )
            prefit_predicted = np.linalg.norm(prior_tag_positions - anchors[valid], axis=1)
            prefit_innovation = (
                np.asarray([row.ranges_mm[slot] for slot in valid], dtype=float) / 1000.0
                - prefit_predicted
            )
            bias_prior = bias_tracker.bias_vector(row.node)
            state_before_absolute = state
            updated, decision = update_raw_ranges(
                state, row, anchors_m=anchors, clock=clock,
                range_bias_m=bias_prior,
                tag_offset_world_m=offset,
                tag_offset_velocity_world_mps=velocity,
                state_update_indices=(0, 1, 2),
                correction_gain=absolute_position_gain,
                maximum_position_step_m=maximum_absolute_position_step_m,
                config=range_config,
            )
            key = "accepted" if decision.accepted else "rejected"
            node_counts[row.node][key] += 1
            if decision.accepted:
                state = updated
            absolute_delta = state.position_m - state_before_absolute.position_m
            cumulative_absolute_correction += absolute_delta
            if drift_enabled:
                state, drift_decision = drift_corrector.observe(
                    state,
                    node=row.node,
                    decision=decision,
                    anchors_m=anchors,
                    tag_offset_world_m=offset,
                    tag_offset_velocity_world_mps=velocity,
                    rotation_world_from_sensor=last_rotation,
                    range_bias_m=bias_prior,
                    cumulative_absolute_position_correction_m=(
                        cumulative_absolute_correction
                    ),
                )
                if drift_decision.reason != "UPDATE_PERIOD_NOT_REACHED":
                    drift_decisions.append(drift_decision)
            bias_tracker.update(row.node, decision)
            decisions.append((
                event_time,
                row.node,
                valid,
                prefit_innovation,
                decision,
                bias_prior,
                absolute_delta,
            ))

    if len(rows) < 100 or not decisions:
        raise RuntimeError("insufficient ten-node output")
    time_out = np.asarray([row[0] for row in rows]) - initial.time_s
    fused = np.stack([row[1] for row in rows])
    imu_only = np.stack([row[2] for row in rows])
    ankle_left = np.stack([row[3] for row in rows])
    ankle_right = np.stack([row[4] for row in rows])
    decision_time = np.asarray([item[0] for item in decisions]) - initial.time_s
    decision_node = np.asarray([item[1] for item in decisions])
    decision_accepted = np.asarray([item[4].accepted for item in decisions], dtype=bool)
    prefit_residual = np.full((len(decisions), 8), np.nan)
    postfit_residual = np.full((len(decisions), 8), np.nan)
    robust_weight = np.full((len(decisions), 8), np.nan)
    measured_range = np.full((len(decisions), 8), np.nan)
    link_epoch = np.full((len(decisions), 8), np.nan)
    range_sigma = np.full((len(decisions), 8), np.nan)
    range_bias_prior = np.stack([item[5] for item in decisions])
    absolute_position_delta = np.stack([item[6] for item in decisions])
    for index, (_, _, valid, prefit, decision, _, _) in enumerate(decisions):
        prefit_residual[index, valid] = prefit
        for local, anchor in enumerate(decision.anchors):
            postfit_residual[index, anchor] = decision.innovations_m[local]
            robust_weight[index, anchor] = decision.robust_weights[local]
            measured_range[index, anchor] = decision.measured_ranges_m[local]
            link_epoch[index, anchor] = decision.link_epochs_s[local] - initial.time_s
            range_sigma[index, anchor] = decision.sigma_m[local]

    drift_time = np.asarray([
        decision.reference_epoch_s - initial.time_s for decision in drift_decisions
    ])
    drift_accepted = np.asarray([decision.accepted for decision in drift_decisions], dtype=bool)
    drift_reason = np.asarray([decision.reason for decision in drift_decisions], dtype="U40")
    drift_rank = np.asarray([decision.rank for decision in drift_decisions], dtype=int)
    drift_condition = np.asarray([decision.condition for decision in drift_decisions])
    drift_velocity_delta = (
        np.stack([decision.velocity_delta_mps for decision in drift_decisions])
        if drift_decisions else np.empty((0, 3))
    )
    drift_bias_delta = (
        np.stack([decision.accelerometer_bias_delta_mps2 for decision in drift_decisions])
        if drift_decisions else np.empty((0, 3))
    )
    drift_singular = np.full((len(drift_decisions), 6), np.nan)
    drift_input_update = []
    drift_input_node = []
    drift_input_anchor = []
    drift_input_lag = []
    drift_input_innovation = []
    drift_input_weight = []
    for update_index, decision in enumerate(drift_decisions):
        drift_singular[update_index, :len(decision.scaled_singular_values)] = (
            decision.scaled_singular_values
        )
        for row_index in range(decision.row_count):
            drift_input_update.append(update_index)
            drift_input_node.append(decision.node[row_index])
            drift_input_anchor.append(decision.anchor[row_index])
            drift_input_lag.append(decision.lag_s[row_index])
            drift_input_innovation.append(decision.innovation_difference_m[row_index])
            drift_input_weight.append(decision.effective_weight[row_index])

    contact_time = np.asarray([row[0] - initial.time_s for row in contact_decisions])
    contact_accepted = np.asarray([row[1].accepted for row in contact_decisions], dtype=bool)
    contact_side = np.asarray([row[1].side or "none" for row in contact_decisions], dtype="U5")
    contact_reason = np.asarray([row[1].reason for row in contact_decisions], dtype="U40")
    contact_innovation = (
        np.stack([row[1].velocity_innovation_mps for row in contact_decisions])
        if contact_decisions else np.empty((0, 3))
    )
    contact_delta = (
        np.stack([row[1].applied_velocity_delta_mps for row in contact_decisions])
        if contact_decisions else np.empty((0, 3))
    )
    np.savez_compressed(
        output / "H01_TEN_NODE_TIGHT_FUSION.npz",
        time_s=time_out,
        fused_root_state=fused,
        imu_only_root_state=imu_only,
        proxy_ankle_left_world_m=ankle_left,
        proxy_ankle_right_world_m=ankle_right,
        anchors_world_m=anchors,
        uwb_time_s=decision_time,
        uwb_node=decision_node,
        uwb_accepted=decision_accepted,
        raw_range_measured_m=measured_range,
        raw_range_link_epoch_s=link_epoch,
        raw_range_sigma_m=range_sigma,
        raw_range_prefit_residual_m=prefit_residual,
        raw_range_postfit_residual_m=postfit_residual,
        raw_range_robust_weight=robust_weight,
        persistent_range_bias_prior_m=range_bias_prior,
        absolute_position_delta_m=absolute_position_delta,
        drift_update_time_s=drift_time,
        drift_update_accepted=drift_accepted,
        drift_update_reason=drift_reason,
        drift_update_rank=drift_rank,
        drift_update_condition=drift_condition,
        drift_update_scaled_singular_values=drift_singular,
        drift_velocity_delta_mps=drift_velocity_delta,
        drift_accelerometer_bias_delta_mps2=drift_bias_delta,
        drift_input_update_index=np.asarray(drift_input_update, dtype=int),
        drift_input_node=np.asarray(drift_input_node),
        drift_input_anchor=np.asarray(drift_input_anchor, dtype=int),
        drift_input_lag_s=np.asarray(drift_input_lag),
        drift_input_innovation_difference_m=np.asarray(drift_input_innovation),
        drift_input_effective_inverse_variance=np.asarray(drift_input_weight),
        contact_time_s=contact_time,
        contact_accepted=contact_accepted,
        contact_side=contact_side,
        contact_reason=contact_reason,
        contact_velocity_innovation_mps=contact_innovation,
        contact_velocity_delta_mps=contact_delta,
    )
    z = fused[:, 2]
    ankle_z = np.minimum(ankle_left[:, 2], ankle_right[:, 2])
    finite_pre = prefit_residual[np.isfinite(prefit_residual)]
    finite_post = postfit_residual[np.isfinite(postfit_residual)]
    finite_weight = np.isfinite(robust_weight)
    reporting_los = finite_weight & (robust_weight >= 0.5)
    reporting_los_residual = np.abs(postfit_residual[reporting_los])
    reporting_los_per_update = np.sum(reporting_los, axis=1)
    residual_by_anchor = {}
    for anchor in range(8):
        values = prefit_residual[:, anchor]
        values = values[np.isfinite(values)]
        residual_by_anchor[chr(ord("A") + anchor)] = {
            "count": int(len(values)),
            "median_m": float(np.median(values)),
            "p10_m": float(np.percentile(values, 10)),
            "p90_m": float(np.percentile(values, 90)),
            "positive_fraction": float(np.mean(values > 0.0)),
        }
    accepted_drift = [decision for decision in drift_decisions if decision.accepted]
    accepted_contact = [row[1] for row in contact_decisions if row[1].accepted]
    accepted_contact_sides = [decision.side for decision in accepted_contact]
    audit = {
        "schema": "biospur.c2.h01.split_drift_absolute_raw_range.v2",
        "status": "DIAGNOSTIC_SPLIT_DRIFT_ABSOLUTE_COMPLETE",
        "scientific_pass": False,
        "old_T4_or_solved_position_consumed": False,
        "fusion_architecture": {
            "nominal_state": "RootState[position,velocity,accelerometer_bias]",
            "imu_role": "200_HZ_NOMINAL_PROPAGATION",
            "absolute_position_channel": {
                "measurement": "7_OR_8_RAW_RANGES_AT_PER_LINK_T_ROUND_OVER_2_EPOCHS",
                "active_state_indices": [0, 1, 2],
                "gain": absolute_position_gain,
                "maximum_step_m": maximum_absolute_position_step_m,
                "velocity_or_bias_nominal_update": False,
                "maximum_observed_step_m": float(np.max(np.linalg.norm(absolute_position_delta, axis=1))),
            },
            "drift_channel": {
                "enabled": bool(drift_enabled),
                "measurement": "SAME_NODE_ANCHOR_FIXED_LAG_INNOVATION_DIFFERENCE",
                "other_channel_ledger_removed": [
                    "PERSISTENT_RANGE_BIAS_CHANGE",
                    "CUMULATIVE_ABSOLUTE_POSITION_CORRECTION",
                ],
                "candidate_state_indices": [3, 4, 5, 6, 7, 8],
                "rank_gated_rule": (
                    "rank_3_updates_velocity_only; rank_6_required_before_"
                    "accelerometer_bias_update"
                ),
                "position_nominal_update": False,
                "minimum_lag_s": drift_config.minimum_lag_s,
                "maximum_lag_s": drift_config.maximum_lag_s,
                "update_period_s": drift_config.update_period_s,
                "accepted_updates": len(accepted_drift),
                "attempt_records": len(drift_decisions),
                "rank_histogram": {
                    str(rank): int(np.sum(drift_rank == rank))
                    for rank in sorted(set(drift_rank.tolist()))
                },
                "cumulative_velocity_delta_norm_mps": float(np.sum([
                    np.linalg.norm(decision.velocity_delta_mps) for decision in accepted_drift
                ])),
                "cumulative_accelerometer_bias_delta_norm_mps2": float(np.sum([
                    np.linalg.norm(decision.accelerometer_bias_delta_mps2) for decision in accepted_drift
                ])),
            },
            "priority": "DRIFT_SUPPRESSION_OVER_INSTANTANEOUS_UWB_POSITION",
        },
        "single_foot_contact": {
            "enabled": bool(contact_enabled),
            "active_state_indices": [3, 4, 5],
            "input": "FROZEN_DISPLAY_PROXY_ANKLE_OFFSET_AND_VELOCITY",
            "maximum_relative_speed_mps": contact_config.maximum_relative_speed_mps,
            "velocity_sigma_mps": contact_config.velocity_sigma_mps,
            "maximum_velocity_step_mps": contact_config.maximum_velocity_step_mps,
            "accepted_updates": len(accepted_contact),
            "minimum_side_dwell_s": contact_config.minimum_side_dwell_s,
            "switch_height_hysteresis_m": contact_config.switch_height_hysteresis_m,
            "accepted_side_switches": int(sum(
                current != previous
                for previous, current in zip(
                    accepted_contact_sides[:-1], accepted_contact_sides[1:]
                )
            )),
            "dual_foot_position_lock_used": False,
            "ground_position_constraint_used": False,
            "side_histogram": {
                side: sum(decision.side == side for decision in accepted_contact)
                for side in ("left", "right")
            },
        },
        "positive_nlos_model": {
            "type": "positive_innovation_Cauchy_influence_times_Huber_guard",
            "active_scale_m": float(nlos_scale_m),
            "static_00_sensitivity_scales_m": [0.08, 0.12, 0.20],
            "range_deletion_used": False,
        },
        "persistent_range_bias": {
            "type": "per_node_anchor_nonnegative_random_walk_scalar_KF",
            "root_bias_cross_covariance_retained": False,
            "final_bias_m": bias_tracker.snapshot(),
            "antenna_phase_centre_calibration_is_current_gate": False,
        },
        "raw_range_updates": len(decisions),
        "nodes": node_counts,
        "pelvis_z_m": {
            "minimum": float(np.min(z)), "p05": float(np.percentile(z, 5)),
            "median": float(np.median(z)), "p95": float(np.percentile(z, 95)),
            "maximum": float(np.max(z)), "span": float(np.ptp(z)),
        },
        "proxy_lowest_ankle_z_m": {
            "minimum": float(np.min(ankle_z)), "median": float(np.median(ankle_z)),
            "maximum": float(np.max(ankle_z)),
        },
        "root_displacement_m": {
            "fused_end": float(np.linalg.norm(fused[-1, :3] - fused[0, :3])),
            "imu_only_end": float(np.linalg.norm(imu_only[-1, :3] - imu_only[0, :3])),
        },
        "range_residuals": {
            "prefit_absolute_m": {
                "median": float(np.median(np.abs(finite_pre))),
                "p90": float(np.percentile(np.abs(finite_pre), 90)),
                "p95": float(np.percentile(np.abs(finite_pre), 95)),
            },
            "postfit_absolute_m": {
                "median": float(np.median(np.abs(finite_post))),
                "p90": float(np.percentile(np.abs(finite_post), 90)),
                "p95": float(np.percentile(np.abs(finite_post), 95)),
            },
            "by_anchor_prefit": residual_by_anchor,
            "effective_los_reporting_only": {
                "robust_weight_threshold": 0.5,
                "threshold_used_by_solver_as_hard_gate": False,
                "fraction_of_available_links": float(
                    np.sum(reporting_los) / np.sum(finite_weight)
                ),
                "fraction_of_updates_with_at_least_four_links": float(
                    np.mean(reporting_los_per_update >= 4)
                ),
                "absolute_postfit_m": {
                    "median": float(np.median(reporting_los_residual)),
                    "p90": float(np.percentile(reporting_los_residual, 90)),
                    "p95": float(np.percentile(reporting_los_residual, 95)),
                },
            },
            "interpretation": (
                "Residuals, weights, and signed anchor structure are diagnostic "
                "evidence; H01 has no external truth, so accepted numerical "
                "updates alone are not a scientific body/world qualification."
            ),
        },
        "proxy_geometry": proxy_audit,
        "orientation": orientation_audit,
        "decode_errors": decode_audit.decode_errors,
        "limitations": [
            "display landmark versus antenna offset is centimetre-scale refinement, not the current gate",
            "persistent range-bias nuisance states remain outside the root cross-covariance",
            "fixed-lag drift rank is evidence per update; weak bias modes are regularized and not claimed fully observable",
            "proxy ankle height is never fed back as a ground-position constraint",
            "H01 has no external trajectory truth, so scientific_pass remains false",
        ],
        "wall_s": time.perf_counter() - started,
    }
    (output / "AUDIT.json").write_text(json.dumps(audit, indent=2) + "\n")
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--nlos-scale-m", type=float, default=0.12)
    parser.add_argument(
        "--contact",
        action="store_true",
        help="enable switchable single-foot velocity-only contact information",
    )
    parser.add_argument("--no-drift", action="store_true")
    parser.add_argument("--drift-max-velocity-step-mps", type=float, default=0.020)
    parser.add_argument("--absolute-position-gain", type=float, default=0.20)
    parser.add_argument("--maximum-absolute-position-step-m", type=float, default=0.020)
    args = parser.parse_args()
    print(json.dumps(run(
        args.output,
        nlos_scale_m=args.nlos_scale_m,
        contact_enabled=args.contact,
        drift_enabled=not args.no_drift,
        drift_max_velocity_step_mps=args.drift_max_velocity_step_mps,
        absolute_position_gain=args.absolute_position_gain,
        maximum_absolute_position_step_m=args.maximum_absolute_position_step_m,
    ), indent=2))


if __name__ == "__main__":
    main()
