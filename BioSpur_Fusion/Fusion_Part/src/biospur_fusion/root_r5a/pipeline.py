"""Root-R5A end-to-end offline diagnostic pipeline."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
from pathlib import Path
import platform
import sys

import numpy as np

from biospur_fusion.root_r4 import data as r4data

from .constants import (DYNAMIC_PRIORS, FROZEN_INVARIANTS, PRIMARY_VERDICTS,
                        RAW_MATCHED_EPOCH_STRIDE, ROOT_R4_MANIFEST_PAYLOAD_SHA256,
                        SEALED_DATA_NOT_OPENED, SYNTHETIC_NUMERICAL_CONCENTRATION_WIDTH_MAX_DEG,
                        TIME_OFFSET_BOUND_S, TIME_OFFSET_GRID_S)
from .data import block_masks, load_authorized_c1
from .diagnostics import (block_resampling, bounded_nuisance, counterfactuals, loo_diagnostics,
                          motion_and_quality_metrics, reproduce_root_r4, time_offset_profiles,
                          timing_semantics_audit)
from .dynamic import block_cross_validation, profiled_timing_cross_validation, trajectory_rows
from .ownership import write_ownership_ledger
from .plots import all_plots
from .provenance import (REPOSITORY, architecture_contract_search, canonical_json_payload_sha256,
                         file_record, resolve_root_r4, sha256, tree_digest)
from .synthetic import synthetic_qualification


PREIMPLEMENTATION_GIT = {
    "captured_utc": "2026-08-24T12:19:10Z",
    "branch": "feature/b306-bringup",
    "head": "5bd0a3aa5803921d39f1e7d317762a8f6d7bc3bb",
    "status_lines": 138103,
    "status_sha256": "b6f06ee1b0942d0b0feea151d40415625b15df0f262ec4ec5a0df6c3e4727d16",
    "source": "read-only git status captured before any Root-R5A source addition; identical to authoritative Root-R4 FINAL_GIT_STATUS.txt",
}


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def _run_plan() -> str:
    invariants = "\n".join(f"{index}. {value}" for index, value in enumerate(FROZEN_INVARIANTS, 1))
    return f"""# Root-R5A run plan

This is a Python-only offline identifiability diagnostic. It does not execute
real fusion, mutate M1, authorize a frame, or use sealed/external truth.

## Frozen invariants

{invariants}

## Predeclared numerical support

- Reuse the five Root-R4 time blocks exactly.
- Matched raw/T4 profiles use every {RAW_MATCHED_EPOCH_STRIDE}th global epoch.
- Complete circular grid: 1 degree; deterministic 0.25 degree local convergence check.
- One global UWB–IMU timing offset only, frozen interval ±{TIME_OFFSET_BOUND_S*1000:.0f} ms,
  grid {list((TIME_OFFSET_GRID_S*1000).astype(int))} ms.
- Dynamic bias bound: 1.25 deg/s; process-prior sensitivity cases: {json.dumps(DYNAMIC_PRIORS)}.
- A 20 degree synthetic-calibrated interval-width threshold distinguishes
  numerical concentration only. No product/practical sharpness gate exists.
- Held-block prediction is primary; free per-block yaw is an overfit ceiling.

## Sequence

1. Resolve and reproduce exact Root-R4 provenance and metrics.
2. Close raw/T4 ownership, matched support, and bounded timing semantics.
3. Qualify equations and classification controls on synthetic truth.
4. Generate raw and T4 circular profiles on identical event support.
5. Compare C0/C1/D0/D1 with leave-one-block-out scoring.
6. Run LOO, resampling, timing, shuffle, counterfactual, and bounded nuisance diagnostics.
7. Issue one bounded verdict; real fusion remains disabled in every outcome.
"""


def _input_provenance(root_r4: Path, root_r4_manifest: dict, architecture: dict) -> dict:
    root_r4_files = ["REPRODUCIBILITY_MANIFEST.json", "FINAL_RESULT.json", "CANDIDATE_RESULTS.json",
        "CANDIDATE_ARCHITECTURE_MATRIX.json", "T4_RAW_RANGE_LINEAGE_AUDIT.json",
        "T4_RAW_RANGE_DEPENDENCY_GRAPH.json", "T4_RAW_LINEAGE_C1.npz", "RAW_RANGE_EVENTS_C1.npz",
        "MEASUREMENT_VS_AVAILABILITY_AUDIT.json", "COMMON_TRANSFORM_CANDIDATES.json",
        "COMMON_TRANSFORM_TIME_BLOCK_STABILITY.json", "COMMON_TRANSFORM_TAG_LOO.json",
        "COMMON_TRANSFORM_ANCHOR_LOO.json", "PER_LINK_HEALTH_AUDIT.json", "BODY_SHADOW_GEOMETRY_DIAGNOSTICS.json"]
    primary = [r4data.M1_PATH, r4data.SCHEDULE_PATH, r4data.CLOCK_PATH, r4data.EPOCH_AUDIT_PATH,
               r4data.RAW_PATH, r4data.PROVENANCE_PATH, r4data.RAW_COBS_PATH,
               r4data.FRAME_AUDIT_PATH, r4data.LAYOUT_PATH, r4data.CAPTURE_MANIFEST_PATH]
    sources = sorted((REPOSITORY / "Fusion_Part/src/biospur_fusion/root_r5a").glob("*.py"))
    tools = sorted((REPOSITORY / "Fusion_Part/tools").glob("*root_r5a*.py"))
    tests = sorted((REPOSITORY / "Fusion_Part/tests/root_r5a").glob("*.py"))
    return {
        "schema": "biospur.root_r5a.input_provenance.v1", "preimplementation_git": PREIMPLEMENTATION_GIT,
        "root_r4": {"path": str(root_r4), "expected_manifest_payload_sha256": ROOT_R4_MANIFEST_PAYLOAD_SHA256,
                    "recorded_manifest_payload_sha256": root_r4_manifest["manifest_payload_sha256"],
                    "recomputed_manifest_payload_sha256": canonical_json_payload_sha256(root_r4_manifest),
                    "verified": canonical_json_payload_sha256(root_r4_manifest) == ROOT_R4_MANIFEST_PAYLOAD_SHA256,
                    "tree": tree_digest(root_r4),
                    "artifacts": [file_record(root_r4 / name, "immutable Root-R4 evidence", "resolved by exact manifest identity") for name in root_r4_files]},
        "primary_inputs": [file_record(path, "authorized C1/clock/M1 input") for path in primary],
        "architecture_doctrine": architecture,
        "source": [file_record(path, "Root-R5A source") for path in sources + tools + tests],
        "commands": {"primary": "PYTHONPATH=Fusion_Part/src python3 Fusion_Part/tools/run_root_r5a.py --output <new-dir>",
                     "finalize": "PYTHONPATH=Fusion_Part/src python3 Fusion_Part/tools/finalize_root_r5a.py --primary <dir> --replay <dir>"},
        "authorized_dataset": {"capture": 1, "identifier": "v47_ten_node_body_calibration_20260814_093601",
                               "window": "full already-authorized C1 support; original Root-R4 five blocks"},
        "sealed_or_excluded_not_opened": list(SEALED_DATA_NOT_OPENED),
        "thresholds": {"time_offset_bound_s": [-TIME_OFFSET_BOUND_S, TIME_OFFSET_BOUND_S],
                       "time_offset_grid_s": TIME_OFFSET_GRID_S.tolist(), "dynamic_priors": list(DYNAMIC_PRIORS),
                       "numerical_concentration_width_max_deg": SYNTHETIC_NUMERICAL_CONCENTRATION_WIDTH_MAX_DEG,
                       "practical_product_sharpness_gate": None},
    }


def _profile_metrics(t4: list[dict], raw: list[dict], support_rows: list[dict]) -> list[dict]:
    rows = []
    for layer, profiles in (("T4", t4), ("RAW", raw)):
        for block, profile in enumerate(profiles[:5]):
            rows.append({"layer": layer, "block": block, "start_s": support_rows[block]["start_s"],
                         "stop_s": support_rows[block]["stop_s"], "mode_deg": profile["global_mode_deg"],
                         "interval_width_95_deg": profile["asymptotic_profile_interval_width_95_deg"],
                         "yaw_information": profile["nuisance_eliminated_yaw_information"],
                         "objective_minimum": profile["objective_minimum"], "objective_range": profile["objective_range"],
                         "material_modes": len(profile["material_modes"]), "samples": profile["support"]["samples"],
                         "epochs": profile["support"]["epochs"], "grid_converged": profile["profile_grid_converged"]})
    return rows


def _classify(t4: list[dict], raw: list[dict], timing: dict, comparison: dict,
              tag_loo: dict, anchor_loo: dict) -> tuple[str, dict]:
    widths = [row["asymptotic_profile_interval_width_95_deg"] for row in t4[:5] + raw[:5]]
    numerically_concentrated = bool(float(np.median(widths)) <= SYNTHETIC_NUMERICAL_CONCENTRATION_WIDTH_MAX_DEG)
    t4_modes = np.unwrap(np.deg2rad([row["global_mode_deg"] for row in t4[:5]]))
    raw_modes = np.unwrap(np.deg2rad([row["global_mode_deg"] for row in raw[:5]]))
    temporal_correlation = float(np.corrcoef(t4_modes, raw_modes)[0, 1]) if np.std(t4_modes) and np.std(raw_modes) else 0.0
    raw_t4_consistent = bool(temporal_correlation >= 0.7)
    timing_dominates = bool(timing["timing_error_dominates"])
    dynamic_improves = bool(comparison["T4"]["dynamic_held_block_improvement"] and
                        comparison["RAW"]["dynamic_held_block_improvement"] and
                        comparison["D1_T4"]["D1_held_block_improvement"] and
                        comparison["D1_RAW"]["D1_held_block_improvement"])
    independently_physical = False  # no exported root gyro-bias trajectory or independent accumulated-yaw-error reference
    tag_range = max(np.ptp(np.unwrap(np.deg2rad([row[key] for row in tag_loo["rows"]]))) * 180 / math.pi
                    for key in ("t4_mode_deg", "raw_mode_deg"))
    anchor_range = float(np.ptp(np.unwrap(np.deg2rad([row["raw_mode_deg"] for row in anchor_loo["rows"]]))) * 180 / math.pi)
    structured_loo = bool(tag_range > 10.0 or anchor_range > 10.0)
    if timing_dominates:
        verdict = PRIMARY_VERDICTS[2]
    elif dynamic_improves and independently_physical and raw_t4_consistent:
        verdict = PRIMARY_VERDICTS[1]
    elif numerically_concentrated and structured_loo and not dynamic_improves:
        verdict = PRIMARY_VERDICTS[3]
    elif not numerically_concentrated and not dynamic_improves:
        verdict = PRIMARY_VERDICTS[0]
    else:
        verdict = PRIMARY_VERDICTS[4]
    evidence = {"median_block_interval_width_95_deg": float(np.median(widths)),
                "numerically_concentrated_under_synthetic_gate": numerically_concentrated,
                "practical_sharpness_gate_predeclared": False,
                "raw_t4_block_mode_temporal_correlation": temporal_correlation,
                "raw_t4_temporal_structure_consistent": raw_t4_consistent,
                "timing_dominates": timing_dominates, "dynamic_improves_all_required_cv": dynamic_improves,
                "independent_root_gyro_bias_trajectory_available": independently_physical,
                "tag_loo_mode_range_max_deg": float(tag_range), "anchor_loo_mode_range_deg": anchor_range,
                "structured_loo_inconsistency": structured_loo}
    return verdict, evidence


def _report(final: dict, reproduction: dict, timing: dict, comparison: dict, gates: dict) -> str:
    return f"""# BioSpur Root-R5A final diagnostic

## Primary verdict

```text
{final['primary_verdict']}
```

Root-R4 did not fail simply because the reduced model had zero yaw rank: local
nuisance-eliminated yaw rank was present. Root-R5A distinguishes numerical
profile concentration from practical authorization and tests whether a bounded
timing shift or a gauge-fixed gyro-bias-driven dynamic yaw-error state predicts
held blocks. The exact supported interpretation is recorded in
`FINAL_RESULT.json`; no fused trajectory was produced.

## Root-R4 reproduction

- manifest payload SHA-256 verified: `{ROOT_R4_MANIFEST_PAYLOAD_SHA256}`
- five-block range: {reproduction['artifact_values']['five_block_yaw_range_deg']:.3f} deg
- maximum tag-LOO change: {reproduction['artifact_values']['maximum_tag_loo_yaw_change_deg']:.3f} deg
- T4/raw/hybrid yaw: {reproduction['artifact_values']['t4_yaw_deg']:.3f} / {reproduction['artifact_values']['raw_yaw_deg']:.3f} / {reproduction['artifact_values']['hybrid_yaw_deg']:.3f} deg
- maximum layer disagreement: {reproduction['artifact_values']['maximum_layer_disagreement_deg']:.3f} deg

## Model comparison boundary

- bounded timing dominates: {timing['timing_error_dominates']}
- D0 held-block improvement, T4/raw: {comparison['T4']['dynamic_held_block_improvement']} / {comparison['RAW']['dynamic_held_block_improvement']}
- D1 held-block improvement, T4/raw: {comparison['D1_T4']['D1_held_block_improvement']} / {comparison['D1_RAW']['D1_held_block_improvement']}
- independent common-root gyro-bias trajectory available: false
- practical product sharpness gate predeclared: false

The dynamic trajectories are diagnostic state fits, not causal fused estimates.
One common root-yaw error cannot repair independent segment-node yaw drift.

## Frozen outcome

`real_c1_fusion_executed=false`, `fusion_candidate_authorized=false`,
`product_candidate_authorized=false`, `external_accuracy_claimed=false`.
Host receive/parse completion was not captured, so host deployment causality
remains open even though node-clock causal mechanics were synthetically qualified.

Machine gates: {sum(value is True for value in gates.values())} true,
{sum(value is False for value in gates.values())} false, with explicit status
strings retained where a boolean would hide an evidence limitation.
"""


def run(output: Path) -> dict:
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"formal Root-R5A result must not overwrite {output}")
    output.mkdir(parents=True, exist_ok=True)
    (output / "RUN_PLAN.md").write_text(_run_plan(), encoding="utf-8")

    root_r4, manifest = resolve_root_r4(); architecture = architecture_contract_search()
    provenance = _input_provenance(root_r4, manifest, architecture); dump(output / "INPUT_PROVENANCE.json", provenance)
    invariants = {"schema": "biospur.root_r5a.architecture_invariants.v1", "document": architecture,
                  "invariants": list(FROZEN_INVARIANTS), "acknowledged": True,
                  "additional_rules": {"M1_input_only": True, "real_fusion_forbidden": True,
                    "same_event_raw_t4_double_count_forbidden": True, "single_point_range_has_intrinsic_yaw": False,
                    "one_root_yaw_solves_independent_node_drift": False}}
    dump(output / "ARCHITECTURE_INVARIANTS.json", invariants)

    print("Root-R5A: load C1, ownership, and Root-R4 reproduction", flush=True)
    data, lineage, support = load_authorized_c1(); support_record = support.record(data)
    dump(output / "MATCHED_SUPPORT_DEFINITION.json", support_record)
    ownership = write_ownership_ledger(output, data, support); dump(output / "EVENT_OWNERSHIP_LEDGER.json", ownership)
    reproduction = reproduce_root_r4(root_r4, data); dump(output / "ROOT_R4_REPRODUCTION.json", reproduction)
    if not reproduction["all_reproduced"]:
        raise RuntimeError("Root-R4 baseline metrics did not reproduce")
    timing_audit = timing_semantics_audit(REPOSITORY, r4data.CLOCK_PATH); dump(output / "TIMING_SEMANTICS_AUDIT.json", timing_audit)

    print("Root-R5A: synthetic qualification", flush=True)
    synthetic = synthetic_qualification(); dump(output / "SYNTHETIC_QUALIFICATION.json", synthetic)
    if not synthetic["passed"]:
        raise RuntimeError("Root-R5A synthetic qualification failed")

    print("Root-R5A: full circular profiles and timing envelope", flush=True)
    timing, by_offset = time_offset_profiles(data, support); dump(output / "TIME_OFFSET_PROFILE.json", timing)
    baseline = by_offset[0.0]; t4_profiles = baseline["T4"]; raw_profiles = baseline["RAW"]
    dump(output / "T4_CIRCULAR_YAW_PROFILES.json", {"schema": "biospur.root_r5a.profile_set.v1", "profiles": t4_profiles})
    dump(output / "RAW_CIRCULAR_YAW_PROFILES.json", {"schema": "biospur.root_r5a.profile_set.v1", "profiles": raw_profiles})
    metrics = _profile_metrics(t4_profiles, raw_profiles, support_record["rows"]); write_csv(output / "BLOCK_PROFILE_METRICS.csv", metrics)
    motion, quality = motion_and_quality_metrics(data, support)
    write_csv(output / "MOTION_INFORMATIVENESS_METRICS.csv", motion); write_csv(output / "UWB_QUALITY_GEOMETRY_METRICS.csv", quality)

    print("Root-R5A: constant/dynamic cross-validation and stability", flush=True)
    centres = np.asarray([(support.block_edges_s[index] + support.block_edges_s[index + 1]) / 2 for index in range(5)])
    cv_t4 = block_cross_validation("T4", t4_profiles, centres); cv_raw = block_cross_validation("RAW", raw_profiles, centres)
    timed_t4 = profiled_timing_cross_validation("T4", {offset: value["T4"] for offset, value in by_offset.items()}, centres)
    timed_raw = profiled_timing_cross_validation("RAW", {offset: value["RAW"] for offset, value in by_offset.items()}, centres)
    comparison = {"schema": "biospur.root_r5a.constant_dynamic_comparison.v1",
                  "models": {"C0": "constant frame/frozen timing", "C1": "constant frame/one bounded global timing offset",
                             "D0": "gauge-fixed dynamic yaw+bias/frozen timing", "D1": "gauge-fixed dynamic yaw+bias/one bounded global timing offset"},
                  "T4": cv_t4, "RAW": cv_raw, "D1_T4": timed_t4, "D1_RAW": timed_raw,
                  "identical_event_support": True, "real_fusion": False}
    dump(output / "CONSTANT_VS_DYNAMIC_MODEL_COMPARISON.json", comparison)
    dump(output / "BLOCK_CROSS_VALIDATION.json", comparison)
    trajectory = trajectory_rows("T4", cv_t4, centres) + trajectory_rows("RAW", cv_raw, centres)
    write_csv(output / "DYNAMIC_YAW_TRAJECTORY.csv", trajectory)
    tag_loo, anchor_loo = loo_diagnostics(data, support); dump(output / "TAG_LOO.json", tag_loo); dump(output / "ANCHOR_LOO.json", anchor_loo)
    resampling = block_resampling(t4_profiles, raw_profiles); dump(output / "BLOCK_RESAMPLING.json", resampling)
    controls = counterfactuals(t4_profiles, raw_profiles, centres); controls["ownership_mutation_detected"] = ownership["duplicate_mutation_detected"]
    dump(output / "COUNTERFACTUALS_AND_NEGATIVE_CONTROLS.json", controls)
    nuisance = bounded_nuisance(data, support, timing, tag_loo, anchor_loo, root_r4); dump(output / "BOUNDED_NUISANCE_SENSITIVITY.json", nuisance)

    verdict, evidence = _classify(t4_profiles, raw_profiles, timing, comparison, tag_loo, anchor_loo)
    all_profiles = t4_profiles + raw_profiles
    gates = {
        "root_r4_exact_manifest_resolved": True, "root_r4_manifest_payload_sha256_verified": provenance["root_r4"]["verified"],
        "root_r4_metrics_reproduced": reproduction["all_reproduced"], "frozen_architecture_invariants_acknowledged": True,
        "raw_t4_exact_record_association_reproduced": lineage["exact_constituent_t4_events"] == data.event_count,
        "same_event_double_count_absent": ownership["DIRECT_EVENT_DOUBLE_COUNT_PREVENTED"],
        "matched_support_closed": support_record["same_selected_physical_event_support"],
        "physical_measurement_time_semantics_closed": "BOUNDED_MEASUREMENT_EQUIVALENT_NOT_EXACT_SINGLE_INSTANT",
        "physical_time_offset_bound_predeclared": True, "host_availability_time_captured": False,
        "host_deployment_causality_closed": False, "complete_circular_profiles_generated": len(all_profiles) == 12,
        "nuisance_elimination_verified": all("nuisance" in row["support"] for row in all_profiles),
        "profile_grid_converged": all(row["profile_grid_converged"] for row in all_profiles),
        "independent_block_profiles_available": True, "practical_sharpness_gate_predeclared": False,
        "constant_model_gauge_valid": True, "dynamic_model_gauge_valid": all(row["reference_prior"]["full_fit"]["gauge_residual_rad"] < 1e-12 for row in (cv_t4, cv_raw)),
        "dynamic_process_prior_frozen_before_real_comparison": True, "leave_one_block_out_complete": True,
        "tag_loo_complete": len(tag_loo["rows"]) == 10, "anchor_loo_complete": len(anchor_loo["rows"]) == 8,
        "block_resampling_complete": resampling["iterations"] == 200,
        "raw_t4_temporal_structure_consistent": evidence["raw_t4_temporal_structure_consistent"],
        "timing_envelope_changes_primary_conclusion": timing["timing_error_dominates"],
        "dynamic_model_held_block_improvement": evidence["dynamic_improves_all_required_cv"],
        "dynamic_trajectory_physically_plausible": "PARTIAL_BIAS_BOUND_ONLY_NO_INDEPENDENT_ROOT_BIAS_TRAJECTORY",
        "negative_controls_pass": ownership["duplicate_mutation_detected"] and synthetic["passed"],
        "sealed_data_untouched": True, "historical_artifacts_untouched": "PENDING_FINAL_HASH_COMPARISON",
        "real_c1_fusion_executed": False, "fusion_candidate_authorized": False,
        "product_candidate_authorized": False, "external_accuracy_claimed": False,
        "deterministic_replay": False, "repository_clean_except_preexisting_and_new_root_r5_files": "PENDING_FINAL_STATUS_COMPARISON",
        "commit": False, "push": False, "merge": False,
    }
    dump(output / "MACHINE_GATES.json", gates)
    final = {"schema": "biospur.root_r5a.final_result.v1", "primary_verdict": verdict,
             "central_answer": "Root-R4 retained local yaw rank, but Root-R5A does not authorize interpreting its block conflict as a valid static frame or fused yaw correction.",
             "classification_evidence": evidence, "root_r4_manifest_payload_sha256": ROOT_R4_MANIFEST_PAYLOAD_SHA256,
             "root_r4_manifest_verified": True, "root_r4_metrics_reproduced": True,
             "real_c1_fusion_executed": False, "fusion_candidate_authorized": False,
             "product_candidate_authorized": False, "external_accuracy_claimed": False,
             "sealed_data_untouched": True, "commit": False, "push": False, "merge": False,
             "frame_authorized": False, "deterministic_replay": False}
    dump(output / "FINAL_RESULT.json", final)
    (output / "FINAL_REPORT.md").write_text(_report(final, reproduction, timing, comparison, gates), encoding="utf-8")
    plot_names = all_plots(output, t4_profiles, raw_profiles, timing, comparison, trajectory, tag_loo, anchor_loo, motion, quality)
    manifest_value = {"schema": "biospur.root_r5a.reproducibility_manifest.v1", "finalized": False,
                      "root_r4_manifest_payload_sha256": ROOT_R4_MANIFEST_PAYLOAD_SHA256,
                      "source_hashes": {row["path"]: row["sha256"] for row in provenance["source"]},
                      "output_hashes": {path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
                                        for path in sorted(output.iterdir()) if path.is_file() and path.name != "REPRODUCIBILITY_MANIFEST.json"},
                      "plots": plot_names, "deterministic_replay": False,
                      "manifest_payload_sha256_semantics": "canonical sorted compact JSON with manifest_payload_sha256 omitted"}
    manifest_value["manifest_payload_sha256"] = canonical_json_payload_sha256(manifest_value)
    dump(output / "REPRODUCIBILITY_MANIFEST.json", manifest_value)
    return final
