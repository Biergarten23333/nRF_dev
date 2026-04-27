# BLE OTA Plan

This repository now treats BLE as the control plane between the nRF54L15 DK and
the DWM1001 tag.

## Role split

- `nRF52840 DK`: unified BLE control host.
- `BSxxxx` on DWM1001C: BLE peripheral and UWB runtime node, with the runtime name generated from the board UID.
- `UWB`: still carries ranging and localization traffic.

## Current state

The BLE link is currently used for:

- `PING` and `STATUS` health checks.
- OTA handshakes such as `OTA_STATUS`, `OTA_PREPARE`, `OTA_BEGIN`, and
  `OTA_CANCEL`.

The current implementation now has two concrete app families:

- `apps/master_control`: nRF52840 unified BLE central that can switch between
  receiver mode and OTA mode with `BTN1` / `BTN2`.
- `apps/master_ota`: legacy OTA-only host that still scans for the DFU SMP
  service and uploads a signed MCUboot image over BLE.
- `apps/tag_ota`: DWM1001-side BLE peripheral that advertises the DFU SMP
  service, confirms the first booted image, and accepts MCUmgr image uploads.

That means the control link is ready, and the next step is to run the image
transfer path end to end.

## What full OTA still needs

To actually replace USB flashing, the tag image must be bootloader-ready:

- reserve bootloader / upgrade partitions
- enable an OTA-capable boot path on the DWM1001 target
- add a real image transport and commit flow

The repository now includes helpers to build the signed tag image and feed it
into the OTA-capable host:

```bash
scripts/build_ble_ota_test.sh
scripts/build_master_control.sh
```

That script builds `apps/tag_ota`, generates
`apps/master_ota/generated/ota_image.inc` from the signed binary, and then
builds `apps/master_ota`.
