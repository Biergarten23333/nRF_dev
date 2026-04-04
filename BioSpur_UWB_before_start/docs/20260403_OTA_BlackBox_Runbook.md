# 2026-04-03 OTA BlackBox Runbook

## Purpose

Document the real strict-UUID OTA flow as observed in runtime logs, using the current single-shot launcher. This is the canonical blackbox sequence for the unified `master_control` image.

## Canonical Flow

Observed successful runs start from a RECV baseline on the controller, then follow this sequence:

1. `status`
2. `device kind anchor`
3. `device show`
4. `status`
5. `ota_target uuid <32hex>`
6. `ota_target show`
7. `mode ota`
8. reboot / serial reconnect
9. `status`
10. `ota_target show`
11. `initiate`
12. controller scan/connect
13. `DFU SMP service ready`
14. `OTA upload gate open`
15. `OTA upload starting`
16. `OTA upload progress`
17. `OTA upload complete`
18. `OTA pending/test request`
19. `OTA reset request`
20. `OTA command sequence sent`

Role switching is separate from OTA. It is validated after OTA completes, using the dedicated role-switch helper.

## Required Preconditions

- 52840 controller is flashed with the current `master_control` build.
- The launcher starts from an already-RECV controller state. The OTA launcher does not issue `mode recv` itself.
- The target UUID is known and authoritative for selection.
- The controller USB CDC port is available, for example:
  - `/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_XXXXXXXX-if00`

## Deterministic Launcher

Use the strict-UUID single-shot launcher:

```bash
python3 scripts/ota_single_shot_stable.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_XXXXXXXX-if00 \
  --target-uuid <TARGET_UUID_32HEX> \
  --anchor-port /dev/serial/by-id/usb-SEGGER_J-Link_XXXXXXXXXXXX-if00 \
  --anchor-reset-preflight \
  --out-dir logs/live_ota_<anchor>_<timestamp>/stage1
```

The launcher behavior is:

- verify the controller UART is ready
- send `device kind anchor`
- verify `System target: kind=anchor`
- verify `OTA NUS stage: disabled`
- write `ota_target uuid <UUID>`
- confirm `ota_target show` matches the UUID
- send `mode ota`
- wait for reboot and reconnect
- confirm restored UUID state
- send `initiate`
- wait for connection, DFU readiness, upload gate, and upload completion

## Behavioral Answers

- `mode recv` required before OTA?
  - The controller must already be in RECV at launcher start, but the launcher does not send `mode recv` as part of the OTA sequence.
- `device kind anchor` required every time?
  - Yes. Observed successful OTA runs depend on issuing `device kind anchor` before writing the UUID.
- Does OTA always reboot?
  - Yes. `mode ota` triggers a reboot and the serial session drops.
- After reboot, does OTA rely on restored state or re-sent commands?
  - Restored state. The launcher verifies the UUID and OTA filter after reconnect before sending `initiate`.
- What is the first point where OTA can fail?
  - Before `mode ota`, if the launcher cannot prove `device kind anchor`, `OTA NUS stage: disabled`, and a matching UUID readback.
- What is the last point before upload begins?
  - `OTA upload gate open`.
- Is role switching part of OTA flow?
  - No. It is a separate post-OTA validation step.

## Hidden Dependencies Proven by Logs

- `device kind anchor` clears or resets OTA target defaults, so UUID must be written after the kind switch.
- After `mode ota`, reconnect is mandatory because the UART drops during reboot.
- `initiate` must only be sent after the rebooted runtime proves `Control mode loaded: OTA`, `UART control ready`, and restored UUID/filter state.
- The OTA launcher starts from RECV, but the successful path is not a typed `mode recv` transition.

## Evidence

Successful runtime evidence is captured in the per-anchor OTA logs, for example:

- `logs/live_ota_anchorA_full_retry4_20260404_175847/stage1/single_shot.log`
- `logs/live_ota_anchorB_full_fixkind_20260404_184740/stage1/single_shot.log`
- `logs/live_ota_anchorC_full_20260404_191703/stage1/single_shot.log`
- `logs/live_ota_anchorD_full_20260404_192645/stage1/single_shot.log`
- `logs/live_ota_anchorE_full_20260404_193527/stage1/single_shot.log`
- `logs/live_ota_anchorF_full_20260404_194515/stage1/single_shot.log`
- `logs/live_ota_anchorG_full_20260404_195456/stage1/single_shot.log`
- `logs/live_ota_anchorH_full_20260404_200239/stage1/single_shot.log`

The role-switch validation is captured separately with:

- `scripts/serial_switch_role.py`

