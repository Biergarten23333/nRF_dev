# Static Tag Absolute Accuracy

Transform source: anchor alignment only, reflection allowed, no scale. No tag truth was used for fitting.

This is the currently available production tag-solver output, not yet the full 5 Vx x 4 Tx replay matrix.

| version | eval_set | n | median_3d_mm | p95_3d_mm | rms_3d_mm | median_horizontal_mm | median_vertical_mm |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v1-old | all8 | 24 | 160.0 | 314.8 | 191.6 | 51.6 | 141.5 |
| v1-old | noG | 24 | 154.5 | 330.1 | 192.0 | 51.1 | 140.8 |
| v2 | all8 | 24 | 81.5 | 233.1 | 132.1 | 42.6 | 68.2 |
| v2 | noG | 24 | 80.5 | 242.9 | 134.0 | 46.7 | 69.7 |
| v3-full | all8 | 24 | 121.1 | 280.1 | 159.0 | 51.3 | 104.0 |
| v3-full | noG | 24 | 116.4 | 290.4 | 159.6 | 45.8 | 109.1 |
| v3-lite | all8 | 24 | 81.7 | 233.5 | 132.3 | 42.8 | 68.8 |
| v3-lite | noG | 24 | 81.1 | 243.2 | 134.2 | 47.1 | 70.2 |
| v4-io | all8 | 24 | 77.4 | 270.3 | 136.5 | 42.1 | 63.1 |
| v4-io | noG | 24 | 81.3 | 278.6 | 139.4 | 44.7 | 63.5 |
