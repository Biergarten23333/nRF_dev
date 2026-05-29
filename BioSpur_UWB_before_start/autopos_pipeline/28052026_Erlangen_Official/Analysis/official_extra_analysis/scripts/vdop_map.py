#!/usr/bin/env python3
"""Task 3: range-only VDOP/GDOP maps for the official AutoPos layout.

Official dataset convention:
  x_mm, y_mm = horizontal plane
  z_mm       = vertical axis, upper layer is negative z
  reported_height_mm = -z_mm

The default Jacobian is range-only: G_row = [ux, uy, uz].
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


LAYOUT_HORIZONTAL_AXES = ("x_mm", "y_mm")
LAYOUT_VERTICAL_AXIS = "z_mm"
LAYOUT_UPPER_LAYER_SIGN = "negative_z"
REPORTED_HEIGHT_EXPR = "-z_mm"
ANCHOR_LABELS = list("ABCDEFGH")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def append_run_meta(out_dir: Path, entry: dict) -> None:
    meta_path = out_dir / "run_meta.json"
    lock_path = out_dir / "run_meta.json.lock"
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except json.JSONDecodeError:
                meta = {"runs": []}
        else:
            meta = {"runs": []}
        meta.setdefault("runs", []).append(entry)
        tmp = meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
        tmp.replace(meta_path)
        fcntl.flock(lock, fcntl.LOCK_UN)


def load_layout(path: Path) -> tuple[list[str], np.ndarray]:
    data = json.loads(path.read_text())
    anchors = data["anchors"]
    labels = [a.get("label", chr(ord("A") + int(a["id"]))) for a in anchors]
    coords = np.array([[a["x_mm"], a["y_mm"], a["z_mm"]] for a in anchors], dtype=np.float32)
    return labels, coords


def anchor_mask(labels: list[str], mask_name: str) -> list[int]:
    if mask_name == "all8":
        keep = ANCHOR_LABELS
    elif mask_name == "noG":
        keep = [a for a in ANCHOR_LABELS if a != "G"]
    elif mask_name.startswith("drop") and len(mask_name) > 4:
        dropped = list(mask_name[4:])
        bad = [a for a in dropped if a not in ANCHOR_LABELS]
        if bad:
            raise ValueError(f"unknown anchors in mask {mask_name}: {bad}")
        keep = [a for a in ANCHOR_LABELS if a not in set(dropped)]
    else:
        raise ValueError(f"unknown mask {mask_name}")
    return [labels.index(a) for a in keep]


def make_grid(layout: np.ndarray, static_points: np.ndarray | None, grid_mm: float, margin_mm: float) -> tuple[np.ndarray, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    pts = layout if static_points is None or static_points.size == 0 else np.vstack([layout, static_points])
    lo = np.nanmin(pts, axis=0) - margin_mm
    hi = np.nanmax(pts, axis=0) + margin_mm
    xs = np.arange(math.floor(lo[0] / grid_mm) * grid_mm, math.ceil(hi[0] / grid_mm) * grid_mm + 0.5 * grid_mm, grid_mm, dtype=np.float32)
    ys = np.arange(math.floor(lo[1] / grid_mm) * grid_mm, math.ceil(hi[1] / grid_mm) * grid_mm + 0.5 * grid_mm, grid_mm, dtype=np.float32)
    zs = np.arange(math.floor(lo[2] / grid_mm) * grid_mm, math.ceil(hi[2] / grid_mm) * grid_mm + 0.5 * grid_mm, grid_mm, dtype=np.float32)
    xx, yy, zz = np.meshgrid(xs, ys, zs, indexing="xy")
    grid = np.column_stack([xx.ravel(), yy.ravel(), zz.ravel()]).astype(np.float32)
    return grid, (xs, ys, zs)


def compute_dop_torch(
    grid: np.ndarray,
    anchors: np.ndarray,
    *,
    device_name: str,
    with_range_bias: bool,
    chunk_size: int,
) -> dict[str, np.ndarray]:
    import torch

    device = torch.device(device_name)
    a = torch.as_tensor(anchors, dtype=torch.float32, device=device)
    gdop_out = np.full(grid.shape[0], np.nan, dtype=np.float32)
    hdop_out = np.full(grid.shape[0], np.nan, dtype=np.float32)
    vdop_out = np.full(grid.shape[0], np.nan, dtype=np.float32)
    cond_out = np.full(grid.shape[0], np.nan, dtype=np.float32)
    eps = torch.tensor(1e-6, dtype=torch.float32, device=device)
    eye_dim = 4 if with_range_bias else 3
    eye = torch.eye(eye_dim, dtype=torch.float32, device=device).unsqueeze(0) * 1e-7
    for start in range(0, grid.shape[0], chunk_size):
        stop = min(start + chunk_size, grid.shape[0])
        p = torch.as_tensor(grid[start:stop], dtype=torch.float32, device=device)
        diff = p[:, None, :] - a[None, :, :]
        dist = torch.linalg.norm(diff, dim=2).clamp_min(eps)
        u = diff / dist[:, :, None]
        if with_range_bias:
            ones = torch.ones((u.shape[0], u.shape[1], 1), dtype=torch.float32, device=device)
            g = torch.cat([u, ones], dim=2)
        else:
            g = u
        gram = torch.matmul(g.transpose(1, 2), g)
        try:
            q = torch.linalg.inv(gram + eye)
        except RuntimeError:
            q = torch.linalg.pinv(gram)
        diag = torch.diagonal(q, dim1=1, dim2=2)
        diag = torch.clamp(diag, min=0.0)
        gdop = torch.sqrt(torch.sum(diag[:, :3], dim=1))
        hdop = torch.sqrt(diag[:, 0] + diag[:, 1])
        vdop = torch.sqrt(diag[:, 2])
        try:
            cond = torch.linalg.cond(gram)
        except RuntimeError:
            cond = torch.full_like(gdop, float("nan"))
        gdop_out[start:stop] = gdop.detach().cpu().numpy()
        hdop_out[start:stop] = hdop.detach().cpu().numpy()
        vdop_out[start:stop] = vdop.detach().cpu().numpy()
        cond_out[start:stop] = cond.detach().cpu().numpy()
    return {"gdop": gdop_out, "hdop": hdop_out, "vdop": vdop_out, "cond": cond_out}


def nearest_grid_indices(points: np.ndarray, xs: np.ndarray, ys: np.ndarray, zs: np.ndarray) -> np.ndarray:
    nx, ny, nz = len(xs), len(ys), len(zs)
    ix = np.clip(np.searchsorted(xs, points[:, 0]), 0, nx - 1)
    iy = np.clip(np.searchsorted(ys, points[:, 1]), 0, ny - 1)
    iz = np.clip(np.searchsorted(zs, points[:, 2]), 0, nz - 1)
    for arr, vals, idx in [(xs, points[:, 0], ix), (ys, points[:, 1], iy), (zs, points[:, 2], iz)]:
        left = np.maximum(idx - 1, 0)
        choose_left = np.abs(arr[left] - vals) < np.abs(arr[idx] - vals)
        idx[choose_left] = left[choose_left]
    # meshgrid(indexing="xy") flattened from shape (ny, nx, nz).
    return (iy * nx * nz + ix * nz + iz).astype(int)


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def markdown_table_from_records(rows: list[dict], columns: list[str] | None = None) -> str:
    if not rows:
        return ""
    columns = columns or list(rows[0].keys())
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        vals = []
        for col in columns:
            val = row.get(col, "")
            if isinstance(val, (float, np.floating)):
                vals.append(f"{float(val):.3f}")
            elif isinstance(val, (int, np.integer)):
                vals.append(str(int(val)))
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


def plot_vdop_slices(
    out_path: Path,
    vdop_by_mask: dict[str, np.ndarray],
    axes: tuple[np.ndarray, np.ndarray, np.ndarray],
    anchors: np.ndarray,
    *,
    grid_mm: float,
) -> None:
    xs, ys, zs = axes
    nx, ny, nz = len(xs), len(ys), len(zs)
    z_targets = [np.nanmax(zs), np.nanmedian(zs), np.nanmin(zs)]
    z_indices = [int(np.argmin(np.abs(zs - z))) for z in z_targets]
    masks = list(vdop_by_mask.keys())
    fig, axs = plt.subplots(len(masks), len(z_indices), figsize=(5.2 * len(z_indices), 4.4 * len(masks)), squeeze=False)
    finite_vals = np.concatenate([v[np.isfinite(v)] for v in vdop_by_mask.values()])
    vmax = float(np.nanpercentile(finite_vals, 95)) if finite_vals.size else 5.0
    vmax = max(2.0, min(vmax, 20.0))
    for r, mask in enumerate(masks):
        cube = vdop_by_mask[mask].reshape(ny, nx, nz)
        for c, zi in enumerate(z_indices):
            ax = axs[r, c]
            im = ax.imshow(
                cube[:, :, zi],
                origin="lower",
                extent=[xs[0], xs[-1], ys[0], ys[-1]],
                aspect="equal",
                vmin=0.0,
                vmax=vmax,
                cmap="viridis",
            )
            ax.scatter(anchors[:, 0], anchors[:, 1], c="white", edgecolors="black", s=40)
            ax.set_title(f"{mask} VDOP, z={zs[zi]:.0f} mm, h={-zs[zi]:.0f} mm")
            ax.set_xlabel("layout x mm")
            ax.set_ylabel("layout y mm")
            fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.suptitle(f"Range-only VDOP slices, grid {grid_mm:.0f} mm")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_mid_4panel(out_path: Path, dop: dict[str, np.ndarray], axes: tuple[np.ndarray, np.ndarray, np.ndarray], *, mask: str, grid_mm: float) -> None:
    xs, ys, zs = axes
    nx, ny, nz = len(xs), len(ys), len(zs)
    zi = int(np.argmin(np.abs(zs - np.nanmedian(zs))))
    metrics = ["gdop", "hdop", "vdop", "cond"]
    titles = ["GDOP", "HDOP", "VDOP", "cond(GTG)"]
    fig, axs = plt.subplots(2, 2, figsize=(11, 9), squeeze=False)
    for ax, metric, title in zip(axs.ravel(), metrics, titles):
        cube = dop[metric].reshape(ny, nx, nz)
        vals = cube[:, :, zi]
        finite = vals[np.isfinite(vals)]
        vmax = float(np.nanpercentile(finite, 95)) if finite.size else 5.0
        if metric == "cond":
            vmax = min(vmax, 200.0)
        else:
            vmax = max(2.0, min(vmax, 20.0))
        im = ax.imshow(vals, origin="lower", extent=[xs[0], xs[-1], ys[0], ys[-1]], aspect="equal", vmin=0.0, vmax=vmax, cmap="magma")
        ax.set_title(f"{title}, z={zs[zi]:.0f} mm")
        ax.set_xlabel("layout x mm")
        ax.set_ylabel("layout y mm")
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.suptitle(f"{mask} DOP diagnostics, grid {grid_mm:.0f} mm")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-root", default="autopos_pipeline/28052026_Erlangen_Official")
    parser.add_argument("--layout-json", default=None)
    parser.add_argument("--static-table", default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--grid-mm", type=float, default=100.0)
    parser.add_argument("--margin-mm", type=float, default=500.0)
    parser.add_argument("--masks", default="all8,noG,dropH")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--with-range-bias", action="store_true")
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--tag", default="")
    parser.add_argument("--chunk-size", type=int, default=200000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    official_root = Path(args.official_root).resolve()
    layout_json = Path(args.layout_json).resolve() if args.layout_json else official_root / "solver/outputs/v1_to_v4_io_field_check/v4-io/layout.json"
    static_table = Path(args.static_table).resolve() if args.static_table else official_root / "solver/outputs/v1_to_v4_io_field_check/tables/static_all_captures.csv"
    out_dir = Path(args.out_dir).resolve() if args.out_dir else official_root / "Analysis/official_extra_analysis"
    tables_dir = out_dir / "tables"
    figs_dir = out_dir / "figs"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figs_dir.mkdir(parents=True, exist_ok=True)

    labels, layout = load_layout(layout_json)
    static_points = np.empty((0, 3), dtype=np.float32)
    static_df = None
    if static_table.exists():
        static_df = pd.read_csv(static_table)
        if "version" in static_df.columns:
            static_df = static_df[static_df["version"] == "v4-io"].copy()
        static_points = static_df[["mean_x", "mean_y", "mean_z"]].to_numpy(dtype=np.float32)

    grid, axes = make_grid(layout, static_points, args.grid_mm, args.margin_mm)
    masks = [m.strip() for m in args.masks.split(",") if m.strip()]

    import torch

    if args.device == "cuda":
        device_name = "cuda"
    elif args.device == "cpu":
        device_name = "cpu"
    else:
        device_name = "cuda" if torch.cuda.is_available() else "cpu"
    if device_name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but torch.cuda.is_available() is false")

    summary_rows = []
    sample_rows = []
    vdop_for_plot = {}
    all_dop_by_mask = {}
    for mask in masks:
        idx = anchor_mask(labels, mask)
        mask_anchors = layout[idx]
        dop = compute_dop_torch(
            grid,
            mask_anchors,
            device_name=device_name,
            with_range_bias=args.with_range_bias,
            chunk_size=args.chunk_size,
        )
        all_dop_by_mask[mask] = dop
        vdop_for_plot[mask] = dop["vdop"]
        finite = np.isfinite(dop["vdop"])
        summary_rows.append(
            {
                "grid_mm": args.grid_mm,
                "mask": mask,
                "n_grid_points": int(grid.shape[0]),
                "n_finite": int(np.count_nonzero(finite)),
                "n_nan": int(grid.shape[0] - np.count_nonzero(finite)),
                "vdop_median": float(np.nanmedian(dop["vdop"])),
                "vdop_p90": float(np.nanpercentile(dop["vdop"], 90)),
                "vdop_p95": float(np.nanpercentile(dop["vdop"], 95)),
                "gdop_median": float(np.nanmedian(dop["gdop"])),
                "hdop_median": float(np.nanmedian(dop["hdop"])),
                "cond_p95": float(np.nanpercentile(dop["cond"], 95)),
                "with_range_bias": bool(args.with_range_bias),
                "device": device_name,
            }
        )
        if static_df is not None and len(static_df) > 0:
            nn = nearest_grid_indices(static_points, *axes)
            for row_idx, (_, src_row) in zip(nn, static_df.iterrows()):
                sample_rows.append(
                    {
                        "grid_mm": args.grid_mm,
                        "mask": mask,
                        "version": src_row.get("version", ""),
                        "ID": src_row.get("ID", ""),
                        "location": src_row.get("location", ""),
                        "height": src_row.get("height", ""),
                        "facing": src_row.get("facing", ""),
                        "mean_x": src_row.get("mean_x", np.nan),
                        "mean_y": src_row.get("mean_y", np.nan),
                        "mean_z": src_row.get("mean_z", np.nan),
                        "gdop": float(dop["gdop"][row_idx]),
                        "hdop": float(dop["hdop"][row_idx]),
                        "vdop": float(dop["vdop"][row_idx]),
                        "cond": float(dop["cond"][row_idx]),
                        "radial_p95": src_row.get("radial_p95", np.nan),
                        "pct_ge8": src_row.get("pct_ge8", np.nan),
                    }
                )

    grid_tag = f"grid{int(args.grid_mm)}" + ("_rangebias" if args.with_range_bias else "")
    if args.tag:
        grid_tag += f"_{args.tag}"
    write_csv(tables_dir / f"dop_grid_summary_{grid_tag}.csv", summary_rows)
    write_csv(tables_dir / f"dop_by_facing_group_{grid_tag}.csv", sample_rows)

    if sample_rows:
        sample_df = pd.DataFrame(sample_rows)
        group = (
            sample_df.groupby(["mask", "facing", "height"], dropna=False)
            .agg(
                n=("ID", "count"),
                vdop_median=("vdop", "median"),
                vdop_p95=("vdop", lambda s: float(np.nanpercentile(s, 95))),
                gdop_median=("gdop", "median"),
                radial_p95_median=("radial_p95", "median"),
                pct_ge8_median=("pct_ge8", "median"),
            )
            .reset_index()
        )
        group.to_csv(tables_dir / f"dop_facing_height_summary_{grid_tag}.csv", index=False)
        md = ["# VDOP Summary\n\n", f"Grid: {args.grid_mm:.0f} mm. Default model: range-only Jacobian.\n\n"]
        md.append("## Grid Summary\n\n")
        md.append(markdown_table_from_records(summary_rows))
        md.append("\n\n## Static Facing/Height Samples\n\n")
        md.append(markdown_table_from_records(group.to_dict("records")))
        md.append("\n")
        (tables_dir / f"dop_summary_{grid_tag}.md").write_text("".join(md))

    if not args.no_plots:
        plot_vdop_slices(figs_dir / f"vdop_slices_{grid_tag}.png", vdop_for_plot, axes, layout, grid_mm=args.grid_mm)
    if "all8" in all_dop_by_mask and not args.no_plots:
        plot_mid_4panel(figs_dir / f"dop_4panel_mid_all8_{grid_tag}.png", all_dop_by_mask["all8"], axes, mask="all8", grid_mm=args.grid_mm)

    append_run_meta(
        out_dir,
        {
            "script": "vdop_map.py",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "args": vars(args),
            "seed": args.seed,
            "axis_convention": {
                "layout_horizontal_axes": LAYOUT_HORIZONTAL_AXES,
                "layout_vertical_axis": LAYOUT_VERTICAL_AXIS,
                "layout_upper_layer_sign": LAYOUT_UPPER_LAYER_SIGN,
                "reported_height_mm": REPORTED_HEIGHT_EXPR,
            },
            "layout_json": str(layout_json),
            "layout_sha256": sha256_file(layout_json),
            "static_table": str(static_table),
            "device": device_name,
            "grid_points": int(grid.shape[0]),
            "grid_axes_counts": [len(axes[0]), len(axes[1]), len(axes[2])],
            "masks": masks,
        },
    )

    print(f"[vdop] grid={args.grid_mm:.0f}mm points={grid.shape[0]} device={device_name} masks={','.join(masks)}")
    print(f"[vdop] wrote {tables_dir / f'dop_summary_{grid_tag}.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
