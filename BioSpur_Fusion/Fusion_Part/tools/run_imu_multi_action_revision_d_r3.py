#!/usr/bin/env python3
"""Qualify and execute D-1 R3 cycle topology; never enters D0."""
from __future__ import annotations

import argparse
import hashlib
import json
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
from biospur_fusion.imu_multi_action_revision_d.r3_cycle import (
    _runs,
    associate_bilateral_cycles,
    build_excursion_signal,
    detect_cycles,
    relative_orientation,
    relative_rate_signal,
    run_r3_synthetic_qualification,
    select_pre_reference,
)


BASELINE = "7c659b24b714b1ef4d9143658d1a6ee49ffb92ce"
ACTIONS = (
    "initial_still_attempt2", "t_pose", "arms", "left_elbow",
    "right_elbow_attempt2", "left_knee", "right_knee", "left_heel",
    "right_heel", "squats", "trunk",
)
SOURCE = ROOT / "Fusion_Part/src/biospur_fusion/imu_multi_action_revision_d/r3_cycle.py"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")


def current_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def write_manifest(output: Path) -> None:
    dump(output / "SHA256_MANIFEST.json", {
        str(path.relative_to(output)): sha256(path)
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "SHA256_MANIFEST.json"
    })


def qualify(contract_path: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    if current_head() != BASELINE:
        raise RuntimeError(f"HEAD must remain {BASELINE}")
    contract = json.loads(contract_path.read_text())
    output.mkdir(parents=True)
    shutil.copyfile(contract_path, output / "R3_CYCLE_DEFINITION.json")
    result = run_r3_synthetic_qualification(contract)
    dump(output / "R3_SYNTHETIC_CYCLE_QUALIFICATION.json", result)
    freeze = {
        "schema": "biospur-revision-d-minus-1-r3-synthetic-freeze-v1",
        "pass": bool(result["pass"]),
        "terminal_outcome": result["terminal_outcome"],
        "baseline_commit": BASELINE,
        "contract_sha256": sha256(output / "R3_CYCLE_DEFINITION.json"),
        "r3_cycle_source_absolute_path": str(SOURCE.resolve()),
        "r3_cycle_source_sha256": sha256(SOURCE),
        "real_capture_accessed": False,
        "FINAL_STILL_STATUS": "SEALED",
        "D0_AUTHORIZED": False,
    }
    dump(output / "R3_SYNTHETIC_FREEZE.json", freeze)
    write_manifest(output)
    return freeze


def _post_neutral(signal: Mapping[str, Any], domain_rows: np.ndarray, after_row: int, time_ns: np.ndarray, config: Mapping[str, Any]) -> dict[str, Any]:
    hz = float(config["signal"]["rate_hz"])
    minimum = max(1, round(float(config["post_neutral"]["minimum_quiet_duration_s"]) * hz))
    rows = domain_rows[domain_rows >= int(after_row)]
    snr = np.asarray(signal["relative_rate_snr"], float)
    valid = np.asarray(signal["relative_rate_valid"], bool)
    mask = valid[rows] & np.isfinite(snr[rows]) & (snr[rows] <= float(config["post_neutral"]["maximum_relative_rate_snr"]))
    candidates = [(a, b) for a, b in _runs(mask) if b - a >= minimum]
    if not candidates:
        return {"POST_NEUTRAL_AVAILABLE": False, "RETURN_TO_NEUTRAL_FACTOR": "UNAVAILABLE", "quiet_range": None}
    a, b = candidates[0]; selected = rows[a:b]
    return {
        "POST_NEUTRAL_AVAILABLE": True,
        "RETURN_TO_NEUTRAL_FACTOR": "AVAILABLE_AUXILIARY_ONLY",
        "quiet_range": {
            "start_row": int(selected[0]), "stop_row_exclusive": int(selected[-1] + 1),
            "start_global_time_ns": int(time_ns[selected[0]]), "stop_global_time_ns": int(time_ns[selected[-1]]),
            "duration_s": float(len(selected) / hz),
        },
        "exact_pre_pose_return_required": False,
    }


def _decorate_detection(detection: Mapping[str, Any], time_ns: np.ndarray) -> dict[str, Any]:
    def decorate(item: Mapping[str, Any]) -> dict[str, Any]:
        out = dict(item)
        for key in ("start_row", "peak_row"):
            if key in out:
                out[key.replace("row", "global_time_ns")] = int(time_ns[int(out[key])])
        if "stop_row_exclusive" in out:
            stop = max(int(out["start_row"]), int(out["stop_row_exclusive"]) - 1)
            out["stop_global_time_ns"] = int(time_ns[stop])
        return out
    return {
        "ACTIVE_BOUT_VALID": bool(detection["ACTIVE_BOUT_VALID"]),
        "CYCLE_TOPOLOGY_VALID": bool(detection["CYCLE_TOPOLOGY_VALID"]),
        "detected_repetition_count": int(detection["detected_repetition_count"]),
        "complete_cycles": [decorate(x) for x in detection["complete_cycles"]],
        "partial_repetitions": [decorate(x) for x in detection["partial_repetitions"]],
        "rejected_candidates": [decorate(x) for x in detection["rejected_candidates"]],
        "active_bouts": [decorate(x) for x in detection["active_bouts"]],
        "invalid_row_count": int(detection["invalid_row_count"]),
    }


def _analyze_chain(
    timeline: Any,
    segment_index: Mapping[str, int],
    parent: str,
    child: str,
    domain_ns: tuple[int, int],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    rows = np.flatnonzero((timeline.time_ns >= int(domain_ns[0])) & (timeline.time_ns <= int(domain_ns[1])))
    p, c = segment_index[parent], segment_index[child]
    relative = relative_orientation(timeline.rotation[:, p], timeline.rotation[:, c])
    valid = timeline.valid[:, p] & timeline.valid[:, c]
    covariance = timeline.covariance_rad2[:, p] + timeline.covariance_rad2[:, c]
    rate = relative_rate_signal(timeline.time_ns, relative, covariance, valid, config)
    reference = select_pre_reference(rate["snr"], rate["valid"], rows, config)
    if reference is None:
        return {
            "parent": parent, "child": child, "status": "FAIL_PRE_ACTION_REFERENCE_MISSING",
            "ACTIVE_BOUT_VALID": False, "CYCLE_TOPOLOGY_VALID": False,
            "detected_repetition_count": 0, "POST_NEUTRAL_AVAILABLE": False,
        }, None, None
    signal = build_excursion_signal(
        timeline.time_ns, timeline.rotation[:, p], timeline.rotation[:, c],
        timeline.covariance_rad2[:, p], timeline.covariance_rad2[:, c],
        timeline.valid[:, p], timeline.valid[:, c], reference["row_indices"], config,
    )
    detection = detect_cycles(signal, rows, config)
    after = max(
        [reference["stop_row_exclusive"]]
        + [x["stop_row_exclusive"] for x in detection["active_bouts"]]
        + [x["stop_row_exclusive"] for x in detection["complete_cycles"]]
        + [x["stop_row_exclusive"] for x in detection["partial_repetitions"]]
    )
    post = _post_neutral(signal, rows, after, timeline.time_ns, config)
    decorated = _decorate_detection(detection, timeline.time_ns)
    max_excursion = float(np.nanmax(np.asarray(signal["smoothed_excursion_rad"])[rows]))
    median_sigma = float(np.nanmedian(np.asarray(signal["uncertainty_rad"])[rows]))
    evidence = config["parameter_evidence"]
    functional = max_excursion >= float(evidence["functional_subspace_minimum_excursion_rad"])
    functional &= max_excursion / max(median_sigma, 1e-12) >= float(evidence["functional_subspace_minimum_excursion_sigma"])
    record = {
        "parent": parent, "child": child, "status": "ANALYZED_SIGNAL_ONLY",
        "primary_coordinate": "SO3_GEODESIC_EXCURSION_FROM_ROBUST_PRE_RELATIVE_POSE",
        "reference": {
            **reference,
            "start_global_time_ns": int(timeline.time_ns[reference["start_row"]]),
            "stop_global_time_ns": int(timeline.time_ns[reference["stop_row_exclusive"] - 1]),
            "reference_sigma_rad": float(signal["reference_sigma_rad"]),
            "retained_fraction": float(signal["reference_retained_fraction"]),
        },
        **decorated,
        **post,
        "maximum_excursion_rad": max_excursion,
        "maximum_excursion_deg": float(np.degrees(max_excursion)),
        "median_uncertainty_rad": median_sigma,
        "maximum_excursion_sigma": max_excursion / max(median_sigma, 1e-12),
        "sufficient_excitation_for_functional_subspace_or_axis": bool(functional),
        "sufficient_reversal_evidence_for_sign": bool(detection["complete_cycles"]),
        "sufficient_reference_evidence_for_joint_zero": "INITIAL_STILL_AND_TPOSE_AVAILABLE_NOT_EXACT_ACTION_RETURN",
        "quiet_required_for_cycle": False,
        "exact_pre_pose_return_required_for_cycle": False,
        "post_neutral_required_for_cycle": False,
    }
    plot_data = {"rows": rows, "signal": signal, "detection": detection, "post": post, "label": f"{parent}→{child}"}
    return record, plot_data, detection


def _render_dynamic_plot(path: Path, action: str, chains: list[dict[str, Any]], timeline: Any, post_summary: str) -> None:
    colors = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd"]
    fig, (ax, dx) = plt.subplots(2, 1, figsize=(14, 8), sharex=True, constrained_layout=True)
    origin = min(int(timeline.time_ns[x["rows"][0]]) for x in chains)
    for ordinal, item in enumerate(chains):
        color = colors[ordinal % len(colors)]; rows = item["rows"]; signal = item["signal"]
        t = (timeline.time_ns[rows] - origin) / 1e9
        q = np.asarray(signal["smoothed_excursion_rad"])[rows]
        u = np.asarray(signal["uncertainty_rad"])[rows]
        derivative = np.asarray(signal["derivative_rad_s"])[rows]
        ax.plot(t, q, color=color, linewidth=1.4, label=item["label"])
        ax.fill_between(t, np.maximum(0, q-u), q+u, color=color, alpha=.15)
        dx.plot(t, derivative, color=color, linewidth=1.0, label=item["label"])
        for cycle in item["detection"]["complete_cycles"]:
            a = (timeline.time_ns[cycle["start_row"]] - origin) / 1e9
            p = (timeline.time_ns[cycle["peak_row"]] - origin) / 1e9
            b = (timeline.time_ns[cycle["stop_row_exclusive"] - 1] - origin) / 1e9
            ax.axvspan(a, b, color=color, alpha=.08); ax.plot(p, q[np.searchsorted(rows, cycle["peak_row"])], "o", color=color)
        for cycle in item["detection"]["partial_repetitions"]:
            p = (timeline.time_ns[cycle["peak_row"]] - origin) / 1e9
            ax.axvline(p, color="#ff7f0e", linestyle="--", alpha=.8)
        for bout in item["detection"]["active_bouts"]:
            ax.axvline((timeline.time_ns[bout["start_row"]]-origin)/1e9, color=color, linestyle=":", alpha=.6)
            ax.axvline((timeline.time_ns[bout["stop_row_exclusive"]-1]-origin)/1e9, color=color, linestyle=":", alpha=.6)
        quiet = item["post"].get("quiet_range")
        if quiet:
            ax.axvspan((quiet["start_global_time_ns"]-origin)/1e9, (quiet["stop_global_time_ns"]-origin)/1e9, color="#17becf", alpha=.12)
    ax.set(title=f"D−1 R3 signal-only topology — {action}\n{post_summary}", ylabel="relative excursion (rad)")
    dx.set(xlabel="seconds from plotted domain start", ylabel="excursion derivative (rad/s)")
    ax.legend(loc="upper right", fontsize=8); dx.legend(loc="upper right", fontsize=8)
    ax.grid(alpha=.2); dx.grid(alpha=.2)
    fig.savefig(path, dpi=150); plt.close(fig)


def _render_static_plot(path: Path, action: str, r2_action: Mapping[str, Any]) -> None:
    signal = r2_action["timeline_signal"]
    t = (np.asarray(signal["global_time_ns"], np.int64) - int(signal["global_time_ns"][0])) / 1e9
    activity = np.asarray(signal["activity_snr"], float)
    fig, ax = plt.subplots(figsize=(14, 5), constrained_layout=True)
    ax.plot(t, activity, color="black")
    for phase in r2_action["phases"]:
        a=(phase["start_global_time_ns"]-signal["global_time_ns"][0])/1e9
        b=(phase["stop_global_time_ns"]-signal["global_time_ns"][0])/1e9
        ax.axvspan(a,b,color="#4daf4a",alpha=.15)
    ax.set(title=f"D−1 R3 — {action}: static reference, cycle topology not applicable", xlabel="seconds", ylabel="activity SNR")
    ax.grid(alpha=.2); fig.savefig(path,dpi=150); plt.close(fig)


def _build_factor_eligibility(
    arms: Mapping[str, Any], heels: Mapping[str, Any], r2_requirements: Mapping[str, Any], static_available: bool,
) -> dict[str, Any]:
    rows = []
    for name, chain in (("shoulder_L", arms["left_primary"]), ("shoulder_R", arms["right_primary"]), ("knee_L", heels["left"]), ("knee_R", heels["right"])):
        rows.append({
            "parameter_block": name,
            "source": "R3_SIGNAL_ONLY_PRIMARY_CHAIN",
            "sufficient_excitation_for_functional_subspace_or_axis": chain["sufficient_excitation_for_functional_subspace_or_axis"],
            "sufficient_reversal_evidence_for_sign": chain["sufficient_reversal_evidence_for_sign"],
            "sufficient_reference_evidence_for_joint_zero": static_available,
            "detected_repetition_count": chain["detected_repetition_count"],
            "post_neutral_available": chain["POST_NEUTRAL_AVAILABLE"],
            "functional_axis_factor_eligible": chain["sufficient_excitation_for_functional_subspace_or_axis"],
            "sign_factor_eligible": chain["sufficient_reversal_evidence_for_sign"],
            "return_to_neutral_factor": chain["RETURN_TO_NEUTRAL_FACTOR"],
        })
    return {
        "schema": "biospur-revision-d-minus-1-r3-factor-eligibility-v1",
        "targeted_parameter_blocks": rows,
        "targeted_required_parameter_evidence_present": all(x["functional_axis_factor_eligible"] and x["sign_factor_eligible"] and x["sufficient_reference_evidence_for_joint_zero"] for x in rows),
        "non_target_r2_requirements_preserved_read_only": r2_requirements,
        "exact_repetition_count_is_qc_only": True,
        "missing_post_neutral_disables_only_return_factor": True,
        "D0_AUTHORIZED": False,
    }


def run_real(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output
    if output.exists():
        raise FileExistsError(output)
    if current_head() != BASELINE:
        raise RuntimeError(f"HEAD must remain {BASELINE}")
    freeze = json.loads((args.qualification / "R3_SYNTHETIC_FREEZE.json").read_text())
    synthetic = json.loads((args.qualification / "R3_SYNTHETIC_CYCLE_QUALIFICATION.json").read_text())
    if not freeze["pass"] or not synthetic["pass"]:
        raise RuntimeError("R3 synthetic qualification did not pass")
    if sha256(args.qualification / "R3_CYCLE_DEFINITION.json") != freeze["contract_sha256"]:
        raise RuntimeError("R3 contract changed after synthetic freeze")
    if sha256(SOURCE) != freeze["r3_cycle_source_sha256"]:
        raise RuntimeError("R3 cycle source changed after synthetic freeze")
    phase = json.loads((args.phase_a / "RESULT.json").read_text())
    if not phase["pass"] or tuple(sorted(phase["calibration_windows"], key=lambda x: phase["calibration_windows"][x][0])) != ACTIONS:
        raise RuntimeError("qualified eleven-action calibration-only Phase-A binding failed")
    if phase["final_still"] != "SEALED" or phase["data_access"]["final_still"] != "SEALED_NOT_OPENED":
        raise RuntimeError("final_still firewall binding failed")
    cache = args.phase_a / "Q2_HUMAN_QUASI_STATIC_CACHE.npz"
    if sha256(cache) != phase["q2_cache_sha256"]:
        raise RuntimeError("Q2 cache SHA mismatch")
    r2 = json.loads((args.r2 / "ACTION_PHASE_TIMELINE.json").read_text())
    r2_result = json.loads((args.r2 / "RESULT.json").read_text())
    if sha256(args.r2 / "ACTION_PHASE_TIMELINE.json") != r2_result["action_phase_timeline_sha256"]:
        raise RuntimeError("immutable R2 timeline SHA mismatch")
    audit_matrix = json.loads((args.r2_audit / "ACTION_BOUT_CYCLE_NEUTRAL_MATRIX.json").read_text())
    r2_requirements = json.loads((args.r2_audit / "RESIDUAL_EVIDENCE_REQUIREMENTS.json").read_text())
    config = json.loads((args.qualification / "R3_CYCLE_DEFINITION.json").read_text())
    gates = json.loads(args.legacy_gates.read_text())
    q2 = load_q2_cache(cache)
    start = min(int(x["search_domain_ns"][0]) for x in r2["actions"].values())
    stop = max(int(x["search_domain_ns"][1]) for x in r2["actions"].values())
    timeline = build_common_timeline(q2, start, stop, gates["common_time"])
    nodes = {node: i for i, node in enumerate(timeline.node_order)}
    segment_index = {segment: nodes[node] for node, segment in gates["node_to_segment"].items()}
    domains = {name: tuple(r2["actions"][name]["search_domain_ns"]) for name in ACTIONS}
    analyses: dict[str, tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]] = {}
    chain_specs = {
        "shoulder_L": ("torso", "upper_arm_L", "arms"), "shoulder_R": ("torso", "upper_arm_R", "arms"),
        "elbow_L": ("upper_arm_L", "forearm_L", "left_elbow"), "elbow_R": ("upper_arm_R", "forearm_R", "right_elbow_attempt2"),
        "hip_L": ("pelvis", "thigh_L", "left_knee"), "hip_R": ("pelvis", "thigh_R", "right_knee"),
        "knee_L": ("thigh_L", "shank_L", "left_heel"), "knee_R": ("thigh_R", "shank_R", "right_heel"),
        "squat_hip_L": ("pelvis", "thigh_L", "squats"), "squat_hip_R": ("pelvis", "thigh_R", "squats"),
        "squat_knee_L": ("thigh_L", "shank_L", "squats"), "squat_knee_R": ("thigh_R", "shank_R", "squats"),
        "trunk": ("pelvis", "torso", "trunk"),
    }
    for name, (parent, child, action) in chain_specs.items():
        analyses[name] = _analyze_chain(timeline, segment_index, parent, child, domains[action], config)
    shoulder_l, shoulder_r = analyses["shoulder_L"][0], analyses["shoulder_R"][0]
    knee_l, knee_r = analyses["knee_L"][0], analyses["knee_R"][0]
    left_cycles = analyses["shoulder_L"][2]["complete_cycles"] if analyses["shoulder_L"][2] else []
    right_cycles = analyses["shoulder_R"][2]["complete_cycles"] if analyses["shoulder_R"][2] else []
    association = associate_bilateral_cycles(left_cycles, right_cycles, timeline.time_ns, config)
    arms = {
        "schema": "biospur-revision-d-minus-1-r3-arms-chain-selection-audit-v1",
        "left_primary": shoulder_l, "right_primary": shoulder_r,
        "bilateral_association": association,
        "forearm_diagnostics_not_primary": {"left": analyses["elbow_L"][0], "right": analyses["elbow_R"][0]},
        "primary_chain_correction": "R2_AGGREGATE_INCLUDED_FOREARMS_AND_CLASSIFIED_RIGHT_PHASES_WITH_LEFT_REFERENCE;R3_USES_INDEPENDENT_TORSO_TO_UPPER_ARM_SIGNALS",
        "protocol_order_is_soft_qc": True,
        "CYCLE_TOPOLOGY_VALID": bool(association["all_required_classes_present"]),
    }
    heels = {
        "schema": "biospur-revision-d-minus-1-r3-heel-cycle-topology-audit-v1",
        "left": knee_l, "right": knee_r,
        "thigh_compensation_is_nuisance_not_failure": True,
        "foot_trajectory_required": False,
        "CYCLE_TOPOLOGY_VALID": bool(knee_l["CYCLE_TOPOLOGY_VALID"] and knee_r["CYCLE_TOPOLOGY_VALID"]),
    }
    static_available = bool(r2["actions"]["initial_still_attempt2"]["phases"] and r2["actions"]["t_pose"]["phases"])
    factor = _build_factor_eligibility(arms, heels, r2_requirements, static_available)
    matrix = {"schema": "biospur-revision-d-minus-1-r3-action-bout-cycle-neutral-matrix-v1", "actions": {}}
    for action in ACTIONS:
        old = audit_matrix["actions"][action]
        matrix["actions"][action] = {
            "source": "R2_READ_ONLY_DECOMPOSITION_UNCHANGED",
            "ACTIVE_BOUT_VALID": old["CONTIGUOUS_BOUT_STATUS"].startswith("PASS"),
            "CYCLE_TOPOLOGY_VALID": None if old["COMPLETE_CYCLE_COUNT"] is None else old["COMPLETE_CYCLE_COUNT"] > 0,
            "detected_repetition_count": old["COMPLETE_CYCLE_COUNT"],
            "POST_NEUTRAL_AVAILABLE": old["POST_ACTION_QUIET_PLATEAU_STATUS"].startswith("PASS"),
            "exact_count_is_qc_only": True,
        }
    matrix["actions"]["arms"] = {
        "source": "R3_SIGNAL_ONLY_CORRECTED_PRIMARY_CHAINS",
        "ACTIVE_BOUT_VALID": bool(shoulder_l["ACTIVE_BOUT_VALID"] and shoulder_r["ACTIVE_BOUT_VALID"]),
        "CYCLE_TOPOLOGY_VALID": bool(arms["CYCLE_TOPOLOGY_VALID"]),
        "detected_repetition_count": {"left_primary": shoulder_l["detected_repetition_count"], "right_primary": shoulder_r["detected_repetition_count"], "bilateral_pairs": len(association["pairs"])},
        "detected_classes": association["chronological_block_classes"],
        "POST_NEUTRAL_AVAILABLE": bool(shoulder_l["POST_NEUTRAL_AVAILABLE"] and shoulder_r["POST_NEUTRAL_AVAILABLE"]),
        "exact_count_is_qc_only": True,
    }
    for action, chain in (("left_heel", knee_l), ("right_heel", knee_r)):
        matrix["actions"][action] = {
            "source": "R3_SIGNAL_ONLY_CORRECTED_THIGH_TO_SHANK_CHAIN",
            "ACTIVE_BOUT_VALID": bool(chain["ACTIVE_BOUT_VALID"]),
            "CYCLE_TOPOLOGY_VALID": bool(chain["CYCLE_TOPOLOGY_VALID"]),
            "detected_repetition_count": chain["detected_repetition_count"],
            "POST_NEUTRAL_AVAILABLE": bool(chain["POST_NEUTRAL_AVAILABLE"]),
            "exact_count_is_qc_only": True,
        }
    targeted_active = matrix["actions"]["arms"]["ACTIVE_BOUT_VALID"] and matrix["actions"]["left_heel"]["ACTIVE_BOUT_VALID"] and matrix["actions"]["right_heel"]["ACTIVE_BOUT_VALID"]
    targeted_cycles = matrix["actions"]["arms"]["CYCLE_TOPOLOGY_VALID"] and matrix["actions"]["left_heel"]["CYCLE_TOPOLOGY_VALID"] and matrix["actions"]["right_heel"]["CYCLE_TOPOLOGY_VALID"]
    if not targeted_active:
        terminal = "FAIL_REQUIRED_ACTIVE_BOUT_AMBIGUOUS"
    elif not targeted_cycles:
        terminal = "FAIL_REQUIRED_CYCLE_TOPOLOGY"
    elif not factor["targeted_required_parameter_evidence_present"]:
        terminal = "FAIL_REQUIRED_PARAMETER_EVIDENCE_MISSING"
    else:
        terminal = "PASS_ACTIVE_BOUTS_AND_REQUIRED_CYCLE_TOPOLOGY_WITH_PARTIAL_POST_NEUTRAL"
    output.mkdir(parents=True)
    shutil.copyfile(args.qualification / "R3_CYCLE_DEFINITION.json", output / "R3_CYCLE_DEFINITION.json")
    shutil.copyfile(args.qualification / "R3_SYNTHETIC_CYCLE_QUALIFICATION.json", output / "R3_SYNTHETIC_CYCLE_QUALIFICATION.json")
    dump(output / "R3_ACTION_BOUT_CYCLE_NEUTRAL_MATRIX.json", matrix)
    dump(output / "R3_ARMS_CHAIN_SELECTION_AUDIT.json", arms)
    dump(output / "R3_HEEL_CYCLE_TOPOLOGY_AUDIT.json", heels)
    dump(output / "R3_FACTOR_ELIGIBILITY.json", factor)
    comparison = {
        "schema": "biospur-revision-d-minus-1-r2-vs-r3-comparison-v1",
        "R2_DISPOSITION": "FAIL_IMMUTABLE_UNDER_ORIGINAL_CONTRACT",
        "R2_FAILURE_DECOMPOSITION": "IMMUTABLE",
        "changed_targets": {
            "arms": {"r2_cycles": old_count(audit_matrix, "arms"), "r3": matrix["actions"]["arms"]["detected_repetition_count"]},
            "left_heel": {"r2_cycles": old_count(audit_matrix, "left_heel"), "r3_cycles": knee_l["detected_repetition_count"]},
            "right_heel": {"r2_cycles": old_count(audit_matrix, "right_heel"), "r3_cycles": knee_r["detected_repetition_count"]},
        },
        "change_cause": "PREDECLARED_SIGNAL_ONLY_AMPLITUDE_RELATIVE_CYCLE_SEMANTICS;NO_RESULT_DEPENDENT_THRESHOLD_CHANGE",
        "r2_artifacts_modified": False,
    }
    dump(output / "R2_VS_R3_COMPARISON.json", comparison)
    data_access = {
        "schema": "biospur-revision-d-minus-1-r3-data-access-audit-v1",
        "opened": ["QUALIFIED_CALIBRATION_ONLY_Q2_CACHE", "ELEVEN_CALIBRATION_ACTION_WINDOWS", "IMMUTABLE_R2_DERIVED_EVIDENCE"],
        "phase_a_result_sha256": sha256(args.phase_a / "RESULT.json"), "q2_cache_sha256": sha256(cache),
        "r2_timeline_sha256": sha256(args.r2 / "ACTION_PHASE_TIMELINE.json"),
        "forbidden_opened": [], "FINAL_STILL_STATUS": "SEALED", "final_still_samples_accessed": False,
        "golf": "SEALED", "boxing": "SEALED", "walk": "SEALED", "UWB_T4_Anchor": "SEALED", "operator_measurements": "SEALED",
        "D0_entered": False, "jacobian_computed": False, "solver_started": False, "replay_started": False, "render_started": False,
    }
    dump(output / "DATA_ACCESS_AUDIT.json", data_access)
    plot_dir = output / "per_action_plots"; plot_dir.mkdir()
    _render_static_plot(plot_dir / "initial_still_attempt2.png", "initial_still_attempt2", r2["actions"]["initial_still_attempt2"])
    _render_static_plot(plot_dir / "t_pose.png", "t_pose", r2["actions"]["t_pose"])
    action_plot_chains = {
        "arms": [analyses["shoulder_L"][1], analyses["shoulder_R"][1]],
        "left_elbow": [analyses["elbow_L"][1]], "right_elbow_attempt2": [analyses["elbow_R"][1]],
        "left_knee": [analyses["hip_L"][1]], "right_knee": [analyses["hip_R"][1]],
        "left_heel": [analyses["knee_L"][1]], "right_heel": [analyses["knee_R"][1]],
        "squats": [analyses[x][1] for x in ("squat_hip_L", "squat_hip_R", "squat_knee_L", "squat_knee_R")],
        "trunk": [analyses["trunk"][1]],
    }
    for action, items in action_plot_chains.items():
        usable = [x for x in items if x is not None]
        if usable:
            post = ", ".join(f"{x['label']} post-neutral={x['post']['POST_NEUTRAL_AVAILABLE']}" for x in usable)
            _render_dynamic_plot(plot_dir / f"{action}.png", action, usable, timeline, post)
        else:
            _render_static_plot(plot_dir / f"{action}.png", action, r2["actions"][action])
    report = f"""# D−1 R3 signal-only cycle topology\n\n`{terminal}`\n\nR2 remains `FAIL_IMMUTABLE_UNDER_ORIGINAL_CONTRACT`; its failure decomposition and artifacts were not modified. R3 synthetic qualification passed before real calibration-only execution. Arms use independent torso-to-upper-arm coordinates; heel actions use thigh-to-shank coordinates. Cycle completion uses amplitude-relative rise/extremum/reversal/recovery and does not require quiet, exact pose return, post-neutral, a fitted axis, or an exact repetition count.\n\nArms: left={shoulder_l['detected_repetition_count']}, right={shoulder_r['detected_repetition_count']}, bilateral associations={len(association['pairs'])}, detected blocks={association['chronological_block_classes']}. Heel cycles: left={knee_l['detected_repetition_count']}, right={knee_r['detected_repetition_count']}. Post-neutral is reported independently and only controls return-factor availability.\n\n`D0_READY_FOR_SEPARATE_AUTHORIZATION = {str(terminal.startswith('PASS_')).lower()}`\n\n`FINAL_STILL_STATUS = SEALED`\n\nNo D0 objective, Jacobian, solver, replay, calibration render, commit, or push was performed.\n"""
    (output / "REPORT.md").write_text(report)
    result = {
        "schema": "biospur-revision-d-minus-1-r3-result-v1", "terminal_outcome": terminal,
        "pass": terminal.startswith("PASS_"), "D0_READY_FOR_SEPARATE_AUTHORIZATION": terminal.startswith("PASS_"),
        "R2_DISPOSITION": "FAIL_IMMUTABLE_UNDER_ORIGINAL_CONTRACT", "R2_FAILURE_DECOMPOSITION": "IMMUTABLE",
        "FINAL_STILL_STATUS": "SEALED", "nonlinear_solver_started": False, "jacobian_computed": False,
        "D0_entered": False, "golf": "SEALED", "boxing": "SEALED", "walk": "SEALED", "uwb_t4_anchor": "SEALED", "operator_measurements": "SEALED",
    }
    dump(output / "RESULT.json", result)
    write_manifest(output)
    return result


def old_count(matrix: Mapping[str, Any], action: str) -> int | None:
    return matrix["actions"][action]["COMPLETE_CYCLE_COUNT"]


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    q = sub.add_parser("qualify")
    q.add_argument("--contract", type=Path, required=True); q.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--qualification", type=Path, required=True); run.add_argument("--phase-a", type=Path, required=True)
    run.add_argument("--legacy-gates", type=Path, required=True); run.add_argument("--r2", type=Path, required=True)
    run.add_argument("--r2-audit", type=Path, required=True); run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = qualify(args.contract, args.output) if args.command == "qualify" else run_real(args)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
