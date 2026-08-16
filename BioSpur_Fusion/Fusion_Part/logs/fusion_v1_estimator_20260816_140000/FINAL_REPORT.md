# Final report

## Verdict

`ESTIMATOR_IMPLEMENTED_BUT_SCIENTIFICALLY_INVALID`

The estimator is real, nonlinear, world-referenced and articulated; it converged on A/B/C, walk and final still, and never stretches a segment. It nonetheless fails the scientific bar for the reasons in `FINAL_RESULT.json`. Numerical convergence is not treated as validation.

## Implementation and inputs

New clean modules implement residuals, asynchronous interpolation, Q1-driven articulated FK, subject calibration and sparse batch optimization. Inputs are canonical SHA `836ee43...`, common-time sidecar SHA `ced0b929...`, `common_clock_v1.json`, verified IMU scales, pair statistics, diagnostic Q1/T4, and frozen V4-io geometry. No old body estimator is imported.

## Controlled tests

All requested development perturbation classes were actually re-solved. Segment variation remained zero. Costs alone cannot demonstrate uncertainty growth or smooth recovery; the model has no posterior covariance, which is a failed requirement.

## Validation and held-out

Configuration/calibration hashes were frozen before walk/final-still. No tuning followed. Golf and boxing were not opened. No external accuracy or clinical-angle claim is made.

## Repository

Starting HEAD `412233adcb0a5a8551f2a5d1085c79b8c2c26ae5`, branch `feature/b306-bringup`. Only new fusion_v1 code/config and new estimator logs were written. Historical files were not modified. No commit or push.
