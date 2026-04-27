# Ref115 Current Config

Date: 2026-03-25

This document is the current source of truth for Tag `115` in the **static
Ref115 monitor** workflow.

For the motion/OTA test family, use:

- `scripts/build_tag115_ble_motion.sh`

## Main Build

- Build script: `scripts/build_ref115_monitor_4.sh`
- Build dir: `build-ref115-monitor-4`
- Flash target: `760186115`

## Stable Runtime Parameters

- `APP_TAG_FIXED_MODE = 1`
- `APP_TAG_FIXED_ANCHOR_COUNT = 4`
- `APP_TAG_FIXED_ANCHOR_0_ID = 1`
- `APP_TAG_FIXED_ANCHOR_1_ID = 2`
- `APP_TAG_FIXED_ANCHOR_2_ID = 5`
- `APP_TAG_FIXED_ANCHOR_3_ID = 6`
- `APP_TAG_TDMA_ENABLE = 1`
- `APP_TAG_TDMA_SLOT_INDEX = 1`
- `APP_TAG_TDMA_SLOT_COUNT = 6`
- `APP_TAG_TDMA_SLOT_PERIOD_MS = 10`
- `APP_TAG_TDMA_SLOT_ACTIVE_MS = 9`
- `APP_TAG_LOC_MIN_QUALITY_PERCENT = 20`
- `APP_TAG_RANGE_CONTINUITY_ENABLE = 0`
- `APP_TAG_SUMMARY_PERIOD = 1`
- `APP_TAG_STATUS_PERIOD_MS = 0`
- `APP_TAG_PENDING_PRINT_PERIOD = 1`
- `APP_TAG_VERBOSE_RANGING = 0`
- `APP_TAG_VERBOSE_MEASUREMENTS = 0`
- `APP_TAG_EKF_ENABLE = 0`
- `APP_TAG_RANGE_SOFT_RESIDUAL_MM = 140`
- `APP_TAG_RANGE_HARD_RESIDUAL_MM = 260`

## What This Means

- The tag uses the fixed `B,C,F,G` anchor subset.
- The 6-slot TDMA cycle gives a practical position update cadence of about `15-20 Hz`.
- `motion_dt` is the interval between successful solved positions, not the raw per-anchor poll time.
- `raw_xyz` is the direct solver output.
- `xyz` is the same as `raw_xyz` in the current stable build because the EKF is
  disabled there.

## Expected Live Output

Typical serial output should look like:

```text
Tag motion summary sweep=264 plan=fixed sweep_ms=7 active=4 used=4 lower=2 upper=2 raw_xyz=(6624,925,520) mm xyz=(6624,925,520) mm rms=18 mm max=27 mm anchors=[B,C,F,G] motion_dt=61 ms disp=1 mm vel=(-16,0,-16) mm/s speed=23 mm/s accel=...
```

## Related Docs

- [`docs/ref115_autopositioning_workflow.md`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/docs/ref115_autopositioning_workflow.md)
- [`docs/ref115_fast_build.md`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/docs/ref115_fast_build.md)
