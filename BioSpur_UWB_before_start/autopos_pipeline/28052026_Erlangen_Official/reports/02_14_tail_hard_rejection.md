# Phase 2.14 P95 Tail Decomposition + Hard Link Rejection

- Generated: `2026-06-10T12:11:17`
- Ground-truth terminology: `Vicon`
- Scope: offline diagnostic replay only; no production files were modified.

## Part A -- Tail Decomposition
The oracle analysis is physically separated from the implementable selectors. It uses Vicon truth only to choose the best subset and is therefore a non-deployable upper bound.

| position | headline_err_3d_mm | oracle_best_err_3d_mm | oracle_improvement_mm | oracle_dropped_links | verdict |
| --- | --- | --- | --- | --- | --- |
| ID04 | 127.174 | 25.236 | 101.938 | C,F | dominated by 2 bad link(s) |
| ID07 | 119.710 | 40.457 | 79.252 | B,G | dominated by 2 bad link(s) |
| ID14 | 92.202 | 46.713 | 45.489 | B,D | dominated by 2 bad link(s) |
| ID01 | 89.088 | 30.713 | 58.375 | C,G | dominated by 2 bad link(s) |
| ID03 | 82.991 | 40.043 | 42.947 | D,G | dominated by 2 bad link(s) |
| ID18 | 78.658 | 46.867 | 31.791 | C,E | dominated by 2 bad link(s) |


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
