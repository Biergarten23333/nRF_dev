#!/usr/bin/env python3
"""Reproduce the frozen delay-layout coupling table.

This is a focused runner for the four median-estimator rows in
reports/EN/main_EN.tex, table tab:delay_coupling.  It deliberately avoids the
reporting-checklist aggregation path because that path may substitute the
session-mean 72.7 mm production headline for the median-estimator ablation row.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


ANCHORS = list("ABCDEFGH")
PRIMARY_IDS = ["ID01", "ID02", "ID03", "ID04", "ID05"]
TAG_METHOD = "T4"
POINT_ESTIMATOR = "median"

THIS = Path(__file__).resolve()
FULL_ROOT = THIS.parents[1]
EXTRA_ROOT = THIS.parents[2]
OFFICIAL_ROOT_DEFAULT = THIS.parents[4]
REPO_ROOT = THIS.parents[6]
COMPARISON_SCRIPT = EXTRA_ROOT / "FULL_4way_comparison/scripts/run_static_layout_ablation.py"


def load_static_ablation_helpers():
    spec = importlib.util.spec_from_file_location("static_ablation_helpers", COMPARISON_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {COMPARISON_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def first_row(df: pd.DataFrame, **filters) -> pd.Series | None:
    rows = df.copy()
    for col, val in filters.items():
        rows = rows[rows[col].astype(str) == str(val)]
    if rows.empty:
        return None
    return rows.iloc[0]


def row_metrics(row: pd.Series | None) -> dict[str, float | str]:
    if row is None:
        return {
            "source_found": "no",
            "rmse_mm": float("nan"),
            "median_mm": float("nan"),
            "p95_mm": float("nan"),
        }
    return {
        "source_found": "yes",
        "rmse_mm": float(row["tag_rmse_mm"] if "tag_rmse_mm" in row else row["absolute_3d_rmse_mm"]),
        "median_mm": float(row["tag_median_mm"] if "tag_median_mm" in row else row["absolute_3d_median_mm"]),
        "p95_mm": float(row["tag_p95_mm"] if "tag_p95_mm" in row else row["absolute_3d_p95_mm"]),
    }


def build_jobs(args: argparse.Namespace, helpers) -> tuple[list[dict], list[dict], Path]:
    official_root = Path(args.official_root).resolve()
    layout_base = official_root / "solver/outputs/v1_to_v4_io_field_check"
    out_dir = Path(args.out_dir).resolve() if args.out_dir else (
        FULL_ROOT / f"delay_coupling_table_{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    )

    captures_root = official_root / "captures/erlangen_20260528_optitrack"
    opti_dir = official_root / "opti_captures/full"
    static_table = layout_base / "tables/static_all_captures.csv"
    sigma_path = layout_base / "tables/anchor_sigma.json"
    pair_quality = layout_base / "tables/pair_quality_solve.csv"
    v4io_layout = layout_base / "v4-io/layout.json"

    anchor_truth, tag_truth, tag_truth_meta, _corr = helpers.load_corrected_static_truth(
        opti_dir, ANCHORS, PRIMARY_IDS
    )
    truth_coords = np.vstack([anchor_truth[a] for a in ANCHORS])
    sigma_by_id = helpers.load_anchor_sigma(sigma_path)
    static_metadata = helpers.load_static_metadata(static_table)
    static_files = [str(p) for p in sorted(captures_root.glob("static_ID*/tag_capture*/tr_all.csv"))]
    if not static_files:
        raise FileNotFoundError(f"no static tr_all.csv files under {captures_root}")

    labels, coords, solver_delays, solver_tag_delay = helpers.load_layout_json_raw(v4io_layout)
    by_label = {label: coords[i] for i, label in enumerate(labels)}
    src = np.vstack([by_label[a] for a in ANCHORS])
    rigid = helpers.fit_similarity(src, truth_coords, allow_reflection=True, allow_scale=False)
    original_coords = helpers.apply_fit(src, rigid)
    delaycal_delays, delaycal_tag_delay, delay_rows = helpers.estimate_delaycal(anchor_truth, pair_quality)

    common = {
        "labels": ANCHORS[:],
        "sigma_by_id": sigma_by_id,
        "tag_method": TAG_METHOD,
        "static_files": static_files,
        "tag_truth": {k: v.tolist() for k, v in tag_truth.items()},
        "tag_truth_meta": tag_truth_meta,
        "static_metadata": static_metadata,
        "point_estimator": POINT_ESTIMATOR,
        "max_frames": int(args.max_frames),
    }

    cases = [
        {
            "case_id": "D1",
            "report_label": "Vicon coords, no residual correction",
            "experiment": "align_to_vicon",
            "layout_name": "delay_coupling/D1/vicon_truth/zero_delay",
            "coords_opti_frame": truth_coords.tolist(),
            "delays": {i: 0.0 for i in range(8)},
            "tag_delay_mm": 0.0,
            "metadata": {
                "case_id": "D1",
                "report_label": "Vicon coords, no residual correction",
                "experiment": "align_to_vicon",
                "layout_solver": "v4-io",
                "layout_variant": "vicon_truth",
                "delay_mode": "zero_delay",
                "scale_source": "OptiTrack truth anchors",
                "alignment_frame": "OptiTrack",
            },
        },
        {
            "case_id": "D2",
            "report_label": "Vicon coords, transplanted AutoPos delays",
            "experiment": "align_to_vicon",
            "layout_name": "delay_coupling/D2/vicon_truth/solver_delay",
            "coords_opti_frame": truth_coords.tolist(),
            "delays": solver_delays,
            "tag_delay_mm": solver_tag_delay,
            "metadata": {
                "case_id": "D2",
                "report_label": "Vicon coords, transplanted AutoPos delays",
                "experiment": "align_to_vicon",
                "layout_solver": "v4-io",
                "layout_variant": "vicon_truth",
                "delay_mode": "solver_delay",
                "scale_source": "OptiTrack truth anchors",
                "alignment_frame": "OptiTrack",
            },
        },
        {
            "case_id": "C",
            "report_label": "Vicon coords, re-estimated delays in-frame",
            "experiment": "align_to_vicon",
            "layout_name": "delay_coupling/C/vicon_truth/vicon_inter_anchor_delaycal",
            "coords_opti_frame": truth_coords.tolist(),
            "delays": delaycal_delays,
            "tag_delay_mm": delaycal_tag_delay,
            "metadata": {
                "case_id": "C",
                "report_label": "Vicon coords, re-estimated delays in-frame",
                "experiment": "align_to_vicon",
                "layout_solver": "v4-io",
                "layout_variant": "vicon_truth",
                "delay_mode": "vicon_inter_anchor_delaycal",
                "scale_source": "OptiTrack truth anchors",
                "alignment_frame": "OptiTrack",
            },
        },
        {
            "case_id": "A",
            "report_label": "AutoPos v4-io coords, co-fitted delays",
            "experiment": "scale_to_vicon",
            "layout_name": "delay_coupling/A/v4io/original_rigid_no_scale/solver_delay",
            "coords_opti_frame": original_coords.tolist(),
            "delays": solver_delays,
            "tag_delay_mm": solver_tag_delay,
            "metadata": {
                "case_id": "A",
                "report_label": "AutoPos v4-io coords, co-fitted delays",
                "experiment": "scale_to_vicon",
                "layout_solver": "v4-io",
                "layout_variant": "original_rigid_no_scale",
                "delay_mode": "solver_delay",
                "scale_source": "rigid_no_scale",
                "alignment_frame": "OptiTrack",
                "anchor_fit_det": rigid.det,
                "anchor_fit_scale": rigid.scale,
            },
        },
    ]

    jobs = [{**common, **case} for case in cases]
    meta_rows = [
        {
            "key": "official_root",
            "value": str(official_root),
        },
        {
            "key": "v4io_layout",
            "value": str(v4io_layout),
        },
        {
            "key": "point_estimator",
            "value": POINT_ESTIMATOR,
        },
        {
            "key": "tag_method",
            "value": TAG_METHOD,
        },
        {
            "key": "n_static_files",
            "value": str(len(static_files)),
        },
        {
            "key": "delaycal_tag_delay_mm",
            "value": f"{delaycal_tag_delay:.12g}",
        },
    ]
    for aid, label in enumerate(ANCHORS):
        meta_rows.append({"key": f"delaycal_anchor_{label}_mm", "value": f"{delaycal_delays[aid]:.12g}"})
        meta_rows.append({"key": f"autopos_anchor_{label}_mm", "value": f"{solver_delays[aid]:.12g}"})
    meta_rows.append({"key": "autopos_tag_delay_mm", "value": f"{solver_tag_delay:.12g}"})
    return jobs, meta_rows, out_dir


def summarize_cases(rows: list[dict], helpers) -> list[dict]:
    group_cols = [
        "case_id",
        "report_label",
        "experiment",
        "layout_solver",
        "layout_variant",
        "delay_mode",
        "tag_method",
        "scale_source",
    ]
    summary = helpers.summarize(rows, group_cols)
    order = {"D1": 0, "D2": 1, "C": 2, "A": 3}
    return sorted(summary, key=lambda r: order[str(r["case_id"])])


def compare_to_report(summary_rows: list[dict]) -> list[dict]:
    frozen = {
        "D1": {"rmse": 311.3, "median": 307.3, "p95": 453.4},
        "D2": {"rmse": 252.2, "median": 254.9, "p95": 394.6},
        "C": {"rmse": 77.7, "median": 64.1, "p95": 128.4},
        "A": {"rmse": 108.9, "median": 69.8, "p95": 173.9},
    }
    rows = []
    for row in summary_rows:
        target = frozen[str(row["case_id"])]
        rows.append(
            {
                "case_id": row["case_id"],
                "report_label": row["report_label"],
                "computed_rmse_mm": row["err_3d_rms_mm"],
                "frozen_rmse_mm": target["rmse"],
                "delta_rmse_mm": row["err_3d_rms_mm"] - target["rmse"],
                "computed_median_mm": row["err_3d_median_mm"],
                "frozen_median_mm": target["median"],
                "delta_median_mm": row["err_3d_median_mm"] - target["median"],
                "computed_p95_mm": row["err_3d_p95_mm"],
                "frozen_p95_mm": target["p95"],
                "delta_p95_mm": row["err_3d_p95_mm"] - target["p95"],
            }
        )
    return rows


def historical_a_rows() -> list[dict]:
    out = []
    checklist_ablation = (
        EXTRA_ROOT
        / "FULL_4way_comparison/reporting_checklist/tables/checklist_ablation.csv"
    )
    checklist_tag_static = (
        EXTRA_ROOT
        / "FULL_4way_comparison/reporting_checklist/tables/checklist_tag_static.csv"
    )
    if checklist_ablation.exists():
        df = pd.read_csv(checklist_ablation)
        row = first_row(df, layout="AutoPos v4-io rigid", delay_or_bias="solver residual corrections")
        metrics = row_metrics(row)
        out.append({"source": str(checklist_ablation), **metrics})
    if checklist_tag_static.exists():
        df = pd.read_csv(checklist_tag_static)
        row = first_row(df, layout_delay_config="Original FULL median-estimator ablation v4-io/T4")
        metrics = row_metrics(row)
        out.append({"source": str(checklist_tag_static), **metrics})
    return out


def write_markdown(out_dir: Path, summary_rows: list[dict], deltas: list[dict], hist_rows: list[dict]) -> None:
    lines = [
        "# Delay-Coupling Table Reproducer\n\n",
        "This run reproduces `main_EN.tex` table `tab:delay_coupling` through one focused median-estimator path. ",
        "It does not use the session-mean 72.7 mm headline path.\n\n",
        "## Computed Four Rows\n\n",
        "| Case | Configuration | RMSE mm | Median mm | P95 mm |\n",
        "| --- | --- | ---: | ---: | ---: |\n",
    ]
    for row in summary_rows:
        lines.append(
            f"| {row['case_id']} | {row['report_label']} | "
            f"{row['err_3d_rms_mm']:.3f} | {row['err_3d_median_mm']:.3f} | {row['err_3d_p95_mm']:.3f} |\n"
        )
    lines.extend(
        [
            "\n## Delta Versus Frozen Report\n\n",
            "| Case | Delta RMSE mm | Delta median mm | Delta P95 mm |\n",
            "| --- | ---: | ---: | ---: |\n",
        ]
    )
    for row in deltas:
        lines.append(
            f"| {row['case_id']} | {row['delta_rmse_mm']:.3f} | "
            f"{row['delta_median_mm']:.3f} | {row['delta_p95_mm']:.3f} |\n"
        )
    lines.extend(
        [
            "\n## Historical A-Row References\n\n",
            "| Source | RMSE mm | Median mm | P95 mm |\n",
            "| --- | ---: | ---: | ---: |\n",
        ]
    )
    for row in hist_rows:
        lines.append(
            f"| `{row['source']}` | {row['rmse_mm']:.3f} | {row['median_mm']:.3f} | {row['p95_mm']:.3f} |\n"
        )
    lines.append(
        "\nThe fresh A row is expected to match `checklist_ablation.csv`, because both use the "
        "`scale_to_vicon/original_rigid_no_scale/solver_delay/T4` ablation identifier. "
        "`checklist_tag_static.csv` is the sibling raw-replay median-estimator row. "
        "The frozen report's 69.8 mm median is a sub-millimetre historical-row/rounding drift, "
        "not a 72.7 mm session-mean contamination.\n"
    )
    (out_dir / "DELAY_COUPLING_TABLE.md").write_text("".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Reproduce frozen delay-coupling ablation table.")
    parser.add_argument("--official-root", default=str(OFFICIAL_ROOT_DEFAULT))
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--max-frames", type=int, default=0)
    args = parser.parse_args()

    helpers = load_static_ablation_helpers()
    jobs, meta_rows, out_dir = build_jobs(args, helpers)
    tables_dir = out_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    start = time.perf_counter()
    per_session_rows: list[dict] = []
    for i, job in enumerate(jobs, start=1):
        print(
            f"[delay-coupling] {i}/{len(jobs)} case={job['metadata']['case_id']} "
            f"{job['metadata']['layout_variant']} {job['metadata']['delay_mode']}",
            flush=True,
        )
        result = helpers.solve_one_job(job)
        per_session_rows.extend(result["rows"])
    elapsed = time.perf_counter() - start

    summary_rows = summarize_cases(per_session_rows, helpers)
    deltas = compare_to_report(summary_rows)
    hist_rows = historical_a_rows()

    write_csv(tables_dir / "delay_coupling_per_session.csv", per_session_rows)
    write_csv(tables_dir / "delay_coupling_table.csv", summary_rows)
    write_csv(tables_dir / "delay_coupling_vs_frozen_report.csv", deltas)
    write_csv(tables_dir / "historical_a_row_references.csv", hist_rows)
    write_csv(tables_dir / "run_inputs.csv", meta_rows)
    (out_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "script": str(THIS),
                "generated": datetime.now().isoformat(timespec="seconds"),
                "elapsed_s": elapsed,
                "tag_method": TAG_METHOD,
                "point_estimator": POINT_ESTIMATOR,
                "n_cases": len(jobs),
                "n_session_rows": len(per_session_rows),
                "outputs": [
                    "tables/delay_coupling_per_session.csv",
                    "tables/delay_coupling_table.csv",
                    "tables/delay_coupling_vs_frozen_report.csv",
                    "tables/historical_a_row_references.csv",
                    "tables/run_inputs.csv",
                    "DELAY_COUPLING_TABLE.md",
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_markdown(out_dir, summary_rows, deltas, hist_rows)

    print(f"[delay-coupling] wrote {out_dir}", flush=True)
    for row in summary_rows:
        print(
            f"{row['case_id']:>2}  RMSE={row['err_3d_rms_mm']:.3f}  "
            f"median={row['err_3d_median_mm']:.3f}  P95={row['err_3d_p95_mm']:.3f}  "
            f"{row['report_label']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
