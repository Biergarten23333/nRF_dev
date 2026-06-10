#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from phase2_6_diagnostics_closure import find_static_capture_dirs, load_offline_solver
from phase2_7_final_closure import fit_similarity, fit_tag_model_generic, summarize_errors, tag_error_rows, write_csv_rows
from phase2_8_runtime_correction import make_vb_zero_delay_layout
from phase2_solver_ablation import load_primary_vicon_anchor_truth, load_sweep_deltas
from scripts.audit_helpers import ANCHOR_LABELS, markdown_table
from scripts.phase1_common import load_phase1_data, tag_coord_map


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 2.9 estimated-distance runtime correction check.")
    parser.add_argument("--data-dir", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def layout_coords(layout: Any) -> np.ndarray:
    return np.asarray([[layout.anchors[i].x_mm, layout.anchors[i].y_mm, layout.anchors[i].z_mm] for i in range(8)], dtype=float)


def load_layout_coords(path: Path) -> np.ndarray:
    obj = json.loads(path.read_text(encoding="utf-8"))
    anchors = sorted(obj["anchors"], key=lambda a: int(a["id"]))
    return np.asarray([[a["x_mm"], a["y_mm"], a["z_mm"]] for a in anchors], dtype=float)


def corrected_frames_additive(
    frames: list[Any],
    additive: np.ndarray,
    Frame: Any,
    Observation: Any,
) -> list[Any]:
    out = []
    for frame in frames:
        obs = [
            Observation(item.anchor_id, float(item.range_mm) - float(additive[item.anchor_id]), item.quality_percent, item.status)
            for item in frame.observations
        ]
        out.append(
            Frame(
                tag=frame.tag,
                sweep=frame.sweep,
                host_elapsed_s=frame.host_elapsed_s,
                host_epoch_s=frame.host_epoch_s,
                observations=tuple(obs),
                imu=frame.imu,
            )
        )
    return out


def corrected_frames_with_estimated_dist(
    original_frames: list[Any],
    previous_results: list[Any],
    additive: np.ndarray,
    rho: float,
    anchors_xyz: np.ndarray,
    Frame: Any,
    Observation: Any,
) -> list[Any]:
    out = []
    for frame, result in zip(original_frames, previous_results):
        p = np.asarray([result.x_mm, result.y_mm, result.z_mm], dtype=float)
        obs = []
        for item in frame.observations:
            d_hat = float(np.linalg.norm(p - anchors_xyz[item.anchor_id]))
            corrected = float(item.range_mm) - float(additive[item.anchor_id]) - float(rho) * d_hat
            obs.append(Observation(item.anchor_id, corrected, item.quality_percent, item.status))
        out.append(
            Frame(
                tag=frame.tag,
                sweep=frame.sweep,
                host_elapsed_s=frame.host_elapsed_s,
                host_epoch_s=frame.host_epoch_s,
                observations=tuple(obs),
                imu=frame.imu,
            )
        )
    return out


def solve_sequence(layout: Any, Solver: Any, SolverConfig: Any, frames: list[Any]) -> tuple[list[int], list[Any], np.ndarray]:
    solver = Solver(layout, SolverConfig(method="T4"))
    solved_indices: list[int] = []
    results: list[Any] = []
    for idx, frame in enumerate(frames):
        result = solver.solve_frame(frame)
        if result is not None:
            solved_indices.append(idx)
            results.append(result)
    if results:
        points = np.asarray([[r.x_mm, r.y_mm, r.z_mm] for r in results], dtype=float)
        mean_point = np.nanmean(points, axis=0)
    else:
        mean_point = np.full(3, np.nan)
    return solved_indices, results, mean_point


def run_estimated_distance_for_position(
    *,
    position: str,
    frames: list[Any],
    layout: Any,
    Solver: Any,
    SolverConfig: Any,
    Frame: Any,
    Observation: Any,
    link_df: pd.DataFrame,
    sweep_delta: dict[str, float],
) -> tuple[dict[str, np.ndarray], list[dict], dict]:
    train = link_df[link_df["position"].astype(str) != position].reset_index(drop=True)
    fit = fit_tag_model_generic(f"estimated_distance_loo_without_{position}", train, sweep_delta, ["vicon_distance_mm"])
    additive = 0.5 * fit.anchor_deltas_mm + 0.5 * fit.delta_tag_mm
    rho = float(fit.coeffs["vicon_distance_mm"])
    anchors_xyz = layout_coords(layout)

    frames0 = corrected_frames_additive(frames, additive, Frame, Observation)
    idx0, res0, mean0 = solve_sequence(layout, Solver, SolverConfig, frames0)
    raw0 = [frames[i] for i in idx0]

    frames1 = corrected_frames_with_estimated_dist(raw0, res0, additive, rho, anchors_xyz, Frame, Observation)
    idx1, res1, mean1 = solve_sequence(layout, Solver, SolverConfig, frames1)
    raw1 = [raw0[i] for i in idx1]

    frames2 = corrected_frames_with_estimated_dist(raw1, res1, additive, rho, anchors_xyz, Frame, Observation)
    _idx2, res2, mean2 = solve_sequence(layout, Solver, SolverConfig, frames2)

    movement = {
        "position": position,
        "iter0_frames": int(len(res0)),
        "iter1_frames": int(len(res1)),
        "iter2_frames": int(len(res2)),
        "iter0_to_iter1_move_mm": float(np.linalg.norm(mean1 - mean0)),
        "iter1_to_iter2_move_mm": float(np.linalg.norm(mean2 - mean1)),
        "rho_percent": float(rho * 100.0),
        "delta_tag_mm": float(fit.delta_tag_mm),
        "train_rms_mm": float(fit.rms_mm),
    }
    points = {
        "estimated_distance_iter0_additive_only": mean0,
        "estimated_distance_iter1": mean1,
        "estimated_distance_iter2": mean2,
    }
    fit_row = {
        "heldout_position": position,
        "train_links": fit.n_links,
        "rho_percent": float(rho * 100.0),
        "delta_tag_mm": float(fit.delta_tag_mm),
        "train_rms_mm": float(fit.rms_mm),
    }
    return points, [movement], fit_row


def plot_summary(path: Path, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(10.5, 4.8))
    labels = df["mode"].astype(str).tolist()
    x = np.arange(len(labels))
    ax.bar(x - 0.24, df["median_3d_mm"].to_numpy(dtype=float), width=0.24, label="median")
    ax.bar(x, df["rmse_3d_mm"].to_numpy(dtype=float), width=0.24, label="RMSE")
    ax.bar(x + 0.24, df["p95_3d_mm"].to_numpy(dtype=float), width=0.24, label="P95")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=18, ha="right")
    ax.set_ylabel("static tag error [mm]")
    ax.set_title("Estimated-Distance Runtime Correction")
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def build_report(out_dir: Path, summary_rows: list[dict], movement_summary: list[dict], fit_summary: list[dict], fig_name: str) -> None:
    iter1 = next(r for r in summary_rows if r["mode"] == "estimated_distance_iter1")
    supervised = next(r for r in summary_rows if r["mode"] == "vicon_distance_covariate")
    production = next(r for r in summary_rows if r["mode"] == "production_baseline_T4_mean")
    iter1_vs_supervised = iter1["rmse_3d_mm"] - supervised["rmse_3d_mm"]
    iter1_vs_production = iter1["rmse_3d_mm"] - production["rmse_3d_mm"]
    iter2_move_max = next(r["max_mm"] for r in movement_summary if r["metric"] == "iter1_to_iter2_move_mm")
    converged = bool(iter2_move_max <= 2.0)

    lines = ["# Phase 2.9 Estimated-Distance Runtime Candidate\n"]
    lines.append(f"- Generated: `{pd.Timestamp.now().isoformat(timespec='seconds')}`")
    lines.append("- Ground-truth terminology: `Vicon`")
    lines.append("- Scope: one final deployability experiment; no existing reports were modified.")
    lines.append("")
    lines.append("## Result")
    lines.append(
        "Calibration is still supervised and leave-one-position-out: `rho`, `Delta_i`, and `Delta_tag` are fitted using Vicon link distance on the training positions. "
        "Runtime does not use Vicon distance. Iter0 solves with additive correction only. Iter1 computes per-frame estimated link distances from the iter0 solution and applies "
        "`r1 = r - Delta_i/2 - Delta_tag/2 - rho * ||x_hat - a_i||`, then solves again. Iter2 repeats the same operation from iter1."
    )
    lines.append("")
    lines.append(markdown_table(summary_rows, ["mode", "positions", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm", "median_horizontal_mm", "median_vertical_mm", "fit_uses_vicon_link_distance", "runtime_uses_vicon_link_distance"]))
    lines.append("")
    lines.append(
        f"Iter1 RMSE delta vs supervised Vicon-distance row: `{iter1_vs_supervised:.3f}` mm. "
        f"Iter1 RMSE delta vs production baseline: `{iter1_vs_production:.3f}` mm. "
        f"Iter2 convergence check: **{'PASS' if converged else 'MOVES >2 mm'}**."
    )
    lines.append("")
    lines.append(f"![Estimated-distance runtime comparison](figures/{fig_name})")
    lines.append("")
    lines.append("## Iteration Movement")
    lines.append(markdown_table(movement_summary, ["metric", "median_mm", "p95_mm", "max_mm"]))
    lines.append("")
    lines.append("## LOO Fit Coefficients")
    lines.append(markdown_table(fit_summary, ["rho_percent_median", "rho_percent_min", "rho_percent_max", "delta_tag_median_mm", "train_rms_median_mm"]))
    lines.append("")
    lines.append("STOP: Phase 2.9 complete. This is the final diagnostics record.")
    (out_dir / "02_9_estimated_distance_runtime.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    data_dir = args.data_dir.resolve()
    out_dir = args.out_dir.resolve()
    tables_dir = out_dir / "tables"
    figures_dir = out_dir / "figures"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    phase1 = load_phase1_data(data_dir, out_dir)
    primary_truth = load_primary_vicon_anchor_truth(data_dir, phase1.anchor_truth)
    tag_truth = tag_coord_map(phase1.tag_truth)
    sweep_delta = load_sweep_deltas(out_dir)
    link_df = pd.read_csv(tables_dir / "03_tag_link_bias_links.csv")
    link_df["position"] = link_df["position"].astype(str)
    link_df["anchor"] = link_df["anchor"].astype(str)

    read_tr_all_frames, Solver, load_layout_json, SolverConfig, Frame, Observation = load_offline_solver(data_dir)
    capture_dirs = find_static_capture_dirs(data_dir)
    positions = set(link_df["position"].astype(str))
    frames_by_position = {
        pos: read_tr_all_frames(path, tags={"BSF66F"}, min_anchors=4)
        for pos, path in capture_dirs.items()
        if pos in positions
    }

    layout_path = make_vb_zero_delay_layout(out_dir)
    sigma_path = data_dir / "solver" / "outputs" / "v1_to_v4_io_field_check" / "tables" / "anchor_sigma.json"
    layout = load_layout_json(layout_path, sigma_path)

    all_points: dict[str, dict[str, np.ndarray]] = {
        "estimated_distance_iter0_additive_only": {},
        "estimated_distance_iter1": {},
        "estimated_distance_iter2": {},
    }
    movement_rows: list[dict] = []
    fit_rows: list[dict] = []

    def run_one(position: str):
        return position, run_estimated_distance_for_position(
            position=position,
            frames=frames_by_position[position],
            layout=layout,
            Solver=Solver,
            SolverConfig=SolverConfig,
            Frame=Frame,
            Observation=Observation,
            link_df=link_df,
            sweep_delta=sweep_delta,
        )

    max_workers = max(1, min(args.workers, len(frames_by_position)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(run_one, pos): pos for pos in sorted(frames_by_position)}
        for fut in as_completed(futs):
            position, (points, movement, fit_row) = fut.result()
            for mode, point in points.items():
                all_points[mode][position] = point
            movement_rows.extend(movement)
            fit_rows.append(fit_row)

    coords = load_layout_coords(layout_path)
    dst = np.asarray([primary_truth[a] for a in ANCHOR_LABELS], dtype=float)
    rigid = fit_similarity(coords, dst, allow_reflection=True, allow_scale=False)
    per_position_rows = []
    for mode, points in all_points.items():
        rows = tag_error_rows(
            method=mode,
            registration="anchor_only_3d_rigid",
            points_by_position=points,
            tag_truth=tag_truth,
            transform=rigid.apply,
        )
        for row in rows:
            row["fit_uses_vicon_link_distance"] = True
            row["runtime_uses_vicon_link_distance"] = False
        per_position_rows.extend(rows)

    estimated_summary = summarize_errors(per_position_rows, group_cols=["method"])
    for row in estimated_summary:
        row["mode"] = row.pop("method")
        row["fit_uses_vicon_link_distance"] = True
        row["runtime_uses_vicon_link_distance"] = False

    existing_rows: list[dict] = []
    reg_summary = pd.read_csv(tables_dir / "06_registration_harmonization_summary.csv")
    prod = reg_summary[
        (reg_summary["method"].astype(str) == "production_baseline_T4_mean")
        & (reg_summary["registration"].astype(str) == "anchor_only_3d_rigid")
    ].iloc[0]
    existing_rows.append(
        {
            "mode": "production_baseline_T4_mean",
            "positions": int(prod["positions"]),
            "median_3d_mm": float(prod["median_3d_mm"]),
            "p95_3d_mm": float(prod["p95_3d_mm"]),
            "rmse_3d_mm": float(prod["rmse_3d_mm"]),
            "median_horizontal_mm": float(prod["median_horizontal_mm"]),
            "median_vertical_mm": float(prod["median_vertical_mm"]),
            "fit_uses_vicon_link_distance": False,
            "runtime_uses_vicon_link_distance": False,
        }
    )
    runtime_summary = pd.read_csv(tables_dir / "07_runtime_correction_summary.csv")
    for wanted in ["vicon_distance_covariate", "measured_median_range_covariate"]:
        row = runtime_summary[runtime_summary["mode"].astype(str) == wanted].iloc[0]
        existing_rows.append(
            {
                "mode": wanted,
                "positions": int(row["positions"]),
                "median_3d_mm": float(row["median_3d_mm"]),
                "p95_3d_mm": float(row["p95_3d_mm"]),
                "rmse_3d_mm": float(row["rmse_3d_mm"]),
                "median_horizontal_mm": float(row["median_horizontal_mm"]),
                "median_vertical_mm": float(row["median_vertical_mm"]),
                "fit_uses_vicon_link_distance": bool(row["fit_uses_vicon_link_distance"]),
                "runtime_uses_vicon_link_distance": bool(row["runtime_uses_vicon_link_distance"]),
            }
        )

    order = [
        "production_baseline_T4_mean",
        "vicon_distance_covariate",
        "measured_median_range_covariate",
        "estimated_distance_iter0_additive_only",
        "estimated_distance_iter1",
        "estimated_distance_iter2",
    ]
    summary_rows = existing_rows + estimated_summary
    summary_rows = sorted(summary_rows, key=lambda r: order.index(r["mode"]))

    move_df = pd.DataFrame(movement_rows)
    movement_summary = []
    for col in ["iter0_to_iter1_move_mm", "iter1_to_iter2_move_mm"]:
        vals = move_df[col].to_numpy(dtype=float)
        movement_summary.append(
            {
                "metric": col,
                "median_mm": float(np.nanmedian(vals)),
                "p95_mm": float(np.nanpercentile(vals, 95)),
                "max_mm": float(np.nanmax(vals)),
            }
        )
    fit_df = pd.DataFrame(fit_rows)
    fit_summary = [
        {
            "rho_percent_median": float(np.nanmedian(fit_df["rho_percent"].to_numpy(dtype=float))),
            "rho_percent_min": float(np.nanmin(fit_df["rho_percent"].to_numpy(dtype=float))),
            "rho_percent_max": float(np.nanmax(fit_df["rho_percent"].to_numpy(dtype=float))),
            "delta_tag_median_mm": float(np.nanmedian(fit_df["delta_tag_mm"].to_numpy(dtype=float))),
            "train_rms_median_mm": float(np.nanmedian(fit_df["train_rms_mm"].to_numpy(dtype=float))),
        }
    ]

    write_csv_rows(tables_dir / "08_estimated_distance_runtime_positions.csv", per_position_rows)
    write_csv_rows(tables_dir / "08_estimated_distance_runtime_summary.csv", summary_rows)
    write_csv_rows(tables_dir / "08_estimated_distance_iteration_movement.csv", movement_rows)
    write_csv_rows(tables_dir / "08_estimated_distance_iteration_movement_summary.csv", movement_summary)
    write_csv_rows(tables_dir / "08_estimated_distance_loo_fits.csv", fit_rows)
    write_csv_rows(tables_dir / "08_estimated_distance_fit_summary.csv", fit_summary)

    fig = figures_dir / "08_estimated_distance_runtime_comparison.png"
    plot_summary(fig, summary_rows)
    build_report(out_dir, summary_rows, movement_summary, fit_summary, fig.name)
    print(f"Phase 2.9 report written: {out_dir / '02_9_estimated_distance_runtime.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
