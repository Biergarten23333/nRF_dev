# Task F3 - Stratified LMH Sanity Check

V5 explicit scalar/per-tier check:

| label | dtag_mode | d_tag_used_mm | median_3d_mm | rmse_3d_mm | notes |
| --- | --- | --- | --- | --- | --- |
| V5_CV5_stratified_scalar | single_scalar_from_disjoint_stratified_cal | 54.952 | 55.356 | 76.249 | cal=ID03;ID04;ID06;ID14;ID16;ID19 eval_complement=true |
| V5_CV5_stratified_per_tier | three_tier_values_from_disjoint_stratified_cal |  | 56.146 | 77.617 | LOW=61.955; MID=36.811; HIGH=58.674; cal=ID03;ID04;ID06;ID14;ID16;ID19 |

Stability across 100 random stratified scalar splits:

| config | mean_median_3d_mm | std_median_3d_mm | min_median_3d_mm | p95_median_3d_mm |
| --- | --- | --- | --- | --- |
| V4_CV4 | 61.801 | 6.144 | 50.404 | 72.151 |
| V5_CV5 | 68.163 | 8.685 | 51.906 | 80.550 |

