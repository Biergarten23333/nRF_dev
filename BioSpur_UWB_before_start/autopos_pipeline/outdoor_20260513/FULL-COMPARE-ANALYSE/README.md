# AutoPos 2026-05-13 Full Compare 分析报告

本文是 `outdoor_20260513` 三套 clean rebuild 结果的集中分析。中文优先，方便直接整理给教授；关键算法名保留英文，避免和代码/表格脱节。

本次 rebuild 新增了一个版本：

```text
v4-io-td = V4-io fixed layout + static common Tag-delay scan
```

它不是新的 Anchor layout solver。它保持 `V4-io` 的 anchor 坐标和 per-anchor delay 不变，只用 static captures 扫描一个 common type-level Tag delay，然后重新做下游 static / roto / wand evaluation。

---

## 1. 本次分析目录

三套正式结果：

| Directory | Meaning |
|---|---|
| `FULL-COMPARE-1000` | 使用全部 1000-set inter-anchor sweep 解 layout |
| `FULL-COMPARE-500` | 使用 first-500 sweep 解 layout，并用 last-500 做 holdout |
| `FULL-COMPARE-500+500` | first-500 和 last-500 分别解 layout，再 align + consensus |

核心脚本：

```text
autopos_pipeline/outdoor_20260513/run_clean_full_compare.py
```

核心输出：

```text
tables/version_summary.csv
tables/autopos_quality_summary.csv
tables/delay_sanity.csv
tables/static_all_captures.csv
tables/roto_all_captures.csv
tables/wand_static_summary.csv
v4-io-td/tag_delay_scan.csv
v4-io-td/tag_delay_scan_first500.csv
v4-io-td/tag_delay_scan_last500.csv
reports/full_compare_report.md
figures/
```

---

## 2. 这次到底回答什么问题？

这次不是单纯问“哪个版本 Tag RMS 最低”。我们要回答三层问题：

1. **AutoPos anchor layout 自身是否可靠？**
   - 看 inter-anchor residual / holdout / split stability / delay sanity。
   - 这部分不依赖 static Tag RMS。

2. **AutoPos layout 对下游定位是否稳定？**
   - 用 static repeatability 验证。
   - 用 roto circle-fit 验证。
   - 用 wand rigid-body distance 验证。

3. **Tag delay 是否值得加入当前 pipeline？**
   - 新增 `v4-io-td`，估一个 common type-level Tag delay。
   - 重点看它是否明显改善 static / roto / wand。
   - 如果 scan curve 很平，就不能把估计值当成 strong calibration。

---

## 3. Solver 版本定义

| Version | Paper name | Anchor delay | Tag delay | Layout source | Meaning |
|---|---|---:|---:|---|---|
| `v1-old` | V1 | No | No | early MDS baseline | 最早最弱 baseline |
| `v2` | V2 | No | No | IVW pair fusion | 加权 pair fusion |
| `v3-lite` | V3-lite | No | No | MAD/MVUE robust fusion | 抗 outlier / 抗方向不对称 |
| `v3-full` | V3-full | Yes | No | Tukey + per-anchor delay | 第一次引入 anchor delay，但当前不稳定 |
| `v4-io` | V4-io | Yes | No | Huber bounded-delay inter-anchor | 当前 production baseline |
| `v4-io-td` | V4-io-td | Yes | Common type-level | V4-io fixed + static delay scan | 下游 Tag-delay compensation test |
| `v4-io-roto` | V4-io-roto | Yes | No | V4-io + RotoArm soft constraints | 实验性 Z/动态约束 |
| `v4-io-wand` | V4-io-wand | Yes | No | V4-io + W01-W04 wand constraints | 实验性 calibration wand 约束 |
| `v5` | V5 | Uses V4 | No | diagnostics only | FIM / uncertainty / usable-area |

重要定义：

```text
V4-io = AutoPos production baseline
V4-io-td = V4-io layout fixed, only add common Tag delay in downstream positioning
V4-io-roto / V4-io-wand = experimental layout-constraint branches
V5 = diagnostics layer, not a new layout
```

---

## 4. v4-io-td 是什么？为什么要做？

之前主线模型是：

```text
r_i = ||p_tag - A_i|| + b_anchor_i + noise
```

其中：

- `A_i` 是第 i 个 anchor 坐标；
- `b_anchor_i` 是 per-anchor delay；
- `p_tag` 是 Tag 位置。

`v4-io-td` 加一个 common Tag delay：

```text
r_i = ||p_tag - A_i|| + b_anchor_i + c_tag_type + noise
```

但这里有一个现实限制：

> 在普通 deploy 中，Tag delay 很难现场估计，因为 Tag 位置未知，`p_tag` 和 `c_tag_type` 会互相吸收。

所以这次只做一个谨慎实验：

1. 固定 V4-io anchor layout；
2. 固定 V4-io anchor delay；
3. 用 static captures 的 median per-anchor ranges；
4. 扫描 `c_tag_type`；
5. 看 objective curve 有没有明确最低点；
6. 再用这个 delay 跑全部 static / roto / wand validation。

这不是 factory calibration。它只是回答：

> 在当前数据里，一个 common Tag delay 是否能明显改善下游 repeatability？

---

## 5. 1000-set 结果

`FULL-COMPARE-1000/tables/version_summary.csv`

| Version | Tag delay mm | AutoPos RMS | AutoPos p95 | Static median | Static p95 | Roto median | Roto p95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| v1-old | 0.0 | 64.23 | 143.92 | 50.75 | 79.52 | 103.18 | 154.75 |
| v2 | 0.0 | 40.43 | 80.36 | 48.65 | 71.04 | 103.09 | 155.95 |
| v3-lite | 0.0 | 40.82 | 82.01 | 48.69 | 70.91 | 103.07 | 155.94 |
| v3-full | 0.0 | 66.42 | 182.57 | 54.94 | 84.07 | 108.47 | 156.03 |
| v4-io | 0.0 | 44.34 | 87.75 | 49.25 | 81.65 | 103.82 | 157.55 |
| v4-io-td | 3.0 | 44.34 | 87.75 | 48.91 | 82.81 | 102.65 | 154.95 |
| v4-io-roto | 0.0 | 57.91 | 134.07 | 48.05 | 71.87 | 100.23 | 138.23 |
| v4-io-wand | 0.0 | 44.24 | 87.60 | 48.58 | 77.30 | 101.41 | 151.98 |
| v5 | 0.0 | 44.34 | 87.75 | 49.25 | 81.65 | 103.82 | 157.55 |

### 5.1 1000-set 主要结论

1. `v2` / `v3-lite` 的 inter-anchor residual 最低。
   - AutoPos RMS 约 40-41 mm。
   - 这说明 robust pair fusion 已经解释了大部分 inter-anchor 数据。

2. `v4-io` 的 inter-anchor RMS 略高。
   - RMS 44.34 mm。
   - 但它有 bounded per-anchor delay，物理模型更完整，工程上更适合作为 production baseline。

3. `v3-full` 明显不稳定。
   - AutoPos RMS 66.42 mm，p95 182.57 mm。
   - 它虽然是 delay-aware，但 delay / geometry coupling 处理不够稳。

4. `v4-io-td` 只带来很小改善。
   - static median: 49.25 -> 48.91 mm。
   - roto median: 103.82 -> 102.65 mm。
   - static p95 反而略差：81.65 -> 82.81 mm。
   - 说明 common Tag delay 对这批数据不是主导误差。

5. `v4-io-roto` 对 roto tail 改善明显，但牺牲 AutoPos self-consistency。
   - roto p95: 157.55 -> 138.23 mm。
   - AutoPos RMS: 44.34 -> 57.91 mm。
   - 适合作为 experimental branch，不适合作为当前主 baseline。

6. `v4-io-wand` 有轻微改善。
   - static p95: 81.65 -> 77.30 mm。
   - roto median: 103.82 -> 101.41 mm。
   - 但改善不够强，Wand 本身数据质量仍是限制。

---

## 6. v4-io-td Tag-delay scan

### 6.1 1000-set scan

`FULL-COMPARE-1000/v4-io-td/tag_delay_scan.csv`

| Rank | Tag delay mm | Case RMS median | Raw RMS | Raw p95 abs |
|---:|---:|---:|---:|---:|
| 1 | 3.0 | 40.05 | 61.46 | 123.18 |
| 2 | 2.0 | 40.29 | 61.39 | 123.67 |
| 3 | 4.0 | 40.40 | 61.55 | 122.68 |
| 4 | 1.0 | 40.54 | 61.33 | 124.15 |
| 5 | 0.0 | 40.80 | 61.28 | 124.64 |

解释：

- 最佳点在 `+3 mm`。
- 但 `+2 / +3 / +4 mm` 非常接近。
- 从 0 mm 到 3 mm，case RMS median 只改善约 0.75 mm。
- raw RMS 几乎没有明显改善。

所以：

> 当前数据对 common Tag delay 只有弱可观测性。估计值大约在 +3 mm 附近，但不能作为强 factory calibration 结论。

### 6.2 500-set scan

`FULL-COMPARE-500/v4-io-td/tag_delay_scan.csv`

| Rank | Tag delay mm | Case RMS median | Raw RMS | Raw p95 abs |
|---:|---:|---:|---:|---:|
| 1 | 4.0 | 40.73 | 61.97 | 116.74 |
| 2 | 5.0 | 40.88 | 62.07 | 118.01 |
| 3 | 3.0 | 40.89 | 61.86 | 115.62 |
| 4 | 2.0 | 41.07 | 61.76 | 114.49 |
| 5 | 1.0 | 41.26 | 61.68 | 114.19 |

### 6.3 500+500 split scan

`FULL-COMPARE-500+500/v4-io-td/tag_delay_scan_first500.csv`

| Rank | Tag delay mm | Case RMS median | Raw RMS | Raw p95 abs |
|---:|---:|---:|---:|---:|
| 1 | 4.0 | 40.73 | 61.97 | 116.74 |
| 2 | 5.0 | 40.88 | 62.07 | 118.01 |
| 3 | 3.0 | 40.89 | 61.86 | 115.62 |

`FULL-COMPARE-500+500/v4-io-td/tag_delay_scan_last500.csv`

| Rank | Tag delay mm | Case RMS median | Raw RMS | Raw p95 abs |
|---:|---:|---:|---:|---:|
| 1 | 3.0 | 39.52 | 61.15 | 114.69 |
| 2 | 4.0 | 39.60 | 61.22 | 114.21 |
| 3 | 2.0 | 39.77 | 61.08 | 115.94 |

500+500 consensus 使用：

```text
(first500 best 4.0 + last500 best 3.0) / 2 = 3.5 mm
```

这说明：

- common Tag delay 的估计值在三套 sweep 选择下比较一致：约 `+3 到 +4 mm`。
- 但 objective 很平，改善很弱。
- 因此它可以作为 exploratory result，而不是强校准结论。

---

## 7. 500-set 结果

`FULL-COMPARE-500/tables/version_summary.csv`

| Version | Tag delay mm | AutoPos RMS | AutoPos p95 | Static median | Static p95 | Roto median | Roto p95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| v1-old | 0.0 | 63.27 | 143.74 | 50.33 | 76.09 | 103.19 | 162.10 |
| v2 | 0.0 | 40.56 | 80.45 | 48.30 | 71.95 | 102.65 | 160.27 |
| v3-lite | 0.0 | 41.03 | 81.79 | 48.58 | 71.94 | 102.56 | 160.42 |
| v3-full | 0.0 | 61.84 | 178.31 | 55.77 | 90.77 | 108.70 | 158.14 |
| v4-io | 0.0 | 44.69 | 89.17 | 48.43 | 80.86 | 102.97 | 160.31 |
| v4-io-td | 4.0 | 44.69 | 89.17 | 47.94 | 82.26 | 101.78 | 157.09 |
| v4-io-roto | 0.0 | 56.89 | 131.80 | 48.20 | 74.48 | 99.77 | 142.09 |
| v4-io-wand | 0.0 | 44.35 | 87.38 | 48.18 | 79.12 | 101.36 | 154.66 |
| v5 | 0.0 | 44.69 | 89.17 | 48.43 | 80.86 | 102.97 | 160.31 |

### 7.1 500-set holdout

`FULL-COMPARE-500/tables/holdout_generalization.csv`

关键版本：

| Version | Train | Eval | RMS | p50 abs | p95 abs | Max abs |
|---|---|---|---:|---:|---:|---:|
| v4-io | first500 | last500 | 44.92 | 15.30 | 90.11 | 168.10 |
| v4-io-td | first500 | last500 | 44.92 | 15.30 | 90.11 | 168.10 |
| v4-io-roto | first500 | last500 | 58.51 | 24.01 | 133.55 | 146.45 |
| v4-io-wand | first500 | last500 | 44.79 | 20.89 | 83.44 | 165.38 |

解释：

- `v4-io-td` 不改变 anchor layout，所以 holdout inter-anchor 指标和 `v4-io` 完全一样。
- `v4-io` first500 -> last500 泛化稳定，RMS 44.92 mm。
- `v4-io-roto` 的 holdout 明显更差，说明它在 inter-anchor self-consistency 上不是主线。
- `v4-io-wand` 和 `v4-io` 很接近。

---

## 8. 500+500 split-consensus 结果

`FULL-COMPARE-500+500/tables/version_summary.csv`

| Version | Tag delay mm | AutoPos RMS | AutoPos p95 | Static median | Static p95 | Roto median | Roto p95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| v1-old | 0.0 | 64.23 | 143.91 | 50.87 | 77.82 | 103.58 | 162.62 |
| v2 | 0.0 | 40.43 | 80.43 | 48.47 | 72.21 | 103.06 | 160.81 |
| v3-lite | 0.0 | 40.83 | 81.95 | 48.60 | 72.06 | 103.01 | 160.81 |
| v3-full | 0.0 | 64.91 | 177.98 | 52.48 | 85.58 | 106.73 | 157.80 |
| v4-io | 0.0 | 44.24 | 88.55 | 48.39 | 80.59 | 103.45 | 160.50 |
| v4-io-td | 3.5 | 44.24 | 88.55 | 47.97 | 81.76 | 102.31 | 157.79 |
| v4-io-roto | 0.0 | 57.53 | 133.71 | 48.02 | 75.10 | 99.97 | 142.10 |
| v4-io-wand | 0.0 | 44.20 | 88.50 | 48.15 | 79.26 | 101.88 | 154.75 |
| v5 | 0.0 | 44.24 | 88.55 | 48.39 | 80.59 | 103.45 | 160.50 |

### 8.1 Split 的意义

`500+500` 是最适合讲 robustness 的结果，因为它检查：

```text
first500 layout 和 last500 layout 是否一致
```

对 `v4-io-td` 来说：

- first500 best tag delay = +4 mm；
- last500 best tag delay = +3 mm；
- consensus = +3.5 mm。

这说明 `v4-io-td` 的估计值在 split 下稳定，但改善仍然很弱。

---

## 9. Static repeatability 分析

Static 使用 `Static_Test/ID01-ID24`，实际有效为 23 个点，`ID10` missing。

核心结果：

1. `v2` / `v3-lite` static median 很强。
   - 1000-set 下约 48.65 / 48.69 mm。

2. `v4-io` static median 稍差一点。
   - 1000-set 下 49.25 mm。
   - 但它的模型更物理，delay bounded，更适合作为 production baseline。

3. `v4-io-td` 对 static median 只改善约 0.3-0.5 mm。
   - 1000: 49.25 -> 48.91 mm。
   - 500: 48.43 -> 47.94 mm。
   - 500+500: 48.39 -> 47.97 mm。

4. `v4-io-td` 对 static p95 没有改善，甚至略差。
   - 1000: 81.65 -> 82.81 mm。
   - 500: 80.86 -> 82.26 mm。
   - 500+500: 80.59 -> 81.76 mm。

结论：

> common Tag delay 不是 static tail 的主要误差源。static tail 更可能来自空间区域、NLOS、anchor/tag orientation、Z observability 或个别方向问题。

---

## 10. Roto dynamic 分析

Roto 使用 `Roto_Test/ID25-ID41`，peer 为：

```text
BS2DCE = inner
BSDC91 = outer
```

核心结果：

1. `v4-io-td` 对 roto 有小幅改善。
   - 1000 roto median: 103.82 -> 102.65 mm。
   - 500 roto median: 102.97 -> 101.78 mm。
   - 500+500 roto median: 103.45 -> 102.31 mm。

2. 但 `v4-io-roto` 对 roto 改善更明显。
   - 1000 roto p95: 157.55 -> 138.23 mm。
   - 500 roto p95: 160.31 -> 142.09 mm。
   - 500+500 roto p95: 160.50 -> 142.10 mm。

3. `v4-io-roto` 的代价是 AutoPos inter-anchor residual 变差。
   - 1000 AutoPos RMS: 44.34 -> 57.91 mm。
   - 500 AutoPos RMS: 44.69 -> 56.89 mm。
   - 500+500 AutoPos RMS: 44.24 -> 57.53 mm。

结论：

> RotoArm 数据确实包含对动态/Z 的有用信息，但当前 `v4-io-roto` 是 experimental soft-constraint branch，不能替代 `v4-io` 作为主 AutoPos baseline。

---

## 11. Wand calibration 分析

Wand 使用 W01-W04 static rigid-body captures。

Wand 约束的定位：

- `v4-io-wand` 是对之前 calibration wand concept 的诚实验证；
- 它不是主线；
- 它有轻微改善，但没有强到可以支撑“Wand constraint 显著提升 AutoPos”。

当前结果：

| Mode | v4-io static p95 | v4-io-wand static p95 | v4-io roto p95 | v4-io-wand roto p95 |
|---|---:|---:|---:|---:|
| 1000 | 81.65 | 77.30 | 157.55 | 151.98 |
| 500 | 80.86 | 79.12 | 160.31 | 154.66 |
| 500+500 | 80.59 | 79.26 | 160.50 | 154.75 |

解释：

- Wand 对 p95/tail 有轻微帮助；
- 但改善幅度不大；
- W01-W04 自身刚体距离噪声仍然偏大；
- W05 由于 TDMA 下三颗 Tag 不同步，不应用作 synchronized rigid-body constraint。

---

## 12. V2/V3-lite 为什么 inter-anchor 更好，但不是最终主线？

这是报告里要讲清楚的一点。

`v2` / `v3-lite` 在 inter-anchor residual 上确实很好：

```text
1000-set:
V2 RMS      40.43 mm
V3-lite RMS 40.82 mm
V4-io RMS   44.34 mm
```

但它们没有显式 anchor delay model。结果可能是：

```text
未建模的 antenna / RF delay 被吸收到 anchor geometry 里
```

这不一定让 repeatability 变差。因为如果系统 bias 稳定，Tag positioning 仍然可能稳定。但它会让 layout 的物理解释变弱。

所以建议报告中这样表述：

> V2/V3-lite are strong empirical baselines. They achieve low inter-anchor residual and good repeatability, but they do not explicitly model antenna delay. V4-io is preferred as the production AutoPos baseline because it provides a bounded, physically interpretable delay-aware layout, even when its raw residual is not the absolute lowest.

中文：

> V2/V3-lite 是很强的经验 baseline，但它们没有显式建模 antenna delay，可能把 delay bias 吸收到几何坐标里。V4-io 虽然 raw residual 不是最低，但它有 bounded delay model，更物理、更适合现场 pipeline。

---

## 13. Tag delay 的最终判断

现在可以回答：

> 我们能不能在当前数据里估一个 common Tag delay？

答案是：

```text
可以探索性估计，约 +3 到 +4 mm。
但改善很小，objective 很平，不能当作 strong calibration。
```

证据：

| Mode | Best Tag delay | Static median change | Static p95 change | Roto median change | Roto p95 change |
|---|---:|---:|---:|---:|---:|
| 1000 | +3.0 mm | 49.25 -> 48.91 | 81.65 -> 82.81 | 103.82 -> 102.65 | 157.55 -> 154.95 |
| 500 | +4.0 mm | 48.43 -> 47.94 | 80.86 -> 82.26 | 102.97 -> 101.78 | 160.31 -> 157.09 |
| 500+500 | +3.5 mm | 48.39 -> 47.97 | 80.59 -> 81.76 | 103.45 -> 102.31 | 160.50 -> 157.79 |

解释：

- median 有小改善；
- p95 没有稳定改善；
- roto 有小改善；
- delay 估计值在不同 split 下比较一致；
- 但整体贡献太小，不是当前主要误差源。

所以建议写成：

> A common type-level Tag-delay term was tested by fixing the V4-io AutoPos layout and scanning a shared delay using static captures. The optimum was consistently around +3 to +4 mm across 1000, 500, and split-consensus runs. However, the improvement in downstream validation was marginal and the scan objective was shallow, so Tag delay is not a dominant error source in this dataset. Per-tag or factory calibration may still be useful, but online field estimation is not justified by this evidence.

---

## 14. 哪个版本应作为主结论？

推荐主线：

```text
Production baseline: V4-io
Diagnostics: V5
Exploratory branch 1: V4-io-td
Exploratory branch 2: V4-io-roto
Exploratory branch 3: V4-io-wand
Strong empirical baselines: V2 / V3-lite
Unstable historical delay-aware solver: V3-full
```

具体讲法：

1. `V4-io` 是当前 production baseline。
   - bounded per-anchor delay；
   - split / holdout 稳；
   - 下游 validation 稳；
   - 物理解释合理。

2. `V2/V3-lite` 是强 baseline。
   - residual 更低；
   - repeatability 也很好；
   - 但没有 delay model，不作为最终 field pipeline 解释框架。

3. `V4-io-td` 是小修正。
   - delay 约 +3 到 +4 mm；
   - 不是主要提升来源。

4. `V4-io-roto` 证明 RotoArm/Z 信息有价值。
   - dynamic roto tail 明显改善；
   - 但 inter-anchor self-consistency 变差；
   - 应继续作为 future joint optimization，而不是当前主线。

5. `V4-io-wand` 是诚实的 calibration wand 尝试。
   - 有轻微改善；
   - 但数据质量不足以作为强论点。

---

## 15. 给教授看的报告结构建议

建议不要一开始讲 Tag RMS。应先讲 AutoPos。

### 15.1 Section 1: AutoPos without OptiTrack

重点：

```text
没有 OptiTrack 时，不能直接声称 absolute accuracy。
我们评价的是 AutoPos layout self-consistency 和 downstream repeatability。
```

指标：

- inter-anchor RMS / p95；
- holdout generalization；
- split layout stability；
- delay sanity；
- downstream repeatability。

### 15.2 Section 2: Algorithm progression

讲版本递进：

```text
V1 -> V2 -> V3-lite -> V3-full -> V4-io -> V5
```

再说明 experimental branches：

```text
V4-io-td
V4-io-roto
V4-io-wand
```

### 15.3 Section 3: Main quantitative table

用 `FULL-COMPARE-1000` 作为主表，然后用 500 / 500+500 证明 consistency。

### 15.4 Section 4: Tag delay experiment

建议标题：

```text
Common type-level Tag delay is weakly observable and not the dominant error source
```

### 15.5 Section 5: RotoArm and Wand

RotoArm：

```text
useful as dynamic/Z validation and future constraint source
```

Wand：

```text
tested honestly, weak improvement, current data too noisy for strong calibration
```

---

## 16. 最终一句话总结

中文：

> 本次 2026-05-13 outdoor clean rebuild 显示，AutoPos 在无 OptiTrack 条件下可以通过 inter-anchor self-consistency、holdout、split stability、delay sanity 和 downstream repeatability 进行评价。V2/V3-lite 是很强的 no-delay empirical baseline，但 V4-io 由于具有 bounded per-anchor delay 和更合理的物理解释，仍然是当前最适合的 production baseline。新增的 V4-io-td 估计出约 +3 到 +4 mm 的 common Tag delay，但改善很小，说明 Tag delay 不是这批数据的主导误差源；RotoArm 和 Wand 约束提供有价值的探索性信息，但目前仍应作为 experimental branches。

English:

> The 2026-05-13 outdoor clean rebuild shows that AutoPos can be evaluated without OptiTrack using inter-anchor self-consistency, holdout generalization, split-layout stability, delay sanity, and downstream repeatability. V2/V3-lite are strong no-delay empirical baselines, but V4-io remains the preferred production baseline because it provides a bounded, physically interpretable per-anchor delay model. The new V4-io-td branch estimates a consistent common Tag delay of about +3 to +4 mm, but the downstream improvement is marginal, indicating that Tag delay is not the dominant error source in this dataset. RotoArm and Wand constraints remain useful exploratory signals rather than production replacements for V4-io.
