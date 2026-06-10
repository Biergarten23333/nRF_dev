#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from scripts.audit_helpers import ANCHOR_LABELS, markdown_table, valid_mask
from scripts.phase1_common import (
    RNG_SEED,
    anchor_coord_map,
    load_data_config,
    load_phase1_data,
    mapping_as_anchor_label,
    tag_coord_map,
)


PRIMARY_VICON_IDS = ("ID01", "ID02", "ID03", "ID04", "ID05")
EXPECTED_BASELINE_MEDIAN_MM = 92.8
EXPECTED_BASELINE_RMS_MM = 105.4
MULTIPATH_DIRECTED_LINKS = {"C-G", "G-C", "F-G", "G-F", "E-H", "H-E"}


@dataclass
class RigidFit:
    rotation: np.ndarray
    translation: np.ndarray
    aligned: np.ndarray
    det: float

    def apply(self, points: np.ndarray) -> np.ndarray:
        pts = np.asarray(points, dtype=float)
        return pts @ self.rotation + self.translation


@dataclass
class TagFit:
    name: str
    delta_tag_mm: float
    rho: float
    anchor_deltas_mm: np.ndarray
    pred: np.ndarray
    residual: np.ndarray
    rms_mm: float
    corr_with_sweep: float
    n_links: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 2 solver ablation and circularity checks.")
    parser.add_argument("--data-dir", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    parser.add_argument("--pair-holdout-trials", type=int, default=200)
    parser.add_argument("--tag-bootstrap", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=RNG_SEED)
    return parser.parse_args()


def load_full_compare_module(data_dir: Path):
    script = data_dir.parent / "outdoor_20260513" / "run_clean_full_compare.py"
    if not script.exists():
        raise FileNotFoundError(f"missing full-compare helper: {script}")
    spec = importlib.util.spec_from_file_location("phase2_full_compare", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    staged = data_dir / "solver" / "work" / "field_dataset_staged"
    module.DATA = staged
    module.SWEEP_CSV = staged / "sweep1000" / "pairs_all.csv"
    return module


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        for row in rows:
            for key in row:
                if key not in fields:
                    fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def summarize_values(vals: list[float] | np.ndarray) -> dict[str, float]:
    arr = np.asarray(vals, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {"n": 0, "mean": math.nan, "rms": math.nan, "median": math.nan, "p95": math.nan, "max": math.nan}
    return {
        "n": int(arr.size),
        "mean": float(np.mean(arr)),
        "rms": float(np.sqrt(np.mean(arr * arr))),
        "median": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
        "max": float(np.max(arr)),
    }


def load_primary_vicon_anchor_truth(data_dir: Path, fallback_truth: pd.DataFrame) -> dict[str, np.ndarray]:
    path = data_dir / "Analysis" / "official_extra_analysis" / "FULL" / "tables" / "opti_anchor_medians_by_file.csv"
    if path.exists():
        by_file = pd.read_csv(path)
        use = by_file[by_file["file_id"].astype(str).isin(PRIMARY_VICON_IDS)].copy()
        if not use.empty:
            truth = (
                use.groupby("anchor", as_index=False)
                .agg(x_mm=("x_mm", "median"), y_vertical_mm=("y_vertical_mm", "median"), z_mm=("z_mm", "median"))
                .sort_values("anchor")
            )
            return anchor_coord_map(truth)
    return anchor_coord_map(fallback_truth)


def fit_rigid(src: np.ndarray, dst: np.ndarray, *, allow_reflection: bool = True) -> RigidFit:
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    x = src - src_c
    y = dst - dst_c
    u, _s, vt = np.linalg.svd(x.T @ y)
    d = np.ones(3)
    if not allow_reflection and np.linalg.det(u @ vt) < 0:
        d[-1] = -1.0
    rotation = u @ np.diag(d) @ vt
    translation = dst_c - src_c @ rotation
    aligned = src @ rotation + translation
    return RigidFit(rotation=rotation, translation=translation, aligned=aligned, det=float(np.linalg.det(rotation)))


def pairwise_shape_rms(src: np.ndarray, dst: np.ndarray) -> float:
    diffs = []
    for i in range(len(ANCHOR_LABELS)):
        for j in range(i + 1, len(ANCHOR_LABELS)):
            diffs.append(float(np.linalg.norm(src[i] - src[j]) - np.linalg.norm(dst[i] - dst[j])))
    arr = np.asarray(diffs, dtype=float)
    return float(np.sqrt(np.mean(arr * arr)))


def alignment_metrics(layout: Any, truth_by_anchor: dict[str, np.ndarray]) -> tuple[dict, RigidFit, list[dict]]:
    src = np.asarray(layout.x, dtype=float)
    dst = np.asarray([truth_by_anchor[a] for a in ANCHOR_LABELS], dtype=float)
    fit = fit_rigid(src, dst, allow_reflection=True)
    diff = fit.aligned - dst
    err3 = np.linalg.norm(diff, axis=1)
    horizontal = np.sqrt(diff[:, 0] ** 2 + diff[:, 2] ** 2)
    vertical = np.abs(diff[:, 1])
    summary = {
        "variant": layout.version,
        "anchor_median_3d_mm": float(np.percentile(err3, 50)),
        "anchor_rms_3d_mm": float(np.sqrt(np.mean(err3 * err3))),
        "anchor_p95_3d_mm": float(np.percentile(err3, 95)),
        "anchor_max_3d_mm": float(np.max(err3)),
        "anchor_horizontal_rms_mm": float(np.sqrt(np.mean(horizontal * horizontal))),
        "anchor_vertical_rms_mm": float(np.sqrt(np.mean(vertical * vertical))),
        "shape_rms_mm": pairwise_shape_rms(src, dst),
        "rigid_det": fit.det,
    }
    rows = []
    for idx, anchor in enumerate(ANCHOR_LABELS):
        rows.append(
            {
                "variant": layout.version,
                "anchor": anchor,
                "err_x_mm": float(diff[idx, 0]),
                "err_y_vertical_mm": float(diff[idx, 1]),
                "err_z_mm": float(diff[idx, 2]),
                "err_3d_mm": float(err3[idx]),
                "err_horizontal_mm": float(horizontal[idx]),
                "err_vertical_mm": float(vertical[idx]),
                "aligned_x_mm": float(fit.aligned[idx, 0]),
                "aligned_y_vertical_mm": float(fit.aligned[idx, 1]),
                "aligned_z_mm": float(fit.aligned[idx, 2]),
                "truth_x_mm": float(dst[idx, 0]),
                "truth_y_vertical_mm": float(dst[idx, 1]),
                "truth_z_mm": float(dst[idx, 2]),
            }
        )
    return summary, fit, rows


def load_sweep_deltas(out_dir: Path) -> dict[str, float]:
    path = out_dir / "tables" / "02_pair_bias_anchor_deltas.csv"
    if not path.exists():
        raise FileNotFoundError(f"missing Phase 1 pair delta table: {path}")
    df = pd.read_csv(path)
    col = "delta_full_mm" if "delta_full_mm" in df.columns else "delta_additive_only_mm"
    return {str(row["anchor"]): float(row[col]) for _, row in df.iterrows()}


def pair_model_design(pair_names: list[str], distances: np.ndarray) -> np.ndarray:
    x = np.zeros((len(pair_names), 9), dtype=float)
    for row, pair in enumerate(pair_names):
        a, b = pair.split("-")
        x[row, ANCHOR_LABELS.index(a)] = 0.5
        x[row, ANCHOR_LABELS.index(b)] = 0.5
        x[row, 8] = distances[row]
    return x


def fit_pair_model(pair_df: pd.DataFrame) -> dict:
    pair_names = pair_df["pair"].astype(str).tolist()
    distances = pair_df["vicon_distance_mm"].to_numpy(dtype=float)
    y = pair_df["bias_mm"].to_numpy(dtype=float)
    x = pair_model_design(pair_names, distances)
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    pred = x @ beta
    residual = y - pred
    return {
        "beta": beta,
        "pred": pred,
        "residual": residual,
        "rms": float(np.sqrt(np.mean(residual * residual))),
        "rho": float(beta[8]),
        "anchor_deltas": beta[:8],
    }


def leave_pair_out(pair_df: pd.DataFrame, trials: int, seed: int) -> tuple[list[dict], dict]:
    rng = np.random.default_rng(seed)
    rows = []
    idx_all = np.arange(len(pair_df))
    for trial in range(trials):
        train_idx = np.sort(rng.choice(idx_all, size=21, replace=False))
        held_idx = np.array([i for i in idx_all if i not in set(train_idx)], dtype=int)
        train = pair_df.iloc[train_idx].reset_index(drop=True)
        held = pair_df.iloc[held_idx].reset_index(drop=True)
        fit = fit_pair_model(train)
        x_held = pair_model_design(held["pair"].astype(str).tolist(), held["vicon_distance_mm"].to_numpy(dtype=float))
        y_held = held["bias_mm"].to_numpy(dtype=float)
        residual = y_held - x_held @ fit["beta"]
        rows.append(
            {
                "trial": trial,
                "train_pairs": ",".join(train["pair"].astype(str).tolist()),
                "heldout_pairs": ",".join(held["pair"].astype(str).tolist()),
                "train_rms_mm": fit["rms"],
                "heldout_rms_mm": float(np.sqrt(np.mean(residual * residual))),
                "heldout_median_abs_mm": float(np.percentile(np.abs(residual), 50)),
                "heldout_p95_abs_mm": float(np.percentile(np.abs(residual), 95)),
            }
        )
    s = summarize_values([r["heldout_rms_mm"] for r in rows])
    summary = {
        "trials": trials,
        "heldout_rms_mean_mm": float(np.mean([r["heldout_rms_mm"] for r in rows])),
        "heldout_rms_median_mm": s["median"],
        "heldout_rms_p95_mm": s["p95"],
        "heldout_rms_max_mm": s["max"],
    }
    return rows, summary


def build_tag_model(link_df: pd.DataFrame, sweep_delta: dict[str, float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean_sweep = float(np.mean([sweep_delta[a] for a in ANCHOR_LABELS]))
    x = np.zeros((len(link_df), 9), dtype=float)
    for row, anchor in enumerate(link_df["anchor"].astype(str)):
        idx = ANCHOR_LABELS.index(anchor)
        if idx < 7:
            x[row, idx] = 0.5
        else:
            x[row, :7] = -0.5
        x[row, 7] = 0.5  # Delta_tag
        x[row, 8] = float(link_df.iloc[row]["vicon_distance_mm"])
    y = link_df["bias_mm"].to_numpy(dtype=float) - 0.5 * mean_sweep
    return x, y, np.full(len(link_df), mean_sweep, dtype=float)


def fit_tag_bias_model(name: str, link_df: pd.DataFrame, sweep_delta: dict[str, float]) -> TagFit:
    x, y, mean_sweep = build_tag_model(link_df.reset_index(drop=True), sweep_delta)
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    theta = np.r_[beta[:7], -float(np.sum(beta[:7]))]
    anchor_deltas = mean_sweep[0] + theta
    delta_tag = float(beta[7])
    rho = float(beta[8])
    anchors = link_df["anchor"].astype(str).map(lambda a: ANCHOR_LABELS.index(a)).to_numpy(dtype=int)
    distances = link_df["vicon_distance_mm"].to_numpy(dtype=float)
    pred = 0.5 * delta_tag + 0.5 * anchor_deltas[anchors] + rho * distances
    residual = link_df["bias_mm"].to_numpy(dtype=float) - pred
    sweep_vec = np.asarray([sweep_delta[a] for a in ANCHOR_LABELS], dtype=float)
    corr = float(np.corrcoef(sweep_vec, anchor_deltas)[0, 1]) if np.std(anchor_deltas) > 0 else math.nan
    return TagFit(
        name=name,
        delta_tag_mm=delta_tag,
        rho=rho,
        anchor_deltas_mm=anchor_deltas,
        pred=pred,
        residual=residual,
        rms_mm=float(np.sqrt(np.mean(residual * residual))),
        corr_with_sweep=corr,
        n_links=int(len(link_df)),
    )


def bootstrap_tag_model(link_df: pd.DataFrame, sweep_delta: dict[str, float], n_boot: int, seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    rows = []
    idx = np.arange(len(link_df))
    for boot in range(n_boot):
        sample = link_df.iloc[rng.choice(idx, size=len(idx), replace=True)].reset_index(drop=True)
        fit = fit_tag_bias_model("bootstrap", sample, sweep_delta)
        row = {
            "bootstrap": boot,
            "delta_tag_mm": fit.delta_tag_mm,
            "rho_percent": fit.rho * 100.0,
            "rms_mm": fit.rms_mm,
        }
        for i, anchor in enumerate(ANCHOR_LABELS):
            row[f"delta_{anchor}_mm"] = float(fit.anchor_deltas_mm[i])
        rows.append(row)
    return rows


def tag_ci_rows(boot_rows: list[dict]) -> list[dict]:
    df = pd.DataFrame(boot_rows)
    rows = []
    for key, label in [("delta_tag_mm", "Delta_tag"), ("rho_percent", "rho_percent")]:
        vals = df[key].to_numpy(dtype=float)
        rows.append(
            {
                "parameter": label,
                "ci95_low": float(np.percentile(vals, 2.5)),
                "ci95_high": float(np.percentile(vals, 97.5)),
                "median": float(np.percentile(vals, 50)),
            }
        )
    for anchor in ANCHOR_LABELS:
        vals = df[f"delta_{anchor}_mm"].to_numpy(dtype=float)
        rows.append(
            {
                "parameter": f"Delta_{anchor}",
                "ci95_low": float(np.percentile(vals, 2.5)),
                "ci95_high": float(np.percentile(vals, 97.5)),
                "median": float(np.percentile(vals, 50)),
            }
        )
    return rows


def solve_v4_custom(
    mod: Any,
    pair_dists: dict[tuple[int, int], float],
    anchor_ids: list[int],
    *,
    x_init: np.ndarray | None,
    delay_bound_mm: float | None,
    delay_prior_sigma_mm: float = 20.0,
):
    lp, _g2l, _l2g = mod.local_pairs(pair_dists, anchor_ids)
    n = len(anchor_ids)
    if x_init is None:
        x_init = mod.mds_init(lp, n)
    pmap = mod.pos_param_map(n)
    has_delay = delay_bound_mm is not None

    def unpack(v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x = mod.unpack_pos(v[: len(pmap)], n)
        dly = np.zeros(n, dtype=float)
        if has_delay and n > 1:
            dly[1:] = v[len(pmap) :]
        return x, dly

    def fun(v: np.ndarray) -> np.ndarray:
        x, dly = unpack(v)
        out = [(np.linalg.norm(x[i] - x[j]) + dly[i] + dly[j] - dist) / 15.0 for (i, j), dist in lp.items()]
        if has_delay and n > 1:
            out.extend((dly[1:] / delay_prior_sigma_mm).tolist())
        out.extend(mod.physical_layout_prior_residuals(x, anchor_ids))
        return np.asarray(out, dtype=float)

    x0 = mod.pack_pos(x_init)
    if has_delay:
        x0 = np.r_[x0, np.zeros(max(0, n - 1))]
        lo = np.r_[np.full(len(pmap), -np.inf), np.full(max(0, n - 1), -abs(delay_bound_mm))]
        hi = np.r_[np.full(len(pmap), np.inf), np.full(max(0, n - 1), abs(delay_bound_mm))]
    else:
        lo = np.full(len(pmap), -np.inf)
        hi = np.full(len(pmap), np.inf)
    res = least_squares(fun, x0, loss="huber", f_scale=2.0, bounds=(lo, hi), max_nfev=7000)
    x, dly = unpack(res.x)
    res.physical_diagnostics = mod.layout_physical_diagnostics(x, anchor_ids)
    return mod.gauge_align_local(x), dly, res


def residual_rms(layout: Any, pair_dists: dict[tuple[int, int], float], mod: Any, anchor_ids: list[int]) -> float:
    lp, _g2l, _l2g = mod.local_pairs(pair_dists, anchor_ids)
    vals = []
    for (i, j), dist in lp.items():
        vals.append(float(np.linalg.norm(layout.x[i] - layout.x[j]) + layout.dly[i] + layout.dly[j] - dist))
    return float(np.sqrt(np.mean(np.asarray(vals) ** 2))) if vals else math.nan


def delay_summary(layout: Any, bound: float | None) -> dict:
    d = np.asarray(layout.dly, dtype=float)
    near = int(np.sum(np.abs(d) >= 0.95 * abs(bound))) if bound else 0
    return {
        "delay_min_mm": float(np.min(d)),
        "delay_max_mm": float(np.max(d)),
        "delay_l2_mm": float(np.linalg.norm(d)),
        "delay_near_bound_count": near,
    }


def median_static_links(link_df: pd.DataFrame) -> dict[str, list[tuple[int, float]]]:
    out: dict[str, list[tuple[int, float]]] = {}
    for position, g in link_df.groupby("position"):
        obs = []
        for _, row in g.iterrows():
            obs.append((ANCHOR_LABELS.index(str(row["anchor"])), float(row["median_range_mm"])))
        out[str(position)] = obs
    return out


def solve_tag_position(
    obs: list[tuple[int, float]],
    anchor_xyz: np.ndarray,
    sigma_by_anchor: dict[int, float],
    correction_by_anchor: dict[int, float],
    residual_delay: np.ndarray | None = None,
) -> np.ndarray:
    if len(obs) < 4:
        return np.full(3, np.nan)
    residual_delay = np.zeros(8, dtype=float) if residual_delay is None else np.asarray(residual_delay, dtype=float)
    x0 = np.mean([anchor_xyz[a] for a, _r in obs], axis=0)

    def fun(p: np.ndarray) -> np.ndarray:
        vals = []
        for aid, measured in obs:
            corr = float(correction_by_anchor.get(aid, 0.0))
            pred = float(np.linalg.norm(p - anchor_xyz[aid]) + residual_delay[aid])
            sigma = max(5.0, float(sigma_by_anchor.get(aid, 50.0)))
            vals.append((pred - (measured - corr)) / sigma)
        return np.asarray(vals, dtype=float)

    res = least_squares(fun, x0, loss="huber", f_scale=2.0, max_nfev=300)
    return res.x


def leave_one_position_delta_tag(link_df: pd.DataFrame, sweep_delta: dict[str, float]) -> dict[str, float]:
    out = {}
    positions = sorted(link_df["position"].astype(str).unique())
    for position in positions:
        train = link_df[link_df["position"].astype(str) != position].reset_index(drop=True)
        fit = fit_tag_bias_model(f"loo_without_{position}", train, sweep_delta)
        out[position] = float(fit.delta_tag_mm)
    return out


def evaluate_static_transfer(
    layouts: dict[str, Any],
    fits: dict[str, RigidFit],
    link_df: pd.DataFrame,
    tag_truth: dict[str, np.ndarray],
    truth_meta: dict[str, dict],
    sweep_delta: dict[str, float],
    loo_delta_tag: dict[str, float],
    sigma_by_anchor: dict[int, float],
) -> tuple[list[dict], list[dict]]:
    obs_by_position = median_static_links(link_df)
    rows = []
    for variant, layout in layouts.items():
        fit = fits[variant]
        for position, obs in obs_by_position.items():
            if position not in tag_truth:
                continue
            if variant in {"baseline_v4io", "V-A_unbounded"}:
                correction = {aid: 0.0 for aid, _r in obs}
                residual_delay = np.asarray(layout.dly, dtype=float)
                policy = "native_solver_delay"
            else:
                dt = float(loo_delta_tag[position])
                correction = {aid: 0.5 * sweep_delta[ANCHOR_LABELS[aid]] + 0.5 * dt for aid, _r in obs}
                residual_delay = np.asarray(layout.dly, dtype=float)
                policy = "sweep_delta_plus_loo_tag_delta"
            pos_local = solve_tag_position(obs, np.asarray(layout.x, dtype=float), sigma_by_anchor, correction, residual_delay)
            pos_vicon = fit.apply(pos_local[None, :])[0]
            err = pos_vicon - tag_truth[position]
            e3 = float(np.linalg.norm(err))
            rows.append(
                {
                    "variant": variant,
                    "position": position,
                    "policy": policy,
                    "tag_truth_source": truth_meta.get(position, {}).get("tag_truth_source", ""),
                    "truth_reconstructed": bool(truth_meta.get(position, {}).get("tag_truth_source", "") != "motive_iantenna"),
                    "loo_delta_tag_mm": float(loo_delta_tag.get(position, math.nan)),
                    "err_x_mm": float(err[0]),
                    "err_y_vertical_mm": float(err[1]),
                    "err_z_mm": float(err[2]),
                    "err_horizontal_mm": float(math.hypot(err[0], err[2])),
                    "err_vertical_mm": float(abs(err[1])),
                    "err_3d_mm": e3,
                    "solved_x_mm": float(pos_vicon[0]),
                    "solved_y_vertical_mm": float(pos_vicon[1]),
                    "solved_z_mm": float(pos_vicon[2]),
                    "truth_x_mm": float(tag_truth[position][0]),
                    "truth_y_vertical_mm": float(tag_truth[position][1]),
                    "truth_z_mm": float(tag_truth[position][2]),
                }
            )
    summary_rows = []
    for variant, g in pd.DataFrame(rows).groupby("variant"):
        vals = g["err_3d_mm"].to_numpy(dtype=float)
        s = summarize_values(vals)
        worst = g.sort_values("err_3d_mm", ascending=False).iloc[0]
        summary_rows.append(
            {
                "variant": variant,
                "positions": int(s["n"]),
                "static_tag_median_3d_mm": s["median"],
                "static_tag_rmse_3d_mm": s["rms"],
                "static_tag_p95_3d_mm": s["p95"],
                "static_tag_max_3d_mm": s["max"],
                "worst_position": worst["position"],
                "reconstructed_truth_positions": int(g["truth_reconstructed"].sum()),
            }
        )
    return rows, sorted(summary_rows, key=lambda r: r["variant"])


def make_anchor_error_plot(path: Path, anchor_error_rows: list[dict]) -> None:
    df = pd.DataFrame(anchor_error_rows)
    variants = list(df["variant"].drop_duplicates())
    fig, ax = plt.subplots(figsize=(10, 4.8))
    width = 0.18
    x = np.arange(len(ANCHOR_LABELS))
    for k, variant in enumerate(variants):
        vals = [float(df[(df["variant"] == variant) & (df["anchor"] == a)]["err_3d_mm"].iloc[0]) for a in ANCHOR_LABELS]
        ax.bar(x + (k - (len(variants) - 1) / 2) * width, vals, width=width, label=variant)
    ax.set_xticks(x, ANCHOR_LABELS)
    ax.set_ylabel("Vicon-aligned anchor error [mm]")
    ax.set_title("Anchor Layout Error by Solver Variant")
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def make_tag_delta_plot(path: Path, sweep_delta: dict[str, float], tag_fit: TagFit, tag_fit_no12: TagFit) -> None:
    x = np.asarray([sweep_delta[a] for a in ANCHOR_LABELS], dtype=float)
    y = tag_fit.anchor_deltas_mm
    y2 = tag_fit_no12.anchor_deltas_mm
    fig, ax = plt.subplots(figsize=(5.8, 5.2))
    ax.scatter(x, y, s=64, color="#2f6f9f", label="all tag links")
    ax.scatter(x, y2, s=58, color="#b4493a", marker="x", label="excluding top 12")
    for i, anchor in enumerate(ANCHOR_LABELS):
        ax.annotate(anchor, (x[i], y[i]), xytext=(4, 4), textcoords="offset points", fontsize=9)
    lo = float(min(np.min(x), np.min(y), np.min(y2)) - 20)
    hi = float(max(np.max(x), np.max(y), np.max(y2)) + 20)
    ax.plot([lo, hi], [lo, hi], color="black", linewidth=0.9, linestyle="--", label="1:1")
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_xlabel("Sweep-fit Delta_i [mm]")
    ax.set_ylabel("Tag-fit Delta_i [mm]")
    ax.set_title("Tag-Side Anchor Delta vs Sweep Delta")
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def make_static_error_plot(path: Path, static_rows: list[dict]) -> None:
    df = pd.DataFrame(static_rows)
    variants = list(df["variant"].drop_duplicates())
    data = [df[df["variant"] == v]["err_3d_mm"].to_numpy(dtype=float) for v in variants]
    fig, ax = plt.subplots(figsize=(8, 4.8))
    ax.boxplot(data, tick_labels=variants, showmeans=True)
    ax.set_ylabel("Static tag absolute error [mm]")
    ax.set_title("Leave-One-Position-Out Static Tag Transfer")
    ax.grid(True, axis="y", alpha=0.25)
    plt.setp(ax.get_xticklabels(), rotation=20, ha="right")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def solve_frame_position_fast(
    obs: list[tuple[int, float]],
    anchor_xyz: np.ndarray,
    sigma_by_anchor: dict[int, float],
    correction_by_anchor: dict[int, float],
    residual_delay: np.ndarray,
    x0: np.ndarray | None,
) -> np.ndarray:
    if x0 is None or not np.all(np.isfinite(x0)):
        x0 = np.mean([anchor_xyz[a] for a, _r in obs], axis=0)
    p = np.asarray(x0, dtype=float).copy()
    for _ in range(8):
        j_rows = []
        r_rows = []
        w_rows = []
        for aid, measured in obs:
            anchor = anchor_xyz[aid]
            diff = p - anchor
            dist = float(np.linalg.norm(diff))
            if dist < 1e-6:
                continue
            corrected = measured - float(correction_by_anchor.get(aid, 0.0))
            pred = dist + float(residual_delay[aid])
            sigma = max(5.0, float(sigma_by_anchor.get(aid, 50.0)))
            rn = (pred - corrected) / sigma
            hw = 1.0 if abs(rn) <= 2.0 else 2.0 / max(abs(rn), 1e-9)
            j_rows.append(diff / dist / sigma)
            r_rows.append(rn)
            w_rows.append(math.sqrt(hw))
        if len(j_rows) < 3:
            break
        jac = np.asarray(j_rows, dtype=float) * np.asarray(w_rows, dtype=float)[:, None]
        res = np.asarray(r_rows, dtype=float) * np.asarray(w_rows, dtype=float)
        try:
            step, *_ = np.linalg.lstsq(jac, -res, rcond=None)
        except np.linalg.LinAlgError:
            break
        if not np.all(np.isfinite(step)):
            break
        norm = float(np.linalg.norm(step))
        if norm > 500.0:
            step *= 500.0 / norm
        p += step
        if float(np.linalg.norm(step)) < 0.02:
            break
    return p


def torch_cuda_devices() -> list[str]:
    try:
        import torch

        if not torch.cuda.is_available():
            return []
        return [f"cuda:{idx}" for idx in range(torch.cuda.device_count())]
    except Exception:
        return []


def prewarm_torch_cuda(devices: list[str]) -> None:
    if not devices:
        return
    import torch

    for device_name in devices:
        device = torch.device(device_name)
        with torch.cuda.device(device):
            a = torch.eye(3, device=device).repeat(2, 1, 1)
            b = torch.ones((2, 3), device=device)
            _ = torch.linalg.solve(a, b)
            torch.cuda.synchronize(device)


def solve_frames_torch_batched(
    records: list[dict],
    anchor_xyz: np.ndarray,
    sigma_by_anchor: dict[int, float],
    correction_by_anchor: dict[int, float],
    residual_delay: np.ndarray,
    device_name: str,
    warmstart_passes: int = 2,
) -> np.ndarray:
    import torch

    if not records:
        return np.zeros((0, 3), dtype=float)
    device = torch.device(device_name)
    dtype = torch.float32
    with torch.cuda.device(device):
        n = len(records)
        max_obs = max(len(rec["obs"]) for rec in records)
        aid_arr = np.zeros((n, max_obs), dtype=np.int64)
        rng_arr = np.zeros((n, max_obs), dtype=np.float32)
        mask_arr = np.zeros((n, max_obs), dtype=bool)
        for row, rec in enumerate(records):
            for col, (aid, measured) in enumerate(rec["obs"]):
                aid_arr[row, col] = int(aid)
                rng_arr[row, col] = float(measured)
                mask_arr[row, col] = True

        aids = torch.as_tensor(aid_arr, device=device)
        ranges = torch.as_tensor(rng_arr, device=device, dtype=dtype)
        mask = torch.as_tensor(mask_arr, device=device)
        anchors = torch.as_tensor(anchor_xyz.astype(np.float32), device=device, dtype=dtype)
        sigma = torch.as_tensor([max(5.0, float(sigma_by_anchor.get(i, 50.0))) for i in range(8)], device=device, dtype=dtype)
        corr = torch.as_tensor([float(correction_by_anchor.get(i, 0.0)) for i in range(8)], device=device, dtype=dtype)
        dly = torch.as_tensor(np.asarray(residual_delay, dtype=np.float32), device=device, dtype=dtype)

        obs_anchor = anchors[aids]
        weights = mask.to(dtype).unsqueeze(-1)
        denom = weights.sum(dim=1).clamp_min(1.0)
        centroid_init = (obs_anchor * weights).sum(dim=1) / denom
        p = centroid_init.clone()
        eye = torch.eye(3, device=device, dtype=dtype).unsqueeze(0)

        def refine(p0: "torch.Tensor") -> "torch.Tensor":
            p_cur = p0
            for _ in range(10):
                diff = p_cur[:, None, :] - obs_anchor
                dist = torch.linalg.norm(diff, dim=2).clamp_min(1e-4)
                corrected = ranges - corr[aids]
                pred = dist + dly[aids]
                sig = sigma[aids]
                rn = (pred - corrected) / sig
                rn = torch.where(mask, rn, torch.zeros_like(rn))
                abs_rn = rn.abs()
                huber = torch.where(abs_rn <= 2.0, torch.ones_like(abs_rn), 2.0 / abs_rn.clamp_min(1e-6))
                sqrt_w = torch.sqrt(huber) * mask.to(dtype)
                jac = diff / dist[..., None] / sig[..., None]
                jac = jac * sqrt_w[..., None]
                res = rn * sqrt_w
                normal = torch.matmul(jac.transpose(1, 2), jac) + 1e-4 * eye
                rhs = -torch.matmul(jac.transpose(1, 2), res[..., None]).squeeze(-1)
                step = torch.linalg.solve(normal, rhs)
                step_norm = torch.linalg.norm(step, dim=1).clamp_min(1e-6)
                step = torch.where((step_norm > 500.0)[:, None], step * (500.0 / step_norm)[:, None], step)
                p_cur = p_cur + step
                if bool((step_norm < 0.02).all().item()):
                    break
            return p_cur

        p = refine(p)
        for _ in range(max(0, warmstart_passes)):
            shifted = p.clone()
            if shifted.shape[0] > 1:
                shifted[1:] = p[:-1]
                shifted[0] = centroid_init[0]
            p = refine(shifted)
        torch.cuda.synchronize(device)
        return p.detach().cpu().numpy().astype(float)


def build_roto_frames(roto_df: pd.DataFrame, mapping: dict[int, str]) -> dict[tuple[str, str], list[dict]]:
    work = roto_df[valid_mask(roto_df)].copy()
    work = work[~work["capture_id"].astype(str).eq("R01-Static-middle-test")].copy()
    if work.empty:
        return {}
    work["range_mm"] = pd.to_numeric(work["range_mm"], errors="coerce")
    work["sweep"] = pd.to_numeric(work["sweep"], errors="coerce").astype("Int64")
    work["host_elapsed_s"] = pd.to_numeric(work.get("host_elapsed_s", 0.0), errors="coerce").fillna(0.0)
    grouped: dict[tuple[str, str, int], dict] = {}
    for _, row in work.iterrows():
        try:
            aid_raw = int(row["anchor_id"])
            aid = ANCHOR_LABELS.index(mapping_as_anchor_label(aid_raw, mapping))
            rng = float(row["range_mm"])
            sweep = int(row["sweep"])
        except Exception:
            continue
        if not (0 <= aid < 8 and rng > 0):
            continue
        capture_id = str(row["capture_id"])
        peer = str(row.get("peer_name", "unknown") or "unknown")
        key = (capture_id, peer, sweep)
        rec = grouped.setdefault(key, {"capture_id": capture_id, "peer": peer, "sweep": sweep, "t": float(row["host_elapsed_s"]), "obs": []})
        rec["obs"].append((aid, rng))

    out: dict[tuple[str, str], list[dict]] = {}
    for (capture_id, peer, _sweep), rec in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        if len(rec["obs"]) >= 4:
            out.setdefault((capture_id, peer), []).append(rec)
    return out


def evaluate_roto_from_captures(
    fc: Any,
    layouts: dict[str, Any],
    roto_df: pd.DataFrame,
    mapping: dict[int, str],
    sweep_delta: dict[str, float],
    sigma_by_anchor: dict[int, float],
) -> tuple[list[dict], list[dict], list[dict], dict]:
    frames = build_roto_frames(roto_df, mapping)
    devices = torch_cuda_devices()
    prewarm_torch_cuda(devices)
    started = time.monotonic()
    backend = {
        "backend": "torch_cuda_batched" if devices else "cpu_sequential",
        "devices": ",".join(devices),
        "tasks": int(len(layouts) * len(frames)),
        "capture_peer_groups": int(len(frames)),
    }

    def run_task(variant: str, layout: Any, key: tuple[str, str], recs: list[dict], device_name: str | None) -> dict:
        capture_id, peer = key
        anchor_xyz = np.asarray(layout.x, dtype=float)
        residual_delay = np.asarray(layout.dly, dtype=float)
        correction = {}
        if variant in {"V-B_calibrated", "V-C_calibrated_residual"}:
            correction = {aid: 0.5 * sweep_delta[ANCHOR_LABELS[aid]] for aid in range(8)}
        counts = [len(rec["obs"]) for rec in recs]
        if device_name:
            arr = solve_frames_torch_batched(recs, anchor_xyz, sigma_by_anchor, correction, residual_delay, device_name)
        else:
            pos = []
            last = None
            for rec in recs:
                obs = rec["obs"]
                p = solve_frame_position_fast(obs, anchor_xyz, sigma_by_anchor, correction, residual_delay, last)
                pos.append(p)
                last = p
            arr = np.asarray(pos, dtype=float)
        row = {"version": variant, "ID": capture_id, "peer": peer, "path": "captures/current", "tilt": "", "facing": "", "position_backend": device_name or "cpu"}
        fit = fc.fit_circle_3d(arr)
        theta = fit.pop("_theta", None)
        row.update(fit)
        if fit.get("status") == "ok" and theta is not None:
            row.update(fc.per_turn_center_stats(arr, theta))
        counts_arr = np.asarray(counts, dtype=float)
        if counts_arr.size:
            row.update(
                {
                    "median_anchors": float(np.median(counts_arr)),
                    "pct_ge7": float(np.mean(counts_arr >= 7) * 100.0),
                    "pct_ge8": float(np.mean(counts_arr >= 8) * 100.0),
                }
            )
        return row

    tasks = [(variant, layout, key, recs) for variant, layout in layouts.items() for key, recs in frames.items()]
    rows = []
    if devices and tasks:
        with ThreadPoolExecutor(max_workers=len(devices)) as pool:
            futs = []
            for idx, (variant, layout, key, recs) in enumerate(tasks):
                futs.append(pool.submit(run_task, variant, layout, key, recs, devices[idx % len(devices)]))
            for fut in as_completed(futs):
                rows.append(fut.result())
    else:
        for variant, layout, key, recs in tasks:
            rows.append(run_task(variant, layout, key, recs, None))
    rows.sort(key=lambda r: (r["version"], r["ID"], r["peer"]))
    phys_rows = fc.roto_physical_consistency(rows)
    summary_rows = fc.roto_physical_summary(phys_rows)
    for row in phys_rows:
        row["variant"] = row.pop("version")
    for row in summary_rows:
        row["variant"] = row.pop("version")
    backend["elapsed_s"] = time.monotonic() - started
    return rows, phys_rows, summary_rows, backend


def save_layout_json(path: Path, layout: Any, anchor_ids: list[int], extra: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    obj = {
        "version": layout.version,
        "label": layout.label,
        "anchor_ids": anchor_ids,
        "anchors": [
            {
                "id": int(gi),
                "label": ANCHOR_LABELS[gi],
                "x_mm": float(layout.x[li, 0]),
                "y_mm": float(layout.x[li, 1]),
                "z_mm": float(layout.x[li, 2]),
                "d_anchor_mm": float(layout.dly[li]),
            }
            for li, gi in enumerate(anchor_ids)
        ],
        "extra": extra or layout.extra,
    }
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def build_report(
    out_dir: Path,
    tag_fit_rows: list[dict],
    tag_ci: list[dict],
    outlier_rows: list[dict],
    pair_holdout_summary: dict,
    transfer_guard: dict,
    solver_rows: list[dict],
    static_summary: list[dict],
    roto_summary: list[dict],
    roto_backend: dict,
    noise_rows: list[dict],
    multipath_rows: list[dict],
    fig_names: dict[str, str],
) -> None:
    lines = ["# Phase 2 Solver Ablation\n"]
    lines.append(f"- Generated: `{pd.Timestamp.now().isoformat(timespec='seconds')}`")
    lines.append(f"- Ground-truth terminology: `Vicon`")
    lines.append("- Scope: Phase 2 diagnostics and solver ablation only; no production solver files were modified.")
    lines.append("")
    lines.append("## 2.0 Tag-Side Additive Refit")
    lines.append("The model is `bias_{p,i} = Delta_tag/2 + Delta_i/2 + rho_tag*d`. Its additive gauge is fixed by forcing the tag-fit anchor Delta mean to match the sweep-fit Delta mean.")
    lines.append("")
    lines.append(markdown_table(tag_fit_rows, ["fit", "links", "delta_tag_mm", "rho_tag_percent", "rms_mm", "corr_delta_i_vs_sweep"]))
    lines.append("")
    lines.append("Bootstrap 95% intervals from the all-link tag model:")
    lines.append("")
    lines.append(markdown_table(tag_ci, ["parameter", "median", "ci95_low", "ci95_high"]))
    lines.append("")
    lines.append(f"![Tag-side Delta comparison](figures/{fig_names['tag_delta']})")
    lines.append("")
    lines.append("Largest 12 absolute-bias tag links excluded in the sensitivity fit:")
    lines.append("")
    lines.append(markdown_table(outlier_rows, ["position", "anchor", "bias_mm", "vicon_distance_mm", "tag_truth_source", "truth_reconstructed", "facing"]))
    lines.append("")
    lines.append("## 2.1 Baseline Reproduction")
    baseline = next(r for r in solver_rows if r["variant"] == "baseline_v4io")
    gate = "PASS" if baseline["baseline_gate_pass"] else "FAIL"
    lines.append(f"Baseline v4-io reproduction gate: **{gate}**. Expected Vicon-registered anchor median/RMSE are about `{EXPECTED_BASELINE_MEDIAN_MM:.1f}`/`{EXPECTED_BASELINE_RMS_MM:.1f}` mm.")
    lines.append("")
    lines.append(markdown_table([baseline], ["variant", "anchor_median_3d_mm", "anchor_rms_3d_mm", "shape_rms_mm", "solve_pair_rms_mm", "delay_min_mm", "delay_max_mm", "delay_near_bound_count"]))
    lines.append("")
    lines.append("## 2.2 Solver Variants")
    lines.append(markdown_table(solver_rows, ["variant", "delay_policy", "anchor_median_3d_mm", "anchor_rms_3d_mm", "anchor_p95_3d_mm", "shape_rms_mm", "solve_pair_rms_mm", "raw_pair_rms_mm", "delay_min_mm", "delay_max_mm", "delay_near_bound_count"]))
    lines.append("")
    lines.append(f"![Anchor error by variant](figures/{fig_names['anchor_error']})")
    lines.append("")
    lines.append("## 2.3 Circularity Guards")
    lines.append(markdown_table([pair_holdout_summary], ["trials", "in_sample_rms_mm", "heldout_rms_mean_mm", "heldout_rms_median_mm", "heldout_rms_p95_mm", "heldout_rms_max_mm"]))
    lines.append("")
    lines.append("Tag-side transfer uses sweep-fitted Delta_i and a Delta_tag refit with the evaluated static position left out.")
    lines.append("")
    lines.append(markdown_table([transfer_guard], ["variant", "positions", "static_tag_median_3d_mm", "static_tag_rmse_3d_mm", "static_tag_p95_3d_mm", "static_tag_max_3d_mm"]))
    lines.append("")
    lines.append("## 2.4 Static Tag And RotoArm")
    lines.append("Static tag absolute errors are after applying each variant's anchor-layout rigid registration to Vicon. V-B and V-C use the leave-one-position-out tag-delay transfer policy.")
    lines.append("")
    lines.append(markdown_table(static_summary, ["variant", "positions", "static_tag_median_3d_mm", "static_tag_rmse_3d_mm", "static_tag_p95_3d_mm", "static_tag_max_3d_mm", "worst_position", "reconstructed_truth_positions"]))
    lines.append("")
    lines.append(f"![Static tag errors](figures/{fig_names['static_error']})")
    lines.append("")
    lines.append("RotoArm replay excludes `R01-Static-middle-test`; current capture replay has 17 dynamic captures.")
    lines.append(f"Roto replay backend: `{roto_backend.get('backend', '')}` on `{roto_backend.get('devices', '')}`; elapsed `{float(roto_backend.get('elapsed_s', math.nan)):.2f}` s for `{roto_backend.get('tasks', '')}` layout/capture-peer tasks.")
    lines.append("")
    lines.append(markdown_table(roto_summary, ["variant", "capture_pairs", "deltaR_error_mean_mm", "deltaR_error_rms_mm", "abs_deltaR_error_median_mm", "abs_deltaR_error_p95_mm", "turn_center_rms_median_mm", "turn_center_rms_p95_mm"]))
    lines.append("")
    lines.append("## 2.5 Side Diagnostics")
    lines.append("Per-anchor static link noise confirms the Phase 1 F/G anomaly:")
    lines.append("")
    lines.append(markdown_table(noise_rows, ["anchor", "links", "noise_median_std_mm", "noise_p95_std_mm", "flag"]))
    lines.append("")
    lines.append("Pair residuals cross-referenced with the directed multipath watchlist `{C-G, G-C, F-G, G-F, E-H, H-E}`:")
    lines.append("")
    lines.append(markdown_table(multipath_rows, ["pair", "full_residual_mm", "abs_full_residual_mm", "multipath_watchlist"]))
    lines.append("")
    lines.append("STOP: Phase 2 ablation only. Do not proceed to solver integration or production changes until this report is reviewed.")
    (out_dir / "02_solver_ablation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    data_dir = args.data_dir.resolve()
    out_dir = args.out_dir.resolve()
    tables_dir = out_dir / "tables"
    figures_dir = out_dir / "figures"
    layout_dir = out_dir / "phase2_solver_layouts"
    for path in (tables_dir, figures_dir, layout_dir):
        path.mkdir(parents=True, exist_ok=True)

    data = load_phase1_data(data_dir, out_dir)
    cfg = load_data_config(data.data_dir)
    mapping = {int(k): v for k, v in cfg.ANCHOR_ID_TO_LABEL.items()}
    sweep_delta = load_sweep_deltas(out_dir)
    primary_truth = load_primary_vicon_anchor_truth(data_dir, data.anchor_truth)
    tag_truth = tag_coord_map(data.tag_truth)
    truth_meta = data.tag_truth.set_index("ID").to_dict("index")

    link_path = tables_dir / "03_tag_link_bias_links.csv"
    if not link_path.exists():
        raise FileNotFoundError(f"missing Phase 1 tag link table: {link_path}")
    link_df = pd.read_csv(link_path)
    link_df["anchor"] = link_df["anchor"].astype(str)
    link_df["position"] = link_df["position"].astype(str)

    top12_idx = link_df["bias_mm"].abs().sort_values(ascending=False).head(12).index
    tag_fit_all = fit_tag_bias_model("all_links", link_df, sweep_delta)
    tag_fit_no12 = fit_tag_bias_model("excluding_top12_abs_bias", link_df.drop(index=top12_idx).reset_index(drop=True), sweep_delta)
    boot_rows = bootstrap_tag_model(link_df, sweep_delta, args.tag_bootstrap, args.seed + 101)
    tag_ci = tag_ci_rows(boot_rows)
    tag_fit_rows = [
        {
            "fit": tag_fit_all.name,
            "links": tag_fit_all.n_links,
            "delta_tag_mm": tag_fit_all.delta_tag_mm,
            "rho_tag_percent": tag_fit_all.rho * 100.0,
            "rms_mm": tag_fit_all.rms_mm,
            "corr_delta_i_vs_sweep": tag_fit_all.corr_with_sweep,
        },
        {
            "fit": tag_fit_no12.name,
            "links": tag_fit_no12.n_links,
            "delta_tag_mm": tag_fit_no12.delta_tag_mm,
            "rho_tag_percent": tag_fit_no12.rho * 100.0,
            "rms_mm": tag_fit_no12.rms_mm,
            "corr_delta_i_vs_sweep": tag_fit_no12.corr_with_sweep,
        },
    ]
    write_csv(tables_dir / "04_tag_side_model_summary.csv", tag_fit_rows)
    delta_rows = []
    for idx, anchor in enumerate(ANCHOR_LABELS):
        delta_rows.append(
            {
                "anchor": anchor,
                "sweep_delta_mm": sweep_delta[anchor],
                "tag_fit_delta_mm": float(tag_fit_all.anchor_deltas_mm[idx]),
                "tag_fit_no_top12_delta_mm": float(tag_fit_no12.anchor_deltas_mm[idx]),
            }
        )
    write_csv(tables_dir / "04_tag_side_anchor_deltas.csv", delta_rows)
    write_csv(tables_dir / "04_tag_side_bootstrap.csv", boot_rows)
    write_csv(tables_dir / "04_tag_side_bootstrap_ci.csv", tag_ci)
    outlier_rows = link_df.loc[top12_idx].assign(abs_bias_mm=link_df.loc[top12_idx, "bias_mm"].abs()).sort_values("abs_bias_mm", ascending=False).to_dict("records")
    write_csv(tables_dir / "04_tag_side_top12_abs_bias_links.csv", outlier_rows)

    pair_path = tables_dir / "02_pair_bias_vs_distance.csv"
    pair_df = pd.read_csv(pair_path)
    in_sample_fit = fit_pair_model(pair_df)
    holdout_rows, holdout_summary = leave_pair_out(pair_df, args.pair_holdout_trials, args.seed + 202)
    holdout_summary["in_sample_rms_mm"] = in_sample_fit["rms"]
    write_csv(tables_dir / "04_pair_leave_pairs_out_trials.csv", holdout_rows)
    write_csv(tables_dir / "04_pair_leave_pairs_out_summary.csv", [holdout_summary])

    fc = load_full_compare_module(data_dir)
    mod = fc.load_eval_module()
    anchor_ids = list(range(8))
    raw = fc.load_sweep_grouped()
    mod.ANCHOR_SIGMA = fc.compute_anchor_sigma(mod, raw)
    fused = fc.fuse_all(mod, raw, anchor_ids)["v3"]
    calibrated = {
        (i, j): float(dist - 0.5 * (sweep_delta[ANCHOR_LABELS[i]] + sweep_delta[ANCHOR_LABELS[j]]))
        for (i, j), dist in fused.items()
    }

    init_base, _ = mod.solve_autopos_v1(fused, anchor_ids)
    x_base, d_base, res_base = mod.solve_v4(fused, anchor_ids, init_base)
    baseline = fc.Layout("baseline_v4io", "Baseline v4-io", x_base, d_base, {"success": bool(res_base.success), **getattr(res_base, "physical_diagnostics", {})})

    x_va, d_va, res_va = solve_v4_custom(mod, fused, anchor_ids, x_init=init_base, delay_bound_mm=400.0)
    va = fc.Layout("V-A_unbounded", "V-A unbounded delay", x_va, d_va, {"success": bool(res_va.success), **getattr(res_va, "physical_diagnostics", {})})

    init_cal, _ = mod.solve_autopos_v1(calibrated, anchor_ids)
    x_vb, d_vb, res_vb = solve_v4_custom(mod, calibrated, anchor_ids, x_init=init_cal, delay_bound_mm=None)
    vb = fc.Layout("V-B_calibrated", "V-B sweep-calibrated geometry", x_vb, d_vb, {"success": bool(res_vb.success), **getattr(res_vb, "physical_diagnostics", {})})

    x_vc, d_vc, res_vc = solve_v4_custom(mod, calibrated, anchor_ids, x_init=x_vb, delay_bound_mm=30.0)
    vc = fc.Layout("V-C_calibrated_residual", "V-C calibrated + residual delay", x_vc, d_vc, {"success": bool(res_vc.success), **getattr(res_vc, "physical_diagnostics", {})})

    layouts = {
        baseline.version: baseline,
        va.version: va,
        vb.version: vb,
        vc.version: vc,
    }
    solve_range_by_variant = {
        baseline.version: fused,
        va.version: fused,
        vb.version: calibrated,
        vc.version: calibrated,
    }
    raw_range_by_variant = {key: fused for key in layouts}
    delay_bound_by_variant = {baseline.version: 60.0, va.version: 400.0, vb.version: None, vc.version: 30.0}
    policy_by_variant = {
        baseline.version: "production bound abs(d_i)<=60, d_A=0",
        va.version: "same objective, widened abs(d_i)<=400, d_A=0",
        vb.version: "subtract sweep (Delta_i+Delta_j)/2, solve geometry only",
        vc.version: "V-B plus residual abs(d_i)<=30",
    }

    solver_rows = []
    anchor_error_rows = []
    rigid_fits: dict[str, RigidFit] = {}
    for variant, layout in layouts.items():
        summary, fit, err_rows = alignment_metrics(layout, primary_truth)
        rigid_fits[variant] = fit
        anchor_error_rows.extend(err_rows)
        summary.update(delay_summary(layout, delay_bound_by_variant[variant]))
        summary["delay_policy"] = policy_by_variant[variant]
        summary["solve_pair_rms_mm"] = residual_rms(layout, solve_range_by_variant[variant], mod, anchor_ids)
        summary["raw_pair_rms_mm"] = residual_rms(layout, raw_range_by_variant[variant], mod, anchor_ids)
        summary["baseline_gate_pass"] = bool(
            variant != baseline.version
            or (
                abs(summary["anchor_median_3d_mm"] - EXPECTED_BASELINE_MEDIAN_MM) < 2.0
                and abs(summary["anchor_rms_3d_mm"] - EXPECTED_BASELINE_RMS_MM) < 2.0
            )
        )
        solver_rows.append(summary)
        save_layout_json(layout_dir / variant / "layout.json", layout, anchor_ids)
    write_csv(tables_dir / "04_solver_ablation_summary.csv", solver_rows)
    write_csv(tables_dir / "04_solver_anchor_errors.csv", anchor_error_rows)

    loo_delta_tag = leave_one_position_delta_tag(link_df, sweep_delta)
    write_csv(tables_dir / "04_static_loo_delta_tag.csv", [{"position": k, "loo_delta_tag_mm": v} for k, v in sorted(loo_delta_tag.items())])
    static_rows, static_summary = evaluate_static_transfer(
        layouts,
        rigid_fits,
        link_df,
        tag_truth,
        truth_meta,
        sweep_delta,
        loo_delta_tag,
        mod.ANCHOR_SIGMA,
    )
    write_csv(tables_dir / "04_static_tag_transfer_positions.csv", static_rows)
    write_csv(tables_dir / "04_static_tag_transfer_summary.csv", static_summary)
    transfer_guard = next(r for r in static_summary if r["variant"] == "V-B_calibrated")

    roto_rows, roto_phys_rows, roto_summary, roto_backend = evaluate_roto_from_captures(
        fc,
        layouts,
        data.roto_df,
        mapping,
        sweep_delta,
        mod.ANCHOR_SIGMA,
    )
    write_csv(tables_dir / "04_roto_replay_all.csv", roto_rows)
    write_csv(tables_dir / "04_roto_replay_physical.csv", roto_phys_rows)
    write_csv(tables_dir / "04_roto_replay_summary.csv", roto_summary)
    write_csv(tables_dir / "04_roto_replay_backend.csv", [roto_backend])

    noise_df = pd.read_csv(tables_dir / "03_tag_link_noise.csv")
    noise_rows = []
    for anchor, g in noise_df.groupby("anchor"):
        vals = pd.to_numeric(g["range_std_mm"], errors="coerce").dropna().to_numpy(dtype=float)
        noise_rows.append(
            {
                "anchor": anchor,
                "links": int(len(vals)),
                "noise_median_std_mm": float(np.percentile(vals, 50)),
                "noise_p95_std_mm": float(np.percentile(vals, 95)),
                "flag": "F/G high-noise anomaly" if anchor in {"F", "G"} else "",
            }
        )
    noise_rows = sorted(noise_rows, key=lambda r: ANCHOR_LABELS.index(r["anchor"]))
    write_csv(tables_dir / "04_anchor_noise_side_diagnostic.csv", noise_rows)

    multipath_rows = []
    for _, row in pair_df.iterrows():
        pair = str(row["pair"])
        flag = pair in {p for p in MULTIPATH_DIRECTED_LINKS if p[0] < p[2]}
        multipath_rows.append(
            {
                "pair": pair,
                "full_residual_mm": float(row["full_residual_mm"]),
                "abs_full_residual_mm": abs(float(row["full_residual_mm"])),
                "multipath_watchlist": bool(flag),
            }
        )
    multipath_rows = sorted(multipath_rows, key=lambda r: r["abs_full_residual_mm"], reverse=True)
    multipath_display_rows = []
    seen_pairs = set()
    for row in multipath_rows:
        if row["multipath_watchlist"] or len(multipath_display_rows) < 8:
            multipath_display_rows.append(row)
            seen_pairs.add(row["pair"])
    for row in multipath_rows:
        if row["pair"] not in seen_pairs and len(multipath_display_rows) < 14:
            multipath_display_rows.append(row)
            seen_pairs.add(row["pair"])
    write_csv(tables_dir / "04_multipath_pair_residuals.csv", multipath_rows)

    fig_tag = figures_dir / "04_tag_delta_vs_sweep_delta.png"
    fig_anchor = figures_dir / "04_solver_anchor_errors.png"
    fig_static = figures_dir / "04_static_tag_transfer_errors.png"
    make_tag_delta_plot(fig_tag, sweep_delta, tag_fit_all, tag_fit_no12)
    make_anchor_error_plot(fig_anchor, anchor_error_rows)
    make_static_error_plot(fig_static, static_rows)

    build_report(
        out_dir,
        tag_fit_rows,
        tag_ci,
        outlier_rows,
        holdout_summary,
        transfer_guard,
        solver_rows,
        static_summary,
        roto_summary,
        roto_backend,
        noise_rows,
        multipath_display_rows,
        {
            "tag_delta": fig_tag.name,
            "anchor_error": fig_anchor.name,
            "static_error": fig_static.name,
        },
    )
    print(f"Phase 2 report written: {out_dir / '02_solver_ablation.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
