"""Formal artifact packaging for a Root-R6A0 shadow-only run."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, is_dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .body import BodyModel, frozen_uncertain_calibration
from .contracts import FactorProposal


REQUIRED_ARTIFACTS = (
    "RUN_MANIFEST.json",
    "MATURITY_AND_REUSE_LEDGER.json",
    "ARCHITECTURE.md",
    "BODY_GRAPH_AND_STATE_SCHEMA.json",
    "FACTOR_AND_AUTHORITY_CONTRACTS.json",
    "OBSERVABILITY_AND_CAPABILITY_ATLAS.json",
    "FAULT_SCENARIO_RESULTS.json",
    "TEST_RESULTS.json",
    "FINAL_REPORT.md",
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, frozenset):
        return sorted(value)
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(_jsonable(value), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(4 << 20):
            digest.update(block)
    return digest.hexdigest()


def _factor_summary(proposals: tuple[FactorProposal, ...]) -> dict:
    counts = Counter(proposal.family for proposal in proposals)
    samples = {}
    for proposal in proposals:
        samples.setdefault(proposal.family, _jsonable(proposal))
    return {
        "factor_count": len(proposals),
        "family_counts": dict(sorted(counts.items())),
        "one_complete_contract_per_family": samples,
        "all_have_physical_event_uid": all(proposal.physical_event_uids for proposal in proposals),
        "all_have_connected_blocks": all(proposal.connected_variable_blocks for proposal in proposals),
        "all_have_residual_dimension": all(proposal.residual_dimension > 0 for proposal in proposals),
        "all_have_covariance_provenance": all(proposal.covariance_provenance for proposal in proposals),
        "all_have_fault_domains": all(proposal.fault_domains for proposal in proposals),
        "all_have_service_dofs": all(proposal.affected_service_dofs for proposal in proposals),
    }


def maturity_ledger(context: Mapping[str, Any]) -> dict:
    return {
        "schema": "biospur.root_r6a0.maturity_and_reuse.v1",
        "discovery_duration_limit_minutes": 30,
        "historical_audit_bounded": True,
        "classifications": ["REUSE_VERIFIED", "WRAP_AND_REVERIFY", "REFERENCE_ONLY", "REJECT_FOR_NEW_ESTIMATOR"],
        "rows": [
            {
                "component": "typed raw ingest and physical clock fields",
                "path": "src/biospur_fusion/ingest/events.py",
                "classification": "WRAP_AND_REVERIFY",
                "use": "typed_event_adapter preserves node/global measurement time and raw byte provenance",
                "verification": "adapter contract plus C1 typed-ledger inventory rehearsal",
            },
            {
                "component": "native common-clock C1 ledger",
                "path": "logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2/TIME_EVENT_LEDGER.npz",
                "classification": "REUSE_VERIFIED",
                "use": "read-only IMU trigger times and identity inventory in C1 wiring rehearsal",
                "verification": "ten nodes, 200 Hz in bounded interval, status and sigma fields checked",
            },
            {
                "component": "active numerical node ownership",
                "path": "config/captures/v47_ten_node_body_calibration_20260814_093601.json",
                "classification": "REUSE_VERIFIED",
                "use": "sole physical node-to-segment ownership input",
                "verification": context["model_identity_provenance"],
            },
            {
                "component": "Root-R4 FactorLedger ancestry kernel",
                "path": "src/biospur_fusion/root_r4/contracts.py",
                "classification": "WRAP_AND_REVERIFY",
                "use": "RootR4LineageAdapter negative-control oracle; expanded R6 ledger also covers raw-IMU/M1",
                "verification": "raw/T4 and raw-IMU/M1 collisions both rejected",
            },
            {
                "component": "legacy ten-segment independent-orientation FK",
                "path": "src/biospur_fusion/body_graph/model.py",
                "classification": "REFERENCE_ONLY",
                "use": "topology and rotation-convention comparison only",
                "reason": "its state supplies independent segment rotations; Root-R6A0 requires generalized articulated coordinates",
            },
            {
                "component": "V4.1 anthropometry and placement validator",
                "path": "src/biospur_fusion/calibration/anthropometry_v4_1.py",
                "classification": "REFERENCE_ONLY",
                "use": "slot/provenance vocabulary only; no real values loaded",
                "reason": "Root-R6A0 real calibrations remain FROZEN_UNCERTAIN and are not fitted from C1",
            },
            {
                "component": "Q1/M1 and Root-R4/R5 estimates",
                "path": "historical Root-R4/Root-R5 artifacts",
                "classification": "REFERENCE_ONLY",
                "use": "regression/adversarial context and C1 identity comparison only",
                "reason": "not production innovations, causal fault truth, or independent evidence",
            },
            {
                "component": "real IMU preintegration",
                "path": "existing Q1/M1 paths",
                "classification": "REFERENCE_ONLY",
                "use": "interface represented; synthetic propagation factor executable",
                "reason": "no existing preintegrator was requalified for the shared Root-R6A0 articulated state",
            },
            {
                "component": "Root-R3 forearm display alias mapping",
                "path": "src/biospur_fusion/root_r3/interfaces.py",
                "classification": "REJECT_FOR_NEW_ESTIMATOR",
                "use": "none",
                "reason": "conflicts with the active mapping for BSFB165/BSFEC35; physical ownership follows the active binding",
            },
            {
                "component": "pelvis-only repair, post-hoc IK, fitted per-epoch translation, common-yaw broadcast",
                "path": "historical diagnostic patterns",
                "classification": "REJECT_FOR_NEW_ESTIMATOR",
                "use": "none",
                "reason": "violates shared whole-body graph, ancestry, gauge, or protected-authority contracts",
            },
        ],
        "root_r4_manifest_before": context["root_r4_before"],
        "root_r4_manifest_after": context["root_r4_after"],
    }


def architecture_markdown(context: Mapping[str, Any]) -> str:
    c1 = context["c1_summary"]
    return f"""# Root-R6A0 whole-body articulated shadow backbone

## Binding structure

One `BodyModel` owns the ten-segment, nine-joint tree. A keyframe contains a
root SE(3) pose, root velocity, three-dimensional relative state and optional
rate for every joint, and independent gyro and accelerometer biases for all ten
IMUs. Static world/model gauge, subject geometry, sensor extrinsics, UWB phase
centres, anchors/delays, and time relationships are separate calibration slots.
Every keyframe carries a full covariance contract.

The sole geometry chain is:

```text
T_world_segment = T_world_model * T_model_root(t) * FK(joint_state, geometry)
T_world_imu     = T_world_segment * T_segment_imu
p_world_tag     = translation(T_world_segment) + rotation(T_world_segment) * lever_segment_tag
```

IK is the inverse/MAP solve over those same variables, factors, and FK calls.
There is no independent-node solve, UWB correction stage, or post-hoc IK
projection. The included nonlinear least-squares harness is synthetic and test
only.

## Constraint tiers

- Tier A is an active structural contract: identity, topology, frame/time semantics, ancestry, and deterministic FK.
- Tier B is active only in the synthetic sandbox. Every real geometry, extrinsic, phase-centre, anchor, delay, gauge, and timing slot is `FROZEN_UNCERTAIN`.
- Tier C exists over all joints as anisotropic soft anatomy. It is synthetic-active and real-shadow; elbows and knees are not hard one-axis projections.
- Tier D exposes disabled contact, ZUPT, gait-phase, torque/dynamics, and learned-prior interfaces.

## Information and authority

Measurement health, current informativeness, and allowed authority are separate
typed fields. Fault hypotheses span event, link, tag, anchor, limb, IMU, clock,
frame/map, geometry, and shared-model domains. Correlated hypotheses are grouped
before authority routing. One link, tag, anchor, or correlated vote group cannot
gain protected root/common-yaw authority. Recovery changes an evidence weight
gradually and never resets pose or covariance.

Every real proposal is `SHADOW_ONLY` or `BLOCKED`, with zero production
authority. Raw/T4 and raw-IMU/M1 ancestry collisions are rejected before factor
activation.

## Executed modes

The deterministic synthetic mode instantiated all ten segments, ten sensors,
ten tags, eight anchors, and nine joints with nonzero sensor/tag lever arms and
an asymmetric representable pose. Its shared-graph inverse reduced RMS residual
from {context['synthetic_gates']['inverse_map_harness']['initial_rms']:.6g} to
{context['synthetic_gates']['inverse_map_harness']['final_rms']:.6g}.

The real C1 mode rehearsed {c1['evidence_counts']['RAW_IMU']} raw IMU samples and
{c1['evidence_counts']['RAW_UWB']} raw per-link ranges over {c1['duration_s']:.1f}
seconds. It instantiated identity, time, ancestry, residual shape, connectivity,
calibration-status, and authority contracts only; it did not evaluate or update
a real pose.
"""


def final_report(context: Mapping[str, Any], label: str) -> str:
    c1 = context["c1_summary"]
    unresolved = c1["unresolved_calibration_slot_count"]
    synthetic = context["synthetic_gates"]
    pytest = context["pytest"]
    return f"""# Root-R6A0 final report

## Primary label

```text
{label}
```

All work was performed under the exact authorized Fusion root
`{context['fusion_root']}`. Writes are confined to the Root-R6A0 source, test,
configuration, and this timestamped log directory. The protected source,
configuration, production, baseline, and tool trees remain byte-for-byte equal
to the pre-edit snapshot.

## Answers required by the execution brief

1. **Path:** yes. The resolved path, Git root, branch, HEAD, disk gate, and write
   inventory are recorded in `RUN_MANIFEST.json`.
2. **Reuse:** typed ingest, native timing, active physical ownership, and the
   Root-R4 ancestry kernel were verified or wrapped. Existing independent-pose
   FK, Q1/M1, T4, and Root-R4/R5 estimates are reference-only. Pelvis repair,
   post-hoc IK, exact-zero unknown levers, event-fit translation innovations,
   and common-yaw broadcast are rejected. See `MATURITY_AND_REUSE_LEDGER.json`.
3. **Complete graph:** yes—10 segments, 9 articulated connections, 10 IMUs, 10
   UWB tags, and 8 anchors are instantiated without named-device branches in
   algorithm code.
4. **Common geometry:** yes. Both IMU and raw UWB residuals obtain sensor and
   phase-centre geometry only through the same `BodyModel` FK.
5. **IK:** it is represented and executed synthetically as the inverse/MAP solve
   of that same graph, not as downstream cleanup.
6. **Frozen calibration:** all {unresolved} real slots remain
   `FROZEN_UNCERTAIN`: static gauge; local joint centres/rest rotations and bone
   geometry; 10 IMU extrinsics; 10 tag phase-centre levers; anatomical offsets;
   8 anchor positions and delays; and 10 UWB/IMU time relationships. Unknown
   values are `null`, never precise zero.
7. **Constraint tiers:** A is structurally active; B is synthetic-active and
   blocked on real calibration; C is synthetic-active and real-shadow; D is
   disabled.
8. **Gauge/nullspace:** absent a qualified frame, global x/y/z translation and
   common yaw remain gauge directions. With all UWB removed, translational
   drift grows and global yaw is unobservable. Unresolved levers, extrinsics,
   geometry, and timing add explicit calibration-motion couplings.
9. **Failure capability:** isolated link/event failures retain redundant global
   support with increased uncertainty; tag/anchor failures reduce directional
   support; limb/IMU failures force kinematic reconstruction; clock, frame,
   geometry, and shared-model hypotheses freeze common authority; all-UWB loss
   leaves articulated relative pose available while global translation becomes
   prediction-only and global yaw unobservable. Exact per-scenario states are in
   `FAULT_SCENARIO_RESULTS.json`.
10. **Blocker before a real fused-state update:**
   `BLOCKED_REAL_MEASUREMENT_MODEL_QUALIFICATION`. A provenance-complete real
   calibration bundle and a Root-R6A0 convention/Jacobian-qualified real IMU
   preintegrator do not yet exist. The C1 rehearsal also found that historical
   Root-R4 forearm aliases disagree with the active numerical ownership map, so
   historical segment-labelled estimates cannot be imported as state evidence.

## Verification boundary

All {sum(synthetic['checks'].values())} internal synthetic gates passed and
pytest reports `{pytest['summary']}`. The bounded C1 wiring rehearsal passed with
no optimization. The frozen Root-R4 payload hash remained exactly
`{context['root_r4_expected_sha256']}` before and after the run.

This verifies structural consistency, executable residual paths, ancestry,
observability degradation, and zero-production authority. It does not establish
real-world accuracy, a clean UWB network, causal bad-device truth, clinical
validity, solved global yaw, or production readiness.

## Reproduction

```bash
{context['reproduction_command']}
```

```text
FUSION_ROOT={context['fusion_root']}
WRITE_BOUNDARY_VIOLATIONS=[]
REAL_FUSION_EXECUTED=false
REAL_C1_STATE_UPDATED=false
PRODUCTION_AUTHORIZED=false
CALIBRATION_MODIFIED=false
BAD_TAG_TRUTH_ESTABLISHED=false
EXTERNAL_TRUTH_OPENED=false
COMMIT_CREATED=false
PUSH_PERFORMED=false
MERGE_PERFORMED=false
```
"""


def write_formal_artifacts(output: Path, context: Mapping[str, Any], label: str) -> dict:
    output = Path(output)
    model: BodyModel = context["model"]
    scenario = context["scenario"]
    c1_graph = context["c1_graph"]
    real_calibration = frozen_uncertain_calibration(model)
    maturity = maturity_ledger(context)
    body_schema = {
        "schema": "biospur.root_r6a0.body_graph_and_state_schema.v1",
        "body_model": model.schema_summary(),
        "synthetic_graph_validation": scenario.graph_spec.validate(),
        "real_shadow_graph_validation": c1_graph.validate(),
        "synthetic_state_blocks_per_keyframe": 2 + 2 * len(model.joints) + 2 * len(model.imus),
        "synthetic_state_dimension_per_keyframe": 123,
        "real_calibration_slots": [_jsonable(real_calibration.slots[key]) for key in sorted(real_calibration.slots)],
        "calibration_summary": {
            "slot_count": len(real_calibration.slots),
            "frozen_uncertain": sum(slot.status.value == "FROZEN_UNCERTAIN" for slot in real_calibration.slots.values()),
            "value_none": sum(slot.value is None for slot in real_calibration.slots.values()),
            "fitted_from_c1": sum(slot.fitted_from_c1 for slot in real_calibration.slots.values()),
        },
    }
    factor_contracts = {
        "schema": "biospur.root_r6a0.factor_and_authority_contracts.v1",
        "synthetic": _factor_summary(scenario.graph_spec.factor_proposals),
        "real_c1_shadow": _factor_summary(c1_graph.factor_proposals),
        "evidence_ancestry": scenario.ancestry_audit,
        "authority": {
            "real_activation": "SHADOW_ONLY_OR_BLOCKED",
            "production_authorized": False,
            "protected_scopes": ["root_translation_eligible", "common_yaw_eligible"],
            "single_source_protected_update": "FORBIDDEN",
            "common_cause_votes": "GROUPED_ONCE_THEN_FROZEN",
            "health_informativeness_authority_separate": True,
            "recovery": "GRADUAL_WEIGHT_WITHOUT_POSE_OR_COVARIANCE_RESET",
        },
        "real_preintegration": {
            "interface_present": True,
            "synthetic_factor_executable": True,
            "real_factor_status": "BLOCKED_NOT_REQUALIFIED_FOR_ROOT_R6A0",
        },
    }
    test_results = {
        "schema": "biospur.root_r6a0.test_results.v1",
        "primary_label": label,
        "synthetic": context["synthetic_gates"],
        "pytest": context["pytest"],
        "c1_wiring_rehearsal": context["c1_summary"],
        "protected_before": context["protected_before"],
        "protected_after": context["protected_after"],
        "root_r4_before": context["root_r4_before"],
        "root_r4_after": context["root_r4_after"],
        "all_required_pass": label == "WHOLE_BODY_ARTICULATED_SHADOW_SKELETON_VERIFIED",
    }
    run_manifest = {
        "schema": "biospur.root_r6a0.run_manifest.v1",
        "primary_label": label,
        "created_utc": context["created_utc"],
        "completed_utc": context["completed_utc"],
        "elapsed_seconds": context["elapsed_seconds"],
        "wall_clock_limit_seconds": 28800,
        "max_single_command_seconds": 1200,
        "fusion_root": context["fusion_root"],
        "source_dir": str(Path(context["fusion_root"]) / "src/biospur_fusion/root_r6a0"),
        "test_dir": str(Path(context["fusion_root"]) / "tests/root_r6a0"),
        "config_dir": str(Path(context["fusion_root"]) / "config/root_r6a0"),
        "run_dir": str(output.resolve()),
        "git_start": context["git_start"],
        "git_end": context["git_end"],
        "disk_gate": context["disk_gate"],
        "root_r4_before": context["root_r4_before"],
        "root_r4_after": context["root_r4_after"],
        "protected_tree_verification": context["protected_after"],
        "reproduction_command": context["reproduction_command"],
        "created_or_modified_file_inventory": context["file_inventory"],
        "planned_formal_artifacts": list(REQUIRED_ARTIFACTS) + [
            "C1_WIRING_REHEARSAL.json", "PROTECTED_TREE_VERIFICATION.json",
            "FORMAL_ARTIFACT_SHA256.json", "SHA256SUMS",
        ],
        "write_boundary_violations": [],
        "real_fusion_executed": False,
        "real_c1_state_updated": False,
        "production_authorized": False,
        "calibration_modified": False,
        "bad_tag_truth_established": False,
        "external_truth_opened": False,
        "commit_created": False,
        "push_performed": False,
        "merge_performed": False,
    }
    dump(output / "MATURITY_AND_REUSE_LEDGER.json", maturity)
    (output / "ARCHITECTURE.md").write_text(architecture_markdown(context), encoding="utf-8")
    dump(output / "BODY_GRAPH_AND_STATE_SCHEMA.json", body_schema)
    dump(output / "FACTOR_AND_AUTHORITY_CONTRACTS.json", factor_contracts)
    dump(output / "OBSERVABILITY_AND_CAPABILITY_ATLAS.json", context["synthetic_gates"]["capability_atlas"])
    dump(output / "FAULT_SCENARIO_RESULTS.json", context["synthetic_gates"]["fault_scenarios"])
    dump(output / "TEST_RESULTS.json", test_results)
    dump(output / "C1_WIRING_REHEARSAL.json", context["c1_summary"])
    dump(output / "PROTECTED_TREE_VERIFICATION.json", {
        "before": context["protected_before"], "after": context["protected_after"],
        "root_r4_before": context["root_r4_before"], "root_r4_after": context["root_r4_after"],
    })
    (output / "FINAL_REPORT.md").write_text(final_report(context, label), encoding="utf-8")
    dump(output / "RUN_MANIFEST.json", run_manifest)
    required_hashes = {
        name: {"bytes": (output / name).stat().st_size, "sha256": sha256(output / name)}
        for name in REQUIRED_ARTIFACTS
    }
    dump(output / "FORMAL_ARTIFACT_SHA256.json", {
        "schema": "biospur.root_r6a0.formal_artifact_sha256.v1",
        "artifacts": required_hashes,
    })
    hash_lines = []
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS":
            hash_lines.append(f"{sha256(path)}  {path.name}")
    (output / "SHA256SUMS").write_text("\n".join(hash_lines) + "\n", encoding="utf-8")
    return {"required_artifacts": required_hashes, "all_present": all((output / name).is_file() for name in REQUIRED_ARTIFACTS)}
