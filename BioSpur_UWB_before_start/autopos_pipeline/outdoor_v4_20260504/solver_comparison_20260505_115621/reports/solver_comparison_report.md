# V3/V4/V5 Solver Comparison - Outdoor 2026-05-04 Data

All outputs are contained in this timestamped comparison directory. Existing repository solver outputs were not modified.

## Data Inventory

- Inter-anchor sweep: 28 fused pairs from 28,000 raw rows.
- Tag-anchor TR source: ID01-ID31 captures.
- Valid TR rows available: see `positioning/positioning_by_capture.csv` and source summaries.

## Table 1: Calibration Quality

| Solver | Inter RMS all 28 | Inlier RMS <=30mm | N inlier | d_anchor range | d_tag values |
|---|---:|---:|---:|---:|---|
| V1 no delay | 53.5 | 12.1 | 23 | 0.0 | N/A |
| V3 Tukey | 63.8 | 5.6 | 24 | 116.5 | N/A |
| V4 inter-only | 47.0 | 13.3 | 24 | 64.0 | N/A |
| V4 joint roto | 50.7 | 13.2 | 20 | 63.6 | BS2DCE=0.0, BSDC91=50.1 |
| V4 joint all | 56.8 | 11.7 | 17 | 64.0 | BS2DCE=0.0, BSDC91=43.3, BSF66F=60.0 |

## Table 2: ID02 Center-Mid Positioning

| Solver | N | X std | Y std | Z std | 3D std |
|---|---:|---:|---:|---:|---:|
| V1 no delay | 601 | 18.1 | 25.5 | 42.1 | 52.4 |
| V3 Tukey | 601 | 17.5 | 25.9 | 43.7 | 53.8 |
| V4 inter-only | 601 | 18.6 | 26.2 | 44.5 | 54.9 |
| V4 joint roto | 601 | 19.2 | 25.8 | 48.0 | 57.8 |
| V4 joint all | 601 | 28.5 | 33.8 | 49.2 | 66.1 |
| **V3 concept paper** | **820** | **23.4** | **14.3** | **41.3** | **49.6** |
| **V1 concept paper** | **820** | **41.5** | **37.0** | **119.8** | **132.0** |

## Table 3: All-Capture Positioning Summary

| Solver | X med | Y med | Z med | 3D med | 3D best | 3D worst |
|---|---:|---:|---:|---:|---:|---:|
| V1 no delay | 31.0 | 42.6 | 54.9 | 74.8 | 47.8 | 257.1 |
| V3 Tukey | 31.4 | 45.1 | 59.2 | 79.3 | 46.6 | 207.1 |
| V4 inter-only | 31.5 | 43.8 | 55.2 | 75.1 | 48.6 | 268.7 |
| V4 joint roto | 29.5 | 40.5 | 56.5 | 76.7 | 56.8 | 273.0 |
| V4 joint all | 31.4 | 40.4 | 51.7 | 74.4 | 54.4 | 184.0 |

## Table 4: Center vs Edge Positioning

| Solver | Center median 3D | Edge median 3D | Ratio |
|---|---:|---:|---:|
| V1 no delay | 69.5 | 114.5 | 1.6 |
| V3 Tukey | 65.8 | 108.2 | 1.6 |
| V4 inter-only | 68.9 | 117.4 | 1.7 |
| V4 joint roto | 69.5 | 111.4 | 1.6 |
| V4 joint all | 68.8 | 98.8 | 1.4 |

## Table 5: Per-Anchor FIM Uncertainty

| Anchor | V3 sigma_3D | V4 interonly sigma_3D | V4 joint all sigma_3D |
|---|---:|---:|---:|
| A | 0.0 | 0.0 | 0.0 |
| B | 51.3 | 23.0 | 88.3 |
| C | 75.4 | 47.4 | 125.3 |
| D | 164.4 | 69.4 | 92.9 |
| E | 106.6 | 46.1 | 53.8 |
| F | 164.6 | 61.5 | 81.2 |
| G | 133.6 | 101.6 | 135.1 |
| H | 163.0 | 70.9 | 92.0 |

## Table 6: GDOP Prediction Accuracy

| Solver | Pearson r (GDOP vs 3D std) | sigma_range estimate |
|---|---:|---:|
| V1 no delay | 0.1 | 71.7 |
| V3 Tukey | 0.1 | 69.3 |
| V4 inter-only | 0.1 | 72.7 |
| V4 joint roto | 0.2 | 72.9 |
| V4 joint all | 0.1 | 65.8 |

## Solver Convergence and FIM Conditioning

| Solver | Converged | Notes | FIM condition number |
|---|---|---|---:|
| V1 no delay | Yes | Inter-anchor only, no delay variables | - |
| V3 Tukey | Yes | Reached max IRLS iterations; stable inlier residual core | 8.33e3 |
| V4 inter-only | Yes | `ftol` convergence | 1.23e3 |
| V4 joint roto | No | Hit max function evaluations; d_tag BSDC91 drifted to +50 mm | 1.66e3 |
| V4 joint all | No | Hit max function evaluations; d_tag BSF66F hit +60 mm bound | 5.36e3 |

## Anchor Count Ablation on ID02 (V4 inter-only layout)

| Case | Anchors | N | X std | Y std | Z std | 3D std |
|---|---|---:|---:|---:|---:|---:|
| all_8 | ABCDEFGH | 601 | 18.6 | 26.2 | 44.5 | 54.9 |
| no_H_7 | ABCDEFG | 601 | 16.7 | 23.4 | 39.4 | 48.7 |
| no_DH_6 | ABCEFG | 601 | 14.1 | 19.6 | 37.1 | 44.2 |
| no_DGH_5 | ABCEF | 601 | 14.6 | 18.4 | 34.8 | 42.0 |
| best4_BCEF | BCEF | 598 | 21.7 | 24.5 | 49.5 | 59.3 |

## Key Findings

1. On ID02, the new outdoor data broadly matches the original concept-paper V3
   precision level. V3 Tukey gives 53.8 mm 3D std on ID02, close to the concept
   paper V3 number of 49.6 mm. V1 no-delay is also much better outdoors
   (52.4 mm here vs 132.0 mm in the concept paper), which means this outdoor
   data set is cleaner and less delay/layout limited than the older concept
   setup.

2. V4 inter-only does not improve ID02 over V3 in this run. ID02 3D std is
   54.9 mm for V4 inter-only versus 53.8 mm for V3 Tukey. This suggests the
   current performance floor is dominated more by per-sweep tag-anchor ranging
   noise/outliers than by the inter-anchor layout model.

3. V4 joint with tag ranges is not yet a production calibration result. Both
   V4 joint roto and V4 joint all hit the maximum function evaluation limit.
   V4 joint all also drives BSF66F d_tag to the +60 mm bound, so the dense
   joint tag-factor model still needs staged admission, stronger priors, or
   better outlier filtering before it should be trusted as the final layout.

4. Across all 27 static captures, the median 3D std is similar for V1, V3, and
   V4 inter-only: 74.8 mm, 79.3 mm, and 75.1 mm respectively. V4 joint all has
   the best median at 74.4 mm but is not accepted as a calibration solve because
   it did not converge cleanly.

5. Center placements are consistently easier than edge placements. For V3
   Tukey, center median 3D std is 65.8 mm while edge median is 108.2 mm
   (ratio 1.6). This points to geometry/visibility and tag orientation effects
   rather than a single global layout error.

6. The FIM uncertainty ranking is consistent with weaker geometry for some
   upper/far anchors. For V4 inter-only, G has the largest sigma_3D (101.6),
   followed by H/D/F. This broadly matches the observed residual sensitivity
   around the upper/far side, though FIM values should be treated as relative
   indicators because of gauge and delay coupling.

7. GDOP alone does not predict measured precision well in this data set.
   Pearson r is only 0.1-0.2 across solvers, so the measured error is not just
   geometry. Range quality, antenna orientation, and anchor-specific outliers
   are significant.

8. Anchor-count ablation on ID02 shows that removing weak anchors can improve
   short-term precision: all 8 anchors give 54.9 mm 3D std, while removing H
   gives 48.7 mm and removing D/H gives 44.2 mm. This confirms that more anchors
   are not always better unless outlier handling is strong.

## Files

- Solves: `solves/*.json`
- FIM outputs: `fim/*.json`
- Positioning CSV: `positioning/positioning_by_capture.csv`
- Ablation: `positioning/id02_anchor_count_ablation_v4_interonly.json`
