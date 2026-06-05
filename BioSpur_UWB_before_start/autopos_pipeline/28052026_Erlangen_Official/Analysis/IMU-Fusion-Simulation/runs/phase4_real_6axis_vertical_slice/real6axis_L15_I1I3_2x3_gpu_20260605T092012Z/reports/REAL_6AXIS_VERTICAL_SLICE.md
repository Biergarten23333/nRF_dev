# Real 6-Axis IMU Vertical Slice

Generated: 2026-06-05T09:27:55.748388+00:00
Run ID: `real6axis_L15_I1I3_2x3_gpu_20260605T092012Z`

## Purpose

This run is the first explicit simulated 6-axis IMU packet smoke test.
It is not the older position-domain drift-prior proxy.

## Chosen Solver Matrix

```text
A0 + U4/P0 + L15 + I1+I3

forward solver:
  T5LITE_UWB2IMU  = UWB corrects IMU
  T2LITE_IMU2UWB  = IMU corrects UWB
  T3LITE_BIDIR    = causal bidirectional-lite

session solver:
  T9LITE_UWB2IMU  = full-session UWB corrects IMU
  T9LITE_IMU2UWB  = full-session IMU corrects UWB
  T10LITE_BIDIR   = full-session bidirectional-lite
```

Rationale:

- `L15` = InvenSense ICM-42688-P high-precision consumer/drone 6-axis IMU.
- `I1+I3` = low-pass + residual bias/random-walk model.
- Forward rows are causal-position-domain vertical slices.
- Session rows are torch full-session position-domain objectives; they are GPU-capable and should be run with `cuda:0 cuda:1` when available.

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

## Summary

| experiment_id | information_use | coupling_mode | solver_family | gpu_backend | trackmedian_err3d_p50_mm | trackmedian_err3d_p95_mm | legacy_deltaR_error_rms_mm | imu_only_endpoint_drift_3d_trackmedian_mm | uwb_update_accept_rate_trackmedian |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| REAL6_B0_A0_U4_P0_T1 | causal-forward | uwb_only_control | T1 | nan | 105.8 | 231.8 | 80.1 | nan | nan |
| REAL6_X_A0_L0_I0_T11 | causal-forward | imu_only_diagnostic | T11 | nan | 0.8 | 1.4 | 247.8 | 8.1 | nan |
| REAL6_X_A0_L15_I1+I3_T11 | causal-forward | imu_only_diagnostic | T11 | nan | 2970.0 | 7462.2 | 4888.3 | 8990.3 | nan |
| REAL6_X_A0_U4_P0_L15_I1+I3_T2LITE_IMU2UWB | causal-forward | imu_corrects_uwb | T2LITE | nan | 85.2 | 165.2 | 245.7 | nan | nan |
| REAL6_X_A0_U4_P0_L15_I1+I3_T3LITE_BIDIR | causal-forward | bidirectional_joint | T3LITE | nan | 61.8 | 100.9 | 411.9 | nan | 1.0 |
| REAL6_X_A0_U4_P0_L15_I1+I3_T5LITE | causal-forward | uwb_corrects_imu | T5LITE | nan | 60.1 | 86.0 | 455.8 | nan | 1.0 |
| REAL6_X_A0_U4_P0_L15_I1+I3_T10LITE_BIDIR | full-session | bidirectional_joint | T10LITE | cuda:0+cuda:1 | 97.2 | 212.7 | 147.6 | nan | nan |
| REAL6_X_A0_U4_P0_L15_I1+I3_T9LITE_IMU2UWB | full-session | imu_corrects_uwb | T9LITE | cuda:0+cuda:1 | 97.1 | 212.7 | 147.2 | nan | nan |
| REAL6_X_A0_U4_P0_L15_I1+I3_T9LITE_UWB2IMU | full-session | uwb_corrects_imu | T9LITE | cuda:0+cuda:1 | 98.7 | 215.0 | 127.1 | nan | nan |

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
figs/triad_sheets/opti_vs_pure_uwb_vs_uwb_imu_full.png
figs/triad_sheets/opti_vs_pure_uwb_vs_uwb_imu_selected.png
```

One-method-per-figure PNGs:

```text
figs/method_sheets/full/01_pure_UWB__REAL6_B0_A0_U4_P0_T1.png
figs/method_sheets/full/02_perfect_IMU__REAL6_X_A0_L0_I0_T11.png
figs/method_sheets/full/03_realistic_IMU__REAL6_X_A0_L15_I1_I3_T11.png
figs/method_sheets/full/04_Fwd_IMU-_UWB__REAL6_X_A0_U4_P0_L15_I1_I3_T2LITE_IMU2UWB.png
figs/method_sheets/full/05_Fwd_Bidir__REAL6_X_A0_U4_P0_L15_I1_I3_T3LITE_BIDIR.png
figs/method_sheets/full/06_Fwd_UWB-_IMU__REAL6_X_A0_U4_P0_L15_I1_I3_T5LITE.png
figs/method_sheets/full/07_Sess_UWB-_IMU__REAL6_X_A0_U4_P0_L15_I1_I3_T9LITE_UWB2IMU.png
figs/method_sheets/full/08_Sess_IMU-_UWB__REAL6_X_A0_U4_P0_L15_I1_I3_T9LITE_IMU2UWB.png
figs/method_sheets/full/09_Sess_Bidir__REAL6_X_A0_U4_P0_L15_I1_I3_T10LITE_BIDIR.png
figs/method_sheets/selected/01_pure_UWB__REAL6_B0_A0_U4_P0_T1.png
figs/method_sheets/selected/02_perfect_IMU__REAL6_X_A0_L0_I0_T11.png
figs/method_sheets/selected/03_realistic_IMU__REAL6_X_A0_L15_I1_I3_T11.png
figs/method_sheets/selected/04_Fwd_IMU-_UWB__REAL6_X_A0_U4_P0_L15_I1_I3_T2LITE_IMU2UWB.png
figs/method_sheets/selected/05_Fwd_Bidir__REAL6_X_A0_U4_P0_L15_I1_I3_T3LITE_BIDIR.png
figs/method_sheets/selected/06_Fwd_UWB-_IMU__REAL6_X_A0_U4_P0_L15_I1_I3_T5LITE.png
figs/method_sheets/selected/07_Sess_UWB-_IMU__REAL6_X_A0_U4_P0_L15_I1_I3_T9LITE_UWB2IMU.png
figs/method_sheets/selected/08_Sess_IMU-_UWB__REAL6_X_A0_U4_P0_L15_I1_I3_T9LITE_IMU2UWB.png
figs/method_sheets/selected/09_Sess_Bidir__REAL6_X_A0_U4_P0_L15_I1_I3_T10LITE_BIDIR.png
```

Opti + pure UWB + UWB+IMU comparison PNGs:

```text
figs/triad_sheets/opti_vs_pure_uwb_vs_uwb_imu_full.png
figs/triad_sheets/opti_vs_pure_uwb_vs_uwb_imu_selected.png
```

## Phase 4 Consequence

This smoke proves the simulation line can produce and consume explicit
`ax/ay/az/gx/gy/gz` packets. The official Phase 4 FULL runner still needs
the same packet interface wired into real T5/T6/T7/T8/T9/T10 implementations
and should not rely on the old drift-prior proxy.
