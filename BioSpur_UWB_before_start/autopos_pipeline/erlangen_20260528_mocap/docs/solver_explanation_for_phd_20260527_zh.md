# AutoPos V1-V4 Solver 中文说明

这份文档是为了明天现场解释用的。目标不是写论文，而是能清楚回答：

- 我们到底测了什么？
- layout 是怎么解出来的？
- V1 到 V4-io 有什么区别？
- static / roto / wand 在这里到底是拟合输入，还是验证数据？
- ultrasound height 在 solver 里起什么作用？

## 一句话总结

我们不是直接测 anchor 的 XYZ 坐标。我们先通过 AutoPos sweep 收集 anchor-anchor 之间的 UWB 距离，然后把它变成一个 3D distance-geometry 问题来求解 anchor layout。当前主要使用的 `V4-io` 会鲁棒融合有方向的 inter-anchor 测距，并同时估计 anchor 坐标和有边界的 per-anchor range-delay / bias。标准 `V4-io` 里，static / roto / wand capture 只是后续验证，不参与 anchor layout 拟合。

## 我们实际测了什么

AutoPos sweep 会让 A-H 每个 anchor 轮流当 master。

例如：

- `SW-A`：A 是 master。
- `SW-B`：B 是 master。
- ...
- `SW-H`：H 是 master。

在 `SW-A` 的时候，系统会记录 A 到其他 anchor 的距离。在 `SW-B` 的时候，系统会记录 B 到其他 anchor 的距离。这样同一个 anchor pair 可能有两个方向的数据：

```text
A -> B
B -> A
```

这些原始 sweep 数据会被 stage 到：

```text
solver/work/field_dataset_staged/sweep1000/pairs_all.csv
```

solver 的 anchor layout 输入就是这个 staged sweep 表。

## 标准 solver 不使用什么

标准 layout solver 包括：

- `V1`
- `V2`
- `V3-lite`
- `V3-full`
- `V4-io`

这些版本解 anchor layout 的时候，不使用 static / roto / wand capture。

逻辑是：

```text
sweep data -> 解 anchor layout
static / roto / wand -> 验证这个 layout 好不好
```

只有实验性分支，例如 `v4-io-roto` 或 `v4-io-wand`，才会把 roto / wand 信息注入 layout。那种情况必须单独说明，不能和标准 `V4-io` 混在一起讲。

## 为什么要做 directed pair fusion

以 A-B 为例，sweep 里可能同时有：

```text
A -> B
B -> A
```

这两个方向的距离不一定完全一样。原因包括：

- 天线方向性。
- anchor 摆放方向。
- 局部遮挡和多径。
- 信号质量差异。
- radio / clock / delay bias。

所以 solver 不是直接拿某一条数据，而是先把 directed measurement 融合成一个更稳健的 pair distance。

V1 到 V4-io 的区别可以这样解释：

| Version | Pair fusion | Delay model | 含义 |
|---|---|---|---|
| V1 / `v1-old` | 双向简单平均 | 无 delay | 最早的 baseline |
| V2 / `v2` | inverse-variance weighted fusion | 无 delay | 对噪声大的方向降权 |
| V3-lite / `v3-lite` | median + MAD/MVUE robust fusion | 无 delay | 更鲁棒地处理不对称和离群值 |
| V3-full / `v3-full` | MAD/MVUE robust fusion | per-anchor delay，交替更新 | 第一版 delay-aware solver |
| V4-io / `v4-io` | MAD/MVUE robust fusion | bounded per-anchor delay，joint Huber solve | 当前主要生产版本 |

## Anchor Layout 是怎么解出来的

pair fusion 之后，solver 得到一组 anchor-anchor 距离：

```text
d_AB, d_AC, ..., d_GH
```

然后求解 8 个 anchor 的 3D 坐标：

```text
p_A, p_B, ..., p_H
```

对于没有 delay 的版本，残差基本是：

```text
residual_ij = ||p_i - p_j|| - d_ij
```

对于 `V4-io`，残差是：

```text
residual_ij = ||p_i - p_j|| + b_i + b_j - d_ij
```

这里：

- `p_i` 是 anchor `i` 的 3D 坐标。
- `d_ij` 是融合后的 anchor `i` 和 anchor `j` 的测量距离。
- `b_i` / `b_j` 是每个 anchor 的 range-delay / bias correction。

当前代码里的 `V4-io` 使用：

- Huber robust loss。
- 距离残差尺度约 `15 mm`。
- delay regularization 约 `20 mm`。
- delay bound 为 `[-60, +60] mm`。
- soft two-layer physical prior。

## Soft Two-Layer Prior 是什么

这个 prior 的意思不是强行把每层 anchor 压成一个完美平面。

它表达的是物理常识：

- A/B/C/D 应该属于 lower layer。
- E/F/G/H 应该属于 upper layer。
- D 可以和 A/B/C 有一定 Z 偏差。
- E/F/G/H 不强制完全共面。
- 但整体上 ABCD 应该低于 EFGH。
- 上下层间距应该在合理范围内。

也就是说，这个 prior 是软约束，用来防止 solver 得到明显不符合物理摆放的解。

## 为什么坐标系本身有歧义

只靠距离，无法确定绝对世界坐标。距离只能确定形状，不能确定：

- 整体平移。
- 整体旋转。
- 镜像方向。

所以代码必须人为固定一个 coordinate gauge：

- A 放在原点。
- B 定义本地 X 方向。
- A/B/C 定义初始 gauge plane。

这不代表现实中 A/B/C 一定完全水平。它只是为了消除数学自由度。

因此，如果只看 UWB 距离，solver 本身不知道真实世界的左右方向，也不知道某个镜像是不是物理正确。这个需要额外物理约定或外部测量来确定。

## Ultrasound Height 是什么角色

Ultrasound height 不是标准 `V4-io` inter-anchor solve 的一部分。

纯 UWB solver 输出：

```text
layout.json
```

然后 ultrasound 后处理输出：

```text
layout_us_height.json
```

当前 ultrasound 后处理在有 F/G/H 测高数据时，使用 F/G/H 的 antenna-center height 来找一个物理 z-up 的刚性坐标对齐：

```text
z_corrected = dot(raw_xyz, fitted_z_axis) + z_shift
```

它做三件事：

- 选择物理正确的 Z 方向。
- 保证 ABCD 在 EFGH 下方。
- 把选中的上层 anchor 对齐到 ultrasound 测得的 antenna-center 高度。

它不修改原始 sweep 数据，也不改变 inter-anchor solver 的 residual。它是坐标系和高度的后处理对齐。

## 各个验证指标是什么意思

### AutoPos RMS / P95

这是 inter-anchor residual：

```text
predicted anchor-anchor distance - fused measured anchor-anchor distance
```

它说明解出来的 anchor layout 能多好地解释 AutoPos sweep 数据。

注意：这不是 tag 定位误差。

### Static Median / Static P95

Static capture 是 stationary tag。tag 不动，solver 用固定 anchor layout 逐帧解 tag position，然后看点云扩散程度。

它回答的问题是：

```text
tag 不动时，解出来的位置稳不稳定？
```

### Roto dR RMS / Turn-Center Median

RotoArm 是运动学一致性验证。两个 tag 装在旋转臂上，半径差是机械上已知的。

它回答的问题是：

```text
解出来的 tag 轨迹是否符合一个真实旋转刚体的行为？
```

它不是严格意义上的绝对 motion-capture ground truth。

### Wand Validation

Wand validation 检查多个 tag 的刚体距离是否保持一致。

它回答的问题是：

```text
解出来的 tag position 是否保持已知刚体几何关系？
```

## 为什么 RMS 好也可能 layout 错

低 AutoPos RMS 只说明 layout 能拟合 UWB 测距图。它不自动证明物理方向一定正确。

可能的问题包括：

- layout 被镜像。
- z-up 方向反了。
- z 方向可观测性弱，导致坐标系倾斜。
- garage / vehicle / metal 环境造成多径。
- 某些 antenna blind direction 的 pair 距离很差。
- delay 变量吸收了本应属于 geometry 的错误。

所以我们还需要看：

- ultrasound height alignment。
- ABCD / EFGH 上下层关系。
- first-half / second-half split stability。
- static repeatability。
- roto consistency。
- wand rigid-body consistency。

## 明天可以直接这样解释

可以直接说：

> 我们先做 AutoPos sweep，让 A-H 每个 anchor 轮流当 master，得到有方向的 inter-anchor UWB 距离。然后 solver 把 A->B 和 B->A 这类 directed measurement 鲁棒融合成一个 pair distance，再解一个 3D distance-geometry 问题。当前主要使用的 V4-io 会同时估计 anchor 坐标和有边界的 per-anchor range-delay / bias，并使用 Huber loss 和 soft two-layer physical prior。标准 V4-io 不用 static、roto、wand 去拟合 anchor layout，这些 capture 是后续验证。Ultrasound height 是把纯 UWB layout 对齐到物理 z-up frame 的后处理，不是替代 UWB solve。

## 如果对方问“你怎么知道这个 layout 是好的？”

分三层回答：

1. Sweep residual 小：
   `AutoPos RMS` 和 `AutoPos p95` 说明 layout 能解释 inter-anchor sweep 距离。

2. Layout 能泛化：
   把 sweep 分成 first half 和 second half，各自解 layout，再做 rigid alignment，比较 anchor 位置差异。

3. 独立 capture 数据符合物理：
   static tag 稳定，roto tag 保持合理半径差，wand tag 保持刚体距离。

## 如果对方问“最大的弱点是什么？”

可以诚实回答：

> 纯 UWB inter-anchor distance 无法唯一确定绝对坐标方向和镜像方向，而且两层矩形 anchor setup 的 Z 方向可观测性比 XY 弱。所以 solver 需要物理约定和 optional ultrasound height alignment 来选择正确物理坐标系。另外 garage 这种环境多径更强，某些 pair 会被拉偏，所以 outdoor LOS 是更干净的验证环境。

## 相关代码文件

- `autopos_pipeline/erlangen_20260528_mocap/solver/scripts/run_v1_to_v4_io.py`
- `autopos_pipeline/erlangen_20260528_mocap/solver/scripts/run_v4io_field_check.py`
- `autopos_pipeline/erlangen_20260528_mocap/solver/scripts/apply_ultrasound_height_to_layout.py`
- `autopos_pipeline/outdoor_20260513/run_clean_full_compare.py`
- `autopos_pipeline/outdoor_20260513/analysis_20260513_182053/run_full_evaluation_same_pipeline_20260513.py`

