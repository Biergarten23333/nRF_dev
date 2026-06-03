#!/usr/bin/env python3
"""Task 5: pairwise anchor residual heatmaps and raw sweep asymmetry."""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
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
ANCHORS = list("ABCDEFGH")


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


def mad(x: pd.Series) -> float:
    arr = x.to_numpy(dtype=float)
    med = np.nanmedian(arr)
    return float(np.nanmedian(np.abs(arr - med)))


def blank_matrix(fill=np.nan) -> np.ndarray:
    m = np.full((len(ANCHORS), len(ANCHORS)), fill, dtype=float)
    np.fill_diagonal(m, 0.0)
    return m


def plot_heatmap(path: Path, matrix: np.ndarray, title: str, cbar_label: str, *, cmap: str, symmetric: bool) -> None:
    fig, ax = plt.subplots(figsize=(7, 6))
    finite = matrix[np.isfinite(matrix)]
    if finite.size:
        if symmetric:
            vmax = float(np.nanpercentile(np.abs(finite), 95))
            vmin = -vmax
        else:
            vmin = float(np.nanpercentile(finite, 5))
            vmax = float(np.nanpercentile(finite, 95))
            if vmax <= vmin:
                vmax = vmin + 1.0
    else:
        vmin, vmax = -1.0, 1.0
    im = ax.imshow(matrix, cmap=cmap, vmin=vmin, vmax=vmax)
    ax.set_xticks(range(len(ANCHORS)), ANCHORS)
    ax.set_yticks(range(len(ANCHORS)), ANCHORS)
    ax.set_title(title)
    for i in range(len(ANCHORS)):
        for j in range(len(ANCHORS)):
            if i == j or not np.isfinite(matrix[i, j]):
                continue
            ax.text(j, i, f"{matrix[i,j]:.0f}", ha="center", va="center", fontsize=8, color="white" if abs(matrix[i,j]) > 0.6 * max(abs(vmin), abs(vmax)) else "black")
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label(cbar_label)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-root", default="autopos_pipeline/28052026_Erlangen_Official")
    parser.add_argument("--residuals-csv", default=None)
    parser.add_argument("--pairs-all-csv", default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    official_root = Path(args.official_root).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else Path(__file__).resolve().parents[1]
    tables_dir = out_dir / "tables"
    figs_dir = out_dir / "figs"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figs_dir.mkdir(parents=True, exist_ok=True)
    residuals_csv = Path(args.residuals_csv).resolve() if args.residuals_csv else official_root / "solver/outputs/v1_to_v4_io_field_check/tables/layout_residuals_per_pair.csv"
    pairs_all_csv = Path(args.pairs_all_csv).resolve() if args.pairs_all_csv else official_root / "solver/work/field_dataset_staged/sweep1000/pairs_all.csv"

    residuals = pd.read_csv(residuals_csv)
    raw = pd.read_csv(pairs_all_csv)
    idx = {a: i for i, a in enumerate(ANCHORS)}

    raw_group = raw.groupby(["a", "b", "master"], dropna=False).agg(
        median_raw_mm=("raw_mm", "median"),
        mean_raw_mm=("raw_mm", "mean"),
        std_raw_mm=("raw_mm", "std"),
        mad_raw_mm=("raw_mm", mad),
        n=("raw_mm", "count"),
        q10=("quality_percent", lambda s: float(np.nanpercentile(s, 10))),
        q50=("quality_percent", "median"),
    ).reset_index()
    raw_group.to_csv(tables_dir / "pair_raw_direction_summary.csv", index=False)

    scatter_rows = []
    asym_rows = []
    scatter_mat = blank_matrix()
    asym_mat = blank_matrix()
    for (a, b), group in raw_group.groupby(["a", "b"], dropna=False):
        ia, ib = idx[a], idx[b]
        rows = {r["master"]: r for _, r in group.iterrows()}
        combined = raw[(raw["a"] == a) & (raw["b"] == b)]
        scatter = float(combined["raw_mm"].std())
        scatter_mad = mad(combined["raw_mm"])
        scatter_mat[ia, ib] = scatter_mat[ib, ia] = scatter
        scatter_rows.append(
            {
                "pair": f"{a}-{b}",
                "std_raw_mm": scatter,
                "mad_raw_mm": scatter_mad,
                "n": int(len(combined)),
                "involves_G": "G" in (a, b),
            }
        )
        if a in rows and b in rows:
            asym = float(rows[a]["median_raw_mm"] - rows[b]["median_raw_mm"])
            asym_mat[ia, ib] = asym
            asym_mat[ib, ia] = -asym
            asym_rows.append(
                {
                    "pair": f"{a}-{b}",
                    "master_a": a,
                    "master_b": b,
                    "median_master_a_mm": float(rows[a]["median_raw_mm"]),
                    "median_master_b_mm": float(rows[b]["median_raw_mm"]),
                    "asym_a_minus_b_mm": asym,
                    "abs_asym_mm": abs(asym),
                    "involves_G": "G" in (a, b),
                }
            )
    write_csv(tables_dir / "pair_raw_scatter.csv", scatter_rows)
    write_csv(tables_dir / "pair_raw_asymmetry.csv", asym_rows)
    plot_heatmap(figs_dir / "pair_raw_scatter_heatmap.png", scatter_mat, "Raw sweep pair scatter (std)", "std raw mm", cmap="viridis", symmetric=False)
    plot_heatmap(figs_dir / "pair_raw_asymmetry_heatmap.png", asym_mat, "Raw sweep directional asymmetry", "median master(i)-master(j) mm", cmap="coolwarm", symmetric=True)

    worst_rows = []
    for (version, eval_set), df in residuals.groupby(["version", "eval_set"], dropna=False):
        bias_mat = blank_matrix()
        abs_mat = blank_matrix()
        for _, row in df.iterrows():
            a, b = str(row["pair"]).split("-")
            ia, ib = idx[a], idx[b]
            bias = float(row["residual_mm"])
            bias_mat[ia, ib] = bias_mat[ib, ia] = bias
            abs_mat[ia, ib] = abs_mat[ib, ia] = abs(bias)
            worst_rows.append(
                {
                    "version": version,
                    "eval_set": eval_set,
                    "pair": row["pair"],
                    "residual_mm": bias,
                    "abs_residual_mm": abs(bias),
                    "measured_mm": row["measured_mm"],
                    "predicted_mm": row["predicted_mm"],
                    "involves_G": "G" in str(row["pair"]),
                }
            )
        tag = f"{version}_{eval_set}".replace("/", "_")
        plot_heatmap(figs_dir / f"pair_residual_bias_heatmap_{tag}.png", bias_mat, f"{version} {eval_set} residual bias", "measured-predicted mm", cmap="coolwarm", symmetric=True)
        plot_heatmap(figs_dir / f"pair_residual_abs_heatmap_{tag}.png", abs_mat, f"{version} {eval_set} abs residual", "abs residual mm", cmap="magma", symmetric=False)

    worst = pd.DataFrame(worst_rows).sort_values(["eval_set", "abs_residual_mm"], ascending=[True, False])
    worst.to_csv(tables_dir / "worst_pairs.csv", index=False)
    v4 = worst[(worst["version"] == "v4-io") & (worst["eval_set"] == "all1000")].head(12)
    if v4.empty:
        v4 = worst[(worst["version"] == "v4-io")].head(12)
    md = ["# Pair Residual Diagnostics\n\n"]
    md.append("Raw directional asymmetry uses staged sweep `pairs_all.csv`; layout residual bias uses solver residual table.\n\n")
    md.append("G-involving pairs are explicitly flagged because OptiTrack G marker labeling is suspect.\n\n")
    md.append("## V4-io worst pairs\n\n")
    md.append("| version | eval_set | pair | residual_mm | abs_residual_mm | involves_G |\n")
    md.append("| --- | --- | --- | --- | --- | --- |\n")
    for _, row in v4.iterrows():
        md.append(f"| {row['version']} | {row['eval_set']} | {row['pair']} | {row['residual_mm']:.1f} | {row['abs_residual_mm']:.1f} | {row['involves_G']} |\n")
    asym_df = pd.DataFrame(asym_rows).sort_values("abs_asym_mm", ascending=False).head(12)
    md.append("\n## Largest raw directional asymmetries\n\n")
    md.append("| pair | asym_a_minus_b_mm | abs_asym_mm | involves_G |\n")
    md.append("| --- | --- | --- | --- |\n")
    for _, row in asym_df.iterrows():
        md.append(f"| {row['pair']} | {row['asym_a_minus_b_mm']:.1f} | {row['abs_asym_mm']:.1f} | {row['involves_G']} |\n")
    (tables_dir / "pair_residual_diagnostics.md").write_text("".join(md))

    append_run_meta(
        out_dir,
        {
            "script": "pair_residual_heatmap.py",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "args": vars(args),
            "seed": args.seed,
            "axis_convention": {
                "layout_horizontal_axes": LAYOUT_HORIZONTAL_AXES,
                "layout_vertical_axis": LAYOUT_VERTICAL_AXIS,
                "layout_upper_layer_sign": LAYOUT_UPPER_LAYER_SIGN,
                "reported_height_mm": REPORTED_HEIGHT_EXPR,
            },
            "residuals_csv": str(residuals_csv),
            "residuals_sha256": sha256_file(residuals_csv),
            "pairs_all_csv": str(pairs_all_csv),
            "pairs_all_sha256": sha256_file(pairs_all_csv),
        },
    )
    print(f"[pair] wrote {tables_dir / 'pair_residual_diagnostics.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
