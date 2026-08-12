#!/usr/bin/env python3
"""Pure, versioned BSFC2CC calibration qualification policy V2.

V1 remains implemented by :mod:`v47_c2cc_revalidation_v2`.  This module does
not reinterpret or modify V1 evidence: it separates raw anomaly observation
from the blocking, causal runtime-containment decision.
"""
from __future__ import annotations

import math
import sys

POLICY_NAME = "C2CC_CALIBRATION_QUALIFICATION_POLICY_V2"
VALIDATED = "C2CC_DEVICE_CALIBRATION_VALIDATED"
CONDITIONAL_V1 = "C2CC_DEVICE_CALIBRATION_REVALIDATION_CONDITIONAL"


def legacy_policy_v1_verdict(systematic: dict, capture: dict, transient: dict,
                             runtime_q1_pass: bool) -> str:
    """Reproduce the frozen V1 aggregation without changing its semantics."""
    if not capture.get("pass"):
        return "C2CC_REVALIDATION_CAPTURE_FAIL"
    if not systematic.get("pass"):
        return "C2CC_DEVICE_CALIBRATION_REVALIDATION_FAIL"
    if transient.get("pass") and runtime_q1_pass:
        return "C2CC_DEVICE_CALIBRATION_REVALIDATION_PASS"
    if transient.get("conditional_only") and runtime_q1_pass:
        return CONDITIONAL_V1
    return "C2CC_DEVICE_CALIBRATION_REVALIDATION_FAIL"


def raw_transient_diagnostic(old_gate: dict, transient_events: list[dict],
                             historical_disposition: str,
                             capture_integrity: dict) -> dict:
    """Preserve V1 statistics exactly while making their V2 role explicit."""
    events = []
    for row in transient_events:
        events.append({
            "pose": int(row["pose"]),
            "seq": int(row["seq"]),
            "node_us": int(row["node_us"]),
            "dominant_channel": row.get("dominant_channel", "UNKNOWN_FROM_V1_ARTIFACT"),
            "gyro_co_motion": bool(row.get("gyro_co_motion", False)),
            "handling_consistent": bool(row.get("handling_consistent", False)),
            "transport_or_time_anomaly": bool(row.get("transport_or_time_anomaly", False)),
        })
    return {
        "schema": "biospur-c2cc-raw-transient-diagnostic-v2",
        "policy": POLICY_NAME,
        "result": "OBSERVED_NON_BLOCKING" if old_gate.get("transient_count", 0) else "NONE_OBSERVED_NON_BLOCKING",
        "blocking": False,
        "accepted_stationary_samples": int(old_gate["samples"]),
        "raw_transient_candidates": int(old_gate["transient_count"]),
        "isolated_transient_count": int(old_gate["isolated_count"]),
        "maximum_consecutive_anomalous_samples": int(old_gate["maximum_consecutive"]),
        "empirical_rate_per_sample": old_gate["rate_per_sample"],
        "exact_clopper_pearson_95_interval": list(old_gate["exact_clopper_pearson_95_interval"]),
        "old_v1_rate_confidence_exposure_sufficient": bool(old_gate["rate_confidence_exposure_sufficient"]),
        "old_v1_policy_consequence": CONDITIONAL_V1 if old_gate.get("conditional_only") else "NOT_CONDITIONAL",
        "events": events,
        "all_events_lack_gyro_co_motion": all(not x["gyro_co_motion"] for x in events),
        "all_events_lack_handling_consistency": all(not x["handling_consistent"] for x in events),
        "transport_and_time_integrity_pass": bool(capture_integrity.get("checks", {}).get("accepted_window_time_sequence"))
            and bool(capture_integrity.get("checks", {}).get("accepted_window_crc_decode_parse_serial_queue_errors"))
            and bool(capture_integrity.get("checks", {}).get("no_reconnect")),
        "historical_forensic_disposition": historical_disposition,
        "population_separation": "HISTORICAL_AND_FORMAL_COUNTS_NOT_MERGED",
    }


def _finite_number(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def runtime_outlier_containment(capture_integrity: dict, diagnostic: dict,
                                q1_rows: list[dict], numerical_integrity: dict) -> dict:
    """Evaluate containment of the observed isolated anomaly class.

    The quaternion bound is not a fitted engineering threshold.  A rejected
    Q1 update leaves the quaternion untouched; the audit's angle conversion
    can nevertheless show a few microdegrees because a unit-quaternion dot
    product is represented in binary64.  The bound below is the corresponding
    four-epsilon numerical-equivalence envelope.
    """
    epsilon = sys.float_info.epsilon
    q_equivalence_deg = math.degrees(2.0 * math.acos(1.0 - 4.0 * epsilon))
    transient_keys = {(int(x["pose"]), int(x["seq"])) for x in diagnostic["events"]}
    audit_by_key = {(int(x["pose"]), int(x["seq"])): (i, x) for i, x in enumerate(q1_rows)}
    event_audit = []
    for key in sorted(transient_keys):
        found = audit_by_key.get(key)
        if found is None:
            event_audit.append({"pose": key[0], "seq": key[1], "present_in_q1_audit": False, "pass": False})
            continue
        index, row = found
        previous = q1_rows[index - 1] if index > 0 and int(q1_rows[index - 1]["pose"]) == key[0] else None
        following = q1_rows[index + 1] if index + 1 < len(q1_rows) and int(q1_rows[index + 1]["pose"]) == key[0] else None
        checks = {
            "causally_rejected": row.get("accepted") is False and row.get("reason") == "INNOVATION_NIS_REJECTED",
            "nis_finite": _finite_number(row.get("nis")),
            "quaternion_numerically_unchanged": _finite_number(row.get("quaternion_update_step_deg"))
                and float(row["quaternion_update_step_deg"]) <= q_equivalence_deg,
            "covariance_positive_and_finite": _finite_number(row.get("covariance_min_eigenvalue"))
                and float(row["covariance_min_eigenvalue"]) > 0.0 and bool(row.get("numerical_pass")),
            "no_false_moving_state": row.get("motion_state") != "MOVING",
            "previous_nominal": previous is not None and not previous.get("transient_candidate")
                and bool(previous.get("accepted")) and previous.get("motion_state") == "STATIONARY",
            "next_nominal_accepted": following is not None and not following.get("transient_candidate")
                and bool(following.get("accepted")) and following.get("reason") == "ACCEPTED"
                and following.get("motion_state") == "STATIONARY" and bool(following.get("numerical_pass")),
        }
        event_audit.append({
            "pose": key[0], "seq": key[1], "present_in_q1_audit": True,
            "accepted": bool(row.get("accepted")), "rejection_reason": row.get("reason"),
            "nis": float(row["nis"]),
            "quaternion_update_step_deg": float(row["quaternion_update_step_deg"]),
            "covariance_min_eigenvalue": float(row["covariance_min_eigenvalue"]),
            "motion_state": row.get("motion_state"), "checks": checks, "pass": all(checks.values()),
        })
    per_pose = numerical_integrity.get("per_pose", [])
    global_checks = {
        "capture_integrity": bool(capture_integrity.get("pass")),
        "observed_class_isolated_single_samples": diagnostic.get("maximum_consecutive_anomalous_samples") == 1,
        "all_identified_events_evaluated": len(event_audit) == len(transient_keys) and all(x.get("present_in_q1_audit") for x in event_audit),
        "all_events_contained": bool(event_audit) and all(x.get("pass") for x in event_audit),
        "covariance_valid_full_replay": bool(numerical_integrity.get("runtime_q1_pass"))
            and bool(numerical_integrity.get("all_finite"))
            and bool(per_pose) and all(int(x.get("cholesky_failures", 1)) == 0
                                   and _finite_number(x.get("min_covariance_eigenvalue"))
                                   and float(x["min_covariance_eigenvalue"]) > 0.0 for x in per_pose),
    }
    passed = all(global_checks.values())
    return {
        "schema": "biospur-c2cc-runtime-outlier-containment-v2",
        "policy": POLICY_NAME,
        "result": "PASS" if passed else "FAIL",
        "pass": passed,
        "blocking": True,
        "supported_anomaly_class": "OBSERVED_ISOLATED_SINGLE_SAMPLE_ACCELEROMETER_OUTLIERS_ONLY",
        "unsupported_generalization": "ARBITRARY_MULTI_SAMPLE_BURSTS_NOT_PROVEN_SAFE",
        "q1_quaternion_numerical_equivalence_bound_deg": q_equivalence_deg,
        "q1_quaternion_bound_basis": "binary64 four-epsilon unit-quaternion dot-product envelope; rejected Q1 update has no correction step",
        "global_checks": global_checks,
        "event_audit": event_audit,
    }


def aggregate_v2(systematic: dict, capture: dict, containment: dict,
                 other_calibration_blockers: list[str] | None = None) -> tuple[dict, dict]:
    blockers = list(other_calibration_blockers or [])
    checks = {
        "systematic_calibration_validity": bool(systematic.get("pass")),
        "capture_integrity": bool(capture.get("pass")),
        "runtime_outlier_containment": bool(containment.get("pass")),
        "no_other_calibration_qualification_blocker": not blockers,
    }
    passed = all(checks.values())
    verdict = VALIDATED if passed else "C2CC_DEVICE_CALIBRATION_NOT_VALIDATED"
    final = {"schema": "biospur-c2cc-final-qualification-v2", "policy": POLICY_NAME,
             "primary_verdict": verdict, "pass": passed, "checks": checks,
             "other_calibration_blockers": blockers,
             "raw_transient_diagnostic_is_non_blocking": True}
    disposition = {
        "schema": "biospur-c2cc-device-disposition-v2", "policy": POLICY_NAME,
        "node": "BSFC2CC", "from": "FROZEN_CANDIDATE_PENDING_REVALIDATION",
        "to": "FROZEN_CALIBRATION_VALIDATED" if passed else "FROZEN_CANDIDATE_PENDING_REVALIDATION",
        "deployable_state_claimed": False,
        "remaining_independent_deployment_gate": "NOT_EVALUATED_OUTSIDE_OFFLINE_CALIBRATION_POLICY_SCOPE",
        "numeric_transfer_to_other_boards": False, "parameter_changes": 0,
    }
    return final, disposition
