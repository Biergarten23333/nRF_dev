#!/usr/bin/env python3
"""Build the data-native comparison assets for the two-page fusion brief."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml


THIS = Path(__file__).resolve()
EN_ROOT = THIS.parents[1]
ANALYSIS_ROOT = THIS.parents[3]
SIM_ROOT = ANALYSIS_ROOT / "IMU-Fusion-Simulation"
FACTORY_ROOT = SIM_ROOT / "runs" / "phase4_algorithm_factory"
SENSOR_CONFIG = SIM_ROOT / "configs" / "sensors.yaml"
OUT_CSV = EN_ROOT / "fusion_two_page_20way_performance.csv"
OUT_FIG = EN_ROOT / "fig" / "fusion_two_page_20way_performance.png"

ACTIVE_5090D = [
    "L0",
    "L1",
    "L2",
    "L3",
    "L4",
    "L5",
    "L7",
    "L8",
    "L10",
    "L11",
    "L12",
    "L13",
    "L14",
    "L15",
    "L16",
    "L17",
    "L18",
    "L19",
]
SEEDS = [f"S{i:02d}" for i in range(5)]
STAMP = "20260606T221157Z"

DISPLAY_NAMES = {
    "L0": "Perfect Vicon IMU",
    "L1": "Perfect sampled IMU",
    "L2": "MPU6050-like",
    "L3": "MPU9250/ICM20948-like",
    "L4": "LIS2DH12 accel proxy",
    "L5": "BMI270/LSM6DSO-like",
    "L7": "Poor low-cost clone",
    "L8": "Mis-mounted IMU",
    "L10": "MPU-6050",
    "L11": "ICM-20948",
    "L12": "ICM-20602",
    "L13": "ICM-42605",
    "L14": "ICM-42670-P",
    "L15": "ICM-42688-P",
    "L16": "ICM-45686",
    "L17": "BMI270",
    "L18": "BMI088",
    "L19": "LSM6DSV16X",
    "L20": "Xsens MTi-3",
}


def summary_path(sensor: str, seed: str) -> Path:
    if sensor != "L20":
        return (
            FACTORY_ROOT
            / f"phase4_{sensor}_TRUEFULL_{seed}_5090D_DUALLANE_{STAMP}"
            / "tables"
            / "phase4_summary.csv"
        )

    matches = sorted(
        FACTORY_ROOT.glob(
            f"phase4_L20_TRUEFULL_REPLACE_L2_{seed}_2x1080ti_*/tables/phase4_summary.csv"
        )
    )
    if len(matches) != 1:
        raise RuntimeError(f"Expected one L20 source for {seed}, found {matches}")
    return matches[0]


def load_sensor(sensor: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for seed in SEEDS:
        path = summary_path(sensor, seed)
        if not path.exists():
            raise FileNotFoundError(path)
        frame = pd.read_csv(path)
        frame["source_path"] = str(path.relative_to(SIM_ROOT))
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def best_fusion_row(sensor: str, frame: pd.DataFrame) -> dict[str, object]:
    candidates = frame[
        (frame["kind"] == "position_fusion")
        & (frame["A"] == "A0")
        & (frame["U"] == "U4")
    ].copy()
    if candidates.empty:
        raise RuntimeError(f"No A0/U4 position-fusion rows for {sensor}")

    grouped = (
        candidates.groupby(["U", "P", "I", "T"], dropna=False)
        .agg(
            seed_count=("seed_id", "nunique"),
            p50_mm=("trackmedian_err3d_p50_mm", "mean"),
            p95_mm=("trackmedian_err3d_p95_mm", "mean"),
            p95_seed_sd_mm=("trackmedian_err3d_p95_mm", "std"),
            rmse_mm=("trackmedian_err3d_rmse_mm", "mean"),
            thickness_p95_mm=("trackmedian_circle_thickness_p95_mm", "mean"),
        )
        .reset_index()
        .sort_values(["p95_mm", "p50_mm", "rmse_mm"])
    )
    best = grouped.iloc[0]
    if int(best["seed_count"]) != 5:
        raise RuntimeError(f"Best row for {sensor} does not contain five seeds")
    return {
        "id": sensor,
        "sensor_name": DISPLAY_NAMES[sensor],
        "comparison_class": "synthetic_imu",
        "best_config": f"A0/U4/{best['P']}/{sensor}/{best['I']}/{best['T']}",
        "seed_count": int(best["seed_count"]),
        "p50_mm": float(best["p50_mm"]),
        "p95_mm": float(best["p95_mm"]),
        "p95_seed_sd_mm": float(best["p95_seed_sd_mm"]),
        "rmse_mm": float(best["rmse_mm"]),
        "thickness_p95_mm": float(best["thickness_p95_mm"]),
    }


def uwb_controls(frame: pd.DataFrame) -> tuple[dict[str, object], float]:
    baseline = frame[
        (frame["kind"] == "uwb_only")
        & (frame["A"] == "A0")
        & (frame["U"] == "U4")
        & (frame["T"] == "T1")
    ].copy()
    b0 = baseline[baseline["P"] == "P0"]
    p4 = baseline[baseline["P"] == "P4"]
    if b0.empty or p4.empty:
        raise RuntimeError("Missing B0 or matched-P4 UWB control")
    control = {
        "id": "B0",
        "sensor_name": "Pure UWB control",
        "comparison_class": "uwb_only_control",
        "best_config": "A0/U4/P0/no-IMU/T1",
        "seed_count": int(b0["seed_id"].nunique()),
        "p50_mm": float(b0["trackmedian_err3d_p50_mm"].mean()),
        "p95_mm": float(b0["trackmedian_err3d_p95_mm"].mean()),
        "p95_seed_sd_mm": float(b0["trackmedian_err3d_p95_mm"].std()),
        "rmse_mm": float(b0["trackmedian_err3d_rmse_mm"].mean()),
        "thickness_p95_mm": float(
            b0["trackmedian_circle_thickness_p95_mm"].mean()
        ),
    }
    return control, float(p4["trackmedian_err3d_p95_mm"].mean())


def plot_performance(results: pd.DataFrame, matched_p4_p95: float) -> None:
    plot_df = results.sort_values("p95_mm", ascending=False).reset_index(drop=True)
    labels = [f"{row.id}  {row.sensor_name}" for row in plot_df.itertuples()]
    colors = []
    for row in plot_df.itertuples():
        if row.id == "B0":
            colors.append("#9aa0a6")
        elif row.id in {"L0", "L1"}:
            colors.append("#4c78a8")
        elif row.id in {"L4", "L7", "L8"}:
            colors.append("#e07b39")
        elif row.id == "L20":
            colors.append("#8f63b8")
        else:
            colors.append("#2a9d8f")

    fig, ax = plt.subplots(figsize=(10.4, 7.2))
    y = np.arange(len(plot_df))
    bars = ax.barh(y, plot_df["p95_mm"], color=colors, height=0.72)
    ax.axvline(
        matched_p4_p95,
        color="#b44b4b",
        linestyle="--",
        linewidth=1.6,
        label=f"matched pure-UWB P4: {matched_p4_p95:.1f} mm",
    )
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8.5)
    ax.set_xlabel("Five-seed mean track-median 3D P95 error (mm)")
    ax.set_title(
        "20-way benchmark: 19 synthetic IMU scenarios + pure-UWB B0 control",
        fontsize=12,
        weight="bold",
    )
    ax.grid(axis="x", alpha=0.22)
    ax.set_axisbelow(True)
    ax.legend(loc="lower right", frameon=False, fontsize=8.5)
    ax.set_xlim(0, max(250.0, float(plot_df["p95_mm"].max()) * 1.12))
    for bar, value in zip(bars, plot_df["p95_mm"], strict=True):
        ax.text(
            value + 2.0,
            bar.get_y() + bar.get_height() / 2,
            f"{value:.1f}",
            va="center",
            fontsize=7.8,
        )
    fig.text(
        0.01,
        0.005,
        "Best A0/U4 production row selected independently per registered synthetic sensor; "
        "L6 and L9 are reserved IDs without models or results.",
        fontsize=7.5,
        color="#444444",
    )
    fig.tight_layout(rect=(0, 0.025, 1, 1))
    OUT_FIG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_FIG, dpi=240, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    sensor_config = yaml.safe_load(SENSOR_CONFIG.read_text(encoding="utf-8"))
    configured = list(sensor_config)
    expected = ACTIVE_5090D + ["L20"]
    if configured != expected:
        raise RuntimeError(f"Unexpected sensor registry: {configured}")

    frames = {sensor: load_sensor(sensor) for sensor in expected}
    rows = [best_fusion_row(sensor, frames[sensor]) for sensor in expected]
    b0, matched_p4_p95 = uwb_controls(frames["L0"])
    rows.append(b0)
    results = pd.DataFrame(rows)
    if len(results) != 20:
        raise RuntimeError(f"Expected 20 comparison objects, got {len(results)}")
    b0_p95 = float(results.loc[results["id"] == "B0", "p95_mm"].iloc[0])
    results["p95_improvement_vs_b0_mm"] = b0_p95 - results["p95_mm"]
    results["rank"] = results["p95_mm"].rank(method="first").astype(int)
    results["matched_p4_uwb_p95_mm"] = matched_p4_p95
    results = results.sort_values("rank").reset_index(drop=True)
    results.to_csv(OUT_CSV, index=False, float_format="%.6f")
    plot_performance(results, matched_p4_p95)
    print(results.to_string(index=False))
    print(f"Wrote {OUT_CSV}")
    print(f"Wrote {OUT_FIG}")


if __name__ == "__main__":
    main()
