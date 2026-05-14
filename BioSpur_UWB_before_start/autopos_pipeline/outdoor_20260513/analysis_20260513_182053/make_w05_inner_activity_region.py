#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.path import Path as MplPath
from matplotlib.patches import Polygon
import numpy as np
from scipy.spatial import ConvexHull, cKDTree


ANALYSIS = Path(__file__).resolve().parent
BASE = ANALYSIS / "figures" / "w05_dynamic_probe" / "residual_maps"
ANCHORS = list("ABCDEFGH")
MARGIN_MM = 450.0


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


def polygon_signed_area(poly):
    x = poly[:, 0]
    y = poly[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - y * np.roll(x, -1)))


def inward_offset_convex_polygon(poly, margin):
    # poly must be convex and ordered. Offset each edge inward by `margin`,
    # then intersect adjacent offset lines.
    if polygon_signed_area(poly) < 0:
        poly = poly[::-1]
    centroid = poly.mean(axis=0)
    lines = []
    n = len(poly)
    for i in range(n):
        p = poly[i]
        q = poly[(i + 1) % n]
        edge = q - p
        normal_left = np.array([-edge[1], edge[0]], dtype=float)
        normal_left /= np.linalg.norm(normal_left)
        mid = (p + q) * 0.5
        # For CCW polygon, interior is left side. Ensure normal points inward.
        if np.dot(centroid - mid, normal_left) < 0:
            normal_left = -normal_left
        p_off = p + margin * normal_left
        lines.append((p_off, edge))
    out = []
    for i in range(n):
        p1, d1 = lines[i - 1]
        p2, d2 = lines[i]
        A = np.column_stack([d1, -d2])
        b = p2 - p1
        try:
            t, _ = np.linalg.solve(A, b)
            out.append(p1 + t * d1)
        except np.linalg.LinAlgError:
            pass
    return np.asarray(out, dtype=float)


def polygon_area(poly):
    return abs(polygon_signed_area(poly))


def plot_solver(solver: str):
    points = load_points(solver)
    anchors = load_anchors(solver)
    anchor_xy = np.asarray([anchors[a] for a in ANCHORS], dtype=float)
    hull = ConvexHull(anchor_xy)
    hull_poly = anchor_xy[hull.vertices]
    inner = inward_offset_convex_polygon(hull_poly, MARGIN_MM)

    xmin, ymin = hull_poly.min(axis=0)
    xmax, ymax = hull_poly.max(axis=0)
    pad = 250.0
    gx = np.linspace(xmin - pad, xmax + pad, 280)
    gy = np.linspace(ymin - pad, ymax + pad, 280)
    XI, YI = np.meshgrid(gx, gy)
    ZI = idw_grid(points, XI, YI)

    fig, ax = plt.subplots(figsize=(9.5, 7.2))
    im = ax.imshow(
        ZI, origin="lower", extent=[gx[0], gx[-1], gy[0], gy[-1]],
        aspect="auto", cmap="magma_r", vmin=0, vmax=float(np.nanpercentile(ZI, 92)),
    )
    ax.plot(*np.vstack([hull_poly, hull_poly[0]]).T, color="white", lw=1.3, alpha=0.9, label="8-anchor outer hull")
    ax.add_patch(Polygon(inner, closed=True, fill=False, edgecolor="red", linewidth=4.0, label=f"recommended inner activity region ({MARGIN_MM:.0f} mm margin)"))
    for a, xy in anchors.items():
        ax.scatter(xy[0], xy[1], marker="^", s=72, c="white", edgecolors="black", linewidths=0.8, zorder=5)
        ax.text(xy[0], xy[1], f" {a}", fontsize=9, color="black", weight="bold", zorder=6)
    ax.set_title(f"W05 {solver}: recommended activity region inside 8 anchors")
    ax.set_xlabel("X mm")
    ax.set_ylabel("Y mm")
    ax.legend(loc="upper right")
    cbar = fig.colorbar(im, ax=ax, shrink=0.92)
    cbar.set_label("estimated median abs residual (mm)")
    out = BASE / f"{solver}_solver" / f"fig_w05_{solver.lower()}_recommended_inner_activity_region_xy.png"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)

    rows = []
    for i, (x, y) in enumerate(inner):
        rows.append({"solver": solver, "vertex": i, "x_mm": float(x), "y_mm": float(y)})
    return {
        "solver": solver,
        "margin_mm": MARGIN_MM,
        "outer_hull_area_m2": polygon_area(hull_poly) / 1e6,
        "inner_region_area_m2": polygon_area(inner) / 1e6,
        "area_ratio": polygon_area(inner) / max(1e-9, polygon_area(hull_poly)),
    }, rows


def main():
    summary = []
    verts = []
    for solver in ["V3", "V4"]:
        row, vrows = plot_solver(solver)
        summary.append(row)
        verts.extend(vrows)
    write_csv(BASE / "w05_recommended_inner_activity_region_summary.csv", summary)
    write_csv(BASE / "w05_recommended_inner_activity_region_vertices.csv", verts)
    print("Wrote recommended inner activity region figures and CSVs")


if __name__ == "__main__":
    main()
