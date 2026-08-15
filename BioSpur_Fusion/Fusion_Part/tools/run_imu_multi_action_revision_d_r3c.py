#!/usr/bin/env python3
"""R3C freeze, Q2-through-synthetic, observation-only, and one formal run."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Mapping

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Fusion_Part/src"))

from biospur_fusion.imu_multi_action_engineering_v1.common_time import build_common_timeline
from biospur_fusion.imu_multi_action_engineering_v1.pipeline import load_q2_cache
from biospur_fusion.imu_multi_action_revision_d.r3b_topology import (
    legacy_reference_diagnostic,
    phase_groups_from_cycle_vectors,
    quantiles,
    runs,
)
from biospur_fusion.imu_multi_action_revision_d.r3c_activity import (
    analyze_chain,
    build_chain_signal,
    q2_through_synthetic_qualification,
)
from biospur_fusion.imu_preview_v0.io import savez_deterministic


BASELINE_HEAD = "7c659b24b714b1ef4d9143658d1a6ee49ffb92ce"
MODULE = ROOT / "Fusion_Part/src/biospur_fusion/imu_multi_action_revision_d/r3c_activity.py"
RUNNER = Path(__file__).resolve()
Q2_SOURCE = ROOT / "Fusion_Part/src/biospur_fusion/imu_multi_action_engineering_v1/q2.py"
COMMON_TIME_SOURCE = ROOT / "Fusion_Part/src/biospur_fusion/imu_multi_action_engineering_v1/common_time.py"
ACTIONS = ("initial_still_attempt2", "t_pose", "arms", "left_elbow", "right_elbow_attempt2", "left_knee", "right_knee", "left_heel", "right_heel", "squats", "trunk")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean(value: Any) -> Any:
    if isinstance(value, np.ndarray): return clean(value.tolist())
    if isinstance(value, (np.integer,)): return int(value)
    if isinstance(value, (np.floating,)): return clean(float(value))
    if isinstance(value, float): return value if math.isfinite(value) else None
    if isinstance(value, dict): return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)): return [clean(item) for item in value]
    return value


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(clean(value), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")


def manifest(output: Path) -> None:
    dump(output / "SHA256_MANIFEST.json", {str(path.relative_to(output)): sha256(path) for path in sorted(output.rglob("*")) if path.is_file() and path.name != "SHA256_MANIFEST.json"})


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def chain_specs(mapping: Mapping[str, Any]) -> list[dict[str, str]]:
    output = []
    for action, item in mapping["actions"].items():
        for group, role in (("primary_chains", "PRIMARY"), ("diagnostic_chains", "DIAGNOSTIC")):
            for name, pair in item.get(group, {}).items():
                output.append({"key": f"{action}:{name}", "action": action, "name": name, "parent": pair[0], "child": pair[1], "role": role})
    return output


def verify_manifest(directory: Path) -> None:
    expected = json.loads((directory / "SHA256_MANIFEST.json").read_text())
    for relative, digest in expected.items():
        if sha256(directory / relative) != digest:
            raise RuntimeError(f"manifest mismatch: {directory / relative}")


def freeze(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists(): raise FileExistsError(args.output)
    if git_head() != BASELINE_HEAD: raise RuntimeError("baseline HEAD changed")
    verify_manifest(args.r3c0)
    history = json.loads((args.r3c0 / "R3_HISTORY_REPRODUCIBILITY_AUDIT.json").read_text())
    dimensional = json.loads((args.r3c0 / "ACTIVITY_DIMENSIONAL_ANALYSIS.json").read_text())
    if history["classification"] == "UNRESOLVED" or not history["resolved_for_r3c_r_gate"]:
        raise RuntimeError("historical reproducibility unresolved")
    if dimensional["verdict"] != "PASS_ROOT_CAUSE_IDENTIFIED":
        raise RuntimeError("dimensional audit not passed")
    args.output.mkdir(parents=True)
    for path in (args.contract, args.chain_map): shutil.copyfile(path, args.output / path.name)
    record = {
        "schema": "biospur-r3c-pre-formal-freeze-v1",
        "baseline_head": BASELINE_HEAD,
        "contract_sha256": sha256(args.contract),
        "chain_map_sha256": sha256(args.chain_map),
        "module_sha256": sha256(MODULE),
        "runner_sha256": sha256(RUNNER),
        "q2_source_sha256": sha256(Q2_SOURCE),
        "common_time_source_sha256": sha256(COMMON_TIME_SOURCE),
        "q2_config_sha256": sha256(args.q2_gates),
        "r3c0_manifest_sha256": sha256(args.r3c0 / "SHA256_MANIFEST.json"),
        "historical_classification": history["classification"],
        "dimensional_analysis": "PASS",
        "formal_r3c_accessed": False,
        "thresholds_selected_from_formal_r3c_result": False,
        "R3B_ORIGINAL_VERDICT": "FAIL_REQUIRED_MOTION_EVIDENCE_MISSING",
        "R3B_ADOPTABLE": False,
        "R3B_PRIMARY_BLOCKER": "FAIL_ACTIVITY_UNCERTAINTY_MODEL_INVALID",
        "FINAL_STILL_STATUS": "SEALED",
        "D0": "NOT_STARTED",
    }
    dump(args.output / "R3C_RUN_FREEZE.json", record)
    manifest(args.output)
    return record


def verify_freeze(directory: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    verify_manifest(directory)
    record = json.loads((directory / "R3C_RUN_FREEZE.json").read_text())
    contract = json.loads((directory / "R3C_ACTIVITY_MODEL_CONTRACT.json").read_text())
    mapping = json.loads((directory / "R3C_ACTION_CHAIN_MAP.json").read_text())
    checks = {
        "contract_sha256": sha256(directory / "R3C_ACTIVITY_MODEL_CONTRACT.json"),
        "chain_map_sha256": sha256(directory / "R3C_ACTION_CHAIN_MAP.json"),
        "module_sha256": sha256(MODULE),
        "runner_sha256": sha256(RUNNER),
        "q2_source_sha256": sha256(Q2_SOURCE),
        "common_time_source_sha256": sha256(COMMON_TIME_SOURCE),
    }
    for key, value in checks.items():
        if record[key] != value: raise RuntimeError(f"frozen binding changed: {key}")
    q2_config = json.loads((ROOT / "Fusion_Part/config/imu_multi_action_engineering_preview_v1/gates_v1.json").read_text())
    return record, contract, mapping, q2_config


def synthetic(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists(): raise FileExistsError(args.output)
    freeze_record, contract, _, q2_config = verify_freeze(args.freeze)
    if sha256(ROOT / "Fusion_Part/config/imu_multi_action_engineering_preview_v1/gates_v1.json") != freeze_record["q2_config_sha256"]:
        raise RuntimeError("Q2 config changed after freeze")
    args.output.mkdir(parents=True)
    result = q2_through_synthetic_qualification(contract, q2_config["q2"])
    dump(args.output / "R3C_Q2_THROUGH_SYNTHETIC_QUALIFICATION.json", result)
    dump(args.output / "R3C_SYNTHETIC_BINDING.json", {"freeze_manifest_sha256": sha256(args.freeze / "SHA256_MANIFEST.json"), "module_sha256": freeze_record["module_sha256"], "q2_source_sha256": freeze_record["q2_source_sha256"], "real_capture_accessed": False, "pass": result["pass"]})
    manifest(args.output)
    return result


def real_context(args: argparse.Namespace, contract: Mapping[str, Any]):
    phase = json.loads((args.phase_a / "RESULT.json").read_text())
    cache = args.phase_a / "Q2_HUMAN_QUASI_STATIC_CACHE.npz"
    if not phase["pass"] or sha256(cache) != phase["q2_cache_sha256"]: raise RuntimeError("Phase-A cache binding failed")
    if phase["final_still"] != "SEALED" or phase["data_access"]["final_still"] != "SEALED_NOT_OPENED": raise RuntimeError("final_still firewall failed")
    r2 = json.loads((args.r2 / "ACTION_PHASE_TIMELINE.json").read_text())
    r2_result = json.loads((args.r2 / "RESULT.json").read_text())
    if sha256(args.r2 / "ACTION_PHASE_TIMELINE.json") != r2_result["action_phase_timeline_sha256"]: raise RuntimeError("R2 binding failed")
    gates = json.loads(args.legacy_gates.read_text())
    q2 = load_q2_cache(cache)
    domains = {action: tuple(r2["actions"][action]["search_domain_ns"]) for action in ACTIONS}
    timeline = build_common_timeline(q2, min(value[0] for value in domains.values()), max(value[1] for value in domains.values()), {"rate_hz": float(contract["common_time"]["rate_hz"]), "maximum_bracket_gap_s": float(contract["common_time"]["maximum_interpolation_bracket_gap_s"]), "require_same_boot_epoch": True})
    node_index = {node: i for i, node in enumerate(timeline.node_order)}
    segment_index = {segment: node_index[node] for node, segment in gates["node_to_segment"].items()}
    return phase, cache, r2, timeline, segment_index, domains


def rows_for(timeline: Any, domain: tuple[int, int]) -> np.ndarray:
    return np.flatnonzero((timeline.time_ns >= domain[0]) & (timeline.time_ns <= domain[1]))


def observation(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists(): raise FileExistsError(args.output)
    freeze_record, contract, mapping, _ = verify_freeze(args.freeze)
    qualification = json.loads((args.synthetic / "R3C_Q2_THROUGH_SYNTHETIC_QUALIFICATION.json").read_text())
    if not qualification["pass"]: raise RuntimeError("synthetic qualification failed")
    phase, cache, r2, timeline, segment_index, domains = real_context(args, contract)
    sanity = contract["observation_only_sanity"]
    records = []
    for spec in chain_specs(mapping):
        rows = rows_for(timeline, domains[spec["action"]])
        signal = build_chain_signal(timeline, segment_index[spec["parent"]], segment_index[spec["child"]], rows, contract)
        if signal["status"] != "AVAILABLE":
            records.append({**spec, "status": signal["status"], "pass": False, "failure": "BASELINE_OR_VALID_SUPPORT"})
            continue
        baseline = signal["baseline"]; z = signal["activity_z"]; finite = signal["activity"]["valid"][rows] & np.isfinite(z[rows])
        baseline_rows = np.asarray(baseline["row_indices"], int)
        scale = float(baseline["activity_scale_rad_s"]); raw_threshold = float(baseline["activity_median_rad_s"]) + float(contract["active_bout"]["onset_activity_z"]) * scale
        false_positive = float(np.mean(z[baseline_rows][np.isfinite(z[baseline_rows])] >= float(contract["active_bout"]["onset_activity_z"])))
        p95 = float(np.percentile(z[rows][finite], 95)) if np.any(finite) else math.nan
        valid_fraction = float(np.mean(finite))
        gates = {
            "scale_finite_positive": math.isfinite(scale) and scale > 0,
            "absolute_q2_gauge_not_denominator": not signal["absolute_q2_covariance_used_in_activity_denominator"],
            "raw_threshold_physically_possible": math.degrees(raw_threshold) <= float(sanity["maximum_raw_onset_threshold_deg_s"]),
            "quiet_false_positive_controlled": false_positive <= float(sanity["maximum_quiet_false_positive_rate"]),
            "active_distribution_reportably_separated": p95 >= float(sanity["minimum_active_p95_separation_sigma"]),
            "valid_fraction": valid_fraction >= float(sanity["minimum_pair_valid_fraction"]),
            "finite_on_valid_rows": bool(np.all(np.isfinite(z[rows][finite]))) and bool(np.any(finite)),
        }
        records.append({**spec, "status": "OBSERVATION_ONLY_NO_CYCLE_EVALUATION", "scale_rad_s": scale, "scale_deg_s": math.degrees(scale), "scale_components": baseline, "raw_onset_threshold_rad_s": raw_threshold, "raw_onset_threshold_deg_s": math.degrees(raw_threshold), "quiet_false_positive_rate": false_positive, "active_z_p95": p95, "active_z_max": float(np.max(z[rows][finite])) if np.any(finite) else None, "pair_valid_fraction": valid_fraction, "absolute_q2_covariance_trace_rad2": quantiles(signal["absolute_q2_covariance_trace_rad2_audit_only"][rows]), "gates": gates, "pass": all(gates.values()), "cycle_results_computed": False})
    passed = len(records) == len(chain_specs(mapping)) and all(item["pass"] for item in records)
    args.output.mkdir(parents=True)
    dump(args.output / "R3C_OBSERVATION_ONLY_ACTIVITY_SANITY.json", {"schema": "biospur-r3c-observation-only-sanity-v1", "chains": records, "all_19_chains_evaluated": len(records) == 19, "cycle_results_computed": False, "pass": passed, "OBSERVATION_ONLY_SANITY": "PASS" if passed else "FAIL"})
    dump(args.output / "R3C_OBSERVATION_DATA_ACCESS_AUDIT.json", {"opened": ["CALIBRATION_ONLY_Q2_CACHE", "ELEVEN_CALIBRATION_BROAD_WINDOWS", "FROZEN_MAPPING"], "forbidden_opened": [], "q2_cache_sha256": sha256(cache), "formal_r3c_consumed": False, "cycles_computed": False, "FINAL_STILL": "SEALED", "WALK": "SEALED", "GOLF": "SEALED", "BOXING": "SEALED", "UWB": "SEALED", "operator_measurements": "SEALED"})
    result = {"schema": "biospur-r3c-observation-result-v1", "pass": passed, "terminal_outcome": "PASS_R3C_OBSERVATION_ONLY_SANITY" if passed else "FAIL_ACTIVITY_UNCERTAINTY_MODEL_INVALID", "formal_r3c_consumed": False}
    dump(args.output / "RESULT.json", result); manifest(args.output)
    return result


def static_plateau(timeline: Any, rows: np.ndarray, contract: Mapping[str, Any]) -> dict[str, Any]:
    hz = float(contract["common_time"]["rate_hz"]); length = max(2, round(0.4 * hz)); candidates = []
    for offset in range(0, max(1, len(rows) - length + 1), max(1, round(0.1 * hz))):
        block = rows[offset:offset + length]
        valid = timeline.all_nodes_valid[block]
        if len(block) != length or float(np.mean(valid)) < 0.8: continue
        gyro = np.linalg.norm(timeline.gyro_rad_s[block][valid], axis=2)
        score = np.median(gyro, axis=1)
        candidates.append((float(np.median(score)), int(block[0]), block, float(np.percentile(score, 95))))
    if not candidates: return {"status": "FAIL_VALID_TIME_SUPPORT", "REFERENCE_ZERO_RETURN_EVIDENCE": "FAIL"}
    median, _, block, p95 = min(candidates, key=lambda item: item[:2])
    return {"status": "AVAILABLE", "REFERENCE_ZERO_RETURN_EVIDENCE": "PASS", "plateau": {"start_row": int(block[0]), "stop_row_exclusive": int(block[-1] + 1), "row_indices": block.tolist(), "duration_s": len(block) / hz, "gyro_median_rad_s": median, "gyro_p95_rad_s": p95}}


def arm_association(left: list[dict[str, Any]], right: list[dict[str, Any]], time_ns: np.ndarray, contract: Mapping[str, Any]) -> dict[str, Any]:
    maximum_offset = float(contract["phase_association"]["maximum_bilateral_peak_offset_s"]); minimum_overlap = float(contract["phase_association"]["minimum_bilateral_cycle_overlap_fraction"])
    candidates = []
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            overlap = max(0, min(a["stop_row_exclusive"], b["stop_row_exclusive"]) - max(a["start_row"], b["start_row"]))
            denominator = max(1, min(a["stop_row_exclusive"] - a["start_row"], b["stop_row_exclusive"] - b["start_row"]))
            offset = abs(int(time_ns[a["peak_row"]]) - int(time_ns[b["peak_row"]])) / 1e9
            if offset <= maximum_offset and overlap / denominator >= minimum_overlap: candidates.append((offset, -overlap, i, j))
    used_left, used_right, pairs = set(), set(), []
    for offset, negative_overlap, i, j in sorted(candidates):
        if i not in used_left and j not in used_right:
            used_left.add(i); used_right.add(j); pairs.append({"left": i, "right": j, "peak_offset_s": offset, "overlap_rows": -negative_overlap})
    events = [{"phase": "bilateral", "start_row": min(left[p["left"]]["start_row"], right[p["right"]]["start_row"]), "stop_row_exclusive": max(left[p["left"]]["stop_row_exclusive"], right[p["right"]]["stop_row_exclusive"]), **p} for p in pairs]
    events += [{"phase": "left_dominant", "start_row": item["start_row"], "stop_row_exclusive": item["stop_row_exclusive"], "left": i} for i, item in enumerate(left) if i not in used_left]
    events += [{"phase": "right_dominant", "start_row": item["start_row"], "stop_row_exclusive": item["stop_row_exclusive"], "right": i} for i, item in enumerate(right) if i not in used_right]
    events.sort(key=lambda item: item["start_row"])
    classes = [item["phase"] for item in events]
    return {"events": events, "pairs": pairs, "chronological_classes": classes, "left_present": "left_dominant" in classes, "right_present": "right_dominant" in classes, "bilateral_present": "bilateral" in classes}


def plot_chain(path: Path, spec: Mapping[str, str], timeline: Any, rows: np.ndarray, signal: Mapping[str, Any], evidence: Mapping[str, Any]) -> None:
    time = (timeline.time_ns[rows] - timeline.time_ns[rows[0]]) / 1e9
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True, constrained_layout=True)
    axes[0].plot(time, signal["activity"]["rate_rad_s"][rows]); axes[0].axhline(signal["baseline"]["activity_median_rad_s"], color="green", linestyle="--"); axes[0].set_ylabel("activity rad/s")
    axes[1].plot(time, signal["activity_z"][rows]); axes[1].axhline(4.0, color="red", linestyle="--", label="onset z=4"); axes[1].axhline(2.0, color="orange", linestyle=":", label="offset z=2")
    baseline = np.asarray(signal["baseline"]["row_indices"], int); axes[1].axvspan((timeline.time_ns[baseline[0]] - timeline.time_ns[rows[0]]) / 1e9, (timeline.time_ns[baseline[-1]] - timeline.time_ns[rows[0]]) / 1e9, color="green", alpha=0.15, label="quiet baseline")
    for bout in evidence["active_bouts"]: axes[1].axvspan((timeline.time_ns[bout["start_row"]] - timeline.time_ns[rows[0]]) / 1e9, (timeline.time_ns[bout["stop_row_exclusive"] - 1] - timeline.time_ns[rows[0]]) / 1e9, color="blue", alpha=0.1)
    axes[1].set_ylabel("activity z"); axes[1].legend(loc="upper right")
    axes[2].plot(time, signal["smoothed_excursion_rad"][rows]);
    for candidate in evidence["candidates"]["extrema_candidates"]:
        row = candidate["peak_row"]; axes[2].scatter((timeline.time_ns[row] - timeline.time_ns[rows[0]]) / 1e9, signal["smoothed_excursion_rad"][row], color="green" if candidate["passes_prominence"] else "red", s=18)
    axes[2].set_ylabel("excursion rad"); axes[2].set_xlabel("seconds from broad-envelope start")
    axes[0].set_title(f"R3C {spec['key']} — empirical scale={signal['baseline']['activity_scale_rad_s']:.6f} rad/s")
    for axis in axes: axis.grid(alpha=0.2)
    fig.savefig(path, dpi=140); plt.close(fig)


def formal(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists(): raise FileExistsError(args.output)
    freeze_record, contract, mapping, _ = verify_freeze(args.freeze)
    qualification = json.loads((args.synthetic / "R3C_Q2_THROUGH_SYNTHETIC_QUALIFICATION.json").read_text())
    observation_result = json.loads((args.observation / "RESULT.json").read_text())
    history = json.loads((args.r3c0 / "R3_HISTORY_REPRODUCIBILITY_AUDIT.json").read_text())
    dimensional = json.loads((args.r3c0 / "ACTIVITY_DIMENSIONAL_ANALYSIS.json").read_text())
    prerequisites = {"HISTORICAL_REPRODUCIBILITY_ISSUE": history["classification"] != "UNRESOLVED" and history["resolved_for_r3c_r_gate"], "DIMENSIONAL_ANALYSIS": dimensional["verdict"] == "PASS_ROOT_CAUSE_IDENTIFIED", "ACTIVITY_NORMALIZER_QUALIFICATION": qualification["pass"], "Q2_THROUGH_SYNTHETIC": qualification["pass"], "OBSERVATION_ONLY_SANITY": observation_result["pass"]}
    if not all(prerequisites.values()): raise RuntimeError(f"formal R3C prerequisites failed: {prerequisites}")
    phase, cache, r2, timeline, segment_index, domains = real_context(args, contract)
    specs = chain_specs(mapping); chains = {}; signals = {}; evidence_by_chain = {}; array_payload = {"common_time_ns": timeline.time_ns}
    args.output.mkdir(parents=True); plot_dir = args.output / "r3c_chain_plots"; plot_dir.mkdir()
    for spec in specs:
        rows = rows_for(timeline, domains[spec["action"]])
        signal, evidence = analyze_chain(timeline, segment_index[spec["parent"]], segment_index[spec["child"]], rows, contract)
        key = spec["key"]
        if signal["status"] != "AVAILABLE":
            chains[key] = {**spec, "status": signal["status"], "ACTIVE_MOTION_EVIDENCE": "FAIL", "CYCLE_TOPOLOGY_EVIDENCE": "FAIL"}; continue
        baseline_rows = np.asarray(signal["baseline"]["row_indices"], int)
        legacy = legacy_reference_diagnostic(signal["relative"], baseline_rows, float(contract["local_excursion_uncertainty"]["orientation_floor_rad"]))
        active_rows = sorted({row for bout in evidence["active_bouts"] for row in range(bout["start_row"], bout["stop_row_exclusive"])})
        maximum_excursion = float(np.nanmax(signal["smoothed_excursion_rad"][rows])); excursion_sigma = float(np.nanmedian(signal["excursion_uncertainty_rad"][rows]))
        functional = bool(active_rows) and maximum_excursion >= float(contract["factor_evidence"]["minimum_excursion_rad"]) and maximum_excursion / max(excursion_sigma, 1e-12) >= float(contract["factor_evidence"]["minimum_excursion_sigma"])
        summary = {**spec, "status": "AVAILABLE", "baseline": signal["baseline"], "reference": {"legacy_retained_fraction": legacy.get("retained_fraction"), "tangent_covariance_rad2": signal["reference"]["tangent_covariance_rad2"], "effective_sample_count": signal["reference"]["effective_sample_count"]}, "activity": {"rad_s": quantiles(signal["activity"]["rate_rad_s"][rows]), "z": quantiles(signal["activity_z"][rows]), "valid_fraction": float(np.mean(signal["activity"]["valid"][rows]))}, "activity_scale_components": signal["baseline"], "absolute_q2_covariance_trace_rad2_audit_only": quantiles(signal["absolute_q2_covariance_trace_rad2_audit_only"][rows]), "absolute_q2_covariance_used_in_denominator": False, "active_bouts": evidence["active_bouts"], "active_rows": active_rows, "ACTIVE_MOTION_EVIDENCE": "PASS" if active_rows else "FAIL", "cycle": evidence["cycles"], "CYCLE_TOPOLOGY_EVIDENCE": "PASS" if evidence["cycles"]["complete_cycles"] else "FAIL", "candidate_diagnostics": evidence["candidates"], "FUNCTIONAL_AXIS_FACTOR_ELIGIBILITY": "ELIGIBLE" if functional else "NOT_ELIGIBLE", "SIGN_ELIGIBLE": bool(evidence["cycles"]["complete_cycles"]), "ZERO_ELIGIBLE": bool(legacy.get("retained_fraction") is not None and legacy["retained_fraction"] >= 0.70), "RETURN_FACTOR_ELIGIBLE": False, "maximum_excursion_rad": maximum_excursion, "maximum_excursion_sigma": maximum_excursion / max(excursion_sigma, 1e-12)}
        chains[key] = summary; signals[key] = signal; evidence_by_chain[key] = evidence
        prefix = key.replace(":", "__")
        domain_baseline_mask = np.isin(rows, baseline_rows)
        array_payload.update({f"{prefix}__domain_rows": rows, f"{prefix}__global_time_ns": timeline.time_ns[rows], f"{prefix}__activity_increment_rad": signal["activity"]["increment_rad"][rows], f"{prefix}__activity_rate_rad_s": signal["activity"]["rate_rad_s"][rows], f"{prefix}__process_floor_rad_s": signal["activity"]["process_rate_floor_rad_s"][rows], f"{prefix}__activity_z": signal["activity_z"][rows], f"{prefix}__valid_mask": signal["activity"]["valid"][rows].astype(np.uint8), f"{prefix}__baseline_mask": domain_baseline_mask.astype(np.uint8), f"{prefix}__excursion_rad": signal["excursion_rad"][rows], f"{prefix}__smoothed_excursion_rad": signal["smoothed_excursion_rad"][rows], f"{prefix}__excursion_uncertainty_rad": signal["excursion_uncertainty_rad"][rows], f"{prefix}__absolute_q2_covariance_trace_rad2_audit_only": signal["absolute_q2_covariance_trace_rad2_audit_only"][rows]})
        plot_chain(plot_dir / f"{prefix}.png", spec, timeline, rows, signal, evidence)
    static = {action: static_plateau(timeline, rows_for(timeline, domains[action]), contract) for action in ("initial_still_attempt2", "t_pose")}
    required = [item for item in chains.values() if item["role"] == "PRIMARY"]
    q2_ok = timeline.accounting["all_nodes_valid_fraction"] >= 0.95
    normalizer_ok = all(item.get("status") == "AVAILABLE" and math.isfinite(float(item["baseline"]["activity_scale_rad_s"])) and float(item["baseline"]["activity_scale_rad_s"]) > 0 and not item["absolute_q2_covariance_used_in_denominator"] for item in required)
    motion_ok = all(item.get("ACTIVE_MOTION_EVIDENCE") == "PASS" for item in required) and all(item["REFERENCE_ZERO_RETURN_EVIDENCE"] == "PASS" for item in static.values())
    reversal_ok = all(item.get("CYCLE_TOPOLOGY_EVIDENCE") == "PASS" for item in required)
    arms = arm_association(chains["arms:left"]["cycle"]["complete_cycles"], chains["arms:right"]["cycle"]["complete_cycles"], timeline.time_ns, contract)
    elbow = {side: phase_groups_from_cycle_vectors(chains[key]["cycle"]["complete_cycles"], float(contract["phase_association"]["minimum_axis_cluster_separation_deg"])) for side, key in (("left", "left_elbow:elbow_L"), ("right", "right_elbow_attempt2:elbow_R"))}
    trunk = phase_groups_from_cycle_vectors(chains["trunk:trunk"]["cycle"]["complete_cycles"], float(contract["phase_association"]["minimum_axis_cluster_separation_deg"]))
    association_ok = arms["left_present"] and arms["right_present"] and arms["bilateral_present"] and all(len(value.get("groups", [])) >= 2 for value in elbow.values()) and len(trunk.get("groups", [])) >= 3
    if not q2_ok: terminal = "FAIL_Q2_OR_TIMESTAMP_INPUT_QUALITY"
    elif not normalizer_ok: terminal = "FAIL_ACTIVITY_UNCERTAINTY_MODEL_INVALID"
    elif not motion_ok: terminal = "FAIL_REQUIRED_MOTION_EVIDENCE_MISSING"
    elif not reversal_ok: terminal = "FAIL_REQUIRED_REVERSAL_EVIDENCE_MISSING"
    elif not association_ok: terminal = "FAIL_ACTION_CHAIN_ASSOCIATION_AMBIGUOUS"
    else: terminal = "PASS_R3C_SIGNAL_DERIVED_MOTION_EVIDENCE"
    phase_association = {"arms": arms, "elbows": elbow, "trunk": trunk}
    factor_rows = [{"ACTION": item["action"], "CHAIN": key, "ROLE": item["role"], "ACTIVE_ROWS": len(item.get("active_rows", [])), "CYCLE_COUNT": len(item.get("cycle", {}).get("complete_cycles", [])), "FUNCTIONAL_AXIS_ELIGIBLE": item.get("FUNCTIONAL_AXIS_FACTOR_ELIGIBILITY") == "ELIGIBLE", "SIGN_ELIGIBLE": bool(item.get("SIGN_ELIGIBLE", False)), "ZERO_ELIGIBLE": bool(item.get("ZERO_ELIGIBLE", False)), "RETURN_FACTOR_ELIGIBLE": bool(item.get("RETURN_FACTOR_ELIGIBLE", False)), "FAIL_REASON": None if item.get("FUNCTIONAL_AXIS_FACTOR_ELIGIBILITY") == "ELIGIBLE" else item.get("status", "INSUFFICIENT_EVIDENCE")} for key, item in chains.items()]
    savez_deterministic(args.output / "R3C_CHAIN_ACTIVITY_ARRAYS.npz", array_payload)
    dump(args.output / "R3C_ACTION_MOTION_CYCLE_TIMELINE.json", {"schema": "biospur-r3c-action-motion-cycle-timeline-v1", "static": static, "chains": chains, "phase_association": phase_association, "terminal_outcome": terminal})
    dump(args.output / "R3C_ONSET_OFFSET_EXTREMA_CANDIDATES.json", {"schema": "biospur-r3c-candidate-diagnostics-v1", "chains": {key: item.get("candidate_diagnostics", {}) for key, item in chains.items()}})
    dump(args.output / "R3C_ACTIVITY_SCALE_COMPONENTS.json", {"schema": "biospur-r3c-formal-scale-components-v1", "chains": {key: item.get("activity_scale_components") for key, item in chains.items()}})
    dump(args.output / "R3C_FACTOR_ELIGIBILITY_MATRIX.json", {"schema": "biospur-r3c-factor-eligibility-v1", "rows": factor_rows, "D0_READY_FOR_SEPARATE_AUTHORIZATION": terminal.startswith("PASS_")})
    dump(args.output / "R3C_DATA_ACCESS_AUDIT.json", {"opened": ["CALIBRATION_ONLY_Q2_CACHE", "ELEVEN_CALIBRATION_BROAD_WINDOWS", "R2_R3_R3A_R3B_DERIVED_EVIDENCE", "FROZEN_MAPPING"], "forbidden_opened": [], "q2_cache_sha256": sha256(cache), "formal_r3c_run_count": 1, "FINAL_STILL": "SEALED", "WALK": "SEALED", "GOLF": "SEALED", "BOXING": "SEALED", "UWB": "SEALED", "operator_measurements": "SEALED", "D0": "NOT_STARTED", "JACOBIAN": "NOT_STARTED", "SOLVER": "NOT_STARTED", "FREEZE": "NOT_CREATED", "REPLAY": "NOT_STARTED", "RENDER": "NOT_STARTED", "COMMIT_PUSH": "NOT_PERFORMED"})
    result = {"schema": "biospur-r3c-formal-result-v1", "terminal_outcome": terminal, "pass": terminal.startswith("PASS_"), "D0_READY_FOR_SEPARATE_AUTHORIZATION": terminal.startswith("PASS_"), "prerequisites": prerequisites, "all_19_chains_evaluated": len(chains) == 19, "formal_r3c_run_count": 1, "D0": "NOT_STARTED", "JACOBIAN": "NOT_STARTED", "SOLVER": "NOT_STARTED", "FREEZE": "NOT_CREATED", "REPLAY": "NOT_STARTED", "RENDER": "NOT_STARTED", "FINAL_STILL": "SEALED", "GOLF": "SEALED", "BOXING": "SEALED", "WALK": "SEALED", "UWB": "SEALED", "COMMIT_PUSH": "NOT_PERFORMED"}
    dump(args.output / "RESULT.json", result)
    (args.output / "REPORT.md").write_text(f"# Revision D D−1 R3C formal motion evidence\n\n`{terminal}`\n\nThe R3B absolute-pose covariance normalizer is not used. All 19 chains were evaluated once with a same-signal quiet empirical scale and the frozen 0.035 rad/s process-noise floor. Active motion, reversal topology, reference quality, and factor eligibility remain separate.\n\n`D0_READY_FOR_SEPARATE_AUTHORIZATION = {str(result['D0_READY_FOR_SEPARATE_AUTHORIZATION']).lower()}`\n\n`FINAL_STILL = SEALED`\n")
    manifest(args.output)
    return result


def add_real_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--phase-a", type=Path, required=True); parser.add_argument("--r2", type=Path, required=True); parser.add_argument("--legacy-gates", type=Path, required=True)


def main() -> int:
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    item = sub.add_parser("freeze"); item.add_argument("--contract", type=Path, required=True); item.add_argument("--chain-map", type=Path, required=True); item.add_argument("--q2-gates", type=Path, required=True); item.add_argument("--r3c0", type=Path, required=True); item.add_argument("--output", type=Path, required=True)
    item = sub.add_parser("synthetic"); item.add_argument("--freeze", type=Path, required=True); item.add_argument("--output", type=Path, required=True)
    item = sub.add_parser("observation"); item.add_argument("--freeze", type=Path, required=True); item.add_argument("--synthetic", type=Path, required=True); add_real_arguments(item); item.add_argument("--output", type=Path, required=True)
    item = sub.add_parser("formal"); item.add_argument("--freeze", type=Path, required=True); item.add_argument("--synthetic", type=Path, required=True); item.add_argument("--observation", type=Path, required=True); item.add_argument("--r3c0", type=Path, required=True); add_real_arguments(item); item.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = freeze(args) if args.command == "freeze" else synthetic(args) if args.command == "synthetic" else observation(args) if args.command == "observation" else formal(args)
    print(json.dumps(clean(result), sort_keys=True))
    return 0 if result.get("pass", True) else 2


if __name__ == "__main__": raise SystemExit(main())
