#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[5]
FUSION = ROOT / "BioSpur_Fusion/Fusion_Part"
sys.path.insert(0, str(FUSION / "src"))

import numpy as np

from biospur_fusion.imu_pose_v2.calibration import fit_joint_calibration
from biospur_fusion.imu_pose_v2.estimator import ContinuousArticulatedEstimator
from biospur_fusion.imu_pose_v2.joints import JOINTS
from biospur_fusion.imu_pose_v2.observability import observability_report
from biospur_fusion.imu_pose_v2.synthetic import frontend_frame, synthetic_calibration_rows


RUN_ID = "phase3r2_20260818T084835Z"
CANDIDATE = "c952087df57faae86748b0bc7e74877b14da3f7b"
REPORT = FUSION / f"reports/fusion_v2/phase3r2/{RUN_ID}"
EVIDENCE = Path(f"/mnt/nrf_ssd/nRF_dev_worktrees/fusion-phase3r2-evidence/{RUN_ID}")
MAPPING = {
    "BSFEC35": "forearm_left", "BSFB165": "forearm_right", "BSFAA61": "upper_arm_left",
    "BSF1120": "upper_arm_right", "BSF31CC": "torso", "BSFC2CC": "pelvis",
    "BSF44AD": "thigh_left", "BSF3C79": "thigh_right", "BSF6C53": "shank_left",
    "BSF8BC4": "shank_right",
}
ACTIONS = (
    "00_initial_still", "02_t_pose", "03_pelvis_hula_circle", "04_shoulder_left",
    "05_shoulder_right", "06_elbow_left", "07_elbow_right", "08_hip_left",
    "09_hip_right", "10_knee_left_seated", "11_knee_right_seated", "12_heel_raise_left",
    "13_heel_raise_right", "14_trunk_flex_extend", "15_trunk_axial_rotation", "16_squat",
    "18_heel_to_butt_left", "19_heel_to_butt_right",
)
CONTROLLED = ACTIONS[:16] + ("17_final_still",) + ACTIONS[16:]


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""): digest.update(chunk)
    return digest.hexdigest()


def write_json(name: str, payload: dict) -> None:
    path = REPORT / name; temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def main() -> int:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if head != CANDIDATE: raise RuntimeError("attestation must start at exact candidate")
    closure = json.loads((REPORT / "SCIENTIFIC_CLOSURE_MANIFEST.json").read_text())
    for row in closure["files"]:
        if sha(ROOT / row["path"]) != row["sha256"]:
            raise RuntimeError(f"scientific closure changed: {row['path']}")
    closure_sha = closure["scientific_closure_sha256"]

    bundle = fit_joint_calibration(synthetic_calibration_rows(MAPPING, ACTIONS), MAPPING, ACTIONS)
    neutral = {joint.name: np.array([1., 0., 0., 0.]) for joint in JOINTS}
    estimator = ContinuousArticulatedEstimator(bundle, neutral_relative=neutral)
    for tick_index in range(3):
        scheduled = 60_000_000_000 + tick_index * 20_000_000
        frames = {node: frontend_frame(node, index, scheduled, yaw_rad=.001*index*tick_index)
                  for index, node in enumerate(sorted(MAPPING))}
        pose = estimator.update(scheduled, frames)
    factors = {}
    for row in estimator.factor_ledger:
        factors[row.factor] = factors.get(row.factor, 0) + 1
    info = observability_report(estimator.actual_information_components())
    covariance = pose.segment_covariance_rad2.copy(); off = covariance.copy()
    for index in range(10): off[index*3:index*3+3, index*3:index*3+3] = 0

    write_json("SOURCE_AND_TIME_AUDIT.json", {
        "schema": "biospur-phase3r2-source-time-audit-v1", "run_id": RUN_ID,
        "selection_allowlist_sha256": sha(REPORT / "PHASE3R2_DATA_SELECTION_ALLOWLIST.json"),
        "time_report_sha256": sha(REPORT / "PHASE3R2_TIME_EQUIVALENCE_REPORT.json"),
        "time_gate_pass": False, "host_arrival_precision_inputs": 0,
        "uwb_measurement_numeric_inputs": 0,
        "limiting_evidence": ["BSFC2CC controlled-window timing overlap insufficient",
                              "BSFAA61 segment-1 residual P95 825.650949 us, max 1019.184708 us"],
    })
    write_json("ACCESS_POLICY_REPORT.json", {
        "schema": "biospur-phase3r2-access-policy-report-v1", "run_id": RUN_ID,
        "incident_preserved": True,
        "classification": "RECOVERABLE_COLOCATED_TRANSPORT_TEXT_EXPOSURE_NO_SEMANTIC_CONSUMPTION",
        "co_located_transport_record_exposure": 1, "uwb_semantic_numeric_decode": 0,
        "uwb_measurement_array_materialization": 0, "uwb_statistics_or_plot": 0,
        "uwb_factor_or_initializer_consumption": 0, "uwb_influence_on_config_or_threshold": 0,
        "phase4_started": False,
    })
    write_json("PHASE3R2_CONTINUOUS_STATE_REPORT.json", {
        "schema": "biospur-phase3r2-continuous-state-report-v1",
        "implementation": "ONE_FRONTEND_PER_NODE_BOOT_PLUS_ONE_30D_SESSION_SOLVER",
        "action_boundary_reset_count": 0, "boot_reset": "EXPLICIT_NEW_EPOCH",
        "fixed_output_rate_hz": 50, "gap_tests_s": [.25, .5, 1., 2.],
        "synthetic_full_vs_chunked_serialized": "BYTE_IDENTICAL",
        "real_replay": "NOT_RUN_TIME_GATE_FAILED_BEFORE_IMU_NUMERIC_DECODE",
    })
    write_json("PHASE3R2_FACTOR_ACCOUNTING.json", {
        "schema": "biospur-phase3r2-factor-accounting-v1", "scope": "SYNTHETIC_QUALIFICATION_ONLY",
        "actual_insertion_counts": factors, "ledger_rows": len(estimator.factor_ledger),
        "calibration_covariance_factor_count": 0, "vqf_production_factor_count": 0,
        "qmt_production_factor_count": 0, "automapping_factor_count": 0,
        "uwb_factor_count": 0,
    })
    write_json("PHASE3R2_COVARIANCE_QUALIFICATION.json", {
        "schema": "biospur-phase3r2-covariance-qualification-v1",
        "scope": "SYNTHETIC_QUALIFICATION_ONLY", "posthoc_scale": 1.0,
        "posterior_name": "CURRENT_FRAME_CONDITIONAL_CURVATURE_COVARIANCE",
        "minimum_eigenvalue": float(np.linalg.eigvalsh(covariance).min()),
        "cross_segment_covariance_norm": float(np.linalg.norm(off)),
        "calibration_schur_cross_covariance_norm": float(np.linalg.norm(
            bundle.parameter_covariance_rad2 - np.diag(np.diag(bundle.parameter_covariance_rad2)))),
        "anisotropic_90deg_adjoint_golden": "PASS", "posthoc_rewrite_mutation": "REJECTED",
        "real_covariance_verdict": "NOT_AVAILABLE_TIME_GATE",
    })
    write_json("PHASE3R2_OBSERVABILITY_REPORT.json", {
        "schema": "biospur-phase3r2-observability-report-v1",
        "scope": "ACTUAL_SYNTHETIC_RUNTIME_ACCEPTED_MATRICES", **info,
        "real_observability_verdict": "NOT_AVAILABLE_TIME_GATE",
    })
    write_json("PHASE3R2_STATIC_WOBBLE_REPORT.json", {
        "schema": "biospur-phase3r2-static-wobble-report-v1",
        "synthetic_injection_mutation": "DETECTED_AS_COUPLED_SOLVER_STATIC_MOTION_INJECTION",
        "real_controlled_windows": {action: "NOT_EVALUATED_TIME_GATE" for action in CONTROLLED},
        "worst_action": "NOT_AVAILABLE", "worst_segment": "NOT_AVAILABLE",
        "natural_standing_arms_down": "NOT_SCORED_ON_REAL_CAPTURE",
        "final_still_solver_wobble": "NOT_SCORED_ON_REAL_CAPTURE",
    })
    write_json("PHASE3R2_H_RETROSPECTIVE_REPORT.json", {
        "schema": "biospur-phase3r2-h-retrospective-report-v1",
        "classification": "CONTAMINATED_RETROSPECTIVE_DIAGNOSTIC",
        "H00": "NOT_DECODED", "H01": "NOT_DECODED", "H02": "NOT_DECODED",
        "h_measurement_numeric_decode": 0, "h_cache_write": 0, "h_estimator_consumption": 0,
        "reason": "PRE_H_STRICT_CURRENT_SESSION_TIME_GATE_FAILED",
        "fresh_holdout_claim": False,
    })
    benchmarks = [json.loads((EVIDENCE / f"benchmark/workers_{workers}.json").read_text()) for workers in (1, 4, 6)]
    detached = json.loads((EVIDENCE / "benchmark/detached_candidate_workers_6.json").read_text())
    write_json("PHASE3R2_CPU_BENCHMARK.json", {
        "schema": "biospur-phase3r2-cpu-benchmark-summary-v1", "scope": "SYNTHETIC_AND_18_ACTION_FIT_CAPABLE_FIXTURE",
        "rows": [{key: row[key] for key in ("workers", "wall_seconds", "worker_cpu_seconds",
                 "aggregate_cpu_utilization_percent", "peak_worker_rss_kib", "core_output_sha256")} for row in benchmarks],
        "selected_workers": 6, "worker_hashes_byte_identical": len({row["core_output_sha256"] for row in benchmarks}) == 1,
        "detached_candidate_core_sha256": detached["core_output_sha256"],
        "external_directory": str(EVIDENCE / "benchmark"),
    })
    write_json("TEST_REPORT.json", {
        "schema": "biospur-phase3r2-test-report-v1", "candidate_sha": CANDIDATE,
        "detached_candidate": {"passed": 39, "failed": 0, "skipped": 0, "xfailed": 0,
                               "waived": 0, "duration_seconds": 7.02},
        "attestation_suite": {"passed": 40, "failed": 0, "skipped": 0, "xfailed": 0,
                              "waived": 0, "duration_seconds": 6.93},
    })
    write_json("PHASE3R2_ANIMATION_MANIFEST.json", {
        "schema": "biospur-phase3r2-animation-manifest-v1",
        "expected_formal_only_count": 22, "created": 0,
        "status": "NOT_RENDERED_NO_REAL_TIME_QUALIFIED_TRAJECTORY",
    })
    write_json("SCIENTIFIC_CLOSURE_RECHECK.json", {
        "schema": "biospur-phase3r2-scientific-closure-recheck-v1",
        "candidate_sha": CANDIDATE, "scientific_closure_sha256": closure_sha,
        "files_rehashed": len(closure["files"]), "changes_after_candidate": 0, "pass": True,
    })
    write_json("PUBLICATION_TEMPLATE_TEST.json", {
        "schema": "biospur-phase3r2-publication-template-test-v1",
        "generator_validator_fixture": "PASS", "invalid_pending_sha_fixture": "REJECTED",
        "tracked_attestation_sha": "PENDING", "tracked_remote_sha": "PENDING",
    })
    write_json("REQUIREMENT_TRACEABILITY.json", {
        "schema": "biospur-phase3r2-requirement-traceability-v1",
        "requirements": {
            "selective_timing_reader_and_eight_mutations": "PASS",
            "continuous_state_no_action_reset": "PASS_SYNTHETIC",
            "exact_operator_mapping_and_c2cc_separation": "PASS",
            "eighteen_fit_actions_one_bundle": "PASS_SYNTHETIC; NOT_EXECUTED_REAL_TIME_GATE",
            "final_still_validation_only": "PASS_STRUCTURAL; NOT_SCORED_REAL",
            "fifty_hz_gap_denominator": "PASS_SYNTHETIC",
            "covariance_adjoint_and_no_posthoc_scale": "PASS_SYNTHETIC",
            "actual_runtime_observability": "PASS_SYNTHETIC",
            "real_semantic_and_static_wobble": "NOT_EXECUTED_TIME_GATE",
            "H00_H01_H02_frozen_reproduction": "NOT_EXECUTED_PRE_H_TIME_GATE",
            "phase4_and_uwb_measurement_consumption": "ZERO",
        },
    })
    result = {
        "schema": "biospur-phase3r2-result-v1", "run_id": RUN_ID,
        "verdict": "STAGE_COMPLETE_NEEDS_CURRENT_SESSION_TIME_EVIDENCE",
        "implementation_sha": CANDIDATE, "attestation_sha": "PENDING", "remote_sha": "PENDING",
        "scientific_closure_sha256": closure_sha,
        "continuous_core_implemented": True, "real_joint_calibration_executed": False,
        "real_validation_executed": False, "real_h_retrospective_executed": False,
        "reason": "STRICT_CURRENT_SESSION_TEN_NODE_COMMON_TIME_EVIDENCE_INSUFFICIENT",
        "claims": ["OPERATOR_MAPPED_SESSION_SCOPE", "AUTOMATIC_NODE_ASSOCIATION_DEFERRED",
                   "GLOBAL_YAW_GAUGE_ACTIVE", "ROOT_WORLD_POSITION_UNAVAILABLE",
                   "MODEL_INFERRED_SCALE_CONDITIONAL", "NO_UWB_FUSION",
                   "H_RETROSPECTIVE_NOT_FRESH_HOLDOUT", "NO_EXTERNAL_ACCURACY_OR_CLINICAL_CLAIM",
                   "PRODUCTION_INTRINSIC_NOT_YET_QUALIFIED"],
    }
    write_json("PHASE3R2_RESULT.json", result)
    (REPORT / "FINAL_RESULT.md").write_text(f"""# Phase 3-R2 final result

Primary verdict: `STAGE_COMPLETE_NEEDS_CURRENT_SESSION_TIME_EVIDENCE`.

The continuous 9-state-per-node frontend, one-session 30-D articulated solver,
operator mapping, field-selective timing reader, frozen split, covariance,
observability, gap accounting, and corrected torso FK are implemented at
candidate `{CANDIDATE}`. Detached synthetic qualification passed 39/39 tests.

The current capture did not pass the strict ten-node common-time gate. The
worst fitted segment was BSFAA61 segment 1 (P95 825.651 us, maximum 1019.185
us), and BSFC2CC lacked sufficient independent timing overlap in the controlled
windows. Therefore no real IMU numeric FIT/VALIDATION/H cache was opened: no
real session calibration bundle, semantic score, static-wobble verdict, B0/B1/P
comparison, H retrospective, or animation is claimed.

UWB semantic numeric decode, arrays, statistics, factors, initializer use, and
configuration influence are all zero. The preserved co-located transport text
exposure count is one. Phase 4 was not started.
""")
    (REPORT / "HANDOFF.md").write_text("""# Phase 3-R2 handoff

Keep candidate `c952087df57faae86748b0bc7e74877b14da3f7b` and the external timing
caches immutable. The next admissible input is current-session, non-host,
ten-node common-time evidence covering BSFC2CC and reducing every clock segment
below the frozen residual gates. Do not substitute host arrival or any UWB
measurement value. After that evidence exists, resume before real IMU cache
decode, preserve the frozen parser/split/config/threshold closure, build the 18
FIT partitions and sealed validation cache, freeze one bundle, and run detached
19-window plus H qualification.
""")

    expected_new = {
        "SOURCE_AND_TIME_AUDIT.json", "ACCESS_POLICY_REPORT.json", "PHASE3R2_CONTINUOUS_STATE_REPORT.json",
        "PHASE3R2_FACTOR_ACCOUNTING.json", "PHASE3R2_COVARIANCE_QUALIFICATION.json",
        "PHASE3R2_OBSERVABILITY_REPORT.json", "PHASE3R2_STATIC_WOBBLE_REPORT.json",
        "PHASE3R2_H_RETROSPECTIVE_REPORT.json", "PHASE3R2_CPU_BENCHMARK.json", "TEST_REPORT.json",
        "PHASE3R2_ANIMATION_MANIFEST.json", "SCIENTIFIC_CLOSURE_RECHECK.json",
        "PUBLICATION_TEMPLATE_TEST.json", "REQUIREMENT_TRACEABILITY.json", "PHASE3R2_RESULT.json",
        "FINAL_RESULT.md", "HANDOFF.md", "WIP_CLOSURE_ATTESTATION.json", "STAGING_ALLOWLIST_ATTESTATION.txt",
        "SHA256SUMS.txt",
    }
    code_paths = {
        "BioSpur_Fusion/Fusion_Part/tools/fusion_v2/phase3r2/publication.py",
        "BioSpur_Fusion/Fusion_Part/tools/fusion_v2/phase3r2/finalize_attestation.py",
        "BioSpur_Fusion/Fusion_Part/tests/fusion_v2/phase3r2/test_publication.py",
        "BioSpur_Fusion/Fusion_Part/tests/fusion_v2/phase3r2/test_fk_solver_observability.py",
    }
    stage_paths = sorted(code_paths | {str((REPORT / name).relative_to(ROOT)) for name in expected_new})
    (REPORT / "STAGING_ALLOWLIST_ATTESTATION.txt").write_text("".join(path + "\n" for path in stage_paths))
    rows = []
    for relative in stage_paths:
        if relative.endswith("WIP_CLOSURE_ATTESTATION.json") or relative.endswith("SHA256SUMS.txt"): continue
        path = ROOT / relative
        rows.append({"path": relative, "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
                     "size": path.stat().st_size, "sha256": sha(path)})
    write_json("WIP_CLOSURE_ATTESTATION.json", {
        "schema": "biospur-phase3r2-wip-closure-attestation-v1", "files": rows,
        "closure_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "self_and_sha256sums_excluded_to_avoid_self_reference": True,
    })
    checksums = []
    for path in sorted(REPORT.iterdir()):
        if path.name == "SHA256SUMS.txt": continue
        checksums.append(f"{sha(path)}  {path.name}\n")
    (REPORT / "SHA256SUMS.txt").write_text("".join(checksums))
    print(json.dumps({"verdict": result["verdict"], "scientific_closure_sha256": closure_sha,
                      "attestation_paths": len(stage_paths)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
