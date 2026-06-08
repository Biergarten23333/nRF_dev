# IMU Datasheet Parameter Notes

This file records how datasheet-level IMU numbers are represented in
`configs/sensors.yaml`.

## Hard Gate

`L10-L19` are consumer/drone-grade candidates:

```text
minimum axes: 6-axis accelerometer + gyroscope
maximum chip price: 100 EUR/chip
source requirement: datasheet or distributor page linked in sensors.yaml
```

`L20+` may be calibrated industrial IMU/AHRS modules used as lab/reference
candidates. These rows are not constrained by the <=100 EUR chip-only gate, but
they still require vendor/distributor specification links and recorded
datasheet-level parameters.

## Datasheet Versus Simulation Fields

Datasheets usually provide:

```text
noise density
initial zero-g / zero-rate offset
offset drift over temperature
scale-factor tolerance
ODR and full-scale options
```

The simulation cannot directly use initial offset as final drift, because a real
system will perform at least static bias calibration before fusion. Therefore
each sensor has two classes of fields:

```text
datasheet_*:
  raw device-level values copied from datasheet/distributor documentation

simulation fields:
  post-calibration residuals used to generate synthetic IMU drift
```

The sampled-grid noise fields use the Phase 3 nominal 120 Hz grid:

```text
accel_noise_mg ~= datasheet_accel_noise_ug_sqrt_hz * sqrt(60 Hz) / 1000
gyro_noise_dps ~= datasheet_gyro_noise_mdps_sqrt_hz * sqrt(60 Hz) / 1000
```

`60 Hz` is the nominal half-rate bandwidth proxy for the 120 Hz sampled grid.
When the datasheet gives RMS noise directly, the closest RMS value may be used
instead.

## Residual Bias And Drift

`residual_accel_bias_mg` and `residual_gyro_bias_dps` are not raw datasheet
initial offsets. They model remaining bias after simple static calibration,
temperature drift, board strain, vibration, and imperfect mounting.

`accel_bias_random_walk_mg_sqrt_s` and `gyro_bias_random_walk_dps_sqrt_s`
control how that residual bias evolves during a trajectory. The values are
datasheet-informed but intentionally conservative, because MEMS datasheets do
not always publish Allan-variance random-walk terms for consumer chips.

`vibration_sensitivity_mg` and `extrinsic_mg` are board/application residuals,
not pure silicon claims. They represent the fact that drone/handheld mounting,
PCB strain, and IMU-to-body alignment can dominate raw datasheet noise.

## Active Phase 3 Sensor Set

```text
Phase 2 controls:
  L0, L1, L2, L3, L4, L5, L7, L8

Datasheet-backed consumer/drone IMUs:
  L10 InvenSense MPU-6050
  L11 InvenSense ICM-20948
  L12 InvenSense ICM-20602
  L13 InvenSense ICM-42605
  L14 InvenSense ICM-42670-P
  L15 InvenSense ICM-42688-P
  L16 InvenSense ICM-45686
  L17 Bosch BMI270
  L18 Bosch BMI088
  L19 ST LSM6DSV16X

Industrial/lab reference modules:
  L20 Xsens/Movella MTi-3-5A-T AHRS module, simulated as accel+gyro only
```

The Phase 3 runner must load sensor properties from `configs/sensors.yaml`.
Hardcoding a separate L table in a runner is not allowed for Phase 3.
