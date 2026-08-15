"""Build immutable payload-separated calibration and held-out ledgers."""
from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np


@dataclass(frozen=True)
class LedgerWindow:
    name: str
    start_ns: int
    stop_ns: int


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(4 << 20):
            digest.update(block)
    return digest.hexdigest()


def _select(data: np.ndarray, windows: tuple[LedgerWindow, ...]) -> np.ndarray:
    accepted = data["status"] == 1
    selected = np.zeros(len(data), dtype=bool)
    for window in windows:
        selected |= ((data["global_time_ns"] >= window.start_ns)
                     & (data["global_time_ns"] <= window.stop_ns))
    return data[accepted & selected].copy()


def _savez_deterministic(path: Path, arrays: Mapping[str, np.ndarray]) -> None:
    """Write byte-identical NPZ archives (NumPy's ZIP timestamp is ambient)."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for key in sorted(arrays):
            payload = io.BytesIO()
            np.lib.format.write_array(payload, np.asarray(arrays[key]), allow_pickle=False)
            info = zipfile.ZipInfo(f"{key}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o600 << 16
            archive.writestr(info, payload.getvalue())


def materialize_payload_firewall(
    source_npz: Path,
    calibration_npz: Path,
    heldout_npz: Path,
    *,
    calibration_window: LedgerWindow,
    heldout_windows: tuple[LedgerWindow, ...],
    calibration_actions: tuple[LedgerWindow, ...] = (),
) -> dict:
    """Write disjoint ledgers; neither output contains the other's payloads."""
    if calibration_window.stop_ns >= min(window.start_ns for window in heldout_windows):
        raise ValueError("calibration and held-out time ranges are not ordered/disjoint")
    calibration_npz.parent.mkdir(parents=True, exist_ok=True)
    heldout_npz.parent.mkdir(parents=True, exist_ok=True)
    calibration_arrays: dict[str, np.ndarray] = {}
    heldout_arrays: dict[str, np.ndarray] = {}
    accounting: dict[str, Mapping[str, int]] = {}
    with np.load(source_npz, allow_pickle=False) as source:
        for key in sorted(source.files):
            calibration = _select(source[key], (calibration_window,))
            heldout = _select(source[key], heldout_windows)
            if np.intersect1d(calibration["raw_record_index"], heldout["raw_record_index"]).size:
                raise RuntimeError(f"raw-record overlap crosses firewall for {key}")
            calibration_arrays[key] = calibration
            heldout_arrays[key] = heldout
            accounting[key] = {"calibration": len(calibration), "heldout": len(heldout)}
    action_dtype = np.dtype([("name", "U32"), ("start_ns", "<i8"), ("stop_ns", "<i8")])
    calibration_arrays["action_windows"] = np.asarray(
        [(row.name, row.start_ns, row.stop_ns) for row in calibration_actions],
        dtype=action_dtype,
    )
    heldout_arrays["action_windows"] = np.asarray(
        [(row.name, row.start_ns, row.stop_ns) for row in heldout_windows],
        dtype=action_dtype,
    )
    _savez_deterministic(calibration_npz, calibration_arrays)
    _savez_deterministic(heldout_npz, heldout_arrays)
    return {
        "schema": "biospur-payload-firewall-v1",
        "source_ledger_sha256": sha256(source_npz),
        "calibration": {
            "path": "CALIBRATION_TYPED_LEDGER.npz",
            "sha256": sha256(calibration_npz),
            "window": calibration_window.__dict__,
            "records": sum(value["calibration"] for value in accounting.values()),
        },
        "heldout": {
            "path": "HELDOUT_TYPED_LEDGER.npz",
            "sha256": sha256(heldout_npz),
            "windows": [window.__dict__ for window in heldout_windows],
            "records": sum(value["heldout"] for value in accounting.values()),
        },
        "per_array": accounting,
        "calibration_actions": [window.__dict__ for window in calibration_actions],
        "payload_overlap": False,
    }
