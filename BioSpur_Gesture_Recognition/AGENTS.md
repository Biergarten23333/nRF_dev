# Agent notes — BioSpur_Gesture_Recognition

## BioSpur-GR B120 USB CDC port

The computer-facing B120 central for this Gesture Recognition project is:

- Stable by-id path after the BioSpur-GR USB rename:
  `/dev/serial/by-id/usb-BioSpur-GR_BioSpur-GR_51D4A5716A4C5551-if00`
- Port: usually `/dev/ttyACM0` or `/dev/ttyACM3`; this can move after replug/reflash.
- USB manufacturer/product/description: `BioSpur-GR`
- VID:PID: `2FE3:10F2`
- Serial: `51D4A5716A4C5551`

Use this port for current BioSpur-GR/B120 tests. It accepts line commands such
as:

```text
status
scan
stop
help
```

Do **not** confuse it with these other ports:

- `/dev/ttyACM0` and `/dev/ttyACM12`: `BioSpur_BLE_Control`,
  `VID:PID=2FE3:1002`; these may belong to the UWB/older BioSpur setup.
- `/dev/ttyACM1`, `/dev/ttyACM2`, `/dev/ttyACM8`, `/dev/ttyACM9`,
  `/dev/ttyACM10`: SEGGER/J-Link CDC ports.

When testing this project, identify the B120 by `BioSpur-GR`,
`VID:PID=2FE3:10F2`, and serial `51D4A5716A4C5551`, not by a generic
`BioSpur` manufacturer string or by a moving `/dev/ttyACM*` number.

## GR module firmware update rule: OTA only

The first GR module MCUboot-capable image was already flashed by J-Link on
2026-06-12. From now on, do **not** directly J-Link flash the GR module during
normal work.

Normal GR module updates must use BLE OTA through the BioSpur-GR/B120 bridge:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_Gesture_Recognition
scripts/build_gr_module.sh
scripts/embed_gr_ota_image.sh
scripts/build_biospur_gr_b120.sh
GR_JLINK_SNR=1050070698
scripts/jlink_flash_nrf5340_dualcore_by_snr.sh "$GR_JLINK_SNR" build/central_b120 1000
scripts/ota_gr_module_via_b120.py
```

Direct J-Link flashing of the GR nRF52840 is emergency recovery only: use it
only if OTA/bootloader recovery is broken and the user explicitly approves
connecting the physical probe to the GR module. The emergency script refuses to
run unless `GR_EMERGENCY_DIRECT_FLASH=YES` is set.

Be especially careful around OTA/DFU code. Do not make changes that can strand
the GR module without a working MCUboot/SMP/NUS recovery path.

## J-Link flashing rules for this project

Do **not** use `nrfjprog` or `west flash` for this project. Use repository
J-Link scripts with an explicit serial number.

Current temporary GR bench J-Link SNR:

- `1050070698`

Probe discovery:

```bash
scripts/jlink_show_emulators.sh
```

B120 build and LFRC check:

```bash
scripts/build_biospur_gr_b120.sh
scripts/assert_b120_internal_osc_build.sh build/central_b120
```

B120 flash:

```bash
GR_JLINK_SNR=1050070698
scripts/jlink_flash_nrf5340_dualcore_by_snr.sh "$GR_JLINK_SNR" build/central_b120 1000
```

Reference only from the UWB system: `960148546` was `Master_Anchor`, and
`1050070698` was `Master_Tag`. The UWB Tag/Anchor body update rules are
separate from this GR project; do not mix the port/probe assumptions.

## Mechanical glove USB serial port

The ACEBOTT ESP32-WROOM-32E mechanical glove uses a CH340 USB serial adapter.
It normally appears as:

- Port: `/dev/ttyUSB0`
- VID:PID: `1A86:7523`
- Description: `USB Serial`

This is separate from the B120 and SEGGER/J-Link ports.
