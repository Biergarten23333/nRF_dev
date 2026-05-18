# V1 vs V4-io: Static 4-Anchor Selector Probe

这个临时实验用于验证：all-anchor 评估可能掩盖 layout bias，而 4-anchor dynamic-style selector 会暴露 V1 与 V4-io 的差异。

## Inputs

- Static captures: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_20260513/Static_Test`
- V1 layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_20260513/FULL-COMPARE-1000/v1-old/layout.json`
- V4-io layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_20260513/FULL-COMPARE-1000/v4-io/layout.json`
- 每帧 selector: exactly 2 lower anchors + 2 upper anchors, non-coplanar.
- Selector scoring: mean quality first, tetrahedron volume second.
- 同一批 static captures、同一套 selector 规则分别跑 V1 与 V4-io。

## Compact Result

| Version | captures | 4-anchor median 3D std | 4-anchor p95 | 4-anchor max | all-anchor median 3D std | all-anchor p95 | 4-anchor median Z std | all-anchor median Z std |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| v1-old | 23 | 67.4 | 85.3 | 91.0 | 42.7 | 64.2 | 50.4 | 32.2 |
| v4-io | 23 | 68.7 | 89.0 | 109.3 | 43.4 | 60.3 | 56.4 | 33.9 |

## Main Takeaway

- 4-anchor median 3D std improvement from V1 to V4-io: `-2.0%`.
- 4-anchor median Z std improvement from V1 to V4-io: `-11.9%`.

本次简化 selector 下，V4-io 没有显著优于 V1：all-anchor 仍接近，4-anchor 下二者也接近，且 V4-io 的 Z median 略高。
这说明 20260513 broadcast static 数据里，delay-aware layout 的优势不能简单通过这个离线 4-anchor selector 复现。
如果要复现 concept PDF 的 130mm 机制，需要进一步使用当时 real-time on-tag selector/solver 逻辑，或把旧数据同样跑进这个对比框架。

## Files

- `summary_compact.csv`: 最核心的 V1 vs V4-io 汇总。
- `summary_by_capture.csv`: 每个 static capture 的详细对比。
- `per_frame_4anchor_selection.csv`: 每帧选锚和解算细节。
