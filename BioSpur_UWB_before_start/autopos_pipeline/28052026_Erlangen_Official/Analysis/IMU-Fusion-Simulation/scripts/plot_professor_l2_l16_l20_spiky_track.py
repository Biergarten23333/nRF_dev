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

plt.rcParams.update(
    {
        "font.size": 17,
        "axes.titlesize": 18,
        "axes.labelsize": 17,
        "xtick.labelsize": 14,
        "ytick.labelsize": 14,
        "legend.fontsize": 14,
        "figure.titlesize": 20,
        "lines.linewidth": 2.2,
    }
)


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


def plane_projection_basis(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    opt = df[["opti_x_mm", "opti_y_mm", "opti_z_mm"]].to_numpy(float)
    centre = np.nanmean(opt, axis=0)
    centered = opt - centre
    _u, _s, vh = np.linalg.svd(centered, full_matrices=False)
    e1 = vh[0]
    e2 = vh[1]
    return centre, e1, e2


def project_to_plane(df: pd.DataFrame, centre: np.ndarray, e1: np.ndarray, e2: np.ndarray, prefix: str = "") -> tuple[np.ndarray, np.ndarray]:
    cols = [f"{prefix}x_mm", f"{prefix}y_mm", f"{prefix}z_mm"] if prefix else ["x_mm", "y_mm", "z_mm"]
    xyz = df[cols].to_numpy(float) - centre
    return xyz @ e1, xyz @ e2


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

    tilt_tracks = [
        ("Almost planar", "R01", "BS2DCE"),
        ("Mid tilt", "R08", "BS2DCE"),
        ("Almost vertical", "R16", "BS2DCE"),
    ]
    tilt_fusions: dict[str, pd.DataFrame] = {}
    for imu_label, exp in EXPERIMENTS.items():
        tilt_fusions[imu_label] = reconstruct(exp, streams, b0_all)

    fig, axes = plt.subplots(
        len(tilt_tracks),
        len(EXPERIMENTS),
        figsize=(17.6, 15.0),
        constrained_layout=True,
    )
    for row_idx, (tilt_label, cap, tag) in enumerate(tilt_tracks):
        track_b0 = b0_all[(b0_all["capture_id"].astype(str) == cap) & (b0_all["tag"].astype(str) == tag)].sort_values("time_s").copy()
        if track_b0.empty:
            continue
        centre, e1, e2 = plane_projection_basis(track_b0)
        opt_u, opt_v = project_to_plane(track_b0, centre, e1, e2, "opti_")
        b0_u, b0_v = project_to_plane(track_b0, centre, e1, e2)
        all_u = [opt_u, b0_u]
        all_v = [opt_v, b0_v]
        projected: dict[str, tuple[np.ndarray, np.ndarray, float]] = {}
        for imu_label, track_all in tilt_fusions.items():
            track_fusion = track_all[(track_all["capture_id"].astype(str) == cap) & (track_all["tag"].astype(str) == tag)].sort_values("time_s").copy()
            fu_u, fu_v = project_to_plane(track_fusion, centre, e1, e2)
            projected[imu_label] = (fu_u, fu_v, pct(err3d(track_fusion), 95))
            all_u.append(fu_u)
            all_v.append(fu_v)
        umin, umax = float(np.nanmin(np.concatenate(all_u))), float(np.nanmax(np.concatenate(all_u)))
        vmin, vmax = float(np.nanmin(np.concatenate(all_v))), float(np.nanmax(np.concatenate(all_v)))
        span = max(umax - umin, vmax - vmin)
        upad = (span - (umax - umin)) / 2 + 80.0
        vpad = (span - (vmax - vmin)) / 2 + 80.0

        for col_idx, (imu_label, _exp) in enumerate(EXPERIMENTS.items()):
            ax = axes[row_idx, col_idx]
            fu_u, fu_v, p95 = projected[imu_label]
            ax.plot(opt_u, opt_v, color="black", linewidth=2.6, label="Vicon truth")
            ax.plot(b0_u, b0_v, color="#8cc8ef", linewidth=1.1, alpha=0.45, label="Pure UWB")
            ax.plot(fu_u, fu_v, color="#8b4cc2", linewidth=2.2, alpha=0.95, label="UWB+IMU")
            if row_idx == 0:
                ax.set_title(imu_label, pad=9)
            if col_idx == 0:
                ax.set_ylabel(f"{tilt_label}\n{cap}/{tag}\n[mm]")
            if row_idx == len(tilt_tracks) - 1:
                ax.set_xlabel("[mm]")
            ax.text(
                0.03,
                0.97,
                f"P95 {p95:.0f} mm",
                transform=ax.transAxes,
                ha="left",
                va="top",
                bbox=dict(facecolor="white", edgecolor="0.75", alpha=0.88, boxstyle="round,pad=0.22"),
            )
            ax.set_aspect("equal", adjustable="box")
            ax.set_xlim(umin - upad, umax + upad)
            ax.set_ylim(vmin - vpad, vmax + vpad)
            ax.grid(alpha=0.25)
            if row_idx == 0 and col_idx == 0:
                ax.legend(loc="lower left", fontsize=12)
    fig.savefig(OUT_DIR / "01_roto_tilt_by_imu_3x3_plane_projection.png", dpi=230, bbox_inches="tight")
    plt.close(fig)

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

    fig, axes = plt.subplots(
        1,
        3,
        figsize=(15.8, 6.9),
        sharex=True,
        sharey=True,
        gridspec_kw={"wspace": 0.08},
    )
    for ax, (label, exp) in zip(axes, EXPERIMENTS.items()):
        fusion = fusions[label]
        row = metrics[metrics["label"] == label].iloc[0]
        ax.plot(b0["opti_x_mm"], b0["opti_z_mm"], color="black", linewidth=2.7, label="Vicon truth")
        ax.plot(b0["x_mm"], b0["z_mm"], color="#8cc8ef", linewidth=1.3, alpha=0.45, label="Pure UWB")
        ax.plot(fusion["x_mm"], fusion["z_mm"], color="#8b4cc2", linewidth=2.4, alpha=0.95, label="UWB+IMU")
        ax.set_title(
            f"{label}\n3D P95: {row.b0_err3d_p95_mm:.0f} -> {row.fusion_err3d_p95_mm:.0f} mm",
            fontsize=18,
            pad=8,
        )
        ax.grid(alpha=0.28)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlim(*xlim)
        ax.set_ylim(*zlim)
        ax.set_xlabel("X position [mm]", labelpad=6)
    axes[0].set_ylabel("Z position [mm]", labelpad=6)
    axes[0].legend(loc="best", fontsize=14)
    fig.suptitle(
        f"Spiky RotoArm track {CAPTURE_ID}/{TAG}: pure UWB has {int(np.sum(b0_jump > 200))} XZ jumps >200 mm",
        fontsize=20,
        y=0.985,
    )
    fig.tight_layout(rect=(0.01, 0.02, 0.995, 0.92), w_pad=0.6)
    fig.savefig(OUT_DIR / "01_R01_BS2DCE_same_track_XZ_L2_L16_L20.png", dpi=220)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(15, 6.2))
    t = b0["time_s"].to_numpy(float) - float(b0["time_s"].iloc[0])
    ax.plot(t, b0_err, color="#8cc8ef", alpha=0.55, linewidth=1.2, label=f"Pure UWB P95={pct(b0_err, 95):.0f} mm")
    colors = ["#5877c8", "#9c66cc", "#16a085"]
    for color, (label, _exp) in zip(colors, EXPERIMENTS.items()):
        fusion = fusions[label]
        tt = fusion["time_s"].to_numpy(float) - float(fusion["time_s"].iloc[0])
        fusion_err = err3d(fusion)
        ax.plot(tt, fusion_err, color=color, linewidth=2.2, alpha=0.95, label=f"{label} P95={pct(fusion_err, 95):.0f} mm")
    ax.axhline(200.0, color="#c0392b", linestyle="--", linewidth=2.0, alpha=0.95, label="200 mm threshold")
    ax.set_title(f"3D error over time: {CAPTURE_ID}/{TAG}", fontsize=20)
    ax.set_xlabel("time in capture (s)")
    ax.set_ylabel("3D error to Vicon [mm]")
    ax.grid(alpha=0.28)
    ax.legend(fontsize=14, ncols=2)
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
