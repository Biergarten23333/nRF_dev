# V4 Joint Solve Checkpoint - 2026-05-03

## Implementation

Rewrote:

`autopos_pipeline/solve_v4_fusion/solve_v4.py`

Backup of the previous solver:

`autopos_pipeline/solve_v4_fusion/solve_v4_old.py`

The new solver packs all variables into one parameter vector and runs one `scipy.optimize.least_squares` call:

- `anchor_xyz[8]`
- `d_anchor[1..7]`, with `d_anchor[0]=0`
- `d_tag[1..2]`, with `d_tag[0]=0`
- `tag_pos[N]`

Tag range residual now includes both anchor and tag delay:

```text
||tag_pos[k] - anchor_xyz[i]|| + d_anchor[i] + d_tag[tag] - range_mm
```

Gauge:

- A at origin
- B on x-axis
- C.z=0 to fix the remaining rotation about x-axis

Delay bounds were added after observing that global Huber loss also softens delay priors. Default bound:

```text
|d_anchor| <= 30mm
|d_tag| <= 30mm
```

## Data

Stationary 30s TR capture:

`SS-TWR/alt-SS-TWR/broadcast/logs/tag_delay_cal_stationary_30s_20260503_103617/recv_20260503_103618`

Prepared V4 data:

`autopos_pipeline/logs/v4_data_stationary30_tr_20260503.json`

Stats:

```text
inter_anchor_ranges = 28
tag_anchor_ranges = 6936
tag_position_initializers = 901
```

Raw inter-anchor expanded data was also prepared:

`autopos_pipeline/logs/v4_data_stationary30_tr_rawinter_20260503.json`

Stats:

```text
inter_anchor_ranges = 5594
tag_anchor_ranges = 6936
```

The raw-inter run is currently too slow with numerical Jacobian because the Python residual loop is not vectorized.

## Results

### Joint solve, sigma_inter=30, sigma_tag=80

Output:

`autopos_pipeline/solve_v4_fusion/anchor_layout_v4_joint_stationary30_sub10_20260503.json`

```text
inter_anchor_rms = 257.17mm
tag_anchor_rms = 83.39mm
d_tag:
  BS2DCE = 0.0mm
  BSDC91 = +7.0mm
  BSF66F = +3.8mm
```

Interpretation:

Tag delays are reasonable, but tag ranges dominate the 28 median inter-anchor observations and pull the anchor geometry away from the inter-anchor sweep.

### Joint solve, stronger inter weight, sigma_inter=5, sigma_tag=80

Output:

`autopos_pipeline/solve_v4_fusion/anchor_layout_v4_joint_stationary30_medinter_sub10_si5_20260503.json`

```text
inter_anchor_rms = 123.56mm
tag_anchor_rms = 124.70mm
d_tag:
  BS2DCE = 0.0mm
  BSDC91 = +6.1mm
  BSF66F = +45.1mm
```

Problem:

Without hard delay bounds, global Huber lets delay priors become weak. H escaped to `-134.7mm`, and BSF66F d_tag escaped to `+45.1mm`.

### Joint solve, sigma_inter=5, sigma_tag=80, delay bound ±30mm

Output:

`autopos_pipeline/solve_v4_fusion/anchor_layout_v4_joint_stationary30_medinter_sub10_si5_bound30_20260503.json`

```text
inter_anchor_rms = 126.10mm
tag_anchor_rms = 124.64mm
d_anchor:
  F = -30.0mm
  H = -30.0mm
d_tag:
  BS2DCE = 0.0mm
  BSDC91 = +5.4mm
  BSF66F = +30.0mm
```

Problem:

Several delays hit the bound, so the model is still trying to use delay to absorb larger geometry/range inconsistency.

Top inter-anchor errors remain large:

```text
B-H = -374.8mm
A-F = +321.2mm
A-G = +251.3mm
A-B = -192.1mm
B-E = +184.7mm
```

## Verdict

The joint-solve architecture is now implemented correctly: `d_tag` is in the same optimization as anchor positions and anchor delays.

But the current solve is not yet a valid layout result:

- Best bounded joint run still has `inter_anchor_rms=126mm`, above the target `<=95mm`.
- BSF66F d_tag hits `+30mm` bound.
- F/H anchor delays hit `-30mm` bound.
- The same bad inter-anchor pairs remain dominant.

Do not push any V4 joint output to Tags yet.

## Next Technical Step

The remaining blocker is not missing `d_tag`; it is robust handling of inconsistent observations.

Recommended next implementation:

1. Vectorize residual/Jacobian path enough to run raw inter-anchor observations, or keep medians but scale pair sigma by set count.
2. Add per-observation robust weighting/reporting that does not let delay priors get softened by Huber.
3. Consider bounds or priors on anchor movement from the current physical layout, not a box prior, just a weak "do not move 500mm unless data truly demands it" prior.
4. Re-run joint solve and require:
   - `inter_anchor_rms <= 95mm`
   - all `d_anchor` and `d_tag` inside ±15mm ideally, none at ±30mm bound
   - 4-anchor vs 8-anchor position delta improves substantially


## 2026-05-03 500-set Inter Init Retest
Ran V4 joint solve with the 500-set inter-anchor free layout as initialization. Also regenerated V4 data with the new 500-set `pairs_all.csv` so the inter-anchor observations themselves match the new sweep.
| Run | inter_rms_mm | tag_rms_mm | d_anchor range mm | d_tag values mm | Verdict |
|---|---:|---:|---|---|---|
| 500set init + old inter data / i30 t80 | 257.17 | 83.39 | -1.8..1.6 | BS2DCE=0.0, BSDC91=7.0, BSF66F=3.8 | fail: tag pulls inter too far |
| 500set inter data / i30 t80 | 235.14 | 83.63 | -2.1..3.0 | BS2DCE=0.0, BSDC91=7.9, BSF66F=6.4 | fail: tag pulls inter too far |
| 500set inter data / i5 t80 | 89.06 | 134.31 | -30.0..30.0 | BS2DCE=0.0, BSDC91=-1.0, BSF66F=30.0 | fail: delay bound hit |
| 500set inter data / i30 t150 | 162.01 | 93.21 | -3.1..1.5 | BS2DCE=0.0, BSDC91=5.3, BSF66F=4.1 | fail: tag pulls inter too far |

Conclusion: 500-set init alone does not fix V4. With real 500-set inter observations, the solver still shows a conflict between stationary TR tag-anchor ranges and inter-anchor ranges. Strong inter weighting recovers inter RMS but forces delay variables to bounds and makes tag residuals worse. Therefore APOS/DTAG should not be pushed from these V4 joint outputs yet.

## 2026-05-03 Elevation Residual Diagnostic
Purpose: test whether stationary tag-anchor residuals are explained by elevation-dependent antenna delay. Fixed the 500-set inter-only anchor layout, then fit only one stationary position plus one scalar d_tag per Tag. Residual is `geom + d_tag - range`.
Overall residual-vs-elevation correlation: `0.161`. This is weak, not a clean monotonic elevation effect.
| Tag | fit_rms_mm | fitted_d_tag_mm | corr(resid,elev) | largest mean residuals |
|---|---:|---:|---:|---|
| BS2DCE | 95.5 | 90.3 | 0.152 | H +192mm @ +15deg, F +126mm @ +14deg, D +72mm @ -16deg, A -68mm @ -15deg |
| BSDC91 | 204.4 | 1.5 | 0.293 | C -441mm @ -24deg, E -292mm @ +12deg, G +122mm @ +24deg, A +79mm @ -11deg |
| BSF66F | 71.3 | 55.9 | -0.007 | F -80mm @ +9deg, B +59mm @ -16deg, E +49mm @ +15deg, A -49mm @ -22deg |

Interpretation: the pattern is anchor/tag-combination specific rather than purely elevation-specific. BSDC91 is the clearest bad case: C is `-441mm` and E is `-292mm`, while D/H are near zero. If this were only elevation delay, anchors at similar elevation would move together; they do not. BSF66F has almost zero correlation (`-0.007`).

Actionable check: inspect physical orientation and line-of-sight around C/E/G/H first, especially relative to BSDC91. Also check whether C/E board orientation differs from the others. Do not deploy V4 layout from this data yet.

## 2026-05-03 Huber 125216 Joint Solve Retest

Used all 28 inter-anchor pairs from `a17_powercycle_full_sweep_500set_20260503_125216`, Huber inter-only layout as init, and stationary30 TR data. V4 solver now accepts `--loss`, `--f-scale`, `--sigma-inter`, and `--sigma-tag`; `--f-scale 30` is interpreted as a 30mm inter-anchor threshold and converted to normalized residual units.

| Run | all inter RMS | inter <=50mm RMS/n | tag RMS | tag <=100mm RMS/n | d_anchor range | d_tag | Verdict |
|---|---:|---:|---:|---:|---|---|---|
| Huber125216 i15/t80 | 125.8 | 23.0/18 | 96.4 | 53.8/559 | -14.0..8.8 | BS2DCE=0.0, BSDC91=16.4, BSF66F=19.2 | fail: d_tag > ±10mm; do not push APOS/DTAG |
| Huber125216 i15/t150 | 91.8 | 14.0/19 | 111.6 | 55.4/480 | -9.3..5.2 | BS2DCE=0.0, BSDC91=18.0, BSF66F=18.7 | fail: d_tag > ±10mm; do not push APOS/DTAG |

Conclusion: Huber preserves a strong inter-anchor inlier core, but stationary TR still forces per-tag delay estimates around +18mm and leaves large BSDC91 E/G/H residuals. This does not satisfy the planned `d_tag <= ±10mm` gate, so the resulting V4 layouts should not be deployed.

## 2026-05-03 Huber APOS Hardware Validation

After power cycle, APOS verified successfully to all three Tags using `anchor_layout_interonly_huber_125216.json`. Capture: `SS-TWR/alt-SS-TWR/broadcast/logs/motion_3tag_huber125216_apos_20260503_171112/recv_20260503_171113`. Anchor preflight was ready=8/8. Capture produced `positions_all=1800` in 60s, balanced at ~600 rows per Tag.

### Firmware TS RMS comparison

| Tag | Previous median/mean/p95 RMS | Huber APOS median/mean/p95 RMS | Median delta |
|---|---:|---:|---:|
| BS2DCE | 105.0/118.1/223.0 | 134.0/131.8/155.0 | +29.0 |
| BSDC91 | 105.0/122.0/234.0 | 171.0/167.3/195.0 | +66.0 |
| BSF66F | 156.0/158.2/196.0 | 112.5/109.4/138.0 | -43.5 |

Interpretation: Huber APOS strongly improves BSF66F and lowers p95 for all tags, but worsens median RMS for both Roto tags, especially BSDC91. This is not a universal improvement.

### Host-side d_tag replay on TR raw ranges

Used d_tag from the i15/t80 joint solve only in offline host replay: BS2DCE=0mm, BSDC91=+16.388mm, BSF66F=+19.228mm. Positive d_tag was subtracted from measured TR ranges.

| Tag | no correction median/mean/p95 RMS | d_tag corrected median/mean/p95 RMS | Median delta |
|---|---:|---:|---:|
| BS2DCE | 138.8/137.1/163.7 | 138.8/137.1/163.7 | +0.0 |
| BSDC91 | 181.9/178.9/216.6 | 175.3/172.1/207.0 | -6.6 |
| BSF66F | 122.9/118.8/151.8 | 114.3/110.7/143.9 | -8.6 |

Conclusion: d_tag appears directionally useful for BSF66F/BSDC91, but the effect is modest (~7-9mm median RMS improvement), not enough to explain the larger Roto-tag residual issue. Do not firmware-integrate DTAG yet; keep it host-side until layout/tag residual behavior is clearer.
