# Extended Mechanism Ablation Summary

Generated: 2026-06-17T23:39:27

## Synthesis Table

| item | hypothesis_tested | verdict | key_number |
| --- | --- | --- | --- |
| 01 | Range-residual D_tag changes by height tier | mixed | V4_CV4 spread 11.8 mm; V5_CV5 spread 7.4 mm; Vicon_Ccm spread 14.1 mm |
| 02 | Elevation angle explains rho | mixed | best abs-angle R2 0.107 (V5_CV5) |
| 03 | Effective D_tag differs by anchor | supported | V5 anchor spread 131.0 mm |
| 04 | NLOS exclusions materially shift D_tag | supported | V5 exclude D,F delta -15.3 mm |
| 05 | LOO fold D_tag correlates with held-out metadata | mixed | best height R2 0.038 (V4_CV4) |
| 06 | Joint V4-to-V5 morph has a lower diagnostic valley | supported | global min alpha 0.15, D 52.0, median 56.4 mm |
| 07 | Common anchor shift and tag shift are partly interchangeable | supported | best anchor shift 100.0, tag shift -60.0 |
| 08 | Facing group changes D_tag | supported | facing metadata present |
| 09 | Board-frame incidence explains rho | skipped | board orientation input unavailable |
| 10 | Low-order antenna model beats scalar D_tag | supported | V5 best M2 median 54.8 mm |
| 11 | Calibration quality improves with set size | supported | k=4 stratified mean 69.0 mm |
| 12 | Calibration design matters | supported | best V5_CV5 stratified_LMH median 40.8 mm |
| 13 | D_tag criterion optimum varies across CV folds | supported | max spread 18.0 mm (V5_CV5 d_min_median) |
| 14 | Vicon delay regularization changes oracle tail | mixed | best C_Vicon_cm_e10 median 63.4 mm |
| 15 | Anchor common mode is layer-dependent | not supported | upper-lower c diff -8.6 mm |
| 16 | Residual variance has structured factors | supported | top factor anchor_id fraction 0.090 |
| 17 | Historical rho weighting/removal improves solves | supported | best V4_CV4 inverse_rms median 50.9 mm |
| 18 | Static residuals drift over acquisition time | mixed | D_tag early/mid/late 53.0, 59.6, 34.3 mm |
| 19 | ROTO tags have device-specific D_tag | supported | median per-tag D_tag spread 24.9 mm |
| 20 | Dynamic residual correlates with motion state | mixed | speed-residual R2 0.000 |
| 21 | Lower range percentiles mitigate NLOS | supported | V5 best p30 median 47.5 mm |
| 22 | Single anchors have D_tag leverage | supported | max delta -8.1 mm removing F (Vicon_Ccm) |
| 23 | Differential ranging cancels common-mode errors | mixed | median differential/absolute RMS ratio 1.416 |
| 24 | Residual distribution shape differs by layer | mixed | skew upper 1.76, lower 1.60 |

## Tag Delay Physical Interpretation

Range-residual D_tag is computed directly from measured range minus geometric truth minus anchor delay, so it is the closest table here to a physical delay estimate. Position-optimal D_tag can move away from that value because it also absorbs solver geometry and vertical-error tradeoffs. Items 2 and 10 separate elevation-dependent residual structure from a pure scalar tag-delay interpretation.

## Cancellation Valley Characterization

Item 6 morphs the V4/V5 geometry and delay models together in the common Vicon-evaluation frame, so the alpha endpoints remain comparable to the transfer-matrix endpoints. Item 7 then isolates the common-mode ambiguity by shifting all V5 anchor delays against the scalar tag delay.

## NLOS / Link Quality

Items 17, 22, 23, and 24 should be read together: downweighting/removal tests deployability, jackknife shows D_tag leverage, differential ranging checks common-mode cancellation, and distribution shape exposes positive-tail NLOS.

## Calibration Transfer

The learning-curve and design-ablation tables use calibration-set D_tag estimates from range residuals. Item 11 uses interpolation over an actual solved D_tag grid to keep the 500-iteration sweep tractable while avoiding fabricated metrics.

## V4 vs V5 Fair Comparison

V4+C_V4+D_LOO remains the empirical static median winner on this 24-position campaign. V5 has metric-correct anchor geometry and generally reduces geometry-induced D_tag aliasing. In-sample sweep optima are diagnostic only, and LOO_CV is cross-validated on this same campaign rather than an independent external holdout.

## Runtime

| item | elapsed_s | mean_cpu_percent | max_cpu_percent | physical_cores | logical_cores | workers |
| --- | --- | --- | --- | --- | --- | --- |
| Item 01 | 0.010 | 38.500 | 38.500 | 6 | 12 | 6 |
| Item 02 | 0.724 | 21.900 | 21.900 | 6 | 12 | 6 |
| Item 03 | 0.016 | 21.100 | 21.100 | 6 | 12 | 6 |
| Item 04 | 0.087 | 21.200 | 21.200 | 6 | 12 | 6 |
| Item 05 | 0.074 | 20.500 | 20.500 | 6 | 12 | 6 |
| Item 06 | 1493.992 | 82.061 | 99.900 | 6 | 12 | 6 |
| Item 07 | 649.824 | 71.072 | 100.000 | 6 | 12 | 6 |
| Item 08 | 132.009 | 68.427 | 86.500 | 6 | 12 | 6 |
| Item 09 | 0.000 | 0.000 | 0.000 | 6 | 12 | 6 |
| Item 10 | 9.301 | 63.700 | 85.700 | 6 | 12 | 6 |
| Item 11 | 141.373 | 60.183 | 73.300 | 6 | 12 | 6 |
| Item 12 | 11.964 | 44.600 | 67.600 | 6 | 12 | 6 |
| Item 13 | 141.286 | 67.965 | 77.700 | 6 | 12 | 6 |
| Item 14 | 9.548 | 38.733 | 51.700 | 6 | 12 | 6 |
| Item 15 | 4.585 | 32.600 | 32.600 | 6 | 12 | 6 |
| Item 16 | 0.020 | 28.000 | 28.000 | 6 | 12 | 6 |
| Item 17 | 16.976 | 42.525 | 57.200 | 6 | 12 | 6 |
| Item 18 | 0.022 | 11.100 | 11.100 | 6 | 12 | 6 |
| Item 19 | 3.555 | 74.388 | 100.000 | 6 | 12 | 6 |
| Item 20 | 66.188 | 73.624 | 100.000 | 6 | 12 | 6 |
| Item 21 | 0.427 | 37.000 | 37.000 | 6 | 12 | 6 |
| Item 22 | 0.058 | 33.800 | 33.800 | 6 | 12 | 6 |
| Item 23 | 0.028 | 25.800 | 25.800 | 6 | 12 | 6 |
| Item 24 | 0.083 | 33.300 | 33.300 | 6 | 12 | 6 |

## Runtime Summary Block

```text
=== EXTENDED MECHANISM ABLATION - RUNTIME SUMMARY ===
Machine: i7-8700K 6C/12T 32GB
Workers: 6 (process pool), GPU idle

Item 01: 0.0 s
Item 02: 0.7 s
Item 03: 0.0 s
Item 04: 0.1 s
Item 05: 0.1 s
Item 06: 1494.0 s
Item 07: 649.8 s
Item 08: 132.0 s
Item 09: 0.0 s
Item 10: 9.3 s
Item 11: 141.4 s
Item 12: 12.0 s
Item 13: 141.3 s
Item 14: 9.5 s
Item 15: 4.6 s
Item 16: 0.0 s
Item 17: 17.0 s
Item 18: 0.0 s
Item 19: 3.6 s
Item 20: 66.2 s
Item 21: 0.4 s
Item 22: 0.1 s
Item 23: 0.0 s
Item 24: 0.1 s

Total wall time: 2714.7 s
Mean CPU%: 42.2%
Max CPU%: 100.0%
```
