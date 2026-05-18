# AutoPos 2026-05-13 XYZ Repeatability Breakdown

这份报告保留主 `README.md` 的结论口径，但把定位重复性拆成 `X / Y / Z` 三个方向。重点是看 Z 是否仍然是主要 repeatability / consistency 来源，以及不同 solver / 数据切分 / 空间区域下 Z 占比如何变化。

指标定义：

- `X std / Y std / Z std`: 每个 static capture 内，所有 solved tag positions 在对应轴上的标准差。
- `3D std`: `sqrt(X_std^2 + Y_std^2 + Z_std^2)`。
- `Z share`: `Z_std^2 / (X_std^2 + Y_std^2 + Z_std^2)`，表示 3D 方差里有多少来自 Z。
- 没有 OptiTrack，因此这些是 repeatability / consistency，不是 absolute accuracy。
- 表格中的 median 是对每个 capture 的对应指标分别取中位数。因此 `3D med` 是 per-capture `3D std` 的 median，不是由 `X med / Y med / Z med` 重新计算出来的。

## 1. V4-io 主结果：XYZ 拆解

| Dataset | N | X med | Y med | Z med | Horizontal med | 3D med | Z share med | X p95 | Y p95 | Z p95 | 3D p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| FULL-COMPARE-1000 | 23 | 26.0 | 16.3 | 37.9 | 30.4 | 49.2 | 62.1% | 41.5 | 21.9 | 67.8 | 81.6 |
| FULL-COMPARE-500 | 23 | 26.4 | 16.5 | 39.9 | 30.5 | 48.4 | 63.8% | 43.1 | 22.0 | 68.4 | 80.9 |
| FULL-COMPARE-500+500 | 23 | 26.3 | 16.6 | 40.7 | 30.5 | 48.4 | 63.7% | 43.1 | 21.9 | 68.6 | 80.6 |

解读：V4-io 的 X/Y median 大约 26mm / 16mm，而 Z median 大约 38-41mm。Z 不是唯一误差源，但它贡献了约 62-64% 的 3D variance，是当前最值得继续优化的轴。

## 2. 不同 Solver 的 Static XYZ 对比

### FULL-COMPARE-1000

| Version | N | X med | Y med | Z med | 3D med | Z share med | Z / horizontal | Z p95 | 3D p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| v2 | 23 | 24.7 | 16.2 | 38.6 | 48.7 | 62.6% | 1.3x | 60.6 | 71.0 |
| v3-lite | 23 | 24.7 | 16.2 | 38.5 | 48.7 | 62.3% | 1.3x | 60.2 | 70.9 |
| v4-io | 23 | 26.0 | 16.3 | 37.9 | 49.2 | 62.1% | 1.2x | 67.8 | 81.6 |
| v4-io-td | 23 | 25.9 | 16.2 | 37.5 | 48.9 | 62.9% | 1.2x | 68.1 | 82.8 |
| v4-io-roto | 23 | 25.0 | 16.6 | 37.5 | 48.1 | 63.1% | 1.2x | 63.4 | 71.9 |
| v4-io-wand | 23 | 26.2 | 16.0 | 38.2 | 48.6 | 60.3% | 1.3x | 65.5 | 77.3 |

### FULL-COMPARE-500

| Version | N | X med | Y med | Z med | 3D med | Z share med | Z / horizontal | Z p95 | 3D p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| v2 | 23 | 24.9 | 16.3 | 39.6 | 48.3 | 63.4% | 1.3x | 63.3 | 72.0 |
| v3-lite | 23 | 25.0 | 16.3 | 39.5 | 48.6 | 63.4% | 1.3x | 63.3 | 71.9 |
| v4-io | 23 | 26.4 | 16.5 | 39.9 | 48.4 | 63.8% | 1.3x | 68.4 | 80.9 |
| v4-io-td | 23 | 26.5 | 16.3 | 38.4 | 47.9 | 63.6% | 1.3x | 68.8 | 82.3 |
| v4-io-roto | 23 | 25.2 | 16.9 | 37.7 | 48.2 | 62.0% | 1.2x | 61.9 | 74.5 |
| v4-io-wand | 23 | 26.4 | 15.9 | 38.8 | 48.2 | 60.1% | 1.3x | 66.9 | 79.1 |

### FULL-COMPARE-500+500

| Version | N | X med | Y med | Z med | 3D med | Z share med | Z / horizontal | Z p95 | 3D p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| v2 | 23 | 25.0 | 16.3 | 39.9 | 48.5 | 63.3% | 1.3x | 63.5 | 72.2 |
| v3-lite | 23 | 25.0 | 16.3 | 39.8 | 48.6 | 63.3% | 1.3x | 63.3 | 72.1 |
| v4-io | 23 | 26.3 | 16.6 | 40.7 | 48.4 | 63.7% | 1.3x | 68.6 | 80.6 |
| v4-io-td | 23 | 26.4 | 16.5 | 39.4 | 48.0 | 63.8% | 1.3x | 68.9 | 81.8 |
| v4-io-roto | 23 | 25.3 | 17.0 | 37.8 | 48.0 | 62.3% | 1.3x | 62.3 | 75.1 |
| v4-io-wand | 23 | 26.3 | 16.0 | 39.1 | 48.1 | 59.4% | 1.3x | 67.2 | 79.3 |

解读：V2/V3-lite 在 static median 上很接近 V4-io；V4-io-roto 通常能压低 static tail，但它使用了 Roto 信息注入，不是纯 inter-anchor holdout。Common Tag delay (`V4-io-td`) 对 XYZ 分解影响很小，说明当前 static repeatability 不是由一个统一 tag delay 主导。

## 3. V4-io 空间分组：哪里 Z 更弱？

### 按位置

| Group | N | X med | Y med | Z med | 3D med | Z share med | Z p95 | 3D p95 | worst ID by 3D |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| center | 12 | 25.9 | 15.0 | 36.5 | 46.9 | 63.0% | 55.8 | 64.4 | ID18 (67.9) |
| edge | 11 | 26.3 | 18.1 | 41.8 | 50.4 | 62.1% | 69.6 | 85.2 | ID08 (88.2) |

### 按高度

| Group | N | X med | Y med | Z med | 3D med | Z share med | Z p95 | 3D p95 | worst ID by 3D |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| high | 8 | 25.9 | 15.2 | 29.3 | 41.6 | 47.3% | 54.2 | 69.9 | ID09 (82.3) |
| low | 7 | 27.1 | 16.4 | 49.9 | 59.7 | 70.8% | 65.9 | 73.5 | ID07 (75.9) |
| mid | 8 | 22.7 | 16.6 | 39.8 | 48.8 | 66.1% | 61.0 | 76.3 | ID08 (88.2) |

### 按朝向

| Group | N | X med | Y med | Z med | 3D med | Z share med | Z p95 | 3D p95 | worst ID by 3D |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| ABEF | 6 | 23.6 | 17.6 | 36.1 | 47.3 | 64.4% | 49.4 | 58.6 | ID17 (61.5) |
| ADHE | 5 | 25.9 | 17.7 | 35.4 | 47.8 | 54.8% | 48.4 | 56.1 | ID20 (57.6) |
| BCGF | 6 | 25.8 | 14.2 | 39.7 | 49.7 | 63.4% | 56.7 | 64.4 | ID18 (67.9) |
| CDHG | 6 | 30.4 | 17.6 | 56.0 | 67.8 | 63.0% | 70.3 | 86.7 | ID08 (88.2) |

空间结论：低高度和 `CDHG` facing 的 Z/std 更容易变大；这和主报告中 CDHG tail 较大的结论一致。Z 的问题不是均匀分布的，而是和空间区域、朝向、可见 anchor 组合有关。

注意：这些空间分组的样本数较小，特别是 facing 分组通常只有 `N=5/6`。因此分组 `p95` 更应该被理解为 near-worst-case 指示值，而不是稳定的统计尾部估计。

## 4. V4-io 最差 Static Captures：XYZ 明细

| Rank | ID | location | height | facing | X std | Y std | Z std | 3D std | Z share | pct >=8 anchors |
| ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | ID08 | edge | mid | CDHG | 42.5 | 30.3 | 71.1 | 88.2 | 64.9% | 19.5 |
| 2 | ID09 | edge | high | CDHG | 46.0 | 21.1 | 64.9 | 82.3 | 62.1% | 35.4 |
| 3 | ID07 | edge | low | CDHG | 27.5 | 18.8 | 68.2 | 75.9 | 80.7% | 14.8 |
| 4 | ID18 | center | low | BCGF | 27.1 | 13.7 | 60.7 | 67.9 | 80.0% | 39.9 |
| 5 | ID17 | center | low | ABEF | 26.8 | 19.7 | 51.8 | 61.5 | 70.8% | 35.8 |
| 6 | ID19 | center | low | CDHG | 32.9 | 16.4 | 47.0 | 59.7 | 62.1% | 28.0 |
| 7 | ID20 | center | low | ADHE | 24.4 | 15.0 | 49.9 | 57.6 | 75.3% | 49.8 |
| 8 | ID05 | edge | mid | BCGF | 29.8 | 17.0 | 41.8 | 54.1 | 59.6% | 26.6 |
| 9 | ID04 | edge | low | BCGF | 25.6 | 14.1 | 44.7 | 53.4 | 70.0% | 58.1 |
| 10 | ID11 | edge | mid | ADHE | 21.3 | 17.7 | 42.2 | 50.4 | 69.9% | 26.0 |

最差样本大多不是 X/Y 同时爆炸，而是 Z 或单轴尾部把 3D 拉高。这说明后续优化应该优先检查垂直几何、上下层 anchor 质量、以及特定朝向下的 NLOS/天线遮挡。

## 5. Roto 每转圆心重复性：XYZ 拆解

Roto 不再把 raw circle thickness 当动态定位误差。这里仅拆解每转一圈拟合出的圆心稳定性：如果同一个 Roto capture 转了很多圈，每圈拟合出的圆心应该接近。

| Dataset | Version | Tag | N captures | center X std med | center Y std med | center Z std med | center 3D RMS med | Z share med |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| FULL-COMPARE-1000 | v4-io | BS2DCE | 17 | 11.9 | 10.0 | 18.6 | 23.9 | 57.5% |
| FULL-COMPARE-1000 | v4-io | BSDC91 | 17 | 8.9 | 8.8 | 12.9 | 17.4 | 54.9% |
| FULL-COMPARE-1000 | v4-io-roto | BS2DCE | 17 | 10.4 | 8.2 | 15.0 | 19.8 | 56.2% |
| FULL-COMPARE-1000 | v4-io-roto | BSDC91 | 17 | 8.7 | 7.5 | 11.8 | 16.6 | 51.9% |
| FULL-COMPARE-1000 | v4-io-wand | BS2DCE | 17 | 11.2 | 9.0 | 16.7 | 22.8 | 57.8% |
| FULL-COMPARE-1000 | v4-io-wand | BSDC91 | 17 | 9.1 | 8.3 | 12.3 | 16.7 | 53.5% |
| FULL-COMPARE-500 | v4-io | BS2DCE | 17 | 11.8 | 10.1 | 18.9 | 24.2 | 57.2% |
| FULL-COMPARE-500 | v4-io | BSDC91 | 17 | 9.3 | 8.4 | 13.3 | 18.2 | 53.1% |
| FULL-COMPARE-500 | v4-io-roto | BS2DCE | 17 | 10.7 | 8.3 | 15.1 | 21.4 | 55.0% |
| FULL-COMPARE-500 | v4-io-roto | BSDC91 | 17 | 8.8 | 7.4 | 11.7 | 17.0 | 52.4% |
| FULL-COMPARE-500 | v4-io-wand | BS2DCE | 17 | 11.5 | 9.5 | 17.6 | 23.6 | 56.7% |
| FULL-COMPARE-500 | v4-io-wand | BSDC91 | 17 | 9.4 | 7.9 | 12.4 | 17.1 | 53.2% |
| FULL-COMPARE-500+500 | v4-io | BS2DCE | 17 | 11.9 | 10.4 | 19.2 | 24.6 | 57.2% |
| FULL-COMPARE-500+500 | v4-io | BSDC91 | 17 | 9.5 | 8.5 | 13.4 | 18.2 | 53.8% |
| FULL-COMPARE-500+500 | v4-io-roto | BS2DCE | 17 | 10.8 | 8.4 | 15.2 | 21.4 | 55.4% |
| FULL-COMPARE-500+500 | v4-io-roto | BSDC91 | 17 | 8.9 | 7.4 | 11.7 | 17.0 | 52.4% |
| FULL-COMPARE-500+500 | v4-io-wand | BS2DCE | 17 | 11.7 | 9.7 | 17.9 | 23.8 | 56.4% |
| FULL-COMPARE-500+500 | v4-io-wand | BSDC91 | 17 | 9.4 | 8.0 | 12.5 | 17.1 | 53.4% |

Roto 每转圆心的 XYZ 分解比 raw circle thickness 更适合讲：它描述的是重复转动时中心估计是否稳定，而不是把真实运动轨迹厚度误写成动态定位误差。

## 6. Wand XYZ 能拆到什么程度？

当前 clean rebuild 保存的是 W01-W04 三 Tag 的 pairwise distance median/bias，没有保存逐帧三颗 Tag 的完整 XYZ 相对坐标表。因此本报告能给出 Wand-as-Tag 的边长 bias RMS，但不能在现有输出文件里严格拆成 `X/Y/Z` 三个方向。

如果下一轮要让 Wand 也支持 XYZ 分解，需要在 solver 输出中额外保存每个 Wand capture、每颗 Wand Tag、每帧 solved position：

- `capture_id`
- `peer_name`
- `sweep/time`
- `x_mm, y_mm, z_mm`
- `anchors_used / pct_ge8 / residual`

有了这个表以后，可以进一步分析三颗 Tag 的相对向量 `p_B - p_A`、`p_C - p_A` 在 X/Y/Z 方向上的 bias 和 drift。

## 7. 可直接讲的版本

中文：

> 当前无 OptiTrack，因此我们报告的是重复性而不是绝对精度。把 static Tag repeatability 拆成 X/Y/Z 后可以看到，X/Y 通常在 16-26mm 量级，Z 通常在 38-41mm 量级，Z 贡献了约 62-64% 的 3D variance。因此系统当前不是完全随机等方误差，而是仍然存在明显的垂直方向弱点。低高度、CDHG 朝向和部分 edge 区域的 Z tail 更明显。Roto 每转圆心重复性进一步说明，运动学一致性在 20mm 量级，但 raw circle thickness 不应被解释为动态定位误差。

English:

> Without OptiTrack, the reported positioning numbers are repeatability metrics rather than absolute accuracy. Decomposing the static tag repeatability into X/Y/Z shows that X/Y are typically around 16-26 mm, while Z is around 38-41 mm and contributes roughly 62-64% of the total 3D variance. The current error is therefore not isotropic; vertical observability remains the dominant weakness, especially for low-height, CDHG-facing, and some edge-region captures. The roto per-revolution center analysis provides a cleaner kinematic consistency metric around the 20 mm level, while raw circle thickness should not be interpreted as dynamic positioning error.

## 8. 对下一步实验/报告的建议

1. 主报告里同时给 `3D std` 和 `X/Y/Z std`，否则读者看不出是不是 Z 在拖后腿。
2. 对教授汇报时，优先讲 `Z share`，因为它比单独 Z std 更能说明 3D error 的来源。
3. 如果后面有 OptiTrack，必须同时报告 absolute error 的 X/Y/Z bias 和 std；当前只能报告 repeatability。
4. Wand 下一轮最好保存 per-frame XYZ，不然只能讲边长 consistency，不能讲方向性误差。
