from __future__ import annotations

import json
from pathlib import Path

import pytest

from biospur_fusion.calibration_v2.phase2r.governance import (
    AccessClass,
    DataAccessBroker,
    DataAccessViolation,
    Rule,
)


def fixture(tmp_path: Path):
    root = tmp_path / "dataset"
    (root / "identity").mkdir(parents=True)
    (root / "holdout/H00/rep_01/raw").mkdir(parents=True)
    (root / "checksums").mkdir()
    (root / "DATA_ACCESS_POLICY.json").write_text(json.dumps({
        "schema": "biospur-phase2r-data-access-v1",
        "phase2_solver_forbidden": ["SEALED_NODE_TO_BODY_GROUND_TRUTH.json", "holdout_numeric_content"],
    }))
    for relative in DataAccessBroker.BOOTSTRAP_METADATA:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("{}" if path.suffix == ".json" else "metadata")
    (root / "identity/SEALED_NODE_TO_BODY_GROUND_TRUTH.json").write_text('{"secret":true}')
    (root / "holdout/H00/rep_01/raw/fusion_host_raw.cobs.bin").write_bytes(b"secret")
    return root, tmp_path / "ledger.jsonl"


def test_policy_is_bootstrap_and_allowed_metadata_reads(tmp_path):
    root, ledger = fixture(tmp_path)
    broker = DataAccessBroker.bootstrap(root, ledger, "pytest")
    assert broker.read_json(root / "CAPTURE_PLAN_FINAL.json", purpose="plan") == {}
    rows = [json.loads(x) for x in ledger.read_text().splitlines()]
    assert rows[0]["access_class"] == AccessClass.POLICY
    assert all(row["observed_sha256"] for row in rows)


@pytest.mark.parametrize("target", [
    "identity/SEALED_NODE_TO_BODY_GROUND_TRUTH.json",
    "holdout/H00/rep_01/raw/fusion_host_raw.cobs.bin",
])
def test_sealed_paths_denied_before_open(tmp_path, target):
    root, ledger = fixture(tmp_path)
    broker = DataAccessBroker.bootstrap(root, ledger, "pytest")
    with pytest.raises(DataAccessViolation):
        broker.read_bytes(root / target, purpose="mutation")
    row = json.loads(ledger.read_text().splitlines()[-1])
    assert row["allowed"] is False
    assert row["observed_sha256"] is None
    assert row["payload_bytes_read"] == 0


def test_symlink_and_lexical_alias_denied(tmp_path):
    root, ledger = fixture(tmp_path)
    broker = DataAccessBroker.bootstrap(root, ledger, "pytest")
    link = tmp_path / "plan-link"
    link.symlink_to(root / "CAPTURE_PLAN_FINAL.json")
    with pytest.raises(DataAccessViolation):
        broker.read_bytes(link, purpose="symlink mutation")
    with pytest.raises(DataAccessViolation):
        broker.read_bytes(root / "subject/../CAPTURE_PLAN_FINAL.json", purpose="dotdot mutation")


def test_numeric_decode_on_metadata_denied(tmp_path):
    root, ledger = fixture(tmp_path)
    broker = DataAccessBroker.bootstrap(root, ledger, "pytest")
    with pytest.raises(DataAccessViolation):
        broker.read_bytes(root / "CAPTURE_PLAN_FINAL.json", purpose="numeric mutation", numeric_measurements=1)


def test_rule_cannot_enable_holdout_numeric(tmp_path):
    root, ledger = fixture(tmp_path)
    broker = DataAccessBroker.bootstrap(root, ledger, "pytest")
    target = (root / "holdout/H00/rep_01/raw/fusion_host_raw.cobs.bin").resolve()
    with pytest.raises(DataAccessViolation):
        broker.add_exact_rules([Rule(target, AccessClass.PRETRUTH_MEASUREMENT, ("imu",), True)])


def test_hash_pinned_rule_rejects_changed_bytes(tmp_path):
    root, ledger = fixture(tmp_path)
    broker = DataAccessBroker.bootstrap(root, ledger, "pytest")
    target = root / "prior.json"
    target.write_text("original")
    broker.add_exact_rules([Rule(target.resolve(), AccessClass.PRETRUTH_NON_ROLE_PRIOR, ("prior",), False, "0" * 64)])
    with pytest.raises(DataAccessViolation, match="hash mismatch"):
        broker.read_bytes(target.resolve(), purpose="identity mutation")


def test_reveal_requires_validated_freeze_and_exact_commitment(tmp_path):
    root, ledger = fixture(tmp_path)
    truth = root / "identity/SEALED_NODE_TO_BODY_GROUND_TRUTH.json"
    import hashlib
    commitment = hashlib.sha256(truth.read_bytes()).hexdigest()
    policy = json.loads((root / "DATA_ACCESS_POLICY.json").read_text())
    policy["ground_truth_commitment_sha256"] = commitment
    (root / "DATA_ACCESS_POLICY.json").write_text(json.dumps(policy))
    broker = DataAccessBroker.bootstrap(root, ledger, "reveal-test")
    with pytest.raises(DataAccessViolation):
        broker.enable_sealed_mapping_reveal(truth, commitment, freeze_validated=False)
    broker.enable_sealed_mapping_reveal(truth, commitment, freeze_validated=True)
    assert broker.read_json(truth, purpose="sealed validation") == {"secret": True}
