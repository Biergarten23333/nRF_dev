# Fix `mcumgr` BLE Permission Issues (TX Direct OTA)

This repository currently uses direct BLE OTA with:

```bash
mcumgr --conntype ble --hci 0 --name BSGR_TX01 ...
```

If you see:

`can't init hci: can't down device: operation not permitted`

the firmware is usually fine, but host HCI control requires elevated privilege.

## Fast Path (Recommended Tomorrow Morning)

Run BLE OTA commands with `sudo`:

```bash
sudo mcumgr --conntype ble --hci 0 --name BSGR_TX01 image list
```

If this succeeds, continue OTA using `--sudo` wrappers in `tools/ota/`.

## Optional Path (Advanced): setcap on mcumgr

Use only if your team accepts capability-based execution:

```bash
MCUMGR_BIN="$(command -v mcumgr)"
sudo setcap cap_net_admin,cap_net_raw+eip "${MCUMGR_BIN}"
getcap "${MCUMGR_BIN}"
mcumgr --conntype ble --hci 0 --name BSGR_TX01 image list
```

Notes:
- Capabilities can be cleared by reinstalling/replacing the binary.
- If policy disallows setcap, stay with `sudo`.

## Verify BLE Host State

```bash
hciconfig hci0 -a
rfkill list bluetooth
tools/host/check_ble_host_env.sh
```

`hci0` must exist and Bluetooth must not be soft/hard blocked.

## First Re-Test Command After Permission Fix

```bash
sudo mcumgr --conntype ble --hci 0 --name BSGR_TX01 image list
```

