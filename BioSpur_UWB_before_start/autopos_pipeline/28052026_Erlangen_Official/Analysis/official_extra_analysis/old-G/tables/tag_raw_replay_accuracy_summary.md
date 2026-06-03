# Static Tag Raw Replay Absolute Matrix

Source: raw static `tr_all.csv` captures replayed through the C-core T-series solver.

Official frame lock: anchor-derived reflection-allowed rigid transform, no scale. Tag truth is not used for fitting.

Tag truth source: corrected static OptiTrack `Iantenna`. ID01 and ID05 use deterministic I1..I5 relabeling plus a rebuilt ball-local consensus antenna; all other captures use Motive `Iantenna` as exported.

Eval sets:

- `all8`: solve with all available anchors and align with all 8 anchors.
- `noG`: drop G observations before solving and align using anchors A/B/C/D/E/F/H.

## V4-io / T4 Headline

| eval_set | n | median_3d_mm | p95_3d_mm | rms_3d_mm | median_horizontal_mm | median_vertical_mm | median_repeatability_d3_mm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| all8 | 24 | 69.1 | 182.3 | 107.0 | 41.3 | 55.0 | 67.4 |
| noG | 24 | 83.9 | 291.1 | 138.7 | 67.9 | 43.9 | 56.2 |

## Best Median 3D Absolute Errors

| rank | version | tag_method | eval_set | median_3d_mm | p95_3d_mm | rms_3d_mm | vertical_median_mm |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: |
| 1 | v4-io | T3 | all8 | 62.3 | 158.2 | 101.8 | 48.6 |
| 2 | v4-io | T4 | all8 | 69.1 | 182.3 | 107.0 | 55.0 |
| 3 | v2 | T3 | all8 | 71.3 | 190.1 | 100.5 | 53.1 |
| 4 | v3-lite | T3 | all8 | 72.4 | 190.2 | 100.6 | 52.7 |
| 5 | v2 | T4 | all8 | 75.0 | 166.0 | 102.0 | 58.6 |
| 6 | v3-lite | T4 | all8 | 75.2 | 164.6 | 101.9 | 58.7 |
| 7 | v4-io | T2 | all8 | 76.0 | 273.1 | 137.8 | 62.6 |
| 8 | v4-io | T1 | all8 | 76.4 | 273.8 | 138.1 | 62.3 |

## Full Matrix

| version | Tx | eval_set | median_3d_mm | p95_3d_mm | rms_3d_mm | horiz_med_mm | vert_med_mm | repeat_d3_med_mm |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v1-old | T1 | all8 | 168.1 | 316.5 | 194.1 | 53.9 | 145.0 | 63.0 |
| v1-old | T2 | all8 | 167.7 | 316.3 | 194.0 | 53.1 | 145.9 | 62.9 |
| v1-old | T3 | all8 | 119.8 | 312.4 | 168.3 | 56.8 | 111.9 | 64.5 |
| v1-old | T4 | all8 | 134.3 | 253.6 | 160.6 | 50.9 | 128.4 | 68.0 |
| v2 | T1 | all8 | 79.7 | 232.7 | 133.0 | 42.7 | 68.1 | 61.9 |
| v2 | T2 | all8 | 78.6 | 231.6 | 132.7 | 42.0 | 67.7 | 61.8 |
| v2 | T3 | all8 | 71.3 | 190.1 | 100.5 | 45.6 | 53.1 | 63.3 |
| v2 | T4 | all8 | 75.0 | 166.0 | 102.0 | 43.4 | 58.6 | 62.7 |
| v3-lite | T1 | all8 | 79.8 | 233.1 | 133.2 | 42.9 | 68.7 | 62.1 |
| v3-lite | T2 | all8 | 78.9 | 232.2 | 132.9 | 42.2 | 68.4 | 62.0 |
| v3-lite | T3 | all8 | 72.4 | 190.2 | 100.6 | 46.0 | 52.7 | 63.1 |
| v3-lite | T4 | all8 | 75.2 | 164.6 | 101.9 | 43.5 | 58.7 | 62.7 |
| v3-full | T1 | all8 | 121.0 | 278.3 | 160.4 | 51.9 | 99.5 | 64.5 |
| v3-full | T2 | all8 | 122.9 | 278.6 | 160.0 | 51.1 | 99.6 | 65.3 |
| v3-full | T3 | all8 | 78.9 | 233.3 | 126.1 | 43.3 | 70.5 | 56.2 |
| v3-full | T4 | all8 | 107.4 | 224.5 | 131.9 | 45.1 | 77.7 | 61.5 |
| v4-io | T1 | all8 | 76.4 | 273.8 | 138.1 | 43.7 | 62.3 | 58.6 |
| v4-io | T2 | all8 | 76.0 | 273.1 | 137.8 | 43.9 | 62.6 | 58.9 |
| v4-io | T3 | all8 | 62.3 | 158.2 | 101.8 | 44.8 | 48.6 | 58.7 |
| v4-io | T4 | all8 | 69.1 | 182.3 | 107.0 | 41.3 | 55.0 | 67.4 |
| v1-old | T1 | noG | 159.6 | 382.5 | 201.5 | 72.7 | 139.3 | 57.9 |
| v1-old | T2 | noG | 159.6 | 382.1 | 201.6 | 72.8 | 139.5 | 58.0 |
| v1-old | T3 | noG | 143.2 | 346.1 | 187.4 | 71.9 | 104.4 | 57.5 |
| v1-old | T4 | noG | 143.2 | 346.1 | 187.4 | 71.9 | 104.4 | 57.5 |
| v2 | T1 | noG | 92.7 | 304.2 | 149.6 | 66.7 | 58.0 | 56.2 |
| v2 | T2 | noG | 92.6 | 303.8 | 149.7 | 66.4 | 58.2 | 56.1 |
| v2 | T3 | noG | 90.4 | 256.3 | 131.3 | 67.4 | 46.3 | 58.4 |
| v2 | T4 | noG | 90.4 | 256.3 | 131.3 | 67.4 | 46.3 | 58.4 |
| v3-lite | T1 | noG | 93.5 | 304.3 | 149.9 | 66.8 | 58.3 | 56.3 |
| v3-lite | T2 | noG | 93.4 | 303.9 | 150.0 | 66.5 | 58.4 | 56.2 |
| v3-lite | T3 | noG | 91.1 | 257.2 | 131.9 | 67.8 | 46.5 | 58.5 |
| v3-lite | T4 | noG | 91.1 | 257.2 | 131.9 | 67.8 | 46.5 | 58.5 |
| v3-full | T1 | noG | 136.4 | 304.0 | 168.3 | 75.2 | 93.6 | 65.2 |
| v3-full | T2 | noG | 136.3 | 304.8 | 168.3 | 75.3 | 93.2 | 65.1 |
| v3-full | T3 | noG | 116.8 | 281.4 | 158.2 | 73.7 | 69.1 | 63.5 |
| v3-full | T4 | noG | 116.8 | 281.4 | 158.2 | 73.7 | 69.1 | 63.5 |
| v4-io | T1 | noG | 97.5 | 329.2 | 154.8 | 66.9 | 56.2 | 55.8 |
| v4-io | T2 | noG | 98.2 | 328.8 | 154.8 | 66.9 | 57.3 | 55.9 |
| v4-io | T3 | noG | 83.9 | 291.1 | 138.7 | 67.9 | 43.9 | 56.2 |
| v4-io | T4 | noG | 83.9 | 291.1 | 138.7 | 67.9 | 43.9 | 56.2 |
