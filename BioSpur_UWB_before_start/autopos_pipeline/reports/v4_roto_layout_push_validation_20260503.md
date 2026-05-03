# V4 Roto Layout Push Validation - 2026-05-03

## Deployment

Pushed `autopos_pipeline/solve_v4_fusion/anchor_layout_v4_rotoarm_tilted_redo.json` to all three Tags using verified APOS forwarding.

- APOS log: `SS-TWR/alt-SS-TWR/broadcast/logs/apos_verified_v4_rotoarm_tilted_redo_20260503_182240/summary.json`
- Result: `APOS_VERIFY_ALL layout_match=True`
- Source after commit: `SETTINGS`

## Validation Capture

- Capture: `SS-TWR/alt-SS-TWR/broadcast/logs/motion_3tag_v4_roto_layout_60s_20260503_182606/recv_20260503_182607`
- Anchor preflight: ready=8/8
- positions_all: 1800
- Per-tag TS: BSF66F=600, BS2DCE=600, BSDC91=600

## RMS Comparison vs Huber APOS Baseline

Baseline capture: `SS-TWR/alt-SS-TWR/broadcast/logs/motion_3tag_huber125216_apos_20260503_171112/recv_20260503_171113`

| Tag | Huber mean | V4 mean | Huber median | V4 median | Huber p95 | V4 p95 | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| BSF66F | 109.4 | 98.1 | 112.5 | 98.0 | 138 | 156 | median/mean improved, p95 worse |
| BS2DCE | 131.8 | 128.1 | 134.0 | 116.0 | 155 | 244 | median/mean improved, p95 much worse |
| BSDC91 | 167.3 | 133.7 | 171.0 | 126.0 | 195 | 225 | strong median/mean improvement, p95 worse |

## Interpretation

The V4 roto layout improves central RMS for all three Tags, especially BSDC91. This supports the D/H shift as a useful correction rather than pure solver drift.

However, p95 outliers increased, especially for BS2DCE. That means this layout is promising but needs either:

1. output RMS gate re-enabled/tuned, or
2. V4 robust objective tuned further against moving outliers, or
3. a longer validation run to see whether p95 is capture-specific.

## Decision

Keep the V4 roto layout for now. Do not rollback immediately because median and mean RMS improved across all three Tags. Next validation should be a 120s or 300s run plus outlier analysis.
