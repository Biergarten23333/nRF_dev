#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from collections import deque
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.path import Path as MplPath
import numpy as np
from scipy.ndimage import binary_closing, binary_opening
from scipy.spatial import ConvexHull, cKDTree


ANALYSIS = Path(__file__).resolve().parent
BASE = ANALYSIS / "figures" / "w05_dynamic_probe" / "residual_maps"
ANCHORS = list("ABCDEFGH")

QUALITY_THRESH_MM = 70.0
ANCHOR_EXCLUSION_RADII_MM = [500.0, 700.0, 1000.0]
GRID_N = 300


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict]) -> None:
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


def load_json(path: Path):
    return json.loads(path.read_text())


def load_points(solver: str):
    path = BASE / f"{solver}_solver" / f"w05_{solver.lower()}_usable_area_points.csv"
    return [{
        "x": float(r["x_mm"]),
        "y": float(r["y_mm"]),
        "err": float(r["median_abs_residual_mm"]),
    } for r in read_csv(path)]


def load_anchors(solver: str):
    name = "v3_lite_layout.json" if solver == "V3" else "v4_io_layout.json"
    layout = load_json(ANALYSIS / "solves" / name)["anchors"]
    return {a: np.array([float(layout[a][0]), float(layout[a][1])], dtype=float) for a in ANCHORS}


def idw_grid(points, xi, yi, k=20, power=2.0):
    pts = np.array([[p["x"], p["y"]] for p in points], dtype=float)
    vals = np.array([p["err"] for p in points], dtype=float)
    tree = cKDTree(pts)
    grid_pts = np.column_stack([xi.ravel(), yi.ravel()])
    dist, idx = tree.query(grid_pts, k=min(k, len(points)))
    dist = np.maximum(dist, 1e-6)
    weights = 1.0 / (dist ** power)
    out = np.sum(weights * vals[idx], axis=1) / np.sum(weights, axis=1)
    return out.reshape(xi.shape)


def largest_component(mask: np.ndarray) -> np.ndarray:
    seen = np.zeros(mask.shape, dtype=bool)
    best = []
    h, w = mask.shape
    for r in range(h):
        for c in range(w):
            if not mask[r, c] or seen[r, c]:
                continue
            comp = []
            q = deque([(r, c)])
            seen[r, c] = True
            while q:
                rr, cc = q.popleft()
                comp.append((rr, cc))
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        if dr == 0 and dc == 0:
                            continue
                        nr, nc = rr + dr, cc + dc
                        if 0 <= nr < h and 0 <= nc < w and mask[nr, nc] and not seen[nr, nc]:
                            seen[nr, nc] = True
                            q.append((nr, nc))
            if len(comp) > len(best):
                best = comp
    out = np.zeros(mask.shape, dtype=bool)
    for r, c in best:
        out[r, c] = True
    return out


def min_dist_to_anchors(grid_xy, anchor_xy):
    tree = cKDTree(anchor_xy)
    dist, _ = tree.query(grid_xy, k=1)
    return dist


def plot_solver_radius(solver: str, exclusion_radius_mm: float):
    points = load_points(solver)
    anchors = load_anchors(solver)
    anchor_xy = np.asarray([anchors[a] for a in ANCHORS], dtype=float)
    hull = ConvexHull(anchor_xy)
    hull_xy = anchor_xy[hull.vertices]
    poly = MplPath(hull_xy)

    xmin, ymin = hull_xy.min(axis=0)
    xmax, ymax = hull_xy.max(axis=0)
    pad = 200.0
    gx = np.linspace(xmin - pad, xmax + pad, GRID_N)
    gy = np.linspace(ymin - pad, ymax + pad, GRID_N)
    XI, YI = np.meshgrid(gx, gy)
    grid = np.column_stack([XI.ravel(), YI.ravel()])
    inside = poly.contains_points(grid).reshape(XI.shape)
    nearest_anchor = min_dist_to_anchors(grid, anchor_xy).reshape(XI.shape)
    ZI = idw_grid(points, XI, YI)

    physical_mask = inside & (nearest_anchor >= exclusion_radius_mm)
    quality_mask = ZI <= QUALITY_THRESH_MM
    mask = physical_mask & quality_mask
    mask = binary_opening(mask, structure=np.ones((2, 2)))
    mask = binary_closing(mask, structure=np.ones((9, 9)))
    region = largest_component(mask)

    cell_area_m2 = abs((gx[1] - gx[0]) * (gy[1] - gy[0])) / 1e6
    area_m2 = float(region.sum() * cell_area_m2)
    med_err = float(np.median(ZI[region])) if region.any() else float("nan")
    p90_err = float(np.percentile(ZI[region], 90)) if region.any() else float("nan")

    outdir = BASE / f"{solver}_solver"
    fig, ax = plt.subplots(figsize=(9.5, 7.2))
    im = ax.imshow(
        ZI, origin="lower", extent=[gx[0], gx[-1], gy[0], gy[-1]],
        aspect="auto", cmap="magma_r", vmin=0, vmax=float(np.nanpercentile(ZI, 92)),
    )
    ax.plot(*np.vstack([hull_xy, hull_xy[0]]).T, color="white", lw=1.2, alpha=0.9, label="8-anchor hull")
    for a, xy in anchors.items():
        ax.add_patch(plt.Circle(xy, exclusion_radius_mm, fill=False, edgecolor="cyan", linewidth=1.0, alpha=0.45))
        ax.scatter(xy[0], xy[1], marker="^", s=72, c="white", edgecolors="black", linewidths=0.8, zorder=5)
        ax.text(xy[0], xy[1], f" {a}", fontsize=9, color="black", weight="bold", zorder=6)
    if region.any():
        ax.contour(XI, YI, region.astype(float), levels=[0.5], colors="red", linewidths=4.0)
    ax.set_title(f"W05 {solver}: activity region, anchor exclusion {exclusion_radius_mm:.0f} mm")
    ax.set_xlabel("X mm")
    ax.set_ylabel("Y mm")
    ax.legend(loc="upper right")
    cbar = fig.colorbar(im, ax=ax, shrink=0.92)
    cbar.set_label("estimated median abs residual (mm)")
    out = outdir / f"fig_w05_{solver.lower()}_activity_region_anchor_exclusion_{int(exclusion_radius_mm)}mm_xy.png"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)

    return {
        "solver": solver,
        "quality_thresh_mm": QUALITY_THRESH_MM,
        "anchor_exclusion_radius_mm": exclusion_radius_mm,
        "area_m2": area_m2,
        "median_estimated_residual_mm": med_err,
        "p90_estimated_residual_mm": p90_err,
        "grid_cells": int(region.sum()),
    }


def main():
    rows = []
    for solver in ["V3", "V4"]:
        for radius in ANCHOR_EXCLUSION_RADII_MM:
            rows.append(plot_solver_radius(solver, radius))
    write_csv(BASE / "w05_activity_region_anchor_exclusion_summary.csv", rows)
    print("Wrote anchor-exclusion activity region figures and summary")


if __name__ == "__main__":
    main()
