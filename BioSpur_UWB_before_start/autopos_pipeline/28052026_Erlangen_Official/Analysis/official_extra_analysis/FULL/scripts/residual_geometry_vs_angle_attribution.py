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
COMPARISON_ROOT = EXTRA_ROOT / "FULL_4way_comparison"
ABLATION_SCRIPT = COMPARISON_ROOT / "scripts/run_static_layout_ablation.py"
LOO_SUMMARY = COMPARISON_ROOT / "tables/v5_loo_tag_delay_summary.csv"
SOLVER_ROOT = REPO_ROOT / "biospur_tag_positioning_offline_solver"
LAYOUT_BASE = OFFICIAL_ROOT / "solver/outputs/v1_to_v4_io_field_check"
V5_LAYOUT = LAYOUT_BASE / "v5-commonmode/layout.json"
CAPTURES_ROOT = OFFICIAL_ROOT / "captures/erlangen_20260528_optitrack"
OPTI_ROOT = OFFICIAL_ROOT / "opti_captures/full"
ANCHORS = list("ABCDEFGH")
STATIC_TAG = "BSF66F"

sys.path.insert(0, str(SOLVER_ROOT))
from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames  # noqa: E402


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


def session_id_from_path(path: Path) -> str:
    for parent in path.parents:
        if parent.name.startswith("static_ID"):
            return parent.name.split("_")[1]
    return path.parents[1].name


def precompute_static_median_ranges(static_files: list[Path]) -> tuple[dict[str, dict[int, float]], dict[str, dict[int, int]]]:
    medians: dict[str, dict[int, float]] = {}
    counts: dict[str, dict[int, int]] = {}
    for path in static_files:
        sid = session_id_from_path(path)
        frames = read_tr_all_frames(path, tags={STATIC_TAG}, min_anchors=4)
        by_anchor: dict[int, list[float]] = {aid: [] for aid in range(8)}
        for frame in frames:
            for obs in frame.observations:
                if 0 <= obs.anchor_id < 8 and obs.range_mm > 0.0:
                    by_anchor[obs.anchor_id].append(float(obs.range_mm))
        medians[sid] = {aid: float(np.nanmedian(vals)) for aid, vals in by_anchor.items() if vals}
        counts[sid] = {aid: len(vals) for aid, vals in by_anchor.items() if vals}
    return medians, counts


def loo_deployable_dtag() -> float:
    df = pd.read_csv(LOO_SUMMARY)
    row = df[(df["variant"] == "deployable_v5_rigid_selfcal") & (df["case"] == "loo_tag_delay")]
    if row.empty:
        raise RuntimeError(f"missing deployable LOO row in {LOO_SUMMARY}")
    return float(row.iloc[0]["d_tag_median_mm"])


def existing_physical_sim3_layout_path() -> str:
    if not LOO_SUMMARY.exists():
        return ""
    df = pd.read_csv(LOO_SUMMARY)
    if "fold_layout_dir" not in df.columns:
        return ""
    paths = [Path(p) for p in df["fold_layout_dir"].dropna().astype(str).unique()]
    for root in paths:
        if not root.exists():
            continue
        hits = sorted(root.glob("*_physical_sim3_vicon_delaycal_loo_tag_delay.json"))
        if hits:
            return str(hits[0])
    return ""


def ols(y: np.ndarray, columns: list[tuple[str, np.ndarray]]) -> dict[str, Any]:
    mask = np.isfinite(y)
    for _, x in columns:
        mask &= np.isfinite(x)
    yy = y[mask].astype(float)
    names = ["intercept"] + [name for name, _ in columns]
    if yy.size <= len(columns) + 1:
        return {
            "model": "+".join(name for name, _ in columns),
            "n": int(yy.size),
            "r2": float("nan"),
            "sse": float("nan"),
            "coefficients_json": "{}",
            "p_values_json": "{}",
        }
    xcols = [np.ones(yy.size, dtype=float)] + [x[mask].astype(float) for _, x in columns]
    xmat = np.column_stack(xcols)
    beta, *_ = np.linalg.lstsq(xmat, yy, rcond=None)
    pred = xmat @ beta
    resid = yy - pred
    sse = float(np.sum(resid * resid))
    sst = float(np.sum((yy - np.mean(yy)) ** 2))
    r2 = float(1.0 - sse / sst) if sst > 0.0 else float("nan")
    p_values: dict[str, float] = {}
    try:
        from scipy import stats

        dof = int(yy.size - xmat.shape[1])
        if dof > 0:
            sigma2 = sse / dof
            cov = sigma2 * np.linalg.pinv(xmat.T @ xmat)
            se = np.sqrt(np.maximum(np.diag(cov), 0.0))
            tstat = np.divide(beta, se, out=np.full_like(beta, np.nan), where=se > 0.0)
            pvals = 2.0 * stats.t.sf(np.abs(tstat), dof)
            p_values = {names[i]: float(pvals[i]) for i in range(len(names))}
    except Exception:
        p_values = {}
    return {
        "model": "+".join(name for name, _ in columns),
        "n": int(yy.size),
        "r2": r2,
        "sse": sse,
        "coefficients_json": json.dumps({names[i]: float(beta[i]) for i in range(len(names))}, sort_keys=True),
        "p_values_json": json.dumps(p_values, sort_keys=True),
    }


def residualize(y: np.ndarray, columns: list[np.ndarray]) -> np.ndarray:
    mask = np.isfinite(y)
    for col in columns:
        mask &= np.isfinite(col)
    out = np.full_like(y, np.nan, dtype=float)
    if int(np.sum(mask)) <= len(columns) + 1:
        return out
    xmat = np.column_stack([np.ones(int(np.sum(mask)), dtype=float)] + [col[mask] for col in columns])
    beta, *_ = np.linalg.lstsq(xmat, y[mask], rcond=None)
    out[mask] = y[mask] - xmat @ beta
    return out


def corr(a: np.ndarray, b: np.ndarray) -> float:
    mask = np.isfinite(a) & np.isfinite(b)
    if int(np.sum(mask)) < 3:
        return float("nan")
    aa = a[mask]
    bb = b[mask]
    if float(np.std(aa)) <= 1e-12 or float(np.std(bb)) <= 1e-12:
        return float("nan")
    return float(np.corrcoef(aa, bb)[0, 1])


def pick_verdict(partial_r2_dz: float, partial_r2_theta: float, p_dz: float, p_theta: float) -> tuple[str, str]:
    dz_sig = np.isfinite(p_dz) and p_dz < 0.05 and partial_r2_dz >= 0.02
    theta_sig = np.isfinite(p_theta) and p_theta < 0.05 and partial_r2_theta >= 0.02
    if dz_sig and theta_sig:
        if partial_r2_theta > 2.0 * partial_r2_dz:
            return "angle-limited", "theta survives the joint model and contributes more partial R2 than Delta_z"
        if partial_r2_dz > 2.0 * partial_r2_theta:
            return "geometry-limited", "Delta_z survives the joint model and contributes more partial R2 than theta"
        return "mixed", "both Delta_z and theta survive the joint model with comparable partial R2"
    if theta_sig:
        return "angle-limited", "theta survives after controlling Delta_z; Delta_z does not"
    if dz_sig:
        return "geometry-limited", "Delta_z survives after controlling theta; theta does not"
    if np.nan_to_num(partial_r2_theta, nan=0.0) > np.nan_to_num(partial_r2_dz, nan=0.0):
        return "angle-leaning-inconclusive", "theta has larger partial R2 but does not pass the significance/size gate"
    return "geometry-leaning-inconclusive", "Delta_z has larger partial R2 but does not pass the significance/size gate"


def main() -> int:
    parser = argparse.ArgumentParser(description="Attribute V5 deployable residual structure to geometry or elevation angle.")
    parser.add_argument("--out-dir", type=Path, default=FULL_ROOT / "tables")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    tables = args.out_dir.resolve()
    residual_path = tables / "A_residual_attribution.csv"
    summary_path = tables / "A_residual_attribution_summary.csv"
    regression_path = tables / "A_residual_attribution_regressions.csv"
    if not args.replace:
        for path in (residual_path, summary_path, regression_path):
            if path.exists():
                raise SystemExit(f"refusing to overwrite existing output without --replace: {path}")

    ablation = load_module(ABLATION_SCRIPT, "residual_attribution_ablation_helpers")
    labels, coords_layout, delays, _layout_tag_delay = ablation.load_layout_json_raw(V5_LAYOUT)
    by_label = {label: coords_layout[i] for i, label in enumerate(labels)}
    src = np.vstack([by_label[a] for a in ANCHORS])
    anchor_truth, tag_truth_vicon, tag_truth_meta, _corrections = ablation.load_corrected_static_truth(
        OPTI_ROOT,
        ANCHORS,
        ablation.PRIMARY_IDS,
    )
    truth_coords_vicon = np.vstack([anchor_truth[a] for a in ANCHORS])
    rigid = ablation.fit_similarity(src, truth_coords_vicon, allow_reflection=True, allow_scale=False)
    coords_rigid_vicon = ablation.apply_fit(src, rigid)
    anchor_truth_in_layout = (truth_coords_vicon - rigid.translation) @ rigid.rotation.T
    rigid_residual_layout = src - anchor_truth_in_layout
    rigid_residual_vicon = coords_rigid_vicon - truth_coords_vicon
    d_tag = loo_deployable_dtag()

    z_vals = src[:, 2]
    z_cut = float(np.nanmedian(z_vals))
    layers = ["top" if z <= z_cut else "bottom" for z in z_vals]
    layer_z = {
        "top_mean_z_mm": float(np.nanmean([z_vals[i] for i, layer in enumerate(layers) if layer == "top"])),
        "bottom_mean_z_mm": float(np.nanmean([z_vals[i] for i, layer in enumerate(layers) if layer == "bottom"])),
    }

    static_files = sorted(CAPTURES_ROOT.glob("static_ID*/tag_capture*/tr_all.csv"))
    medians_by_id, counts_by_id = precompute_static_median_ranges(static_files)
    rows: list[dict[str, Any]] = []
    for sid in sorted(medians_by_id):
        if sid not in tag_truth_vicon:
            continue
        p_vicon = tag_truth_vicon[sid]
        p_layout = (p_vicon - rigid.translation) @ rigid.rotation.T
        for aid, measured in sorted(medians_by_id[sid].items()):
            a_layout = src[int(aid)]
            diff = a_layout - p_layout
            horizontal = float(math.hypot(diff[0], diff[1]))
            theta = float(math.degrees(math.atan2(diff[2], horizontal)))
            geom = float(np.linalg.norm(diff))
            rho = float(measured) - geom - float(delays[int(aid)]) - d_tag
            rows.append(
                {
                    "ID": sid,
                    "anchor_id": int(aid),
                    "anchor": ANCHORS[int(aid)],
                    "n_ranges": int(counts_by_id[sid].get(int(aid), 0)),
                    "median_range_mm": float(measured),
                    "geom_range_mm": geom,
                    "d_anchor_mm": float(delays[int(aid)]),
                    "d_tag_mm": d_tag,
                    "rho_mm": rho,
                    "theta_deg": theta,
                    "theta_abs_deg": abs(theta),
                    "layer": layers[int(aid)],
                    "anchor_layout_z_mm": float(src[int(aid), 2]),
                    "delta_z_i_mm": float(rigid_residual_layout[int(aid), 2]),
                    "delta_y_vicon_vertical_mm": float(rigid_residual_vicon[int(aid), 1]),
                    "tag_truth_source": tag_truth_meta.get(sid, {}).get("tag_truth_source", ""),
                    "source_v5_layout": str(V5_LAYOUT),
                    "opti_anchor_truth_source": str(OPTI_ROOT),
                }
            )

    df = pd.DataFrame(rows)
    y = df["rho_mm"].to_numpy(dtype=float)
    dz = df["delta_z_i_mm"].to_numpy(dtype=float)
    theta = df["theta_deg"].to_numpy(dtype=float)
    reg_geometry = ols(y, [("delta_z_i_mm", dz)])
    reg_angle = ols(y, [("theta_deg", theta)])
    reg_joint = ols(y, [("delta_z_i_mm", dz), ("theta_deg", theta)])
    sse_geometry = float(reg_geometry["sse"])
    sse_angle = float(reg_angle["sse"])
    sse_joint = float(reg_joint["sse"])
    partial_r2_dz = float((sse_angle - sse_joint) / sse_angle) if np.isfinite(sse_angle) and sse_angle > 0.0 else float("nan")
    partial_r2_theta = float((sse_geometry - sse_joint) / sse_geometry) if np.isfinite(sse_geometry) and sse_geometry > 0.0 else float("nan")
    partial_corr_dz = corr(residualize(y, [theta]), residualize(dz, [theta]))
    partial_corr_theta = corr(residualize(y, [dz]), residualize(theta, [dz]))
    coeffs_joint = json.loads(reg_joint["coefficients_json"])
    pvals_joint = json.loads(reg_joint["p_values_json"]) if reg_joint["p_values_json"] else {}
    verdict, verdict_reason = pick_verdict(
        partial_r2_dz,
        partial_r2_theta,
        float(pvals_joint.get("delta_z_i_mm", float("nan"))),
        float(pvals_joint.get("theta_deg", float("nan"))),
    )

    layer_rows: list[dict[str, Any]] = []
    for layer, g in df.groupby("layer", dropna=False):
        vals = g["rho_mm"].to_numpy(dtype=float)
        layer_rows.append(
            {
                "layer": layer,
                "n": int(len(g)),
                "mean_rho_mm": float(np.nanmean(vals)),
                "median_rho_mm": float(np.nanmedian(vals)),
                "rms_rho_mm": float(math.sqrt(np.nanmean(vals * vals))),
                "mean_anchor_z_mm": float(np.nanmean(g["anchor_layout_z_mm"].to_numpy(dtype=float))),
            }
        )
    layer_mean = {row["layer"]: row["mean_rho_mm"] for row in layer_rows}
    layer_offset = float(layer_mean.get("top", float("nan")) - layer_mean.get("bottom", float("nan")))

    regression_rows = [
        {**reg_geometry, "partial_r2_added_last": "", "partial_corr_added_last": ""},
        {**reg_angle, "partial_r2_added_last": "", "partial_corr_added_last": ""},
        {
            **reg_joint,
            "partial_r2_delta_z_given_theta": partial_r2_dz,
            "partial_corr_delta_z_given_theta": partial_corr_dz,
            "partial_r2_theta_given_delta_z": partial_r2_theta,
            "partial_corr_theta_given_delta_z": partial_corr_theta,
        },
    ]
    summary = {
        "script": str(THIS),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "rows": int(len(rows)),
        "positions": int(df["ID"].nunique()),
        "anchors": int(df["anchor"].nunique()),
        "d_tag_mm": d_tag,
        "mean_rho_mm": float(np.nanmean(y)),
        "median_rho_mm": float(np.nanmedian(y)),
        "rms_rho_mm": float(math.sqrt(np.nanmean(y * y))),
        "top_minus_bottom_mean_rho_mm": layer_offset,
        **layer_z,
        "geometry_r2": float(reg_geometry["r2"]),
        "angle_r2": float(reg_angle["r2"]),
        "joint_r2": float(reg_joint["r2"]),
        "joint_delta_z_coeff_mm_per_mm": float(coeffs_joint.get("delta_z_i_mm", float("nan"))),
        "joint_theta_coeff_mm_per_deg": float(coeffs_joint.get("theta_deg", float("nan"))),
        "partial_r2_delta_z_given_theta": partial_r2_dz,
        "partial_corr_delta_z_given_theta": partial_corr_dz,
        "partial_r2_theta_given_delta_z": partial_r2_theta,
        "partial_corr_theta_given_delta_z": partial_corr_theta,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "source_v5_layout": str(V5_LAYOUT),
        "opti_anchor_truth_source": str(OPTI_ROOT),
        "existing_physical_sim3_vicon_layout_path": existing_physical_sim3_layout_path(),
        "axis_note": "delta_z_i_mm and theta use the V5 layout frame where z is vertical; delta_y_vicon_vertical_mm is included for the Vicon-Y sanity check.",
    }

    residual_path.parent.mkdir(parents=True, exist_ok=True)
    write_csv(residual_path, rows)
    write_csv(regression_path, regression_rows + [{"model": "layer_means", "rows_json": json.dumps(layer_rows, sort_keys=True)}])
    write_csv(summary_path, [summary])
    print(json.dumps({"status": "ok", "summary": summary, "layers": layer_rows, "regressions": regression_rows}, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
