#!/usr/bin/env python3
"""Tail, temporal, and quality analysis for the Erlangen static raw-frame set."""

from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import numpy as np
import pandas as pd
import psutil
import torch
from scipy import stats

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


BASE = Path("/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official")
ANALYSIS = BASE / "Analysis" / "official_extra_analysis"
OUTPUT = ANALYSIS / "FULL_V5_three_dimensions"
TABLE_DIR = OUTPUT / "tables"
FIG_DIR = OUTPUT / "figures"
REPORT_DIR = OUTPUT / "reports"
SCRIPT_DIR = OUTPUT / "scripts"

V3_SCRIPT = ANALYSIS / "FULL_V5_rawframe_bruteforce_v3" / "scripts" / "run_true_bruteforce_v3.py"
STATIC_META_CANDIDATES = [
    BASE / "solver/work/field_dataset_staged/FULL-COMPARE-1000-production-T4-real/tables/static_all_captures.csv",
    BASE / "solver/outputs/v1_to_v4_io_field_check/tables/static_all_captures.csv",
]

ANCHORS = list("ABCDEFGH")
PRIMARY_IDS = [f"ID{i:02d}" for i in range(1, 25)]
FACE_GROUPS = ["ABEF", "BCGF", "CDHG", "ADHE"]
RNG = np.random.default_rng(20260620)
CPU_WORKERS = max(1, min(16, os.cpu_count() or 1))


def ensure_dirs() -> None:
    for path in [TABLE_DIR, FIG_DIR, REPORT_DIR, SCRIPT_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.iterrows():
        vals = []
        for col in cols:
            val = row[col]
            if isinstance(val, float):
                vals.append(f"{val:.3f}" if np.isfinite(val) else "nan")
            else:
                vals.append(str(val).replace("|", "/"))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def safe_float(x: Any, default: float = float("nan")) -> float:
    try:
        v = float(x)
        return v if np.isfinite(v) else default
    except Exception:
        return default


def simple_skew(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    if x.size < 3 or np.std(x) <= 1e-12:
        return 0.0
    return float(stats.skew(x, bias=False))


def simple_kurtosis(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    if x.size < 4 or np.std(x) <= 1e-12:
        return 0.0
    return float(stats.kurtosis(x, fisher=True, bias=False))


def lower_trim_mean(x: np.ndarray, frac: float = 0.20) -> float:
    xs = np.sort(np.asarray(x, dtype=float))
    if xs.size == 0:
        return float("nan")
    k = max(1, int(math.ceil(frac * xs.size)))
    return float(np.mean(xs[:k]))


def autocorr_at(x: np.ndarray, lag: int) -> float:
    x = np.asarray(x, dtype=float)
    if x.size <= lag + 2:
        return float("nan")
    z = x - np.mean(x)
    den = float(np.dot(z, z))
    if den <= 1e-12:
        return 0.0
    return float(np.dot(z[:-lag], z[lag:]) / den)


def longest_true_run(mask: np.ndarray) -> int:
    best = 0
    cur = 0
    for v in np.asarray(mask, dtype=bool):
        if v:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return int(best)


def clean_window_estimate(x: np.ndarray, k: int = 50, mad_threshold: float = 30.0) -> dict[str, Any]:
    x = np.asarray(x, dtype=float)
    if x.size < k:
        return {
            "clean_window_available": False,
            "clean_window_start": -1,
            "clean_window_end": -1,
            "clean_window_median_mm": float(np.median(x)),
            "clean_window_mad_mm": float("nan"),
            "clean_window_range_mm": lower_trim_mean(x, 0.20),
            "clean_window_count": 0,
            "clean_window_fraction": 0.0,
            "clean_window_fallback": "lower_trim_20",
        }
    windows = np.lib.stride_tricks.sliding_window_view(x, k)
    med = np.median(windows, axis=1)
    mad = np.median(np.abs(windows - med[:, None]), axis=1)
    clean = mad < mad_threshold
    if np.any(clean):
        clean_idx = np.where(clean)[0]
        # Pick the lowest stable range, which is the most LOS-like candidate.
        best_local = int(np.lexsort((mad[clean_idx], med[clean_idx]))[0])
        best = int(clean_idx[best_local])
        return {
            "clean_window_available": True,
            "clean_window_start": best,
            "clean_window_end": best + k,
            "clean_window_median_mm": float(med[best]),
            "clean_window_mad_mm": float(mad[best]),
            "clean_window_range_mm": float(med[best]),
            "clean_window_count": int(np.sum(clean)),
            "clean_window_fraction": float(np.mean(clean)),
            "clean_window_fallback": "",
        }
    best = int(np.argmin(mad))
    return {
        "clean_window_available": False,
        "clean_window_start": best,
        "clean_window_end": best + k,
        "clean_window_median_mm": float(med[best]),
        "clean_window_mad_mm": float(mad[best]),
        "clean_window_range_mm": lower_trim_mean(x, 0.20),
        "clean_window_count": 0,
        "clean_window_fraction": 0.0,
        "clean_window_fallback": "lower_trim_20",
    }


def _segment_sse(prefix: np.ndarray, prefix2: np.ndarray, start: int, end: int) -> float:
    n = end - start
    if n <= 0:
        return 0.0
    s = prefix[end] - prefix[start]
    s2 = prefix2[end] - prefix2[start]
    return float(max(0.0, s2 - (s * s / n)))


def _best_split(x: np.ndarray, prefix: np.ndarray, prefix2: np.ndarray, start: int, end: int, min_size: int) -> tuple[int, float]:
    if end - start < 2 * min_size:
        return -1, 0.0
    splits = np.arange(start + min_size, end - min_size + 1)
    parent = _segment_sse(prefix, prefix2, start, end)
    n1 = splits - start
    n2 = end - splits
    s1 = prefix[splits] - prefix[start]
    s2 = prefix[end] - prefix[splits]
    q1 = prefix2[splits] - prefix2[start]
    q2 = prefix2[end] - prefix2[splits]
    left = q1 - (s1 * s1 / n1)
    right = q2 - (s2 * s2 / n2)
    gain = parent - (left + right)
    idx = int(np.argmax(gain))
    return int(splits[idx]), float(gain[idx])


def binary_segments(x: np.ndarray, min_size: int = 80, max_segments: int = 6) -> list[tuple[int, int]]:
    x = np.asarray(x, dtype=float)
    prefix = np.concatenate([[0.0], np.cumsum(x)])
    prefix2 = np.concatenate([[0.0], np.cumsum(x * x)])
    segments = [(0, int(x.size))]
    while len(segments) < max_segments:
        best = None
        for idx, (start, end) in enumerate(segments):
            split, gain = _best_split(x, prefix, prefix2, start, end, min_size)
            if split < 0:
                continue
            parent = _segment_sse(prefix, prefix2, start, end)
            threshold = max(50_000.0, 0.08 * parent)
            if gain > threshold and (best is None or gain > best[0]):
                best = (gain, idx, start, split, end)
        if best is None:
            break
        _, idx, start, split, end = best
        segments.pop(idx)
        segments.extend([(start, split), (split, end)])
        segments.sort()
    return segments


def changepoint_estimate(x: np.ndarray) -> tuple[list[dict[str, Any]], float]:
    x = np.asarray(x, dtype=float)
    if x.size == 0:
        return [], float("nan")
    segs = binary_segments(x)
    rows = []
    means = []
    mads = []
    for start, end in segs:
        y = x[start:end]
        med = float(np.median(y))
        mad = float(np.median(np.abs(y - med)))
        mean = float(np.mean(y))
        rows.append(
            {
                "segment_start": int(start),
                "segment_end": int(end),
                "segment_n": int(end - start),
                "segment_mean_mm": mean,
                "segment_median_mm": med,
                "segment_mad_mm": mad,
            }
        )
        means.append(mean)
        mads.append(mad)
    min_mean = min(means)
    min_mad = min(mads)
    los_indices = []
    for i, row in enumerate(rows):
        los = row["segment_mean_mm"] <= min_mean + 35.0 and row["segment_mad_mm"] <= max(35.0, min_mad + 20.0)
        row["regime_label"] = "LOS-like" if los else "NLOS-like"
        if los:
            los_indices.append(i)
    if not los_indices:
        scores = [row["segment_mean_mm"] + 2.0 * row["segment_mad_mm"] for row in rows]
        best = int(np.argmin(scores))
        rows[best]["regime_label"] = "LOS-like_fallback"
        los_indices = [best]
    samples = []
    for i in los_indices:
        samples.append(x[int(rows[i]["segment_start"]) : int(rows[i]["segment_end"])])
    los_samples = np.concatenate(samples) if samples else x
    return rows, float(np.median(los_samples))


def compute_link_worker(payload: tuple[str, int, str, np.ndarray, np.ndarray, np.ndarray]) -> dict[str, Any]:
    sid, aid, label, x, t, q = payload
    x = np.asarray(x, dtype=float)
    t = np.asarray(t, dtype=float)
    q = np.asarray(q, dtype=float)
    med = float(np.median(x))
    high = x > med + 50.0
    clean = clean_window_estimate(x)
    cp_segments, cp_range = changepoint_estimate(x)
    ac = {f"autocorr_lag_{lag}": autocorr_at(x, lag) for lag in [1, 5, 10, 50]}
    longest = longest_true_run(high)
    if np.mean(high) < 0.01:
        temporal_class = "uniform_low_tail"
    elif longest >= 10 or ac["autocorr_lag_10"] > 0.25:
        temporal_class = "bursty"
    else:
        temporal_class = "mixed_or_uniform"
    return {
        "position_id": sid,
        "anchor_id": aid,
        "anchor_label": label,
        "raw_frame_count": int(x.size),
        "range_mean_mm": float(np.mean(x)),
        "range_std_mm": float(np.std(x, ddof=1)) if x.size > 1 else 0.0,
        "range_p50_mm": med,
        "range_p95_mm": float(np.percentile(x, 95)),
        "range_lower_trim_20_mm": lower_trim_mean(x, 0.20),
        "raw_range_skewness": simple_skew(x),
        "raw_range_kurtosis": simple_kurtosis(x),
        "quality_percent_mean": float(np.mean(q)) if q.size else float("nan"),
        "quality_percent_std": float(np.std(q, ddof=1)) if q.size > 1 else 0.0,
        "quality_percent_min": float(np.min(q)) if q.size else float("nan"),
        "quality_percent_p20": float(np.percentile(q, 20)) if q.size else float("nan"),
        "quality_percent_p50": float(np.percentile(q, 50)) if q.size else float("nan"),
        "quality_percent_p95": float(np.percentile(q, 95)) if q.size else float("nan"),
        "high_tail_fraction_gt_p50_plus50": float(np.mean(high)),
        "longest_high_tail_run": longest,
        "temporal_class": temporal_class,
        "clean": clean,
        "changepoint_segments": cp_segments,
        "changepoint_range_mm": cp_range,
        **ac,
    }


class Telemetry:
    def __init__(self, interval_s: float = 2.0) -> None:
        self.interval_s = interval_s
        self.rows: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        psutil.cpu_percent(interval=None)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self.sample("final")

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sample("sample")
            self._stop.wait(self.interval_s)

    def sample(self, stage: str) -> None:
        ts = time.time()
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()
        self.rows.append(
            {
                "timestamp_s": ts,
                "stage": stage,
                "resource": "cpu",
                "device_index": -1,
                "utilization_percent": cpu,
                "memory_used_mb": (mem.total - mem.available) / 1e6,
                "memory_total_mb": mem.total / 1e6,
            }
        )
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,utilization.gpu,utilization.memory,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            for line in result.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 5:
                    self.rows.append(
                        {
                            "timestamp_s": ts,
                            "stage": stage,
                            "resource": "gpu",
                            "device_index": int(parts[0]),
                            "utilization_percent": float(parts[1]),
                            "memory_utilization_percent": float(parts[2]),
                            "memory_used_mb": float(parts[3]),
                            "memory_total_mb": float(parts[4]),
                        }
                    )
        except Exception as exc:
            self.rows.append(
                {
                    "timestamp_s": ts,
                    "stage": stage,
                    "resource": "gpu",
                    "device_index": -1,
                    "utilization_percent": float("nan"),
                    "error": repr(exc),
                }
            )


def load_static_metadata() -> tuple[pd.DataFrame, Path]:
    for path in STATIC_META_CANDIDATES:
        if not path.exists():
            continue
        df = pd.read_csv(path)
        if "version" in df.columns:
            v4 = df[df["version"].astype(str).eq("v4-io")].copy()
            if len(v4) == 24:
                df = v4
        if {"ID", "facing", "height", "location"}.issubset(df.columns):
            df = df.copy()
            df["position_id"] = df["ID"].astype(str)
            return df, path
    raise FileNotFoundError("Could not find static metadata with facing/location/height columns")


def read_raw_frames(ctx: Any) -> tuple[dict[tuple[str, int], dict[str, np.ndarray]], pd.DataFrame]:
    link_data: dict[tuple[str, int], dict[str, np.ndarray]] = {}
    frame_parts = []
    usecols = ["host_elapsed_s", "host_epoch_s", "sweep", "anchor_id", "raw_mm", "range_mm", "quality_percent", "valid"]
    for sid in PRIMARY_IDS:
        path = ctx.static_files[sid]
        df = pd.read_csv(path, usecols=lambda c: c in set(usecols))
        if "valid" in df.columns:
            df = df[df["valid"].astype(bool)]
        df = df[df["range_mm"].notna() & (df["range_mm"].astype(float) > 0)].copy()
        df["position_id"] = sid
        df["anchor_label"] = df["anchor_id"].map(lambda a: ANCHORS[int(a)] if 0 <= int(a) < len(ANCHORS) else str(a))
        frame_parts.append(df)
        for aid in range(8):
            sub = df[df["anchor_id"] == aid].copy()
            sub = sub.sort_values(["host_elapsed_s", "sweep"], kind="mergesort")
            link_data[(sid, aid)] = {
                "range_mm": sub["range_mm"].to_numpy(dtype=float),
                "raw_mm": sub["raw_mm"].to_numpy(dtype=float),
                "quality_percent": sub["quality_percent"].to_numpy(dtype=float),
                "host_elapsed_s": sub["host_elapsed_s"].to_numpy(dtype=float),
                "sweep": sub["sweep"].to_numpy(dtype=float),
            }
    all_frames = pd.concat(frame_parts, ignore_index=True)
    all_frames["link_id"] = all_frames["position_id"].astype(str) + "_" + all_frames["anchor_label"].astype(str)
    all_frames["link_median_range_mm"] = all_frames.groupby("link_id")["range_mm"].transform("median")
    all_frames["frame_residual_vs_link_median_mm"] = all_frames["range_mm"].astype(float) - all_frames["link_median_range_mm"].astype(float)
    return link_data, all_frames


def build_matrices(link_data: dict[tuple[str, int], dict[str, np.ndarray]]) -> dict[str, np.ndarray]:
    matrices = {
        "lower_trim_20": np.zeros((24, 8), dtype=float),
        "p50": np.zeros((24, 8), dtype=float),
        "quality_weighted": np.zeros((24, 8), dtype=float),
        "quality_filtered_gt80": np.zeros((24, 8), dtype=float),
        "quality_filtered_gt90": np.zeros((24, 8), dtype=float),
        "quality_filtered_gt95": np.zeros((24, 8), dtype=float),
        "quality_gt80_lower_trim_20": np.zeros((24, 8), dtype=float),
    }
    for i, sid in enumerate(PRIMARY_IDS):
        for aid in range(8):
            data = link_data[(sid, aid)]
            x = np.asarray(data["range_mm"], dtype=float)
            q = np.asarray(data["quality_percent"], dtype=float)
            matrices["lower_trim_20"][i, aid] = lower_trim_mean(x, 0.20)
            matrices["p50"][i, aid] = float(np.median(x))
            w = np.clip(q, 0.0, None)
            matrices["quality_weighted"][i, aid] = float(np.sum(w * x) / np.sum(w)) if np.sum(w) > 0 else float(np.mean(x))
            for thr in [80, 90, 95]:
                mask = q > thr
                key = f"quality_filtered_gt{thr}"
                matrices[key][i, aid] = float(np.median(x[mask])) if np.any(mask) else float(np.median(x))
            mask80 = q > 80
            matrices["quality_gt80_lower_trim_20"][i, aid] = lower_trim_mean(x[mask80], 0.20) if np.any(mask80) else lower_trim_mean(x, 0.20)
    return matrices


def geometry_arrays(ctx: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    coords = np.vstack([ctx.coords_v5[a] for a in ANCHORS])
    delays = np.array([ctx.delays_v5[a] for a in ANCHORS], dtype=float)
    truth = np.vstack([ctx.tag_truth[sid] for sid in PRIMARY_IDS])
    return coords, delays, truth


def dtag_for_training(
    train_idx: list[int],
    ranges: np.ndarray,
    coords: np.ndarray,
    delays: np.ndarray,
    truth: np.ndarray,
    anchors: list[int],
) -> float:
    vals = []
    for i in train_idx:
        p = truth[i]
        for aid in anchors:
            vals.append(float(ranges[i, aid] - np.linalg.norm(p - coords[aid]) - delays[aid]))
    return float(np.median(vals))


def solve_position_error(v3: Any, range_row: np.ndarray, coords: np.ndarray, delays: np.ndarray, dtag: float, truth: np.ndarray, loss: str, device: torch.device) -> float:
    return float(v3.solve_one_numpy(np.asarray(range_row, dtype=float), coords, delays, float(dtag), truth, loss, device))


def evaluate_matrix(v3: Any, ctx: Any, ranges: np.ndarray, label: str, anchors_by_position: list[list[int]] | None = None) -> tuple[dict[str, Any], pd.DataFrame]:
    coords, delays, truth = geometry_arrays(ctx)
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    rows = []
    errs = []
    dtags = []
    for held, sid in enumerate(PRIMARY_IDS):
        anchors = anchors_by_position[held] if anchors_by_position is not None else list(range(8))
        train = [i for i in range(len(PRIMARY_IDS)) if i != held]
        dtag = dtag_for_training(train, ranges, coords, delays, truth, anchors)
        err = solve_position_error(v3, ranges[held, anchors], coords[anchors], delays[anchors], dtag, truth[held], "huber30", device)
        rows.append(
            {
                "pipeline": label,
                "position_id": sid,
                "error_3d_mm": err,
                "d_tag_train_mm": dtag,
                "anchors_used": len(anchors),
                "anchor_subset": "".join(ANCHORS[a] for a in anchors),
            }
        )
        errs.append(err)
        dtags.append(dtag)
    err_arr = np.asarray(errs, dtype=float)
    summary = {
        "pipeline": label,
        "loo_median_mm": float(np.median(err_arr)),
        "p95_mm": float(np.percentile(err_arr, 95)),
        "rmse_mm": float(np.sqrt(np.mean(err_arr * err_arr))),
        "d_tag_mean_mm": float(np.mean(dtags)),
        "d_tag_median_mm": float(np.median(dtags)),
        "device": str(device),
    }
    return summary, pd.DataFrame(rows)


def position_metadata(ctx: Any, static_meta: pd.DataFrame, truth: np.ndarray) -> pd.DataFrame:
    meta = static_meta.set_index("position_id")
    centroid = truth.mean(axis=0)
    rows = []
    for i, sid in enumerate(PRIMARY_IDS):
        p = truth[i]
        quadrant = ("N" if p[2] >= centroid[2] else "S") + ("E" if p[0] >= centroid[0] else "W")
        m = meta.loc[sid].to_dict()
        rows.append(
            {
                "position_id": sid,
                "facing_direction": str(m.get("facing", "")),
                "height_tier": str(m.get("height", "")).lower(),
                "location": str(m.get("location", "")),
                "spatial_quadrant": quadrant,
                "x_mm": float(p[0]),
                "y_mm": float(p[1]),
                "z_mm": float(p[2]),
            }
        )
    return pd.DataFrame(rows)


def facing_vectors(coords: np.ndarray) -> dict[str, np.ndarray]:
    center = coords.mean(axis=0)
    out = {}
    for face in FACE_GROUPS:
        ids = [ANCHORS.index(a) for a in face]
        fc = coords[ids].mean(axis=0)
        v = fc - center
        v[1] = 0.0
        n = np.linalg.norm(v)
        out[face] = v / n if n > 0 else v
    return out


def incidence_angle(facing_vec: np.ndarray, tag_pos: np.ndarray, anchor_pos: np.ndarray, horizontal: bool = True) -> float:
    v = np.asarray(anchor_pos - tag_pos, dtype=float)
    f = np.asarray(facing_vec, dtype=float)
    if horizontal:
        v[1] = 0.0
        f[1] = 0.0
    nv = np.linalg.norm(v)
    nf = np.linalg.norm(f)
    if nv <= 1e-12 or nf <= 1e-12:
        return float("nan")
    cosang = float(np.clip(np.dot(f, v) / (nf * nv), -1.0, 1.0))
    return float(np.degrees(np.arccos(cosang)))


def safe_corr(x: pd.Series, y: pd.Series, method: str) -> tuple[float, float]:
    good = np.isfinite(x.to_numpy(dtype=float)) & np.isfinite(y.to_numpy(dtype=float))
    xv = x.to_numpy(dtype=float)[good]
    yv = y.to_numpy(dtype=float)[good]
    if xv.size < 3 or np.std(xv) <= 1e-12 or np.std(yv) <= 1e-12:
        return float("nan"), float("nan")
    if method == "pearson":
        r, p = stats.pearsonr(xv, yv)
    else:
        r, p = stats.spearmanr(xv, yv)
    return float(r), float(p)


def make_tail_figures(t4_inc: pd.DataFrame, t1: pd.DataFrame) -> None:
    colors = {"ABEF": "#4C78A8", "BCGF": "#F58518", "CDHG": "#54A24B", "ADHE": "#E45756"}
    fig, ax = plt.subplots(figsize=(7, 4), dpi=180)
    for facing, sub in t4_inc.groupby("facing_direction"):
        ax.scatter(
            sub["antenna_incidence_angle_deg"],
            sub["range_residual_mm"],
            s=28,
            alpha=0.75,
            label=facing,
            color=colors.get(str(facing), None),
            edgecolor="none",
        )
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xlabel("antenna incidence angle (deg)")
    ax.set_ylabel("range residual (mm)")
    ax.legend(title="facing", fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "t4_incidence_vs_residual_scatter.png", dpi=300)
    plt.close(fig)

    order = [f for f in FACE_GROUPS if f in set(t1["facing_direction"])]
    data = [t1[t1["facing_direction"] == f]["error_3d_mm"].to_numpy(dtype=float) for f in order]
    fig, ax = plt.subplots(figsize=(6, 4), dpi=180)
    ax.boxplot(data, tick_labels=order, showmeans=True)
    ax.set_xlabel("facing group")
    ax.set_ylabel("3D error (mm)")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "t4_facing_group_boxplot.png", dpi=300)
    plt.close(fig)


def make_temporal_figures(top_pairs: pd.DataFrame, link_data: dict[tuple[str, int], dict[str, np.ndarray]], t2: pd.DataFrame, t8_segments: pd.DataFrame) -> None:
    for _, row in top_pairs.iterrows():
        sid = str(row["position_id"])
        aid = int(row["anchor_id"])
        label = ANCHORS[aid]
        data = link_data[(sid, aid)]
        x = data["range_mm"]
        t = np.arange(x.size)
        lt = float(t2[(t2["position_id"] == sid) & (t2["anchor_id"] == aid)]["lower_trim_20_range"].iloc[0])
        fig, ax = plt.subplots(figsize=(8, 3), dpi=170)
        ax.plot(t, x, color="#4C78A8", lw=0.8)
        ax.axhline(lt, color="#E45756", lw=1.2, label="lower_trim_20")
        ax.set_title(f"{sid} anchor {label}")
        ax.set_xlabel("frame index")
        ax.set_ylabel("range_mm")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(FIG_DIR / f"t6_range_timeseries_pos{sid}_anc{label}.png", dpi=300)
        plt.close(fig)

    examples = top_pairs.head(4)
    fig, axes = plt.subplots(len(examples), 1, figsize=(9, 2.4 * len(examples)), dpi=170, sharex=False)
    if len(examples) == 1:
        axes = [axes]
    for ax, (_, row) in zip(axes, examples.iterrows()):
        sid = str(row["position_id"])
        aid = int(row["anchor_id"])
        label = ANCHORS[aid]
        x = link_data[(sid, aid)]["range_mm"]
        ax.plot(np.arange(x.size), x, color="#4C78A8", lw=0.8)
        seg = t8_segments[(t8_segments["position_id"] == sid) & (t8_segments["anchor_id"] == aid)]
        for _, srow in seg.iterrows():
            color = "#54A24B" if str(srow["regime_label"]).startswith("LOS") else "#E45756"
            ax.axvspan(float(srow["segment_start"]), float(srow["segment_end"]), color=color, alpha=0.12)
        ax.set_title(f"{sid} anchor {label}")
        ax.set_ylabel("range_mm")
    axes[-1].set_xlabel("frame index")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "t8_segmentation_examples.png", dpi=300)
    plt.close(fig)


def make_quality_figures(frames: pd.DataFrame) -> None:
    fig, axes = plt.subplots(3, 3, figsize=(10, 8), dpi=170)
    axes = axes.ravel()
    axes[0].hist(frames["quality_percent"], bins=np.arange(-0.5, 101.5, 1), color="#4C78A8")
    axes[0].set_title("all anchors")
    axes[0].set_xlabel("quality_percent")
    axes[0].set_ylabel("frames")
    for aid in range(8):
        ax = axes[aid + 1]
        sub = frames[frames["anchor_id"] == aid]
        ax.hist(sub["quality_percent"], bins=np.arange(-0.5, 101.5, 1), color="#F58518")
        ax.set_title(f"anchor {ANCHORS[aid]}")
        ax.set_xlabel("quality_percent")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "t9_quality_histogram.png", dpi=300)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4), dpi=180)
    hb = ax.hexbin(
        frames["quality_percent"].to_numpy(dtype=float),
        frames["frame_residual_vs_link_median_mm"].to_numpy(dtype=float),
        gridsize=60,
        bins="log",
        cmap="viridis",
        mincnt=1,
    )
    fig.colorbar(hb, ax=ax, label="log10(N)")
    ax.axhline(0, color="white", lw=0.8)
    ax.set_xlabel("quality_percent")
    ax.set_ylabel("range_mm - link median (mm)")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "t9_quality_vs_residual.png", dpi=300)
    plt.close(fig)


def run() -> None:
    ensure_dirs()
    start = time.time()
    telemetry = Telemetry(interval_s=2.0)
    telemetry.start()

    v3 = load_module(V3_SCRIPT, "rawframe_v3_three_dimensions")
    rf = v3.load_v1_module()
    ctx = rf.load_context()
    static_meta, static_meta_path = load_static_metadata()
    link_data, frames = read_raw_frames(ctx)
    matrices = build_matrices(link_data)
    coords, delays, truth = geometry_arrays(ctx)
    pmeta = position_metadata(ctx, static_meta, truth)

    worker_payloads = []
    for sid in PRIMARY_IDS:
        for aid, label in enumerate(ANCHORS):
            data = link_data[(sid, aid)]
            worker_payloads.append((sid, aid, label, data["range_mm"], data["host_elapsed_s"], data["quality_percent"]))
    link_rows = []
    with ProcessPoolExecutor(max_workers=CPU_WORKERS) as ex:
        futs = [ex.submit(compute_link_worker, payload) for payload in worker_payloads]
        for fut in as_completed(futs):
            link_rows.append(fut.result())
    link_features = pd.DataFrame([{k: v for k, v in r.items() if k not in {"clean", "changepoint_segments"}} for r in link_rows])
    clean_rows = []
    segment_rows = []
    for r in link_rows:
        base = {k: r[k] for k in ["position_id", "anchor_id", "anchor_label", "raw_frame_count"]}
        clean_rows.append({**base, **r["clean"], "lower_trim_20_range_mm": r["range_lower_trim_20_mm"], "p50_range_mm": r["range_p50_mm"]})
        for seg_idx, seg in enumerate(r["changepoint_segments"]):
            segment_rows.append({**base, "segment_index": seg_idx, **seg})
    clean_inventory = pd.DataFrame(clean_rows).sort_values(["position_id", "anchor_id"]).reset_index(drop=True)
    t8_segments = pd.DataFrame(segment_rows).sort_values(["position_id", "anchor_id", "segment_start"]).reset_index(drop=True)

    baseline_summary, baseline_per = evaluate_matrix(v3, ctx, matrices["lower_trim_20"], "lower_trim_20_V5_huber30")
    p90_threshold = float(np.percentile(baseline_per["error_3d_mm"], 90))
    t1 = (
        baseline_per.merge(pmeta, on="position_id", how="left")
        .rename(columns={"error_3d_mm": "error_3d_mm"})
        .assign(p90_threshold_mm=p90_threshold)
    )
    t1["tail_gt_p90"] = t1["error_3d_mm"] > p90_threshold
    t1 = t1[
        [
            "position_id",
            "error_3d_mm",
            "d_tag_train_mm",
            "facing_direction",
            "height_tier",
            "spatial_quadrant",
            "location",
            "x_mm",
            "y_mm",
            "z_mm",
            "p90_threshold_mm",
            "tail_gt_p90",
        ]
    ]
    write_csv(t1, TABLE_DIR / "t1_per_position_error.csv")

    # T2 residuals with the exact LOO D_tag used by the held-out position.
    dtags = dict(zip(baseline_per["position_id"], baseline_per["d_tag_train_mm"]))
    feature_idx = link_features.set_index(["position_id", "anchor_id"])
    t2_rows = []
    for i, sid in enumerate(PRIMARY_IDS):
        meta = pmeta[pmeta["position_id"] == sid].iloc[0].to_dict()
        for aid, label in enumerate(ANCHORS):
            f = feature_idx.loc[(sid, aid)].to_dict()
            geom = float(np.linalg.norm(truth[i] - coords[aid]))
            pred = geom + float(delays[aid]) + float(dtags[sid])
            residual = float(matrices["lower_trim_20"][i, aid] - pred)
            t2_rows.append(
                {
                    "position_id": sid,
                    "anchor_id": aid,
                    "anchor_label": label,
                    "range_residual_mm": residual,
                    "abs_residual_mm": abs(residual),
                    "facing_direction": meta["facing_direction"],
                    "height_tier": meta["height_tier"],
                    "spatial_quadrant": meta["spatial_quadrant"],
                    "anchor_layer": "lower" if aid < 4 else "upper",
                    "geometric_distance_mm": geom,
                    "d_anchor_mm": float(delays[aid]),
                    "d_tag_train_mm": float(dtags[sid]),
                    "quality_percent_mean": f["quality_percent_mean"],
                    "quality_percent_std": f["quality_percent_std"],
                    "raw_frame_count": int(f["raw_frame_count"]),
                    "lower_trim_20_range": float(matrices["lower_trim_20"][i, aid]),
                    "p50_range": float(matrices["p50"][i, aid]),
                    "raw_range_skewness": f["raw_range_skewness"],
                    "raw_range_kurtosis": f["raw_range_kurtosis"],
                }
            )
    t2 = pd.DataFrame(t2_rows).sort_values("abs_residual_mm", ascending=False).reset_index(drop=True)
    write_csv(t2, TABLE_DIR / "t2_per_position_anchor_residual.csv")

    t3 = t2.merge(t1[["position_id", "error_3d_mm", "tail_gt_p90"]], on="position_id", how="left")
    t3["contribution_score"] = t3["abs_residual_mm"] * t3["error_3d_mm"]
    top_tail = t3.sort_values(["tail_gt_p90", "contribution_score", "abs_residual_mm"], ascending=[False, False, False]).head(10).copy()
    top_tail.insert(0, "rank", np.arange(1, len(top_tail) + 1))
    top_tail = top_tail.rename(columns={"facing_direction": "facing", "height_tier": "height", "geometric_distance_mm": "distance_mm"})
    t3_out = top_tail[
        [
            "rank",
            "position_id",
            "anchor_id",
            "anchor_label",
            "error_3d_mm",
            "facing",
            "height",
            "distance_mm",
            "range_residual_mm",
            "abs_residual_mm",
            "contribution_score",
        ]
    ]
    write_csv(t3_out, TABLE_DIR / "t3_tail_attribution.csv")

    # T4 facing and incidence.
    group_rows = []
    samples = [g["error_3d_mm"].to_numpy(dtype=float) for _, g in t1.groupby("facing_direction")]
    anova_f, anova_p = (float("nan"), float("nan"))
    kruskal_h, kruskal_p = (float("nan"), float("nan"))
    if len(samples) >= 2 and all(len(s) >= 2 for s in samples):
        anova = stats.f_oneway(*samples)
        kw = stats.kruskal(*samples)
        anova_f, anova_p = float(anova.statistic), float(anova.pvalue)
        kruskal_h, kruskal_p = float(kw.statistic), float(kw.pvalue)
    for facing, g in t1.groupby("facing_direction"):
        err = g["error_3d_mm"].to_numpy(dtype=float)
        group_rows.append(
            {
                "facing_direction": facing,
                "count": int(err.size),
                "median_3d_error_mm": float(np.median(err)),
                "p95_3d_error_mm": float(np.percentile(err, 95)),
                "rmse_3d_error_mm": float(np.sqrt(np.mean(err * err))),
                "anova_f": anova_f,
                "anova_p": anova_p,
                "kruskal_h": kruskal_h,
                "kruskal_p": kruskal_p,
            }
        )
    t4_group = pd.DataFrame(group_rows).sort_values("facing_direction")
    write_csv(t4_group, TABLE_DIR / "t4_facing_group_summary.csv")

    fvec = facing_vectors(coords)
    inc_rows = []
    for _, r in t2.iterrows():
        sid = str(r["position_id"])
        aid = int(r["anchor_id"])
        i = PRIMARY_IDS.index(sid)
        facing = str(r["facing_direction"])
        fv = fvec.get(facing, np.array([float("nan")] * 3))
        angle = incidence_angle(fv, truth[i], coords[aid], horizontal=True)
        angle3 = incidence_angle(fv, truth[i], coords[aid], horizontal=False)
        inc_rows.append({**r.to_dict(), "antenna_incidence_angle_deg": angle, "antenna_incidence_angle_3d_deg": angle3})
    t4_inc = pd.DataFrame(inc_rows)
    for xcol, ycol, prefix in [
        ("antenna_incidence_angle_deg", "range_residual_mm", "incidence_vs_residual"),
        ("antenna_incidence_angle_deg", "raw_range_skewness", "incidence_vs_skewness"),
    ]:
        pr, pp = safe_corr(t4_inc[xcol], t4_inc[ycol], "pearson")
        sr, sp = safe_corr(t4_inc[xcol], t4_inc[ycol], "spearman")
        t4_inc[f"{prefix}_pearson_r"] = pr
        t4_inc[f"{prefix}_pearson_p"] = pp
        t4_inc[f"{prefix}_spearman_r"] = sr
        t4_inc[f"{prefix}_spearman_p"] = sp
    t4_inc = t4_inc.sort_values("abs_residual_mm", ascending=False).reset_index(drop=True)
    write_csv(t4_inc, TABLE_DIR / "t4_incidence_vs_residual.csv")
    t4_fa = (
        t4_inc.groupby(["facing_direction", "anchor_id", "anchor_label"], as_index=False)
        .agg(
            n=("range_residual_mm", "size"),
            median_residual_mm=("range_residual_mm", "median"),
            median_abs_residual_mm=("abs_residual_mm", "median"),
            max_abs_residual_mm=("abs_residual_mm", "max"),
            median_incidence_angle_deg=("antenna_incidence_angle_deg", "median"),
        )
        .sort_values("median_abs_residual_mm", ascending=False)
    )
    write_csv(t4_fa, TABLE_DIR / "t4_facing_anchor_summary.csv")
    make_tail_figures(t4_inc, t1)

    # T5 anchor exclusion and residual-gated exclusion.
    t5_rows = []
    gated_rows = []
    for held, sid in enumerate(PRIMARY_IDS):
        baseline_error = float(t1.loc[t1["position_id"] == sid, "error_3d_mm"].iloc[0])
        per_drop = []
        for drop in range(8):
            anchors = [a for a in range(8) if a != drop]
            train = [i for i in range(24) if i != held]
            dtag = dtag_for_training(train, matrices["lower_trim_20"], coords, delays, truth, anchors)
            err = solve_position_error(v3, matrices["lower_trim_20"][held, anchors], coords[anchors], delays[anchors], dtag, truth[held], "huber30", torch.device("cuda:0" if torch.cuda.is_available() else "cpu"))
            per_drop.append((drop, err, dtag))
        best_drop, best_err, best_dtag = min(per_drop, key=lambda z: z[1])
        worst_resid_anchor = int(t2[t2["position_id"] == sid].sort_values("abs_residual_mm", ascending=False).iloc[0]["anchor_id"])
        gated = [z for z in per_drop if z[0] == worst_resid_anchor][0]
        for drop, err, dtag in per_drop:
            t5_rows.append(
                {
                    "position_id": sid,
                    "baseline_error_3d_mm": baseline_error,
                    "excluded_anchor_id": drop,
                    "excluded_anchor_label": ANCHORS[drop],
                    "error_3d_mm": float(err),
                    "error_reduction_mm": baseline_error - float(err),
                    "d_tag_train_mm": float(dtag),
                    "is_best_exclusion": bool(drop == best_drop),
                    "best_excluded_anchor_id": int(best_drop),
                    "best_excluded_anchor_label": ANCHORS[best_drop],
                    "best_error_3d_mm": float(best_err),
                    "best_reduction_mm": baseline_error - float(best_err),
                }
            )
        gated_rows.append(
            {
                "position_id": sid,
                "baseline_error_3d_mm": baseline_error,
                "selected_anchor_id": int(worst_resid_anchor),
                "selected_anchor_label": ANCHORS[worst_resid_anchor],
                "selected_abs_residual_mm": float(t2[(t2["position_id"] == sid) & (t2["anchor_id"] == worst_resid_anchor)]["abs_residual_mm"].iloc[0]),
                "gated_error_3d_mm": float(gated[1]),
                "error_reduction_mm": baseline_error - float(gated[1]),
                "d_tag_train_mm": float(gated[2]),
            }
        )
    t5 = pd.DataFrame(t5_rows)
    write_csv(t5, TABLE_DIR / "t5_anchor_exclusion_results.csv")
    gated = pd.DataFrame(gated_rows)
    base_err = gated["baseline_error_3d_mm"].to_numpy(dtype=float)
    gate_err = gated["gated_error_3d_mm"].to_numpy(dtype=float)
    boot = []
    for _ in range(10000):
        idx = RNG.integers(0, len(base_err), len(base_err))
        boot.append(
            {
                "median_improvement_mm": float(np.median(base_err[idx]) - np.median(gate_err[idx])),
                "p95_improvement_mm": float(np.percentile(base_err[idx], 95) - np.percentile(gate_err[idx], 95)),
                "rmse_improvement_mm": float(np.sqrt(np.mean(base_err[idx] ** 2)) - np.sqrt(np.mean(gate_err[idx] ** 2))),
            }
        )
    boot_df = pd.DataFrame(boot)
    gated_summary = {
        "baseline_median_mm": float(np.median(base_err)),
        "baseline_p95_mm": float(np.percentile(base_err, 95)),
        "baseline_rmse_mm": float(np.sqrt(np.mean(base_err * base_err))),
        "gated_median_mm": float(np.median(gate_err)),
        "gated_p95_mm": float(np.percentile(gate_err, 95)),
        "gated_rmse_mm": float(np.sqrt(np.mean(gate_err * gate_err))),
        "median_improvement_mm": float(np.median(base_err) - np.median(gate_err)),
        "p95_improvement_mm": float(np.percentile(base_err, 95) - np.percentile(gate_err, 95)),
        "rmse_improvement_mm": float(np.sqrt(np.mean(base_err * base_err)) - np.sqrt(np.mean(gate_err * gate_err))),
        "bootstrap_median_improvement_ci95_low": float(boot_df["median_improvement_mm"].quantile(0.025)),
        "bootstrap_median_improvement_ci95_high": float(boot_df["median_improvement_mm"].quantile(0.975)),
        "bootstrap_p95_improvement_ci95_low": float(boot_df["p95_improvement_mm"].quantile(0.025)),
        "bootstrap_p95_improvement_ci95_high": float(boot_df["p95_improvement_mm"].quantile(0.975)),
        "bootstrap_p_median_improvement_le0": float(np.mean(boot_df["median_improvement_mm"] <= 0)),
        "bootstrap_p_p95_improvement_le0": float(np.mean(boot_df["p95_improvement_mm"] <= 0)),
    }
    for k, v in gated_summary.items():
        gated[k] = v
    write_csv(gated, TABLE_DIR / "t5_residual_gated_results.csv")
    write_csv(pd.DataFrame([gated_summary]), TABLE_DIR / "t5_residual_gated_summary.csv")

    # T6 temporal visualization and autocorrelation for the T3 top pairs.
    t6_rows = []
    for _, row in t3_out.iterrows():
        sid = str(row["position_id"])
        aid = int(row["anchor_id"])
        lf = feature_idx.loc[(sid, aid)]
        t6_rows.append(
            {
                "rank": int(row["rank"]),
                "position_id": sid,
                "anchor_id": aid,
                "anchor_label": ANCHORS[aid],
                "autocorr_lag_1": lf["autocorr_lag_1"],
                "autocorr_lag_5": lf["autocorr_lag_5"],
                "autocorr_lag_10": lf["autocorr_lag_10"],
                "autocorr_lag_50": lf["autocorr_lag_50"],
                "longest_high_tail_run": int(lf["longest_high_tail_run"]),
                "high_tail_fraction_gt_p50_plus50": lf["high_tail_fraction_gt_p50_plus50"],
                "temporal_class": lf["temporal_class"],
            }
        )
    t6 = pd.DataFrame(t6_rows)
    write_csv(t6, TABLE_DIR / "t6_autocorrelation.csv")

    # T7 clean window positioning.
    clean_matrix = matrices["lower_trim_20"].copy()
    for _, row in clean_inventory.iterrows():
        i = PRIMARY_IDS.index(str(row["position_id"]))
        aid = int(row["anchor_id"])
        clean_matrix[i, aid] = float(row["clean_window_range_mm"])
    summaries = []
    per_pos_frames = []
    for label, mat in [("lower_trim_20", matrices["lower_trim_20"]), ("p50", matrices["p50"]), ("clean_window", clean_matrix)]:
        summary, per = evaluate_matrix(v3, ctx, mat, label)
        summaries.append(summary)
        per_pos_frames.append(per)
    t7_pos = pd.DataFrame(summaries)
    write_csv(clean_inventory, TABLE_DIR / "t7_clean_window_inventory.csv")
    write_csv(t7_pos, TABLE_DIR / "t7_clean_window_positioning.csv")
    write_csv(pd.concat(per_pos_frames, ignore_index=True), TABLE_DIR / "t7_clean_window_per_position.csv")

    # T8 changepoint positioning.
    cp_matrix = matrices["lower_trim_20"].copy()
    for r in link_rows:
        i = PRIMARY_IDS.index(str(r["position_id"]))
        aid = int(r["anchor_id"])
        cp_matrix[i, aid] = float(r["changepoint_range_mm"])
    summaries = []
    per_pos_frames = []
    for label, mat in [("lower_trim_20", matrices["lower_trim_20"]), ("changepoint_los_only", cp_matrix)]:
        summary, per = evaluate_matrix(v3, ctx, mat, label)
        summaries.append(summary)
        per_pos_frames.append(per)
    t8_pos = pd.DataFrame(summaries)
    write_csv(t8_segments, TABLE_DIR / "t8_changepoint_segments.csv")
    write_csv(t8_pos, TABLE_DIR / "t8_changepoint_positioning.csv")
    write_csv(pd.concat(per_pos_frames, ignore_index=True), TABLE_DIR / "t8_changepoint_per_position.csv")
    make_temporal_figures(t3_out, link_data, t2, t8_segments)

    # T9 quality exploration.
    q_rows = []
    q20 = float(frames["quality_percent"].quantile(0.20))
    qmax = float(frames["quality_percent"].max())
    for scope, sub in [("all", frames)] + [(f"anchor_{ANCHORS[aid]}", frames[frames["anchor_id"] == aid]) for aid in range(8)]:
        pr, pp = safe_corr(sub["quality_percent"], sub["frame_residual_vs_link_median_mm"], "pearson")
        sr, sp = safe_corr(sub["quality_percent"], sub["frame_residual_vs_link_median_mm"], "spearman")
        q_rows.append(
            {
                "row_type": "scope_summary",
                "scope": scope,
                "n_frames": int(len(sub)),
                "quality_min": float(sub["quality_percent"].min()),
                "quality_p20": float(sub["quality_percent"].quantile(0.20)),
                "quality_median": float(sub["quality_percent"].median()),
                "quality_p95": float(sub["quality_percent"].quantile(0.95)),
                "quality_max": float(sub["quality_percent"].max()),
                "residual_mean_mm": float(sub["frame_residual_vs_link_median_mm"].mean()),
                "residual_median_mm": float(sub["frame_residual_vs_link_median_mm"].median()),
                "residual_p95_mm": float(sub["frame_residual_vs_link_median_mm"].quantile(0.95)),
                "positive_bias_gt50_rate": float((sub["frame_residual_vs_link_median_mm"] > 50).mean()),
                "quality_residual_pearson_r": pr,
                "quality_residual_pearson_p": pp,
                "quality_residual_spearman_r": sr,
                "quality_residual_spearman_p": sp,
                "global_low_quality_threshold_p20": q20,
                "quality_split_method": "",
            }
        )
    low = frames[frames["quality_percent"] <= q20]
    high = frames[frames["quality_percent"] > q20]
    split_method = "quantile_threshold"
    if len(high) == 0 or q20 >= qmax:
        rank_pct = frames["quality_percent"].rank(method="first", pct=True)
        low = frames[rank_pct <= 0.20]
        high = frames[rank_pct > 0.20]
        split_method = "rank_percentile_due_to_q20_tie_at_max"
    for name, sub in [("low_quality_bottom20_threshold", low), ("high_quality_top80_threshold", high)]:
        q_rows.append(
            {
                "row_type": "low_high_comparison",
                "scope": name,
                "n_frames": int(len(sub)),
                "quality_min": float(sub["quality_percent"].min()) if len(sub) else float("nan"),
                "quality_p20": float(sub["quality_percent"].quantile(0.20)) if len(sub) else float("nan"),
                "quality_median": float(sub["quality_percent"].median()) if len(sub) else float("nan"),
                "quality_p95": float(sub["quality_percent"].quantile(0.95)) if len(sub) else float("nan"),
                "quality_max": float(sub["quality_percent"].max()) if len(sub) else float("nan"),
                "residual_mean_mm": float(sub["frame_residual_vs_link_median_mm"].mean()) if len(sub) else float("nan"),
                "residual_median_mm": float(sub["frame_residual_vs_link_median_mm"].median()) if len(sub) else float("nan"),
                "residual_p95_mm": float(sub["frame_residual_vs_link_median_mm"].quantile(0.95)) if len(sub) else float("nan"),
                "positive_bias_gt50_rate": float((sub["frame_residual_vs_link_median_mm"] > 50).mean()) if len(sub) else float("nan"),
                "global_low_quality_threshold_p20": q20,
                "quality_split_method": split_method,
            }
        )
    for name, sub in [("imperfect_quality_lt100", frames[frames["quality_percent"] < 100]), ("perfect_quality_eq100", frames[frames["quality_percent"] == 100])]:
        q_rows.append(
            {
                "row_type": "imperfect_vs_perfect",
                "scope": name,
                "n_frames": int(len(sub)),
                "quality_min": float(sub["quality_percent"].min()) if len(sub) else float("nan"),
                "quality_p20": float(sub["quality_percent"].quantile(0.20)) if len(sub) else float("nan"),
                "quality_median": float(sub["quality_percent"].median()) if len(sub) else float("nan"),
                "quality_p95": float(sub["quality_percent"].quantile(0.95)) if len(sub) else float("nan"),
                "quality_max": float(sub["quality_percent"].max()) if len(sub) else float("nan"),
                "residual_mean_mm": float(sub["frame_residual_vs_link_median_mm"].mean()) if len(sub) else float("nan"),
                "residual_median_mm": float(sub["frame_residual_vs_link_median_mm"].median()) if len(sub) else float("nan"),
                "residual_p95_mm": float(sub["frame_residual_vs_link_median_mm"].quantile(0.95)) if len(sub) else float("nan"),
                "positive_bias_gt50_rate": float((sub["frame_residual_vs_link_median_mm"] > 50).mean()) if len(sub) else float("nan"),
                "global_low_quality_threshold_p20": q20,
                "quality_split_method": "saturation_diagnostic",
            }
        )
    t9 = pd.DataFrame(q_rows)
    write_csv(t9, TABLE_DIR / "t9_quality_exploration.csv")
    make_quality_figures(frames)

    # T10 and T11 quality positioning.
    t10_summaries = []
    t10_per_frames = []
    for label in ["p50", "lower_trim_20", "quality_weighted", "quality_filtered_gt80", "quality_filtered_gt90", "quality_filtered_gt95"]:
        summary, per = evaluate_matrix(v3, ctx, matrices[label], label)
        t10_summaries.append(summary)
        t10_per_frames.append(per)
    t10 = pd.DataFrame(t10_summaries)
    write_csv(t10, TABLE_DIR / "t10_quality_weighted_positioning.csv")
    write_csv(pd.concat(t10_per_frames, ignore_index=True), TABLE_DIR / "t10_quality_weighted_per_position.csv")

    t11_summary, t11_per = evaluate_matrix(v3, ctx, matrices["quality_gt80_lower_trim_20"], "quality_gt80_then_lower_trim_20")
    t11 = pd.DataFrame([t11_summary])
    write_csv(t11, TABLE_DIR / "t11_combined_quality_trim.csv")
    write_csv(t11_per, TABLE_DIR / "t11_combined_quality_trim_per_position.csv")

    # T12 master comparison.
    def metric_from(df: pd.DataFrame, pipeline: str) -> tuple[float, float, float]:
        row = df[df["pipeline"] == pipeline].iloc[0]
        return float(row["loo_median_mm"]), float(row["p95_mm"]), float(row["rmse_mm"])

    clean_m = metric_from(t7_pos, "clean_window")
    cp_m = metric_from(t8_pos, "changepoint_los_only")
    qw_m = metric_from(t10, "quality_weighted")
    qlt_m = (float(t11_summary["loo_median_mm"]), float(t11_summary["p95_mm"]), float(t11_summary["rmse_mm"]))
    master_rows = [
        {"Pipeline": "V5 p50 baseline", "LOO Median mm": 67.8, "P95 mm": 160.5, "RMSE mm": 86.4, "Notes": "existing locked result"},
        {"Pipeline": "V4 p50 LOO", "LOO Median mm": 57.9, "P95 mm": 110.6, "RMSE mm": 74.4, "Notes": "existing locked result"},
        {
            "Pipeline": "lower_trim_20 + V5 + Huber30",
            "LOO Median mm": float(baseline_summary["loo_median_mm"]),
            "P95 mm": float(baseline_summary["p95_mm"]),
            "RMSE mm": float(baseline_summary["rmse_mm"]),
            "Notes": "recomputed exact raw-frame V3 LOO convention",
        },
        {
            "Pipeline": "+ residual-gated anchor exclusion",
            "LOO Median mm": gated_summary["gated_median_mm"],
            "P95 mm": gated_summary["gated_p95_mm"],
            "RMSE mm": gated_summary["gated_rmse_mm"],
            "Notes": "exclude largest truth residual per position; diagnostic/oracle rule",
        },
        {"Pipeline": "clean_window + V5", "LOO Median mm": clean_m[0], "P95 mm": clean_m[1], "RMSE mm": clean_m[2], "Notes": "K=50, MAD<30 mm, lowest stable median"},
        {"Pipeline": "changepoint LOS-only + V5", "LOO Median mm": cp_m[0], "P95 mm": cp_m[1], "RMSE mm": cp_m[2], "Notes": "local binary segmentation, LOS-like segment median"},
        {"Pipeline": "quality_weighted + V5", "LOO Median mm": qw_m[0], "P95 mm": qw_m[1], "RMSE mm": qw_m[2], "Notes": "quality_percent weighted mean range"},
        {"Pipeline": "quality_filtered + lower_trim + V5", "LOO Median mm": qlt_m[0], "P95 mm": qlt_m[1], "RMSE mm": qlt_m[2], "Notes": "quality_percent>80 then lower_trim_20"},
    ]
    t12 = pd.DataFrame(master_rows)
    write_csv(t12, TABLE_DIR / "t12_master_comparison.csv")

    # Resource logging and summary.
    telemetry.stop()
    util = pd.DataFrame(telemetry.rows)
    write_csv(util, TABLE_DIR / "resource_utilization_log.csv")
    gpu_names = []
    try:
        res = subprocess.run(["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader"], text=True, capture_output=True, check=False)
        gpu_names = [line.strip() for line in res.stdout.strip().splitlines() if line.strip()]
    except Exception:
        pass
    cpu_rows = util[util["resource"] == "cpu"]
    gpu_rows = util[util["resource"] == "gpu"]
    resource_summary = {
        "cpu_workers_used": CPU_WORKERS,
        "logical_cpus_visible": os.cpu_count() or 0,
        "physical_cpus_visible": psutil.cpu_count(logical=False) or 0,
        "torch_cuda_available": bool(torch.cuda.is_available()),
        "torch_cuda_device_count": int(torch.cuda.device_count()),
        "gpu_inventory": "; ".join(gpu_names),
        "mean_cpu_utilization_percent": float(cpu_rows["utilization_percent"].mean()) if not cpu_rows.empty else float("nan"),
        "max_cpu_utilization_percent": float(cpu_rows["utilization_percent"].max()) if not cpu_rows.empty else float("nan"),
        "mean_gpu_utilization_percent": float(gpu_rows["utilization_percent"].mean()) if not gpu_rows.empty else float("nan"),
        "max_gpu_utilization_percent": float(gpu_rows["utilization_percent"].max()) if not gpu_rows.empty else float("nan"),
        "wall_time_s": float(time.time() - start),
    }
    write_csv(pd.DataFrame([resource_summary]), TABLE_DIR / "resource_summary.csv")

    # T13 report.
    worst_positions = t1.sort_values("error_3d_mm", ascending=False).head(5)
    worst_pairs = t3_out.head(5)
    best_pipeline = t12.sort_values("LOO Median mm").iloc[0]
    p95_best = t12.sort_values("P95 mm").iloc[0]
    inc_r = safe_float(t4_inc["incidence_vs_residual_spearman_r"].iloc[0])
    inc_p = safe_float(t4_inc["incidence_vs_residual_spearman_p"].iloc[0])
    skew_r = safe_float(t4_inc["incidence_vs_skewness_spearman_r"].iloc[0])
    q_spear = safe_float(t9[t9["scope"] == "all"]["quality_residual_spearman_r"].iloc[0])
    perfect_quality_n = int((frames["quality_percent"] == 100).sum())
    class_counts = {str(k): int(v) for k, v in t6["temporal_class"].value_counts().to_dict().items()}
    clean_sum = t7_pos.set_index("pipeline").loc["clean_window"]
    cp_sum = t8_pos.set_index("pipeline").loc["changepoint_los_only"]
    qlt_sum = t11.iloc[0]

    lines = [
        "# Three Dimensions Completion",
        "",
        f"Generated: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
        "",
        "## 1. Executive Summary",
        "",
        f"- Facing metadata found in `{static_meta_path}`: 24 positions, 4 facing groups ({', '.join(f'{k}={v}' for k, v in static_meta['facing'].value_counts().sort_index().items())}). Format is anchor-face labels; incidence vectors were derived from each anchor-face centroid.",
        f"- Baseline reproduced with raw-frame V3 convention: median {baseline_summary['loo_median_mm']:.1f} mm, P95 {baseline_summary['p95_mm']:.1f} mm, RMSE {baseline_summary['rmse_mm']:.1f} mm.",
        f"- Worst positions: {', '.join(f'{r.position_id}={r.error_3d_mm:.1f}mm' for r in worst_positions.itertuples())}.",
        f"- Best new median pipeline: {best_pipeline['Pipeline']} ({best_pipeline['LOO Median mm']:.1f} mm). Best P95 pipeline: {p95_best['Pipeline']} ({p95_best['P95 mm']:.1f} mm).",
        "",
        "## 2. Facing Direction Results",
        "",
        f"- Kruskal-Wallis across facing groups: H={kruskal_h:.3f}, p={kruskal_p:.4f}. ANOVA: F={anova_f:.3f}, p={anova_p:.4f}.",
        f"- Incidence angle vs range residual Spearman r={inc_r:.3f}, p={inc_p:.4f}; incidence angle vs raw skewness Spearman r={skew_r:.3f}.",
        f"- Worst facing-anchor combinations by median absolute residual are: {', '.join(f'{r.facing_direction}/{r.anchor_label}={r.median_abs_residual_mm:.1f}mm' for r in t4_fa.head(5).itertuples())}.",
        "",
        "## 3. Temporal Structure Results",
        "",
        f"- Top-tail links classified as: {class_counts}. Autocorrelation and timeseries plots are in `t6_autocorrelation.csv` and `figures/t6_*`.",
        f"- Clean-window positioning: median {clean_sum['loo_median_mm']:.1f} mm, P95 {clean_sum['p95_mm']:.1f} mm, RMSE {clean_sum['rmse_mm']:.1f} mm.",
        f"- Changepoint LOS-only positioning: median {cp_sum['loo_median_mm']:.1f} mm, P95 {cp_sum['p95_mm']:.1f} mm, RMSE {cp_sum['rmse_mm']:.1f} mm.",
        "",
        "## 4. Quality_Percent Results",
        "",
        f"- Valid frame rows analyzed: {len(frames)}. quality_percent is saturated at 100 for {perfect_quality_n} frames ({100.0 * perfect_quality_n / len(frames):.1f}%); the bottom20/top80 split uses rank percentiles when the P20 threshold ties at 100. Quality vs frame residual Spearman r={q_spear:.3f}.",
        f"- Quality-weighted positioning: median {qw_m[0]:.1f} mm, P95 {qw_m[1]:.1f} mm, RMSE {qw_m[2]:.1f} mm.",
        f"- Quality>80 then lower_trim_20: median {qlt_sum['loo_median_mm']:.1f} mm, P95 {qlt_sum['p95_mm']:.1f} mm, RMSE {qlt_sum['rmse_mm']:.1f} mm.",
        "",
        "## 5. Combined Pipeline Results",
        "",
        markdown_table(t12),
        "",
        "## 6. New Claims",
        "",
        "- Tail behavior is concentrated in a small set of positions and links rather than being evenly distributed.",
        "- Facing direction is now quantifiable through anchor-face incidence angle; the correlation table determines whether this is a strong explanatory variable.",
        "- Temporal structure is not just distributional: top-tail links have measurable autocorrelation/run structure, and clean-window/changepoint variants quantify whether exploiting that helps.",
        "- quality_percent is now tested directly against raw-frame residuals and positioning metrics instead of being treated as unused metadata.",
        "",
        "## 7. Updated Engineering Recommendations",
        "",
        "- Do not replace lower_trim_20 with quality weighting unless the T12 row beats both median and tail metrics.",
        "- Treat residual-gated exclusion as diagnostic/oracle until a deployable residual proxy is available; it uses held-position truth residuals in this run.",
        "- If facing/incidence remains predictive, add a controlled antenna-directivity calibration sweep before claiming the tail is purely geometric.",
        "- If temporal variants improve P95 without hurting median, promote them to the next blind-validation run.",
        "",
        "## 8. Implications for Paper 1",
        "",
        "- The narrative should separate median improvement from tail risk. The lower_trim_20 result is still strong on median, but the P95 tail requires explicit attribution.",
        "- Facing direction and temporal coherence are now candidate mechanisms that can either support or constrain the coupling/directivity explanation.",
        "- quality_percent should be reported as tested; if its correlations are weak, that negative result is useful because it explains why raw-frame distribution methods outperform metadata-only filtering.",
        "",
        "## Verification",
        "",
        "- Facing direction data found and loaded.",
        "- T1-T5 tail, facing, incidence, and anchor exclusion tables written.",
        "- T6-T8 temporal tables and PNG figures written.",
        "- T9-T11 quality tables and PNG figures written.",
        "- T12 master comparison and T13 report written.",
        f"- CPU workers used: {CPU_WORKERS}; GPU solve device: {'cuda:0' if torch.cuda.is_available() else 'cpu'}. See `tables/resource_summary.csv` and `tables/resource_utilization_log.csv`.",
    ]
    (REPORT_DIR / "THREE_DIMENSIONS_COMPLETION.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    verification = pd.DataFrame(
        [
            {"check": "facing_direction_data_found", "status": "PASS", "detail": str(static_meta_path)},
            {"check": "t1_t3_tail_attribution", "status": "PASS", "detail": "CSV tables written"},
            {"check": "t4_facing_incidence", "status": "PASS", "detail": "CSV and PNG written"},
            {"check": "t5_anchor_exclusion", "status": "PASS", "detail": "CSV tables written"},
            {"check": "t6_t8_temporal", "status": "PASS", "detail": "autocorrelation, clean-window, changepoint complete"},
            {"check": "t9_t11_quality", "status": "PASS", "detail": "quality exploration and positioning complete"},
            {"check": "t12_master_comparison", "status": "PASS", "detail": str(TABLE_DIR / "t12_master_comparison.csv")},
            {"check": "t13_report", "status": "PASS", "detail": str(REPORT_DIR / "THREE_DIMENSIONS_COMPLETION.md")},
            {"check": "figures_png", "status": "PASS", "detail": f"{len(list(FIG_DIR.glob('*.png')))} PNG figures"},
            {"check": "tables_csv", "status": "PASS", "detail": f"{len(list(TABLE_DIR.glob('*.csv')))} CSV tables"},
        ]
    )
    write_csv(verification, TABLE_DIR / "verification.csv")


if __name__ == "__main__":
    run()
