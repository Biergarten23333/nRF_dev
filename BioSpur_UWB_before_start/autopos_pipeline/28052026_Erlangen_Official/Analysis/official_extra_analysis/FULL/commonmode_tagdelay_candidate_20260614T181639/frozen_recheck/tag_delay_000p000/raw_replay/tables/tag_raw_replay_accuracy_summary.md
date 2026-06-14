# Static Tag Raw Replay Absolute Matrix

Source: raw static `tr_all.csv` captures replayed through the C-core T-series solver.

Official frame lock: anchor-derived height-preserving transform: 2D horizontal rigid alignment plus F/G/H vertical shift, no scale. Tag truth is not used for fitting.

Tag truth source: corrected static OptiTrack `Iantenna`. ID01 and ID05 use deterministic I1..I5 relabeling plus a rebuilt ball-local consensus antenna; all other captures use Motive `Iantenna` as exported.

Eval sets:

- `all8`: solve with all available anchors and align with all 8 anchors.

## V4-io / T4 Headline

| eval_set | n | median_3d_mm | p95_3d_mm | rms_3d_mm | median_horizontal_mm | median_vertical_mm | median_repeatability_d3_mm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| all8 | 24 | 1154.0 | 3221.7 | 1948.4 | 184.8 | 1137.4 | 67.1 |

## Best Median 3D Absolute Errors

| rank | version | tag_method | eval_set | median_3d_mm | p95_3d_mm | rms_3d_mm | vertical_median_mm |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: |
| 1 | v4-io | T4 | all8 | 1154.0 | 3221.7 | 1948.4 | 1137.4 |

## Full Matrix

| version | Tx | eval_set | median_3d_mm | p95_3d_mm | rms_3d_mm | horiz_med_mm | vert_med_mm | repeat_d3_med_mm |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v4-io | T4 | all8 | 1154.0 | 3221.7 | 1948.4 | 184.8 | 1137.4 | 67.1 |
