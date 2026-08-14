import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2"


def load(name):
    return json.loads((OUT / name).read_text())


def test_gate0_and_boot_segmentation_are_closed():
    result = load("TIME_ALIGNMENT_RESULT.json")
    assert result["gate_0_pass"] is True
    assert max(row["clean_residual_p95_us"] for row in result["clock_models"].values()) < 500
    assert max(row["clean_residual_max_us"] for row in result["clock_models"].values()) < 1000
    assert result["clock_models"]["BSF31CC"]["boot_epoch"] == 1
    assert result["clock_models"]["BSFC2CC"]["boot_epoch"] == 1
    assert all(row["corroborated"] for row in result["gates"]["boot_segment_audit"].values())


def test_exact_accounting_and_fail_closed_boundary():
    accounting = load("EVENT_ACCOUNTING.json")
    assert accounting["exact"] is True
    assert accounting["measurement_accounting"]["accepted"] == 2_899_999
    assert accounting["measurement_accounting"]["outside-clock-segment"] == 266_197
    assert load("FRAME_BINDING_RESULT.json")["qualified"] is False
    heldout = load("HELDOUT_VALIDATION.json")
    assert heldout["status"] == "NOT_OPENED" and not heldout["walk_opened"] and not heldout["final_still_opened"]
    numerical = load("NUMERICAL_INTEGRITY.json")
    assert numerical["q1_all_finite"] and numerical["q1_cholesky_failures"] == 0
    assert numerical["joint_estimator_ran"] is False


def test_deterministic_replay_and_provenance():
    replay = load("DETERMINISTIC_REPLAY.json")
    assert replay["pass"] is True
    provenance = load("CAPTURE_PROVENANCE.json")
    assert provenance["raw_sha256_before"] == provenance["raw_sha256_after"]
    assert provenance["layout_sha256"] == "20320e53d48b171c016a0e8d1d93b3cb10e979cf4c21c15c21647d5c0b9878b1"
    assert provenance["solver"] == "UWB_TAG_T4" and provenance["hardware_accessed"] is False
