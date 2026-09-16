from __future__ import annotations

import numpy as np
from dataclasses import replace

from biospur_fusion.c2_coupled_progressive.continuous_frontend import CAPTURE2_PROTOCOL_SLOTS
from biospur_fusion.c2_uwb_root_world.continuous_root_ab import (
    ContinuousRootAB,
    DiagnosticBootstrap,
    PelvisContinuousVQF,
    admit_uwb_timestamp,
    bootstrap_action00_root,
    hold_mean_no_measurement_gap,
    raw_range_reference_ns,
)
from biospur_fusion.c2_uwb_root_world.tight_range import RawRangeDecision
from biospur_fusion.c2_uwb_root_world.u0 import ClockModel, UwbRow
from biospur_fusion.root_r3.estimator import RootFilterConfig, propagate_inertial
from biospur_fusion.root_r3.models import RootState


ANCHORS = np.array([
    [0, 0, 0], [4, 0, 0], [4, 3, 0], [0, 3, 0],
    [0, 0, 2], [4, 0, 2], [4, 3, 2], [0, 3, 2],
], dtype=float)
CLOCK = ClockModel(0, 1000.0, 0.0, 10.0)


def row_for(position, *, strobe_us=1_000_000, sequence=1):
    ranges = tuple(int(round(np.linalg.norm(position - anchor) * 1000)) for anchor in ANCHORS)
    return UwbRow(
        "BSFC2CC", 0, sequence, sequence, strobe_us, strobe_us,
        tuple(range(8)), ranges, (1000,) * 8, (100,) * 8, 0xFF,
    )


def canonical_time_ns(row, clock=CLOCK):
    return raw_range_reference_ns(row, clock)[1]


def bootstrap_for(position, clock=CLOCK):
    row = row_for(position)
    return bootstrap_action00_root(
        row, measurement_time_ns=canonical_time_ns(row, clock),
        anchors_m=ANCHORS, clock=clock,
    )


def accepted_solver_decision(epoch_s):
    zeros = np.zeros(8)
    sigma = np.full(8, 0.12)
    return RawRangeDecision(
        True, "ACCEPTED", tuple(range(8)), np.full(8, epoch_s), epoch_s,
        np.ones(8), np.ones(8), zeros, zeros, np.ones(8), sigma,
        3, 2.0, 1, "FOCUSED_NO_RAW_TEST", sigma,
    )


def rejected_solver_decision(epoch_s):
    decision = accepted_solver_decision(epoch_s)
    return replace(decision, accepted=False, reason="SOLVER_OR_GEOMETRY_REJECT")


def test_protocol_inventory_keeps_skipped_marker():
    assert len(CAPTURE2_PROTOCOL_SLOTS) == 20
    assert sum(row.acquired for row in CAPTURE2_PROTOCOL_SLOTS) == 19
    assert CAPTURE2_PROTOCOL_SLOTS[1].action_id == "01_neutral_sway"
    assert not CAPTURE2_PROTOCOL_SLOTS[1].acquired


def test_prior_free_bootstrap_and_identical_ab_state():
    position = np.array([1.2, 1.1, 0.9])
    bootstrap = bootstrap_for(position)
    assert np.linalg.norm(bootstrap.state.position_m - position) < 0.003
    assert "NOT_SCIENTIFIC_R" in bootstrap.provenance
    ab = ContinuousRootAB(bootstrap)
    assert ab.a.vector.tobytes() == ab.b.vector.tobytes()
    assert ab.a.covariance.tobytes() == ab.b.covariance.tobytes()
    before = ab.a.vector.tobytes()
    ab.label_boundary(1, "01_neutral_sway")
    assert ab.a.vector.tobytes() == before


def test_true_gap_holds_mean_and_grows_covariance():
    bootstrap = bootstrap_for(np.array([1.2, 1.1, 0.9]))
    moving = bootstrap.state.vector.copy()
    moving[3:6] = [1.0, -2.0, 0.5]
    state = type(bootstrap.state)(bootstrap.state.time_s, moving, bootstrap.state.covariance)
    advanced = hold_mean_no_measurement_gap(state, state.time_s + 0.040068, RootFilterConfig())
    assert advanced.vector.tobytes() == state.vector.tobytes()
    assert np.trace(advanced.covariance) > np.trace(state.covariance)


def test_a_never_commits_uwb_and_gap_is_reported():
    position = np.array([1.2, 1.1, 0.9])
    bootstrap = bootstrap_for(position)
    ab = ContinuousRootAB(bootstrap)
    t0 = bootstrap.state.time_s
    force = np.array([0.0, 0.0, 9.80665])
    ab.add_imu(time_s=t0 + 0.005, force_sensor_mps2=force, rotation_world_from_sensor=np.eye(3))
    ab.add_imu(time_s=t0 + 0.045068, force_sensor_mps2=force, rotation_world_from_sensor=np.eye(3))
    metrics = ab.metrics()
    assert metrics.a_uwb_commits == 0
    assert metrics.missing_imu_intervals == 7
    assert np.isclose(metrics.maximum_imu_gap_s, 0.040068)


def test_one_vqf_survives_labels_and_reports_real_gap(monkeypatch):
    class Block:
        def __init__(self, sample_period_s):
            assert sample_period_s == 0.005

        def step(self, gyro, acceleration, magnetometer):
            assert magnetometer is None
            return np.array([np.sqrt(0.5), 0.0, np.sqrt(0.5), 0.0])

    monkeypatch.setattr(
        "biospur_fusion.c2_uwb_root_world.continuous_root_ab.qmt.OriEstVQFBlock", Block,
    )
    owner = PelvisContinuousVQF()
    for index in range(100):
        assert owner.step(boot=0, timer_us=index * 5000, acc_raw=(0, 0, 2048),
                          gyro_raw=(0, 0, 0), preparation=True) is None
    result = owner.step(boot=0, timer_us=500000, acc_raw=(0, 0, 2048), gyro_raw=(0, 0, 0))
    assert result is not None
    owner.step(boot=0, timer_us=540068, acc_raw=(0, 0, 2048), gyro_raw=(0, 0, 0))
    assert owner.samples == 102
    assert owner.gaps == [(500000, 540068)]


def test_uwb_raw_median_plus_minus_point_four_ns_uses_canonical_time():
    position = np.array([1.2, 1.1, 0.9])
    for base_ns in (0.0, 234_000_000_000_000.0):
      for residual_ns in (-0.4, 0.4):
        clock = ClockModel(0, 1000.0, base_ns + residual_ns, 10.0)
        row = row_for(position)
        _raw_reference_ns, canonical_ns = raw_range_reference_ns(row, clock)
        initial = RootState(
            canonical_ns * 1e-9 - 0.005,
            np.r_[position, np.zeros(6)], np.eye(9) * 0.1,
        )
        ab = ContinuousRootAB(DiagnosticBootstrap(
            initial, tuple(range(8)), 1.0, 0.0,
        ))
        decision = ab.add_uwb(
            row, measurement_time_ns=canonical_ns,
            anchors_m=ANCHORS, clock=clock,
        )
        assert decision is not None
        assert ab.b.time_s == canonical_ns * 1e-9
        # The next canonical nanosecond and the next native-200 tick must both
        # be forward for branch B even when the raw float median rounded down.
        force = np.array([0.0, 0.0, 9.80665])
        ab.add_imu(
            time_s=(canonical_ns + 1) * 1e-9,
            force_sensor_mps2=force, rotation_world_from_sensor=np.eye(3),
        )


def test_bootstrap_uses_canonical_epoch_and_fails_closed_outside_envelope():
    position = np.array([1.2, 1.1, 0.9])
    for base_ns in (0.0, 234_000_000_000_000.0):
      for residual_ns in (-0.4, 0.4, 0.500001, 0.75, -0.75):
        clock = ClockModel(0, 1000.0, base_ns + residual_ns, 10.0)
        row = row_for(position)
        canonical_ns = canonical_time_ns(row, clock)
        bootstrap = bootstrap_action00_root(
            row, measurement_time_ns=canonical_ns,
            anchors_m=ANCHORS, clock=clock,
        )
        assert bootstrap.state.time_s == canonical_ns * 1e-9
        ab = ContinuousRootAB(bootstrap)
        assert ab.a.vector.tobytes() == ab.b.vector.tobytes()
        assert ab.a.covariance.tobytes() == ab.b.covariance.tobytes()
        force = np.array([0.0, 0.0, 9.80665])
        ab.add_imu(
            time_s=(canonical_ns + 1) * 1e-9,
            force_sensor_mps2=force, rotation_world_from_sensor=np.eye(3),
        )
        ab.add_imu(
            time_s=(canonical_ns + 5_000_001) * 1e-9,
            force_sensor_mps2=force, rotation_world_from_sensor=np.eye(3),
        )

        import pytest
        with pytest.raises(ValueError, match="integer-nanosecond"):
            bootstrap_action00_root(
                row, measurement_time_ns=float(canonical_ns),
                anchors_m=ANCHORS, clock=clock,
            )
        with pytest.raises(ValueError, match="rounded raw median"):
            bootstrap_action00_root(
                row, measurement_time_ns=canonical_ns + 1,
                anchors_m=ANCHORS, clock=clock,
            )


def test_helper_preserves_solver_float_link_dt(monkeypatch):
    import biospur_fusion.c2_uwb_root_world.continuous_root_ab as module

    position = np.array([1.2, 1.1, 0.9])
    clock = ClockModel(0, 1000.000123, 234_000_000_000_000.4, 10.0)
    row = row_for(position)
    expected_epochs = np.asarray([
        clock.seconds(row.strobe_us + 0.5 * row.t_round_us[slot])
        for slot in range(8)
    ])
    expected_dt = expected_epochs - float(np.median(expected_epochs))
    original = module.solve_shared_root
    observed = []

    def spy(links, **kwargs):
        observed.append(np.asarray([link.link_dt_s for link in links]))
        return original(links, **kwargs)

    monkeypatch.setattr(module, "solve_shared_root", spy)
    raw_ns, canonical_ns = raw_range_reference_ns(row, clock)
    bootstrap = bootstrap_action00_root(
        row, measurement_time_ns=canonical_ns, anchors_m=ANCHORS, clock=clock,
    )
    assert abs(raw_ns - canonical_ns) <= 0.500001
    assert bootstrap.state.time_s == canonical_ns * 1e-9
    assert observed and all(np.array_equal(value, expected_dt) for value in observed)


def test_timestamp_admission_splits_0_to_3_from_4_and_8_link_solver_rows(monkeypatch):
    position = np.array([1.2, 1.1, 0.9])
    base = row_for(position)
    reasons = {}
    admissions = []
    for count in (0, 1, 2, 3, 4, 8):
        mask = (1 << count) - 1
        row = replace(
            base, sequence=count + 1, sweep=count + 1,
            strobe_us=base.strobe_us + count * 1000,
            valid_mask=mask,
            t_round_us=tuple(100 + anchor * 9000 for anchor in range(8)),
        )
        admission = admit_uwb_timestamp(row, CLOCK)
        admissions.append((row, admission))
        reasons[admission.reason] = reasons.get(admission.reason, 0) + 1
        assert admission.stream_progress_ns == int(round(
            CLOCK.a_ns_per_us * row.strobe_us + CLOCK.b_ns
        ))
        if count < 4:
            assert not admission.eligible_for_range_solver
            assert admission.dispatch_measurement_ns == admission.stream_progress_ns
        else:
            assert admission.eligible_for_range_solver
            assert admission.dispatch_measurement_ns >= admission.stream_progress_ns
    assert reasons == {
        "NO_VALID_LINK_STROBE_WATERMARK_ONLY": 1,
        "FEWER_THAN_FOUR_VALID_LINKS_STROBE_WATERMARK_ONLY": 3,
        "ELIGIBLE_RANGE_REFERENCE": 2,
    }

    bootstrap = bootstrap_for(position)
    ab = ContinuousRootAB(bootstrap)
    before = (ab.a.vector.tobytes(), ab.b.vector.tobytes(), ab.metrics())
    called = 0
    original = __import__(
        "biospur_fusion.c2_uwb_root_world.continuous_root_ab", fromlist=["update_raw_ranges"],
    ).update_raw_ranges

    def spy(*args, **kwargs):
        nonlocal called
        called += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        "biospur_fusion.c2_uwb_root_world.continuous_root_ab.update_raw_ranges", spy,
    )
    for row, admission in admissions[:4]:
        if admission.eligible_for_range_solver:
            ab.add_uwb(row, measurement_time_ns=admission.dispatch_measurement_ns,
                       anchors_m=ANCHORS, clock=CLOCK)
    assert called == 0
    assert ab.a.vector.tobytes() == before[0]
    assert ab.b.vector.tobytes() == before[1]

    bad_identity = replace(base, anchor_ids=(7, 6, 5, 4, 3, 2, 1, 0))
    bad = admit_uwb_timestamp(bad_identity, CLOCK)
    assert not bad.eligible_for_range_solver
    assert bad.reason == "ANCHOR_IDENTITY_MISMATCH_STROBE_WATERMARK_ONLY"


def test_credible_large_proposal_uses_existing_bounded_full_state_gain(monkeypatch):
    import biospur_fusion.c2_uwb_root_world.continuous_root_ab as module

    position = np.array([1.2, 1.1, 0.9])
    bootstrap = bootstrap_for(position)
    config = replace(RootFilterConfig(), recovery_good_events=1)
    ab = ContinuousRootAB(bootstrap, root_config=config)
    row = row_for(position, strobe_us=1_005_000, sequence=2)
    epoch_ns = canonical_time_ns(row)
    epoch_s = epoch_ns * 1e-9
    proposal_norm_m = 1.8955096843713826

    prepared_ids = []

    def propose_large(prior, *_args, **kwargs):
        prepared_ids.append(id(kwargs["prepared"]))
        gain = kwargs.get("correction_gain", 1.0)
        delta = np.zeros(9)
        delta[[0, 3, 6]] = [proposal_norm_m, 0.40, -0.20]
        vector = prior.vector + gain * delta
        return (
            RootState(prior.time_s, vector, prior.covariance * (1.0 - 0.5 * gain)),
            accepted_solver_decision(prior.time_s),
        )

    monkeypatch.setattr(module, "update_raw_ranges", propose_large)
    decision = ab.add_uwb(
        row, measurement_time_ns=epoch_ns, anchors_m=ANCHORS, clock=CLOCK,
    )

    assert decision.solver_accepted
    assert decision.accepted
    assert decision.reason == "ACCEPTED_COMMITTED_BOUNDED_MEASUREMENT_INFLUENCE"
    assert len(prepared_ids) == 2 and prepared_ids[0] == prepared_ids[1]
    assert np.isclose(np.linalg.norm(decision.solver_position_correction_m), proposal_norm_m)
    assert np.isclose(
        np.linalg.norm(decision.proposed_position_correction_m),
        config.maximum_position_influence_m,
    )
    scale = config.maximum_position_influence_m / proposal_norm_m
    assert np.isclose(ab.b.velocity_mps[0], 0.40 * scale)
    assert np.isclose(ab.b.accelerometer_bias_mps2[0], -0.20 * scale)
    assert ab.metrics().b_uwb_accepted == 1
    assert ab.metrics().b_uwb_rejected == 0


def test_solver_reject_is_exact_no_event_without_partial_commit(monkeypatch):
    import biospur_fusion.c2_uwb_root_world.continuous_root_ab as module

    position = np.array([1.2, 1.1, 0.9])
    bootstrap = bootstrap_for(position)
    config = replace(RootFilterConfig(), recovery_good_events=1)
    ab = ContinuousRootAB(bootstrap, root_config=config)
    row = row_for(position, strobe_us=1_005_000, sequence=2)
    epoch_ns = canonical_time_ns(row)
    epoch_s = epoch_ns * 1e-9
    before_vector = ab.b.vector.tobytes()
    before_covariance = ab.b.covariance.tobytes()
    before_time = ab.b.time_s

    def reject(prior, *_args, **_kwargs):
        return prior, rejected_solver_decision(prior.time_s)

    monkeypatch.setattr(module, "update_raw_ranges", reject)
    decision = ab.add_uwb(
        row, measurement_time_ns=epoch_ns, anchors_m=ANCHORS, clock=CLOCK,
    )

    assert not decision.solver_accepted
    assert not decision.accepted
    assert decision.reason == "REJECT_SOLVER_SOLVER_OR_GEOMETRY_REJECT"
    assert ab.b.time_s == before_time
    assert ab.b.vector.tobytes() == before_vector
    assert ab.b.covariance.tobytes() == before_covariance
    np.testing.assert_array_equal(decision.proposed_position_correction_m, np.zeros(3))


def test_recovery_rejection_has_no_partial_candidate_or_covariance_commit(monkeypatch):
    import biospur_fusion.c2_uwb_root_world.continuous_root_ab as module

    position = np.array([1.2, 1.1, 0.9])
    bootstrap = bootstrap_for(position)
    ab = ContinuousRootAB(bootstrap)
    row = row_for(position, strobe_us=1_400_000, sequence=2)
    epoch_ns = canonical_time_ns(row)
    epoch_s = epoch_ns * 1e-9
    before_vector = ab.b.vector.tobytes()
    before_covariance = ab.b.covariance.tobytes()
    before_time = ab.b.time_s

    def propose_small(prior, *_args, **_kwargs):
        gain = _kwargs.get("correction_gain", 1.0)
        vector = prior.vector.copy()
        vector[0] += gain * 0.01
        return (
            RootState(prior.time_s, vector, prior.covariance * (1.0 - 0.5 * gain)),
            accepted_solver_decision(prior.time_s),
        )

    monkeypatch.setattr(module, "update_raw_ranges", propose_small)
    decision = ab.add_uwb(
        row, measurement_time_ns=epoch_ns, anchors_m=ANCHORS, clock=CLOCK,
    )

    assert decision.reason == "REJECT_PROPOSED_STATE_UWB_RECOVERY_1_OF_5"
    assert not decision.accepted and decision.solver_accepted
    assert ab.b.time_s == before_time
    assert ab.b.vector.tobytes() == before_vector
    assert ab.b.covariance.tobytes() == before_covariance


def test_dropout_requires_five_consecutive_solver_successes_before_commit(monkeypatch):
    import biospur_fusion.c2_uwb_root_world.continuous_root_ab as module

    position = np.array([1.2, 1.1, 0.9])
    bootstrap = bootstrap_for(position)
    ab = ContinuousRootAB(bootstrap)

    def propose_small(prior, *_args, **kwargs):
        gain = kwargs.get("correction_gain", 1.0)
        vector = prior.vector.copy()
        vector[0] += gain * 0.01
        return (
            RootState(prior.time_s, vector, prior.covariance * (1.0 - 0.1 * gain)),
            accepted_solver_decision(prior.time_s),
        )

    monkeypatch.setattr(module, "update_raw_ranges", propose_small)
    decisions = []
    for index, strobe_us in enumerate(
        (1_400_000, 1_520_000, 1_640_000, 1_760_000, 1_880_000), start=2,
    ):
        row = row_for(position, strobe_us=strobe_us, sequence=index)
        decisions.append(ab.add_uwb(
            row, measurement_time_ns=canonical_time_ns(row),
            anchors_m=ANCHORS, clock=CLOCK,
        ))

    assert [decision.accepted for decision in decisions] == [False] * 4 + [True]
    assert [decision.recovery_good_events for decision in decisions] == [1, 2, 3, 4, 5]
    assert [decision.reason for decision in decisions[:4]] == [
        f"REJECT_PROPOSED_STATE_UWB_RECOVERY_{count}_OF_5"
        for count in range(1, 5)
    ]
    assert decisions[-1].reason == "ACCEPTED_COMMITTED_FULL_MEASUREMENT_INFLUENCE"
    assert ab.metrics().b_uwb_rejected == 4
    assert ab.metrics().b_uwb_accepted == 1


def test_solver_rejects_restart_five_good_dropout_recovery(monkeypatch):
    import biospur_fusion.c2_uwb_root_world.continuous_root_ab as module

    position = np.array([1.2, 1.1, 0.9])
    ab = ContinuousRootAB(bootstrap_for(position))
    def propose(prior, row, *_args, **kwargs):
        if row.sequence <= 6:
            return prior, rejected_solver_decision(prior.time_s)
        gain = kwargs.get("correction_gain", 1.0)
        vector = prior.vector.copy()
        vector[0] += gain * 0.01
        return (
            RootState(prior.time_s, vector, prior.covariance * (1.0 - 0.1 * gain)),
            accepted_solver_decision(prior.time_s),
        )

    monkeypatch.setattr(module, "update_raw_ranges", propose)
    decisions = []
    for index in range(10):
        row = row_for(
            position,
            strobe_us=1_400_000 + index * 120_000,
            sequence=index + 2,
        )
        decisions.append(ab.add_uwb(
            row, measurement_time_ns=canonical_time_ns(row),
            anchors_m=ANCHORS, clock=CLOCK,
        ))

    assert [decision.reason for decision in decisions[:5]] == [
        "REJECT_SOLVER_SOLVER_OR_GEOMETRY_REJECT"
    ] * 5
    assert [decision.recovery_good_events for decision in decisions[:5]] == [0] * 5
    assert [decision.accepted for decision in decisions[5:]] == [False] * 4 + [True]
    assert [decision.recovery_good_events for decision in decisions[5:]] == [1, 2, 3, 4, 5]
    assert decisions[-1].reason == "ACCEPTED_COMMITTED_FULL_MEASUREMENT_INFLUENCE"


def test_persistent_credible_offset_is_consumed_by_repeated_bounded_updates(monkeypatch):
    import biospur_fusion.c2_uwb_root_world.continuous_root_ab as module

    position = np.array([1.2, 1.1, 0.9])
    config = replace(RootFilterConfig(), recovery_good_events=1)
    ab = ContinuousRootAB(bootstrap_for(position), root_config=config)
    target = position + np.array([0.13, 0.0, 0.0])

    def propose_target(prior, *_args, **kwargs):
        gain = kwargs.get("correction_gain", 1.0)
        vector = prior.vector + gain * (np.r_[target, np.zeros(6)] - prior.vector)
        return RootState(
            prior.time_s, vector, prior.covariance * (1.0 - 0.2 * gain),
        ), accepted_solver_decision(prior.time_s)

    monkeypatch.setattr(module, "update_raw_ranges", propose_target)
    decisions = []
    for index, strobe_us in enumerate((1_005_000, 1_010_000, 1_015_000), start=2):
        row = row_for(position, strobe_us=strobe_us, sequence=index)
        decisions.append(ab.add_uwb(
            row, measurement_time_ns=canonical_time_ns(row),
            anchors_m=ANCHORS, clock=CLOCK,
        ))

    applied = [np.linalg.norm(row.proposed_position_correction_m) for row in decisions]
    assert np.all(np.asarray(applied) <= 0.05 + 1e-12)
    np.testing.assert_allclose(applied[:2], [0.05, 0.05], atol=1e-12)
    assert 0.0 < applied[2] < applied[1]
    assert all(row.accepted and row.solver_accepted for row in decisions)
    assert np.linalg.norm(ab.b.position_m - target) < 0.001


def test_rejected_event_is_inert_for_current_and_future_imu_state(monkeypatch):
    import biospur_fusion.c2_uwb_root_world.continuous_root_ab as module

    position = np.array([1.2, 1.1, 0.9])
    bootstrap = bootstrap_for(position)
    observed = ContinuousRootAB(bootstrap)
    absent = ContinuousRootAB(bootstrap)
    force = np.array([0.3, -0.2, 9.7])
    rotation = np.eye(3)
    first_imu_time = bootstrap.state.time_s + 0.005
    for branch in (observed, absent):
        branch.add_imu(
            time_s=first_imu_time,
            force_sensor_mps2=force,
            rotation_world_from_sensor=rotation,
        )

    before = (observed.b.time_s, observed.b.vector.tobytes(), observed.b.covariance.tobytes())

    def reject(prior, *_args, **_kwargs):
        return prior, rejected_solver_decision(prior.time_s)

    monkeypatch.setattr(module, "update_raw_ranges", reject)
    row = row_for(position, strobe_us=1_010_000, sequence=2)
    decision = observed.add_uwb(
        row, measurement_time_ns=canonical_time_ns(row), anchors_m=ANCHORS, clock=CLOCK,
    )
    assert not decision.accepted and not decision.solver_accepted
    assert (observed.b.time_s, observed.b.vector.tobytes(), observed.b.covariance.tobytes()) == before

    second_imu_time = first_imu_time + 0.010
    for branch in (observed, absent):
        branch.add_imu(
            time_s=second_imu_time,
            force_sensor_mps2=force,
            rotation_world_from_sensor=rotation,
        )
    assert observed.b.time_s == absent.b.time_s
    assert observed.b.vector.tobytes() == absent.b.vector.tobytes()
    assert observed.b.covariance.tobytes() == absent.b.covariance.tobytes()


def test_repeated_real_ranges_arrest_constant_accelerometer_bias_drift():
    true_position = np.array([1.2, 1.1, 0.9])
    config = replace(RootFilterConfig(), recovery_good_events=1)
    ab = ContinuousRootAB(bootstrap_for(true_position), root_config=config)
    t0 = ab.a.time_s
    measured_force = np.array([0.40, 0.0, 9.80665])
    decisions = []

    for step in range(1, 801):
        imu_time = t0 + step * 0.005
        if step % 24 == 0:
            uwb_time = imu_time - 0.0025
            strobe_us = int(round((uwb_time - 0.0005) * 1e6))
            row = row_for(true_position, strobe_us=strobe_us, sequence=step // 24 + 1)
            decisions.append(ab.add_uwb(
                row, measurement_time_ns=canonical_time_ns(row),
                anchors_m=ANCHORS, clock=CLOCK,
            ))
        ab.add_imu(
            time_s=imu_time,
            force_sensor_mps2=measured_force,
            rotation_world_from_sensor=np.eye(3),
        )

    committed = [decision for decision in decisions if decision is not None and decision.accepted]
    assert committed
    assert max(np.linalg.norm(row.proposed_position_correction_m) for row in committed) <= 0.05 + 1e-12
    assert np.linalg.norm(ab.b.position_m - true_position) < np.linalg.norm(ab.a.position_m - true_position)
    assert np.linalg.norm(ab.b.velocity_mps) < np.linalg.norm(ab.a.velocity_mps)
    assert abs(ab.b.accelerometer_bias_mps2[0] - 0.40) < abs(ab.a.accelerometer_bias_mps2[0] - 0.40)


def test_large_absolute_imu_motion_with_small_uwb_correction_is_accepted():
    position = np.array([1.2, 1.1, 0.9])
    bootstrap = bootstrap_for(position)
    vector = bootstrap.state.vector.copy()
    vector[3:6] = [10.0, 0.0, 0.0]
    moving = DiagnosticBootstrap(
        RootState(bootstrap.state.time_s, vector, bootstrap.state.covariance.copy()),
        bootstrap.anchors_used,
        bootstrap.condition,
        bootstrap.residual_rms_m,
    )
    config = replace(RootFilterConfig(), recovery_good_events=1)
    ab = ContinuousRootAB(moving, root_config=config)
    target_strobe_us = 1_100_000
    target_time_s = CLOCK.seconds(target_strobe_us + 500)
    expected_imu_position = position + np.array([1.0, 0.0, 0.0])
    row = row_for(
        expected_imu_position + np.array([0.01, 0.0, 0.0]),
        strobe_us=target_strobe_us,
        sequence=2,
    )

    decision = ab.add_uwb(
        row, measurement_time_ns=canonical_time_ns(row),
        anchors_m=ANCHORS, clock=CLOCK,
    )

    assert decision.accepted
    assert decision.solver_accepted
    assert decision.reason == "ACCEPTED_COMMITTED_FULL_MEASUREMENT_INFLUENCE"
    assert np.isclose(ab.b.time_s, target_time_s, rtol=0.0, atol=1e-15)
    assert ab.b.position_m[0] > position[0] + 0.9
    assert 0.0 < np.linalg.norm(decision.proposed_position_correction_m) < 0.05
    assert np.isfinite(ab.b.velocity_mps).all()
    assert np.isfinite(ab.b.accelerometer_bias_mps2).all()
