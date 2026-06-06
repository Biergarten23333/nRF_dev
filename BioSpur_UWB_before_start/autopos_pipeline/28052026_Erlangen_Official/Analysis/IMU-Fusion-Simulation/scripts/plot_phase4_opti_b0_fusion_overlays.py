#!/usr/bin/env python3
"""Plot Opti/B0/Fusion overlays for selected Phase 4 position-fusion rows."""

from __future__ import annotations

import importlib.util
import math
import re
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

EXPERIMENTS = [
    "X_A0_U4_P4_L20_I3_T2",
    "X_A0_U4_P4_L20_I5_T2",
    "X_A0_U4_P0_L20_I5_T2",
    "X_A0_U4_P4_L16_I6_T4",
    "X_A0_U4_P4_L16_I5_T2",
    "X_A0_U4_P0_L16_I5_T2",
    "X_A0_U4_P4_L2_I5_T3",
]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


F = load_module(FACTORY_SCRIPT, "phase4_factory_for_overlay_plots")


def parse_experiment(exp: str) -> dict[str, str]:
    mo = re.fullmatch(r"X_(A\d+)_(U\d+)_(P\d+)_(L\d+)_(I\d+)_(T\d+)", exp)
    if not mo:
        raise ValueError(f"expected position-domain experiment ID, got {exp}")
    return {"A": mo.group(1), "U": mo.group(2), "P": mo.group(3), "L": mo.group(4), "I": mo.group(5), "T": mo.group(6)}


def pct(values: np.ndarray, q: float) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, q))


def err3d(df: pd.DataFrame, xcol: str, ycol: str, zcol: str) -> np.ndarray:
    xyz = df[[xcol, ycol, zcol]].to_numpy(float)
    opti = df[["opti_x_mm", "opti_y_mm", "opti_z_mm"]].to_numpy(float)
    return np.linalg.norm(xyz - opti, axis=1)


def reconstruct(exp: str, seed_id: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    parts = parse_experiment(exp)
    F._SEED_ID = seed_id
    F.install_sensor_props(parts["L"])
    streams = F.load_streams()
    b0 = streams[("A0", "U4", "P0")].copy()
    stream = streams[(parts["A"], parts["U"], parts["P"])].copy()
    imu = F.S1.simulate_imu_for_li(b0, f"phase4_{parts['L'].lower()}_full_{seed_id}_{parts['A']}", parts["L"], parts["I"])
    params = F.POSITION_T_PARAMS[parts["T"]]
    process = F.S1.li_process_factor(parts["L"], parts["I"])
    fusion = F.S1.position_fusion_samples(
        stream,
        imu,
        exp,
        str(params["deployability"]),
        f"Reconstructed overlay {exp} {seed_id}",
        float(params["prior_sigma_base"]) * process,
        float(params["measurement_sigma"]),
    )
    return b0, stream, fusion


def metrics_for_tracks(b0: pd.DataFrame, fusion: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (capture_id, tag), g in fusion.groupby(["capture_id", "tag"], sort=True):
        b = b0[(b0["capture_id"] == capture_id) & (b0["tag"] == tag)].sort_values("time_s")
        gg = g.sort_values("time_s")
        b0_e = err3d(b, "x_mm", "y_mm", "z_mm")
        f_e = err3d(gg, "x_mm", "y_mm", "z_mm")
        rows.append(
            {
                "capture_id": capture_id,
                "tag": tag,
                "b0_p95_mm": pct(b0_e, 95),
                "fusion_p95_mm": pct(f_e, 95),
                "improvement_p95_mm": pct(b0_e, 95) - pct(f_e, 95),
            }
        )
    return pd.DataFrame(rows).sort_values("improvement_p95_mm", ascending=False)


def setup_axis(ax, title: str) -> None:
    ax.set_title(title, fontsize=7)
    ax.grid(alpha=0.22, linewidth=0.5)
    ax.set_aspect("equal", adjustable="box")
    ax.tick_params(labelsize=6)


def plot_track(ax, b0: pd.DataFrame, fusion: pd.DataFrame, capture_id: str, tag: str, title: str, legend: bool = False) -> None:
    b = b0[(b0["capture_id"] == capture_id) & (b0["tag"] == tag)].sort_values("time_s")
    f = fusion[(fusion["capture_id"] == capture_id) & (fusion["tag"] == tag)].sort_values("time_s")
    ax.plot(b["opti_x_mm"], b["opti_z_mm"], color="black", linewidth=1.4, label="Opti")
    ax.plot(b["x_mm"], b["z_mm"], color="#74a9cf", linewidth=0.75, alpha=0.45, label="B0 P0 UWB")
    ax.plot(f["x_mm"], f["z_mm"], color="#8a4fbf", linewidth=1.15, alpha=0.9, label="Fusion")
    setup_axis(ax, title)
    if legend:
        ax.legend(fontsize=7, loc="best")


def contact_sheet(exp: str, b0: pd.DataFrame, fusion: pd.DataFrame, metrics: pd.DataFrame, out: Path) -> None:
    tracks = list(fusion.groupby(["capture_id", "tag"], sort=True).groups)
    ncols = 4
    nrows = int(math.ceil(len(tracks) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(15, 3.1 * nrows))
    axes = np.asarray(axes).reshape(-1)
    metric_by_key = {(r.capture_id, r.tag): r for r in metrics.itertuples()}
    for idx, (capture_id, tag) in enumerate(tracks):
        row = metric_by_key[(capture_id, tag)]
        title = f"{capture_id}/{tag} B0P95={row.b0_p95_mm:.0f} F={row.fusion_p95_mm:.0f} d={row.improvement_p95_mm:.0f}"
        plot_track(axes[idx], b0, fusion, capture_id, tag, title, legend=(idx == 0))
    for ax in axes[len(tracks) :]:
        ax.axis("off")
    fig.suptitle(f"{exp} seed S00: Opti vs B0 vs Fusion (horizontal X-Z plane)", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.985))
    fig.savefig(out, dpi=180)
    plt.close(fig)


def selected_sheet(exp: str, b0: pd.DataFrame, fusion: pd.DataFrame, metrics: pd.DataFrame, out: Path) -> None:
    best = metrics.head(4)
    worst = metrics.tail(4)
    selected = pd.concat([best, worst], ignore_index=True)
    fig, axes = plt.subplots(2, 4, figsize=(16, 8))
    axes = axes.reshape(-1)
    for idx, row in selected.iterrows():
        title = f"{row.capture_id}/{row.tag}\\nB0P95={row.b0_p95_mm:.0f}, Fusion={row.fusion_p95_mm:.0f}, d={row.improvement_p95_mm:.0f}"
        plot_track(axes[idx], b0, fusion, row.capture_id, row.tag, title, legend=(idx == 0))
    fig.suptitle(f"{exp} seed S00: 4 strongest improvements + 4 weakest improvements", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out, dpi=180)
    plt.close(fig)


def main() -> None:
    seed_id = "S00"
    latest = sorted((SIM_ROOT / "runs" / "phase4_analysis").glob("l2_l16_l20_truefull_5seed_opti_truth_*"))[-1]
    out_dir = latest / "figs" / "opti_b0_fusion_overlays"
    table_dir = latest / "tables" / "opti_b0_fusion_overlays"
    report_dir = latest / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    table_dir.mkdir(parents=True, exist_ok=True)

    report = [
        "# Opti / B0 / Fusion Overlay Plots",
        "",
        f"Generated UTC: `{datetime.now(UTC).isoformat()}`",
        "",
        "Coordinate note: plots use the official horizontal `X-Z` plane. Official vertical axis is `Y`.",
        "",
        "B0 is `A0/U4/P0/T1`, pure UWB with no IMU fusion.",
        "",
    ]
    for exp in EXPERIMENTS:
        b0, _stream, fusion = reconstruct(exp, seed_id)
        metrics = metrics_for_tracks(b0, fusion)
        metrics.to_csv(table_dir / f"{exp}_S00_track_overlay_metrics.csv", index=False)
        contact = out_dir / f"{exp}_S00_Opti_B0_Fusion_contact_sheet.png"
        selected = out_dir / f"{exp}_S00_Opti_B0_Fusion_selected_tracks.png"
        contact_sheet(exp, b0, fusion, metrics, contact)
        selected_sheet(exp, b0, fusion, metrics, selected)
        report.extend(
            [
                f"## `{exp}`",
                "",
                f"- mean track B0 P95: {metrics['b0_p95_mm'].mean():.1f} mm",
                f"- mean track Fusion P95: {metrics['fusion_p95_mm'].mean():.1f} mm",
                f"- mean track P95 improvement: {metrics['improvement_p95_mm'].mean():.1f} mm",
                f"- contact sheet: `figs/opti_b0_fusion_overlays/{contact.name}`",
                f"- selected tracks: `figs/opti_b0_fusion_overlays/{selected.name}`",
                "",
            ]
        )

    report_path = report_dir / "PHASE4_OPTI_B0_FUSION_OVERLAYS.md"
    report_path.write_text("\n".join(report), encoding="utf-8")
    print(report_path)


if __name__ == "__main__":
    main()
