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
from scripts.phase1_common import (
    RNG_SEED,
    assert_sweep_direction_columns,
    load_phase1_data,
    pairwise_vicon_distances,
    save_markdown_fragment,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 1.2 pair bias vs Vicon distance diagnostics.")
    parser.add_argument("--data-dir", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    parser.add_argument("--bootstrap", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=RNG_SEED)
    return parser.parse_args()


def design_matrix(pairs: list[tuple[str, str]], distances: np.ndarray, mode: str) -> np.ndarray:
    if mode == "full":
        x = np.zeros((len(pairs), 9), dtype=float)
        for row, (a, b) in enumerate(pairs):
            x[row, ANCHOR_LABELS.index(a)] = 0.5
            x[row, ANCHOR_LABELS.index(b)] = 0.5
            x[row, 8] = distances[row]
        return x
    if mode == "additive":
        x = np.zeros((len(pairs), 8), dtype=float)
        for row, (a, b) in enumerate(pairs):
            x[row, ANCHOR_LABELS.index(a)] = 0.5
            x[row, ANCHOR_LABELS.index(b)] = 0.5
        return x
    if mode == "proportional":
        return distances[:, None]
    raise ValueError(mode)


def fit_model(y: np.ndarray, pairs: list[tuple[str, str]], distances: np.ndarray, mode: str) -> dict:
    x = design_matrix(pairs, distances, mode)
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    pred = x @ beta
    residual = y - pred
    rms = float(np.sqrt(np.mean(residual**2)))
    sst = float(np.sum((y - y.mean()) ** 2))
    ssr = float(np.sum(residual**2))
    r2 = float(1.0 - ssr / sst) if sst else float("nan")
    rho = float(beta[-1]) if mode in {"full", "proportional"} else float("nan")
    deltas = beta[:8] if mode in {"full", "additive"} else np.full(8, np.nan)
    return {"mode": mode, "beta": beta, "pred": pred, "residual": residual, "rms": rms, "r2": r2, "rho": rho, "deltas": deltas}


def main() -> int:
    args = parse_args()
    data = load_phase1_data(args.data_dir, args.out_dir)
    assert_sweep_direction_columns(data.sweep_df)
    distances_by_pair = pairwise_vicon_distances(data.anchor_truth)
    valid = data.sweep_df[data.sweep_df["valid"].astype(bool)].copy()
    valid["pair"] = valid[["a", "b"]].astype(str).agg("-".join, axis=1)
    med = valid.groupby("pair")["dist_mm"].median()
    pairs: list[tuple[str, str]] = []
    pair_names: list[str] = []
    d_vals: list[float] = []
    b_vals: list[float] = []
    measured_vals: list[float] = []
    for i, a in enumerate(ANCHOR_LABELS):
        for b in ANCHOR_LABELS[i + 1 :]:
            name = f"{a}-{b}"
            measured = float(med[name])
            truth = float(distances_by_pair[name])
            pairs.append((a, b))
            pair_names.append(name)
            d_vals.append(truth)
            measured_vals.append(measured)
            b_vals.append(measured - truth)
    distances = np.asarray(d_vals, dtype=float)
    y = np.asarray(b_vals, dtype=float)

    fits = {mode: fit_model(y, pairs, distances, mode) for mode in ["full", "additive", "proportional"]}
    rng = np.random.default_rng(args.seed)
    rho_samples = []
    idx = np.arange(len(y))
    for _ in range(args.bootstrap):
        sample = rng.choice(idx, size=len(idx), replace=True)
        sample_pairs = [pairs[i] for i in sample]
        sample_dist = distances[sample]
        sample_y = y[sample]
        rho_samples.append(fit_model(sample_y, sample_pairs, sample_dist, "full")["rho"])
    rho_ci = np.percentile(np.asarray(rho_samples), [2.5, 97.5])

    full = fits["full"]
    pair_rows = []
    for i, name in enumerate(pair_names):
        pair_rows.append(
            {
                "pair": name,
                "measured_median_mm": measured_vals[i],
                "vicon_distance_mm": distances[i],
                "bias_mm": y[i],
                "full_pred_mm": full["pred"][i],
                "full_residual_mm": full["residual"][i],
            }
        )
    pair_df = pd.DataFrame(pair_rows)
    pair_df.to_csv(data.tables_dir / "02_pair_bias_vs_distance.csv", index=False)

    model_rows = []
    for label, fit in [
        ("additive_plus_proportional", fits["full"]),
        ("additive_only_rho0", fits["additive"]),
        ("proportional_only_delta0", fits["proportional"]),
    ]:
        model_rows.append(
            {
                "model": label,
                "rms_residual_mm": fit["rms"],
                "r2": fit["r2"],
                "rho": fit["rho"],
                "rho_percent": fit["rho"] * 100.0 if np.isfinite(fit["rho"]) else np.nan,
                "rho_ci95_low_percent": rho_ci[0] * 100.0 if label == "additive_plus_proportional" else np.nan,
                "rho_ci95_high_percent": rho_ci[1] * 100.0 if label == "additive_plus_proportional" else np.nan,
            }
        )
    model_df = pd.DataFrame(model_rows)
    model_df.to_csv(data.tables_dir / "02_pair_bias_model_summary.csv", index=False)
    delta_df = pd.DataFrame(
        {
            "anchor": ANCHOR_LABELS,
            "delta_full_mm": fits["full"]["deltas"],
            "delta_additive_only_mm": fits["additive"]["deltas"],
        }
    )
    delta_df.to_csv(data.tables_dir / "02_pair_bias_anchor_deltas.csv", index=False)

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.scatter(distances, y, label="observed pair bias", color="#2f6f9f")
    order = np.argsort(distances)
    ax.plot(distances[order], fits["proportional"]["pred"][order], label="proportional-only fit", color="#b4493a")
    ax.scatter(distances, fits["full"]["pred"], label="additive+proportional prediction", color="#3a8f5a", marker="x")
    for x, yy, name in zip(distances, y, pair_names):
        ax.annotate(name, (x, yy), fontsize=7, xytext=(3, 3), textcoords="offset points")
    ax.set_xlabel("Vicon inter-anchor distance [mm]")
    ax.set_ylabel("Median raw sweep bias [mm]")
    ax.set_title("Pair Bias vs Vicon Distance")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig_path1 = data.figures_dir / "02_pair_bias_vs_distance.png"
    fig.savefig(fig_path1, dpi=180)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.axhline(0, color="black", linewidth=0.8)
    ax.scatter(distances, full["residual"], color="#7b4fa3")
    for x, yy, name in zip(distances, full["residual"], pair_names):
        ax.annotate(name, (x, yy), fontsize=7, xytext=(3, 3), textcoords="offset points")
    ax.set_xlabel("Vicon inter-anchor distance [mm]")
    ax.set_ylabel("Full-model residual [mm]")
    ax.set_title("Residuals After Additive + Proportional Fit")
    fig.tight_layout()
    fig_path2 = data.figures_dir / "02_pair_bias_residuals_vs_distance.png"
    fig.savefig(fig_path2, dpi=180)
    plt.close(fig)

    body = []
    raw_ratio = float(np.median(np.asarray(measured_vals) / distances))
    body.append(f"Phase-0 raw ratio was `{raw_ratio:.4f}`. It conflates additive per-device delay with proportional distance bias, so it is not used directly as rho.")
    body.append("The degenerate global intercept was not included; the additive component is represented by the 8 per-anchor delay terms.")
    body.append("")
    body.append(markdown_table(model_rows, ["model", "rms_residual_mm", "r2", "rho_percent", "rho_ci95_low_percent", "rho_ci95_high_percent"]))
    body.append("")
    body.append(markdown_table(delta_df.to_dict("records"), ["anchor", "delta_full_mm", "delta_additive_only_mm"]))
    body.append("")
    body.append(f"![Pair bias vs distance](figures/{fig_path1.name})")
    body.append("")
    body.append(f"![Residuals vs distance](figures/{fig_path2.name})")
    body.append("")
    body.append(markdown_table(pair_rows, ["pair", "measured_median_mm", "vicon_distance_mm", "bias_mm", "full_pred_mm", "full_residual_mm"]))
    save_markdown_fragment(data.fragments_dir / "02_pair_bias.md", "1.2 Pair Bias vs Distance", "\n".join(body))
    print(f"rho_full={fits['full']['rho']*100.0:.3f}% rms={fits['full']['rms']:.2f} mm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

