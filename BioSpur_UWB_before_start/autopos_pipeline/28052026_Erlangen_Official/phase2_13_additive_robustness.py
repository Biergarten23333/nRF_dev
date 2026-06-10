#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from phase2_6_diagnostics_closure import find_static_capture_dirs, load_offline_solver
from phase2_7_final_closure import fit_similarity, fit_tag_model_generic, summarize_errors, tag_error_rows, write_csv_rows
from phase2_8_runtime_correction import make_vb_zero_delay_layout
from phase2_9_estimated_distance_runtime import corrected_frames_additive, load_layout_coords
from phase2_solver_ablation import load_primary_vicon_anchor_truth, load_sweep_deltas
from scripts.audit_helpers import ANCHOR_LABELS, markdown_table
from scripts.phase1_common import load_phase1_data, tag_coord_map


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 2.13 robustness check for coherent additive-only tag calibration.")
    parser.add_argument("--data-dir", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def top12_mask(link_df: pd.DataFrame, top12_df: pd.DataFrame) -> pd.Series:
    keys = {(str(r.position), str(r.anchor)) for r in top12_df.itertuples(index=False)}
    return link_df.apply(lambda r: (str(r["position"]), str(r["anchor"])) in keys, axis=1)


def solve_additive_no_top12(
    *,
    data_dir: Path,
    layout: Any,
    Solver: Any,
    SolverConfig: Any,
    Frame: Any,
    Observation: Any,
    frames_by_position: dict[str, list[Any]],
    link_df: pd.DataFrame,
    outlier_mask: pd.Series,
    sweep_delta: dict[str, float],
    workers: int,
) -> tuple[dict[str, np.ndarray], list[dict], list[dict]]:
    points: dict[str, np.ndarray] = {}
    details: list[dict] = []
    fits: list[dict] = []

    def run_one(position: str) -> tuple[str, np.ndarray, dict, dict]:
        train = link_df[
            (link_df["position"].astype(str) != position)
            & (~outlier_mask)
        ].reset_index(drop=True)
        fit = fit_tag_model_generic(f"additive_no_top12_loo_without_{position}", train, sweep_delta, [])
        additive = 0.5 * fit.anchor_deltas_mm + 0.5 * float(fit.delta_tag_mm)
        frames = corrected_frames_additive(frames_by_position[position], additive, Frame, Observation)
        solver = Solver(layout, SolverConfig(method="T4"))
        results = []
        for frame in frames:
            result = solver.solve_frame(frame)
            if result is not None:
                results.append(result)
        if results:
            pts = np.asarray([[r.x_mm, r.y_mm, r.z_mm] for r in results], dtype=float)
            point = np.nanmean(pts, axis=0)
            d3 = np.linalg.norm(pts - point[None, :], axis=1)
            residual = np.asarray([r.residual_rms_mm for r in results], dtype=float)
            d3_std = float(np.sqrt(np.nanmean(d3 * d3)))
            residual_median = float(np.nanmedian(residual))
        else:
            point = np.full(3, np.nan)
            d3_std = float("nan")
            residual_median = float("nan")
        detail = {
            "mode": "tagfit_additive_only_no_top12_train",
            "position": position,
            "frames_input": int(len(frames_by_position[position])),
            "frames_solved": int(len(results)),
            "d3_std_mm": d3_std,
            "residual_rms_median_mm": residual_median,
        }
        fit_row = {
            "position": position,
            "train_links": int(fit.n_links),
            "delta_tag_mm": float(fit.delta_tag_mm),
            "train_rms_mm": float(fit.rms_mm),
            "anchor_delta_min_mm": float(np.min(fit.anchor_deltas_mm)),
            "anchor_delta_max_mm": float(np.max(fit.anchor_deltas_mm)),
        }
        return position, point, detail, fit_row

    max_workers = max(1, min(workers, len(frames_by_position)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(run_one, pos): pos for pos in sorted(frames_by_position)}
        for fut in as_completed(futs):
            position, point, detail, fit_row = fut.result()
            points[position] = point
            details.append(detail)
            fits.append(fit_row)
    details.sort(key=lambda r: r["position"])
    fits.sort(key=lambda r: r["position"])
    return points, details, fits


def summary_from_position_file(path: Path, method: str, registration: str = "anchor_only_3d_rigid") -> list[dict]:
    df = pd.read_csv(path)
    sub = df[df["method"].astype(str) == method].copy()
    if "registration" in sub.columns:
        sub = sub[sub["registration"].astype(str) == registration].copy()
    return sub.to_dict("records")


def make_comparison_rows(
    production_rows: list[dict],
    additive_rows: list[dict],
    joint_rows: list[dict],
    no_top12_rows: list[dict],
) -> list[dict]:
    def by_pos(rows: list[dict]) -> dict[str, float]:
        return {str(r["position"]): float(r["err_3d_mm"]) for r in rows}

    prod = by_pos(production_rows)
    add = by_pos(additive_rows)
    joint = by_pos(joint_rows)
    no12 = by_pos(no_top12_rows)
    rows = []
    for pos in sorted(prod):
        p = prod[pos]
        row = {
            "position": pos,
            "production_err_3d_mm": p,
            "additive_only_err_3d_mm": add[pos],
            "additive_no_top12_err_3d_mm": no12[pos],
            "joint_rho_err_3d_mm": joint[pos],
            "additive_only_delta_vs_prod_mm": add[pos] - p,
            "additive_no_top12_delta_vs_prod_mm": no12[pos] - p,
            "joint_rho_delta_vs_prod_mm": joint[pos] - p,
            "additive_only_improves": bool(add[pos] < p),
            "additive_no_top12_improves": bool(no12[pos] < p),
            "joint_rho_improves": bool(joint[pos] < p),
        }
        rows.append(row)
    return rows


def improvement_summary(comparison_rows: list[dict]) -> list[dict]:
    df = pd.DataFrame(comparison_rows)
    specs = [
        ("tagfit_additive_only_coherent", "additive_only"),
        ("tagfit_additive_only_no_top12_train", "additive_no_top12"),
        ("tagfit_joint_additive_plus_rho", "joint_rho"),
    ]
    rows = []
    for mode, prefix in specs:
        delta = df[f"{prefix}_delta_vs_prod_mm"].to_numpy(dtype=float)
        improves = df[f"{prefix}_improves"].to_numpy(dtype=bool)
        rows.append(
            {
                "mode": mode,
                "positions": int(len(df)),
                "improved_vs_production": int(np.sum(improves)),
                "worse_or_equal_vs_production": int(len(df) - np.sum(improves)),
                "median_delta_vs_production_mm": float(np.nanmedian(delta)),
                "rmse_delta_vs_production_mm": float(np.sqrt(np.nanmean(delta * delta))),
                "p95_delta_vs_production_mm": float(np.nanpercentile(delta, 95)),
                "max_worse_delta_mm": float(np.nanmax(delta)),
                "max_better_delta_mm": float(np.nanmin(delta)),
            }
        )
    return rows


def fit_summary(fit_rows: list[dict]) -> list[dict]:
    df = pd.DataFrame(fit_rows)
    return [
        {
            "mode": "tagfit_additive_only_no_top12_train",
            "delta_tag_median_mm": float(np.nanmedian(df["delta_tag_mm"].to_numpy(dtype=float))),
            "delta_tag_min_mm": float(np.nanmin(df["delta_tag_mm"].to_numpy(dtype=float))),
            "delta_tag_max_mm": float(np.nanmax(df["delta_tag_mm"].to_numpy(dtype=float))),
            "train_rms_median_mm": float(np.nanmedian(df["train_rms_mm"].to_numpy(dtype=float))),
            "train_links_median": float(np.nanmedian(df["train_links"].to_numpy(dtype=float))),
        }
    ]


def build_report(
    out_dir: Path,
    summary_rows: list[dict],
    improvement_rows: list[dict],
    fit_rows: list[dict],
) -> None:
    lines = ["# Phase 2.13 Additive-Only Robustness Check\n"]
    lines.append(f"- Generated: `{pd.Timestamp.now().isoformat(timespec='seconds')}`")
    lines.append("- Ground-truth terminology: `Vicon`")
    lines.append("- Scope: robustness check for the new coherent additive-only headline row; no production files were modified.")
    lines.append("")
    lines.append("## Result")
    lines.append(
        "All rows use C-core T4, mean session estimator, and anchor-only 3D rigid/reflection registration. "
        "`tagfit_additive_only_no_top12_train` refits the coherent additive-only tag model with the top-12 absolute-bias links removed from each LOO training set."
    )
    lines.append("")
    lines.append(markdown_table(summary_rows, ["mode", "positions", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm", "median_horizontal_mm", "median_vertical_mm"]))
    lines.append("")
    lines.append("## Improvement Counts")
    lines.append(markdown_table(improvement_rows, ["mode", "positions", "improved_vs_production", "worse_or_equal_vs_production", "median_delta_vs_production_mm", "max_worse_delta_mm", "max_better_delta_mm"]))
    lines.append("")
    lines.append("## No-Top12 Fit Coefficients")
    lines.append(markdown_table(fit_rows, ["mode", "delta_tag_median_mm", "delta_tag_min_mm", "delta_tag_max_mm", "train_rms_median_mm", "train_links_median"]))
    lines.append("")
    lines.append("STOP: Phase 2.13 complete. Additive-only headline robustness is frozen here.")
    (out_dir / "02_13_additive_robustness.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    data_dir = args.data_dir.resolve()
    out_dir = args.out_dir.resolve()
    tables_dir = out_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    phase1 = load_phase1_data(data_dir, out_dir)
    primary_truth = load_primary_vicon_anchor_truth(data_dir, phase1.anchor_truth)
    tag_truth = tag_coord_map(phase1.tag_truth)
    sweep_delta = load_sweep_deltas(out_dir)
    link_df = pd.read_csv(tables_dir / "03_tag_link_bias_links.csv")
    link_df["position"] = link_df["position"].astype(str)
    link_df["anchor"] = link_df["anchor"].astype(str)
    top12_df = pd.read_csv(tables_dir / "04_tag_side_top12_abs_bias_links.csv")
    mask = top12_mask(link_df, top12_df)

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
    layout_xyz = load_layout_coords(layout_path)
    truth_xyz = np.asarray([primary_truth[a] for a in ANCHOR_LABELS], dtype=float)
    rigid = fit_similarity(layout_xyz, truth_xyz, allow_reflection=True, allow_scale=False)

    no_top12_points, no_top12_details, no_top12_fits = solve_additive_no_top12(
        data_dir=data_dir,
        layout=layout,
        Solver=Solver,
        SolverConfig=SolverConfig,
        Frame=Frame,
        Observation=Observation,
        frames_by_position=frames_by_position,
        link_df=link_df,
        outlier_mask=mask,
        sweep_delta=sweep_delta,
        workers=args.workers,
    )
    no_top12_rows = tag_error_rows(
        method="tagfit_additive_only_no_top12_train",
        registration="anchor_only_3d_rigid",
        points_by_position=no_top12_points,
        tag_truth=tag_truth,
        transform=rigid.apply,
    )

    prod_rows = summary_from_position_file(tables_dir / "06_registration_harmonization_positions.csv", "production_baseline_T4_mean")
    add_rows = summary_from_position_file(tables_dir / "11_additive_coherence_positions.csv", "tagfit_additive_only_coherent")
    joint_rows = summary_from_position_file(tables_dir / "11_additive_coherence_positions.csv", "tagfit_joint_additive_plus_rho")
    all_rows = []
    for mode, rows in [
        ("production_baseline_T4_mean", prod_rows),
        ("tagfit_additive_only_coherent", add_rows),
        ("tagfit_additive_only_no_top12_train", no_top12_rows),
        ("tagfit_joint_additive_plus_rho", joint_rows),
    ]:
        for row in rows:
            copy = dict(row)
            copy["method"] = mode
            all_rows.append(copy)
    summary_rows = summarize_errors(all_rows, group_cols=["method"])
    for row in summary_rows:
        row["mode"] = row.pop("method")
    order = [
        "production_baseline_T4_mean",
        "tagfit_additive_only_coherent",
        "tagfit_additive_only_no_top12_train",
        "tagfit_joint_additive_plus_rho",
    ]
    summary_rows = sorted(summary_rows, key=lambda r: order.index(r["mode"]))

    comparison_rows = make_comparison_rows(prod_rows, add_rows, joint_rows, no_top12_rows)
    improvement_rows = improvement_summary(comparison_rows)
    no_top12_fit_summary = fit_summary(no_top12_fits)

    write_csv_rows(tables_dir / "12_additive_robustness_no_top12_positions.csv", no_top12_rows)
    write_csv_rows(tables_dir / "12_additive_robustness_no_top12_details.csv", no_top12_details)
    write_csv_rows(tables_dir / "12_additive_robustness_no_top12_fits.csv", no_top12_fits)
    write_csv_rows(tables_dir / "12_additive_robustness_position_comparison.csv", comparison_rows)
    write_csv_rows(tables_dir / "12_additive_robustness_improvement_counts.csv", improvement_rows)
    write_csv_rows(tables_dir / "12_additive_robustness_summary.csv", summary_rows)
    write_csv_rows(tables_dir / "12_additive_robustness_fit_summary.csv", no_top12_fit_summary)
    build_report(out_dir, summary_rows, improvement_rows, no_top12_fit_summary)
    print(f"Phase 2.13 report written: {out_dir / '02_13_additive_robustness.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
