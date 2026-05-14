#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.spatial import cKDTree


ANALYSIS = Path(__file__).resolve().parent
BASE = ANALYSIS / "figures" / "w05_dynamic_probe" / "residual_maps"
ANCHORS = list("ABCDEFGH")
USABLE_MM = 60.0
MIN_ANCHORS = 7


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def load_json(path: Path):
    return json.loads(path.read_text())


def load_points(path: Path):
    rows = read_csv(path)
    pts = []
    for r in rows:
        pts.append({
            "x": float(r["x_mm"]),
            "y": float(r["y_mm"]),
            "z": float(r["z_mm"]),
            "err": float(r["median_abs_residual_mm"]),
            "usable": 1.0 if int(float(r["used_anchors"])) >= MIN_ANCHORS and float(r["median_abs_residual_mm"]) <= USABLE_MM else 0.0,
        })
    return pts


def load_layout(solver: str):
    name = "v3_lite_layout.json" if solver == "V3" else "v4_io_layout.json"
    layout = load_json(ANALYSIS / "solves" / name)["anchors"]
    zobj = load_json(ANALYSIS / "physical_z_transform_20260513.json")
    z_sign = float(zobj["z_sign"])
    z_offset = float(zobj["z_offset_mm"])
    out = {}
    for a in ANCHORS:
        x, y, z = [float(v) for v in layout[a]]
        out[a] = np.array([x, y, z_sign * z + z_offset], dtype=float)
    return out


def idw_grid(x, y, values, xi, yi, k=18, power=2.0):
    pts = np.column_stack([x, y])
    tree = cKDTree(pts)
    grid_pts = np.column_stack([xi.ravel(), yi.ravel()])
    k = min(k, len(pts))
    dist, idx = tree.query(grid_pts, k=k)
    if k == 1:
        dist = dist[:, None]
        idx = idx[:, None]
    dist = np.maximum(dist, 1e-6)
    w = 1.0 / (dist ** power)
    vals = np.asarray(values, dtype=float)[idx]
    out = np.sum(w * vals, axis=1) / np.sum(w, axis=1)
    return out.reshape(xi.shape)


def bounds_for(points, anchors, axes):
    vals0 = [p[axes[0]] for p in points] + [anchors[a][{"x": 0, "y": 1, "z": 2}[axes[0]]] for a in ANCHORS]
    vals1 = [p[axes[1]] for p in points] + [anchors[a][{"x": 0, "y": 1, "z": 2}[axes[1]]] for a in ANCHORS]
    lo0, hi0 = min(vals0), max(vals0)
    lo1, hi1 = min(vals1), max(vals1)
    pad0 = max(100.0, 0.07 * (hi0 - lo0))
    pad1 = max(100.0, 0.07 * (hi1 - lo1))
    return (lo0 - pad0, hi0 + pad0), (lo1 - pad1, hi1 + pad1)


def plot_one(points, anchors, solver, axes, value_key, out_name, title, cmap, vmin, vmax, cbar_label):
    labels = {"x": "X mm", "y": "Y mm", "z": "Z mm"}
    idx = {"x": 0, "y": 1, "z": 2}
    (xlo, xhi), (ylo, yhi) = bounds_for(points, anchors, axes)
    gx = np.linspace(xlo, xhi, 260)
    gy = np.linspace(ylo, yhi, 260)
    XI, YI = np.meshgrid(gx, gy)
    xs = np.asarray([p[axes[0]] for p in points], dtype=float)
    ys = np.asarray([p[axes[1]] for p in points], dtype=float)
    vals = np.asarray([p[value_key] for p in points], dtype=float)
    ZI = idw_grid(xs, ys, vals, XI, YI)

    fig, ax = plt.subplots(figsize=(9.5, 7.2))
    im = ax.imshow(ZI, origin="lower", extent=[xlo, xhi, ylo, yhi], aspect="auto",
                   cmap=cmap, vmin=vmin, vmax=vmax)
    ax.contour(XI, YI, ZI, levels=7, colors="white", alpha=0.35, linewidths=0.7)
    if value_key == "err":
        cs = ax.contour(XI, YI, ZI, levels=[USABLE_MM], colors="red", linewidths=2.6)
        ax.clabel(cs, inline=True, fontsize=9, fmt={USABLE_MM: f"best usable <= {USABLE_MM:.0f} mm"})
    else:
        cs = ax.contour(XI, YI, ZI, levels=[0.5], colors="red", linewidths=2.6)
        ax.clabel(cs, inline=True, fontsize=9, fmt={0.5: "best usable boundary"})
    # Sample coverage hint: tiny black dots; continuous field is the IDW estimate.
    ax.scatter(xs, ys, s=2, c="black", alpha=0.07, linewidths=0)
    for a, pos in anchors.items():
        ax.scatter(pos[idx[axes[0]]], pos[idx[axes[1]]], marker="^", s=62,
                   c="white", edgecolors="black", linewidths=0.8, zorder=5)
        ax.text(pos[idx[axes[0]]], pos[idx[axes[1]]], f" {a}", fontsize=9,
                color="black", weight="bold", zorder=6)
    ax.set_xlabel(labels[axes[0]])
    ax.set_ylabel(labels[axes[1]])
    ax.set_title(title)
    ax.grid(False)
    cbar = fig.colorbar(im, ax=ax, shrink=0.92)
    cbar.set_label(cbar_label)
    fig.savefig(BASE / f"{solver}_solver" / out_name, dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_solver(solver: str):
    solver_dir = BASE / f"{solver}_solver"
    points = load_points(solver_dir / f"w05_{solver.lower()}_usable_area_points.csv")
    anchors = load_layout(solver)
    errs = np.asarray([p["err"] for p in points], dtype=float)
    err_vmax = float(np.percentile(errs, 92))
    for axes, name in [(("x", "y"), "xy"), (("x", "z"), "xz"), (("y", "z"), "yz")]:
        plot_one(
            points, anchors, solver, axes, "err",
            f"fig_w05_{solver.lower()}_estimated_full_area_residual_{name}.png",
            f"W05 {solver}: estimated full-area residual quality ({name.upper()})",
            "magma_r", 0.0, err_vmax,
            "estimated median abs residual (mm)",
        )
        plot_one(
            points, anchors, solver, axes, "usable",
            f"fig_w05_{solver.lower()}_estimated_full_area_usable_probability_{name}.png",
            f"W05 {solver}: estimated usable area <= {USABLE_MM:.0f} mm ({name.upper()})",
            "viridis", 0.0, 1.0,
            "estimated usable probability",
        )


def main():
    for solver in ["V3", "V4"]:
        plot_solver(solver)
    print(f"Wrote continuous full-area W05 heatmaps under {BASE}/V3_solver and {BASE}/V4_solver")


if __name__ == "__main__":
    main()
