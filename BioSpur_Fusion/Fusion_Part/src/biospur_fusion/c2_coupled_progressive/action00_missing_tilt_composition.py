"""Inert controller composition for the sealed Action00 MISSING outcome."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import pickle
from typing import Mapping

import numpy as np

from .action00_missing_tilt_adapter import (
    Action00MissingTiltNodeInitialization,
    load_action00_missing_tilt_initialization,
)
from .continuous_frontend import ContinuousClockOwner
from biospur_fusion.c2_uwb_root_world.gap_tilt_recovery import (
    CausalTiltTrustStateMachine,
    GapTiltRecoveryConfig,
    GapTiltRecoveryController,
    TiltTrustState,
)
from biospur_fusion.root_r3 import (
    CausalDelayedRootFilter,
    RootFilterConfig,
    RootState,
    RootTranslationEdgeMode,
)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _state_bytes(state: RootState) -> tuple[float, bytes, bytes]:
    return state.time_s, state.vector.tobytes(), state.covariance.tobytes()


def _root_owner_bytes(root: CausalDelayedRootFilter) -> bytes:
    """Snapshot every field covered by the root owner's atomic rollback."""

    return pickle.dumps(root._prepare_position_rollback(), protocol=5)


@dataclass(frozen=True)
class Action00MissingTiltControllerOwner:
    """One isolated controller plus its post-constructor owner baselines."""

    initialization: Action00MissingTiltNodeInitialization
    controller: GapTiltRecoveryController
    root_owner_bytes: bytes
    trust_owner_bytes: bytes
    config_digest: str

    def __post_init__(self) -> None:
        root = self.controller.root
        trust = self.controller.trust
        if (
            trust.issuer is not self.initialization.issuer
            or trust.state is not TiltTrustState.UNTRUSTED
            or root.publication_token().revision != 1
            or root._following_input_mode
            is not RootTranslationEdgeMode.CV_NO_ACCELERATION
            or self.root_owner_bytes != _root_owner_bytes(root)
            or self.trust_owner_bytes != trust.owner_bytes()
        ):
            raise ValueError("Action00 MISSING controller baseline is inconsistent")

    @property
    def node(self) -> str:
        return self.initialization.evidence.source_node


@dataclass(frozen=True)
class Action00MissingTiltControllerComposition:
    """Ten sorted, isolated, mechanism-only controller owners."""

    nodes: tuple[Action00MissingTiltControllerOwner, ...]
    policy_digest: str
    digest: str
    qualification: str = "MECHANISM_ONLY_UNQUALIFIED"

    def __post_init__(self) -> None:
        names = tuple(row.node for row in self.nodes)
        if (
            self.qualification != "MECHANISM_ONLY_UNQUALIFIED"
            or len(names) != 10
            or names != tuple(sorted(set(names)))
        ):
            raise ValueError("Action00 MISSING composition requires ten sorted nodes")
        if len({id(row.controller.root) for row in self.nodes}) != 10:
            raise ValueError("Action00 MISSING roots are aliased")
        if len({id(row.controller.trust) for row in self.nodes}) != 10:
            raise ValueError("Action00 MISSING trust owners are aliased")
        expected = _digest({
            "schema": "biospur.c2.action00_missing_tilt_controller_composition.v1",
            "policy_digest": self.policy_digest,
            "nodes": [{
                "node": row.node,
                "terminal_digest": row.initialization.terminal.digest,
                "issuer_digest": row.initialization.issuer.digest,
                "evidence_digest": row.initialization.evidence.digest,
                "root_owner": hashlib.sha256(row.root_owner_bytes).hexdigest(),
                "trust_owner": hashlib.sha256(row.trust_owner_bytes).hexdigest(),
                "config_digest": row.config_digest,
            } for row in self.nodes],
            "product_ready": False,
            "scientific_pass": False,
            "qualification": self.qualification,
        })
        if self.digest != expected:
            raise ValueError("Action00 MISSING composition digest mismatch")

    @property
    def product_ready(self) -> bool:
        return False

    @property
    def scientific_pass(self) -> bool:
        return False

    @property
    def architecture_ready(self) -> bool:
        return False


def compose_action00_missing_tilt_controllers(
    *,
    clock_owner: ContinuousClockOwner,
    initial_root_states: Mapping[str, RootState],
    root_config: RootFilterConfig,
    recovery_config: GapTiltRecoveryConfig,
) -> Action00MissingTiltControllerComposition:
    """Construct isolated CV-mode controllers without consuming an event."""

    if type(root_config) is not RootFilterConfig:
        raise TypeError("composition requires RootFilterConfig")
    if type(recovery_config) is not GapTiltRecoveryConfig:
        raise TypeError("composition requires GapTiltRecoveryConfig")
    initialized = load_action00_missing_tilt_initialization(clock_owner)
    seeds = dict(initial_root_states)
    expected_nodes = {row.evidence.source_node for row in initialized.nodes}
    if set(seeds) != expected_nodes or any(type(row) is not RootState for row in seeds.values()):
        raise ValueError("root seed inventory differs from Action00 policy")
    config_digest = _digest({
        "root": asdict(root_config),
        "recovery": asdict(recovery_config),
        "inertial_capable": True,
    })
    rows = []
    for initialization in initialized.nodes:
        node = initialization.evidence.source_node
        seed = seeds[node]
        if seed.time_s != initialization.evidence.measurement_time_s:
            raise ValueError("root seed epoch differs from Action00 terminal identity")
        owned_seed = RootState(
            seed.time_s,
            np.array(seed.vector, dtype=float, copy=True),
            np.array(seed.covariance, dtype=float, copy=True),
        )
        before_state = _state_bytes(owned_seed)
        root = CausalDelayedRootFilter(owned_seed, root_config, inertial=True)
        if root.publication_token().revision != 0:
            raise RuntimeError("new root owner is not pristine")
        trust = CausalTiltTrustStateMachine(
            recovery_config, initialization.issuer, initialization.evidence,
        )
        controller = GapTiltRecoveryController(root, trust)
        if (
            _state_bytes(root.current_state) != before_state
            or root.publication_token().revision != 1
            or root._following_input_mode
            is not RootTranslationEdgeMode.CV_NO_ACCELERATION
        ):
            raise RuntimeError("controller constructor changed pristine root state")
        rows.append(Action00MissingTiltControllerOwner(
            initialization=initialization,
            controller=controller,
            root_owner_bytes=_root_owner_bytes(root),
            trust_owner_bytes=trust.owner_bytes(),
            config_digest=config_digest,
        ))
    payload = {
        "schema": "biospur.c2.action00_missing_tilt_controller_composition.v1",
        "policy_digest": initialized.policy.digest,
        "nodes": [{
            "node": row.node,
            "terminal_digest": row.initialization.terminal.digest,
            "issuer_digest": row.initialization.issuer.digest,
            "evidence_digest": row.initialization.evidence.digest,
            "root_owner": hashlib.sha256(row.root_owner_bytes).hexdigest(),
            "trust_owner": hashlib.sha256(row.trust_owner_bytes).hexdigest(),
            "config_digest": row.config_digest,
        } for row in rows],
        "product_ready": False,
        "scientific_pass": False,
        "qualification": "MECHANISM_ONLY_UNQUALIFIED",
    }
    return Action00MissingTiltControllerComposition(
        tuple(rows), initialized.policy.digest, _digest(payload),
    )
