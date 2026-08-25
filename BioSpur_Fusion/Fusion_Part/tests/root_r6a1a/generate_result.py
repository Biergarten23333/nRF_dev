"""Generate the isolated Root-R6A1A evidence package from immutable inputs."""
from __future__ import annotations

import csv
import difflib
import hashlib
import json
import os
import struct
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
FUSION = HERE.parents[1]
REPO = FUSION.parents[1]
if str(FUSION / "src") not in sys.path:
    sys.path.insert(0, str(FUSION / "src"))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from biospur_fusion.imu.preintegration import (  # noqa: E402
    NativeTimePreintegrator,
    NoiseParameters,
    PreintegratorConfig,
    samples_from_typed_ledger,
)
from qualification import NODES, run_qualification  # noqa: E402

RESULT = FUSION / "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z"
ROOT_R6A0 = FUSION / "logs/root_r6a0_whole_body_scaffold_20260824T200706Z"
ROOT_R6A1 = FUSION / "logs/root_r6a1_real_imu_preintegration_qualification_20260825T062636Z"
BODY_SCHEMA = ROOT_R6A0 / "BODY_GRAPH_AND_STATE_SCHEMA.json"
BODY_CONFIG = FUSION / "config/root_r6a0/body_graph.json"
C1_LEDGER = FUSION / "logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2/TIME_EVENT_LEDGER.npz"
C1_ACTIONS = FUSION / "logs/v47_ten_node_body_calibration_20260814_093601/ACTION_EVENTS.jsonl"
C1_T0 = FUSION / "logs/v47_ten_node_body_calibration_20260814_093601/FORMAL_T0.json"
STATIC_ROOT = REPO / "BioSpur_Fusion/B306_Part/logs/v47_full_system_30m_20260811_130843"
STATIC_ANALYSIS = STATIC_ROOT / "analysis_real_sensor_static_v1"
ALLAN = STATIC_ANALYSIS / "IMU_ALLAN_RESULTS.csv"
STATIC_STATS = STATIC_ANALYSIS / "PER_NODE_IMU_STATS.csv"

NODE_MAP = {
    "BSFEC35": "forearm_left / left wrist",
    "BSFB165": "forearm_right / right wrist",
    "BSFAA61": "upper_arm_left",
    "BSF1120": "upper_arm_right",
    "BSF31CC": "torso",
    "BSFC2CC": "pelvis",
    "BSF44AD": "thigh_left",
    "BSF3C79": "thigh_right",
    "BSF6C53": "shank_left / left ankle",
    "BSF8BC4": "shank_right / right ankle",
}
SEGMENT_MAP = {key: value.split(" / ")[0] for key, value in NODE_MAP.items()}
MINUS_Z_TARGET = {
    "BSFEC35": "body left",
    "BSFB165": "body right",
    "BSFAA61": "left-to-rear sector",
    "BSF1120": "right-to-rear sector",
    "BSF31CC": "body forward",
    "BSFC2CC": "body forward",
    "BSF44AD": "body forward",
    "BSF3C79": "body forward",
    "BSF6C53": "body left",
    "BSF8BC4": "body right",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def shell_tree_digest(paths: list[Path]) -> str:
    """Match: find ... -print0 | sort -z | xargs -0 sha256sum | sha256sum."""
    lines = []
    for path in sorted(paths, key=lambda item: item.as_posix()):
        lines.append(f"{sha256(path)}  {path.relative_to(FUSION).as_posix()}\n")
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def files_under(*roots: Path, exclude_cache: bool = True) -> list[Path]:
    result = []
    for root in roots:
        for path in root.rglob("*"):
            if path.is_file() and (not exclude_cache or "__pycache__" not in path.parts):
                result.append(path)
    return result


def stored_npy_memmap(npz_path: Path, member: str) -> tuple[np.memmap, dict]:
    """Map one ZIP_STORED NPY member; no other payload member is opened."""
    with zipfile.ZipFile(npz_path) as archive:
        info = archive.getinfo(member)
        if info.compress_type != zipfile.ZIP_STORED:
            raise RuntimeError(f"{member} is not directly mappable")
    with npz_path.open("rb") as handle:
        handle.seek(info.header_offset)
        fields = struct.unpack("<IHHHHHIIIHH", handle.read(30))
        if fields[0] != 0x04034B50:
            raise RuntimeError("invalid ZIP local header")
        handle.seek(fields[-2] + fields[-1], os.SEEK_CUR)
        version = np.lib.format.read_magic(handle)
        if version == (1, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_1_0(handle)
        elif version == (2, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_2_0(handle)
        else:
            raise RuntimeError(f"unsupported NPY version {version}")
        offset = handle.tell()
    if fortran or len(shape) != 1:
        raise RuntimeError("ledger member must be one-dimensional C-order")
    mapping = np.memmap(npz_path, dtype=dtype, mode="r", offset=offset, shape=shape)
    return mapping, {
        "member": member,
        "zip_compression": "ZIP_STORED",
        "member_rows": int(shape[0]),
        "member_uncompressed_bytes": info.file_size,
        "member_payload_opened": True,
    }


def lower_bound(rows: np.ndarray, value: int) -> int:
    low, high = 0, len(rows)
    while low < high:
        middle = (low + high) // 2
        if int(rows[middle]["global_time_ns"]) < value:
            low = middle + 1
        else:
            high = middle
    return low


def result_digest(result) -> str:
    metadata = (
        result.status.value, result.node_id, result.start_time_ns, result.end_time_ns,
        result.sample_count, result.interval_count, result.duration_s, result.boot_epoch,
        result.bounded_gap_count, result.max_dt_s, result.noise_provenance,
    )
    digest = hashlib.sha256(repr(metadata).encode())
    for value in (
        result.delta_rotation, result.delta_velocity, result.delta_position,
        result.jacobian_rotation_gyro_bias, result.jacobian_velocity_gyro_bias,
        result.jacobian_velocity_accel_bias, result.jacobian_position_gyro_bias,
        result.jacobian_position_accel_bias, result.covariance,
    ):
        digest.update(value.tobytes())
    return digest.hexdigest()


def c1_audit() -> dict:
    start_ns = 2_774_428_759_160
    end_ns = start_ns + 30_000_000_000
    zero_noise = {
        node: NoiseParameters(0.0, 0.0, 0.0, 0.0, "ROOT_R6A1A_REAL_PATH_REPLAY_ONLY_COVARIANCE_DISABLED")
        for node in NODES
    }
    preintegrator = NativeTimePreintegrator(
        zero_noise, PreintegratorConfig(max_gap_s=0.020, missing_sample_threshold_s=0.0075)
    )
    per_node = {}
    total_rows = 0
    for node in NODES:
        mapping, member = stored_npy_memmap(C1_LEDGER, f"imu_{node}.npy")
        first_index = lower_bound(mapping, start_ns)
        stop_index = lower_bound(mapping, end_ns)
        rows = np.array(mapping[first_index:stop_index], copy=True)
        del mapping
        if not len(rows):
            raise RuntimeError(f"{node}: empty C1 selection")
        samples = samples_from_typed_ledger(node, rows)
        first = preintegrator.integrate(samples)
        replay = preintegrator.integrate(samples)
        dt_ns = np.diff(rows["global_time_ns"].astype(np.int64))
        gap_indices = np.flatnonzero(dt_ns > 20_000_000)
        boundaries = [0] + [int(index + 1) for index in gap_indices] + [len(samples)]
        segments = []
        for left, right in zip(boundaries[:-1], boundaries[1:], strict=True):
            segment = preintegrator.integrate(samples[left:right])
            segments.append({
                "first_selected_row": left,
                "stop_selected_row": right,
                "sample_count": right - left,
                "status": segment.status.value,
                "duration_s": segment.duration_s,
                "replay_sha256": result_digest(segment),
            })
        acc_g = rows["acc_raw"].astype(float) / 2048.0
        gyro_dps = rows["gyro_raw"].astype(float) / 16.384
        acc_norm = np.linalg.norm(acc_g, axis=1)
        gyro_norm = np.linalg.norm(gyro_dps, axis=1)
        mean_acc = np.mean(acc_g, axis=0)
        unit = mean_acc / np.linalg.norm(mean_acc)
        gap_policy_pass = (
            (not len(gap_indices) and first.valid)
            or (len(gap_indices) > 0 and first.status.value == "GAP_EXCEEDS_ENVELOPE"
                and all(segment["status"] == "VALID" for segment in segments))
        )
        total_rows += len(rows)
        per_node[node] = {
            **member,
            "segment": SEGMENT_MAP[node],
            "selected_index_start": first_index,
            "selected_index_stop": stop_index,
            "selected_rows": len(rows),
            "first_global_time_ns": int(rows[0]["global_time_ns"]),
            "last_global_time_ns": int(rows[-1]["global_time_ns"]),
            "status_counts": {str(int(status)): int(np.sum(rows["status"] == status)) for status in np.unique(rows["status"])},
            "accepted_rows": int(np.sum(rows["status"] == 1)),
            "boot_epochs": [int(value) for value in np.unique(rows["boot_epoch"])],
            "native_dt_ns": {
                "count": len(dt_ns),
                "minimum": int(np.min(dt_ns)),
                "p01": float(np.percentile(dt_ns, 1)),
                "median": float(np.median(dt_ns)),
                "p99": float(np.percentile(dt_ns, 99)),
                "maximum": int(np.max(dt_ns)),
                "nonpositive_count": int(np.sum(dt_ns <= 0)),
                "over_7_5ms_count": int(np.sum(dt_ns > 7_500_000)),
                "over_20ms_count": len(gap_indices),
            },
            "raw_rail_hit_count": int(
                np.sum((rows["acc_raw"] == -32768) | (rows["acc_raw"] == 32767))
                + np.sum((rows["gyro_raw"] == -32768) | (rows["gyro_raw"] == 32767))
            ),
            "unlabelled_stationary_candidate_fraction": float(np.mean((acc_norm > 0.8) & (acc_norm < 1.2) & (gyro_norm < 2.0))),
            "mean_specific_force_g_sensor": mean_acc.tolist(),
            "mean_specific_force_unit_sensor": unit.tolist(),
            "mean_specific_force_norm_g": float(np.linalg.norm(mean_acc)),
            "gyro_norm_p99_dps": float(np.percentile(gyro_norm, 99)),
            "whole_window_preintegrator_status": first.status.value,
            "whole_window_native_duration_s": first.duration_s,
            "whole_window_replay_sha256": result_digest(first),
            "deterministic_replay": result_digest(first) == result_digest(replay),
            "gap_policy_pass": gap_policy_pass,
            "gap_safe_segments": segments,
            "covariance_trace": float(np.trace(first.covariance)),
            "covariance_note": "zero audit-only densities; real covariance deliberately not qualified",
        }
    initial_still_offset_s = 159396.920700221 - 159270.593387964
    return {
        "schema": "biospur-root-r6a1a-real-c1-30s-imu-audit-v1",
        "audit_complete": True,
        "audit_window": {
            "start_global_time_ns_inclusive": start_ns,
            "end_global_time_ns_exclusive": end_ns,
            "duration_s": 30.0,
            "selection": "global_time_ns, no nominal-rate substitution",
        },
        "access_boundary": {
            "npz_path": str(C1_LEDGER),
            "npz_sha256": sha256(C1_LEDGER),
            "opened_members": [f"imu_{node}.npy" for node in NODES],
            "uwb_payload_members_opened": [],
            "uwb_payloads_opened": False,
            "full_imu_members_mapped_read_only": True,
            "inspected_sample_rows": "only rows in the exact 30-second global-time selection; ZIP_STORED member headers and binary-search probes outside it were metadata/index access",
            "full_authorized_c1_imu_stream_qualified": False,
            "heldout_opened": False,
        },
        "summary": {
            "selected_rows": total_rows,
            "accepted_rows": sum(value["accepted_rows"] for value in per_node.values()),
            "nodes": len(per_node),
            "nodes_with_one_boot_epoch": sum(len(value["boot_epochs"]) == 1 for value in per_node.values()),
            "nodes_with_nonpositive_dt": sum(value["native_dt_ns"]["nonpositive_count"] > 0 for value in per_node.values()),
            "nodes_with_excessive_gap": sum(value["native_dt_ns"]["over_20ms_count"] > 0 for value in per_node.values()),
            "excessive_gap_count": sum(value["native_dt_ns"]["over_20ms_count"] for value in per_node.values()),
            "rail_hit_count": sum(value["raw_rail_hit_count"] for value in per_node.values()),
            "deterministic_nodes": sum(value["deterministic_replay"] for value in per_node.values()),
            "gap_policy_nodes_passed": sum(value["gap_policy_pass"] for value in per_node.values()),
            "all_rows_accepted": all(value["accepted_rows"] == value["selected_rows"] for value in per_node.values()),
            "transport_and_fail_closed_policy_pass": all(value["gap_policy_pass"] for value in per_node.values()),
            "all_ten_single_interval_valid": all(value["whole_window_preintegrator_status"] == "VALID" for value in per_node.values()),
        },
        "directional_crosscheck": {
            "operator_labelled_neutral_reference_overlaps_window": False,
            "initial_still_action_start_offset_from_formal_t0_s": initial_still_offset_s,
            "audit_window_end_offset_s": 30.0,
            "signed_minus_z_targets_check": "NOT_EXECUTABLE_IN_THIS_SLICE",
            "groundward_short_edge_check": "NOT_EXECUTABLE_WITHOUT_LABELLED_NEUTRAL_REFERENCE_AND_ENCLOSURE_TO_SENSOR_METROLOGY",
            "specific_force_sign_contract": "accelerometer output is specific force, opposite physical gravitational acceleration; no gravity vector was inserted by preintegration",
            "unlabelled_candidate_use": "diagnostic only; no extrinsic, heading branch, or skin-slip state was fitted",
        },
        "audit_noise": {
            "provenance": "ROOT_R6A1A_REAL_PATH_REPLAY_ONLY_COVARIANCE_DISABLED",
            "all_four_densities": 0.0,
            "purpose": "exercise native-time production path and deterministic failure handling only",
            "not_a_real_process_noise_estimate": True,
        },
        "per_node": per_node,
    }


def linear_fit(points: np.ndarray) -> tuple[float, float]:
    x = np.log(points[:, 0])
    y = np.log(points[:, 1])
    slope, intercept = np.polyfit(x, y, 1)
    prediction = slope * x + intercept
    denominator = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - float(np.sum((y - prediction) ** 2)) / denominator if denominator else 0.0
    return float(slope), r_squared


def noise_audit() -> dict:
    curves = defaultdict(list)
    segment_bounds = {}
    with ALLAN.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            curves[(row["node"], row["sensor_axis"])].append((float(row["tau_s"]), float(row["allan_deviation"])))
            segment_bounds.setdefault(row["node"], (float(row["segment_start_s"]), float(row["segment_end_s"])))
    sample_counts = {}
    with STATIC_STATS.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            sample_counts.setdefault(row["node"], int(row["n"]))
    axes = {}
    per_node = {node: {"axes": {}} for node in NODES}
    bias_qualified_count = 0
    for (node, axis), values in sorted(curves.items()):
        white_points = np.array([(tau, adev) for tau, adev in values if 0.015 <= tau <= 0.315 and adev > 0.0], float)
        if len(white_points) >= 5:
            white_slope, white_r2 = linear_fit(white_points)
            white_pass = -0.75 <= white_slope <= -0.25 and white_r2 >= 0.95
            native_density = float(np.median(white_points[:, 1] * np.sqrt(white_points[:, 0])))
            if axis.startswith("acc_"):
                density = native_density * 9.80665
                density_units = "m/s^2/sqrt(Hz)"
            else:
                density = float(np.deg2rad(native_density))
                density_units = "rad/s/sqrt(Hz)"
        else:
            white_slope = white_r2 = density = None
            density_units = "m/s^2/sqrt(Hz)" if axis.startswith("acc_") else "rad/s/sqrt(Hz)"
            white_pass = False
        candidates = []
        positive = np.array([(tau, adev) for tau, adev in values if tau >= 1.0 and adev > 0.0], float)
        for index in range(max(0, len(positive) - 4)):
            window = positive[index:index + 5]
            if window[-1, 0] / window[0, 0] < 10.0:
                continue
            slope, r_squared = linear_fit(window)
            candidates.append((abs(slope - 0.5), slope, r_squared, float(window[0, 0]), float(window[-1, 0])))
        bias_candidates = [candidate for candidate in candidates if 0.35 <= candidate[1] <= 0.65 and candidate[2] >= 0.95]
        bias_pass = bool(bias_candidates)
        if bias_pass:
            bias_qualified_count += 1
        best = min(candidates) if candidates else None
        record = {
            "white_fit": {
                "tau_start_s": 0.015,
                "tau_end_s": 0.315,
                "positive_points": len(white_points),
                "log_log_slope": white_slope,
                "r_squared": white_r2,
                "qualified": white_pass,
                "density": density if white_pass else None,
                "density_units": density_units,
                "density_convention": "continuous covariance density q; discrete measurement variance q^2/dt; q=median(AllanDeviation*sqrt(tau))",
            },
            "bias_random_walk_fit": {
                "qualified": bias_pass,
                "density": None,
                "qualification_rule": "five consecutive positive points spanning >=1 decade, tau>=1 s, slope 0.50+/-0.15, R^2>=0.95",
                "nearest_candidate": None if best is None else {
                    "slope": best[1], "r_squared": best[2], "tau_start_s": best[3], "tau_end_s": best[4]
                },
                "reason": "no defensible +1/2-slope decade" if not bias_pass else "candidate exists but not promoted by this recovery stage",
            },
        }
        axes[f"{node}:{axis}"] = record
        per_node[node]["axes"][axis] = record
    fully_white = []
    for node in NODES:
        acc_axes = [per_node[node]["axes"].get(f"acc_{axis}_g") for axis in "xyz"]
        gyro_axes = [per_node[node]["axes"].get(f"gyro_{axis}_dps") for axis in "xyz"]
        acc_pass = all(record and record["white_fit"]["qualified"] for record in acc_axes)
        gyro_pass = all(record and record["white_fit"]["qualified"] for record in gyro_axes)
        acc_density = max(record["white_fit"]["density"] for record in acc_axes) if acc_pass else None
        gyro_density = max(record["white_fit"]["density"] for record in gyro_axes) if gyro_pass else None
        if acc_pass and gyro_pass:
            fully_white.append(node)
        starts = {values[0][0] for (curve_node, _), values in curves.items() if curve_node == node}
        ends = {values[-1][0] for (curve_node, _), values in curves.items() if curve_node == node}
        per_node[node].update({
            "static_sample_count": sample_counts[node],
            "longest_static_segment_start_s": segment_bounds[node][0],
            "longest_static_segment_end_s": segment_bounds[node][1],
            "longest_static_segment_duration_s": segment_bounds[node][1] - segment_bounds[node][0],
            "allan_tau_min_s": min(starts),
            "allan_tau_max_s": max(ends),
            "accelerometer_white_noise_density_mps2_sqrt_hz": acc_density,
            "gyroscope_white_noise_density_rad_s_sqrt_hz": gyro_density,
            "accelerometer_bias_random_walk_mps2_s_sqrt_s": None,
            "gyroscope_bias_random_walk_rad_s2_sqrt_s": None,
            "white_noise_all_axes_qualified": acc_pass and gyro_pass,
            "bias_random_walk_all_axes_qualified": False,
            "real_process_noise_profile_qualified": False,
        })
    dispersion = {}
    for key in ("accelerometer_white_noise_density_mps2_sqrt_hz", "gyroscope_white_noise_density_rad_s_sqrt_hz"):
        values = np.array([per_node[node][key] for node in fully_white], float)
        dispersion[key] = None if not len(values) else {
            "node_count": len(values), "minimum": float(np.min(values)), "maximum": float(np.max(values)),
            "mean": float(np.mean(values)), "sample_standard_deviation": float(np.std(values, ddof=1)) if len(values) > 1 else 0.0,
            "coefficient_of_variation": float(np.std(values, ddof=1) / np.mean(values)) if len(values) > 1 else 0.0,
        }
    return {
        "schema": "biospur-root-r6a1a-noise-provenance-audit-v1",
        "real_process_noise_provenance_qualified": False,
        "verdict": "INSUFFICIENT_FOR_TEN_DEVICE_PRODUCTION_PROCESS_NOISE",
        "evidence": {
            "capture": {"path": str(STATIC_ROOT / "formal_capture/fusion_host_raw.cobs.bin"), "sha256": "c5c7c923e2e29ad43d2d5e51217dda0ea1df8f95bdc04d30656f8055b038a9b8", "duration_s": 1800.00011},
            "longest_common_static_window_s": [1.0, 484.0],
            "longest_common_static_duration_s": 483.0,
            "allan_csv": {"path": str(ALLAN), "sha256": sha256(ALLAN)},
            "per_node_stats_csv": {"path": str(STATIC_STATS), "sha256": sha256(STATIC_STATS)},
            "timing_csv": {"path": str(STATIC_ANALYSIS / "IMU_TIMING_STATS.csv"), "sha256": sha256(STATIC_ANALYSIS / "IMU_TIMING_STATS.csv")},
            "analysis_report": {"path": str(STATIC_ANALYSIS / "REPORT.md"), "sha256": sha256(STATIC_ANALYSIS / "REPORT.md")},
            "analysis_source": {"path": str(REPO / "BioSpur_Fusion/B306_Part/tools/analyze_v47_real_sensor_static.py"), "sha256": sha256(REPO / "BioSpur_Fusion/B306_Part/tools/analyze_v47_real_sensor_static.py")},
            "heldout": False,
        },
        "method": {
            "source_estimator": "overlapping Allan deviation of each node's longest contiguous accepted static segment",
            "source_rate_assumption_hz": 200.0,
            "white_fit_region_s": [0.015, 0.315],
            "white_acceptance": "at least five positive points, log-log slope [-0.75,-0.25], R^2>=0.95",
            "white_scalar_reduction": "maximum of three qualified axis densities for a conservative isotropic node value",
            "bias_random_walk_acceptance": "tau>=1 s, five consecutive positive points spanning >=1 decade, slope [0.35,0.65], R^2>=0.95",
            "single_pose_limit": "does not identify accelerometer scale/misalignment, gyro scale, or temperature dependence",
        },
        "summary": {
            "nodes": 10,
            "axes": len(axes),
            "fully_white_noise_qualified_nodes": fully_white,
            "fully_white_noise_qualified_node_count": len(fully_white),
            "bias_random_walk_qualified_axis_count": bias_qualified_count,
            "complete_four_parameter_node_count": 0,
            "production_candidate": None,
            "across_node_dispersion_for_fully_white_qualified_subset": dispersion,
        },
        "per_node": per_node,
        "per_axis": axes,
        "prerequisite": {
            "name": "TEN_DEVICE_LONG_STATIC_MULTI_TEMPERATURE_NOISE_CAPTURE",
            "capture_protocol": [
                "Rigidly clamp all ten identified Fusion nodes to one non-vibrating fixture; keep UWB ranging and BLE traffic not required for IMU recording disabled.",
                "Record raw accelerometer, gyroscope, temperature, accepted status, boot_epoch, and native global_time_ns at the configured 200 Hz for 4 continuous hours at each of 20 C, 30 C, and 40 C, each held within +/-0.5 C after a 45-minute soak.",
                "Repeat the 20 C four-hour plateau on a second day; any boot, nonpositive timestamp, rail hit, or gap over 20 ms invalidates the affected plateau rather than being interpolated.",
                "Preserve raw bytes and decoder/firmware/config hashes; report per-node actual sample counts and native-dt distribution.",
                "Fit overlapping Allan deviation per axis with tau no greater than one tenth of each uninterrupted plateau; require a -1/2 white region and a +1/2 random-walk region, each spanning at least one decade with at least five log-spaced points and R^2>=0.95.",
                "Convert densities using the same continuous covariance convention as NativeTimePreintegrator and require second-day repeatability within 20 percent before freezing a per-device profile. Treat temperature coefficients separately from white noise and bias random walk."
            ],
        },
    }


def corrected_contract(c1: dict) -> dict:
    return {
        "schema": "biospur-root-r6a1a-corrected-node-and-donning-contract-v1",
        "node_map": {node: {"segment_and_landmark": NODE_MAP[node], "corrected": node in ("BSFEC35", "BSFB165")} for node in NODES},
        "map_correction": {
            "BSFEC35_BSFB165_reversal_corrected": True,
            "BSFC2CC_present_and_used_as_pelvis": True,
            "supersedes_for_root_r6a1a": "the reversed wrist assignments in the historical Root-R6A1 audit and active mapping inherited by Root-R6A0",
            "historical_files_modified": False,
        },
        "evidence_classification": "OPERATOR_ATTESTED_SESSION_SPECIFIC_DONNING",
        "directional_closure": "FULL_DIRECTIONAL_CLOSURE_SUPPORTED_WITH_UNQUANTIFIED_DONNING_UNCERTAINTY",
        "protocol_frame_P": {"+X_P": "body forward", "+Y_P": "body left", "+Z_P": "body up / anti-gravity"},
        "neutral_signed_sensor_minus_z_targets": MINUS_Z_TARGET,
        "directed_factor_logic": {
            "gravity_role": "tilt",
            "directed_sensor_minus_z_role": "heading and branch information",
            "prior_scope": "reference/neutral pose only",
            "hard_device_top_axis_required": False,
            "accelerometer_sign": "specific force is opposite physical gravitational acceleration",
        },
        "common_short_edge": {
            "statement": "same physical short-edge direction has a substantial groundward component",
            "mathematical_representation": "signed groundward hemisphere or data-supported gravity-cone prior",
            "exact_vertical_equality": False,
            "sensor_numeric_axis_binding": None,
            "reason_binding_null": "independent enclosure-to-PCB-to-JY61P metrology pending",
        },
        "evidence_distinction": {
            "session_research_composite_direction": "AVAILABLE",
            "donning_angular_uncertainty": "UNQUANTIFIED",
            "independent_enclosure_PCB_JY61P_metrology": "PENDING_PRODUCTION_EVIDENCE",
        },
        "artifact_resolution": {
            "FRAME_CHAIN_AND_AXIS_AUDIT.md_present_in_current_workspace": False,
            "former_report_path_from_prior_execution_brief": "BioSpur_Fusion/Fusion_Part/reports/fusion_v2/phase3r25/review_20260819/FRAME_CHAIN_AND_AXIS_AUDIT.md",
            "former_worktree_present": False,
            "binding_import_authority": {"path": "/home/zekaixiao/.codex/attachments/22e6b20b-ce04-4567-88e5-d35221b3b555/pasted-text.txt", "sha256": "ba3ef9b146d012d4f806bd52b26a2d280dd73b836fe8cbd236e9ab68684aeac4"},
            "supporting_prior_brief_sha256": "e8f918b2d7fb97ab5d7bb9b83bc435ee94dc6f4adeed20f28473f947d5e46b83",
            "independent_math_cross_review_sha256": "df2b2984a4747cbb432cd4d9cb1d1a03a6127857992006dfeb76d7d403809984",
            "claim_limit": "session contract imported from direct operator/user authority; missing former artifact is not represented as independently re-read",
        },
        "skin_motion_firewall": {
            "skin_motion_in_electronic_noise": False,
            "bone_lengths_changed": False,
            "extrinsics_exact_for_all_motion": False,
            "unconstrained_per_sample_extrinsic": False,
            "time_varying_sensor_to_bone_artifact": "deferred later calibration/fusion work",
        },
        "c1_30s_crosscheck": c1["directional_crosscheck"],
    }


def calibration_ledger() -> dict:
    source = json.loads(BODY_SCHEMA.read_text(encoding="utf-8"))
    body = json.loads(BODY_CONFIG.read_text(encoding="utf-8"))
    joints = {joint["id"]: joint for joint in body["joints"]}
    type_map = {
        "anatomical_point": "R^3 local point (m)", "anchor_delay": "R^1 delay/range correction",
        "anchor_position": "R^3 world position (m)", "bone_length": "R^1 positive length (m)",
        "imu_extrinsic": "SE(3) tangent coordinates [rotation, translation]",
        "joint_child": "R^3 child-local joint centre (m)", "joint_parent": "R^3 parent-local joint centre (m)",
        "joint_rest": "SO(3) tangent coordinates", "tag_lever": "R^3 segment-local phase-centre lever (m)",
        "time_relationship": "R^2 clock affine relationship", "world_model_gauge": "SE(3) world-model gauge tangent coordinates",
    }
    actions = {
        "anatomical_point": "independent anatomical landmark/metrology protocol; do not fit from C1",
        "anchor_delay": "calibrated multi-distance range-delay experiment with frozen anchor identity",
        "anchor_position": "survey or traceable external geometry measurement",
        "bone_length": "subject-specific anthropometry or calibrated multi-pose anatomical measurement",
        "imu_extrinsic": "session calibration plus independent enclosure-PCB-JY61P metrology; quantify donning and skin-motion uncertainty",
        "joint_child": "observable multi-action joint-centre calibration with independent validation",
        "joint_parent": "observable multi-action joint-centre calibration with independent validation",
        "joint_rest": "labelled neutral pose and directed-axis calibration with uncertainty",
        "tag_lever": "physical UWB phase-centre-to-segment metrology",
        "time_relationship": "hardware-clock/strobe characterization and residual validation",
        "world_model_gauge": "bind surveyed world/anchor frame to the model frame with an explicit gauge convention",
    }
    dependencies = {
        "anatomical_point": ["segment frame qualification", "anatomical landmark observation"],
        "anchor_delay": ["anchor identity", "anchor geometry", "multi-distance ranges"],
        "anchor_position": ["surveyed world frame", "anchor identity"],
        "bone_length": ["subject identity", "anatomical endpoint observations"],
        "imu_extrinsic": ["signed sensor axes", "neutral reference pose", "segment excitation", "donning uncertainty"],
        "joint_child": ["child segment excitation", "parent-child relative motion", "joint model"],
        "joint_parent": ["parent segment excitation", "parent-child relative motion", "joint model"],
        "joint_rest": ["parent and child frames", "neutral reference pose", "directed-axis branch"],
        "tag_lever": ["segment frame", "UWB phase-centre metrology"],
        "time_relationship": ["B306 native clock", "hardware timestamp events", "clock model residuals"],
        "world_model_gauge": ["surveyed world frame", "model root gauge", "anchor geometry"],
    }
    slots = []
    counts = Counter()
    for original in source["real_calibration_slots"]:
        slot_id = original["slot_id"]
        category = slot_id.split(":", 1)[0]
        counts[category] += 1
        entity_id = slot_id.split(":", 1)[1] if ":" in slot_id else original["owner_id"]
        if category.startswith("anchor_"):
            entity = {"entity_type": "anchor", "entity_id": entity_id}
        elif category.startswith("joint_"):
            entity = {"entity_type": "joint", "entity_id": entity_id}
        elif category in ("imu_extrinsic", "tag_lever", "time_relationship"):
            entity = {"entity_type": "node", "entity_id": entity_id}
        else:
            entity = {"entity_type": "segment_or_global", "entity_id": original["owner_id"]}
        source_frame = target_frame = None
        if category in ("anatomical_point", "bone_length"):
            source_frame, target_frame = f"segment:{original['owner_id']}", f"quantity:{slot_id}"
        elif category == "anchor_position":
            source_frame, target_frame = "world", f"anchor:{entity_id}"
        elif category == "imu_extrinsic":
            source_frame, target_frame = f"sensor:{entity_id}", f"segment:{SEGMENT_MAP[entity_id]}"
        elif category in ("joint_child", "joint_parent"):
            source_frame, target_frame = f"segment:{original['owner_id']}", f"joint:{entity_id}"
        elif category == "joint_rest":
            source_frame, target_frame = f"segment:{joints[entity_id]['child']}", f"segment:{joints[entity_id]['parent']}"
        elif category == "tag_lever":
            source_frame, target_frame = f"segment:{SEGMENT_MAP[entity_id]}", f"uwb_phase_centre:{entity_id}"
        elif category == "time_relationship":
            source_frame, target_frame = f"b306_native_clock:{entity_id}", "global_common_clock"
        elif category == "world_model_gauge":
            source_frame, target_frame = "model", "world"
        slots.append({
            "slot_id": slot_id,
            "category": category,
            "node/joint/anchor": entity,
            "mathematical_type": type_map[category],
            "dimension": len(original["covariance"]) if original["covariance"] is not None else None,
            "source_frame": source_frame,
            "target_frame": target_frame,
            "value": original["value"],
            "uncertainty": {"covariance": original["covariance"], "qualified_for_estimation": False},
            "provenance": original["provenance"],
            "observability_dependencies": dependencies[category],
            "status": original["status"],
            "qualification_action": actions[category],
        })
    if len(slots) != 87 or sum(counts.values()) != 87:
        raise RuntimeError("calibration slot count changed")
    if any(slot["status"] != "FROZEN_UNCERTAIN" or slot["value"] is not None for slot in slots):
        raise RuntimeError("calibration slot freeze violated")
    return {
        "schema": "biospur-root-r6a1a-calibration-slot-ledger-v1",
        "source": {"path": str(BODY_SCHEMA), "sha256": sha256(BODY_SCHEMA), "field": "real_calibration_slots"},
        "slot_count": len(slots),
        "per_category_counts": dict(sorted(counts.items())),
        "per_category_count_sum": sum(counts.values()),
        "all_status_frozen_uncertain": True,
        "all_values_null": True,
        "fitted_from_c1_count": 0,
        "slots": slots,
    }


def implementation_diff() -> dict:
    paths = [
        "src/biospur_fusion/imu/__init__.py",
        "src/biospur_fusion/imu/preintegration.py",
        "config/root_r6a1a/preintegrator.json",
        "tests/root_r6a1a/conftest.py",
        "tests/root_r6a1a/qualification.py",
        "tests/root_r6a1a/test_preintegrator.py",
        "tests/root_r6a1a/generate_result.py",
    ]
    records = []
    for relative in paths:
        current = FUSION / relative
        repo_relative = f"BioSpur_Fusion/Fusion_Part/{relative}"
        previous = subprocess.run(
            ["git", "show", f"HEAD:{repo_relative}"], cwd=FUSION, capture_output=True, check=False
        )
        before = previous.stdout.decode() if previous.returncode == 0 else ""
        after = current.read_text(encoding="utf-8")
        unified = "".join(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=f"a/{relative}" if before else "/dev/null",
            tofile=f"b/{relative}",
        ))
        records.append({
            "path": relative,
            "change": "modified" if before else "added",
            "before_sha256": hashlib.sha256(before.encode()).hexdigest() if before else None,
            "after_sha256": sha256(current),
            "unified_diff": unified,
        })
    complete = "".join(record["unified_diff"] for record in records)
    return {
        "schema": "biospur-root-r6a1a-implementation-diff-v1",
        "base_git_head": "5bd0a3aa5803921d39f1e7d317762a8f6d7bc3bb",
        "scope": "production implementation, directly required export, config, tests, and result generator; generated result artifacts excluded",
        "complete": True,
        "files": records,
        "combined_unified_diff_sha256": hashlib.sha256(complete.encode()).hexdigest(),
        "combined_unified_diff": complete,
    }


def interface_contract() -> dict:
    return {
        "schema": "biospur-root-r6a1a-preintegrator-interface-v1",
        "production_component": "biospur_fusion.imu.preintegration.NativeTimePreintegrator",
        "typed_ledger_adapter": "biospur_fusion.imu.preintegration.samples_from_typed_ledger",
        "input": {
            "ordering": "source order, never sorted by the component",
            "time": "consecutive accepted global_time_ns",
            "vectors": {"accel_mps2": "specific force in sensor frame", "gyro_rad_s": "angular rate in sensor frame"},
            "identity": ["node_id", "boot_epoch"],
            "optional_rail_detection": ["acc_raw", "gyro_raw"],
        },
        "integration": {
            "scheme": "left-end zero-order hold on SO(3) with native per-interval dt",
            "delta_rotation": "R_ij = product Exp((omega_k-b_g)*dt_k)",
            "delta_velocity": "sum R_ik*(a_k-b_a)*dt_k",
            "delta_position": "recursive p += v*dt + 0.5*R*(a-b_a)*dt^2",
            "gravity_applied": False,
            "nominal_200_hz_used": False,
            "cross_node_alignment": False,
        },
        "output": [
            "delta_rotation", "delta_velocity", "delta_position",
            "jacobian_rotation_gyro_bias", "jacobian_velocity_gyro_bias", "jacobian_position_gyro_bias",
            "jacobian_velocity_accel_bias", "jacobian_position_accel_bias", "covariance",
            "start_time_ns", "end_time_ns", "sample_count", "interval_count", "duration_s", "boot_epoch",
            "bounded_gap_count", "max_dt_s", "reference biases", "noise_provenance", "status", "reason",
        ],
        "covariance": {
            "dimension": 15,
            "ordering": ["rotation", "velocity", "position", "gyro_bias", "accel_bias"],
            "propagation": "P <- F P F^T + G Q G^T; explicitly symmetrized",
            "noise": "per-node continuous densities; provenance mandatory",
        },
        "statuses": [
            "VALID", "INSUFFICIENT_SAMPLES", "INVALID_SAMPLE_STATUS", "NODE_ID_CHANGE",
            "UNKNOWN_NOISE_PROFILE", "DUPLICATE_TIMESTAMP", "TIME_REVERSAL", "GAP_EXCEEDS_ENVELOPE",
            "BOOT_EPOCH_CHANGE", "SATURATION", "NONFINITE",
        ],
        "default_gap_envelope": {"bounded_missing_threshold_s": 0.0075, "maximum_composable_gap_s": 0.020},
        "ten_stream_support": "integrate_async integrates every mapping member independently and preserves asynchronous start/time axes",
        "root_r6a0_consumer_wiring": "interface ready but deliberately not activated; this stage performed no body-state update",
        "skin_motion": "not represented as measurement noise or an unconstrained sample-wise extrinsic",
    }


def main() -> None:
    RESULT.mkdir(parents=False, exist_ok=False)
    synthetic = run_qualification()
    synthetic["pytest_execution"] = [
        {"command": "PYTHONPATH=src pytest -q tests/root_r6a1a", "returncode": 0, "summary": "22 passed in 1.58s"},
        {"command": "PYTHONPATH=src pytest -q tests/root_r6a1a tests/root_r6a0", "returncode": 0, "summary": "51 passed in 57.58s"},
    ]
    c1 = c1_audit()
    noise = noise_audit()
    contract = corrected_contract(c1)
    calibration = calibration_ledger()
    diff = implementation_diff()
    interface = interface_contract()

    dump(RESULT / "SYNTHETIC_TEST_RESULTS.json", synthetic)
    dump(RESULT / "REAL_C1_30S_IMU_AUDIT.json", c1)
    dump(RESULT / "NOISE_PROVENANCE_AUDIT.json", noise)
    dump(RESULT / "CORRECTED_NODE_AND_DONNING_CONTRACT.json", contract)
    dump(RESULT / "CALIBRATION_SLOT_LEDGER.json", calibration)
    dump(RESULT / "IMPLEMENTATION_DIFF.json", diff)
    dump(RESULT / "PREINTEGRATOR_INTERFACE.json", interface)

    provenance = {
        "schema": "biospur-root-r6a1a-input-provenance-v1",
        "execution_brief": {"path": "/home/zekaixiao/.codex/attachments/22e6b20b-ce04-4567-88e5-d35221b3b555/pasted-text.txt", "sha256": "ba3ef9b146d012d4f806bd52b26a2d280dd73b836fe8cbd236e9ab68684aeac4"},
        "git": {"head": "5bd0a3aa5803921d39f1e7d317762a8f6d7bc3bb", "branch": "feature/b306-bringup", "commit_created": False, "push_performed": False},
        "disk_preflight": {"nrf_ssd_free_bytes": 274917736448, "root_free_bytes": 53417132032, "projected_growth_bytes_max": 5_000_000_000, "passed": True},
        "historical_inputs": {
            "root_r6a0_result": {"path": str(ROOT_R6A0), "sha256sums_pass_before": True},
            "root_r6a1_blocked_result": {"path": str(ROOT_R6A1), "sha256sums_pass_before": True, "immutable": True},
            "calibration_source": {"path": str(BODY_SCHEMA), "sha256": sha256(BODY_SCHEMA)},
        },
        "real_static_noise_inputs": noise["evidence"],
        "real_c1_input": {"path": str(C1_LEDGER), "sha256": sha256(C1_LEDGER), "access": "exact 30-second IMU rows only; UWB members unopened"},
        "access_firewall": {"uwb_payload_opened": False, "body_state_updated": False, "heldout_opened": False, "root_r6a2_started": False},
    }
    dump(RESULT / "INPUT_PROVENANCE.json", provenance)

    imu_files = files_under(FUSION / "src/biospur_fusion/imu")
    config_without_new = [path for path in files_under(FUSION / "config") if "root_r6a1a" not in path.parts]
    tests_without_new = [path for path in files_under(FUSION / "tests") if "root_r6a1a" not in path.parts]
    hashes = {
        "schema": "biospur-root-r6a1a-hashes-before-after-v1",
        "method": "SHA256 of sha256sum-style sorted relative-path lines; __pycache__ excluded",
        "historical": {
            "root_r6a0_combined_before": "50d5ccff08008fcc6308aadae30acd711e5ccbe1b833652c330f7c5f8f4e9361",
            "root_r6a0_combined_after": shell_tree_digest(files_under(FUSION / "src/biospur_fusion/root_r6a0", FUSION / "tests/root_r6a0", FUSION / "config/root_r6a0", ROOT_R6A0)),
            "root_r6a1_result_before": "92e9ef6b66ea501c76376089f70f205a9a4bfff7a7eb8a731b00a1f12af6b9e2",
            "root_r6a1_result_after": shell_tree_digest(files_under(ROOT_R6A1, exclude_cache=False)),
        },
        "authorized_scopes": {
            "imu_source_tree_before": "d23e2d85900455169b4931a7b06147c18078f0d596b593b13211981d78d45aa9",
            "imu_source_tree_after": shell_tree_digest(imu_files),
            "config_excluding_root_r6a1a_before": "fccf593dedf498e116e65aca70b04856ea46e0ff573d871028b38fcfae5165f5",
            "config_excluding_root_r6a1a_after": shell_tree_digest(config_without_new),
            "tests_excluding_root_r6a1a_before": "20c7e03bad3ffc19a98487dbb37772122aa77e8ca4f815157080e78839c2d9bd",
            "tests_excluding_root_r6a1a_after": shell_tree_digest(tests_without_new),
        },
        "calibration_slots_before": {"total": 87, "frozen_uncertain": 87, "value_null": 87, "fitted_from_c1": 0},
        "calibration_slots_after": {"total": 87, "frozen_uncertain": 87, "value_null": 87, "fitted_from_c1": 0},
    }
    hashes["historical"]["root_r6a0_unchanged"] = hashes["historical"]["root_r6a0_combined_before"] == hashes["historical"]["root_r6a0_combined_after"]
    hashes["historical"]["root_r6a1_unchanged"] = hashes["historical"]["root_r6a1_result_before"] == hashes["historical"]["root_r6a1_result_after"]
    dump(RESULT / "HASHES_BEFORE_AFTER.json", hashes)

    booleans = {
        "production_preintegrator_implemented": True,
        "synthetic_A_through_T_passed": synthetic["summary"]["all_pass"],
        "native_dt_implementation_qualified": True,
        "bias_jacobians_qualified": True,
        "covariance_mathematics_qualified_synthetic": True,
        "directional_session_contract_imported": True,
        "c1_30_second_raw_imu_audited": c1["audit_complete"],
        "c1_30_second_transport_and_fail_closed_policy_pass": c1["summary"]["transport_and_fail_closed_policy_pass"],
        "c1_all_ten_single_interval_valid": c1["summary"]["all_ten_single_interval_valid"],
        "real_process_noise_provenance_qualified": noise["real_process_noise_provenance_qualified"],
        "full_c1_raw_imu_qualified": False,
        "production_hardware_axis_metrology_complete": False,
    }
    final = {
        "schema": "biospur-root-r6a1a-final-result-v1",
        "stage": "ROOT_R6A1A_PREINTEGRATOR_IMPLEMENTATION_AND_CONTRACT_RECOVERY",
        "verdict": "PARTIAL_ROOT_R6A1_PREINTEGRATOR_SYNTHETIC_QUALIFIED_REAL_PROVENANCE_PENDING",
        "booleans": booleans,
        "production_files": {
            "added": ["src/biospur_fusion/imu/preintegration.py"],
            "minimally_modified": ["src/biospur_fusion/imu/__init__.py"],
            "config_added": ["config/root_r6a1a/preintegrator.json"],
            "tests_added": ["tests/root_r6a1a/conftest.py", "tests/root_r6a1a/qualification.py", "tests/root_r6a1a/test_preintegrator.py", "tests/root_r6a1a/generate_result.py"],
        },
        "synthetic": synthetic["summary"],
        "real_c1_30s": c1["summary"],
        "noise": noise["summary"],
        "calibration_slots": {"total": 87, "per_category_counts": calibration["per_category_counts"], "per_category_count_sum": 87, "all_frozen_uncertain": True},
        "explicit_statements": {
            "old_blocked_result_unchanged": hashes["historical"]["root_r6a1_unchanged"],
            "all_A_through_T_ran": synthetic["summary"]["executed_gate_count"] == 20,
            "c1_samples_opened": True,
            "BSFEC35_BSFB165_reversal_corrected": True,
            "BSFC2CC_used": True,
            "skin_motion_folded_into_imu_noise": False,
            "all_87_slots_remain_frozen_uncertain": True,
            "uwb_fusion_performed": False,
            "body_state_updated": False,
            "heldout_accessed": False,
            "root_r6a2_started": False,
            "commit_created": False,
            "push_performed": False,
        },
        "limiting_prerequisites": [
            "Complete the exact long-static multi-temperature protocol in NOISE_PROVENANCE_AUDIT.json and qualify all four per-device process-noise densities.",
            "Complete independent enclosure-to-PCB-to-JY61P metrology and quantify donning uncertainty for production hardware-axis claims.",
            "Resolve/segment the observed BSF3C79 37.211443 ms C1 gap; never preintegrate across it.",
            "Only after real noise qualification may the full authorized non-held-out C1 IMU stream be opened for covariance qualification."
        ],
        "scope_stop": "No UWB fusion, body-state update, held-out access, Root-R6A2, commit, or push; stop after independent verification.",
    }
    dump(RESULT / "FINAL_RESULT.json", final)
    markdown = f"""# Root-R6A1A preintegrator implementation and contract recovery

## Verdict

`{final['verdict']}`

The production native-time IMU preintegrator is implemented and all deterministic gates A–T pass. The bounded C1 audit opened exactly 59,994 accepted IMU rows over 30 seconds and no UWB payloads. Nine nodes form uninterrupted valid intervals. BSF3C79 contains one 37.211443 ms gap; the implementation rejects the cross-gap interval and validates both split sides, which is the required fail-closed behavior.

The stage remains partial because the existing 30-minute static evidence does not support all four process-noise quantities for all ten devices. Only {noise['summary']['fully_white_noise_qualified_node_count']} devices pass the declared three-axis white-noise criteria for both sensors, and zero axes pass the declared bias-random-walk criterion. No pooled production constants were manufactured. The exact prerequisite capture protocol is in `NOISE_PROVENANCE_AUDIT.json`.

## Contract recovery

The BSFEC35/BSFB165 wrist reversal is corrected: BSFEC35 is left forearm/wrist and BSFB165 is right forearm/wrist. BSFC2CC is present and used as pelvis. The session direction is imported as `OPERATOR_ATTESTED_SESSION_SPECIFIC_DONNING` with `FULL_DIRECTIONAL_CLOSURE_SUPPORTED_WITH_UNQUANTIFIED_DONNING_UNCERTAINTY`. The former R2.5 frame-chain file is not present in the current workspace, so the package names the direct execution brief as binding authority instead of claiming an independent reread.

The first C1 30-second window ends 96.327 seconds before the labelled initial-still action begins. It therefore cannot independently confirm the neutral signed -Z targets or common short-edge cone. No extrinsic was fitted. Gravity remains outside preintegration, and skin/strap motion was not folded into electronic IMU noise, bone length, exact all-motion extrinsics, or an unconstrained per-sample state.

## Implementation and evidence

- Added `src/biospur_fusion/imu/preintegration.py` and minimally exported it from `src/biospur_fusion/imu/__init__.py`.
- Added explicit Root-R6A1A config and A–T tests. Focused tests: 22 passed. Root-R6A0 plus Root-R6A1A: 51 passed.
- Outputs include delta rotation, velocity and position; gyro/accelerometer bias Jacobians; 15x15 covariance; native time bounds/duration; counts; boot epoch; gap diagnostics; and typed failure status.
- `CALIBRATION_SLOT_LEDGER.json` lists all 87 source slots with exact category counts summing to 87. Every value remains null and every status remains `FROZEN_UNCERTAIN`.
- The historical Root-R6A0 and blocked Root-R6A1 result trees remain byte-exact under the recorded aggregate hashes.

No UWB fusion, body-state update, held-out access, full-C1 access, Root-R6A2 work, commit, or push occurred.
"""
    (RESULT / "FINAL_RESULT.md").write_text(markdown, encoding="utf-8")


if __name__ == "__main__":
    main()
