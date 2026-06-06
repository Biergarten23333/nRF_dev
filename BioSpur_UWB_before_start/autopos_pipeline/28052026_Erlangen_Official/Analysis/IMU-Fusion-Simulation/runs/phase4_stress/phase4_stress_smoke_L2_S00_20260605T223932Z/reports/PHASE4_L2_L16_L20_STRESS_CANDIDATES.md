# Phase 4 L2/L16/L20 Stress Candidate Test

Generated: 2026-06-05T22:39:41.904321+00:00
Run ID: `phase4_stress_smoke_L2_S00_20260605T223932Z`
Status: `complete`
Wall time: 0.1 min

## Scope

- Sensors: `L2`
- Seeds: `S00`
- Stress cases: `ST0_nominal`
- Position branch: `A0/U4`, P=`P4`, I=`I5`, T=`T2`
- Evaluation truth: Opti/Vicon. Same-P deltas compare each fusion row against pure UWB with the same P filter.
- Coordinate source: Phase 4 consumes the official aligned ROTO table with columns `uwb_x_mm`, `uwb_y_vertical_mm`, `uwb_z_mm`, `opti_x_mm`, `opti_y_vertical_mm`, `opti_z_mm`.
- Metric naming follows that table: `horizontal_xz` is the aligned horizontal plane and `vertical_y` is the aligned vertical axis. Do not read this as raw device XY/Z naming.

## Stress Cases

| stress_id | description | bias | noise | rw | vib | extrinsic |
| --- | --- | --- | --- | --- | --- | --- |
| ST0_nominal | control: datasheet/residual parameters unchanged | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |

## Robust Ranking

| robust_rank | experiment_short | sensor_label | p95_mean | p95_worst | sameP_delta_p95_mean | sameP_improved_fraction | horiz_xz_p95_mean | vertical_y_p95_mean | radius_abs_mean | thickness_p95_mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | X_A0_U4_P4_L2_I5_T2 | L2 MPU6050/JY61P-like | 147.3 | 147.3 | -8.4 | 0.0 | 112.2 | 101.5 | 19.5 | 125.8 |

## Best Row Per Sensor And Stress

| stress_id | experiment_short | sensor_label | p95_mean | sameP_delta_p95_mean | horiz_xz_p95_mean | vertical_y_p95_mean | radius_abs_mean | thickness_p95_mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ST0_nominal | X_A0_U4_P4_L2_I5_T2 | L2 MPU6050/JY61P-like | 147.3 | -8.4 | 112.2 | 101.5 | 19.5 | 125.8 |

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