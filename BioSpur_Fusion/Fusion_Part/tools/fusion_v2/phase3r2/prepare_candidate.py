#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[5]
FUSION = ROOT / "BioSpur_Fusion/Fusion_Part"
RUN_ID = "phase3r2_20260818T084835Z"
REPORT = FUSION / f"reports/fusion_v2/phase3r2/{RUN_ID}"
EXTERNAL = Path(f"/mnt/nrf_ssd/nRF_dev_worktrees/fusion-phase3r2-evidence/{RUN_ID}")
SOURCE = EXTERNAL / "source_audit/PHASE3R2_DATA_SELECTION_ALLOWLIST.json"
TIME = EXTERNAL / "source_audit/PHASE3R2_TIME_CONTEXT.json"


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            value.update(chunk)
    return value.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def main() -> int:
    selection = json.loads(SOURCE.read_text())
    compact_windows = []
    for row in selection["development_windows"] + selection["retrospective_diagnostics"]:
        compact_windows.append({key: row[key] for key in (
            "action_id", "classification", "relative_dir", "manifest", "manifest_sha256",
            "event_ledger", "event_ledger_sha256", "slice", "slice_sha256",
            "continuous_start_byte_inclusive", "formal_start_byte_inclusive",
            "formal_stop_byte_exclusive", "continuous_end_byte_exclusive",
        )})
    tracked_selection = {
        "schema": selection["schema"], "run_id": RUN_ID,
        "dataset_root": selection["dataset_root"],
        "canonical_raw": selection["canonical_raw"],
        "canonical_raw_sha256": selection["canonical_raw_sha256"],
        "recursive_discovery": False, "invalid_redo_numeric_reads": 0,
        "fit_bearing_action_count": 18, "controlled_validation_window_count": 19,
        "retrospective_count": 3, "chronological_action_ids": selection["chronological_action_ids"],
        "calibration_replay_classification": selection["calibration_replay_classification"],
        "uwb_measurement_policy": selection["uwb_measurement_policy"],
        "literal_windows": compact_windows,
        "full_external_allowlist": {"path": str(SOURCE), "sha256": sha(SOURCE)},
    }
    write_json(REPORT / "PHASE3R2_DATA_SELECTION_ALLOWLIST.json", tracked_selection)

    timing = json.loads(TIME.read_text())
    worst = max(timing["clock_models"].items(), key=lambda item: item[1]["residual_p95_us"])
    replay_max = max(row["rational_vs_self_replay_max_difference_ns"] for row in timing["fit_details"].values())
    write_json(REPORT / "PHASE3R2_TIME_EQUIVALENCE_REPORT.json", {
        "schema": "biospur-phase3r2-time-equivalence-report-v1", "run_id": RUN_ID,
        "source": {"path": str(TIME), "sha256": sha(TIME)},
        "strict_gate": timing["gate"],
        "rational_self_replay_max_difference_ns": replay_max,
        "worst_clock_segment": {"identity": worst[0], **worst[1]},
        "sample_age_model": timing["sample_age_model"],
        "bsfc2cc_controlled_window_overlap": "INSUFFICIENT",
        "precision_host_arrival_inputs": 0, "uwb_measurement_numeric_inputs": 0,
        "verdict": "STAGE_COMPLETE_NEEDS_CURRENT_SESSION_TIME_EVIDENCE",
    })
    write_json(REPORT / "PHASE3R2_SESSION_CALIBRATION_BUNDLE.json", {
        "schema": "biospur-phase3r2-session-calibration-bundle-unavailable-v1",
        "run_id": RUN_ID, "status": "NOT_CREATED",
        "reason": "STRICT_CURRENT_SESSION_TEN_NODE_COMMON_TIME_GATE_FAILED_BEFORE_IMU_NUMERIC_FIT_CACHE",
        "fit_measurement_numeric_decode": 0, "validation_measurement_numeric_decode": 0,
        "h_measurement_numeric_decode": 0, "final_still_static_factor_count": 0,
        "authority": "NO_CALIBRATION_AUTHORITY_CREATED",
    })

    scientific_paths = [
        "BioSpur_Fusion/Fusion_Part/src/biospur_fusion/io_v2/phase3r2_selective.py",
        "BioSpur_Fusion/Fusion_Part/src/biospur_fusion/time/phase3r2_context.py",
    ]
    scientific_paths += [str(path.relative_to(ROOT)) for path in sorted(
        (FUSION / "src/biospur_fusion/imu_pose_v2").glob("*.py"))]
    scientific_paths += [str(path.relative_to(ROOT)) for path in sorted(
        (FUSION / "config/fusion_v2/phase3r2").glob("*.json"))]
    scientific_paths += [
        str((REPORT / "PHASE3R2_DATA_SELECTION_ALLOWLIST.json").relative_to(ROOT)),
        str((REPORT / "PHASE3R2_TIME_EQUIVALENCE_REPORT.json").relative_to(ROOT)),
        str((REPORT / "PHASE3R2_SESSION_CALIBRATION_BUNDLE.json").relative_to(ROOT)),
    ]
    scientific_files = []
    closure = hashlib.sha256()
    for relative in sorted(scientific_paths):
        path = ROOT / relative; digest = sha(path)
        scientific_files.append({"path": relative, "sha256": digest, "size": path.stat().st_size})
        closure.update(relative.encode() + b"\0" + digest.encode() + b"\0")
    write_json(REPORT / "SCIENTIFIC_CLOSURE_MANIFEST.json", {
        "schema": "biospur-phase3r2-scientific-closure-v1", "run_id": RUN_ID,
        "status": "LIMITED_CANDIDATE_NO_REAL_CALIBRATION_BUNDLE",
        "files": scientific_files, "scientific_closure_sha256": closure.hexdigest(),
        "explicit_exclusions": ["renderer", "report_serializer", "publication_generator",
                                "publication_validator", "attestation_text"],
    })

    porcelain = subprocess.check_output(["git", "status", "--porcelain=v1", "-z"], cwd=ROOT)
    paths = []
    for record in porcelain.split(b"\0"):
        if not record: continue
        relative = record[3:].decode()
        if relative.endswith("WIP_CLOSURE_MANIFEST.json") or relative.endswith("STAGING_ALLOWLIST_CANDIDATE.txt"):
            continue
        path = ROOT / relative
        if path.is_dir():
            paths.extend(str(item.relative_to(ROOT)) for item in path.rglob("*") if item.is_file() and "__pycache__" not in item.parts)
        elif path.is_file() and "__pycache__" not in path.parts:
            paths.append(relative)
    rows = []
    for relative in sorted(set(paths)):
        path = ROOT / relative; mode = stat.S_IMODE(path.stat().st_mode)
        rows.append({"path": relative, "mode": f"{mode:04o}", "size": path.stat().st_size, "sha256": sha(path)})
    wip_hash = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    write_json(REPORT / "WIP_CLOSURE_MANIFEST.json", {
        "schema": "biospur-phase3r2-wip-closure-v1", "run_id": RUN_ID,
        "files": rows, "wip_closure_sha256": wip_hash,
        "self_exclusion": "manifest cannot hash itself", "staging_allowlist_exclusion": "path list is not source",
    })
    staged_paths = sorted(set(paths + [
        str((REPORT / "WIP_CLOSURE_MANIFEST.json").relative_to(ROOT)),
        str((REPORT / "STAGING_ALLOWLIST_CANDIDATE.txt").relative_to(ROOT)),
    ]))
    (REPORT / "STAGING_ALLOWLIST_CANDIDATE.txt").write_text("".join(path + "\n" for path in staged_paths))
    print(json.dumps({"scientific_closure_sha256": closure.hexdigest(), "wip_closure_sha256": wip_hash,
                      "staging_paths": len(staged_paths)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
