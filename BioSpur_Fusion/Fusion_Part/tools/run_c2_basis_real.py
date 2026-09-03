#!/usr/bin/env python3
"""Run sealed C2 progressive calibration, fresh batch, and direct FK viewer."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import traceback
from typing import Any, Mapping

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biospur_fusion.v0.contracts import dump_json, sha256_file
from biospur_fusion.v0.c2_basis.calibration import execute_progressive_and_batch
from biospur_fusion.v0.c2_basis.contracts import (
    CAPTURE_ID, C2_IDENTITY, load_c2_authority, load_config,
)
from biospur_fusion.v0.c2_basis.geometry import LIMBS, load_body_geometry
from biospur_fusion.v0.c2_basis.model import CalibrationState, HEADING_SEGMENTS
from biospur_fusion.v0.c2_basis.real_data import load_c2_episodes, verify_fresh_seal
from biospur_fusion.v0.c2_basis.validation import validate_basis_contract
from biospur_fusion.v0.c2_basis.viewer import write_audit_frames, write_viewer


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _geometry_summary(geometry) -> dict[str, Any]:
    def scalar(row) -> dict[str, Any]:
        return {
            "value_m": row.value_m, "lower_m": row.lower_m,
            "upper_m": row.upper_m, "sigma_m": row.sigma_m,
            "source": row.source, "estimator_coordinate": row.estimator_coordinate,
        }
    return {
        "segments": {name: scalar(row) for name, row in geometry.segments.items()},
        "biacromial": scalar(geometry.biacromial),
        "chest_to_acromion": scalar(geometry.chest_to_acromion),
        "pelvis_imu_to_chest_imu": scalar(geometry.pelvis_imu_to_chest_imu),
        "internal_hip_spacing": scalar(geometry.internal_hip_spacing),
        "surface_breadths_are_not_internal_hip_centers": True,
        "height_role": "BROAD_QA_ONLY",
    }


def _directory_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _run(preselection: Path, expected_sha256: str | None) -> int:
    preselection = preselection.resolve()
    run_dir = preselection.parent
    seal = verify_fresh_seal(ROOT, preselection, expected_sha256)
    if any((run_dir / name).exists() for name in (
        "C2_ACTION_ACCESS", "RAW_ACCESS_AUDIT.json", "PROGRESSIVE_AND_BATCH.json",
        "PROGRESSIVE_CHECKPOINTS.jsonl", "RESULT_PRE_VISUAL.json", "FINAL_REPORT.json",
    )):
        raise FileExistsError("refusing to overwrite an existing C2 real-run artifact")
    nrf_free = shutil.disk_usage("/mnt/nrf_ssd").free
    root_free = shutil.disk_usage("/").free
    if nrf_free < 100 * 1024**3 or root_free < 40 * 1024**3:
        raise RuntimeError("disk gate failed immediately before C2 raw access")
    config = load_config(ROOT)
    authority = load_c2_authority(ROOT)
    geometry = load_body_geometry(ROOT)
    basis = validate_basis_contract(config, geometry)
    try:
        episodes, stillness, access = load_c2_episodes(
            ROOT, run_dir, preselection, config,
            expected_seal_sha256=seal["sha256"],
        )
        access_path = run_dir / "RAW_ACCESS_AUDIT.json"
        dump_json(access_path, _jsonable(access)); access_path.chmod(0o444)
        print("STAGE C2 raw ingest complete; progressive calibration begin", flush=True)
        checkpoint_path = run_dir / "PROGRESSIVE_CHECKPOINTS.jsonl"

        def checkpoint(event: Mapping[str, Any]) -> None:
            with checkpoint_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(
                    _jsonable(event), sort_keys=True, separators=(",", ":"),
                ) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

        execution = execute_progressive_and_batch(
            episodes, geometry, config, progress_callback=checkpoint,
        )
        checkpoint_path.chmod(0o444)
        execution_path = run_dir / "PROGRESSIVE_AND_BATCH.json"
        dump_json(execution_path, _jsonable(execution.report)); execution_path.chmod(0o444)

        batch_state = np.asarray(execution.batch_result["state"], dtype=float)
        state = CalibrationState.from_vector(batch_state)
        batch = execution.report["fresh_batch"]
        conflicts = {
            "held_out_individual_covariance_blocks": batch["held_out"]["named_conflicts"],
            "held_out_pooled_edge_action_secondary": batch["held_out"][
                "pooled_named_conflicts_secondary"
            ],
            "physical_gates": [
                name for name, passed in batch["feasibility"]["gates"].items()
                if not passed
            ],
        }
        profile_summary = {
            "capture_id": CAPTURE_ID,
            "readiness_percent": execution.progressive_profile.snapshots[-1]["readiness_percent"],
            "ready_before_visual_gate": execution.progressive_profile.snapshots[-1]["ready"],
            "posterior_information": batch["information"],
            "functional_branch_uncertainty": execution.batch_functional.audit,
            "measurements": _geometry_summary(geometry),
            "conflicts": conflicts,
            "node_labels": C2_IDENTITY,
            "render_contract": "DIRECT_RAW_PATH_FIXED_GEOMETRY_FK_NO_IK_REBASE_OR_REPAIR",
        }
        viewer_path = write_viewer(
            run_dir / "C2_DIRECT_FIXED_GEOMETRY_FK_VIEWER.html",
            episodes, batch_state, execution.batch_functional.candidate,
            geometry, profile_summary,
        )
        frames = write_audit_frames(
            run_dir / "VISUAL_AUDIT_FRAMES", episodes, batch_state,
            execution.batch_functional.candidate, geometry,
        )
        viewer_path.chmod(0o444)
        for row in frames:
            Path(row["path"]).chmod(0o444)
            row["sha256"] = sha256_file(Path(row["path"]))

        acceptance = config["acceptance"]
        final_snapshot = execution.progressive_profile.snapshots[-1]
        gates = {
            "basis_contract": basis["pass"],
            "all_19_complete_episodes_retained": len(episodes) == 19,
            "progressive_final_ready_before_visual": final_snapshot["ready"],
            "fresh_batch_rank": batch["information"]["gauge_reduced_heading_rank"] == 9,
            "fresh_batch_heading_uncertainty": (
                batch["information"]["maximum_posterior_heading_sigma_deg"]
                <= float(acceptance["maximum_heading_posterior_sigma_deg"])
            ),
            "held_out_every_individual_covariance_block": batch["held_out"]["pass"],
            "hard_physical_feasibility": batch["feasibility"]["pass"],
            "progressive_fresh_batch_agreement": execution.comparison["pass"],
            "visual_gate": None,
        }
        learned = {
            "pelvis_root_yaw_gauge_rad": 0.0,
            "nine_segment_heading_corrections_rad": dict(zip(
                HEADING_SEGMENTS, state.headings_rad.tolist(),
            )),
            "eight_bounded_sensor_axial_offsets_m": dict(zip(
                LIMBS, state.axial_offsets_m.tolist(),
            )),
            "sensor_to_segment_rotation_matrices": {
                segment: matrix.tolist()
                for segment, matrix in execution.batch_functional.candidate.body_from_sensor_by_segment.items()
            },
            "functional_candidate_id": execution.batch_functional.candidate.candidate_id,
            "capture_wide_gyro_bias_rad_s": {
                C2_IDENTITY[node]: row.gyro_bias_rad_s.tolist()
                for node, row in stillness.by_node.items()
            },
            "fixed_geometry": _geometry_summary(geometry),
            "bone_lengths_learned_from_imu": False,
        }
        previsual = {
            "schema": "biospur-c2-basis-real-result-pre-visual-v1",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "verdict": "INCONCLUSIVE_PENDING_INDEPENDENT_VISUAL_AUDIT",
            "preselection": {"path": str(preselection), "sha256": seal["sha256"]},
            "synthetic_qualification": seal["synthetic_qualification"],
            "gates": gates,
            "progressive_curve": [{
                "step": row["step"], "episode": row["added_episode"],
                "readiness_percent": row["readiness_percent"],
                "ready": row["ready"], "assessment": row["assessment"],
                "rank": row["information"]["gauge_reduced_heading_rank"],
                "maximum_heading_sigma_deg": row["information"].get(
                    "maximum_posterior_heading_sigma_deg"
                ),
                "held_out_conflict_count": len(row["held_out"]["named_conflicts"]),
            } for row in execution.progressive_profile.snapshots],
            "fresh_batch": batch,
            "progressive_batch_comparison": execution.comparison,
            "learned_and_used_parameters": learned,
            "conflicts": conflicts,
            "file_access_audit": {
                "path": str(access_path), "sha256": sha256_file(access_path),
                "hxx_payload_opened": False, "capture1_payload_opened": False,
                "capture3_payload_opened": False, "uwb_spatial_payload_consumed": False,
            },
            "viewer": {
                "path": str(viewer_path), "sha256": sha256_file(viewer_path),
                "frames": frames,
                "inspection_status": "PENDING_ASSISTANT_DIRECT_IMAGE_INSPECTION",
            },
            "disk": {
                "nrf_ssd_free_bytes_before_raw": nrf_free,
                "root_free_bytes_before_raw": root_free,
                "run_directory_bytes": _directory_bytes(run_dir),
                "projected_growth_limit_bytes": 5 * 1024**3,
            },
            "architecture": [
                "fixed measured anthropometric geometry",
                "capture-wide stillness/gravity/gyro-bias state",
                "qualitative wear branch bank plus QMT/Seel functional calibration",
                "nine relative headings with pelvis yaw gauge and graph synchronization",
                "one bounded all-action refinement",
                "persistent chronological progressive posterior",
                "fresh cumulative batch and within-episode held-out prediction",
                "direct fixed-geometry FK viewer without IK or repair",
            ],
        }
        output = run_dir / "RESULT_PRE_VISUAL.json"
        dump_json(output, _jsonable(previsual)); output.chmod(0o444)
        print(json.dumps({
            "result_pre_visual": str(output),
            "viewer": str(viewer_path),
            "frames": [row["path"] for row in frames],
            "quantitative_gates": gates,
        }, indent=2))
        return 0
    except Exception as exc:
        checkpoint_path = run_dir / "PROGRESSIVE_CHECKPOINTS.jsonl"
        if checkpoint_path.exists():
            checkpoint_path.chmod(0o444)
        failure = run_dir / "RUN_FAILURE.json"
        if not failure.exists():
            dump_json(failure, {
                "schema": "biospur-c2-basis-real-run-failure-v1",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "exception_type": type(exc).__name__,
                "exception": str(exc),
                "traceback": traceback.format_exc(),
                "blind_rerun_performed": False,
                "preselection": {"path": str(preselection), "sha256": seal["sha256"]},
                "progressive_checkpoints": (
                    {"path": str(checkpoint_path), "sha256": sha256_file(checkpoint_path)}
                    if checkpoint_path.exists() else None
                ),
            })
            failure.chmod(0o444)
        raise


def _finalize(run_dir: Path, verdict: str, notes: list[str]) -> int:
    run_dir = run_dir.resolve()
    pre_path = run_dir / "RESULT_PRE_VISUAL.json"
    final_path = run_dir / "FINAL_REPORT.json"
    visual_path = run_dir / "VISUAL_AUDIT.json"
    if final_path.exists() or visual_path.exists():
        raise FileExistsError("refusing to overwrite final visual evidence")
    pre = json.loads(pre_path.read_text(encoding="utf-8"))
    for row in pre["viewer"]["frames"]:
        if sha256_file(Path(row["path"])) != row["sha256"]:
            raise RuntimeError("visual audit frame changed before finalization")
    visual_pass = verdict == "PASS"
    visual = {
        "schema": "biospur-c2-direct-fk-independent-visual-audit-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "inspector": "CODEX_DIRECT_IMAGE_INSPECTION",
        "views": ["front", "side", "top"],
        "frames": pre["viewer"]["frames"],
        "notes": notes,
        "knee_forward_back_standing_split_visible": verdict == "FAIL_KNEE_SPLIT",
        "direct_raw_path_fixed_geometry": True,
        "ik_or_animation_repair": False,
        "pass": visual_pass,
        "verdict": verdict,
    }
    dump_json(visual_path, visual); visual_path.chmod(0o444)
    gates = {**pre["gates"], "visual_gate": visual_pass}
    quantitative = all(value is True for name, value in gates.items() if name != "visual_gate")
    if quantitative and visual_pass:
        final_verdict = "PASS"
    elif verdict == "INCONCLUSIVE" and quantitative:
        final_verdict = "INCONCLUSIVE"
    else:
        final_verdict = "FAIL"
    final = {
        **pre,
        "schema": "biospur-c2-basis-final-report-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "verdict": final_verdict,
        "gates": gates,
        "viewer": {
            **pre["viewer"],
            "inspection_status": "COMPLETE",
            "visual_audit": {"path": str(visual_path), "sha256": sha256_file(visual_path)},
        },
        "product_ready": final_verdict == "PASS",
        "thresholds_relaxed_after_failure": False,
    }
    final["disk"]["run_directory_bytes_after_visual"] = _directory_bytes(run_dir)
    dump_json(final_path, final); final_path.chmod(0o444)
    print(json.dumps({
        "final_report": str(final_path), "sha256": sha256_file(final_path),
        "verdict": final_verdict, "gates": gates,
    }, indent=2))
    return 0 if final_verdict == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preselection", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--finalize-run", type=Path)
    parser.add_argument(
        "--visual-verdict",
        choices=("PASS", "FAIL", "FAIL_KNEE_SPLIT", "INCONCLUSIVE"),
    )
    parser.add_argument("--visual-note", action="append", default=[])
    args = parser.parse_args()
    if args.finalize_run is not None:
        if args.preselection is not None or args.visual_verdict is None:
            parser.error("--finalize-run requires --visual-verdict and excludes --preselection")
        return _finalize(args.finalize_run, args.visual_verdict, args.visual_note)
    if args.preselection is None:
        parser.error("--preselection is required for a real run")
    return _run(args.preselection, args.expected_sha256)


if __name__ == "__main__":
    raise SystemExit(main())
