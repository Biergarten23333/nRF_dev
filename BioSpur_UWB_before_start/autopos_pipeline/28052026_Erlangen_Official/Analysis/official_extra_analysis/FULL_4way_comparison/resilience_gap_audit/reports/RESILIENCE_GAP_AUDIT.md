# Resilience Gap Audit

This audit adds diagnostic coverage for three previously open gaps: AutoPos bootstrap numerical precision, layout-level residual delay correction numerical precision, and synthetic packet/dropout stress. It uses the four FULL comparison conditions.

## Scope

- Bootstrap input: raw inter-anchor range rows from `pairs_all.csv`, resampled within each unordered anchor pair.
- Layout bootstrap: metric MDS from bootstrap pair medians, then the same four case-specific alignment/scale gauges.
- Numerical-precision check: analytical per-pair median SE uses `1.2533 * std / sqrt(n)` and is compared to the existing layout bootstrap spread.
- Delay bootstrap: additive layout-level residual delay correction differences relative to anchor A, interpreted as within-campaign median sampling SE rather than independent delay repeatability.
- Static stress: raw static frames replayed through the T4 solver with synthetic frame packet-drop and anchor observation dropout.
- ROTO stress: solved-sample thinning only; this measures dynamic ATE/RPE/update-rate sensitivity, not raw ROTO range re-solving.

## Bootstrap Layout Numerical Precision

| case_id | coordinate_sd_median_mm | coordinate_sd_p95_mm | pairwise_distance_sd_median_mm | pairwise_distance_sd_p95_mm | scale_factor_sd |
| --- | --- | --- | --- | --- | --- |
| original_selfcal | 1.02 | 1.11 | 0.76 | 1.31 | 0.000 |
| vicon_truth_delaycal | 0.00 | 0.00 | 0.00 | 0.00 | 0.000 |
| scale_to_vicon_delaycal | 0.97 | 1.06 | 0.70 | 1.24 | 0.000 |
| one_baseline_EH_delaycal | 1.20 | 1.29 | 1.24 | 1.54 | 0.000 |

## Bootstrap Numerical Precision Check

| case_id | pair_analytical_se_median_mm | pair_analytical_se_p95_mm | bootstrap_pairwise_distance_sd_median_mm | bootstrap_coordinate_sd_median_mm | pairwise_sd_over_median_se | coordinate_sd_over_median_se |
| --- | --- | --- | --- | --- | --- | --- |
| original_selfcal | 0.665 | 0.857 | 0.761 | 1.024 | 1.146 | 1.541 |
| vicon_truth_delaycal | 0.665 | 0.857 | 0.000 | 0.000 | nan | nan |
| scale_to_vicon_delaycal | 0.665 | 0.857 | 0.698 | 0.968 | 1.051 | 1.456 |
| one_baseline_EH_delaycal | 0.665 | 0.857 | 1.235 | 1.196 | 1.859 | 1.799 |

The pair-level analytical median SE is sub-millimeter because each unordered anchor pair has about two thousand samples. The layout bootstrap SD is the propagated version of that within-campaign median sampling SE, with modest MDS/gauge amplification. It is therefore a numerical-precision diagnostic, not independent deployment repeatability.

## Delay Bootstrap Numerical Precision

| case_id | anchor_delay_rel_A_sd_median_mm | anchor_delay_rel_A_sd_p95_mm | anchor_delay_rel_A_sd_worst_mm | pair_residual_rms_median_mm |
| --- | --- | --- | --- | --- |
| original_selfcal | 0.56 | 0.62 | 0.63 | 51.55 |
| vicon_truth_delaycal | 0.45 | 0.47 | 0.47 | 43.84 |
| scale_to_vicon_delaycal | 0.53 | 0.59 | 0.60 | 34.38 |
| one_baseline_EH_delaycal | 0.54 | 0.60 | 0.62 | 37.26 |

## Static Dropout Stress

| case_id | condition | solve_fraction | n_frames_attempted | n_frames_failed | nonconvergence_rate | position_err_3d_p50_mm | position_err_3d_p95_mm | sample_err_3d_p50_mm | sample_err_3d_p95_mm | repeatability_d3_std_median_mm | gdop_median |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| original_selfcal | baseline_all_frames_all8 | 1.000 | 2880 | 0 | 0.000 | 76.3 | 180.9 | 88.6 | 260.7 | 63.1 | 1.1 |
| original_selfcal | anchor_keep_4 | 1.000 | 2880 | 0 | 0.000 | 87.7 | 292.4 | 167.6 | 506.9 | 219.5 | 2.0 |
| original_selfcal | anchor_keep_4_fixedrandom | 0.962 | 2880 | 109 | 0.038 | 129.8 | 649.5 | 149.8 | 686.4 | 94.5 | 2.0 |
| original_selfcal | anchor_keep_4_bestgdop | 0.971 | 2880 | 83 | 0.029 | 384.0 | 2981.3 | 398.9 | 3009.7 | 64.2 | 1.5 |
| original_selfcal | frame_keep_50_anchor_keep_4 | 1.000 | 1487 | 0 | 0.000 | 90.9 | 337.3 | 165.9 | 533.6 | 220.7 | 2.1 |
| vicon_truth_delaycal | baseline_all_frames_all8 | 1.000 | 2880 | 0 | 0.000 | 65.0 | 126.7 | 78.9 | 167.4 | 62.4 | 1.1 |
| vicon_truth_delaycal | anchor_keep_4 | 1.000 | 2880 | 0 | 0.000 | 65.0 | 127.9 | 156.1 | 440.1 | 185.3 | 2.0 |
| vicon_truth_delaycal | anchor_keep_4_fixedrandom | 0.966 | 2880 | 97 | 0.034 | 163.2 | 517.9 | 167.6 | 605.0 | 90.7 | 2.0 |
| vicon_truth_delaycal | anchor_keep_4_bestgdop | 0.972 | 2880 | 81 | 0.028 | 277.8 | 2808.6 | 292.7 | 2846.7 | 80.5 | 1.5 |
| vicon_truth_delaycal | frame_keep_50_anchor_keep_4 | 1.000 | 1457 | 0 | 0.000 | 68.0 | 115.9 | 155.9 | 456.3 | 192.8 | 2.0 |
| scale_to_vicon_delaycal | baseline_all_frames_all8 | 1.000 | 2880 | 0 | 0.000 | 69.3 | 134.8 | 83.8 | 174.1 | 60.4 | 1.1 |
| scale_to_vicon_delaycal | anchor_keep_4 | 1.000 | 2880 | 0 | 0.000 | 83.2 | 140.2 | 153.6 | 444.5 | 195.6 | 2.0 |
| scale_to_vicon_delaycal | anchor_keep_4_fixedrandom | 0.962 | 2880 | 109 | 0.038 | 183.8 | 490.0 | 189.8 | 536.3 | 91.9 | 1.9 |
| scale_to_vicon_delaycal | anchor_keep_4_bestgdop | 0.976 | 2880 | 70 | 0.024 | 241.0 | 2831.5 | 263.4 | 2877.5 | 53.8 | 1.5 |
| scale_to_vicon_delaycal | frame_keep_50_anchor_keep_4 | 1.000 | 1477 | 0 | 0.000 | 66.7 | 156.8 | 157.9 | 452.0 | 204.8 | 2.0 |
| one_baseline_EH_delaycal | baseline_all_frames_all8 | 1.000 | 2880 | 0 | 0.000 | 60.6 | 127.9 | 79.5 | 177.3 | 64.1 | 1.1 |
| one_baseline_EH_delaycal | anchor_keep_4 | 1.000 | 2880 | 0 | 0.000 | 63.8 | 163.8 | 147.5 | 445.9 | 192.8 | 2.0 |
| one_baseline_EH_delaycal | anchor_keep_4_fixedrandom | 0.957 | 2880 | 125 | 0.043 | 190.0 | 660.2 | 209.5 | 775.3 | 91.3 | 2.0 |
| one_baseline_EH_delaycal | anchor_keep_4_bestgdop | 0.973 | 2880 | 77 | 0.027 | 224.2 | 2892.1 | 252.1 | 2937.3 | 55.1 | 1.5 |
| one_baseline_EH_delaycal | frame_keep_50_anchor_keep_4 | 1.000 | 1481 | 0 | 0.000 | 73.8 | 141.7 | 153.0 | 435.3 | 197.8 | 2.1 |

The `anchor_keep_4_bestgdop` control uses the requested criterion: for each static truth position it evaluates all C(8,4)=70 subsets, computes range-only GDOP from unit-vector rows, picks the minimum-GDOP subset, and keeps that subset fixed across frames. The fair fixed-vs-fixed comparison is `anchor_keep_4_fixedrandom` versus `anchor_keep_4_bestgdop`: median GDOP changes from 1.96 to 1.54, while position P50 changes from 173.5 mm to 259.4 mm and median non-convergence changes from 3.8% to 2.7%. The rotating random-per-frame keep-4 baseline remains useful but is not the clean control; its median position P50 is 74.1 mm and median GDOP is 2.04. P50/P95 values in this table are conditional on convergence; failed frames are exposed separately.

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

The bootstrap is a numerical-precision diagnostic, not a true repeated-deployment AutoPos split. After the analytical SE check, the layout-bootstrap and delay-bootstrap rows should be read as within-campaign numerical precision rather than deployment repeatability. Delay numbers are quoted as differences relative to anchor A because the absolute delay/common-mode gauge is not identifiable from ranges alone.

Static dropout is the strongest stress table here because it replays raw range frames through the solver. The best-GDOP keep-4 result shows that GDOP alone is not a safe runtime subset-selection criterion; a deployable 4-anchor policy needs layer diversity, root/mirror sanity checks, and residual gating. ROTO dropout is intentionally labelled as solved-sample thinning; a full raw dynamic range re-solve with dropout would be a heavier follow-up.

## Output Tables

- `../tables/bootstrap_layout_repeatability.csv`
- `../tables/bootstrap_pair_sampling_se.csv`
- `../tables/bootstrap_numerical_precision.csv`
- `../tables/bootstrap_anchor_repeatability.csv`
- `../tables/bootstrap_delay_sd.csv`
- `../tables/bootstrap_delay_per_anchor.csv`
- `../tables/static_dropout_stress_summary.csv`
- `../tables/static_dropout_stress_per_position.csv`
- `../tables/roto_sample_dropout_stress_summary.csv`
