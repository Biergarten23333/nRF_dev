#!/usr/bin/env python3
"""Raw-frame brute-force v2.

This v2 run adds an explicit raw-data discovery gate and uses PyTorch/CUDA for
the frame-level B2 solver. B0/B1/B5/B6 reuse the prior implementation after
retargeting its output globals to this v2 directory.
"""

from __future__ import annotations

import importlib.util
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import numpy as np
import pandas as pd
import torch

BASE = Path("/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official")
ANALYSIS = BASE / "Analysis" / "official_extra_analysis"
OUT = ANALYSIS / "FULL_V5_rawframe_bruteforce_v2"
SCRIPT_DIR = OUT / "scripts"
TABLE_DIR = OUT / "tables"
FIG_DIR = OUT / "figures"
CACHE_DIR = OUT / "cache"
REPORT_DIR = OUT / "reports"
CAPTURE_ROOT = BASE / "captures" / "erlangen_20260528_optitrack"
PREV_SCRIPT = ANALYSIS / "FULL_V5_rawframe_bruteforce" / "scripts" / "run_rawframe_bruteforce.py"
ANCHORS = list("ABCDEFGH")
PRIMARY_IDS = [f"ID{i:02d}" for i in range(1, 25)]


def load_prev_module():
    if not PREV_SCRIPT.exists():
        raise FileNotFoundError(f"previous raw-frame script not found: {PREV_SCRIPT}")
    spec = importlib.util.spec_from_file_location("rawframe_v1", PREV_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rawframe_v1"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.OUT = OUT
    mod.SCRIPT_DIR = SCRIPT_DIR
    mod.TABLE_DIR = TABLE_DIR
    mod.FIG_DIR = FIG_DIR
    mod.CACHE_DIR = CACHE_DIR
    mod.REPORT_DIR = REPORT_DIR
    return mod


def ensure_dirs() -> None:
    for d in [SCRIPT_DIR, TABLE_DIR, FIG_DIR, CACHE_DIR, REPORT_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def sid_from_path(path: Path) -> str:
    m = re.search(r"static_(ID\d+)", str(path))
    if not m:
        raise ValueError(f"cannot extract static ID from {path}")
    return m.group(1)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


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


def write_report(name: str, lines: list[str]) -> None:
    (REPORT_DIR / name).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def discover_raw_data() -> dict[str, Any]:
    files = sorted(CAPTURE_ROOT.glob("static_ID*/tag_capture*/tr_all.csv"), key=lambda p: int(sid_from_path(p)[2:]))
    if len(files) != 24:
        raise FileNotFoundError(f"expected 24 static tr_all.csv files under {CAPTURE_ROOT}, found {len(files)}")
    rows = []
    first_schema = None
    for path in files:
        sid = sid_from_path(path)
        df = pd.read_csv(path)
        if first_schema is None:
            first_schema = {
                "path": str(path),
                "shape": str(df.shape),
                "columns": ", ".join(df.columns),
                "head": df.head().to_string(index=False),
                "anchor_counts": str(df.groupby("anchor_id").size().to_dict()) if "anchor_id" in df else "",
                "valid_counts": str(df["valid"].value_counts(dropna=False).to_dict()) if "valid" in df else "",
            }
        n_rows = int(len(df))
        n_frames = int(df["sweep"].nunique()) if "sweep" in df else 0
        n_anchors = int(df["anchor_id"].nunique()) if "anchor_id" in df else 0
        n_valid = int(df["valid"].astype(bool).sum()) if "valid" in df else n_rows
        min_link = int(df.groupby("anchor_id").size().min()) if "anchor_id" in df else 0
        max_link = int(df.groupby("anchor_id").size().max()) if "anchor_id" in df else 0
        rows.append(
            {
                "capture_id": sid,
                "file_path": str(path),
                "n_rows": n_rows,
                "n_valid_rows": n_valid,
                "n_frames": n_frames,
                "n_anchors": n_anchors,
                "min_rows_per_anchor": min_link,
                "max_rows_per_anchor": max_link,
                "data_format": "one row per frame-anchor observation",
            }
        )
    inv = pd.DataFrame(rows)
    write_csv(inv, TABLE_DIR / "raw_data_inventory.csv")
    expected = 24 * 8 * 1200
    total_rows = int(inv["n_rows"].sum())
    total_valid = int(inv["n_valid_rows"].sum())
    ratio = total_rows / expected
    first = first_schema or {}
    lines = [
        "# Data Discovery",
        "",
        f"Generated: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
        "",
        "## Verdict",
        "",
        "Raw per-frame range data WAS found. The static captures use `tr_all.csv` with one row per sweep-anchor observation.",
        "",
        f"- Static files found: {len(files)}",
        f"- Total raw rows: {total_rows}",
        f"- Total valid rows: {total_valid}",
        f"- Expected rows, 24 x 8 x 1200: {expected}",
        f"- Ratio to expected: {ratio:.3f}",
        "",
        "## First Capture Structure",
        "",
        f"- File: `{first.get('path', '')}`",
        f"- Shape: `{first.get('shape', '')}`",
        f"- Anchor counts: `{first.get('anchor_counts', '')}`",
        f"- Valid counts: `{first.get('valid_counts', '')}`",
        "",
        "Columns:",
        "",
        f"`{first.get('columns', '')}`",
        "",
        "Head:",
        "",
        "```text",
        first.get("head", ""),
        "```",
        "",
        "The previous v1 output tables with 192 rows are per-link feature tables, not proof that raw frames were absent. v2 still reruns B2 with PyTorch/CUDA as requested.",
    ]
    write_report("DATA_DISCOVERY.md", lines)
    return {"files": files, "inventory": inv, "total_rows": total_rows, "total_valid_rows": total_valid, "ratio": ratio}


def build_torch_cache(ctx: Any, device: torch.device) -> dict[str, dict[str, torch.Tensor]]:
    coords = torch.tensor(np.vstack([ctx.coords_v5[a] for a in ANCHORS]), dtype=torch.float32, device=device)
    delays = torch.tensor([ctx.delays_v5[a] for a in ANCHORS], dtype=torch.float32, device=device)
    cache = {"_coords": coords, "_delays": delays}
    for sid in PRIMARY_IDS:
        obs_parts = []
        idx_parts = []
        weight_parts = []
        for aid in range(8):
            x = np.asarray(ctx.raw_ranges[(sid, aid)], dtype=np.float32)
            n_eff = float(ctx.link_neff[(sid, aid)])
            w = math.sqrt(max(1.0, n_eff) / max(1, x.size))
            obs_parts.append(x)
            idx_parts.append(np.full(x.size, aid, dtype=np.int64))
            weight_parts.append(np.full(x.size, w, dtype=np.float32))
        cache[sid] = {
            "obs": torch.tensor(np.concatenate(obs_parts), dtype=torch.float32, device=device),
            "anchor_idx": torch.tensor(np.concatenate(idx_parts), dtype=torch.long, device=device),
            "weights": torch.tensor(np.concatenate(weight_parts), dtype=torch.float32, device=device),
        }
    return cache


def torch_loss(residual: torch.Tensor, loss_name: str) -> torch.Tensor:
    if loss_name == "l2":
        return torch.mean(residual * residual)
    if loss_name == "huber":
        delta = 50.0
        abs_r = torch.abs(residual)
        return torch.mean(torch.where(abs_r <= delta, 0.5 * residual * residual, delta * (abs_r - 0.5 * delta)))
    if loss_name == "student_t":
        nu = 3.0
        scale = 50.0
        return torch.mean(torch.log1p((residual / scale) ** 2 / nu))
    if loss_name == "asymmetric":
        adjusted = torch.where(residual > 0, residual * 0.45, residual)
        return torch.mean(adjusted * adjusted)
    raise ValueError(loss_name)


def solve_torch_position(sid: str, dtag_mm: float, ctx: Any, cache: dict[str, dict[str, torch.Tensor]], loss_name: str) -> dict[str, Any]:
    coords = cache["_coords"]
    delays = cache["_delays"]
    obs = cache[sid]["obs"]
    idx = cache[sid]["anchor_idx"]
    weights = cache[sid]["weights"]
    x0 = np.vstack([ctx.coords_v5[a] for a in ANCHORS]).mean(axis=0).astype(np.float32)
    p = torch.tensor(x0, dtype=torch.float32, device=obs.device, requires_grad=True)
    dtag = torch.tensor(float(dtag_mm), dtype=torch.float32, device=obs.device)
    opt = torch.optim.LBFGS([p], lr=1.0, max_iter=80, tolerance_grad=1e-5, tolerance_change=1e-6, line_search_fn="strong_wolfe")

    def closure():
        opt.zero_grad(set_to_none=True)
        pred = torch.linalg.norm(p.unsqueeze(0) - coords[idx], dim=1) + delays[idx] + dtag
        residual = (obs - pred) * weights
        loss = torch_loss(residual, loss_name)
        loss.backward()
        return loss

    ok = True
    notes = "torch_lbfgs"
    try:
        opt.step(closure)
    except Exception as exc:
        ok = False
        notes = repr(exc)
    with torch.no_grad():
        est = p.detach().cpu().numpy().astype(float)
    e = est - ctx.tag_truth[sid]
    return {
        "ok": ok,
        "position_id": sid,
        "x_mm": est[0],
        "y_mm": est[1],
        "z_mm": est[2],
        "error_3d_mm": float(np.linalg.norm(e)),
        "error_horiz_mm": float(np.linalg.norm(e[[0, 2]])),
        "error_vert_mm": float(abs(e[1])),
        "signed_vertical_mm": float(e[1]),
        "n_ranges": int(obs.numel()),
        "backend": str(obs.device),
        "notes": notes,
    }


def run_b2_torch(rf: Any, ctx: Any) -> dict[str, Any]:
    t0 = time.time()
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    cache = build_torch_cache(ctx, device)
    splits = rf.make_splits(ctx)
    losses = ["l2", "huber", "student_t", "asymmetric"]
    details = []
    summaries = []
    for loss_name in losses:
        for split in splits:
            dtag = rf.fit_dtag_from_raw_frames(split["train"], ctx.coords_v5, ctx.delays_v5, ctx)
            rows = []
            for sid in split["eval"]:
                row = solve_torch_position(sid, dtag, ctx, cache, loss_name)
                row.update(
                    {
                        "loss": loss_name,
                        "split_family": split["family"],
                        "split": split["split"],
                        "evidence_label": split["label"],
                        "dtag_fit_mm": dtag,
                    }
                )
                details.append(row)
                rows.append(row)
            summary = rf.aggregate_errors(rows)
            summary.update(
                {
                    "loss": loss_name,
                    "split_family": split["family"],
                    "split": split["split"],
                    "evidence_label": split["label"],
                    "dtag_fit_mm": dtag,
                    "backend": str(device),
                }
            )
            summaries.append(summary)
    detail_df = pd.DataFrame(details)
    summary_df = pd.DataFrame(summaries)
    write_csv(detail_df, TABLE_DIR / "b2_frame_level_per_position.csv")
    write_csv(summary_df, TABLE_DIR / "b2_frame_level_results_by_split.csv")
    loo_rows = []
    for loss, group in detail_df[detail_df["split_family"] == "loo_position"].groupby("loss"):
        metrics = rf.aggregate_errors(group.to_dict("records"))
        metrics.update(
            {
                "loss": loss,
                "split_family": "loo_position",
                "split": "ALL_LOO",
                "evidence_label": "HELD-OUT",
                "dtag_fit_mm": float(group["dtag_fit_mm"].mean()),
                "backend": str(device),
            }
        )
        loo_rows.append(metrics)
    loo_df = pd.DataFrame(loo_rows)
    write_csv(loo_df, TABLE_DIR / "b2_loo_summary.csv")
    rf.make_b2_figures(loo_df)
    best = None if loo_df.empty else loo_df.sort_values("median_3d_mm").iloc[0].to_dict()
    lines = [
        "# B2 Frame-Level Robust Solver, PyTorch/CUDA",
        "",
        f"Generated: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
        "",
        f"Backend: `{device}`",
        "The loss is evaluated over the raw frame-anchor observations, with per-link weights `sqrt(N_eff / N_actual)`.",
        "",
    ]
    if best:
        lines.append(f"Best B2 LOO loss: `{best['loss']}` at {best['median_3d_mm']:.3f} mm median 3D.")
    write_report("TASK_B2_FRAME_SOLVER.md", lines)
    return {"best": best, "loo": loo_df, "elapsed_s": time.time() - t0, "backend": str(device)}


def raw_loading_checkpoint(ctx: Any) -> dict[str, Any]:
    x = np.asarray(ctx.raw_ranges[("ID01", 0)], dtype=float)
    row = {
        "link": "ID01_anchor_A",
        "n_frames_loaded": int(x.size),
        "min_mm": float(np.min(x)),
        "p05_mm": float(np.percentile(x, 5)),
        "median_mm": float(np.median(x)),
        "p95_mm": float(np.percentile(x, 95)),
        "max_mm": float(np.max(x)),
        "status": "OK_RAW_FRAMES" if x.size > 100 else "ERROR_AGGREGATED",
    }
    write_csv(pd.DataFrame([row]), TABLE_DIR / "raw_loading_checkpoint.csv")
    print("VERIFICATION:")
    print(f"  Link (ID01, anchor_A): {row['n_frames_loaded']} raw ranges loaded")
    print(f"  Range values: min={row['min_mm']:.1f}, p05={row['p05_mm']:.1f}, median={row['median_mm']:.1f}, p95={row['p95_mm']:.1f}, max={row['max_mm']:.1f}")
    print(f"  Status: {row['status']}")
    if row["status"] != "OK_RAW_FRAMES":
        raise RuntimeError("still using aggregated data")
    return row


def write_row_counts() -> None:
    rows = []
    for path in sorted(TABLE_DIR.glob("*.csv")):
        try:
            n = len(pd.read_csv(path))
        except Exception:
            n = -1
        rows.append({"file": path.name, "rows": n})
    write_csv(pd.DataFrame(rows), TABLE_DIR / "output_row_counts.csv")


def main() -> None:
    ensure_dirs()
    start = time.time()
    rf = load_prev_module()
    rf.ensure_dirs()
    print("=== RAW-FRAME BRUTEFORCE V2: DISCOVERY + TORCH B2 ===")
    print(f"Output: {OUT}")
    status_rows = []
    discovery = discover_raw_data()
    ctx = rf.load_context()
    checkpoint = raw_loading_checkpoint(ctx)

    t = time.time()
    b0 = rf.run_b0(ctx)
    status_rows.append({"task": "B0", "status": "OK", "elapsed_s": time.time() - t, "error": ""})

    if b0["gate"] == "STOP_B1_B4":
        b1 = {"best": None, "loo": pd.DataFrame(), "elapsed_s": 0.0}
        b2 = {"best": None, "loo": pd.DataFrame(), "elapsed_s": 0.0, "backend": "not_run"}
        write_report("TASK_B1_LINK_ESTIMATORS.md", ["# B1 Per-Link LOS Estimators", "", "Gate-skipped because B0 oracle lower bound exceeded 50 mm."])
        write_report("TASK_B2_FRAME_SOLVER.md", ["# B2 Frame-Level Robust Solver", "", "Gate-skipped because B0 oracle lower bound exceeded 50 mm."])
    else:
        t = time.time()
        b1 = rf.run_b1(ctx, b0)
        status_rows.append({"task": "B1", "status": "OK", "elapsed_s": time.time() - t, "error": ""})
        t = time.time()
        b2 = run_b2_torch(rf, ctx)
        status_rows.append({"task": "B2_TORCH", "status": "OK", "elapsed_s": time.time() - t, "error": ""})

    t = time.time()
    b3b4 = rf.run_b3_b4_gate(b0, b1, b2)
    status_rows.append({"task": "B3_B4", "status": "OK", "elapsed_s": time.time() - t, "error": ""})
    t = time.time()
    b5 = rf.run_b5(ctx, b0, b1, b2)
    status_rows.append({"task": "B5", "status": "OK", "elapsed_s": time.time() - t, "error": ""})
    t = time.time()
    b6 = rf.run_b6(b0, b1, b2, b3b4, b5)
    status_rows.append({"task": "B6", "status": "OK", "elapsed_s": time.time() - t, "error": ""})

    verification = {
        "script": str(Path(__file__)),
        "syntax_compile": "PASS",
        "torch_version": torch.__version__,
        "torch_import": "PASS",
        "cuda_available": bool(torch.cuda.is_available()),
        "cuda_device_count": int(torch.cuda.device_count()),
        "b2_backend": b2.get("backend", ""),
        "raw_total_rows": int(discovery["total_rows"]),
        "raw_expected_ratio": float(discovery["ratio"]),
        "checkpoint_status": checkpoint["status"],
        "total_wall_s": time.time() - start,
    }
    write_csv(pd.DataFrame([verification]), TABLE_DIR / "verification.csv")
    write_csv(pd.DataFrame(status_rows), TABLE_DIR / "task_status.csv")
    write_row_counts()
    final = [
        "# Raw-Frame Brute-Force V2 Completion",
        "",
        f"Generated: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
        "",
        "## Data Discovery",
        "",
        f"- Raw per-frame data found: YES",
        f"- Total static raw rows: {discovery['total_rows']}",
        f"- Ratio to 230400 expected rows: {discovery['ratio']:.3f}",
        f"- ID01 anchor A frames loaded: {checkpoint['n_frames_loaded']}",
        "",
        "## Results",
        "",
        f"- B0 oracle median: {b0['summary']['oracle_lower_bound_median_3d_mm']:.3f} mm",
        f"- B1 best held-out: {b1['best']['estimator'] if b1.get('best') else 'none'} / {b1['best']['median_3d_mm'] if b1.get('best') else float('nan'):.3f} mm",
        f"- B2 PyTorch best held-out: {b2['best']['loss'] if b2.get('best') else 'none'} / {b2['best']['median_3d_mm'] if b2.get('best') else float('nan'):.3f} mm",
        f"- B2 backend: {b2.get('backend', '')}",
        f"- Achievement level: {b6.get('achievement_level', 'UNKNOWN')}",
        "",
        "## Task Status",
        "",
        md_table(pd.DataFrame(status_rows)),
        "",
        f"Total wall time: {time.time() - start:.3f} s",
    ]
    write_report("RAWFRAME_V2_COMPLETION.md", final)
    print("\n".join(final))


if __name__ == "__main__":
    main()
