# Fusion Master DK

This application runs on nRF52840 DK/J-Link `683234364`. Marker
`dk-fusion-imu-relay-v6` makes native USB CDC the primary PC transport while
mirroring application records to RTT as a debug fallback.

It scans for `BSFxxxx` plus the Fusion service UUID, requests 2M PHY/DLE and a
15–30 ms connection interval, exchanges ATT MTU, discovers the data,
telemetry, and control characteristics explicitly, and subscribes to both
notify paths.

CDC USB identity:

```text
VID:PID 2FE3:10F4
Product BioSpur Fusion Master
```

Resolve it by USB identity rather than `/dev/ttyACM<n>`. The CDC command
surface is:

```text
LIST
BSF#### <B306 or TAG command>
```

`LIST` reports the connected BSF name, last scan RSSI, subscription state, and
control handle. A BSF-prefixed line is written to that board's control
characteristic. Outputs are stable text records:

- `FUSION_UWB`
- `FUSION_TELEMETRY`
- `FUSION_IMU`
- `FUSION_REPLY`

The exact binary layouts and text grammar are documented in
`../../docs/ble_protocol.md`. BLE callbacks only validate/copy records into a
fixed queue; formatting occurs in the logger thread. CDC ring overflow,
malformed records, and logger queue overflow remain observable.

Build only below the centralized directory:

```bash
cd /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion
PYTHONNOUSERSITE=1 \
PYTHONPATH=/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/lib/python3.12/site-packages \
ZEPHYR_BASE=/home/zekaixiao/ncs/v2.8.0/zephyr \
ZEPHYR_TOOLCHAIN_VARIANT=zephyr \
ZEPHYR_SDK_INSTALL_DIR=/home/zekaixiao/ncs/toolchains/b81a7cd864/opt/zephyr-sdk \
/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/bin/python3 -m west \
  build --pristine=always -b nrf52840dk/nrf52840 \
  -s B306_Part/host/fusion_master \
  -d B306_Part/builds/dk-fusion-imu-relay-v6
python3 tools/zephyr_memory_gate.py \
  --zephyr-dir B306_Part/builds/dk-fusion-imu-relay-v6/fusion_master/zephyr \
  --flash-limit-percent 95 --ram-limit-percent 85
```

Flash only DK probe `683234364`, always with explicit probe selection. Never
allow an interactive J-Link probe-selection dialog.
