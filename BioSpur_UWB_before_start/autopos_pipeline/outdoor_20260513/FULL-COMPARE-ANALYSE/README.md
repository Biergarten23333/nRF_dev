# AutoPos 2026-05-13 Clean Rebuild Full Analysis

这份报告基于 2026-05-15 重新 clean rebuild 的三套结果：`FULL-COMPARE-1000`、`FULL-COMPARE-500`、`FULL-COMPARE-500+500`。

核心口径：没有 OptiTrack，因此本报告主要讨论 **AutoPos layout self-consistency**、**static repeatability**、**Roto kinematic consistency**，而不是绝对定位 accuracy。

Roto 不再用 raw circle-thickness RMS 当动态定位误差；Roto 主指标改为半径差 `R_outer - R_inner` 是否稳定在 120mm，以及每转一圈拟合圆心的重复性。

## 1. 数据覆盖

- Static: 23 sessions: ID01, ID02, ID03, ID04, ID05, ID06, ID07, ID08, ID09, ID11, ID12, ID13, ID14, ID15, ID16, ID17, ID18, ID19, ID20, ID21, ID22, ID23, ID24
- Roto: 17 sessions: ID25, ID26, ID27, ID28, ID29, ID30, ID31, ID32, ID33, ID34, ID35, ID36, ID37, ID38, ID39, ID40, ID41
- Wand: 5 sessions: W01, W02, W03, W04, W05

`ID10` 没有采集；三套结果中都记录为 `status=missing`，不参与数值统计。

## 2. Solver 信息隔离

所有 solver 使用同一套完整 static / roto / wand capture 做 validation。区别只在 layout generation 或 downstream compensation 阶段，不改变验证数据。

| Version | Algorithm | Extra info |
| --- | --- | --- |
| V1 | simple mean, no delay | none |
| V2 | weighted pair fusion, no delay | none |
| V3-lite | MAD/MVUE robust fusion, no delay | none |
| V3-full | robust fusion + anchor delay | none |
| V4-io | production bounded-delay solver | none |
| V4-io-td | V4-io fixed layout + common Tag delay | Tag delay only |
| V4-io-roto | V4-io + RotoArm soft constraints | Roto only |
| V4-io-wand | V4-io + W01-W04 soft constraints | Wand only |
| V5 | V4-io diagnostics | diagnostics |

`V4-io-roto` 使用了 Roto 信息注入 layout，因此它的 Roto validation 不是 fully independent holdout；其它 sweep-only / inter-anchor versions 的 Roto 是外部验证。

## 3. 一眼结论

| Dataset | AutoPos RMS | AutoPos p95 | Static med | Static p95 | Roto dR RMS | Turn-center med |
| --- | --- | --- | --- | --- | --- | --- |
| FULL-COMPARE-1000 | 44.3 | 87.7 | 49.2 | 81.6 | 32.3 | 20.6 |
| FULL-COMPARE-500 | 44.7 | 89.2 | 48.4 | 80.9 | 33.0 | 20.9 |
| FULL-COMPARE-500+500 | 44.2 | 88.5 | 48.4 | 80.6 | 33.4 | 21.0 |

V4-io 的三套结果非常接近：static median 约 48-49mm，Roto 半径差 RMS 约 32-33mm，每转圆心重复性约 21mm。

## 4. AutoPos Layout Self-Consistency

### FULL-COMPARE-1000 / `solve`

| Version | RMS | p50 | p95 | max |
| --- | --- | --- | --- | --- |
| V1 | 64.2 | 28.3 | 143.9 | 183.1 |
| V2 | 40.4 | 27.1 | 80.4 | 90.4 |
| V3-lite | 40.8 | 27.4 | 82.0 | 91.7 |
| V3-full | 66.4 | 2.0 | 182.6 | 207.9 |
| V4-io | 44.3 | 15.3 | 87.7 | 163.3 |
| V4-io-roto | 57.9 | 22.8 | 134.1 | 146.9 |
| V4-io-wand | 44.2 | 17.5 | 87.6 | 163.8 |

### FULL-COMPARE-500 / `solve`

| Version | RMS | p50 | p95 | max |
| --- | --- | --- | --- | --- |
| V1 | 63.3 | 29.4 | 143.7 | 175.1 |
| V2 | 40.6 | 27.1 | 80.5 | 89.0 |
| V3-lite | 41.0 | 27.2 | 81.8 | 90.1 |
| V3-full | 61.8 | 7.2 | 178.3 | 192.2 |
| V4-io | 44.7 | 15.3 | 89.2 | 165.7 |
| V4-io-roto | 56.9 | 22.7 | 131.8 | 140.8 |
| V4-io-wand | 44.3 | 16.3 | 87.4 | 163.0 |

### FULL-COMPARE-500 / `holdout_last500`

| Version | RMS | p50 | p95 | max |
| --- | --- | --- | --- | --- |
| V1 | 62.0 | 25.4 | 144.0 | 169.0 |
| V2 | 40.6 | 33.5 | 78.7 | 90.5 |
| V3-lite | 41.2 | 34.0 | 79.6 | 92.5 |
| V3-full | 62.2 | 6.4 | 178.5 | 196.6 |
| V4-io | 44.9 | 15.3 | 90.1 | 168.1 |
| V4-io-roto | 58.5 | 24.0 | 133.6 | 146.4 |
| V4-io-wand | 44.8 | 20.9 | 83.4 | 165.4 |

### FULL-COMPARE-500+500 / `all1000`

| Version | RMS | p50 | p95 | max |
| --- | --- | --- | --- | --- |
| V1 | 64.2 | 28.3 | 143.9 | 183.1 |
| V2 | 40.4 | 27.0 | 80.4 | 90.7 |
| V3-lite | 40.8 | 26.9 | 82.0 | 90.7 |
| V3-full | 64.9 | 2.6 | 178.0 | 220.4 |
| V4-io | 44.2 | 14.9 | 88.5 | 162.1 |
| V4-io-roto | 57.5 | 23.5 | 133.7 | 144.4 |
| V4-io-wand | 44.2 | 17.2 | 88.5 | 162.6 |

V2/V3-lite 在 inter-anchor residual 上最干净，说明 pair fusion 很强。V4-io 稍高，但它带有 bounded anchor delay 和更工程化的 robust objective。V4-io-roto 牺牲 inter-anchor residual 来满足 RotoArm soft constraints。

## 5. Static Tag Repeatability

### FULL-COMPARE-1000

| Version | N | D3 med | D3 p95 | D3 max |
| --- | --- | --- | --- | --- |
| V2 | 23 | 48.7 | 71.0 | 77.5 |
| V3-lite | 23 | 48.7 | 70.9 | 77.1 |
| V4-io | 23 | 49.2 | 81.6 | 88.2 |
| V4-io-td | 23 | 48.9 | 82.8 | 89.4 |
| V4-io-roto | 23 | 48.1 | 71.9 | 81.1 |
| V4-io-wand | 23 | 48.6 | 77.3 | 83.5 |

### FULL-COMPARE-500

| Version | N | D3 med | D3 p95 | D3 max |
| --- | --- | --- | --- | --- |
| V2 | 23 | 48.3 | 72.0 | 83.0 |
| V3-lite | 23 | 48.6 | 71.9 | 82.5 |
| V4-io | 23 | 48.4 | 80.9 | 107.9 |
| V4-io-td | 23 | 47.9 | 82.3 | 107.5 |
| V4-io-roto | 23 | 48.2 | 74.5 | 82.8 |
| V4-io-wand | 23 | 48.2 | 79.1 | 95.8 |

### FULL-COMPARE-500+500

| Version | N | D3 med | D3 p95 | D3 max |
| --- | --- | --- | --- | --- |
| V2 | 23 | 48.5 | 72.2 | 83.9 |
| V3-lite | 23 | 48.6 | 72.1 | 83.1 |
| V4-io | 23 | 48.4 | 80.6 | 109.0 |
| V4-io-td | 23 | 48.0 | 81.8 | 108.7 |
| V4-io-roto | 23 | 48.0 | 75.1 | 83.0 |
| V4-io-wand | 23 | 48.1 | 79.3 | 97.1 |

Static 是最接近定位重复性的主指标。当前所有主线版本 median 基本在 48-50mm。V4-io-td 只有小幅改善；V4-io-roto 的 static p95 较好，但它不再是纯 inter-anchor layout。

### Static grouping, FULL-COMPARE-1000

| By | Group | N | D3 med | D3 p95 | max |
| --- | --- | --- | --- | --- | --- |
| location | center | 108 | 48.1 | 67.9 | 78.6 |
| location | edge | 99 | 50.4 | 85.0 | 146.7 |
| height | high | 72 | 43.0 | 77.4 | 83.6 |
| height | low | 63 | 58.6 | 76.0 | 84.7 |
| height | mid | 72 | 49.3 | 88.2 | 146.7 |
| facing | ABEF | 54 | 47.9 | 61.6 | 74.8 |
| facing | ADHE | 45 | 48.7 | 57.6 | 67.9 |
| facing | BCGF | 54 | 50.8 | 67.9 | 78.6 |
| facing | CDHG | 54 | 63.0 | 88.6 | 146.7 |

`CDHG` facing 和 edge 区域尾部较大，说明天线朝向/空间区域仍是主要误差来源之一。

## 6. Roto Kinematic Consistency

Roto 主指标：

- `dR RMS`: `RMS((R_outer - R_inner) - 120mm)`
- `abs dR p95`: `|(R_outer - R_inner) - 120mm|` 的 p95
- `turn med`: 每转一圈拟合圆心后的 3D RMS 中位数

### FULL-COMPARE-1000

| Version | N | dR mean | dR RMS | abs dR med | abs dR p95 | turn med | turn p95 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| V4-io | 17 | -24.3 | 32.3 | 23.5 | 50.5 | 20.6 | 28.9 |
| V4-io-td | 17 | -23.5 | 31.6 | 22.8 | 49.1 | 20.0 | 28.3 |
| V4-io-roto | 17 | -21.4 | 29.9 | 24.9 | 45.4 | 18.0 | 25.9 |
| V4-io-wand | 17 | -23.2 | 31.7 | 21.4 | 49.8 | 19.1 | 28.6 |
| V5 | 17 | -24.3 | 32.3 | 23.5 | 50.5 | 20.6 | 28.9 |

### FULL-COMPARE-500

| Version | N | dR mean | dR RMS | abs dR med | abs dR p95 | turn med | turn p95 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| V4-io | 17 | -24.8 | 33.0 | 24.3 | 51.4 | 20.9 | 30.5 |
| V4-io-td | 17 | -23.8 | 32.2 | 23.5 | 49.7 | 20.4 | 29.1 |
| V4-io-roto | 17 | -22.0 | 30.4 | 26.3 | 46.0 | 18.0 | 27.5 |
| V4-io-wand | 17 | -23.8 | 32.4 | 22.0 | 50.5 | 19.5 | 29.9 |
| V5 | 17 | -24.8 | 33.0 | 24.3 | 51.4 | 20.9 | 30.5 |

### FULL-COMPARE-500+500

| Version | N | dR mean | dR RMS | abs dR med | abs dR p95 | turn med | turn p95 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| V4-io | 17 | -25.3 | 33.4 | 25.0 | 52.1 | 21.0 | 30.9 |
| V4-io-td | 17 | -24.3 | 32.6 | 24.2 | 50.6 | 20.7 | 29.4 |
| V4-io-roto | 17 | -22.1 | 30.5 | 26.5 | 46.0 | 18.1 | 27.5 |
| V4-io-wand | 17 | -24.2 | 32.7 | 22.5 | 51.2 | 19.5 | 30.2 |
| V5 | 17 | -25.3 | 33.4 | 25.0 | 52.1 | 21.0 | 30.9 |

正式结果中 V4-io 的 Roto dR RMS 约 32-33mm，abs dR p95 约 50-52mm，turn-center median 约 21mm。`V4-io-roto` 的 Roto 指标较好，但因其注入了 RotoArm 信息，主要用于证明该约束和数据相容，而不是独立 holdout。

## 7. Wand Constraint Ablation

| Dataset | Version | AutoPos RMS | Static med | Static p95 | Roto dR RMS |
| --- | --- | --- | --- | --- | --- |
| FULL-COMPARE-1000 | V4-io | 44.3 | 49.2 | 81.6 | 32.3 |
| FULL-COMPARE-1000 | V4-io-wand | 44.2 | 48.6 | 77.3 | 31.7 |
| FULL-COMPARE-500 | V4-io | 44.7 | 48.4 | 80.9 | 33.0 |
| FULL-COMPARE-500 | V4-io-wand | 44.3 | 48.2 | 79.1 | 32.4 |
| FULL-COMPARE-500+500 | V4-io | 44.2 | 48.4 | 80.6 | 33.4 |
| FULL-COMPARE-500+500 | V4-io-wand | 44.2 | 48.1 | 79.3 | 32.7 |

Wand 约束带来小幅改善，但不构成主贡献。W01-W04 可用于静态刚体约束；W05 只适合 coverage/diagnostic。

## 8. Wand-as-Tag Relative Geometry

这里把 Wand 当作三颗普通 Tag 来验证：先分别解出 `BSCCF4`、`BS9336`、`BS955A` 的位置，再计算三颗 Tag 之间的距离。这个分析回答的是：

- Wand 三颗 Tag 解出来以后，内部相对距离是否接近卷尺 GT？
- W01-W04 四个静态姿态下，三角形边长是否稳定？
- 这不是 W05 动态刚体分析；W05 因为 TDMA 三颗 Tag 不同一时刻，不能直接当逐帧刚体约束。

GT 边长：

| Pair | GT |
| --- | ---: |
| BSCCF4-BS9336 | 670.0 |
| BSCCF4-BS955A | 659.7 |
| BS9336-BS955A | 708.7 |

V4-io 下 W01-W04 的 Wand-as-Tag 边长验证：

| Dataset | ok edges | bias mean | abs bias med | bias RMS | abs bias max |
| --- | ---: | ---: | ---: | ---: | ---: |
| FULL-COMPARE-1000 | 10 | -3.5 | 59.4 | 59.3 | 89.2 |
| FULL-COMPARE-500 | 10 | -4.9 | 58.4 | 58.2 | 88.5 |
| FULL-COMPARE-500+500 | 10 | -2.2 | 57.4 | 58.3 | 90.0 |

FULL-COMPARE-1000 / V4-io 明细：

| Capture | Pair | GT | measured median | bias |
| --- | --- | ---: | ---: | ---: |
| W01 | BSCCF4-BS9336 | 670.0 | 610.6 | -59.4 |
| W01 | BSCCF4-BS955A | 659.7 | 587.1 | -72.6 |
| W01 | BS9336-BS955A | 708.7 | 797.9 | 89.2 |
| W02 | BSCCF4-BS9336 | 670.0 | 671.5 | 1.5 |
| W02 | BSCCF4-BS955A | 659.7 | 711.8 | 52.1 |
| W02 | BS9336-BS955A | 708.7 | 661.8 | -46.9 |
| W03 | BSCCF4-BS955A | 659.7 | 574.6 | -85.1 |
| W04 | BSCCF4-BS9336 | 670.0 | 692.3 | 22.3 |
| W04 | BSCCF4-BS955A | 659.7 | 734.5 | 74.8 |
| W04 | BS9336-BS955A | 708.7 | 697.9 | -10.8 |

解读：Wand-as-Tag 的相对几何 bias RMS 约 58-59mm，和 static Tag repeatability 的 48-50mm 是同一量级但更严格，因为它要求三颗独立解算的 Tag 在相对距离上同时一致。W03 缺少部分 peer，因此只有一条边可用。这个结果说明 W01-W04 作为刚体约束是有信息量的，但数据本身并不干净；因此 `V4-io-wand` 只能作为 ablation / soft constraint，而不是主线精度证明。

## 9. Common Tag Delay Scan

| Dataset | Version | Tag delay | Static med | Static p95 | Roto dR RMS |
| --- | --- | --- | --- | --- | --- |
| FULL-COMPARE-1000 | V4-io | 0.0 | 49.2 | 81.6 | 32.3 |
| FULL-COMPARE-1000 | V4-io-td | 3.0 | 48.9 | 82.8 | 31.6 |
| FULL-COMPARE-500 | V4-io | 0.0 | 48.4 | 80.9 | 33.0 |
| FULL-COMPARE-500 | V4-io-td | 4.0 | 47.9 | 82.3 | 32.2 |
| FULL-COMPARE-500+500 | V4-io | 0.0 | 48.4 | 80.6 | 33.4 |
| FULL-COMPARE-500+500 | V4-io-td | 3.5 | 48.0 | 81.8 | 32.6 |

Common Tag delay 估计约 3-4mm，收益很小。当前数据下它不是主导误差源；如果后续要做 Tag delay，应该走 factory/type calibration。

## 10. 500 / 1000 / 500+500

| Dataset | Version | AutoPos RMS | Static med | Roto dR RMS |
| --- | --- | --- | --- | --- |
| FULL-COMPARE-1000 | V2 | 40.4 | 48.7 | 32.7 |
| FULL-COMPARE-1000 | V3-lite | 40.8 | 48.7 | 32.7 |
| FULL-COMPARE-1000 | V4-io | 44.3 | 49.2 | 32.3 |
| FULL-COMPARE-1000 | V4-io-roto | 57.9 | 48.1 | 29.9 |
| FULL-COMPARE-1000 | V4-io-wand | 44.2 | 48.6 | 31.7 |
| FULL-COMPARE-500 | V2 | 40.6 | 48.3 | 33.0 |
| FULL-COMPARE-500 | V3-lite | 41.0 | 48.6 | 33.0 |
| FULL-COMPARE-500 | V4-io | 44.7 | 48.4 | 33.0 |
| FULL-COMPARE-500 | V4-io-roto | 56.9 | 48.2 | 30.4 |
| FULL-COMPARE-500 | V4-io-wand | 44.3 | 48.2 | 32.4 |
| FULL-COMPARE-500+500 | V2 | 40.4 | 48.5 | 33.4 |
| FULL-COMPARE-500+500 | V3-lite | 40.8 | 48.6 | 33.4 |
| FULL-COMPARE-500+500 | V4-io | 44.2 | 48.4 | 33.4 |
| FULL-COMPARE-500+500 | V4-io-roto | 57.5 | 48.0 | 30.5 |
| FULL-COMPARE-500+500 | V4-io-wand | 44.2 | 48.1 | 32.7 |

500-only、1000、500+500 三套结果整体接近，说明从 500 到 1000 sweep set 的增益有限；split-consensus 主要作为 no-OptiTrack 条件下的稳定性检查。

## 11. 可直接讲给教授的结论

1. AutoPos 在无 OptiTrack 条件下，应报告 repeatability 和 self-consistency，不应宣称绝对 accuracy。
2. Static Tag repeatability median 约 48-50mm，是当前最直接的定位重复性指标。
3. Roto 作为运动学一致性：V4-io 下 dR RMS 约 32-33mm，turn-center median 约 21mm。不要把 circle-thickness diagnostic 写成 dynamic positioning error。
4. V2/V3-lite 的 inter-anchor fit 很好，V4-io 的价值在于 delay-aware 和 production robustness，不一定在每个 residual 数字上最低。
5. Wand-as-Tag 显示 W01-W04 三 Tag 相对距离 bias RMS 约 58-59mm；Wand 约束有信息量，但数据不够干净，因此只作为 soft constraint ablation。
6. V4-io-roto 和 V4-io-wand 是单独信息源注入实验，不和 TD 混合。RotoArm 有改善 Roto consistency / static tail 的迹象，但不是 fully independent holdout。

## 12. Recommended Wording

中文：

> 本实验没有使用 OptiTrack，因此定位性能以重复性与自洽性为主进行评估。AutoPos layout 先通过 inter-anchor self-consistency、holdout 和 split stability 检验，再用完整采集的 static / roto / wand 数据做下游验证。Static Tag 的 3D repeatability median 约为 48-50mm。Roto 数据不作为绝对动态定位误差，而作为运动学一致性验证；两个 Roto tag 的已知半径差 120mm 在 V4-io 下达到约 32-33mm RMS 的一致性，每转圆心重复性约 21mm。Wand W01-W04 作为三 Tag 相对几何验证，边长 bias RMS 约 58-59mm，因此更适合作为 soft constraint / diagnostic，而不是主线 accuracy 证明。

English：

> Since no OptiTrack ground truth is available, we evaluate AutoPos through repeatability and self-consistency rather than absolute accuracy. The anchor layout is first checked by inter-anchor self-consistency, holdout, and split-layout stability, and then validated on the complete static, roto, and wand captures. Static tag repeatability is around 48-50 mm median 3D std. The roto data are reported as kinematic consistency rather than absolute dynamic positioning accuracy; the known 120 mm radius difference between the two roto tags is preserved with about 32-33 mm RMS error under V4-io, and per-revolution center repeatability is around 21 mm. The W01-W04 wand captures provide a three-tag relative-geometry check, with pairwise-distance bias RMS around 58-59 mm, so they are best treated as a soft constraint / diagnostic rather than the primary accuracy claim.

## 13. Key Files

- `FULL-COMPARE-1000/tables/version_summary.csv`
- `FULL-COMPARE-1000/tables/autopos_quality_summary.csv`
- `FULL-COMPARE-1000/tables/static_all_captures.csv`
- `FULL-COMPARE-1000/tables/roto_physical_consistency_summary.csv`
- `FULL-COMPARE-1000/figures/roto_deltaR_distribution.png`
- `FULL-COMPARE-1000/figures/roto_turn_center_rms_distribution.png`
- `FULL-COMPARE-500/tables/version_summary.csv`
- `FULL-COMPARE-500/tables/autopos_quality_summary.csv`
- `FULL-COMPARE-500/tables/static_all_captures.csv`
- `FULL-COMPARE-500/tables/roto_physical_consistency_summary.csv`
- `FULL-COMPARE-500/figures/roto_deltaR_distribution.png`
- `FULL-COMPARE-500/figures/roto_turn_center_rms_distribution.png`
- `FULL-COMPARE-500+500/tables/version_summary.csv`
- `FULL-COMPARE-500+500/tables/autopos_quality_summary.csv`
- `FULL-COMPARE-500+500/tables/static_all_captures.csv`
- `FULL-COMPARE-500+500/tables/roto_physical_consistency_summary.csv`
- `FULL-COMPARE-500+500/figures/roto_deltaR_distribution.png`
- `FULL-COMPARE-500+500/figures/roto_turn_center_rms_distribution.png`
