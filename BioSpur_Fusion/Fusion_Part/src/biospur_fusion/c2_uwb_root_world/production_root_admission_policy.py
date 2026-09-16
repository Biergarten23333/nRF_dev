"""Typed production root-admission policy and an integration-free atomic owner.

This module does not decode ranges, solve a root, propagate IMU, or choose any
numeric policy.  It consumes already-prepared same-epoch evidence and owns the
smallest state transition that can be composed into a production pipeline.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import hmac
import json
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

import numpy as np

from biospur_fusion.root_r3.models import RootState


PRODUCTION_ROOT_ADMISSION_SCHEMA = "biospur.c2.production_root_admission_policy.v1"
STATISTICAL_OWNER_SCHEMA = "biospur.c2.root_statistical_consistency_owner.v1"
CONTINUITY_OWNER_SCHEMA = "biospur.c2.root_atomic_continuity_owner.v1"
REANCHOR_OWNER_SCHEMA = "biospur.c2.root_controlled_reanchor_owner.v1"
STATISTICAL_ROLE = "PREPARED_UWB_INNOVATION_CONSISTENCY"
CONTINUITY_ROLE = "SAME_EPOCH_MEASUREMENT_INFLUENCE_CONTINUITY"
REANCHOR_ROLE = "CAUSAL_FRESH_EPOCH_REANCHOR"
QUALIFIED = "PRODUCTION_QUALIFIED"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

# Intentionally empty until an independently approved registry artifact is
# pinned in source.  Ordinary constructor data can never promote an owner.
APPROVED_PRODUCTION_ROOT_ADMISSION_REGISTRY_SHA256: str | None = None


class _ProductionIssuanceToken:
    pass


_PRODUCTION_ISSUANCE = _ProductionIssuanceToken()


def _hash64(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _plain(value):
    if isinstance(value, np.ndarray):
        return {
            "dtype": value.dtype.str,
            "shape": value.shape,
            "bytes": value.tobytes().hex(),
        }
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, np.generic):
        return value.item()
    return value


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        _plain(value), sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _readonly(value: object, shape: tuple[int, ...]) -> np.ndarray:
    result = np.asarray(value, dtype=float).copy()
    if result.shape != shape or not np.isfinite(result).all():
        raise ValueError(f"expected finite array with shape {shape}")
    result.setflags(write=False)
    return result


def _qualification_fields(
    *, schema: str, expected_schema: str, role: str, expected_role: str,
    derivation_path: str, derivation_sha256: str, source_artifact_sha256: str,
    independent_acceptance_sha256: str, qualification_status: str,
) -> None:
    if (
        schema != expected_schema or role != expected_role
        or not derivation_path.strip()
        or any(not _hash64(value) for value in (
            derivation_sha256, source_artifact_sha256,
            independent_acceptance_sha256,
        ))
        or qualification_status != QUALIFIED
    ):
        raise ValueError("invalid typed production-policy qualification")


@dataclass(frozen=True)
class StatisticalConsistencyOwner:
    schema: str
    role: str
    false_admission_probability: float
    nis_thresholds_by_dof: tuple[tuple[int, float], ...]
    minimum_geometry_rank: int
    derivation_path: str
    derivation_sha256: str
    source_artifact_sha256: str
    independent_acceptance_sha256: str
    qualification_status: str
    _issuance: object
    digest: str = ""

    def __post_init__(self) -> None:
        if self._issuance is not _PRODUCTION_ISSUANCE:
            raise ValueError("production owner was not issued by the pinned registry loader")
        _qualification_fields(
            schema=self.schema, expected_schema=STATISTICAL_OWNER_SCHEMA,
            role=self.role, expected_role=STATISTICAL_ROLE,
            derivation_path=self.derivation_path,
            derivation_sha256=self.derivation_sha256,
            source_artifact_sha256=self.source_artifact_sha256,
            independent_acceptance_sha256=self.independent_acceptance_sha256,
            qualification_status=self.qualification_status,
        )
        if not 0.0 < float(self.false_admission_probability) < 1.0:
            raise ValueError("false-admission probability must lie in (0,1)")
        thresholds = tuple(
            (int(dof), float(threshold))
            for dof, threshold in self.nis_thresholds_by_dof
        )
        if (
            not thresholds
            or len({dof for dof, _ in thresholds}) != len(thresholds)
            or any(dof < 1 or not math.isfinite(threshold) or threshold <= 0.0
                   for dof, threshold in thresholds)
            or tuple(sorted(thresholds)) != thresholds
            or isinstance(self.minimum_geometry_rank, bool)
            or self.minimum_geometry_rank < 1
        ):
            raise ValueError("invalid digest-bound DoF/NIS threshold table")
        object.__setattr__(self, "nis_thresholds_by_dof", thresholds)
        value = _digest({
            key: item for key, item in asdict(self).items()
            if key not in ("digest", "_issuance")
        })
        if self.digest and not hmac.compare_digest(self.digest, value):
            raise ValueError("statistical owner digest mismatch")
        object.__setattr__(self, "digest", value)

    @property
    def production_qualified(self) -> bool:
        return True

    def threshold_for_dof(self, degrees_of_freedom: int) -> float | None:
        return dict(self.nis_thresholds_by_dof).get(degrees_of_freedom)


@dataclass(frozen=True)
class AtomicContinuityOwner:
    schema: str
    role: str
    prediction_horizons_s: tuple[float, ...]
    maximum_position_effect_m: tuple[float, ...]
    transition_model_sha256: str
    derivation_path: str
    derivation_sha256: str
    source_artifact_sha256: str
    independent_acceptance_sha256: str
    qualification_status: str
    _issuance: object
    digest: str = ""

    def __post_init__(self) -> None:
        if self._issuance is not _PRODUCTION_ISSUANCE:
            raise ValueError("production owner was not issued by the pinned registry loader")
        _qualification_fields(
            schema=self.schema, expected_schema=CONTINUITY_OWNER_SCHEMA,
            role=self.role, expected_role=CONTINUITY_ROLE,
            derivation_path=self.derivation_path,
            derivation_sha256=self.derivation_sha256,
            source_artifact_sha256=self.source_artifact_sha256,
            independent_acceptance_sha256=self.independent_acceptance_sha256,
            qualification_status=self.qualification_status,
        )
        horizons = tuple(float(value) for value in self.prediction_horizons_s)
        limits = tuple(float(value) for value in self.maximum_position_effect_m)
        if (
            not horizons or len(horizons) != len(limits)
            or horizons[0] != 0.0
            or any(not math.isfinite(value) or value < 0.0 for value in horizons)
            or any(right <= left for left, right in zip(horizons, horizons[1:]))
            or any(not math.isfinite(value) or value <= 0.0 for value in limits)
            or not _hash64(self.transition_model_sha256)
        ):
            raise ValueError("invalid continuity horizons or limits")
        object.__setattr__(self, "prediction_horizons_s", horizons)
        object.__setattr__(self, "maximum_position_effect_m", limits)
        value = _digest({
            key: item for key, item in asdict(self).items()
            if key not in ("digest", "_issuance")
        })
        if self.digest and not hmac.compare_digest(self.digest, value):
            raise ValueError("continuity owner digest mismatch")
        object.__setattr__(self, "digest", value)

    @property
    def production_qualified(self) -> bool:
        return True


@dataclass(frozen=True)
class ControlledReanchorOwner:
    schema: str
    role: str
    imu_period_s: float
    uwb_period_s: float
    minimum_consecutive_credible_epochs: int
    maximum_credible_gap_s: float
    total_body_nodes: int
    minimum_anchor_links_for_one_node: int
    temporal_model_sha256: str
    derivation_path: str
    derivation_sha256: str
    source_artifact_sha256: str
    independent_acceptance_sha256: str
    qualification_status: str
    _issuance: object
    digest: str = ""

    def __post_init__(self) -> None:
        if self._issuance is not _PRODUCTION_ISSUANCE:
            raise ValueError("production owner was not issued by the pinned registry loader")
        _qualification_fields(
            schema=self.schema, expected_schema=REANCHOR_OWNER_SCHEMA,
            role=self.role, expected_role=REANCHOR_ROLE,
            derivation_path=self.derivation_path,
            derivation_sha256=self.derivation_sha256,
            source_artifact_sha256=self.source_artifact_sha256,
            independent_acceptance_sha256=self.independent_acceptance_sha256,
            qualification_status=self.qualification_status,
        )
        if (
            not math.isclose(float(self.imu_period_s), 0.005, rel_tol=0.0, abs_tol=1e-15)
            or not math.isclose(float(self.uwb_period_s), 0.12, rel_tol=0.0, abs_tol=1e-15)
            or isinstance(self.minimum_consecutive_credible_epochs, bool)
            or self.minimum_consecutive_credible_epochs < 1
            or not math.isfinite(float(self.maximum_credible_gap_s))
            or self.maximum_credible_gap_s < self.uwb_period_s
            or self.total_body_nodes != 10
            or self.minimum_anchor_links_for_one_node < 4
            or not _hash64(self.temporal_model_sha256)
        ):
            raise ValueError("invalid controlled-reanchor owner")
        value = _digest({
            key: item for key, item in asdict(self).items()
            if key not in ("digest", "_issuance")
        })
        if self.digest and not hmac.compare_digest(self.digest, value):
            raise ValueError("reanchor owner digest mismatch")
        object.__setattr__(self, "digest", value)

    @property
    def production_qualified(self) -> bool:
        return True


@dataclass(frozen=True)
class ProductionRootAdmissionPolicy:
    schema: str
    statistical: StatisticalConsistencyOwner
    continuity: AtomicContinuityOwner
    reanchor: ControlledReanchorOwner
    digest: str = ""

    def __post_init__(self) -> None:
        if (
            self.schema != PRODUCTION_ROOT_ADMISSION_SCHEMA
            or type(self.statistical) is not StatisticalConsistencyOwner
            or type(self.continuity) is not AtomicContinuityOwner
            or type(self.reanchor) is not ControlledReanchorOwner
            or not all(owner.production_qualified for owner in (
                self.statistical, self.continuity, self.reanchor,
            ))
            or len({
                self.statistical.independent_acceptance_sha256,
                self.continuity.independent_acceptance_sha256,
                self.reanchor.independent_acceptance_sha256,
            }) != 3
        ):
            raise ValueError("production root-admission policy is unqualified")
        value = _digest({
            "schema": self.schema,
            "statistical_owner": self.statistical.digest,
            "continuity_owner": self.continuity.digest,
            "reanchor_owner": self.reanchor.digest,
        })
        if self.digest and not hmac.compare_digest(self.digest, value):
            raise ValueError("production root-admission policy digest mismatch")
        object.__setattr__(self, "digest", value)


def load_production_root_admission_policy(
    registry_path: str | Path,
) -> ProductionRootAdmissionPolicy:
    """Load the sole production type through a source-pinned registry.

    The pin is deliberately absent today.  Adding it is a separately reviewed
    source change; arbitrary hashes/status strings cannot mint qualification.
    """
    if APPROVED_PRODUCTION_ROOT_ADMISSION_REGISTRY_SHA256 is None:
        raise RuntimeError("PRODUCTION_ROOT_ADMISSION_REGISTRY_EMPTY")
    payload = Path(registry_path).read_bytes()
    actual = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(
        actual, APPROVED_PRODUCTION_ROOT_ADMISSION_REGISTRY_SHA256,
    ):
        raise RuntimeError("FOREIGN_PRODUCTION_ROOT_ADMISSION_REGISTRY")
    document = json.loads(payload)
    if document.get("schema") != PRODUCTION_ROOT_ADMISSION_SCHEMA:
        raise RuntimeError("INVALID_PRODUCTION_ROOT_ADMISSION_REGISTRY_SCHEMA")
    statistical = StatisticalConsistencyOwner(
        **document["statistical"], _issuance=_PRODUCTION_ISSUANCE,
    )
    continuity = AtomicContinuityOwner(
        **document["continuity"], _issuance=_PRODUCTION_ISSUANCE,
    )
    reanchor = ControlledReanchorOwner(
        **document["reanchor"], _issuance=_PRODUCTION_ISSUANCE,
    )
    return ProductionRootAdmissionPolicy(
        document["schema"], statistical, continuity, reanchor,
        document.get("digest", ""),
    )


@dataclass(frozen=True)
class DiagnosticRootAdmissionFixture:
    schema: str
    provenance: str
    payload_digest: str

    def __post_init__(self) -> None:
        if (
            self.schema != "biospur.c2.diagnostic_root_admission_fixture.v1"
            or not self.provenance.strip() or not _hash64(self.payload_digest)
        ):
            raise ValueError("invalid diagnostic fixture")

    @property
    def production_qualified(self) -> bool:
        return False

    @property
    def product_ready(self) -> bool:
        return False

    @property
    def scientific_pass(self) -> bool:
        return False


@dataclass(frozen=True)
class DiagnosticStatisticalSpec:
    false_admission_probability: float
    nis_thresholds_by_dof: tuple[tuple[int, float], ...]
    minimum_geometry_rank: int
    digest: str = ""

    def __post_init__(self) -> None:
        thresholds = tuple((int(dof), float(value)) for dof, value in self.nis_thresholds_by_dof)
        if (
            not 0.0 < float(self.false_admission_probability) < 1.0
            or not thresholds
            or tuple(sorted(thresholds)) != thresholds
            or len({dof for dof, _ in thresholds}) != len(thresholds)
            or isinstance(self.minimum_geometry_rank, bool)
            or self.minimum_geometry_rank < 1
            or any(dof < 1 or not math.isfinite(value) or value <= 0.0 for dof, value in thresholds)
        ):
            raise ValueError("invalid diagnostic statistical fixture")
        object.__setattr__(self, "nis_thresholds_by_dof", thresholds)
        object.__setattr__(self, "digest", _digest({
            "diagnostic_only": True,
            "false_admission_probability": self.false_admission_probability,
            "nis_thresholds_by_dof": thresholds,
            "minimum_geometry_rank": self.minimum_geometry_rank,
        }))

    def threshold_for_dof(self, degrees_of_freedom: int) -> float | None:
        return dict(self.nis_thresholds_by_dof).get(degrees_of_freedom)


@dataclass(frozen=True)
class DiagnosticContinuitySpec:
    prediction_horizons_s: tuple[float, ...]
    maximum_position_effect_m: tuple[float, ...]
    transition_model_sha256: str
    digest: str = ""

    def __post_init__(self) -> None:
        horizons = tuple(float(item) for item in self.prediction_horizons_s)
        limits = tuple(float(item) for item in self.maximum_position_effect_m)
        if (
            not horizons or horizons[0] != 0.0 or len(horizons) != len(limits)
            or any(right <= left for left, right in zip(horizons, horizons[1:]))
            or any(not math.isfinite(item) or item <= 0.0 for item in limits)
            or not _hash64(self.transition_model_sha256)
        ):
            raise ValueError("invalid diagnostic continuity fixture")
        object.__setattr__(self, "prediction_horizons_s", horizons)
        object.__setattr__(self, "maximum_position_effect_m", limits)
        object.__setattr__(self, "digest", _digest({
            "diagnostic_only": True,
            "prediction_horizons_s": horizons,
            "maximum_position_effect_m": limits,
            "transition_model_sha256": self.transition_model_sha256,
        }))


@dataclass(frozen=True)
class DiagnosticReanchorSpec:
    imu_period_s: float
    uwb_period_s: float
    minimum_consecutive_credible_epochs: int
    maximum_credible_gap_s: float
    total_body_nodes: int
    minimum_anchor_links_for_one_node: int
    digest: str = ""

    def __post_init__(self) -> None:
        if (
            not math.isclose(float(self.imu_period_s), 0.005, rel_tol=0.0, abs_tol=1e-15)
            or not math.isclose(float(self.uwb_period_s), 0.12, rel_tol=0.0, abs_tol=1e-15)
            or self.minimum_consecutive_credible_epochs < 1
            or self.maximum_credible_gap_s < self.uwb_period_s
            or self.total_body_nodes != 10
            or self.minimum_anchor_links_for_one_node < 4
        ):
            raise ValueError("invalid diagnostic reanchor fixture")
        object.__setattr__(self, "digest", _digest({
            "diagnostic_only": True,
            "imu_period_s": self.imu_period_s,
            "uwb_period_s": self.uwb_period_s,
            "minimum_consecutive_credible_epochs": self.minimum_consecutive_credible_epochs,
            "maximum_credible_gap_s": self.maximum_credible_gap_s,
            "total_body_nodes": self.total_body_nodes,
            "minimum_anchor_links_for_one_node": self.minimum_anchor_links_for_one_node,
        }))


@dataclass(frozen=True)
class DiagnosticRootAdmissionPolicy:
    statistical: DiagnosticStatisticalSpec
    continuity: DiagnosticContinuitySpec
    reanchor: DiagnosticReanchorSpec
    provenance: str
    digest: str = ""

    def __post_init__(self) -> None:
        if (
            type(self.statistical) is not DiagnosticStatisticalSpec
            or type(self.continuity) is not DiagnosticContinuitySpec
            or type(self.reanchor) is not DiagnosticReanchorSpec
            or not self.provenance.strip()
        ):
            raise ValueError("invalid diagnostic policy")
        object.__setattr__(self, "digest", _digest({
            "schema": "biospur.c2.diagnostic_root_admission_policy.v1",
            "statistical": self.statistical.digest,
            "continuity": self.continuity.digest,
            "reanchor": self.reanchor.digest,
            "provenance": self.provenance,
            "product_ready": False,
            "scientific_pass": False,
        }))

    @property
    def product_ready(self) -> bool:
        return False

    @property
    def scientific_pass(self) -> bool:
        return False


@dataclass(frozen=True)
class PreparedStatisticalInput:
    innovation: np.ndarray
    innovation_covariance: np.ndarray
    degrees_of_freedom: int
    false_admission_probability: float
    nis_threshold: float
    effective_geometry_rank: int
    geometry_valid: bool
    measurement_integrity_valid: bool
    covariance_model_valid: bool
    source_digest: str

    def __post_init__(self) -> None:
        if isinstance(self.degrees_of_freedom, bool) or self.degrees_of_freedom < 1:
            raise ValueError("invalid statistical degrees of freedom")
        innovation = _readonly(self.innovation, (self.degrees_of_freedom,))
        covariance = _readonly(
            self.innovation_covariance,
            (self.degrees_of_freedom, self.degrees_of_freedom),
        )
        if (
            not np.allclose(covariance, covariance.T, rtol=0.0, atol=1e-12)
            or np.min(np.linalg.eigvalsh(covariance)) <= 0.0
            or not 0.0 < float(self.false_admission_probability) < 1.0
            or not math.isfinite(float(self.nis_threshold))
            or self.nis_threshold <= 0.0
            or isinstance(self.effective_geometry_rank, bool)
            or not 1 <= self.effective_geometry_rank <= self.degrees_of_freedom
            or type(self.geometry_valid) is not bool
            or type(self.measurement_integrity_valid) is not bool
            or type(self.covariance_model_valid) is not bool
            or not _hash64(self.source_digest)
        ):
            raise ValueError("invalid prepared statistical input")
        object.__setattr__(self, "innovation", innovation)
        object.__setattr__(self, "innovation_covariance", covariance)

    @property
    def nis(self) -> float:
        return float(self.innovation @ np.linalg.solve(
            self.innovation_covariance, self.innovation,
        ))


@dataclass(frozen=True)
class PreparedContinuityInput:
    prediction_horizons_s: tuple[float, ...]
    position_effect_m: np.ndarray
    transition_model_sha256: str
    source_digest: str

    def __post_init__(self) -> None:
        horizons = tuple(float(value) for value in self.prediction_horizons_s)
        effects = _readonly(self.position_effect_m, (len(horizons), 3))
        if not horizons or any(not math.isfinite(value) for value in horizons) or any(
            not _hash64(value) for value in (self.transition_model_sha256, self.source_digest)
        ):
            raise ValueError("invalid prepared continuity input")
        object.__setattr__(self, "prediction_horizons_s", horizons)
        object.__setattr__(self, "position_effect_m", effects)


@dataclass(frozen=True)
class PreparedRootAdmissionInput:
    event_id: str
    event_sequence: int
    measurement_time_s: float
    availability_time_s: float
    imu_prediction: RootState
    measurement_candidate: RootState
    bounded_reanchor_candidate: RootState
    statistical: PreparedStatisticalInput
    continuity: PreparedContinuityInput
    bounded_reanchor_continuity: PreparedContinuityInput
    node_inventory: tuple[str, ...]
    trusted_nodes: tuple[str, ...]
    valid_anchor_links_by_node: Mapping[str, int]
    root_translation_only: bool
    source_digest: str
    digest: str = ""

    def __post_init__(self) -> None:
        nodes = tuple(self.node_inventory)
        trusted = tuple(self.trusted_nodes)
        counts = {str(key): int(value) for key, value in self.valid_anchor_links_by_node.items()}
        if (
            not self.event_id or isinstance(self.event_sequence, bool)
            or self.event_sequence < 0
            or not math.isfinite(float(self.measurement_time_s))
            or not math.isfinite(float(self.availability_time_s))
            or self.availability_time_s < self.measurement_time_s
            or type(self.imu_prediction) is not RootState
            or type(self.measurement_candidate) is not RootState
            or type(self.bounded_reanchor_candidate) is not RootState
            or self.imu_prediction.time_s != self.measurement_time_s
            or self.measurement_candidate.time_s != self.measurement_time_s
            or self.bounded_reanchor_candidate.time_s != self.measurement_time_s
            or len(nodes) != 10 or len(set(nodes)) != 10
            or not trusted or len(set(trusted)) != len(trusted)
            or set(trusted) - set(nodes) or set(counts) != set(nodes)
            or any(value < 0 for value in counts.values())
            or type(self.root_translation_only) is not bool
            or not _hash64(self.source_digest)
        ):
            raise ValueError("invalid prepared root-admission identity")
        prediction = _frozen_state(self.imu_prediction)
        candidate = _frozen_state(self.measurement_candidate)
        bounded_candidate = _frozen_state(self.bounded_reanchor_candidate)
        if not np.array_equal(
            self.continuity.position_effect_m[0],
            candidate.vector[:3] - prediction.vector[:3],
        ):
            raise ValueError("continuity input is not candidate versus same-epoch prediction")
        if not np.array_equal(
            self.bounded_reanchor_continuity.position_effect_m[0],
            bounded_candidate.vector[:3] - prediction.vector[:3],
        ):
            raise ValueError("bounded reanchor is not versus same-epoch prediction")
        object.__setattr__(self, "imu_prediction", prediction)
        object.__setattr__(self, "measurement_candidate", candidate)
        object.__setattr__(self, "bounded_reanchor_candidate", bounded_candidate)
        object.__setattr__(self, "node_inventory", nodes)
        object.__setattr__(self, "trusted_nodes", trusted)
        value = _digest({
            "event_id": self.event_id,
            "event_sequence": self.event_sequence,
            "measurement_time_s": self.measurement_time_s,
            "availability_time_s": self.availability_time_s,
            "imu_prediction": asdict(prediction),
            "measurement_candidate": asdict(candidate),
            "bounded_reanchor_candidate": asdict(bounded_candidate),
            "statistical": asdict(self.statistical),
            "continuity": asdict(self.continuity),
            "bounded_reanchor_continuity": asdict(
                self.bounded_reanchor_continuity,
            ),
            "node_inventory": nodes,
            "trusted_nodes": trusted,
            "valid_anchor_links_by_node": counts,
            "root_translation_only": self.root_translation_only,
            "source_digest": self.source_digest,
        })
        if self.digest and not hmac.compare_digest(self.digest, value):
            raise ValueError("prepared root-admission input digest mismatch")
        object.__setattr__(
            self, "valid_anchor_links_by_node", MappingProxyType(counts),
        )
        object.__setattr__(self, "digest", value)


def _frozen_state(state: RootState) -> RootState:
    vector = _readonly(state.vector, (9,))
    covariance = _readonly(state.covariance, (9, 9))
    if not np.allclose(covariance, covariance.T, rtol=0.0, atol=1e-12):
        raise ValueError("root covariance is not symmetric")
    return RootState(float(state.time_s), vector, covariance)


class AdmissionDisposition(str, Enum):
    REJECT_NO_EVENT = "REJECT_NO_EVENT"
    HOLD_TEMPORAL_EVIDENCE = "HOLD_TEMPORAL_EVIDENCE"
    ACCEPT_COMMIT_ROOT = "ACCEPT_COMMIT_ROOT"


@dataclass(frozen=True)
class RootAdmissionDecision:
    disposition: AdmissionDisposition
    reason: str
    nis: float
    temporal_count: int
    direct_nodes: tuple[str, ...]
    propagated_nodes: tuple[str, ...]
    input_digest: str


@dataclass(frozen=True)
class _MachineState:
    root_state: RootState
    revision: int
    last_consumed_measurement_time_s: float | None
    last_credible_measurement_time_s: float | None
    credible_run_length: int
    digest: str = ""

    def __post_init__(self) -> None:
        if (
            isinstance(self.revision, bool) or self.revision < 0
            or isinstance(self.credible_run_length, bool) or self.credible_run_length < 0
            or any(value is not None and not math.isfinite(float(value)) for value in (
                self.last_consumed_measurement_time_s,
                self.last_credible_measurement_time_s,
            ))
        ):
            raise ValueError("invalid root-admission machine state")
        object.__setattr__(self, "root_state", _frozen_state(self.root_state))
        value = _digest({key: item for key, item in asdict(self).items() if key != "digest"})
        if self.digest and not hmac.compare_digest(self.digest, value):
            raise ValueError("root-admission state digest mismatch")
        object.__setattr__(self, "digest", value)


@dataclass(frozen=True)
class RootAdmissionSnapshot:
    owner_key: object
    policy_digest: str
    state: _MachineState


@dataclass
class _OneShot:
    consumed: bool


@dataclass(frozen=True)
class PreparedRootAdmission:
    owner_key: object
    base_state_digest: str
    decision: RootAdmissionDecision
    next_state: _MachineState | None
    one_shot: _OneShot


class _RootAdmissionMachine:
    """Pure prepare plus assignment-only commit for already-prepared evidence."""

    def __init__(self, policy, initial_state: RootState):
        self.policy = policy
        self.__owner_key = object()
        self._state = _MachineState(_frozen_state(initial_state), 0, None, None, 0)

    @property
    def current_state(self) -> RootState:
        return _frozen_state(self._state.root_state)

    @property
    def revision(self) -> int:
        return self._state.revision

    def owner_bytes(self) -> bytes:
        return json.dumps({
            "policy": self.policy.digest,
            "state": self._state.digest,
        }, sort_keys=True, separators=(",", ":")).encode()

    def snapshot(self) -> RootAdmissionSnapshot:
        return RootAdmissionSnapshot(self.__owner_key, self.policy.digest, self._state)

    def clone(self):
        result = type(self)(self.policy, self._state.root_state)
        result._state = _MachineState(
            self._state.root_state,
            self._state.revision,
            self._state.last_consumed_measurement_time_s,
            self._state.last_credible_measurement_time_s,
            self._state.credible_run_length,
        )
        return result

    def rollback(self, snapshot: RootAdmissionSnapshot) -> None:
        if (
            type(snapshot) is not RootAdmissionSnapshot
            or snapshot.owner_key is not self.__owner_key
            or snapshot.policy_digest != self.policy.digest
        ):
            raise RuntimeError("FOREIGN_ROOT_ADMISSION_ROLLBACK")
        self._state = _MachineState(
            snapshot.state.root_state,
            snapshot.state.revision,
            snapshot.state.last_consumed_measurement_time_s,
            snapshot.state.last_credible_measurement_time_s,
            snapshot.state.credible_run_length,
        )

    def _reject(self, value: PreparedRootAdmissionInput, reason: str, nis: float) -> PreparedRootAdmission:
        decision = RootAdmissionDecision(
            AdmissionDisposition.REJECT_NO_EVENT, reason, nis,
            self._state.credible_run_length, (), (), value.digest,
        )
        return PreparedRootAdmission(
            self.__owner_key, self._state.digest, decision, None, _OneShot(False),
        )

    def prepare(self, value: PreparedRootAdmissionInput) -> PreparedRootAdmission:
        if type(value) is not PreparedRootAdmissionInput:
            raise TypeError("prepared root-admission input required")
        statistical = value.statistical
        continuity = value.continuity
        bounded_continuity = value.bounded_reanchor_continuity
        nis = statistical.nis
        owned_threshold = self.policy.statistical.threshold_for_dof(
            statistical.degrees_of_freedom,
        )
        if (
            value.source_digest != self.policy.digest
            or statistical.source_digest != self.policy.statistical.digest
            or continuity.source_digest != self.policy.continuity.digest
            or bounded_continuity.source_digest != self.policy.continuity.digest
            or statistical.false_admission_probability
            != self.policy.statistical.false_admission_probability
            or owned_threshold is None
            or statistical.nis_threshold != owned_threshold
            or statistical.effective_geometry_rank
            < self.policy.statistical.minimum_geometry_rank
            or not statistical.geometry_valid
            or not statistical.measurement_integrity_valid
            or not statistical.covariance_model_valid
            or nis > statistical.nis_threshold
        ):
            return self._reject(value, "STATISTICAL_OR_MEASUREMENT_INTEGRITY_REJECT", nis)
        continuity_identity_valid = (
            continuity.prediction_horizons_s
            == self.policy.continuity.prediction_horizons_s
            and continuity.transition_model_sha256
            == self.policy.continuity.transition_model_sha256
        )
        if not continuity_identity_valid:
            return self._reject(value, "FOREIGN_CONTINUITY_EVIDENCE_REJECT", nis)
        normal_continuity_pass = all(
            float(np.linalg.norm(effect)) <= limit
            for effect, limit in zip(
                continuity.position_effect_m,
                self.policy.continuity.maximum_position_effect_m,
            )
        )
        if any(
            value.valid_anchor_links_by_node[node]
            < self.policy.reanchor.minimum_anchor_links_for_one_node
            for node in value.trusted_nodes
        ):
            return self._reject(value, "TRUSTED_NODE_LINK_GEOMETRY_REJECT", nis)
        if len(value.trusted_nodes) == 1 and (
            not value.root_translation_only
            or not np.array_equal(
                value.measurement_candidate.vector[3:],
                value.imu_prediction.vector[3:],
            )
            or not np.array_equal(
                value.bounded_reanchor_candidate.vector[3:],
                value.imu_prediction.vector[3:],
            )
        ):
            return self._reject(value, "ONE_NODE_ROOT_ONLY_INTEGRITY_REJECT", nis)
        if (
            self._state.last_consumed_measurement_time_s is not None
            and value.measurement_time_s <= self._state.last_consumed_measurement_time_s
        ):
            return self._reject(value, "STALE_OR_REPLAYED_EPOCH", nis)
        direct = tuple(sorted(value.trusted_nodes))
        propagated = tuple(sorted(set(value.node_inventory) - set(direct)))
        if normal_continuity_pass and len(direct) > 1:
            decision = RootAdmissionDecision(
                AdmissionDisposition.ACCEPT_COMMIT_ROOT,
                "STATISTICALLY_CREDIBLE_ATOMIC_UPDATE_COMMITTED",
                nis, 0, direct, propagated, value.digest,
            )
            next_state = _MachineState(
                value.measurement_candidate, self._state.revision + 1,
                value.measurement_time_s, None, 0,
            )
            return PreparedRootAdmission(
                self.__owner_key, self._state.digest, decision, next_state,
                _OneShot(False),
            )
        if (
            bounded_continuity.prediction_horizons_s
            != self.policy.continuity.prediction_horizons_s
            or bounded_continuity.transition_model_sha256
            != self.policy.continuity.transition_model_sha256
            or any(
                float(np.linalg.norm(effect)) > limit
                for effect, limit in zip(
                    bounded_continuity.position_effect_m,
                    self.policy.continuity.maximum_position_effect_m,
                )
            )
        ):
            return self._reject(value, "CONTROLLED_REANCHOR_CONTINUITY_REJECT", nis)
        previous_credible = self._state.last_credible_measurement_time_s
        count = self._state.credible_run_length
        if (
            previous_credible is None
            or value.measurement_time_s - previous_credible
            > self.policy.reanchor.maximum_credible_gap_s
        ):
            count = 0
        count += 1
        if count < self.policy.reanchor.minimum_consecutive_credible_epochs:
            decision = RootAdmissionDecision(
                AdmissionDisposition.HOLD_TEMPORAL_EVIDENCE,
                "CREDIBLE_EPOCH_HELD_FOR_CAUSAL_REANCHOR",
                nis, count, direct, propagated, value.digest,
            )
            next_state = _MachineState(
                self._state.root_state, self._state.revision + 1,
                value.measurement_time_s, value.measurement_time_s, count,
            )
        else:
            decision = RootAdmissionDecision(
                AdmissionDisposition.ACCEPT_COMMIT_ROOT,
                "FRESH_CAUSAL_BOUNDED_REANCHOR_COMMITTED",
                nis, count, direct, propagated, value.digest,
            )
            next_state = _MachineState(
                value.bounded_reanchor_candidate, self._state.revision + 1,
                value.measurement_time_s, None, 0,
            )
        return PreparedRootAdmission(
            self.__owner_key, self._state.digest, decision, next_state, _OneShot(False),
        )

    def commit(self, prepared: PreparedRootAdmission) -> RootAdmissionDecision:
        if (
            type(prepared) is not PreparedRootAdmission
            or prepared.owner_key is not self.__owner_key
            or prepared.one_shot.consumed
            or prepared.base_state_digest != self._state.digest
        ):
            raise RuntimeError("STALE_REPLAYED_OR_FOREIGN_ROOT_ADMISSION")
        prepared.one_shot.consumed = True
        if prepared.next_state is not None:
            self._state = prepared.next_state
        return prepared.decision


class ProductionRootAdmissionMachine(_RootAdmissionMachine):
    def __init__(self, policy: ProductionRootAdmissionPolicy, initial_state: RootState):
        if type(policy) is not ProductionRootAdmissionPolicy:
            raise TypeError("machine requires a registry-issued production policy")
        super().__init__(policy, initial_state)


class DiagnosticRootAdmissionMachine(_RootAdmissionMachine):
    def __init__(self, policy: DiagnosticRootAdmissionPolicy, initial_state: RootState):
        if type(policy) is not DiagnosticRootAdmissionPolicy:
            raise TypeError("diagnostic machine requires diagnostic-only policy")
        super().__init__(policy, initial_state)

    @property
    def product_ready(self) -> bool:
        return False

    @property
    def scientific_pass(self) -> bool:
        return False
