import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.ankle_contact import (
    AnkleContactConfig,
    AnkleContactDetector,
    DualFootFootholdCorrector,
    FootContactEvidence,
    FootStillnessProfile,
    FootSupportState,
    fit_stillness_profiles,
)
from biospur_fusion.root_r3.estimator import (
    CausalDelayedRootFilter,
    RootFilterConfig,
)
from biospur_fusion.root_r3.models import (
    AdditiveRootConstraint,
    ImuSample,
    PositionObservation,
    RootState,
)


def _state(position=(2.0, 1.0, 0.9), velocity=(0.3, -0.1, 0.0)):
    vector = np.r_[position, velocity, np.zeros(3)]
    return RootState(1.0, vector, np.eye(9) * 0.1)


def _evidence(side, contact, confidence=0.9):
    return FootContactEvidence(
        side, 1.0, confidence, contact, 0.01, 0.01, 0.0, "TEST"
    )


def _profile():
    return FootStillnessProfile(0.1, 0.1)


def _support_evidence(
    side, state, *, confidence=0.9, prior_held=False, height=0.0,
    prior_held_support_confidence=None,
):
    return FootContactEvidence(
        side,
        1.0,
        confidence,
        state is FootSupportState.STANCE_CONFIRMED,
        0.01,
        0.01,
        height,
        "TEST",
        support_state=state.value,
        prior_held=prior_held,
        prior_held_support_confidence=prior_held_support_confidence,
        positive_swing=state is FootSupportState.SWING_CONFIRMED,
        swing_observable=True,
    )


def test_static_fit_and_hysteresis_reject_motion_and_high_foot():
    rng = np.random.default_rng(4)
    static = {
        side: [(np.c_[
            rng.normal(0.0, 0.005, 500),
            rng.normal(0.0, 0.005, 500),
            rng.normal(9.80665, 0.005, 500),
        ], rng.normal(0.0, 0.004, (500, 3)))]
        for side in ("left", "right")
    }
    config = AnkleContactConfig(window_samples=20, enter_samples=3, exit_samples=2)
    detector = AnkleContactDetector(fit_stillness_profiles(static, config), config)
    latest = None
    for index in range(25):
        latest = detector.update(
            "left", time_s=index * 0.005,
            acceleration_mps2=np.array([0.0, 0.0, 9.80665]),
            gyro_rad_s=np.zeros(3), relative_height_m=0.0,
            relative_speed_mps=0.0,
        )
    assert latest.contact
    for index in range(2):
        latest = detector.update(
            "left", time_s=0.2 + index * 0.005,
            acceleration_mps2=np.array([2.0, 0.0, 9.80665]),
            gyro_rad_s=np.array([2.0, 0.0, 0.0]), relative_height_m=0.10,
            relative_speed_mps=1.0,
        )
    assert not latest.contact


def test_motion_only_becomes_uncertain_and_positive_lift_confirms_swing():
    profile = {"left": _profile(), "right": _profile()}
    config = AnkleContactConfig(
        window_samples=5, enter_samples=2, exit_samples=2
    )
    detector = AnkleContactDetector(profile, config)

    for index in range(8):
        evidence = detector.update(
            "left",
            time_s=index * 0.005,
            acceleration_mps2=np.array([0.0, 0.0, 9.80665]),
            gyro_rad_s=np.zeros(3),
            relative_height_m=0.0,
            relative_speed_mps=0.0,
            positive_swing=False,
            swing_observable=True,
        )
    assert evidence.resolved_support_state is FootSupportState.STANCE_CONFIRMED

    uncertain = detector.update(
        "left",
        time_s=0.05,
        acceleration_mps2=np.array([3.0, 0.0, 9.80665]),
        gyro_rad_s=np.array([5.0, 0.0, 0.0]),
        relative_height_m=0.0,
        relative_speed_mps=2.0,
        positive_swing=False,
        swing_observable=True,
    )
    assert uncertain.resolved_support_state is FootSupportState.UNCERTAIN
    assert not uncertain.contact
    assert uncertain.reason == "SUPPORT_UNCERTAIN_MOTION_ONLY"

    for index in range(2):
        swing = detector.update(
            "left",
            time_s=0.055 + index * 0.005,
            acceleration_mps2=np.array([3.0, 0.0, 9.80665]),
            gyro_rad_s=np.array([5.0, 0.0, 0.0]),
            relative_height_m=0.10,
            relative_speed_mps=2.0,
            positive_swing=True,
            swing_observable=True,
        )
    assert swing.resolved_support_state is FootSupportState.SWING_CONFIRMED
    assert swing.reason == "CONTACT_EXIT_SWING_CONFIRMED"


def test_stationary_activity_prior_holds_motion_and_positive_lift_uncertain():
    config = AnkleContactConfig(
        window_samples=5, enter_samples=2, exit_samples=2
    )
    detector = AnkleContactDetector(
        {"left": _profile(), "right": _profile()},
        config,
        stationary_no_flight_prior=True,
    )
    for index in range(8):
        evidence = detector.update(
            "left",
            time_s=index * 0.005,
            acceleration_mps2=np.array([0.0, 0.0, 9.80665]),
            gyro_rad_s=np.zeros(3),
            relative_height_m=0.0,
            relative_speed_mps=0.0,
            positive_swing=False,
            swing_observable=True,
        )
    assert not evidence.prior_held
    last_confirmed_confidence = evidence.confidence
    uncertain = detector.update(
        "left",
        time_s=0.05,
        acceleration_mps2=np.array([3.0, 0.0, 9.80665]),
        gyro_rad_s=np.array([5.0, 0.0, 0.0]),
        relative_height_m=0.0,
        relative_speed_mps=2.0,
        positive_swing=False,
        swing_observable=True,
    )
    assert uncertain.resolved_support_state is FootSupportState.UNCERTAIN
    assert uncertain.prior_held
    assert uncertain.owns_root_constraint
    assert uncertain.activity_prior_conflict
    assert uncertain.prior_held_support_confidence == pytest.approx(
        last_confirmed_confidence
    )

    for index in range(config.exit_samples + 2):
        positive = detector.update(
            "left",
            time_s=0.055 + index * 0.005,
            acceleration_mps2=np.array([3.0, 0.0, 9.80665]),
            gyro_rad_s=np.array([5.0, 0.0, 0.0]),
            relative_height_m=0.10,
            relative_speed_mps=2.0,
            positive_swing=True,
            swing_observable=True,
        )
    assert positive.resolved_support_state is FootSupportState.UNCERTAIN
    assert positive.prior_held and positive.owns_root_constraint
    assert not positive.contact
    assert positive.positive_swing and positive.activity_prior_conflict
    assert positive.prior_held_support_confidence == pytest.approx(
        last_confirmed_confidence
    )
    assert positive.reason == (
        "ACTIVITY_PRIOR_CONFLICT_POSITIVE_LIFT_PRIOR_HELD"
    )
    for index in range(30):
        recovered = detector.update(
            "left",
            time_s=0.10 + index * 0.005,
            acceleration_mps2=np.array([0.0, 0.0, 9.80665]),
            gyro_rad_s=np.zeros(3),
            relative_height_m=0.0,
            relative_speed_mps=0.0,
            positive_swing=False,
            swing_observable=True,
        )
        if recovered.resolved_support_state is FootSupportState.STANCE_CONFIRMED:
            break
    assert recovered.resolved_support_state is FootSupportState.STANCE_CONFIRMED
    assert recovered.prior_held_support_confidence is None


def test_without_activity_prior_positive_lift_releases_after_existing_dwell():
    config = AnkleContactConfig(
        window_samples=5, enter_samples=2, exit_samples=12
    )
    detector = AnkleContactDetector(
        {"left": _profile(), "right": _profile()}, config
    )
    for index in range(8):
        evidence = detector.update(
            "left",
            time_s=index * 0.005,
            acceleration_mps2=np.array([0.0, 0.0, 9.80665]),
            gyro_rad_s=np.zeros(3),
            relative_height_m=0.0,
            relative_speed_mps=0.0,
            positive_swing=False,
            swing_observable=True,
        )
    assert evidence.resolved_support_state is FootSupportState.STANCE_CONFIRMED
    for index in range(12):
        evidence = detector.update(
            "left",
            time_s=0.05 + index * 0.005,
            acceleration_mps2=np.array([3.0, 0.0, 9.80665]),
            gyro_rad_s=np.array([5.0, 0.0, 0.0]),
            relative_height_m=0.10,
            relative_speed_mps=2.0,
            positive_swing=True,
            swing_observable=True,
        )
    assert evidence.resolved_support_state is FootSupportState.SWING_CONFIRMED
    assert evidence.reason == "CONTACT_EXIT_SWING_CONFIRMED"


def test_stationary_activity_prior_holds_bilateral_positive_lift_after_entry():
    config = AnkleContactConfig(
        window_samples=5, enter_samples=2, exit_samples=12
    )
    detector = AnkleContactDetector(
        {"left": _profile(), "right": _profile()},
        config,
        stationary_no_flight_prior=True,
    )
    for index in range(8):
        for side in ("left", "right"):
            evidence = detector.update(
                side,
                time_s=index * 0.005,
                acceleration_mps2=np.array([0.0, 0.0, 9.80665]),
                gyro_rad_s=np.zeros(3),
                relative_height_m=0.0,
                relative_speed_mps=0.0,
                positive_swing=False,
                swing_observable=True,
            )
            assert not evidence.prior_held
    for index in range(config.exit_samples + 2):
        for side in ("left", "right"):
            evidence = detector.update(
                side,
                time_s=0.05 + index * 0.005,
                acceleration_mps2=np.array([3.0, 0.0, 9.80665]),
                gyro_rad_s=np.array([5.0, 0.0, 0.0]),
                relative_height_m=0.10,
                relative_speed_mps=2.0,
                positive_swing=True,
                swing_observable=True,
            )
            assert evidence.resolved_support_state is FootSupportState.UNCERTAIN
            assert evidence.prior_held and evidence.activity_prior_conflict
    assert all(
        detector.support_state(side) is FootSupportState.UNCERTAIN
        for side in ("left", "right")
    )


def test_stationary_activity_prior_does_not_invent_initial_stance_identity():
    config = AnkleContactConfig(
        window_samples=5, enter_samples=2, exit_samples=12
    )
    detector = AnkleContactDetector(
        {"left": _profile(), "right": _profile()},
        config,
        stationary_no_flight_prior=True,
    )
    for index in range(8):
        evidence = detector.update(
            "left",
            time_s=index * 0.005,
            acceleration_mps2=np.array([3.0, 0.0, 9.80665]),
            gyro_rad_s=np.array([5.0, 0.0, 0.0]),
            relative_height_m=0.10,
            relative_speed_mps=2.0,
            positive_swing=True,
            swing_observable=True,
        )
    assert evidence.resolved_support_state is FootSupportState.SWING_CONFIRMED
    assert not evidence.prior_held
    assert not evidence.owns_root_constraint


def test_dual_foothold_persists_world_point_and_releases_independently():
    corrector = DualFootFootholdCorrector()
    offsets = {"left": np.array([-0.1, 0.0, -0.9]), "right": np.array([0.1, 0.0, -0.9])}
    velocities = {"left": np.zeros(3), "right": np.zeros(3)}
    state = _state()
    state, first = corrector.update(
        state,
        evidence={"left": _evidence("left", True), "right": _evidence("right", True)},
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    assert set(first.entered_sides) == {"left", "right"}
    assert set(first.constrained_sides) == {"left", "right"}
    moved = RootState(state.time_s, state.vector + np.r_[[0.2, 0.0, 0.0], np.zeros(6)], state.covariance)
    corrected, decision = corrector.update(
        moved,
        evidence={"left": _evidence("left", True), "right": _evidence("right", False)},
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    assert decision.active_sides == ("left",)
    assert decision.released_sides == ("right",)
    assert corrected.position_m[0] < moved.position_m[0]
    assert corrected.position_m[2] == moved.position_m[2]


def test_uncertain_retains_identity_without_constraint_and_recovers_no_reanchor():
    corrector = DualFootFootholdCorrector()
    offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    velocities = {side: np.zeros(3) for side in offsets}
    state, entered = corrector.update(
        _state(),
        evidence={
            "left": _support_evidence(
                "left", FootSupportState.STANCE_CONFIRMED
            ),
            "right": _support_evidence(
                "right", FootSupportState.SWING_CONFIRMED
            ),
        },
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    original = corrector.footholds_world_m()["left"].copy()
    assert entered.entered_sides == ("left",)

    moved = RootState(
        1.1,
        state.vector + np.r_[[0.2, 0.0, 0.0], np.zeros(6)],
        state.covariance,
    )
    unchanged, uncertain = corrector.update(
        moved,
        evidence={
            "left": _support_evidence(
                "left", FootSupportState.UNCERTAIN, confidence=0.1
            ),
            "right": _support_evidence(
                "right", FootSupportState.SWING_CONFIRMED
            ),
        },
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    np.testing.assert_array_equal(unchanged.vector, moved.vector)
    assert uncertain.active_sides == ("left",)
    assert uncertain.constrained_sides == ()
    assert uncertain.released_sides == ()
    np.testing.assert_array_equal(
        corrector.footholds_world_m()["left"], original
    )

    recovered, decision = corrector.update(
        moved,
        evidence={
            "left": _support_evidence(
                "left", FootSupportState.STANCE_CONFIRMED
            ),
            "right": _support_evidence(
                "right", FootSupportState.SWING_CONFIRMED
            ),
        },
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    assert decision.entered_sides == ()
    assert decision.constrained_sides == ("left",)
    assert recovered.position_m[0] < moved.position_m[0]
    np.testing.assert_array_equal(
        corrector.footholds_world_m()["left"], original
    )


def test_prior_held_uncertain_constrains_but_is_not_confirmed_stance():
    corrector = DualFootFootholdCorrector()
    offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    velocities = {side: np.zeros(3) for side in offsets}
    state, _ = corrector.update(
        _state(),
        evidence={
            "left": _support_evidence(
                "left", FootSupportState.STANCE_CONFIRMED
            ),
            "right": _support_evidence(
                "right", FootSupportState.SWING_CONFIRMED
            ),
        },
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    moved = RootState(
        1.1,
        state.vector + np.r_[[0.1, 0.0, 0.0], np.zeros(6)],
        state.covariance,
    )
    evidence = _support_evidence(
        "left",
        FootSupportState.UNCERTAIN,
        confidence=0.1,
        prior_held=True,
        prior_held_support_confidence=0.9,
    )
    assert not evidence.is_confirmed_stance
    corrected, decision = corrector.update(
        moved,
        evidence={
            "left": evidence,
            "right": _support_evidence(
                "right", FootSupportState.SWING_CONFIRMED
            ),
        },
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    assert decision.constrained_sides == ("left",)
    assert corrected.position_m[0] < moved.position_m[0]
    assert decision.entered_sides == ()
    assert decision.released_sides == ()
    history = corrector.foothold_ownership_history()
    assert len(history) == 1
    assert history[0].release_time_s is None


def test_foothold_history_uses_root_ownership_half_open_intervals():
    corrector = DualFootFootholdCorrector()
    offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    velocities = {side: np.zeros(3) for side in offsets}
    vector = _state().vector
    entered_state = RootState(1.0, vector.copy(), np.eye(9))
    entered, _ = corrector.update(
        entered_state,
        evidence={
            "left": _evidence("left", True),
            "right": _evidence("right", False),
        },
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    assert corrector.footholds_at_time(0.999) == {}
    np.testing.assert_allclose(
        corrector.footholds_at_time(1.0)["left"],
        entered.position_m + offsets["left"],
    )

    released_state = RootState(1.5, entered.vector.copy(), np.eye(9))
    corrector.update(
        released_state,
        evidence={
            "left": _evidence("left", False),
            "right": _evidence("right", False),
        },
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    assert "left" in corrector.footholds_at_time(1.499999)
    assert corrector.footholds_at_time(1.5) == {}
    history = corrector.foothold_ownership_history()
    assert len(history) == 1
    assert history[0].start_time_s == 1.0
    assert history[0].release_time_s == 1.5


def test_filter_accepts_only_same_epoch_current_constraint():
    state = _state()
    root_filter = CausalDelayedRootFilter(state)
    updated = RootState(state.time_s, state.vector + np.r_[[0.01, 0, 0], np.zeros(6)], state.covariance)
    operator = AdditiveRootConstraint(updated.vector - state.vector)
    root_filter.apply_current_constraint(
        updated, operator=operator, owner="TEST_POSE_REGAUGE"
    )
    np.testing.assert_allclose(root_filter.current_state.position_m, updated.position_m)
    with pytest.raises(ValueError, match="timestamp"):
        root_filter.apply_current_constraint(
            RootState(2.0, updated.vector, updated.covariance),
            operator=operator,
            owner="TEST_POSE_REGAUGE",
        )


def test_prior_held_contact_does_not_posthoc_clear_delayed_uwb_update():
    initial = _state(velocity=(0.0, 0.0, 0.0))
    corrector = DualFootFootholdCorrector()
    offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    velocities = {side: np.zeros(3) for side in offsets}
    confirmed = {
        "left": _support_evidence(
            "left", FootSupportState.STANCE_CONFIRMED
        ),
        "right": _support_evidence(
            "right", FootSupportState.SWING_CONFIRMED
        ),
    }
    initial, _ = corrector.update(
        initial,
        evidence=confirmed,
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    config = RootFilterConfig(
        maximum_position_influence_m=0.05,
        nis_limit_3d=1e12,
        fixed_lag_s=2.0,
        recovery_good_events=1,
    )
    root_filter = CausalDelayedRootFilter(initial, config)
    for sequence, time_s in enumerate((1.5, 2.0), start=1):
        assert root_filter.add_imu(ImuSample(
            time_s,
            time_s,
            np.array([0.0, 0.0, 9.80665]),
            np.eye(3),
            sequence,
        ))
    before_uwb = root_filter.current_state.position_m.copy()
    observation = PositionObservation(
        1.5,
        2.0,
        before_uwb + np.array([2.0, 0.0, 0.0]),
        np.eye(3) * 0.01,
        "T",
        (0, 1, 2, 3),
    )
    uwb_decision = root_filter.add_position(
        observation, processing_time_s=2.0
    )
    assert uwb_decision.accepted
    assert np.linalg.norm(
        uwb_decision.availability_applied_position_delta_m
    ) > 0.0

    prior_held = {
        "left": _support_evidence(
            "left",
            FootSupportState.UNCERTAIN,
            confidence=0.1,
            prior_held=True,
            prior_held_support_confidence=0.9,
        ),
        "right": _support_evidence(
            "right", FootSupportState.SWING_CONFIRMED
        ),
    }
    corrected, contact_decision = corrector.update(
        root_filter.current_state,
        evidence=prior_held,
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    root_filter.apply_current_constraint(
        corrected,
        operator=contact_decision.replay_operator,
        owner="TEST_PRIOR_HELD_CONTACT",
    )

    assert np.linalg.norm(contact_decision.applied_position_delta_m[:2]) <= 0.04 + 1e-12
    assert np.linalg.norm(root_filter.current_state.position_m - before_uwb) > 0.0


def test_pose_change_reconcile_exactly_preserves_single_owned_xy_foothold():
    corrector = DualFootFootholdCorrector()
    evidence = {
        "left": _evidence("left", True),
        "right": _evidence("right", False),
    }
    old_offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    zero_velocity = {side: np.zeros(3) for side in old_offsets}
    state, _ = corrector.update(
        _state(), evidence=evidence,
        ankle_offset_world_m=old_offsets,
        ankle_offset_velocity_world_mps=zero_velocity,
    )
    new_offsets = {
        "left": old_offsets["left"] + np.array([0.18, -0.07, 0.0]),
        "right": old_offsets["right"].copy(),
    }
    reconciled, decision = corrector.reconcile_pose_change(
        state,
        evidence=evidence,
        previous_ankle_offset_world_m=old_offsets,
        previous_ankle_offset_velocity_world_mps=zero_velocity,
        ankle_offset_world_m=new_offsets,
        ankle_offset_velocity_world_mps=zero_velocity,
    )
    assert decision.accepted
    assert decision.constrained_sides == ("left",)
    assert decision.post_xy_residual_m["left"] < 1e-12
    checked, soft = corrector.update(
        reconciled, evidence=evidence,
        ankle_offset_world_m=new_offsets,
        ankle_offset_velocity_world_mps=zero_velocity,
    )
    np.testing.assert_allclose(soft.applied_position_delta_m[:2], 0.0, atol=1e-12)
    np.testing.assert_allclose(checked.position_m, reconciled.position_m)


def test_pose_change_reconcile_preserves_primary_and_reports_both_feet():
    corrector = DualFootFootholdCorrector()
    evidence = {
        "left": _evidence("left", True, 0.95),
        "right": _evidence("right", True, 0.75),
    }
    old_offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    zero_velocity = {side: np.zeros(3) for side in old_offsets}
    state, _ = corrector.update(
        _state(), evidence=evidence,
        ankle_offset_world_m=old_offsets,
        ankle_offset_velocity_world_mps=zero_velocity,
    )
    new_offsets = {
        "left": old_offsets["left"] + np.array([0.10, 0.0, 0.0]),
        "right": old_offsets["right"] + np.array([-0.10, 0.0, 0.0]),
    }
    _updated, decision = corrector.reconcile_pose_change(
        state,
        evidence=evidence,
        previous_ankle_offset_world_m=old_offsets,
        previous_ankle_offset_velocity_world_mps=zero_velocity,
        ankle_offset_world_m=new_offsets,
        ankle_offset_velocity_world_mps=zero_velocity,
    )
    assert decision.primary_side == "left"
    assert decision.constrained_sides == ("left",)
    assert set(decision.pre_xy_residual_m) == {"left", "right"}
    assert set(decision.post_xy_residual_m) == {"left", "right"}
    assert decision.post_xy_residual_m["left"] < 1e-12
    assert decision.post_xy_residual_m["right"] > 0.06


def test_pose_reconcile_does_not_consume_existing_foothold_residual():
    corrector = DualFootFootholdCorrector()
    evidence = {
        "left": _evidence("left", True),
        "right": _evidence("right", False),
    }
    offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    velocity = {side: np.zeros(3) for side in offsets}
    owned, _ = corrector.update(
        _state(), evidence=evidence,
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocity,
    )
    drifted = RootState(
        owned.time_s,
        owned.vector + np.r_[[0.13, 0.0, 0.0], np.zeros(6)],
        owned.covariance,
    )

    reconciled, reconcile = corrector.reconcile_pose_change(
        drifted,
        evidence=evidence,
        previous_ankle_offset_world_m=offsets,
        previous_ankle_offset_velocity_world_mps=velocity,
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocity,
    )
    corrected, soft = corrector.update(
        reconciled, evidence=evidence,
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocity,
    )

    np.testing.assert_allclose(reconcile.applied_position_delta_m, 0.0)
    assert np.linalg.norm(soft.applied_position_delta_m[:2]) <= 0.04 + 1e-12
    assert np.linalg.norm(corrected.position_m[:2] - drifted.position_m[:2]) <= 0.04 + 1e-12


def test_pose_reconcile_applies_only_offset_and_velocity_gauge_delta():
    corrector = DualFootFootholdCorrector()
    evidence = {
        "left": _evidence("left", True),
        "right": _evidence("right", False),
    }
    old_offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    new_offsets = {
        "left": old_offsets["left"] + np.array([0.02, -0.01, 0.0]),
        "right": old_offsets["right"].copy(),
    }
    old_velocity = {
        "left": np.array([0.2, -0.1, 0.0]),
        "right": np.zeros(3),
    }
    new_velocity = {
        "left": np.array([0.1, -0.07, 0.0]),
        "right": np.zeros(3),
    }
    owned, _ = corrector.update(
        _state(), evidence=evidence,
        ankle_offset_world_m=old_offsets,
        ankle_offset_velocity_world_mps=old_velocity,
    )
    drifted = RootState(
        owned.time_s,
        owned.vector + np.r_[[0.13, 0.0, 0.0], np.zeros(6)],
        owned.covariance,
    )

    reconciled, decision = corrector.reconcile_pose_change(
        drifted,
        evidence=evidence,
        previous_ankle_offset_world_m=old_offsets,
        previous_ankle_offset_velocity_world_mps=old_velocity,
        ankle_offset_world_m=new_offsets,
        ankle_offset_velocity_world_mps=new_velocity,
    )

    np.testing.assert_allclose(
        decision.applied_position_delta_m[:2], [-0.02, 0.01]
    )
    np.testing.assert_allclose(
        decision.applied_velocity_delta_mps[:2], [0.1, -0.03]
    )
    np.testing.assert_allclose(
        reconciled.position_m[:2] - drifted.position_m[:2], [-0.02, 0.01]
    )


def test_primary_exit_handoff_cannot_create_an_unbounded_reconcile_snap():
    corrector = DualFootFootholdCorrector()
    both = {
        "left": _evidence("left", True, 0.95),
        "right": _evidence("right", True, 0.75),
    }
    old_offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    velocity = {side: np.zeros(3) for side in old_offsets}
    state, _ = corrector.update(
        _state(), evidence=both,
        ankle_offset_world_m=old_offsets,
        ankle_offset_velocity_world_mps=velocity,
    )
    conflicting_offsets = {
        "left": old_offsets["left"].copy(),
        "right": old_offsets["right"] + np.array([0.13, 0.0, 0.0]),
    }
    release_left = {
        "left": _evidence("left", False, 0.0),
        "right": _evidence("right", True, 0.75),
    }

    after_release, release = corrector.update(
        state, evidence=release_left,
        ankle_offset_world_m=conflicting_offsets,
        ankle_offset_velocity_world_mps=velocity,
    )
    next_state, next_reconcile = corrector.reconcile_pose_change(
        after_release,
        evidence=release_left,
        previous_ankle_offset_world_m=conflicting_offsets,
        previous_ankle_offset_velocity_world_mps=velocity,
        ankle_offset_world_m=conflicting_offsets,
        ankle_offset_velocity_world_mps=velocity,
    )

    assert release.released_sides == ("left",)
    assert release.constrained_sides == ("right",)
    assert np.linalg.norm(release.applied_position_delta_m[:2]) <= 0.04 + 1e-12
    assert next_reconcile.primary_side == "right"
    np.testing.assert_allclose(next_reconcile.applied_position_delta_m, 0.0)
    np.testing.assert_allclose(next_state.position_m, after_release.position_m)


def test_root_pose_gauge_reexpression_weights_consistent_bilateral_feet():
    corrector = DualFootFootholdCorrector()
    evidence = {
        "left": _evidence("left", True, 0.9),
        "right": _evidence("right", True, 0.6),
    }
    source = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    target = {
        side: offset + np.array([0.02, -0.01, 0.0])
        for side, offset in source.items()
    }
    decision = corrector.reexpress_root_between_pose_gauges(
        np.array([2.0, 1.0, 0.9]),
        owned_footholds_world_m={
            "left": np.zeros(3), "right": np.zeros(3)
        },
        evidence=evidence,
        source_ankle_offset_world_m=source,
        target_ankle_offset_world_m=target,
        primary_side_at_measurement="left",
    )

    assert decision.constrained_sides == ("left", "right")
    np.testing.assert_allclose(
        decision.applied_position_delta_m, [-0.02, 0.01, 0.0]
    )


def test_root_pose_gauge_reexpression_uses_stable_primary_on_conflict():
    corrector = DualFootFootholdCorrector()
    evidence = {
        "left": _evidence("left", True, 0.95),
        "right": _evidence("right", True, 0.55),
    }
    source = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    target = {
        "left": source["left"] + np.array([0.10, 0.0, 0.0]),
        "right": source["right"] - np.array([0.10, 0.0, 0.0]),
    }
    corrector.update(
        _state(),
        evidence={
            "left": _evidence("left", False, 0.1),
            "right": _evidence("right", True, 0.99),
        },
        ankle_offset_world_m=source,
        ankle_offset_velocity_world_mps={
            side: np.zeros(3) for side in source
        },
    )
    assert corrector.primary_side == "right"
    first = corrector.reexpress_root_between_pose_gauges(
        np.array([2.0, 1.0, 0.9]),
        owned_footholds_world_m={
            "left": np.zeros(3), "right": np.zeros(3)
        },
        evidence=evidence,
        source_ankle_offset_world_m=source,
        target_ankle_offset_world_m=target,
        primary_side_at_measurement="left",
    )
    reversed_confidence = {
        "left": _evidence("left", True, 0.45),
        "right": _evidence("right", True, 0.99),
    }
    second = corrector.reexpress_root_between_pose_gauges(
        np.array([2.0, 1.0, 0.9]),
        owned_footholds_world_m={
            "left": np.zeros(3), "right": np.zeros(3)
        },
        evidence=reversed_confidence,
        source_ankle_offset_world_m=source,
        target_ankle_offset_world_m=target,
        primary_side_at_measurement="left",
    )

    assert first.primary_side == second.primary_side == "left"
    assert corrector.primary_side == "right"
    assert first.constrained_sides == second.constrained_sides == ("left",)
    np.testing.assert_allclose(first.applied_position_delta_m, [-0.1, 0.0, 0.0])
    np.testing.assert_allclose(second.applied_position_delta_m, [-0.1, 0.0, 0.0])


def test_historical_root_pose_gauge_fails_without_primary_snapshot():
    corrector = DualFootFootholdCorrector()
    offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    with pytest.raises(ValueError, match="primary snapshot"):
        corrector.reexpress_root_between_pose_gauges(
            np.array([2.0, 1.0, 0.9]),
            owned_footholds_world_m={"left": np.zeros(3)},
            evidence={
                "left": _evidence("left", True),
                "right": _evidence("right", False),
            },
            source_ankle_offset_world_m=offsets,
            target_ankle_offset_world_m=offsets,
            primary_side_at_measurement=None,
        )


def test_historical_primary_is_purely_reselected_for_confirmed_subset():
    corrector = DualFootFootholdCorrector()
    offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    evidence = {
        "left": FootContactEvidence(
            "left", 1.0, 0.95, False, 1.0, 1.0, 0.0,
            "SUPPORT_UNCERTAIN_MOTION_ONLY_PRIOR_HELD",
            support_state="UNCERTAIN", prior_held=True,
        ),
        "right": FootContactEvidence(
            "right", 1.0, 0.8, True, 0.1, 0.1, 0.0,
            "CONTACT_HELD", support_state="STANCE_CONFIRMED",
        ),
    }

    decision = corrector.reexpress_root_between_pose_gauges(
        np.array([2.0, 1.0, 0.9]),
        owned_footholds_world_m={"right": np.array([2.1, 1.0, 0.0])},
        evidence=evidence,
        source_ankle_offset_world_m=offsets,
        target_ankle_offset_world_m=offsets,
        primary_side_at_measurement="left",
    )

    assert decision.primary_side == "right"
    assert decision.constrained_sides == ("right",)
    assert corrector.primary_side is None


def test_contact_root_target_query_is_pure_and_uses_historical_primary():
    corrector = DualFootFootholdCorrector()
    offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    evidence = {
        "left": _evidence("left", True, 0.9),
        "right": _evidence("right", True, 0.8),
    }
    corrector.update(
        _state(),
        evidence={
            "left": _evidence("left", False),
            "right": _evidence("right", True),
        },
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps={
            side: np.zeros(3) for side in offsets
        },
    )
    assert corrector.primary_side == "right"
    footholds = {
        "left": np.array([1.9, 1.0, 0.0]),
        "right": np.array([2.3, 1.0, 0.0]),
    }
    decision = corrector.root_target_for_owned_footholds(
        np.array([2.2, 1.1, 0.9]),
        owned_footholds_world_m=footholds,
        evidence=evidence,
        ankle_offset_world_m=offsets,
        primary_side_at_measurement="left",
    )

    assert decision.primary_side == "left"
    assert decision.constrained_sides == ("left",)
    assert corrector.primary_side == "right"
    np.testing.assert_allclose(decision.root_position_m, [2.0, 1.0, 0.9])


def test_reexpressed_arrival_root_needs_no_alpha_zero_pose_reconcile():
    corrector = DualFootFootholdCorrector()
    evidence = {
        side: _evidence(side, True, 0.9)
        for side in ("left", "right")
    }
    published_offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    zero_velocity = {side: np.zeros(3) for side in published_offsets}
    published_root, _ = corrector.update(
        _state(), evidence=evidence,
        ankle_offset_world_m=published_offsets,
        ankle_offset_velocity_world_mps=zero_velocity,
    )
    source_offsets = {
        side: offset + np.array([0.08, -0.03, 0.0])
        for side, offset in published_offsets.items()
    }
    source_root = published_root.position_m - np.array([0.08, -0.03, 0.0])
    gauge = corrector.reexpress_root_between_pose_gauges(
        source_root,
        owned_footholds_world_m=corrector.footholds_world_m(),
        evidence=evidence,
        source_ankle_offset_world_m=source_offsets,
        target_ankle_offset_world_m=published_offsets,
        primary_side_at_measurement="left",
    )
    state = RootState(
        published_root.time_s,
        np.r_[gauge.root_position_m, published_root.vector[3:]],
        published_root.covariance,
    )
    reconciled, decision = corrector.reconcile_pose_change(
        state,
        evidence=evidence,
        previous_ankle_offset_world_m=published_offsets,
        previous_ankle_offset_velocity_world_mps=zero_velocity,
        ankle_offset_world_m=published_offsets,
        ankle_offset_velocity_world_mps=zero_velocity,
    )

    np.testing.assert_allclose(gauge.root_position_m, published_root.position_m)
    np.testing.assert_allclose(decision.applied_position_delta_m[:2], 0.0)
    np.testing.assert_allclose(reconciled.position_m, state.position_m)


def test_bilateral_fast_release_exposes_flight_and_recontact() -> None:
    profile_data = {
        side: [(np.tile([0.0, 0.0, 9.80665], (300, 1)), np.zeros((300, 3)))]
        for side in ("left", "right")
    }
    config = AnkleContactConfig(
        window_samples=10, threshold_scale=1.5,
        enter_samples=4, exit_samples=3,
    )
    # Exactly-zero calibration features are intentionally invalid thresholds;
    # add known small noise so the profile remains finite and reproducible.
    rng = np.random.default_rng(11)
    profile_data = {
        side: [(
            profile_data[side][0][0] + rng.normal(0.0, 0.002, (300, 3)),
            rng.normal(0.0, 0.002, (300, 3)),
        )]
        for side in ("left", "right")
    }
    detector = AnkleContactDetector(
        fit_stillness_profiles(profile_data, config), config
    )

    def sample(side, index, *, flight=False):
        return detector.update(
            side, time_s=index * 0.005,
            acceleration_mps2=(
                np.array([3.0, 0.0, 4.0]) if flight
                else np.array([0.0, 0.0, 9.80665])
            ),
            gyro_rad_s=(np.array([4.0, 0.0, 0.0]) if flight else np.zeros(3)),
            relative_height_m=(0.12 if flight else 0.0),
            relative_speed_mps=(2.0 if flight else 0.0),
        )

    for index in range(20):
        left = sample("left", index)
        right = sample("right", index)
    assert left.contact and right.contact
    for index in range(20, 23):
        left = sample("left", index, flight=True)
        right = sample("right", index, flight=True)
    assert not left.contact and not right.contact
    # After the moving window clears, a stable low ankle can own a new contact.
    for index in range(23, 45):
        left = sample("left", index)
    assert left.contact


def test_supported_ankle_vertical_envelope_is_not_a_z_pin() -> None:
    corrector = DualFootFootholdCorrector()
    offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    velocities = {side: np.zeros(3) for side in offsets}
    state, _ = corrector.update(
        _state(),
        evidence={
            "left": _evidence("left", True),
            "right": _evidence("right", False),
        },
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    # A 20 cm heel-rise remains untouched inside the broad 43 cm envelope.
    raised = RootState(
        state.time_s, state.vector + np.r_[[0.0, 0.0, 0.20], np.zeros(6)],
        state.covariance,
    )
    inside, _ = corrector.update(
        raised,
        evidence={
            "left": _evidence("left", True),
            "right": _evidence("right", False),
        },
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    assert inside.position_m[2] == raised.position_m[2]
    # A metre-scale UWB vertical excursion while support is active is reduced.
    impossible = RootState(
        state.time_s, state.vector + np.r_[[0.0, 0.0, 1.0], np.zeros(6)],
        state.covariance,
    )
    bounded, _ = corrector.update(
        impossible,
        evidence={
            "left": _evidence("left", True),
            "right": _evidence("right", False),
        },
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    assert bounded.position_m[2] < impossible.position_m[2]


def test_soft_contact_replay_operator_recomputes_unsaturated_and_saturated_delta():
    corrector = DualFootFootholdCorrector()
    offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    velocities = {side: np.zeros(3) for side in offsets}
    evidence = {
        "left": _evidence("left", True),
        "right": _evidence("right", False),
    }
    entered, _ = corrector.update(
        _state(velocity=(0.0, 0.0, 0.0)),
        evidence=evidence,
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    small = RootState(
        entered.time_s,
        entered.vector + np.r_[[0.01, 0.0, 0.0], np.zeros(6)],
        entered.covariance,
    )
    corrected_small, decision = corrector.update(
        small,
        evidence=evidence,
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    assert decision.replay_operator is not None
    np.testing.assert_array_equal(
        decision.replay_operator.apply(small).vector,
        corrected_small.vector,
    )
    with pytest.raises(ValueError, match="read-only"):
        decision.replay_operator.position_target_m[0] = 100.0
    with pytest.raises(ValueError, match="read-only"):
        decision.replay_operator.velocity_target_mps[0] = 100.0

    revised_small = RootState(
        small.time_s,
        entered.vector + np.r_[[0.02, 0.0, 0.0], np.zeros(6)],
        entered.covariance,
    )
    revised_small_after = decision.replay_operator.apply(revised_small)
    original_delta = corrected_small.position_m - small.position_m
    revised_delta = revised_small_after.position_m - revised_small.position_m
    assert abs(revised_delta[0]) > abs(original_delta[0])
    assert abs(revised_delta[0]) < 0.04

    saturated = RootState(
        small.time_s,
        entered.vector + np.r_[[0.20, 0.0, 0.0], np.zeros(6)],
        entered.covariance,
    )
    saturated_after = decision.replay_operator.apply(saturated)
    assert np.linalg.norm(
        saturated_after.position_m - saturated.position_m
    ) == pytest.approx(0.04)


def test_soft_contact_replay_operator_preserves_both_z_envelope_bounds():
    corrector = DualFootFootholdCorrector()
    offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    velocities = {side: np.zeros(3) for side in offsets}
    evidence = {
        "left": _evidence("left", True),
        "right": _evidence("right", False),
    }
    entered, _ = corrector.update(
        _state(velocity=(0.0, 0.0, 0.0)),
        evidence=evidence,
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    _same, decision = corrector.update(
        entered,
        evidence=evidence,
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    operator = decision.replay_operator
    assert operator is not None
    below = RootState(
        entered.time_s,
        entered.vector + np.r_[[0.0, 0.0, -0.20], np.zeros(6)],
        entered.covariance,
    )
    above = RootState(
        entered.time_s,
        entered.vector + np.r_[[0.0, 0.0, 1.00], np.zeros(6)],
        entered.covariance,
    )
    inside = RootState(
        entered.time_s,
        entered.vector + np.r_[[0.0, 0.0, 0.20], np.zeros(6)],
        entered.covariance,
    )

    assert operator.apply(below).position_m[2] > below.position_m[2]
    assert operator.apply(above).position_m[2] < above.position_m[2]
    assert operator.apply(inside).position_m[2] == inside.position_m[2]


def test_bilateral_selector_is_continuous_at_existing_coherence_gate():
    corrector = DualFootFootholdCorrector()
    evidence = {
        "left": _support_evidence(
            "left", FootSupportState.STANCE_CONFIRMED, confidence=0.0
        ),
        "right": _support_evidence(
            "right", FootSupportState.STANCE_CONFIRMED, confidence=1.0
        ),
    }
    gate = corrector.config.maximum_bilateral_root_target_disagreement_m

    def selected(disagreement):
        targets = np.array([[0.0, 0.0, 0.0], [disagreement, 0.0, 0.0]])
        constrained, rows, coefficients, primary = (
            corrector._select_root_target_rows_pure(
                active=("left", "right"),
                evidence=evidence,
                root_position_targets=targets,
                primary_side="left",
            )
        )
        return constrained, primary, np.average(
            targets[rows], axis=0, weights=coefficients
        )

    at_gate = selected(gate)
    above = selected(gate + 1e-9)
    below = selected(gate - 1e-6)
    assert at_gate[:2] == (("left",), "left")
    assert above[:2] == (("left",), "left")
    np.testing.assert_array_equal(at_gate[2], np.zeros(3))
    np.testing.assert_array_equal(above[2], np.zeros(3))
    assert below[0] == ("left", "right")
    assert 0.0 < below[2][0] < 1e-9


def test_bilateral_selector_anchors_zero_confidence_primary_without_jump():
    corrector = DualFootFootholdCorrector()
    evidence = {
        "left": _support_evidence(
            "left", FootSupportState.STANCE_CONFIRMED, confidence=0.0
        ),
        "right": _support_evidence(
            "right", FootSupportState.STANCE_CONFIRMED, confidence=0.8
        ),
    }
    targets = np.array([[0.0, 0.0, 0.0], [0.058, 0.0, 0.0]])
    constrained, rows, coefficients, primary = (
        corrector._select_root_target_rows_pure(
            active=("left", "right"),
            evidence=evidence,
            root_position_targets=targets,
            primary_side="left",
        )
    )
    selected = np.average(targets[rows], axis=0, weights=coefficients)
    assert constrained == ("left", "right")
    assert primary == "left"
    assert coefficients.sum() == pytest.approx(1.0)
    assert 0.0 < selected[0] < 0.001


def test_bilateral_selector_zero_distance_matches_old_confidence_average():
    corrector = DualFootFootholdCorrector()
    evidence = {
        "left": _support_evidence(
            "left", FootSupportState.STANCE_CONFIRMED, confidence=0.25
        ),
        "right": _support_evidence(
            "right", FootSupportState.STANCE_CONFIRMED, confidence=0.75
        ),
    }
    targets = np.array([[0.2, -0.1, 0.0], [0.2, -0.1, 0.0]])
    constrained, rows, coefficients, primary = (
        corrector._select_root_target_rows_pure(
            active=("left", "right"),
            evidence=evidence,
            root_position_targets=targets,
            primary_side="right",
        )
    )
    assert constrained == ("left", "right")
    assert primary == "right"
    np.testing.assert_allclose(coefficients, [0.25, 0.75])
    np.testing.assert_allclose(
        np.average(targets[rows], axis=0, weights=coefficients),
        np.average(targets, axis=0, weights=[0.25, 0.75]),
    )


def test_bilateral_selector_is_side_order_invariant_and_primary_stable():
    corrector = DualFootFootholdCorrector()
    evidence = {
        "left": _support_evidence(
            "left", FootSupportState.STANCE_CONFIRMED, confidence=0.4
        ),
        "right": _support_evidence(
            "right", FootSupportState.STANCE_CONFIRMED, confidence=0.7
        ),
    }
    targets = {"left": np.array([0.0, 0.0, 0.0]), "right": np.array([0.03, 0.0, 0.0])}

    def evaluate(order):
        rows_in = np.stack([targets[side] for side in order])
        constrained, rows, weights, primary = corrector._select_root_target_rows_pure(
            active=order,
            evidence=evidence,
            root_position_targets=rows_in,
            primary_side="right",
        )
        return primary, np.average(rows_in[rows], axis=0, weights=weights), set(constrained)

    forward = evaluate(("left", "right"))
    reverse = evaluate(("right", "left"))
    assert forward[0] == reverse[0] == "right"
    assert forward[2] == reverse[2] == {"left", "right"}
    np.testing.assert_allclose(forward[1], reverse[1])


def test_prior_held_confidence_is_frozen_for_episode_ordinary_constraint():
    corrector = DualFootFootholdCorrector()
    offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    velocities = {side: np.zeros(3) for side in offsets}
    confirmed = {
        "left": _support_evidence(
            "left", FootSupportState.STANCE_CONFIRMED, confidence=0.8
        ),
        "right": _support_evidence(
            "right", FootSupportState.SWING_CONFIRMED
        ),
    }
    entered, _ = corrector.update(
        _state(), evidence=confirmed,
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    moved = RootState(
        entered.time_s,
        entered.vector + np.r_[[0.03, 0.0, 0.0], np.zeros(6)],
        entered.covariance,
    )
    prior = {
        "left": _support_evidence(
            "left", FootSupportState.UNCERTAIN,
            confidence=0.0, prior_held=True,
            prior_held_support_confidence=0.8,
        ),
        "right": confirmed["right"],
    }
    _first, first = corrector.update(
        moved, evidence=prior,
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    assert corrector.prior_held_support_confidence() == {"left": 0.8}
    assert first.replay_operator is not None
    assert first.replay_operator.confidence == pytest.approx(0.8)

    prior["left"] = _support_evidence(
        "left", FootSupportState.UNCERTAIN,
        confidence=0.6, prior_held=True,
        prior_held_support_confidence=0.8,
    )
    _second, second = corrector.update(
        moved, evidence=prior,
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    assert second.replay_operator.confidence == pytest.approx(0.8)


def test_prior_held_confidence_recovery_release_and_new_episode_lifecycle():
    corrector = DualFootFootholdCorrector()
    offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    velocities = {side: np.zeros(3) for side in offsets}

    def evidence(state, confidence, prior=False, prior_confidence=None):
        return {
            "left": _support_evidence(
                "left", state, confidence=confidence, prior_held=prior,
                prior_held_support_confidence=prior_confidence,
            ),
            "right": _support_evidence(
                "right", FootSupportState.SWING_CONFIRMED
            ),
        }

    state, _ = corrector.update(
        _state(), evidence=evidence(FootSupportState.STANCE_CONFIRMED, 0.75),
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    state, prior = corrector.update(
        state, evidence=evidence(
            FootSupportState.UNCERTAIN, 0.0, True, 0.75
        ),
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    frozen_operator = prior.replay_operator
    assert frozen_operator is not None and frozen_operator.confidence == pytest.approx(0.75)
    state, recovered = corrector.update(
        state, evidence=evidence(FootSupportState.STANCE_CONFIRMED, 0.55),
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    assert corrector.prior_held_support_confidence() == {}
    assert recovered.replay_operator is not None
    assert recovered.replay_operator.confidence == pytest.approx(0.55)
    # The already-frozen journal operator cannot learn from future recovery.
    assert frozen_operator.confidence == pytest.approx(0.75)

    state, released = corrector.update(
        state, evidence=evidence(FootSupportState.SWING_CONFIRMED, 0.0),
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    assert released.released_sides == ("left",)
    assert corrector.prior_held_support_confidence() == {}
    state, entered = corrector.update(
        state, evidence=evidence(FootSupportState.STANCE_CONFIRMED, 0.4),
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    assert entered.entered_sides == ("left",)
    _state_after, new_prior = corrector.update(
        state, evidence=evidence(
            FootSupportState.UNCERTAIN, 0.1, True, 0.4
        ),
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    assert new_prior.replay_operator is not None
    assert new_prior.replay_operator.confidence == pytest.approx(0.4)


def test_prior_held_without_confirmed_episode_cannot_create_constraint():
    corrector = DualFootFootholdCorrector()
    offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    unchanged, decision = corrector.update(
        _state(),
        evidence={
            "left": _support_evidence(
                "left", FootSupportState.UNCERTAIN,
                confidence=0.9, prior_held=True,
            ),
            "right": _support_evidence(
                "right", FootSupportState.SWING_CONFIRMED
            ),
        },
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps={side: np.zeros(3) for side in offsets},
    )
    assert decision.active_sides == decision.constrained_sides == ()
    assert decision.replay_operator is None
    np.testing.assert_array_equal(unchanged.vector, _state().vector)


def test_existing_prior_held_foothold_without_detector_owner_is_fail_closed():
    corrector = DualFootFootholdCorrector()
    offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    velocities = {side: np.zeros(3) for side in offsets}
    entered, _ = corrector.update(
        _state(),
        evidence={
            "left": _support_evidence(
                "left", FootSupportState.STANCE_CONFIRMED, confidence=0.8
            ),
            "right": _support_evidence(
                "right", FootSupportState.SWING_CONFIRMED
            ),
        },
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    foothold = corrector.footholds_world_m()["left"].copy()
    moved = RootState(
        1.1,
        entered.vector + np.r_[[0.03, 0.0, 0.0], np.zeros(6)],
        entered.covariance,
    )
    unchanged, decision = corrector.update(
        moved,
        evidence={
            "left": _support_evidence(
                "left", FootSupportState.UNCERTAIN,
                confidence=0.8, prior_held=True,
                prior_held_support_confidence=None,
            ),
            "right": _support_evidence(
                "right", FootSupportState.SWING_CONFIRMED
            ),
        },
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    assert decision.active_sides == ("left",)
    assert decision.constrained_sides == ()
    assert decision.replay_operator is None
    assert corrector.prior_held_support_confidence() == {}
    np.testing.assert_array_equal(
        corrector.footholds_world_m()["left"], foothold
    )
    np.testing.assert_array_equal(unchanged.vector, moved.vector)


def test_dual_prior_held_selector_uses_only_detector_frozen_confidence():
    corrector = DualFootFootholdCorrector()
    initial_offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    zero_velocity = {side: np.zeros(3) for side in initial_offsets}
    state, _ = corrector.update(
        _state(velocity=(0.0, 0.0, 0.0)),
        evidence={
            "left": _support_evidence(
                "left", FootSupportState.STANCE_CONFIRMED, confidence=0.7
            ),
            "right": _support_evidence(
                "right", FootSupportState.STANCE_CONFIRMED, confidence=0.8
            ),
        },
        ankle_offset_world_m=initial_offsets,
        ankle_offset_velocity_world_mps=zero_velocity,
    )
    offsets = {
        "left": initial_offsets["left"] + np.array([0.018, 0.0, 0.0]),
        "right": initial_offsets["right"] - np.array([0.012, 0.0, 0.0]),
    }
    velocities = {
        "left": np.array([0.02, -0.01, 0.0]),
        "right": np.array([-0.03, 0.04, 0.0]),
    }
    # Frozen owners observed in the sealed v7 continuity-failure interval.
    frozen = {
        "left": 0.25713831260999137,
        "right": 0.2585864345994572,
    }

    def prior_evidence(left_instantaneous, right_instantaneous):
        return {
            "left": _support_evidence(
                "left", FootSupportState.UNCERTAIN,
                confidence=left_instantaneous, prior_held=True,
                prior_held_support_confidence=frozen["left"],
            ),
            "right": _support_evidence(
                "right", FootSupportState.UNCERTAIN,
                confidence=right_instantaneous, prior_held=True,
                prior_held_support_confidence=frozen["right"],
            ),
        }

    _first_state, first = corrector.update(
        state,
        evidence=prior_evidence(0.0, 0.9),
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    _second_state, second = corrector.update(
        state,
        evidence=prior_evidence(0.95, 0.01),
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=velocities,
    )
    assert corrector.prior_held_support_confidence() == frozen
    assert first.replay_operator is not None
    assert second.replay_operator is not None
    assert first.constrained_sides == second.constrained_sides == (
        "left", "right"
    )
    np.testing.assert_allclose(
        first.replay_operator.position_target_m,
        second.replay_operator.position_target_m,
    )
    np.testing.assert_allclose(
        first.replay_operator.velocity_target_mps,
        second.replay_operator.velocity_target_mps,
    )
    assert first.replay_operator.confidence == pytest.approx(
        second.replay_operator.confidence
    )


def test_selector_owner_is_shared_by_update_reconcile_and_pure_queries():
    corrector = DualFootFootholdCorrector()
    evidence = {
        "left": _support_evidence(
            "left", FootSupportState.STANCE_CONFIRMED, confidence=0.4
        ),
        "right": _support_evidence(
            "right", FootSupportState.STANCE_CONFIRMED, confidence=0.8
        ),
    }
    old_offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    zero_velocity = {side: np.zeros(3) for side in old_offsets}
    entered, _ = corrector.update(
        _state(velocity=(0.0, 0.0, 0.0)),
        evidence=evidence,
        ankle_offset_world_m=old_offsets,
        ankle_offset_velocity_world_mps=zero_velocity,
    )
    assert corrector.primary_side == "right"
    current_offsets = {
        "left": old_offsets["left"] + np.array([0.02, 0.0, 0.0]),
        "right": old_offsets["right"] - np.array([0.01, 0.0, 0.0]),
    }
    footholds = corrector.footholds_world_m()
    target = corrector.root_target_for_owned_footholds(
        entered.position_m,
        owned_footholds_world_m=footholds,
        evidence=evidence,
        ankle_offset_world_m=current_offsets,
        primary_side_at_measurement="right",
    )
    reconciled, _ = corrector.reconcile_pose_change(
        entered,
        evidence=evidence,
        previous_ankle_offset_world_m=old_offsets,
        previous_ankle_offset_velocity_world_mps=zero_velocity,
        ankle_offset_world_m=current_offsets,
        ankle_offset_velocity_world_mps=zero_velocity,
    )
    reexpressed = corrector.reexpress_root_between_pose_gauges(
        entered.position_m,
        owned_footholds_world_m=footholds,
        evidence=evidence,
        source_ankle_offset_world_m=old_offsets,
        target_ankle_offset_world_m=current_offsets,
        primary_side_at_measurement="right",
    )
    _updated, update = corrector.update(
        entered,
        evidence=evidence,
        ankle_offset_world_m=current_offsets,
        ankle_offset_velocity_world_mps=zero_velocity,
    )
    np.testing.assert_allclose(reconciled.position_m, target.root_position_m)
    np.testing.assert_allclose(reexpressed.root_position_m, target.root_position_m)
    assert update.replay_operator is not None
    np.testing.assert_allclose(
        update.replay_operator.position_target_m[:2],
        target.root_position_m[:2],
    )
