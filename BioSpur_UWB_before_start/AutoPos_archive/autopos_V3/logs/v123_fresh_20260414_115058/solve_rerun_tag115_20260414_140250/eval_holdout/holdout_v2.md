# Holdout Evaluation (Floating Reference)

- layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_rerun_tag115_20260414_140250/v2_fused/anchor_layout_v2_iter_tag115.json`
- train_session (for solved_reference_m): `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_rerun_tag115_20260414_140250/floating_ref115_train`
- holdout_session: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_rerun_tag115_20260414_140250/floating_ref115_holdout`
- solved_reference_m: `2.448927, 0.538599, 0.800479`
- used_samples: `397` (skipped `3`)

## Overall Residual Stats (mm)

| metric | value |
|---|---:|
| mean | -145.043 |
| rms | 198.392 |
| p50(abs) | 128.690 |
| p95(abs) | 401.429 |
| max(abs) | 458.429 |

## Per-Anchor Residual RMS (mm)

| anchor | n | mean | rms | p95(abs) |
|---|---:|---:|---:|---:|
| A | 49 | -240.647 | 254.390 | 391.272 |
| B | 50 | -40.608 | 53.492 | 98.098 |
| C | 50 | -400.709 | 401.412 | 441.779 |
| D | 50 | -189.312 | 190.229 | 221.852 |
| E | 49 | -78.404 | 86.331 | 127.090 |
| F | 50 | -10.906 | 36.091 | 73.076 |
| G | 50 | -199.038 | 200.055 | 232.788 |
| H | 49 | 1.638 | 17.061 | 33.583 |
