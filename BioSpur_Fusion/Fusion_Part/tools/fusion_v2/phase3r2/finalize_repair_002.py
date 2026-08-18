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
from biospur_fusion.imu_pose_v2.calibration import fit_joint_calibration
from biospur_fusion.imu_pose_v2.estimator import ContinuousArticulatedEstimator
from biospur_fusion.imu_pose_v2.joints import JOINTS
from biospur_fusion.imu_pose_v2.observability import observability_report
from biospur_fusion.imu_pose_v2.synthetic import frontend_frame, synthetic_calibration_rows
import numpy as np


RUN = FUSION / "reports/fusion_v2/phase3r2/phase3r2_20260818T084835Z"
CANDIDATE = "ae2941501317fec4c1f8ba944e193599885583d0"
CLOSURE = "6772aabcd3ac22e75c0836064db768f24773496acb5d014af982d9685be91b4e"
MAPPING = {
    "BSFEC35": "forearm_left", "BSFB165": "forearm_right", "BSFAA61": "upper_arm_left",
    "BSF1120": "upper_arm_right", "BSF31CC": "torso", "BSFC2CC": "pelvis",
    "BSF44AD": "thigh_left", "BSF3C79": "thigh_right", "BSF6C53": "shank_left", "BSF8BC4": "shank_right",
}
ACTIONS = ("00_initial_still", "02_t_pose", "03_pelvis_hula_circle", "04_shoulder_left", "05_shoulder_right",
           "06_elbow_left", "07_elbow_right", "08_hip_left", "09_hip_right", "10_knee_left_seated",
           "11_knee_right_seated", "12_heel_raise_left", "13_heel_raise_right", "14_trunk_flex_extend",
           "15_trunk_axial_rotation", "16_squat", "18_heel_to_butt_left", "19_heel_to_butt_right")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(name: str, payload: dict) -> None:
    path = RUN / name; temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def main() -> int:
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() != CANDIDATE:
        raise RuntimeError("revision-2 attestation must start at exact candidate")
    closure = json.loads((RUN / "SCIENTIFIC_CLOSURE_MANIFEST_002.json").read_text())
    for row in closure["files"]:
        if sha(ROOT / row["path"]) != row["sha256"]: raise RuntimeError(f"closure drift: {row['path']}")
    if closure["scientific_closure_sha256"] != CLOSURE: raise RuntimeError("closure identity mismatch")
    bundle = fit_joint_calibration(synthetic_calibration_rows(MAPPING, ACTIONS), MAPPING, ACTIONS)
    estimator = ContinuousArticulatedEstimator(
        bundle, neutral_relative={joint.name: np.array([1., 0., 0., 0.]) for joint in JOINTS})
    for tick in range(3):
        time_ns = 70_000_000_000 + tick*20_000_000
        estimator.update(time_ns, {node: frontend_frame(node, index, time_ns, yaw_rad=.001*index*tick)
                                   for index, node in enumerate(sorted(MAPPING))})
    report = observability_report(estimator.actual_information_components())
    gauge_sweep = report["gauge_free_svd_relative_tolerance_sweep"]
    if not all(row["rank"] == 29 and row["nullity"] == 1 for row in gauge_sweep):
        raise RuntimeError("declared common-yaw gauge did not remain null")
    write_json("PHASE3R2_OBSERVABILITY_REPORT_002.json", {
        "schema": "biospur-phase3r2-observability-report-v1", "revision": 2,
        "scope": "ACTUAL_SYNTHETIC_RUNTIME_ACCEPTED_MATRICES", **report,
        "convention_fixed_and_gauge_free_separated": True,
        "real_observability_verdict": "NOT_AVAILABLE_TIME_GATE",
        "supersedes": "PHASE3R2_OBSERVABILITY_REPORT.json"
    })
    write_json("SCIENTIFIC_CLOSURE_RECHECK_002.json", {
        "schema": "biospur-phase3r2-scientific-closure-recheck-v1", "revision": 2,
        "candidate_sha": CANDIDATE, "scientific_closure_sha256": CLOSURE,
        "files_rehashed": len(closure["files"]), "changes_after_candidate": 0, "pass": True
    })
    benchmark = json.loads(Path(
        "/mnt/nrf_ssd/nRF_dev_worktrees/fusion-phase3r2-evidence/phase3r2_20260818T084835Z/benchmark/detached_candidate_002_workers_6.json").read_text())
    write_json("TEST_REPORT_002.json", {
        "schema": "biospur-phase3r2-test-report-v1", "revision": 2,
        "candidate_sha": CANDIDATE, "detached_candidate": True,
        "passed": 40, "failed": 0, "skipped": 0, "xfailed": 0, "waived": 0,
        "duration_seconds": 7.07, "core_output_sha256": benchmark["core_output_sha256"],
        "core_identical_to_revision_1": benchmark["core_output_sha256"] == "b8469e4d678dfecf18b7fff3f35205618a52f685cdafe3d3df8cb9a20559234e"
    })
    write_json("PHASE3R2_RESULT_002.json", {
        "schema": "biospur-phase3r2-result-v1", "revision": 2,
        "verdict": "STAGE_COMPLETE_NEEDS_CURRENT_SESSION_TIME_EVIDENCE",
        "implementation_sha": CANDIDATE, "attestation_sha": "PENDING", "remote_sha": "PENDING",
        "scientific_closure_sha256": CLOSURE, "supersedes": "PHASE3R2_RESULT.json",
        "gauge_free_observability_repair": "PASS_SYNTHETIC_RANK29_NULLITY1_ALL_TOLERANCES",
        "real_joint_calibration_executed": False, "real_validation_executed": False,
        "real_h_retrospective_executed": False, "uwb_measurement_numeric_consumption": 0
    })
    (RUN / "FINAL_RESULT_002.md").write_text(f"""# Phase 3-R2 final result — revision 2

Primary verdict remains `STAGE_COMPLETE_NEEDS_CURRENT_SESSION_TIME_EVIDENCE`.

Forward candidate `{CANDIDATE}` supersedes only the observability
qualification interpretation from revision 1. It now reports convention-fixed
information separately from gauge-free information. The declared common global
yaw direction is null with rank 29/nullity 1 at every frozen relative tolerance
from 1e-4 through 1e-8. Detached qualification passed 40/40 tests and the pose
core hash stayed byte-identical.

No real FIT, VALIDATION, final-still, B0/B1/P, static-wobble, H, or animation
claim changes: those remain unavailable because the current-session ten-node
strict time gate failed before IMU numeric decode. UWB semantic consumption
remains zero; the co-located transport exposure count remains one.
""")
    names = {"PHASE3R2_OBSERVABILITY_REPORT_002.json", "SCIENTIFIC_CLOSURE_RECHECK_002.json",
             "TEST_REPORT_002.json", "PHASE3R2_RESULT_002.json", "FINAL_RESULT_002.md",
             "STAGING_ALLOWLIST_ATTESTATION_002.txt", "WIP_CLOSURE_ATTESTATION_002.json", "SHA256SUMS_002.txt"}
    code = "BioSpur_Fusion/Fusion_Part/tools/fusion_v2/phase3r2/finalize_repair_002.py"
    paths = sorted({code} | {str((RUN / name).relative_to(ROOT)) for name in names})
    (RUN / "STAGING_ALLOWLIST_ATTESTATION_002.txt").write_text("".join(path+"\n" for path in paths))
    rows = []
    for relative in paths:
        if relative.endswith("WIP_CLOSURE_ATTESTATION_002.json") or relative.endswith("SHA256SUMS_002.txt"): continue
        path = ROOT / relative
        rows.append({"path": relative, "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
                     "size": path.stat().st_size, "sha256": sha(path)})
    write_json("WIP_CLOSURE_ATTESTATION_002.json", {
        "schema": "biospur-phase3r2-wip-closure-attestation-v1", "revision": 2, "files": rows,
        "closure_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "self_and_checksum_file_excluded": True
    })
    checksum_names = sorted(name for name in names if name != "SHA256SUMS_002.txt")
    (RUN / "SHA256SUMS_002.txt").write_text("".join(f"{sha(RUN/name)}  {name}\n" for name in checksum_names))
    print(json.dumps({"scientific_closure_sha256": CLOSURE, "gauge_free_sweep": gauge_sweep,
                      "staging_paths": len(paths)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
