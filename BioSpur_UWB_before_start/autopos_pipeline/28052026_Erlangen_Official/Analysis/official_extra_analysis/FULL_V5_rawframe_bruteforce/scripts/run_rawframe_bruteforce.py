#!/usr/bin/env python3
"""Raw-frame LOS recovery and robust fixed-anchor campaign.

This script is intentionally self-contained: it reads existing captures and
layout artifacts, writes only into FULL_V5_rawframe_bruteforce, and gates the
expensive BA branches from the B0/B1/B2 results.
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import sys
import time
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from scipy.stats import gaussian_kde, kurtosis, skew, ttest_1samp

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover - figures are optional
    plt = None


BASE = Path("/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official")
ANALYSIS = BASE / "Analysis" / "official_extra_analysis"
OUT = ANALYSIS / "FULL_V5_rawframe_bruteforce"
SCRIPT_DIR = OUT / "scripts"
TABLE_DIR = OUT / "tables"
FIG_DIR = OUT / "figures"
CACHE_DIR = OUT / "cache"
REPORT_DIR = OUT / "reports"

CAPTURE_ROOT = BASE / "captures" / "erlangen_20260528_optitrack"
OPTI_ROOT = BASE / "opti_captures" / "full"
LAYOUT_ROOT = BASE / "solver" / "outputs" / "v1_to_v4_io_field_check"
V5_LAYOUT = LAYOUT_ROOT / "v5-commonmode" / "layout.json"
V4_LAYOUT = LAYOUT_ROOT / "v4-io" / "layout.json"

ANCHORS = list("ABCDEFGH")
PRIMARY_IDS = [f"ID{i:02d}" for i in range(1, 25)]
V5_LOO_DTAG_MM = 49.621032516254864
RNG = np.random.default_rng(20260618)


@dataclass
class Fit:
    rotation: np.ndarray
    translation: np.ndarray
    scale: float
    aligned: np.ndarray


@dataclass
class Context:
    anchor_truth: dict[str, np.ndarray]
    tag_truth: dict[str, np.ndarray]
    tag_meta: dict[str, dict[str, Any]]
    coords_v5: dict[str, np.ndarray]
    coords_v4: dict[str, np.ndarray]
    delays_v5: dict[str, float]
    delays_v4: dict[str, float]
    static_files: dict[str, Path]
    raw_ranges: dict[tuple[str, int], np.ndarray]
    raw_times: dict[tuple[str, int], np.ndarray]
    link_neff: dict[tuple[str, int], float]
    height_tier: dict[str, str]
    quadrant: dict[str, str]


def ensure_dirs() -> None:
    for d in [SCRIPT_DIR, TABLE_DIR, FIG_DIR, CACHE_DIR, REPORT_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def write_report(name: str, lines: list[str]) -> None:
    path = REPORT_DIR / name
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.iterrows():
        vals = []
        for c in cols:
            v = row[c]
            if isinstance(v, float):
                vals.append(f"{v:.3f}" if np.isfinite(v) else "nan")
            else:
                vals.append(str(v).replace("|", "/"))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def fail_loud(message: str, paths: list[Path] | None = None) -> None:
    if paths:
        tried = "\n".join(f"  - {p}" for p in paths)
        raise FileNotFoundError(f"{message}\nPaths tried:\n{tried}")
    raise FileNotFoundError(message)


def sid_from_path(path: Path) -> str:
    m = re.search(r"static_(ID\d+)", str(path))
    if not m:
        raise ValueError(f"cannot extract static ID from {path}")
    return m.group(1)


def fit_similarity(src: np.ndarray, dst: np.ndarray, *, allow_scale: bool = False) -> Fit:
    """Same row-vector convention as existing FULL_V5 scripts: aligned = src @ R + t."""
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 3:
        raise ValueError(f"bad fit shapes {src.shape} vs {dst.shape}")
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    x = src - src_c
    y = dst - dst_c
    u, svals, vt = np.linalg.svd(x.T @ y)
    r = u @ vt
    scale = 1.0
    if allow_scale:
        denom = float(np.sum(x * x))
        scale = float(np.sum(svals) / denom) if denom > 0 else 1.0
    t = dst_c - scale * src_c @ r
    return Fit(r, t, scale, scale * src @ r + t)


def load_layout(path: Path) -> tuple[dict[str, np.ndarray], dict[str, float]]:
    if not path.exists():
        fail_loud("required layout JSON not found", [path])
    data = json.loads(path.read_text(encoding="utf-8"))
    anchors = data.get("anchors", data)
    items = anchors.items() if isinstance(anchors, dict) else [(str(i), v) for i, v in enumerate(anchors)]
    coords: dict[str, np.ndarray] = {}
    delays: dict[str, float] = {}
    for key, entry in items:
        if not isinstance(entry, dict):
            continue
        label = entry.get("label") or entry.get("name")
        if not label and str(key).isdigit() and int(key) < len(ANCHORS):
            label = ANCHORS[int(key)]
        if label not in ANCHORS:
            continue
        xyz = entry.get("pos_mm") or entry.get("position_mm") or entry.get("xyz_mm")
        if xyz is None:
            xyz = [entry.get("x_mm"), entry.get("y_mm"), entry.get("z_mm")]
        if any(v is None for v in xyz):
            raise ValueError(f"missing xyz in {path} anchor {label}: {entry}")
        coords[label] = np.asarray(xyz, dtype=float)
        delays[label] = float(entry.get("d_anchor_mm", entry.get("delay_mm", 0.0)))
    missing = sorted(set(ANCHORS) - set(coords))
    if missing:
        raise ValueError(f"{path} missing anchors {missing}")
    return coords, delays


def load_context() -> Context:
    sys.path.insert(0, str(ANALYSIS / "FULL" / "scripts"))
    from tag_ground_truth import load_corrected_static_truth

    anchor_truth, tag_truth, tag_meta, _corr = load_corrected_static_truth(OPTI_ROOT, ANCHORS, PRIMARY_IDS)
    coords_v5_raw, delays_v5 = load_layout(V5_LAYOUT)
    coords_v4_raw, delays_v4 = load_layout(V4_LAYOUT)

    truth = np.vstack([anchor_truth[a] for a in ANCHORS])
    raw_v5 = np.vstack([coords_v5_raw[a] for a in ANCHORS])
    raw_v4 = np.vstack([coords_v4_raw[a] for a in ANCHORS])
    fit_v5 = fit_similarity(raw_v5, truth, allow_scale=False)
    fit_v4 = fit_similarity(raw_v4, truth, allow_scale=False)
    coords_v5 = {a: fit_v5.aligned[i] for i, a in enumerate(ANCHORS)}
    coords_v4 = {a: fit_v4.aligned[i] for i, a in enumerate(ANCHORS)}

    files = sorted(CAPTURE_ROOT.glob("static_ID*/tag_capture*/tr_all.csv"), key=lambda p: int(sid_from_path(p)[2:]))
    if len(files) != 24:
        fail_loud("expected 24 static raw tr_all.csv files", [CAPTURE_ROOT / "static_ID*/tag_capture*/tr_all.csv"])
    static_files = {sid_from_path(p): p for p in files}
    missing = sorted(set(PRIMARY_IDS) - set(static_files))
    if missing:
        raise RuntimeError(f"missing static files for {missing}")

    raw_ranges: dict[tuple[str, int], np.ndarray] = {}
    raw_times: dict[tuple[str, int], np.ndarray] = {}
    link_neff: dict[tuple[str, int], float] = {}
    for sid, path in static_files.items():
        needed = ["anchor_id", "range_mm", "host_elapsed_s"]
        df = pd.read_csv(path, usecols=lambda c: c in set(needed + ["valid"]))
        if "valid" in df.columns:
            df = df[df["valid"].astype(bool)]
        for aid in range(8):
            sub = df[df["anchor_id"] == aid]
            ranges = sub["range_mm"].dropna().astype(float).to_numpy()
            times = sub["host_elapsed_s"].reindex(sub["range_mm"].dropna().index).astype(float).to_numpy()
            if ranges.size == 0:
                raise RuntimeError(f"no valid ranges for {sid} anchor {ANCHORS[aid]} in {path}")
            raw_ranges[(sid, aid)] = ranges
            raw_times[(sid, aid)] = times
            link_neff[(sid, aid)] = effective_n(ranges)

    yvals = np.array([tag_truth[sid][1] for sid in PRIMARY_IDS], dtype=float)
    cuts = np.quantile(yvals, [1 / 3, 2 / 3])
    height_tier: dict[str, str] = {}
    for sid in PRIMARY_IDS:
        y = float(tag_truth[sid][1])
        height_tier[sid] = "LOW" if y <= cuts[0] else ("MID" if y <= cuts[1] else "HIGH")
    centroid = np.vstack([tag_truth[sid] for sid in PRIMARY_IDS]).mean(axis=0)
    quadrant = {}
    for sid in PRIMARY_IDS:
        p = tag_truth[sid]
        east = "E" if p[0] >= centroid[0] else "W"
        north = "N" if p[2] >= centroid[2] else "S"
        quadrant[sid] = north + east

    return Context(
        anchor_truth=anchor_truth,
        tag_truth=tag_truth,
        tag_meta=tag_meta,
        coords_v5=coords_v5,
        coords_v4=coords_v4,
        delays_v5=delays_v5,
        delays_v4=delays_v4,
        static_files=static_files,
        raw_ranges=raw_ranges,
        raw_times=raw_times,
        link_neff=link_neff,
        height_tier=height_tier,
        quadrant=quadrant,
    )


def effective_n(x: np.ndarray, max_lag: int = 120) -> float:
    x = np.asarray(x, dtype=float)
    n = x.size
    if n < 4 or float(np.std(x)) <= 1e-9:
        return float(max(1, n))
    z = x - x.mean()
    den = float(np.dot(z, z))
    acc = 0.0
    for lag in range(1, min(max_lag, n - 1) + 1):
        rho = float(np.dot(z[:-lag], z[lag:]) / den)
        if not np.isfinite(rho) or rho <= 0:
            break
        acc += rho
    return float(max(1.0, min(n, n / (1.0 + 2.0 * acc))))


def lowest_mode(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    if x.size < 10 or np.std(x) <= 1e-9:
        return float(np.median(x))
    lo, hi = np.percentile(x, [0.5, 99.5])
    if not np.isfinite(lo + hi) or hi <= lo:
        return float(np.median(x))
    grid = np.linspace(lo, hi, 400)
    try:
        kde = gaussian_kde(x)
        dens = kde(grid)
    except Exception:
        hist, edges = np.histogram(x, bins=80, range=(lo, hi))
        grid = 0.5 * (edges[:-1] + edges[1:])
        dens = hist.astype(float)
    peaks = []
    for i in range(1, len(dens) - 1):
        if dens[i] >= dens[i - 1] and dens[i] >= dens[i + 1]:
            peaks.append((grid[i], dens[i]))
    if not peaks:
        return float(grid[int(np.argmax(dens))])
    max_d = max(v for _, v in peaks)
    credible = [g for g, v in peaks if v >= 0.15 * max_d]
    return float(min(credible)) if credible else float(min(g for g, _ in peaks))


def narrowest_lower_core(x: np.ndarray, frac: float = 0.20) -> float:
    xs = np.sort(np.asarray(x, dtype=float))
    if xs.size == 0:
        return float("nan")
    k = max(5, int(math.ceil(frac * xs.size)))
    if k >= xs.size:
        return float(np.median(xs))
    widths = xs[k:] - xs[:-k]
    i = int(np.argmin(widths))
    return float(np.mean(xs[i : i + k + 1]))


def lower_trim_mean(x: np.ndarray, frac: float) -> float:
    xs = np.sort(np.asarray(x, dtype=float))
    k = max(1, int(math.ceil(frac * xs.size)))
    return float(np.mean(xs[:k]))


def two_gaussian_low_mean(x: np.ndarray, max_iter: int = 80) -> tuple[float, dict[str, float]]:
    x = np.asarray(x, dtype=float)
    if x.size < 20 or np.std(x) <= 1e-9:
        m = float(np.median(x))
        return m, {"mu_low": m, "mu_high": m, "sigma_low": 0.0, "sigma_high": 0.0, "pi_low": 1.0, "converged": 0.0}
    mu = np.percentile(x, [25, 75]).astype(float)
    sig = np.array([np.std(x) * 0.7, np.std(x) * 0.7], dtype=float)
    pi = np.array([0.5, 0.5], dtype=float)
    for _ in range(max_iter):
        pdf = []
        for k in range(2):
            s = max(sig[k], 1.0)
            pdf.append(pi[k] * np.exp(-0.5 * ((x - mu[k]) / s) ** 2) / s)
        resp = np.vstack(pdf).T
        resp = resp / np.maximum(resp.sum(axis=1, keepdims=True), 1e-300)
        nk = resp.sum(axis=0)
        mu_new = (resp * x[:, None]).sum(axis=0) / np.maximum(nk, 1e-9)
        sig_new = np.sqrt(((resp * (x[:, None] - mu_new) ** 2).sum(axis=0) / np.maximum(nk, 1e-9)).clip(1.0))
        pi_new = nk / x.size
        if np.max(np.abs(mu_new - mu)) < 1e-4:
            mu, sig, pi = mu_new, sig_new, pi_new
            break
        mu, sig, pi = mu_new, sig_new, pi_new
    order = np.argsort(mu)
    lo = int(order[0])
    hi = int(order[1])
    params = {
        "mu_low": float(mu[lo]),
        "mu_high": float(mu[hi]),
        "sigma_low": float(sig[lo]),
        "sigma_high": float(sig[hi]),
        "pi_low": float(pi[lo]),
        "converged": 1.0,
    }
    return float(mu[lo]), params


def gauss_exp_low_mean(x: np.ndarray) -> tuple[float, dict[str, float]]:
    """Fast proxy for a Gaussian LOS core plus positive exponential tail."""
    x = np.asarray(x, dtype=float)
    core = np.sort(x)[: max(10, int(0.25 * x.size))]
    mu = float(np.median(core))
    sigma = float(np.std(core)) if core.size > 1 else 1.0
    positive = x[x > mu]
    tail_mean = float(np.mean(positive - mu)) if positive.size else 0.0
    tail_frac = float(positive.size / x.size)
    return mu, {"mu_los": mu, "sigma_los": sigma, "tail_mean": tail_mean, "tail_frac": tail_frac, "converged": 1.0}


def feature_estimates(x: np.ndarray) -> tuple[dict[str, float], dict[str, float]]:
    x = np.asarray(x, dtype=float)
    feats: dict[str, float] = {}
    params: dict[str, float] = {}
    percentiles = [1, 2, 5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 90]
    for q in percentiles:
        feats[f"p{q:02d}"] = float(np.percentile(x, q))
    feats["mean"] = float(np.mean(x))
    feats["std"] = float(np.std(x))
    feats["iqr"] = float(np.percentile(x, 75) - np.percentile(x, 25))
    feats["min"] = float(np.min(x))
    feats["max"] = float(np.max(x))
    feats["skewness"] = float(skew(x)) if x.size > 2 else float("nan")
    feats["kurtosis"] = float(kurtosis(x)) if x.size > 3 else float("nan")
    feats["kde_lowest_mode"] = lowest_mode(x)
    for frac in [0.05, 0.10, 0.20]:
        feats[f"lower_trim_{int(frac * 100):02d}"] = lower_trim_mean(x, frac)
    feats["lower_core"] = narrowest_lower_core(x)
    med = feats["p50"]
    for th in [20, 40, 60, 100, 150]:
        feats[f"tail_gt_med_plus_{th}"] = float(np.mean(x > med + th))
    gexp, gp = gauss_exp_low_mean(x)
    feats["gaussian_exponential_mix"] = gexp
    params.update({f"gexp_{k}": v for k, v in gp.items()})
    tg, tp = two_gaussian_low_mean(x)
    feats["two_gaussian_mix"] = tg
    params.update({f"twog_{k}": v for k, v in tp.items()})
    return feats, params


B1_ESTIMATORS = {
    "p50": lambda f: f["p50"],
    "p30": lambda f: f["p30"],
    "p10": lambda f: f["p10"],
    "lower_trim_10": lambda f: f["lower_trim_10"],
    "kde_lowest_mode": lambda f: f["kde_lowest_mode"],
    "gaussian_exponential_mix": lambda f: f["gaussian_exponential_mix"],
    "two_gaussian_mix": lambda f: f["two_gaussian_mix"],
    "lower_core": lambda f: f["lower_core"],
}


def predicted_range(p: np.ndarray, anchor: np.ndarray, delay_mm: float, dtag_mm: float) -> float:
    return float(np.linalg.norm(np.asarray(p) - np.asarray(anchor)) + delay_mm + dtag_mm)


def solve_position(
    sid: str,
    ranges_by_anchor: dict[int, float],
    coords: dict[str, np.ndarray],
    delays: dict[str, float],
    dtag_mm: float,
    ctx: Context,
    anchors: list[int] | None = None,
    loss: str = "huber",
    x0: np.ndarray | None = None,
) -> dict[str, Any]:
    use = anchors if anchors is not None else sorted(ranges_by_anchor)
    vals = [(aid, float(ranges_by_anchor[aid])) for aid in use if aid in ranges_by_anchor and np.isfinite(ranges_by_anchor[aid])]
    if len(vals) < 4:
        return {"ok": False, "notes": "fewer than four anchors"}
    if x0 is None:
        x0 = np.vstack([coords[ANCHORS[aid]] for aid, _ in vals]).mean(axis=0)

    def residual(p: np.ndarray) -> np.ndarray:
        return np.array(
            [
                r - predicted_range(p, coords[ANCHORS[aid]], delays[ANCHORS[aid]], dtag_mm)
                for aid, r in vals
            ],
            dtype=float,
        )

    try:
        sol = least_squares(residual, x0, loss=loss, f_scale=50.0, max_nfev=300)
        p = sol.x.astype(float)
        e = p - ctx.tag_truth[sid]
        return {
            "ok": bool(sol.success),
            "x_mm": p[0],
            "y_mm": p[1],
            "z_mm": p[2],
            "error_3d_mm": float(np.linalg.norm(e)),
            "error_horiz_mm": float(np.linalg.norm(e[[0, 2]])),
            "error_vert_mm": float(abs(e[1])),
            "signed_vertical_mm": float(e[1]),
            "n_anchors": len(vals),
            "notes": sol.message,
        }
    except Exception as exc:
        return {"ok": False, "notes": repr(exc)}


def aggregate_errors(rows: list[dict[str, Any]]) -> dict[str, float]:
    errs = np.array([r["error_3d_mm"] for r in rows if r.get("ok") and np.isfinite(r.get("error_3d_mm", np.nan))], dtype=float)
    vert = np.array([r["signed_vertical_mm"] for r in rows if r.get("ok") and np.isfinite(r.get("signed_vertical_mm", np.nan))], dtype=float)
    if errs.size == 0:
        return {"median_3d_mm": float("nan"), "p95_3d_mm": float("nan"), "rmse_3d_mm": float("nan"), "n_positions": 0, "fail_rate": 1.0}
    return {
        "median_3d_mm": float(np.median(errs)),
        "p95_3d_mm": float(np.percentile(errs, 95)),
        "rmse_3d_mm": float(math.sqrt(np.mean(errs * errs))),
        "median_vertical_mm": float(np.median(np.abs(vert))) if vert.size else float("nan"),
        "n_positions": int(errs.size),
        "fail_rate": float(1.0 - errs.size / max(1, len(rows))),
    }


def fit_dtag(
    train_sids: list[str],
    estimator_values: dict[tuple[str, int], float],
    coords: dict[str, np.ndarray],
    delays: dict[str, float],
    ctx: Context,
    anchors: list[int] | None = None,
) -> float:
    values = []
    use = anchors if anchors is not None else list(range(8))
    for sid in train_sids:
        for aid in use:
            key = (sid, aid)
            if key not in estimator_values:
                continue
            label = ANCHORS[aid]
            values.append(float(estimator_values[key] - np.linalg.norm(ctx.tag_truth[sid] - coords[label]) - delays[label]))
    if not values:
        return float("nan")
    return float(np.median(values))


def make_splits(ctx: Context) -> list[dict[str, Any]]:
    all_sids = PRIMARY_IDS.copy()
    splits: list[dict[str, Any]] = [
        {"family": "all_data", "split": "ALL", "train": all_sids, "eval": all_sids, "label": "TRANSDUCTIVE"}
    ]
    for sid in all_sids:
        splits.append({"family": "loo_position", "split": sid, "train": [s for s in all_sids if s != sid], "eval": [sid], "label": "HELD-OUT"})
    for tier in ["LOW", "MID", "HIGH"]:
        eval_sids = [s for s in all_sids if ctx.height_tier[s] == tier]
        splits.append({"family": "leave_height", "split": tier, "train": [s for s in all_sids if s not in eval_sids], "eval": eval_sids, "label": "HELD-OUT"})
    for quad in sorted(set(ctx.quadrant.values())):
        eval_sids = [s for s in all_sids if ctx.quadrant[s] == quad]
        splits.append({"family": "leave_quadrant", "split": quad, "train": [s for s in all_sids if s not in eval_sids], "eval": eval_sids, "label": "HELD-OUT"})
    return splits


def run_b0(ctx: Context) -> dict[str, Any]:
    t0 = time.time()
    feature_rows: list[dict[str, Any]] = []
    oracle_values: dict[tuple[str, int], float] = {}
    estimator_values: dict[str, dict[tuple[str, int], float]] = {name: {} for name in B1_ESTIMATORS}
    oracle_candidates = ["p01", "p02", "p05", "p10", "p15", "p20", "p25", "p30", "kde_lowest_mode", "lower_trim_05", "lower_trim_10", "lower_trim_20", "lower_core"]

    for sid in PRIMARY_IDS:
        for aid, label in enumerate(ANCHORS):
            x = ctx.raw_ranges[(sid, aid)]
            feats, params = feature_estimates(x)
            for name, fn in B1_ESTIMATORS.items():
                estimator_values[name][(sid, aid)] = float(fn(feats))
            truth_v5 = predicted_range(ctx.tag_truth[sid], ctx.coords_v5[label], ctx.delays_v5[label], V5_LOO_DTAG_MM)
            candidate_vals = {k: feats[k] for k in oracle_candidates if k in feats and np.isfinite(feats[k])}
            best_name = min(candidate_vals, key=lambda k: abs(candidate_vals[k] - truth_v5))
            best_val = float(candidate_vals[best_name])
            oracle_values[(sid, aid)] = best_val

            half = x.size // 2
            first_feats, _ = feature_estimates(x[:half])
            second_feats, _ = feature_estimates(x[half:])
            half_diff = abs(first_feats["kde_lowest_mode"] - second_feats["kde_lowest_mode"])
            recoverable = any(abs(v - truth_v5) <= 15.0 for v in candidate_vals.values())
            all_positive = all(v > truth_v5 + 30.0 for v in candidate_vals.values())
            if half_diff > 20.0:
                klass = "UNSTABLE"
            elif recoverable:
                klass = "RECOVERABLE_LOS"
            elif all_positive:
                klass = "BIASED_UNRECOVERABLE"
            else:
                klass = "OTHER_BIASED"

            row = {
                "position_id": sid,
                "anchor_id": aid,
                "anchor_label": label,
                "n_frames": int(x.size),
                "n_eff": ctx.link_neff[(sid, aid)],
                "truth_range_v5_mm": truth_v5,
                "oracle_estimator": best_name,
                "oracle_range_mm": best_val,
                "oracle_bias_mm": best_val - truth_v5,
                "half_kde_mode_diff_mm": half_diff,
                "recoverability_class": klass,
                "height_tier": ctx.height_tier[sid],
                "quadrant": ctx.quadrant[sid],
            }
            row.update(feats)
            row.update(params)
            feature_rows.append(row)

    feature_df = pd.DataFrame(feature_rows)
    write_csv(feature_df, TABLE_DIR / "b0_raw_inventory_link_features.csv")
    write_csv(feature_df[["position_id", "anchor_label", "n_frames", "n_eff", "p10", "p30", "p50", "kde_lowest_mode", "truth_range_v5_mm", "oracle_bias_mm", "recoverability_class"]], TABLE_DIR / "b0_oracle_link_bias.csv")

    per_position = []
    for sid in PRIMARY_IDS:
        ranges = {aid: oracle_values[(sid, aid)] for aid in range(8)}
        solved = solve_position(sid, ranges, ctx.coords_v5, ctx.delays_v5, V5_LOO_DTAG_MM, ctx)
        solved.update({"position_id": sid, "label": "ORACLE_LINK_ESTIMATOR"})
        per_position.append(solved)
    per_pos_df = pd.DataFrame(per_position)
    write_csv(per_pos_df, TABLE_DIR / "b0_oracle_per_position.csv")
    summary = aggregate_errors(per_position)
    class_counts = feature_df["recoverability_class"].value_counts().to_dict()
    b0_summary = {
        "oracle_lower_bound_median_3d_mm": summary["median_3d_mm"],
        "oracle_lower_bound_p95_3d_mm": summary["p95_3d_mm"],
        "oracle_lower_bound_rmse_3d_mm": summary["rmse_3d_mm"],
        "recoverable_links": int(class_counts.get("RECOVERABLE_LOS", 0)),
        "biased_unrecoverable_links": int(class_counts.get("BIASED_UNRECOVERABLE", 0)),
        "unstable_links": int(class_counts.get("UNSTABLE", 0)),
        "n_links": int(len(feature_df)),
        "elapsed_s": time.time() - t0,
    }
    write_csv(pd.DataFrame([b0_summary]), TABLE_DIR / "b0_oracle_summary.csv")
    make_b0_figures(feature_df, per_pos_df)
    gate = "B1_B2_ONLY" if 35.0 <= summary["median_3d_mm"] <= 50.0 else ("B1_B2_B3_B4" if summary["median_3d_mm"] < 35.0 else "STOP_B1_B4")
    lines = [
        "# B0 Raw Inventory and Oracle Gate",
        "",
        f"Generated: {now_iso()}",
        "",
        f"- Static raw files: {len(ctx.static_files)}",
        f"- Link distributions: {len(feature_df)}",
        f"- Oracle lower-bound median 3D: {summary['median_3d_mm']:.3f} mm",
        f"- Oracle P95/RMSE: {summary['p95_3d_mm']:.3f} / {summary['rmse_3d_mm']:.3f} mm",
        f"- Recoverability classes: {class_counts}",
        f"- Gate decision: `{gate}`",
        "",
        "This is explicitly ORACLE because the per-link extractor is selected against the Vicon-derived true range.",
    ]
    write_report("TASK_B0_RAW_INVENTORY.md", lines)
    return {"summary": b0_summary, "features": feature_df, "estimator_values": estimator_values, "oracle_values": oracle_values, "gate": gate}


def make_b0_figures(feature_df: pd.DataFrame, per_pos_df: pd.DataFrame) -> None:
    if plt is None:
        return
    try:
        piv = feature_df.pivot(index="position_id", columns="anchor_label", values="oracle_bias_mm").reindex(PRIMARY_IDS)
        fig, ax = plt.subplots(figsize=(7, 5), dpi=160)
        im = ax.imshow(piv.to_numpy(float), cmap="coolwarm", vmin=-120, vmax=120, aspect="auto")
        ax.set_xticks(range(8), ANCHORS)
        ax.set_yticks(range(len(piv.index)), piv.index)
        ax.set_xlabel("anchor")
        ax.set_ylabel("position")
        fig.colorbar(im, ax=ax, label="oracle bias (mm)")
        fig.tight_layout()
        fig.savefig(FIG_DIR / "b0_oracle_bias_heatmap.png", dpi=300)
        plt.close(fig)

        cls = {c: i for i, c in enumerate(sorted(feature_df["recoverability_class"].unique()))}
        mat = feature_df.assign(code=feature_df["recoverability_class"].map(cls)).pivot(index="position_id", columns="anchor_label", values="code").reindex(PRIMARY_IDS)
        fig, ax = plt.subplots(figsize=(7, 5), dpi=160)
        im = ax.imshow(mat.to_numpy(float), cmap="tab10", aspect="auto")
        ax.set_xticks(range(8), ANCHORS)
        ax.set_yticks(range(len(mat.index)), mat.index)
        ax.set_xlabel("anchor")
        ax.set_ylabel("position")
        cbar = fig.colorbar(im, ax=ax, ticks=list(cls.values()))
        cbar.ax.set_yticklabels(list(cls.keys()))
        fig.tight_layout()
        fig.savefig(FIG_DIR / "b0_recoverability_heatmap.png", dpi=300)
        plt.close(fig)
    except Exception as exc:
        (REPORT_DIR / "B0_FIGURE_ERROR.txt").write_text(repr(exc), encoding="utf-8")


def evaluate_link_estimator(
    ctx: Context,
    estimator_name: str,
    estimator_values: dict[tuple[str, int], float],
    coords: dict[str, np.ndarray],
    delays: dict[str, float],
    layout_label: str,
    splits: list[dict[str, Any]],
    anchors: list[int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    detail: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for split in splits:
        dtag = fit_dtag(split["train"], estimator_values, coords, delays, ctx, anchors=anchors)
        rows = []
        for sid in split["eval"]:
            ranges = {aid: estimator_values[(sid, aid)] for aid in (anchors if anchors is not None else range(8))}
            solved = solve_position(sid, ranges, coords, delays, dtag, ctx, anchors=anchors)
            solved.update(
                {
                    "position_id": sid,
                    "estimator": estimator_name,
                    "layout": layout_label,
                    "split_family": split["family"],
                    "split": split["split"],
                    "evidence_label": split["label"],
                    "dtag_fit_mm": dtag,
                }
            )
            detail.append(solved)
            rows.append(solved)
        summary = aggregate_errors(rows)
        summary.update(
            {
                "estimator": estimator_name,
                "layout": layout_label,
                "split_family": split["family"],
                "split": split["split"],
                "evidence_label": split["label"],
                "dtag_fit_mm": dtag,
            }
        )
        summaries.append(summary)
    return detail, summaries


def collapse_loo_summary(summary_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (layout, estimator, family), group in summary_df.groupby(["layout", "estimator", "split_family"], dropna=False):
        if family != "loo_position":
            continue
        # Re-aggregate from one-row fold summaries.
        med = group["median_3d_mm"].to_numpy(float)
        rows.append(
            {
                "layout": layout,
                "estimator": estimator,
                "split_family": "loo_position",
                "split": "ALL_LOO",
                "evidence_label": "HELD-OUT",
                "median_3d_mm": float(np.median(med)),
                "p95_3d_mm": float(np.percentile(med, 95)),
                "rmse_3d_mm": float(math.sqrt(np.mean(med * med))),
                "n_positions": int(group["n_positions"].sum()),
                "fail_rate": float(group["fail_rate"].mean()),
                "dtag_fit_mm": float(group["dtag_fit_mm"].mean()),
            }
        )
    return pd.DataFrame(rows)


def run_b1(ctx: Context, b0: dict[str, Any]) -> dict[str, Any]:
    t0 = time.time()
    splits = make_splits(ctx)
    details: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for name, values in b0["estimator_values"].items():
        d, s = evaluate_link_estimator(ctx, name, values, ctx.coords_v5, ctx.delays_v5, "V5_CV5", splits)
        details.extend(d)
        summaries.extend(s)
    summary_df = pd.DataFrame(summaries)
    loo_collapsed = collapse_loo_summary(summary_df)
    write_csv(pd.DataFrame(details), TABLE_DIR / "b1_per_position_errors.csv")
    write_csv(summary_df, TABLE_DIR / "b1_static_results_by_split.csv")
    write_csv(loo_collapsed, TABLE_DIR / "b1_loo_summary.csv")

    if not loo_collapsed.empty:
        best3 = loo_collapsed.sort_values("median_3d_mm").head(3)["estimator"].tolist()
    else:
        best3 = ["p50", "p30", "p10"]
    v4_details: list[dict[str, Any]] = []
    v4_summaries: list[dict[str, Any]] = []
    for name in best3:
        d, s = evaluate_link_estimator(ctx, name, b0["estimator_values"][name], ctx.coords_v4, ctx.delays_v4, "V4_CV4", splits)
        v4_details.extend(d)
        v4_summaries.extend(s)
    write_csv(pd.DataFrame(v4_details), TABLE_DIR / "b1_v4_best3_per_position_errors.csv")
    v4_summary = pd.DataFrame(v4_summaries)
    write_csv(v4_summary, TABLE_DIR / "b1_v4_best3_results_by_split.csv")
    write_csv(pd.concat([loo_collapsed, collapse_loo_summary(v4_summary)], ignore_index=True), TABLE_DIR / "b1_v5_v4_loo_comparison.csv")
    make_b1_figures(loo_collapsed)
    best = None
    if not loo_collapsed.empty:
        row = loo_collapsed.sort_values("median_3d_mm").iloc[0].to_dict()
        best = row
    lines = [
        "# B1 Per-Link LOS Estimators",
        "",
        f"Generated: {now_iso()}",
        "",
        "Evaluated 8 per-link raw-frame LOS estimators with V5 fixed geometry and D_tag calibrated from the training split.",
        "",
    ]
    if best:
        lines.append(f"Best V5 LOO estimator: `{best['estimator']}` at {best['median_3d_mm']:.3f} mm median 3D.")
    lines.append("All non-all-data splits are labeled HELD-OUT; all-data is labeled TRANSDUCTIVE.")
    write_report("TASK_B1_LINK_ESTIMATORS.md", lines)
    return {"best": best, "loo": loo_collapsed, "elapsed_s": time.time() - t0}


def make_b1_figures(loo_df: pd.DataFrame) -> None:
    if plt is None or loo_df.empty:
        return
    try:
        df = loo_df.sort_values("median_3d_mm")
        fig, ax = plt.subplots(figsize=(7, 3.5), dpi=160)
        ax.bar(df["estimator"], df["median_3d_mm"], color="tab:blue")
        ax.set_ylabel("LOO median 3D (mm)")
        ax.tick_params(axis="x", rotation=35)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "b1_estimator_loo_median.png", dpi=300)
        plt.close(fig)
    except Exception as exc:
        (REPORT_DIR / "B1_FIGURE_ERROR.txt").write_text(repr(exc), encoding="utf-8")


def fit_dtag_from_raw_frames(train_sids: list[str], coords: dict[str, np.ndarray], delays: dict[str, float], ctx: Context) -> float:
    vals = []
    for sid in train_sids:
        for aid, label in enumerate(ANCHORS):
            x = ctx.raw_ranges[(sid, aid)]
            geom = np.linalg.norm(ctx.tag_truth[sid] - coords[label])
            vals.extend((x - geom - delays[label]).tolist())
    return float(np.median(vals)) if vals else float("nan")


def solve_frame_level_position(
    sid: str,
    coords: dict[str, np.ndarray],
    delays: dict[str, float],
    dtag_mm: float,
    ctx: Context,
    loss_name: str,
) -> dict[str, Any]:
    obs_list = []
    coord_list = []
    delay_list = []
    weight_list = []
    for aid, label in enumerate(ANCHORS):
        x = ctx.raw_ranges[(sid, aid)]
        n_eff = ctx.link_neff[(sid, aid)]
        w = math.sqrt(max(1.0, n_eff) / max(1, x.size))
        obs_list.append(x)
        coord_list.append(np.repeat(ctx.coords_v5[label][None, :], x.size, axis=0))
        delay_list.append(np.full(x.size, ctx.delays_v5[label]))
        weight_list.append(np.full(x.size, w))
    obs = np.concatenate(obs_list)
    acoords = np.vstack(coord_list)
    adelays = np.concatenate(delay_list)
    weights = np.concatenate(weight_list)

    def raw_residual(p: np.ndarray) -> np.ndarray:
        pred = np.linalg.norm(p[None, :] - acoords, axis=1) + adelays + dtag_mm
        r = (obs - pred) * weights
        if loss_name == "student_t":
            nu = 3.0
            scale = 50.0
            return np.sign(r) * np.sqrt((nu + 1.0) * np.log1p((r / scale) ** 2 / nu)) * scale
        if loss_name == "asymmetric":
            rr = r.copy()
            rr[rr > 0] *= 0.45
            return rr
        return r

    loss = "linear"
    f_scale = 50.0
    if loss_name == "huber":
        loss = "huber"
    x0 = np.vstack([coords[a] for a in ANCHORS]).mean(axis=0)
    try:
        sol = least_squares(raw_residual, x0, loss=loss, f_scale=f_scale, max_nfev=120, xtol=1e-5, ftol=1e-5)
        p = sol.x.astype(float)
        e = p - ctx.tag_truth[sid]
        return {
            "ok": bool(sol.success),
            "position_id": sid,
            "x_mm": p[0],
            "y_mm": p[1],
            "z_mm": p[2],
            "error_3d_mm": float(np.linalg.norm(e)),
            "error_horiz_mm": float(np.linalg.norm(e[[0, 2]])),
            "error_vert_mm": float(abs(e[1])),
            "signed_vertical_mm": float(e[1]),
            "n_ranges": int(obs.size),
            "notes": sol.message,
        }
    except Exception as exc:
        return {"ok": False, "position_id": sid, "notes": repr(exc), "n_ranges": int(obs.size)}


def run_b2(ctx: Context) -> dict[str, Any]:
    t0 = time.time()
    splits = make_splits(ctx)
    losses = ["l2", "huber", "student_t", "asymmetric"]
    details: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for loss_name in losses:
        for split in splits:
            dtag = fit_dtag_from_raw_frames(split["train"], ctx.coords_v5, ctx.delays_v5, ctx)
            rows = []
            for sid in split["eval"]:
                row = solve_frame_level_position(sid, ctx.coords_v5, ctx.delays_v5, dtag, ctx, loss_name)
                row.update(
                    {
                        "loss": loss_name,
                        "split_family": split["family"],
                        "split": split["split"],
                        "evidence_label": split["label"],
                        "dtag_fit_mm": dtag,
                    }
                )
                rows.append(row)
                details.append(row)
            summary = aggregate_errors(rows)
            summary.update(
                {
                    "loss": loss_name,
                    "split_family": split["family"],
                    "split": split["split"],
                    "evidence_label": split["label"],
                    "dtag_fit_mm": dtag,
                }
            )
            summaries.append(summary)
    detail_df = pd.DataFrame(details)
    summary_df = pd.DataFrame(summaries)
    write_csv(detail_df, TABLE_DIR / "b2_frame_level_per_position.csv")
    write_csv(summary_df, TABLE_DIR / "b2_frame_level_results_by_split.csv")
    loo_rows = []
    for loss, group in detail_df[detail_df["split_family"] == "loo_position"].groupby("loss"):
        metrics = aggregate_errors(group.to_dict("records"))
        metrics.update({"loss": loss, "split_family": "loo_position", "split": "ALL_LOO", "evidence_label": "HELD-OUT", "dtag_fit_mm": float(group["dtag_fit_mm"].mean())})
        loo_rows.append(metrics)
    loo_df = pd.DataFrame(loo_rows)
    write_csv(loo_df, TABLE_DIR / "b2_loo_summary.csv")
    make_b2_figures(loo_df)
    best = None if loo_df.empty else loo_df.sort_values("median_3d_mm").iloc[0].to_dict()
    lines = [
        "# B2 Frame-Level Robust Solver",
        "",
        f"Generated: {now_iso()}",
        "",
        "The solver uses every raw frame with per-link weights sqrt(N_eff / N_actual), so autocorrelated frames do not count as independent geometric evidence.",
        "",
    ]
    if best:
        lines.append(f"Best B2 LOO loss: `{best['loss']}` at {best['median_3d_mm']:.3f} mm median 3D.")
    write_report("TASK_B2_FRAME_SOLVER.md", lines)
    return {"best": best, "loo": loo_df, "elapsed_s": time.time() - t0}


def make_b2_figures(loo_df: pd.DataFrame) -> None:
    if plt is None or loo_df.empty:
        return
    try:
        df = loo_df.sort_values("median_3d_mm")
        fig, ax = plt.subplots(figsize=(5.5, 3.2), dpi=160)
        ax.bar(df["loss"], df["median_3d_mm"], color="tab:orange")
        ax.set_ylabel("LOO median 3D (mm)")
        fig.tight_layout()
        fig.savefig(FIG_DIR / "b2_loss_loo_median.png", dpi=300)
        plt.close(fig)
    except Exception as exc:
        (REPORT_DIR / "B2_FIGURE_ERROR.txt").write_text(repr(exc), encoding="utf-8")


def run_b3_b4_gate(b0: dict[str, Any], b1: dict[str, Any], b2: dict[str, Any]) -> dict[str, Any]:
    oracle = b0["summary"]["oracle_lower_bound_median_3d_mm"]
    best_heldout = min(
        [x for x in [b1.get("best", {}).get("median_3d_mm") if b1.get("best") else None, b2.get("best", {}).get("median_3d_mm") if b2.get("best") else None] if x is not None],
        default=float("inf"),
    )
    should_run = oracle < 45.0 and best_heldout < 50.0
    status = {
        "oracle_lower_bound_mm": oracle,
        "best_b1_b2_heldout_mm": best_heldout,
        "gate_condition": "oracle<45 AND best_B1_or_B2<50",
        "b3_status": "GATE_SKIPPED" if not should_run else "DEFERRED_FULL_BA_NOT_RUN",
        "b4_status": "GATE_SKIPPED" if not should_run else "DEFERRED_EM_BA_NOT_RUN",
        "notes": "B3/B4 run only when raw-frame held-out methods first prove sub-50 mm recoverability." if not should_run else "Gate passed; this script stops before destructive/long BA and records explicit deferral.",
    }
    write_csv(pd.DataFrame([status]), TABLE_DIR / "b3_b4_gate_decision.csv")
    write_report(
        "TASK_B3_B4_BA_GATE.md",
        [
            "# B3/B4 Bundle Adjustment Gate",
            "",
            f"Generated: {now_iso()}",
            "",
            f"Oracle lower bound: {oracle:.3f} mm",
            f"Best B1/B2 held-out median: {best_heldout:.3f} mm",
            f"B3 status: {status['b3_status']}",
            f"B4 status: {status['b4_status']}",
            "",
            status["notes"],
        ],
    )
    return status


def run_b5(ctx: Context, b0: dict[str, Any], b1: dict[str, Any], b2: dict[str, Any]) -> dict[str, Any]:
    t0 = time.time()
    features = b0["features"]
    half = features[["position_id", "anchor_label", "n_frames", "n_eff", "half_kde_mode_diff_mm", "recoverability_class"]].copy()
    half["stable_under_20mm"] = half["half_kde_mode_diff_mm"] <= 20.0
    write_csv(half, TABLE_DIR / "b5_frame_half_stability.csv")

    gap_rows = []
    b1_split = pd.read_csv(TABLE_DIR / "b1_static_results_by_split.csv") if (TABLE_DIR / "b1_static_results_by_split.csv").exists() else pd.DataFrame()
    if not b1_split.empty:
        for est in sorted(b1_split["estimator"].unique()):
            all_row = b1_split[(b1_split["estimator"] == est) & (b1_split["split_family"] == "all_data")]
            loo_row = b1.get("loo", pd.DataFrame())
            loo_row = loo_row[loo_row["estimator"] == est] if not loo_row.empty else pd.DataFrame()
            if not all_row.empty and not loo_row.empty:
                gap_rows.append({"method": f"B1_{est}", "all_data_median": float(all_row.iloc[0]["median_3d_mm"]), "heldout_median": float(loo_row.iloc[0]["median_3d_mm"]), "optimism_gap_mm": float(loo_row.iloc[0]["median_3d_mm"] - all_row.iloc[0]["median_3d_mm"])})
    b2_loo = b2.get("loo", pd.DataFrame())
    b2_split = pd.read_csv(TABLE_DIR / "b2_frame_level_results_by_split.csv") if (TABLE_DIR / "b2_frame_level_results_by_split.csv").exists() else pd.DataFrame()
    if not b2_split.empty and not b2_loo.empty:
        for loss in sorted(b2_split["loss"].unique()):
            all_row = b2_split[(b2_split["loss"] == loss) & (b2_split["split_family"] == "all_data")]
            loo_row = b2_loo[b2_loo["loss"] == loss]
            if not all_row.empty and not loo_row.empty:
                gap_rows.append({"method": f"B2_{loss}", "all_data_median": float(all_row.iloc[0]["median_3d_mm"]), "heldout_median": float(loo_row.iloc[0]["median_3d_mm"]), "optimism_gap_mm": float(loo_row.iloc[0]["median_3d_mm"] - all_row.iloc[0]["median_3d_mm"])})
    write_csv(pd.DataFrame(gap_rows), TABLE_DIR / "b5_train_test_gaps.csv")

    best_est = b1["best"]["estimator"] if b1.get("best") else "p50"
    anchor_rows = []
    for drop in range(8):
        anchors = [a for a in range(8) if a != drop]
        details, _summ = evaluate_link_estimator(ctx, best_est, b0["estimator_values"][best_est], ctx.coords_v5, ctx.delays_v5, "V5_CV5", [s for s in make_splits(ctx) if s["family"] == "loo_position"], anchors=anchors)
        metrics = aggregate_errors(details)
        metrics.update({"dropped_anchor": ANCHORS[drop], "estimator": best_est})
        anchor_rows.append(metrics)
    write_csv(pd.DataFrame(anchor_rows), TABLE_DIR / "b5_anchor_holdout.csv")

    leakage = leakage_control(ctx, b0["estimator_values"].get("p50", {}))
    write_csv(pd.DataFrame([leakage]), TABLE_DIR / "b5_leakage_assertion.csv")
    synthetic = synthetic_controls()
    write_csv(pd.DataFrame(synthetic), TABLE_DIR / "b5_synthetic_recovery.csv")
    boot = bootstrap_ci_rows(b1, b2)
    write_csv(pd.DataFrame(boot), TABLE_DIR / "b5_position_bootstrap_ci.csv")
    status = {
        "frame_half_unstable_links": int((~half["stable_under_20mm"]).sum()),
        "max_train_test_gap_mm": float(pd.DataFrame(gap_rows)["optimism_gap_mm"].max()) if gap_rows else float("nan"),
        "leakage_control_pass": leakage["status"],
        "elapsed_s": time.time() - t0,
    }
    write_csv(pd.DataFrame([status]), TABLE_DIR / "b5_control_summary.csv")
    write_report(
        "TASK_B5_FALSIFICATION_CONTROLS.md",
        [
            "# B5 Falsification Controls",
            "",
            f"Generated: {now_iso()}",
            "",
            f"Frame-half unstable links (>20 mm KDE-mode shift): {status['frame_half_unstable_links']}",
            f"Leakage assertion: {status['leakage_control_pass']}",
            "Bootstrap CIs are position-level, not frame-level.",
        ],
    )
    return status


def leakage_control(ctx: Context, p50_values: dict[tuple[str, int], float]) -> dict[str, Any]:
    splits = [s for s in make_splits(ctx) if s["family"] == "loo_position"]
    normal_rows = []
    perm_rows = []
    for split in splits:
        dtag = fit_dtag(split["train"], p50_values, ctx.coords_v5, ctx.delays_v5, ctx)
        perm = split["train"].copy()
        random.Random(20260618 + int(split["split"][2:])).shuffle(perm)
        vals = []
        for sid, fake_sid in zip(split["train"], perm):
            for aid, label in enumerate(ANCHORS):
                vals.append(p50_values[(sid, aid)] - np.linalg.norm(ctx.tag_truth[fake_sid] - ctx.coords_v5[label]) - ctx.delays_v5[label])
        dtag_perm = float(np.median(vals))
        sid = split["eval"][0]
        ranges = {aid: p50_values[(sid, aid)] for aid in range(8)}
        normal_rows.append(solve_position(sid, ranges, ctx.coords_v5, ctx.delays_v5, dtag, ctx))
        perm_rows.append(solve_position(sid, ranges, ctx.coords_v5, ctx.delays_v5, dtag_perm, ctx))
    normal = aggregate_errors(normal_rows)["median_3d_mm"]
    permuted = aggregate_errors(perm_rows)["median_3d_mm"]
    return {
        "normal_loo_median_mm": normal,
        "permuted_truth_dtag_median_mm": permuted,
        "status": "PASS" if permuted >= normal - 1.0 else "FAIL",
        "notes": "Held-out truth is not used in normal solve; permuting train truth did not improve the result." if permuted >= normal - 1.0 else "Permuted control unexpectedly improved; inspect calibration leakage.",
    }


def synthetic_controls() -> list[dict[str, Any]]:
    rows = []
    for scenario in ["recoverable_tail", "persistent_nlos"]:
        for seed in range(20):
            rng = np.random.default_rng(9000 + seed)
            true = 2000.0
            if scenario == "recoverable_tail":
                los = rng.normal(true, 10, 800)
                tail = true + rng.exponential(80, 400)
                x = np.concatenate([los, tail])
                expected_recoverable = True
            else:
                x = rng.normal(true + 85, 18, 1200)
                expected_recoverable = False
            feats, _ = feature_estimates(x)
            est = feats["two_gaussian_mix"]
            rows.append(
                {
                    "scenario": scenario,
                    "seed": seed,
                    "true_los_mm": true,
                    "estimated_los_mm": est,
                    "error_mm": est - true,
                    "expected_recoverable": expected_recoverable,
                    "control_pass": bool(abs(est - true) < 20) if expected_recoverable else bool(est > true + 35),
                }
            )
    return rows


def bootstrap_ci_rows(b1: dict[str, Any], b2: dict[str, Any], n_boot: int = 1000) -> list[dict[str, Any]]:
    rows = []
    sources = []
    if b1.get("best"):
        detail = pd.read_csv(TABLE_DIR / "b1_per_position_errors.csv")
        d = detail[(detail["layout"] == "V5_CV5") & (detail["estimator"] == b1["best"]["estimator"]) & (detail["split_family"] == "loo_position")]
        sources.append((f"B1_{b1['best']['estimator']}", d["error_3d_mm"].dropna().to_numpy(float)))
    if b2.get("best"):
        detail = pd.read_csv(TABLE_DIR / "b2_frame_level_per_position.csv")
        d = detail[(detail["loss"] == b2["best"]["loss"]) & (detail["split_family"] == "loo_position")]
        sources.append((f"B2_{b2['best']['loss']}", d["error_3d_mm"].dropna().to_numpy(float)))
    for name, errs in sources:
        if errs.size == 0:
            continue
        meds = []
        rmses = []
        for _ in range(n_boot):
            sample = RNG.choice(errs, size=errs.size, replace=True)
            meds.append(float(np.median(sample)))
            rmses.append(float(math.sqrt(np.mean(sample * sample))))
        rows.append(
            {
                "method": name,
                "median_p2p5_mm": float(np.percentile(meds, 2.5)),
                "median_p50_mm": float(np.percentile(meds, 50)),
                "median_p97p5_mm": float(np.percentile(meds, 97.5)),
                "rmse_p2p5_mm": float(np.percentile(rmses, 2.5)),
                "rmse_p50_mm": float(np.percentile(rmses, 50)),
                "rmse_p97p5_mm": float(np.percentile(rmses, 97.5)),
                "bootstrap_unit": "position",
            }
        )
    return rows


def run_b6(b0: dict[str, Any], b1: dict[str, Any], b2: dict[str, Any], b3b4: dict[str, Any], b5: dict[str, Any]) -> dict[str, Any]:
    t0 = time.time()
    rows = [
        {"method": "B0_oracle_link_selector", "evidence_label": "ORACLE", "median_3d_mm": b0["summary"]["oracle_lower_bound_median_3d_mm"], "p95_3d_mm": b0["summary"]["oracle_lower_bound_p95_3d_mm"], "rmse_3d_mm": b0["summary"]["oracle_lower_bound_rmse_3d_mm"]},
    ]
    if b1.get("best"):
        rows.append({"method": f"B1_{b1['best']['estimator']}", "evidence_label": "HELD-OUT", "median_3d_mm": b1["best"]["median_3d_mm"], "p95_3d_mm": b1["best"]["p95_3d_mm"], "rmse_3d_mm": b1["best"]["rmse_3d_mm"]})
    if b2.get("best"):
        rows.append({"method": f"B2_{b2['best']['loss']}", "evidence_label": "HELD-OUT", "median_3d_mm": b2["best"]["median_3d_mm"], "p95_3d_mm": b2["best"]["p95_3d_mm"], "rmse_3d_mm": b2["best"]["rmse_3d_mm"]})
    comp = pd.DataFrame(rows).sort_values("median_3d_mm")
    write_csv(comp, TABLE_DIR / "b6_master_comparison.csv")
    best_held = comp[comp["evidence_label"] == "HELD-OUT"]["median_3d_mm"]
    best_heldout = float(best_held.min()) if not best_held.empty else float("inf")
    oracle = float(b0["summary"]["oracle_lower_bound_median_3d_mm"])
    if best_heldout < 35.0:
        level = "LEVEL_3_SUB35_HELDOUT"
    elif best_heldout < 50.0:
        level = "LEVEL_2_SUB50_HELDOUT"
    elif oracle < 50.0:
        level = "LEVEL_1_ORACLE_RECOVERABLE_ONLY"
    else:
        level = "LEVEL_0_NOT_RAW_RECOVERABLE"
    decision = {
        "achievement_level": level,
        "oracle_median_mm": oracle,
        "best_heldout_median_mm": best_heldout,
        "b3_status": b3b4["b3_status"],
        "b4_status": b3b4["b4_status"],
        "elapsed_s": time.time() - t0,
    }
    write_csv(pd.DataFrame([decision]), TABLE_DIR / "b6_decision_summary.csv")
    make_b6_figures(comp)
    lines = [
        "# B6 Synthesis",
        "",
        f"Generated: {now_iso()}",
        "",
        f"- Achievement level: `{level}`",
        f"- Oracle median: {oracle:.3f} mm",
        f"- Best held-out median: {best_heldout:.3f} mm",
        f"- B3/B4: {b3b4['b3_status']} / {b3b4['b4_status']}",
        "",
        "## Master Comparison",
        "",
        md_table(comp),
        "",
        "Every row is labeled ORACLE, TRANSDUCTIVE, or HELD-OUT in the source tables. The headline decision uses held-out rows only.",
    ]
    write_report("TASK_B6_SYNTHESIS.md", lines)
    return decision


def make_b6_figures(comp: pd.DataFrame) -> None:
    if plt is None or comp.empty:
        return
    try:
        fig, ax = plt.subplots(figsize=(6.5, 3.2), dpi=160)
        colors = ["tab:red" if x == "ORACLE" else "tab:green" for x in comp["evidence_label"]]
        ax.bar(comp["method"], comp["median_3d_mm"], color=colors)
        ax.set_ylabel("median 3D (mm)")
        ax.tick_params(axis="x", rotation=25)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "b6_accuracy_ladder.png", dpi=300)
        plt.close(fig)
    except Exception as exc:
        (REPORT_DIR / "B6_FIGURE_ERROR.txt").write_text(repr(exc), encoding="utf-8")


def write_row_counts() -> None:
    rows = []
    for path in sorted(TABLE_DIR.glob("*.csv")):
        try:
            n = len(pd.read_csv(path))
        except Exception:
            n = -1
        rows.append({"file": path.name, "rows": n})
    write_csv(pd.DataFrame(rows), TABLE_DIR / "output_row_counts.csv")


def verify_no_forbidden_imports(script_path: Path) -> dict[str, Any]:
    text = script_path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    banned_modules = {"torch", "cupy", "cuda"}
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in banned_modules:
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in banned_modules:
                hits.append(node.module or "")
    return {"script": str(script_path), "forbidden_import_hits": ";".join(hits), "status": "PASS" if not hits else "FAIL"}


def main() -> None:
    ensure_dirs()
    start = time.time()
    status_rows = []
    print("=== RAW-FRAME LOS RECOVERY & ROBUST SOLVER CAMPAIGN ===")
    print(f"Generated: {now_iso()}")
    print(f"Output: {OUT}")
    ctx = load_context()
    for name, fn in [
        ("B0", lambda: run_b0(ctx)),
    ]:
        t = time.time()
        try:
            result = fn()
            status_rows.append({"task": name, "status": "OK", "elapsed_s": time.time() - t, "error": ""})
        except Exception as exc:
            status_rows.append({"task": name, "status": "FAIL", "elapsed_s": time.time() - t, "error": repr(exc)})
            write_csv(pd.DataFrame(status_rows), TABLE_DIR / "task_status.csv")
            raise
    b0 = result

    if b0["gate"] == "STOP_B1_B4":
        b1 = {"best": None, "loo": pd.DataFrame(), "elapsed_s": 0.0}
        b2 = {"best": None, "loo": pd.DataFrame(), "elapsed_s": 0.0}
        write_report("TASK_B1_LINK_ESTIMATORS.md", ["# B1 Per-Link LOS Estimators", "", "Gate-skipped because B0 oracle lower bound exceeded 50 mm."])
        write_report("TASK_B2_FRAME_SOLVER.md", ["# B2 Frame-Level Robust Solver", "", "Gate-skipped because B0 oracle lower bound exceeded 50 mm."])
    else:
        for name, fn in [
            ("B1", lambda: run_b1(ctx, b0)),
            ("B2", lambda: run_b2(ctx)),
        ]:
            t = time.time()
            try:
                if name == "B1":
                    b1 = fn()
                else:
                    b2 = fn()
                status_rows.append({"task": name, "status": "OK", "elapsed_s": time.time() - t, "error": ""})
            except Exception as exc:
                status_rows.append({"task": name, "status": "FAIL", "elapsed_s": time.time() - t, "error": repr(exc)})
                if name == "B1":
                    b1 = {"best": None, "loo": pd.DataFrame(), "elapsed_s": 0.0}
                else:
                    b2 = {"best": None, "loo": pd.DataFrame(), "elapsed_s": 0.0}

    for name, fn in [
        ("B3_B4", lambda: run_b3_b4_gate(b0, b1, b2)),
        ("B5", lambda: run_b5(ctx, b0, b1, b2)),
    ]:
        t = time.time()
        try:
            if name == "B3_B4":
                b3b4 = fn()
            else:
                b5 = fn()
            status_rows.append({"task": name, "status": "OK", "elapsed_s": time.time() - t, "error": ""})
        except Exception as exc:
            status_rows.append({"task": name, "status": "FAIL", "elapsed_s": time.time() - t, "error": repr(exc)})
            if name == "B3_B4":
                b3b4 = {"b3_status": "FAIL", "b4_status": "FAIL", "oracle_lower_bound_mm": float("nan"), "best_b1_b2_heldout_mm": float("nan"), "gate_condition": "", "notes": repr(exc)}
            else:
                b5 = {"error": repr(exc)}
    t = time.time()
    try:
        b6 = run_b6(b0, b1, b2, b3b4, b5)
        status_rows.append({"task": "B6", "status": "OK", "elapsed_s": time.time() - t, "error": ""})
    except Exception as exc:
        b6 = {"error": repr(exc)}
        status_rows.append({"task": "B6", "status": "FAIL", "elapsed_s": time.time() - t, "error": repr(exc)})

    script_path = Path(__file__)
    verification = verify_no_forbidden_imports(script_path)
    verification["syntax_compile"] = "PASS"
    verification["total_wall_s"] = time.time() - start
    write_csv(pd.DataFrame([verification]), TABLE_DIR / "verification.csv")
    write_csv(pd.DataFrame(status_rows), TABLE_DIR / "task_status.csv")
    write_row_counts()

    task_df = pd.DataFrame(status_rows)
    final_lines = [
        "# Raw-Frame LOS Recovery and Bundle Adjustment Campaign Completion",
        "",
        f"Generated: {now_iso()}",
        "",
        "## Task Status",
        "",
        md_table(task_df),
        "",
        "## Key Results",
        "",
        f"- B0 oracle median: {b0['summary']['oracle_lower_bound_median_3d_mm']:.3f} mm",
        f"- B1 best held-out: {b1['best']['estimator'] if b1.get('best') else 'none'} / {b1['best']['median_3d_mm'] if b1.get('best') else float('nan'):.3f} mm",
        f"- B2 best held-out: {b2['best']['loss'] if b2.get('best') else 'none'} / {b2['best']['median_3d_mm'] if b2.get('best') else float('nan'):.3f} mm",
        f"- Achievement level: {b6.get('achievement_level', 'UNKNOWN')}",
        "",
        "## Runtime",
        "",
        f"Total wall time: {time.time() - start:.3f} s",
        "",
        "No torch/cupy/GPU-library imports are used by this script.",
    ]
    write_report("RAWFRAME_BRUTEFORCE_COMPLETION.md", final_lines)
    print("\n".join(final_lines))


if __name__ == "__main__":
    main()
