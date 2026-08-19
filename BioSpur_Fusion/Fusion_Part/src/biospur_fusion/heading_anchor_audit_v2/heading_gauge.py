"""Typed heading-gauge state and fail-closed semantic boundaries.

The canonical state is ``(K_PROTOCOL_RELATIVE, PSI_PROTOCOL_TO_COMMON)``.
Common-frame H is deliberately a derived view and is never accepted as an
independently writable constructor field.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from types import MappingProxyType
from typing import Mapping, Sequence

import numpy as np


HEADING_GAUGE_SEMANTIC_VERSION = "biospur.phase3.heading_gauge_state.v1"
HEADING_GAUGE_CACHE_KEY = "heading_gauge_state_v1"
R23_MIGRATION_ID = "r23_psi0_representative_to_k_v1"
R23_SOURCE_SCHEMA = "biospur-phase3r23-prevalidation-session-static-heading-candidate-v1"
AUTHORIZED_R23_SOURCE_SHA256 = (
    "3d375378028561b7cc225f53b0afd4ca8878857f1a653e7df51b6357b2cdd0c3"
)
WRAP_CONVENTION = "[-pi,pi)"
BRANCH_EVALUATION_SEMANTIC_VERSION = "biospur.phase3.heading_branch_evaluation.v1"
FUTURE_CANDIDATE_SCHEMA = "biospur.phase3.heading_candidate.v2"

INVALIDATED_R26_DERIVED_CACHE_FIELDS = (
    "branch_bits",
    "branch_scores",
    "branch_ordering",
    "margins",
    "interval_centres",
    "first_motion_branch_status",
    "candidate",
    "candidate_sha_association",
)
INHERITABLE_GAUGE_INDEPENDENT_CACHE_FIELDS = (
    "gauge_independent_family_block_counts",
    "deficit_counts",
    "paired_bootstrap_raw_samples",
    "k_derived_bootstrap_half_widths",
)


class HeadingGaugeValidationError(ValueError):
    """A typed heading-gauge contract was not satisfied."""


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _wrap_2pi_scalar(value: float) -> float:
    wrapped = (float(value) + math.pi) % (2.0 * math.pi) - math.pi
    return -math.pi if wrapped == math.pi else wrapped


def _require_canonical_radian(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise HeadingGaugeValidationError(f"{field} must be a finite radian float")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise HeadingGaugeValidationError(f"{field} must be a finite radian float") from exc
    if not math.isfinite(result):
        raise HeadingGaugeValidationError(f"{field} must be finite")
    if result < -math.pi or result >= math.pi:
        raise HeadingGaugeValidationError(
            f"{field} is not canonical radians in {WRAP_CONVENTION}; degrees are forbidden"
        )
    return result


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise HeadingGaugeValidationError(f"{field} must be a nonempty SHA-256 hex string")
    try:
        int(value, 16)
    except ValueError as exc:
        raise HeadingGaugeValidationError(f"{field} must be SHA-256 hex") from exc
    return value.lower()


@dataclass(frozen=True, slots=True, init=False)
class HeadingGaugeState:
    """Immutable canonical K/psi state with read-only common-frame H views."""

    semantic_version: str
    coordinate_order: tuple[str, ...]
    _k_values: tuple[float, ...]
    psi_protocol_to_common_rad: float
    wrap_convention: str
    source_solution_sha256: str
    source_schema: str
    migration_id: str
    semantic_cache_key: str

    def __init__(
        self,
        *,
        coordinate_order: Sequence[str],
        k_protocol_relative_rad_by_coordinate: Mapping[str, float],
        psi_protocol_to_common_rad: float,
        source_solution_sha256: str,
        source_schema: str,
        migration_id: str,
        semantic_version: str = HEADING_GAUGE_SEMANTIC_VERSION,
        wrap_convention: str = WRAP_CONVENTION,
        semantic_cache_key: str = HEADING_GAUGE_CACHE_KEY,
    ) -> None:
        from .core import COORDINATE_ORDER

        order = tuple(coordinate_order)
        if semantic_version != HEADING_GAUGE_SEMANTIC_VERSION:
            raise HeadingGaugeValidationError("unknown heading semantic_version")
        if semantic_cache_key != HEADING_GAUGE_CACHE_KEY:
            raise HeadingGaugeValidationError("stale or unknown semantic cache key")
        if wrap_convention != WRAP_CONVENTION:
            raise HeadingGaugeValidationError("wrap convention must be [-pi,pi)")
        if order != tuple(COORDINATE_ORDER):
            raise HeadingGaugeValidationError("coordinate_order does not exactly match the fixed order")
        if len(set(order)) != len(order):
            raise HeadingGaugeValidationError("duplicate coordinate")
        if not isinstance(k_protocol_relative_rad_by_coordinate, Mapping):
            raise HeadingGaugeValidationError("typed K mapping required")
        if set(k_protocol_relative_rad_by_coordinate) != set(order):
            missing = sorted(set(order) - set(k_protocol_relative_rad_by_coordinate))
            extra = sorted(set(k_protocol_relative_rad_by_coordinate) - set(order))
            raise HeadingGaugeValidationError(
                f"K coordinate set mismatch; missing={missing}, extra={extra}"
            )
        if not isinstance(source_schema, str) or not source_schema:
            raise HeadingGaugeValidationError("source_schema must be nonempty")
        if migration_id != R23_MIGRATION_ID:
            raise HeadingGaugeValidationError("unknown migration_id")
        values = tuple(
            _require_canonical_radian(
                k_protocol_relative_rad_by_coordinate[name],
                f"k_protocol_relative_rad_by_coordinate.{name}",
            )
            for name in order
        )
        object.__setattr__(self, "semantic_version", semantic_version)
        object.__setattr__(self, "coordinate_order", order)
        object.__setattr__(self, "_k_values", values)
        object.__setattr__(
            self,
            "psi_protocol_to_common_rad",
            _require_canonical_radian(
                psi_protocol_to_common_rad, "psi_protocol_to_common_rad"
            ),
        )
        object.__setattr__(self, "wrap_convention", wrap_convention)
        object.__setattr__(
            self,
            "source_solution_sha256",
            _require_sha256(source_solution_sha256, "source_solution_sha256"),
        )
        object.__setattr__(self, "source_schema", source_schema)
        object.__setattr__(self, "migration_id", migration_id)
        object.__setattr__(self, "semantic_cache_key", semantic_cache_key)

    @property
    def k_protocol_relative_rad_by_coordinate(self) -> Mapping[str, float]:
        return MappingProxyType(dict(zip(self.coordinate_order, self._k_values, strict=True)))

    def k_protocol_relative_rad(self, coordinate: str) -> float:
        try:
            return self._k_values[self.coordinate_order.index(coordinate)]
        except ValueError as exc:
            raise HeadingGaugeValidationError(f"unknown coordinate {coordinate!r}") from exc

    def h_common_rad(self, coordinate: str) -> float:
        return _wrap_2pi_scalar(
            self.k_protocol_relative_rad(coordinate)
            + self.psi_protocol_to_common_rad
        )

    @property
    def h_common_rad_by_coordinate(self) -> Mapping[str, float]:
        return MappingProxyType(
            {name: self.h_common_rad(name) for name in self.coordinate_order}
        )

    def R_PI(self, R_EiI: np.ndarray, coordinate: str) -> np.ndarray:
        matrix = _validated_rotation_input(R_EiI)
        return _rz(self.k_protocol_relative_rad(coordinate)) @ matrix

    def R_GI(self, R_EiI: np.ndarray, coordinate: str) -> np.ndarray:
        matrix = _validated_rotation_input(R_EiI)
        return _rz(self.h_common_rad(coordinate)) @ matrix

    def with_common_gauge(self, psi_protocol_to_common_rad: float) -> HeadingGaugeState:
        """Return the same invariant K state at an explicitly supplied gauge psi."""
        return HeadingGaugeState(
            coordinate_order=self.coordinate_order,
            k_protocol_relative_rad_by_coordinate=self.k_protocol_relative_rad_by_coordinate,
            psi_protocol_to_common_rad=psi_protocol_to_common_rad,
            source_solution_sha256=self.source_solution_sha256,
            source_schema=self.source_schema,
            migration_id=self.migration_id,
        )

    def shifted_common_gauge(self, alpha_rad: float) -> HeadingGaugeState:
        alpha = float(alpha_rad)
        if not math.isfinite(alpha):
            raise HeadingGaugeValidationError("gauge shift must be finite radians")
        return self.with_common_gauge(
            _wrap_2pi_scalar(self.psi_protocol_to_common_rad + alpha)
        )

    def with_branch_bits(self, bits: Sequence[int]) -> HeadingGaugeState:
        values = tuple(bits)
        if len(values) != len(self.coordinate_order) or any(bit not in (0, 1) for bit in values):
            raise HeadingGaugeValidationError("branch bits must be nine ordered binary values")
        shifted = {
            name: _wrap_2pi_scalar(self._k_values[index] + math.pi * values[index])
            for index, name in enumerate(self.coordinate_order)
        }
        return HeadingGaugeState(
            coordinate_order=self.coordinate_order,
            k_protocol_relative_rad_by_coordinate=shifted,
            psi_protocol_to_common_rad=self.psi_protocol_to_common_rad,
            source_solution_sha256=self.source_solution_sha256,
            source_schema=self.source_schema,
            migration_id=self.migration_id,
        )

    def to_payload(self) -> dict:
        return {
            "semantic_version": self.semantic_version,
            "coordinate_order": list(self.coordinate_order),
            "k_protocol_relative_rad_by_coordinate": dict(
                self.k_protocol_relative_rad_by_coordinate
            ),
            "psi_protocol_to_common_rad": self.psi_protocol_to_common_rad,
            "wrap_convention": self.wrap_convention,
            "source_solution_sha256": self.source_solution_sha256,
            "source_schema": self.source_schema,
            "migration_id": self.migration_id,
            "semantic_cache_key": self.semantic_cache_key,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_payload())

    def payload_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> HeadingGaugeState:
        expected = {
            "semantic_version",
            "coordinate_order",
            "k_protocol_relative_rad_by_coordinate",
            "psi_protocol_to_common_rad",
            "wrap_convention",
            "source_solution_sha256",
            "source_schema",
            "migration_id",
            "semantic_cache_key",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise HeadingGaugeValidationError("heading state payload fields are not exact")
        return cls(**dict(payload))


def _rz(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.asarray(((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0)))


def _validated_rotation_input(value: np.ndarray) -> np.ndarray:
    matrix = np.asarray(value, dtype=float)
    if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
        raise HeadingGaugeValidationError("R_EiI must be a finite 3x3 matrix")
    return matrix


def migrate_r23_psi_zero_candidate(
    candidate: Mapping[str, object], *, source_solution_sha256: str
) -> HeadingGaugeState:
    """Migrate the one authorized R2.3 psi-zero representative to typed K."""
    if not isinstance(candidate, Mapping):
        raise HeadingGaugeValidationError("legacy source must be a mapping")
    if candidate.get("schema") != R23_SOURCE_SCHEMA:
        raise HeadingGaugeValidationError("unknown legacy source schema")
    if _require_sha256(source_solution_sha256, "source_solution_sha256") != AUTHORIZED_R23_SOURCE_SHA256:
        raise HeadingGaugeValidationError("source SHA mismatch")
    from .core import COORDINATE_ORDER

    order = candidate.get("parameter_order")
    if not isinstance(order, list) or tuple(order) != tuple(COORDINATE_ORDER):
        raise HeadingGaugeValidationError("legacy coordinate order mismatch")
    if len(set(order)) != len(order):
        raise HeadingGaugeValidationError("legacy coordinate order contains duplicates")
    if candidate.get("continuous_psi_orbit") is not True:
        raise HeadingGaugeValidationError("legacy continuous-orbit contract missing")
    symmetries = candidate.get("symmetries")
    if not isinstance(symmetries, list) or "continuous common h_i/psi_GP shift" not in symmetries:
        raise HeadingGaugeValidationError("legacy continuous-orbit provenance missing")
    modes = candidate.get("joint_modes")
    if not isinstance(modes, list) or len(modes) != 2 ** len(order):
        raise HeadingGaugeValidationError("legacy complete 512-mode set required")
    if candidate.get("joint_mode_count") != len(modes):
        raise HeadingGaugeValidationError("legacy joint_mode_count mismatch")
    seen: set[tuple[int, ...]] = set()
    zero_mode: Mapping[str, object] | None = None
    required_orbit = "add common alpha to every h_i and psi_GP for alpha in S1"
    for row in modes:
        if not isinstance(row, Mapping):
            raise HeadingGaugeValidationError("legacy mode must be a mapping")
        if "representative_psi_GP_rad" not in row:
            raise HeadingGaugeValidationError("representative_psi_GP_rad missing")
        psi0 = float(row["representative_psi_GP_rad"])
        if not math.isfinite(psi0) or abs(psi0) > 1e-15:
            raise HeadingGaugeValidationError("representative_psi_GP_rad must be explicitly zero")
        if row.get("continuous_orbit") != required_orbit:
            raise HeadingGaugeValidationError("legacy mode continuous-orbit contract mismatch")
        bits_raw = row.get("pi_branch_bits")
        if not isinstance(bits_raw, list) or len(bits_raw) != len(order) or any(
            bit not in (0, 1) for bit in bits_raw
        ):
            raise HeadingGaugeValidationError("legacy branch bits malformed")
        bits = tuple(int(bit) for bit in bits_raw)
        if bits in seen:
            raise HeadingGaugeValidationError("legacy duplicate branch coordinates")
        seen.add(bits)
        headings = row.get("relative_heading_rad")
        if not isinstance(headings, Mapping) or set(headings) != set(order):
            raise HeadingGaugeValidationError("legacy relative-heading coordinate set mismatch")
        for name in order:
            _require_canonical_radian(headings[name], f"legacy relative_heading_rad.{name}")
        objective = row.get("objective")
        if isinstance(objective, bool) or not math.isfinite(float(objective)):
            raise HeadingGaugeValidationError("legacy objective must be finite")
        if not any(bits):
            zero_mode = row
    if zero_mode is None or len(seen) != 2 ** len(order):
        raise HeadingGaugeValidationError("legacy zero mode or complete coordinate set missing")
    values = zero_mode["relative_heading_rad"]
    assert isinstance(values, Mapping)
    computed_source_sha = legacy_r23_solution_sha(candidate)
    if computed_source_sha != source_solution_sha256:
        raise HeadingGaugeValidationError("source SHA does not bind the legacy solution content")
    return HeadingGaugeState(
        coordinate_order=order,
        k_protocol_relative_rad_by_coordinate=values,
        psi_protocol_to_common_rad=0.0,
        source_solution_sha256=source_solution_sha256,
        source_schema=R23_SOURCE_SCHEMA,
        migration_id=R23_MIGRATION_ID,
    )


def legacy_r23_solution_sha(candidate: Mapping[str, object]) -> str:
    """Reproduce the frozen R2.6 source-solution digest for migration binding."""
    modes = candidate.get("joint_modes")
    if not isinstance(modes, list):
        raise HeadingGaugeValidationError("legacy mode set missing")
    zero = next(
        (row for row in modes if isinstance(row, Mapping)
         and row.get("pi_branch_bits") == [0] * 9),
        None,
    )
    if not isinstance(zero, Mapping):
        raise HeadingGaugeValidationError("legacy zero mode missing")
    values = zero.get("relative_heading_rad")
    if not isinstance(values, Mapping):
        raise HeadingGaugeValidationError("legacy zero-mode K values missing")
    from .core import COORDINATE_ORDER
    payload = {
        "coordinate_order": list(COORDINATE_ORDER),
        # "headings" is part of the frozen legacy digest algorithm only.  It
        # is never emitted by the repaired schema or exposed to consumers.
        "headings": {name: float(values[name]) for name in COORDINATE_ORDER},
        "objective": float(zero["objective"]),
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


@dataclass(frozen=True, slots=True)
class BranchEvaluation:
    """Immutable typed envelope for branch-dependent derived quantities."""

    heading_state: HeadingGaugeState
    _payload_bytes: bytes

    @classmethod
    def create(
        cls, heading_state: HeadingGaugeState, evaluation: Mapping, selection: Mapping
    ) -> BranchEvaluation:
        if not isinstance(heading_state, HeadingGaugeState):
            raise TypeError("typed HeadingGaugeState required")
        payload = {
            "semantic_version": BRANCH_EVALUATION_SEMANTIC_VERSION,
            "semantic_cache_key": HEADING_GAUGE_CACHE_KEY,
            "heading_gauge_state_sha256": heading_state.payload_sha256(),
            "evaluation": dict(evaluation),
            "selection": dict(selection),
        }
        return cls(heading_state=heading_state, _payload_bytes=_canonical_json_bytes(payload))

    def to_payload(self) -> dict:
        return json.loads(self._payload_bytes)


def validate_semantic_cache(cache: Mapping[str, object], state: HeadingGaugeState) -> None:
    if not isinstance(cache, Mapping):
        raise HeadingGaugeValidationError("cache envelope must be a mapping")
    if cache.get("semantic_cache_key") != HEADING_GAUGE_CACHE_KEY:
        raise HeadingGaugeValidationError("stale or unknown semantic cache key")
    if cache.get("heading_gauge_state_sha256") != state.payload_sha256():
        raise HeadingGaugeValidationError("cache heading-state SHA mismatch")
    forbidden = sorted(set(cache).intersection(INVALIDATED_R26_DERIVED_CACHE_FIELDS))
    if forbidden:
        raise HeadingGaugeValidationError(f"stale R2.6 derived cache fields refused: {forbidden}")


def validate_future_candidate_payload(
    payload: Mapping[str, object], state: HeadingGaugeState
) -> None:
    if payload.get("schema") != FUTURE_CANDIDATE_SCHEMA:
        raise HeadingGaugeValidationError("legacy or unknown candidate schema refused")
    if payload.get("semantic_cache_key") != HEADING_GAUGE_CACHE_KEY:
        raise HeadingGaugeValidationError("candidate semantic cache key mismatch")
    if payload.get("heading_gauge_state_sha256") != state.payload_sha256():
        raise HeadingGaugeValidationError("candidate heading-state SHA mismatch")
    nodes = payload.get("nodes")
    if not isinstance(nodes, list) or len(nodes) != len(state.coordinate_order):
        raise HeadingGaugeValidationError("candidate node set mismatch")
    by_coordinate = {row.get("coordinate"): row for row in nodes if isinstance(row, Mapping)}
    if tuple(row.get("coordinate") for row in nodes if isinstance(row, Mapping)) != state.coordinate_order:
        raise HeadingGaugeValidationError("candidate coordinate order mismatch")
    if set(by_coordinate) != set(state.coordinate_order):
        raise HeadingGaugeValidationError("candidate duplicate/missing coordinate")
    for coordinate in state.coordinate_order:
        row = by_coordinate[coordinate]
        if set(row) != {
            "coordinate",
            "k_protocol_relative_rad",
            "psi_protocol_to_common_rad",
            "h_common_rad_derived",
            "h_common_derivation",
        }:
            raise HeadingGaugeValidationError("candidate typed angle fields are not exact")
        if row["h_common_derivation"] != "wrap_2pi(k_protocol_relative_rad + psi_protocol_to_common_rad)":
            raise HeadingGaugeValidationError("candidate H derivation formula mismatch")
        expected_k = state.k_protocol_relative_rad(coordinate)
        expected_psi = state.psi_protocol_to_common_rad
        expected_h = state.h_common_rad(coordinate)
        if abs(_wrap_2pi_scalar(float(row["k_protocol_relative_rad"]) - expected_k)) > 1e-12:
            raise HeadingGaugeValidationError("candidate K mismatch")
        if abs(_wrap_2pi_scalar(float(row["psi_protocol_to_common_rad"]) - expected_psi)) > 1e-12:
            raise HeadingGaugeValidationError("candidate psi mismatch")
        if abs(_wrap_2pi_scalar(float(row["h_common_rad_derived"]) - expected_h)) > 1e-12:
            raise HeadingGaugeValidationError("inconsistent derived H")
