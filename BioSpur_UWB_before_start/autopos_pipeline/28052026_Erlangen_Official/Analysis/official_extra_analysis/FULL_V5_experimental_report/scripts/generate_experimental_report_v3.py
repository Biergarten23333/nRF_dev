#!/usr/bin/env python3
"""Generate the V3 editorial revision of the experimental report.

V3 does not create new analysis data. It rewrites the report narrative around the
delay-layout coupling thesis, compresses the raw-frame section, and keeps the V2
tables/figures as-is.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd


BASE = Path("/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline")
ANALYSIS = BASE / "28052026_Erlangen_Official" / "Analysis" / "official_extra_analysis"
OUT = ANALYSIS / "FULL_V5_experimental_report"
REPORT_DIR = OUT / "report"
TABLE_DIR = OUT / "tables"


def read_csv(name: str) -> pd.DataFrame:
    return pd.read_csv(TABLE_DIR / name)


def fmt(value, ndigits: int = 1, empty: str = "") -> str:
    try:
        if pd.isna(value):
            return empty
        return f"{float(value):.{ndigits}f}"
    except Exception:
        return str(value)


def md_table(rows: list[dict], columns: list[str]) -> str:
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in rows:
        vals = []
        for col in columns:
            val = row.get(col, "")
            if val is None or (isinstance(val, float) and pd.isna(val)):
                val = ""
            vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def section(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end)].strip()


def row(df: pd.DataFrame, col: str, value: str) -> pd.Series:
    return df[df[col].astype(str).eq(str(value))].iloc[0]


def main() -> None:
    v2_path = REPORT_DIR / "EXPERIMENTAL_REPORT_V2.md"
    v2 = v2_path.read_text()

    preface = v2[: v2.index("## 0. Executive Summary")].strip()
    sections_1_to_11 = section(v2, "## 1. Dataset", "## 12. Raw-Frame")
    old_section_13 = section(v2, "## 13. Anchor", "## 14. Consolidated")
    old_appendix = v2[v2.index("## Appendix A") :].strip()

    headline = read_csv("locked_headline_v2.csv")
    registry = read_csv("master_number_registry_v2.csv")

    h = {str(r["Row"]): r for _, r in headline.iterrows()}
    reg = {str(r["metric_name"]): r for _, r in registry.iterrows()}

    # Frequently used values.
    v4_scale = float(reg["v4-io_sim3_scale"]["value"])
    v5_scale = float(reg["v5-commonmode_sim3_scale"]["value"])
    v4_p50 = float(h["B"]["Median 3D mm"])
    v5_p50 = float(h["C"]["Median 3D mm"])
    lt_v5 = float(h["O"]["Median 3D mm"])
    lt_v5_p95 = float(h["O"]["P95 mm"])
    lt_v5_rmse = float(h["O"]["RMSE mm"])
    v4_raw = 53.868441
    oracle = float(h["Q"]["Median 3D mm"])
    v5_p50_p95 = float(h["C"]["P95 mm"])
    v4_p50_p95 = float(h["B"]["P95 mm"])
    raw_rows = int(float(reg["raw_static_rows"]["value"]))
    raw_valid = int(float(reg["raw_static_valid_rows"]["value"]))
    raw_ratio = float(reg["raw_expected_ratio"]["value"])
    grid_configs = int(float(reg["rawframe_v3_grid_configs"]["value"]))
    boot_low = float(reg["rawframe_bootstrap_ci95_low"]["value"])
    boot_high = float(reg["rawframe_bootstrap_ci95_high"]["value"])
    aa_rows = int(float(reg["aa_valid_rows"]["value"]))
    aa_skew = float(reg["aa_mean_skewness"]["value"])
    aa_delta = float(reg["aa_mean_p50_minus_lower_trim20"]["value"])
    anchor_lt = float(reg["anchor_lower_trim20_best_loo"]["value"])
    anchor_p50_e0 = float(reg["anchor_p50_e_zero_best_loo"]["value"])
    anchor_control = float(reg["anchor_current_p50_control_loo"]["value"])
    p_new_wins = float(reg["anchor_e_zero_p_new_wins"]["value"])

    headline_rows = headline.to_dict("records")
    headline_md = md_table(
        headline_rows,
        ["Row", "Variant", "Median 3D mm", "P95 mm", "RMSE mm", "Evaluation", "New", "Source"],
    )

    claim_rows = [
        {"Level": "A", "Claim count": 10, "Meaning": "Proven within this campaign"},
        {"Level": "B", "Claim count": 10, "Meaning": "Supported with caveats"},
        {"Level": "C", "Claim count": 5, "Meaning": "Hypothesis only"},
        {"Level": "D", "Claim count": 2, "Meaning": "Disproven or should not be claimed"},
    ]
    claim_level_md = md_table(claim_rows, ["Level", "Claim count", "Meaning"])

    revised_claim_rows = [
        {
            "ID": 26,
            "Level": "B",
            "Claim": "Lower-quantile tag-anchor range aggregation improves static positioning when enough frames are accumulated; the improvement comes from reducing NLOS positive bias.",
            "Recommended wording": "In static batch mode with about 1200 frames per link, lower-quantile aggregation reduces tag-anchor positive bias and gives 44.5 mm median LOO on this campaign.",
        },
        {
            "ID": 27,
            "Level": "A",
            "Claim": "Inter-anchor raw ranges are nearly symmetric; lower-tail aggregation is inappropriate for anchor self-calibration here.",
            "Recommended wording": "Use p50/median for inter-anchor self-calibration in this dataset; the lower-tail statistic is tag-anchor specific.",
        },
        {
            "ID": 28,
            "Level": "B",
            "Claim": "e_i=0 or very small e_i is slightly better for the raw-frame static estimator.",
            "Recommended wording": "The e_i=0 variant is the current best row, but its advantage is not statistically locked with 24 positions.",
        },
        {
            "ID": 29,
            "Level": "A",
            "Claim": "When tag-anchor NLOS positive bias is reduced, V4's cancellation advantage disappears and V5 becomes the better geometry.",
            "Recommended wording": "This ranking flip independently confirms the delay-layout coupling mechanism on the Erlangen campaign.",
        },
    ]
    revised_claim_md = md_table(revised_claim_rows, ["ID", "Level", "Claim", "Recommended wording"])

    section0 = f"""## 0. Executive Summary

This campaign demonstrates that UWB anchor self-calibration has a fundamental
delay-layout coupling problem: the solver can trade metric scale for delay parameters,
producing a geometrically distorted layout that accidentally cancels NLOS-induced
range bias. The V5 common-mode parameterization fixes this coupling and restores
metric scale (Sim3 {v5_scale:.3f} vs V4 {v4_scale:.3f}), but on p50 ranges the
cancellation-assisted V4 layout still gives lower single-environment positioning
error ({v4_p50:.1f} vs {v5_p50:.1f} mm). When tag-anchor NLOS bias is reduced through
lower-quantile range aggregation, the cancellation advantage disappears and V5 becomes
the better geometry ({lt_v5:.1f} vs {v4_raw:.1f} mm LOO). This ranking flip is the
strongest independent confirmation of the coupling mechanism.

The report should therefore be read as evidence for one central thesis: the geometry
that minimizes single-environment positioning error is not necessarily the physically
correct geometry. V4 is better under p50 static positioning on the Erlangen campaign,
but its scale is wrong. V5 is physically better by anchor-scale metrics and becomes
empirically better when tag-anchor positive range bias is reduced [source:
FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv;
FULL_transfer_matrix/tables/transfer_matrix_48cells.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

The supporting evidence is broad. The Sim3 comparison shows the anchor-side scale
defect directly. The transfer matrix shows that V4's p50 layout gives the lower static
median in the same environment. The profile likelihoods, Fisher eigenvalue, and morph
valley show weakly identifiable delay-layout directions. The raw-frame static batch
result shows that once the tag-anchor positive tail is reduced, V4's empirical advantage
disappears [source: FULL_V5_batch3_falsification/tables/f3_profile_alpha_dtag.csv;
FULL_V5_GPU_tier1/reports/task2_status.json;
FULL_V5_extended_mechanism_ablations/tables/item06_morph_markers.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

Top-level findings after the V3 editorial revision:

- V5 fixes the anchor-side scale leak: V4 Sim3 scale is {v4_scale:.3f} and V5 Sim3
  scale is {v5_scale:.3f}; rigid anchor RMSE improves from 105.4 mm to 63.0 mm
  [source: FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv].
- V4 remains the p50 static LOO winner on this campaign: {v4_p50:.1f} mm for V4+LOO
  versus {v5_p50:.1f} mm for V5+LOO [source:
  FULL_transfer_matrix/tables/transfer_matrix_48cells.csv].
- In static batch mode with about 1200 frames per link, lower-quantile tag-anchor
  aggregation with V5 and Huber30 reaches {lt_v5:.1f} mm median LOO, while the best
  V4 geometry row in the same raw-frame ranking is {v4_raw:.1f} mm [source:
  FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].
- The median gain does not solve the tail: lower-quantile V5 has P95 {lt_v5_p95:.1f}
  mm and RMSE {lt_v5_rmse:.1f} mm, while V4+LOO p50 has P95 {v4_p50_p95:.1f} mm
  [source: FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv;
  FULL_transfer_matrix/tables/transfer_matrix_48cells.csv].
- Inter-anchor lower-tail aggregation is a negative result. Inter-anchor raw ranges
  are nearly symmetric (mean skewness {aa_skew:.3f}), and the best lower-tail anchor
  row is {anchor_lt:.1f} mm versus {anchor_control:.1f} mm for the current p50 control
  [source: FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv;
  FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].
- ROTO remains a separate dynamic limitation. The conservative V5 best-fit-aligned
  median is 101.5 mm, and static lower-quantile aggregation does not apply to one-frame
  dynamic ranges [source: FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv;
  FULL_V5_followup_validation/reports/FOLLOWUP_VALIDATION_SUMMARY.md].

### Updated Locked Headline Table

{headline_md}

Note: Row O ({lt_v5:.1f} mm median) has P95 = {lt_v5_p95:.1f} mm and RMSE =
{lt_v5_rmse:.1f} mm. The median improvement does not extend to the tail. Row B
(V4+LOO, {v4_p50:.1f} mm median) has P95 = {v4_p50_p95:.1f} mm. Readers should
compare both median and P95 when evaluating these rows.

### Claim Confidence Levels

{claim_level_md}

The V3 narrative removes the old claim that tested mixture models are a scientific
finding and downgrades the lower-quantile method claim from Level A to Level B. The
method itself is simple; the scientific result is the ranking flip that confirms
delay-layout coupling [source: FULL_V5_experimental_report/tables/claim_evidence_matrix_v2.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].
"""

    section12 = f"""## 12. Raw-Frame LOS Extraction Campaign

### 12.1 Raw-Frame Data Availability

The raw-frame campaign began by checking whether the static captures actually contain
per-frame tag-anchor range data. They do. The v2 discovery run found 24 static
`tr_all.csv` files with one row per sweep-anchor observation, {raw_rows:,} raw rows,
{raw_valid:,} valid rows, and a ratio of {float(reg['raw_expected_ratio']['value']):.3f}
to the nominal 24 x 8 x 1200 observations [source:
FULL_V5_rawframe_bruteforce_v2/tables/raw_data_inventory.csv;
FULL_V5_rawframe_bruteforce_v2/reports/DATA_DISCOVERY.md].

The first capture had 31 columns, including `host_elapsed_s`, `host_epoch_s`, `sweep`,
`anchor_id`, `raw_mm`, `range_mm`, `quality_percent`, `valid`, and IMU summary fields
[source: FULL_V5_rawframe_bruteforce_v2/reports/DATA_DISCOVERY.md]. The v3 raw-link
inventory contains 192 links, one for each of 24 positions and 8 anchors. Each link
stores distribution statistics such as min, p01, p05, p40, p50, and max [source:
FULL_V5_rawframe_bruteforce_v3/tables/raw_link_inventory.csv].

This raw data matters because p50 collapses a full static distribution into a single
number. With roughly 1200 samples per link, each tag-anchor pair has enough information
to test whether lower-quantile aggregation changes the positioning result. The method
is not the scientific contribution; taking a lower fraction of samples is obvious.
The value of the experiment is that it tests whether reducing the positive tag-anchor
tail changes the V4/V5 ranking [source:
FULL_V5_rawframe_bruteforce_v3/tables/s2_full_grid.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

### 12.2 Ranking Flip Under NLOS Reduction

Under p50 aggregation, V4+C_V4+D_LOO gives {v4_p50:.1f} mm median 3D error and
V5+C_V5+D_LOO gives {v5_p50:.1f} mm [source:
FULL_transfer_matrix/tables/transfer_matrix_48cells.csv]. This is the original
paradox: V4 has the worse physical scale but the better single-environment p50
positioning median.

When tag-anchor ranges are aggregated by lower-quantile methods, that ranking reverses.
The honest raw-frame v3 ranking gives {lt_v5:.1f} mm LOO for V5 with lower_trim_20
and Huber30. The best V4 geometry row in the same ranking is {v4_raw:.1f} mm. The
best Vicon geometry row is 44.7 mm [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv]. This is the strongest
new evidence in the V3 report because it links the empirical ranking directly to
the NLOS component that V4 was apparently cancelling.

The oracle lower bound in the v3 master ladder is {oracle:.1f} mm [source:
FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv]. The blind lower-quantile
V5 row is {lt_v5:.1f} mm, so the campaign-level median nearly reaches the recoverable
limit from the available raw distributions. This should be interpreted narrowly:
in a static batch mode with about 1200 frames per link, lower-quantile aggregation
achieves {lt_v5:.1f} mm median 3D error under LOO on this campaign. It is not a
general statement about one-frame real-time UWB tracking.

The tail remains unresolved. V5 p50 has median {v5_p50:.1f} mm and P95
{v5_p50_p95:.1f} mm. The lower-quantile V5 row has median {lt_v5:.1f} mm but P95
{lt_v5_p95:.1f} mm and RMSE {lt_v5_rmse:.1f} mm [source:
FULL_V5_followup_validation/tables/f6_final_comparison.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv]. The method improves
typical error but does not solve worst-case static positioning. The 5000-iteration
bootstrap interval is also wide: {boot_low:.1f} to {boot_high:.1f} mm [source:
FULL_V5_rawframe_bruteforce_v3/tables/s5_bootstrap_summary.csv].

The earlier p30 finding should be read through this section. p30 was an incomplete
preview of the same effect; the v3 result uses the full raw static distribution and
fold-wise D_tag recalibration [source:
FULL_V5_followup_validation/tables/f4_percentile_recalibrated.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv]. The correct conclusion
is that lower-quantile aggregation reduces tag-anchor NLOS positive bias, which
eliminates V4's cancellation advantage and reveals V5 as the metrically correct and
empirically better geometry on this campaign.
"""

    # Compress the old section 13 by replacing 13.5 and adding the requested
    # physical sentence in 13.2.
    section13 = old_section_13.replace(
        "This is physically different from the tag-anchor static histograms. Static tag-anchor\n"
        "captures sample a tag placement in the room and can include link-specific NLOS tails.\n"
        "Inter-anchor captures are static, repeated, and quasi-constant. Multipath can still\n"
        "create a bias, but it does not necessarily appear as a frame-to-frame positive tail\n"
        "that a lower-tail statistic can remove [source:\n"
        "FULL_V5_anchor_lower_trim/reports/TASK_L1_INTER_ANCHOR_DISTRIBUTIONS.md].",
        "This is physically different from the tag-anchor static histograms. Static tag-anchor\n"
        "captures sample a tag placement in the room and can include link-specific NLOS tails.\n"
        "Both anchors are static and wall-mounted; multipath creates a quasi-static bias rather\n"
        "than a frame-variable positive tail. Multipath can still create a bias, but it does\n"
        "not necessarily appear as a positive tail that a lower-tail statistic can remove\n"
        "[source: FULL_V5_anchor_lower_trim/reports/TASK_L1_INTER_ANCHOR_DISTRIBUTIONS.md].",
    )
    if "### 13.5 Consequences for the V5 Anchor Solver" in section13:
        before, tail = section13.split("### 13.5 Consequences for the V5 Anchor Solver", 1)
        section13 = before.strip() + "\n\n" + (
            "The solver consequence is simple. V5 should be described as a common-mode family, "
            "not one immutable configuration: p50 inter-anchor aggregation, common-mode c, "
            "optional e_i, and a chosen e_i regularization. The official artifact used e_reg20; "
            "the blind experiment suggests e_i=0 or e_reg5 as candidate settings for the next "
            "capture, but the small bootstrap-supported advantage is not enough to remove e_i "
            "support from the solver [source: FULL_V5_anchor_lower_trim/tables/l2_anchor_solver_results.csv; "
            "FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv]."
        )

    section14 = f"""## 14. Consolidated Findings

V3 organizes the claim matrix around the delay-layout coupling thesis. Single-environment
positioning accuracy is not a reliable proxy for physical calibration quality because
the solver can trade scale and delay against structured range bias. V4, V5, Vicon,
raw-frame lower-quantile aggregation, Fisher analysis, and the cancellation valley
are all evidence for this one mechanism rather than isolated wins and losses [source:
FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

{claim_level_md}

### 14.A Revised New Claims

{revised_claim_md}

Claim 26 is downgraded from Level A to Level B. The operation is technically simple:
lower-quantile aggregation reduces the positive tail of a static tag-anchor range
histogram. The supported finding is that this improves median static positioning when
enough frames are accumulated, while the P95 remains high [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

Claim 29 remains Level A because it is the core mechanism result. Under p50, V4 wins
despite worse physical scale. Under lower-quantile tag-anchor aggregation, V5 wins.
That ranking flip independently confirms that V4's p50 advantage came from beneficial
delay-layout-NLOS cancellation on this campaign [source:
FULL_transfer_matrix/tables/transfer_matrix_48cells.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

The previous claim about mixture-model ranking is removed from the claim matrix.
The simplest lower-tail statistic outperformed the tested mixture models on this
dataset, but that is a methodological outcome, not a paper-level scientific claim
[source: FULL_V5_rawframe_bruteforce_v2/tables/b6_master_comparison.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

### 14.B Consolidated Interpretation

The campaign now supports four ordered statements. First, V5 fixes metric anchor
scale. Second, V4 wins p50 static positioning in this one environment because the
distorted layout cancels part of the tag-anchor positive bias. Third, reducing that
positive bias reverses the empirical ranking and makes V5 the better geometry. Fourth,
the remaining P95 and ROTO results show that typical static error and tail/dynamic
error are different problems [source:
FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv;
FULL_transfer_matrix/tables/transfer_matrix_48cells.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv;
FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].
"""

    section15 = f"""## 15. Negative Results Summary

The negative results remain part of the experimental record. They prevent accidental
overclaiming and separate static batch median improvements from tail behavior,
dynamic tracking, and generalization.

| Experiment | Result | Interpretation | Source |
| --- | --- | --- | --- |
| MLP learned range correction | MLP residual median 118.0 mm versus scalar 98.5 mm | 24 static positions were too few for a learned correction model | FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md |
| GNN attention correction | attention residual median 121.1 mm | graph model overfit or lacked enough independent data | FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md |
| Solver search | best 82.7 mm in GPU discovery; fixed search still about 82.6 mm | no candidate beat V4/V5 baselines after proper D_tag LOO handling | FULL_V5_overnight_batch2/tables/n2_solver_search_fixed.csv |
| Bayesian Gaussian posterior | 95% coverage 0.33; Student-t increased it to 0.46 | posterior remained under-calibrated | FULL_V5_final_gate/tables/g2_unified_noise_models.csv |
| NLOS detector generalization | random PR-AUC 0.949 collapsed to 0.42-0.55 in hard splits | model memorized anchor identity and campaign-specific structure | FULL_V5_batch3_falsification/tables/f5_nlos_splits.csv |
| Rigid two-tag ROTO solver | joint range-level solver 261.8-264.2 mm versus independent 101.1 mm | tested constraint forced geometry but did not solve dynamic range bias | FULL_V5_final_gate/tables/g5_joint_solver_summary.csv |
| p30 dynamic transfer | ROTO p30 best 283.9 mm versus raw/p50 101.5 mm | static percentile aggregation did not transfer to single-frame dynamic ranges | FULL_V5_followup_validation/reports/FOLLOWUP_VALIDATION_SUMMARY.md |
| lower-tail aggregation for inter-anchor ranges | best lower-tail anchor row {anchor_lt:.1f} mm versus p50 control {anchor_control:.1f} mm | inter-anchor distributions were nearly symmetric, so lower-tail aggregation introduced downward bias | FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv |

The negative inter-anchor result is not a contradiction of the tag-anchor result.
It shows that estimator choice must follow the measurement distribution: tag-anchor
static histograms can contain a positive frame-variable tail, while inter-anchor
raw ranges in this room were nearly symmetric [source:
FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv].
"""

    section16 = f"""## 16. Updated Engineering Recommendations

### 16.1 Layer 1: Anchor Self-Calibration

Use p50/median for inter-anchor range aggregation in the Erlangen-style setup. The
inter-anchor raw distributions are nearly symmetric, with mean skewness {aa_skew:.3f},
and lower-tail aggregation worsened the blind anchor refit [source:
FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv;
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

Prefer e_i=0 or low e_reg as a next-campaign candidate, not as a locked replacement.
p50/e_i=0 is the current best row at {anchor_p50_e0:.1f} mm, p50/e_reg5 is 43.2 mm,
and the current p50/e_reg20 control is {anchor_control:.1f} mm, but the paired
bootstrap P(new wins) is {p_new_wins:.3f} [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv;
FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv].

### 16.2 Layer 2: Tag Position Solver

For static or batch operation with at least tens of frames per link, lower-quantile
tag-anchor aggregation should be tested against p50. The current Erlangen row is
{lt_v5:.1f} mm LOO versus {v5_p50:.1f} mm for the V5 p50 baseline [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv;
FULL_V5_followup_validation/tables/f6_final_comparison.csv].

WARNING: the lower_trim_20 result improves median but not tail. P95 goes from
{v5_p50_p95:.1f} mm for V5 p50 to {lt_v5_p95:.1f} mm for V5 lower_trim_20. For
applications where worst-case error matters more than typical error, lower_trim_20
may not be the right choice without additional tail-reduction measures such as
per-anchor stability checks or anchor-specific fallback strategies [source:
FULL_V5_followup_validation/tables/f6_final_comparison.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

For real-time dynamic operation with one range frame per solve, do not use a temporal
lower-quantile statistic unless a sliding window is explicitly accumulated. The
current ROTO floor remains about 101.5 mm under the conservative anchor-bridge
best-fit alignment [source: FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].

### 16.3 Layer 3: D_tag Calibration

Treat D_tag as per-device and per-estimator. The p50 V5 LOO value is 49.621 mm, but
the lower-quantile V5/Huber30 row has mean training D_tag 6.9 mm, and the anchor
lower-trim p50/e_i=0 row has mean D_tag 8.5 mm [source:
FULL_4way_comparison/tables/v5_loo_tag_delay_summary.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv;
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

Do not hardcode the p50 D_tag in firmware for static lower-quantile processing. The
range estimator changes the observation and therefore changes the calibrated D_tag
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

### 16.5 Implementation Checklist

For the next static batch run, every result row should state anchor layout source,
anchor e_i setting, tag range estimator, solver loss, and D_tag calibration method.
A complete row would read: `V5 p50 anchors, e_i=0, lower-quantile tag ranges,
Huber30, D_tag LOO`. Without those fields, 43.2 mm, 44.5 mm, 49.6 mm, and 67.8 mm
can be confused even though they describe different pipelines [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv;
FULL_V5/tables/static_summary_DLOO.csv].
"""

    section17 = f"""## 17. Open Questions and Recommended Next Steps

Priority 1 is tail reduction. The lower-quantile static row improves median error
but leaves P95 at {lt_v5_p95:.1f} mm. The next analysis should identify which
positions and anchors cause the tail, whether those cases share per-anchor instability,
and whether anchor-specific fallback rules can reduce P95 without giving back the
median gain [source: FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

Priority 2 is second-room validation with a frozen pipeline. The next dataset should
repeat p50 V4, p50 V5, lower-quantile V5, p50/e_i=0 anchors, and the same hard
validation splits on 6-12 known positions. This is required before claiming that
V5 transfers better or that the lower-quantile result generalizes beyond Erlangen
[source: FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s5_bootstrap_summary.csv].

Priority 3 is a quasi-static sliding-window mode. Static lower-quantile aggregation
needs a histogram, but many deployed use cases have pauses or slow movement. A future
implementation should accumulate a short window only when IMU and range stability
indicate low motion, then compare p50 and lower-quantile solves [source:
FULL_V5_rawframe_bruteforce_v2/reports/DATA_DISCOVERY.md;
FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].

Priority 4 is D_tag self-calibration from unknown positions. The current D_tag values
are tied to known static positions or fold-wise known-position residuals. Deployment
needs a method to calibrate a tag with a small number of arbitrary static placements
without Vicon truth [source:
FULL_V5_extended_mechanism_ablations/tables/item11_calibration_learning_curve_summary.csv;
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

The remaining hardware questions from V2 still stand: physical phase-center
measurement, tag-orientation sweeps, CIR/NLOS labels, and a real 9th anchor trial.
Those are secondary to the tail question because the report already has a strong
median mechanism result; the unresolved risk is the high-error tail [source:
FULL_V5_phase_center_sensitivity/tables/a6_robustness_summary.csv;
FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv].
"""

    # Keep appendices unchanged as requested, but refresh the title/comment around V3.
    report = "\n\n".join(
        [
            "# Comprehensive Experimental Report V3: Erlangen 28-May-2026 V5 Analysis Campaign",
            f"Generated: {datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}",
            "This editorial revision reorganizes the V2 report around the delay-layout coupling thesis. It does not add new computations or change the V2 tables and figures.",
            section0.strip(),
            sections_1_to_11.strip(),
            section12.strip(),
            section13.strip(),
            section14.strip(),
            section15.strip(),
            section16.strip(),
            section17.strip(),
            old_appendix.strip(),
        ]
    )

    # Remove V2's old generated word-count comment and add a V3 count.
    report = report.rsplit("<!-- Word count:", 1)[0].rstrip()
    report += f"\n\n<!-- Word count: {len(report.split())} -->\n"

    out_path = REPORT_DIR / "EXPERIMENTAL_REPORT_V3.md"
    out_path.write_text(report)
    print(f"Wrote {out_path}")
    print(f"Word count: {len(report.split())}")


if __name__ == "__main__":
    main()
