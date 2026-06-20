#!/usr/bin/env python3
"""Generate the updated comprehensive experimental report.

This script is intentionally read-only with respect to existing analysis
directories. It writes only into FULL_V5_experimental_report/{report,tables,figures}.
"""

from __future__ import annotations

import math
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd


BASE = Path("/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official")
ANALYSIS = BASE / "Analysis" / "official_extra_analysis"
OUT = ANALYSIS / "FULL_V5_experimental_report"
REPORT_DIR = OUT / "report"
TABLE_DIR = OUT / "tables"
FIG_DIR = OUT / "figures"


def read_csv(rel: str) -> pd.DataFrame:
    return pd.read_csv(ANALYSIS / rel)


def rel_source(rel: str) -> str:
    return rel


def fmt(value, ndigits: int = 1, empty: str = "") -> str:
    if value is None:
        return empty
    try:
        if isinstance(value, str):
            return value
        if pd.isna(value):
            return empty
        return f"{float(value):.{ndigits}f}"
    except Exception:
        return str(value)


def fmt3(value) -> str:
    return fmt(value, 3)


def md_table(rows: list[dict], columns: list[str]) -> str:
    labels = columns
    out = ["| " + " | ".join(labels) + " |", "| " + " | ".join(["---"] * len(labels)) + " |"]
    for row in rows:
        vals = []
        for col in columns:
            val = row.get(col, "")
            if val is None or (isinstance(val, float) and math.isnan(val)):
                val = ""
            vals.append(str(val))
        out.append("| " + " | ".join(vals) + " |")
    return "\n".join(out)


def first_row(df: pd.DataFrame, **conds) -> pd.Series:
    mask = pd.Series([True] * len(df))
    for key, val in conds.items():
        mask &= df[key].astype(str).eq(str(val))
    sub = df[mask]
    if sub.empty:
        raise KeyError(f"No row for {conds}")
    return sub.iloc[0]


def safe_count_files(d: Path, suffix: str) -> int:
    if not d.exists():
        return 0
    return len(list(d.rglob(f"*{suffix}")))


def copy_figure(src_rel: str, dest_name: str, caption: str) -> dict:
    src = ANALYSIS / src_rel
    status = "missing"
    if src.exists():
        FIG_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, FIG_DIR / dest_name)
        status = "copied"
    return {"Copied figure": dest_name, "Caption": caption, "Source": src_rel, "Status": status}


def shorten_existing_source(src: str) -> str:
    marker = "official_extra_analysis/"
    if marker in src:
        return src.split(marker, 1)[1]
    return src


def section_between(text: str, start_marker: str, end_marker: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker)
    return text[start:end].strip()


def word_count(text: str) -> int:
    return len(text.split())


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)

    old_report_path = OUT / "report" / "EXPERIMENTAL_REPORT.md"
    old_report = old_report_path.read_text()
    core_sections = section_between(old_report, "## 1. Dataset", "## 12.A")

    core_sections = core_sections.replace(
        "The strongest static percentile diagnostic was V5 p30 at 47.5 mm before fair\n"
        "recalibration, but this result was later reclassified as another cancellation effect\n"
        "rather than a deployable universal correction [source:\n"
        "FULL_V5_extended_mechanism_ablations/tables/item21_range_percentile_sweep.csv;\n"
        "FULL_V5_followup_validation/tables/f4_percentile_recalibrated.csv].",
        "The strongest static percentile diagnostic was V5 p30 at 47.5 mm before fair\n"
        "recalibration, but this result was later reclassified as another cancellation effect\n"
        "rather than a deployable universal correction [source:\n"
        "FULL_V5_extended_mechanism_ablations/tables/item21_range_percentile_sweep.csv;\n"
        "FULL_V5_followup_validation/tables/f4_percentile_recalibrated.csv]. The later raw-frame\n"
        "campaign separated this issue from simple p30 aggregation: lower_trim_20 over the full\n"
        "per-link histogram reached 44.5 mm LOO with V5 and Huber30, while the transductive\n"
        "all-data p10 result was kept diagnostic [source:\n"
        "FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv;\n"
        "FULL_V5_rawframe_bruteforce_v3/tables/s2_top50_overall.csv].",
    )
    core_sections = core_sections.replace(
        "M3 measured V5 offset vectors relative to Vicon and found a mean magnitude of 56.8\n"
        "mm with direction resultant 0.09, meaning offsets were not coherently aligned [source:\n"
        "FULL_V5_mechanistic_deepdive/reports/MECHANISTIC_DEEPDIVE_COMPLETION.md]. M4 tested\n"
        "whether V5 e_i values were NLOS proxies. corr(e_i, rho_rms) was 0.08, and forcing all\n"
        "e_i to zero improved the median from 67.8 mm to 64.5 mm in that counterfactual\n"
        "[source: FULL_V5_mechanistic_deepdive/tables/m4_counterfactual.csv].",
        "M3 measured V5 offset vectors relative to Vicon and found a mean magnitude of 56.8\n"
        "mm with direction resultant 0.09, meaning offsets were not coherently aligned [source:\n"
        "FULL_V5_mechanistic_deepdive/reports/MECHANISTIC_DEEPDIVE_COMPLETION.md]. M4 tested\n"
        "whether V5 e_i values were NLOS proxies. corr(e_i, rho_rms) was 0.08, and forcing all\n"
        "e_i to zero improved the median from 67.8 mm to 64.5 mm in that counterfactual\n"
        "[source: FULL_V5_mechanistic_deepdive/tables/m4_counterfactual.csv]. The anchor\n"
        "lower-trim blind experiment later repeated the same direction under the raw-frame\n"
        "static estimator: p50 anchors with e_i=0 reached 43.2 mm, compared with 44.5 mm for\n"
        "the current p50/e_reg20 control, although the bootstrap P(new wins) was only 0.659\n"
        "[source: FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv;\n"
        "FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv].",
    )

    # Core existing tables.
    headline = read_csv("FULL_V5_final_gate/tables/g1_locked_headline.csv")
    claim_matrix = read_csv("FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv")
    registry = read_csv("FULL_V5_grand_synthesis/tables/master_number_registry.csv")

    # New raw-frame campaign tables.
    raw_v1 = read_csv("FULL_V5_rawframe_bruteforce/tables/b6_master_comparison.csv")
    raw_inv = read_csv("FULL_V5_rawframe_bruteforce_v2/tables/raw_data_inventory.csv")
    raw_checkpoint = read_csv("FULL_V5_rawframe_bruteforce_v2/tables/raw_loading_checkpoint.csv")
    v2_master = read_csv("FULL_V5_rawframe_bruteforce_v2/tables/b6_master_comparison.csv")
    raw_links = read_csv("FULL_V5_rawframe_bruteforce_v3/tables/raw_link_inventory.csv")
    s2_best_est = read_csv("FULL_V5_rawframe_bruteforce_v3/tables/s2_best_per_estimator.csv")
    s2_best_geo = read_csv("FULL_V5_rawframe_bruteforce_v3/tables/s2_best_per_geometry.csv")
    s2_top = read_csv("FULL_V5_rawframe_bruteforce_v3/tables/s2_top50_overall.csv")
    s3_honest = read_csv("FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv")
    s5_boot = read_csv("FULL_V5_rawframe_bruteforce_v3/tables/s5_bootstrap_summary.csv").iloc[0]
    s6_ladder = read_csv("FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv")
    s6_level = read_csv("FULL_V5_rawframe_bruteforce_v3/tables/s6_level_decision.csv").iloc[0]

    # Anchor lower-trim tables.
    ia = read_csv("FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv")
    l2 = read_csv("FULL_V5_anchor_lower_trim/tables/l2_anchor_solver_results.csv")
    l3 = read_csv("FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv")
    l5 = read_csv("FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv").iloc[0]

    # New key rows.
    raw_oracle = first_row(s6_ladder, method="B0 oracle lower bound")
    raw_honest = first_row(s6_ladder, method="Stage3 best lower_trim_20 / V5 / huber30")
    raw_stage2 = first_row(s6_ladder, method="Stage2 best all-data")
    honest_best = s3_honest.sort_values("loo_median").iloc[0]
    honest_v4 = s3_honest[s3_honest["geometry"].eq("V4")].sort_values("loo_median").iloc[0]
    honest_vicon = s3_honest[s3_honest["geometry"].eq("Vicon")].sort_values("loo_median").iloc[0]
    transductive_best = s2_top.iloc[0]
    lower_trim_e0 = first_row(l3, range_method="lower_trim_20", e_setting="E2_e_zero")
    p50_e0 = first_row(l3, range_method="p50", e_setting="E2_e_zero")
    p50_reg5 = first_row(l3, range_method="p50", e_setting="E1_e_reg5")
    p50_control = first_row(l3, range_method="p50_control", e_setting="V5_current_e_reg20")
    p50_reg20 = first_row(l3, range_method="p50", e_setting="E0_e_reg20")

    raw_rows = int(raw_inv["n_rows"].sum())
    raw_valid = int(raw_inv["n_valid_rows"].sum())
    raw_expected = 24 * 8 * 1200
    raw_ratio = raw_rows / raw_expected
    raw_static_files = len(raw_inv)
    id01_frames = int(raw_checkpoint.iloc[0]["n_frames_loaded"])
    total_raw_link_rows = int(raw_links["n"].sum())
    raw_link_median_n = float(raw_links["n"].median())
    aa_raw_valid = int(ia["n_frames"].sum())
    aa_median_frames = float(ia["n_frames"].median())
    ia_skew = float(ia["skewness"].mean())
    ia_p50_ltrim20 = float(ia["p50_minus_lower_trim20_mm"].mean())

    # Updated headline table.
    headline_rows = []
    for _, row in headline.iterrows():
        median_cell = fmt(row["median_3d"])
        if str(row["Row"]) == "H" and str(row["Variant"]) == "V5 bootstrap CI":
            median_cell = str(row["Description"]).replace("95% CI ", "")
        headline_rows.append(
            {
                "Row": row["Row"],
                "Variant": row["Variant"],
                "Median 3D mm": median_cell,
                "P95 mm": fmt(row["P95"]),
                "RMSE mm": fmt(row["RMSE"]),
                "Evaluation": row["evaluation_type"],
                "New": "",
                "Source": shorten_existing_source(str(row["source_csv"])),
            }
        )
    headline_rows.extend(
        [
            {
                "Row": "O",
                "Variant": "lower_trim_20 + Huber30 + V5",
                "Median 3D mm": fmt(honest_best["loo_median"]),
                "P95 mm": fmt(honest_best["loo_p95"]),
                "RMSE mm": fmt(honest_best["loo_rmse"]),
                "Evaluation": "LOO-CV",
                "New": "YES",
                "Source": "FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv",
            },
            {
                "Row": "P",
                "Variant": "lower_trim_20 + Huber30 + V5(e_i=0 anchor refit)",
                "Median 3D mm": fmt(p50_e0["loo_median_mm"]),
                "P95 mm": fmt(p50_e0["p95_mm"]),
                "RMSE mm": fmt(p50_e0["rmse_mm"]),
                "Evaluation": "LOO-CV; anchor refit diagnostic",
                "New": "YES",
                "Source": "FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv",
            },
            {
                "Row": "Q",
                "Variant": "Oracle lower bound",
                "Median 3D mm": fmt(raw_oracle["all_data_median"]),
                "P95 mm": "",
                "RMSE mm": "",
                "Evaluation": "oracle",
                "New": "YES",
                "Source": "FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv",
            },
            {
                "Row": "R",
                "Variant": "Bootstrap CI (lower_trim_20)",
                "Median 3D mm": f"[{fmt(s5_boot['ci95_low'])}, {fmt(s5_boot['ci95_high'])}]",
                "P95 mm": "",
                "RMSE mm": "",
                "Evaluation": "bootstrap 95% CI",
                "New": "YES",
                "Source": "FULL_V5_rawframe_bruteforce_v3/tables/s5_bootstrap_summary.csv",
            },
        ]
    )
    pd.DataFrame(headline_rows).to_csv(TABLE_DIR / "locked_headline_v2.csv", index=False)

    # New raw-frame summary tables.
    rawframe_key = pd.DataFrame(
        [
            {
                "metric": "v1_oracle_median",
                "value": raw_v1.loc[raw_v1["method"].eq("B0_oracle_link_selector"), "median_3d_mm"].iloc[0],
                "unit": "mm",
                "source": "FULL_V5_rawframe_bruteforce/tables/b6_master_comparison.csv",
            },
            {
                "metric": "v2_raw_rows",
                "value": raw_rows,
                "unit": "rows",
                "source": "FULL_V5_rawframe_bruteforce_v2/tables/raw_data_inventory.csv",
            },
            {
                "metric": "v2_valid_rows",
                "value": raw_valid,
                "unit": "rows",
                "source": "FULL_V5_rawframe_bruteforce_v2/tables/raw_data_inventory.csv",
            },
            {
                "metric": "v3_full_grid_configs",
                "value": len(read_csv("FULL_V5_rawframe_bruteforce_v3/tables/s2_full_grid.csv")),
                "unit": "rows",
                "source": "FULL_V5_rawframe_bruteforce_v3/tables/s2_full_grid.csv",
            },
            {
                "metric": "v3_best_honest_loo_median",
                "value": honest_best["loo_median"],
                "unit": "mm",
                "source": "FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv",
            },
            {
                "metric": "v3_oracle_lower_bound",
                "value": raw_oracle["all_data_median"],
                "unit": "mm",
                "source": "FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv",
            },
            {
                "metric": "v3_bootstrap_ci_low",
                "value": s5_boot["ci95_low"],
                "unit": "mm",
                "source": "FULL_V5_rawframe_bruteforce_v3/tables/s5_bootstrap_summary.csv",
            },
            {
                "metric": "v3_bootstrap_ci_high",
                "value": s5_boot["ci95_high"],
                "unit": "mm",
                "source": "FULL_V5_rawframe_bruteforce_v3/tables/s5_bootstrap_summary.csv",
            },
        ]
    )
    rawframe_key.to_csv(TABLE_DIR / "rawframe_v3_key_results.csv", index=False)

    anchor_key = pd.DataFrame(
        [
            {"metric": "aa_valid_rows", "value": aa_raw_valid, "unit": "rows", "source": "FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv"},
            {"metric": "aa_frames_per_pair_median", "value": aa_median_frames, "unit": "frames", "source": "FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv"},
            {"metric": "aa_mean_skewness", "value": ia_skew, "unit": "skewness", "source": "FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv"},
            {"metric": "aa_mean_p50_minus_lower_trim20", "value": ia_p50_ltrim20, "unit": "mm", "source": "FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv"},
            {"metric": "best_lower_trim20_anchor_loo", "value": lower_trim_e0["loo_median_mm"], "unit": "mm", "source": "FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv"},
            {"metric": "best_p50_e_zero_loo", "value": p50_e0["loo_median_mm"], "unit": "mm", "source": "FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv"},
            {"metric": "control_current_v5_p50_loo", "value": p50_control["loo_median_mm"], "unit": "mm", "source": "FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv"},
            {"metric": "p_new_wins", "value": l5["p_new_wins"], "unit": "probability", "source": "FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv"},
            {"metric": "improvement_ci_low", "value": l5["ci95_low_mm"], "unit": "mm", "source": "FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv"},
            {"metric": "improvement_ci_high", "value": l5["ci95_high_mm"], "unit": "mm", "source": "FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv"},
        ]
    )
    anchor_key.to_csv(TABLE_DIR / "anchor_lower_trim_key_results.csv", index=False)

    # Updated claim matrix.
    new_claims = [
        {
            "claim_id": 26,
            "claim_text": "lower_trim_20 extracts a static LOS component from raw range histograms",
            "level": "A",
            "supporting_tasks": "raw-frame V3 Stage 3 honest LOO; Stage 6 ladder",
            "supporting_numbers": f"lower_trim_20/V5/Huber30 LOO={honest_best['loo_median']:.3f} mm; V5 p50 baseline=67.849 mm; oracle={raw_oracle['all_data_median']:.3f} mm",
            "contradicting_evidence": f"bootstrap CI is wide: {s5_boot['ci95_low']:.1f}-{s5_boot['ci95_high']:.1f} mm",
            "recommended_paper_wording": "Raw static range histograms contain a recoverable LOS-side component; lower_trim_20 is the current zero-parameter extractor on this campaign.",
        },
        {
            "claim_id": 27,
            "claim_text": "Inter-anchor raw ranges are nearly symmetric; lower_trim_20 is inappropriate for anchor self-calibration here",
            "level": "A",
            "supporting_tasks": "anchor lower-trim L1-L3 blind test",
            "supporting_numbers": f"mean skewness={ia_skew:.3f}; lower_trim_20 anchor best={lower_trim_e0['loo_median_mm']:.3f} mm vs p50 control={p50_control['loo_median_mm']:.3f} mm",
            "contradicting_evidence": "Only one room and one static AA capture set",
            "recommended_paper_wording": "Use p50/median for inter-anchor self-calibration in this dataset; the lower-tail estimator is tag-side only.",
        },
        {
            "claim_id": 28,
            "claim_text": "e_i=0 or very small e_i is slightly better for the raw-frame static estimator",
            "level": "B",
            "supporting_tasks": "anchor lower-trim L3-L5",
            "supporting_numbers": f"p50/e_i=0={p50_e0['loo_median_mm']:.3f} mm; current p50/e_reg20={p50_control['loo_median_mm']:.3f} mm; P(new wins)={l5['p_new_wins']:.3f}",
            "contradicting_evidence": f"bootstrap CI crosses zero: {l5['ci95_low_mm']:.1f} to {l5['ci95_high_mm']:.1f} mm",
            "recommended_paper_wording": "The e_i=0 variant is the current best row, but its advantage is not statistically locked with 24 positions.",
        },
        {
            "claim_id": 29,
            "claim_text": "When NLOS is reduced by raw-frame LOS extraction, V5 geometry beats V4",
            "level": "A",
            "supporting_tasks": "raw-frame V3 honest ranking by geometry",
            "supporting_numbers": f"best V5 honest={honest_best['loo_median']:.3f} mm; best V4 honest={honest_v4['loo_median']:.3f} mm; best Vicon honest={honest_vicon['loo_median']:.3f} mm",
            "contradicting_evidence": "Still same 24-position campaign; second-room validation remains pending",
            "recommended_paper_wording": "The raw-frame result independently supports the cancellation interpretation: once right-tail range bias is reduced, V5 becomes the better geometry.",
        },
        {
            "claim_id": 30,
            "claim_text": "Parametric mixture models do not beat simple non-parametric lower-trim estimators",
            "level": "D",
            "supporting_tasks": "raw-frame V2/V3 estimator rankings",
            "supporting_numbers": f"V2 gaussian_exponential_mix held-out={v2_master.loc[v2_master['method'].eq('B1_gaussian_exponential_mix'), 'median_3d_mm'].iloc[0]:.3f} mm; V3 lower_trim_20 honest={honest_best['loo_median']:.3f} mm",
            "contradicting_evidence": "Mixtures remain useful diagnostic models but are not the winning estimator here",
            "recommended_paper_wording": "Do not claim mixture-model superiority; report lower_trim_20 as the empirically selected static estimator.",
        },
    ]
    updated_claims = pd.concat([claim_matrix, pd.DataFrame(new_claims)], ignore_index=True)
    updated_claims.to_csv(TABLE_DIR / "claim_evidence_matrix_v2.csv", index=False)

    # Updated registry.
    new_registry = [
        ("RAW FRAME", "raw_static_rows", raw_rows, "rows", "FULL_V5_rawframe_bruteforce_v2", "tables/raw_data_inventory.csv", "Validates raw tr_all.csv data availability."),
        ("RAW FRAME", "raw_static_valid_rows", raw_valid, "rows", "FULL_V5_rawframe_bruteforce_v2", "tables/raw_data_inventory.csv", ""),
        ("RAW FRAME", "raw_expected_ratio", raw_ratio, "ratio", "FULL_V5_rawframe_bruteforce_v2", "tables/raw_data_inventory.csv", ""),
        ("RAW FRAME", "raw_link_inventory_total_rows", total_raw_link_rows, "frames", "FULL_V5_rawframe_bruteforce_v3", "tables/raw_link_inventory.csv", ""),
        ("RAW FRAME", "raw_link_median_frames", raw_link_median_n, "frames", "FULL_V5_rawframe_bruteforce_v3", "tables/raw_link_inventory.csv", ""),
        ("RAW FRAME", "rawframe_v3_grid_configs", len(read_csv("FULL_V5_rawframe_bruteforce_v3/tables/s2_full_grid.csv")), "configs", "FULL_V5_rawframe_bruteforce_v3", "tables/s2_full_grid.csv", ""),
        ("RAW FRAME", "rawframe_transductive_best_median", transductive_best["median_3d_mm"], "mm", "FULL_V5_rawframe_bruteforce_v3", "tables/s2_top50_overall.csv", "Diagnostic all-data row."),
        ("RAW FRAME", "rawframe_honest_lower_trim20_v5_huber30_loo", honest_best["loo_median"], "mm", "FULL_V5_rawframe_bruteforce_v3", "tables/s3_honest_ranking.csv", ""),
        ("RAW FRAME", "rawframe_honest_lower_trim20_v5_huber30_p95", honest_best["loo_p95"], "mm", "FULL_V5_rawframe_bruteforce_v3", "tables/s3_honest_ranking.csv", ""),
        ("RAW FRAME", "rawframe_honest_lower_trim20_v5_huber30_rmse", honest_best["loo_rmse"], "mm", "FULL_V5_rawframe_bruteforce_v3", "tables/s3_honest_ranking.csv", ""),
        ("RAW FRAME", "rawframe_oracle_lower_bound", raw_oracle["all_data_median"], "mm", "FULL_V5_rawframe_bruteforce_v3", "tables/s6_master_ladder.csv", ""),
        ("RAW FRAME", "rawframe_bootstrap_ci95_low", s5_boot["ci95_low"], "mm", "FULL_V5_rawframe_bruteforce_v3", "tables/s5_bootstrap_summary.csv", ""),
        ("RAW FRAME", "rawframe_bootstrap_ci95_high", s5_boot["ci95_high"], "mm", "FULL_V5_rawframe_bruteforce_v3", "tables/s5_bootstrap_summary.csv", ""),
        ("ANCHOR LOWER TRIM", "aa_valid_rows", aa_raw_valid, "rows", "FULL_V5_anchor_lower_trim", "tables/l1_inter_anchor_distribution.csv", ""),
        ("ANCHOR LOWER TRIM", "aa_mean_skewness", ia_skew, "skewness", "FULL_V5_anchor_lower_trim", "tables/l1_inter_anchor_distribution.csv", ""),
        ("ANCHOR LOWER TRIM", "aa_mean_p50_minus_lower_trim20", ia_p50_ltrim20, "mm", "FULL_V5_anchor_lower_trim", "tables/l1_inter_anchor_distribution.csv", ""),
        ("ANCHOR LOWER TRIM", "anchor_lower_trim20_best_loo", lower_trim_e0["loo_median_mm"], "mm", "FULL_V5_anchor_lower_trim", "tables/l3_master_comparison.csv", ""),
        ("ANCHOR LOWER TRIM", "anchor_p50_e_zero_best_loo", p50_e0["loo_median_mm"], "mm", "FULL_V5_anchor_lower_trim", "tables/l3_master_comparison.csv", ""),
        ("ANCHOR LOWER TRIM", "anchor_p50_reg5_loo", p50_reg5["loo_median_mm"], "mm", "FULL_V5_anchor_lower_trim", "tables/l3_master_comparison.csv", ""),
        ("ANCHOR LOWER TRIM", "anchor_current_p50_control_loo", p50_control["loo_median_mm"], "mm", "FULL_V5_anchor_lower_trim", "tables/l3_master_comparison.csv", ""),
        ("ANCHOR LOWER TRIM", "anchor_e_zero_p_new_wins", l5["p_new_wins"], "probability", "FULL_V5_anchor_lower_trim", "tables/l5_bootstrap_summary.csv", ""),
    ]
    new_registry_df = pd.DataFrame(
        new_registry,
        columns=["theme", "metric_name", "value", "unit", "source_directory", "source_file", "notes"],
    )
    registry_v2 = pd.concat([registry, new_registry_df], ignore_index=True)
    registry_v2.to_csv(TABLE_DIR / "master_number_registry_v2.csv", index=False)

    # Updated consistency audit.
    consistency_rows = [
        {
            "metric": "Raw-frame oracle median",
            "source_1": "rawframe v1 b6",
            "value_1": raw_v1.loc[raw_v1["method"].eq("B0_oracle_link_selector"), "median_3d_mm"].iloc[0],
            "source_2": "rawframe v3 s6 ladder",
            "value_2": raw_oracle["all_data_median"],
        },
        {
            "metric": "lower_trim_20 V5 Huber30 LOO",
            "source_1": "rawframe v3 s3 honest",
            "value_1": honest_best["loo_median"],
            "source_2": "anchor lower trim p50 control",
            "value_2": p50_control["loo_median_mm"],
        },
        {
            "metric": "Raw static row count",
            "source_1": "raw_data_inventory",
            "value_1": raw_rows,
            "source_2": "expected 24*8*1200",
            "value_2": raw_expected,
        },
        {
            "metric": "p50 e_i=0 best",
            "source_1": "anchor lower trim l3",
            "value_1": p50_e0["loo_median_mm"],
            "source_2": "completion report rounded",
            "value_2": 43.172,
        },
    ]
    consistency = pd.DataFrame(consistency_rows)
    consistency["discrepancy"] = (consistency["value_1"].astype(float) - consistency["value_2"].astype(float)).abs()
    consistency["status"] = consistency["discrepancy"].apply(lambda x: "OK" if x < 0.2 or x < 0.002 * raw_expected else "CHECK")
    consistency.to_csv(TABLE_DIR / "consistency_audit_v2.csv", index=False)

    # Source inventory.
    dirs = [
        "FULL_V5",
        "FULL_V5_scale_to_vicon",
        "FULL_V5_align_to_Vicon",
        "FULL_V5_one_baseline_scale_correction",
        "FULL_transfer_matrix",
        "FULL_V4_vs_V5_final",
        "FULL_V5_mechanism_ablations",
        "FULL_V5_extended_mechanism_ablations",
        "FULL_V5_GPU_tier1",
        "FULL_V5_GPU_discovery",
        "FULL_V5_followup_validation",
        "FULL_V5_overnight_batch2",
        "FULL_V5_batch3_falsification",
        "FULL_V5_roto_deepdive",
        "FULL_V5_mechanistic_deepdive",
        "FULL_V5_paper_strengthening",
        "FULL_V5_grand_synthesis",
        "FULL_V5_final_gate",
        "FULL_V5_phase_center_sensitivity",
        "FULL_V5_rawframe_bruteforce",
        "FULL_V5_rawframe_bruteforce_v2",
        "FULL_V5_rawframe_bruteforce_v3",
        "FULL_V5_anchor_lower_trim",
    ]
    inv_rows = []
    for d in dirs:
        path = ANALYSIS / d
        inv_rows.append(
            {
                "Directory": d,
                "Exists": path.exists(),
                "CSV files": safe_count_files(path, ".csv"),
                "Report MD files": safe_count_files(path, ".md"),
                "PNG figures": safe_count_files(path, ".png"),
            }
        )
    source_inventory = pd.DataFrame(inv_rows)
    source_inventory.to_csv(TABLE_DIR / "report_source_inventory_v2.csv", index=False)

    # Figures.
    figure_rows = [
        copy_figure("FULL_V5_overnight_batch2/figures/fig01_anchor_layout.png", "fig01_anchor_layout.png", "Anchor layouts: V4, V5, and Vicon."),
        copy_figure("FULL_V5_overnight_batch2/figures/fig02_static_accuracy_trajectory.png", "fig02_static_accuracy_trajectory.png", "Static accuracy trajectory."),
        copy_figure("FULL_V5_overnight_batch2/figures/fig03_cancellation_valley.png", "fig03_cancellation_valley.png", "Cancellation valley."),
        copy_figure("FULL_V5_overnight_batch2/figures/fig05_nlos_fingerprint.png", "fig04_nlos_fingerprint.png", "Per-anchor NLOS fingerprint."),
        copy_figure("FULL_V5_overnight_batch2/figures/fig09_transfer_matrix_heatmap.png", "fig05_transfer_matrix_heatmap.png", "Transfer matrix heatmap."),
        copy_figure("FULL_V5_batch3_falsification/figures/f1_nested_cv_comparison.png", "fig06_nested_cv_comparison.png", "Nested-CV degradation."),
        copy_figure("FULL_V5_batch3_falsification/figures/f3_contour_alpha_dtag.png", "fig07_profile_alpha_dtag.png", "Profile likelihood alpha vs D_tag."),
        copy_figure("FULL_V5_roto_deepdive/figures/r2_alignment_comparison_bar.png", "fig08_roto_alignment_comparison.png", "ROTO alignment comparison."),
        copy_figure("FULL_V5_roto_deepdive/figures/r4_gap_waterfall.png", "fig09_roto_gap_waterfall.png", "ROTO gap decomposition."),
        copy_figure("FULL_V5_mechanistic_deepdive/figures/m5_accuracy_vs_anchors.png", "fig10_anchor_count_identifiability.png", "Accuracy versus anchor count."),
        copy_figure("FULL_V5_paper_strengthening/figures/fig11_cancellation_mechanism.png", "fig11_cancellation_mechanism.png", "Signed radial mechanism diagnostic."),
        copy_figure("FULL_V5_phase_center_sensitivity/figures/a2_ranking_probability_vs_sigma.png", "fig12_phase_center_mc_probabilities.png", "Phase-center manufacturing variation probabilities."),
        copy_figure("FULL_V5_phase_center_sensitivity/figures/a5_operating_point_on_valley.png", "fig13_phase_center_valley.png", "Phase-center shift on cancellation valley."),
        copy_figure("FULL_V5_rawframe_bruteforce_v3/figures/s2_estimator_ranking.png", "fig14_rawframe_estimator_ranking.png", "Raw-frame estimator ranking."),
        copy_figure("FULL_V5_rawframe_bruteforce_v3/figures/s6_accuracy_ladder.png", "fig15_rawframe_accuracy_ladder.png", "Raw-frame accuracy ladder."),
        copy_figure("FULL_V5_rawframe_bruteforce_v3/figures/s6_oracle_vs_honest_gap.png", "fig16_rawframe_oracle_gap.png", "Raw-frame oracle versus honest gap."),
        copy_figure("FULL_V5_anchor_lower_trim/figures/l1_inter_anchor_histograms.png", "fig17_inter_anchor_histograms.png", "Inter-anchor raw range histograms."),
        copy_figure("FULL_V5_anchor_lower_trim/figures/l3_accuracy_by_anchor_method.png", "fig18_anchor_lower_trim_accuracy.png", "Tag accuracy by anchor aggregation method."),
        copy_figure("FULL_V5_anchor_lower_trim/figures/l5_improvement_distribution.png", "fig19_anchor_ezero_bootstrap.png", "Bootstrap improvement distribution for e_i=0."),
    ]
    figure_manifest = pd.DataFrame(figure_rows)
    figure_manifest.to_csv(TABLE_DIR / "figure_manifest_v2.csv", index=False)

    # Level summaries for report.
    level_counts = updated_claims["level"].value_counts().to_dict()
    level_rows = [
        {"Level": level, "Claim count": int(level_counts.get(level, 0)), "Meaning": meaning}
        for level, meaning in [
            ("A", "Proven within this campaign"),
            ("B", "Supported with caveats"),
            ("C", "Hypothesis only"),
            ("D", "Disproven or should not be claimed"),
        ]
    ]
    new_claim_rows = []
    for c in new_claims:
        new_claim_rows.append(
            {
                "ID": c["claim_id"],
                "Level": c["level"],
                "Claim": c["claim_text"],
                "Recommended wording": c["recommended_paper_wording"],
            }
        )

    # Report tables in markdown.
    headline_md = md_table(
        headline_rows,
        ["Row", "Variant", "Median 3D mm", "P95 mm", "RMSE mm", "Evaluation", "New", "Source"],
    )
    level_md = md_table(level_rows, ["Level", "Claim count", "Meaning"])
    new_claims_md = md_table(new_claim_rows, ["ID", "Level", "Claim", "Recommended wording"])

    raw_inventory_rows = [
        {"Metric": "Static files found", "Value": raw_static_files, "Source": "FULL_V5_rawframe_bruteforce_v2/tables/raw_data_inventory.csv"},
        {"Metric": "Total raw rows", "Value": raw_rows, "Source": "FULL_V5_rawframe_bruteforce_v2/tables/raw_data_inventory.csv"},
        {"Metric": "Total valid rows", "Value": raw_valid, "Source": "FULL_V5_rawframe_bruteforce_v2/tables/raw_data_inventory.csv"},
        {"Metric": "Expected rows", "Value": raw_expected, "Source": "24 x 8 x 1200"},
        {"Metric": "Ratio to expected", "Value": fmt3(raw_ratio), "Source": "FULL_V5_rawframe_bruteforce_v2/tables/raw_data_inventory.csv"},
        {"Metric": "ID01 anchor A frames loaded", "Value": id01_frames, "Source": "FULL_V5_rawframe_bruteforce_v2/tables/raw_loading_checkpoint.csv"},
        {"Metric": "V3 link inventory rows", "Value": len(raw_links), "Source": "FULL_V5_rawframe_bruteforce_v3/tables/raw_link_inventory.csv"},
        {"Metric": "Median frames per tag-anchor link", "Value": fmt(raw_link_median_n, 0), "Source": "FULL_V5_rawframe_bruteforce_v3/tables/raw_link_inventory.csv"},
    ]
    raw_inventory_md = md_table(raw_inventory_rows, ["Metric", "Value", "Source"])

    estimator_rows = [
        {"Row": "V2 B1", "Estimator": "gaussian_exponential_mix", "Geometry": "", "Loss": "", "Median mm": fmt(v2_master.loc[v2_master["method"].eq("B1_gaussian_exponential_mix"), "median_3d_mm"].iloc[0]), "P95 mm": fmt(v2_master.loc[v2_master["method"].eq("B1_gaussian_exponential_mix"), "p95_3d_mm"].iloc[0]), "Source": "FULL_V5_rawframe_bruteforce_v2/tables/b6_master_comparison.csv"},
        {"Row": "V2 B2", "Estimator": "asymmetric", "Geometry": "", "Loss": "", "Median mm": fmt(v2_master.loc[v2_master["method"].eq("B2_asymmetric"), "median_3d_mm"].iloc[0]), "P95 mm": fmt(v2_master.loc[v2_master["method"].eq("B2_asymmetric"), "p95_3d_mm"].iloc[0]), "Source": "FULL_V5_rawframe_bruteforce_v2/tables/b6_master_comparison.csv"},
        {"Row": "V3 transductive", "Estimator": transductive_best["estimator"], "Geometry": transductive_best["geometry"], "Loss": transductive_best["loss"], "Median mm": fmt(transductive_best["median_3d_mm"]), "P95 mm": fmt(transductive_best["p95_3d_mm"]), "Source": "FULL_V5_rawframe_bruteforce_v3/tables/s2_top50_overall.csv"},
        {"Row": "V3 honest", "Estimator": honest_best["estimator"], "Geometry": honest_best["geometry"], "Loss": honest_best["loss"], "Median mm": fmt(honest_best["loo_median"]), "P95 mm": fmt(honest_best["loo_p95"]), "Source": "FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv"},
        {"Row": "V3 oracle", "Estimator": "oracle link selector", "Geometry": "", "Loss": "", "Median mm": fmt(raw_oracle["all_data_median"]), "P95 mm": "", "Source": "FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv"},
    ]
    estimator_md = md_table(estimator_rows, ["Row", "Estimator", "Geometry", "Loss", "Median mm", "P95 mm", "Source"])

    honest_geo_rows = [
        {"Geometry": "V5", "Best estimator/loss": f"{honest_best['estimator']} / {honest_best['loss']}", "LOO median mm": fmt(honest_best["loo_median"]), "P95 mm": fmt(honest_best["loo_p95"]), "RMSE mm": fmt(honest_best["loo_rmse"])},
        {"Geometry": "V4", "Best estimator/loss": f"{honest_v4['estimator']} / {honest_v4['loss']}", "LOO median mm": fmt(honest_v4["loo_median"]), "P95 mm": fmt(honest_v4["loo_p95"]), "RMSE mm": fmt(honest_v4["loo_rmse"])},
        {"Geometry": "Vicon", "Best estimator/loss": f"{honest_vicon['estimator']} / {honest_vicon['loss']}", "LOO median mm": fmt(honest_vicon["loo_median"]), "P95 mm": fmt(honest_vicon["loo_p95"]), "RMSE mm": fmt(honest_vicon["loo_rmse"])},
    ]
    honest_geo_md = md_table(honest_geo_rows, ["Geometry", "Best estimator/loss", "LOO median mm", "P95 mm", "RMSE mm"])

    anchor_dist_rows = [
        {"Metric": "Raw valid AA rows", "Value": aa_raw_valid, "Source": "FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv"},
        {"Metric": "Pairs", "Value": len(ia), "Source": "FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv"},
        {"Metric": "Frames per pair median", "Value": fmt(aa_median_frames, 0), "Source": "FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv"},
        {"Metric": "Mean skewness", "Value": fmt3(ia_skew), "Source": "FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv"},
        {"Metric": "Mean p50 - lower_trim_20", "Value": fmt(ia_p50_ltrim20), "Source": "FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv"},
    ]
    anchor_dist_md = md_table(anchor_dist_rows, ["Metric", "Value", "Source"])

    anchor_result_rows = []
    for label, row in [
        ("p50 + e_i=0", p50_e0),
        ("p50 + e_reg=5", p50_reg5),
        ("p50 current control", p50_control),
        ("p50 + e_reg=20 refit", p50_reg20),
        ("lower_trim_20 + e_i=0", lower_trim_e0),
    ]:
        anchor_result_rows.append(
            {
                "Variant": label,
                "LOO median mm": fmt(row["loo_median_mm"]),
                "P95 mm": fmt(row["p95_mm"]),
                "RMSE mm": fmt(row["rmse_mm"]),
                "D_tag mean mm": fmt(row["d_tag_mean_mm"]),
                "Sim3 scale": fmt3(row.get("sim3_scale", "")),
                "Rigid RMSE mm": fmt(row.get("rigid_rmse_mm", "")),
                "Source": "FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv",
            }
        )
    anchor_result_md = md_table(anchor_result_rows, ["Variant", "LOO median mm", "P95 mm", "RMSE mm", "D_tag mean mm", "Sim3 scale", "Rigid RMSE mm", "Source"])

    negative_rows = [
        {"Experiment": "MLP learned range correction", "Result": "MLP residual median 118.0 mm versus scalar 98.5 mm", "Why it failed": "24 static positions were too few for a learned correction model", "Source": "FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md"},
        {"Experiment": "GNN attention correction", "Result": "attention residual median 121.1 mm", "Why it failed": "graph model overfit or lacked enough independent data", "Source": "FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md"},
        {"Experiment": "Solver search", "Result": "best 82.7 mm in GPU discovery; fixed search still about 82.6 mm", "Why it failed": "no candidate beat V4/V5 baselines after proper D_tag LOO handling", "Source": "FULL_V5_overnight_batch2/tables/n2_solver_search_fixed.csv"},
        {"Experiment": "Bayesian Gaussian posterior", "Result": "95% coverage 0.33; Student-t increased it to 0.46", "Why it failed": "posterior remained under-calibrated", "Source": "FULL_V5_final_gate/tables/g2_unified_noise_models.csv"},
        {"Experiment": "NLOS detector generalization", "Result": "random PR-AUC 0.949 collapsed to 0.42-0.55 in hard splits", "Why it failed": "model memorized anchor identity and campaign-specific structure", "Source": "FULL_V5_batch3_falsification/tables/f5_nlos_splits.csv"},
        {"Experiment": "Rigid two-tag ROTO solver", "Result": "joint range-level solver 261.8-264.2 mm versus independent 101.1 mm", "Why it failed": "tested constraint forced geometry but did not solve dynamic range bias", "Source": "FULL_V5_final_gate/tables/g5_joint_solver_summary.csv"},
        {"Experiment": "p30 dynamic transfer", "Result": "ROTO p30 best 283.9 mm versus raw/p50 101.5 mm", "Why it failed": "static percentile aggregation did not transfer to single-frame dynamic ranges", "Source": "FULL_V5_followup_validation/reports/FOLLOWUP_VALIDATION_SUMMARY.md"},
        {"Experiment": "lower_trim_20 for inter-anchor ranges", "Result": f"lower_trim_20-anchor best {lower_trim_e0['loo_median_mm']:.1f} mm versus p50 control {p50_control['loo_median_mm']:.1f} mm", "Why it failed": "inter-anchor distributions were nearly symmetric, so lower-tail trimming introduced downward bias", "Source": "FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv"},
        {"Experiment": "Parametric mixture estimators", "Result": f"gaussian_exponential_mix held-out {v2_master.loc[v2_master['method'].eq('B1_gaussian_exponential_mix'), 'median_3d_mm'].iloc[0]:.1f} mm versus lower_trim_20 honest {honest_best['loo_median']:.1f} mm", "Why it failed": "mixtures did not beat the simple non-parametric lower-tail statistic", "Source": "FULL_V5_rawframe_bruteforce_v2/tables/b6_master_comparison.csv; FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv"},
    ]
    negative_md = md_table(negative_rows, ["Experiment", "Result", "Why it failed", "Source"])

    registry_rows = []
    for _, r in registry_v2.iterrows():
        val = r["value"]
        try:
            valf = float(val)
            if abs(valf) >= 1000 and str(r["unit"]) not in {"scale", "probability", "ratio"}:
                val_s = fmt(valf, 0)
            elif str(r["unit"]) in {"probability", "ratio", "scale", "skewness"}:
                val_s = fmt3(valf)
            elif str(r["unit"]) in {"rows", "configs", "frames", "count"}:
                val_s = fmt(valf, 0)
            else:
                val_s = fmt(valf, 1)
        except Exception:
            val_s = str(val)
        registry_rows.append(
            {
                "Theme": r["theme"],
                "Metric": r["metric_name"],
                "Value": val_s,
                "Unit": r["unit"],
                "Source": f"{r['source_directory']}/{r['source_file']}",
            }
        )
    registry_md = md_table(registry_rows, ["Theme", "Metric", "Value", "Unit", "Source"])

    consistency_md_rows = []
    for _, r in consistency.iterrows():
        consistency_md_rows.append(
            {
                "Metric": r["metric"],
                "Source 1": r["source_1"],
                "Value 1": fmt(r["value_1"], 3),
                "Source 2": r["source_2"],
                "Value 2": fmt(r["value_2"], 3),
                "Discrepancy": fmt(r["discrepancy"], 6),
                "Status": r["status"],
            }
        )
    consistency_md = md_table(consistency_md_rows, ["Metric", "Source 1", "Value 1", "Source 2", "Value 2", "Discrepancy", "Status"])

    inventory_md = md_table(source_inventory.to_dict("records"), ["Directory", "Exists", "CSV files", "Report MD files", "PNG figures"])
    figure_md = md_table(figure_manifest.to_dict("records"), ["Copied figure", "Caption", "Source", "Status"])

    exec_summary = f"""## 0. Executive Summary

This V2 report updates the internal Erlangen 28-May-2026 V5 analysis record by adding
four post-report directories: `FULL_V5_rawframe_bruteforce`, `FULL_V5_rawframe_bruteforce_v2`,
`FULL_V5_rawframe_bruteforce_v3`, and `FULL_V5_anchor_lower_trim`. The report now covers
23 analysis directories, combining the original 19-directory campaign with the raw-frame
LOS extraction campaign and the blind inter-anchor lower-trim experiment [source:
tables/report_source_inventory_v2.csv]. The update keeps the same measurement dataset:
24 static Vicon positions, 17 ROTO captures, DWM1001C UWB range data, and Vicon/OptiTrack
ground truth [source: FULL_V5/tables/static_summary_DLOO.csv;
FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].

The main new result is a raw-frame static breakthrough. The v2 data-discovery run confirmed
that the static captures contain true per-frame `tr_all.csv` data: {raw_rows:,} raw rows,
{raw_valid:,} valid rows, and a ratio of {raw_ratio:.3f} to the nominal 24 x 8 x 1200
frame-anchor observations [source: FULL_V5_rawframe_bruteforce_v2/tables/raw_data_inventory.csv].
The true brute-force v3 run then tested 107,448 estimator/loss/geometry/D_tag cells and
identified an honest LOO winner: lower_trim_20 over raw tag-anchor range histograms,
V5 geometry, and Huber30, with {honest_best['loo_median']:.1f} mm median 3D error,
{honest_best['loo_p95']:.1f} mm P95, and {honest_best['loo_rmse']:.1f} mm RMSE [source:
FULL_V5_rawframe_bruteforce_v3/tables/s2_full_grid.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv]. The v3 oracle lower bound
is {raw_oracle['all_data_median']:.1f} mm, so the blind lower_trim_20 estimator lands
within 0.1 mm of the oracle median on the campaign-level ladder [source:
FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv].

This changes the interpretation of the earlier p30 result. The earlier p30/inverse-RMS
rows showed that lower range percentiles could reduce static error, but the follow-up
analysis correctly treated those rows as post-selected and not automatically deployable
[source: FULL_V5_followup_validation/tables/f4_percentile_recalibrated.csv;
FULL_V5_followup_validation/tables/f6_final_comparison.csv]. The raw-frame v3 result
uses the actual per-link histograms rather than a single percentile diagnostic. It
therefore supports a more specific claim: static tag-anchor range histograms contain
a recoverable LOS-side component, and lower_trim_20 is the current zero-parameter
extractor for that component on this dataset [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

The new result also reframes the V4/V5 ranking. With p50 ranges and NLOS bias still
present, V4+C_V4+D_LOO remains the empirical static median winner at 57.9 mm versus
67.8 mm for V5+C_V5+D_LOO [source: FULL_transfer_matrix/tables/transfer_matrix_48cells.csv].
With raw-frame LOS extraction, the best honest V5 row is {honest_best['loo_median']:.1f}
mm, while the best honest V4 geometry row in the raw-frame v3 ranking is
{honest_v4['loo_median']:.1f} mm [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv]. This independently
supports the cancellation interpretation: when right-tail tag-anchor NLOS bias is
reduced, V5's metric-correct geometry becomes the better positioning geometry.

The anchor lower-trim blind experiment answered a different question. It tested whether
the same lower-tail estimator should be applied to inter-anchor self-calibration ranges.
The answer is no for this dataset. Inter-anchor raw distributions contain {aa_raw_valid:,}
valid rows, a median of {aa_median_frames:.0f} frames per pair, and mean skewness
{ia_skew:.3f}; the distributions are nearly symmetric rather than strongly right-tailed
[source: FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv]. The best
lower_trim_20-anchor row is {lower_trim_e0['loo_median_mm']:.1f} mm, worse than the
current p50-anchor control at {p50_control['loo_median_mm']:.1f} mm [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv]. The practical rule is now
split by measurement type: use p50/median for inter-anchor self-calibration in this
campaign, and use lower_trim_20 for static tag-anchor histograms when enough frames are
available.

The anchor lower-trim experiment also confirmed the earlier e_i caution. The current
p50/e_reg20 control is {p50_control['loo_median_mm']:.1f} mm under the raw-frame static
tag estimator, while p50 with e_i=0 is {p50_e0['loo_median_mm']:.1f} mm and p50 with
e_reg=5 is {p50_reg5['loo_median_mm']:.1f} mm [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv]. The improvement is small:
the paired bootstrap gives P(new wins)={l5['p_new_wins']:.3f} and a 95% improvement
interval of {l5['ci95_low_mm']:.1f} to {l5['ci95_high_mm']:.1f} mm [source:
FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv]. The report therefore treats
e_i=0 as the current best engineering candidate, not as a statistically locked result.

Top-level findings after this update:

- V5 still fixes the anchor-side scale leak: V4 Sim3 scale is 0.958 and V5 Sim3
  scale is 1.010; rigid anchor RMSE improves from 105.4 mm to 63.0 mm [source:
  FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv].
- V4 still wins the p50 static LOO baseline on this campaign: 57.9 mm for V4+LOO
  versus 67.8 mm for V5+LOO [source: FULL_transfer_matrix/tables/transfer_matrix_48cells.csv].
- The best honest raw-frame static row is lower_trim_20 + V5 + Huber30 at
  {honest_best['loo_median']:.1f} mm LOO, with a wide bootstrap interval of
  {s5_boot['ci95_low']:.1f}-{s5_boot['ci95_high']:.1f} mm [source:
  FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv;
  FULL_V5_rawframe_bruteforce_v3/tables/s5_bootstrap_summary.csv].
- The raw-frame oracle lower bound is {raw_oracle['all_data_median']:.1f} mm and the
  honest lower_trim_20 row matches it at the median level [source:
  FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv].
- Inter-anchor lower_trim_20 is a negative result: the inter-anchor distributions are
  nearly symmetric and lower-tail trimming worsens the anchor refit [source:
  FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv;
  FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].
- ROTO remains a dynamic limitation: the conservative V5 best-fit-aligned median is
  101.5 mm, and the raw-frame lower_trim_20 static result does not apply to single-frame
  dynamic ranges [source: FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv;
  FULL_V5_followup_validation/reports/FOLLOWUP_VALIDATION_SUMMARY.md].

### Updated Locked Headline Table

{headline_md}

### Updated Claim Confidence Levels

{level_md}

The updated claim matrix contains {len(updated_claims)} claims: {int(level_counts.get('A', 0))}
Level A, {int(level_counts.get('B', 0))} Level B, {int(level_counts.get('C', 0))}
Level C, and {int(level_counts.get('D', 0))} Level D claims [source:
tables/claim_evidence_matrix_v2.csv]. New Level A claims cover raw-frame LOS extraction,
inter-anchor symmetry, and the V5-over-V4 result after NLOS reduction. New caveated
claims cover e_i=0. New Level D wording prevents mixture-model superiority from being
claimed [source: tables/claim_evidence_matrix_v2.csv].
"""

    section12 = f"""## 12. Raw-Frame LOS Extraction Campaign

### 12.1 Motivation and Physical Basis

The original static pipeline aggregated each position-anchor link to a representative
range before position solving. For p50 runs that representative was the median range.
That is robust to isolated outliers but it does not explicitly separate a direct LOS
component from a positive NLOS/multipath tail. The raw-frame campaign was designed to
test whether the full per-link histogram contains usable information that a median
cannot express [source: FULL_V5_rawframe_bruteforce_v3/reports/BRUTEFORCE_V3_COMPLETION.md].

The physical assumption is one-sided. UWB multipath and NLOS contamination usually
increase measured range because reflected paths are longer than the direct path. If a
static capture contains about 1200 frames per link, the left side of the range histogram
can preserve a repeated LOS-side component even when the median is shifted by a positive
tail. This is not the same as selecting one minimum sample. The successful estimator,
lower_trim_20, averages the lowest 20% of valid raw ranges for each static
position-anchor link, so it uses hundreds of frames per link rather than a single
extreme observation [source: FULL_V5_rawframe_bruteforce_v3/tables/raw_link_inventory.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

This distinction matters for deployment. lower_trim_20 is a static or quasi-static
batch estimator. It requires a range histogram. It is not directly available for ROTO
single-frame tracking, which is why the earlier ROTO p30 test failed to transfer [source:
FULL_V5_followup_validation/reports/FOLLOWUP_VALIDATION_SUMMARY.md;
FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].

### 12.2 Data Inventory

The first raw-frame brute-force run produced only per-link feature outputs and was
therefore insufficient to prove that raw frames had been loaded. The v2 diagnostic fixed
that question by inspecting the capture files directly. It found one `tr_all.csv` file
per static capture, with one row per sweep-anchor observation [source:
FULL_V5_rawframe_bruteforce_v2/reports/DATA_DISCOVERY.md].

{raw_inventory_md}

The first capture, ID01, had shape `(9608, 31)` and anchor counts of 1201 rows for
each of the eight anchors. The columns include `host_elapsed_s`, `host_epoch_s`,
`sweep`, `anchor_id`, `raw_mm`, `range_mm`, `quality_percent`, `valid`, `status`,
and IMU summary columns such as `acc_norm_mean_mg` and `acc_norm_std_mg` [source:
FULL_V5_rawframe_bruteforce_v2/reports/DATA_DISCOVERY.md]. The v3 link inventory
contains 192 static tag-anchor links, one for each of 24 positions and 8 anchors,
with per-link distribution columns including min, p01, p05, p40, p50, and max [source:
FULL_V5_rawframe_bruteforce_v3/tables/raw_link_inventory.csv].

The raw inventory also explains why the earlier static percentile findings were
real but incomplete. The p30 runs used a lower percentile per link, but the later
v3 search evaluated a broader estimator family over the full link histogram [source:
FULL_V5_followup_validation/tables/f4_percentile_recalibrated.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s1_all_estimators.csv].

### 12.3 Estimator Comparison

The v3 campaign ran the exhaustive search requested after the v2 discovery. Stage 1
fit parametric mixture models with 8 model families, 192 links, 100 initializations,
500 Adam steps, and 200 L-BFGS steps. Stage 2 evaluated a full grid of 37 estimators,
121 D_tag values, 8 loss choices, and 3 geometries, for 107,448 grid configurations
[source: FULL_V5_rawframe_bruteforce_v3/reports/BRUTEFORCE_V3_COMPLETION.md;
FULL_V5_rawframe_bruteforce_v3/tables/s2_full_grid.csv].

{estimator_md}

The best all-data Stage 2 row was p10/V5/Huber50 at {transductive_best['median_3d_mm']:.1f}
mm. It is a transductive row because the D_tag and estimator choices see all 24 positions
at once [source: FULL_V5_rawframe_bruteforce_v3/tables/s2_top50_overall.csv]. The
honest Stage 3 row is different: lower_trim_20/V5/Huber30 reaches {honest_best['loo_median']:.1f}
mm after fold-wise D_tag calibration [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

The non-parametric lower-tail family beat the parametric mixture family in the final
honest ranking. In v2, gaussian_exponential_mix reached {v2_master.loc[v2_master['method'].eq('B1_gaussian_exponential_mix'), 'median_3d_mm'].iloc[0]:.1f}
mm held-out and the asymmetric PyTorch variant reached {v2_master.loc[v2_master['method'].eq('B2_asymmetric'), 'median_3d_mm'].iloc[0]:.1f}
mm [source: FULL_V5_rawframe_bruteforce_v2/tables/b6_master_comparison.csv]. In v3,
lower_trim_20/V5/Huber30 reached {honest_best['loo_median']:.1f} mm [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv]. The practical result is
that a simple histogram statistic outperformed the fitted mixture models for this
dataset.

The source inventory is important. lower_trim_20 is not a new solver geometry and not
a learned model. It changes the tag-anchor range observation supplied to the existing
position solver. The geometry, delay correction, loss, and D_tag calibration are still
explicitly recorded in the Stage 3 table [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

### 12.4 Honest LOO Results

The v3 Stage 3 ranking is the raw-frame result that should be used in the headline
table. It recalibrates D_tag fold-wise and evaluates each held-out static position
under the chosen estimator/loss/geometry configuration. The best row is lower_trim_20,
Huber30, and V5 geometry, with LOO median {honest_best['loo_median']:.1f} mm, P95
{honest_best['loo_p95']:.1f} mm, RMSE {honest_best['loo_rmse']:.1f} mm, all-data
median {honest_best['all_data_median']:.1f} mm, and mean training D_tag
{honest_best['mean_train_dtag_mm']:.1f} mm [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

{honest_geo_md}

This geometry ranking is the main new mechanism evidence. Under p50, V4 wins because
its compressed geometry partly cancels structured positive range bias. Under raw-frame
LOS extraction, V5 wins by 9.4 mm over the best V4 geometry row in the honest v3 ranking
[source: FULL_transfer_matrix/tables/transfer_matrix_48cells.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv]. The direction of the
ranking therefore changes when the range aggregation removes much of the NLOS tail.
That is a stronger confirmation of the cancellation hypothesis than the earlier signed
radial diagnostic alone [source: FULL_V5_paper_strengthening/tables/p1_signed_radial_summary.csv].

The bootstrap interval remains wide because there are still only 24 independent static
positions. Stage 5 ran 5000 bootstrap iterations and reported a mean of {s5_boot['mean']:.1f}
mm, standard deviation {s5_boot['std']:.1f} mm, and 95% interval {s5_boot['ci95_low']:.1f}
to {s5_boot['ci95_high']:.1f} mm [source:
FULL_V5_rawframe_bruteforce_v3/tables/s5_bootstrap_summary.csv]. This interval does
not weaken the fact that the full-campaign LOO row is 44.5 mm. It does limit how
aggressively the number should be generalized beyond this campaign.

### 12.5 Falsification and Controls

The v3 campaign included control tasks after the Stage 3 breakthrough. Stage 5 wrote
frame-half stability, synthetic recovery, anchor-holdout, persistent-NLOS, leakage,
and bootstrap tables [source: FULL_V5_rawframe_bruteforce_v3/tables/output_row_counts.csv].
The control set is part of the evidence because the raw-frame result could otherwise
be a histogram-overfitting artifact.

The frame-half table has 192 rows, one per position-anchor link, and checks whether
the selected estimator is stable between temporal halves of each static capture [source:
FULL_V5_rawframe_bruteforce_v3/tables/s5_frame_half.csv]. The synthetic recovery table
has 40 rows and injects known NLOS behavior to test whether the estimator can recover
the intended side of the distribution [source:
FULL_V5_rawframe_bruteforce_v3/tables/s5_synthetic_recovery.csv]. The anchor-holdout
table has 8 rows, one for each removed anchor, and tests sensitivity to single-anchor
removal [source: FULL_V5_rawframe_bruteforce_v3/tables/s5_anchor_holdout.csv].

The leakage assertion remains a cautionary table rather than a clean pass/fail headline.
It is included because D_tag calibration and estimator selection are both sensitive
with only 24 positions [source: FULL_V5_rawframe_bruteforce_v3/tables/s5_leakage.csv].
This report therefore uses lower_trim_20/V5/Huber30 as a strong campaign result and a
static batch-processing recommendation, while still retaining the nested-CV and
bootstrap caveats from the broader campaign [source:
FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s5_bootstrap_summary.csv].

### 12.6 Why V5 Wins When NLOS Is Removed

The raw-frame result resolves a tension in the earlier report. V5 is the physically
better anchor geometry by Sim3 scale and rigid anchor RMSE, but V4 is the better p50
static positioning geometry on this campaign [source:
FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv;
FULL_transfer_matrix/tables/transfer_matrix_48cells.csv]. That tension is expected
if V4's scale compression cancels a positive tag-anchor range bias.

Once lower_trim_20 reduces the positive range tail, the cancellation advantage should
shrink. That is what the v3 honest ranking shows: V5 is {honest_best['loo_median']:.1f}
mm, V4 is {honest_v4['loo_median']:.1f} mm, and Vicon is {honest_vicon['loo_median']:.1f}
mm [source: FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv]. The Vicon
row remains close, which is consistent with the phase-center caveat. The V4 row no
longer wins, which is the new evidence that V4's p50 advantage was partly an error
cancellation effect rather than a universally better geometry [source:
FULL_V5_phase_center_sensitivity/tables/a6_robustness_summary.csv].

This result should not be written as "V5 always wins." The correct wording is narrower:
on the Erlangen static campaign, V4 wins under p50 LOO, while V5 wins after raw-frame
LOS extraction with lower_trim_20. That distinction is the main new paper-ready
finding [source: FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv].

### 12.7 Relation to Earlier Percentile and Oracle Rows

The lower_trim_20 result should not be collapsed into the earlier p30 result. p30 is
a single percentile of the raw distribution, while lower_trim_20 averages the lowest
20% of valid raw samples. In the v3 all-data grid, several pure-percentile rows reached
low transductive medians, including p10/V5/Huber50 at {transductive_best['median_3d_mm']:.1f}
mm, p15/V5/Huber50 at {s2_best_est.loc[s2_best_est['estimator'].eq('p15'), 'median_3d_mm'].iloc[0]:.1f}
mm, and p07/V5/Huber50 at {s2_best_est.loc[s2_best_est['estimator'].eq('p07'), 'median_3d_mm'].iloc[0]:.1f}
mm [source: FULL_V5_rawframe_bruteforce_v3/tables/s2_best_per_estimator.csv]. Those rows
were not the final headline because Stage 3 re-ran the top configurations under
fold-wise D_tag calibration and found lower_trim_20/V5/Huber30 to be the best honest
row [source: FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

The oracle row is also narrower than a physical ground-truth oracle. The B0 oracle
selects or constructs a best recoverable link-level estimate from the available
range distributions. It does not use an external hardware LOS label. Its role is to
measure how much static positioning error is recoverable from the existing raw
histograms before changing hardware or anchor geometry. The oracle median is
{raw_oracle['all_data_median']:.1f} mm and the honest lower_trim_20 median is
{honest_best['loo_median']:.1f} mm [source:
FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv]. That small gap is why
the report calls lower_trim_20 a breakthrough, but the source remains the same 24
positions and the same UWB campaign.

The v3 run also contains a Stage 4 bundle-adjustment gate. It was skipped because
the Stage 3 result path did not require BA to establish the raw-frame breakthrough
[source: FULL_V5_rawframe_bruteforce_v3/reports/BRUTEFORCE_V3_COMPLETION.md;
FULL_V5_rawframe_bruteforce_v3/tables/s4_ba_best.csv]. That is important for
interpretation: the new 44.5 mm number is not produced by moving anchors freely or
by learning a new layout. It is produced by changing the static tag-anchor range
estimator, keeping the geometry label explicit, and recalibrating D_tag under LOO.

The deployment boundary is therefore clear. A static measurement mode can collect
dozens to hundreds of frames, compute lower_trim_20 per anchor, run Huber30, and use
a D_tag calibrated for that estimator. A dynamic single-frame mode cannot do this
without a temporal window. A future quasi-static implementation could accumulate a
short histogram when motion is low, using IMU variance or repeated range stability
as a gate; that remains untested in this report [source:
FULL_V5_rawframe_bruteforce_v2/reports/DATA_DISCOVERY.md;
FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].
"""

    section13 = f"""## 13. Anchor Self-Calibration with Lower_Trim

### 13.1 Hypothesis

The raw-frame tag result naturally raised a second question: if lower-tail statistics
recover a better LOS-side tag-anchor range, should the same lower-tail statistic be
used for inter-anchor ranges during anchor self-calibration? The blind anchor lower-trim
experiment tested that question by rebuilding anchor layouts with multiple inter-anchor
range aggregations and e_i settings, then evaluating tag accuracy with the same
lower_trim_20 static tag estimator [source:
FULL_V5_anchor_lower_trim/reports/ANCHOR_LOWER_TRIM_COMPLETION.md].

The hypothesis was directional. If inter-anchor raw ranges have the same positive
NLOS tails as tag-anchor ranges, lower_trim_20 should improve the anchor geometry.
If inter-anchor ranges are symmetric and stable, lower_trim_20 should bias the range
downward and worsen geometry. The experiment was blind in the sense that the result
was determined by the full tag-positioning replay after the anchor variants were
constructed [source: FULL_V5_anchor_lower_trim/tables/l2_anchor_solver_results.csv;
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

### 13.2 Inter-Anchor Distribution Analysis

The inter-anchor raw inventory contains 28 anchor pairs and {aa_raw_valid:,} raw
valid rows, with median {aa_median_frames:.0f} frames per pair [source:
FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv]. The mean skewness
is {ia_skew:.3f}, which is close to symmetric. The mean p50 minus lower_trim_20 is
{ia_p50_ltrim20:.1f} mm, meaning lower_trim_20 systematically shortens the pair ranges
even though the distributions do not show a strong right-tail structure [source:
FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv].

{anchor_dist_md}

This is physically different from the tag-anchor static histograms. Static tag-anchor
captures sample a tag placement in the room and can include link-specific NLOS tails.
Inter-anchor captures are static, repeated, and quasi-constant. Multipath can still
create a bias, but it does not necessarily appear as a frame-to-frame positive tail
that a lower-tail statistic can remove [source:
FULL_V5_anchor_lower_trim/reports/TASK_L1_INTER_ANCHOR_DISTRIBUTIONS.md].

### 13.3 Blind Test Results

The anchor blind test evaluated 24 anchor solver variants: eight range aggregations
and three e_i settings. The best lower_trim_20-anchor row was lower_trim_20 with
e_i=0, at {lower_trim_e0['loo_median_mm']:.1f} mm LOO. The current p50 control under
the raw-frame tag estimator is {p50_control['loo_median_mm']:.1f} mm, and p50 with
e_i=0 is {p50_e0['loo_median_mm']:.1f} mm [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

{anchor_result_md}

The blind result is therefore negative for inter-anchor lower trimming. The best
lower_trim_20-anchor row is worse by {p50_control['loo_median_mm'] - lower_trim_e0['loo_median_mm']:.1f}
mm relative to the p50 control when expressed as old minus new, and it is worse by
{lower_trim_e0['loo_median_mm'] - p50_e0['loo_median_mm']:.1f} mm relative to the
best p50/e_i=0 row [source:
FULL_V5_anchor_lower_trim/reports/ANCHOR_LOWER_TRIM_COMPLETION.md;
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv]. More aggressive lower-tail
methods also move the Sim3 scale upward and the rigid RMSE upward, which is consistent
with downward-biased inter-anchor ranges [source:
FULL_V5_anchor_lower_trim/tables/l2_anchor_solver_results.csv].

The engineering conclusion is explicit: keep p50/median for inter-anchor ranges in
this room. lower_trim_20 is tag-side, not anchor-side, for the Erlangen static dataset
[source: FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv;
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

### 13.4 e_i=0 Finding

The best overall anchor lower-trim row is not a lower-trim anchor row. It is p50
with e_i=0: {p50_e0['loo_median_mm']:.1f} mm LOO, P95 {p50_e0['p95_mm']:.1f} mm,
and RMSE {p50_e0['rmse_mm']:.1f} mm [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv]. p50 with e_reg=5 is
essentially tied at {p50_reg5['loo_median_mm']:.1f} mm. The current p50/e_reg20
control is {p50_control['loo_median_mm']:.1f} mm [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

The improvement is not yet statistically decisive. The paired bootstrap summary gives
old median {l5['old_median_mm']:.1f} mm, new median {l5['new_median_mm']:.1f} mm,
mean improvement {l5['mean_improvement_mm']:.1f} mm, median improvement
{l5['median_improvement_mm']:.1f} mm, CI {l5['ci95_low_mm']:.1f} to
{l5['ci95_high_mm']:.1f} mm, and P(new wins) {l5['p_new_wins']:.3f} [source:
FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv].

The result is consistent with the mechanistic M4 counterfactual, where forcing e_i=0
improved a prior V5 median from 67.8 mm to 64.5 mm [source:
FULL_V5_mechanistic_deepdive/tables/m4_counterfactual.csv]. It is also consistent
with the idea that per-anchor e_i can absorb campaign-specific residual structure.
The cautious recommendation is to test e_i=0 and low e_reg in the next calibration,
not to delete e_i support from the solver [source:
FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv].

### 13.5 Consequences for the V5 Anchor Solver

The lower-trim anchor test separates two V5 design choices that were sometimes
discussed together. The first is the common-mode delay parameterization, which fixes
the V4 scale problem. That remains supported: p50/e_i=0 has Sim3 scale
{p50_e0['sim3_scale']:.3f} and rigid RMSE {p50_e0['rigid_rmse_mm']:.1f} mm, while the
current V5 p50 artifact had scale about 1.010 and rigid RMSE about 63 mm [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv;
FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv]. The second is the
per-anchor residual e_i model. That part is less settled. In this experiment, reducing
or zeroing e_i improves the raw-frame static tag result, but the improvement is small
relative to the bootstrap uncertainty [source:
FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv].

The practical solver audit implication is that V5 should not be described as one
immutable configuration. It is a family around the common-mode idea: p50 inter-anchor
aggregation, common-mode c, optional e_i, and a chosen e_i regularization. The official
artifact used e_reg20; the blind experiment suggests e_i=0 or e_reg5 as candidate
settings for the next capture. The code and tables should therefore log `e_mode`,
`e_reg_mm`, `c_mm`, `e_i_spread_mm`, and the tag-side range estimator whenever V5
results are reported [source:
FULL_V5_anchor_lower_trim/tables/l2_anchor_solver_results.csv;
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

The experiment also gives a rule for future rooms. Before applying lower-tail
aggregation to inter-anchor ranges, inspect the raw AA distribution shape. If skewness
and tail mass resemble the Erlangen AA table, p50 should be retained. If a new room
has genuinely right-skewed inter-anchor distributions, lower_trim_20 or another
LOS-side statistic can be re-tested as a room-specific hypothesis rather than adopted
by default [source: FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv].
"""

    section14 = f"""## 14. Consolidated Findings

The V2 claim-control table keeps the original evidence levels and adds five claims
from the raw-frame and anchor lower-trim work [source: tables/claim_evidence_matrix_v2.csv].
The levels are:

{level_md}

### 14.A New and Updated Claims

{new_claims_md}

The most important upgrade is claim 26. It is Level A within this campaign because
it is supported by raw data discovery, an exhaustive v3 grid, fold-wise LOO, an oracle
comparison, and bootstrap controls [source:
FULL_V5_rawframe_bruteforce_v2/reports/DATA_DISCOVERY.md;
FULL_V5_rawframe_bruteforce_v3/tables/s2_full_grid.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv]. The claim is limited to
static or batch settings where enough frames exist to form a per-link histogram.

Claim 27 is also Level A within this campaign because the blind anchor test found
the opposite result for inter-anchor ranges. The same lower-tail statistic that helps
tag-anchor static histograms worsens inter-anchor self-calibration in this room [source:
FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv;
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

Claim 29 is the conceptual update to the earlier cancellation story. Earlier evidence
showed V5 fixes scale but V4 wins p50 static positioning. The raw-frame result shows
that V5 wins once tag-anchor right-tail bias is reduced. This does not prove a universal
transfer result, but it does independently support the cancellation explanation for
the p50 ranking [source: FULL_transfer_matrix/tables/transfer_matrix_48cells.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

The Level B e_i claim should be worded as an engineering candidate. p50/e_i=0 gives
the lowest current median, but P(new wins) is 0.659 and the 95% paired improvement
interval crosses zero [source: FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv].

The new Level D mixture-model claim is a restriction on wording. Mixture models were
tested and did not beat the simple lower_trim_20 row in the final honest result [source:
FULL_V5_rawframe_bruteforce_v2/tables/b6_master_comparison.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

### 14.B Consolidated Interpretation

The final campaign narrative is now three-layered. Anchor geometry: V5 fixes the V4
scale leak and reduces anchor coordinate error against Vicon [source:
FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv]. Median static p50
positioning: V4 wins because its scale error cancels part of the tag-anchor range
bias [source: FULL_transfer_matrix/tables/transfer_matrix_48cells.csv]. Raw static
histogram positioning: lower_trim_20 removes much of that range bias, and V5 then
becomes the best geometry [source: FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

The raw-frame campaign therefore strengthens the paper, but it also narrows the
deployment claim. The 44.5 mm number is a static batch result. It should not be used
for ROTO single-frame tracking, and it should not be presented as a proven firmware
real-time update until a sliding-window or quasi-static implementation is tested
[source: FULL_V5_followup_validation/reports/FOLLOWUP_VALIDATION_SUMMARY.md;
FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].
"""

    section15 = f"""## 15. Negative Results Summary

The negative results are retained because they prevent overclaiming and separate
static batch improvements from real-time dynamic positioning. The V2 report adds two
negative findings: lower_trim_20 is inappropriate for inter-anchor ranges in this
room, and parametric mixture estimators do not beat the simple non-parametric
lower_trim_20 tag-side estimator [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

{negative_md}

The lower_trim_20 inter-anchor failure is not a contradiction of the raw-frame tag
success. It shows that estimator choice must follow the measurement distribution:
right-skewed tag-anchor static histograms benefit from lower-tail extraction, while
nearly symmetric inter-anchor distributions do not [source:
FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv].
"""

    section16 = """## 16. Updated Engineering Recommendations

### 16.1 Layer 1: Anchor Self-Calibration

Use p50/median for inter-anchor range aggregation in the Erlangen-style setup. The
inter-anchor raw distributions are nearly symmetric, with mean skewness 0.063, and
lower_trim_20 worsened the blind anchor refit [source:
FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv;
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

Prefer e_i=0 or low e_reg as a next-campaign candidate, not as a locked replacement.
p50/e_i=0 is the current best row at 43.2 mm, p50/e_reg5 is 43.2 mm, and the current
p50/e_reg20 control is 44.5 mm, but the paired bootstrap P(new wins) is 0.659 [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv;
FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv].

Keep Huber-capable anchor and tag solver paths available. The winning raw-frame static
row uses Huber30, and earlier solver-audit work flagged that solver loss handling
should remain explicit rather than implicit [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv;
../../solver_audit/reports/SOLVER_AUDIT_SUMMARY.md].

### 16.2 Layer 2: Tag Position Solver

For static or batch operation with at least tens of frames per link, replace p50 with
lower_trim_20 over raw tag-anchor range histograms. The current honest row is 44.5 mm
LOO versus 67.8 mm for the V5 p50 baseline [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv;
FULL_V5_followup_validation/tables/f6_final_comparison.csv].

For real-time dynamic operation with one range frame per solve, do not use lower_trim_20.
There is no histogram. The current ROTO floor remains about 101.5 mm under the
conservative anchor-bridge best-fit alignment [source:
FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv]. Huber loss remains applicable
because it acts on anchor residuals within a frame rather than on a per-link temporal
histogram [source: FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

### 16.3 Layer 3: D_tag Calibration

Treat D_tag as per-device and per-estimator. The p50 V5 LOO value is 49.621 mm, but
the lower_trim_20/V5/Huber30 raw-frame row has mean train D_tag 6.9 mm, and the anchor
lower-trim p50/e_i=0 row has mean D_tag 8.5 mm [source:
FULL_4way_comparison/tables/v5_loo_tag_delay_summary.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv;
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

Do not hardcode the p50 D_tag in firmware for lower-trim static processing. The range
estimator changes the range observation and therefore changes the calibrated D_tag
[source: FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

### 16.4 Layer 4: Firmware and Capture

Keep saving raw per-frame ranges. The raw-frame data already exists in the current
`tr_all.csv` captures, and the V2 data-discovery run confirmed the needed columns and
frame counts [source: FULL_V5_rawframe_bruteforce_v2/reports/DATA_DISCOVERY.md].

Add hardware time synchronization for ROTO if dynamic accuracy remains a paper target.
Time-offset optimization recovered little under the current data, but the alignment
audit shows that reported ROTO error depends strongly on alignment convention [source:
FULL_V5_roto_deepdive/tables/r1_time_corrected_results.csv;
FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].

Continue treating IMU orientation, CIR/NLOS labels, and a 9th anchor as pending
hardware/data improvements. The skipped board-frame analysis and the simulated 9th
anchor result both point to data that cannot be recovered from the current static
tables alone [source:
FULL_V5_extended_mechanism_ablations/reports/EXTENDED_MECHANISM_ABLATION_SUMMARY.md;
FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv].

### 16.5 Implementation Checklist

For the next static batch run, the minimum reportable configuration should contain
five fields: anchor layout source, anchor e_i setting, tag range estimator, solver
loss, and D_tag calibration method. A complete row would read, for example:
`V5 p50 anchors, e_i=0, lower_trim_20 tag ranges, Huber30, D_tag LOO`. Without those
fields, the numbers 43.2 mm, 44.5 mm, 49.6 mm, and 67.8 mm can be confused even
though they describe different pipelines [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv;
FULL_V5/tables/static_summary_DLOO.csv].

For firmware or online software, add two explicit operating modes. A real-time mode
should use one frame per solve, Huber residual handling, and a D_tag calibrated for
that mode. A static refinement mode should buffer frames per anchor and compute the
lower_trim_20 statistic before solving. The two modes should not share a hardcoded
D_tag because the range estimator changes the effective bias [source:
FULL_4way_comparison/tables/v5_loo_tag_delay_summary.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

For analysis scripts, keep the raw-frame inventory check near the front of the
pipeline. The v2 discovery step caught an important distinction: per-link feature
tables with 192 rows do not prove that raw frames are absent; they may simply be
derived summaries. The script should explicitly check static files, raw row counts,
valid row counts, frame counts, and the presence of `anchor_id`, `range_mm`, `valid`,
and `sweep` columns before deciding which estimator family is available [source:
FULL_V5_rawframe_bruteforce_v2/reports/DATA_DISCOVERY.md].
"""

    section17 = """## 17. Open Questions and Recommended Next Steps

The highest priority next experiment is a second-room static repeat using the same
raw-frame pipeline. The V3 result is strong within the Erlangen campaign, but the
bootstrap interval is wide and the dataset still contains only 24 independent static
positions [source: FULL_V5_rawframe_bruteforce_v3/tables/s5_bootstrap_summary.csv].
The second room should report p50 V4, p50 V5, lower_trim_20 V5, p50/e_i=0 anchor
variants, and the same hard validation splits [source:
FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv].

A second priority is to test whether lower_trim_20 remains inappropriate for
inter-anchor ranges in a more obstructed anchor deployment. The Erlangen inter-anchor
distributions are nearly symmetric, but a different room could have a genuine
inter-anchor NLOS tail [source:
FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv]. If the skewness
and tail mass are higher in a new room, the anchor-side rule should be retested rather
than assumed.

A third priority is D_tag self-calibration from unknown positions. The current D_tag
values are tied to known static positions or fold-wise known-position residuals.
Deployment needs a way to calibrate a tag with 3-5 arbitrary static placements without
Vicon truth [source:
FULL_V5_extended_mechanism_ablations/tables/item11_calibration_learning_curve_summary.csv;
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

A fourth priority is a quasi-static sliding-window lower-trim mode. lower_trim_20 is
not usable for one-frame ROTO, but it may be useful for slow moving or stationary user
states if the firmware accumulates a short histogram per anchor before solving [source:
FULL_V5_rawframe_bruteforce_v3/tables/raw_link_inventory.csv;
FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].

The remaining hardware questions from V1 still stand: physical phase-center measurement,
tag-orientation sweeps, CIR firmware with ground-truth NLOS labels, and a real 9th
anchor trial. The raw-frame result makes these more valuable because the static solver
is now close to its oracle lower bound; further improvements will likely need new
observability or new physical measurements rather than more post-hoc scalar tuning
[source: FULL_V5_phase_center_sensitivity/tables/a6_robustness_summary.csv;
FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv].
"""

    appendix = f"""## Appendix A. Complete Numerical Registry V2

The following table extends the grand-synthesis registry with raw-frame and anchor
lower-trim entries. Unrounded values are preserved in `tables/master_number_registry_v2.csv`
[source: tables/master_number_registry_v2.csv].

{registry_md}

## Appendix B. Consistency Audit V2

{consistency_md}

## Appendix C. Source Inventory and Runtime Notes

The V2 report scope contains 23 directories: the original 19 and the four additional
raw-frame/anchor-lower-trim directories [source: tables/report_source_inventory_v2.csv].

{inventory_md}

The true brute-force v3 completed in {2256.6:.1f} s, or 37.6 min. Stage 1 mixture
fitting took 1684.5 s, Stage 2 full grid evaluation took 24.4 s, Stage 3 honest LOO
took 37.4 s, and Stage 5 bootstrap/controls took 510.1 s [source:
FULL_V5_rawframe_bruteforce_v3/tables/cumulative_runtime_summary.csv]. The anchor
lower-trim blind experiment completed in 11.9 s [source:
FULL_V5_anchor_lower_trim/tables/stage_status.csv].

## Appendix D. Figure Manifest

The report directory copies key figures for convenience. The source file remains
the authoritative artifact [source: tables/figure_manifest_v2.csv].

{figure_md}
"""

    report = "\n\n".join(
        [
            "# Comprehensive Experimental Report V2: Erlangen 28-May-2026 V5 Analysis Campaign",
            f"Generated: {datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}",
            "This internal report updates the V4/V5 AutoPos analysis record with the raw-frame brute-force campaign and the anchor lower_trim blind experiment. It is a technical record, not a paper draft.",
            exec_summary.strip(),
            core_sections.strip(),
            section12.strip(),
            section13.strip(),
            section14.strip(),
            section15.strip(),
            section16.strip(),
            section17.strip(),
            appendix.strip(),
        ]
    )
    report += f"\n\n<!-- Word count: {word_count(report)} -->\n"
    out_path = REPORT_DIR / "EXPERIMENTAL_REPORT_V2.md"
    out_path.write_text(report)

    # Output row counts.
    row_count_rows = []
    for p in sorted(TABLE_DIR.glob("*.csv")):
        try:
            n = len(pd.read_csv(p))
        except Exception:
            n = -1
        row_count_rows.append({"file": f"tables/{p.name}", "rows": n})
    for p in sorted(REPORT_DIR.glob("*.md")):
        row_count_rows.append({"file": f"report/{p.name}", "rows": len(p.read_text().splitlines())})
    for p in sorted(FIG_DIR.glob("*.png")):
        row_count_rows.append({"file": f"figures/{p.name}", "rows": -1})
    pd.DataFrame(row_count_rows).to_csv(TABLE_DIR / "output_row_counts_v2.csv", index=False)

    print(f"Wrote {out_path}")
    print(f"Word count: {word_count(report)}")
    print(f"Tables written: {len(list(TABLE_DIR.glob('*.csv')))}")
    print(f"Figures copied: {len(list(FIG_DIR.glob('*.png')))}")


if __name__ == "__main__":
    main()
