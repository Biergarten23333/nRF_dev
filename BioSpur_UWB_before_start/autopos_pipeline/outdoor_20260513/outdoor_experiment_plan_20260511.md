# 室外大实验计划 - 2026-05-13

目标：用当前稳定的 broadcast SS-TWR 系统，采集一套干净、可解释、能写进报告的 AutoPos 室外数据。核心不是只用 Wand 验证精度，而是把 Wand 的已知 T 结构作为几何约束，用来进一步校准/细化 Anchor layout。静态 Tag 和 Roto Arm 用于验证 refined layout 的定位效果。

## 当前稳定基线

- `Master_Anchor`：只控制 Anchor，不用于普通 Tag。
- `Master_Tag`：只控制 Tag，不用于 Anchor。
- Anchor 固件：冻结的 A18 common anchor image。
- Anchor / Master_Anchor 固件保持冻结；室外实验期间不改 Anchor image，不 OTA Anchor，不重刷 Master_Anchor。
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
5. 不向 Tag 端 push APOS layout；所有 layout / position 都用离线 solver 计算。
6. 用 `BSF66F` 采集完整静态定位 ID 序列，离线验证 refined layout。
7. 用 Roto Arm 采集动态圆周运动 ID 序列，离线验证动态定位。

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

## Phase 3 - Wand Capture 准备

使用 `Master_Tag`，不要使用 `Master_Anchor`。这一步不做 APOS layout push；只确认 Wand Tags 能稳定按 `10Hz/tag` 输出 raw TR 数据。初始 layout 和后续 refined layout 都只在离线 solver 中使用。

Targets：

```text
Wand Tags: BSCCF4, BS9336, BS955A
```

通过条件：

- 三个 Wand target 都 `10Hz/tag` CFG verify 成功。
- 60s smoke capture 中三只 Wand Tags 都有连续 TR 输出。
- capture 文件完整记录 raw TR、Tag BS code、anchor id、range、quality、valid/status 字段。
- 不依赖设备端在线 position 输出；现场只采 raw range，回到离线 solver 计算 layout / pose / position。

## Phase 4 - Wand-Assisted Layout Calibration 数据集

这一阶段是明天最重要的部分。Wand 不是只用来验证精度；Wand 的三 Tag 已知 T 结构是额外几何约束，应当用于反向细化 Anchor layout。

Wand 用 BS code：

```text
BSCCF4,BS9336,BS955A
```

采集计划：

| ID | Wand 姿态 | 时长 | 目的 | Note
|---|---|---:|---|
| W01 | 小架子固定，AB 为三角形局部底边；现场摆放时 AB 尽量沿 Z 轴，A 上 B 下，C 为尖端且指向 ABEF | 120s | 稳定刚体约束 | windy env Wand will slightly turn, relative pisition relationship is important, absolute position must not be considered |
| W02 | 同样小架子姿态，绕重心旋转，C 指向 BCGF | 120s | 多方向约束 | windy env Wand will slightly turn, relative pisition relationship is important, absolute position must not be considered |
| W03 | 同样小架子姿态，绕重心旋转，C 指向 CDHG | 120s | 多方向约束 | windy env Wand will slightly turn, relative pisition relationship is important, absolute position must not be considered |
| W04 | 同样小架子姿态，绕重心旋转，C 指向 ADHE | 120s | 多方向约束 | windy env Wand will slightly turn, relative pisition relationship is important, absolute position must not be considered |
| W05 | Free Move：像 OptiTrack Calibration Wand 一样，在空间内缓慢移动和旋转 | 180s | 大范围 Wand 约束 |

现场备注：不要求平桌子。实际可执行 fixture 是小架子：从刚体局部几何看，
`BSCCF4-BS9336` 形成三角形的 AB 边，`BS955A` 是三角形尖端。这里的
“AB 为底边”只是三角形局部几何定义，不表示现场一定水平放置。现场摆放时
AB 尽量接近竖直方向，即近似沿 Z 轴，`BSCCF4` 在上、`BS9336` 在下；
然后通过绕重心旋转整个 fixture 来改变 `BS955A` 尖端的大致朝向。

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

## Phase 5 - Offline Wand-Refined Layout 生成

不向任何 Tag push Wand-refined layout。用 Phase 1 的 inter-anchor sweep 和 Phase 4 的 Wand TR 数据，在电脑端离线生成 refined layout，并冻结为本次实验后续验证使用的 solver 输入。

输入：

```text
inter-anchor sweep: Phase 1 的 100/500 set sweep
Wand captures: W01-W06 raw TR
```

通过条件：

- `layout_v4_wand_refined.json` 生成成功。
- `wand_refinement_report.md` 记录 inter-only vs refined 的 anchor coordinate delta。
- refined layout 不能让 inter-anchor residual 明显变坏。
- Phase 6/7 的 BSF66F/Roto 验证使用同一个 frozen offline layout 文件，不在设备端切换 APOS layout。

## Phase 6 - BSF66F 静态定位验证数据集

这一阶段主要用 `BSF66F` 做 static reference。它最接近现有 AutoPos
paper-style 结果，是验证 Wand-refined layout 的静态主对比数据。`BS2DCE/BSDC91` 不作为
static 主体，它们属于后面的 Roto Arm 动态实验。

推荐采集：

| ID | Tag 摆放位置 | 时长 | 备注 |
|---|---|---:|---|
| ID01 | Near ABEF face, low height | 60s | edge + low |x
| ID02 | Near ABEF face, mid height | 60s | edge + mid |x
| ID03 | Near ABEF face, high height | 60s | edge + high |x
| ID04 | Near BCGF face, low height | 60s | edge + low | x
| ID05 | Near BCGF face, mid height | 60s | edge + mid | x
| ID06 | Near BCGF face, high height | 60s | edge + high |x
| ID07 | Near CDHG face, low height | 60s | edge + low | x
| ID08 | Near CDHG face, mid height | 60s | edge + mid | x
| ID09 | Near CDHG face, high height | 60s | edge + high |x
| ID10 | Near ADHE face, low height | 60s | edge + low | 
| ID11 | Near ADHE face, mid height | 60s | edge + mid | x
| ID12 | Near ADHE face, high height | 60s | edge + high |x
| ID13 | Center mid, Tag faces ABEF | 60s | known orientation | x
| ID14 | Center mid, Tag faces BCGF | 60s | known orientation | x
| ID15 | Center mid, Tag faces CDHG | 60s | known orientation | x
| ID16 | Center mid, Tag faces ADHE | 60s | known orientation | x
| ID17 | Center low, Tag faces ABEF | 60s | known orientation | x
| ID18 | Center low, Tag faces BCGF | 60s | known orientation | x
| ID19 | Center low, Tag faces CDHG | 60s | known orientation | x
| ID20 | Center low, Tag faces ADHE | 60s | known orientation | x
| ID21 | Center high, Tag faces ABEF | 60s | known orientation |x
| ID22 | Center high, Tag faces BCGF | 60s | known orientation |x
| ID23 | Center high, Tag faces CDHG | 60s | known orientation |x
| ID24 | Center high, Tag faces ADHE | 60s | known orientation |x

通过条件：

- `BSF66F` 约 `10Hz/tag`。
- 每段 capture 中 8 个 Anchor 都经常被看到。
- 静态 center-mid 的 3D std 应该接近之前的 `40-50mm` 范围。

## Phase 7 - Roto Arm Test 动态圆周验证数据集

这一阶段直接做，不作为 optional。Roto Arm 用 `BS2DCE` 和 `BSDC91` 两个
RotoTag。真实轨迹近似 3D 圆，后处理用 3D circle fit residual 评估动态定位误差。

推荐采集：

数据记录目录：
`/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_20260513/Roto_Test`

| ID | Roto 姿态 | 时长 | 备注 |
|---|---|---:|---|
| ID25 | Almost planar | 180s | planar 时不区分 antenna faces 哪个面 | x
| ID26 | Small tilt, antenna faces ABEF | 180s | 上次已完成的基础方向 | x
| ID27 | Small tilt, antenna faces BCGF | 180s | 上次已完成的基础方向 | x
| ID28 | Small tilt, antenna faces CDHG | 180s | 上次已完成的基础方向 | x
| ID29 | Small tilt, antenna faces ADHE | 180s | 上次已完成的基础方向 | x
| ID30 | Mid tilt, antenna faces ABEF | 180s | 上次因雨中断，这次补齐 | x
| ID31 | Mid tilt, antenna faces BCGF | 180s | mid tilt | x
| ID32 | Mid tilt, antenna faces CDHG | 180s | mid tilt | x
| ID33 | Mid tilt, antenna faces ADHE | 180s | mid tilt | x
| ID34 | High tilt, antenna faces ABEF | 180s | high tilt | x
| ID35 | High tilt, antenna faces BCGF | 180s | high tilt | x
| ID36 | High tilt, antenna faces CDHG | 180s | high tilt | x
| ID37 | High tilt, antenna faces ADHE | 180s | high tilt | x
| ID38 | Almost vertical, antenna faces ABEF | 180s | near vertical | x
| ID39 | Almost vertical, antenna faces BCGF | 180s | near vertical | x
| ID40 | Almost vertical, antenna faces CDHG | 180s | near vertical | x
| ID41 | Almost vertical, antenna faces ADHE | 180s | near vertical | x

注意：

- 除 almost planar 外，"faces ABEF/BCGF/CDHG/ADHE" 是现场姿态标签，用于对比不同方向。
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
Roto_Test/
Wand_Test/
Roto_Test/
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
