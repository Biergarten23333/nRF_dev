"""Static, non-persuasive Root-R5A diagnostic plots from machine payloads."""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


META = {"Software": "biospur-root-r5a", "Title": "Internal diagnostic; no external truth"}


def _save(fig, path: Path):
    fig.savefig(path, dpi=140, bbox_inches="tight", metadata=META); plt.close(fig)


def profile_plot(output: Path, layer: str, profiles: list[dict]) -> Path:
    fig, ax = plt.subplots(figsize=(10, 5.5))
    for profile in profiles[:5]:
        ax.plot(profile["grid_yaw_deg"], profile["normalized_delta_objective"],
                label=profile["block"], linewidth=1.2)
    ax.set(xlabel="yaw of R_V4_from_N (deg)", ylabel="normalized robust profile Δ objective",
           title=f"{layer} circular yaw profiles — internal C1 diagnostic")
    ax.set_xlim(-180, 180); ax.set_ylim(bottom=0); ax.grid(alpha=.25); ax.legend(ncol=3)
    path = output / f"{layer}_CIRCULAR_YAW_PROFILES.png"; _save(fig, path); return path


def block_plot(output: Path, t4: list[dict], raw: list[dict]) -> Path:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    blocks = np.arange(5)
    for profiles, label, marker in ((t4, "T4", "o"), (raw, "raw", "s")):
        ax1.plot(blocks, [row["global_mode_deg"] for row in profiles[:5]], marker=marker, label=label)
        ax2.plot(blocks, [row["asymptotic_profile_interval_width_95_deg"] for row in profiles[:5]], marker=marker, label=label)
    ax1.set(ylabel="profile mode (deg)", title="Block modes and internal profile widths; not external accuracy")
    ax2.set(xlabel="predeclared Root-R4 time block", ylabel="local 95% pseudo-profile width (deg)")
    for ax in (ax1, ax2): ax.grid(alpha=.25); ax.legend()
    path = output / "BLOCK_MODES_AND_INTERVAL_WIDTHS.png"; _save(fig, path); return path


def timing_plot(output: Path, timing: dict) -> Path:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    offsets = np.asarray([row["offset_s"] for row in timing["rows"]]) * 1000
    for layer in ("T4", "RAW"):
        ax1.plot(offsets, [row["layers"][layer]["combined_mode_deg"] for row in timing["rows"]], marker="o", label=layer)
        ax2.plot(offsets, [row["layers"][layer]["block_mode_range_deg"] for row in timing["rows"]], marker="o", label=layer)
    ax1.set(ylabel="combined yaw mode (deg)", title="Physically bounded single global UWB–IMU offset")
    ax2.set(xlabel="global offset applied to frozen M1 geometry (ms)", ylabel="five-block mode range (deg)")
    for ax in (ax1, ax2): ax.grid(alpha=.25); ax.legend()
    path = output / "TIME_OFFSET_PROFILE.png"; _save(fig, path); return path


def cross_validation_plot(output: Path, comparison: dict) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharey=True)
    for ax, layer in zip(axes, ("T4", "RAW")):
        folds = comparison[layer]["reference_prior"]["folds"]; x = np.arange(5); width = .35
        ax.bar(x - width/2, [row["held_constant_score"] for row in folds], width, label="constant")
        ax.bar(x + width/2, [row["held_dynamic_score"] for row in folds], width, label="dynamic")
        ax.set(title=layer, xlabel="held block", ylabel="normalized held profile score"); ax.grid(axis="y", alpha=.25); ax.legend()
    fig.suptitle("Constant versus constrained dynamic held-block performance")
    path = output / "CONSTANT_VS_DYNAMIC_HELD_BLOCK.png"; _save(fig, path); return path


def trajectory_plot(output: Path, rows: list[dict]) -> Path:
    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    for layer in ("T4", "RAW"):
        selected = [row for row in rows if row["layer"] == layer]
        axes[0].plot([row["time_s"] for row in selected], [row["delta_psi_root_deg"] for row in selected], marker="o", label=layer)
        axes[1].plot([row["time_s"] for row in selected], [row["b_g_root_dps"] for row in selected], marker="o", label=layer)
    axes[0].set(ylabel="diagnostic δψ_root (deg)", title="Gauge-fixed diagnostic yaw-error state; not a fused trajectory")
    axes[1].set(xlabel="block-centre time (s)", ylabel="diagnostic root gyro bias (deg/s)")
    for ax in axes: ax.grid(alpha=.25); ax.legend()
    path = output / "DYNAMIC_YAW_AND_GYRO_BIAS.png"; _save(fig, path); return path


def loo_plot(output: Path, tag: dict, anchor: dict) -> Path:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 8))
    x = np.arange(len(tag["rows"])); ax1.plot(x, [row["t4_mode_deg"] for row in tag["rows"]], "o-", label="T4")
    ax1.plot(x, [row["raw_mode_deg"] for row in tag["rows"]], "s-", label="raw")
    ax1.set_xticks(x, [row["omitted_tag"] for row in tag["rows"]], rotation=35, ha="right"); ax1.set(ylabel="yaw mode (deg)", title="Tag leave-one-out")
    y = np.arange(len(anchor["rows"])); ax2.plot(y, [row["raw_mode_deg"] for row in anchor["rows"]], "o-")
    ax2.set_xticks(y, [str(row["omitted_anchor"]) for row in anchor["rows"]]); ax2.set(xlabel="omitted anchor", ylabel="raw yaw mode (deg)", title="Raw anchor leave-one-out")
    for ax in (ax1, ax2): ax.grid(alpha=.25); ax.legend() if ax is ax1 else None
    path = output / "TAG_AND_ANCHOR_LOO_STABILITY.png"; _save(fig, path); return path


def motion_quality_plot(output: Path, motion: list[dict], quality: list[dict]) -> Path:
    fig, ax1 = plt.subplots(figsize=(9, 5.5)); blocks = np.arange(5)
    ax2 = ax1.twinx()
    ax1.plot(blocks, [row["median_fk_point_speed_mps"] for row in motion], "o-", color="#1769aa", label="motion")
    ax2.plot(blocks, [row["median_abs_canonical_residual_m"] for row in quality], "s-", color="#c62828", label="UWB residual")
    ax1.set(xlabel="predeclared block", ylabel="median FK point speed (m/s)", title="Motion informativeness and UWB quality are separate quantities")
    ax2.set_ylabel("median |canonical range residual| (m)"); ax1.grid(alpha=.25)
    lines = ax1.lines + ax2.lines; ax1.legend(lines, [line.get_label() for line in lines])
    path = output / "MOTION_INFORMATIVENESS_VS_UWB_QUALITY.png"; _save(fig, path); return path


def all_plots(output: Path, t4: list[dict], raw: list[dict], timing: dict, comparison: dict,
              trajectory: list[dict], tag: dict, anchor: dict, motion: list[dict], quality: list[dict]) -> list[str]:
    paths = [profile_plot(output, "T4", t4), profile_plot(output, "RAW", raw), block_plot(output, t4, raw),
             timing_plot(output, timing), cross_validation_plot(output, comparison), trajectory_plot(output, trajectory),
             loo_plot(output, tag, anchor), motion_quality_plot(output, motion, quality)]
    return [path.name for path in paths]
