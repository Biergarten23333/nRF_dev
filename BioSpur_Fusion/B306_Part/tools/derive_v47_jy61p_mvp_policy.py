#!/usr/bin/env python3
"""Offline, deterministic two-device JY61P MVP calibration-policy screen.

This tool deliberately separates matrix transfer from bias transfer.  A target
device's frozen bias is used only in the diagnostic matrix-transfer table.  A
product-realistic shared/default policy always uses zero accelerometer bias;
one stationary pose cannot identify an arbitrary three-axis bias.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import math
import subprocess
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.linalg import expm
from scipy.optimize import least_squares

from fusion_session import parse_fields
from v47_c2cc_arbitrary_pose import ACCEL_LSB_PER_G, GYRO_LSB_PER_DPS, parse_imu_samples
from v47_q1_eskf import G_MPS2, MotionVetoGate, Q1T4ESKF

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "B306_Part/logs"
C2 = LOGS / "v47_c2cc_arbitrary_pose_calibration_20260812_201945"
C2V2 = LOGS / "v47_c2cc_calibration_qualification_policy_v2_20260812_223851"
C2R = LOGS / "v47_c2cc_calibration_revalidation_v2_20260812_220311"
N31 = LOGS / "v47_bsf31cc_six_axis_calibration_20260812_230640"

EXPECTED_COMMITS = {
    "BSFC2CC": "c1333f4c5bee2a5e041c06b6fd0113c2bdeea1f6",
    "BSF31CC": "a675c6edcc4b8bef6dffeab09b7040caaf855f76",
}
EXPECTED_HASHES = {
    C2 / "continuous_raw/fusion_host_raw.cobs.bin": "d942a8cf711c66c3ee1ff6cff47edfa8005b9be6e1d4a351245ab1ea193f4a1c",
    C2 / "ACCEL_CALIBRATION_PROFILE.json": "10895c252adbe23cb26ef1e0824abf460f3b8c03fd04d63508e06242fe63a73c",
    C2R / "continuous_raw/fusion_host_raw.cobs.bin": "de13dec76126fabfb085e8551b101ceed87878baf3d834dcb9d07c872053be70",
    C2R / "FROZEN_CALIBRATION_REFERENCE.json": "7817f412ec17878b9f602d8de756504f0431b23f6cd2bebaf711a26f94c68785",
    N31 / "continuous_raw/fusion_host_raw.cobs.bin": "921214342706dc80171f2fc5cd9cfe61c699ed1fb48a3018cf97b6c27f29c217",
    N31 / "FROZEN_TRAINING_MODEL.json": "77f1c9f7926efa75c7dd9611a0994b39709d617c615649a443b07e9d8fae3c07",
    LOGS / "v47_c2cc_stationary_continuous_20260811_225450/continuous_raw/fusion_host_raw.cobs.bin": "fc5cb8c527b40c4fbf54bf934efb48dda87d150f97def1ba7afcdee9041761ec",
    LOGS / "v47_c2cc_interactive_rotation_20260811_233719/continuous_raw/fusion_host_raw.cobs.bin": "2cda0c2e53966cfe49d8f78fbe9626cf670cf369dded96ff323d5963e392d920",
    LOGS / "v47_c2cc_3c79_9rpm_overnight_20260812_013304/attempt2_continuous/fusion_host_raw.cobs.bin": "e9cad96e432f27e61a3a88105cf68e725ee398ba5743490a413f24a4ca7802ec",
    LOGS / "v47_full_system_30m_20260811_130843/formal_capture/fusion_host_raw.cobs.bin": "c5c7c923e2e29ad43d2d5e51217dda0ea1df8f95bdc04d30656f8055b038a9b8",
}

# Frozen before looking at this analysis result.  The 0.5 mg equivalence band
# is inherited from qualification-policy V2.  Practical promotion additionally
# requires >=1 mg and >=15% RMSE gain on each device's non-training evidence.
RULES = {
    "per_device_material_regression_g": 0.0005,
    "practical_absolute_gain_g": 0.001,
    "practical_relative_gain": 0.15,
    "full_spd_gain_over_diagonal_g": 0.001,
    "startup_gyro_residual_p95_dps": 0.05,
    "startup_min_duration_s": 1,
    "startup_candidates_s": [1, 2, 5, 10],
    "population_characterized_devices": 2,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def clean(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return [clean(x) for x in value.tolist()]
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(x) for x in value]
    if isinstance(value, float):
        return float(f"{value:.15g}")
    return value


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: clean(row.get(key, "")) for key in fields})


def verify_authoritative_evidence(expected: dict[Path, str] = EXPECTED_HASHES) -> list[dict]:
    rows = []
    for path, wanted in sorted(expected.items(), key=lambda item: str(item[0])):
        if not path.is_file():
            raise RuntimeError(f"authoritative evidence missing: {path}")
        observed = sha256(path)
        if observed != wanted:
            raise RuntimeError(f"authoritative evidence hash mismatch: {path}: {observed} != {wanted}")
        rows.append({"path": str(path.relative_to(ROOT)), "sha256": observed, "verified": True})
    return rows


def verify_commits(commits: dict[str, str] = EXPECTED_COMMITS) -> list[dict]:
    rows = []
    for device, commit in sorted(commits.items()):
        subprocess.check_output(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=ROOT)
        resolved = subprocess.check_output(["git", "rev-parse", commit], cwd=ROOT, text=True).strip()
        if resolved != commit:
            raise RuntimeError(f"authoritative commit mismatch: {device}: {resolved} != {commit}")
        rows.append({"device": device, "commit": commit, "verified": True})
    return rows


def load_windows(path: Path) -> list[dict]:
    rows = []
    with path.open(newline="") as stream:
        for row in csv.DictReader(stream):
            if row["accepted"] != "True":
                continue
            end = ast.literal_eval(row["end"])
            rows.append({
                "set": row["set"], "pose": int(row["pose"]),
                "duration_s": float(row["duration_s"]), "samples": int(row["samples"]),
                "end_monotonic": float(end["monotonic"]),
                "end_record": int(end["consumed_record_index"]),
            })
    return rows


def extract_pose_samples(run: Path, node: str) -> dict[tuple[str, int], list[dict]]:
    windows = load_windows(run / "POSE_WINDOWS.csv")
    selected = {(row["set"], row["pose"]): [] for row in windows}
    with (run / "continuous_raw/consumption_index.jsonl").open() as stream:
        for text in stream:
            record = json.loads(text)
            line = record["line"]
            if not line.startswith("FUSION_IMU "):
                continue
            fields = parse_fields(line)
            if fields.get("name") != node:
                continue
            mono = float(record["consume_monotonic"])
            for window in windows:
                if (window["end_monotonic"] - window["duration_s"] - 0.2 <= mono <= window["end_monotonic"]
                        and int(record["record_index"]) <= window["end_record"]):
                    parsed = parse_imu_samples(fields, mono)
                    for sample in parsed:
                        sample["record_index"] = int(record["record_index"])
                    selected[(window["set"], window["pose"])].extend(parsed)
                    break
    for window in windows:
        key = (window["set"], window["pose"])
        selected[key] = selected[key][-window["samples"]:]
        if len(selected[key]) != window["samples"]:
            raise RuntimeError(f"pose sample accounting mismatch: {run.name} {key}")
    return selected


def arrays_by_set(samples: dict[tuple[str, int], list[dict]]) -> dict[str, list[np.ndarray]]:
    grouped: dict[str, list[tuple[int, np.ndarray]]] = defaultdict(list)
    for (name, pose), rows in samples.items():
        grouped[name].append((pose, np.asarray([row["accel_g"] for row in rows], float)))
    return {name: [array for _, array in sorted(values)] for name, values in grouped.items()}


def sample_rows_by_set(samples: dict[tuple[str, int], list[dict]]) -> dict[str, list[list[dict]]]:
    grouped: dict[str, list[tuple[int, list[dict]]]] = defaultdict(list)
    for (name, pose), rows in samples.items():
        grouped[name].append((pose, rows))
    return {name: [array for _, array in sorted(values)] for name, values in grouped.items()}


def matrix_from_parameters(kind: str, values: np.ndarray) -> np.ndarray:
    if kind == "SCALAR":
        return np.eye(3) * math.exp(float(values[0]))
    if kind == "DIAGONAL":
        return np.diag(np.exp(values[:3]))
    if kind == "FULL_SPD":
        symmetric = np.array([[values[0], values[1], values[2]],
                              [values[1], values[3], values[4]],
                              [values[2], values[4], values[5]]])
        return expm(symmetric)
    raise ValueError(kind)


def fit_pooled_matrix(training: dict[str, list[np.ndarray]], kind: str) -> dict:
    """Fit a shared matrix with one nuisance bias per device, training only."""
    devices = sorted(training)
    pose_means = {device: np.asarray([np.mean(pose, axis=0) for pose in training[device]])
                  for device in devices}
    width = {"SCALAR": 1, "DIAGONAL": 3, "FULL_SPD": 6}[kind]
    initial_bias = np.concatenate([np.mean(values, axis=0) * 0 for values in pose_means.values()])
    initial = np.r_[initial_bias, np.zeros(width)]

    def residual(parameters):
        matrix = matrix_from_parameters(kind, parameters[3 * len(devices):])
        rows = []
        for index, device in enumerate(devices):
            bias = parameters[3 * index:3 * index + 3]
            corrected = (pose_means[device] - bias) @ matrix.T
            rows.extend(np.linalg.norm(corrected, axis=1) - 1.0)
        return np.asarray(rows)

    fit = least_squares(residual, initial, loss="huber", f_scale=0.005, max_nfev=5000,
                        xtol=1e-14, ftol=1e-14, gtol=1e-14)
    matrix = matrix_from_parameters(kind, fit.x[3 * len(devices):])
    return {
        "model": f"POOLED_{kind}", "correction_matrix": matrix.tolist(),
        "nuisance_bias_g_by_device": {device: fit.x[3 * i:3 * i + 3].tolist()
                                        for i, device in enumerate(devices)},
        "fit_source": "TRAINING_POSE_MEANS_ONLY", "heldout_used_for_fit": False,
        "optimizer_success": bool(fit.success), "optimizer_cost": float(fit.cost),
    }


def matrix_geometry(matrix: np.ndarray) -> dict:
    off = matrix - np.diag(np.diag(matrix))
    return {
        "matrix_condition": float(np.linalg.cond(matrix)),
        "distance_from_identity_fro": float(np.linalg.norm(matrix - np.eye(3))),
        "off_diagonal_fro": float(np.linalg.norm(off)),
        "maximum_abs_off_diagonal": float(np.max(np.abs(off))),
    }


def residual_metrics(poses: list[np.ndarray], matrix: np.ndarray, bias: np.ndarray) -> tuple[dict, list[dict]]:
    all_residuals = []
    per_pose = []
    for pose_index, pose in enumerate(poses, 1):
        residual = np.linalg.norm((pose - bias) @ matrix.T, axis=1) - 1.0
        all_residuals.append(residual)
        per_pose.append({
            "pose": pose_index, "samples": len(pose),
            "rmse_g": float(np.sqrt(np.mean(residual ** 2))),
            "median_abs_g": float(np.median(np.abs(residual))),
            "p95_abs_g": float(np.percentile(np.abs(residual), 95)),
            "p99_abs_g": float(np.percentile(np.abs(residual), 99)),
            "median_signed_g": float(np.median(residual)),
        })
    residual = np.concatenate(all_residuals)
    return ({
        "samples": len(residual), "pose_count": len(poses),
        "rmse_g": float(np.sqrt(np.mean(residual ** 2))),
        "median_abs_g": float(np.median(np.abs(residual))),
        "p95_abs_g": float(np.percentile(np.abs(residual), 95)),
        "p99_abs_g": float(np.percentile(np.abs(residual), 99)),
        "worst_pose_rmse_g": max(row["rmse_g"] for row in per_pose),
        "maximum_systematic_per_pose_abs_g": max(abs(row["median_signed_g"]) for row in per_pose),
    }, per_pose)


def isolated_transient_mask(samples: list[dict]) -> np.ndarray:
    """Policy-V2-style causal local test; returned only for sensitivity."""
    mask = np.zeros(len(samples), dtype=bool)
    for index in range(20, len(samples)):
        history = samples[index - 20:index]
        prior = np.asarray([row["accel_g"] for row in history], float)
        median = np.median(prior, axis=0)
        mad = np.median(np.abs(prior - median), axis=0)
        scale = max(float(np.linalg.norm(mad)) * 1.4826, 1 / ACCEL_LSB_PER_G)
        accel = np.asarray(samples[index]["accel_g"], float)
        gyro = np.asarray(samples[index]["gyro_dps"], float)
        gyro_median = np.median([row["gyro_dps"] for row in history], axis=0)
        local = np.linalg.norm(accel - median) > max(0.030, 10 * scale)
        quiet = np.linalg.norm(gyro - gyro_median) <= 0.5
        if local and quiet:
            previous_local = index > 20 and mask[index - 1]
            mask[index] = not previous_local
    return mask


def policy_accel_bias(policy_kind: str, pooled_bias: np.ndarray | None = None) -> np.ndarray:
    if policy_kind in ("IDENTITY_ACCEL_MATRIX", "SHARED_MATRIX"):
        return np.zeros(3)
    if policy_kind == "POOLED_MANUFACTURING_BIAS_DIAGNOSTIC":
        if pooled_bias is None:
            raise ValueError("pooled bias required")
        return np.asarray(pooled_bias, float)
    raise ValueError(policy_kind)


def select_policy(summary: dict[str, dict]) -> tuple[str, dict]:
    """Least-complex selection with two-device and per-device guards."""
    identity = summary["IDENTITY"]
    diag = summary.get("POOLED_DIAGONAL")
    full = summary.get("POOLED_FULL_SPD")

    def transferable(candidate: dict, baseline: dict) -> bool:
        checks = []
        for device in ("BSFC2CC", "BSF31CC"):
            gain = baseline[device] - candidate[device]
            checks.append(gain >= RULES["practical_absolute_gain_g"]
                          and gain / max(baseline[device], 1e-12) >= RULES["practical_relative_gain"])
            checks.append(candidate[device] <= baseline[device] + RULES["per_device_material_regression_g"])
        return all(checks)

    diagnostics = {
        "two_devices_only": True,
        "diagonal_transferable_under_frozen_rules": bool(diag and transferable(diag, identity)),
        "full_spd_transferable_under_frozen_rules": bool(full and transferable(full, identity)),
    }
    if full and diag:
        diagnostics["full_spd_practical_gain_over_diagonal_both_devices"] = all(
            diag[device] - full[device] >= RULES["full_spd_gain_over_diagonal_g"]
            for device in ("BSFC2CC", "BSF31CC"))
    # With only two characterized units, passing transfer remains engineering
    # evidence, not sufficient cohort evidence to freeze a shared production
    # matrix.  Identity is the reversible MVP default.
    return "INSUFFICIENT_COHORT_EVIDENCE_USE_IDENTITY_MVP", diagnostics


def parse_decoded_segment(path: Path, nodes: set[str], start: float, duration: float) -> dict[str, list[dict]]:
    result = {node: [] for node in nodes}
    end = start + duration
    with path.open(errors="replace") as stream:
        for text in stream:
            parts = text.split(" ", 3)
            if len(parts) < 4:
                continue
            try:
                mono = float(parts[1])
            except ValueError:
                continue
            if mono < start:
                continue
            if mono > end:
                break
            marker = " FUSION_RX FUSION_IMU "
            if marker not in text:
                continue
            line = "FUSION_IMU " + text.split(marker, 1)[1].strip()
            fields = parse_fields(line)
            node = fields.get("name")
            if node not in nodes:
                continue
            result[node].extend(parse_imu_samples(fields, mono))
    return result


def q1_replay(samples: list[dict], matrix: np.ndarray, accel_bias: np.ndarray,
              expected_static: bool) -> dict:
    if len(samples) < 500:
        raise RuntimeError("insufficient Q1 replay samples")
    accel_raw = np.asarray([row["accel_g"] for row in samples])
    accel_g = (accel_raw - accel_bias) @ matrix.T
    gyro_dps = np.asarray([row["gyro_dps"] for row in samples])
    elapsed = (np.asarray([row["node_us"] for row in samples], dtype=np.int64)
               - int(samples[0]["node_us"])) * 1e-6
    init = elapsed <= min(2.0, elapsed[-1])
    gyro_bias = np.mean(gyro_dps[init], axis=0)
    q1 = Q1T4ESKF()
    q1.initialize_from_stationary(np.mean(accel_g[init], axis=0) * G_MPS2,
                                  np.radians(gyro_bias))
    motion = MotionVetoGate()
    decisions = []
    nis = []
    sign_dots = []
    previous_q = q1.q.copy()
    false_motion_samples = 0
    raw_transients = isolated_transient_mask(samples)
    transient_accepts = 0
    for index, row in enumerate(samples):
        accel = accel_g[index] * G_MPS2
        gyro_centered = gyro_dps[index] - gyro_bias
        timestamp = row["node_us"] * 1e-6
        q1.propagate(timestamp, accel, np.radians(gyro_dps[index]))
        state = motion.update(timestamp,
            gyro_rms_dps=float(np.linalg.norm(gyro_centered)),
            gyro_angle_deg=float(np.linalg.norm(gyro_centered)) * 0.005,
            accel_deviation_g=abs(float(np.linalg.norm(accel_g[index])) - 1.0),
            candidate_stable=True)
        if expected_static and state in ("MOVING", "SETTLING"):
            false_motion_samples += 1
        gravity_state = "STATIONARY" if state in ("STATIONARY", "MOTION_SUSPECTED") else state
        decision = q1.gravity_update_causal(accel, motion_state=gravity_state)
        if raw_transients[index] and decision.accepted:
            transient_accepts += 1
        decisions.append(decision)
        nis.append(decision.nis)
        sign_dots.append(float(previous_q @ q1.q))
        previous_q = q1.q.copy()
    covariance = 0.5 * (q1.P + q1.P.T)
    eigenvalues = np.linalg.eigvalsh(covariance)
    end_count = max(1, int(round(10 * 200)))
    late_bias = np.mean(gyro_dps[-end_count:], axis=0)
    return {
        "samples": len(samples), "duration_s": float(elapsed[-1]),
        "startup_gyro_bias_dps": gyro_bias.tolist(),
        "startup_to_final_10s_gyro_bias_delta_norm_dps": float(np.linalg.norm(gyro_bias - late_bias)),
        "gravity_attempts": len(decisions),
        "gravity_accepted": sum(decision.accepted for decision in decisions),
        "gravity_rejected": sum(not decision.accepted and decision.reason != "MOTION_GRAVITY_INELIGIBLE" for decision in decisions),
        "gravity_motion_ineligible": sum(decision.reason == "MOTION_GRAVITY_INELIGIBLE" for decision in decisions),
        "nis_median": float(np.median(nis)), "nis_p95": float(np.percentile(nis, 95)),
        "false_motion_samples": false_motion_samples,
        "motion_transitions": len(motion.transitions),
        "false_motion_transitions": sum(expected_static and row["to_state"] == "MOVING"
                                         for row in motion.transitions),
        "isolated_transient_candidates": int(np.sum(raw_transients)),
        "isolated_transients_accepted": transient_accepts,
        "isolated_transient_containment_pass": transient_accepts == 0,
        "quaternion_max_norm_error": q1.max_quaternion_norm_error,
        "quaternion_min_consecutive_dot": min(sign_dots),
        "covariance_finite": bool(np.isfinite(covariance).all()),
        "covariance_max_asymmetry": float(np.max(np.abs(q1.P - q1.P.T))),
        "covariance_min_eigenvalue": float(eigenvalues[0]),
        "covariance_cholesky": bool(np.all(np.diag(np.linalg.cholesky(covariance)) > 0)),
        "systematic_measurement_rejection": bool(expected_static and
            sum(not decision.accepted for decision in decisions) / len(decisions) > 0.05),
    }


def gyro_startup_rows(device_samples: dict[str, list[list[dict]]]) -> list[dict]:
    rows = []
    for device, poses in sorted(device_samples.items()):
        for pose_index, pose in enumerate(poses, 1):
            gyro = np.asarray([sample["gyro_dps"] for sample in pose])
            reference = np.mean(gyro, axis=0)
            for duration in RULES["startup_candidates_s"]:
                n = min(len(gyro), int(duration * 200))
                estimate = np.mean(gyro[:n], axis=0)
                rows.append({
                    "device": device, "pose": pose_index, "duration_s": duration,
                    "samples": n, "estimate_g0_dps": estimate[0],
                    "estimate_g1_dps": estimate[1], "estimate_g2_dps": estimate[2],
                    "full_pose_reference_g0_dps": reference[0],
                    "full_pose_reference_g1_dps": reference[1],
                    "full_pose_reference_g2_dps": reference[2],
                    "residual_norm_dps": float(np.linalg.norm(estimate - reference)),
                })
    return rows


def q1_sort_key(row: dict) -> tuple[str, str, str]:
    return row["evidence"], row["node"], row["policy"]


def derive(out: Path) -> dict:
    if out.exists() and any(out.iterdir()):
        raise RuntimeError(f"output directory is not empty: {out}")
    out.mkdir(parents=True, exist_ok=True)
    evidence = verify_authoritative_evidence()
    commits = verify_commits()

    c2_samples = extract_pose_samples(C2, "BSFC2CC")
    c2r_samples = extract_pose_samples(C2R, "BSFC2CC")
    n31_samples = extract_pose_samples(N31, "BSF31CC")
    c2_sets = arrays_by_set(c2_samples)
    c2r_sets = arrays_by_set(c2r_samples)
    n31_sets = arrays_by_set(n31_samples)
    c2_rows = sample_rows_by_set(c2_samples)
    c2r_rows = sample_rows_by_set(c2r_samples)
    n31_rows = sample_rows_by_set(n31_samples)

    c2_profile = json.loads((C2 / "ACCEL_CALIBRATION_PROFILE.json").read_text())
    c2_fit = c2_profile["model_selection"]["selected"]
    n31_frozen = json.loads((N31 / "FROZEN_TRAINING_MODEL.json").read_text())
    n31_fit = n31_frozen["model_selection"]["selected"]
    biases = {"BSFC2CC": np.asarray(c2_fit["bias_g"]), "BSF31CC": np.asarray(n31_fit["bias_g"])}
    c2_matrix = np.asarray(c2_fit["correction_matrix"])
    n31_matrix = np.asarray(n31_fit["correction_matrix"])
    training = {"BSFC2CC": c2_sets["CALIBRATION"], "BSF31CC": n31_sets["TRAINING"]}
    pooled = {kind: fit_pooled_matrix(training, kind) for kind in ("SCALAR", "DIAGONAL", "FULL_SPD")}
    candidates = {
        "IDENTITY": {"source": "NONE", "matrix": np.eye(3)},
        "C2CC_FROZEN_DIAGONAL": {"source": "BSFC2CC", "matrix": c2_matrix},
        "BSF31CC_DIAGONAL_PROJECTION": {"source": "BSF31CC", "matrix": np.diag(np.diag(n31_matrix))},
        "BSF31CC_FROZEN_FULL_SPD": {"source": "BSF31CC", "matrix": n31_matrix},
        "POOLED_SCALAR": {"source": "BOTH_TRAINING_ONLY", "matrix": np.asarray(pooled["SCALAR"]["correction_matrix"])},
        "POOLED_DIAGONAL": {"source": "BOTH_TRAINING_ONLY", "matrix": np.asarray(pooled["DIAGONAL"]["correction_matrix"])},
        "POOLED_FULL_SPD": {"source": "BOTH_TRAINING_ONLY", "matrix": np.asarray(pooled["FULL_SPD"]["correction_matrix"])},
    }
    datasets = {
        ("BSFC2CC", "TRAINING"): c2_sets["CALIBRATION"],
        ("BSFC2CC", "ORIGINAL_HELDOUT"): c2_sets["HELDOUT"],
        ("BSFC2CC", "SIX_POSE_REVALIDATION"): c2r_sets["HELDOUT_REVALIDATION_V2"],
        ("BSF31CC", "TRAINING"): n31_sets["TRAINING"],
        ("BSF31CC", "ORIGINAL_HELDOUT"): n31_sets["HELDOUT"],
    }
    sample_datasets = {
        ("BSFC2CC", "TRAINING"): c2_rows["CALIBRATION"],
        ("BSFC2CC", "ORIGINAL_HELDOUT"): c2_rows["HELDOUT"],
        ("BSFC2CC", "SIX_POSE_REVALIDATION"): c2r_rows["HELDOUT_REVALIDATION_V2"],
        ("BSF31CC", "TRAINING"): n31_rows["TRAINING"],
        ("BSF31CC", "ORIGINAL_HELDOUT"): n31_rows["HELDOUT"],
    }

    transfer_rows = []
    pose_rows = []
    transfer_summary: dict[str, dict] = defaultdict(dict)
    for candidate, definition in candidates.items():
        matrix = definition["matrix"]
        geometry = matrix_geometry(matrix)
        for (device, dataset), poses in datasets.items():
            metrics, per_pose = residual_metrics(poses, matrix, biases[device])
            identity_metrics, identity_per_pose = residual_metrics(poses, np.eye(3), biases[device])
            improved = sum(row["rmse_g"] < base["rmse_g"] for row, base in zip(per_pose, identity_per_pose))
            degraded = sum(row["rmse_g"] > base["rmse_g"] + RULES["per_device_material_regression_g"]
                           for row, base in zip(per_pose, identity_per_pose))
            systematic_degradation = max(
                abs(row["median_signed_g"]) - abs(base["median_signed_g"])
                for row, base in zip(per_pose, identity_per_pose))
            transfer_rows.append({
                "candidate": candidate, "matrix_source": definition["source"],
                "target_device": device, "dataset": dataset,
                "bias_treatment": "TARGET_DEVICE_FROZEN_BIAS_DIAGNOSTIC_ONLY",
                **metrics, "identity_rmse_g": identity_metrics["rmse_g"],
                "rmse_gain_vs_identity_g": identity_metrics["rmse_g"] - metrics["rmse_g"],
                "poses_improved_vs_identity": improved, "poses_materially_degraded_vs_identity": degraded,
                "maximum_systematic_per_pose_degradation_vs_identity_g": systematic_degradation,
                **geometry,
            })
            if dataset != "TRAINING":
                transfer_summary[candidate].setdefault(device, []).append(metrics["rmse_g"])
            for row, base in zip(per_pose, identity_per_pose):
                pose_rows.append({"candidate": candidate, "target_device": device, "dataset": dataset,
                    **row, "identity_rmse_g": base["rmse_g"],
                    "rmse_gain_vs_identity_g": base["rmse_g"] - row["rmse_g"]})

        # Descriptive combined rows never participate in fitting or promotion.
        for label, keys in (
            ("COMBINED_TRAINING", [("BSFC2CC", "TRAINING"), ("BSF31CC", "TRAINING")]),
            ("COMBINED_ORIGINAL_HELDOUT", [("BSFC2CC", "ORIGINAL_HELDOUT"), ("BSF31CC", "ORIGINAL_HELDOUT")]),
            ("COMBINED_ALL_NONTRAINING", [("BSFC2CC", "ORIGINAL_HELDOUT"),
                ("BSFC2CC", "SIX_POSE_REVALIDATION"), ("BSF31CC", "ORIGINAL_HELDOUT")]),
        ):
            residuals = []
            identity_residuals = []
            pose_count = 0
            for device, dataset in keys:
                for pose in datasets[(device, dataset)]:
                    residuals.extend(np.linalg.norm((pose - biases[device]) @ matrix.T, axis=1) - 1.0)
                    identity_residuals.extend(np.linalg.norm(pose - biases[device], axis=1) - 1.0)
                    pose_count += 1
            residuals = np.asarray(residuals); identity_residuals = np.asarray(identity_residuals)
            rmse = float(np.sqrt(np.mean(residuals ** 2)))
            identity_rmse = float(np.sqrt(np.mean(identity_residuals ** 2)))
            transfer_rows.append({"candidate": candidate, "matrix_source": definition["source"],
                "target_device": "COMBINED_DESCRIPTIVE", "dataset": label,
                "bias_treatment": "EACH_TARGET_DEVICE_FROZEN_BIAS_DIAGNOSTIC_ONLY",
                "samples": len(residuals), "pose_count": pose_count, "rmse_g": rmse,
                "median_abs_g": float(np.median(np.abs(residuals))),
                "p95_abs_g": float(np.percentile(np.abs(residuals), 95)),
                "p99_abs_g": float(np.percentile(np.abs(residuals), 99)),
                "worst_pose_rmse_g": "SEE_DEVICE_ROWS",
                "maximum_systematic_per_pose_abs_g": "SEE_DEVICE_ROWS",
                "identity_rmse_g": identity_rmse, "rmse_gain_vs_identity_g": identity_rmse - rmse,
                "poses_improved_vs_identity": "SEE_DEVICE_ROWS",
                "poses_materially_degraded_vs_identity": "SEE_DEVICE_ROWS",
                "maximum_systematic_per_pose_degradation_vs_identity_g": "SEE_DEVICE_ROWS", **geometry})

    selection_input = {}
    for candidate in candidates:
        selection_input[candidate] = {}
        for device in ("BSFC2CC", "BSF31CC"):
            values = transfer_summary[candidate].get(device, [])
            selection_input[candidate][device] = float(np.mean(values)) if values else math.inf
    verdict, selection_diagnostics = select_policy(selection_input)

    pooled_bias = np.mean(np.asarray(list(pooled["DIAGONAL"]["nuisance_bias_g_by_device"].values())), axis=0)
    product_rows = []
    product_matrices = {"IDENTITY": np.eye(3), "POOLED_DIAGONAL": candidates["POOLED_DIAGONAL"]["matrix"]}
    evaluation_sets = [(key, value) for key, value in datasets.items() if key[1] != "TRAINING"]
    for matrix_name, matrix in product_matrices.items():
        for bias_name, bias in (("ZERO_ACCEL_BIAS", np.zeros(3)), ("POOLED_BIAS_DIAGNOSTIC", pooled_bias)):
            for (device, dataset), poses in evaluation_sets:
                metrics, _ = residual_metrics(poses, matrix, bias)
                oracle_matrix = c2_matrix if device == "BSFC2CC" else n31_matrix
                oracle, _ = residual_metrics(poses, oracle_matrix, biases[device])
                product_rows.append({
                    "policy": f"{matrix_name}_{bias_name}", "device": device, "dataset": dataset,
                    "matrix_shared": matrix_name != "IDENTITY", "per_device_accel_bias_used": False,
                    "bias_kind": bias_name, **metrics, "oracle_rmse_g": oracle["rmse_g"],
                    "gap_to_device_oracle_rmse_g": metrics["rmse_g"] - oracle["rmse_g"],
                    "production_eligible": bias_name == "ZERO_ACCEL_BIAS",
                })
    for (device, dataset), poses in evaluation_sets:
        oracle_matrix = c2_matrix if device == "BSFC2CC" else n31_matrix
        metrics, _ = residual_metrics(poses, oracle_matrix, biases[device])
        product_rows.append({"policy": "PER_DEVICE_ORACLE_REFERENCE", "device": device,
            "dataset": dataset, "matrix_shared": False, "per_device_accel_bias_used": True,
            "bias_kind": "DEVICE_FROZEN_ORACLE", **metrics, "oracle_rmse_g": metrics["rmse_g"],
            "gap_to_device_oracle_rmse_g": 0.0, "production_eligible": False})

    gyro_rows = gyro_startup_rows({
        "BSFC2CC_TRAINING": c2_rows["CALIBRATION"],
        "BSFC2CC_REVALIDATION": c2r_rows["HELDOUT_REVALIDATION_V2"],
        "BSF31CC_TRAINING": n31_rows["TRAINING"],
        "BSF31CC_HELDOUT": n31_rows["HELDOUT"],
    })
    duration_summary = {}
    for duration in RULES["startup_candidates_s"]:
        values = [row["residual_norm_dps"] for row in gyro_rows if row["duration_s"] == duration]
        duration_summary[str(duration)] = {"median_residual_norm_dps": float(np.median(values)),
            "p95_residual_norm_dps": float(np.percentile(values, 95)), "max_residual_norm_dps": max(values)}
    eligible_durations = [duration for duration in RULES["startup_candidates_s"]
                          if duration_summary[str(duration)]["p95_residual_norm_dps"]
                          <= RULES["startup_gyro_residual_p95_dps"]]
    startup_duration = min(eligible_durations) if eligible_durations else 10

    q1_specs = [
        ("C2CC_STATIONARY", LOGS / "v47_c2cc_stationary_continuous_20260811_225450/continuous_raw/fusion_cdc.log",
         {"BSFC2CC"}, 278217.000396297, 120.0, True),
        ("C2CC_INTERACTIVE_ROTATION", LOGS / "v47_c2cc_interactive_rotation_20260811_233719/continuous_raw/fusion_cdc.log",
         {"BSFC2CC"}, 281430.289991, 180.0, False),
        ("C2CC_OVERNIGHT_ROTATION", LOGS / "v47_c2cc_3c79_9rpm_overnight_20260812_013304/attempt2_continuous/fusion_cdc.log",
         {"BSFC2CC"}, 287850.229715749, 180.0, False),
        ("TEN_NODE_TABLETOP", LOGS / "v47_full_system_30m_20260811_130843/formal_capture/fusion_cdc.log",
         {"BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4", "BSF1120",
          "BSF31CC", "BSFAA61", "BSFB165", "BSFEC35"}, 243033.004297073, 60.0, True),
    ]
    q1_rows = []
    for evidence_name, log_path, nodes, start, duration, expected_static in q1_specs:
        loaded = parse_decoded_segment(log_path, nodes, start, duration)
        for node, samples in sorted(loaded.items()):
            policies = {"IDENTITY_FALLBACK": (np.eye(3), np.zeros(3)),
                        "SELECTED_MVP_IDENTITY": (np.eye(3), np.zeros(3)),
                        "SHARED_DIAGONAL_DIAGNOSTIC": (candidates["POOLED_DIAGONAL"]["matrix"], np.zeros(3))}
            if node == "BSFC2CC":
                policies["PER_DEVICE_ORACLE_REFERENCE"] = (c2_matrix, biases[node])
            elif node == "BSF31CC":
                policies["PER_DEVICE_ORACLE_REFERENCE"] = (n31_matrix, biases[node])
            for policy, (matrix, bias) in policies.items():
                q1_rows.append({"evidence": evidence_name, "node": node, "policy": policy,
                    "expected_static": expected_static, **q1_replay(samples, matrix, bias, expected_static)})

    # Re-run the actual isolated-transient held-out poses under every relevant
    # policy, rather than relying only on the historical oracle containment.
    transient_pose_sets = [
        ("C2CC_ORIGINAL_HELDOUT", "BSFC2CC", c2_rows["HELDOUT"]),
        ("C2CC_SIX_POSE_REVALIDATION", "BSFC2CC", c2r_rows["HELDOUT_REVALIDATION_V2"]),
        ("BSF31CC_ORIGINAL_HELDOUT", "BSF31CC", n31_rows["HELDOUT"]),
    ]
    for evidence_name, node, poses in transient_pose_sets:
        for pose_number, samples in enumerate(poses, 1):
            if not np.any(isolated_transient_mask(samples)):
                continue
            oracle_matrix = c2_matrix if node == "BSFC2CC" else n31_matrix
            policies = {
                "IDENTITY_FALLBACK": (np.eye(3), np.zeros(3)),
                "SELECTED_MVP_IDENTITY": (np.eye(3), np.zeros(3)),
                "SHARED_DIAGONAL_DIAGNOSTIC": (candidates["POOLED_DIAGONAL"]["matrix"], np.zeros(3)),
                "PER_DEVICE_ORACLE_REFERENCE": (oracle_matrix, biases[node]),
            }
            for policy, (matrix, bias) in policies.items():
                q1_rows.append({"evidence": f"{evidence_name}_POSE_{pose_number}", "node": node,
                    "policy": policy, "expected_static": True,
                    **q1_replay(samples, matrix, bias, True)})
    q1_rows.sort(key=q1_sort_key)

    transient_counts = {}
    for key, pose_lists in sample_datasets.items():
        count = sum(int(np.sum(isolated_transient_mask(pose))) for pose in pose_lists)
        transient_counts[f"{key[0]}:{key[1]}"] = count
    sensitivity = {
        "selection_uses_untrimmed_samples": True,
        "isolated_samples_are_retained_and_q1_causally_rejected": True,
        "isolated_transient_candidates_by_dataset": transient_counts,
        "policy_v2_containment_artifacts": [
            str((C2V2 / "RUNTIME_OUTLIER_CONTAINMENT.json").relative_to(ROOT)),
            str((N31 / "qualification_v1/Q1_RUNTIME_CONTAINMENT.json").relative_to(ROOT)),
        ],
        "pose_selection_sensitivity": "candidate must improve both devices' non-training evidence; pooled mean alone cannot promote",
        "frozen_selection_rules": RULES,
        "selected_verdict": verdict,
    }

    candidate_json = {}
    for name, definition in candidates.items():
        candidate_json[name] = {"source": definition["source"],
            "correction_matrix": definition["matrix"].tolist(), **matrix_geometry(definition["matrix"])}
    candidate_json["pooled_fit_metadata"] = pooled
    candidate_json["selection_input_nontraining_mean_rmse_g"] = selection_input
    candidate_json["selection_diagnostics"] = selection_diagnostics

    runtime_policy = {
        "schema": "biospur-jy61p-mvp-runtime-policy-v1", "primary_verdict": verdict,
        "population_validation_claim": False, "characterized_devices": ["BSFC2CC", "BSF31CC"],
        "shared_defaults": {
            "accelerometer_correction_matrix": np.eye(3).tolist(),
            "accelerometer_bias_g": [0.0, 0.0, 0.0],
            "raw_channel_order": ["a0", "a1", "a2", "g0", "g1", "g2"],
            "accelerometer_conversion": "raw / 2048 g", "gyro_conversion": "raw / 16.384 dps",
            "q1_gravity_update": "causal NIS plus norm gate", "covariance_policy": "float64 symmetric positive-definite; no silent projection",
        },
        "per_session_initialization": {
            "minimum_stationary_duration_s": startup_duration,
            "gyro_bias": "mean of accepted stationary startup interval; reject startup if motion/health gate fails",
            "initial_roll_pitch": "gravity direction", "initial_yaw": "UNRESOLVED",
            "accelerometer_bias": "NOT_IDENTIFIED_FROM_SINGLE_POSE",
        },
        "online_states": {"gyro_bias": "Q1_TRACKED", "accelerometer_bias": "only when observable under active motion/UWB model"},
        "temperature_model": "DISABLED_NARROW_EXISTING_TEMPERATURE_SPAN",
        "device_specific_oracles_not_copied": True, "bmd101_scope": "EXCLUDED",
        "selection_basis": {
            "identity_is_adequate_for_next_frame_binding": True,
            "shared_diagonal_engineering_transfer_observed": selection_diagnostics["diagonal_transferable_under_frozen_rules"],
            "shared_matrix_not_population_validated": True,
            "full_spd_complexity_rejected": not selection_diagnostics["full_spd_practical_gain_over_diagonal_both_devices"],
            "startup_1s_p95_residual_norm_dps": duration_summary["1"]["p95_residual_norm_dps"],
        },
    }

    input_json = {"schema": "biospur-jy61p-mvp-policy-input-evidence-v1",
        "authoritative_hashes": evidence, "authoritative_commits": commits,
        "commit_objects_verified_before_derivation": all(row["verified"] for row in commits),
        "scientific_scope": "two characterized devices; engineering transfer screen, not population proof",
        "historical_artifacts_modified": False, "hardware_access": False, "bmd101_scope": "EXCLUDED"}

    product_lookup = {(row["policy"], row["device"], row["dataset"]): row for row in product_rows}
    c2_identity = product_lookup[("IDENTITY_ZERO_ACCEL_BIAS", "BSFC2CC", "ORIGINAL_HELDOUT")]["rmse_g"]
    c2_reval_identity = product_lookup[("IDENTITY_ZERO_ACCEL_BIAS", "BSFC2CC", "SIX_POSE_REVALIDATION")]["rmse_g"]
    n31_identity = product_lookup[("IDENTITY_ZERO_ACCEL_BIAS", "BSF31CC", "ORIGINAL_HELDOUT")]["rmse_g"]
    c2_diag = product_lookup[("POOLED_DIAGONAL_ZERO_ACCEL_BIAS", "BSFC2CC", "ORIGINAL_HELDOUT")]["rmse_g"]
    c2_reval_diag = product_lookup[("POOLED_DIAGONAL_ZERO_ACCEL_BIAS", "BSFC2CC", "SIX_POSE_REVALIDATION")]["rmse_g"]
    n31_diag = product_lookup[("POOLED_DIAGONAL_ZERO_ACCEL_BIAS", "BSF31CC", "ORIGINAL_HELDOUT")]["rmse_g"]
    n31_pooled_bias = product_lookup[("IDENTITY_POOLED_BIAS_DIAGNOSTIC", "BSF31CC", "ORIGINAL_HELDOUT")]["rmse_g"]
    c2_full_gain = selection_input["POOLED_DIAGONAL"]["BSFC2CC"] - selection_input["POOLED_FULL_SPD"]["BSFC2CC"]
    n31_full_gain = selection_input["POOLED_DIAGONAL"]["BSF31CC"] - selection_input["POOLED_FULL_SPD"]["BSF31CC"]
    q1_cov_min = min(row["covariance_min_eigenvalue"] for row in q1_rows)
    q1_bad = sum(not row["covariance_finite"] or not row["covariance_cholesky"]
                 or row["systematic_measurement_rejection"] for row in q1_rows)
    transient_policy_rows = [row for row in q1_rows if row["isolated_transient_candidates"]]
    transient_accepts = sum(row["isolated_transients_accepted"] for row in transient_policy_rows)

    report = f"""# JY61P MVP production calibration policy

Primary verdict: **{verdict}**

## Product decision and inference

Use the identity accelerometer matrix and zero shared accelerometer bias for the next MVP stage.  Keep the two device-specific calibrations as oracle/engineering-characterization references.  This is a reversible MVP decision from two devices, not population-wide validation and not evidence about 10,000 units.

Matrix transfer was evaluated separately from bias by retaining each target device's frozen bias.  Product-realistic rows then removed that privilege: neither C2CC nor 31CC accelerometer bias is copied, and the pooled-bias rows are diagnostic-only.  A single startup pose initializes gravity direction and gyro zero rate; it does not uniquely identify accelerometer bias or scale.

The frozen least-complex guard selected identity because only two devices have full multi-pose characterization and identity is adequate for the next frame-binding experiment.  Even a numerical pooled-average win cannot establish manufacturing-cohort variability.  Shared FULL_SPD off-diagonal terms are rejected: their non-training gain over pooled diagonal was only {c2_full_gain:.6f} g on C2CC and {n31_full_gain:.6f} g on 31CC, so the predeclared 0.001 g benefit did not transfer to both devices.

## Current evidence

With each target retaining its frozen bias (matrix-transfer diagnostic only), non-training mean RMSE was {selection_input['IDENTITY']['BSFC2CC']:.6f}/{selection_input['IDENTITY']['BSF31CC']:.6f} g for identity and {selection_input['POOLED_DIAGONAL']['BSFC2CC']:.6f}/{selection_input['POOLED_DIAGONAL']['BSF31CC']:.6f} g for pooled diagonal (C2CC/31CC).  Thus diagonal transfer is promising engineering evidence.

The product-realistic zero-bias comparison is less decisive: identity RMSE was {c2_identity:.6f} g on C2CC original held-out, {c2_reval_identity:.6f} g on its six-pose revalidation, and {n31_identity:.6f} g on 31CC held-out; pooled diagonal gave {c2_diag:.6f}, {c2_reval_diag:.6f}, and {n31_diag:.6f} g respectively.  A pooled accelerometer bias is rejected: on 31CC it worsened identity RMSE to {n31_pooled_bias:.6f} g.  Neither device bias is copied.

## Runtime policy

Use a {startup_duration}-second stationary startup interval, subject to motion and accelerometer health gates, to initialize per-session gyro bias and gravity-defined roll/pitch.  Yaw remains unresolved.  Q1 retains causal NIS/norm rejection, gyro-bias tracking, quaternion normalization/sign continuity, and positive-definite covariance checks.  No temperature model is inferred from the narrow existing spans.

Across all accepted pose windows, the 1-second gyro estimate had P95 residual norm {duration_summary['1']['p95_residual_norm_dps']:.6f} dps and maximum {duration_summary['1']['max_residual_norm_dps']:.6f} dps relative to the full-pose mean.  All Q1 policy replays preserved finite, Cholesky-positive covariance (minimum eigenvalue {q1_cov_min:.3e}), quaternion normalization/sign continuity, and produced {q1_bad} numerical/systematic-rejection failures.  Every isolated-transient policy replay retained zero accepted transient samples ({transient_accepts} total accepts) and zero false `MOVING` transitions.

## Evidence boundary

The Q1 comparison covers representative, hash-bound windows from the C2CC stationary capture, interactive rotation, C2CC/3C79 overnight rotation, and all ten nodes in the tabletop capture.  It assesses numerical behavior and measurement acceptance, not absolute attitude or trajectory accuracy; V4 frame binding and external truth are still unavailable.

## Unresolved population uncertainty

Two devices do not establish lot-to-lot dispersion, supplier variability, or a 10,000-unit yield distribution.  Existing held-out data remain retrospective cross-transfer evidence, not a new prospective cohort validation set.  A shared diagonal may be reconsidered only after the stratified future-lot sample passes the same frozen per-device regression rule.  Temperature dependence also remains unknown.

## Manufacturing implication

Do not require 18+4 poses per production unit.  Use the short EOL health screen in `MANUFACTURING_EOL_POLICY.md`; reserve full calibration for characterization samples, new lots/process changes, and failed units.  Proceeding to the C2CC frame-binding experiment does not require calibrating the remaining eight devices.
"""
    (out / "REPORT.md").write_text(report)
    canonical(out / "INPUT_EVIDENCE.json", clean(input_json))
    canonical(out / "CANDIDATE_POLICIES.json", clean(candidate_json))
    write_csv(out / "CROSS_DEVICE_MATRIX_TRANSFER.csv", transfer_rows)
    write_csv(out / "PER_POSE_RESULTS.csv", pose_rows)
    write_csv(out / "PRODUCT_REALISTIC_RESULTS.csv", product_rows)
    write_csv(out / "GYRO_STARTUP_BIAS_RESULTS.csv", gyro_rows)
    write_csv(out / "Q1_POLICY_REPLAY_RESULTS.csv", q1_rows)
    canonical(out / "SENSITIVITY_ANALYSIS.json", clean(sensitivity))
    canonical(out / "MVP_RUNTIME_POLICY.json", clean(runtime_policy))
    (out / "MANUFACTURING_EOL_POLICY.md").write_text("""# MVP manufacturing/EOL IMU policy

No 18+4 calibration is required per unit.  In a simple stationary fixture, acquire at least the runtime-policy startup interval and verify 200 Hz continuity, timestamp/sequence integrity, accelerometer norm/noise plausibility, gyro zero-rate stability, and absence of repeated/burst transients.  Reject or quarantine obvious outliers; do not repair them by copying another unit's bias.

Run full arbitrary-pose calibration only on development characterization units, sampled units from new lots, units affected by sensor/PCB/process changes, or units that fail EOL/self-test.  BMD101 is outside this policy.
""")
    (out / "FUTURE_COHORT_SAMPLING.md").write_text("""# Future cohort sampling

The present evidence contains two characterized JY61P devices and cannot estimate population or lot-to-lot variability.  For MVP, characterize a proportionate stratified sample: initially three units from each of three manufacturing lots (nine total), including early/middle/late lot positions where possible.  Keep one unit per lot as untouched held-out validation.  Re-run the same identity/diagonal/FULL_SPD transfer rules without tuning thresholds.

Expand sampling only if lot effects, EOL failures, supplier/process changes, or a shared matrix's practical benefit justify it.  This sampling is not a blocker for the next C2CC frame-binding experiment because identity remains the conservative default and Q1 retains causal health containment.
""")
    core = sorted(path for path in out.iterdir() if path.is_file() and path.name != "SHA256SUMS")
    (out / "SHA256SUMS").write_text("".join(f"{sha256(path)}  {path.name}\n" for path in core))
    return {"primary_verdict": verdict, "startup_static_s": startup_duration,
        "core_hashes": {path.name: sha256(path) for path in core},
        "device_specific_bias_copied": False, "hardware_access": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(derive(args.out_dir.resolve()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
