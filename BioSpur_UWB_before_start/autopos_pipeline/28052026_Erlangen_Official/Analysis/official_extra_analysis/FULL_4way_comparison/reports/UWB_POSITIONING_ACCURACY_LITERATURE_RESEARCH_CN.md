# UWB 定位精度文献调研笔记

Generated: 2026-06-03

本笔记回答一个很具体的问题：涉及 UWB indoor positioning 的论文里，2D/3D 定位精度通常是多少？他们到底用什么参数来定义“精度”？

结论先放前面：UWB 文献里的“accuracy”不能只看一个数字。必须同时看维度、ground truth、锚点是否人工测量、是否用了滤波/IMU/VIO/AoA、静态还是动态、LOS/NLOS、报的是 mean/median 还是 P90/P95。对 AutoPos 来说，最公平的主指标应是 3D Euclidean error 的 P50/P95/RMSE，同时强制拆 horizontal 与 vertical，因为 3D UWB 的 vertical 往往是最容易被藏起来的误差。

## 1. 文献里常用哪些精度指标

### 1.1 点位误差：2D 与 3D Euclidean error

最常见定义是把 UWB 输出位置与 ground truth 位置逐点相减。

2D horizontal error：

```text
e_2D = sqrt((x_uwb - x_gt)^2 + (y_uwb - y_gt)^2)
```

3D error：

```text
e_3D = sqrt((x_uwb - x_gt)^2 + (y_uwb - y_gt)^2 + (z_uwb - z_gt)^2)
```

如果坐标系不同，论文通常会先做 frame alignment。工业 UWB/Vicon 评估论文常用 SVD/Kabsch/Procrustes 将 UWB frame 对齐到 Vicon frame，再计算误差。这里必须说明是否允许 scale；如果 scale 也被 fit 进去，那这个数已经不是纯 absolute accuracy。

### 1.2 聚合统计：mean、RMSE、median、P90/P95

常见汇总方式：

- `mean error` / average accuracy：平均 Euclidean error。直观，但容易被 tail 拉高。
- `RMSE`：平方后平均再开根，更惩罚大 outlier。
- `median` / P50：典型表现，适合说明“平时大概多准”。
- `P75/P90/P95/P99`：tail performance。工程上 P90/P95 通常比 median 更重要，因为系统上线怕的是坏点。
- `CDF/eCDF`：整条误差分布，UWB 论文很常见。
- `max`：少数论文报，但如果没有样本数和 NLOS 情况，意义有限。

### 1.3 Precision / repeatability

很多论文会区分：

- `accuracy`：相对 ground truth 的绝对误差。
- `precision` 或 `repeatability`：同一静态点重复测量时的 dispersion/std。

这点和我们现在的分析完全一致：AutoPos 的 absolute error 是 `P_opti - P_autopos`；repeatability 是同一个 capture 内 solved positions 自己的散布。滤波会改变输出点 `P_autopos_tag_capture`，所以 absolute error 当然也会变；但它主要改善随机抖动，不一定能消除 layout/scale 的系统误差。

### 1.4 Per-axis error：尤其是 vertical

严肃的 3D UWB 报告应该拆：

- signed bias: `mean(dx), mean(dy), mean(dz)`
- absolute axis error: `abs(dx), abs(dy), abs(dz)` 的 P50/P95
- horizontal error vs vertical error

原因很简单：很多 UWB 系统 2D 看起来很漂亮，但 3D vertical 会差一个量级。文献中也明确出现过 horizontal RMSE 约 3--8 cm、vertical RMSE 约 36 cm 到 1.91 m 的情况；所以只报 2D 或只报 3D mean 都不够诚实。

### 1.5 Dynamic-specific metrics

动态定位通常还会报：

- trajectory ATE / trajectory RMSE
- pointwise 2D/3D error CDF
- latency / time alignment error
- update rate / sample rate
- dropout / availability
- speed-bin error
- phase-bin error
- path/radius/turn-center/relative-distance consistency

对于我们的 ROTO，必须明确“没有绝对 UTC 同步”，所以时间 offset 是通过轨迹形状/误差最小化估计出来的。这个 offset estimate 本身要成为方法的一部分。

### 1.6 Geometry / deployment metrics

常见影响参数：

- number of anchors
- anchor height and 3D spread
- coplanar vs non-coplanar deployment
- HDOP / VDOP / PDOP / GDOP
- LOS/NLOS ratio
- tag height / body shielding / metal clutter
- anchor survey accuracy
- antenna delay calibration
- filtering or fusion: EKF, UKF, PF, IMU, VIO, AoA

这意味着论文对比时不能只写“某论文 10 cm，我们 7 cm”。要写清楚：他们是不是已知锚点？有没有 Vicon/laser survey？有没有 EKF/VIO/AoA？报的是 2D 还是 3D？静态还是动态？

## 2. 代表性文献数字

下面只列和我们最相关的 UWB 定位/跟踪文献。数字按原论文口径保留，所以不要跨论文直接排名。

| Paper / system | Scenario | Reported accuracy | Metrics they use | Notes for AutoPos comparison |
|---|---:|---:|---|---|
| Delamare et al., 2020, static/dynamic UWB industrial evaluation | 3D, Vicon ground truth, LOS industrial/lab | Dynamic mapping examples约 23--24 cm 3D mean；4/5/6 anchor dynamic 3D mean约 24/18/20 cm | RMSE, eCDF, X/Y/Z, 2D, 3D, mean error, std | 和我们一样使用 Vicon 对齐并拆 2D/3D；但锚点是人工/Vicon测量，不是纯 self-calibration。Source: https://www.mdpi.com/2413-4155/2/2/23 |
| Decawave MDEK1001 8-range EKF improvement, 2021 | 3D, MDEK1001/PANS, static/moving indoor | 大房间 8-range P75 3D约 16 cm；公寓场景 8-range P75约 60 cm；4-range/PANS coverage会失败 | 3D CDF, P75, coverage/fix failure, EKF output | 很适合对比 PANS/MDEK1001。关键是它用了 EKF 和额外 ranges；他们强调 coverage 与 failed fixes。Source: https://www.mdpi.com/1424-8220/21/5/1787 |
| AAU industrial static/mobile UWB evaluation, 2022 | 2D industrial, static + AMR mobile | 静态 optimized median 17 cm / P90 40 cm；mobile optimized median 19 cm / P90 52 cm；不同 tag placement 14--28 cm median | median, 90%-ile, 99%-ile, samples, SD, static/mobile CDF | 非常适合引用，因为它直接说 realistic industrial UWB 通常是 few-decimeter，而 ideal scenarios 才可能 sub-decimeter。Source: https://www.mdpi.com/2079-9292/11/20/3294 |
| Comprehensive NLOS / linearization evaluation, 2023 | 3D simulation/algorithm comparison | 3D P95：EKF/UKF约 1.32/1.25 m，PF/RPF约 0.71/0.67 m；horizontal RMSE 3--8 cm，但 vertical RMSE 36 cm--1.91 m | CDF P50/P68/P95, RMSE, average error, horizontal vs vertical | 这篇很适合支持“vertical 必须单独报”。它也说明 3D UWB 中 vertical 和 linearization/filter choice 会极大影响结果。Source: https://www.mdpi.com/2076-3417/13/10/6187 |
| UTIL UWB TDoA dataset, IJRR 2024 | UAV 3D, DWM1000 TDoA + IMU/height, mocap ground truth | Good LOS/anchor constellations 可以到约 8--15 cm RMSE；obstructed/NLOS 会明显恶化 | APE RMSE, threshold success rate, mocap GT, raw TDoA/SNR/power features | 机器人/UAV领域常用 APE RMSE 和 threshold success。不是纯 TWR，也通常融合 IMU/height。Source: https://journals.sagepub.com/doi/full/10.1177/02783649241230640 |
| In-home Pozyx tracker evaluation, 2024 | 2D/static/dynamic mobility device tracking | Mock condo static X/Y约 21.1 ± 9.4 cm 与 17.3 ± 8.9 cm；dynamic rolling约 19.1 cm，walking约 20.5 cm | mean ± SD, static/dynamic task error | 说明真实生活环境里 20 cm 级别很常见。Source summary: https://www.sciencedirect.com/science/article/pii/S1350453324000560 |
| DOEC / 3D UWB + AoA, IEEE TCE 2025 | 3D static/dynamic, custom method with AoA/Kalman/error correction | Static average从 0.76 m 到 6.9 cm；dynamic average从 1.08 m 到 23 cm | average static/dynamic positioning error | 这是增强型系统，不是纯 inter-anchor self-calibration；可以作为“带 AoA/滤波/补偿的高级方案”对比。Source: https://colab.ws/articles/10.1109%2Ftce.2025.3570630 |
| UTrack3D, MobiSys 2024 | tabletop 3D UWB tracking, CIR/phase-based, controlled volume | 约 90% samples < 9 mm；速度/receiver数量影响 tail | tracking error CDF, 90%-ile, speed sensitivity, receiver count | 这是 tracking/phase/CIR 类系统，和普通 TWR multilateration不是同一个问题。它说明“UWB 可以很准”，但不能拿来直接压 AutoPos。Source: https://faculty.cc.gatech.edu/~dhekne/UTrack3D_MobiSys2024.pdf |
| Indoor UWB positioning/tracking dataset, Scientific Data 2023 | Range/CIR dataset, static positions along paths | 主要提供 dataset 与 reproducible analysis tools，不主打单一定位 headline | range error, CIR, LoS/NLoS, positioning/tracking algorithm evaluation | 有价值在于它强调 UWB ranging/CIR 数据集、LoS/NLoS、anchor-tag combinations。Source: https://www.nature.com/articles/s41597-023-02639-5 |

## 3. 粗略量级总结

### 3.1 商用 TWR/TDoA，已知锚点，较好 LOS

常见数字：

- 2D static: 1--20 cm，取决于环境是否理想。
- 3D static: 10--30 cm 更常见；vertical 可能显著拖后腿。
- dynamic: 15--30 cm median/mean 很常见；P90/P95 可到 40--100 cm，尤其在工业 clutter 或低高度 tag。

### 3.2 真实工业/居家环境

常见数字：

- median/mean 通常在 15--30 cm；
- P90/P95/P99 tail 可能非常宽；
- NLOS、人体遮挡、金属、低 tag height、anchor geometry 都会把 tail 推到 0.5--1 m 甚至更高。

AAU 2022 的一句话很有用：idealized/simplified scenarios 可以 sub-decimeter，但 realistic operational scenarios 通常是 few-decimeter。

### 3.3 DWM1001 / PANS 类系统

MDEK1001/PANS 如果只用有限 4 ranges：

- 3D 会因为 redundancy 不足而不稳定；
- coverage / failed fix 是必须报的指标；
- 用更多 ranges + robust EKF 后，P75 可明显改善，但仍会随房间/NLOS从十几厘米到几十厘米。

### 3.4 高级自研系统

如果用了 AoA、VIO、IMU、CIR phase tracking、custom multi-antenna receiver：

- 可以报 1--7 cm，甚至 mm-level tracking；
- 但这不是普通 UWB multilateration，也不是纯 inter-anchor self-calibration。

这种文献适合放在 Related Work 的 “enhanced UWB / sensor fusion / phase tracking” 小节，不适合当 direct baseline。

## 4. AutoPos 应该怎么和文献对齐

AutoPos 的核心卖点不是“单点最小误差”，而是：

1. 纯 UWB inter-anchor self-calibration；
2. 不依赖现场 Vicon/laser/tape anchor survey；
3. 3D anchor layout + tag localization 都被 OptiTrack 独立验证；
4. 把 layout、scale、delay、tag solver、DOP、dropout、ROTO 动态误差拆开。

因此建议论文中这样定义主指标：

### 4.1 Anchor layout accuracy

主指标：

- reflection-allowed rigid no-scale RMS
- shape RMS / pairwise distance distortion
- similarity scale diagnostic 只能作为 diagnostic，不能作为 accuracy claim

不要把 similarity-scaled number 当 headline，因为那用了 OptiTrack truth 修 scale。

### 4.2 Static tag accuracy

主指标：

- 3D Euclidean error: P50, P90, P95, RMSE, max
- horizontal 2D error: P50/P95
- vertical absolute error: P50/P95
- signed X/Y/Z bias
- repeatability: per-capture D3 std
- outlier rate: within 50/80/100/200/300 mm

FULL 当前可写：

```text
Corrected FULL static production v4-io:
74.0 mm median 3D, 282.1 mm P95.

Corrected FULL static raw replay v4-io/T4:
69.7 mm median 3D, 173.9 mm P95.
```

如果写一句话，建议：

```text
Against independent OptiTrack ground truth, AutoPos achieves about 7 cm median static 3D absolute error under the corrected FULL dataset, with a wide vertical/tail component that must be reported separately.
```

### 4.3 ROTO / dynamic accuracy

主指标：

- 3D pointwise trajectory error P50/P95/RMSE
- horizontal vs vertical dynamic P95
- turn-center error
- radius error
- two-wand relative-distance consistency
- error vs angular speed
- error vs rotation phase
- time-offset selection method and residual uncertainty

FULL 当前可写：

```text
Corrected FULL ROTO v4-io/T4:
105.8 mm track-median 3D P50, 231.8 mm track-median 3D P95.
```

一句话：

```text
Dynamic ROTO absolute validation is about 10 cm median 3D trajectory error and roughly 20--23 cm P95, after capture-level time-offset alignment to OptiTrack.
```

### 4.4 Literature positioning statement

可以这样放到论文或报告里：

```text
Compared with published UWB indoor positioning evaluations, AutoPos falls in the sub-decimeter-to-decimeter regime for median static 3D accuracy and around decimeter-level for dynamic ROTO trajectory accuracy. Its contribution is different from most surveyed-anchor UWB systems: the anchor coordinates are recovered from inter-anchor UWB ranging alone and then validated against OptiTrack, rather than being manually measured or provided by the optical system.
```

## 5. 对外写作时必须避免的坑

### 5.1 不要拿我们的 3D median 去比别人 2D mean

很多论文只报 2D，或者把 vertical 固定/忽略。我们是 3D anchor self-calibration + 3D tag validation，不能拿 3D 和 2D 直接排名。

### 5.2 不要拿未滤波 calibration-level validation 去比别人 EKF/VIO output

我们故意保留无滤波版，是为了 measurement-level / calibration-level validation。Filtered Deployment Output 是另一个问题：上线后会改善多少。

### 5.3 不要混淆 absolute accuracy 与 repeatability

滤波可以让 repeatability 变好，也会改变 absolute error；但如果主误差是 layout scale/delay coupling，滤波不能从根上修复。

### 5.4 不要把 known-anchor control 当 field result

OptiTrack anchors + delaycal 是 lower bound/control，不是现场可直接达到的 AutoPos claim。

### 5.5 不要隐藏 vertical

必须拆 horizontal 与 vertical。UWB 3D 论文里 vertical 常常最差；我们自己的结果也是 vertical/tail 结构非常明显。

## 6. 推荐论文指标模板

论文 Method / Results 可以固定用下面这个模板：

```text
For each evaluated method, we report:

1. 3D Euclidean absolute error:
   mean, RMSE, P50, P90, P95, P99, max.

2. Horizontal and vertical components:
   horizontal 2D error P50/P95, vertical absolute error P50/P95,
   signed X/Y/Z bias and standard deviation.

3. Precision/repeatability:
   per-static-capture D3 standard deviation.

4. Coverage/outlier:
   valid-fix rate and percentage below 50/80/100/200/300 mm.

5. Dynamic metrics:
   trajectory ATE/RMSE, P50/P95, time-offset method, speed/phase bins,
   turn-center/radius/relative-distance consistency.

6. Geometry/calibration:
   number of anchors, 3D anchor spread, HDOP/VDOP/GDOP,
   anchor survey or self-calibration method, delay treatment,
   filter/fusion state.
```

## 7. 最短结论

文献中 UWB 2D/3D 定位精度从厘米级到米级都有，但公平比较必须按实验条件拆开。理想 LOS、已知锚点、滤波/融合系统可以做到几厘米到十几厘米；真实工业/居家环境常见是 15--30 cm median/mean，tail 可到 0.5--1 m；3D vertical 往往明显差于 horizontal。AutoPos 当前 FULL 结果属于 median sub-decimeter / dynamic decimeter-level 的范围，贡献点在于纯 UWB anchor self-calibration 和 OptiTrack 绝对验证，而不是只追求单个最小定位数字。
