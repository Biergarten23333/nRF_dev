# ABLATION_NO115_SUMMARY

- sweep_data: `logs/live_autopos_sweep_loop_A_to_H_100sets_20260416_103752/pairs_all.csv`
- cm_run_log: `logs/tag115_cm_fresh_20260416_154100/run.log`
- online_with115_run: `logs/tag115_online_rms_v5/run_20260416_163714`
- online_no115_run: `logs/tag115_online_rms_v5/run_20260416_175721` (in progress)
- live_online_log: `session_id=96492`

## Table 1: 2x2 Ablation Matrix — Offline Two-Level Enforced 3D Std (mm)

| Variant | With Tag115 | No Tag115 | Delta (No-With) |
|---|---:|---:|---:|
| V1 | 168.7 | 132.0 | -36.7 |
| V2 | 168.7 | 132.0 | -36.7 |
| V3-lite | 149.2 | 132.3 | -16.9 |
| V3-full | 49.3 (with) | 49.6 (no) | +0.3 |

## Table 2: Offline Two-Level — Full Detail

| Variant | X Std | Y Std | Z Std | 3D Std | Res RMS |
|---|---:|---:|---:|---:|---:|
| V1 | 59.246 | 45.390 | 151.274 | 168.683 | 21.763 |
| V2 | 59.246 | 45.390 | 151.274 | 168.683 | 21.763 |
| V3-lite | 40.728 | 41.810 | 137.333 | 149.221 | 25.491 |
| V3-full+Tag115 | 23.374 | 14.221 | 41.010 | 49.299 | 26.295 |
| V1-no115 | 41.461 | 36.957 | 119.750 | 132.004 | 27.829 |
| V2-no115 | 41.461 | 36.957 | 119.750 | 132.004 | 27.829 |
| V3-lite-no115 | 42.908 | 33.762 | 120.488 | 132.282 | 29.482 |
| V3-full-no115 | 23.397 | 14.287 | 41.300 | 49.570 | 27.936 |

## Table 3: Online All Fixes — 3D Std (first 200 points)

| Variant | With Tag115 | No Tag115 |
|---|---:|---:|
| V1 | 81.5 | 138.1 |
| V2 | 82.7 | 100.4 |
| V3-lite | 109.2 | RUNNING |
| V3-full | 94.7 | RUNNING |

## Table 4: Online 8-Anchor Only — 3D Std (first 200 points, anchors=A..H)

| Variant | With Tag115 | No Tag115 |
|---|---:|---:|
| V1 | 31.4 | 27.2 |
| V2 | 33.8 | 29.2 |
| V3-lite | 38.1 | RUNNING |
| V3-full | 32.5 | RUNNING |

## Table 5: Holdout RMS (mm)

| Variant | With Tag115 | No Tag115 |
|---|---:|---:|
| V1 | 56.972 | 59.761 |
| V2 | 56.972 | 59.761 |
| V3-lite | 58.953 | 60.968 |
| V3-full | 40.162 | 58.379 |

## Key Conclusions (Current)

- 离线 two-level 下，去掉 115 后：V1/V2 从 168.7 mm 降到 132.0 mm，V3-lite 从 149.2 mm 降到 132.3 mm。
- V3-full 在离线 two-level 下仍最稳：with115=49.3 mm，no115=49.6 mm，差异很小（+0.27 mm）。
- Holdout 上，with115 在 V1/V2/V3-lite/V3-full 都优于 no115（分别约 2.8 / 2.8 / 2.0 / 18.2 mm 改善）。
- 在线 no115 运行已在 `run_20260416_175721` 进行中，V1/V2 已落盘，V3-lite/V3-full 仍在 RUNNING。
