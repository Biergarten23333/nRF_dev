# Holdout Evaluation (Floating Reference)

- layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/live_autopos_sweep_loop_A_to_H_100sets_20260416_103752/solve_rerun_20260416_154100_tag115cm/v3_full_no115/anchor_layout_v3_full_with_train_ref.json`
- train_session (for solved_reference_m): `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_cm_fresh_20260416_154100/split_for_v3full/floating_ref115_train`
- holdout_session: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_cm_fresh_20260416_154100/split_for_v3full/floating_ref115_holdout`
- solved_reference_m: `1.192080, 2.189499, 0.768754`
- used_samples: `156` (skipped `4`)

## Overall Residual Stats (mm)

| metric | value |
|---|---:|
| mean | -7.158 |
| rms | 58.379 |
| p50(abs) | 35.985 |
| p95(abs) | 124.098 |
| max(abs) | 158.848 |

## Per-Anchor Residual RMS (mm)

| anchor | n | mean | rms | p95(abs) |
|---|---:|---:|---:|---:|
| A | 19 | 25.610 | 30.197 | 48.021 |
| B | 20 | -10.993 | 33.060 | 63.143 |
| C | 19 | -60.426 | 65.153 | 95.221 |
| D | 20 | -3.893 | 18.709 | 31.457 |
| E | 20 | 113.098 | 116.777 | 156.948 |
| F | 19 | -54.154 | 56.336 | 72.580 |
| G | 20 | -34.399 | 44.012 | 85.299 |
| H | 19 | -36.970 | 41.700 | 61.533 |
