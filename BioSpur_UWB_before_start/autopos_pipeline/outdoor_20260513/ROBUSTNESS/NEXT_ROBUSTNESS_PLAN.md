# AutoPos 下一步 Robustness / Z-Observability 计划

目标：不是简单追一个最低 RMS，而是系统性回答三个问题：

1. 当前 8-anchor baseline 在什么情况下会失稳？
2. 失稳主要来自低冗余、NLOS/low-QF、Z 几何弱，还是某些 anchor / 区域 / 朝向？
3. 如果要增加 anchor 或引入 Roto/Wand 约束，怎样证明它真的提高 robustness，而不是只在某个指标上偶然变好？

本计划优先使用现有 20260513 数据，尽量不要求重新采集。需要新实验的部分单独列出。

---

## 0. 当前认知

当前最重要的发现：

- Static Tag repeatability median 约 `48-50mm`。
- XYZ 拆解后，Z median 约 `38-41mm`，Z share 约 `62-64%`。
- 低高度、`CDHG` 朝向、部分 edge 区域 tail 更明显。
- Roto vertical configuration 给最强 Z excitation；每转圆心重复性在 vertical 下最好。
- Wand W01-W04 作为三 Tag 相对几何验证有信息量，但 pairwise bias RMS 约 `58-59mm`，数据不够干净，适合 soft constraint / diagnostic。

因此下一步主线：

> 从 “layout 平均能不能用” 转向 “在丢 anchor / NLOS / 低冗余 / Z 弱几何下是否仍然稳定”。

---

## 1. Low-QF / NLOS 诊断

### 1.1 目的

回答：

- 当前 solver 对 low quality / NLOS 是如何处理的？
- 某个 anchor 被手挡住、NLOS、或者 response 偏差大时，是被剔除、降权，还是直接拉偏定位？
- 是否存在 `valid=1` 但实际 range 有系统偏差的情况？

### 1.2 当前机制

当前 downstream solver 的处理大致是：

- 只用 `valid == 1` 的 range。
- 每帧有效 anchor `<4` 时跳过。
- `>=4` 时用 sigma-weighted Huber 解 tag position。
- Huber 会 downweight 大 residual。
- 但没有真正的语义级 NLOS classifier。

因此当前系统是：

> valid gate + anchor_sigma + Huber residual downweight

不是：

> explicit NLOS detection and rejection

### 1.3 现有数据可做的分析

对所有 static / roto / wand tag-anchor frame 生成 per-anchor residual diagnostic：

每条 observation 输出：

- capture ID
- tag peer
- sweep / timestamp
- anchor ID
- measured range
- predicted range from solved position
- residual = predicted - measured
- quality_percent
- valid/status
- anchor_sigma
- Huber weight
- anchors_used

然后按 anchor / 区域 / 高度 / facing 统计：

| 指标 | 含义 |
| --- | --- |
| residual median | 系统性偏大/偏小 |
| residual MAD / RMS | 噪声水平 |
| residual p95 | tail / NLOS 风险 |
| low-QF rate | low quality 比例 |
| large-residual rate | residual 超阈值比例 |
| Huber-downweighted rate | 被 Huber 降权比例 |

### 1.4 关键图

- 每个 anchor 的 residual histogram。
- residual vs quality_percent scatter。
- residual spatial heatmap：XY / XZ / YZ。
- 每个 anchor 的 large-residual spatial map。
- `quality_percent` 低但 residual 正常、以及 `quality_percent` 高但 residual 异常的对比。

### 1.5 判断标准

如果某 anchor 经常出现：

- residual median 明显偏正/偏负；
- residual p95 大；
- quality 高但 residual 大；
- 只在某些空间区域/朝向变差；

说明它可能是 NLOS / antenna orientation / local geometry 问题，而不是全局 layout 问题。

---

## 2. Anchor Removal Ablation

### 2.1 目的

不是证明“加 anchor 一定更好”，而是找当前 8-anchor baseline 的脆弱点。

回答：

- 断掉 A/B/C/D/E/F/G/H 任意一个，系统是否还能输出？
- 哪个 anchor 对 Z 最关键？
- 哪些 static position / facing 对某个 anchor 最依赖？
- 断一个 anchor 后 fail rate 是否明显上升？

### 2.2 实验设计

使用 `FULL-COMPARE-1000/v4-io/layout.json`，对所有 static captures 重跑：

| Condition | Active anchors |
| --- | --- |
| baseline | A B C D E F G H |
| no-A | B C D E F G H |
| no-B | A C D E F G H |
| ... | ... |
| no-H | A B C D E F G |

每个 condition 都使用同一套 static captures。

### 2.3 输出指标

整体：

- solved frame rate
- insufficient frame rate
- X/Y/Z median std
- 3D median std
- Z share
- 3D p95
- worst capture

按分组：

- location: center / edge
- height: low / mid / high
- facing: ABEF / BCGF / CDHG / ADHE

### 2.4 注意事项

删除 anchor 只能说明：

> 当前系统对这个 anchor 的依赖程度。

不能直接说明：

> 新增 anchor 一定提高精度。

所以报告中应避免写：

> remove D gets worse, therefore adding more anchors will improve.

应该写：

> remove-one ablation identifies robustness bottlenecks and anchor dependency.

---

## 3. Random Dropout Robustness

### 3.1 目的

模拟真实 broadcast 场景下 anchor response 不稳定：

- 有时 8 anchors 全部返回；
- 有时只有 7/6/5；
- 个别 anchor 因 NLOS / timing / antenna orientation 丢失。

回答：

- 当前系统在随机丢包下有多稳？
- 从 8 anchor 降到 7/6/5/4 时，Z 是否快速恶化？
- 4-anchor 是否出现低 residual 但高 position spread 的现象？

### 3.2 两种仿真

#### A. keep-k anchors

每帧随机保留 k 个 anchor：

| Condition | k |
| --- | ---: |
| keep8 | 8 |
| keep7 | 7 |
| keep6 | 6 |
| keep5 | 5 |
| keep4 | 4 |

每个 k 重复 100-500 次 Monte Carlo。

#### B. independent dropout probability

每个 anchor 独立 dropout：

| Condition | dropout probability |
| --- | ---: |
| p05 | 5% |
| p10 | 10% |
| p20 | 20% |
| p30 | 30% |
| p40 | 40% |

每个 condition 重复 100-500 次。

### 3.3 输出指标

- solved frame rate
- fail rate `<4 anchors`
- median anchors used
- X/Y/Z med
- 3D med
- Z share
- 3D p95
- per-capture worst tail

### 3.4 关键判断

如果 keep6 仍接近 baseline，但 keep5/keep4 急剧变差：

> 系统需要至少 6 anchors 才有稳定 redundancy。

如果某些 facing 在 keep7 都明显变差：

> 不是数量问题，而是特定空间区域/朝向对某些 anchor 过度依赖。

---

## 4. 4-Anchor Online Solver 复现

### 4.1 背景

Concept PDF 里 `130mm` 级 tag error 很可能来自 online 4-anchor solver / selector，而不是当前 offline all-anchor solver。

已确认：

- offline 4-anchor selector 会放大 V1 spread；
- 但 20260513 好数据 + offline LS 没有复现 130mm；
- 真正放大的可能是 online solver 状态机、selector、初始化、实时限制和低冗余共同作用。

### 4.2 下一步

如果要严格复现旧结果，需要找到或重建当时 online solver 逻辑：

- active anchor selection
- two-level enforcement
- quality / geometry / volume score
- on-tag solver initial guess
- iteration count / convergence rule
- possible filtering / previous-frame dependence

然后用 20260513 data 做 offline replay。

### 4.3 输出

对比：

| Solver | Anchor mode | Static 3D med | Z med | p95 | fail rate |
| --- | --- | ---: | ---: | ---: | ---: |
| offline all-anchor | variable all valid | | | | |
| offline 2+2 selector | best 4 | | | | |
| reconstructed online selector | real-time-like 4 | | | | |

---

## 5. Candidate Anchor Simulation

### 5.1 目的

回答：

- 增加 anchor 是否可能提高 robustness？
- 应该加在哪个方向、高度、区域？
- 新 anchor 对 Z observability 是否有真实帮助？

### 5.2 为什么不能只靠 removal ablation

Removal ablation 只能证明：

> 当前 8 anchors 缺一个会变差。

它不能证明：

> 增加第 9/10 个 anchor 一定变好。

因为新 anchor 可能：

- 几何位置好；
- 几何位置差；
- NLOS 多；
- 天线朝向差；
- 引入坏 range；
- 被 solver 错误信任。

### 5.3 可做的候选 anchor 仿真

基于当前 V4-io layout 和 static / roto coverage，构造候选 anchor 点：

- 当前 CDHG / edge weak 区域外侧；
- 当前低高度 Z tail 较大的方向；
- 上层新 anchor；
- 下层新 anchor；
- 对角方向新 anchor；
- 高度明显不同的新 anchor。

对每个 candidate 计算：

- GDOP / FIM uncertainty；
- predicted Z uncertainty；
- random dropout 下的 fail rate；
- keep-k 下的 robustness；
- with candidate vs without candidate 的差异。

### 5.4 噪声假设

新 anchor 不应假设完美。建议三档：

| Scenario | New anchor sigma |
| --- | ---: |
| optimistic | 30mm |
| nominal | 50mm |
| pessimistic | 80mm |

如果只有 optimistic 有收益，说明加 anchor 风险大。

如果 nominal / pessimistic 仍提升 Z uncertainty 或 fail rate，说明该位置值得实测。

---

## 6. Vertical Roto Z-Injection 实验

### 6.1 当前发现

Roto tilt 分组下，vertical 的 per-turn center repeatability 最好：

- vertical 3D med 约 17mm；
- small tilt 反而更差；
- 这符合 vertical Roto 给 Z 最强 excitation 的物理预期。

### 6.2 下一轮实验设计

如果要把 RotoArm 从 validation 升级为 calibration constraint，建议：

| Group | Count | Purpose |
| --- | ---: | --- |
| planar | 1 | baseline |
| mid tilt | 2 | medium Z excitation |
| vertical | 4+ | strongest Z injection |

每组要求：

- 足够圈数；
- 记录实际朝向；
- 尽量保持转速稳定；
- 保存 per-frame positions；
- 输出 per-turn center XYZ。

### 6.3 判断

如果 vertical consistently：

- 降低 Z std；
- 降低 Roto dR RMS；
- 降低 turn-center RMS；
- 不恶化 static validation；

则可以把 vertical Roto 写成：

> Z-observability injection constraint

---

## 7. Wand 下一轮数据要求

当前 Wand 只能看 pairwise distance median/bias，不能拆 XYZ。

下一轮 pipeline 应保存：

- capture ID
- peer name
- sweep/time
- x/y/z solved position
- anchors used
- pct_ge8
- residual
- quality summary

这样才能计算：

- 三颗 Tag 的相对向量；
- 每条边在 X/Y/Z 的分量偏差；
- 三角形是否旋转/缩放/扭曲；
- W05 动态时 TDMA 时间错位影响。

---

## 8. 优先级

建议按这个顺序做：

1. **Per-anchor residual / low-QF / NLOS diagnostic**
   - 最直接回答 “坏 range 如何进入 solver”。

2. **Leave-one-anchor-out ablation**
   - 找当前 8-anchor baseline 的依赖点。

3. **Random dropout robustness**
   - 模拟真实 broadcast 丢包和低冗余。

4. **Candidate anchor simulation**
   - 用 FIM/GDOP + dropout 判断加 anchor 的合理位置。

5. **Online 4-anchor solver replay**
   - 解释旧 PDF 130mm 机制。

6. **Vertical Roto Z-injection clean experiment**
   - 支撑下一版 V4-io-roto / V5 的故事线。

---

## 9. 最终报告应包含的表

### Table A: Low-QF / NLOS residual diagnostic

| Anchor | residual med | residual RMS | abs p95 | low-QF rate | downweighted rate |
| --- | ---: | ---: | ---: | ---: | ---: |

### Table B: Leave-one-anchor-out

| Condition | solved rate | X med | Y med | Z med | 3D med | Z share | 3D p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |

### Table C: Random dropout

| Condition | solved rate | median anchors | X med | Y med | Z med | 3D med | 3D p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |

### Table D: Candidate anchor simulation

| Candidate | height | location | FIM Z improvement | dropout p95 improvement | risk |
| --- | --- | --- | ---: | ---: | --- |

### Table E: Roto tilt / Z injection

| Tilt | N | X med | Y med | Z med | 3D med | Z share |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |

---

## 10. 推荐对教授的表述

中文：

> 当前结果显示，AutoPos 的平均 repeatability 已经接近 5cm，但误差并不是各向同性的，Z 方向贡献了约 62-64% 的 3D variance。因此下一步不应只追求整体 RMS，而应系统评估垂直可观测性和低冗余 robustness。我们将通过 per-anchor residual/NLOS diagnostic、leave-one-anchor-out、random dropout Monte Carlo 和 candidate-anchor FIM simulation 来定位脆弱区域，并判断是否需要增加 anchor 或引入 vertical Roto Z-injection。

English:

> The current AutoPos repeatability is close to 5 cm, but the error is not isotropic: the Z component contributes about 62-64% of the 3D variance. The next step should therefore focus on vertical observability and low-redundancy robustness rather than only minimizing the overall RMS. We will use per-anchor residual/NLOS diagnostics, leave-one-anchor-out ablation, random-dropout Monte Carlo, and candidate-anchor FIM simulation to identify weak regions and evaluate whether additional anchors or vertical Roto Z-injection are justified.
