# Filtered Deployment Output 中文总结

本报告总结 Erlangen 2026-05-28 official dataset 上新增的 tag-output filtering 分析。它是
deployment-output supplement，不替代原来的无滤波 official validation。

## 1. 分析目的

原始 official analysis 故意不加 EKF/UKF/KF，因为它要验证 calibration-level /
measurement-level accuracy：AutoPos layout 和 tag solver 本身到底准不准。

这次 filtered deployment analysis 回答的是另一个问题：

> 如果真实系统上线时在 tag output 后面加时间滤波，static capture 的输出会稳多少？
> absolute accuracy 会不会跟着明显变好？

结论很清楚：

- filter 显著改善 repeatability / jitter；
- filter 对 absolute accuracy 只有小幅、方向不稳定的改善；
- absolute tail 仍然主要由 layout / delay / scale calibration error 决定。

## 2. 已完成矩阵

本次完整跑完：

```text
5 layouts: v1-old, v2, v3-lite, v3-full, v4-io
x 2 eval sets: all8, noG
x (T1--T4 + F0--F5, plus T5a--T5e)
= 290 solver cells
x 24 static captures
= 6960 per-session rows
```

输出位置：

- `filtered_deployment/tables/filtered_static_abs_errors_per_session.csv`
- `filtered_deployment/tables/filtered_static_accuracy_summary.csv`
- `filtered_deployment/tables/filtered_static_metrics_full.csv`
- `filtered_deployment/tables/filtered_static_per_axis_bias.csv`
- `filtered_deployment/tables/filtered_static_outlier_rates.csv`
- `filtered_deployment/tables/filtered_static_radial_decomposition.csv`
- `filtered_deployment/tables/filtered_static_bootstrap_ci.csv`
- `filtered_deployment/reports/filtered_static_results.md`
- `filtered_deployment/reports/filtered_static_bootstrap_ci.md`

## 3. Filter 命名

外置 position filter：

- `F0`: 无滤波 baseline。
- `F1`: causal constant-velocity position Kalman filter。
- `F2`: robust position Kalman filter，带 innovation downweighting。
- `F3`: adaptive static/dynamic position Kalman filter。
- `F4`: fixed-lag smoother，有有限输出延迟，deployment 上可实现。
- `F5`: full RTS offline smoother，只适合 appendix/diagnostic，不是实时零延迟输出。

原生 range-space filtered solver：

- `T5a`: range EKF，constant-velocity state。
- `T5b`: robust range EKF。
- `T5c`: adaptive range EKF；有 IMU 活动量时可调 process noise。
- `T5d`: range UKF。
- `T5e`: range EKF + common range-bias state。

## 4. Absolute Error vs Repeatability

两个指标不是一回事。

Absolute error:

```text
absolute error = || P_opti - P_autopos_tag_capture ||
```

它回答“准不准”。它受 layout scale bias、anchor delay、frame alignment、vertical geometry
等系统性误差影响。

Repeatability:

```text
repeatability = per-frame positions around the capture's own center
```

它回答“稳不稳、抖不抖”。它不需要 OptiTrack truth。滤波器最擅长改善这个。

## 5. 最公平的滤波前后对比

最公平口径是同一个 solver 的 `F0 -> F4/F5`。这里 `F0` 是无滤波，`F4/F5` 是滤波后。

注意：下面必须拆开 horizontal 和 vertical。本文表格里 `horizontal` 指 OptiTrack
frame 的 `X/Z` 平面；`vertical` 指 OptiTrack frame 的 `Y` 轴。对应到 AutoPos solver
习惯，可以理解为 `XY` 水平精度和 `Z` 高度/垂直精度。不能只看 3D，因为本系统一直是
horizontal 相对好、vertical 明显更差。

### v4-io / all8 / T3

| metric | T3+F0 无滤波 | T3+F4 fixed-lag | T3+F5 RTS offline |
|---|---:|---:|---:|
| absolute median 3D | 62.3 mm | 59.8 mm | 60.3 mm |
| absolute P95 3D | 158.2 mm | 155.2 mm | 155.2 mm |
| absolute RMS 3D | 101.8 mm | 100.9 mm | 100.7 mm |
| horizontal median | 44.8 mm | 45.2 mm | 45.1 mm |
| horizontal P95 | 101.2 mm | 97.9 mm | 97.0 mm |
| vertical median | 48.6 mm | 44.3 mm | 42.5 mm |
| vertical P95 abs. | 147.3 mm | 144.5 mm | 144.4 mm |
| repeat-D3 median | 58.7 mm | 24.0 mm | 21.0 mm |

Interpretation:

- 3D absolute median 只改善约 `2.5 mm`；
- horizontal median 基本没变，`44.8 -> 45.2 mm`；
- vertical median 小幅改善，`48.6 -> 44.3 mm`；
- vertical P95 仍然很大，`147.3 -> 144.5 mm`，说明高度/垂直 tail 没被 filter 根治；
- repeatability 从 `58.7 mm` 降到 `24.0 mm` 或 `21.0 mm`，改善非常大。

### v4-io / all8 / T4

| metric | T4+F0 无滤波 | T4+F4 fixed-lag | T4+F5 RTS offline |
|---|---:|---:|---:|
| absolute median 3D | 69.1 mm | 68.7 mm | 68.3 mm |
| absolute P95 3D | 182.3 mm | 183.0 mm | 182.5 mm |
| absolute RMS 3D | 107.0 mm | 107.9 mm | 107.8 mm |
| horizontal median | 41.3 mm | 41.2 mm | 41.2 mm |
| horizontal P95 | 86.6 mm | 85.5 mm | 85.4 mm |
| vertical median | 55.0 mm | 51.3 mm | 50.9 mm |
| vertical P95 abs. | 180.4 mm | 181.1 mm | 180.7 mm |
| repeat-D3 median | 67.4 mm | 21.4 mm | 18.7 mm |

Interpretation:

- horizontal absolute 基本不变；
- vertical median 小幅改善，但 vertical P95 仍约 `181 mm`，没有本质变化；
- repeatability 从 `67.4 mm` 降到约 `19--21 mm`。

### 生产输出口径的 horizontal/vertical 背景

原 official production `v4-io/all8` 的 per-axis 结构更极端：

| component | value |
|---|---:|
| horizontal 2D median | 43.8 mm |
| horizontal 2D P95 | 82.8 mm |
| vertical median | 63.1 mm |
| vertical P95 abs. | 259.4 mm |

这就是为什么报告必须单独写 horizontal 和 vertical：3D median 看起来是 `77.4 mm`，
但 vertical tail 已经到 `~260 mm`，这才是系统最需要解释的误差结构。

## 6. 为什么 absolute 没有明显变好？

先澄清一个非常重要的逻辑点：

> 只要 `P_autopos_tag_capture` 变了，absolute error 一定会变。

这是对的。因为 absolute error 的定义就是：

```text
e_abs = || P_opti - P_autopos_tag_capture ||
```

所以滤波后如果 capture-level tag position 从
`P_autopos_tag_capture_before` 变成 `P_autopos_tag_capture_after`，那么：

```text
e_abs_before = || P_opti - P_autopos_tag_capture_before ||
e_abs_after  = || P_opti - P_autopos_tag_capture_after  ||
```

除非这个移动刚好落在等误差球面上，否则 `e_abs_after` 一定不同于 `e_abs_before`。

但是这里真正的问题不是 absolute error 会不会变，而是：

> `P_autopos_tag_capture` 的移动方向，是否稳定地朝 `P_opti` 靠近？

答案是否定的。Filter 不知道 `P_opti` 在哪里，它只能降噪，不能自动纠正 systematic bias。

可以把每帧位置写成：

```text
p_t = p_true + systematic_bias + random_noise_t
```

无滤波 capture center 约为：

```text
median(p_t) ≈ p_true + systematic_bias
```

滤波后 capture center 约为：

```text
median(filter(p_t)) ≈ p_true + systematic_bias
```

所以 filter 可以压低 `random_noise_t`，但如果主要 absolute error 是
`systematic_bias`，它不会自动消失。

实际逐点检查也支持这一点：

- `T3: F0 -> F4`，24 个 static 点里 12 个 absolute 变好，12 个变差；
- median capture center movement 约 `4.8 mm`；
- `T4: F0 -> F4`，24 个 static 点里 9 个变好，15 个变差；
- median capture center movement 约 `4.4 mm`。

也就是说，filter 会让 capture center 轻微移动，但这个移动不是稳定朝 OptiTrack truth
方向走。

## 7. Native T5 结果

当前 `T5` 原生滤波 solver 还不是最佳路线。

| solver | median 3D | P95 3D | RMS 3D | repeat-D3 |
|---|---:|---:|---:|---:|
| T5b/T5c | 67.5 mm | 238.4 mm | 122.3 mm | 33.0 mm |
| T5e | 73.6 mm | 228.4 mm | 129.1 mm | 43.9 mm |
| T5a | 144.1 mm | 259.5 mm | 153.5 mm | 43.8 mm |

`T5b/T5c` 的 median 可以接受，但 P95 明显差于 `T3+F4` 和 `T4+F4`。目前不能 claim
T5 优于 external filter。

## 8. 建议写进论文/报告的话

推荐表述：

> Temporal filtering substantially improves deployment smoothness and static
> repeatability, reducing median within-capture 3D scatter from approximately
> 60--70 mm to about 20--24 mm. However, absolute accuracy improves only
> marginally, because the dominant absolute error is calibration/layout-scale
> error rather than zero-mean temporal noise.

中文解释：

> 滤波显著提升部署输出的稳定性，但没有根本改变绝对精度。原因是本数据集的主要
> absolute error 来自 layout/scale/delay 这类系统性误差，而不是每帧零均值随机噪声。

## 9. 不能过度 claim 的点

- 不能说 filter 把 absolute accuracy 提升到厘米级。
- 不能用 `F5` 当实时部署性能，因为它是 full offline smoother。
- 不能说 `T5` 已经优于 `T3/T4 + external filter`。
- 不能用 filtered result 替代 original official no-filter validation。

## 10. 推荐 headline

最稳 headline：

```text
For v4-io/all8, temporal filtering reduces static within-capture repeatability
scatter from 58--67 mm to about 19--24 mm, while absolute median 3D error changes
only marginally: T3 improves from 62.3 mm to 59.8 mm, and deployment-oriented T4
remains around 69 mm. This confirms that the remaining absolute error is dominated
by calibration/layout-scale structure rather than frame-to-frame temporal noise.
```
