#!/usr/bin/env python3
"""ROTO dynamic diagnostics: speed, phase, radius, and two-tag consistency.

Input is a `roto_absolute` directory containing:

* tables/roto_abs_samples_v4io_T4.csv
* tables/roto_abs_per_track.csv

The script writes a `dynamic_diagnostics` subdirectory under that ROTO root.
It is intentionally table-driven so the same script can be reused for original
FULL, known-anchor, full-scale, and one-baseline ROTO outputs.
"""

from __future__ import annotations

import argparse
import csv
import math
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


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


def finite_percentile(values, pct: float) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, pct))


def summarize(values, prefix: str) -> dict:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {
            f"{prefix}_mean_mm": float("nan"),
            f"{prefix}_rmse_mm": float("nan"),
            f"{prefix}_p50_mm": float("nan"),
            f"{prefix}_p90_mm": float("nan"),
            f"{prefix}_p95_mm": float("nan"),
            f"{prefix}_max_mm": float("nan"),
        }
    return {
        f"{prefix}_mean_mm": float(np.mean(arr)),
        f"{prefix}_rmse_mm": float(math.sqrt(np.mean(arr * arr))),
        f"{prefix}_p50_mm": float(np.percentile(arr, 50)),
        f"{prefix}_p90_mm": float(np.percentile(arr, 90)),
        f"{prefix}_p95_mm": float(np.percentile(arr, 95)),
        f"{prefix}_max_mm": float(np.max(arr)),
    }


def fit_circle_plane(points: np.ndarray) -> dict:
    pts = np.asarray(points, dtype=float)
    pts = pts[np.isfinite(pts).all(axis=1)]
    if pts.shape[0] < 30:
        return {"status": "insufficient", "n": int(pts.shape[0])}
    center0 = pts.mean(axis=0)
    _u, _s, vh = np.linalg.svd(pts - center0, full_matrices=False)
    e1, e2, normal = vh[0], vh[1], vh[-1]
    uv = np.column_stack([(pts - center0) @ e1, (pts - center0) @ e2])
    x, y = uv[:, 0], uv[:, 1]
    a = np.column_stack([2 * x, 2 * y, np.ones_like(x)])
    b = x * x + y * y
    cx, cy, c = np.linalg.lstsq(a, b, rcond=None)[0]
    radius = math.sqrt(max(0.0, float(c + cx * cx + cy * cy)))
    center3 = center0 + cx * e1 + cy * e2
    return {
        "status": "ok",
        "center": center3,
        "e1": e1,
        "e2": e2,
        "normal": normal,
        "center_uv": np.asarray([cx, cy], dtype=float),
        "radius_mm": float(radius),
    }


def project_phase_radius(points: np.ndarray, circle: dict) -> tuple[np.ndarray, np.ndarray]:
    pts = np.asarray(points, dtype=float)
    rel = pts - circle["center"]
    u = rel @ circle["e1"]
    v = rel @ circle["e2"]
    uv = np.column_stack([u, v])
    radius = np.linalg.norm(uv, axis=1)
    phase = np.mod(np.arctan2(v, u), 2 * np.pi)
    return phase, radius


def add_motion_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    out_parts = []
    circle_rows = []
    for (capture_id, tag), g in df.groupby(["capture_id", "tag"], sort=True):
        g = g.sort_values("opti_time_s").copy()
        opti = g[["opti_x_mm", "opti_y_vertical_mm", "opti_z_mm"]].to_numpy(dtype=float)
        uwb = g[["uwb_x_mm", "uwb_y_vertical_mm", "uwb_z_mm"]].to_numpy(dtype=float)
        circle = fit_circle_plane(opti)
        if circle["status"] != "ok":
            continue
        phase, opti_radius = project_phase_radius(opti, circle)
        _uwb_phase, uwb_radius = project_phase_radius(uwb, circle)
        unwrapped = np.unwrap(phase)
        t = g["opti_time_s"].to_numpy(dtype=float)
        if t.size >= 3:
            omega = np.gradient(unwrapped, t)
        else:
            omega = np.full_like(unwrapped, np.nan)
        g["phase_rad"] = phase
        g["phase_deg"] = np.degrees(phase)
        g["angular_speed_rad_s"] = np.abs(omega)
        g["angular_speed_deg_s"] = np.degrees(np.abs(omega))
        g["opti_radius_mm"] = opti_radius
        g["uwb_radius_in_opti_circle_plane_mm"] = uwb_radius
        g["signed_radius_error_mm"] = uwb_radius - opti_radius
        g["abs_radius_error_mm"] = np.abs(g["signed_radius_error_mm"])
        out_parts.append(g)
        circle_rows.append(
            {
                "capture_id": capture_id,
                "tag": tag,
                "n_samples": int(len(g)),
                "opti_circle_radius_mm": float(circle["radius_mm"]),
                "angular_speed_deg_s_median": finite_percentile(g["angular_speed_deg_s"], 50),
                "angular_speed_deg_s_p95": finite_percentile(g["angular_speed_deg_s"], 95),
                "abs_radius_error_p50_mm": finite_percentile(g["abs_radius_error_mm"], 50),
                "abs_radius_error_p95_mm": finite_percentile(g["abs_radius_error_mm"], 95),
            }
        )
    if not out_parts:
        return pd.DataFrame(), circle_rows
    return pd.concat(out_parts, ignore_index=True), circle_rows


def bin_summary(df: pd.DataFrame, by: str, label_col: str) -> list[dict]:
    rows = []
    for key, g in df.groupby(by, sort=True):
        row = {label_col: key, "n_samples": int(len(g))}
        row.update(summarize(g["err3d_mm"], "err3d"))
        row.update(summarize(g["err_horizontal_xz_mm"], "err_horizontal_xz"))
        row.update(summarize(g["err_vertical_y_mm"], "err_vertical_y"))
        row.update(summarize(g["abs_radius_error_mm"], "abs_radius"))
        rows.append(row)
    return rows


def make_speed_bins(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    vals = df["angular_speed_deg_s"].to_numpy(dtype=float)
    vals = vals[np.isfinite(vals)]
    if vals.size < 10:
        df["speed_bin"] = "unknown"
        return df
    q1, q2 = np.percentile(vals, [33.333, 66.667])
    def label(v: float) -> str:
        if not math.isfinite(v):
            return "unknown"
        if v <= q1:
            return f"slow <= {q1:.1f} deg/s"
        if v <= q2:
            return f"mid {q1:.1f}-{q2:.1f} deg/s"
        return f"fast > {q2:.1f} deg/s"
    df["speed_bin"] = [label(float(v)) for v in df["angular_speed_deg_s"]]
    return df


def make_phase_bins(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    edges = [0, 90, 180, 270, 360]
    labels = ["0-90", "90-180", "180-270", "270-360"]
    df["phase_bin_deg"] = pd.cut(df["phase_deg"], bins=edges, labels=labels, include_lowest=True, right=False).astype(str)
    return df


def relative_distance_consistency(df: pd.DataFrame, tolerance_s: float) -> tuple[list[dict], list[dict]]:
    sample_rows = []
    summary_rows = []
    for capture_id, g in df.groupby("capture_id", sort=True):
        b = g[g["tag"] == "BS2DCE"].sort_values("uwb_time_s")
        c = g[g["tag"] == "BSDC91"].sort_values("uwb_time_s")
        if b.empty or c.empty:
            continue
        merged = pd.merge_asof(
            b,
            c,
            on="uwb_time_s",
            suffixes=("_B", "_C"),
            tolerance=tolerance_s,
            direction="nearest",
        ).dropna(subset=["uwb_x_mm_C"])
        if merged.empty:
            continue
        uwb_b = merged[["uwb_x_mm_B", "uwb_y_vertical_mm_B", "uwb_z_mm_B"]].to_numpy(dtype=float)
        uwb_c = merged[["uwb_x_mm_C", "uwb_y_vertical_mm_C", "uwb_z_mm_C"]].to_numpy(dtype=float)
        opti_b = merged[["opti_x_mm_B", "opti_y_vertical_mm_B", "opti_z_mm_B"]].to_numpy(dtype=float)
        opti_c = merged[["opti_x_mm_C", "opti_y_vertical_mm_C", "opti_z_mm_C"]].to_numpy(dtype=float)
        uwb_dist = np.linalg.norm(uwb_b - uwb_c, axis=1)
        opti_dist = np.linalg.norm(opti_b - opti_c, axis=1)
        signed = uwb_dist - opti_dist
        abs_err = np.abs(signed)
        for i, row in enumerate(merged.itertuples(index=False)):
            sample_rows.append(
                {
                    "capture_id": capture_id,
                    "uwb_time_s": float(getattr(row, "uwb_time_s")),
                    "uwb_relative_distance_mm": float(uwb_dist[i]),
                    "opti_relative_distance_mm": float(opti_dist[i]),
                    "signed_relative_distance_error_mm": float(signed[i]),
                    "abs_relative_distance_error_mm": float(abs_err[i]),
                }
            )
        summary = {"capture_id": capture_id, "n_matched_samples": int(len(merged))}
        summary.update(summarize(abs_err, "abs_relative_distance_error"))
        summary["signed_relative_distance_error_median_mm"] = finite_percentile(signed, 50)
        summary_rows.append(summary)
    return sample_rows, summary_rows


def plot_bin(rows: list[dict], label_col: str, metric: str, out: Path, title: str) -> None:
    if not rows:
        return
    labels = [str(r[label_col]) for r in rows]
    vals = [float(r.get(metric, np.nan)) for r in rows]
    fig, ax = plt.subplots(figsize=(8.5, 4.5), constrained_layout=True)
    ax.bar(np.arange(len(labels)), vals, color="#4C78A8")
    ax.set_xticks(np.arange(len(labels)))
    ax.set_xticklabels(labels, rotation=25, ha="right")
    ax.set_ylabel(metric.replace("_", " "))
    ax.set_title(title)
    ax.grid(axis="y", alpha=0.3)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=180)
    plt.close(fig)


def write_report(path: Path, speed_rows: list[dict], phase_rows: list[dict], radius_rows: list[dict], rel_rows: list[dict]) -> None:
    lines = ["# ROTO Dynamic Diagnostics\n\n"]
    lines.append(f"Generated {datetime.now(UTC).isoformat()}.\n\n")
    lines.append("This report decomposes the already time-aligned ROTO absolute samples by angular speed, rotation phase, radius error, horizontal/vertical dynamic error, and two-tag relative-distance consistency.\n\n")
    if speed_rows:
        best = min(speed_rows, key=lambda r: r["err3d_p50_mm"])
        worst = max(speed_rows, key=lambda r: r["err3d_p50_mm"])
        lines.append(f"- Speed bins: best median 3D `{best['speed_bin']}` = {best['err3d_p50_mm']:.1f} mm; worst `{worst['speed_bin']}` = {worst['err3d_p50_mm']:.1f} mm.\n")
    if phase_rows:
        vals = ", ".join(f"{r['phase_bin_deg']}:{r['err3d_p50_mm']:.1f}" for r in phase_rows)
        lines.append(f"- Phase-bin median 3D errors: {vals} mm.\n")
    if radius_rows:
        abs_p50 = finite_percentile([r["abs_radius_error_p50_mm"] for r in radius_rows], 50)
        abs_p95 = finite_percentile([r["abs_radius_error_p95_mm"] for r in radius_rows], 50)
        lines.append(f"- Track-level radius absolute error median-of-medians/P95-medians: {abs_p50:.1f} / {abs_p95:.1f} mm.\n")
    if rel_rows:
        rel_p50 = finite_percentile([r["abs_relative_distance_error_p50_mm"] for r in rel_rows], 50)
        rel_p95 = finite_percentile([r["abs_relative_distance_error_p95_mm"] for r in rel_rows], 50)
        lines.append(f"- Two-wand relative-distance abs error median-of-medians/P95-medians: {rel_p50:.1f} / {rel_p95:.1f} mm.\n")
    lines.append("\n## Tables\n\n")
    lines.append("- `tables/roto_dynamic_samples_v4io_T4.csv`\n")
    lines.append("- `tables/roto_error_by_angular_speed.csv`\n")
    lines.append("- `tables/roto_error_by_phase.csv`\n")
    lines.append("- `tables/roto_radius_error_by_track.csv`\n")
    lines.append("- `tables/roto_two_wand_relative_distance_summary.csv`\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="ROTO dynamic diagnostics from absolute sample tables.")
    parser.add_argument("--roto-root", required=True)
    parser.add_argument("--relative-tolerance-s", type=float, default=0.08)
    args = parser.parse_args()

    roto_root = Path(args.roto_root).resolve()
    samples_csv = roto_root / "tables/roto_abs_samples_v4io_T4.csv"
    if not samples_csv.exists():
        raise FileNotFoundError(samples_csv)
    out = roto_root / "dynamic_diagnostics"
    tables = out / "tables"
    figs = out / "figs"
    reports = out / "reports"
    for p in [tables, figs, reports]:
        p.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(samples_csv)
    dyn, radius_rows = add_motion_columns(df)
    dyn = make_speed_bins(make_phase_bins(dyn))
    write_csv(tables / "roto_dynamic_samples_v4io_T4.csv", dyn.to_dict("records"))
    speed_rows = bin_summary(dyn, "speed_bin", "speed_bin")
    phase_rows = bin_summary(dyn, "phase_bin_deg", "phase_bin_deg")
    write_csv(tables / "roto_error_by_angular_speed.csv", speed_rows)
    write_csv(tables / "roto_error_by_phase.csv", phase_rows)
    write_csv(tables / "roto_radius_error_by_track.csv", radius_rows)

    rel_samples, rel_summary = relative_distance_consistency(dyn, tolerance_s=args.relative_tolerance_s)
    write_csv(tables / "roto_two_wand_relative_distance_samples.csv", rel_samples)
    write_csv(tables / "roto_two_wand_relative_distance_summary.csv", rel_summary)

    plot_bin(speed_rows, "speed_bin", "err3d_p50_mm", figs / "roto_error_by_angular_speed.png", "ROTO error by angular speed")
    plot_bin(phase_rows, "phase_bin_deg", "err3d_p50_mm", figs / "roto_error_by_phase.png", "ROTO error by rotation phase")
    write_report(reports / "ROTO_DYNAMIC_DIAGNOSTICS.md", speed_rows, phase_rows, radius_rows, rel_summary)
    print(f"[roto-dynamic] wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
