# Fusion B306 ↔ DK BLE protocol

This document is the B306-side command reference and binary-record contract.
The shared source of truth is `../include/biospur_fusion_ble.h`. All integers
are little-endian. Records have no application CRC; BLE Link Layer CRC24
protects transport. The protocol version is 2.

## Roles and service

- B306: BLE peripheral, UWB/IMU producer, and control endpoint.
- nRF52840 DK `683234364`: Fusion Master central and native USB CDC bridge.
- PC: sends line commands and persists `FUSION_*` output.

| UUID | Use |
|---|---|
| `7b120001-4e77-4a71-a045-7b4d3f2a9000` | Primary service |
| `7b120002-4e77-4a71-a045-7b4d3f2a9000` | Record notify (kinds 1, 3, 4) |
| `7b120003-4e77-4a71-a045-7b4d3f2a9000` | Telemetry notify (kind 2) |
| `7b120004-4e77-4a71-a045-7b4d3f2a9000` | ASCII control write |

The DK requests 2M PHY, 251-byte DLE, ATT MTU exchange, and a 15–30 ms
connection interval. Both ends provision eight ATT/L2CAP/ACL TX contexts.

## Record envelope and kinds

Every record starts with `version:u8, kind:u8, len:u16`. Receivers reject a
version mismatch, an unknown kind, or a declared/actual length mismatch.

### Kind 1 — UWB

`bsf_ble_uwb_packet_t`, 184 bytes. It embeds the byte-identical 90-byte
`bsl_uwb_t` plus the B306 strobe capture record. The underlying DWM1001C UART
frame remains exactly 96 bytes.

### Kind 2 — telemetry

`bsf_ble_telemetry_t`, 158 bytes, on its dedicated characteristic. It carries
UART/strobe/watchdog state plus:

- `drop_unsub`: a record was produced without a subscribed DK;
- `drop_err` and `last_notify_error`: `bt_gatt_notify()` failed;
- `imu_pulls`, `imu_dup`, `imu_i2c_err`, `imu_records`;
- `ctrl_rx`, `ctrl_bad_bsf`, `relay_tx`, `relay_ack`, `relay_timeout`; and
- current IMU rate, batch size, and active state.

### Kind 3 — IMU

Variable-length packed layout:

```text
version:u8 kind=3:u8 len:u16
seq:u16 base_timer2_ts_us:u32
N × {
    delta_us:u16
    acc_x:i16 acc_y:i16 acc_z:i16
    gyro_x:i16 gyro_y:i16 gyro_z:i16
}
temperature_raw:i16
```

`N` is derived from `len`, must be 1–5, and defaults to 2. Length is
`12 + 14*N` bytes (26–82 bytes). `seq` is the first accepted, non-duplicate
sample. `base_timer2_ts_us` is TIMER2 low-32 at the first TWIM pull initiation;
each sample delta uses the same clock. Accelerometer, gyroscope, and
temperature remain raw:

```text
acc_g  = raw / 32768 × 16
gyro_dps = raw / 32768 × 2000
temp_C = raw / 100
```

The DK emits one offline-parseable line per record:

```text
FUSION_IMU proto=<v> master_ms=<ms> seq=<seq> base_us=<timer2>
  n=<N> temp_raw=<t> samples=<dt,ax,ay,az,gx,gy,gz;...>
```

### Kind 4 — control reply

```text
version:u8 kind=4:u8 len:u16
source:u8             # 0=B306, 1=tag
correlation:u16
text:ASCII[len-7]
```

The DK emits `FUSION_REPLY ... source=B306|TAG correlation=<n> text=<text>`.
Relay commands first receive a B306 `RELAY_QUEUED` reply. A later tag UART ack
uses the same correlation and `source=TAG`; a two-second miss produces
`source=TAG text=TIMEOUT`.

## Control grammar

The control value is one ASCII line of at most 200 bytes:

```text
BSF#### <command>
```

B306 compares `BSF####` with its FICR-derived BLE name. A nonmatching line is
ignored and increments `ctrl_bad_bsf`.

Local commands:

```text
PING
STATUS
REBOOT
COUNTERS
COUNTERS CLEAR
IMU START
IMU STOP
IMU RATE=200|100|50
IMU BATCH=1|2|3|4|5
IMU STATUS
IMU PROVISION
IMU SELFTEST
IMU CAL_ACC
```

`IMU PROVISION` performs the explicit unlock/configure/save/restart sequence
and reports immediate plus post-restart register reads. It never runs
automatically. `IMU CAL_ACC` is operator-triggered only and requires the board
to be level and stationary. Boot performs only a one-second-delayed read of
registers 0x61, 0x63, 0x03, and 0x1F; a mismatch warns but is not rewritten.
No sample pulls or kind-3 records occur before `IMU START`.

Relay commands:

```text
TAG PING
TAG REBOOT
TAG STATUS
TAG TDMA_STATUS
TAG CFG id=<n> slot=<s> count=<c>
        [period=10] [active=9] [epoch=5000]
TAG TDMA CLEAR
TAG RAW <existing-tag-command>
```

`TAG CFG` becomes:

```text
CFG TAG=<id> SLOT=<s> COUNT=<c> PERIOD=<p> ACTIVE=<a> EPOCH=<e>
```

The on-air address remains `0xB100 + id`; it is never a command parameter.
The audited tag has no direct free-run command, so `TAG TDMA CLEAR` is
explicitly mapped to `REBOOT`, restoring documented boot/free-run behavior.
`TAG RAW` is the escape hatch for the existing tag command surface.

For TAG CFG, the tag's `CFG_OK ... LIVE=1` means ACCEPTED/QUEUED, not
transmitting. Proof of transmission is rising strobe and valid UART frame
counters increasing at approximately 10 Hz.

## UART relay frames

`biospur_link.h` defines a variable relay frame with magic `C3 6D`, version,
type (`1=command`, `2=ack`), payload length, correlation, up to 191 ASCII
bytes, and CRC-16/CCITT-FALSE. It is unambiguously distinct from the fixed
96-byte UWB data frame. The header is maintained byte-identically in the B306
and fusion-tag trees.

## USB CDC

The DK USB identity is VID:PID `2FE3:10F4`, product
`BioSpur Fusion Master`. CDC is the primary command/output channel; RTT mirrors
the same application records as a debug fallback. Resolve the port by USB
identity, never by `/dev/ttyACM<n>`. `LIST` reports the connected BSF name,
last scan RSSI, subscription state, and control handle.
