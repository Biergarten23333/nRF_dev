# Fusion Master DK

This application runs on nRF52840 DK/J-Link `683234364`. Marker
`dk-fusion-imu-relay-v29` keeps native USB CDC as the primary PC transport and
adds the same line-command surface on SEGGER RTT down-channel 0. Application
diagnostics are mirrored to RTT up-channel 0. CDC defaults to versioned
COBS/CRC binary records carrying node identity; the PC decoder restores the
stable `FUSION_*` grammar without DK-side ASCII formatting. `OUTPUT TEXT` and
`OUTPUT BINARY` switch CDC output at runtime. The bridge-ready record includes the
current connection interval and slave latency; later negotiated changes remain
reported by `FUSION_CI_UPDATED`. v13 parses BLE protocol v4 and reconstructs
the full IMU TIMER2 epoch from the protected kind-3 low word. After a
reconnect it defers kind-3 records until a full-width UWB timestamp or
telemetry wrap count seeds the epoch; it never treats an unreferenced low word
as full time. It temporarily
also accepts v25's nonconforming 64-bit prefix so the OTA transition is
observable. Telemetry still carries the TIMER2 counter-width field.

It scans for `BSFxxxx` plus the Fusion service UUID, requests 2M PHY/DLE and a
fixed 50 ms connection interval from connection creation, exchanges ATT MTU, discovers the data,
telemetry, and control characteristics explicitly, and subscribes to both
notify paths. It holds up to ten Fusion-PCB connections. Connection and GATT
bring-up are deliberately serialized; after one peer reaches
`FUSION_BRIDGE_READY`, scanning resumes for the next. Every multiplexed data,
telemetry, IMU, and reply record carries `name=BSFxxxx`.

v29 retains v28's kind-3 N=10 support and link-contract reporting, while fixing
connection creation at 50 ms (`interval_min=interval_max=40`). The existing
post-discovery 50 ms request remains as an intentional no-op. It records the negotiated link
contract rather than inferring it from successful requests. Each `LIST` peer
row includes PHY readback, DLE TX/RX length and time, and
`link_contract=PENDING|PASS|FAIL`; PHY/DLE update callbacks emit the same
contract as soon as each readback arrives. D5 attributed the prior bimodal
service split to scheduling/phase rather than fragmentation, so v28 observes
and reports failures but does not disconnect or reshuffle a peer.

CDC USB identity:

```text
VID:PID 2FE3:10F4
Product BioSpur Fusion Master
```

Resolve it by USB identity rather than `/dev/ttyACM<n>`. The CDC command
and RTT command surfaces are identical:

```text
LIST
MASTER STATUS
SPACING STATUS
SPACING OFF
SPACING ON
OUTPUT BINARY
OUTPUT TEXT
BSF#### <B306 or TAG command>
```

`LIST` first reports aggregate `count`, `ready`, and `capacity`, then emits one
`FUSION_PEER` row per connection with name, RSSI, subscription state,
connection parameters, PHY, and per-peer counters. A BSF-prefixed line is
written only to that board's control characteristic. The PC decoder exposes
stable text records:

- `FUSION_UWB`
- `FUSION_TELEMETRY`
- `FUSION_IMU`
- `FUSION_REPLY`
- `FUSION_QUEUE`
- `FUSION_QOS`

v25 enables the installed SDC QoS connection-event report and emits one
aggregate per connection per second. It also exposes central ACL spacing:
OFF applies 7500 us; ON performs a full fleet disconnect, applies
5000 us before any new create-connection command, then reconnects. It does
not change CI, PHY, controller packet count, event length, or any pool.

The exact BLE layouts, host-frame v1 layout, and text grammar are documented in
`../../docs/ble_protocol.md`. BLE callbacks only validate/copy records into a
fixed queue; the logger emits binary without per-record `vsnprintf`. CDC ring overflow,
malformed records, and logger queue overflow remain observable.
The transport-neutral encoder is in `../include/host_binary_protocol.h`; it
uses standard C types only and is the shared source intended for the
B120/nRF5340 carrier.

RTT is the diagnostic backup when native USB is unavailable. It must always
select DK probe `683234364` explicitly. The v9 ELF places `_SEGGER_RTT` at
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
  build --pristine=always --sysbuild -b nrf52840dk/nrf52840 \
  -s B306_Part/host/fusion_master \
  -d B306_Part/builds/dk-fusion-imu-relay-v29
python3 tools/zephyr_memory_gate.py \
  --zephyr-dir B306_Part/builds/dk-fusion-imu-relay-v29/fusion_master/zephyr \
  --flash-limit-percent 95 --ram-limit-percent 85
```

Flash only DK probe `683234364`, always with explicit probe selection. Never
allow an interactive J-Link probe-selection dialog.
