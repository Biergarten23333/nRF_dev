# Real 6-Axis IMU Vertical Slice

Generated: 2026-06-05T08:54:04.467084+00:00
Run ID: `real6axis_L15_I1I3_world_20260605T_fixed`

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
-> orientation mode: world_aligned
-> accel_x/y/z + gyro_x/y/z at ODR
-> gravity/world-frame transform
-> L15 noise/bias/random-walk/vibration/timestamp/quantization
-> I1+I3 low-pass and residual reduction
-> pure IMU dead reckoning and UWB+IMU fusion
```

Important limitation:

```text
orientation_source = world_aligned
The official B0 sample table has Opti xyz only. This smoke does not yet
use a full exported Vicon rigid-body quaternion. The default smoke uses
a world-aligned body frame so the perfect-IMU control is numerically valid.
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
| REAL6_X_A0_L0_I0_T11 | 0.8 | 1.4 | 247.8 | 8.1 | nan |
| REAL6_X_A0_L15_I1+I3_T11 | 2438.2 | 6878.2 | 7057.3 | 9244.2 | nan |
| REAL6_X_A0_U4_P0_L15_I1+I3_T5LITE | 61.0 | 88.4 | 512.2 | nan | 1.0 |

## Outputs

```text
tables/real_6axis_summary.csv
tables/real_6axis_track_metrics.csv
traces/real_6axis_samples.csv.gz
traces/real_6axis_imu_packets.csv.gz
figs/contact_sheets/real_6axis_overlay_full.png
figs/contact_sheets/real_6axis_overlay_selected.png
figs/method_sheets/full/*.png
figs/method_sheets/selected/*.png
```

One-method-per-figure PNGs:

```text
figs/method_sheets/full/01_pure_UWB__REAL6_B0_A0_U4_P0_T1.png
figs/method_sheets/full/02_perfect_IMU__REAL6_X_A0_L0_I0_T11.png
figs/method_sheets/full/03_realistic_IMU__REAL6_X_A0_L15_I1_I3_T11.png
figs/method_sheets/full/04_UWB_IMU__REAL6_X_A0_U4_P0_L15_I1_I3_T5LITE.png
figs/method_sheets/selected/01_pure_UWB__REAL6_B0_A0_U4_P0_T1.png
figs/method_sheets/selected/02_perfect_IMU__REAL6_X_A0_L0_I0_T11.png
figs/method_sheets/selected/03_realistic_IMU__REAL6_X_A0_L15_I1_I3_T11.png
figs/method_sheets/selected/04_UWB_IMU__REAL6_X_A0_U4_P0_L15_I1_I3_T5LITE.png
```

## Phase 4 Consequence

This smoke proves the simulation line can produce and consume explicit
`ax/ay/az/gx/gy/gz` packets. The official Phase 4 FULL runner still needs
the same packet interface wired into real T5/T6/T7/T8/T9/T10 implementations
and should not rely on the old drift-prior proxy.
