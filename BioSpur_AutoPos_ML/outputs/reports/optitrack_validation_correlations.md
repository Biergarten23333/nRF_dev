# OptiTrack Validation Correlations

Generated: `2026-05-30T00:15:46.178532+00:00`

## Summary

- Layout validation rows: `10`
- Layout correlation rows: `24`
- Session validation rows: `192`
- Session correlation rows: `126`
- No GPU is used by this script.

## Strongest Layout-Level Correlations

| X feature | Y error | N | Pearson r | Spearman r |
|---|---|---:|---:|---:|
| `layout_eval_roto_abs_deltaR_p95_mm` | `opti_err_3d_p95_mm` | 5 | 0.942041 | 1.000000 |
| `layout_eval_roto_abs_deltaR_p95_mm` | `opti_err_3d_rms_mm` | 5 | 0.986388 | 1.000000 |
| `layout_eval_autopos_p95_mm` | `opti_err_3d_p95_mm` | 5 | 0.684413 | 0.900000 |
| `layout_eval_autopos_p95_mm` | `opti_err_3d_rms_mm` | 5 | 0.668102 | 0.900000 |
| `layout_eval_static_p95_mm` | `opti_err_3d_median_mm` | 5 | 0.900227 | 0.900000 |
| `layout_eval_static_p95_mm` | `opti_err_vertical_median_mm` | 5 | 0.896318 | 0.900000 |
| `layout_eval_autopos_rms_mm` | `opti_err_3d_median_mm` | 5 | 0.797426 | 0.800000 |
| `layout_eval_autopos_rms_mm` | `opti_err_vertical_median_mm` | 5 | 0.790126 | 0.800000 |
| `layout_eval_roto_abs_deltaR_p95_mm` | `opti_err_3d_median_mm` | 5 | 0.981783 | 0.700000 |
| `layout_eval_roto_abs_deltaR_p95_mm` | `opti_err_vertical_median_mm` | 5 | 0.977515 | 0.700000 |

## Strongest Session-Level Correlations

| Version | Eval set | Grid | X feature | Y error | N | Pearson r | Spearman r |
|---|---|---:|---|---|---:|---:|---:|
| `v4-io` | `all8` | 100.0 | `cond` | `err_3d_mm` | 24 | -0.663868 | -0.774463 |
| `v4-io` | `all8` | 50.0 | `gdop` | `err_3d_mm` | 24 | -0.646621 | -0.773913 |
| `v4-io` | `all8` | 50.0 | `vdop` | `err_3d_mm` | 24 | -0.710009 | -0.770435 |
| `v4-io` | `all8` | 50.0 | `cond` | `err_3d_mm` | 24 | -0.651172 | -0.768696 |
| `v4-io` | `all8` | 100.0 | `gdop` | `err_3d_mm` | 24 | -0.656751 | -0.759645 |
| `v4-io` | `all8` | 50.0 | `vdop` | `err_vertical_mm` | 24 | -0.720040 | -0.758261 |
| `v4-io` | `all8` | 50.0 | `gdop` | `err_vertical_mm` | 24 | -0.648356 | -0.751304 |
| `v4-io` | `all8` | 50.0 | `cond` | `err_vertical_mm` | 24 | -0.644275 | -0.747826 |
| `v4-io` | `all8` | 100.0 | `cond` | `err_vertical_mm` | 24 | -0.658612 | -0.742648 |
| `v4-io` | `all8` | 100.0 | `gdop` | `err_vertical_mm` | 24 | -0.660000 | -0.739161 |
| `v4-io` | `all8` | 100.0 | `vdop` | `err_3d_mm` | 24 | -0.716895 | -0.736982 |
| `v4-io` | `all8` | 100.0 | `vdop` | `err_vertical_mm` | 24 | -0.728443 | -0.725215 |

## Interpretation Guardrails

- Layout-level correlations use only 5 solver versions for `all8`; treat them as directional hints.
- Session-level DOP correlations currently apply to Erlangen `v4-io` DOP grids.
- These tables are for feature calibration, not supervised model training.
