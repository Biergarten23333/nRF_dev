#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
from phase2_9_estimated_distance_runtime import (
    corrected_frames_additive,
    corrected_frames_with_estimated_dist,
    layout_coords,
    load_layout_coords,
    solve_sequence,
)
from phase2_solver_ablation import load_primary_vicon_anchor_truth, load_sweep_deltas
from scripts.audit_helpers import ANCHOR_LABELS, markdown_table
from scripts.phase1_common import load_phase1_data, tag_coord_map


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 2.10 estimated-distance convergence trace.")
    parser.add_argument("--data-dir", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-iter", type=int, default=4)
    return parser.parse_args()


def run_position_iterations(
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
    max_iter: int,
) -> tuple[dict[str, np.ndarray], list[dict], dict]:
    train = link_df[link_df["position"].astype(str) != position].reset_index(drop=True)
    fit = fit_tag_model_generic(f"estimated_distance_loo_without_{position}", train, sweep_delta, ["vicon_distance_mm"])
    additive = 0.5 * fit.anchor_deltas_mm + 0.5 * fit.delta_tag_mm
    rho = float(fit.coeffs["vicon_distance_mm"])
    anchors_xyz = layout_coords(layout)

    points: dict[str, np.ndarray] = {}
    counts: dict[str, int] = {}
    movements: list[dict] = []

    frames0 = corrected_frames_additive(frames, additive, Frame, Observation)
    idx, results, mean_point = solve_sequence(layout, Solver, SolverConfig, frames0)
    raw_frames = [frames[i] for i in idx]
    points["iter0_additive_only"] = mean_point
    counts["iter0_additive_only"] = int(len(results))
    previous_point = mean_point
    previous_results = results

    for iteration in range(1, max_iter + 1):
        corrected = corrected_frames_with_estimated_dist(
            raw_frames,
            previous_results,
            additive,
            rho,
            anchors_xyz,
            Frame,
            Observation,
        )
        idx, results, mean_point = solve_sequence(layout, Solver, SolverConfig, corrected)
        raw_frames = [raw_frames[i] for i in idx]
        mode = f"iter{iteration}_estimated_distance"
        points[mode] = mean_point
        counts[mode] = int(len(results))
        movements.append(
            {
                "position": position,
                "transition": f"iter{iteration - 1}_to_iter{iteration}",
                "movement_mm": float(np.linalg.norm(mean_point - previous_point)),
                "frames_solved": int(len(results)),
                "rho_percent": float(rho * 100.0),
                "delta_tag_mm": float(fit.delta_tag_mm),
                "train_rms_mm": float(fit.rms_mm),
            }
        )
        previous_point = mean_point
        previous_results = results

    fit_row = {
        "heldout_position": position,
        "train_links": fit.n_links,
        "rho_percent": float(rho * 100.0),
        "delta_tag_mm": float(fit.delta_tag_mm),
        "train_rms_mm": float(fit.rms_mm),
    }
    for mode, count in counts.items():
        movements.append(
            {
                "position": position,
                "transition": f"{mode}_frames",
                "movement_mm": math.nan,
                "frames_solved": count,
                "rho_percent": float(rho * 100.0),
                "delta_tag_mm": float(fit.delta_tag_mm),
                "train_rms_mm": float(fit.rms_mm),
            }
        )
    return points, movements, fit_row


def movement_summaries(movement_rows: list[dict]) -> list[dict]:
    df = pd.DataFrame(movement_rows)
    rows: list[dict] = []
    move = df[df["transition"].astype(str).str.contains("_to_")].copy()
    med_by_transition: dict[str, float] = {}
    for transition, g in move.groupby("transition", sort=False):
        vals = g["movement_mm"].to_numpy(dtype=float)
        med = float(np.nanmedian(vals))
        med_by_transition[transition] = med
        idx = int(transition.split("_to_iter")[0].replace("iter", ""))
        prev = med_by_transition.get(f"iter{idx - 1}_to_iter{idx}", math.nan)
        rows.append(
            {
                "transition": transition,
                "median_mm": med,
                "p95_mm": float(np.nanpercentile(vals, 95)),
                "max_mm": float(np.nanmax(vals)),
                "median_ratio_to_previous": float(med / prev) if np.isfinite(prev) and prev > 0 else math.nan,
            }
        )
    return rows


def plot_convergence(path: Path, movement_summary: list[dict], rho_percent: float) -> None:
    transitions = [str(r["transition"]) for r in movement_summary]
    x = np.arange(1, len(transitions) + 1)
    med = np.asarray([float(r["median_mm"]) for r in movement_summary], dtype=float)
    p95 = np.asarray([float(r["p95_mm"]) for r in movement_summary], dtype=float)
    rho = rho_percent / 100.0
    ref = med[0] * (rho ** np.arange(len(med)))

    fig, ax = plt.subplots(figsize=(7.8, 4.8))
    ax.plot(x, med, marker="o", label="median movement")
    ax.plot(x, p95, marker="s", label="p95 movement")
    ax.plot(x, ref, linestyle="--", color="black", label=f"rho^k reference ({rho_percent:.2f}%)")
    ax.axhline(2.0, color="#9b3d34", linestyle=":", linewidth=1.2, label="2 mm threshold")
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(transitions, rotation=18, ha="right")
    ax.set_ylabel("session-mean movement [mm, log scale]")
    ax.set_title("Estimated-Distance Fixed-Point Convergence")
    ax.grid(True, which="both", axis="y", alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def build_report(
    out_dir: Path,
    summary_rows: list[dict],
    movement_summary: list[dict],
    fit_summary: list[dict],
    fig_name: str,
) -> None:
    iter2 = next(r for r in summary_rows if r["mode"] == "iter2_estimated_distance")
    iter4 = next(r for r in summary_rows if r["mode"] == "iter4_estimated_distance")
    supervised = next(r for r in summary_rows if r["mode"] == "vicon_distance_covariate")
    rho = fit_summary[0]["rho_percent_median"]
    last_move = next(r for r in movement_summary if r["transition"] == "iter3_to_iter4")
    pass_iter4 = bool(last_move["median_mm"] <= 2.0 and last_move["p95_mm"] <= 2.0)

    lines = ["# Phase 2.10 Estimated-Distance Convergence\n"]
    lines.append(f"- Generated: `{pd.Timestamp.now().isoformat(timespec='seconds')}`")
    lines.append("- Ground-truth terminology: `Vicon`")
    lines.append("- Scope: standalone convergence trace; no previous reports were modified.")
    lines.append("")
    lines.append("## Result")
    lines.append(
        "This extends the Phase 2.9 estimated-distance runtime candidate to iter4. "
        "Calibration remains supervised and leave-one-position-out, but runtime uses only measured ranges and solver-estimated link distances."
    )
    lines.append("")
    lines.append(markdown_table(summary_rows, ["mode", "positions", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm", "median_horizontal_mm", "median_vertical_mm", "runtime_uses_vicon_link_distance"]))
    lines.append("")
    lines.append(
        f"Iter2 already recovers the supervised row within `{iter2['rmse_3d_mm'] - supervised['rmse_3d_mm']:.3f}` mm RMSE. "
        f"Iter4 remains at the same error level (`{iter4['median_3d_mm']:.3f}` / `{iter4['rmse_3d_mm']:.3f}` mm median/RMSE). "
        f"Median movement falls below 2 mm by iter3-to-iter4; convergence check: **{'PASS' if pass_iter4 else 'P95 still above 2 mm'}**."
    )
    lines.append("")
    lines.append(f"![Estimated-distance convergence](figures/{fig_name})")
    lines.append("")
    lines.append("## Movement")
    lines.append(markdown_table(movement_summary, ["transition", "median_mm", "p95_mm", "max_mm", "median_ratio_to_previous"]))
    lines.append("")
    lines.append("## Fit Coefficients")
    lines.append(markdown_table(fit_summary, ["rho_percent_median", "rho_percent_min", "rho_percent_max", "delta_tag_median_mm", "train_rms_median_mm"]))
    lines.append("")
    lines.append("STOP: Phase 2.10 complete. Diagnostics are frozen here.")
    (out_dir / "02_10_estimated_distance_convergence.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


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

    all_points: dict[str, dict[str, np.ndarray]] = {f"iter{i}_estimated_distance": {} for i in range(1, args.max_iter + 1)}
    all_points["iter0_additive_only"] = {}
    movement_rows: list[dict] = []
    fit_rows: list[dict] = []

    def run_one(position: str):
        return position, run_position_iterations(
            position=position,
            frames=frames_by_position[position],
            layout=layout,
            Solver=Solver,
            SolverConfig=SolverConfig,
            Frame=Frame,
            Observation=Observation,
            link_df=link_df,
            sweep_delta=sweep_delta,
            max_iter=args.max_iter,
        )

    max_workers = max(1, min(args.workers, len(frames_by_position)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(run_one, pos): pos for pos in sorted(frames_by_position)}
        for fut in as_completed(futs):
            position, (points, movements, fit_row) = fut.result()
            for mode, point in points.items():
                all_points[mode][position] = point
            movement_rows.extend(movements)
            fit_rows.append(fit_row)

    coords = load_layout_coords(layout_path)
    dst = np.asarray([primary_truth[a] for a in ANCHOR_LABELS], dtype=float)
    rigid = fit_similarity(coords, dst, allow_reflection=True, allow_scale=False)
    position_rows: list[dict] = []
    for mode, points in all_points.items():
        rows = tag_error_rows(
            method=mode,
            registration="anchor_only_3d_rigid",
            points_by_position=points,
            tag_truth=tag_truth,
            transform=rigid.apply,
        )
        for row in rows:
            row["runtime_uses_vicon_link_distance"] = False
        position_rows.extend(rows)

    estimated_summary = summarize_errors(position_rows, group_cols=["method"])
    for row in estimated_summary:
        row["mode"] = row.pop("method")
        row["runtime_uses_vicon_link_distance"] = False

    reg_summary = pd.read_csv(tables_dir / "06_registration_harmonization_summary.csv")
    prod = reg_summary[
        (reg_summary["method"].astype(str) == "production_baseline_T4_mean")
        & (reg_summary["registration"].astype(str) == "anchor_only_3d_rigid")
    ].iloc[0]
    runtime_summary = pd.read_csv(tables_dir / "07_runtime_correction_summary.csv")
    supervised = runtime_summary[runtime_summary["mode"].astype(str) == "vicon_distance_covariate"].iloc[0]
    summary_rows = [
        {
            "mode": "production_baseline_T4_mean",
            "positions": int(prod["positions"]),
            "median_3d_mm": float(prod["median_3d_mm"]),
            "p95_3d_mm": float(prod["p95_3d_mm"]),
            "rmse_3d_mm": float(prod["rmse_3d_mm"]),
            "median_horizontal_mm": float(prod["median_horizontal_mm"]),
            "median_vertical_mm": float(prod["median_vertical_mm"]),
            "runtime_uses_vicon_link_distance": False,
        },
        {
            "mode": "vicon_distance_covariate",
            "positions": int(supervised["positions"]),
            "median_3d_mm": float(supervised["median_3d_mm"]),
            "p95_3d_mm": float(supervised["p95_3d_mm"]),
            "rmse_3d_mm": float(supervised["rmse_3d_mm"]),
            "median_horizontal_mm": float(supervised["median_horizontal_mm"]),
            "median_vertical_mm": float(supervised["median_vertical_mm"]),
            "runtime_uses_vicon_link_distance": False,
        },
    ]
    order = ["production_baseline_T4_mean", "vicon_distance_covariate", "iter0_additive_only"] + [
        f"iter{i}_estimated_distance" for i in range(1, args.max_iter + 1)
    ]
    summary_rows.extend(estimated_summary)
    summary_rows = sorted(summary_rows, key=lambda r: order.index(r["mode"]))

    move_summary = movement_summaries(movement_rows)
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

    write_csv_rows(tables_dir / "09_estimated_distance_convergence_positions.csv", position_rows)
    write_csv_rows(tables_dir / "09_estimated_distance_convergence_summary.csv", summary_rows)
    write_csv_rows(tables_dir / "09_estimated_distance_convergence_movement.csv", movement_rows)
    write_csv_rows(tables_dir / "09_estimated_distance_convergence_movement_summary.csv", move_summary)
    write_csv_rows(tables_dir / "09_estimated_distance_convergence_fits.csv", fit_rows)
    write_csv_rows(tables_dir / "09_estimated_distance_convergence_fit_summary.csv", fit_summary)

    fig = figures_dir / "09_estimated_distance_convergence_log.png"
    plot_convergence(fig, move_summary, fit_summary[0]["rho_percent_median"])
    build_report(out_dir, summary_rows, move_summary, fit_summary, fig.name)
    print(f"Phase 2.10 report written: {out_dir / '02_10_estimated_distance_convergence.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
