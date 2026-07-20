# Human handover 2: B306 signed first image

This procedure flashes the NINA-B306 nRF52840 on the Fusion PCB. It does not
flash the DWM1001C, the Fusion Master DK, or any deployed wand tag.

The J-Link OB probe is serial `1050070698`. This target is human-only: Codex
has not executed any command in this document.

## Artifacts

First SWD image:

```text
/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/B306_Part/handover/b306-first-dfu-v1/artifacts/merged.hex
SHA-256 d4392a2eec99d91b2fbcd97e321b76cb552d49419b3ea0914705742648c60f82
```

Signed application for the later BLE-only DFU acceptance:

```text
/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/B306_Part/handover/b306-first-dfu-v1/artifacts/app_update.bin
SHA-256 578e62a705218c1e1591406ae3931fdce2a45e432a3bf006a82bdfb4e73dadf6
```

The included `dfu_application.zip` has SHA-256
`a5dd7d3a478e46eb9bad019f9c90f1d8879c9f348391c4a1f000cec7d7b2bf58`;
it is retained as NCS build output but is not used by this SWD command.

## Permanent first-flash facts

MCUboot signing algorithm: ECDSA P-256.

SHA-256 fingerprint of the DER SubjectPublicKeyInfo:

```text
0e525dedaa7f50fb38d3c8f1792cacaa20f70204aa46ef6b50d720479c6ef5a2
```

Generated and frozen 1 MiB flash layout:

| Region | Start | End | Size |
|---|---:|---:|---:|
| MCUboot | `0x000000` | `0x00C000` | 48 KiB |
| MCUboot pad | `0x00C000` | `0x00C200` | 512 B |
| Application payload | `0x00C200` | `0x086000` | 487.5 KiB |
| Primary slot, including pad | `0x00C000` | `0x086000` | 488 KiB |
| Secondary slot | `0x086000` | `0x100000` | 488 KiB |

The exact map is included as `pm_static.yml` with SHA-256
`8a0bd54788224848390cf628c38a804e6cf172b7e4ddae7633153d33e213ed09`.

## Pre-flight checklist

1. Stop any capture.
2. Confirm J-Link serial `1050070698` is physically wired to the Fusion PCB
   B306 SWD pads, not to the DWM1001C and not to the probe's on-board target.
3. Power the Fusion PCB and share ground between probe and board.
4. Confirm the selected target is nRF52840/NINA-B306.
5. Recompute the `merged.hex` SHA-256 and match it exactly.
6. Confirm the protected private-key backup exists and recomputes the
   `0e525d...f5a2` fingerprint above. Do not regenerate the key.
7. Read `pm_static.yml` and explicitly confirm the five flash regions above.
   Do not change the layout.
8. Confirm that a full erase is intended. This is the first B306 flash and the
   command contains `--erase`.

## Flash command

Run exactly:

```bash
cd /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion
LD_PRELOAD=/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/lib/x86_64-linux-gnu/libffi.so.7 \
PYTHONNOUSERSITE=1 \
PYTHONPATH=/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/lib/python3.12/site-packages \
ZEPHYR_BASE=/home/zekaixiao/ncs/v2.8.0/zephyr \
ZEPHYR_TOOLCHAIN_VARIANT=zephyr \
ZEPHYR_SDK_INSTALL_DIR=/home/zekaixiao/ncs/toolchains/b81a7cd864/opt/zephyr-sdk \
/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/bin/python3 -m west \
  flash --skip-rebuild \
  -d B306_Part/build-b306-first-dfu/firmware \
  -r jlink \
  --dev-id 1050070698 \
  --erase \
  -f /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/B306_Part/handover/b306-first-dfu-v1/artifacts/merged.hex
```

## Post-flash verification

Observe in this order:

1. MCUboot starts over RTT and selects the primary image.
2. The application RTT log includes
   `firmware=b306-first-dfu-v1 identity=0x.... name=BSF....`.
3. The application reports `BLE SMP advertising started as BSF....`.
4. A BLE scanner sees the same seven-character `BSF%04X` name and the Zephyr
   SMP service UUID `8D53DC1D-1DB7-4CD3-868B-8A527460AA84`.
5. The active-low P0.13 status LED changes state every 500 ms.

This image intentionally produces no UWB UART, ready capture, or IMU traffic.
If MCUboot runs but advertising does not appear, suspect the LF clock setup
first: NINA-B306-01B has no LFXO and this image selects calibrated 500 ppm
LFRC. Report exactly which RTT lines, LED behavior, and BLE observations were
present, then stop. Do not make or flash a revised image.

## Rollback/recovery

There is no earlier known-good B306 application to restore. For a failed or
partially programmed first flash, the recovery baseline is the same complete
`merged.hex` above, SHA-256
`d4392a2eec99d91b2fbcd97e321b76cb552d49419b3ea0914705742648c60f82`.
After checking wiring and power, rerun the exact flash command once. If the
same unexpected result repeats, stop and report it; do not iterate images.

After a later unconfirmed BLE test update, MCUboot's expected rollback is the
previous confirmed primary image and does not require SWD.
