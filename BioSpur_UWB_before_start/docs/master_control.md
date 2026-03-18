# External Master Plan

This project now uses the `nrf54l15dk/nrf54l15/cpuapp` board as the BLE control
host.

## Current role split

- `apps/master/`: BLE central and control host on the nRF54L15 DK.
- `apps/tag/`: BLE peripheral plus UWB ranging tag on DWM1001C.
- `apps/anchor/`: UWB execution nodes on the DWM1001 anchors.

## Current control link

- `nRF54L15 DK` scans for the `Tag_rot` BLE peripheral.
- `Tag_rot` advertises a NUS service.
- The master sends control commands over BLE instead of USB/UART.

## Current command set

The master currently cycles through:

- `PING`
- `STATUS`
- `OTA_STATUS`
- `OTA_PREPARE`

The tag replies with health, UWB status, and OTA handshake state.

## What is still missing for full OTA

The BLE control link is in place, but full image transfer still needs:

- a bootloader-ready tag image
- reserved upgrade partitions
- an image transport and commit path

See [ble_ota_plan.md](ble_ota_plan.md) for the split between control-plane
handshake and actual firmware update transport.
