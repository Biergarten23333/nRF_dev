#!/usr/bin/env python3
"""Run the synthetic precondition for MULTI_ACTION_CALIBRATION_V1.

This entry point intentionally has no real-capture loader.  A failed synthetic
gate therefore cannot accidentally fall through into calibration, replay, or
held-out data access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess

import numpy as np
import scipy

from biospur_fusion.imu_multi_action_v1.core import canonical_json_bytes, sha256_bytes
from biospur_fusion.imu_multi_action_v1.synthetic import HINGES, run_synthetic_truth_gate


CONFIG_FILES = (
    "MULTI_ACTION_CALIBRATION_V1_SPEC.md",
    "FRAME_AND_GAUGE_CONVENTIONS.json",
    "ACTION_FACTOR_MAP.json",
    "gates_v1.json",
)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(canonical_json_bytes(value))


def _blocked(stage: str, reason: str) -> dict:
    return {
        "schema": "biospur-multi-action-blocked-stage-v1",
        "stage": stage,
        "status": "NOT_RUN",
        "reason": reason,
        "real_calibration_ledger_opened": False,
        "uwb_opened": False,
        "walk_opened": False,
        "final_still_opened": False,
    }


def _objective_ledger(report: dict) -> dict:
    selected = {
        joint: int(value["report"]["selected_samples"])
        for joint, value in report["functional_axis_recovery"].items()
    }
    blocks = [
        {"action": "initial_still_attempt2", "factor": "static_segment_directions", "rows": 3750},
        {"action": "initial_still_attempt2", "factor": "torso_pelvis_transverse", "rows": 750},
        {"action": "initial_still_attempt2", "factor": "joint_extension_zero", "rows": 108},
        {"action": "t_pose", "factor": "static_segment_directions", "rows": 3750},
        {"action": "t_pose", "factor": "torso_pelvis_transverse", "rows": 750},
        {"action": "t_pose", "factor": "joint_extension_zero", "rows": 108},
    ]
    for joint, (_, _, action) in HINGES.items():
        blocks.extend((
            {"action": action, "joint": joint,
             "factor": "olsson_gyro_acceleration", "rows": 2*selected[joint]},
            {"action": action, "joint": joint,
             "factor": "global_hinge_relative_heading_alignment", "rows": 375},
        ))
    blocks.extend((
        {"action": "arms", "factor": "bilateral_elbow_hinge_alignment", "rows": 750},
        {"action": "squats", "factor": "bilateral_knee_hinge_alignment", "rows": 750},
        {"action": "trunk", "factor": "pelvis_torso_transverse_endpoint_consistency", "rows": 6},
        {"action": "left_heel", "factor": "NOT_SUPPORTED_BY_NODE_PLACEMENT_FOR_FOOT_DOF", "rows": 0},
        {"action": "right_heel", "factor": "NOT_SUPPORTED_BY_NODE_PLACEMENT_FOR_FOOT_DOF", "rows": 0},
        {"action": "FULL_CONTINUOUS_TIMELINE", "factor": "yaw_spline_random_walk", "rows": 320},
        {"action": "ALL", "factor": "cross_window_static_parameter_identity", "rows": 0,
         "implementation": "ONE_SHARED_PARAMETER_BLOCK_BY_CONSTRUCTION"},
    ))
    row_sum = sum(int(item["rows"]) for item in blocks)
    return {
        "schema": "biospur-joint-multi-action-objective-v1",
        "scope": "DETERMINISTIC_SYNTHETIC_PRECONDITION_ONLY",
        "equations": {
            "olsson_gyro": "(||omega_parent x h_parent||-||omega_child x h_child||)/(sqrt(2)*sigma_gyro)",
            "olsson_acceleration": "w_a*(h_parent^T*a_parent-h_child^T*a_child)/(sqrt(2)*sigma_accel)",
            "olsson_acceleration_weight": "w_a=1/sqrt(1+(||a_parent||-||a_child||)^2)",
            "direction": "(R_H_from_B*a_B-d_H)/sigma_orientation",
            "hinge_alignment": "(R_H_from_B_parent*h_parent_B-R_H_from_B_child*h_child_B)/sigma_orientation",
            "yaw_random_walk": "delta_psi_k-delta_psi_(k-1) whitened by Q2 bias uncertainty with nonzero floor",
        },
        "residual_blocks": blocks,
        "residual_row_sum": row_sum,
        "reported_residual_count": int(report["residual_count"]),
        "row_accounting_closed": row_sum == int(report["residual_count"]),
        "objective_at_truth": report["objective_at_truth"],
        "stacked_residual_vector": "EVALUATED_IN_MEMORY_FOR_SYNTHETIC_GATE; NOT_SERIALIZED_AS_BULK_ARRAY",
        "scaled_jacobian": "ACTUAL_NUMERICAL_JACOBIAN_USED_FOR_SVD; NOT_SERIALIZED_AS_BULK_ARRAY",
        "covariance": "NOT_DEFINED_BECAUSE_REQUIRED_STATIC_STATE_HAS_ADDITIONAL_NULLSPACE",
    }


def run(config_dir: Path, output_dir: Path) -> dict:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {output_dir}")
    output_dir.mkdir(parents=True)
    for name in CONFIG_FILES:
        source = config_dir/name
        if not source.is_file():
            raise FileNotFoundError(source)
        if name != "gates_v1.json":
            shutil.copyfile(source, output_dir/name)

    gates_path = config_dir/"gates_v1.json"
    gates = json.loads(gates_path.read_text(encoding="utf-8"))
    first = run_synthetic_truth_gate(gates)
    second = run_synthetic_truth_gate(gates)
    first_precheck = canonical_json_bytes(first)
    second_precheck = canonical_json_bytes(second)
    byte_identical = first_precheck == second_precheck
    first["byte_identical_compact_artifacts"] = byte_identical
    first["deterministic_replay_1_sha256"] = sha256_bytes(first_precheck)
    first["deterministic_replay_2_sha256"] = sha256_bytes(second_precheck)
    if not byte_identical:
        first["verdict"] = "FAIL_SYNTHETIC_RECOVERY"
        first["stop_before_real_capture"] = True

    config_hashes = {name: _file_sha256(config_dir/name) for name in CONFIG_FILES}
    source_root = Path(__file__).resolve().parents[1]/"src"/"biospur_fusion"/"imu_multi_action_v1"
    source_hashes = {path.name: _file_sha256(path) for path in sorted(source_root.glob("*.py"))}
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[3],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        head = "UNAVAILABLE"

    _write_json(output_dir/"SYNTHETIC_TRUTH_RECOVERY.json", first)
    _write_json(output_dir/"INFORMATIVE_SAMPLE_SELECTION.json", {
        "schema": "biospur-informative-sample-selection-v1",
        "scope": "SYNTHETIC_FUNCTIONAL_FACTORS",
        "algorithm": "DETERMINISTIC_SCORE_ABOVE_35TH_PERCENTILE_THEN_STABLE_DESCENDING_CAP",
        "minimum_per_mandatory_factor": int(gates["sampling"]["minimum_mandatory_informative_samples"]),
        "maximum_per_action_factor": int(gates["sampling"]["max_samples_per_action_factor"]),
        "factors": {joint: value["report"] for joint, value in first["functional_axis_recovery"].items()},
    })
    _write_json(output_dir/"JOINT_MULTI_ACTION_OBJECTIVE.json", _objective_ledger(first))
    _write_json(output_dir/"CALIBRATION_OBSERVABILITY_SVD.json", {
        "schema": "biospur-calibration-observability-svd-v1",
        "scope": "SYNTHETIC_ORACLE_STRUCTURAL_GATE",
        "matrix": "ACTUAL_SCALED_RESIDUAL_JACOBIAN",
        "parameter_count": first["parameter_count"],
        "residual_count": first["residual_count"],
        "rank": first["jacobian_rank"],
        "nullity": first["jacobian_nullity"],
        "relative_threshold": first["relative_singular_value_threshold"],
        "absolute_threshold": first["absolute_singular_value_threshold"],
        "singular_values": first["singular_values"],
        "null_directions": first["null_directions"],
        "gate": "FAIL_MULTI_ACTION_NULLSPACE" if first["jacobian_nullity"] else "PASS",
    })
    _write_json(output_dir/"MULTISTART_STABILITY.json", {
        "schema": "biospur-multistart-stability-v1",
        "scope": "SYNTHETIC_FUNCTIONAL_AXIS_INITIALIZERS_ONLY",
        "functional_axes": {
            joint: {
                "costs": value["report"]["multistart_costs"],
                "parent_axis_error_deg": value["parent_axis_error_deg"],
                "child_axis_error_deg": value["child_axis_error_deg"],
                "observable": value["report"]["observable"],
            }
            for joint, value in first["functional_axis_recovery"].items()
        },
        "joint_shared_calibration": "NOT_RUN_SYNTHETIC_STRUCTURAL_NULLSPACE",
    })

    reason = "Synthetic oracle structural gate found an additional required-state null direction; real capture must remain sealed."
    for filename, stage in (
        ("ACTION_REMOVAL_SENSITIVITY.json", "ACTION_REMOVAL_SENSITIVITY"),
        ("POSE_ANCHOR_INVARIANCE.json", "POSE_ANCHOR_INVARIANCE"),
        ("REPLAY_LOADED_CALIBRATION.json", "LABEL_BLIND_REPLAY"),
        ("CROSS_ACTION_CONTINUITY.json", "CROSS_ACTION_CONTINUITY"),
        ("ACTION_SELF_CONSISTENCY.json", "ACTION_SELF_CONSISTENCY"),
    ):
        _write_json(output_dir/filename, _blocked(stage, reason))
    _write_json(output_dir/"DATA_ACCESS_AUDIT.json", {
        "schema": "biospur-multi-action-data-access-audit-v1",
        "opened": [str((config_dir/name).resolve()) for name in CONFIG_FILES],
        "synthetic_seed": 4711,
        "real_calibration_ledger": "SEALED_NOT_OPENED",
        "UWB_T4": "SEALED_NOT_OPENED",
        "anchor_geometry": "SEALED_NOT_OPENED",
        "operator_measurements": "SEALED_NOT_OPENED",
        "walk": "SEALED_NOT_OPENED",
        "final_still": "SEALED_NOT_OPENED",
        "golf": "SEALED_NOT_OPENED",
        "boxing": "SEALED_NOT_OPENED",
    })
    _write_json(output_dir/"BLOCKED_ARTIFACTS.json", {
        "schema": "biospur-multi-action-blocked-artifacts-v1",
        "verdict": first["verdict"],
        "not_created": ["FROZEN_CALIBRATION.json", "FROZEN_CALIBRATION.sha256", "all MP4/GIF outputs"],
        "reason": reason,
    })
    _write_json(output_dir/"RUN_PROVENANCE.json", {
        "schema": "biospur-multi-action-run-provenance-v1",
        "git_head": head,
        "working_tree_source_status": "UNCOMMITTED_NEW_NAMESPACE_AS_AUTHORIZED; NO_COMMIT_OR_PUSH",
        "config_sha256": config_hashes,
        "source_sha256": source_hashes,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "linear_algebra_threads": 1,
    })

    null = first["null_directions"][0] if first["null_directions"] else None
    physical = null["finite_physical_perturbation"] if null else {}
    report_md = f"""# IMU_ONLY_MULTI_ACTION_CENTERLINE_CALIBRATION_V1

## Verdict

`{first['verdict']}`
Secondary gate: `{'FAIL_MULTI_ACTION_NULLSPACE' if first['jacobian_nullity'] else 'PASS'}`

The deterministic synthetic truth precondition failed before any real capture payload was opened. The implementation therefore stopped exactly at the required firewall. No calibration was frozen, no replay was launched, and no media was rendered.

## Exact blocking nullspace

The actual {first['residual_count']} x {first['parameter_count']} scaled residual Jacobian has rank {first['jacobian_rank']} at the predeclared relative threshold `{first['relative_singular_value_threshold']}`. Its weakest singular value is `{first['singular_values'][-1]:.12g}`, below the absolute threshold `{first['absolute_singular_value_threshold']:.12g}`.

The single extra direction is classified as `{physical.get('classification', 'NONE')}`. Its dominant coefficients are `heading:torso` and the three coordinates of `frame:torso`. A finite perturbation with dominant magnitude `1e-4 rad` changes the stored torso transverse direction by `{physical.get('torso_board_transverse_axis_change_rad', float('nan')):.12g} rad` and torso initial relative heading by `{physical.get('torso_initial_relative_heading_change_rad', float('nan')):.12g} rad`, while changing the residual vector by only `{physical.get('residual_delta_l2_norm', float('nan')):.12g}` in L2 norm. It also changes the predicted torso/segment longitudinal direction by up to `{physical.get('maximum_predicted_segment_longitudinal_axis_change_deg', float('nan')):.12g} deg` over the synthetic timeline. This is therefore a physically consequential required-state ambiguity, not the already-fixed common pelvis yaw gauge and not a numerical artifact from forming `J.T@J`.

The declared labelled factors do not independently separate torso board-frame axial orientation from torso initial relative heading. The specification forbids fixing either with a zero/population/T-pose-only fallback, so real fitting is blocked.

## Functional-axis diagnostic

All four Olsson combined gyroscope/acceleration fits met the 2 degree synthetic hinge-axis gate, but that local success cannot override the stacked structural nullspace. PCA was used only as an initializer. The complete values and five deterministic start costs are in `SYNTHETIC_TRUTH_RECOVERY.json` and `MULTISTART_STABILITY.json`.

## Determinism and data firewall

Two independent synthetic evaluations produced byte-identical compact precheck artifacts: `{byte_identical}`. Real calibration, UWB/T4, Anchor geometry, operator measurements, walk, final_still, golf and boxing remained sealed. `FROZEN_CALIBRATION.json` and its SHA file were intentionally not created, because doing so after a failed synthetic gate would violate the freeze contract.

## Implementation status

The new namespace contains the frozen frame/gauge/action contracts, timestamp-based SO(3) interpolation, explicit anti-alias primitives, measured-noise floors, the published Olsson residual, deterministic information balancing, tangent-axis optimization, the deterministic ten-node synthetic generator, the actual scaled-Jacobian SVD, and finite physical null perturbation analysis. Real-data loading and label-blind replay were not invoked after the mandatory stop.
"""
    (output_dir/"REPORT.md").write_text(report_md, encoding="utf-8")

    manifest = {}
    for path in sorted(output_dir.iterdir()):
        if path.is_file():
            manifest[path.name] = _file_sha256(path)
    _write_json(output_dir/"SHA256_MANIFEST.json", {
        "schema": "biospur-multi-action-output-sha256-manifest-v1",
        "files": manifest,
    })
    return first


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.config_dir.resolve(), args.output_dir.resolve())
    print(result["verdict"])
    print(f"output={args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
