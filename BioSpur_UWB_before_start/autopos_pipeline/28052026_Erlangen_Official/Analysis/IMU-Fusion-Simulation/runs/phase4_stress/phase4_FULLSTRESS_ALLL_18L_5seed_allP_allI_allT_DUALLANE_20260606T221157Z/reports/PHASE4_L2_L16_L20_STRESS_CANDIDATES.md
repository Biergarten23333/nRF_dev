# Phase 4 L2/L16/L20 Stress Candidate Test

Generated: 2026-06-07T03:51:34.127011+00:00
Run ID: `phase4_FULLSTRESS_ALLL_18L_5seed_allP_allI_allT_DUALLANE_20260606T221157Z`
Status: `complete`
Wall time: 326.2 min

## Scope

- Sensors: `L0, L1, L2, L3, L4, L5, L7, L8, L10, L11, L12, L13, L14, L15, L16, L17, L18, L19`
- Seeds: `S00, S01, S02, S03, S04`
- Stress cases: `ST0_nominal, ST1_vibration_3x, ST2_bias_rw_2x, ST3_extrinsic_4x, ST4_harsh_combo`
- Position branch: `A0/U4`, P=`P0, P1, P2, P3, P4, P5`, I=`I0, I1, I2, I3, I4, I5, I6, I7, I8`, T=`T2, T3, T4, T5`
- Evaluation truth: Opti/Vicon. Same-P deltas compare each fusion row against pure UWB with the same P filter.
- Coordinate source: Phase 4 consumes the official aligned ROTO table with columns `uwb_x_mm`, `uwb_y_vertical_mm`, `uwb_z_mm`, `opti_x_mm`, `opti_y_vertical_mm`, `opti_z_mm`.
- Metric naming follows that table: `horizontal_xz` is the aligned horizontal plane and `vertical_y` is the aligned vertical axis. Do not read this as raw device XY/Z naming.

## Stress Cases

| stress_id | description | bias | noise | rw | vib | extrinsic |
| --- | --- | --- | --- | --- | --- | --- |
| ST0_nominal | control: datasheet/residual parameters unchanged | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| ST1_vibration_3x | motor/body vibration sensitivity multiplied by 3 | 1.0 | 1.0 | 1.0 | 3.0 | 1.0 |
| ST2_bias_rw_2x | residual accel bias and bias random-walk multiplied by 2 | 2.0 | 1.0 | 2.0 | 1.0 | 1.0 |
| ST3_extrinsic_4x | IMU/body mounting or frame residual multiplied by 4 | 1.0 | 1.0 | 1.0 | 1.0 | 4.0 |
| ST4_harsh_combo | combined bad case: bias 2.5x, noise 1.5x, rw 3x, vibration 4x, extrinsic 4x | 2.5 | 1.5 | 3.0 | 4.0 | 4.0 |

## Robust Ranking

| robust_rank | experiment_short | sensor_label | p95_mean | p95_worst | sameP_delta_p95_mean | sameP_improved_fraction | horiz_xz_p95_mean | vertical_y_p95_mean | radius_abs_mean | thickness_p95_mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | X_A0_U4_P4_L0_I5_T2 | L0 | 99.1 | 99.1 | 39.8 | 1.0 | 66.6 | 84.0 | 13.9 | 96.9 |
| 2 | X_A0_U4_P3_L0_I5_T2 | L0 | 109.2 | 109.2 | 91.1 | 1.0 | 83.2 | 86.1 | 30.5 | 102.8 |
| 3 | X_A0_U4_P1_L0_I5_T2 | L0 | 106.7 | 106.7 | 72.9 | 1.0 | 79.6 | 85.2 | 27.9 | 100.2 |
| 4 | X_A0_U4_P4_L0_I6_T2 | L0 | 101.1 | 101.1 | 37.8 | 1.0 | 67.8 | 86.5 | 14.9 | 98.2 |
| 5 | X_A0_U4_P3_L0_I6_T2 | L0 | 112.2 | 112.2 | 88.1 | 1.0 | 85.2 | 87.7 | 32.5 | 104.0 |
| 6 | X_A0_U4_P4_L0_I8_T2 | L0 | 103.0 | 103.0 | 35.9 | 1.0 | 69.1 | 88.8 | 15.7 | 99.1 |
| 7 | X_A0_U4_P2_L0_I5_T2 | L0 | 111.6 | 111.6 | 79.8 | 1.0 | 86.0 | 89.4 | 17.7 | 98.9 |
| 8 | X_A0_U4_P1_L0_I6_T2 | L0 | 109.8 | 109.8 | 69.8 | 1.0 | 81.8 | 87.3 | 29.7 | 101.9 |
| 9 | X_A0_U4_P0_L0_I5_T2 | L0 | 118.3 | 118.3 | 113.5 | 1.0 | 83.3 | 93.5 | 30.5 | 101.5 |
| 10 | X_A0_U4_P4_L0_I4_T2 | L0 | 105.2 | 105.2 | 33.7 | 1.0 | 70.6 | 90.5 | 16.3 | 100.0 |
| 11 | X_A0_U4_P3_L0_I8_T2 | L0 | 114.8 | 114.8 | 85.5 | 1.0 | 86.9 | 89.2 | 34.3 | 105.5 |
| 12 | X_A0_U4_P1_L0_I8_T2 | L0 | 112.4 | 112.4 | 67.2 | 1.0 | 84.1 | 89.1 | 31.3 | 104.0 |
| 13 | X_A0_U4_P4_L0_I3_T2 | L0 | 106.7 | 106.7 | 32.2 | 1.0 | 71.9 | 91.6 | 16.6 | 101.1 |
| 14 | X_A0_U4_P2_L0_I6_T2 | L0 | 114.9 | 114.9 | 76.5 | 1.0 | 88.5 | 91.7 | 19.5 | 101.2 |
| 15 | X_A0_U4_P0_L0_I6_T2 | L0 | 121.7 | 121.7 | 110.1 | 1.0 | 85.4 | 96.1 | 32.5 | 103.1 |
| 16 | X_A0_U4_P4_L0_I2_T2 | L0 | 108.3 | 108.3 | 30.6 | 1.0 | 73.2 | 92.6 | 17.1 | 102.1 |
| 17 | X_A0_U4_P4_L0_I7_T2 | L0 | 108.3 | 108.3 | 30.6 | 1.0 | 73.2 | 92.6 | 17.1 | 102.1 |
| 18 | X_A0_U4_P3_L0_I4_T2 | L0 | 117.4 | 117.4 | 82.8 | 1.0 | 89.2 | 90.7 | 35.9 | 107.0 |
| 19 | X_A0_U4_P1_L0_I4_T2 | L0 | 114.9 | 114.9 | 64.7 | 1.0 | 86.2 | 89.9 | 32.8 | 105.4 |
| 20 | X_A0_U4_P4_L0_I1_T2 | L0 | 109.9 | 109.9 | 29.0 | 1.0 | 74.2 | 93.3 | 17.6 | 102.9 |

## Best Row Per Sensor And Stress

| stress_id | experiment_short | sensor_label | p95_mean | sameP_delta_p95_mean | horiz_xz_p95_mean | vertical_y_p95_mean | radius_abs_mean | thickness_p95_mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ST0_nominal | X_A0_U4_P4_L0_I5_T2 | L0 | 99.1 | 39.8 | 66.6 | 84.0 | 13.9 | 96.9 |
| ST0_nominal | X_A0_U4_P4_L1_I5_T2 | L1 | 106.8 | 32.1 | 74.3 | 88.8 | 15.4 | 100.6 |
| ST0_nominal | X_A0_U4_P4_L16_I5_T2 | L16 ICM-45686 | 112.3 | 26.6 | 79.1 | 91.1 | 16.9 | 111.7 |
| ST0_nominal | X_A0_U4_P4_L19_I5_T2 | L19 | 119.0 | 19.9 | 84.5 | 90.5 | 16.7 | 107.7 |
| ST0_nominal | X_A0_U4_P4_L15_I5_T2 | L15 | 119.8 | 19.1 | 82.7 | 96.6 | 17.0 | 113.1 |
| ST0_nominal | X_A0_U4_P4_L5_I8_T2 | L5 | 121.5 | 17.4 | 86.8 | 99.5 | 18.2 | 118.6 |
| ST0_nominal | X_A0_U4_P4_L14_I5_T2 | L14 | 125.1 | 13.8 | 87.6 | 97.1 | 17.7 | 114.5 |
| ST0_nominal | X_A0_U4_P4_L13_I3_T2 | L13 | 128.0 | 10.9 | 89.2 | 103.3 | 20.7 | 118.8 |
| ST0_nominal | X_A0_U4_P4_L18_I3_T2 | L18 | 128.0 | 10.9 | 87.1 | 104.2 | 21.7 | 117.6 |
| ST0_nominal | X_A0_U4_P4_L12_I5_T4 | L12 | 128.5 | 10.3 | 91.8 | 106.9 | 23.2 | 113.2 |
| ST0_nominal | X_A0_U4_P4_L17_I3_T2 | L17 | 129.6 | 9.3 | 91.6 | 107.7 | 21.8 | 118.6 |
| ST0_nominal | X_A0_U4_P4_L2_I3_T4 | L2 MPU6050/JY61P-like | 130.1 | 8.8 | 92.3 | 106.3 | 23.6 | 115.5 |
| ST0_nominal | X_A0_U4_P4_L3_I8_T4 | L3 | 130.7 | 8.2 | 93.7 | 106.6 | 23.3 | 115.6 |
| ST0_nominal | X_A0_U4_P4_L11_I8_T4 | L11 | 131.5 | 7.4 | 90.6 | 109.6 | 23.6 | 115.6 |
| ST0_nominal | X_A0_U4_P4_L4_I8_T3 | L4 | 131.8 | 7.1 | 93.7 | 110.9 | 23.7 | 116.6 |
| ST0_nominal | X_A0_U4_P4_L10_I5_T4 | L10 | 132.6 | 6.3 | 92.9 | 106.9 | 23.6 | 115.6 |
| ST0_nominal | X_A0_U4_P4_L7_I5_T3 | L7 | 133.1 | 5.8 | 97.0 | 109.0 | 23.8 | 117.4 |
| ST0_nominal | X_A0_U4_P4_L8_I1_T3 | L8 | 135.1 | 3.8 | 95.4 | 111.3 | 23.8 | 118.2 |
| ST1_vibration_3x | X_A0_U4_P4_L0_I5_T2 | L0 | 99.1 | 39.8 | 66.6 | 84.0 | 13.9 | 96.9 |
| ST1_vibration_3x | X_A0_U4_P4_L1_I5_T2 | L1 | 108.6 | 30.3 | 70.5 | 87.5 | 15.5 | 100.3 |
| ST1_vibration_3x | X_A0_U4_P4_L16_I5_T2 | L16 ICM-45686 | 117.4 | 21.5 | 81.3 | 95.2 | 17.0 | 111.3 |
| ST1_vibration_3x | X_A0_U4_P4_L5_I5_T2 | L5 | 119.3 | 19.6 | 83.2 | 94.4 | 16.9 | 113.7 |
| ST1_vibration_3x | X_A0_U4_P4_L19_I3_T2 | L19 | 119.3 | 19.6 | 83.4 | 99.0 | 19.8 | 108.8 |
| ST1_vibration_3x | X_A0_U4_P4_L15_I5_T2 | L15 | 121.2 | 17.6 | 82.2 | 94.5 | 17.3 | 115.7 |
| ST1_vibration_3x | X_A0_U4_P4_L14_I5_T2 | L14 | 125.8 | 13.1 | 88.1 | 98.3 | 18.0 | 116.2 |
| ST1_vibration_3x | X_A0_U4_P4_L13_I5_T2 | L13 | 126.2 | 12.6 | 89.0 | 95.3 | 18.1 | 119.8 |
| ST1_vibration_3x | X_A0_U4_P4_L18_I3_T2 | L18 | 128.0 | 10.9 | 88.7 | 106.5 | 22.0 | 115.8 |
| ST1_vibration_3x | X_A0_U4_P4_L3_I5_T4 | L3 | 129.4 | 9.5 | 89.2 | 106.3 | 23.1 | 111.1 |
| ST1_vibration_3x | X_A0_U4_P4_L17_I5_T4 | L17 | 130.3 | 8.6 | 89.7 | 104.3 | 23.2 | 111.0 |
| ST1_vibration_3x | X_A0_U4_P4_L12_I5_T4 | L12 | 130.6 | 8.3 | 90.5 | 108.0 | 23.2 | 109.3 |

## Figures

- `figs/01_best_per_sensor_by_stress_p95.png`
- `figs/02_top12_worstcase_p95.png`
- `figs/03_L2_stress_top_candidates.png`
- `figs/03_L16_stress_top_candidates.png`
- `figs/03_L20_stress_top_candidates.png`

## Tables

- `tables/stress_robust_ranking.csv`
- `tables/stress_by_case_ranking.csv`
- `tables/stress_best_by_sensor_case.csv`
- `tables/stress_summary.csv`
- `tables/stress_track_metrics.csv`