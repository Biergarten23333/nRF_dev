# V3/V4 Solver Verification Report

Output directory: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_v4_20260504/solver_verify_20260505_121740`

## Table A: Fusion Method Impact

| Pair | V1 avg | V3 MVUE | Diff | sigma_ab | sigma_ba | Asymmetry |
| --- | --- | --- | --- | --- | --- | --- |
| A-B | 4529.9 | 4529.8 | 0.0 | 20.0 | 20.8 | 32.5 |
| A-C | 5352.5 | 5348.3 | 4.2 | 35.6 | 27.4 | 25.0 |
| A-D | 3025.1 | 3026.9 | -1.7 | 16.3 | 23.7 | 16.0 |
| A-E | 1493.8 | 1492.9 | 0.9 | 16.3 | 17.0 | 6.5 |
| A-F | 4911.7 | 4911.5 | 0.2 | 31.1 | 25.2 | 14.0 |
| A-G | 5515.8 | 5516.4 | -0.6 | 17.8 | 22.2 | 17.0 |
| A-H | 3258.1 | 3258.6 | -0.5 | 19.3 | 20.0 | 17.5 |
| B-C | 3102.7 | 3102.7 | 0.1 | 40.8 | 22.2 | 1.5 |
| B-D | 5340.5 | 5339.0 | 1.5 | 40.0 | 38.5 | 2.0 |
| B-E | 4758.5 | 4760.0 | -1.5 | 20.8 | 26.7 | 0.0 |
| B-F | 1853.6 | 1855.3 | -1.6 | 25.2 | 32.6 | 2.0 |
| B-G | 3295.9 | 3296.0 | -0.1 | 29.7 | 29.7 | 6.0 |
| B-H | 5591.7 | 5591.8 | -0.1 | 26.7 | 25.2 | 6.0 |
| C-D | 4454.1 | 4454.4 | -0.3 | 44.5 | 54.1 | 11.5 |
| C-E | 5571.4 | 5564.2 | 7.2 | 20.8 | 42.3 | 27.0 |
| C-F | 3356.3 | 3357.7 | -1.4 | 23.7 | 31.1 | 2.0 |
| C-G | 1519.2 | 1522.0 | -2.8 | 29.7 | 42.3 | 12.0 |
| C-H | 4807.6 | 4806.6 | 1.0 | 40.0 | 44.5 | 8.0 |
| D-E | 3403.1 | 3404.5 | -1.4 | 28.2 | 28.2 | 1.0 |
| D-F | 5694.9 | 5694.9 | -0.0 | 22.2 | 17.8 | 8.0 |
| D-G | 4676.0 | 4677.4 | -1.5 | 20.8 | 23.7 | 1.0 |
| D-H | 1546.6 | 1545.8 | 0.7 | 43.0 | 56.3 | 5.0 |
| E-F | 4471.7 | 4473.4 | -1.7 | 19.3 | 22.2 | 13.0 |
| E-G | 5367.3 | 5368.1 | -0.7 | 20.8 | 23.0 | 11.0 |
| E-H | 2969.6 | 2968.6 | 1.0 | 31.1 | 35.6 | 1.0 |
| F-G | 3070.7 | 3072.0 | -1.2 | 13.3 | 20.8 | 7.0 |
| F-H | 5417.9 | 5418.1 | -0.2 | 26.7 | 31.1 | 5.0 |
| G-H | 4529.2 | 4528.4 | 0.7 | 19.3 | 17.8 | 14.0 |


Max abs(V1 avg - V3 MVUE) = **7.16 mm**. Only one pair exceeds 5 mm, so the fusion method can shift the layout slightly, but it cannot by itself explain the concept-paper V1/V3 gap of roughly 80 mm in 3D positioning.


## Table B: V3 Debug Summary

| Metric | Value |
| --- | --- |
| Converged | No |
| Iterations | 30 |
| Final delay range (max-min) | 39.3 |
| Max abs(delay) | 35.1 |
| N Tukey-rejected pairs | 8 |
| Inlier RMS (<=30mm) | 7.0 |
| All-pair RMS | 76.4 |


## Table C: Delay Comparison

| Anchor | V3 Tukey delay | V4 inter-only delay | Difference |
| --- | --- | --- | --- |
| A | 0.0 | 0.0 | 0.0 |
| B | 11.1 | 22.3 | -11.2 |
| C | 26.6 | 20.4 | 6.2 |
| D | 1.1 | 11.6 | -10.5 |
| E | -4.2 | -5.9 | 1.8 |
| F | 35.1 | 3.8 | 31.3 |
| G | 18.9 | 60.0 | -41.1 |
| H | 10.3 | 9.6 | 0.7 |


## Table D: ID02 Positioning - All Methods

| Layout | Eval method | N | X | Y | Z | 3D |
| --- | --- | --- | --- | --- | --- | --- |
| V1 (V1-avg fusion) | 8anc Huber | 601 | 17.6 | 24.0 | 41.9 | 51.4 |
| V1 (V1-avg fusion) | 8anc L2 | 601 | 23.8 | 33.4 | 59.4 | 72.2 |
| V1 (V1-avg fusion) | QA select | 601 | 52.8 | 77.7 | 126.6 | 157.6 |
| V3-lite (MVUE, no delay) | 8anc Huber | 601 | 17.6 | 23.9 | 41.6 | 51.2 |
| V3-full (MVUE + delay) | 8anc Huber | 601 | 17.9 | 26.5 | 42.3 | 53.0 |
| V3-full (MVUE + delay) | 8anc L2 | 601 | 23.0 | 35.0 | 58.9 | 72.3 |
| V3-full (MVUE + delay) | QA select | 601 | 58.7 | 81.1 | 105.8 | 145.6 |
| V4 inter-only | 8anc Huber | 601 | 18.8 | 27.1 | 44.3 | 55.2 |
| V4 inter-only | QA select | 601 | 69.2 | 96.6 | 131.4 | 177.1 |
| Concept V1 | QA select | 820 | 41.5 | 37.0 | 119.8 | 132.0 |
| Concept V3 | QA select | 820 | 23.4 | 14.3 | 41.3 | 49.6 |


## Table E: Ablation Matrix

| Factor changed | 3D std | Delta from baseline |
| --- | --- | --- |
| Baseline: V1-avg, 8anc Huber | 51.4 | ref |
| Change fusion: V3-MVUE, 8anc Huber | 51.2 | -0.2 |
| Add delay est: V3-full, 8anc Huber | 53.0 | 1.7 |
| Change eval: V3-full, QA select | 145.6 | 94.3 |
| Remove D/H: V3-full, 6anc Huber | 44.6 | -6.7 |


## Notes

- V1 in this run uses simple averaging only; V3-lite/V3-full use MAD+MVUE.

- QA selection tries size-4 subsets with at least two lower and two upper anchors, plus the full visible set.

- The V3 iteration log and sorted residuals are saved in `debug/`.

## Key Findings

1. V1 simple averaging and V3 MAD+MVUE fusion are almost identical on this outdoor 500-set sweep. The largest pair-level shift is C-E at 7.2 mm; most pairs are within 0-2 mm. This means the new outdoor raw sweep is already clean enough that robust fusion is not the dominant improvement source.
2. V3-full alternating Tukey did not converge in 30 iterations. It ended with delay range 39.3 mm and max abs(delay) 35.1 mm, much better than the previous 116.5 mm range, but still not a clean physical antenna-delay solution.
3. Tukey became too aggressive late in the run: residual sigma collapsed to the 1 mm floor and 8 pairs were fully rejected, including B-C with -382 mm. That is a warning that this V3 implementation is fitting an over-pruned graph, not a stable all-pair geometry.
4. ID02 with all-8-anchor Huber is already near the concept V3 result: V1 avg = 51.4 mm, V3-lite = 51.2 mm, V3-full = 53.0 mm, concept V3 = 49.6 mm. The current outdoor data no longer reproduces the bad concept V1 baseline.
5. The quality-aware selector implemented here performs worse on ID02 because it picks the lowest per-sweep residual subset, which can be geometrically unstable. That differs from a production on-board selector that also uses quality/history/gating. Residual-minimization alone is not a faithful concept-paper selector.
6. Removing D/H from V3-full improves ID02 all-8-equivalent Huber evaluation to 44.6 mm, suggesting D/H still inject enough noisy geometry into this center-mid capture to hurt position stability.
7. Main conclusion: the suspicious previous comparison was not only a V1 fusion fairness issue. On this outdoor dataset, the simple V1 layout is already good; the remaining floor looks more like live TR/range noise plus weak or bad anchors than an inter-anchor fusion problem.
