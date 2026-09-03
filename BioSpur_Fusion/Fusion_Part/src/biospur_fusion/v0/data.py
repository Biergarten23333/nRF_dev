"""IMU-only adapter for exact Capture1 ledger members and windows."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import struct
from typing import Any
import zipfile

import numpy as np

from .contracts import HELD_OUT_LABELS, NODES, SUPERSEDED_INITIAL_STILL, WINDOWS, sha256_file


G = 9.80665


def _stored_npy_memmap(npz_path: Path, member: str) -> tuple[np.memmap, dict[str, Any]]:
    """Map one exact ZIP_STORED NPY member without importing mixed UWB code."""
    with zipfile.ZipFile(npz_path) as archive:
        info = archive.getinfo(member)
        if info.compress_type != zipfile.ZIP_STORED:
            raise RuntimeError(f"{member} is not ZIP_STORED")
    with Path(npz_path).open("rb") as handle:
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
        raise RuntimeError("ledger members must be one-dimensional C-order arrays")
    return np.memmap(npz_path, dtype=dtype, mode="r", offset=offset, shape=shape), {
        "member": member,
        "member_rows": int(shape[0]),
        "member_uncompressed_bytes": int(info.file_size),
        "zip_compression": "ZIP_STORED",
    }


def load_capture1_imu_only(ledger: Path) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, Any]]:
    """Open only ``imu_<node>.npy`` ZIP members and exact authorized slices.

    The NPZ central directory may also contain spatial payloads, but this
    adapter never requests, maps, hashes, copies, or decodes those members.
    """
    ledger = Path(ledger).resolve()
    windows: dict[str, dict[str, np.ndarray]] = {label: {} for label, _, _ in WINDOWS}
    audit: dict[str, Any] = {
        "schema": "biospur-fusion-v0-imu-only-ledger-access-v1",
        "ledger": str(ledger),
        "ledger_sha256": sha256_file(ledger),
        "member_selection_rule": "exact imu_<corrected-node>.npy names only",
        "opened_members": [],
        "spatial_members_opened": [],
        "allowed_common_time_field": "global_time_ns",
        "windows": {},
        "superseded_initial_still_rejected": list(SUPERSEDED_INITIAL_STILL),
        "held_out_payloads_opened": [],
    }
    mapped: dict[str, np.memmap] = {}
    for node in NODES:
        member = f"imu_{node}.npy"
        rows, metadata = _stored_npy_memmap(ledger, member)
        mapped[node] = rows
        audit["opened_members"].append(member)
        audit.setdefault("members", {})[node] = metadata
    for label, start_ns, stop_ns in WINDOWS:
        window_audit: dict[str, Any] = {
            "start_global_time_ns": start_ns,
            "stop_global_time_ns_exclusive": stop_ns,
            "role": "CAPTURE1_CALIBRATION_REPLAY_VERIFICATION",
            "nodes": {},
        }
        for node in NODES:
            rows = mapped[node]
            left = int(np.searchsorted(rows["global_time_ns"], start_ns, side="left"))
            right = int(np.searchsorted(rows["global_time_ns"], stop_ns, side="left"))
            sliced = np.asarray(rows[left:right]).copy()
            accepted = sliced[sliced["status"] == 1]
            if len(accepted) < 2:
                raise RuntimeError(f"{label}/{node}: insufficient accepted IMU rows")
            times = accepted["global_time_ns"]
            if np.any(times < start_ns) or np.any(times >= stop_ns) or np.any(np.diff(times) <= 0):
                raise RuntimeError(f"{label}/{node}: invalid native time")
            if not np.isfinite(accepted["acc_raw"].astype(float)).all() or not np.isfinite(
                accepted["gyro_raw"].astype(float)
            ).all():
                raise RuntimeError(f"{label}/{node}: non-finite IMU sample")
            windows[label][node] = accepted
            window_audit["nodes"][node] = {
                "slice_start_index": left,
                "slice_stop_index": right,
                "accepted_rows": int(len(accepted)),
                "first_time_ns": int(times[0]),
                "last_time_ns": int(times[-1]),
                "boot_epochs": [int(x) for x in np.unique(accepted["boot_epoch"])],
                "payload_sha256": hashlib.sha256(accepted.tobytes()).hexdigest(),
            }
        audit["windows"][label] = window_audit
    audit["opened_members"].sort()
    audit["imu_only_selection_pass"] = (
        audit["opened_members"] == sorted(f"imu_{node}.npy" for node in NODES)
        and not audit["spatial_members_opened"]
    )
    return windows, audit


def load_authorized_action_imu_only(
    ledger: Path,
    *,
    action_label: str,
    start_ns: int,
    stop_ns: int,
    expected_ledger_sha256: str,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Load one predeclared action while decoding only exact IMU members.

    Selection and role authorization happen before this boundary.  This
    function deliberately has no action-discovery behavior: it accepts one
    exact half-open interval, verifies the container hash, and maps only the
    ten ``imu_<node>.npy`` members.
    """
    ledger = Path(ledger).resolve()
    normalized = action_label.strip().lower().replace("-", "_").replace(" ", "_")
    if any(label in normalized for label in HELD_OUT_LABELS):
        raise ValueError("held-out Golf/Boxing action is forbidden")
    if int(stop_ns) <= int(start_ns):
        raise ValueError("action window must be a non-empty half-open interval")
    ledger_sha = sha256_file(ledger)
    if ledger_sha != expected_ledger_sha256:
        raise ValueError("action ledger SHA-256 does not match the predeclaration")
    audit: dict[str, Any] = {
        "schema": "biospur-fusion-v0-authorized-action-imu-access-v1",
        "ledger": str(ledger),
        "ledger_sha256": ledger_sha,
        "action_label": action_label,
        "start_global_time_ns": int(start_ns),
        "stop_global_time_ns_exclusive": int(stop_ns),
        "member_selection_rule": "exact imu_<corrected-node>.npy names only",
        "opened_members": [],
        "opened_payload_classes": ["TEN_NODE_IMU", "GLOBAL_TIME_NS_WITHIN_IMU_ROWS"],
        "spatial_members_opened": [],
        "held_out_payloads_opened": [],
        "nodes": {},
    }
    output: dict[str, np.ndarray] = {}
    for node in NODES:
        member = f"imu_{node}.npy"
        rows, metadata = _stored_npy_memmap(ledger, member)
        audit["opened_members"].append(member)
        left = int(np.searchsorted(rows["global_time_ns"], int(start_ns), side="left"))
        right = int(np.searchsorted(rows["global_time_ns"], int(stop_ns), side="left"))
        sliced = np.asarray(rows[left:right]).copy()
        accepted = sliced[sliced["status"] == 1]
        if len(accepted) < 2:
            raise RuntimeError(f"{action_label}/{node}: insufficient accepted IMU rows")
        times = accepted["global_time_ns"]
        if (
            np.any(times < int(start_ns))
            or np.any(times >= int(stop_ns))
            or np.any(np.diff(times) <= 0)
        ):
            raise RuntimeError(f"{action_label}/{node}: invalid native time")
        if (
            not np.isfinite(accepted["acc_raw"].astype(float)).all()
            or not np.isfinite(accepted["gyro_raw"].astype(float)).all()
        ):
            raise RuntimeError(f"{action_label}/{node}: non-finite IMU sample")
        output[node] = accepted
        audit["nodes"][node] = {
            **metadata,
            "slice_start_index": left,
            "slice_stop_index": right,
            "accepted_rows": int(len(accepted)),
            "first_time_ns": int(times[0]),
            "last_time_ns": int(times[-1]),
            "boot_epochs": [int(value) for value in np.unique(accepted["boot_epoch"])],
            "payload_sha256": hashlib.sha256(accepted.tobytes()).hexdigest(),
        }
    audit["opened_members"].sort()
    audit["ten_node_coverage_complete"] = set(output) == set(NODES)
    audit["imu_only_selection_pass"] = bool(
        audit["opened_members"] == sorted(f"imu_{node}.npy" for node in NODES)
        and not audit["spatial_members_opened"]
        and not audit["held_out_payloads_opened"]
        and audit["ten_node_coverage_complete"]
    )
    return output, audit


def si_samples(rows: np.ndarray, signed_axis: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    signed = np.eye(3) if signed_axis is None else np.asarray(signed_axis, float)
    if signed.shape != (3, 3) or not np.allclose(signed.T @ signed, np.eye(3), atol=1e-12):
        raise ValueError("signed-axis representative must be orthogonal")
    acc = rows["acc_raw"].astype(float) / 2048.0 * G
    gyr = np.deg2rad(rows["gyro_raw"].astype(float) / 16.384)
    return (signed @ acc.T).T, (signed @ gyr.T).T
