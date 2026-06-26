# BioSpur BLE Monitor

Flutter desktop UI for the nRF52840 Dongle BLE listener.

The app follows the existing BioSpur dark/lime UI style and opens the listener
CDC stream through `scripts/ble_listener_tail.py`. It parses:

- `BL;...` boot/version lines
- `BSTAT;...` listener status summaries
- `BADV;...` BLE advertisement rows

Views:

- `Status Monitor`: dongle state, scan state, tag/anchor/DFU counts, LED legend,
  and live peer table.
- `Raw Log`: raw CDC lines with autoscroll, copy, and clear controls.

Run:

```bash
./run_ble_ui.sh
```

Manual run:

```bash
flutter run -d linux
```

Build:

```bash
flutter build linux
```

Build Debian package:

```bash
flutter build linux --release
./packaging/linux_deb/build_deb.sh
```

The default port is the current BioSpur BLE listener dongle:

```text
/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Listener_760AE3DFC3CD8F38-if00
```

The Python tail helper can also auto-detect the dongle by `BioSpur_BLE_Listener`
or VID:PID `2FE3:10F3`.
