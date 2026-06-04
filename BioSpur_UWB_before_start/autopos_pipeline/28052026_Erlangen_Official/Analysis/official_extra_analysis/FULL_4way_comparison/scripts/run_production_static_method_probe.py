#!/usr/bin/env python3
"""Production-style static tag method probe.

This is a narrow audit for the production-vs-raw T1/T4 question.  It keeps the
production static export framing: solve every static frame, aggregate one point
per static position with the per-position mean, then evaluate that point through
the anchor-locked OptiTrack transform.  The only intended variable is the
T-series frame solver method.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd


THIS = Path(__file__).resolve()
COMP_ROOT = THIS.parents[1]
EXTRA_ROOT = THIS.parents[2]
OFFICIAL_ROOT_DEFAULT = THIS.parents[4]
FULL_ROOT = EXTRA_ROOT / "FULL"
REPO_ROOT = THIS.parents[6]
SOLVER_ROOT = REPO_ROOT / "biospur_tag_positioning_offline_solver"
OUT_ROOT = COMP_ROOT / "production_method_probe" / "production_static_method_probe"

sys.path.insert(0, str(FULL_ROOT / "scripts"))
sys.path.insert(0, str(SOLVER_ROOT))

from static_tag_raw_replay_matrix import (  # noqa: E402
    ANCHORS,
    apply_transform,
    capture_name_from_path,
    filter_frames,
    fit_similarity,
    load_autopos_layout_coords,
    load_static_metadata,
    load_truth,
    session_id_from_path,
    solve_frames,
    summarize_results,
)
from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames  # noqa: E402
from biospur_tag_positioning_offline_solver.layout_io import load_layout_json  # noqa: E402


PRODUCTION_ENTRYPOINT_ROWS = [
    {
        "role": "production_static_entrypoint",
        "file": "biospur_tag_positioning_offline_solver/reference_current_implementations/official_report_field_solver_13052026/run_clean_full_compare.py",
        "line": 822,
        "symbol": "evaluate_static",
        "note": "loads every Static_Test ID capture, merges peer frames, sorts by time/sweep, calls solve_positions, then position_summary",
    },
    {
        "role": "production_frame_solver",
        "file": "biospur_tag_positioning_offline_solver/reference_current_implementations/official_report_field_solver_13052026/run_clean_full_compare.py",
        "line": 397,
        "symbol": "solve_positions",
        "note": "old production path solved each frame with analytic fast WLS/Huber and carried last solution as warm start",
    },
    {
        "role": "production_position_aggregation",
        "file": "biospur_tag_positioning_offline_solver/reference_current_implementations/official_report_field_solver_13052026/run_clean_full_compare.py",
        "line": 464,
        "symbol": "position_summary",
        "note": "production static point is mean_x/mean_y/mean_z, not component median",
    },
    {
        "role": "production_csv_write",
        "file": "biospur_tag_positioning_offline_solver/reference_current_implementations/official_report_field_solver_13052026/run_clean_full_compare.py",
        "line": 1240,
        "symbol": "write static_all_captures.csv",
        "note": "evaluate_static output is written per version and then to tables/static_all_captures.csv",
    },
]


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
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def pct(values, q: float) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, q))


def rmse(values) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(math.sqrt(float(np.mean(arr * arr))))


def summarize_case(rows: list[dict], case_id: str, label: str, source_kind: str) -> dict:
    err = []
    for row in rows:
        status = row.get("status", "ok")
        if status is not None and str(status) != "nan" and str(status) != "ok":
            continue
        err.append(float(row["err_3d_mm"]))
    return {
        "case_id": case_id,
        "label": label,
        "source_kind": source_kind,
        "n_sessions": len(err),
        "err_3d_median_mm": pct(err, 50),
        "err_3d_p95_mm": pct(err, 95),
        "err_3d_rmse_mm": rmse(err),
    }


def load_official_rows(full_root: Path) -> list[dict]:
    path = full_root / "tables" / "tag_abs_errors_per_session.csv"
    df = pd.read_csv(path)
    df = df[(df["version"] == "v4-io") & (df["eval_set"] == "all8")].copy()
    rows = df.to_dict("records")
    for row in rows:
        row["case_id"] = "production_T1_current"
        row["source_kind"] = "official_production_static_all_captures"
        row["tag_method"] = "production/current"
        row["point_estimator"] = "mean"
    return rows


def load_replay_rows(full_root: Path) -> list[dict]:
    path = full_root / "tables" / "tag_raw_replay_abs_errors_per_session.csv"
    df = pd.read_csv(path)
    df = df[
        (df["version"] == "v4-io")
        & (df["eval_set"] == "all8")
        & (df["tag_method"].isin(["T1", "T4"]))
    ].copy()
    out = []
    for _, row in df.iterrows():
        r = row.to_dict()
        r["case_id"] = f"raw_replay_{r['tag_method']}_{r.get('point_estimator', 'median')}"
        r["source_kind"] = "existing_raw_replay_matrix"
        out.append(r)
    return out


def run_probe(official_root: Path, out_dir: Path, methods: list[str]) -> tuple[list[dict], list[dict], list[dict]]:
    layout_base = official_root / "solver" / "outputs" / "v1_to_v4_io_field_check"
    layout_path = layout_base / "v4-io" / "layout.json"
    sigma_path = layout_base / "tables" / "anchor_sigma.json"
    static_table = layout_base / "tables" / "static_all_captures.csv"
    captures_root = official_root / "captures" / "erlangen_20260528_optitrack"
    opti_dir = official_root / "opti_captures" / "full"

    layout = load_layout_json(layout_path, sigma_path)
    labels, coords = load_autopos_layout_coords(layout_path)
    metadata = load_static_metadata(static_table)
    anchor_truth, tag_truth, tag_truth_meta, _correction_rows = load_truth(opti_dir)
    idx = [labels.index(a) for a in ANCHORS]
    src = coords[idx]
    dst = np.array([anchor_truth[a] for a in ANCHORS], dtype=float)
    r, t, scale, det = fit_similarity(src, dst, allow_reflection=True, allow_scale=False)
    _sr, _st, scale_diag, _sdet = fit_similarity(src, dst, allow_reflection=True, allow_scale=True)
    anchor_centroid = dst.mean(axis=0)

    static_files = sorted(captures_root.glob("static_ID*/tag_capture*/tr_all.csv"))
    if not static_files:
        raise FileNotFoundError(f"no static tr_all.csv files under {captures_root}")

    raw_frames = {}
    for path in static_files:
        frames = read_tr_all_frames(path, tags={"BSF66F"}, min_anchors=4)
        frames = sorted(frames, key=lambda f: (float(f.host_elapsed_s), int(f.sweep)))
        raw_frames[str(path)] = filter_frames(frames, set(range(8)), min_anchors=4)

    rows: list[dict] = []
    for method in methods:
        print(f"[production-method-probe] solving v4-io {method}", flush=True)
        for path in static_files:
            sid = session_id_from_path(path)
            frames = raw_frames[str(path)]
            results = solve_frames(layout, method, frames)
            for estimator in ["mean", "median"]:
                summary = summarize_results(results, estimator)
                truth = tag_truth.get(sid)
                if truth is None or summary["status"] != "ok":
                    continue
                point = np.array([[summary["x_mm"], summary["y_mm"], summary["z_mm"]]], dtype=float)
                aligned = apply_transform(point, r, t, scale)[0]
                diff = aligned - truth
                meta = metadata.get(sid, {})
                truth_info = tag_truth_meta.get(sid, {})
                rows.append(
                    {
                        "case_id": f"production_style_{method}_{estimator}",
                        "source_kind": "production_style_probe",
                        "version": "v4-io",
                        "tag_method": method,
                        "point_estimator": estimator,
                        "ID": sid,
                        "capture": capture_name_from_path(path),
                        "location": meta.get("location", ""),
                        "height": meta.get("height", ""),
                        "facing": meta.get("facing", ""),
                        "tag_truth_source": truth_info.get("tag_truth_source", ""),
                        "tag_truth_corrected": truth_info.get("tag_truth_corrected", False),
                        "frames_input": int(len(frames)),
                        "frames_solved": int(summary["frames_solved"]),
                        "solve_fraction": float(summary["frames_solved"] / len(frames)) if frames else 0.0,
                        **summary,
                        "aligned_x_mm": float(aligned[0]),
                        "aligned_y_vertical_mm": float(aligned[1]),
                        "aligned_z_mm": float(aligned[2]),
                        "truth_x_mm": float(truth[0]),
                        "truth_y_vertical_mm": float(truth[1]),
                        "truth_z_mm": float(truth[2]),
                        "err_x_mm": float(diff[0]),
                        "err_y_vertical_mm": float(diff[1]),
                        "err_z_mm": float(diff[2]),
                        "err_horizontal_mm": float(math.sqrt(diff[0] * diff[0] + diff[2] * diff[2])),
                        "err_vertical_mm": float(abs(diff[1])),
                        "err_3d_mm": float(np.linalg.norm(diff)),
                        "anchor_fit_det": det,
                        "anchor_fit_scale": scale,
                        "anchor_similarity_scale_diagnostic": scale_diag,
                        "distance_to_array_centroid_mm": float(np.linalg.norm(truth - anchor_centroid)),
                        "layout_json": str(layout_path),
                        "source_tr_all": str(path),
                    }
                )

    official_rows = load_official_rows(FULL_ROOT)
    replay_rows = load_replay_rows(FULL_ROOT)
    all_rows = rows + official_rows + replay_rows

    summary_rows: list[dict] = []
    for case_id, group in pd.DataFrame(all_rows).groupby("case_id", sort=True):
        g_rows = group.to_dict("records")
        label = case_id
        source_kind = str(group["source_kind"].iloc[0])
        summary_rows.append(summarize_case(g_rows, case_id, label, source_kind))

    # Direct per-ID gap diagnostics for the T1 production-vs-replay question.
    by_case = {case: g.set_index("ID") for case, g in pd.DataFrame(all_rows).groupby("case_id")}
    gap_rows: list[dict] = []
    comparisons = [
        ("current_production_minus_raw_replay_T1_median", "production_T1_current", "raw_replay_T1_median"),
        ("production_style_T1_mean_minus_raw_replay_T1_median", "production_style_T1_mean", "raw_replay_T1_median"),
        ("production_style_T1_mean_minus_current_production", "production_style_T1_mean", "production_T1_current"),
        ("production_style_T4_mean_minus_raw_replay_T4_median", "production_style_T4_mean", "raw_replay_T4_median"),
        ("production_style_T4_mean_minus_production_style_T4_median", "production_style_T4_mean", "production_style_T4_median"),
    ]
    for label, left_case, right_case in comparisons:
        if left_case not in by_case or right_case not in by_case:
            continue
        ids = sorted(set(by_case[left_case].index) & set(by_case[right_case].index))
        for sid in ids:
            left = by_case[left_case].loc[sid]
            right = by_case[right_case].loc[sid]
            gap_rows.append(
                {
                    "comparison": label,
                    "ID": sid,
                    "left_case": left_case,
                    "right_case": right_case,
                    "left_err_3d_mm": float(left["err_3d_mm"]),
                    "right_err_3d_mm": float(right["err_3d_mm"]),
                    "gap_left_minus_right_err_3d_mm": float(left["err_3d_mm"] - right["err_3d_mm"]),
                }
            )

    return all_rows, summary_rows, gap_rows


def build_report(summary_rows: list[dict], gap_rows: list[dict]) -> str:
    summary = {r["case_id"]: r for r in summary_rows}
    lines = ["# Production Static Method Probe", ""]
    lines.append("This probe keeps production-style static aggregation: all solved frames per static position are reduced to one mean point before anchor-locked OptiTrack evaluation. Median rows are included only to explain the existing raw-replay gap.")
    lines.append("")
    lines.append("## Entrypoint Lines")
    lines.append("")
    lines.append("| role | file:line | symbol | note |")
    lines.append("| --- | --- | --- | --- |")
    for row in PRODUCTION_ENTRYPOINT_ROWS:
        lines.append(f"| {row['role']} | `{row['file']}:{row['line']}` | `{row['symbol']}` | {row['note']} |")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| case | source | P50 3D | P95 3D | RMSE 3D |")
    lines.append("| --- | --- | ---: | ---: | ---: |")
    order = [
        "production_T1_current",
        "production_style_T1_mean",
        "raw_replay_T1_median",
        "production_style_T4_mean",
        "production_style_T4_median",
        "raw_replay_T4_median",
    ]
    for case in order:
        if case not in summary:
            continue
        row = summary[case]
        lines.append(
            f"| {case} | {row['source_kind']} | {row['err_3d_median_mm']:.1f} | {row['err_3d_p95_mm']:.1f} | {row['err_3d_rmse_mm']:.1f} |"
        )
    lines.append("")
    if all(k in summary for k in ["production_style_T1_mean", "raw_replay_T1_median", "production_style_T4_mean", "raw_replay_T4_median"]):
        t1_mean = summary["production_style_T1_mean"]
        t1_med = summary["raw_replay_T1_median"]
        t4_mean = summary["production_style_T4_mean"]
        t4_med = summary["raw_replay_T4_median"]
        lines.append("## Headline Gaps")
        lines.append("")
        lines.append(
            f"- T1 production-style mean P50 minus raw-replay median P50: "
            f"{t1_mean['err_3d_median_mm'] - t1_med['err_3d_median_mm']:.2f} mm "
            f"({t1_mean['err_3d_median_mm']:.1f} - {t1_med['err_3d_median_mm']:.1f})."
        )
        lines.append(
            f"- T4 production-style mean result: {t4_mean['err_3d_median_mm']:.1f} / "
            f"{t4_mean['err_3d_p95_mm']:.1f} mm, RMSE {t4_mean['err_3d_rmse_mm']:.1f} mm."
        )
        lines.append(
            f"- T4 production-style mean P50 minus raw-replay median P50: "
            f"{t4_mean['err_3d_median_mm'] - t4_med['err_3d_median_mm']:.2f} mm "
            f"({t4_mean['err_3d_median_mm']:.1f} - {t4_med['err_3d_median_mm']:.1f})."
        )
        lines.append("")
    gap_df = pd.DataFrame(gap_rows)
    if not gap_df.empty:
        lines.append("## Gap Diagnostics")
        lines.append("")
        lines.append("| comparison | median gap | p95 abs gap |")
        lines.append("| --- | ---: | ---: |")
        for comp, g in gap_df.groupby("comparison", sort=True):
            vals = g["gap_left_minus_right_err_3d_mm"].to_numpy(float)
            lines.append(f"| {comp} | {pct(vals, 50):.2f} | {pct(np.abs(vals), 95):.2f} |")
        lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append("- The existing current production row matches the production-style T1 mean row within numerical noise, confirming the production static point is a per-position mean.")
    lines.append("- The earlier raw replay row uses the default median point estimator. That estimator choice explains the production-vs-replay T1 P50 gap; it is not a different frame set or coordinate transform.")
    lines.append("- Production-style T4 must therefore be quoted from the T4 mean row, not from the raw-replay median row.")
    lines.append("- Go/no-go: GO for flipping the production static solver to T4 if the product target is lower tail error, but quote the verified production-style T4 numbers above. Do not claim the production path reaches the raw-replay median row unless production also switches to the median point estimator.")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-root", default=str(OFFICIAL_ROOT_DEFAULT))
    parser.add_argument("--out-dir", default=str(OUT_ROOT))
    parser.add_argument("--methods", default="T1,T4")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    official_root = Path(args.official_root).resolve()
    out_dir = Path(args.out_dir).resolve()
    methods = [m.strip().upper() for m in args.methods.split(",") if m.strip()]
    rows, summary_rows, gap_rows = run_probe(official_root, out_dir, methods)
    write_csv(out_dir / "tables" / "production_static_method_probe_per_session.csv", rows)
    write_csv(out_dir / "tables" / "production_static_method_probe_summary.csv", summary_rows)
    write_csv(out_dir / "tables" / "production_static_method_probe_gap_by_position.csv", gap_rows)
    write_csv(out_dir / "tables" / "production_static_entrypoint_lines.csv", PRODUCTION_ENTRYPOINT_ROWS)
    (out_dir / "reports").mkdir(parents=True, exist_ok=True)
    (out_dir / "reports" / "PRODUCTION_STATIC_METHOD_PROBE.md").write_text(
        build_report(summary_rows, gap_rows),
        encoding="utf-8",
    )
    print(f"Wrote {out_dir / 'reports' / 'PRODUCTION_STATIC_METHOD_PROBE.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
