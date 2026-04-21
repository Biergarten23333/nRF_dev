# nRF52840 Dongle Scanner

This directory contains a lightweight nRF52840 dongle firmware that scans
BioSpur BLE advertisements and prints structured JSON lines over USB CDC ACM.

The USB device product name is `BS-BLE-SCANNER`.

The matching desktop UI lives in `flutter_ui/lib/scanner/` and is wired in by
`flutter_ui/lib/main.dart`.

## Firmware

Build the dongle firmware with Zephyr:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
west build -b nrf52840dongle/nrf52840 -s 52840_dongle_scanner/firmware \
  -d build-52840_dongle_scanner --pristine=always
```

The firmware emits one JSON object per scan update. The host-side Flutter app
reads those JSON lines from the dongle's CDC ACM port and renders the current
BioSpur devices.

The UI is intentionally static: it shows selected serial port, connect state,
and two device panels:

- Anchor
- Tag

There is no rolling log panel.

## Flashing

The nRF52840 dongle uses Nordic's built-in USB bootloader.

1. Put the dongle into bootloader mode by pressing the reset button.
2. Package the Zephyr hex with `nrfutil`.
3. Flash the resulting zip with `nrfutil dfu usb-serial`.

Example:

```bash
nrfutil pkg generate --hw-version 52 --sd-req=0x00 \
  --application build-52840_dongle_scanner/zephyr/zephyr.hex \
  --application-version 1 biospur_dongle_scanner.zip

nrfutil dfu usb-serial -pkg biospur_dongle_scanner.zip -p /dev/ttyACM0
```

## Host reader

The Flutter UI launches:

```bash
python3 52840_dongle_scanner/backend/dongle_scan_bridge.py --port <cdc-port>
```

That helper just relays JSON lines from the dongle to stdout.
