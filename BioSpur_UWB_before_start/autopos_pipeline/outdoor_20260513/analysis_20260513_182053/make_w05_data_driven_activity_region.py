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
EDGE_MARGIN_MM = 0.0
GRID_N = 280


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


def polygon_signed_area(poly):
    x = poly[:, 0]
    y = poly[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - y * np.roll(x, -1)))


def polygon_distance_to_edges(points_xy, hull_xy):
    out = np.full(len(points_xy), np.inf)
    for i in range(len(hull_xy)):
        a = hull_xy[i]
        b = hull_xy[(i + 1) % len(hull_xy)]
        ab = b - a
        denom = float(np.dot(ab, ab))
        ap = points_xy - a
        t = np.clip((ap @ ab) / denom, 0.0, 1.0)
        closest = a + t[:, None] * ab
        out = np.minimum(out, np.linalg.norm(points_xy - closest, axis=1))
    return out


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


def plot_solver(solver: str):
    points = load_points(solver)
    anchors = load_anchors(solver)
    anchor_xy = np.asarray([anchors[a] for a in ANCHORS], dtype=float)
    hull = ConvexHull(anchor_xy)
    hull_xy = anchor_xy[hull.vertices]
    if polygon_signed_area(hull_xy) < 0:
        hull_xy = hull_xy[::-1]
    poly = MplPath(hull_xy)

    xmin, ymin = hull_xy.min(axis=0)
    xmax, ymax = hull_xy.max(axis=0)
    pad = 200.0
    gx = np.linspace(xmin - pad, xmax + pad, GRID_N)
    gy = np.linspace(ymin - pad, ymax + pad, GRID_N)
    XI, YI = np.meshgrid(gx, gy)
    grid = np.column_stack([XI.ravel(), YI.ravel()])
    inside = poly.contains_points(grid).reshape(XI.shape)
    edge_dist = polygon_distance_to_edges(grid, hull_xy).reshape(XI.shape)
    ZI = idw_grid(points, XI, YI)

    # Pure data-driven policy inside the UWB area:
    #   1. stay inside the 8-anchor hull;
    #   2. keep grid cells whose W05-estimated residual is below the quality threshold;
    #   3. take the largest connected region and smooth tiny holes.
    # No artificial geometric shrink is applied here.
    mask = inside & (edge_dist >= EDGE_MARGIN_MM) & (ZI <= QUALITY_THRESH_MM)
    mask = binary_opening(mask, structure=np.ones((2, 2)))
    mask = binary_closing(mask, structure=np.ones((9, 9)))
    region = largest_component(mask)

    cell_area_m2 = abs((gx[1] - gx[0]) * (gy[1] - gy[0])) / 1e6
    area_m2 = float(region.sum() * cell_area_m2)
    if region.any():
        med_err = float(np.median(ZI[region]))
        p90_err = float(np.percentile(ZI[region], 90))
    else:
        med_err = float("nan")
        p90_err = float("nan")

    outdir = BASE / f"{solver}_solver"
    fig, ax = plt.subplots(figsize=(9.5, 7.2))
    im = ax.imshow(
        ZI, origin="lower", extent=[gx[0], gx[-1], gy[0], gy[-1]],
        aspect="auto", cmap="magma_r", vmin=0, vmax=float(np.nanpercentile(ZI, 92)),
    )
    ax.plot(*np.vstack([hull_xy, hull_xy[0]]).T, color="white", lw=1.2, alpha=0.9, label="8-anchor hull")
    if region.any():
        ax.contour(XI, YI, region.astype(float), levels=[0.5], colors="red", linewidths=4.0)
    for a, xy in anchors.items():
        ax.scatter(xy[0], xy[1], marker="^", s=72, c="white", edgecolors="black", linewidths=0.8, zorder=5)
        ax.text(xy[0], xy[1], f" {a}", fontsize=9, color="black", weight="bold", zorder=6)
    ax.set_title(f"W05 {solver}: pure data-driven activity region inside 8 anchors")
    ax.set_xlabel("X mm")
    ax.set_ylabel("Y mm")
    ax.legend(loc="upper right")
    cbar = fig.colorbar(im, ax=ax, shrink=0.92)
    cbar.set_label("estimated median abs residual (mm)")
    fig.savefig(outdir / f"fig_w05_{solver.lower()}_data_driven_activity_region_xy.png", dpi=300, bbox_inches="tight")
    plt.close(fig)

    return {
        "solver": solver,
        "policy": "pure data-driven inside 8-anchor hull",
        "quality_thresh_mm": QUALITY_THRESH_MM,
        "edge_margin_mm": EDGE_MARGIN_MM,
        "area_m2": area_m2,
        "median_estimated_residual_mm": med_err,
        "p90_estimated_residual_mm": p90_err,
        "grid_cells": int(region.sum()),
    }


def main():
    rows = [plot_solver("V3"), plot_solver("V4")]
    write_csv(BASE / "w05_data_driven_activity_region_summary.csv", rows)
    print("Wrote data-driven activity region figures and summary")


if __name__ == "__main__":
    main()
