#!/usr/bin/env python3
"""Fresh C2-only run with explicit 2026-08-28 measured soft priors."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import run_pure_imu_v0_progressive as progressive_runner
from biospur_fusion.v0.contracts import dump_json, sha256_file
from biospur_fusion.v0.dual_capture import load_protocol
from biospur_fusion.v0.progressive_calibration import (
    REAL_SUBJECT_PROGRESSIVE_GEOMETRY_PRIOR,
)


RUN_REL = Path("logs/pure_imu_v0_c2_measured_prior_20260828T213320Z")
SOURCE_PRESELECTION_REL = Path(
    "logs/pure_imu_v0_progressive_calibration_20260828T132736Z/"
    "METADATA_PRESELECTION.json"
)
SOURCE_PRESELECTION_SHA256 = (
    "dd9867e999fe63dd0f25fe4d645d5c17bdc8e799c841664df3ca083c5c7db2bf"
)
ANTHROPOMETRY_REL = Path(
    "config/body_calibration_v4_1/"
    "v47_subject_surface_anthropometry_20260828.json"
)
ANTHROPOMETRY_SHA256 = (
    "faeb3291f59e35f6543467918631aafbe945501b064c19a26b946172c5fbd871"
)


def initialize() -> Path:
    """Seal selection and external-measurement semantics before payload access."""

    run_dir = ROOT / RUN_REL
    if run_dir.exists():
        raise FileExistsError(run_dir)
    root_free = shutil.disk_usage("/").free / 1e9
    nrf_free = shutil.disk_usage("/mnt/nrf_ssd").free / 1e9
    if root_free < 40.0 or nrf_free < 100.0:
        raise RuntimeError(
            f"disk gate failed: root={root_free:.1f}GB nrf={nrf_free:.1f}GB"
        )
    source_path = ROOT / SOURCE_PRESELECTION_REL
    if sha256_file(source_path) != SOURCE_PRESELECTION_SHA256:
        raise RuntimeError("source immutable preselection changed")
    anthropometry_path = ROOT / ANTHROPOMETRY_REL
    if sha256_file(anthropometry_path) != ANTHROPOMETRY_SHA256:
        raise RuntimeError("anthropometry provenance changed")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    run_dir.mkdir(parents=True, exist_ok=False)
    payload = {
        "schema": "biospur-pure-imu-v0-c2-measured-soft-prior-preselection-v1",
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "record_status": (
            "IMMUTABLE_BEFORE_NEW_C2_PAYLOAD_OPEN_TIMING_READ_OR_ACTION_SLICE_HASH"
        ),
        "selection_authority": {
            "path": str(SOURCE_PRESELECTION_REL),
            "sha256": SOURCE_PRESELECTION_SHA256,
            "exact_capture_role_order_episode_bounds_attempt_partition_and_exclusions": (
                "INCORPORATED_UNCHANGED_BY_HASH"
            ),
            "payload_or_slice_hash_used_to_select": False,
            "selection_changed": False,
        },
        "captures": {"CAPTURE2": source["captures"]["CAPTURE2"]},
        "external_prior_provenance": {
            "path": str(ANTHROPOMETRY_REL),
            "sha256": ANTHROPOMETRY_SHA256,
            "raw_readings_preserved_unaveraged": True,
            "surface_measurements_relabelled_as_internal_joint_truth": False,
            "loss_class": "EXACT_GAUSSIAN_OUTSIDE_ROBUST_SENSOR_LOSS",
            "segment_prior_mean_m": {
                "upper_arm_left": 0.3175,
                "upper_arm_right": 0.3175,
                "forearm_left": 0.255,
                "forearm_right": 0.255,
                "thigh_left": 0.48,
                "thigh_right": 0.48,
                "shank_left": 0.43,
                "shank_right": 0.43,
            },
            "segment_prior_sigma_m": {
                "upper_arm_left": 0.03,
                "upper_arm_right": 0.03,
                "forearm_left": 0.05,
                "forearm_right": 0.05,
                "thigh_left": 0.03,
                "thigh_right": 0.03,
                "shank_left": 0.06,
                "shank_right": 0.06,
            },
            "progressive_latent_geometry": (
                REAL_SUBJECT_PROGRESSIVE_GEOMETRY_PRIOR.audit()
            ),
            "bitrochanteric_0p335m_used_as_hip_joint_center_spacing": False,
            "bicristal_0p315_to_0p335m_used_as_hip_joint_center_spacing": False,
            "pelvis_torso_length_directly_measured": False,
        },
        "repair_contract": {
            "primary_capture": "CAPTURE2_FIRST_FORMAL_PANEL_GUIDED_CAPTURE",
            "capture1_role": "DEFERRED_DELAY_ROBUSTNESS_VALIDATION_NOT_FITTED_HERE",
            "measured_lengths_affect_solver_not_only_viewer": True,
            "data_only_information_audit_separate_from_prior_information": True,
            "factor_phase_allowlist": [
                "VERIFIED_PRE_REST",
                "REST_TO_ACTION_TRANSITION",
                "FORMAL_ACTION_OR_HOLD",
                "ACTION_TO_REST_TRANSITION",
                "VERIFIED_POST_REST",
            ],
            "unclassified_complete_episode_rows_in_objective": False,
            "standing_reference_actions": ["00_initial_still", "17_final_still"],
            "left_right_node_swap_used": False,
            "action_pose_template_used": False,
            "viewer_coordinates_used_as_truth": False,
            "gravity_direction_used_for_horizontal_normal_only": True,
            "ground_plane_height_observed": False,
            "pelvis_global_translation_observed": False,
            "force_plate_or_pressure_insole_used": False,
        },
        "source_binding": {
            str(path.relative_to(ROOT)): sha256_file(path)
            for path in (
                ROOT / "src/biospur_fusion/v0/raw6_heading.py",
                ROOT / "src/biospur_fusion/v0/physical_graph.py",
                ROOT / "src/biospur_fusion/v0/progressive_calibration.py",
                ROOT / "src/biospur_fusion/v0/progressive_synthetic.py",
                ROOT / "tools/run_pure_imu_v0_progressive.py",
                anthropometry_path,
                Path(__file__).resolve(),
            )
        },
        "scientific_contract": source["scientific_contract"],
        "global_exclusions": source["global_exclusions"],
        "disk_gate": {
            "nrf_ssd_free_gb_observed": nrf_free,
            "root_free_gb_observed": root_free,
            "projected_growth_gb": 0.25,
            "nrf_ssd_free_gb_min": 100,
            "root_free_gb_min": 40,
            "projected_growth_gb_max": 5,
            "pass": True,
        },
    }
    path = run_dir / "METADATA_PRESELECTION.json"
    dump_json(path, payload)
    path.chmod(0o444)
    print(json.dumps({
        "run_dir": str(run_dir),
        "preselection_sha256": sha256_file(path),
        "disk_gate": payload["disk_gate"],
    }, indent=2, sort_keys=True), flush=True)
    return run_dir


def configure() -> tuple[Path, dict, dict]:
    run_dir = ROOT / RUN_REL
    preselection_path = run_dir / "METADATA_PRESELECTION.json"
    if not preselection_path.exists() or preselection_path.stat().st_mode & 0o222:
        raise RuntimeError("fresh immutable preselection missing")
    progressive_runner.RUN_REL = RUN_REL
    progressive_runner.PRESELECTION_SHA256 = sha256_file(preselection_path)
    preselection = progressive_runner._preselection(ROOT)
    selection = progressive_runner._frozen_selection(ROOT, preselection)
    protocol = load_protocol(ROOT)
    return run_dir, selection, protocol


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("initialize", "synthetic", "capture2"))
    parser.add_argument("--max-nfev", type=int, default=60)
    parser.add_argument("--wall-limit-s", type=float, default=30.0)
    args = parser.parse_args()
    if args.mode == "initialize":
        initialize()
        return
    run_dir, selection, protocol = configure()
    if args.mode == "synthetic":
        progressive_runner._write_synthetic(
            run_dir, args.max_nfev, args.wall_limit_s,
        )
        return
    progressive_runner._run_capture(
        ROOT, run_dir, "CAPTURE2", selection, protocol,
        max_nfev=args.max_nfev, wall_limit_s=args.wall_limit_s,
    )


if __name__ == "__main__":
    main()
