# FULL 4-Way ROTO Comparison

Generated 2026-06-03T16:01:54.342856+00:00.

All rows use the corrected FULL OptiTrack export and fixed capture-level offsets from the original FULL v4-io/T4 ROTO alignment. This compares spatial layout/delay/tag-solver variants, not a newly fitted time offset per variant.

## Best Overall Rows

| experiment | layout_solver | layout_variant | delay_mode | tag_method | scale_source | baseline_pair | tracks_ok | err3d_p50_track_median_mm | err3d_p95_track_median_mm | err_horizontal_xz_p95_track_median_mm | err_vertical_y_p95_track_median_mm | turn_center_abs_error_3d_track_median_mm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| one_baseline | v4-io | one_baseline_scale | solver_delay | T4 | B-C | B-C | 34 | 100.1 | 220.5 | 159.3 | 176.2 | 69.4 |
| one_baseline | v4-io | one_baseline_scale | solver_delay | T2 | B-C | B-C | 34 | 101.8 | 216.4 | 160.7 | 173.4 | 68.9 |
| one_baseline | v4-io | one_baseline_scale | solver_delay | T1 | B-C | B-C | 34 | 101.9 | 216.3 | 160.5 | 172.7 | 69.1 |
| one_baseline | v4-io | one_baseline_scale | solver_delay | T3 | B-C | B-C | 34 | 102.2 | 221.3 | 163.5 | 180.7 | 70.9 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T3 | F-H | F-H | 34 | 102.7 | 203.3 | 146.4 | 169.7 | 78.9 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T3 | E-F | E-F | 34 | 102.7 | 202.7 | 145.5 | 170.1 | 78.9 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T3 | B-C | B-C | 34 | 102.8 | 211.3 | 147.9 | 170.9 | 78.4 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | F-H | F-H | 34 | 103.0 | 204.9 | 135.7 | 173.4 | 73.6 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T2 | E-F | E-F | 34 | 103.1 | 205.5 | 148.3 | 171.2 | 75.9 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T1 | E-F | E-F | 34 | 103.2 | 205.2 | 148.6 | 170.8 | 76.0 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T1 | B-C | B-C | 34 | 103.3 | 212.7 | 152.2 | 176.9 | 77.5 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T1 | F-H | F-H | 34 | 103.3 | 205.7 | 149.5 | 172.2 | 76.4 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T2 | B-C | B-C | 34 | 103.3 | 211.8 | 152.0 | 176.5 | 77.5 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T3 | F-H | F-H | 34 | 103.3 | 201.3 | 135.7 | 173.9 | 75.9 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T2 | F-H | F-H | 34 | 103.4 | 205.6 | 149.5 | 171.6 | 76.3 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | C-F | C-F | 34 | 103.4 | 203.3 | 136.5 | 173.9 | 73.4 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | F-G | F-G | 34 | 103.4 | 201.6 | 135.2 | 173.8 | 73.1 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | D-F | D-F | 34 | 103.5 | 201.6 | 135.5 | 173.4 | 73.0 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T2 | F-H | F-H | 34 | 103.5 | 203.8 | 137.9 | 170.6 | 72.9 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | B-F | B-F | 34 | 103.5 | 203.2 | 136.4 | 173.6 | 73.3 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | B-C | B-C | 34 | 103.5 | 201.6 | 135.5 | 173.3 | 73.0 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T1 | F-H | F-H | 34 | 103.5 | 205.1 | 138.8 | 171.7 | 72.9 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | E-F | E-F | 34 | 103.6 | 202.3 | 135.2 | 173.7 | 73.2 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | B-C | B-C | 34 | 103.7 | 216.7 | 147.9 | 173.8 | 78.3 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | F-H | F-H | 34 | 103.9 | 206.5 | 147.1 | 171.8 | 77.0 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T2 | F-G | F-G | 34 | 104.1 | 204.2 | 137.8 | 170.2 | 71.7 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | C-G | C-G | 34 | 104.1 | 201.5 | 134.6 | 172.6 | 72.9 |
| one_baseline | v2 | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T3 | F-H | F-H | 34 | 104.1 | 206.6 | 146.1 | 175.2 | 80.2 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T2 | C-F | C-F | 34 | 104.1 | 205.1 | 137.8 | 169.4 | 72.4 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T3 | C-F | C-F | 34 | 104.1 | 201.8 | 144.2 | 170.9 | 78.6 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T1 | C-F | C-F | 34 | 104.2 | 205.4 | 138.6 | 170.3 | 72.5 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T1 | E-F | E-F | 34 | 104.2 | 204.4 | 137.9 | 170.5 | 71.7 |
| one_baseline | v3-lite | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T3 | F-H | F-H | 34 | 104.2 | 206.4 | 146.3 | 175.2 | 80.2 |
| one_baseline | v3-lite | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T1 | F-H | F-H | 34 | 104.2 | 208.9 | 150.3 | 178.0 | 77.1 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T1 | F-G | F-G | 34 | 104.2 | 204.7 | 137.6 | 170.8 | 71.7 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T2 | E-F | E-F | 34 | 104.2 | 204.3 | 137.7 | 169.9 | 71.8 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T3 | C-F | C-F | 34 | 104.2 | 200.7 | 135.8 | 173.6 | 76.1 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T3 | D-E | D-E | 34 | 104.3 | 200.5 | 137.1 | 172.2 | 76.7 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | E-F | E-F | 34 | 104.3 | 205.4 | 144.7 | 171.8 | 76.7 |
| one_baseline | v2 | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T1 | F-H | F-H | 34 | 104.3 | 209.1 | 150.0 | 178.0 | 77.0 |

## Best Row Per Experiment

| experiment | layout_solver | layout_variant | delay_mode | tag_method | scale_source | baseline_pair | tracks_ok | err3d_p50_track_median_mm | err3d_p95_track_median_mm | err_horizontal_xz_p95_track_median_mm | err_vertical_y_p95_track_median_mm | turn_center_abs_error_3d_track_median_mm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| one_baseline | v4-io | one_baseline_scale | solver_delay | T4 | B-C | B-C | 34 | 100.1 | 220.5 | 159.3 | 176.2 | 69.4 |
| align_to_vicon | v1-old | vicon_truth | vicon_inter_anchor_delaycal | T4 |  |  | 34 | 105.6 | 200.4 | 137.2 | 172.0 | 69.8 |
| scale_to_vicon | v1-old | vicon_truth | vicon_inter_anchor_delaycal | T4 | none_truth_anchor |  | 34 | 105.6 | 200.4 | 137.2 | 172.0 | 69.8 |

## ROTO Post-Solve Filtered Replay

This report was generated before the newer filtered replay. These rows are post-solve trajectory filters on the already solved ROTO samples. They do not change the range solver, layout, residual delay corrections, tag solver, or capture-level time offset. `F4` is bounded fixed-lag smoothing with latency; `F5` is offline and uses future samples.

| case | filter | deployability | track P50 3D | track P95 3D | sample RMSE 3D | center RMS 3D | dR RMS | verdict |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| FULL original v4-io/T4 | F0 | baseline | 105.8 | 231.8 | 141.3 | 72.1 | 25.9 | BASELINE_UNFILTERED |
| FULL original v4-io/T4 | F4 | fixed_lag | 86.3 | 158.2 | 103.1 | 69.1 | 24.5 | FILTER_HELPS |
| FULL original v4-io/T4 | F5 | offline_upper_bound | 83.3 | 148.6 | 98.9 | 68.3 | 24.6 | OFFLINE_FILTER_HELPS_DIAGNOSTIC_ONLY |
| Vicon anchors + delaycal / T4 | F0 | baseline | 105.6 | 200.4 | 125.4 | 72.7 | 18.0 | BASELINE_UNFILTERED |
| Vicon anchors + delaycal / T4 | F4 | fixed_lag | 84.0 | 145.4 | 95.1 | 70.5 | 17.7 | FILTER_HELPS |
| Vicon anchors + delaycal / T4 | F5 | offline_upper_bound | 82.7 | 139.1 | 91.2 | 70.0 | 18.0 | OFFLINE_FILTER_HELPS_DIAGNOSTIC_ONLY |

Filtered interpretation:

- Fixed-lag smoothing helps ROTO: original FULL improves from **105.8 / 231.8 mm** to **86.3 / 158.2 mm**.
- Offline `F5` is better, **83.3 / 148.6 mm**, but it is diagnostic only because it uses future samples.
- This is a trajectory-output/latency result, not evidence that the underlying calibration or range solver became better.

## ROTO Lever-Armed Pseudo-IMU Replay

This is the OptiTrack-derived oracle test. It fits wand-body motion from non-antenna markers, estimates the body-to-UWB-antenna lever arm against `WandBantenna`/`WandCantenna`, and applies an antenna-point relative-motion prior. It is not a real IMU deployment result yet.

| case | fusion | deployability | track P50 3D | track P95 3D | sample RMSE 3D | center RMS 3D | dR RMS | verdict |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| FULL original v4-io/T4 | PI0 | baseline | 105.8 | 231.8 | 141.3 | 72.1 | 25.9 | BASELINE_UNFILTERED |
| FULL original v4-io/T4 | PI1 | online_oracle | 66.1 | 97.5 | 71.6 | 60.4 | 12.3 | PSEUDO_IMU_HELPS |
| FULL original v4-io/T4 | PI4 | offline_upper_bound | 58.7 | 81.5 | 61.8 | 57.6 | 5.3 | PSEUDO_IMU_HELPS_DIAGNOSTIC_ONLY |
| Vicon anchors + delaycal / T4 | PI1 | online_oracle | 64.0 | 100.2 | 69.8 | 63.3 | 11.7 | PSEUDO_IMU_HELPS |
| Vicon anchors + delaycal / T4 | PI4 | offline_upper_bound | 59.9 | 82.0 | 63.1 | 60.5 | 4.3 | PSEUDO_IMU_HELPS_DIAGNOSTIC_ONLY |

Pseudo-IMU interpretation:

- A correctly lever-armed motion prior has much stronger leverage than generic smoothing: original FULL drops to **66.1 / 97.5 mm** under the causal oracle prior.
- Because the prior comes from OptiTrack, this is an upper-bound experiment. A real result requires real IMU data, IMU-to-antenna extrinsic calibration, and raw-range fusion validation.
- The full 4x FULL matrix is in `roto_pseudo_imu/tables/roto_pseudo_imu_summary.csv`.
