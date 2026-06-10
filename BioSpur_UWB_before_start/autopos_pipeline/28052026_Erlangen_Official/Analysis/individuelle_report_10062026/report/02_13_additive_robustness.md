# Phase 2.13 Additive-Only Robustness Check

- Generated: `2026-06-10T10:55:09`
- Ground-truth terminology: `Vicon`
- Scope: robustness check for the new coherent additive-only headline row; no production files were modified.

## Result
All rows use C-core T4, mean session estimator, and anchor-only 3D rigid/reflection registration. `tagfit_additive_only_no_top12_train` refits the coherent additive-only tag model with the top-12 absolute-bias links removed from each LOO training set.

| mode | positions | median_3d_mm | p95_3d_mm | rmse_3d_mm | median_horizontal_mm | median_vertical_mm |
| --- | --- | --- | --- | --- | --- | --- |
| production_baseline_T4_mean | 24 | 72.689 | 171.497 | 109.845 | 37.420 | 61.870 |
| tagfit_additive_only_coherent | 24 | 49.449 | 115.583 | 64.237 | 29.736 | 40.165 |
| tagfit_additive_only_no_top12_train | 24 | 48.282 | 118.983 | 69.603 | 29.383 | 33.941 |
| tagfit_joint_additive_plus_rho | 24 | 60.744 | 112.370 | 73.152 | 29.743 | 38.884 |


## Improvement Counts
| mode | positions | improved_vs_production | worse_or_equal_vs_production | median_delta_vs_production_mm | max_worse_delta_mm | max_better_delta_mm |
| --- | --- | --- | --- | --- | --- | --- |
| tagfit_additive_only_coherent | 24 | 16 | 8 | -33.701 | 38.611 | -151.677 |
| tagfit_additive_only_no_top12_train | 24 | 18 | 6 | -33.371 | 27.327 | -105.374 |
| tagfit_joint_additive_plus_rho | 24 | 15 | 9 | -27.512 | 67.588 | -120.896 |


## No-Top12 Fit Coefficients
| mode | delta_tag_median_mm | delta_tag_min_mm | delta_tag_max_mm | train_rms_median_mm | train_links_median |
| --- | --- | --- | --- | --- | --- |
| tagfit_additive_only_no_top12_train | 105.535 | 101.079 | 108.649 | 60.516 | 172.000 |


STOP: Phase 2.13 complete. Additive-only headline robustness is frozen here.
