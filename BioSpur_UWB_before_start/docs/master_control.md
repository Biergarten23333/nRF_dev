# External Master Plan

This project now uses the `nrf52840dk/nrf52840` board as the BLE control host.

## Current role split

- `apps/master_control/`: unified BLE control host on the nRF52840 DK.
- `apps/tag/`: BLE peripheral plus UWB ranging tag on DWM1001C.
- `apps/anchor/`: UWB execution nodes on the DWM1001 anchors.

## Current control link

- `nRF52840 DK` scans for `BS*` BLE peripherals in receiver mode.
- Motion tags advertise a NUS service, auto-generate a `BSxxxx` runtime identity, and keep OTA enabled.
- The master sends control commands over BLE instead of USB/UART.

## Current TDMA policy

- Motion tags derive their TDMA slot automatically from the runtime `BSxxxx` identity.
- No manual slot index needs to be assigned for the standard motion tag flow.
- The master can still use the token identity to decide which tag should be treated as which peer.

## Current command set

The master control app now boots into one of two runtime modes:

- `RECV`
  - scans for `BS*` peripherals
  - receives `UWB TAG POSITION` / `TagSummary`
- `OTA`
  - scans for OTA-capable `BS*` peripherals
  - uploads the signed image payload over BLE

Runtime switching:

- `BTN1` toggles `RECV` and `OTA`
- `BTN2` forces `OTA`
- A successful switch blinks LEDs 1 through 4 three times and prints the new mode on serial before rebooting into the selected mode

UART commands:

- `status`
- `mode recv`
- `mode ota`
