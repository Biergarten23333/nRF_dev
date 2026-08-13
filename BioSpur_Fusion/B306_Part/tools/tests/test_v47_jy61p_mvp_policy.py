import json
from pathlib import Path

import numpy as np
import pytest

import derive_v47_jy61p_mvp_policy as policy


def test_no_cross_device_bias_copying():
    assert np.array_equal(policy.policy_accel_bias("IDENTITY_ACCEL_MATRIX"), np.zeros(3))
    assert np.array_equal(policy.policy_accel_bias("SHARED_MATRIX"), np.zeros(3))
    assert "device" not in policy.policy_accel_bias.__code__.co_varnames


def test_pooled_fit_uses_training_argument_only_and_marks_heldout_excluded():
    rng = np.random.default_rng(47)
    directions = rng.normal(size=(18, 3)); directions /= np.linalg.norm(directions, axis=1)[:, None]
    training = {"A": [row[None, :] for row in directions],
                "B": [(row * 1.003)[None, :] for row in directions]}
    result = policy.fit_pooled_matrix(training, "DIAGONAL")
    assert result["fit_source"] == "TRAINING_POSE_MEANS_ONLY"
    assert result["heldout_used_for_fit"] is False


def test_full_spd_complexity_rejected_when_identity_or_diagonal_adequate():
    summary = {
        "IDENTITY": {"BSFC2CC": .003, "BSF31CC": .003},
        "POOLED_DIAGONAL": {"BSFC2CC": .0028, "BSF31CC": .0028},
        "POOLED_FULL_SPD": {"BSFC2CC": .0027, "BSF31CC": .0027},
    }
    verdict, diagnostic = policy.select_policy(summary)
    assert verdict == "INSUFFICIENT_COHORT_EVIDENCE_USE_IDENTITY_MVP"
    assert diagnostic["full_spd_practical_gain_over_diagonal_both_devices"] is False


def test_per_device_regression_protection_blocks_shared_promotion():
    summary = {
        "IDENTITY": {"BSFC2CC": .004, "BSF31CC": .004},
        "POOLED_DIAGONAL": {"BSFC2CC": .002, "BSF31CC": .0046},
        "POOLED_FULL_SPD": {"BSFC2CC": .001, "BSF31CC": .0047},
    }
    _, diagnostic = policy.select_policy(summary)
    assert diagnostic["diagonal_transferable_under_frozen_rules"] is False
    assert diagnostic["full_spd_transferable_under_frozen_rules"] is False


def test_oracle_and_product_realistic_are_distinct_in_formal_output():
    out = policy.LOGS / "v47_jy61p_mvp_policy_test_fixture_missing"
    # This is a source-structure assertion; no derivation or evidence write.
    source = Path(policy.__file__).read_text()
    assert "PER_DEVICE_ORACLE_REFERENCE" in source
    assert "ZERO_ACCEL_BIAS" in source
    assert "per_device_accel_bias_used" in source
    assert not out.exists()


def test_selection_is_deterministic():
    summary = {
        "IDENTITY": {"BSFC2CC": .004, "BSF31CC": .005},
        "POOLED_DIAGONAL": {"BSFC2CC": .002, "BSF31CC": .003},
        "POOLED_FULL_SPD": {"BSFC2CC": .0019, "BSF31CC": .0029},
    }
    assert policy.select_policy(summary) == policy.select_policy(json.loads(json.dumps(summary)))


def test_q1_output_order_is_explicit_and_deterministic():
    rows = [
        {"evidence": "B", "node": "N1", "policy": "P2"},
        {"evidence": "A", "node": "N2", "policy": "P1"},
        {"evidence": "A", "node": "N1", "policy": "P2"},
    ]
    assert sorted(rows, key=policy.q1_sort_key) == [rows[2], rows[1], rows[0]]


def test_isolated_transient_is_retained_as_sensitivity_candidate():
    samples = []
    for index in range(60):
        accel = [0.0, 0.0, 1.0]
        if index == 30:
            accel = [0.0, .12, 1.0]
        samples.append({"accel_g": accel, "gyro_dps": [0.0, 0.0, 0.0]})
    mask = policy.isolated_transient_mask(samples)
    assert int(mask.sum()) == 1 and bool(mask[30])
    assert len(samples) == 60


def test_authoritative_hash_mismatch_fails_closed(tmp_path):
    evidence = tmp_path / "evidence.bin"
    evidence.write_bytes(b"authoritative")
    with pytest.raises(RuntimeError, match="hash mismatch"):
        policy.verify_authoritative_evidence({evidence: "0" * 64})


def test_deriver_has_no_hardware_access_path():
    source = Path(policy.__file__).read_text()
    for forbidden in ("serial.Serial(", ".send(", "JLinkExe", "nrfjprog", "subprocess.run("):
        assert forbidden not in source
