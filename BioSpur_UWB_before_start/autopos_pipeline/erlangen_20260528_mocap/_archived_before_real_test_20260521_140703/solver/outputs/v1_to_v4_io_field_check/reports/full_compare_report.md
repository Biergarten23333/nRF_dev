# v1_to_v4_io_field_check Report

Mode: `1000`

## Version Summary

| Version | Tag delay | AutoPos RMS | AutoPos p95 | Static med | Static p95 | Roto ΔR RMS | Roto |ΔR| p95 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v1-old | 0.0 | 65.10 | 111.31 | 53.35 | 53.51 | 40.65 | 49.88 |
| v2 | 0.0 | 41.19 | 77.50 | 46.98 | 46.99 | 30.46 | 38.43 |
| v3-lite | 0.0 | 40.71 | 76.97 | 47.09 | 47.10 | 30.42 | 38.39 |
| v3-full | 0.0 | 66.34 | 140.12 | 45.10 | 45.41 | 28.70 | 37.45 |
| v4-io | 0.0 | 32.48 | 73.25 | 46.39 | 46.41 | 31.39 | 39.32 |

## Notes

- AutoPos RMS/p95 are inter-anchor layout residual metrics, not Tag RMS.
- Static validation uses all available ID01-ID24 captures; missing captures are recorded as missing.
- Roto validation uses every collected Roto_Test/ID* folder and both peers when present.
- Roto is reported as kinematic consistency: ΔR error and per-turn center repeatability. Circle-thickness diagnostics are not dynamic positioning accuracy.
- `v4-io-roto` and `v4-io-wand` are experimental soft-constraint branches built on top of `v4-io`.
- `v4-io-td` keeps the V4-io anchor layout fixed and scans one common static type-level tag delay; it is a downstream compensation test, not a factory calibration.