#!/usr/bin/env python3
"""Create professor-facing L2/L16/L20 comparison on the spikiest ROTO track.

Presentation convention for this figure: vertical is shown as Z, because that
is the convention used in the surrounding BioSpur discussion and slides. The
official aligned CSV stores that height coordinate as y_vertical, so the
vertical-Z panel is computed from the in-memory y_mm/opti_y_mm columns and
displayed as Z height.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


THIS = Path(__file__).resolve()
SIM_ROOT = THIS.parents[1]
FACTORY_SCRIPT = SIM_ROOT / "scripts" / "run_phase4_l2_singleI_full_factory.py"
ANALYSIS_DIR = SIM_ROOT / "runs" / "phase4_analysis" / "l2_l16_l20_truefull_5seed_opti_truth_20260605T221806Z"
OUT_DIR = ANALYSIS_DIR / "figs" / "professor_l2_l16_l20_spiky_track"
TABLE_DIR = ANALYSIS_DIR / "tables" / "professor_l2_l16_l20_spiky_track"
REPORT_PATH = ANALYSIS_DIR / "reports" / "PHASE4_PROFESSOR_L2_L16_L20_SPIKY_TRACK.md"

SEED_ID = "S00"
CAPTURE_ID = "R01"
TAG = "BS2DCE"

EXPERIMENTS = {
    "L2 MPU6050/JY61P-like": "X_A0_U4_P4_L2_I5_T3",
    "L16 ICM-45686": "X_A0_U4_P4_L16_I6_T4",
    "L20 Xsens MTi-3": "X_A0_U4_P4_L20_I3_T2",
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


F = load_module(FACTORY_SCRIPT, "phase4_factory_for_professor_spiky_track")


def parse_experiment(exp: str) -> dict[str, str]:
    parts = exp.split("_")
    return {"A": parts[1], "U": parts[2], "P": parts[3], "L": parts[4], "I": parts[5], "T": parts[6]}


def pct(values: np.ndarray, q: float) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, q))


def pick_track(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["capture_id"].astype(str) == CAPTURE_ID) & (df["tag"].astype(str) == TAG)].sort_values("time_s").copy()


def err3d(df: pd.DataFrame) -> np.ndarray:
    xyz = df[["x_mm", "y_mm", "z_mm"]].to_numpy(float)
    opt = df[["opti_x_mm", "opti_y_mm", "opti_z_mm"]].to_numpy(float)
    return np.linalg.norm(xyz - opt, axis=1)


def err_vertical_z(df: pd.DataFrame) -> np.ndarray:
    # User-facing frame: Z is height. Official aligned ROTO frame: y_vertical is
    # height. The in-memory y_mm/opti_y_mm columns are loaded from y_vertical.
    return np.abs(df["y_mm"].to_numpy(float) - df["opti_y_mm"].to_numpy(float))


def jump_xz(df: pd.DataFrame) -> np.ndarray:
    xz = df[["x_mm", "z_mm"]].to_numpy(float)
    return np.linalg.norm(np.diff(xz, axis=0), axis=1)


def reconstruct(exp: str, streams: dict[tuple[str, str, str], pd.DataFrame], b0: pd.DataFrame) -> pd.DataFrame:
    parts = parse_experiment(exp)
    F._SEED_ID = SEED_ID
    F.install_sensor_props(parts["L"])
    stream = streams[(parts["A"], parts["U"], parts["P"])].copy()
    imu = F.S1.simulate_imu_for_li(b0, f"phase4_{parts['L'].lower()}_full_{SEED_ID}_{parts['A']}", parts["L"], parts["I"])
    params = F.POSITION_T_PARAMS[parts["T"]]
    process = F.S1.li_process_factor(parts["L"], parts["I"])
    return F.S1.position_fusion_samples(
        stream,
        imu,
        exp,
        str(params["deployability"]),
        f"Professor-facing comparison {exp}",
        float(params["prior_sigma_base"]) * process,
        float(params["measurement_sigma"]),
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)

    streams = F.load_streams()
    b0_all = streams[("A0", "U4", "P0")].copy()
    b0 = pick_track(b0_all)
    b0_err = err3d(b0)
    b0_zerr = err_vertical_z(b0)
    b0_jump = jump_xz(b0)

    fusions: dict[str, pd.DataFrame] = {}
    for label, exp in EXPERIMENTS.items():
        fusions[label] = pick_track(reconstruct(exp, streams, b0_all))

    rows: list[dict] = []
    for label, exp in EXPERIMENTS.items():
        fusion = fusions[label]
        fusion_err = err3d(fusion)
        fusion_zerr = err_vertical_z(fusion)
        fusion_jump = jump_xz(fusion)
        rows.append(
            {
                "label": label,
                "experiment_id": exp,
                "capture_id": CAPTURE_ID,
                "tag": TAG,
                "seed_id": SEED_ID,
                "b0_err3d_p50_mm": pct(b0_err, 50),
                "b0_err3d_p95_mm": pct(b0_err, 95),
                "b0_err3d_rmse_mm": float(np.sqrt(np.nanmean(b0_err * b0_err))),
                "fusion_err3d_p50_mm": pct(fusion_err, 50),
                "fusion_err3d_p95_mm": pct(fusion_err, 95),
                "fusion_err3d_rmse_mm": float(np.sqrt(np.nanmean(fusion_err * fusion_err))),
                "improvement_p95_mm": pct(b0_err, 95) - pct(fusion_err, 95),
                "b0_jump_xz_p99_mm": pct(b0_jump, 99),
                "b0_max_jump_xz_mm": float(np.nanmax(b0_jump)),
                "b0_jump_xz_gt200_count": int(np.sum(b0_jump > 200)),
                "fusion_jump_xz_p99_mm": pct(fusion_jump, 99),
                "fusion_max_jump_xz_mm": float(np.nanmax(fusion_jump)),
                "fusion_jump_xz_gt200_count": int(np.sum(fusion_jump > 200)),
                "b0_vertical_z_p95_mm": pct(b0_zerr, 95),
                "fusion_vertical_z_p95_mm": pct(fusion_zerr, 95),
            }
        )
    metrics = pd.DataFrame(rows)
    metrics.to_csv(TABLE_DIR / "R01_BS2DCE_L2_L16_L20_professor_metrics_verticalZ.csv", index=False)

    xs: list[float] = []
    zs: list[float] = []
    for df in [b0, *fusions.values()]:
        xs.extend(df["x_mm"].to_numpy(float))
        xs.extend(df["opti_x_mm"].to_numpy(float))
        zs.extend(df["z_mm"].to_numpy(float))
        zs.extend(df["opti_z_mm"].to_numpy(float))
    pad = 80.0
    xlim = (float(np.nanmin(xs) - pad), float(np.nanmax(xs) + pad))
    zlim = (float(np.nanmin(zs) - pad), float(np.nanmax(zs) + pad))

    fig, axes = plt.subplots(1, 3, figsize=(18, 6), sharex=True, sharey=True)
    for ax, (label, exp) in zip(axes, EXPERIMENTS.items()):
        fusion = fusions[label]
        row = metrics[metrics["label"] == label].iloc[0]
        ax.plot(b0["opti_x_mm"], b0["opti_z_mm"], color="black", linewidth=2.0, label="Opti truth")
        ax.plot(b0["x_mm"], b0["z_mm"], color="#8cc8ef", linewidth=0.8, alpha=0.42, label="B0 pure UWB")
        ax.plot(fusion["x_mm"], fusion["z_mm"], color="#8b4cc2", linewidth=1.5, alpha=0.95, label="UWB+IMU fusion")
        ax.set_title(
            f"{label}\n{exp}\nB0 P95={row.b0_err3d_p95_mm:.0f} mm -> Fusion P95={row.fusion_err3d_p95_mm:.0f} mm",
            fontsize=10,
        )
        ax.grid(alpha=0.25)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlim(*xlim)
        ax.set_ylim(*zlim)
        ax.set_xlabel("X position (mm)")
    axes[0].set_ylabel("Z position (mm)")
    axes[0].legend(loc="best", fontsize=9)
    fig.suptitle(
        f"Same spiky ROTO track: {CAPTURE_ID}/{TAG} | B0 has {int(np.sum(b0_jump > 200))} X-Z jumps >200 mm",
        fontsize=14,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(OUT_DIR / "01_R01_BS2DCE_same_track_XZ_L2_L16_L20.png", dpi=220)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(14, 6))
    t = b0["time_s"].to_numpy(float) - float(b0["time_s"].iloc[0])
    ax.plot(t, b0_err, color="#8cc8ef", alpha=0.55, linewidth=0.8, label=f"B0 pure UWB P95={pct(b0_err, 95):.0f} mm")
    colors = ["#5877c8", "#9c66cc", "#16a085"]
    for color, (label, _exp) in zip(colors, EXPERIMENTS.items()):
        fusion = fusions[label]
        tt = fusion["time_s"].to_numpy(float) - float(fusion["time_s"].iloc[0])
        fusion_err = err3d(fusion)
        ax.plot(tt, fusion_err, color=color, linewidth=1.2, alpha=0.95, label=f"{label} fusion P95={pct(fusion_err, 95):.0f} mm")
    ax.set_title(f"3D error over time: {CAPTURE_ID}/{TAG}")
    ax.set_xlabel("time in capture (s)")
    ax.set_ylabel("3D error vs Opti (mm)")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=9, ncols=2)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "02_R01_BS2DCE_err3d_time_L2_L16_L20.png", dpi=220)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(t, b0_zerr, color="#8cc8ef", alpha=0.55, linewidth=0.8, label=f"B0 pure UWB vertical Z P95={pct(b0_zerr, 95):.0f} mm")
    for color, (label, _exp) in zip(colors, EXPERIMENTS.items()):
        fusion = fusions[label]
        tt = fusion["time_s"].to_numpy(float) - float(fusion["time_s"].iloc[0])
        fusion_zerr = err_vertical_z(fusion)
        ax.plot(tt, fusion_zerr, color=color, linewidth=1.2, alpha=0.95, label=f"{label} fusion vertical Z P95={pct(fusion_zerr, 95):.0f} mm")
    ax.set_title(f"Vertical Z / height error over time: {CAPTURE_ID}/{TAG}")
    ax.set_xlabel("time in capture (s)")
    ax.set_ylabel("vertical Z abs error vs Opti (mm)")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=9, ncols=2)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "03_R01_BS2DCE_vertical_Z_error_time_L2_L16_L20.png", dpi=220)
    # Also overwrite the previous confusing filename so old links show corrected labels.
    fig.savefig(OUT_DIR / "03_R01_BS2DCE_vertical_y_error_time_L2_L16_L20.png", dpi=220)
    plt.close(fig)

    report = [
        "# Professor L2/L16/L20 Spiky Track Comparison",
        "",
        f"Generated UTC: `{datetime.now(UTC).isoformat()}`",
        "",
        f"Selected track: `{CAPTURE_ID}/{TAG}` because B0 pure UWB has the most X-Z jump spikes in the ROTO set.",
        "",
        "Presentation convention: this report labels the height axis as `Vertical Z`; internally this uses the official aligned table's `y_vertical` height column.",
        "",
        f"- B0 X-Z `jump > 200 mm`: `{int(np.sum(b0_jump > 200))}` samples",
        f"- B0 X-Z `jump_p99`: `{pct(b0_jump, 99):.1f} mm`",
        f"- B0 X-Z `max_jump`: `{float(np.nanmax(b0_jump)):.1f} mm`",
        f"- B0 3D P95: `{pct(b0_err, 95):.1f} mm`",
        f"- B0 vertical Z P95: `{pct(b0_zerr, 95):.1f} mm`",
        "",
        "## Experiments",
        "",
    ]
    for _, row in metrics.iterrows():
        report.extend(
            [
                f"### `{row['experiment_id']}`",
                "",
                f"- IMU: {row['label']}",
                f"- Fusion 3D P95: `{row['fusion_err3d_p95_mm']:.1f} mm`",
                f"- P95 improvement vs B0: `{row['improvement_p95_mm']:.1f} mm`",
                f"- Fusion X-Z jump >200mm count: `{int(row['fusion_jump_xz_gt200_count'])}`",
                f"- Fusion vertical Z P95: `{row['fusion_vertical_z_p95_mm']:.1f} mm`",
                "",
            ]
        )
    report.extend(
        [
            "## Figures",
            "",
            "- `figs/professor_l2_l16_l20_spiky_track/01_R01_BS2DCE_same_track_XZ_L2_L16_L20.png`",
            "- `figs/professor_l2_l16_l20_spiky_track/02_R01_BS2DCE_err3d_time_L2_L16_L20.png`",
            "- `figs/professor_l2_l16_l20_spiky_track/03_R01_BS2DCE_vertical_Z_error_time_L2_L16_L20.png`",
            "",
            "## Table",
            "",
            "- `tables/professor_l2_l16_l20_spiky_track/R01_BS2DCE_L2_L16_L20_professor_metrics_verticalZ.csv`",
        ]
    )
    REPORT_PATH.write_text("\n".join(report), encoding="utf-8")
    print(OUT_DIR)
    print(REPORT_PATH)
    print(metrics.to_string(index=False))


if __name__ == "__main__":
    main()
