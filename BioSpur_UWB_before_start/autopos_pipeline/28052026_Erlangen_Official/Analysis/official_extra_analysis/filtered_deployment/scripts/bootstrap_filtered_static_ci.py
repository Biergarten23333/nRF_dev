#!/usr/bin/env python3
"""Bootstrap confidence intervals for filtered deployment static metrics."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


THIS = Path(__file__).resolve()
FILTERED_ROOT = THIS.parents[1]


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def rms(x: np.ndarray, axis=None):
    return np.sqrt(np.nanmean(x * x, axis=axis))


def point_metric(values: np.ndarray, metric: str) -> float:
    if metric in {"median_3d", "repeat_d3_std_median", "horizontal_median"}:
        return float(np.nanmedian(values))
    if metric in {"p95_3d", "vertical_abs_p95"}:
        return float(np.nanpercentile(values, 95))
    if metric == "rms_3d":
        return float(rms(values))
    raise ValueError(metric)


def sample_metric(samples: np.ndarray, metric: str) -> np.ndarray:
    if metric in {"median_3d", "repeat_d3_std_median", "horizontal_median"}:
        return np.nanmedian(samples, axis=1)
    if metric in {"p95_3d", "vertical_abs_p95"}:
        return np.nanpercentile(samples, 95, axis=1)
    if metric == "rms_3d":
        return rms(samples, axis=1)
    raise ValueError(metric)


def rms_scalar(x: np.ndarray) -> float:
    return float(np.sqrt(np.nanmean(x * x)))


def bootstrap(values: np.ndarray, metric: str, rng: np.random.Generator, n_boot: int) -> tuple[float, float, float]:
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return float("nan"), float("nan"), float("nan")
    if metric == "vertical_abs_p95":
        values = np.abs(values)
    point = point_metric(values, metric)
    idx = rng.integers(0, len(values), size=(n_boot, len(values)))
    boots = sample_metric(values[idx], metric)
    return point, float(np.nanpercentile(boots, 2.5)), float(np.nanpercentile(boots, 97.5))


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap filtered static deployment metrics.")
    parser.add_argument("--filtered-root", default=str(FILTERED_ROOT))
    parser.add_argument("--n-boot", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    root = Path(args.filtered_root).resolve()
    tables = root / "tables"
    figs = root / "figs"
    reports = root / "reports"
    figs.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)

    src = tables / "filtered_static_abs_errors_per_session.csv"
    df = pd.read_csv(src)
    rng = np.random.default_rng(args.seed)
    rows: list[dict] = []
    metrics = [
        ("median_3d", "err_3d_mm"),
        ("p95_3d", "err_3d_mm"),
        ("rms_3d", "err_3d_mm"),
        ("repeat_d3_std_median", "d3_std_mm"),
        ("vertical_abs_p95", "err_y_vertical_mm"),
        ("horizontal_median", "err_horizontal_mm"),
    ]
    for (version, solver, family, eval_set), g in df.groupby(["version", "solver", "solver_family", "eval_set"]):
        for metric, col in metrics:
            values = g[col].to_numpy(dtype=float)
            point, lo, hi = bootstrap(values, metric, rng, args.n_boot)
            rows.append(
                {
                    "version": version,
                    "solver": solver,
                    "solver_family": family,
                    "eval_set": eval_set,
                    "metric": metric,
                    "point_mm": point,
                    "ci_low_mm": lo,
                    "ci_high_mm": hi,
                    "n_positions": int(len(g)),
                    "n_boot": int(args.n_boot),
                    "seed": int(args.seed),
                    "source": str(src),
                }
            )
    out_csv = tables / "filtered_static_bootstrap_ci.csv"
    write_csv(out_csv, rows)

    ci = pd.DataFrame(rows)
    pick_solvers = ["T3+F0", "T3+F4", "T4+F0", "T4+F4", "T4+F5", "T5b", "T5e"]
    pick = ci[
        (ci["version"] == "v4-io")
        & (ci["eval_set"] == "all8")
        & (ci["metric"] == "median_3d")
        & (ci["solver"].isin(pick_solvers))
    ].copy()
    pick["solver"] = pd.Categorical(pick["solver"], categories=pick_solvers, ordered=True)
    pick = pick.sort_values("solver")
    if len(pick):
        fig, ax = plt.subplots(figsize=(8, 4.5), constrained_layout=True)
        x = np.arange(len(pick))
        y = pick["point_mm"].to_numpy(dtype=float)
        lo = y - pick["ci_low_mm"].to_numpy(dtype=float)
        hi = pick["ci_high_mm"].to_numpy(dtype=float) - y
        ax.errorbar(x, y, yerr=[lo, hi], fmt="o", capsize=4)
        ax.set_xticks(x)
        ax.set_xticklabels(pick["solver"].astype(str), rotation=35, ha="right")
        ax.set_ylabel("median 3D absolute error mm")
        ax.set_title("Filtered static bootstrap CI, v4-io/all8")
        ax.grid(axis="y", alpha=0.25)
        fig.savefig(figs / "filtered_bootstrap_ci_v4io_all8.png", dpi=150)
        plt.close(fig)

    md = ["# Filtered Static Bootstrap Confidence Intervals\n\n"]
    md.append(f"Source: `{src}`. Bootstrap: n={args.n_boot}, seed={args.seed}.\n\n")
    md.append("## V4-io / all8 Headline CI\n\n")
    md.append("| solver | metric | point | CI low | CI high |\n")
    md.append("| --- | --- | ---: | ---: | ---: |\n")
    sub = ci[(ci["version"] == "v4-io") & (ci["eval_set"] == "all8") & (ci["solver"].isin(pick_solvers))]
    sub = sub[sub["metric"].isin(["median_3d", "p95_3d", "rms_3d", "repeat_d3_std_median"])]
    sub["solver"] = pd.Categorical(sub["solver"], categories=pick_solvers, ordered=True)
    sub = sub.sort_values(["solver", "metric"])
    for _, row in sub.iterrows():
        md.append(
            f"| {row['solver']} | {row['metric']} | {row['point_mm']:.1f} | "
            f"{row['ci_low_mm']:.1f} | {row['ci_high_mm']:.1f} |\n"
        )
    (reports / "filtered_static_bootstrap_ci.md").write_text("".join(md), encoding="utf-8")
    print(f"[filtered-bootstrap] wrote {len(rows)} rows to {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
