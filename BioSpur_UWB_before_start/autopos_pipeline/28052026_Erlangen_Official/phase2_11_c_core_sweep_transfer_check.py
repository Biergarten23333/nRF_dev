#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from phase2_6_diagnostics_closure import find_static_capture_dirs, load_offline_solver
from phase2_7_final_closure import fit_similarity, summarize_errors, summarize_solver_results, tag_error_rows, write_csv_rows
from phase2_8_runtime_correction import make_vb_zero_delay_layout
from phase2_9_estimated_distance_runtime import corrected_frames_additive, load_layout_coords
from phase2_solver_ablation import leave_one_position_delta_tag, load_primary_vicon_anchor_truth, load_sweep_deltas
from scripts.audit_helpers import ANCHOR_LABELS, markdown_table
from scripts.phase1_common import load_phase1_data, tag_coord_map


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C-core T4 check for V-B layout with sweep-derived tag corrections.")
    parser.add_argument("--data-dir", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def solve_correction_mode(
    *,
    mode: str,
    data_dir: Path,
    layout: Any,
    Solver: Any,
    SolverConfig: Any,
    Frame: Any,
    Observation: Any,
    frames_by_position: dict[str, list[Any]],
    sweep_delta: dict[str, float],
    loo_delta_tag: dict[str, float],
    workers: int,
) -> tuple[dict[str, np.ndarray], list[dict]]:
    sweep_vec = np.asarray([sweep_delta[a] for a in ANCHOR_LABELS], dtype=float)

    def run_one(position: str) -> tuple[str, np.ndarray, dict]:
        if mode == "vb_sweep_anchor_delta_only":
            additive = 0.5 * sweep_vec
        elif mode == "vb_sweep_plus_loo_tag_delta":
            additive = 0.5 * sweep_vec + 0.5 * float(loo_delta_tag[position])
        else:
            raise ValueError(mode)
        frames = corrected_frames_additive(frames_by_position[position], additive, Frame, Observation)
        solver = Solver(layout, SolverConfig(method="T4"))
        results = []
        for frame in frames:
            result = solver.solve_frame(frame)
            if result is not None:
                results.append(result)
        summary = summarize_solver_results(results)
        detail = {
            "mode": mode,
            "position": position,
            "frames_input": int(len(frames_by_position[position])),
            "frames_solved": int(summary["frames_solved"]),
            "d3_std_mm": float(summary["d3_std_mm"]),
            "residual_rms_median_mm": float(summary["residual_rms_median_mm"]),
            "loo_delta_tag_mm": float(loo_delta_tag.get(position, np.nan)),
        }
        return position, summary["point"], detail

    points: dict[str, np.ndarray] = {}
    details: list[dict] = []
    max_workers = max(1, min(workers, len(frames_by_position)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(run_one, pos): pos for pos in sorted(frames_by_position)}
        for fut in as_completed(futs):
            position, point, detail = fut.result()
            points[position] = point
            details.append(detail)
    details.sort(key=lambda r: (r["mode"], r["position"]))
    return points, details


def build_report(out_dir: Path, summary_rows: list[dict]) -> None:
    lines = ["# Phase 2.11 C-core Sweep-Transfer Check\n"]
    lines.append(f"- Generated: `{pd.Timestamp.now().isoformat(timespec='seconds')}`")
    lines.append("- Ground-truth terminology: `Vicon`")
    lines.append("- Scope: one C-core T4 consistency check for the individual-report ladder table; no production files were modified.")
    lines.append("")
    lines.append("## Result")
    lines.append(
        "Both rows use the V-B calibrated layout, C-core T4, mean session estimator, and anchor-only 3D rigid/reflection registration. "
        "`vb_sweep_plus_loo_tag_delta` is the C-core counterpart of the earlier Phase 2 simplified-WLS V-B transfer row: sweep-fitted anchor Delta_i terms plus a leave-one-position-out Delta_tag term, with no tag-side proportional rho."
    )
    lines.append("")
    lines.append(markdown_table(summary_rows, ["mode", "positions", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm", "median_horizontal_mm", "median_vertical_mm"]))
    lines.append("")
    lines.append("STOP: Phase 2.11 complete. Use this only to avoid mixing WLS and C-core rows in the standalone report draft.")
    (out_dir / "02_11_c_core_sweep_transfer_check.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    loo_delta_tag = leave_one_position_delta_tag(link_df, sweep_delta)

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

    all_position_rows: list[dict] = []
    all_detail_rows: list[dict] = []
    for mode in ["vb_sweep_anchor_delta_only", "vb_sweep_plus_loo_tag_delta"]:
        points, details = solve_correction_mode(
            mode=mode,
            data_dir=data_dir,
            layout=layout,
            Solver=Solver,
            SolverConfig=SolverConfig,
            Frame=Frame,
            Observation=Observation,
            frames_by_position=frames_by_position,
            sweep_delta=sweep_delta,
            loo_delta_tag=loo_delta_tag,
            workers=args.workers,
        )
        rows = tag_error_rows(
            method=mode,
            registration="anchor_only_3d_rigid",
            points_by_position=points,
            tag_truth=tag_truth,
            transform=rigid.apply,
        )
        all_position_rows.extend(rows)
        all_detail_rows.extend(details)

    summary_rows = summarize_errors(all_position_rows, group_cols=["method"])
    for row in summary_rows:
        row["mode"] = row.pop("method")
    write_csv_rows(tables_dir / "10_c_core_sweep_transfer_positions.csv", all_position_rows)
    write_csv_rows(tables_dir / "10_c_core_sweep_transfer_details.csv", all_detail_rows)
    write_csv_rows(tables_dir / "10_c_core_sweep_transfer_summary.csv", summary_rows)
    build_report(out_dir, summary_rows)
    print(f"Phase 2.11 report written: {out_dir / '02_11_c_core_sweep_transfer_check.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
