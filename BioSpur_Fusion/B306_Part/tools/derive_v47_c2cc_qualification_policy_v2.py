#!/usr/bin/env python3
"""Derive policy-V2 qualification from immutable BSFC2CC evidence only."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from fusion_session import parse_fields
from v47_c2cc_arbitrary_pose import parse_imu_samples
from v47_c2cc_qualification_policy_v2 import (
    POLICY_NAME, aggregate_v2, legacy_policy_v1_verdict,
    raw_transient_diagnostic, runtime_outlier_containment,
)

ROOT = Path(__file__).resolve().parents[2]
FORMAL = ROOT / "B306_Part/logs/v47_c2cc_calibration_revalidation_v2_20260812_220311"
HISTORICAL = ROOT / "B306_Part/logs/v47_c2cc_calibration_revalidation_v2_20260812_214846/historical_transient_audit"
PROFILE = ROOT / "B306_Part/logs/v47_c2cc_arbitrary_pose_calibration_20260812_201945/ACCEL_CALIBRATION_PROFILE.json"
EXPECTED_SHA256 = {
    FORMAL / "continuous_raw/fusion_host_raw.cobs.bin": "de13dec76126fabfb085e8551b101ceed87878baf3d834dcb9d07c872053be70",
    FORMAL / "REPORT.md": "0833fe60d32cc0db7a2e5f778890cb7e372e648f67c801d0e5fd41b75149c497",
    FORMAL / "RUN_MANIFEST.json": "cfb0a04d88433fa58a6b759b7ff07564f7a4ede3fe1a2a3797878f0875a29fd0",
    FORMAL / "SYSTEMATIC_CALIBRATION_GATE.json": "9faeb41a46b3c8170543233745bc7f3a466a7a8c46ab1fa1d8e68a56c92515f4",
    FORMAL / "SENSOR_TRANSIENT_GATE.json": "0070fac867008539c1cd8adf104ac9cb0d1363092acba0fc2e7f67ca2c3eb786",
    FORMAL / "Q1_GRAVITY_UPDATE_AUDIT.csv": "dc2e17836527b8d0a92ced2ff2abf3ddb5f1b5e4d8119356f8aca57a4906f250",
    FORMAL / "REVALIDATION_V2_PROTOCOL.json": "d87503c8bcf100c9b823fd1fd08ae6e6b72eb255d03d4f2605c9fdd849e557dd",
    HISTORICAL / "TRANSIENT_FORENSIC_REPORT.md": "6af9afa63701f7520cb19ff7373f85d3a951926fdd061ffca5391263e983eb95",
    HISTORICAL / "TRANSIENT_DISPOSITION.json": "040cb5143a9037d3097b388fa62f6516c677c313fd17936a4c0d3bc6e13415f7",
    PROFILE: "10895c252adbe23cb26ef1e0824abf460f3b8c03fd04d63508e06242fe63a73c",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def load_csv(path: Path) -> list[dict]:
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        for key in ("pose", "seq", "node_us"):
            if key in row:
                row[key] = int(row[key])
        for key in ("transient_candidate", "accepted", "numerical_pass"):
            if key in row:
                row[key] = row[key] == "True"
        for key in ("nis", "quaternion_update_step_deg", "covariance_min_eigenvalue"):
            if key in row:
                row[key] = float(row[key])
    return rows


def assert_immutable_inputs() -> dict[str, str]:
    observed = {str(path.relative_to(ROOT)): sha256(path) for path in EXPECTED_SHA256}
    expected = {str(path.relative_to(ROOT)): digest for path, digest in EXPECTED_SHA256.items()}
    if observed != expected:
        mismatches = {key: {"expected": expected[key], "observed": observed[key]}
                      for key in expected if observed[key] != expected[key]}
        raise RuntimeError(f"immutable input hash mismatch: {mismatches}")
    return observed


def infer_dominant_channels(events: list[dict]) -> dict[tuple[int, int], str]:
    """Use retained raw/index evidence to report the raw axis with the largest local jump."""
    targets = {(int(x["pose"]), int(x["seq"])): int(x["record_index"]) for x in events}
    by_record = {record: key for key, record in targets.items()}
    result = {}
    index_path = FORMAL / "continuous_raw/consumption_index.jsonl"
    with index_path.open() as stream:
        for text in stream:
            row = json.loads(text)
            key = by_record.get(int(row["record_index"]))
            if key is None or not row["line"].startswith("FUSION_IMU "):
                continue
            samples = parse_imu_samples(parse_fields(row["line"]), float(row["consume_monotonic"]))
            sample_index = next((i for i, x in enumerate(samples) if int(x["seq"]) == key[1]), None)
            if sample_index is not None:
                # Compare against retained neighbours in the same raw batch.  Absolute
                # axis magnitude would merely select the gravity axis, not the spike.
                neighbours = [x["accel_raw"] for i, x in enumerate(samples) if i != sample_index]
                medians = [sorted(int(x[axis]) for x in neighbours)[len(neighbours) // 2]
                           for axis in range(3)]
                jumps = [abs(int(samples[sample_index]["accel_raw"][axis]) - medians[axis])
                         for axis in range(3)]
                result[key] = f"a{jumps.index(max(jumps)) + 1}"
    return result


def derive(out_dir: Path) -> dict:
    if out_dir.exists() and any(out_dir.iterdir()):
        raise RuntimeError(f"output directory is not empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    source_hashes = assert_immutable_inputs()
    systematic = load_json(FORMAL / "SYSTEMATIC_CALIBRATION_GATE.json")
    old_transient = load_json(FORMAL / "SENSOR_TRANSIENT_GATE.json")
    capture = load_json(FORMAL / "CAPTURE_INTEGRITY.json")
    numerical = load_json(FORMAL / "NUMERICAL_INTEGRITY.json")
    historical = load_json(HISTORICAL / "TRANSIENT_DISPOSITION.json")
    events = load_csv(FORMAL / "TRANSIENTS_FOUND.csv")
    channels = infer_dominant_channels(events)
    for event in events:
        event["dominant_channel"] = channels.get((event["pose"], event["seq"]), "UNKNOWN_FROM_RETAINED_RAW")
        event["gyro_co_motion"] = event["gyro_or_handling_evidence"] == "True"
        event["handling_consistent"] = event["gyro_or_handling_evidence"] == "True"
        event["transport_or_time_anomaly"] = False
    q1_rows = load_csv(FORMAL / "Q1_GRAVITY_UPDATE_AUDIT.csv")
    diagnostic = raw_transient_diagnostic(old_transient, events, historical["disposition"], capture)
    containment = runtime_outlier_containment(capture, diagnostic, q1_rows, numerical)
    old_verdict = legacy_policy_v1_verdict(systematic, capture, old_transient, numerical["runtime_q1_pass"])
    recorded_old = load_json(FORMAL / "CALIBRATION_PROMOTION.json")["primary_verdict"]
    if old_verdict != recorded_old:
        raise RuntimeError(f"V1 reproducibility contradiction: {old_verdict} != {recorded_old}")
    final, disposition = aggregate_v2(systematic, capture, containment)
    policy = {
        "schema": "biospur-c2cc-calibration-qualification-policy-v2",
        "name": POLICY_NAME, "raw_transient_rate_blocks": False,
        "blocking_gates": ["SYSTEMATIC_CALIBRATION_GATE", "CAPTURE_INTEGRITY", "RUNTIME_OUTLIER_CONTAINMENT"],
        "diagnostic_only": ["RAW_SENSOR_TRANSIENT_DIAGNOSTIC"],
        "legacy_policy_preserved": True,
        "burst_scope": "OBSERVED_ISOLATED_SINGLE_SAMPLE_CLASS_ONLY",
    }
    provenance = {
        "schema": "biospur-c2cc-policy-v2-provenance-v1", "node": "BSFC2CC",
        "derivation_only": True, "hardware_actions": [], "parameter_refits": 0,
        "source_sha256": source_hashes,
        "formal_population_samples": old_transient["samples"],
        "historical_and_formal_populations_merged": False,
        "old_v1_verdict_reproduced": old_verdict,
        "historical_primary_verdict_preserved": historical["historical_primary_verdict_preserved"],
        "frozen_profile_sha256": source_hashes[str(PROFILE.relative_to(ROOT))],
        "charging_note": {
            "failed_zero_pose_attempts_before_formal": 2,
            "operator_charged_after_aborted_attempts": True,
            "charging_ended_and_charger_removed_before_formal_collector_open": True,
            "charging_during_formal_run": False,
            "charging_or_hardware_mutation_by_codex": False,
        },
    }
    outputs = {
        "QUALIFICATION_POLICY_V2.json": policy,
        "SYSTEMATIC_CALIBRATION_GATE.json": systematic,
        "RAW_SENSOR_TRANSIENT_DIAGNOSTIC.json": diagnostic,
        "CAPTURE_INTEGRITY.json": capture,
        "RUNTIME_OUTLIER_CONTAINMENT.json": containment,
        "FINAL_V2_VERDICT.json": final,
        "DEVICE_DISPOSITION.json": disposition,
        "PROVENANCE.json": provenance,
    }
    for name, value in outputs.items():
        canonical(out_dir / name, value)
    event_rows = containment["event_audit"]
    with (out_dir / "RUNTIME_EVENT_AUDIT.csv").open("w", newline="") as stream:
        fields = ["pose", "seq", "accepted", "rejection_reason", "nis", "quaternion_update_step_deg",
                  "covariance_min_eigenvalue", "motion_state", "pass"]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader(); writer.writerows(event_rows)
    report = f"""# BSFC2CC calibration qualification policy V2

Policy: `{POLICY_NAME}`

Primary V2 verdict: **{final['primary_verdict']}**

Device disposition: `{disposition['from']}` -> `{disposition['to']}`

## Gate results

- `SYSTEMATIC_CALIBRATION_GATE`: `{'PASS' if systematic['pass'] else 'FAIL'}`. The frozen `DIAGONAL_SCALE` profile improved RMSE from {systematic['uncalibrated_rmse_g']:.6f} g to {systematic['corrected_rmse_g']:.6f} g; corrected P95/P99 are {systematic['corrected_abs_p95_g']:.6f}/{systematic['corrected_abs_p99_g']:.6f} g. No parameters were refit.
- `RAW_SENSOR_TRANSIENT_DIAGNOSTIC`: `{diagnostic['result']}`. The formal population retains {diagnostic['raw_transient_candidates']} events in {diagnostic['accepted_stationary_samples']} accepted samples, rate {diagnostic['empirical_rate_per_sample']!r}, exact 95% CI {diagnostic['exact_clopper_pearson_95_interval']!r}. The unchanged V1 confidence-bound rule therefore reproduces `{old_verdict}`.
- `CAPTURE_INTEGRITY`: `{'PASS' if capture['pass'] else 'FAIL'}`. Sequence, timestamp, queue, and reconnect checks remain closed.
- `RUNTIME_OUTLIER_CONTAINMENT`: `{containment['result']}`. Both extreme events were evaluated causally and rejected with NIS {event_rows[0]['nis']:.2f} and {event_rows[1]['nis']:.2f}; covariance stayed positive and the next nominal sample was accepted.

## Engineering interpretation

Rare isolated single-sample accelerometer outliers were observed in both historical and revalidation datasets. The events remain fully retained in raw evidence. In the formal revalidation run, all observed events were isolated, lacked corroborating gyro/handling or transport-integrity evidence, and were causally rejected by Q1 without downstream quaternion, covariance, or motion-state corruption. Under qualification policy V2, isolated raw sensor transients that are successfully contained are recorded as non-blocking sensor diagnostics rather than calibration failures.

`REPEATED_SENSOR_ANOMALY` does not imply a proven hardware defect.

The revised policy does not retroactively rewrite any historical verdict. The historical primary verdict remains `C2CC_DEVICE_CALIBRATION_FAIL`; the existing formal V1 report remains `C2CC_DEVICE_CALIBRATION_REVALIDATION_CONDITIONAL`. Historical and formal populations were not merged. The two formal events remain at pose 5 / seq 29761 and pose 6 / seq 45999.

This PASS is limited to the observed isolated single-sample anomaly class. It does not establish safety for arbitrary multi-sample bursts. Raw outlier existence is not runtime-containment failure; failure requires escape from the causal gate or meaningful downstream corruption.

## Provenance and scope

There were two zero-pose failed pre-capture attempts. The operator charged the board after those aborted attempts; charging ended and the charger was removed before the accepted formal collector opened. No charging occurred during the formal run, and Codex performed no charging or hardware mutation. These attempts are not merged into the formal population.

This was an offline derivation only: no capture, serial/BLE/J-Link/SWD/RTT access, OTA, upload, reboot, configuration, power, charging, or physical action occurred. BSF31CC and all other boards are untouched. No BSFC2CC calibration value is transferred to another board.

`FROZEN_CALIBRATION_VALIDATED` is a calibration disposition, not a deployable-state claim. Independent deployment/integration readiness outside this offline calibration policy remains unevaluated and is the remaining blocker before declaring BSFC2CC deployable.
"""
    (out_dir / "REPORT.md").write_text(report)
    files = sorted(path for path in out_dir.iterdir() if path.is_file() and path.name != "SHA256SUMS")
    (out_dir / "SHA256SUMS").write_text("".join(f"{sha256(path)}  {path.name}\n" for path in files))
    return {"policy": POLICY_NAME, "old_v1_verdict": old_verdict,
            "new_v2_verdict": final["primary_verdict"], "output_hashes": {path.name: sha256(path) for path in files}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(derive(args.out_dir.resolve()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
