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
- Anchor USB is power-only for strict mode. The launcher does not depend on Anchor serial, RTT, or direct debug access.
- The controller USB CDC port is available, for example:
  - `/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_XXXXXXXX-if00`

## Deterministic Launcher

Use the strict-UUID single-shot launcher:

```bash
python3 scripts/ota_single_shot_stable.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_XXXXXXXX-if00 \
  --target-uuid <TARGET_UUID_32HEX> \
  --out-dir logs/live_ota_<anchor>_<timestamp>/stage1
```

The launcher behavior is:

- treat Anchor USB as power-only and skip Anchor USB / RTT observability gating
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
- Missing Anchor-side USB logs are not a strict failure condition. Strict preflight uses controller-side UUID/restore/DFU evidence only.

## Field Semantics

- `target_observability_available` is a legacy field name kept for launcher summary compatibility.
- In strict mode it no longer means `anchor_lines > 0`.
- In strict mode it no longer means Anchor-side serial / RTT / USB logs were visible.
- In strict mode it now only means the launcher was allowed to proceed without forbidden-channel observability gating.
- The actual strict proof should be read from the explicit fields and log evidence:
  - controller-side UUID selection proof
  - restore proof after `mode ota`
  - `DFU SMP service ready`

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

## AUTOPOS Validation

AUTOPOS is validated separately from OTA and does not use Anchor USB as a data path.
The validated path is:

1. candidate accept by UUID
2. `bt_conn_le_create(...)`
3. `connected(...)`
4. `DISC start[...]`
5. `DISC complete[...]`
6. `ANCHOR_CTRL[...] link ready`
7. state read proves current role / `busy=...`
8. `R MASTER` or `R MATRIX`
9. `VALIDATE`
10. `COMMIT`
11. `REBOOT`
12. clear old link
13. fresh reconnect
14. final role verification
15. `AUTOPOS apply success: master=<X>`

### Build / Flash / Run

Build:

```bash
cmake --build build-master-control-ota-fix-20260403 -j4
```

Flash 52840 DK non-interactively:

```bash
nrfjprog --snr 683234364 --program build-master-control-ota-fix-20260403/master_control/zephyr/zephyr.hex --sectorerase --verify -f NRF52
nrfjprog --snr 683234364 --reset -f NRF52
```

If `flash_master_noninteractive.sh` / `reset_then_flash.sh` ever needs J-Link fallback, the repository now resolves the master probe to:

- SN `683234364` -> `nRF52840_xxAA`

and keeps anchors on the `nRF52832_XXAA` default. This avoids a stale generic 52832 device assumption in the master flashing path.

Run AUTOPOS round A validation:

```bash
python3 scripts/run_autopos_round.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_XXXXXXXX-if00 \
  --master A \
  --duration-s 200 \
  --out-dir logs/live_autopos_roundA_<timestamp>
```

### Minimal Success Proof

These log lines prove the full AUTOPOS flow:

- `CONNECT queue[...]`
- `Connected[...]`
- `DISC complete[...]`
- `ANCHOR_CTRL[...] link ready`
- `AUTOPOS anchor A role verified`
- `AUTOPOS anchor B role verified`
- `AUTOPOS anchor C role verified`
- `AUTOPOS anchor D role verified`
- `AUTOPOS anchor E role verified`
- `AUTOPOS anchor F role verified`
- `AUTOPOS anchor G role verified`
- `AUTOPOS anchor H role verified`
- `AUTOPOS apply success: master=A`
- `AUTOPOS: mode=AUTOPOS state=ready staged=A last_success=A error=-`

### Failure Taxonomy

- `ACCEPT failure`
  - no `SCAN hit:` for the target UUID
- `CONNECT failure`
  - `CONNECT pending[...] rc=<err>` or `Failed to connect[...]`
- `DISCOVERY_START failure`
  - `Could not start anchor-ctrl discovery[...]`
  - now logged with `connected_for`, retry count, and start-failure count
- `DISCOVERY_COMPLETE failure`
  - no `DISC complete[...]` before timeout
- `READY failure`
  - no `ANCHOR_CTRL[...] link ready`
  - `AUTOPOS wait anchor ready timeout`
- `APPLY failure`
  - missing `OK PENDING_ROLE`, `OK VALID`, or `OK COMMIT REBOOT_REQUIRED`
- `REBOOT/CLEAR failure`
  - `AUTOPOS wait anchor cleared timeout`
- `RECONNECT failure`
  - disconnect observed but no fresh `Connected[...]` for the target UUID
- `VERIFY failure`
  - post-reboot state never reaches `role=master` or `role=matrix`
