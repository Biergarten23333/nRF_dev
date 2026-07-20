# B306 first-flash checkpoint

Status: board definition, signed minimal image, frozen partition ABI, and both
human handovers are complete. No probe was contacted for flashing, recovery,
or target inspection.

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

The final pristine NCS v2.8.0 sysbuild completed without Kconfig or CMake
warnings. It uses ECDSA P-256 MCUboot, BLE SMP image/OS groups, non-blocking
RTT, secure-boot flash-patch hardening, identity advertising, and the LED
heartbeat. A config audit confirms UART and I2C ingest are disabled.

The signed application validates with the private signing key. Its MCUboot TLV
key hash is
`0e525dedaa7f50fb38d3c8f1792cacaa20f70204aa46ef6b50d720479c6ef5a2`,
matching the recorded public-key fingerprint.

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

## Still unknown

- The human-observed MCUboot, RTT, LED, LFRC/BLE, UART, and strobe results.
- A complete BLE-only upload/test/revert/confirm cycle and the workstation host
  command sequence for it.
- Runtime stack high-water marks.
- Whether the fitted JY61P acknowledges at `0x50`, accepts the 200 Hz rate, and
  has the expected axis signs.
- Timer implementation and wrap behavior, UART/strobe pairing, clock-filter
  residual, and all later capture measurements.

Dependent work is stopped pending the human report from both handovers.
