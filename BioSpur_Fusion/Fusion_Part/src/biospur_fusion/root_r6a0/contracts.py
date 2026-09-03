"""Typed solver-independent contracts for the Root-R6A0 information graph."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

import numpy as np


class CalibrationStatus(str, Enum):
    KNOWN_SYNTHETIC = "KNOWN_SYNTHETIC"
    VERIFIED_INPUT = "VERIFIED_INPUT"
    FROZEN_UNCERTAIN = "FROZEN_UNCERTAIN"


class ActivationState(str, Enum):
    ACTIVE_STRUCTURAL = "ACTIVE_STRUCTURAL"
    ACTIVE_SYNTHETIC = "ACTIVE_SYNTHETIC"
    SHADOW_ONLY = "SHADOW_ONLY"
    DISABLED = "DISABLED"
    BLOCKED = "BLOCKED"


class EvidenceRepresentation(str, Enum):
    RAW_IMU = "RAW_IMU"
    RAW_UWB = "RAW_UWB"
    M1_DERIVED = "M1_DERIVED"
    T4_DERIVED = "T4_DERIVED"
    SYNTHETIC_TRUTH = "SYNTHETIC_TRUTH"


class FaultDomain(str, Enum):
    SINGLE_EVENT = "single_event"
    TAG_ANCHOR_LINK = "tag_anchor_link"
    TAG = "tag"
    ANCHOR = "anchor"
    LIMB = "limb"
    IMU_NODE = "imu_node"
    CLOCK_TIMING = "clock_timing"
    ANCHOR_MAP_FRAME = "anchor_map_frame"
    BODY_GEOMETRY = "body_geometry"
    SHARED_SOFTWARE_MODEL = "shared_software_model"


class MeasurementHealth(str, Enum):
    HEALTHY = "HEALTHY"
    SUSPECT = "SUSPECT"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class Informativeness(str, Enum):
    INFORMATIVE = "INFORMATIVE"
    WEAK = "WEAK"
    UNINFORMATIVE = "UNINFORMATIVE"
    UNKNOWN = "UNKNOWN"


class AuthorityScope(str, Enum):
    PROBE_ONLY = "probe_only"
    LOCAL_SEGMENT_ONLY = "local_segment_only"
    LIMB_PROPAGATION = "limb_propagation"
    INCREMENT_ONLY = "increment_only"
    ROOT_TRANSLATION_ELIGIBLE = "root_translation_eligible"
    COMMON_YAW_ELIGIBLE = "common_yaw_eligible"
    QUARANTINE = "quarantine"
    COMMON_CAUSE_FREEZE = "common_cause_freeze"


class CapabilityLevel(str, Enum):
    DIRECTLY_OBSERVED = "DIRECTLY_OBSERVED"
    MULTI_SENSOR_SUPPORTED = "MULTI_SENSOR_SUPPORTED"
    KINEMATICALLY_RECONSTRUCTED = "KINEMATICALLY_RECONSTRUCTED"
    PREDICTED_ONLY = "PREDICTED_ONLY"
    UNOBSERVABLE = "UNOBSERVABLE"


class ServiceDOF(str, Enum):
    BODY_RELATIVE_POSE = "body_relative_pose"
    JOINT_ANGLES = "joint_angles"
    DERIVED_WRIST_ANKLE_POSITIONS = "derived_wrist_ankle_positions"
    GLOBAL_POSITION = "global_position"
    GLOBAL_YAW = "global_yaw"
    CLINICAL_METRICS = "clinical_metrics"
    VISUALIZATION_CONTINUITY = "visualization_continuity"


@dataclass(frozen=True)
class CalibrationSlot:
    slot_id: str
    kind: str
    owner_id: str
    status: CalibrationStatus
    value: tuple[float, ...] | None
    covariance: tuple[tuple[float, ...], ...] | None
    provenance: str
    fitted_from_c1: bool = False

    def __post_init__(self) -> None:
        if self.fitted_from_c1:
            raise ValueError("Root-R6A0 may not fit calibration from C1")
        if self.status is CalibrationStatus.FROZEN_UNCERTAIN and self.value is not None:
            raise ValueError("unknown calibration must remain value=None, never precise zero")
        if self.status is not CalibrationStatus.FROZEN_UNCERTAIN and self.value is None:
            raise ValueError("known calibration lacks value")
        if self.covariance is not None:
            cov = np.asarray(self.covariance, float)
            if cov.ndim != 2 or cov.shape[0] != cov.shape[1] or not np.isfinite(cov).all():
                raise ValueError(f"invalid covariance for {self.slot_id}")
            if np.min(np.linalg.eigvalsh(0.5 * (cov + cov.T))) < -1e-12:
                raise ValueError(f"non-PSD covariance for {self.slot_id}")


@dataclass(frozen=True)
class EvidenceRecord:
    event_uid: str
    physical_event_uid: str
    representation: EvidenceRepresentation
    raw_ancestry: frozenset[str]
    measurement_time_s: float
    availability_time_s: float | None
    owner_id: str
    covariance_provenance: str
    fault_domains: tuple[FaultDomain, ...]
    payload_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.event_uid or not self.physical_event_uid or not self.raw_ancestry:
            raise ValueError("evidence identity and raw ancestry are mandatory")
        if not np.isfinite(self.measurement_time_s):
            raise ValueError("measurement time must be finite")
        if self.availability_time_s is not None:
            if not np.isfinite(self.availability_time_s):
                raise ValueError("availability time must be finite")
            if self.availability_time_s + 1e-12 < self.measurement_time_s:
                raise ValueError("availability precedes physical measurement")
        if self.representation in (EvidenceRepresentation.RAW_IMU, EvidenceRepresentation.RAW_UWB):
            if self.physical_event_uid not in self.raw_ancestry:
                raise ValueError("raw record ancestry must include its physical event UID")


@dataclass(frozen=True)
class StateBlock:
    block_id: str
    keyframe_time_s: float | None
    role: str
    dimension: int
    owner_id: str
    uncertainty_status: str

    def __post_init__(self) -> None:
        if self.dimension <= 0:
            raise ValueError("state block dimension must be positive")


@dataclass(frozen=True)
class FactorProposal:
    factor_id: str
    family: str
    physical_event_uids: tuple[str, ...]
    raw_ancestry: frozenset[str]
    measurement_time_s: float | None
    availability_time_s: float | None
    connected_variable_blocks: tuple[str, ...]
    residual_dimension: int
    covariance_provenance: str
    fault_domains: tuple[FaultDomain, ...]
    activation_state: ActivationState
    allowed_authority_scope: AuthorityScope
    affected_service_dofs: tuple[ServiceDOF, ...]

    def __post_init__(self) -> None:
        if self.residual_dimension <= 0 or not self.connected_variable_blocks:
            raise ValueError("factor needs residual rows and connected state blocks")
        if self.measurement_time_s is not None and self.availability_time_s is not None:
            if self.availability_time_s + 1e-12 < self.measurement_time_s:
                raise ValueError("factor availability precedes measurement")
        if self.activation_state is ActivationState.SHADOW_ONLY and self.allowed_authority_scope in (
            AuthorityScope.ROOT_TRANSLATION_ELIGIBLE,
            AuthorityScope.COMMON_YAW_ELIGIBLE,
        ):
            # Eligibility is recorded by AuthorityProposal; a real-data factor itself
            # cannot carry protected correction authority.
            raise ValueError("shadow factor cannot directly carry protected authority")


@dataclass(frozen=True)
class HealthHypothesis:
    hypothesis_id: str
    domain: FaultDomain
    members: tuple[str, ...]
    measurement_health: MeasurementHealth
    evidence_uids: tuple[str, ...]
    probability: float
    common_cause_id: str | None
    stateful_age: int = 0

    def __post_init__(self) -> None:
        if not 0.0 <= self.probability <= 1.0:
            raise ValueError("hypothesis probability outside [0,1]")


@dataclass(frozen=True)
class AuthorityProposal:
    proposal_id: str
    source_ids: tuple[str, ...]
    requested_scope: AuthorityScope
    granted_scope: AuthorityScope
    measurement_health: MeasurementHealth
    informativeness: Informativeness
    activation_state: ActivationState
    target_blocks: tuple[str, ...]
    affected_service_dofs: tuple[ServiceDOF, ...]
    common_cause_groups: tuple[str, ...]
    production_authorized: bool = False
    recovery_weight: float = 0.0
    reason: str = ""

    def __post_init__(self) -> None:
        if self.production_authorized:
            raise ValueError("Root-R6A0 is zero-production-authority")
        if not 0.0 <= self.recovery_weight <= 1.0:
            raise ValueError("recovery weight outside [0,1]")


@dataclass(frozen=True)
class CapabilityState:
    service_dof: ServiceDOF
    level: CapabilityLevel
    uncertainty_scale: float
    ancestry: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if self.uncertainty_scale < 1.0 or not np.isfinite(self.uncertainty_scale):
            raise ValueError("capability uncertainty scale must be finite and >= 1")


@dataclass(frozen=True)
class GraphSpec:
    schema: str
    mode: str
    segments: tuple[str, ...]
    joints: tuple[str, ...]
    imu_nodes: tuple[str, ...]
    uwb_tags: tuple[str, ...]
    anchors: tuple[int, ...]
    state_blocks: tuple[StateBlock, ...]
    calibration_slots: tuple[CalibrationSlot, ...]
    factor_proposals: tuple[FactorProposal, ...]
    constraint_tiers: Mapping[str, ActivationState]
    expected_nullspace: tuple[str, ...]
    inverse_problem: str
    production_authorized: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> dict[str, Any]:
        checks = {
            "ten_segments": len(self.segments) == 10 and len(set(self.segments)) == 10,
            "nine_joints": len(self.joints) == 9 and len(set(self.joints)) == 9,
            "ten_imus": len(self.imu_nodes) == 10 and len(set(self.imu_nodes)) == 10,
            "ten_tags": len(self.uwb_tags) == 10 and len(set(self.uwb_tags)) == 10,
            "eight_anchors": len(self.anchors) == 8 and len(set(self.anchors)) == 8,
            "state_uncertainty_present": bool(self.state_blocks) and all(b.uncertainty_status for b in self.state_blocks),
            "calibration_provenance_present": bool(self.calibration_slots) and all(s.provenance for s in self.calibration_slots),
            "factor_contract_complete": all(
                p.connected_variable_blocks and p.covariance_provenance and p.fault_domains
                and p.affected_service_dofs for p in self.factor_proposals
            ),
            "shadow_only_real": self.mode != "REAL_C1_SHADOW" or all(
                p.activation_state in (ActivationState.SHADOW_ONLY, ActivationState.DISABLED, ActivationState.BLOCKED)
                for p in self.factor_proposals
            ),
            "zero_production_authority": not self.production_authorized,
            "ik_is_same_graph_inverse": self.inverse_problem == "MAP_OVER_THIS_GRAPH_USING_SHARED_BODYMODEL_FK",
        }
        checks["pass"] = all(checks.values())
        return checks

    def as_json(self) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, frozenset):
                return sorted(value)
            if isinstance(value, tuple):
                return [convert(item) for item in value]
            if isinstance(value, list):
                return [convert(item) for item in value]
            if isinstance(value, Mapping):
                return {str(key): convert(item) for key, item in value.items()}
            return value

        return convert(asdict(self))


def state_blocks_for_keyframe(time_s: float, joints: Sequence[str], imu_nodes: Sequence[str]) -> tuple[StateBlock, ...]:
    stamp = f"{time_s:.9f}"
    blocks = [
        StateBlock(f"kf:{stamp}:root_pose", time_s, "root_pose_se3", 6, "whole_body", "FULL_COVARIANCE"),
        StateBlock(f"kf:{stamp}:root_velocity", time_s, "root_velocity", 3, "whole_body", "FULL_COVARIANCE"),
    ]
    blocks.extend(StateBlock(f"kf:{stamp}:joint:{joint}", time_s, "relative_joint_state", 3, joint, "FULL_COVARIANCE") for joint in joints)
    blocks.extend(StateBlock(f"kf:{stamp}:joint_rate:{joint}", time_s, "relative_joint_rate", 3, joint, "FULL_COVARIANCE") for joint in joints)
    blocks.extend(StateBlock(f"kf:{stamp}:gyro_bias:{node}", time_s, "independent_gyro_bias", 3, node, "FULL_COVARIANCE") for node in imu_nodes)
    blocks.extend(StateBlock(f"kf:{stamp}:accel_bias:{node}", time_s, "independent_accelerometer_bias", 3, node, "FULL_COVARIANCE") for node in imu_nodes)
    return tuple(blocks)
