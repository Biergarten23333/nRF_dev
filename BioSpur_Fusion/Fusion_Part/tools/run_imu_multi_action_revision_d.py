#!/usr/bin/env python3
"""Bounded Revision-D preflight and D-1 segmentation runner."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Fusion_Part/src"))

from biospur_fusion.imu_multi_action_engineering_v1.common_time import build_common_timeline
from biospur_fusion.imu_multi_action_engineering_v1.pipeline import load_q2_cache
from biospur_fusion.imu_multi_action_revision_d.segmentation import (
    ACTIONS, run_segmentation_negative_controls, segment_revision_d, sha256,
)

BASELINE = "7c659b24b714b1ef4d9143658d1a6ee49ffb92ce"


def dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")


def current_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def manifest(output: Path) -> None:
    dump(output / "SHA256_MANIFEST.json", {
        str(path.relative_to(output)): sha256(path)
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "SHA256_MANIFEST.json"
    })


def firewall_audit(source: Path) -> dict:
    text = source.read_text()
    tree = ast.parse(text)
    identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    identifiers |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    forbidden = {
        "least_squares": "least_squares" in identifiers,
        "attempt7_x_end": any("attempt7" in name.lower() for name in identifiers),
        "attempt8_x_end": any("attempt8" in name.lower() for name in identifiers),
        "fitted_functional_axes": any("fitted_functional" in name.lower() for name in identifiers),
        "joint_zero_input": any("joint_zero" in name.lower() for name in identifiers),
        "expected_skeleton": any(name.startswith("EXPECTED_") or "skeleton" in name.lower() for name in identifiers),
        "protocol_prior": any("protocol_prior" in name.lower() for name in identifiers),
        "solver_or_replay_module_import": any("solver" in name or "replay" in name for name in imports),
    }
    return {
        "schema": "biospur-revision-d-segmentation-input-firewall-v1",
        "source_absolute_path": str(source.resolve()),
        "source_sha256": sha256(source),
        "ast_identifier_count": len(identifiers),
        "imported_modules": sorted(imports),
        "forbidden_token_presence": forbidden,
        "allowed_inputs": ["common_time_q2_timeline", "eleven_labelled_search_envelopes", "node_to_segment_identity", "frozen_boundary_contract"],
        "real_capture_accessed_during_preflight": False,
        "D_MINUS_1_CALIBRATION_PARAMETER_FIREWALL": "PASS" if not any(forbidden.values()) else "FAIL",
    }


def run_preflight(args: argparse.Namespace) -> dict:
    output = args.output
    if output.exists():
        raise FileExistsError(output)
    if current_head() != BASELINE:
        raise RuntimeError(f"HEAD must remain exact baseline {BASELINE}")
    output.mkdir(parents=True)
    contract = json.loads(args.contract.read_text())
    semantics = json.loads(args.semantics.read_text())
    controls = run_segmentation_negative_controls(contract)
    firewall = firewall_audit(ROOT / "Fusion_Part/src/biospur_fusion/imu_multi_action_revision_d/segmentation.py")
    shutil.copyfile(args.contract, output / "ACTION_BOUNDARY_CONTRACT.json")
    shutil.copyfile(args.semantics, output / "TRUNK_PRODUCT_SEMANTICS.json")
    dump(output / "SEGMENTATION_NEGATIVE_CONTROLS.json", controls)
    dump(output / "SEGMENTATION_INPUT_FIREWALL.json", firewall)
    passed = controls["pass"] and firewall["D_MINUS_1_CALIBRATION_PARAMETER_FIREWALL"] == "PASS"
    result = {
        "schema": "biospur-revision-d-d-minus-1-preflight-freeze-v1",
        "pass": passed,
        "terminal_outcome": "PASS_D_MINUS_1_PREFLIGHT_FREEZE" if passed else "FAIL_SEGMENTATION_NEGATIVE_CONTROL",
        "baseline_commit": BASELINE,
        "trunk_product_semantics_sha256": sha256(output / "TRUNK_PRODUCT_SEMANTICS.json"),
        "action_boundary_contract_sha256": sha256(output / "ACTION_BOUNDARY_CONTRACT.json"),
        "segmentation_source_sha256": firewall["source_sha256"],
        "LABEL_USAGE": contract["LABEL_USAGE"],
        "real_capture_accessed": False,
        "ATTEMPT7_DISPOSITION": "FAIL_IMMUTABLE",
        "ATTEMPT8_DISPOSITION": "FAIL_SINGLE_START_NOT_CONVERGED",
        "NEXT_PHASE_AUTHORIZED": passed,
    }
    dump(output / "PREFLIGHT_FREEZE.json", result)
    manifest(output)
    return result


def old_new_comparison(old_path: Path, new: dict) -> dict:
    old = json.loads(old_path.read_text())
    actions = {}
    for action in ACTIONS:
        old_phases = old.get("actions", {}).get(action, [])
        new_phases = new["actions"][action]["phases"]
        new_membership = {}
        for phase in new_phases:
            for row in phase["full_row_indices"]:
                new_membership.setdefault(int(row), set()).add(phase["phase"])
        rows = []
        for phase in old_phases:
            old_rows = [int(row) for row in phase.get("row_indices", [])]
            old_runs = 0
            if old_rows:
                ordered = sorted(set(old_rows)); old_runs = 1 + sum(b != a + 1 for a, b in zip(ordered, ordered[1:]))
            mapped = sorted({name for row in old_rows for name in new_membership.get(row, ())})
            outside = sum(row not in new_membership for row in old_rows)
            rows.append({
                "old_phase": phase.get("phase"),
                "old_selected_rows": len(old_rows),
                "old_contiguous_run_count": old_runs,
                "mapped_new_continuous_phases": mapped,
                "old_rows_outside_any_new_complete_bout": outside,
                "previous_scattered_membership": old_runs > 1,
            })
        actions[action] = rows
    return {
        "schema": "biospur-revision-d-old-vs-new-phase-membership-v1",
        "historical_source": str(old_path.resolve()),
        "historical_source_sha256": sha256(old_path),
        "historical_rows_used_for_segmentation": False,
        "actions": actions,
    }


def render_timeline_plots(output: Path, result: dict, contract: dict) -> None:
    plot_dir = output / "timeline_plots"
    plot_dir.mkdir()
    onset = float(contract["state_machine"]["onset_activity_snr"])
    offset = float(contract["state_machine"]["offset_activity_snr"])
    colors = {"PRE_REFERENCE":"#4daf4a", "POST_REFERENCE":"#377eb8", "TRANSITION":"#ff7f00", "ACTIVE_BOUT_ID":"#e41a1c", "HOLD":"#984ea3", "INVALID":"#555555", "UNINFORMATIVE_VALID":"#cccccc"}
    for action in ACTIONS:
        item = result["actions"][action]
        signal = item["timeline_signal"]
        times = np.asarray(signal["global_time_ns"], np.int64)
        relative_s = (times - times[0]) / 1e9
        snr = np.asarray(signal["activity_snr"], float)
        fig, ax = plt.subplots(figsize=(12, 4.5), constrained_layout=True)
        ax.plot(relative_s, snr, color="black", linewidth=1.0, label="relative activity / uncertainty")
        ax.axhline(onset, color="#e41a1c", linestyle="--", label="onset threshold")
        ax.axhline(offset, color="#377eb8", linestyle=":", label="offset threshold")
        operator = item["operator_window_ns"]
        ax.axvspan((operator[0]-times[0])/1e9, (operator[1]-times[0])/1e9, color="#ffff99", alpha=.25, label="operator search envelope")
        for run in item["status_runs"]:
            start=(run["start_global_time_ns"]-times[0])/1e9; stop=(run["stop_global_time_ns"]-times[0])/1e9
            ax.axvspan(start, stop, color=colors[run["status"]], alpha=.12)
        for phase in item["phases"]:
            start=(phase["start_global_time_ns"]-times[0])/1e9; stop=(phase["stop_global_time_ns"]-times[0])/1e9
            ax.text((start+stop)/2, max(0.5, np.nanpercentile(snr[snr>=0],95) if np.any(snr>=0) else 1), phase["phase"], rotation=20, ha="center", fontsize=7)
        upper = max(6.0, float(np.nanpercentile(snr[snr >= 0], 99)) * 1.15) if np.any(snr >= 0) else 6.0
        ax.set(
            title=f"Revision D signal-derived boundary — {action}",
            xlabel="seconds from safe-domain start",
            ylabel="activity SNR",
            ylim=(0, upper),
        )
        ax.legend(loc="upper right", fontsize=7, ncol=2)
        fig.savefig(plot_dir / f"{action}.png", dpi=150)
        plt.close(fig)


def run_segment(args: argparse.Namespace) -> dict:
    output = args.output
    if output.exists():
        raise FileExistsError(output)
    if current_head() != BASELINE:
        raise RuntimeError(f"HEAD must remain exact baseline {BASELINE}")
    preflight = json.loads((args.preflight / "PREFLIGHT_FREEZE.json").read_text())
    if not preflight["pass"]:
        raise RuntimeError("D-1 preflight did not pass")
    if sha256(args.preflight / "ACTION_BOUNDARY_CONTRACT.json") != preflight["action_boundary_contract_sha256"]:
        raise RuntimeError("boundary contract changed after preflight freeze")
    if sha256(args.preflight / "TRUNK_PRODUCT_SEMANTICS.json") != preflight["trunk_product_semantics_sha256"]:
        raise RuntimeError("trunk product semantics changed after preflight freeze")
    segmentation_source = ROOT / "Fusion_Part/src/biospur_fusion/imu_multi_action_revision_d/segmentation.py"
    if sha256(segmentation_source) != preflight["segmentation_source_sha256"]:
        raise RuntimeError("segmentation source changed after preflight freeze")
    phase = json.loads((args.phase_a / "RESULT.json").read_text())
    cache = args.phase_a / "Q2_HUMAN_QUASI_STATIC_CACHE.npz"
    if not phase["pass"] or sha256(cache) != phase["q2_cache_sha256"]:
        raise RuntimeError("qualified Phase-A cache binding failed")
    contract = json.loads((args.preflight / "ACTION_BOUNDARY_CONTRACT.json").read_text())
    legacy_gates = json.loads(args.legacy_gates.read_text())
    windows = {name: tuple(value) for name, value in phase["calibration_windows"].items()}
    q2 = load_q2_cache(cache)
    start = min(value[0] for value in windows.values()) - int(round(contract["search_domain"]["pre_roll_s"] * 1e9))
    stop = max(value[1] for value in windows.values()) + int(round(contract["search_domain"]["post_roll_s"] * 1e9))
    timeline = build_common_timeline(q2, start, stop, legacy_gates["common_time"])
    result = segment_revision_d(
        timeline, windows, legacy_gates["node_to_segment"], contract,
        preflight["trunk_product_semantics_sha256"], preflight["action_boundary_contract_sha256"],
    )
    output.mkdir(parents=True)
    dump(output / "ACTION_PHASE_TIMELINE.json", result)
    dump(output / "ACTION_TEMPORAL_ACCOUNTING.json", {
        "schema": "biospur-revision-d-action-temporal-accounting-v1",
        "actions": {name: value["temporal_accounting"] for name, value in result["actions"].items()},
        "closed": all(value["temporal_accounting"]["closed"] for value in result["actions"].values()),
    })
    dump(output / "ACTION_BOUNDARY_UNCERTAINTY.json", {
        "schema": "biospur-revision-d-action-boundary-uncertainty-v1",
        "actions": {name: [
            {key: phase.get(key) for key in ("phase", "bout_id", "start_global_time_ns", "stop_global_time_ns", "boundary_confidence", "boundary_uncertainty", "truncated")}
            for phase in value["phases"]
        ] for name, value in result["actions"].items()},
    })
    shutil.copyfile(args.preflight / "ACTION_BOUNDARY_CONTRACT.json", output / "ACTION_BOUNDARY_CONTRACT.json")
    shutil.copyfile(args.preflight / "SEGMENTATION_NEGATIVE_CONTROLS.json", output / "SEGMENTATION_NEGATIVE_CONTROLS.json")
    firewall = json.loads((args.preflight / "SEGMENTATION_INPUT_FIREWALL.json").read_text())
    firewall.update({
        "qualified_phase_a_result_absolute_path": str((args.phase_a / "RESULT.json").resolve()),
        "qualified_phase_a_result_sha256": sha256(args.phase_a / "RESULT.json"),
        "q2_cache_sha256": sha256(cache),
        "opened_modalities": ["CALIBRATION_Q2_COMMON_TIME", "ELEVEN_ACTION_SEARCH_ENVELOPES"],
        "forbidden_modalities_opened": [],
    })
    dump(output / "SEGMENTATION_INPUT_FIREWALL.json", firewall)
    dump(output / "OLD_VS_NEW_PHASE_MEMBERSHIP.json", old_new_comparison(args.old_segmentation, result))
    render_timeline_plots(output, result, contract)
    final = {
        "schema": "biospur-revision-d-d-minus-1-result-v1",
        "terminal_outcome": result["terminal_outcome"],
        "pass": result["pass"],
        "action_phase_timeline_sha256": sha256(output / "ACTION_PHASE_TIMELINE.json"),
        "action_boundary_contract_sha256": preflight["action_boundary_contract_sha256"],
        "trunk_product_semantics_sha256": preflight["trunk_product_semantics_sha256"],
        "common_time_accounting": timeline.accounting,
        "ATTEMPT7_DISPOSITION": "FAIL_IMMUTABLE",
        "ATTEMPT8_DISPOSITION": "FAIL_SINGLE_START_NOT_CONVERGED",
        "nonlinear_solver_started": False,
        "model_edited_after_real_segmentation": False,
        "golf": "SEALED", "boxing": "SEALED", "walk": "SEALED", "final_still": "SEALED", "uwb": "SEALED",
    }
    dump(output / "RESULT.json", final)
    manifest(output)
    return final


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--contract", type=Path, required=True)
    preflight.add_argument("--semantics", type=Path, required=True)
    preflight.add_argument("--output", type=Path, required=True)
    segment = sub.add_parser("segment")
    segment.add_argument("--preflight", type=Path, required=True)
    segment.add_argument("--phase-a", type=Path, required=True)
    segment.add_argument("--legacy-gates", type=Path, required=True)
    segment.add_argument("--old-segmentation", type=Path, required=True)
    segment.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_preflight(args) if args.command == "preflight" else run_segment(args)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
