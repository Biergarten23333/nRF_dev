# Offline Tag Position RMS Compare

- cm_run_log: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_cm_fresh_20260416_154100/run.log`
- cm_samples_total: `80`
- min_quality: `0`
- min_anchors: `4`
- quality_window: `10`
- volume_min_m3: `1e-06`
- volume_max_m3: `0.1`
- require_two_level(2 lower + 2 upper): `True`

| Layout | solved_samples | pos_std_x(mm) | pos_std_y(mm) | pos_std_z(mm) | pos_std_3d(mm) | residual_mean_rms(mm) | residual_global_rms(mm) |
|---|---:|---:|---:|---:|---:|---:|---:|
| V1-no115 | 79 | 41.461 | 36.957 | 119.750 | 132.004 | 27.829 | 30.088 |
| V2-no115 | 79 | 41.461 | 36.957 | 119.750 | 132.004 | 27.829 | 30.088 |
| V3-lite-no115 | 79 | 42.908 | 33.762 | 120.488 | 132.282 | 29.482 | 31.642 |
| V3-full-no115 | 78 | 23.397 | 14.287 | 41.300 | 49.570 | 27.936 | 30.281 |

## Selection Filter Stats

| Layout | skipped_no_valid4 | skipped_geom_filter |
|---|---:|---:|
| V1-no115 | 0 | 1 |
| V2-no115 | 0 | 1 |
| V3-lite-no115 | 0 | 1 |
| V3-full-no115 | 0 | 2 |
