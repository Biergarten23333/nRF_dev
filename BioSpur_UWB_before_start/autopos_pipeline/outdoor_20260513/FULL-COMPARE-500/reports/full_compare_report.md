# FULL-COMPARE-500 Report

Mode: `500`

## Version Summary

| Version | AutoPos RMS | AutoPos p95 | Static med | Static p95 | Roto med | Roto p95 |
| --- | --- | --- | --- | --- | --- | --- |
| v1-old | 63.27 | 143.74 | 50.33 | 76.09 | 103.19 | 162.10 |
| v2 | 40.56 | 80.45 | 48.30 | 71.95 | 102.65 | 160.27 |
| v3-lite | 41.03 | 81.79 | 48.58 | 71.94 | 102.56 | 160.42 |
| v3-full | 61.84 | 178.31 | 55.77 | 90.77 | 108.70 | 158.14 |
| v4-io | 44.69 | 89.17 | 48.43 | 80.86 | 102.97 | 160.31 |
| v4-io-roto | 56.89 | 131.80 | 48.20 | 74.48 | 99.77 | 142.09 |
| v4-io-wand | 44.35 | 87.38 | 48.18 | 79.12 | 101.36 | 154.66 |
| v5 | 44.69 | 89.17 | 48.43 | 80.86 | 102.97 | 160.31 |

## Notes

- AutoPos RMS/p95 are inter-anchor layout residual metrics, not Tag RMS.
- Static validation uses all available ID01-ID24 captures; missing captures are recorded as missing.
- Roto validation uses every collected Roto_Test/ID* folder and both peers when present.
- `v4-io-roto` and `v4-io-wand` are experimental soft-constraint branches built on top of `v4-io`.