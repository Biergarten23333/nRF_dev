from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from biospur_fusion.c2_coupled_progressive.action00_missing_tilt_adapter import (
    _read_sealed,
    load_action00_missing_tilt_initialization,
)
from biospur_fusion.c2_coupled_progressive.action00_tilt_trust_policy import (
    load_engineering_action00_tilt_policy_result,
)
from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    CONTINUOUS_FRONTEND_SCHEMA,
    ContinuousClockOwner,
    NodeClockBinding,
)
from biospur_fusion.c2_uwb_root_world.authoritative_articulated_fusion import (
    Native200ClockMappingOwner,
)
from biospur_fusion.c2_uwb_root_world.gap_tilt_recovery import TiltEvidenceStatus
from biospur_fusion.c2_uwb_root_world.run_calibration import _clock_models


ROOT = Path(__file__).resolve().parents[1]
CLOCK_PATH = ROOT / "logs/c2_uwb_beacon_clock_20260903_141552/CLOCK_TABLE_CALIBRATION_ONLY.json"
RESULT_PATH = ROOT / (
    "logs/c2_action00_engineering_policy_v2_prereg_20260908T221958Z/run/RESULT.json"
)
RESULT_SHA256 = "ac08ae6d22d3be9ef6933450c07b6d7a13b65341735e535dcb95c1c564be61fc"
POLICY_DIGEST = "802a322874da1f4f0544f8b6931ad8b1dff4967523370866e6a868b07d464d12"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clock_owner():
    document = json.loads(CLOCK_PATH.read_text(encoding="utf-8"))
    owner_sha = _sha(CLOCK_PATH)
    bindings = []
    for node, model in sorted(_clock_models(CLOCK_PATH).items()):
        mapping = Native200ClockMappingOwner(
            node=node,
            clock_domain="B306_TIMER2",
            boot_epoch=model.boot_epoch,
            a_ns_per_us=model.a_ns_per_us,
            b_ns=model.b_ns,
            clock_owner_sha256=owner_sha,
        )
        bindings.append(NodeClockBinding(
            node,
            model.boot_epoch,
            "B306_TIMER2",
            mapping.digest,
            model.a_ns_per_us,
            model.b_ns,
            owner_sha,
            str(document["source_sha256"]),
        ))
    return ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, tuple(bindings))


def _tampered_result(tmp_path, mutate):
    document = json.loads(RESULT_PATH.read_text())
    mutate(document)
    result = tmp_path / "RESULT.json"
    result.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    result_sha = _sha(result)
    result.chmod(0o444)
    return result, result_sha


def test_exact_sealed_missing_result_materializes_ten_typed_node_owners():
    clock_owner = _clock_owner()
    initialized = load_action00_missing_tilt_initialization(clock_owner)
    assert initialized.policy.digest == POLICY_DIGEST
    assert initialized.policy.status == "MISSING"
    assert not initialized.product_ready and not initialized.scientific_pass
    assert len(initialized.nodes) == 10
    for row in initialized.nodes:
        node = row.evidence.source_node
        terminal = initialized.policy.terminal_identities[node]
        binding = clock_owner.binding_for(node)
        assert row.terminal is terminal
        assert row.issuer.clock_mapping_digest == binding.clock_mapping_digest
        assert row.issuer.clock_domain == binding.clock_domain
        assert row.issuer.source_owner_digest == initialized.policy.diagnostic_source_binding_digests[node]
        assert row.issuer.missing_policy_digest == initialized.policy.digest
        assert row.evidence.status is TiltEvidenceStatus.MISSING
        assert row.evidence.event_identity == terminal.event_identity
        assert row.evidence.source_sequence == terminal.source_sequence
        assert row.evidence.measurement_time_s == terminal.common_global_ns * 1e-9
        assert row.evidence.availability_time_s == terminal.availability_global_ns * 1e-9
        assert row.evidence.issuer_binding_digest == row.issuer.digest


def test_wrong_result_sha_fails_at_public_loader_without_side_effect():
    clock = _clock_owner()
    before = repr(clock)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_engineering_action00_tilt_policy_result(
            RESULT_PATH, expected_result_sha256="0" * 64, clock_owner=clock,
        )
    assert repr(clock) == before


def test_sealed_reader_rejects_wrong_sha(tmp_path):
    path = tmp_path / "sealed.txt"
    path.write_bytes(b"evidence\n")
    path.chmod(0o444)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        _read_sealed(path, "0" * 64)


def test_wrong_typed_clock_owner_fails_closed():
    clock = _clock_owner()
    foreign_first = replace(clock.bindings[0], b_ns=clock.bindings[0].b_ns + 1.0)
    foreign = replace(clock, bindings=(foreign_first,) + clock.bindings[1:])
    with pytest.raises(ValueError, match="clock owner|terminal identity"):
        load_action00_missing_tilt_initialization(foreign)


@pytest.mark.parametrize("mutation", [
    lambda document: document["policy"]["expected_nodes"].pop(),
    lambda document: document["policy"]["terminal_identities"]["BSF1120"].update(
        event_identity="tampered-terminal",
    ),
    lambda document: document["policy"]["diagnostic_source_binding_digests"].update(
        BSF1120="f" * 64,
    ),
])
def test_inventory_terminal_and_source_tampering_fail_closed(
    tmp_path, mutation,
):
    path, result_sha = _tampered_result(tmp_path, mutation)
    with pytest.raises(ValueError, match="sealed|inventory|invalid"):
        load_engineering_action00_tilt_policy_result(
            path, expected_result_sha256=result_sha, clock_owner=_clock_owner(),
        )


def test_rejection_has_no_mutable_owner_or_file_side_effect(tmp_path):
    path, result_sha = _tampered_result(
        tmp_path,
        lambda document: document["policy"]["terminal_identities"]["BSF31CC"].update(
            source_sequence=65536,
        ),
    )
    clock = _clock_owner()
    before_clock = repr(clock)
    before_result = _sha(path)
    with pytest.raises(ValueError):
        load_engineering_action00_tilt_policy_result(
            path, expected_result_sha256=result_sha, clock_owner=clock,
        )
    assert repr(clock) == before_clock
    assert _sha(path) == before_result
