#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

import numpy as np
import pandas as pd

THIS = Path(__file__).resolve()
FULL_ROOT = THIS.parents[1]
EXTRA_ROOT = THIS.parents[2]
OFFICIAL_ROOT = THIS.parents[4]
REPO_ROOT = THIS.parents[6]
ABLATION_SCRIPT = EXTRA_ROOT / "FULL_4way_comparison/scripts/run_static_layout_ablation.py"
LAYOUT_BASE = OFFICIAL_ROOT / "solver/outputs/v1_to_v4_io_field_check"
V5_LAYOUT = LAYOUT_BASE / "v5-commonmode/layout.json"
CAPTURES_ROOT = OFFICIAL_ROOT / "captures/erlangen_20260528_optitrack"
OPTI_ROOT = OFFICIAL_ROOT / "opti_captures/full"
ANCHORS = list("ABCDEFGH")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
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


def regression(x: np.ndarray, y: np.ndarray) -> dict[str, float]:
    mask = np.isfinite(x) & np.isfinite(y)
    xx = x[mask]
    yy = y[mask]
    if xx.size < 3 or float(np.nanstd(xx)) <= 1e-12:
        return {"n": int(xx.size), "slope_mm": float("nan"), "intercept_mm": float("nan"), "r2": float("nan")}
    slope, intercept = np.polyfit(xx, yy, 1)
    pred = slope * xx + intercept
    ss_res = float(np.sum((yy - pred) ** 2))
    ss_tot = float(np.sum((yy - np.mean(yy)) ** 2))
    return {
        "n": int(xx.size),
        "slope_mm": float(slope),
        "intercept_mm": float(intercept),
        "r2": float(1.0 - ss_res / ss_tot) if ss_tot > 0.0 else float("nan"),
    }


def geometry_gain(layout_coords: np.ndarray, point_layout: np.ndarray, fit) -> dict[str, float]:
    diff = point_layout[None, :] - layout_coords
    dist = np.linalg.norm(diff, axis=1)
    keep = dist > 1e-9
    u = diff[keep] / dist[keep, None]
    gram = u.T @ u
    g = np.linalg.pinv(gram) @ np.sum(u, axis=0)
    g_vicon = g @ fit.rotation
    return {
        "g_x_layout": float(g[0]),
        "g_y_layout": float(g[1]),
        "g_z_layout_vertical": float(g[2]),
        "g_x_vicon": float(g_vicon[0]),
        "g_y_vicon_vertical": float(g_vicon[1]),
        "g_z_vicon": float(g_vicon[2]),
        "geometry_cond": float(np.linalg.cond(gram)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute per-position g_z tag-delay bias sensitivity for V5 common-mode.")
    parser.add_argument("--out-dir", type=Path, default=FULL_ROOT)
    args = parser.parse_args()
    out_dir = args.out_dir.resolve()
    tables = out_dir / "tables"

    if not V5_LAYOUT.exists():
        raise FileNotFoundError(f"missing V5 layout; run S1 first: {V5_LAYOUT}")
    ablation = load_module(ABLATION_SCRIPT, "gz_static_ablation_helpers")
    labels, coords, delays, _tag_delay = ablation.load_layout_json_raw(V5_LAYOUT)
    by_label = {label: coords[i] for i, label in enumerate(labels)}
    src = np.vstack([by_label[a] for a in ANCHORS])
    anchor_truth, tag_truth, tag_truth_meta, _corr = ablation.load_corrected_static_truth(
        OPTI_ROOT,
        ANCHORS,
        ablation.PRIMARY_IDS,
    )
    truth_coords = np.vstack([anchor_truth[a] for a in ANCHORS])
    rigid = ablation.fit_similarity(src, truth_coords, allow_reflection=True, allow_scale=False)
    coords_vicon_rigid = ablation.apply_fit(src, rigid)
    sigma_by_id = ablation.load_anchor_sigma(LAYOUT_BASE / "tables/anchor_sigma.json")
    metadata_by_id = ablation.load_static_metadata(LAYOUT_BASE / "tables/static_all_captures.csv")
    layout = ablation.build_layout(
        name="v5_commonmode_rigid_vicon_zero_tag",
        labels=ANCHORS,
        coords_opti_frame=coords_vicon_rigid,
        delays=delays,
        tag_delay_mm=0.0,
        sigma_by_id=sigma_by_id,
        metadata={"version": "v5-commonmode", "case": "zero_tag"},
    )
    solver = ablation.TagPositionSolver(layout, ablation.SolverConfig(method="T4"))

    rows: list[dict[str, Any]] = []
    for path in sorted(CAPTURES_ROOT.glob("static_ID*/tag_capture*/tr_all.csv")):
        sid = ablation.session_id_from_path(path)
        row = ablation.solve_static_file_with_layout(
            path,
            layout=layout,
            solver=solver,
            tag_truth=tag_truth,
            tag_truth_meta=tag_truth_meta,
            metadata_by_id=metadata_by_id,
            metadata={"version": "v5-commonmode", "case": "zero_tag", "alignment": "rigid_v5_to_vicon"},
            tag_method="T4",
            point_estimator="mean",
        )
        if row is None or sid not in tag_truth:
            continue
        truth_layout = (tag_truth[sid] - rigid.translation) @ rigid.rotation.T
        gain = geometry_gain(src, truth_layout, rigid)
        row.update(gain)
        row["source_tr_all"] = str(path)
        row["layout_json"] = str(V5_LAYOUT)
        rows.append(row)

    df = pd.DataFrame(rows)
    reg_layout = regression(df["g_z_layout_vertical"].to_numpy(dtype=float), df["err_y_vertical_mm"].to_numpy(dtype=float))
    reg_vicon = regression(df["g_y_vicon_vertical"].to_numpy(dtype=float), df["err_y_vertical_mm"].to_numpy(dtype=float))
    summary = {
        "script": str(THIS),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "layout_json": str(V5_LAYOUT),
        "rows": int(len(rows)),
        "v5_zero_tag_median_3d_mm": float(np.nanmedian(df["err_3d_mm"].to_numpy(dtype=float))) if rows else float("nan"),
        "v5_zero_tag_rmse_3d_mm": float(math.sqrt(np.nanmean(df["err_3d_mm"].to_numpy(dtype=float) ** 2))) if rows else float("nan"),
        "v5_zero_tag_vertical_median_abs_mm": float(np.nanmedian(df["err_vertical_y_mm"].to_numpy(dtype=float))) if rows else float("nan"),
        "gz_layout_slope_missing_dtag_mm": reg_layout["slope_mm"],
        "gz_layout_intercept_mm": reg_layout["intercept_mm"],
        "gz_layout_r2": reg_layout["r2"],
        "gy_vicon_slope_missing_dtag_mm": reg_vicon["slope_mm"],
        "gy_vicon_intercept_mm": reg_vicon["intercept_mm"],
        "gy_vicon_r2": reg_vicon["r2"],
        "axis_note": "g_z_layout_vertical uses AutoPos layout z; g_y_vicon_vertical is the same vector rotated into Vicon Y for sign/axis sanity.",
    }
    write_csv(tables / "gz_bias_sensitivity_per_position.csv", rows)
    write_csv(tables / "gz_bias_sensitivity_summary.csv", [summary])
    print(json.dumps({"status": "ok", "summary": summary}, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
