#!/usr/bin/env python3
"""Audit whether preserved C2 derived artifacts can identify nonhinge heading.

This is a derived-artifact closure audit.  It never opens a training payload or
held-out source and it does not execute calibration, QMT, fitting, or rendering.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
SPRINT = RUN / "CONTINUATION_SPRINT"
FRESH_MANIFEST = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.json"
FRESH_NPZ = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.npz"
OUTPUT = SPRINT / "C2_FRESH_NONHINGE_INPUT_CLOSURE_AUDIT_001.json"
NONHINGE_EDGES = (
    "pelvis_torso",
    "shoulder_left",
    "shoulder_right",
    "hip_left",
    "hip_right",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_new(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def _is_calibrated_accelerometer_series_key(key: str) -> bool:
    leaf = key.rsplit("/", 1)[-1]
    return leaf in {"acc_mps2", "accelerometer_mps2"}


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("nonhinge input closure audit requires canonical Fusion_Part")
    manifest = json.loads(FRESH_MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("heldout_opened") is not False:
        raise RuntimeError("fresh manifest does not prove held-out remained closed")

    containers: list[dict[str, Any]] = []
    real_series_paths: list[str] = []
    all_npz = sorted(RUN.rglob("*.npz"))
    for path in all_npz:
        relative = path.relative_to(WORKSPACE).as_posix()
        synthetic_renderer_fixture = (
            "/tmp/renderer_smoke_" in f"/{relative}"
            and path.name == "synthetic_frozen_renderer_input.npz"
        )
        with np.load(path, allow_pickle=False) as arrays:
            series_keys = sorted(
                key for key in arrays.files
                if _is_calibrated_accelerometer_series_key(key)
            )
            nuisance_only_keys = sorted(
                key for key in arrays.files
                if "accelerometer" in key.lower()
                and key not in series_keys
            )
            containers.append({
                "path": relative,
                "sha256": _sha256(path),
                "array_key_count": len(arrays.files),
                "synthetic_renderer_fixture_excluded_from_real_evidence": (
                    synthetic_renderer_fixture
                ),
                "calibrated_accelerometer_series_keys": series_keys,
                "accelerometer_named_nuisance_or_covariance_key_count": len(
                    nuisance_only_keys
                ),
            })
            if series_keys and not synthetic_renderer_fixture:
                real_series_paths.append(relative)

    edge_counts: dict[str, set[int]] = {edge: set() for edge in NONHINGE_EDGES}
    edge_state_rows = 0
    with np.load(FRESH_NPZ, allow_pickle=False) as fresh:
        for key in fresh.files:
            prefix = "frozen/heading_edge_state/"
            if not key.startswith(prefix):
                continue
            edge = key.rsplit(":", 1)[-1]
            if edge not in edge_counts:
                continue
            value = np.asarray(fresh[key], dtype=float)
            if value.shape != (4,):
                raise RuntimeError(f"unexpected heading edge state shape: {key}")
            edge_counts[edge].add(int(np.rint(value[3])))
            edge_state_rows += 1
    if edge_state_rows == 0 or any(values != {0} for values in edge_counts.values()):
        raise RuntimeError("frozen nonhinge edge observations are not exactly zero")
    if real_series_paths:
        raise RuntimeError("a preserved real calibrated accelerometer series was overlooked")

    output = {
        "schema": "biospur-c2-fresh-nonhinge-input-closure-audit-v1",
        "created_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        "authority_scope": {
            "derived_artifacts_only": True,
            "raw_payload_read": False,
            "heldout_opened": False,
            "fit_qmt_or_progressive_state_modified": False,
            "render_executed": False,
        },
        "frozen_bindings": {
            "fresh_manifest": {
                "path": FRESH_MANIFEST.relative_to(WORKSPACE).as_posix(),
                "sha256": _sha256(FRESH_MANIFEST),
            },
            "fresh_npz": {
                "path": FRESH_NPZ.relative_to(WORKSPACE).as_posix(),
                "sha256": _sha256(FRESH_NPZ),
            },
        },
        "persisted_array_container_inventory": containers,
        "real_non_synthetic_calibrated_accelerometer_series_paths": real_series_paths,
        "frozen_nonhinge_heading_edge_state": {
            "edge_state_row_count": edge_state_rows,
            "observation_counts_by_edge": {
                edge: sorted(values) for edge, values in edge_counts.items()
            },
            "all_five_edges_have_exactly_zero_effective_observations": True,
        },
        "causal_boundary": {
            "unique_full_body_pose_selectable_from_current_frozen_arrays": False,
            "reason": (
                "No preserved real calibrated accelerometer time series exists, and "
                "all five nonhinge persistent heading edge states have zero effective "
                "observations. A unique heading would require unauthorized ROM, manual, "
                "or pixel assumptions."
            ),
            "minimum_future_owner_input_action": (
                "At an authorized chronological runtime freeze, atomically persist each "
                "OrientedAction acc_mps2 array with its exact time_us, boot epoch, span, "
                "gyro, quaternion, and gap covariance. Then an edge-local nonhinge "
                "likelihood may consume existing gap-safe AlignedPair maps and full-R3 "
                "center levers, retaining broad or multimodal heading when weak."
            ),
            "current_frozen_arrays_may_be_backfilled_or_inferred": False,
            "current_8_point_nonhinge_support_role": (
                "factorized uncertainty/provenance diagnostic only"
            ),
        },
        "source_hashes_after_future_export_fix": {
            path.relative_to(WORKSPACE).as_posix(): _sha256(path)
            for path in (
                WORKSPACE / "src/biospur_fusion/v0/c2_progressive/pipeline_runtime.py",
                WORKSPACE / "src/biospur_fusion/v0/c2_progressive/orientation.py",
                WORKSPACE / "tests/v0/test_c2_p2_prefit_owners.py",
            )
        },
        "scientific_acceptance_pass": False,
        "tuned_human_pose_pass": False,
    }
    _write_new(OUTPUT, output)
    print(json.dumps({
        "output": OUTPUT.relative_to(WORKSPACE).as_posix(),
        "sha256": _sha256(OUTPUT),
        "npz_count": len(containers),
        "real_calibrated_accelerometer_series_count": 0,
        "nonhinge_edge_state_rows": edge_state_rows,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
