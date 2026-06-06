# Filtered Deployment Analysis

This directory contains the filtered tag-output supplement for the Erlangen
2026-05-28 official analysis.

The unfiltered `official_extra_analysis/` results remain the calibration-level
and measurement-level validation.  This folder answers a separate deployment
question: what would the tag output look like if a time filter were added on
top of, or integrated into, the tag positioning layer?

## Truth Rule

OptiTrack truth is only an evaluation reference. It is not used in filter
updates, initialization tuning, noise tuning, anchor selection, or
hyperparameter selection. Frame alignment remains anchor-locked.

## Completed Static Matrix

Run completed on 2026-06-01:

```text
5 layouts
x 2 eval sets: all8, noG
x (4 tag solvers T1--T4 x 6 external filters F0--F5 + 5 native T5 variants)
= 290 solver cells
x 24 static captures
= 6960 per-session rows
```

Main outputs:

- `tables/filtered_static_abs_errors_per_session.csv`
- `tables/filtered_static_accuracy_summary.csv`
- `tables/filtered_static_metrics_full.csv`
- `tables/filtered_static_per_axis_bias.csv`
- `tables/filtered_static_outlier_rates.csv`
- `tables/filtered_static_radial_decomposition.csv`
- `tables/filtered_static_bootstrap_ci.csv`
- `reports/filtered_static_results.md`
- `reports/filtered_static_bootstrap_ci.md`
- `reports/FILTERED_DEPLOYMENT_SUMMARY_CN.md`
- `figs/filtered_v4io_all8_solver_ranking.png`
- `figs/filtered_static_cdf_v4io_all8.png`
- `figs/filtered_bootstrap_ci_v4io_all8.png`

## Filter Naming

- `F0`: pass-through baseline; equivalent to the existing per-frame output.
- `F1`: causal constant-velocity position Kalman filter.
- `F2`: robust position Kalman filter with innovation downweighting.
- `F3`: adaptive static/dynamic position Kalman filter.
- `F4`: fixed-lag position smoother; deployable with bounded output latency.
- `F5`: full RTS offline smoother; diagnostic/appendix only, not zero-latency deployment.
- `T5a`: native range-space EKF with constant-velocity state.
- `T5b`: native robust range-space EKF.
- `T5c`: native adaptive range-space EKF; uses IMU activity when available.
- `T5d`: native range-space UKF.
- `T5e`: native range-space EKF with an additional common range-bias state.

## Headline Reading

For `v4-io/all8`, the best median static absolute result is `T3+F4`:
median 3D `59.8 mm`, P95 `155.2 mm`, RMS `100.9 mm`. The unfiltered T3 baseline
is already close: `62.3 mm`, P95 `158.2 mm`, RMS `101.8 mm`.

For deployment-oriented `T4`, filtering mainly improves repeatability, not the
absolute error budget. `T4+F0` is median `69.1 mm`, P95 `182.3 mm`, repeat-D3
`67.4 mm`; `T4+F4` is median `68.7 mm`, P95 `183.0 mm`, repeat-D3 `21.4 mm`.

Current native `T5` prototypes are useful comparisons but are not yet better
than external filtering. `T5b/T5c` give median `67.5 mm`, but their P95 is
`238.4 mm`, worse than `T3/T4` external-filter candidates.

## Interpretation

Filtering cleans temporal scatter strongly, but it does not remove the
calibration-layer layout/scale tail. Therefore these numbers should be reported
as a deployment-output supplement, not as a replacement for the official
unfiltered validation.
