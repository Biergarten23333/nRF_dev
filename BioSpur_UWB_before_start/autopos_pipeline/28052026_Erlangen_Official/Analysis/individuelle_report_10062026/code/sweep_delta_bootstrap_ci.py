#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from pair_bias_vs_distance import fit_model
from scripts.audit_helpers import ANCHOR_LABELS, markdown_table
from scripts.phase1_common import RNG_SEED
from phase2_7_final_closure import write_csv_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap confidence intervals for sweep-fitted anchor Delta_i terms.")
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    parser.add_argument("--bootstrap", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=RNG_SEED + 1500)
    return parser.parse_args()


def load_pair_rows(tables_dir: Path) -> tuple[list[tuple[str, str]], np.ndarray, np.ndarray]:
    df = pd.read_csv(tables_dir / "02_pair_bias_vs_distance.csv")
    pairs = []
    for pair in df["pair"].astype(str):
        a, b = pair.split("-")
        pairs.append((a, b))
    distances = df["vicon_distance_mm"].to_numpy(dtype=float)
    y = df["bias_mm"].to_numpy(dtype=float)
    return pairs, distances, y


def residual_bootstrap(
    pairs: list[tuple[str, str]],
    distances: np.ndarray,
    y: np.ndarray,
    mode: str,
    n_boot: int,
    seed: int,
) -> tuple[dict, np.ndarray]:
    fit = fit_model(y, pairs, distances, mode)
    rng = np.random.default_rng(seed)
    residual = np.asarray(fit["residual"], dtype=float)
    residual = residual - float(np.nanmean(residual))
    samples = []
    for _ in range(n_boot):
        y_boot = np.asarray(fit["pred"], dtype=float) + rng.choice(residual, size=len(residual), replace=True)
        samples.append(fit_model(y_boot, pairs, distances, mode)["beta"])
    return fit, np.asarray(samples, dtype=float)


def make_rows(full_fit: dict, full_samples: np.ndarray, additive_fit: dict, additive_samples: np.ndarray, n_boot: int) -> tuple[list[dict], list[dict]]:
    delta_rows = []
    for idx, anchor in enumerate(ANCHOR_LABELS):
        full_ci = np.percentile(full_samples[:, idx], [2.5, 50.0, 97.5])
        additive_ci = np.percentile(additive_samples[:, idx], [2.5, 50.0, 97.5])
        delta_rows.append(
            {
                "anchor": anchor,
                "delta_full_mm": float(full_fit["deltas"][idx]),
                "delta_full_boot_median_mm": float(full_ci[1]),
                "delta_full_ci95_low_mm": float(full_ci[0]),
                "delta_full_ci95_high_mm": float(full_ci[2]),
                "delta_additive_only_mm": float(additive_fit["deltas"][idx]),
                "delta_additive_boot_median_mm": float(additive_ci[1]),
                "delta_additive_ci95_low_mm": float(additive_ci[0]),
                "delta_additive_ci95_high_mm": float(additive_ci[2]),
                "bootstrap_n": int(n_boot),
                "bootstrap_method": "fixed_design_residual_bootstrap",
            }
        )
    rho_samples = full_samples[:, 8]
    rho_ci = np.percentile(rho_samples * 100.0, [2.5, 50.0, 97.5])
    model_rows = [
        {
            "parameter": "rho_percent",
            "estimate": float(full_fit["rho"] * 100.0),
            "boot_median": float(rho_ci[1]),
            "ci95_low": float(rho_ci[0]),
            "ci95_high": float(rho_ci[2]),
            "bootstrap_n": int(n_boot),
            "bootstrap_method": "fixed_design_residual_bootstrap",
        },
        {
            "parameter": "full_model_rms_mm",
            "estimate": float(full_fit["rms"]),
            "boot_median": float("nan"),
            "ci95_low": float("nan"),
            "ci95_high": float("nan"),
            "bootstrap_n": int(n_boot),
            "bootstrap_method": "fixed_design_residual_bootstrap",
        },
    ]
    return delta_rows, model_rows


def build_report(out_dir: Path, delta_rows: list[dict], model_rows: list[dict]) -> None:
    lines = ["# Sweep Delta Bootstrap Confidence Intervals\n"]
    lines.append(f"- Generated: `{pd.Timestamp.now().isoformat(timespec='seconds')}`")
    lines.append("- Scope: supporting table for the individual report; no solver files were modified.")
    lines.append("")
    lines.append(
        "Confidence intervals use a fixed-design residual bootstrap over the 28 unordered anchor-pair residuals from the Phase 1.2 full model. "
        "This keeps the Vicon pair geometry fixed and estimates model-parameter sensitivity to the remaining pair-level residual structure."
    )
    lines.append("")
    lines.append(markdown_table(delta_rows, ["anchor", "delta_full_mm", "delta_full_ci95_low_mm", "delta_full_ci95_high_mm", "delta_additive_only_mm", "delta_additive_ci95_low_mm", "delta_additive_ci95_high_mm"]))
    lines.append("")
    lines.append(markdown_table(model_rows, ["parameter", "estimate", "ci95_low", "ci95_high", "bootstrap_n", "bootstrap_method"]))
    lines.append("")
    lines.append("STOP: bootstrap support table complete.")
    (out_dir / "sweep_delta_bootstrap_ci.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    out_dir = args.out_dir.resolve()
    tables_dir = out_dir / "tables"
    pairs, distances, y = load_pair_rows(tables_dir)
    full_fit, full_samples = residual_bootstrap(pairs, distances, y, "full", args.bootstrap, args.seed)
    additive_fit, additive_samples = residual_bootstrap(pairs, distances, y, "additive", args.bootstrap, args.seed + 1)
    delta_rows, model_rows = make_rows(full_fit, full_samples, additive_fit, additive_samples, args.bootstrap)
    write_csv_rows(tables_dir / "15_sweep_delta_bootstrap_ci.csv", delta_rows)
    write_csv_rows(tables_dir / "15_sweep_delta_bootstrap_model_ci.csv", model_rows)
    build_report(out_dir, delta_rows, model_rows)
    print(f"Sweep delta bootstrap report written: {out_dir / 'sweep_delta_bootstrap_ci.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
