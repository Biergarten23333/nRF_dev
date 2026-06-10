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
from phase2_7_final_closure import (
    fit_similarity,
    fit_tag_model_generic,
    summarize_errors,
    summarize_solver_results,
    tag_error_rows,
    write_csv_rows,
)
from phase2_solver_ablation import load_primary_vicon_anchor_truth, load_sweep_deltas
from scripts.audit_helpers import ANCHOR_LABELS, markdown_table
from scripts.phase1_common import load_phase1_data, tag_coord_map


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 2.8 runtime-measured range correction check.")
    parser.add_argument("--data-dir", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def make_vb_zero_delay_layout(out_dir: Path) -> Path:
    src = out_dir / "phase2_solver_layouts" / "V-B_calibrated" / "layout.json"
    dst = out_dir / "phase2_8_layouts" / "V-B_calibrated_zero_delay" / "layout.json"
    obj = json.loads(src.read_text(encoding="utf-8"))
    for item in obj["anchors"]:
        item["d_anchor_mm"] = 0.0
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    return dst


def load_layout_coords(path: Path) -> np.ndarray:
    obj = json.loads(path.read_text(encoding="utf-8"))
    anchors = sorted(obj["anchors"], key=lambda a: int(a["id"]))
    return np.asarray([[a["x_mm"], a["y_mm"], a["z_mm"]] for a in anchors], dtype=float)


def corrected_frames(
    frames: list[Any],
    fit: Any,
    mode: str,
    Frame: Any,
    Observation: Any,
) -> list[Any]:
    additive = 0.5 * fit.anchor_deltas_mm + 0.5 * fit.delta_tag_mm
    if mode == "vicon_distance_covariate":
        coeff = float(fit.coeffs["vicon_distance_mm"])
    elif mode == "measured_median_range_covariate":
        coeff = float(fit.coeffs["median_range_mm"])
    else:
        raise ValueError(mode)

    out = []
    for frame in frames:
        obs = []
        for item in frame.observations:
            r = float(item.range_mm)
            a = float(additive[item.anchor_id])
            if mode == "vicon_distance_covariate":
                # Model: r = (1 + rho) * d + additive.
                r_corr = (r - a) / (1.0 + coeff)
            else:
                # Model: r - d = additive + gamma * r.
                # Algebraic runtime form: d = (1 - gamma) * r - additive.
                r_corr = (1.0 - coeff) * r - a
            obs.append(Observation(item.anchor_id, r_corr, item.quality_percent, item.status))
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


def solve_mode(
    *,
    mode: str,
    data_dir: Path,
    layout_path: Path,
    sigma_path: Path,
    frames_by_position: dict[str, list[Any]],
    link_df: pd.DataFrame,
    sweep_delta: dict[str, float],
    workers: int,
) -> tuple[dict[str, np.ndarray], list[dict], list[dict]]:
    _read, TagPositionSolver, load_layout_json, SolverConfig, Frame, Observation = load_offline_solver(data_dir)
    layout = load_layout_json(layout_path, sigma_path)
    cov = "vicon_distance_mm" if mode == "vicon_distance_covariate" else "median_range_mm"
    points: dict[str, np.ndarray] = {}
    detail_rows: list[dict] = []
    fit_rows: list[dict] = []

    def run_one(position: str) -> tuple[str, np.ndarray, dict, dict]:
        train = link_df[link_df["position"].astype(str) != position].reset_index(drop=True)
        fit = fit_tag_model_generic(f"{mode}_loo_without_{position}", train, sweep_delta, [cov])
        frames = corrected_frames(frames_by_position[position], fit, mode, Frame, Observation)
        solver = TagPositionSolver(layout, SolverConfig(method="T4"))
        results = []
        for frame in frames:
            result = solver.solve_frame(frame)
            if result is not None:
                results.append(result)
        summary = summarize_solver_results(results)
        coeff = float(fit.coeffs[cov])
        detail = {
            "mode": mode,
            "position": position,
            "frames_input": int(len(frames_by_position[position])),
            "frames_solved": int(summary["frames_solved"]),
            "d3_std_mm": float(summary["d3_std_mm"]),
            "residual_rms_median_mm": float(summary["residual_rms_median_mm"]),
        }
        fit_row = {
            "mode": mode,
            "heldout_position": position,
            "train_links": fit.n_links,
            "delta_tag_mm": fit.delta_tag_mm,
            "train_rms_mm": fit.rms_mm,
            "coefficient": coeff,
            "coefficient_percent": coeff * 100.0,
            "covariate": cov,
        }
        return position, summary["point"], detail, fit_row

    max_workers = max(1, min(workers, len(frames_by_position)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(run_one, pos): pos for pos in sorted(frames_by_position)}
        for fut in as_completed(futs):
            position, point, detail, fit_row = fut.result()
            points[position] = point
            detail_rows.append(detail)
            fit_rows.append(fit_row)
    detail_rows.sort(key=lambda r: (r["mode"], r["position"]))
    fit_rows.sort(key=lambda r: (r["mode"], r["heldout_position"]))
    return points, detail_rows, fit_rows


def plot_summary(path: Path, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(7.0, 4.3))
    labels = df["mode"].astype(str).tolist()
    x = np.arange(len(labels))
    ax.bar(x - 0.18, df["median_3d_mm"].to_numpy(dtype=float), width=0.36, label="median")
    ax.bar(x + 0.18, df["rmse_3d_mm"].to_numpy(dtype=float), width=0.36, label="RMSE")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=15, ha="right")
    ax.set_ylabel("static tag error [mm]")
    ax.set_title("Distance-rho Runtime Correction Check")
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def build_report(out_dir: Path, summary_rows: list[dict], fit_summary: list[dict], fig_name: str) -> None:
    vicon = next(r for r in summary_rows if r["mode"] == "vicon_distance_covariate")
    measured = next(r for r in summary_rows if r["mode"] == "measured_median_range_covariate")
    delta_med = measured["median_3d_mm"] - vicon["median_3d_mm"]
    delta_rmse = measured["rmse_3d_mm"] - vicon["rmse_3d_mm"]
    delta_p95 = measured["p95_3d_mm"] - vicon["p95_3d_mm"]
    holds = abs(delta_med) <= 5.0 and abs(delta_rmse) <= 5.0 and abs(delta_p95) <= 8.0

    lines = ["# Phase 2.8 Runtime Range Correction Check\n"]
    lines.append(f"- Generated: `{pd.Timestamp.now().isoformat(timespec='seconds')}`")
    lines.append("- Ground-truth terminology: `Vicon`")
    lines.append("- Scope: single runtime-covariate check; diagnostics hard-freeze after this report.")
    lines.append("")
    lines.append("## Result")
    lines.append(
        "Both rows use V-B layout, C-core T4, mean session estimator, leave-one-position-out calibration, "
        "and anchor-only 3D rigid/reflection registration. The first row fits `rho` against Vicon link distance, then applies "
        "`r_corr = (r - Delta_i/2 - Delta_tag/2) / (1 + rho)`. The second row fits the proportional term against measured "
        "session-median range and applies the exact measured-covariate runtime correction "
        "`r_corr = (1 - gamma) * r - Delta_i/2 - Delta_tag/2`."
    )
    lines.append("")
    lines.append(markdown_table(summary_rows, ["mode", "positions", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm", "median_horizontal_mm", "median_vertical_mm", "fit_uses_vicon_link_distance", "runtime_uses_vicon_link_distance"]))
    lines.append("")
    lines.append(
        f"Measured-range correction delta vs Vicon-distance row: median `{delta_med:.3f}` mm, "
        f"RMSE `{delta_rmse:.3f}` mm, P95 `{delta_p95:.3f}` mm. "
        f"Runtime implementability check: **{'PASS' if holds else 'FAIL'}**."
    )
    lines.append("")
    lines.append(f"![Runtime correction comparison](figures/{fig_name})")
    lines.append("")
    lines.append("## LOO Fit Coefficients")
    lines.append(markdown_table(fit_summary, ["mode", "covariate", "coefficient_percent_median", "coefficient_percent_min", "coefficient_percent_max", "delta_tag_median_mm", "train_rms_median_mm"]))
    lines.append("")
    lines.append("STOP: Phase 2.8 complete. Hard freeze diagnostics here.")
    (out_dir / "02_8_runtime_correction.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


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

    read_tr_all_frames, _TagPositionSolver, _load_layout_json, _SolverConfig, _Frame, _Observation = load_offline_solver(data_dir)
    capture_dirs = find_static_capture_dirs(data_dir)
    positions = set(link_df["position"].astype(str))
    frames_by_position = {
        pos: read_tr_all_frames(path, tags={"BSF66F"}, min_anchors=4)
        for pos, path in capture_dirs.items()
        if pos in positions
    }

    layout_path = make_vb_zero_delay_layout(out_dir)
    sigma_path = data_dir / "solver" / "outputs" / "v1_to_v4_io_field_check" / "tables" / "anchor_sigma.json"
    modes = ["vicon_distance_covariate", "measured_median_range_covariate"]
    all_points: dict[str, dict[str, np.ndarray]] = {}
    details: list[dict] = []
    fits: list[dict] = []
    for mode in modes:
        points, detail_rows, fit_rows = solve_mode(
            mode=mode,
            data_dir=data_dir,
            layout_path=layout_path,
            sigma_path=sigma_path,
            frames_by_position=frames_by_position,
            link_df=link_df,
            sweep_delta=sweep_delta,
            workers=args.workers,
        )
        all_points[mode] = points
        details.extend(detail_rows)
        fits.extend(fit_rows)

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
            row["fit_uses_vicon_link_distance"] = bool(mode == "vicon_distance_covariate")
            row["runtime_uses_vicon_link_distance"] = False
        per_position_rows.extend(rows)
    summary_rows = summarize_errors(per_position_rows, group_cols=["method"])
    for row in summary_rows:
        row["mode"] = row.pop("method")
        row["fit_uses_vicon_link_distance"] = bool(row["mode"] == "vicon_distance_covariate")
        row["runtime_uses_vicon_link_distance"] = False
    summary_rows = sorted(summary_rows, key=lambda r: modes.index(r["mode"]))

    fit_df = pd.DataFrame(fits)
    fit_summary = []
    for mode, g in fit_df.groupby("mode", sort=False):
        fit_summary.append(
            {
                "mode": mode,
                "covariate": str(g["covariate"].iloc[0]),
                "coefficient_percent_median": float(np.nanmedian(g["coefficient_percent"].to_numpy(dtype=float))),
                "coefficient_percent_min": float(np.nanmin(g["coefficient_percent"].to_numpy(dtype=float))),
                "coefficient_percent_max": float(np.nanmax(g["coefficient_percent"].to_numpy(dtype=float))),
                "delta_tag_median_mm": float(np.nanmedian(g["delta_tag_mm"].to_numpy(dtype=float))),
                "train_rms_median_mm": float(np.nanmedian(g["train_rms_mm"].to_numpy(dtype=float))),
            }
        )

    write_csv_rows(tables_dir / "07_runtime_correction_positions.csv", per_position_rows)
    write_csv_rows(tables_dir / "07_runtime_correction_summary.csv", summary_rows)
    write_csv_rows(tables_dir / "07_runtime_correction_loo_fits.csv", fits)
    write_csv_rows(tables_dir / "07_runtime_correction_fit_summary.csv", fit_summary)
    write_csv_rows(tables_dir / "07_runtime_correction_solve_details.csv", details)

    fig = figures_dir / "07_runtime_correction_comparison.png"
    plot_summary(fig, summary_rows)
    build_report(out_dir, summary_rows, fit_summary, fig.name)
    print(f"Phase 2.8 report written: {out_dir / '02_8_runtime_correction.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
