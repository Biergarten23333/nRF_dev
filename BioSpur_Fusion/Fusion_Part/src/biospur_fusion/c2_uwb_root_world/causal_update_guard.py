"""Pure causal admission guard for candidate C2 measurement updates.

This module does not estimate, clamp, or commit state.  It compares a
measurement-updated candidate with the same-availability-epoch IMU-only
prediction and returns which already-computed state the caller may publish.
All physical thresholds are explicit, provenance-qualified inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np


_SIDES = frozenset(("left", "right"))
_SUPPORT_STATES = frozenset(
    ("STANCE_CONFIRMED", "UNCERTAIN", "SWING_CONFIRMED", "UNOBSERVABLE")
)
_PUBLIC_HINGE_IDS = frozenset(
    ("elbow_left", "elbow_right", "knee_left", "knee_right")
)
_PUBLIC_HINGE_OWNER = "biospur_fusion.c2_articulated_biomechanics"


class CandidateKind(str, Enum):
    ROOT_POSITION = "ROOT_POSITION"
    ARTICULATED_IK = "ARTICULATED_IK"


class ReachabilityClass(str, Enum):
    NOMINAL = "NOMINAL"
    DYNAMIC_FALL = "DYNAMIC_FALL"


class TransitionDisposition(str, Enum):
    ACCEPT_CANDIDATE = "ACCEPT_CANDIDATE"
    REJECT_USE_IMU_PREDICTION = "REJECT_USE_IMU_PREDICTION"


class TransitionReason(str, Enum):
    ACCEPT_NOMINAL = "ACCEPT_NOMINAL"
    ACCEPT_CORROBORATED_DYNAMIC = "ACCEPT_CORROBORATED_DYNAMIC"
    REJECT_UNREACHABLE_TRANSITION = "REJECT_UNREACHABLE_TRANSITION"
    REJECT_CAUSAL_EVIDENCE_INVALID = "REJECT_CAUSAL_EVIDENCE_INVALID"
    REJECT_CONSENSUS_INSUFFICIENT = "REJECT_CONSENSUS_INSUFFICIENT"
    REJECT_REACHABILITY_UNQUALIFIED = "REJECT_REACHABILITY_UNQUALIFIED"


def _readonly_array(value: Any, shape: tuple[int, ...] | None = None) -> np.ndarray:
    result = np.asarray(value).copy()
    if shape is not None and result.shape != shape:
        raise ValueError(f"array must have shape {shape}, got {result.shape}")
    result.setflags(write=False)
    return result


def _readonly_float_array(
    value: Any, shape: tuple[int, ...] | None = None
) -> np.ndarray:
    return _readonly_array(np.asarray(value, dtype=float), shape)


def _nonblank(value: str) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _identity_inventory_valid(values: tuple[object, ...]) -> bool:
    return all(_nonblank(value) for value in values) and len(set(values)) == len(values)


def _finite_positive(value: float) -> bool:
    return math.isfinite(float(value)) and float(value) > 0.0


def _same_time(first: float, second: float) -> bool:
    scale = max(1.0, abs(float(first)), abs(float(second)))
    return abs(float(first) - float(second)) <= 32.0 * np.finfo(float).eps * scale


def _covariance_valid(value: np.ndarray) -> bool:
    covariance = np.asarray(value, dtype=float)
    if covariance.shape != (9, 9) or not np.isfinite(covariance).all():
        return False
    if not np.allclose(covariance, covariance.T, rtol=0.0, atol=1e-12):
        return False
    try:
        return bool(np.min(np.linalg.eigvalsh(covariance)) >= -1e-12)
    except np.linalg.LinAlgError:
        return False


@dataclass(frozen=True)
class ReachabilityEnvelope:
    """Qualified physical limits; every numeric field is caller-owned."""

    reachability_class: ReachabilityClass
    maximum_root_displacement_m: float
    maximum_root_speed_change_mps: float
    maximum_root_implied_acceleration_mps2: float
    maximum_joint_step_rad: float
    maximum_joint_angular_velocity_rad_s: float
    maximum_joint_angular_acceleration_rad_s2: float
    maximum_evidence_age_s: float
    minimum_impulse_mps: float
    minimum_angular_rate_rad_s: float
    minimum_activity_persistence_s: float
    minimum_unique_nodes: int
    maximum_node_root_spread_m: float
    maximum_node_geometry_condition: float
    provenance: str

    def qualification_errors(self) -> tuple[str, ...]:
        errors: list[str] = []
        if not _nonblank(self.provenance):
            errors.append("BLANK_REACHABILITY_PROVENANCE")
        numeric = {
            "maximum_root_displacement_m": self.maximum_root_displacement_m,
            "maximum_root_speed_change_mps": self.maximum_root_speed_change_mps,
            "maximum_root_implied_acceleration_mps2": self.maximum_root_implied_acceleration_mps2,
            "maximum_joint_step_rad": self.maximum_joint_step_rad,
            "maximum_joint_angular_velocity_rad_s": self.maximum_joint_angular_velocity_rad_s,
            "maximum_joint_angular_acceleration_rad_s2": self.maximum_joint_angular_acceleration_rad_s2,
            "maximum_evidence_age_s": self.maximum_evidence_age_s,
            "minimum_impulse_mps": self.minimum_impulse_mps,
            "minimum_angular_rate_rad_s": self.minimum_angular_rate_rad_s,
            "minimum_activity_persistence_s": self.minimum_activity_persistence_s,
            "maximum_node_root_spread_m": self.maximum_node_root_spread_m,
            "maximum_node_geometry_condition": self.maximum_node_geometry_condition,
        }
        errors.extend(
            f"INVALID_{name.upper()}"
            for name, value in numeric.items()
            if not _finite_positive(value)
        )
        if not isinstance(self.minimum_unique_nodes, int) or self.minimum_unique_nodes < 2:
            errors.append("INVALID_MINIMUM_UNIQUE_NODES")
        if not isinstance(self.reachability_class, ReachabilityClass):
            errors.append("INVALID_REACHABILITY_CLASS")
        return tuple(errors)


@dataclass(frozen=True)
class RootKinematicState:
    time_s: float
    position_m: np.ndarray
    velocity_mps: np.ndarray
    covariance: np.ndarray

    def __post_init__(self) -> None:
        object.__setattr__(self, "position_m", _readonly_float_array(self.position_m, (3,)))
        object.__setattr__(self, "velocity_mps", _readonly_float_array(self.velocity_mps, (3,)))
        object.__setattr__(self, "covariance", _readonly_float_array(self.covariance, (9, 9)))


@dataclass(frozen=True)
class PublicHingeKinematics:
    """Output of the public hinge owner at an authenticated native-200 tick."""

    source_time_s: float
    joint_ids: tuple[str, ...]
    joint_step_rad: np.ndarray
    joint_angular_velocity_rad_s: np.ndarray
    joint_angular_acceleration_rad_s2: np.ndarray
    rom_valid: bool
    fk_valid: bool
    owner: str
    provenance: str

    def __post_init__(self) -> None:
        ids = tuple(self.joint_ids)
        size = len(ids)
        object.__setattr__(self, "joint_ids", ids)
        object.__setattr__(self, "joint_step_rad", _readonly_float_array(self.joint_step_rad, (size,)))
        object.__setattr__(
            self,
            "joint_angular_velocity_rad_s",
            _readonly_float_array(self.joint_angular_velocity_rad_s, (size,)),
        )
        object.__setattr__(
            self,
            "joint_angular_acceleration_rad_s2",
            _readonly_float_array(self.joint_angular_acceleration_rad_s2, (size,)),
        )


@dataclass(frozen=True)
class InnovationIntegrityEvidence:
    measurement_time_s: float
    nis: float
    nis_limit: float
    measurement_integrity_valid: bool
    covariance_model_valid: bool
    provenance: str


@dataclass(frozen=True)
class CausalImuActivitySummary:
    """Per-node causal motion features computed before the candidate update."""

    measurement_time_s: float
    availability_time_s: float
    window_start_time_s: float
    node_ids: tuple[str, ...]
    latest_sample_time_s: np.ndarray
    impulse_mps: np.ndarray
    angular_rate_rad_s: np.ndarray
    persistence_s: np.ndarray
    provenance: str

    def __post_init__(self) -> None:
        ids = tuple(self.node_ids)
        size = len(ids)
        object.__setattr__(self, "node_ids", ids)
        for name in (
            "latest_sample_time_s",
            "impulse_mps",
            "angular_rate_rad_s",
            "persistence_s",
        ):
            object.__setattr__(
                self, name, _readonly_float_array(getattr(self, name), (size,))
            )


@dataclass(frozen=True)
class IndependentNodeConsensusEvidence:
    """Independent, pre-joint-candidate root fixes at one measurement epoch."""

    measurement_time_s: float
    node_ids: tuple[str, ...]
    root_position_m: np.ndarray
    geometry_rank: np.ndarray
    geometry_condition: np.ndarray
    computed_before_joint_candidate: bool
    provenance: str

    def __post_init__(self) -> None:
        ids = tuple(self.node_ids)
        object.__setattr__(self, "node_ids", ids)
        object.__setattr__(
            self,
            "root_position_m",
            _readonly_float_array(self.root_position_m, (len(ids), 3)),
        )
        object.__setattr__(
            self, "geometry_rank", _readonly_array(self.geometry_rank, (len(ids),))
        )
        object.__setattr__(
            self,
            "geometry_condition",
            _readonly_float_array(self.geometry_condition, (len(ids),)),
        )


@dataclass(frozen=True)
class CausalContactTransitionEvidence:
    """Frozen contact-owner lifecycle state; no contact is recomputed here."""

    time_s: float
    previous_state: Mapping[str, str]
    current_state: Mapping[str, str]
    released_sides: tuple[str, ...]
    swing_sides: tuple[str, ...]
    provenance: str

    def __post_init__(self) -> None:
        previous = MappingProxyType({str(key): str(value) for key, value in self.previous_state.items()})
        current = MappingProxyType({str(key): str(value) for key, value in self.current_state.items()})
        object.__setattr__(self, "previous_state", previous)
        object.__setattr__(self, "current_state", current)
        object.__setattr__(self, "released_sides", tuple(str(value) for value in self.released_sides))
        object.__setattr__(self, "swing_sides", tuple(str(value) for value in self.swing_sides))


@dataclass(frozen=True)
class CandidateTransition:
    kind: CandidateKind
    measurement_time_s: float
    availability_time_s: float
    previous_published_time_s: float
    previous_published_state: RootKinematicState
    imu_prediction: RootKinematicState
    measurement_candidate: RootKinematicState
    hinge: PublicHingeKinematics | None = None
    integrity: InnovationIntegrityEvidence | None = None


@dataclass(frozen=True)
class TransitionDecision:
    disposition: TransitionDisposition
    reason: TransitionReason
    subreasons: tuple[str, ...]
    selected_state: RootKinematicState
    selected_envelope: str | None
    metrics: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "subreasons", tuple(str(value) for value in self.subreasons))
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))

    @property
    def accepted(self) -> bool:
        return self.disposition is TransitionDisposition.ACCEPT_CANDIDATE

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "disposition": self.disposition.value,
            "reason": self.reason.value,
            "subreasons": list(self.subreasons),
            "selected_envelope": self.selected_envelope,
            "metrics": dict(self.metrics),
        }


def _decision(
    proposal: CandidateTransition,
    *,
    accepted: bool,
    reason: TransitionReason,
    subreasons: tuple[str, ...] = (),
    envelope: ReachabilityEnvelope | None = None,
    metrics: Mapping[str, Any] | None = None,
) -> TransitionDecision:
    return TransitionDecision(
        TransitionDisposition.ACCEPT_CANDIDATE if accepted
        else TransitionDisposition.REJECT_USE_IMU_PREDICTION,
        reason,
        subreasons,
        proposal.measurement_candidate if accepted else proposal.imu_prediction,
        None if envelope is None else envelope.reachability_class.value,
        {} if metrics is None else metrics,
    )


def _proposal_errors(proposal: CandidateTransition) -> tuple[str, ...]:
    errors: list[str] = []
    if not isinstance(proposal.kind, CandidateKind):
        errors.append("INVALID_CANDIDATE_KIND")
    times = (
        proposal.measurement_time_s,
        proposal.availability_time_s,
        proposal.previous_published_time_s,
        proposal.previous_published_state.time_s,
        proposal.imu_prediction.time_s,
        proposal.measurement_candidate.time_s,
    )
    if not all(math.isfinite(float(value)) for value in times):
        errors.append("NONFINITE_PROPOSAL_TIME")
    if proposal.measurement_time_s > proposal.availability_time_s:
        errors.append("MEASUREMENT_AFTER_AVAILABILITY")
    if not _same_time(
        proposal.previous_published_state.time_s, proposal.previous_published_time_s
    ):
        errors.append("PREVIOUS_PUBLICATION_EPOCH_MISMATCH")
    if not _finite_positive(
        proposal.availability_time_s - proposal.previous_published_time_s
    ):
        errors.append("INVALID_AUTHORITATIVE_TRANSITION_INTERVAL")
    numeric_horizon = proposal.imu_prediction.time_s
    if (
        numeric_horizon < proposal.measurement_time_s
        or numeric_horizon > proposal.availability_time_s
    ):
        errors.append("PREDICTION_EPOCH_MISMATCH")
    if not _same_time(proposal.measurement_candidate.time_s, numeric_horizon):
        errors.append("CANDIDATE_EPOCH_MISMATCH")
    for name, state in (
        ("PREVIOUS_PUBLICATION", proposal.previous_published_state),
        ("PREDICTION", proposal.imu_prediction),
        ("CANDIDATE", proposal.measurement_candidate),
    ):
        if not np.isfinite(state.position_m).all() or not np.isfinite(state.velocity_mps).all():
            errors.append(f"{name}_STATE_NONFINITE")
        if not _covariance_valid(state.covariance):
            errors.append(f"{name}_COVARIANCE_INVALID")
    hinge = proposal.hinge
    if proposal.kind is CandidateKind.ROOT_POSITION and hinge is not None:
        errors.append("ROOT_POSITION_CANDIDATE_HAS_HINGE_PAYLOAD")
    if proposal.kind is CandidateKind.ARTICULATED_IK and hinge is None:
        errors.append("ARTICULATED_CANDIDATE_LACKS_PUBLIC_HINGE_EVIDENCE")
    if proposal.kind is CandidateKind.ARTICULATED_IK and proposal.integrity is None:
        errors.append("ARTICULATED_CANDIDATE_LACKS_INTEGRITY_EVIDENCE")
    if hinge is not None:
        source_age = proposal.measurement_time_s - hinge.source_time_s
        if (
            not math.isfinite(float(hinge.source_time_s))
            or not source_age > 0.0
            or source_age > 0.005005
            or proposal.measurement_time_s > proposal.availability_time_s
        ):
            errors.append("HINGE_SOURCE_EPOCH_INVALID")
        if not _identity_inventory_valid(hinge.joint_ids):
            errors.append("HINGE_IDENTITY_INVALID")
        if set(hinge.joint_ids) != _PUBLIC_HINGE_IDS:
            errors.append("PUBLIC_HINGE_INVENTORY_INVALID")
        if hinge.owner != _PUBLIC_HINGE_OWNER:
            errors.append("PUBLIC_HINGE_OWNER_INVALID")
        if not _nonblank(hinge.provenance):
            errors.append("HINGE_PROVENANCE_INVALID")
        if not all(np.isfinite(value).all() for value in (
            hinge.joint_step_rad,
            hinge.joint_angular_velocity_rad_s,
            hinge.joint_angular_acceleration_rad_s2,
        )):
            errors.append("HINGE_KINEMATICS_NONFINITE")
        if np.any(hinge.joint_step_rad < 0.0):
            errors.append("HINGE_STEP_NEGATIVE")
        if not hinge.rom_valid:
            errors.append("ROM_INVALID")
        if not hinge.fk_valid:
            errors.append("FK_INVALID")
    integrity = proposal.integrity
    if integrity is not None:
        if not _same_time(integrity.measurement_time_s, proposal.measurement_time_s):
            errors.append("INTEGRITY_EPOCH_MISMATCH")
        if not _nonblank(integrity.provenance):
            errors.append("INTEGRITY_PROVENANCE_INVALID")
        if not math.isfinite(float(integrity.nis)) or integrity.nis < 0.0:
            errors.append("NIS_INVALID")
        if not _finite_positive(integrity.nis_limit):
            errors.append("NIS_LIMIT_UNQUALIFIED")
        elif integrity.nis > integrity.nis_limit:
            errors.append("NIS_LIMIT_EXCEEDED")
        if not integrity.measurement_integrity_valid:
            errors.append("MEASUREMENT_INTEGRITY_INVALID")
        if not integrity.covariance_model_valid:
            errors.append("MEASUREMENT_COVARIANCE_INVALID")
    return tuple(errors)


def _metrics(proposal: CandidateTransition) -> dict[str, float]:
    position_delta = (
        proposal.measurement_candidate.position_m - proposal.imu_prediction.position_m
    )
    velocity_delta = (
        proposal.measurement_candidate.velocity_mps - proposal.imu_prediction.velocity_mps
    )
    hinge = proposal.hinge
    transition_interval_s = (
        proposal.availability_time_s - proposal.previous_published_time_s
    )
    return {
        "root_displacement_m": float(np.linalg.norm(position_delta)),
        "root_speed_change_mps": float(np.linalg.norm(velocity_delta)),
        "root_implied_acceleration_mps2": float(
            np.linalg.norm(velocity_delta) / transition_interval_s
        ),
        "joint_step_maximum_rad": 0.0 if hinge is None else float(np.max(hinge.joint_step_rad)),
        "joint_angular_velocity_maximum_rad_s": 0.0 if hinge is None else float(
            np.max(np.abs(hinge.joint_angular_velocity_rad_s))
        ),
        "joint_angular_acceleration_maximum_rad_s2": 0.0 if hinge is None else float(
            np.max(np.abs(hinge.joint_angular_acceleration_rad_s2))
        ),
    }


def _within(metrics: Mapping[str, float], envelope: ReachabilityEnvelope) -> bool:
    return not _violations(metrics, envelope)


def _violations(
    metrics: Mapping[str, float], envelope: ReachabilityEnvelope
) -> tuple[str, ...]:
    pairs = (
        ("root_displacement_m", envelope.maximum_root_displacement_m),
        ("root_speed_change_mps", envelope.maximum_root_speed_change_mps),
        ("root_implied_acceleration_mps2", envelope.maximum_root_implied_acceleration_mps2),
        ("joint_step_maximum_rad", envelope.maximum_joint_step_rad),
        ("joint_angular_velocity_maximum_rad_s", envelope.maximum_joint_angular_velocity_rad_s),
        ("joint_angular_acceleration_maximum_rad_s2", envelope.maximum_joint_angular_acceleration_rad_s2),
    )
    return tuple(
        f"{envelope.reachability_class.value}_{name.upper()}_EXCEEDED"
        for name, limit in pairs
        if float(metrics[name])
        > float(limit) + 64.0 * np.finfo(float).eps * max(1.0, float(limit))
    )


def _dynamic_corroboration_errors(
    proposal: CandidateTransition,
    envelope: ReachabilityEnvelope,
    activity: CausalImuActivitySummary | None,
    consensus: IndependentNodeConsensusEvidence | None,
    contact: CausalContactTransitionEvidence | None,
) -> tuple[tuple[str, ...], int, float]:
    errors: list[str] = []
    if activity is None or consensus is None or contact is None:
        return ("MISSING_DYNAMIC_CORROBORATION",), 0, math.inf
    if not all(_nonblank(value) for value in (
        activity.provenance, consensus.provenance, contact.provenance
    )):
        errors.append("DYNAMIC_PROVENANCE_INVALID")
    if not _identity_inventory_valid(activity.node_ids):
        errors.append("ACTIVITY_NODE_ID_INVALID")
    if not _identity_inventory_valid(consensus.node_ids):
        errors.append("CONSENSUS_NODE_ID_INVALID")
    if not _same_time(activity.measurement_time_s, proposal.measurement_time_s):
        errors.append("ACTIVITY_MEASUREMENT_EPOCH_MISMATCH")
    if not _same_time(activity.availability_time_s, proposal.availability_time_s):
        errors.append("ACTIVITY_AVAILABILITY_EPOCH_MISMATCH")
    if not _same_time(consensus.measurement_time_s, proposal.measurement_time_s):
        errors.append("CONSENSUS_MEASUREMENT_EPOCH_MISMATCH")
    if not _same_time(contact.time_s, proposal.availability_time_s):
        errors.append("CONTACT_AVAILABILITY_EPOCH_MISMATCH")
    if not consensus.computed_before_joint_candidate:
        errors.append("CONSENSUS_NOT_PRE_JOINT_CANDIDATE")
    if activity.window_start_time_s > activity.measurement_time_s:
        errors.append("ACTIVITY_WINDOW_REVERSED")
    if (
        not math.isfinite(activity.window_start_time_s)
        or not np.isfinite(activity.latest_sample_time_s).all()
        or np.any(activity.latest_sample_time_s > proposal.measurement_time_s)
        or np.any(activity.latest_sample_time_s < activity.window_start_time_s)
    ):
        errors.append("ACTIVITY_TIME_INVALID_OR_FUTURE")
    if not all(np.isfinite(value).all() for value in (
        activity.impulse_mps, activity.angular_rate_rad_s, activity.persistence_s,
        consensus.root_position_m, consensus.geometry_condition,
    )):
        errors.append("DYNAMIC_EVIDENCE_NONFINITE")
    rank_domain_valid = bool(
        np.issubdtype(consensus.geometry_rank.dtype, np.integer)
        and not np.issubdtype(consensus.geometry_rank.dtype, np.bool_)
        and np.all(consensus.geometry_rank >= 0)
    )
    if not rank_domain_valid:
        errors.append("CONSENSUS_GEOMETRY_RANK_NOT_INTEGER")
    condition_domain_valid = bool(
        np.isfinite(consensus.geometry_condition).all()
        and np.all(consensus.geometry_condition > 0.0)
    )
    if not condition_domain_valid:
        errors.append("CONSENSUS_GEOMETRY_CONDITION_INVALID")
    if (
        np.any(activity.impulse_mps < 0.0)
        or np.any(activity.angular_rate_rad_s < 0.0)
        or np.any(activity.persistence_s < 0.0)
    ):
        errors.append("ACTIVITY_VALUE_NEGATIVE")
    window_duration = activity.measurement_time_s - activity.window_start_time_s
    if (
        window_duration < 0.0
        or np.any(activity.persistence_s > window_duration)
        or np.any(
            activity.persistence_s
            > activity.latest_sample_time_s - activity.window_start_time_s
        )
    ):
        errors.append("ACTIVITY_PERSISTENCE_OUTSIDE_WINDOW")
    ages = proposal.measurement_time_s - activity.latest_sample_time_s
    if np.any(ages < 0.0) or np.any(ages > envelope.maximum_evidence_age_s):
        errors.append("ACTIVITY_EVIDENCE_STALE_OR_FUTURE")
    if set(contact.previous_state) != _SIDES or set(contact.current_state) != _SIDES:
        errors.append("CONTACT_SIDE_INVENTORY_INVALID")
    if any(value not in _SUPPORT_STATES for value in (
        *contact.previous_state.values(), *contact.current_state.values()
    )):
        errors.append("CONTACT_STATE_INVALID")
    if len(set(contact.released_sides)) != len(contact.released_sides) or set(
        contact.released_sides
    ) - _SIDES:
        errors.append("CONTACT_RELEASE_IDENTITY_INVALID")
    if len(set(contact.swing_sides)) != len(contact.swing_sides) or set(
        contact.swing_sides
    ) - _SIDES:
        errors.append("CONTACT_SWING_IDENTITY_INVALID")
    valid_inventory = (
        set(contact.previous_state) == _SIDES and set(contact.current_state) == _SIDES
    )
    actual_releases = set() if not valid_inventory else {
        side for side in _SIDES
        if contact.previous_state[side] in {"STANCE_CONFIRMED", "UNCERTAIN"}
        and contact.current_state[side] == "SWING_CONFIRMED"
    }
    if set(contact.released_sides) != actual_releases:
        errors.append("CONTACT_RELEASE_TRANSITION_MISMATCH")
    if not actual_releases:
        errors.append("CONTACT_ACTUAL_RELEASE_MISSING")
    actual_swings = set() if not valid_inventory else {
        side for side in _SIDES if contact.current_state[side] == "SWING_CONFIRMED"
    }
    if set(contact.swing_sides) != actual_swings:
        errors.append("CONTACT_SWING_SNAPSHOT_MISMATCH")

    active_mask = (
        (activity.impulse_mps >= envelope.minimum_impulse_mps)
        & (activity.angular_rate_rad_s >= envelope.minimum_angular_rate_rad_s)
        & (activity.persistence_s >= envelope.minimum_activity_persistence_s)
    )
    active_nodes = {
        node for node, active in zip(activity.node_ids, active_mask) if bool(active)
        if _nonblank(node)
    }
    consensus_nodes = {node for node in consensus.node_ids if _nonblank(node)}
    corroborated = active_nodes & consensus_nodes
    consensus_index = {node: index for index, node in enumerate(consensus.node_ids)}
    if rank_domain_valid and condition_domain_valid and any(
        consensus.geometry_rank[consensus_index[node]] != 3
        or consensus.geometry_condition[consensus_index[node]]
        > envelope.maximum_node_geometry_condition
        for node in corroborated
    ):
        errors.append("CONSENSUS_GEOMETRY_DEGENERATE")
    subset_indices = [
        index for index, node in enumerate(consensus.node_ids) if node in corroborated
    ]
    if subset_indices:
        subset_roots = consensus.root_position_m[subset_indices]
        centre = np.median(subset_roots, axis=0)
        spread = float(np.max(np.linalg.norm(subset_roots - centre, axis=1)))
    else:
        spread = math.inf
    if spread > envelope.maximum_node_root_spread_m:
        errors.append("CONSENSUS_ROOT_SPREAD_EXCEEDED")
    return tuple(errors), len(corroborated), spread


def evaluate_candidate_transition(
    proposal: CandidateTransition,
    *,
    nominal_envelope: ReachabilityEnvelope | None,
    dynamic_envelope: ReachabilityEnvelope | None = None,
    activity: CausalImuActivitySummary | None = None,
    consensus: IndependentNodeConsensusEvidence | None = None,
    contact: CausalContactTransitionEvidence | None = None,
) -> TransitionDecision:
    """Evaluate one candidate without mutating or clamping any input."""

    proposal_errors = _proposal_errors(proposal)
    if proposal_errors:
        return _decision(
            proposal,
            accepted=False,
            reason=TransitionReason.REJECT_CAUSAL_EVIDENCE_INVALID,
            subreasons=proposal_errors,
        )
    if nominal_envelope is None:
        return _decision(
            proposal,
            accepted=False,
            reason=TransitionReason.REJECT_REACHABILITY_UNQUALIFIED,
            subreasons=("MISSING_NOMINAL_ENVELOPE",),
        )
    nominal_errors = nominal_envelope.qualification_errors()
    if nominal_errors or nominal_envelope.reachability_class is not ReachabilityClass.NOMINAL:
        return _decision(
            proposal,
            accepted=False,
            reason=TransitionReason.REJECT_REACHABILITY_UNQUALIFIED,
            subreasons=nominal_errors or ("NOMINAL_ENVELOPE_CLASS_INVALID",),
        )
    metrics = _metrics(proposal)
    nominal_violations = _violations(metrics, nominal_envelope)
    if not nominal_violations:
        return _decision(
            proposal,
            accepted=True,
            reason=TransitionReason.ACCEPT_NOMINAL,
            envelope=nominal_envelope,
            metrics=metrics,
        )
    if dynamic_envelope is None:
        return _decision(
            proposal,
            accepted=False,
            reason=TransitionReason.REJECT_REACHABILITY_UNQUALIFIED,
            subreasons=nominal_violations + ("MISSING_DYNAMIC_ENVELOPE",),
            metrics=metrics,
        )
    dynamic_errors = dynamic_envelope.qualification_errors()
    if dynamic_errors or dynamic_envelope.reachability_class is not ReachabilityClass.DYNAMIC_FALL:
        return _decision(
            proposal,
            accepted=False,
            reason=TransitionReason.REJECT_REACHABILITY_UNQUALIFIED,
            subreasons=dynamic_errors or ("DYNAMIC_ENVELOPE_CLASS_INVALID",),
            metrics=metrics,
        )
    dynamic_violations = _violations(metrics, dynamic_envelope)
    if dynamic_violations:
        return _decision(
            proposal,
            accepted=False,
            reason=TransitionReason.REJECT_UNREACHABLE_TRANSITION,
            subreasons=dynamic_violations,
            envelope=dynamic_envelope,
            metrics=metrics,
        )
    corroboration_errors, unique_nodes, spread = _dynamic_corroboration_errors(
        proposal, dynamic_envelope, activity, consensus, contact
    )
    metrics = {
        **metrics,
        "corroborated_unique_node_count": int(unique_nodes),
        "independent_node_root_spread_m": float(spread),
    }
    if unique_nodes < dynamic_envelope.minimum_unique_nodes:
        return _decision(
            proposal,
            accepted=False,
            reason=TransitionReason.REJECT_CONSENSUS_INSUFFICIENT,
            subreasons=corroboration_errors + ("UNIQUE_NODE_CONSENSUS_BELOW_LIMIT",),
            envelope=dynamic_envelope,
            metrics=metrics,
        )
    if corroboration_errors:
        return _decision(
            proposal,
            accepted=False,
            reason=TransitionReason.REJECT_CAUSAL_EVIDENCE_INVALID,
            subreasons=corroboration_errors,
            envelope=dynamic_envelope,
            metrics=metrics,
        )
    return _decision(
        proposal,
        accepted=True,
        reason=TransitionReason.ACCEPT_CORROBORATED_DYNAMIC,
        envelope=dynamic_envelope,
        metrics=metrics,
    )
