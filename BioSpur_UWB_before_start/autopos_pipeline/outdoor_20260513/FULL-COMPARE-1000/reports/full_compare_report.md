# FULL-COMPARE-1000 Report

Mode: `1000`

## Version Summary

| Version | Tag delay | AutoPos RMS | AutoPos p95 | Static med | Static p95 | Roto ΔR RMS | Roto |ΔR| p95 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v1-old | 0.0 | 64.23 | 143.92 | 50.75 | 79.52 | 32.92 | 54.15 |
| v2 | 0.0 | 40.43 | 80.36 | 48.65 | 71.04 | 32.72 | 50.70 |
| v3-lite | 0.0 | 40.82 | 82.01 | 48.69 | 70.91 | 32.74 | 50.75 |
| v3-full | 0.0 | 66.42 | 182.57 | 54.94 | 84.07 | 31.25 | 48.37 |
| v4-io | 0.0 | 44.34 | 87.75 | 49.25 | 81.65 | 32.29 | 50.51 |
| v4-io-td | 3.0 | 44.34 | 87.75 | 48.91 | 82.81 | 31.61 | 49.14 |
| v4-io-roto | 0.0 | 57.91 | 134.07 | 48.05 | 71.87 | 29.89 | 45.36 |
| v4-io-wand | 0.0 | 44.24 | 87.60 | 48.58 | 77.30 | 31.67 | 49.76 |
| v5 | 0.0 | 44.34 | 87.75 | 49.25 | 81.65 | 32.29 | 50.51 |

## Notes

- AutoPos RMS/p95 are inter-anchor layout residual metrics, not Tag RMS.
- Static validation uses all available ID01-ID24 captures; missing captures are recorded as missing.
- Roto validation uses every collected Roto_Test/ID* folder and both peers when present.
- Roto is reported as kinematic consistency: ΔR error and per-turn center repeatability. Circle-thickness diagnostics are not dynamic positioning accuracy.
- `v4-io-roto` and `v4-io-wand` are experimental soft-constraint branches built on top of `v4-io`.
- `v4-io-td` keeps the V4-io anchor layout fixed and scans one common static type-level tag delay; it is a downstream compensation test, not a factory calibration.