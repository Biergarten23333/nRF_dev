# ROTO Pseudo-IMU Replay

Generated 2026-06-04T10:20:18.903072+00:00.

This is an oracle diagnostic: OptiTrack markers provide the pseudo-IMU relative-motion prior. The wand body pose is fitted from non-antenna markers, then the UWB antenna point is recovered through a fitted body-to-antenna lever arm.

## Fusion Definitions

- `PI0`: unfiltered solved UWB antenna positions; deployability=`baseline`.
- `PI1`: causal filter with strong OptiTrack-derived antenna relative-motion prior; deployability=`online_oracle`.
- `PI2`: causal filter with balanced OptiTrack-derived antenna relative-motion prior; deployability=`online_oracle`.
- `PI3`: bounded-lag smoother over PI1 causal pseudo-IMU trajectory; deployability=`fixed_lag_oracle`.
- `PI4`: full-sequence RTS smoother with strong pseudo-IMU prior; uses future samples; deployability=`offline_upper_bound`.
- `PI5`: full-sequence RTS smoother with balanced pseudo-IMU prior; uses future samples; deployability=`offline_upper_bound`.

## Lever-Arm Sanity

Across capture/tag tracks, body-fit antenna residual medians are 0.6 mm P50-of-P50 and 1.5 mm P50-of-P95. This validates that the pseudo-IMU prior is applied to the antenna point, not to the marker-body centroid.

## Summary

| case_label | fusion_id | fusion_deployability | trackmedian_err3d_p50_mm | trackmedian_err3d_p95_mm | sample_err3d_rmse_mm | turn_center_abs_error_3d_rms_mm | legacy_deltaR_error_rms_mm | improvement_vs_PI0_trackmedian_err3d_p50_mm | fusion_verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FULL original v4-io/T4 | PI0 | baseline | 105.84 | 231.80 | 141.27 | 72.08 | 25.90 | 0.00 | BASELINE_UNFILTERED |
| FULL original v4-io/T4 | PI1 | online_oracle | 66.10 | 97.52 | 71.63 | 60.40 | 12.31 | 39.74 | PSEUDO_IMU_HELPS |
| FULL original v4-io/T4 | PI2 | online_oracle | 84.68 | 153.18 | 98.89 | 67.64 | 23.18 | 21.16 | PSEUDO_IMU_HELPS |
| FULL original v4-io/T4 | PI3 | fixed_lag_oracle | 63.29 | 102.52 | 70.71 | 60.61 | 17.10 | 42.55 | PSEUDO_IMU_HELPS |
| FULL original v4-io/T4 | PI4 | offline_upper_bound | 58.67 | 81.52 | 61.76 | 57.64 | 5.35 | 47.17 | PSEUDO_IMU_HELPS_DIAGNOSTIC_ONLY |
| FULL original v4-io/T4 | PI5 | offline_upper_bound | 75.25 | 127.73 | 88.14 | 64.56 | 19.71 | 30.59 | PSEUDO_IMU_HELPS_DIAGNOSTIC_ONLY |
| One-baseline E-H + delaycal / v4-io/T4 | PI0 | baseline | 106.21 | 200.39 | 126.69 | 77.08 | 13.36 | 0.00 | BASELINE_UNFILTERED |
| One-baseline E-H + delaycal / v4-io/T4 | PI1 | online_oracle | 70.16 | 104.60 | 73.74 | 68.15 | 10.36 | 36.06 | PSEUDO_IMU_HELPS |
| One-baseline E-H + delaycal / v4-io/T4 | PI2 | online_oracle | 85.50 | 139.11 | 92.84 | 74.30 | 14.63 | 20.71 | PSEUDO_IMU_HELPS |
| One-baseline E-H + delaycal / v4-io/T4 | PI3 | fixed_lag_oracle | 72.98 | 108.29 | 77.44 | 67.96 | 14.49 | 33.24 | PSEUDO_IMU_HELPS |
| One-baseline E-H + delaycal / v4-io/T4 | PI4 | offline_upper_bound | 65.67 | 88.91 | 67.68 | 65.54 | 3.64 | 40.54 | PSEUDO_IMU_HELPS_DIAGNOSTIC_ONLY |
| One-baseline E-H + delaycal / v4-io/T4 | PI5 | offline_upper_bound | 79.22 | 123.32 | 84.56 | 71.12 | 10.99 | 27.00 | PSEUDO_IMU_HELPS_DIAGNOSTIC_ONLY |
| Full similarity scale + delaycal / v4-io/T4 | PI0 | baseline | 110.50 | 200.71 | 127.76 | 76.66 | 15.61 | 0.00 | BASELINE_UNFILTERED |
| Full similarity scale + delaycal / v4-io/T4 | PI1 | online_oracle | 69.68 | 104.01 | 74.39 | 67.70 | 11.28 | 40.83 | PSEUDO_IMU_HELPS |
| Full similarity scale + delaycal / v4-io/T4 | PI2 | online_oracle | 86.22 | 142.11 | 94.54 | 73.83 | 16.84 | 24.28 | PSEUDO_IMU_HELPS |
| Full similarity scale + delaycal / v4-io/T4 | PI3 | fixed_lag_oracle | 71.34 | 113.05 | 79.29 | 67.38 | 15.74 | 39.16 | PSEUDO_IMU_HELPS |
| Full similarity scale + delaycal / v4-io/T4 | PI4 | offline_upper_bound | 65.16 | 86.83 | 67.68 | 65.08 | 3.94 | 45.35 | PSEUDO_IMU_HELPS_DIAGNOSTIC_ONLY |
| Full similarity scale + delaycal / v4-io/T4 | PI5 | offline_upper_bound | 80.14 | 126.81 | 86.14 | 70.56 | 13.08 | 30.37 | PSEUDO_IMU_HELPS_DIAGNOSTIC_ONLY |
| Vicon anchors + delaycal / T4 | PI0 | baseline | 105.59 | 200.43 | 125.40 | 72.73 | 17.99 | 0.00 | BASELINE_UNFILTERED |
| Vicon anchors + delaycal / T4 | PI1 | online_oracle | 63.99 | 100.23 | 69.85 | 63.30 | 11.66 | 41.60 | PSEUDO_IMU_HELPS |
| Vicon anchors + delaycal / T4 | PI2 | online_oracle | 82.00 | 135.87 | 90.50 | 69.69 | 18.91 | 23.58 | PSEUDO_IMU_HELPS |
| Vicon anchors + delaycal / T4 | PI3 | fixed_lag_oracle | 66.11 | 107.69 | 75.04 | 62.78 | 16.22 | 39.47 | PSEUDO_IMU_HELPS |
| Vicon anchors + delaycal / T4 | PI4 | offline_upper_bound | 59.90 | 81.99 | 63.07 | 60.48 | 4.34 | 45.68 | PSEUDO_IMU_HELPS_DIAGNOSTIC_ONLY |
| Vicon anchors + delaycal / T4 | PI5 | offline_upper_bound | 74.94 | 122.48 | 81.67 | 66.20 | 14.87 | 30.64 | PSEUDO_IMU_HELPS_DIAGNOSTIC_ONLY |

## Interpretation

Because the motion prior is derived from OptiTrack, PI1/PI3/PI4 are not deployable accuracy claims. They bound how much a correctly lever-armed inertial relative-motion source could help after the existing UWB position solve.

## Output Tables

- `../tables/roto_pseudo_imu_summary.csv`
- `../tables/roto_pseudo_imu_per_track.csv`
- `../tables/roto_pseudo_imu_extrinsics.csv`
