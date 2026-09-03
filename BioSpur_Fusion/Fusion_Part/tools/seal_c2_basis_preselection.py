#!/usr/bin/env python3
"""Seal immutable C2 metadata, code, and synthetic evidence before raw access."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biospur_fusion.v0.contracts import dump_json, sha256_file
from biospur_fusion.v0.c2_basis.contracts import (
    ANTHROPOMETRY_REL,
    CAPTURE_ID,
    CONFIG_REL,
    EPISODE_SELECTION,
    FRAME_SEMANTICS_REL,
    SEALED_IDENTITY_REL,
    WEAR_AMENDMENT_REL,
    load_c2_authority,
    load_config,
)
from biospur_fusion.v0.c2_basis.geometry import load_body_geometry
from biospur_fusion.v0.c2_basis.real_data import metadata_authorities
from biospur_fusion.v0.c2_basis.validation import validate_basis_contract


def _git(command: list[str]) -> str:
    return subprocess.run(
        ["git", *command], cwd=ROOT, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()


def _source_files() -> list[Path]:
    package = sorted((ROOT / "src/biospur_fusion/v0/c2_basis").glob("*.py"))
    return [
        *package,
        ROOT / "src/biospur_fusion/v0/raw6_heading.py",
        ROOT / "src/biospur_fusion/v0/dual_capture.py",
        ROOT / "src/biospur_fusion/v0/episode.py",
        ROOT / "tools/run_c2_basis_synthetic_qualification.py",
        ROOT / "tools/seal_c2_basis_preselection.py",
        ROOT / "tools/run_c2_basis_real.py",
        ROOT / "tests/v0/test_c2_basis_contracts.py",
        ROOT / "tests/v0/test_c2_basis_validation.py",
        ROOT / CONFIG_REL,
        ROOT / SEALED_IDENTITY_REL,
        ROOT / WEAR_AMENDMENT_REL,
        ROOT / FRAME_SEMANTICS_REL,
        ROOT / ANTHROPOMETRY_REL,
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--synthetic-qualification", type=Path, required=True)
    args = parser.parse_args()
    synthetic_path = args.synthetic_qualification.resolve()
    synthetic = json.loads(synthetic_path.read_text(encoding="utf-8"))
    if synthetic.get("pass") is not True:
        raise SystemExit("independent synthetic qualification is not PASS")
    if not all(
        case["held_out"].get("acceptance_unit")
        == "INDIVIDUAL_PREREGISTERED_COVARIANCE_BLOCK"
        and case["held_out"].get("pass") is True
        for case in synthetic["cases"]
    ):
        raise SystemExit("synthetic qualification lacks passing individual-block held-out gates")
    dilution = next(
        (row for row in synthetic["mutation_controls"]
         if row["name"] == "bad_held_out_block_hidden_by_pooled_pair"), None,
    )
    if dilution is None or dilution.get("rejected") is not True:
        raise SystemExit("synthetic qualification lacks the held-out dilution mutation")
    energetic = next(
        (row for row in synthetic["mutation_controls"]
         if row["name"] == "energetic_motion_systematic_joint_error_not_normalized_away"), None,
    )
    if energetic is None or energetic.get("rejected") is not True:
        raise SystemExit("synthetic qualification lacks energetic systematic-error rejection")

    failed_block_id = "shoulder_right:02_t_pose:covariance_bin_004"
    covariance_history = []
    for relative in (
        Path("logs/c2_basis_synthetic_qualification_20260829_003632/qualification.json"),
        Path("logs/c2_basis_synthetic_qualification_20260829_005140/qualification.json"),
        Path("logs/c2_basis_synthetic_qualification_20260829_011051/qualification.json"),
    ):
        payload = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        case = next(row for row in payload["cases"] if row["name"] == "randomized_mounts_seed_20260830")
        block = next(
            row for row in case["held_out"]["individual_block_records"]
            if row["covariance_block_id"] == failed_block_id
        )
        covariance_history.append({
            "path": str(relative), "sha256": sha256_file(ROOT / relative),
            "pass": payload["pass"], "sigma_mps2": block["joint_center_sigma_mps2"],
            "nrmse": block["joint_center_nrmse"], "block_pass": block["pass"],
        })
    current_case = next(
        row for row in synthetic["cases"] if row["name"] == "randomized_mounts_seed_20260830"
    )
    current_block = next(
        row for row in current_case["held_out"]["individual_block_records"]
        if row["covariance_block_id"] == failed_block_id
    )

    nrf_free = shutil.disk_usage("/mnt/nrf_ssd").free
    root_free = shutil.disk_usage("/").free
    projected = 500 * 1024 * 1024
    if nrf_free < 100 * 1024**3 or root_free < 40 * 1024**3 or projected > 5 * 1024**3:
        raise SystemExit("disk gate failed before metadata seal")
    authorities = metadata_authorities(ROOT)
    authority = load_c2_authority(ROOT)
    config = load_config(ROOT)
    geometry = load_body_geometry(ROOT)
    basis = validate_basis_contract(config, geometry)
    sources = _source_files()
    missing = [str(path) for path in sources if not path.is_file()]
    if missing:
        raise SystemExit(f"preselection source inventory incomplete: {missing}")
    source_hashes = {
        str(path.relative_to(ROOT)): sha256_file(path) for path in sources
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = ROOT / "logs" / f"c2_basis_progressive_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    selection = authorities["selected_actions"]
    seal = {
        "schema": "biospur-c2-basis-metadata-preselection-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "record_status": "SEALED_BEFORE_ANY_NEW_PAYLOAD_OPEN_OR_HASH",
        "scope": "CAPTURE2_ONLY",
        "capture_id": CAPTURE_ID,
        "scientific_independence": {
            "capture1": "FORBIDDEN",
            "capture3": "FORBIDDEN",
            "hxx_golf_boxing": "FORBIDDEN",
            "uwb_spatial": "FORBIDDEN",
            "freeze_or_candidate_lock": "FORBIDDEN",
            "cross_capture_parameter_sharing": False,
        },
        "payload_boundary": {
            "new_payload_opened_before_seal": False,
            "new_payload_hashed_before_seal": False,
            "full_container_hash_will_not_be_recomputed": True,
            "sealed_container_sha256_imported_only": authorities[
                "sealed_raw_container_sha256_imported_not_recomputed"
            ],
        },
        "episode_semantics": {
            "one_persistent_profile": True,
            "chronological_order": [row[0] for row in EPISODE_SELECTION],
            "exact_attempts": {action: attempt for action, attempt in EPISODE_SELECTION},
            "every_episode": "REST_TRANSITION_ACTION_TRANSITION_REST",
            "prefixes": "POSTERIOR_DIAGNOSTICS_NOT_INDEPENDENT_CALIBRATIONS",
            "whole_action_held_out_partition": False,
            "held_out": config["sampling"]["held_out_strategy"],
            "selected_actions": selection,
        },
        "metadata_authorities": {
            "sealed_identity": {
                "path": str(authority["sealed_path"].relative_to(ROOT)),
                "sha256": sha256_file(authority["sealed_path"]),
            },
            "wear_amendment": {
                "path": str(authority["amendment_path"].relative_to(ROOT)),
                "sha256": sha256_file(authority["amendment_path"]),
            },
            "frame_semantics_amendment": {
                "path": str(authority["frame_semantics_path"].relative_to(ROOT)),
                "sha256": sha256_file(authority["frame_semantics_path"]),
            },
            "anthropometry": {
                "path": str(geometry.provenance_path.relative_to(ROOT)),
                "sha256": sha256_file(geometry.provenance_path),
            },
            "frozen_exact_selection": {
                "path": str(authorities["selection_path"].relative_to(ROOT)),
                "sha256": authorities["selection_sha256"],
                "canonical_selection_sha256": authorities["selection"]["selection_sha256"],
            },
            "historical_access_metadata_sha256": authorities["historical_access_hashes"],
        },
        "basis_contract": basis,
        "synthetic_qualification": {
            "path": str(synthetic_path.relative_to(ROOT)),
            "sha256": sha256_file(synthetic_path),
            "pass": True,
            "held_out_acceptance_unit": "INDIVIDUAL_PREREGISTERED_COVARIANCE_BLOCK",
            "pooled_edge_action_metric": "SECONDARY_DIAGNOSTIC_ONLY",
        },
        "covariance_repair_audit": {
            "failed_block": failed_block_id,
            "preserved_before_intermediate_history": covariance_history,
            "current_after_noise_only_propagation": {
                "sigma_mps2": current_block["joint_center_sigma_mps2"],
                "nrmse": current_block["joint_center_nrmse"],
                "block_pass": current_block["pass"],
            },
            "lever_bounds": "DERIVED_FROM_FIXED_GEOMETRY_MAXIMUM_CONNECTION_NORMS",
            "lever_bounds_depend_on_truth_fit_residual_or_failed_outcome": False,
            "noise_covariance_source": "VERIFIED_INITIAL_STILL_OR_INDEPENDENT_SYNTHETIC_SENSOR_NOISE",
            "dynamic_residual_or_jerk_treated_as_noise": False,
            "identical_noise_propagation_for_train_and_held_out": True,
            "individual_block_threshold": config["acceptance"]["maximum_held_out_joint_center_nrmse"],
            "threshold_relaxed": False,
            "energetic_systematic_error_mutation": energetic,
        },
        "bounded_structural_failure_evidence": [{
            "path": str(relative),
            "sha256": sha256_file(ROOT / relative),
            "blind_rerun_performed": False,
        } for relative in (
            Path("logs/c2_basis_synthetic_qualification_20260829_012531/qualification.json"),
            Path("logs/c2_basis_synthetic_qualification_20260829_012858/qualification.json"),
        )],
        "bounded_real_ingest_failure_evidence": [
            {
                "path": "logs/c2_basis_progressive_20260829_014942/RUN_FAILURE.json",
                "sha256": sha256_file(
                    ROOT / "logs/c2_basis_progressive_20260829_014942/RUN_FAILURE.json"
                ),
                "failure_stage": "FIRST_C2_EPISODE_PHASE_MATERIALIZATION",
                "payload_scope_opened": ["00_initial_still"],
                "other_c2_episode_payload_opened": False,
                "causal_repair": (
                    "C2-preregistered nonzero temporal transition partitions for "
                    "stationary references; no motion or pose claim"
                ),
                "blind_rerun_performed": False,
            },
            {
                "path": "logs/c2_basis_progressive_20260829_020529/RUN_FAILURE.json",
                "sha256": sha256_file(
                    ROOT / "logs/c2_basis_progressive_20260829_020529/RUN_FAILURE.json"
                ),
                "access_artifact": (
                    "logs/c2_basis_progressive_20260829_020529/"
                    "C2_ACTION_ACCESS/00_initial_still.json"
                ),
                "access_artifact_sha256": sha256_file(
                    ROOT / "logs/c2_basis_progressive_20260829_020529/"
                    "C2_ACTION_ACCESS/00_initial_still.json"
                ),
                "failure_stage": "C2_FORBIDDEN_PAYLOAD_SCHEMA_AUDIT",
                "payload_scope_opened": ["00_initial_still"],
                "artifact_proves_decoded_payload_classes": ["TEN_NODE_IMU"],
                "artifact_proves_uwb_spatial_fields_decoded": [],
                "causal_repair": (
                    "exact fail-closed list-valued decoder schema audit; empty list "
                    "is required and Boolean aliases remain rejected"
                ),
                "blind_rerun_performed": False,
            },
            {
                "path": "logs/c2_basis_progressive_20260829_021855/RUN_FAILURE.json",
                "sha256": sha256_file(
                    ROOT / "logs/c2_basis_progressive_20260829_021855/RUN_FAILURE.json"
                ),
                "raw_access_audit": (
                    "logs/c2_basis_progressive_20260829_021855/RAW_ACCESS_AUDIT.json"
                ),
                "raw_access_audit_sha256": sha256_file(
                    ROOT / "logs/c2_basis_progressive_20260829_021855/RAW_ACCESS_AUDIT.json"
                ),
                "failure_stage": "FRESH_BATCH_INITIAL_HEADING_GRAPH_SYNCHRONIZATION",
                "payload_scope_opened": [row[0] for row in EPISODE_SELECTION],
                "wall_limit_s": 35.0,
                "threshold_or_budget_relaxed": False,
                "causal_repair": (
                    "precompute delta-independent joint/hinge kinematic rows once per "
                    "edge, retain the exact soft-L1 objective, and add in-loop wall traces"
                ),
                "evidence_lifecycle_repair": (
                    "append-only fsync progressive checkpoints before fresh batch"
                ),
                "blind_rerun_performed": False,
            },
            {
                "path": "logs/c2_basis_progressive_20260829_030442/RUN_FAILURE.json",
                "sha256": sha256_file(
                    ROOT / "logs/c2_basis_progressive_20260829_030442/RUN_FAILURE.json"
                ),
                "progressive_checkpoints": (
                    "logs/c2_basis_progressive_20260829_030442/"
                    "PROGRESSIVE_CHECKPOINTS.jsonl"
                ),
                "progressive_checkpoints_sha256": sha256_file(
                    ROOT / "logs/c2_basis_progressive_20260829_030442/"
                    "PROGRESSIVE_CHECKPOINTS.jsonl"
                ),
                "failure_stage": "ALL_ACTION_REFINEMENT_NO_FINITE_START",
                "retained_episode_count": 19,
                "final_readiness_percent": 0.0,
                "threshold_or_budget_relaxed": False,
                "causal_repair": (
                    "exact sparse analytic Jacobian for the same robustified sensor "
                    "residual and bounded offset priors; no residual rows removed"
                ),
                "blind_rerun_performed": False,
            },
            {
                "path": "logs/c2_basis_progressive_20260829_034827/RUN_FAILURE.json",
                "sha256": sha256_file(
                    ROOT / "logs/c2_basis_progressive_20260829_034827/RUN_FAILURE.json"
                ),
                "progressive_checkpoints": (
                    "logs/c2_basis_progressive_20260829_034827/"
                    "PROGRESSIVE_CHECKPOINTS.jsonl"
                ),
                "progressive_checkpoints_sha256": sha256_file(
                    ROOT / "logs/c2_basis_progressive_20260829_034827/"
                    "PROGRESSIVE_CHECKPOINTS.jsonl"
                ),
                "failure_stage": "FRESH_BATCH_STAGE_D_SEQUENTIAL_TIME_FRAGMENTATION",
                "retained_episode_count": 19,
                "multistarts": 16,
                "per_start_wall_s_observed": 5.625,
                "shared_stage_wall_limit_s": 90.0,
                "threshold_or_budget_relaxed": False,
                "causal_repair": (
                    "execute independent Stage-D starts with eight workers under one "
                    "unchanged shared deadline; retain all 16 starts"
                ),
                "blind_rerun_performed": False,
            },
        ],
        "code_versions": {
            "git_commit": _git(["rev-parse", "HEAD"]),
            "git_dirty": bool(_git(["status", "--porcelain", "--untracked-files=all"])),
            "python": sys.version,
            "qmt": importlib.metadata.version("qmt"),
            "vqf": importlib.metadata.version("vqf"),
        },
        "source_files_sha256": source_hashes,
        "disk_gate": {
            "nrf_ssd_free_bytes": nrf_free,
            "root_free_bytes": root_free,
            "projected_growth_bytes": projected,
            "minimum_nrf_ssd_free_bytes": 100 * 1024**3,
            "minimum_root_free_bytes": 40 * 1024**3,
            "maximum_growth_bytes": 5 * 1024**3,
            "project_bytes_at_seal": sum(
                path.stat().st_size for path in ROOT.rglob("*") if path.is_file()
            ),
            "pass": True,
        },
    }
    output = run_dir / "METADATA_PRESELECTION.json"
    dump_json(output, seal)
    output.chmod(0o444)
    print(json.dumps({
        "run_dir": str(run_dir),
        "preselection": str(output),
        "sha256": sha256_file(output),
        "record_status": seal["record_status"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
