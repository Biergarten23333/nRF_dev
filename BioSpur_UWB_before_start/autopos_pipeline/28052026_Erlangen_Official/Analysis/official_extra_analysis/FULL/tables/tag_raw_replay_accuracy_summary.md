# Static Tag Raw Replay Absolute Matrix

Source: raw static `tr_all.csv` captures replayed through the C-core T-series solver.

Official frame lock: anchor-derived reflection-allowed rigid transform, no scale. Tag truth is not used for fitting.

Tag truth source: corrected static OptiTrack `Iantenna`. ID01 and ID05 use deterministic I1..I5 relabeling plus a rebuilt ball-local consensus antenna; all other captures use Motive `Iantenna` as exported.

Eval sets:

- `all8`: solve with all available anchors and align with all 8 anchors.

## V4-io / T4 Headline

| eval_set | n | median_3d_mm | p95_3d_mm | rms_3d_mm | median_horizontal_mm | median_vertical_mm | median_repeatability_d3_mm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| all8 | 24 | 69.7 | 173.9 | 108.9 | 37.5 | 60.0 | 67.4 |

## Best Median 3D Absolute Errors

| rank | version | tag_method | eval_set | median_3d_mm | p95_3d_mm | rms_3d_mm | vertical_median_mm |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: |
| 1 | v4-io | T3 | all8 | 69.2 | 173.0 | 106.6 | 47.8 |
| 2 | v4-io | T4 | all8 | 69.7 | 173.9 | 108.9 | 60.0 |
| 3 | v4-io | T1 | all8 | 70.8 | 283.7 | 139.3 | 64.0 |
| 4 | v4-io | T2 | all8 | 71.1 | 282.9 | 139.0 | 63.9 |
| 5 | v2 | T3 | all8 | 74.0 | 205.3 | 106.2 | 60.1 |
| 6 | v3-lite | T3 | all8 | 74.7 | 205.3 | 106.2 | 64.1 |
| 7 | v2 | T4 | all8 | 76.5 | 168.2 | 104.9 | 66.0 |
| 8 | v3-lite | T4 | all8 | 77.1 | 168.1 | 104.9 | 66.5 |

## Full Matrix

| version | Tx | eval_set | median_3d_mm | p95_3d_mm | rms_3d_mm | horiz_med_mm | vert_med_mm | repeat_d3_med_mm |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v1-old | T1 | all8 | 157.9 | 327.8 | 193.3 | 53.9 | 140.5 | 63.0 |
| v1-old | T2 | all8 | 157.5 | 327.7 | 193.2 | 53.7 | 140.8 | 62.9 |
| v1-old | T3 | all8 | 121.6 | 318.7 | 168.7 | 55.5 | 96.5 | 64.5 |
| v1-old | T4 | all8 | 132.9 | 236.8 | 160.2 | 49.9 | 120.8 | 68.0 |
| v2 | T1 | all8 | 81.6 | 247.5 | 134.7 | 43.5 | 74.0 | 61.9 |
| v2 | T2 | all8 | 81.2 | 246.4 | 134.3 | 44.4 | 73.3 | 61.8 |
| v2 | T3 | all8 | 74.0 | 205.3 | 106.2 | 49.0 | 60.1 | 63.3 |
| v2 | T4 | all8 | 76.5 | 168.2 | 104.9 | 42.6 | 66.0 | 62.7 |
| v3-lite | T1 | all8 | 82.3 | 247.9 | 134.9 | 43.8 | 74.6 | 62.1 |
| v3-lite | T2 | all8 | 82.0 | 247.0 | 134.5 | 44.7 | 74.1 | 62.0 |
| v3-lite | T3 | all8 | 74.7 | 205.3 | 106.2 | 49.2 | 64.1 | 63.1 |
| v3-lite | T4 | all8 | 77.1 | 168.1 | 104.9 | 43.2 | 66.5 | 62.7 |
| v3-full | T1 | all8 | 116.2 | 291.6 | 160.2 | 48.8 | 109.7 | 64.5 |
| v3-full | T2 | all8 | 116.8 | 291.6 | 159.8 | 47.5 | 110.0 | 65.3 |
| v3-full | T3 | all8 | 89.8 | 250.3 | 129.4 | 46.2 | 70.4 | 56.2 |
| v3-full | T4 | all8 | 98.2 | 237.0 | 132.9 | 40.8 | 89.4 | 61.5 |
| v4-io | T1 | all8 | 70.8 | 283.7 | 139.3 | 42.7 | 64.0 | 58.6 |
| v4-io | T2 | all8 | 71.1 | 282.9 | 139.0 | 42.8 | 63.9 | 58.9 |
| v4-io | T3 | all8 | 69.2 | 173.0 | 106.6 | 45.5 | 47.8 | 58.7 |
| v4-io | T4 | all8 | 69.7 | 173.9 | 108.9 | 37.5 | 60.0 | 67.4 |
