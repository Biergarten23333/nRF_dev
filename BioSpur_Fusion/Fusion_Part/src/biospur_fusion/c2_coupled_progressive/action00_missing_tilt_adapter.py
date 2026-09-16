"""Fail-closed bridge from the sealed Action00 MISSING policy to tilt trust.

This module owns no clock, threshold, or mutable recovery state.  It pins the
one sealed engineering result, rehydrates it through the public policy loader
using the caller's typed common-clock owner, and materializes only MISSING
initial evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat

from .action00_tilt_trust_policy import (
    Action00TerminalIdentity,
    EngineeringAction00TiltPolicy,
    load_engineering_action00_tilt_policy_result,
)
from .continuous_frontend import ContinuousClockOwner
from biospur_fusion.c2_uwb_root_world.gap_tilt_recovery import (
    MissingTiltDiagnosticIssuerBinding,
    TiltEvidenceStatus,
    TiltTrustFrameEvidence,
)


def _read_sealed(path: Path, expected_sha256: str) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise ValueError("sealed Action00 evidence cannot be opened safely") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_mode & 0o222:
            raise ValueError("sealed Action00 evidence is not read-only regular data")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read()
        after = os.fstat(descriptor)
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or len(payload) != before.st_size
        ):
            raise ValueError("sealed Action00 evidence changed while loading")
    finally:
        os.close(descriptor)
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("sealed Action00 evidence SHA-256 mismatch")
    return payload


@dataclass(frozen=True)
class Action00MissingTiltNodeInitialization:
    """One exact terminal identity and its non-promotable MISSING owners."""

    terminal: Action00TerminalIdentity
    issuer: MissingTiltDiagnosticIssuerBinding
    evidence: TiltTrustFrameEvidence

    def __post_init__(self) -> None:
        if (
            self.evidence.status is not TiltEvidenceStatus.MISSING
            or self.evidence.event_identity != self.terminal.event_identity
            or self.evidence.source_sequence != self.terminal.source_sequence
            or self.evidence.boot_epoch != self.terminal.boot_epoch
            or self.evidence.span_id != str(self.terminal.span_id)
            or self.evidence.measurement_time_s != self.terminal.common_global_ns * 1e-9
            or self.evidence.availability_time_s
            != self.terminal.availability_global_ns * 1e-9
            or self.issuer.source_node != self.evidence.source_node
            or self.issuer.boot_epoch != self.evidence.boot_epoch
            or self.issuer.clock_domain != self.evidence.clock_domain
            or self.issuer.clock_mapping_digest != self.evidence.clock_mapping_digest
            or self.issuer.source_owner_digest != self.evidence.source_owner_digest
            or self.evidence.issuer_binding_digest != self.issuer.digest
        ):
            raise ValueError("Action00 MISSING initialization identity mismatch")


@dataclass(frozen=True)
class Action00MissingTiltInitialization:
    """Immutable all-node output of the sealed MISSING-policy adapter."""

    policy: EngineeringAction00TiltPolicy
    nodes: tuple[Action00MissingTiltNodeInitialization, ...]

    def __post_init__(self) -> None:
        if (
            self.policy.status != "MISSING"
            or self.policy.product_ready
            or self.policy.scientific_pass
            or tuple(row.evidence.source_node for row in self.nodes)
            != tuple(sorted(self.policy.expected_nodes))
            or any(
                row.terminal is not self.policy.terminal_identities[row.evidence.source_node]
                or row.issuer.source_owner_digest
                != self.policy.diagnostic_source_binding_digests[row.evidence.source_node]
                or row.issuer.missing_policy_digest != self.policy.digest
                for row in self.nodes
            )
        ):
            raise ValueError("Action00 result is not the exact all-node MISSING policy")

    @property
    def product_ready(self) -> bool:
        return False

    @property
    def scientific_pass(self) -> bool:
        return False


def load_action00_missing_tilt_initialization(
    clock_owner: ContinuousClockOwner,
) -> Action00MissingTiltInitialization:
    """Load and materialize the pinned Action00 v2 MISSING result exactly once."""

    if type(clock_owner) is not ContinuousClockOwner:
        raise TypeError("Action00 MISSING adapter requires ContinuousClockOwner")
    workspace = Path(__file__).resolve().parents[3]
    result_path = workspace / (
        "logs/c2_action00_engineering_policy_v2_prereg_20260908T221958Z/"
        "run/RESULT.json"
    )
    result_sha256 = (
        "ac08ae6d22d3be9ef6933450c07b6d7a13b65341735e535dcb95c1c564be61fc"
    )
    evidence_path = workspace / (
        "logs/c2_action00_engineering_policy_v2_prereg_20260908T221958Z/"
        "SHA256SUMS"
    )
    evidence_sha256 = (
        "add5d8bc6984532c24c6da9c01abbdf28e73d1d76b0185664d45a2dec92f055b"
    )
    missing_policy_digest = (
        "802a322874da1f4f0544f8b6931ad8b1dff4967523370866e6a868b07d464d12"
    )
    evidence_payload = _read_sealed(
        evidence_path, evidence_sha256,
    )
    result_row = f"{result_sha256}  run/RESULT.json".encode("ascii")
    if result_row not in evidence_payload.splitlines():
        raise ValueError("sealed Action00 evidence does not bind the pinned result")
    policy = load_engineering_action00_tilt_policy_result(
        result_path,
        expected_result_sha256=result_sha256,
        clock_owner=clock_owner,
    )
    if policy.status != "MISSING" or policy.digest != missing_policy_digest:
        raise ValueError("sealed Action00 policy is not the pinned MISSING outcome")

    rows = []
    for node in sorted(policy.expected_nodes):
        terminal = policy.terminal_identities[node]
        binding = clock_owner.binding_for(node)
        source_owner_digest = policy.diagnostic_source_binding_digests[node]
        issuer = MissingTiltDiagnosticIssuerBinding(
            source_node=node,
            boot_epoch=binding.boot_epoch,
            clock_domain=binding.clock_domain,
            clock_mapping_digest=binding.clock_mapping_digest,
            source_owner_digest=source_owner_digest,
            missing_policy_digest=policy.digest,
        )
        evidence = TiltTrustFrameEvidence(
            event_identity=terminal.event_identity,
            measurement_time_s=terminal.common_global_ns * 1e-9,
            availability_time_s=terminal.availability_global_ns * 1e-9,
            source_sequence=terminal.source_sequence,
            source_node=node,
            boot_epoch=terminal.boot_epoch,
            span_id=str(terminal.span_id),
            clock_domain=binding.clock_domain,
            clock_mapping_digest=binding.clock_mapping_digest,
            source_owner_digest=source_owner_digest,
            status=TiltEvidenceStatus.MISSING,
            tilt_error_rad=None,
            maximum_trusted_tilt_error_rad=None,
            diagnostic_owner_digest=None,
            issuer_binding_digest=issuer.digest,
        )
        rows.append(Action00MissingTiltNodeInitialization(terminal, issuer, evidence))
    return Action00MissingTiltInitialization(policy, tuple(rows))
