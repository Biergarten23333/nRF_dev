# ROTO Dynamic Diagnostics

Generated 2026-06-04T14:31:55.427608+00:00.

This report decomposes the already time-aligned ROTO absolute samples by angular speed, rotation phase, radius error, horizontal/vertical dynamic error, and two-tag relative-distance consistency.

- Speed bins: best median 3D `mid 68.0-84.1 deg/s` = 101.3 mm; worst `slow <= 68.0 deg/s` = 108.4 mm.
- Phase-bin median 3D errors: 0-90:106.1, 180-270:106.5, 270-360:97.2, 90-180:106.9 mm.
- Track-level radius absolute error median-of-medians/P95-medians: 50.5 / 143.5 mm.
- Two-wand relative-distance abs error median-of-medians/P95-medians: 59.6 / 174.5 mm.

## Tables

- `tables/roto_dynamic_samples_v4io_T4.csv`
- `tables/roto_error_by_angular_speed.csv`
- `tables/roto_error_by_phase.csv`
- `tables/roto_radius_error_by_track.csv`
- `tables/roto_two_wand_relative_distance_summary.csv`
