# Bootstrap Confidence Intervals

n_boot=10000, seed=42. MC metrics included: False (0 metric groups).

## V4-io headline CIs

| metric | eval_set | stat | point | ci_low | ci_high | unit | n_values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| layout_rigid_3d_error | all8 | rms | 105.42 | 80.02 | 134.94 | mm | 8 |
| static_radial_p95 | all_static_sessions | median | 105.12 | 91.80 | 128.87 | mm | 24 |
| tag_absolute_3d_error | all8 | median | 73.96 | 59.90 | 143.20 | mm | 24 |
| tag_absolute_3d_error | all8 | p95 | 282.13 | 152.38 | 359.62 | mm | 24 |
| tag_absolute_vertical_error | all8 | median | 65.32 | 40.16 | 122.41 | mm | 24 |
| tag_raw_replay_3d_error | T1/all8 | median | 70.78 | 60.21 | 139.24 | mm | 24 |
| tag_raw_replay_3d_error | T1/all8 | p95 | 283.68 | 146.88 | 361.10 | mm | 24 |
| tag_raw_replay_vertical_error | T1/all8 | median | 63.97 | 45.89 | 118.80 | mm | 24 |
| tag_raw_replay_repeatability_d3 | T1/all8 | median | 58.64 | 52.33 | 68.07 | mm | 24 |
| tag_raw_replay_3d_error | T2/all8 | median | 71.06 | 59.59 | 139.23 | mm | 24 |
| tag_raw_replay_3d_error | T2/all8 | p95 | 282.87 | 146.74 | 361.02 | mm | 24 |
| tag_raw_replay_vertical_error | T2/all8 | median | 63.95 | 38.79 | 119.63 | mm | 24 |
| tag_raw_replay_repeatability_d3 | T2/all8 | median | 58.93 | 52.78 | 68.36 | mm | 24 |
| tag_raw_replay_3d_error | T3/all8 | median | 69.16 | 57.17 | 94.77 | mm | 24 |
| tag_raw_replay_3d_error | T3/all8 | p95 | 172.99 | 123.58 | 307.38 | mm | 24 |
| tag_raw_replay_vertical_error | T3/all8 | median | 47.76 | 28.56 | 68.31 | mm | 24 |
| tag_raw_replay_repeatability_d3 | T3/all8 | median | 58.74 | 51.66 | 70.08 | mm | 24 |
| tag_raw_replay_3d_error | T4/all8 | median | 69.69 | 56.94 | 120.57 | mm | 24 |
| tag_raw_replay_3d_error | T4/all8 | p95 | 173.93 | 134.47 | 278.16 | mm | 24 |
| tag_raw_replay_vertical_error | T4/all8 | median | 59.98 | 36.17 | 99.99 | mm | 24 |
| tag_raw_replay_repeatability_d3 | T4/all8 | median | 67.44 | 56.10 | 77.24 | mm | 24 |
| roto_abs_deltaR_error | roto_pairs | median | 33.33 | 22.52 | 40.15 | mm | 17 |
| roto_abs_deltaR_error | roto_pairs | p95 | 42.58 | 40.17 | 47.25 | mm | 17 |
| roto_turn_center_rms | roto_tags | median | 14.31 | 13.16 | 17.36 | mm | 34 |
