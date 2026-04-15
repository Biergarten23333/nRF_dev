# Holdout Evaluation (Floating Reference)

- layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_rerun_20260414_133932/v2/v2_fused/anchor_layout_v2_iterative.json`
- train_session (for solved_reference_m): `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/floating_ref115_train`
- holdout_session: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/floating_ref115_holdout`
- solved_reference_m: `2.055728, 0.806608, 0.771310`
- used_samples: `117` (skipped `123`)

## Overall Residual Stats (mm)

| metric | value |
|---|---:|
| mean | -148.298 |
| rms | 259.529 |
| p50(abs) | 241.181 |
| p95(abs) | 383.943 |
| max(abs) | 411.943 |

## Per-Anchor Residual RMS (mm)

| anchor | n | mean | rms | p95(abs) |
|---|---:|---:|---:|---:|
| A | 0 | n/a | n/a | n/a |
| B | 0 | n/a | n/a | n/a |
| C | 0 | n/a | n/a | n/a |
| D | 0 | n/a | n/a | n/a |
| E | 29 | -123.355 | 129.894 | 189.479 |
| F | 28 | 194.327 | 195.536 | 224.656 |
| G | 30 | -369.743 | 370.210 | 401.493 |
| H | 30 | -270.747 | 271.357 | 298.281 |
