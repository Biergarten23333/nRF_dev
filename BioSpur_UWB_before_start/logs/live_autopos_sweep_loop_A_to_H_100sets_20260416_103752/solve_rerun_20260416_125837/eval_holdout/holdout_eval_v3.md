# Holdout Evaluation (Floating Reference)

- layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/live_autopos_sweep_loop_A_to_H_100sets_20260416_103752/solve_rerun_20260416_125837/v3_lite/v3_fused/anchor_layout_v3_lite_iterative.json`
- train_session (for solved_reference_m): `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/floating_ref115_train`
- holdout_session: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/floating_ref115_holdout`
- solved_reference_m: `1.893745, 1.139590, 0.707362`
- used_samples: `117` (skipped `123`)

## Overall Residual Stats (mm)

| metric | value |
|---|---:|
| mean | -283.689 |
| rms | 306.350 |
| p50(abs) | 339.029 |
| p95(abs) | 381.175 |
| max(abs) | 413.962 |

## Per-Anchor Residual RMS (mm)

| anchor | n | mean | rms | p95(abs) |
|---|---:|---:|---:|---:|
| A | 0 | n/a | n/a | n/a |
| B | 0 | n/a | n/a | n/a |
| C | 0 | n/a | n/a | n/a |
| D | 0 | n/a | n/a | n/a |
| E | 29 | -343.238 | 345.641 | 409.362 |
| F | 28 | -83.050 | 85.840 | 119.371 |
| G | 30 | -345.482 | 345.982 | 377.232 |
| H | 30 | -351.595 | 352.065 | 379.129 |
