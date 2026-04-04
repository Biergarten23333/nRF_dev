# BioSpur UWB (Current Prompt A Baseline)

This repository currently uses a unified Anchor firmware path for Phase 2.1 validation.

## Unified Anchor Build (single artifact)

Build one unified Anchor image:

```bash
./scripts/build_anchor_unified.sh build-anchor-unified-phase21a
```

Output:
- `build-anchor-unified-phase21a/zephyr/zephyr.elf`
- `build-anchor-unified-phase21a/zephyr/zephyr.hex`

## Provision Anchor Identity + Role

Provision one device (J-Link serial must be anchor probe: `7xxxxxx`):

```bash
python3 scripts/provision_anchor.py \
  --probe-serial 760185876 \
  --anchor-id B \
  --role master \
  --verify
```

Supported roles:
- `master`
- `matrix`
- `responder`

Read back config:

```bash
python3 scripts/read_config.py --probe-serial 760185876
```

## BLE Identification Baseline

Unified anchor advertises stable identification over BLE:

- manufacturer payload contains:
  - marker/version (`BSA`, `0x01`)
  - `device_uuid` (stable)
  - `anchor_id_cfg` (`0=U`, `1..8=A..H`)
  - `role` (0..3)

Scan map output:

```bash
python3 scripts/scan_and_map.py --timeout-s 8
```

JSON scan output:

```bash
python3 scripts/scan_and_map.py --timeout-s 8 --json
```

Provisioning with BLE precheck:

```bash
python3 scripts/provision_anchor.py \
  --probe-serial 760185876 \
  --anchor-id B \
  --role master \
  --device-uuid B9179575C776C98F1CB132DD6EDC6223 \
  --use-ble-scan \
  --verify
```

## BLE Config/Control Plane (anchor-first)

Unified anchor now exposes a formal BLE control service:

- Service UUID: `2f2b8f40-84e0-4be6-b6bf-2fd95f39d3f0`
- Characteristics:
  - state `...d3f1` (read/notify)
  - active config `...d3f2` (read)
  - pending config `...d3f3` (read)
  - control `...d3f4` (write)
  - result `...d3f5` (read/notify)

Control model is two-step and safe:
1. Write pending (`R/L/G` or full commands)
2. `VALIDATE`
3. `COMMIT` (persist + verify, may return `REBOOT_REQUIRED`)
4. `REBOOT`
5. Readback verify (`state/active/pending/result`)

Common control commands:
- `R <MASTER|MATRIX|RESPONDER|UNSET>`
- `L <U|A..H>`
- `G <generation>`
- `VALIDATE`
- `COMMIT`
- `REBOOT`
- `SYNC`

Host helper:

```bash
python3 scripts/ble_anchor_control.py \
  --device-uuid B9179575C776C98F1CB132DD6EDC6223 \
  --set-role matrix \
  --set-label B \
  --set-generation 11 \
  --validate \
  --commit \
  --json
```

Readback:

```bash
python3 scripts/ble_anchor_control.py \
  --device-uuid B9179575C776C98F1CB132DD6EDC6223 \
  --json
```

Error model examples:
- `ERR:BUSY`
- `ERR:INVALID_CONFIG`
- `ERR:WRITE_FAIL`
- `ERR:CRC_MISMATCH`
- `ERR:INVALID_ROLE`
- `ERR:INVALID_LABEL`

## nRF52840 OTA Target Safety (control-center rule)

For OTA operations from `master_control`/`master_ota`, the safety rule is:

- **No verified identity, no destructive operation.**
- Target UUID is authoritative for OTA.
- Name/prefix/token are auxiliary filters only.

Required command flow before `mode ota`:

```text
device kind anchor
ota_target uuid <32hex>
ota_target name <optional exact name or ->
ota_target token <optional id or -1>
mode ota
```

New control command:
- `ota_target uuid <32hex|->`

Behavior:
- if target UUID is missing or mismatched, OTA start is blocked.
- non-target peers must not be selected for upload.
- in UUID-authoritative mode, prefix is advisory; exact name is enforced only when explicitly set.

Canonical blackbox runbook for the observed single-shot flow:

- [docs/20260403_OTA_BlackBox_Runbook.md](docs/20260403_OTA_BlackBox_Runbook.md)

Note (current Tag115 path):
- Current Tag115 advertisements may expose `uuid=-` in OTA scan logs.
- With strict UUID-authoritative gate enabled, OTA to Tag115 is blocked until UUID is present in advertising/manufacturer payload.
- For immediate bench validation, use direct J-Link flash to Tag115 and then BLE control via unified `master_control` RECV path.

Legacy loop-test helper:

```bash
python3 scripts/loop_test_ota_targeting.py \
  --target-uuid D8757CA15A1837997DD3DB09F3B29C35 \
  --target-name ANCHOR-E-BS2B67 \
  --trials 3 \
  --flash-image build-master-control-anchor-ota-safe-20260331b/master_control/zephyr/zephyr.hex \
  --out-dir logs/anchor_ota_phase_20260331_loop_uuid_b
```

## Startup Signature (required check)

After reset, the firmware prints:

```text
ANCHOR: unified; ANCHOR_ID: B; ROLE: master; DEVICE_UUID: <hex>; MCU_UID: <hex>
```

Also verify role behavior line:
- master: `Anchor master auto schedule ...`
- matrix: `SS-TWR responder ready ... allow_tag_polls=0`
- responder: `SS-TWR responder ready ... allow_tag_polls=1`

## Prompt A Notes

- Prompt A scope is minimal Phase 2.1 validation.
- Do not re-expand into `A..H x role` artifact packaging for this step.
- Use one unified artifact + provisioning to select identity/role.

Primary execution log:
- `docs/BlackBox_20260330.md`

## Official Unified 52840 Control Build

Build command:

```bash
./scripts/build_master_control_unified.sh build-master-control-unified
```

This build consolidates:
- Scan
- Connect
- Receive
- Runtime control command dispatch
- OTA mode (identity-safe targeting)

No reflashing is needed to switch receiver-side behavior (`scan`, `conn`, command dispatch).
Mode switch to OTA remains runtime-driven (`mode ota`) with reboot.

### Runtime commands (master_control UART)

- `status`
- `mode recv`
- `mode ota`
- `scan`
- `conn`
- `cmd <raw NUS command>`
- `oneshot <raw NUS command>`
- `oneshot show`
- `oneshot clear`

Examples:

```text
cmd MODE?
cmd MODE AOTA
cmd CFG_STATUS
```

This replaces prior one-off reflashes for simple one-shot control actions.

## Tag Positioning Mode Map (current)

- `PMODE=0` dynamic (`MOTION`)
- `PMODE=1` fixed (`FIXED`)
- `PMODE=2` calibration (`CAL`)
- `PMODE=3` anchor OTA quiet mode (`AOTA`)

## UART Serial Role Switch (Phase2.1-next)

Unified anchor firmware now includes a UART command channel (ASCII lines, `CR/LF`):

- `ROLE?`
- `ROLE SET <master|matrix|responder>`
- `ANCHOR SET <A..H>`
- `CONFIG SAVE`
- `REBOOT`
- `STATUS`

Response contract:
- query/status: structured single-line text
- command result: `OK` or `ERR:<reason>`

Busy rule:
- `ROLE SET` and `ANCHOR SET` update RAM working config.
- `CONFIG SAVE` persists flash config only when not in ranging-active state.
- if ranging is active, `CONFIG SAVE` returns `ERR:BUSY`.

Helper script:

```bash
python3 scripts/serial_switch_role.py \
  --port /dev/serial/by-id/usb-SEGGER_J-Link_000760185876-if00 \
  --role master \
  --anchor-id B \
  --save \
  --reboot
```

Notes:
- This path does **not** replace J-Link recovery/provisioning; J-Link remains fallback.
- Serial command path requires host-to-target UART RX wiring/path to be functional on the selected board.

## Identity model (important)

Do not treat advertising name as sole identity.

- Stable physical identity: `device_uuid` (authoritative)
- Logical assignment: `anchor_id_cfg` (`U`/`A..H`) + role
- Human display: `BSxxxx` / advertising name

Unassigned anchors (`U`) remain discoverable and configurable over BLE.
