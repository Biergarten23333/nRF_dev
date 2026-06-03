# v1_to_v4_io_field_check Report

Mode: `1000`

## Version Summary

| Version | Tag delay | AutoPos RMS | AutoPos p95 | Static med | Static p95 | Roto ΔR RMS | Roto |ΔR| p95 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v1-old | 0.0 | 238.82 | 379.42 | nan | nan | nan | nan |
| v2 | 0.0 | 111.17 | 217.43 | nan | nan | nan | nan |
| v3-lite | 0.0 | 113.08 | 219.50 | nan | nan | nan | nan |
| v3-full | 0.0 | 136.08 | 312.99 | nan | nan | nan | nan |
| v4-io | 0.0 | 132.31 | 205.09 | nan | nan | nan | nan |

## Notes

- AutoPos RMS/p95 are inter-anchor layout residual metrics, not Tag RMS.
- Static validation uses all available ID01-ID24 captures; missing captures are recorded as missing.
- Roto validation uses every collected Roto_Test/ID* folder and both peers when present.
- Roto is reported as kinematic consistency: ΔR error and per-turn center repeatability. Circle-thickness diagnostics are not dynamic positioning accuracy.
- `v4-io-roto` and `v4-io-wand` are experimental soft-constraint branches built on top of `v4-io`.
- `v4-io-td` keeps the V4-io anchor layout fixed and scans one common static type-level tag delay; it is a downstream compensation test, not a factory calibration.