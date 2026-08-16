# Fusion v1 continuation report

## Verdict

`REFERENCE_ESTIMATOR_INVALID`

Validated acquisition infrastructure has been integrated, but no fitted
articulated estimator exists. “Partially validated estimator” would therefore
overstate the evidence.

## Reuse and timing

The exact raw, canonical, Listener, timing-result and typed-ledger hashes match
their recorded identities. V4-io geometry and capture binding are reused.
Historical Q1 and T4 products are diagnostic-only. Historical body models,
calibrations, trajectories, D0/R-series and D0B-R2 artifacts are rejected.
Exact classifications are in `REUSE_AUDIT.json`.

All ten prior affine coefficients were loaded without refitting and reproduce
the acquisition ledger with zero-nanosecond maximum difference. Timing retains
`TIME_ALIGNMENT_PASS`: worst P95 280.852 us and maximum 408.279 us. The common
time sidecar has 7,295,015 IMU and 2,271,712 individual-range rows, including
status for rows outside the accepted formal clock domain.

## Sensor evidence

Hardware conversion uses 2048 LSB/g, 16.384 LSB/(degree/s), and 9.80665 m/s².
All ten nodes have separate low-motion statistics. All 80 UWB pairs have
separate descriptive statistics; robust static spreads range from 17.8 to
604.9 mm. These statistics are not ground-truth range biases.

## Human model and estimator

Clean pelvis-rooted FK and non-robotic model rules are implemented/documented.
Subject geometry, sensor extrinsics, functional axes, static fused
initialization and nonlinear smoothing are not fitted. Contact is disabled.

## Validation

Eleven software/infrastructure tests pass. Required scientific validation has
not run. Held-out golf and boxing remain unopened by new scientific code.

## Repository

Starting HEAD `412233adcb0a5a8551f2a5d1085c79b8c2c26ae5` on
`feature/b306-bringup`. Only new `fusion_v1` and
`logs/fusion_v1_reference_20260816_130000` files were added. Historical
estimators and logs were not modified. No commit or push was performed.

