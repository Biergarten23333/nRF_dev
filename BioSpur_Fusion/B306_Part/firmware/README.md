# B306 bootable firmware

This NCS v2.8.0 sysbuild targets the custom
`biospur_fusion_nrf52840/nrf52840` board for NINA-B306-01B. The Stage 1 image
contains:

- signed ECDSA-P256 MCUboot with equal internal-flash slots;
- mcumgr SMP over BLE with image and OS groups;
- FICR-derived `BSF%04X` advertising;
- non-blocking RTT logs and an active-low P0.13 LED heartbeat.

It deliberately contains no IMU driver, UWB UART parser, ready-edge capture, or
fusion logic. The first-flash image was `b306-first-dfu-v1`, version `0.1.0+0`.
The first accepted BLE-only update was `b306-stage1-ota-v2`, version
`0.1.1+0`; Stage 1 upload, real MCUboot revert, confirmation, and persistence
across reboot passed on the Fusion PCB on 2026-07-20.

The current source and installed image are `b306-fast-ota-v4`, version
`0.1.3+0`. The image raises the L2CAP MTU and ACL buffers for the shared
448-byte fast OTA path and confirms a test image only after BLE/SMP advertising
starts successfully. A failure before that health point remains unconfirmed so
MCUboot can revert it.

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
  -d B306_Part/builds/b306-fast-ota-v4 \
  -- -DBOARD_ROOT=/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/B306_Part/firmware
```

The explicit Python isolation avoids the incomplete packages in the user
Python site. The build consumes the private key path from `sysbuild.conf`; see
`../docs/dfu.md` before moving or replacing that key.

Primary outputs:

```text
B306_Part/builds/b306-fast-ota-v4/merged.hex
B306_Part/builds/b306-fast-ota-v4/firmware/zephyr/zephyr.signed.bin
B306_Part/builds/b306-fast-ota-v4/dfu_application.zip
```

`merged.hex` is only for a human-run SWD handover. B306 updates use the signed
binary over BLE SMP. The installed and confirmed v4 signed binary has SHA-256
`f23b0a12f7652f64e0154fb97238a72bcd57604913c1f5cc59b75e1d0e7bcae9`;
see `../docs/dfu.md` for the exact OTA and read-only post-reset evidence.

## Flash boundary

Do not flash a Fusion-PCB SWD target from this README. Fusion-PCB SWD is
human-only, and the first-flash command, probe identity, hashes, pre-flight
checks, post-flash observations, and rollback remain frozen in
`B306_Part/handover/b306-first-dfu-v1/`. Later B306 OTA is allowed only outside
a capture and only after stating the exact marker and image SHA.
