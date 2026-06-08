# 这套系统的 Surviving-4 Axis-DOP 大评估

生成时间: `2026-06-07T20:55:02.252895+00:00`

## 结论先说

- 这不是通用 UWB 结论；这是针对当前 `A-H` 编号、当前 anchor 坐标、当前 117 个 layout 的评估。
- 全 8 anchor 的 DOP 不能代表冗余能力；真正的问题在 `drop4`，也就是只剩 4 个 anchor 的最坏几何。
- `drop4` score 中位数 `18.50`，p90 `20.63`，p95 `22.55`，最大 `24.10`。
- 相对 all8 的恶化倍率中位数 `16.99x`，p95 `19.80x`，最大 `21.06x`。
- 风险分布: critical=34, high=55, medium=20, acceptable=8。

## 系统性问题

1. `drop4` 主要打爆 Z/高度方向。绝对最差轴统计: Z=105, Y=12。

2. 从相对 all8 的恶化倍率看，最常被放大的轴是: Z=98, Y=12, X=7。

3. 最危险的 drop4 不是随机分散，而是集中在少数几组:

- `dropABGH`: 45/117
- `dropCDEF`: 26/117
- `dropADFG`: 22/117
- `dropBCEH`: 12/117
- `dropBDFH`: 7/117
- `dropACEG`: 5/117

4. 换成 surviving anchors 看，本质是某些 4-anchor 子集几何不够立体或水平覆盖不均衡:

- 剩 `CDEF`: 45/117
- 剩 `ABGH`: 26/117
- 剩 `BCEH`: 22/117
- 剩 `ADFG`: 12/117
- 剩 `ACEG`: 7/117
- 剩 `BDFH`: 5/117

## 最脆弱 Layout

| Capture | Version | Variant | Drop | Survive | Score | Ratio | Worst Axis | X p95 | Y p95 | Z p95 | XY area keep/all | Z span keep/all |
|---|---|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|
| `Garage_test_nah_2` | `v4-io` | `default` | `dropCDEF` | `ABGH` | 24.10 | 21.06x | `Z` | 0.79 | 15.48 | 30.45 | 0.97 | 0.99 |
| `Outdoor_LOS` | `v3-full` | `default` | `dropCDEF` | `ABGH` | 23.17 | 19.60x | `Z` | 1.10 | 10.92 | 32.52 | 0.98 | 0.99 |
| `outdoor_20260513` | `v4-io-roto` | `default` | `dropADFG` | `BCEH` | 22.81 | 19.87x | `Z` | 14.48 | 0.85 | 28.87 | 0.96 | 0.91 |
| `outdoor_20260513` | `v4-io-roto` | `first500` | `dropADFG` | `BCEH` | 22.81 | 19.87x | `Z` | 14.48 | 0.85 | 28.87 | 0.96 | 0.91 |
| `outdoor_20260513` | `v4-io-roto` | `consensus` | `dropADFG` | `BCEH` | 22.72 | 19.78x | `Z` | 14.38 | 0.85 | 28.79 | 0.96 | 0.91 |
| `outdoor_20260513` | `v4-io-roto` | `default` | `dropADFG` | `BCEH` | 22.65 | 19.71x | `Z` | 14.41 | 0.89 | 28.62 | 0.96 | 0.92 |
| `outdoor_20260513` | `v4-io-roto` | `last500_aligned` | `dropADFG` | `BCEH` | 22.53 | 19.60x | `Z` | 14.28 | 0.84 | 28.51 | 0.96 | 0.92 |
| `outdoor_20260513` | `v4-io-roto` | `us_height` | `dropADFG` | `BCEH` | 22.35 | 19.44x | `Z` | 15.20 | 1.11 | 27.34 | 0.94 | 1.00 |
| `Outdoor_LOS` | `v4-io` | `default` | `dropBCEH` | `ADFG` | 21.24 | 17.90x | `Z` | 12.94 | 1.00 | 27.21 | 0.99 | 0.99 |
| `outdoor_20260513` | `v3-full` | `last500_aligned` | `dropADFG` | `BCEH` | 21.18 | 18.60x | `Z` | 13.99 | 1.15 | 26.18 | 0.96 | 0.93 |
| `Outdoor_LOS` | `v3-full` | `us_height` | `dropCDEF` | `ABGH` | 20.98 | 17.77x | `Z` | 1.07 | 8.46 | 30.40 | 0.98 | 0.77 |
| `Outdoor_LOS` | `v4-io` | `us_height` | `dropBCEH` | `ADFG` | 20.81 | 17.54x | `Z` | 11.68 | 1.59 | 27.45 | 0.98 | 0.85 |
| `outdoor_v4_20260504` | `v3-lite` | `default` | `dropCDEF` | `ABGH` | 20.50 | 18.79x | `Z` | 0.79 | 12.83 | 25.92 | 0.97 | 0.77 |
| `outdoor_20260513` | `v4-io-td` | `last500_aligned` | `dropABGH` | `CDEF` | 20.48 | 17.83x | `Z` | 1.10 | 9.19 | 28.90 | 0.93 | 0.87 |
| `outdoor_20260513` | `v4-io` | `last500_aligned` | `dropABGH` | `CDEF` | 20.48 | 17.83x | `Z` | 1.10 | 9.19 | 28.90 | 0.93 | 0.87 |
| `outdoor_20260513` | `v5` | `last500_aligned` | `dropABGH` | `CDEF` | 20.48 | 17.83x | `Z` | 1.10 | 9.19 | 28.90 | 0.93 | 0.87 |
| `outdoor_v4_20260504` | `v2` | `default` | `dropCDEF` | `ABGH` | 20.43 | 18.70x | `Z` | 0.79 | 12.75 | 25.85 | 0.97 | 0.77 |
| `outdoor_20260513` | `v4` | `default` | `dropABGH` | `CDEF` | 20.41 | 17.76x | `Z` | 1.10 | 9.13 | 28.81 | 0.93 | 0.87 |
| `outdoor_20260513` | `v4-io` | `default` | `dropABGH` | `CDEF` | 20.41 | 17.76x | `Z` | 1.10 | 9.13 | 28.81 | 0.93 | 0.87 |
| `outdoor_20260513` | `v4-io-td` | `default` | `dropABGH` | `CDEF` | 20.41 | 17.76x | `Z` | 1.10 | 9.13 | 28.81 | 0.93 | 0.87 |

## 每个数据组建议优先候选

| Group | 推荐 Version | Variant | Worst Drop4 | Survive | Score | Worst Axis | 最差 Version | 最差 Score |
|---|---|---|---|---|---:|---|---|---:|
| `28052026_Erlangen_Official/solver/outputs/v1_to_v4_io_field_check` | `v3-full` | `default` | `dropCDEF` | `ABGH` | 16.22 | `Z` | `v4-io:default` | 17.98 |
| `28052026_Erlangen_Smoke/solver/outputs/v1_to_v4_io_field_check` | `v3-full` | `default` | `dropBCEH` | `ADFG` | 14.47 | `Z` | `v4-io:default` | 17.80 |
| `Garage_Test/solver/outputs/v1_to_v4_io_field_check` | `v3-lite` | `default` | `dropABGH` | `CDEF` | 11.57 | `Z` | `v3-full:default` | 13.79 |
| `Garage_test_2/solver/outputs/v1_to_v4_io_field_check` | `v1-old` | `default` | `dropABGH` | `CDEF` | 11.22 | `Z` | `v4-io:default` | 13.77 |
| `Garage_test_nah_2/solver/outputs/v1_to_v4_io_field_check` | `v3-full` | `default` | `dropBDFH` | `ACEG` | 12.36 | `Y` | `v4-io:default` | 24.10 |
| `Outdoor_LOS/solver/outputs/v1_to_v4_io_field_check` | `v1-old` | `us_height` | `dropCDEF` | `ABGH` | 13.17 | `Z` | `v3-full:default` | 23.17 |
| `Outdoor_LOS_2/solver/outputs/v1_to_v4_io_field_check` | `v2` | `us_height` | `dropCDEF` | `ABGH` | 15.91 | `Z` | `v4-io:default` | 19.12 |
| `Outdoor_LOS_3/solver/outputs/v1_to_v4_io_field_check` | `v3-lite` | `default` | `dropCDEF` | `ABGH` | 16.77 | `Z` | `v4-io:default` | 19.15 |
| `outdoor_20260513/FULL-COMPARE` | `v1` | `default` | `dropCDEF` | `ABGH` | 17.88 | `Z` | `v4:default` | 20.41 |
| `outdoor_20260513/FULL-COMPARE-1000` | `v1-old` | `default` | `dropCDEF` | `ABGH` | 17.88 | `Z` | `v4-io-roto:default` | 22.65 |
| `outdoor_20260513/FULL-COMPARE-500` | `v1-old` | `default` | `dropCDEF` | `ABGH` | 17.13 | `Z` | `v4-io-roto:default` | 22.81 |
| `outdoor_20260513/FULL-COMPARE-500+500` | `v1-old` | `first500` | `dropCDEF` | `ABGH` | 17.13 | `Z` | `v4-io-roto:first500` | 22.81 |
| `outdoor_20260513/reports/us_height_alignment_from_fgh_20260523/FULL-COMPARE-1000` | `v1-old` | `us_height` | `dropCDEF` | `ABGH` | 17.21 | `Z` | `v4-io-roto:us_height` | 22.35 |
| `outdoor_v4_20260504/FULL-COMPARE` | `v1` | `default` | `dropABGH` | `CDEF` | 17.83 | `Z` | `v3-lite:default` | 20.50 |

## 针对这套系统的优化方向

1. 先按 `drop4_score` 选型，不要按 all8 score 选型。all8 只是正常状态，drop4 才暴露冗余。
2. 把 `{ABGH}`, `{CDEF}`, `{ADFG}`, `{BCEH}` 当成当前编号体系下的高风险 4-anchor 子集来审查。
3. 重新编号或重新布点时，要让任何连续/同侧/同高度倾向的 4 个 anchor 不会同时构成唯一 surviving 子集。编号应该跨高度、跨对角、跨场地边界交错。
4. 如果硬件位置可改，优先增加 surviving-4 的 Z 方向可观测性: 上下层高度差要保留，不能让 surviving 4 几乎都落在同一高度结构或同一倾斜平面。
5. 对 outdoor_20260513 的 `v4-io-roto` 和部分 `v4-io/v5`，不要只因为实测误差看起来好就直接部署；它们在 surviving-4 下有明显冗余风险。
6. 对 Garage/Garage_test_nah 的 `v4-io` 类布局，要重点检查 `dropCDEF` 后剩余 `ABGH` 的几何；这是当前最严重的崩溃模式。

## 使用口径

- `critical`: drop4 score >= 20，说明只剩 4 anchor 时几何已经严重退化。
- `high`: 16-20，能比较但不建议作为高可靠冗余。
- `medium`: 12-16，需要结合实测误差和应用容忍度。
- `acceptable`: <12，只代表当前采样空间内较稳，不等于所有环境都安全。
