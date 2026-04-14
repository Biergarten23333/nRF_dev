# Holdout Evaluation (Floating Reference)

- layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/v3_lite/v3_fused/anchor_layout_v3_lite_iterative.json`
- train_session (for solved_reference_m): `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/floating_ref115_train`
- holdout_session: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/floating_ref115_holdout`
- solved_reference_m: `0.011353, 0.003749, 0.014705`
- used_samples: `117` (skipped `123`)

## Overall Residual Stats (mm)

| metric | value |
|---|---:|
| mean | -185.747 |
| rms | 211.205 |
| p50(abs) | 171.854 |
| p95(abs) | 354.202 |
| max(abs) | 366.002 |

## Per-Anchor Residual RMS (mm)

| anchor | n | mean | rms | p95(abs) |
|---|---:|---:|---:|---:|
| A | 0 | n/a | n/a | n/a |
| B | 0 | n/a | n/a | n/a |
| C | 0 | n/a | n/a | n/a |
| D | 0 | n/a | n/a | n/a |
| E | 29 | -91.537 | 100.175 | 157.661 |
| F | 28 | -108.970 | 111.112 | 145.291 |
| G | 30 | -198.654 | 199.522 | 230.404 |
| H | 30 | -335.569 | 336.060 | 363.102 |
