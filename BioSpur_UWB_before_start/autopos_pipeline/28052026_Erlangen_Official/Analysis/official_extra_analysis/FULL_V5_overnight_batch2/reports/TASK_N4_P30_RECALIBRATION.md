# Task N4 - p30 Recalibration

Generated: 2026-06-18T01:08:36

Full p30 anchor self-calibration was not run because no isolated anchor self-calibration API was exposed by the prior scripts. This task therefore reports the required fallback: p30 D_tag recalibration on the existing V5 layout.

| pipeline | range_percentile | layout_source | dtag_mode | d_tag_mm | median_3d | p95 | rmse | vertical_slope | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V5_p50_layout_p50_ranges_DLOO | 50 | V5_p50_existing | fixed_p50_LOO | 49.621 | 67.809 | 160.509 | 86.400 | 76.029 |  |
| V5_p50_layout_p30_ranges_Dtag_p50 | 30 | V5_p50_existing | fixed_p50_LOO_on_p30_ranges | 49.621 | 47.496 | 135.633 | 75.030 | 38.613 |  |
| V5_p50_layout_p30_ranges_Dtag_p30_LOO | 30 | V5_p50_existing | LOO_from_p30_range_residuals | 32.986 | 59.842 | 158.761 | 81.403 | 73.267 | fallback: full p30 anchor self-calibration was not rerun |
| V5_p50_layout_p30_ranges_invRMS_Dtag_p30_LOO | 30 | V5_p50_existing | LOO_from_p30_range_residuals | 32.986 | 56.011 | 143.120 | 79.482 | 71.672 | fallback best-practice; full p30 anchor self-calibration requires invoking anchor calibration pipeline source |
