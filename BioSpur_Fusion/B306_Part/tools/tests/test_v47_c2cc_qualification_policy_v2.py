import copy
import hashlib
import json
import math
from pathlib import Path

import pytest

from derive_v47_c2cc_qualification_policy_v2 import (
    EXPECTED_SHA256, FORMAL, HISTORICAL, PROFILE, derive, load_csv, load_json, sha256,
)
from v47_c2cc_qualification_policy_v2 import (
    CONDITIONAL_V1, POLICY_NAME, aggregate_v2, legacy_policy_v1_verdict,
    raw_transient_diagnostic, runtime_outlier_containment,
)


def formal_inputs():
    systematic = load_json(FORMAL / "SYSTEMATIC_CALIBRATION_GATE.json")
    capture = load_json(FORMAL / "CAPTURE_INTEGRITY.json")
    transient = load_json(FORMAL / "SENSOR_TRANSIENT_GATE.json")
    numerical = load_json(FORMAL / "NUMERICAL_INTEGRITY.json")
    events = load_csv(FORMAL / "TRANSIENTS_FOUND.csv")
    for event in events:
        event.update(dominant_channel="a1", gyro_co_motion=False,
                     handling_consistent=False, transport_or_time_anomaly=False)
    historical = load_json(HISTORICAL / "TRANSIENT_DISPOSITION.json")
    diagnostic = raw_transient_diagnostic(transient, events, historical["disposition"], capture)
    q1 = load_csv(FORMAL / "Q1_GRAVITY_UPDATE_AUDIT.csv")
    return systematic, capture, transient, numerical, diagnostic, q1


def test_old_policy_reproduces_recorded_conditional():
    systematic, capture, transient, numerical, _, _ = formal_inputs()
    assert legacy_policy_v1_verdict(systematic, capture, transient,
                                    numerical["runtime_q1_pass"]) == CONDITIONAL_V1
    assert load_json(FORMAL / "CALIBRATION_PROMOTION.json")["primary_verdict"] == CONDITIONAL_V1


def test_v2_preserves_exact_transient_statistics_and_ci():
    _, _, transient, _, diagnostic, _ = formal_inputs()
    assert diagnostic["raw_transient_candidates"] == 2
    assert diagnostic["accepted_stationary_samples"] == 38530
    assert diagnostic["empirical_rate_per_sample"] == transient["rate_per_sample"]
    assert diagnostic["exact_clopper_pearson_95_interval"] == transient["exact_clopper_pearson_95_interval"]
    assert diagnostic["exact_clopper_pearson_95_interval"] == [
        0.0000062863135328378424, 0.00018749540224892103]


def test_v2_raw_transients_are_explicitly_non_blocking():
    systematic, capture, _, numerical, diagnostic, q1 = formal_inputs()
    containment = runtime_outlier_containment(capture, diagnostic, q1, numerical)
    final, _ = aggregate_v2(systematic, capture, containment)
    assert diagnostic["result"] == "OBSERVED_NON_BLOCKING"
    assert diagnostic["blocking"] is False
    assert final["raw_transient_diagnostic_is_non_blocking"] is True
    assert final["primary_verdict"] == "C2CC_DEVICE_CALIBRATION_VALIDATED"


def test_v2_real_q1_runtime_containment_passes_without_downstream_corruption():
    _, capture, _, numerical, diagnostic, q1 = formal_inputs()
    result = runtime_outlier_containment(capture, diagnostic, q1, numerical)
    assert result["result"] == "PASS"
    assert [(x["pose"], x["seq"]) for x in result["event_audit"]] == [(5, 29761), (6, 45999)]
    assert all(not x["accepted"] and x["rejection_reason"] == "INNOVATION_NIS_REJECTED"
               and x["pass"] for x in result["event_audit"])
    assert result["event_audit"][0]["nis"] == pytest.approx(924.8456242572006)
    assert result["event_audit"][1]["nis"] == pytest.approx(911.445415196842)


def test_v2_fails_if_extreme_event_is_accepted_and_materially_corrupts_state():
    _, capture, _, numerical, diagnostic, q1 = formal_inputs()
    corrupt = copy.deepcopy(q1)
    row = next(x for x in corrupt if x["transient_candidate"])
    row.update(accepted=True, reason="ACCEPTED", quaternion_update_step_deg=5.0,
               covariance_min_eigenvalue=-1.0, motion_state="MOVING", numerical_pass=False)
    result = runtime_outlier_containment(capture, diagnostic, corrupt, numerical)
    assert result["result"] == "FAIL"
    assert not result["global_checks"]["all_events_contained"]


def test_v2_fails_closed_on_capture_integrity_corruption():
    systematic, capture, _, numerical, diagnostic, q1 = formal_inputs()
    broken = copy.deepcopy(capture); broken["pass"] = False
    containment = runtime_outlier_containment(broken, diagnostic, q1, numerical)
    final, disposition = aggregate_v2(systematic, broken, containment)
    assert containment["result"] == "FAIL"
    assert final["primary_verdict"] == "C2CC_DEVICE_CALIBRATION_NOT_VALIDATED"
    assert disposition["to"] == "FROZEN_CANDIDATE_PENDING_REVALIDATION"


def test_v2_fails_closed_on_sustained_burst_outside_observed_scope():
    _, capture, _, numerical, diagnostic, q1 = formal_inputs()
    burst = copy.deepcopy(diagnostic); burst["maximum_consecutive_anomalous_samples"] = 2
    result = runtime_outlier_containment(capture, burst, q1, numerical)
    assert result["result"] == "FAIL"
    assert result["unsupported_generalization"] == "ARBITRARY_MULTI_SAMPLE_BURSTS_NOT_PROVEN_SAFE"


def test_v2_does_not_refit_or_mutate_frozen_or_historical_inputs():
    before = {path: sha256(path) for path in EXPECTED_SHA256}
    assert before == EXPECTED_SHA256
    source = (Path(__file__).parents[1] / "derive_v47_c2cc_qualification_policy_v2.py").read_text()
    assert "fit_model" not in source and "fit_and_select" not in source
    assert sha256(PROFILE) == "10895c252adbe23cb26ef1e0824abf460f3b8c03fd04d63508e06242fe63a73c"
    assert load_json(HISTORICAL / "TRANSIENT_DISPOSITION.json")["historical_primary_verdict_preserved"] == "C2CC_DEVICE_CALIBRATION_FAIL"


def test_complete_v2_derivation_is_byte_deterministic(tmp_path):
    first = tmp_path / "first"; second = tmp_path / "second"
    a = derive(first); b = derive(second)
    assert a == b
    assert a["policy"] == POLICY_NAME
    assert a["new_v2_verdict"] == "C2CC_DEVICE_CALIBRATION_VALIDATED"
    assert (first / "SHA256SUMS").read_bytes() == (second / "SHA256SUMS").read_bytes()
    for name in a["output_hashes"]:
        assert (first / name).read_bytes() == (second / name).read_bytes()
