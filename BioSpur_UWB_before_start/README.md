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

## BLE Identification (Prompt B)

Unified anchor advertises stable identification over BLE (discovery-only path):

- manufacturer payload contains:
  - marker/version (`BSA`, `0x01`)
  - `device_uuid` (stable)
  - `anchor_id_runtime` (0..7)
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
