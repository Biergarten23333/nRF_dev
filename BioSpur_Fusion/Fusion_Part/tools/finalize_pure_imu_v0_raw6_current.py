#!/usr/bin/env python3
"""Seal the current-semantics pure-IMU V0 decision from derived evidence only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "logs/pure_imu_v0_raw6_bounded_access_20260828T060355Z"
SYNTHETIC_RUN = ROOT / "logs/pure_imu_v0_raw6_edge_global_20260828T050124Z"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "sha256": sha256(path)}


def capture_summary(capture: str) -> dict[str, Any]:
    result_path = RUN / f"{capture}_CURRENT_QMT_RESULT.json"
    gates_path = RUN / f"{capture}_CURRENT_FINAL_GATES.json"
    access_path = RUN / f"{capture}_PAYLOAD_ACCESS_AUDIT.json"
    result = load(result_path)
    gate_artifact = load(gates_path)
    decision = gate_artifact["decision"]
    access = load(access_path)
    viewer_rederived = RUN / f"{capture}_CURRENT_VIEWER_REDERIVATION.json"
    if viewer_rederived.exists():
        viewer_container = load(viewer_rederived)
        viewer = viewer_container["new_viewer"]
        viewer_evidence = artifact(viewer_rederived)
    else:
        viewer = result["direct_fk_viewer"]
        viewer_evidence = artifact(result_path)
    viewer_path = Path(viewer["path"])
    unified = result["unified_graph"]
    transition = result["transition_ablation"]
    qmt = {
        edge: {
            "training_actions": row["qmt"]["qualification"][
                "signal_qualified_training_actions"
            ],
            "pooled_heading_deg": row["qmt"]["qualification"]["pooled_heading_deg"],
            "axis_multistart_spread_deg": row["qmt"]["qualification"][
                "axis_multistart_spread_deg"
            ],
            "all_action_diagnostic_spread_deg": row["qmt"][
                "action_estimate_spread_deg"
            ],
            "held_out_actions": row["qmt"]["held_out_comparison"][
                "signal_qualified_actions"
            ],
            "train_to_heldout_difference_deg": row["qmt"][
                "held_out_comparison"
            ]["train_to_heldout_heading_difference_deg"],
            "qualification_pass": row["qmt"]["qualification"]["pass"],
        }
        for edge, row in result["edgewise_baseline"]["edges"].items()
        if row["qmt"] is not None
    }
    proof_rows = access["hostile_timing_access_proofs"]
    initial_action = "initial_still" if capture == "CAPTURE1" else "00_initial_still"
    initial_drift = result["drift_stillness"]["actions"][initial_action]
    return {
        "result": artifact(result_path),
        "final_gates": artifact(gates_path),
        "bounded_access_audit": artifact(access_path),
        "viewer_evidence": viewer_evidence,
        "viewer": artifact(viewer_path),
        "decision": decision,
        "relative_headings_deg": unified["headings_deg"],
        "edge_headings_deg": {
            edge: row["relative_heading_deg"] for edge, row in unified["edges"].items()
        },
        "gauge": {
            "count": unified["root_yaw_gauge_count"],
            "segment": unified["root_yaw_gauge_segment"],
            "publishable_relative_heading_dimension": unified[
                "publishable_heading_dimension"
            ],
        },
        "rank_and_globalization": {
            "rank_after_gauge": unified["numeric_rank_after_gauge"],
            "condition_number": unified["condition_number"],
            "minimum_singular_value": unified["numeric_singular_values"][-1],
            "multistart_count": len(unified["multistart"]),
            "broad_independent_start_count": unified["multistart_contract"][
                "broad_independent_start_count"
            ],
            "maximum_heading_spread_deg": unified["multistart_max_spread_deg"],
            "cost_spread": (
                max(row["cost"] for row in unified["multistart"])
                - min(row["cost"] for row in unified["multistart"])
            ),
            "all_nine_full_circle_profiled": unified["multistart_contract"][
                "all_nine_coordinates_receive_full_circle_coverage_per_start"
            ],
        },
        "qmt_excitation_aware": qmt,
        "b5_physical_rms_mps2": {
            edge: {
                "train": row["train"]["physical_rms_mps2"],
                "held_out": row["held_out"]["physical_rms_mps2"],
            }
            for edge, row in unified["edges"].items()
        },
        "transition_removal": {
            "full_rank": transition["full_complete_episode_rank"],
            "formal_action_only_rank": transition["formal_action_only_rank"],
            "smallest_singular_information_retained_ratio": transition[
                "smallest_singular_information_retained_ratio"
            ],
            "maximum_heading_change_deg": transition["maximum_heading_change_deg"],
        },
        "initial_still_raw6_drift": {
            segment: {
                "pre_post_rotation_distance_deg": row[
                    "pre_post_rotation_distance_deg"
                ],
                "pre_bias_norm_deg_s": row["pre_bias_norm_deg_s"],
                "post_bias_norm_deg_s": row["post_bias_norm_deg_s"],
            }
            for segment, row in initial_drift.items()
        },
        "hostile_access": {
            "selected_actions": len(access["selected_actions"]),
            "proof_count": len(proof_rows),
            "actual_os_read_calls": sum(
                row["proof"]["actual_os_read_calls"] for row in proof_rows
            ),
            "actual_os_seek_calls": sum(
                row["proof"]["actual_os_seek_calls"] for row in proof_rows
            ),
            "binary_search_probe_count": sum(
                row["proof"]["binary_search_probe_count"] for row in proof_rows
            ),
            "all_action_plus_two_superframe_brackets": all(
                row["proof"][
                    "all_sequential_timing_rows_within_action_plus_two_superframes"
                ] for row in proof_rows
            ),
            "all_binary_probes_accounted": all(
                row["proof"]["every_binary_search_probe_separately_accounted"]
                for row in proof_rows
            ),
            "no_full_traversal": all(
                row["proof"]["no_full_file_traversal_proven_by_actual_read_union"]
                for row in proof_rows
            ),
            "all_hxx_timing_bytes_untouched": all(
                not row["proof"]["all_hxx_timing_interval_bytes_touched"]
                for row in proof_rows
            ),
            "hxx_payload_opened": access["hxx_payload_opened"],
            "capture3_payload_opened": access["capture3_payload_opened"],
        },
        "direct_fk": viewer["quantitative_direct_fk"],
    }


def main() -> None:
    outputs = [RUN / "VISUAL_QA.json", RUN / "FINAL_DECISION.json", RUN / "REPORT.md"]
    if any(path.exists() for path in outputs):
        raise RuntimeError("refusing to overwrite sealed final evidence")

    preselection = RUN / "METADATA_PRESELECTION.json"
    synthetic_path = SYNTHETIC_RUN / "SYNTHETIC_QUALIFICATION.json"
    prior_failure_path = SYNTHETIC_RUN / "SYNTHETIC_BROAD_START_FAILURE_PROVISIONAL.json"
    contaminated_path = SYNTHETIC_RUN / "ACCESS_CONTAMINATED_NOT_QUALIFYING.json"
    synthetic = load(synthetic_path)
    captures = {capture: capture_summary(capture) for capture in ("CAPTURE1", "CAPTURE2")}

    visual = {
        "schema": "biospur-pure-imu-v0-browser-visual-qa-v1",
        "inspection_surface": "Codex in-app browser against a loopback-only HTTP server",
        "viewer_pose_used_as_estimator_input": False,
        "pose_label_used_to_correct_or_rescue_fit": False,
        "observations": {
            "CAPTURE1": {
                "viewer": captures["CAPTURE1"]["viewer"],
                "window": "initial_still",
                "time_ns_observed": 16_520_000_000,
                "boundary": "FORMAL_ACTION_OR_HOLD",
                "maximum_joint_angle_deg_displayed": 170.2,
                "observation": (
                    "Front view remains grossly folded and self-crossed after the "
                    "trunk-frame FK correction; it is incompatible with the independent "
                    "natural-standing protocol sanity description."
                ),
                "verdict": "FAIL",
            },
            "CAPTURE2": {
                "viewer": captures["CAPTURE2"]["viewer"],
                "windows": ["00_initial_still", "16_squat"],
                "initial_still_midpoint_maximum_joint_angle_deg": 174.3,
                "squat_midpoint_maximum_joint_angle_deg": 163.4,
                "observation": (
                    "Initial-still and squat front views retain a folded upper-body "
                    "chain with self-crossing limbs; the replay is not anatomically coherent."
                ),
                "verdict": "FAIL",
            },
        },
        "overall": "FAIL",
    }
    (RUN / "VISUAL_QA.json").write_text(
        json.dumps(visual, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    decision = {
        "schema": "biospur-pure-imu-v0-final-decision-v1",
        "verdict": "FAIL",
        "decision_semantics": (
            "Real-data results directly violate factor-consistency, fitted-connection, "
            "ROM, and direct-FK gates. This is not an absence-of-data INCONCLUSIVE result."
        ),
        "preselection": artifact(preselection),
        "preselection_read_only": not bool(preselection.stat().st_mode & 0o222),
        "synthetic": {
            "qualification": artifact(synthetic_path),
            "pass": synthetic["pass"],
            "rank_after_gauge": synthetic["exact_graph"]["rank_after_gauge"],
            "maximum_heading_error_deg": synthetic["exact_graph"][
                "maximum_heading_error_deg"
            ],
            "maximum_broad_multistart_spread_deg": synthetic[
                "broad_multistart_evidence"
            ]["maximum_heading_spread_deg"],
            "noise_maximum_heading_errors_deg": [
                row["maximum_heading_error_deg"] for row in synthetic["noise_robustness"]
            ],
            "transition_information_retained_ratio": synthetic[
                "transition_removal"
            ]["information_retained_ratio"],
            "all_nine_degenerate_edge_controls_drop_to_rank_eight": all(
                row["rank_after_gauge"] == 8
                for row in synthetic["negative_controls"]
            ),
            "prior_broad_start_failure": artifact(prior_failure_path),
            "prior_broad_start_failure_spread_deg": load(prior_failure_path)[
                "maximum_heading_spread_deg"
            ],
            "synthetic_success_is_not_product_pass": True,
        },
        "access_contaminated_prior_run": artifact(contaminated_path),
        "access_contaminated_prior_run_qualifies": False,
        "captures": captures,
        "visual_qa": artifact(RUN / "VISUAL_QA.json"),
        "tests": {
            "command": (
                "PYTHONPATH=src .venv-v0/bin/python -m pytest -q "
                "tests/v0/test_bounded_capture1_access.py "
                "tests/v0/test_v0_raw6_heading.py "
                "tests/v0/test_v0_validation_plumbing.py "
                "tests/v0/test_v0_dual_capture.py"
            ),
            "passed": 73,
            "failed": 0,
            "elapsed_s": 39.78,
        },
        "global_scientific_contract": {
            "raw_accelerometer_gyroscope_only": True,
            "magnetometer_used": False,
            "vendor_or_global_orientation_truth_used": False,
            "old_qmt_off_or_shared_ik_state_used": False,
            "manual_quaternion_or_viewer_pose_used": False,
            "action_label_used_as_pose_truth": False,
            "b4_used_or_required": False,
            "exactly_one_pelvis_yaw_gauge_per_capture": True,
            "captures_fitted_independently": True,
            "hxx_or_capture3_payload_opened": False,
            "uwb_or_fusion_state_used": False,
        },
        "failed_real_gates": {
            capture: summary["decision"]["failed_gates"]
            for capture, summary in captures.items()
        },
        "exact_blockers": {
            "CAPTURE1": {
                "factor_conflict": "elbow_right edgewise-to-unified change 49.9996 deg",
                "implausible_fitted_segments": [
                    "torso 0.0754 m", "upper_arm_right 0.1534 m",
                    "thigh_left 0.0720 m", "thigh_right 0.1547 m",
                ],
                "transition_ablation": "formal-only headings change by up to 136.19 deg",
                "natural_standing_sanity": (
                    "initial_still p95 maximum joint angle 178.40 deg; folded viewer"
                ),
            },
            "CAPTURE2": {
                "factor_conflicts": [
                    "elbow_left edgewise-to-unified change 89.1682 deg",
                    "knee_left edgewise-to-unified change 53.1963 deg",
                ],
                "implausible_fitted_segments": [
                    "torso 0.0526 m", "upper_arm_left 0.0299 m",
                    "upper_arm_right 0.0154 m", "thigh_left 0.1647 m",
                    "thigh_right 0.1257 m",
                ],
                "held_out_qmt_disagreement": [
                    "knee_left +58.7260 deg", "knee_right -26.2842 deg",
                ],
                "rom": "hip_right violates ROM in 100% of displayed frames",
            },
        },
        "recapture_requested": False,
        "next_scientific_step": (
            "Use the existing bounded slices to replace unconstrained B5 lever profiling "
            "with a separately validated physical connection/extrinsic model, then resolve "
            "the named QMT-versus-unified edge conflicts and repeat the unchanged gates. "
            "No missing B4 or generic new-capture request is justified by this run."
        ),
    }
    (RUN / "FINAL_DECISION.json").write_text(
        json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    c1 = captures["CAPTURE1"]
    c2 = captures["CAPTURE2"]
    report = f"""# BioSpur pure-IMU V0 final report — FAIL

The no-B4 raw-six-axis pipeline is implemented and auditable, but it does not
qualify on the present real captures. Synthetic observability, hostile bounded
access, the excitation-aware QMT baseline, one-gauge rank, broad independent
multistart, and held-out B5 residual gates pass. Real factor consistency,
fitted connection geometry, anatomical ROM, and direct FK do not.

## What passed

- The immutable metadata-first preselection is `{sha256(preselection)}` and was
  not mutated. B4 is absent and not required.
- Synthetic truth is independent of the estimator: rank 9 after one pelvis yaw
  gauge, maximum exact error {synthetic['exact_graph']['maximum_heading_error_deg']:.3g}
  deg, broad full-circle spread
  {synthetic['broad_multistart_evidence']['maximum_heading_spread_deg']:.3g} deg.
  The earlier 152.97 deg broad-start failure remains preserved and nonqualifying.
- Capture1/Capture2 each use one capture-wide nine-heading vector, 6 genuinely
  broad independent starts plus the edgewise control, full-circle profiling of
  every heading, rank 9, and multistart spreads
  {c1['rank_and_globalization']['maximum_heading_spread_deg']:.3g}/
  {c2['rank_and_globalization']['maximum_heading_spread_deg']:.3g} deg.
- All {c1['hostile_access']['selected_actions']} Capture1 and
  {c2['hostile_access']['selected_actions']} Capture2 windows have actual
  `os.read`/`os.lseek` accounting, explicit binary probes, action plus two
  superframe sequential brackets, no full traversal, and zero Hxx timing-byte
  intersection. No Hxx/Capture3/UWB spatial payload was opened.
- The corrected excitation-aware QMT gate passes all four hinges in each
  capture. Every all-action record remains diagnostic; unrelated unexcited
  actions are not contradictory calibration evidence.
- 73 focused V0 tests pass.

## Why the real result fails

| Capture | Exact failures |
|---|---|
| Capture1 | elbow-right baseline/refinement conflict 50.00 deg; fitted torso 0.075 m, right upper arm 0.153 m, thighs 0.072/0.155 m; ROM failure; natural-standing p95 maximum joint angle 178.40 deg; transition removal changes headings by 136.19 deg |
| Capture2 | elbow-left/knee-left baseline/refinement conflicts 89.17/53.20 deg; all five fitted torso/upper-arm/thigh separations fail broad ranges; right hip violates ROM in every displayed frame; held-out QMT knee differences +58.73/-26.28 deg |

Capture1's raw6 initial-still behavior supports the independent sanity check:
pre/post attitude changes are only {min(row['pre_post_rotation_distance_deg'] for row in c1['initial_still_raw6_drift'].values()):.2f}--
{max(row['pre_post_rotation_distance_deg'] for row in c1['initial_still_raw6_drift'].values()):.2f} deg with small gyro-bias norms. The folded viewer is therefore treated as
a calibration/extrinsic/FK failure, not reinterpreted as operator pose. No
neutral-pose correction, IK, manual quaternion, action-label pose factor, or
viewer rescue was applied.

## Decision

**FAIL.** This is a real-data contradiction, not an `INCONCLUSIVE` lack of
measurements. The existing rich slices are numerically full-rank; no B4 or
generic recapture request is justified. The next scientific step is to replace
unconstrained B5 lever profiling with a separately validated physical
connection/extrinsic model and resolve the named QMT-versus-unified edges before
rerunning the unchanged gates.

## Evidence

- `FINAL_DECISION.json`
- `VISUAL_QA.json`
- `CAPTURE1_CURRENT_FINAL_GATES.json`
- `CAPTURE2_CURRENT_FINAL_GATES.json`
- `CAPTURE1_DIRECT_RAW6_FK_VIEWER_V4.html`
- `CAPTURE2_DIRECT_RAW6_FK_VIEWER_V3.html`
- `CAPTURE1_PAYLOAD_ACCESS_AUDIT.json`
- `CAPTURE2_PAYLOAD_ACCESS_AUDIT.json`
- `METADATA_PRESELECTION.json`
"""
    (RUN / "REPORT.md").write_text(report, encoding="utf-8")

    for path in outputs:
        path.chmod(0o444)

    manifest_path = RUN / "MANIFEST.sha256"
    if manifest_path.exists():
        raise RuntimeError("refusing to overwrite sealed manifest")
    manifest_rows = []
    for path in sorted(RUN.rglob("*")):
        if path.is_file() and path != manifest_path:
            manifest_rows.append(f"{sha256(path)}  {path.relative_to(RUN)}")
    manifest_path.write_text("\n".join(manifest_rows) + "\n", encoding="utf-8")
    manifest_path.chmod(0o444)

    print(json.dumps({
        "verdict": "FAIL",
        "final_decision": artifact(RUN / "FINAL_DECISION.json"),
        "report": artifact(RUN / "REPORT.md"),
        "visual_qa": artifact(RUN / "VISUAL_QA.json"),
        "manifest": artifact(manifest_path),
    }, indent=2))


if __name__ == "__main__":
    main()
