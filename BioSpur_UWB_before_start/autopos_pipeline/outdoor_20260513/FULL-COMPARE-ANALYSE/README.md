# AutoPos 2026-05-13 Full Compare 分析报告

本文是对 `outdoor_20260513` 三套正式 Full Compare 结果的集中解释。中文优先，方便后续整理成给教授看的报告；关键术语保留英文名，避免和代码/表格脱节。

分析目录：

- `autopos_pipeline/outdoor_20260513/FULL-COMPARE-1000`
- `autopos_pipeline/outdoor_20260513/FULL-COMPARE-500`
- `autopos_pipeline/outdoor_20260513/FULL-COMPARE-500+500`

核心脚本：

- `autopos_pipeline/outdoor_20260513/run_clean_full_compare.py`

核心输出：

- `tables/version_summary.csv`
- `tables/autopos_quality_summary.csv`
- `tables/holdout_generalization.csv`
- `tables/split_layout_stability.csv`
- `tables/static_all_captures.csv`
- `tables/roto_all_captures.csv`
- `tables/wand_static_summary.csv`
- `figures/progression_autopos_static_roto.png`
- `reports/full_compare_report.md`

---

## 1. 这次实验到底在回答什么问题？

这次不是单纯比较“Tag 定位 RMS 谁更低”。这次真正要回答的是：

1. **AutoPos anchor layout 自身是否可靠？**
   - 不依赖 OptiTrack，也不先看 Tag RMS。
   - 主要看 inter-anchor residual、holdout、split layout stability、delay sanity、FIM / usable-area diagnostics。

2. **AutoPos layout 对下游定位是否有稳定贡献？**
   - 用 static tag repeatability 验证。
   - 用 roto dynamic circle-fit residual 验证。
   - 用 wand rigid body distance 验证。

3. **算法版本是否有清楚的能力递进？**
   - V1: 最早 baseline。
   - V2/V3-lite: 更好的 pair fusion / robust fusion。
   - V3-full: 引入 antenna delay estimation，但目前不稳定。
   - V4-io: 当前 production inter-anchor solver。
   - V4-io-roto / V4-io-wand: 两个 experimental constraint-injection 分支。
   - V5: 不改变 layout，负责 uncertainty / diagnostics。

4. **1000 set、500 set、500+500 split consensus 是否给出一致结论？**
   - 如果三者趋势一致，说明结论不是某个 sweep 子集偶然造成的。

---

## 2. 数据集与评价范围

这次结果使用了所有已经采集到的数据，而不是旧 `FULL-COMPARE/` 里为了节省时间而抽样的版本。

### 2.1 Sweep 数据

`sweep1000/pairs_all.csv`

三种 layout generation 方式：

| 目录 | Layout 生成方式 | 目的 |
|---|---|---|
| `FULL-COMPARE-1000` | 使用全部 1000 set sweep 解 layout | full-data baseline |
| `FULL-COMPARE-500` | 使用 first 500 set sweep 解 layout | 测试少一半 sweep 是否仍稳定 |
| `FULL-COMPARE-500+500` | first 500 和 last 500 分别解 layout，再 align + consensus | 测试 split stability / generalization |

### 2.2 Static 数据

Static 使用 `Static_Test/ID01-ID24`。实际有效采集为 23 个点，`ID10` 没有采到，因此在表中应记录为 `missing`，不能硬算。

分组逻辑：

| Group | IDs | 含义 |
|---|---|---|
| edge low | ID01, ID04, ID07, ID10 | 四个侧面低高度，ID10 missing |
| edge mid | ID02, ID05, ID08, ID11 | 四个侧面中高度 |
| edge high | ID03, ID06, ID09, ID12 | 四个侧面高高度 |
| center mid | ID13-ID16 | 中心中高度，四个朝向 |
| center low | ID17-ID20 | 中心低高度，四个朝向 |
| center high | ID21-ID24 | 中心高高度，四个朝向 |

Static 指标是 **repeatability**，不是 absolute accuracy，因为没有 OptiTrack / survey ground truth。

### 2.3 Roto 数据

Roto 使用 `Roto_Test/ID25-ID41`，每个 capture 中有两个 Roto peer：

- `BS2DCE`
- `BSDC91`

一共 34 个 peer-capture 组合，即 17 个 ID x 2 个 tag。

Roto 指标：

- fitted circle radius
- radial std
- plane/off-axis std
- 3D circle RMS
- plane tilt angle

Roto 的意义不是“静止点重复性”，而是动态约束下的空间一致性。

### 2.4 Wand 数据

Wand 静态刚体约束使用 W01-W04。

已知三边：

| Pair | Ground truth |
|---|---:|
| BSCCF4-BS9336 | 670.0 mm |
| BSCCF4-BS955A | 659.7 mm |
| BS9336-BS955A | 708.7 mm |

W05 是 dynamic free move。由于 TDMA 下三颗 Wand tag 并非严格同一时刻测距，因此 W05 不作为同步 rigid-body constraint，只用于 coverage / residual / usable-area 类型诊断。

---

## 3. 版本定义

| Version | 真实含义 | Delay-aware | 是否使用额外几何约束 |
|---|---|---:|---|
| `v1-old` | earliest MDS baseline, simple bidirectional mean | No | No |
| `v2` | IVW pair fusion + no-delay solve | No | No |
| `v3-lite` | MAD/MVUE robust pair fusion + no-delay solve | No | No |
| `v3-full` | robust fusion + Tukey / median-style antenna delay estimation | Yes | No |
| `v4-io` | current production inter-anchor Huber bounded-delay solver | Yes | No |
| `v4-io-roto` | V4-io + RotoArm soft constraints | Yes | RotoArm |
| `v4-io-wand` | V4-io + W01-W04 calibration-wand soft constraints | Yes | Static Wand |
| `v5` | V4-io diagnostics layer | Uses V4 | No new layout |

重要说明：

- `v5` 不应该被理解为“更高精度 solver”。它复用 `v4-io` layout，增加 FIM / uncertainty / usable-area 诊断。
- 当前 `v4-io-roto` 是 lightweight pseudo-constraint branch：用 V4-io 得到少量 roto pseudo-positions，再作为软约束注入 anchor layout。它不是完整 joint RotoArm optimizer。这一点在报告中要诚实说明。
- `v4-io-wand` 是为了验证 calibration wand 假设。即使最后没有明显提升，也应该保留，因为它对应之前和教授沟通过的实验方向。

---

## 4. 总览结果

### 4.1 FULL-COMPARE-1000

| Version | AutoPos RMS | AutoPos p95 | Static median | Static p95 | Roto median | Roto p95 |
|---|---:|---:|---:|---:|---:|---:|
| v1-old | 64.23 | 143.92 | 50.75 | 79.52 | 103.18 | 154.75 |
| v2 | 40.43 | 80.36 | 48.65 | 71.04 | 103.09 | 155.95 |
| v3-lite | 40.82 | 82.01 | 48.69 | 70.91 | 103.07 | 155.94 |
| v3-full | 66.42 | 182.57 | 54.94 | 84.07 | 108.47 | 156.03 |
| v4-io | 44.34 | 87.75 | 49.25 | 81.65 | 103.82 | 157.55 |
| v4-io-roto | 57.91 | 134.07 | 48.05 | 71.87 | 100.23 | 138.23 |
| v4-io-wand | 44.24 | 87.60 | 48.58 | 77.30 | 101.41 | 151.98 |
| v5 | 44.34 | 87.75 | 49.25 | 81.65 | 103.82 | 157.55 |

### 4.2 FULL-COMPARE-500

| Version | AutoPos RMS | AutoPos p95 | Static median | Static p95 | Roto median | Roto p95 |
|---|---:|---:|---:|---:|---:|---:|
| v1-old | 63.27 | 143.74 | 50.33 | 76.09 | 103.19 | 162.10 |
| v2 | 40.56 | 80.45 | 48.30 | 71.95 | 102.65 | 160.27 |
| v3-lite | 41.03 | 81.79 | 48.58 | 71.94 | 102.56 | 160.42 |
| v3-full | 61.84 | 178.31 | 55.77 | 90.77 | 108.70 | 158.14 |
| v4-io | 44.69 | 89.17 | 48.43 | 80.86 | 102.97 | 160.31 |
| v4-io-roto | 56.89 | 131.80 | 48.20 | 74.48 | 99.77 | 142.09 |
| v4-io-wand | 44.35 | 87.38 | 48.18 | 79.12 | 101.36 | 154.66 |
| v5 | 44.69 | 89.17 | 48.43 | 80.86 | 102.97 | 160.31 |

### 4.3 FULL-COMPARE-500+500

| Version | AutoPos RMS | AutoPos p95 | Static median | Static p95 | Roto median | Roto p95 |
|---|---:|---:|---:|---:|---:|---:|
| v1-old | 64.23 | 143.91 | 50.87 | 77.82 | 103.58 | 162.62 |
| v2 | 40.43 | 80.43 | 48.47 | 72.21 | 103.06 | 160.81 |
| v3-lite | 40.83 | 81.95 | 48.60 | 72.06 | 103.01 | 160.81 |
| v3-full | 64.91 | 177.98 | 52.48 | 85.58 | 106.73 | 157.80 |
| v4-io | 44.24 | 88.55 | 48.39 | 80.59 | 103.45 | 160.50 |
| v4-io-roto | 57.53 | 133.71 | 48.02 | 75.10 | 99.97 | 142.10 |
| v4-io-wand | 44.20 | 88.50 | 48.15 | 79.26 | 101.88 | 154.75 |
| v5 | 44.24 | 88.55 | 48.39 | 80.59 | 103.45 | 160.50 |

### 4.4 第一眼结论

1. **500 set 已经非常接近 1000 set。**
   - V2/V3-lite/V4-io 在 500、1000、500+500 三套结果中差异都很小。
   - 说明这次 inter-anchor sweep 的统计量比较稳定，不是必须 1000 set 才能得到结论。

2. **V2/V3-lite 在 AutoPos inter-anchor self-consistency 上最好。**
   - 1000-set 下 V2: 40.43 mm，V3-lite: 40.82 mm。
   - 这说明 robust pair fusion / no-delay geometry 已经能把 sweep matrix 拟合得很好。

3. **V4-io 的 AutoPos RMS 比 V2/V3-lite 略高，但 delay sanity 更合理。**
   - 1000-set V4-io RMS 为 44.34 mm。
   - Delay 没有撞边界，delay L2 约 43.9 mm。
   - 这是一个工程上更可信、更稳的 production solver，而不是单纯追求最小 residual。

4. **V3-full 当前不适合作为主线最终版本。**
   - AutoPos RMS 和 p95 明显变差。
   - Delay 出现极大值：1000-set 下 min = -220.12 mm，max = 96.23 mm，delay L2 = 244.18 mm。
   - 这说明 V3-full 的 delay estimation 有吸收几何误差 / 不稳定的风险。

5. **V4-io-roto 对 roto validation 有帮助，但牺牲 inter-anchor self-consistency。**
   - 1000-set 下 roto median 从 V4-io 的 103.82 mm 改到 100.23 mm。
   - roto p95 从 157.55 mm 改到 138.23 mm，tail 明显改善。
   - 但 AutoPos RMS 从 44.34 mm 变差到 57.91 mm。
   - 说明 RotoArm soft constraints 确实给动态/空间覆盖带来信息，但当前注入方式还不能作为主 layout solver。

6. **V4-io-wand 是弱改善/弱影响。**
   - 1000-set static median: V4-io 49.25 mm, V4-io-wand 48.58 mm。
   - roto median: V4-io 103.82 mm, V4-io-wand 101.41 mm。
   - 但 wand 自身三边 median abs bias 仍在约 56-63 mm 量级。
   - 结论应是：Wand 数据有一定信息，但当前 W01-W04 的刚体约束质量不足以显著改变 AutoPos。

---

## 5. AutoPos layout 自身质量分析

这一节不看 Tag RMS，只看 AutoPos anchor layout 是否自洽。

### 5.1 Inter-anchor residual

1000-set 下：

| Version | RMS | p50 abs | p75 abs | p95 abs | max abs |
|---|---:|---:|---:|---:|---:|
| v1-old | 64.23 | 28.31 | 51.10 | 143.92 | 183.12 |
| v2 | 40.43 | 27.15 | 42.99 | 80.36 | 90.38 |
| v3-lite | 40.82 | 27.36 | 43.22 | 82.01 | 91.65 |
| v3-full | 66.42 | 2.00 | 7.16 | 182.57 | 207.90 |
| v4-io | 44.34 | 15.26 | 25.30 | 87.75 | 163.32 |
| v4-io-roto | 57.91 | 22.82 | 57.05 | 134.07 | 146.93 |
| v4-io-wand | 44.24 | 17.55 | 28.21 | 87.60 | 163.80 |

读法：

- V1-old 的 RMS/p95 很差，说明最早 baseline 对 raw sweep matrix 很敏感。
- V2/V3-lite 的 RMS 和 p95 最好，说明这次 sweep 数据质量高时，no-delay robust fusion 就已经能拟合得很好。
- V3-full 的 p50 很小但 p95/max 极大，这是一个危险信号：它可能让大部分 pair 看起来很好，但把少数 pair / delay 搞得很糟。
- V4-io 的 p50/p75 非常好，但 max 较高，说明 Huber/bounded-delay 把大部分 residual 控住了，但仍有少数 pair tail。

### 5.2 500-set holdout

`FULL-COMPARE-500` 使用 first 500 solve，last 500 holdout。

| Version | Train RMS | Holdout RMS | Train p95 | Holdout p95 |
|---|---:|---:|---:|---:|
| v1-old | 63.27 | 61.98 | 143.74 | 144.03 |
| v2 | 40.56 | 40.57 | 80.45 | 78.65 |
| v3-lite | 41.03 | 41.20 | 81.79 | 79.64 |
| v3-full | 61.84 | 62.17 | 178.31 | 178.48 |
| v4-io | 44.69 | 44.92 | 89.17 | 90.11 |
| v4-io-roto | 56.89 | 58.51 | 131.80 | 133.55 |
| v4-io-wand | 44.35 | 44.79 | 87.38 | 83.44 |

结论：

- train 和 holdout 非常接近。
- 说明这次 sweep 的 first half / second half 是一致的，没有明显过拟合。
- V2/V3-lite/V4-io 的 holdout 可靠性都不错。
- V4-io-wand 的 holdout p95 比 train p95 还低一点，说明 wand 分支没有明显 overfit first 500。
- V4-io-roto 的 holdout 仍然差于 V4-io，说明当前 roto pseudo-constraint 分支不应作为主 AutoPos layout。

### 5.3 Split layout stability

`FULL-COMPARE-500+500` 中，first 500 和 last 500 分别解 layout，再 Procrustes align 后看 per-anchor 差异。

关键观察：

- V2 / V3-lite 的 split 差异大约是几毫米到 9 mm 量级。
- V4-io 的 split 差异大约是 5-13 mm 量级。
- V4-io-roto 的 split 差异也在 5-11 mm 量级。
- V3-full 的 split 差异非常大，最高接近 193 mm，delay 差异也很大。

这说明：

1. V2/V3-lite/V4-io 的 layout 从两个独立 half 中得到的结果基本一致。
2. V3-full 的 delay-aware 方案在 split 上明显不稳定。
3. V4-io 虽然 AutoPos RMS 不如 V2/V3-lite 低，但 split stability 仍在合理范围内。

### 5.4 Delay sanity

| Version | 1000 delay min/max | 1000 delay L2 | near bound |
|---|---:|---:|---:|
| v3-full | -220.12 / 96.23 | 244.18 | 2 |
| v4-io | -2.39 / 32.30 | 43.90 | 0 |
| v4-io-roto | -28.91 / 13.17 | 35.23 | 0 |
| v4-io-wand | -9.79 / 60.00 | 63.99 | 1 |

结论：

- V3-full 的 delay 过大，不适合作为稳定主线。
- V4-io 的 delay 最健康，没有撞边界。
- V4-io-wand 有一个 delay 到 60 mm 边界，说明 wand constraint 在推某个 delay 到边界附近，需要谨慎解释。
- V4-io-roto 的 delay 范围较健康，但 AutoPos residual 变差，说明它是用几何变形换取动态 roto residual 改善。

---

## 6. Static Tag repeatability 分析

Static 指标是固定 Tag 的重复性，不是 absolute accuracy。没有 OptiTrack 时，它能反映 layout + ranging + solver 的稳定性，但不能单独证明绝对坐标正确。

### 6.1 总体水平

三套数据策略中，static median 非常接近：

| Version | 1000 | 500 | 500+500 |
|---|---:|---:|---:|
| v1-old | 50.75 | 50.33 | 50.87 |
| v2 | 48.65 | 48.30 | 48.47 |
| v3-lite | 48.69 | 48.58 | 48.60 |
| v3-full | 54.94 | 55.77 | 52.48 |
| v4-io | 49.25 | 48.43 | 48.39 |
| v4-io-roto | 48.05 | 48.20 | 48.02 |
| v4-io-wand | 48.58 | 48.18 | 48.15 |

结论：

- 主流版本的 static median 都在 48-50 mm 附近。
- 这说明 static repeatability 的瓶颈可能不完全来自 AutoPos layout；也可能来自 UWB ranging noise、NLOS、Tag antenna orientation、Z geometry。
- V3-full 明显偏差，说明 delay instability 会影响 static。
- V4-io-roto 和 V4-io-wand 对 static median 有小幅改善，但不是决定性改善。

### 6.2 空间分组

1000-set static group：

| Group | median 3D std |
|---|---:|
| center | 48.35 mm |
| edge | 50.43 mm |
| high | 43.08 mm |
| mid | 49.25 mm |
| low | 58.21 mm |
| facing ABEF | 47.89 mm |
| facing ADHE | 48.79 mm |
| facing BCGF | 50.78 mm |
| facing CDHG | 63.03 mm |

最明显的结论：

1. **low height 明显更差。**
   - low median 约 58 mm。
   - high median 约 43 mm。
   - 这说明低高度位置的几何 / NLOS / 地面反射可能更差。

2. **CDHG 方向明显更差。**
   - CDHG facing median 约 63 mm。
   - ABEF / ADHE / BCGF 在 48-51 mm。
   - 这说明 CDHG 方向存在空间/朝向相关的问题。

3. **edge 比 center 略差，但不是最大问题。**
   - center 48.35 mm，edge 50.43 mm。
   - 差异存在，但不如高度和 CDHG 方向明显。

### 6.3 最差 static capture

三个数据策略中最差 static 几乎都指向同一个位置：

- `ID08`: edge mid CDHG
- `ID07`: edge low CDHG
- `ID09`: edge high CDHG

这很重要。它说明 static 的坏例子不是随机散布，而是和 **CDHG face / direction** 强相关。

1000-set 下：

| Version | Worst ID | 3D std | Z std |
|---|---|---:|---:|
| v3-full | ID08 | 146.73 | 125.88 |
| v1-old | ID08 | 90.24 | 71.50 |
| v4-io | ID08 | 88.19 | 71.07 |
| v4-io-wand | ID08 | 83.52 | 66.34 |
| v4-io-roto | ID08 | 81.14 | 66.58 |

读法：

- ID08 是稳定的 worst case。
- worst case 主要由 Z std 拉高。
- V4-io-roto / V4-io-wand 对 ID08 有一点帮助，但没有彻底解决。
- 如果要给教授讲“哪里最弱”，CDHG/ID08 是非常明确的 evidence。

---

## 7. Roto dynamic 分析

Roto 是动态验证，和 static repeatability 是互补的。Static 可以很稳定但仍然有 bias；Roto 的 circle-fit residual 更容易暴露空间连续性和 Z 方向弱点。

### 7.1 总体趋势

1000-set：

| Version | Roto median | Roto p95 | Roto max |
|---|---:|---:|---:|
| v1-old | 103.18 | 154.75 | 160.68 |
| v2 | 103.09 | 155.95 | 173.89 |
| v3-lite | 103.07 | 155.94 | 173.78 |
| v3-full | 108.47 | 156.03 | 177.97 |
| v4-io | 103.82 | 157.55 | 171.71 |
| v4-io-roto | 100.23 | 138.23 | 148.88 |
| v4-io-wand | 101.41 | 151.98 | 162.06 |

结论：

- V4-io-roto 对 roto tail 有最明显改善。
- V4-io-wand 也有轻微改善，但不如 roto 分支。
- V3-full 仍然不是好选择。
- V2/V3-lite/V4-io 的 roto median 其实很接近，说明 Roto 的瓶颈可能更多是动态 ranging / spatial geometry，而不只是 inter-anchor layout residual。

### 7.2 Tilt ablation

1000-set roto group：

| Tilt | Peer | median RMS |
|---|---|---:|
| planar | BS2DCE | 153.65 |
| planar | BSDC91 | 138.67 |
| small | BS2DCE | 137.39 |
| small | BSDC91 | 141.78 |
| mid | BS2DCE | 103.09 |
| mid | BSDC91 | 102.54 |
| high | BS2DCE | 100.01 |
| high | BSDC91 | 86.80 |
| vertical | BS2DCE | 87.13 |
| vertical | BSDC91 | 89.59 |

非常清楚的结论：

1. **planar / small tilt 最差。**
   - 因为对 Z 方向约束弱。

2. **high / vertical tilt 明显更好。**
   - 这支持之前 concept 中关于 RotoArm 引入 Z 信息的假设。
   - 尤其 vertical 的 BS2DCE/BS DC91 都在 87-90 mm 左右。

3. **RotoArm 的信息价值是存在的。**
   - 但当前 `v4-io-roto` 只是轻量 soft-constraint branch，还不能直接作为最终 solver。
   - 更合理的论文叙述是：RotoArm 数据证明 high-tilt / vertical motion 对 Z 可观测性有帮助，未来可发展为更完整 joint optimization。

### 7.3 最差 roto capture

最差 Roto 几乎总是：

- `ID29 BS2DCE mid ABEF`
- `ID26 BS2DCE small BCGF`

1000-set worst：

| Version | ID | Peer | Tilt | RMS |
|---|---|---|---|---:|
| v3-full | ID29 | BS2DCE | mid | 177.97 |
| v2 | ID29 | BS2DCE | mid | 173.89 |
| v3-lite | ID29 | BS2DCE | mid | 173.78 |
| v4-io | ID29 | BS2DCE | mid | 171.71 |
| v4-io-wand | ID29 | BS2DCE | mid | 162.06 |
| v4-io-roto | worst reduced to 148.88 max | - | - | - |

`v4-io-roto` 的意义主要体现在这里：它没有让 AutoPos inter-anchor RMS 变好，但让 Roto worst/tail 明显下降。

---

## 8. Wand calibration 约束分析

Wand 的问题要实事求是：我们确实做了 W01-W04 static calibration wand，但它本身的刚体距离误差并不小。

### 8.1 Wand 三边 bias

1000-set 下 median abs bias：

| Version | median abs bias | mean abs bias | max abs bias |
|---|---:|---:|---:|
| v1-old | 52.49 | 46.95 | 98.70 |
| v2 | 50.18 | 51.60 | 92.85 |
| v3-lite | 50.67 | 51.59 | 93.06 |
| v3-full | 55.74 | 63.71 | 125.03 |
| v4-io | 59.39 | 51.46 | 89.23 |
| v4-io-roto | 53.50 | 56.39 | 107.65 |
| v4-io-wand | 62.68 | 48.80 | 93.21 |

注意：

- median abs bias 大约 50-63 mm。
- 这对一个 calibration wand 来说并不算很强的刚体约束。
- 因此 `v4-io-wand` 没有显著提升是合理的。

### 8.2 Wand 约束是否“乱用”？

目前结论：

- Wand 不是完全没用：`v4-io-wand` 在 static/roto tail 上有轻微改善。
- 但 Wand 也不是强约束：它自身 bias 比较大，不能期待显著提升 AutoPos。
- W01-W04 可以作为论文中的探索性分支：我们尝试 calibration wand rigid-body constraints，但当前数据质量不足以支撑它成为主 solver。
- W05 不应该用于同步刚体约束，因为 TDMA 下三颗 tag 不是同一时刻观测；它更适合 coverage / residual map / usable-area diagnosis。

---

## 9. 1000 vs 500 vs 500+500

这部分非常重要，因为它说明结果是不是稳定。

### 9.1 500 set 是否足够？

从结果看，500 set 已经足够接近 1000 set。

例如：

| Version | 1000 AutoPos RMS | 500 AutoPos RMS | 500+500 AutoPos RMS |
|---|---:|---:|---:|
| v2 | 40.43 | 40.56 | 40.43 |
| v3-lite | 40.82 | 41.03 | 40.83 |
| v4-io | 44.34 | 44.69 | 44.24 |
| v4-io-wand | 44.24 | 44.35 | 44.20 |

结论：

- 这次 sweep 质量比较稳定。
- 1000 set 的优势不是“显著提高精度”，而是让报告更有说服力。
- 500 set 可以作为 future fast calibration 的候选。

### 9.2 500+500 split 是否有意义？

有意义。它给了一个无 OptiTrack 情况下很重要的判断标准：

- first half 和 second half 是否能解出一致 layout？
- consensus layout 是否接近 full 1000？
- 某个 solver 是否对数据 split 很敏感？

结果表明：

- V2/V3-lite/V4-io split 很稳定。
- V3-full split 很不稳定。
- V4-io-roto / V4-io-wand 的 split 差异在可接受范围，但 roto branch 牺牲了 inter-anchor self-consistency。

---

## 10. 该如何给教授讲这个故事？

建议不要把报告讲成“某个版本 Tag RMS 最低”。那样会很弱，因为没有 OptiTrack。

更好的故事是：

### 10.1 主线 1：AutoPos 在无外部 ground truth 下的自洽验证

我们提出一组 no-OptiTrack 评价标准：

1. inter-anchor self-consistency
2. first/last holdout generalization
3. split layout stability
4. delay sanity
5. downstream static repeatability
6. downstream roto dynamic consistency
7. wand rigid body check

这比只报 Tag RMS 更完整。

### 10.2 主线 2：软件/算法从 fragile baseline 到 robust production

可以这样讲：

- V1-old：早期 baseline，对数据异常更敏感。
- V2/V3-lite：pair fusion 和 robust fusion 显著改善 inter-anchor matrix。
- V3-full：第一次尝试 delay estimation，但当前实现不稳定，delay 会吸收几何误差。
- V4-io：工程上更稳定的 bounded-delay solver，是当前 production baseline。
- V4-io-roto：RotoArm 信息对 dynamic/Z 方向有价值，但当前只是 experimental branch。
- V4-io-wand：calibration wand 尝试保留，但当前数据质量不足以成为主路线。
- V5：不是新 layout solver，而是 reliability / usable-area diagnostics。

### 10.3 主线 3：D/H 或坏数据 robustness

如果和 20260504 比：

- 20260504 D/H 数据很差，但 robust solver 仍把结果拉回到可用范围。
- 20260513 D/H 正常后，整体 repeatability 并没有成倍提升。
- 这说明下游 repeatability 的瓶颈不仅是 D/H，也包括 Z geometry、NLOS、Tag antenna orientation、dynamic ranging noise。
- 同时也说明 robust solver 对坏数据有抵抗力：坏数据时没有彻底崩。

### 10.4 主线 4：空间弱点定位

当前最清楚的空间弱点是：

- static: `CDHG` 方向，尤其 `ID08 edge mid CDHG`
- roto: `ID29 BS2DCE mid ABEF` 和 `ID26 BS2DCE small BCGF`
- height: low height 更差
- roto tilt: planar/small tilt 差，high/vertical tilt 好

这比单一 RMS 更有工程意义，因为它告诉我们下一步应该改哪里。

---

## 11. 推荐结论

### 11.1 当前主 baseline

建议主 baseline 写：

> Current production AutoPos uses V4-io: MAD/MVUE inter-anchor fusion, Huber bounded-delay joint layout solve, and V5 diagnostics for uncertainty / usable-area analysis.

中文：

> 当前主 AutoPos baseline 是 V4-io：使用 MAD/MVUE inter-anchor fusion，Huber bounded-delay joint layout solve，并使用 V5 作为 uncertainty / usable-area 诊断层。

理由：

- AutoPos RMS 不是最低，但稳定。
- Delay sanity 好。
- Split stability 好。
- Static/roto 下游表现稳定。
- 工程上可解释。

### 11.2 V2/V3-lite 怎么解释？

V2/V3-lite 在 inter-anchor residual 上更好，但它们 no-delay。

推荐解释：

> On this clean outdoor dataset, robust pair fusion alone already explains most inter-anchor measurements. However, no-delay solvers do not model antenna bias explicitly, so they are strong baselines but less physically complete than V4-io.

中文：

> 在这次干净的户外数据中，robust pair fusion 本身已经能解释大部分 inter-anchor measurement。因此 V2/V3-lite 的 residual 很低。但它们没有显式建模 antenna delay，所以适合作为强 baseline，而不是最终 production model。

### 11.3 V3-full 怎么解释？

不要把 V3-full 作为成功版本。

推荐写：

> V3-full demonstrates that naive/free antenna-delay estimation can be unstable. Although it reduces many small residuals, its delay variables become too large and the p95/max residuals worsen.

中文：

> V3-full 说明“引入 antenna delay estimation”本身不是自动变好。如果 delay 没有合理约束，它会吸收几何误差，导致 tail residual 和 split stability 变差。

### 11.4 RotoArm 怎么解释？

推荐写：

> RotoArm data is valuable as a Z-observability and dynamic validation source. The high/vertical tilt experiments show much lower circle residual than planar/small tilt. However, the current V4-io-roto branch is still experimental and trades inter-anchor self-consistency for dynamic-tail improvement.

中文：

> RotoArm 数据有价值，尤其用于 Z 可观测性和动态验证。high/vertical tilt 明显优于 planar/small tilt。但当前 V4-io-roto 还只是实验分支，它改善了 roto tail，却牺牲了 inter-anchor self-consistency，因此不应直接替代 V4-io。

### 11.5 Calibration Wand 怎么解释？

推荐写：

> Static calibration-wand constraints were tested because they were part of the original experimental concept. The current W01-W04 data shows only weak improvement and the wand rigid-body distance bias remains around 50-60 mm, so it is not yet strong enough to drive layout calibration.

中文：

> Calibration wand 约束确实测试了，因为这是原始实验构想的一部分。但 W01-W04 当前三边刚体误差仍在 50-60 mm 量级，只带来弱改善，不足以作为主 layout calibration 约束。

---

## 12. 推荐给教授看的核心表

如果时间有限，建议最终报告只放这些表：

1. **Version summary table**
   - AutoPos RMS / p95
   - Static median / p95
   - Roto median / p95

2. **Holdout table**
   - first500 train vs last500 holdout

3. **Split stability table**
   - per-anchor first500 vs last500 layout difference

4. **Delay sanity table**
   - delay min/max/L2/bound hits

5. **Static spatial summary**
   - center vs edge
   - low/mid/high
   - facing direction

6. **Roto tilt ablation table**
   - planar/small/mid/high/vertical

7. **Wand rigid-body bias table**
   - W01-W04 measured vs GT

8. **Worst-case table**
   - worst static captures
   - worst roto captures

---

## 13. 最终一句话总结

这次实验最适合这样总结：

> We validated AutoPos without OptiTrack by combining inter-anchor self-consistency, train/holdout generalization, split-layout stability, delay sanity, static repeatability, dynamic RotoArm consistency, and calibration-wand checks. The current V4-io solver is the best production baseline: it is not always the lowest residual, but it has stable bounded delay, strong split consistency, and reliable downstream validation. RotoArm and Wand constraints are useful exploratory signals, but they should remain experimental branches until the joint constraint model and data quality improve.

中文版本：

> 在没有 OptiTrack 的情况下，我们用 inter-anchor 自洽性、train/holdout 泛化、split layout 稳定性、delay 合理性、static repeatability、RotoArm 动态一致性和 calibration wand 刚体检查共同评价 AutoPos。当前最适合作为 production baseline 的是 V4-io：它不一定拥有最低 residual，但 delay 合理、split 稳定、下游验证稳定。RotoArm 和 Wand 约束提供了有价值的探索性信息，但在 joint constraint model 和数据质量进一步改进前，不应替代 V4-io 主线。

