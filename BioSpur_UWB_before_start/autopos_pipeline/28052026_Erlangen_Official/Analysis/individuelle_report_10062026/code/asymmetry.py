#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.audit_helpers import ANCHOR_LABELS, markdown_table
from scripts.phase1_common import RNG_SEED, assert_sweep_direction_columns, load_phase1_data, robust_center, save_markdown_fragment


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 1.1 directed sweep asymmetry diagnostics.")
    parser.add_argument("--data-dir", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    parser.add_argument("--bootstrap", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=RNG_SEED)
    return parser.parse_args()


def bootstrap_diff(a: np.ndarray, b: np.ndarray, n_boot: int, rng: np.random.Generator) -> tuple[float, float]:
    vals = np.empty(n_boot, dtype=float)
    for idx in range(n_boot):
        aa = a[rng.integers(0, len(a), len(a))]
        bb = b[rng.integers(0, len(b), len(b))]
        vals[idx] = np.median(aa) - np.median(bb)
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def main() -> int:
    args = parse_args()
    data = load_phase1_data(args.data_dir, args.out_dir)
    assert_sweep_direction_columns(data.sweep_df)
    rng = np.random.default_rng(args.seed)
    valid = data.sweep_df[data.sweep_df["valid"].astype(bool)].copy()
    rows = []
    matrix = np.full((8, 8), np.nan)
    for i, a in enumerate(ANCHOR_LABELS):
        for j, b in enumerate(ANCHOR_LABELS[i + 1 :], start=i + 1):
            ab = pd.to_numeric(
                valid.loc[(valid["initiator"] == a) & (valid["responder"] == b), "dist_mm"], errors="coerce"
            ).dropna().to_numpy()
            ba = pd.to_numeric(
                valid.loc[(valid["initiator"] == b) & (valid["responder"] == a), "dist_mm"], errors="coerce"
            ).dropna().to_numpy()
            med_ab = robust_center(ab)
            med_ba = robust_center(ba)
            asym = med_ab - med_ba
            ci_low, ci_high = bootstrap_diff(ab, ba, args.bootstrap, rng)
            significant = bool(ci_low > 0 or ci_high < 0)
            matrix[i, j] = asym
            matrix[j, i] = -asym
            rows.append(
                {
                    "pair": f"{a}-{b}",
                    "n_ab": len(ab),
                    "n_ba": len(ba),
                    "median_ab_mm": med_ab,
                    "median_ba_mm": med_ba,
                    "asymmetry_ab_minus_ba_mm": asym,
                    "ci95_low_mm": ci_low,
                    "ci95_high_mm": ci_high,
                    "ci_excludes_zero": significant,
                }
            )
    out_df = pd.DataFrame(rows)
    out_df.to_csv(data.tables_dir / "01_asymmetry.csv", index=False)

    fig, ax = plt.subplots(figsize=(7, 6))
    vmax = np.nanmax(np.abs(matrix))
    im = ax.imshow(matrix, cmap="RdBu_r", vmin=-vmax, vmax=vmax)
    ax.set_xticks(range(8), ANCHOR_LABELS)
    ax.set_yticks(range(8), ANCHOR_LABELS)
    ax.set_xlabel("Responder")
    ax.set_ylabel("Initiator")
    for ii in range(8):
        for jj in range(8):
            if ii == jj:
                continue
            ax.text(jj, ii, f"{matrix[ii, jj]:.0f}", ha="center", va="center", fontsize=8)
    ax.set_title("Directed Sweep Asymmetry (mm)")
    fig.colorbar(im, ax=ax, label="median(i->j) - median(j->i) [mm]")
    fig.tight_layout()
    fig_path = data.figures_dir / "01_asymmetry_heatmap.png"
    fig.savefig(fig_path, dpi=180)
    plt.close(fig)

    significant_count = int(out_df["ci_excludes_zero"].sum())
    body = []
    body.append(f"Computed robust directed asymmetry on 28 anchor pairs using median(i->j) - median(j->i), with {args.bootstrap} bootstrap resamples per pair.")
    body.append("")
    body.append(markdown_table(
        [
            {
                "pairs": len(out_df),
                "significant_ci_excludes_zero": significant_count,
                "max_abs_asymmetry_mm": float(out_df["asymmetry_ab_minus_ba_mm"].abs().max()),
                "median_abs_asymmetry_mm": float(out_df["asymmetry_ab_minus_ba_mm"].abs().median()),
            }
        ],
        ["pairs", "significant_ci_excludes_zero", "max_abs_asymmetry_mm", "median_abs_asymmetry_mm"],
    ))
    body.append(f"![Asymmetry heatmap](figures/{fig_path.name})")
    body.append("")
    body.append(markdown_table(out_df.to_dict("records"), ["pair", "n_ab", "n_ba", "median_ab_mm", "median_ba_mm", "asymmetry_ab_minus_ba_mm", "ci95_low_mm", "ci95_high_mm", "ci_excludes_zero"]))
    body.append("No time-drift analysis was run because sweep rows have no per-sample timestamps.")
    save_markdown_fragment(data.fragments_dir / "01_asymmetry.md", "1.1 Asymmetry", "\n".join(body))
    print(f"wrote {data.tables_dir / '01_asymmetry.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

