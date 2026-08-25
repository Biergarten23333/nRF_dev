"""Independent utilities for the Root-R6A2A-R1 execution audit.

This module is deliberately outside the estimator package.  It may observe and
replay the sealed synthetic scenarios, but it does not alter Root-R6A2A source,
thresholds, scenarios, or configuration.
"""
from __future__ import annotations

from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from biospur_fusion.root_r6a0.math3d import so3_exp, so3_log


NOT_RECORDED = "NOT_RECORDED_IN_PARENT"
PARENT_NAME = "root_r6a2a_synthetic_fault_aware_shadow_20260825T114046Z"
PARENT_SHA256SUMS_SHA = "ab17eed4c1c8e37079f844688100572c5515b2a3121b3cecf6d4f0086978b7f7"
CHECKPOINT_HEAD = "ec451cf140b25e7dbe545e3e09d50b0d3d6edbe8"

IMPLEMENTATION_PATHS = (
    "src/biospur_fusion/root_r6a2a/__init__.py",
    "src/biospur_fusion/root_r6a2a/contracts.py",
    "src/biospur_fusion/root_r6a2a/shadow.py",
    "src/biospur_fusion/root_r6a2a/qualification.py",
    "tests/root_r6a2a/conftest.py",
    "tests/root_r6a2a/test_integrated_shadow.py",
    "tests/root_r6a2a/generate_result.py",
    "tests/root_r6a2a/verify_result.py",
)

CONFIG_PATHS = (
    "config/root_r6a0/body_graph.json",
    "logs/root_r6a1c_bsf31cc_hardware_addendum_20260825T105220Z/HARDWARE_FAMILY_BINDING.json",
    "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z/CALIBRATION_SLOT_LEDGER.json",
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=True
    ).encode()


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))


def jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted(jsonable(item) for item in value)
    return value


def write_json(path: Path, value: Any) -> None:
    Path(path).write_text(
        json.dumps(jsonable(value), indent=2, sort_keys=True, allow_nan=False) + "\n"
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with Path(path).open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(jsonable(row), sort_keys=True, allow_nan=False) + "\n")


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set, np.ndarray)):
        return json.dumps(jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, np.generic):
        return value.item()
    return value


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str] | None = None) -> None:
    rows = list(rows)
    if fields is None:
        fields = []
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    fields.append(key)
                    seen.add(key)
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fields})


def file_inventory(root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(Path(root).iterdir()):
        if not path.is_file():
            continue
        row: dict[str, Any] = {
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        if path.suffix == ".json":
            payload = json.loads(path.read_text())
            row["schema"] = payload.get("schema", NOT_RECORDED) if isinstance(payload, dict) else NOT_RECORDED
            row["top_level_fields"] = list(payload) if isinstance(payload, dict) else []
        else:
            row["schema"] = NOT_RECORDED
            row["top_level_fields"] = []
        rows.append(row)
    return rows


def snapshot_files(fusion: Path, paths: Sequence[str]) -> dict[str, Any]:
    return {
        path: {
            "sha256": sha256_file(Path(fusion) / path),
            "size_bytes": (Path(fusion) / path).stat().st_size,
        }
        for path in paths
    }


def parent_snapshot(parent: Path) -> dict[str, Any]:
    return {
        row["name"]: {"sha256": row["sha256"], "size_bytes": row["size_bytes"]}
        for row in file_inventory(parent)
    }


def state_vector(state: Any, model: Any) -> np.ndarray:
    return np.concatenate(
        (
            np.asarray(state.root_translation_model_m, float),
            np.asarray(state.root_rotation_model_rotvec, float),
            np.asarray(state.root_velocity_model_mps, float),
            *(np.asarray(state.joint_rotvec[joint], float) for joint in model.joint_ids),
            *(np.asarray(state.joint_rate_rad_s[joint], float) for joint in model.joint_ids),
            *(np.asarray(state.gyro_bias_rad_s[node], float) for node in model.imu_ids),
            *(np.asarray(state.accel_bias_mps2[node], float) for node in model.imu_ids),
        )
    )


def tangent_error(estimate: Any, truth: Any, model: Any) -> np.ndarray:
    root_rotation = so3_log(
        so3_exp(truth.root_rotation_model_rotvec).T
        @ so3_exp(estimate.root_rotation_model_rotvec)
    )
    joint_rotations = [
        so3_log(
            so3_exp(truth.joint_rotvec[joint]).T
            @ so3_exp(estimate.joint_rotvec[joint])
        )
        for joint in model.joint_ids
    ]
    return np.concatenate(
        (
            np.asarray(estimate.root_translation_model_m) - np.asarray(truth.root_translation_model_m),
            root_rotation,
            np.asarray(estimate.root_velocity_model_mps) - np.asarray(truth.root_velocity_model_mps),
            *joint_rotations,
            *(np.asarray(estimate.joint_rate_rad_s[joint]) - np.asarray(truth.joint_rate_rad_s[joint]) for joint in model.joint_ids),
            *(np.asarray(estimate.gyro_bias_rad_s[node]) - np.asarray(truth.gyro_bias_rad_s[node]) for node in model.imu_ids),
            *(np.asarray(estimate.accel_bias_mps2[node]) - np.asarray(truth.accel_bias_mps2[node]) for node in model.imu_ids),
        )
    )


def state_order(model: Any) -> list[str]:
    labels = [
        "root_position_x_m", "root_position_y_m", "root_position_z_m",
        "root_orientation_x_rad", "root_orientation_y_rad", "root_orientation_z_rad",
        "root_velocity_x_mps", "root_velocity_y_mps", "root_velocity_z_mps",
    ]
    for prefix, identifiers, unit in (
        ("joint_orientation", model.joint_ids, "rad"),
        ("joint_rate", model.joint_ids, "rad_s"),
        ("gyro_bias", model.imu_ids, "rad_s"),
        ("accel_bias", model.imu_ids, "mps2"),
    ):
        for identifier in identifiers:
            for axis in "xyz":
                labels.append(f"{prefix}:{identifier}:{axis}_{unit}")
    return labels


def rotation_error_rad(first_rotvec: np.ndarray, second_rotvec: np.ndarray) -> float:
    return float(np.linalg.norm(so3_log(so3_exp(first_rotvec).T @ so3_exp(second_rotvec))))


def numeric_summary(values: Sequence[float]) -> dict[str, float | int | None]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if array.size == 0:
        return {"count": 0, "rmse": None, "mean": None, "median": None, "p95": None, "max": None}
    return {
        "count": int(array.size),
        "rmse": float(np.sqrt(np.mean(np.square(array)))),
        "mean": float(np.mean(array)),
        "median": float(np.median(array)),
        "p95": float(np.quantile(array, 0.95)),
        "max": float(np.max(array)),
    }


def phase_for_step(spec: Any, step_index: int | None, mode: str) -> str:
    if spec.fault == "none":
        return "CLEAN"
    if step_index is None or step_index < spec.fault_start_step:
        return "PRE_FAULT"
    if step_index <= spec.fault_end_step:
        return "DURING_FAULT"
    if mode in {"RECOVERY_PENDING", "CONTROLLED_REENTRY"}:
        return "RECOVERY"
    return "POST_FAULT"


def multiset_changed_count(first: Sequence[Mapping[str, Any]], second: Sequence[Mapping[str, Any]]) -> int:
    a = Counter(json.dumps(jsonable(row), sort_keys=True, separators=(",", ":")) for row in first)
    b = Counter(json.dumps(jsonable(row), sort_keys=True, separators=(",", ":")) for row in second)
    exact_matches = sum((a & b).values())
    return max(sum(a.values()), sum(b.values())) - exact_matches


def source_line(path: Path, needle: str) -> int:
    for index, line in enumerate(Path(path).read_text().splitlines(), 1):
        if needle in line:
            return index
    raise ValueError(f"{needle!r} not found in {path}")

