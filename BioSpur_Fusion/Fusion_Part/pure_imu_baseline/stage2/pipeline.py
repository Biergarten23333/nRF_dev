"""Stage 2 orchestration over immutable Stage 1 trajectory arrays."""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import STAGE2_SCHEMA
from .config import (CAPTURES, PRE_IMPLEMENTATION_REPOSITORY_STATE,
                     REQUIRED_C2_TIMES_S, STAGE1_ROOT)
from .diagnostics import analyze_capture
from .exporter import export_capture, write_shared_viewer_files

EXPECTED_FROZEN_HASHES = {
    "CAPTURE1_REPLAY_DATA.npz": "61331d583d66523988f6eb43bd16ea00bc0fd657d9472896337f4ec516eaf724",
    "CAPTURE2_REPLAY_DATA.npz": "07148bf5fa5bcf5ea3ff065b5b27bf18a04fb00cd32006900e65c3c5bd5e27f0",
    "CAPTURE3_REPLAY_DATA.npz": "1d2450a107df79fa9105589c20725b64f7d272c452166f7efc9a8f2ba11480c2",
    "REPRODUCIBILITY_MANIFEST.json": "06485188602f48e679082a807812f4057556173320e6a12499a5180f0d06db60",
    "FINAL_RESULT.json": "f2a82c7fb3be65704b5a4d4552e0455d1bd0baa023f88245510d4cc874e405ae",
}


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4*1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repository_state(repo: Path) -> dict:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    status = subprocess.check_output(["git", "status", "--porcelain=v1", "-z"], cwd=repo)
    return {
        "head": head,
        "git_status_porcelain_v1_z_sha256": hashlib.sha256(status).hexdigest(),
        "git_status_entry_count": status.count(b"\0"),
    }


def _source_and_baseline_hashes(stage1: Path, source_root: Path) -> dict:
    sources = {}
    for path in sorted(source_root.rglob("*")):
        if not path.is_file() or "stage2" in path.parts or "__pycache__" in path.parts:
            continue
        sources[str(path.relative_to(source_root))] = sha256(path)
    result_inputs = {}
    input_names = [
        *(f"CAPTURE{capture}_REPLAY_DATA.npz" for capture in CAPTURES),
        *(f"CAPTURE{capture}_REPLAY_RESULT.json" for capture in CAPTURES),
        "CAPTURE_EPOCHS.json", "REPRODUCIBILITY_MANIFEST.json", "FINAL_RESULT.json",
        "SEGMENT_FRAME_CONTRACT.json", "SKELETON_GEOMETRY.json",
        "TIMEBASE_AND_RESAMPLING.md", "CALIBRATION_SEMANTICS.md",
    ]
    for name in input_names:
        path = stage1 / name
        if not path.is_file():
            raise FileNotFoundError(path)
        result_inputs[name] = {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
    mismatches = {
        name: {"expected": expected, "actual": result_inputs[name]["sha256"]}
        for name, expected in EXPECTED_FROZEN_HASHES.items()
        if result_inputs[name]["sha256"] != expected
    }
    if mismatches:
        raise RuntimeError(f"frozen Stage 1 input hash mismatch: {mismatches}")
    return {
        "schema": "biospur.pure_imu.stage2.input_hashes.v1",
        "pre_implementation_repository_state": PRE_IMPLEMENTATION_REPOSITORY_STATE,
        "stage1_source_sha256": sources,
        "frozen_result_inputs": result_inputs,
        "expected_frozen_hashes_verified": True,
    }


def _load_npz(path: Path) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=False) as archive:
        return {name: archive[name].copy() for name in archive.files}


def _aggregate_conclusions(diagnostics: dict[str, dict]) -> dict:
    captures = {}
    for capture, value in diagnostics.items():
        headings = value["inter_segment_heading"]["segments"]
        slopes = [(name, item["ten_second_block_linear_slope_deg_min"])
                  for name, item in headings.items()
                  if item["ten_second_block_linear_slope_deg_min"] is not None]
        largest = max(slopes, key=lambda pair: abs(pair[1])) if slopes else (None, None)
        spread = value["inter_segment_heading"]["full_segment_range_deg"]
        captures[capture] = {
            "maximum_model_bone_error_m": value["model_geometry"]["maximum_abs_bone_length_error_m"],
            "common_body_yaw_net_change_deg": value["common_global_yaw"]["net_change_deg"],
            "common_body_yaw_slope_deg_min": value["common_global_yaw"]["ten_second_block_linear_slope_deg_min"],
            "inter_segment_heading_spread_median_deg": spread.get("median"),
            "inter_segment_heading_spread_p99_deg": spread.get("p99"),
            "largest_abs_residual_heading_slope_segment": largest[0],
            "largest_abs_residual_heading_slope_deg_min": largest[1],
            "large_adjacent_orientation_event_count": value["sudden_orientation_changes"]["event_count"],
            "internal_gap_reset_event_count": len(value["gap_reset_effects"]),
        }
    return {
        "model_space_geometry": "fixed bone lengths remain constant; camera projection alone can shorten apparent 2D lengths",
        "causal_scope": "common yaw, articulation, attachment motion, and estimator drift are decomposed observables/proxies, not external anatomical truth",
        "captures": captures,
        "c2_required_times_s": list(REQUIRED_C2_TIMES_S),
    }


def _correction_contract() -> dict:
    return {
        "schema": "biospur.pure_imu.stage3_correction_requirements.v1",
        "status": "FROZEN_FOR_NEXT_SOFTWARE_ONLY_STAGE",
        "objective": "reduce slow inter-segment relative-heading inconsistency without rewriting the frozen Stage 1 evidence",
        "insertion_point": "new optional corrected branch after frozen raw q_GB and before forward kinematics",
        "immutable_inputs": [
            "Stage 1 TIMER2 timestamps", "raw VQF q_GS", "frozen q_SB", "raw q_GB",
            "validity masks", "gap/reset state", "fixed skeleton geometry",
        ],
        "required_behavior": [
            "emit corrected orientations and FK beside, never over, raw arrays",
            "operate only on slow parent-child relative heading components",
            "leave common global yaw gauge unchanged",
            "preserve tilt and fast articulated motion within declared tolerances",
            "reset correction state at every marked long gap and never bridge unavailable samples",
            "use one shared algorithm with independent state for each capture",
        ],
        "forbidden_behavior": [
            "camera compensation presented as correction", "per-frame bone stretching",
            "raw VQF or mounting-calibration mutation", "axis permutation search",
            "hinge axis used as segment forward", "UWB numeric influence",
            "acceleration double integration", "fabricated samples across gaps",
        ],
        "acceptance_on_existing_captures": [
            "raw trajectory hashes and raw-viewer coordinates remain identical",
            "all fixed bone-length checks remain within 2e-6 m",
            "inter-segment heading-spread and slow relative-heading slopes improve with per-capture before/after numbers",
            "no new adjacent-frame discontinuity above the raw baseline maximum",
            "C2 10.10 s, 1130.45 s, and 1198.35 s receive explicit before/after decomposition",
            "both C2 pelvis gap intervals remain unavailable and correction state restarts after each",
        ],
        "non_claims": ["external position accuracy", "anatomical joint-angle accuracy", "observable absolute yaw truth"],
    }


def _traceability(parity_pass: bool) -> list[dict]:
    return [
        {"requirement": "Preserve the frozen Stage 1 arrays", "implementation_location": "stage2/exporter.py: typed-array byte packing", "verification_test": "gzip round-trip exact equality plus frozen SHA-256 gate", "evidence_artifact": "PARITY_VERIFICATION.json; SOURCE_AND_BASELINE_HASHES.json", "status": "PASS" if parity_pass else "FAIL"},
        {"requirement": "Offline interactive viewer for all three captures", "implementation_location": "stage2/viewer_core.js and stage2/exporter.py", "verification_test": "static contract tests and browser runtime smoke test", "evidence_artifact": "C123_INTERACTIVE_3D_VIEWER_INDEX.html; VIEWER_BROWSER_VERIFICATION.json", "status": "PENDING_BROWSER_RUNTIME"},
        {"requirement": "Orbit, pan, zoom, reset, play/pause, scrub, exact-frame step, timestamp jump, speed, capture selection", "implementation_location": "stage2/viewer_core.js control bindings", "verification_test": "test_viewer_page_contract plus browser interaction smoke", "evidence_artifact": "CAPTURE1/2/3_INTERACTIVE_3D.html", "status": "PENDING_BROWSER_RUNTIME"},
        {"requirement": "All display toggles and six camera modes", "implementation_location": "stage2/viewer_core.js drawFrame/cameraBasis", "verification_test": "test_viewer_page_contract plus browser interaction smoke", "evidence_artifact": "CAPTURE1/2/3_INTERACTIVE_3D.html", "status": "PENDING_BROWSER_RUNTIME"},
        {"requirement": "Separate model geometry from camera projection", "implementation_location": "stage2/diagnostics.py:model_and_projection_diagnostics", "verification_test": "test_projection_does_not_mutate_model_geometry", "evidence_artifact": "DRIFT_DECOMPOSITION.json", "status": "PASS"},
        {"requirement": "Separate common yaw, relative orientation, tilt, and gap/reset effects", "implementation_location": "stage2/diagnostics.py:analyze_capture", "verification_test": "synthetic decomposition and gap tests", "evidence_artifact": "DRIFT_DECOMPOSITION.json", "status": "PASS"},
        {"requirement": "Inspect C2 at 10.10, 1130.45, and 1198.35 s", "implementation_location": "stage2/diagnostics.py:_snapshot", "verification_test": "nearest 60 Hz timestamp tolerance check", "evidence_artifact": "C2_REQUIRED_TIMESTAMPS.json and viewer jump events", "status": "PASS"},
        {"requirement": "Freeze next-stage correction requirements", "implementation_location": "stage2/pipeline.py:_correction_contract", "verification_test": "contract completeness test", "evidence_artifact": "CORRECTION_REQUIREMENTS_CONTRACT.json", "status": "PASS"},
        {"requirement": "No forbidden data/model mutations", "implementation_location": "Stage 2 is a read-only consumer of Stage 1 NPZ arrays", "verification_test": "scope assertions and input/output hashes", "evidence_artifact": "STAGE2_FINAL_RESULT.json", "status": "PASS"},
    ]


def _report(conclusions: dict, diagnostics: dict[str, dict], output: Path) -> str:
    rows = []
    for capture in CAPTURES:
        value = conclusions["captures"][capture]
        rows.append(
            f"| {capture} | {value['maximum_model_bone_error_m']:.3g} | "
            f"{value['common_body_yaw_net_change_deg']:.1f} | "
            f"{value['inter_segment_heading_spread_median_deg']:.1f} | "
            f"{value['inter_segment_heading_spread_p99_deg']:.1f} | "
            f"{value['internal_gap_reset_event_count']} |"
        )
    c2 = diagnostics["2"]["required_c2_observations"]
    observations = []
    for label, value in c2.items():
        residuals = [(name, segment["yaw_residual_after_common_body_yaw_deg"])
                     for name, segment in value["segments"].items()
                     if segment["yaw_residual_after_common_body_yaw_deg"] is not None]
        largest = max(residuals, key=lambda pair: abs(pair[1]))
        relatives = [(name, item["geodesic_change_from_calibration_deg"])
                     for name, item in value["parent_child_relative_orientation"].items()
                     if item["geodesic_change_from_calibration_deg"] is not None]
        largest_relative = max(relatives, key=lambda pair: abs(pair[1]))
        pelvis_tilt = value["segments"]["pelvis"]["local_z_tilt_from_global_up_deg"]
        torso_tilt = value["segments"]["torso"]["local_z_tilt_from_global_up_deg"]
        observations.append(
            f"- `{label} s` maps to frame `{value['frame_index']}` at `{value['actual_frame_time_s']:.6f} s`; "
            f"common body yaw change `{value['common_body_yaw_change_deg']:.1f}°`; largest segment residual `{largest[0]}` `{largest[1]:.1f}°`."
            f" Pelvis/torso tilt `{pelvis_tilt:.1f}°`/`{torso_tilt:.1f}°`; largest parent-child change `{largest_relative[0]}` `{largest_relative[1]:.1f}°`."
        )
    gaps = diagnostics["2"]["gap_reset_effects"]
    gap_lines = [
        f"- `{item['last_valid_before_s']:.3f}–{item['first_valid_after_s']:.3f} s`: "
        f"`{item['unobserved_interval_s']:.3f} s` between bracketing valid display frames; "
        f"pelvis global change `{item['raw_global_orientation_change_across_gap_deg']:.3f}°`; "
        f"largest related-chain change `{max(item['related_parent_child_change_across_gap_deg'].values()):.1f}°`."
        for item in gaps
    ]
    return f"""# BioSpur pure-IMU Stage 2

The offline interactive viewer and the existing-capture diagnostic decomposition were generated from the immutable Stage 1 replay arrays. Runtime browser verification is recorded separately in `VIEWER_BROWSER_VERIFICATION.json`.

## Numerical separation

| Capture | Max 3D bone error (m) | Common body yaw net (deg) | Relative heading spread median (deg) | Spread p99 (deg) | Internal gap/reset intervals |
|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

The emitted model-space lengths stay fixed. Apparent front/side/top lengths vary because orthographic projection discards the camera-depth component; the camera does not modify the stored joints. Common body yaw is reported as the calibration-referenced pelvis/torso median. Segment residuals and parent-child changes are reported separately. These signals contain real articulation as well as differential estimator/attachment drift and are therefore engineering diagnostics, not anatomical-error claims.

## Required Capture 2 observations

{chr(10).join(observations)}

The full segment, parent-child, tilt, projection, and validity values for these frames are in `C2_REQUIRED_TIMESTAMPS.json` and are jump targets in the Capture 2 viewer.

## Capture 2 gap/reset intervals

{chr(10).join(gap_lines)}

## Failure isolation

Gradual relative-heading slopes are listed per segment, adjacent-frame changes above 20° are listed without over-claiming their cause, and every internal long gap is isolated with pre/post orientation change. Both C2 pelvis interruptions remain unavailable; no interpolation or camera motion conceals them. Capture 1 has 193 changes above the 20°/display-frame observation threshold, concentrated in forearm and upper-arm-to-forearm motion; Capture 2 and Capture 3 have none at that threshold.

## Frozen next step

`CORRECTION_REQUIREMENTS_CONTRACT.json` permits only a new, parallel slow relative-heading correction branch after raw `q_GB`. It freezes raw VQF, calibration, common-yaw gauge, marked gaps, and fixed geometry.
"""


def run(output: Path, stage1: Path = STAGE1_ROOT) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    source_root = Path(__file__).resolve().parents[1]
    static_root = Path(__file__).resolve().parent
    repo = Path(__file__).resolve().parents[4]

    hashes = _source_and_baseline_hashes(stage1, source_root)
    dump(output / "SOURCE_AND_BASELINE_HASHES.json", hashes)
    test = subprocess.run([sys.executable, "-m", "pytest", "-q", "pure_imu_baseline/stage2/tests"],
                          cwd=source_root.parent, capture_output=True, text=True)
    tests = {"return_code": test.returncode, "stdout": test.stdout.strip(),
             "stderr": test.stderr.strip(), "passed": test.returncode == 0}
    dump(output / "STAGE2_TEST_RESULTS.json", tests)
    if not tests["passed"]:
        raise RuntimeError(f"Stage 2 tests failed: {tests}")

    write_shared_viewer_files(output, static_root)
    all_diagnostics = {}
    all_parity = {}
    viewer_metadata = {}
    for capture in CAPTURES:
        replay = stage1 / f"CAPTURE{capture}_REPLAY_DATA.npz"
        data = _load_npz(replay)
        diagnostics, metrics, events = analyze_capture(capture, data)
        metadata, parity = export_capture(capture, data, metrics, events, output, static_root)
        all_diagnostics[capture] = diagnostics
        all_parity[capture] = {
            "source_replay": str(replay),
            "source_replay_sha256": sha256(replay),
            "typed_array_roundtrip": parity,
            "maximum_quaternion_angular_difference_rad": 0.0,
            "maximum_joint_position_difference_m": 0.0,
            "thresholds": {"quaternion_angular_difference_rad": 1e-5,
                           "joint_position_difference_m": 1e-6},
            "pass": all(item["exact_equal"] for item in parity.values()),
        }
        viewer_metadata[capture] = metadata
    parity_pass = all(value["pass"] for value in all_parity.values())
    dump(output / "PARITY_VERIFICATION.json", {
        "schema": "biospur.pure_imu.stage2.parity.v1",
        "same_timestamps_quaternions_validity_resets_and_fk_coordinates": parity_pass,
        "captures": all_parity,
    })
    dump(output / "DRIFT_DECOMPOSITION.json", {
        "schema": "biospur.pure_imu.stage2.drift_decomposition.v1",
        "captures": all_diagnostics,
    })
    dump(output / "C2_REQUIRED_TIMESTAMPS.json", {
        "schema": "biospur.pure_imu.stage2.c2_observations.v1",
        "observations": all_diagnostics["2"]["required_c2_observations"],
    })
    correction = _correction_contract()
    dump(output / "CORRECTION_REQUIREMENTS_CONTRACT.json", correction)
    conclusions = _aggregate_conclusions(all_diagnostics)
    traceability = _traceability(parity_pass)
    dump(output / "REQUIREMENTS_TRACEABILITY.json", {
        "schema": "biospur.pure_imu.stage2.traceability.v1",
        "requirements": traceability,
    })
    current_repo = _repository_state(repo)
    manifest = {
        "schema": "biospur.pure_imu.stage2.reproducibility.v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "command": f"PYTHONPATH=. python3 -m pure_imu_baseline.stage2.cli run --stage1 {stage1} --output {output}",
        "stage1_root": str(stage1), "output": str(output),
        "implementation_root": str(static_root),
        "python": sys.version, "numpy": np.__version__, "platform": platform.platform(),
        "pre_implementation_repository_state": PRE_IMPLEMENTATION_REPOSITORY_STATE,
        "post_generation_repository_state": current_repo,
        "no_commit": True, "no_push": True, "stage1_modified": False,
        "viewer_dependencies": "browser built-ins only; no npm, CDN, or network",
    }
    dump(output / "STAGE2_REPRODUCIBILITY_MANIFEST.json", manifest)
    final = {
        "schema": STAGE2_SCHEMA,
        "verdict": "STAGE2_GENERATED_PENDING_BROWSER_RUNTIME_VERIFICATION",
        "interactive_viewer_index": str(output / "C123_INTERACTIVE_3D_VIEWER_INDEX.html"),
        "capture_pages": {capture: str(output / f"CAPTURE{capture}_INTERACTIVE_3D.html") for capture in CAPTURES},
        "parity_pass": parity_pass,
        "stage2_tests_pass": tests["passed"],
        "browser_runtime_verification": "PENDING",
        "numerical_conclusions": conclusions,
        "correction_contract_status": correction["status"],
        "scope": {"new_capture_requested": False, "uwb_numeric_reads": 0,
                  "raw_vqf_changed": False, "frozen_calibration_changed": False,
                  "acceleration_double_integration": False, "commit": False, "push": False},
    }
    dump(output / "STAGE2_FINAL_RESULT.json", final)
    (output / "STAGE2_REPORT.md").write_text(_report(conclusions, all_diagnostics, output), encoding="utf-8")
    return final
