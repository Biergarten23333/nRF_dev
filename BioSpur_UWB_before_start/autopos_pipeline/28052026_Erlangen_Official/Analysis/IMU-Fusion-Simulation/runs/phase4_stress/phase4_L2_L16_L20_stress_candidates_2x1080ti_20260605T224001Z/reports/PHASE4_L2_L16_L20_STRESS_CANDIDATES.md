# Phase 4 L2/L16/L20 Stress Candidate Test

Generated: 2026-06-05T22:48:57.098686+00:00
Run ID: `phase4_L2_L16_L20_stress_candidates_2x1080ti_20260605T224001Z`
Status: `complete`
Wall time: 8.9 min

## Scope

- Sensors: `L2, L16, L20`
- Seeds: `S00, S01, S02, S03, S04`
- Stress cases: `ST0_nominal, ST1_vibration_3x, ST2_bias_rw_2x, ST3_extrinsic_4x, ST4_harsh_combo`
- Position branch: `A0/U4`, P=`P0, P4, P5`, I=`I3, I5`, T=`T2, T4`
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
| 1 | X_A0_U4_P4_L20_I3_T2 | L20 Xsens MTi-3 | 124.1 | 131.9 | 14.8 | 1.0 | 86.1 | 101.6 | 20.1 | 114.5 |
| 2 | X_A0_U4_P4_L20_I5_T2 | L20 Xsens MTi-3 | 123.2 | 137.7 | 15.7 | 1.0 | 87.0 | 94.7 | 17.3 | 112.4 |
| 3 | X_A0_U4_P4_L20_I5_T4 | L20 Xsens MTi-3 | 128.4 | 129.4 | 10.5 | 1.0 | 87.7 | 104.1 | 22.8 | 104.2 |
| 4 | X_A0_U4_P4_L16_I5_T4 | L16 ICM-45686 | 129.1 | 131.8 | 9.8 | 1.0 | 87.9 | 103.9 | 22.8 | 107.2 |
| 5 | X_A0_U4_P4_L16_I3_T2 | L16 ICM-45686 | 127.1 | 137.6 | 11.8 | 0.9 | 88.5 | 103.7 | 20.4 | 119.3 |
| 6 | X_A0_U4_P4_L16_I5_T2 | L16 ICM-45686 | 126.3 | 142.6 | 12.6 | 0.8 | 90.9 | 98.2 | 17.7 | 117.2 |
| 7 | X_A0_U4_P4_L20_I3_T4 | L20 Xsens MTi-3 | 132.0 | 133.8 | 6.9 | 1.0 | 88.4 | 105.5 | 23.3 | 109.6 |
| 8 | X_A0_U4_P4_L16_I3_T4 | L16 ICM-45686 | 132.3 | 133.2 | 6.6 | 1.0 | 89.0 | 105.7 | 23.4 | 112.4 |
| 9 | X_A0_U4_P0_L20_I5_T2 | L20 Xsens MTi-3 | 143.4 | 158.5 | 88.4 | 1.0 | 108.0 | 107.9 | 38.9 | 122.5 |
| 10 | X_A0_U4_P4_L2_I5_T4 | L2 MPU6050/JY61P-like | 133.4 | 135.6 | 5.5 | 1.0 | 93.5 | 109.2 | 23.4 | 117.2 |
| 11 | X_A0_U4_P4_L2_I3_T4 | L2 MPU6050/JY61P-like | 134.0 | 135.9 | 4.9 | 1.0 | 92.7 | 108.4 | 23.6 | 116.1 |
| 12 | X_A0_U4_P5_L20_I5_T2 | L20 Xsens MTi-3 | 137.4 | 150.9 | 19.3 | 1.0 | 105.0 | 102.2 | 40.1 | 105.4 |
| 13 | X_A0_U4_P5_L20_I3_T2 | L20 Xsens MTi-3 | 140.8 | 147.7 | 15.9 | 1.0 | 108.1 | 104.8 | 47.1 | 105.7 |
| 14 | X_A0_U4_P0_L16_I5_T2 | L16 ICM-45686 | 148.0 | 167.9 | 83.8 | 1.0 | 112.6 | 111.1 | 39.9 | 127.4 |
| 15 | X_A0_U4_P0_L20_I3_T2 | L20 Xsens MTi-3 | 152.1 | 162.6 | 79.7 | 1.0 | 113.7 | 119.9 | 46.0 | 127.6 |
| 16 | X_A0_U4_P5_L16_I5_T2 | L16 ICM-45686 | 141.6 | 159.0 | 15.1 | 0.8 | 110.4 | 104.6 | 40.7 | 109.6 |
| 17 | X_A0_U4_P5_L16_I3_T2 | L16 ICM-45686 | 143.9 | 153.2 | 12.9 | 0.9 | 111.5 | 107.7 | 47.7 | 110.0 |
| 18 | X_A0_U4_P5_L20_I5_T4 | L20 Xsens MTi-3 | 149.0 | 150.4 | 7.7 | 1.0 | 114.4 | 109.9 | 53.9 | 103.6 |
| 19 | X_A0_U4_P5_L16_I5_T4 | L16 ICM-45686 | 149.2 | 152.1 | 7.5 | 1.0 | 116.6 | 110.8 | 54.1 | 106.0 |
| 20 | X_A0_U4_P0_L16_I3_T2 | L16 ICM-45686 | 157.5 | 173.1 | 74.3 | 1.0 | 116.7 | 120.9 | 46.8 | 133.0 |

## Best Row Per Sensor And Stress

| stress_id | experiment_short | sensor_label | p95_mean | sameP_delta_p95_mean | horiz_xz_p95_mean | vertical_y_p95_mean | radius_abs_mean | thickness_p95_mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ST0_nominal | X_A0_U4_P4_L16_I5_T2 | L16 ICM-45686 | 112.3 | 26.6 | 79.1 | 91.1 | 16.9 | 111.7 |
| ST0_nominal | X_A0_U4_P4_L20_I5_T2 | L20 Xsens MTi-3 | 116.2 | 22.6 | 80.2 | 92.6 | 16.5 | 106.6 |
| ST0_nominal | X_A0_U4_P4_L2_I3_T4 | L2 MPU6050/JY61P-like | 130.1 | 8.8 | 92.3 | 106.3 | 23.6 | 115.5 |
| ST1_vibration_3x | X_A0_U4_P4_L20_I5_T2 | L20 Xsens MTi-3 | 114.9 | 23.9 | 80.7 | 92.6 | 16.7 | 106.9 |
| ST1_vibration_3x | X_A0_U4_P4_L16_I5_T2 | L16 ICM-45686 | 117.4 | 21.5 | 81.3 | 95.2 | 17.0 | 111.3 |
| ST1_vibration_3x | X_A0_U4_P4_L2_I5_T4 | L2 MPU6050/JY61P-like | 132.0 | 6.9 | 88.8 | 109.1 | 23.3 | 110.6 |
| ST2_bias_rw_2x | X_A0_U4_P4_L20_I3_T2 | L20 Xsens MTi-3 | 125.3 | 13.5 | 91.3 | 102.0 | 20.3 | 116.3 |
| ST2_bias_rw_2x | X_A0_U4_P4_L16_I5_T4 | L16 ICM-45686 | 129.0 | 9.9 | 86.7 | 103.5 | 22.9 | 106.5 |
| ST2_bias_rw_2x | X_A0_U4_P4_L2_I5_T4 | L2 MPU6050/JY61P-like | 133.8 | 5.1 | 94.8 | 110.0 | 23.3 | 120.2 |
| ST3_extrinsic_4x | X_A0_U4_P4_L20_I5_T2 | L20 Xsens MTi-3 | 121.6 | 17.3 | 87.7 | 89.7 | 16.9 | 111.5 |
| ST3_extrinsic_4x | X_A0_U4_P4_L16_I3_T2 | L16 ICM-45686 | 122.4 | 16.5 | 86.0 | 100.9 | 20.0 | 116.5 |
| ST3_extrinsic_4x | X_A0_U4_P4_L2_I5_T4 | L2 MPU6050/JY61P-like | 133.8 | 5.1 | 93.3 | 109.7 | 23.4 | 115.8 |
| ST4_harsh_combo | X_A0_U4_P4_L20_I5_T4 | L20 Xsens MTi-3 | 129.4 | 9.4 | 91.0 | 106.8 | 23.1 | 112.7 |
| ST4_harsh_combo | X_A0_U4_P4_L16_I3_T4 | L16 ICM-45686 | 131.7 | 7.1 | 91.6 | 105.4 | 23.5 | 115.7 |
| ST4_harsh_combo | X_A0_U4_P4_L2_I5_T4 | L2 MPU6050/JY61P-like | 135.6 | 3.2 | 99.6 | 111.9 | 23.7 | 122.1 |

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