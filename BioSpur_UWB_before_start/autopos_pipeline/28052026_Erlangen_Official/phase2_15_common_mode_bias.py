#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
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


FACING_ORDER = ("ABEF", "BCGF", "CDHG", "ADHE")


@dataclass
class FacingTagFit:
    name: str
    delta_tag_by_facing: dict[str, float]
    anchor_deltas_mm: np.ndarray
    rms_mm: float
    n_links: int
    rank: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 2.15 common-mode tag bias structure test.")
    parser.add_argument("--data-dir", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def fit_tag_model_per_facing(
    name: str,
    link_df: pd.DataFrame,
    sweep_delta: dict[str, float],
) -> FacingTagFit:
    work = link_df.reset_index(drop=True).copy()
    mean_sweep = float(np.mean([sweep_delta[a] for a in ANCHOR_LABELS]))
    facings = [f for f in FACING_ORDER if f in set(work["facing"].astype(str))]
    for f in sorted(set(work["facing"].astype(str))):
        if f not in facings:
            facings.append(f)
    x = np.zeros((len(work), 7 + len(facings)), dtype=float)
    for row, anchor in enumerate(work["anchor"].astype(str)):
        idx = ANCHOR_LABELS.index(anchor)
        if idx < 7:
            x[row, idx] = 0.5
        else:
            x[row, :7] = -0.5
    facing_index = {f: idx for idx, f in enumerate(facings)}
    for row, facing in enumerate(work["facing"].astype(str)):
        x[row, 7 + facing_index[facing]] = 0.5
    y = work["bias_mm"].to_numpy(dtype=float) - 0.5 * mean_sweep
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    theta = np.r_[beta[:7], -float(np.sum(beta[:7]))]
    anchor_deltas = mean_sweep + theta
    delta_tag_by_facing = {f: float(beta[7 + idx]) for f, idx in facing_index.items()}
    anchors = work["anchor"].astype(str).map(lambda a: ANCHOR_LABELS.index(a)).to_numpy(dtype=int)
    pred = 0.5 * anchor_deltas[anchors] + 0.5 * work["facing"].astype(str).map(delta_tag_by_facing).to_numpy(dtype=float)
    residual = work["bias_mm"].to_numpy(dtype=float) - pred
    return FacingTagFit(
        name=name,
        delta_tag_by_facing=delta_tag_by_facing,
        anchor_deltas_mm=anchor_deltas,
        rms_mm=float(np.sqrt(np.nanmean(residual * residual))),
        n_links=int(len(work)),
        rank=int(np.linalg.matrix_rank(x)),
    )


def compute_common_mode_rows(link_df: pd.DataFrame, sweep_delta: dict[str, float], headline_positions: pd.DataFrame) -> tuple[list[dict], list[dict], list[dict]]:
    err_map = headline_positions.set_index("position")["err_3d_mm"].astype(float).to_dict()
    link_rows: list[dict] = []
    position_rows: list[dict] = []
    fit_rows: list[dict] = []
    for position in sorted(link_df["position"].astype(str).unique()):
        train = link_df[link_df["position"].astype(str) != position].reset_index(drop=True)
        test = link_df[link_df["position"].astype(str) == position].reset_index(drop=True)
        fit = fit_tag_model_generic(f"common_mode_loo_without_{position}", train, sweep_delta, [])
        additive = 0.5 * fit.anchor_deltas_mm + 0.5 * float(fit.delta_tag_mm)
        residuals = []
        for row in test.itertuples(index=False):
            aid = ANCHOR_LABELS.index(str(row.anchor))
            corrected = float(row.median_range_mm) - float(additive[aid])
            residual = corrected - float(row.vicon_distance_mm)
            residuals.append(residual)
            link_rows.append(
                {
                    "position": position,
                    "anchor": str(row.anchor),
                    "facing": str(row.facing),
                    "height": str(row.height),
                    "location": str(row.location),
                    "corrected_median_range_mm": corrected,
                    "vicon_distance_mm": float(row.vicon_distance_mm),
                    "residual_after_additive_mm": residual,
                    "link_noise_std_mm": float(row.range_std_mm),
                    "headline_err_3d_mm": float(err_map[position]),
                }
            )
        vals = np.asarray(residuals, dtype=float)
        c_median = float(np.nanmedian(vals))
        c_mean = float(np.nanmean(vals))
        after = vals - c_median
        position_rows.append(
            {
                "position": position,
                "facing": str(test["facing"].iloc[0]),
                "height": str(test["height"].iloc[0]),
                "location": str(test["location"].iloc[0]),
                "headline_err_3d_mm": float(err_map[position]),
                "c_p_median_mm": c_median,
                "c_p_mean_mm": c_mean,
                "abs_c_p_median_mm": abs(c_median),
                "per_link_residual_rms_mm": float(np.sqrt(np.nanmean(vals * vals))),
                "after_common_mode_rms_mm": float(np.sqrt(np.nanmean(after * after))),
                "after_common_mode_max_abs_mm": float(np.nanmax(np.abs(after))),
            }
        )
        fit_rows.append(
            {
                "position": position,
                "fit": "global_additive_only_loo",
                "delta_tag_mm": float(fit.delta_tag_mm),
                "train_rms_mm": float(fit.rms_mm),
                "train_links": int(fit.n_links),
            }
        )
    pos_df = pd.DataFrame(position_rows).sort_values("abs_c_p_median_mm", ascending=False)
    rank = {str(row.position): idx + 1 for idx, row in enumerate(pos_df.itertuples(index=False))}
    for row in position_rows:
        row["abs_c_p_rank"] = int(rank[str(row["position"])])
    return link_rows, position_rows, fit_rows


def common_mode_summary(link_rows: list[dict], position_rows: list[dict]) -> list[dict]:
    link_df = pd.DataFrame(link_rows)
    pos_df = pd.DataFrame(position_rows)
    residual = link_df["residual_after_additive_mm"].to_numpy(dtype=float)
    cp = link_df["position"].astype(str).map(pos_df.set_index("position")["c_p_median_mm"]).to_numpy(dtype=float)
    total_sse_zero = float(np.nansum(residual * residual))
    after_sse = float(np.nansum((residual - cp) ** 2))
    centered = residual - float(np.nanmean(residual))
    total_sse_centered = float(np.nansum(centered * centered))
    return [
        {
            "positions": int(len(pos_df)),
            "links": int(len(link_df)),
            "c_p_median_mm": float(np.nanmedian(pos_df["c_p_median_mm"].to_numpy(dtype=float))),
            "c_p_p05_mm": float(np.nanpercentile(pos_df["c_p_median_mm"].to_numpy(dtype=float), 5)),
            "c_p_p95_mm": float(np.nanpercentile(pos_df["c_p_median_mm"].to_numpy(dtype=float), 95)),
            "abs_c_p_median_mm": float(np.nanmedian(pos_df["abs_c_p_median_mm"].to_numpy(dtype=float))),
            "abs_c_p_max_mm": float(np.nanmax(pos_df["abs_c_p_median_mm"].to_numpy(dtype=float))),
            "common_mode_energy_fraction": float(1.0 - after_sse / total_sse_zero) if total_sse_zero > 0 else float("nan"),
            "common_mode_centered_r2": float(1.0 - after_sse / total_sse_centered) if total_sse_centered > 0 else float("nan"),
            "headline_err_vs_abs_c_p_corr": float(np.corrcoef(pos_df["headline_err_3d_mm"].to_numpy(dtype=float), pos_df["abs_c_p_median_mm"].to_numpy(dtype=float))[0, 1]),
        }
    ]


def categorical_r2_rows(position_rows: list[dict]) -> list[dict]:
    df = pd.DataFrame(position_rows)
    y = df["c_p_median_mm"].to_numpy(dtype=float)
    rows = []
    for cols in [("facing",), ("height",), ("location",), ("facing", "height"), ("facing", "location"), ("height", "location"), ("facing", "height", "location")]:
        x_df = pd.get_dummies(df[list(cols)].astype(str), drop_first=False, dtype=float)
        x = np.column_stack([np.ones(len(df)), x_df.to_numpy(dtype=float)])
        beta = np.linalg.lstsq(x, y, rcond=None)[0]
        pred = x @ beta
        sse = float(np.nansum((y - pred) ** 2))
        tss = float(np.nansum((y - np.nanmean(y)) ** 2))
        rows.append(
            {
                "model": "+".join(cols),
                "n": int(len(df)),
                "rank": int(np.linalg.matrix_rank(x)),
                "r2": float(1.0 - sse / tss) if tss > 0 else float("nan"),
                "rmse_mm": float(np.sqrt(np.nanmean((y - pred) ** 2))),
            }
        )
    return rows


def group_summary_rows(position_rows: list[dict], group_col: str) -> list[dict]:
    df = pd.DataFrame(position_rows)
    rows = []
    for value, g in df.groupby(group_col, sort=True):
        vals = g["c_p_median_mm"].to_numpy(dtype=float)
        rows.append(
            {
                "grouping": group_col,
                "group": str(value),
                "positions": int(len(g)),
                "c_p_median_mm": float(np.nanmedian(vals)),
                "c_p_mean_mm": float(np.nanmean(vals)),
                "abs_c_p_median_mm": float(np.nanmedian(np.abs(vals))),
                "headline_err_median_mm": float(np.nanmedian(g["headline_err_3d_mm"].to_numpy(dtype=float))),
            }
        )
    return rows


def solve_points_for_position_process(payload: tuple[str, str, str]) -> tuple[list[dict], list[dict], list[dict]]:
    data_dir = Path(payload[0])
    out_dir = Path(payload[1])
    position = payload[2]
    tables_dir = out_dir / "tables"
    phase1 = load_phase1_data(data_dir, out_dir)
    primary_truth = load_primary_vicon_anchor_truth(data_dir, phase1.anchor_truth)
    tag_truth = tag_coord_map(phase1.tag_truth)
    sweep_delta = load_sweep_deltas(out_dir)
    link_df = pd.read_csv(tables_dir / "03_tag_link_bias_links.csv")
    link_df["position"] = link_df["position"].astype(str)
    link_df["anchor"] = link_df["anchor"].astype(str)
    c_df = pd.read_csv(tables_dir / "14_common_mode_position_offsets.csv")
    c_p = float(c_df[c_df["position"].astype(str) == position]["c_p_median_mm"].iloc[0])

    read_tr_all_frames, Solver, load_layout_json, SolverConfig, Frame, Observation = load_offline_solver(data_dir)
    capture_dirs = find_static_capture_dirs(data_dir)
    frames = read_tr_all_frames(capture_dirs[position], tags={"BSF66F"}, min_anchors=4)
    layout_path = make_vb_zero_delay_layout(out_dir)
    sigma_path = data_dir / "solver" / "outputs" / "v1_to_v4_io_field_check" / "tables" / "anchor_sigma.json"
    layout = load_layout_json(layout_path, sigma_path)
    coords = load_layout_coords(layout_path)
    truth_xyz = np.asarray([primary_truth[a] for a in ANCHOR_LABELS], dtype=float)
    rigid = fit_similarity(coords, truth_xyz, allow_reflection=True, allow_scale=False)

    train = link_df[link_df["position"].astype(str) != position].reset_index(drop=True)
    test = link_df[link_df["position"].astype(str) == position].reset_index(drop=True)
    facing = str(test["facing"].iloc[0])
    fit_global = fit_tag_model_generic(f"global_additive_loo_without_{position}", train, sweep_delta, [])
    fit_facing = fit_tag_model_per_facing(f"per_facing_delta_tag_loo_without_{position}", train, sweep_delta)
    global_add = 0.5 * fit_global.anchor_deltas_mm + 0.5 * float(fit_global.delta_tag_mm)
    if facing not in fit_facing.delta_tag_by_facing:
        delta_for_facing = float(np.mean(list(fit_facing.delta_tag_by_facing.values())))
    else:
        delta_for_facing = float(fit_facing.delta_tag_by_facing[facing])
    facing_add = 0.5 * fit_facing.anchor_deltas_mm + 0.5 * delta_for_facing
    oracle_add = global_add + c_p

    modes = {
        "tagfit_additive_only_coherent_replay": global_add,
        "tagfit_per_facing_delta_tag": facing_add,
        "position_common_offset_oracle": oracle_add,
    }
    points: dict[str, np.ndarray] = {}
    detail_rows: list[dict] = []
    for mode, additive in modes.items():
        corrected = corrected_frames_additive(frames, additive, Frame, Observation)
        solver = Solver(layout, SolverConfig(method="T4"))
        results = []
        for frame in corrected:
            result = solver.solve_frame(frame)
            if result is not None:
                results.append(result)
        if results:
            pts = np.asarray([[r.x_mm, r.y_mm, r.z_mm] for r in results], dtype=float)
            point = np.nanmean(pts, axis=0)
            residuals = np.asarray([r.residual_rms_mm for r in results], dtype=float)
            d3 = np.linalg.norm(pts - point[None, :], axis=1)
            residual_median = float(np.nanmedian(residuals))
            d3_std = float(np.sqrt(np.nanmean(d3 * d3)))
        else:
            point = np.full(3, np.nan)
            residual_median = float("nan")
            d3_std = float("nan")
        points[mode] = point
        detail_rows.append(
            {
                "mode": mode,
                "position": position,
                "frames_input": int(len(frames)),
                "frames_solved": int(len(results)),
                "residual_rms_median_mm": residual_median,
                "d3_std_mm": d3_std,
            }
        )

    position_rows: list[dict] = []
    for mode, point in points.items():
        position_rows.extend(
            tag_error_rows(
                method=mode,
                registration="anchor_only_3d_rigid",
                points_by_position={position: point},
                tag_truth=tag_truth,
                transform=rigid.apply,
            )
        )
    fit_rows = [
        {
            "position": position,
            "fit": "global_additive_only",
            "heldout_facing": facing,
            "delta_tag_for_position_mm": float(fit_global.delta_tag_mm),
            "train_rms_mm": float(fit_global.rms_mm),
            "train_links": int(fit_global.n_links),
            "rank": 8,
        },
        {
            "position": position,
            "fit": "per_facing_delta_tag",
            "heldout_facing": facing,
            "delta_tag_for_position_mm": delta_for_facing,
            "train_rms_mm": float(fit_facing.rms_mm),
            "train_links": int(fit_facing.n_links),
            "rank": int(fit_facing.rank),
            **{f"delta_tag_{f}_mm": float(fit_facing.delta_tag_by_facing.get(f, np.nan)) for f in FACING_ORDER},
        },
        {
            "position": position,
            "fit": "position_common_offset_oracle",
            "heldout_facing": facing,
            "delta_tag_for_position_mm": float(fit_global.delta_tag_mm + 2.0 * c_p),
            "common_offset_c_p_mm": c_p,
            "train_rms_mm": float("nan"),
            "train_links": int(fit_global.n_links),
            "rank": 24,
        },
    ]
    return position_rows, detail_rows, fit_rows


def fit_summary_rows(fit_rows: list[dict]) -> list[dict]:
    df = pd.DataFrame(fit_rows)
    rows = []
    for fit, g in df.groupby("fit", sort=False):
        rows.append(
            {
                "fit": str(fit),
                "positions": int(len(g)),
                "delta_tag_for_position_median_mm": float(np.nanmedian(g["delta_tag_for_position_mm"].to_numpy(dtype=float))),
                "delta_tag_for_position_min_mm": float(np.nanmin(g["delta_tag_for_position_mm"].to_numpy(dtype=float))),
                "delta_tag_for_position_max_mm": float(np.nanmax(g["delta_tag_for_position_mm"].to_numpy(dtype=float))),
                "train_rms_median_mm": float(np.nanmedian(g["train_rms_mm"].to_numpy(dtype=float))) if np.isfinite(g["train_rms_mm"].to_numpy(dtype=float)).any() else float("nan"),
                "rank_median": float(np.nanmedian(g["rank"].to_numpy(dtype=float))),
            }
        )
    return rows


def build_report(
    out_dir: Path,
    common_summary: list[dict],
    top_cp_rows: list[dict],
    worst6_rows: list[dict],
    group_rows: list[dict],
    r2_rows: list[dict],
    replay_summary: list[dict],
    fit_summary: list[dict],
) -> None:
    lines = ["# Phase 2.15 Common-Mode Bias Structure Test\n"]
    lines.append(f"- Generated: `{pd.Timestamp.now().isoformat(timespec='seconds')}`")
    lines.append("- Ground-truth terminology: `Vicon`")
    lines.append("- Scope: common-mode residual structure and exploratory per-facing tag-delay refit; no production files were modified.")
    lines.append("")
    lines.append("## 2.15a Common-Mode Residuals")
    lines.append(
        "The scalar `c_p` is the per-position median of corrected median range minus Vicon link distance under the coherent additive-only LOO correction. "
        "This is an analysis diagnostic, not a runtime correction."
    )
    lines.append("")
    lines.append(markdown_table(common_summary, ["positions", "links", "c_p_median_mm", "c_p_p05_mm", "c_p_p95_mm", "abs_c_p_median_mm", "abs_c_p_max_mm", "common_mode_energy_fraction", "common_mode_centered_r2", "headline_err_vs_abs_c_p_corr"]))
    lines.append("")
    lines.append("Largest absolute `c_p` positions:")
    lines.append(markdown_table(top_cp_rows, ["position", "facing", "height", "location", "headline_err_3d_mm", "c_p_median_mm", "abs_c_p_rank", "after_common_mode_rms_mm"]))
    lines.append("")
    lines.append("Worst-6 headline positions cross-check:")
    lines.append(markdown_table(worst6_rows, ["position", "headline_err_3d_mm", "c_p_median_mm", "abs_c_p_rank", "after_common_mode_rms_mm"]))
    lines.append("")
    lines.append("## 2.15b Stratification")
    lines.append(markdown_table(r2_rows, ["model", "n", "rank", "r2", "rmse_mm"]))
    lines.append("")
    lines.append(markdown_table(group_rows, ["grouping", "group", "positions", "c_p_median_mm", "c_p_mean_mm", "abs_c_p_median_mm", "headline_err_median_mm"]))
    lines.append("")
    lines.append("## 2.15c Per-Facing Delta_tag Refit")
    lines.append(
        "The per-facing row is exploratory because each LOO fold leaves only five training positions in the held-out facing group. "
        "The position-common-offset oracle uses Vicon-derived `c_p` and is non-deployable; it is included only to bound the common-mode hypothesis."
    )
    lines.append("")
    lines.append(markdown_table(replay_summary, ["mode", "positions", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm", "median_horizontal_mm", "median_vertical_mm"]))
    lines.append("")
    lines.append("Fit summary:")
    lines.append(markdown_table(fit_summary, ["fit", "positions", "delta_tag_for_position_median_mm", "delta_tag_for_position_min_mm", "delta_tag_for_position_max_mm", "train_rms_median_mm", "rank_median"]))
    lines.append("")
    lines.append(
        "STOP: Phase 2.15 complete. Common-mode and per-facing tag-delay diagnostics are offline analyses only."
    )
    (out_dir / "02_15_common_mode_bias.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    data_dir = args.data_dir.resolve()
    out_dir = args.out_dir.resolve()
    tables_dir = out_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    phase1 = load_phase1_data(data_dir, out_dir)
    sweep_delta = load_sweep_deltas(out_dir)
    link_df = pd.read_csv(tables_dir / "03_tag_link_bias_links.csv")
    link_df["position"] = link_df["position"].astype(str)
    link_df["anchor"] = link_df["anchor"].astype(str)
    headline_positions = pd.read_csv(tables_dir / "11_additive_coherence_positions.csv")
    headline_positions = headline_positions[headline_positions["method"].astype(str) == "tagfit_additive_only_coherent"].copy()

    link_rows, position_rows, additive_fit_rows = compute_common_mode_rows(link_df, sweep_delta, headline_positions)
    write_csv_rows(tables_dir / "14_common_mode_link_residuals.csv", link_rows)
    write_csv_rows(tables_dir / "14_common_mode_position_offsets.csv", position_rows)
    write_csv_rows(tables_dir / "14_common_mode_additive_fit_rows.csv", additive_fit_rows)

    common_summary = common_mode_summary(link_rows, position_rows)
    r2_rows = categorical_r2_rows(position_rows)
    group_rows = []
    for col in ("facing", "height", "location"):
        group_rows.extend(group_summary_rows(position_rows, col))
    write_csv_rows(tables_dir / "14_common_mode_summary.csv", common_summary)
    write_csv_rows(tables_dir / "14_common_mode_categorical_r2.csv", r2_rows)
    write_csv_rows(tables_dir / "14_common_mode_group_summary.csv", group_rows)

    positions = sorted(pd.DataFrame(position_rows)["position"].astype(str).tolist())
    payloads = [(str(data_dir), str(out_dir), pos) for pos in positions]
    replay_position_rows: list[dict] = []
    replay_detail_rows: list[dict] = []
    replay_fit_rows: list[dict] = []
    max_workers = max(1, min(args.workers, len(positions)))
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(solve_points_for_position_process, payload): payload[2] for payload in payloads}
        for fut in as_completed(futs):
            rows, details, fits = fut.result()
            replay_position_rows.extend(rows)
            replay_detail_rows.extend(details)
            replay_fit_rows.extend(fits)
    replay_position_rows.sort(key=lambda r: (r["method"], r["position"]))
    replay_detail_rows.sort(key=lambda r: (r["mode"], r["position"]))
    replay_fit_rows.sort(key=lambda r: (r["fit"], r["position"]))
    replay_summary = summarize_errors(replay_position_rows, group_cols=["method"])
    for row in replay_summary:
        row["mode"] = row.pop("method")
    order = {
        "tagfit_additive_only_coherent_replay": 0,
        "tagfit_per_facing_delta_tag": 1,
        "position_common_offset_oracle": 2,
    }
    replay_summary = sorted(replay_summary, key=lambda r: order.get(str(r["mode"]), 99))
    replay_fit_summary = fit_summary_rows(replay_fit_rows)
    write_csv_rows(tables_dir / "14_common_mode_replay_positions.csv", replay_position_rows)
    write_csv_rows(tables_dir / "14_common_mode_replay_details.csv", replay_detail_rows)
    write_csv_rows(tables_dir / "14_common_mode_replay_fits.csv", replay_fit_rows)
    write_csv_rows(tables_dir / "14_common_mode_replay_summary.csv", replay_summary)
    write_csv_rows(tables_dir / "14_common_mode_replay_fit_summary.csv", replay_fit_summary)

    pos_df = pd.DataFrame(position_rows)
    top_cp_rows = pos_df.sort_values("abs_c_p_median_mm", ascending=False).head(10).to_dict("records")
    worst_positions = headline_positions.sort_values("err_3d_mm", ascending=False)["position"].astype(str).head(6).tolist()
    worst6_rows = pos_df[pos_df["position"].astype(str).isin(worst_positions)].sort_values("headline_err_3d_mm", ascending=False).to_dict("records")
    build_report(out_dir, common_summary, top_cp_rows, worst6_rows, group_rows, r2_rows, replay_summary, replay_fit_summary)
    print(f"Phase 2.15 report written: {out_dir / '02_15_common_mode_bias.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
