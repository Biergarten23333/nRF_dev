"""Digest-bound offline reference owners for the U7D.1 ROOT fixture."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import hmac
import json
import math
import re
from types import MappingProxyType
from typing import Mapping, Sequence

import numpy as np

from biospur_fusion.c2_uwb_calibration.adaptive_nodes import AdaptiveNodeTrustConfig, adaptive_root_minimum_std_m, select_trusted_body_nodes
from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectNodeLinkClock
from biospur_fusion.c2_uwb_calibration.shared_root import solve_shared_root
from biospur_fusion.c2_uwb_root_world import causal_update_transaction as tx
from biospur_fusion.c2_uwb_root_world.causal_update_guard import CandidateKind, ReachabilityEnvelope
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import StrictFloorOffset, build_causal_links, group_epoch_times_ns
from biospur_fusion.c2_uwb_root_world.tight_range import ExternalRangeInformationWeights, RawRangeUpdateConfig, linearize_raw_range_factors
from biospur_fusion.c2_uwb_root_world.u0 import ClockModel, UwbRow
from biospur_fusion.root_r3.estimator import CausalDelayedRootFilter, RootFilterConfig
from biospur_fusion.root_r3.models import PositionObservation, RootState


def _ro(value, shape):
    result = np.asarray(value, float).copy()
    if result.shape != shape or not np.isfinite(result).all():
        raise ValueError("invalid owner array")
    result.setflags(write=False)
    return result


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash64(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


@dataclass(frozen=True)
class PoseTagLinkOwner:
    node: str
    anchor: int
    query_time_ns: float
    pose_time_ns: int
    offset_world_m: np.ndarray
    offset_velocity_world_mps: np.ndarray
    source_epoch: int
    source_revision: int
    source_sha256: str

    def __post_init__(self):
        if not self.node or not 0 <= self.anchor < 8 or not _hash64(self.source_sha256):
            raise ValueError("invalid pose owner identity")
        if not (math.isfinite(self.query_time_ns) and self.pose_time_ns < self.query_time_ns
                and 0 < self.query_time_ns - self.pose_time_ns <= 5_005_000):
            raise ValueError("invalid strict-floor owner")
        object.__setattr__(self, "offset_world_m", _ro(self.offset_world_m, (3,)))
        object.__setattr__(self, "offset_velocity_world_mps", _ro(self.offset_velocity_world_mps, (3,)))


@dataclass(frozen=True)
class RangeInformationOwner:
    nominal_sigma_m: float
    positive_nlos_cauchy_scale_m: float
    weights_by_node: Mapping[str, np.ndarray]
    provenance: str

    def __post_init__(self):
        if not self.provenance or not all(math.isfinite(x) and x > 0 for x in (
                self.nominal_sigma_m, self.positive_nlos_cauchy_scale_m)):
            raise ValueError("invalid range information owner")
        values = {str(key): _ro(value, (8,)) for key, value in self.weights_by_node.items()}
        if any(np.any(value <= 0) or np.any(value > 1) for value in values.values()):
            raise ValueError("information weights outside (0,1]")
        object.__setattr__(self, "weights_by_node", MappingProxyType(values))

    def sigma(self, quality: int):
        if isinstance(quality, bool) or not isinstance(quality, int) or quality < 1:
            return math.nan
        return self.nominal_sigma_m * math.sqrt(100.0 / quality)


ROOT_FREE_REACHABILITY_EVIDENCE_SCHEMA = "biospur.c2.root_free_reachability_evidence.v1"
ROOT_FREE_REACHABILITY_ROLE = "PRODUCTION_ROOT_REACHABILITY_POLICY"


@dataclass(frozen=True)
class ReachabilityPolicyEvidence:
    """Typed, content-bound evidence for one exact reachability envelope."""

    schema: str
    role: str
    envelope: ReachabilityEnvelope
    source_artifact_sha256: str
    owner_digest: str
    qualification_status: str
    digest: str = ""

    def __post_init__(self):
        if (
            self.schema != ROOT_FREE_REACHABILITY_EVIDENCE_SCHEMA
            or self.role != ROOT_FREE_REACHABILITY_ROLE
            or type(self.envelope) is not ReachabilityEnvelope
            or self.envelope.qualification_errors()
            or not _hash64(self.source_artifact_sha256)
            or not _hash64(self.owner_digest)
            or self.qualification_status not in (
                "MECHANISM_ONLY_UNQUALIFIED",
                "PRODUCTION_QUALIFIED",
            )
        ):
            raise ValueError("invalid root-free reachability policy evidence")
        value = hashlib.sha256(_canonical({
            "schema": self.schema,
            "role": self.role,
            "envelope": {
                **asdict(self.envelope),
                "reachability_class": self.envelope.reachability_class.name,
            },
            "source_artifact_sha256": self.source_artifact_sha256,
            "owner_digest": self.owner_digest,
            "qualification_status": self.qualification_status,
        })).hexdigest()
        if self.digest and not hmac.compare_digest(self.digest, value):
            raise ValueError("root-free reachability evidence digest mismatch")
        object.__setattr__(self, "digest", value)

    @property
    def production_qualified(self) -> bool:
        return self.qualification_status == "PRODUCTION_QUALIFIED"


@dataclass(frozen=True)
class RootFreeReferenceOwnerTemplate:
    """Structure-only static owners available before root or pose-link ownership."""

    root_config: RootFilterConfig
    inertial: bool
    anchors_m: np.ndarray
    clocks: Mapping[str, DirectNodeLinkClock]
    anchor_delay_m: np.ndarray
    tag_delay_m: float
    range_information: RangeInformationOwner
    reachability_evidence: ReachabilityPolicyEvidence
    trust_config: AdaptiveNodeTrustConfig
    root_config_provenance: str
    anchor_provenance: str
    clock_provenance: str
    guard_policy: str
    digest: str = ""

    def __post_init__(self):
        provenance = (
            self.root_config_provenance,
            self.anchor_provenance,
            self.clock_provenance,
            self.guard_policy,
            self.range_information.provenance,
            self.nominal_envelope.provenance,
        )
        if (
            type(self.inertial) is not bool
            or any(not value for value in provenance)
            or type(self.reachability_evidence) is not ReachabilityPolicyEvidence
        ):
            raise ValueError("root-free static owner provenance missing")
        anchors = _ro(self.anchors_m, (8, 3))
        delay = _ro(self.anchor_delay_m, (8,))
        if np.linalg.matrix_rank(anchors - anchors.mean(0)) != 3 or np.allclose(anchors, 0):
            raise ValueError("zero/degenerate anchor placeholder")
        clocks = {str(key): value for key, value in self.clocks.items()}
        if (
            len(clocks) != 10
            or set(clocks) != set(self.range_information.weights_by_node)
            or any(type(value) is not DirectNodeLinkClock for value in clocks.values())
        ):
            raise ValueError("clock/range owner inventory")
        if not math.isfinite(self.tag_delay_m):
            raise ValueError("tag delay invalid")
        self.trust_config.validate()
        object.__setattr__(self, "anchors_m", anchors)
        object.__setattr__(self, "anchor_delay_m", delay)
        object.__setattr__(self, "clocks", MappingProxyType(clocks))
        computed = hashlib.sha256(_canonical(self._manifest())).hexdigest()
        if self.digest and not hmac.compare_digest(self.digest, computed):
            raise ValueError("root-free static owner digest mismatch")
        object.__setattr__(self, "digest", computed)

    def _manifest(self):
        array = lambda value: np.asarray(value).tolist()
        return {
            "schema": "biospur.c2.root_free_reference_owner.v1",
            "root_config": asdict(self.root_config),
            "inertial": self.inertial,
            "anchors_m": array(self.anchors_m),
            "clocks": [asdict(self.clocks[key]) for key in sorted(self.clocks)],
            "anchor_delay_m": array(self.anchor_delay_m),
            "tag_delay_m": self.tag_delay_m,
            "range_information": {
                "nominal_sigma_m": self.range_information.nominal_sigma_m,
                "positive_nlos_cauchy_scale_m": self.range_information.positive_nlos_cauchy_scale_m,
                "weights_by_node": {
                    key: array(value)
                    for key, value in sorted(self.range_information.weights_by_node.items())
                },
                "provenance": self.range_information.provenance,
            },
            "reachability_evidence": {
                "schema": self.reachability_evidence.schema,
                "role": self.reachability_evidence.role,
                "envelope": {
                    **asdict(self.reachability_evidence.envelope),
                    "reachability_class": self.reachability_evidence.envelope.reachability_class.name,
                },
                "source_artifact_sha256": self.reachability_evidence.source_artifact_sha256,
                "owner_digest": self.reachability_evidence.owner_digest,
                "qualification_status": self.reachability_evidence.qualification_status,
                "digest": self.reachability_evidence.digest,
            },
            "trust_config": asdict(self.trust_config),
            "provenance": {
                "root_config": self.root_config_provenance,
                "anchors": self.anchor_provenance,
                "clocks": self.clock_provenance,
                "guard_policy": self.guard_policy,
            },
        }

    @property
    def production_qualified(self) -> bool:
        return self.reachability_evidence.production_qualified

    @property
    def nominal_envelope(self) -> ReachabilityEnvelope:
        return self.reachability_evidence.envelope

    def validate_integrity(self):
        current = hashlib.sha256(_canonical(self._manifest())).hexdigest()
        if not _hash64(self.digest) or not hmac.compare_digest(self.digest, current):
            raise ValueError("root-free static owner manifest digest mismatch")


def materialize_reference_owner(
    template: RootFreeReferenceOwnerTemplate,
    *,
    initial_state: RootState,
    pose_links: tuple[PoseTagLinkOwner, ...],
    initial_state_provenance: str,
) -> "ReferenceOwnerBundle":
    """Purely add post-solve state and current strict-floor pose ownership."""

    if type(template) is not RootFreeReferenceOwnerTemplate:
        raise TypeError("materialization requires a root-free static owner")
    template.validate_integrity()
    if not template.production_qualified:
        raise ValueError("root-free static owner is mechanism-only and unqualified")
    return _materialize_reference_owner_unchecked(
        template,
        initial_state=initial_state,
        pose_links=pose_links,
        initial_state_provenance=initial_state_provenance,
    )


def materialize_mechanism_reference_owner(
    template: RootFreeReferenceOwnerTemplate,
    *,
    initial_state: RootState,
    pose_links: tuple[PoseTagLinkOwner, ...],
    initial_state_provenance: str,
) -> "ReferenceOwnerBundle":
    """Materialize an explicitly non-promotable diagnostic reference owner.

    This keeps the scientific/production gate intact while allowing the same
    estimator plumbing to be exercised on real captures.  Callers must retain
    the template's ``MECHANISM_ONLY_UNQUALIFIED`` status in their result.
    """

    if type(template) is not RootFreeReferenceOwnerTemplate:
        raise TypeError("materialization requires a root-free static owner")
    template.validate_integrity()
    if template.production_qualified:
        raise ValueError("qualified templates must use materialize_reference_owner")
    return _materialize_reference_owner_unchecked(
        template,
        initial_state=initial_state,
        pose_links=pose_links,
        initial_state_provenance=initial_state_provenance,
    )


def _materialize_reference_owner_unchecked(
    template: RootFreeReferenceOwnerTemplate,
    *,
    initial_state: RootState,
    pose_links: tuple[PoseTagLinkOwner, ...],
    initial_state_provenance: str,
) -> "ReferenceOwnerBundle":
    if type(initial_state) is not RootState or not initial_state_provenance:
        raise ValueError("materialization requires an owned solved root state")
    links = tuple(pose_links)
    if (
        len(links) != 80
        or len({(link.node, link.anchor) for link in links}) != 80
        or {link.node for link in links} != set(template.clocks)
        or any(type(link) is not PoseTagLinkOwner for link in links)
    ):
        raise ValueError("materialization requires exactly 80 current strict-floor pose links")
    return ReferenceOwnerBundle(
        template.root_config,
        template.inertial,
        initial_state,
        template.anchors_m,
        template.clocks,
        template.anchor_delay_m,
        template.tag_delay_m,
        links,
        template.range_information,
        template.nominal_envelope,
        template.trust_config,
        template.root_config_provenance,
        initial_state_provenance,
        template.anchor_provenance,
        template.clock_provenance,
        template.guard_policy,
    )


@dataclass(frozen=True)
class ReferenceOwnerBundle:
    root_config: RootFilterConfig
    inertial: bool
    initial_state: RootState
    anchors_m: np.ndarray
    clocks: Mapping[str, DirectNodeLinkClock]
    anchor_delay_m: np.ndarray
    tag_delay_m: float
    pose_links: tuple[PoseTagLinkOwner, ...]
    range_information: RangeInformationOwner
    nominal_envelope: ReachabilityEnvelope
    trust_config: AdaptiveNodeTrustConfig
    root_config_provenance: str
    initial_state_provenance: str
    anchor_provenance: str
    clock_provenance: str
    guard_policy: str
    digest: str = ""

    def __post_init__(self):
        if type(self.inertial) is not bool or any(not value for value in (
                self.root_config_provenance, self.initial_state_provenance,
                self.anchor_provenance, self.clock_provenance, self.guard_policy)):
            raise ValueError("owner provenance missing")
        initial = RootState(
            float(self.initial_state.time_s),
            _ro(self.initial_state.vector, (9,)),
            _ro(self.initial_state.covariance, (9, 9)),
        )
        anchors = _ro(self.anchors_m, (8, 3))
        delay = _ro(self.anchor_delay_m, (8,))
        if np.linalg.matrix_rank(anchors - anchors.mean(0)) != 3 or np.allclose(anchors, 0):
            raise ValueError("zero/degenerate anchor placeholder")
        clocks = {str(key): value for key, value in self.clocks.items()}
        if len(clocks) != 10 or set(clocks) != set(self.range_information.weights_by_node):
            raise ValueError("clock/range owner inventory")
        if len(self.pose_links) != 80 or len({(x.node, x.anchor) for x in self.pose_links}) != 80:
            raise ValueError("pose link inventory")
        if {x.node for x in self.pose_links} != set(clocks):
            raise ValueError("pose/clock inventory")
        if all(np.linalg.norm(x.offset_world_m) == 0 for x in self.pose_links):
            raise ValueError("zero pose geometry placeholder")
        if not math.isfinite(self.tag_delay_m):
            raise ValueError("tag delay invalid")
        object.__setattr__(self, "initial_state", initial)
        object.__setattr__(self, "anchors_m", anchors)
        object.__setattr__(self, "anchor_delay_m", delay)
        object.__setattr__(self, "clocks", MappingProxyType(clocks))
        digest = self._computed_digest()
        if self.digest and not hmac.compare_digest(self.digest, digest):
            raise ValueError("owner digest mismatch")
        object.__setattr__(self, "digest", digest)

    def _computed_digest(self):
        try:
            return hashlib.sha256(_canonical(self._manifest())).hexdigest()
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("owner manifest integrity invalid") from exc

    def validate_integrity(self):
        current = self._computed_digest()
        if not _hash64(self.digest) or not hmac.compare_digest(self.digest, current):
            raise ValueError("owner manifest digest mismatch")

    def _manifest(self):
        array = lambda value: np.asarray(value).tolist()
        return {"schema": "biospur.c2.u7d1.owner.v1", "root_config": asdict(self.root_config),
            "inertial": self.inertial,
            "initial": {"time_s": self.initial_state.time_s, "vector": array(self.initial_state.vector),
                        "covariance": array(self.initial_state.covariance)},
            "anchors_m": array(self.anchors_m), "clocks": [asdict(self.clocks[key]) for key in sorted(self.clocks)],
            "anchor_delay_m": array(self.anchor_delay_m), "tag_delay_m": self.tag_delay_m,
            "pose_links": [{"node": x.node, "anchor": x.anchor, "query_time_ns": x.query_time_ns,
                "pose_time_ns": x.pose_time_ns, "offset_world_m": array(x.offset_world_m),
                "offset_velocity_world_mps": array(x.offset_velocity_world_mps), "source_epoch": x.source_epoch,
                "source_revision": x.source_revision, "source_sha256": x.source_sha256}
                for x in sorted(self.pose_links, key=lambda value: (value.node, value.anchor))],
            "range_information": {"nominal_sigma_m": self.range_information.nominal_sigma_m,
                "positive_nlos_cauchy_scale_m": self.range_information.positive_nlos_cauchy_scale_m,
                "weights_by_node": {key: array(value) for key, value in sorted(self.range_information.weights_by_node.items())},
                "provenance": self.range_information.provenance},
            "nominal_envelope": {**asdict(self.nominal_envelope),
                "reachability_class": self.nominal_envelope.reachability_class.name},
            "trust_config": asdict(self.trust_config),
            "provenance": {"root_config": self.root_config_provenance,
                "initial_state": self.initial_state_provenance, "anchors": self.anchor_provenance,
                "clocks": self.clock_provenance, "guard_policy": self.guard_policy}}

    def make_root(self):
        self.validate_integrity()
        root_state = RootState(
            float(self.initial_state.time_s),
            _ro(self.initial_state.vector, (9,)),
            _ro(self.initial_state.covariance, (9, 9)),
        )
        return CausalDelayedRootFilter(root_state, self.root_config, inertial=self.inertial)

    def pose(self, node, query):
        matches = [item for item in self.pose_links if item.node == node and item.query_time_ns == query]
        if len(matches) != 1:
            raise ValueError("missing/stale exact pose link owner")
        item = matches[0]
        return StrictFloorOffset(item.offset_world_m, item.pose_time_ns, query,
                                 query - item.pose_time_ns, item.source_epoch)


@dataclass(frozen=True)
class BoundGroupPacket:
    owner_digest: str
    rows: tuple[UwbRow, ...]
    digest: str = ""

    def __post_init__(self):
        if not _hash64(self.owner_digest):
            raise ValueError("packet owner digest invalid")
        rows = tuple(self.rows)
        value = hashlib.sha256(_canonical({"owner_digest": self.owner_digest,
            "rows": [asdict(row) for row in rows]})).hexdigest()
        if self.digest and not hmac.compare_digest(self.digest, value):
            raise ValueError("packet digest mismatch")
        object.__setattr__(self, "rows", rows)
        object.__setattr__(self, "digest", value)


@dataclass(frozen=True)
class OwnerGroupResult:
    decision: str
    root_reason: str
    state: np.ndarray
    covariance: np.ndarray
    factors: tuple
    candidate_rank: int
    candidate_condition: float
    link_count: int
    guard_calls: int


def _execute(owner: ReferenceOwnerBundle, root, rows: Sequence[UwbRow]):
    links, _, measurement, availability = build_causal_links(rows, clocks=owner.clocks,
        strict_floor_offset=owner.pose, anchor_delay_m=owner.anchor_delay_m,
        tag_delay_m=owner.tag_delay_m, sigma_for_quality=owner.range_information.sigma)
    links = tuple(replace(link, information_weight=float(
        owner.range_information.weights_by_node[link.node][link.anchor])) for link in links)
    token = root.publication_token()
    selection = select_trusted_body_nodes(links, anchors_m=owner.anchors_m,
        initial_root_m=token.state.vector[:3], root_velocity_mps=token.state.vector[3:6],
        total_nodes=10, config=owner.trust_config)
    candidate = solve_shared_root(selection.trusted_links, anchors_m=owner.anchors_m,
        initial_root_m=token.state.vector[:3], root_velocity_mps=token.state.vector[3:6])
    if not candidate.success or candidate.rank != 3 or not math.isfinite(candidate.condition):
        raise RuntimeError("candidate invalid")
    std = adaptive_root_minimum_std_m(len(selection.trusted_nodes))
    observation = PositionObservation(measurement, availability, candidate.root_position_m,
        np.eye(3) * std ** 2, "C2_SHARED_ROOT_DIAGNOSTIC", candidate.anchors_used,
        "DIAGNOSTIC_NODE_COUNT_FLOOR_ONLY_NOT_CALIBRATED_R", source_sequence=1)
    calls = 0
    original = tx.evaluate_candidate_transition

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    tx.evaluate_candidate_transition = counted
    try:
        transaction = tx.execute_causal_update_transaction(root=root, observation=observation,
            kind=CandidateKind.ROOT_POSITION, nominal_envelope=owner.nominal_envelope)
    finally:
        tx.evaluate_candidate_transition = original
    factors = []
    pose = {(item.node, item.anchor): item for item in owner.pose_links}
    config = RawRangeUpdateConfig(nominal_sigma_m=owner.range_information.nominal_sigma_m,
        positive_nlos_cauchy_scale_m=owner.range_information.positive_nlos_cauchy_scale_m)
    for row in sorted(rows, key=lambda item: item.node):
        clock = owner.clocks[row.node]
        epochs, _, _ = group_epoch_times_ns((row,), clocks={row.node: clock})
        reference = float(np.median(epochs)) * 1e-9
        state = RootState(reference, token.state.vector, token.state.covariance)
        weights = ExternalRangeInformationWeights(row.node, float(np.min(epochs)) * 1e-9 - 1e-6,
            owner.range_information.weights_by_node[row.node], owner.range_information.provenance)
        geometry = pose[(row.node, 0)]
        factors.append(linearize_raw_range_factors(state, row, anchors_m=owner.anchors_m,
            clock=ClockModel(clock.boot_epoch, clock.a_ns_per_us, clock.b_ns, 0.),
            information_weights=weights, tag_offset_world_m=geometry.offset_world_m,
            tag_offset_velocity_world_mps=geometry.offset_velocity_world_mps,
            config=config, maximum_condition=1e10))
    return OwnerGroupResult(transaction.decision.reason.value, transaction.root_decision_reason,
        root.current_state.vector.copy(), root.current_state.covariance.copy(), tuple(factors),
        candidate.rank, candidate.condition, len(links), calls)


class OwnerBoundCoordinator:
    def __init__(self, owner: ReferenceOwnerBundle):
        self.owner = owner
        self.root = owner.make_root()

    def process(self, packet: BoundGroupPacket):
        self.owner.validate_integrity()
        if not hmac.compare_digest(packet.owner_digest, self.owner.digest):
            raise ValueError("stale owner packet")
        check = hashlib.sha256(_canonical({"owner_digest": packet.owner_digest,
            "rows": [asdict(row) for row in packet.rows]})).hexdigest()
        if not hmac.compare_digest(check, packet.digest):
            raise ValueError("packet digest mismatch")
        return _execute(self.owner, self.root, packet.rows)


def execute_direct_reference(owner, packet):
    owner.validate_integrity()
    if not hmac.compare_digest(owner.digest, packet.owner_digest):
        raise ValueError("stale owner packet")
    return _execute(owner, owner.make_root(), packet.rows)
