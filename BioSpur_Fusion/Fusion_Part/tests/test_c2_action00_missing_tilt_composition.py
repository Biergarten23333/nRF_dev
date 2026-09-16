from dataclasses import replace
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from biospur_fusion.c2_coupled_progressive.action00_missing_tilt_adapter import (
    load_action00_missing_tilt_initialization,
)
from biospur_fusion.c2_coupled_progressive.action00_missing_tilt_composition import (
    _root_owner_bytes,
    compose_action00_missing_tilt_controllers,
)
from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    CONTINUOUS_FRONTEND_SCHEMA,
    ContinuousClockOwner,
    NodeClockBinding,
)
from biospur_fusion.c2_uwb_root_world.authoritative_articulated_fusion import (
    Native200ClockMappingOwner,
)
from biospur_fusion.c2_uwb_root_world.gap_tilt_recovery import GapTiltRecoveryConfig
from biospur_fusion.c2_uwb_root_world.run_calibration import _clock_models
from biospur_fusion.root_r3 import ImuSample, RootFilterConfig, RootState, RootTranslationEdgeMode


ROOT = Path(__file__).resolve().parents[1]
CLOCK_PATH = ROOT / "logs/c2_uwb_beacon_clock_20260903_141552/CLOCK_TABLE_CALIBRATION_ONLY.json"


def _clock_owner():
    document = json.loads(CLOCK_PATH.read_text(encoding="utf-8"))
    owner_sha = hashlib.sha256(CLOCK_PATH.read_bytes()).hexdigest()
    bindings = []
    for node, model in sorted(_clock_models(CLOCK_PATH).items()):
        mapping = Native200ClockMappingOwner(
            node=node, clock_domain="B306_TIMER2", boot_epoch=model.boot_epoch,
            a_ns_per_us=model.a_ns_per_us, b_ns=model.b_ns,
            clock_owner_sha256=owner_sha,
        )
        bindings.append(NodeClockBinding(
            node, model.boot_epoch, "B306_TIMER2", mapping.digest,
            model.a_ns_per_us, model.b_ns, owner_sha,
            str(document["source_sha256"]),
        ))
    return ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, tuple(bindings))


def _inputs(clock=None):
    clock = _clock_owner() if clock is None else clock
    initialized = load_action00_missing_tilt_initialization(clock)
    seeds = {}
    for index, row in enumerate(initialized.nodes):
        vector = np.arange(9, dtype=float) * (index + 1) * 1e-3
        covariance = np.eye(9) * (0.1 + index * 1e-3)
        seeds[row.evidence.source_node] = RootState(
            row.evidence.measurement_time_s, vector, covariance,
        )
    root_config = RootFilterConfig()
    recovery_config = GapTiltRecoveryConfig(0.006, 200)
    return clock, seeds, root_config, recovery_config


def _compose(clock=None, seeds=None):
    owned_clock, owned_seeds, root_config, recovery_config = _inputs(clock)
    return compose_action00_missing_tilt_controllers(
        clock_owner=owned_clock,
        initial_root_states=owned_seeds if seeds is None else seeds,
        root_config=root_config,
        recovery_config=recovery_config,
    ), owned_clock, owned_seeds


def _owner_pair(row):
    return _root_owner_bytes(row.controller.root), row.controller.trust.owner_bytes()


def test_constructs_ten_sorted_isolated_cv_controllers_with_exact_seed_bytes():
    composition, _clock, seeds = _compose()
    assert len(composition.nodes) == 10
    assert tuple(row.node for row in composition.nodes) == tuple(sorted(seeds))
    assert composition.qualification == "MECHANISM_ONLY_UNQUALIFIED"
    assert not composition.product_ready and not composition.scientific_pass
    assert not composition.architecture_ready
    vector_ids = set()
    covariance_ids = set()
    for row in composition.nodes:
        state = row.controller.root.current_state
        seed = seeds[row.node]
        assert state.time_s == seed.time_s
        assert state.vector.tobytes() == seed.vector.tobytes()
        assert state.covariance.tobytes() == seed.covariance.tobytes()
        assert row.controller.root.publication_token().revision == 1
        assert row.controller.root._following_input_mode is RootTranslationEdgeMode.CV_NO_ACCELERATION
        assert row.root_owner_bytes == _root_owner_bytes(row.controller.root)
        assert row.trust_owner_bytes == row.controller.trust.owner_bytes()
        assert not np.shares_memory(state.vector, seed.vector)
        assert not np.shares_memory(state.covariance, seed.covariance)
        vector_ids.add(id(state.vector))
        covariance_ids.add(id(state.covariance))
    assert len(vector_ids) == len(covariance_ids) == 10


def test_one_node_future_event_cannot_mutate_any_other_controller():
    composition, _clock, _seeds = _compose()
    target = composition.nodes[0]
    others_before = {row.node: _owner_pair(row) for row in composition.nodes[1:]}
    previous = target.initialization.evidence
    measurement = previous.measurement_time_s + 0.005
    availability = max(previous.availability_time_s, measurement)
    evidence = replace(
        previous,
        event_identity=previous.event_identity + ":next",
        measurement_time_s=measurement,
        availability_time_s=availability,
        source_sequence=(previous.source_sequence + 1) & 0xFFFF,
        digest="",
    )
    sample = ImuSample(
        measurement, availability, np.array([0.0, 0.0, 9.80665]), np.eye(3),
        evidence.source_sequence,
    )
    assert target.controller.add_imu(sample, evidence)
    assert target.controller.root.publication_token().revision == 2
    assert {row.node: _owner_pair(row) for row in composition.nodes[1:]} == others_before


@pytest.mark.parametrize("field,value", [
    ("source_node", "FOREIGN"),
    ("clock_mapping_digest", "f" * 64),
    ("source_owner_digest", "e" * 64),
])
def test_foreign_future_evidence_rejects_with_exact_root_and_trust_noop(field, value):
    composition, _clock, _seeds = _compose()
    row = composition.nodes[0]
    previous = row.initialization.evidence
    measurement = previous.measurement_time_s + 0.005
    availability = max(previous.availability_time_s, measurement)
    evidence = replace(
        previous,
        event_identity=previous.event_identity + ":foreign",
        measurement_time_s=measurement,
        availability_time_s=availability,
        source_sequence=(previous.source_sequence + 1) & 0xFFFF,
        digest="",
        **{field: value},
    )
    sample = ImuSample(
        measurement, availability, np.array([0.0, 0.0, 9.80665]), np.eye(3),
        evidence.source_sequence,
    )
    before = _owner_pair(row)
    with pytest.raises(ValueError, match="issuer|stale|foreign|misassociated"):
        row.controller.add_imu(sample, evidence)
    assert _owner_pair(row) == before


def test_terminal_replay_and_future_misassociation_are_exact_noops():
    composition, _clock, _seeds = _compose()
    row = composition.nodes[0]
    previous = row.initialization.evidence
    before = _owner_pair(row)
    replay_sample = ImuSample(
        previous.measurement_time_s, previous.availability_time_s,
        np.array([0.0, 0.0, 9.80665]), np.eye(3), previous.source_sequence,
    )
    with pytest.raises(ValueError, match="stale|foreign|misassociated"):
        row.controller.add_imu(replay_sample, previous)
    assert _owner_pair(row) == before
    measurement = previous.measurement_time_s + 0.005
    availability = max(previous.availability_time_s, measurement)
    future = replace(
        previous,
        event_identity=previous.event_identity + ":future",
        measurement_time_s=measurement,
        availability_time_s=availability,
        source_sequence=(previous.source_sequence + 1) & 0xFFFF,
        digest="",
    )
    mismatched = ImuSample(
        measurement, availability, np.array([0.0, 0.0, 9.80665]), np.eye(3),
        (future.source_sequence + 1) & 0xFFFF,
    )
    with pytest.raises(ValueError, match="stale|foreign|misassociated"):
        row.controller.add_imu(mismatched, future)
    assert _owner_pair(row) == before


def test_valid_future_evidence_root_false_rolls_back_full_root_and_trust_owner():
    composition, _clock, _seeds = _compose()
    row = composition.nodes[0]
    previous = row.initialization.evidence
    measurement = previous.measurement_time_s + 0.005
    availability = max(previous.availability_time_s, measurement)
    evidence = replace(
        previous,
        event_identity=previous.event_identity + ":m1-invalid",
        measurement_time_s=measurement,
        availability_time_s=availability,
        source_sequence=(previous.source_sequence + 1) & 0xFFFF,
        digest="",
    )
    sample = ImuSample(
        measurement, availability, np.array([0.0, 0.0, 9.80665]), np.eye(3),
        evidence.source_sequence, m1_valid=False,
    )
    before = _owner_pair(row)
    assert not row.controller.add_imu(sample, evidence)
    assert _owner_pair(row) == before


def test_wrong_seed_inventory_and_foreign_clock_reject_before_mutating_inputs():
    clock, seeds, root_config, recovery_config = _inputs()
    seed_bytes = {node: (row.vector.tobytes(), row.covariance.tobytes()) for node, row in seeds.items()}
    incomplete = dict(seeds)
    incomplete.pop(next(iter(incomplete)))
    with pytest.raises(ValueError, match="inventory"):
        compose_action00_missing_tilt_controllers(
            clock_owner=clock, initial_root_states=incomplete,
            root_config=root_config, recovery_config=recovery_config,
        )
    foreign_first = replace(clock.bindings[0], b_ns=clock.bindings[0].b_ns + 1.0)
    foreign_clock = replace(clock, bindings=(foreign_first,) + clock.bindings[1:])
    with pytest.raises(ValueError, match="clock owner|terminal identity"):
        compose_action00_missing_tilt_controllers(
            clock_owner=foreign_clock, initial_root_states=seeds,
            root_config=root_config, recovery_config=recovery_config,
        )
    assert seed_bytes == {
        node: (row.vector.tobytes(), row.covariance.tobytes()) for node, row in seeds.items()
    }
