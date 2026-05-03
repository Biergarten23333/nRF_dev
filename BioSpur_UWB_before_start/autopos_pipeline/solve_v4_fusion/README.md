# AutoPos V4 Fusion

V4 is a high-DOF solver path for fusing:

- inter-anchor sweep ranges
- tag-to-anchor broadcast ranges
- optional per-anchor delay variables

This directory intentionally does not modify V3 solvers. The first implementation is a SciPy least-squares version because `python-gtsam` is not installed in the current environment.

## Phase A

Anchor positions and tag positions are optimized together. Antenna delays are fixed to zero.

```bash
python3 autopos_pipeline/scripts/prepare_v4_data.py \
  --pairs-csv SS-TWR/alt-SS-TWR/broadcast/logs/autopos_v3_anchor_only_100set_prewarm10_20260502_211137/solve_v3_box/pairs_all.csv \
  --tag-capture SS-TWR/alt-SS-TWR/broadcast/logs/pipeline_after_autopos_broadcast_3tag_600s_20260502_193908 \
  --out autopos_pipeline/logs/v4_data_latest.json

python3 autopos_pipeline/solve_v4_fusion/solve_v4.py \
  --phase A \
  --data autopos_pipeline/logs/v4_data_latest.json \
  --init-layout SS-TWR/alt-SS-TWR/broadcast/logs/autopos_v3_anchor_only_100set_prewarm10_20260502_211137/solve_v3_free_variants/free_loose_planes.json \
  --output autopos_pipeline/solve_v4_fusion/anchor_layout_v4_phaseA.json
```

## Current Data Caveat

Current broadcast motion captures mostly contain `TS`, `RXG`, and `CD` lines. Their `cm_all.csv` files are header-only, so they do not currently provide per-anchor tag range observations.

That means V4 can run as an inter-anchor-only high-DOF sanity check today, but it cannot yet perform the intended tag-to-anchor fusion until the capture path exports per-anchor ranges.

