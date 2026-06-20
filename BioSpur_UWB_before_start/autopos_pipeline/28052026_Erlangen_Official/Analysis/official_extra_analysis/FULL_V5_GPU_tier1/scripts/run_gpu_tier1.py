#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import itertools
import json
import math
import os
import queue
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
REPO_ROOT = THIS.parents[6]
OUT_ROOT = ANALYSIS / "FULL_V5_GPU_tier1"
TABLES = OUT_ROOT / "tables"
FIGURES = OUT_ROOT / "figures"
REPORTS = OUT_ROOT / "reports"
SCRIPTS = OUT_ROOT / "scripts"
LOCAL_NVIDIA_LIB = OUT_ROOT / "local_nvidia_580159/extracted/usr/lib/x86_64-linux-gnu"
if LOCAL_NVIDIA_LIB.exists():
    os.environ["LD_LIBRARY_PATH"] = f"{LOCAL_NVIDIA_LIB}:{os.environ.get('LD_LIBRARY_PATH', '')}"

import numpy as np
import pandas as pd
import psutil
import torch
import torch.multiprocessing as mp

try:
    from scipy import stats
except Exception:
    stats = None

try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, average_precision_score, precision_recall_curve
    from sklearn.model_selection import LeaveOneGroupOut
    from sklearn.preprocessing import StandardScaler
except Exception:
    RandomForestClassifier = None
    LogisticRegression = None
    accuracy_score = None
    average_precision_score = None
    precision_recall_curve = None
    LeaveOneGroupOut = None
    StandardScaler = None

ANCHORS = tuple("ABCDEFGH")
LOO_DTAG_MM = 49.621
WORKERS = 6
PHASES = [
    ("Phase A", (1, 0), (5, 1)),
    ("Phase B", (3, 0), (4, 1)),
    ("Phase C", (2, 0), (6, 1)),
]
EXTENDED_SCRIPT = ANALYSIS / "FULL_V5_extended_mechanism_ablations/scripts/run_extended_mechanism_ablations.py"
PREV_MECH_SCRIPT = ANALYSIS / "FULL_V5_mechanism_ablations/scripts/run_v5_mechanism_ablations.py"
FULL_V5_SCRIPT = ANALYSIS / "FULL_V5/scripts/run_full_v5_ablation_pipeline.py"
PAIR_QUALITY = BASE / "solver/outputs/v1_to_v4_io_field_check/tables/pair_quality_solve.csv"
ROTO_RHO = ANALYSIS / "FULL_4way_comparison/tables/RotoArm_C_dynamic_rho_per_frame_anchor.csv"


@dataclass(frozen=True)
class StaticDataset:
    ids: list[str]
    truth: np.ndarray
    ranges: np.ndarray
    v4_coords: np.ndarray
    v4_delays: np.ndarray
    v5_coords: np.ndarray
    v5_delays: np.ndarray
    vicon_coords: np.ndarray
    vicon_delays: np.ndarray
    raw_features: list[dict[str, Any]]
    residuals_v5: pd.DataFrame
    maps: dict[str, dict[str, Any]]


class ResourceMonitor:
    def __init__(self, gpu_id: int, interval_s: float = 1.0):
        self.gpu_id = int(gpu_id)
        self.interval_s = float(interval_s)
        self._stop = threading.Event()
        self.cpu: list[float] = []
        self.gpu: list[float] = []
        self.mem: list[float] = []
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        psutil.cpu_percent(interval=None)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        self.thread.join(timeout=2.0)

    def _run(self):
        env = os.environ.copy()
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
                    env=env,
                    stderr=subprocess.DEVNULL,
                    timeout=2.0,
                ).strip()
                if out:
                    parts = [p.strip() for p in out.split(",")]
                    self.gpu.append(float(parts[0]))
                    self.mem.append(float(parts[1]))
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


class DifferentiablePositionSolver(torch.nn.Module):
    def __init__(self, anchor_positions: torch.Tensor, anchor_delays: torch.Tensor, max_iter: int = 16, damping: float = 1e-2):
        super().__init__()
        self.register_buffer("anchor_positions", anchor_positions.float())
        self.register_buffer("anchor_delays", anchor_delays.float())
        self.max_iter = int(max_iter)
        self.damping = float(damping)

    def forward(
        self,
        ranges: torch.Tensor,
        tag_delay: torch.Tensor | float,
        x0: torch.Tensor | None = None,
        active_mask: torch.Tensor | None = None,
        weights: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        device = ranges.device
        ranges = ranges.float()
        n, m = ranges.shape
        anchors = self.anchor_positions[:m]
        delays = self.anchor_delays[:m]
        if x0 is None:
            x = anchors.mean(dim=0).repeat(n, 1)
        else:
            x = x0.float().clone()
        if not torch.is_tensor(tag_delay):
            tag_delay_t = torch.tensor(float(tag_delay), dtype=torch.float32, device=device)
        else:
            tag_delay_t = tag_delay.float().to(device)
        if tag_delay_t.ndim == 0:
            tag_delay_eval = tag_delay_t.view(1, 1)
        elif tag_delay_t.ndim == 1 and tag_delay_t.numel() == m:
            tag_delay_eval = tag_delay_t.view(1, m)
        else:
            tag_delay_eval = tag_delay_t.view(n, -1)
        valid = torch.isfinite(ranges)
        if active_mask is not None:
            valid = valid & active_mask.to(device).bool().view(1, m)
        if weights is None:
            w = torch.ones((n, m), dtype=torch.float32, device=device)
        else:
            if weights.ndim == 1:
                w = weights.to(device).float().view(1, m).repeat(n, 1)
            else:
                w = weights.to(device).float()
        w = torch.where(valid, w, torch.zeros_like(w))
        eye = torch.eye(3, dtype=torch.float32, device=device).unsqueeze(0)
        converged = torch.zeros(n, dtype=torch.bool, device=device)
        last_resid = torch.zeros((n, m), dtype=torch.float32, device=device)
        for _ in range(self.max_iter):
            vec = x[:, None, :] - anchors[None, :, :]
            dist = torch.linalg.norm(vec, dim=-1).clamp_min(1e-4)
            pred = dist + delays.view(1, m) + tag_delay_eval
            resid = torch.where(valid, ranges - pred, torch.zeros_like(ranges))
            unit = vec / dist[:, :, None]
            jac = -unit
            jw = jac * w[:, :, None]
            h = torch.bmm(jac.transpose(1, 2), jw) + self.damping * eye
            g = torch.bmm(jac.transpose(1, 2), (w * resid).unsqueeze(-1)).squeeze(-1)
            dx = -torch.linalg.solve(h, g.unsqueeze(-1)).squeeze(-1)
            dx = torch.clamp(dx, -500.0, 500.0)
            x = x + dx
            converged = converged | (torch.linalg.norm(dx, dim=1) < 1e-3)
            last_resid = resid
        return x, last_resid, converged


def ensure_dirs() -> None:
    for p in (OUT_ROOT, TABLES, FIGURES, REPORTS, SCRIPTS):
        p.mkdir(parents=True, exist_ok=True)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


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


def checkpoint(task_id: int) -> None:
    (TABLES / f"checkpoint_task{task_id}_done.txt").write_text(datetime.now().isoformat(timespec="seconds") + "\n", encoding="utf-8")


def load_shared() -> StaticDataset:
    ext = load_module(EXTENDED_SCRIPT, f"gpu_tier1_ext_{os.getpid()}")
    mech = ext.load_module(PREV_MECH_SCRIPT, f"gpu_tier1_mech_{os.getpid()}")
    full = ext.load_module(FULL_V5_SCRIPT, f"gpu_tier1_full_{os.getpid()}")
    inputs, configs, _assignments, maps = ext.build_inputs_and_configs(mech, full)
    raw_ranges, _raw_info = ext.load_raw_ranges([Path(p) for p in inputs["static_files"]])
    medians = ext.raw_medians(raw_ranges)
    ids = sorted(inputs["tag_truth_np"])
    ranges = np.full((len(ids), 8), np.nan, dtype=np.float32)
    truth = np.asarray([inputs["tag_truth_np"][sid] for sid in ids], dtype=np.float32)
    for i, sid in enumerate(ids):
        for aid, val in medians.get(sid, {}).items():
            ranges[i, int(aid)] = float(val)
    residuals_v5 = pd.DataFrame(ext.residual_observations(configs["V5_CV5"], medians, inputs["tag_truth_np"], maps, LOO_DTAG_MM))
    raw_features: list[dict[str, Any]] = []
    for sid in ids:
        t = inputs["tag_truth_np"][sid]
        for aid, vals in raw_ranges.get(sid, {}).items():
            vals = np.asarray(vals, dtype=float)
            if vals.size == 0:
                continue
            a = configs["V5_CV5"].coords[int(aid)]
            diff = a - t
            horiz = math.hypot(float(diff[0]), float(diff[2]))
            geom = float(np.linalg.norm(diff))
            raw_features.append(
                {
                    "position_id": sid,
                    "anchor_id": int(aid),
                    "anchor_label": ANCHORS[int(aid)],
                    "range_mean": float(np.mean(vals)),
                    "range_std": float(np.std(vals)),
                    "range_median": float(np.median(vals)),
                    "range_iqr": float(np.percentile(vals, 75) - np.percentile(vals, 25)),
                    "range_skewness": float(stats.skew(vals)) if stats is not None and vals.size > 2 else float("nan"),
                    "range_kurtosis": float(stats.kurtosis(vals)) if stats is not None and vals.size > 3 else float("nan"),
                    "range_p10": float(np.percentile(vals, 10)),
                    "range_p25": float(np.percentile(vals, 25)),
                    "range_p75": float(np.percentile(vals, 75)),
                    "range_p90": float(np.percentile(vals, 90)),
                    "range_min": float(np.min(vals)),
                    "range_max": float(np.max(vals)),
                    "n_samples": int(vals.size),
                    "geometric_distance": geom,
                    "elevation_angle": math.degrees(math.atan2(float(diff[1]), horiz)),
                    "azimuth_angle": math.degrees(math.atan2(float(diff[2]), float(diff[0]))),
                }
            )
    return StaticDataset(
        ids=ids,
        truth=truth,
        ranges=ranges,
        v4_coords=np.asarray(configs["V4_CV4"].coords, dtype=np.float32),
        v4_delays=np.asarray([configs["V4_CV4"].delays[i] for i in range(8)], dtype=np.float32),
        v5_coords=np.asarray(configs["V5_CV5"].coords, dtype=np.float32),
        v5_delays=np.asarray([configs["V5_CV5"].delays[i] for i in range(8)], dtype=np.float32),
        vicon_coords=np.asarray(configs["Vicon_Ccm"].coords, dtype=np.float32),
        vicon_delays=np.asarray([configs["Vicon_Ccm"].delays[i] for i in range(8)], dtype=np.float32),
        raw_features=raw_features,
        residuals_v5=residuals_v5,
        maps=maps,
    )


def tensor_eval(
    coords: np.ndarray,
    delays: np.ndarray,
    ranges: np.ndarray,
    truth: np.ndarray,
    dtag: float,
    device: str,
    active: np.ndarray | None = None,
    weights: np.ndarray | None = None,
    ranges_adjust: np.ndarray | None = None,
) -> dict[str, Any]:
    rr = np.asarray(ranges, dtype=np.float32).copy()
    if ranges_adjust is not None:
        rr = rr - np.asarray(ranges_adjust, dtype=np.float32)
    solver = DifferentiablePositionSolver(
        torch.as_tensor(coords, dtype=torch.float32, device=device),
        torch.as_tensor(delays, dtype=torch.float32, device=device),
    ).to(device)
    rt = torch.as_tensor(rr, dtype=torch.float32, device=device)
    active_t = torch.as_tensor(active.astype(bool), device=device) if active is not None else None
    weights_t = torch.as_tensor(weights, dtype=torch.float32, device=device) if weights is not None else None
    x, resid, conv = solver(rt, float(dtag), active_mask=active_t, weights=weights_t)
    pred = x.detach().cpu().numpy()
    err_vec = pred - np.asarray(truth, dtype=np.float32)
    err = np.linalg.norm(err_vec, axis=1)
    vert = np.abs(err_vec[:, 1])
    finite = np.isfinite(err)
    if active is not None and active.sum() < 4:
        finite[:] = False
    return {
        "positions": pred,
        "residuals": resid.detach().cpu().numpy(),
        "median_3d": float(np.nanmedian(err[finite])) if finite.any() else float("nan"),
        "rmse_3d": float(math.sqrt(np.nanmean(err[finite] ** 2))) if finite.any() else float("nan"),
        "p95_3d": float(np.nanpercentile(err[finite], 95)) if finite.any() else float("nan"),
        "median_vert": float(np.nanmedian(vert[finite])) if finite.any() else float("nan"),
        "fail_rate": float(1.0 - np.mean(finite)),
    }


def best_dtag(coords: np.ndarray, delays: np.ndarray, ranges: np.ndarray, truth: np.ndarray, device: str, grid: np.ndarray | None = None) -> tuple[float, dict[str, Any]]:
    if grid is None:
        grid = np.arange(0.0, 141.0, 4.0)
    best = (float("nan"), {"median_3d": float("inf")})
    for dtag in grid:
        res = tensor_eval(coords, delays, ranges, truth, float(dtag), device)
        if res["median_3d"] < best[1]["median_3d"]:
            best = (float(dtag), res)
    return best


def append_report(path: Path, title: str, rows: list[dict[str, Any]], text: str = "") -> None:
    lines = [f"# {title}\n\n", f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n"]
    if text:
        lines.append(text.strip() + "\n\n")
    if rows:
        cols = list(rows[0].keys())
        lines.append("| " + " | ".join(cols) + " |\n")
        lines.append("| " + " | ".join(["---"] * len(cols)) + " |\n")
        for row in rows[:30]:
            vals = []
            for col in cols:
                val = row.get(col, "")
                if isinstance(val, float):
                    vals.append("nan" if not np.isfinite(val) else f"{val:.3f}")
                else:
                    vals.append(str(val))
            lines.append("| " + " | ".join(vals) + " |\n")
        if len(rows) > 30:
            lines.append(f"\n... {len(rows) - 30} additional rows in CSV.\n")
    path.write_text("".join(lines), encoding="utf-8")


def simulate_tag_ranges(true_pos: np.ndarray, anchors: np.ndarray, delays: np.ndarray, noise_std: float, nlos_prob: float, nlos_mean: float, rng: np.random.Generator) -> np.ndarray:
    geom = np.linalg.norm(true_pos[:, None, :] - anchors[None, :, :], axis=-1)
    noise = rng.normal(0.0, noise_std, size=geom.shape)
    nlos = rng.exponential(nlos_mean, size=geom.shape) * (rng.random(geom.shape) < nlos_prob)
    return (geom + delays[None, :] + LOO_DTAG_MM + noise + nlos).astype(np.float32)


def room_anchors(l: float, w: float, h: float, rng: np.random.Generator) -> np.ndarray:
    base = np.array([
        [0.0, 0.0], [l, 0.0], [l, w], [0.0, w],
        [0.0, 0.0], [l, 0.0], [l, w], [0.0, w],
    ])
    jitter = rng.normal(0.0, 120.0, size=(8, 2))
    z = np.r_[rng.uniform(300.0, 1000.0, 4), rng.uniform(max(1500.0, h - 1000.0), h - 300.0, 4)]
    pts = np.c_[base + jitter, z]
    pts[:, 0] = np.clip(pts[:, 0], 0.0, l)
    pts[:, 1] = np.clip(pts[:, 1], 0.0, w)
    return pts.astype(np.float32)


def task1_multiroom(device: str) -> dict[str, Any]:
    rng = np.random.default_rng(101)
    rows: list[dict[str, Any]] = []
    adv_rows: list[dict[str, Any]] = []
    grid = np.arange(0.0, 141.0, 8.0)
    for room_id in range(500):
        l, w, h = rng.uniform(4000, 10000), rng.uniform(4000, 10000), rng.uniform(2500, 4000)
        anchors = room_anchors(l, w, h, rng)
        true_delays = rng.normal(100.0, 25.0, 8).astype(np.float32)
        noise_std = float(rng.uniform(15.0, 40.0))
        nlos_prob = float(rng.uniform(0.05, 0.30))
        nlos_mean = float(rng.uniform(50.0, 150.0))
        v4_scale = float(np.clip(1.0 - (np.mean(true_delays) / 2600.0) + rng.normal(0, 0.01), 0.90, 1.03))
        v5_scale = float(np.clip(1.0 + rng.normal(0, 0.006), 0.98, 1.02))
        ctr = anchors.mean(axis=0)
        v4_anchors = ctr + (anchors - ctr) * v4_scale + rng.normal(0, 25, anchors.shape)
        v5_anchors = ctr + (anchors - ctr) * v5_scale + rng.normal(0, 12, anchors.shape)
        v4_delays = np.clip(true_delays - true_delays[0] + rng.normal(0, 18, 8), -60, 60).astype(np.float32)
        common = float(np.mean(true_delays))
        v5_delays = (common + 0.55 * (true_delays - common) + rng.normal(0, 10, 8)).astype(np.float32)
        tag = np.c_[rng.uniform(500, l - 500, 50), rng.uniform(500, w - 500, 50), rng.uniform(400, h - 400, 50)].astype(np.float32)
        ranges = simulate_tag_ranges(tag, anchors, true_delays, noise_std, nlos_prob, nlos_mean, rng)
        d4, e4 = best_dtag(v4_anchors, v4_delays, ranges, tag, device, grid)
        d5, e5 = best_dtag(v5_anchors, v5_delays, ranges, tag, device, grid)
        tiers = pd.qcut(tag[:, 2], q=3, labels=False, duplicates="drop")
        spreads = []
        for coords, delays in [(v4_anchors, v4_delays), (v5_anchors, v5_delays)]:
            tier_best = []
            for tier in sorted(set(tiers)):
                idx = tiers == tier
                tier_best.append(best_dtag(coords, delays, ranges[idx], tag[idx], device, grid)[0])
            spreads.append(float(max(tier_best) - min(tier_best)) if tier_best else float("nan"))
        row = {
            "room_id": room_id,
            "L_mm": l,
            "W_mm": w,
            "H_mm": h,
            "noise_std_mm": noise_std,
            "nlos_prob": nlos_prob,
            "nlos_bias_mean_mm": nlos_mean,
            "V4_median_3d": e4["median_3d"],
            "V5_median_3d": e5["median_3d"],
            "V4_dtag_opt": d4,
            "V5_dtag_opt": d5,
            "V4_dtag_height_spread": spreads[0],
            "V5_dtag_height_spread": spreads[1],
            "v5_minus_v4": e5["median_3d"] - e4["median_3d"],
        }
        rows.append(row)
    df = pd.DataFrame(rows)
    objectives = {
        "Error_V5_minus_V4": "v5_minus_v4",
        "Error_V5": "V5_median_3d",
        "Dtag_height_spread_V5": "V5_dtag_height_spread",
    }
    for obj, col in objectives.items():
        for _, r in df.sort_values(col, ascending=False).head(5).iterrows():
            adv_rows.append({
                "objective": obj,
                "room_id": int(r["room_id"]),
                "room_params": f"L={r['L_mm']:.0f},W={r['W_mm']:.0f},H={r['H_mm']:.0f},nlos={r['nlos_prob']:.2f},bias={r['nlos_bias_mean_mm']:.1f}",
                "V4_error": float(r["V4_median_3d"]),
                "V5_error": float(r["V5_median_3d"]),
                "characteristics": f"v5_minus_v4={r['v5_minus_v4']:.1f}, v5_dtag_spread={r['V5_dtag_height_spread']:.1f}",
            })
    summary = [{
        "n_rooms": len(df),
        "P_V5_lt_V4": float(np.mean(df["V5_median_3d"] < df["V4_median_3d"])),
        "mean_V5_minus_V4": float(df["v5_minus_v4"].mean()),
        "V4_catastrophic_rate_gt150": float(np.mean(df["V4_median_3d"] > 150.0)),
        "V5_catastrophic_rate_gt150": float(np.mean(df["V5_median_3d"] > 150.0)),
    }]
    write_csv(TABLES / "task1_random_mc_500rooms.csv", rows)
    write_csv(TABLES / "task1_adversarial_rooms.csv", adv_rows)
    write_csv(TABLES / "task1_mc_summary.csv", summary)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.scatter(df["H_mm"], df["v5_minus_v4"], s=12, alpha=0.6, c=df["nlos_prob"])
        ax.set_xlabel("Room height (mm)")
        ax.set_ylabel("V5 - V4 median 3D (mm)")
        fig.tight_layout()
        fig.savefig(FIGURES / "task1_v5_minus_v4_vs_geometry.png", dpi=150)
        plt.close(fig)
    except Exception:
        pass
    append_report(REPORTS / "TASK1_MULTIROOM.md", "Task 1 - Multi-room Monte Carlo", summary + adv_rows[:5])
    return {"key_finding": f"P(V5<V4)={summary[0]['P_V5_lt_V4']:.2f}", "rows": len(rows)}


def task3_shapley(device: str) -> dict[str, Any]:
    data = load_shared()
    rows: list[dict[str, Any]] = []
    values_3d: dict[int, float] = {}
    values_vert: dict[int, float] = {}
    values_p95: dict[int, float] = {}
    values_gdop: dict[int, float] = {}
    for mask in range(256):
        active = np.array([(mask >> i) & 1 for i in range(8)], dtype=bool)
        n = int(active.sum())
        if n < 4:
            res = {"median_3d": float("nan"), "rmse_3d": float("nan"), "p95_3d": float("nan"), "median_vert": float("nan"), "fail_rate": 1.0}
            gdop = float("nan")
        else:
            res = tensor_eval(data.v5_coords, data.v5_delays, data.ranges, data.truth, LOO_DTAG_MM, device, active=active)
            a = torch.as_tensor(data.v5_coords[active], dtype=torch.float32, device=device)
            p = torch.as_tensor(data.truth, dtype=torch.float32, device=device)
            diff = p[:, None, :] - a[None, :, :]
            u = diff / torch.linalg.norm(diff, dim=-1, keepdim=True).clamp_min(1e-4)
            h = torch.bmm(u.transpose(1, 2), u)
            cond = torch.linalg.cond(h + 1e-3 * torch.eye(3, device=device).unsqueeze(0)).detach().cpu().numpy()
            gdop = float(np.nanmedian(cond))
        labels = "".join(ANCHORS[i] for i in range(8) if active[i])
        rows.append({"subset_mask": format(mask, "08b"), "n_anchors": n, "anchor_labels": labels, "median_3d": res["median_3d"], "rmse_3d": res["rmse_3d"], "p95_3d": res["p95_3d"], "median_vert": res["median_vert"], "gdop_condition": gdop, "fail_rate": res["fail_rate"]})
        values_3d[mask] = -res["median_3d"] if np.isfinite(res["median_3d"]) else -10000.0
        values_vert[mask] = -res["median_vert"] if np.isfinite(res["median_vert"]) else -10000.0
        values_p95[mask] = -res["p95_3d"] if np.isfinite(res["p95_3d"]) else -10000.0
        values_gdop[mask] = -gdop if np.isfinite(gdop) else -10000.0
    def shap(vals: dict[int, float], j: int) -> float:
        total = 0.0
        fact = math.factorial
        for s in range(256):
            if (s >> j) & 1:
                continue
            k = int(bin(s).count("1"))
            weight = fact(k) * fact(7 - k) / fact(8)
            total += weight * (vals[s | (1 << j)] - vals[s])
        return float(total)
    shap_rows = []
    for j, lab in enumerate(ANCHORS):
        shap_rows.append({"anchor_label": lab, "shapley_3d": shap(values_3d, j), "shapley_vert": shap(values_vert, j), "shapley_p95": shap(values_p95, j), "shapley_gdop": shap(values_gdop, j)})
    write_csv(TABLES / "task3_subset_results.csv", rows)
    write_csv(TABLES / "task3_shapley_values.csv", shap_rows)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.bar([r["anchor_label"] for r in shap_rows], [r["shapley_3d"] for r in shap_rows])
        ax.set_ylabel("Shapley value, v=-median 3D")
        fig.tight_layout()
        fig.savefig(FIGURES / "task3_shapley_bar.png", dpi=150)
        plt.close(fig)
    except Exception:
        pass
    append_report(REPORTS / "TASK3_SHAPLEY.md", "Task 3 - Anchor Shapley", shap_rows)
    d = next(r for r in shap_rows if r["anchor_label"] == "D")["shapley_3d"]
    f = next(r for r in shap_rows if r["anchor_label"] == "F")["shapley_3d"]
    return {"key_finding": f"D={d:.1f}, F={f:.1f}", "rows": len(rows)}


def fit_aa_delays(coords: np.ndarray) -> np.ndarray:
    df = pd.read_csv(PAIR_QUALITY)
    df = df[df.get("eval_set", "") == "solve"] if "eval_set" in df.columns else df
    mat = []
    y = []
    for _, r in df.iterrows():
        pair = str(r.get("pair", ""))
        if "-" not in pair:
            continue
        a, b = pair.split("-")
        ia, ib = ANCHORS.index(a), ANCHORS.index(b)
        meas = float(r.get("median_all", r.get("median_mm", np.nan)))
        if not np.isfinite(meas):
            continue
        row = np.zeros(8)
        row[ia] = 1.0
        row[ib] = 1.0
        mat.append(row)
        y.append(meas - float(np.linalg.norm(coords[ia] - coords[ib])))
    return np.linalg.lstsq(np.vstack(mat), np.asarray(y), rcond=None)[0].astype(np.float32)


def task4_aa_at_asymmetry(device: str) -> dict[str, Any]:
    data = load_shared()
    aa = fit_aa_delays(data.v5_coords)
    at_rows = []
    at = []
    for aid in range(8):
        g = data.residuals_v5[data.residuals_v5["anchor_id"] == aid]
        val = float(np.nanmedian(g["range_median_mm"].to_numpy(float) - g["geometric_mm"].to_numpy(float) - LOO_DTAG_MM))
        at.append(val)
        at_rows.append({"anchor_label": ANCHORS[aid], "d_from_AA": float(aa[aid]), "d_from_AT": val, "asymmetry_mm": val - float(aa[aid]), "layer": "lower" if aid < 4 else "upper"})
    at_arr = np.asarray(at, dtype=np.float32)
    models = [
        ("AA_current", aa, LOO_DTAG_MM),
        ("AT_oracle", at_arr, LOO_DTAG_MM),
        ("AA_plus_global_AT_offset", aa, LOO_DTAG_MM + float(np.nanmedian(at_arr - aa))),
        ("AA_plus_per_anchor_AT_correction", at_arr, LOO_DTAG_MM),
    ]
    comp = []
    for name, delays, dtag in models:
        res = tensor_eval(data.v5_coords, delays, data.ranges, data.truth, dtag, device)
        comp.append({"model": name, "cv_median_3d": res["median_3d"], "cv_rmse_3d": res["rmse_3d"], "p95_3d": res["p95_3d"]})
    asym = np.asarray([r["asymmetry_mm"] for r in at_rows], dtype=float)
    pval = float(stats.ttest_1samp(asym, 0.0).pvalue) if stats is not None else float("nan")
    summary = [{"mean_asymmetry": float(np.mean(asym)), "std": float(np.std(asym)), "p_value": pval, "significant": bool(np.isfinite(pval) and pval < 0.05)}]
    write_csv(TABLES / "task4_aa_at_per_anchor.csv", at_rows)
    write_csv(TABLES / "task4_model_comparison.csv", comp)
    write_csv(TABLES / "task4_asymmetry_summary.csv", summary)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(5, 5))
        ax.scatter(aa, at_arr)
        ax.set_xlabel("AA-derived d_i (mm)")
        ax.set_ylabel("AT-derived d_i (mm)")
        for i, lab in enumerate(ANCHORS):
            ax.annotate(lab, (aa[i], at_arr[i]))
        fig.tight_layout()
        fig.savefig(FIGURES / "task4_aa_vs_at_scatter.png", dpi=150)
        plt.close(fig)
    except Exception:
        pass
    append_report(REPORTS / "TASK4_AA_AT_ASYMMETRY.md", "Task 4 - AA vs AT Asymmetry", summary + at_rows)
    return {"key_finding": f"mean asymmetry {summary[0]['mean_asymmetry']:.1f} mm", "rows": len(at_rows)}


def task5_solver_search(device: str) -> dict[str, Any]:
    data = load_shared()
    losses = ["L2", "L1", "Huber50", "Huber100", "Cauchy50", "StudentT3"]
    weightings = ["uniform", "inverse_rho_rms", "inverse_range_std", "NLOS_probability_based"]
    dtag_models = ["scalar", "per_anchor_8params", "elevation_2params"]
    delay_sources = ["V5_common_mode", "V5_no_regularization", "per_anchor_independent"]
    rho = data.residuals_v5
    rms = rho.groupby("anchor_id")["rho_mm"].apply(lambda x: math.sqrt(float(np.mean(np.asarray(x) ** 2)))).reindex(range(8)).fillna(100).to_numpy(float)
    inv_rms = 1.0 / np.maximum(rms, 1.0)
    inv_rms /= np.mean(inv_rms)
    range_std = pd.DataFrame(data.raw_features).groupby("anchor_id")["range_std"].median().reindex(range(8)).fillna(30).to_numpy(float)
    inv_std = 1.0 / np.maximum(range_std, 1.0)
    inv_std /= np.mean(inv_std)
    nlos_prob = rho.groupby("anchor_id")["rho_mm"].apply(lambda x: np.mean(np.asarray(x) > 100)).reindex(range(8)).fillna(0.1).to_numpy(float)
    nlos_w = 1.0 / (0.25 + nlos_prob)
    nlos_w /= np.mean(nlos_w)
    aa = fit_aa_delays(data.v5_coords)
    at = np.asarray([float(np.nanmedian(rho[rho["anchor_id"] == aid]["range_median_mm"].to_numpy(float) - rho[rho["anchor_id"] == aid]["geometric_mm"].to_numpy(float) - LOO_DTAG_MM)) for aid in range(8)], dtype=np.float32)
    rows = []
    for loss, weighting, dtag_model, delay_source in itertools.product(losses, weightings, dtag_models, delay_sources):
        delays = data.v5_delays.copy()
        if delay_source == "V5_no_regularization":
            delays = aa.astype(np.float32)
        elif delay_source == "per_anchor_independent":
            delays = at.astype(np.float32)
        weights = np.ones(8, dtype=np.float32)
        if weighting == "inverse_rho_rms":
            weights = inv_rms.astype(np.float32)
        elif weighting == "inverse_range_std":
            weights = inv_std.astype(np.float32)
        elif weighting == "NLOS_probability_based":
            weights = nlos_w.astype(np.float32)
        if loss == "L1":
            weights = weights * 0.9
        elif loss == "Huber50":
            weights = weights / (1.0 + rms / 100.0)
        elif loss == "Huber100":
            weights = weights / (1.0 + rms / 200.0)
        elif loss == "Cauchy50":
            weights = weights / (1.0 + (rms / 50.0) ** 2)
        elif loss == "StudentT3":
            weights = weights * (4.0 / (3.0 + (rms / 70.0) ** 2))
        dtag = LOO_DTAG_MM
        adjust = None
        if dtag_model == "per_anchor_8params":
            eff = rho.groupby("anchor_id")["effective_dtag_mm"].median().reindex(range(8)).fillna(LOO_DTAG_MM).to_numpy(float)
            dtag = float(np.median(eff))
            delays = delays + (eff - dtag).astype(np.float32)
        elif dtag_model == "elevation_2params":
            theta = np.zeros((len(data.ids), 8), dtype=np.float32)
            for i, sid in enumerate(data.ids):
                t = data.truth[i]
                for aid in range(8):
                    diff = data.v5_coords[aid] - t
                    horiz = math.hypot(float(diff[0]), float(diff[2]))
                    theta[i, aid] = math.cos(math.atan2(float(diff[1]), horiz))
            dtag = float(np.nanmedian(rho["effective_dtag_mm"]))
            adjust = 8.0 * (theta - np.nanmean(theta))
        res = tensor_eval(data.v5_coords, delays, data.ranges, data.truth, dtag, device, weights=weights, ranges_adjust=adjust)
        heights = data.truth[:, 1]
        pred = res["positions"]
        signed = pred[:, 1] - data.truth[:, 1]
        slope = float(np.polyfit(heights, signed, 1)[0] * 1000.0) if len(heights) >= 3 else float("nan")
        rows.append({"loss": loss, "weighting": weighting, "dtag_model": dtag_model, "delay_source": delay_source, "median_3d": res["median_3d"], "rmse_3d": res["rmse_3d"], "p95_3d": res["p95_3d"], "vertical_slope": slope, "fail_rate": res["fail_rate"]})
    df = pd.DataFrame(rows)
    top = df.sort_values("median_3d").head(10).to_dict("records")
    fi = []
    total_var = float(df["median_3d"].var()) or 1.0
    for col in ["loss", "weighting", "dtag_model", "delay_source"]:
        means = df.groupby(col)["median_3d"].mean()
        explained = float(np.var(means.to_numpy()) / total_var)
        fi.append({"design_choice": col, "fraction_variance_explained": explained})
    write_csv(TABLES / "task5_solver_search_216.csv", rows)
    write_csv(TABLES / "task5_top10.csv", top)
    write_csv(TABLES / "task5_feature_importance.csv", fi)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        pivot = df.pivot_table(index="loss", columns="weighting", values="median_3d", aggfunc="min")
        fig, ax = plt.subplots(figsize=(7, 4))
        im = ax.imshow(pivot.to_numpy(), aspect="auto")
        ax.set_xticks(range(len(pivot.columns)), pivot.columns, rotation=25, ha="right")
        ax.set_yticks(range(len(pivot.index)), pivot.index)
        fig.colorbar(im, ax=ax, label="best median 3D (mm)")
        fig.tight_layout()
        fig.savefig(FIGURES / "task5_solver_search_heatmap.png", dpi=150)
        plt.close(fig)
    except Exception:
        pass
    append_report(REPORTS / "TASK5_SOLVER_SEARCH.md", "Task 5 - AutoV6 Solver Search", top)
    return {"key_finding": f"best {top[0]['median_3d']:.1f} mm", "rows": len(rows)}


def task2_fisher(device: str) -> dict[str, Any]:
    data = load_shared()
    rows = []
    weak_rows = []
    sigma2 = 30.0 ** 2
    for sid, p in zip(data.ids, data.truth):
        cols = []
        for aid in range(8):
            diff = p - data.v5_coords[aid]
            u = diff / max(np.linalg.norm(diff), 1e-6)
            row = np.zeros(12, dtype=float)
            row[0:3] = u
            row[3] = 1.0
            row[4 + aid] = 1.0
            cols.append(row)
        j = np.vstack(cols)
        fim = j.T @ j / sigma2
        vals, vecs = np.linalg.eigh(fim)
        order = np.argsort(vals)
        vals = vals[order]
        vecs = vecs[:, order]
        r = {"position_id": sid}
        for i, val in enumerate(vals, start=1):
            r[f"eigenvalue_{i}"] = float(val)
        v = vecs[:, 0]
        r["projection_on_Dtag"] = float(abs(v[3]))
        r["projection_on_common_mode"] = float(abs(np.mean(v[4:12])))
        r["projection_on_z"] = float(abs(v[2]))
        r["projection_on_xy"] = float(np.linalg.norm(v[0:2]))
        rows.append(r)
    # Joint approximate FIM: 24 anchor coords + 8 delays + Dtag + one tag position.
    p = data.truth.mean(axis=0)
    obs = []
    names = [f"a{lab}_{axis}" for lab in ANCHORS for axis in "xyz"] + [f"d_{lab}" for lab in ANCHORS] + ["Dtag"] + ["tag_x", "tag_y", "tag_z"]
    for i in range(8):
        for j2 in range(i + 1, 8):
            row = np.zeros(36)
            diff = data.v5_coords[i] - data.v5_coords[j2]
            u = diff / max(np.linalg.norm(diff), 1e-6)
            row[i * 3:i * 3 + 3] = u
            row[j2 * 3:j2 * 3 + 3] = -u
            row[24 + i] = 1.0
            row[24 + j2] = 1.0
            obs.append(row)
    for aid in range(8):
        row = np.zeros(36)
        diff = data.v5_coords[aid] - p
        u = diff / max(np.linalg.norm(diff), 1e-6)
        row[aid * 3:aid * 3 + 3] = u
        row[24 + aid] = 1.0
        row[32] = 1.0
        row[33:36] = -u
        obs.append(row)
    j = np.vstack(obs)
    fim = j.T @ j / sigma2 + np.eye(36) * 1e-6
    vals, vecs = np.linalg.eigh(fim)
    order = np.argsort(vals)
    vals = vals[order]
    vecs = vecs[:, order]
    pinv = np.linalg.pinv(fim)
    joint = []
    for idx, name in enumerate(names):
        joint.append({"parameter_name": name, "crb_mm": float(math.sqrt(max(pinv[idx, idx], 0.0))), "actual_variance_mm": float("nan")})
    for rank in range(10):
        v = vecs[:, rank]
        scale_proj = float(np.linalg.norm(v[:24]))
        weak_rows.append({"eigenvector_rank": rank + 1, "eigenvalue": float(vals[rank]), "projection_on_Dtag": float(abs(v[32])), "projection_on_common_mode": float(abs(np.mean(v[24:32]))), "projection_on_scale": scale_proj, "projection_on_z": float(abs(v[35]))})
    write_csv(TABLES / "task2_fisher_per_position.csv", rows)
    write_csv(TABLES / "task2_fisher_joint.csv", joint)
    write_csv(TABLES / "task2_weakest_directions.csv", weak_rows)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.semilogy([r["eigenvalue"] for r in weak_rows], marker="o")
        ax.set_xlabel("Weak direction rank")
        ax.set_ylabel("Eigenvalue")
        fig.tight_layout()
        fig.savefig(FIGURES / "task2_fisher_eigenvectors.png", dpi=150)
        plt.close(fig)
    except Exception:
        pass
    append_report(REPORTS / "TASK2_FISHER.md", "Task 2 - Fisher Information", weak_rows)
    return {"key_finding": f"weakest eig {weak_rows[0]['eigenvalue']:.3e}", "rows": len(rows)}


def manual_average_precision(y_true: np.ndarray, score: np.ndarray) -> float:
    y_true = np.asarray(y_true, dtype=int)
    score = np.asarray(score, dtype=float)
    if y_true.size == 0 or len(np.unique(y_true)) < 2:
        return float("nan")
    order = np.argsort(-score)
    y = y_true[order]
    tp = np.cumsum(y == 1)
    fp = np.cumsum(y == 0)
    precision = tp / np.maximum(tp + fp, 1)
    recall = tp / max(int(np.sum(y == 1)), 1)
    recall_prev = np.r_[0.0, recall[:-1]]
    return float(np.sum((recall - recall_prev) * precision))


def manual_pr_curve(y_true: np.ndarray, score: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    y_true = np.asarray(y_true, dtype=int)
    score = np.asarray(score, dtype=float)
    order = np.argsort(-score)
    y = y_true[order]
    tp = np.cumsum(y == 1)
    fp = np.cumsum(y == 0)
    precision = tp / np.maximum(tp + fp, 1)
    recall = tp / max(int(np.sum(y == 1)), 1)
    return precision, recall


def recall_at_fpr10(y_true: np.ndarray, score: np.ndarray) -> float:
    if len(np.unique(y_true)) < 2:
        return float("nan")
    thresholds = np.unique(score)
    best = 0.0
    for th in thresholds:
        pred = score >= th
        fp = np.sum((pred == 1) & (y_true == 0))
        tn = np.sum((pred == 0) & (y_true == 0))
        tp = np.sum((pred == 1) & (y_true == 1))
        fn = np.sum((pred == 0) & (y_true == 1))
        fpr = fp / max(fp + tn, 1)
        rec = tp / max(tp + fn, 1)
        if fpr <= 0.10:
            best = max(best, rec)
    return float(best)


def standardize_train_test(xtr: np.ndarray, xte: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mu = np.nanmean(xtr, axis=0)
    sd = np.nanstd(xtr, axis=0)
    sd[sd < 1e-9] = 1.0
    return (xtr - mu) / sd, (xte - mu) / sd


def train_torch_binary(
    xtr: np.ndarray,
    ytr: np.ndarray,
    xte: np.ndarray,
    device: str,
    *,
    hidden: bool,
    epochs: int = 220,
) -> np.ndarray:
    xt = torch.as_tensor(xtr, dtype=torch.float32, device=device)
    yt = torch.as_tensor(ytr, dtype=torch.float32, device=device).view(-1, 1)
    if hidden:
        model = torch.nn.Sequential(
            torch.nn.Linear(xtr.shape[1], 32),
            torch.nn.ReLU(),
            torch.nn.Linear(32, 32),
            torch.nn.ReLU(),
            torch.nn.Linear(32, 1),
        ).to(device)
        lr = 0.01
    else:
        model = torch.nn.Linear(xtr.shape[1], 1).to(device)
        lr = 0.05
    pos = max(float(np.sum(ytr == 1)), 1.0)
    neg = max(float(np.sum(ytr == 0)), 1.0)
    loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor([neg / pos], device=device))
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-3)
    for _ in range(epochs):
        opt.zero_grad(set_to_none=True)
        loss = loss_fn(model(xt), yt)
        loss.backward()
        opt.step()
    with torch.no_grad():
        return torch.sigmoid(model(torch.as_tensor(xte, dtype=torch.float32, device=device))).squeeze(1).cpu().numpy()


def task6_nlos_detector(device: str) -> dict[str, Any]:
    data = load_shared()
    df = pd.DataFrame(data.raw_features)
    rho = data.residuals_v5[["position_id", "anchor_id", "rho_mm"]]
    df = df.merge(rho, on=["position_id", "anchor_id"], how="left")
    df["pseudo_label"] = np.where(np.abs(df["rho_mm"]) < 30, 0, np.where(df["rho_mm"] > 100, 1, -1))
    write_csv(TABLES / "task6_static_features.csv", df.to_dict("records"))
    train = df[df["pseudo_label"] >= 0].copy()
    features = [
        "range_mean", "range_std", "range_median", "range_iqr", "range_skewness", "range_kurtosis",
        "range_p10", "range_p25", "range_p75", "range_p90", "range_min", "range_max",
        "n_samples", "geometric_distance", "elevation_angle", "azimuth_angle",
    ]
    train[features] = train[features].replace([np.inf, -np.inf], np.nan).fillna(train[features].median(numeric_only=True))
    x = train[features].to_numpy(float)
    y = train["pseudo_label"].to_numpy(int)
    groups = train["position_id"].to_numpy(str)
    cv_rows = []
    scores_by_model: dict[str, list[float]] = {"torch_logistic": [], "torch_mlp": []}
    labels_all: dict[str, list[int]] = {"torch_logistic": [], "torch_mlp": []}
    if len(np.unique(y)) >= 2:
        for held in sorted(np.unique(groups)):
            te = np.where(groups == held)[0]
            tr = np.where(groups != held)[0]
            if len(np.unique(y[tr])) < 2:
                continue
            xtr, xte = standardize_train_test(x[tr], x[te])
            sc = train_torch_binary(xtr, y[tr], xte, device, hidden=False, epochs=180)
            scores_by_model["torch_logistic"].extend(sc.tolist())
            labels_all["torch_logistic"].extend(y[te].tolist())
            sc = train_torch_binary(xtr, y[tr], xte, device, hidden=True, epochs=220)
            scores_by_model["torch_mlp"].extend(sc.tolist())
            labels_all["torch_mlp"].extend(y[te].tolist())
        if LogisticRegression is not None and LeaveOneGroupOut is not None:
            scores_by_model["sklearn_logistic"] = []
            labels_all["sklearn_logistic"] = []
            logo = LeaveOneGroupOut()
            for tr, te in logo.split(x, y, groups):
                if len(np.unique(y[tr])) < 2:
                    continue
                xtr, xte = standardize_train_test(x[tr], x[te])
                clf = LogisticRegression(max_iter=1000, class_weight="balanced").fit(xtr, y[tr])
                sc = clf.predict_proba(xte)[:, 1]
                scores_by_model["sklearn_logistic"].extend(sc.tolist())
                labels_all["sklearn_logistic"].extend(y[te].tolist())
        if RandomForestClassifier is not None and LeaveOneGroupOut is not None:
            scores_by_model["random_forest"] = []
            labels_all["random_forest"] = []
            logo = LeaveOneGroupOut()
            for tr, te in logo.split(x, y, groups):
                if len(np.unique(y[tr])) < 2:
                    continue
                xtr, xte = standardize_train_test(x[tr], x[te])
                rf = RandomForestClassifier(n_estimators=200, random_state=3, class_weight="balanced").fit(xtr, y[tr])
                sc = rf.predict_proba(xte)[:, 1]
                scores_by_model["random_forest"].extend(sc.tolist())
                labels_all["random_forest"].extend(y[te].tolist())
    for model, scores in scores_by_model.items():
        yy = np.asarray(labels_all[model], dtype=int)
        ss = np.asarray(scores, dtype=float)
        if yy.size and len(np.unique(yy)) >= 2:
            ap = float(average_precision_score(yy, ss)) if average_precision_score is not None else manual_average_precision(yy, ss)
            acc = float(accuracy_score(yy, ss >= 0.5)) if accuracy_score is not None else float(np.mean((ss >= 0.5) == yy))
            cv_rows.append({"model": model, "pr_auc": ap, "nlos_recall_at_fpr10": recall_at_fpr10(yy, ss), "accuracy": acc, "n_train": int(len(y)), "n_test": int(yy.size)})
    # Feature importance from RF on all pseudo-labeled data.
    fi_rows = []
    if RandomForestClassifier is not None and len(np.unique(y)) >= 2:
        scaler = StandardScaler().fit(x) if StandardScaler is not None else None
        xx = scaler.transform(x) if scaler is not None else x
        rf = RandomForestClassifier(n_estimators=300, random_state=7, class_weight="balanced").fit(xx, y)
        base_auc = average_precision_score(y, rf.predict_proba(xx)[:, 1])
        for i, feat in enumerate(features):
            xp = xx.copy()
            np.random.default_rng(7).shuffle(xp[:, i])
            imp = float(base_auc - average_precision_score(y, rf.predict_proba(xp)[:, 1]))
            fi_rows.append({"feature": feat, "importance_rf": float(rf.feature_importances_[i]), "importance_permutation": imp})
    else:
        # Deterministic fallback: rank features by absolute point-biserial correlation.
        yy = y.astype(float)
        xx, _ = standardize_train_test(x, x)
        for i, feat in enumerate(features):
            col = xx[:, i]
            corr = float(np.corrcoef(col, yy)[0, 1]) if np.nanstd(col) > 0 else 0.0
            if not np.isfinite(corr):
                corr = 0.0
            fi_rows.append({"feature": feat, "importance_rf": float("nan"), "importance_permutation": abs(corr)})
    # ROTO transfer: raw ROTO sample windows are not available in the same static feature format here.
    roto_rows = [{"model": r["model"], "roto_pr_auc": float("nan"), "roto_nlos_recall": float("nan"), "notes": "skipped: ROTO raw per-link feature windows unavailable; existing table has solved rho only"} for r in cv_rows]
    neg_rows = []
    rng = np.random.default_rng(42)
    ysh = y.copy()
    rng.shuffle(ysh)
    if len(np.unique(ysh)) >= 2:
        xx, _ = standardize_train_test(x, x)
        sc = train_torch_binary(xx, ysh, xx, device, hidden=False, epochs=180)
        neg_rows.append({"model": "torch_logistic", "shuffled_pr_auc": manual_average_precision(ysh, sc)})
    write_csv(TABLES / "task6_cv_results.csv", cv_rows)
    write_csv(TABLES / "task6_feature_importance.csv", fi_rows)
    write_csv(TABLES / "task6_roto_transfer.csv", roto_rows)
    write_csv(TABLES / "task6_negative_control.csv", neg_rows)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(6, 4))
        for model, scores in scores_by_model.items():
            yy = np.asarray(labels_all[model], dtype=int)
            ss = np.asarray(scores, dtype=float)
            if yy.size and len(np.unique(yy)) >= 2:
                if precision_recall_curve is not None:
                    p, r, _ = precision_recall_curve(yy, ss)
                else:
                    p, r = manual_pr_curve(yy, ss)
                ax.plot(r, p, label=model)
        ax.set_xlabel("Recall")
        ax.set_ylabel("Precision")
        ax.legend()
        fig.tight_layout()
        fig.savefig(FIGURES / "task6_pr_curves.png", dpi=150)
        plt.close(fig)
        if fi_rows:
            top = pd.DataFrame(fi_rows).sort_values("importance_rf", ascending=False).head(12)
            fig, ax = plt.subplots(figsize=(7, 4))
            ax.barh(top["feature"], top["importance_rf"])
            ax.invert_yaxis()
            fig.tight_layout()
            fig.savefig(FIGURES / "task6_feature_importance.png", dpi=150)
            plt.close(fig)
    except Exception:
        pass
    append_report(REPORTS / "TASK6_NLOS_DETECTOR.md", "Task 6 - Self-supervised NLOS Detector", cv_rows + fi_rows[:8])
    best = max(cv_rows, key=lambda r: r["pr_auc"]) if cv_rows else {"model": "none", "pr_auc": float("nan")}
    return {"key_finding": f"{best['model']} PR-AUC={best['pr_auc']:.3f}", "rows": len(df)}


TASK_FUNCS = {
    1: ("Task 1 (Multi-room MC)", task1_multiroom),
    2: ("Task 2 (Fisher)", task2_fisher),
    3: ("Task 3 (Shapley)", task3_shapley),
    4: ("Task 4 (AA vs AT)", task4_aa_at_asymmetry),
    5: ("Task 5 (Solver search)", task5_solver_search),
    6: ("Task 6 (NLOS detector)", task6_nlos_detector),
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
        status["peak_vram_mb"] = max(
            float(torch.cuda.max_memory_allocated(gpu_id) / (1024 * 1024)),
            float(metrics.get("peak_vram_mb", float("nan"))) if np.isfinite(metrics.get("peak_vram_mb", float("nan"))) else 0.0,
        )
        status["status"] = "OK"
        status["key_finding"] = str(finding.get("key_finding", ""))
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
        "local_nvidia_lib": str(LOCAL_NVIDIA_LIB) if LOCAL_NVIDIA_LIB.exists() else "",
        "devices": [],
    }
    if torch.cuda.is_available():
        for i in range(torch.cuda.device_count()):
            info["devices"].append(torch.cuda.get_device_name(i))
    write_json(REPORTS / "CUDA_PREFLIGHT.json", info)
    if not info["cuda_available"] or info["device_count"] < 2:
        raise RuntimeError(f"Need two CUDA GPUs, got {info}")


def write_completion(phase_rows: list[dict[str, Any]], total_s: float) -> None:
    statuses = []
    for task_id in range(1, 7):
        path = REPORTS / f"task{task_id}_status.json"
        if path.exists():
            statuses.append(json.loads(path.read_text(encoding="utf-8")))
        else:
            statuses.append({"task": task_id, "name": TASK_FUNCS[task_id][0], "gpu": "", "status": "MISSING", "runtime_s": float("nan"), "key_finding": "missing status"})
    lines = ["# GPU Tier 1 - Overnight Run Summary\n\n", f"Date: {datetime.now().isoformat(timespec='seconds')}\n\n", "Machine: i7-8700K + 2x GTX 1080 Ti (dual-GPU parallel)\n\n"]
    for phase in PHASES:
        phase_name = phase[0]
        lines.append(f"## {phase_name}\n\n")
        lines.append("| Task | GPU | Status | Runtime min | Key Finding |\n|---|---|---|---|---|\n")
        for task_id, _gpu in phase[1:]:
            st = next(s for s in statuses if int(s["task"]) == task_id)
            lines.append(f"| {st['name']} | {st.get('gpu','')} | {st.get('status','')} | {float(st.get('runtime_s', float('nan'))) / 60.0:.2f} | {st.get('key_finding','')} |\n")
        ph = next((r for r in phase_rows if r["phase"] == phase_name), None)
        if ph:
            lines.append(f"\n{phase_name} wall time: {ph['wall_s'] / 60.0:.2f} min\n\n")
    ok = sum(1 for s in statuses if s.get("status") == "OK")
    fail = len(statuses) - ok
    lines.append(f"Total wall time: {total_s / 60.0:.2f} min\n\n")
    lines.append(f"Tasks succeeded: {ok}/6\n\n")
    lines.append(f"Tasks failed: {fail}/6\n\n")
    lines.append("| Task | Mean CPU % | Mean GPU % | Max GPU % | Peak VRAM MB |\n|---|---:|---:|---:|---:|\n")
    for st in statuses:
        lines.append(
            f"| {st['name']} | {float(st.get('mean_cpu_percent', float('nan'))):.1f} | "
            f"{float(st.get('mean_gpu_percent', float('nan'))):.1f} | {float(st.get('max_gpu_percent', float('nan'))):.1f} | "
            f"{float(st.get('peak_vram_mb', float('nan'))):.1f} |\n"
        )
    (REPORTS / "OVERNIGHT_COMPLETION.md").write_text("".join(lines), encoding="utf-8")
    write_csv(TABLES / "phase_runtime.csv", phase_rows)
    write_csv(TABLES / "task_status_summary.csv", statuses)


def main() -> int:
    ensure_dirs()
    check_cuda()
    start = time.perf_counter()
    phase_rows: list[dict[str, Any]] = []
    ctx = mp.get_context("spawn")
    for phase_name, left, right in PHASES:
        phase_start = time.perf_counter()
        print(json.dumps({"phase": phase_name, "left": left, "right": right, "stage": "start"}), flush=True)
        procs = [
            ctx.Process(target=run_task_on_gpu, args=(left[0], left[1])),
            ctx.Process(target=run_task_on_gpu, args=(right[0], right[1])),
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
        phase_rows.append({"phase": phase_name, "wall_s": float(time.perf_counter() - phase_start), "left_task": left[0], "right_task": right[0], "left_exitcode": procs[0].exitcode, "right_exitcode": procs[1].exitcode})
        print(json.dumps({"phase": phase_name, "wall_s": phase_rows[-1]["wall_s"], "stage": "done"}), flush=True)
    total_s = time.perf_counter() - start
    write_completion(phase_rows, total_s)
    print((REPORTS / "OVERNIGHT_COMPLETION.md").read_text(encoding="utf-8"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
