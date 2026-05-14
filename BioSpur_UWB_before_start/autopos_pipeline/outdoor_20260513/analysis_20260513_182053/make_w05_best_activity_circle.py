#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.path import Path as MplPath
from matplotlib.patches import Circle
import numpy as np
from scipy.spatial import ConvexHull, cKDTree


ANALYSIS = Path(__file__).resolve().parent
BASE = ANALYSIS / "figures" / "w05_dynamic_probe" / "residual_maps"
ANCHORS = list("ABCDEFGH")
USABLE_MM = 60.0


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


def idw_grid(points, xi, yi, k=18, power=2.0):
    pts = np.array([[p["x"], p["y"]] for p in points], dtype=float)
    vals = np.array([p["err"] for p in points], dtype=float)
    tree = cKDTree(pts)
    grid_pts = np.column_stack([xi.ravel(), yi.ravel()])
    dist, idx = tree.query(grid_pts, k=min(k, len(points)))
    dist = np.maximum(dist, 1e-6)
    weights = 1.0 / (dist ** power)
    out = np.sum(weights * vals[idx], axis=1) / np.sum(weights, axis=1)
    return out.reshape(xi.shape)


def polygon_distance_to_edges(points_xy, hull_xy):
    # Minimum distance from each point to each hull edge.
    out = np.full(len(points_xy), np.inf)
    for i in range(len(hull_xy)):
        a = hull_xy[i]
        b = hull_xy[(i + 1) % len(hull_xy)]
        ab = b - a
        denom = float(np.dot(ab, ab))
        ap = points_xy - a
        t = np.clip((ap @ ab) / denom, 0.0, 1.0)
        closest = a + t[:, None] * ab
        d = np.linalg.norm(points_xy - closest, axis=1)
        out = np.minimum(out, d)
    return out


def find_best_circle(points, anchors):
    anchor_xy = np.array([anchors[a] for a in ANCHORS], dtype=float)
    hull = ConvexHull(anchor_xy)
    hull_xy = anchor_xy[hull.vertices]
    poly = MplPath(hull_xy)

    xmin, ymin = hull_xy.min(axis=0)
    xmax, ymax = hull_xy.max(axis=0)
    nx = ny = 240
    gx = np.linspace(xmin, xmax, nx)
    gy = np.linspace(ymin, ymax, ny)
    XI, YI = np.meshgrid(gx, gy)
    ZI = idw_grid(points, XI, YI)
    grid = np.column_stack([XI.ravel(), YI.ravel()])
    inside = poly.contains_points(grid)
    good = (ZI.ravel() <= USABLE_MM) & inside
    if not np.any(good):
        # Fallback: choose minimum-error location inside hull.
        masked = np.where(inside, ZI.ravel(), np.inf)
        idx = int(np.argmin(masked))
        return grid[idx], 0.0, ZI, gx, gy, hull_xy

    good_pts = grid[good]
    bad_or_out = grid[~good]
    bad_tree = cKDTree(bad_or_out)
    dist_to_bad, _ = bad_tree.query(good_pts, k=1)
    dist_to_edge = polygon_distance_to_edges(good_pts, hull_xy)
    radii = np.minimum(dist_to_bad, dist_to_edge)

    # Prefer a large clean region, but if tied choose lower center error.
    good_err = ZI.ravel()[good]
    score = radii - 0.8 * np.maximum(0, good_err - np.nanmin(good_err))
    idx = int(np.argmax(score))
    return good_pts[idx], float(radii[idx]), ZI, gx, gy, hull_xy


def plot(solver: str):
    points = load_points(solver)
    anchors = load_anchors(solver)
    center, radius, ZI, gx, gy, hull_xy = find_best_circle(points, anchors)
    outdir = BASE / f"{solver}_solver"

    fig, ax = plt.subplots(figsize=(9.2, 7.2))
    im = ax.imshow(
        ZI,
        origin="lower",
        extent=[gx[0], gx[-1], gy[0], gy[-1]],
        aspect="auto",
        cmap="magma_r",
        vmin=0,
        vmax=float(np.nanpercentile(ZI, 92)),
    )
    ax.plot(*np.vstack([hull_xy, hull_xy[0]]).T, color="white", lw=1.2, alpha=0.8, label="8-anchor hull")
    for a, xy in anchors.items():
        ax.scatter(xy[0], xy[1], marker="^", s=70, c="white", edgecolors="black", linewidths=0.8, zorder=5)
        ax.text(xy[0], xy[1], f" {a}", fontsize=9, color="black", weight="bold", zorder=6)
    circ = Circle(center, radius, fill=False, edgecolor="red", linewidth=4.0, zorder=7)
    ax.add_patch(circ)
    ax.scatter([center[0]], [center[1]], marker="+", c="red", s=120, linewidths=2.5, zorder=8)
    ax.set_title(f"W05 {solver}: recommended activity circle inside 8-anchor area")
    ax.set_xlabel("X mm")
    ax.set_ylabel("Y mm")
    ax.legend(loc="upper right")
    cbar = fig.colorbar(im, ax=ax, shrink=0.92)
    cbar.set_label("estimated median abs residual (mm)")
    fig.savefig(outdir / f"fig_w05_{solver.lower()}_recommended_activity_circle_xy.png", dpi=300, bbox_inches="tight")
    plt.close(fig)

    row = {
        "solver": solver,
        "criterion": f"largest approximate circle inside 8-anchor hull with estimated median abs residual <= {USABLE_MM:.0f} mm",
        "center_x_mm": float(center[0]),
        "center_y_mm": float(center[1]),
        "radius_mm": float(radius),
        "diameter_mm": float(radius * 2),
        "area_m2": float(math.pi * radius * radius / 1e6),
    }
    return row


def main():
    rows = [plot("V3"), plot("V4")]
    write_csv(BASE / "w05_recommended_activity_circle_xy.csv", rows)
    print(f"Wrote recommended activity circle figures and {BASE / 'w05_recommended_activity_circle_xy.csv'}")


if __name__ == "__main__":
    main()
