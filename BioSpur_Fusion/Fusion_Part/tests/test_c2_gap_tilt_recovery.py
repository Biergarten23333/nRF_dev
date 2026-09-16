from dataclasses import replace

import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.gap_tilt_recovery import (
    AuthoritativeGapEvidence,
    CausalTiltTrustStateMachine,
    GapTiltRecoveryConfig,
    GapTiltRecoveryController,
    MissingTiltDiagnosticIssuerBinding,
    TiltDiagnosticIssuerBinding,
    TiltEvidenceStatus,
    TiltTrustFrameEvidence,
    TiltTrustState,
)
from biospur_fusion.root_r3 import (
    CausalDelayedRootFilter,
    ImuSample,
    PositionObservation,
    RootFilterConfig,
    RootState,
    RootTranslationEdgeMode,
)


CLOCK = "a" * 64
SOURCE = "b" * 64
DIAGNOSTIC = "c" * 64
THRESHOLD = "d" * 64
MISSING_POLICY = "e" * 64


def _issuer():
    return TiltDiagnosticIssuerBinding(
        "pelvis", 7, "B306_TIMER2", CLOCK, SOURCE, DIAGNOSTIC,
        THRESHOLD, 0.05,
    )


def _missing_issuer():
    return MissingTiltDiagnosticIssuerBinding(
        "pelvis", 7, "B306_TIMER2", CLOCK, SOURCE, MISSING_POLICY,
    )


def _evidence(
    sequence,
    time_s,
    status=TiltEvidenceStatus.TRUSTED,
    span="s0",
    *,
    availability_time_s=None,
    event_identity=None,
):
    issuer = _issuer()
    missing = status is TiltEvidenceStatus.MISSING
    return TiltTrustFrameEvidence(
        f"event-{sequence}" if event_identity is None else event_identity,
        time_s,
        time_s if availability_time_s is None else availability_time_s,
        sequence,
        "pelvis", 7, span,
        "B306_TIMER2", CLOCK, SOURCE, status,
        None if missing else (0.01 if status is TiltEvidenceStatus.TRUSTED else 0.10),
        None if missing else issuer.maximum_trusted_tilt_error_rad,
        None if missing else DIAGNOSTIC,
        issuer.digest,
    )


def _sample(
    sequence, time_s, force=(2.0, 0.0, 9.80665), *, availability_time_s=None,
):
    return ImuSample(
        time_s,
        time_s if availability_time_s is None else availability_time_s,
        np.asarray(force),
        np.eye(3),
        sequence,
    )


def _missing_evidence(
    sequence, time_s, span="s0", *, availability_time_s=None, event_identity=None,
):
    issuer = _missing_issuer()
    return TiltTrustFrameEvidence(
        f"missing-event-{sequence}" if event_identity is None else event_identity,
        time_s,
        time_s if availability_time_s is None else availability_time_s,
        sequence,
        "pelvis",
        7,
        span,
        "B306_TIMER2",
        CLOCK,
        SOURCE,
        TiltEvidenceStatus.MISSING,
        None,
        None,
        None,
        issuer.digest,
    )


def _root():
    vector = np.zeros(9)
    vector[3] = 0.2
    return CausalDelayedRootFilter(
        RootState(0.0, vector, np.eye(9) * 0.1),
        RootFilterConfig(fixed_lag_s=2.0, nis_limit_3d=1e9),
        inertial=True,
    )


def _controller(required=3, initial_status=TiltEvidenceStatus.TRUSTED):
    root = _root()
    issuer = _missing_issuer() if initial_status is TiltEvidenceStatus.MISSING else _issuer()
    initial_evidence = (
        _missing_evidence(0, 0.0)
        if initial_status is TiltEvidenceStatus.MISSING
        else _evidence(0, 0.0, initial_status)
    )
    trust = CausalTiltTrustStateMachine(
        GapTiltRecoveryConfig(0.006, required),
        issuer,
        initial_evidence,
    )
    return GapTiltRecoveryController(root, trust)


def _gap(current_sequence=1, current_time=1.0, current_span="s1"):
    return AuthoritativeGapEvidence(
        "event-0", f"event-{current_sequence}", 0.0, current_time,
        0, current_sequence, "pelvis", 7, "s0", current_span,
        "B306_TIMER2", CLOCK, SOURCE,
    )


def _root_bytes(root):
    return (
        root.publication_token().digest,
        tuple((row.state.time_s, row.state.vector.tobytes(),
               row.state.covariance.tobytes(), None if row.incoming_edge is None
               else (row.incoming_edge.inertial, row.incoming_edge.input_owner))
              for row in root._snapshots),
        root.health_snapshot(), root._mode, root._publication_revision,
        root._last_force.tobytes(), root._last_rotation.tobytes(),
        root._following_input_mode, root._last_source_gap_owner,
        root.future_imu_count, root.future_uwb_count,
        root.preavailability_output_count, root.late_imu_rejected,
    )


def test_no_gap_uses_inertial_and_default_api_is_byte_compatible():
    implicit = _root()
    explicit = _root()
    sample = _sample(1, 0.005)
    assert implicit.add_imu(sample)
    assert explicit.add_imu(
        sample,
        edge_mode=RootTranslationEdgeMode.INERTIAL,
        following_input_mode=RootTranslationEdgeMode.INERTIAL,
    )
    np.testing.assert_array_equal(implicit.current_state.vector, explicit.current_state.vector)
    np.testing.assert_array_equal(implicit.current_state.covariance, explicit.current_state.covariance)
    controller = _controller()
    assert controller.add_imu(sample, _evidence(1, 0.005))
    assert controller.root._snapshots[-1].incoming_edge.inertial
    assert controller.trust.state is TiltTrustState.TRUSTED


def test_gap_is_explicit_no_update_then_cv_and_never_recovers_without_evidence():
    controller = _controller()
    before_vector = controller.root.current_state.vector.tobytes()
    before_covariance = controller.root.current_state.covariance.copy()
    assert controller.add_imu(
        _sample(1, 1.0), _evidence(1, 1.0, span="s1"), gap=_gap(),
    )
    assert controller.root.current_state.vector.tobytes() == before_vector
    assert np.trace(controller.root.current_state.covariance) > np.trace(before_covariance)
    assert controller.trust.state is TiltTrustState.UNTRUSTED
    assert controller.root._last_source_gap_owner == _gap().digest
    velocity = controller.root.current_state.velocity_mps.tobytes()
    for sequence in range(2, 8):
        time_s = 1.0 + 0.005 * (sequence - 1)
        assert controller.add_imu(
            _sample(sequence, time_s),
            _evidence(sequence, time_s, TiltEvidenceStatus.MISSING, "s1"),
        )
        assert not controller.root._snapshots[-1].incoming_edge.inertial
    assert controller.trust.state is TiltTrustState.UNTRUSTED
    assert controller.root.current_state.velocity_mps.tobytes() == velocity


def test_transition_modes_are_pre_and_post_evidence_causal():
    controller = _controller(required=3)
    assert controller.add_imu(
        _sample(1, 1.0), _evidence(1, 1.0, span="s1"), gap=_gap(),
    )
    assert controller.root._following_input_mode is RootTranslationEdgeMode.CV_NO_ACCELERATION
    assert controller.add_imu(_sample(2, 1.005), _evidence(2, 1.005, span="s1"))
    assert not controller.root._snapshots[-1].incoming_edge.inertial
    assert controller.add_imu(_sample(3, 1.010), _evidence(3, 1.010, span="s1"))
    assert not controller.root._snapshots[-1].incoming_edge.inertial
    assert controller.trust.state is TiltTrustState.TRUSTED
    assert controller.root._following_input_mode is RootTranslationEdgeMode.INERTIAL
    emitted = controller.root.emit(1.012)
    assert emitted.root_velocity_mps[0] > controller.root.current_state.velocity_mps[0]
    assert controller.add_imu(
        _sample(4, 1.015),
        _evidence(4, 1.015, TiltEvidenceStatus.UNTRUSTED, "s1"),
    )
    assert controller.root._snapshots[-1].incoming_edge.inertial
    assert controller.trust.state is TiltTrustState.UNTRUSTED
    assert controller.root._following_input_mode is RootTranslationEdgeMode.CV_NO_ACCELERATION
    emitted = controller.root.emit(1.017)
    np.testing.assert_array_equal(emitted.root_velocity_mps, controller.root.current_state.velocity_mps)


def test_delayed_position_update_replays_cv_edges_and_leaves_velocity_bias_exact():
    controller = _controller(required=20)
    replay = _controller(required=20)
    for item in (controller, replay):
        assert item.add_imu(
            _sample(1, 1.0), _evidence(1, 1.0, span="s1"), gap=_gap(),
        )
    for sequence in range(2, 9):
        t = 1.0 + 0.005 * (sequence - 1)
        for item in (controller, replay):
            assert item.add_imu(_sample(sequence, t), _evidence(sequence, t, span="s1"))
    before = controller.root.current_state
    observation = PositionObservation(
        1.0175, 1.037, before.position_m + np.array([0.02, 0.0, 0.0]),
        np.eye(3) * 0.02, "body", (0, 1, 2, 3), source_sequence=1,
    )
    decision = controller.root.add_position(
        observation, processing_time_s=1.037, state_update_indices=(0, 1, 2),
    )
    assert decision.accepted
    assert controller.root.current_state.vector[3:9].tobytes() == before.vector[3:9].tobytes()
    assert controller.root.current_state.covariance.tobytes() != replay.root.current_state.covariance.tobytes()
    assert all(
        row.incoming_edge is None or not row.incoming_edge.inertial
        for row in controller.root._snapshots
        if row.state.time_s > 1.0
    )
    for item in (controller, replay):
        assert item.add_imu(_sample(9, 1.040), _evidence(9, 1.040, span="s1"))
    assert controller.root.current_state.vector[:3].tobytes() != replay.root.current_state.vector[:3].tobytes()
    assert controller.root.current_state.vector[3:9].tobytes() == replay.root.current_state.vector[3:9].tobytes()
    assert controller.root.current_state.covariance.tobytes() != replay.root.current_state.covariance.tobytes()
    assert not controller.root._snapshots[-1].incoming_edge.inertial
    assert not replay.root._snapshots[-1].incoming_edge.inertial


@pytest.mark.parametrize("mutation", ["foreign", "stale", "missing_gap"])
def test_bad_evidence_and_missing_gap_are_exact_no_event(mutation):
    controller = _controller()
    root_before = _root_bytes(controller.root)
    trust_before = controller.trust.owner_bytes()
    evidence = _evidence(1, 0.005)
    gap = None
    if mutation == "foreign":
        evidence = replace(evidence, source_owner_digest="e" * 64, digest="")
    elif mutation == "stale":
        evidence = _evidence(0, 0.005)
    else:
        evidence = _evidence(1, 1.0, span="s1")
    with pytest.raises(ValueError):
        controller.add_imu(_sample(evidence.source_sequence, evidence.measurement_time_s), evidence, gap=gap)
    assert _root_bytes(controller.root) == root_before
    assert controller.trust.owner_bytes() == trust_before


def test_root_rejection_and_participant_failure_roll_back_both_owners(monkeypatch):
    controller = _controller()
    controller.root.advance_to_availability(0.006)
    root_before = _root_bytes(controller.root)
    trust_before = controller.trust.owner_bytes()
    assert not controller.add_imu(_sample(1, 0.005), _evidence(1, 0.005))
    assert _root_bytes(controller.root) == root_before
    assert controller.trust.owner_bytes() == trust_before
    controller = _controller()
    root_before = _root_bytes(controller.root)
    trust_before = controller.trust.owner_bytes()
    sample = _sample(1, 0.005)
    evidence = _evidence(1, 0.005)
    monkeypatch.setattr(controller.trust, "commit", lambda _plan: (_ for _ in ()).throw(RuntimeError("injected")))
    with pytest.raises(RuntimeError, match="injected"):
        controller.add_imu(sample, evidence)
    assert _root_bytes(controller.root) == root_before
    assert controller.trust.owner_bytes() == trust_before


def test_prepare_is_pure_stale_plan_fails_and_clone_is_value_equal_nonaliased():
    controller = _controller()
    sample = _sample(1, 0.005)
    evidence = _evidence(1, 0.005)
    before = controller.trust.owner_bytes()
    plan = controller.trust.prepare(sample, evidence)
    assert controller.trust.owner_bytes() == before
    clone = controller.trust.clone()
    assert clone.owner_bytes() == before
    controller.trust.commit(plan)
    assert clone.owner_bytes() == before
    with pytest.raises(RuntimeError, match="STALE"):
        controller.trust.commit(plan)


def test_mechanism_is_explicitly_not_production_ready_without_frontend_issuer():
    controller = _controller()
    assert not _issuer().production_ready
    assert not controller.trust.production_ready
    assert not controller.production_ready


def test_same_batch_sequence_reuse_and_equal_availability_are_chronological():
    controller = _controller()
    for time_s, event_identity in ((0.005, "batch-event-a"), (0.010, "batch-event-b")):
        sample = _sample(17, time_s, availability_time_s=0.020)
        evidence = _evidence(
            17,
            time_s,
            availability_time_s=0.020,
            event_identity=event_identity,
        )
        assert controller.add_imu(sample, evidence)
    assert controller.root.current_state.time_s == 0.010
    assert controller.trust._snapshot.event.event_identity == "batch-event-b"


def test_uint16_source_sequence_wrap_is_identity_not_chronology():
    controller = _controller()
    assert controller.add_imu(_sample(0xFFFF, 0.005), _evidence(0xFFFF, 0.005))
    assert controller.add_imu(_sample(0, 0.010), _evidence(0, 0.010))
    assert controller.trust._snapshot.event.source_sequence == 0
    for invalid_sequence in (-1, 0x10000):
        with pytest.raises(ValueError, match="invalid tilt-trust frame evidence"):
            _evidence(invalid_sequence, 0.015)


def test_availability_regression_is_exact_byte_inert():
    controller = _controller()
    assert controller.add_imu(
        _sample(3, 0.005, availability_time_s=0.020),
        _evidence(3, 0.005, availability_time_s=0.020),
    )
    root_before = _root_bytes(controller.root)
    trust_before = controller.trust.owner_bytes()
    with pytest.raises(ValueError, match="stale, foreign, or misassociated"):
        controller.add_imu(
            _sample(4, 0.010, availability_time_s=0.019),
            _evidence(4, 0.010, availability_time_s=0.019),
        )
    assert _root_bytes(controller.root) == root_before
    assert controller.trust.owner_bytes() == trust_before


def test_exact_event_identity_replay_is_exact_byte_inert():
    controller = _controller()
    assert controller.add_imu(
        _sample(5, 0.005),
        _evidence(5, 0.005, event_identity="immutable-event"),
    )
    root_before = _root_bytes(controller.root)
    trust_before = controller.trust.owner_bytes()
    with pytest.raises(ValueError, match="stale, foreign, or misassociated"):
        controller.add_imu(
            _sample(6, 0.010),
            _evidence(6, 0.010, event_identity="immutable-event"),
        )
    assert _root_bytes(controller.root) == root_before
    assert controller.trust.owner_bytes() == trust_before


def test_equal_batch_availability_preserves_delayed_root_integration_parity():
    batched = _controller(required=20)
    reference = _controller(required=20)
    for sequence, time_s in enumerate((0.005, 0.010, 0.015, 0.020), start=1):
        assert batched.add_imu(
            _sample(sequence, time_s, availability_time_s=0.020),
            _evidence(sequence, time_s, availability_time_s=0.020),
        )
        assert reference.add_imu(
            _sample(sequence, time_s),
            _evidence(sequence, time_s),
        )
    np.testing.assert_array_equal(
        batched.root.current_state.vector, reference.root.current_state.vector,
    )
    np.testing.assert_array_equal(
        batched.root.current_state.covariance, reference.root.current_state.covariance,
    )
    observation = PositionObservation(
        0.0125,
        0.020,
        batched.root.current_state.position_m + np.array([0.01, -0.01, 0.0]),
        np.eye(3) * 0.02,
        "body",
        (0, 1, 2, 3),
        source_sequence=9,
    )
    batched_decision = batched.root.add_position(
        observation, processing_time_s=0.020, state_update_indices=(0, 1, 2),
    )
    reference_decision = reference.root.add_position(
        observation, processing_time_s=0.020, state_update_indices=(0, 1, 2),
    )
    assert batched_decision.accepted and reference_decision.accepted
    np.testing.assert_array_equal(
        batched.root.current_state.vector, reference.root.current_state.vector,
    )
    np.testing.assert_array_equal(
        batched.root.current_state.covariance, reference.root.current_state.covariance,
    )
    assert batched.add_imu(
        _sample(5, 0.025, availability_time_s=0.025),
        _evidence(5, 0.025, availability_time_s=0.025),
    )
    assert reference.add_imu(_sample(5, 0.025), _evidence(5, 0.025))
    np.testing.assert_array_equal(
        batched.root.current_state.vector, reference.root.current_state.vector,
    )
    np.testing.assert_array_equal(
        batched.root.current_state.covariance, reference.root.current_state.covariance,
    )


def test_missing_initial_policy_is_untrusted_with_cv_following_mode():
    controller = _controller(initial_status=TiltEvidenceStatus.MISSING)
    assert controller.trust.state is TiltTrustState.UNTRUSTED
    assert controller.trust._snapshot.consecutive_trusted_frames == 0
    assert controller.root._following_input_mode is RootTranslationEdgeMode.CV_NO_ACCELERATION
    emitted = controller.root.emit(0.002)
    np.testing.assert_array_equal(
        emitted.root_velocity_mps, controller.root.current_state.velocity_mps,
    )


def test_missing_initial_keeps_position_only_uwb_available_and_vb_inert():
    controller = _controller(required=200, initial_status=TiltEvidenceStatus.MISSING)
    before = controller.root.current_state
    observation = PositionObservation(
        0.0,
        0.0,
        before.position_m + np.array([0.01, 0.0, 0.0]),
        np.eye(3) * 0.02,
        "body",
        (0, 1, 2, 3),
        source_sequence=1,
    )
    decision = controller.root.add_position(
        observation, state_update_indices=(0, 1, 2),
    )
    assert decision.accepted
    assert controller.root.current_state.vector[3:9].tobytes() == before.vector[3:9].tobytes()
    assert controller.add_imu(
        _sample(1, 0.005),
        _missing_evidence(1, 0.005),
    )
    assert not controller.root._snapshots[-1].incoming_edge.inertial
    assert controller.root.current_state.vector[3:9].tobytes() == before.vector[3:9].tobytes()


def test_missing_initial_misassociation_is_exact_root_and_trust_noop():
    controller = _controller(initial_status=TiltEvidenceStatus.MISSING)
    root_before = _root_bytes(controller.root)
    trust_before = controller.trust.owner_bytes()
    with pytest.raises(ValueError, match="stale, foreign, or misassociated"):
        controller.add_imu(
            _sample(2, 0.005),
            _missing_evidence(1, 0.005),
        )
    assert _root_bytes(controller.root) == root_before
    assert controller.trust.owner_bytes() == trust_before


def test_missing_issuer_has_no_threshold_and_rejects_verdict_byte_inertly():
    issuer = _missing_issuer()
    assert not hasattr(issuer, "maximum_trusted_tilt_error_rad")
    assert not hasattr(issuer, "diagnostic_owner_digest")
    controller = _controller(initial_status=TiltEvidenceStatus.MISSING)
    root_before = _root_bytes(controller.root)
    trust_before = controller.trust.owner_bytes()
    with pytest.raises(ValueError, match="bound diagnostic issuer"):
        controller.add_imu(_sample(1, 0.005), _evidence(1, 0.005))
    assert _root_bytes(controller.root) == root_before
    assert controller.trust.owner_bytes() == trust_before
    with pytest.raises(ValueError, match="missing-policy owner"):
        CausalTiltTrustStateMachine(
            GapTiltRecoveryConfig(0.006, 3),
            _issuer(),
            _evidence(0, 0.0, TiltEvidenceStatus.MISSING),
        )
