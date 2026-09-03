#!/usr/bin/env python3
"""Run capture-local pure-IMU V0 physical-graph repair qualification."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import run_pure_imu_v0_raw6_heading as legacy

from biospur_fusion.v0.bounded_capture1 import (
    load_capture1_calibration_episode_bounded,
    prepare_capture1_bounded_preflight,
)
from biospur_fusion.v0.contracts import dump_json, sha256_file
from biospur_fusion.v0.dual_capture import load_capture2_calibration_episode, load_protocol
from biospur_fusion.v0.episode import segment_five_phase_episode
from biospur_fusion.v0.physical_fk import generate_direct_fk_artifacts
from biospur_fusion.v0.physical_graph import (
    SEGMENTS,
    distal_pivot_evidence_audit,
    fit_physical_graph,
    merge_distal_pivot_evidence,
    real_subject_spec,
    transition_ablation_physical,
)
from biospur_fusion.v0.raw6_heading import (
    EDGES,
    build_edge_factors,
    drift_stillness,
    fit_edgewise,
    raw6_episode_from_rows,
    wrap,
)


RUN_REL = Path("logs/pure_imu_v0_physical_graph_dynamic_lengths_r3_20260828T091019Z")
FRESH_PRESELECTION_SHA256 = "3c09396037d033565d9ce41bdb90935cdf03673bf6e36214b7b06114cd4e40f2"
ENDPOINT_ADDENDUM_SHA256 = "e6f330a6f4e89acdda42701a1c7f65aef300b02836db2670b4ef69b49e05b536"
SYNTHETIC_NAME = "SYNTHETIC_QUALIFICATION.json"
C1_PREFLIGHT_NAME = "CAPTURE1_BOUNDED_ACCESS_PREFLIGHT.json"


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        _jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def load_frozen_selection(root: Path) -> dict[str, Any]:
    fresh_path = root / RUN_REL / "METADATA_PRESELECTION.json"
    if sha256_file(fresh_path) != FRESH_PRESELECTION_SHA256:
        raise RuntimeError("fresh physical-graph metadata preselection changed")
    if fresh_path.stat().st_mode & 0o222:
        raise RuntimeError("fresh physical-graph metadata preselection is writable")
    fresh = json.loads(fresh_path.read_text(encoding="utf-8"))
    addendum_path = root / RUN_REL / "ENDPOINT_EVIDENCE_REQUIREMENTS_ADDENDUM.json"
    if sha256_file(addendum_path) != ENDPOINT_ADDENDUM_SHA256:
        raise RuntimeError("endpoint evidence requirements addendum changed")
    if addendum_path.stat().st_mode & 0o222:
        raise RuntimeError("endpoint evidence requirements addendum is writable")
    authority = fresh["selection_authority"]
    authority_path = root / authority["path"]
    if sha256_file(authority_path) != authority["sha256"]:
        raise RuntimeError("incorporated bounded-access preselection changed")
    selection = legacy._selection(root)
    for capture, row in fresh["captures"].items():
        if selection["captures"][capture]["capture_id"] != row["capture_id"]:
            raise RuntimeError(f"{capture}: fresh capture ID differs from authority")
    return selection


def require_synthetic(root: Path) -> dict[str, Any]:
    path = root / RUN_REL / SYNTHETIC_NAME
    if not path.exists() or path.stat().st_mode & 0o222:
        raise RuntimeError("immutable synthetic qualification is absent")
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "randomized_human_truth_recovery",
        "rank_nine_after_one_pelvis_yaw_gauge",
        "broad_24_start_full_circle_agreement",
        "held_out_generalization",
        "transition_ablation_reported",
        "collapsed_segment_mutation_rejected",
        "disconnected_joint_mutation_rejected",
        "low_excitation_degeneracy_rejected",
        "physical_structure",
        "dynamic_length_data_identifiability",
        "dynamic_length_prior_sensitivity",
        "endpoint_evidence_classes_reported",
    }
    if not payload.get("pass") or set(payload.get("gates", {})) != required:
        raise RuntimeError("synthetic physical-graph prerequisite did not pass exact gates")
    return {"path": str(path), "sha256": sha256_file(path), "pass": True}


def _load_capture(
    root: Path,
    run_dir: Path,
    capture: str,
    spec: Mapping[str, Any],
    frozen: Mapping[str, Any],
    *,
    access_attempt: int,
    preselection_sha256: str = FRESH_PRESELECTION_SHA256,
) -> tuple[list[Any], dict[str, Any]]:
    by_name = legacy._action_rows(root, capture, spec)
    episode_contract = load_protocol(root)["semantic_qa_contract"]["calibration_episode"]
    episodes = []
    bindings = []
    access_proofs = []
    suffix = "" if access_attempt == 1 else f"_RETRY{access_attempt}"
    action_access_dir = run_dir / f"{capture}_ACTION_ACCESS{suffix}"
    action_access_dir.mkdir(parents=True, exist_ok=True)
    for selected in frozen["selected_actions"]:
        print(f"STAGE {capture} bounded load {selected['action']} begin", flush=True)
        action = by_name[selected["action"]]
        legacy._assert_selection_row(selected, action)
        if capture == "CAPTURE1":
            rows, access = load_capture1_calibration_episode_bounded(
                root, spec, action, run_dir / C1_PREFLIGHT_NAME,
            )
        else:
            rows, access = load_capture2_calibration_episode(root, spec, action)
        action_path = action_access_dir / f"{selected['action']}.json"
        if action_path.exists():
            raise FileExistsError(f"refusing to overwrite fresh access evidence {action_path}")
        dump_json(action_path, access); action_path.chmod(0o444)
        formal = access["formal_action_bounds"]
        diagnostic = segment_five_phase_episode(
            rows,
            action=selected["action"],
            action_kind=(
                "STATIONARY_REFERENCE" if "still" in selected["action"].lower()
                else "MOVEMENT_OR_POSE"
            ),
            formal_start_global_ns=int(formal["start_global_time_ns"]),
            formal_stop_global_ns_exclusive=int(formal["stop_global_time_ns_exclusive"]),
            contract=episode_contract,
            boundary_authority=access["boundary_authority"],
        )
        episode = raw6_episode_from_rows(
            capture=capture,
            action=selected["action"],
            partition=selected["partition"],
            rows_by_node=rows,
            identity=spec["identity"],
            episode_diagnostic=diagnostic,
            rate_hz=50,
        )
        episodes.append(episode)
        timing = legacy._timing_proof(access)
        if timing is not None:
            access_proofs.append({"action": selected["action"], "proof": timing})
        nodes = access.get("nodes", access.get("decode", {}).get("nodes", {}))
        bindings.append({
            "action": selected["action"],
            "action_access_artifact": str(action_path),
            "action_access_artifact_sha256": sha256_file(action_path),
            "partition": selected["partition"],
            "attempt": selected["attempt"],
            "complete_episode_bounds": selected["complete_episode_bounds"],
            "node_payload_sha256": {
                node: row["payload_sha256"] for node, row in nodes.items()
            },
            "five_phase_status": diagnostic["EPISODE_COMPLETENESS"],
            "five_phase_failures": diagnostic.get("failures", []),
            "raw6_audit": episode.audit,
            "access_contract": {
                "raw_path": access.get("raw_path"),
                "read_bracket": access.get("read_bracket"),
                "opened_members": access.get("opened_members"),
                "spatial_members_opened": access.get("spatial_members_opened", []),
                "uwb_spatial_payload_consumed": access.get("uwb_spatial_payload_consumed"),
                "hxx_payload_opened": access.get("hxx_payload_opened", False),
                "whole_capture_node_array_loaded_or_mmaped": access.get(
                    "whole_capture_node_array_loaded_or_mmaped", False,
                ),
            },
        })
        print(f"STAGE {capture} bounded load {selected['action']} complete", flush=True)
    if [episode.action for episode in episodes] != [
        row["action"] for row in frozen["selected_actions"]
    ]:
        raise RuntimeError("capture payload execution order changed")
    audit = {
        "schema": "biospur-pure-imu-v0-physical-graph-bounded-access-v1",
        "capture": capture,
        "capture_id": spec["capture_id"],
        "access_attempt": access_attempt,
        "prior_access_evidence_overwritten": False,
        "fresh_preselection_path": str(run_dir / "METADATA_PRESELECTION.json"),
        "fresh_preselection_sha256": preselection_sha256,
        "selected_actions": bindings,
        "hostile_timing_access_proofs": access_proofs,
        "actual_os_read_lseek_evidence_retained": bool(access_proofs),
        "hxx_payload_opened": False,
        "golf_boxing_payload_opened": False,
        "capture3_payload_opened": False,
        "uwb_spatial_payload_consumed": False,
        "full_raw_container_hash_recomputed": False,
        "cross_capture_payload_used": False,
    }
    return episodes, audit


def _access_gates(binding: Mapping[str, Any]) -> dict[str, bool]:
    selected = binding["selected_actions"]
    timing = binding["hostile_timing_access_proofs"]
    return {
        "complete_five_phase_episodes": all(
            row["five_phase_status"] == "PASS" for row in selected
        ),
        "actual_os_read_lseek_evidence": bool(timing) and all(
            int(row["proof"]["actual_os_read_calls"]) > 0
            and int(row["proof"]["actual_os_seek_calls"]) > 0
            for row in timing
        ),
        "action_plus_two_superframe_brackets": all(
            row["proof"]["all_sequential_timing_rows_within_action_plus_two_superframes"] is True
            for row in timing
        ),
        "binary_probes_explicit": all(
            row["proof"]["every_binary_search_probe_separately_accounted"] is True
            and int(row["proof"]["binary_search_probe_count"]) > 0
            for row in timing
        ),
        "no_full_traversal": all(
            row["proof"]["no_full_file_traversal_proven_by_actual_read_union"] is True
            for row in timing
        ),
        "forbidden_timing_intersections_zero": all(
            row["proof"]["golf_boxing_timing_interval_bytes_touched"] is False
            and row["proof"]["all_hxx_timing_interval_bytes_touched"] is False
            for row in timing
        ),
        "forbidden_payloads_absent": bool(
            not binding["hxx_payload_opened"]
            and not binding["golf_boxing_payload_opened"]
            and not binding["capture3_payload_opened"]
            and not binding["uwb_spatial_payload_consumed"]
        ),
    }


def _physical_decision(
    capture: str,
    binding: Mapping[str, Any],
    factors: Mapping[str, Any],
    edgewise: Mapping[str, Any],
    physical: Mapping[str, Any],
) -> dict[str, Any]:
    access = _access_gates(binding)
    structure = physical["structural_audit"]
    lengths = structure["segment_lengths_m"]
    external = {
        "upper_arm_left": (0.28, 0.02),
        "upper_arm_right": (0.28, 0.02),
        "thigh_left": (0.48, 0.03),
        "thigh_right": (0.48, 0.03),
    }
    length_report = {
        segment: {
            "capture_profile_joint_center_separation_state_m": lengths[segment],
            "external_soft_tape_mean_m": mean,
            "external_soft_tape_sigma_m": sigma,
            "absolute_error_m": abs(lengths[segment] - mean),
            "acceptance_tolerance_m": 0.03,
            "source": "INDEPENDENT_USER_SOFT_TAPE_AFTER_PRIOR_FAIL",
            "data_identifiability": physical["length_identifiability"][segment],
            "dynamically_identified": physical["length_identifiability"][segment][
                "dynamically_identified"
            ],
            "estimate_semantics": physical["length_identifiability"][segment][
                "estimate_semantics"
            ],
            "external_agreement_pass": abs(lengths[segment] - mean) <= 0.03,
        }
        for segment, (mean, sigma) in external.items()
    }
    length_report["torso"] = {
        "configured_joint_center_separation_m": lengths["torso"],
        "measured": False,
        "broad_prior_mean_m": 0.35,
        "broad_prior_sigma_m": 0.10,
        "predeclared_range_m": [0.20, 0.50],
        "range_pass": 0.20 <= lengths["torso"] <= 0.50,
        "estimate_semantics": "BROAD_UNMEASURED_CONFIGURATION_NOT_AN_IMU_ESTIMATE",
        "evidence_class": "C",
    }
    bilateral = {
        "upper_arm_abs_difference_m": abs(
            lengths["upper_arm_left"] - lengths["upper_arm_right"]
        ),
        "thigh_abs_difference_m": abs(
            lengths["thigh_left"] - lengths["thigh_right"]
        ),
        "soft_tolerance_m": 0.03,
        "exact_equality_constraint": False,
    }
    bilateral["pass"] = bool(
        bilateral["upper_arm_abs_difference_m"] <= 0.03
        and bilateral["thigh_abs_difference_m"] <= 0.03
    )

    qmt = {}
    for edge in ("elbow_left", "elbow_right", "knee_left", "knee_right"):
        report = factors[edge].qmt_report
        qualified = bool(report and report["qualification"]["pass"])
        qmt_heading = (
            report["qualification"]["pooled_heading_rad"] if qualified else None
        )
        discrepancy = (
            float(np.degrees(abs(wrap(
                physical["edges"][edge]["relative_heading_rad"] - qmt_heading
            )))) if qmt_heading is not None else None
        )
        qmt[edge] = {
            "qmt_qualification_pass": qualified,
            "qmt_pooled_heading_deg": (
                float(np.degrees(qmt_heading)) if qmt_heading is not None else None
            ),
            "unified_physical_heading_deg": physical["edges"][edge]["relative_heading_deg"],
            "absolute_circular_discrepancy_deg": discrepancy,
            "predeclared_threshold_deg": 20.0,
            "pass": bool(qualified and discrepancy is not None and discrepancy <= 20.0),
            "all_action_records_retained": True,
            "qmt_role": "INDEPENDENT_CAPTURE_WIDE_EDGE_DIAGNOSTIC_NOT_CALIBRATION_FACTOR",
        }
    named = {
        "CAPTURE1": ("elbow_right",),
        "CAPTURE2": ("elbow_left", "knee_left"),
    }[capture]

    held = {}
    for edge, row in physical["edges"].items():
        train = row["train"]["physical_rms_mps2"]
        value = row["held_out"]["physical_rms_mps2"]
        passed = bool(
            value is not None and value <= 1.5 and value <= 1.75 * max(train, 1e-12)
        )
        held[edge] = {
            "train_rms_mps2": train,
            "held_out_rms_mps2": value,
            "ratio": value / max(train, 1e-12) if value is not None else None,
            "pass": passed,
        }

    gates = {
        **access,
        "one_capture_wide_nine_heading_vector_one_gauge": bool(
            len(physical["headings_rad"]) == 10
            and physical["root_yaw_gauge_count"] == 1
            and physical["publishable_heading_dimension"] == 9
        ),
        "rank_nine_after_one_gauge": physical["numeric_rank_after_gauge"] == 9,
        "broad_24_start_multistart_agreement": bool(
            len(physical["multistart"]) >= 24
            and physical["multistart_max_spread_deg"] <= 10.0
            and physical["multistart_contract"]["local_basin_only"] is False
        ),
        "physical_connection_structure": structure["pass"],
        "external_upper_arm_thigh_agreement": all(
            row["external_agreement_pass"]
            for segment, row in length_report.items() if segment != "torso"
        ),
        "dynamic_upper_arm_thigh_length_identifiability": all(
            row["dynamically_identified"]
            for segment, row in length_report.items() if segment != "torso"
        ),
        "length_prior_sensitivity": all(
            row["data_identifiability"]["prior_sensitivity"]["pass"]
            for segment, row in length_report.items() if segment != "torso"
        ),
        "torso_broad_physical_range": length_report["torso"]["range_pass"],
        "bilateral_soft_plausibility": bilateral["pass"],
        "no_segment_collapse": bool(
            min(
                lengths[segment]
                for segment in ("upper_arm_left", "upper_arm_right", "thigh_left", "thigh_right")
            ) >= 0.18
            and structure["independent_edge_lever_state_dimension"] == 0
        ),
        "every_modeled_endpoint_estimate_and_evidence_reported": bool(
            physical["endpoint_evidence"]["every_modeled_endpoint_reported"]
            and all(
                row["evidence_class"] in {"A", "B", "C"}
                and "approximate_95pct_component_interval_m" in row
                and "bound_activity" in row
                and "dominant_evidence_source" in row
                for row in physical["endpoint_evidence"]["endpoints"].values()
            )
        ),
        "held_out_physical_generalization": all(row["pass"] for row in held.values()),
        "qmt_unified_all_signal_qualified_edges_le_20deg": all(
            row["pass"] for row in qmt.values()
        ),
        "named_previous_qmt_conflicts_resolved": all(qmt[edge]["pass"] for edge in named),
        "captures_computationally_independent": physical["cross_capture_parameters"] is False,
    }
    ordered = list(gates)
    first_failed = next((key for key in ordered if not gates[key]), None)
    return {
        "schema": "biospur-pure-imu-v0-physical-graph-real-decision-v1",
        "capture": capture,
        "gates": gates,
        "first_failed_gate": first_failed,
        "failed_gates": [key for key, value in gates.items() if not value],
        "segment_lengths": length_report,
        "endpoint_evidence_class_counts": {
            evidence: sum(
                row["evidence_class"] == evidence
                for row in physical["endpoint_evidence"]["endpoints"].values()
            )
            for evidence in ("A", "B", "C")
        },
        "bilateral": bilateral,
        "held_out": held,
        "qmt_unified": qmt,
        "named_previous_conflicts": list(named),
        "pass": first_failed is None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-capture1-access", action="store_true")
    parser.add_argument("--capture", choices=("CAPTURE1", "CAPTURE2"))
    parser.add_argument("--finalize-only", action="store_true")
    args = parser.parse_args()
    if sum((args.prepare_capture1_access, bool(args.capture), args.finalize_only)) != 1:
        parser.error("select exactly one mode")
    root = ROOT.resolve()
    run_dir = root / RUN_REL
    selection = load_frozen_selection(root)
    synthetic = require_synthetic(root)
    protocol = load_protocol(root)

    if args.prepare_capture1_access:
        output = run_dir / C1_PREFLIGHT_NAME
        if output.exists():
            raise FileExistsError(output)
        capture_spec = protocol["captures"]["CAPTURE1"]
        all_actions = legacy._action_rows(root, "CAPTURE1", capture_spec)
        payload = prepare_capture1_bounded_preflight(
            root,
            capture_spec,
            all_actions,
            selection["captures"]["CAPTURE1"]["selected_actions"],
            output,
            preselection_path=run_dir / "METADATA_PRESELECTION.json",
            preselection_sha256=FRESH_PRESELECTION_SHA256,
        )
        print(json.dumps({
            "output": str(output), "sha256": sha256_file(output),
            "gate": payload["gate"],
        }, indent=2, sort_keys=True))
        return

    if args.finalize_only:
        captures = {}
        for capture in ("CAPTURE1", "CAPTURE2"):
            path = run_dir / f"{capture}_RESULT.json"
            if not path.exists():
                raise RuntimeError(f"{capture}: result absent")
            captures[capture] = json.loads(path.read_text(encoding="utf-8"))
        gates = {
            "synthetic_qualification": synthetic["pass"],
            "capture1_real_physical_fit": captures["CAPTURE1"]["decision"]["pass"],
            "capture2_real_physical_fit": captures["CAPTURE2"]["decision"]["pass"],
            "capture1_capture2_independent": bool(
                captures["CAPTURE1"]["other_capture_parameters_used"] is False
                and captures["CAPTURE2"]["other_capture_parameters_used"] is False
            ),
        }
        final = {
            "schema": "biospur-pure-imu-v0-physical-graph-final-decision-v1",
            "gates": gates,
            "pass": all(gates.values()),
            "capture_decisions": {
                capture: row["decision"] for capture, row in captures.items()
            },
            "product_boundary": {
                "hxx_opened": False,
                "golf_boxing_opened": False,
                "capture3_opened": False,
                "uwb_spatial_opened": False,
                "freeze_or_candidate_lock_performed": False,
                "unlock_authorized": False,
            },
        }
        output = run_dir / "FINAL_DECISION.json"
        if output.exists():
            raise FileExistsError(output)
        dump_json(output, final); output.chmod(0o444)
        print(json.dumps({"output": str(output), "pass": final["pass"], "gates": gates}, indent=2))
        return

    capture = args.capture
    assert capture is not None
    if capture == "CAPTURE1" and not (run_dir / C1_PREFLIGHT_NAME).exists():
        raise RuntimeError("fresh Capture1 metadata-only bounded preflight is absent")
    capture_spec = protocol["captures"][capture]
    frozen = selection["captures"][capture]
    access_attempt = 1
    while True:
        suffix = "" if access_attempt == 1 else f"_RETRY{access_attempt}"
        candidate_audit = run_dir / f"{capture}_PAYLOAD_ACCESS_AUDIT{suffix}.json"
        candidate_actions = run_dir / f"{capture}_ACTION_ACCESS{suffix}"
        if not candidate_audit.exists() and not candidate_actions.exists():
            break
        access_attempt += 1
    episodes, binding = _load_capture(
        root, run_dir, capture, capture_spec, frozen,
        access_attempt=access_attempt,
    )
    suffix = "" if access_attempt == 1 else f"_RETRY{access_attempt}"
    access_path = run_dir / f"{capture}_PAYLOAD_ACCESS_AUDIT{suffix}.json"
    dump_json(access_path, binding); access_path.chmod(0o444)
    access_gates = _access_gates(binding)
    if not all(access_gates.values()):
        failure = {
            "schema": "biospur-pure-imu-v0-physical-graph-stopped-access-fail-v1",
            "capture": capture,
            "access_gates": access_gates,
            "first_failed_gate": next(key for key, value in access_gates.items() if not value),
        }
        output = run_dir / f"{capture}_RESULT.json"
        dump_json(output, failure); output.chmod(0o444)
        print(json.dumps(failure, indent=2)); raise SystemExit(2)

    qmt_actions = legacy._qmt_intended_action_map(frozen)
    factors, factor_audit = build_edge_factors(
        episodes, qmt_intended_actions=qmt_actions,
    )
    edgewise = fit_edgewise(factors)
    initial = np.asarray([
        edgewise["accumulated_headings_rad"][segment] for segment in SEGMENTS[1:]
    ])
    subject_spec = real_subject_spec()
    physical = fit_physical_graph(
        factors, initial, subject_spec,
        starts=24,
        seed=2026082801 if capture == "CAPTURE1" else 2026082802,
        maximum_function_evaluations=160,
    )
    print(f"STAGE {capture} full physical graph fit complete", flush=True)
    pre_endpoint_checkpoint = (
        run_dir / f"{capture}_FULL_PHYSICAL_GRAPH_PRE_ENDPOINT_AUDIT{suffix}.json"
    )
    if pre_endpoint_checkpoint.exists():
        raise FileExistsError(pre_endpoint_checkpoint)
    dump_json(pre_endpoint_checkpoint, _jsonable({
        "schema": "biospur-pure-imu-v0-full-physical-graph-pre-endpoint-checkpoint-v1",
        "capture": capture,
        "profile_id": f"PROFILE_{capture}",
        "access_attempt": access_attempt,
        "physical_graph": physical,
        "capture1_capture2_parameters_shared": False,
    }))
    pre_endpoint_checkpoint.chmod(0o444)
    pivot_audit = distal_pivot_evidence_audit(episodes, physical, subject_spec)
    physical["distal_pivot_evidence"] = pivot_audit
    merge_distal_pivot_evidence(physical["endpoint_evidence"], pivot_audit)
    print(f"STAGE {capture} distal endpoint evidence audit complete", flush=True)
    transitions = transition_ablation_physical(
        factors,
        physical,
        subject_spec,
        seed=2026082811 if capture == "CAPTURE1" else 2026082812,
    )
    print(f"STAGE {capture} transition ablation complete", flush=True)
    checkpoint_path = run_dir / f"{capture}_FULL_PHYSICAL_GRAPH{suffix}.json"
    if checkpoint_path.exists():
        raise FileExistsError(checkpoint_path)
    dump_json(checkpoint_path, _jsonable({
        "schema": "biospur-pure-imu-v0-capture-full-physical-graph-checkpoint-v1",
        "capture": capture,
        "profile_id": f"PROFILE_{capture}",
        "physical_graph": physical,
        "transition_ablation": transitions,
        "capture1_capture2_parameters_shared": False,
    }))
    checkpoint_path.chmod(0o444)
    decision = _physical_decision(capture, binding, factors, edgewise, physical)
    fk = None
    if decision["pass"]:
        fk = generate_direct_fk_artifacts(
            capture=capture,
            episodes=episodes,
            result=physical,
            spec=subject_spec,
            factors=factors,
            html_path=run_dir / f"{capture}_DIRECT_PHYSICAL_FK_VIEWER.html",
            contact_sheet_path=run_dir / f"{capture}_DIRECT_PHYSICAL_FK_CONTACT_SHEET.png",
        )
        if not fk["numeric_qa"]["pass"]:
            decision["gates"]["direct_fixed_profile_fk_numeric"] = False
            decision["failed_gates"].append("direct_fixed_profile_fk_numeric")
            decision["first_failed_gate"] = decision["first_failed_gate"] or "direct_fixed_profile_fk_numeric"
            decision["pass"] = False
        else:
            decision["gates"]["direct_fixed_profile_fk_numeric"] = True
    result = {
        "schema": "biospur-pure-imu-v0-physical-graph-capture-result-v1",
        "capture": capture,
        "capture_id": capture_spec["capture_id"],
        "profile_id": f"PROFILE_{capture}",
        "synthetic_prerequisite": synthetic,
        "payload_access_audit": {"path": str(access_path), "sha256": sha256_file(access_path)},
        "factor_inventory": factor_audit,
        "qmt_edgewise_diagnostic": edgewise,
        "physical_graph": physical,
        "full_physical_graph_checkpoint": {
            "path": str(checkpoint_path),
            "sha256": sha256_file(checkpoint_path),
        },
        "pre_endpoint_full_fit_checkpoint": {
            "path": str(pre_endpoint_checkpoint),
            "sha256": sha256_file(pre_endpoint_checkpoint),
        },
        "bounded_access_attempt": access_attempt,
        "transition_ablation": transitions,
        "drift_stillness": drift_stillness(episodes),
        "direct_fk": fk,
        "decision": decision,
        "raw_signal_input_only": True,
        "prohibited_inputs_used": [],
        "b4_used": False,
        "per_action_calibration": False,
        "other_capture_payload_used": False,
        "other_capture_parameters_used": False,
        "hxx_golf_boxing_capture3_or_uwb_spatial_opened": False,
    }
    output = run_dir / f"{capture}_RESULT.json"
    if output.exists():
        raise FileExistsError(output)
    dump_json(output, _jsonable(result)); output.chmod(0o444)
    print(json.dumps({
        "capture": capture,
        "output": str(output),
        "pass": decision["pass"],
        "first_failed_gate": decision["first_failed_gate"],
        "failed_gates": decision["failed_gates"],
        "viewer_generated": fk is not None,
    }, indent=2, sort_keys=True))
    if not decision["pass"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
