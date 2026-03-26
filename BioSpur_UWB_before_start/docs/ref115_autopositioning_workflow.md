# Ref115 Autopositioning Workflow

This is the current one-command host-side workflow for recalibrating the fixed
`A-H` anchor layout with static reference Tag `115`.

If `115` is currently flashed as a motion/OTA test tag, reflash it with the
static monitor image before running this workflow.

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
  - The main static build is the fixed `4-anchor` profile:
    - `B,C,F,G`
  - The current stable monitor build uses a `6-slot TDMA` cycle to keep the
    output cadence in the `15-20 Hz` range.
  - The dedicated build entrypoint is:
    - `scripts/build_ref115_monitor_4.sh`

## One Command

Capture, solve, update files, rebuild, and flash `115`:

```bash
python3 scripts/recalibrate_anchor_layout_with_ref115.py
```

The default behavior is:

1. Build/flash `115` in `capture_mode=calibration`
2. Capture the reference session
3. Solve the updated anchor layout
4. Rebuild/reflash `115` in `post_mode=monitor` with the main fixed
   `B,C,F,G` monitor profile

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

Collect using calibration mode, then leave `115` in the fixed 4-anchor static monitor mode:

```bash
python3 scripts/recalibrate_anchor_layout_with_ref115.py \
  --capture-mode calibration \
  --post-mode monitor \
  --monitor-anchor-count 4
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
  - `APP_TAG_RANGE_SOFT_RESIDUAL_MM = 140`
  - `APP_TAG_RANGE_HARD_RESIDUAL_MM = 260`
  - `APP_TAG_LOC_MIN_QUALITY_PERCENT = 20`
  - `APP_TAG_RANGE_CONTINUITY_ENABLE = 0`
  - `APP_TAG_TDMA_ENABLE = 1`
  - `APP_TAG_TDMA_SLOT_INDEX = 1`
  - `APP_TAG_TDMA_SLOT_COUNT = 6`
  - `APP_TAG_TDMA_SLOT_PERIOD_MS = 10`
  - `APP_TAG_TDMA_SLOT_ACTIVE_MS = 9`
  - `APP_TAG_EKF_ENABLE = 0`
- Current mode defaults:
  - `capture_mode = calibration`
  - `post_mode = monitor`
  - `monitor_anchor_count = 4`

For the current single source of truth on Tag `115`, see:

- [`docs/ref115_current_config.md`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/docs/ref115_current_config.md)

## Current Best Static Result For Ref115

The latest continuity-gate sweep is stored in:

- `logs/tag_sessions/continuity_opt_115/result_20260320_cont_opt2.json`

The current best configuration is:

- `range_soft = 140 mm`
- `range_hard = 260 mm`
- `build = build-ref115-monitor-4`
- `monitor cycle = 6 TDMA slots at 10 ms each`
- `EKF disabled in monitor mode`

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

- `build-ref115-monitor-4/zephyr/merged.hex`

The script now clears and rebuilds its target `build-dir` before each compile.
This avoids stale `mcuboot` child-image state when the same workflow is rerun
multiple times.

## Important Note

This is still a host-side offline autopositioning pipeline. It is not yet a
fully automatic runtime network feature like productized PANS/DRTLS systems.
You trigger it from the computer when you want to refresh the stored anchor
layout.
