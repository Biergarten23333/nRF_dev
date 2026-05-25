# Outdoor 2026-05-13 Keep-k Robustness Summary

Generated with `scripts/robustness_keepk_outdoor.py` on the outdoor 2026-05-13 dataset.

## Scope

- Static: all 23 static captures.
- Roto: all 17 Roto captures, split into 34 tag tracks.
- Methods: T1, T2, T3.
- Repeats: 30 Monte Carlo repeats for each random keep-k condition.
- Note: keep-8 uses strict 8-anchor frames only. keep-7 to keep-4 randomly select that many anchors from frames with enough available anchors.

## Static Median Repeatability

Values are median per-capture standard deviations in mm.

| Method | keep-8 3D | keep-7 3D | keep-6 3D | keep-5 3D | keep-4 3D | keep-8 Z | keep-4 Z |
|---|---:|---:|---:|---:|---:|---:|---:|
| T1 | 50.3 | 55.1 | 83.0 | 109.5 | 169.2 | 41.3 | 131.0 |
| T2 | 51.8 | 56.2 | 83.5 | 113.5 | 171.1 | 42.0 | 136.7 |
| T3 | 48.6 | 53.6 | 73.9 | 98.0 | 129.9 | 39.8 | 99.8 |

## Roto Median Center Repeatability

Values are median per-track turn-center RMS deviations in mm.

| Method | keep-8 | keep-7 | keep-6 | keep-5 | keep-4 |
|---|---:|---:|---:|---:|---:|
| T1 | 22.0 | 31.5 | 52.2 | 91.9 | 137.0 |
| T2 | 22.1 | 32.6 | 54.5 | 92.5 | 142.1 |
| T3 | 22.6 | 29.8 | 42.1 | 59.3 | 82.0 |

## Interpretation

The static and Roto results tell the same story: 8-anchor and 7-anchor operation are much more stable, keep-6 is the transition region, and keep-5/keep-4 enter the large-error regime. T2 quality weighting does not materially fix the loss of geometry in this clean LOS dataset. T3 is now the dynamic-stable variant: it does not hard-reject anchors, and it improves Roto turn-center consistency by using soft residual memory plus a weak previous-position prior.

## Files

- `keepk_summary.csv`
- `keepk_detail.csv`
- `keepk_t1_t2_t3_repeatability.png`
- `roto_keepk_summary.csv`
- `roto_keepk_detail.csv`
- `roto_keepk_t1_t2_t3_center_repeatability.png`
