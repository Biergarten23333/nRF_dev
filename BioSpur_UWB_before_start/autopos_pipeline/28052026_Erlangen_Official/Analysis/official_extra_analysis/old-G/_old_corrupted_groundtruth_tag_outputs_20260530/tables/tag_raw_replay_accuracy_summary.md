# Static Tag Raw Replay Absolute Matrix

Source: raw static `tr_all.csv` captures replayed through the C-core T-series solver.

Official frame lock: anchor-derived reflection-allowed rigid transform, no scale. Tag truth is not used for fitting.

Eval sets:

- `all8`: solve with all available anchors and align with all 8 anchors.
- `noG`: drop G observations before solving and align using anchors A/B/C/D/E/F/H.

## V4-io / T4 Headline

| eval_set | n | median_3d_mm | p95_3d_mm | rms_3d_mm | median_horizontal_mm | median_vertical_mm | median_repeatability_d3_mm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| all8 | 24 | 69.1 | 182.3 | 104.8 | 38.1 | 55.0 | 67.4 |
| noG | 24 | 83.0 | 291.1 | 137.1 | 66.2 | 43.9 | 56.2 |

## Best Median 3D Absolute Errors

| rank | version | tag_method | eval_set | median_3d_mm | p95_3d_mm | rms_3d_mm | vertical_median_mm |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: |
| 1 | v4-io | T3 | all8 | 62.3 | 158.2 | 99.6 | 48.6 |
| 2 | v4-io | T4 | all8 | 69.1 | 182.3 | 104.8 | 55.0 |
| 3 | v2 | T3 | all8 | 71.3 | 190.1 | 98.0 | 53.1 |
| 4 | v3-lite | T3 | all8 | 72.4 | 190.2 | 98.1 | 52.7 |
| 5 | v2 | T4 | all8 | 75.0 | 166.0 | 99.6 | 58.6 |
| 6 | v3-lite | T4 | all8 | 75.2 | 164.6 | 99.5 | 58.7 |
| 7 | v4-io | T2 | all8 | 76.0 | 273.1 | 136.1 | 62.6 |
| 8 | v4-io | T1 | all8 | 76.4 | 273.8 | 136.4 | 62.3 |

## Full Matrix

| version | Tx | eval_set | median_3d_mm | p95_3d_mm | rms_3d_mm | horiz_med_mm | vert_med_mm | repeat_d3_med_mm |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v1-old | T1 | all8 | 157.8 | 316.5 | 192.4 | 51.3 | 139.8 | 63.0 |
| v1-old | T2 | all8 | 157.5 | 316.3 | 192.3 | 51.9 | 140.7 | 62.9 |
| v1-old | T3 | all8 | 119.9 | 312.4 | 166.2 | 53.8 | 111.9 | 64.5 |
| v1-old | T4 | all8 | 134.5 | 253.6 | 158.5 | 49.2 | 128.4 | 68.0 |
| v2 | T1 | all8 | 79.7 | 232.7 | 131.2 | 39.6 | 68.1 | 61.9 |
| v2 | T2 | all8 | 78.6 | 231.6 | 130.8 | 40.4 | 67.7 | 61.8 |
| v2 | T3 | all8 | 71.3 | 190.1 | 98.0 | 38.2 | 53.1 | 63.3 |
| v2 | T4 | all8 | 75.0 | 166.0 | 99.6 | 41.5 | 58.6 | 62.7 |
| v3-lite | T1 | all8 | 79.8 | 233.1 | 131.3 | 39.8 | 68.7 | 62.1 |
| v3-lite | T2 | all8 | 78.9 | 232.2 | 131.0 | 40.5 | 68.4 | 62.0 |
| v3-lite | T3 | all8 | 72.4 | 190.2 | 98.1 | 38.7 | 52.7 | 63.1 |
| v3-lite | T4 | all8 | 75.2 | 164.6 | 99.5 | 42.0 | 58.7 | 62.7 |
| v3-full | T1 | all8 | 117.2 | 278.3 | 158.7 | 49.2 | 95.9 | 64.5 |
| v3-full | T2 | all8 | 119.0 | 278.6 | 158.3 | 49.2 | 95.5 | 65.3 |
| v3-full | T3 | all8 | 78.9 | 233.3 | 124.1 | 40.0 | 70.5 | 56.2 |
| v3-full | T4 | all8 | 102.2 | 224.5 | 129.9 | 44.3 | 77.7 | 61.5 |
| v4-io | T1 | all8 | 76.4 | 273.8 | 136.4 | 41.4 | 62.3 | 58.6 |
| v4-io | T2 | all8 | 76.0 | 273.1 | 136.1 | 42.3 | 62.6 | 58.9 |
| v4-io | T3 | all8 | 62.3 | 158.2 | 99.6 | 43.9 | 48.6 | 58.7 |
| v4-io | T4 | all8 | 69.1 | 182.3 | 104.8 | 38.1 | 55.0 | 67.4 |
| v1-old | T1 | noG | 159.6 | 382.5 | 199.8 | 72.7 | 139.3 | 57.9 |
| v1-old | T2 | noG | 159.6 | 382.1 | 199.9 | 72.8 | 139.5 | 58.0 |
| v1-old | T3 | noG | 143.9 | 346.1 | 185.5 | 71.9 | 104.4 | 57.5 |
| v1-old | T4 | noG | 143.9 | 346.1 | 185.5 | 71.9 | 104.4 | 57.5 |
| v2 | T1 | noG | 89.4 | 304.2 | 148.0 | 63.9 | 58.0 | 56.2 |
| v2 | T2 | noG | 90.0 | 303.8 | 148.1 | 63.7 | 58.2 | 56.1 |
| v2 | T3 | noG | 89.1 | 256.3 | 129.5 | 67.0 | 46.3 | 58.4 |
| v2 | T4 | noG | 89.1 | 256.3 | 129.5 | 67.0 | 46.3 | 58.4 |
| v3-lite | T1 | noG | 90.1 | 304.3 | 148.2 | 64.1 | 58.3 | 56.3 |
| v3-lite | T2 | noG | 90.3 | 303.9 | 148.3 | 64.0 | 58.4 | 56.2 |
| v3-lite | T3 | noG | 89.6 | 257.2 | 130.1 | 67.2 | 46.5 | 58.5 |
| v3-lite | T4 | noG | 89.6 | 257.2 | 130.1 | 67.2 | 46.5 | 58.5 |
| v3-full | T1 | noG | 125.8 | 304.0 | 166.8 | 73.9 | 89.7 | 65.2 |
| v3-full | T2 | noG | 125.6 | 304.8 | 166.8 | 74.0 | 89.2 | 65.1 |
| v3-full | T3 | noG | 110.4 | 281.4 | 156.8 | 73.7 | 69.1 | 63.5 |
| v3-full | T4 | noG | 110.4 | 281.4 | 156.8 | 73.7 | 69.1 | 63.5 |
| v4-io | T1 | noG | 91.6 | 329.2 | 153.3 | 63.7 | 56.2 | 55.8 |
| v4-io | T2 | noG | 92.4 | 328.8 | 153.3 | 63.7 | 57.3 | 55.9 |
| v4-io | T3 | noG | 83.0 | 291.1 | 137.1 | 66.2 | 43.9 | 56.2 |
| v4-io | T4 | noG | 83.0 | 291.1 | 137.1 | 66.2 | 43.9 | 56.2 |
