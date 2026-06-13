#!/usr/bin/env python3
"""Phase-1 audit: v4-io delay-bound saturation and pairwise range excess."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def r2_score(y: np.ndarray, yhat: np.ndarray) -> float:
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return float(1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan")


def main() -> None:
    root = Path(__file__).resolve().parents[4]
    out = root / "Analysis/official_extra_analysis/FULL/audit_phase1"
    (out / "tables").mkdir(parents=True, exist_ok=True)
    (out / "figs").mkdir(parents=True, exist_ok=True)

    layout_path = root / "solver/outputs/v1_to_v4_io_field_check/v4-io/layout.json"
    layout = json.loads(layout_path.read_text())
    delay_rows = []
    for anchor in layout["anchors"]:
        delay = float(anchor.get("d_anchor_mm") or 0.0)
        delay_rows.append(
            {
                "anchor": anchor["label"],
                "d_anchor_mm": delay,
                "within_1mm_of_60_bound": abs(abs(delay) - 60.0) <= 1.0,
            }
        )
    delay_df = pd.DataFrame(delay_rows)
    delay_df.to_csv(out / "tables/v4io_anchor_delays.csv", index=False)

    coord_path = root / "Analysis/official_extra_analysis/FULL/tables/layout_abs_errors_all8.csv"
    coord = pd.read_csv(coord_path)
    coord = coord[(coord["version"] == "v4-io") & (coord["eval_set"] == "all8")].copy()
    coord = coord.sort_values("anchor")

    pair_rows = []
    for i in range(len(coord)):
        for j in range(i + 1, len(coord)):
            a = coord.iloc[i]
            b = coord.iloc[j]
            pair = f"{a['anchor']}-{b['anchor']}"
            autopos_a = np.array([a["aligned_x_mm"], a["aligned_y_vertical_mm"], a["aligned_z_mm"]], dtype=float)
            autopos_b = np.array([b["aligned_x_mm"], b["aligned_y_vertical_mm"], b["aligned_z_mm"]], dtype=float)
            truth_a = np.array([a["truth_x_mm"], a["truth_y_vertical_mm"], a["truth_z_mm"]], dtype=float)
            truth_b = np.array([b["truth_x_mm"], b["truth_y_vertical_mm"], b["truth_z_mm"]], dtype=float)
            d_autopos = float(np.linalg.norm(autopos_a - autopos_b))
            d_vicon = float(np.linalg.norm(truth_a - truth_b))
            pair_rows.append(
                {
                    "pair": pair,
                    "vicon_distance_mm": d_vicon,
                    "autopos_distance_mm": d_autopos,
                    "excess_mm": d_autopos - d_vicon,
                    "abs_excess_mm": abs(d_autopos - d_vicon),
                }
            )
    pair_df = pd.DataFrame(pair_rows)
    pair_df.to_csv(out / "tables/v4io_pair_abs_range_excess.csv", index=False)

    x_m = pair_df["vicon_distance_mm"].to_numpy(dtype=float) / 1000.0
    y = pair_df["excess_mm"].to_numpy(dtype=float)
    y_abs = pair_df["abs_excess_mm"].to_numpy(dtype=float)

    signed_const = float(y.mean())
    signed_prop_slope = float(np.dot(x_m, y) / np.dot(x_m, x_m))
    abs_const = float(y_abs.mean())
    abs_prop_slope = float(np.dot(x_m, y_abs) / np.dot(x_m, x_m))
    fit = {
        "n_pairs": int(len(pair_df)),
        "signed_constant_offset_mm": signed_const,
        "signed_constant_r2": r2_score(y, np.full_like(y, signed_const)),
        "signed_proportional_slope_mm_per_m": signed_prop_slope,
        "signed_proportional_r2": r2_score(y, signed_prop_slope * x_m),
        "absolute_constant_offset_mm": abs_const,
        "absolute_constant_r2": r2_score(y_abs, np.full_like(y_abs, abs_const)),
        "absolute_proportional_slope_mm_per_m": abs_prop_slope,
        "absolute_proportional_r2": r2_score(y_abs, abs_prop_slope * x_m),
        "delay_bound_mm": 60.0,
        "delay_saturated_count_within_1mm": int(delay_df["within_1mm_of_60_bound"].sum()),
        "tag_delay_mm": float(layout.get("tag_delay_mm") or 0.0),
        "layout_path": str(layout_path),
        "coordinate_table": str(coord_path),
    }
    (out / "tables/v4io_delay_bound_scale_fit.json").write_text(json.dumps(fit, indent=2) + "\n")

    fig, ax = plt.subplots(figsize=(7.0, 4.4), dpi=180)
    ax.scatter(x_m, y, s=38, color="#0072B2", edgecolor="white", linewidth=0.6, label="anchor pairs")
    xx = np.linspace(max(0.0, x_m.min() * 0.9), x_m.max() * 1.05, 200)
    ax.axhline(
        signed_const,
        color="#D55E00",
        linestyle="--",
        linewidth=1.6,
        label=f"constant: {signed_const:+.1f} mm, R2={fit['signed_constant_r2']:.2f}",
    )
    ax.plot(
        xx,
        signed_prop_slope * xx,
        color="#009E73",
        linewidth=1.6,
        label=f"proportional: {signed_prop_slope:+.1f} mm/m, R2={fit['signed_proportional_r2']:.2f}",
    )
    ax.axhline(0, color="0.35", linewidth=0.8)
    for _, row in pair_df.iterrows():
        ax.annotate(
            row["pair"],
            (row["vicon_distance_mm"] / 1000.0, row["excess_mm"]),
            xytext=(2, 2),
            textcoords="offset points",
            fontsize=6,
        )
    ax.set_xlabel("Vicon inter-anchor distance [m]")
    ax.set_ylabel("AutoPos - Vicon distance [mm]")
    ax.set_title("v4-io pairwise range excess after rigid registration")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8, loc="best")
    fig.tight_layout()
    fig.savefig(out / "figs/v4io_pair_range_excess_vs_vicon_distance.png")
    plt.close(fig)

    print(json.dumps(fit, indent=2))
    print(delay_df.to_string(index=False))


if __name__ == "__main__":
    main()
