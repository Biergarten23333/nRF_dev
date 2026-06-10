# Phase 2.14 P95 Tail Decomposition + Hard Link Rejection

- Generated: `2026-06-10T12:16:30`
- Ground-truth terminology: `Vicon`
- Scope: offline diagnostic replay only; no production files were modified.

## Part A -- Tail Decomposition
The oracle analysis is physically separated from the implementable selectors. It uses Vicon truth only to choose the best subset and is therefore a non-deployable upper bound.

Worst-6 per-link decomposition from the corrected headline row:

| position | anchor | range_minus_vicon_mm | range_minus_solved_distance_mm | link_noise_std_mm | in_top12_abs_bias |
| --- | --- | --- | --- | --- | --- |
| ID01 | E | 176.643 | 116.935 | 22.913 | False |
| ID01 | H | -77.811 | -71.185 | 144.003 | False |
| ID01 | C | -76.937 | -8.577 | 27.914 | False |
| ID01 | A | -69.735 | -155.649 | 24.855 | False |
| ID01 | F | 30.316 | -5.957 | 63.629 | False |
| ID01 | D | -27.910 | 20.551 | 22.388 | False |
| ID01 | G | -12.636 | -6.009 | 118.407 | False |
| ID01 | B | -3.023 | -14.218 | 21.294 | False |
| ID03 | D | 344.384 | 288.728 | 23.073 | True |
| ID03 | B | 271.502 | 218.775 | 22.951 | True |
| ID03 | F | -106.487 | -42.703 | 84.655 | False |
| ID03 | C | -63.257 | -82.201 | 22.270 | False |
| ID03 | E | -59.560 | -119.282 | 24.702 | False |
| ID03 | A | -30.997 | -118.134 | 31.917 | False |
| ID03 | G | -26.235 | -48.053 | 104.284 | False |
| ID03 | H | -17.590 | -1.352 | 33.946 | False |
| ID04 | F | 261.923 | 142.343 | 83.982 | True |
| ID04 | H | 150.754 | 109.300 | 31.774 | False |
| ID04 | C | -72.090 | -39.511 | 19.409 | False |
| ID04 | B | 38.193 | -16.105 | 24.608 | False |
| ID04 | G | -23.911 | -113.980 | 103.773 | False |
| ID04 | E | -8.887 | -81.730 | 25.409 | False |
| ID04 | D | -8.110 | 20.501 | 21.551 | False |
| ID04 | A | -0.945 | -45.538 | 18.263 | False |
| ID07 | G | 155.516 | 43.339 | 118.176 | False |
| ID07 | E | 154.859 | 164.229 | 21.965 | False |
| ID07 | C | -110.864 | -171.872 | 24.488 | False |
| ID07 | B | -104.185 | -52.723 | 30.543 | False |
| ID07 | A | -69.719 | -29.218 | 25.186 | False |
| ID07 | D | -55.332 | -74.023 | 20.202 | False |
| ID07 | F | -51.118 | -42.647 | 93.595 | False |
| ID07 | H | 33.642 | -43.632 | 43.390 | False |
| ID14 | B | -90.288 | -58.526 | 21.050 | False |
| ID14 | D | -68.835 | -34.474 | 27.769 | False |
| ID14 | E | -65.123 | -129.177 | 30.467 | False |
| ID14 | G | -64.983 | -99.229 | 114.130 | False |
| ID14 | C | -58.538 | 4.153 | 21.961 | False |
| ID14 | H | 9.449 | -6.869 | 27.768 | False |
| ID14 | F | -8.993 | 0.421 | 72.706 | False |
| ID14 | A | 5.830 | 4.897 | 22.125 | False |
| ID18 | A | -142.842 | -125.445 | 19.284 | False |
| ID18 | E | -102.070 | -18.393 | 31.741 | False |
| ID18 | G | -53.693 | -76.924 | 104.803 | False |
| ID18 | B | -50.107 | -52.293 | 23.368 | False |
| ID18 | D | -46.876 | -46.433 | 30.799 | False |
| ID18 | F | -28.541 | 16.039 | 78.621 | False |
| ID18 | C | 25.732 | -18.556 | 23.728 | False |
| ID18 | H | 8.451 | 47.686 | 45.774 | False |


| position | headline_err_3d_mm | oracle_best_err_3d_mm | oracle_improvement_mm | oracle_dropped_links | verdict |
| --- | --- | --- | --- | --- | --- |
| ID04 | 127.174 | 25.236 | 101.938 | C,F | oracle improves after dropping 2 link(s) |
| ID07 | 119.710 | 40.457 | 79.252 | B,G | oracle improves after dropping 2 link(s) |
| ID14 | 92.202 | 46.713 | 45.489 | B,D | oracle improves after dropping 2 link(s) |
| ID01 | 89.088 | 30.713 | 58.375 | C,G | oracle improves after dropping 2 link(s) |
| ID03 | 82.991 | 40.043 | 42.947 | D,G | oracle improves after dropping 2 link(s) |
| ID18 | 78.658 | 46.867 | 31.791 | C,E | oracle improves after dropping 2 link(s) |


## Part B/C -- Deployable Selector Evaluation
B1 is session-level static rejection. B2 is the deployment-shaped frame-level greedy selector. B3 is frame-level exhaustive subset selection. All three use only corrected ranges, solver residuals, and subset geometry; Vicon is not used by the selectors.

| mode | oracle | deployable | positions | median_3d_mm | rmse_3d_mm | p95_3d_mm | median_delta_vs_headline_mm | p95_delta_vs_headline_mm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| headline_additive_only | False | True | 24 | 49.449 | 64.237 | 115.583 | 0.000 | 0.000 |
| B1_session_t2_g1.5 | False | True | 24 | 49.626 | 66.330 | 115.583 | 0.177 | 0.000 |
| B2_frame_greedy_tau4_t2_g1.5 | False | True | 24 | 52.966 | 66.407 | 109.770 | 3.517 | -5.813 |
| B3_frame_exhaustive_t2_g2 | False | True | 24 | 65.948 | 77.829 | 113.894 | 16.499 | -1.689 |
| oracle_best_subset_median_range | True | False | 24 | 27.845 | 32.438 | 49.550 | -21.604 | -66.033 |


Best deployable selector selected for production-baseline comparison: `B2_frame_greedy_tau4_t2_g1.5`.

Selector family best rows:
| family | mode | positions | median_3d_mm | rmse_3d_mm | p95_3d_mm | median_regression_vs_headline_mm |
| --- | --- | --- | --- | --- | --- | --- |
| B1_session | B1_session_t2_g1.5 | 24 | 49.626 | 66.330 | 115.583 | 0.177 |
| B2_frame_greedy | B2_frame_greedy_tau4_t2_g1.5 | 24 | 52.966 | 66.407 | 109.770 | 3.517 |
| B3_frame_exhaustive | B3_frame_exhaustive_t2_g2 | 24 | 65.948 | 77.829 | 113.894 | 16.499 |


## Per-Position Deltas
| mode | positions | improved_vs_headline | worse_or_equal_vs_headline | median_delta_vs_headline_mm | max_worse_delta_mm | max_better_delta_mm |
| --- | --- | --- | --- | --- | --- | --- |
| B1_session_t2_g1.5 | 24 | 0 | 24 | 0.000 | 54.001 | 0.000 |
| B2_frame_greedy_tau4_t2_g1.5 | 24 | 11 | 13 | 0.267 | 50.466 | -17.329 |
| B3_frame_exhaustive_t2_g2 | 24 | 6 | 18 | 4.845 | 99.388 | -100.216 |


Worst selector regressions relative to the headline row:
| mode | position | headline_err_3d_mm | selector_err_3d_mm | delta_vs_headline_mm |
| --- | --- | --- | --- | --- |
| B3_frame_exhaustive_t2_g2 | ID22 | 62.095 | 161.483 | 99.388 |
| B3_frame_exhaustive_t2_g2 | ID20 | 31.631 | 113.955 | 82.324 |
| B3_frame_exhaustive_t2_g2 | ID24 | 33.713 | 99.245 | 65.532 |
| B3_frame_exhaustive_t2_g2 | ID05 | 49.324 | 104.035 | 54.711 |
| B1_session_t2_g1.5 | ID24 | 33.713 | 87.714 | 54.001 |
| B2_frame_greedy_tau4_t2_g1.5 | ID24 | 33.713 | 84.179 | 50.466 |
| B3_frame_exhaustive_t2_g2 | ID16 | 36.835 | 68.755 | 31.920 |
| B3_frame_exhaustive_t2_g2 | ID06 | 23.122 | 54.129 | 31.007 |
| B3_frame_exhaustive_t2_g2 | ID12 | 49.574 | 79.862 | 30.288 |
| B2_frame_greedy_tau4_t2_g1.5 | ID22 | 62.095 | 92.203 | 30.108 |
| B2_frame_greedy_tau4_t2_g1.5 | ID06 | 23.122 | 47.237 | 24.116 |
| B3_frame_exhaustive_t2_g2 | ID14 | 92.202 | 111.053 | 18.851 |
| B3_frame_exhaustive_t2_g2 | ID19 | 27.369 | 43.166 | 15.796 |
| B3_frame_exhaustive_t2_g2 | ID21 | 15.166 | 27.296 | 12.130 |
| B3_frame_exhaustive_t2_g2 | ID11 | 37.288 | 47.388 | 10.100 |
| B2_frame_greedy_tau4_t2_g1.5 | ID12 | 49.574 | 55.801 | 6.227 |
| B3_frame_exhaustive_t2_g2 | ID01 | 89.088 | 95.211 | 6.123 |
| B2_frame_greedy_tau4_t2_g1.5 | ID11 | 37.288 | 42.025 | 4.737 |


## Selector Audit vs Oracle
| mode | selector_dropped_total | oracle_dropped_total | false_drop_total | miss_total | false_drop_rate | miss_rate |
| --- | --- | --- | --- | --- | --- | --- |
| B1_session_t2_g1.5 | 1 | 40 | 1 | 40 | 1.000 | 1.000 |
| B2_frame_greedy_tau4_t2_g1.5 | 22 | 40 | 18 | 36 | 0.818 | 0.900 |
| B3_frame_exhaustive_t2_g2 | 107 | 40 | 84 | 17 | 0.785 | 0.425 |


## Production Baseline Selector Check
| mode | positions | median_3d_mm | rmse_3d_mm | p95_3d_mm | median_horizontal_mm | median_vertical_mm |
| --- | --- | --- | --- | --- | --- | --- |
| production_baseline_with_best_selector | 24 | 73.377 | 107.529 | 170.942 | 37.950 | 63.255 |


## Drop Cross-References
| mode | position | anchor | dropped_frame_percent | oracle_dropped_this_link | in_top12_abs_bias | high_noise_FG_anchor | cir_watchlist_anchor_member |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B1_session_t2_g1.5 | ID24 | C | 100.000 | False | True | False | True |
| B2_frame_greedy_tau4_t2_g1.5 | ID01 | A | 13.239 | False | False | False | False |
| B2_frame_greedy_tau4_t2_g1.5 | ID04 | F | 5.995 | True | True | True | True |
| B2_frame_greedy_tau4_t2_g1.5 | ID05 | A | 35.000 | True | True | False | False |
| B2_frame_greedy_tau4_t2_g1.5 | ID05 | G | 9.750 | False | False | True | True |
| B2_frame_greedy_tau4_t2_g1.5 | ID06 | C | 43.381 | False | False | False | True |
| B2_frame_greedy_tau4_t2_g1.5 | ID08 | B | 25.895 | True | True | False | False |
| B2_frame_greedy_tau4_t2_g1.5 | ID08 | C | 5.662 | False | False | False | True |
| B2_frame_greedy_tau4_t2_g1.5 | ID10 | G | 20.733 | False | False | True | True |
| B2_frame_greedy_tau4_t2_g1.5 | ID10 | F | 8.743 | True | False | True | True |
| B2_frame_greedy_tau4_t2_g1.5 | ID11 | C | 46.794 | False | False | False | True |
| B2_frame_greedy_tau4_t2_g1.5 | ID12 | C | 29.725 | False | False | False | True |
| B2_frame_greedy_tau4_t2_g1.5 | ID14 | E | 5.329 | False | False | False | True |
| B2_frame_greedy_tau4_t2_g1.5 | ID15 | G | 6.328 | False | False | True | True |
| B2_frame_greedy_tau4_t2_g1.5 | ID16 | G | 9.409 | False | False | True | True |
| B2_frame_greedy_tau4_t2_g1.5 | ID20 | F | 5.329 | False | True | True | True |
| B2_frame_greedy_tau4_t2_g1.5 | ID21 | D | 24.147 | False | True | False | False |
| B2_frame_greedy_tau4_t2_g1.5 | ID22 | A | 53.122 | False | True | False | False |
| B2_frame_greedy_tau4_t2_g1.5 | ID22 | G | 14.821 | False | False | True | True |
| B2_frame_greedy_tau4_t2_g1.5 | ID23 | B | 31.640 | False | False | False | False |
| B2_frame_greedy_tau4_t2_g1.5 | ID24 | C | 89.925 | False | True | False | True |
| B2_frame_greedy_tau4_t2_g1.5 | ID24 | E | 8.993 | False | False | False | True |
| B2_frame_greedy_tau4_t2_g1.5 | ID24 | A | 5.079 | False | False | False | False |
| B3_frame_exhaustive_t2_g2 | ID01 | A | 85.845 | False | False | False | False |
| B3_frame_exhaustive_t2_g2 | ID01 | E | 48.793 | False | False | False | True |
| B3_frame_exhaustive_t2_g2 | ID01 | H | 39.800 | False | False | False | True |
| B3_frame_exhaustive_t2_g2 | ID01 | C | 8.160 | True | False | False | True |
| B3_frame_exhaustive_t2_g2 | ID01 | G | 6.661 | True | False | True | True |
| B3_frame_exhaustive_t2_g2 | ID02 | A | 40.383 | False | False | False | False |
| B3_frame_exhaustive_t2_g2 | ID02 | E | 37.302 | False | False | False | True |
| B3_frame_exhaustive_t2_g2 | ID02 | G | 22.981 | False | False | True | True |
| B3_frame_exhaustive_t2_g2 | ID02 | C | 19.067 | True | False | False | True |
| B3_frame_exhaustive_t2_g2 | ID02 | D | 7.244 | True | False | False | False |
| B3_frame_exhaustive_t2_g2 | ID03 | D | 69.276 | True | True | False | False |
| B3_frame_exhaustive_t2_g2 | ID03 | B | 62.281 | False | True | False | False |
| B3_frame_exhaustive_t2_g2 | ID04 | H | 79.850 | False | False | False | True |
| B3_frame_exhaustive_t2_g2 | ID04 | F | 79.184 | True | True | True | True |
| B3_frame_exhaustive_t2_g2 | ID04 | C | 16.736 | True | False | False | True |
| B3_frame_exhaustive_t2_g2 | ID04 | G | 10.491 | False | False | True | True |
| B3_frame_exhaustive_t2_g2 | ID04 | A | 5.912 | False | False | False | False |


STOP: Phase 2.14 complete. Oracle rows are upper bounds only; B1/B2/B3 remain offline diagnostic selectors and were not integrated into production.
