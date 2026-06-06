# ROTO Dynamic Diagnostics

Generated 2026-06-03T15:17:49.135647+00:00.

This report decomposes the already time-aligned ROTO absolute samples by angular speed, rotation phase, radius error, horizontal/vertical dynamic error, and two-tag relative-distance consistency.

- Speed bins: best median 3D `mid 68.0-84.1 deg/s` = 101.9 mm; worst `fast > 84.1 deg/s` = 103.2 mm.
- Phase-bin median 3D errors: 0-90:101.1, 180-270:107.5, 270-360:100.6, 90-180:100.4 mm.
- Track-level radius absolute error median-of-medians/P95-medians: 59.2 / 171.3 mm.
- Two-wand relative-distance abs error median-of-medians/P95-medians: 104.6 / 254.8 mm.

## Tables

- `tables/roto_dynamic_samples_v4io_T4.csv`
- `tables/roto_error_by_angular_speed.csv`
- `tables/roto_error_by_phase.csv`
- `tables/roto_radius_error_by_track.csv`
- `tables/roto_two_wand_relative_distance_summary.csv`
