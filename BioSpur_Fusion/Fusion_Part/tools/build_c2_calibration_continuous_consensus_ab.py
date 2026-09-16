#!/usr/bin/env python3
"""Run one continuous native-200 root A/B across all acquired calibration actions."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from biospur_fusion.c2_coupled_progressive.calibration_native200_archive import (
    CalibrationNative200Archive,
    EXPECTED_SHA256 as NATIVE200_ARCHIVE_SHA256,
)
from biospur_fusion.c2_coupled_progressive.contracts import EPISODES
from biospur_fusion.c2_coupled_progressive.native200_publication_producer import (
    Native200PublicationProducer,
)
from biospur_fusion.c2_uwb_calibration.antenna_los import rotation_from_wxyz
from biospur_fusion.c2_uwb_root_world.action00_session_offsets import (
    StaticSessionOffsetProfile,
    robust_corrected_epoch_consensus,
)
from biospur_fusion.c2_uwb_root_world.root_correction_slew import (
    CausalRootCorrectionSlew,
)
from biospur_fusion.c2_uwb_root_world.split_fusion import (
    ConsensusAccelerationBiasConfig,
    ConsensusRotationObservation,
    FixedLagConsensusDriftConfig,
    FixedLagConsensusDriftCorrector,
)
from biospur_fusion.root_r3 import (
    AdditiveRootConstraint,
    CausalDelayedRootFilter,
    ImuSample,
    PositionObservation,
    RootFilterConfig,
)
from biospur_fusion.root_r3.replay import initial_state
from biospur_fusion.root_r3.estimator import propagate_inertial
from biospur_fusion.c2_uwb_root_world.continuous_root_ab import (
    hold_mean_no_measurement_gap,
)


ROOT = Path(__file__).resolve().parents[1]
ACTION00 = "00_initial_still"
NATIVE200_ROTATION_OWNER = hashlib.sha256(json.dumps(
    {
        "schema": "biospur.c2.calibration.native200.rotation_owner.v1",
        "sources": {
            str(path.resolve()): digest
            for path, digest in NATIVE200_ARCHIVE_SHA256.items()
        },
        "association": "CAUSAL_PREVIOUS_NATIVE200_SAMPLE_ZOH",
    },
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _profile(document: dict) -> StaticSessionOffsetProfile:
    value = document["profile"]
    return StaticSessionOffsetProfile(
        document["training_start_s"], document["training_stop_s_exclusive"],
        value["common_root_gauge_m"], value["offsets_by_node_m"],
        value["samples_by_node"], 30, value["provenance"], value["digest"],
    )


def _body_epoch_availability_time_s(row: dict) -> float:
    node_times_s = np.asarray([
        float(node["reference_time_s"]) for node in row["nodes"]
    ], dtype=float)
    if node_times_s.size == 0 or not np.isfinite(node_times_s).all():
        raise RuntimeError("body consensus lacks finite constituent node epochs")
    return float(np.max(node_times_s))


def _bootstrap_publication_is_available(
    frame_time_s: float,
    first_availability_time_s: float,
) -> bool:
    return float(frame_time_s) + 1e-12 >= float(first_availability_time_s)


def _diagnostic_consensus_drift_config(
    *, enable_bias: bool, rotation_owner: str, action_id: str,
) -> FixedLagConsensusDriftConfig:
    bias_policy = ConsensusAccelerationBiasConfig(
        minimum_distinct_epochs=5,
        maximum_scaled_condition=500.0,
        temporal_huber_threshold_sigma=2.5,
        maximum_robust_standardized_rms=1.5,
        maximum_accelerometer_bias_step_mps2=0.20,
        rotation_owner=rotation_owner,
        rotation_action_id=action_id,
        minimum_inlier_epochs=5,
        maximum_bias_window_epochs=8,
        maximum_candidate_subsets=56,
    ) if enable_bias else None
    return FixedLagConsensusDriftConfig(
        minimum_lag_s=0.32,
        maximum_lag_s=0.72,
        update_period_s=0.48,
        minimum_consensus_pairs=4,
        rank_relative_tolerance=1e-3 if enable_bias else 1e-2,
        maximum_velocity_step_mps=0.50,
        covariance_floor=1e-12,
        acceleration_bias=bias_policy,
    )


def _action_observations(
    facts_path: Path,
    profile: StaticSessionOffsetProfile,
    *,
    exclude_training_prefix: bool,
) -> tuple[list[PositionObservation], list[dict]]:
    observations = []
    audit = []
    for line in facts_path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        time_s = float(row["reference_time_s"])
        node_times_s = np.asarray([
            float(node["reference_time_s"]) for node in row["nodes"]
        ], dtype=float)
        availability_time_s = _body_epoch_availability_time_s(row)
        if availability_time_s + 1e-12 < time_s:
            raise RuntimeError("body consensus availability precedes measurement")
        if exclude_training_prefix and time_s < profile.training_stop_s:
            continue
        candidates = {
            node["node"]: node["candidate_root_world_m"]
            for node in row["nodes"] if node["direct_usable"]
        }
        try:
            consensus = robust_corrected_epoch_consensus(profile, candidates)
        except ValueError:
            continue
        retained = np.asarray([
            consensus.corrected_positions_m[node]
            for node in consensus.trusted_nodes
        ])
        spread = np.linalg.norm(retained - consensus.root_position_m, axis=1)
        sigma = max(0.12, float(np.median(spread)))
        observations.append(PositionObservation(
            time_s, availability_time_s, consensus.root_position_m,
            np.eye(3) * sigma**2, "C2_BODY_CONSENSUS", tuple(range(8)),
            "ACTION00_PROFILE_ROBUST_CONSENSUS", True, True,
            int(row["epoch_sequence"]),
        ))
        audit.append({
            "measurement_time_s": time_s,
            "availability_time_s": availability_time_s,
            "minimum_constituent_time_s": float(np.min(node_times_s)),
            "maximum_constituent_time_s": availability_time_s,
            "trusted_nodes": list(consensus.trusted_nodes),
            "rejected_nodes": list(consensus.rejected_nodes),
            "root_position_m": consensus.root_position_m.tolist(),
            "sigma_m": sigma,
        })
    return observations, audit


def _assign_session_source_sequences(
    observations: list[PositionObservation], audit: list[dict], *, start: int,
) -> tuple[list[PositionObservation], list[dict], int]:
    """Replace action-local epoch identities with one session chronology."""

    if start < 0 or len(observations) != len(audit):
        raise ValueError("invalid session source-sequence assignment")
    assigned = []
    assigned_audit = []
    for offset, (observation, audit_row) in enumerate(zip(observations, audit)):
        local_sequence = int(observation.source_sequence)
        session_sequence = start + offset
        assigned.append(replace(observation, source_sequence=session_sequence))
        row = dict(audit_row)
        row["action_local_epoch_sequence"] = local_sequence
        row["session_global_source_sequence"] = session_sequence
        assigned_audit.append(row)
    return assigned, assigned_audit, start + len(assigned)


def _apply_no_update_gap(root: CausalDelayedRootFilter, stop_s: float) -> None:
    if stop_s <= root.current_state.time_s + 1e-12:
        return
    plan = root._prepare_no_update_gap(
        gap_start_time_s=root.current_state.time_s,
        gap_end_time_s=stop_s,
        availability_time_s=stop_s,
    )
    root._apply_prevalidated_no_update_gap(plan)


def _metrics(
    values: np.ndarray,
    reference: np.ndarray,
    *,
    time_s: np.ndarray,
    span: np.ndarray,
) -> dict:
    displacement = np.linalg.norm(values - reference, axis=1)
    steps = np.linalg.norm(np.diff(values, axis=0), axis=1)
    dt = np.diff(time_s)
    same_span = np.asarray(span[1:] == span[:-1], dtype=bool)
    native200 = same_span & (dt >= 0.0045) & (dt <= 0.0055)
    native_indices = np.flatnonzero(native200)
    if native_indices.size:
        local = int(native_indices[np.argmax(steps[native_indices])])
        maximum_step = float(steps[local])
        step_identity = {
            "before_index": local,
            "after_index": local + 1,
            "t0_s": float(time_s[local]),
            "t1_s": float(time_s[local + 1]),
            "dt_s": float(dt[local]),
            "span": int(span[local]),
        }
        p99 = float(np.quantile(steps[native_indices], 0.99))
    else:
        maximum_step = 0.0
        p99 = 0.0
        step_identity = None
    return {
        "endpoint_displacement_m": float(displacement[-1]),
        "maximum_displacement_m": float(displacement.max()),
        "rms_displacement_m": float(np.sqrt(np.mean(displacement**2))),
        "maximum_native200_step_m": maximum_step,
        "maximum_native200_step_identity": step_identity,
        "p99_native200_step_m": p99,
        "same_span_native200_step_count": int(native_indices.size),
        "excluded_gap_or_non_native_step_count": int(len(steps) - native_indices.size),
    }


def _authenticated_anchor_bounds(batch: Path, action: str) -> tuple[np.ndarray, np.ndarray, dict]:
    action_result = json.loads((batch / f"{action}.stdout.json").read_text(encoding="utf-8"))
    layout = Path(action_result["layout_path"]).resolve()
    if _sha256(layout) != action_result["layout_sha256"]:
        raise RuntimeError("authenticated anchor layout hash mismatch")
    document = json.loads(layout.read_text(encoding="utf-8"))
    rows = sorted(document["anchors"], key=lambda row: int(row["id"]))
    if [int(row["id"]) for row in rows] != list(range(8)):
        raise RuntimeError("anchor layout is not canonical 0..7")
    anchors = np.asarray([
        [row["x_mm"], row["y_mm"], row["z_mm"]] for row in rows
    ], dtype=float) / 1000.0
    if anchors.shape != (8, 3) or not np.isfinite(anchors).all():
        raise RuntimeError("anchor layout is not a finite 8x3 array")
    return anchors.min(axis=0) - 0.75, anchors.max(axis=0) + 0.75, {
        "path": str(layout),
        "sha256": action_result["layout_sha256"],
        "margin_m": 0.75,
    }


def run(
    *, batch: Path, profile_result: Path, output: Path,
    actions: tuple[str, ...] = EPISODES,
    maximum_action_duration_s: float | None = None,
    enable_consensus_bias: bool = False,
) -> dict:
    output = output.resolve()
    if output.exists() or ROOT not in output.parents:
        raise ValueError("output must be a new directory under Fusion_Part")
    output.mkdir(parents=False)
    started = time.monotonic()
    profile = _profile(json.loads(profile_result.read_text(encoding="utf-8")))
    archive = CalibrationNative200Archive.from_sealed_archives()
    body_owner = Native200PublicationProducer.from_sealed_archives()
    clock = body_owner._clocks[ACTION00]

    observations = {}
    observation_audit = {}
    facts_bindings = {}
    canonical_actions = tuple(action for action in EPISODES if action in actions)
    if (
        not actions or any(action not in EPISODES for action in actions)
        or actions != canonical_actions
    ):
        raise ValueError("actions must be a non-empty ordered subset of calibration episodes")
    if maximum_action_duration_s is not None and (
        len(actions) != 1
        or not np.isfinite(maximum_action_duration_s)
        or maximum_action_duration_s <= 0.0
    ):
        raise ValueError("bounded action duration requires one action and a positive duration")
    next_uwb_source_sequence = 0
    for action in actions:
        facts = batch / action / "BODY_EPOCH_FACTS.jsonl"
        facts_bindings[action] = {
            "path": str(facts.resolve()),
            "sha256": _sha256(facts),
        }
        rows, audit = _action_observations(
            facts, profile, exclude_training_prefix=action == ACTION00,
        )
        if not rows:
            raise RuntimeError(f"action has no usable UWB consensus: {action}")
        rows, audit, next_uwb_source_sequence = _assign_session_source_sequences(
            rows, audit, start=next_uwb_source_sequence,
        )
        observations[action] = rows
        observation_audit[action] = audit

    first = observations[actions[0]][0]
    publication_not_before_s = float(first.availability_time_s)
    excluded_bootstrap_frame_count = 0
    initial = first.root_position_m.copy()
    config = RootFilterConfig(
        inertial_acceleration_noise_mps2_sqrt_hz=0.30,
        accelerometer_bias_rw_mps3_sqrt_hz=0.003,
        nis_limit_3d=100.0,
        maximum_position_influence_m=0.05,
        fixed_lag_s=0.20,
        uwb_stale_s=0.60,
        uwb_dropout_s=1.20,
        recovery_good_events=3,
    )
    pure = initial_state(first.measurement_time_s, initial)
    fused = CausalDelayedRootFilter(
        initial_state(first.measurement_time_s, initial), config, inertial=True,
    )
    slew = CausalRootCorrectionSlew(
        release_period_s=0.12,
        maximum_correction_m=config.maximum_position_influence_m,
    )
    slew.sample(
        first.measurement_time_s,
        fused.current_state.position_m,
        fused.current_state.velocity_mps,
    )
    if enable_consensus_bias and len(actions) != 1:
        raise ValueError("consensus bias diagnostic requires one action")
    rotation_owner = hashlib.sha256(json.dumps({
        "archive_owner": NATIVE200_ROTATION_OWNER,
        "base_pose_owner": body_owner.base_pose_owner_digest,
        "action": actions[0],
        "association": "CAUSAL_PREVIOUS_NATIVE200_SAMPLE_ZOH",
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    drift_config = _diagnostic_consensus_drift_config(
        enable_bias=enable_consensus_bias,
        rotation_owner=rotation_owner,
        action_id=actions[0],
    )
    bias_policy = drift_config.acceleration_bias
    drift = FixedLagConsensusDriftCorrector(drift_config)
    cumulative_absolute_correction_m = np.zeros(3)

    all_time = []
    all_pure = []
    all_fused = []
    all_action_index = []
    all_span = []
    all_velocity = []
    all_bias = []
    decisions = []
    drift_decisions = []
    gap_rows = []
    action_metrics = {}
    sequence = 0
    for action_index, action in enumerate(actions):
        source = archive.actions[action]
        absolute_s = np.asarray([
            clock.global_ns(int(timer)) * 1e-9 for timer in source.time_us
        ])
        rotations = np.asarray([
            body_owner._alignment @ rotation_from_wxyz(value)
            for value in source.sensor_quat_wxyz
        ])
        start = int(np.searchsorted(
            absolute_s, pure.time_s, side="right",
        ))
        if start >= len(absolute_s):
            raise RuntimeError(f"action has no native-200 frame after state: {action}")
        if absolute_s[start] - pure.time_s > 0.005001:
            gap_rows.append({
                "before_action": action,
                "start_s": float(pure.time_s),
                "stop_s": float(absolute_s[start]),
                "duration_s": float(absolute_s[start] - pure.time_s),
            })
            pure = hold_mean_no_measurement_gap(
                pure, float(absolute_s[start]), config,
            )
            _apply_no_update_gap(fused, float(absolute_s[start]))
            drift.clear_derivative_history()
            slew.sample(
                float(absolute_s[start]), fused.current_state.position_m,
                fused.current_state.velocity_mps,
            )
            start += 1
        cursor = int(np.searchsorted(
            [row.availability_time_s for row in observations[action]],
            pure.time_s, side="right",
        ))
        if action_index == 0:
            cursor = max(cursor, 1)
        action_begin = len(all_time)
        previous_span = source.span[start - 1] if start else source.span[0]
        for frame in range(start, len(absolute_s)):
            time_s = float(absolute_s[frame])
            if (
                maximum_action_duration_s is not None
                and time_s >= first.measurement_time_s + maximum_action_duration_s
            ):
                break
            if source.span[frame] != previous_span:
                gap_rows.append({
                    "inside_action": action,
                    "start_s": float(pure.time_s),
                    "stop_s": time_s,
                    "duration_s": float(time_s - pure.time_s),
                })
                pure = hold_mean_no_measurement_gap(pure, time_s, config)
                _apply_no_update_gap(fused, time_s)
                drift.clear_derivative_history()
                slew.sample(
                    time_s, fused.current_state.position_m,
                    fused.current_state.velocity_mps,
                )
                previous_span = source.span[frame]
                continue
            rows = observations[action]
            while cursor < len(rows) and rows[cursor].availability_time_s < time_s:
                original = rows[cursor]
                measurement_time_s = original.measurement_time_s
                availability_time_s = original.availability_time_s
                if availability_time_s <= fused.current_state.time_s + 1e-12:
                    raise RuntimeError(
                        f"UWB chronology overlaps published state: {action}/{cursor}"
                    )
                fused.advance_to_availability(availability_time_s)
                observation = PositionObservation(
                    measurement_time_s, availability_time_s,
                    original.root_position_m, original.covariance_m2,
                    original.tag_id, original.anchors, original.quality_state,
                    original.frame_valid, original.physical_point_valid,
                    original.source_sequence,
                )
                absolute_before = fused.current_state
                decision = fused.add_position(
                    observation, processing_time_s=availability_time_s,
                    state_update_indices=(0, 1, 2),
                )
                absolute_bias_delta_mps2 = (
                    fused.current_state.accelerometer_bias_mps2
                    - absolute_before.accelerometer_bias_mps2
                )
                if (
                    decision.accepted
                    and fused.current_state.vector[3:9].tobytes()
                    != absolute_before.vector[3:9].tobytes()
                ):
                    raise RuntimeError(
                        "absolute position update mutated velocity or bias"
                    )
                if decision.accepted:
                    if (
                        decision.availability_applied_velocity_delta_mps.tobytes()
                        != np.zeros(3).tobytes()
                    ):
                        raise RuntimeError("absolute position update mutated velocity")
                    cumulative_absolute_correction_m += (
                        decision.applied_position_delta_m
                    )
                    slew.install(
                        availability_time_s,
                        decision.availability_applied_position_delta_m,
                    )
                before_drift = fused.current_state
                measurement_state = fused.committed_state_at(measurement_time_s)
                rotation_observation = None
                if enable_consensus_bias:
                    rotation_frame = int(np.searchsorted(
                        absolute_s, measurement_time_s, side="right",
                    ) - 1)
                    if rotation_frame < 0:
                        raise RuntimeError("UWB precedes native200 rotation source")
                    if rotation_frame + 1 >= len(absolute_s):
                        raise RuntimeError("UWB rotation lacks strict-floor successor")
                    rotation_source_time_s = float(absolute_s[rotation_frame])
                    next_rotation_source_time_s = float(absolute_s[rotation_frame + 1])
                    if (
                        source.span[rotation_frame + 1] != source.span[rotation_frame]
                        or not rotation_source_time_s <= measurement_time_s < next_rotation_source_time_s
                        or next_rotation_source_time_s - rotation_source_time_s > 0.005001
                    ):
                        raise RuntimeError("UWB rotation strict-floor source is invalid")
                    rotation_observation = ConsensusRotationObservation.from_owner(
                        association_measurement_time_s=measurement_time_s,
                        source_measurement_time_s=rotation_source_time_s,
                        availability_time_s=rotation_source_time_s,
                        association_sequence=int(observation.source_sequence),
                        action_id=action,
                        source_frame=rotation_frame,
                        source_span=int(source.span[rotation_frame]),
                        next_source_measurement_time_s=next_rotation_source_time_s,
                        next_source_frame=rotation_frame + 1,
                        next_source_span=int(source.span[rotation_frame + 1]),
                        tag_id=observation.tag_id,
                        anchors=tuple(observation.anchors),
                        rotation_owner=rotation_owner,
                        rotation_world_from_sensor=rotations[rotation_frame],
                    )
                drift_state, drift_decision = drift.observe(
                    before_drift,
                    observation=observation,
                    post_absolute_position_at_measurement_m=(
                        measurement_state.state.position_m
                    ),
                    cumulative_absolute_position_correction_m=(
                        cumulative_absolute_correction_m
                    ),
                    trusted_node_count=len(
                        observation_audit[action][cursor]["trusted_nodes"]
                    ),
                    total_body_nodes=10,
                    rotation_observation=rotation_observation,
                )
                if drift_decision.accepted:
                    if drift_state.position_m.tobytes() != before_drift.position_m.tobytes():
                        raise RuntimeError("consensus drift update mutated position")
                    delta = drift_state.vector - before_drift.vector
                    fused.apply_current_constraint(
                        drift_state,
                        operator=AdditiveRootConstraint(delta),
                        owner="FIXED_LAG_BODY_CONSENSUS_VELOCITY_AND_BIAS",
                    )
                drift_decisions.append({
                    "action": action,
                    "measurement_time_s": measurement_time_s,
                    "availability_time_s": availability_time_s,
                    "source_sequence": int(observation.source_sequence),
                    "accepted": bool(drift_decision.accepted),
                    "reason": drift_decision.reason,
                    "velocity_delta_mps": drift_decision.velocity_delta_mps.tolist(),
                    "bias_delta_mps2": drift_decision.accelerometer_bias_delta_mps2.tolist(),
                    "rank": int(drift_decision.rank),
                    "condition": float(drift_decision.condition),
                    "scaled_singular_values": drift_decision.scaled_singular_values.tolist(),
                    "bias_fit_status": drift_decision.bias_fit_status,
                    "bias_fit_inlier_epochs": drift_decision.bias_fit_inlier_epochs,
                    "bias_fit_robust_standardized_rms": (
                        drift_decision.bias_fit_robust_standardized_rms
                    ),
                    "bias_fit_rank": drift_decision.bias_fit_rank,
                    "bias_fit_condition": drift_decision.bias_fit_condition,
                    "rotation_observation_digest": (
                        None if rotation_observation is None
                        else rotation_observation.canonical_digest
                    ),
                })
                decisions.append({
                    "action": action,
                    "measurement_time_s": measurement_time_s,
                    "availability_time_s": availability_time_s,
                    "measurement_state_token_digest": measurement_state.digest,
                    "measurement_state_token_revision": (
                        measurement_state.publication_revision
                    ),
                    "accepted": bool(decision.accepted),
                    "reason": decision.reason,
                    "nis": None if decision.nis is None else float(decision.nis),
                    "availability_applied_delta_m": (
                        decision.availability_applied_position_delta_m.tolist()
                    ),
                    "availability_applied_velocity_delta_mps": (
                        decision.availability_applied_velocity_delta_mps.tolist()
                    ),
                    "availability_applied_bias_delta_mps2": (
                        absolute_bias_delta_mps2
                    ).tolist(),
                })
                cursor += 1
            sample = ImuSample(
                time_s, time_s, source.calibrated_acc_mps2[frame],
                rotations[frame], sequence,
            )
            sequence += 1
            pure, _ = propagate_inertial(
                pure, time_s, source.calibrated_acc_mps2[frame],
                rotations[frame], config,
            )
            if not fused.add_imu(sample):
                raise RuntimeError(f"native-200 IMU rejected: {action}/{frame}")
            publication = slew.sample(
                time_s, fused.current_state.position_m,
                fused.current_state.velocity_mps,
            )
            if not _bootstrap_publication_is_available(
                time_s, publication_not_before_s,
            ):
                excluded_bootstrap_frame_count += 1
                previous_span = source.span[frame]
                continue
            all_time.append(time_s)
            all_pure.append(pure.position_m.copy())
            all_fused.append(publication.position_m.copy())
            all_action_index.append(action_index)
            all_span.append(int(source.span[frame]))
            all_velocity.append(fused.current_state.velocity_mps.copy())
            all_bias.append(fused.current_state.accelerometer_bias_mps2.copy())
            previous_span = source.span[frame]
        begin = action_begin
        stop = len(all_time)
        action_reference_a = np.asarray(all_pure[begin])
        action_reference_b = np.asarray(all_fused[begin])
        action_decisions = [row for row in decisions if row["action"] == action]
        action_metrics[action] = {
            "frames": stop - begin,
            "uwb_observations": len(observations[action]),
            "uwb_accepted": sum(row["accepted"] for row in action_decisions),
            "uwb_rejected": sum(not row["accepted"] for row in action_decisions),
            "pure_imu_from_action_start": _metrics(
                np.asarray(all_pure[begin:stop]), action_reference_a,
                time_s=np.asarray(all_time[begin:stop]),
                span=np.asarray(all_span[begin:stop]),
            ),
            "imu_uwb_from_action_start": _metrics(
                np.asarray(all_fused[begin:stop]), action_reference_b,
                time_s=np.asarray(all_time[begin:stop]),
                span=np.asarray(all_span[begin:stop]),
            ),
        }
        print(json.dumps({
            "checkpoint_action": action,
            "completed_actions": action_index + 1,
            "native200_frames": len(all_time),
            "uwb_decisions": len(decisions),
            "elapsed_s": time.monotonic() - started,
        }), flush=True)

    time_axis = np.asarray(all_time)
    roots_a = np.asarray(all_pure)
    roots_b = np.asarray(all_fused)
    action_index = np.asarray(all_action_index, dtype=np.int16)
    spans = np.asarray(all_span, dtype=np.int32)
    velocities_b = np.asarray(all_velocity)
    biases_b = np.asarray(all_bias)
    np.savez_compressed(
        output / "CONTINUOUS_ROOT_TRAJECTORIES.npz",
        global_time_s=time_axis,
        action_index=action_index,
        pure_imu_root_m=roots_a,
        imu_uwb_root_m=roots_b,
        initial_root_m=initial,
        source_span=spans,
        imu_uwb_velocity_mps=velocities_b,
        imu_uwb_accelerometer_bias_mps2=biases_b,
    )
    (output / "UWB_DECISIONS.json").write_text(
        json.dumps(decisions, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "UWB_OBSERVATION_AUDIT.json").write_text(
        json.dumps(observation_audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "DRIFT_DECISIONS.json").write_text(
        json.dumps(drift_decisions, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    anchor_lower, anchor_upper, anchor_owner = _authenticated_anchor_bounds(
        batch, actions[0],
    )
    within_anchor_envelope = bool(np.all(
        (roots_b >= anchor_lower) & (roots_b <= anchor_upper)
    ))
    bias_delta = biases_b - biases_b[0]
    action04_gates = {
        "finite": bool(
            np.isfinite(roots_b).all() and np.isfinite(velocities_b).all()
            and np.isfinite(biases_b).all()
        ),
        "absolute_updates_velocity_inert": all(
            np.asarray(row["availability_applied_velocity_delta_mps"]).tobytes()
            == np.zeros(3).tobytes()
            for row in decisions if row["accepted"]
        ),
        "absolute_updates_bias_inert": all(
            np.asarray(row["availability_applied_bias_delta_mps2"]).tobytes()
            == np.zeros(3).tobytes()
            for row in decisions if row["accepted"]
        ),
        "inside_authenticated_anchor_envelope_plus_margin": within_anchor_envelope,
        "same_span_maximum_native200_step_le_5mm": bool(
            action_metrics[actions[0]]["imu_uwb_from_action_start"]["maximum_native200_step_m"]
            <= 0.005 + 1e-12
        ),
        "velocity_correction_bounded": all(
            np.linalg.norm(row["velocity_delta_mps"]) <= 0.50 + 1e-12
            for row in drift_decisions if row["accepted"]
        ),
        "bias_correction_bounded": all(
            np.linalg.norm(row["bias_delta_mps2"]) <= 0.20 + 1e-12
            for row in drift_decisions if row["accepted"]
        ),
    }
    result = {
        "schema": "biospur.c2.calibration.continuous_consensus_ab.v1",
        "status": (
            "ACTION_DIAGNOSTIC_PASS" if all(action04_gates.values())
            else "ACTION_DIAGNOSTIC_FAIL"
        ) if len(actions) == 1 else "FULL_ACQUIRED_CALIBRATION_CONTINUOUS_AB_COMPLETE",
        "product_ready": False,
        "scientific_pass": False,
        "acquired_actions": list(actions),
        "acquired_action_count": len(actions),
        "protocol_action_01": "OPERATOR_SKIPPED_NOT_ACQUIRED",
        "state_reset_at_action_boundary": False,
        "unobserved_inter_action_interval_policy": "MEAN_HELD_COVARIANCE_GROWN",
        "native200_frames": len(time_axis),
        "uwb_decisions": len(decisions),
        "uwb_accepted": sum(row["accepted"] for row in decisions),
        "uwb_rejected": sum(not row["accepted"] for row in decisions),
        "decision_reasons": dict(Counter(row["reason"] for row in decisions)),
        "drift_decisions": len(drift_decisions),
        "drift_accepted": sum(row["accepted"] for row in drift_decisions),
        "drift_rejected": sum(not row["accepted"] for row in drift_decisions),
        "drift_decision_reasons": dict(Counter(row["reason"] for row in drift_decisions)),
        "gap_count": len(gap_rows),
        "gap_total_s": float(sum(row["duration_s"] for row in gap_rows)),
        "global_pure_imu": _metrics(
            roots_a, initial, time_s=time_axis, span=spans,
        ),
        "global_imu_uwb": _metrics(
            roots_b, initial, time_s=time_axis, span=spans,
        ),
        "root_state_deltas": {
            "position_m": (roots_b[-1] - roots_b[0]).tolist(),
            "velocity_mps": (velocities_b[-1] - velocities_b[0]).tolist(),
            "accelerometer_bias_mps2": (biases_b[-1] - biases_b[0]).tolist(),
        },
        "bounded_prefix": {
            "maximum_action_duration_s": maximum_action_duration_s,
            "start_measurement_time_s": float(first.measurement_time_s),
            "stop_measurement_time_s_exclusive": (
                None if maximum_action_duration_s is None
                else float(first.measurement_time_s + maximum_action_duration_s)
            ),
        },
        "consensus_acceleration_bias": {
            "enabled": enable_consensus_bias,
            "rotation_owner": rotation_owner if enable_consensus_bias else None,
            "base_pose_owner": (
                body_owner.base_pose_owner_digest if enable_consensus_bias else None
            ),
            "rotation_association": "CAUSAL_PREVIOUS_NATIVE200_SAMPLE_ZOH",
            "diagnostic_fixture_policy": (
                None if bias_policy is None else {
                    name: value for name, value in bias_policy.__dict__.items()
                }
            ),
        },
        "anchor_envelope": {
            **anchor_owner,
            "lower_m": anchor_lower.tolist(),
            "upper_m": anchor_upper.tolist(),
        },
        "diagnostic_gates": action04_gates,
        "action_metrics": action_metrics,
        "profile_digest": profile.digest,
        "profile_result_sha256": _sha256(profile_result),
        "batch": str(batch.resolve()),
        "body_epoch_facts": facts_bindings,
        "bootstrap_publication_exclusion": {
            "measurement_time_s": float(first.measurement_time_s),
            "availability_time_s": publication_not_before_s,
            "duration_s": float(
                publication_not_before_s - first.measurement_time_s
            ),
            "excluded_native200_frames": excluded_bootstrap_frame_count,
            "policy": (
                "BUFFER_IMU_FROM_BOOTSTRAP_MEASUREMENT_TO_AVAILABILITY;"
                "NO_PUBLICATION_OR_METRICS_BEFORE_AVAILABILITY"
            ),
        },
        "pose_contract": "IDENTICAL_SEALED_NATIVE200_FK_IK_ROOT_TRANSLATION_ONLY",
        "correction_publication": {
            "owner": "CausalRootCorrectionSlew",
            "release_period_s": 0.12,
            "maximum_filter_position_influence_m": 0.05,
            "installed_corrections": slew.installed_count,
        },
        "limitations": [
            "inter-action sensor data were not recorded and are represented as explicit gaps",
            "Action00 session-relative offsets are diagnostic, not antenna calibration",
            "runtime covariance and NIS threshold are not yet Vicon-qualified",
        ],
        "wall_s": time.monotonic() - started,
    }
    result_path = output / "RESULT.json"
    result_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    files = [
        output / "CONTINUOUS_ROOT_TRAJECTORIES.npz",
        output / "UWB_DECISIONS.json",
        output / "UWB_OBSERVATION_AUDIT.json",
        output / "DRIFT_DECISIONS.json",
        result_path,
    ]
    (output / "SHA256SUMS").write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in files),
        encoding="utf-8",
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--profile-result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--action", choices=EPISODES)
    parser.add_argument("--maximum-action-duration-s", type=float)
    parser.add_argument("--enable-consensus-bias", action="store_true")
    arguments = parser.parse_args()
    print(json.dumps(run(
        batch=arguments.batch,
        profile_result=arguments.profile_result,
        output=arguments.output,
        actions=(arguments.action,) if arguments.action else EPISODES,
        maximum_action_duration_s=arguments.maximum_action_duration_s,
        enable_consensus_bias=arguments.enable_consensus_bias,
    ), indent=2, sort_keys=True))
