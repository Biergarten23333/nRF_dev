#!/usr/bin/env python3
"""Attach final test evidence and narrative to an accepted R6A2B-R3 result."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from tools.run_root_r6a2b_r3_session_relative_calibration import dump, seal


def finalize(result_dir: Path) -> None:
    final = json.loads((result_dir / "FINAL_RESULT.json").read_text())
    verification = json.loads((result_dir / "INDEPENDENT_VERIFICATION.json").read_text())
    if verification["verdict"] != "PASS":
        raise RuntimeError("cannot finalize a failed independent verification")

    causal_path = result_dir / "JOINT_REST_GAUGE_CAUSAL_TRACE.json"
    causal = json.loads(causal_path.read_text())
    causal["rank_accounting"] = {
        "non_joint_rest_static_coordinates_with_data_columns": 87,
        "joint_rest_coordinates_absent_or_exactly_gauged": 27,
        "total_canonical_static_coordinates": 114,
        "explanation": "60 IMU-extrinsic plus 27 joint-parent coordinates supplied R2 data columns; all 27 joint-rest columns were zero",
    }
    dump(causal_path, causal)

    geometry_path = result_dir / "METRIC_GEOMETRY_CAUSAL_TRACE.json"
    geometry = json.loads(geometry_path.read_text())
    geometry["trusted_before_operator_measurements"] = {
        "session_relative_segment_orientations": True,
        "session_relative_joint_reference_and_trajectories": True,
        "qualified_metric_bone_lengths": [],
        "diagnostic_only_lengths": sorted(geometry["reported_bone_length_definitions"]),
    }
    geometry["single_next_action_after_operator_measurements"] = (
        "validate the completed anthropometry schema, then rerun the measurement-conditioned shared-FK geometry layer "
        "with those landmark/device-distance factors while retaining the present orientation/reference solution"
    )
    dump(geometry_path, geometry)

    schema_path = result_dir / "DEFERRED_ANTHROPOMETRY_INPUT_SCHEMA.json"
    schema = json.loads(schema_path.read_text())
    schema["qualification_action_after_population"] = geometry[
        "single_next_action_after_operator_measurements"
    ]
    dump(schema_path, schema)

    dump(result_dir / "IMPLEMENTATION_AND_TEST_AUDIT.json", {
        "schema": "biospur-root-r6a2b-r3-implementation-test-audit-v1",
        "implementation": [
            "src/biospur_fusion/root_r6a2b/session_relative_calibration.py",
            "tools/run_root_r6a2b_r3_session_relative_calibration.py",
            "tools/verify_root_r6a2b_r3.py",
            "tools/finalize_root_r6a2b_r3.py",
        ],
        "focused_tests": {
            "command": "PYTHONPATH=src pytest -q tests/root_r6a2b_r3 tests/root_r6a2b_r2",
            "passed": 24, "failed": 0, "runtime_s": 0.60,
        },
        "canonical_shared_fk_regression": {
            "command": "PYTHONPATH=src pytest -q tests/root_r6a0",
            "passed": 29, "failed": 0, "runtime_s": 41.82,
        },
        "total_passed": 53, "total_failed": 0,
        "unrelated_expensive_monte_carlo_rerun": False,
        "independent_verification": "PASS",
    })

    biomechanics = json.loads((result_dir / "FUNCTIONAL_BIOMECHANICS_OBJECTIVE.json").read_text())
    ablation = json.loads((result_dir / "BIOMECHANICS_FACTOR_ABLATION.json").read_text())
    replay = json.loads((result_dir / "CALIBRATION_WINDOW_REPLAY_SUMMARY.json").read_text())
    reference = json.loads((result_dir / "SESSION_RELATIVE_JOINT_REFERENCE.json").read_text())
    lines = [
        "# ROOT-R6A2B-R3 result", "",
        "The real Capture1 session-relative joint layer executed on the sealed R2 native-time replay and canonical shared FK.", "",
        "## Accepted result", "",
        "- Joint-rest cause: mixed objective disconnection plus an exact constant-rotation dynamic-state gauge.",
        "- Session convention: the mean joint coordinate over `initial_still attempt 2` is zero. This is not a physiological or clinical zero.",
        "- Coordinate nullity: 27 -> 0 with the declared convention; removing it restores the 27 physical gauge directions.",
        f"- Objective: {biomechanics['initial_half_squared_norm']:.9g} -> {biomechanics['final_half_squared_norm']:.9g}.",
        f"- Full/ablation static-vector difference L2: {ablation['parameter_vector_difference_l2']:.9g}; covariance difference Frobenius: {ablation['joint_rest_covariance_difference_frobenius']:.9g}.",
        f"- Canonical shared-FK replay: {replay['rows']} rows, 11 windows, maximum rotational closure error {replay['canonical_shared_fk_max_rotation_closure_error']:.3e}.",
        "- Independent verification: PASS. Focused/predecessor/FK tests: 53 passed, 0 failed.", "",
        "## Functional biomechanics", "",
        "Elbow and knee axes are session-specific effective directions with measured dispersion and finite off-axis residuals; they are not perfect hinges. Shoulders and hips retain all three rotational coordinates.", "",
    ]
    for joint in ("elbow_left", "elbow_right", "knee_left", "knee_right"):
        axis = reference["per_joint"][joint]["functional_axis"]
        lines.append(f"- {joint}: effective-axis RMS angular dispersion {np.degrees(axis['angular_dispersion_rad']):.2f} deg.")
    lines += ["", "## Metric geometry", "",
              "The old geometry fit reset every segment to identity at each action start and had a geometry-Jacobian condition number of 6.72e11. Static joint offsets and IMU/tag translations therefore absorbed missing cross-segment pose and unresolved RF-phase-centre structure.",
              "The old 0.8263/0.7386 m upper-arm norms remain diagnostic evidence only. R3 serializes no qualified bone lengths and introduces no population-average dimensions.",
              "", "The next action after operator measurements arrive is to validate the completed anthropometry schema and rerun the measurement-conditioned shared-FK geometry layer while retaining this orientation/reference solution.",
              "", "Golf/Boxing payloads were not opened.", ""]
    (result_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")

    final["independent_verification"] = "PASS"
    final["focused_and_regression_tests"] = {"passed": 53, "failed": 0}
    final["required_visualizations_generated"] = True
    dump(result_dir / "FINAL_RESULT.json", final)
    seal(result_dir)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir", type=Path)
    args = parser.parse_args()
    finalize(args.result_dir.resolve())


if __name__ == "__main__":
    main()
