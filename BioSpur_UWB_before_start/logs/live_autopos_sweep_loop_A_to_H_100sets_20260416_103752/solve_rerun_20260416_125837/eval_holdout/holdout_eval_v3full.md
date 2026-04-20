# Holdout Evaluation (Floating Reference)

- layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/live_autopos_sweep_loop_A_to_H_100sets_20260416_103752/solve_rerun_20260416_125837/v3_full_tag115_cm/anchor_layout_v3_full.json`
- train_session (for solved_reference_m): `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/floating_ref115_train`
- holdout_session: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/floating_ref115_holdout`
- solved_reference_m: `2.401863, 1.060733, 0.750219`
- used_samples: `117` (skipped `123`)

## Overall Residual Stats (mm)

| metric | value |
|---|---:|
| mean | -143.661 |
| rms | 206.974 |
| p50(abs) | 86.736 |
| p95(abs) | 323.108 |
| max(abs) | 340.097 |

## Per-Anchor Residual RMS (mm)

| anchor | n | mean | rms | p95(abs) |
|---|---:|---:|---:|---:|
| A | 0 | n/a | n/a | n/a |
| B | 0 | n/a | n/a | n/a |
| C | 0 | n/a | n/a | n/a |
| D | 0 | n/a | n/a | n/a |
| E | 29 | -1.540 | 40.723 | 71.864 |
| F | 28 | -290.026 | 290.837 | 326.347 |
| G | 30 | -292.950 | 293.539 | 324.700 |
| H | 30 | 4.853 | 18.809 | 32.619 |
