# Holdout Evaluation (Floating Reference)

- layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/live_autopos_sweep_loop_A_to_H_100sets_20260416_103752/solve_rerun_20260416_125837/v2/v2_fused/anchor_layout_v2_iterative.json`
- train_session (for solved_reference_m): `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/floating_ref115_train`
- holdout_session: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/floating_ref115_holdout`
- solved_reference_m: `1.886848, 1.134451, 0.709141`
- used_samples: `117` (skipped `123`)

## Overall Residual Stats (mm)

| metric | value |
|---|---:|
| mean | -278.346 |
| rms | 300.598 |
| p50(abs) | 334.098 |
| p95(abs) | 377.355 |
| max(abs) | 398.211 |

## Per-Anchor Residual RMS (mm)

| anchor | n | mean | rms | p95(abs) |
|---|---:|---:|---:|---:|
| A | 0 | n/a | n/a | n/a |
| B | 0 | n/a | n/a | n/a |
| C | 0 | n/a | n/a | n/a |
| D | 0 | n/a | n/a | n/a |
| E | 29 | -327.487 | 330.005 | 393.611 |
| F | 28 | -82.052 | 84.876 | 118.374 |
| G | 30 | -342.898 | 343.402 | 374.648 |
| H | 30 | -349.498 | 349.970 | 377.032 |
