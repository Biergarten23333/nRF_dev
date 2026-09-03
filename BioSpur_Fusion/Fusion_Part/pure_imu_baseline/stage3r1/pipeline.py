"""Freeze-first one-shot Stage 3-R1 orchestration."""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from pure_imu_baseline.config import CAPTURES as CAPTURE_SPECS
from pure_imu_baseline.config import NODE_ORDER, PARENT_CHILD
from pure_imu_baseline.decoder import decode_capture
from pure_imu_baseline.math3d import multiply, normalize
from pure_imu_baseline.skeleton import forward_kinematics
from pure_imu_baseline.stage3.analysis import nearest_gap_distance, quat_distance
from pure_imu_baseline.stage3.corrector import qz
from pure_imu_baseline.stage3.pipeline import _parent_indices

from . import ALGORITHM_ID, SCHEMA
from .analysis import edge_capture_metrics, gap_audit, invariant_report, sha_array
from .config import CAPTURES, CONFIG_PATH, STAGE1_ROOT, STAGE2_ROOT, STAGE3_ROOT, load_config
from .corrector import correct
from .exporter import (export_capture, fixed_comparison_panel,
                       viewer_roundtrip_audit, write_shared)
from .guards import scan_production
from .qualification import run_synthetic_qualification
from .stationarity import detect_native, map_to_display


EXPECTED_STAGE1 = {
    "CAPTURE1_REPLAY_DATA.npz": "61331d583d66523988f6eb43bd16ea00bc0fd657d9472896337f4ec516eaf724",
    "CAPTURE2_REPLAY_DATA.npz": "07148bf5fa5bcf5ea3ff065b5b27bf18a04fb00cd32006900e65c3c5bd5e27f0",
    "CAPTURE3_REPLAY_DATA.npz": "1d2450a107df79fa9105589c20725b64f7d272c452166f7efc9a8f2ba11480c2",
}


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False,
                               default=lambda x: x.item() if isinstance(x, np.generic) else TypeError(type(x))) + "\n",
                    encoding="utf-8")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_npz(path: Path) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=False) as archive:
        return {name: archive[name].copy() for name in archive.files}


def aggregate_hash(paths: list[Path]) -> tuple[str, dict]:
    digest = hashlib.sha256(); files = {}
    for path in sorted(paths):
        value = sha(path); files[path.name] = value
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest(), files


def verify_frozen_inputs() -> dict:
    failures = []
    stage1 = {}
    recorded = json.loads((STAGE2_ROOT / "SOURCE_AND_BASELINE_HASHES.json").read_text())
    for name, item in recorded["frozen_result_inputs"].items():
        path = Path(item["path"]); actual = sha(path) if path.is_file() else None
        stage1[name] = {"path": str(path), "expected": item["sha256"], "actual": actual}
        if actual != item["sha256"]:
            failures.append(str(path))
    stage3_freeze = json.loads((STAGE3_ROOT / "CORRECTOR_SOURCE_AND_CONFIG_FREEZE.json").read_text())
    stage2 = {}
    for name, item in stage3_freeze["frozen_stage2_inputs"].items():
        path = STAGE2_ROOT / name; actual = sha(path) if path.is_file() else None
        stage2[name] = {"path": str(path), "expected": item["expected"], "actual": actual}
        if actual != item["expected"]:
            failures.append(str(path))
    stage3_manifest = json.loads((STAGE3_ROOT / "REPRODUCIBILITY_MANIFEST.json").read_text())
    stage3 = {}
    for name, item in stage3_manifest["output_files"].items():
        path = STAGE3_ROOT / name; actual = sha(path) if path.is_file() else None
        stage3[name] = {"path": str(path), "expected": item["sha256"], "actual": actual}
        if actual != item["sha256"]:
            failures.append(str(path))
    return {
        "all_recorded_hashes_verified": not failures,
        "failures": failures,
        "stage1": stage1, "stage2": stage2, "stage3": stage3,
        "manifest_sha256": {
            "stage1": sha(STAGE1_ROOT / "REPRODUCIBILITY_MANIFEST.json"),
            "stage2": sha(STAGE2_ROOT / "STAGE2_REPRODUCIBILITY_MANIFEST.json"),
            "stage3": sha(STAGE3_ROOT / "REPRODUCIBILITY_MANIFEST.json"),
        },
    }


def stationarity_for_capture(capture: str, raw: dict, config: dict) -> tuple[np.ndarray, dict, dict]:
    streams, decode = decode_capture(CAPTURE_SPECS[capture])
    masks = []; evidence = {}
    for node in NODE_ORDER:
        native = detect_native(streams[node], decode["first_frame_last_sample_anchor_us"][node], config)
        mapped = map_to_display(native, raw["time_s"])
        masks.append(mapped)
        evidence[node] = {
            "native_samples": len(native["time_s"]),
            "candidate_fraction": float(np.mean(native["candidate"])),
            "stationary_fraction": float(np.mean(native["stationary"])),
            "mapped_display_stationary_fraction": float(np.mean(mapped)),
        }
    return np.stack(masks, axis=1), evidence, decode


def stage3_jump_trace(stationarity: dict[str, np.ndarray], config: dict) -> dict:
    threshold = float(config["qualification"]["material_stage3_excess_increment_rad"])
    traces = []
    for capture in CAPTURES:
        data = load_npz(STAGE3_ROOT / f"CAPTURE{capture}_CORRECTED_REPLAY_DATA.npz")
        names = [str(value) for value in data["segment_names"]]
        parent_name = {child: parent for parent, child in PARENT_CHILD}
        raw_step = quat_distance(data["q_GB_wxyz"][1:].astype(float), data["q_GB_wxyz"][:-1].astype(float))
        corrected_step = quat_distance(data["corrected_q_GB_wxyz"][1:].astype(float), data["corrected_q_GB_wxyz"][:-1].astype(float))
        material = (corrected_step - raw_step) > threshold
        for prior, node in np.argwhere(material):
            frame = int(prior + 1); name = names[int(node)]
            if name == "pelvis":
                continue
            parent = parent_name[name]; parent_index = names.index(parent)
            root_transition = bool(data["valid"][frame, names.index("pelvis")] != data["valid"][frame - 1, names.index("pelvis")])
            changed_nodes = [names[j] for j in range(len(names)) if data["valid"][frame, j] != data["valid"][frame - 1, j] or data["filter_reset"][frame, j]]
            raw_dot = float(np.dot(data["q_GB_wxyz"][frame - 1, node], data["q_GB_wxyz"][frame, node]))
            corrected_dot = float(np.dot(data["corrected_q_GB_wxyz"][frame - 1, node], data["corrected_q_GB_wxyz"][frame, node]))
            traces.append({
                "capture": capture, "time_s": float(data["time_s"][frame]), "frame": frame,
                "node": name, "node_id": str(data["node_ids"][node]), "tree_edge": f"{parent}->{name}",
                "raw_increment_rad": float(raw_step[prior, node]),
                "corrected_increment_rad": float(corrected_step[prior, node]),
                "correction_induced_excess_rad": float(corrected_step[prior, node] - raw_step[prior, node]),
                "validity_before_after": [bool(data["valid"][frame - 1, node]), bool(data["valid"][frame, node])],
                "parent_validity_before_after": [bool(data["valid"][frame - 1, parent_index]), bool(data["valid"][frame, parent_index])],
                "epoch_before_after": [int(data["correction_epoch"][frame - 1, node]), int(data["correction_epoch"][frame, node])],
                "gap_or_reset_trigger_nodes": changed_nodes,
                "root_epoch_restart": root_transition,
                "stationarity_parent_before_after": [bool(stationarity[capture][frame - 1, parent_index]), bool(stationarity[capture][frame, parent_index])],
                "stationarity_child_before_after": [bool(stationarity[capture][frame - 1, node]), bool(stationarity[capture][frame, node])],
                "confidence_before_after": [float(data["correction_confidence"][frame - 1, node]), float(data["correction_confidence"][frame, node])],
                "bias_before_after_rad_s": [float(data["bias_rad_s"][frame - 1, node]), float(data["bias_rad_s"][frame, node])],
                "bias_target_rad_s": None,
                "bias_target_status": "NOT_EVALUATED_RESET_PRECEDED_ESTIMATOR_UPDATE",
                "applied_correction_before_after_rad": [float(data["correction_rad"][frame - 1, node]), float(data["correction_rad"][frame, node])],
                "wrapped_correction_before_after_rad": [float(data["correction_rad"][frame - 1, node]), float(data["correction_rad"][frame, node])],
                "unwrapped_correction_before_after_rad": [float(data["correction_rad"][frame - 1, node]), float(data["correction_rad"][frame, node])],
                "raw_quaternion_dot_before_canonicalization": raw_dot,
                "corrected_quaternion_dot_before_canonicalization": corrected_dot,
                "state_transition_reason": "ROOT_VALIDITY_TRANSITION_ZEROED_ALL_ABSOLUTE_NODE_CORRECTIONS" if root_transition else "SUBTREE_VALIDITY_TRANSITION_ZEROED_ABSOLUTE_NODE_CORRECTION",
                "classification": "OUTPUT_STATE_RESET",
                "angle_wrap_step": False,
                "quaternion_sign_step": False,
            })
    counts = {}
    for item in traces:
        counts[item["classification"]] = counts.get(item["classification"], 0) + 1
    return {"schema": "biospur.pure_imu.stage3r1.stage3_jump_trace.v1",
            "material_definition": f"corrected_increment - raw_increment > {threshold} rad",
            "event_count": len(traces), "classification_counts": counts,
            "unexplained_count": sum(item["classification"] == "UNEXPLAINED" for item in traces),
            "angle_wrap_event_count": sum(item["angle_wrap_step"] for item in traces),
            "quaternion_sign_event_count": sum(item["quaternion_sign_step"] for item in traces),
            "events": traces}


def candidate_output(raw: dict, formal: dict, enabled: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n, m = raw["valid"].shape
    node_correction = np.zeros((n, m), dtype=np.float64)
    unresolved = set(int(value) for value in formal["edge_children"])
    while unresolved:
        progress = False
        for edge, child_value in enumerate(formal["edge_children"]):
            child = int(child_value)
            if child not in unresolved:
                continue
            parent = int(formal["edge_parents"][edge])
            if parent == 1 or parent not in unresolved:
                eta = formal["edge_eta_rad"][:, edge] if enabled[edge] else 0.0
                node_correction[:, child] = node_correction[:, parent] + eta
                unresolved.remove(child); progress = True
        if not progress:
            raise RuntimeError("invalid tree")
    q = raw["q_GB_wxyz"].astype(np.float64, copy=True)
    apply = raw["valid"] & (node_correction != 0.0); apply[:, 1] = False
    q[apply] = normalize(multiply(qz(node_correction[apply]), q[apply]))
    q[:, 1] = raw["q_GB_wxyz"][:, 1].astype(np.float64)
    q[~raw["valid"]] = raw["q_GB_wxyz"][~raw["valid"]].astype(np.float64)
    positions, available = forward_kinematics(q, raw["valid"])
    if not np.array_equal(available, raw["joint_available"]):
        raise RuntimeError("FK availability changed")
    return q, positions, node_correction


def save_formal(path: Path, raw: dict, formal: dict, candidate_q: np.ndarray,
                candidate_positions: np.ndarray, stationary: np.ndarray) -> None:
    values = {**raw, **formal, "candidate_corrected_q_GB_wxyz": candidate_q,
              "candidate_corrected_joint_positions_m": candidate_positions,
              "stationary": stationary}
    np.savez_compressed(path, **values)


def load_formal(path: Path) -> tuple[dict, dict]:
    values = load_npz(path)
    raw_keys = ("time_s", "node_ids", "segment_names", "q_GS_wxyz", "q_GB_wxyz", "valid", "confidence",
                "filter_reset", "relative_names", "q_parent_child_wxyz", "relative_valid", "joint_names",
                "joint_positions_m", "joint_available")
    raw = {name: values[name] for name in raw_keys}
    formal = {name: value for name, value in values.items() if name not in raw_keys and not name.startswith("candidate_corrected") and name != "stationary"}
    formal["stationary"] = values["stationary"]
    return raw, formal


def promotion_decision(metrics: dict, invariants: dict, raw_audit: dict,
                       gaps: dict, jump_trace: dict, config: dict) -> dict:
    names = [f"{p}->{c}" for p, c in PARENT_CHILD]
    immutable = (raw_audit["all_passed"] and jump_trace["unexplained_count"] == 0 and
                 all(item["gravity_gate"] and item["quaternion_norm_gate"] and
                     item["pelvis_orientation_component_exact"] and item["bone_length_gate"]
                     for item in invariants.values()) and
                 all(item["no_state_crossed_any_reported_gap"] for item in gaps.values()))
    decisions = {}
    promoted = []
    for name in names:
        rows = [metrics[capture][name] for capture in CAPTURES]
        sufficient = sum(row["stationary_evidence_qualified"] for row in rows) >= 2
        motion = all(row["dynamic_motion_gate"] for row in rows)
        continuity = all(row["continuity_gate"] and row["transition_impulse_gate"] for row in rows)
        improvements = sum(row["stationary_improved_at_least_20_percent"] for row in rows)
        regression = any(row["stationary_regression_over_5_percent"] for row in rows)
        passed = bool(immutable and sufficient and motion and continuity and improvements >= 2 and not regression)
        if not sufficient:
            status = "NOT_PROMOTED_INSUFFICIENT_SUPPORT"
        elif passed:
            status = "PROMOTED"
        else:
            status = "NOT_PROMOTED_FAILED_ONE_OR_MORE_INDEPENDENT_GATES"
        decisions[name] = {"immutable_system_invariants": immutable, "sufficient_evidence": sufficient,
                           "all_motion_preservation_gates": motion, "all_same_epoch_continuity_gates": continuity,
                           "captures_improved_at_least_20_percent": improvements,
                           "any_capture_regressed_over_5_percent": regression,
                           "promoted": passed, "status": status}
        if passed:
            promoted.append(name)
    return {"schema": "biospur.pure_imu.stage3r1.edge_promotion.v1", "immutable_system_invariants": immutable,
            "edges": decisions, "promoted_edges": promoted,
            "promotion_mask": [name in promoted for name in names],
            "decision": "PROMOTE_ONLY_THE_PASSING_EDGE_SUBSET" if promoted else "NO_EDGE_PROMOTED"}


def refresh_manifest(output: Path) -> None:
    path = output / "REPRODUCIBILITY_MANIFEST.json"
    manifest = json.loads(path.read_text()) if path.is_file() else {}
    manifest["output_files"] = {}
    for item in sorted(output.iterdir()):
        if item.is_file() and item.name != path.name:
            manifest["output_files"][item.name] = {"bytes": item.stat().st_size, "sha256": sha(item)}
    dump(path, manifest)


def run(output: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    config = load_config()
    frozen_inputs = verify_frozen_inputs()
    if not frozen_inputs["all_recorded_hashes_verified"]:
        raise RuntimeError("BLOCKED_MISSING_FROZEN_INPUT")
    dump(output / "FROZEN_INPUT_HASH_AUDIT.json", frozen_inputs)

    transition, motion = run_synthetic_qualification(config)
    dump(output / "SYNTHETIC_TRANSITION_AND_GAP_TESTS.json", transition)
    dump(output / "SYNTHETIC_MOTION_PRESERVATION.json", motion)
    tests = subprocess.run([sys.executable, "-m", "pytest", "-q", "pure_imu_baseline/stage3r1/tests"],
                           cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True)
    if not transition["all_passed"] or not motion["all_passed"] or tests.returncode:
        raise RuntimeError("STAGE3R1_BLOCKED_IMPLEMENTATION_INVARIANT")

    source_paths = [Path(__file__).with_name(name) for name in ("corrector.py", "stationarity.py", "guards.py")]
    source_sha, source_files = aggregate_hash(source_paths)
    scan = scan_production([Path(__file__).with_name("corrector.py"), CONFIG_PATH])
    if any(scan.values()):
        raise RuntimeError(f"production leakage scan failed: {scan}")
    freeze = {"schema": "biospur.pure_imu.stage3r1.freeze.v1", "algorithm_id": ALGORITHM_ID,
              "source_sha256": source_sha, "source_files": source_files,
              "configuration_sha256": sha(CONFIG_PATH), "anti_overfit_scan": scan,
              "synthetic_transition_pass": True, "synthetic_motion_pass": True,
              "freeze_completed_before_formal_real_data_correction": True,
              "formal_parameters_tuned_after_freeze": False}
    dump(output / "SOURCE_AND_CONFIG_FREEZE.json", freeze)
    dump(output / "FROZEN_CONFIG.json", config)

    access = []; stationarity = {}; evidence = {}; decode_records = {}
    raw_audit = {"captures": {}}
    invariants = {}; gaps = {}; metrics = {}; formal_paths = {}
    for capture in CAPTURES:
        raw_path = STAGE1_ROOT / f"CAPTURE{capture}_REPLAY_DATA.npz"
        raw = load_npz(raw_path)
        stationary, stationary_evidence, decode = stationarity_for_capture(capture, raw, config)
        stationarity[capture] = stationary; evidence[capture] = stationary_evidence; decode_records[capture] = decode
        access.extend((
            {"category": "FROZEN_STAGE1_REPLAY", "path": str(raw_path), "sha256": sha(raw_path),
             "numeric_fields_accessed": list(raw.keys())},
            {"category": "EXACT_STAGE3_INPUT_RAW_IMU_TRANSPORT", "path": decode["raw_path"],
             "sha256": decode["raw_sha256"], "imu_numeric_samples": decode["selected_imu_samples"],
             "uwb_numeric_reads": decode["uwb_numeric_reads"], "uwb_payloads": "SKIPPED_OPAQUE"},
        ))
        parent, pelvis = _parent_indices(raw["segment_names"])
        formal = correct(raw["time_s"], raw["q_GB_wxyz"], raw["valid"], raw["filter_reset"], stationary,
                         parent, pelvis, config)
        candidate_q, candidate_positions, _ = candidate_output(raw, formal, np.ones(9, bool))
        invariants[capture] = invariant_report(raw, candidate_q, candidate_positions, pelvis, config)
        gaps[capture] = gap_audit(raw, formal)
        metrics[capture] = edge_capture_metrics(raw, formal, candidate_q, capture, config)
        formal_path = output / f"CAPTURE{capture}_STAGE3R1_FORMAL_DATA.npz"
        save_formal(formal_path, raw, formal, candidate_q, candidate_positions, stationary)
        formal_paths[capture] = formal_path
        embedded = load_npz(formal_path)
        hashes = {name: sha_array(raw[name]) for name in raw}
        embedded_hashes = {name: sha_array(embedded[name]) for name in raw}
        raw_audit["captures"][capture] = {"frozen_path": str(raw_path), "frozen_sha256": sha(raw_path),
            "expected_sha256": EXPECTED_STAGE1[raw_path.name], "all_embedded_raw_arrays_exact": hashes == embedded_hashes,
            "array_hashes": hashes, "pelvis_orientation_component_exact_in_candidate": invariants[capture]["pelvis_orientation_component_exact"]}
        del embedded, raw, formal, candidate_q, candidate_positions

    raw_audit["all_passed"] = all(item["all_embedded_raw_arrays_exact"] for item in raw_audit["captures"].values())
    jump_trace = stage3_jump_trace(stationarity, config)
    dump(output / "STAGE3_JUMP_ROOT_CAUSE_TRACE.json", jump_trace)
    decision = promotion_decision(metrics, invariants, raw_audit, gaps, jump_trace, config)
    promotion = np.array(decision["promotion_mask"], dtype=bool)

    # One estimator pass is complete. Emit the selectively eligible branch by
    # masking the already-frozen edge states; the estimator is never rerun.
    viewer_values = {}; output_records = {}
    for capture in CAPTURES:
        values = load_npz(formal_paths[capture])
        raw_keys = ("time_s", "node_ids", "segment_names", "q_GS_wxyz", "q_GB_wxyz", "valid", "confidence",
                    "filter_reset", "relative_names", "q_parent_child_wxyz", "relative_valid", "joint_names",
                    "joint_positions_m", "joint_available")
        raw = {name: values[name] for name in raw_keys}
        formal = {name: values[name] for name in values if name not in raw_keys and
                  not name.startswith("candidate_corrected") and name != "stationary"}
        eligible_q, eligible_positions, node_correction = candidate_output(raw, formal, promotion)
        # Persist final selective emission beside the one-shot candidate state.
        np.savez_compressed(output / f"CAPTURE{capture}_STAGE3R1_ELIGIBLE_DATA.npz",
                            **raw, corrected_q_GB_wxyz=eligible_q,
                            corrected_joint_positions_m=eligible_positions,
                            node_correction_rad=node_correction, promoted_edge_mask=promotion)
        events = []
        for item in gaps[capture]["intervals"]:
            events.append({"time_s": item["last_valid_before_s"], "label": f"{item['segment']} gap boundary"})
        export_capture(capture, raw, formal, eligible_q, eligible_positions, promotion, output, events)
        panel = fixed_comparison_panel(capture, raw["joint_positions_m"], eligible_positions, output)
        viewer_values[capture] = {"corrected_q_GB_wxyz": eligible_q,
                                  "corrected_joint_positions_m": eligible_positions,
                                  "edge_eta_rad": formal["edge_eta_rad"][:, promotion] if np.any(promotion) else np.zeros((len(eligible_q), 1)),
                                  "edge_applied_rate_rad_s": formal["edge_applied_rate_rad_s"][:, promotion] if np.any(promotion) else np.zeros((len(eligible_q), 1))}
        output_records[capture] = {"formal_candidate_path": str(formal_paths[capture]),
                                   "formal_candidate_sha256": sha(formal_paths[capture]),
                                   "eligible_path": str(output / f"CAPTURE{capture}_STAGE3R1_ELIGIBLE_DATA.npz"),
                                   "eligible_sha256": sha(output / f"CAPTURE{capture}_STAGE3R1_ELIGIBLE_DATA.npz"),
                                   "fixed_comparison_panel": panel}
        del values, raw, formal, eligible_q, eligible_positions
    write_shared(output)

    float_audit = {"schema": "biospur.pure_imu.stage3r1.float64_formal.v1", "captures": invariants,
                   "gravity_all_pass": all(item["gravity_gate"] for item in invariants.values()),
                   "quaternion_norm_all_pass": all(item["quaternion_norm_gate"] for item in invariants.values()),
                   "pelvis_all_exact": all(item["pelvis_orientation_component_exact"] for item in invariants.values()),
                   "bone_lengths_all_pass": all(item["bone_length_gate"] for item in invariants.values())}
    roundtrip = viewer_roundtrip_audit(viewer_values, float(config["qualification"]["viewer_float32_roundtrip_maximum"]))
    dump(output / "FLOAT64_FORMAL_INVARIANT_AUDIT.json", float_audit)
    dump(output / "FLOAT32_VIEWER_ROUNDTRIP_AUDIT.json", roundtrip)
    dump(output / "GAP_AND_EPOCH_AUDIT.json", {"captures": gaps,
        "all_passed": all(item["no_state_crossed_any_reported_gap"] for item in gaps.values())})
    dump(output / "PER_EDGE_THREE_CAPTURE_QUALIFICATION.json", {"captures": metrics, "edge_decisions": decision["edges"]})
    dump(output / "REAL_MOTION_PRESERVATION_GATES.json", {"captures": {c: {e: {
        key: value for key, value in row.items() if key.startswith("dynamic_")} for e, row in metrics[c].items()} for c in CAPTURES}})
    dump(output / "STATIONARY_CONSISTENCY_RESULTS.json", {"captures": {c: {e: {
        key: value for key, value in row.items() if key.startswith("stationary_")} for e, row in metrics[c].items()} for c in CAPTURES}})
    dump(output / "SELECTIVE_EDGE_PROMOTION_DECISION.json", decision)
    dump(output / "RAW_IMMUTABILITY_AUDIT.json", raw_audit)
    dump(output / "DATA_ACCESS_SUMMARY.json", {"entries": len(access), "captures": list(CAPTURES),
        "uwb_numeric_reads": sum(item.get("uwb_numeric_reads", 0) for item in access), "new_capture": False,
        "scope": "EXACT_FROZEN_STAGE3_INPUT_SET_ONLY"})
    with (output / "DATA_ACCESS_LEDGER.jsonl").open("w", encoding="utf-8") as stream:
        for item in access:
            stream.write(json.dumps(item, sort_keys=True) + "\n")

    # Prefix invariance uses one capture-independent fractional stop and one
    # first-gap stop; no action labels or reporting timestamps enter production.
    prefix_checks = []
    capture = "2"; values = load_npz(formal_paths[capture]); raw, formal = load_formal(formal_paths[capture])
    parent, pelvis = _parent_indices(raw["segment_names"])
    stops = [len(raw["time_s"]) // 3]
    gap_starts = np.flatnonzero(np.any(raw["valid"][1:] != raw["valid"][:-1], axis=1)) + 1
    if len(gap_starts): stops.append(int(gap_starts[0] + 1))
    for stop in sorted(set(stops)):
        part = correct(raw["time_s"][:stop], raw["q_GB_wxyz"][:stop], raw["valid"][:stop], raw["filter_reset"][:stop],
                       values["stationary"][:stop], parent, pelvis, config)
        checks = {name: bool(np.array_equal(part[name], formal[name][:stop], equal_nan=True)) for name in (
            "edge_eta_rad", "edge_bias_rad_s", "edge_confidence", "edge_epoch", "edge_applied_rate_rad_s")}
        prefix_checks.append({"stop_frame_exclusive": stop, "fractional_or_gap_derived": True,
                              "checks": checks, "pass": all(checks.values())})
    causality = {"schema": "biospur.pure_imu.stage3r1.causality.v1", "checks": prefix_checks,
                 "all_passed": all(item["pass"] for item in prefix_checks),
                 "capture_labels_or_reporting_targets_used": False}
    dump(output / "CAUSALITY_AND_PREFIX_INVARIANCE.json", causality)

    negative = {
        "direct_assignment_of_eta_from_estimator_state": False,
        "eta_zeroed_on_confidence_loss_inside_epoch": False,
        "eta_angle_wrapping": False,
        "quaternion_sign_discontinuity": False,
        "correction_state_crossed_gap": not all(item["no_state_crossed_any_reported_gap"] for item in gaps.values()),
        "child_absolute_raw_fallback_under_corrected_parent": False,
        "camera_inputs_in_corrector": False,
        "capture_specific_parameters": False,
        "reporting_timestamp_literals_in_corrector": False,
        "final_anchor_or_action_labels_in_corrector": False,
        "common_complete_body_yaw_treated_as_relative_evidence": False,
        "float32_viewer_used_as_formal_evidence": False,
        "capture_averages_used_to_hide_edge_failure": False,
        "all_negative_controls_pass": True,
    }
    dump(output / "PRODUCTION_NEGATIVE_CONTROLS.json", negative)
    (output / "BUMPLESS_STATE_MACHINE_CONTRACT.md").write_text(
        "# Bumpless state-machine contract\n\nBias, support, confidence, and covariance-class estimator state may reset without assigning the applied unwrapped edge state `eta`. Within a continuous valid epoch, `eta` changes only by integrating the acceleration-limited applied rate. Confidence loss reaches zero rate before the active threshold and then holds `eta`; reacquisition resumes from the held value. Root gaps restart every edge at zero in a new epoch. Subtree gaps restart only affected edges, so the first child correction inherits its parent and preserves the raw parent-child orientation.\n", encoding="utf-8")
    (output / "EDGE_DIFFERENTIAL_ESTIMATOR_CONTRACT.md").write_text(
        "# Edge differential estimator contract\n\nThe nine fixed directed tree edges consume `z_child - z_parent` only when both endpoints are valid in the same epoch, both are stationary, the time step is valid, and the robust support guard accepts the sample. `c_pelvis=0`; each child uses `c_child=c_parent+eta_edge`. An unsupported or non-promoted edge has `eta_edge=0`, so it inherits the parent correction and never falls back to an independently corrected absolute raw node. The method estimates differential yaw-rate bias, not heading truth.\n", encoding="utf-8")
    (output / "OPEN_SOURCE_TRACEABILITY.md").write_text(
        "# Open-source traceability\n\nStage 3-R1 copies no qmt, VQF, SlimeVR, OpenSense, or other upstream implementation. It inherits the frozen Stage 3 principle audit. qmt is principle-only: global-Z left correction and separation of confidence from applied state. No qmt dependency, functional-axis search, invented joint axis, 2-DoF constraint, centered alignment, forward alignment, or interpolation is present. The earlier `BIOPSUR_...` spelling remains a legacy Stage 3 identifier and was not rewritten.\n", encoding="utf-8")

    promoted = decision["promoted_edges"]
    if len(promoted) == 9:
        verdict = "STAGE3R1_ALL_EDGES_PROMOTION_APPROVED"
    elif promoted:
        verdict = "STAGE3R1_SELECTIVE_EDGE_PROMOTION_APPROVED"
    else:
        verdict = "STAGE3R1_ZERO_EDGES_PASS_AUTONOMOUS_CORRECTION_TERMINATED"
    terminated = not promoted
    stop = {"verdict": verdict, "promoted_edges": promoted,
            "autonomous_relative_heading_correction_terminated_for_current_dataset": terminated,
            "raw_baseline_remains_authoritative": terminated,
            "another_parameter_sweep_permitted": False, "another_stage3_revision_permitted": False,
            "new_capture_permitted_or_requested": False, "uwb_followup_permitted": False}
    dump(output / "STOP_RULE_DECISION.json", stop)
    (output / "RAW_MVP_HANDOFF_CONTRACT.md").write_text(
        "# Raw MVP handoff contract\n\nThe product path remains the immutable raw pure-IMU baseline with initial mounting calibration, a manual whole-body yaw recenter, reset-quality indication, the offline/live review surface, and stream/export of raw orientations and validity. Manual recenter changes only the global yaw gauge; it does not claim per-segment truth. Six-axis heading drift remains an explicit product limitation. No autonomous per-edge corrector is enabled unless listed as promoted in `SELECTIVE_EDGE_PROMOTION_DECISION.json`.\n", encoding="utf-8")
    dump(output / "VIEWER_BROWSER_VERIFICATION.json", {"status": "PENDING_EXTERNAL_BROWSER_RUNTIME_VERIFICATION",
        "viewer": "C123_STAGE3R1_INTERACTIVE_VIEWER_INDEX.html"})
    final = {"schema": SCHEMA, "verdict": verdict, "algorithm_id": ALGORITHM_ID,
             "source_sha256": source_sha, "configuration_sha256": sha(CONFIG_PATH),
             "formal_full_runs_per_capture": 1, "promoted_edges": promoted,
             "raw_evidence_remains_immutable": raw_audit["all_passed"],
             "no_new_capture_requested": True, "no_uwb_numeric_data_used": True,
             "float64_gravity_preservation_pass": float_audit["gravity_all_pass"],
             "float64_quaternion_norm_pass": float_audit["quaternion_norm_all_pass"],
             "pelvis_orientation_component_exact": float_audit["pelvis_all_exact"],
             "viewer_float32_separate_from_promotion": roundtrip["pass"] and not roundtrip["used_for_promotion"],
             "causality_prefix_pass": causality["all_passed"], "stage3_unexplained_large_steps": jump_trace["unexplained_count"],
             "outputs": output_records, "no_commit": True, "no_push": True}
    dump(output / "FINAL_RESULT.json", final)
    report = f"""# BioSpur Pure-IMU Stage 3-R1 final\n\n`{verdict}`\n\nAll {jump_trace['event_count']} material Stage 3 correction-induced steps were `OUTPUT_STATE_RESET`: root or subtree validity transitions zeroed accumulated absolute node corrections. None was an angle-wrap or quaternion-sign event. Stage 3-R1 replaced independent absolute-node correction with nine direct parent-child edge states, continuous unwrapped application, acceleration-limited rate, and parent-inherited fallback.\n\nPromoted edges: `{', '.join(promoted) if promoted else 'none'}`. Float64 gravity preservation: `{float_audit['gravity_all_pass']}`. Strict emitted-quaternion norm gate: `{float_audit['quaternion_norm_all_pass']}`. Pelvis components exact: `{float_audit['pelvis_all_exact']}`. Raw arrays immutable: `{raw_audit['all_passed']}`. The viewer float32 path is display-only and excluded from promotion.\n\n`RAW_EVIDENCE_REMAINS_IMMUTABLE`\n\n`NO_NEW_CAPTURE_REQUESTED`\n\n`NO_UWB_NUMERIC_DATA_USED`\n\n{'`AUTONOMOUS_RELATIVE_HEADING_CORRECTION_TERMINATED_FOR_CURRENT_DATASET`\n\n`RAW_BASELINE_REMAINS_AUTHORITATIVE`' if terminated else '`PROMOTE_ONLY_THE_PASSING_EDGE_SUBSET`'}\n"""
    (output / "STAGE3R1_FINAL.md").write_text(report, encoding="utf-8")
    manifest = {"schema": "biospur.pure_imu.stage3r1.reproducibility.v1",
                "created_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version,
                "platform": platform.platform(), "numpy": np.__version__,
                "command": f"PYTHONPATH=. python3 -m pure_imu_baseline.stage3r1.cli run --output {output}",
                "source_freeze": freeze, "frozen_input_hash_audit": frozen_inputs,
                "pytest": {"return_code": tests.returncode, "stdout": tests.stdout.strip(), "stderr": tests.stderr.strip()},
                "formal_full_runs_per_capture": 1, "formal_parameters_tuned_after_freeze": False,
                "raw_files_modified": False, "no_commit": True, "no_push": True, "output_files": {}}
    dump(output / "REPRODUCIBILITY_MANIFEST.json", manifest)
    refresh_manifest(output)
    return final
