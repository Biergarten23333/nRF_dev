# V4-io 1000 Static Robustness Analysis

本目录是独立 robustness 子分析，不修改 `FULL-COMPARE-*` 结果。

## Inputs

- Layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_20260513/FULL-COMPARE-1000/v4-io/layout.json`
- Static captures: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_20260513/Static_Test`
- Anchor sigma: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_20260513/FULL-COMPARE-1000/tables/anchor_sigma.json`
- Solver: V4-io downstream sigma-weighted Huber position solve.
- Monte Carlo repeats for random dropout / keep-k: `500`.
- Parallel workers: `12`.

## Main Findings

- Baseline static median: Z `37.9mm`, 3D `49.2mm`, Z share `62.1%`.
- Worst leave-one-out by Z median: `no_C` with Z `45.5mm`, 3D `56.4mm`.
- Worst leave-one-out by 3D median: `no_B` with 3D `58.4mm`.
- Random keep-6 already shows clear degradation: Z `60.9mm`, 3D `77.1mm`.
- Random keep-4 shows low-redundancy degradation: Z `124.6mm`, 3D `156.3mm`, fail rate `0.0%`.
- 30% independent dropout: Z `83.8mm`, 3D `104.8mm`, fail rate `10.5%`.
- Largest per-anchor residual tail: anchor `E`, abs residual p95 `212.7mm`; leave-one-out indicates `B/C` are more geometrically influential than `E`.

## Interpretation: 4-Anchor Selector Effect

这组 500-repeat Monte Carlo 的核心信息是：`8-anchor infrastructure` 下的 all-available solve 和 `4-anchor` 子集定位不是同一个难度级别。这里的 baseline 不是 “每一帧严格固定 8-anchor 求解”，而是 “在 8-anchor 系统下使用当前这一帧所有可用 anchors”。当前 V4-io layout 在所有当前可用 anchor 参与时，static Z median 是 `37.9mm`，3D median 是 `49.2mm`；但随机只保留 4 个 anchor 后，Z median 上升到 `124.6mm`，3D median 上升到 `156.3mm`。

因此，早期 concept/report 里出现的 `100mm+` 级 Z 误差，很可能有相当一部分来自下游 `4-anchor selector` 的几何冗余不足，而不是单纯说明 AutoPos anchor layout 本身失败。尤其是 Z 方向本来就是最弱观测方向；当定位只使用 4 个 anchor 时，任意一个 anchor 的几何位置、质量或 NLOS 偏差都会被显著放大。

更合理的表述是：

> AutoPos/V4-io 在 8-anchor broadcast offline evaluation 下可以达到约 `38mm` Z repeatability；但当下游定位退化到 4-anchor selection 时，Z repeatability 会退化到约 `125mm`。这说明定位精度不仅取决于 layout，也强烈取决于运行时 anchor redundancy 和 selector geometry。

更严谨地说，上面的 `8-anchor broadcast offline evaluation` 指的是 `all-available solve under an 8-anchor infrastructure`。由于某些 epoch 可能没有完整 8 个有效 ranges，它不应被解释为 strict all-8 positioning。

![Random keep-k anchor robustness](figures/random_keep_k_z_3d.png)

Figure: 随机保留 anchor 数量从 8 降到 4 时，Z 和 3D repeatability 都单调恶化。这个图是当前 README 中最直接的证据：低冗余 selector 会把 Z 弱几何放大到 `100mm+` 量级。

所以这张 robustness 图可以用来解释两个现象：

1. `all-available solve under an 8-anchor infrastructure` 下 V1/V4-io 差距可能不大，因为较高 anchor redundancy 会吸收部分 layout / range bias。
2. `4-anchor online/selector` 下误差会显著变大，因为低冗余让 Z 方向弱几何和单 anchor 偏差直接暴露。

## Connection to Earlier Concept Results

这次结果也帮助重新理解 earlier concept/report 里写的：

> outdoor line-of-sight, offline quality-aware anchor selection (`>=2 upper + >=2 lower`), about 820 static Tag115 fixes per session.

这句话容易被误读成 “固定 8-anchor 求解”。更准确地说，它代表的是：Tag session 里可能尝试获得 8 个 anchor 的 ranging，但后处理定位时按 quality / non-coplanarity 选择可用 subset。也就是说，它更接近 `variable-subset offline selector`，而不是强制 all-8 positioning。

如果当时 SSTWR timing、response availability 或 QF filtering 导致很多 epoch 实际只能用 4-6 个 anchor，那么旧实验中的 `100mm+` 级别误差就完全合理。当前 500-repeat robustness 给了一个直接参照：

| Effective anchor count | Z median | 3D median | Interpretation |
| --- | ---: | ---: | --- |
| keep 8 | 37.9 | 49.2 | highest redundancy in this analysis, bias mostly averaged / absorbed |
| keep 6 | 60.9 | 77.1 | redundancy starts to degrade |
| keep 5 | 83.4 | 100.7 | enters 80-100mm level |
| keep 4 | 124.6 | 156.3 | matches the 100mm+ failure regime |

因此，earlier concept 里的 large-error regime 可以被解释为：`offline quality-aware` 虽然比 online solver 更可控，但它仍然可能在许多 fixes 上退化成 4/5/6-anchor subset。此时 delay / layout bias 不再被 8-anchor redundancy 平均掉，而是在不同 subset 之间表现为不同的 position offset，最终把 repeatability std 拉大。

这也解释了为什么 all-available / high-redundancy results 可以很好，而 variable-subset / 4-anchor results 会差很多：它们并不在评估同一个问题。`all-available` 更像是在测高冗余条件下的 repeatability；`quality-aware selector` 和 `online selector` 更像是在测 deployment 时 anchor 不完整、selector 动态变化条件下的 robustness。

## Per-Anchor Residual Diagnostic

![Per-anchor residual p95](figures/residual_abs_p95_by_anchor.png)

Figure: 每个 anchor 的 absolute residual p95。这里 `E` 的 residual tail 最大，说明它是当前数据里最明显的 high-tail anchor；`H` 的 low-Q rate 很高，但有效 observation 数少，所以它不是 residual p95 最大的那个。

注意：residual tail 和几何重要性不是同一个概念。`E` 的 residual tail 最大，说明它是最可疑的 high-tail anchor；但 leave-one-out 里 `no_E` 并没有造成最大退化，而 `no_B / no_C` 更影响整体 3D/Z repeatability。因此这里不能简单说 “E 坏了”，更准确是：`E` 在 residual diagnostics 中最可疑，`B/C` 在 geometry robustness 中更关键。

| Anchor | N | residual med | residual RMS | abs p95 | low-Q<80 | Huber downweighted | large >100mm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 13806 | -15.8 | 44.1 | 91.1 | 0.0% | 18.6% | 3.3% |
| B | 13808 | -12.5 | 101.7 | 196.9 | 0.0% | 18.3% | 10.6% |
| C | 13804 | 24.2 | 51.0 | 101.7 | 0.0% | 17.0% | 5.1% |
| D | 13802 | -27.2 | 69.5 | 153.6 | 0.0% | 23.5% | 13.0% |
| E | 13801 | 37.8 | 98.6 | 212.7 | 0.0% | 45.3% | 27.2% |
| F | 13811 | -5.3 | 42.7 | 99.2 | 0.0% | 15.7% | 4.9% |
| G | 13800 | -4.5 | 43.5 | 94.1 | 0.0% | 17.5% | 4.2% |
| H | 4479 | 18.6 | 52.5 | 109.1 | 90.6% | 19.9% | 6.5% |

## Leave-One-Anchor-Out

![Leave-one-anchor-out robustness](figures/leave_one_anchor_out_z_3d.png)

Figure: 单独移除一个 anchor 时，整体退化相对温和。这个结果和 keep-k 图一起看很重要：系统不是因为某一个 anchor 离开就崩，而是当可用 anchor 总数降到 5/4 时，Z 几何和 subset bias 才明显放大。

| Condition | solved rate | fail rate | X med | Y med | Z med | 3D med | Z share | D3 p95 | worst capture |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| baseline_all_available | 100.0% | 0.0% | 26.0 | 16.3 | 37.9 | 49.2 | 62.1% | 81.6 | ID08 |
| no_A | 100.0% | 0.0% | 28.8 | 17.3 | 43.9 | 54.4 | 62.4% | 74.5 | ID08 |
| no_B | 100.0% | 0.0% | 30.2 | 22.2 | 45.2 | 58.4 | 58.1% | 78.1 | ID07 |
| no_C | 100.0% | 0.0% | 29.1 | 17.7 | 45.5 | 56.4 | 60.0% | 68.6 | ID08 |
| no_D | 100.0% | 0.0% | 29.0 | 18.9 | 43.4 | 56.4 | 62.3% | 72.8 | ID08 |
| no_E | 100.0% | 0.0% | 26.9 | 16.7 | 41.2 | 51.1 | 61.3% | 80.6 | ID17 |
| no_F | 100.0% | 0.0% | 25.2 | 17.3 | 36.3 | 48.4 | 56.6% | 69.7 | ID08 |
| no_G | 100.0% | 0.0% | 25.7 | 16.1 | 39.4 | 51.1 | 61.7% | 110.5 | ID18 |
| no_H | 100.0% | 0.0% | 25.8 | 16.4 | 37.4 | 49.0 | 61.7% | 73.4 | ID09 |

## Random Keep-K

| Condition | solved rate | fail rate | Z med | 3D med | Z share | D3 p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| random_keep_8 | 100.0% | 0.0% | 37.9 | 49.2 | 62.1% | 81.6 |
| random_keep_7 | 100.0% | 0.0% | 43.6 | 53.2 | 64.5% | 105.6 |
| random_keep_6 | 100.0% | 0.0% | 60.9 | 77.1 | 65.9% | 166.5 |
| random_keep_5 | 100.0% | 0.0% | 83.4 | 100.7 | 68.4% | 225.2 |
| random_keep_4 | 100.0% | 0.0% | 124.6 | 156.3 | 65.3% | 355.3 |

`fail rate` 是 numerical solve fail rate，不是质量合格率。`random_keep_4` 的 `0.0%` fail rate 只说明 solver 数值上仍能给出位置；它不代表定位质量可接受，因为 3D repeatability 已经退化到 `156.3mm` median / `355.3mm` p95。

## Independent Dropout

| Condition | solved rate | fail rate | Z med | 3D med | Z share | D3 p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| dropout_p05 | 100.0% | 0.0% | 46.9 | 59.5 | 64.5% | 115.5 |
| dropout_p10 | 99.8% | 0.2% | 55.7 | 69.4 | 65.5% | 142.5 |
| dropout_p20 | 97.5% | 2.5% | 70.9 | 87.9 | 65.7% | 188.4 |
| dropout_p30 | 89.5% | 10.5% | 83.8 | 104.8 | 65.8% | 225.0 |
| dropout_p40 | 74.7% | 25.3% | 94.9 | 119.0 | 65.6% | 285.3 |

## Candidate Anchor Simulation

这是几何/FIM 仿真，不是实测。它不能证明新 anchor 一定提高 repeatability / absolute accuracy，只能给出候选位置的 Z-observability 方向。新 anchor sigma 假设为 50mm。该仿真假设新 anchor 的 range noise 为无偏 Gaussian，不包含 NLOS、天线方向性、同步误差、安装误差或 BLE/TDMA availability 问题。

这里的 `reduction factor` 是相对当前 anchor geometry 的理论 Z uncertainty 降低倍数；例如 `3.54` 表示 FIM 预测的 median Z uncertainty 大约降低 `3.54x`。如果 factor 小于 `1`，则表示该候选位置在某些点上可能反而变差。

| Candidate | x | y | z | median Z uncertainty reduction factor | p05 Z reduction factor |
| --- | ---: | ---: | ---: | ---: | ---: |
| center_low_level | 1538 | 2292 | 71 | 3.54 | 0.11 |
| center_extra_high | 1538 | 2292 | -2473 | 3.32 | 1.76 |
| center_high_level | 1538 | 2292 | -1673 | 2.15 | 0.23 |
| center_mid_level | 1538 | 2292 | -735 | 1.50 | 0.08 |
| west_mid_extra_high | -800 | 2292 | -2473 | 1.37 | 0.43 |
| north_mid_extra_high | 1538 | 5472 | -2473 | 1.27 | 0.31 |
| east_mid_extra_high | 3967 | 2292 | -2473 | 1.26 | 0.58 |
| south_mid_extra_high | 1538 | -904 | -2473 | 0.93 | 0.38 |
| east_mid_low_level | 3967 | 2292 | 71 | 0.86 | 0.01 |
| ne_corner_extra_high | 3967 | 5472 | -2473 | 0.84 | 0.24 |

## Files

- `tables/per_observation_residuals.csv`: 每条 observation 的 residual / QF / Huber weight。
- `tables/residual_by_anchor.csv`: per-anchor NLOS/low-QF/residual summary。
- `tables/condition_capture_details.csv`: 每个 condition 每个 static capture 的 XYZ 结果。
- `tables/condition_summary_overall.csv`: baseline / leave-one-out / dropout 总表。
- `tables/condition_summary_all_groups.csv`: 同上，并按 location / height / facing 分组。
- `tables/candidate_anchor_simulation.csv`: candidate anchor FIM 几何仿真。
- `figures/`: leave-one-out、keep-k、residual tail 图。
