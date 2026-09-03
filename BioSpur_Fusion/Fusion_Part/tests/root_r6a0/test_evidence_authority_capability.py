from __future__ import annotations

from biospur_fusion.root_r6a0.authority import (
    AuthorityRouter,
    RecoveryTracker,
    capability_for_scenario,
    fault_scenario_results,
    group_hypotheses,
)
from biospur_fusion.root_r6a0.contracts import (
    AuthorityScope,
    CapabilityLevel,
    FaultDomain,
    HealthHypothesis,
    Informativeness,
    MeasurementHealth,
    ServiceDOF,
)


def test_raw_t4_and_raw_imu_m1_double_count_is_structurally_rejected(gates):
    assert gates["checks"]["10_dependent_evidence_double_count_impossible"]


def test_synthetic_evidence_has_one_active_factor_owner(scenario):
    assert scenario.ancestry_audit["pass"]
    assert scenario.ancestry_audit["duplicate_claim_count"] == 0
    assert scenario.ancestry_audit["maximum_active_factors_per_raw_event"] == 1


def test_health_informativeness_and_authority_are_distinct_fields():
    proposal = AuthorityRouter().route(
        proposal_id="weak_but_healthy", source_ids=("one",), requested_scope=AuthorityScope.LOCAL_SEGMENT_ONLY,
        measurement_health=MeasurementHealth.HEALTHY, informativeness=Informativeness.WEAK,
        hypotheses=(), target_blocks=("segment",), affected_service_dofs=(ServiceDOF.BODY_RELATIVE_POSE,),
        recovery_weight=1.0,
    )
    assert proposal.measurement_health is MeasurementHealth.HEALTHY
    assert proposal.informativeness is Informativeness.WEAK
    assert proposal.granted_scope is AuthorityScope.LOCAL_SEGMENT_ONLY
    assert not proposal.production_authorized


def test_one_source_cannot_gain_protected_authority():
    proposal = AuthorityRouter().route(
        proposal_id="one_link", source_ids=("one",), requested_scope=AuthorityScope.COMMON_YAW_ELIGIBLE,
        measurement_health=MeasurementHealth.HEALTHY, informativeness=Informativeness.INFORMATIVE,
        hypotheses=(), target_blocks=("root_yaw",), affected_service_dofs=(ServiceDOF.GLOBAL_YAW,),
        independent_tags=1, independent_anchors=1, recovery_weight=1.0,
    )
    assert proposal.granted_scope not in (AuthorityScope.COMMON_YAW_ELIGIBLE, AuthorityScope.ROOT_TRANSLATION_ELIGIBLE)
    assert not proposal.production_authorized


def test_correlated_dirty_links_are_one_common_cause_group():
    hypotheses = tuple(HealthHypothesis(
        f"h{index}", FaultDomain.TAG_ANCHOR_LINK, (f"link{index}",), MeasurementHealth.SUSPECT,
        (f"event{index}",), 0.8, "body_shadow",
    ) for index in range(5))
    assert group_hypotheses(hypotheses) == {"body_shadow": tuple(f"h{index}" for index in range(5))}
    proposal = AuthorityRouter().route(
        proposal_id="correlated", source_ids=tuple(f"link{index}" for index in range(5)),
        requested_scope=AuthorityScope.ROOT_TRANSLATION_ELIGIBLE,
        measurement_health=MeasurementHealth.SUSPECT, informativeness=Informativeness.INFORMATIVE,
        hypotheses=hypotheses, target_blocks=("root",),
        affected_service_dofs=(ServiceDOF.GLOBAL_POSITION,), independent_tags=5,
        independent_anchors=5, recovery_weight=1.0,
    )
    assert proposal.granted_scope is AuthorityScope.COMMON_CAUSE_FREEZE


def test_all_uwb_blackout_preserves_relative_pose_and_degrades_global():
    capability = capability_for_scenario("all_uwb_blackout")
    assert capability[ServiceDOF.BODY_RELATIVE_POSE].level is CapabilityLevel.MULTI_SENSOR_SUPPORTED
    assert capability[ServiceDOF.GLOBAL_POSITION].level is CapabilityLevel.PREDICTED_ONLY
    assert capability[ServiceDOF.GLOBAL_POSITION].uncertainty_scale > 1.0
    assert capability[ServiceDOF.GLOBAL_YAW].level is CapabilityLevel.UNOBSERVABLE


def test_distal_endpoint_is_reconstructed_not_directly_observed():
    capability = capability_for_scenario("imu_freeze")
    assert capability[ServiceDOF.DERIVED_WRIST_ANKLE_POSITIONS].level is CapabilityLevel.KINEMATICALLY_RECONSTRUCTED


def test_fault_atlas_covers_required_failures_and_is_shadow_only():
    results = fault_scenario_results()
    names = {row["scenario"] for row in results["rows"]}
    assert {"positive_bias", "negative_bias", "burst", "dropout", "tag_wide_error",
            "anchor_wide_error", "timing_error", "frame_error", "imu_freeze",
            "all_uwb_blackout"} <= names
    assert results["all_shadow_only"]
    assert results["health_informativeness_authority_separate"]


def test_recovery_is_gradual_without_pose_or_covariance_reset():
    tracker = RecoveryTracker(required_healthy=5)
    tracker.observe("link", MeasurementHealth.FAILED)
    states = [tracker.observe("link", MeasurementHealth.HEALTHY) for _ in range(5)]
    weights = [state.authority_weight for state in states]
    assert weights == sorted(weights)
    assert weights[0] < weights[-1] == 1.0
    assert all(not state.latent_state_reset and not state.covariance_reset for state in states)
