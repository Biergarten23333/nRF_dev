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
