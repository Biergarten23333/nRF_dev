# 室外大实验计划 - 2026-05-11

目标：用当前稳定的 broadcast SS-TWR 系统，采集一套干净、可解释、能写进报告的 AutoPos 室外数据。核心不是只用 Wand 验证精度，而是把 Wand 的已知 T 结构作为几何约束，用来进一步校准/细化 Anchor layout。静态 Tag 和 Roto Arm 用于验证 refined layout 的定位效果。

## 当前稳定基线

- `Master_Anchor`：只控制 Anchor，不用于普通 Tag。
- `Master_Tag`：只控制 Tag，不用于 Anchor。
- Anchor 固件：冻结的 A18 common anchor image。
- Wand/Tag 稳定回退线：V015。
- Wand Tags 固定就是这三个：`BSCCF4`, `BS9336`, `BS955A`。
- 老三 Tag 固定就是这三个：`BSF66F`, `BS2DCE`, `BSDC91`。
- 当前可靠的 Wand 能力：`10Hz/tag`，不是 `30Hz/tag`。
- 当前 Wand 物理 ground truth：
  - `BSCCF4 --285mm-- T center --385mm-- BS9336`
  - `T center --595mm-- BS955A`
  - 卷尺测量误差约 `2-5mm`。

## 实验优先级

1. 室外摆好后，确认 8 个 Anchor 都在线。
2. 采集高质量 inter-anchor sweep。
3. 解算 V4 inter-only 初始 layout。
4. 采集 Wand T 结构数据，用已知刚体几何细化 Anchor layout。
5. 把 Wand-refined layout push 到 Tags。
6. 用 `BSF66F` 采集完整静态定位 ID 序列，验证 refined layout。
7. 用 Roto Arm 采集动态圆周运动 ID 序列，验证动态定位。

## 场地布置

使用 8 Anchor 双层布局。上下层高度差尽量拉大。之前分析已经说明 Z 方向是主要弱项，所以高度差比细小的 XY 对称性更重要。

Anchor 摆放规则：

- 下层：A/B/C/D。
- 上层：E/F/G/H。
- 如果空间允许，不要让所有上层 Anchor 都正好压在下层 Anchor 正上方；给系统一些真实 3D 几何。
- 尽量保留 D/H，但不要让 D/H 的问题阻塞整个实验。我们已经知道 `Best6 no D/H` 仍然可以接近 8 Anchor 的结果。

## Phase 0 - 出门前 Bench Sanity

通过条件：不需要 flash，只做 BLE/UWB sanity check。

1. 给 8 个 Anchor 上电。
2. 给 `Master_Anchor` 上电。
3. 跑一个短的 `10 set` anchor sweep。
4. 给 `Master_Tag` 上电。
5. 老三 Tag 跑 `10Hz/tag`，采集 60s。
6. Wand 跑 `10Hz/tag`，采集 60s。

明天早上不要追 `20Hz/30Hz`。室外大实验依赖的是好数据，不是频率冲刺。

## Phase 1 - 室外 Anchor Sweep

室外 Anchor 摆好后：

1. 给所有 Anchor 上电。
2. 确认 `Master_Anchor` 能看到 8 个 Anchor。
3. 先跑 `100 set` anchor sweep。
4. 如果所有 pair 看起来稳定，再跑 `500 set` 作为 publication-quality 数据。

通过条件：

- 8 个 Anchor 全部在线。
- 28 个 anchor pair 都存在。
- D/H 相关 pair 不能持续缺失。
- 每个 pair 的质量异常要记录，不要隐藏。

如果时间或天气不好：

- `100 set` 足够用于明天的 layout 验证。
- `500 set` 是更好的最终数据，但不是必须阻塞项。

## Phase 2 - 解算 Layout

用最新 sweep 跑 V4 inter-only solve。

立刻报告：

- inter-anchor RMS。
- 每个 pair 的 residual 表。
- Anchor 坐标。
- D/H residual 状态。
- 如果任意 pair residual 超过 `50mm`，明确给出 solver warning。

通过条件：

- Layout 物理上合理。
- RMS 接近之前室外 baseline。
- 除非是 gauge 镜像造成的符号变化，否则不能出现明显不可能的高度/坐标。

## Phase 3 - Push 初始 Layout 到 Wand Tags

使用 `Master_Tag`，不要使用 `Master_Anchor`。这一步只需要先 push 到 Wand Tags，让 Wand capture 能用初始 layout 做在线/离线位置求解。老三 Tag 和 RotoTag 可以等 Wand-refined layout 出来后再统一 push。

Targets：

```text
Wand Tags: BSCCF4, BS9336, BS955A
```

通过条件：

- 三个 Wand target 都报告 layout push 成功。
- verify readback 显示初始 layout/version 已生效。
- 如果 Wand push 失败，先修 Wand push，不要直接跳到 BSF66F/Roto 验证。

## Phase 4 - Wand-Assisted Layout Calibration 数据集

这一阶段是明天最重要的部分。Wand 不是只用来验证精度；Wand 的三 Tag 已知 T 结构是额外几何约束，应当用于反向细化 Anchor layout。

Wand 用 BS code：

```text
BSCCF4,BS9336,BS955A
```

采集计划：

| ID | Wand 姿态 | 时长 | 目的 |
|---|---|---:|---|
| W01 | 小架子固定，AB 为底边，C 为尖端，C 指向 ABEF | 120s | 稳定刚体约束 |
| W02 | 同样小架子姿态，绕重心旋转，C 指向 BCGF | 120s | 多方向约束 |
| W03 | 同样小架子姿态，绕重心旋转，C 指向 CDHG | 120s | 多方向约束 |
| W04 | 同样小架子姿态，绕重心旋转，C 指向 ADHE | 120s | 多方向约束 |
| W05 | 小架子上做小角度可控倾斜，保持稳定 | 120s | Z/高度约束 |
| W06 | Free Move：像 OptiTrack Calibration Wand 一样，在空间内缓慢移动和旋转 | 180s | 大范围 Wand 约束 |

现场备注：不要求平桌子。实际可执行 fixture 是小架子：让
`BSCCF4-BS9336` 形成三角形底边，`BS955A` 是三角形尖端。通过绕重心旋转整个 fixture 来改变 `BS955A` 尖端的大致朝向。

重要解释：`C 指向某个 Anchor 面` 只是物理摆放/尖端朝向标签，不等价于
`BS955A` 的天线主瓣一定正对该面。因为安装方式和板子方向不同，尖端朝向
只是可重复的姿态定义，不是严格天线方向定义。

### Wand Layout Refinement 思路

用 inter-anchor sweep 的 V4 layout 作为初值，然后联合优化：

- Anchor 坐标和 anchor delay。
- 每一帧 Wand 的刚体 pose：位置 + 姿态。
- Wand 三个 Tag 的已知局部坐标：
  - `BSCCF4 = (-285, 0, 0) mm`
  - `BS9336 = ( 385, 0, 0) mm`
  - `BS955A = (   0,-595, 0) mm`

优化残差：

- inter-anchor sweep residual，作为 Anchor layout prior。
- Wand Tag 到 Anchor 的 UWB range residual。
- Wand rigid-body constraint：三 Tag 相对坐标固定，不允许自由漂移。

输出：

```text
layout_v4_interonly_initial.json
layout_v4_wand_refined.json
wand_refinement_report.md
```

必须比较：

- refined layout vs inter-only layout 的 Anchor 坐标变化。
- inter-anchor RMS 是否明显变坏；如果明显变坏，说明 Wand 权重太大。
- Wand rigid residual 是否下降。
- 后续 BSF66F static 3D std 是否下降。
- 后续 Roto circle-fit residual 是否下降。

通过条件：

- 三个 Wand 都约 `10Hz/tag`。
- 每个 Wand capture 尽量看到 8 个 Anchor。
- Wand-refined layout 不能破坏 inter-anchor sweep residual。
- Free Move 数据单独分析，不和静态 Wand pose 混在同一个 repeatability 统计里。

## Phase 5 - Push Wand-Refined Layout 到所有 Tags

使用 `Master_Tag`，不要使用 `Master_Anchor`。

Targets：

```text
老三 Tag: BSF66F, BS2DCE, BSDC91
Wand Tags: BSCCF4, BS9336, BS955A
```

通过条件：

- 每个 target 都报告 refined layout push 成功。
- verify readback 显示 refined layout/version 已生效。
- 如果某一个 Wand Tag 失败，先继续老三 Tag/Roto 验证；Wand 可以后面再重试。

## Phase 6 - BSF66F 静态定位验证数据集

这一阶段主要用 `BSF66F` 做 static reference。它最接近现有 AutoPos
paper-style 结果，是验证 Wand-refined layout 的静态主对比数据。`BS2DCE/BSDC91` 不作为
static 主体，它们属于后面的 Roto Arm 动态实验。

推荐采集：

| ID | Tag 摆放位置 | 时长 | 备注 |
|---|---|---:|---|
| ID01 | Center low, antenna orientation unknown | 60s | center height baseline |
| ID02 | Center mid, antenna orientation unknown | 180s | primary comparison point |
| ID03 | Center high, antenna orientation unknown | 60s | Z observability |
| ID04 | Near ABEF face, low height | 60s | edge + low |
| ID05 | Near ABEF face, mid height | 60s | edge + mid |
| ID06 | Near ABEF face, high height | 60s | edge + high |
| ID07 | Near BCGF face, low height | 60s | edge + low |
| ID08 | Near BCGF face, mid height | 60s | edge + mid |
| ID09 | Near BCGF face, high height | 60s | edge + high |
| ID10 | Near CDHG face, low height | 60s | edge + low |
| ID11 | Near CDHG face, mid height | 60s | edge + mid |
| ID12 | Near CDHG face, high height | 60s | edge + high |
| ID13 | Near ADHE face, low height | 60s | edge + low |
| ID14 | Near ADHE face, mid height | 60s | edge + mid |
| ID15 | Near ADHE face, high height | 60s | edge + high |
| ID16 | Center mid, Tag faces ABEF | 60s | known orientation |
| ID17 | Center mid, Tag faces BCGF | 60s | known orientation |
| ID18 | Center mid, Tag faces CDHG | 60s | known orientation |
| ID19 | Center mid, Tag faces ADHE | 60s | known orientation |
| ID20 | Center low, Tag faces ABEF | 60s | known orientation |
| ID21 | Center low, Tag faces BCGF | 60s | known orientation |
| ID22 | Center low, Tag faces CDHG | 60s | known orientation |
| ID23 | Center low, Tag faces ADHE | 60s | known orientation |
| ID24 | Center high, Tag faces ABEF | 60s | known orientation |
| ID25 | Center high, Tag faces BCGF | 60s | known orientation |
| ID26 | Center high, Tag faces CDHG | 60s | known orientation |
| ID27 | Center high, Tag faces ADHE | 60s | known orientation |

通过条件：

- `BSF66F` 约 `10Hz/tag`。
- 每段 capture 中 8 个 Anchor 都经常被看到。
- 静态 center-mid 的 3D std 应该接近之前的 `40-50mm` 范围。

## Phase 7 - Roto Arm 动态圆周验证数据集

这一阶段直接做，不作为 optional。Roto Arm 用 `BS2DCE` 和 `BSDC91` 两个
RotoTag。真实轨迹近似 3D 圆，后处理用 3D circle fit residual 评估动态定位误差。

推荐采集：

| ID | Roto 姿态 | 时长 | 备注 |
|---|---|---:|---|
| ID28 | Small tilt, antenna faces ABEF | 120s | 上次已完成的基础方向 |
| ID29 | Small tilt, antenna faces BCGF | 120s | 上次已完成的基础方向 |
| ID30 | Small tilt, antenna faces CDHG | 120s | 上次已完成的基础方向 |
| ID31 | Small tilt, antenna faces ADHE | 120s | 上次已完成的基础方向 |
| ID32 | Mid tilt, antenna faces ABEF | 120s | 上次因雨中断，这次补齐 |
| ID33 | Mid tilt, antenna faces BCGF | 120s | mid tilt |
| ID34 | Mid tilt, antenna faces CDHG | 120s | mid tilt |
| ID35 | Mid tilt, antenna faces ADHE | 120s | mid tilt |
| ID36 | High tilt, antenna faces ABEF | 120s | high tilt |
| ID37 | High tilt, antenna faces BCGF | 120s | high tilt |
| ID38 | High tilt, antenna faces CDHG | 120s | high tilt |
| ID39 | High tilt, antenna faces ADHE | 120s | high tilt |
| ID40 | Extra pass for worst / most suspect direction | 120s | 根据 ID28-ID39 的现场质量决定 |

注意：

- 这里的 "faces ABEF/BCGF/CDHG/ADHE" 是现场姿态标签，用于对比不同方向。
- 不要把这个标签过度解释成天线主瓣一定正对该 Anchor 面；安装方式、Tag 姿态、外壳方向都会影响实际天线方向。
- 后处理以 circle-fit residual、radius consistency、two-tag center/normal consistency 为主。

通过条件：

- `BS2DCE/BSDC91` 都能稳定输出。
- 每个 capture 尽量看到 8 个 Anchor。
- 不出现明显随时间漂移。
- 动态 residual 按之前纯 UWB 动态 baseline 解读：`100-300mm` RMS 对 pure UWB 是正常范围。

## 数据命名

使用一个顶层日期目录：

```text
autopos_pipeline/outdoor_20260511/
```

推荐子目录：

```text
sweeps/
solves/
tag_captures/
wand_captures/
roto_captures/
reports/
figures/
```

每个 capture summary 应记录：

- 物理摆放说明；
- Tag list；
- 时长；
- 请求 Hz；
- 实际 Hz；
- valid row count；
- anchors seen；
- 天气 / LOS 备注。

## Stop Conditions

遇到下面情况先停下来 debug，不要继续往后跑：

- anchor sweep 少于 8 个 Anchor；
- layout solve 出现明显不可能的几何；
- TDMA verify 超过一个 Tag 失败；
- V015 rollback 后 Wand 三个 Tag 不能全部达到 `10Hz/tag`。
- Wand-refined layout 让 inter-anchor residual 明显变坏；
- 老三 Tag 无法保持 `10Hz/tag`。

如果 Phase 1-5 不干净，不要把后面的 BSF66F/Roto 结果当作 refined layout 的有效验证。

## 预期最终报告表格

1. Anchor sweep quality：pair median、MAD、residual。
2. Layout result：Anchor 坐标和 RMS。
3. Wand layout refinement：inter-only vs Wand-refined Anchor 坐标变化。
4. Wand rigid-body residual summary。
5. Wand pair-distance verification vs mechanical ground truth。
6. 老三 Tag 静态定位：每个 capture 的 X/Y/Z/3D std。
7. Center vs edge summary。
8. Roto circle-fit dynamic residual summary。
9. Key finding：Wand 是否真的改善/约束了 Anchor layout，而不只是验证精度。
