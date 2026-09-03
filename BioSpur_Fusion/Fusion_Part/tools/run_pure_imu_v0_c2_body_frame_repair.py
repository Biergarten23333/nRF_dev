#!/usr/bin/env python3
"""Fresh C2-only repair for clean phases, leg topology, and body-frame chirality."""
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


RUN_REL = Path("logs/pure_imu_v0_c2_standing_chain_repair_20260828T162222Z")
SOURCE_PRESELECTION_REL = Path(
    "logs/pure_imu_v0_progressive_calibration_20260828T132736Z/"
    "METADATA_PRESELECTION.json"
)
SOURCE_PRESELECTION_SHA256 = (
    "dd9867e999fe63dd0f25fe4d645d5c17bdc8e799c841664df3ca083c5c7db2bf"
)


def initialize() -> Path:
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
    source = json.loads(source_path.read_text(encoding="utf-8"))
    run_dir.mkdir(parents=True, exist_ok=False)
    payload = {
        "schema": "biospur-pure-imu-v0-c2-standing-chain-repair-preselection-v1",
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
        "repair_contract": {
            "primary_capture": "CAPTURE2_FIRST_FORMAL_PANEL_GUIDED_CAPTURE",
            "capture1_role": "DEFERRED_DELAY_ROBUSTNESS_VALIDATION_NOT_FITTED_HERE",
            "factor_phase_allowlist": [
                "VERIFIED_PRE_REST",
                "REST_TO_ACTION_TRANSITION",
                "FORMAL_ACTION_OR_HOLD",
                "ACTION_TO_REST_TRANSITION",
                "VERIFIED_POST_REST",
            ],
            "unclassified_complete_episode_rows_in_objective": False,
            "lower_limb_branch": (
                "SIGNAL_VERIFIED_REST_LEFT_RIGHT_TOPOLOGY_PLUS_"
                "NATURAL_STANDING_GRAVITY_TILT_FULL_CHAIN_CHIRALITY_AND_VERTICAL_ORDER"
            ),
            "standing_reference_actions": ["00_initial_still", "17_final_still"],
            "standing_frame_tilt_tolerance_deg": 30.0,
            "minimum_right_minus_left_knee_and_shank_sensor_m": 0.02,
            "action_pose_template_used": False,
            "viewer_coordinates_used_as_truth": False,
            "left_right_node_swap_used": False,
            "gravity_direction_used_for_natural_standing_tilt_only": True,
            "ground_plane_used": False,
            "pelvis_global_translation_observed": False,
        },
        "source_binding": {
            str(path.relative_to(ROOT)): sha256_file(path)
            for path in (
                ROOT / "src/biospur_fusion/v0/raw6_heading.py",
                ROOT / "src/biospur_fusion/v0/progressive_calibration.py",
                ROOT / "src/biospur_fusion/v0/progressive_synthetic.py",
                ROOT / "tools/run_pure_imu_v0_progressive.py",
                Path(__file__).resolve(),
            )
        },
        "external_prior_provenance": source["external_prior_provenance"],
        "scientific_contract": source["scientific_contract"],
        "global_exclusions": source["global_exclusions"],
        "disk_gate": {
            "nrf_ssd_free_gb_observed": nrf_free,
            "root_free_gb_observed": root_free,
            "projected_growth_gb": 0.20,
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
    parser.add_argument("--max-nfev", type=int, default=50)
    parser.add_argument("--wall-limit-s", type=float, default=20.0)
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
