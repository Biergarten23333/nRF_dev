"""Continuous raw-IMU orientation replay for sealed Capture2 holdouts.

This module owns transport decoding and the one-state-per-node VQF lifecycle.
It deliberately does not fit any calibration parameter from holdout payloads.
"""

from __future__ import annotations

import binascii
from collections import Counter
from pathlib import Path
import struct
from typing import Any, Iterable, Mapping

import numpy as np
import qmt
from scipy.spatial.transform import Rotation

from .contracts import NODE_TO_SEGMENT
from .math_utils import (
    interp_quat_wxyz,
    normalize_quat_wxyz,
    qmt_wxyz_to_rotation,
    rotation_to_qmt_wxyz,
)


G = 9.80665
SAMPLE_PERIOD_S = 0.005
ENVELOPE_HEADER = struct.Struct("<HBBHHIQ")
IMU_SAMPLE = struct.Struct("<Hhhhhhh")


def _cobs_decode(encoded: bytes) -> bytes:
    output = bytearray()
    cursor = 0
    while cursor < len(encoded):
        code = encoded[cursor]
        cursor += 1
        if code == 0 or cursor + code - 1 > len(encoded):
            raise ValueError("invalid COBS")
        output.extend(encoded[cursor : cursor + code - 1])
        cursor += code - 1
        if code != 0xFF and cursor < len(encoded):
            output.append(0)
    return bytes(output)


def _imu_envelope(encoded: bytes) -> tuple[str, bytes] | None:
    raw = _cobs_decode(encoded)
    if len(raw) < ENVELOPE_HEADER.size + 2:
        raise ValueError("short envelope")
    body = raw[:-2]
    expected = struct.unpack_from("<H", raw, len(raw) - 2)[0]
    if binascii.crc_hqx(body, 0xFFFF) != expected:
        raise ValueError("CRC")
    magic, version, kind, node_id, length, _sequence, _master_ms = (
        ENVELOPE_HEADER.unpack_from(body)
    )
    payload = body[ENVELOPE_HEADER.size :]
    if magic != 0x5342 or version != 1 or len(payload) != length:
        raise ValueError("envelope contract")
    node = f"BSF{node_id:04X}"
    if kind != 3 or node not in NODE_TO_SEGMENT:
        return None
    return node, payload


def _complete_cobs_frames(
    path: Path,
    start_byte: int,
    stop_byte: int,
    *,
    chunk_bytes: int = 4 * 1024 * 1024,
) -> Iterable[tuple[int, int, bytes]]:
    """Yield complete encoded frames and their absolute byte interval."""

    size = path.stat().st_size
    if not 0 <= start_byte < stop_byte <= size:
        raise ValueError("invalid continuous raw interval")
    carry = b""
    carry_start = start_byte
    with path.open("rb", buffering=0) as stream:
        stream.seek(start_byte)
        remaining = stop_byte - start_byte
        while remaining:
            chunk = stream.read(min(chunk_bytes, remaining))
            if not chunk:
                raise IOError("short continuous raw read")
            remaining -= len(chunk)
            data = carry + chunk
            data_start = carry_start
            cursor = 0
            while True:
                delimiter = data.find(b"\0", cursor)
                if delimiter < 0:
                    break
                yield (
                    data_start + cursor,
                    data_start + delimiter + 1,
                    data[cursor:delimiter],
                )
                cursor = delimiter + 1
            carry = data[cursor:]
            carry_start = data_start + cursor
    if carry:
        raise RuntimeError("raw stop is not a complete COBS boundary")


def continuous_vqf_holdout_samples(
    raw_path: Path,
    start_byte: int,
    stop_byte: int,
    holdout_ranges: Mapping[str, tuple[int, int]],
) -> tuple[dict[str, dict[str, dict[str, np.ndarray]]], dict[str, Any]]:
    """Run one uninterrupted VQF state per node and retain only Hxx rows."""

    blocks = {
        node: qmt.OriEstVQFBlock(SAMPLE_PERIOD_S)
        for node in NODE_TO_SEGMENT
    }
    last_timer: dict[str, int] = {}
    sample_count: Counter[str] = Counter()
    error_count: Counter[str] = Counter()
    resets: Counter[str] = Counter()
    stored: dict[str, dict[str, list[tuple[int, np.ndarray]]]] = {
        action: {node: [] for node in NODE_TO_SEGMENT}
        for action in holdout_ranges
    }
    for raw_start, raw_stop, encoded in _complete_cobs_frames(
        raw_path, start_byte, stop_byte
    ):
        if not encoded:
            continue
        try:
            decoded = _imu_envelope(encoded)
            if decoded is None:
                continue
            node, payload = decoded
            if len(payload) < 14:
                raise ValueError("short IMU header")
            version, count, _sequence, base_us = struct.unpack_from(
                "<BBHQ", payload, 0
            )
            if (
                version != 7
                or not 1 <= count <= 16
                or len(payload) != 14 + count * IMU_SAMPLE.size
            ):
                raise ValueError("IMU contract")
            action = next(
                (
                    name
                    for name, (left, right) in holdout_ranges.items()
                    if raw_start >= left and raw_stop <= right
                ),
                None,
            )
            for index in range(count):
                delta, ax, ay, az, gx, gy, gz = IMU_SAMPLE.unpack_from(
                    payload, 14 + index * IMU_SAMPLE.size
                )
                timer_us = int(base_us + delta)
                if node in last_timer and timer_us < last_timer[node]:
                    resets[node] += 1
                last_timer[node] = timer_us
                acc = np.array([ax, ay, az], dtype=float) / 2048.0 * G
                gyr = np.deg2rad(
                    np.array([gx, gy, gz], dtype=float) / 16.384
                )
                quat = np.asarray(blocks[node].step(gyr, acc, None), dtype=float)
                sample_count[node] += 1
                if action is not None:
                    stored[action][node].append((timer_us, quat.copy()))
        except (ValueError, struct.error, IndexError) as exc:
            error_count[f"{type(exc).__name__}:{exc}"] += 1
    if any(resets.values()):
        raise RuntimeError(f"node timer reset inside continuous replay: {dict(resets)}")
    output: dict[str, dict[str, dict[str, np.ndarray]]] = {}
    for action, nodes in stored.items():
        output[action] = {}
        for node, rows in nodes.items():
            if len(rows) < 2:
                raise RuntimeError(f"{action}/{node}: insufficient retained rows")
            output[action][node] = {
                "timer_us": np.array([row[0] for row in rows], dtype=np.int64),
                "quat_world_sensor_wxyz": normalize_quat_wxyz(
                    np.stack([row[1] for row in rows])
                ),
            }
    return output, {
        "implementation": "qmt.OriEstVQFBlock",
        "state_instances_per_node": 1,
        "state_reset_count": 0,
        "raw_span": [int(start_byte), int(stop_byte)],
        "raw_bytes_read": int(stop_byte - start_byte),
        "native_samples_consumed": dict(sample_count),
        "decode_errors": dict(error_count),
        "retained_samples": {
            action: {node: len(rows) for node, rows in nodes.items()}
            for action, nodes in stored.items()
        },
        "holdout_rows_used_for_calibration_fit": 0,
    }


def apply_frozen_avatar_calibration(
    raw_quat_wxyz: np.ndarray,
    pelvis_timer_us: np.ndarray,
    *,
    initial_world_sensor: np.ndarray,
    functional_world_yaw_rad: float,
    common_pelvis_yaw_closure_rad: float,
    reference_time_s: float,
    final_reference_time_s: float,
) -> np.ndarray:
    """Apply one frozen C2 calibration state without reading action semantics."""

    time_s = np.asarray(pelvis_timer_us, dtype=float) * 1e-6
    duration = final_reference_time_s - reference_time_s
    if duration <= 0:
        raise ValueError("invalid frozen reference interval")
    alpha = (time_s - reference_time_s) / duration
    common = -alpha * common_pelvis_yaw_closure_rad
    yaw = common + float(functional_world_yaw_rad)
    rotvec = np.zeros((len(yaw), 3), dtype=float)
    rotvec[:, 2] = yaw
    corrected = (
        Rotation.from_rotvec(rotvec).as_matrix()
        @ qmt_wxyz_to_rotation(raw_quat_wxyz).as_matrix()
        @ np.asarray(initial_world_sensor, dtype=float).reshape(3, 3).T
    )
    return normalize_quat_wxyz(
        rotation_to_qmt_wxyz(Rotation.from_matrix(corrected))
    )


def synchronize_holdout_quaternions(
    rows: Mapping[str, Mapping[str, np.ndarray]],
    node_models: Mapping[str, Any],
    start_ns: int,
    stop_ns: int,
    *,
    grid_period_ns: int = 50_000_000,
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray], dict[str, Any]]:
    """Map native rows to one common grid while preserving quaternion SLERP."""

    mapped: dict[str, np.ndarray] = {}
    for node, row in rows.items():
        mapped[node] = np.array(
            [node_models[node].map_ns(int(value)) for value in row["timer_us"]],
            dtype=np.int64,
        )
        if np.any(np.diff(mapped[node]) <= 0):
            raise RuntimeError(f"{node}: non-monotone mapped Hxx time")
    left = max(start_ns, max(int(value[0]) for value in mapped.values()))
    right = min(stop_ns, min(int(value[-1]) for value in mapped.values()))
    first = (left // grid_period_ns + 1) * grid_period_ns
    grid = np.arange(first, right, grid_period_ns, dtype=np.int64)
    if len(grid) < 20:
        raise RuntimeError("Hxx common grid is too short")
    synchronized = {
        node: interp_quat_wxyz(
            (mapped[node] - first) * 1e-9,
            rows[node]["quat_world_sensor_wxyz"],
            (grid - first) * 1e-9,
        )
        for node in rows
    }
    pelvis_node = next(
        node for node, segment in NODE_TO_SEGMENT.items() if segment == "pelvis"
    )
    pelvis_timer_us = np.interp(
        grid.astype(float),
        mapped[pelvis_node].astype(float),
        rows[pelvis_node]["timer_us"].astype(float),
    )
    return grid, pelvis_timer_us, synchronized, {
        "grid_period_ns": int(grid_period_ns),
        "grid_rows": int(len(grid)),
        "formal_interval_ns": [int(start_ns), int(stop_ns)],
        "actual_common_interval_ns": [int(grid[0]), int(grid[-1])],
        "native_row_count": {
            node: int(len(row["timer_us"])) for node, row in rows.items()
        },
    }
