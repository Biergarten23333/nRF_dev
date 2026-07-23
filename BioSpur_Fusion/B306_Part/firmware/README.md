# B306 bootable firmware

This NCS v2.8.0 sysbuild targets the custom
`biospur_fusion_nrf52840/nrf52840` board for NINA-B306-01B. The firmware
contains:

- signed ECDSA-P256 MCUboot with equal internal-flash slots;
- mcumgr SMP over BLE with image and OS groups;
- FICR-derived `BSF%04X` advertising;
- non-blocking RTT logs and an active-low P0.13 LED heartbeat;
- 460800 8N1 UARTE ingest on P1.01 and framed command TX on P1.02;
- fixed-length v2 frame resynchronization, CRC checking, and sweep accounting;
- 1 MHz TIMER2 extended to 64 bits, with natural-wrap accounting;
- dual-edge P1.03 GPIOTE -> dynamically allocated PPI -> TIMER capture;
- 20 ms strobe/frame pairing with explicit four-case verdicts; and
- JY61P 400 kHz TWIM on P0.26/P0.27, boot verification, explicit
  provisioning, continuous chip-time-aware polling, coherency checking, and
  TIMER2-timestamped 50/100/200 Hz output;
- protocol-v2 UWB, telemetry, variable kind-3 IMU, and kind-4 control replies;
  and
- a BSF-addressed writable control characteristic.

It deliberately contains no estimator or fusion logic. IMU values remain raw;
conversion, alignment analysis, and fusion stay on the host. The first-flash
image was `b306-first-dfu-v1`, version `0.1.0+0`.
The first accepted BLE-only update was `b306-stage1-ota-v2`, version
`0.1.1+0`; Stage 1 upload, real MCUboot revert, confirmation, and persistence
across reboot passed on the Fusion PCB on 2026-07-20.

The current source marker is `b306-imu-relay-v12`, version
`0.1.11-imu-relay+0`; it is not an installed-image claim until OTA and
post-reboot verification complete. The previously installed
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
It retains the larger L2CAP/ACL buffers for the shared 448-byte fast OTA path
and confirms a test image only after strobe capture, BLE advertising, and UART
RX start successfully. A failure before that health point remains unconfirmed
so MCUboot can revert it.

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
