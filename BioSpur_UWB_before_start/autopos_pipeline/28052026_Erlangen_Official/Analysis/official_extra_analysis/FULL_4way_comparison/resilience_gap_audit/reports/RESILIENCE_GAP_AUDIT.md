# Resilience Gap Audit

This audit adds diagnostic coverage for three previously open gaps: AutoPos bootstrap repeatability, layout-level residual delay correction bootstrap SD, and synthetic packet/dropout stress. It uses the four FULL comparison conditions.

## Scope

- Bootstrap input: raw inter-anchor range rows from `pairs_all.csv`, resampled within each unordered anchor pair.
- Layout bootstrap: metric MDS from bootstrap pair medians, then the same four case-specific alignment/scale gauges.
- Delay bootstrap: additive layout-level residual delay correction differences relative to anchor A.
- Static stress: raw static frames replayed through the T4 solver with synthetic frame packet-drop and anchor observation dropout.
- ROTO stress: solved-sample thinning only; this measures dynamic ATE/RPE/update-rate sensitivity, not raw ROTO range re-solving.

## Bootstrap Layout Repeatability

| case_id | coordinate_sd_median_mm | coordinate_sd_p95_mm | pairwise_distance_sd_median_mm | pairwise_distance_sd_p95_mm | scale_factor_sd |
| --- | --- | --- | --- | --- | --- |
| original_selfcal | 1.02 | 1.11 | 0.76 | 1.31 | 0.000 |
| vicon_truth_delaycal | 0.00 | 0.00 | 0.00 | 0.00 | 0.000 |
| scale_to_vicon_delaycal | 0.97 | 1.06 | 0.70 | 1.24 | 0.000 |
| one_baseline_EH_delaycal | 1.20 | 1.29 | 1.24 | 1.54 | 0.000 |

## Delay Bootstrap SD

| case_id | anchor_delay_rel_A_sd_median_mm | anchor_delay_rel_A_sd_p95_mm | anchor_delay_rel_A_sd_worst_mm | pair_residual_rms_median_mm |
| --- | --- | --- | --- | --- |
| original_selfcal | 0.56 | 0.62 | 0.63 | 51.55 |
| vicon_truth_delaycal | 0.45 | 0.47 | 0.47 | 43.84 |
| scale_to_vicon_delaycal | 0.53 | 0.59 | 0.60 | 34.38 |
| one_baseline_EH_delaycal | 0.54 | 0.60 | 0.62 | 37.26 |

## Static Dropout Stress

| case_id | condition | solve_fraction | position_err_3d_p50_mm | position_err_3d_p95_mm | sample_err_3d_p50_mm | sample_err_3d_p95_mm | repeatability_d3_std_median_mm |
| --- | --- | --- | --- | --- | --- | --- | --- |
| original_selfcal | baseline_all_frames_all8 | 1.000 | 76.3 | 180.9 | 88.6 | 260.7 | 63.1 |
| original_selfcal | anchor_keep_4 | 1.000 | 87.7 | 292.4 | 167.6 | 506.9 | 219.5 |
| original_selfcal | frame_keep_50_anchor_keep_4 | 1.000 | 86.4 | 217.7 | 169.2 | 502.8 | 219.4 |
| vicon_truth_delaycal | baseline_all_frames_all8 | 1.000 | 65.0 | 126.7 | 78.9 | 167.4 | 62.4 |
| vicon_truth_delaycal | anchor_keep_4 | 1.000 | 65.0 | 127.9 | 156.1 | 440.1 | 185.3 |
| vicon_truth_delaycal | frame_keep_50_anchor_keep_4 | 1.000 | 61.8 | 123.5 | 153.9 | 439.2 | 191.7 |
| scale_to_vicon_delaycal | baseline_all_frames_all8 | 1.000 | 69.3 | 134.8 | 83.8 | 174.1 | 60.4 |
| scale_to_vicon_delaycal | anchor_keep_4 | 1.000 | 83.2 | 140.2 | 153.6 | 444.5 | 195.6 |
| scale_to_vicon_delaycal | frame_keep_50_anchor_keep_4 | 1.000 | 75.7 | 126.5 | 156.5 | 445.6 | 202.2 |
| one_baseline_EH_delaycal | baseline_all_frames_all8 | 1.000 | 60.6 | 127.9 | 79.5 | 177.3 | 64.1 |
| one_baseline_EH_delaycal | anchor_keep_4 | 1.000 | 63.8 | 163.8 | 147.5 | 445.9 | 192.8 |
| one_baseline_EH_delaycal | frame_keep_50_anchor_keep_4 | 1.000 | 76.8 | 227.7 | 150.4 | 473.2 | 209.6 |

## ROTO Solved-Sample Dropout

| case_id | condition | samples_kept | ate_p50_mm | ate_p95_mm | rpe_rmse_mm | median_effective_update_rate_hz |
| --- | --- | --- | --- | --- | --- | --- |
| original_selfcal | solved_sample_keep_100 | 40661 | 102.6 | 256.9 | 134.5 | 10.0 |
| original_selfcal | solved_sample_keep_50 | 20322 | 102.9 | 257.1 | 144.0 | 5.0 |
| original_selfcal | solved_sample_keep_10 | 4067 | 101.2 | 250.5 | 169.3 | 1.0 |
| vicon_truth_delaycal | solved_sample_keep_100 | 40661 | 104.5 | 206.2 | 112.8 | 10.0 |
| vicon_truth_delaycal | solved_sample_keep_50 | 20499 | 104.0 | 205.9 | 123.7 | 5.0 |
| vicon_truth_delaycal | solved_sample_keep_10 | 3995 | 103.4 | 207.8 | 148.9 | 1.0 |
| scale_to_vicon_delaycal | solved_sample_keep_100 | 40661 | 107.7 | 208.0 | 111.7 | 10.0 |
| scale_to_vicon_delaycal | solved_sample_keep_50 | 20148 | 107.5 | 208.3 | 123.6 | 5.0 |
| scale_to_vicon_delaycal | solved_sample_keep_10 | 4230 | 109.3 | 209.9 | 151.4 | 1.1 |
| one_baseline_EH_delaycal | solved_sample_keep_100 | 40661 | 104.4 | 207.4 | 113.4 | 10.0 |
| one_baseline_EH_delaycal | solved_sample_keep_50 | 20448 | 104.0 | 207.1 | 124.4 | 5.0 |
| one_baseline_EH_delaycal | solved_sample_keep_10 | 4121 | 103.3 | 204.5 | 147.7 | 1.0 |

## Interpretation

The bootstrap closes the reporting gap as a diagnostic repeatability estimate, but it does not replace a true repeated-deployment AutoPos split. Delay numbers are quoted as differences relative to anchor A because the absolute delay/common-mode gauge is not identifiable from ranges alone.

Static dropout is the strongest stress table here because it replays raw range frames through the solver. ROTO dropout is intentionally labelled as solved-sample thinning; a full raw dynamic range re-solve with dropout would be a heavier follow-up.

## Output Tables

- `../tables/bootstrap_layout_repeatability.csv`
- `../tables/bootstrap_anchor_repeatability.csv`
- `../tables/bootstrap_delay_sd.csv`
- `../tables/bootstrap_delay_per_anchor.csv`
- `../tables/static_dropout_stress_summary.csv`
- `../tables/static_dropout_stress_per_position.csv`
- `../tables/roto_sample_dropout_stress_summary.csv`
