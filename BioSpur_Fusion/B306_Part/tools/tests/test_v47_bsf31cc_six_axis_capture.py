import math
import csv
import json
from pathlib import Path

import numpy as np
import pytest

import v47_bsf31cc_six_axis_capture as capture

ROOT = Path(__file__).resolve().parents[3]
QUALIFICATION = ROOT / "B306_Part/logs/v47_bsf31cc_six_axis_calibration_20260812_230640/qualification_v1"


def test_exact_sequential_operator_tokens():
    assert capture.TRAINING_TOKENS == [f"POSE_{i:02d}_READY" for i in range(1, 19)]
    assert capture.VALIDATION_TOKENS == [f"VALIDATION_{i:02d}_READY" for i in range(1, 5)]
    assert len(set(capture.TRAINING_TOKENS + capture.VALIDATION_TOKENS)) == 22


def test_capture_is_bsf31cc_specific_and_bmd101_excluded():
    assert capture.NODE == "BSF31CC"
    source = Path(capture.__file__).read_text()
    assert '"bmd101_scope": "EXCLUDED"' in source
    assert "BMD101" not in " ".join(capture.TRAINING + capture.VALIDATION).upper()


def test_capture_has_no_mutating_or_debug_command_literals():
    source = Path(capture.__file__).read_text()
    commands = ('"MASTER STATUS", "LIST", f"{NODE} PING", f"{NODE} BOOT CONFIRM STATUS"')
    assert commands in source
    for forbidden in ("PREPARE", "COMMIT", "REBOOT", "JLink", "nrfjprog", "AutoPos"):
        assert forbidden not in source


def test_training_fit_precedes_heldout_loop_and_uses_training_only():
    source = Path(capture.__file__).read_text()
    fit = source.index("selection = fit_and_select")
    freeze = source.index('atomic(root / "FROZEN_TRAINING_MODEL.json"')
    heldout = source.index("for pose, (instruction, token) in enumerate(zip(VALIDATION")
    assert fit < freeze < heldout
    assert 'for segment in training_sets' in source
    assert "validation_sets" not in source[fit:freeze]


def sample(index, accel=(0.0, 0.0, 1.0), gyro=(0.0, 0.0, 0.0)):
    return {"accel_g": list(accel), "gyro_dps": list(gyro),
            "host_monotonic": index / 200, "seq": index, "node_us": index * 5000}


def test_robust_stationarity_retains_isolated_spike_as_nonblocking_decision_diagnostic():
    samples = [sample(i) for i in range(200)]
    samples[100] = sample(100, accel=(0.0, 0.0, 1.200))
    result = capture.robust_stationarity_metrics(samples)
    assert result["stable"]
    assert result["raw_transient_candidate_count"] == 1
    assert result["maximum_consecutive_raw_transient_samples"] == 1
    assert result["raw_accel_norm_std_g"] > capture.PREREGISTERED["accel_norm_std_max_g"]
    assert result["accel_norm_robust_sigma_g"] == pytest.approx(0.0)


def test_robust_stationarity_rejects_consecutive_burst_and_real_rotation():
    burst = [sample(i) for i in range(200)]
    burst[100] = sample(100, accel=(0.0, 0.0, 1.125))
    burst[101] = sample(101, accel=(0.0, 0.0, 1.125))
    assert not capture.robust_stationarity_metrics(burst)["stable"]
    moving = [sample(i, accel=(math.sin(i / 40), 0.0, math.cos(i / 40)), gyro=(0.0, 2.0, 0.0))
              for i in range(200)]
    assert not capture.robust_stationarity_metrics(moving)["stable"]


def test_formal_bsf31cc_artifact_is_device_specific_and_not_deployable():
    result = json.loads((QUALIFICATION / "CALIBRATION_RESULT.json").read_text())
    device = json.loads((QUALIFICATION / "BSF31CC_DEVICE_CALIBRATION.json").read_text())
    assert result["primary_verdict"] == "BSF31CC_DEVICE_CALIBRATION_VALIDATED"
    assert result["deployment_ready"] is False and result["bmd101_excluded"] is True
    assert device["node"] == "BSF31CC" and device["not_bsf_c2cc_numerical_profile"] is True
    assert device["bmd101_scope"] == "EXCLUDED" and device["transfer_to_other_devices"] is False


def test_formal_gate_a_and_strict_heldout_accounting():
    gate = json.loads((QUALIFICATION / "SYSTEMATIC_CALIBRATION_GATE.json").read_text())
    heldout = json.loads((QUALIFICATION / "HELDOUT_VALIDATION.json").read_text())
    with (QUALIFICATION / "POSE_WINDOWS.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert gate["pass"] and all(gate["checks"].values())
    assert all(gate["coverage_and_numerical_checks"].values())
    assert heldout["parameter_changes_after_freeze"] == 0 and heldout["heldout_samples"] == 16030
    assert sum(row["accepted"] == "True" for row in rows) == 22
    assert sum(row["accepted"] == "False" for row in rows) == 3


def test_formal_transients_are_retained_isolated_and_causally_contained():
    diagnostic = json.loads((QUALIFICATION / "RAW_TRANSIENT_DIAGNOSTIC.json").read_text())
    containment = json.loads((QUALIFICATION / "Q1_RUNTIME_CONTAINMENT.json").read_text())
    with (QUALIFICATION / "RAW_TRANSIENT_EVENTS.csv").open(newline="") as stream:
        events = list(csv.DictReader(stream))
    assert diagnostic["result"] == "OBSERVED_NON_BLOCKING" and diagnostic["event_count"] == 10
    assert diagnostic["maximum_consecutive_anomalous_samples"] == 1
    assert len(events) == 10 and all(row["q1_accepted"] == "False" for row in events)
    assert containment["result"] == "PASS" and all(row["pass"] for row in containment["event_audit"])


def test_deriver_has_no_heldout_refit_or_firmware_write_path():
    source = (ROOT / "B306_Part/tools/derive_v47_bsf31cc_six_axis.py").read_text()
    fit_call = source.index("replay_fit = fit_and_select")
    heldout_gate = source.index("systematic, per_pose = systematic_gate")
    assert "training" in source[fit_call:heldout_gate]
    assert "fit_and_select" not in source[heldout_gate:]
    # Report prose names prohibited hardware operations for auditability; reject
    # executable I/O paths instead of matching those words inside the report.
    for forbidden in ("serial.Serial(", ".send(", "subprocess.run(", "subprocess.check_call("):
        assert forbidden not in source
