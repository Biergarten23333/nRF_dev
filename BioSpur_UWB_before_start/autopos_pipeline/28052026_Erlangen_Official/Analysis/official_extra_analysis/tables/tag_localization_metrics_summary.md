### Localization metric set (cm-scale)

This metric pass is limited to the existing corrected static-tag error outputs. It
does not rerun solvers or regenerate layout, DOP, MC, drift, or the nine additional
diagnostics.

Excluded standard metrics: latency, update rate, drop rate, max gap, availability,
and jitter are not reported because this is an offline static replay, not a real-time
online stream. The static analogue of jitter is repeatability (`D3 std`), which is
cross-referenced below from existing outputs. 2D is also not promoted to the headline:
the system contribution is pure-UWB 3D self-calibration, so 3D remains primary and
OptiTrack Y vertical error is reported explicitly.

3D error percentile set, mm:

| line | mean | RMSE | P50 | P90 | P95 | P99 | max | D3 std P50 | D3 std P95 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| production v4-io/all8 | 111.2 | 138.3 | 77.4 | 187.7 | 270.3 | 349.9 | 369.6 | 58.6 | 88.2 |
| production v4-io/noG | 115.6 | 141.1 | 81.3 | 183.8 | 278.6 | 349.5 | 365.7 | 58.6 | 88.2 |
| best-case raw v4-io/T3/all8 | 84.1 | 101.8 | 62.3 | 149.2 | 158.2 | 261.4 | 292.2 | 58.7 | 137.7 |
| deployment raw v4-io/T4/all8 | 90.2 | 107.0 | 69.1 | 155.7 | 182.3 | 246.0 | 263.8 | 67.4 | 88.4 |


Per-axis signed bias and P95 absolute error, OptiTrack frame with Y vertical:

| line | component | signed bias | signed std | P95 abs | 2D RMSE | 2D P50 | 2D P95 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| production v4-io/all8 | X | 23.7 | 32.4 | 79.9 |  |  |  |
| production v4-io/all8 | Y_vertical | -6.2 | 130.8 | 259.4 |  |  |  |
| production v4-io/all8 | Z | -8.8 | 32.6 | 58.0 |  |  |  |
| production v4-io/all8 | horizontal XZ 2D |  |  |  | 51.6 | 43.8 | 82.8 |
| production v4-io/noG | X | 34.5 | 31.9 | 84.3 |  |  |  |
| production v4-io/noG | Y_vertical | -13.3 | 131.8 | 267.1 |  |  |  |
| production v4-io/noG | Z | 0.6 | 31.2 | 54.9 |  |  |  |
| production v4-io/noG | horizontal XZ 2D |  |  |  | 55.7 | 46.9 | 86.2 |


Cm-scale 3D outlier rates:

| line | >50 | >80 | >100 | >200 | >300 | <=50 | <=80 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| production v4-io/all8 | 75.0% | 41.7% | 41.7% | 8.3% | 4.2% | 25.0% | 58.3% |
| production v4-io/noG | 87.5% | 50.0% | 41.7% | 8.3% | 4.2% | 12.5% | 50.0% |
| best-case raw v4-io/T3/all8 | 66.7% | 33.3% | 29.2% | 4.2% | 0.0% | 33.3% | 66.7% |
| deployment raw v4-io/T4/all8 | 70.8% | 37.5% | 37.5% | 4.2% | 0.0% | 29.2% | 62.5% |


![Static tag 3D error CDF](fig/tag_error_cdf.png)

![Static tag per-axis bias](fig/tag_error_per_axis_bias.png)

Read: production `v4-io/all8` is P50 77.4 mm and P95 270.3 mm. It has 25.0% of positions within 50 mm and 58.3% within 80 mm, but 8.3% above 200 mm and 4.2% above 300 mm. This matches the established radial/scale structure: the median is respectable, but the tail is not random isotropic noise and must stay visible in the report.
