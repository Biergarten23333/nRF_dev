# No-CIR vs CIR-Weighted AutoPos / Static BSF66F Result

Generated from the overnight 2026-06-08 run.

## Scope

This compares the current no-CIR AutoPos layout against a compact-CIR-derived weighted layout.

- Anchor layout metric: V3-box edge RMS against the known box-edge prior used by the layout checker.
- Static tag metric: BSF66F CAL_STATIC ranges, solved with all 8 anchors by grouping adjacent calibration sweeps.
- No OptiTrack is involved in this home test. The static tag metric is repeatability/self-consistency and solver residual, not absolute ground-truth error.

## Layout

| case | sweep/session | layout JSON | V3-box edge RMS mm |
| --- | --- | --- | ---: |
| no-CIR 1000-set | `mainline_autopos_no_tag_solve_30min_20260607_234520` | `solve_v3_box/anchor_layout_v3_box.json` | 102.46 |
| compact-CIR same-sweep unweighted | `mainline_autopos_with_cir_compact_1000_20260608_overnight` | `layout_compare/baseline_v3_box/anchor_layout_v3_box.json` | 112.99 |
| compact-CIR weighted | `mainline_autopos_with_cir_compact_1000_20260608_overnight` | `layout_compare/cir_weighted_v3_box/anchor_layout_v3_box.json` | 114.61 |

Result: the current CIR-derived pair weighting does not improve the layout. It slightly worsens the same-sweep layout and is worse than the existing no-CIR 1000-set.

## Static BSF66F

Capture:

- session: `static_bsf66f_cal_static_no_cir_vs_cir_20260608_fix2`
- firmware marker: `codex-tr2-static-calpmode-a8-20260608-fix2`
- TDMA profile: `static`, `PMODE=4`, 5 Hz
- raw CM rows: 9012 valid rows, all 8 anchors seen
- solved static samples: 500, using `--static-min-anchors 8`

| layout used for BSF66F solve | static 3D RMS mm | static 3D P95 mm | mean solve residual RMS mm | solve residual P95 mm | mean position mm |
| --- | ---: | ---: | ---: | ---: | --- |
| no-CIR layout | 66.42 | 120.08 | 184.26 | 227.42 | x=2330.16, y=936.27, z=1219.26 |
| compact-CIR weighted layout | 70.51 | 127.16 | 190.24 | 233.55 | x=2342.89, y=932.47, z=1184.28 |

Result: the CIR-weighted layout is also worse for the static BSF66F self-consistency metric.

## Interpretation

The current compact-CIR weighting rule is not validated as an improvement. On this run it makes both checked objectives worse:

- layout RMS: 102.46 mm no-CIR vs 114.61 mm CIR-weighted
- BSF66F static repeatability: 66.42 mm no-CIR vs 70.51 mm CIR-weighted
- BSF66F range-solve residual: 184.26 mm no-CIR vs 190.24 mm CIR-weighted

This does not mean CIR is useless. It means the first simple weighting rule is not yet the right mapping from CIR quality to solver weight. CIR should next be treated as a diagnostic/ranking signal first, then calibrated with a parameter sweep against a held-out objective.

## Output Files

- `BSF66F/cm.csv`
- `cm_all.csv`
- `analysis_no_cir_layout_8anchor.json`
- `analysis_no_cir_layout_8anchor.md`
- `analysis_cir_weighted_layout_8anchor.json`
- `analysis_cir_weighted_layout_8anchor.md`
