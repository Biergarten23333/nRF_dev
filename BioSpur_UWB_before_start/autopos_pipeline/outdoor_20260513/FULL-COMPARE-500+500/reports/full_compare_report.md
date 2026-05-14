# FULL-COMPARE-500+500 Report

Mode: `500+500`

## Version Summary

| Version | Tag delay | AutoPos RMS | AutoPos p95 | Static med | Static p95 | Roto med | Roto p95 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v1-old | 0.0 | 64.23 | 143.91 | 50.87 | 77.82 | 103.58 | 162.62 |
| v2 | 0.0 | 40.43 | 80.43 | 48.47 | 72.21 | 103.06 | 160.81 |
| v3-lite | 0.0 | 40.83 | 81.95 | 48.60 | 72.06 | 103.01 | 160.81 |
| v3-full | 0.0 | 64.91 | 177.98 | 52.48 | 85.58 | 106.73 | 157.80 |
| v4-io | 0.0 | 44.24 | 88.55 | 48.39 | 80.59 | 103.45 | 160.50 |
| v4-io-td | 3.5 | 44.24 | 88.55 | 47.97 | 81.76 | 102.31 | 157.79 |
| v4-io-roto | 0.0 | 57.53 | 133.71 | 48.02 | 75.10 | 99.97 | 142.10 |
| v4-io-wand | 0.0 | 44.20 | 88.50 | 48.15 | 79.26 | 101.88 | 154.75 |
| v5 | 0.0 | 44.24 | 88.55 | 48.39 | 80.59 | 103.45 | 160.50 |

## Notes

- AutoPos RMS/p95 are inter-anchor layout residual metrics, not Tag RMS.
- Static validation uses all available ID01-ID24 captures; missing captures are recorded as missing.
- Roto validation uses every collected Roto_Test/ID* folder and both peers when present.
- `v4-io-roto` and `v4-io-wand` are experimental soft-constraint branches built on top of `v4-io`.
- `v4-io-td` keeps the V4-io anchor layout fixed and scans one common static type-level tag delay; it is a downstream compensation test, not a factory calibration.
- Split layout stability across anchors: median=8.56 mm, p95=67.09 mm.