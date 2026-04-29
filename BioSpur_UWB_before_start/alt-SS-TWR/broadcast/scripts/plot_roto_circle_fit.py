#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def load_result(path: Path, tag: str) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"))
    res = obj["results"][tag]
    if "samples_csv" not in res or "circle_fit_3d" not in res:
        raise SystemExit(f"[error] tag {tag} missing fitted 3D circle data in {path}")
    return res


def build_plane_basis(points_xyz_mm: np.ndarray):
    center = np.mean(points_xyz_mm, axis=0)
    centered = points_xyz_mm - center[None, :]
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    u = vh[0]
    v = vh[1]
    n = vh[2]
    return center, u, v, n


def project(points_xyz_mm: np.ndarray, origin_xyz_mm: np.ndarray, basis_u: np.ndarray, basis_v: np.ndarray):
    rel = points_xyz_mm - origin_xyz_mm[None, :]
    return np.stack([rel @ basis_u, rel @ basis_v], axis=1)


def main() -> int:
    ap = argparse.ArgumentParser(description="Plot projected 3D roto-circle fits from analyze_recv_tdma_session.py output.")
    ap.add_argument("--analysis-json", required=True)
    ap.add_argument("--tag", action="append", required=True, help="Tag name, repeatable")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    n = len(args.tag)
    fig, axes = plt.subplots(1, n, figsize=(7 * n, 6), squeeze=False)

    for ax, tag in zip(axes[0], args.tag):
        res = load_result(Path(args.analysis_json), tag)
        samples = np.asarray([[p["x_mm"], p["y_mm"], p["z_mm"]] for p in res["samples_csv"]], dtype=float)
        fit = res["circle_fit_3d"]

        origin, basis_u, basis_v, _ = build_plane_basis(samples)
        proj = project(samples, origin, basis_u, basis_v)
        center3 = np.array([fit["center_x_mm"], fit["center_y_mm"], fit["center_z_mm"]], dtype=float)
        center_uv = project(center3[None, :], origin, basis_u, basis_v)[0]
        radius = float(fit["radius_mm"])

        theta = np.linspace(0.0, 2.0 * np.pi, 720)
        circle_uv = np.stack(
            [
                center_uv[0] + radius * np.cos(theta),
                center_uv[1] + radius * np.sin(theta),
            ],
            axis=1,
        )

        ax.scatter(proj[:, 0], proj[:, 1], s=8, alpha=0.35, label="projected points")
        ax.plot(circle_uv[:, 0], circle_uv[:, 1], linewidth=2.0, label="fitted circle")
        ax.scatter([center_uv[0]], [center_uv[1]], s=50, marker="x", label="circle center")
        ax.set_aspect("equal", adjustable="box")
        ax.grid(True, alpha=0.25)
        ax.set_xlabel("plane u (mm)")
        ax.set_ylabel("plane v (mm)")
        ax.set_title(
            f"{tag}\nR={fit['radius_mm']:.1f} mm  plane_rms={fit['plane_rms_mm']:.1f} mm\n"
            f"radial_rms={fit['radial_rms_mm']:.1f} mm  n={len(samples)}"
        )
        ax.legend(loc="best")

    fig.tight_layout()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=180)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
