# AUTOPOS Sweep Loop Reference

Timestamp: 2026-04-12 Europe/Berlin

## Scope

This document is the operational reference for:

- [run_autopos_sweep_loop.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/run_autopos_sweep_loop.py)

It covers:

1. `A-H` AUTOPOS sweep loop behavior
2. timeout behavior
3. live stdout verbosity
4. expected per-round control flow

## Primary Use

Run a full `A-H` anchor sweep:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

python3 scripts/run_autopos_sweep_loop.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --order ABCDEFGH \
  --sw-sets 10 \
  --verbose 1 \
  --out-dir logs/live_autopos_sweep_loop_A_to_H_10sets_$(date +%Y%m%d_%H%M%S)
```

## Current Behavior

Per round, the script does:

1. open the 52840 CDC port
2. query `status`
3. keep the controller inside `AUTOPOS`
4. reassert `mode autopos`
5. wait until `autopos status` reports:
   - `AUTOPOS: mode=AUTOPOS state=idle`
6. set `device kind anchor`
7. load the `A-H` UUID map
8. stage `autopos round <master>`
9. send `autopos apply`
10. wait until:
   - `AUTOPOS apply success: master=<X>`
   - and `SW-<X>` count reaches `--sw-sets`

Important:

- The script no longer forces a per-round `AUTOPOS -> RECV -> AUTOPOS` reboot boundary.
- Sweep role rotation stays inside `AUTOPOS`.
- This makes round-to-round behavior cleaner and closer to the intended AUTOPOS flow.

## Timeout Behavior

`--timeout-s` is optional.

If omitted, the script auto-scales timeout from `--sw-sets`:

```text
timeout_s = max(480, 360 + 15 * sw_sets)
```

Examples:

- `--sw-sets 10` -> `510 s`
- `--sw-sets 100` -> `1860 s`

If you want to override it manually, pass:

```bash
--timeout-s <seconds>
```

Recommended:

- `10 sets`: default is usually enough
- `100 sets`: default is acceptable, but `2400` is a reasonable manual override if the BLE environment is unstable

## Verbosity Control

`--verbose` controls only live stdout.

Raw per-round logs are still fully written to:

- `round_<X>/master.log`

Levels:

- `--verbose 0`
  - only `SW-X`, success, failure, and key completion markers
- `--verbose 1`
  - normal operator mode
  - suppresses noisy ignored-anchor scan lines
- `--verbose 2`
  - full live flow

Recommended default:

```bash
--verbose 1
```

## Examples

### Quiet Operator Mode

```bash
python3 scripts/run_autopos_sweep_loop.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --order ABCDEFGH \
  --sw-sets 100 \
  --verbose 0 \
  --out-dir logs/live_autopos_sweep_loop_A_to_H_100sets_quiet_$(date +%Y%m%d_%H%M%S)
```

### Normal Sweep Operator Mode

```bash
python3 scripts/run_autopos_sweep_loop.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --order ABCDEFGH \
  --sw-sets 100 \
  --verbose 1 \
  --out-dir logs/live_autopos_sweep_loop_A_to_H_100sets_normal_$(date +%Y%m%d_%H%M%S)
```

### Full Debug Flow

```bash
python3 scripts/run_autopos_sweep_loop.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --order ABCDEFGH \
  --sw-sets 10 \
  --verbose 2 \
  --out-dir logs/live_autopos_sweep_loop_A_to_H_10sets_full_$(date +%Y%m%d_%H%M%S)
```

## Notes On Scan Noise

The very noisy live line:

```text
[AUTOPOS] ANCHOR candidate ignored: ...
```

is no longer part of the intended operator-facing flow.

For normal sweep work:

- use current firmware
- use `--verbose 1`

That combination keeps live output readable while preserving the full raw log on disk.
