# FULL-COMPARE-1000 Report

Mode: `1000`

## Version Summary

| Version | AutoPos RMS | AutoPos p95 | Static med | Static p95 | Roto med | Roto p95 |
| --- | --- | --- | --- | --- | --- | --- |
| v1-old | 64.23 | 143.92 | 50.75 | 79.52 | 103.18 | 154.75 |
| v2 | 40.43 | 80.36 | 48.65 | 71.04 | 103.09 | 155.95 |
| v3-lite | 40.82 | 82.01 | 48.69 | 70.91 | 103.07 | 155.94 |
| v3-full | 66.42 | 182.57 | 54.94 | 84.07 | 108.47 | 156.03 |
| v4-io | 44.34 | 87.75 | 49.25 | 81.65 | 103.82 | 157.55 |
| v4-io-roto | 57.91 | 134.07 | 48.05 | 71.87 | 100.23 | 138.23 |
| v4-io-wand | 44.24 | 87.60 | 48.58 | 77.30 | 101.41 | 151.98 |
| v5 | 44.34 | 87.75 | 49.25 | 81.65 | 103.82 | 157.55 |

## Notes

- AutoPos RMS/p95 are inter-anchor layout residual metrics, not Tag RMS.
- Static validation uses all available ID01-ID24 captures; missing captures are recorded as missing.
- Roto validation uses every collected Roto_Test/ID* folder and both peers when present.
- `v4-io-roto` and `v4-io-wand` are experimental soft-constraint branches built on top of `v4-io`.