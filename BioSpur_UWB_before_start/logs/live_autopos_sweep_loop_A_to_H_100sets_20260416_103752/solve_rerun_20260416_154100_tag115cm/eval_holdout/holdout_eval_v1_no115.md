# Holdout Evaluation (Floating Reference)

- layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/live_autopos_sweep_loop_A_to_H_100sets_20260416_103752/solve_rerun_20260416_154100_tag115cm/eval_holdout/no115_train_ref_layouts/v1_no115_with_train_ref.json`
- train_session (for solved_reference_m): `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_cm_fresh_20260416_154100/split_for_v3full/floating_ref115_train`
- holdout_session: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_cm_fresh_20260416_154100/split_for_v3full/floating_ref115_holdout`
- solved_reference_m: `1.210916, 2.177267, 0.760068`
- used_samples: `156` (skipped `4`)

## Overall Residual Stats (mm)

| metric | value |
|---|---:|
| mean | -11.444 |
| rms | 59.761 |
| p50(abs) | 38.703 |
| p95(abs) | 122.208 |
| max(abs) | 156.958 |

## Per-Anchor Residual RMS (mm)

| anchor | n | mean | rms | p95(abs) |
|---|---:|---:|---:|---:|
| A | 19 | 21.499 | 26.798 | 43.909 |
| B | 20 | -16.883 | 35.456 | 69.033 |
| C | 19 | -63.877 | 68.366 | 98.672 |
| D | 20 | -8.527 | 20.189 | 33.477 |
| E | 20 | 111.208 | 114.948 | 155.058 |
| F | 19 | -59.348 | 61.346 | 77.775 |
| G | 20 | -37.051 | 46.115 | 87.951 |
| H | 19 | -43.545 | 47.626 | 68.108 |
