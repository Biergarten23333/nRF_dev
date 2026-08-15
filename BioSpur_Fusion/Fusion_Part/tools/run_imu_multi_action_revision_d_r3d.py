#!/usr/bin/env python3
"""R3D frame audit, synthetic qualification, and one calibration-only run."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Fusion_Part/src"))

from biospur_fusion.imu_multi_action_engineering_v1.common_time import build_common_timeline
from biospur_fusion.imu_multi_action_engineering_v1.pipeline import load_q2_cache
from biospur_fusion.imu_multi_action_revision_d.r3b_topology import phase_groups_from_cycle_vectors
from biospur_fusion.imu_multi_action_revision_d.r3c_activity import analyze_chain
from biospur_fusion.imu_multi_action_revision_d.r3d_activity import (
    ACTIONS,
    analyze_broad_actions,
    frame_manifest,
    inject_left_yaw,
    row_hash,
    verify_chain_result_binding,
)
from biospur_fusion.imu_multi_action_revision_d.r3d_synthetic import qualify
from biospur_fusion.imu_preview_v0.io import savez_deterministic


BASELINE_COMMIT = "bc4060909285a7d51fe9b464f0867aa004f4ef45"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def clean(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return clean(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return clean(float(value))
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    return value


def canonical(value: Any) -> bytes:
    return (json.dumps(clean(value), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def dump(path: Path, value: Any) -> None:
    path.write_bytes(canonical(value))


def manifest(output: Path) -> None:
    dump(
        output / "SHA256_MANIFEST.json",
        {
            str(path.relative_to(output)): sha256(path)
            for path in sorted(output.rglob("*"))
            if path.is_file() and path.name != "SHA256_MANIFEST.json"
        },
    )


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def verify_sha_manifest(directory: Path) -> None:
    expected = load_json(directory / "SHA256_MANIFEST.json")
    for relative, digest in expected.items():
        path = directory / relative
        if not path.is_file() or sha256(path) != digest:
            raise RuntimeError(f"immutable manifest mismatch: {path}")


def chain_specs(mapping: Mapping[str, Any]) -> list[dict[str, str]]:
    output = []
    for action, item in mapping["actions"].items():
        for group, role in (("primary_chains", "PRIMARY"), ("diagnostic_chains", "DIAGNOSTIC")):
            for name, pair in item.get(group, {}).items():
                output.append(
                    {
                        "key": f"{action}:{name}",
                        "action": action,
                        "name": name,
                        "parent": pair[0],
                        "child": pair[1],
                        "role": role,
                    }
                )
    return output


def rows_for(timeline: Any, domain: tuple[int, int]) -> np.ndarray:
    return np.flatnonzero((timeline.time_ns >= domain[0]) & (timeline.time_ns <= domain[1]))


def real_context(args: argparse.Namespace, contract: Mapping[str, Any]):
    phase = load_json(args.phase_a / "RESULT.json")
    cache = args.phase_a / "Q2_HUMAN_QUASI_STATIC_CACHE.npz"
    if not phase["pass"] or sha256(cache) != phase["q2_cache_sha256"]:
        raise RuntimeError("Phase-A calibration-only Q2 cache binding failed")
    if phase["final_still"] != "SEALED" or phase["data_access"]["final_still"] != "SEALED_NOT_OPENED":
        raise RuntimeError("final_still firewall failed")
    r2 = load_json(args.r2 / "ACTION_PHASE_TIMELINE.json")
    r2_result = load_json(args.r2 / "RESULT.json")
    if sha256(args.r2 / "ACTION_PHASE_TIMELINE.json") != r2_result["action_phase_timeline_sha256"]:
        raise RuntimeError("R2 action-domain binding failed")
    gates = load_json(args.legacy_gates)
    q2 = load_q2_cache(cache)
    domains = {action: tuple(r2["actions"][action]["search_domain_ns"]) for action in ACTIONS}
    timeline = build_common_timeline(
        q2,
        min(value[0] for value in domains.values()),
        max(value[1] for value in domains.values()),
        {
            "rate_hz": float(contract["activity"]["common_time_rate_hz"]),
            "maximum_bracket_gap_s": float(contract["activity"]["maximum_sample_gap_s"]),
            "require_same_boot_epoch": True,
        },
    )
    node_index = {node: index for index, node in enumerate(timeline.node_order)}
    segment_index = {segment: node_index[node] for node, segment in gates["node_to_segment"].items()}
    segment_node = {segment: node for node, segment in gates["node_to_segment"].items()}
    return phase, cache, r2, gates, timeline, segment_index, segment_node, domains


def array_sha(value: np.ndarray) -> str:
    value = np.asarray(value)
    h = hashlib.sha256()
    h.update(str(value.dtype).encode() + b"\0")
    h.update(np.asarray(value.shape, dtype="<i8").tobytes())
    h.update(np.ascontiguousarray(value).tobytes())
    return h.hexdigest()


def _active_rows(evidence: Mapping[str, Any]) -> np.ndarray:
    rows = []
    for bout in evidence["active_bouts"]:
        rows.extend(range(int(bout["start_row"]), int(bout["stop_row_exclusive"])))
    return np.asarray(sorted(set(rows)), int)


def old_chain_output(
    timeline: Any,
    spec: Mapping[str, str],
    segment_index: Mapping[str, int],
    domain: tuple[int, int],
    r3c_contract: Mapping[str, Any],
) -> dict[str, Any]:
    rows = rows_for(timeline, domain)
    signal, evidence = analyze_chain(
        timeline,
        segment_index[spec["parent"]],
        segment_index[spec["child"]],
        rows,
        r3c_contract,
    )
    if signal["status"] != "AVAILABLE":
        return {"status": signal["status"], "key": spec["key"]}
    valid = np.asarray(signal["activity"]["valid"], bool)
    z = np.asarray(signal["activity_z"], float)
    onset = valid[rows] & np.isfinite(z[rows]) & (z[rows] >= float(r3c_contract["active_bout"]["onset_activity_z"]))
    offset = valid[rows] & np.isfinite(z[rows]) & (z[rows] <= float(r3c_contract["active_bout"]["offset_activity_z"]))
    active = _active_rows(evidence)
    cycles = evidence["cycles"]["complete_cycles"]
    direction = phase_groups_from_cycle_vectors(
        cycles, float(r3c_contract["phase_association"]["minimum_axis_cluster_separation_deg"])
    )
    maximum_excursion = float(np.nanmax(signal["smoothed_excursion_rad"][rows]))
    sigma = float(np.nanmedian(signal["excursion_uncertainty_rad"][rows]))
    factor_eligible = (
        len(active) >= int(r3c_contract["factor_evidence"]["minimum_active_rows"])
        and maximum_excursion >= float(r3c_contract["factor_evidence"]["minimum_excursion_rad"])
        and maximum_excursion / max(sigma, 1e-12) >= float(r3c_contract["factor_evidence"]["minimum_excursion_sigma"])
    )
    return {
        "status": "AVAILABLE",
        "key": spec["key"],
        "activity_rate_sha256": array_sha(signal["activity"]["rate_rad_s"][rows]),
        "activity_z_sha256": array_sha(z[rows]),
        "onset_mask_sha256": array_sha(onset.astype(np.uint8)),
        "offset_mask_sha256": array_sha(offset.astype(np.uint8)),
        "active_membership_sha256": array_sha(active.astype("<i8")),
        "cycle_sha256": hashlib.sha256(canonical(cycles)).hexdigest(),
        "direction_groups_sha256": hashlib.sha256(canonical(direction)).hexdigest(),
        "factor_eligible": bool(factor_eligible),
        "active_rows": int(len(active)),
        "complete_cycles": int(len(cycles)),
        "activity_rate": signal["activity"]["rate_rad_s"][rows],
        "activity_z": z[rows],
        "onset_mask": onset,
        "offset_mask": offset,
    }


def compact_old_output(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in ("activity_rate", "activity_z", "onset_mask", "offset_mask")}


def compare_old(reference: Mapping[str, Any], candidate: Mapping[str, Any], tolerance: float) -> dict[str, Any]:
    if reference["status"] != "AVAILABLE" or candidate["status"] != "AVAILABLE":
        return {"changed": reference["status"] != candidate["status"], "status_before": reference["status"], "status_after": candidate["status"]}
    finite_rate = np.isfinite(reference["activity_rate"]) & np.isfinite(candidate["activity_rate"])
    finite_z = np.isfinite(reference["activity_z"]) & np.isfinite(candidate["activity_z"])
    rate_difference = float(np.max(np.abs(reference["activity_rate"][finite_rate] - candidate["activity_rate"][finite_rate]))) if np.any(finite_rate) else 0.0
    z_difference = float(np.max(np.abs(reference["activity_z"][finite_z] - candidate["activity_z"][finite_z]))) if np.any(finite_z) else 0.0
    fields = (
        "activity_rate_sha256",
        "activity_z_sha256",
        "onset_mask_sha256",
        "offset_mask_sha256",
        "active_membership_sha256",
        "cycle_sha256",
        "direction_groups_sha256",
        "factor_eligible",
    )
    changed_fields = [field for field in fields if reference[field] != candidate[field]]
    return {
        "maximum_activity_rate_difference_rad_s": rate_difference,
        "maximum_activity_z_difference": z_difference,
        "changed_fields": changed_fields,
        "changed": bool(rate_difference > tolerance or z_difference > tolerance or changed_fields),
    }


def injected_timeline(timeline: Any, gauges_by_node: Mapping[str, float]) -> Any:
    modified = copy.copy(timeline)
    rotation = np.asarray(timeline.rotation, float).copy()
    node_index = {node: index for index, node in enumerate(timeline.node_order)}
    for node, alpha in gauges_by_node.items():
        index = node_index[node]
        rotation[:, index] = inject_left_yaw(rotation[:, index], float(alpha))
    modified.rotation = rotation
    return modified


def audit(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists():
        raise FileExistsError(args.output)
    if git_head() != BASELINE_COMMIT:
        raise RuntimeError("R3D audit must start from the exact committed R3C checkpoint")
    verify_sha_manifest(args.r3c_formal)
    r3c_result = load_json(args.r3c_formal / "RESULT.json")
    if r3c_result["terminal_outcome"] != "PASS_R3C_SIGNAL_DERIVED_MOTION_EVIDENCE":
        raise RuntimeError("historical R3C result is not the accepted immutable checkpoint")
    contract = load_json(args.contract)
    r3c_contract = load_json(args.r3c_contract)
    mapping = load_json(args.chain_map)
    phase, cache, _, _, timeline, segment_index, segment_node, domains = real_context(args, contract)
    specs = chain_specs(mapping)
    baseline = {
        spec["key"]: old_chain_output(timeline, spec, segment_index, domains[spec["action"]], r3c_contract)
        for spec in specs
    }
    tolerance = float(contract["gauge_qualification"]["old_pairwise_change_detection_tolerance"])
    nonzero = [value for value in contract["gauge_qualification"]["yaw_injections_rad"] if value != 0.0]
    records = []
    node_scenarios = []
    for node in timeline.node_order:
        for alpha in nonzero:
            node_scenarios.append((f"one_node:{node}:{alpha:+.12f}", {node: alpha}, "ONE_NODE_AT_A_TIME"))
    for name, gauges, category in node_scenarios:
        candidate_timeline = injected_timeline(timeline, gauges)
        for spec in specs:
            candidate = old_chain_output(candidate_timeline, spec, segment_index, domains[spec["action"]], r3c_contract)
            comparison = compare_old(baseline[spec["key"]], candidate, tolerance)
            role = "UNRELATED"
            if next(iter(gauges)) == segment_node[spec["parent"]]:
                role = "PARENT_ONLY"
            elif next(iter(gauges)) == segment_node[spec["child"]]:
                role = "CHILD_ONLY"
            records.append({"scenario": name, "category": category, "chain": spec["key"], "injected_role": role, "gauges_rad": gauges, "comparison": comparison, "output": compact_old_output(candidate)})
    for spec in specs:
        parent_node = segment_node[spec["parent"]]
        child_node = segment_node[spec["child"]]
        for alpha in nonzero:
            gauges = {parent_node: alpha, child_node: -alpha}
            candidate = old_chain_output(injected_timeline(timeline, gauges), spec, segment_index, domains[spec["action"]], r3c_contract)
            records.append({"scenario": f"opposite_parent_child:{spec['key']}:{alpha:+.12f}", "category": "OPPOSITE_PARENT_CHILD_YAW", "chain": spec["key"], "injected_role": "PARENT_AND_CHILD_OPPOSITE", "gauges_rad": gauges, "comparison": compare_old(baseline[spec["key"]], candidate, tolerance), "output": compact_old_output(candidate)})
    combinations = []
    nodes = list(timeline.node_order)
    values = [0.0, math.pi / 4, -math.pi / 2, math.pi, -math.pi / 4]
    for shift in range(3):
        combinations.append({node: values[(index + shift) % len(values)] for index, node in enumerate(nodes)})
    for index, gauges in enumerate(combinations):
        candidate_timeline = injected_timeline(timeline, gauges)
        for spec in specs:
            candidate = old_chain_output(candidate_timeline, spec, segment_index, domains[spec["action"]], r3c_contract)
            records.append({"scenario": f"deterministic_ten_node_combination:{index}", "category": "DETERMINISTIC_TEN_NODE_COMBINATION", "chain": spec["key"], "injected_role": "MULTI_NODE", "gauges_rad": gauges, "comparison": compare_old(baseline[spec["key"]], candidate, tolerance), "output": compact_old_output(candidate)})
    changed = [record for record in records if record["comparison"]["changed"]]
    parent_changed = any(record["comparison"]["changed"] and record["injected_role"] == "PARENT_ONLY" for record in records)
    child_changed = any(record["comparison"]["changed"] and record["injected_role"] == "CHILD_ONLY" for record in records)
    opposite_changed = any(record["comparison"]["changed"] and record["injected_role"] == "PARENT_AND_CHILD_OPPOSITE" for record in records)
    combination_changed = any(record["comparison"]["changed"] and record["category"] == "DETERMINISTIC_TEN_NODE_COMBINATION" for record in records)
    args.output.mkdir(parents=True)
    dump(args.output / "Q2_FRAME_MANIFEST.json", frame_manifest())
    dump(
        args.output / "R3C_SUPERSEDING_CONSUMABILITY.json",
        {
            "R3C_ORIGINAL_VERDICT": "PASS_R3C_SIGNAL_DERIVED_MOTION_EVIDENCE",
            "R3C_ORIGINAL_D0_READY": "HISTORICAL_CLAIM_NOT_REWRITTEN",
            "R3C_PAIRWISE_RELATIVE_ACTIVITY": "NOT_YET_QUALIFIED",
            "R3C_CYCLE_TOPOLOGY": "QC_ONLY_NOT_CONSUMABLE",
            "R3C_AXIS_SIGN_ZERO_ELIGIBILITY": "NOT_YET_QUALIFIED",
            "R3C_ARTIFACTS_MODIFIED": False,
        },
    )
    dump(args.output / "R3C_INDEPENDENT_YAW_INJECTION_RECORDS.json", {"schema": "biospur-r3d-old-r3c-yaw-injection-records-v1", "baseline": {key: compact_old_output(value) for key, value in baseline.items()}, "records": records})
    audit_result = {
        "schema": "biospur-r3d-frame-gauge-audit-v1",
        "Q2_DESTINATION_FRAMES_SHARED_ACROSS_NODES": False,
        "analytic_pair_transform": "R_parent^T G_parent^T G_child R_child",
        "old_pairwise_output_changed": bool(changed),
        "changed_record_count": len(changed),
        "total_record_count": len(records),
        "parent_only_change_observed": parent_changed,
        "child_only_change_observed": child_changed,
        "opposite_parent_child_change_observed": opposite_changed,
        "deterministic_ten_node_change_observed": combination_changed,
        "classification": "FAIL_R3C_PAIRWISE_GAUGE_DEPENDENCE" if changed else "R3C_PAIRWISE_GAUGE_DEPENDENCE_NOT_DEMONSTRATED",
        "failure_is_subject_motion_failure": False,
        "q2_cache_sha256": sha256(cache),
    }
    dump(args.output / "R3D0_GAUGE_AUDIT.json", audit_result)
    dump(args.output / "DATA_ACCESS_AUDIT.json", {"opened": ["COMMITTED_R3C_SOURCE_CONFIG_EVIDENCE", "CALIBRATION_ONLY_Q2_CACHE", "ELEVEN_CALIBRATION_ACTION_DOMAINS", "NODE_MAPPING", "GENERIC_TEMPLATE_REFERENCE_ONLY"], "forbidden_opened": [], "FINAL_STILL": "SEALED", "WALK": "SEALED", "GOLF": "SEALED", "BOXING": "SEALED", "UWB_T4_ANCHOR": "SEALED", "OPERATOR_MEASUREMENTS": "SEALED", "REAL_D0_OBJECTIVE": "NOT_EVALUATED", "REAL_D0_JACOBIAN": "NOT_EVALUATED", "REAL_D0_SOLVER": "NOT_STARTED"})
    result = {"terminal_outcome": audit_result["classification"], "pass_for_replacement_implementation": bool(changed), "historical_r3c_rewritten": False}
    dump(args.output / "RESULT.json", result)
    manifest(args.output)
    return result


def synthetic(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists():
        raise FileExistsError(args.output)
    contract = load_json(args.contract)
    first = qualify(contract)
    second = qualify(contract)
    first_bytes = canonical(first)
    second_bytes = canonical(second)
    deterministic = first_bytes == second_bytes
    args.output.mkdir(parents=True)
    (args.output / "R3D_SYNTHETIC_REPLAY_1.json").write_bytes(first_bytes)
    (args.output / "R3D_SYNTHETIC_REPLAY_2.json").write_bytes(second_bytes)
    result = {
        "schema": "biospur-r3d-double-synthetic-result-v1",
        "replay_1_pass": first["pass"],
        "replay_2_pass": second["pass"],
        "byte_identical": deterministic,
        "replay_sha256": hashlib.sha256(first_bytes).hexdigest(),
        "pass": bool(first["pass"] and second["pass"] and deterministic),
    }
    result["terminal_outcome"] = "PASS_R3D_SYNTHETIC_QUALIFICATION" if result["pass"] else "FAIL_R3D_SYNTHETIC_QUALIFICATION"
    dump(args.output / "RESULT.json", result)
    dump(args.output / "DATA_ACCESS_AUDIT.json", {"real_capture_accessed": False, "generator_truth_boundaries_passed_to_detector": False, "sealed_inputs_accessed": [], "REAL_D0_OBJECTIVE": "NOT_EVALUATED", "REAL_D0_JACOBIAN": "NOT_EVALUATED", "REAL_D0_SOLVER": "NOT_STARTED"})
    manifest(args.output)
    return result


def _array_payload(analysis: Mapping[str, Any], timeline: Any) -> dict[str, np.ndarray]:
    payload = {"common_time_ns": np.asarray(timeline.time_ns, np.int64)}
    for node, item in analysis["per_node"].items():
        prefix = f"node__{node}"
        payload[f"{prefix}__increment_rad"] = item["activity"]["increment_rad"]
        payload[f"{prefix}__activity_rad_s"] = item["activity"]["rate_rad_s"]
        payload[f"{prefix}__valid_mask"] = item["activity"]["valid"].astype(np.uint8)
        if item["status"] == "AVAILABLE":
            payload[f"{prefix}__activity_z"] = item["z"]
    for action, item in analysis["actions"].items():
        prefix = f"action__{action}"
        for key in ("BROAD_ACTIVE_ROWS", "PRIMARY_BOARD_ACTIVITY", "PROXIMAL_BOARD_ACTIVITY", "VALID_ROWS", "QUIET_REFERENCE_ROWS"):
            payload[f"{prefix}__{key.lower()}"] = np.asarray(item[key], np.int64)
        for node, mask in item["node_masks"].items():
            payload[f"{prefix}__node__{node}__active_mask"] = np.asarray(mask, np.uint8)
    return payload


def _compact_action(action: str, item: Mapping[str, Any], timeline: Any) -> dict[str, Any]:
    return {
        "action": action,
        "status": item["status"],
        "kind": item["kind"],
        "roles": item["roles"],
        "domain_row_count": int(len(item["domain_rows"])),
        "domain_start_time_ns": int(timeline.time_ns[item["domain_rows"][0]]),
        "domain_stop_time_ns": int(timeline.time_ns[item["domain_rows"][-1]]),
        "BROAD_ACTIVE_ROWS": item["BROAD_ACTIVE_ROWS"].tolist(),
        "PRIMARY_BOARD_ACTIVITY": item["PRIMARY_BOARD_ACTIVITY"].tolist(),
        "PROXIMAL_BOARD_ACTIVITY": item["PROXIMAL_BOARD_ACTIVITY"].tolist(),
        "VALID_ROWS": item["VALID_ROWS"].tolist(),
        "QUIET_REFERENCE_ROWS": item["QUIET_REFERENCE_ROWS"].tolist(),
        "STATIC_PLATEAU_CANDIDATE": item["STATIC_PLATEAU_CANDIDATE"],
        "node_bindings": item["node_bindings"],
        "cycles_computed": False,
        "direction_groups_computed": False,
        "functional_axis_computed": False,
        "joint_sign_computed": False,
        "joint_zero_computed": False,
    }


def formal(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists():
        raise FileExistsError(args.output)
    synthetic_result = load_json(args.synthetic / "RESULT.json")
    gauge_result = load_json(args.audit / "RESULT.json")
    if not synthetic_result["pass"]:
        raise RuntimeError("R3D synthetic qualification did not pass")
    if gauge_result["terminal_outcome"] != "FAIL_R3C_PAIRWISE_GAUGE_DEPENDENCE":
        raise RuntimeError("old pairwise gauge defect was not demonstrated")
    contract = load_json(args.contract)
    mapping = load_json(args.chain_map)
    phase, cache, _, gates, timeline, _, _, domains = real_context(args, contract)
    analysis = analyze_broad_actions(timeline, domains, mapping, gates["node_to_segment"], contract)
    all_available = all(item["status"] == "AVAILABLE" for item in analysis["per_node"].values())
    action_status = {action: item["status"] for action, item in analysis["actions"].items()}
    required_activity = all(value == "PASS" for value in action_status.values())
    lookup = {}
    for action, item in analysis["actions"].items():
        rows = item["domain_rows"]
        for node, mask in item["node_masks"].items():
            lookup[(action, node)] = rows[mask[rows]]
    binding_ok = verify_chain_result_binding(analysis["coverage"], timeline.time_ns, lookup)
    corrupt = [dict(record) for record in analysis["coverage"]]
    corrupt[0]["array_key"] += "__CORRUPTED"
    binding_negative_control = not verify_chain_result_binding(corrupt, timeline.time_ns, lookup)
    gauges = {
        node: contract["gauge_qualification"]["yaw_injections_rad"][(index % 5) + 1]
        for index, node in enumerate(timeline.node_order)
    }
    candidate_timeline = injected_timeline(timeline, gauges)
    injected = analyze_broad_actions(candidate_timeline, domains, mapping, gates["node_to_segment"], contract)
    rate_tolerance = float(contract["gauge_qualification"]["maximum_increment_activity_absolute_difference_rad_s"])
    z_tolerance = float(contract["gauge_qualification"]["maximum_z_absolute_difference"])
    node_replay = []
    gauge_ok = True
    for node in timeline.node_order:
        ref = analysis["per_node"][node]
        cand = injected["per_node"][node]
        finite_rate = np.isfinite(ref["activity"]["rate_rad_s"]) & np.isfinite(cand["activity"]["rate_rad_s"])
        finite_z = np.isfinite(ref["z"]) & np.isfinite(cand["z"])
        rate_diff = float(np.max(np.abs(ref["activity"]["rate_rad_s"][finite_rate] - cand["activity"]["rate_rad_s"][finite_rate]))) if np.any(finite_rate) else 0.0
        z_diff = float(np.max(np.abs(ref["z"][finite_z] - cand["z"][finite_z]))) if np.any(finite_z) else 0.0
        membership = all(np.array_equal(analysis["actions"][action]["node_masks"].get(node, np.zeros(len(timeline.time_ns), bool)), injected["actions"][action]["node_masks"].get(node, np.zeros(len(timeline.time_ns), bool))) for action in ACTIONS)
        passed = rate_diff <= rate_tolerance and z_diff <= z_tolerance and membership
        gauge_ok &= passed
        node_replay.append({"node": node, "gauge_rad": gauges[node], "maximum_rate_absolute_difference_rad_s": rate_diff, "maximum_z_absolute_difference": z_diff, "action_membership_equal": membership, "pass": passed})
    arrays = _array_payload(analysis, timeline)
    action_records = {action: _compact_action(action, item, timeline) for action, item in analysis["actions"].items()}
    args.output.mkdir(parents=True)
    savez_deterministic(args.output / "R3D_NODE_AND_ACTION_ARRAYS.npz", arrays)
    dump(args.output / "R3D_ACTION_BROAD_MASKS.json", {"schema": "biospur-r3d-action-broad-masks-v1", "actions": action_records})
    dump(args.output / "R3D_ACTION_NODE_COVERAGE_MATRIX.json", {"schema": "biospur-r3d-action-node-coverage-v1", "rows": analysis["coverage"]})
    dump(args.output / "R3D_NODE_QUIET_SCALES.json", {"schema": "biospur-r3d-node-quiet-scales-v1", "nodes": {node: item.get("baseline") for node, item in analysis["per_node"].items()}})
    dump(args.output / "R3D_STATIC_PLATEAU_CANDIDATES.json", {"schema": "biospur-r3d-static-plateaus-v1", "actions": {action: item["STATIC_PLATEAU_CANDIDATE"] for action, item in analysis["actions"].items()}})
    dump(args.output / "R3D_GAUGE_INVARIANCE_REPLAY.json", {"schema": "biospur-r3d-formal-gauge-replay-v1", "gauges_rad": gauges, "nodes": node_replay, "pass": gauge_ok})
    if not all_available:
        terminal = "FAIL_R3D_REQUIRED_ACTION_ACTIVITY_MISSING"
    elif not binding_ok or not binding_negative_control:
        terminal = "FAIL_R3D_CHAIN_RESULT_BINDING"
    elif not gauge_ok:
        terminal = "FAIL_R3D_GAUGE_INVARIANCE"
    elif not required_activity:
        terminal = "FAIL_R3D_REQUIRED_ACTION_ACTIVITY_MISSING"
    else:
        terminal = "PASS_R3D_GAUGE_INVARIANT_BROAD_ACTIVITY"
    result = {
        "schema": "biospur-r3d-formal-result-v1",
        "terminal_outcome": terminal,
        "pass": terminal == "PASS_R3D_GAUGE_INVARIANT_BROAD_ACTIVITY",
        "formal_r3d_run_count": 1,
        "all_nodes_available": all_available,
        "action_status": action_status,
        "chain_result_binding": binding_ok,
        "corrupt_binding_negative_control": binding_negative_control,
        "gauge_invariance_replay": gauge_ok,
        "R3C_CYCLES": "NOT_CONSUMED_BY_D0",
        "R3C_DIRECTION_GROUPS": "NOT_CONSUMED_BY_D0",
        "R3C_SIGN_ELIGIBILITY": "NOT_CONSUMED_BY_D0",
        "R3C_ZERO_ELIGIBILITY": "NOT_CONSUMED_BY_D0",
        "EXACT_REPETITION_COUNT": "QC_ONLY",
        "REAL_D0_OBJECTIVE": "NOT_EVALUATED",
        "REAL_D0_JACOBIAN": "NOT_EVALUATED",
        "REAL_D0_SOLVER": "NOT_STARTED",
        "MULTISTART": "NOT_STARTED",
        "CALIBRATION_FREEZE": "NOT_CREATED",
        "REPLAY": "NOT_STARTED",
        "RENDER": "NOT_STARTED",
    }
    dump(args.output / "RESULT.json", result)
    dump(args.output / "DATA_ACCESS_AUDIT.json", {"opened": ["CALIBRATION_ONLY_Q2_CACHE", "ELEVEN_CALIBRATION_ACTION_DOMAINS", "NODE_MAPPING", "GENERIC_TEMPLATE_REFERENCE_ONLY"], "q2_cache_sha256": sha256(cache), "forbidden_opened": [], "FINAL_STILL": "SEALED", "WALK": "SEALED", "GOLF": "SEALED", "BOXING": "SEALED", "UWB_T4_ANCHOR": "SEALED", "OPERATOR_MEASUREMENTS": "SEALED", "formal_r3d_run_count": 1})
    (args.output / "REPORT.md").write_text(f"# Revision D R3D calibration-only broad activity\n\n`{terminal}`\n\nR3D uses only per-node right-increment magnitudes. It publishes broad motion-candidate masks and static plateau candidates, not cycles, directions, functional axes, joint signs, or joint zeros.\n")
    manifest(args.output)
    return result


def add_real(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--phase-a", type=Path, required=True)
    parser.add_argument("--r2", type=Path, required=True)
    parser.add_argument("--legacy-gates", type=Path, required=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    item = sub.add_parser("audit")
    item.add_argument("--contract", type=Path, required=True)
    item.add_argument("--r3c-contract", type=Path, required=True)
    item.add_argument("--chain-map", type=Path, required=True)
    item.add_argument("--r3c-formal", type=Path, required=True)
    add_real(item)
    item.add_argument("--output", type=Path, required=True)
    item = sub.add_parser("synthetic")
    item.add_argument("--contract", type=Path, required=True)
    item.add_argument("--output", type=Path, required=True)
    item = sub.add_parser("formal")
    item.add_argument("--contract", type=Path, required=True)
    item.add_argument("--chain-map", type=Path, required=True)
    item.add_argument("--audit", type=Path, required=True)
    item.add_argument("--synthetic", type=Path, required=True)
    add_real(item)
    item.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args) if args.command == "audit" else synthetic(args) if args.command == "synthetic" else formal(args)
    print(json.dumps(clean(result), sort_keys=True))
    return 0 if result.get("pass", result.get("pass_for_replacement_implementation", True)) else 2


if __name__ == "__main__":
    raise SystemExit(main())
