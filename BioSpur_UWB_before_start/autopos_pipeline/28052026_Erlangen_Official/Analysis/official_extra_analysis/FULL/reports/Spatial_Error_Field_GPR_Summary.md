# UWB 空间误差场建图与动态迁移诊断报告

Updated: 2026-06-22

## 1. 背景与动机

此前 V4/V5 Erlangen 实验已经表明，UWB 自标定存在明显的“延迟-布局耦合”问题：求解器可以通过牺牲物理尺度来吸收一部分结构化测距偏差。V4-io 布局在 Sim3 意义下约有 0.958 的尺度压缩，而 V5 common-mode 布局把尺度恢复到约 1.010，但静态定位误差并不只由锚点尺度决定。静态 raw-frame lower-tail 分析进一步说明，tag-anchor 测距中存在可被统计选择削弱的正偏尾部。

在这个背景下，本实验检验一个更直接的数据驱动假设：若 UWB 解算坐标中存在稳定的三维空间误差场，则可以训练高斯过程回归（GPR）模型

`f(x_uwb, y_uwb, z_uwb) -> (dx, dy, dz)`

来预测补偿向量 `truth - uwb`，在不改变硬件、不重新设计固件的前提下提升绝对定位精度。实验分三步推进：先看静态 24 点是否存在可学习空间场，再看静态场能否迁移到动态 ROTO，最后用动态数据自身做严格 capture-level 交叉验证，诊断动态误差中是否仍含空间可补偿成分。

## 2. 数据资产说明

本轮实验使用两个核心监督数据表。

| 数据资产 | 路径 | 用途 | 规模与关键字段 |
| --- | --- | --- | --- |
| 静态 V4-io 解算与 Vicon 真值表 | `Analysis/official_extra_analysis/FULL/tables/tag_abs_errors_per_session.csv` | 训练/验证静态空间误差场 | 全表 120 行；筛选 `version == "v4-io"` 得到 24 个静态点。输入为 `aligned_x_mm`, `aligned_y_vertical_mm`, `aligned_z_mm`；真值为 `truth_x_mm`, `truth_y_vertical_mm`, `truth_z_mm`；误差列 `err_x_mm`, `err_y_vertical_mm`, `err_z_mm`。符号检查确认 `err_* = aligned - truth`，因此补偿训练目标使用 `-err_* = truth - aligned`。 |
| 动态 RotoArm V5 D_LOO 解算与 Vicon 真值表 | `Analysis/official_extra_analysis/FULL_V5_roto_deepdive/tables/roto_v5_dloo_samples.csv` | 动态迁移与动态自训练验证 | 原始 40,661 行；剔除 `err3d_mm > 500` 或非有限值后保留 40,547 行。输入为 `x`, `y`, `z`；真值为 `truth_x`, `truth_y`, `truth_z`；共有 17 个 captures：R01-R17。 |

需要注意的是，实验 B 是一个强压力测试：静态训练表来自 `FULL` 的 V4-io 静态结果，而动态测试表来自 `FULL_V5_roto_deepdive` 的 V5 D_LOO ROTO 结果。因此它检验的是“静态 V4-io 空间场能否跨管线解释 V5 动态轨迹”，不是同一求解管线下的最公平迁移。

## 3. 实验 A：静态空间误差场建模与留一交叉验证（LOOCV）

脚本路径：

`Analysis/official_extra_analysis/FULL/scripts/static_loocv_gpr_compensation.py`

### 实验设计

对 24 个静态 V4-io 点执行严格 leave-one-out 交叉验证。每一折取 1 个静态点作为测试集，其余 23 个点作为训练集。所有模型都预测或生成补偿向量 `delta = truth - uwb`，然后计算：

`p_corrected = p_uwb + delta_hat`

并以 `p_corrected` 到 Vicon 真值的三维欧氏距离作为补偿后误差。

比较 4 个模型：

| 模型 | 描述 |
| --- | --- |
| Model 0 Baseline | 不补偿，直接使用 `aligned_*` 坐标。 |
| Model 1 Global Translation | 在训练集中计算平均补偿向量，并应用到测试点。 |
| Model 2 Affine/Linear | 使用 `LinearRegression` 拟合 `X -> delta`。 |
| Model 3 Coordinate-only GPR | 三个独立 `GaussianProcessRegressor` 预测 `dx`, `dy`, `dz`；输入/输出均 `StandardScaler`；核函数为 `ConstantKernel * Matern(nu=1.5) + WhiteKernel`；物理 length-scale bounds 为 300-5000 mm。 |

### 实验结果

| Model | Median mm | P95 mm | RMSE mm |
| --- | ---: | ---: | ---: |
| Model 0 Baseline | 73.963 | 282.129 | 139.551 |
| Model 1 Global Translation | 86.091 | 274.475 | 142.075 |
| Model 2 Affine/Linear | 75.470 | 145.104 | 91.566 |
| Model 3 Coordinate-only GPR | 42.300 | 121.566 | 69.272 |

输出图：

`Analysis/official_extra_analysis/FULL/figs/loocv_static_compensation_cdf.png`

### 结论分析

静态 LOOCV 结果显示，V4-io 静态定位误差中存在强烈的非线性空间结构。简单全局平移不仅没有帮助，反而使 median 从 73.963 mm 变差到 86.091 mm；线性/仿射模型能显著压低尾部（P95 从 282.129 mm 降到 145.104 mm），但对 median 改善有限。Coordinate-only GPR 则同时改善典型误差与尾部，把静态 median 从 73.963 mm 降到 42.300 mm，P95 从 282.129 mm 降到 121.566 mm，RMSE 从 139.551 mm 降到 69.272 mm。

这说明静态空间误差场不是一个纯全局偏移，也不是完全线性的尺度/剪切误差，而是带有可学习的局部非线性畸变。由于验证是 24 点严格 LOOCV，这个结果比在同一批点上训练/测试更可信；但它仍然只覆盖 Erlangen 房间内 24 个离散静态位置，不能直接推出动态轨迹泛化能力。

## 4. 实验 B：静态 GPR 向动态轨迹的迁移测试

脚本路径：

`Analysis/official_extra_analysis/FULL/scripts/roto_dynamic_gpr_transfer.py`

### 实验设计

使用全部 24 个静态 V4-io 点训练最终 coordinate-only GPR 模型，模型配置与实验 A 的 Model 3 保持一致。随后将该静态空间场直接应用到清洗后的 ROTO 动态轨迹帧：

`p_dyn_corrected = p_dyn_uwb + f_static(p_dyn_uwb)`

动态数据清洗规则为剔除 `err3d_mm > 500` 的极端帧，保留 40,547 帧。

### 实验结果

| Condition | Median mm | P95 mm | RMSE mm |
| --- | ---: | ---: | ---: |
| Before compensation | 99.725 | 220.757 | 125.799 |
| After static GPR | 101.485 | 216.324 | 125.177 |

| Metric | After - Before |
| --- | ---: |
| Median | +1.759 mm |
| P95 | -4.433 mm |
| RMSE | -0.622 mm |

输出图：

`Analysis/official_extra_analysis/FULL/figs/roto_dynamic_compensation_cdf.png`

### 结论分析

静态 GPR 对动态轨迹没有产生有意义的整体改善。Median 从 99.725 mm 轻微变差到 101.485 mm；P95 和 RMSE 只有很小改善，分别为 4.433 mm 和 0.622 mm。CDF 曲线几乎重合，说明静态 24 点学到的空间场不能直接解释连续 ROTO 运动误差。

这个负结果很重要。它说明静态空间误差场确实存在，但动态误差中有额外主导因素，例如动态单帧测距噪声、姿态变化、相位中心变化、时间/相位对齐误差、瞬时 NLOS 或 ROTO-specific tag delay。换句话说，静态空间场是静态误差的重要组成部分，但不是动态误差的通用补偿器。

## 5. 实验 C：动态轨迹自身的稀疏变分高斯过程（SVGP）交叉验证

脚本路径：

`Analysis/official_extra_analysis/FULL/scripts/roto_dynamic_loco_svgp_gpu.py`

### 实验设计

为诊断动态误差中是否仍包含可建模的空间成分，使用动态数据自身训练模型，但采用严格的 Leave-One-Capture-Out（LOCO）验证，避免时间相关帧泄漏。每一折拿出一个完整 capture 的所有帧作为测试集，用剩余 16 个 captures 训练模型。

由于帧级动态数据有 40,547 行，`sklearn` exact GPR 的 O(n^3) 复杂度不适用。GPU 版脚本改用 GPyTorch sparse variational GP：

| 配置项 | 值 |
| --- | --- |
| 模型 | Batched 3-output SVGP，分别预测 `dx`, `dy`, `dz` |
| Kernel | Matern `nu=1.5` |
| Inducing points | 1024 |
| Epochs | 40 |
| Batch size | 4096 |
| GPU | 两个 worker 分别绑定 `cuda:0` 与 `cuda:1` |
| 硬件 | 2 x NVIDIA GeForce GTX 1080 Ti |
| 实时利用率采样 | GPU0/GPU1 约 55-86%，显存约 1.0-1.4 GB；CPU 总利用率约 28-35% |

该实验不做 150 mm voxel 聚合，直接使用帧级动态训练数据。作为对照，CPU exact GPR 的 150 mm voxel 版本曾得到 After median 87.469 mm；GPU 帧级 SVGP 进一步降低到 85.671 mm。

### 实验结果

| Condition | Median mm | P95 mm | RMSE mm |
| --- | ---: | ---: | ---: |
| Before compensation | 99.725 | 220.757 | 125.799 |
| After GPU SVGP LOCO | 85.671 | 191.945 | 108.207 |

| Metric | Improvement |
| --- | ---: |
| Median | -14.055 mm |
| P95 | -28.812 mm |
| RMSE | -17.592 mm |

输出图：

`Analysis/official_extra_analysis/FULL/figs/roto_dynamic_loco_svgp_gpu_cdf_ind1024_ep40.png`

### 异常现象：R14 负迁移

大多数 captures 获得了明显 median 改善，例如 R01 从 106.259 mm 降到 82.807 mm，R02 从 113.683 mm 降到 86.631 mm，R15 从 101.937 mm 降到 78.497 mm，R16 从 102.398 mm 降到 77.245 mm。

R14 是突出异常：

`R14: 89.288 mm -> 101.873 mm`

即 R14 在动态 SVGP LOCO 下出现 +12.585 mm 的 median 负迁移。CPU 150 mm voxel 版也出现同方向异常，说明这不是 GPU SVGP 的偶然训练噪声，而是 R14 的误差机制与其他 captures 不一致。

一个合理物理解释是：当 ROTO 运动姿态发生剧烈变化，尤其是接近垂直旋转或天线姿态快速改变时，DWM1001C PCB trace antenna 的方向性、相位中心偏移、瞬时遮挡/NLOS 与动态测距状态会主导误差。此时误差不再是单纯的 `x,y,z` 空间位置函数，所以 coordinate-only 空间场会把其他 captures 学到的补偿错误迁移到 R14。

### 结论分析

实验 C 改变了实验 B 的结论边界。静态空间场不能迁移到动态轨迹，但动态数据自身确实包含可建模的空间成分。GPU SVGP 在无随机帧泄漏的 LOCO 设置下把 median 从 99.725 mm 降到 85.671 mm，P95 从 220.757 mm 降到 191.945 mm，RMSE 从 125.799 mm 降到 108.207 mm。这说明动态误差中约有 14 mm median 级别的空间可补偿成分。

同时，R14 负迁移说明动态误差不是单一空间场。仅靠 `x,y,z` 映射无法覆盖姿态/运动状态驱动的误差机制。下一步若要继续压低 ROTO，需要引入 ROTO phase、速度、tag identity、anchor residual RMS、quality 或 CIR 特征，而不是继续只增加空间 GP 容量。

## 6. 综合物理机理结论

三组实验共同支持一个正交分解式的 UWB 误差机制。

### 6.1 静态空间场

静态 24 点中存在强非线性空间误差场。它可能由房间宏观多径、anchor/tag 几何畸变、布局-延迟耦合残留和局部测距偏置共同形成。Coordinate-only GPR 能把 V4-io 静态 LOOCV median 从 73.963 mm 降到 42.300 mm，说明静态绝对误差中有一大块是空间可学习的。

### 6.2 动态局部空间场

动态 ROTO 数据中也存在空间可建模成分，但它不是静态空间场的直接延续。使用动态数据自身并按 capture 留一验证，GPU SVGP 可把 median 从 99.725 mm 降到 85.671 mm。这部分更像是 ROTO 轨迹/房间/动态采样条件下的局部空间误差场，能解释一部分重复出现的轨迹相关偏差。

### 6.3 姿态/运动相关误差

R14 的负迁移说明，动态误差中还有不能由 `x,y,z` 坐标解释的成分。可能机制包括天线方向性、相位中心随姿态变化、动态 NLOS、单帧测距不稳定、时间/相位对齐误差和运动状态相关偏差。这类误差需要引入 IMU 紧耦合、ROTO phase/speed、per-anchor residual、quality_percent、CIR/NLOS 特征或 tag-specific delay 模型；单纯空间映射无法完全补偿。

## 7. 对论文叙事的建议

建议将该实验写成“空间误差场存在，但静态与动态机制不同”的诊断结果，而不是简单宣传 GPR 可以通用提升 UWB 定位。

可主张的结论：

1. 在静态 24 点上，GPR 能显著捕捉非线性空间畸变，LOOCV median 从 73.963 mm 降到 42.300 mm。
2. 静态 GPR 不能直接迁移到 ROTO 动态轨迹，median 从 99.725 mm 变为 101.485 mm，说明动态误差不是静态空间场的简单延伸。
3. 动态数据自身在严格 LOCO 验证下仍含可建模空间成分，GPU SVGP 将 median 降到 85.671 mm，P95 降到 191.945 mm。
4. R14 负迁移显示，动态误差中存在姿态/运动/天线方向性等非空间因素，这为后续 IMU、CIR 和 antenna incidence angle 模型提供了明确动机。

需要避免的过度表述：

- 不应声称 24 点静态 GPR 已经解决动态定位。
- 不应把动态 SVGP 解释为部署级实时补偿器；它目前是离线监督诊断。
- 不应把 R14 异常归因于单一原因，除非后续用姿态、相位或 CIR 特征进一步验证。

## 8. 产物清单

| 产物 | 路径 |
| --- | --- |
| 静态 LOOCV GPR 脚本 | `Analysis/official_extra_analysis/FULL/scripts/static_loocv_gpr_compensation.py` |
| 静态 GPR 到 ROTO 动态迁移脚本 | `Analysis/official_extra_analysis/FULL/scripts/roto_dynamic_gpr_transfer.py` |
| 动态 CPU exact-GPR LOCO 诊断脚本 | `Analysis/official_extra_analysis/FULL/scripts/roto_dynamic_loco_gpr.py` |
| 动态 GPU SVGP LOCO 脚本 | `Analysis/official_extra_analysis/FULL/scripts/roto_dynamic_loco_svgp_gpu.py` |
| 静态 LOOCV CDF | `Analysis/official_extra_analysis/FULL/figs/loocv_static_compensation_cdf.png` |
| 静态 GPR 动态迁移 CDF | `Analysis/official_extra_analysis/FULL/figs/roto_dynamic_compensation_cdf.png` |
| 动态 CPU voxel LOCO CDF | `Analysis/official_extra_analysis/FULL/figs/roto_dynamic_loco_gpr_cdf_voxel150.png` |
| 动态 GPU SVGP LOCO CDF | `Analysis/official_extra_analysis/FULL/figs/roto_dynamic_loco_svgp_gpu_cdf_ind1024_ep40.png` |

## 9. 总结

本轮实验给出清晰分层结论：静态 UWB 绝对误差中存在强空间场，可由 GPR 大幅补偿；但这个静态场不能直接迁移到连续动态 ROTO；动态轨迹自身仍含有空间可学习成分，GPU SVGP 在严格 LOCO 下提供约 14 mm median 改善；剩余动态误差，尤其 R14 负迁移，指向姿态、天线方向性、相位中心和动态 NLOS 等非空间机制。后续研究应从“单一空间校正场”转向“空间场 + 姿态/信道/运动状态”的组合误差模型。
