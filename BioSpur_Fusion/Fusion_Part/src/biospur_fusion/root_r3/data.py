"""Manifest-bound C1 readers; no path guessing and no input writes."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
import sys
from typing import Iterable

import numpy as np

from biospur_fusion.imu.q1 import quaternion_to_matrix
from biospur_fusion.ingest.v47 import _imu_events, iter_cobs_records

from .interfaces import EXPECTED_ANCHORS, m1_segment_points_at, validate_m1_identity
from .models import ImuSample, PositionObservation


@dataclass(frozen=True)
class AuthorizedPaths:
    repository: Path
    m1_npz: Path
    event_schedule_npz: Path
    raw_uwb_npz: Path
    raw_c1_cobs: Path
    c1_input_provenance_json: Path
    frame_audit_json: Path
    layout_json: Path


@dataclass(frozen=True)
class C1UwbTable:
    node_ids: tuple[str, ...]
    node_index: np.ndarray
    source_index: np.ndarray
    measurement_s: np.ndarray
    availability_s: np.ndarray
    strobe_s: np.ndarray
    xyz_m: np.ndarray
    relative_point_m: np.ndarray
    root_observation_identity_m: np.ndarray
    historical_root_candidate_m: np.ndarray
    historical_candidate_valid: np.ndarray
    covariance_diag_m2: np.ndarray
    condition: np.ndarray
    gdop: np.ndarray
    quality_percent: np.ndarray
    used_mask: np.ndarray
    anchors: tuple[tuple[int, ...], ...]
    residuals_m: np.ndarray
    m1_valid: np.ndarray
    m1_future_count: int
    m1_source_index: np.ndarray


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(16 << 20):
            digest.update(block)
    return digest.hexdigest()


def default_authorized_paths() -> AuthorizedPaths:
    repository = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion")
    return AuthorizedPaths(
        repository=repository,
        m1_npz=Path("/tmp/biospur_pure_imu_mvp_m1_20260823T135120Z/CAPTURE1_MVP_REPLAY_DATA.npz"),
        event_schedule_npz=Path("/tmp/biospur_c123_uwb_beacon_causal_replay_20260823T171739Z/C123_UWB_EVENT_SCHEDULE.npz"),
        raw_uwb_npz=Path("/tmp/biospur_c123_uwb_counterfactual_20260823T155948Z/RAW_UWB_TAG_TRAJECTORIES.npz"),
        raw_c1_cobs=repository / "Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/continuous_collector/fusion_host_raw.cobs.bin",
        c1_input_provenance_json=Path("/tmp/biospur_c123_uwb_root_r2_20260824T035440Z/C1_INPUT_AND_PARTITION_PROVENANCE.json"),
        frame_audit_json=repository / "Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2/FRAME_BINDING_RESULT.json",
        layout_json=repository / "B306_Part/deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json",
    )


def verify_authorized_paths(paths: AuthorizedPaths) -> dict:
    provenance = json.loads(paths.c1_input_provenance_json.read_text(encoding="utf-8"))
    expected = provenance["input_hashes"]
    required = [paths.m1_npz, paths.event_schedule_npz, paths.raw_uwb_npz, paths.raw_c1_cobs,
                paths.frame_audit_json, paths.layout_json, paths.c1_input_provenance_json]
    rows = []
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
        if str(path) in expected:
            actual["manifest_expected"] = expected[str(path)]
            actual["manifest_match"] = (actual["bytes"] == expected[str(path)]["bytes"] and
                                        actual["sha256"] == expected[str(path)]["sha256"])
            if not actual["manifest_match"]:
                raise RuntimeError(f"authorized C1 input mismatch: {path}")
        rows.append(actual)
    return {"files": rows, "all_manifest_listed_files_match": all(row.get("manifest_match", True) for row in rows)}


def load_m1(path: Path) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=False) as archive:
        value = {name: archive[name] for name in archive.files}
    validate_m1_identity(value)
    return value


def _raw_node(raw: np.lib.npyio.NpzFile, node: str, field: str) -> np.ndarray:
    return raw[f"c1_{node}_{field}"]


def load_c1_uwb(paths: AuthorizedPaths, m1: dict[str, np.ndarray]) -> C1UwbTable:
    with np.load(paths.event_schedule_npz, allow_pickle=False) as schedule:
        node_index = schedule["c1_node_index"].astype(np.int16)
        source_index = schedule["c1_source_index"].astype(np.int32)
        measurement = schedule["c1_measurement_s"].astype(float)
        availability = schedule["c1_available_s"].astype(float)
        strobe = schedule["c1_strobe_s"].astype(float)
        xyz = schedule["c1_xyz_m"].astype(float)
        historical_root = schedule["c1_root_candidate_m"].astype(float)
        historical_valid = schedule["c1_candidate_valid"].astype(bool)
        used_mask = schedule["c1_used_mask"].astype(np.uint8)
    nodes = tuple(str(value) for value in m1["node_ids"])
    relative, relative_valid, future, m1_source = m1_segment_points_at(m1, node_index, measurement)
    covariance = np.full((len(node_index), 3), np.nan)
    condition = np.full(len(node_index), np.nan)
    gdop = np.full(len(node_index), np.nan)
    quality = np.full((len(node_index), 8), np.nan)
    residuals = np.full((len(node_index), 8), np.nan)
    anchors: list[tuple[int, ...]] = [()] * len(node_index)
    with np.load(paths.raw_uwb_npz, allow_pickle=False) as raw:
        for ni, node in enumerate(nodes):
            rows = np.flatnonzero(node_index == ni)
            src = source_index[rows]
            covariance[rows] = _raw_node(raw, node, "solved_covariance_diag_m2")[src]
            condition[rows] = _raw_node(raw, node, "solved_condition")[src]
            gdop[rows] = _raw_node(raw, node, "solved_gdop")[src]
            quality[rows] = _raw_node(raw, node, "quality")[src]
            residuals[rows] = _raw_node(raw, node, "solved_residuals_m")[src]
            anchor_ids = _raw_node(raw, node, "anchor_id")[src]
            for row, ids, mask in zip(rows, anchor_ids, used_mask[rows]):
                used = tuple(sorted(int(aid) for aid in ids if int(mask) & (1 << int(aid))))
                if any(aid not in EXPECTED_ANCHORS for aid in used):
                    raise ValueError("noncanonical anchor identity")
                anchors[int(row)] = used
    valid = relative_valid & np.all(np.isfinite(xyz), axis=1) & np.all(np.isfinite(covariance), axis=1)
    root = xyz - relative
    root[~valid] = np.nan
    if np.any(measurement > availability + 1e-12):
        raise ValueError("C1 UWB measurement occurs after availability")
    return C1UwbTable(nodes, node_index, source_index, measurement, availability, strobe, xyz, relative, root,
                      historical_root, historical_valid,
                      covariance, condition, gdop, quality, used_mask, tuple(anchors), residuals, valid,
                      int(future.sum()), m1_source)


def position_observations(
    table: C1UwbTable,
    *,
    assumed_rotation_v4_from_m1: np.ndarray,
    frame_valid: bool,
    trunk_sigma_xy_z_m: tuple[float, float] = (0.15, 0.20),
    limb_sigma_xy_z_m: tuple[float, float] = (0.25, 0.30),
) -> list[PositionObservation]:
    rotation = np.asarray(assumed_rotation_v4_from_m1, float)
    if rotation.shape != (3, 3) or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-10):
        raise ValueError("diagnostic frame rotation is invalid")
    values: list[PositionObservation] = []
    for index in range(len(table.node_index)):
        if not table.m1_valid[index]:
            continue
        node = table.node_ids[int(table.node_index[index])]
        root = table.xyz_m[index] - rotation @ table.relative_point_m[index]
        trunk = int(table.node_index[index]) in (0, 1)
        xy, z = trunk_sigma_xy_z_m if trunk else limb_sigma_xy_z_m
        covariance = np.diag(np.maximum(table.covariance_diag_m2[index], 1e-6) + np.array([xy * xy, xy * xy, z * z]))
        values.append(PositionObservation(
            float(table.measurement_s[index]), float(table.availability_s[index]), root, covariance,
            node, table.anchors[index], "NOMINAL", frame_valid, True, int(index),
        ))
    return values


def _fit_affine(x: np.ndarray, y: np.ndarray) -> tuple[float, float, np.ndarray]:
    x = np.asarray(x, float); y = np.asarray(y, float)
    centre = float(np.mean(x)); matrix = np.c_[x - centre, np.ones(len(x))]
    slope, shifted = np.linalg.lstsq(matrix, y, rcond=None)[0]
    intercept = float(shifted - slope * centre)
    residual = y - (slope * x + intercept)
    return float(slope), intercept, residual


def load_pelvis_imu(paths: AuthorizedPaths, m1: dict[str, np.ndarray], table: C1UwbTable,
                    *, node: str = "BSFC2CC") -> tuple[list[ImuSample], dict]:
    """Stream only the authorized pelvis node from raw C1 using existing ingest."""

    node_index = table.node_ids.index(node)
    rows = np.flatnonzero(table.node_index == node_index)
    with np.load(paths.raw_uwb_npz, allow_pickle=False) as raw:
        src = table.source_index[rows]
        strobe_us = _raw_node(raw, node, "strobe_us")[src].astype(float)
        master_ms = _raw_node(raw, node, "master_ms")[src].astype(float)
    timer_slope, timer_intercept, timer_residual = _fit_affine(strobe_us * 1e-6, table.strobe_s[rows])
    # Fit host/DK arrival clock and shift conservatively so no paired UWB event
    # is assigned an availability before the verified B306 frame lower bound.
    arrival_slope, arrival_intercept, _ = _fit_affine(master_ms * 1e-3, table.availability_s[rows])
    predicted = arrival_slope * (master_ms * 1e-3) + arrival_intercept
    arrival_intercept += float(np.max(table.availability_s[rows] - predicted)) + 1e-9
    arrival_residual = arrival_slope * (master_ms * 1e-3) + arrival_intercept - table.availability_s[rows]

    b306_tools = paths.repository / "B306_Part/tools"
    if str(b306_tools) not in sys.path:
        sys.path.insert(0, str(b306_tools))
    from fusion_host_binary import FrameError, decode_frame  # noqa: PLC0415

    m1_node_index = [str(value) for value in m1["node_ids"]].index(node)
    m1_time = np.asarray(m1["time_s"], float)
    m1_q = np.asarray(m1["q_GS_wxyz"], float)[:, m1_node_index]
    m1_valid = np.asarray(m1["valid"], bool)[:, m1_node_index]
    m1_reset = np.asarray(m1["filter_reset"], bool)[:, m1_node_index]
    samples: list[ImuSample] = []
    decode_errors = records = 0
    for index, start, end, encoded, complete in iter_cobs_records(paths.raw_c1_cobs):
        if not complete:
            continue
        try:
            frame = decode_frame(encoded)
            if frame.kind != 3 or frame.node_name != node:
                continue
            records += 1
            for event in _imu_events(frame, 0, (index, start, end, encoded)):
                measurement = timer_slope * (float(event.node_timer_us) * 1e-6) + timer_intercept
                availability = arrival_slope * (float(event.master_arrival_ms) * 1e-3) + arrival_intercept
                orientation_index = int(np.searchsorted(m1_time, measurement, side="right") - 1)
                if orientation_index < 0:
                    continue
                rotation = quaternion_to_matrix(m1_q[orientation_index])
                force = np.asarray(event.payload["acc_raw"], float) / 2048.0 * 9.80665
                samples.append(ImuSample(
                    measurement, max(availability, measurement), force, rotation, int(event.sequence),
                    bool(m1_valid[orientation_index]), bool(m1_reset[orientation_index]),
                ))
        except (FrameError, struct.error, ValueError, IndexError):
            decode_errors += 1
    samples.sort(key=lambda sample: (sample.availability_time_s, sample.measurement_time_s, sample.source_sequence))
    audit = {
        "node": node,
        "records": records,
        "samples": len(samples),
        "decode_errors": decode_errors,
        "timer_map": {"slope": timer_slope, "intercept": timer_intercept,
                      "residual_abs_p95_s": float(np.quantile(np.abs(timer_residual), 0.95)),
                      "residual_abs_max_s": float(np.max(np.abs(timer_residual)))},
        "availability_map": {"slope": arrival_slope, "intercept": arrival_intercept,
                             "paired_margin_min_s": float(np.min(arrival_residual)),
                             "paired_margin_p95_s": float(np.quantile(arrival_residual, 0.95)),
                             "paired_margin_max_s": float(np.max(arrival_residual))},
        "future_imu_count": int(sum(sample.measurement_time_s > sample.availability_time_s + 1e-12 for sample in samples)),
        "orientation_policy": "strict latest frozen M1 q_GS with time <= IMU measurement time",
    }
    return samples, audit


def estimate_static_accelerometer_bias(samples: Iterable[ImuSample], start_s: float, end_s: float) -> tuple[np.ndarray, dict]:
    rows = [sample for sample in samples if start_s <= sample.measurement_time_s <= end_s and sample.m1_valid]
    if len(rows) < 200:
        raise ValueError("insufficient static pelvis IMU samples")
    candidates = np.asarray([
        sample.specific_force_sensor_mps2 + sample.rotation_world_from_sensor.T @ np.array([0.0, 0.0, -9.80665])
        for sample in rows
    ])
    bias = np.median(candidates, axis=0)
    residual_world = np.asarray([
        sample.rotation_world_from_sensor @ (sample.specific_force_sensor_mps2 - bias) + np.array([0.0, 0.0, -9.80665])
        for sample in rows
    ])
    return bias, {
        "samples": len(rows),
        "window_s": [float(start_s), float(end_s)],
        "bias_sensor_mps2": bias.tolist(),
        "residual_world_mps2_median": np.median(residual_world, axis=0).tolist(),
        "residual_world_norm_p95_mps2": float(np.quantile(np.linalg.norm(residual_world, axis=1), 0.95)),
        "method": "component median of f_sensor + R_sensor_from_world*g_world; no C1 UWB used",
    }


def save_imu_cache(path: Path, samples: list[ImuSample], source_hashes: dict[str, str]) -> None:
    np.savez_compressed(
        path,
        measurement_time_s=np.asarray([sample.measurement_time_s for sample in samples], np.float64),
        availability_time_s=np.asarray([sample.availability_time_s for sample in samples], np.float64),
        specific_force_sensor_mps2=np.asarray([sample.specific_force_sensor_mps2 for sample in samples], np.float32),
        rotation_world_from_sensor=np.asarray([sample.rotation_world_from_sensor for sample in samples], np.float32),
        source_sequence=np.asarray([sample.source_sequence for sample in samples], np.uint16),
        m1_valid=np.asarray([sample.m1_valid for sample in samples], bool),
        m1_reset=np.asarray([sample.m1_reset for sample in samples], bool),
        source_hashes_json=np.asarray(json.dumps(source_hashes, sort_keys=True)),
    )


def load_imu_cache(path: Path, source_hashes: dict[str, str]) -> list[ImuSample]:
    with np.load(path, allow_pickle=False) as archive:
        cached_hashes = json.loads(str(archive["source_hashes_json"]))
        if cached_hashes != source_hashes:
            raise ValueError("pelvis IMU cache source hash mismatch")
        measurement_time_s = archive["measurement_time_s"]
        availability_time_s = archive["availability_time_s"]
        specific_force_sensor_mps2 = archive["specific_force_sensor_mps2"]
        rotation_world_from_sensor = archive["rotation_world_from_sensor"]
        source_sequence = archive["source_sequence"]
        m1_valid = archive["m1_valid"]
        m1_reset = archive["m1_reset"]
        return [ImuSample(
            float(measurement_time_s[index]),
            float(availability_time_s[index]),
            specific_force_sensor_mps2[index].astype(float),
            rotation_world_from_sensor[index].astype(float),
            int(source_sequence[index]),
            bool(m1_valid[index]), bool(m1_reset[index]),
        ) for index in range(len(measurement_time_s))]
