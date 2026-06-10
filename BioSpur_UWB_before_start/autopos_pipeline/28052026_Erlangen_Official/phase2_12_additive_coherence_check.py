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
    parser = argparse.ArgumentParser(description="Phase 2.12 additive-coherence check for tag correction ladder.")
    parser.add_argument("--data-dir", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def corrected_frames_additive_rho(
    frames: list[Any],
    additive: np.ndarray,
    rho: float,
    Frame: Any,
    Observation: Any,
) -> list[Any]:
    denom = 1.0 + float(rho)
    out = []
    for frame in frames:
        obs = [
            Observation(
                item.anchor_id,
                (float(item.range_mm) - float(additive[item.anchor_id])) / denom,
                item.quality_percent,
                item.status,
            )
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


def solve_static_modes(
    *,
    data_dir: Path,
    layout: Any,
    Solver: Any,
    SolverConfig: Any,
    Frame: Any,
    Observation: Any,
    frames_by_position: dict[str, list[Any]],
    link_df: pd.DataFrame,
    sweep_delta: dict[str, float],
    workers: int,
) -> tuple[dict[str, dict[str, np.ndarray]], list[dict], list[dict]]:
    sweep_vec = np.asarray([sweep_delta[a] for a in ANCHOR_LABELS], dtype=float)
    modes = [
        "sweep_delta_i_only",
        "sweep_delta_i_plus_additive_only_delta_tag",
        "tagfit_additive_only_coherent",
        "tagfit_joint_delta_only_discard_rho",
        "tagfit_joint_additive_plus_rho",
    ]

    points_by_mode: dict[str, dict[str, np.ndarray]] = {mode: {} for mode in modes}
    details: list[dict] = []
    fit_rows: list[dict] = []

    def run_one(position: str) -> tuple[str, dict[str, np.ndarray], list[dict], list[dict]]:
        train = link_df[link_df["position"].astype(str) != position].reset_index(drop=True)
        fit_add = fit_tag_model_generic(f"additive_only_loo_without_{position}", train, sweep_delta, [])
        fit_joint = fit_tag_model_generic(f"joint_distance_loo_without_{position}", train, sweep_delta, ["vicon_distance_mm"])
        add_delta_tag_only = 0.5 * sweep_vec + 0.5 * float(fit_add.delta_tag_mm)
        add_tagfit_additive = 0.5 * fit_add.anchor_deltas_mm + 0.5 * float(fit_add.delta_tag_mm)
        add_joint = 0.5 * fit_joint.anchor_deltas_mm + 0.5 * float(fit_joint.delta_tag_mm)
        additive_by_mode = {
            "sweep_delta_i_only": 0.5 * sweep_vec,
            "sweep_delta_i_plus_additive_only_delta_tag": add_delta_tag_only,
            "tagfit_additive_only_coherent": add_tagfit_additive,
            "tagfit_joint_delta_only_discard_rho": add_joint,
        }

        solver_points: dict[str, np.ndarray] = {}
        local_details: list[dict] = []

        def solve_frames(mode: str, frames: list[Any]) -> np.ndarray:
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
            local_details.append(
                {
                    "mode": mode,
                    "position": position,
                    "frames_input": int(len(frames_by_position[position])),
                    "frames_solved": int(len(results)),
                    "d3_std_mm": d3_std,
                    "residual_rms_median_mm": residual_median,
                }
            )
            return point

        for mode, additive in additive_by_mode.items():
            frames = corrected_frames_additive(frames_by_position[position], additive, Frame, Observation)
            solver_points[mode] = solve_frames(mode, frames)

        joint_frames = corrected_frames_additive_rho(
            frames_by_position[position],
            add_joint,
            float(fit_joint.coeffs["vicon_distance_mm"]),
            Frame,
            Observation,
        )
        solver_points["tagfit_joint_additive_plus_rho"] = solve_frames("tagfit_joint_additive_plus_rho", joint_frames)

        local_fit_rows = [
            {
                "position": position,
                "fit": "additive_only",
                "train_links": fit_add.n_links,
                "delta_tag_mm": float(fit_add.delta_tag_mm),
                "rho_percent": np.nan,
                "train_rms_mm": float(fit_add.rms_mm),
                "anchor_delta_min_mm": float(np.min(fit_add.anchor_deltas_mm)),
                "anchor_delta_max_mm": float(np.max(fit_add.anchor_deltas_mm)),
            },
            {
                "position": position,
                "fit": "joint_distance_rho",
                "train_links": fit_joint.n_links,
                "delta_tag_mm": float(fit_joint.delta_tag_mm),
                "rho_percent": float(fit_joint.coeffs["vicon_distance_mm"] * 100.0),
                "train_rms_mm": float(fit_joint.rms_mm),
                "anchor_delta_min_mm": float(np.min(fit_joint.anchor_deltas_mm)),
                "anchor_delta_max_mm": float(np.max(fit_joint.anchor_deltas_mm)),
            },
        ]
        return position, solver_points, local_details, local_fit_rows

    max_workers = max(1, min(workers, len(frames_by_position)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(run_one, pos): pos for pos in sorted(frames_by_position)}
        for fut in as_completed(futs):
            position, solver_points, local_details, local_fit_rows = fut.result()
            for mode, point in solver_points.items():
                points_by_mode[mode][position] = point
            details.extend(local_details)
            fit_rows.extend(local_fit_rows)
    details.sort(key=lambda r: (r["mode"], r["position"]))
    fit_rows.sort(key=lambda r: (r["position"], r["fit"]))
    return points_by_mode, details, fit_rows


def fit_summary_rows(fit_rows: list[dict]) -> list[dict]:
    df = pd.DataFrame(fit_rows)
    rows = []
    for fit, g in df.groupby("fit", sort=False):
        rho_vals = g["rho_percent"].to_numpy(dtype=float)
        rho_median = float(np.nanmedian(rho_vals)) if np.isfinite(rho_vals).any() else float("nan")
        rows.append(
            {
                "fit": fit,
                "delta_tag_median_mm": float(np.nanmedian(g["delta_tag_mm"].to_numpy(dtype=float))),
                "delta_tag_min_mm": float(np.nanmin(g["delta_tag_mm"].to_numpy(dtype=float))),
                "delta_tag_max_mm": float(np.nanmax(g["delta_tag_mm"].to_numpy(dtype=float))),
                "rho_percent_median": rho_median,
                "train_rms_median_mm": float(np.nanmedian(g["train_rms_mm"].to_numpy(dtype=float))),
            }
        )
    return rows


def build_report(out_dir: Path, summary_rows: list[dict], fit_summary: list[dict]) -> None:
    lines = ["# Phase 2.12 Additive-Coherence Check\n"]
    lines.append(f"- Generated: `{pd.Timestamp.now().isoformat(timespec='seconds')}`")
    lines.append("- Ground-truth terminology: `Vicon`")
    lines.append("- Scope: final coherence check for intermediate ladder rows; no production files were modified.")
    lines.append("")
    lines.append("## Result")
    lines.append(
        "All rows use the V-B calibrated layout, C-core T4, mean session estimator, and anchor-only 3D rigid/reflection registration. "
        "The check separates coherent additive-only refits from the previous joint distance-rho fit with rho discarded."
    )
    lines.append("")
    lines.append(markdown_table(summary_rows, ["mode", "positions", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm", "median_horizontal_mm", "median_vertical_mm"]))
    lines.append("")
    lines.append("## Fit Coefficients")
    lines.append(markdown_table(fit_summary, ["fit", "delta_tag_median_mm", "delta_tag_min_mm", "delta_tag_max_mm", "rho_percent_median", "train_rms_median_mm"]))
    lines.append("")
    lines.append(
        "Interpretation: the old delta-only row is a strawman if it uses the joint distance-rho intercept while discarding rho. "
        "The coherent additive-only refit should be used for any intermediate additive-only ladder claim."
    )
    lines.append("")
    lines.append("STOP: Phase 2.12 complete. Ladder rows are frozen here.")
    (out_dir / "02_12_additive_coherence_check.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


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

    points_by_mode, details, fit_rows = solve_static_modes(
        data_dir=data_dir,
        layout=layout,
        Solver=Solver,
        SolverConfig=SolverConfig,
        Frame=Frame,
        Observation=Observation,
        frames_by_position=frames_by_position,
        link_df=link_df,
        sweep_delta=sweep_delta,
        workers=args.workers,
    )

    position_rows: list[dict] = []
    for mode, points in points_by_mode.items():
        position_rows.extend(
            tag_error_rows(
                method=mode,
                registration="anchor_only_3d_rigid",
                points_by_position=points,
                tag_truth=tag_truth,
                transform=rigid.apply,
            )
        )

    summary_rows = summarize_errors(position_rows, group_cols=["method"])
    for row in summary_rows:
        row["mode"] = row.pop("method")
    order = [
        "sweep_delta_i_only",
        "sweep_delta_i_plus_additive_only_delta_tag",
        "tagfit_additive_only_coherent",
        "tagfit_joint_delta_only_discard_rho",
        "tagfit_joint_additive_plus_rho",
    ]
    summary_rows = sorted(summary_rows, key=lambda r: order.index(r["mode"]))
    fit_summary = fit_summary_rows(fit_rows)

    write_csv_rows(tables_dir / "11_additive_coherence_positions.csv", position_rows)
    write_csv_rows(tables_dir / "11_additive_coherence_details.csv", details)
    write_csv_rows(tables_dir / "11_additive_coherence_fits.csv", fit_rows)
    write_csv_rows(tables_dir / "11_additive_coherence_fit_summary.csv", fit_summary)
    write_csv_rows(tables_dir / "11_additive_coherence_summary.csv", summary_rows)
    build_report(out_dir, summary_rows, fit_summary)
    print(f"Phase 2.12 report written: {out_dir / '02_12_additive_coherence_check.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
