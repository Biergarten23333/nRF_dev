#!/usr/bin/env python3
"""Run the clean C2 coupled-progressive real calibration once."""

from __future__ import annotations

import csv
import json
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biospur_fusion.c2_coupled_progressive.contracts import RUN_DIR, load_effective_config
from biospur_fusion.c2_coupled_progressive.estimator import (
    attach_capture_wide_heading_streams,
    build_corrected_trajectory,
    build_factor_tape,
    factor_tape_manifest,
    incremental_vs_batch_audit,
    jsonable,
    load_real_episodes,
    order_permutation_audit_with_mount_gate,
    apply_initial_still_gravity_tilt_update,
    apply_standing_gravity_mount_gate,
    refit_cumulative_calibration_tape,
    save_tape_npz,
    solve_fresh_batch_from_tape,
    solve_progressive_from_tape,
)
from biospur_fusion.c2_coupled_progressive.frontend import VerifiedFrontendArchive
from biospur_fusion.c2_coupled_progressive.renderer import render_selected_episodes


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    path.chmod(0o444)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, np.ndarray):
        if value.ndim == 0:
            return float(value)
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float) and not np.isfinite(value):
        return "Infinity" if value > 0 else "-Infinity"
    return value


def _run_dir() -> Path:
    for index in range(1, 100):
        candidate = RUN_DIR / f"REAL_RUN_{index:03d}"
        if not candidate.exists():
            candidate.mkdir()
            return candidate
    raise RuntimeError("no free REAL_RUN_NNN directory")


def _prefix_csv(path: Path, prefixes: list[dict[str, Any]]) -> None:
    fields = [
        "progressive_update_index",
        "chronological_index",
        "qa_label",
        "episode_prequential_nll",
        "episode_information",
        "episode_window_count",
        "episode_span_count",
        "information_logdet",
        "gauge_reduced_rank",
        "rank_fraction",
        "branch_entropy",
        "valid_branch_count",
    ]
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in prefixes:
            writer.writerow({field: row.get(field) for field in fields})
    path.chmod(0o444)


def _prefix_plot(path: Path, prefixes: list[dict[str, Any]]) -> None:
    x = [int(row["chronological_index"]) for row in prefixes]
    fig, axes = plt.subplots(3, 1, figsize=(8, 7), dpi=130, sharex=True)
    axes[0].plot(x, [float(row["information_logdet"]) for row in prefixes], marker="o", color="black")
    axes[0].set_ylabel("info logdet")
    axes[1].plot(x, [float(row["rank_fraction"]) for row in prefixes], marker="o", color="#1f77b4")
    axes[1].set_ylabel("rank fraction")
    axes[2].plot(x, [float(row["branch_entropy"]) for row in prefixes], marker="o", color="#d62728")
    axes[2].set_ylabel("branch entropy")
    axes[2].set_xlabel("chronological episode")
    for ax in axes:
        ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    path.chmod(0o444)


def _factor_details(tape: Any) -> dict[str, Any]:
    return {
        "schema": "biospur-c2-coupled-progressive-factor-details-v1",
        "hinge_axes": [
            _jsonable(row)
            for block in tape.episodes
            for row in block.hinge_axes
        ],
        "centers": [
            _jsonable(row)
            for block in tape.episodes
            for row in block.centers
        ],
        "heading_streams": [
            {
                **_jsonable(row),
                "time_root_s": row.time_root_s[:3].tolist() + row.time_root_s[-3:].tolist() if len(row.time_root_s) >= 6 else row.time_root_s.tolist(),
                "quat2corr_child_sensor_wxyz": row.quat2corr_child_sensor_wxyz[:2].tolist() + row.quat2corr_child_sensor_wxyz[-2:].tolist() if len(row.quat2corr_child_sensor_wxyz) >= 4 else row.quat2corr_child_sensor_wxyz.tolist(),
                "delta_filt_rad": row.delta_filt_rad[:3].tolist() + row.delta_filt_rad[-3:].tolist() if len(row.delta_filt_rad) >= 6 else row.delta_filt_rad.tolist(),
                "rating": row.rating[:3].tolist() + row.rating[-3:].tolist() if len(row.rating) >= 6 else row.rating.tolist(),
                "qmt_state": row.qmt_state[:3].tolist() + row.qmt_state[-3:].tolist() if len(row.qmt_state) >= 6 else row.qmt_state.tolist(),
            }
            for block in tape.episodes
            for row in block.headings
        ],
    }


def main() -> int:
    start = time.perf_counter()
    output = _run_dir()
    print(f"output={output}", flush=True)
    config = load_effective_config()
    frontend = VerifiedFrontendArchive()
    frontend_seal = frontend.verify_seal_and_semantics()
    episodes = list(frontend.episodes())
    print("frontend loaded", len(episodes), flush=True)

    stage_start = time.perf_counter()
    raw_tape = build_factor_tape(episodes)
    calibration_tape = refit_cumulative_calibration_tape(raw_tape)
    base_state = solve_fresh_batch_from_tape(calibration_tape)
    base_gravity_tilt = apply_initial_still_gravity_tilt_update(base_state, episodes)
    base_mount_gate = apply_standing_gravity_mount_gate(base_state, episodes)
    tape = attach_capture_wide_heading_streams(calibration_tape, base_state)
    factor_wall = time.perf_counter() - stage_start
    print(
        f"factor_tape_and_capture_wide_heading wall_s={factor_wall:.3f}",
        flush=True,
    )

    stage_start = time.perf_counter()
    progressive_state, prefixes = solve_progressive_from_tape(tape)
    progressive_wall = time.perf_counter() - stage_start
    print(f"progressive wall_s={progressive_wall:.3f}", flush=True)

    stage_start = time.perf_counter()
    batch_state = solve_fresh_batch_from_tape(tape)
    progressive_gravity_tilt = apply_initial_still_gravity_tilt_update(progressive_state, episodes)
    batch_gravity_tilt = apply_initial_still_gravity_tilt_update(batch_state, episodes)
    progressive_mount_gate = apply_standing_gravity_mount_gate(progressive_state, episodes)
    batch_mount_gate = apply_standing_gravity_mount_gate(batch_state, episodes)
    batch_wall = time.perf_counter() - stage_start
    batch_audit = incremental_vs_batch_audit(progressive_state, batch_state)
    order_audit = order_permutation_audit_with_mount_gate(tape, progressive_state, episodes)
    print(f"batch/order wall_s={time.perf_counter() - stage_start:.3f}", flush=True)

    stage_start = time.perf_counter()
    trajectory = build_corrected_trajectory(episodes, tape, progressive_state)
    trajectory_wall = time.perf_counter() - stage_start
    print(f"trajectory wall_s={trajectory_wall:.3f}", flush=True)

    npz_path = output / "C2_COUPLED_PROGRESSIVE_OUTPUTS.npz"
    bindings = save_tape_npz(npz_path, tape, prefixes, trajectory)
    npz_path.chmod(0o444)

    render_dir = output / "renders"
    render_audit = render_selected_episodes(trajectory, progressive_state, render_dir)
    Path(render_audit["html"]).chmod(0o444)
    for png in render_audit["pngs"] + render_audit.get("diagnostic_pngs", []):
        Path(png).chmod(0o444)
    (render_dir / "DIRECT_FK_RENDER_AUDIT.json").chmod(0o444)
    print(f"render wall_s={render_audit['wall_s']:.3f}", flush=True)

    _prefix_csv(output / "PREFIX_CURVES.csv", prefixes)
    _prefix_plot(output / "PREFIX_CURVES.png", prefixes)

    _write_new(output / "FACTOR_TAPE_MANIFEST.json", factor_tape_manifest(tape))
    _write_new(output / "FACTOR_DETAILS.json", _factor_details(tape))
    _write_new(output / "PROGRESSIVE_PREFIXES.json", {
        "schema": "biospur-c2-coupled-progressive-prefixes-v1",
        "count": len(prefixes),
        "prefixes": prefixes,
    })
    _write_new(output / "POSTERIOR_SUMMARY.json", {
        "schema": "biospur-c2-coupled-progressive-posterior-summary-v1",
        "status": "COMPLETE",
        "summary": progressive_state.summary(),
    })
    _write_new(output / "MOUNT_PHYSICAL_GATE.json", {
        "schema": "biospur-c2-coupled-progressive-mount-physical-gate-real-run-v1",
        "progressive": progressive_mount_gate,
        "fresh_batch": batch_mount_gate,
    })
    _write_new(output / "INCREMENTAL_VS_BATCH.json", batch_audit)
    _write_new(output / "ORDER_PERMUTATION_AUDIT.json", order_audit)

    wall = time.perf_counter() - start
    posterior_summary = progressive_state.summary()
    qmt_failures = factor_tape_manifest(tape)["heading_qmt_failure_count"]
    branch_weights = [float(v) for v in posterior_summary["branch_weights"]]
    max_branch_weight = max(branch_weights) if branch_weights else 0.0
    mount_gate_fallbacks = [
        segment
        for segment, row in progressive_mount_gate["rows"].items()
        if row.get("fallback_to_broad_uncertain_prior", False)
    ]
    mount_gate_fallbacks.extend(progressive_mount_gate.get("standing_topology_selected", {}).get("fallback_segments", []))
    mount_gate_fallbacks = sorted(set(mount_gate_fallbacks))
    verdict = "PASS"
    if qmt_failures:
        verdict = "INCONCLUSIVE"
    if render_audit["status"] != "PASS":
        verdict = "INCONCLUSIVE"
    if max_branch_weight < 0.80 or mount_gate_fallbacks:
        verdict = "INCONCLUSIVE"
    if batch_audit["status"] != "PASS" or order_audit["status"] != "PASS":
        verdict = "FAIL"
    if wall > 1800.0:
        verdict = "FAIL"
    _write_new(output / "REAL_19_PROGRESSIVE_RUN.json", {
        "schema": "biospur-c2-coupled-progressive-real-run-v1",
        "verdict": verdict,
        "wall_s": wall,
        "stage_walls_s": {
            "frontend_s": None,
            "factor_tape_s": factor_wall,
            "progressive_s": progressive_wall,
            "fresh_batch_s": batch_wall,
            "trajectory_s": trajectory_wall,
            "render_s": render_audit["wall_s"],
        },
        "base_mount_gate_before_capture_wide_heading": base_mount_gate,
        "gravity_tilt_updates": {
            "base_before_capture_wide_heading": base_gravity_tilt,
            "progressive": progressive_gravity_tilt,
            "fresh_batch": batch_gravity_tilt,
        },
        "episode_count": len(episodes),
        "prefix_count": len(prefixes),
        "frontend_seal": frontend_seal,
        "effective_config": {
            "torso_scalar_0_425_used_as_posterior": False,
            "trochanter_0_335_used_as_internal_hip_spacing": False,
            "proxy_geometry_owner": config["proxy_geometry"]["role"],
        },
        "factor_tape": factor_tape_manifest(tape),
        "batch_audit_status": batch_audit["status"],
        "order_audit_status": order_audit["status"],
        "render_audit_status": render_audit["status"],
        "mount_physical_gate_status": progressive_mount_gate["status"],
        "branch_concentration_gate": {
            "status": "PASS" if max_branch_weight >= 0.80 else "INCONCLUSIVE",
            "max_branch_weight": max_branch_weight,
            "threshold": 0.80,
        },
        "mount_gate_fallbacks_force_inconclusive": mount_gate_fallbacks,
        "npz": {"path": str(npz_path), "array_binding_count": len(bindings), "array_bindings": bindings},
        "render_outputs": render_audit["pngs"],
        "diagnostic_render_outputs": render_audit.get("diagnostic_pngs", []),
        "html": render_audit["html"],
    })
    print(f"verdict={verdict} wall_s={wall:.3f}", flush=True)
    return 0 if verdict in {"PASS", "INCONCLUSIVE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
