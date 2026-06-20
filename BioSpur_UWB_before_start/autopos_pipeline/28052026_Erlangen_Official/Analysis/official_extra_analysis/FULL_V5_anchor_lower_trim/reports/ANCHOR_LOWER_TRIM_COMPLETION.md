# Anchor Lower-Trim Blind Experiment Completion

Generated: 2026-06-19T10:55:13

## BLIND EXPERIMENT RESULT

- V5 (p50 anchors) + lower_trim_20 tags + Huber30: 44.485 mm LOO
- Best lower_trim_20-anchor row: lower_trim_20__E2_e_zero = 46.375 mm LOO
- lower_trim_20-anchor improvement old - new: -1.890 mm
- lower_trim_20-anchor verdict: WORSE

## Best Overall Variant

- Best overall row: p50__E2_e_zero = 43.172 mm LOO
- Best overall improvement old - best: 1.313 mm
- P(new wins): 0.659
- Best overall verdict: IMPROVEMENT

## Inter-anchor distribution

- Raw valid AA rows: 56000
- Frames per pair median: 2000
- Mean skewness: 0.063
- Mean p50 - lower_trim_20: 32.878 mm

## Top tag results

| range_method | e_setting | layout_label | loo_median_mm | p95_mm | rmse_mm | d_tag_mean_mm | d_tag_median_mm | d_tag_std_mm | sim3_scale | rigid_rmse_mm | device |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| p50 | E2_e_zero | p50__E2_e_zero | 43.172 | 163.093 | 81.790 | 8.531 | 8.577 | 0.760 | 1.010 | 63.069 | cuda:0 |
| p50 | E1_e_reg5 | p50__E1_e_reg5 | 43.223 | 163.121 | 81.799 | 8.404 | 8.470 | 0.734 | 1.010 | 62.942 | cuda:0 |
| p30 | E2_e_zero | p30__E2_e_zero | 44.325 | 164.296 | 82.571 | 14.844 | 14.893 | 0.880 | 1.014 | 65.477 | cuda:0 |
| p30 | E1_e_reg5 | p30__E1_e_reg5 | 44.474 | 164.873 | 82.653 | 14.743 | 14.905 | 0.908 | 1.014 | 65.396 | cuda:0 |
| p50_control | V5_current_e_reg20 | CONTROL_current_V5_p50 | 44.485 | 168.369 | 82.199 | 6.924 | 7.025 | 0.630 | nan | nan | cuda:0 |
| p50 | E0_e_reg20 | p50__E0_e_reg20 | 44.630 | 167.623 | 82.174 | 6.603 | 6.594 | 0.610 | 1.010 | 62.542 | cuda:0 |
| p20 | E2_e_zero | p20__E2_e_zero | 45.182 | 166.423 | 83.533 | 18.135 | 18.595 | 0.993 | 1.016 | 67.233 | cuda:0 |
| p20 | E1_e_reg5 | p20__E1_e_reg5 | 45.252 | 166.946 | 83.565 | 18.087 | 18.564 | 1.015 | 1.016 | 67.156 | cuda:0 |
| p10 | E2_e_zero | p10__E2_e_zero | 45.687 | 166.685 | 83.985 | 23.386 | 23.599 | 0.587 | 1.020 | 71.024 | cuda:0 |
| p10 | E1_e_reg5 | p10__E1_e_reg5 | 45.767 | 166.719 | 83.944 | 23.319 | 23.414 | 0.524 | 1.020 | 70.959 | cuda:0 |

## Runtime

| stage | elapsed_s |
| --- | --- |
| L1 | 3.171 |
| L2 | 2.340 |
| L3 | 3.093 |
| L4 | 0.135 |
| L5 | 0.429 |
| TOTAL | 11.882 |
