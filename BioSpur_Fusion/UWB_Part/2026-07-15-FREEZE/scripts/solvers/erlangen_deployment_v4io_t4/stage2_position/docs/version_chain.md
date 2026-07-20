# Version Chain For IMU-Assisted Tag Positioning

This document fixes the naming boundary for the planned IMU-assisted dynamic
positioning path.

## Current Frozen Baseline

```text
Tag firmware: b62 frozen no-IMU-output baseline
TR format:    TRv3 range-only broadcast summary
Host parser:  parser_v1 range-only compatible parser
Solver:       T4 v5 dynamic candidate
```

T4 v5 behavior:

```text
n >= 8 anchors: memory-free T1 path
n < 8 anchors: T3-style quality/residual EMA + weak previous-position prior
```

## Planned IMU Path

```text
Tag firmware: b63 imu-summary experimental Tag image
TR format:    TRv4 range + IMU summary
Host parser:  parser_v2_imu
Solver:       T4_V6_IMU_GATE
```

TRv4 should keep the old TRv3 range fields and append a compact IMU summary
trailer:

```text
;I,imu_n,acc_norm_mean_mg,acc_norm_std_mg,acc_norm_min_mg,acc_norm_max_mg,imu_skip_count
```

Example:

```text
TR;4;1847;f;0;ff;ff;1773,2144,1770,1438,1666,2084,1768,1413;1773,2144,1770,1438,1666,2084,1768,1413;100,100,100,100,100,100,100,53;OOOOOOOO;96;1200;8200;1;I,8,1004,37,951,1068,1
```

The host parser writes the trailer into `tr_all.csv`:

```text
imu_valid
imu_n
acc_norm_mean_mg
acc_norm_std_mg
acc_norm_min_mg
acc_norm_max_mg
imu_skip_count
```

The offline solver only consumes:

```text
imu_valid
imu_n
acc_norm_std_mg
```

## T4_V6_IMU_GATE Policy

T4_V6_IMU_GATE is backward compatible with T4 v5. It uses the exact T4 v5 path
when IMU data is missing or invalid.

```text
n >= 8 anchors:
  use memory-free T1 path, no IMU prior

n < 8 anchors and valid IMU summary:
  scale the T3-style previous-position prior by std(|a|)

n < 8 anchors and missing/invalid IMU summary:
  fall back to T4 v5 low-redundancy behavior
```

The prior gate is:

```text
sigma_acc_mps2 = acc_norm_std_mg * 0.00980665
prior_scale = exp(-ln(2) * sigma_acc_mps2 / 0.5)
sigma_prior_used = sigma_prior_base / sqrt(prior_scale)
```

Thus `sigma_acc ~= 0.5 m/s^2` halves the prior weight.

## Do Not Rename These Casually

- `T4` means the current T4 v5 implementation in code.
- `T4_V6_IMU_GATE` means the IMU-assisted policy variant.
- `TRv4` means range fields plus compact IMU summary.
- `parser_v2_imu` means the host parser can read TRv4 and still accepts old
  TRv1/TRv2/TRv3.
