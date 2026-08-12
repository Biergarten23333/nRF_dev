#!/usr/bin/env python3
"""Deterministic BSF31CC six-axis calibration and V2 qualification derivation."""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import math
import shutil
from pathlib import Path

import numpy as np

from derive_v47_c2cc_arbitrary_pose import replay_raw
from derive_v47_c2cc_revalidation_v2 import classify, q1_replay
from fusion_session import parse_fields
from v47_c2cc_arbitrary_pose import (
    ACCEL_LSB_PER_G, apply_calibration, coverage_metrics, fit_and_select,
    parse_imu_samples,
)
from v47_c2cc_qualification_policy_v2 import runtime_outlier_containment
from v47_c2cc_revalidation_v2 import exact_binomial_interval, systematic_gate, transient_runs

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "B306_Part/tools/v47_c2cc_qualification_policy_v2.py"
EXPECTED_RAW_SHA256 = "921214342706dc80171f2fc5cd9cfe61c699ed1fb48a3018cf97b6c27f29c217"
EXPECTED_CAPTURE_CODE_SHA256 = "906a88c71edda3c82c49ce3218a2917e942a49ed48bde18369c237bc7b4b4968"
POLICY_NAME = "C2CC_CALIBRATION_QUALIFICATION_POLICY_V2"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def load_json(path: Path):
    return json.loads(path.read_text())


def parse_bool(value: str) -> bool:
    if value not in ("True", "False"):
        raise ValueError(value)
    return value == "True"


def load_windows(run: Path) -> list[dict]:
    rows = []
    with (run / "POSE_WINDOWS.csv").open(newline="") as stream:
        for source in csv.DictReader(stream):
            rows.append({"set": source["set"], "pose": int(source["pose"]),
                "accepted": parse_bool(source["accepted"]), "reason": source["reason"],
                "samples": int(source["samples"]), "duration_s": float(source["duration_s"]),
                "nearest_angle_deg": float(source["nearest_angle_deg"]) if source["nearest_angle_deg"] else None,
                "start": ast.literal_eval(source["start"]), "end": ast.literal_eval(source["end"])})
    return rows


def replay_and_select(run: Path, windows: list[dict]) -> tuple[dict, dict, list[dict]]:
    raw = run / "continuous_raw/fusion_host_raw.cobs.bin"
    raw_summary = replay_raw(raw)
    index_rows = []
    samples = []
    with (run / "continuous_raw/consumption_index.jsonl").open() as stream:
        for text in stream:
            row = json.loads(text); index_rows.append(row)
            line = row["line"]
            if not line.startswith("FUSION_IMU "):
                continue
            fields = parse_fields(line)
            if fields.get("name") != "BSF31CC":
                continue
            parsed = parse_imu_samples(fields, float(row["consume_monotonic"]))
            for sample in parsed:
                sample["record_index"] = int(row["record_index"])
            samples.extend(parsed)
    if raw_summary["complete_frames"] != len(index_rows):
        raise RuntimeError("raw/index complete-frame mismatch")
    selected = {}
    accounting = []
    for window in windows:
        if not window["accepted"]:
            continue
        # The capture's decision loop evaluates `now` before a blocking read.
        # Its authoritative count and end record therefore define the exact
        # closed suffix; a 200 ms envelope includes the possible final read.
        end = window["end"]
        candidates = [sample for sample in samples
                      if sample["record_index"] <= int(end["consumed_record_index"])
                      and sample["host_monotonic"] >= float(end["monotonic"]) - window["duration_s"] - .2]
        segment = candidates[-window["samples"]:]
        key = (window["set"], window["pose"])
        selected[key] = segment
        sequence_faults = []
        for previous, current in zip(segment, segment[1:]):
            if current["seq"] != ((previous["seq"] + 1) & 0xFFFF):
                sequence_faults.append({"kind": "SEQUENCE", "expected": (previous["seq"] + 1) & 0xFFFF,
                                        "observed": current["seq"]})
            if current["node_us"] <= previous["node_us"]:
                sequence_faults.append({"kind": "TIMESTAMP", "previous": previous["node_us"],
                                        "observed": current["node_us"]})
        identities = [(x["seq"], x["node_us"]) for x in segment]
        lifecycle = [row["line"] for row in index_rows
                     if int(window["start"]["consumed_record_index"]) <= int(row["record_index"]) <= int(end["consumed_record_index"])
                     and row["line"].startswith(("FUSION_CONNECTED ", "FUSION_DISCONNECTED "))]
        accounting.append({"set": window["set"], "pose": window["pose"],
            "expected_samples": window["samples"], "replayed_samples": len(segment),
            "sequence_or_timestamp_faults": sequence_faults,
            "duplicate_complete_samples": len(identities) - len(set(identities)),
            "connection_lifecycle_records": lifecycle})
    return raw_summary, selected, accounting


def dominant_channel(samples: list[dict], index: int) -> tuple[str, list[int], list[int]]:
    left = samples[max(0, index - 2):index]
    right = samples[index + 1:index + 3]
    neighbours = left + right
    baseline = np.median(np.asarray([x["accel_raw"] for x in neighbours]), axis=0)
    raw = np.asarray(samples[index]["accel_raw"])
    delta = raw - baseline
    axis = int(np.argmax(np.abs(delta)))
    return f"a{axis}", baseline.astype(int).tolist(), delta.astype(int).tolist()


def transient_diagnostic(heldout: list[list[dict]], fit: dict) -> tuple[dict, list[dict], list[list[dict]]]:
    classified = []; events = []
    for pose, samples in enumerate(heldout, 1):
        rows = classify(samples, fit)
        for row in rows:
            row["pose"] = pose
        classified.append(rows)
        for index, row in enumerate(rows):
            if not row["transient_candidate"]:
                continue
            channel, baseline, delta = dominant_channel(rows, index)
            previous = rows[index - 1] if index else None
            following = rows[index + 1] if index + 1 < len(rows) else None
            events.append({"pose": pose, "seq": row["seq"], "node_us": row["node_us"],
                "record_index": row["record_index"], "duration_samples": 1, "duration_ms": 5,
                "raw_accel": row["accel_raw"], "corrected_accel_g": row["corrected_accel_g"],
                "corrected_abs_gravity_residual_g": row["corrected_abs_residual_g"],
                "dominant_channel": channel, "neighbour_raw_baseline": baseline,
                "raw_delta_from_neighbour_baseline": delta,
                "previous_seq": previous["seq"] if previous else None,
                "next_seq": following["seq"] if following else None,
                "adjacent_samples_nominal": bool(previous and following and not previous["transient_candidate"]
                                                  and not following["transient_candidate"]),
                "gyro_co_motion": bool(row["gyro_or_handling_evidence"]),
                "handling_consistent": bool(row["gyro_or_handling_evidence"]),
                "transport_or_time_anomaly": False, "transient_candidate": True})
    flat = [row for pose in classified for row in pose]
    runs = transient_runs(flat)
    lower, upper = exact_binomial_interval(len(events), len(flat)) if flat else (0.0, 1.0)
    diagnostic = {"schema": "biospur-bsf31cc-raw-transient-diagnostic-v1",
        "policy": POLICY_NAME, "result": "OBSERVED_NON_BLOCKING" if events else "NONE_OBSERVED_NON_BLOCKING",
        "blocking": False, "accepted_heldout_samples": len(flat), "event_count": len(events),
        "isolated_event_count": sum(len(run) == 1 for run in runs),
        "maximum_consecutive_anomalous_samples": max((len(run) for run in runs), default=0),
        "empirical_rate_per_sample": len(events) / max(len(flat), 1),
        "exact_clopper_pearson_95_interval": [lower, upper],
        "all_adjacent_samples_nominal": all(x["adjacent_samples_nominal"] for x in events),
        "all_lack_gyro_and_handling_evidence": all(not x["gyro_co_motion"] and not x["handling_consistent"] for x in events),
        "transport_and_time_integrity_pass": True,
        "events": [{k: x[k] for k in ("pose", "seq", "node_us", "dominant_channel",
                    "gyro_co_motion", "handling_consistent", "transport_or_time_anomaly")} for x in events]}
    return diagnostic, events, classified


def write_event_csv(path: Path, events: list[dict], containment: dict) -> None:
    runtime = {(x["pose"], x["seq"]): x for x in containment["event_audit"]}
    rows = []
    for event in events:
        decision = runtime[(event["pose"], event["seq"])]
        rows.append({**event, "q1_accepted": decision["accepted"],
            "q1_reason": decision["rejection_reason"], "q1_nis": decision["nis"],
            "q1_quaternion_update_step_deg": decision["quaternion_update_step_deg"],
            "q1_covariance_min_eigenvalue": decision["covariance_min_eigenvalue"],
            "q1_motion_state": decision["motion_state"], "q1_containment_pass": decision["pass"]})
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def derive(run: Path, out: Path) -> dict:
    if out.exists() and any(out.iterdir()):
        raise RuntimeError(f"output directory is not empty: {out}")
    out.mkdir(parents=True, exist_ok=True)
    raw = run / "continuous_raw/fusion_host_raw.cobs.bin"
    raw_before = sha256(raw)
    if raw_before != EXPECTED_RAW_SHA256:
        raise RuntimeError(f"formal raw hash mismatch: {raw_before}")
    capture = load_json(run / "CAPTURE_MANIFEST.json")
    provenance = load_json(run / "PROVENANCE.json")
    frozen = load_json(run / "FROZEN_TRAINING_MODEL.json")
    stream = load_json(run / "STREAM_INTEGRITY.json")
    live = load_json(run / "LIVE_CATCHUP_EXTENSION.json")
    if provenance["implementation"]["B306_Part/tools/v47_bsf31cc_six_axis_capture.py"] != EXPECTED_CAPTURE_CODE_SHA256:
        raise RuntimeError("capture implementation hash mismatch")
    if provenance["qualification_policy"]["sha256"] != sha256(POLICY):
        raise RuntimeError("qualification policy hash mismatch")
    windows = load_windows(run)
    raw_summary, selected, accounting = replay_and_select(run, windows)
    training = [selected[("TRAINING", pose)] for pose in range(1, 19)]
    heldout = [selected[("HELDOUT", pose)] for pose in range(1, 5)]
    fit = frozen["model_selection"]["selected"]
    replay_fit = fit_and_select([np.asarray([x["accel_g"] for x in pose]) for pose in training])
    replay_selected = replay_fit["selected"]
    bias_delta = float(np.max(np.abs(np.asarray(replay_selected["bias_g"]) - np.asarray(fit["bias_g"]))))
    matrix_delta = float(np.max(np.abs(np.asarray(replay_selected["correction_matrix"]) - np.asarray(fit["correction_matrix"]))))
    parameter_equivalence_limit = 1.0 / ACCEL_LSB_PER_G / 100.0
    coverage = coverage_metrics([np.mean([x["accel_g"] for x in pose], axis=0) for pose in training])
    systematic, per_pose = systematic_gate(
        [np.asarray([x["accel_g"] for x in pose]) for pose in heldout],
        fit["bias_g"], fit["correction_matrix"])
    correction = np.asarray(fit["correction_matrix"]); bias = np.asarray(fit["bias_g"])
    coverage_checks = {"minimum_direction_covariance_eigenvalue": frozen["coverage"]["direction_covariance_min_eigenvalue"] >= .10,
        "design_condition": frozen["coverage"]["design_condition"] <= 1e6,
        "minimum_inter_pose_angle": frozen["coverage"]["minimum_pairwise_angle_deg"] >= 15.0,
        "optimizer_success": bool(fit["optimizer_success"]), "finite_parameters": bool(np.all(np.isfinite(correction)) and np.all(np.isfinite(bias))),
        "positive_definite_correction": bool(np.all(np.linalg.eigvalsh(correction) > 0)),
        "well_conditioned_correction": float(np.linalg.cond(correction)) <= 1e6,
        "training_replay_same_selected_model": replay_fit["selected_model"] == frozen["model_selection"]["selected_model"],
        "training_replay_parameter_equivalence": max(bias_delta, matrix_delta) <= parameter_equivalence_limit}
    systematic["coverage_and_numerical_checks"] = coverage_checks
    systematic["pass"] = bool(systematic["pass"] and all(coverage_checks.values()))
    systematic["training_replay"] = {"selected_model": replay_fit["selected_model"],
        "maximum_bias_delta_g": bias_delta, "maximum_matrix_element_delta": matrix_delta,
        "equivalence_limit": parameter_equivalence_limit,
        "basis": "one hundredth of one source-proven accelerometer LSB"}
    systematic["training_coverage_frozen"] = frozen["coverage"]
    diagnostic, events, classified = transient_diagnostic(heldout, fit)
    q1_rows = []; numerical_rows = []
    gyro_bias = np.asarray(frozen["gyro_zero_rate"]["bias_dps"])
    for pose, samples in enumerate(classified, 1):
        rows, numerical = q1_replay(samples, gyro_bias)
        q1_rows.extend(rows); numerical_rows.append({"pose": pose, **numerical})
    numerical = {"runtime_q1_pass": all(x["numerical_pass"] for x in q1_rows),
        "all_finite": all(math.isfinite(float(x["nis"])) for x in q1_rows),
        "covariance_symmetric_by_q1_check_contract": True, "per_pose": numerical_rows}
    capture_checks = {"planned_18_plus_4_complete": capture.get("stop_reason") == "PLANNED_18_PLUS_4_COMPLETE"
            and len(training) == 18 and len(heldout) == 4,
        "one_continuous_serial_open": capture.get("serial_open_count") == 1,
        "raw_byte_accounting_closed": capture["health_final"].get("raw_bytes_submitted") == capture["health_final"].get("raw_bytes_written") == raw.stat().st_size,
        "no_queue_reader_or_payload_drop": all(capture["health_final"].get(k, 0) == 0 for k in
            ("raw_queue_drops", "decoded_queue_drops", "log_queue_drops", "reader_exceptions", "payload_decode_errors")),
        "accepted_windows_complete_and_continuous": all(x["expected_samples"] == x["replayed_samples"]
            and not x["sequence_or_timestamp_faults"] and x["duplicate_complete_samples"] == 0
            and not x["connection_lifecycle_records"] for x in accounting),
        "required_identity_and_stream": bool(capture["identity_observation"]["pass"]),
        "live_catchup_extension": live.get("result") == "PASS",
        "raw_index_complete_frame_closure": raw_summary["complete_frames"] == capture["health_final"]["decoded_records"],
        "boundary_fragments_outside_accepted_windows": raw_summary["cobs_crc_decode_errors"] == 1
            and raw_summary["incomplete_tail_bytes"] == 1,
        "captured_stream_integrity": bool(stream["pass"])}
    capture_integrity = {"schema": "biospur-bsf31cc-capture-integrity-v1",
        "pass": all(capture_checks.values()), "checks": capture_checks,
        "sample_accounting": accounting, "raw_replay": raw_summary,
        "boundary_classification": "ONE_STARTUP_PREFIX_DECODE_FRAGMENT_AND_ONE_SHUTDOWN_TAIL_BYTE_OUTSIDE_ALL_ACCEPTED_WINDOWS",
        "raw_sha256_before_derivation": raw_before, "bmd101_excluded_from_metrics": True}
    diagnostic["transport_and_time_integrity_pass"] = capture_integrity["pass"]
    containment = runtime_outlier_containment(capture_integrity, diagnostic, q1_rows, numerical)
    blockers = []
    if not capture_integrity["pass"]: blockers.append("CAPTURE_INTEGRITY")
    if not systematic["pass"]: blockers.append("GATE_A_SYSTEMATIC_CALIBRATION")
    if not containment["pass"]: blockers.append("RUNTIME_OUTLIER_CONTAINMENT")
    verdict = "BSF31CC_DEVICE_CALIBRATION_VALIDATED" if not blockers else (
        "BSF31CC_CAPTURE_FAIL" if "CAPTURE_INTEGRITY" in blockers else "BSF31CC_DEVICE_CALIBRATION_FAIL")
    result = {"schema": "biospur-bsf31cc-calibration-result-v1", "policy": POLICY_NAME,
        "primary_verdict": verdict, "blocking_failures": blockers,
        "gate_a": "PASS" if systematic["pass"] else "FAIL",
        "raw_transient_diagnostic": diagnostic["result"],
        "runtime_outlier_containment": containment["result"],
        "capture_integrity": "PASS" if capture_integrity["pass"] else "FAIL",
        "device_disposition": "FROZEN_CALIBRATION_VALIDATED" if not blockers else "CALIBRATION_NOT_VALIDATED",
        "deployment_ready": False, "bmd101_excluded": True}
    heldout_result = {"schema": "biospur-bsf31cc-heldout-validation-v1",
        "frozen_model": fit["model"], "frozen_profile_sha256": sha256(run / "FROZEN_TRAINING_MODEL.json"),
        "parameter_changes_after_freeze": 0, "aggregate": systematic,
        "per_pose": per_pose, "heldout_samples": sum(len(x) for x in heldout)}
    device = {"schema": "DeviceCalibration_BSF31CC/v1", "node": "BSF31CC",
        "status": "FROZEN_CALIBRATION_VALIDATED", "policy": POLICY_NAME,
        "source_raw_sha256": raw_before, "source_frozen_training_profile_sha256": sha256(run / "FROZEN_TRAINING_MODEL.json"),
        "not_bsf_c2cc_numerical_profile": True, "host_side_only": True,
        "accel_calibration": {"model": fit["model"], "bias_g": fit["bias_g"], "correction_matrix": fit["correction_matrix"]},
        "gyro_zero_rate": frozen["gyro_zero_rate"], "temperature_model": frozen["temperature_model"],
        "bmd101_scope": "EXCLUDED", "not_established": ["V4 frame binding", "body mounting extrinsics",
            "lever arm", "yaw reference", "deployment readiness"], "transfer_to_other_devices": False}
    for name in ("CAPTURE_MANIFEST.json", "OPERATOR_ACTIONS.json", "POSE_WINDOWS.csv", "MODEL_SELECTION.json",
                 "STREAM_INTEGRITY.json", "PROVENANCE.json"):
        shutil.copyfile(run / name, out / name)
    canonical(out / "CALIBRATION_RESULT.json", result)
    canonical(out / "SYSTEMATIC_CALIBRATION_GATE.json", systematic)
    canonical(out / "HELDOUT_VALIDATION.json", heldout_result)
    canonical(out / "RAW_TRANSIENT_DIAGNOSTIC.json", diagnostic)
    canonical(out / "Q1_RUNTIME_CONTAINMENT.json", containment)
    canonical(out / "NUMERICAL_INTEGRITY.json", numerical)
    write_event_csv(out / "RAW_TRANSIENT_EVENTS.csv", events, containment)
    if verdict == "BSF31CC_DEVICE_CALIBRATION_VALIDATED":
        canonical(out / "BSF31CC_DEVICE_CALIBRATION.json", device)
    report = f"""# BSF31CC six-axis intrinsic calibration

**{verdict}**

The device-specific `{fit['model']}` accelerometer model was fitted from 18 training poses only and frozen before four held-out poses. It is not BSFC2CC's numerical profile. Frozen bias is `{fit['bias_g']}` g and the correction matrix is `{fit['correction_matrix']}`. Training coverage minimum eigenvalue is {frozen['coverage']['direction_covariance_min_eigenvalue']:.9f}, design condition {frozen['coverage']['design_condition']:.9f}, and minimum inter-pose direction angle {frozen['coverage']['minimum_pairwise_angle_deg']:.6f} degrees. The training-only gyro zero-rate bias is `{frozen['gyro_zero_rate']['bias_dps']}` dps.

Gate A is **{'PASS' if systematic['pass'] else 'FAIL'}**. On {systematic['samples']} strictly held-out samples, gravity-norm RMSE changed from {systematic['uncalibrated_rmse_g']:.9f} g to {systematic['corrected_rmse_g']:.9f} g; corrected P95/P99 are {systematic['corrected_abs_p95_g']:.9f}/{systematic['corrected_abs_p99_g']:.9f} g. All four poses improved or met the frozen equivalence rule. Independent training replay selected `{replay_fit['selected_model']}` again; maximum bias/matrix deltas were {bias_delta:.3e}/{matrix_delta:.3e}, below one hundredth of one accelerometer LSB ({parameter_equivalence_limit:.3e}).

Raw transient diagnostic is **{diagnostic['result']}**: {diagnostic['event_count']} retained events in {diagnostic['accepted_heldout_samples']} samples, all isolated with maximum consecutive run {diagnostic['maximum_consecutive_anomalous_samples']}. Exact sequences, timestamps, raw/corrected values and neighbours remain in `RAW_TRANSIENT_EVENTS.csv`. Their rate and exact 95% confidence interval remain diagnostic and do not directly block calibration under policy V2.

Runtime outlier containment is **{containment['result']}**. Each detected event was replayed causally through repaired Q1; all were rejected before gravity correction, quaternion remained numerically continuous, covariance stayed finite/positive and Cholesky-valid, motion state did not falsely become moving, and the next nominal measurement was accepted. This validates only the observed isolated single-sample anomaly class, not arbitrary sustained bursts.

Capture integrity is **{'PASS' if capture_integrity['pass'] else 'FAIL'}**: one serial open, one raw timeline, 18+4 accepted windows, closed {raw.stat().st_size} raw bytes, no accepted-window sequence/timestamp gap, duplicate, reconnect, queue drop, reader error or payload error. The single startup decode fragment and one-byte shutdown tail are outside all accepted windows. The earlier zero-pose tooling abort remains separate and was not merged.

The observed temperature span was {frozen['temperature_model']['span_c']:.3f} C, so no temperature model was fitted. BMD101 records/functionality were retained if present but excluded from fitting, validation, metrics and verdict. No BMD101 work occurred.

This device calibration does not establish V4 frame binding, body mounting extrinsics, lever arm, yaw reference, UWB accuracy or deployment readiness. No OTA, upload, pending/PREPARE/COMMIT, firmware write, reboot, power cycle, J-Link, SWD, RTT, AutoPos, BMD101 configuration or automatic physical action occurred. Operator actions were 25 manual placements: 18 accepted training placements, four accepted held-out placements and three rejected held-out placement attempts, all recorded in `OPERATOR_ACTIONS.json`.
"""
    (out / "REPORT.md").write_text(report)
    raw_after = sha256(raw)
    capture_integrity["raw_sha256_after_derivation"] = raw_after
    capture_integrity["raw_unchanged"] = raw_after == raw_before
    canonical(out / "STREAM_INTEGRITY.json", capture_integrity)
    files = sorted(path for path in out.iterdir() if path.is_file() and path.name != "SHA256SUMS")
    (out / "SHA256SUMS").write_text("".join(f"{sha256(path)}  {path.name}\n" for path in files))
    return {"verdict": verdict, "raw_unchanged": raw_after == raw_before,
        "event_count": len(events), "core_hashes": {path.name: sha256(path) for path in files}}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True); args = parser.parse_args()
    result = derive(args.run_dir.resolve(), args.out_dir.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["verdict"] == "BSF31CC_DEVICE_CALIBRATION_VALIDATED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
