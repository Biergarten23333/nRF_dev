# Static Tag Raw Replay Absolute Matrix

Source: raw static `tr_all.csv` captures replayed through the C-core T-series solver.

Official frame lock: anchor-derived reflection-allowed rigid transform, no scale. Tag truth is not used for fitting.

Tag truth source: corrected static OptiTrack `Iantenna`. ID01 and ID05 use deterministic I1..I5 relabeling plus a rebuilt ball-local consensus antenna; all other captures use Motive `Iantenna` as exported.

Eval sets:

- `all8`: solve with all available anchors and align with all 8 anchors.

## V4-io / T4 Headline

| eval_set | n | median_3d_mm | p95_3d_mm | rms_3d_mm | median_horizontal_mm | median_vertical_mm | median_repeatability_d3_mm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| all8 | 24 | 69.8 | 173.8 | 108.9 | 37.6 | 60.0 | 67.4 |

## Best Median 3D Absolute Errors

| rank | version | tag_method | eval_set | median_3d_mm | p95_3d_mm | rms_3d_mm | vertical_median_mm |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: |
| 1 | v4-io | T3 | all8 | 69.1 | 173.0 | 106.6 | 47.7 |
| 2 | v4-io | T4 | all8 | 69.8 | 173.8 | 108.9 | 60.0 |
| 3 | v4-io | T1 | all8 | 70.8 | 283.5 | 139.3 | 64.1 |
| 4 | v4-io | T2 | all8 | 71.0 | 282.8 | 139.0 | 63.9 |
| 5 | v2 | T3 | all8 | 74.0 | 205.3 | 106.1 | 59.7 |
| 6 | v3-lite | T3 | all8 | 74.7 | 205.3 | 106.2 | 63.9 |
| 7 | v2 | T4 | all8 | 76.2 | 168.4 | 104.9 | 66.0 |
| 8 | v3-lite | T4 | all8 | 76.8 | 168.3 | 105.0 | 66.3 |

## Full Matrix

| version | Tx | eval_set | median_3d_mm | p95_3d_mm | rms_3d_mm | horiz_med_mm | vert_med_mm | repeat_d3_med_mm |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v1-old | T1 | all8 | 158.0 | 327.4 | 193.2 | 53.7 | 140.5 | 63.0 |
| v1-old | T2 | all8 | 157.8 | 327.4 | 193.1 | 53.5 | 141.0 | 62.8 |
| v1-old | T3 | all8 | 121.4 | 318.3 | 168.6 | 55.7 | 96.4 | 64.5 |
| v1-old | T4 | all8 | 132.9 | 237.0 | 160.3 | 50.0 | 121.0 | 68.0 |
| v2 | T1 | all8 | 81.4 | 247.3 | 134.6 | 43.6 | 73.8 | 61.9 |
| v2 | T2 | all8 | 81.2 | 246.5 | 134.4 | 44.4 | 73.3 | 61.8 |
| v2 | T3 | all8 | 74.0 | 205.3 | 106.1 | 48.9 | 59.7 | 63.3 |
| v2 | T4 | all8 | 76.2 | 168.4 | 104.9 | 42.7 | 66.0 | 62.7 |
| v3-lite | T1 | all8 | 82.1 | 247.8 | 134.8 | 43.9 | 74.4 | 62.1 |
| v3-lite | T2 | all8 | 81.8 | 246.9 | 134.5 | 44.8 | 73.9 | 62.0 |
| v3-lite | T3 | all8 | 74.7 | 205.3 | 106.2 | 49.2 | 63.9 | 63.1 |
| v3-lite | T4 | all8 | 76.8 | 168.3 | 105.0 | 43.4 | 66.3 | 62.7 |
| v3-full | T1 | all8 | 115.8 | 291.6 | 160.2 | 48.9 | 109.4 | 64.5 |
| v3-full | T2 | all8 | 116.7 | 291.7 | 159.8 | 47.4 | 109.7 | 65.3 |
| v3-full | T3 | all8 | 89.6 | 250.1 | 129.3 | 46.2 | 70.2 | 56.2 |
| v3-full | T4 | all8 | 98.6 | 236.9 | 132.9 | 40.7 | 89.3 | 61.5 |
| v4-io | T1 | all8 | 70.8 | 283.5 | 139.3 | 42.9 | 64.1 | 58.6 |
| v4-io | T2 | all8 | 71.0 | 282.8 | 139.0 | 42.8 | 63.9 | 58.9 |
| v4-io | T3 | all8 | 69.1 | 173.0 | 106.6 | 45.5 | 47.7 | 58.7 |
| v4-io | T4 | all8 | 69.8 | 173.8 | 108.9 | 37.6 | 60.0 | 67.4 |
