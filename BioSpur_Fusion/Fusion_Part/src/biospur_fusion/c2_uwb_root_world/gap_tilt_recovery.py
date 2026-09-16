"""Causal root-translation protection while post-gap tilt is untrusted.

This module deliberately does not decide whether a VQF frame is trustworthy.
It consumes an immutable, source-associated diagnostic fact and owns only the
causal trust transition and the root edge mode selected before that transition.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math

from biospur_fusion.root_r3 import (
    CausalDelayedRootFilter,
    ImuSample,
    RootTranslationEdgeMode,
)


def _digest(payload: object) -> str:
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _sha256(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} must be a SHA-256 digest")
    return value


class TiltTrustState(str, Enum):
    TRUSTED = "TRUSTED"
    UNTRUSTED = "UNTRUSTED"


class TiltEvidenceStatus(str, Enum):
    TRUSTED = "TRUSTED"
    UNTRUSTED = "UNTRUSTED"
    MISSING = "MISSING"


@dataclass(frozen=True)
class GapTiltRecoveryConfig:
    maximum_contiguous_interval_s: float
    required_consecutive_trusted_frames: int

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.maximum_contiguous_interval_s)
            or self.maximum_contiguous_interval_s <= 0.0
            or type(self.required_consecutive_trusted_frames) is not int
            or self.required_consecutive_trusted_frames <= 0
        ):
            raise ValueError("invalid gap/tilt recovery policy")


@dataclass(frozen=True)
class AuthoritativeGapEvidence:
    previous_event_identity: str
    current_event_identity: str
    previous_measurement_time_s: float
    current_measurement_time_s: float
    previous_source_sequence: int
    current_source_sequence: int
    source_node: str
    boot_epoch: int
    previous_span_id: str
    current_span_id: str
    clock_domain: str
    clock_mapping_digest: str
    source_owner_digest: str
    digest: str = ""

    def __post_init__(self) -> None:
        if (
            not self.previous_event_identity
            or not self.current_event_identity
            or self.previous_event_identity == self.current_event_identity
            or not self.source_node
            or not self.previous_span_id
            or not self.current_span_id
            or not self.clock_domain
            or type(self.boot_epoch) is not int
            or type(self.previous_source_sequence) is not int
            or type(self.current_source_sequence) is not int
            or not 0 <= self.previous_source_sequence <= 0xFFFF
            or not 0 <= self.current_source_sequence <= 0xFFFF
            or not math.isfinite(self.previous_measurement_time_s)
            or not math.isfinite(self.current_measurement_time_s)
            or self.current_measurement_time_s <= self.previous_measurement_time_s
        ):
            raise ValueError("invalid source-bound gap evidence")
        _sha256(self.clock_mapping_digest, "clock mapping")
        _sha256(self.source_owner_digest, "source owner")
        expected = _digest(self._payload())
        if self.digest and self.digest != expected:
            raise ValueError("gap evidence digest mismatch")
        object.__setattr__(self, "digest", expected)

    def _payload(self) -> dict:
        return {
            "schema": "biospur.c2.authoritative_gap_evidence.v1",
            "previous_event_identity": self.previous_event_identity,
            "current_event_identity": self.current_event_identity,
            "previous_measurement_time_s": self.previous_measurement_time_s,
            "current_measurement_time_s": self.current_measurement_time_s,
            "previous_source_sequence": self.previous_source_sequence,
            "current_source_sequence": self.current_source_sequence,
            "source_node": self.source_node,
            "boot_epoch": self.boot_epoch,
            "previous_span_id": self.previous_span_id,
            "current_span_id": self.current_span_id,
            "clock_domain": self.clock_domain,
            "clock_mapping_digest": self.clock_mapping_digest,
            "source_owner_digest": self.source_owner_digest,
        }


@dataclass(frozen=True)
class TiltDiagnosticIssuerBinding:
    """Mechanism-only binding; no production frontend issuer is sealed yet."""

    source_node: str
    boot_epoch: int
    clock_domain: str
    clock_mapping_digest: str
    source_owner_digest: str
    diagnostic_owner_digest: str
    threshold_policy_source_digest: str
    maximum_trusted_tilt_error_rad: float
    digest: str = ""

    def __post_init__(self) -> None:
        if (
            not self.source_node
            or type(self.boot_epoch) is not int
            or not self.clock_domain
            or not math.isfinite(self.maximum_trusted_tilt_error_rad)
            or self.maximum_trusted_tilt_error_rad <= 0.0
        ):
            raise ValueError("invalid tilt diagnostic issuer binding")
        for value, field in (
            (self.clock_mapping_digest, "clock mapping"),
            (self.source_owner_digest, "source owner"),
            (self.diagnostic_owner_digest, "tilt diagnostic owner"),
            (self.threshold_policy_source_digest, "threshold policy source"),
        ):
            _sha256(value, field)
        expected = _digest({
            "schema": "biospur.c2.tilt_diagnostic_issuer.mechanism_only.v1",
            "qualification": "MECHANISM_ONLY_UNQUALIFIED",
            "source_node": self.source_node,
            "boot_epoch": self.boot_epoch,
            "clock_domain": self.clock_domain,
            "clock_mapping_digest": self.clock_mapping_digest,
            "source_owner_digest": self.source_owner_digest,
            "diagnostic_owner_digest": self.diagnostic_owner_digest,
            "threshold_policy_source_digest": self.threshold_policy_source_digest,
            "maximum_trusted_tilt_error_rad": self.maximum_trusted_tilt_error_rad,
        })
        if self.digest and self.digest != expected:
            raise ValueError("tilt issuer binding digest mismatch")
        object.__setattr__(self, "digest", expected)

    @property
    def production_ready(self) -> bool:
        return False


@dataclass(frozen=True)
class MissingTiltDiagnosticIssuerBinding:
    """Fail-closed owner for an authenticated policy outcome with no verdict."""

    source_node: str
    boot_epoch: int
    clock_domain: str
    clock_mapping_digest: str
    source_owner_digest: str
    missing_policy_digest: str
    digest: str = ""

    def __post_init__(self) -> None:
        if (
            not self.source_node
            or type(self.boot_epoch) is not int
            or not self.clock_domain
        ):
            raise ValueError("invalid missing tilt diagnostic issuer binding")
        for value, field in (
            (self.clock_mapping_digest, "clock mapping"),
            (self.source_owner_digest, "source owner"),
            (self.missing_policy_digest, "missing tilt policy"),
        ):
            _sha256(value, field)
        expected = _digest({
            "schema": "biospur.c2.missing_tilt_diagnostic_issuer.v1",
            "qualification": "ENGINEERING_DIAGNOSTIC_MISSING",
            "source_node": self.source_node,
            "boot_epoch": self.boot_epoch,
            "clock_domain": self.clock_domain,
            "clock_mapping_digest": self.clock_mapping_digest,
            "source_owner_digest": self.source_owner_digest,
            "missing_policy_digest": self.missing_policy_digest,
        })
        if self.digest and self.digest != expected:
            raise ValueError("missing tilt issuer binding digest mismatch")
        object.__setattr__(self, "digest", expected)

    @property
    def production_ready(self) -> bool:
        return False


@dataclass(frozen=True)
class TiltTrustFrameEvidence:
    event_identity: str
    measurement_time_s: float
    availability_time_s: float
    source_sequence: int
    source_node: str
    boot_epoch: int
    span_id: str
    clock_domain: str
    clock_mapping_digest: str
    source_owner_digest: str
    status: TiltEvidenceStatus
    tilt_error_rad: float | None
    maximum_trusted_tilt_error_rad: float | None
    diagnostic_owner_digest: str | None
    issuer_binding_digest: str
    digest: str = ""

    def __post_init__(self) -> None:
        if (
            not self.event_identity
            or not self.source_node
            or not self.span_id
            or not self.clock_domain
            or type(self.source_sequence) is not int
            or not 0 <= self.source_sequence <= 0xFFFF
            or type(self.boot_epoch) is not int
            or not math.isfinite(self.measurement_time_s)
            or not math.isfinite(self.availability_time_s)
            or self.availability_time_s < self.measurement_time_s
            or type(self.status) is not TiltEvidenceStatus
        ):
            raise ValueError("invalid tilt-trust frame evidence")
        _sha256(self.clock_mapping_digest, "clock mapping")
        _sha256(self.source_owner_digest, "source owner")
        _sha256(self.issuer_binding_digest, "tilt issuer binding")
        if self.status is TiltEvidenceStatus.MISSING:
            if any(value is not None for value in (
                self.tilt_error_rad,
                self.maximum_trusted_tilt_error_rad,
                self.diagnostic_owner_digest,
            )):
                raise ValueError("missing tilt evidence cannot carry a verdict payload")
        else:
            if (
                self.tilt_error_rad is None
                or self.maximum_trusted_tilt_error_rad is None
                or not math.isfinite(self.tilt_error_rad)
                or not math.isfinite(self.maximum_trusted_tilt_error_rad)
                or self.tilt_error_rad < 0.0
                or self.maximum_trusted_tilt_error_rad <= 0.0
                or self.diagnostic_owner_digest is None
            ):
                raise ValueError("tilt verdict lacks a finite owner-bound metric")
            _sha256(self.diagnostic_owner_digest, "tilt diagnostic owner")
            mechanically_trusted = (
                self.tilt_error_rad <= self.maximum_trusted_tilt_error_rad
            )
            if mechanically_trusted != (self.status is TiltEvidenceStatus.TRUSTED):
                raise ValueError("tilt verdict contradicts its bound metric")
        expected = _digest(self._payload())
        if self.digest and self.digest != expected:
            raise ValueError("tilt evidence digest mismatch")
        object.__setattr__(self, "digest", expected)

    def _payload(self) -> dict:
        return {
            "schema": "biospur.c2.tilt_trust_frame_evidence.v1",
            "event_identity": self.event_identity,
            "measurement_time_s": self.measurement_time_s,
            "availability_time_s": self.availability_time_s,
            "source_sequence": self.source_sequence,
            "source_node": self.source_node,
            "boot_epoch": self.boot_epoch,
            "span_id": self.span_id,
            "clock_domain": self.clock_domain,
            "clock_mapping_digest": self.clock_mapping_digest,
            "source_owner_digest": self.source_owner_digest,
            "status": self.status.value,
            "tilt_error_rad": self.tilt_error_rad,
            "maximum_trusted_tilt_error_rad": self.maximum_trusted_tilt_error_rad,
            "diagnostic_owner_digest": self.diagnostic_owner_digest,
            "issuer_binding_digest": self.issuer_binding_digest,
        }


@dataclass(frozen=True)
class TiltTrustTransitionEvidence:
    event_identity: str
    measurement_time_s: float
    previous_state: TiltTrustState
    current_state: TiltTrustState
    consecutive_trusted_frames: int
    reason: str
    gap_digest: str | None
    digest: str = ""

    def __post_init__(self) -> None:
        expected = _digest({
            "schema": "biospur.c2.tilt_trust_transition.v1",
            "event_identity": self.event_identity,
            "measurement_time_s": self.measurement_time_s,
            "previous_state": self.previous_state.value,
            "current_state": self.current_state.value,
            "consecutive_trusted_frames": self.consecutive_trusted_frames,
            "reason": self.reason,
            "gap_digest": self.gap_digest,
        })
        if self.digest and self.digest != expected:
            raise ValueError("tilt transition digest mismatch")
        object.__setattr__(self, "digest", expected)


@dataclass(frozen=True)
class _TrustSnapshot:
    state: TiltTrustState
    consecutive_trusted_frames: int
    event: TiltTrustFrameEvidence
    active_gap_digest: str | None
    transitions: tuple[TiltTrustTransitionEvidence, ...]


@dataclass(frozen=True)
class PreparedGapTiltFrame:
    authority: object
    revision: int
    edge_mode: RootTranslationEdgeMode
    following_input_mode: RootTranslationEdgeMode
    gap: AuthoritativeGapEvidence | None
    before: _TrustSnapshot
    after: _TrustSnapshot
    digest: str


class CausalTiltTrustStateMachine:
    """One-shot prepared causal trust owner; it never evaluates VQF itself."""

    def __init__(
        self,
        config: GapTiltRecoveryConfig,
        issuer: TiltDiagnosticIssuerBinding | MissingTiltDiagnosticIssuerBinding,
        initial_evidence: TiltTrustFrameEvidence,
    ) -> None:
        if initial_evidence.status not in (
            TiltEvidenceStatus.TRUSTED,
            TiltEvidenceStatus.MISSING,
        ):
            raise ValueError("initial tilt frame must be trusted or explicitly missing")
        if (
            initial_evidence.status is TiltEvidenceStatus.MISSING
            and type(issuer) is not MissingTiltDiagnosticIssuerBinding
        ):
            raise ValueError("missing initial tilt frame requires its missing-policy owner")
        if (
            initial_evidence.status is TiltEvidenceStatus.TRUSTED
            and type(issuer) is not TiltDiagnosticIssuerBinding
        ):
            raise ValueError("trusted initial tilt frame requires its diagnostic owner")
        self.config = config
        self.issuer = issuer
        self._validate_issuer(initial_evidence)
        self.__authority = object()
        self._revision = 0
        initial_trusted = initial_evidence.status is TiltEvidenceStatus.TRUSTED
        self._snapshot = _TrustSnapshot(
            TiltTrustState.TRUSTED if initial_trusted else TiltTrustState.UNTRUSTED,
            config.required_consecutive_trusted_frames if initial_trusted else 0,
            initial_evidence,
            None,
            (),
        )

    @property
    def state(self) -> TiltTrustState:
        return self._snapshot.state

    @property
    def production_ready(self) -> bool:
        return False

    @property
    def transition_evidence(self) -> tuple[TiltTrustTransitionEvidence, ...]:
        return self._snapshot.transitions

    @property
    def current_evidence(self) -> TiltTrustFrameEvidence:
        return self._snapshot.event

    def owner_bytes(self) -> bytes:
        return json.dumps({
            "revision": self._revision,
            "config": {
                "maximum_contiguous_interval_s": self.config.maximum_contiguous_interval_s,
                "required_consecutive_trusted_frames": self.config.required_consecutive_trusted_frames,
            },
            "issuer": self.issuer.digest,
            "state": self._snapshot.state.value,
            "consecutive": self._snapshot.consecutive_trusted_frames,
            "event_digest": self._snapshot.event.digest,
            "active_gap_digest": self._snapshot.active_gap_digest,
            "transitions": [row.digest for row in self._snapshot.transitions],
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def clone(self) -> "CausalTiltTrustStateMachine":
        result = object.__new__(CausalTiltTrustStateMachine)
        result.config = self.config
        result.issuer = self.issuer
        result._CausalTiltTrustStateMachine__authority = object()
        result._revision = self._revision
        result._snapshot = self._snapshot
        return result

    def _validate_issuer(self, evidence: TiltTrustFrameEvidence) -> None:
        common_mismatch = (
            evidence.source_node != self.issuer.source_node
            or evidence.boot_epoch != self.issuer.boot_epoch
            or evidence.clock_domain != self.issuer.clock_domain
            or evidence.clock_mapping_digest != self.issuer.clock_mapping_digest
            or evidence.source_owner_digest != self.issuer.source_owner_digest
            or evidence.issuer_binding_digest != self.issuer.digest
        )
        if type(self.issuer) is MissingTiltDiagnosticIssuerBinding:
            invalid_payload = evidence.status is not TiltEvidenceStatus.MISSING
        else:
            invalid_payload = (
                evidence.status is not TiltEvidenceStatus.MISSING
                and (
                    evidence.diagnostic_owner_digest != self.issuer.diagnostic_owner_digest
                    or evidence.maximum_trusted_tilt_error_rad
                    != self.issuer.maximum_trusted_tilt_error_rad
                )
            )
        if self.issuer.production_ready or common_mismatch or invalid_payload:
            raise ValueError("tilt evidence lacks the bound diagnostic issuer")

    def _validate_identity(
        self,
        sample: ImuSample,
        evidence: TiltTrustFrameEvidence,
    ) -> None:
        previous = self._snapshot.event
        self._validate_issuer(evidence)
        if (
            evidence.source_node != previous.source_node
            or evidence.boot_epoch != previous.boot_epoch
            or evidence.clock_domain != previous.clock_domain
            or evidence.clock_mapping_digest != previous.clock_mapping_digest
            or evidence.source_owner_digest != previous.source_owner_digest
            or evidence.event_identity == previous.event_identity
            or evidence.measurement_time_s <= previous.measurement_time_s
            or evidence.availability_time_s < previous.availability_time_s
            or abs(evidence.measurement_time_s - sample.measurement_time_s) > 1e-12
            or abs(evidence.availability_time_s - sample.availability_time_s) > 1e-12
            or type(sample.source_sequence) is not int
            or not 0 <= sample.source_sequence <= 0xFFFF
            or evidence.source_sequence != sample.source_sequence
        ):
            raise ValueError("tilt evidence is stale, foreign, or misassociated")

    def prepare(
        self,
        sample: ImuSample,
        evidence: TiltTrustFrameEvidence,
        *,
        gap: AuthoritativeGapEvidence | None = None,
    ) -> PreparedGapTiltFrame:
        sample.validate()
        self._validate_identity(sample, evidence)
        before = self._snapshot
        interval = evidence.measurement_time_s - before.event.measurement_time_s
        transitions = before.transitions
        active_gap = before.active_gap_digest
        working_state = before.state
        consecutive = before.consecutive_trusted_frames
        if gap is None:
            if (
                evidence.span_id != before.event.span_id
                or interval > self.config.maximum_contiguous_interval_s + 1e-12
            ):
                raise ValueError("source discontinuity lacks authoritative gap evidence")
        else:
            if (
                gap.previous_event_identity != before.event.event_identity
                or gap.current_event_identity != evidence.event_identity
                or abs(gap.previous_measurement_time_s - before.event.measurement_time_s) > 1e-12
                or abs(gap.current_measurement_time_s - evidence.measurement_time_s) > 1e-12
                or gap.previous_source_sequence != before.event.source_sequence
                or gap.current_source_sequence != evidence.source_sequence
                or gap.source_node != evidence.source_node
                or gap.boot_epoch != evidence.boot_epoch
                or gap.previous_span_id != before.event.span_id
                or gap.current_span_id != evidence.span_id
                or gap.clock_domain != evidence.clock_domain
                or gap.clock_mapping_digest != evidence.clock_mapping_digest
                or gap.source_owner_digest != evidence.source_owner_digest
                or (
                    gap.previous_span_id == gap.current_span_id
                    and interval <= self.config.maximum_contiguous_interval_s + 1e-12
                )
            ):
                raise ValueError("gap evidence does not bind the adjacent source frames")
            transition = TiltTrustTransitionEvidence(
                evidence.event_identity,
                evidence.measurement_time_s,
                before.state,
                TiltTrustState.UNTRUSTED,
                0,
                "SOURCE_GAP_ENTER_UNTRUSTED",
                gap.digest,
            )
            transitions = transitions + (transition,)
            working_state = TiltTrustState.UNTRUSTED
            consecutive = 0
            active_gap = gap.digest

        # Edge k-1 -> k uses trust known before consuming evidence at tick k.
        edge_mode = (
            RootTranslationEdgeMode.INERTIAL
            if working_state is TiltTrustState.TRUSTED
            else RootTranslationEdgeMode.CV_NO_ACCELERATION
        )
        if evidence.status is TiltEvidenceStatus.TRUSTED:
            consecutive += 1
            if (
                working_state is TiltTrustState.UNTRUSTED
                and consecutive >= self.config.required_consecutive_trusted_frames
            ):
                transitions = transitions + (TiltTrustTransitionEvidence(
                    evidence.event_identity,
                    evidence.measurement_time_s,
                    TiltTrustState.UNTRUSTED,
                    TiltTrustState.TRUSTED,
                    consecutive,
                    "CONSECUTIVE_TILT_EVIDENCE_RECOVERED",
                    active_gap,
                ),)
                working_state = TiltTrustState.TRUSTED
                active_gap = None
        else:
            consecutive = 0
            if working_state is TiltTrustState.TRUSTED:
                transitions = transitions + (TiltTrustTransitionEvidence(
                    evidence.event_identity,
                    evidence.measurement_time_s,
                    TiltTrustState.TRUSTED,
                    TiltTrustState.UNTRUSTED,
                    0,
                    "TILT_EVIDENCE_MISSING" if evidence.status is TiltEvidenceStatus.MISSING else "TILT_EVIDENCE_UNTRUSTED",
                    active_gap,
                ),)
                working_state = TiltTrustState.UNTRUSTED
        after = _TrustSnapshot(
            working_state, consecutive, evidence, active_gap, transitions,
        )
        digest = _digest({
            "revision": self._revision,
            "edge_mode": edge_mode.value,
            "following_input_mode": (
                RootTranslationEdgeMode.INERTIAL.value
                if after.state is TiltTrustState.TRUSTED
                else RootTranslationEdgeMode.CV_NO_ACCELERATION.value
            ),
            "gap": None if gap is None else gap.digest,
            "before": hashlib.sha256(self.owner_bytes()).hexdigest(),
            "after_event": after.event.digest,
            "after_state": after.state.value,
            "after_consecutive": after.consecutive_trusted_frames,
            "after_gap": after.active_gap_digest,
            "after_transitions": [row.digest for row in after.transitions],
        })
        following_input_mode = (
            RootTranslationEdgeMode.INERTIAL
            if after.state is TiltTrustState.TRUSTED
            else RootTranslationEdgeMode.CV_NO_ACCELERATION
        )
        return PreparedGapTiltFrame(
            self.__authority, self._revision, edge_mode,
            following_input_mode, gap, before, after, digest,
        )

    def commit(self, plan: PreparedGapTiltFrame) -> None:
        if (
            type(plan) is not PreparedGapTiltFrame
            or plan.authority is not self.__authority
            or plan.revision != self._revision
            or plan.before != self._snapshot
        ):
            raise RuntimeError("STALE_OR_FOREIGN_GAP_TILT_PLAN")
        expected = _digest({
            "revision": plan.revision,
            "edge_mode": plan.edge_mode.value,
            "following_input_mode": plan.following_input_mode.value,
            "gap": None if plan.gap is None else plan.gap.digest,
            "before": hashlib.sha256(self.owner_bytes()).hexdigest(),
            "after_event": plan.after.event.digest,
            "after_state": plan.after.state.value,
            "after_consecutive": plan.after.consecutive_trusted_frames,
            "after_gap": plan.after.active_gap_digest,
            "after_transitions": [row.digest for row in plan.after.transitions],
        })
        if plan.digest != expected:
            raise RuntimeError("GAP_TILT_PLAN_DIGEST_MISMATCH")
        self._snapshot = plan.after
        self._revision += 1


class GapTiltRecoveryController:
    """Atomic composition of trust selection and authoritative root ingestion."""

    def __init__(
        self,
        root: CausalDelayedRootFilter,
        trust: CausalTiltTrustStateMachine,
    ) -> None:
        if abs(root.current_state.time_s-trust._snapshot.event.measurement_time_s)>1e-12:
            raise ValueError("root and tilt owner initial epochs differ")
        root.bind_pristine_following_input_mode(
            RootTranslationEdgeMode.INERTIAL
            if trust.state is TiltTrustState.TRUSTED
            else RootTranslationEdgeMode.CV_NO_ACCELERATION
        )
        self.root = root
        self.trust = trust
        self._terminal_attachment_owner_digest = None

    @classmethod
    def attach_terminal_missing(
        cls,
        root: CausalDelayedRootFilter,
        trust: CausalTiltTrustStateMachine,
        *,
        expected_root_publication: object,
    ) -> "GapTiltRecoveryController":
        """Attach fresh terminal MISSING trust without rewriting prior root history."""
        evidence = trust.current_evidence
        if (
            type(trust.issuer) is not MissingTiltDiagnosticIssuerBinding
            or evidence.status is not TiltEvidenceStatus.MISSING
            or trust.state is not TiltTrustState.UNTRUSTED
            or trust.transition_evidence
            or trust._revision != 0
            or root.current_state.time_s + 1e-12 < evidence.measurement_time_s
            or root.current_state.time_s > evidence.availability_time_s + 1e-12
        ):
            raise ValueError("terminal MISSING trust/root ownership mismatch")
        rollback = root._prepare_position_rollback()
        try:
            root._validate_publication_token(expected_root_publication)
            pre_advance_digest = root.publication_token().digest
            root.advance_to_availability(evidence.availability_time_s)
            post_advance = root.publication_token()
            terminal_owner_digest = _digest({
                "schema": "biospur.c2.terminal_missing_root_attachment.v1",
                "pre_advance_root_publication": pre_advance_digest,
                "post_advance_root_publication": post_advance.digest,
                "trust_owner": hashlib.sha256(trust.owner_bytes()).hexdigest(),
                "issuer": trust.issuer.digest,
                "evidence": evidence.digest,
                "measurement_time_s": evidence.measurement_time_s,
                "availability_time_s": evidence.availability_time_s,
            })
            root._bind_terminal_missing_following_mode(
                post_advance,
                availability_time_s=evidence.availability_time_s,
                missing_owner_digest=terminal_owner_digest,
            )
        except Exception:
            root._rollback_prevalidated_position(rollback)
            raise
        result = object.__new__(cls)
        result.root = root
        result.trust = trust
        result._terminal_attachment_owner_digest = terminal_owner_digest
        return result

    @property
    def terminal_attachment_owner_digest(self) -> str | None:
        return self._terminal_attachment_owner_digest

    @property
    def production_ready(self) -> bool:
        return False

    def add_imu(
        self,
        sample: ImuSample,
        evidence: TiltTrustFrameEvidence,
        *,
        gap: AuthoritativeGapEvidence | None = None,
    ) -> bool:
        plan = self.trust.prepare(sample, evidence, gap=gap)
        rollback = self.root._prepare_position_rollback()
        try:
            if gap is None:
                accepted = self.root.add_imu(
                    sample,
                    edge_mode=plan.edge_mode,
                    following_input_mode=plan.following_input_mode,
                )
            else:
                accepted = self.root.ingest_imu_after_source_gap(
                    sample,
                    gap_start_time_s=self.root.current_state.time_s,
                    source_gap_owner=gap.digest,
                    following_input_mode=plan.following_input_mode,
                )
            if not accepted:
                self.root._rollback_prevalidated_position(rollback)
                return False
            self.trust.commit(plan)
            return True
        except Exception:
            self.root._rollback_prevalidated_position(rollback)
            raise
