# v1_to_v4_io_field_check Report

Mode: `1000`

## Version Summary

| Version | Tag delay | AutoPos RMS | AutoPos p95 | Static med | Static p95 | Roto ΔR RMS | Roto |ΔR| p95 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v1-old | 0.0 | 85.25 | 182.22 | 62.91 | 94.64 | 42.90 | 59.88 |
| v2 | 0.0 | 52.36 | 87.24 | 61.72 | 89.07 | 28.80 | 39.85 |
| v3-lite | 0.0 | 52.29 | 87.91 | 61.93 | 89.45 | 28.85 | 39.94 |
| v3-full | 0.0 | 102.50 | 258.59 | 64.30 | 95.09 | 36.13 | 52.25 |
| v4-io | 0.0 | 48.17 | 108.79 | 58.60 | 88.20 | 32.43 | 42.58 |

## Notes

- AutoPos RMS/p95 are inter-anchor layout residual metrics, not Tag RMS.
- Static validation uses all available ID01-ID24 captures; missing captures are recorded as missing.
- Roto validation uses every collected Roto_Test/ID* folder and both peers when present.
- Roto is reported as kinematic consistency: ΔR error and per-turn center repeatability. Circle-thickness diagnostics are not dynamic positioning accuracy.
- `v4-io-roto` and `v4-io-wand` are experimental soft-constraint branches built on top of `v4-io`.
- `v4-io-td` keeps the V4-io anchor layout fixed and scans one common static type-level tag delay; it is a downstream compensation test, not a factory calibration.