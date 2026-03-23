# Ref115 Autopositioning Workflow

This is the current one-command host-side workflow for recalibrating the fixed
`A-H` anchor layout with static reference Tag `115`.

What it does:

1. Capture a static `115 -> Anchor` ranging session.
2. Run the iterative anchor-layout solver with:
   - the stored `A-H` inter-anchor matrix
   - the captured `115` ranges
   - a soft prior that `115` is near `700 mm` height
3. Update:
   - `data/anchor_layout_ah_calibrated.json`
   - `data/anchor_layout_ah_runtime.json`
   - `src/uwb_anchor_layout.c`
4. Optionally rebuild and reflash Tag `115` so it immediately uses the new
   anchor coordinates during on-device localization.

The workflow now separates two `115` modes:

- `calibration` mode:
  - Used only while collecting the ranging session for autopositioning.
  - Opens the tag up to all `8` anchors and enables verbose `Range ...`
    logging so `ranges.csv` contains direct `Tag115 -> Anchor` observations.
- `monitor` mode:
  - Used after calibration for day-to-day static health monitoring.
  - Can run as:
    - `4-anchor` fixed `B,D,F,G`
    - `5/6/7/8-anchor` adaptive tracking
  - This is the mode that should prioritize low jitter and stable static
    output, not maximum anchor coverage.

## One Command

Capture, solve, update files, rebuild, and flash `115`:

```bash
python3 scripts/recalibrate_anchor_layout_with_ref115.py
```

The default behavior is:

1. Build/flash `115` in `capture_mode=calibration`
2. Capture the reference session
3. Solve the updated anchor layout
4. Rebuild/reflash `115` in `post_mode=monitor` with the stable
   `4-anchor fixed B/D/F/G` monitor profile

## Reuse An Existing Session

If `115` already has a valid `ranges.csv` session, skip capture:

```bash
python3 scripts/recalibrate_anchor_layout_with_ref115.py \
  --session-dir logs/tag_sessions/ref115_range_smoke
```

## Update Files Only

If you only want to refresh the layout files and skip build/flash:

```bash
python3 scripts/recalibrate_anchor_layout_with_ref115.py \
  --session-dir logs/tag_sessions/ref115_range_smoke \
  --skip-build \
  --skip-flash
```

## Explicit Mode Control

Collect using calibration mode, then leave `115` in 8-anchor adaptive monitor mode:

```bash
python3 scripts/recalibrate_anchor_layout_with_ref115.py \
  --capture-mode calibration \
  --post-mode monitor \
  --monitor-anchor-count 8
```

Collect using calibration mode, but do not rebuild/reflash after solving:

```bash
python3 scripts/recalibrate_anchor_layout_with_ref115.py \
  --post-mode none
```

## Defaults

- Reference tag serial: `760186115`
- Reference tag port: `/dev/serial/by-id/usb-SEGGER_J-Link_000760186115-if00`
- Reference height prior: `700 mm`
- Reference height sigma: `80 mm`
- Solver profile:
  - `distance_sigma_mm = 90`
  - `vertical_sigma_mm = 180`
  - `lower_plane_sigma_mm = 40`
  - `upper_plane_sigma_mm = 0`
  - `upper_level_sigma_mm = 20`
  - `pair_height_sigma_mm = 25`
- Current recommended live Ref115 localization config:
  - `APP_TAG_EKF_ENABLE = 1`
  - `APP_TAG_EKF_MEAS_STD_MM = 200`
  - `APP_TAG_EKF_PROC_ACCEL_MM_S2 = 1`
  - `APP_TAG_EKF_OUTLIER_GATE_MM = 35`
  - `APP_TAG_RANGE_SOFT_RESIDUAL_MM = 140`
  - `APP_TAG_RANGE_HARD_RESIDUAL_MM = 260`
- Current mode defaults:
  - `capture_mode = calibration`
  - `post_mode = monitor`
  - `monitor_anchor_count = 4`

## Current Best Static Result For Ref115

The latest continuity-gate sweep is stored in:

- `logs/tag_sessions/continuity_opt_115/result_20260320_cont_opt2.json`

The current best configuration is:

- `range_soft = 140 mm`
- `range_hard = 260 mm`

Its 150 s confirmation window produced:

- `x std = 2.31 mm`
- `y std = 1.99 mm`
- `z std = 2.91 mm`
- `rms mean = 42.51 mm`
- `max mean = 58.01 mm`

## Outputs

The script always updates:

- `data/anchor_layout_ah_calibrated.json`
- `data/anchor_layout_ah_runtime.json`
- `src/uwb_anchor_layout.c`

If build is enabled, it also creates:

- `build-tag-ref115-autopos/zephyr/merged.hex`

The script now clears and rebuilds its target `build-dir` before each compile.
This avoids stale `mcuboot` child-image state when the same workflow is rerun
multiple times.

## Ref115 Monitor-Mode Anchor Sweep

The monitor-mode sweep across `8 -> 7 -> 6 -> 5 -> 4` anchors is recorded in:

- `logs/tag_sessions/ref115_monitor_opt/result_20260321_monitor_opt1.json`

Result:

- Best monitor anchor count: `4`
- Best subset: fixed `B,D,F,G`

Latest long-window confirm (`120 s`):

- `x std = 1.84 mm`
- `y std = 1.68 mm`
- `z std = 4.81 mm`
- `rms mean = 25.61 mm`
- `max mean = 36.56 mm`

- Session:
  - `logs/tag_sessions/ref115_fixed_BDFG_confirm2_20260321/summary.json`

Observed conclusion:

- `8/7/6/5-anchor` monitor mode is materially worse for `Ref115` as a static
  health reference.
- `Ref115` should stay in:
  - `calibration mode`: all 8 anchors, verbose range capture
  - `monitor mode`: fixed `B,D,F,G`

## Important Note

This is still a host-side offline autopositioning pipeline. It is not yet a
fully automatic runtime network feature like productized PANS/DRTLS systems.
You trigger it from the computer when you want to refresh the stored anchor
layout.
