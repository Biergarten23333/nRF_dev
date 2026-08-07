# Fusion B306 ↔ DK BLE protocol

This document is the B306-side command reference and binary-record contract.
The shared source of truth is `../include/biospur_fusion_ble.h`. All integers
are little-endian. Records have no application CRC; BLE Link Layer CRC24
protects transport. The protocol version is 7. Version 7 extends the kind-5
loss ledger; version 6 introduced kind 5, and version 5 appended
`imu_missed_deadlines` to telemetry.
The kind-3 byte layout remains frozen: it
carries TIMER2 low-32 and the receiver extends that value before logging.

## Roles and service

- B306: BLE peripheral, UWB/IMU producer, and control endpoint.
- nRF52840 DK `683234364`: Fusion Master central and native USB CDC bridge.
- PC: sends line commands and persists `FUSION_*` output.

| UUID | Use |
|---|---|
| `7b120001-4e77-4a71-a045-7b4d3f2a9000` | Primary service |
| `7b120002-4e77-4a71-a045-7b4d3f2a9000` | Record notify (kinds 1, 3, 4, 5) |
| `7b120003-4e77-4a71-a045-7b4d3f2a9000` | Telemetry notify (kind 2) |
| `7b120004-4e77-4a71-a045-7b4d3f2a9000` | ASCII control write |

The DK requests 2M PHY, 251-byte DLE, ATT MTU exchange, and a 15–30 ms
connection interval. B306 provisions eight ATT/L2CAP/host-ACL TX contexts and,
from v32, six SDC controller TX packets. DK v28 records the actual per-link PHY
and DLE readbacks in `LIST`; a request log is not treated as negotiated proof.

## Record envelope and kinds

Every record starts with `version:u8, kind:u8, len:u16`. Receivers reject a
version mismatch, an unknown kind, or a declared/actual length mismatch.

### Kind 1 — UWB

`bsf_ble_uwb_packet_t`, 184 bytes. It embeds the byte-identical 90-byte
`bsl_uwb_t` plus the B306 strobe capture record. The underlying DWM1001C UART
frame remains exactly 96 bytes.

### Kind 2 — telemetry

`bsf_ble_telemetry_t`, 243 bytes, on its dedicated characteristic. It carries
UART/strobe/watchdog state plus:

- `drop_unsub`: a record was produced without a subscribed DK;
- `drop_err` and `last_notify_error`: `bt_gatt_notify()` failed;
- `imu_pulls`, `imu_dup`, `imu_i2c_err`, `imu_records`, and
  `imu_missed_deadlines` (absolute-deadline periods skipped before an
  accepted sample);
- `ctrl_rx`, `ctrl_bad_bsf`, `relay_tx`, `relay_ack`, `relay_timeout`; and
- current IMU rate, batch size, and active state; and
- `timer_counter_bits`, which is 16/24 only in accelerated Phase G test
  images and 32 in production.

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

`N` is derived from `len`, must be 1–10, and defaults to 5. At 200 Hz batch 5
adds up to 25 ms of batching latency. Raw-data recording accepts that cost to
reduce notification pressure; real-time users can trade latency back through
the runtime `IMU BATCH=N` control. Batch 10 produces a 152-byte record and
reduces the IMU component to 20 notifications/s without changing the default.
The default delivered load is 52
notifications/s/node (40 IMU + 10 UWB + 1 telemetry + 1 queue status). Length is
`12 + 14*N` bytes (26–152 bytes). `seq` is the first accepted, non-duplicate
sample. `base_timer2_ts_us` is the low word of the extended TIMER2 timestamp at
the first TWIM pull initiation. The DK reconstructs the epoch against the
preceding extended IMU base, a full-width UWB timestamp, or telemetry
`timer_wrap_count` before the value reaches its text/host output. Each sample
delta uses the same clock. Accelerometer, gyroscope, and
temperature remain raw:

```text
acc_g  = raw / 32768 × 16
gyro_dps = raw / 32768 × 2000
temp_C = raw / 100
```

The DK v11 or later emits one offline-parseable line per record:

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

### Kind 5 — publisher queue counters

`bsf_ble_queue_counters_t`, 58 bytes, once per second on the data
characteristic:

```text
version:u8 kind=5:u8 len:u16 node_uptime_ms:u32
q_drop_imu:u32 q_drop_uwb:u32 q_drop_ctl:u32
q_hwm_imu:u16 q_hwm_uwb:u16 q_hwm_ctl:u16
publisher_count:u32 publisher_max_us:u32
enq_imu:u32 enq_uwb:u32 enq_ctl:u32
abort_imu:u32 abort_uwb:u32 abort_ctl:u32
```

The DK emits it as `FUSION_QUEUE`. Queue drops are exact drop-oldest events;
the high-water marks are bounded by 64/16/4. `enq_*` counts successful queue
insertions and `abort_*` counts a fully built record rejected before
insertion. Full publisher and enqueue histograms remain queryable through the
control commands below.

## DK → PC host frame v1

USB CDC defaults to binary. Each record is COBS encoded and terminated by
`0x00`, so a corrupt or truncated record cannot consume the next record's
boundary. The decoded little-endian body is:

```text
magic:u16=0x5342 version:u8=1 kind:u8
node_id:u16 payload_len:u16 sequence:u32 master_arrival_ms:u64
payload[payload_len]
crc16_ccitt_false:u16
```

`node_id` is the hexadecimal suffix of `BSF####` and is present in every
frame; master-local diagnostic text uses zero. Host kinds 1/2 carry the
original BLE UWB/telemetry bytes. Host kind 3 carries the DK-extended 64-bit
IMU base plus samples. Host kind 4 carries the control reply prefix/text.
Host kind 5 carries master-local diagnostic text. Host kind 6 carries the
58-byte kind-5 BLE queue-counter record followed by four DK-side cumulative
`uint32_t` counters: delivered IMU/UWB/control and IMU epoch-defer drops. The
paired snapshot makes `enq - q_drop - delivered` exact without independently
sampled window edges. Host kind 7 is a 1 Hz per-connection SDC QoS aggregate:
report count, event-counter gaps, RX CRC-ok/error, NAK, RX timeout, channel
use, active spacing state, and the same delivery counters. The installed SDC
event has no TX count or anchor timestamp. The reference streaming decoder is
`../tools/fusion_host_binary.py`; its tests round-trip all seven kinds and
prove resynchronization after a corrupt frame.

The frame/CRC/COBS definition lives in
`../host/include/host_binary_protocol.h`. It includes only standard C integer
and size types and has no Zephyr, DK, USB, or nRF52840 dependency; the same
encoder therefore drops unchanged into the B120/nRF5340 Fusion Master.

The command input remains newline-delimited ASCII. `OUTPUT TEXT` selects a
concise, human-readable diagnostic stream; `OUTPUT BINARY` restores the
lossless default. RTT always mirrors readable diagnostics. No 1024-byte DK
stack line or split-telemetry reconstruction remains in the production path.

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
QUEUE
QUEUE ENQ=I|U|C
QUEUE PUB HIST=0|1|2|3
IMU START
IMU STOP
IMU RATE=200|100|50
IMU BATCH=1..10
IMU STATUS
IMU LATENCY
IMU DELTA=0
IMU DELTA=1
IMU DELTA=2
IMU PROVISION
IMU SELFTEST
IMU CAL_ACC
BOOT CONFIRM STATUS
BOOT CONFIRM PREPARE
BOOT CONFIRM COMMIT=<8-hex-digit-token>
```

On v32, the three `BOOT CONFIRM` commands implement the MCUboot test-image
contract. `PREPARE` succeeds only after BLE is connected and the data
characteristic is subscribed. The host must receive its token and return that
exact token in `COMMIT`; confirmation occurs only after a further five-second
connected/subscribed guard. An unconfirmed image reboots for MCUboot rollback
after 180 s. The OTA updater never sends an SMP confirmation.

`QUEUE` returns exact queue drops/high-water marks, enqueue counts/maxima, and
publisher count/maximum. `QUEUE ENQ` returns the complete 10 us enqueue
histogram (last bin is ≥100 us). `QUEUE PUB HIST` returns the reused 27-bin
publisher-duration histogram in four pages. `COUNTERS` includes an additional
`CTRQ` reply between `CTR1` and `CTR2`.

`IMU PROVISION` performs the explicit unlock/configure/save/restart sequence
and reports immediate plus post-restart register reads. It never runs
automatically. `IMU CAL_ACC` is operator-triggered only and requires the board
to be level and stationary. Boot performs only a one-second-delayed read of
registers 0x61, 0x63, 0x03, and 0x1F; a mismatch warns but is not rewritten.
No sample pulls or kind-3 records occur before `IMU START`.

On v23 and later, `COUNTERS CLEAR` starts a fresh chip-time residual
distribution. `IMU DELTA=0/1/2` return the three pages that together contain
the per-health-observation count, signed minimum/maximum, maximum absolute
residual, and all 21 fixed histogram bins. Capture tooling must retain all
three replies; no page changes a detector or initiates recovery.

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

## DK host transports

The DK USB identity is VID:PID `2FE3:10F4`, product
`BioSpur Fusion Master`. CDC remains the primary command/output channel.
`dk-fusion-imu-relay-v25` also accepts the byte-identical `LIST` /
`BSF#### <command>` grammar on SEGGER RTT down-channel 0 and mirrors records on
up-channel 0. Resolve CDC by USB identity, never `/dev/ttyACM<n>`; RTT must
select J-Link `683234364` explicitly and uses control-block address
from the matching ELF. `LIST` reports the connected BSF name, last scan RSSI,
subscription state, control handle, active spacing state/generation, HCI
handle, QoS window counts, and DK epoch-defer count. `MASTER STATUS` reports
the DK marker and aggregate loss state. `SPACING OFF` is the explicit 7500 us
baseline; `SPACING ON` disconnects all peers, applies the DERIVED spacing before the
next connection creation, and reconnects the fleet. The value is
`connection_interval / connection_count` = 50,000 / 10 = **5,000 us**, not the
10,000 us this line claimed for several generations. From dk-v36 the derived
value is also the BOOT state, so `SPACING ON` is normally answered `UNCHANGED`;
earlier images booted to `OFF` and had to be told. `SPACING STATUS` is read-only.
