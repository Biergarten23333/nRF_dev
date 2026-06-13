#!/usr/bin/env python3
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fig" / "paper_table_error_offsets.png"

ROWS = [
    ("A", None, None, None, None, None, None),
    ("B", 54.94, 9.79, None, None, None, None),
    ("C", 2.23, 0.00, -2.48, 3.63, None, None),
    ("D", 66.36, 8.87, 21.51, 4.47, 99.11, 4.13),
    ("E", -58.83, 3.44, 22.06, 4.97, None, None),
    ("F", 133.68, 5.23, -80.94, 4.68, 25.51, 4.65),
    ("G", 11.75, 8.76, -7.05, 4.38, 119.52, 2.46),
    ("H", 31.14, 5.52, -110.87, 4.62, 35.18, 3.33),
]

AXES = {
    "x": {"mean_i": 1, "std_i": 2, "color": "#2B6CB0", "label": "x error"},
    "y": {"mean_i": 3, "std_i": 4, "color": "#C2410C", "label": "y error"},
    "z": {"mean_i": 5, "std_i": 6, "color": "#2F855A", "label": "z error"},
}


def complete_rows():
    for row in ROWS:
        if row[1] is not None and row[3] is not None and row[5] is not None:
            yield row


def draw_3d(ax):
    rows = list(complete_rows())
    coords = np.array([(r[1], r[3], r[5]) for r in rows], dtype=float)
    labels = [r[0] for r in rows]
    colors = ["#4C78A8", "#F58518", "#54A24B", "#B279A2"]

    ax.scatter([0], [0], [0], marker="x", s=55, color="#222222", linewidths=1.4)
    ax.text(0, 0, 0, " origin", color="#222222", fontsize=9)

    for row, color in zip(rows, colors):
        label, x, sx, y, sy, z, sz = row
        ax.scatter([x], [y], [z], s=54, color=color, edgecolor="white", linewidth=0.7)
        ax.text(x + 4, y + 4, z + 3, label, color=color, fontsize=10, weight="bold")
        ax.plot([x - sx, x + sx], [y, y], [z, z], color=AXES["x"]["color"], lw=1.8, alpha=0.85)
        ax.plot([x, x], [y - sy, y + sy], [z, z], color=AXES["y"]["color"], lw=1.8, alpha=0.85)
        ax.plot([x, x], [y, y], [z - sz, z + sz], color=AXES["z"]["color"], lw=1.8, alpha=0.85)

    span = np.array(
        [
            coords[:, 0].min() - 25,
            coords[:, 0].max() + 25,
            coords[:, 1].min() - 25,
            coords[:, 1].max() + 25,
            0,
            coords[:, 2].max() + 25,
        ]
    )
    ax.set_xlim(span[0], span[1])
    ax.set_ylim(span[2], span[3])
    ax.set_zlim(span[4], span[5])
    ax.set_box_aspect((span[1] - span[0], span[3] - span[2], span[5] - span[4]))
    ax.set_xlabel("x error (mm)", labelpad=8)
    ax.set_ylabel("y error (mm)", labelpad=8)
    ax.set_zlabel("z error (mm)", labelpad=8)
    ax.set_title("3D offset vectors: complete rows only", pad=14)
    ax.view_init(elev=23, azim=-52)
    ax.grid(True, alpha=0.28)


def draw_axis_panel(ax, axis_key, show_xlabel=False):
    spec = AXES[axis_key]
    ids = [r[0] for r in ROWS]
    y_pos = np.arange(len(ROWS))[::-1]
    means = np.array([r[spec["mean_i"]] if r[spec["mean_i"]] is not None else np.nan for r in ROWS], dtype=float)
    stds = np.array([r[spec["std_i"]] if r[spec["std_i"]] is not None else np.nan for r in ROWS], dtype=float)
    valid = ~np.isnan(means)

    ax.axvline(0, color="#111111", lw=0.9, alpha=0.65)
    ax.errorbar(
        means[valid],
        y_pos[valid],
        xerr=stds[valid],
        fmt="o",
        color=spec["color"],
        ecolor=spec["color"],
        elinewidth=1.5,
        capsize=3,
        markersize=5.5,
    )
    for y, ok in zip(y_pos, valid):
        if not ok:
            ax.text(-126, y, "*", color="#9CA3AF", ha="left", va="center", fontsize=11)

    ax.set_yticks(y_pos)
    ax.set_yticklabels(ids)
    ax.set_xlim(-130, 150)
    ax.set_ylim(-0.7, len(ROWS) - 0.3)
    ax.set_title(f"{spec['label']} (mean +/- SD)", loc="left", fontsize=10, pad=4)
    ax.grid(True, axis="x", color="#E5E7EB", linewidth=0.8)
    ax.grid(True, axis="y", color="#F3F4F6", linewidth=0.6)
    ax.tick_params(axis="both", labelsize=9)
    if show_xlabel:
        ax.set_xlabel("signed error (mm)")
    else:
        ax.tick_params(axis="x", labelbottom=False)


def main():
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.edgecolor": "#D1D5DB",
            "axes.labelcolor": "#111827",
            "xtick.color": "#374151",
            "ytick.color": "#374151",
            "figure.facecolor": "white",
        }
    )

    fig = plt.figure(figsize=(12.8, 7.6), dpi=180)
    grid = fig.add_gridspec(3, 2, width_ratios=[1.18, 1.0], wspace=0.24, hspace=0.34)

    ax3d = fig.add_subplot(grid[:, 0], projection="3d")
    draw_3d(ax3d)

    draw_axis_panel(fig.add_subplot(grid[0, 1]), "x")
    draw_axis_panel(fig.add_subplot(grid[1, 1]), "y")
    draw_axis_panel(fig.add_subplot(grid[2, 1]), "z", show_xlabel=True)

    fig.suptitle("Coordinate error table visualized", x=0.055, y=0.982, ha="left", fontsize=15, weight="bold")
    fig.text(
        0.055,
        0.944,
        "Values are in millimetres; error bars show the +/- term in the table. Asterisks are missing entries.",
        ha="left",
        fontsize=9.5,
        color="#4B5563",
    )
    fig.savefig(OUT, bbox_inches="tight")
    print(OUT)


if __name__ == "__main__":
    main()
