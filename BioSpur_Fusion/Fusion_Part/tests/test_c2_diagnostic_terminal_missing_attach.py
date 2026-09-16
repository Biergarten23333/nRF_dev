from __future__ import annotations

from dataclasses import replace
import pickle

import numpy as np
import pytest

import biospur_fusion.c2_uwb_root_world.diagnostic_terminal_missing_attach as attach_module

from biospur_fusion.c2_coupled_progressive.action00_missing_tilt_adapter import (
    load_action00_missing_tilt_initialization,
)
from biospur_fusion.c2_uwb_root_world.diagnostic_terminal_missing_attach import (
    PELVIS_NODE,
    attach_action00_terminal_missing_to_shared_root,
)
from biospur_fusion.c2_uwb_root_world.gap_tilt_recovery import GapTiltRecoveryConfig
from biospur_fusion.c2_uwb_root_world.gap_tilt_recovery import (
    CausalTiltTrustStateMachine,
    GapTiltRecoveryController,
)
from biospur_fusion.root_r3 import (
    CausalDelayedRootFilter,
    ImuSample,
    PositionObservation,
    RootFilterConfig,
    RootState,
    RootTranslationEdgeMode,
)

from test_c2_action00_missing_tilt_adapter import _clock_owner
from test_c2_gap_tilt_recovery import _missing_evidence, _missing_issuer


def _root_at_terminal(terminal_s: float) -> CausalDelayedRootFilter:
    state = RootState(
        terminal_s - 0.005,
        np.array([1.0, 2.0, 0.8, 0.1, -0.2, 0.3, 0.01, -0.02, 0.03]),
        np.eye(9) * 0.2,
    )
    root = CausalDelayedRootFilter(
        state, RootFilterConfig(fixed_lag_s=2.0), inertial=True,
    )
    assert root.add_imu(ImuSample(
        terminal_s, terminal_s, np.array([0.4, -0.2, 9.7]), np.eye(3), 4,
    ))
    return root


def _root_bytes(root: CausalDelayedRootFilter) -> bytes:
    return pickle.dumps(root._prepare_position_rollback(), protocol=5)


def _snapshot_fields(row):
    edge = row.incoming_edge
    edge_fields = None if edge is None else (
        edge.start_time_s, edge.end_time_s,
        edge.force_sensor_mps2.tobytes(),
        edge.rotation_world_from_sensor.tobytes(),
        edge.inertial, edge.acceleration_noise_variance,
        edge.endpoint_noise_covariance.tobytes(),
        edge.full_edge_process_noise_covariance.tobytes(), edge.input_owner,
    )
    return (
        row.state.time_s, row.state.vector.tobytes(), row.state.covariance.tobytes(),
        row.applied_constraint_cursor, edge_fields,
    )


def test_terminal_missing_attach_preserves_history_and_only_switches_future_mode():
    clock = _clock_owner()
    initialized = load_action00_missing_tilt_initialization(clock)
    terminal = initialized.policy.terminal_identities[PELVIS_NODE]
    root = _root_at_terminal(terminal.common_global_ns * 1e-9)
    token = root.publication_token()
    state_before = (
        root.current_state.time_s, root.current_state.vector.tobytes(),
        root.current_state.covariance.tobytes(),
    )
    snapshots_before = pickle.dumps(root._snapshots, protocol=5)
    constraints_before = pickle.dumps(root._constraint_events, protocol=5)
    attachment = attach_action00_terminal_missing_to_shared_root(
        root=root, initialization=initialized, clock_owner=clock,
        expected_root_publication=token,
    )
    assert not attachment.product_ready and not attachment.scientific_pass
    assert root.publication_token().revision == token.revision + 1
    assert root._following_input_mode is RootTranslationEdgeMode.CV_NO_ACCELERATION
    assert (root.current_state.time_s, root.current_state.vector.tobytes(),
            root.current_state.covariance.tobytes()) == state_before
    assert pickle.dumps(root._snapshots, protocol=5) == snapshots_before
    assert pickle.dumps(root._constraint_events, protocol=5) == constraints_before


def test_terminal_missing_attach_rejects_stale_repeat_and_wrong_epoch_byte_inert():
    clock = _clock_owner()
    initialized = load_action00_missing_tilt_initialization(clock)
    terminal_s = initialized.policy.terminal_identities[PELVIS_NODE].common_global_ns * 1e-9
    root = _root_at_terminal(terminal_s)
    token = root.publication_token()
    stale = _root_at_terminal(terminal_s).publication_token()
    before = _root_bytes(root)
    with pytest.raises(RuntimeError):
        attach_action00_terminal_missing_to_shared_root(
            root=root, initialization=initialized, clock_owner=clock,
            expected_root_publication=stale,
        )
    assert _root_bytes(root) == before
    attachment = attach_action00_terminal_missing_to_shared_root(
        root=root, initialization=initialized, clock_owner=clock,
        expected_root_publication=token,
    )
    after = _root_bytes(root)
    with pytest.raises(RuntimeError):
        attach_action00_terminal_missing_to_shared_root(
            root=root, initialization=initialized, clock_owner=clock,
            expected_root_publication=attachment.controller.root.publication_token(),
        )
    assert _root_bytes(root) == after

    wrong_epoch = _root_at_terminal(terminal_s + 0.005)
    wrong_before = _root_bytes(wrong_epoch)
    with pytest.raises(ValueError, match="ownership mismatch"):
        attach_action00_terminal_missing_to_shared_root(
            root=wrong_epoch, initialization=initialized, clock_owner=clock,
            expected_root_publication=wrong_epoch.publication_token(),
        )
    assert _root_bytes(wrong_epoch) == wrong_before


def test_terminal_missing_attach_rejects_foreign_typed_clock_without_mutation():
    clock = _clock_owner()
    initialized = load_action00_missing_tilt_initialization(clock)
    terminal_s = initialized.policy.terminal_identities[PELVIS_NODE].common_global_ns * 1e-9
    root = _root_at_terminal(terminal_s)
    bindings = tuple(
        replace(row, clock_mapping_digest="f" * 64) if row.node_id == PELVIS_NODE else row
        for row in clock.bindings
    )
    foreign = replace(clock, bindings=bindings)
    before = _root_bytes(root)
    with pytest.raises(ValueError, match="clock/policy"):
        attach_action00_terminal_missing_to_shared_root(
            root=root, initialization=initialized, clock_owner=foreign,
            expected_root_publication=root.publication_token(),
        )
    assert _root_bytes(root) == before


def test_facade_postvalidation_failure_rolls_back_complete_root(monkeypatch):
    clock = _clock_owner()
    initialized = load_action00_missing_tilt_initialization(clock)
    terminal_s = initialized.policy.terminal_identities[PELVIS_NODE].common_global_ns * 1e-9
    root = _root_at_terminal(terminal_s)
    before = _root_bytes(root)

    def fail_result(*_args, **_kwargs):
        raise RuntimeError("injected final validation failure")

    monkeypatch.setattr(attach_module, "DiagnosticTerminalMissingAttachment", fail_result)
    with pytest.raises(RuntimeError, match="injected final"):
        attach_module.attach_action00_terminal_missing_to_shared_root(
            root=root, initialization=initialized, clock_owner=clock,
            expected_root_publication=root.publication_token(),
        )
    assert _root_bytes(root) == before


def test_delayed_uwb_tail_remains_inertial_until_missing_is_available_then_cv():
    config = RootFilterConfig(fixed_lag_s=2.0)
    root = CausalDelayedRootFilter(
        RootState(0.0, np.zeros(9), np.eye(9) * 0.2), config, inertial=True,
    )
    assert root.add_imu(ImuSample(
        0.005, 0.005, np.array([0.5, 0.0, 9.80665]), np.eye(3), 1,
    ))
    before = root.current_state
    decision = root.add_position(
        PositionObservation(
            0.0025, 0.0055, before.position_m + np.array([0.01, 0.0, 0.0]),
            np.eye(3) * 0.02, "body", (0, 1, 2, 3), source_sequence=9,
        ),
        processing_time_s=0.0055,
        state_update_indices=(0, 1, 2),
    )
    assert decision.accepted
    delayed_state_before = root.committed_state_at(0.0025).state
    assert all(
        row.incoming_edge is None or row.incoming_edge.inertial
        for row in root._snapshots if row.state.time_s <= 0.0055
    )
    initial = _missing_evidence(
        1, 0.005, availability_time_s=0.006, event_identity="terminal-missing",
    )
    trust = CausalTiltTrustStateMachine(
        GapTiltRecoveryConfig(0.005, 200), _missing_issuer(), initial,
    )
    controller = GapTiltRecoveryController.attach_terminal_missing(
        root, trust, expected_root_publication=root.publication_token(),
    )
    assert root.current_state.time_s == pytest.approx(0.006)
    assert root._snapshots[-1].incoming_edge is not None
    assert root._snapshots[-1].incoming_edge.inertial
    prior_history = tuple(
        _snapshot_fields(row) for row in root._snapshots if row.state.time_s <= 0.006
    )
    assert controller.add_imu(
        ImuSample(0.010, 0.010, np.array([9.0, 0.0, 9.80665]), np.eye(3), 2),
        _missing_evidence(2, 0.010, event_identity="post-terminal-missing"),
    )
    before_uwb = root.current_state
    uwb = root.add_position(
        PositionObservation(
            0.0075, 0.0105,
            before_uwb.position_m + np.array([0.005, 0.0, 0.0]),
            np.eye(3) * 0.02, "body", (0, 1, 2, 3), source_sequence=10,
        ),
        processing_time_s=0.0105,
        state_update_indices=(0, 1, 2),
    )
    assert uwb.accepted
    assert root.current_state.vector[3:9].tobytes() == before_uwb.vector[3:9].tobytes()
    assert tuple(
        _snapshot_fields(row) for row in root._snapshots if row.state.time_s <= 0.006
    ) == prior_history
    assert all(
        row.incoming_edge is None or not row.incoming_edge.inertial
        for row in root._snapshots if row.state.time_s > 0.006
    )
    delayed_state_after = root.committed_state_at(0.0025).state
    assert delayed_state_after.vector.tobytes() == delayed_state_before.vector.tobytes()
    assert delayed_state_after.covariance.tobytes() == delayed_state_before.covariance.tobytes()
    assert root._snapshots[-1].incoming_edge is not None
    assert not root._snapshots[-1].incoming_edge.inertial
