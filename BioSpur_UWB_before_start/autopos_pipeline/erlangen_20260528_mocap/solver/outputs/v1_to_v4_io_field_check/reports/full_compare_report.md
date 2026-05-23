# v1_to_v4_io_field_check Report

Mode: `1000`

## Version Summary

| Version | Tag delay | AutoPos RMS | AutoPos p95 | Static med | Static p95 | Roto ΔR RMS | Roto |ΔR| p95 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v1-old | 0.0 | 124.75 | 281.19 | nan | nan | 6.51 | 7.63 |
| v2 | 0.0 | 101.93 | 189.26 | nan | nan | 10.98 | 11.26 |
| v3-lite | 0.0 | 100.86 | 188.43 | nan | nan | 10.05 | 10.32 |
| v3-full | 0.0 | 150.21 | 384.22 | nan | nan | 16.17 | 16.18 |
| v4-io | 0.0 | 123.44 | 279.06 | nan | nan | 11.67 | 12.13 |

## Notes

- AutoPos RMS/p95 are inter-anchor layout residual metrics, not Tag RMS.
- Static validation uses all available ID01-ID24 captures; missing captures are recorded as missing.
- Roto validation uses every collected Roto_Test/ID* folder and both peers when present.
- Roto is reported as kinematic consistency: ΔR error and per-turn center repeatability. Circle-thickness diagnostics are not dynamic positioning accuracy.
- `v4-io-roto` and `v4-io-wand` are experimental soft-constraint branches built on top of `v4-io`.
- `v4-io-td` keeps the V4-io anchor layout fixed and scans one common static type-level tag delay; it is a downstream compensation test, not a factory calibration.