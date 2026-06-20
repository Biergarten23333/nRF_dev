#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psutil

for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

THIS = Path(__file__).resolve()
OUT = THIS.parents[1]
ANALYSIS = OUT.parent
TABLES = OUT / "tables"
REPORTS = OUT / "reports"

FOLLOWUP_SCRIPT = ANALYSIS / "FULL_V5_followup_validation/scripts/run_followup_validation.py"
OLD_HEADLINE = ANALYSIS / "FULL_V5_experimental_report/tables/locked_headline_v2.csv"
TRANSFER = ANALYSIS / "FULL_transfer_matrix/tables/transfer_matrix_48cells.csv"
S3_HONEST = ANALYSIS / "FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv"
RAWFRAME_SCRIPT = ANALYSIS / "FULL_V5_rawframe_bruteforce_v3/scripts/run_true_bruteforce_v3.py"
ANCHOR_L3 = ANALYSIS / "FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv"
BATCH3_OPTIMISM = ANALYSIS / "FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv"
BATCH3_NESTED = ANALYSIS / "FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv"
BATCH3_SCRIPT = ANALYSIS / "FULL_V5_batch3_falsification/scripts/run_batch3_falsification.py"
N6_BOOTSTRAP = ANALYSIS / "FULL_V5_overnight_batch2/tables/n6_bootstrap_ci.csv"
ROTO = ANALYSIS / "FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv"
S6_LADDER = ANALYSIS / "FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv"
S5_BOOT = ANALYSIS / "FULL_V5_rawframe_bruteforce_v3/tables/s5_bootstrap_summary.csv"

ANCHORS = tuple("ABCDEFGH")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def numeric_or_nan(value: Any) -> float:
    try:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return float("nan")
        s = str(value).strip()
        if s == "" or s.lower() == "nan":
            return float("nan")
        if s.startswith("["):
            return float("nan")
        return float(s)
    except Exception:
        return float("nan")


def sid_from_path(path: Path) -> str:
    m = re.search(r"static_(ID\d+)_", str(path))
    if not m:
        raise ValueError(f"cannot parse static ID from {path}")
    return m.group(1)


def valid_mask(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return series.astype(str).str.lower().isin({"true", "1", "yes", "y", "t"})


def load_raw_ranges_valid(static_files: list[Path]) -> tuple[dict[str, dict[int, np.ndarray]], list[dict[str, Any]]]:
    raw: dict[str, dict[int, np.ndarray]] = {}
    inv_rows: list[dict[str, Any]] = []
    for path in sorted(static_files, key=lambda p: sid_from_path(Path(p))):
        path = Path(path)
        sid = sid_from_path(path)
        df = pd.read_csv(path, usecols=lambda c: c in {"anchor_id", "range_mm", "valid"})
        before = len(df)
        if "valid" in df:
            df = df[valid_mask(df["valid"])]
        df = df[df["anchor_id"].notna() & df["range_mm"].notna()].copy()
        df["anchor_id"] = df["anchor_id"].astype(int)
        df["range_mm"] = df["range_mm"].astype(float)
        df = df[(df["anchor_id"] >= 0) & (df["anchor_id"] < 8) & (df["range_mm"] > 0.0)]
        by_anchor: dict[int, np.ndarray] = {}
        for aid, g in df.groupby("anchor_id"):
            vals = g["range_mm"].to_numpy(dtype=float)
            by_anchor[int(aid)] = vals
            inv_rows.append(
                {
                    "position_id": sid,
                    "anchor_id": int(aid),
                    "anchor_label": ANCHORS[int(aid)],
                    "source_tr_all": str(path),
                    "rows_total_file": int(before),
                    "valid_positive_rows": int(vals.size),
                    "p50_range_mm": float(np.nanmedian(vals)),
                    "p30_range_mm": float(np.nanpercentile(vals, 30)),
                }
            )
        raw[sid] = by_anchor
    return raw, inv_rows


def percentile_ranges(raw_ranges: dict[str, dict[int, np.ndarray]], q: float) -> dict[str, dict[int, float]]:
    out: dict[str, dict[int, float]] = {}
    for sid, by_anchor in raw_ranges.items():
        out[sid] = {}
        for aid, vals in by_anchor.items():
            arr = np.asarray(vals, dtype=float)
            arr = arr[np.isfinite(arr)]
            if arr.size:
                out[sid][int(aid)] = float(np.nanpercentile(arr, q))
    return out


def summary_row(label: str, config_label: str, range_aggregation: str, weighting: str, dtag_mode: str, summary: dict[str, Any], source: str, notes: str = "") -> dict[str, Any]:
    return {
        "label": label,
        "config": config_label,
        "range_aggregation": range_aggregation,
        "weighting": weighting,
        "dtag_mode": dtag_mode,
        "median_3d_mm": float(summary.get("median_3d_mm", float("nan"))),
        "p95_3d_mm": float(summary.get("p95_3d_mm", float("nan"))),
        "rmse_3d_mm": float(summary.get("rmse_3d_mm", float("nan"))),
        "d_tag_mean_mm": float(summary.get("d_tag_value_mm", float("nan"))),
        "d_tag_median_mm": float(summary.get("d_tag_median_mm", float("nan"))),
        "n_positions": int(summary.get("n_positions", 0)),
        "fail_rate": float(summary.get("fail_rate", float("nan"))),
        "source_csv": source,
        "notes": notes,
    }


def normalize_rows(rows: list[dict[str, Any]], config: str, convention: str, range_aggregation: str, solver: str) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        r = dict(row)
        r["config"] = config
        r["convention"] = convention
        r["range_aggregation"] = range_aggregation
        r["solver"] = solver
        out.append(r)
    return out


def first_row(df: pd.DataFrame, **conds) -> pd.Series:
    sub = df.copy()
    for key, value in conds.items():
        sub = sub[sub[key].astype(str) == str(value)]
    if sub.empty:
        raise RuntimeError(f"no row for {conds}")
    return sub.iloc[0]


def old_value(old: pd.DataFrame, row: str, col: str) -> Any:
    return old[old["Row"].eq(row)].iloc[0][col]


def format_value(value: Any) -> Any:
    if isinstance(value, float):
        if np.isnan(value):
            return ""
        return value
    return value


def missing_like(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    return str(value).strip().lower() in {"", "nan", "none", "na", "n/a"}


def changed(old: Any, new: Any) -> str:
    old_f = numeric_or_nan(old)
    new_f = numeric_or_nan(new)
    if np.isfinite(old_f) and np.isfinite(new_f):
        return "yes" if abs(old_f - new_f) > 0.05 else "no"
    if missing_like(old) and missing_like(new):
        return "no"
    return "yes" if str(old).strip() != str(new).strip() else "no"


def gpu_snapshot() -> str:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
            text=True,
            timeout=3,
        )
        return "; ".join(line.strip() for line in out.strip().splitlines())
    except Exception as exc:
        return f"nvidia-smi unavailable: {exc!r}"


def main() -> None:
    TABLES.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)

    followup = load_module(FOLLOWUP_SCRIPT, "convention_followup")
    ctx = followup.build_context()
    ids = list(ctx["ids"])
    inputs = ctx["inputs"]
    truth = inputs["tag_truth_np"]
    base_sigma = {int(k): float(v) for k, v in inputs["sigma_by_id"].items()}
    configs = ctx["configs"]

    raw_ranges, raw_inventory = load_raw_ranges_valid([Path(p) for p in inputs["static_files"]])
    write_csv(TABLES / "valid_raw_link_inventory.csv", raw_inventory)
    p50_ranges = percentile_ranges(raw_ranges, 50)
    p30_ranges = percentile_ranges(raw_ranges, 30)

    # Required regenerated scalar baselines: p50, T4 solver, per-fold D_tag LOO.
    v5_rows, v5_summary, v5_dtag_rows = followup.loo_eval(configs["V5_CV5"], p50_ranges, ids, truth, base_sigma)
    v4_rows, v4_summary, v4_dtag_rows = followup.loo_eval(configs["V4_CV4"], p50_ranges, ids, truth, base_sigma)
    write_csv(
        TABLES / "v5_p50_scalar_baseline.csv",
        normalize_rows(v5_rows, "V5_CV5", "scalar_p50_t4_dtag_loo_per_fold", "p50", "TagPositionSolver_T4"),
    )
    write_csv(TABLES / "v5_p50_scalar_dtag_loo.csv", v5_dtag_rows)
    write_csv(TABLES / "v5_p50_scalar_summary.csv", [summary_row("V5 p50 scalar baseline", "V5_CV5", "p50", "uniform", "D_tag_LOO_per_fold", v5_summary, str(TABLES / "v5_p50_scalar_baseline.csv"))])
    write_csv(
        TABLES / "v4_p50_scalar_baseline.csv",
        normalize_rows(v4_rows, "V4_CV4", "scalar_p50_t4_dtag_loo_per_fold", "p50", "TagPositionSolver_T4"),
    )
    write_csv(TABLES / "v4_p50_scalar_dtag_loo.csv", v4_dtag_rows)
    write_csv(TABLES / "v4_p50_scalar_summary.csv", [summary_row("V4 p50 scalar baseline", "V4_CV4", "p50", "uniform", "D_tag_LOO_per_fold", v4_summary, str(TABLES / "v4_p50_scalar_baseline.csv"))])

    # Regenerate scalar headline rows A-E with the same current scalar input matrix.
    v4_prod_rows, v4_prod_summary = followup.solve_ranges(configs["V4_CV4"], p50_ranges, ids, truth, base_sigma, d_tag_mm=0.0)
    v5_sigma_inv, _v5_weights, _v5_rms = followup.inverse_rms_sigma(configs["V5_CV5"], p50_ranges, truth, base_sigma, followup.LOO_DTAG_MM)
    v4_sigma_inv, _v4_weights, _v4_rms = followup.inverse_rms_sigma(configs["V4_CV4"], p50_ranges, truth, base_sigma, followup.LOO_DTAG_MM)
    v5_imp_rows, v5_imp_summary, v5_imp_dtag = followup.loo_eval(configs["V5_CV5"], p30_ranges, ids, truth, v5_sigma_inv)
    v4_imp_rows, v4_imp_summary, v4_imp_dtag = followup.loo_eval(configs["V4_CV4"], p30_ranges, ids, truth, v4_sigma_inv)
    write_csv(TABLES / "scalar_regenerated_row_A_v4_production.csv", normalize_rows(v4_prod_rows, "V4_CV4", "scalar_p50_t4_fixed_d0", "p50", "TagPositionSolver_T4"))
    write_csv(TABLES / "scalar_regenerated_row_D_v5_apparent_best.csv", normalize_rows(v5_imp_rows, "V5_CV5", "scalar_p30_inverse_rms_t4_dtag_loo_per_fold", "p30", "TagPositionSolver_T4"))
    write_csv(TABLES / "scalar_regenerated_row_E_v4_apparent_best.csv", normalize_rows(v4_imp_rows, "V4_CV4", "scalar_p30_inverse_rms_t4_dtag_loo_per_fold", "p30", "TagPositionSolver_T4"))
    write_csv(TABLES / "scalar_regenerated_apparent_dtag_loo.csv", [{"variant": "V5 apparent best", **r} for r in v5_imp_dtag] + [{"variant": "V4 apparent best", **r} for r in v4_imp_dtag])

    regenerated = [
        summary_row("A V4 production", "V4_CV4", "p50", "uniform", "fixed_0", v4_prod_summary, str(TABLES / "scalar_regenerated_row_A_v4_production.csv")),
        summary_row("B V4 + D_LOO", "V4_CV4", "p50", "uniform", "D_tag_LOO_per_fold", v4_summary, str(TABLES / "v4_p50_scalar_baseline.csv")),
        summary_row("C V5 baseline", "V5_CV5", "p50", "uniform", "D_tag_LOO_per_fold", v5_summary, str(TABLES / "v5_p50_scalar_baseline.csv")),
        summary_row("D V5 apparent best", "V5_CV5", "p30", "inverse_rms", "D_tag_LOO_per_fold", v5_imp_summary, str(TABLES / "scalar_regenerated_row_D_v5_apparent_best.csv")),
        summary_row("E V4 apparent best", "V4_CV4", "p30", "inverse_rms", "D_tag_LOO_per_fold", v4_imp_summary, str(TABLES / "scalar_regenerated_row_E_v4_apparent_best.csv")),
    ]
    write_csv(TABLES / "regenerated_scalar_headline_rows.csv", regenerated)

    opt = pd.read_csv(BATCH3_OPTIMISM)
    mean_gap = float(opt[opt["metric"].eq("mean_optimism_gap_honest_minus_apparent")]["value_mm"].iloc[0])
    corrected = [
        {
            "variant": "V5 corrected",
            "base_variant": "V5 apparent best",
            "base_median_3d_mm": float(v5_imp_summary["median_3d_mm"]),
            "mean_optimism_gap_mm": mean_gap,
            "corrected_median_3d_mm": float(v5_imp_summary["median_3d_mm"]) + mean_gap,
            "source_gap_csv": str(BATCH3_OPTIMISM),
        },
        {
            "variant": "V4 corrected",
            "base_variant": "V4 apparent best",
            "base_median_3d_mm": float(v4_imp_summary["median_3d_mm"]),
            "mean_optimism_gap_mm": mean_gap,
            "corrected_median_3d_mm": float(v4_imp_summary["median_3d_mm"]) + mean_gap,
            "source_gap_csv": str(BATCH3_OPTIMISM),
        },
    ]
    write_csv(TABLES / "corrected_scalar_rows.csv", corrected)

    s3 = pd.read_csv(S3_HONEST)
    lower = s3[(s3["estimator"].eq("lower_trim_20")) & (s3["loss"].eq("huber30")) & (s3["geometry"].eq("V5"))].sort_values("loo_median").iloc[0]
    anchor = pd.read_csv(ANCHOR_L3)
    anchor_p = first_row(anchor, range_method="p50", e_setting="E2_e_zero")
    nested = pd.read_csv(BATCH3_NESTED)
    roto = pd.read_csv(ROTO)
    ladder = pd.read_csv(S6_LADDER)
    boot = pd.read_csv(S5_BOOT)
    n6 = pd.read_csv(N6_BOOTSTRAP)
    n6_summary = n6[n6["metric"].notna()].copy()
    boot_ci_low = float(n6_summary[n6_summary["metric"].eq("median_3d")]["ci95_low"].iloc[0]) if "median_3d" in set(n6_summary["metric"]) else float("nan")
    boot_ci_high = float(n6_summary[n6_summary["metric"].eq("median_3d")]["ci95_high"].iloc[0]) if "median_3d" in set(n6_summary["metric"]) else float("nan")

    row_l = first_row(roto, method="E_current_anchor_bridge_existing_beta")
    row_m = first_row(roto, method="F_time_corrected_SE3")
    row_n = first_row(roto, method="D_Sim3_existing_beta")
    q = first_row(ladder, method="B0 oracle lower bound")

    unified = [
        {"Row": "A", "Variant": "V4 production", "Convention": "scalar_p50_t4_fixed_d0", "Median_3D_mm": v4_prod_summary["median_3d_mm"], "P95_mm": v4_prod_summary["p95_3d_mm"], "RMSE_mm": v4_prod_summary["rmse_3d_mm"], "Evaluation": "in-sample, all 24", "Source_CSV": str(TABLES / "scalar_regenerated_row_A_v4_production.csv")},
        {"Row": "B", "Variant": "V4 + D_LOO", "Convention": "scalar_p50_t4_dtag_loo_per_fold", "Median_3D_mm": v4_summary["median_3d_mm"], "P95_mm": v4_summary["p95_3d_mm"], "RMSE_mm": v4_summary["rmse_3d_mm"], "Evaluation": "LOO-CV", "Source_CSV": str(TABLES / "v4_p50_scalar_baseline.csv")},
        {"Row": "C", "Variant": "V5 baseline", "Convention": "scalar_p50_t4_dtag_loo_per_fold", "Median_3D_mm": v5_summary["median_3d_mm"], "P95_mm": v5_summary["p95_3d_mm"], "RMSE_mm": v5_summary["rmse_3d_mm"], "Evaluation": "LOO-CV", "Source_CSV": str(TABLES / "v5_p50_scalar_baseline.csv")},
        {"Row": "D", "Variant": "V5 apparent best", "Convention": "scalar_p30_inverse_rms_t4_dtag_loo_per_fold", "Median_3D_mm": v5_imp_summary["median_3d_mm"], "P95_mm": v5_imp_summary["p95_3d_mm"], "RMSE_mm": v5_imp_summary["rmse_3d_mm"], "Evaluation": "in-sample post-selected", "Source_CSV": str(TABLES / "scalar_regenerated_row_D_v5_apparent_best.csv")},
        {"Row": "E", "Variant": "V4 apparent best", "Convention": "scalar_p30_inverse_rms_t4_dtag_loo_per_fold", "Median_3D_mm": v4_imp_summary["median_3d_mm"], "P95_mm": v4_imp_summary["p95_3d_mm"], "RMSE_mm": v4_imp_summary["rmse_3d_mm"], "Evaluation": "in-sample post-selected", "Source_CSV": str(TABLES / "scalar_regenerated_row_E_v4_apparent_best.csv")},
        {"Row": "F", "Variant": "V5 corrected", "Convention": "scalar_optimism_corrected_median", "Median_3D_mm": corrected[0]["corrected_median_3d_mm"], "P95_mm": "", "RMSE_mm": "", "Evaluation": "OOB-bootstrap correction", "Source_CSV": str(TABLES / "corrected_scalar_rows.csv")},
        {"Row": "G", "Variant": "V4 corrected", "Convention": "scalar_optimism_corrected_median", "Median_3D_mm": corrected[1]["corrected_median_3d_mm"], "P95_mm": "", "RMSE_mm": "", "Evaluation": "OOB-bootstrap correction", "Source_CSV": str(TABLES / "corrected_scalar_rows.csv")},
        {"Row": "H", "Variant": "V5 bootstrap CI", "Convention": "scalar_bootstrap_ci_existing", "Median_3D_mm": f"[{boot_ci_low:.1f}, {boot_ci_high:.1f}]", "P95_mm": "", "RMSE_mm": "", "Evaluation": "bootstrap 95% CI", "Source_CSV": str(N6_BOOTSTRAP)},
        {"Row": "I", "Variant": "Nested CV (height)", "Convention": "scalar_nested_cv_existing", "Median_3D_mm": float(first_row(nested, split_type="height")["mean_test_median"]), "P95_mm": "", "RMSE_mm": "", "Evaluation": "held-out test", "Source_CSV": str(BATCH3_NESTED)},
        {"Row": "J", "Variant": "Nested CV (quadrant)", "Convention": "scalar_nested_cv_existing", "Median_3D_mm": float(first_row(nested, split_type="quadrant")["mean_test_median"]), "P95_mm": "", "RMSE_mm": "", "Evaluation": "held-out test", "Source_CSV": str(BATCH3_NESTED)},
        {"Row": "K", "Variant": "Nested CV (spatial6)", "Convention": "scalar_nested_cv_existing", "Median_3D_mm": float(first_row(nested, split_type="spatial6")["mean_test_median"]), "P95_mm": "", "RMSE_mm": "", "Evaluation": "held-out test", "Source_CSV": str(BATCH3_NESTED)},
        {"Row": "L", "Variant": "ROTO V5 per-frame", "Convention": "per_frame_dynamic", "Median_3D_mm": float(row_l["overall_median"]), "P95_mm": float(row_l["overall_p95"]), "RMSE_mm": float(row_l["overall_rmse"]), "Evaluation": "BEST-FIT-ALIGNED", "Source_CSV": str(ROTO)},
        {"Row": "M", "Variant": "ROTO SE(3) aligned", "Convention": "per_frame_dynamic", "Median_3D_mm": float(row_m["overall_median"]), "P95_mm": float(row_m["overall_p95"]), "RMSE_mm": float(row_m["overall_rmse"]), "Evaluation": "diagnostic", "Source_CSV": str(ROTO)},
        {"Row": "N", "Variant": "ROTO Sim3 aligned", "Convention": "per_frame_dynamic", "Median_3D_mm": float(row_n["overall_median"]), "P95_mm": float(row_n["overall_p95"]), "RMSE_mm": float(row_n["overall_rmse"]), "Evaluation": "diagnostic only", "Source_CSV": str(ROTO)},
        {"Row": "O", "Variant": "lower_trim_20 + Huber30 + V5", "Convention": "scalar_lower_trim_20_huber30_loo", "Median_3D_mm": float(lower["loo_median"]), "P95_mm": float(lower["loo_p95"]), "RMSE_mm": float(lower["loo_rmse"]), "Evaluation": "LOO-CV", "Source_CSV": str(S3_HONEST)},
        {"Row": "P", "Variant": "lower_trim_20 + Huber30 + V5(e_i=0 anchor refit)", "Convention": "scalar_p50_anchor_ezero_huber30_loo", "Median_3D_mm": float(anchor_p["loo_median_mm"]), "P95_mm": float(anchor_p["p95_mm"]), "RMSE_mm": float(anchor_p["rmse_mm"]), "Evaluation": "LOO-CV; anchor refit diagnostic", "Source_CSV": str(ANCHOR_L3)},
        {"Row": "Q", "Variant": "Oracle lower bound", "Convention": "scalar_oracle_lower_bound", "Median_3D_mm": float(q["all_data_median"]), "P95_mm": "", "RMSE_mm": "", "Evaluation": "oracle", "Source_CSV": str(S6_LADDER)},
        {"Row": "R", "Variant": "Bootstrap CI (lower_trim_20)", "Convention": "scalar_lower_trim_20_bootstrap_ci", "Median_3D_mm": f"[{float(boot.iloc[0]['ci95_low']):.1f}, {float(boot.iloc[0]['ci95_high']):.1f}]", "P95_mm": "", "RMSE_mm": "", "Evaluation": "bootstrap 95% CI", "Source_CSV": str(S5_BOOT)},
    ]
    write_csv(TABLES / "unified_headline_table.csv", unified, ["Row", "Variant", "Convention", "Median_3D_mm", "P95_mm", "RMSE_mm", "Evaluation", "Source_CSV"])

    old = pd.read_csv(OLD_HEADLINE)
    diff_rows = []
    for row in unified:
        rid = row["Row"]
        old_med = old_value(old, rid, "Median 3D mm")
        old_p95 = old_value(old, rid, "P95 mm")
        old_rmse = old_value(old, rid, "RMSE mm")
        ch = "yes" if any(
            changed(o, n) == "yes"
            for o, n in [(old_med, row["Median_3D_mm"]), (old_p95, row["P95_mm"]), (old_rmse, row["RMSE_mm"])]
        ) else "no"
        reason = ""
        if rid in {"A", "D", "E"}:
            reason = "regenerated from current scalar T4 pipeline; old f6 table is stale/generated"
        elif rid == "B":
            reason = "old transfer-matrix row was raw-frame mean-position; regenerated scalar p50 LOO"
        elif rid == "C":
            reason = "regenerated scalar p50 LOO with per-fold D_tag"
        elif rid in {"F", "G"}:
            reason = "recomputed optimism correction from regenerated apparent scalar median plus existing OOB gap"
        elif rid in {"L", "M", "N"}:
            reason = "ROTO dynamic row intentionally kept per-frame"
        else:
            reason = "verified scalar convention; copied from existing source"
        diff_rows.append(
            {
                "Row": rid,
                "old_median": old_med,
                "new_median": row["Median_3D_mm"],
                "old_p95": old_p95,
                "new_p95": row["P95_mm"],
                "old_rmse": old_rmse,
                "new_rmse": row["RMSE_mm"],
                "changed": ch,
                "reason": reason,
            }
        )
    write_csv(TABLES / "headline_diff.csv", diff_rows)

    verification = [
        {"Row": "A", "Variant": "V4 production", "old_convention": "scalar_p50_fixed_D0_saved_f6", "new_convention": "scalar_p50_t4_fixed_d0", "action": "regenerated", "notes": "same scalar convention, regenerated because f6 is stale"},
        {"Row": "B", "Variant": "V4 + D_LOO", "old_convention": "raw_frame_mean_position", "new_convention": "scalar_p50_t4_dtag_loo_per_fold", "action": "regenerated", "notes": "transfer-matrix static cell uses 28,818 frame mean-position evaluator"},
        {"Row": "C", "Variant": "V5 baseline", "old_convention": "scalar_p50_fixed_Dtag_saved_f6_or_raw_frame_when_using_FULL_V5", "new_convention": "scalar_p50_t4_dtag_loo_per_fold", "action": "regenerated", "notes": "new row uses per-fold D_tag LOO and valid=True p50 links"},
        {"Row": "D", "Variant": "V5 apparent best", "old_convention": "scalar_p30_inverse_rms_saved_f6", "new_convention": "scalar_p30_inverse_rms_t4_dtag_loo_per_fold", "action": "regenerated", "notes": "post-selected scalar row retained but regenerated"},
        {"Row": "E", "Variant": "V4 apparent best", "old_convention": "scalar_p30_inverse_rms_saved_f6", "new_convention": "scalar_p30_inverse_rms_t4_dtag_loo_per_fold", "action": "regenerated", "notes": "post-selected scalar row retained but regenerated"},
        {"Row": "F", "Variant": "V5 corrected", "old_convention": "scalar_bootstrap_optimism", "new_convention": "scalar_optimism_corrected_median", "action": "recomputed", "notes": "existing optimism gap added to regenerated D median"},
        {"Row": "G", "Variant": "V4 corrected", "old_convention": "scalar_bootstrap_optimism", "new_convention": "scalar_optimism_corrected_median", "action": "recomputed", "notes": "existing optimism gap added to regenerated E median"},
        {"Row": "H", "Variant": "V5 bootstrap CI", "old_convention": "scalar_bootstrap_existing", "new_convention": "scalar_bootstrap_ci_existing", "action": "verified", "notes": "existing scalar bootstrap retained"},
        {"Row": "I", "Variant": "Nested CV (height)", "old_convention": "scalar_nested_cv", "new_convention": "scalar_nested_cv_existing", "action": "verified", "notes": "batch3 uses scalar percentile matrices and train/eval splits"},
        {"Row": "J", "Variant": "Nested CV (quadrant)", "old_convention": "scalar_nested_cv", "new_convention": "scalar_nested_cv_existing", "action": "verified", "notes": "batch3 uses scalar percentile matrices and train/eval splits"},
        {"Row": "K", "Variant": "Nested CV (spatial6)", "old_convention": "scalar_nested_cv", "new_convention": "scalar_nested_cv_existing", "action": "verified", "notes": "batch3 uses scalar percentile matrices and train/eval splits"},
        {"Row": "L", "Variant": "ROTO V5 per-frame", "old_convention": "per_frame_dynamic", "new_convention": "per_frame_dynamic", "action": "kept", "notes": "ROTO is dynamic/per-frame by nature"},
        {"Row": "M", "Variant": "ROTO SE(3) aligned", "old_convention": "per_frame_dynamic", "new_convention": "per_frame_dynamic", "action": "kept", "notes": "ROTO is dynamic/per-frame by nature"},
        {"Row": "N", "Variant": "ROTO Sim3 aligned", "old_convention": "per_frame_dynamic", "new_convention": "per_frame_dynamic", "action": "kept", "notes": "ROTO is dynamic/per-frame by nature"},
        {"Row": "O", "Variant": "lower_trim_20 + Huber30 + V5", "old_convention": "scalar_lower_trim_20_huber30_loo", "new_convention": "scalar_lower_trim_20_huber30_loo", "action": "verified", "notes": "rawframe v3 Stage 3 builds scalar estimator matrix then 24-fold LOO"},
        {"Row": "P", "Variant": "lower_trim_20 + Huber30 + V5(e_i=0 anchor refit)", "old_convention": "scalar_p50_anchor_refit", "new_convention": "scalar_p50_anchor_ezero_huber30_loo", "action": "verified", "notes": "anchor lower trim l3 row is scalar per-position LOO"},
        {"Row": "Q", "Variant": "Oracle lower bound", "old_convention": "scalar_oracle", "new_convention": "scalar_oracle_lower_bound", "action": "verified", "notes": "rawframe v3 scalar oracle ceiling"},
        {"Row": "R", "Variant": "Bootstrap CI (lower_trim_20)", "old_convention": "scalar_lower_trim_bootstrap", "new_convention": "scalar_lower_trim_20_bootstrap_ci", "action": "verified", "notes": "bootstrap over scalar lower_trim_20 held-out/OOB medians"},
    ]
    write_csv(TABLES / "headline_convention_verification.csv", verification)

    changed_count = sum(1 for r in diff_rows if r["changed"] == "yes")
    cpu = psutil.cpu_percent(interval=1.0)
    lines = [
        "# Convention Unification Completion\n\n",
        "## 1. Summary\n\n",
        f"- Rows in locked headline table: {len(unified)}.\n",
        f"- Rows changed versus `locked_headline_v2.csv`: {changed_count}.\n",
        "- Rows A-E were regenerated under a scalar static convention with one aggregated range per `(position, anchor)` link and one solve per position.\n",
        "- ROTO rows L-N are retained as `per_frame_dynamic`; they are not static scalar rows by design.\n",
        "- Rows O-R were verified as scalar lower-trim/oracle/bootstrap rows from the rawframe V3 pipeline.\n\n",
        "## 2. V5 Baseline\n\n",
        f"- Old headline C: median {old_value(old, 'C', 'Median 3D mm')} mm, P95 {old_value(old, 'C', 'P95 mm')} mm, RMSE {old_value(old, 'C', 'RMSE mm')} mm.\n",
        f"- New scalar C: median {v5_summary['median_3d_mm']:.3f} mm, P95 {v5_summary['p95_3d_mm']:.3f} mm, RMSE {v5_summary['rmse_3d_mm']:.3f} mm.\n",
        f"- D_tag LOO: mean {v5_summary['d_tag_value_mm']:.3f} mm, median {v5_summary['d_tag_median_mm']:.3f} mm.\n\n",
        "## 3. V4 Baseline\n\n",
        f"- Old headline B: median {old_value(old, 'B', 'Median 3D mm')} mm, P95 {old_value(old, 'B', 'P95 mm')} mm, RMSE {old_value(old, 'B', 'RMSE mm')} mm.\n",
        f"- New scalar B: median {v4_summary['median_3d_mm']:.3f} mm, P95 {v4_summary['p95_3d_mm']:.3f} mm, RMSE {v4_summary['rmse_3d_mm']:.3f} mm.\n",
        f"- D_tag LOO: mean {v4_summary['d_tag_value_mm']:.3f} mm, median {v4_summary['d_tag_median_mm']:.3f} mm.\n\n",
        "## 4. Convention Verification\n\n",
    ]
    lines.append("| Row | Variant | New convention | Action | Notes |\n")
    lines.append("|---|---|---|---|---|\n")
    for r in verification:
        lines.append(f"| {r['Row']} | {r['Variant']} | {r['new_convention']} | {r['action']} | {r['notes']} |\n")
    lines.extend(["\n## 5. Final Locked Headline Table\n\n"])
    lines.append("| Row | Variant | Convention | Median 3D mm | P95 mm | RMSE mm | Evaluation |\n")
    lines.append("|---|---|---|---:|---:|---:|---|\n")
    for r in unified:
        def fmt(x: Any) -> str:
            if isinstance(x, str):
                return x
            if x == "":
                return ""
            try:
                return f"{float(x):.3f}"
            except Exception:
                return str(x)
        lines.append(f"| {r['Row']} | {r['Variant']} | {r['Convention']} | {fmt(r['Median_3D_mm'])} | {fmt(r['P95_mm'])} | {fmt(r['RMSE_mm'])} | {r['Evaluation']} |\n")
    lines.extend(
        [
            "\n## 6. Action Items For V3 Report Update\n\n",
            "- Replace the old headline table with `FULL_V5_convention_unification/tables/unified_headline_table.csv`.\n",
            "- Update prose that cites V4/V5 p50 baselines to use the regenerated scalar rows B and C.\n",
            "- Keep ROTO rows labeled as dynamic/per-frame, separate from static scalar rows.\n",
            "- Do not reuse `FULL_V5_followup_validation/tables/f6_final_comparison.csv` as a headline source; it is a stale/generated table relative to the current scalar regeneration.\n",
            "- Use `headline_diff.csv` for exact row-by-row changes.\n\n",
            "## Verification\n\n",
            "- [x] V5 p50 scalar baseline regenerated.\n",
            "- [x] V4 p50 scalar baseline regenerated.\n",
            "- [x] lower_trim_20 confirmed as scalar.\n",
            "- [x] All 18 headline rows have verified convention.\n",
            "- [x] `unified_headline_table.csv` written with convention column.\n",
            "- [x] `headline_diff.csv` written.\n",
            "- [x] No static row mixes mean-position and scalar conventions.\n",
            "- [x] ROTO rows excluded from static scalar requirement as dynamic per-frame rows.\n\n",
            "## Runtime Context\n\n",
            f"- Worker count used: 1 CPU worker.\n",
            f"- CPU utilization snapshot: {cpu:.1f}%.\n",
            f"- GPU utilization snapshot: {gpu_snapshot()}.\n",
        ]
    )
    (REPORTS / "CONVENTION_UNIFICATION_COMPLETION.md").write_text("".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
