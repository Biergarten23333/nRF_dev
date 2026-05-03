# A17 500-Set Inter-Anchor Analysis - 2026-05-03

## Inputs

Sweep:

`autopos_pipeline/logs/a17_powercycle_full_sweep_500set_20260503_115808`

Extracted pairs:

`autopos_pipeline/logs/a17_powercycle_full_sweep_500set_20260503_115808/pairs_all.csv`

Rows:

```text
28000 rows = 28 pairs x 1000 directional observations
```

Current comparison layout:

`SS-TWR/alt-SS-TWR/broadcast/logs/apos_verified_b61_all3_apos_to_20260503_004436/summary.json`

## Raw Pair Pattern

The B-D / B-E pattern is still present in the 500-set sweep.

Focused pairs, compared against current APOS geometry:

| Pair | n | median | std | geom | error |
|---|---:|---:|---:|---:|---:|
| B-E | 1000 | 4487.0 | 244.6 | 4983.9 | -496.9 |
| B-D | 1000 | 5417.0 | 31.0 | 5870.6 | -453.6 |
| D-E | 1000 | 3354.0 | 1266.2 | 3319.8 | +34.2 |
| B-H | 1000 | 6107.0 | 57.0 | 6151.5 | -44.5 |
| A-F | 1000 | 4922.0 | 97.8 | 4676.6 | +245.4 |
| A-C | 1000 | 5608.0 | 30.2 | 5451.6 | +156.4 |
| A-B | 1000 | 4850.0 | 91.7 | 4738.0 | +112.0 |
| E-F | 1000 | 4198.0 | 22.1 | 4350.4 | -152.4 |
| A-G | 1000 | 5596.0 | 45.5 | 5626.5 | -30.5 |

Per-anchor signed error summary:

| Anchor | mean error | median error | mean abs | max abs | neg/pos |
|---|---:|---:|---:|---:|---:|
| B | -140.9 | -42.9 | 172.9 | 496.9 | 6/1 |
| E | -97.8 | -33.9 | 122.4 | 496.9 | 5/2 |
| A | +62.8 | +51.8 | 98.8 | 245.4 | 3/4 |
| D | -62.0 | -22.5 | 97.7 | 453.6 | 4/3 |
| C | +20.7 | -4.5 | 56.4 | 156.4 | 4/3 |
| F | +7.7 | -4.5 | 78.1 | 245.4 | 4/3 |
| G | +6.0 | +23.6 | 34.0 | 51.9 | 3/4 |
| H | -3.2 | +2.8 | 30.2 | 57.7 | 3/4 |

Interpretation:

- B is the strongest signed-error anchor relative to the current APOS layout.
- B-H is not a bad raw pair; it is close to the current APOS geometry.
- B-D and B-E remain the dominant disagreement with the current APOS geometry.
- D-E has a huge standard deviation because of rare extreme outliers, but its median is close.

## 100-Set vs 500-Set

| Pair | 100-set median/std | 500-set median/std | Comment |
|---|---:|---:|---|
| B-E | 4343.5 / 46.2 | 4487.0 / 244.6 | Still short, now less short but noisier |
| B-D | 5404.0 / 35.1 | 5417.0 / 31.0 | Very stable, same short pattern |
| B-H | 6193.0 / 32.7 | 6107.0 / 57.0 | Still near geometry |
| A-F | 4398.0 / 135.1 | 4922.0 / 97.8 | Changed sign vs current layout |
| A-C | 5634.0 / 256.8 | 5608.0 / 30.2 | Now stable long |
| A-B | 4617.5 / 22.0 | 4850.0 / 91.7 | Changed sign, noisier |

This confirms B-D is not a transient issue. B-E also remains inconsistent but has a wider distribution in the 500-set sweep.

## Inter-Only Free Solve

New helper:

`autopos_pipeline/scripts/solve_inter_anchor_free.py`

Output:

`autopos_pipeline/logs/a17_powercycle_full_sweep_500set_20260503_115808/inter_anchor_free_solve.json`

The solver was run with two initializations:

- Current APOS layout
- Metric MDS initialization from pair medians

Both converged to the same linear least-squares solution, which means this is not an initialization/local-minimum artifact.

### Linear Loss

```text
rms = 78.14 mm
median_abs = 33.03 mm
max_abs = 175.41 mm
```

Solved anchors:

| Anchor | x | y | z |
|---|---:|---:|---:|
| A | 0.0 | 0.0 | 0.0 |
| B | 4726.1 | 0.0 | 0.0 |
| C | 4229.6 | 3716.7 | 0.0 |
| D | -182.4 | 2679.9 | 0.0 |
| E | 436.1 | -134.2 | 1601.3 |
| F | 4645.3 | 67.6 | 1552.8 |
| G | 4054.4 | 3771.5 | 1595.8 |
| H | -344.6 | 2721.2 | 1592.6 |

Top residuals after free solve:

```text
B-D  +175.4 mm
A-G  +166.7 mm
B-H  -136.0 mm
E-H  +135.2 mm
A-B  -123.9 mm
```

### Soft-L1 Loss

```text
rms = 81.87 mm
median_abs = 19.39 mm
max_abs = 217.13 mm
```

Soft-L1 improves the median residual but leaves larger top outliers.

## Conclusion

The 500-set sweep supports the idea that the previous APOS layout geometry is wrong around B/D/E rather than all of those raw ranges being simply bad.

The inter-anchor-only free solve reaches `78 mm RMS`, better than the previous 100-set inter-only result around `92 mm`. This is a stronger anchor-only layout candidate than the previous APOS layout, but it is not yet perfect.

Do not exclude B-H. The raw B-H pair is not the source of the problem.

Recommended next step:

Use the 500-set inter-only free solve as the initial layout for V4 joint solve, then add tag TR data with delay bounds. This should be a better starting point than the current APOS pushed layout.

