from __future__ import annotations

import pickle
from dataclasses import replace

import numpy as np
import pytest

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
from biospur_fusion.root_r3.estimator import propagate_inertial, update_position
from biospur_fusion.root_r3.models import RootState
from tools.build_c2_calibration_continuous_consensus_ab import (
    _assign_session_source_sequences,
    _body_epoch_availability_time_s,
    _bootstrap_publication_is_available,
    _diagnostic_consensus_drift_config,
)


def _state(time_s: float, vector: np.ndarray | None = None) -> RootState:
    return RootState(
        time_s,
        np.zeros(9) if vector is None else np.asarray(vector, dtype=float).copy(),
        np.eye(9, dtype=float) * 0.1,
    )


def test_partial_body_epoch_uses_latest_constituent_availability_and_blocks_early_publication() -> None:
    row = {
        "nodes": [
            {"node": "BSF1120", "reference_time_s": 12.000},
            {"node": "BSF3C79", "reference_time_s": 12.008},
            {"node": "BSF44AD", "reference_time_s": 12.016},
            {"node": "BSF6C53", "reference_time_s": 12.031},
            {"node": "BSF8BC4", "reference_time_s": 12.039},
            {"node": "BSFAA61", "reference_time_s": 12.047},
            {"node": "BSFB165", "reference_time_s": 12.052},
            {"node": "BSFC2CC", "reference_time_s": 12.055},
        ],
    }
    availability = _body_epoch_availability_time_s(row)
    assert availability == 12.055
    assert not _bootstrap_publication_is_available(12.050, availability)
    assert _bootstrap_publication_is_available(availability, availability)


def test_action_local_epoch_reset_becomes_strict_session_sequence_and_audit_keeps_both() -> None:
    def observation(epoch_s: float, local_sequence: int) -> PositionObservation:
        return PositionObservation(
            epoch_s, epoch_s, np.zeros(3), np.eye(3) * 0.02,
            "C2_BODY_CONSENSUS", tuple(range(8)),
            "ACTION00_PROFILE_ROBUST_CONSENSUS", True, True,
            local_sequence,
        )

    first, first_audit, cursor = _assign_session_source_sequences(
        [observation(1.0, 0), observation(1.12, 1)], [{}, {}], start=0,
    )
    second, second_audit, cursor = _assign_session_source_sequences(
        [observation(2.0, 0), observation(2.12, 1)], [{}, {}], start=cursor,
    )
    combined = first + second
    audit = first_audit + second_audit

    assert [row.source_sequence for row in combined] == [0, 1, 2, 3]
    assert [row["action_local_epoch_sequence"] for row in audit] == [0, 1, 0, 1]
    assert [row["session_global_source_sequence"] for row in audit] == [0, 1, 2, 3]
    assert cursor == 4

    corrector = FixedLagConsensusDriftCorrector(_consensus_config())
    reasons = []
    for row in combined:
        state = _state(row.availability_time_s)
        _, decision = corrector.observe(
            state,
            observation=row,
            post_absolute_position_at_measurement_m=state.position_m,
            cumulative_absolute_position_correction_m=np.zeros(3),
            trusted_node_count=10,
            total_body_nodes=10,
        )
        reasons.append(decision.reason)
    assert "STALE_OR_REPLAYED_SOURCE_SEQUENCE" not in reasons


def test_runner_bias_rank_gate_is_exact_and_velocity_only_gate_is_unchanged() -> None:
    bias = _diagnostic_consensus_drift_config(
        enable_bias=True,
        rotation_owner="a" * 64,
        action_id="04_shoulder_left",
    )
    velocity_only = _diagnostic_consensus_drift_config(
        enable_bias=False,
        rotation_owner="unused",
        action_id="04_shoulder_left",
    )
    assert bias.rank_relative_tolerance == 1e-3
    assert bias.acceleration_bias is not None
    assert velocity_only.rank_relative_tolerance == 1e-2
    assert velocity_only.acceleration_bias is None


def _consensus_config() -> FixedLagConsensusDriftConfig:
    return FixedLagConsensusDriftConfig(
        minimum_lag_s=0.32,
        maximum_lag_s=0.72,
        update_period_s=0.48,
        minimum_consensus_pairs=4,
        rank_relative_tolerance=1e-2,
        maximum_velocity_step_mps=0.50,
        covariance_floor=1e-12,
    )


def _consensus_bias_config() -> FixedLagConsensusDriftConfig:
    return FixedLagConsensusDriftConfig(
        minimum_lag_s=0.32,
        maximum_lag_s=0.72,
        update_period_s=0.48,
        minimum_consensus_pairs=4,
        rank_relative_tolerance=1e-3,
        maximum_velocity_step_mps=0.50,
        covariance_floor=1e-12,
        acceleration_bias=ConsensusAccelerationBiasConfig(
            minimum_distinct_epochs=5,
            maximum_scaled_condition=500.0,
            temporal_huber_threshold_sigma=2.5,
            maximum_robust_standardized_rms=1.5,
            maximum_accelerometer_bias_step_mps2=0.20,
            rotation_owner="SEALED_NATIVE200_ORIENTATION",
            rotation_action_id="04_shoulder_left",
            minimum_inlier_epochs=5,
            maximum_bias_window_epochs=8,
            maximum_candidate_subsets=56,
        ),
    )


def _rotation_observation(
    measurement: float,
    availability: float,
    sequence: int,
    rotation: np.ndarray,
    *,
    source_measurement: float | None = None,
    source_frame: int | None = None,
    source_span: int = 0,
    action_id: str = "04_shoulder_left",
    next_source_measurement: float | None = None,
    next_source_span: int | None = None,
    owner: str = "SEALED_NATIVE200_ORIENTATION",
    tag_id: str = "BODY_CONSENSUS",
) -> ConsensusRotationObservation:
    return ConsensusRotationObservation.from_owner(
        association_measurement_time_s=measurement,
        source_measurement_time_s=(
            measurement if source_measurement is None else source_measurement
        ),
        availability_time_s=availability,
        association_sequence=sequence,
        action_id=action_id,
        source_frame=sequence if source_frame is None else source_frame,
        source_span=source_span,
        next_source_measurement_time_s=(
            measurement + 0.005
            if next_source_measurement is None else next_source_measurement
        ),
        next_source_frame=(
            sequence + 1 if source_frame is None else source_frame + 1
        ),
        next_source_span=(
            source_span if next_source_span is None else next_source_span
        ),
        tag_id=tag_id,
        anchors=tuple(range(8)),
        rotation_owner=owner,
        rotation_world_from_sensor=rotation,
    )


def _observation(
    epoch: float,
    sequence: int,
    *,
    availability: float | None = None,
    tag_id: str = "BODY_CONSENSUS",
) -> PositionObservation:
    return PositionObservation(
        epoch,
        epoch if availability is None else availability,
        np.zeros(3),
        np.eye(3) * 0.01,
        tag_id,
        tuple(range(8)),
        "ROBUST_TEN_NODE_CONSENSUS",
        True,
        True,
        sequence,
    )


def test_delayed_absolute_position_update_leaves_velocity_and_bias_mean_exact() -> None:
    vector = np.array([0.0, 0.0, 0.0, 0.3, -0.2, 0.1, 0.02, -0.01, 0.03])
    root = CausalDelayedRootFilter(_state(1.0, vector), RootFilterConfig(), inertial=True)
    before = root.current_state.vector.copy()
    decision = root.add_position(
        PositionObservation(
            1.0, 1.0, np.array([0.04, 0.0, 0.0]),
            np.eye(3) * 0.01, "BODY_CONSENSUS", tuple(range(8)),
            "ROBUST_TEN_NODE_CONSENSUS", True, True, 1,
        ),
        processing_time_s=1.0,
        state_update_indices=(0, 1, 2),
    )
    assert decision.accepted
    assert not np.array_equal(root.current_state.vector[:3], before[:3])
    assert root.current_state.vector[3:9].tobytes() == before[3:9].tobytes()
    assert decision.availability_applied_velocity_delta_mps.tobytes() == np.zeros(3).tobytes()


def test_committed_delayed_state_accessor_returns_post_update_epoch_without_future_mutation() -> None:
    root = CausalDelayedRootFilter(_state(0.0), RootFilterConfig(), inertial=True)
    for sequence, time_s in enumerate(np.arange(0.005, 0.161, 0.005), start=1):
        assert root.add_imu(ImuSample(
            float(time_s), float(time_s), np.array([0.0, 0.0, 9.80665]),
            np.eye(3), sequence,
        ))
    before = root.committed_state_at(0.10)
    decision = root.add_position(
        PositionObservation(
            0.10, 0.16, np.array([0.04, 0.0, 0.0]),
            np.eye(3) * 0.01, "BODY_CONSENSUS", tuple(range(8)),
            "ROBUST_TEN_NODE_CONSENSUS", True, True, 1,
        ),
        processing_time_s=0.16,
        state_update_indices=(0, 1, 2),
    )
    assert decision.accepted
    after = root.committed_state_at(0.10)
    assert after.publication_revision > before.publication_revision
    assert after.query_time_s == 0.10
    assert after.state.position_m.tobytes() != before.state.position_m.tobytes()
    assert after.state.vector[3:9].tobytes() == before.state.vector[3:9].tobytes()
    with pytest.raises(ValueError):
        after.state.vector[0] = 99.0
    frozen = pickle.dumps(after.state, protocol=5)
    assert root.add_imu(ImuSample(
        0.165, 0.165, np.array([0.0, 0.0, 9.80665]), np.eye(3), 99,
    ))
    assert pickle.dumps(root.committed_state_at(0.10).state, protocol=5) == frozen


def test_high_epoch_delayed_position_only_discriminates_replay_roundoff_from_mean_coupling() -> None:
    origin_s = 235_088.0
    vector = np.array([
        1.8, 0.7, 1.2, -11.5, -2.4, -1.2, 0.006, 0.004, -0.016,
    ])
    covariance = np.diag([180_000.0] * 3 + [1_000.0] * 3 + [0.1] * 3)
    root = CausalDelayedRootFilter(
        RootState(origin_s, vector, covariance),
        RootFilterConfig(nis_limit_3d=100.0, maximum_position_influence_m=0.05),
        inertial=True,
    )
    for sequence in range(1, 33):
        time_s = origin_s + sequence * 0.005
        angle = 0.001 * sequence
        cosine, sine = np.cos(angle), np.sin(angle)
        rotation = np.array([
            [cosine, -sine, 0.0],
            [sine, cosine, 0.0],
            [0.0, 0.0, 1.0],
        ])
        force = np.array([
            -0.313 + 0.02 * np.sin(sequence),
            -0.081 + 0.01 * np.cos(sequence),
            9.80665 - 0.035,
        ])
        assert root.add_imu(ImuSample(
            time_s, time_s, force, rotation, sequence,
        ))
    measurement_s = origin_s + 0.1023
    availability_s = measurement_s + 0.0597
    root.advance_to_availability(availability_s)
    before = root.current_state
    decision = root.add_position(
        PositionObservation(
            measurement_s, availability_s, np.array([1.9, 0.8, 1.2]),
            np.eye(3) * 0.02, "BODY_CONSENSUS", tuple(range(8)),
            "ROBUST_TEN_NODE_CONSENSUS", True, True, 1,
        ),
        processing_time_s=availability_s,
        state_update_indices=(0, 1, 2),
    )
    after = root.current_state
    actual_velocity_delta = after.velocity_mps - before.velocity_mps
    actual_bias_delta = (
        after.accelerometer_bias_mps2 - before.accelerometer_bias_mps2
    )
    baseline_replay_velocity_delta = (
        actual_velocity_delta - decision.availability_applied_velocity_delta_mps
    )
    assert decision.accepted
    assert after.position_m.tobytes() != before.position_m.tobytes()
    assert after.covariance.tobytes() != before.covariance.tobytes()
    np.testing.assert_array_equal(
        decision.availability_applied_velocity_delta_mps, np.zeros(3),
    )
    np.testing.assert_array_equal(actual_bias_delta, np.zeros(3))
    np.testing.assert_array_equal(actual_velocity_delta, np.zeros(3))
    np.testing.assert_array_equal(baseline_replay_velocity_delta, np.zeros(3))
    assert after.vector[3:9].tobytes() == before.vector[3:9].tobytes()
    canonical = after
    sample = ImuSample(
        availability_s + 0.005,
        availability_s + 0.005,
        np.array([-0.29, -0.07, 9.78]),
        np.eye(3),
        99,
    )
    expected, _ = propagate_inertial(
        canonical,
        sample.measurement_time_s,
        sample.specific_force_sensor_mps2,
        sample.rotation_world_from_sensor,
        root.config,
    )
    assert root.add_imu(sample)
    np.testing.assert_array_equal(root.current_state.vector, expected.vector)


def test_explicit_all_nine_position_update_matches_legacy_default_exactly() -> None:
    initial = _state(1.0, np.array([
        0.1, 0.2, 0.3, 0.4, -0.2, 0.1, 0.01, -0.02, 0.03,
    ]))
    observation = PositionObservation(
        1.0, 1.0, np.array([0.13, 0.18, 0.31]), np.eye(3) * 0.01,
        "BODY_CONSENSUS", tuple(range(8)),
        "ROBUST_TEN_NODE_CONSENSUS", True, True, 1,
    )
    legacy = CausalDelayedRootFilter(initial, RootFilterConfig(), inertial=True)
    explicit = CausalDelayedRootFilter(
        RootState(1.0, initial.vector.copy(), initial.covariance.copy()),
        RootFilterConfig(),
        inertial=True,
    )
    legacy_decision = legacy.add_position(observation, processing_time_s=1.0)
    explicit_decision = explicit.add_position(
        observation,
        processing_time_s=1.0,
        state_update_indices=tuple(range(9)),
    )
    assert pickle.dumps(legacy_decision, protocol=5) == pickle.dumps(
        explicit_decision, protocol=5,
    )
    assert legacy.current_state.vector.tobytes() == explicit.current_state.vector.tobytes()
    assert (
        legacy.current_state.covariance.tobytes()
        == explicit.current_state.covariance.tobytes()
    )


def test_delayed_absolute_ledger_creates_no_false_velocity_or_preavailability_output() -> None:
    vector = np.zeros(9)
    vector[0] = 0.20
    root = CausalDelayedRootFilter(
        _state(0.0, vector), RootFilterConfig(nis_limit_3d=100.0), inertial=True,
    )
    corrector = FixedLagConsensusDriftCorrector(_consensus_config())
    cumulative = np.zeros(3)
    first_availability = 0.06
    publication_times = []
    accepted_velocity_deltas = []
    for sequence, measurement in enumerate(np.arange(0.0, 2.41, 0.12)):
        availability = float(measurement + 0.06)
        root.advance_to_availability(availability)
        observation = _observation(
            float(measurement), sequence, availability=availability,
        )
        decision = root.add_position(
            observation,
            processing_time_s=availability,
            state_update_indices=(0, 1, 2),
        )
        assert decision.accepted
        cumulative += decision.applied_position_delta_m
        measurement_state = root.committed_state_at(float(measurement))
        before = root.current_state
        candidate, drift_decision = corrector.observe(
            before,
            observation=observation,
            post_absolute_position_at_measurement_m=(
                measurement_state.state.position_m
            ),
            cumulative_absolute_position_correction_m=cumulative,
            trusted_node_count=10,
            total_body_nodes=10,
        )
        if drift_decision.accepted:
            accepted_velocity_deltas.append(drift_decision.velocity_delta_mps)
            root.apply_current_constraint(
                candidate,
                operator=AdditiveRootConstraint(candidate.vector - before.vector),
                owner="TEST_DELAYED_LEDGER_VELOCITY",
            )
        if availability >= first_availability:
            publication_times.append(availability)
    assert publication_times and min(publication_times) >= first_availability
    assert accepted_velocity_deltas
    np.testing.assert_allclose(
        np.stack(accepted_velocity_deltas), 0.0, rtol=0.0, atol=1e-13,
    )


def test_consensus_velocity_channel_is_bounded_bias_inert_and_gap_reacquires() -> None:
    corrector = FixedLagConsensusDriftCorrector(_consensus_config())
    state = _state(0.0)
    cumulative = np.zeros(3)
    accepted = []
    for sequence, epoch in enumerate(np.arange(0.0, 2.41, 0.12)):
        vector = state.vector.copy()
        vector[:3] = np.array([0.15 * epoch, 0.0, 0.0])
        state = RootState(float(epoch), vector, state.covariance.copy())
        before_position = state.position_m.copy()
        before_bias = state.accelerometer_bias_mps2.copy()
        state, decision = corrector.observe(
            state,
            observation=_observation(float(epoch), sequence),
            post_absolute_position_at_measurement_m=state.position_m,
            cumulative_absolute_position_correction_m=cumulative,
            trusted_node_count=10,
            total_body_nodes=10,
        )
        np.testing.assert_array_equal(state.position_m, before_position)
        np.testing.assert_array_equal(state.accelerometer_bias_mps2, before_bias)
        if decision.accepted:
            accepted.append(decision)
            assert np.linalg.norm(decision.velocity_delta_mps) <= 0.50 + 1e-12
    assert accepted
    corrector.clear_derivative_history()
    next_time = float(state.time_s + 0.12)
    state = RootState(next_time, state.vector.copy(), state.covariance.copy())
    before = state.vector.tobytes()
    state, decision = corrector.observe(
        state,
        observation=_observation(next_time, sequence + 1),
        post_absolute_position_at_measurement_m=state.position_m,
        cumulative_absolute_position_correction_m=cumulative,
        trusted_node_count=10,
        total_body_nodes=10,
    )
    assert not decision.accepted and decision.reason == "FIXED_LAG_WARMUP"
    assert state.vector.tobytes() == before


def test_consensus_velocity_commit_composes_with_authoritative_root_filter() -> None:
    initial_vector = np.zeros(9)
    initial_vector[3] = 0.15
    root = CausalDelayedRootFilter(
        _state(0.0, initial_vector), RootFilterConfig(), inertial=True,
    )
    corrector = FixedLagConsensusDriftCorrector(_consensus_config())
    committed = False
    for sequence, epoch in enumerate(np.arange(0.0, 2.41, 0.12)):
        availability = float(epoch + 0.06)
        root.advance_to_availability(availability)
        before = root.current_state
        measurement = root.committed_state_at(float(epoch))
        candidate, decision = corrector.observe(
            before,
            observation=_observation(
                float(epoch), sequence, availability=availability,
            ),
            post_absolute_position_at_measurement_m=measurement.state.position_m,
            cumulative_absolute_position_correction_m=np.zeros(3),
            trusted_node_count=10,
            total_body_nodes=10,
        )
        if not decision.accepted:
            continue
        np.testing.assert_array_equal(candidate.position_m, before.position_m)
        np.testing.assert_array_equal(
            candidate.accelerometer_bias_mps2,
            before.accelerometer_bias_mps2,
        )
        assert candidate.covariance.tobytes() == before.covariance.tobytes()
        velocity_delta = candidate.velocity_mps - before.velocity_mps
        assert 0.0 < np.linalg.norm(velocity_delta) <= 0.50 + 1e-12
        root.apply_current_constraint(
            candidate,
            operator=AdditiveRootConstraint(candidate.vector - before.vector),
            owner="TEST_FIXED_LAG_BODY_CONSENSUS_VELOCITY",
        )
        assert root.current_state.position_m.tobytes() == before.position_m.tobytes()
        assert (
            root.current_state.accelerometer_bias_mps2.tobytes()
            == before.accelerometer_bias_mps2.tobytes()
        )
        assert root.current_state.covariance.tobytes() == before.covariance.tobytes()
        np.testing.assert_array_equal(root.current_state.velocity_mps, candidate.velocity_mps)
        committed = True
        break
    assert committed


def test_consensus_multi_offset_fit_recovers_velocity_and_bias_causally() -> None:
    """Changing rotations, jitter, partial trust, and one outlier remain causal."""
    root = CausalDelayedRootFilter(_state(0.0), RootFilterConfig(), inertial=True)
    corrector = FixedLagConsensusDriftCorrector(_consensus_bias_config())
    measurement_times = np.asarray([
        0.000, 0.117, 0.241, 0.365, 0.486, 0.611, 0.733, 0.852,
        0.972, 1.095,
    ])
    availability_delays = np.asarray([
        0.055, 0.061, 0.058, 0.064, 0.057, 0.063, 0.059, 0.065,
        0.056, 0.062,
    ])
    expected_velocity_delta = np.array([0.18, -0.09, 0.045])
    expected_bias_delta = np.array([0.075, -0.038, 0.026])
    intercept = np.array([0.03, -0.01, 0.02])
    bias_position_jacobian = np.zeros((3, 3))
    bias_velocity_jacobian = np.zeros((3, 3))
    previous_rotation = None
    previous_time = None
    accepted = None
    trusted_counts = [4, 7, 5, 10, 6, 8, 4, 9, 5, 10]
    for sequence, (measurement, delay, trusted) in enumerate(zip(
        measurement_times, availability_delays, trusted_counts, strict=True,
    )):
        angle = 0.31 * measurement
        pitch = 0.19 * measurement
        cz, sz = np.cos(angle), np.sin(angle)
        cy, sy = np.cos(pitch), np.sin(pitch)
        rotation = np.array([
            [cz * cy, -sz, cz * sy],
            [sz * cy, cz, sz * sy],
            [-sy, 0.0, cy],
        ])
        if previous_time is not None:
            dt = float(measurement - previous_time)
            bias_position_jacobian += (
                bias_velocity_jacobian * dt
                - 0.5 * previous_rotation * dt * dt
            )
            bias_velocity_jacobian += -previous_rotation * dt
        corrected = (
            intercept
            + measurement * expected_velocity_delta
            + bias_position_jacobian @ expected_bias_delta
        )
        if sequence == 3:
            corrected += np.array([0.055, -0.040, 0.030])
        availability = float(measurement + delay)
        root.advance_to_availability(availability)
        observation = PositionObservation(
            float(measurement), availability, corrected,
            np.eye(3) * 0.0004, "BODY_CONSENSUS", tuple(range(8)),
            "ROBUST_TEN_NODE_CONSENSUS", True, True, sequence,
        )
        before = root.current_state
        candidate, decision = corrector.observe(
            before,
            observation=observation,
            post_absolute_position_at_measurement_m=np.zeros(3),
            cumulative_absolute_position_correction_m=np.zeros(3),
            trusted_node_count=trusted,
            total_body_nodes=10,
            rotation_observation=_rotation_observation(
                float(measurement), availability, sequence, rotation,
            ),
        )
        assert candidate.position_m.tobytes() == before.position_m.tobytes()
        assert candidate.covariance.tobytes() == before.covariance.tobytes()
        if decision.accepted:
            root.apply_current_constraint(
                candidate,
                operator=AdditiveRootConstraint(candidate.vector - before.vector),
                owner="TEST_CONSENSUS_VELOCITY_AND_BIAS",
            )
            accepted = decision
            break
        previous_time = float(measurement)
        previous_rotation = rotation
    assert accepted is not None
    assert accepted.reason == "ACCEPTED_CONSENSUS_VELOCITY_AND_ACCELEROMETER_BIAS"
    assert accepted.rank == 9
    np.testing.assert_allclose(
        accepted.velocity_delta_mps, expected_velocity_delta, atol=0.035,
    )
    np.testing.assert_allclose(
        accepted.accelerometer_bias_delta_mps2, expected_bias_delta, atol=0.025,
    )
    assert np.linalg.norm(accepted.velocity_delta_mps) <= 0.50
    assert np.linalg.norm(accepted.accelerometer_bias_delta_mps2) <= 0.20
    np.testing.assert_array_equal(root.current_state.position_m, np.zeros(3))
    assert root.current_state.covariance.tobytes() == before.covariance.tobytes()


@pytest.mark.parametrize("gate", ["rank", "condition", "residual"])
def test_unqualified_consensus_bias_fit_falls_back_to_velocity_only(gate: str) -> None:
    config = _consensus_bias_config()
    if gate == "rank":
        config = replace(config, rank_relative_tolerance=0.50)
    elif gate == "condition":
        config = replace(
            config,
            acceleration_bias=replace(
                config.acceleration_bias, maximum_scaled_condition=1.01,
            ),
        )
    else:
        config = replace(
            config,
            acceleration_bias=replace(
                config.acceleration_bias,
                maximum_robust_standardized_rms=1e-12,
            ),
        )
    corrector = FixedLagConsensusDriftCorrector(config)
    state = _state(0.0)
    accepted = None
    for sequence, measurement in enumerate(np.arange(0.0, 1.21, 0.12)):
        availability = float(measurement + 0.06)
        vector = state.vector.copy()
        state = RootState(availability, vector, state.covariance.copy())
        state, decision = corrector.observe(
            state,
            observation=PositionObservation(
                float(measurement), availability,
                np.array([
                    0.2 * measurement + (0.001 * (-1) ** sequence if gate == "residual" else 0.0),
                    0.0,
                    0.0,
                ]),
                np.eye(3) * 0.01, "BODY_CONSENSUS", tuple(range(8)),
                "ROBUST_TEN_NODE_CONSENSUS", True, True, sequence,
            ),
            post_absolute_position_at_measurement_m=np.zeros(3),
            cumulative_absolute_position_correction_m=np.zeros(3),
            trusted_node_count=6,
            total_body_nodes=10,
            rotation_observation=_rotation_observation(
                float(measurement), availability, sequence, np.eye(3),
            ),
        )
        if decision.accepted:
            accepted = decision
            break
    assert accepted is not None
    assert accepted.reason == "ACCEPTED_CONSENSUS_VELOCITY_ONLY"
    assert np.linalg.norm(accepted.velocity_delta_mps) > 0.0
    np.testing.assert_array_equal(
        accepted.accelerometer_bias_delta_mps2, np.zeros(3),
    )


@pytest.mark.parametrize("kind", [
    "missing", "future", "stale", "wrong_owner", "wrong_association",
    "foreign_action", "stale_tick", "cross_span", "bad_digest",
    "bad_rotation",
])
def test_invalid_consensus_bias_rotation_is_exact_no_op(kind: str) -> None:
    corrector = FixedLagConsensusDriftCorrector(_consensus_bias_config())
    state = _state(0.30)
    first = _observation(0.12, 1, availability=0.30)
    _, warmup = corrector.observe(
        state,
        observation=first,
        post_absolute_position_at_measurement_m=state.position_m,
        cumulative_absolute_position_correction_m=np.zeros(3),
        trusted_node_count=6,
        total_body_nodes=10,
        rotation_observation=_rotation_observation(0.12, 0.30, 1, np.eye(3)),
    )
    assert warmup.reason == "FIXED_LAG_WARMUP"
    candidate = RootState(0.36, state.vector.copy(), state.covariance.copy())
    observation = _observation(0.24, 2, availability=0.36)
    rotation = _rotation_observation(0.24, 0.36, 2, np.eye(3))
    if kind == "missing":
        rotation = None
    elif kind == "future":
        rotation = _rotation_observation(0.24, 0.37, 2, np.eye(3))
    elif kind == "stale":
        rotation = _rotation_observation(0.24, 0.30, 2, np.eye(3))
    elif kind == "wrong_owner":
        rotation = _rotation_observation(
            0.24, 0.36, 2, np.eye(3), owner="FOREIGN_ORIENTATION",
        )
    elif kind == "wrong_association":
        rotation = _rotation_observation(0.24, 0.36, 3, np.eye(3))
    elif kind == "foreign_action":
        rotation = _rotation_observation(
            0.24, 0.36, 2, np.eye(3), action_id="05_shoulder_right",
        )
    elif kind == "stale_tick":
        rotation = _rotation_observation(
            0.24, 0.36, 2, np.eye(3), source_measurement=0.23,
            next_source_measurement=0.235,
        )
    elif kind == "cross_span":
        rotation = _rotation_observation(
            0.24, 0.36, 2, np.eye(3), source_span=0,
            next_source_span=1,
        )
    elif kind == "bad_digest":
        rotation = replace(rotation, canonical_digest="0" * 64)
    elif kind == "bad_rotation":
        rotation = _rotation_observation(
            0.24, 0.36, 2, np.diag([1.0, 1.0, 2.0]),
        )
    state_before = pickle.dumps(candidate, protocol=5)
    corrector_before = pickle.dumps(corrector.__dict__, protocol=5)
    returned, decision = corrector.observe(
        candidate,
        observation=observation,
        post_absolute_position_at_measurement_m=candidate.position_m,
        cumulative_absolute_position_correction_m=np.zeros(3),
        trusted_node_count=6,
        total_body_nodes=10,
        rotation_observation=rotation,
    )
    assert not decision.accepted
    assert pickle.dumps(returned, protocol=5) == state_before
    assert pickle.dumps(corrector.__dict__, protocol=5) == corrector_before


@pytest.mark.parametrize(
    ("observation_factory", "reason"),
    [
        (
            lambda: PositionObservation(
                0.24, 0.24, np.zeros(3), np.eye(3) * 0.01,
                "OTHER_CONSENSUS", tuple(range(8)),
                "ROBUST_TEN_NODE_CONSENSUS", True, True, 2,
            ),
            "SOURCE_IDENTITY_MISMATCH",
        ),
        (lambda: _observation(0.12, 1), "STALE_OR_REPLAYED_MEASUREMENT"),
        (lambda: _observation(0.24, 1), "STALE_OR_REPLAYED_SOURCE_SEQUENCE"),
        (
            lambda: PositionObservation(
                0.24, 0.25, np.zeros(3), np.eye(3) * 0.01,
                "BODY_CONSENSUS", tuple(range(8)),
                "ROBUST_TEN_NODE_CONSENSUS", True, True, 2,
            ),
            "STATE_NOT_AT_OBSERVATION_AVAILABILITY",
        ),
    ],
)
def test_invalid_consensus_identity_or_chronology_is_exact_no_op(
    observation_factory, reason: str,
) -> None:
    corrector = FixedLagConsensusDriftCorrector(_consensus_config())
    state = _state(0.12)
    state, warmup = corrector.observe(
        state,
        observation=_observation(0.12, 1),
        post_absolute_position_at_measurement_m=state.position_m,
        cumulative_absolute_position_correction_m=np.zeros(3),
        trusted_node_count=10,
        total_body_nodes=10,
    )
    assert warmup.reason == "FIXED_LAG_WARMUP"
    candidate_state = RootState(0.24, state.vector.copy(), state.covariance.copy())
    state_before = pickle.dumps(candidate_state, protocol=5)
    corrector_before = pickle.dumps(corrector.__dict__, protocol=5)
    returned, decision = corrector.observe(
        candidate_state,
        observation=observation_factory(),
        post_absolute_position_at_measurement_m=candidate_state.position_m,
        cumulative_absolute_position_correction_m=np.zeros(3),
        trusted_node_count=10,
        total_body_nodes=10,
    )
    assert not decision.accepted and decision.reason == reason
    assert pickle.dumps(returned, protocol=5) == state_before
    assert pickle.dumps(corrector.__dict__, protocol=5) == corrector_before


def test_absolute_position_ledger_does_not_create_false_velocity() -> None:
    corrector = FixedLagConsensusDriftCorrector(_consensus_config())
    state = _state(0.0)
    cumulative = np.zeros(3)
    deltas = []
    for index, epoch in enumerate(np.arange(0.0, 2.41, 0.12)):
        cumulative += np.array([0.004, -0.002, 0.001])
        vector = state.vector.copy()
        vector[:3] = cumulative
        state = RootState(float(epoch), vector, state.covariance.copy())
        state, decision = corrector.observe(
            state,
            observation=_observation(float(epoch), index),
            post_absolute_position_at_measurement_m=state.position_m,
            cumulative_absolute_position_correction_m=cumulative,
            trusted_node_count=10,
            total_body_nodes=10,
        )
        if decision.accepted:
            deltas.append(decision.velocity_delta_mps)
    assert deltas
    np.testing.assert_allclose(np.stack(deltas), 0.0, rtol=0.0, atol=1e-14)


def test_35s_observed_acceleration_error_is_bounded_without_native200_teleport() -> None:
    root_config = RootFilterConfig()
    drift = FixedLagConsensusDriftCorrector(_consensus_config())
    state = _state(0.0)
    uncontrolled = _state(0.0)
    slew = CausalRootCorrectionSlew(
        release_period_s=0.12,
        maximum_correction_m=root_config.maximum_position_influence_m,
    )
    slew.sample(0.0, state.position_m, state.velocity_mps)
    cumulative_absolute = np.zeros(3)
    published = [state.position_m.copy()]
    speed = [float(np.linalg.norm(state.velocity_mps))]
    accepted_drift = 0
    for index in range(1, 7001):
        time_s = index * 0.005
        force = np.array([0.313, 0.0, 9.80665])
        state, _ = propagate_inertial(state, time_s, force, np.eye(3), root_config)
        uncontrolled, _ = propagate_inertial(
            uncontrolled, time_s, force, np.eye(3), root_config,
        )
        if index % 24 == 0:
            prediction = state
            state, decision = update_position(
                state,
                PositionObservation(
                    time_s, time_s, np.zeros(3), np.eye(3) * 0.01,
                    "BODY_CONSENSUS", tuple(range(8)),
                    "ROBUST_TEN_NODE_CONSENSUS", True, True, index,
                ),
                root_config,
                state_update_indices=(0, 1, 2),
            )
            assert decision.accepted
            absolute_delta = state.position_m - prediction.position_m
            cumulative_absolute += absolute_delta
            slew.install(time_s, absolute_delta)
            state, drift_decision = drift.observe(
                state,
                observation=_observation(time_s, index),
                post_absolute_position_at_measurement_m=state.position_m,
                cumulative_absolute_position_correction_m=cumulative_absolute,
                trusted_node_count=10,
                total_body_nodes=10,
            )
            accepted_drift += int(drift_decision.accepted)
        publication = slew.sample(time_s, state.position_m, state.velocity_mps)
        published.append(publication.position_m.copy())
        speed.append(float(np.linalg.norm(state.velocity_mps)))
    published = np.asarray(published)
    steps = np.linalg.norm(np.diff(published, axis=0), axis=1)
    assert accepted_drift > 0
    displacement = np.linalg.norm(published, axis=1)
    assert displacement[-1] <= 0.50
    assert np.max(displacement) <= 0.50
    assert np.linalg.norm(state.velocity_mps) <= 0.50
    assert max(speed) <= 0.50
    assert np.max(steps) <= 0.005 + 1e-12
    np.testing.assert_array_equal(state.accelerometer_bias_mps2, np.zeros(3))
    assert np.linalg.norm(uncontrolled.position_m) > 100.0
