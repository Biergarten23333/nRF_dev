# Follow-up Validation Summary

Generated: 2026-06-18T00:52:25

| Task | Key Finding |
| --- | --- |
| F1 | p30+inverse-RMS+recal = 56.0 mm; it does not break 45 mm |
| F2 | ROTO p30 does not transfer: best p30/median window = 283.9 mm vs raw/p50 101.5 mm |
| F3 | scalar stratified mean median 68.2 mm, std 8.7 mm |
| F4 | fair LOO recalibration shifts V5 optimum to p20 = 53.8 mm; p30 = 59.8 mm |
| F5 | selective_DF_p30_else_p50 = 47.3 mm |
| F6 | specified headline variants: V5 improved = 56.0 mm; best row is V4 improved = 54.9 mm |

## Final Headline Table

| variant | percentile | weighting | d_tag_mode | d_tag_value | median_3d_mm | p95_3d_mm | rmse_3d_mm |
| --- | --- | --- | --- | --- | --- | --- | --- |
| V4 production | p50 | uniform | fixed_0 | 0.000 | 71.875 | 175.996 | 110.373 |
| V5 baseline | p50 | uniform | fixed_LOO_49.621 | 49.621 | 67.809 | 160.509 | 86.400 |
| V5 improved | p30 | inverse_rms | LOO_recalibrated_from_p30_range_residuals | 32.986 | 56.011 | 143.120 | 79.482 |
| V4 improved | p30 | inverse_rms | LOO_recalibrated_from_p30_range_residuals | 18.236 | 54.918 | 154.784 | 79.586 |
| Vicon improved | p30 | inverse_rms | LOO_recalibrated_from_p30_range_residuals | 28.265 | 56.328 | 147.948 | 81.789 |

## Runtime

| task | elapsed_s | mean_cpu_percent | max_cpu_percent | workers | physical_cores | logical_cores |
| --- | --- | --- | --- | --- | --- | --- |
| F1 | 0.384 | 12.392 | 33.300 | 6 | 6 | 12 |
| F4 | 0.305 | 13.095 | 21.700 | 6 | 6 | 12 |
| F3 | 0.522 | 11.340 | 16.700 | 6 | 6 | 12 |
| F5 | 0.017 | 10.500 | 10.500 | 6 | 6 | 12 |
| F2 | 47.004 | 54.533 | 66.700 | 6 | 6 | 12 |
| F6 | 0.056 | 9.825 | 15.000 | 6 | 6 | 12 |
