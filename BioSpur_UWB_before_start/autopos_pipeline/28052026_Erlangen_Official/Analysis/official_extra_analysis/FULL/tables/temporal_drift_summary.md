# Temporal / Thermal Drift Diagnostics

Source: static `tr_all.csv` raw per-anchor ranging rows.

Method: for each static session and anchor, fit `range_mm - median(range_mm)` against elapsed minutes. This is a raw-link drift diagnostic, not a tag-position solver result.

## Headline

- Static sessions analyzed: 24
- Anchor-session links analyzed: 192
- Median absolute drift slope: 1.54 mm/min
- P95 absolute drift slope: 16.21 mm/min
- Median absolute drift over capture: 3.07 mm
- P95 absolute drift over capture: 32.42 mm

Interpretation: compare drift-over-capture to static tag repeatability. A few-mm drift is negligible; tens of mm would be report-relevant.

## Per-Anchor Summary

| anchor | sessions | median_abs_slope_mm_min | p95_abs_slope_mm_min | median_abs_drift_mm | p95_abs_drift_mm | median_MAD_mm | worst_session | worst_slope_mm_min |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| A | 24 | 0.71 | 2.44 | 1.43 | 4.89 | 22.24 | ID06 | 3.39 |
| B | 24 | 1.36 | 3.50 | 2.73 | 7.01 | 23.72 | ID09 | -6.31 |
| C | 24 | 1.02 | 4.64 | 2.03 | 9.28 | 22.24 | ID06 | -6.88 |
| D | 24 | 1.44 | 4.71 | 2.88 | 9.42 | 22.98 | ID16 | 9.49 |
| E | 24 | 0.83 | 3.03 | 1.65 | 6.06 | 24.46 | ID23 | 3.52 |
| F | 24 | 6.76 | 19.23 | 13.52 | 38.46 | 50.41 | ID08 | -20.98 |
| G | 24 | 9.68 | 24.67 | 19.35 | 49.31 | 25.95 | ID01 | -25.19 |
| H | 24 | 2.44 | 8.60 | 4.88 | 17.19 | 29.65 | ID01 | -23.92 |

## Worst 10 Links By Absolute Slope

| session | anchor | slope_mm_min | drift_capture_mm | r2 | p95_abs_residual_mm | facing | height |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| ID01 | G | -25.19 | -50.38 | 0.015 | 431.0 | ABEF | low |
| ID15 | G | -25.03 | -50.06 | 0.011 | 439.0 | CDHG | mid |
| ID01 | H | -23.92 | -47.83 | 0.009 | 98.0 | ABEF | low |
| ID13 | G | 22.59 | 45.11 | 0.019 | 58.0 | ABEF | mid |
| ID08 | F | -20.98 | -41.96 | 0.021 | 111.1 | CDHG | mid |
| ID03 | F | -19.66 | -39.31 | 0.018 | 101.6 | ABEF | high |
| ID16 | G | 18.52 | 37.03 | 0.008 | 436.0 | ADHE | mid |
| ID10 | G | 18.29 | 36.59 | 0.012 | 75.0 | ADHE | low |
| ID07 | F | -16.80 | -33.59 | 0.011 | 150.0 | CDHG | low |
| ID09 | F | 16.66 | 33.32 | 0.006 | 206.0 | CDHG | high |

## Row Accounting

- Total rows: 230544
- Rows used: 228265
- excluded_invalid: 2279
- excluded_bad_status: 0
- excluded_bad_anchor: 0
- excluded_missing_time_or_range: 0
- excluded_nonpositive_range: 0
