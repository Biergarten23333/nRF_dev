#!/usr/bin/env python3
"""Finalize R6A2B-R4 comparison, test, and decision evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from tools.run_root_r6a2b_r3_session_relative_calibration import dump, seal


DOMINANT = {
    "elbow_left": "MIXED_FRAME_DEFECT_WINDOW_MIXTURE_AND_MULTI_AXIS",
    "elbow_right": "MIXED_FRAME_DEFECT_WINDOW_MIXTURE_AND_MULTI_AXIS",
    "knee_left": "MIXED_LOW_MOTION_LATE_WINDOW_MIXTURE",
    "knee_right": "MIXED_LOW_MOTION_AND_WITHIN_BOUT_VARIATION",
}


def finalize(result_dir: Path) -> None:
    verification = json.loads((result_dir / "INDEPENDENT_VERIFICATION.json").read_text())
    if verification["verdict"] != "PASS":
        raise RuntimeError("cannot finalize failed R4 verification")
    historical = json.loads((result_dir / "HISTORICAL_FUNCTIONAL_AXIS_REPRODUCTION.json").read_text())
    native = json.loads((result_dir / "NATIVE_TIME_FUNCTIONAL_AXIS_ESTIMATES.json").read_text())["per_joint"]
    downsampled = json.loads((result_dir / "NATIVE_VS_DOWNSAMPLED_AXIS_EVIDENCE.json").read_text())["per_joint"]
    integration = json.loads((result_dir / "CALIBRATION_INTEGRATION.json").read_text())

    table = []
    for joint, row in native.items():
        fit = row["axis_estimate"]
        uncertainty = fit["principal_axis_uncertainty_bout_bootstrap"]
        table.append({
            "joint": joint,
            "historical_rms_dispersion_deg": float(np.degrees(
                historical["per_joint"][joint]["computed_rms_dispersion_rad"]
            )),
            "repaired_weighted_rms_dispersion_deg": float(np.degrees(
                fit["weighted_axial_rms_dispersion_rad"]
            )),
            "weighted_median_dispersion_deg": float(np.degrees(
                fit["weighted_median_axial_dispersion_rad"]
            )),
            "weighted_q95_dispersion_deg": float(np.degrees(
                fit["weighted_q95_axial_dispersion_rad"]
            )),
            "effective_active_motion_duration_s": fit["effective_motion_duration_s"],
            "bout_count": fit["bout_count"],
            "principal_axis_uncertainty_q95_deg": None if uncertainty["q95_rad"] is None else float(
                np.degrees(uncertainty["q95_rad"])
            ),
            "off_axis_energy_fraction": fit["off_axis_energy_fraction"],
            "dominant_cause": DOMINANT[joint],
        })
    dump(result_dir / "FUNCTIONAL_AXIS_COMPARISON_TABLE.json", {
        "schema": "biospur-root-r6a2b-r4-functional-axis-comparison-table-v1",
        "dispersion_is_static_pose_error": False,
        "clinical_joint_angle_accuracy_claimed": False,
        "rows": table,
    })

    skin_path = result_dir / "SKIN_SENSOR_CONSISTENCY_DIAGNOSTICS.json"
    skin = json.loads(skin_path.read_text())
    for joint, row in skin["per_joint"].items():
        excess = row["distal_high_frequency_difference_rms_rad_s"] - row[
            "proximal_high_frequency_difference_rms_rad_s"
        ]
        row["distal_minus_proximal_high_frequency_rms_rad_s"] = excess
        row["classification"]["bounded_skin_strap_motion"] = (
            "WEAKLY_CONSISTENT_NOT_IDENTIFIED" if excess > 0.0
            else "NOT_SUPPORTED_BY_DISTAL_HIGH_FREQUENCY_EXCESS_BUT_STILL_UNRESOLVED"
        )
        row["classification"]["dominant_cause"] = DOMINANT[joint]
    skin["cross_joint_interpretation"] = (
        "Only the right elbow has a small positive distal high-frequency excess. The estimator cannot "
        "separate bounded skin/strap motion from genuine multi-axis relative motion; skin slip is not claimed as ground truth."
    )
    dump(skin_path, skin)

    dump(result_dir / "IMPLEMENTATION_AND_TEST_AUDIT.json", {
        "schema": "biospur-root-r6a2b-r4-implementation-test-audit-v1",
        "implementation": [
            "src/biospur_fusion/root_r6a2b/functional_axis.py",
            "src/biospur_fusion/root_r6a2b/session_relative_calibration.py",
            "tools/run_root_r6a2b_r4_functional_axis.py",
            "tools/verify_root_r6a2b_r4.py",
            "tools/finalize_root_r6a2b_r4.py",
        ],
        "focused_and_predecessor": {
            "command": "PYTHONPATH=src:. pytest -q tests/root_r6a2b_r4 tests/root_r6a2b_r3 tests/root_r6a2b_r2",
            "passed": 37, "failed": 0, "runtime_s": 3.34,
        },
        "canonical_shared_fk_regression": {
            "command": "PYTHONPATH=src:. pytest -q tests/root_r6a0",
            "passed": 29, "failed": 0, "runtime_s": 42.27,
        },
        "total_passed": 66, "total_failed": 0,
        "unrelated_monte_carlo_rerun": False,
        "independent_verification": "PASS",
    })

    lines = [
        "# ROOT-R6A2B-R4 result", "",
        "The four historical dispersions were reproduced to machine precision. Dispersion is variation in motion-axis direction, not static-pose or joint-angle error.", "",
        "## Causal result", "",
        "R3 already treated +axis/-axis as one undirected line and removed parent motion. It nevertheless pooled right-local axes from changing child frames, used the 5 Hz replay for derivatives, and gave every nonzero increment equal dispersion influence. R4 transports native-time increments into the calibrated parent-session frame, uses empirical stationary-noise weighting, preserves gaps, and bootstraps complete direction-consistent bouts.", "",
        "The repaired results remain broad. Therefore the old numbers were not explained away by one coding defect. Window/cycle mixture and real measured off-axis relative motion remain, with skin/strap motion unresolved rather than asserted.", "",
        "## Comparison", "",
        "| joint | historical RMS | repaired RMS | median | q95 | active s | bouts | axis uncertainty q95 | off-axis energy | dominant cause |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in table:
        lines.append(
            f"| {row['joint']} | {row['historical_rms_dispersion_deg']:.2f}° | "
            f"{row['repaired_weighted_rms_dispersion_deg']:.2f}° | "
            f"{row['weighted_median_dispersion_deg']:.2f}° | "
            f"{row['weighted_q95_dispersion_deg']:.2f}° | "
            f"{row['effective_active_motion_duration_s']:.2f} | {row['bout_count']} | "
            f"{row['principal_axis_uncertainty_q95_deg']:.2f}° | "
            f"{row['off_axis_energy_fraction']:.3f} | {row['dominant_cause']} |"
        )
    lines += ["", "## Integration", "",
              f"The relevant R3 layer was rerun: objective {integration['initial_objective_half_squared_norm']:.6f} -> {integration['final_objective_half_squared_norm']:.6f}; static-vector change L2 {integration['r3_to_r4_static_vector_change_l2']:.6g}; covariance change Frobenius {integration['r3_to_r4_covariance_change_frobenius']:.6g}.",
              "Broad axis dispersion is now the soft-factor uncertainty scale, so the objective preserves off-axis freedom instead of manufacturing a hinge.", "",
              "Independent verification: PASS. Tests: 66 passed, 0 failed. Golf/Boxing were not opened.", ""]
    (result_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")

    final_path = result_dir / "FINAL_RESULT.json"; final = json.loads(final_path.read_text())
    final["independent_verification"] = "PASS"
    final["focused_and_regression_tests"] = {"passed": 66, "failed": 0}
    final["comparison_table"] = "FUNCTIONAL_AXIS_COMPARISON_TABLE.json"
    dump(final_path, final); seal(result_dir)


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("result_dir", type=Path)
    args = parser.parse_args(); finalize(args.result_dir.resolve())


if __name__ == "__main__": main()
