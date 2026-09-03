"""Dirty-range hypotheses, authority routing, recovery, and service capability."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .contracts import (
    ActivationState,
    AuthorityProposal,
    AuthorityScope,
    CapabilityLevel,
    CapabilityState,
    FaultDomain,
    HealthHypothesis,
    Informativeness,
    MeasurementHealth,
    ServiceDOF,
)


PROTECTED_SCOPES = frozenset({AuthorityScope.ROOT_TRANSLATION_ELIGIBLE, AuthorityScope.COMMON_YAW_ELIGIBLE})
COMMON_CAUSE_DOMAINS = frozenset({
    FaultDomain.CLOCK_TIMING,
    FaultDomain.ANCHOR_MAP_FRAME,
    FaultDomain.BODY_GEOMETRY,
    FaultDomain.SHARED_SOFTWARE_MODEL,
})


def group_hypotheses(hypotheses: Iterable[HealthHypothesis]) -> dict[str, tuple[str, ...]]:
    groups: dict[str, list[str]] = {}
    for hypothesis in hypotheses:
        group = hypothesis.common_cause_id or f"independent:{hypothesis.hypothesis_id}"
        groups.setdefault(group, []).append(hypothesis.hypothesis_id)
    return {key: tuple(sorted(value)) for key, value in sorted(groups.items())}


@dataclass
class RecoveryState:
    source_id: str
    consecutive_healthy: int = 0
    observations: int = 0
    authority_weight: float = 0.0
    latent_state_reset: bool = False
    covariance_reset: bool = False


@dataclass
class RecoveryTracker:
    required_healthy: int = 5
    states: dict[str, RecoveryState] = field(default_factory=dict)

    def observe(self, source_id: str, health: MeasurementHealth) -> RecoveryState:
        state = self.states.setdefault(source_id, RecoveryState(source_id))
        state.observations += 1
        if health is MeasurementHealth.HEALTHY:
            state.consecutive_healthy += 1
            state.authority_weight = min(1.0, state.consecutive_healthy / float(self.required_healthy))
        else:
            state.consecutive_healthy = 0
            state.authority_weight = 0.0
        # Evidence authority changes, but the latent pose/covariance is never reset.
        state.latent_state_reset = False
        state.covariance_reset = False
        # Return a point-in-time audit snapshot, not the mutable object retained
        # by the tracker. Historical recovery rows must not change retroactively.
        return RecoveryState(**vars(state))


class AuthorityRouter:
    """Fail-closed authority policy. Root-R6A0 can only emit shadow proposals."""

    def route(self, *, proposal_id: str, source_ids: tuple[str, ...], requested_scope: AuthorityScope,
              measurement_health: MeasurementHealth, informativeness: Informativeness,
              hypotheses: tuple[HealthHypothesis, ...], target_blocks: tuple[str, ...],
              affected_service_dofs: tuple[ServiceDOF, ...], independent_tags: int = 0,
              independent_anchors: int = 0, recovery_weight: float = 0.0) -> AuthorityProposal:
        grouped = group_hypotheses(hypotheses)
        common_cause = any(
            hypothesis.domain in COMMON_CAUSE_DOMAINS
            and hypothesis.measurement_health is not MeasurementHealth.HEALTHY
            for hypothesis in hypotheses
        ) or any(len(members) > 1 and not group.startswith("independent:") for group, members in grouped.items())
        if common_cause:
            granted = AuthorityScope.COMMON_CAUSE_FREEZE
            reason = "correlated/common-domain evidence grouped once; protected authority frozen"
        elif measurement_health in (MeasurementHealth.FAILED, MeasurementHealth.UNKNOWN):
            granted = AuthorityScope.QUARANTINE
            reason = "measurement health does not support correction"
        elif informativeness in (Informativeness.UNINFORMATIVE, Informativeness.UNKNOWN):
            granted = AuthorityScope.PROBE_ONLY
            reason = "health and informativeness are separate; this evidence is not currently informative"
        elif requested_scope in PROTECTED_SCOPES and len(source_ids) <= 1:
            granted = AuthorityScope.PROBE_ONLY
            reason = "one link/tag/anchor cannot independently affect root translation or common yaw"
        elif requested_scope is AuthorityScope.ROOT_TRANSLATION_ELIGIBLE and (independent_tags < 2 or independent_anchors < 4):
            granted = AuthorityScope.LOCAL_SEGMENT_ONLY
            reason = "insufficient independent tag/anchor support for root translation eligibility"
        elif requested_scope is AuthorityScope.COMMON_YAW_ELIGIBLE and (independent_tags < 3 or independent_anchors < 4):
            granted = AuthorityScope.INCREMENT_ONLY
            reason = "insufficient distributed geometry for common-yaw eligibility"
        elif recovery_weight < 1.0 and requested_scope in PROTECTED_SCOPES:
            granted = AuthorityScope.INCREMENT_ONLY
            reason = "stateful recovery has not gradually restored eligibility"
        else:
            granted = requested_scope
            reason = "shadow eligibility only; production authority remains zero"
        return AuthorityProposal(
            proposal_id=proposal_id,
            source_ids=source_ids,
            requested_scope=requested_scope,
            granted_scope=granted,
            measurement_health=measurement_health,
            informativeness=informativeness,
            activation_state=ActivationState.SHADOW_ONLY,
            target_blocks=target_blocks,
            affected_service_dofs=affected_service_dofs,
            common_cause_groups=tuple(grouped),
            production_authorized=False,
            recovery_weight=float(recovery_weight),
            reason=reason,
        )


def _capability(level: CapabilityLevel, scale: float, ancestry: tuple[str, ...], reason: str,
                dof: ServiceDOF) -> CapabilityState:
    return CapabilityState(dof, level, scale, ancestry, reason)


def baseline_capability() -> dict[ServiceDOF, CapabilityState]:
    return {
        ServiceDOF.BODY_RELATIVE_POSE: _capability(CapabilityLevel.MULTI_SENSOR_SUPPORTED, 1.0, ("IMU", "UWB", "FK"), "articulated state is jointly supported", ServiceDOF.BODY_RELATIVE_POSE),
        ServiceDOF.JOINT_ANGLES: _capability(CapabilityLevel.MULTI_SENSOR_SUPPORTED, 1.0, ("IMU", "FK", "SOFT_ANATOMY"), "relative rotations are multi-IMU supported", ServiceDOF.JOINT_ANGLES),
        ServiceDOF.DERIVED_WRIST_ANKLE_POSITIONS: _capability(CapabilityLevel.KINEMATICALLY_RECONSTRUCTED, 1.0, ("FK", "CALIBRATION", "IMU", "UWB"), "anatomical endpoints are derived, never direct tag observations", ServiceDOF.DERIVED_WRIST_ANKLE_POSITIONS),
        ServiceDOF.GLOBAL_POSITION: _capability(CapabilityLevel.MULTI_SENSOR_SUPPORTED, 1.0, ("RAW_UWB", "ANCHOR_MAP", "IMU", "FK"), "distributed ranges can support global translation when qualified", ServiceDOF.GLOBAL_POSITION),
        ServiceDOF.GLOBAL_YAW: _capability(CapabilityLevel.MULTI_SENSOR_SUPPORTED, 1.0, ("DISTRIBUTED_RAW_UWB", "IMU", "FK", "GAUGE"), "yaw support requires qualified motion and distributed geometry", ServiceDOF.GLOBAL_YAW),
        ServiceDOF.CLINICAL_METRICS: _capability(CapabilityLevel.UNOBSERVABLE, 4.0, ("UNVALIDATED_ANATOMY",), "no clinical validation or qualified joint model", ServiceDOF.CLINICAL_METRICS),
        ServiceDOF.VISUALIZATION_CONTINUITY: _capability(CapabilityLevel.KINEMATICALLY_RECONSTRUCTED, 1.0, ("STATE_PREDICTION", "FK"), "display continuity can outlive direct measurement support", ServiceDOF.VISUALIZATION_CONTINUITY),
    }


FAULT_SCENARIOS: Mapping[str, dict] = {
    "positive_bias": {"domain": FaultDomain.TAG_ANCHOR_LINK, "health": MeasurementHealth.SUSPECT, "informativeness": Informativeness.WEAK, "scope": AuthorityScope.PROBE_ONLY},
    "negative_bias": {"domain": FaultDomain.TAG_ANCHOR_LINK, "health": MeasurementHealth.SUSPECT, "informativeness": Informativeness.WEAK, "scope": AuthorityScope.PROBE_ONLY},
    "burst": {"domain": FaultDomain.SINGLE_EVENT, "health": MeasurementHealth.FAILED, "informativeness": Informativeness.UNINFORMATIVE, "scope": AuthorityScope.QUARANTINE},
    "dropout": {"domain": FaultDomain.SINGLE_EVENT, "health": MeasurementHealth.FAILED, "informativeness": Informativeness.UNINFORMATIVE, "scope": AuthorityScope.QUARANTINE},
    "tag_wide_error": {"domain": FaultDomain.TAG, "health": MeasurementHealth.SUSPECT, "informativeness": Informativeness.WEAK, "scope": AuthorityScope.LOCAL_SEGMENT_ONLY},
    "anchor_wide_error": {"domain": FaultDomain.ANCHOR, "health": MeasurementHealth.SUSPECT, "informativeness": Informativeness.WEAK, "scope": AuthorityScope.PROBE_ONLY},
    "limb_wide_error": {"domain": FaultDomain.LIMB, "health": MeasurementHealth.SUSPECT, "informativeness": Informativeness.WEAK, "scope": AuthorityScope.LIMB_PROPAGATION},
    "timing_error": {"domain": FaultDomain.CLOCK_TIMING, "health": MeasurementHealth.FAILED, "informativeness": Informativeness.UNINFORMATIVE, "scope": AuthorityScope.COMMON_CAUSE_FREEZE},
    "frame_error": {"domain": FaultDomain.ANCHOR_MAP_FRAME, "health": MeasurementHealth.FAILED, "informativeness": Informativeness.UNINFORMATIVE, "scope": AuthorityScope.COMMON_CAUSE_FREEZE},
    "body_geometry_error": {"domain": FaultDomain.BODY_GEOMETRY, "health": MeasurementHealth.SUSPECT, "informativeness": Informativeness.UNKNOWN, "scope": AuthorityScope.COMMON_CAUSE_FREEZE},
    "shared_model_error": {"domain": FaultDomain.SHARED_SOFTWARE_MODEL, "health": MeasurementHealth.FAILED, "informativeness": Informativeness.UNKNOWN, "scope": AuthorityScope.COMMON_CAUSE_FREEZE},
    "imu_freeze": {"domain": FaultDomain.IMU_NODE, "health": MeasurementHealth.FAILED, "informativeness": Informativeness.UNINFORMATIVE, "scope": AuthorityScope.QUARANTINE},
    "all_uwb_blackout": {"domain": FaultDomain.TAG_ANCHOR_LINK, "health": MeasurementHealth.FAILED, "informativeness": Informativeness.UNINFORMATIVE, "scope": AuthorityScope.COMMON_CAUSE_FREEZE},
}


def capability_for_scenario(name: str) -> dict[ServiceDOF, CapabilityState]:
    if name not in FAULT_SCENARIOS:
        raise KeyError(name)
    values = baseline_capability()
    if name in ("positive_bias", "negative_bias", "burst", "dropout"):
        values[ServiceDOF.GLOBAL_POSITION] = _capability(CapabilityLevel.MULTI_SENSOR_SUPPORTED, 1.15, ("REMAINING_UWB", "IMU", "FK"), "one event/link is downweighted without a root reset", ServiceDOF.GLOBAL_POSITION)
    elif name in ("tag_wide_error", "anchor_wide_error"):
        values[ServiceDOF.GLOBAL_POSITION] = _capability(CapabilityLevel.MULTI_SENSOR_SUPPORTED, 1.6, ("REMAINING_DISTRIBUTED_UWB", "IMU", "FK"), "redundant geometry remains but uncertainty increases", ServiceDOF.GLOBAL_POSITION)
        values[ServiceDOF.GLOBAL_YAW] = _capability(CapabilityLevel.PREDICTED_ONLY, 2.0, ("IMU_INCREMENT", "PRIOR_GAUGE"), "directional UWB support is degraded", ServiceDOF.GLOBAL_YAW)
    elif name == "limb_wide_error":
        values[ServiceDOF.BODY_RELATIVE_POSE] = _capability(CapabilityLevel.KINEMATICALLY_RECONSTRUCTED, 2.5, ("PROXIMAL_IMU", "FK", "SOFT_ANATOMY"), "affected limb is reconstructed through the articulated chain", ServiceDOF.BODY_RELATIVE_POSE)
        values[ServiceDOF.DERIVED_WRIST_ANKLE_POSITIONS] = _capability(CapabilityLevel.KINEMATICALLY_RECONSTRUCTED, 3.0, ("FK", "PROXIMAL_SUPPORT"), "distal endpoints remain explicitly reconstructed", ServiceDOF.DERIVED_WRIST_ANKLE_POSITIONS)
    elif name == "imu_freeze":
        values[ServiceDOF.BODY_RELATIVE_POSE] = _capability(CapabilityLevel.KINEMATICALLY_RECONSTRUCTED, 2.2, ("NEIGHBOUR_IMUS", "RAW_UWB", "FK"), "frozen node is not treated as current direct orientation", ServiceDOF.BODY_RELATIVE_POSE)
        values[ServiceDOF.JOINT_ANGLES] = _capability(CapabilityLevel.KINEMATICALLY_RECONSTRUCTED, 2.5, ("NEIGHBOUR_IMUS", "FK", "SOFT_ANATOMY"), "affected joint loses one direct inertial endpoint", ServiceDOF.JOINT_ANGLES)
    elif name == "timing_error":
        values[ServiceDOF.GLOBAL_POSITION] = _capability(CapabilityLevel.PREDICTED_ONLY, 4.0, ("IMU_PROPAGATION",), "mis-timed ranges have zero correction authority", ServiceDOF.GLOBAL_POSITION)
        values[ServiceDOF.GLOBAL_YAW] = _capability(CapabilityLevel.UNOBSERVABLE, 5.0, ("IMU_INCREMENT",), "cross-modal directional support is invalid", ServiceDOF.GLOBAL_YAW)
    elif name == "frame_error":
        values[ServiceDOF.GLOBAL_POSITION] = _capability(CapabilityLevel.UNOBSERVABLE, 8.0, ("BODY_RELATIVE_STATE",), "anchor/world frame binding is unavailable", ServiceDOF.GLOBAL_POSITION)
        values[ServiceDOF.GLOBAL_YAW] = _capability(CapabilityLevel.UNOBSERVABLE, 8.0, ("BODY_RELATIVE_STATE",), "static gauge cannot be fabricated from motion drift", ServiceDOF.GLOBAL_YAW)
    elif name in ("body_geometry_error", "shared_model_error"):
        values[ServiceDOF.BODY_RELATIVE_POSE] = _capability(CapabilityLevel.PREDICTED_ONLY, 6.0, ("LAST_QUALIFIED_STATE",), "common geometry/model cause freezes propagation of measurement authority", ServiceDOF.BODY_RELATIVE_POSE)
        values[ServiceDOF.DERIVED_WRIST_ANKLE_POSITIONS] = _capability(CapabilityLevel.PREDICTED_ONLY, 8.0, ("LAST_QUALIFIED_STATE",), "derived points inherit the common model uncertainty", ServiceDOF.DERIVED_WRIST_ANKLE_POSITIONS)
        values[ServiceDOF.GLOBAL_POSITION] = _capability(CapabilityLevel.PREDICTED_ONLY, 8.0, ("LAST_QUALIFIED_STATE",), "correlated residual votes are not independent", ServiceDOF.GLOBAL_POSITION)
        values[ServiceDOF.GLOBAL_YAW] = _capability(CapabilityLevel.UNOBSERVABLE, 8.0, ("LAST_QUALIFIED_GAUGE",), "common-yaw authority is frozen", ServiceDOF.GLOBAL_YAW)
    elif name == "all_uwb_blackout":
        values[ServiceDOF.BODY_RELATIVE_POSE] = _capability(CapabilityLevel.MULTI_SENSOR_SUPPORTED, 2.0, ("TEN_IMUS", "FK", "SOFT_ANATOMY"), "body-relative articulated pose remains available", ServiceDOF.BODY_RELATIVE_POSE)
        values[ServiceDOF.JOINT_ANGLES] = _capability(CapabilityLevel.MULTI_SENSOR_SUPPORTED, 2.0, ("TEN_IMUS", "FK"), "relative joint motion remains inertially supported", ServiceDOF.JOINT_ANGLES)
        values[ServiceDOF.DERIVED_WRIST_ANKLE_POSITIONS] = _capability(CapabilityLevel.KINEMATICALLY_RECONSTRUCTED, 3.0, ("IMU", "FK", "FROZEN_CALIBRATION"), "distal points remain derived with growing uncertainty", ServiceDOF.DERIVED_WRIST_ANKLE_POSITIONS)
        values[ServiceDOF.GLOBAL_POSITION] = _capability(CapabilityLevel.PREDICTED_ONLY, 10.0, ("IMU_PROPAGATION",), "no absolute range support", ServiceDOF.GLOBAL_POSITION)
        values[ServiceDOF.GLOBAL_YAW] = _capability(CapabilityLevel.UNOBSERVABLE, 10.0, ("GYRO_INCREMENT_ONLY",), "global yaw gauge is unobservable without qualified directional evidence", ServiceDOF.GLOBAL_YAW)
    return values


def fault_scenario_results() -> dict:
    router = AuthorityRouter()
    rows = []
    for name, spec in FAULT_SCENARIOS.items():
        hypothesis = HealthHypothesis(
            f"hypothesis:{name}", spec["domain"], ("generic_member",), spec["health"],
            (f"event:{name}",), 0.9 if spec["health"] is not MeasurementHealth.HEALTHY else 0.1,
            f"common:{name}" if spec["domain"] in COMMON_CAUSE_DOMAINS or name == "all_uwb_blackout" else None,
        )
        proposal = router.route(
            proposal_id=f"authority:{name}", source_ids=(f"source:{name}",), requested_scope=spec["scope"],
            measurement_health=spec["health"], informativeness=spec["informativeness"],
            hypotheses=(hypothesis,), target_blocks=("shadow:target",),
            affected_service_dofs=(ServiceDOF.GLOBAL_POSITION, ServiceDOF.GLOBAL_YAW),
        )
        capabilities = capability_for_scenario(name)
        rows.append({
            "scenario": name,
            "fault_domain": spec["domain"].value,
            "measurement_health": spec["health"].value,
            "current_informativeness": spec["informativeness"].value,
            "allowed_authority": proposal.granted_scope.value,
            "production_authorized": proposal.production_authorized,
            "capabilities": {dof.value: {
                "level": state.level.value,
                "uncertainty_scale": state.uncertainty_scale,
                "ancestry": list(state.ancestry),
                "reason": state.reason,
            } for dof, state in capabilities.items()},
        })
    tracker = RecoveryTracker()
    recovery = []
    tracker.observe("generic_link", MeasurementHealth.FAILED)
    for _ in range(tracker.required_healthy):
        state = tracker.observe("generic_link", MeasurementHealth.HEALTHY)
        recovery.append({
            "consecutive_healthy": state.consecutive_healthy,
            "authority_weight": state.authority_weight,
            "latent_state_reset": state.latent_state_reset,
            "covariance_reset": state.covariance_reset,
        })
    return {
        "schema": "biospur.root_r6a0.fault_scenarios.v1",
        "rows": rows,
        "recovery": recovery,
        "all_shadow_only": all(not row["production_authorized"] for row in rows),
        "health_informativeness_authority_separate": True,
        "correlated_votes_grouped": True,
    }
