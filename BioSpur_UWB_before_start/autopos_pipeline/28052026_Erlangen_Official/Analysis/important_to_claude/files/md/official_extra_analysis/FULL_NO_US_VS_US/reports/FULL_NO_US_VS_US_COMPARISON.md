# FULL No-US vs US30 Height-Gauge Comparison

Generated 2026-06-04T14:50:04.983310+00:00.

This report compares the original FULL 4-way analysis against the US30/FGH height-gauged rerun. It deliberately separates legacy anchor-locked metrics from the new US height-preserving check.

## Key Point

- Legacy anchor-locked rows with effectively zero US/no-US delta: 8 / 10. This is expected when a metric permits a full 3D rigid/capture alignment that can absorb a gauge-only coordinate change.
- US height-preserving v4-io static point error: P50 72.5 mm, P95 284.8 mm, RMSE 139.1 mm.
- US height-preserving v4-io anchor error: P50 98.4 mm, P95 162.1 mm, RMSE 109.7 mm.

## Legacy No-US vs US Metrics

| metric_id | family | no_us_p50_mm | no_us_p95_mm | us_p50_mm | us_p95_mm | delta_us_minus_no_us_p50_mm | delta_us_minus_no_us_p95_mm |
| --- | --- | --- | --- | --- | --- | --- | --- |
| static_production_anchor_locked_v4io | static_anchor_locked | 73.96 | 282.13 | 73.96 | 282.13 | 0.00 | -0.00 |
| static_raw_replay_anchor_locked_v4io_T4 | static_anchor_locked | 69.69 | 173.93 | 69.75 | 173.81 | 0.06 | -0.11 |
| static_filtered_anchor_locked_v4io_T4_F5 | static_filter_anchor_locked | 64.85 | 175.73 | 64.88 | 175.69 | 0.03 | -0.03 |
| static_one_baseline_EH_delaycal_v4io_T4 | static_4way_anchor_locked | 58.12 | 130.22 | 58.12 | 130.22 | -0.00 | -0.00 |
| roto_original_anchor_locked_v4io_T4 | roto_anchor_locked | 105.84 | 231.80 | 105.84 | 231.80 | -0.00 | 0.00 |
| roto_one_baseline_best_T4 | roto_4way_anchor_locked | 100.10 | 220.46 | 100.10 | 220.46 | 0.00 | 0.00 |
| roto_filtered_F4_original_v4io_T4 | roto_filter_anchor_locked | 86.30 | 158.19 | 86.30 | 158.19 | -0.00 | 0.00 |
| roto_filtered_F5_original_v4io_T4 | roto_filter_anchor_locked | 83.31 | 148.64 | 83.31 | 148.64 | -0.00 | -0.00 |
| roto_pseudo_imu_PI1_original_v4io_T4 | roto_pseudo_imu_anchor_locked | 66.10 | 97.52 | 66.10 | 97.52 | -0.00 | 0.00 |
| roto_pseudo_imu_PI4_original_v4io_T4 | roto_pseudo_imu_anchor_locked | 58.67 | 81.52 | 58.67 | 81.52 | 0.00 | 0.00 |

## US Height-Preserving Metric

The height-preserving check fits only a 2D horizontal rigid transform plus one vertical shift from US-gauge anchors to OptiTrack. It does not allow 3D pitch/roll or global scale to erase the US height gauge. This is a deployment-gauge diagnostic, not the old paper headline metric.

## Output Tables

- `../tables/no_us_vs_us_headline.csv`
- `../tables/us_height_preserving_anchor_errors.csv`
- `../tables/us_height_preserving_static_errors.csv`
