#!/usr/bin/env python3
"""Run Phase S2 using synthetic observations only.

There is intentionally no CLI argument for a capture, ledger, UWB, T4, or
operator-measurement path.  The output is diagnostic evidence, never a frozen
calibration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any

import numpy as np


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "Fusion_Part" / "src"))

from biospur_fusion.imu_multi_action_s2.evaluation import (  # noqa: E402
    ablation_results, action_sensitivity, fit_multistart,
    linearized_finite_profile, negative_controls, svd_from_jacobian,
)
from biospur_fusion.imu_multi_action_s2.human_synthetic import (  # noqa: E402
    generate_human_motion_synthetic,
)
from biospur_fusion.imu_multi_action_s2.observability import S2UnifiedProblem  # noqa: E402
from biospur_fusion.imu_multi_action_s2.segmentation import segment_action_phases  # noqa: E402


CONFIG_REL = Path("Fusion_Part/config/imu_only_multi_action_centerline_calibration_v1_s2")
TEMPLATE_REL = Path("Fusion_Part/config/generic_template_motion_demo_v1/GENERIC_ADULT_PROXY_V1.json")
DEFAULT_OUTPUT_REL = Path("Fusion_Part/logs/imu_only_multi_action_centerline_calibration_v1_s2_synthetic")


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def write_json(path: Path, value: Any) -> None:
    path.write_bytes(canonical_bytes(value))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unsigned_angle_deg(left: np.ndarray, right: np.ndarray) -> float:
    cosine = abs(float(np.asarray(left) @ np.asarray(right)))
    return float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))


def truth_recovery(problem: S2UnifiedProblem, value: np.ndarray) -> dict:
    _, _, fp, fc, _ = problem.unpack(value)
    functional = {}
    for joint in problem.init.functional_parent_B:
        truth_pair = (problem.dataset.truth.hip_axis_B[joint] if joint.startswith("hip_")
                      else problem.dataset.truth.hinge_axis_B[joint])
        functional[joint] = {
            "parent_axis_error_deg_unsigned": unsigned_angle_deg(fp[joint], truth_pair[0]),
            "child_axis_error_deg_unsigned": unsigned_angle_deg(fc[joint], truth_pair[1]),
        }
    output = problem.output_metrics(value)
    maximum_functional = max(max(row.values()) for row in functional.values())
    gates = problem.gates["recovery"]
    passed = (
        output["maximum_segment_axis_error_deg"] <= float(gates["axis_error_deg"])
        and output["graphical_node_rms_mm"] <= float(gates["graphical_node_rms_mm"])
        and maximum_functional <= 2.0
    )
    return {
        "schema": "biospur-s2-synthetic-truth-recovery-v1",
        "comparison_modulo": "ONE_COMMON_GLOBAL_YAW_ONLY",
        "output_metrics": output,
        "functional_axes": functional,
        "maximum_functional_axis_error_deg_unsigned": maximum_functional,
        "predeclared_gates": gates,
        "pass": bool(passed),
        "verdict": "PASS" if passed else "FAIL_SYNTHETIC_RECOVERY",
    }


def report_text(verdict: str, segmentation: dict, recovery: dict,
                observability: dict, sensitivity: dict) -> str:
    unused = sensitivity["declared_action_unused"]
    return f"""# IMU-only multi-action centerline calibration V1 — Phase S2

## Verdict

`{verdict}`

`STOPPED_BEFORE_REAL_DATA_AS_REQUIRED`

This run is synthetic/model/observability-only. It did not read a real
calibration ledger, raw capture, UWB/T4, Anchor geometry, operator
anthropometry, walk, final_still, golf, or boxing. It did not generate a frozen
calibration or media and it was not committed or pushed by this runner.

## Action semantics used

`initial_still_attempt2` is natural upright standing with arms down. `t_pose`
is a separate single static arms-out hold; they are not averaged into one
reference pose. `arms` is segmented as left five, right five, then bilateral
five raise/lower cycles, with the raise plane estimated rather than named.

`left_knee` and `right_knee` mean front high-knee motion. Their main
calibration relation is pelvis-to-thigh/hip motion; the relaxed shank is not
fixed vertical. `left_heel` and `right_heel` mean rear heel-to-butt knee
flexion. Their main relation is thigh-to-shank/knee motion; they are neither
heel raises nor foot/ankle calibration. The two pairs are complementary
proximal and distal lower-chain excitations.

Each elbow window is split by observed change points into curl and forearm
pronation/supination; the two clusters are never pooled into one hinge PCA.
The trunk window is split into left turn, right turn, and forward
bend/recover, and all three use interior samples. Pelvis motion and off-axis
motion retain finite covariance; no body segment is fixed as an ideal robot
link.

## Evidence outcome

Signal-driven segmentation: **{'PASS' if segmentation['pass'] else 'FAIL'}**.
The reduced-state Jacobian at the selected nonconverged synthetic iterate has rank
{observability['rank']}/{observability['parameter_count']} at the unchanged
relative threshold `{observability['relative_threshold']}`. This is only a
`CONDITIONAL_LOCAL_DIAGNOSTIC_ON_INVALID_REDUCED_STATE`: 320 dynamic yaw
coordinates and four joint-zero coordinates were deleted without an equivalent
Schur/profile proof, while bias and lever-arm nuisance were fixed. It does not
qualify torso observability. At the same nonconverged iterate, maximum
segment-axis error was
{recovery['output_metrics']['maximum_segment_axis_error_deg']:.3f} degrees and
graphical-node RMS error was
{recovery['output_metrics']['graphical_node_rms_mm']:.3f} mm. The maximum
unsigned functional-axis error was
{recovery['maximum_functional_axis_error_deg_unsigned']:.3f} degrees.

These errors describe a nonconverged iterate and do not establish scientific
recovery failure. The candidate does **not** authorize real calibration. The current
functional-frame construction is not declared circular—the high-knee pelvis
plane, T-pose arm line, and trunk interior motion are formed from distinct
observation blocks—but poor truth recovery means that this independence is
not yet sufficient evidence of a correct product. No exact pelvis lock,
perfect motion plane, zero-heading prior, or tightened SVD threshold was used.

The common whole-body global-yaw freedom remains a coordinate gauge by
contract: rotating every segment and graphical point together changes only the
world display heading. The current parameterization removes that coordinate
from the optimizer; it does not claim compass heading. A fully independent
finite gauge replay is not accepted because the upstream recovery gate failed.

Declared phases with zero Jacobian information: `{unused}`. Detailed
action-to-residual-to-parameter evidence is in
`ACTION_RESIDUAL_PARAMETER_SENSITIVITY.json`. Linearized ablations and null
profiles are conditional diagnostics only and cannot rehabilitate the invalid
reduced state.

## What remains unobservable or unvalidated

Clinical joint centres, clinical angles, axial segment twist zero, absolute
heading, and root absolute translation are outside this product. More
importantly, the present S2 inverse model has not recovered a stable
publishable synthetic centerline, and its reduced state is not mathematically
equivalent to the required full state. Torso observability is therefore
`NOT_QUALIFIED`, regardless of the conditional 55/55 local rank. Real-data work
remains forbidden.
"""


def build_manifest(output: Path) -> dict:
    files = {}
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name != "SHA256_MANIFEST.json":
            files[path.name] = sha256(path)
    return {"schema": "biospur-s2-sha256-manifest-v1", "files": files}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=REPOSITORY_ROOT / DEFAULT_OUTPUT_REL)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    config_dir = REPOSITORY_ROOT / CONFIG_REL
    gates_path = config_dir / "s2_gates_v1.json"
    template_path = REPOSITORY_ROOT / TEMPLATE_REL
    gates = json.loads(gates_path.read_text())
    template = json.loads(template_path.read_text())
    if sha256(template_path) != gates["fixed_generic_template"]["sha256"]:
        raise RuntimeError("generic template SHA mismatch")

    # Configuration is copied byte-for-byte into the run evidence.
    for name in (
        "OPERATOR_ACTION_CONTRACT.json", "ACTION_SEMANTICS_MANIFEST.json",
        "FRAME_AND_GAUGE_CONTRACT.json", "HUMAN_MOTION_SYNTHETIC_CONTRACT.json",
        "HUMAN_MOTION_MODEL_CONTRACT.md", "TRUNK_FORWARD_AND_RESIDUAL_MODEL.md",
        "FUNCTIONAL_AXIS_AND_PLANE_MODEL.md",
    ):
        shutil.copyfile(config_dir / name, output / name)

    dataset = generate_human_motion_synthetic(gates, template, seed=int(gates["deterministic_seeds"][0]))
    segmentation = segment_action_phases(dataset, gates)
    write_json(output / "ACTION_PHASE_SEGMENTATION.json", segmentation)

    problem = S2UnifiedProblem(dataset, segmentation, gates, template)
    selected, multistart = fit_multistart(problem)
    write_json(output / "MULTISTART_STABILITY.json", multistart)
    recovery = truth_recovery(problem, selected)
    write_json(output / "SYNTHETIC_TRUTH_RECOVERY.json", recovery)

    jacobian = problem.numerical_jacobian(
        selected, step=float(gates["optimization"]["finite_difference_step"])
    )
    observability = svd_from_jacobian(problem, jacobian)
    observability["parameter_names"] = problem.parameter_names
    observability["global_yaw_gauge"] = {
        "optimizer_coordinate_removed": True,
        "physical_interpretation": "COMMON_RIGID_WORLD_YAW_ONLY",
        "independent_finite_replay": "BLOCKED_AFTER_SYNTHETIC_RECOVERY_FAILURE",
    }
    write_json(output / "SCALED_JACOBIAN_OBSERVABILITY.json", observability)

    sensitivity = action_sensitivity(problem, selected, jacobian, segmentation)
    write_json(output / "ACTION_RESIDUAL_PARAMETER_SENSITIVITY.json", sensitivity)
    write_json(output / "SYNTHETIC_ACTION_ABLATION.json", ablation_results(problem, selected, jacobian))
    write_json(output / "NEGATIVE_CONTROL_RESULTS.json", negative_controls(problem, selected, jacobian))
    write_json(output / "TRUNK_NULLSPACE_PROFILE.json", linearized_finite_profile(problem, selected, jacobian))

    noise_report = {
        "schema": "biospur-s2-noise-model-mismatch-v1",
        "configured_seeds": gates["deterministic_seeds"],
        "generator_includes": ["gyro white noise and bias", "accelerometer white noise and bias", "correlated gyro drift", "pelvis co-motion", "off-axis motion", "strap perturbation", "left-right asymmetry"],
        "completed_seed_fits": [gates["deterministic_seeds"][0]],
        "remaining_seed_fits": "NOT_RUN_AFTER_FIRST_SEED_SYNTHETIC_RECOVERY_FAILURE",
        "gate": "FAIL_SYNTHETIC_RECOVERY",
    }
    write_json(output / "SYNTHETIC_NOISE_AND_MODEL_MISMATCH.json", noise_report)

    # Attempt A is a terminal failed checkpoint.  Its 55-coordinate objective
    # is not a validated Schur reduction of the required full state, so no
    # convergence or local-rank result can produce a calibration PASS.
    verdict = "FAIL_SYNTHETIC_RECOVERY"
    write_json(output / "FAILURE_PRECEDENCE.json", {
        "TOP_LEVEL": verdict,
        "PRIMARY_BLOCKER": "FAIL_REDUCED_STATE_INVALID",
        "SECONDARY_BLOCKER": "SOLVER_NOT_CONVERGED",
        "TORSO_OBSERVABILITY": "NOT_QUALIFIED",
        "REAL_DATA_ACCESS": "FORBIDDEN",
    })
    deterministic = {
        "schema": "biospur-s2-deterministic-replay-v1",
        "required_complete_replays": 2,
        "completed_complete_replays": 1,
        "byte_identical": False,
        "status": "NOT_RUN_SECOND_TIME_AFTER_SYNTHETIC_RECOVERY_FAILURE",
        "verdict_effect": "PASS_FORBIDDEN",
    }
    write_json(output / "DETERMINISTIC_REPLAY.json", deterministic)
    write_json(output / "DATA_ACCESS_AUDIT.json", {
        "schema": "biospur-s2-data-access-audit-v1",
        "opened_paths": [str((config_dir / name).resolve()) for name in (
            "s2_gates_v1.json", "OPERATOR_ACTION_CONTRACT.json",
            "ACTION_SEMANTICS_MANIFEST.json", "FRAME_AND_GAUGE_CONTRACT.json",
            "HUMAN_MOTION_SYNTHETIC_CONTRACT.json", "HUMAN_MOTION_MODEL_CONTRACT.md",
            "TRUNK_FORWARD_AND_RESIDUAL_MODEL.md", "FUNCTIONAL_AXIS_AND_PLANE_MODEL.md",
        )] + [str(template_path.resolve())],
        "synthetic_generator": "generate_human_motion_synthetic(seed=2201)",
        "forbidden_inputs": gates["forbidden_inputs"],
        "forbidden_input_open_count": 0,
        "hardware_accessed": False,
        "real_payload_accessed": False,
        "frozen_calibration_created": False,
        "media_created": False,
    })
    (output / "TEST_RESULTS.md").write_text(
        "# Test results\n\nPending the repository test command run after artifact generation.\n"
        "The scientific verdict is already fail-closed and cannot be changed by test count.\n"
    )
    (output / "REPORT.md").write_text(report_text(verdict, segmentation, recovery, observability, sensitivity))
    write_json(output / "SHA256_MANIFEST.json", build_manifest(output))
    print(json.dumps({"verdict": verdict, "output": str(output), "real_data": "SEALED"}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
