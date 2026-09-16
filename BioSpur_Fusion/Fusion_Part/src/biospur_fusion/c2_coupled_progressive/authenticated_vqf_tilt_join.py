"""Causal common-clock join for source-owned VQF diagnostics.

This adapter owns neither clocks nor physical tilt trust.  It validates and
uses the existing ``ContinuousClockOwner`` mapping, and emits an explicitly
unqualified diagnostic fact.  Such a fact cannot drive GapTiltRecovery until
a later Action00 statistical-policy owner classifies it.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math

import numpy as np

from .continuous_frontend import (
    ContinuousClockOwner,
    ContinuousEvent,
    continuous_clock_owner_digest,
    validate_event_clock,
)
from biospur_fusion.ingest.events import RecordType, TypedEvent
from biospur_fusion.ingest.events import EventStatus
from biospur_fusion.c2_uwb_root_world.gap_tilt_recovery import (
    TiltEvidenceStatus, TiltTrustFrameEvidence,
)
from biospur_fusion.v0.c2_progressive.orientation import (
    OrientedAction,
    VQFTiltDiagnosticProvenance,
    verify_vqf_tilt_source_binding,
)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


@dataclass(frozen=True)
class UnqualifiedVQFTiltClockEvidence:
    event_identity: str
    action_id: str
    protocol_action_index: int
    acquired_chronological_index: int
    node: str
    boot_epoch: int
    span_id: int
    source_sequence: int
    timer2_us: int
    common_global_ns: int
    availability_global_ns: int
    raw_start_offset: int
    raw_end_offset: int
    raw_sample_index: int
    clock_domain: str
    clock_mapping_digest: str
    clock_owner_digest: str
    diagnostic_provenance_digest: str
    diagnostic_source_binding_digest: str
    rest_detected: bool
    bias_sigma_rad_s: float
    relative_rest_deviation_gyro: float
    relative_rest_deviation_acceleration: float
    acceleration_norm_residual_mps2: float
    world_tilt_innovation_rad: float
    qualification: str = "UNQUALIFIED_DIAGNOSTIC"
    digest: str = ""

    def __post_init__(self) -> None:
        if (
            not self.event_identity or not self.action_id or not self.node
            or type(self.protocol_action_index) is not int
            or not 0 <= self.protocol_action_index < 20
            or type(self.acquired_chronological_index) is not int
            or self.acquired_chronological_index < 0
            or type(self.boot_epoch) is not int or type(self.span_id) is not int
            or type(self.source_sequence) is not int or self.source_sequence < 0
            or type(self.timer2_us) is not int or self.timer2_us < 0
            or type(self.common_global_ns) is not int or self.common_global_ns < 0
            or type(self.availability_global_ns) is not int
            or self.availability_global_ns < self.common_global_ns
            or self.raw_end_offset <= self.raw_start_offset
            or self.qualification != "UNQUALIFIED_DIAGNOSTIC"
            or type(self.rest_detected) is not bool
        ):
            raise ValueError("invalid unqualified VQF clock evidence")
        for value in (
            self.clock_mapping_digest, self.clock_owner_digest,
            self.diagnostic_provenance_digest,
            self.diagnostic_source_binding_digest,
        ):
            if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("invalid VQF clock-join digest")
        metrics = (
            self.bias_sigma_rad_s, self.relative_rest_deviation_gyro,
            self.relative_rest_deviation_acceleration,
            self.acceleration_norm_residual_mps2, self.world_tilt_innovation_rad,
        )
        if any(not math.isfinite(value) or value < 0.0 for value in metrics):
            raise ValueError("invalid VQF clock-join metric")
        payload = dict(self.__dict__)
        payload.pop("digest")
        expected = _digest({"schema": "biospur.c2.unqualified_vqf_tilt_clock_evidence.v1", **payload})
        if self.digest and self.digest != expected:
            raise ValueError("VQF clock evidence digest mismatch")
        object.__setattr__(self, "digest", expected)

    @property
    def recovery_evidence(self) -> TiltTrustFrameEvidence:
        """Exact missing verdict; callers must not commit it without policy."""
        return TiltTrustFrameEvidence(
            event_identity=self.event_identity,
            measurement_time_s=self.common_global_ns * 1e-9,
            availability_time_s=self.availability_global_ns * 1e-9,
            source_sequence=self.source_sequence,
            source_node=self.node,
            boot_epoch=self.boot_epoch,
            span_id=str(self.span_id),
            clock_domain=self.clock_domain,
            clock_mapping_digest=self.clock_mapping_digest,
            source_owner_digest=self.diagnostic_source_binding_digest,
            status=TiltEvidenceStatus.MISSING,
            tilt_error_rad=None,
            maximum_trusted_tilt_error_rad=None,
            diagnostic_owner_digest=None,
            issuer_binding_digest=self.diagnostic_provenance_digest,
        )


@dataclass(frozen=True)
class PreparedVQFTiltClockJoin:
    authority: object
    revision: int
    previous_identity: tuple[int, int, int, int, str] | None
    next_identity: tuple[int, int, int, int, str]
    evidence: UnqualifiedVQFTiltClockEvidence
    digest: str


class AuthenticatedVQFTiltClockJoin:
    """One-shot prepare/commit owner around an existing clock contract."""

    def __init__(self, *, clock_owner: ContinuousClockOwner,
                 diagnostic_provenance_digest: str) -> None:
        if len(diagnostic_provenance_digest) != 64:
            raise ValueError("invalid expected diagnostic provenance digest")
        self.clock_owner = clock_owner
        self.diagnostic_provenance_digest = diagnostic_provenance_digest
        self.clock_owner_digest = continuous_clock_owner_digest(clock_owner)
        self.__authority = object()
        self._revision = 0
        self._last: dict[str, tuple[int, int, int, int, str]] = {}

    def owner_bytes(self) -> bytes:
        return json.dumps({
            "revision": self._revision,
            "clock": self.clock_owner_digest,
            "diagnostic": self.diagnostic_provenance_digest,
            "last": {key: list(value) for key, value in sorted(self._last.items())},
        }, sort_keys=True, separators=(",", ":")).encode()

    def prepare(self, oriented: OrientedAction, *, event: ContinuousEvent,
                index: int) -> PreparedVQFTiltClockJoin:
        before = self.owner_bytes()
        try:
            if event.kind != "IMU" or type(event.payload_owner) is not TypedEvent:
                raise ValueError("VQF clock join requires one decoded IMU event")
            validate_event_clock(event, self.clock_owner)
            record = event.payload_owner
            if (record.record_type is not RecordType.IMU
                    or record.status is not EventStatus.DECODED or record.raw is None):
                raise ValueError("VQF clock join lacks decoded IMU provenance")
            node = event.node_id
            provenance = oriented.vqf_tilt_diagnostic_provenance
            if (
                type(provenance) is not VQFTiltDiagnosticProvenance
                or provenance.digest != self.diagnostic_provenance_digest
                or provenance.product_ready
            ):
                raise ValueError("foreign or missing VQF diagnostic provenance")
            binding = self.clock_owner.binding_for(node)
            time = oriented.time_us_by_node[node]
            count = len(time)
            if type(index) is not int or not 0 <= index < count:
                raise ValueError("VQF diagnostic index outside source inventory")
            arrays = (
                oriented.derived_boot_epoch_by_node[node],
                oriented.contiguous_span_id_by_node[node],
                oriented.imu_sample_sequence_by_node[node],
                oriented.raw_start_offset_by_node[node],
                oriented.raw_end_offset_by_node[node],
                oriented.raw_sample_index_by_node[node],
                oriented.vqf_residual_bias_sigma_rad_s_by_node[node],
                oriented.vqf_rest_detected_by_node[node],
                oriented.vqf_relative_rest_deviation_by_node[node],
                oriented.acceleration_norm_residual_mps2_by_node[node],
                oriented.world_tilt_innovation_rad_by_node[node],
            )
            if any(len(value) != count for value in arrays):
                raise ValueError("VQF diagnostic row inventory mismatch")
            boot = int(arrays[0][index])
            if boot != binding.boot_epoch:
                raise ValueError("VQF diagnostic boot differs from clock binding")
            timer2_us = int(time[index])
            common_ns = binding.global_ns(timer2_us)
            sequence = int(arrays[2][index])
            raw = record.raw
            base = record.payload.get("base_timer2_us")
            delta = record.payload.get("delta_us")
            expected_event_identity = (
                f"v47:{raw.record_index}:{raw.sample_index}:"
                f"{raw.start_offset}:{raw.end_offset}:{raw.encoded_sha256}"
            )
            if (
                event.action_id != oriented.action
                or event.imu_timer2.trigger_timer2_us != timer2_us
                or type(base) is not int or type(delta) is not int
                or base + delta != timer2_us
                or event.imu_timer2.timer2_base_us != base
                or event.common_global_ns != common_ns
                or record.node_id != node or record.boot_epoch != boot
                or record.node_timer_us != timer2_us or record.sequence != sequence
                or raw.start_offset != int(arrays[3][index])
                or raw.end_offset != int(arrays[4][index])
                or raw.sample_index != int(arrays[5][index])
                or event.event_id != expected_event_identity
            ):
                raise ValueError("VQF diagnostic row differs from decoded IMU event")
            event_identity = event.event_id
            identity = (
                sequence, timer2_us, common_ns,
                event.availability_global_ns, event_identity,
            )
            previous = self._last.get(node)
            if previous is not None and (
                timer2_us <= previous[1] or common_ns <= previous[2]
                or event.availability_global_ns < previous[3]
                or event_identity == previous[4]
            ):
                raise ValueError("VQF diagnostic is stale or replayed")
            source_digest = verify_vqf_tilt_source_binding(oriented, node)
            evidence = UnqualifiedVQFTiltClockEvidence(
                event_identity=event_identity, action_id=event.action_id,
                protocol_action_index=event.action_index,
                acquired_chronological_index=oriented.chronological_index,
                node=node, boot_epoch=boot,
                span_id=int(arrays[1][index]), source_sequence=sequence,
                timer2_us=timer2_us, common_global_ns=common_ns,
                availability_global_ns=event.availability_global_ns,
                raw_start_offset=int(arrays[3][index]), raw_end_offset=int(arrays[4][index]),
                raw_sample_index=int(arrays[5][index]), clock_domain=binding.clock_domain,
                clock_mapping_digest=binding.clock_mapping_digest,
                clock_owner_digest=self.clock_owner_digest,
                diagnostic_provenance_digest=provenance.digest,
                diagnostic_source_binding_digest=source_digest,
                rest_detected=bool(arrays[7][index]), bias_sigma_rad_s=float(arrays[6][index]),
                relative_rest_deviation_gyro=float(arrays[8][index, 0]),
                relative_rest_deviation_acceleration=float(arrays[8][index, 1]),
                acceleration_norm_residual_mps2=float(arrays[9][index]),
                world_tilt_innovation_rad=float(arrays[10][index]),
            )
            digest = _digest({
                "schema": "biospur.c2.prepared_vqf_tilt_clock_join.v1",
                "revision": self._revision, "previous": previous,
                "next": identity, "evidence": evidence.digest,
            })
            return PreparedVQFTiltClockJoin(
                self.__authority, self._revision, previous, identity, evidence, digest,
            )
        except Exception:
            if self.owner_bytes() != before:
                raise RuntimeError("VQF clock join mutated during rejected prepare")
            raise

    def commit(self, plan: PreparedVQFTiltClockJoin) -> UnqualifiedVQFTiltClockEvidence:
        if (
            plan.authority is not self.__authority or plan.revision != self._revision
            or self._last.get(plan.evidence.node) != plan.previous_identity
            or plan.digest != _digest({
                "schema": "biospur.c2.prepared_vqf_tilt_clock_join.v1",
                "revision": plan.revision, "previous": plan.previous_identity,
                "next": plan.next_identity, "evidence": plan.evidence.digest,
            })
        ):
            raise ValueError("stale, foreign, or mutated VQF clock-join plan")
        self._last[plan.evidence.node] = plan.next_identity
        self._revision += 1
        return plan.evidence
