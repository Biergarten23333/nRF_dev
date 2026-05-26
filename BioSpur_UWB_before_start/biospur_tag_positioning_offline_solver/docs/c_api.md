# C API

The C core intentionally solves one frame only. File parsing and batch
processing stay in Python.

Header:

```text
c_core/include/biospur_tagpos/tagpos_solver.h
```

Core function:

```c
int biospur_tagpos_solve_frame(
    const double *anchor_xyz_mm,
    const double *ranges_mm,
    const double *anchor_delay_mm,
    const double *anchor_sigma_mm,
    const double *quality_percent,
    const double *quality_ema_percent,
    const double *residual_ema_abs_mm,
    int n,
    const double *x0_mm,
    double tag_delay_mm,
    const BiospurTagposConfig *cfg,
    double *out_xyz_mm,
    double *out_residuals_mm,
    int *out_used_mask,
    BiospurTagposResult *out_result
);
```

Input convention:

- `anchor_xyz_mm` is flattened `[x0,y0,z0, x1,y1,z1, ...]`.
- `ranges_mm` has length `n`.
- `anchor_delay_mm` has length `n`.
- `anchor_sigma_mm` has length `n`.
- `quality_percent`, `quality_ema_percent`, and `residual_ema_abs_mm` are
  optional reliability inputs.
- `x0_mm` is the previous solved position when available. T1/T2 use it only as
  the initial guess. T3/T4 also use it as a weak motion prior when the selected
  policy path enables dynamic stabilization.

Output convention:

- `out_xyz_mm` is solved tag position.
- `out_residuals_mm[i]` is residual for input anchor `i`.
- `out_used_mask[i]` is 1 for anchors used by the current default methods.
- `out_result` stores RMS, p95, max residual, used count, and rejected index.

Method IDs:

```text
1 = T1 robust WLS
2 = T2 quality-aware WLS
3 = T3 dynamic-stable WLS
4 = T4 adaptive redundancy policy
```

`T4_V6_IMU_GATE` is implemented in the Python wrapper as a policy variant over
method ID 4. The C frame solver remains intentionally IMU-agnostic: the wrapper
adjusts `temporal_prior_sigma_mm` per frame before calling the C core.

Robust loss selector:

```text
BIOSPUR_TAGPOS_LOSS_HUBER = 1
BIOSPUR_TAGPOS_LOSS_TUKEY = 2
```

The default remains Huber. Tukey support exists for experiments, but the tested
T4 default does not use Tukey because MC50 probes degraded clean keep-8 and Roto
metrics.

## IMU Summary Inputs

IMU dynamic gating is supplied through host-parsed capture data, not through the
C ABI. Future TRv4 captures should add these `tr_all.csv` columns:

```text
imu_valid
imu_n
acc_norm_mean_mg
acc_norm_std_mg
acc_norm_min_mg
acc_norm_max_mg
imu_skip_count
```

The solver consumes `imu_n`, `imu_valid`, and `acc_norm_std_mg`. If any are
missing or invalid, `T4_V6_IMU_GATE` falls back to T4 v5 behavior for that
frame.

## Raw IMU Capture Fields

The b68 Tag firmware can emit raw LIS2DH12 XYZ data in the TR trailer. These
fields are parsed and kept in the Python capture model for post-processing, but
they are not passed into the C frame solver ABI:

```text
imu_raw_valid
acc_x_mg
acc_y_mg
acc_z_mg
acc_norm_mg
imu_timestamp_ms
imu_poll_to_read_start_us
imu_poll_to_read_mid_us
imu_poll_to_read_end_us
imu_read_duration_us
```

The `imu_poll_to_read_*_us` fields are measured on the Tag from broadcast poll
TX-done to the I2C read window. They are intended for offline synchronization
checks, not for changing the per-frame C API.
