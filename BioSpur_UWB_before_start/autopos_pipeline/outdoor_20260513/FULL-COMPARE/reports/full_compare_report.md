# AutoPos FULL-COMPARE 20260513

This comparison uses one clean progression line on the same 2026-05-13 data: V1, V2, V3-lite, V3-full, V4.

## Version Definition

| Folder | Solver label | Meaning |
|---|---|---|
| `v1` | `AutoPos V1` | simple bidirectional mean + no-delay geometry solve |
| `v2` | `AutoPos V2` | weighted/IVW pair fusion + no-delay iterative solve |
| `v3lite` | `V3-lite` | MAD/MVUE robust fusion + no-delay layout |
| `v3full` | `V3-full` | robust fusion + Tukey/median-style per-anchor delay estimation |
| `v4` | `V4-interonly` | Huber bounded-delay production-style solve |

## Main Summary

| Version | Inter RMS | Static median | Static best/worst | Roto median RMS | Roto best/worst RMS |
|---|---:|---:|---:|---:|---:|
| v1 | 64.23 | 44.61 | 31.75/72.32 | 115.45 | 66.99/276.45 |
| v2 | 40.43 | 48.80 | 33.72/94.07 | 103.49 | 67.25/195.27 |
| v3lite | 40.82 | 48.83 | 33.70/93.77 | 103.43 | 67.31/195.30 |
| v3full | 66.42 | 55.87 | 40.05/137.16 | 112.87 | 72.21/196.76 |
| v4 | 44.34 | 48.98 | 34.27/104.28 | 106.88 | 65.94/176.91 |

## Notes on RotArm / V4 from `main.pdf`

- In the old concept, V4 meant RotArm Z-injection with known rotating-arm radii.
- The current `v4` folder here is a production-style Huber bounded-delay solver, not the original RotArm-injection V4.
- Roto-arm data is still useful as validation/diagnosis, but it is not necessary to include old RotArm-injection as a main solver unless we implement that constraint cleanly.

## Files

- `tables/full_compare_summary.csv`
- `tables/static_all_versions.csv`
- `tables/roto_all_versions.csv`
- `figures/progression_static_roto.png`
- `figures/progression_inter_rms.png`
- `figures/static_distribution_by_version.png`
