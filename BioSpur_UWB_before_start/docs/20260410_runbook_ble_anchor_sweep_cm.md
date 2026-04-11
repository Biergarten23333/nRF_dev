# 2026-04-10 Runbook

Timestamp: 2026-04-10 Europe/Berlin

## Scope

This runbook fixes the known-good workflow used on 2026-04-10 for:

1. `A-H` anchor sweep with `10` sweep sets (AUTOPOS master/matrix flow)
2. Tag `115` / `BSF66F` calibration mode over BLE
3. `CM` capture on BLE Master CDC

This runbook is BLE-chain oriented.

## Known Devices

- BLE Master 52840 CDC:
  - `/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00`
- Tag:
  - `BSF66F`
- Repo root:
  - `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start`

## Final Output Contract

For `MCAL`, the final required behavior is documented here:

- [cm_output_format_20260410.md](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/docs/cm_output_format_20260410.md)

The fixed contract is:

1. `MCAL` outputs only `CM`
2. `TS` must not appear
3. no raw binary `CM..` garble is printed to CDC
4. one CDC line equals one full sweep
5. each `CM` line contains anchors `0..7`

## 1. BLE Master Basic UART Commands

Open the 52840 CDC and use these commands:

```text
status
mode recv
mode autopos
device kind anchor
device kind tag
ota_target name BSF66F
oneshot MCAL
conn
cmd MCAL
autopos status
autopos map <A..H> <UUID32>
autopos round <A..H>
autopos apply
```

Reference:

- [ble_command_reference_20260409.md](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/docs/ble_command_reference_20260409.md)

## 2. Anchor UUID Map Used For AUTOPOS

These are the stable anchor UUIDs used in the `A-H` sweep flow:

- `A = 4DC6B8187E33803AE8601FB0D7992B96`
- `B = B9179575C776C98F1CB132DD6EDC6223`
- `C = CEE5A7EFCB35F8A56B430047629F5309`
- `D = AB14CCA262A092E70EB26B0ACB0A394B`
- `E = A892AF05DD59CF0D0D3408AD74F364A1`
- `F = 840C68591E90019821AACFF1B73AAA34`
- `G = B3087BC3D87CCCD316AEDC6B71D6677F`
- `H = 1EABFBEC28B8053FBB0D5C448112AE93`

## 3. Responder Setup (Tag/MCAL Stage Only)

This section is **not** a prerequisite for `A-H` AUTOPOS sweep.
Use it only when entering Tag/MCAL capture stage.

### Host BLE Helper Path

Use the host BLE helper to switch each anchor to `responder`:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

python3 scripts/ble_anchor_control.py --device-uuid 4DC6B8187E33803AE8601FB0D7992B96 --set-role responder --validate --commit --reboot --json
python3 scripts/ble_anchor_control.py --device-uuid B9179575C776C98F1CB132DD6EDC6223 --set-role responder --validate --commit --reboot --json
python3 scripts/ble_anchor_control.py --device-uuid CEE5A7EFCB35F8A56B430047629F5309 --set-role responder --validate --commit --reboot --json
python3 scripts/ble_anchor_control.py --device-uuid AB14CCA262A092E70EB26B0ACB0A394B --set-role responder --validate --commit --reboot --json
python3 scripts/ble_anchor_control.py --device-uuid A892AF05DD59CF0D0D3408AD74F364A1 --set-role responder --validate --commit --reboot --json
python3 scripts/ble_anchor_control.py --device-uuid 840C68591E90019821AACFF1B73AAA34 --set-role responder --validate --commit --reboot --json
python3 scripts/ble_anchor_control.py --device-uuid B3087BC3D87CCCD316AEDC6B71D6677F --set-role responder --validate --commit --reboot --json
python3 scripts/ble_anchor_control.py --device-uuid 1EABFBEC28B8053FBB0D5C448112AE93 --set-role responder --validate --commit --reboot --json
```

Expected role after reconnect:

- `role=responder`

Known good session reference:

- [20260409_CM_BlackBox_Runbook.md](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/docs/20260409_CM_BlackBox_Runbook.md)

## 4. A-H Anchor Sweep, 10 Sets (Master/Matrix)

### Full Loop Command

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

python3 scripts/run_autopos_sweep_loop.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --order ABCDEFGH \
  --timeout-s 480 \
  --sw-sets 10 \
  --out-dir logs/live_autopos_sweep_loop_A_to_H_10sets_retry_$(date +%Y%m%d_%H%M%S)
```

What this script does:

1. `status`
2. `mode recv`
3. `mode autopos`
4. `device kind anchor`
5. loads all `autopos map`
6. runs `autopos round <master>`
7. runs `autopos apply`
8. waits until `AUTOPOS apply success`
9. waits until `SW-<master>` count reaches `--sw-sets`

Script:

- [run_autopos_sweep_loop.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/run_autopos_sweep_loop.py)

Known good result artifact from this day:

- [summary.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/live_autopos_sweep_loop_A_to_H_10sets_retry_20260410_074833/summary.json)

## 5. Tag 115 / BSF66F Into MCAL

### BLE Master CDC Sequence

Use this exact sequence on 52840 UART:

```text
device kind tag
ota_target name BSF66F
mode recv
oneshot MCAL
conn
```

Expected success markers:

```text
Connected[0]: ... bs=BSF66F
BSF66F notify: MODE_OK MODE=CAL LIVE=1
```

Direct command variant after connection:

```text
cmd MCAL
```

## 6. CM 900s Capture

### Operator Sequence

1. Make sure anchors are already `responder` (from section 3, Tag stage only)
2. Put BLE Master in tag receive mode
3. Connect to `BSF66F`
4. send `MCAL`
5. keep logging for `900s`

### Known Good 900s Artifact

- [raw.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_cm_aggregate_capture_20260410_900s/raw.log)

This log has the correct final output form:

- one `CM` line per sweep
- anchors `0..7` included in every line
- `timeout` kept inline when present

Example:

```text
[RECV] BSF66F notify: CM;1;2507;0;ok;2593;2593;100;2479;40|CM;1;2507;1;ok;1473;1473;100;2485;31|CM;1;2507;2;ok;4731;4731;96;2466;45|CM;1;2507;3;ok;4107;4107;100;2465;44|CM;1;2507;4;ok;2498;2498;95;2472;46|CM;1;2507;5;ok;1597;1597;100;2482;31|CM;1;2507;6;ok;3622;3622;100;2461;49|CM;1;2507;7;ok;4133;4133;100;2471;37
```

## 7. Tag 115 OTA Notes

The Tag-side `CM-only` behavior was delivered to `BSF66F` using BLE OTA.

Relevant successful OTA session:

- [final_ota.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_cm_only_ota_20260410/final_ota.log)

Key success markers in that session:

1. `OTA upload progress: 100%`
2. `OTA upload complete`
3. `OTA pending/test request`
4. `OTA reset request`
5. `RESULT=SUCCESS_OR_PROGRESS`

## 8. Minimal Replay Recipes

### Replay A-H Sweep 10 Sets

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

python3 scripts/run_autopos_sweep_loop.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --order ABCDEFGH \
  --timeout-s 480 \
  --sw-sets 10 \
  --out-dir logs/live_autopos_sweep_loop_A_to_H_10sets_replay_$(date +%Y%m%d_%H%M%S)
```

### Replay BSF66F MCAL Session

On 52840 CDC:

```text
device kind tag
ota_target name BSF66F
mode recv
oneshot MCAL
conn
```

### Replay Host BLE Anchor Responder Setup

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

python3 scripts/ble_anchor_control.py --device-uuid 4DC6B8187E33803AE8601FB0D7992B96 --set-role responder --validate --commit --reboot --json
```

Repeat for the remaining `B-H` UUIDs.

## 9. Files To Search Later

Use these filenames if you need to find this workflow quickly again:

- `docs/20260410_runbook_ble_anchor_sweep_cm.md`
- `docs/cm_output_format_20260410.md`
- `logs/live_autopos_sweep_loop_A_to_H_10sets_retry_20260410_074833/summary.json`
- `logs/tag115_cm_aggregate_capture_20260410_900s/raw.log`
- `logs/tag115_cm_only_ota_20260410/final_ota.log`
