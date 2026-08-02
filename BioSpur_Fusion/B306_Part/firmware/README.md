# B306 bootable firmware

This NCS v2.8.0 sysbuild targets the custom
`biospur_fusion_nrf52840/nrf52840` board for NINA-B306-01B. The firmware
contains:

- signed ECDSA-P256 MCUboot with equal internal-flash slots;
- mcumgr SMP over BLE with image and OS groups;
- FICR-derived `BSF%04X` advertising;
- non-blocking RTT logs and two active-low, event-driven LEDs on P0.13/P0.14;
- 460800 8N1 UARTE ingest on P1.01 and framed command TX on P1.02;
- fixed-length v2 frame resynchronization, CRC checking, and sweep accounting;
- 1 MHz TIMER2 extended to 64 bits, with natural-wrap accounting;
- dual-edge P1.03 GPIOTE -> dynamically allocated PPI -> TIMER capture;
- 50 ms strobe/frame pairing using a UART-callback timestamp carried with the
  DMA bytes, with explicit four-case verdicts; and
- JY61P 400 kHz TWIM on P0.26/P0.27, boot verification, explicit
  provisioning, continuous chip-time-aware polling, coherency checking, and
  TIMER2-timestamped 50/100/200 Hz output, plus a volatile `IMU RRATE=<code>`
  path, volatile `IMU BW=<hex>`, and unlocked
  `IMU REG=<hex> [VAL=<hex>]` peek/poke path that deliberately perform no
  SAVE or sensor restart. Every `IMU START` also applies and verifies the
  H2-accepted volatile gyro-auto-zero suppression `0x61=0x0001`;
- protocol-v7 UWB, telemetry, variable kind-3 IMU, kind-4 control replies,
  and kind-5 loss-ledger counters;
  and
- a BSF-addressed writable control characteristic.

It deliberately contains no estimator or fusion logic. IMU values remain raw;
conversion, alignment analysis, and fusion stay on the host. The first-flash
image was `b306-first-dfu-v1`, version `0.1.0+0`.
The first accepted BLE-only update was `b306-stage1-ota-v2`, version
`0.1.1+0`; Stage 1 upload, real MCUboot revert, confirmation, and persistence
across reboot passed on the Fusion PCB on 2026-07-20.

The five-node capacity baseline ran `b306-imu-relay-v26`. v27 made IMU batch
5 the runtime default and appended `imu_missed_deadlines` to protocol-v5
telemetry. `b306-imu-relay-v32` keeps that byte-compatible default while
raising the explicit runtime range to `IMU BATCH=1..10`. At 200 Hz, batch 5
adds up to 25 ms of batching latency and yields 51 notifications/s/node
(40 IMU + 10 UWB + 1 telemetry), while batch 10 halves the IMU notification
rate and emits one 152-byte ATT payload per record. v32 also raises the SDC
controller TX packet count from 3 to 6.

v32 replaces boot-time application self-confirmation with a bounded two-command
round trip. An unconfirmed image advertises and connects but remains a test
image until the host receives `BOOT CONFIRM PREPARE`, returns the emitted token
through `BOOT CONFIRM COMMIT=<token>`, and the link stays connected/subscribed
for the five-second guard. If that contract is not completed within 180 s, the
application reboots so MCUboot can revert it. The updater only selects test
mode (`confirm=false`) and never confirms an image. The separate
`b306-v32-noconfirm-proof` marker disables the application confirmation path
while retaining the timeout, specifically to prove automatic rollback.

v20 replaced LED A's cumulative fault latch with a
five-second recent-event window. The first ten parsed outcomes form an
automatic startup baseline; `COUNTERS CLEAR` explicitly establishes the
acceptance baseline. LED B deliberately retains the Phase-C IMU health latch
until acknowledgement because a recovered sensor fault loses data. On the
physical PCB these are the `SENDING` and `PAIRED` LEDs: healthy UWB uses a
20 ms event pulse, while either fault state uses two quick flashes followed by
a long pause. Faults therefore differ from event flicker in kind, not rate.

`b306-imu-relay-v31` replaces v30's whole-queue drain phases with strict
per-record control/UWB/IMU selection, so a continuously refilled IMU queue
cannot starve the higher-priority classes. Protocol 7 grows kind 5 from 34 to
58 bytes with cumulative enqueue and producer-abort counters. Accepted
partial IMU batches are flushed before health recovery instead of silently
discarding already assigned sample sequences. Queue depths, BLE/controller
configuration, kinds 1–4, and all raw sensor records are unchanged.

v21 adds exactly one functional change: the explicit test hook
`TEST ONLY LED SENDING FAULT`. It injects at the `SENDING` indicator's
recent-fault input and replies
`TEST ONLY LED SENDING FAULT INJECTED window_ms=5000`. Normal session tooling
does not emit the `TEST ONLY` namespace. This hook verifies indicator
rendering; it neither corrupts UART bytes nor claims to exercise the
CRC/header/pairing detectors.

v22 adds exactly one functional change after the v21 State-7 hardware
finding: the `PAIRED` health-fault doublet uses two 250 ms flashes separated
by 250 ms, followed by a 1.25 s pause. The operator consistently perceived
the earlier 100 ms doublet as a single 1 Hz flash even while telemetry proved
`health_latched=1`. The already accepted `SENDING` 100 ms doublet and the
test-only command are unchanged.

v23 is measurement-only Phase-G instrumentation. At every successful
50 ms health observation after the first, it records
`signed_chip_time_delta_ms - round(B306_elapsed_us / 1000)` without changing
any detector threshold, fault class, or three-consecutive-I2C escalation
semantics. `COUNTERS CLEAR` starts a new capture distribution. Read the
capture result with `IMU DELTA=0`, `IMU DELTA=1`, and `IMU DELTA=2`.
Together the three pages report sample count, signed minimum/maximum,
maximum absolute residual, and this fixed 21-bin millisecond histogram:

```text
<=-101, -100..-51, -50..-21, -20..-11, -10..-6,
-5, -4, -3, -2, -1, 0, +1, +2, +3, +4, +5,
+6..+10, +11..+20, +21..+50, +51..+100, >=+101
```

v25 first closed the Phase-G timebase audit. The IMU sample deadline and batch
base are 64-bit. UWB frame/strobe timestamps, the UART-callback timestamp, health
timestamps, and IMU scheduling now all consume the same extended TIMER2
clock. The production image uses the natural 32-bit counter. Distinct,
test-only `b306-imu-relay-v25-t16` and `b306-imu-relay-v25-t24` markers select
accelerated 16-bit and 24-bit hardware counters so the identical expansion
path can cross many wraps before the real 71.58-minute acceptance run.

The preceding `b306-imu-relay-v24-t16` test is permanently retained as a
failed marker. Its first 65.536 ms compare reached Zephyr's unhandled IRQn 10,
halted the test image, and MCUboot reverted to v23. v25 explicitly connects
and enables the compiled nrfx TIMER2 handler in the NVIC; the accelerated
images verify that repair before production deployment.

v25 also widened kind-3's protected 10-byte prefix to 14 bytes. Its clean
85-minute rollover evidence is retained, but that byte-layout change is not an
acceptable final wire contract. v26 restores v23's exact 10-byte prefix and
82-byte maximum record. B306 keeps all scheduling and batch arithmetic at
64 bits, serialises only the low word in the protected field, and DK v13
extends it against the preceding IMU base, a full-width UWB timestamp, or
telemetry wrap count before host output. The transition reader accepts v25's
14-byte record only until v26 is installed.

`b306-imu-relay-v19` is permanently retired: two different deployed byte
sequences used that marker. Any historical `fw=b306-imu-relay-v19` line is
ambiguous without the signed-image SHA. The bytes installed immediately
before v20 have SHA-256
`35661ba2b2c9f14604779eb0fb7bdcbe55e3342419b37996510457483f70339e`.
The canonical build wrapper checks `deployed_markers.json` after signing and
rejects a retired marker or changed bytes under a previously deployed marker.

GPIO writes run only from the existing priority-5 parser worker, below the
priority-4 IMU worker. The v19 timing regression remains valid evidence for
that isolation. The read-only
`IMU LATENCY` diagnostic retains the cr1-established 788 us production-shape
baseline at 400 kHz and the proof that the temporary 100 kHz section is
restored before normal operation. The previously installed
`b306-strobe-capture-v8` signed binary is archived as
`b306-installed-v8.signed.bin` in the accepted Stage 2/4b run directory; its
SHA-256 is
`57da2011b25bab04ccfc80ab1aa0ee7cf450984ccd4ac1277d86ee7a209a425f`.
The original build's full SWD-only merged-image SHA-256 was
`4d0b7aca73d1c8e70dfeb92460c5fc09f703f143b3331feda9cef998f612f055`.
An isolated post-run rebuild passed with the same MCUboot payload hash
`94cbf3b858211209f0c5b3851dcafa0cb329d0e73b013bd103164201ad658b21`;
re-signing changed only the valid ECDSA signature. The current rebuilt signed
binary and merged image have SHA-256 `da22a7d55bb8a24c44125249d3f5df06cc85478d271c19b599c426ebe5a18be5`
and `4cc00d3ac137789b00d7e4ec18413beacf6278a7014b66cc817852dbc37b6b33`,
respectively.
It retains the larger L2CAP/ACL buffers for the shared 448-byte fast OTA path.
Historical v8 confirmation occurred after strobe capture, BLE advertising,
and UART RX started; v32 supersedes that local-readiness-only policy with the
host-observed round trip described above.

The v8 BLE OTA completed before its first terminal log was captured. A second
same-image updater run was started only to recover that log; its pre-upload
image-state read proved slot 0 was already version `0.1.7`, active and
confirmed. That second run disconnected during secondary-slot erase/upload and
was stopped after it had partially overwritten slot 1. Slot 0 was not touched;
the subsequent Stage 2/4b run exercised the confirmed v8 application for more
than five minutes. Until a later clean OTA replaces it, do not treat the
partially written secondary slot as a valid rollback image.

The board definition records UWB RX P1.01, UWB TX P1.02, ready P1.03,
I2C SDA P0.26, I2C SCL P0.27, button P0.11, and the calibrated 500 ppm LFRC.
The application overlay enables full-duplex UART1 and 400 kHz I2C0.

## Installed toolchain

- nRF Connect SDK: `v2.8.0`
- isolated NCS-toolchain west: `v1.2.0`
- workspace: `/home/zekaixiao/ncs/v2.8.0`
- toolchain: `/home/zekaixiao/ncs/toolchains/b81a7cd864`

## Reproducible build

```bash
cd /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion
./B306_Part/tools/build_firmware.sh b306-current
```

The explicit Python isolation avoids the incomplete packages in the user
Python site. The wrapper always uses a pristine tree under
`B306_Part/builds/`, then enforces FLASH <=95%, RAM <=85%, and an explicit
finite C malloc arena. The build consumes the private key path from
`sysbuild.conf`; see `../docs/dfu.md` before moving or replacing that key.

The application has no C or kernel-heap allocator callers, so both heaps are
explicitly zero. Automatic thread-analyzer reports are emitted over RTT every
60 seconds; measure those high-water marks before reducing any stack.

Primary outputs:

```text
B306_Part/builds/b306-current/merged.hex
B306_Part/builds/b306-current/firmware/zephyr/zephyr.signed.bin
B306_Part/builds/b306-current/dfu_application.zip
```

`merged.hex` is only for a human-run SWD handover. B306 updates use the signed
binary over BLE SMP. A read-only image-state query confirmed v7 in slot 0 with
`active=true` and `confirmed=true`; its MCUboot image digest is
`ebab8f7fd31c00aa5ad3272c9684e0eee210b74aa20cad874e03376b6f25eaf1`.
See `../UART_BRINGUP_REPORT.md` for the earlier UART evidence and
`../logs/strobe_attribution_5min_20260721_101455/REPORT.md` for the v8 capture
acceptance.

## Flash boundary

Do not flash a Fusion-PCB SWD target from this README. Fusion-PCB SWD is
human-only, and the first-flash command, probe identity, hashes, pre-flight
checks, post-flash observations, and rollback remain frozen in
`B306_Part/handover/b306-first-dfu-v1/`. Later B306 OTA is allowed only outside
a capture and only after stating the exact marker and image SHA.
