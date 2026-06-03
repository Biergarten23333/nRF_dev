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
| production v4-io/all8 | 112.9 | 139.6 | 74.0 | 184.9 | 282.1 | 345.6 | 359.6 | 58.6 | 88.2 |
| best-case raw v4-io/T3/all8 | 87.7 | 106.6 | 69.2 | 146.5 | 173.0 | 277.3 | 307.4 | 58.7 | 137.7 |
| deployment raw v4-io/T4/all8 | 91.8 | 108.9 | 69.7 | 156.5 | 173.9 | 254.6 | 278.2 | 67.4 | 88.4 |


Per-axis signed bias and P95 absolute error, OptiTrack frame with Y vertical:

| line | component | signed bias | signed std | P95 abs | 2D RMSE | 2D P50 | 2D P95 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| production v4-io/all8 | X | 22.6 | 31.8 | 71.1 |  |  |  |
| production v4-io/all8 | Y_vertical | -19.7 | 131.8 | 274.0 |  |  |  |
| production v4-io/all8 | Z | -6.3 | 30.9 | 54.1 |  |  |  |
| production v4-io/all8 | horizontal XZ 2D |  |  |  | 49.3 | 42.3 | 72.5 |


Cm-scale 3D outlier rates:

| line | >50 | >80 | >100 | >200 | >300 | <=50 | <=80 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| production v4-io/all8 | 87.5% | 45.8% | 41.7% | 8.3% | 4.2% | 12.5% | 54.2% |
| best-case raw v4-io/T3/all8 | 75.0% | 37.5% | 29.2% | 4.2% | 4.2% | 25.0% | 62.5% |
| deployment raw v4-io/T4/all8 | 79.2% | 45.8% | 37.5% | 4.2% | 0.0% | 20.8% | 54.2% |


![Static tag 3D error CDF](fig/tag_error_cdf.png)

![Static tag per-axis bias](fig/tag_error_per_axis_bias.png)

Read: production `v4-io/all8` is P50 74.0 mm and P95 282.1 mm. It has 12.5% of positions within 50 mm and 54.2% within 80 mm, but 8.3% above 200 mm and 4.2% above 300 mm. This matches the established radial/scale structure: the median is respectable, but the tail is not random isotropic noise and must stay visible in the report.
