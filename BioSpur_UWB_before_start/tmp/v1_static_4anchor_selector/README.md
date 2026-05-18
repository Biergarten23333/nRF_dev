# V1 4-Anchor Selector Static Probe

这个临时实验只读数据，不修改 `FULL-COMPARE-*` 的任何输出。

## Inputs

- Layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_20260513/FULL-COMPARE-1000/v1-old/layout.json`
- Static root: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_20260513/Static_Test`
- Solver: V1 layout, per-frame nonlinear least squares.
- 4-anchor selection: every frame choose exactly 2 lower anchors from A-D and 2 upper anchors from E-H.
- Selection score: highest mean quality, then largest tetrahedron volume.
- All-anchor comparison: same frame, use all valid anchors when available.

## Overall Result

- Captures evaluated: `23`
- 4-anchor median 3D std: `67.4 mm`
- 4-anchor p95 3D std across captures: `85.3 mm`
- 4-anchor max 3D std across captures: `91.0 mm`
- all-anchor median 3D std: `42.7 mm`
- all-anchor p95 3D std across captures: `64.2 mm`
- 4-anchor median Z std: `50.4 mm`
- all-anchor median Z std: `32.2 mm`

## Interpretation

这个结果就是 concept PDF 那条线的机制复现：同一个 V1 layout，在 full/all-anchor 冗余条件下看起来还行，
但一旦每帧只用动态 2+2 的 4-anchor 子集，不同子集的系统偏差会变成位置云的扩散，尤其容易放大 Z。

## Files

- `summary_by_capture.csv`: 每个 static capture 的 4-anchor vs all-anchor 对照。
- `per_frame_4anchor_selection.csv`: 每帧选中的 4-anchor subset 和解算位置。
- `ID13_per_frame_4anchor_selection.csv`: ID13 单独逐帧明细，方便快速看 selector 行为。

## Per-Capture Summary

| ID | N4 | 4-anchor 3D std | 4-anchor Z std | all-anchor 3D std | all-anchor Z std | top selected subsets |
|---|---:|---:|---:|---:|---:|---|
| ID01 | 601 | 81.6 | 44.9 | 45.0 | 36.8 | BDEG:503;ACFG:50;ADEF:33;CDEG:15 |
| ID02 | 601 | 72.6 | 63.0 | 38.3 | 31.7 | BDEG:601 |
| ID03 | 600 | 45.4 | 29.8 | 36.9 | 23.3 | BDEG:600 |
| ID04 | 601 | 91.0 | 80.5 | 58.9 | 50.8 | BDEG:557;ADEF:32;CDEG:6;BDEF:6 |
| ID05 | 601 | 62.0 | 47.9 | 48.3 | 37.3 | BDEG:601 |
| ID06 | 600 | 41.4 | 30.1 | 30.2 | 19.5 | BDEG:593;ADEF:7 |
| ID07 | 600 | 80.9 | 68.3 | 54.6 | 48.0 | BDEG:584;ADEF:16 |
| ID08 | 601 | 67.4 | 49.5 | 42.4 | 30.2 | BDEG:596;ACFG:3;ADEF:2 |
| ID09 | 601 | 48.7 | 37.1 | 36.3 | 26.9 | BDEG:601 |
| ID11 | 601 | 60.4 | 49.7 | 45.0 | 37.8 | BDEG:549;CDEG:30;ACFG:22 |
| ID12 | 601 | 49.7 | 37.4 | 38.9 | 26.4 | BDEG:601 |
| ID13 | 601 | 74.3 | 62.9 | 50.8 | 43.0 | BDEG:590;ACFG:11 |
| ID14 | 600 | 70.5 | 53.6 | 40.4 | 32.2 | BDEG:565;ADEF:23;CDEG:12 |
| ID15 | 600 | 61.2 | 50.4 | 43.6 | 34.9 | BDEG:563;ACFG:37 |
| ID16 | 601 | 72.7 | 52.7 | 41.5 | 31.5 | BDEG:541;ACFG:37;ADEF:23 |
| ID17 | 601 | 85.7 | 67.7 | 68.4 | 59.3 | BDEG:566;ACFG:23;ADEF:12 |
| ID18 | 601 | 72.8 | 56.3 | 53.1 | 45.3 | BDEG:578;ACFG:23 |
| ID19 | 601 | 71.2 | 57.7 | 64.6 | 55.7 | BDEG:601 |
| ID20 | 601 | 71.3 | 58.2 | 60.0 | 53.0 | BDEG:593;ACFG:8 |
| ID21 | 600 | 54.8 | 40.7 | 42.3 | 31.9 | BDEG:600 |
| ID22 | 601 | 56.7 | 40.8 | 38.1 | 24.0 | BDEG:588;ACFG:13 |
| ID23 | 601 | 56.0 | 39.8 | 42.7 | 29.1 | BDEG:598;CDEG:2;ACFG:1 |
| ID24 | 601 | 66.5 | 56.7 | 36.7 | 25.0 | BDEG:542;ACFG:39;ADEF:20 |
