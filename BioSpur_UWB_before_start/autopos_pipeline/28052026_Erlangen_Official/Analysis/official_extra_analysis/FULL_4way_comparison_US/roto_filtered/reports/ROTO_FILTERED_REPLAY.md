# ROTO Filtered Replay

Generated 2026-06-04T14:28:41.303857+00:00.

These are post-solve trajectory filters applied to already solved ROTO v4-io/T4 samples. They keep layout, residual delay correction, tag solver, and capture-level time alignment fixed.

## Filter Definitions

- `F0`: unfiltered solved ROTO samples; deployability=`baseline`.
- `F1`: constant-velocity Kalman filter; deployability=`online`.
- `F2`: constant-velocity Kalman filter with innovation down-weighting; deployability=`online`.
- `F3`: adaptive-acceleration constant-velocity Kalman filter; deployability=`online`.
- `F4`: bounded-latency fixed-lag smoother over the F2 trajectory; deployability=`fixed_lag`.
- `F5`: full-sequence RTS smoother; uses future samples; deployability=`offline_upper_bound`.

## Summary

| case_label | filter_id | filter_deployability | trackmedian_err3d_p50_mm | trackmedian_err3d_p95_mm | sample_err3d_rmse_mm | turn_center_abs_error_3d_rms_mm | legacy_deltaR_error_rms_mm | improvement_vs_F0_trackmedian_err3d_p50_mm | filter_verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FULL original v4-io/T4 | F5 | offline_upper_bound | 83.3 | 148.6 | 98.9 | 68.3 | 24.6 | 22.5 | OFFLINE_FILTER_HELPS_DIAGNOSTIC_ONLY |
| FULL original v4-io/T4 | F4 | fixed_lag | 86.3 | 158.2 | 103.1 | 69.1 | 24.5 | 19.5 | FILTER_HELPS |
| FULL original v4-io/T4 | F0 | baseline | 105.8 | 231.8 | 141.3 | 72.1 | 25.9 | 0.0 | BASELINE_UNFILTERED |
| FULL original v4-io/T4 | F3 | online | 111.1 | 213.4 | 133.0 | 72.3 | 21.3 | -5.2 | FILTER_HURTS |
| FULL original v4-io/T4 | F1 | online | 116.0 | 219.0 | 137.5 | 74.8 | 20.3 | -10.2 | FILTER_HURTS |
| FULL original v4-io/T4 | F2 | online | 116.8 | 218.8 | 136.1 | 74.0 | 20.4 | -10.9 | FILTER_HURTS |
| One-baseline E-H + delaycal / v4-io/T4 | F5 | offline_upper_bound | 86.3 | 141.7 | 92.6 | 74.3 | 13.0 | 19.9 | OFFLINE_FILTER_HELPS_DIAGNOSTIC_ONLY |
| One-baseline E-H + delaycal / v4-io/T4 | F4 | fixed_lag | 87.7 | 146.0 | 96.4 | 74.9 | 12.7 | 18.5 | FILTER_HELPS |
| One-baseline E-H + delaycal / v4-io/T4 | F3 | online | 102.0 | 184.2 | 117.8 | 77.4 | 10.1 | 4.2 | FILTER_NEUTRAL |
| One-baseline E-H + delaycal / v4-io/T4 | F1 | online | 102.5 | 187.4 | 121.1 | 79.4 | 11.2 | 3.7 | FILTER_NEUTRAL |
| One-baseline E-H + delaycal / v4-io/T4 | F2 | online | 102.6 | 185.3 | 119.4 | 78.7 | 9.7 | 3.6 | FILTER_NEUTRAL |
| One-baseline E-H + delaycal / v4-io/T4 | F0 | baseline | 106.2 | 200.4 | 126.7 | 77.1 | 13.4 | 0.0 | BASELINE_UNFILTERED |
| Full similarity scale + delaycal / v4-io/T4 | F5 | offline_upper_bound | 87.7 | 146.2 | 95.3 | 74.1 | 15.6 | 22.8 | OFFLINE_FILTER_HELPS_DIAGNOSTIC_ONLY |
| Full similarity scale + delaycal / v4-io/T4 | F4 | fixed_lag | 88.8 | 151.2 | 99.2 | 74.7 | 15.3 | 21.7 | FILTER_HELPS |
| Full similarity scale + delaycal / v4-io/T4 | F1 | online | 103.3 | 182.6 | 120.9 | 79.0 | 12.8 | 7.2 | FILTER_HELPS |
| Full similarity scale + delaycal / v4-io/T4 | F2 | online | 103.4 | 180.6 | 119.7 | 78.5 | 12.0 | 7.1 | FILTER_HELPS |
| Full similarity scale + delaycal / v4-io/T4 | F3 | online | 103.9 | 179.7 | 118.5 | 77.1 | 13.0 | 6.6 | FILTER_HELPS |
| Full similarity scale + delaycal / v4-io/T4 | F0 | baseline | 110.5 | 200.7 | 127.8 | 76.7 | 15.6 | 0.0 | BASELINE_UNFILTERED |
| Vicon anchors + delaycal / T4 | F5 | offline_upper_bound | 82.7 | 139.1 | 91.2 | 70.0 | 18.0 | 22.9 | OFFLINE_FILTER_HELPS_DIAGNOSTIC_ONLY |
| Vicon anchors + delaycal / T4 | F4 | fixed_lag | 84.0 | 145.4 | 95.1 | 70.5 | 17.7 | 21.6 | FILTER_HELPS |
| Vicon anchors + delaycal / T4 | F3 | online | 98.9 | 181.4 | 115.4 | 73.2 | 15.6 | 6.7 | FILTER_HELPS |
| Vicon anchors + delaycal / T4 | F1 | online | 99.5 | 181.3 | 117.8 | 75.2 | 14.9 | 6.1 | FILTER_HELPS |
| Vicon anchors + delaycal / T4 | F2 | online | 99.5 | 181.3 | 116.5 | 74.6 | 14.5 | 6.1 | FILTER_HELPS |
| Vicon anchors + delaycal / T4 | F0 | baseline | 105.6 | 200.4 | 125.4 | 72.7 | 18.0 | 0.0 | BASELINE_UNFILTERED |

## Interpretation

- Best deployable/fixed-lag filtered row by track-median 3D P50: `Vicon anchors + delaycal / T4` `F4` at 84.0 / 145.4 mm.
- Best offline upper bound: `Vicon anchors + delaycal / T4` `F5` at 82.7 / 139.1 mm.
- F5 uses future samples and is diagnostic only. F4 adds bounded output latency. F1-F3 are online post-solve filters.

## Output Tables

- `../tables/roto_filtered_summary.csv`
- `../tables/roto_filtered_per_track.csv`
