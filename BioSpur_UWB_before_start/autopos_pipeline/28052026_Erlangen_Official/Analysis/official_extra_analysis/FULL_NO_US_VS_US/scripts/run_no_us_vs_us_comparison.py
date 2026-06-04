#!/usr/bin/env python3
"""Compare the original FULL 4-way outputs against the US30 height-gauged rerun."""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


ANCHORS = list("ABCDEFGH")


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


def fmt(x: float, ndigits: int = 1) -> str:
    if x is None or not math.isfinite(float(x)):
        return "nan"
    return f"{float(x):.{ndigits}f}"


def first(df: pd.DataFrame, **filters) -> pd.Series | None:
    out = df.copy()
    for col, val in filters.items():
        if col not in out.columns:
            return None
        out = out[out[col].astype(str) == str(val)]
    if out.empty:
        return None
    return out.iloc[0]


def summarize_errors(df: pd.DataFrame, err_col: str = "err_3d_mm") -> dict[str, float]:
    return {
        "p50_mm": pct(df[err_col], 50),
        "p95_mm": pct(df[err_col], 95),
        "rmse_mm": rmse(df[err_col]),
    }


def add_pair_row(
    rows: list[dict],
    *,
    metric_id: str,
    family: str,
    metric_label: str,
    no_us: dict[str, float],
    us: dict[str, float],
    note: str,
) -> None:
    row = {
        "metric_id": metric_id,
        "family": family,
        "metric_label": metric_label,
        "no_us_p50_mm": no_us.get("p50_mm", float("nan")),
        "no_us_p95_mm": no_us.get("p95_mm", float("nan")),
        "no_us_rmse_mm": no_us.get("rmse_mm", float("nan")),
        "us_p50_mm": us.get("p50_mm", float("nan")),
        "us_p95_mm": us.get("p95_mm", float("nan")),
        "us_rmse_mm": us.get("rmse_mm", float("nan")),
        "delta_us_minus_no_us_p50_mm": us.get("p50_mm", float("nan")) - no_us.get("p50_mm", float("nan")),
        "delta_us_minus_no_us_p95_mm": us.get("p95_mm", float("nan")) - no_us.get("p95_mm", float("nan")),
        "delta_us_minus_no_us_rmse_mm": us.get("rmse_mm", float("nan")) - no_us.get("rmse_mm", float("nan")),
        "note": note,
    }
    rows.append(row)


def static_production(root: Path) -> dict[str, float]:
    df = pd.read_csv(root / "tables/tag_abs_errors_per_session.csv")
    g = df[(df["version"].astype(str) == "v4-io") & (df["eval_set"].astype(str) == "all8")].copy()
    return summarize_errors(g)


def static_raw(root: Path, method: str = "T4") -> dict[str, float]:
    df = pd.read_csv(root / "tables/tag_raw_replay_accuracy_summary.csv")
    row = first(df, version="v4-io", eval_set="all8", tag_method=method)
    if row is None:
        return {"p50_mm": float("nan"), "p95_mm": float("nan"), "rmse_mm": float("nan")}
    return {
        "p50_mm": float(row["err_3d_median_mm"]),
        "p95_mm": float(row["err_3d_p95_mm"]),
        "rmse_mm": float(row["err_3d_rms_mm"]),
    }


def static_filtered(root: Path, solver: str) -> dict[str, float]:
    path = root / "filtered_deployment/tables/filtered_static_accuracy_summary.csv"
    if not path.exists():
        return {"p50_mm": float("nan"), "p95_mm": float("nan"), "rmse_mm": float("nan")}
    df = pd.read_csv(path)
    row = first(df, version="v4-io", eval_set="all8", solver=solver)
    if row is None:
        return {"p50_mm": float("nan"), "p95_mm": float("nan"), "rmse_mm": float("nan")}
    return {
        "p50_mm": float(row["err_3d_median_mm"]),
        "p95_mm": float(row["err_3d_p95_mm"]),
        "rmse_mm": float(row["err_3d_rms_mm"]),
    }


def static_4way_best(comp: Path, label: str, filter_kwargs: dict[str, str]) -> dict[str, float]:
    df = pd.read_csv(comp / "tables/static_4way_accuracy_summary.csv")
    g = df.copy()
    for col, val in filter_kwargs.items():
        g = g[g[col].astype(str) == str(val)]
    if g.empty:
        return {"p50_mm": float("nan"), "p95_mm": float("nan"), "rmse_mm": float("nan")}
    row = g.sort_values(["err_3d_median_mm", "err_3d_p95_mm"]).iloc[0]
    return {
        "p50_mm": float(row["err_3d_median_mm"]),
        "p95_mm": float(row["err_3d_p95_mm"]),
        "rmse_mm": float(row.get("err_3d_rms_mm", float("nan"))),
        "label": label,
    }


def roto_solver(root: Path, layout: str = "v4-io", method: str = "T4") -> dict[str, float]:
    df = pd.read_csv(root / "roto_absolute/tables/roto_abs_summary_by_solver.csv")
    row = first(df, layout=layout, tag_method=method)
    if row is None:
        return {"p50_mm": float("nan"), "p95_mm": float("nan"), "rmse_mm": float("nan")}
    return {
        "p50_mm": float(row["err3d_p50_track_median_mm"]),
        "p95_mm": float(row["err3d_p95_track_median_mm"]),
        "rmse_mm": float(row["err3d_rmse_track_median_mm"]),
    }


def roto_4way_best(comp: Path, filter_kwargs: dict[str, str]) -> dict[str, float]:
    df = pd.read_csv(comp / "tables/roto_4way_accuracy_summary.csv")
    g = df.copy()
    for col, val in filter_kwargs.items():
        g = g[g[col].astype(str) == str(val)]
    if g.empty:
        return {"p50_mm": float("nan"), "p95_mm": float("nan"), "rmse_mm": float("nan")}
    row = g.sort_values(["err3d_p50_track_median_mm", "err3d_p95_track_median_mm"]).iloc[0]
    return {
        "p50_mm": float(row["err3d_p50_track_median_mm"]),
        "p95_mm": float(row["err3d_p95_track_median_mm"]),
        "rmse_mm": float(row.get("err3d_rmse_track_median_mm", float("nan"))),
    }


def filtered_roto(comp: Path, case: str, filter_id: str) -> dict[str, float]:
    df = pd.read_csv(comp / "roto_filtered/tables/roto_filtered_summary.csv")
    row = first(df, case=case, filter_id=filter_id)
    if row is None:
        return {"p50_mm": float("nan"), "p95_mm": float("nan"), "rmse_mm": float("nan")}
    return {
        "p50_mm": float(row["trackmedian_err3d_p50_mm"]),
        "p95_mm": float(row["trackmedian_err3d_p95_mm"]),
        "rmse_mm": float(row["sample_err3d_rmse_mm"]),
    }


def pseudo_roto(comp: Path, case: str, fusion_id: str) -> dict[str, float]:
    df = pd.read_csv(comp / "roto_pseudo_imu/tables/roto_pseudo_imu_summary.csv")
    row = first(df, case=case, fusion_id=fusion_id)
    if row is None:
        return {"p50_mm": float("nan"), "p95_mm": float("nan"), "rmse_mm": float("nan")}
    return {
        "p50_mm": float(row["trackmedian_err3d_p50_mm"]),
        "p95_mm": float(row["trackmedian_err3d_p95_mm"]),
        "rmse_mm": float(row["sample_err3d_rmse_mm"]),
    }


def load_layout_points(layout_dir: Path, version: str) -> dict[str, np.ndarray]:
    data = json.loads((layout_dir / version / "layout.json").read_text(encoding="utf-8"))
    return {
        str(a["label"]).upper(): np.asarray([float(a["x_mm"]), float(a["y_mm"]), float(a["z_mm"])])
        for a in data["anchors"]
    }


def load_opti_anchor_truth(full_root: Path) -> dict[str, np.ndarray]:
    df = pd.read_csv(full_root / "tables/opti_anchor_medians_by_file.csv")
    out: dict[str, np.ndarray] = {}
    for anchor, g in df.groupby("anchor"):
        out[str(anchor)] = np.asarray(
            [
                float(np.median(g["x_mm"])),
                float(np.median(g["y_vertical_mm"])),
                float(np.median(g["z_mm"])),
            ],
            dtype=float,
        )
    return out


def fit_2d_rigid(src_xy: np.ndarray, dst_xy: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    src_c = src_xy.mean(axis=0)
    dst_c = dst_xy.mean(axis=0)
    x = src_xy - src_c
    y = dst_xy - dst_c
    u, _s, vt = np.linalg.svd(x.T @ y)
    d = np.ones(2)
    if np.linalg.det(u @ vt) < 0:
        d[-1] = -1.0
    r = u @ np.diag(d) @ vt
    t = dst_c - src_c @ r
    return r, t, float(np.linalg.det(r))


def apply_height_preserving(points_us: np.ndarray, r2: np.ndarray, t2: np.ndarray, z_shift: float) -> np.ndarray:
    xy = points_us[:, :2] @ r2 + t2
    vertical = points_us[:, 2] + z_shift
    return np.column_stack([xy[:, 0], vertical, xy[:, 1]])


def us_height_preserving_metrics(full_us: Path, layout_us: Path, version: str = "v4-io") -> tuple[list[dict], list[dict]]:
    layout_points = load_layout_points(layout_us, version)
    opti = load_opti_anchor_truth(full_us)
    labels = [a for a in ANCHORS if a in layout_points and a in opti]
    src = np.vstack([layout_points[a] for a in labels])
    dst = np.vstack([opti[a] for a in labels])
    # Source horizontal is US x/y, source vertical is US z. Destination horizontal is Opti X/Z, vertical is Opti Y.
    r2, t2, det2 = fit_2d_rigid(src[:, :2], dst[:, [0, 2]])
    z_shift = float(np.mean(dst[:, 1] - src[:, 2]))
    anchor_aligned = apply_height_preserving(src, r2, t2, z_shift)
    anchor_diff = anchor_aligned - dst
    anchor_rows = []
    for label, diff, aligned, truth in zip(labels, anchor_diff, anchor_aligned, dst, strict=True):
        anchor_rows.append(
            {
                "version": version,
                "anchor": label,
                "alignment": "US height-preserving: 2D horizontal rigid + vertical shift, no pitch/roll or 3D scale",
                "err_3d_mm": float(np.linalg.norm(diff)),
                "err_horizontal_xz_mm": float(math.hypot(diff[0], diff[2])),
                "err_vertical_y_mm": float(abs(diff[1])),
                "aligned_x_mm": float(aligned[0]),
                "aligned_y_vertical_mm": float(aligned[1]),
                "aligned_z_mm": float(aligned[2]),
                "truth_x_mm": float(truth[0]),
                "truth_y_vertical_mm": float(truth[1]),
                "truth_z_mm": float(truth[2]),
                "fit_det_2d": det2,
                "fit_vertical_shift_mm": z_shift,
            }
        )

    static_src = pd.read_csv(layout_us / "tables/static_all_captures.csv")
    static_src = static_src[static_src["version"].astype(str) == version].copy()
    truth_df = pd.read_csv(full_us / "tables/tag_abs_errors_per_session.csv")
    truth_by_id = {
        str(r["ID"]): np.asarray([r["truth_x_mm"], r["truth_y_vertical_mm"], r["truth_z_mm"]], dtype=float)
        for _, r in truth_df[(truth_df["version"].astype(str) == version) & (truth_df["eval_set"].astype(str) == "all8")].iterrows()
    }
    static_rows = []
    for _, row in static_src.iterrows():
        sid = str(row["ID"])
        truth = truth_by_id.get(sid)
        if truth is None:
            continue
        p_us = np.asarray([[float(row["mean_x"]), float(row["mean_y"]), float(row["mean_z"])]], dtype=float)
        aligned = apply_height_preserving(p_us, r2, t2, z_shift)[0]
        diff = aligned - truth
        static_rows.append(
            {
                "version": version,
                "ID": sid,
                "alignment": "US height-preserving: 2D horizontal rigid + vertical shift, no pitch/roll or 3D scale",
                "err_3d_mm": float(np.linalg.norm(diff)),
                "err_horizontal_xz_mm": float(math.hypot(diff[0], diff[2])),
                "err_vertical_y_mm": float(abs(diff[1])),
                "aligned_x_mm": float(aligned[0]),
                "aligned_y_vertical_mm": float(aligned[1]),
                "aligned_z_mm": float(aligned[2]),
                "truth_x_mm": float(truth[0]),
                "truth_y_vertical_mm": float(truth[1]),
                "truth_z_mm": float(truth[2]),
                "fit_det_2d": det2,
                "fit_vertical_shift_mm": z_shift,
            }
        )
    return anchor_rows, static_rows


def build_headline_rows(extra_root: Path, comp_no: Path, comp_us: Path, full_no: Path, full_us: Path) -> list[dict]:
    rows: list[dict] = []
    add_pair_row(
        rows,
        metric_id="static_production_anchor_locked_v4io",
        family="static_anchor_locked",
        metric_label="Production static v4-io, legacy 3D anchor-locked evaluation",
        no_us=static_production(full_no),
        us=static_production(full_us),
        note="This old metric permits full 3D anchor-locking to OptiTrack; pure gauge changes can disappear.",
    )
    add_pair_row(
        rows,
        metric_id="static_raw_replay_anchor_locked_v4io_T4",
        family="static_anchor_locked",
        metric_label="Raw replay median-estimator v4-io/T4, legacy anchor-locked evaluation",
        no_us=static_raw(full_no, "T4"),
        us=static_raw(full_us, "T4"),
        note="Median-estimator ablation, not deployed production mean aggregation.",
    )
    add_pair_row(
        rows,
        metric_id="static_filtered_anchor_locked_v4io_T4_F5",
        family="static_filter_anchor_locked",
        metric_label="Static filtered deployment v4-io/T4+F5, legacy anchor-locked evaluation",
        no_us=static_filtered(full_no, "T4+F5"),
        us=static_filtered(full_us, "T4+F5"),
        note="Stationary smoother/static-lock; still old anchor-locked evaluation.",
    )
    add_pair_row(
        rows,
        metric_id="static_one_baseline_EH_delaycal_v4io_T4",
        family="static_4way_anchor_locked",
        metric_label="One-baseline E-H + delaycal v4-io/T4 static",
        no_us=static_4way_best(
            comp_no,
            "one-baseline E-H",
            {
                "experiment": "one_baseline",
                "layout_solver": "v4-io",
                "layout_variant": "one_baseline_scale",
                "delay_mode": "one_baseline_layout_inter_anchor_delaycal",
                "scale_source": "E-H",
                "tag_method": "T4",
            },
        ),
        us=static_4way_best(
            comp_us,
            "one-baseline E-H",
            {
                "experiment": "one_baseline",
                "layout_solver": "v4-io",
                "layout_variant": "one_baseline_scale",
                "delay_mode": "one_baseline_layout_inter_anchor_delaycal",
                "scale_source": "E-H",
                "tag_method": "T4",
            },
        ),
        note="Derived 4-way static row under the same old aggregate metric.",
    )
    add_pair_row(
        rows,
        metric_id="roto_original_anchor_locked_v4io_T4",
        family="roto_anchor_locked",
        metric_label="ROTO original v4-io/T4 track-median ATE",
        no_us=roto_solver(full_no, "v4-io", "T4"),
        us=roto_solver(full_us, "v4-io", "T4"),
        note="ROTO absolute samples are evaluated after fixed capture-level alignment; pure static gauge changes may cancel.",
    )
    add_pair_row(
        rows,
        metric_id="roto_one_baseline_best_T4",
        family="roto_4way_anchor_locked",
        metric_label="Best one-baseline ROTO T4 row",
        no_us=roto_4way_best(comp_no, {"experiment": "one_baseline", "layout_solver": "v4-io", "tag_method": "T4"}),
        us=roto_4way_best(comp_us, {"experiment": "one_baseline", "layout_solver": "v4-io", "tag_method": "T4"}),
        note="Best matching one-baseline/v4-io/T4 ROTO row from each 4-way table.",
    )
    for fid in ["F4", "F5"]:
        add_pair_row(
            rows,
            metric_id=f"roto_filtered_{fid}_original_v4io_T4",
            family="roto_filter_anchor_locked",
            metric_label=f"ROTO filtered original v4-io/T4 {fid}",
            no_us=filtered_roto(comp_no, "full_original_v4io_T4", fid),
            us=filtered_roto(comp_us, "full_original_v4io_T4", fid),
            note="Post-solve trajectory filter; same old ROTO evaluation.",
        )
    for pid in ["PI1", "PI4"]:
        add_pair_row(
            rows,
            metric_id=f"roto_pseudo_imu_{pid}_original_v4io_T4",
            family="roto_pseudo_imu_anchor_locked",
            metric_label=f"ROTO pseudo-IMU original v4-io/T4 {pid}",
            no_us=pseudo_roto(comp_no, "full_original_v4io_T4", pid),
            us=pseudo_roto(comp_us, "full_original_v4io_T4", pid),
            note="OptiTrack-derived oracle prior; same old ROTO evaluation.",
        )
    return rows


def write_report(out_root: Path, headline: list[dict], us_anchor: list[dict], us_static: list[dict]) -> None:
    report_dir = out_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# FULL No-US vs US30 Height-Gauge Comparison")
    lines.append("")
    lines.append(f"Generated {datetime.now(UTC).isoformat()}.")
    lines.append("")
    lines.append(
        "This report compares the original FULL 4-way analysis against the US30/FGH height-gauged rerun. "
        "It deliberately separates legacy anchor-locked metrics from the new US height-preserving check."
    )
    lines.append("")
    lines.append("## Key Point")
    lines.append("")
    identical = [
        r
        for r in headline
        if math.isfinite(float(r["delta_us_minus_no_us_p50_mm"]))
        and abs(float(r["delta_us_minus_no_us_p50_mm"])) < 1e-6
        and abs(float(r["delta_us_minus_no_us_p95_mm"])) < 1e-6
    ]
    lines.append(
        f"- Legacy anchor-locked rows with effectively zero US/no-US delta: {len(identical)} / {len(headline)}. "
        "This is expected when a metric permits a full 3D rigid/capture alignment that can absorb a gauge-only coordinate change."
    )
    us_static_v4 = [r for r in us_static if r.get("version") == "v4-io"]
    us_anchor_v4 = [r for r in us_anchor if r.get("version") == "v4-io"]
    if us_static_v4:
        lines.append(
            f"- US height-preserving v4-io static point error: P50 {fmt(pct([r['err_3d_mm'] for r in us_static_v4], 50))} mm, "
            f"P95 {fmt(pct([r['err_3d_mm'] for r in us_static_v4], 95))} mm, RMSE {fmt(rmse([r['err_3d_mm'] for r in us_static_v4]))} mm."
        )
    if us_anchor_v4:
        lines.append(
            f"- US height-preserving v4-io anchor error: P50 {fmt(pct([r['err_3d_mm'] for r in us_anchor_v4], 50))} mm, "
            f"P95 {fmt(pct([r['err_3d_mm'] for r in us_anchor_v4], 95))} mm, RMSE {fmt(rmse([r['err_3d_mm'] for r in us_anchor_v4]))} mm."
        )
    lines.append("")
    lines.append("## Legacy No-US vs US Metrics")
    lines.append("")
    cols = [
        "metric_id",
        "family",
        "no_us_p50_mm",
        "no_us_p95_mm",
        "us_p50_mm",
        "us_p95_mm",
        "delta_us_minus_no_us_p50_mm",
        "delta_us_minus_no_us_p95_mm",
    ]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    for row in headline:
        vals = []
        for col in cols:
            if col in {"metric_id", "family"}:
                vals.append(str(row[col]))
            else:
                vals.append(fmt(float(row[col]), 2))
        lines.append("| " + " | ".join(vals) + " |")
    lines.append("")
    lines.append("## US Height-Preserving Metric")
    lines.append("")
    lines.append(
        "The height-preserving check fits only a 2D horizontal rigid transform plus one vertical shift from US-gauge anchors to OptiTrack. "
        "It does not allow 3D pitch/roll or global scale to erase the US height gauge. This is a deployment-gauge diagnostic, not the old paper headline metric."
    )
    lines.append("")
    lines.append("## Output Tables")
    lines.append("")
    for name in [
        "no_us_vs_us_headline.csv",
        "us_height_preserving_anchor_errors.csv",
        "us_height_preserving_static_errors.csv",
    ]:
        lines.append(f"- `../tables/{name}`")
    (report_dir / "FULL_NO_US_VS_US_COMPARISON.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate no-US vs US30 comparison report.")
    parser.add_argument("--extra-root", default="autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis")
    parser.add_argument("--layout-us", default="autopos_pipeline/28052026_Erlangen_Official/solver/outputs/v1_to_v4_io_field_check_US")
    parser.add_argument("--out-root", default=None)
    args = parser.parse_args()

    extra = Path(args.extra_root).resolve()
    out_root = Path(args.out_root).resolve() if args.out_root else extra / "FULL_NO_US_VS_US"
    comp_no = extra / "FULL_4way_comparison"
    comp_us = extra / "FULL_4way_comparison_US"
    full_no = extra / "FULL"
    full_us = extra / "FULL_US"
    layout_us = Path(args.layout_us).resolve()

    (out_root / "tables").mkdir(parents=True, exist_ok=True)
    headline = build_headline_rows(extra, comp_no, comp_us, full_no, full_us)
    versions_path = layout_us / "tables/us_height_layout_summary.csv"
    if versions_path.exists():
        versions = pd.read_csv(versions_path)["version"].astype(str).tolist()
    else:
        versions = ["v1-old", "v2", "v3-lite", "v3-full", "v4-io"]
    us_anchor: list[dict] = []
    us_static: list[dict] = []
    for version in versions:
        a_rows, s_rows = us_height_preserving_metrics(full_us, layout_us, version)
        us_anchor.extend(a_rows)
        us_static.extend(s_rows)
    write_csv(out_root / "tables/no_us_vs_us_headline.csv", headline)
    write_csv(out_root / "tables/us_height_preserving_anchor_errors.csv", us_anchor)
    write_csv(out_root / "tables/us_height_preserving_static_errors.csv", us_static)
    write_report(out_root, headline, us_anchor, us_static)
    print(f"Wrote {out_root / 'reports/FULL_NO_US_VS_US_COMPARISON.md'}")


if __name__ == "__main__":
    main()
