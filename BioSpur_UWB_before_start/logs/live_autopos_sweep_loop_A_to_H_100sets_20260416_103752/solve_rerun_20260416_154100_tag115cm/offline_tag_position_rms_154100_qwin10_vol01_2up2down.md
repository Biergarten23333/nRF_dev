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
| V1 | 79 | 59.246 | 45.390 | 151.274 | 168.683 | 21.763 | 24.228 |
| V2 | 79 | 59.246 | 45.390 | 151.274 | 168.683 | 21.763 | 24.228 |
| V3-lite | 79 | 40.728 | 41.810 | 137.333 | 149.221 | 25.491 | 27.766 |
| V3-full+Tag115 | 78 | 23.374 | 14.221 | 41.010 | 49.299 | 26.295 | 28.738 |
| V3-full-no115 | 78 | 23.397 | 14.287 | 41.300 | 49.570 | 27.936 | 30.281 |

## Selection Filter Stats

| Layout | skipped_no_valid4 | skipped_geom_filter |
|---|---:|---:|
| V1 | 0 | 1 |
| V2 | 0 | 1 |
| V3-lite | 0 | 1 |
| V3-full+Tag115 | 0 | 2 |
| V3-full-no115 | 0 | 2 |
