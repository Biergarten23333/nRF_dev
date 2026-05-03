# Post Physical Fix 500-set Inter-anchor Pair Analysis

- Current pairs: `autopos_pipeline/logs/a17_powercycle_full_sweep_500set_20260503_125216/pairs_all.csv`
- Layout reference: `autopos_pipeline/solve_v4_fusion/anchor_layout_v4_interonly_500set.json`
- Previous comparison pairs: `autopos_pipeline/logs/a17_powercycle_full_sweep_500set_20260503_123205/pairs_all.csv`
- Error convention: `median_measured - geometric_distance_from_layout`.
- Note: `n=1000` means 500 sets with both directions aggregated.

## Top Pair Errors

| Pair | n | median | std | geom | error | previous error | delta vs previous |
|---|---:|---:|---:|---:|---:|---:|---:|
| A-B | 1000 | 4443.0 | 24.6 | 4726.1 | -283.1 | -292.1 | 9.0 |
| A-G | 1000 | 5569.0 | 38.5 | 5762.7 | -193.7 | -190.7 | -3.0 |
| B-D | 1000 | 5411.0 | 38.3 | 5592.4 | -181.4 | -172.4 | -9.0 |
| E-H | 1000 | 2817.0 | 33.8 | 2960.2 | -143.2 | -137.2 | -6.0 |
| C-D | 1000 | 4670.0 | 39.6 | 4532.1 | 137.9 | 134.9 | 3.0 |
| A-C | 1000 | 5513.0 | 38.0 | 5630.5 | -117.5 | -144.5 | 27.0 |
| C-H | 1000 | 4829.0 | 36.9 | 4944.7 | -115.7 | -109.7 | -6.0 |
| B-H | 1000 | 6077.0 | 1252.3 | 5971.0 | 106.0 | 151.0 | -45.0 |
| B-E | 1000 | 4672.0 | 125.3 | 4581.1 | 90.9 | 493.9 | -403.0 |
| A-D | 1000 | 2768.0 | 48.2 | 2686.1 | 81.9 | 64.9 | 17.0 |
| E-G | 1000 | 5397.0 | 128.8 | 5324.2 | 72.8 | 83.8 | -11.0 |
| A-H | 1000 | 3237.5 | 166.4 | 3171.8 | 65.7 | 78.2 | -12.5 |
| A-F | 1000 | 4843.0 | 42.1 | 4898.4 | -55.4 | -195.4 | 140.0 |
| G-H | 1000 | 4570.0 | 46.0 | 4522.6 | 47.4 | 46.4 | 1.0 |
| D-E | 1000 | 3336.0 | 28.0 | 3296.3 | 39.7 | 43.7 | -4.0 |
| D-H | 1000 | 1564.0 | 42.2 | 1601.4 | -37.4 | -41.4 | 4.0 |
| C-G | 1000 | 1643.0 | 31.8 | 1606.3 | 36.7 | 37.7 | -1.0 |
| C-F | 1000 | 3958.0 | 28.4 | 3987.4 | -29.4 | -25.4 | -4.0 |
| F-G | 1000 | 3774.0 | 27.2 | 3751.0 | 23.0 | 14.0 | 9.0 |
| F-H | 1000 | 5670.0 | 24.9 | 5651.7 | 18.3 | 28.3 | -10.0 |
| D-F | 1000 | 5719.0 | 1254.6 | 5704.5 | 14.5 | 12.5 | 2.0 |
| D-G | 1000 | 4667.0 | 29.2 | 4657.1 | 9.9 | 15.9 | -6.0 |
| B-C | 1000 | 3740.0 | 40.4 | 3749.7 | -9.7 | -8.7 | -1.0 |
| C-E | 1000 | 5645.0 | 35.1 | 5637.7 | 7.3 | 11.3 | -4.0 |
| B-G | 1000 | 4156.0 | 32.1 | 4149.9 | 6.1 | 17.1 | -11.0 |
| A-E | 1000 | 1670.0 | 22.8 | 1665.0 | 5.0 | -9.0 | 14.0 |
| B-F | 1000 | 1560.0 | 38.4 | 1556.4 | 3.6 | -35.4 | 39.0 |
| E-F | 1000 | 4212.0 | 26.0 | 4214.4 | -2.4 | 17.6 | -20.0 |

## Requested Focus Pairs

| Pair | current error | current std | previous error | previous std | verdict |
|---|---:|---:|---:|---:|---|
| B-D | -181.4 | 38.3 | -172.4 | 55.7 | roughly unchanged |
| B-E | 90.9 | 125.3 | 493.9 | 207.7 | improved by 403mm |
| A-F | -55.4 | 42.1 | -195.4 | 89.9 | improved by 140mm |
| A-C | -117.5 | 38.0 | -144.5 | 34.1 | roughly unchanged |

## Per-anchor Mean Signed Error

| Anchor | mean error | median error | mean abs | max abs | neg/pos |
|---|---:|---:|---:|---:|---:|
| A | -71.0 | -55.4 | 114.6 | 283.1 | 4/3 |
| B | -38.2 | 3.6 | 97.3 | 283.1 | 3/4 |
| H | -8.4 | 18.3 | 76.2 | 143.2 | 3/4 |
| D | 9.3 | 14.5 | 71.8 | 181.4 | 2/5 |
| C | -12.9 | -9.7 | 64.9 | 137.9 | 4/3 |
| G | 0.3 | 23.0 | 55.7 | 193.7 | 1/6 |
| E | 10.0 | 7.3 | 51.6 | 143.2 | 2/5 |
| F | -4.0 | 3.6 | 21.0 | 55.4 | 3/4 |

## Robust Inter-only Free Solve

All 28 pairs were included. Robust loss was used to soft-downweight large residual pairs instead of manually selecting a potentially under-constrained clean subset.

| Run | RMS mm | median abs mm | max abs mm | top residuals | Verdict |
|---|---:|---:|---:|---|---|
| soft_l1 f_scale=2 (~60mm transition) | 66.6 | 22.9 | 161.9 | B-D +162, C-H +140, A-F -137, B-H -129 | usable diagnostic, not 30-40mm clean layout |
| soft_l1 f_scale=1 (~30mm transition) | 68.4 | 18.0 | 173.2 | B-D +173, A-F -162, C-H +156, B-H -117 | usable diagnostic, not 30-40mm clean layout |
| cauchy f_scale=1 apos init | 106.6 | 10.9 | 298.7 | A-B +299, C-D -265, B-D +245, A-G +220 | too aggressive / init-sensitive, not reliable |
| cauchy f_scale=1 mds init | 126.0 | 4.4 | 365.3 | C-G -365, D-E -313, B-F +301, A-D -210 | too aggressive / init-sensitive, not reliable |

Conclusion: robust loss improves the typical pair fit (`median_abs` down to ~18-23mm), but the all-pair RMS remains ~66-68mm because stable bad edges such as B-D, C-H, A-F, B-H, C-D still disagree with the rest of the geometry. This is better than plain 78mm but not the expected 30-40mm clean-LOS layout. Do not treat this as a final APOS layout yet.

## Huber 30mm All-pair Inter-only Solve

Ran all 28 pairs with `loss=huber`, 30mm transition, no manual pair exclusion. APOS and MDS initialization converged to the same solution.

| Metric | Value |
|---|---:|
| all-pair RMS | 75.4mm |
| all-pair median abs | 18.3mm |
| inlier RMS, abs residual <=30mm | 15.0mm over 19 pairs |
| inlier RMS, abs residual <=50mm | 17.4mm over 20 pairs |
| inlier RMS, abs residual <=100mm | 35.7mm over 25 pairs |

Largest Huber outliers after solve:

```text
B-D err=+211.6mm std=38.3mm
A-F err=-206.0mm std=42.1mm
C-H err=+199.8mm std=36.9mm
E-G err=-90.7mm std=128.8mm
B-H err=-82.7mm std=1252.3mm
E-H err=+64.3mm std=33.8mm
C-D err=-62.7mm std=39.6mm
D-E err=-52.0mm std=28.0mm
```

Interpretation: Huber successfully finds a very self-consistent main geometry: the 20-pair inlier set is ~17mm RMS. The poor all-pair RMS is caused by a small set of stable system outliers, especially B-D/A-F/C-H. This is stronger evidence than the earlier soft_l1 run that the main geometry is usable if outlier pairs are handled explicitly in downstream V4.
