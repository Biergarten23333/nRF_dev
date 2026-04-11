# TX BLE OTA Runbook (Direct PC -> TX)

This runbook is for direct BLE DFU from PC to TX node (`BSGR_TX01`) using `mcumgr`.
Central bridge is intentionally out of scope.

## Prerequisites

- Repository root:
  - `/home/zekaixiao/Documents/nRF_dev/BioSpur_MultiNode_Gesture_System`
- TX signed image:
  - `/home/zekaixiao/Documents/nRF_dev/BioSpur_MultiNode_Gesture_System/tx_node/build/tx_node/zephyr/zephyr.signed.bin`
- BLE adapter:
  - `hci0`
- Scripts in:
  - `tools/ota/`
  - `tools/host/`

## Quick Host/TX Check

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_MultiNode_Gesture_System
tools/host/check_ble_host_env.sh
tools/ota/tx_ble_probe.sh
```

Expected:
- `BSGR_TX01` visible in scan.
- Probe reports SMP and NUS present.

## If Host Permission Error Appears

If `mcumgr` returns:
- `can't init hci: can't down device: operation not permitted`

Use `sudo` path first:

```bash
sudo mcumgr --conntype ble --hci 0 --name BSGR_TX01 image list
```

Then use the OTA wrappers with `--sudo`.

Optional capability approach is documented in:
- `tools/host/fix_mcumgr_ble_permissions.md`

## Exact OTA Sequence

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_MultiNode_Gesture_System

# 1) list current images
tools/ota/tx_ble_image_list.sh --sudo

# 2) upload new signed image
tools/ota/tx_ble_upload.sh --sudo

# 3) list again, copy slot1 hash
tools/ota/tx_ble_image_list.sh --sudo

# 4) mark new image for test boot
tools/ota/tx_ble_test.sh --sudo --hash <slot1_hash>

# 5) reset
tools/ota/tx_ble_reset.sh --sudo

# 6) wait for re-advertise, then list
tools/ota/tx_ble_image_list.sh --sudo

# 7) only after stable post-reset behavior, confirm
tools/ota/tx_ble_confirm.sh --sudo
```

## One-Command Guided Cycle

```bash
tools/ota/tx_ble_full_cycle.sh --sudo
```

Optional:
- Force peer: `--peer BSGR_TX02`
- Use custom image: `--image /abs/path/custom.signed.bin`
- Auto-confirm at end (only if you accept it): `--confirm`

## Rollback Detection

After `image test` + `reset`:
- If post-reset `image list` still shows old slot active and test flag cleared, rollback happened.
- Do **not** confirm in that case.

## Success Criteria

- `image list` reachable over BLE.
- Upload succeeds without transport error.
- `image test` accepted.
- After reset, new image remains booted and reachable.
- `image confirm` succeeds after stability verification.

## Common Failure Patterns

| Pattern | Meaning | Action |
|---|---|---|
| `operation not permitted` on HCI down | Host BLE privilege blocker | Re-run with `--sudo` |
| `BSGR_TX01` not seen in scan | TX not advertising or power/reset issue | Re-power TX and re-run `tx_ble_probe.sh` |
| SMP missing in probe | TX DFU service not reachable | Reflash known-good TX build once, then retry |
| Upload works but post-reset rolls back | New image unstable | Keep rollback safety; inspect runtime before confirm |

