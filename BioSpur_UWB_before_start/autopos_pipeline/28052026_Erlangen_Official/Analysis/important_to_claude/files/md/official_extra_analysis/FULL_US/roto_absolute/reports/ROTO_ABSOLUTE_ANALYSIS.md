# FULL ROTO Absolute OptiTrack Analysis

This analysis uses the corrected FULL OptiTrack export. All anchors A--H are retained; no anchor-removal evaluation is generated in this path.

Spatial alignment is anchor-locked: AutoPos layout anchors are aligned to OptiTrack antenna medians using reflection-allowed rigid Kabsch with no scale. Tag/Wand trajectories are never used to fit the spatial transform.

Because there is no trusted shared UTC timestamp, one relative time offset is estimated per ROTO capture from the primary `v4-io/T4` trajectory. The same offset is then reused for all layout/tag-solver combinations.

Selected Wand mapping: `default`; `BS2DCE -> WandBantenna`, `BSDC91 -> WandCantenna`.

## Primary v4-io/T4 Headline

- Track-median 3D P50: **105.8 mm**; track-median 3D P95: **231.8 mm**.
- Sample-weighted 3D P50/P95: **102.6 / 256.9 mm**.
- Horizontal XZ sample P50/P95: **66.1 / 179.0 mm**.
- Vertical Y sample P50/P95: **61.6 / 205.9 mm**.
- Track-median turn-center absolute 3D error: **69.1 mm**.

## Time Alignment

Offsets solved for 17 captures. Median offset is 45.512 s (range 37.708 to 48.703 s). Median primary alignment score is 101.8 mm.

The offset is an analysis variable, not a latency measurement. Periodic circular motion can create secondary local minima; see `roto_time_alignment_candidates_v4io_T4.csv`.

## Solver Matrix

| layout | tag_method | tracks_ok | err3d_p50_track_median_mm | err3d_p95_track_median_mm | err_horizontal_xz_p95_track_median_mm | err_vertical_y_p95_track_median_mm | turn_center_abs_error_3d_track_median_mm |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v1-old | T1 | 34 | 139.1 | 290.2 | 192.3 | 239.0 | 62.0 |
| v1-old | T2 | 34 | 139.1 | 290.0 | 193.0 | 240.0 | 61.9 |
| v1-old | T3 | 34 | 141.2 | 284.7 | 193.1 | 247.1 | 64.4 |
| v1-old | T4 | 34 | 134.7 | 280.6 | 197.3 | 220.0 | 63.6 |
| v2 | T1 | 34 | 110.8 | 231.6 | 169.4 | 186.1 | 77.1 |
| v2 | T2 | 34 | 110.9 | 231.9 | 170.1 | 186.1 | 77.1 |
| v2 | T3 | 34 | 113.5 | 234.7 | 167.7 | 185.4 | 78.8 |
| v2 | T4 | 34 | 110.4 | 237.0 | 164.5 | 190.3 | 77.7 |
| v3-full | T1 | 34 | 122.2 | 270.7 | 169.5 | 212.0 | 70.2 |
| v3-full | T2 | 34 | 122.0 | 270.2 | 170.4 | 212.3 | 70.1 |
| v3-full | T3 | 34 | 120.0 | 270.1 | 169.9 | 220.4 | 75.4 |
| v3-full | T4 | 34 | 117.4 | 273.5 | 172.8 | 224.3 | 70.1 |
| v3-lite | T1 | 34 | 111.0 | 231.5 | 170.4 | 186.5 | 76.8 |
| v3-lite | T2 | 34 | 111.2 | 232.2 | 170.7 | 187.5 | 76.8 |
| v3-lite | T3 | 34 | 113.4 | 235.2 | 168.6 | 186.0 | 78.9 |
| v3-lite | T4 | 34 | 110.4 | 237.1 | 165.2 | 190.1 | 78.0 |
| v4-io | T1 | 34 | 106.7 | 233.6 | 168.4 | 182.1 | 69.1 |
| v4-io | T2 | 34 | 106.5 | 232.5 | 168.1 | 181.6 | 69.0 |
| v4-io | T3 | 34 | 110.0 | 236.0 | 171.5 | 189.1 | 71.4 |
| v4-io | T4 | 34 | 105.8 | 231.8 | 167.5 | 187.4 | 69.1 |

## Output Files

- `tables/roto_abs_summary_by_solver.csv`
- `tables/roto_abs_per_track.csv`
- `tables/roto_abs_samples_v4io_T4.csv`
- `tables/roto_time_offsets_v4io_T4.csv`
- `tables/roto_time_alignment_candidates_v4io_T4.csv`
- `tables/roto_wand_mapping_decision.csv`
- `figs/roto_abs_cdf_v4io_T4.png`
- `figs/roto_abs_per_capture_v4io_T4.png`
- `figs/roto_xy_vertical_split_v4io_T4.png`
- `figs/roto_solver_matrix_median3d.png`
