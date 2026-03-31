# Ref115/F66F Unified Reference-Tag Architecture (Two Modes, TDMA-Safe)

## Executive recommendation

Treat `760186115` / `BSF66F` as a **single static-reference product line** with only two runtime modes:

- `REF_MODE_CALIBRATION`
- `REF_MODE_MONITOR`

Do not keep it in the motion-tag family for normal operation.

Routine switching between calibration and monitor should be done by BLE command (OTA reserved for true firmware upgrades).

---

## Current repo evidence (what exists today)

### Calibration role exists

- Ref115 calibration profile scripts and docs exist (including OTA-capable calibration profile).
- Calibration mode already has TDMA safety behavior on tag side:
  - runtime/master TDMA overrides disabled
  - `CFG` / `TDMA_SET` commands ignored in calibration mode
  - code path: [`apps/tag/src/uwb_tag_ble.c`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/tag/src/uwb_tag_ble.c)

### Monitor role exists

- Monitor profile currently uses TDMA (typical fixed subset + slot config), documented in:
  - [`docs/ref115_current_config.md`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/docs/ref115_current_config.md)

### Problem still present operationally

- Role switching is still too image-centric in practice (calibration vs monitor builds).
- Operator can accidentally drift into motion-family behavior on 115 unless guarded.

---

## TDMA coexistence analysis (hard constraint)

Target runtime includes static Ref115 plus up to 3 motion tags.

### What must be true

1. Ref115 monitor must consume bounded TDMA airtime.
2. Motion tags must keep independent unique slots.
3. Calibration mode must not run concurrently in normal multi-tag runtime.

### Policy

- **Normal operation**:
  - Ref115 fixed to monitor mode.
  - Ref115 gets a reserved monitor slot (stable identity).
  - Motion tags occupy separate slots assigned by central.
- **Calibration operation**:
  - Explicit maintenance window only.
  - Ref115 temporarily exits monitor schedule (or runs non-TDMA calibration role intentionally).
  - Motion multi-tag run is paused or held in controlled reduced mode.

### Slot model recommendation

- Keep `slot_count_active = number_of_ready_runtime_tags` in motion control plane.
- Reserve one stable logical slot for Ref115 monitor when enabled.
- For 1 ref + 3 motion, use 4 active slots (not inflated fixed 10 active slots).
- Ref115 calibration must not silently join that runtime schedule.

---

## Proposed unified two-mode design

## Mode: `REF_MODE_CALIBRATION`

Purpose:

- temporary maintenance mode for autopositioning capture

Behavior:

- open anchor measurement policy for full capture (8-anchor intent)
- verbose range output for capture tooling
- ignore runtime/master TDMA assignment commands
- no persistence of runtime TDMA slot from central

Lifetime:

- short-lived maintenance only

## Mode: `REF_MODE_MONITOR`

Purpose:

- long-lived static health/reference operation after accepted layout

Behavior:

- bounded subset/sweep profile for stable cadence
- TDMA enabled with stable reference slot identity
- no calibration-only verbose flooding

Lifetime:

- default always-on mode

## Persistence

- Store current ref mode in settings/NVS.
- Persist monitor as default.
- Calibration mode should auto-expire or require explicit operator intent.

---

## BLE vs button mode switching

## BLE command switching (primary)

Pros:

- OTA-era consistent, remote, automatable
- no physical access needed
- central can log/audit mode transitions

Risks:

- accidental command misuse without guards

Required guards:

- require explicit command + confirmation token
- reject calibration switch while multi-tag runtime active unless forced

## Button switching (fallback)

Pros:

- recovery path when BLE control unavailable

Risks:

- accidental presses if not gated
- ambiguous with reset unless clearly separated

Recommended fallback behavior:

- boot-hold gesture only (not short press)
- visible mode marker on serial/log at boot

## Hybrid recommendation

- BLE command as default path
- button as recovery only

---

## Minimal staged implementation plan (no broad rewrite)

1. Add runtime enum in unified Ref115 firmware:
   - `REF_MODE_CALIBRATION`, `REF_MODE_MONITOR`
2. Add BLE commands:
   - `REF_MODE_GET`
   - `REF_MODE_SET CAL`
   - `REF_MODE_SET MON`
3. Store mode in settings (with safety constraints).
4. Apply mode at safe scheduler boundary + explicit reboot marker if needed.
5. Add guard:
   - block `CAL` entry during active multi-tag runtime unless explicit maintenance override.
6. Keep OTA for image upgrades only (not everyday mode toggles).

---

## Operational policy (final)

## Normal operation

- Ref115 always monitor mode.
- Motion tags run concurrently under TDMA with unique slots.
- Ref115 remains in-network without starving motion tags.

## Maintenance/autopositioning

- Operator explicitly requests Ref115 calibration mode.
- Calibration mode is temporary and gated.
- After capture/solve, Ref115 returns to monitor mode.

## Firmware upgrade

- OTA only for firmware revision changes.
- Not for routine calibration/monitor switching once unified runtime mode exists.

---

## Recovery if wrong mode active

1. Query mode (`REF_MODE_GET`).
2. If accidentally in calibration during runtime, set monitor mode and confirm.
3. If BLE unavailable, use physical fallback gesture (if enabled), then verify by startup marker.

