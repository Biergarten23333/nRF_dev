# Holdout Evaluation (Floating Reference)

- layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/live_autopos_sweep_loop_A_to_H_100sets_20260416_103752/solve_rerun_20260416_154100_tag115cm/v1/anchor_layout_v1_soft_iterative.json`
- train_session (for solved_reference_m): `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_cm_fresh_20260416_154100/split_for_v3full/floating_ref115_train`
- holdout_session: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_cm_fresh_20260416_154100/split_for_v3full/floating_ref115_holdout`
- solved_reference_m: `1.212206, 2.172861, 0.810618`
- used_samples: `156` (skipped `4`)

## Overall Residual Stats (mm)

| metric | value |
|---|---:|
| mean | -14.342 |
| rms | 56.972 |
| p50(abs) | 43.843 |
| p95(abs) | 102.004 |
| max(abs) | 131.183 |

## Per-Anchor Residual RMS (mm)

| anchor | n | mean | rms | p95(abs) |
|---|---:|---:|---:|---:|
| A | 19 | 33.633 | 37.244 | 56.043 |
| B | 20 | -8.076 | 32.207 | 60.226 |
| C | 19 | -53.286 | 58.592 | 88.081 |
| D | 20 | -3.076 | 18.557 | 32.274 |
| E | 20 | 85.433 | 90.247 | 129.283 |
| F | 19 | -66.085 | 67.885 | 84.511 |
| G | 20 | -60.871 | 66.776 | 111.771 |
| H | 19 | -46.132 | 50.002 | 70.695 |
