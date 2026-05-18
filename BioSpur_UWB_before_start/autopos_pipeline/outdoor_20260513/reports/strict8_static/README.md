# Strict 8/8 Static Validation

这个目录只回答一个问题：如果 static Tag validation **只保留 8 个 anchor 全部有效的帧**，少一个 anchor 都丢掉，那么 V4-io 的 repeatability 会变成什么？

输入：

- Layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_20260513/FULL-COMPARE-1000/v4-io/layout.json`
- Baseline: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_20260513/FULL-COMPARE-1000/v4-io/static_all_captures.csv`
- Filter: per-frame valid anchors must be exactly A-H all present.
- Solver: same downstream sigma-weighted Huber position solve as clean rebuild.

## Main result

| Condition | captures | frames | X med | Y med | Z med | 3D med | 3D RMS | 3D p95 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| All-available | 23 | 13817 | 26.0 | 16.3 | 37.9 | 49.2 | 54.8 | 81.6 |
| Strict 8/8 only | 23 | 4408 | 23.4 | 14.8 | 37.4 | 44.5 | 49.4 | 67.0 |

Frame retention under strict 8/8: `31.9%`.

## Interpretation

Strict 8/8 会让 X/Y 和 tail 小幅变好：X median 从 `26.0mm` 变成 `23.4mm`，Y median 从 `16.3mm` 变成 `14.8mm`，3D RMS 从 `54.8mm` 变成 `49.4mm`，3D p95 从 `81.6mm` 变成 `67.0mm`。

但最重要的发现是：Z median 基本不变，从 `37.9mm` 到 `37.4mm`。也就是说，缺 anchor 帧主要恶化 horizontal/tail；Z 弱点即使在 full-response frames 里也存在。

这说明之前 all-available 的 `49mm` 结果不是被“缺 anchor 帧”简单污染出来的。更准确地说：

1. **缺 anchor 帧确实会污染 X/Y 和 tail**。strict 8/8 的 X/Y median 和 3D p95 明显更好，说明少 anchor epoch 会给 all-available 结果带来一部分尾部退化。
2. **Z weakness 更像 geometry-driven，而不是 availability-driven**。Z median 几乎不随 strict 8/8 filtering 改变，说明只靠“每帧凑齐 8 anchor”不能解决 Z。
3. **它不是 100mm+ 的主因**。strict 8/8 只把 3D median 从 `49.2mm` 改到 `44.5mm`，没有从 `100mm+` 拉回 `40mm` 这种数量级变化。
4. **低冗余危险主要发生在强制 keep-4/5/6 或 selector 长时间退化时**。All-available solve 即使有些帧少于 8 个 anchor，只要多数帧仍有 6/7/8 个 anchor，robust solver 可以吸收一部分波动。
5. **strict 8/8 是 coverage/availability 指标，不一定是 production accuracy 上界**。它只保留 `31.9%` 的帧；这些帧更干净，但不是完整 session。

因此，当前结论应该改成：

> Strict 8/8 filtering mainly improves X/Y and tail behavior, while Z remains almost unchanged. This indicates that the persistent Z weakness is geometry-driven rather than availability-driven. The 100mm+ regime is more consistent with long low-redundancy periods, such as 4/5-anchor selector behavior, rather than occasional missing-anchor frames alone.

## Worst strict-8 captures

| ID | loc | height | facing | strict8 frames | strict8 % | X | Y | Z | 3D | delta 3D vs allavail |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ID08 | edge | mid | CDHG | 117 | 19.5 | 36.7 | 32.2 | 77.0 | 91.2 | 3.0 |
| ID09 | edge | high | CDHG | 213 | 35.4 | 32.6 | 19.8 | 55.8 | 67.5 | -14.7 |
| ID07 | edge | low | CDHG | 89 | 14.8 | 29.6 | 18.9 | 51.2 | 62.1 | -13.8 |
| ID19 | center | low | CDHG | 168 | 28.0 | 28.7 | 13.8 | 45.5 | 55.6 | -4.1 |
| ID05 | edge | mid | BCGF | 160 | 26.6 | 24.0 | 14.8 | 42.6 | 51.0 | -3.0 |
| ID17 | center | low | ABEF | 215 | 35.8 | 23.7 | 16.8 | 41.6 | 50.7 | -10.8 |
| ID18 | center | low | BCGF | 240 | 39.9 | 22.4 | 13.3 | 43.2 | 50.4 | -17.5 |
| ID01 | edge | low | ABEF | 150 | 25.0 | 26.1 | 20.4 | 35.3 | 48.4 | -0.8 |

## Lowest strict-8 retention captures

| ID | loc | height | facing | all frames | strict8 frames | strict8 % | allavail 3D | strict8 3D |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ID07 | edge | low | CDHG | 600 | 89 | 14.8 | 75.9 | 62.1 |
| ID08 | edge | mid | CDHG | 601 | 117 | 19.5 | 88.2 | 91.2 |
| ID01 | edge | low | ABEF | 601 | 150 | 25.0 | 49.2 | 48.4 |
| ID21 | center | high | ABEF | 600 | 150 | 25.0 | 43.0 | 42.6 |
| ID11 | edge | mid | ADHE | 601 | 156 | 26.0 | 50.4 | 46.4 |
| ID12 | edge | high | ADHE | 601 | 157 | 26.1 | 47.0 | 41.7 |
| ID05 | edge | mid | BCGF | 601 | 160 | 26.6 | 54.1 | 51.0 |
| ID02 | edge | mid | ABEF | 601 | 162 | 27.0 | 45.4 | 41.8 |
| ID19 | center | low | CDHG | 601 | 168 | 28.0 | 59.7 | 55.6 |
| ID16 | center | mid | ADHE | 601 | 169 | 28.1 | 47.8 | 42.1 |

## Files

- `strict8_static_by_capture.csv`: per-capture strict 8/8 result and all-available comparison.
- `strict8_summary.csv`: aggregate median / p95 / RMS.
