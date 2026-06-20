# Data Discovery

Generated: 2026-06-18T23:10:37

## Verdict

Raw per-frame range data WAS found. The static captures use `tr_all.csv` with one row per sweep-anchor observation.

- Static files found: 24
- Total raw rows: 230544
- Total valid rows: 228265
- Expected rows, 24 x 8 x 1200: 230400
- Ratio to expected: 1.001

## First Capture Structure

- File: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/captures/erlangen_20260528_optitrack/static_ID01_BSF66F_120s_20260528_110629/tag_capture_20260528_110630/tr_all.csv`
- Shape: `(9608, 31)`
- Anchor counts: `{0: 1201, 1: 1201, 2: 1201, 3: 1201, 4: 1201, 5: 1201, 6: 1201, 7: 1201}`
- Valid counts: `{1: 9506, 0: 102}`

Columns:

`host_elapsed_s, host_epoch_s, sweep, conn_id, peer_name, tag_id, plan, pmode, anchor_id, raw_mm, range_mm, quality_percent, valid, status, quality_flag_percent, first_to_last_us, frame_us, poll_count, tr_version, rx_mask, air_us, post_us, cycle_us, rx_seen, imu_valid, imu_n, acc_norm_mean_mg, acc_norm_std_mg, acc_norm_min_mg, acc_norm_max_mg, imu_skip_count`

Head:

```text
 host_elapsed_s  host_epoch_s  sweep  conn_id peer_name  tag_id plan  pmode  anchor_id  raw_mm  range_mm  quality_percent  valid status  quality_flag_percent  first_to_last_us  frame_us  poll_count  tr_version  rx_mask  air_us  post_us  cycle_us  rx_seen  imu_valid  imu_n  acc_norm_mean_mg  acc_norm_std_mg  acc_norm_min_mg  acc_norm_max_mg  imu_skip_count
       0.000086  1.779959e+09   2833      NaN    BSF66F     NaN    f      0          0    1523      1523              100      1      O                     0                 0         0           0           2      NaN     NaN      NaN       NaN      NaN          0      0               NaN              NaN              NaN              NaN               0
       0.000086  1.779959e+09   2833      NaN    BSF66F     NaN    f      0          1    1745      1745              100      1      O                     0                 0         0           0           2      NaN     NaN      NaN       NaN      NaN          0      0               NaN              NaN              NaN              NaN               0
       0.000086  1.779959e+09   2833      NaN    BSF66F     NaN    f      0          2    2156      2156              100      1      O                     0                 0         0           0           2      NaN     NaN      NaN       NaN      NaN          0      0               NaN              NaN              NaN              NaN               0
       0.000086  1.779959e+09   2833      NaN    BSF66F     NaN    f      0          3    2144      2144              100      1      O                     0                 0         0           0           2      NaN     NaN      NaN       NaN      NaN          0      0               NaN              NaN              NaN              NaN               0
       0.000086  1.779959e+09   2833      NaN    BSF66F     NaN    f      0          4    2309      2309              100      1      O                     0                 0         0           0           2      NaN     NaN      NaN       NaN      NaN          0      0               NaN              NaN              NaN              NaN               0
```

The previous v1 output tables with 192 rows are per-link feature tables, not proof that raw frames were absent. v2 still reruns B2 with PyTorch/CUDA as requested.
