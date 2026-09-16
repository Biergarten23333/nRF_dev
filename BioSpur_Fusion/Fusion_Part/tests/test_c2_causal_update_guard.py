from __future__ import annotations

from dataclasses import MISSING
from dataclasses import replace
import json

import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.causal_update_guard import (
    CandidateKind,
    CandidateTransition,
    CausalContactTransitionEvidence,
    CausalImuActivitySummary,
    IndependentNodeConsensusEvidence,
    InnovationIntegrityEvidence,
    PublicHingeKinematics,
    ReachabilityClass,
    ReachabilityEnvelope,
    RootKinematicState,
    TransitionDisposition,
    TransitionReason,
    evaluate_candidate_transition,
)


_PROVENANCE = "TEST_FIXTURE_ONLY:synthetic-qualified-envelope-v1"


def _envelope(
    reachability_class: ReachabilityClass = ReachabilityClass.NOMINAL,
    **changes: object,
) -> ReachabilityEnvelope:
    values: dict[str, object] = {
        "reachability_class": reachability_class,
        "maximum_root_displacement_m": 0.10 if reachability_class is ReachabilityClass.NOMINAL else 0.80,
        "maximum_root_speed_change_mps": 0.20 if reachability_class is ReachabilityClass.NOMINAL else 4.0,
        "maximum_root_implied_acceleration_mps2": 2.0 if reachability_class is ReachabilityClass.NOMINAL else 40.0,
        "maximum_joint_step_rad": 0.08 if reachability_class is ReachabilityClass.NOMINAL else 0.80,
        "maximum_joint_angular_velocity_rad_s": 1.0 if reachability_class is ReachabilityClass.NOMINAL else 12.0,
        "maximum_joint_angular_acceleration_rad_s2": 8.0 if reachability_class is ReachabilityClass.NOMINAL else 80.0,
        "maximum_evidence_age_s": 0.025,
        "minimum_impulse_mps": 0.40,
        "minimum_angular_rate_rad_s": 1.5,
        "minimum_activity_persistence_s": 0.020,
        "minimum_unique_nodes": 2,
        "maximum_node_root_spread_m": 0.12,
        "maximum_node_geometry_condition": 100.0,
        "provenance": _PROVENANCE,
    }
    values.update(changes)
    return ReachabilityEnvelope(**values)  # type: ignore[arg-type]


def _state(
    *,
    time_s: float = 10.1,
    position: tuple[float, float, float] = (0.0, 0.0, 0.0),
    velocity: tuple[float, float, float] = (0.0, 0.0, 0.0),
    covariance: np.ndarray | None = None,
) -> RootKinematicState:
    return RootKinematicState(
        time_s=time_s,
        position_m=np.asarray(position),
        velocity_mps=np.asarray(velocity),
        covariance=np.eye(9) if covariance is None else covariance,
    )


def _hinge(**changes: object) -> PublicHingeKinematics:
    values: dict[str, object] = {
        "source_time_s": 9.995,
        "joint_ids": ("elbow_left", "elbow_right", "knee_left", "knee_right"),
        "joint_step_rad": np.asarray((0.02, 0.03, 0.02, 0.03)),
        "joint_angular_velocity_rad_s": np.asarray((0.2, 0.3, 0.2, 0.3)),
        "joint_angular_acceleration_rad_s2": np.asarray((1.0, 1.5, 1.0, 1.5)),
        "rom_valid": True,
        "fk_valid": True,
        "owner": "biospur_fusion.c2_articulated_biomechanics",
        "provenance": _PROVENANCE,
    }
    values.update(changes)
    return PublicHingeKinematics(**values)  # type: ignore[arg-type]


def _integrity(**changes: object) -> InnovationIntegrityEvidence:
    values: dict[str, object] = {
        "measurement_time_s": 10.0,
        "nis": 1.0,
        "nis_limit": 10.0,
        "measurement_integrity_valid": True,
        "covariance_model_valid": True,
        "provenance": _PROVENANCE,
    }
    values.update(changes)
    return InnovationIntegrityEvidence(**values)  # type: ignore[arg-type]


def _proposal(
    *,
    position: tuple[float, float, float] = (0.05, 0.0, 0.0),
    velocity: tuple[float, float, float] = (0.10, 0.0, 0.0),
    kind: CandidateKind = CandidateKind.ROOT_POSITION,
    hinge: PublicHingeKinematics | None = None,
    integrity: InnovationIntegrityEvidence | None = None,
    covariance: np.ndarray | None = None,
) -> CandidateTransition:
    return CandidateTransition(
        kind=kind,
        measurement_time_s=10.0,
        availability_time_s=10.1,
        previous_published_time_s=10.0,
        previous_published_state=_state(time_s=10.0),
        imu_prediction=_state(),
        measurement_candidate=_state(
            position=position, velocity=velocity, covariance=covariance
        ),
        hinge=hinge,
        integrity=integrity,
    )


def _activity(
    nodes: tuple[str, ...] = ("BSF0001", "BSF0002"),
    **changes: object,
) -> CausalImuActivitySummary:
    count = len(nodes)
    values: dict[str, object] = {
        "measurement_time_s": 10.0,
        "availability_time_s": 10.1,
        "window_start_time_s": 9.95,
        "node_ids": nodes,
        "latest_sample_time_s": np.full(count, 9.995),
        "impulse_mps": np.full(count, 0.6),
        "angular_rate_rad_s": np.full(count, 2.0),
        "persistence_s": np.full(count, 0.03),
        "provenance": _PROVENANCE,
    }
    values.update(changes)
    return CausalImuActivitySummary(**values)  # type: ignore[arg-type]


def test_candidate_numeric_horizon_may_stop_between_measurement_and_availability():
    proposal = _proposal()
    prediction = _state(time_s=10.0)
    candidate = _state(
        time_s=10.0, position=(0.05, 0.0, 0.0),
        velocity=(0.10, 0.0, 0.0),
    )
    decision = evaluate_candidate_transition(
        replace(
            proposal, imu_prediction=prediction,
            measurement_candidate=candidate,
        ),
        nominal_envelope=_envelope(),
    )
    assert decision.accepted

    mismatch = evaluate_candidate_transition(
        replace(
            proposal, imu_prediction=prediction,
            measurement_candidate=replace(candidate, time_s=10.01),
        ),
        nominal_envelope=_envelope(),
    )
    assert "CANDIDATE_EPOCH_MISMATCH" in mismatch.subreasons

    outside = evaluate_candidate_transition(
        replace(
            proposal, imu_prediction=_state(time_s=10.100001),
            measurement_candidate=_state(time_s=10.100001),
        ),
        nominal_envelope=_envelope(),
    )
    assert "PREDICTION_EPOCH_MISMATCH" in outside.subreasons


def _consensus(
    nodes: tuple[str, ...] = ("BSF0001", "BSF0002"),
    **changes: object,
) -> IndependentNodeConsensusEvidence:
    count = len(nodes)
    values: dict[str, object] = {
        "measurement_time_s": 10.0,
        "node_ids": nodes,
        "root_position_m": np.asarray(
            [(0.45 + 0.01 * index, 0.0, 0.0) for index in range(count)]
        ),
        "geometry_rank": np.full(count, 3, dtype=np.int64),
        "geometry_condition": np.full(count, 4.0),
        "computed_before_joint_candidate": True,
        "provenance": _PROVENANCE,
    }
    values.update(changes)
    return IndependentNodeConsensusEvidence(**values)  # type: ignore[arg-type]


def _contact(**changes: object) -> CausalContactTransitionEvidence:
    values: dict[str, object] = {
        "time_s": 10.1,
        "previous_state": {"left": "STANCE_CONFIRMED", "right": "STANCE_CONFIRMED"},
        "current_state": {"left": "SWING_CONFIRMED", "right": "STANCE_CONFIRMED"},
        "released_sides": ("left",),
        "swing_sides": ("left",),
        "provenance": _PROVENANCE,
    }
    values.update(changes)
    return CausalContactTransitionEvidence(**values)  # type: ignore[arg-type]


def _dynamic_decision(
    proposal: CandidateTransition | None = None,
    **changes: object,
):
    arguments: dict[str, object] = {
        "proposal": _proposal(position=(0.45, 0.0, 0.0), velocity=(1.0, 0.0, 0.0)),
        "nominal_envelope": _envelope(),
        "dynamic_envelope": _envelope(ReachabilityClass.DYNAMIC_FALL),
        "activity": _activity(),
        "consensus": _consensus(),
        "contact": _contact(),
    }
    if proposal is not None:
        arguments["proposal"] = proposal
    arguments.update(changes)
    actual_proposal = arguments.pop("proposal")
    return evaluate_candidate_transition(actual_proposal, **arguments)


def test_ordinary_root_candidate_accepts_without_pose_evidence() -> None:
    decision = evaluate_candidate_transition(
        _proposal(), nominal_envelope=_envelope()
    )
    assert decision.accepted
    assert decision.reason is TransitionReason.ACCEPT_NOMINAL
    assert decision.selected_state is not decision.metrics
    assert np.array_equal(decision.selected_state.position_m, (0.05, 0.0, 0.0))


def test_articulated_candidate_requires_public_hinge_rom_fk_owner() -> None:
    missing = evaluate_candidate_transition(
        _proposal(kind=CandidateKind.ARTICULATED_IK), nominal_envelope=_envelope()
    )
    rom = evaluate_candidate_transition(
        _proposal(kind=CandidateKind.ARTICULATED_IK, hinge=_hinge(rom_valid=False), integrity=_integrity()),
        nominal_envelope=_envelope(),
    )
    fk = evaluate_candidate_transition(
        _proposal(kind=CandidateKind.ARTICULATED_IK, hinge=_hinge(fk_valid=False), integrity=_integrity()),
        nominal_envelope=_envelope(),
    )
    assert "ARTICULATED_CANDIDATE_LACKS_PUBLIC_HINGE_EVIDENCE" in missing.subreasons
    assert "ROM_INVALID" in rom.subreasons
    assert "FK_INVALID" in fk.subreasons
    assert all(not item.accepted for item in (missing, rom, fk))


def test_root_rejects_hinge_and_public_hinge_rejects_free_hip() -> None:
    root = evaluate_candidate_transition(
        _proposal(hinge=_hinge()), nominal_envelope=_envelope()
    )
    hip = evaluate_candidate_transition(
        _proposal(
            kind=CandidateKind.ARTICULATED_IK,
            hinge=_hinge(joint_ids=("hip_left", "elbow_right", "knee_left", "knee_right")),
            integrity=_integrity(),
        ),
        nominal_envelope=_envelope(),
    )
    assert "ROOT_POSITION_CANDIDATE_HAS_HINGE_PAYLOAD" in root.subreasons
    assert "PUBLIC_HINGE_INVENTORY_INVALID" in hip.subreasons


def test_previous_publication_owns_transition_interval() -> None:
    forged = replace(_proposal(), previous_published_time_s=10.09)
    mismatch = replace(
        _proposal(), previous_published_state=_state(time_s=9.9)
    )
    assert "PREVIOUS_PUBLICATION_EPOCH_MISMATCH" in evaluate_candidate_transition(
        forged, nominal_envelope=_envelope()
    ).subreasons
    assert "PREVIOUS_PUBLICATION_EPOCH_MISMATCH" in evaluate_candidate_transition(
        mismatch, nominal_envelope=_envelope()
    ).subreasons


def test_fully_corroborated_dynamic_transition_accepts_only_qualified_envelope() -> None:
    decision = _dynamic_decision()
    missing = _dynamic_decision(dynamic_envelope=None)
    blank = _dynamic_decision(
        dynamic_envelope=_envelope(ReachabilityClass.DYNAMIC_FALL, provenance="")
    )
    assert decision.accepted
    assert decision.reason is TransitionReason.ACCEPT_CORROBORATED_DYNAMIC
    assert decision.metrics["corroborated_unique_node_count"] == 2
    assert missing.reason is TransitionReason.REJECT_REACHABILITY_UNQUALIFIED
    assert blank.reason is TransitionReason.REJECT_REACHABILITY_UNQUALIFIED


def test_quiet_contact_teleport_rejects_and_selects_prediction() -> None:
    decision = _dynamic_decision(
        activity=_activity(impulse_mps=np.zeros(2), angular_rate_rad_s=np.zeros(2)),
        contact=_contact(
            current_state={"left": "STANCE_CONFIRMED", "right": "STANCE_CONFIRMED"},
            released_sides=(),
            swing_sides=(),
        ),
    )
    assert not decision.accepted
    assert decision.disposition is TransitionDisposition.REJECT_USE_IMU_PREDICTION
    assert decision.reason is TransitionReason.REJECT_CONSENSUS_INSUFFICIENT
    assert decision.selected_state.position_m.tobytes() == _state().position_m.tobytes()


def test_single_node_activity_does_not_create_consensus() -> None:
    decision = _dynamic_decision(
        activity=_activity(("BSF0001",)), consensus=_consensus(("BSF0001",))
    )
    assert decision.reason is TransitionReason.REJECT_CONSENSUS_INSUFFICIENT
    assert decision.metrics["corroborated_unique_node_count"] == 1


def test_consensus_without_motion_or_contact_rejects() -> None:
    decision = _dynamic_decision(
        activity=_activity(persistence_s=np.zeros(2)),
        contact=_contact(
            current_state={"left": "STANCE_CONFIRMED", "right": "STANCE_CONFIRMED"},
            released_sides=(),
            swing_sides=(),
        ),
    )
    assert decision.reason is TransitionReason.REJECT_CONSENSUS_INSUFFICIENT
    assert "CONTACT_ACTUAL_RELEASE_MISSING" in decision.subreasons


def test_already_swing_is_not_a_new_release() -> None:
    decision = _dynamic_decision(
        contact=_contact(
            previous_state={"left": "SWING_CONFIRMED", "right": "STANCE_CONFIRMED"},
            current_state={"left": "SWING_CONFIRMED", "right": "STANCE_CONFIRMED"},
            released_sides=("left",),
        )
    )
    assert not decision.accepted
    assert "CONTACT_RELEASE_TRANSITION_MISMATCH" in decision.subreasons
    assert "CONTACT_ACTUAL_RELEASE_MISSING" in decision.subreasons


@pytest.mark.parametrize(
    "persistence",
    (np.asarray((-0.01, 0.03)), np.asarray((0.06, 0.03))),
)
def test_negative_or_excess_persistence_rejects(persistence: np.ndarray) -> None:
    decision = _dynamic_decision(activity=_activity(persistence_s=persistence))
    assert not decision.accepted
    assert any(
        reason in decision.subreasons
        for reason in ("ACTIVITY_VALUE_NEGATIVE", "ACTIVITY_PERSISTENCE_OUTSIDE_WINDOW")
    )


@pytest.mark.parametrize(
    ("activity", "consensus", "subreason"),
    (
        (_activity(("BSF0001", "BSF0001")), _consensus(), "ACTIVITY_NODE_ID_INVALID"),
        (_activity(), _consensus(("BSF0001", "BSF0001")), "CONSENSUS_NODE_ID_INVALID"),
        (
            _activity(),
            _consensus(geometry_rank=np.asarray((3, 2))),
            "CONSENSUS_GEOMETRY_DEGENERATE",
        ),
        (
            _activity(),
            _consensus(geometry_condition=np.asarray((4.0, 101.0))),
            "CONSENSUS_GEOMETRY_DEGENERATE",
        ),
    ),
)
def test_duplicate_and_degenerate_consensus_fail_closed(
    activity: CausalImuActivitySummary,
    consensus: IndependentNodeConsensusEvidence,
    subreason: str,
) -> None:
    decision = _dynamic_decision(activity=activity, consensus=consensus)
    assert not decision.accepted
    assert subreason in decision.subreasons


def test_runtime_domains_and_blank_node_ids_fail_closed() -> None:
    forged = replace(_proposal(), kind="FREE_HIP")  # type: ignore[arg-type]
    forged_decision = evaluate_candidate_transition(forged, nominal_envelope=_envelope())
    blank_decision = _dynamic_decision(
        activity=_activity(("BSF0001", " ")),
        consensus=_consensus(("BSF0001", " ")),
    )
    assert "INVALID_CANDIDATE_KIND" in forged_decision.subreasons
    assert "ACTIVITY_NODE_ID_INVALID" in blank_decision.subreasons
    assert "CONSENSUS_NODE_ID_INVALID" in blank_decision.subreasons
    assert not forged_decision.accepted and not blank_decision.accepted


def test_negative_consensus_condition_rejects() -> None:
    decision = _dynamic_decision(
        consensus=_consensus(geometry_condition=np.asarray((4.0, -1.0)))
    )
    assert not decision.accepted
    assert "CONSENSUS_GEOMETRY_CONDITION_INVALID" in decision.subreasons


@pytest.mark.parametrize(
    "rank",
    (
        np.asarray((3.0, np.nan)),
        np.asarray((3.0, np.inf)),
        np.asarray((3.0, -np.inf)),
        np.asarray((3.0, 2.5)),
        np.asarray(("3", "3"), dtype=object),
        np.asarray((True, True)),
        np.asarray((3, [2]), dtype=object),
    ),
)
def test_invalid_geometry_rank_domains_reject_without_throw(rank: np.ndarray) -> None:
    decision = _dynamic_decision(consensus=_consensus(geometry_rank=rank))
    assert decision.reason is TransitionReason.REJECT_CAUSAL_EVIDENCE_INVALID
    assert "CONSENSUS_GEOMETRY_RANK_NOT_INTEGER" in decision.subreasons


@pytest.mark.parametrize("condition", (np.nan, np.inf, -np.inf))
def test_nonfinite_geometry_condition_rejects_without_throw(condition: float) -> None:
    decision = _dynamic_decision(
        consensus=_consensus(geometry_condition=np.asarray((4.0, condition)))
    )
    assert decision.reason is TransitionReason.REJECT_CAUSAL_EVIDENCE_INVALID
    assert "CONSENSUS_GEOMETRY_CONDITION_INVALID" in decision.subreasons


def test_inactive_consensus_outlier_does_not_change_corroborating_subset() -> None:
    nodes = ("BSF0001", "BSF0002", "BSF0003")
    activity = _activity(
        nodes,
        impulse_mps=np.asarray((0.6, 0.6, 0.0)),
        angular_rate_rad_s=np.asarray((2.0, 2.0, 0.0)),
        persistence_s=np.asarray((0.03, 0.03, 0.0)),
    )
    consensus = _consensus(
        nodes,
        root_position_m=np.asarray(
            ((0.45, 0.0, 0.0), (0.46, 0.0, 0.0), (100.0, 0.0, 0.0))
        ),
    )
    decision = _dynamic_decision(activity=activity, consensus=consensus)
    assert decision.accepted
    assert decision.metrics["corroborated_unique_node_count"] == 2
    assert decision.metrics["independent_node_root_spread_m"] < 0.01


@pytest.mark.parametrize(
    ("changes", "subreason"),
    (
        ({"latest_sample_time_s": np.asarray((10.001, 9.995))}, "ACTIVITY_TIME_INVALID_OR_FUTURE"),
        ({"latest_sample_time_s": np.asarray((9.970, 9.995))}, "ACTIVITY_EVIDENCE_STALE_OR_FUTURE"),
        ({"window_start_time_s": 10.01}, "ACTIVITY_WINDOW_REVERSED"),
        ({"measurement_time_s": 10.001}, "ACTIVITY_MEASUREMENT_EPOCH_MISMATCH"),
    ),
)
def test_future_stale_mixed_and_reversed_activity_rejects(
    changes: dict[str, object], subreason: str
) -> None:
    decision = _dynamic_decision(activity=_activity(**changes))
    assert not decision.accepted
    assert subreason in decision.subreasons


@pytest.mark.parametrize(
    ("position", "velocity", "expected"),
    (
            ((0.81, 0.0, 0.0), (1.0, 0.0, 0.0), "DYNAMIC_FALL_ROOT_DISPLACEMENT_M_EXCEEDED"),
            ((0.45, 0.0, 0.0), (4.1, 0.0, 0.0), "DYNAMIC_FALL_ROOT_SPEED_CHANGE_MPS_EXCEEDED"),
            ((0.45, 0.0, 0.0), (4.01, 0.0, 0.0), "DYNAMIC_FALL_ROOT_IMPLIED_ACCELERATION_MPS2_EXCEEDED"),
    ),
)
def test_root_reachability_metrics_are_independently_gated(
    position: tuple[float, float, float],
    velocity: tuple[float, float, float],
    expected: str,
) -> None:
    decision = _dynamic_decision(proposal=_proposal(position=position, velocity=velocity))
    assert decision.reason is TransitionReason.REJECT_UNREACHABLE_TRANSITION
    assert expected in decision.subreasons


@pytest.mark.parametrize(
    ("hinge_change", "expected"),
    (
            ({"joint_step_rad": np.asarray((0.81, 0.1, 0.1, 0.1))}, "DYNAMIC_FALL_JOINT_STEP_MAXIMUM_RAD_EXCEEDED"),
            ({"joint_angular_velocity_rad_s": np.asarray((12.1, 1.0, 1.0, 1.0))}, "DYNAMIC_FALL_JOINT_ANGULAR_VELOCITY_MAXIMUM_RAD_S_EXCEEDED"),
            ({"joint_angular_acceleration_rad_s2": np.asarray((80.1, 1.0, 1.0, 1.0))}, "DYNAMIC_FALL_JOINT_ANGULAR_ACCELERATION_MAXIMUM_RAD_S2_EXCEEDED"),
    ),
)
def test_joint_reachability_metrics_are_independently_gated(
    hinge_change: dict[str, object], expected: str
) -> None:
    proposal = _proposal(
        kind=CandidateKind.ARTICULATED_IK,
        hinge=_hinge(**hinge_change),
        integrity=_integrity(),
        position=(0.45, 0.0, 0.0),
        velocity=(1.0, 0.0, 0.0),
    )
    decision = _dynamic_decision(proposal=proposal)
    assert decision.reason is TransitionReason.REJECT_UNREACHABLE_TRANSITION
    assert expected in decision.subreasons


def test_covariance_and_integrity_failures_cannot_be_bypassed() -> None:
    bad_covariance = np.eye(9)
    bad_covariance[0, 0] = -1.0
    covariance = _dynamic_decision(
        proposal=_proposal(
            position=(0.45, 0.0, 0.0),
            velocity=(1.0, 0.0, 0.0),
            covariance=bad_covariance,
        )
    )
    integrity = _dynamic_decision(
        proposal=_proposal(
            position=(0.45, 0.0, 0.0),
            velocity=(1.0, 0.0, 0.0),
            integrity=InnovationIntegrityEvidence(
                measurement_time_s=10.0,
                nis=11.0,
                nis_limit=10.0,
                measurement_integrity_valid=True,
                covariance_model_valid=True,
                provenance=_PROVENANCE,
            ),
        )
    )
    assert "CANDIDATE_COVARIANCE_INVALID" in covariance.subreasons
    assert "NIS_LIMIT_EXCEEDED" in integrity.subreasons
    assert not covariance.accepted and not integrity.accepted


def test_missing_or_blank_envelope_provenance_rejects() -> None:
    missing = evaluate_candidate_transition(_proposal(), nominal_envelope=None)
    blank = evaluate_candidate_transition(
        _proposal(), nominal_envelope=_envelope(provenance=" ")
    )
    assert missing.reason is TransitionReason.REJECT_REACHABILITY_UNQUALIFIED
    assert blank.reason is TransitionReason.REJECT_REACHABILITY_UNQUALIFIED


def test_contact_lifecycle_is_consumed_without_recomputation() -> None:
    evidence = _contact(
        released_sides=("right",),
        swing_sides=("right",),
        current_state={"left": "STANCE_CONFIRMED", "right": "SWING_CONFIRMED"},
    )
    before = (dict(evidence.previous_state), dict(evidence.current_state))
    decision = _dynamic_decision(contact=evidence)
    assert decision.accepted
    assert before == (dict(evidence.previous_state), dict(evidence.current_state))
    with pytest.raises(TypeError):
        evidence.current_state["right"] = "STANCE_CONFIRMED"  # type: ignore[index]


def test_aliases_are_deeply_immutable_and_decision_is_idempotent() -> None:
    position = np.asarray((0.05, 0.0, 0.0))
    roots = np.asarray(((0.45, 0.0, 0.0), (0.46, 0.0, 0.0)))
    proposal = _proposal(position=tuple(position))
    consensus = _consensus(root_position_m=roots)
    position[:] = 99.0
    roots[:] = 99.0
    before = (
        proposal.measurement_candidate.position_m.tobytes(),
        consensus.root_position_m.tobytes(),
        proposal.measurement_candidate.covariance.tobytes(),
    )
    first = _dynamic_decision(
        proposal=replace(
            proposal,
            measurement_candidate=_state(position=(0.45, 0.0, 0.0), velocity=(1.0, 0.0, 0.0)),
        ),
        consensus=consensus,
    )
    second = _dynamic_decision(
        proposal=replace(
            proposal,
            measurement_candidate=_state(position=(0.45, 0.0, 0.0), velocity=(1.0, 0.0, 0.0)),
        ),
        consensus=consensus,
    )
    after = (
        proposal.measurement_candidate.position_m.tobytes(),
        consensus.root_position_m.tobytes(),
        proposal.measurement_candidate.covariance.tobytes(),
    )
    assert before == after
    assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(second.to_dict(), sort_keys=True)
    with pytest.raises(ValueError):
        consensus.root_position_m[0, 0] = 0.0


def test_all_dynamic_evidence_timestamps_are_availability_bounded() -> None:
    consensus = replace(_consensus(), measurement_time_s=10.2)
    contact = replace(_contact(), time_s=10.2)
    consensus_decision = _dynamic_decision(consensus=consensus)
    contact_decision = _dynamic_decision(contact=contact)
    assert "CONSENSUS_MEASUREMENT_EPOCH_MISMATCH" in consensus_decision.subreasons
    assert "CONTACT_AVAILABILITY_EPOCH_MISMATCH" in contact_decision.subreasons


def test_no_numeric_limit_has_a_dataclass_default() -> None:
    for field in ReachabilityEnvelope.__dataclass_fields__.values():
        if field.name == "reachability_class" or field.name == "provenance":
            continue
        assert field.default is MISSING
        assert field.default_factory is MISSING
