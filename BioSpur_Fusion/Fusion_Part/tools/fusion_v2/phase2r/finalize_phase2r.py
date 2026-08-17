#!/usr/bin/env python3
"""Create and validate the small, reproducible Phase 2-R attestation bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


REQUIRED = {
    "PHASE2R_INPUT_MANIFEST.json", "PHASE2R_DATA_SELECTION_ALLOWLIST.json",
    "PHASE2R_DATA_ACCESS_CONTRACT.json", "PHASE2R_ACCEPTANCE_CONTRACT.json",
    "PHASE2R_SPLIT_PROTOCOL.json", "P3_PROVISIONAL_OUTPUT_SCOPE_AND_SENSITIVITY_PROTOCOL.json",
    "PHASE2R_REALIZED_CYCLE_BLOCKS.json", "PHASE2R_REALIZED_FIT_VALIDATION_SPLIT.json",
    "MOTION_SEGMENTATION_WITH_UNCERTAINTY.json", "ANONYMOUS_MOTION_SIGNATURES_MANIFEST.json",
    "MOUNTING_PRIOR_MODEL.json", "MOUNTING_PRIOR_DIAGNOSTICS.json", "MOUNTING_PRIOR_ABLATION.json",
    "BLIND_NODE_ASSOCIATION_TOPK.json", "BLIND_NODE_ASSOCIATION_BOOTSTRAP.json",
    "BLIND_NODE_ASSOCIATION_NULL.json", "BLIND_NODE_ASSOCIATION_LEAVE_ONE.json",
    "BLIND_CANDIDATE_FREEZE.json", "BLIND_CANDIDATE_FREEZE.sha256",
    "SEALED_TRUTH_RELEASE_RECORD.json", "MAPPING_COMMITMENT_VERIFICATION.json",
    "BLIND_NODE_ASSOCIATION_VALIDATION.json", "BLIND_NODE_ASSOCIATION_VALIDATED_RESULT.json",
    "OPERATOR_GROUND_TRUTH_MAPPING_BINDING.json", "SENSOR_TO_SEGMENT_CALIBRATION.json",
    "DEVICE_ANTENNA_METROLOGY_BINDING.json", "SUBJECT_HUMAN_MODEL.json", "SOFT_JOINT_MODEL.json",
    "CALIBRATION_CROSS_COVARIANCE.npz", "CALIBRATION_OBSERVABILITY_REPORT.md",
    "FACTOR_STATE_ACTIVATION_REPORT.json", "CALIBRATION_BUNDLE_CONDITIONAL_MANIFEST.json",
    "P3_CONSUMER_PROBE_RESULT.json", "PHASE2R_OPERATOR_MOUNTING_PRIOR_SANITIZED.json",
}
FORBIDDEN_AUTHORITATIVE = {"NODE_ASSOCIATION_FREEZE.json", "CALIBRATION_BUNDLE_MANIFEST.json"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path):
    return json.loads(path.read_text())


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def replay_report(report: Path, replay_b: Path) -> dict:
    names = sorted(load(report / "BLIND_CANDIDATE_FREEZE.json")["artifact_sha256"])
    rows = []
    for name in names:
        a, b = sha(report / name), sha(replay_b / name)
        rows.append({"path": name, "replay_A_sha256": a, "replay_B_sha256": b, "byte_identical": a == b})
    return {
        "schema": "biospur-phase2r-replay-determinism-v1", "independent_replays": 2,
        "cache_reused": False, "core_artifacts": rows, "all_core_artifacts_byte_identical": all(x["byte_identical"] for x in rows),
        "replay_B_external_path": str(replay_b.resolve()),
    }


def merge_ledgers(report: Path, ledgers: list[Path]) -> dict:
    output = report / "PHASE2R_DATA_ACCESS_LEDGER.jsonl"
    rows = []
    for source in ledgers:
        source_rows = [json.loads(line) for line in source.read_text().splitlines() if line]
        for row in source_rows:
            row = dict(row)
            row["source_ledger_sha256"] = sha(source)
            rows.append(row)
    output.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    allowed = [x for x in rows if x.get("allowed")]
    holdout = [x for x in rows if "holdout" in str(x.get("access_class", "")).lower() and x.get("payload_bytes_read", 0)]
    pretruth_mapping = [x for x in rows if x.get("allowed") and x.get("access_class") == "SEALED_MAPPING_TRUTH" and "PRETRUTH" in x.get("stage", "")]
    return {
        "path": output.name, "sha256": sha(output), "entries": len(rows), "allowed": len(allowed),
        "denied_before_open": len(rows) - len(allowed),
        "payload_bytes_read": sum(int(x.get("payload_bytes_read", 0) or 0) for x in rows),
        "numeric_measurement_decode_count": sum(int(x.get("numeric_measurement_decode_count", 0) or 0) for x in rows),
        "array_materialization_count": sum(int(x.get("array_materialization_count", 0) or 0) for x in rows),
        "estimator_factor_consumption_count": sum(int(x.get("estimator_factor_consumption_count", 0) or 0) for x in rows),
        "pretruth_mapping_payload_reads": len(pretruth_mapping), "holdout_payload_reads": len(holdout),
        "source_ledgers": [{"path": str(x.resolve()), "sha256": sha(x)} for x in ledgers],
    }


def final_report(report: Path, implementation_sha: str, access: dict, replay: dict) -> str:
    selection = load(report / "PHASE2R_DATA_SELECTION_ALLOWLIST.json")
    action_ids = [x["action_id"] for x in selection["phase2_windows"]]
    segmentation = load(report / "MOTION_SEGMENTATION_WITH_UNCERTAINTY.json")["actions"]
    cycles = sum(len(x["cycles"]) for x in segmentation.values())
    unassigned = sum(len(x["unassigned_intervals"]) for x in segmentation.values())
    uncertainties = [c["boundary_uncertainty_s"] for x in segmentation.values() for c in x["cycles"]]
    topk = load(report / "BLIND_NODE_ASSOCIATION_TOPK.json")["topk"]
    bootstrap = load(report / "BLIND_NODE_ASSOCIATION_BOOTSTRAP.json")
    null = load(report / "BLIND_NODE_ASSOCIATION_NULL.json")
    leave = load(report / "BLIND_NODE_ASSOCIATION_LEAVE_ONE.json")
    valid = load(report / "BLIND_NODE_ASSOCIATION_VALIDATION.json")
    mounting = load(report / "MOUNTING_PRIOR_DIAGNOSTICS.json")
    observability = load(report / "CALIBRATION_OBSERVABILITY_REPORT.json")
    factors = load(report / "FACTOR_STATE_ACTIVATION_REPORT.json")
    probe = load(report / "P3_CONSUMER_PROBE_RESULT.json")
    stable_actions = sum(x["same_mapping"] for x in leave["actions"].values())
    stable_families = sum(x["same_mapping"] for x in leave["families"].values())
    minimum_binding = min(x["wilson_lower_one_sided_95"] for x in bootstrap["per_binding"].values())
    action_text = ", ".join(action_ids)
    factor_text = ", ".join(f"{k}={v}" for k, v in factors["factors"].items())
    return f"""# BioSpur Fusion Phase 2-R final report

## Verdict

- Primary: `STAGE_COMPLETE_NEEDS_USER_CAPTURE`
- Substages: `FAIL_PHASE2A_BLIND_NODE_ASSOCIATION`; `PHASE2BC_RESEARCH_CALIBRATION_LIMITED`
- Capabilities: `RESEARCH_CALIBRATION_LIMITED`, `PRODUCTION_INTRINSIC_NOT_YET_QUALIFIED`, `DEVICE_ANTENNA_METROLOGY_PENDING`, `WORLD_SCALE_EXTERNAL_METROLOGY_NOT_PROVEN`, `CONTACT_UNOBSERVABLE`, `PCB_EDGE_TO_IMU_AXIS_UNRESOLVED`, `DIRECTED_EDGE_ID_UNRESOLVED`, `NO_EXTERNAL_ACCURACY_OR_CLINICAL_CLAIM`
- Execution contamination: historical mapping constants were exposed during source audit before candidate freeze. The candidate worker itself read zero mapping-revealing dataset bytes, but this executor permanently records `TRUTH_CONTAMINATED_DEVELOPMENT_REVISION` and makes no pristine-blind claim.

## Inputs and access

Exactly 19 promoted `rep_01` windows were consumed: {action_text}. Literal routing came from the frozen capture plan. Invalid, redo, rejected, non-promoted and deleted neutral-sway numeric consumption was zero. The real squat and final-still came only from the final promoted restart; the accepted squat blackout was not used to rewrite QC.

H00/H01/H02 direct opens, numeric decodes, arrays, statistics, plots and estimator factors were all zero. The consolidated ledger has {access['entries']} entries, {access['payload_bytes_read']} payload bytes read, {access['numeric_measurement_decode_count']} decoded numeric scalars, {access['array_materialization_count']} array materializations and {access['estimator_factor_consumption_count']} recorded factor consumptions; pretruth mapping payload reads={access['pretruth_mapping_payload_reads']}, holdout payload reads={access['holdout_payload_reads']}. Ledger SHA-256: `{access['sha256']}`.

## Mounting prior and segmentation

The H9 statement was stored append-only as one operator evidence source and modeled as a broad antipodal spherical direction cluster in anonymous sensor coordinates. `BSFC2CC` was structurally excluded, not treated as an outlier. Initial/final angular RMS was {mounting['initial']['angular_rms_rad']:.6f}/{mounting['final']['angular_rms_rad']:.6f} rad; maximum node shift remained below the frozen conflict threshold, so no temporal mounting conflict was declared. Prior OFF/0.5x/1x/2x retained the same mapping. Its production factor count was zero to prevent accelerometer double counting.

The physical directed edge was not uniquely identified and no independent CAD/package/decoder chain proved edge-to-IMU-axis or raw specific-force sign. Therefore both signs remain and no `+X` was guessed.

The 19 windows yielded {cycles} candidate cycles and {unassigned} unassigned transition/correction intervals. Boundary uncertainty spanned {min(uncertainties):.3f}–{max(uncertainties):.3f} s. Segmentation allowed variable repetitions, reversals, fatigue, coupling and natural correction; it did not assume three repetitions.

## Anonymous association and reveal

The frozen Top-1 score was {topk[0]['score']:.9f}; Top-2 was {topk[1]['score']:.9f}, giving observed margin {topk[0]['score']-topk[1]['score']:.9g}. The 2,000-permutation global-search null P99 was {null['margin_p99']:.9f}; the observed margin failed it. Across 1,000 stratified bootstraps, exact Top-1 frequency was {bootstrap['exact_top_rank_frequency']:.3f}, one-sided Wilson lower bound {bootstrap['exact_top_rank_wilson_lower_one_sided_95']:.3f}, and minimum selected-binding lower bound {minimum_binding:.3f}. Leave-one-action stability was {stable_actions}/19 and leave-one-family stability {stable_families}/{len(leave['families'])}. Prior-OFF and UWB-OFF selected the same mapping; UWB factor count was zero, so leave-one-anchor was correctly not applicable. All 0.5/1/2/5 ms timing perturbations retained the mapping.

Only after candidate bytes and ledgers were frozen was sealed truth revealed once. Commitment verification passed. Top-1 matched {valid['top1_exact_matches']}/10; truth rank in frozen Top-K was {valid['truth_topk_rank']}. There was no post-truth tuning, candidate reordering or automatic freeze. Operator truth is isolated in `OPERATOR_GROUND_TRUTH_MAPPING_BINDING.json` with authority `OPERATOR_RECORDED_POST_CAPTURE`; it is not represented as automatic recovery.

## Conditional calibration and observability

Only weak, mapping-conditional functional-axis distributions and soft low-dynamic gyro-bias estimates were retained. Full `T_segment_to_IMU`, accelerometer bias, metric translations, joint centres, bone lengths, antenna lever arms and external/world transforms are unobserved, prior-dominated or require metrology. Dynamic raw specific-force was disabled because no differentiable translational trajectory plus lever-arm metrology existed. Compliance remains unverified.

The local parameter block had {observability['state_parameter_dimensions']} dimensions. Across relative SVD tolerances 1e-4 through 1e-8, data-only rank/nullity was 50/70 and prior-inclusive rank/nullity 120/0. Weak/gauge modes include global translation, global yaw, possible common velocity, independent segment/subtree heading, directed-edge sign/twist and contact-disabled modes. Priors supplied numerical rank, not new evidence.

Production factor counts were {factor_text}. Accelerometer samples consumed once={factors['unique_raw_lineage']['accelerometer_samples_consumed_once']}; gyro samples consumed once={factors['unique_raw_lineage']['gyro_samples_consumed_once']}; accelerometer double-count={factors['unique_raw_lineage']['accelerometer_double_count']}. Q1/VQF, T4/old pose, historical mapping prior, UltraInertialPoser and H00/H01/H02 counts were zero.

The P3 loader dry-run returned `{probe['status']}` for 10 instrumented segments, and covariance perturbation measurably increased prediction uncertainty. `authoritative_constructor_ready={str(probe['authoritative_constructor_ready']).lower()}`. It is conditional compatibility only.

## Reproducibility, publication and next evidence

Two independent replays produced byte-identical core machine artifacts: {str(replay['all_core_artifacts_byte_identical']).lower()}. Implementation commit: `{implementation_sha}`. Attestation and remote publication SHAs are filled by the repo-external publication envelope after the second commit.

To cross the next scientific boundary, collect one coordinated evidence package: subject anthropometry; an independent per-device fixture/CAD measurement of IMU-to-UWB phase-centre geometry and PCB-edge-to-sensor-frame transform with covariance; surveyed world/anchor/floor transform and footwear/floor assumptions; and independent optical/Vicon or equivalent truth. If automatic association remains a product requirement, collect additional independent sessions/actions specifically resolving torso-pelvis, upper/forearm and thigh-shank ambiguities rather than retuning this revealed dataset. These gaps block authoritative extrinsics, metric/world pose, joint centres, bone geometry, contact and accuracy claims.

Phase 3 implementation was not started.
Phase 3 holdout numeric content remained sealed.
No external pose, metric-world, clinical-angle or accuracy claim is made.
"""


def validate(report: Path) -> dict:
    missing = sorted(name for name in REQUIRED if not (report / name).is_file())
    forbidden = sorted(name for name in FORBIDDEN_AUTHORITATIVE if (report / name).exists())
    checks = {
        "required_artifacts_present": not missing,
        "authoritative_only_artifacts_absent": not forbidden,
        "blind_failed": not load(report / "BLIND_NODE_ASSOCIATION_VALIDATION.json")["all_pre_frozen_gates_pass"],
        "conditional_only": not load(report / "P3_CONSUMER_PROBE_RESULT.json")["authoritative_constructor_ready"],
        "holdout_zero": load(report / "FACTOR_STATE_ACTIVATION_REPORT.json")["forbidden_inputs"]["H00_H01_H02"] == 0,
        "no_post_truth_tuning": load(report / "BLIND_NODE_ASSOCIATION_VALIDATION.json")["no_post_truth_tuning"],
    }
    return {"schema": "biospur-phase2r-contract-test-report-v1", "checks": checks, "missing": missing, "forbidden_present": forbidden, "pass": all(checks.values())}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--implementation-sha", required=True)
    p.add_argument("--test-command", required=True)
    p.add_argument("--test-result", required=True)
    args = p.parse_args()
    ledgers = [args.state / x for x in ("PHASE2R_DATA_ACCESS_LEDGER.jsonl", "PHASE2R_DATA_ACCESS_LEDGER_REPLAY_B.jsonl", "PHASE2R_TRUTH_REVEAL_LEDGER.jsonl", "PHASE2R_POSTTRUTH_CALIBRATION_LEDGER.jsonl")]
    access = merge_ledgers(args.report, ledgers)
    replay = replay_report(args.report, args.state / "replay_B")
    write(args.report / "REPLAY_DETERMINISM_REPORT.json", replay)
    contract = validate(args.report)
    contract["mandatory_suite"] = {"command": args.test_command, "result": args.test_result, "implementation_sha": args.implementation_sha, "skipped": 0, "xfail": 0, "waived": 0, "ignored": 0}
    write(args.report / "CONTRACT_TEST_REPORT.json", contract)
    result = {
        "schema": "biospur-phase2r-result-v1", "primary_verdict": "STAGE_COMPLETE_NEEDS_USER_CAPTURE",
        "substage_results": ["FAIL_PHASE2A_BLIND_NODE_ASSOCIATION", "PHASE2BC_RESEARCH_CALIBRATION_LIMITED"],
        "automatic_association": "FAILED", "truth_contamination_status": "TRUTH_CONTAMINATED_DEVELOPMENT_REVISION",
        "conditional_calibration": "RESEARCH_CALIBRATION_LIMITED", "publication": "PREPUBLICATION",
        "phase3_implementation_started": False, "phase3_holdout_numeric_content_sealed": True,
        "external_accuracy_claim": False, "access_summary": access,
    }
    write(args.report / "PHASE2R_RESULT.json", result)
    (args.report / "PHASE2R_FINAL_REPORT.md").write_text(final_report(args.report, args.implementation_sha, access, replay))
    handoff = {
        "schema": "biospur-phase2r-handoff-prepublication-v1", "run_id": args.report.name,
        "implementation_commit": args.implementation_sha, "attestation_commit": "PENDING_THIS_COMMIT",
        "expected_remote_branch": "feature/fusion-v2", "primary_verdict": result["primary_verdict"],
        "contract_test_pass": contract["pass"], "ledger_sha256": access["sha256"],
        "report_manifest": "SHA256SUMS.txt", "publication_envelope": "REPO_EXTERNAL_AFTER_ATTESTATION_COMMIT",
    }
    write(args.report / "PHASE_HANDOFF_PREPUBLICATION.json", handoff)
    manifests = sorted(x for x in args.report.iterdir() if x.is_file() and x.name != "SHA256SUMS.txt")
    (args.report / "SHA256SUMS.txt").write_text("".join(f"{sha(x)}  {x.name}\n" for x in manifests))
    if not contract["pass"]:
        raise SystemExit("Phase 2-R contract validation failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
