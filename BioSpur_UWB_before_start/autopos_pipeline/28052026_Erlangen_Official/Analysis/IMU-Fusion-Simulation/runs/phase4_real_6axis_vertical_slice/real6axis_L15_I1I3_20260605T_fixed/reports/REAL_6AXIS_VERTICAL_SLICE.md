# Real 6-Axis IMU Vertical Slice

Generated: 2026-06-05T08:37:57.808620+00:00
Run ID: `real6axis_L15_I1I3_20260605T_fixed`

## Purpose

This run is the first explicit simulated 6-axis IMU packet smoke test.
It is not the older position-domain drift-prior proxy.

## Chosen Combination

```text
A0 + U4/P0 + L15 + I1+I3 + T5LITE
```

Rationale:

- `L15` = InvenSense ICM-42688-P high-precision consumer/drone 6-axis IMU.
- `I1+I3` = low-pass + residual bias/random-walk model.
- `T5LITE` = causal position/velocity Kalman update driven by simulated accel/gyro packet plus UWB position updates.

## IMU Packet Model

```text
Opti/Vicon xyz trajectory
-> trajectory-derived yaw fallback
-> accel_x/y/z + gyro_x/y/z at ODR
-> gravity/world-frame transform
-> L15 noise/bias/random-walk/vibration/timestamp/quantization
-> I1+I3 low-pass and residual reduction
-> pure IMU dead reckoning and UWB+IMU fusion
```

Important limitation:

```text
orientation_source = trajectory_yaw_fallback
The official B0 sample table has Opti xyz only. This smoke does not yet use
a full exported Vicon rigid-body quaternion. Roll/pitch are assumed zero.
```

Sensor fields used:

```text
name = invensense_icm42688p_high_precision_6axis
accel_noise_mg = 0.54
gyro_noise_dps = 0.022
residual_accel_bias_mg = 0.075
residual_gyro_bias_dps = 0.008
accel_bias_random_walk_mg_sqrt_s = 0.012
gyro_bias_random_walk_dps_sqrt_s = 0.001
timestamp_jitter_ms = 0.35
quantization_mg = 0.061
vibration_sensitivity_mg = 0.1
extrinsic_mg = 0.02
```

## Rows

```text
REAL6_B0_A0_U4_P0_T1                 pure UWB
REAL6_X_A0_L0_I0_T11                 perfect IMU-only no-drift diagnostic
REAL6_X_A0_L15_I1+I3_T11             realistic simulated 6-axis IMU-only
REAL6_X_A0_U4_P0_L15_I1+I3_T5LITE    causal UWB+IMU fusion smoke
```

## Summary

| experiment_id | trackmedian_err3d_p50_mm | trackmedian_err3d_p95_mm | legacy_deltaR_error_rms_mm | imu_only_endpoint_drift_3d_trackmedian_mm | uwb_update_accept_rate_trackmedian |
| --- | --- | --- | --- | --- | --- |
| REAL6_B0_A0_U4_P0_T1 | 105.8 | 231.8 | 80.1 | nan | nan |
| REAL6_X_A0_L0_I0_T11 | 100936.0 | 810248.8 | 18763616.7 | 952686.9 | nan |
| REAL6_X_A0_L15_I1+I3_T11 | 100772.6 | 809066.1 | 18137603.5 | 956494.8 | nan |
| REAL6_X_A0_U4_P0_L15_I1+I3_T5LITE | 89501.9 | 803063.7 | 18952902.9 | nan | 1.0 |

## Outputs

```text
tables/real_6axis_summary.csv
tables/real_6axis_track_metrics.csv
traces/real_6axis_samples.csv.gz
traces/real_6axis_imu_packets.csv.gz
figs/contact_sheets/real_6axis_overlay_full.png
figs/contact_sheets/real_6axis_overlay_selected.png
```

## Phase 4 Consequence

This smoke proves the simulation line can produce and consume explicit
`ax/ay/az/gx/gy/gz` packets. The official Phase 4 FULL runner still needs
the same packet interface wired into real T5/T6/T7/T8/T9/T10 implementations
and should not rely on the old drift-prior proxy.
