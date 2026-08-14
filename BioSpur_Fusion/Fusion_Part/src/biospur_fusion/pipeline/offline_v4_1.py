"""V4.1 orchestration with immutable V3/V4 and held-out firewall."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from biospur_fusion.calibration.anthropometry_v4_1 import validate_anthropometry_v4_1
from biospur_fusion.calibration.centerline_real_capture_v4_1 import run as run_centerline


RAW_SHA = "a491520739400064db520377ec87a9331feb6274cd42a7e6d9aad57a2b93d56a"
LAYOUT_SHA = "20320e53d48b171c016a0e8d1d93b3cb10e979cf4c21c15c21647d5c0b9878b1"
V3_COMMIT = "acbe8c6d3bd337aca20ebf899a52ee414ad7970c"
V4_COMMIT = "94d2818667e56f94ff1314f9aaf6baaf36076073"
V3_MANIFEST_SHA = "9b84160a146072d87f1cbb37bdcba561408418c95e0461a969a3533158388d24"
V4_MANIFEST_SHA = "fa93c9eacd3dfde74be219e6f362db9fd1e62ed8948d73e744486b132390bd7d"
V4_GATES_SHA = "7541339a74f9230693dd38cdaea9aac3292dfabb4d49369b3b1e0cee98c790e3"
GIF_LABEL = "Centerline visualization; axial segment twist and clinical joint angles are not validated."


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(4 << 20):
            digest.update(block)
    return digest.hexdigest()


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _verify_historical(path: Path, commit: str, expected_manifest_sha: str) -> dict:
    manifest = path / "SHA256SUMS"
    mismatches = []
    checked = 0
    actual_manifest_sha = sha(manifest)
    for line in manifest.read_text(encoding="utf-8").splitlines():
        expected, relative = line.split("  ", 1)
        artifact = path / relative
        actual = sha(artifact) if artifact.is_file() else None
        checked += 1
        if actual != expected:
            mismatches.append({"path": relative, "expected": expected, "actual": actual})
    return {
        "path": str(path.resolve()),
        "source_commit": commit,
        "manifest_sha256_expected": expected_manifest_sha,
        "manifest_sha256_actual": actual_manifest_sha,
        "manifest_identity_pass": actual_manifest_sha == expected_manifest_sha,
        "files_checked": checked,
        "mismatches": mismatches,
        "immutable_verified": not mismatches and actual_manifest_sha == expected_manifest_sha,
    }


def _placeholders(output: Path, reason: str, foot_verdict: str) -> None:
    for name, columns in {
        "BODY_STATE_TIMELINE.csv": ["global_time_ns", "status"],
        "SEGMENT_AXES.csv": ["global_time_ns", "segment", "status"],
        "JOINT_CENTRES.csv": ["global_time_ns", "joint", "status"],
        "ANTENNA_POSITIONS.csv": ["global_time_ns", "node", "status"],
        "MEASUREMENT_REJECTION_LEDGER.csv": ["global_time_ns", "node", "reason", "status"],
    }.items():
        with (output / name).open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle, lineterminator="\n").writerow(columns)
    dump(output / "CALIBRATION_FREEZE_MANIFEST.json", {
        "status": "NOT_FROZEN",
        "reason": reason,
        "axial_twist_gauges_unresolved": True,
    })
    dump(output / "HELDOUT_VALIDATION.json", {
        "status": "NOT_OPENED",
        "reason": reason,
        "walk_opened": False,
        "final_still_opened": False,
        "opened_once_after_freeze": False,
    })
    dump(output / "ANIMATION_GATE.json", {
        "pass": False,
        "reason": reason,
        "gif_generated": False,
        "required_label": GIF_LABEL,
    })
    dump(output / "FOOT_RENDERING_VERDICT.json", {
        "verdict": foot_verdict,
        "blocks_centerline_calibration": False,
    })


def run(capture: Path, output: Path, repo: Path) -> dict:
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"immutable V4.1 output already exists: {output}")
    output.mkdir(parents=True, exist_ok=True)

    v3 = capture / "analysis_body_fusion_v3"
    v4 = capture / "analysis_body_fusion_v4"
    immutable = {
        "v3": _verify_historical(v3, V3_COMMIT, V3_MANIFEST_SHA),
        "v4": _verify_historical(v4, V4_COMMIT, V4_MANIFEST_SHA),
    }
    immutable["pass"] = immutable["v3"]["immutable_verified"] and immutable["v4"]["immutable_verified"]
    dump(output / "V3_V4_IMMUTABILITY_AUDIT.json", immutable)
    if not immutable["pass"]:
        raise RuntimeError("V3_V4_IMMUTABILITY_CHECK_FAILED")

    raw = capture / "continuous_collector/fusion_host_raw.cobs.bin"
    layout = repo / "B306_Part/deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json"
    if sha(raw) != RAW_SHA:
        raise RuntimeError("CAPTURE_RAW_SHA_MISMATCH")
    if sha(layout) != LAYOUT_SHA:
        raise RuntimeError("BLOCKED_UWB_GEOMETRY_PROVENANCE")
    old_gates = repo / "Fusion_Part/config/body_calibration_v4/invariance_gates_v1.json"
    if sha(old_gates) != V4_GATES_SHA:
        raise RuntimeError("V4_HISTORICAL_GATE_CHANGED")

    config = repo / "Fusion_Part/config/body_calibration_v4_1"
    schema_path = config / "anthropometry_schema_v4_1.json"
    inputs_path = config / "v47_subject_inputs_v4_1.json"
    gates_path = config / "invariance_gates_v4_1.json"
    anthropometry, audit = validate_anthropometry_v4_1(inputs_path)
    dump(output / "ANTHROPOMETRY_INPUT_AUDIT.json", audit)
    gates = json.loads(gates_path.read_text(encoding="utf-8"))
    dump(output / "PREDECLARED_GATE_MANIFEST.json", {
        "path": str(gates_path.resolve()),
        "sha256": sha(gates_path),
        "declared_before_calibration": True,
        "observability_threshold_loaded_from_gate_json": gates["execution_gates"]["observability_relative_singular_value_threshold"],
        "historical_v4_gate_path": str(old_gates.resolve()),
        "historical_v4_gate_sha256": V4_GATES_SHA,
        "historical_v4_physical_gates_preserved": gates["historical_v4_comparison"],
    })
    dump(output / "INPUT_SCHEMA_MANIFEST.json", {
        "schema_path": str(schema_path.resolve()),
        "schema_sha256": sha(schema_path),
        "input_path": str(inputs_path.resolve()),
        "input_sha256": sha(inputs_path),
        "classes": [
            "DIRECT_SURFACE_MEASUREMENT",
            "DERIVED_JOINT_CENTER",
            "SENSOR_PLACEMENT",
            "RENDERING_ONLY",
        ],
    })

    calibration_ledger = v3 / "ledgers/calibration/CALIBRATION_TYPED_LEDGER.npz"
    result = run_centerline(
        calibration_ledger,
        layout,
        inputs_path,
        gates_path,
        output / "calibration",
    )
    reason = result["verdict"]
    if not result["pass"]:
        _placeholders(output, reason, result["FOOT_RENDERING"])
    else:
        # A separate one-shot held-out runner must consume the serialized freeze.
        # Keeping this stage explicit prevents accidental validation leakage.
        reason = "CENTERLINE_FROZEN_HELDOUT_ONE_SHOT_NOT_RUN_BY_CALIBRATION_STAGE"
        _placeholders(output, reason, result["FOOT_RENDERING"])

    dump(output / "PROVENANCE.json", {
        "capture": str(capture.resolve()),
        "raw_sha256_before": RAW_SHA,
        "raw_sha256_after": sha(raw),
        "raw_modified": False,
        "layout_absolute_path": str(layout.resolve()),
        "layout_sha256": LAYOUT_SHA,
        "uwb_solver": "UWB_TAG_T4",
        "anthropometry_sha256": sha(inputs_path),
        "anthropometry_schema_sha256": sha(schema_path),
        "gates_sha256": sha(gates_path),
        "hardware_accessed": False,
        "calibration_payload_opened": result["calibration_payload_opened"],
        "heldout_payload_opened": False,
        "v3_modified": False,
        "v4_modified": False,
    })
    (output / "REPORT.md").write_text(
        "# Ten-node body Fusion V4.1 measurement-conditioned centerline\n\n"
        f"Top-level verdict: `{reason}`.\n\n"
        f"- `FULL_SEGMENT_POSE_CALIBRATION`: `{result['FULL_SEGMENT_POSE_CALIBRATION']}`\n"
        f"- `STICK_FIGURE_CENTERLINE_CALIBRATION`: `{result['STICK_FIGURE_CENTERLINE_CALIBRATION']}`\n"
        f"- `FOOT_RENDERING`: `{result['FOOT_RENDERING']}`\n\n"
        "V3 and V4 were treated as immutable historical runs; both SHA manifests were fully "
        "verified before V4.1 began. V4.1 separates direct palpable-landmark measurements, "
        "derived internal joint-centre geometry, two-stage sensor placement, and rendering-only "
        "shoe geometry. Foot and ankle rendering inputs do not participate in the centerline "
        "solver gate.\n\n"
        "The historical input remains fail-closed: no direct subject measurements, named/versioned "
        "shoulder or hip derivation, marked antenna phase-centre/enclosure transform, or evidence-bounded "
        "capture placement prior was found. These fields remain `MISSING`; no population values or "
        "fabricated historical board offsets were substituted. Input validation therefore completed "
        "before the calibration ledger was opened. Held-out walk/final_still were not opened and no "
        "GIF was generated.\n\n"
        "When valid inputs exist, all calibration-estimated capture placement vectors are bounded "
        "nuisance parameters in the measurement and posterior Jacobians, per-coordinate profiles, "
        "multistart, interleaved, and action-removal refits. Joint-centre and antenna predictions are "
        "computed independently. Anthropometric scalars are treated as fixed; reported output "
        "uncertainty explicitly excludes anthropometric measurement and derivation uncertainty.\n",
        encoding="utf-8",
    )
    return {
        "verdict": reason,
        "calibration_pass": bool(result["pass"]),
        "anthropometry_complete": anthropometry is not None,
        "calibration_payload_opened": result["calibration_payload_opened"],
        "heldout_opened": False,
        "v3_immutable": True,
        "v4_immutable": True,
        "foot_rendering": result["FOOT_RENDERING"],
    }
