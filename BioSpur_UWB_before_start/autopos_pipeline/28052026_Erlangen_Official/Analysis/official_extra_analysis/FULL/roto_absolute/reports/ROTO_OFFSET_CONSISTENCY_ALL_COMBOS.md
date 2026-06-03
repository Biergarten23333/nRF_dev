# ROTO Offset Consistency Across Solver Combinations

Each layout/tag-solver combination independently searched the OptiTrack time offset for every ROTO capture. Spatial alignment remained anchor-locked; only the timing offset changed.

Reference for deltas: `v4-io/T4`, because it is the production pipeline used by the main ROTO absolute report.

Across the 19 non-reference combinations, median absolute offset delta has median 0.010 s; P95 absolute offset delta has median 0.020 s; worst max absolute delta across combinations is 0.060 s.

## Summary By Solver

| layout | tag_method | median_abs_delta_s | p95_abs_delta_s | max_abs_delta_s | within_0p10s_pct | within_0p50s_pct | score_median_3d_mm | outliers_gt_0p5s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1-old | T1 | 0.010 | 0.028 | 0.040 | 100.000 | 100.000 | 127.665 |  |
| v1-old | T2 | 0.010 | 0.036 | 0.040 | 100.000 | 100.000 | 127.710 |  |
| v1-old | T3 | 0.025 | 0.052 | 0.060 | 100.000 | 100.000 | 130.165 |  |
| v1-old | T4 | 0.010 | 0.026 | 0.030 | 100.000 | 100.000 | 125.012 |  |
| v2 | T1 | 0.010 | 0.016 | 0.020 | 100.000 | 100.000 | 105.661 |  |
| v2 | T2 | 0.010 | 0.020 | 0.020 | 100.000 | 100.000 | 105.552 |  |
| v2 | T3 | 0.010 | 0.020 | 0.020 | 100.000 | 100.000 | 105.710 |  |
| v2 | T4 | 0.010 | 0.016 | 0.020 | 100.000 | 100.000 | 108.491 |  |
| v3-lite | T1 | 0.010 | 0.020 | 0.020 | 100.000 | 100.000 | 105.838 |  |
| v3-lite | T2 | 0.010 | 0.020 | 0.020 | 100.000 | 100.000 | 105.752 |  |
| v3-lite | T3 | 0.010 | 0.015 | 0.015 | 100.000 | 100.000 | 106.002 |  |
| v3-lite | T4 | 0.010 | 0.020 | 0.020 | 100.000 | 100.000 | 108.291 |  |
| v3-full | T1 | 0.015 | 0.031 | 0.035 | 100.000 | 100.000 | 117.633 |  |
| v3-full | T2 | 0.010 | 0.031 | 0.035 | 100.000 | 100.000 | 117.547 |  |
| v3-full | T3 | 0.020 | 0.032 | 0.040 | 100.000 | 100.000 | 118.598 |  |
| v3-full | T4 | 0.010 | 0.028 | 0.040 | 100.000 | 100.000 | 114.092 |  |
| v4-io | T1 | 0.005 | 0.015 | 0.015 | 100.000 | 100.000 | 102.570 |  |
| v4-io | T2 | 0.005 | 0.011 | 0.015 | 100.000 | 100.000 | 102.418 |  |
| v4-io | T3 | 0.005 | 0.017 | 0.025 | 100.000 | 100.000 | 101.957 |  |
| v4-io | T4 | 0.000 | 0.000 | 0.000 | 100.000 | 100.000 | 101.802 |  |

## Interpretation

Small deltas mean the timing segment is stable and does not depend on which solver is used. Large deltas indicate that a solver trajectory can align to a different turn phase, usually because the circular motion is periodic and the trajectory shape is less distinctive.

This appendix is not used to choose a better timing offset for the main metric. It is a robustness check for the chosen unified timing reference.

