#!/usr/bin/env python3
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ANALYSIS = Path(__file__).resolve().parent
BASE = ANALYSIS / "figures" / "w05_dynamic_probe" / "residual_maps"
THRESHOLDS_MM = [40.0, 60.0, 80.0, 120.0]
DEFAULT_USABLE_MM = 60.0
MIN_ANCHORS = 7


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    fields = []
    for row in rows:
        for k in row:
            if k not in fields:
                fields.append(k)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def load_points(solver_dir: Path, solver: str):
    residual_path = solver_dir / f"w05_per_anchor_residuals_{solver.lower()}_physical_z.csv"
    rows = read_csv(residual_path)
    grouped = defaultdict(list)
    for r in rows:
        peer = r.get("peer_name") or r.get("peer") or ""
        key = (peer, int(float(r["sweep"])))
        grouped[key].append(r)
    points = []
    for (peer, sweep), rs in grouped.items():
        abs_res = np.asarray([float(r["abs_residual_mm"]) for r in rs], dtype=float)
        signed = np.asarray([float(r["residual_mm"]) for r in rs], dtype=float)
        q = np.asarray([float(r["quality_percent"]) for r in rs], dtype=float)
        r0 = rs[0]
        def get_float(*names, default=0.0):
            for name in names:
                if name in r0 and r0[name] != "":
                    return float(r0[name])
            return default
        points.append({
            "peer_name": peer,
            "sweep": sweep,
            "x_mm": get_float("x_mm", "x"),
            "y_mm": get_float("y_mm", "y"),
            "z_mm": get_float("z_mm", "z"),
            "used_anchors": int(float(r0["used_anchors"])),
            "median_abs_residual_mm": float(np.median(abs_res)),
            "p80_abs_residual_mm": float(np.percentile(abs_res, 80)),
            "rms_residual_mm": float(np.sqrt(np.mean(signed * signed))),
            "median_quality_percent": float(np.median(q)),
        })
    return points


def projection_plot(points, out: Path, title: str, usable_mm: float = DEFAULT_USABLE_MM):
    specs = [("x_mm", "y_mm", "XY"), ("x_mm", "z_mm", "XZ"), ("y_mm", "z_mm", "YZ")]
    labels = {"x_mm": "X mm", "y_mm": "Y mm", "z_mm": "Z mm"}
    fig, axs = plt.subplots(1, 3, figsize=(17, 5.2))
    vals = np.asarray([p["median_abs_residual_mm"] for p in points], dtype=float)
    vmax = float(np.percentile(vals, 95))
    for ax, (xk, yk, name) in zip(axs, specs):
        bad = [p for p in points if not (p["used_anchors"] >= MIN_ANCHORS and p["median_abs_residual_mm"] <= usable_mm)]
        good = [p for p in points if p["used_anchors"] >= MIN_ANCHORS and p["median_abs_residual_mm"] <= usable_mm]
        if bad:
            ax.scatter([p[xk] for p in bad], [p[yk] for p in bad], c="#c9c9c9", s=5, alpha=0.23, linewidths=0, label="outside criterion")
        if good:
            sc = ax.scatter([p[xk] for p in good], [p[yk] for p in good],
                            c=[p["median_abs_residual_mm"] for p in good],
                            cmap="viridis_r", vmin=0, vmax=vmax, s=9, alpha=0.82, linewidths=0,
                            label=f"usable <= {usable_mm:.0f} mm")
        else:
            sc = ax.scatter([], [])
        ax.set_title(name)
        ax.set_xlabel(labels[xk])
        ax.set_ylabel(labels[yk])
        ax.grid(True, alpha=0.25)
        ax.legend(loc="best", fontsize=8)
    fig.colorbar(sc, ax=axs.tolist(), shrink=0.9, label="median abs residual per solved point (mm)")
    fig.suptitle(title)
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)


def threshold_panels(points, out: Path, title: str):
    fig, axs = plt.subplots(2, 2, figsize=(12.5, 10.5), sharex=True, sharey=True)
    for ax, thr in zip(axs.ravel(), THRESHOLDS_MM):
        good = [p for p in points if p["used_anchors"] >= MIN_ANCHORS and p["median_abs_residual_mm"] <= thr]
        bad = [p for p in points if p not in good]
        if bad:
            ax.scatter([p["x_mm"] for p in bad], [p["y_mm"] for p in bad], c="#d1d1d1", s=4, alpha=0.18, linewidths=0)
        if good:
            ax.scatter([p["x_mm"] for p in good], [p["y_mm"] for p in good], c=[p["z_mm"] for p in good],
                       cmap="plasma", s=8, alpha=0.8, linewidths=0)
        pct = 100.0 * len(good) / max(1, len(points))
        ax.set_title(f"median abs residual <= {thr:.0f} mm: {pct:.1f}%")
        ax.set_xlabel("X mm")
        ax.set_ylabel("Y mm")
        ax.grid(True, alpha=0.25)
    fig.suptitle(title + " - XY usable area by threshold")
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)


def binned_heatmaps(points, out: Path, title: str):
    specs = [("x_mm", "y_mm", "XY"), ("x_mm", "z_mm", "XZ"), ("y_mm", "z_mm", "YZ")]
    labels = {"x_mm": "X mm", "y_mm": "Y mm", "z_mm": "Z mm"}
    fig, axs = plt.subplots(1, 3, figsize=(17, 5.4))
    for ax, (xk, yk, name) in zip(axs, specs):
        x = np.asarray([p[xk] for p in points], dtype=float)
        y = np.asarray([p[yk] for p in points], dtype=float)
        v = np.asarray([p["median_abs_residual_mm"] for p in points], dtype=float)
        bins = 45
        sum_v, xe, ye = np.histogram2d(x, y, bins=bins, weights=v)
        cnt, _, _ = np.histogram2d(x, y, bins=[xe, ye])
        med_like = np.divide(sum_v, cnt, out=np.full_like(sum_v, np.nan, dtype=float), where=cnt > 0)
        im = ax.imshow(med_like.T, origin="lower", extent=[xe[0], xe[-1], ye[0], ye[-1]],
                       aspect="auto", cmap="magma_r", vmin=0, vmax=np.nanpercentile(v, 90))
        ax.set_title(name)
        ax.set_xlabel(labels[xk])
        ax.set_ylabel(labels[yk])
        ax.grid(False)
    fig.colorbar(im, ax=axs.tolist(), shrink=0.9, label="bin mean of median abs residual (mm)")
    fig.suptitle(title + " - residual heatmap")
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)


def summarize(points, solver: str):
    rows = []
    n = len(points)
    for thr in THRESHOLDS_MM:
        good = [p for p in points if p["used_anchors"] >= MIN_ANCHORS and p["median_abs_residual_mm"] <= thr]
        rows.append({
            "solver": solver,
            "criterion": f"used_anchors>={MIN_ANCHORS} and median_abs_residual<={thr:.0f}mm",
            "points_total": n,
            "points_usable": len(good),
            "usable_percent": 100.0 * len(good) / max(1, n),
        })
    for peer in sorted({p["peer_name"] for p in points}):
        sub = [p for p in points if p["peer_name"] == peer]
        vals = np.asarray([p["median_abs_residual_mm"] for p in sub], dtype=float)
        rows.append({
            "solver": solver,
            "criterion": f"peer={peer}",
            "points_total": len(sub),
            "median_abs_residual_mm": float(np.median(vals)),
            "p90_abs_residual_mm": float(np.percentile(vals, 90)),
            "usable_60mm_percent": 100.0 * sum(p["used_anchors"] >= MIN_ANCHORS and p["median_abs_residual_mm"] <= DEFAULT_USABLE_MM for p in sub) / max(1, len(sub)),
        })
    return rows


def main():
    all_summary = []
    for solver in ["V3", "V4"]:
        solver_dir = BASE / f"{solver}_solver"
        points = load_points(solver_dir, solver)
        write_csv(solver_dir / f"w05_{solver.lower()}_usable_area_points.csv", points)
        write_csv(solver_dir / f"w05_{solver.lower()}_usable_area_summary.csv", summarize(points, solver))
        all_summary.extend(summarize(points, solver))
        projection_plot(points, solver_dir / f"fig_w05_{solver.lower()}_usable_area_current_session_projections.png",
                        f"W05 {solver}: usable area in current UWB session")
        threshold_panels(points, solver_dir / f"fig_w05_{solver.lower()}_usable_area_thresholds_xy.png",
                         f"W05 {solver}: usable area in current UWB session")
        binned_heatmaps(points, solver_dir / f"fig_w05_{solver.lower()}_usable_area_residual_heatmaps.png",
                        f"W05 {solver}: usable area in current UWB session")
    write_csv(BASE / "w05_v3_v4_usable_area_summary.csv", all_summary)
    print(f"Wrote usable-area figures and summaries under {BASE}/V3_solver and {BASE}/V4_solver")


if __name__ == "__main__":
    main()
