# nRF52840 Dongle BLE Listener - 2026-06-25

## Device

- App USB product: `BioSpur_BLE_Listener`
- VID:PID: `2FE3:10F3`
- Current serial: `760AE3DFC3CD8F38`
- Current CDC path:
  `/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Listener_760AE3DFC3CD8F38-if00`

## Firmware

- App: `SS-TWR/alt-SS-TWR/broadcast/apps/ble_listener`
- Build script:
  `SS-TWR/alt-SS-TWR/broadcast/scripts/build_52840_dongle_ble_listener.sh`
- DFU package script:
  `SS-TWR/alt-SS-TWR/broadcast/scripts/package_52840_dongle_ble_listener_dfu.sh`
- Built image:
  `SS-TWR/alt-SS-TWR/broadcast/build-52840-dongle-ble-listener-20260625-v2/zephyr/zephyr.hex`
- DFU zip:
  `SS-TWR/alt-SS-TWR/broadcast/biospur_ble_listener_dongle_20260625_v2.zip`

The app is observer-only: it scans BLE advertisements and scan responses. It
does not connect, does not send NUS/OTA commands, and does not occupy the single
Tag/Anchor BLE connection slot.

## USB Output

Startup:

```text
BL;1;BOOT;fw=biospur-ble-listener;version=20260625.2
BL;1;LED;map=status=led0;rgb=red:led1,green:led2,blue:led3
BL;1;READY;mode=active_scan_only;connect=0;commands=0;line=BADV/BSTAT
```

BioSpur advertisement event:

```text
BADV;1;<uptime_ms>;<addr>;<rssi>;<kind>;<name>;<id>;<role>;<uuid>;<dfu>
```

Periodic status:

```text
BSTAT;1;<uptime_ms>;tags=<n>;anchors=<n>;dfu=<n>;unknown=<n>;total=<n>;stale=<n>;adv=<n>;printed=<n>;scan=<0|1>
```

## Parsed Broadcasts

Tag manufacturer data:

```text
FF FF 'B' tag_id bs_code_le16
```

Anchor manufacturer data:

```text
FF FF 'B' 'S' 'A' 01 uuid16 anchor_id_cfg role
```

DFU visibility is detected from the MCUmgr SMP service UUID in advertisement
data.

## LED States

- Blue blink: scanner is running, no recent BioSpur Tag/Anchor/DFU broadcast.
- Green blink with status LED: at least one recent Tag or Anchor broadcast.
- Yellow blink with status LED: DFU advertiser seen recently.
- Red blink: Bluetooth or scan startup error.
- Red + blue: app is alive but scan is not running.

## Important Limitation

This is not a BLE sniffer. It cannot decode or observe NUS payloads inside an
already established Tag<->Master_Tag or Anchor<->Master_Anchor connection. It
can show advertisements, RSSI while devices are advertising, DFU presence, and
dropout/re-advertising behavior after a link is lost.
