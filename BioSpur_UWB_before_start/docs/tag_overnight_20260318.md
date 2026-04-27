# Overnight Tag Stability Run (2026-03-18)

Session artifacts:

- `logs/tag_sessions/tag_760186127_20260318_011144/summary.json`
- `logs/tag_sessions/tag_760186127_20260318_011144/positions.csv`
- `logs/tag_sessions/tag_760186127_20260318_011144/ranges.csv`
- `logs/tag_sessions/tag_760186127_20260318_011144/raw.log`

## High-Level Result

The overnight run was successful and stable.

- Duration: `7200 s`
- Position samples: `811`
- Summary samples used: `809` (first `2` sweeps skipped)
- Chosen anchor subset: always `B,D,E,H`
- Position mean: `(1733.24, 3217.85, 785.31) mm`
- Position stddev: `(7.51, 9.20, 16.37) mm`
- Residual mean: `rms=8.99 mm`, `max=12.40 mm`

## Stability

First 50 summarized samples:

- mean position: `(1753.06, 3233.46, 771.52) mm`
- stddev: `(7.60, 10.71, 23.78) mm`

Last 50 summarized samples:

- mean position: `(1734.32, 3220.76, 794.12) mm`
- stddev: `(3.95, 5.41, 9.96) mm`

Drift from first 50 to last 50:

- `dx = -18.74 mm`
- `dy = -12.70 mm`
- `dz = +22.60 mm`

This indicates no large overnight drift. The location estimate stayed within a few centimeters over the full run.

## Range Stability By Anchor

Representative filtered range statistics:

- `A`: mean `2966.66 mm`, std `9.56 mm`
- `B`: mean `3899.35 mm`, std `13.33 mm`
- `C`: mean `2812.30 mm`, std `11.21 mm`
- `D`: mean `2082.35 mm`, std `6.69 mm`
- `E`: mean `3390.31 mm`, std `9.22 mm`
- `F`: mean `3460.04 mm`, std `12.36 mm`
- `G`: mean `2739.65 mm`, std `8.15 mm`
- `H`: mean `1990.82 mm`, std `12.43 mm`

The best runtime localization subset remained `B,D,E,H`, which is consistent with the current non-coplanar selection logic.

## Recommendation

The anchor layout and current single-tag runtime are stable enough to move on to absolute validation.

Recommended next step:

1. Run `ground-truth` tests at known points.
2. Compare solved position vs measured true point.
3. Only after that, decide whether to:
   - tune antenna delays further
   - adjust anchor layout slightly
   - start multi-tag scheduling
