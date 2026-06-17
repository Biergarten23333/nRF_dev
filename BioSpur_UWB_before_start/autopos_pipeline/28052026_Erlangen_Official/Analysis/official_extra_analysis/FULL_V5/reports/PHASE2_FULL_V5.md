# PHASE 2 - FULL V5

## Static Summary

| tag_delay_mode | tag_delay_value_mm | median_3d_mm | p95_3d_mm | rmse_3d_mm | median_vert_mm | signed_vertical_slope_mm_per_m |
| --- | --- | --- | --- | --- | --- | --- |
| D0 | 0.000 | 109.515 | 223.859 | 140.520 | 89.421 | 196.420 |
| D_LOO_CV | 49.621 | 67.849 | 153.635 | 82.799 | 59.417 | 76.943 |
| D_sweep_opt | 76.000 | 56.619 | 128.054 | 74.484 | 46.702 | 23.454 |

## Delay Comparison

| anchor_label | v4_d_anchor_mm | v5_d_anchor_mm | v5_minus_v4_d_anchor_mm | v5_common_mode_mm | v5_differential_e_i_mm |
| --- | --- | --- | --- | --- | --- |
| A | 0.000 | 99.634 | 99.634 | 111.985 | -12.351 |
| B | 37.127 | 113.688 | 76.561 | 111.985 | 1.703 |
| C | 60.000 | 127.338 | 67.338 | 111.985 | 15.353 |
| D | 60.000 | 124.511 | 64.511 | 111.985 | 12.526 |
| E | 31.143 | 109.625 | 78.482 | 111.985 | -2.359 |
| F | 27.043 | 111.362 | 84.319 | 111.985 | -0.623 |
| G | 27.559 | 100.042 | 72.483 | 111.985 | -11.943 |
| H | 32.419 | 109.679 | 77.260 | 111.985 | -2.306 |

## ROTO Summary

| tag_delay_mode | tag_delay_value_mm | median_3d_mm | p95_3d_mm | rmse_3d_mm | median_vert_mm | notes |
| --- | --- | --- | --- | --- | --- | --- |
| D0 | 0.000 | 126.394 | 276.249 | 167.274 | 82.721 | ROTO BEST-FIT-ALIGNED; no hardware time sync |
| D_LOO_CV | 49.621 | 101.485 | 214.369 | 126.226 | 64.004 | ROTO BEST-FIT-ALIGNED; no hardware time sync |

## Runtime

| physical_cores | logical_cores | workers | elapsed_s | mean_cpu_percent | max_cpu_percent |
| --- | --- | --- | --- | --- | --- |
| 6 | 12 | 6 | 84.157 | 50.453 | 99.700 |

