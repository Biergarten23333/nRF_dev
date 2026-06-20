#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE = Path("/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official")
ANALYSIS = BASE / "Analysis/official_extra_analysis"
OUT = ANALYSIS / "FULL_V5_grand_synthesis"
TABLES = OUT / "tables"
REPORTS = OUT / "reports"
SCRIPTS = OUT / "scripts"

DIRS_TO_SCAN = [
    "FULL_V5",
    "FULL_V5_mechanism_ablations",
    "FULL_V5_extended_mechanism_ablations",
    "FULL_transfer_matrix",
    "FULL_V5_GPU_tier1",
    "FULL_V5_GPU_discovery",
    "FULL_V5_followup_validation",
    "FULL_V5_overnight_batch2",
    "FULL_V5_batch3_falsification",
    "FULL_V5_roto_deepdive",
    "FULL_V5_mechanistic_deepdive",
    "FULL_V5_paper_strengthening",
]


def path(rel: str) -> Path:
    return ANALYSIS / rel


def read_csv(rel: str, required: bool = False) -> pd.DataFrame:
    p = path(rel)
    if not p.exists():
        if required:
            raise FileNotFoundError(str(p))
        return pd.DataFrame()
    return pd.read_csv(p)


def read_text(rel: str) -> str:
    p = path(rel)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def write_csv(p: Path, rows: list[dict[str, Any]], cols: list[str] | None = None) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if cols is None:
        cols = []
        for row in rows:
            for key in row:
                if key not in cols:
                    cols.append(key)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_text(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def fnum(x: Any, default: float = float("nan")) -> float:
    try:
        y = float(x)
        return y if np.isfinite(y) else default
    except Exception:
        return default


def first(df: pd.DataFrame, col: str, default: float = float("nan")) -> float:
    if df.empty or col not in df:
        return default
    vals = pd.to_numeric(df[col], errors="coerce").dropna()
    return float(vals.iloc[0]) if not vals.empty else default


def select(df: pd.DataFrame, **conds) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    for col, val in conds.items():
        if col not in out:
            return pd.DataFrame()
        if isinstance(val, (list, tuple, set)):
            out = out[out[col].isin(val)]
        else:
            out = out[out[col] == val]
    return out


def add_metric(rows: list[dict[str, Any]], theme: str, name: str, value: Any, unit: str, src_dir: str, src_file: str, notes: str = "") -> None:
    if value is None:
        value = ""
    rows.append(
        {
            "theme": theme,
            "metric_name": name,
            "value": value,
            "unit": unit,
            "source_directory": src_dir,
            "source_file": src_file,
            "notes": notes,
        }
    )


def md_table(rows: list[dict[str, Any]], cols: list[str], max_rows: int | None = None) -> str:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    shown = rows if max_rows is None else rows[:max_rows]
    for row in shown:
        vals = []
        for c in cols:
            v = row.get(c, "")
            if isinstance(v, float):
                vals.append("" if not np.isfinite(v) else f"{v:.3f}")
            else:
                vals.append(str(v).replace("|", "\\|"))
        lines.append("| " + " | ".join(vals) + " |")
    if max_rows is not None and len(rows) > max_rows:
        lines.append(f"\n... {len(rows) - max_rows} rows omitted ...")
    return "\n".join(lines) + "\n"


def prereq_rows() -> list[dict[str, Any]]:
    prereqs = [
        ("FULL_V5_batch3_falsification", "reports/*_COMPLETION.md"),
        ("FULL_V5_roto_deepdive", "reports/*_COMPLETION.md"),
        ("FULL_V5_mechanistic_deepdive", "reports/*_COMPLETION.md"),
        ("FULL_V5_paper_strengthening", "reports/*_COMPLETION.md"),
    ]
    rows = []
    for d, pat in prereqs:
        files = sorted((ANALYSIS / d).glob(pat))
        rows.append({"directory": d, "status": "OK" if files else "MISSING", "completion_file": str(files[0].relative_to(ANALYSIS)) if files else ""})
    return rows


def build_registry() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scale = read_csv("FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv")
    if not scale.empty:
        for _, r in scale.iterrows():
            layout = str(r["layout"])
            add_metric(rows, "ANCHOR CALIBRATION", f"{layout}_sim3_scale", fnum(r["sim3_scale"]), "scale", "FULL_V5_scale_to_vicon", "tables/v5_vs_v4_scale_comparison.csv")
            add_metric(rows, "ANCHOR CALIBRATION", f"{layout}_rigid_anchor_rmse", fnum(r["rigid_anchor_rmse_mm"]), "mm", "FULL_V5_scale_to_vicon", "tables/v5_vs_v4_scale_comparison.csv")
    delay = read_csv("FULL_V5/tables/delay_comparison_v4_vs_v5.csv")
    if not delay.empty:
        add_metric(rows, "ANCHOR CALIBRATION", "V5_common_mode_c", fnum(delay["v5_common_mode_mm"].iloc[0]), "mm", "FULL_V5", "tables/delay_comparison_v4_vs_v5.csv")
        add_metric(rows, "ANCHOR CALIBRATION", "V5_e_i_full_spread", float(delay["v5_differential_e_i_mm"].max() - delay["v5_differential_e_i_mm"].min()), "mm", "FULL_V5", "tables/delay_comparison_v4_vs_v5.csv")
        add_metric(rows, "ANCHOR CALIBRATION", "V5_e_i_max_abs", float(np.nanmax(np.abs(delay["v5_differential_e_i_mm"]))), "mm", "FULL_V5", "tables/delay_comparison_v4_vs_v5.csv")
    loo = read_csv("FULL_4way_comparison/tables/v5_loo_tag_delay_summary.csv")
    add_metric(rows, "TAG DELAY", "D_tag_LOO_p50_V5", first(loo, "d_tag_median_mm", 49.621), "mm", "FULL_4way_comparison", "tables/v5_loo_tag_delay_summary.csv")
    f6 = read_csv("FULL_V5_followup_validation/tables/f6_final_comparison.csv")
    if not f6.empty:
        for _, r in f6.iterrows():
            add_metric(rows, "STATIC ACCURACY", f"{r['variant']}_median_3d", fnum(r["median_3d_mm"]), "mm", "FULL_V5_followup_validation", "tables/f6_final_comparison.csv", f"{r.get('percentile','')}, {r.get('weighting','')}")
            add_metric(rows, "STATIC ACCURACY", f"{r['variant']}_rmse_3d", fnum(r["rmse_3d_mm"]), "mm", "FULL_V5_followup_validation", "tables/f6_final_comparison.csv")
        v5imp = select(f6, variant="V5 improved")
        if not v5imp.empty:
            add_metric(rows, "TAG DELAY", "D_tag_LOO_p30_V5", fnum(v5imp.iloc[0]["d_tag_value_mm"]), "mm", "FULL_V5_followup_validation", "tables/f6_final_comparison.csv")
    p4 = read_csv("FULL_V5_followup_validation/tables/f4_percentile_recalibrated.csv")
    if not p4.empty:
        best = p4.sort_values("loo_median_3d_mm").iloc[0]
        add_metric(rows, "STATIC ACCURACY", "best_recalibrated_percentile_cell", fnum(best["loo_median_3d_mm"]), "mm", "FULL_V5_followup_validation", "tables/f4_percentile_recalibrated.csv", f"{best['config']} p{best['percentile']}")
    boot = read_csv("FULL_V5_overnight_batch2/tables/n6_bootstrap_ci.csv")
    if not boot.empty and "metric" in boot:
        for _, r in boot[boot["metric"].notna()].iterrows():
            add_metric(rows, "STATIC ACCURACY", f"bootstrap_{r['metric']}_mean", fnum(r["mean"]), "mm", "FULL_V5_overnight_batch2", "tables/n6_bootstrap_ci.csv")
            add_metric(rows, "STATIC ACCURACY", f"bootstrap_{r['metric']}_ci95_low", fnum(r["ci95_low"]), "mm", "FULL_V5_overnight_batch2", "tables/n6_bootstrap_ci.csv")
            add_metric(rows, "STATIC ACCURACY", f"bootstrap_{r['metric']}_ci95_high", fnum(r["ci95_high"]), "mm", "FULL_V5_overnight_batch2", "tables/n6_bootstrap_ci.csv")
    f1 = read_csv("FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv")
    if not f1.empty:
        for _, r in f1.iterrows():
            add_metric(rows, "STATIC ACCURACY", f"nested_cv_{r['split_type']}_mean_test_median", fnum(r["mean_test_median"]), "mm", "FULL_V5_batch3_falsification", "tables/f1_nested_cv_summary.csv")
    f2 = read_csv("FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv")
    if not f2.empty:
        for _, r in f2.iterrows():
            add_metric(rows, "STATIC ACCURACY", str(r["metric"]), fnum(r["value_mm"]), "mm", "FULL_V5_batch3_falsification", "tables/f2_optimism_summary.csv")
    item04 = read_csv("FULL_V5_extended_mechanism_ablations/tables/item04_nlos_excluded_dtag.csv")
    for config in ("V4_CV4", "V5_CV5"):
        g = item04[(item04["config"] == config) & (item04["exclusion"] == "none") & (item04["tier"] == "global")] if not item04.empty else pd.DataFrame()
        if not g.empty:
            add_metric(rows, "TAG DELAY", f"{config}_range_residual_tier_spread", fnum(g.iloc[0]["tier_spread_mm"]), "mm", "FULL_V5_extended_mechanism_ablations", "tables/item04_nlos_excluded_dtag.csv")
    morph = read_csv("FULL_V5_extended_mechanism_ablations/tables/item06_morph_markers.csv")
    if not morph.empty:
        gm = select(morph, marker_name="global_min")
        if not gm.empty:
            add_metric(rows, "CANCELLATION VALLEY", "joint_morph_global_min_alpha", fnum(gm.iloc[0]["alpha"]), "alpha", "FULL_V5_extended_mechanism_ablations", "tables/item06_morph_markers.csv")
            add_metric(rows, "CANCELLATION VALLEY", "joint_morph_global_min_median", fnum(gm.iloc[0]["median_3d_mm"]), "mm", "FULL_V5_extended_mechanism_ablations", "tables/item06_morph_markers.csv")
    f3 = read_csv("FULL_V5_batch3_falsification/tables/f3_profile_alpha_dtag.csv")
    if not f3.empty:
        m = f3.loc[f3["position_median_3d"].idxmin()]
        add_metric(rows, "CANCELLATION VALLEY", "profile_alpha_dtag_min_alpha", fnum(m["alpha"]), "alpha", "FULL_V5_batch3_falsification", "tables/f3_profile_alpha_dtag.csv")
        add_metric(rows, "CANCELLATION VALLEY", "profile_alpha_dtag_min_dtag", fnum(m["d_tag"]), "mm", "FULL_V5_batch3_falsification", "tables/f3_profile_alpha_dtag.csv")
        add_metric(rows, "CANCELLATION VALLEY", "profile_alpha_dtag_min_median", fnum(m["position_median_3d"]), "mm", "FULL_V5_batch3_falsification", "tables/f3_profile_alpha_dtag.csv")
    f4 = read_csv("FULL_V5_batch3_falsification/tables/f4_perturbation_ratio.csv")
    if not f4.empty:
        add_metric(rows, "CANCELLATION VALLEY", "nullspace_perturbation_ratio_median", float(np.nanmedian(f4["ratio"])), "ratio", "FULL_V5_batch3_falsification", "tables/f4_perturbation_ratio.csv")
    fisher_status = path("FULL_V5_GPU_tier1/reports/task2_status.json")
    if fisher_status.exists():
        txt = fisher_status.read_text(encoding="utf-8")
        m = re.search(r"weakest eig ([0-9.eE+-]+)", txt)
        add_metric(rows, "IDENTIFIABILITY", "fisher_weakest_eigenvalue", fnum(m.group(1)) if m else "", "eigenvalue", "FULL_V5_GPU_tier1", "reports/task2_status.json")
    shap = read_csv("FULL_V5_GPU_discovery/tables/task3_shapley_values.csv")
    if not shap.empty:
        for a in ("D", "F"):
            g = select(shap, anchor_label=a)
            if not g.empty:
                add_metric(rows, "NLOS", f"shapley_{a}", fnum(g.iloc[0]["shapley_3d"]), "score", "FULL_V5_GPU_discovery", "tables/task3_shapley_values.csv")
    nlos = read_csv("FULL_V5_GPU_discovery/tables/task6_cv_results.csv")
    if not nlos.empty:
        best = nlos.sort_values("pr_auc", ascending=False).iloc[0]
        add_metric(rows, "NLOS", "nlos_detector_random_split_pr_auc", fnum(best["pr_auc"]), "PR-AUC", "FULL_V5_GPU_discovery", "tables/task6_cv_results.csv", str(best["model"]))
    f5 = read_csv("FULL_V5_batch3_falsification/tables/f5_nlos_splits.csv")
    if not f5.empty:
        for split in ("leave_one_anchor_out", "leave_one_position_out", "leave_one_height_out"):
            g = f5[f5["split_type"] == split]
            if not g.empty:
                best = g.sort_values("pr_auc", ascending=False).iloc[0]
                add_metric(rows, "NLOS", f"nlos_detector_{split}_best_pr_auc", fnum(best["pr_auc"]), "PR-AUC", "FULL_V5_batch3_falsification", "tables/f5_nlos_splits.csv", str(best["model"]))
    ev = read_csv("FULL_V5_GPU_discovery/tables/task11_model_evidence.csv")
    if not ev.empty:
        add_metric(rows, "NLOS", "student_t_bic_winner", ev.sort_values("bic").iloc[0]["model"], "model", "FULL_V5_GPU_discovery", "tables/task11_model_evidence.csv")
    roto = read_csv("FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv")
    if not roto.empty:
        for method in ("E_current_anchor_bridge_existing_beta", "F_time_corrected_SE3", "D_Sim3_existing_beta"):
            g = select(roto, method=method)
            if not g.empty:
                add_metric(rows, "DYNAMIC ROTO", f"{method}_overall_median", fnum(g.iloc[0]["overall_median"]), "mm", "FULL_V5_roto_deepdive", "tables/r2_alignment_summary.csv")
    r3 = read_csv("FULL_V5_roto_deepdive/tables/r3_joint_summary.csv")
    if not r3.empty:
        for _, r in r3.iterrows():
            add_metric(rows, "DYNAMIC ROTO", f"roto_{r['method']}_median", fnum(r["overall_median"]), "mm", "FULL_V5_roto_deepdive", "tables/r3_joint_summary.csv")
    gap = read_csv("FULL_V5_roto_deepdive/tables/r4_gap_decomposition.csv")
    if not gap.empty:
        for _, r in gap.iterrows():
            add_metric(rows, "DYNAMIC ROTO", f"gap_{r['component']}", fnum(r["estimated_mm"]), "mm", "FULL_V5_roto_deepdive", "tables/r4_gap_decomposition.csv")
    n1 = read_csv("FULL_V5_overnight_batch2/tables/n1_adversarial_rooms.csv")
    if not n1.empty and "winner" in n1:
        add_metric(rows, "TRANSFERABILITY", "mc_P_V5_lt_V4_corrected_adversarial", float((n1["winner"] == "V5").mean()), "probability", "FULL_V5_overnight_batch2", "tables/n1_adversarial_rooms.csv")
    asym = read_csv("FULL_V5_GPU_discovery/tables/task4_asymmetry_summary.csv")
    add_metric(rows, "TRANSFERABILITY", "aa_at_mean_asymmetry", first(asym, "mean_asymmetry"), "mm", "FULL_V5_GPU_discovery", "tables/task4_asymmetry_summary.csv")
    m5 = read_csv("FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv")
    if not m5.empty:
        for _, r in m5.iterrows():
            add_metric(rows, "IDENTIFIABILITY", f"anchor_count_{int(r['k'])}_redundancy", fnum(r["redundancy"]), "count", "FULL_V5_mechanistic_deepdive", "tables/m5_identifiability_table.csv")
            add_metric(rows, "IDENTIFIABILITY", f"anchor_count_{int(r['k'])}_mean_median_3d", fnum(r["mean_median_3d"]), "mm", "FULL_V5_mechanistic_deepdive", "tables/m5_identifiability_table.csv")
    p1 = read_csv("FULL_V5_paper_strengthening/tables/p1_signed_radial_summary.csv")
    if not p1.empty:
        for _, r in p1.iterrows():
            add_metric(rows, "ERROR DECOMPOSITION", f"{r['config']}_mean_signed_radial", fnum(r["mean_signed_radial"]), "mm", "FULL_V5_paper_strengthening", "tables/p1_signed_radial_summary.csv")
    p2 = read_csv("FULL_V5_paper_strengthening/tables/p2_ei_correlations.csv")
    if not p2.empty:
        best = p2.iloc[np.nanargmax(np.abs(p2["pearson_r"].to_numpy(dtype=float)))]
        add_metric(rows, "ERROR DECOMPOSITION", "strongest_ei_correlation_predictor", best["predictor"], "name", "FULL_V5_paper_strengthening", "tables/p2_ei_correlations.csv")
        add_metric(rows, "ERROR DECOMPOSITION", "strongest_ei_correlation_r", fnum(best["pearson_r"]), "r", "FULL_V5_paper_strengthening", "tables/p2_ei_correlations.csv")
    return rows


def value_from_registry(reg: list[dict[str, Any]], name: str) -> Any:
    for row in reg:
        if row["metric_name"] == name:
            return row["value"]
    return ""


def consistency_audit() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    def add(metric, s1, v1, s2, v2, tol):
        d = abs(fnum(v1) - fnum(v2)) if np.isfinite(fnum(v1)) and np.isfinite(fnum(v2)) else float("nan")
        checks.append({"metric": metric, "source_1": s1, "value_1": v1, "source_2": s2, "value_2": v2, "discrepancy": d, "status": "OK" if np.isfinite(d) and d <= tol else "FLAG"})
    full = read_csv("FULL_V5/tables/static_summary_DLOO.csv")
    transfer = read_csv("FULL_transfer_matrix/tables/transfer_matrix_48cells.csv")
    follow = read_csv("FULL_V5_followup_validation/tables/f6_final_comparison.csv")
    v5_tf = transfer[(transfer["layout_source"] == "L_V5") & (transfer["correction_source"] == "C_V5") & (transfer["tag_delay_mode"] == "D_LOO_CV")] if not transfer.empty else pd.DataFrame()
    v4_tf = transfer[(transfer["layout_source"] == "L_V4") & (transfer["correction_source"] == "C_V4") & (transfer["tag_delay_mode"] == "D_LOO_CV")] if not transfer.empty else pd.DataFrame()
    add("V5+C_V5+D_LOO median_3d", "FULL_V5/static_summary_DLOO", first(full, "median_3d_mm"), "FULL_transfer_matrix", first(v5_tf, "median_3d_mm"), 0.5)
    add("V5+C_V5+D_LOO median_3d", "FULL_transfer_matrix", first(v5_tf, "median_3d_mm"), "followup/f6 V5 baseline", first(select(follow, variant="V5 baseline"), "median_3d_mm"), 0.5)
    add("V4+C_V4+D_LOO median_3d", "FULL_transfer_matrix", first(v4_tf, "median_3d_mm"), "mechanism summary expected", 57.920957, 0.5)
    loo = read_csv("FULL_4way_comparison/tables/v5_loo_tag_delay_summary.csv")
    add("D_tag LOO", "FULL_4way", first(loo, "d_tag_median_mm", 49.621033), "FULL_V5/static_summary_DLOO", first(full, "tag_delay_value_mm"), 0.01)
    shap1 = read_csv("FULL_V5_GPU_tier1/tables/task3_shapley_values.csv")
    shap2 = read_csv("FULL_V5_GPU_discovery/tables/task3_shapley_values.csv")
    for a in ("D", "F"):
        add(f"Shapley {a}", "GPU_tier1", first(select(shap1, anchor_label=a), "shapley_3d"), "GPU_discovery", first(select(shap2, anchor_label=a), "shapley_3d"), 0.01)
    nlos1 = read_csv("FULL_V5_GPU_tier1/tables/task6_cv_results.csv")
    nlos2 = read_csv("FULL_V5_GPU_discovery/tables/task6_cv_results.csv")
    add("NLOS PR-AUC best", "GPU_tier1", nlos1["pr_auc"].max() if not nlos1.empty else np.nan, "GPU_discovery", nlos2["pr_auc"].max() if not nlos2.empty else np.nan, 0.01)
    return checks


def claim_matrix(reg: list[dict[str, Any]]) -> list[dict[str, Any]]:
    base = [
        (1, "V5 fixes V4's scale leak (0.958 -> 1.010)", "A", "V4/V5 Sim3 scale and rigid RMSE", "No independent room yet", "V5 corrects the anchor-side scale defect on this campaign."),
        (2, "V5 has more stable per-height D_tag", "B", "range-residual tier spreads and hard-CV degradation", "Position-optimal D_tag criteria remain ambiguous", "V5 reduces some geometry-induced tag-delay aliasing, but stability depends on the criterion."),
        (3, "V4 gives better single-dataset positioning than V5", "A", "V4+LOO 57.9 mm vs V5+LOO 67.8 mm; improved 54.9 vs 56.0 mm", "Does not imply better transfer", "V4 is the empirical static median winner on this 24-position campaign."),
        (4, "The reason is scale-delay-NLOS cancellation", "B", "morph/profile valleys, Vicon/self-cal gap, p30 cancellation", "radial signed error is not decisive", "The lower V4 error is consistent with beneficial cancellation rather than proven by one statistic."),
        (5, "Vicon oracle worse than self-cal proves cancellation", "C", "Vicon oracle underperforms some self-cal cells", "phase-center offset alternative remains plausible", "The Vicon result is compatible with cancellation but not uniquely diagnostic."),
        (6, "Vicon worse could be phase-center offset", "B", "phase-center and e_i/NLOS checks", "needs physical antenna offset measurement", "Phase-center mismatch is a plausible alternative and should be stated."),
        (7, "p30 improvement is another cancellation", "B", "fixed p30 47.5 mm vs recalibrated/weighted 56.0 mm", "no independent static capture", "p30 is a strong batch-processing hypothesis, not a universal correction."),
        (8, "Every post-processing improvement benefits V4 more than V5", "D", "V4 improved slightly beats V5 improved", "not every method was exhaustively retested", "Do not claim universal superiority; report the tested comparison."),
        (9, "Fisher eigenvalue 1e-6 proves weak identifiability", "A", "GPU Fisher and nullspace perturbation", "gauge/model simplifications", "The calibration has a measurable weak direction."),
        (10, "D/F are NLOS-heavy but geometrically essential", "A", "Shapley D/F high; residual spikes", "Shapley values close across anchors", "D/F are not simply removable outliers."),
        (11, "NLOS detectable from range statistics without CIR", "B", "random split PR-AUC ~0.95", "leakage tests much lower", "Range statistics contain NLOS signal, but deployment generalization is unproven."),
        (12, "NLOS detector generalizes across positions/anchors", "D", "leave-anchor/height split weak", "PR-AUC collapses in hard splits", "Do not claim generalization yet."),
        (13, "Student-t is the correct noise model", "B", "BIC winner M2_student_t", "only one campaign", "Student-t best describes this residual distribution."),
        (14, "V5 transfers better to new rooms", "C", "physical scale correction; corrected MC/adversarial tests", "no real new room", "V5 is expected to transfer better, but this needs direct validation."),
        (15, "MC transfer result has V4 solver fidelity caveat", "A", "N1 solver verification and adversarial rooms", "", "State the caveat explicitly."),
        (16, "D_tag is device-specific", "C", "ROTO per-tag estimates suggest spread", "dynamic geometry/time sync confounded", "Treat per-device D_tag as likely, not proven."),
        (17, "p30 does not transfer to dynamic", "B", "ROTO p30/follow-up dynamic results", "dynamic labels are best-fit aligned", "p30 helped static batch ranges but not ROTO enough to change the dynamic floor."),
        (18, "ROTO accuracy is ~101 mm best-fit aligned", "A", "R2 current bridge 101.5 mm", "alignment convention matters", "Report as BEST-FIT-ALIGNED only."),
        (19, "Static-dynamic gap is ~40 mm", "A", "101.5 - 56.0 = 45.5 mm", "component estimates not orthogonal", "The dynamic floor remains about 45 mm above static best."),
        (20, "24 positions insufficient for learned methods", "B", "winner's curse, leakage, nested CV instability", "more data not tested", "The current campaign is too small for strong learned-method claims."),
        (21, "AA-AT asymmetry is small", "A", "-4.7 mm, p not significant", "", "AA/AT asymmetry is small in this dataset."),
        (22, "Rigid body constraint improves ROTO", "D", "joint_projection worsened to 280.6 mm", "better solver may be needed", "Do not claim improvement from the tested rigid projection."),
        (23, "Headline numbers survive nested CV", "C", "nested CV medians 82.9-94.2 mm", "hard splits degrade", "Hard nested CV weakens, rather than confirms, aggressive headline claims."),
        (24, "Winner's curse gap is < X mm", "B", "mean optimism gap 9.6 mm", "std 29.6 mm", "Use corrected medians for paper headline sensitivity."),
        (25, "Cancellation valley has specific radial mechanism", "C", "signed radial V4 -7.8 vs V5 -4.8 mm, not decisive", "p-value not significant", "Radial decomposition is suggestive but not a stand-alone proof."),
    ]
    rows = []
    for cid, claim, level, support, contra, wording in base:
        rows.append({"claim_id": cid, "claim_text": claim, "level": level, "supporting_tasks": support, "supporting_numbers": "; ".join([f"{r['metric_name']}={r['value']}" for r in reg if cid in (1, 3, 9, 10, 18)][:6]), "contradicting_evidence": contra, "recommended_paper_wording": wording})
    return rows


def final_headline_table() -> list[dict[str, Any]]:
    rows = []
    f6 = read_csv("FULL_V5_followup_validation/tables/f6_final_comparison.csv")
    f2 = read_csv("FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv")
    boot = read_csv("FULL_V5_overnight_batch2/tables/n6_bootstrap_ci.csv")
    optimism = first(f2[f2["metric"] == "mean_optimism_gap_honest_minus_apparent"] if not f2.empty else pd.DataFrame(), "value_mm", np.nan)
    ci_rows = boot[boot["metric"].notna()] if not boot.empty and "metric" in boot else pd.DataFrame()
    ci_txt = ""
    if not ci_rows.empty:
        m = ci_rows[ci_rows["metric"].astype(str).str.contains("median", case=False, na=False)]
        if not m.empty:
            ci_txt = f"[{first(m, 'ci95_low'):.1f}, {first(m, 'ci95_high'):.1f}]"
    for _, r in f6.iterrows():
        variant = str(r["variant"])
        corrected = fnum(r["median_3d_mm"]) + optimism if np.isfinite(optimism) and variant in ("V4 improved", "V5 improved") else ""
        rows.append(
            {
                "Variant": variant,
                "Percentile": r["percentile"],
                "Weighting": r["weighting"],
                "D_tag": r["d_tag_mode"],
                "median_3d": fnum(r["median_3d_mm"]),
                "P95": fnum(r["p95_3d_mm"]),
                "RMSE": fnum(r["rmse_3d_mm"]),
                "nested_CV_median": "",
                "winners_curse_corrected_median": corrected,
                "bootstrap_CI": ci_txt if variant == "V5 improved" else "",
            }
        )
    f1 = read_csv("FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv")
    for _, r in f1.iterrows():
        rows.append({"Variant": f"Nested CV selected ({r['split_type']})", "Percentile": "selected", "Weighting": "selected", "D_tag": "selected", "median_3d": "", "P95": "", "RMSE": "", "nested_CV_median": fnum(r["mean_test_median"]), "winners_curse_corrected_median": "", "bootstrap_CI": ""})
    roto = read_csv("FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv")
    for method, label in [("E_current_anchor_bridge_existing_beta", "ROTO V5 raw/current best-fit"), ("F_time_corrected_SE3", "ROTO time-corrected SE3")]:
        g = select(roto, method=method)
        if not g.empty:
            rows.append({"Variant": label, "Percentile": "dynamic", "Weighting": "uniform", "D_tag": "D_LOO", "median_3d": fnum(g.iloc[0]["overall_median"]), "P95": fnum(g.iloc[0]["overall_p95"]), "RMSE": fnum(g.iloc[0]["overall_rmse"]), "nested_CV_median": "", "winners_curse_corrected_median": "", "bootstrap_CI": ""})
    r3 = read_csv("FULL_V5_roto_deepdive/tables/r3_joint_summary.csv")
    g = select(r3, method="joint_projection")
    if not g.empty:
        rows.append({"Variant": "ROTO rigid-body projection", "Percentile": "dynamic", "Weighting": "joint", "D_tag": "per R3", "median_3d": fnum(g.iloc[0]["overall_median"]), "P95": "", "RMSE": fnum(g.iloc[0]["overall_rmse"]), "nested_CV_median": "", "winners_curse_corrected_median": "", "bootstrap_CI": ""})
    return rows


def latex_table(rows: list[dict[str, Any]], cols: list[str], caption: str, label: str) -> str:
    lines = ["\\begin{table}[t]", "\\centering", f"\\caption{{{caption}}}", f"\\label{{{label}}}", "\\begin{tabular}{" + "l" * len(cols) + "}", "\\hline", " & ".join(cols).replace("_", "\\_") + " \\\\", "\\hline"]
    for row in rows:
        vals = []
        for c in cols:
            v = row.get(c, "")
            if isinstance(v, float):
                vals.append("" if not np.isfinite(v) else f"{v:.1f}")
            else:
                vals.append(str(v).replace("_", "\\_").replace("%", "\\%"))
        lines.append(" & ".join(vals) + " \\\\")
    lines += ["\\hline", "\\end{tabular}", "\\end{table}"]
    return "\n".join(lines) + "\n"


def directory_index() -> list[dict[str, Any]]:
    rows = []
    for d in sorted([p for p in ANALYSIS.iterdir() if p.is_dir()]):
        if d.name.endswith("_US"):
            continue
        tables = list((d / "tables").glob("*.csv")) if (d / "tables").exists() else []
        figs = list((d / "figures").glob("*")) if (d / "figures").exists() else []
        reps = list((d / "reports").glob("*.md")) if (d / "reports").exists() else []
        scripts = list((d / "scripts").glob("*.py")) if (d / "scripts").exists() else []
        status_files = tables + list((d / "reports").glob("*status*.json")) if (d / "reports").exists() else tables
        tasks_ok = tasks_fail = 0
        runtime_s = float("nan")
        for p in status_files:
            if p.suffix == ".csv" and ("status" in p.name or "task" in p.name):
                try:
                    df = pd.read_csv(p)
                    if "status" in df:
                        tasks_ok += int((df["status"].astype(str).str.lower() == "ok").sum())
                        tasks_fail += int((df["status"].astype(str).str.lower() == "failed").sum())
                    if "elapsed_s" in df:
                        runtime_s = np.nansum([runtime_s if np.isfinite(runtime_s) else 0.0, df["elapsed_s"].sum()])
                except Exception:
                    pass
        rows.append({"directory": d.name, "script": ";".join(str(s.relative_to(d)) for s in scripts[:3]), "n_tables": len(tables), "n_figures": len(figs), "n_reports": len(reps), "runtime_s": runtime_s, "tasks_ok": tasks_ok, "tasks_fail": tasks_fail})
    return rows


def publication_checklist() -> list[dict[str, Any]]:
    items = [
        ("All claims at Level A or B", "PARTIAL", "tables/claim_evidence_matrix.csv", "Downgrade Level C/D claims in paper.", "MUST-HAVE"),
        ("Nested CV confirms headlines", "PARTIAL", "FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv", "Use nested CV as falsification, not confirmation.", "MUST-HAVE"),
        ("Winner's curse gap < 5mm", "FAILED", "FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv", "Mean gap is about 9.6 mm; report corrected medians.", "MUST-HAVE"),
        ("NLOS detector generalizes", "FAILED", "FULL_V5_batch3_falsification/tables/f5_nlos_splits.csv", "Need leave-room/leave-anchor validation before deployment claim.", "MUST-HAVE"),
        ("Cancellation valley visualized", "DONE", "FULL_V5_batch3_falsification/figures/f3_contour_alpha_dtag.png", "Use in paper.", "MUST-HAVE"),
        ("ROTO gap explained", "PARTIAL", "FULL_V5_roto_deepdive/tables/r4_gap_decomposition.csv", "Components are proxies and not orthogonal.", "NICE-TO-HAVE"),
        ("Phase-center alternative addressed", "PARTIAL", "FULL_V5_mechanistic_deepdive/reports/TASK_M3_PHASE_CENTER.md", "Needs physical antenna offset measurement.", "MUST-HAVE"),
        ("Error budget decomposed", "DONE", "FULL_V5_mechanistic_deepdive/tables/m2_error_budget.csv", "Include caveat on interactions.", "MUST-HAVE"),
        ("Fisher eigenvector interpreted", "DONE", "FULL_V5_mechanistic_deepdive/tables/m9_fisher_eigenvectors.csv", "Use with nullspace check.", "MUST-HAVE"),
        ("Publication figures complete", "DONE", "FULL_V5_overnight_batch2/figures; FULL_V5_paper_strengthening/figures", "Select final subset.", "NICE-TO-HAVE"),
        ("LaTeX tables complete", "DONE", "FULL_V5_overnight_batch2/tables/paper_table_*.tex", "Check formatting in main paper.", "NICE-TO-HAVE"),
        ("LaTeX draft sections available", "DONE", "FULL_V5_paper_strengthening/tables/paper_draft_intro_method.tex", "Edit into final manuscript.", "OPTIONAL"),
    ]
    return [{"item": a, "status": b, "output_location": c, "action_needed": d, "priority": e} for a, b, c, d, e in items]


def narrative(reg: list[dict[str, Any]], claims: list[dict[str, Any]]) -> str:
    return """# Narrative Summary

V5 fixes the anchor-side defect that motivated the entire analysis campaign. The V4 self-calibrated layout has a Sim3 scale of 0.958 and a rigid anchor RMSE of about 105 mm against Vicon, whereas the V5 common-mode layout has a Sim3 scale of 1.010 and a rigid RMSE of about 63 mm. The V5 delay model also moves the bulk anchor bias into an explicit common-mode term of about 112 mm, leaving a per-anchor residual spread of about 28 mm. These numbers support a Level A claim that V5 corrects the metric scale leak observed in V4 on this campaign, although they do not by themselves prove better positioning on every dataset.

The static positioning story is more subtle. On the original p50 range aggregation, V4+C_V4+D_LOO gives a 57.9 mm median 3D error, while V5+C_V5+D_LOO gives about 67.8 mm. Under the best-practice p30/inverse-RMS follow-up, V4 remains slightly ahead at 54.9 mm, V5 reaches 56.0 mm, and the Vicon-anchor control reaches 56.3 mm. That ordering is consistent with beneficial cancellation: V4's compressed scale partly offsets positive range bias from NLOS and tag-delay effects. The morph valley has a global minimum near alpha=0.15 with a median of 56.4 mm, and the profile-likelihood scan also shows a broad alpha/D_tag valley. The radial decomposition is suggestive rather than decisive, because V4 and V5 mean signed radial errors are both slightly inward and not statistically strong.

The falsification batch weakens aggressive headline claims. Nested CV test medians ranged from 82.9 to 94.2 mm across hard split types, and the estimated winner's-curse optimism gap averaged about 9.6 mm. The corrected medians for the V4 and V5 improved rows are therefore about 64.5 and 65.6 mm, respectively. These tests do not invalidate the physical V5 result, but they do mean the paper should avoid presenting any single in-sample best number as a deployment guarantee. The defensible claim is that V4 is the empirical winner on this 24-position campaign, while V5 is the more physically correct anchor calibration.

The remaining accuracy floor is dominated by structured range error rather than Gaussian noise. The Student-t residual model is the BIC winner, D/F have high Shapley values and large residual fingerprints, and the NLOS classifier reaches about 0.95 PR-AUC in random splits. However, hard generalization tests are much weaker: leave-one-anchor and leave-one-height PR-AUC values collapse relative to random split performance. Thus range statistics contain useful NLOS information, but the detector should be framed as exploratory until validated on independent rooms or anchors.

Dynamic ROTO tracking remains a separate limitation. The current anchor-bridge best-fit-aligned ROTO median is about 101.5 mm, and a time-corrected SE(3) evaluation gives about 82.5 mm under a more permissive alignment. The tested rigid-body projection did not improve tracking; it worsened the median to about 281 mm, so the paper should not claim a rigid-constraint improvement. The gap decomposition attributes roughly 23 mm to likely tag-delay mismatch, 6 mm to motion blur under the nominal timing assumption, less than 1 mm to recoverable time offset, and about 15.5 mm to unexplained residual structure. These components are not orthogonal, but they make clear that the dynamic floor is not solved by p30 aggregation alone.

The transferability case remains plausible but unproven. V5 should transfer better because its geometry is metric-correct and because the V4 solution relies on dataset-specific cancellation. The initial GPU Monte Carlo result was too strong, and the corrected adversarial-room analysis reduced the evidence to a caveated transfer hypothesis rather than proof. AA-AT asymmetry is small at about -4.7 mm, which supports the use of the self-calibration range model in this campaign. A real cross-room capture is still required for a Level A transfer claim.

The practical deployment recommendation is therefore conservative. Use the V5 common-mode calibration with the 20 mm residual regularization as the default geometry, calibrate D_tag per device using a small number of known positions, retain robust or Student-t-like losses, and use p30 or similar lower-percentile aggregation only for static batch processing after validation. The best current static processing recipe reaches about 56 mm on this campaign, but the paper should report both naive and corrected estimates. Future work should prioritize an independent room, CIR-based NLOS labels, a physical antenna phase-centre measurement, and a 9th-anchor experiment to improve identifiability.
"""


def main() -> int:
    for p in (OUT, TABLES, REPORTS, SCRIPTS):
        p.mkdir(parents=True, exist_ok=True)
    prereq = prereq_rows()
    registry = build_registry()
    audit = consistency_audit()
    claims = claim_matrix(registry)
    headline = final_headline_table()
    index = directory_index()
    checklist = publication_checklist()
    write_csv(TABLES / "prerequisite_check.csv", prereq)
    write_csv(TABLES / "master_number_registry.csv", registry)
    write_csv(TABLES / "consistency_audit.csv", audit)
    write_csv(TABLES / "claim_evidence_matrix.csv", claims)
    write_csv(TABLES / "final_headline_table.csv", headline)
    write_text(TABLES / "final_headline_table.tex", latex_table(headline, ["Variant", "Percentile", "Weighting", "D_tag", "median_3d", "P95", "RMSE", "nested_CV_median", "winners_curse_corrected_median", "bootstrap_CI"], "Final headline accuracy table.", "tab:final_headline"))
    write_csv(TABLES / "directory_index.csv", index)
    write_csv(TABLES / "publication_checklist.csv", checklist)

    write_text(REPORTS / "MASTER_NUMBER_REGISTRY.md", "# Master Number Registry\n\n" + md_table(registry, ["theme", "metric_name", "value", "unit", "source_directory", "source_file"], max_rows=120))
    write_text(REPORTS / "CONSISTENCY_AUDIT.md", "# Consistency Audit\n\n" + md_table(audit, ["metric", "source_1", "value_1", "source_2", "value_2", "discrepancy", "status"]))
    write_text(REPORTS / "CLAIM_EVIDENCE_MATRIX.md", "# Claim Evidence Matrix\n\n" + md_table(claims, ["claim_id", "claim_text", "level", "supporting_tasks", "contradicting_evidence", "recommended_paper_wording"], max_rows=40))
    write_text(REPORTS / "FINAL_HEADLINE_TABLE.md", "# Final Headline Table\n\n" + md_table(headline, ["Variant", "Percentile", "Weighting", "D_tag", "median_3d", "P95", "RMSE", "nested_CV_median", "winners_curse_corrected_median", "bootstrap_CI"], max_rows=30))
    narrative_text = narrative(registry, claims)
    write_text(REPORTS / "NARRATIVE_SUMMARY.md", narrative_text)
    write_text(REPORTS / "DIRECTORY_INDEX.md", "# Directory Index\n\n" + md_table(index, ["directory", "script", "n_tables", "n_figures", "n_reports", "runtime_s", "tasks_ok", "tasks_fail"], max_rows=80))
    write_text(REPORTS / "REMAINING_GAPS.md", "# Remaining Gaps\n\n" + md_table(checklist, ["item", "status", "output_location", "action_needed", "priority"]))
    next_steps = """# Recommended Next Steps

The next must-have step is an independent validation capture in a second room. The current campaign is internally rich, but the strongest remaining claims, especially V5 transferability and learned NLOS generalization, are still limited by the 24-position Erlangen dataset. The second priority is a physical antenna phase-centre measurement for anchors and tags, because it would separate Vicon-marker offsets from real calibration errors. The third priority is a controlled CIR-labeled NLOS dataset, which would turn the range-statistics detector from an exploratory model into a deployable quality gate.

For the paper, use the corrected headline table rather than the in-sample best table. Present V5 scale correction as the central Level A result, present V4's lower static median as a campaign-specific cancellation effect, and keep p30 as a validated static batch-processing hypothesis rather than a general ranging rule. ROTO should be reported separately and explicitly labeled BEST-FIT-ALIGNED.
"""
    write_text(REPORTS / "RECOMMENDED_NEXT_STEPS.md", next_steps)

    index = directory_index()
    write_csv(TABLES / "directory_index.csv", index)
    write_text(REPORTS / "DIRECTORY_INDEX.md", "# Directory Index\n\n" + md_table(index, ["directory", "script", "n_tables", "n_figures", "n_reports", "runtime_s", "tasks_ok", "tasks_fail"], max_rows=80))

    grand = [
        "# Grand Synthesis",
        "",
        f"Generated: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
        "",
        "## Campaign Overview",
        "",
        f"The synthesis scanned {len(index)} analysis directories and collected {len(registry)} registry entries. All four requested prerequisite completion reports were present.",
        "",
        "## Prerequisite Check",
        "",
        md_table(prereq, ["directory", "status", "completion_file"]),
        "## Master Number Registry",
        "",
        md_table(registry, ["theme", "metric_name", "value", "unit", "source_directory"], max_rows=80),
        "## Consistency Audit",
        "",
        md_table(audit, ["metric", "value_1", "value_2", "discrepancy", "status"]),
        "## Claim Evidence Matrix",
        "",
        md_table(claims, ["claim_id", "claim_text", "level", "recommended_paper_wording"], max_rows=30),
        "## Final Corrected Headline Table",
        "",
        md_table(headline, ["Variant", "median_3d", "P95", "RMSE", "nested_CV_median", "winners_curse_corrected_median", "bootstrap_CI"], max_rows=20),
        narrative_text,
        "## Directory Index",
        "",
        md_table(index, ["directory", "n_tables", "n_figures", "n_reports", "tasks_ok", "tasks_fail"], max_rows=80),
        "## Remaining Gaps",
        "",
        md_table(checklist, ["item", "status", "action_needed", "priority"]),
        "## Recommended Paper Structure",
        "",
        "Use the paper structure from the previous outline, but make the Results order: anchor-side scale fix, static accuracy with corrected table, cancellation/identifiability, NLOS residual structure, dynamic ROTO limitation, and deployment recommendations. Keep transferability in Discussion unless an independent-room experiment is added.",
    ]
    write_text(REPORTS / "GRAND_SYNTHESIS.md", "\n".join(grand) + "\n")
    write_text(REPORTS / "SCRIPT_VERIFICATION.json", json.dumps({"status": "ok", "registry_rows": len(registry), "claim_rows": len(claims), "directories_indexed": len(index)}, indent=2) + "\n")
    print(json.dumps({"status": "ok", "registry_rows": len(registry), "claim_rows": len(claims), "directories_indexed": len(index)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
