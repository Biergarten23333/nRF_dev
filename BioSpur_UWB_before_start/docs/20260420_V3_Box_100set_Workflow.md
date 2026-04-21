# 20260420 V3-Box 100-Set Workflow

## Goal

One-command workflow for:

1. `100 set sweep`
2. `pairs_all.csv`
3. `V3-box solver`
4. `CM115 floating reference injection`
5. `result analysis`
6. `layout png`

## One-Command Workflow

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

scripts/run_v3_box_100set_workflow.sh
```

Default environment used by the script:

- `PORT=/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00`
- `ORDER=ABCDEFGH`
- `SW_SETS=100`
- `TIMEOUT_S=7200`
- `QUIET_TAG_NAME=auto`
- `REF_SESSION=logs/tag115_cm_fresh_20260416_154100`
- `REF_MIN_CM_LINES=80`

## Override Example

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

OUT_DIR=logs/v3_box_100set_custom_$(date +%Y%m%d_%H%M%S) \
REF_SESSION=logs/tag115_cm_fresh_20260416_154100 \
REF_MIN_CM_LINES=80 \
PORT=/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00 \
scripts/run_v3_box_100set_workflow.sh
```

## Internal Steps

The workflow script runs:

1. `python3 scripts/run_autopos_sweep_and_solve_v3_box.py`
2. inside that script:
   - `run_autopos_sweep_loop.py`
   - `autopos_extract_pairs_from_sweep_summary.py`
   - `autopos_extract_ranges_from_tag_cm_runlog.py` if `REF_SESSION` only has `run.log`
   - `prepare_autopos_v3_box.py`
   - `fuse_bidirectional_matrix_v3.py`
   - `solve_anchor_layout_v3_full.py --geometry-mode box`
3. `python3 scripts/summarize_anchor_layout_result.py`
4. `python3 scripts/plot_anchor_layout.py`

## Main Outputs

Assume output root is:

```text
logs/v3_box_100set_with115_YYYYMMDD_HHMMSS
```

Important files:

- `sweep/summary.json`
- `solve_v3_box/pairs_all.csv`
- `solve_v3_box/v3_box_fused/final_pair_distances_v3.csv`
- `solve_v3_box/v3_box_fused/inter_anchor_matrix_v3fused.json`
- `solve_v3_box/anchor_layout_v3_box.json`
- `solve_v3_box/anchor_layout_v3_box_v3full_history.json`
- `solve_v3_box/result_summary_v3_box.md`
- `solve_v3_box/anchor_layout_v3_box_plot.png`
- `pipeline_manifest.json`

## Notes

- `CM115` is used as floating reference input to the solver, not as a post-solve step.
- If the floating reference directory does not contain `ranges.csv` but does contain `run.log`, the workflow now auto-extracts `ranges.csv`.
- Historical `CM115` logs with only `80` CM notify lines are supported via `REF_MIN_CM_LINES=80`.
- `run_autopos_sweep_loop.py` now discards `10` warm-up SW lines per round by default, so `SW_SETS=100` means `100` kept sets and `110` device-side sweep sets.

## Important: When Tag115 Must Be Re-Captured

If the anchor layout has changed, the old floating-reference session must not be reused blindly.

Reason:

1. `sweep` captures `anchor-anchor` data
2. `Tag115 CM` captures `tag-anchor` data
3. if anchor geometry changes, the old `tag-anchor` session is no longer matched to the new layout

So:

- old `REF_SESSION` is only valid for the old physical anchor layout
- after changing anchor positions, the correct workflow is to capture a fresh `Tag115 CM` session for the new layout

## Recommended Fresh-Tag115 Workflow

For a new layout, the recommended end-to-end flow is:

1. `100 set sweep`
2. capture a fresh fixed-position `Tag115 CM` session under the current anchor layout
3. auto-extract `ranges.csv` from that new `run.log` if needed
4. solve `V3-box` with the fresh floating-reference session
5. generate result summary and layout plot

In other words, the mathematically correct pipeline is:

```text
sweep(anchor-anchor) -> fresh Tag115 CM(tag-anchor) -> V3-box solver + floating reference -> analysis
```

Not:

```text
new sweep(anchor-anchor) -> old Tag115 CM(tag-anchor from old layout) -> solver
```

## Practical Rule

Use the old `REF_SESSION` only if all of the following remain unchanged:

- the physical anchor layout
- the Tag115 fixed position
- the intended floating-reference interpretation

If any of those changed, capture a new `Tag115 CM` session first.
