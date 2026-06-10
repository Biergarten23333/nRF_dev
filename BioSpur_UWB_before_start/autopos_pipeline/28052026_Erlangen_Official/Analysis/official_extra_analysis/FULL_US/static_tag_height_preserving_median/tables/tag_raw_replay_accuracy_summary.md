# Static Tag Raw Replay Absolute Matrix

Source: raw static `tr_all.csv` captures replayed through the C-core T-series solver.

Official frame lock: anchor-derived reflection-allowed rigid transform, no scale. Tag truth is not used for fitting.

Tag truth source: corrected static OptiTrack `Iantenna`. ID01 and ID05 use deterministic I1..I5 relabeling plus a rebuilt ball-local consensus antenna; all other captures use Motive `Iantenna` as exported.

Eval sets:

- `all8`: solve with all available anchors and align with all 8 anchors.

## V4-io / T4 Headline

| eval_set | n | median_3d_mm | p95_3d_mm | rms_3d_mm | median_horizontal_mm | median_vertical_mm | median_repeatability_d3_mm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| all8 | 24 | 109.2 | 230.5 | 149.7 | 41.3 | 100.3 | 67.4 |

## Best Median 3D Absolute Errors

| rank | version | tag_method | eval_set | median_3d_mm | p95_3d_mm | rms_3d_mm | vertical_median_mm |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: |
| 1 | v4-io | T4 | all8 | 109.2 | 230.5 | 149.7 | 100.3 |
| 2 | v3-full | T4 | all8 | 112.4 | 240.3 | 141.7 | 90.9 |
| 3 | v1-old | T4 | all8 | 130.8 | 269.8 | 170.3 | 118.4 |
| 4 | v2 | T4 | all8 | 136.2 | 266.3 | 176.5 | 133.5 |
| 5 | v3-lite | T4 | all8 | 137.2 | 266.6 | 177.2 | 134.3 |

## Full Matrix

| version | Tx | eval_set | median_3d_mm | p95_3d_mm | rms_3d_mm | horiz_med_mm | vert_med_mm | repeat_d3_med_mm |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v1-old | T4 | all8 | 130.8 | 269.8 | 170.3 | 50.7 | 118.4 | 68.0 |
| v2 | T4 | all8 | 136.2 | 266.3 | 176.5 | 51.8 | 133.5 | 62.7 |
| v3-lite | T4 | all8 | 137.2 | 266.6 | 177.2 | 51.7 | 134.3 | 62.7 |
| v3-full | T4 | all8 | 112.4 | 240.3 | 141.7 | 47.9 | 90.9 | 61.5 |
| v4-io | T4 | all8 | 109.2 | 230.5 | 149.7 | 41.3 | 100.3 | 67.4 |
