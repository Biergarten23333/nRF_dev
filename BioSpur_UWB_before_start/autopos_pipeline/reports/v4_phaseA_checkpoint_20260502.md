# AutoPos V4 Phase A Checkpoint - 2026-05-02

Implemented new V4 pipeline files:

- `autopos_pipeline/scripts/prepare_v4_data.py`
- `autopos_pipeline/solve_v4_fusion/solve_v4.py`
- `autopos_pipeline/solve_v4_fusion/README.md`

This is a SciPy least-squares implementation because `python-gtsam` is not installed in the current environment.

## Data Prepared

Command output for the current inter-anchor sweep plus broadcast captures:

```text
inter_anchor_ranges: 28
tag_anchor_ranges: 0
tag_position_initializers: 5000
```

Important blocker: current broadcast motion captures have header-only `cm_all.csv` files and raw logs contain `TS/RXG/CD`, but not per-anchor `range_mm` rows. Therefore the intended V4 tag-to-anchor fusion cannot run yet with existing logs.

## Phase A Dry Run

Ran `solve_v4.py --phase A` with the current data. Since `tag_anchor_ranges=0`, this is an inter-anchor-only high-DOF sanity solve, not real V4 fusion.

Output:

- `autopos_pipeline/logs/v4_data_20260502_latest.json`
- `autopos_pipeline/solve_v4_fusion/anchor_layout_v4_phaseA_20260502.json`

Result:

```text
inter_anchor_rms_mm = 91.95
tag_anchor_count = 0
```

This confirms the solver works, but also confirms we need per-anchor tag range export before Phase A can answer the real question.

## Required Next Firmware/Host Data Export

For each broadcast sweep, export one row per anchor response, for example into `cm_all.csv` or a new `tr_all.csv`:

```text
host_epoch_s,peer_name,sweep,tag_id,anchor_id,range_mm,quality_percent,ci,status
```

Once those rows exist, rerun:

```bash
python3 autopos_pipeline/scripts/prepare_v4_data.py \
  --pairs-csv <pairs_all.csv> \
  --tag-capture <broadcast_capture_dir> \
  --out autopos_pipeline/logs/v4_data_<timestamp>.json

python3 autopos_pipeline/solve_v4_fusion/solve_v4.py \
  --phase A \
  --data autopos_pipeline/logs/v4_data_<timestamp>.json \
  --init-layout <free_loose_planes_or_current_best.json> \
  --output autopos_pipeline/solve_v4_fusion/anchor_layout_v4_phaseA_<timestamp>.json
```
