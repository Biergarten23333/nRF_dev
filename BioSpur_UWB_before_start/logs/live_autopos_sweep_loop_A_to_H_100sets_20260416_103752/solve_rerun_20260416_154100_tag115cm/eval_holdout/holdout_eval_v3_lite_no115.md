# Holdout Evaluation (Floating Reference)

- layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/live_autopos_sweep_loop_A_to_H_100sets_20260416_103752/solve_rerun_20260416_154100_tag115cm/eval_holdout/no115_train_ref_layouts/v3l_no115_with_train_ref.json`
- train_session (for solved_reference_m): `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_cm_fresh_20260416_154100/split_for_v3full/floating_ref115_train`
- holdout_session: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_cm_fresh_20260416_154100/split_for_v3full/floating_ref115_holdout`
- solved_reference_m: `1.204054, 2.179310, 0.757816`
- used_samples: `156` (skipped `4`)

## Overall Residual Stats (mm)

| metric | value |
|---|---:|
| mean | -11.624 |
| rms | 60.968 |
| p50(abs) | 38.222 |
| p95(abs) | 125.398 |
| max(abs) | 160.148 |

## Per-Anchor Residual RMS (mm)

| anchor | n | mean | rms | p95(abs) |
|---|---:|---:|---:|---:|
| A | 19 | 19.370 | 25.122 | 41.780 |
| B | 20 | -14.999 | 34.598 | 67.149 |
| C | 19 | -64.419 | 68.873 | 99.214 |
| D | 20 | -8.715 | 20.269 | 33.665 |
| E | 20 | 114.398 | 118.037 | 158.248 |
| F | 19 | -63.345 | 65.220 | 81.771 |
| G | 20 | -35.332 | 44.746 | 86.232 |
| H | 19 | -45.310 | 49.245 | 69.873 |
