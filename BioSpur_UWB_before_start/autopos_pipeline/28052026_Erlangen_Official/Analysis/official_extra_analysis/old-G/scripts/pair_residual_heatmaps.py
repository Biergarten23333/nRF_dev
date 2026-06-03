#!/usr/bin/env python3
"""Task 5: pairwise anchor residual and asymmetry heatmaps."""

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


def matrix_from_pairs(df: pd.DataFrame, value_col: str) -> np.ndarray:
    mat = np.full((len(ANCHOR_LABELS), len(ANCHOR_LABELS)), np.nan, dtype=float)
    np.fill_diagonal(mat, 0.0)
    idx = {a: i for i, a in enumerate(ANCHOR_LABELS)}
    for _, row in df.iterrows():
        a, b = str(row["pair"]).split("-")
        i, j = idx[a], idx[b]
        val = float(row[value_col])
        mat[i, j] = val
        mat[j, i] = val
    return mat


def plot_matrix(path: Path, mat: np.ndarray, title: str, cbar_label: str, cmap: str = "coolwarm", symmetric: bool = True) -> None:
    finite = mat[np.isfinite(mat)]
    if finite.size == 0:
        vmax = 1.0
        vmin = -1.0 if symmetric else 0.0
    elif symmetric:
        vmax = float(np.nanmax(np.abs(finite)))
        vmin = -vmax
    else:
        vmin = 0.0
        vmax = float(np.nanpercentile(finite, 95))
    fig, ax = plt.subplots(figsize=(7, 6))
    im = ax.imshow(mat, cmap=cmap, vmin=vmin, vmax=vmax)
    ax.set_xticks(range(len(ANCHOR_LABELS)), ANCHOR_LABELS)
    ax.set_yticks(range(len(ANCHOR_LABELS)), ANCHOR_LABELS)
    ax.set_title(title)
    for i in range(len(ANCHOR_LABELS)):
        for j in range(len(ANCHOR_LABELS)):
            if np.isfinite(mat[i, j]):
                ax.text(j, i, f"{mat[i,j]:.0f}", ha="center", va="center", fontsize=8, color="white" if abs(mat[i,j]) > 0.55 * max(abs(vmin), abs(vmax), 1) else "black")
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cb.set_label(cbar_label)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def write_csv(path: Path, rows: list[dict]) -> None:
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
    parser.add_argument("--version", default="v4-io")
    parser.add_argument("--eval-set", default="all1000")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    official_root = Path(args.official_root).resolve()
    solver_out = official_root / "solver/outputs/v1_to_v4_io_field_check"
    out_dir = Path(args.out_dir).resolve() if args.out_dir else official_root / "Analysis/official_extra_analysis"
    tables_dir = out_dir / "tables"
    figs_dir = out_dir / "figs"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figs_dir.mkdir(parents=True, exist_ok=True)

    residuals_path = solver_out / "tables/layout_residuals_per_pair.csv"
    quality_path = solver_out / "tables/pair_quality_solve.csv"
    residuals = pd.read_csv(residuals_path)
    quality = pd.read_csv(quality_path)

    main = residuals[(residuals["version"] == args.version) & (residuals["eval_set"] == args.eval_set)].copy()
    if main.empty:
        # Some versions have per-version all1000 files while aggregate uses solve only.
        per_version = solver_out / args.version / "layout_residuals_all1000.csv"
        if per_version.exists():
            main = pd.read_csv(per_version)
        else:
            raise ValueError(f"No residual rows for {args.version}/{args.eval_set}")

    if "pair" not in main.columns:
        raise ValueError(f"No pair column in residual table for {args.version}")

    bias_mat = matrix_from_pairs(main, "residual_mm")
    abs_mat = matrix_from_pairs(main, "abs_residual_mm")
    asym_mat = matrix_from_pairs(quality, "asymmetry")
    mad_mat = matrix_from_pairs(quality, "mad_all")

    plot_matrix(figs_dir / "pair_residual_bias_heatmap.png", bias_mat, f"{args.version} pair residual bias ({args.eval_set})", "predicted - measured mm", cmap="coolwarm", symmetric=True)
    plot_matrix(figs_dir / "pair_residual_abs_heatmap.png", abs_mat, f"{args.version} pair absolute residual ({args.eval_set})", "abs residual mm", cmap="magma", symmetric=False)
    plot_matrix(figs_dir / "pair_residual_asymmetry.png", asym_mat, "Sweep directional asymmetry med_ab - med_ba", "asymmetry mm", cmap="viridis", symmetric=False)
    plot_matrix(figs_dir / "pair_residual_scatter_heatmap.png", mad_mat, "Sweep pair scatter MAD", "MAD mm", cmap="plasma", symmetric=False)

    merged = main.merge(quality[["pair", "asymmetry", "mad_all", "n_ab", "n_ba"]], on="pair", how="left")
    merged["involves_G"] = merged["pair"].str.contains("G")
    worst = merged.sort_values("abs_residual_mm", ascending=False).head(28)
    write_csv(tables_dir / "worst_pairs.csv", worst.to_dict("records"))

    summary = {
        "version": args.version,
        "eval_set": args.eval_set,
        "median_abs_residual_mm": float(merged["abs_residual_mm"].median()),
        "p95_abs_residual_mm": float(np.nanpercentile(merged["abs_residual_mm"], 95)),
        "max_abs_residual_mm": float(merged["abs_residual_mm"].max()),
        "mean_abs_asymmetry_mm": float(merged["asymmetry"].abs().mean()),
        "g_pair_median_abs_residual_mm": float(merged[merged["involves_G"]]["abs_residual_mm"].median()),
        "non_g_pair_median_abs_residual_mm": float(merged[~merged["involves_G"]]["abs_residual_mm"].median()),
    }
    (tables_dir / "pair_residual_summary.md").write_text(
        "# Pair Residual Summary\n\n"
        + "\n".join(f"- `{k}`: {v:.3f}" if isinstance(v, float) else f"- `{k}`: {v}" for k, v in summary.items())
        + "\n"
    )

    append_run_meta(
        out_dir,
        {
            "script": "pair_residual_heatmaps.py",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "args": vars(args),
            "seed": args.seed,
            "residuals_path": str(residuals_path),
            "residuals_sha256": sha256_file(residuals_path),
            "quality_path": str(quality_path),
            "quality_sha256": sha256_file(quality_path),
        },
    )
    print(f"[pair] wrote {tables_dir / 'pair_residual_summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
