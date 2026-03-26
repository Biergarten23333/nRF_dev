# Ref115 Fast Build

This is the speed-focused runtime build for Tag `115`.

It is an experimental faster profile, not the main stable build.

It keeps the current two-plane solver constraints, but trims the live sweep
pattern to a faster single-tag profile:

- `APP_TAG_FIXED_MODE = 1`
- `APP_TAG_FIXED_ANCHOR_COUNT = 4`
- `APP_TAG_FIXED_ANCHOR_0_ID = 1`
- `APP_TAG_FIXED_ANCHOR_1_ID = 2`
- `APP_TAG_FIXED_ANCHOR_2_ID = 5`
- `APP_TAG_FIXED_ANCHOR_3_ID = 6`
- `APP_TAG_TDMA_ENABLE = 0`

The build entrypoint is:

```bash
scripts/build_ref115_monitor_4_fast.sh
```

Default build dir:

```bash
build-ref115-monitor-4-fast
```

Why this version:

- The tag no longer waits for the 100 ms TDMA cycle, so the solve cadence is
  driven by the actual ranging loop instead of the slot schedule.
- The solver still uses a 4-anchor two-plane subset, so `Z` stays anchored by
  the lower/upper geometry instead of collapsing to a single-plane solution.
- This is the best choice when only Tag `115` is active and you want to
  prototype the highest practical update rate.

If you need the absolute fastest possible single-subset profile, use a fixed
4-anchor build instead. That is faster, but it is less robust when one anchor
starts misbehaving.
