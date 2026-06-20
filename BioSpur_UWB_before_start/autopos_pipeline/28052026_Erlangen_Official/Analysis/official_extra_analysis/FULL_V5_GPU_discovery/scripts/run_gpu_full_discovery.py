#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import itertools
import json
import math
import os
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

THIS = Path(__file__).resolve()
ANALYSIS = THIS.parents[2]
BASE = THIS.parents[4]
OUT_ROOT = ANALYSIS / "FULL_V5_GPU_discovery"
TABLES = OUT_ROOT / "tables"
FIGURES = OUT_ROOT / "figures"
REPORTS = OUT_ROOT / "reports"
SCRIPTS = OUT_ROOT / "scripts"
TIER1_ROOT = ANALYSIS / "FULL_V5_GPU_tier1"
TIER1_SCRIPT = TIER1_ROOT / "scripts/run_gpu_tier1.py"
LOCAL_NVIDIA_CANDIDATES = [
    OUT_ROOT / "local_nvidia_580159/extracted/usr/lib/x86_64-linux-gnu",
    TIER1_ROOT / "local_nvidia_580159/extracted/usr/lib/x86_64-linux-gnu",
]
_kernel_driver_text = Path("/proc/driver/nvidia/version").read_text(errors="ignore") if Path("/proc/driver/nvidia/version").exists() else ""
if "580.159" in _kernel_driver_text:
    for _lib in LOCAL_NVIDIA_CANDIDATES:
        if _lib.exists():
            os.environ["LD_LIBRARY_PATH"] = f"{_lib}:{os.environ.get('LD_LIBRARY_PATH', '')}"
            break

import numpy as np
import pandas as pd
import psutil
import torch
import torch.multiprocessing as mp

try:
    from scipy import stats
except Exception:
    stats = None

ANCHORS = tuple("ABCDEFGH")
LOO_DTAG_MM = 49.621
WORKERS = 6
PHASES = [
    ("A", (1, 0), (5, 1)),
    ("B", (3, 0), (4, 1)),
    ("C", (2, 0), (6, 1)),
    ("D", (7, 0), (8, 1)),
    ("E", (9, 0), (10, 1)),
    ("F", (11, 0), (12, 1)),
    ("G", (13, 0), (14, 1)),
    ("H", (15, 0), (16, 1)),
    ("I", (17, 0), None),
]
ROTO_RHO = ANALYSIS / "FULL_4way_comparison/tables/RotoArm_C_dynamic_rho_per_frame_anchor.csv"
EXTENDED_ITEM20 = ANALYSIS / "FULL_V5_extended_mechanism_ablations/tables/item20_dynamic_residual_vs_motion.csv"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def ensure_dirs() -> None:
    for p in (OUT_ROOT, TABLES, FIGURES, REPORTS, SCRIPTS):
        p.mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(fieldnames or [])
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_report(path: Path, title: str, rows: list[dict[str, Any]], text: str = "") -> None:
    lines = [f"# {title}\n\n", f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n"]
    if text:
        lines.append(text.strip() + "\n\n")
    if rows:
        cols = list(rows[0].keys())
        lines.append("| " + " | ".join(cols) + " |\n")
        lines.append("| " + " | ".join(["---"] * len(cols)) + " |\n")
        for row in rows[:40]:
            vals = []
            for col in cols:
                val = row.get(col, "")
                if isinstance(val, (float, np.floating)):
                    vals.append("nan" if not np.isfinite(val) else f"{float(val):.3f}")
                else:
                    vals.append(str(val))
            lines.append("| " + " | ".join(vals) + " |\n")
        if len(rows) > 40:
            lines.append(f"\n... {len(rows) - 40} additional rows in CSV.\n")
    path.write_text("".join(lines), encoding="utf-8")


def checkpoint(task_id: int) -> None:
    (TABLES / f"checkpoint_task{task_id}_done.txt").write_text(datetime.now().isoformat(timespec="seconds") + "\n", encoding="utf-8")


def finite(arr: Any) -> np.ndarray:
    x = np.asarray(arr, dtype=float)
    return x[np.isfinite(x)]


def rmse(arr: Any) -> float:
    x = finite(arr)
    return float(math.sqrt(np.mean(x * x))) if x.size else float("nan")


def percentile(arr: Any, pct: float) -> float:
    x = finite(arr)
    return float(np.percentile(x, pct)) if x.size else float("nan")


def safe_corr(x: np.ndarray, y: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 3 or np.std(x[m]) <= 1e-12 or np.std(y[m]) <= 1e-12:
        return 0.0
    val = float(np.corrcoef(x[m], y[m])[0, 1])
    return val if np.isfinite(val) else 0.0


def regression_slope(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 3 or np.std(x[m]) <= 1e-12:
        return float("nan"), float("nan")
    a, b = np.polyfit(x[m], y[m], 1)
    pred = a * x[m] + b
    ssr = float(np.sum((y[m] - pred) ** 2))
    sst = float(np.sum((y[m] - np.mean(y[m])) ** 2))
    return float(a * 1000.0), float(1.0 - ssr / sst) if sst > 0 else float("nan")


def maybe_plot(name: str, fn) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fn(plt)
    except Exception:
        (REPORTS / f"{name}_PLOT_SKIPPED.txt").write_text(traceback.format_exc(), encoding="utf-8")


tier1 = load_module(TIER1_SCRIPT, "gpu_tier1_reused_for_full_discovery")
if "580.159" not in _kernel_driver_text:
    _stale = {str(p) for p in LOCAL_NVIDIA_CANDIDATES}
    _ld_parts = [p for p in os.environ.get("LD_LIBRARY_PATH", "").split(":") if p and p not in _stale]
    os.environ["LD_LIBRARY_PATH"] = ":".join(_ld_parts)
tier1.OUT_ROOT = OUT_ROOT
tier1.TABLES = TABLES
tier1.FIGURES = FIGURES
tier1.REPORTS = REPORTS
tier1.SCRIPTS = SCRIPTS


class ResourceMonitor:
    def __init__(self, gpu_id: int, interval_s: float = 1.0):
        self.gpu_id = int(gpu_id)
        self.interval_s = float(interval_s)
        self.cpu: list[float] = []
        self.gpu: list[float] = []
        self.mem: list[float] = []
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        psutil.cpu_percent(interval=None)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        self.thread.join(timeout=2.0)

    def _run(self):
        while not self._stop.is_set():
            self.cpu.append(float(psutil.cpu_percent(interval=None)))
            try:
                out = subprocess.check_output(
                    [
                        "nvidia-smi",
                        f"--id={self.gpu_id}",
                        "--query-gpu=utilization.gpu,memory.used",
                        "--format=csv,noheader,nounits",
                    ],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=2.0,
                    env=os.environ.copy(),
                ).strip()
                if out:
                    util, mem = [p.strip() for p in out.split(",")[:2]]
                    self.gpu.append(float(util))
                    self.mem.append(float(mem))
            except Exception:
                pass
            self._stop.wait(self.interval_s)

    def summary(self) -> dict[str, float]:
        return {
            "mean_cpu_percent": float(np.mean(self.cpu)) if self.cpu else float("nan"),
            "max_cpu_percent": float(np.max(self.cpu)) if self.cpu else float("nan"),
            "mean_gpu_percent": float(np.mean(self.gpu)) if self.gpu else float("nan"),
            "max_gpu_percent": float(np.max(self.gpu)) if self.gpu else float("nan"),
            "peak_vram_mb": float(np.max(self.mem)) if self.mem else float("nan"),
        }


@dataclass(frozen=True)
class Config:
    name: str
    coords: np.ndarray
    delays: np.ndarray


def load_data():
    return tier1.load_shared()


def configs(data) -> dict[str, Config]:
    return {
        "V4_CV4": Config("V4_CV4", np.asarray(data.v4_coords, dtype=np.float32), np.asarray(data.v4_delays, dtype=np.float32)),
        "V5_CV5": Config("V5_CV5", np.asarray(data.v5_coords, dtype=np.float32), np.asarray(data.v5_delays, dtype=np.float32)),
        "Vicon_Ccm": Config("Vicon_Ccm", np.asarray(data.vicon_coords, dtype=np.float32), np.asarray(data.vicon_delays, dtype=np.float32)),
    }


def eval_config(cfg: Config, data, dtag: float, device: str, ranges_adjust: np.ndarray | None = None, weights: np.ndarray | None = None) -> dict[str, Any]:
    return tier1.tensor_eval(cfg.coords, cfg.delays, data.ranges, data.truth, float(dtag), device, ranges_adjust=ranges_adjust, weights=weights)


def feature_frame(data, cfg: Config, dtag: float = LOO_DTAG_MM) -> pd.DataFrame:
    df = pd.DataFrame(data.raw_features).copy()
    if df.empty:
        raise RuntimeError("raw static range features are unavailable")
    pos_idx = {sid: i for i, sid in enumerate(data.ids)}
    rows = []
    for _, r in df.iterrows():
        sid = str(r["position_id"])
        aid = int(r["anchor_id"])
        p = np.asarray(data.truth[pos_idx[sid]], dtype=float)
        a = np.asarray(cfg.coords[aid], dtype=float)
        diff = a - p
        horiz = math.hypot(float(diff[0]), float(diff[2]))
        geom = float(np.linalg.norm(diff))
        elev = math.degrees(math.atan2(float(diff[1]), horiz))
        az = math.degrees(math.atan2(float(diff[2]), float(diff[0])))
        target = float(r["range_median"]) - geom
        rho = target - float(cfg.delays[aid]) - float(dtag)
        row = r.to_dict()
        row.update(
            {
                "position_index": pos_idx[sid],
                "geometric_distance_cfg": geom,
                "elevation_angle_cfg": elev,
                "azimuth_angle_cfg": az,
                "target_correction_mm": target,
                "rho_mm": rho,
                "base_correction_mm": float(cfg.delays[aid]) + float(dtag),
                "height_mm": float(p[1]),
                "x_mm": float(p[0]),
                "y_mm": float(p[1]),
                "z_mm": float(p[2]),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def feature_columns(df: pd.DataFrame) -> list[str]:
    cols = [
        "elevation_angle_cfg",
        "azimuth_angle_cfg",
        "geometric_distance_cfg",
        "range_std",
        "range_iqr",
        "range_skewness",
        "range_kurtosis",
        "n_samples",
    ]
    for a in range(8):
        df[f"anchor_{a}"] = (df["anchor_id"].astype(int) == a).astype(float)
        cols.append(f"anchor_{a}")
    df["sin_az"] = np.sin(np.deg2rad(df["azimuth_angle_cfg"].to_numpy(float)))
    df["cos_az"] = np.cos(np.deg2rad(df["azimuth_angle_cfg"].to_numpy(float)))
    cols.extend(["sin_az", "cos_az"])
    for col in cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    med = df[cols].median(numeric_only=True)
    df[cols] = df[cols].replace([np.inf, -np.inf], np.nan).fillna(med).fillna(0.0)
    return cols


def standardize(xtr: np.ndarray, xte: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    mu = np.nanmean(xtr, axis=0)
    sd = np.nanstd(xtr, axis=0)
    sd[sd < 1e-9] = 1.0
    return (xtr - mu) / sd, (xte - mu) / sd, mu, sd


def fit_linear(x: np.ndarray, y: np.ndarray, ridge: float = 1e-3) -> np.ndarray:
    xx = np.c_[np.ones(len(x)), x]
    reg = ridge * np.eye(xx.shape[1])
    reg[0, 0] = 0.0
    return np.linalg.solve(xx.T @ xx + reg, xx.T @ y)


def predict_linear(beta: np.ndarray, x: np.ndarray) -> np.ndarray:
    return np.c_[np.ones(len(x)), x] @ beta


def train_mlp_regressor(xtr: np.ndarray, ytr: np.ndarray, xte: np.ndarray, device: str, epochs: int = 350) -> tuple[np.ndarray, torch.nn.Module, np.ndarray, np.ndarray]:
    xtr_s, xte_s, mu, sd = standardize(xtr, xte)
    ym = float(np.mean(ytr))
    ys = float(np.std(ytr)) or 1.0
    xt = torch.as_tensor(xtr_s, dtype=torch.float32, device=device)
    yt = torch.as_tensor((ytr - ym) / ys, dtype=torch.float32, device=device).view(-1, 1)
    model = torch.nn.Sequential(
        torch.nn.Linear(xtr.shape[1], 32),
        torch.nn.ReLU(),
        torch.nn.Linear(32, 32),
        torch.nn.ReLU(),
        torch.nn.Linear(32, 1),
    ).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=0.01, weight_decay=1e-3)
    for _ in range(epochs):
        opt.zero_grad(set_to_none=True)
        loss = torch.nn.functional.mse_loss(model(xt), yt)
        loss.backward()
        opt.step()
    with torch.no_grad():
        pred = model(torch.as_tensor(xte_s, dtype=torch.float32, device=device)).squeeze(1).cpu().numpy() * ys + ym
    model._feature_mu = torch.as_tensor(mu, dtype=torch.float32, device=device)  # type: ignore[attr-defined]
    model._feature_sd = torch.as_tensor(sd, dtype=torch.float32, device=device)  # type: ignore[attr-defined]
    model._target_mean = ym  # type: ignore[attr-defined]
    model._target_std = ys  # type: ignore[attr-defined]
    return pred, model, mu, sd


def predictions_to_adjust_matrix(data, df: pd.DataFrame, pred_corr: np.ndarray, cfg: Config, dtag: float) -> np.ndarray:
    mat = np.zeros_like(data.ranges, dtype=np.float32)
    for idx, (_, r) in enumerate(df.iterrows()):
        i = int(r["position_index"])
        aid = int(r["anchor_id"])
        mat[i, aid] = float(pred_corr[idx] - (cfg.delays[aid] + dtag))
    return mat


def solve_cv_predictions(data, df: pd.DataFrame, pred_corr: np.ndarray, cfg: Config, dtag: float, device: str) -> dict[str, Any]:
    adjust = predictions_to_adjust_matrix(data, df, pred_corr, cfg, dtag)
    return eval_config(cfg, data, dtag, device, ranges_adjust=adjust)


def task1(device: str) -> dict[str, Any]:
    return tier1.task1_multiroom(device)


def task2(device: str) -> dict[str, Any]:
    return tier1.task2_fisher(device)


def task3(device: str) -> dict[str, Any]:
    return tier1.task3_shapley(device)


def task4(device: str) -> dict[str, Any]:
    return tier1.task4_aa_at_asymmetry(device)


def task5(device: str) -> dict[str, Any]:
    return tier1.task5_solver_search(device)


def task6(device: str) -> dict[str, Any]:
    return tier1.task6_nlos_detector(device)


def task7_learned_correction(device: str) -> dict[str, Any]:
    data = load_data()
    cfg = configs(data)["V5_CV5"]
    df = feature_frame(data, cfg)
    feats = feature_columns(df)
    x = df[feats].to_numpy(float)
    y = df["target_correction_mm"].to_numpy(float)
    groups = df["position_id"].to_numpy(str)
    pred_by_model = {m: np.zeros(len(df), dtype=float) for m in ["scalar", "per_anchor_scalar", "linear", "mlp"]}
    for held in sorted(np.unique(groups)):
        tr = groups != held
        te = groups == held
        pred_by_model["scalar"][te] = float(np.median(y[tr]))
        med_anchor = df.loc[tr].groupby("anchor_id")["target_correction_mm"].median().to_dict()
        pred_by_model["per_anchor_scalar"][te] = [float(med_anchor.get(int(a), np.median(y[tr]))) for a in df.loc[te, "anchor_id"]]
        xtr, xte, _mu, _sd = standardize(x[tr], x[te])
        beta = fit_linear(xtr, y[tr])
        pred_by_model["linear"][te] = predict_linear(beta, xte)
        pred, _model, _mu2, _sd2 = train_mlp_regressor(x[tr], y[tr], x[te], device, epochs=280)
        pred_by_model["mlp"][te] = pred
    rows = []
    for model, pred in pred_by_model.items():
        res = solve_cv_predictions(data, df, pred, cfg, LOO_DTAG_MM, device)
        rows.append(
            {
                "model": model,
                "cv_median_3d_mm": res["median_3d"],
                "cv_rmse_3d_mm": res["rmse_3d"],
                "cv_range_rmse_mm": rmse(y - pred),
            }
        )
    xtr_s, _xte_s, mu, sd = standardize(x, x)
    full_pred, full_model, _mu, _sd = train_mlp_regressor(x, y, x, device, epochs=450)
    grad_rows = []
    xt = torch.as_tensor(xtr_s, dtype=torch.float32, device=device).requires_grad_(True)
    out = full_model(xt).sum()
    out.backward()
    grad = xt.grad.detach().abs().mean(dim=0).cpu().numpy()
    base_rmse = rmse(y - full_pred)
    rng = np.random.default_rng(7)
    for i, feat in enumerate(feats):
        xp = x.copy()
        rng.shuffle(xp[:, i])
        xp_s = (xp - mu) / sd
        with torch.no_grad():
            pp = full_model(torch.as_tensor(xp_s, dtype=torch.float32, device=device)).squeeze(1).cpu().numpy()
        pp = pp * float(full_model._target_std) + float(full_model._target_mean)  # type: ignore[attr-defined]
        grad_rows.append({"feature": feat, "gradient_magnitude": float(grad[i]), "permutation_importance": float(rmse(y - pp) - base_rmse)})
    neg_rows = []
    mlp_med = next(r["cv_median_3d_mm"] for r in rows if r["model"] == "mlp")
    scalar_med = next(r["cv_median_3d_mm"] for r in rows if r["model"] == "scalar")
    shuffled_meds = []
    for seed in range(40):
        rng = np.random.default_rng(seed)
        ysh = y.copy()
        rng.shuffle(ysh)
        pred_sh = np.zeros(len(df), dtype=float)
        for held in sorted(np.unique(groups)):
            tr = groups != held
            te = groups == held
            pred, _model, _m, _s = train_mlp_regressor(x[tr], ysh[tr], x[te], device, epochs=120)
            pred_sh[te] = pred
        res = solve_cv_predictions(data, df, pred_sh, cfg, LOO_DTAG_MM, device)
        shuffled_meds.append(res["median_3d"])
    p_val = float((1 + np.sum(np.asarray(shuffled_meds) <= mlp_med)) / (1 + len(shuffled_meds)))
    neg_rows.append({"model": "mlp_shuffled_targets", "shuffled_cv_median_3d": float(np.mean(shuffled_meds)), "p_value": p_val})
    write_csv(TABLES / "task7_model_comparison.csv", rows)
    write_csv(TABLES / "task7_feature_importance.csv", sorted(grad_rows, key=lambda r: r["permutation_importance"], reverse=True))
    write_csv(TABLES / "task7_negative_control.csv", neg_rows)
    append_report(
        REPORTS / "TASK7_LEARNED_CORRECTION.md",
        "Task 7 - Learned Range Correction",
        rows + neg_rows,
        f"Scalar baseline median={scalar_med:.1f} mm; MLP median={mlp_med:.1f} mm.",
    )
    return {"key_finding": f"MLP median {mlp_med:.1f} mm vs scalar {scalar_med:.1f} mm", "rows": len(rows)}


def task8_landscape(device: str) -> dict[str, Any]:
    data = load_data()
    cfg = configs(data)["V5_CV5"]
    scales = np.round(np.arange(0.93, 1.0701, 0.005), 3)
    shifts = np.arange(-60.0, 60.1, 10.0)
    dtags = np.arange(0.0, 140.1, 5.0)
    ctr = cfg.coords.mean(axis=0)
    rows = []
    for s in scales:
        coords = ctr + (cfg.coords - ctr) * float(s)
        for dc in shifts:
            delays = cfg.delays + float(dc)
            for dtag in dtags:
                res = tier1.tensor_eval(coords, delays, data.ranges, data.truth, float(dtag), device)
                slope, _r2 = regression_slope(data.truth[:, 1], res["positions"][:, 1] - data.truth[:, 1])
                rows.append(
                    {
                        "s": float(s),
                        "delta_c_mm": float(dc),
                        "d_tag_mm": float(dtag),
                        "median_3d_mm": res["median_3d"],
                        "rmse_mm": res["rmse_3d"],
                        "vert_slope": slope,
                    }
                )
    df = pd.DataFrame(rows)
    write_csv(TABLES / "task8_3d_landscape.csv", rows)
    slice_rows = []
    for fixed_var, mask, var1, var2 in [
        ("delta_c_mm", np.isclose(df["delta_c_mm"], 0.0), "s", "d_tag_mm"),
        ("s", np.isclose(df["s"], 1.0), "delta_c_mm", "d_tag_mm"),
        ("d_tag_mm", np.isclose(df["d_tag_mm"], 50.0), "s", "delta_c_mm"),
    ]:
        for _, r in df[mask].iterrows():
            slice_rows.append({"fixed_var": fixed_var, "fixed_value": float(r[fixed_var]), "var1": float(r[var1]), "var2": float(r[var2]), "median_3d": float(r["median_3d_mm"])})
    ridge = []
    for (s, dc), g in df.groupby(["s", "delta_c_mm"]):
        r = g.sort_values("median_3d_mm").iloc[0]
        ridge.append({"s": float(s), "delta_c_mm": float(dc), "best_d_tag_mm": float(r["d_tag_mm"]), "min_median_3d_mm": float(r["median_3d_mm"])})
    write_csv(TABLES / "task8_2d_slices.csv", slice_rows)
    write_csv(TABLES / "task8_valley_ridge.csv", ridge)
    best = df.sort_values("median_3d_mm").iloc[0].to_dict()

    def heat(fig_name: str, fixed_mask, xcol: str, ycol: str):
        def _plot(plt):
            sub = df[fixed_mask]
            piv = sub.pivot_table(index=ycol, columns=xcol, values="median_3d_mm", aggfunc="min")
            fig, ax = plt.subplots(figsize=(7, 5))
            im = ax.imshow(piv.to_numpy(), origin="lower", aspect="auto", extent=[piv.columns.min(), piv.columns.max(), piv.index.min(), piv.index.max()])
            ax.set_xlabel(xcol)
            ax.set_ylabel(ycol)
            fig.colorbar(im, ax=ax, label="median 3D (mm)")
            fig.tight_layout()
            fig.savefig(FIGURES / fig_name, dpi=150)
            plt.close(fig)
        maybe_plot(fig_name, _plot)

    heat("task8_heatmap_scale_dtag.png", np.isclose(df["delta_c_mm"], 0.0), "s", "d_tag_mm")
    heat("task8_heatmap_c_dtag.png", np.isclose(df["s"], 1.0), "delta_c_mm", "d_tag_mm")
    heat("task8_heatmap_scale_c.png", np.isclose(df["d_tag_mm"], 50.0), "s", "delta_c_mm")
    append_report(REPORTS / "TASK8_LANDSCAPE.md", "Task 8 - Global Loss Landscape", [best] + ridge[:12])
    return {"key_finding": f"min {best['median_3d_mm']:.1f} mm at s={best['s']:.3f}, dc={best['delta_c_mm']:.0f}, D={best['d_tag_mm']:.0f}", "rows": len(rows)}


def task9_layout_optimization(device: str) -> dict[str, Any]:
    data = load_data()
    cfg = configs(data)["V5_CV5"]
    ranges_t = torch.as_tensor(data.ranges, dtype=torch.float32, device=device)
    truth_t = torch.as_tensor(data.truth, dtype=torch.float32, device=device)
    base_coords = torch.as_tensor(cfg.coords, dtype=torch.float32, device=device)
    base_delays = torch.as_tensor(cfg.delays, dtype=torch.float32, device=device)
    lo = torch.as_tensor(np.nanmin(np.r_[data.truth, cfg.coords], axis=0) - 1000.0, dtype=torch.float32, device=device)
    hi = torch.as_tensor(np.nanmax(np.r_[data.truth, cfg.coords], axis=0) + 1000.0, dtype=torch.float32, device=device)
    restart_rows = []
    best_state = None
    rng = np.random.default_rng(9)
    for restart in range(10):
        delta = torch.nn.Parameter(torch.as_tensor(rng.normal(0, 40, cfg.coords.shape), dtype=torch.float32, device=device))
        ddelta = torch.nn.Parameter(torch.as_tensor(rng.normal(0, 10, cfg.delays.shape), dtype=torch.float32, device=device))
        dtag_var = torch.nn.Parameter(torch.tensor(float(LOO_DTAG_MM + rng.normal(0, 10)), dtype=torch.float32, device=device))
        opt = torch.optim.Adam([delta, ddelta, dtag_var], lr=0.8)
        for step in range(500):
            opt.zero_grad(set_to_none=True)
            coords = torch.minimum(torch.maximum(base_coords + delta, lo), hi)
            delays = torch.nn.functional.softplus(base_delays + ddelta)
            solver = tier1.DifferentiablePositionSolver(coords, delays, max_iter=10).to(device)
            pred, _resid, _conv = solver(ranges_t, dtag_var)
            err = torch.linalg.norm(pred - truth_t, dim=1)
            tau = 15.0
            soft_med = tau * torch.logsumexp(err / tau, dim=0) - tau * math.log(err.numel())
            reg = 1e-4 * torch.mean(delta * delta) + 1e-4 * torch.mean(ddelta * ddelta)
            loss = soft_med + reg
            loss.backward()
            opt.step()
        coords_np = torch.minimum(torch.maximum(base_coords + delta, lo), hi).detach().cpu().numpy()
        delays_np = torch.nn.functional.softplus(base_delays + ddelta).detach().cpu().numpy()
        dtag = float(dtag_var.detach().cpu())
        res = tier1.tensor_eval(coords_np, delays_np, data.ranges, data.truth, dtag, device)
        disp = np.linalg.norm(coords_np - cfg.coords, axis=1)
        ctr0 = cfg.coords.mean(axis=0)
        ctr1 = coords_np.mean(axis=0)
        scale = float(np.linalg.norm(coords_np - ctr1) / max(np.linalg.norm(cfg.coords - ctr0), 1e-9))
        row = {"restart_id": restart, "median_3d": res["median_3d"], "rmse": res["rmse_3d"], "d_tag": dtag, "scale": scale, "per_anchor_displacement": float(np.mean(disp)), "max_anchor_displacement": float(np.max(disp))}
        restart_rows.append(row)
        if best_state is None or row["median_3d"] < best_state[0]["median_3d"]:
            best_state = (row, coords_np, delays_np)
    assert best_state is not None
    best_row, best_coords, best_delays = best_state
    compare = []
    for aid, lab in enumerate(ANCHORS):
        compare.append(
            {
                "anchor_label": lab,
                "v5_x": float(cfg.coords[aid, 0]),
                "v5_y": float(cfg.coords[aid, 1]),
                "v5_z": float(cfg.coords[aid, 2]),
                "opt_x": float(best_coords[aid, 0]),
                "opt_y": float(best_coords[aid, 1]),
                "opt_z": float(best_coords[aid, 2]),
                "opt_delay": float(best_delays[aid]),
                "displacement_mm": float(np.linalg.norm(best_coords[aid] - cfg.coords[aid])),
            }
        )
    write_csv(TABLES / "task9_optimized_layouts.csv", restart_rows)
    write_csv(TABLES / "task9_v5_vs_optimized.csv", compare)
    append_report(REPORTS / "TASK9_LAYOUT_OPTIMIZATION.md", "Task 9 - Continuous Layout Optimization", [best_row] + compare)
    return {"key_finding": f"best optimized median {best_row['median_3d']:.1f} mm, mean move {best_row['per_anchor_displacement']:.1f} mm", "rows": len(restart_rows)}


def residual_model_predictions(df: pd.DataFrame, features: list[str], model: str, device: str) -> np.ndarray:
    y = df["rho_mm"].to_numpy(float)
    groups = df["position_id"].to_numpy(str)
    x = df[features].to_numpy(float)
    pred = np.zeros(len(df), dtype=float)
    for held in sorted(np.unique(groups)):
        tr = groups != held
        te = groups == held
        if model == "M0_global":
            pred[te] = float(np.mean(y[tr]))
        elif model == "M1_anchor":
            means = df.loc[tr].groupby("anchor_id")["rho_mm"].mean().to_dict()
            pred[te] = [float(means.get(int(a), np.mean(y[tr]))) for a in df.loc[te, "anchor_id"]]
        elif model in ("M2_antenna", "M3_spatial"):
            use_cols = ["elevation_angle_cfg", "azimuth_angle_cfg", "sin_az", "cos_az"] if model == "M2_antenna" else ["x_mm", "y_mm", "z_mm", "height_mm", "geometric_distance_cfg"]
            xx = df[use_cols].replace([np.inf, -np.inf], np.nan).fillna(df[use_cols].median()).to_numpy(float)
            xtr, xte, _mu, _sd = standardize(xx[tr], xx[te])
            beta = fit_linear(xtr, y[tr])
            pred[te] = predict_linear(beta, xte)
        else:
            p, _mdl, _mu, _sd = train_mlp_regressor(x[tr], y[tr], x[te], device, epochs=180)
            pred[te] = p
    return pred


def task10_residual_decomposition(device: str) -> dict[str, Any]:
    data = load_data()
    cfg = configs(data)["V5_CV5"]
    df = feature_frame(data, cfg)
    feats = feature_columns(df)
    models = [
        ("M0_global", 1),
        ("M1_anchor", 9),
        ("M2_antenna", 15),
        ("M3_spatial", 19),
        ("M4_full_mlp", 35),
    ]
    rows = []
    preds: dict[str, np.ndarray] = {}
    for name, n_params in models:
        pred = residual_model_predictions(df, feats, name, device)
        preds[name] = pred
        adjust = np.zeros_like(data.ranges, dtype=np.float32)
        for idx, (_, r) in enumerate(df.iterrows()):
            adjust[int(r["position_index"]), int(r["anchor_id"])] = float(pred[idx])
        res = eval_config(cfg, data, LOO_DTAG_MM, device, ranges_adjust=adjust)
        rows.append({"model": name, "n_params": n_params, "cv_range_rmse": rmse(df["rho_mm"].to_numpy(float) - pred), "cv_position_median_3d": res["median_3d"]})
    # Antenna pattern from the antenna model's linear approximation on a grid.
    ant_rows = []
    for elev in np.linspace(-45, 45, 19):
        for az in np.linspace(-180, 180, 25):
            mask = np.ones(len(df), dtype=bool)
            target = preds["M2_antenna"]
            dist = (df["elevation_angle_cfg"].to_numpy(float) - elev) ** 2 + 0.05 * (df["azimuth_angle_cfg"].to_numpy(float) - az) ** 2
            idx = np.argsort(dist)[:16]
            ant_rows.append({"elevation_deg": float(elev), "azimuth_deg": float(az), "predicted_correction_mm": float(np.mean(target[idx]))})
    y = df["rho_mm"].to_numpy(float)
    sst = float(np.var(y)) or 1.0
    var_rows = []
    prev = np.zeros_like(y)
    for name, _n in models:
        ss = float(max(0.0, np.var(preds[name]) - np.var(prev)))
        var_rows.append({"component": name, "fraction_explained": ss / sst})
        prev = preds[name]
    residual_frac = max(0.0, 1.0 - sum(r["fraction_explained"] for r in var_rows))
    var_rows.append({"component": "residual", "fraction_explained": residual_frac})
    write_csv(TABLES / "task10_nested_models.csv", rows)
    write_csv(TABLES / "task10_antenna_pattern.csv", ant_rows)
    write_csv(TABLES / "task10_variance_attribution.csv", var_rows)
    append_report(REPORTS / "TASK10_RESIDUAL_DECOMPOSITION.md", "Task 10 - Neural Residual Field Decomposition", rows + var_rows)
    best = min(rows, key=lambda r: r["cv_position_median_3d"])
    return {"key_finding": f"{best['model']} median {best['cv_position_median_3d']:.1f} mm", "rows": len(rows)}


def gaussian_ll(x: np.ndarray, sigma: np.ndarray | float, mu: np.ndarray | float = 0.0) -> float:
    s = np.maximum(np.asarray(sigma, dtype=float), 1e-6)
    r = np.asarray(x, dtype=float) - np.asarray(mu, dtype=float)
    return float(np.sum(-0.5 * np.log(2 * np.pi * s * s) - 0.5 * (r / s) ** 2))


def task11_model_evidence(device: str) -> dict[str, Any]:
    _ = device
    data = load_data()
    cfg = configs(data)["V5_CV5"]
    df = feature_frame(data, cfg)
    rho = df["rho_mm"].to_numpy(float)
    aid = df["anchor_id"].to_numpy(int)
    elev = df["elevation_angle_cfg"].to_numpy(float)
    rows = []
    sigma = np.std(rho) or 1.0
    models = []
    models.append(("M0_global_gaussian", 1, gaussian_ll(rho, sigma, 0.0)))
    sig_j = np.array([np.std(rho[aid == j]) or sigma for j in range(8)])
    models.append(("M1_per_anchor_gaussian", 8, gaussian_ll(rho, sig_j[aid], 0.0)))
    if stats is not None:
        nu_grid = np.arange(2.5, 20.5, 0.5)
        best = max((float(np.sum(stats.t.logpdf(rho / sigma, df=nu) - np.log(sigma))), nu) for nu in nu_grid)
        models.append(("M2_student_t", 2, best[0]))
    else:
        models.append(("M2_student_t", 2, gaussian_ll(rho, sigma * 1.4, 0.0)))
    pos = np.clip(rho, 0, None)
    pi = float(np.mean(rho > 80.0))
    lam = 1.0 / max(float(np.mean(pos[pos > 0])) if np.any(pos > 0) else 80.0, 1.0)
    ga = np.exp(-0.5 * (rho / sigma) ** 2) / (sigma * math.sqrt(2 * math.pi))
    ex = np.where(rho >= 0, lam * np.exp(-lam * rho), 1e-12)
    ll_mix = float(np.sum(np.log(np.maximum((1 - pi) * ga + pi * ex, 1e-300))))
    models.append(("M3_gaussian_exponential_tail", 3, ll_mix))
    ll4 = 0.0
    for j in range(8):
        rj = rho[aid == j]
        sj = np.std(rj) or sigma
        pj = float(np.mean(rj > 80.0))
        posj = np.clip(rj, 0, None)
        lj = 1.0 / max(float(np.mean(posj[posj > 0])) if np.any(posj > 0) else 80.0, 1.0)
        ga = np.exp(-0.5 * (rj / sj) ** 2) / (sj * math.sqrt(2 * math.pi))
        ex = np.where(rj >= 0, lj * np.exp(-lj * rj), 1e-12)
        ll4 += float(np.sum(np.log(np.maximum((1 - pj) * ga + pj * ex, 1e-300))))
    models.append(("M4_per_anchor_mixture", 24, ll4))
    beta = fit_linear(elev[:, None], (rho > 80).astype(float), ridge=1e-2)
    p_e = np.clip(predict_linear(beta, elev[:, None]), 0.01, 0.8)
    ll5 = float(np.sum(np.log(np.maximum((1 - p_e) * ga[: len(rho)] + p_e * ex[: len(rho)], 1e-300)))) if len(ga) == len(rho) else ll_mix
    models.append(("M5_elevation_tail_mixture", 5, ll5))
    n = len(rho)
    for name, k, ll in models:
        rows.append({"model": name, "n_params": k, "log_likelihood": ll, "aic": float(2 * k - 2 * ll), "bic": float(k * math.log(n) - 2 * ll), "loo_score": float(ll - k)})
    pred_rows = []
    rng = np.random.default_rng(11)
    for name, k, ll in models:
        if "mixture" in name:
            sim = rng.normal(0, sigma, 20000) + (rng.random(20000) < pi) * rng.exponential(1.0 / lam, 20000)
        elif "student" in name and stats is not None:
            sim = stats.t.rvs(df=4, scale=sigma, size=20000, random_state=rng)
        else:
            sim = rng.normal(0, sigma, 20000)
        pred_rows.append({"model": name, "p50_rho": percentile(sim, 50), "p90_rho": percentile(sim, 90), "p99_rho": percentile(sim, 99), "pct_gt100": float(np.mean(sim > 100.0))})
    write_csv(TABLES / "task11_model_evidence.csv", rows)
    write_csv(TABLES / "task11_posterior_predictive.csv", pred_rows)
    best = min(rows, key=lambda r: r["bic"])
    append_report(REPORTS / "TASK11_MODEL_EVIDENCE.md", "Task 11 - Bayesian Model Evidence Tournament", rows + pred_rows)
    return {"key_finding": f"BIC winner {best['model']}", "rows": len(rows)}


def fim_covariance(pos: np.ndarray, anchors: np.ndarray, sigma_mm: float = 35.0) -> np.ndarray:
    diff = pos[None, :] - anchors
    u = diff / np.linalg.norm(diff, axis=1, keepdims=True).clip(min=1e-6)
    h = u.T @ u / (sigma_mm * sigma_mm)
    return np.linalg.pinv(h + np.eye(3) * 1e-9)


def task12_bayesian_solver(device: str) -> dict[str, Any]:
    data = load_data()
    cfgs = configs(data)
    rows = []
    calib = []
    for label in ["V4_CV4", "V5_CV5"]:
        cfg = cfgs[label]
        res = eval_config(cfg, data, LOO_DTAG_MM, device)
        pred = res["positions"]
        maha = []
        volumes = []
        for i, sid in enumerate(data.ids):
            cov = fim_covariance(pred[i], cfg.coords)
            eig = np.linalg.eigvalsh(cov).clip(min=1e-9)
            err = pred[i] - data.truth[i]
            m2 = float(err @ np.linalg.pinv(cov) @ err)
            maha.append(m2)
            volumes.append(float(4.0 / 3.0 * math.pi * np.prod(np.sqrt(eig * 7.815))))
            rows.append({"config": label, "position_id": sid, "mean_x": float(pred[i, 0]), "mean_y": float(pred[i, 1]), "mean_z": float(pred[i, 2]), "err_3d_mm": float(np.linalg.norm(err)), "credible_volume_95_mm3": volumes[-1], "mahalanobis2": m2})
        for level, q in [(0.50, 2.366), (0.80, 4.642), (0.95, 7.815)]:
            calib.append({"config": label, "credible_level": level, "empirical_coverage": float(np.mean(np.asarray(maha) <= q)), "median_credible_volume": float(np.median(volumes))})
    write_csv(TABLES / "task12_posterior_summary.csv", rows)
    write_csv(TABLES / "task12_calibration.csv", calib)
    def _plot(plt):
        df = pd.DataFrame(rows)
        fig, ax = plt.subplots(figsize=(6, 4))
        for label, g in df.groupby("config"):
            ax.scatter(g["credible_volume_95_mm3"], g["err_3d_mm"], label=label)
        ax.set_xscale("log")
        ax.set_xlabel("95% credible volume (mm^3)")
        ax.set_ylabel("3D error (mm)")
        ax.legend()
        fig.tight_layout()
        fig.savefig(FIGURES / "task12_credible_ellipsoids.png", dpi=150)
        plt.close(fig)
    maybe_plot("task12_credible_ellipsoids", _plot)
    append_report(REPORTS / "TASK12_BAYESIAN_SOLVER.md", "Task 12 - Bayesian Position Solver", calib)
    v5_cov = next(r for r in calib if r["config"] == "V5_CV5" and abs(r["credible_level"] - 0.95) < 1e-9)
    return {"key_finding": f"V5 95pct coverage {v5_cov['empirical_coverage']:.2f}", "rows": len(rows)}


class AttentionResidual(torch.nn.Module):
    def __init__(self, n_features: int):
        super().__init__()
        self.score = torch.nn.Sequential(torch.nn.Linear(n_features, 32), torch.nn.ReLU(), torch.nn.Linear(32, 1))
        self.value = torch.nn.Sequential(torch.nn.Linear(n_features, 32), torch.nn.ReLU(), torch.nn.Linear(32, 1))

    def forward(self, x: torch.Tensor, pos_index: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        raw_score = self.score(x).squeeze(1)
        val = self.value(x).squeeze(1)
        att = torch.zeros_like(raw_score)
        for p in torch.unique(pos_index):
            m = pos_index == p
            att[m] = torch.softmax(raw_score[m], dim=0)
        return val, att


def task13_gnn_attention(device: str) -> dict[str, Any]:
    data = load_data()
    cfg = configs(data)["V5_CV5"]
    df = feature_frame(data, cfg)
    feats = feature_columns(df)
    x = df[feats].to_numpy(float)
    y = df["rho_mm"].to_numpy(float)
    groups = df["position_id"].to_numpy(str)
    pos_index = df["position_index"].to_numpy(int)
    pred = np.zeros(len(df), dtype=float)
    att_all = np.zeros(len(df), dtype=float)
    for held in sorted(np.unique(groups)):
        tr = groups != held
        te = groups == held
        xtr, xte, mu, sd = standardize(x[tr], x[te])
        ym = float(np.mean(y[tr]))
        ys = float(np.std(y[tr])) or 1.0
        model = AttentionResidual(x.shape[1]).to(device)
        opt = torch.optim.Adam(model.parameters(), lr=0.01, weight_decay=1e-3)
        xt = torch.as_tensor(xtr, dtype=torch.float32, device=device)
        yt = torch.as_tensor((y[tr] - ym) / ys, dtype=torch.float32, device=device)
        pt = torch.as_tensor(pos_index[tr], dtype=torch.long, device=device)
        for _ in range(260):
            opt.zero_grad(set_to_none=True)
            val, att = model(xt, pt)
            loss = torch.mean((val - yt) ** 2) + 0.01 * torch.mean(att * torch.log(att.clamp_min(1e-6)))
            loss.backward()
            opt.step()
        with torch.no_grad():
            val, att = model(torch.as_tensor(xte, dtype=torch.float32, device=device), torch.as_tensor(pos_index[te], dtype=torch.long, device=device))
        pred[te] = val.cpu().numpy() * ys + ym
        att_all[te] = att.cpu().numpy()
    adjust = np.zeros_like(data.ranges, dtype=np.float32)
    for idx, (_, r) in enumerate(df.iterrows()):
        adjust[int(r["position_index"]), int(r["anchor_id"])] = float(pred[idx])
    res = eval_config(cfg, data, LOO_DTAG_MM, device, ranges_adjust=adjust)
    cv_rows = [{"model": "attention_residual", "cv_range_rmse": rmse(y - pred), "cv_position_median_3d": res["median_3d"], "cv_rmse_3d": res["rmse_3d"]}]
    att_rows = []
    df["attention"] = att_all
    for aid, g in df.groupby("anchor_id"):
        att_rows.append({"anchor_label": ANCHORS[int(aid)], "mean_attention": float(g["attention"].mean()), "rho_rms": rmse(g["rho_mm"])})
    ablation = []
    for aid in range(8):
        weights = np.ones(8, dtype=np.float32)
        weights[aid] = 0.1
        r = eval_config(cfg, data, LOO_DTAG_MM, device, weights=weights)
        ablation.append({"ablation": f"downweight_{ANCHORS[aid]}", "median_3d": r["median_3d"], "rmse_3d": r["rmse_3d"]})
    write_csv(TABLES / "task13_gnn_cv_results.csv", cv_rows)
    write_csv(TABLES / "task13_attention_weights.csv", att_rows)
    write_csv(TABLES / "task13_ablation.csv", ablation)
    def _plot(plt):
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.bar([r["anchor_label"] for r in att_rows], [r["mean_attention"] for r in att_rows])
        ax.set_ylabel("Mean learned attention")
        fig.tight_layout()
        fig.savefig(FIGURES / "task13_attention_graph.png", dpi=150)
        plt.close(fig)
    maybe_plot("task13_attention_graph", _plot)
    append_report(REPORTS / "TASK13_GNN_ATTENTION.md", "Task 13 - GNN Attention Residual Model", cv_rows + att_rows)
    return {"key_finding": f"attention residual median {res['median_3d']:.1f} mm", "rows": len(att_rows)}


def dop_at(points: np.ndarray, anchors: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    vals = []
    vds = []
    conds = []
    for p in points:
        diff = p[None, :] - anchors
        u = diff / np.linalg.norm(diff, axis=1, keepdims=True).clip(min=1e-6)
        q = np.linalg.pinv(u.T @ u + np.eye(3) * 1e-9)
        vals.append(float(math.sqrt(max(np.trace(q), 0.0))))
        vds.append(float(math.sqrt(max(q[1, 1], 0.0))))
        conds.append(float(np.linalg.cond(u.T @ u + np.eye(3) * 1e-9)))
    return np.asarray(vals), np.asarray(vds), np.asarray(conds)


def candidate_grid(data, step_mm: float = 250.0) -> np.ndarray:
    lo = np.nanmin(np.r_[data.truth, data.v5_coords], axis=0) + np.array([150, 150, 150])
    hi = np.nanmax(np.r_[data.truth, data.v5_coords], axis=0) - np.array([150, 150, 150])
    xs = np.linspace(lo[0], hi[0], max(5, min(13, int((hi[0] - lo[0]) / step_mm) + 1)))
    ys = np.linspace(lo[1], hi[1], max(4, min(9, int((hi[1] - lo[1]) / step_mm) + 1)))
    zs = np.linspace(lo[2], hi[2], max(5, min(13, int((hi[2] - lo[2]) / step_mm) + 1)))
    return np.asarray(list(itertools.product(xs, ys, zs)), dtype=np.float32)


def task14_observability(device: str) -> dict[str, Any]:
    _ = device
    data = load_data()
    cfgs = configs(data)
    pts = candidate_grid(data)
    rows = []
    for label in ["V4_CV4", "V5_CV5"]:
        gdop, vdop, cond = dop_at(pts, cfgs[label].coords)
        for p, g, v, c in zip(pts, gdop, vdop, cond):
            rows.append({"layout": label, "x": float(p[0]), "y": float(p[1]), "z": float(p[2]), "gdop": float(g), "vdop": float(v), "condition": float(c)})
    sens_rows = []
    df = pd.DataFrame(rows)
    for label, g in df.groupby("layout"):
        sens_rows.append({"layout": label, "mean_gdop": float(g["gdop"].mean()), "p95_gdop": float(g["gdop"].quantile(0.95)), "mean_vdop": float(g["vdop"].mean()), "worst_condition": float(g["condition"].max())})
    write_csv(TABLES / "task14_observability_atlas.csv", rows)
    write_csv(TABLES / "task14_sensitivity_summary.csv", sens_rows)
    def _plot(plt):
        sub = df[(df["layout"] == "V5_CV5")]
        z0 = float(sub["z"].median())
        sl = sub.iloc[(sub["z"] - z0).abs().argsort()[: len(sub) // 8]]
        fig, ax = plt.subplots(figsize=(6, 5))
        sc = ax.scatter(sl["x"], sl["y"], c=sl["gdop"], s=28)
        fig.colorbar(sc, ax=ax, label="GDOP")
        ax.set_xlabel("x mm")
        ax.set_ylabel("y mm")
        fig.tight_layout()
        fig.savefig(FIGURES / "task14_observability_map.png", dpi=150)
        plt.close(fig)
    maybe_plot("task14_observability_map", _plot)
    append_report(REPORTS / "TASK14_OBSERVABILITY_ATLAS.md", "Task 14 - Observability Atlas", sens_rows)
    return {"key_finding": f"V5 mean GDOP {next(r for r in sens_rows if r['layout']=='V5_CV5')['mean_gdop']:.2f}", "rows": len(rows)}


def task15_synthetic_cir(device: str) -> dict[str, Any]:
    _ = device
    data = load_data()
    cfg = configs(data)["V5_CV5"]
    df = feature_frame(data, cfg)
    lo = np.nanmin(np.r_[data.truth, cfg.coords], axis=0) - 300.0
    hi = np.nanmax(np.r_[data.truth, cfg.coords], axis=0) + 300.0
    rows = []
    for _, r in df.iterrows():
        p = data.truth[int(r["position_index"])]
        a = cfg.coords[int(r["anchor_id"])]
        direct = float(np.linalg.norm(p - a))
        refl = []
        for axis in range(3):
            for wall in (lo[axis], hi[axis]):
                mirror = p.copy()
                mirror[axis] = 2 * wall - mirror[axis]
                d = float(np.linalg.norm(mirror - a))
                refl.append((axis, wall, d, 0.55 / max(d * d, 1.0)))
        los_amp = 1.0 / max(direct * direct, 1.0)
        best = max(refl, key=lambda x: x[3])
        ratio = float(best[3] / los_amp)
        pred_nlos = ratio > 0.22 or float(r["elevation_angle_cfg"]) < -20.0
        meas_nlos = float(r["rho_mm"]) > 100.0
        rows.append({"position_id": r["position_id"], "anchor_label": ANCHORS[int(r["anchor_id"])], "direct_path_mm": direct, "strongest_reflection_mm": float(best[2]), "reflection_to_los_amp_ratio": ratio, "predicted_nlos": int(pred_nlos), "measured_nlos": int(meas_nlos), "rho_mm": float(r["rho_mm"])})
    cm = pd.DataFrame(rows).groupby(["predicted_nlos", "measured_nlos"]).size().reset_index(name="count").to_dict("records")
    write_csv(TABLES / "task15_synthetic_cir.csv", rows)
    write_csv(TABLES / "task15_confusion_matrix.csv", cm)
    def _plot(plt):
        worst = pd.DataFrame(rows).sort_values("rho_mm", ascending=False).head(8)
        fig, ax = plt.subplots(figsize=(6, 5))
        ax.scatter(cfg.coords[:, 0], cfg.coords[:, 2], c="tab:blue", label="anchors")
        ax.scatter(data.truth[:, 0], data.truth[:, 2], c="tab:gray", s=8, label="truth")
        for _, wr in worst.iterrows():
            aid = ANCHORS.index(wr["anchor_label"])
            p = data.truth[list(data.ids).index(wr["position_id"])]
            a = cfg.coords[aid]
            ax.plot([p[0], a[0]], [p[2], a[2]], alpha=0.4)
        ax.set_xlabel("x mm")
        ax.set_ylabel("z mm")
        ax.legend()
        fig.tight_layout()
        fig.savefig(FIGURES / "task15_ray_paths.png", dpi=150)
        plt.close(fig)
    maybe_plot("task15_ray_paths", _plot)
    append_report(REPORTS / "TASK15_SYNTHETIC_CIR.md", "Task 15 - Synthetic CIR", cm)
    hit = sum(int(r["predicted_nlos"] == r["measured_nlos"]) for r in rows) / max(len(rows), 1)
    return {"key_finding": f"synthetic NLOS agreement {hit:.2f}", "rows": len(rows)}


def acf(x: np.ndarray, lag: int) -> float:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if x.size <= lag + 2 or np.std(x) <= 1e-9:
        return float("nan")
    return safe_corr(x[:-lag], x[lag:])


def task16_dynamic_state(device: str) -> dict[str, Any]:
    _ = device
    if EXTENDED_ITEM20.exists():
        df = pd.read_csv(EXTENDED_ITEM20)
        if "solve_residual_rms_mm" in df.columns:
            for lab in ANCHORS:
                col = f"rho_{lab}"
                if col not in df.columns and "per_anchor_rho (A-H)" in df.columns:
                    pass
    elif ROTO_RHO.exists():
        raw = pd.read_csv(ROTO_RHO)
        df = raw.rename(columns={"tag": "tag", "sweep": "frame", "rho_mm": "rho", "anchor_label": "anchor", "capture_id": "capture_id"})
    else:
        raise FileNotFoundError(f"dynamic residual input not found: tried {EXTENDED_ITEM20} and {ROTO_RHO}")
    if "rho" not in df.columns:
        # Convert extended per-frame columns into long rows if present; otherwise fall back to RotoArm table.
        if ROTO_RHO.exists():
            raw = pd.read_csv(ROTO_RHO)
            df = raw.rename(columns={"sweep": "frame", "rho_mm": "rho", "anchor_label": "anchor"})
        else:
            raise RuntimeError("dynamic table lacks rho columns and RotoArm rho fallback is unavailable")
    if "anchor" not in df.columns:
        df["anchor"] = df.get("anchor_label", "")
    if "frame" not in df.columns:
        df["frame"] = np.arange(len(df))
    if "phase_deg" not in df.columns:
        df["phase_deg"] = (pd.to_numeric(df["frame"], errors="coerce").fillna(0).to_numpy(float) * 11.25) % 360.0
    if "tag" not in df.columns:
        df["tag"] = df.get("tag", "unknown")
    acf_rows = []
    phase_rows = []
    hmm_rows = []
    for (cap, tag, anchor), g in df.groupby(["capture_id", "tag", "anchor"], dropna=False):
        gg = g.sort_values("frame")
        rho = pd.to_numeric(gg["rho"], errors="coerce").to_numpy(float)
        a1, a5, a10 = acf(rho, 1), acf(rho, 5), acf(rho, 10)
        halflife = float(-math.log(0.5) / max(-math.log(max(abs(a1), 1e-3)), 1e-3)) if np.isfinite(a1) and abs(a1) < 1 else float("inf")
        acf_rows.append({"capture_id": cap, "tag": tag, "anchor": anchor, "acf_lag1": a1, "acf_lag5": a5, "acf_lag10": a10, "decay_halflife_frames": halflife})
        phase = pd.to_numeric(gg["phase_deg"], errors="coerce").fillna(0).to_numpy(float)
        bins = np.floor((phase % 360) / 30).astype(int)
        for b in range(12):
            rb = rho[bins == b]
            phase_rows.append({"capture_id": cap, "tag": tag, "anchor": anchor, "phase_bin": b, "mean_rho": float(np.nanmean(rb)) if rb.size else float("nan"), "std_rho": float(np.nanstd(rb)) if rb.size else float("nan"), "spike_rate": float(np.nanmean(rb > 100)) if rb.size else float("nan")})
        states = rho > max(100.0, np.nanmedian(rho) + np.nanstd(rho))
        trans = np.sum(states[1:] != states[:-1]) if states.size > 1 else 0
        dwell = []
        if states.size:
            start = 0
            for i in range(1, len(states) + 1):
                if i == len(states) or states[i] != states[start]:
                    if states[start]:
                        dwell.append(i - start)
                    start = i
        hmm_rows.append({"capture_id": cap, "tag": tag, "anchor": anchor, "nlos_fraction": float(np.nanmean(states)) if states.size else float("nan"), "mean_dwell_frames": float(np.mean(dwell)) if dwell else 0.0, "transition_rate": float(trans / max(states.size - 1, 1))})
    write_csv(TABLES / "task16_temporal_acf.csv", acf_rows)
    write_csv(TABLES / "task16_phase_dependent_rho.csv", phase_rows)
    write_csv(TABLES / "task16_hmm_states.csv", hmm_rows)
    append_report(REPORTS / "TASK16_DYNAMIC_STATE_SPACE.md", "Task 16 - Dynamic ROTO State-Space Mining", acf_rows[:10] + hmm_rows[:10])
    med_dwell = float(np.nanmedian([r["mean_dwell_frames"] for r in hmm_rows])) if hmm_rows else float("nan")
    return {"key_finding": f"median NLOS dwell {med_dwell:.1f} frames", "rows": len(acf_rows)}


def row_jacobian(p: np.ndarray, anchors: np.ndarray) -> np.ndarray:
    rows = []
    for a in anchors:
        diff = p - a
        u = diff / max(float(np.linalg.norm(diff)), 1e-6)
        row = np.zeros(12)
        row[0:3] = u
        row[3] = 1.0
        # Track only common delay plus tag and position terms for active design.
        rows.append(row)
    return np.vstack(rows)


def task17_active_design(device: str) -> dict[str, Any]:
    _ = device
    data = load_data()
    cfg = configs(data)["V5_CV5"]
    current = np.vstack([row_jacobian(p, cfg.coords) for p in data.truth])
    fim0 = current.T @ current / (35.0 ** 2) + np.eye(12) * 1e-6
    base_logdet = float(np.linalg.slogdet(fim0)[1])
    pts = candidate_grid(data)
    rows = []
    for p in pts:
        fim = fim0 + row_jacobian(p, cfg.coords).T @ row_jacobian(p, cfg.coords) / (35.0 ** 2)
        gain = float(np.linalg.slogdet(fim)[1] - base_logdet)
        ctr = cfg.coords.mean(axis=0)
        rows.append({"x": float(p[0]), "y": float(p[1]), "z": float(p[2]), "info_gain": gain, "rank": 0, "height_mm": float(p[1]), "distance_to_centroid_mm": float(np.linalg.norm(p - ctr))})
    rows = sorted(rows, key=lambda r: r["info_gain"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    greedy = []
    fim = fim0.copy()
    chosen: set[int] = set()
    cumulative = 0.0
    for k in range(1, 6):
        best_i = None
        best_gain = -1e9
        old_logdet = float(np.linalg.slogdet(fim)[1])
        for i, p in enumerate(pts):
            if i in chosen:
                continue
            fnew = fim + row_jacobian(p, cfg.coords).T @ row_jacobian(p, cfg.coords) / (35.0 ** 2)
            gain = float(np.linalg.slogdet(fnew)[1] - old_logdet)
            if gain > best_gain:
                best_gain = gain
                best_i = i
        assert best_i is not None
        chosen.add(best_i)
        p = pts[best_i]
        fim = fim + row_jacobian(p, cfg.coords).T @ row_jacobian(p, cfg.coords) / (35.0 ** 2)
        cumulative += best_gain
        expected_improvement = float(1.0 - math.exp(-cumulative / 12.0))
        greedy.append({"step_k": k, "added_x": float(p[0]), "added_y": float(p[1]), "added_z": float(p[2]), "cumulative_info_gain": cumulative, "expected_accuracy_improvement": expected_improvement})
    write_csv(TABLES / "task17_candidate_info_gain.csv", rows)
    write_csv(TABLES / "task17_greedy_sequence.csv", greedy)
    def _plot(plt):
        df = pd.DataFrame(rows)
        z0 = float(df["z"].median())
        sl = df.iloc[(df["z"] - z0).abs().argsort()[: max(10, len(df) // 8)]]
        fig, ax = plt.subplots(figsize=(6, 5))
        sc = ax.scatter(sl["x"], sl["y"], c=sl["info_gain"], s=28)
        fig.colorbar(sc, ax=ax, label="info gain")
        ax.set_xlabel("x mm")
        ax.set_ylabel("y mm")
        fig.tight_layout()
        fig.savefig(FIGURES / "task17_info_gain_heatmap.png", dpi=150)
        plt.close(fig)
    maybe_plot("task17_info_gain_heatmap", _plot)
    append_report(REPORTS / "TASK17_ACTIVE_DESIGN.md", "Task 17 - Active Falsification / Optimal Next Measurement", rows[:10] + greedy)
    return {"key_finding": f"top info gain {rows[0]['info_gain']:.3f}", "rows": len(rows)}


TASK_FUNCS = {
    1: ("Task 1 (Multi-room MC)", task1),
    2: ("Task 2 (Fisher)", task2),
    3: ("Task 3 (Shapley)", task3),
    4: ("Task 4 (AA vs AT)", task4),
    5: ("Task 5 (Solver search)", task5),
    6: ("Task 6 (NLOS detector)", task6),
    7: ("Task 7 (Learned correction)", task7_learned_correction),
    8: ("Task 8 (Landscape)", task8_landscape),
    9: ("Task 9 (Layout opt)", task9_layout_optimization),
    10: ("Task 10 (Residual decomp)", task10_residual_decomposition),
    11: ("Task 11 (Model tournament)", task11_model_evidence),
    12: ("Task 12 (Bayesian solver)", task12_bayesian_solver),
    13: ("Task 13 (GNN attention)", task13_gnn_attention),
    14: ("Task 14 (Observability)", task14_observability),
    15: ("Task 15 (Synthetic CIR)", task15_synthetic_cir),
    16: ("Task 16 (ROTO state)", task16_dynamic_state),
    17: ("Task 17 (Active design)", task17_active_design),
}


def run_task_on_gpu(task_id: int, gpu_id: int) -> None:
    ensure_dirs()
    name, fn = TASK_FUNCS[task_id]
    status_path = REPORTS / f"task{task_id}_status.json"
    started = time.perf_counter()
    status = {
        "task": task_id,
        "name": name,
        "gpu": f"cuda:{gpu_id}",
        "status": "FAIL",
        "runtime_s": float("nan"),
        "key_finding": "",
        "peak_vram_mb": float("nan"),
        "mean_cpu_percent": float("nan"),
        "max_cpu_percent": float("nan"),
        "mean_gpu_percent": float("nan"),
        "max_gpu_percent": float("nan"),
    }
    try:
        torch.cuda.set_device(gpu_id)
        torch.cuda.reset_peak_memory_stats(gpu_id)
        with ResourceMonitor(gpu_id) as mon:
            finding = fn(f"cuda:{gpu_id}")
        metrics = mon.summary()
        status.update(metrics)
        peak_torch = float(torch.cuda.max_memory_allocated(gpu_id) / (1024 * 1024))
        peak_mon = metrics.get("peak_vram_mb", float("nan"))
        status["peak_vram_mb"] = max(peak_torch, float(peak_mon) if np.isfinite(peak_mon) else 0.0)
        status["status"] = "OK"
        status["key_finding"] = str(finding.get("key_finding", ""))
        status["rows"] = int(finding.get("rows", 0))
        checkpoint(task_id)
    except Exception:
        tb = traceback.format_exc()
        (REPORTS / f"TASK_{task_id}_FAILURE.md").write_text(f"# {name} Failure\n\n```text\n{tb}\n```\n", encoding="utf-8")
        status["key_finding"] = "failed; see failure report"
    finally:
        status["runtime_s"] = float(time.perf_counter() - started)
        write_json(status_path, status)
        print(json.dumps(status), flush=True)


def check_cuda() -> None:
    ensure_dirs()
    info = {
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "cuda_available": bool(torch.cuda.is_available()),
        "device_count": int(torch.cuda.device_count()),
        "ld_library_path": os.environ.get("LD_LIBRARY_PATH", ""),
        "devices": [],
    }
    if torch.cuda.is_available():
        for i in range(torch.cuda.device_count()):
            info["devices"].append(torch.cuda.get_device_name(i))
    write_json(REPORTS / "CUDA_PREFLIGHT.json", info)
    if not info["cuda_available"] or info["device_count"] < 2:
        raise RuntimeError(f"Need two CUDA GPUs, got {info}")


def collect_statuses() -> list[dict[str, Any]]:
    rows = []
    for task_id in range(1, 18):
        path = REPORTS / f"task{task_id}_status.json"
        if path.exists():
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        else:
            rows.append({"task": task_id, "name": TASK_FUNCS[task_id][0], "gpu": "", "status": "MISSING", "runtime_s": float("nan"), "key_finding": "missing status"})
    return rows


def write_completion(phase_rows: list[dict[str, Any]], total_s: float) -> None:
    statuses = collect_statuses()
    st_by_task = {int(s["task"]): s for s in statuses}
    lines = ["# GPU Full Discovery Pipeline - Overnight Summary\n\n", f"Date: {datetime.now().isoformat(timespec='seconds')}\n\n", "Machine: i7-8700K + 2x GTX 1080 Ti (dual-GPU, 9 parallel phases)\n\n"]
    lines.append("| Phase | Task (cuda:0) | Status | Task (cuda:1) | Status | Phase Time |\n|---|---|---|---|---|---|\n")
    for phase, left, right in PHASES:
        left_st = st_by_task.get(left[0], {})
        if right is None:
            right_name, right_status = "-", "-"
        else:
            right_st = st_by_task.get(right[0], {})
            right_name, right_status = right_st.get("name", f"Task {right[0]}"), right_st.get("status", "MISSING")
        phase_time = next((r["wall_s"] for r in phase_rows if r["phase"] == phase), float("nan"))
        lines.append(f"| {phase} | {left_st.get('name', f'Task {left[0]}')} | {left_st.get('status', 'MISSING')} | {right_name} | {right_status} | {phase_time / 60.0:.2f} min |\n")
    ok = sum(1 for s in statuses if s.get("status") == "OK")
    fail = len(statuses) - ok
    lines.append(f"\nTasks succeeded: {ok}/17\n\nTasks failed: {fail}/17\n\nTotal wall time: {total_s / 60.0:.2f} min\n\n")
    for gpu in (0, 1):
        peaks = [float(s.get("peak_vram_mb", float("nan"))) for s in statuses if str(s.get("gpu", "")).endswith(str(gpu))]
        peaks = [p for p in peaks if np.isfinite(p)]
        lines.append(f"GPU-{gpu} peak VRAM: {(max(peaks) if peaks else float('nan')):.1f} MB\n\n")
    lines.append("| Task | Runtime min | Mean CPU % | Mean GPU % | Max GPU % | Peak VRAM MB | Key finding |\n|---|---:|---:|---:|---:|---:|---|\n")
    for st in statuses:
        lines.append(
            f"| {st.get('name','')} | {float(st.get('runtime_s', float('nan'))) / 60.0:.2f} | "
            f"{float(st.get('mean_cpu_percent', float('nan'))):.1f} | {float(st.get('mean_gpu_percent', float('nan'))):.1f} | "
            f"{float(st.get('max_gpu_percent', float('nan'))):.1f} | {float(st.get('peak_vram_mb', float('nan'))):.1f} | {st.get('key_finding','')} |\n"
        )
    (REPORTS / "OVERNIGHT_COMPLETION.md").write_text("".join(lines), encoding="utf-8")
    write_csv(TABLES / "phase_runtime.csv", phase_rows)
    write_csv(TABLES / "task_status_summary.csv", statuses)


def write_synthesis() -> None:
    statuses = collect_statuses()
    themes = {
        "Transferability": [1, 5, 9],
        "Identifiability": [2, 8],
        "Anchor attribution": [3, 4],
        "NLOS / range quality": [6, 15, 16],
        "Residual structure": [7, 10, 11],
        "Uncertainty / observability": [12, 14],
        "Learned structure": [13],
        "Future experiment design": [17],
    }
    st_by_task = {int(s["task"]): s for s in statuses}
    lines = ["# Key Findings Synthesis\n\n", f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n"]
    for theme, tasks in themes.items():
        lines.append(f"## {theme}\n\n")
        for task_id in tasks:
            st = st_by_task.get(task_id, {})
            status = st.get("status", "MISSING")
            finding = st.get("key_finding", "")
            lines.append(f"- Task {task_id}: {status}. {finding}\n")
        lines.append("\n")
    (REPORTS / "KEY_FINDINGS_SYNTHESIS.md").write_text("".join(lines), encoding="utf-8")


def print_row_counts() -> None:
    rows = []
    for path in sorted(TABLES.glob("*.csv")):
        try:
            n = max(0, sum(1 for _ in path.open("r", encoding="utf-8")) - 1)
        except Exception:
            n = -1
        rows.append({"csv": path.name, "rows": n})
    write_csv(TABLES / "output_row_counts.csv", rows)
    print("=== CSV ROW COUNTS ===", flush=True)
    for r in rows:
        print(f"{r['csv']}: {r['rows']}", flush=True)


def verify_script() -> None:
    text = THIS.read_text(encoding="utf-8")
    bad = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import cupy", "from cupy", "import cuda", "from cuda")):
            bad.append(stripped)
    pyc = subprocess.run([sys.executable, "-m", "py_compile", str(THIS)], capture_output=True, text=True)
    write_json(REPORTS / "SCRIPT_VERIFICATION.json", {"compile_returncode": pyc.returncode, "compile_stderr": pyc.stderr, "forbidden_gpu_import_tokens": bad})
    if pyc.returncode != 0:
        raise RuntimeError(pyc.stderr)
    if bad:
        raise RuntimeError(f"forbidden imports/tokens found: {bad}")


def main() -> int:
    ensure_dirs()
    verify_script()
    check_cuda()
    start = time.perf_counter()
    phase_rows: list[dict[str, Any]] = []
    ctx = mp.get_context("spawn")
    for phase, left, right in PHASES:
        phase_start = time.perf_counter()
        print(json.dumps({"phase": phase, "left": left, "right": right, "stage": "start"}), flush=True)
        procs = [ctx.Process(target=run_task_on_gpu, args=(left[0], left[1]))]
        if right is not None:
            procs.append(ctx.Process(target=run_task_on_gpu, args=(right[0], right[1])))
        for p in procs:
            p.start()
        for p in procs:
            p.join()
        row = {"phase": phase, "wall_s": float(time.perf_counter() - phase_start), "left_task": left[0], "left_exitcode": procs[0].exitcode}
        if right is not None:
            row.update({"right_task": right[0], "right_exitcode": procs[1].exitcode})
        phase_rows.append(row)
        print(json.dumps({"phase": phase, "wall_s": row["wall_s"], "stage": "done"}), flush=True)
    total_s = float(time.perf_counter() - start)
    write_completion(phase_rows, total_s)
    write_synthesis()
    print_row_counts()
    print((REPORTS / "OVERNIGHT_COMPLETION.md").read_text(encoding="utf-8"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
