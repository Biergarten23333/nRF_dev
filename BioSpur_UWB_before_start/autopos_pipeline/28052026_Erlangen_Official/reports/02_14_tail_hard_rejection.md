# Phase 2.14 P95 Tail Decomposition + Hard Link Rejection

- Generated: `2026-06-10T11:47:17`
- Ground-truth terminology: `Vicon`
- Scope: offline diagnostic replay only; no production files were modified.

## Part A -- Tail Decomposition
The oracle analysis is physically separated from the implementable selectors. It uses Vicon truth only to choose the best subset and is therefore a non-deployable upper bound.

Worst-6 per-link decomposition from the headline row:

| position | anchor | range_minus_vicon_mm | range_minus_solved_distance_mm | link_noise_std_mm | in_top12_abs_bias |
| --- | --- | --- | --- | --- | --- |
| ID01 | A | -301.576 | -387.490 | 24.855 | False |
| ID01 | D | -231.692 | -183.231 | 22.388 | False |
| ID01 | C | -225.158 | -156.798 | 27.914 | False |
| ID01 | H | -192.909 | -186.282 | 144.003 | False |
| ID01 | G | -184.931 | -178.305 | 118.407 | False |
| ID01 | B | -158.808 | -170.004 | 21.294 | False |
| ID01 | F | -130.250 | -166.524 | 63.629 | False |
| ID01 | E | 33.875 | -25.833 | 22.913 | False |
| ID03 | F | -272.754 | -208.971 | 84.655 | False |
| ID03 | A | -261.216 | -348.353 | 31.917 | False |
| ID03 | E | -212.167 | -271.889 | 24.702 | False |
| ID03 | C | -210.907 | -229.852 | 22.270 | False |
| ID03 | G | -199.099 | -220.917 | 104.284 | False |
| ID03 | D | 156.114 | 100.459 | 23.073 | True |
| ID03 | H | -130.178 | -113.940 | 33.946 | False |
| ID03 | B | 127.156 | 74.429 | 22.951 | True |
| ID04 | A | -229.920 | -274.514 | 18.263 | False |
| ID04 | C | -220.109 | -187.530 | 19.409 | False |
| ID04 | D | -211.067 | -182.456 | 21.551 | False |
| ID04 | G | -196.675 | -286.744 | 103.773 | False |
| ID04 | E | -159.385 | -232.228 | 25.409 | False |
| ID04 | B | -115.873 | -170.170 | 24.608 | False |
| ID04 | F | 111.009 | -8.571 | 83.982 | True |
| ID04 | H | 45.181 | 3.726 | 31.774 | False |
| ID07 | A | -301.553 | -261.052 | 25.186 | False |
| ID07 | B | -264.183 | -212.721 | 30.543 | False |
| ID07 | C | -260.499 | -321.508 | 24.488 | False |
| ID07 | D | -260.257 | -278.948 | 20.202 | False |
| ID07 | F | -215.075 | -206.604 | 93.595 | False |
| ID07 | H | -76.810 | -154.085 | 43.390 | False |
| ID07 | E | 11.184 | 20.554 | 21.965 | False |
| ID07 | G | -9.773 | -121.950 | 118.176 | False |
| ID14 | D | -274.322 | -239.961 | 27.769 | False |
| ID14 | B | -249.707 | -217.946 | 21.050 | False |
| ID14 | G | -239.461 | -273.707 | 114.130 | False |
| ID14 | A | -222.853 | -223.786 | 22.125 | False |
| ID14 | E | -217.963 | -282.017 | 30.467 | False |
| ID14 | C | -205.992 | -143.301 | 21.961 | False |
| ID14 | F | -171.196 | -161.783 | 72.706 | False |
| ID14 | H | -102.012 | -118.331 | 27.768 | False |
| ID18 | A | -377.726 | -360.329 | 19.284 | False |
| ID18 | E | -256.450 | -172.774 | 31.741 | False |
| ID18 | D | -251.448 | -251.005 | 30.799 | False |
| ID18 | G | -227.699 | -250.930 | 104.803 | False |
| ID18 | B | -207.853 | -210.039 | 23.368 | False |
| ID18 | F | -191.559 | -146.979 | 78.621 | False |
| ID18 | C | -118.212 | -162.499 | 23.728 | False |
| ID18 | H | -103.051 | -63.816 | 45.774 | False |

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

Interpretation: the oracle confirms substantial tail headroom, but the deployable selectors do not convert that headroom into a clean replacement for the headline additive-only row. B2 is the least damaging deployable selector: it lowers P95 by 5.8 mm while increasing the median by 3.5 mm, so it is a marginal tail trade-off rather than a new headline result. B1 is essentially neutral on P95 and worsens RMSE. B3 is too aggressive, with a 16.5 mm median regression despite a small P95 reduction.

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

The same B2 selector applied to the uncalibrated production baseline is also not a meaningful standalone fix: median changes from 72.7 to 73.4 mm, RMSE from 109.8 to 107.5 mm, and P95 from 171.5 to 170.9 mm. Hard rejection therefore does not explain the production baseline or replace the coherent tag-side calibration.


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
