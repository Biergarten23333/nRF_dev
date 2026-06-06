# ROTO Dynamic Diagnostics

Generated 2026-06-04T14:31:59.276879+00:00.

This report decomposes the already time-aligned ROTO absolute samples by angular speed, rotation phase, radius error, horizontal/vertical dynamic error, and two-tag relative-distance consistency.

- Speed bins: best median 3D `mid 68.0-84.1 deg/s` = 104.6 mm; worst `slow <= 68.0 deg/s` = 110.5 mm.
- Phase-bin median 3D errors: 0-90:109.3, 180-270:110.3, 270-360:100.1, 90-180:109.9 mm.
- Track-level radius absolute error median-of-medians/P95-medians: 50.6 / 142.6 mm.
- Two-wand relative-distance abs error median-of-medians/P95-medians: 61.3 / 180.3 mm.

## Tables

- `tables/roto_dynamic_samples_v4io_T4.csv`
- `tables/roto_error_by_angular_speed.csv`
- `tables/roto_error_by_phase.csv`
- `tables/roto_radius_error_by_track.csv`
- `tables/roto_two_wand_relative_distance_summary.csv`
