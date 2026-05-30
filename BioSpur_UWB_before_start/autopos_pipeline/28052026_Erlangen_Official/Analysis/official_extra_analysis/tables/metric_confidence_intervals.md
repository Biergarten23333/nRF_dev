# Bootstrap Confidence Intervals

n_boot=10000, seed=42. MC metrics included: True (600 metric groups).

## V4-io headline CIs

| metric | eval_set | stat | point | ci_low | ci_high | unit | n_values |
| --- | --- | --- | --- | --- | --- | --- | --- |
| layout_rigid_3d_error | all8 | rms | 104.94 | 81.35 | 133.24 | mm | 8 |
| layout_rigid_3d_error | noG | rms | 104.41 | 78.06 | 133.05 | mm | 7 |
| static_radial_p95 | all_static_sessions | median | 105.12 | 91.80 | 128.87 | mm | 24 |
| tag_absolute_3d_error | all8 | median | 77.38 | 52.12 | 141.08 | mm | 24 |
| tag_absolute_3d_error | all8 | p95 | 270.26 | 151.92 | 369.57 | mm | 24 |
| tag_absolute_vertical_error | all8 | median | 63.12 | 38.91 | 118.67 | mm | 24 |
| tag_absolute_3d_error | noG | median | 81.27 | 63.20 | 146.74 | mm | 24 |
| tag_absolute_3d_error | noG | p95 | 278.60 | 154.82 | 365.73 | mm | 24 |
| tag_absolute_vertical_error | noG | median | 63.51 | 44.36 | 121.37 | mm | 24 |
| tag_raw_replay_3d_error | T1/all8 | median | 76.44 | 51.64 | 135.63 | mm | 24 |
| tag_raw_replay_3d_error | T1/all8 | p95 | 273.83 | 152.23 | 370.94 | mm | 24 |
| tag_raw_replay_vertical_error | T1/all8 | median | 62.31 | 33.77 | 116.35 | mm | 24 |
| tag_raw_replay_repeatability_d3 | T1/all8 | median | 58.64 | 52.09 | 68.07 | mm | 24 |
| tag_raw_replay_3d_error | T1/noG | median | 97.51 | 72.55 | 138.95 | mm | 24 |
| tag_raw_replay_3d_error | T1/noG | p95 | 329.23 | 161.26 | 376.55 | mm | 24 |
| tag_raw_replay_vertical_error | T1/noG | median | 56.22 | 40.68 | 76.36 | mm | 24 |
| tag_raw_replay_repeatability_d3 | T1/noG | median | 55.76 | 50.30 | 70.12 | mm | 24 |
| tag_raw_replay_3d_error | T2/all8 | median | 76.00 | 53.02 | 135.62 | mm | 24 |
| tag_raw_replay_3d_error | T2/all8 | p95 | 273.10 | 152.02 | 370.87 | mm | 24 |
| tag_raw_replay_vertical_error | T2/all8 | median | 62.55 | 35.32 | 116.01 | mm | 24 |
| tag_raw_replay_repeatability_d3 | T2/all8 | median | 58.93 | 52.78 | 68.58 | mm | 24 |
| tag_raw_replay_3d_error | T2/noG | median | 98.15 | 72.94 | 139.61 | mm | 24 |
| tag_raw_replay_3d_error | T2/noG | p95 | 328.84 | 160.65 | 376.60 | mm | 24 |
| tag_raw_replay_vertical_error | T2/noG | median | 57.26 | 41.51 | 71.76 | mm | 24 |
| tag_raw_replay_repeatability_d3 | T2/noG | median | 55.92 | 50.38 | 69.99 | mm | 24 |
| tag_raw_replay_3d_error | T3/all8 | median | 62.26 | 45.78 | 96.32 | mm | 24 |
| tag_raw_replay_3d_error | T3/all8 | p95 | 158.17 | 116.44 | 292.22 | mm | 24 |
| tag_raw_replay_vertical_error | T3/all8 | median | 48.62 | 27.22 | 58.37 | mm | 24 |
| tag_raw_replay_repeatability_d3 | T3/all8 | median | 58.74 | 51.66 | 70.08 | mm | 24 |
| tag_raw_replay_3d_error | T3/noG | median | 83.90 | 72.21 | 134.03 | mm | 24 |
| tag_raw_replay_3d_error | T3/noG | p95 | 291.14 | 156.42 | 339.61 | mm | 24 |
| tag_raw_replay_vertical_error | T3/noG | median | 43.87 | 29.43 | 68.72 | mm | 24 |
| tag_raw_replay_repeatability_d3 | T3/noG | median | 56.22 | 46.11 | 72.79 | mm | 24 |
| tag_raw_replay_3d_error | T4/all8 | median | 69.14 | 52.17 | 114.50 | mm | 24 |
| tag_raw_replay_3d_error | T4/all8 | p95 | 182.33 | 133.28 | 263.83 | mm | 24 |
| tag_raw_replay_vertical_error | T4/all8 | median | 55.04 | 27.85 | 97.75 | mm | 24 |
| tag_raw_replay_repeatability_d3 | T4/all8 | median | 67.44 | 56.10 | 77.24 | mm | 24 |
| tag_raw_replay_3d_error | T4/noG | median | 83.90 | 72.21 | 134.03 | mm | 24 |
| tag_raw_replay_3d_error | T4/noG | p95 | 291.14 | 156.42 | 339.61 | mm | 24 |
| tag_raw_replay_vertical_error | T4/noG | median | 43.87 | 31.36 | 68.77 | mm | 24 |
| tag_raw_replay_repeatability_d3 | T4/noG | median | 56.22 | 46.11 | 72.79 | mm | 24 |
| roto_abs_deltaR_error | roto_pairs | median | 33.33 | 22.52 | 40.15 | mm | 17 |
| roto_abs_deltaR_error | roto_pairs | p95 | 42.58 | 40.17 | 47.25 | mm | 17 |
| roto_turn_center_rms | roto_tags | median | 14.31 | 13.16 | 17.36 | mm | 34 |
