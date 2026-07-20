# B306 first-flash checkpoint

Status: board definition, signed minimal image, frozen partition ABI, both
human handovers, B306 first-image BLE/SMP bring-up, and the complete BLE-only
upload/revert/confirm cycle are complete. The human reported that the B306
first flash completed. Codex did not access either Fusion-PCB probe; it flashed
and observed only the authorized nRF52840 DK probe `683234364`.

## Board facts encoded

| Net | NINA pad | nRF52840 | First-image state |
|---|---:|---|---|
| `UWB_RX1` | GPIO_35 | P1.01 | Pinctrl recorded; UART1 disabled |
| `UWB_TX1` | GPIO_36 | P1.02 | Pinctrl recorded; UART1 disabled |
| `UWB_RDY` | GPIO_37 | P1.03 | GPIO recorded; unused |
| JY61P SDA | GPIO_42 | P0.26 | Pinctrl recorded; I2C0 disabled |
| JY61P SCL | GPIO_44 | P0.27 | Pinctrl recorded; I2C0 disabled |
| `BUTTON_1` | GPIO_32 | P0.11 | Active-low input |
| status LED | GPIO_1 | P0.13 | Active-low heartbeat |

NINA-B306-01B has no LFXO. MCUboot and the application resolve to LFRC,
500 ppm, with `CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC_CALIBRATION=y`.

## Image

Marker: `b306-first-dfu-v1`, version `0.1.0+0`.
The handover build embeds source commit `b306bba05f8e`.

The final pristine NCS v2.8.0 sysbuild completed without Kconfig or CMake
warnings. It uses ECDSA P-256 MCUboot, BLE SMP image/OS groups, non-blocking
RTT, secure-boot flash-patch hardening, identity advertising, and the LED
heartbeat. A config audit confirms UART and I2C ingest are disabled.

The signed application validates with the private signing key. Its MCUboot TLV
key hash is
`0e525dedaa7f50fb38d3c8f1792cacaa20f70204aa46ef6b50d720479c6ef5a2`,
matching the recorded public-key fingerprint.

Final handover artifact hashes:

```text
merged.hex     d4392a2eec99d91b2fbcd97e321b76cb552d49419b3ea0914705742648c60f82
app_update.bin 578e62a705218c1e1591406ae3931fdce2a45e432a3bf006a82bdfb4e73dadf6
```

## Frozen layout

| Region | Start | End | Size |
|---|---:|---:|---:|
| MCUboot | `0x000000` | `0x00C000` | 48 KiB |
| MCUboot pad | `0x00C000` | `0x00C200` | 512 B |
| Application payload | `0x00C200` | `0x086000` | 487.5 KiB |
| Primary image slot | `0x00C000` | `0x086000` | 488 KiB |
| Secondary image slot | `0x086000` | `0x100000` | 488 KiB |
| SRAM | `0x20000000` | `0x20040000` | 256 KiB |

The pristine static rebuild reproduced the original dynamic Partition Manager
output byte-for-byte after the two explanatory comment lines.

## Build resource use

| Image | Resource | Used | Capacity | Used | Free |
|---|---|---:|---:|---:|---:|
| Application | Flash payload region | 169,556 B | 499,200 B | 33.97% | 329,644 B |
| Application | RAM | 48,692 B | 262,144 B | 18.57% | 213,452 B |
| MCUboot | Flash partition | 31,504 B | 49,152 B | 64.10% | 17,648 B |
| MCUboot | RAM | 17,728 B | 262,144 B | 6.76% | 244,416 B |

Configured application stacks include main 1,536 B, system workqueue 2,304 B,
log-processing 1,024 B, and mcumgr transport workqueue 4,096 B. Actual
remaining stack headroom is `UNKNOWN` until the image runs on hardware; global
free RAM is not presented as stack headroom. Runtime stack measurement was not
added because the first image is intentionally minimal.

## DK-observed BLE/SMP bring-up

On 2026-07-20, the authorized DK probe `683234364` was flashed with the
one-shot tester marker `dk-b306-bringup-v1`. Its `merged.hex` SHA-256 was:

```text
c99fb6bb1c8daed12fe5a116703e324f8525712e850f0d6eacd3a0715e712afb
```

The tester found the human-flashed Fusion PCB as `BSF3C79` with RSSI
`-57 dBm`, connected, negotiated an ATT MTU of 247 bytes and 251-byte DLE in
both directions, and switched both TX and RX to 2M PHY. It discovered the SMP
primary service at handles 16--19 and verified that its characteristic supports
write-without-response and notify. CCC subscription succeeded, producing:

```text
B306_BRINGUP_PASS name=BSF3C79 rssi=-57 smp_service=1 smp_write_cmd=1 smp_notify=1 mtu=247
```

The raw RTT record is under
`logs/b306_ble_bringup_20260720_151733/raw_rtt.log`. This confirms that the
application boots, the LFRC configuration supports BLE operation on the board,
the required identity advertisement is present, and the BLE SMP endpoint is
discoverable and subscribable. It is not an OTA upload/revert/confirm test.

## BLE-only DFU acceptance

On 2026-07-20, the DK ran a one-shot Stage 1 acceptance harness. It pushed this
signed application to B306 over BLE SMP:

```text
marker                 b306-stage1-ota-v2
version                0.1.1+0
signed-bin SHA-256     7f821fbf26144026c0ff8912118a3d3f098ec29ce9567633b5673df12425db02
MCUboot image digest   8c695e2d49c97aab5692c69ac8447189ecf0e4d73b8d1129917ff3cd8f36c1dc
DK merged.hex SHA-256  3def096e9fbb9b56c0c1fd31f1c29ded1e2d7fa827d6603fbe06cd2be3d5b2bf
```

The first trial showed `0.1.1` active and unconfirmed, then intentionally reset
without confirmation. MCUboot restored `0.1.0` active and confirmed. The second
trial uploaded the same pinned image, marked it for test, confirmed it through
SMP, rebooted, and showed `0.1.1` still active and confirmed. The final device
identity remained `BSF3C79`; ATT MTU was 247 and the v2 advertising marker was
present.

```text
B306_STAGE1_OTA_PASS name=BSF3C79 marker=b306-stage1-ota-v2 version=0.1.1+0 file_sha=7f821fbf26144026c0ff8912118a3d3f098ec29ce9567633b5673df12425db02 image_digest=8c695e2d49c97aab5692c69ac8447189ecf0e4d73b8d1129917ff3cd8f36c1dc
```

The acceptance record is
`logs/b306_stage1_ota_20260720_155326/rtt_acceptance.log`. No capture process
was active, and no Fusion-PCB SWD interface was touched. Stage 1 is accepted;
at the end of that acceptance run the installed B306 image was `0.1.1+0`,
active and confirmed.

The acceptance-only harness was then removed. The maintained updater is
`host/dk_ota/`, which build-time imports the SHA-pinned fast OTA core from the
read-only UWB FREEZE instead of carrying a second SMP implementation. It
installed and self-confirmed `b306-fast-ota-v3`, version `0.1.2+0`, on
2026-07-20. A subsequent real re-OTA installed and self-confirmed
`b306-fast-ota-v4`, version `0.1.3+0`; artifact hashes and both OTA records are
in `docs/dfu.md`.

## Still unknown

- The human-observed B306 MCUboot banner, application RTT marker, and LED
  heartbeat. LFRC/BLE operation is independently confirmed by the DK test.
- Runtime stack high-water marks.
- Whether the fitted JY61P acknowledges at `0x50`, accepts the 200 Hz rate, and
  has the expected axis signs.
- Timer implementation and wrap behavior, UART/strobe pairing, clock-filter
  residual, and all later capture measurements.

The first image intentionally has UART, I2C, and ready capture disabled, so
online anchors cannot be exercised by this image. Those paths require later
versioned firmware and measurement work.
