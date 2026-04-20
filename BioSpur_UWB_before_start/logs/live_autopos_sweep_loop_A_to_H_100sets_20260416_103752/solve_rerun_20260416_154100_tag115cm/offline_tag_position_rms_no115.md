# Offline Tag Position RMS Compare

- cm_run_log: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_cm_fresh_20260416_154100/run.log`
- cm_samples_total: `80`
- min_quality: `0`
- min_anchors: `4`
- quality_window: `10`
- volume_min_m3: `1e-06`
- volume_max_m3: `0.1`
- require_two_level(2 lower + 2 upper): `False`

| Layout | solved_samples | pos_std_x(mm) | pos_std_y(mm) | pos_std_z(mm) | pos_std_3d(mm) | residual_mean_rms(mm) | residual_global_rms(mm) |
|---|---:|---:|---:|---:|---:|---:|---:|
| V1-no115 | 80 | 40.636 | 37.870 | 273.293 | 278.881 | 24.959 | 27.547 |
| V2-no115 | 80 | 40.636 | 37.870 | 273.293 | 278.881 | 24.959 | 27.547 |
| V3-lite-no115 | 80 | 42.113 | 35.264 | 206.860 | 214.028 | 26.529 | 29.054 |
| V3-full-no115 | 80 | 24.594 | 15.541 | 182.277 | 184.585 | 26.441 | 28.918 |

## Selection Filter Stats

| Layout | skipped_no_valid4 | skipped_geom_filter |
|---|---:|---:|
| V1-no115 | 0 | 0 |
| V2-no115 | 0 | 0 |
| V3-lite-no115 | 0 | 0 |
| V3-full-no115 | 0 | 0 |
