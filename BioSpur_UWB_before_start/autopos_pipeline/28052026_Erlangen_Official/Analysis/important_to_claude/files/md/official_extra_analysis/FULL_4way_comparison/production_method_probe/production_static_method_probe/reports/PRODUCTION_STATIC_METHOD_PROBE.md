# Production Static Method Probe

This probe keeps production-style static aggregation: all solved frames per static position are reduced to one mean point before anchor-locked OptiTrack evaluation. Median rows are included only to explain the existing raw-replay gap.

## Entrypoint Lines

| role | file:line | symbol | note |
| --- | --- | --- | --- |
| production_static_entrypoint | `biospur_tag_positioning_offline_solver/reference_current_implementations/official_report_field_solver_13052026/run_clean_full_compare.py:822` | `evaluate_static` | loads every Static_Test ID capture, merges peer frames, sorts by time/sweep, calls solve_positions, then position_summary |
| production_frame_solver | `biospur_tag_positioning_offline_solver/reference_current_implementations/official_report_field_solver_13052026/run_clean_full_compare.py:397` | `solve_positions` | old production path solved each frame with analytic fast WLS/Huber and carried last solution as warm start |
| production_position_aggregation | `biospur_tag_positioning_offline_solver/reference_current_implementations/official_report_field_solver_13052026/run_clean_full_compare.py:464` | `position_summary` | production static point is mean_x/mean_y/mean_z, not component median |
| production_csv_write | `biospur_tag_positioning_offline_solver/reference_current_implementations/official_report_field_solver_13052026/run_clean_full_compare.py:1240` | `write static_all_captures.csv` | evaluate_static output is written per version and then to tables/static_all_captures.csv |

## Summary

| case | source | P50 3D | P95 3D | RMSE 3D |
| --- | --- | ---: | ---: | ---: |
| production_T1_current | official_production_static_all_captures | 74.0 | 282.1 | 139.6 |
| production_style_T1_mean | production_style_probe | 74.0 | 282.1 | 139.6 |
| raw_replay_T1_median | existing_raw_replay_matrix | 70.8 | 283.7 | 139.3 |
| production_style_T4_mean | production_style_probe | 72.7 | 171.5 | 109.8 |
| production_style_T4_median | production_style_probe | 69.7 | 173.9 | 108.9 |
| raw_replay_T4_median | existing_raw_replay_matrix | 69.7 | 173.9 | 108.9 |

## Headline Gaps

- T1 production-style mean P50 minus raw-replay median P50: 3.18 mm (74.0 - 70.8).
- T4 production-style mean result: 72.7 / 171.5 mm, RMSE 109.8 mm.
- T4 production-style mean P50 minus raw-replay median P50: 3.00 mm (72.7 - 69.7).

## Gap Diagnostics

| comparison | median gap | p95 abs gap |
| --- | ---: | ---: |
| current_production_minus_raw_replay_T1_median | 0.67 | 7.48 |
| production_style_T1_mean_minus_current_production | -0.00 | 0.00 |
| production_style_T1_mean_minus_raw_replay_T1_median | 0.67 | 7.48 |
| production_style_T4_mean_minus_production_style_T4_median | 0.70 | 6.43 |
| production_style_T4_mean_minus_raw_replay_T4_median | 0.70 | 6.43 |

## Interpretation

- The existing current production row matches the production-style T1 mean row within numerical noise, confirming the production static point is a per-position mean.
- The earlier raw replay row uses the default median point estimator. That estimator choice explains the production-vs-replay T1 P50 gap; it is not a different frame set or coordinate transform.
- Production-style T4 must therefore be quoted from the T4 mean row, not from the raw-replay median row.
- Go/no-go: GO for flipping the production static solver to T4 if the product target is lower tail error, but quote the verified production-style T4 numbers above. Do not claim the production path reaches the raw-replay median row unless production also switches to the median point estimator.

## Real Production Run Confirmation

After this probe, the actual production export path was switched to T4 at `solve_positions` while keeping the `position_summary` mean aggregation unchanged. The real end-to-end export evaluates to **72.691 / 171.493 mm**, RMSE **109.843 mm**, matching the probe's **72.689 / 171.497 mm**, RMSE **109.845 mm** within numerical noise. Therefore the deployed static headline is the production mean-aggregated T4 row, not the 69.7 / 173.9 median-estimator ablation.
