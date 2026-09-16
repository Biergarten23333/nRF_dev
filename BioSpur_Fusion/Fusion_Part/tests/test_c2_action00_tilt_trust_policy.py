from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from biospur_fusion.c2_coupled_progressive.action00_tilt_trust_policy import (
    Action00TiltPolicyRegistry,
    Action00TiltPolicySchemaError,
    BLOCK_SAMPLE_COUNT,
    ENGINEERING_RESULT_SCHEMA,
    EngineeringAction00TiltPolicy,
    EngineeringAction00TiltPolicyIssuer,
    LegacyAction00TiltPolicySchemaError,
    REQUIRED_CONSECUTIVE_QUALIFYING_FRAMES,
    TARGET_BLOCK_FALSE_ALARM_ALPHA,
    _digest,
    exact_vqf_parameter_payload,
    load_engineering_action00_tilt_policy_result,
)
from biospur_fusion.c2_coupled_progressive.authenticated_vqf_tilt_join import (
    UnqualifiedVQFTiltClockEvidence,
)
from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    CONTINUOUS_FRONTEND_SCHEMA,
    ContinuousClockOwner,
    NodeClockBinding,
    continuous_clock_owner_digest,
)
from biospur_fusion.c2_uwb_root_world.gap_tilt_recovery import TiltEvidenceStatus
from biospur_fusion.v0.c2_progressive.orientation import ContinuousVQFState
from biospur_fusion.v0.c2_progressive.pipeline_runtime import (
    _runtime_vqf_tilt_authority,
    _validated_settings_initial_state_binding,
)
from biospur_fusion.v0.c2_progressive.range_reader import DecodedAction, IMU_DTYPE
from tools.run_c2_action00_engineering_tilt_policy import _write_new


class _Guard:
    capture_id = "C2"
    def bind_vqf_instance(self, node, instance): pass
    def begin_episode(self, index, action): pass


def _initial():
    row = {
        "gyro_bias_rad_s": [0.0, 0.0, 0.0],
        "gyro_bias_covariance_rad2_s2": (np.eye(3) * 1e-8).tolist(),
        "gyro_observation_covariance_rad2_s2": (np.eye(3) * 1e-7).tolist(),
        "accelerometer_norm_mps2": 9.80665,
        "accelerometer_observation_covariance_m2_s4": (np.eye(3) * 1e-4).tolist(),
    }
    return {"nodes": {f"node{index}": dict(row) for index in range(10)}}


def _provenance(*, source_owned=True):
    initial = _initial()
    authority, capability = _runtime_vqf_tilt_authority(
        seal_authority={
            "seal_sha256": "1" * 64,
            "qualified_source_hashes": ({"initial.json": "2" * 64} if source_owned else {}),
        },
        settings={"execution_contract": {"initial_stochastic_state_relative_path": "initial.json"}},
        initial_semantic_sha256=_digest(initial),
        settings_semantic_sha256="3" * 64,
    )
    state = ContinuousVQFState(
        initial, execution_guard=_Guard(), sample_period_s=.005,
        unknown_boot_orientation_sigma_rad=1.0,
        unknown_unusable_episode_orientation_sigma_rad=.5,
        tilt_diagnostic_runtime_authority=authority,
        _tilt_provenance_capability=capability,
    )
    by_node = {}
    for node_index in range(10):
        rows = np.zeros(2, dtype=IMU_DTYPE)
        rows["derived_boot_epoch"] = 1
        rows["imu_sample_sequence"] = [0, 1]
        rows["node_timer_us"] = [1_000_000, 1_005_000]
        rows["acc_raw"][:, 2] = 2048
        rows["raw_start_offset"] = [10 + node_index * 100, 42 + node_index * 100]
        rows["raw_end_offset"] = [42 + node_index * 100, 74 + node_index * 100]
        rows["raw_sample_index"] = [0, 1]
        rows["decode_acceptance_status"] = 1
        by_node[f"node{node_index}"] = rows
    oriented = state.process(DecodedAction(
        "00_initial_still", 0, (10, 1074), by_node,
        {"slice_sha256": "4" * 64}, {"decoder": "synthetic"},
    ))
    return oriented.vqf_tilt_diagnostic_provenance


def _row(index: int, provenance, *, action="00_initial_still", protocol_index=0,
         chronological_index=0, span=0, timer_shift=0, rest=True,
         source_binding="7" * 64, node="node0", world_tilt=None):
    timer = 2_000_000 + index * 5_000 + timer_shift
    return UnqualifiedVQFTiltClockEvidence(
        event_identity=f"event-{node}-{index}-{action}-{timer}", action_id=action,
        protocol_action_index=protocol_index,
        acquired_chronological_index=chronological_index,
        node=node, boot_epoch=1, span_id=span, source_sequence=index % 65536,
        timer2_us=timer, common_global_ns=timer * 1_000,
        availability_global_ns=timer * 1_000 + 1_000_000,
        raw_start_offset=100 + index * 32, raw_end_offset=132 + index * 32,
        raw_sample_index=index % 20, clock_domain="B306_TIMER2",
        clock_mapping_digest="5" * 64, clock_owner_digest=CLOCK_OWNER_DIGEST,
        diagnostic_provenance_digest=provenance.digest,
        diagnostic_source_binding_digest=source_binding,
        rest_detected=rest, bias_sigma_rad_s=.0002,
        relative_rest_deviation_gyro=.5,
        relative_rest_deviation_acceleration=.5,
        acceleration_norm_residual_mps2=.02,
        world_tilt_innovation_rad=(
            .003 + (index // BLOCK_SAMPLE_COUNT) * .0001
            if world_tilt is None else world_tilt
        ),
    )


def _registry(provenance):
    return Action00TiltPolicyRegistry(provenance=provenance, initial_state=_initial())


def _clock_owner(*, b_ns=0.0):
    return ContinuousClockOwner(
        CONTINUOUS_FRONTEND_SCHEMA,
        tuple(
            NodeClockBinding(
                f"node{index}", 1, "B306_TIMER2", "5" * 64,
                1000.0, b_ns, "a" * 64, "b" * 64,
            )
            for index in range(10)
        ),
    )


CLOCK_OWNER_DIGEST = continuous_clock_owner_digest(_clock_owner())


def _calibration_rows(provenance, blocks=19):
    return [
        _row(index, provenance, node=f"node{node_index}",
             source_binding=f"{node_index + 10:064x}")
        for node_index in range(10)
        for index in range(blocks * BLOCK_SAMPLE_COUNT)
    ]


def _jsonable(value):
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _sealed_result(tmp_path, policy, *, name="result.json", mutate=None, schema=ENGINEERING_RESULT_SCHEMA):
    document = {
        "schema": schema,
        "status": policy.status,
        "product_ready": False,
        "scientific_pass": False,
        "policy": _jsonable(policy),
    }
    if mutate is not None:
        mutate(document)
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    path = tmp_path / name
    path.write_bytes(payload)
    path.chmod(0o444)
    return path, hashlib.sha256(payload).hexdigest()


def _one_block_missing_policy(provenance):
    registry = _registry(provenance)
    registry.ingest(_calibration_rows(provenance, blocks=1))
    return registry.finalize()


def test_joint_conformal_formula_and_attainable_alpha_are_preregistered():
    provenance = _provenance()
    registry = _registry(provenance)
    rows = _calibration_rows(provenance)
    registry.ingest(rows)
    policy = registry.finalize()
    node = policy.node_policies["node0"]
    assert node.status == "ENGINEERING_DIAGNOSTIC"
    assert node.eligible_block_count == 19 and node.conformal_rank == 19
    assert node.attainable_false_alarm_alpha == pytest.approx(TARGET_BLOCK_FALSE_ALARM_ALPHA)
    scores = [registry._frame_score(row, node.scales) for row in rows if row.node == "node0"]
    assert node.nonconformity_threshold == max(scores)
    assert policy.product_ready is False and policy.scientific_pass is False
    assert registry.preregistration.vqf_parameters_digest == _digest(exact_vqf_parameter_payload())


def test_insufficient_blocks_returns_missing_without_threshold():
    provenance = _provenance()
    registry = _registry(provenance)
    registry.ingest(_calibration_rows(provenance, blocks=18))
    policy = registry.finalize()
    node = policy.node_policies["node0"]
    assert policy.status == "MISSING" and node.status == "MISSING"
    assert node.nonconformity_threshold is None
    assert node.conformal_rank > node.eligible_block_count


def test_partial_node_inventory_can_never_issue_complete_policy():
    provenance = _provenance()
    registry = _registry(provenance)
    registry.ingest([
        _row(index, provenance, node="node0")
        for index in range(19 * BLOCK_SAMPLE_COUNT)
    ])
    policy = registry.finalize()
    assert policy.status == "MISSING"
    assert policy.expected_nodes == tuple(f"node{index}" for index in range(10))
    with pytest.raises(ValueError, match="complete calibrated policy"):
        EngineeringAction00TiltPolicyIssuer(policy)


def test_blocks_restart_at_gap_and_count_ineligible_and_every_tail():
    provenance = _provenance()
    rows = []
    for index in range(21 * BLOCK_SAMPLE_COUNT):
        shift = 5_000 if index >= 250 else 0
        span = 1 if index >= 250 else 0
        row = _row(index, provenance, span=span, timer_shift=shift, node="node0")
        if index == 400:
            row = replace(row, rest_detected=False, digest="")
        rows.append(row)
    registry = _registry(provenance)
    registry.ingest(rows + [row for row in _calibration_rows(provenance) if row.node != "node0"])
    node = registry.finalize().node_policies["node0"]
    assert node.eligible_block_count == 19
    assert node.ineligible_block_count == 1
    assert node.incomplete_frame_count == 200
    assert node.status == "ENGINEERING_DIAGNOSTIC"


def test_batch_and_stream_collection_have_identical_inventory_policy():
    provenance = _provenance()
    rows = _calibration_rows(provenance)
    batch = _registry(provenance); batch.ingest(rows)
    stream = _registry(provenance)
    stream.ingest(rows[:713]); stream.ingest(rows[713:2401]); stream.ingest(rows[2401:])
    assert batch.owner_bytes() == stream.owner_bytes()
    assert batch.finalize().digest == stream.finalize().digest


def test_foreign_later_action_and_provenance_mutation_reject_atomically():
    provenance = _provenance()
    registry = _registry(provenance)
    for foreign in (
        _row(0, provenance, action="04_shoulder_left", protocol_index=4, chronological_index=3),
        replace(_row(0, provenance), diagnostic_provenance_digest="8" * 64, digest=""),
    ):
        before = registry.owner_bytes()
        with pytest.raises(ValueError, match="Action00"):
            registry.ingest([foreign])
        assert registry.owner_bytes() == before


def test_missing_initial_file_owner_and_direct_policy_construction_fail_closed():
    with pytest.raises(ValueError, match="source-owned"):
        _registry(_provenance(source_owned=False))
    with pytest.raises((TypeError, ValueError)):
        EngineeringAction00TiltPolicy(
            "1" * 64, "2" * 64, "3" * 64, {}, {}, "4" * 64, {}, "MISSING",
        )


def test_first_post_action00_gap_untrusts_before_exact_200_good_frames_recover():
    provenance = _provenance()
    registry = _registry(provenance)
    registry.ingest(_calibration_rows(provenance))
    issuer = EngineeringAction00TiltPolicyIssuer(registry.finalize())
    start = 50_000
    invalid = _row(start, provenance, action="04_shoulder_left", protocol_index=4,
                   chronological_index=3, rest=True, source_binding="8" * 64,
                   node="node0", world_tilt=.003)
    first = issuer.classify(invalid)
    assert first.status is TiltEvidenceStatus.UNTRUSTED
    assert first.reason == "SOURCE_DISCONTINUITY_ENTER_UNTRUSTED"
    for offset in range(1, REQUIRED_CONSECUTIVE_QUALIFYING_FRAMES):
        decision = issuer.classify(_row(
            start + offset, provenance, action="04_shoulder_left", protocol_index=4,
            chronological_index=3, source_binding="8" * 64, node="node0",
            world_tilt=.003,
        ))
        assert decision.status is TiltEvidenceStatus.UNTRUSTED
    recovered = issuer.classify(_row(
        start + REQUIRED_CONSECUTIVE_QUALIFYING_FRAMES, provenance,
        action="04_shoulder_left", protocol_index=4, chronological_index=3,
        source_binding="8" * 64, node="node0", world_tilt=.003,
    ))
    assert recovered.status is TiltEvidenceStatus.TRUSTED
    assert recovered.consecutive_qualifying_frames == 200


def test_settings_path_binding_is_exact_regular_file_and_rejects_symlink(tmp_path: Path):
    initial = _initial()
    state_path = tmp_path / "initial.json"
    state_path.write_text(json.dumps(initial, sort_keys=True), encoding="utf-8")
    settings = {"execution_contract": {
        "canonical_workspace": str(tmp_path),
        "initial_stochastic_state_relative_path": "initial.json",
        "initial_stochastic_state_semantic_sha256": _digest(initial),
    }}
    binding = _validated_settings_initial_state_binding(settings, initial, _digest(initial))
    assert binding["sha256"] == hashlib.sha256(state_path.read_bytes()).hexdigest()
    assert binding["relative_path"] == "initial.json"
    state_path.rename(tmp_path / "owned.json")
    state_path.symlink_to(tmp_path / "owned.json")
    with pytest.raises(ValueError, match="symlink"):
        _validated_settings_initial_state_binding(settings, initial, _digest(initial))


def test_policy_result_round_trip_binds_terminal_sequence_and_clock_owner(tmp_path):
    assert CLOCK_OWNER_DIGEST == "9782c00621e95d3e9c91a241c55de1c153fba6b7a5ee34f06e3ef26232160baa"
    policy = _one_block_missing_policy(_provenance())
    path = tmp_path / "runner-output.json"
    _write_new(path, {
        "schema": ENGINEERING_RESULT_SCHEMA,
        "status": policy.status,
        "product_ready": policy.product_ready,
        "scientific_pass": policy.scientific_pass,
        "policy": policy,
    })
    result_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    loaded = load_engineering_action00_tilt_policy_result(
        path, expected_result_sha256=result_sha, clock_owner=_clock_owner(),
    )
    assert loaded.digest == policy.digest
    assert all(
        terminal.source_sequence == BLOCK_SAMPLE_COUNT - 1
        for terminal in loaded.terminal_identities.values()
    )


@pytest.mark.parametrize("invalid_sequence", [-1, 65536])
def test_policy_loader_rejects_invalid_terminal_uint16_sequence(tmp_path, invalid_sequence):
    policy = _one_block_missing_policy(_provenance())
    def mutate(document):
        document["policy"]["terminal_identities"]["node0"]["source_sequence"] = invalid_sequence
    path, result_sha = _sealed_result(
        tmp_path, policy, name=f"invalid-{invalid_sequence}.json", mutate=mutate,
    )
    with pytest.raises(Action00TiltPolicySchemaError, match="invalid sealed"):
        load_engineering_action00_tilt_policy_result(
            path, expected_result_sha256=result_sha, clock_owner=_clock_owner(),
        )


def test_policy_loader_rejects_missing_sequence_and_legacy_result(tmp_path):
    policy = _one_block_missing_policy(_provenance())
    def remove_sequence(document):
        document["policy"]["terminal_identities"]["node0"].pop("source_sequence")
    missing_path, missing_sha = _sealed_result(
        tmp_path, policy, name="missing-sequence.json", mutate=remove_sequence,
    )
    with pytest.raises(LegacyAction00TiltPolicySchemaError, match="lacks uint16"):
        load_engineering_action00_tilt_policy_result(
            missing_path, expected_result_sha256=missing_sha, clock_owner=_clock_owner(),
        )
    legacy_path, legacy_sha = _sealed_result(
        tmp_path, policy, name="legacy.json",
        schema="biospur.c2.action00_engineering_tilt_policy.real_result.v1",
    )
    with pytest.raises(LegacyAction00TiltPolicySchemaError, match="lacks terminal uint16"):
        load_engineering_action00_tilt_policy_result(
            legacy_path, expected_result_sha256=legacy_sha, clock_owner=_clock_owner(),
        )


def test_policy_loader_rejects_terminal_source_and_result_tampering(tmp_path):
    policy = _one_block_missing_policy(_provenance())
    mutations = (
        lambda document: document["policy"]["terminal_identities"]["node0"].update(
            event_identity="tampered-event",
        ),
        lambda document: document["policy"]["diagnostic_source_binding_digests"].update(
            node0="f" * 64,
        ),
    )
    for index, mutation in enumerate(mutations):
        path, result_sha = _sealed_result(
            tmp_path, policy, name=f"tampered-{index}.json", mutate=mutation,
        )
        with pytest.raises(Action00TiltPolicySchemaError, match="invalid sealed"):
            load_engineering_action00_tilt_policy_result(
                path, expected_result_sha256=result_sha, clock_owner=_clock_owner(),
            )
    path, result_sha = _sealed_result(tmp_path, policy, name="sha-mismatch.json")
    with pytest.raises(Action00TiltPolicySchemaError, match="SHA-256 mismatch"):
        load_engineering_action00_tilt_policy_result(
            path, expected_result_sha256="0" * 64, clock_owner=_clock_owner(),
        )


def test_policy_loader_rejects_foreign_typed_clock_owner(tmp_path):
    policy = _one_block_missing_policy(_provenance())
    path, result_sha = _sealed_result(tmp_path, policy)
    with pytest.raises(Action00TiltPolicySchemaError, match="clock owner"):
        load_engineering_action00_tilt_policy_result(
            path, expected_result_sha256=result_sha, clock_owner=_clock_owner(b_ns=1.0),
        )


def test_policy_loader_never_follows_a_result_symlink(tmp_path):
    policy = _one_block_missing_policy(_provenance())
    target, result_sha = _sealed_result(tmp_path, policy, name="target.json")
    link = tmp_path / "result-link.json"
    link.symlink_to(target)
    with pytest.raises(Action00TiltPolicySchemaError, match="opened safely"):
        load_engineering_action00_tilt_policy_result(
            link, expected_result_sha256=result_sha, clock_owner=_clock_owner(),
        )
