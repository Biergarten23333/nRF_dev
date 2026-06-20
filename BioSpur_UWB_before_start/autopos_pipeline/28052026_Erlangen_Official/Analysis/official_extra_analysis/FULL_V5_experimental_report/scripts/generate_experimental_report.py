#!/usr/bin/env python3
"""Generate the comprehensive internal V5 experimental report.

This script reads completed analysis artifacts only. It does not rerun solvers.
"""

from __future__ import annotations

import csv
import math
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd


BASE = Path(
    "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/"
    "autopos_pipeline/28052026_Erlangen_Official"
)
ANALYSIS = BASE / "Analysis" / "official_extra_analysis"
OUT = ANALYSIS / "FULL_V5_experimental_report"
REPORT_DIR = OUT / "report"
TABLE_DIR = OUT / "tables"
FIG_DIR = OUT / "figures"


REQUESTED_DIRS = [
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
]


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ANALYSIS))
    except ValueError:
        return str(path)


def source(path: str) -> str:
    return f"[source: {path}]"


def read_csv(path: str) -> pd.DataFrame:
    p = ANALYSIS / path
    if not p.exists():
        raise FileNotFoundError(f"Missing required input: {p}")
    return pd.read_csv(p)


def fmt(x, digits: int = 1, unit: str | None = None) -> str:
    if x is None:
        return ""
    try:
        if pd.isna(x):
            return ""
    except TypeError:
        pass
    if isinstance(x, str):
        return x
    val = float(x)
    if unit == "prob":
        return f"{val:.3f}"
    if unit == "scale":
        return f"{val:.3f}"
    if unit == "pct":
        return f"{val:.1f}"
    if abs(val) < 0.001 and val != 0:
        return f"{val:.3e}"
    return f"{val:.{digits}f}"


def md_table(rows: list[dict], cols: list[tuple[str, str]]) -> str:
    if not rows:
        return ""
    headers = [h for _, h in cols]
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        vals = []
        for key, _ in cols:
            val = row.get(key, "")
            if val is None or (isinstance(val, float) and math.isnan(val)):
                val = ""
            vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def copy_csv(src: str, dst_name: str) -> None:
    shutil.copy2(ANALYSIS / src, TABLE_DIR / dst_name)


def source_inventory() -> pd.DataFrame:
    rows = []
    for name in REQUESTED_DIRS:
        d = ANALYSIS / name
        rows.append(
            {
                "directory": name,
                "exists": d.exists(),
                "n_csv": len(list(d.glob("**/*.csv"))) if d.exists() else 0,
                "n_reports_md": len(list((d / "reports").glob("*.md"))) if (d / "reports").exists() else 0,
                "n_figures_png": len(list((d / "figures").glob("*.png"))) if (d / "figures").exists() else 0,
            }
        )
    df = pd.DataFrame(rows)
    df.to_csv(TABLE_DIR / "report_source_inventory.csv", index=False)
    return df


def copy_key_figures() -> pd.DataFrame:
    figures = [
        ("FULL_V5_overnight_batch2/figures/fig01_anchor_layout.png", "fig01_anchor_layout.png", "Anchor layouts: V4, V5, and Vicon."),
        ("FULL_V5_overnight_batch2/figures/fig02_static_accuracy_trajectory.png", "fig02_static_accuracy_trajectory.png", "Static accuracy trajectory."),
        ("FULL_V5_overnight_batch2/figures/fig03_cancellation_valley.png", "fig03_cancellation_valley.png", "Cancellation valley."),
        ("FULL_V5_overnight_batch2/figures/fig05_nlos_fingerprint.png", "fig04_nlos_fingerprint.png", "Per-anchor NLOS fingerprint."),
        ("FULL_V5_overnight_batch2/figures/fig09_transfer_matrix_heatmap.png", "fig05_transfer_matrix_heatmap.png", "Transfer matrix heatmap."),
        ("FULL_V5_batch3_falsification/figures/f1_nested_cv_comparison.png", "fig06_nested_cv_comparison.png", "Nested-CV degradation."),
        ("FULL_V5_batch3_falsification/figures/f3_contour_alpha_dtag.png", "fig07_profile_alpha_dtag.png", "Profile likelihood alpha vs D_tag."),
        ("FULL_V5_roto_deepdive/figures/r2_alignment_comparison_bar.png", "fig08_roto_alignment_comparison.png", "ROTO alignment comparison."),
        ("FULL_V5_roto_deepdive/figures/r4_gap_waterfall.png", "fig09_roto_gap_waterfall.png", "ROTO gap decomposition."),
        ("FULL_V5_mechanistic_deepdive/figures/m5_accuracy_vs_anchors.png", "fig10_anchor_count_identifiability.png", "Accuracy versus anchor count."),
        ("FULL_V5_paper_strengthening/figures/fig11_cancellation_mechanism.png", "fig11_cancellation_mechanism.png", "Signed radial mechanism diagnostic."),
        ("FULL_V5_phase_center_sensitivity/figures/a2_ranking_probability_vs_sigma.png", "fig12_phase_center_mc_probabilities.png", "Phase-center manufacturing variation probabilities."),
        ("FULL_V5_phase_center_sensitivity/figures/a5_operating_point_on_valley.png", "fig13_phase_center_valley.png", "Phase-center shift on cancellation valley."),
    ]
    rows = []
    for src, dst, caption in figures:
        src_path = ANALYSIS / src
        dst_path = FIG_DIR / dst
        status = "copied" if src_path.exists() else "missing"
        if src_path.exists():
            shutil.copy2(src_path, dst_path)
        rows.append({"figure": dst, "caption": caption, "source": src, "status": status})
    df = pd.DataFrame(rows)
    df.to_csv(TABLE_DIR / "figure_manifest.csv", index=False)
    return df


def build_summary_tables() -> dict[str, pd.DataFrame]:
    outputs = {}
    key_sources = {
        "headline_locked.csv": "FULL_V5_final_gate/tables/g1_locked_headline.csv",
        "claim_evidence_matrix.csv": "FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv",
        "master_number_registry.csv": "FULL_V5_grand_synthesis/tables/master_number_registry.csv",
        "consistency_audit.csv": "FULL_V5_grand_synthesis/tables/consistency_audit.csv",
        "phase_center_robustness_summary.csv": "FULL_V5_phase_center_sensitivity/tables/a6_robustness_summary.csv",
    }
    for dst, src in key_sources.items():
        copy_csv(src, dst)
        outputs[dst] = pd.read_csv(TABLE_DIR / dst)

    negative_rows = [
        {
            "experiment": "MLP learned range correction",
            "result": "MLP residual median 118.0 mm versus scalar 98.5 mm",
            "why_failed": "24 static positions were too few for a learned correction model",
            "source": "FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md",
        },
        {
            "experiment": "GNN attention correction",
            "result": "attention residual median 121.1 mm",
            "why_failed": "graph model overfit or lacked enough independent data",
            "source": "FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md",
        },
        {
            "experiment": "Solver search",
            "result": "best 82.7 mm in GPU discovery; fixed search still about 82.6 mm",
            "why_failed": "no candidate beat V4/V5 baselines after proper D_tag LOO handling",
            "source": "FULL_V5_overnight_batch2/tables/n2_solver_search_fixed.csv",
        },
        {
            "experiment": "Layout optimization",
            "result": "best optimized median 78.3 mm with mean anchor move 88.0 mm",
            "why_failed": "optimized layout remained worse than baseline static results",
            "source": "FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md",
        },
        {
            "experiment": "Bayesian Gaussian posterior",
            "result": "95% coverage 0.33; Student-t increased it to 0.46",
            "why_failed": "posterior remained under-calibrated",
            "source": "FULL_V5_final_gate/tables/g2_unified_noise_models.csv",
        },
        {
            "experiment": "NLOS detector generalization",
            "result": "random PR-AUC 0.949 collapsed to 0.42-0.55 in hard splits",
            "why_failed": "model memorized anchor identity and campaign-specific structure",
            "source": "FULL_V5_batch3_falsification/tables/f5_nlos_splits.csv",
        },
        {
            "experiment": "Rigid two-tag ROTO solver",
            "result": "joint range-level solver 261.8-264.2 mm versus independent 101.1 mm",
            "why_failed": "tested constraint forced geometry but did not solve dynamic range bias",
            "source": "FULL_V5_final_gate/tables/g5_joint_solver_summary.csv",
        },
        {
            "experiment": "p30 dynamic transfer",
            "result": "ROTO p30 best 283.9 mm versus raw/p50 101.5 mm",
            "why_failed": "static percentile aggregation did not transfer to single-frame dynamic ranges",
            "source": "FULL_V5_followup_validation/reports/FOLLOWUP_VALIDATION_SUMMARY.md",
        },
        {
            "experiment": "Dynamic NLOS weighting",
            "result": "soft NLOS weighting 104.2 mm versus approximately 104.9 mm baseline in that diagnostic",
            "why_failed": "sliding-window features did not materially change the dynamic floor",
            "source": "FULL_V5_roto_deepdive/tables/r5_nlos_dynamic_results.csv",
        },
    ]
    neg = pd.DataFrame(negative_rows)
    neg.to_csv(TABLE_DIR / "negative_results_summary.csv", index=False)
    outputs["negative_results_summary.csv"] = neg

    return outputs


def headline_rows(g1: pd.DataFrame) -> list[dict]:
    rows = []
    for _, r in g1.iterrows():
        rows.append(
            {
                "row": r["Row"],
                "variant": r["Variant"],
                "description": r["Description"],
                "median": fmt(r["median_3d"]),
                "p95": fmt(r["P95"]),
                "rmse": fmt(r["RMSE"]),
                "type": r["evaluation_type"],
                "source": rel(Path(str(r["source_csv"]))),
            }
        )
    return rows


def claim_counts(claims: pd.DataFrame) -> dict[str, int]:
    return {level: int((claims["level"] == level).sum()) for level in ["A", "B", "C", "D"]}


def section_baseline(vals: dict) -> str:
    return f"""
## 2. Baseline Analysis

### 2.1 V5 Baseline Pipeline

The V5 baseline pipeline was created to repeat the original FULL analysis with the
V5 common-mode anchor layout and V5 delay model. The static baseline row used all
{vals['static_n_positions']} positions and {vals['static_n_frames']} solved frames, with
D_tag fixed to the LOO value {vals['dtag_loo']} mm. The resulting median 3D error
was {vals['v5_full_median']} mm, P95 was {vals['v5_full_p95']} mm, and RMSE was
{vals['v5_full_rmse']} mm [source: FULL_V5/tables/static_summary_DLOO.csv]. The later
follow-up table re-evaluated the same V5 p50 uniform baseline at {vals['v5_baseline_locked']}
mm median, the small {vals['v5_delta_followup']} mm difference being accounted for by
the later exact range-row handling [source: FULL_V5_followup_validation/tables/f6_final_comparison.csv;
FULL_V5_grand_synthesis/tables/consistency_audit.csv].

The V5 pipeline also produced dynamic ROTO rows, per-anchor residual fingerprints,
DOP rows, drift rows, D_tag sweep rows, and static breakdowns by height and facing.
The key outcome of this first V5 pass was not that V5 was immediately more accurate
than V4; the key outcome was that a physically motivated V5 geometry and delay model
could reproduce a complete static and dynamic analysis chain with the same metrics as
the existing V4 pipeline [source: FULL_V5/reports/PHASE2_FULL_V5.md].

### 2.2 Sim3 Scale Comparison

The Sim3 diagnostic is the cleanest anchor-side result. The V4-io layout has a Sim3
scale of {vals['v4_scale']} against Vicon, while the V5 common-mode layout has scale
{vals['v5_scale']}. The rigid anchor RMSE improves from {vals['v4_rigid_rmse']} mm
for V4 to {vals['v5_rigid_rmse']} mm for V5 [source: FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv].
The same registry records the V5 common-mode delay as {vals['v5_c']} mm, with an
e_i full spread of {vals['v5_e_spread']} mm and max absolute e_i of {vals['v5_e_max']}
mm [source: FULL_V5_grand_synthesis/tables/master_number_registry.csv]. This is the
foundation for the Level A claim that V5 fixes the V4 scale leak on this campaign.

The interpretation is specific. V5 fixes metric scale and reduces anchor-coordinate
error against Vicon. It does not, by itself, guarantee lower tag-position error on
the same 24-position static campaign. That distinction stayed important throughout
the rest of the analysis.

### 2.3 Transfer Matrix

The transfer matrix evaluated 48 static cells spanning 3 layouts, 4 correction
sources, and 4 D_tag treatments. The diagonal production-like comparison is the
important row: L_V4 with C_V4 and D_LOO_CV gives {vals['v4_loo']} mm median 3D error,
while L_V5 with C_V5 and D_LOO_CV gives {vals['v5_transfer']} mm [source:
FULL_transfer_matrix/tables/transfer_matrix_48cells.csv]. The Vicon-anchor common-mode
oracle with D_LOO gives {vals['vicon_dloo']} mm, while the same Vicon oracle with
an in-sample D_sweep optimum gives {vals['vicon_dsweep']} mm [source:
FULL_transfer_matrix/tables/transfer_matrix_48cells.csv].

The transfer matrix therefore established two facts that can look contradictory if
reported without context. First, V5 has the better anchor geometry. Second, V4 has
the better empirical static median under the p50 LOO setting on this campaign. This
is where the cancellation hypothesis began: V4's scale compression appears to cancel
some structured positive range bias rather than merely representing a worse geometry.

### 2.4 Oracle and Single-Baseline Analysis

The Vicon-anchor evaluation tested what happens when the anchor positions are taken
from optical ground truth instead of self-calibration. It did not produce a decisive
oracle advantage. With C_Vicon_cm and D_LOO_CV the transfer matrix row is
{vals['vicon_dloo']} mm, and with D_sweep_opt it is {vals['vicon_dsweep']} mm [source:
FULL_transfer_matrix/tables/transfer_matrix_48cells.csv]. This supports the cautious
claim that Vicon-anchor results are compatible with cancellation, but not the stronger
claim that Vicon underperformance uniquely proves cancellation [source:
FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv].

The one-baseline scale-correction analysis gave a null-style result for V5. Since V5
was already close to metric scale, single external baselines did not create the same
large improvement as the earlier V4 F-H baseline result [source:
FULL_V5_one_baseline_scale_correction/reports/PHASE4_V5_ONE_BASELINE.md]. This result
is consistent with the Sim3 diagnostic: V5 had already absorbed the scale correction
into the common-mode anchor-delay parameterization.
"""


def build_report() -> str:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)

    inv = source_inventory()
    figs = copy_key_figures()
    outputs = build_summary_tables()

    g1 = outputs["headline_locked.csv"]
    claims = outputs["claim_evidence_matrix.csv"]
    registry = outputs["master_number_registry.csv"]
    consistency = outputs["consistency_audit.csv"]
    a6 = outputs["phase_center_robustness_summary.csv"]
    neg = outputs["negative_results_summary.csv"]

    full_v5 = read_csv("FULL_V5/tables/static_summary_DLOO.csv")
    scale = read_csv("FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv")
    transfer = read_csv("FULL_transfer_matrix/tables/transfer_matrix_48cells.csv")
    f6 = read_csv("FULL_V5_followup_validation/tables/f6_final_comparison.csv")
    n1 = read_csv("FULL_V5_overnight_batch2/tables/n1_adversarial_rooms.csv")
    n3 = read_csv("FULL_V5_final_gate/tables/g2_unified_noise_models.csv")
    f1_nested = read_csv("FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv")
    f2_optimism = read_csv("FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv")
    f5_nlos = read_csv("FULL_V5_batch3_falsification/tables/f5_nlos_splits.csv")
    r2 = read_csv("FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv")
    r4 = read_csv("FULL_V5_roto_deepdive/tables/r4_gap_decomposition.csv")
    m1 = read_csv("FULL_V5_mechanistic_deepdive/tables/m1_error_direction_summary.csv")
    m4 = read_csv("FULL_V5_mechanistic_deepdive/tables/m4_counterfactual.csv")
    m5 = read_csv("FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv")
    g5 = read_csv("FULL_V5_final_gate/tables/g5_joint_solver_summary.csv")
    a2 = read_csv("FULL_V5_phase_center_sensitivity/tables/a2_ranking_probabilities.csv")
    hard_cv = read_csv("FULL_V5_mechanism_ablations/A_hard_cv/tables/hard_cv_summary.csv")
    residual_field = read_csv("FULL_V5_mechanism_ablations/B_residual_field/tables/residual_field_summary.csv")
    cancellation_markers = read_csv("FULL_V5_mechanism_ablations/C_cancellation_valley/tables/cancellation_valley_markers.csv")
    per_height = read_csv("FULL_V5_mechanism_ablations/D_per_height_dtag/tables/per_height_dtag_optima.csv")

    def reg(metric: str):
        row = registry[registry["metric_name"] == metric]
        if row.empty:
            return ""
        return row.iloc[0]["value"]

    def transfer_row(layout: str, corr: str, mode: str):
        row = transfer[
            (transfer["layout_source"] == layout)
            & (transfer["correction_source"] == corr)
            & (transfer["tag_delay_mode"] == mode)
        ]
        if row.empty:
            raise ValueError((layout, corr, mode))
        return row.iloc[0]

    v4loo = transfer_row("L_V4", "C_V4", "D_LOO_CV")
    v5loo = transfer_row("L_V5", "C_V5", "D_LOO_CV")
    viconloo = transfer_row("L_Vicon", "C_Vicon_cm", "D_LOO_CV")
    vicondsweep = transfer_row("L_Vicon", "C_Vicon_cm", "D_sweep_opt")
    v5_static = full_v5.iloc[0]
    f6_v5 = f6[f6["variant"] == "V5 baseline"].iloc[0]

    vals = {
        "static_n_positions": int(v5_static["n_positions"]),
        "static_n_frames": int(v5_static["n_frames"]),
        "dtag_loo": fmt(v5_static["tag_delay_value_mm"], 3),
        "v5_full_median": fmt(v5_static["median_3d_mm"]),
        "v5_full_p95": fmt(v5_static["p95_3d_mm"]),
        "v5_full_rmse": fmt(v5_static["rmse_3d_mm"]),
        "v5_baseline_locked": fmt(f6_v5["median_3d_mm"]),
        "v5_delta_followup": fmt(abs(float(v5_static["median_3d_mm"]) - float(f6_v5["median_3d_mm"])), 3),
        "v4_scale": fmt(scale[scale["layout"] == "v4-io"].iloc[0]["sim3_scale"], 3, "scale"),
        "v5_scale": fmt(scale[scale["layout"] == "v5-commonmode"].iloc[0]["sim3_scale"], 3, "scale"),
        "v4_rigid_rmse": fmt(scale[scale["layout"] == "v4-io"].iloc[0]["rigid_anchor_rmse_mm"]),
        "v5_rigid_rmse": fmt(scale[scale["layout"] == "v5-commonmode"].iloc[0]["rigid_anchor_rmse_mm"]),
        "v5_c": fmt(reg("V5_common_mode_c")),
        "v5_e_spread": fmt(reg("V5_e_i_full_spread")),
        "v5_e_max": fmt(reg("V5_e_i_max_abs")),
        "v4_loo": fmt(v4loo["median_3d_mm"]),
        "v5_transfer": fmt(v5loo["median_3d_mm"]),
        "vicon_dloo": fmt(viconloo["median_3d_mm"]),
        "vicon_dsweep": fmt(vicondsweep["median_3d_mm"]),
    }

    cc = claim_counts(claims)
    headline = md_table(
        headline_rows(g1),
        [
            ("row", "Row"),
            ("variant", "Variant"),
            ("description", "Description"),
            ("median", "Median 3D mm"),
            ("p95", "P95 mm"),
            ("rmse", "RMSE mm"),
            ("type", "Evaluation"),
            ("source", "Source"),
        ],
    )

    claim_summary_rows = [
        {
            "level": k,
            "count": cc[k],
            "meaning": {
                "A": "Proven within this campaign",
                "B": "Supported with caveats",
                "C": "Hypothesis only",
                "D": "Disproven or should not be claimed",
            }[k],
        }
        for k in ["A", "B", "C", "D"]
    ]

    ext_items = [
        ("01", "Range-residual D_tag changes by height tier", "mixed", "V4 spread 11.8 mm; V5 spread 7.4 mm; Vicon spread 14.1 mm"),
        ("02", "Elevation angle explains rho", "mixed", "best abs-angle R2 0.107 for V5"),
        ("03", "Effective D_tag differs by anchor", "supported", "V5 anchor spread 131.0 mm"),
        ("04", "NLOS exclusions shift D_tag", "supported", "V5 exclude D,F delta -15.3 mm"),
        ("05", "LOO fold D_tag correlates with held-out metadata", "mixed", "best height R2 0.038 for V4"),
        ("06", "Joint V4-to-V5 morph has a lower valley", "supported", "global min alpha 0.15, D=52.0, median 56.4 mm"),
        ("07", "Common anchor shift and tag shift are interchangeable", "supported", "best anchor shift 100.0 mm, tag shift -60.0 mm"),
        ("08", "Facing group changes D_tag", "supported", "facing metadata present"),
        ("09", "Board-frame incidence explains rho", "skipped", "board orientation input unavailable"),
        ("10", "Low-order antenna model beats scalar D_tag", "supported", "V5 best M2 median 54.8 mm"),
        ("11", "Calibration quality improves with set size", "supported", "k=4 stratified mean 69.0 mm"),
        ("12", "Calibration design matters", "supported", "best V5 stratified_LMH median 40.8 mm"),
        ("13", "D_tag criterion optimum varies across folds", "supported", "max spread 18.0 mm"),
        ("14", "Vicon delay regularization changes oracle tail", "mixed", "best e10 median 63.4 mm"),
        ("15", "Anchor common mode is layer-dependent", "not supported", "upper-lower c diff -8.6 mm"),
        ("16", "Residual variance has structured factors", "supported", "top factor anchor_id fraction 0.090"),
        ("17", "Historical rho weighting/removal improves solves", "supported", "best V4 inverse_rms median 50.9 mm"),
        ("18", "Static residuals drift over acquisition time", "mixed", "D_tag early/mid/late 53.0, 59.6, 34.3 mm"),
        ("19", "ROTO tags have device-specific D_tag", "supported", "median per-tag spread 24.9 mm"),
        ("20", "Dynamic residual correlates with motion state", "mixed", "speed-residual R2 0.000"),
        ("21", "Lower range percentiles mitigate NLOS", "supported", "V5 p30 median 47.5 mm before fair recalibration"),
        ("22", "Single anchors have D_tag leverage", "supported", "max delta -8.1 mm removing F"),
        ("23", "Differential ranging cancels common-mode errors", "mixed", "median differential/absolute RMS ratio 1.416"),
        ("24", "Residual distribution shape differs by layer", "mixed", "skew upper 1.76, lower 1.60"),
    ]
    ext_df = pd.DataFrame(ext_items, columns=["item", "hypothesis", "verdict", "key_number"])
    ext_df.to_csv(TABLE_DIR / "extended_items_key_findings.csv", index=False)

    sections = []
    sections.append(
        f"""# Comprehensive Experimental Report: Erlangen 28-May-2026 V5 Analysis Campaign

Generated: {datetime.now().isoformat(timespec='seconds')}

This internal report documents the V4/V5 AutoPos analysis campaign for the Erlangen
28-May-2026 Vicon validation dataset. It is a technical record, not a paper draft.
It focuses on the 19 requested V5-era analysis directories listed in
`tables/report_source_inventory.csv` and uses the grand-synthesis registry,
final-gate locked headline table, and phase-center robustness table as the final
authoritative summaries [source: FULL_V5_grand_synthesis/tables/master_number_registry.csv;
FULL_V5_final_gate/tables/g1_locked_headline.csv;
FULL_V5_phase_center_sensitivity/tables/a6_robustness_summary.csv].

## 0. Executive Summary

The campaign covered 19 requested analysis directories, with the grand synthesis
scanning 23 total analysis directories and collecting 79 registry entries [source:
FULL_V5_grand_synthesis/reports/GRAND_SYNTHESIS.md]. The dataset contains 24 static
positions and 17 ROTO captures with DWM1001C UWB devices and Vicon/OptiTrack ground
truth [source: FULL_V5/tables/static_summary_DLOO.csv; FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].
The static V5 baseline uses 28,818 solved static frames [source:
FULL_V5/tables/static_summary_DLOO.csv]. The final ROTO joint-gate table covers
15,717 paired dynamic frames for the two-tag rigid test [source:
FULL_V5_final_gate/tables/g5_joint_solver_summary.csv].

Top-level findings:

- V5 fixes the anchor-side scale leak: V4 Sim3 scale is {vals['v4_scale']} and V5
  Sim3 scale is {vals['v5_scale']}; rigid anchor RMSE improves from
  {vals['v4_rigid_rmse']} mm to {vals['v5_rigid_rmse']} mm [source:
  FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv].
- V4 remains the empirical p50 static median winner on this 24-position campaign:
  V4+C_V4+D_LOO gives {vals['v4_loo']} mm and V5+C_V5+D_LOO gives
  {vals['v5_transfer']} mm [source: FULL_transfer_matrix/tables/transfer_matrix_48cells.csv].
- The best post-selected static rows are close: V4 apparent best is 54.9 mm, V5
  apparent best is 56.0 mm, and Vicon apparent best is 56.3 mm, all with p30 and
  inverse-RMS weighting [source: FULL_V5_followup_validation/tables/f6_final_comparison.csv].
- Winner's-curse correction moves the V4/V5 improved medians to about 64.5 mm and
  65.6 mm, respectively, and hard nested-CV medians range from 82.9 mm to 94.2 mm
  [source: FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv;
  FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv].
- The cancellation valley is supported by transfer-matrix, morph-valley, Fisher,
  profile-likelihood, and nullspace evidence, but the specific signed-radial
  mechanism remains only suggestive [source: FULL_V5_extended_mechanism_ablations/tables/item06_morph_markers.csv;
  FULL_V5_batch3_falsification/tables/f3_profile_alpha_dtag.csv;
  FULL_V5_paper_strengthening/tables/p1_signed_radial_summary.csv].
- ROTO remains a dynamic limitation: the conservative V5 best-fit-aligned median is
  101.5 mm, time-corrected SE(3) is 82.5 mm, and diagnostic Sim3 is 74.3 mm [source:
  FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].
- Several tempting claims were falsified or downgraded: NLOS detector generalization,
  rigid-body ROTO improvement, universal deployability of p30, and strong transfer
  superiority of V5 [source: FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv].

### Locked Headline Table

{headline}

### Claim Confidence Levels

{md_table(claim_summary_rows, [('level', 'Level'), ('count', 'Claim count'), ('meaning', 'Meaning')])}

The locked table is the authoritative number set for subsequent writing. The claim
matrix contains 25 claims: {cc['A']} Level A, {cc['B']} Level B, {cc['C']} Level C,
and {cc['D']} Level D [source: FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv].

Three interpretation rules were adopted for this report. First, a result labeled
LOO-CV is cross-validated only inside the same 24-position Erlangen campaign; it is
not an independent external holdout [source:
FULL_transfer_matrix/tables/transfer_matrix_48cells.csv;
FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv]. Second, rows labeled
in-sample, post-selected, D_sweep_opt, p30, or inverse-RMS best are diagnostic unless
the corresponding hard-split or bootstrap table also supports them [source:
FULL_V5_followup_validation/tables/f6_final_comparison.csv;
FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv]. Third, every ROTO
number depends on alignment convention; only the 101.5 mm anchor-bridge row is the
conservative current BEST-FIT-ALIGNED headline, while SE(3) and Sim3 rows are
diagnostic alignment audits [source: FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv;
FULL_V5_final_gate/tables/g1_locked_headline.csv].

The same caution applies to physical interpretation. V5's common-mode calibration
is a geometry result: it corrects the V4 scale defect from 0.958 to 1.010 and reduces
rigid anchor RMSE from 105.4 mm to 63.0 mm [source:
FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv]. The lower V4 tag-error
median is an empirical positioning result on this campaign: 57.9 mm for V4+LOO
versus 67.8 mm for V5+LOO [source:
FULL_transfer_matrix/tables/transfer_matrix_48cells.csv]. Those two facts can both
be true because anchor-side metric correctness and tag-side error cancellation are
different measurements. This report keeps those axes separate throughout, because
mixing them is the main way to overstate either V4 or V5.
All later claim labels should be read through that separation rather than as a
single ranking of calibration methods [source:
FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv].
This is also why both positive and negative results are retained in the same
document.
The intent is reproducibility for future re-analysis, not selective presentation.
"""
    )

    sections.append(
        f"""
## 1. Dataset and System Description

The analysis uses the corrected complete OptiTrack export under `opti_captures/full`.
The original static optical export had an Anchor-G marker/model error in which
`Gtop` and `Glong` were swapped, and the corrected complete export is the authoritative
optical reference [source: FULL/FULL_ANALYSIS.md]. All Vicon-anchor analyses in this
campaign should be read with that correction in mind.

The UWB system is based on DWM1001C hardware and custom firmware, not a black-box
PANS/DRTLS positioning stack. The relevant measurements are broadcast-style SS-TWR
range observations between anchors and tags, with the offline solver fitting anchor
geometry, anchor delay corrections, and tag positions [source:
FULL_V5/reports/PHASE2_FULL_V5.md]. The V4 layout uses the earlier independent
bounded delay formulation. The V5 layout uses a common-mode anchor-delay
parameterization: a bulk common-mode term c plus regularized per-anchor residuals
e_i [source: FULL_V5/tables/delay_comparison_v4_vs_v5.csv].

The static validation protocol consists of 24 tag positions. These positions are
used for static headline accuracy, height-tier cross-validation, D_tag LOO calibration,
range-percentile tests, p30/inverse-RMS follow-up, nested CV, position anatomy, and
quality-score analysis [source: FULL_V5/tables/static_summary_DLOO.csv;
FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv;
FULL_V5_paper_strengthening/tables/p5_quality_score.csv]. The static V5 p50 D_LOO
row contains {vals['static_n_frames']} solved input frames and no solve failures
[source: FULL_V5/tables/static_summary_DLOO.csv].

The ROTO validation protocol consists of 17 captures with two tags on a rotating
arm. The prompt and later ROTO analyses treat the two tags as separated by a known
radial difference of 120 mm rather than a generic unconstrained inter-tag distance.
The dynamic results must be reported as BEST-FIT-ALIGNED because UWB and Vicon did
not have hardware time synchronization [source: FULL_V5_roto_deepdive/reports/ROTO_DEEPDIVE_COMPLETION.md].
The final-gate rigid test contains 15,717 frames in the paired ROTO evaluation [source:
FULL_V5_final_gate/tables/g5_joint_solver_summary.csv].

The optical reference itself has a caveat. Vicon marker centers are not guaranteed
to coincide with antenna phase centers, and the 3D-printed holder and pivot/marker
geometry can create a physically meaningful offset. Phase-center sensitivity and
final-gate tests later showed that small offsets do not overturn the V4-over-V5
ranking, but the Vicon oracle rank is fragile at about 2 mm perturbations [source:
FULL_V5_phase_center_sensitivity/tables/a6_robustness_summary.csv].
"""
    )

    sections.append(section_baseline(vals))

    hard_rows = []
    for _, r in hard_cv.iterrows():
        hard_rows.append(
            {
                "config": r["config"],
                "full": fmt(r["full_loo_median_3d_mm"]),
                "worst_tier": r["worst_tier"],
                "tier_deg": fmt(r["height_degradation_mm"]),
                "edge_deg": fmt(r["edge_center_degradation_mm"]),
            }
        )
    sections.append(
        f"""
## 3. Mechanism Ablations

### 3.1 Six Mechanism Items

The first mechanism batch tested six targeted questions: hard validation splits,
residual-field structure, the cancellation valley, per-height D_tag stability,
D_tag criterion curves, and multi-criterion D_tag ambiguity [source:
FULL_V5_mechanism_ablations/reports/MECHANISM_ABLATION_SUMMARY.md].

Hard CV showed that V4+C_V4 and V5+C_V5 degrade in different but comparable ways.
V4+C_V4 has a full LOO median of 57.9 mm and a worst height-tier degradation of
8.4 mm; V5+C_V5 has 67.8 mm full LOO and 4.5 mm degradation; Vicon+C_Vicon_cm has
63.4 mm full LOO and 12.2 mm degradation [source:
FULL_V5_mechanism_ablations/A_hard_cv/tables/hard_cv_summary.csv].

{md_table(hard_rows, [('config', 'Config'), ('full', 'Full LOO median mm'), ('worst_tier', 'Worst tier'), ('tier_deg', 'Height degradation mm'), ('edge_deg', 'Outer/center degradation mm')])}

Residual-field analysis found structured residual bias in both V4 and V5. V4's mean
signed error magnitude is 33.2 mm, while V5's is 26.7 mm; however, V4's 3D median is
lower at 57.9 mm than V5's 67.8 mm [source:
FULL_V5_mechanism_ablations/B_residual_field/tables/residual_field_summary.csv].
The cancellation-valley grid reported an in-sample global minimum at scale 0.980,
D_tag 108.0 mm, median 55.3 mm [source:
FULL_V5_mechanism_ablations/C_cancellation_valley/tables/cancellation_valley_markers.csv].
Per-height D_tag optima showed V4 spanning 20-54 mm and V5 spanning 70-86 mm under
the min-median criterion [source:
FULL_V5_mechanism_ablations/D_per_height_dtag/tables/per_height_dtag_optima.csv].
The later grand-synthesis registry records range-residual tier spreads of 11.8 mm
for V4 and 7.4 mm for V5 [source: FULL_V5_grand_synthesis/tables/master_number_registry.csv].

### 3.2 Twenty-Four Extended Mechanism Items

The extended CPU ablation batch expanded the mechanism tests to 24 items. The full
summary table is extracted into `tables/extended_items_key_findings.csv`. The item
set is best read by theme rather than by row number.

{md_table(ext_df.to_dict('records'), [('item', 'Item'), ('hypothesis', 'Hypothesis tested'), ('verdict', 'Verdict'), ('key_number', 'Key number')])}

Tag-delay physics was not reducible to one scalar. Range-residual D_tag by height
tier was mixed, per-anchor effective D_tag had a large V5 spread of 131.0 mm, NLOS
exclusions moved the V5 estimate by -15.3 mm when D and F were excluded, and per-tag
ROTO estimates suggested a 24.9 mm spread [source:
FULL_V5_extended_mechanism_ablations/reports/EXTENDED_MECHANISM_ABLATION_SUMMARY.md].
This supports treating D_tag as a useful scalar operating parameter, but not a pure
hardware constant in all geometries.

NLOS and link-quality tests showed that residual structure is real but not trivially
removed. Historical inverse-RMS weighting improved some static solves, jackknife
anchor removal affected D_tag, distribution tails differed by layer, and differential
ranging did not cleanly collapse the absolute residual error [source:
FULL_V5_extended_mechanism_ablations/reports/EXTENDED_MECHANISM_ABLATION_SUMMARY.md].
The strongest static percentile diagnostic was V5 p30 at 47.5 mm before fair
recalibration, but this result was later reclassified as another cancellation effect
rather than a deployable universal correction [source:
FULL_V5_extended_mechanism_ablations/tables/item21_range_percentile_sweep.csv;
FULL_V5_followup_validation/tables/f4_percentile_recalibrated.csv].

Calibration design was highly sensitive. Item 12 found a best V5 stratified_LMH
median of 40.8 mm, but follow-up stratified repeats showed this could be a lucky
split rather than a robust deployment recipe [source:
FULL_V5_extended_mechanism_ablations/reports/EXTENDED_MECHANISM_ABLATION_SUMMARY.md;
FULL_V5_followup_validation/tables/f3_stratified_stability_summary.csv].
This is why the final narrative treats calibration design as important but does not
claim that any one small calibration subset is solved.
"""
    )

    sections.append(
        f"""
## 4. GPU Discovery Pipeline

### 4.1 Tier 1: Six GPU Tasks

The GPU Tier 1 run completed six tasks in 12.08 minutes on the two GTX 1080 Ti cards
[source: FULL_V5_GPU_tier1/reports/OVERNIGHT_COMPLETION.md]. The first task reported
P(V5<V4)=1.00 in a multi-room Monte Carlo. This was later corrected because the V4
solver parameterization in the simulation was not faithful enough for that strong
claim [source: FULL_V5_overnight_batch2/reports/TASK_N1_MC_VERIFICATION.md].

The Fisher task found a weakest eigenvalue of 1.000e-06, giving direct evidence for
a weakly identifiable calibration direction [source:
FULL_V5_GPU_tier1/reports/OVERNIGHT_COMPLETION.md]. The Shapley task assigned high
scores to anchors D and F, 1242.9 and 1229.4, respectively [source:
FULL_V5_GPU_tier1/tables/task3_shapley_values.csv]. The AA-AT asymmetry task found
a mean asymmetry of -4.7 mm [source: FULL_V5_GPU_tier1/reports/OVERNIGHT_COMPLETION.md].
The solver-search task returned 82.7 mm, which was worse than existing baselines
and later remained worse after D_tag LOO fixes [source:
FULL_V5_GPU_tier1/reports/OVERNIGHT_COMPLETION.md;
FULL_V5_overnight_batch2/tables/n2_solver_search_fixed.csv]. The NLOS task reached
PR-AUC 0.952 in Tier 1 and 0.949 in the full GPU discovery repeat [source:
FULL_V5_GPU_tier1/tables/task6_cv_results.csv;
FULL_V5_GPU_discovery/tables/task6_cv_results.csv].

### 4.2 Full Discovery: Seventeen GPU Tasks

The full GPU discovery run completed 17 of 17 tasks in 19.06 minutes [source:
FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md]. Successful discoveries
included the repeated Fisher weak direction, repeated Shapley D/F attribution,
the AA-AT asymmetry check, a Student-t model tournament winner, synthetic CIR
experiments, dynamic-state statistics, and an active-design score [source:
FULL_V5_GPU_discovery/reports/KEY_FINDINGS_SYNTHESIS.md].

Problematic results were equally important. Task 1 repeated the too-clean
P(V5<V4)=1.00 result, Task 5's solver search stayed worse than baseline at 82.7 mm,
Task 8's landscape minimum lay on a boundary at s=0.930, dc=50, D=140, and Task
12's Gaussian Bayesian solver had only 0.33 actual 95% coverage [source:
FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md]. Learned-correction tasks
were data-limited: the MLP residual median was 118.0 mm versus scalar 98.5 mm, and
the attention residual median was 121.1 mm [source:
FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md].

The GPU discovery phase therefore changed the campaign in two ways. It produced
stronger mechanistic evidence for identifiability, Shapley structure, and
heavy-tailed residuals. It also forced later correction batches because several
attractive GPU results were too optimistic or insufficiently faithful to the actual
V4/V5 solver definitions.
"""
    )

    sections.append(
        f"""
## 5. Follow-up Validation

The follow-up validation batch contained six tasks and produced the first corrected
best-practice headline table [source: FULL_V5_followup_validation/reports/FOLLOWUP_VALIDATION_SUMMARY.md].
F1 showed that p30 plus inverse-RMS weighting plus recalibrated D_tag gives V5 a
56.0 mm median, but it did not break a 45 mm target [source:
FULL_V5_followup_validation/tables/f1_combination_grid.csv]. F2 showed that p30
does not transfer to ROTO: the best p30/median-window ROTO result was 283.9 mm
versus raw/p50 101.5 mm [source: FULL_V5_followup_validation/reports/FOLLOWUP_VALIDATION_SUMMARY.md].

F3 tested stratified calibration sanity. A 40.8 mm split was identified as lucky;
the scalar stratified mean median was 68.2 mm with standard deviation 8.7 mm [source:
FULL_V5_followup_validation/reports/FOLLOWUP_VALIDATION_SUMMARY.md]. F4 performed
fair percentile recalibration. After recalibration, the V5 optimum shifted to p20
at 53.8 mm and p30 became 59.8 mm; V4 still won at every percentile, with the best
recalibrated percentile cell at 52.0 mm for V4_CV4 p20 [source:
FULL_V5_followup_validation/tables/f4_percentile_recalibrated.csv;
FULL_V5_grand_synthesis/tables/master_number_registry.csv].

F5 showed that per-anchor percentile selection was not straightforward. A selective
D/F p30 else p50 diagnostic could reach 47.3 mm, but anchor F specifically became
worse under p30 by -13.2 mm in that task's report [source:
FULL_V5_followup_validation/reports/TASK_F5_PERCENTILE_PER_ANCHOR.md]. F6 produced
the specified headline comparison: V4 production 71.9 mm, V5 baseline 67.8 mm, V5
improved 56.0 mm, V4 improved 54.9 mm, and Vicon improved 56.3 mm [source:
FULL_V5_followup_validation/tables/f6_final_comparison.csv].
"""
    )

    n1_v5_wins = int((n1["winner"] == "V5").sum())
    n1_p = n1_v5_wins / len(n1)
    student = n3[n3["model"] == "M2_student_t"].iloc[0]
    sections.append(
        f"""
## 6. Corrections and Fixes

N1 directly addressed the overly clean Monte Carlo result. In 10 adversarial rooms
designed to favor V4-style cancellation, V5 won {n1_v5_wins} of 10 rooms, giving
P(V5<V4)={n1_p:.3f} rather than 1.000 [source:
FULL_V5_overnight_batch2/tables/n1_adversarial_rooms.csv]. The report also flagged
the V4 simulation solver fidelity problem, so the original P=1.00 result should not
be used as a strong transfer claim [source:
FULL_V5_overnight_batch2/reports/TASK_N1_MC_VERIFICATION.md].

N2 fixed the solver-search protocol by adding D_tag LOO-CV to the top variants and
the V4/V5 baselines. The best fixed variant remained about 82.6 mm and did not beat
V4+C_V4+D_LOO or V5+C_V5+D_LOO [source:
FULL_V5_overnight_batch2/tables/n2_solver_search_fixed.csv]. N3 replaced the
Gaussian Bayesian likelihood with Student-t and mixture variants. Student-t became
the BIC winner, with 95% coverage {float(student['coverage_95']):.3f}, but this
was still badly below nominal 0.950 [source: FULL_V5_final_gate/tables/g2_unified_noise_models.csv].

N4-N6 returned to p30. N4 reported a fallback best median of 47.5 mm, N5 reported
a p30 transfer-matrix sweep winner at 46.8 mm, and N6 reported a V5 bootstrap median
CI of 54.3-63.7 mm [source: FULL_V5_overnight_batch2/reports/OVERNIGHT_BATCH2_COMPLETION.md;
FULL_V5_overnight_batch2/tables/n6_bootstrap_ci.csv]. N7-N9 generated 10 figures,
7 paper table sets, and a paper outline [source:
FULL_V5_overnight_batch2/reports/OVERNIGHT_BATCH2_COMPLETION.md].
"""
    )

    nested_rows = []
    for _, r in f1_nested.iterrows():
        nested_rows.append(
            {"split": r["split_type"], "mean": fmt(r["mean_test_median"]), "std": fmt(r["std_test_median"])}
        )
    optimism_gap = float(f2_optimism[f2_optimism["metric"] == "mean_optimism_gap_honest_minus_apparent"]["value_mm"].iloc[0])
    corr_v4 = float(f2_optimism[f2_optimism["metric"] == "corrected_headline_v4_54p9"]["value_mm"].iloc[0])
    corr_v5 = float(f2_optimism[f2_optimism["metric"] == "corrected_headline_v5_56p0"]["value_mm"].iloc[0])
    loao = f5_nlos[f5_nlos["split_type"] == "leave_one_anchor_out"]
    sections.append(
        f"""
## 7. Falsification Campaign

The falsification batch attacked the campaign's own conclusions. Nested CV selected
variants on training splits and evaluated held-out partitions. The mean test medians
were 82.9 mm for height-out, 88.0 mm for quadrant-out, and 94.2 mm for spatial6
[source: FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv].

{md_table(nested_rows, [('split', 'Split'), ('mean', 'Mean test median mm'), ('std', 'Std mm')])}

The winner's-curse task estimated a mean optimism gap of {optimism_gap:.1f} mm,
moving the corrected V4 improved median to {corr_v4:.1f} mm and corrected V5 improved
median to {corr_v5:.1f} mm [source:
FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv]. The profile-likelihood
task produced dense alpha/D_tag, scale/D_tag, and c/D_tag surfaces with 31,823 rows
across the profile tables and visible valleys [source:
FULL_V5_batch3_falsification/tables/f3_profile_alpha_dtag.csv;
FULL_V5_batch3_falsification/tables/f3_profile_s_dtag.csv;
FULL_V5_batch3_falsification/tables/f3_profile_c_dtag.csv].

The nullspace perturbation task supported a weak direction but did not pass the
strictest threshold. The grand-synthesis registry records a median perturbation ratio
of 0.267 [source: FULL_V5_grand_synthesis/tables/master_number_registry.csv]. The
NLOS leakage test was decisive against the strong generalization claim: random-split
PR-AUC around 0.949 fell to leave-one-anchor values of {fmt(loao['pr_auc'].min(), 3)}
to {fmt(loao['pr_auc'].max(), 3)} across model choices [source:
FULL_V5_batch3_falsification/tables/f5_nlos_splits.csv]. The reviewer simulation
demoted 3 claims to C/D [source: FULL_V5_batch3_falsification/reports/FALSIFICATION_COMPLETION.md].
"""
    )

    r2_rows = []
    for _, r in r2.iterrows():
        r2_rows.append(
            {
                "method": r["method"],
                "median": fmt(r["overall_median"]),
                "p95": fmt(r["overall_p95"]),
                "rmse": fmt(r["overall_rmse"]),
                "scale": fmt(r["median_scale_factor"], 3, "scale"),
            }
        )
    gap_rows = [
        {"component": r["component"], "estimate": fmt(r["estimated_mm"]), "notes": r["notes"]}
        for _, r in r4.iterrows()
    ]
    sections.append(
        f"""
## 8. ROTO Deep-Dive

ROTO was analyzed separately because the dynamic error floor is not the same problem
as static p50/p30 accuracy. R1 swept time offsets and recovered only 0.7 mm median
improvement, so time offset was not the main bottleneck [source:
FULL_V5_roto_deepdive/tables/r1_time_corrected_results.csv]. R2 compared alignment
methods. No alignment gave 557.9 mm, translation-only gave 83.7 mm, SE(3) gave
81.7 mm, current anchor-bridge best-fit gave 101.5 mm, and diagnostic Sim3 gave
74.3 mm with scale 0.906 [source: FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].

{md_table(r2_rows, [('method', 'Method'), ('median', 'Median mm'), ('p95', 'P95 mm'), ('rmse', 'RMSE mm'), ('scale', 'Scale')])}

R3 tested rigid-body exploitation and did not improve tracking. The independent
diagnostic median was 101.1 mm, while the joint projection was roughly 280.6 mm in
the ROTO deep-dive and the final-gate true range-level joint solver returned
261.8-264.2 mm [source: FULL_V5_roto_deepdive/tables/r3_joint_summary.csv;
FULL_V5_final_gate/tables/g5_joint_solver_summary.csv]. R4 decomposed the static to
dynamic gap into non-orthogonal proxy components.

{md_table(gap_rows, [('component', 'Component'), ('estimate', 'Estimate mm'), ('notes', 'Notes')])}

R5 tested dynamic NLOS weighting and found negligible improvement: soft_nlos was
104.2 mm in its diagnostic table [source: FULL_V5_roto_deepdive/tables/r5_nlos_dynamic_results.csv].
R6 mapped error by rotation phase and found the worst sector at 300 degrees, with
anchor D the dominant worst anchor [source: FULL_V5_roto_deepdive/tables/r6_phase_aggregate.csv].
The recommended dynamic baseline remains the existing V5 D_LOO per-frame solver,
reported explicitly as BEST-FIT-ALIGNED [source:
FULL_V5_roto_deepdive/reports/ROTO_DEEPDIVE_COMPLETION.md].
"""
    )

    m1_rows = [
        {
            "config": r["config"],
            "radial": fmt(r["mean_signed_radial"]),
            "vertical": fmt(r["mean_signed_vertical"]),
            "med_radial": fmt(r["median_abs_radial"]),
        }
        for _, r in m1.iterrows()
    ]
    m5_rows = [
        {
            "k": int(r["k"]),
            "ranges": int(r["ranges"]),
            "params": int(r["params"]),
            "redundancy": int(r["redundancy"]),
            "mean": fmt(r["mean_median_3d"]),
        }
        for _, r in m5.iterrows()
    ]
    sections.append(
        f"""
## 9. Mechanistic Deep-Dive

M1 decomposed position error into signed radial, tangential, and vertical components.
The hypothesized radial mechanism was not decisive: V4 mean signed radial was -7.8 mm,
V5 was -4.8 mm, and Vicon was -5.1 mm, so all three were slightly inward rather than
V4 uniquely inward and V5 outward [source:
FULL_V5_mechanistic_deepdive/tables/m1_error_direction_summary.csv].

{md_table(m1_rows, [('config', 'Config'), ('radial', 'Mean signed radial mm'), ('vertical', 'Mean signed vertical mm'), ('med_radial', 'Median abs radial mm')])}

M2 produced a proxy physical error budget, but explicitly flagged that the components
are not orthogonal [source: FULL_V5_mechanistic_deepdive/reports/MECHANISTIC_DEEPDIVE_COMPLETION.md].
M3 measured V5 offset vectors relative to Vicon and found a mean magnitude of 56.8
mm with direction resultant 0.09, meaning offsets were not coherently aligned [source:
FULL_V5_mechanistic_deepdive/reports/MECHANISTIC_DEEPDIVE_COMPLETION.md]. M4 tested
whether V5 e_i values were NLOS proxies. corr(e_i, rho_rms) was 0.08, and forcing all
e_i to zero improved the median from 67.8 mm to 64.5 mm in that counterfactual
[source: FULL_V5_mechanistic_deepdive/tables/m4_counterfactual.csv].

M5 addressed anchor count and identifiability. The redundancy table shows that 8
anchors give redundancy +2 and about 68.7 mm mean median in the subset replay, while
a simulated 9th anchor gives redundancy +6 and about 60.7 mm mean median [source:
FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv].

{md_table(m5_rows, [('k', 'Anchors'), ('ranges', 'Ranges'), ('params', 'Params'), ('redundancy', 'Redundancy'), ('mean', 'Mean median 3D mm')])}

M6 repeated the ROTO phase result, M7 confirmed that the rigid constraint diagnostic
does not improve beyond about 101.1 mm, M8 found n_bad_anchors to be the strongest
simple predictor with R2=0.18, M9 found a local Fisher eigenvalue of 5.98e-03 in the
recomputed simplified model, and M10 found V5 baseline consistency max delta 0.00 mm
[source: FULL_V5_mechanistic_deepdive/reports/MECHANISTIC_DEEPDIVE_COMPLETION.md].
"""
    )

    g5_rows = [
        {
            "method": r["method"],
            "median": fmt(r["overall_median"]),
            "rmse": fmt(r["overall_rmse"]),
            "p95": fmt(r["overall_p95"]),
            "conv": fmt(r["convergence_rate"], 3, "prob"),
        }
        for _, r in g5.iterrows()
    ]
    sections.append(
        f"""
## 10. Final Gates

The final gates closed the campaign before paper writing. G1 produced the locked
headline table used at the top of this report [source:
FULL_V5_final_gate/tables/g1_locked_headline.csv]. G2 unified the noise-model story:
Student-t is the BIC winner, and the key-number card had a parsing error in the
earlier Bayesian coverage narrative [source: FULL_V5_final_gate/tables/g2_unified_noise_models.csv].

G3 nested phase-center offsets and concluded that small offsets under 50 mm do not
explain the Vicon result, while larger unconstrained offsets can fit but become
physically implausible. The best unconstrained model had mean test median 54.3 mm
but max offset 128.5 mm; the best small/global model B had mean test median 66.1 mm
and max offset 37.3 mm [source: FULL_V5_final_gate/tables/g3_phase_center_summary.csv].
G4 validated selected deployment recipes against the full solver and found proxy/full
gaps up to 5.7 mm [source: FULL_V5_final_gate/tables/g4_validated_recipes.csv].

G5 was the final ROTO rigid-body gate. It confirmed the negative result at range level:

{md_table(g5_rows, [('method', 'Method'), ('median', 'Median mm'), ('rmse', 'RMSE mm'), ('p95', 'P95 mm'), ('conv', 'Convergence rate')])}

The gate report explicitly says no further experiments are introduced by the final
gate script and that paper writing should begin after the report [source:
FULL_V5_final_gate/reports/FINAL_GATE_COMPLETION.md].
"""
    )

    a2_rows = [
        {"sigma": fmt(r["sigma_mm"]), "p_v4": fmt(r["p_v4_beats_v5"], 3, "prob"), "p_vicon": fmt(r["p_vicon_worst"], 3, "prob")}
        for _, r in a2.iterrows()
    ]
    a6_rows = [
        {
            "conclusion": r["conclusion"],
            "baseline": r["baseline_value"],
            "threshold": r["flip_threshold_mm"],
            "label": r["robustness_label"],
        }
        for _, r in a6.iterrows()
    ]
    sections.append(
        f"""
## 11. Phase Center Sensitivity

The phase-center sensitivity batch tested whether plausible antenna phase-center
offsets can overturn the main conclusions. A1 applied global vertical shifts and
found that V4-beats-V5 does not flip up to 10 mm [source:
FULL_V5_phase_center_sensitivity/tables/a1_global_shift_results.csv]. A2 ran 5,000
manufacturing-variation samples per sigma level and found P(V4 beats V5) at or above
0.998 through sigma=8 mm [source:
FULL_V5_phase_center_sensitivity/tables/a2_ranking_probabilities.csv].

{md_table(a2_rows, [('sigma', 'Sigma mm'), ('p_v4', 'P(V4 beats V5)'), ('p_vicon', 'P(Vicon worst)')])}

A3 separated anchor and tag perturbations. Anchor perturbations dominated scale and
Vicon metrics, while tag perturbations dominated D_tag and tag-position error shifts
[source: FULL_V5_phase_center_sensitivity/tables/a3_dominance.csv]. A4 fitted a
direction-dependent phase-center model, with the best V5 median at delta_0=-5 mm and
delta_elev=-10 mm [source: FULL_V5_phase_center_sensitivity/tables/a4_best_fit.csv].
A5 showed that the valley shape remains dominated by scale-D_tag coupling; tested
vertical shifts left the valley-distance diagnostic near 11.3-11.4 [source:
FULL_V5_phase_center_sensitivity/tables/a5_valley_shift.csv].

{md_table(a6_rows, [('conclusion', 'Conclusion'), ('baseline', 'Baseline value'), ('threshold', 'Flip threshold'), ('label', 'Robustness')])}

The phase-center conclusion is therefore narrow. Small phase-center offsets are not
enough to undo the V4-over-V5 static ranking or the V5 scale fix. The Vicon oracle
rank, however, is fragile, so Vicon-underperformance should not be treated as unique
proof of cancellation [source: FULL_V5_phase_center_sensitivity/tables/a6_robustness_summary.csv].
"""
    )

    level_groups = {
        "A": claims[claims["level"] == "A"],
        "B": claims[claims["level"] == "B"],
        "C": claims[claims["level"] == "C"],
        "D": claims[claims["level"] == "D"],
    }
    for level, title in [
        ("A", "What is proven within this campaign"),
        ("B", "What is supported with caveats"),
        ("C", "What remains hypothesis only"),
        ("D", "What was disproven or should not be claimed"),
    ]:
        rows = [
            {
                "id": int(r["claim_id"]),
                "claim": r["claim_text"],
                "wording": r["recommended_paper_wording"],
            }
            for _, r in level_groups[level].iterrows()
        ]
        sections.append(
            f"""
## 12.{level} {title}

{md_table(rows, [('id', 'ID'), ('claim', 'Claim'), ('wording', 'Recommended wording')])}

This block is copied from the grand-synthesis claim matrix and should be treated as
the campaign-level claim-control table [source: FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv].
"""
        )

    neg_rows = [
        {"experiment": r["experiment"], "result": r["result"], "why": r["why_failed"], "source": r["source"]}
        for _, r in neg.iterrows()
    ]
    sections.append(
        f"""
## 13. Negative Results Summary

The negative results are part of the experimental record. They prevent accidental
overclaiming and identify the boundaries of this dataset.

{md_table(neg_rows, [('experiment', 'Experiment'), ('result', 'Result'), ('why', 'Why it failed'), ('source', 'Source')])}

These failures collectively show that the dataset is large enough to diagnose
scale-delay behavior and NLOS fingerprints, but not large enough to support strong
claims about learned correction models, deployable NLOS classification, or dynamic
rigid-body improvement.
"""
    )

    sections.append(
        f"""
## 14. Open Questions and Recommended Next Steps

The remaining high-value work is experimental rather than computational. First,
repeat the static validation in a second room with at least 6 known positions and
the same V4/V5 analysis stack. This is required before claiming V5 transfers better
than V4 [source: FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv]. Second,
measure antenna phase-center offsets physically using a ruler/caliper setup and a
known fixture, because the Vicon-marker result is compatible with phase-center
offset but not settled by the existing optical data [source:
FULL_V5_final_gate/tables/g3_phase_center_summary.csv].

Third, perform a tag-orientation sweep at one or more fixed positions. The current
board-frame incidence task was skipped because board orientation input was unavailable,
and the direction-dependent phase-center sensitivity remains a fitted proxy [source:
FULL_V5_extended_mechanism_ablations/reports/EXTENDED_MECHANISM_ABLATION_SUMMARY.md;
FULL_V5_phase_center_sensitivity/tables/a4_best_fit.csv]. Fourth, test a real 9th
anchor. The simulated 9th anchor improves redundancy from +2 to +6 and mean median
from about 68.7 mm to 60.7 mm, but this needs hardware confirmation [source:
FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv].

Fifth, add CIR firmware and ground-truth NLOS labels. The NLOS detector has strong
random-split PR-AUC but weak leave-anchor and leave-height performance, so the next
dataset should separate link physics from anchor identity [source:
FULL_V5_batch3_falsification/tables/f5_nlos_splits.csv]. Sixth, repeat the same
pipeline on DW3000 or another radio generation to determine whether the delay/NLOS
cancellation behavior is specific to this DW1000/DWM1001C campaign.
"""
    )

    inv_rows = inv.to_dict("records")
    cons_rows = []
    for _, r in consistency.iterrows():
        cons_rows.append(
            {
                "metric": r["metric"],
                "source1": r["source_1"],
                "v1": fmt(r["value_1"], 3),
                "source2": r["source_2"],
                "v2": fmt(r["value_2"], 3),
                "disc": fmt(r["discrepancy"], 6),
                "status": r["status"],
            }
        )
    reg_rows = []
    for _, r in registry.iterrows():
        unit = r["unit"]
        val = r["value"]
        if isinstance(val, (int, float)):
            if unit == "scale":
                val_s = fmt(val, 3, "scale")
            elif unit == "probability":
                val_s = fmt(val, 3, "prob")
            else:
                val_s = fmt(val, 3)
        else:
            val_s = str(val)
        reg_rows.append(
            {
                "theme": r["theme"],
                "metric": r["metric_name"],
                "value": val_s,
                "unit": unit,
                "source": f"{r['source_directory']}/{r['source_file']}",
            }
        )

    sections.append(
        f"""
## Appendix A. Complete Numerical Registry

The following table is copied from the grand-synthesis registry and rounded for this
report. The unrounded values are preserved in `tables/master_number_registry.csv`
[source: FULL_V5_grand_synthesis/tables/master_number_registry.csv].

{md_table(reg_rows, [('theme', 'Theme'), ('metric', 'Metric'), ('value', 'Value'), ('unit', 'Unit'), ('source', 'Source')])}

## Appendix B. Consistency Audit

{md_table(cons_rows, [('metric', 'Metric'), ('source1', 'Source 1'), ('v1', 'Value 1'), ('source2', 'Source 2'), ('v2', 'Value 2'), ('disc', 'Discrepancy'), ('status', 'Status')])}

## Appendix C. Source Inventory and Runtime Notes

The requested report scope contains 19 directories. The inventory below records
CSV/report/figure counts at generation time [source: tables/report_source_inventory.csv].

{md_table(inv_rows, [('directory', 'Directory'), ('exists', 'Exists'), ('n_csv', 'CSV files'), ('n_reports_md', 'Report MD files'), ('n_figures_png', 'PNG figures')])}

The long-running CPU ablations were the extended mechanism items: Item 06 took
1494.0 s, Item 07 took 649.8 s, and the extended batch total wall time was 2714.7 s
[source: FULL_V5_extended_mechanism_ablations/reports/EXTENDED_MECHANISM_ABLATION_SUMMARY.md].
The GPU Tier 1 run took 12.08 min, and the full GPU discovery run took 19.06 min
[source: FULL_V5_GPU_tier1/reports/OVERNIGHT_COMPLETION.md;
FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md]. Later CPU batches were shorter:
falsification took 233.5 s, ROTO deep-dive 176.0 s, final gates 147.1 s, and
phase-center sensitivity 29.8 s [source:
FULL_V5_grand_synthesis/tables/directory_index.csv;
FULL_V5_final_gate/tables/final_gate_task_status.csv;
FULL_V5_phase_center_sensitivity/tables/phase_center_task_status.csv].

## Appendix D. Figure Manifest

The report directory copies key figures for convenience. The source file remains
the authoritative artifact.

{md_table(figs.to_dict('records'), [('figure', 'Copied figure'), ('caption', 'Caption'), ('source', 'Source'), ('status', 'Status')])}
"""
    )

    report = "\n".join(sections)
    word_count = len(report.split())
    report += f"\n\n<!-- Word count: {word_count} -->\n"
    (REPORT_DIR / "EXPERIMENTAL_REPORT.md").write_text(report, encoding="utf-8")
    return report


def main() -> None:
    report = build_report()
    print(f"Wrote {REPORT_DIR / 'EXPERIMENTAL_REPORT.md'}")
    print(f"Word count: {len(report.split())}")


if __name__ == "__main__":
    main()
