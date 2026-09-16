"""Diagnostic-only terminal attachment of the sealed Action00 MISSING owner."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from biospur_fusion.c2_coupled_progressive.action00_missing_tilt_adapter import (
    Action00MissingTiltInitialization,
)
from biospur_fusion.c2_coupled_progressive.action00_tilt_trust_policy import (
    REQUIRED_CONSECUTIVE_QUALIFYING_FRAMES,
)
from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    ContinuousClockOwner,
    continuous_clock_owner_digest,
)
from biospur_fusion.root_r3 import CausalDelayedRootFilter

from .gap_tilt_recovery import (
    CausalTiltTrustStateMachine,
    GapTiltRecoveryConfig,
    GapTiltRecoveryController,
    TiltEvidenceStatus,
)


PELVIS_NODE = "BSFC2CC"


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _state_bytes(root: CausalDelayedRootFilter) -> tuple[float, bytes, bytes]:
    state = root.current_state
    return state.time_s, state.vector.tobytes(), state.covariance.tobytes()


@dataclass(frozen=True)
class DiagnosticTerminalMissingAttachment:
    controller: GapTiltRecoveryController
    owner_digest: str
    expected_pre_attach_revision: int
    expected_post_attach_revision: int
    post_attach_publication_digest: str
    qualification: str = "DIAGNOSTIC_ONLY_NON_PROMOTABLE"

    def __post_init__(self) -> None:
        if (
            self.qualification != "DIAGNOSTIC_ONLY_NON_PROMOTABLE"
            or not self.owner_digest
            or self.controller.production_ready
            or self.controller.root.publication_token().revision
            != self.expected_post_attach_revision
            or self.controller.root.publication_token().digest
            != self.post_attach_publication_digest
            or not self.expected_pre_attach_revision < self.expected_post_attach_revision
            or self.controller.trust.current_evidence.status
            is not TiltEvidenceStatus.MISSING
        ):
            raise ValueError("terminal MISSING attachment is not diagnostic-only")

    @property
    def product_ready(self) -> bool:
        return False

    @property
    def scientific_pass(self) -> bool:
        return False


def attach_action00_terminal_missing_to_shared_root(
    *,
    root: CausalDelayedRootFilter,
    initialization: Action00MissingTiltInitialization,
    clock_owner: ContinuousClockOwner,
    expected_root_publication: object,
) -> DiagnosticTerminalMissingAttachment:
    """Switch only future root edges after exact terminal MISSING evidence."""
    if type(root) is not CausalDelayedRootFilter:
        raise TypeError("terminal attachment requires CausalDelayedRootFilter")
    if type(initialization) is not Action00MissingTiltInitialization:
        raise TypeError("terminal attachment requires the sealed typed Action00 result")
    if type(clock_owner) is not ContinuousClockOwner:
        raise TypeError("terminal attachment requires the typed common clock owner")
    rows = tuple(row for row in initialization.nodes if row.evidence.source_node == PELVIS_NODE)
    if len(rows) != 1:
        raise ValueError("sealed Action00 MISSING result lacks one pelvis terminal")
    row = rows[0]
    binding = clock_owner.binding_for(PELVIS_NODE)
    if (
        row.evidence.boot_epoch != binding.boot_epoch
        or row.evidence.clock_domain != binding.clock_domain
        or row.evidence.clock_mapping_digest != binding.clock_mapping_digest
        or row.issuer.clock_mapping_digest != binding.clock_mapping_digest
        or row.issuer.source_owner_digest
        != initialization.policy.diagnostic_source_binding_digests[PELVIS_NODE]
        or row.issuer.missing_policy_digest != initialization.policy.digest
    ):
        raise ValueError("terminal MISSING evidence does not match the typed clock/policy")
    cadence_s = (
        binding.global_ns(row.terminal.timer2_us + 5_000)
        - binding.global_ns(row.terminal.timer2_us)
    ) * 1e-9
    recovery_config = GapTiltRecoveryConfig(
        cadence_s, REQUIRED_CONSECUTIVE_QUALIFYING_FRAMES,
    )
    current = root.publication_token()
    if (
        expected_root_publication is not current
        and (
            getattr(expected_root_publication, "revision", None) != current.revision
            or getattr(expected_root_publication, "digest", None) != current.digest
        )
    ):
        raise RuntimeError("terminal attachment expected a stale root publication")
    outer_rollback = root._prepare_position_rollback()
    before_state = _state_bytes(root)
    was_at_availability = abs(
        root.current_state.time_s - row.evidence.availability_time_s
    ) <= 1e-12
    try:
        trust = CausalTiltTrustStateMachine(recovery_config, row.issuer, row.evidence)
        boundary_owner = _digest({
            "schema": "biospur.c2.diagnostic_terminal_missing_attach.v1",
            "qualification": "DIAGNOSTIC_ONLY_NON_PROMOTABLE",
            "clock_owner": continuous_clock_owner_digest(clock_owner),
            "policy": initialization.policy.digest,
            "terminal": row.terminal.digest,
            "issuer": row.issuer.digest,
            "evidence": row.evidence.digest,
            "root_publication": current.digest,
        })
        controller = GapTiltRecoveryController.attach_terminal_missing(
            root, trust, expected_root_publication=expected_root_publication,
        )
        if was_at_availability and _state_bytes(root) != before_state:
            raise RuntimeError("terminal MISSING attachment changed root numeric state")
        if abs(root.current_state.time_s - row.evidence.availability_time_s) > 1e-12:
            raise RuntimeError("terminal MISSING attachment did not reach availability")
        owner = controller.terminal_attachment_owner_digest
        if owner is None:
            raise RuntimeError("terminal MISSING controller lacks its attachment owner")
        owner = _digest({"boundary": boundary_owner, "root_attachment": owner})
        post = root.publication_token()
        return DiagnosticTerminalMissingAttachment(
            controller, owner, current.revision, post.revision, post.digest,
        )
    except Exception:
        root._rollback_prevalidated_position(outer_rollback)
        raise
