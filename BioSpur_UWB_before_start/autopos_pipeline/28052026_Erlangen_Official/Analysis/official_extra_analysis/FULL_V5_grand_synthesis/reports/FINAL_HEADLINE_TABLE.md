# Final Headline Table

| Variant | Percentile | Weighting | D_tag | median_3d | P95 | RMSE | nested_CV_median | winners_curse_corrected_median | bootstrap_CI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V4 production | p50 | uniform | fixed_0 | 71.875 | 175.996 | 110.373 |  |  |  |
| V5 baseline | p50 | uniform | fixed_LOO_49.621 | 67.809 | 160.509 | 86.400 |  |  |  |
| V5 improved | p30 | inverse_rms | LOO_recalibrated_from_p30_range_residuals | 56.011 | 143.120 | 79.482 |  | 65.579 | [54.3, 63.7] |
| V4 improved | p30 | inverse_rms | LOO_recalibrated_from_p30_range_residuals | 54.918 | 154.784 | 79.586 |  | 64.487 |  |
| Vicon improved | p30 | inverse_rms | LOO_recalibrated_from_p30_range_residuals | 56.328 | 147.948 | 81.789 |  |  |  |
| Nested CV selected (height) | selected | selected | selected |  |  |  | 82.925 |  |  |
| Nested CV selected (quadrant) | selected | selected | selected |  |  |  | 88.042 |  |  |
| Nested CV selected (spatial6) | selected | selected | selected |  |  |  | 94.250 |  |  |
| ROTO V5 raw/current best-fit | dynamic | uniform | D_LOO | 101.485 | 214.369 | 126.226 |  |  |  |
| ROTO time-corrected SE3 | dynamic | uniform | D_LOO | 82.516 | 185.207 | 103.746 |  |  |  |
| ROTO rigid-body projection | dynamic | joint | per R3 | 280.602 |  | 315.974 |  |  |  |
