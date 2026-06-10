# Phase 2.15 Common-Mode Bias Structure Test

- Generated: `2026-06-10T12:06:32`
- Ground-truth terminology: `Vicon`
- Scope: common-mode residual structure and exploratory per-facing tag-delay refit; no production files were modified.

## 2.15a Common-Mode Residuals
The scalar `c_p` is the per-position median of corrected median range minus Vicon link distance under the coherent additive-only LOO correction. This is an analysis diagnostic, not a runtime correction.

| positions | links | c_p_median_mm | c_p_p05_mm | c_p_p95_mm | abs_c_p_median_mm | abs_c_p_max_mm | common_mode_energy_fraction | common_mode_centered_r2 | headline_err_vs_abs_c_p_corr |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | 192 | -26.971 | -60.443 | 4.681 | 26.971 | 76.725 | -0.059 | -0.059 | 0.144 |


Largest absolute `c_p` positions:
| position | facing | height | location | headline_err_3d_mm | c_p_median_mm | abs_c_p_rank | after_common_mode_rms_mm |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ID17 | ABEF | low | center | 48.779 | -76.725 | 1 | 66.143 |
| ID14 | BCGF | mid | center | 92.202 | -61.729 | 2 | 40.905 |
| ID19 | CDHG | low | center | 27.369 | -53.152 | 3 | 209.836 |
| ID07 | CDHG | low | edge | 119.710 | -53.149 | 4 | 112.114 |
| ID02 | ABEF | mid | edge | 61.383 | -51.316 | 5 | 50.059 |
| ID18 | BCGF | low | center | 78.658 | -48.404 | 6 | 51.130 |
| ID08 | CDHG | mid | edge | 76.836 | -39.010 | 7 | 126.264 |
| ID21 | ABEF | high | center | 15.166 | -38.825 | 8 | 96.718 |
| ID20 | ADHE | low | center | 31.631 | -37.950 | 9 | 125.642 |
| ID12 | ADHE | high | edge | 49.574 | -31.026 | 10 | 85.705 |


Worst-6 headline positions cross-check:
| position | headline_err_3d_mm | c_p_median_mm | abs_c_p_rank | after_common_mode_rms_mm |
| --- | --- | --- | --- | --- |
| ID04 | 127.174 | -4.387 | 24 | 112.860 |
| ID07 | 119.710 | -53.149 | 4 | 112.114 |
| ID14 | 92.202 | -61.729 | 2 | 40.905 |
| ID01 | 89.088 | -20.265 | 15 | 79.609 |
| ID03 | 82.991 | -28.431 | 11 | 172.281 |
| ID18 | 78.658 | -48.404 | 6 | 51.130 |


## 2.15b Stratification
| model | n | rank | r2 | rmse_mm |
| --- | --- | --- | --- | --- |
| facing | 24 | 4 | 0.179 | 18.849 |
| height | 24 | 3 | 0.125 | 19.461 |
| location | 24 | 2 | 0.089 | 19.856 |
| facing+height | 24 | 6 | 0.305 | 17.351 |
| facing+location | 24 | 5 | 0.269 | 17.793 |
| height+location | 24 | 4 | 0.215 | 18.440 |
| facing+height+location | 24 | 7 | 0.394 | 16.197 |


| grouping | group | positions | c_p_median_mm | c_p_mean_mm | abs_c_p_median_mm | headline_err_median_mm |
| --- | --- | --- | --- | --- | --- | --- |
| facing | ABEF | 6 | -33.628 | -39.138 | 33.628 | 55.531 |
| facing | ADHE | 6 | -12.783 | -16.114 | 12.783 | 37.062 |
| facing | BCGF | 6 | -22.595 | -25.572 | 22.595 | 70.377 |
| facing | CDHG | 6 | -33.607 | -34.503 | 33.607 | 43.761 |
| height | high | 8 | -20.796 | -19.925 | 20.796 | 38.014 |
| height | low | 8 | -43.177 | -37.961 | 43.177 | 73.187 |
| height | mid | 8 | -26.971 | -28.609 | 26.971 | 49.501 |
| location | center | 12 | -33.077 | -35.053 | 33.077 | 41.022 |
| location | edge | 12 | -23.938 | -22.610 | 23.938 | 64.549 |


## 2.15c Per-Facing Delta_tag Refit
The per-facing row is exploratory because each LOO fold leaves only five training positions in the held-out facing group. The position-common-offset oracle uses Vicon-derived `c_p` and is non-deployable; it is included only to bound the common-mode hypothesis.

| mode | positions | median_3d_mm | p95_3d_mm | rmse_3d_mm | median_horizontal_mm | median_vertical_mm |
| --- | --- | --- | --- | --- | --- | --- |
| tagfit_additive_only_coherent_replay | 24 | 49.449 | 115.583 | 64.237 | 29.736 | 40.165 |
| tagfit_per_facing_delta_tag | 24 | 47.555 | 115.176 | 65.701 | 28.609 | 38.661 |
| position_common_offset_oracle | 24 | 45.654 | 130.629 | 70.708 | 28.411 | 37.955 |


Fit summary:
| fit | positions | delta_tag_for_position_median_mm | delta_tag_for_position_min_mm | delta_tag_for_position_max_mm | train_rms_median_mm | rank_median |
| --- | --- | --- | --- | --- | --- | --- |
| global_additive_only | 24 | 145.180 | 142.044 | 149.615 | 99.507 | 8.000 |
| per_facing_delta_tag | 24 | 146.759 | 99.330 | 172.926 | 99.165 | 11.000 |
| position_common_offset_oracle | 24 | 91.702 | -3.848 | 159.649 |  | 24.000 |


STOP: Phase 2.15 complete. Common-mode and per-facing tag-delay diagnostics are offline analyses only.
