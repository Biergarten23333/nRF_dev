# Phase 2.12 Additive-Coherence Check

- Generated: `2026-06-10T10:41:33`
- Ground-truth terminology: `Vicon`
- Scope: final coherence check for intermediate ladder rows; no production files were modified.

## Result
All rows use the V-B calibrated layout, C-core T4, mean session estimator, and anchor-only 3D rigid/reflection registration. The check separates coherent additive-only refits from the previous joint distance-rho fit with rho discarded.

| mode | positions | median_3d_mm | p95_3d_mm | rmse_3d_mm | median_horizontal_mm | median_vertical_mm |
| --- | --- | --- | --- | --- | --- | --- |
| sweep_delta_i_only | 24 | 100.375 | 211.880 | 135.884 | 44.731 | 90.660 |
| sweep_delta_i_plus_additive_only_delta_tag | 24 | 64.456 | 124.391 | 80.193 | 34.699 | 52.987 |
| tagfit_additive_only_coherent | 24 | 49.449 | 115.583 | 64.237 | 29.736 | 40.165 |
| tagfit_joint_delta_only_discard_rho | 24 | 254.004 | 400.646 | 261.483 | 62.900 | 245.424 |
| tagfit_joint_additive_plus_rho | 24 | 60.744 | 112.370 | 73.152 | 29.743 | 38.884 |


## Fit Coefficients
| fit | delta_tag_median_mm | delta_tag_min_mm | delta_tag_max_mm | rho_percent_median | train_rms_median_mm |
| --- | --- | --- | --- | --- | --- |
| additive_only | 145.180 | 142.044 | 149.615 |  | 99.507 |
| joint_distance_rho | -157.160 | -191.033 | -127.598 | 7.673 | 95.480 |


Interpretation: the old delta-only row is a strawman if it uses the joint distance-rho intercept while discarding rho. The coherent additive-only refit should be used for any intermediate additive-only ladder claim.

STOP: Phase 2.12 complete. Ladder rows are frozen here.
