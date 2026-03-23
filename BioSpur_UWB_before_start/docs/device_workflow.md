# Device Workflow

Default workflow for attached DWM1001 boards:

1. Before every flash:
   - `reset -> flash -> reset`
2. Before every serial read:
   - `reset -> open serial -> read`

Helper scripts:

- `scripts/reset_then_flash.sh <snr> <hex_path>`
- `scripts/reset_then_read_serial.py <snr> <serial_port> --duration 8`
- `scripts/capture_tag_session.py <snr> <serial_port> --duration 120`
- `scripts/run_ground_truth_point.py <snr> <serial_port> --label ... --truth-x ... --truth-y ... --truth-z ...`
- `scripts/analyze_ground_truth_session.py <session_dir> --label ... --truth-x ... --truth-y ... --truth-z ...`
- `scripts/analyze_ground_truth_batch.py --root logs/ground_truth`
- `scripts/generate_ground_truth_points.py`

Build note:

- For per-anchor images that depend on `APP_ANCHOR_*` CMake cache variables, use `west build ... --no-sysbuild`.
- If `sysbuild` is used, the outer cache may show the requested values while the inner anchor app still uses its defaults. That produces a board that boots with the wrong `anchor_id/master` role.

Examples:

```bash
scripts/reset_then_flash.sh 760186071 build-anchor-A-master/zephyr/zephyr.hex
scripts/reset_then_read_serial.py 760186071 /dev/serial/by-id/usb-SEGGER_J-Link_000760186071-if00
scripts/capture_tag_session.py 760186127 /dev/serial/by-id/usb-SEGGER_J-Link_000760186127-if00 --duration 300 --skip-sweeps 2
```

## Three-Tag UWB Test Setup

Current intended test roles:

- `760185886`: static reference tag, lower height, about `90-100 cm`
- `760186115`: static reference tag, higher than `760185886`
- rotating tag: dynamic target, connected through BLE and kept in continuous motion

Recommended current tag images:

- `760185886` -> `build-tag-slot0/zephyr/zephyr.hex`
- `760186115` -> `build-tag-slot1/zephyr/zephyr.hex`

These two builds are the existing direct-flash UART-console tag images already present in the repo:

- `build-tag-slot0`: `tag_id=0`, `tdma=1`, `slot=0/10`
- `build-tag-slot1`: `tag_id=1`, `tdma=1`, `slot=1/10`

Suggested flash commands:

```bash
scripts/reset_then_flash.sh 760185886 build-tag-slot0/zephyr/zephyr.hex
scripts/reset_then_flash.sh 760186115 build-tag-slot1/zephyr/zephyr.hex
```

Suggested serial read commands:

```bash
scripts/reset_then_read_serial.py 760185886 /dev/serial/by-id/usb-SEGGER_J-Link_000760185886-if00 --duration 10 --settle 0.2
scripts/reset_then_read_serial.py 760186115 /dev/serial/by-id/usb-SEGGER_J-Link_000760186115-if00 --duration 10 --settle 0.2
```

Suggested longer captures:

```bash
scripts/capture_tag_session.py 760185886 /dev/serial/by-id/usb-SEGGER_J-Link_000760185886-if00 --duration 300 --skip-sweeps 2 --session-name tag_760185886_static_low
scripts/capture_tag_session.py 760186115 /dev/serial/by-id/usb-SEGGER_J-Link_000760186115-if00 --duration 300 --skip-sweeps 2 --session-name tag_760186115_static_high
```

## Three-Tag Test Goal

Use the two static tags plus one rotating tag to separate three different problems:

1. common geometry error
2. dynamic solver jump / subset instability
3. multi-tag scheduling / air-time interference

## Recommended Physical Placement

1. Put `760185886` at a fixed lower position, about `90-100 cm`.
2. Put `760186115` at a fixed higher position.
3. Keep the rotating tag moving continuously in the intended rotation test area.
4. Measure and note the real height difference:
   - `delta_z_true = z_115 - z_886`

## Recommended Test Sequence

1. Verify both static tags boot and produce `Tag app ready` and `Tag motion summary`.
2. Run both static tags alone for `5-10 min`.
3. Add the rotating BLE tag and run all three together for `20-30 min`.
4. If needed, repeat with the rotating tag closer to likely NLOS regions.

## What To Watch

For `760185886` and `760186115`:

- `xyz` mean
- `xyz` stddev
- `z` stability
- `rms` and `max`
- chosen anchors
- update rate

For the rotating tag:

- position jump size
- `z` span
- `rms` and `max`
- whether the estimated rotation center drifts

Most important cross-check:

- compare solved `delta_z = z_115 - z_886` against the real measured height difference

## How To Interpret Results

If both static tags drift together:

- suspect anchor geometry, antenna delays, or common-layout error first

If both static tags stay stable but the rotating tag jumps:

- suspect solver dynamics first:
  - anchor subset switching
  - weak continuity constraints
  - weak vertical constraint

If single-tag runs are stable but three-tag runs degrade:

- suspect TDMA / scheduling / shared air-time contention first

## Optimization Order

1. static absolute accuracy
   - antenna delays
   - anchor layout fine adjustment
2. dynamic solver stability
   - subset hysteresis
   - motion continuity penalty
   - stronger upper/lower anchor balance for `Z`
3. latency and multi-tag throughput
   - track-anchor count
   - full-sweep interval
   - TDMA timing
