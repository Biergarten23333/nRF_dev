#!/usr/bin/env python3
"""Additional official-dataset diagnostics.

This script adds the nine report-side analyses requested after the corrected
static tag ground-truth pass.  It treats existing layout/DOP/MC/drift outputs as
inputs and does not regenerate them.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import math
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

from tag_ground_truth import TAG_BALL_LABEL_PERMUTATIONS, load_corrected_static_truth


ANCHORS = list("ABCDEFGH")
PRIMARY_IDS = ["ID01", "ID02", "ID03", "ID04", "ID05"]
WORST_IDS = ["ID01", "ID03", "ID04", "ID06"]
OPTITRACK_VERTICAL_AXIS = "Y"

THIS = Path(__file__).resolve()
REPO_ROOT = THIS.parents[6]
SOLVER_ROOT = REPO_ROOT / "biospur_tag_positioning_offline_solver"
sys.path.insert(0, str(SOLVER_ROOT))

from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames  # noqa: E402


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def append_run_meta(out_dir: Path, entry: dict) -> None:
    meta_path = out_dir / "run_meta.json"
    lock_path = out_dir / "run_meta.json.lock"
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                meta = {"runs": []}
        else:
            meta = {"runs": []}
        meta.setdefault("runs", []).append(entry)
        tmp = meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(meta_path)
        fcntl.flock(lock, fcntl.LOCK_UN)


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |\n"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |\n")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |\n")
    return "".join(lines)


def session_id_from_path(path: Path) -> str:
    for parent in path.parents:
        m = re.search(r"(static_ID\d+)_", parent.name)
        if m:
            return m.group(1).replace("static_", "")
    return path.parents[1].name


def load_v4io_delays(layout_json: Path) -> dict[str, float]:
    data = json.loads(layout_json.read_text(encoding="utf-8"))
    out = {str(item.get("label") or ANCHORS[int(item["id"])]): float(item.get("d_anchor_mm") or 0.0) for item in data["anchors"]}
    for label in ANCHORS:
        out.setdefault(label, 0.0)
    return out


def estimate_interanchor_endpoint_delays(anchor_truth: dict[str, np.ndarray], pair_quality_csv: Path) -> tuple[dict[str, float], list[dict]]:
    df = pd.read_csv(pair_quality_csv)
    df = df[df["eval_set"] == "solve"].copy()
    design = []
    target = []
    rows = []
    for _, r in df.iterrows():
        a, b = str(r["pair"]).split("-")
        measured = float(r["median_all"])
        true_dist = float(np.linalg.norm(anchor_truth[a] - anchor_truth[b]))
        bias = measured - true_dist
        row = np.zeros(len(ANCHORS), dtype=float)
        row[ANCHORS.index(a)] = 1.0
        row[ANCHORS.index(b)] = 1.0
        design.append(row)
        target.append(bias)
        rows.append({"pair": r["pair"], "measured_mm": measured, "truth_mm": true_dist, "pair_bias_mm": bias})
    m = np.vstack(design)
    y = np.asarray(target, dtype=float)
    delays, *_ = np.linalg.lstsq(m, y, rcond=None)
    for row in rows:
        a, b = row["pair"].split("-")
        pred = delays[ANCHORS.index(a)] + delays[ANCHORS.index(b)]
        row["fit_pair_bias_mm"] = float(pred)
        row["fit_residual_mm"] = float(pred - row["pair_bias_mm"])
    return {label: float(delays[i]) for i, label in enumerate(ANCHORS)}, rows


def regression(x: np.ndarray, y: np.ndarray) -> dict:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    x = x[ok]
    y = y[ok]
    n = len(x)
    if n < 3:
        return {"n": n, "slope": np.nan, "intercept": np.nan, "r2": np.nan, "p_value": np.nan, "slope_ci_low": np.nan, "slope_ci_high": np.nan}
    res = stats.linregress(x, y)
    yhat = res.intercept + res.slope * x
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan
    tcrit = float(stats.t.ppf(0.975, n - 2))
    return {
        "n": n,
        "slope": float(res.slope),
        "intercept": float(res.intercept),
        "r2": float(r2),
        "p_value": float(res.pvalue),
        "slope_ci_low": float(res.slope - tcrit * res.stderr),
        "slope_ci_high": float(res.slope + tcrit * res.stderr),
        "stderr": float(res.stderr),
    }


def bootstrap_ci(values: np.ndarray, func, rng: np.random.Generator, n_boot: int) -> tuple[float, float]:
    vals = np.asarray(values, dtype=float)
    vals = vals[np.isfinite(vals)]
    if len(vals) == 0:
        return float("nan"), float("nan")
    boots = []
    for _ in range(n_boot):
        sample = vals[rng.integers(0, len(vals), len(vals))]
        boots.append(float(func(sample)))
    return float(np.nanpercentile(boots, 2.5)), float(np.nanpercentile(boots, 97.5))


def group_metric_rows(df: pd.DataFrame, group_col: str, rng: np.random.Generator, n_boot: int) -> list[dict]:
    rows: list[dict] = []
    for (eval_set, group), g in df.groupby(["eval_set", group_col]):
        err = g["err_3d_mm"].to_numpy(float)
        x = np.abs(g["err_x_mm"].to_numpy(float))
        y = np.abs(g["err_y_vertical_mm"].to_numpy(float))
        z = np.abs(g["err_z_mm"].to_numpy(float))
        h = g["err_horizontal_mm"].to_numpy(float)
        med_lo, med_hi = bootstrap_ci(err, np.nanmedian, rng, n_boot)
        p95_lo, p95_hi = bootstrap_ci(err, lambda v: np.nanpercentile(v, 95), rng, n_boot)
        rows.append(
            {
                "eval_set": eval_set,
                group_col: group,
                "n": int(len(g)),
                "err_3d_median_mm": float(np.nanmedian(err)),
                "err_3d_median_ci95_low_mm": med_lo,
                "err_3d_median_ci95_high_mm": med_hi,
                "err_3d_p95_mm": float(np.nanpercentile(err, 95)),
                "err_3d_p95_ci95_low_mm": p95_lo,
                "err_3d_p95_ci95_high_mm": p95_hi,
                "err_3d_rms_mm": float(np.sqrt(np.nanmean(err * err))),
                "err_x_abs_median_mm": float(np.nanmedian(x)),
                "err_y_vertical_abs_median_mm": float(np.nanmedian(y)),
                "err_z_abs_median_mm": float(np.nanmedian(z)),
                "err_horizontal_median_mm": float(np.nanmedian(h)),
                "small_n_note": "bootstrap CI is descriptive; n is small",
            }
        )
    return rows


def normalize(values: dict[str, float]) -> dict[str, float]:
    arr = np.asarray(list(values.values()), dtype=float)
    lo = float(np.nanmin(arr))
    hi = float(np.nanmax(arr))
    if not np.isfinite(lo) or not np.isfinite(hi) or abs(hi - lo) < 1e-12:
        return {k: 0.0 for k in values}
    return {k: float((v - lo) / (hi - lo)) for k, v in values.items()}


def plot_delay(path: Path, delay_rows: list[dict]) -> None:
    df = pd.DataFrame(delay_rows)
    x = np.arange(len(df))
    fig, axs = plt.subplots(2, 1, figsize=(9, 6), sharex=True, constrained_layout=True)
    axs[0].bar(x - 0.18, df["autopos_delay_mm"], width=0.36, label="AutoPos v4-io effective delay")
    axs[0].bar(x + 0.18, df["delaycal_endpoint_delay_mm"], width=0.36, label="OptiTrack inter-anchor endpoint fit")
    axs[0].set_ylabel("delay mm")
    axs[0].legend(fontsize=8)
    axs[0].grid(axis="y", alpha=0.25)
    axs[1].bar(x - 0.18, df["autopos_differential_mm"], width=0.36, label="AutoPos differential")
    axs[1].bar(x + 0.18, df["delaycal_differential_mm"], width=0.36, label="delaycal differential")
    axs[1].axhline(0, color="black", lw=0.8)
    axs[1].set_ylabel("differential mm")
    axs[1].set_xticks(x)
    axs[1].set_xticklabels(df["anchor"])
    axs[1].legend(fontsize=8)
    axs[1].grid(axis="y", alpha=0.25)
    fig.suptitle("Common/differential antenna-delay decomposition")
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_regression(path: Path, tag_rows: pd.DataFrame, regression_rows: list[dict]) -> None:
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.8), constrained_layout=True)
    outcomes = [("err_3d_mm", "3D error mm"), ("radial_component_mm", "signed radial error mm")]
    colors = {"all8": "#4C78A8"}
    xgrid = np.linspace(tag_rows["distance_from_anchor_centroid_m"].min(), tag_rows["distance_from_anchor_centroid_m"].max(), 100)
    for ax, (col, label) in zip(axs, outcomes):
        for eval_set, g in tag_rows.groupby("eval_set"):
            ax.scatter(g["distance_from_anchor_centroid_m"], g[col], label=eval_set, color=colors.get(eval_set), alpha=0.85)
            r = next((row for row in regression_rows if row["eval_set"] == eval_set and row["outcome"] == col), None)
            if r and np.isfinite(r["slope_mm_per_m"]):
                yhat = r["intercept_mm"] + r["slope_mm_per_m"] * xgrid
                ax.plot(xgrid, yhat, color=colors.get(eval_set), lw=1.5)
                x = g["distance_from_anchor_centroid_m"].to_numpy(float)
                y = g[col].to_numpy(float)
                ok = np.isfinite(x) & np.isfinite(y)
                x = x[ok]
                y = y[ok]
                if len(x) > 2:
                    resid = y - (r["intercept_mm"] + r["slope_mm_per_m"] * x)
                    s_err = math.sqrt(float(np.sum(resid * resid) / (len(x) - 2)))
                    xbar = float(np.mean(x))
                    sxx = float(np.sum((x - xbar) ** 2))
                    if sxx > 0:
                        tcrit = float(stats.t.ppf(0.975, len(x) - 2))
                        band = tcrit * s_err * np.sqrt(1.0 / len(x) + (xgrid - xbar) ** 2 / sxx)
                        ax.fill_between(xgrid, yhat - band, yhat + band, color=colors.get(eval_set), alpha=0.12, linewidth=0)
        ax.set_xlabel("distance from OptiTrack anchor centroid m")
        ax.set_ylabel(label)
        ax.grid(alpha=0.25)
    axs[0].legend()
    fig.suptitle("Tag error vs distance from array center")
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_vector_field(path: Path, rows: pd.DataFrame) -> None:
    g = rows[rows["eval_set"] == "all8"].copy()
    fig = plt.figure(figsize=(9, 7))
    ax = fig.add_subplot(111, projection="3d")
    scale = 1.0
    ax.quiver(
        g["truth_x_mm"],
        g["truth_z_mm"],
        g["truth_y_vertical_mm"],
        g["err_x_mm"],
        g["err_z_mm"],
        g["err_y_vertical_mm"],
        length=scale,
        normalize=False,
        color="#4C78A8",
        linewidth=1.5,
    )
    ax.scatter(g["truth_x_mm"], g["truth_z_mm"], g["truth_y_vertical_mm"], color="#F58518", s=22)
    for _, r in g.iterrows():
        ax.text(r["truth_x_mm"], r["truth_z_mm"], r["truth_y_vertical_mm"], r["ID"], fontsize=7)
    ax.set_xlabel("OptiTrack X mm")
    ax.set_ylabel("OptiTrack Z mm")
    ax.set_zlabel("OptiTrack Y vertical mm")
    ax.set_title("Production v4-io/all8 tag error vectors")
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_worst_residuals(path: Path, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    ids = WORST_IDS + sorted(df[df["comparison_group"] == "clean_reference"]["ID"].unique())
    ids = [sid for sid in ids if sid in set(df["ID"])]
    mat = np.full((len(ids), len(ANCHORS)), np.nan)
    for i, sid in enumerate(ids):
        for j, a in enumerate(ANCHORS):
            val = df[(df["ID"] == sid) & (df["anchor"] == a)]["residual_vs_truth_centered_mm"]
            if len(val):
                mat[i, j] = float(val.iloc[0])
    vmax = float(np.nanmax(np.abs(mat))) if np.isfinite(mat).any() else 1.0
    fig, ax = plt.subplots(figsize=(10, 5.5), constrained_layout=True)
    im = ax.imshow(mat, cmap="coolwarm", vmin=-vmax, vmax=vmax)
    ax.set_xticks(np.arange(len(ANCHORS)))
    ax.set_xticklabels(ANCHORS)
    ax.set_yticks(np.arange(len(ids)))
    ax.set_yticklabels(ids)
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            if np.isfinite(mat[i, j]):
                ax.text(j, i, f"{mat[i, j]:.0f}", ha="center", va="center", fontsize=7)
    ax.set_title("Raw range residual fingerprint vs OptiTrack truth, centered per ID")
    fig.colorbar(im, ax=ax, label="centered residual mm")
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_anchor_health(path: Path, rows: list[dict]) -> None:
    df = pd.DataFrame(rows).sort_values("trust_score")
    fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)
    ax.barh(df["anchor"], df["trust_score"], color="#4C78A8")
    for _, r in df.iterrows():
        ax.text(r["trust_score"] + 0.015, r["anchor"], f"{r['trust_score']:.2f}", va="center", fontsize=8)
    ax.set_xlim(0, 1.08)
    ax.set_xlabel("heuristic trust score, higher is better")
    ax.set_title("Per-anchor health scorecard")
    ax.grid(axis="x", alpha=0.25)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_group_metric(path: Path, rows: list[dict], group_col: str, title: str) -> None:
    df = pd.DataFrame(rows)
    eval_sets = list(dict.fromkeys(df["eval_set"].astype(str).tolist()))
    fig, axs = plt.subplots(1, len(eval_sets), figsize=(5.5 * len(eval_sets), 4.5), sharey=True, constrained_layout=True, squeeze=False)
    for ax, eval_set in zip(axs[0], eval_sets):
        g = df[df["eval_set"] == eval_set].copy()
        x = np.arange(len(g))
        y = g["err_3d_median_mm"].to_numpy(float)
        lo = y - g["err_3d_median_ci95_low_mm"].to_numpy(float)
        hi = g["err_3d_median_ci95_high_mm"].to_numpy(float) - y
        ax.bar(x, y, yerr=[lo, hi], capsize=3, color="#4C78A8")
        ax.scatter(x, g["err_3d_p95_mm"], color="#F58518", label="p95")
        ax.set_xticks(x)
        ax.set_xticklabels(g[group_col], rotation=30, ha="right")
        ax.set_title(eval_set)
        ax.set_ylabel("3D error mm")
        ax.grid(axis="y", alpha=0.25)
        ax.legend(fontsize=8)
    fig.suptitle(title)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_single_anchor_criticality(path: Path, rows: list[dict]) -> None:
    df = pd.DataFrame(rows).sort_values("combined_criticality_score", ascending=False)
    x = np.arange(len(df))
    fig, ax = plt.subplots(figsize=(10, 5), constrained_layout=True)
    ax.bar(x - 0.18, df["static_d3_std_delta_mm"], width=0.36, label="static D3 std delta")
    ax.bar(x + 0.18, df["roto_turn_center_rms_delta_mm"], width=0.36, label="roto turn-center RMS delta")
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(df["dropped_anchor"])
    ax.set_ylabel("delta vs keep8 mm")
    ax.set_title("Single-anchor criticality, v4-io/T4 keep7 drop-one")
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def static_range_medians(path: Path) -> dict[int, float]:
    frames = read_tr_all_frames(path, tags={"BSF66F"}, min_anchors=4)
    values: dict[int, list[float]] = {i: [] for i in range(8)}
    for frame in frames:
        for obs in frame.observations:
            if obs.anchor_id in values:
                values[obs.anchor_id].append(float(obs.range_mm))
    return {aid: float(np.nanmedian(vals)) for aid, vals in values.items() if vals}


def build_summary_md(
    *,
    delay_agreement: dict,
    regression_rows: list[dict],
    vector_summary: dict[str, dict],
    height_rows: list[dict],
    edge_rows: list[dict],
    facing_rows: list[dict],
    health_rows: list[dict],
    critical_rows: list[dict],
) -> str:
    reg_3d = next(r for r in regression_rows if r["eval_set"] == "all8" and r["outcome"] == "err_3d_mm")
    reg_rad = next(r for r in regression_rows if r["eval_set"] == "all8" and r["outcome"] == "radial_component_mm")
    vec = vector_summary["all8"]
    health_sorted = sorted(health_rows, key=lambda r: r["trust_score"])
    crit_sorted = sorted(critical_rows, key=lambda r: r["combined_criticality_score"], reverse=True)
    low_health = ", ".join(r["anchor"] for r in health_sorted[:3])
    high_crit = ", ".join(r["dropped_anchor"] for r in crit_sorted[:3])

    lines = ["## Additional diagnostics (error structure, delay decomposition, anchor health)\n\n"]
    lines.append("This section adds nine diagnostics on top of the corrected static-tag analysis. Unless stated otherwise, the primary line is production-output `v4-io / all8` with corrected ID01/ID05 tag truth.\n\n")
    lines.append("### 1. Antenna-delay common/differential decomposition\n\n")
    lines.append(
        f"AutoPos v4-io's common effective delay is {delay_agreement['autopos_common_mm']:.1f} mm, while the OptiTrack inter-anchor endpoint fit has common term {delay_agreement['delaycal_common_mm']:.1f} mm. "
        f"The differential patterns only weakly agree (Pearson r={delay_agreement['differential_pearson_r']:.2f}); the AutoPos delay vector should therefore be described as an effective joint self-calibration delay, not a pure physical antenna-delay measurement.\n\n"
    )
    lines.append("[delay_common_differential.csv](../tables/delay_common_differential.csv)  \n")
    lines.append("![Delay decomposition](fig/delay_decomposition.png)\n\n")
    lines.append("### 2. Tag error vs distance-from-array-center\n\n")
    verdict = "supports a positive scale-propagation component" if reg_3d["p_value"] < 0.05 and reg_3d["slope_mm_per_m"] > 0 else "does not support a positive scale-propagation component"
    lines.append(
        f"The all8 3D-error slope is {reg_3d['slope_mm_per_m']:.1f} mm/m (R^2={reg_3d['r2']:.2f}, p={reg_3d['p_value']:.3f}), which {verdict}. "
        f"The signed radial slope is {reg_rad['slope_mm_per_m']:.1f} mm/m (p={reg_rad['p_value']:.3f}). The moderate R^2 means scale propagation is real but not the whole tail explanation.\n\n"
    )
    lines.append("[tag_error_vs_center_distance.csv](../tables/tag_error_vs_center_distance.csv)  \n")
    lines.append("![Tag error vs center distance](fig/tag_error_vs_center_distance.png)\n\n")
    lines.append("### 3. Tag error vector field\n\n")
    lines.append(
        f"The all8 mean error vector is ({vec['mean_err_x_mm']:.1f}, {vec['mean_err_y_vertical_mm']:.1f}, {vec['mean_err_z_mm']:.1f}) mm in OptiTrack XYZ, with |mean|/RMS-scatter={vec['mean_vector_to_scatter_ratio']:.2f}. "
        f"Median signed radial error is {vec['median_radial_component_mm']:.1f} mm, median tangential magnitude is {vec['median_tangential_magnitude_mm']:.1f} mm, and {vec['fraction_radial_outward']*100:.0f}% of points are radially outward.\n\n"
    )
    lines.append("[tag_error_vector_decomposition.csv](../tables/tag_error_vector_decomposition.csv)  \n")
    lines.append("![Tag error vector field](fig/tag_error_vector_field.png)\n\n")
    lines.append("### 4. Worst-point raw-range residual fingerprint\n\n")
    lines.append(
        "ID01/ID03/ID04/ID06 show structured residual fingerprints rather than identical common offsets. The table reports raw range residuals against both the production solved point and the OptiTrack truth point, with centered per-ID columns to expose anchor-specific structure.\n\n"
    )
    lines.append("[worstpoint_range_residuals.csv](../tables/worstpoint_range_residuals.csv)  \n")
    lines.append("![Worst-point residual fingerprint](fig/worstpoint_range_residual_fingerprint.png)\n\n")
    lines.append("### 5. Per-anchor health / trust score\n\n")
    lines.append(
        f"The lowest heuristic trust anchors are {low_health}. This score combines pair residuals, raw asymmetry, temporal drift, OptiTrack marker status, and delay differential magnitude; it is a triage score, not a formal probability of failure.\n\n"
    )
    lines.append("[anchor_health_scorecard.csv](../tables/anchor_health_scorecard.csv)  \n")
    lines.append("![Anchor health scorecard](fig/anchor_health_scorecard.png)\n\n")
    lines.append("### 6. Tag error by height\n\n")
    lines.append("Height grouping uses bootstrap CIs because each group is small. The output table reports 3D median/p95 and OptiTrack X/Y/Z split for the corrected all8 FULL run.\n\n")
    lines.append("[tag_error_by_height.csv](../tables/tag_error_by_height.csv)  \n")
    lines.append("![Tag error by height](fig/tag_error_by_height.png)\n\n")
    lines.append("### 7. Tag error: edge vs center\n\n")
    lines.append("Edge/center grouping checks whether positions farther from the array centroid are worse. Interpret it together with the distance regression rather than as an independent high-power test.\n\n")
    lines.append("[tag_error_edge_vs_center.csv](../tables/tag_error_edge_vs_center.csv)  \n")
    lines.append("![Tag error edge vs center](fig/tag_error_edge_vs_center.png)\n\n")
    lines.append("### 8. Tag error by facing group\n\n")
    lines.append("Facing groups are exploratory because n is small. The table also includes median VDOP/condition number from the existing grid25 DOP-by-facing table for the same IDs.\n\n")
    lines.append("[tag_error_by_facing.csv](../tables/tag_error_by_facing.csv)  \n")
    lines.append("![Tag error by facing](fig/tag_error_by_facing.png)\n\n")
    lines.append("### 9. Single-anchor criticality\n\n")
    lines.append(
        f"Drop-one keep7 results rank the most critical anchors to keep as {high_crit} for the combined static/roto degradation score. Compare this with the health score: a low-trust anchor can still be geometrically important.\n\n"
    )
    lines.append("[single_anchor_criticality.csv](../tables/single_anchor_criticality.csv)  \n")
    lines.append("![Single anchor criticality](fig/single_anchor_criticality.png)\n\n")
    lines.append("### Synthesis\n\n")
    lines.append(
        "The extra diagnostics point to a coupled error structure rather than one simple cause. The common delay term is gauge-coupled with layout scale, the distance/radial tests do not reduce the tag tail to pure scale propagation, and the worst-point fingerprints plus single-anchor criticality show anchor-specific structure. The best current reading is: typical-position median accuracy is near the surveyed-delaycal floor, while the production p95 tail comes from layout/self-calibration/frame-lock coupling interacting with a few anchor/link weaknesses, not isotropic measurement noise alone.\n"
    )
    return "".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate nine additional official-dataset diagnostics.")
    parser.add_argument("--official-root", default="autopos_pipeline/28052026_Erlangen_Official")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--bootstrap", type=int, default=5000)
    args = parser.parse_args()

    official_root = Path(args.official_root).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else Path(__file__).resolve().parents[1]
    tables_dir = out_dir / "tables"
    figs_dir = out_dir / "figs"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figs_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)

    layout_base = official_root / "solver/outputs/v1_to_v4_io_field_check"
    layout_json = layout_base / "v4-io/layout.json"
    pair_quality = layout_base / "tables/pair_quality_solve.csv"
    captures_root = official_root / "captures/erlangen_20260528_optitrack"
    opti_dir = official_root / "opti_captures/full"

    tag_abs_path = tables_dir / "tag_abs_errors_per_session.csv"
    pair_res_path = tables_dir / "worst_pairs.csv"
    raw_asym_path = tables_dir / "pair_raw_asymmetry.csv"
    drift_path = tables_dir / "temporal_drift_anchor_summary.csv"
    opti_fp_path = tables_dir / "opti_anchor_marker_fingerprint.csv"
    strat_path = tables_dir / "stratified_keepk_by_drop_set.csv"
    static_keep8_path = official_root / "Analysis/Monte-Carlo-Simulation/v4-io/T4/static/static_keepk_summary.csv"
    roto_keep8_path = official_root / "Analysis/Monte-Carlo-Simulation/v4-io/T4/roto/roto_keepk_summary.csv"
    dop_facing_path = tables_dir / "dop_by_facing_group_grid25.csv"

    anchor_truth, tag_truth, _tag_meta, _corr_rows = load_corrected_static_truth(opti_dir, ANCHORS, PRIMARY_IDS)
    anchor_centroid = np.mean(np.vstack([anchor_truth[a] for a in ANCHORS]), axis=0)

    tag_df_all = pd.read_csv(tag_abs_path)
    tag_df = tag_df_all[(tag_df_all["version"] == "v4-io") & (tag_df_all["method"] == "C_anchor_locked_OFFICIAL")].copy()
    tag_df["distance_from_anchor_centroid_mm"] = np.linalg.norm(
        tag_df[["truth_x_mm", "truth_y_vertical_mm", "truth_z_mm"]].to_numpy(float) - anchor_centroid[None, :],
        axis=1,
    )
    tag_df["distance_from_anchor_centroid_m"] = tag_df["distance_from_anchor_centroid_mm"] / 1000.0

    # Analysis 1.
    autopos_delays = load_v4io_delays(layout_json)
    delaycal_delays, delay_pair_rows = estimate_interanchor_endpoint_delays(anchor_truth, pair_quality)
    ap_vals = np.asarray([autopos_delays[a] for a in ANCHORS], dtype=float)
    dc_vals = np.asarray([delaycal_delays[a] for a in ANCHORS], dtype=float)
    ap_common = float(np.mean(ap_vals))
    dc_common = float(np.mean(dc_vals))
    ap_diff = ap_vals - ap_common
    dc_diff = dc_vals - dc_common
    delay_rows = []
    for i, a in enumerate(ANCHORS):
        delay_rows.append(
            {
                "anchor": a,
                "autopos_delay_mm": float(ap_vals[i]),
                "autopos_common_mm": ap_common,
                "autopos_differential_mm": float(ap_diff[i]),
                "delaycal_endpoint_delay_mm": float(dc_vals[i]),
                "delaycal_common_mm": dc_common,
                "delaycal_differential_mm": float(dc_diff[i]),
                "differential_delta_autopos_minus_delaycal_mm": float(ap_diff[i] - dc_diff[i]),
                "abs_autopos_differential_mm": float(abs(ap_diff[i])),
            }
        )
    corr = float(np.corrcoef(ap_diff, dc_diff)[0, 1])
    agreement = {
        "autopos_common_mm": ap_common,
        "autopos_differential_std_mm": float(np.std(ap_diff, ddof=1)),
        "autopos_differential_min_mm": float(np.min(ap_diff)),
        "autopos_differential_max_mm": float(np.max(ap_diff)),
        "delaycal_common_mm": dc_common,
        "delaycal_differential_std_mm": float(np.std(dc_diff, ddof=1)),
        "delaycal_differential_min_mm": float(np.min(dc_diff)),
        "delaycal_differential_max_mm": float(np.max(dc_diff)),
        "differential_pearson_r": corr,
        "differential_rmse_mm": float(np.sqrt(np.mean((ap_diff - dc_diff) ** 2))),
        "delaycal_pair_residual_rms_mm": float(np.sqrt(np.mean([r["fit_residual_mm"] ** 2 for r in delay_pair_rows]))),
        "interpretation": "AutoPos delay is an effective joint self-calibration delay; common mode is gauge/scale coupled.",
    }
    write_csv(tables_dir / "delay_common_differential.csv", delay_rows)
    write_csv(tables_dir / "delay_method_agreement.csv", [agreement])
    plot_delay(figs_dir / "delay_decomposition.png", delay_rows)

    # Analyses 2 and 3.
    vector_rows = []
    for _, r in tag_df.iterrows():
        truth = np.array([r["truth_x_mm"], r["truth_y_vertical_mm"], r["truth_z_mm"]], dtype=float)
        err = np.array([r["err_x_mm"], r["err_y_vertical_mm"], r["err_z_mm"]], dtype=float)
        radial_vec = truth - anchor_centroid
        radial_norm = float(np.linalg.norm(radial_vec))
        unit = radial_vec / radial_norm if radial_norm > 1e-9 else np.zeros(3)
        radial_component = float(np.dot(err, unit))
        tangential = err - radial_component * unit
        vector_rows.append(
            {
                "eval_set": r["eval_set"],
                "ID": r["ID"],
                "location": r["location"],
                "height": r["height"],
                "facing": r["facing"],
                "truth_x_mm": float(truth[0]),
                "truth_y_vertical_mm": float(truth[1]),
                "truth_z_mm": float(truth[2]),
                "err_x_mm": float(err[0]),
                "err_y_vertical_mm": float(err[1]),
                "err_z_mm": float(err[2]),
                "err_3d_mm": float(r["err_3d_mm"]),
                "distance_from_anchor_centroid_mm": radial_norm,
                "distance_from_anchor_centroid_m": radial_norm / 1000.0,
                "radial_component_mm": radial_component,
                "radial_outward": bool(radial_component > 0.0),
                "tangential_magnitude_mm": float(np.linalg.norm(tangential)),
                "radial_unit_x": float(unit[0]),
                "radial_unit_y_vertical": float(unit[1]),
                "radial_unit_z": float(unit[2]),
            }
        )
    vec_df = pd.DataFrame(vector_rows)
    regression_rows = []
    for eval_set, g in vec_df.groupby("eval_set"):
        for outcome in ["err_3d_mm", "radial_component_mm"]:
            reg = regression(g["distance_from_anchor_centroid_m"].to_numpy(float), g[outcome].to_numpy(float))
            slope = reg["slope"]
            regression_rows.append(
                {
                    "eval_set": eval_set,
                    "outcome": outcome,
                    "n": reg["n"],
                    "slope_mm_per_m": slope,
                    "intercept_mm": reg["intercept"],
                    "r2": reg["r2"],
                    "p_value": reg["p_value"],
                    "slope_ci95_low_mm_per_m": reg["slope_ci_low"],
                    "slope_ci95_high_mm_per_m": reg["slope_ci_high"],
                    "verdict": "positive significant" if reg["p_value"] < 0.05 and slope > 0 else "not positive significant",
                }
            )
    vector_summary: dict[str, dict] = {}
    vector_summary_rows = []
    for eval_set, g in vec_df.groupby("eval_set"):
        err_mat = g[["err_x_mm", "err_y_vertical_mm", "err_z_mm"]].to_numpy(float)
        mean_vec = np.nanmean(err_mat, axis=0)
        scatter = err_mat - mean_vec[None, :]
        rms_scatter = float(np.sqrt(np.nanmean(np.sum(scatter * scatter, axis=1))))
        row = {
            "eval_set": eval_set,
            "n": int(len(g)),
            "mean_err_x_mm": float(mean_vec[0]),
            "mean_err_y_vertical_mm": float(mean_vec[1]),
            "mean_err_z_mm": float(mean_vec[2]),
            "mean_vector_norm_mm": float(np.linalg.norm(mean_vec)),
            "rms_scatter_about_mean_mm": rms_scatter,
            "mean_vector_to_scatter_ratio": float(np.linalg.norm(mean_vec) / rms_scatter) if rms_scatter > 0 else np.nan,
            "median_radial_component_mm": float(np.nanmedian(g["radial_component_mm"])),
            "median_tangential_magnitude_mm": float(np.nanmedian(g["tangential_magnitude_mm"])),
            "fraction_radial_outward": float(np.mean(g["radial_component_mm"] > 0)),
        }
        vector_summary[eval_set] = row
        vector_summary_rows.append(row)
    write_csv(tables_dir / "tag_error_vs_center_distance.csv", regression_rows)
    write_csv(tables_dir / "tag_error_vector_decomposition.csv", vector_rows)
    write_csv(tables_dir / "tag_error_vector_summary.csv", vector_summary_rows)
    plot_regression(figs_dir / "tag_error_vs_center_distance.png", vec_df, regression_rows)
    plot_vector_field(figs_dir / "tag_error_vector_field.png", vec_df)

    # Analysis 4.
    all8 = tag_df[tag_df["eval_set"] == "all8"].copy()
    clean_ids = [sid for sid in all8.sort_values("err_3d_mm")["ID"].tolist() if sid not in WORST_IDS][:4]
    selected_ids = WORST_IDS + clean_ids
    static_files = {session_id_from_path(p): p for p in sorted(captures_root.glob("static_ID*/tag_capture*/tr_all.csv"))}
    residual_rows = []
    for sid in selected_ids:
        if sid not in static_files:
            continue
        med_ranges = static_range_medians(static_files[sid])
        prod = all8[all8["ID"] == sid].iloc[0]
        solved = np.array([prod["aligned_x_mm"], prod["aligned_y_vertical_mm"], prod["aligned_z_mm"]], dtype=float)
        truth = np.array([prod["truth_x_mm"], prod["truth_y_vertical_mm"], prod["truth_z_mm"]], dtype=float)
        temp_rows = []
        for aid, measured in med_ranges.items():
            a = ANCHORS[aid]
            anchor = anchor_truth[a]
            temp_rows.append(
                {
                    "ID": sid,
                    "comparison_group": "worst" if sid in WORST_IDS else "clean_reference",
                    "anchor": a,
                    "median_raw_range_mm": measured,
                    "distance_solved_to_anchor_mm": float(np.linalg.norm(solved - anchor)),
                    "distance_truth_to_anchor_mm": float(np.linalg.norm(truth - anchor)),
                    "residual_vs_solved_mm": float(measured - np.linalg.norm(solved - anchor)),
                    "residual_vs_truth_mm": float(measured - np.linalg.norm(truth - anchor)),
                    "autopos_effective_delay_mm": autopos_delays[a],
                    "delaycal_endpoint_delay_mm": delaycal_delays[a],
                    "production_err_3d_mm": float(prod["err_3d_mm"]),
                    "location": prod["location"],
                    "height": prod["height"],
                    "facing": prod["facing"],
                }
            )
        med_solved = float(np.nanmedian([r["residual_vs_solved_mm"] for r in temp_rows]))
        med_truth = float(np.nanmedian([r["residual_vs_truth_mm"] for r in temp_rows]))
        for row in temp_rows:
            row["residual_vs_solved_centered_mm"] = row["residual_vs_solved_mm"] - med_solved
            row["residual_vs_truth_centered_mm"] = row["residual_vs_truth_mm"] - med_truth
            residual_rows.append(row)
    write_csv(tables_dir / "worstpoint_range_residuals.csv", residual_rows)
    plot_worst_residuals(figs_dir / "worstpoint_range_residual_fingerprint.png", residual_rows)

    # Analysis 5.
    pair_res = pd.read_csv(pair_res_path)
    pair_res = pair_res[(pair_res["version"] == "v4-io") & (pair_res["eval_set"] == "all1000")]
    raw_asym = pd.read_csv(raw_asym_path)
    drift = pd.read_csv(drift_path)
    marker_flag = {a: 1.0 if a == "G" else 0.0 for a in ANCHORS}
    pair_metric = {}
    asym_metric = {}
    drift_metric = {}
    delay_metric = {a: abs(float(ap_diff[i])) for i, a in enumerate(ANCHORS)}
    for a in ANCHORS:
        pair_metric[a] = float(np.nanmedian(pair_res[pair_res["pair"].str.contains(a)]["abs_residual_mm"]))
        asym_metric[a] = float(np.nanmedian(raw_asym[raw_asym["pair"].str.contains(a)]["abs_asym_mm"]))
        drift_metric[a] = float(drift[drift["anchor"] == a]["p95_abs_slope_mm_per_min"].iloc[0])
    n_pair = normalize(pair_metric)
    n_asym = normalize(asym_metric)
    n_drift = normalize(drift_metric)
    n_delay = normalize(delay_metric)
    health_rows = []
    for a in ANCHORS:
        risk = float(np.mean([n_pair[a], n_asym[a], n_drift[a], marker_flag[a], n_delay[a]]))
        health_rows.append(
            {
                "anchor": a,
                "trust_score": float(1.0 - risk),
                "risk_score": risk,
                "pair_residual_median_abs_mm": pair_metric[a],
                "raw_asymmetry_median_abs_mm": asym_metric[a],
                "temporal_drift_p95_abs_slope_mm_per_min": drift_metric[a],
                "opti_marker_fingerprint_flag": int(marker_flag[a]),
                "autopos_delay_differential_abs_mm": delay_metric[a],
                "norm_pair_residual_risk": n_pair[a],
                "norm_raw_asymmetry_risk": n_asym[a],
                "norm_temporal_drift_risk": n_drift[a],
                "norm_delay_differential_risk": n_delay[a],
                "note": "heuristic composite, not a rigorous probability",
            }
        )
    write_csv(tables_dir / "anchor_health_scorecard.csv", sorted(health_rows, key=lambda r: r["trust_score"]))
    plot_anchor_health(figs_dir / "anchor_health_scorecard.png", health_rows)

    # Analyses 6, 7, 8.
    height_rows = group_metric_rows(tag_df, "height", rng, args.bootstrap)
    edge_rows = group_metric_rows(tag_df, "location", rng, args.bootstrap)
    facing_rows = group_metric_rows(tag_df, "facing", rng, args.bootstrap)
    dop = pd.read_csv(dop_facing_path)
    dop = dop[(dop["version"] == "v4-io") & (dop["mask"].isin(["all8"]))]
    dop_group = dop.groupby(["mask", "facing"]).agg(dop_vdop_median=("vdop", "median"), dop_cond_median=("cond", "median")).reset_index()
    for row in facing_rows:
        dg = dop_group[(dop_group["mask"] == row["eval_set"]) & (dop_group["facing"] == row["facing"])]
        if not dg.empty:
            row["dop_vdop_median"] = float(dg["dop_vdop_median"].iloc[0])
            row["dop_cond_median"] = float(dg["dop_cond_median"].iloc[0])
        else:
            row["dop_vdop_median"] = np.nan
            row["dop_cond_median"] = np.nan
    write_csv(tables_dir / "tag_error_by_height.csv", height_rows)
    write_csv(tables_dir / "tag_error_edge_vs_center.csv", edge_rows)
    write_csv(tables_dir / "tag_error_by_facing.csv", facing_rows)
    plot_group_metric(figs_dir / "tag_error_by_height.png", height_rows, "height", "Production v4-io tag error by height")
    plot_group_metric(figs_dir / "tag_error_edge_vs_center.png", edge_rows, "location", "Production v4-io tag error: edge vs center")
    plot_group_metric(figs_dir / "tag_error_by_facing.png", facing_rows, "facing", "Production v4-io tag error by facing group")

    # Analysis 9.
    strat = pd.read_csv(strat_path)
    static_keep8 = pd.read_csv(static_keep8_path)
    roto_keep8 = pd.read_csv(roto_keep8_path)
    base_static = float(static_keep8[static_keep8["keep_k"] == 8]["d3_std_mm_median"].iloc[0])
    base_roto = float(roto_keep8[roto_keep8["keep_k"] == 8]["turn_center_rms_3d_mm_median"].iloc[0])
    static7 = strat[(strat["layout"] == "v4-io") & (strat["tag_method"] == "T4") & (strat["kind"] == "static") & (strat["keep_k"] == 7)]
    roto7 = strat[(strat["layout"] == "v4-io") & (strat["tag_method"] == "T4") & (strat["kind"] == "roto") & (strat["keep_k"] == 7)]
    static_delta = {}
    roto_delta = {}
    for a in ANCHORS:
        s = static7[static7["dropped_set"] == a]
        r = roto7[roto7["dropped_set"] == a]
        static_delta[a] = float(s["d3_std_mm_median"].iloc[0] - base_static) if not s.empty else np.nan
        roto_delta[a] = float(r["turn_center_rms_3d_mm_median"].iloc[0] - base_roto) if not r.empty else np.nan
    ns = normalize(static_delta)
    nr = normalize(roto_delta)
    critical_rows = []
    for a in ANCHORS:
        s = static7[static7["dropped_set"] == a].iloc[0]
        r = roto7[roto7["dropped_set"] == a].iloc[0]
        score = float(np.nanmean([ns[a], nr[a]]))
        critical_rows.append(
            {
                "dropped_anchor": a,
                "keep_set": s["keep_set"],
                "static_d3_std_median_mm": float(s["d3_std_mm_median"]),
                "static_d3_std_keep8_baseline_mm": base_static,
                "static_d3_std_delta_mm": static_delta[a],
                "roto_turn_center_rms_median_mm": float(r["turn_center_rms_3d_mm_median"]),
                "roto_turn_center_rms_keep8_baseline_mm": base_roto,
                "roto_turn_center_rms_delta_mm": roto_delta[a],
                "combined_criticality_score": score,
                "rank_note": "higher score means more critical to keep",
            }
        )
    critical_rows = sorted(critical_rows, key=lambda row: row["combined_criticality_score"], reverse=True)
    for i, row in enumerate(critical_rows, start=1):
        row["criticality_rank"] = i
    write_csv(tables_dir / "single_anchor_criticality.csv", critical_rows)
    plot_single_anchor_criticality(figs_dir / "single_anchor_criticality.png", critical_rows)

    summary_md = build_summary_md(
        delay_agreement=agreement,
        regression_rows=regression_rows,
        vector_summary=vector_summary,
        height_rows=height_rows,
        edge_rows=edge_rows,
        facing_rows=facing_rows,
        health_rows=health_rows,
        critical_rows=critical_rows,
    )
    (tables_dir / "additional_diagnostics_summary.md").write_text(summary_md, encoding="utf-8")

    new_figs = [
        "delay_decomposition.png",
        "tag_error_vs_center_distance.png",
        "tag_error_vector_field.png",
        "worstpoint_range_residual_fingerprint.png",
        "anchor_health_scorecard.png",
        "tag_error_by_height.png",
        "tag_error_edge_vs_center.png",
        "tag_error_by_facing.png",
        "single_anchor_criticality.png",
    ]
    for rel in ["reports/fig", "reports/to_be_discuess/fig"]:
        dest = out_dir / rel
        dest.mkdir(parents=True, exist_ok=True)
        for fig in new_figs:
            shutil.copy2(figs_dir / fig, dest / fig)

    outputs = [
        "tables/delay_common_differential.csv",
        "tables/delay_method_agreement.csv",
        "tables/tag_error_vs_center_distance.csv",
        "tables/tag_error_vector_decomposition.csv",
        "tables/tag_error_vector_summary.csv",
        "tables/worstpoint_range_residuals.csv",
        "tables/anchor_health_scorecard.csv",
        "tables/tag_error_by_height.csv",
        "tables/tag_error_edge_vs_center.csv",
        "tables/tag_error_by_facing.csv",
        "tables/single_anchor_criticality.csv",
        "tables/additional_diagnostics_summary.md",
        *[f"figs/{fig}" for fig in new_figs],
    ]
    source_files = [
        tag_abs_path,
        layout_json,
        pair_quality,
        pair_res_path,
        raw_asym_path,
        drift_path,
        opti_fp_path,
        strat_path,
        static_keep8_path,
        roto_keep8_path,
        dop_facing_path,
    ]
    append_run_meta(
        out_dir,
        {
            "script": "additional_diagnostics.py",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "args": vars(args),
            "axis_convention": {
                "tag_errors_frame": "OptiTrack",
                "optitrack_vertical_axis": OPTITRACK_VERTICAL_AXIS,
                "autopos_height_convention": "height = -z in AutoPos frame; not used for tag-error vector math",
            },
            "primary_line": "production-output v4-io all8 from tag_abs_errors_per_session.csv",
            "tag_truth_marker": "corrected_Iantenna",
            "tag_truth_corrections": {sid: ",".join(str(i) for i in perm) for sid, perm in TAG_BALL_LABEL_PERMUTATIONS.items()},
            "source_files": [str(p) for p in source_files],
            "source_sha256": {str(p): sha256_file(p) for p in source_files if p.exists()},
            "outputs": outputs,
        },
    )
    print(f"[additional] wrote {len(outputs)} outputs; summary={tables_dir / 'additional_diagnostics_summary.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
