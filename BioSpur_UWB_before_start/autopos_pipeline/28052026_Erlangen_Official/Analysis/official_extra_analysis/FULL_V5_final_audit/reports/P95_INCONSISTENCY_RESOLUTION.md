# V5 Baseline P95/RMSE Inconsistency Resolution

## Source A: FULL_V5 raw-frame baseline -> P95=153.6, RMSE=82.8

Primary file:

`/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL_V5/tables/static_summary_DLOO.csv`

Row:

| layout_source | correction_source | tag_delay_mode | tag_delay_value_mm | n_positions | n_frames | median_3d_mm | p95_3d_mm | rmse_3d_mm |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| L_V5 | C_V5 | D_LOO_CV | 49.621 | 24 | 28818 | 67.848731 | 153.634628 | 82.798727 |

This row is also repeated in:

- `FULL_V5/tables/unified_results.csv`
- `FULL_transfer_matrix/tables/transfer_matrix_48cells.csv`
- `FULL_transfer_matrix/tables/unified_results.csv`
- `FULL_V4_vs_V5_final/tables/final_transfer_matrix_diagonal.csv`
- `FULL_V4_vs_V5_final/tables/final_v4_vs_v5_static_comparison.csv`
- Several mechanism-ablation tables that explicitly reference the transfer-matrix or existing V5 row.

Code provenance:

- `FULL_V5/scripts/run_full_v5_ablation_pipeline.py` sets `point_estimator = "mean"` for Phase 2 static evaluation.
- `static_cell_worker()` calls `solve_static_file_with_layout(..., point_estimator="mean")`.
- The unified result note is `static mean-position estimator; V5 rigid anchor lock, no scale`.
- `FULL_V5/tables/static_per_position.csv`, filtered to `tag_delay_mode == D_LOO_CV`, reproduces Source A exactly:
  - median = 67.848731 mm
  - P95 = 153.634628 mm
  - RMSE = 82.798727 mm

## Source B: followup synthetic-p50 row -> P95=160.5, RMSE=86.4

Primary saved file:

`/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL_V5_followup_validation/tables/f6_final_comparison.csv`

Row:

| variant | layout | correction | percentile | weighting | d_tag_mode | d_tag_value | n_positions | median_3d_mm | p95_3d_mm | rmse_3d_mm |
|---|---|---|---:|---|---|---:|---:|---:|---:|---:|
| V5 baseline | L_V5 | C_V5 | p50 | uniform | fixed_LOO_49.621 | 49.621 | 24 | 67.809259 | 160.508642 | 86.399892 |

This row is copied or cited by:

- `FULL_V5_followup_validation/tables/f1_combination_grid.csv`
- `FULL_V5_followup_validation/tables/f4_percentile_fixed_dtag.csv`
- `FULL_V5_followup_validation/tables/f5_selective_percentile_results.csv`
- `FULL_V5_overnight_batch2/tables/n4_p30_recalibration.csv`
- `FULL_V5_overnight_batch2/tables/paper_table_static_accuracy.csv`
- `FULL_V5_grand_synthesis/tables/final_headline_table.csv`
- `FULL_V5_final_gate/tables/g1_locked_headline.csv`
- `FULL_V5_experimental_report/tables/headline_locked.csv`
- `FULL_V5_experimental_report/tables/locked_headline_v2.csv`

Code provenance:

- `FULL_V5_followup_validation/scripts/run_followup_validation.py` builds `p50_ranges = percentile_ranges(raw_ranges, 50)`.
- `solve_ranges()` creates one synthetic frame per static position using one scalar range per anchor, with `quality_percent=100.0`.
- `task_f6()` writes `f6_final_comparison.csv` from that synthetic p50 range matrix.

Important reproducibility note:

The saved `f6_final_comparison.csv` does not reproduce from the current saved followup script and dependency chain. Re-running the same nominal call through the current `build_context()`, `percentile_ranges(..., 50)`, and `solve_ranges(..., d_tag_mm=49.621)` gives:

| convention | n_positions | median_3d_mm | p95_3d_mm | rmse_3d_mm |
|---|---:|---:|---:|---:|
| Current followup synthetic p50 fixed D_tag=49.621 reproduction | 24 | 63.199331 | 174.045269 | 90.978964 |

The saved CSV timestamp is `2026-06-18 00:52:25`, while the followup script timestamp is `2026-06-18 00:53:39`, so the saved table predates the current script file. Treat Source B as a stale/generated table unless it is regenerated and locked again under a named scalar-range convention.

## Root Cause

The two cited rows are not the same measurement.

Source A is the original `FULL_V5` static raw-frame replay result. It solves every usable raw frame, then summarizes each static capture with the mean solved position. Its summary uses 24 positions and 28,818 solved frames.

Source B is a later followup-validation row using a synthetic scalar p50 range matrix: one median range per `(position, anchor)` link, then one solve per position. It has 24 positions but no 28,818-frame mean-position stage.

The inconsistency is not explained by position count, anchor layout, or nominal tag delay:

- Both saved rows use 24 positions.
- Both saved rows use `L_V5 + C_V5`.
- Both saved rows use the same nominal tag delay value, 49.621 mm.
- The difference is the evaluator/range representation: raw-frame mean-position replay versus synthetic p50 range-vector solve.

The reporting failure happened downstream: `FULL_V5_grand_synthesis/tables/consistency_audit.csv` compared only the median between `FULL_V5/static_summary_DLOO` and `followup/f6 V5 baseline`. The medians differ by only 0.039 mm, so the audit marked the rows `OK`, but it never compared P95 or RMSE. Later headline tables then used Source B's P95/RMSE while treating the row as the canonical V5 baseline.

## Correct Value For Paper

For the original `FULL_V5` V5+D_LOO baseline, the correct paper value is Source A:

| Convention | Median mm | P95 mm | RMSE mm |
|---|---:|---:|---:|
| `FULL_V5` raw-frame mean-position baseline, `L_V5+C_V5+D_LOO_CV` | 67.848731 | 153.634628 | 82.798727 |

Use this when the paper says "V5 baseline" and cites `FULL_V5`, `FULL_transfer_matrix`, or the original V5 static deployment result.

Do not use `67.809 / 160.509 / 86.400` as the canonical V5 baseline. That row is a later synthetic-p50 generated table, and it is not reproducible from the current saved script state.

If the paper needs a scalar-range p50 baseline for comparison with lower-trim/clean-window/quality pipelines, it must be labeled as a separate convention and regenerated. The current regenerated scalar baselines found during this trace are:

| Convention | Median mm | P95 mm | RMSE mm | Source |
|---|---:|---:|---:|---|
| Current followup synthetic p50, fixed D_tag=49.621 | 63.199331 | 174.045269 | 90.978964 | regenerated in this audit trace |
| Three-dimension raw-frame V3 scalar p50 LOO D_tag-per-fold | 64.143069 | 175.416166 | 85.011178 | `FULL_V5_three_dimensions/tables/t10_quality_weighted_positioning.csv` |

These are not replacements for Source A; they are different scalar-range evaluation conventions.

## Action

Fix the V3 report/headline table as follows:

1. Replace row C `V5 baseline, p50, uniform, D_LOO=49.6` if it is intended to mean the original `FULL_V5` baseline:
   - old: 67.809 / 160.509 / 86.400 from `FULL_V5_followup_validation/tables/f6_final_comparison.csv`
   - corrected: 67.849 / 153.635 / 82.799 from `FULL_V5/tables/static_summary_DLOO.csv`

2. If row C is intended to be a scalar p50 baseline, rename it and regenerate it:
   - do not call it the canonical `V5 baseline`
   - cite the regenerated scalar pipeline source
   - use one internally consistent median/P95/RMSE triplet from the same per-position errors

3. Update all consistency audits to compare `median_3d_mm`, `p95_3d_mm`, and `rmse_3d_mm`, not median alone.

4. Add an evaluator-convention column to headline tables:
   - `raw_frame_mean_position`
   - `synthetic_p50_fixed_dtag`
   - `scalar_range_loo_dtag_per_fold`
   - `lower_trim_20_huber30_loo`

## Generated Evidence Tables

This trace wrote two audit support tables:

- `FULL_V5_final_audit/tables/p95_inconsistency_convention_summary.csv`
- `FULL_V5_final_audit/tables/p95_inconsistency_per_position_three_way.csv`

Run context:

- Worker count used: 1 CPU worker, tracing only.
- GPU use: none.
- Utilization snapshot during trace: CPU 5.9%, GPU0 0%, GPU1 0%.

