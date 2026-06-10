# Phase 2.9 Estimated-Distance Runtime Candidate

- Generated: `2026-06-10T01:18:27`
- Ground-truth terminology: `Vicon`
- Scope: one final deployability experiment; no existing reports were modified.

## Result
Calibration is still supervised and leave-one-position-out: `rho`, `Delta_i`, and `Delta_tag` are fitted using Vicon link distance on the training positions. Runtime does not use Vicon distance. Iter0 solves with additive correction only. Iter1 computes per-frame estimated link distances from the iter0 solution and applies `r1 = r - Delta_i/2 - Delta_tag/2 - rho * ||x_hat - a_i||`, then solves again. Iter2 repeats the same operation from iter1.

| mode | positions | median_3d_mm | p95_3d_mm | rmse_3d_mm | median_horizontal_mm | median_vertical_mm | fit_uses_vicon_link_distance | runtime_uses_vicon_link_distance |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| production_baseline_T4_mean | 24 | 72.689 | 171.497 | 109.845 | 37.420 | 61.870 | False | False |
| vicon_distance_covariate | 24 | 60.744 | 112.370 | 73.152 | 29.743 | 38.884 | True | False |
| measured_median_range_covariate | 24 | 76.610 | 120.272 | 91.019 | 41.693 | 41.173 | False | False |
| estimated_distance_iter0_additive_only | 24 | 254.004 | 400.646 | 261.483 | 62.900 | 245.424 | True | False |
| estimated_distance_iter1 | 24 | 77.874 | 117.802 | 82.515 | 33.219 | 44.412 | True | False |
| estimated_distance_iter2 | 24 | 61.852 | 113.054 | 73.038 | 29.384 | 40.180 | True | False |


Iter1 RMSE delta vs supervised Vicon-distance row: `9.364` mm. Iter1 RMSE delta vs production baseline: `-27.330` mm. Iter2 convergence check: **MOVES >2 mm**.

![Estimated-distance runtime comparison](figures/08_estimated_distance_runtime_comparison.png)

## Iteration Movement
| metric | median_mm | p95_mm | max_mm |
| --- | --- | --- | --- |
| iter0_to_iter1_move_mm | 301.992 | 381.217 | 445.990 |
| iter1_to_iter2_move_mm | 21.216 | 34.102 | 49.275 |


## LOO Fit Coefficients
| rho_percent_median | rho_percent_min | rho_percent_max | delta_tag_median_mm | train_rms_median_mm |
| --- | --- | --- | --- | --- |
| 7.673 | 6.849 | 8.496 | -157.160 | 95.480 |


STOP: Phase 2.9 complete. This is the final diagnostics record.
