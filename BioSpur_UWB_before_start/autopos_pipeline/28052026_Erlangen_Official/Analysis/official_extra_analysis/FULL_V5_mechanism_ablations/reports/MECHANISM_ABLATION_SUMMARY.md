# V5 Mechanism Ablation Summary

Generated: 2026-06-17T21:09:56

## Synthesis

Hard height-tier CV degradation is 8.4 mm for V4+C_V4 and 4.5 mm for V5+C_V5. The hard-CV degradation is similar, so the evidence is mixed rather than decisive. Use the tables below to separate validation robustness, residual-field structure, scale-delay valley shape, and D_tag criterion ambiguity.

## Item A - Hard CV

| config | full_loo_median_3d_mm | worst_tier | worst_tier_median_3d_mm | height_degradation_mm | worst_edge_center | edge_center_degradation_mm |
| --- | --- | --- | --- | --- | --- | --- |
| V4+C_V4 | 57.921 | LOW | 66.334 | 8.414 | OUTER | 14.797 |
| V5+C_V5 | 67.849 | LOW | 72.388 | 4.539 | OUTER | 16.856 |
| Vicon+C_Vicon_cm | 63.392 | LOW | 75.588 | 12.197 | OUTER | 19.328 |

## Item B - Residual Field

| config | mean_signed_error_magnitude | structured_bias_index | mean_error_direction_resultant | median_3d | rmse_3d |
| --- | --- | --- | --- | --- | --- |
| V4+C_V4 | 33.163 | 1.324 | 0.486 | 57.921 | 74.372 |
| V5+C_V5 | 26.721 | 1.354 | 0.344 | 67.849 | 82.799 |

## Item C - Cancellation Valley

| marker_name | s | nearest_grid_s | d_tag_mm | median_3d_mm | source |
| --- | --- | --- | --- | --- | --- |
| V5_LOO | 1.000 |  | 49.621 | 67.849 | exact_transfer_matrix |
| V4_equiv | 0.949 | 0.950 | 0.000 | 274.819 | nearest_grid |
| global_min | 0.980 |  | 108.000 | 55.297 | grid |

## Item D - Per-height D_tag Stability

| config | min_dtag_min_median | max_dtag_min_median | tier_spread_mm |
| --- | --- | --- | --- |
| V4+C_V4 | 20.000 | 54.000 | 34.000 |
| V5+C_V5 | 70.000 | 86.000 | 16.000 |
| Vicon+C_Vicon_cm | 72.000 | 88.000 | 16.000 |

## Item E - D_tag Curve Critical Points

| config | d_tag_min_median | d_tag_min_rmse | d_tag_min_p95 | d_tag_zero_slope | spread_mm |
| --- | --- | --- | --- | --- | --- |
| L_V4+C_V4 | 50.000 | 56.000 | 56.000 | 69.730 | 19.730 |
| L_V4+C_V5 | 0.000 | 0.000 | 2.000 | 0.000 | 2.000 |
| L_V4+C_none | 88.000 | 90.000 | 116.000 | 107.550 | 28.000 |
| L_V5+C_V5 | 76.000 | 74.000 | 70.000 | 88.573 | 18.573 |
| L_V5+C_none | 120.000 | 120.000 | 120.000 | 120.000 | 0.000 |
| L_Vicon+C_Vicon_cm | 68.000 | 76.000 | 84.000 | 87.330 | 19.330 |

## Item F - Multi-criterion D_tag

| config | d_tag_min_median | d_tag_min_rmse | d_tag_min_p95 | d_tag_zero_slope | d_tag_loo_cv | spread_mm |
| --- | --- | --- | --- | --- | --- | --- |
| L_V4+C_V4 | 50.000 | 56.000 | 56.000 | 69.730 | 49.621 | 20.109 |
| L_V4+C_V5 | 0.000 | 0.000 | 2.000 | 0.000 | 49.621 | 49.621 |
| L_V4+C_none | 88.000 | 90.000 | 116.000 | 107.550 | 49.621 | 66.379 |
| L_V5+C_V5 | 76.000 | 74.000 | 70.000 | 88.573 | 49.621 | 38.952 |
| L_V5+C_none | 120.000 | 120.000 | 120.000 | 120.000 | 49.621 | 70.379 |
| L_Vicon+C_Vicon_cm | 68.000 | 76.000 | 84.000 | 87.330 | 49.621 | 37.709 |

## Runtime

| item | elapsed_s | mean_cpu_percent | max_cpu_percent | physical_cores | logical_cores | workers |
| --- | --- | --- | --- | --- | --- | --- |
| Item E | 0.028 | 14.300 | 14.300 | 6 | 12 | 6 |
| Item F | 0.023 | 10.700 | 10.700 | 6 | 12 | 6 |
| Item A | 5.141 | 30.380 | 47.800 | 6 | 12 | 6 |
| Item B | 4.103 | 25.050 | 26.700 | 6 | 12 | 6 |
| Item D | 118.240 | 59.041 | 82.800 | 6 | 12 | 6 |
| Item C | 1118.756 | 63.727 | 91.900 | 6 | 12 | 6 |

