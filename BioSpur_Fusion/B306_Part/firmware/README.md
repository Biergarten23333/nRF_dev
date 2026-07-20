# B306 first-flash firmware

This NCS v2.8.0 sysbuild targets the custom
`biospur_fusion_nrf52840/nrf52840` board for NINA-B306-01B. The first image
contains:

- signed ECDSA-P256 MCUboot with equal internal-flash slots;
- mcumgr SMP over BLE with image and OS groups;
- FICR-derived `BSF%04X` advertising;
- non-blocking RTT logs and an active-low P0.13 LED heartbeat.

It deliberately contains no IMU driver, UWB UART parser, ready-edge capture, or
fusion logic. Those are held until the first flash and a BLE-only DFU cycle
have both passed.

The board definition records UWB RX P1.01, unused UWB TX P1.02, ready P1.03,
I2C SDA P0.26, I2C SCL P0.27, button P0.11, and the calibrated 500 ppm LFRC.
UART1 and I2C0 remain disabled in this image.

## Installed toolchain

- nRF Connect SDK: `v2.8.0`
- isolated NCS-toolchain west: `v1.2.0`
- workspace: `/home/zekaixiao/ncs/v2.8.0`
- toolchain: `/home/zekaixiao/ncs/toolchains/b81a7cd864`

## Reproducible build

```bash
cd /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion
PYTHONNOUSERSITE=1 \
PYTHONPATH=/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/lib/python3.12/site-packages \
ZEPHYR_BASE=/home/zekaixiao/ncs/v2.8.0/zephyr \
ZEPHYR_TOOLCHAIN_VARIANT=zephyr \
ZEPHYR_SDK_INSTALL_DIR=/home/zekaixiao/ncs/toolchains/b81a7cd864/opt/zephyr-sdk \
/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/bin/python3 -m west \
  build --sysbuild --pristine=always \
  -b biospur_fusion_nrf52840/nrf52840 \
  -s B306_Part/firmware \
  -d B306_Part/build-b306-first-dfu \
  -- -DBOARD_ROOT=/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/B306_Part/firmware
```

The explicit Python isolation avoids the incomplete packages in the user
Python site. The build consumes the private key path from `sysbuild.conf`; see
`../docs/dfu.md` before moving or replacing that key.

Primary outputs:

```text
B306_Part/build-b306-first-dfu/merged.hex
B306_Part/build-b306-first-dfu/firmware/zephyr/zephyr.signed.bin
B306_Part/build-b306-first-dfu/dfu_application.zip
```

`merged.hex` is the first SWD image. The signed binary is copied into the
handover as `app_update.bin` for later BLE-only acceptance.

## Flash boundary

Do not flash from this README. Fusion-PCB SWD is human-only, and the executable
command, probe identity, hashes, pre-flight checks, post-flash observations,
and rollback are all frozen in `B306_Part/handover/b306-first-dfu-v1/`.
