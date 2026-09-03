#!/usr/bin/env python3
"""Run progressive synthetic qualification or one bounded real capture."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import run_pure_imu_v0_physical_graph as physical_runner
import run_pure_imu_v0_raw6_heading as legacy

from biospur_fusion.v0.contracts import dump_json, sha256_file
from biospur_fusion.v0.dual_capture import load_protocol
from biospur_fusion.v0.physical_graph import real_subject_spec
from biospur_fusion.v0.progressive_calibration import (
    HEADING_DIMENSION,
    REAL_SUBJECT_PROGRESSIVE_GEOMETRY_PRIOR,
    b5_only_factors,
    classify_episode,
    final_gates,
    factor_partition_identity,
    held_out_report,
    information_snapshot,
    physical_state_report,
    readiness_snapshot,
    select_factor_actions,
    solve_cumulative,
)
from biospur_fusion.v0.progressive_synthetic import qualify_progressive_synthetic
from biospur_fusion.v0.raw6_heading import build_edge_factors, wrap


RUN_REL = Path("logs/pure_imu_v0_progressive_calibration_20260828T132736Z")
PRESELECTION_SHA256 = "dd9867e999fe63dd0f25fe4d645d5c17bdc8e799c841664df3ca083c5c7db2bf"
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


def _preselection(root: Path) -> dict[str, Any]:
    path = root / RUN_REL / "METADATA_PRESELECTION.json"
    if sha256_file(path) != PRESELECTION_SHA256 or path.stat().st_mode & 0o222:
        raise RuntimeError("progressive preselection is absent, changed, or writable")
    payload = json.loads(path.read_text(encoding="utf-8"))
    authority_row = payload["selection_authority"]
    authority = root / authority_row.get(
        "path", authority_row.get("root_exact_selection_path"),
    )
    expected = authority_row.get(
        "sha256", authority_row.get("root_exact_selection_sha256"),
    )
    if sha256_file(authority) != expected:
        raise RuntimeError("progressive selection authority changed")
    return payload


def _frozen_selection(root: Path, preselection: Mapping[str, Any]) -> dict[str, Any]:
    selection = legacy._selection(root)
    for capture, row in preselection["captures"].items():
        exact = selection["captures"][capture]
        projected = [
            {"action": item["action"], "attempt": item["attempt"], "partition": item["partition"]}
            for item in exact["selected_actions"]
        ]
        expected_order = row.get("ordered_episodes")
        if expected_order is None and "ordered_action_attempt_partition" in row:
            role = {"TRAIN": "IDENTIFICATION_TRAIN", "HELD_OUT": "HELD_OUT_VALIDATION"}
            expected_order = []
            for token in row["ordered_action_attempt_partition"]:
                action, attempt, partition = token.rsplit(":", 2)
                expected_order.append({
                    "action": action, "attempt": int(attempt),
                    "partition": role[partition],
                })
        if expected_order is None:
            action_attempt = [
                {"action": token.rsplit(":", 1)[0], "attempt": int(token.rsplit(":", 1)[1])}
                for token in row["order"]
            ]
            if [
                {"action": item["action"], "attempt": item["attempt"]}
                for item in projected
            ] != action_attempt:
                raise RuntimeError(f"{capture}: progressive episode action/attempt order changed")
            expected_order = projected
        if projected != expected_order:
            raise RuntimeError(f"{capture}: progressive episode order changed")
        if exact["capture_id"] != row["capture_id"]:
            raise RuntimeError(f"{capture}: progressive capture identity changed")
    return selection


def _require_synthetic(run_dir: Path) -> dict[str, Any]:
    path = run_dir / SYNTHETIC_NAME
    if not path.exists() or path.stat().st_mode & 0o222:
        raise RuntimeError("immutable progressive synthetic qualification is absent")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not payload.get("pass"):
        raise RuntimeError("progressive synthetic prerequisite did not pass")
    return {"path": str(path), "sha256": sha256_file(path), "pass": True}


def _write_synthetic(run_dir: Path, max_nfev: int, wall_limit_s: float) -> None:
    output = run_dir / SYNTHETIC_NAME
    if output.exists():
        raise FileExistsError(output)
    result = qualify_progressive_synthetic(
        max_nfev=max_nfev, wall_limit_s=wall_limit_s,
    )
    snapshot_dir = run_dir / "SYNTHETIC_PROGRESSIVE_SNAPSHOTS"
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    snapshots = result.pop("progressive_snapshots")
    index = []
    for row in snapshots:
        path = snapshot_dir / f"STEP_{row['step']:02d}_{row['episode']}.json"
        dump_json(path, _jsonable(row)); path.chmod(0o444)
        index.append({
            "step": row["step"], "episode": row["episode"],
            "path": str(path), "sha256": sha256_file(path),
        })
    result["progressive_snapshot_index"] = index
    dump_json(output, _jsonable(result)); output.chmod(0o444)
    print(json.dumps({
        "output": str(output), "pass": result["pass"],
        "qualification_gates": result["qualification_gates"],
        "recovery": result["recovery"],
    }, indent=2, sort_keys=True), flush=True)
    if not result["pass"]:
        raise SystemExit(2)


def _prepare_capture1(
    root: Path, run_dir: Path, selection: Mapping[str, Any], protocol: Mapping[str, Any],
) -> None:
    output = run_dir / C1_PREFLIGHT_NAME
    if output.exists():
        raise FileExistsError(output)
    spec = protocol["captures"]["CAPTURE1"]
    all_actions = legacy._action_rows(root, "CAPTURE1", spec)
    result = physical_runner.prepare_capture1_bounded_preflight(
        root, spec, all_actions,
        selection["captures"]["CAPTURE1"]["selected_actions"], output,
        preselection_path=run_dir / "METADATA_PRESELECTION.json",
        preselection_sha256=PRESELECTION_SHA256,
    )
    print(json.dumps({
        "output": str(output), "sha256": sha256_file(output), "gate": result["gate"],
    }, indent=2, sort_keys=True), flush=True)


def _access_gates(binding: Mapping[str, Any]) -> dict[str, bool]:
    return physical_runner._access_gates(binding)


def _run_capture(
    root: Path, run_dir: Path, capture: str,
    selection: Mapping[str, Any], protocol: Mapping[str, Any],
    *, max_nfev: int, wall_limit_s: float,
) -> None:
    synthetic = _require_synthetic(run_dir)
    if capture == "CAPTURE1" and not (run_dir / C1_PREFLIGHT_NAME).exists():
        raise RuntimeError("Capture1 metadata-only bounded preflight is absent")
    output = run_dir / f"{capture}_RESULT.json"
    if output.exists():
        raise FileExistsError(output)
    spec = protocol["captures"][capture]
    frozen = selection["captures"][capture]
    episodes, binding = physical_runner._load_capture(
        root, run_dir, capture, spec, frozen,
        access_attempt=1, preselection_sha256=PRESELECTION_SHA256,
    )
    access_path = run_dir / f"{capture}_PAYLOAD_ACCESS_AUDIT.json"
    dump_json(access_path, binding); access_path.chmod(0o444)
    access_gates = _access_gates(binding)
    if not all(access_gates.values()):
        result = {
            "schema": "biospur-pure-imu-v0-progressive-capture-result-v1",
            "capture": capture, "terminal_decision": "FAIL",
            "first_failed_gate": next(key for key, value in access_gates.items() if not value),
            "access_gates": access_gates,
        }
        dump_json(output, result); output.chmod(0o444)
        raise SystemExit(2)

    full_factors = b5_only_factors(episodes)
    snapshots_dir = run_dir / f"{capture}_PROGRESSIVE_SNAPSHOTS"
    snapshots_dir.mkdir(parents=True, exist_ok=False)
    previous = None
    previous_coverage = None
    previous_conflicts: list[str] = []
    previous_training_identity = None
    last_training_solve = None
    last_training_step = None
    snapshot_index = []
    in_memory_snapshots = []
    retained_actions: list[str] = []
    subject_spec = real_subject_spec()
    for index, episode in enumerate(episodes):
        retained_actions.append(episode.action)
        cumulative = select_factor_actions(full_factors, retained_actions)
        episode_only = select_factor_actions(full_factors, [episode.action])
        training_identity = factor_partition_identity(cumulative, "IDENTIFICATION_TRAIN")
        held_identity = factor_partition_identity(cumulative, "HELD_OUT_VALIDATION")
        if episode.partition == "HELD_OUT_VALIDATION":
            if previous is None or last_training_solve is None or previous_training_identity is None:
                raise RuntimeError("held-out episode has no preceding trained profile")
            if training_identity["sha256"] != previous_training_identity["sha256"]:
                raise RuntimeError("held-out episode changed the retained training factor set")
            state = previous.copy()
            solve = copy.deepcopy(last_training_solve)
            solve["state"] = state
            solve["validation_only_no_refit"] = {
                "applied": True,
                "reason": "UNCHANGED_TRAINING_FACTOR_SET",
                "training_factor_sha256_before": previous_training_identity["sha256"],
                "training_factor_sha256_after": training_identity["sha256"],
                "strict_state_identity_with_preceding_profile": bool(np.array_equal(state, previous)),
                "profile_source_step": last_training_step,
                "extra_optimizer_iterations_granted": 0,
                "held_out_factors_used_in_objective": False,
            }
        else:
            solve = solve_cumulative(
                cumulative, subject_spec, previous_state=previous,
                seed=(91000 if capture == "CAPTURE1" else 92000) + index,
                geometry_prior=REAL_SUBJECT_PROGRESSIVE_GEOMETRY_PRIOR,
                starts=5 if index == len(episodes) - 1 else 3,
                max_nfev=max_nfev, wall_limit_s=wall_limit_s,
                optimization_retain_fraction=0.50,
            )
            state = solve["state"]
            solve["validation_only_no_refit"] = {"applied": False}
            last_training_solve = copy.deepcopy(solve)
            last_training_step = index + 1
        information = information_snapshot(
            cumulative,
            episode_only,
            subject_spec,
            state,
            geometry_prior=REAL_SUBJECT_PROGRESSIVE_GEOMETRY_PRIOR,
        )
        held = held_out_report(cumulative, state, subject_spec)
        physical = physical_state_report(state, subject_spec, information)
        readiness = readiness_snapshot(information, held, physical, previous_coverage)
        assessment = classify_episode(information, held, previous_conflicts)
        snapshot = {
            "schema": "biospur-pure-imu-v0-progressive-snapshot-v1",
            "capture": capture, "capture_id": spec["capture_id"],
            "profile_id": f"{capture[0]}{capture[-1]}_PROGRESSIVE_PROFILE",
            "step": index + 1, "episode": episode.action,
            "episode_partition": episode.partition,
            "retained_episodes": list(retained_actions),
            "exact_bounded_slice_binding": binding["selected_actions"][index],
            "factor_identity": {
                "training": training_identity,
                "held_out": held_identity,
                "training_unchanged_from_previous_step": (
                    training_identity["sha256"] == previous_training_identity["sha256"]
                    if previous_training_identity is not None else None
                ),
            },
            "state": state.tolist(),
            "solve": {key: value for key, value in solve.items() if key != "state"},
            "information": information,
            "held_out": held,
            "physical": physical,
            "readiness": readiness,
            "episode_assessment": assessment,
            "parameter_change_from_previous": {
                "heading_max_deg": (
                    float(np.max(np.degrees(np.abs(wrap(
                        state[:HEADING_DIMENSION] - previous[:HEADING_DIMENSION]
                    ))))) if previous is not None else None
                ),
                "normalized_full_state": (
                    float(np.linalg.norm(state - previous) / np.sqrt(len(state)))
                    if previous is not None else None
                ),
            },
            "cross_capture_payload_or_parameter_used": False,
        }
        path = snapshots_dir / f"STEP_{index + 1:02d}_{episode.action}.json"
        dump_json(path, _jsonable(snapshot)); path.chmod(0o444)
        snapshot_index.append({
            "step": index + 1, "episode": episode.action,
            "path": str(path), "sha256": sha256_file(path),
        })
        in_memory_snapshots.append(snapshot)
        previous = state
        previous_training_identity = training_identity
        previous_coverage = readiness["group_coverage_percent"]
        previous_conflicts = held["named_conflicts"]
        print(
            f"STAGE {capture} progressive {index + 1}/{len(episodes)} "
            f"{episode.action} {assessment['classification']} "
            f"coverage={readiness['overall_coverage_percent']:.1f}% "
            f"readiness={readiness['overall_readiness_percent']:.1f}%",
            flush=True,
        )

    # Execute qmt's upstream Olsson primitive as an independent raw acc/gyr
    # baseline only after the progressive product state is fixed.
    qmt_actions = legacy._qmt_intended_action_map(frozen)
    _, qmt_audit = build_edge_factors(episodes, qmt_intended_actions=qmt_actions)
    final = in_memory_snapshots[-1]
    gates = final_gates(final)
    named_required = {
        "CAPTURE1": {"elbow_right"},
        "CAPTURE2": {"elbow_left", "knee_left", "knee_right"},
    }[capture]
    gates["named_previous_conflicts_resolved"] = not bool(
        named_required & set(final["held_out"]["conflict_edges"])
    )
    gates["synthetic_prerequisite"] = synthetic["pass"]
    gates["bounded_access_proof"] = all(access_gates.values())
    gates["viewer_gate"] = False
    passed_quantitative = all(value for key, value in gates.items() if key != "viewer_gate")
    terminal = "INCONCLUSIVE" if passed_quantitative else "FAIL"
    result = {
        "schema": "biospur-pure-imu-v0-progressive-capture-result-v1",
        "capture": capture, "capture_id": spec["capture_id"],
        "profile_id": f"{capture[0]}{capture[-1]}_PROGRESSIVE_PROFILE",
        "terminal_decision": terminal,
        "profile_locked": False,
        "viewer_generated": False,
        "hxx_opened": False,
        "golf_boxing_capture3_opened": False,
        "cross_capture_payload_parameter_prior_or_warm_start_used": False,
        "external_progressive_geometry_prior": (
            REAL_SUBJECT_PROGRESSIVE_GEOMETRY_PRIOR.audit()
        ),
        "gates": gates,
        "failed_gates": [key for key, value in gates.items() if not value],
        "first_failed_gate": next((key for key, value in gates.items() if not value), None),
        "snapshot_index": snapshot_index,
        "final_snapshot_sha256": snapshot_index[-1]["sha256"],
        "access_audit": {"path": str(access_path), "sha256": sha256_file(access_path)},
        "qmt_olsson_executable_baseline": {
            "version": "qmt-0.2.4",
            "source_file_spdx": "LicenseRef-Unspecified",
            "execution_only_no_source_copied": True,
            "edges": qmt_audit,
        },
        "raw_accelerometer_gyroscope_only": True,
        "terminal_reason": (
            "QUANTITATIVE_GATES_NOT_ALL_MET"
            if terminal == "FAIL" else "QUANTITATIVE_GATES_MET_BUT_VIEWER_REQUIRES_SEPARATE_DIRECT_EVIDENCE"
        ),
    }
    dump_json(output, _jsonable(result)); output.chmod(0o444)
    print(json.dumps({
        "output": str(output), "terminal_decision": terminal,
        "failed_gates": result["failed_gates"],
    }, indent=2, sort_keys=True), flush=True)
    if terminal != "PASS":
        raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--synthetic", action="store_true")
    modes.add_argument("--prepare-capture1-access", action="store_true")
    modes.add_argument("--capture", choices=("CAPTURE1", "CAPTURE2"))
    parser.add_argument("--max-nfev", type=int, default=50)
    parser.add_argument("--wall-limit-s", type=float, default=20.0)
    args = parser.parse_args()
    root = ROOT.resolve(); run_dir = root / RUN_REL
    preselection = _preselection(root)
    selection = _frozen_selection(root, preselection)
    protocol = load_protocol(root)
    if args.synthetic:
        _write_synthetic(run_dir, args.max_nfev, args.wall_limit_s)
    elif args.prepare_capture1_access:
        _require_synthetic(run_dir)
        _prepare_capture1(root, run_dir, selection, protocol)
    else:
        _run_capture(
            root, run_dir, args.capture, selection, protocol,
            max_nfev=args.max_nfev, wall_limit_s=args.wall_limit_s,
        )


if __name__ == "__main__":
    main()
