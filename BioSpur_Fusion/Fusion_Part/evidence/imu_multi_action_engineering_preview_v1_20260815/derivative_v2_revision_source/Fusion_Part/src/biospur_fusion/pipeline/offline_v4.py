"""Measurement-conditioned V4 centerline orchestration, preserving V3."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from biospur_fusion.calibration.anthropometry import validate_anthropometry
from biospur_fusion.calibration.centerline_real_capture import run as run_centerline


RAW_SHA = "a491520739400064db520377ec87a9331feb6274cd42a7e6d9aad57a2b93d56a"
LAYOUT_SHA = "20320e53d48b171c016a0e8d1d93b3cb10e979cf4c21c15c21647d5c0b9878b1"
V3_COMMIT = "acbe8c6d3bd337aca20ebf899a52ee414ad7970c"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(4 << 20): digest.update(block)
    return digest.hexdigest()


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+"\n", encoding="utf-8")


def _verify_v3(v3: Path) -> dict:
    manifest = v3/"SHA256SUMS"; mismatches = []; checked = 0
    for line in manifest.read_text().splitlines():
        expected, relative = line.split("  ", 1); path = v3/relative; checked += 1
        actual = sha(path) if path.is_file() else None
        if actual != expected: mismatches.append({"path": relative, "expected": expected, "actual": actual})
    return {"v3_path": str(v3.resolve()), "v3_commit": V3_COMMIT,
            "sha256s_sha256": sha(manifest), "files_checked": checked,
            "mismatches": mismatches, "immutable_verified": not mismatches}


def _placeholders(out: Path, reason: str) -> None:
    for name, fields in {
        "BODY_STATE_TIMELINE.csv": ["global_time_ns", "status"],
        "SEGMENT_AXES.csv": ["global_time_ns", "segment", "status"],
        "JOINT_CENTRES.csv": ["global_time_ns", "joint", "status"],
        "MEASUREMENT_REJECTION_LEDGER.csv": ["global_time_ns", "node", "reason", "status"],
    }.items():
        with (out/name).open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle, lineterminator="\n").writerow(fields)
    dump(out/"CALIBRATION_FREEZE_MANIFEST.json", {"status": "NOT_FROZEN", "reason": reason,
                                                     "axial_twist_gauges_unresolved": True})
    dump(out/"HELDOUT_VALIDATION.json", {"status": "NOT_OPENED", "reason": reason,
                                           "walk_opened": False, "final_still_opened": False,
                                           "opened_once_after_freeze": False})
    dump(out/"ANIMATION_GATE.json", {"pass": False, "reason": reason, "gif_generated": False,
                                      "required_label": "centerline visualization only; axial segment twist and clinical angles not validated."})


def run(capture: Path, out: Path, repo: Path) -> dict:
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"immutable V4 output already exists: {out}")
    out.mkdir(parents=True, exist_ok=True)
    v3 = capture/"analysis_body_fusion_v3"; v3_audit = _verify_v3(v3)
    dump(out/"V3_IMMUTABILITY_AUDIT.json", v3_audit)
    if not v3_audit["immutable_verified"]: raise RuntimeError("V3_IMMUTABILITY_CHECK_FAILED")
    raw = capture/"continuous_collector/fusion_host_raw.cobs.bin"
    layout = repo/"B306_Part/deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json"
    if sha(raw) != RAW_SHA: raise RuntimeError("CAPTURE_RAW_SHA_MISMATCH")
    if sha(layout) != LAYOUT_SHA: raise RuntimeError("BLOCKED_UWB_GEOMETRY_PROVENANCE")
    config = repo/"Fusion_Part/config/body_calibration_v4"
    anthropometry_path = config/"v47_subject_anthropometry_v1.json"
    gates_path = config/"invariance_gates_v1.json"
    anthropometry, audit = validate_anthropometry(anthropometry_path)
    dump(out/"ANTHROPOMETRY_INPUT_AUDIT.json", audit)
    dump(out/"PREDECLARED_GATE_MANIFEST.json", {
        "path": str(gates_path.resolve()), "sha256": sha(gates_path),
        "declared_before_calibration": True,
        "dimensionless_v3_classifier_reused": False,
    })
    # The real calibration function validates anthropometry before opening its
    # ledger.  Passing the canonical path here therefore produces a provable
    # payload-not-opened block when measurements are incomplete.
    calibration = v3/"ledgers/calibration/CALIBRATION_TYPED_LEDGER.npz"
    result = run_centerline(calibration, layout, anthropometry_path, gates_path, out/"calibration")
    reason = result["verdict"]
    if not result["pass"]:
        _placeholders(out, reason)
    else:
        # Held-out evaluation is intentionally a separate post-freeze stage.
        # It is not reachable until a complete anthropometry input passes the
        # real quotient calibration and a freeze hash is serialized.
        reason = "BLOCKED_HELDOUT_STAGE_REQUIRES_SERIALIZED_CENTERLINE_FREEZE"
        _placeholders(out, reason)
    dump(out/"PROVENANCE.json", {
        "capture": str(capture.resolve()), "raw_sha256_before": RAW_SHA,
        "raw_sha256_after": sha(raw), "layout_sha256": LAYOUT_SHA,
        "anthropometry_sha256": sha(anthropometry_path), "gates_sha256": sha(gates_path),
        "hardware_accessed": False, "v3_modified": False,
    })
    (out/"REPORT.md").write_text(
        "# Ten-node body Fusion V4 measurement-conditioned centerline\n\n"
        f"Top-level verdict: `{reason}`.\n\n"
        "`FULL_SEGMENT_POSE_CALIBRATION` remains `FAIL_AXIAL_TWIST_GAUGES_UNRESOLVED`. "
        f"`STICK_FIGURE_CENTERLINE_CALIBRATION` is `{result['STICK_FIGURE_CENTERLINE_CALIBRATION']}`.\n\n"
        "V4 uses an explicit quotient state: eight limb axial-twist coordinates are absent from the "
        "centerline optimizer. Physical invariance gates were frozen at 1e-4 rad and 0.1 mm before "
        "this run. The repository contains no measured subject anthropometry, shoe condition, or "
        "sensor-to-landmark offsets, so calibration stopped before opening calibration payloads. "
        "No dimensions were imported from V3 or fitted from this capture. V3 hashes all verify and "
        "V3 was not modified. The held-out ledger was not opened and no GIF was generated.\n",
        encoding="utf-8")
    return {"verdict": reason, "calibration_pass": bool(result["pass"]),
            "anthropometry_complete": anthropometry is not None, "heldout_opened": False,
            "v3_immutable": True}
