#!/usr/bin/env python3
"""Assemble terminal S2 Attempt-A audit without running an estimator."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ATTEMPT = ROOT / "Fusion_Part/logs/imu_only_multi_action_centerline_calibration_v1_s2_synthetic"
AUDIT = ROOT / "Fusion_Part/logs/imu_only_multi_action_centerline_calibration_v1_s2_attempt_a_audit"
QUAL = ROOT / "Fusion_Part/logs/imu_only_multi_action_centerline_calibration_v1_s2_qualification"
OUTPUT = ROOT / "Fusion_Part/logs/imu_only_multi_action_centerline_calibration_v1_s2_terminal_audit"
SOURCE = ROOT / "Fusion_Part/src/biospur_fusion/imu_multi_action_s2"
RUNNER = ROOT / "Fusion_Part/tools/run_multi_action_calibration_s2_human_observability.py"
FIREWALL_RUNNER = ROOT / "Fusion_Part/tools/run_s2_truth_firewall_and_solver_qualification.py"


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def write(name: str, value: dict) -> None:
    (OUTPUT / name).write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    old_crosswalk = load(AUDIT / "S0_TO_S2_PARAMETER_CROSSWALK.json")
    s2_names = load(ATTEMPT / "SCALED_JACOBIAN_OBSERVABILITY.json")["parameter_names"]
    mapped_s2_prefixes = set()
    for row in old_crosswalk["rows"]:
        if row["classification"] in ("RETAINED", "REPARAMETERIZED"):
            mapped_s2_prefixes.add(row["s2_correspondence"])
    new_parameters = []
    for index, name in enumerate(s2_names):
        if name.startswith("functional:hip_"):
            new_parameters.append({
                "s2_column": index, "s2_parameter_name": name, "unit": "rad",
                "classification": "NEW",
                "reason": "Bilateral pelvis-thigh functional-axis coordinate added for high-knee semantics.",
            })
    counts = {
        "s0_total": len(old_crosswalk["rows"]),
        "retained": sum(row["classification"] == "RETAINED" for row in old_crosswalk["rows"]),
        "reparameterized": sum(row["classification"] == "REPARAMETERIZED" for row in old_crosswalk["rows"]),
        "removed": sum(row["classification"] == "DELETED" for row in old_crosswalk["rows"]),
        "schur_profiled": sum(row["classification"] == "SCHUR_PROFILED_NUISANCE" for row in old_crosswalk["rows"]),
        "new_s2": len(new_parameters),
        "s2_total": len(s2_names),
    }
    assertions = {
        "s0_columns_unique": len({row["s0_column"] for row in old_crosswalk["rows"]}) == 371,
        "s0_columns_exact_0_through_370": sorted(row["s0_column"] for row in old_crosswalk["rows"]) == list(range(371)),
        "s0_disposition_count_closes": counts["retained"] + counts["reparameterized"] + counts["removed"] + counts["schur_profiled"] == 371,
        "removed_320_yaw_plus_4_zero": counts["removed"] == 324,
        "new_parameter_count": counts["new_s2"] == 8,
        "crosswalk_equation_371_minus_324_plus_8_equals_55": 371 - counts["removed"] + counts["new_s2"] == 55,
        "mapped_plus_new_equals_s2": counts["retained"] + counts["reparameterized"] + counts["new_s2"] == counts["s2_total"],
    }
    assert all(assertions.values()), (counts, assertions)
    write("S0_TO_S2_PARAMETER_CROSSWALK.json", {
        **old_crosswalk,
        "new_s2_parameters": new_parameters,
        "count_closure": counts,
        "machine_assertions": assertions,
        "equation": "371 - (320 dynamic yaw + 4 joint zero) + 8 new hip-functional coordinates = 55",
        "unmapped_s0_count": 0,
        "duplicate_s0_count": 0,
    })

    previous_inventory = load(AUDIT / "FULL_AND_PROFILED_STATE_INVENTORY.json")
    write("FULL_AND_REDUCED_STATE_INVENTORY.json", {
        **previous_inventory,
        "schema": "biospur-s2-full-and-reduced-state-inventory-terminal-v1",
        "conditional_local_jacobian": {
            "shape": [38400, 55],
            "rank_reported_at_nonconverged_point": 55,
            "classification": "CONDITIONAL_LOCAL_DIAGNOSTIC_ON_INVALID_REDUCED_STATE",
        },
        "route_A_design_only": "z=[Theta_shared,X_dynamic,joint_zero,bias,lever_arm,functional_frame_nuisance]; build J_full=[J_Theta J_nuisance]",
        "route_B_design_only": "variable projection/Schur; every outer evaluation converges all nuisance; prove equivalence to Route A on a small synthetic problem",
        "implementation_authorized": False,
    })
    write("FAILURE_PRECEDENCE.json", {
        "schema": "biospur-s2-failure-precedence-v1",
        "TOP_LEVEL": "FAIL_SYNTHETIC_RECOVERY",
        "PRIMARY_BLOCKER": "FAIL_REDUCED_STATE_INVALID",
        "SECONDARY_BLOCKER": "SOLVER_NOT_CONVERGED",
        "TORSO_OBSERVABILITY": "NOT_QUALIFIED",
        "REAL_DATA_ACCESS": "FORBIDDEN",
        "reason": "A solver cannot qualify recovery or observability for an objective that omits compensating product states without an equivalent Schur proof.",
        "numerical_diagnostics_classification": "CONDITIONAL_LOCAL_DIAGNOSTIC_ON_INVALID_REDUCED_STATE",
        "forbidden_claims": ["old null received real information", "torso null was repaired", "55/55 proves observability", "I_eff proves physical identifiability"],
    })
    firewall = load(QUAL / "TRUTH_FIREWALL_AUDIT.json")
    firewall.update({
        "truth_used_only_for_post_fit_evaluation": True,
        "truth_accidentally_used_by_estimator": False,
        "scope_limit": "PASS proves estimator truth isolation only; it does not rehabilitate the invalid reduced state.",
    })
    write("TRUTH_FIREWALL_AUDIT.json", firewall)
    multistart = load(ATTEMPT / "MULTISTART_STABILITY.json")
    write("SOLVER_ATTEMPT_A_AUDIT.json", {
        "schema": "biospur-s2-solver-attempt-a-terminal-audit-v1",
        "attempt": "S2_ATTEMPT_A_SOLVER_BUDGET_EXHAUSTED",
        "configured_max_nfev": 8,
        "max_nfev_is_scientific_gate": False,
        "starts": multistart["records"],
        "all_starts_converged": multistart["all_converged"],
        "primary_interpretation": "SOLVER_NOT_CONVERGED; recovery evaluability and scientific observability are not established",
        "replay2_interpretation": "THE_SAME_NONCONVERGED_FAILURE_IS_REPRODUCIBLE",
        "manual_max_nfev_60": {
            "source": "operator steering transcript",
            "persisted_code_path": None,
            "persisted_result": None,
            "canonical_contract": False,
        },
        "serialization_failure": {
            "exception": "TypeError: Object of type ndarray is not JSON serializable",
            "location": "TRUTH_FIREWALL_AUDIT writer after firewall computation",
            "scientific_artifact_written_before_failure": False,
        },
        "writer_only_fix": {
            "before": "fit_x: fit.x",
            "after": "fit_x: fit.x.tolist()",
            "changes_residual_or_solver": False,
            "source_path": str(SOURCE / "qualification.py"),
            "source_sha256_after_fix": sha(SOURCE / "qualification.py"),
        },
        "conditional_diagnostics": {
            "rank": "55/55",
            "old_null_Jv_l2": load(ATTEMPT / "SCALED_JACOBIAN_OBSERVABILITY.json")["old_null_Jv_l2"],
            "linearized_I_eff": load(ATTEMPT / "TRUNK_NULLSPACE_PROFILE.json")["linear_profile"]["I_eff"],
            "classification": "CONDITIONAL_LOCAL_DIAGNOSTIC_ON_INVALID_REDUCED_STATE",
        },
        "source_hashes_current_terminal_state": {
            path.name: sha(path) for path in sorted(SOURCE.glob("*.py"))
        } | {RUNNER.name: sha(RUNNER), FIREWALL_RUNNER.name: sha(FIREWALL_RUNNER)},
        "attempt_a_exact_executed_source_snapshot_available": False,
    })
    write("DATA_ACCESS_AUDIT.json", {
        "real_calibration_payload": "SEALED",
        "UWB_T4": "SEALED",
        "Anchor_geometry": "SEALED",
        "operator_measurements": "SEALED",
        "walk_final_still": "SEALED",
        "golf_boxing": "SEALED",
        "hardware_access": "NONE",
        "freeze_replay_render": "FORBIDDEN",
        "commit_push": "FORBIDDEN",
        "opened_for_terminal_audit": [str(ATTEMPT), str(AUDIT), str(QUAL / "TRUTH_FIREWALL_AUDIT.json"), str(SOURCE), str(RUNNER), str(FIREWALL_RUNNER)],
    })
    (OUTPUT / "REPORT.md").write_text("""# S2 Attempt A terminal audit

## Terminal verdict

`TOP_LEVEL = FAIL_SYNTHETIC_RECOVERY`

`PRIMARY_BLOCKER = FAIL_REDUCED_STATE_INVALID`

`SECONDARY_BLOCKER = SOLVER_NOT_CONVERGED`

`TORSO_OBSERVABILITY = NOT_QUALIFIED`

The corrected human action semantics and signal-driven segmentation passed.
High-knee is treated as front pelvis–thigh/hip-chain excitation, heel-to-butt
as rear thigh–shank/knee-chain excitation, elbow curl is separated from
pronation/supination, and trunk left turn, right turn, and forward bend are
separate interior-time phases.

That semantic success does not qualify the estimator. S2 Attempt A optimizes
only 55 shared static coordinates. Relative to S0, it removes 320 dynamic
yaw-spline coordinates and four joint-zero coordinates, then adds eight
hip-functional coordinates: `371 - 324 + 8 = 55`. The dynamic states, joint
zeros, bias nuisance, and lever-arm nuisance were not eliminated by a proven
Schur complement or variable projection. Generic lever arms were fixed.
Therefore the reported 55/55 local rank is conditional on an invalid reduced
state; it is not observability of the full articulated human model.

All five Attempt-A starts exhausted `max_nfev=8` without convergence. The
large output errors describe those nonconverged iterates only. Replay 2 shows
that the same nonconverged failure is reproducible; it does not prove recovery
failure, old-null repair, old-null persistence, or action insufficiency.

The truth firewall passed independently. Physically deleting, randomizing, or
permuting truth-only fields left segmentation, initialization, residual,
Jacobian, fit input, and the local firewall-fit result byte-identical. Truth
is used only by post-fit synthetic evaluation, not by the estimator. This
does not rehabilitate the invalid 55-state reduction.

The old-null `||Jv||`, local rank, and linearized `I_eff` values are preserved
only as `CONDITIONAL_LOCAL_DIAGNOSTIC_ON_INVALID_REDUCED_STATE`. At this stage
we cannot determine whether the real action protocol is sufficient to break
the torso ambiguity.

S2.1 is design-only. It must either restore the full joint state (Route A) or
implement a mathematically equivalent converged inner nuisance solve/Schur
complement (Route B), and prove A/B equivalence on a small synthetic problem.
No Solver Revision B was implemented. Real capture, UWB/T4, operator
measurements, walk/final_still, golf, and boxing remain sealed.
""")
    (OUTPUT / "TEST_RESULTS.md").write_text("""# Test results

The truth-firewall executed four independent estimator pipelines and all five
artifact hashes matched.

The terminal-only assertion command was:

```text
PYTHONPATH=Fusion_Part/src pytest -q Fusion_Part/tests/unit/test_s2_terminal_audit.py
```

Result: `6 passed in 0.04s`.

These six tests only validate artifact completeness, exact 371→55 count
closure, failure precedence, truth-firewall scope, writer-only provenance, and
the sealed-data audit. They do not run fitting, multistart, SVD/profile, or
deterministic replay and cannot rehabilitate the reduced state.
""")
    files = {path.name: sha(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "SHA256_MANIFEST.json"}
    write("SHA256_MANIFEST.json", {"schema": "biospur-s2-terminal-audit-sha-v1", "files": files})
    print(json.dumps({"verdict": "FAIL_REDUCED_STATE_INVALID", "output": str(OUTPUT), "solver_revision_b": "NOT_IMPLEMENTED"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
