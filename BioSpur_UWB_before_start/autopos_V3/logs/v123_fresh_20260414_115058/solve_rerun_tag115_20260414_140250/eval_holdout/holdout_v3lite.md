# Holdout Evaluation (Floating Reference)

- layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_rerun_tag115_20260414_140250/v3_lite_fused/anchor_layout_v3lite_iter_tag115.json`
- train_session (for solved_reference_m): `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_rerun_tag115_20260414_140250/floating_ref115_train`
- holdout_session: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_rerun_tag115_20260414_140250/floating_ref115_holdout`
- solved_reference_m: `2.444204, 0.496586, 0.804767`
- used_samples: `397` (skipped `3`)

## Overall Residual Stats (mm)

| metric | value |
|---|---:|
| mean | -148.172 |
| rms | 203.707 |
| p50(abs) | 129.634 |
| p95(abs) | 408.715 |
| max(abs) | 465.715 |

## Per-Anchor Residual RMS (mm)

| anchor | n | mean | rms | p95(abs) |
|---|---:|---:|---:|---:|
| A | 49 | -252.016 | 265.170 | 402.640 |
| B | 50 | -51.925 | 62.518 | 109.415 |
| C | 50 | -407.995 | 408.686 | 449.065 |
| D | 50 | -209.824 | 210.652 | 242.364 |
| E | 49 | -70.349 | 79.087 | 119.034 |
| F | 50 | -6.461 | 35.006 | 70.538 |
| G | 50 | -192.749 | 193.799 | 226.499 |
| H | 49 | 8.555 | 19.016 | 33.604 |
