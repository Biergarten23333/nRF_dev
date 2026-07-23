# Fusion Master DK

This application runs on nRF52840 DK/J-Link `683234364`. Marker
`dk-fusion-imu-relay-v7` keeps native USB CDC as the primary PC transport and
adds the same line-command surface on SEGGER RTT down-channel 0. Application
records are mirrored to RTT up-channel 0.

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
and RTT command surfaces are identical:

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

RTT is the temporary transport when native USB is unavailable. It must always
select DK probe `683234364` explicitly. The v7 ELF places `_SEGGER_RTT` at
`0x20002100`; host tooling verifies both up/down buffer 0 before use:

```bash
/usr/bin/python3 B306_Part/tools/capture_jlink_rtt.py \
  --serial-number 683234364 --device nRF52840_xxAA \
  --address 0x20002100 --duration-s 5 \
  --command LIST \
  --output B306_Part/logs/rtt_list.log
```

For ordered bring-up, add `--transport=rtt` to
`B306_Part/tools/fusion_session.py start|stop`. The same S1–S7/T1–T3 parser and
gates are used; only the byte transport changes.

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
  -d B306_Part/builds/dk-fusion-imu-relay-v7
python3 tools/zephyr_memory_gate.py \
  --zephyr-dir B306_Part/builds/dk-fusion-imu-relay-v7/fusion_master/zephyr \
  --flash-limit-percent 95 --ram-limit-percent 85
```

Flash only DK probe `683234364`, always with explicit probe selection. Never
allow an interactive J-Link probe-selection dialog.
