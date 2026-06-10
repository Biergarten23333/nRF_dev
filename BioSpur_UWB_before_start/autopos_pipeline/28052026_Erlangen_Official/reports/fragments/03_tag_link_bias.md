## 1.3 Tag Link Bias

Static tag link bias was computed over `192` position-anchor links using the verified anchor_id mapping from `data_config.py`.
Linear pooled bias-vs-distance fit: intercept `17.6` mm, slope `7.514%`, residual RMS `100.9` mm.
Reconstructed tag truth positions are flagged in all tables. Among the top 12 absolute-bias links, `1` use reconstructed truth.
`R01-Static-middle-test` is excluded from dynamic RotoArm analysis and listed only as an auxiliary static range check.

![Tag bias vs distance](figures/03_tag_bias_vs_distance.png)

![Per-anchor bias boxplot](figures/03_tag_anchor_bias_boxplot.png)

![Tag bias matrix](figures/03_tag_bias_matrix.png)

Per-anchor summary:

| anchor | links | bias_mean_mm | bias_median_mm | bias_p95_abs_mm | noise_median_std_mm |
| --- | --- | --- | --- | --- | --- |
| A | 24 | 228.946 | 193.362 | 475.399 | 22.219 |
| B | 24 | 155.663 | 112.355 | 410.924 | 24.610 |
| C | 24 | 145.019 | 93.182 | 307.841 | 22.293 |
| D | 24 | 202.621 | 171.984 | 408.800 | 24.571 |
| E | 24 | 150.130 | 130.170 | 316.331 | 24.547 |
| F | 24 | 161.832 | 132.221 | 369.309 | 85.886 |
| G | 24 | 171.768 | 151.461 | 301.930 | 95.511 |
| H | 24 | 111.858 | 99.344 | 246.303 | 40.959 |


Facing/truth-source stratification:

| facing | truth_reconstructed | links | bias_mean_mm | bias_median_mm | bias_p95_abs_mm |
| --- | --- | --- | --- | --- | --- |
| ABEF | False | 40 | 151.802 | 121.081 | 412.151 |
| ABEF | True | 8 | 158.741 | 160.996 | 274.485 |
| ADHE | False | 48 | 174.497 | 146.336 | 305.530 |
| BCGF | False | 40 | 162.407 | 139.196 | 329.964 |
| BCGF | True | 8 | 201.770 | 139.840 | 486.729 |
| CDHG | False | 48 | 167.496 | 145.005 | 422.345 |


Largest absolute link biases:

| position | anchor | bias_mm | vicon_distance_mm | median_range_mm | tag_truth_source | truth_reconstructed | facing |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ID05 | A | 640.394 | 2433.6 | 3074.0 | reconstructed_from_relabelled_balls | True | BCGF |
| ID24 | C | 589.845 | 2249.2 | 2839.0 | motive_iantenna | False | ADHE |
| ID19 | G | 565.309 | 2242.7 | 2808.0 | motive_iantenna | False | CDHG |
| ID03 | D | 532.711 | 2445.3 | 2978.0 | motive_iantenna | False | ABEF |
| ID22 | A | 501.835 | 2112.2 | 2614.0 | motive_iantenna | False | BCGF |
| ID08 | B | 446.516 | 2210.5 | 2657.0 | motive_iantenna | False | CDHG |
| ID19 | E | 439.252 | 2279.7 | 2719.0 | motive_iantenna | False | CDHG |
| ID03 | B | 415.968 | 2019.0 | 2435.0 | motive_iantenna | False | ABEF |
| ID04 | F | 412.948 | 1863.1 | 2276.0 | motive_iantenna | False | BCGF |
| ID21 | D | 411.950 | 2230.0 | 2642.0 | motive_iantenna | False | ABEF |
| ID09 | D | 390.946 | 2023.1 | 2414.0 | motive_iantenna | False | CDHG |
| ID20 | F | 383.878 | 2299.1 | 2683.0 | motive_iantenna | False | ADHE |
