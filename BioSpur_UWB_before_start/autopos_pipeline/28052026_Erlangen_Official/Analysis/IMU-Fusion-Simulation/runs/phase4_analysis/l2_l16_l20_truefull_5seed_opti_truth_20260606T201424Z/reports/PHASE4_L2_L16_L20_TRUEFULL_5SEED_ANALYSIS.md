# Phase 4 L2/L16/L20 TRUEFULL 5-Seed Opti-Truth Analysis

Generated UTC: `2026-06-06T20:14:34.828793+00:00`

## Scope

Inputs:

- sensors: L2, L16, L20
- seeds: S00-S04 for each sensor
- rows per seed: 1377 runnable rows, 5292 declared rows, 3915 recorded exclusions
- evaluation truth: Opti/Vicon trajectory in the official ROTO sample tables

Primary production scope is A0. A1/A2/A3 are controls/oracle layouts and are not used for the production recommendation.

Important metric note: stored factory summaries contain legacy `horizontal_xz` and `vertical_y` split fields. This report does not relabel those fields as XY/Z. The primary ranking uses 3D Opti-truth metrics plus same-P improvement and ROTO shape metrics.

## Headline

Best production accuracy row: `X_A0_U4_P4_L20_I5_T2`.

- 5-seed mean track-median 3D P50/P95/RMSE = 68.4 / 112.1 / 75.7 mm
- same-P UWB P95 = 138.9 mm
- same-P P95 improvement = 26.8 mm
- B0 P0 P95 improvement = 119.7 mm
- ROTO radius abs / band P95 = 16.6 / 105.1 mm

Best rescue-from-raw-UWB row: `X_A0_U4_P0_L20_I5_T2`.

- 5-seed mean track-median 3D P95 = 133.9 mm
- same-P P95 improvement = 97.9 mm

Raw-range branch status: not recommended in the current proxy implementation.

- best raw row `X_A0_R4_L20_I5_T10` has P95 = 465.4 mm
- B0 delta = -233.6 mm, so it is worse than B0

## Production A0 UWB Baselines By P

| P | p50_mean | p95_mean | rmse_mean | radius_abs_mean | thickness_p95_mean | deltaR_rms_mean |
| --- | --- | --- | --- | --- | --- | --- |
| P4 | 78.2 | 138.9 | 86.2 | 23.7 | 116.7 | 391.6 |
| P5 | 92.9 | 156.7 | 103.5 | 56.0 | 102.0 | 459.6 |
| P1 | 91.6 | 179.6 | 108.5 | 52.2 | 134.4 | 229.7 |
| P2 | 98.6 | 191.3 | 114.8 | 51.9 | 144.0 | 159.3 |
| P3 | 99.9 | 200.3 | 117.9 | 53.0 | 159.1 | 107.8 |
| P0 | 105.8 | 231.8 | 132.8 | 59.7 | 173.6 | 80.1 |

## Top Production Rows By Accuracy

| experiment_short | n | p50_mean | p95_mean | p95_std | rmse_mean | sameP_uwb_p95_mean | sameP_delta_p95_mean | sameP_improved_seed_fraction | b0_delta_p95_mean | radius_abs_mean | thickness_p95_mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| X_A0_U4_P4_L20_I5_T2 | 5 | 68.4 | 112.1 | 2.1 | 75.7 | 138.9 | 26.8 | 1.0 | 119.7 | 16.6 | 105.1 |
| X_A0_U4_P4_L16_I5_T2 | 5 | 69.0 | 114.9 | 4.7 | 76.1 | 138.9 | 23.9 | 1.0 | 116.9 | 16.9 | 107.8 |
| X_A0_U4_P4_L20_I4_T2 | 5 | 70.4 | 117.5 | 5.2 | 77.6 | 138.9 | 21.4 | 1.0 | 114.3 | 18.8 | 109.4 |
| X_A0_U4_P4_L16_I3_T2 | 5 | 71.1 | 118.4 | 3.0 | 77.9 | 138.9 | 20.5 | 1.0 | 113.4 | 19.7 | 109.3 |
| X_A0_U4_P4_L20_I2_T2 | 5 | 71.6 | 118.5 | 2.8 | 79.0 | 138.9 | 20.4 | 1.0 | 113.3 | 20.0 | 112.5 |
| X_A0_U4_P4_L20_I8_T2 | 5 | 70.6 | 118.9 | 4.8 | 78.6 | 138.9 | 20.0 | 1.0 | 112.9 | 18.0 | 111.5 |
| X_A0_U4_P4_L20_I6_T2 | 5 | 68.7 | 119.5 | 2.6 | 77.4 | 138.9 | 19.4 | 1.0 | 112.3 | 17.6 | 106.8 |
| X_A0_U4_P4_L20_I3_T2 | 5 | 71.2 | 120.0 | 1.6 | 78.6 | 138.9 | 18.9 | 1.0 | 111.8 | 19.5 | 108.6 |
| X_A0_U4_P4_L16_I6_T2 | 5 | 73.0 | 121.1 | 2.0 | 79.3 | 138.9 | 17.8 | 1.0 | 110.7 | 17.9 | 109.8 |
| X_A0_U4_P4_L20_I1_T2 | 5 | 72.8 | 121.8 | 3.6 | 78.9 | 138.9 | 17.1 | 1.0 | 110.0 | 20.1 | 110.4 |
| X_A0_U4_P4_L20_I7_T2 | 5 | 71.3 | 122.0 | 4.3 | 80.2 | 138.9 | 16.9 | 1.0 | 109.9 | 19.9 | 114.1 |
| X_A0_U4_P1_L20_I5_T2 | 5 | 75.6 | 122.7 | 2.0 | 82.5 | 179.6 | 56.9 | 1.0 | 109.1 | 34.0 | 112.0 |
| X_A0_U4_P4_L16_I8_T2 | 5 | 71.8 | 122.9 | 3.5 | 80.7 | 138.9 | 15.9 | 1.0 | 108.9 | 18.3 | 111.1 |
| X_A0_U4_P4_L16_I1_T2 | 5 | 73.0 | 123.6 | 5.8 | 81.2 | 138.9 | 15.3 | 1.0 | 108.2 | 20.5 | 119.5 |
| X_A0_U4_P3_L20_I5_T2 | 5 | 76.4 | 123.6 | 1.0 | 83.5 | 200.3 | 76.7 | 1.0 | 108.2 | 37.1 | 113.7 |
| X_A0_U4_P4_L16_I7_T2 | 5 | 73.3 | 124.2 | 5.0 | 81.3 | 138.9 | 14.7 | 1.0 | 107.6 | 20.2 | 113.9 |
| X_A0_U4_P4_L16_I4_T2 | 5 | 71.2 | 125.0 | 2.7 | 79.9 | 138.9 | 13.9 | 1.0 | 106.8 | 19.1 | 113.3 |
| X_A0_U4_P4_L16_I2_T2 | 5 | 73.6 | 126.3 | 4.1 | 82.1 | 138.9 | 12.6 | 1.0 | 105.5 | 20.3 | 111.5 |
| X_A0_U4_P1_L16_I5_T2 | 5 | 75.9 | 126.4 | 5.3 | 83.9 | 179.6 | 53.2 | 1.0 | 105.4 | 34.7 | 115.2 |
| X_A0_U4_P2_L20_I5_T2 | 5 | 79.8 | 127.5 | 4.0 | 86.0 | 191.3 | 63.9 | 1.0 | 104.3 | 24.1 | 112.8 |

## Top Production Rows By Same-P Rescue

| experiment_short | n | p95_mean | sameP_uwb_p95_mean | sameP_delta_p95_mean | sameP_improved_seed_fraction | b0_delta_p95_mean | radius_abs_mean | thickness_p95_mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| X_A0_U4_P0_L20_I5_T2 | 5 | 133.9 | 231.8 | 97.9 | 1.0 | 97.9 | 37.2 | 113.4 |
| X_A0_U4_P0_L16_I5_T2 | 5 | 135.9 | 231.8 | 95.9 | 1.0 | 95.9 | 37.9 | 121.3 |
| X_A0_U4_P0_L20_I6_T2 | 5 | 138.0 | 231.8 | 93.8 | 1.0 | 93.8 | 39.7 | 119.3 |
| X_A0_U4_P0_L20_I8_T2 | 5 | 141.1 | 231.8 | 90.7 | 1.0 | 90.7 | 40.9 | 120.7 |
| X_A0_U4_P0_L20_I4_T2 | 5 | 143.6 | 231.8 | 88.2 | 1.0 | 88.2 | 42.9 | 121.4 |
| X_A0_U4_P0_L16_I6_T2 | 5 | 144.8 | 231.8 | 87.0 | 1.0 | 87.0 | 40.6 | 123.4 |
| X_A0_U4_P0_L16_I8_T2 | 5 | 145.5 | 231.8 | 86.3 | 1.0 | 86.3 | 41.6 | 122.9 |
| X_A0_U4_P0_L16_I4_T2 | 5 | 148.0 | 231.8 | 83.9 | 1.0 | 83.9 | 43.6 | 125.6 |
| X_A0_U4_P0_L16_I3_T2 | 5 | 148.3 | 231.8 | 83.5 | 1.0 | 83.5 | 45.3 | 122.3 |
| X_A0_U4_P0_L20_I3_T2 | 5 | 149.9 | 231.8 | 81.9 | 1.0 | 81.9 | 44.6 | 119.9 |
| X_A0_U4_P0_L20_I2_T2 | 5 | 151.2 | 231.8 | 80.6 | 1.0 | 80.6 | 45.7 | 125.4 |
| X_A0_U4_P0_L20_I7_T2 | 5 | 151.3 | 231.8 | 80.5 | 1.0 | 80.5 | 45.5 | 130.1 |
| X_A0_U4_P0_L20_I1_T2 | 5 | 151.9 | 231.8 | 79.9 | 1.0 | 79.9 | 46.0 | 125.5 |
| X_A0_U4_P0_L16_I7_T2 | 5 | 152.1 | 231.8 | 79.7 | 1.0 | 79.7 | 46.2 | 129.0 |
| X_A0_U4_P0_L16_I2_T2 | 5 | 154.5 | 231.8 | 77.3 | 1.0 | 77.3 | 46.6 | 126.7 |
| X_A0_U4_P3_L20_I5_T2 | 5 | 123.6 | 200.3 | 76.7 | 1.0 | 108.2 | 37.1 | 113.7 |
| X_A0_U4_P0_L16_I1_T2 | 5 | 156.9 | 231.8 | 74.9 | 1.0 | 74.9 | 46.8 | 134.9 |
| X_A0_U4_P3_L16_I5_T2 | 5 | 128.0 | 200.3 | 72.3 | 1.0 | 103.8 | 37.7 | 116.5 |
| X_A0_U4_P3_L20_I6_T2 | 5 | 129.1 | 200.3 | 71.1 | 1.0 | 102.7 | 39.2 | 116.8 |
| X_A0_U4_P3_L20_I8_T2 | 5 | 131.9 | 200.3 | 68.4 | 1.0 | 99.9 | 40.0 | 118.5 |

## Matched Best Combo Across Sensors

| experiment_short | n | p50_mean | p95_mean | p95_std | rmse_mean | sameP_delta_p95_mean | b0_delta_p95_mean | radius_abs_mean | thickness_p95_mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| X_A0_U4_P4_L16_I5_T2 | 5 | 69.0 | 114.9 | 4.7 | 76.1 | 23.9 | 116.9 | 16.9 | 107.8 |
| X_A0_U4_P4_L2_I5_T2 | 5 | 83.9 | 146.2 | 6.5 | 94.2 | -7.3 | 85.6 | 20.1 | 129.6 |
| X_A0_U4_P4_L20_I5_T2 | 5 | 68.4 | 112.1 | 2.1 | 75.7 | 26.8 | 119.7 | 16.6 | 105.1 |

## Top Five Per Sensor

| L | experiment_short | p50_mean | p95_mean | p95_std | sameP_delta_p95_mean | radius_abs_mean | thickness_p95_mean |
| --- | --- | --- | --- | --- | --- | --- | --- |
| L16 | X_A0_U4_P4_L16_I5_T2 | 69.0 | 114.9 | 4.7 | 23.9 | 16.9 | 107.8 |
| L16 | X_A0_U4_P4_L16_I3_T2 | 71.1 | 118.4 | 3.0 | 20.5 | 19.7 | 109.3 |
| L16 | X_A0_U4_P4_L16_I6_T2 | 73.0 | 121.1 | 2.0 | 17.8 | 17.9 | 109.8 |
| L16 | X_A0_U4_P4_L16_I8_T2 | 71.8 | 122.9 | 3.5 | 15.9 | 18.3 | 111.1 |
| L16 | X_A0_U4_P4_L16_I1_T2 | 73.0 | 123.6 | 5.8 | 15.3 | 20.5 | 119.5 |
| L2 | X_A0_U4_P4_L2_I5_T4 | 75.0 | 129.5 | 2.1 | 9.4 | 23.2 | 112.4 |
| L2 | X_A0_U4_P4_L2_I5_T3 | 74.8 | 131.2 | 2.0 | 7.7 | 23.4 | 113.7 |
| L2 | X_A0_U4_P4_L2_I6_T4 | 75.1 | 131.5 | 3.0 | 7.4 | 23.4 | 112.9 |
| L2 | X_A0_U4_P4_L2_I6_T3 | 75.2 | 131.8 | 2.7 | 7.1 | 23.5 | 114.6 |
| L2 | X_A0_U4_P4_L2_I8_T4 | 75.6 | 132.0 | 2.3 | 6.9 | 23.5 | 114.7 |
| L20 | X_A0_U4_P4_L20_I5_T2 | 68.4 | 112.1 | 2.1 | 26.8 | 16.6 | 105.1 |
| L20 | X_A0_U4_P4_L20_I4_T2 | 70.4 | 117.5 | 5.2 | 21.4 | 18.8 | 109.4 |
| L20 | X_A0_U4_P4_L20_I2_T2 | 71.6 | 118.5 | 2.8 | 20.4 | 20.0 | 112.5 |
| L20 | X_A0_U4_P4_L20_I8_T2 | 70.6 | 118.9 | 4.8 | 20.0 | 18.0 | 111.5 |
| L20 | X_A0_U4_P4_L20_I6_T2 | 68.7 | 119.5 | 2.6 | 19.4 | 17.6 | 106.8 |

## Raw-Range Branch

| experiment_short | n | p50_mean | p95_mean | p95_std | b0_uwb_p95_mean | b0_delta_p95_mean | b0_improved_seed_fraction | radius_abs_mean | thickness_p95_mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| X_A0_R4_L20_I5_T10 | 5 | 291.1 | 465.4 | 14.3 | 231.8 | -233.6 | 0.0 | 107.5 | 163.4 |
| X_A0_R4_L16_I5_T10 | 5 | 305.3 | 473.6 | 16.8 | 231.8 | -241.8 | 0.0 | 110.6 | 164.1 |
| X_A0_R4_L20_I6_T10 | 5 | 316.9 | 484.9 | 7.4 | 231.8 | -253.1 | 0.0 | 113.3 | 153.0 |
| X_A0_R4_L20_I8_T10 | 5 | 326.2 | 501.2 | 22.8 | 231.8 | -269.4 | 0.0 | 117.4 | 156.5 |
| X_A0_R4_L16_I6_T10 | 5 | 328.7 | 510.3 | 7.1 | 231.8 | -278.5 | 0.0 | 114.1 | 162.8 |
| X_A0_R4_L20_I4_T10 | 5 | 337.8 | 514.6 | 23.7 | 231.8 | -282.8 | 0.0 | 126.7 | 146.2 |
| X_A0_R3_L20_I5_T10 | 5 | 330.8 | 517.9 | 12.4 | 231.8 | -286.1 | 0.0 | 126.9 | 149.4 |
| X_A0_R4_L16_I8_T10 | 5 | 332.7 | 520.2 | 15.1 | 231.8 | -288.4 | 0.0 | 117.5 | 158.6 |
| X_A0_R3_L16_I5_T10 | 5 | 340.7 | 525.9 | 13.1 | 231.8 | -294.1 | 0.0 | 129.4 | 149.1 |
| X_A0_R2_L20_I5_T10 | 5 | 337.7 | 528.9 | 11.0 | 231.8 | -297.1 | 0.0 | 131.2 | 147.9 |
| X_A0_R2_L16_I5_T10 | 5 | 347.9 | 537.8 | 13.5 | 231.8 | -306.0 | 0.0 | 133.6 | 149.8 |
| X_A0_R4_L20_I5_T9 | 5 | 341.2 | 538.7 | 10.9 | 231.8 | -306.9 | 0.0 | 128.4 | 146.1 |
| X_A0_R3_L20_I6_T10 | 5 | 351.0 | 542.7 | 3.4 | 231.8 | -310.9 | 0.0 | 134.1 | 144.1 |
| X_A0_R4_L16_I3_T10 | 5 | 365.4 | 545.7 | 8.8 | 231.8 | -313.9 | 0.0 | 134.8 | 147.2 |
| X_A0_R4_L16_I4_T10 | 5 | 357.0 | 546.3 | 14.2 | 231.8 | -314.5 | 0.0 | 124.0 | 160.5 |

## Figures

- `figs/01_top_A0_position_vs_sameP_UWB.png`
- `figs/02_matched_best_combo_sensor_comparison.png`
- `figs/03_T2_P_I_heatmap_by_sensor.png`
- `figs/04_raw_range_branch_vs_B0.png`
- `figs/05_best_row_track_distribution.png`

## Outputs

- `tables/production_A0_position_ranking_by_accuracy.csv`
- `tables/production_A0_position_ranking_by_sameP_rescue.csv`
- `tables/production_A0_raw_range_ranking.csv`
- `tables/matched_best_combo_L2_L16_L20.csv`
- `tables/top5_by_sensor.csv`
- `tables/all_seed_summary_with_deltas.csv`
- `tables/all_seed_track_metrics.csv`