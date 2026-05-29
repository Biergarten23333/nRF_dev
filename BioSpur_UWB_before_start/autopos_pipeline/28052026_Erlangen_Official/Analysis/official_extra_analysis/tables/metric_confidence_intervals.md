# Bootstrap Confidence Intervals

n_boot=10000, seed=42. MC metrics included: False (0 metric groups).

## V4-io headline CIs

| metric | eval_set | stat | point | ci_low | ci_high | unit | n_values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| layout_rigid_3d_error | all8 | rms | 104.94 | 81.35 | 133.24 | mm | 8 |
| layout_rigid_3d_error | noG | rms | 104.41 | 78.06 | 133.05 | mm | 7 |
| static_radial_p95 | all_static_sessions | median | 105.12 | 91.80 | 128.87 | mm | 24 |
| tag_absolute_3d_error | all8 | median | 77.38 | 53.82 | 141.08 | mm | 24 |
| tag_absolute_3d_error | all8 | p95 | 270.26 | 151.92 | 369.57 | mm | 24 |
| tag_absolute_vertical_error | all8 | median | 63.12 | 38.91 | 116.43 | mm | 24 |
| tag_absolute_3d_error | noG | median | 81.27 | 63.20 | 146.74 | mm | 24 |
| tag_absolute_3d_error | noG | p95 | 278.60 | 154.82 | 365.73 | mm | 24 |
| tag_absolute_vertical_error | noG | median | 63.51 | 44.36 | 113.53 | mm | 24 |
| roto_abs_deltaR_error | roto_pairs | median | 33.33 | 22.52 | 40.15 | mm | 17 |
| roto_abs_deltaR_error | roto_pairs | p95 | 42.58 | 40.17 | 47.25 | mm | 17 |
| roto_turn_center_rms | roto_tags | median | 14.31 | 13.16 | 17.36 | mm | 34 |
