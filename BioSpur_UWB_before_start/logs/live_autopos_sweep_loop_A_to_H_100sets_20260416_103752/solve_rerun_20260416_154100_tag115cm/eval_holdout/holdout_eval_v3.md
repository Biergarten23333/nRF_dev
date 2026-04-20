# Holdout Evaluation (Floating Reference)

- layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/live_autopos_sweep_loop_A_to_H_100sets_20260416_103752/solve_rerun_20260416_154100_tag115cm/v3_lite/v3_fused/anchor_layout_v3_lite_iterative.json`
- train_session (for solved_reference_m): `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_cm_fresh_20260416_154100/split_for_v3full/floating_ref115_train`
- holdout_session: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_cm_fresh_20260416_154100/split_for_v3full/floating_ref115_holdout`
- solved_reference_m: `1.205058, 2.176433, 0.809819`
- used_samples: `156` (skipped `4`)

## Overall Residual Stats (mm)

| metric | value |
|---|---:|
| mean | -13.193 |
| rms | 58.953 |
| p50(abs) | 44.818 |
| p95(abs) | 104.941 |
| max(abs) | 138.388 |

## Per-Anchor Residual RMS (mm)

| anchor | n | mean | rms | p95(abs) |
|---|---:|---:|---:|---:|
| A | 19 | 33.052 | 36.721 | 55.463 |
| B | 20 | -3.979 | 31.431 | 56.129 |
| C | 19 | -51.934 | 57.365 | 86.728 |
| D | 20 | -0.120 | 18.300 | 35.230 |
| E | 20 | 92.638 | 97.096 | 136.488 |
| F | 19 | -72.440 | 74.085 | 90.866 |
| G | 20 | -55.852 | 62.235 | 106.752 |
| H | 19 | -51.406 | 54.906 | 75.969 |
