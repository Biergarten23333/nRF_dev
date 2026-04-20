# Holdout Evaluation (Floating Reference)

- layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/live_autopos_sweep_loop_A_to_H_100sets_20260416_103752/solve_rerun_20260416_154100_tag115cm/v3_full_tag115_cm/anchor_layout_v3_full.json`
- train_session (for solved_reference_m): `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_cm_fresh_20260416_154100/split_for_v3full/floating_ref115_train`
- holdout_session: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_cm_fresh_20260416_154100/split_for_v3full/floating_ref115_holdout`
- solved_reference_m: `1.120409, 2.159956, 0.813041`
- used_samples: `156` (skipped `4`)

## Overall Residual Stats (mm)

| metric | value |
|---|---:|
| mean | -6.108 |
| rms | 40.162 |
| p50(abs) | 30.806 |
| p95(abs) | 76.987 |
| max(abs) | 106.112 |

## Per-Anchor Residual RMS (mm)

| anchor | n | mean | rms | p95(abs) |
|---|---:|---:|---:|---:|
| A | 19 | -17.716 | 23.871 | 36.805 |
| B | 20 | 14.780 | 34.504 | 57.480 |
| C | 19 | 11.005 | 26.736 | 51.111 |
| D | 20 | -6.940 | 19.572 | 32.856 |
| E | 20 | 43.195 | 52.073 | 87.045 |
| F | 19 | -47.186 | 49.675 | 65.612 |
| G | 20 | 10.332 | 29.335 | 43.926 |
| H | 19 | -60.849 | 63.833 | 85.412 |
