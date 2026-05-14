#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main() -> int:
    ap = argparse.ArgumentParser(description="Plot anchor layout JSON to PNG.")
    ap.add_argument("--layout-json", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--title", default=None)
    ap.add_argument(
        "--pair-mode",
        choices=("box", "none"),
        default="box",
        help="Draw A-E/B-F/C-G/D-H pair lines in box mode.",
    )
    args = ap.parse_args()

    layout_path = Path(args.layout_json)
    out = Path(args.output)
    raw = json.loads(layout_path.read_text(encoding="utf-8"))
    anchors = raw["anchors"]
    pts = {a["label"]: (float(a["x_mm"]), float(a["y_mm"]), float(a["z_mm"])) for a in anchors}

    fig = plt.figure(figsize=(14, 5))
    title = args.title or layout_path.stem

    ax1 = fig.add_subplot(1, 3, 1)
    ax2 = fig.add_subplot(1, 3, 2)
    ax3 = fig.add_subplot(1, 3, 3, projection="3d")

    def color(label: str) -> str:
        return "#1f77b4" if label in "ABCD" else "#d62728"

    for label, (x, y, z) in pts.items():
        c = color(label)
        ax1.scatter(x, y, c=c, s=80)
        ax1.text(x + 40, y + 40, label, fontsize=10)

        ax2.scatter(x, z, c=c, s=80)
        ax2.text(x + 40, z + 20, label, fontsize=10)

        ax3.scatter([x], [y], [z], c=c, s=80)
        ax3.text(x, y, z, label, size=9)

    if args.pair_mode == "box":
        for lo, up in [("A", "E"), ("B", "F"), ("C", "G"), ("D", "H")]:
            x1, y1, z1 = pts[lo]
            x2, y2, z2 = pts[up]
            ax1.plot([x1, x2], [y1, y2], "--", alpha=0.4, color="gray")
            ax2.plot([x1, x2], [z1, z2], "--", alpha=0.4, color="gray")
            ax3.plot([x1, x2], [y1, y2], [z1, z2], "--", alpha=0.4, color="gray")

    for cyc in [("A", "B", "C", "D", "A"), ("E", "F", "G", "H", "E")]:
        xs, ys, zs = [], [], []
        for k in cyc:
            x, y, z = pts[k]
            xs.append(x)
            ys.append(y)
            zs.append(z)
        ax3.plot(xs, ys, zs, alpha=0.45)

    ax1.set_title("Top View (XY)")
    ax1.set_xlabel("X (mm)")
    ax1.set_ylabel("Y (mm)")
    ax1.axis("equal")
    ax1.grid(True, alpha=0.3)

    ax2.set_title("Side View (XZ)")
    ax2.set_xlabel("X (mm)")
    ax2.set_ylabel("Z (mm)")
    ax2.grid(True, alpha=0.3)

    ax3.set_title("3D View")
    ax3.set_xlabel("X (mm)")
    ax3.set_ylabel("Y (mm)")
    ax3.set_zlabel("Z (mm)")
    ax3.view_init(elev=22, azim=-58)

    fig.suptitle(title)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=180, bbox_inches="tight")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
