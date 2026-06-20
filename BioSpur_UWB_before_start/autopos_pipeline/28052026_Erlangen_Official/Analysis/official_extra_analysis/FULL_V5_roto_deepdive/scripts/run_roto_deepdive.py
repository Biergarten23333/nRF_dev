#!/usr/bin/env python3
from __future__ import annotations

import ast
import csv
import importlib.util
import json
import math
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from multiprocessing import get_context
from pathlib import Path
from typing import Any

for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

import numpy as np
import pandas as pd

try:
    import psutil
except Exception:
    psutil = None

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception:
    plt = None

try:
    from scipy.optimize import least_squares
except Exception:
    least_squares = None


BASE = Path("/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official")
ANALYSIS = BASE / "Analysis/official_extra_analysis"
OUT_ROOT = ANALYSIS / "FULL_V5_roto_deepdive"
SCRIPTS = OUT_ROOT / "scripts"
TABLES = OUT_ROOT / "tables"
FIGURES = OUT_ROOT / "figures"
REPORTS = OUT_ROOT / "reports"

FULL_V5_SCRIPT = ANALYSIS / "FULL_V5/scripts/run_full_v5_ablation_pipeline.py"
FOLLOWUP_SCRIPT = ANALYSIS / "FULL_V5_followup_validation/scripts/run_followup_validation.py"
RHO_PATH = ANALYSIS / "FULL_4way_comparison/tables/RotoArm_C_dynamic_rho_per_frame_anchor.csv"

ROTO_TAGS = ["BS2DCE", "BSDC91"]
ANCHORS = list("ABCDEFGH")
LOO_DTAG_MM = 49.621
WORKERS = 6
SAMPLES_PATH = TABLES / "roto_v5_dloo_samples.csv"
RANGES_PATH = TABLES / "roto_v5_dloo_ranges_long.csv"


def ensure_dirs() -> None:
    for p in (OUT_ROOT, SCRIPTS, TABLES, FIGURES, REPORTS):
        p.mkdir(parents=True, exist_ok=True)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


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
        writer.writerows(rows)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def pct(vals: Any, q: float) -> float:
    arr = np.asarray(vals, dtype=float)
    arr = arr[np.isfinite(arr)]
    return float(np.percentile(arr, q)) if arr.size else float("nan")


def rmse(vals: Any) -> float:
    arr = np.asarray(vals, dtype=float)
    arr = arr[np.isfinite(arr)]
    return float(math.sqrt(np.mean(arr * arr))) if arr.size else float("nan")


def summarize_err(err: np.ndarray) -> dict[str, float]:
    return {"median_3d": pct(err, 50), "p95": pct(err, 95), "rmse": rmse(err)}


def md_table(rows: list[dict[str, Any]], max_rows: int = 30) -> str:
    if not rows:
        return ""
    cols = list(rows[0].keys())
    lines = ["| " + " | ".join(cols) + " |\n", "| " + " | ".join(["---"] * len(cols)) + " |\n"]
    for row in rows[:max_rows]:
        vals = []
        for col in cols:
            val = row.get(col, "")
            if isinstance(val, float):
                vals.append("nan" if not np.isfinite(val) else f"{val:.3f}")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |\n")
    if len(rows) > max_rows:
        lines.append(f"\n... {len(rows)-max_rows} more rows in CSV.\n")
    return "".join(lines)


def write_report(path: Path, title: str, rows: list[dict[str, Any]] | None = None, text: str = "") -> None:
    lines = [f"# {title}\n\n", f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n"]
    if text:
        lines.append(text.strip() + "\n\n")
    if rows:
        lines.append(md_table(rows))
    path.write_text("".join(lines), encoding="utf-8")


class ResourceMonitor:
    def __enter__(self):
        self.cpu: list[float] = []
        self._stop = False
        import threading

        def loop() -> None:
            while not self._stop:
                if psutil is not None:
                    self.cpu.append(float(psutil.cpu_percent(interval=None)))
                time.sleep(0.5)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop = True
        self._thread.join(timeout=1)

    def summary(self) -> dict[str, Any]:
        return {
            "mean_cpu_percent": float(np.nanmean(self.cpu)) if self.cpu else float("nan"),
            "max_cpu_percent": float(np.nanmax(self.cpu)) if self.cpu else float("nan"),
            "workers": WORKERS,
        }


def checkpoint_path(task: str) -> Path:
    return TABLES / f"checkpoint_{task.lower()}_done.txt"


def status_path(task: str) -> Path:
    return REPORTS / f"{task.lower()}_status.json"


def run_task(task: str, fn) -> dict[str, Any]:
    if checkpoint_path(task).exists() and status_path(task).exists():
        status = json.loads(status_path(task).read_text(encoding="utf-8"))
        status["checkpoint_reused"] = True
        return status
    started = time.perf_counter()
    with ResourceMonitor() as mon:
        try:
            result = fn()
            status = {"task": task, "status": "ok", "elapsed_s": time.perf_counter() - started, **result, **mon.summary()}
            checkpoint_path(task).write_text(datetime.now().isoformat(timespec="seconds") + "\n", encoding="utf-8")
        except Exception as exc:
            status = {"task": task, "status": "failed", "elapsed_s": time.perf_counter() - started, "error": repr(exc), "traceback": traceback.format_exc(), **mon.summary()}
    write_json(status_path(task), status)
    print(json.dumps(status, sort_keys=True), flush=True)
    return status


def fit_similarity(src: np.ndarray, dst: np.ndarray, allow_scale: bool) -> tuple[np.ndarray, np.ndarray, float]:
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    cs = src.mean(axis=0)
    cd = dst.mean(axis=0)
    x = src - cs
    y = dst - cd
    u, svals, vt = np.linalg.svd(x.T @ y)
    r = u @ vt
    if np.linalg.det(r) < 0:
        vt[-1, :] *= -1
        r = u @ vt
    scale = 1.0
    if allow_scale:
        denom = float(np.sum(x * x))
        scale = float(np.sum(svals) / denom) if denom > 0 else 1.0
    t = cd - scale * cs @ r
    return r, t, scale


def apply_transform(points: np.ndarray, r: np.ndarray, t: np.ndarray, s: float) -> np.ndarray:
    return float(s) * np.asarray(points, dtype=float) @ r + t


def rotation_angle_deg(r: np.ndarray) -> float:
    val = (np.trace(r) - 1.0) / 2.0
    return float(math.degrees(math.acos(float(np.clip(val, -1.0, 1.0)))))


def interpolate_opti(full: Any, opti_by_capture: dict[str, Any], cid: str, marker: str, time_s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return full.interpolate_opti(opti_by_capture[cid][marker], np.asarray(time_s, dtype=float))


def build_context() -> dict[str, Any]:
    full = load_module(FULL_V5_SCRIPT, "roto_deep_full_v5")
    inputs = full.prepare_inputs()
    fit_rigid = full.fit_similarity(inputs["coords_v5"], inputs["truth_coords"], allow_reflection=True, allow_scale=False)
    fit_sim3 = full.fit_similarity(inputs["coords_v5"], inputs["truth_coords"], allow_reflection=True, allow_scale=True)
    anchors_vicon = full.apply_fit(inputs["coords_v5"], fit_rigid)
    return {"full": full, "inputs": inputs, "fit_rigid": fit_rigid, "fit_sim3": fit_sim3, "anchors_vicon": anchors_vicon}


def solve_roto_job(job: dict[str, Any]) -> dict[str, Any]:
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    full = load_module(FULL_V5_SCRIPT, f"roto_deep_full_worker_{os.getpid()}")
    return full.solve_roto_track_worker(job)


def solve_cache(ctx: dict[str, Any]) -> pd.DataFrame:
    full = ctx["full"]
    inputs = ctx["inputs"]
    if SAMPLES_PATH.exists():
        return pd.read_csv(SAMPLES_PATH)
    layout_dir = TABLES / "generated_layouts"
    layout_path = layout_dir / "v5_commonmode_dloo.json"
    full.write_layout_json(layout_path, name="roto_deep_v5_commonmode_dloo", coords=inputs["coords_v5"], delays=inputs["delays_v5"], tag_delay_mm=0.0)
    jobs = []
    for cid, cap_path in inputs["roto_files"].items():
        for tag in ROTO_TAGS:
            jobs.append(
                {
                    "track_id": f"DLOO_{cid}_{tag}",
                    "layout_path": str(layout_path),
                    "capture_path": str(cap_path),
                    "sigma_path": str(full.SIGMA_PATH),
                    "layout_source": "L_V5",
                    "correction_source": "C_V5",
                    "tag_delay_mode": "D_LOO_CV",
                    "d_tag_mm": LOO_DTAG_MM,
                    "capture_id": cid,
                    "tag": tag,
                    "method": "T4",
                    "need_rho": False,
                }
            )
    tracks = []
    mp_ctx = get_context("spawn")
    with ProcessPoolExecutor(max_workers=WORKERS, mp_context=mp_ctx) as pool:
        futures = [pool.submit(solve_roto_job, j) for j in jobs]
        for i, fut in enumerate(as_completed(futures), 1):
            tracks.append(fut.result())
            if i % 8 == 0 or i == len(futures):
                print(json.dumps({"stage": "solve_roto_cache", "done": i, "total": len(futures)}), flush=True)
    rows = []
    fit = ctx["fit_rigid"]
    for tr in tracks:
        cid = tr["capture_id"]
        tag = tr["tag"]
        marker = inputs["mapping"].get(tag, "")
        beta = inputs["offsets"].get(cid, float("nan"))
        if not marker or not np.isfinite(beta):
            continue
        pts_raw = np.asarray([[p["x_mm"], p["y_mm"], p["z_mm"]] for p in tr["points"]], dtype=float)
        pts = full.apply_fit(pts_raw, fit)
        t = np.asarray([p["time_s"] for p in tr["points"]], dtype=float)
        truth0, good0 = interpolate_opti(full, inputs["opti_by_capture"], cid, marker, t)
        truth, good = interpolate_opti(full, inputs["opti_by_capture"], cid, marker, t + beta)
        for p, raw, tv0, tv, ok0, ok, item in zip(pts, pts_raw, truth0, truth, good0, good, tr["points"]):
            if not ok:
                continue
            diff = p - tv
            diff0 = p - tv0 if ok0 else np.full(3, np.nan)
            rows.append(
                {
                    "capture_id": cid,
                    "tag": tag,
                    "marker": marker,
                    "sweep": int(item["sweep"]),
                    "uwb_time_s": float(item["time_s"]),
                    "beta_s": float(beta),
                    "x_raw": float(raw[0]),
                    "y_raw": float(raw[1]),
                    "z_raw": float(raw[2]),
                    "x": float(p[0]),
                    "y": float(p[1]),
                    "z": float(p[2]),
                    "truth_x": float(tv[0]),
                    "truth_y": float(tv[1]),
                    "truth_z": float(tv[2]),
                    "truth0_x": float(tv0[0]) if ok0 else float("nan"),
                    "truth0_y": float(tv0[1]) if ok0 else float("nan"),
                    "truth0_z": float(tv0[2]) if ok0 else float("nan"),
                    "err3d_mm": float(np.linalg.norm(diff)),
                    "err3d_beta0_mm": float(np.linalg.norm(diff0)) if ok0 else float("nan"),
                    "residual_rms_mm": float(item.get("residual_rms_mm", float("nan"))),
                }
            )
    write_csv(SAMPLES_PATH, rows)
    return pd.DataFrame(rows)


def load_ranges_long() -> pd.DataFrame:
    if RANGES_PATH.exists():
        return pd.read_csv(RANGES_PATH)
    df = pd.read_csv(RHO_PATH)
    df = df[(df["layout"].astype(str).str.contains("v5", case=False, na=False)) & (np.isclose(df["d_tag_mm"].astype(float), 49.6, atol=0.2))]
    cols = ["capture_id", "tag", "sweep", "host_elapsed_s", "anchor_id", "anchor_label", "range_measured_mm", "rho_mm", "solver_used_anchor"]
    out = df[cols].copy()
    out.to_csv(RANGES_PATH, index=False)
    return out


def time_offset_metrics(samples: pd.DataFrame, ctx: dict[str, Any], cid: str, tag: str, dt_ms: np.ndarray) -> list[dict[str, Any]]:
    full = ctx["full"]
    inputs = ctx["inputs"]
    sub = samples[(samples["capture_id"] == cid) & (samples["tag"] == tag)].sort_values("uwb_time_s")
    xyz = sub[["x", "y", "z"]].to_numpy(float)
    t = sub["uwb_time_s"].to_numpy(float)
    beta = float(sub["beta_s"].iloc[0])
    marker = str(sub["marker"].iloc[0])
    rows = []
    for dms in dt_ms:
        truth, good = interpolate_opti(full, inputs["opti_by_capture"], cid, marker, t + beta + float(dms) / 1000.0)
        mask = good & np.isfinite(truth).all(axis=1)
        err = np.linalg.norm(xyz[mask] - truth[mask], axis=1)
        rows.append({"capture_id": cid, "tag": tag, "delta_t_ms": float(dms), "median_3d": pct(err, 50), "rmse": rmse(err), "n": int(err.size)})
    return rows


def task_r1(ctx: dict[str, Any], samples: pd.DataFrame) -> dict[str, Any]:
    dt_ms = np.arange(-500, 501, 1)
    sweep_rows = []
    best_rows = []
    corrected = []
    for (cid, tag), g in samples.groupby(["capture_id", "tag"]):
        rows = time_offset_metrics(samples, ctx, cid, tag, dt_ms)
        sweep_rows.extend(rows)
        df = pd.DataFrame(rows)
        best = df.sort_values("median_3d").iloc[0]
        zero = df.iloc[(df["delta_t_ms"].abs()).argmin()]
        existing = summarize_err(g["err3d_mm"].to_numpy(float))
        best_rows.append({"capture_id": cid, "tag": tag, "delta_t_opt_ms": float(best["delta_t_ms"]), "improvement_mm": float(zero["median_3d"] - best["median_3d"]), "method": "median_3d_grid"})
        corrected.append({"capture_id": cid, "tag": tag, "median_3d_before": existing["median_3d"], "median_3d_after": float(best["median_3d"]), "delta_t_opt_ms": float(best["delta_t_ms"])})
    write_csv(TABLES / "r1_time_offset_sweep.csv", sweep_rows)
    write_csv(TABLES / "r1_best_offset.csv", best_rows)
    write_csv(TABLES / "r1_time_corrected_results.csv", corrected)
    if plt is not None:
        fig, ax = plt.subplots(figsize=(7, 4))
        sdf = pd.DataFrame(sweep_rows)
        for (_cid, _tag), g in sdf.groupby(["capture_id", "tag"]):
            ax.plot(g["delta_t_ms"], g["median_3d"], alpha=0.18, lw=0.8)
        ax.set_xlabel("additional delta_t (ms)")
        ax.set_ylabel("median 3D error (mm)")
        fig.tight_layout()
        fig.savefig(FIGURES / "r1_error_vs_time_offset.png", dpi=220)
        plt.close(fig)
    write_report(REPORTS / "TASK_R1_TIME_SYNC.md", "Task R1 - Time Offset Optimization", best_rows[:20], "Sweep is relative to the existing capture-level beta_s offsets.")
    return {"key_finding": f"median improvement {pct([r['improvement_mm'] for r in best_rows], 50):.1f} mm", "rows": len(sweep_rows)}


def align_errors(src: np.ndarray, dst: np.ndarray, method: str) -> tuple[np.ndarray, float, float, float]:
    if method == "none":
        pred = src
        r = np.eye(3)
        t = np.zeros(3)
        s = 1.0
    elif method == "translation":
        t = dst.mean(axis=0) - src.mean(axis=0)
        pred = src + t
        r = np.eye(3)
        s = 1.0
    elif method == "se3":
        r, t, s = fit_similarity(src, dst, allow_scale=False)
        pred = apply_transform(src, r, t, s)
    elif method == "sim3":
        r, t, s = fit_similarity(src, dst, allow_scale=True)
        pred = apply_transform(src, r, t, s)
    else:
        raise ValueError(method)
    err = np.linalg.norm(pred - dst, axis=1)
    return err, s, rotation_angle_deg(r), float(np.linalg.norm(t))


def task_r2(ctx: dict[str, Any], samples: pd.DataFrame) -> dict[str, Any]:
    best_dt = pd.read_csv(TABLES / "r1_best_offset.csv") if (TABLES / "r1_best_offset.csv").exists() else pd.DataFrame()
    dt_map = {(r.capture_id, r.tag): float(r.delta_t_opt_ms) / 1000.0 for r in best_dt.itertuples()} if not best_dt.empty else {}
    rows = []
    for (cid, tag), g in samples.groupby(["capture_id", "tag"]):
        g = g.sort_values("uwb_time_s")
        src = g[["x", "y", "z"]].to_numpy(float)
        dst_existing = g[["truth_x", "truth_y", "truth_z"]].to_numpy(float)
        dst0 = g[["truth0_x", "truth0_y", "truth0_z"]].to_numpy(float)
        methods = [
            ("A_none_beta0", "none", dst0),
            ("B_translation_existing_beta", "translation", dst_existing),
            ("C_SE3_existing_beta", "se3", dst_existing),
            ("D_Sim3_existing_beta", "sim3", dst_existing),
            ("E_current_anchor_bridge_existing_beta", "none", dst_existing),
        ]
        dt = dt_map.get((cid, tag), 0.0)
        if abs(dt) > 0:
            truth_dt, good = interpolate_opti(ctx["full"], ctx["inputs"]["opti_by_capture"], cid, str(g["marker"].iloc[0]), g["uwb_time_s"].to_numpy(float) + float(g["beta_s"].iloc[0]) + dt)
            src_f = src[good]
            dst_f = truth_dt[good]
            methods.append(("F_time_corrected_SE3", "se3", dst_f))
        for name, meth, dst in methods:
            mask = np.isfinite(src).all(axis=1) & np.isfinite(dst).all(axis=1)
            src_use = src[mask] if name != "F_time_corrected_SE3" else src_f
            dst_use = dst[mask] if name != "F_time_corrected_SE3" else dst_f
            if len(src_use) < 4:
                continue
            err, scale, rot, trans = align_errors(src_use, dst_use, meth)
            rows.append({"capture_id": cid, "tag": tag, "method": name, "median_3d": pct(err, 50), "p95": pct(err, 95), "rmse": rmse(err), "scale_factor": scale, "rotation_deg": rot, "translation_mm": trans, "n": int(len(err))})
    df = pd.DataFrame(rows)
    summary = []
    for method, g in df.groupby("method"):
        summary.append({"method": method, "overall_median": pct(g["median_3d"], 50), "overall_p95": pct(g["p95"], 50), "overall_rmse": pct(g["rmse"], 50), "median_scale_factor": pct(g["scale_factor"], 50)})
    write_csv(TABLES / "r2_alignment_comparison.csv", rows)
    write_csv(TABLES / "r2_alignment_summary.csv", summary)
    if plt is not None:
        fig, ax = plt.subplots(figsize=(7, 4))
        s = pd.DataFrame(summary).sort_values("overall_median")
        ax.bar(s["method"], s["overall_median"], color="#4C78A8")
        ax.set_ylabel("track median 3D error (mm)")
        ax.tick_params(axis="x", rotation=60, labelsize=8)
        fig.tight_layout()
        fig.savefig(FIGURES / "r2_alignment_comparison_bar.png", dpi=220)
        plt.close(fig)
    write_report(REPORTS / "TASK_R2_ALIGNMENT.md", "Task R2 - Alignment Comparison", summary, "Method E is the current anchor-bridge plus existing beta evaluation.")
    best = min(summary, key=lambda r: r["overall_median"])
    return {"key_finding": f"best {best['method']} {best['overall_median']:.1f} mm", "rows": len(rows)}


def load_range_cube(samples: pd.DataFrame) -> pd.DataFrame:
    ranges = load_ranges_long()
    keys = samples[["capture_id", "tag", "sweep", "uwb_time_s"]].drop_duplicates()
    merged = ranges.merge(keys, on=["capture_id", "tag", "sweep"], how="inner")
    return merged


def dtag_estimates(ctx: dict[str, Any], samples: pd.DataFrame, ranges: pd.DataFrame) -> dict[str, float]:
    anchors = np.asarray(ctx["anchors_vicon"], dtype=float)
    delays = np.asarray([ctx["inputs"]["delays_v5"][i] for i in range(8)], dtype=float)
    samp_idx = samples.set_index(["capture_id", "tag", "sweep"])
    out = {}
    for tag, g in ranges.groupby("tag"):
        vals = []
        for r in g.itertuples():
            key = (r.capture_id, r.tag, int(r.sweep))
            if key not in samp_idx.index:
                continue
            s = samp_idx.loc[key]
            truth = np.asarray([s.truth_x, s.truth_y, s.truth_z], dtype=float)
            aid = int(r.anchor_id)
            vals.append(float(r.range_measured_mm) - np.linalg.norm(truth - anchors[aid]) - delays[aid])
        out[tag] = float(np.nanmedian(vals)) if vals else LOO_DTAG_MM
    return out


def project_rigid_pair(p1: np.ndarray, p2: np.ndarray, baseline: float = 120.0) -> tuple[np.ndarray, np.ndarray, float]:
    c = 0.5 * (p1 + p2)
    v = p1 - p2
    v[1] = 0.0
    n = np.linalg.norm(v)
    if n < 1e-6:
        u = np.array([1.0, 0.0, 0.0])
    else:
        u = v / n
    return c + 0.5 * baseline * u, c - 0.5 * baseline * u, n


def task_r3(ctx: dict[str, Any], samples: pd.DataFrame) -> dict[str, Any]:
    ranges = load_range_cube(samples)
    dtag = dtag_estimates(ctx, samples, ranges)
    write_csv(TABLES / "r3_estimated_dtag.csv", [{"tag": k, "d_tag_estimated_mm": v, "method": "vicon_truth_range_residual"} for k, v in dtag.items()])
    rows = []
    base_rows = []
    pair_rows = []
    for cid, gcap in samples.groupby("capture_id"):
        piv = {tag: gcap[gcap["tag"] == tag].set_index("sweep").sort_index() for tag in ROTO_TAGS}
        common = sorted(set(piv[ROTO_TAGS[0]].index).intersection(set(piv[ROTO_TAGS[1]].index)))
        for sw in common:
            a = piv[ROTO_TAGS[0]].loc[sw]
            b = piv[ROTO_TAGS[1]].loc[sw]
            p1 = np.asarray([a.x, a.y, a.z], dtype=float)
            p2 = np.asarray([b.x, b.y, b.z], dtype=float)
            q1, q2, base = project_rigid_pair(p1, p2, 120.0)
            t1 = np.asarray([a.truth_x, a.truth_y, a.truth_z], dtype=float)
            t2 = np.asarray([b.truth_x, b.truth_y, b.truth_z], dtype=float)
            base_rows.append({"capture_id": cid, "frame": int(sw), "baseline_mm": float(np.linalg.norm(p1 - p2)), "deviation_from_120": float(np.linalg.norm(p1 - p2) - 120.0)})
            pair_rows.append({"capture_id": cid, "tag": ROTO_TAGS[0], "method": "joint_projection", "err3d_mm": float(np.linalg.norm(q1 - t1))})
            pair_rows.append({"capture_id": cid, "tag": ROTO_TAGS[1], "method": "joint_projection", "err3d_mm": float(np.linalg.norm(q2 - t2))})
            pair_rows.append({"capture_id": cid, "tag": ROTO_TAGS[0], "method": "independent", "err3d_mm": float(a.err3d_mm)})
            pair_rows.append({"capture_id": cid, "tag": ROTO_TAGS[1], "method": "independent", "err3d_mm": float(b.err3d_mm)})
    df = pd.DataFrame(pair_rows)
    for (method, tag), g in df.groupby(["method", "tag"]):
        rows.append({"capture_id": "ALL", "method": method, "tag": tag, "median_3d": pct(g["err3d_mm"], 50), "rmse": rmse(g["err3d_mm"])})
    summary = []
    bdf = pd.DataFrame(base_rows)
    for method, g in df.groupby("method"):
        summary.append({"method": method, "overall_median": pct(g["err3d_mm"], 50), "overall_rmse": rmse(g["err3d_mm"]), "baseline_error_mm": pct(np.abs(bdf["deviation_from_120"]), 50)})
    write_csv(TABLES / "r3_joint_solver_results.csv", rows)
    write_csv(TABLES / "r3_joint_summary.csv", summary)
    write_csv(TABLES / "r3_baseline_length.csv", base_rows)
    if plt is not None:
        fig, ax = plt.subplots(figsize=(5, 4))
        for method, g in df.groupby("method"):
            vals = np.sort(g["err3d_mm"].to_numpy(float))
            ax.plot(vals, np.linspace(0, 1, len(vals)), label=method)
        ax.set_xlabel("3D error (mm)")
        ax.set_ylabel("CDF")
        ax.legend()
        fig.tight_layout()
        fig.savefig(FIGURES / "r3_joint_vs_independent_cdf.png", dpi=220)
        plt.close(fig)
        fig, ax = plt.subplots(figsize=(7, 3))
        for cid, g in bdf.groupby("capture_id"):
            ax.plot(g["frame"], g["baseline_mm"], alpha=0.2)
        ax.axhline(120.0, color="k", ls="--")
        ax.set_ylabel("independent baseline (mm)")
        ax.set_xlabel("sweep")
        fig.tight_layout()
        fig.savefig(FIGURES / "r3_baseline_length_over_time.png", dpi=220)
        plt.close(fig)
    write_report(REPORTS / "TASK_R3_RIGID_BODY.md", "Task R3 - Two-Tag Rigid Body", summary, "Implemented bounded rigid projection and Vicon-truth D_tag estimation; not a full global scipy range-level optimizer.")
    best = min(summary, key=lambda r: r["overall_median"])
    return {"key_finding": f"{best['method']} {best['overall_median']:.1f} mm", "rows": len(pair_rows)}


def task_r4(ctx: dict[str, Any], samples: pd.DataFrame) -> dict[str, Any]:
    r1 = pd.read_csv(TABLES / "r1_time_corrected_results.csv") if (TABLES / "r1_time_corrected_results.csv").exists() else pd.DataFrame()
    r3 = pd.read_csv(TABLES / "r3_estimated_dtag.csv") if (TABLES / "r3_estimated_dtag.csv").exists() else pd.DataFrame()
    dyn_med = pct(samples.groupby(["capture_id", "tag"])["err3d_mm"].median(), 50)
    static_best = 56.011
    gap = dyn_med - static_best
    dtag_comp = pct(np.abs(r3["d_tag_estimated_mm"] - LOO_DTAG_MM), 50) if not r3.empty else float("nan")
    time_comp = pct(r1["median_3d_before"] - r1["median_3d_after"], 50) if not r1.empty else float("nan")
    # Motion proxy from truth speed.
    speeds = []
    for (_cid, _tag), g in samples.groupby(["capture_id", "tag"]):
        g = g.sort_values("uwb_time_s")
        xyz = g[["truth_x", "truth_y", "truth_z"]].to_numpy(float)
        t = g["uwb_time_s"].to_numpy(float)
        if len(t) > 3:
            v = np.linalg.norm(np.gradient(xyz, axis=0), axis=1) / np.maximum(np.gradient(t), 1e-3)
            speeds.extend(v.tolist())
    motion_comp = pct(speeds, 50) * 0.010 if speeds else float("nan")  # 10 ms nominal poll window.
    range_agg = max(0.0, dyn_med - 101.5) if np.isfinite(dyn_med) else float("nan")
    components = [
        {"component": "D_tag mismatch", "estimated_mm": dtag_comp, "method": "median |ROTO Dtag_est - static 49.621|", "notes": "Upper-bound proxy, not orthogonal contribution."},
        {"component": "Motion blur", "estimated_mm": motion_comp, "method": "median Vicon speed * 10 ms", "notes": "Uses nominal poll window."},
        {"component": "Time alignment recoverable", "estimated_mm": time_comp, "method": "R1 before-after median improvement", "notes": "Recoverable portion from offset sweep."},
        {"component": "Range aggregation / dynamic single-frame", "estimated_mm": range_agg, "method": "dynamic median relative to 101.5 reference", "notes": "Proxy only; static subsampling not rerun here."},
    ]
    explained = sum(float(c["estimated_mm"]) for c in components if np.isfinite(float(c["estimated_mm"])))
    components.append({"component": "Unexplained", "estimated_mm": float(gap - explained), "method": "dynamic-static gap minus listed proxies", "notes": f"gap={gap:.1f} mm"})
    components.append({"component": "TOTAL static-to-dynamic gap", "estimated_mm": float(gap), "method": "median dynamic track error - static best", "notes": f"dynamic={dyn_med:.1f}, static={static_best:.1f}"})
    write_csv(TABLES / "r4_gap_decomposition.csv", components)
    write_csv(TABLES / "r4_subsample_static.csv", [{"n_samples": 1, "median_3d": float("nan"), "comparison": "not rerun; see notes"}, {"n_samples": 1200, "median_3d": static_best, "comparison": "static best-practice"}])
    if plt is not None:
        fig, ax = plt.subplots(figsize=(6, 3.5))
        plot_rows = components[:-1]
        ax.bar([r["component"] for r in plot_rows], [r["estimated_mm"] for r in plot_rows], color="#4C78A8")
        ax.tick_params(axis="x", rotation=55, labelsize=8)
        ax.set_ylabel("estimated contribution (mm)")
        fig.tight_layout()
        fig.savefig(FIGURES / "r4_gap_waterfall.png", dpi=220)
        plt.close(fig)
    write_report(REPORTS / "TASK_R4_GAP_DECOMPOSITION.md", "Task R4 - Static-Dynamic Gap", components, "Components are first-order proxies and are not independent additive causal terms.")
    return {"key_finding": f"gap {gap:.1f} mm", "rows": len(components)}


def weighted_gn(pos0: np.ndarray, ranges: dict[int, float], anchors: np.ndarray, delays: np.ndarray, dtag: float, weights: dict[int, float]) -> np.ndarray:
    x = np.asarray(pos0, dtype=float).copy()
    for _ in range(8):
        hs = []
        rs = []
        ws = []
        for aid, rng in ranges.items():
            a = anchors[aid]
            diff = x - a
            dist = max(float(np.linalg.norm(diff)), 1e-6)
            pred = dist + delays[aid] + dtag
            hs.append(diff / dist)
            rs.append(float(rng) - pred)
            ws.append(float(weights.get(aid, 1.0)))
        if len(hs) < 4:
            break
        h = np.asarray(hs)
        r = np.asarray(rs)
        w = np.asarray(ws)
        lhs = h.T @ (w[:, None] * h) + np.eye(3) * 1e-3
        rhs = h.T @ (w * r)
        dx = np.linalg.solve(lhs, rhs)
        dx = np.clip(dx, -250, 250)
        x = x + dx
        if np.linalg.norm(dx) < 1e-3:
            break
    return x


def task_r5(ctx: dict[str, Any], samples: pd.DataFrame) -> dict[str, Any]:
    ranges = load_range_cube(samples)
    anchors = np.asarray(ctx["anchors_vicon"], dtype=float)
    delays = np.asarray([ctx["inputs"]["delays_v5"][i] for i in range(8)], dtype=float)
    sample_idx = samples.set_index(["capture_id", "tag", "sweep"])
    feature_rows = []
    result_rows = []
    solved_rows = {"uniform": [], "soft_nlos": [], "hard_reject": []}
    for (cid, tag, aid), g in ranges.groupby(["capture_id", "tag", "anchor_id"]):
        g = g.sort_values("sweep")
        vals = g["range_measured_mm"].to_numpy(float)
        roll = pd.Series(vals).rolling(20, min_periods=5)
        std = roll.std().fillna(np.nanmedian(pd.Series(vals).rolling(5, min_periods=1).std())).to_numpy(float)
        med_std = np.nanmedian(std)
        for row, st in zip(g.itertuples(), std):
            p_nlos = float(1.0 / (1.0 + math.exp(-(st - med_std) / max(med_std, 1.0))))
            feature_rows.append({"capture_id": cid, "tag": tag, "frame": int(row.sweep), "anchor_id": int(aid), "rolling_std": float(st), "p_nlos": p_nlos})
    feat = pd.DataFrame(feature_rows)
    feat_map = {(r.capture_id, r.tag, int(r.frame), int(r.anchor_id)): float(r.p_nlos) for r in feat.itertuples()}
    by_frame = ranges.groupby(["capture_id", "tag", "sweep"])
    for key, g in by_frame:
        if key not in sample_idx.index:
            continue
        s = sample_idx.loc[key]
        pos0 = np.asarray([s.x, s.y, s.z], dtype=float)
        truth = np.asarray([s.truth_x, s.truth_y, s.truth_z], dtype=float)
        rr = {int(r.anchor_id): float(r.range_measured_mm) for r in g.itertuples()}
        if len(rr) < 4:
            continue
        weights_soft = {aid: max(0.05, 1.0 - feat_map.get((key[0], key[1], int(key[2]), aid), 0.0)) for aid in rr}
        keep = {aid: w for aid, w in weights_soft.items() if w >= 0.30}
        pos_u = weighted_gn(pos0, rr, anchors, delays, LOO_DTAG_MM, {aid: 1.0 for aid in rr})
        pos_s = weighted_gn(pos0, rr, anchors, delays, LOO_DTAG_MM, weights_soft)
        pos_h = weighted_gn(pos0, {aid: rr[aid] for aid in keep} if len(keep) >= 4 else rr, anchors, delays, LOO_DTAG_MM, {aid: 1.0 for aid in keep} if len(keep) >= 4 else {aid: 1.0 for aid in rr})
        solved_rows["uniform"].append(float(np.linalg.norm(pos_u - truth)))
        solved_rows["soft_nlos"].append(float(np.linalg.norm(pos_s - truth)))
        solved_rows["hard_reject"].append(float(np.linalg.norm(pos_h - truth)))
    for method, vals in solved_rows.items():
        result_rows.append({"method": method, "median_3d": pct(vals, 50), "p95": pct(vals, 95), "rmse": rmse(vals), "n_frames": len(vals)})
    write_csv(TABLES / "r5_nlos_dynamic_features.csv", feature_rows[:20000])
    write_csv(TABLES / "r5_nlos_dynamic_results.csv", result_rows)
    if plt is not None:
        fig, ax = plt.subplots(figsize=(5, 4))
        for method, vals in solved_rows.items():
            arr = np.sort(np.asarray(vals, dtype=float))
            ax.plot(arr, np.linspace(0, 1, len(arr)), label=method)
        ax.set_xlabel("3D error (mm)")
        ax.set_ylabel("CDF")
        ax.legend()
        fig.tight_layout()
        fig.savefig(FIGURES / "r5_nlos_dynamic_cdf.png", dpi=220)
        plt.close(fig)
    write_report(REPORTS / "TASK_R5_NLOS_DYNAMIC.md", "Task R5 - Dynamic NLOS Solver", result_rows, "Uses rolling range-std NLOS proxy; not the static MLP because dynamic raw windows have different feature support.")
    best = min(result_rows, key=lambda r: r["median_3d"])
    return {"key_finding": f"{best['method']} {best['median_3d']:.1f} mm", "rows": len(feature_rows)}


def task_r6(ctx: dict[str, Any], samples: pd.DataFrame) -> dict[str, Any]:
    ranges = load_range_cube(samples)
    rho_by = ranges.groupby(["capture_id", "tag", "sweep", "anchor_label"])["rho_mm"].mean().reset_index()
    rows = []
    for (cid, tag), g in samples.groupby(["capture_id", "tag"]):
        g = g.sort_values("uwb_time_s")
        truth = g[["truth_x", "truth_y", "truth_z"]].to_numpy(float)
        circle = ctx["full"].fit_circle_3d(truth)
        if circle.get("status") != "ok":
            continue
        c = np.asarray([circle["center_x_mm"], circle["center_y_mm"], circle["center_z_mm"]], dtype=float)
        rel = truth - c
        phase = (np.degrees(np.arctan2(rel[:, 2], rel[:, 0])) + 360.0) % 360.0
        radial_truth = np.linalg.norm(rel[:, [0, 2]], axis=1)
        uwb = g[["x", "y", "z"]].to_numpy(float)
        radial_uwb = np.linalg.norm((uwb - c)[:, [0, 2]], axis=1)
        tmp = g.copy()
        tmp["sector_deg"] = (np.floor(phase / 30.0) * 30.0).astype(int)
        tmp["signed_radial"] = radial_uwb - radial_truth
        for sector, sg in tmp.groupby("sector_deg"):
            rg = rho_by[(rho_by["capture_id"] == cid) & (rho_by["tag"] == tag) & (rho_by["sweep"].isin(sg["sweep"]))]
            worst = ""
            if not rg.empty:
                worst = str(rg.groupby("anchor_label")["rho_mm"].apply(lambda x: np.nanmedian(np.abs(x))).sort_values(ascending=False).index[0])
            rows.append({"capture_id": cid, "tag": tag, "sector_deg": int(sector), "n_frames": int(len(sg)), "median_3d": pct(sg["err3d_mm"], 50), "mean_signed_radial": float(np.nanmean(sg["signed_radial"])), "worst_anchor": worst})
    df = pd.DataFrame(rows)
    agg = []
    for sector, g in df.groupby("sector_deg"):
        worst = g["worst_anchor"].mode().iloc[0] if not g["worst_anchor"].mode().empty else ""
        agg.append({"sector_deg": int(sector), "mean_median_3d": float(g["median_3d"].mean()), "worst_anchor": worst})
    write_csv(TABLES / "r6_phase_error.csv", rows)
    write_csv(TABLES / "r6_phase_aggregate.csv", agg)
    if plt is not None and agg:
        adf = pd.DataFrame(agg).sort_values("sector_deg")
        theta = np.deg2rad(adf["sector_deg"].to_numpy(float) + 15.0)
        rad = adf["mean_median_3d"].to_numpy(float)
        fig = plt.figure(figsize=(5, 5))
        ax = fig.add_subplot(111, projection="polar")
        ax.bar(theta, rad, width=np.deg2rad(28), alpha=0.8)
        fig.tight_layout()
        fig.savefig(FIGURES / "r6_polar_error.png", dpi=220)
        plt.close(fig)
    write_report(REPORTS / "TASK_R6_PHASE_MAP.md", "Task R6 - Rotation Phase Error", agg, "Phase is computed in the horizontal XZ plane from Vicon marker trajectories.")
    worst = max(agg, key=lambda r: r["mean_median_3d"]) if agg else {}
    return {"key_finding": f"worst sector {worst.get('sector_deg', 'NA')} deg", "rows": len(rows)}


def verify_script() -> None:
    src = (SCRIPTS / "run_roto_deepdive.py").read_text(encoding="utf-8")
    compile(src, str(SCRIPTS / "run_roto_deepdive.py"), "exec")
    tree = ast.parse(src)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    write_json(REPORTS / "SCRIPT_VERIFICATION.json", {"compiles": True, "gpu_related_imports": [x for x in imports if any(k in x.lower() for k in ["torch", "cupy", "cuda"])]})


def write_completion(statuses: list[dict[str, Any]]) -> None:
    rows = [{"task": s.get("task"), "status": s.get("status"), "elapsed_s": s.get("elapsed_s", float("nan")), "key_finding": s.get("key_finding", s.get("error", "")), "mean_cpu_percent": s.get("mean_cpu_percent", float("nan"))} for s in statuses]
    write_csv(TABLES / "roto_deepdive_task_status_summary.csv", rows)
    lines = ["# ROTO Deep-Dive Completion\n\n", f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n"]
    lines.append("| task | status | elapsed_s | key_finding |\n| --- | --- | --- | --- |\n")
    for r in rows:
        elapsed = r["elapsed_s"]
        lines.append(f"| {r['task']} | {r['status']} | {'' if not np.isfinite(float(elapsed)) else f'{float(elapsed):.1f}'} | {r['key_finding']} |\n")
    lines.append("\n## Recommended Dynamic Tracking Pipeline\n\nUse the existing V5 D_LOO per-frame solver as the conservative baseline. Treat time-offset tuning and rigid two-tag projection as diagnostics until hardware time sync and a true range-level rigid-body solver are validated.\n")
    (REPORTS / "ROTO_DEEPDIVE_COMPLETION.md").write_text("".join(lines), encoding="utf-8")


def main() -> int:
    ensure_dirs()
    verify_script()
    ctx = build_context()
    samples = solve_cache(ctx)
    statuses = [
        run_task("R1", lambda: task_r1(ctx, samples)),
        run_task("R2", lambda: task_r2(ctx, samples)),
        run_task("R3", lambda: task_r3(ctx, samples)),
        run_task("R4", lambda: task_r4(ctx, samples)),
        run_task("R5", lambda: task_r5(ctx, samples)),
        run_task("R6", lambda: task_r6(ctx, samples)),
    ]
    write_completion(statuses)
    print(f"Completion report: {REPORTS / 'ROTO_DEEPDIVE_COMPLETION.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
