# B306 bootable firmware

This NCS v2.8.0 sysbuild targets the custom
`biospur_fusion_nrf52840/nrf52840` board for NINA-B306-01B. The firmware
contains:

- signed ECDSA-P256 MCUboot with equal internal-flash slots;
- mcumgr SMP over BLE with image and OS groups;
- FICR-derived `BSF%04X` advertising;
- non-blocking RTT logs and an active-low P0.13 LED heartbeat;
- receive-only 460800 8N1 UARTE ingest on P1.01;
- fixed-length v2 frame resynchronization, CRC checking, and sweep accounting;
- diagnostic UWB and telemetry BLE notifications.

It deliberately contains no IMU driver, ready-edge capture, production
batching, or fusion logic. The first-flash image was `b306-first-dfu-v1`,
version `0.1.0+0`.
The first accepted BLE-only update was `b306-stage1-ota-v2`, version
`0.1.1+0`; Stage 1 upload, real MCUboot revert, confirmation, and persistence
across reboot passed on the Fusion PCB on 2026-07-20.

The current source and installed image are `b306-uart-rx-p1.01-v7`, version
`0.1.6+0`. Its signed binary SHA-256 is
`08079df5c4c84ca845fad0a455f95221ff5e673037f6c85d65ccf5abb8fddd94`.
It retains the larger L2CAP/ACL buffers for the shared 448-byte fast OTA path
and confirms a test image only after BLE advertising and UART RX start
successfully. A failure before that health point remains unconfirmed so MCUboot
can revert it.

The board definition records UWB RX P1.01, unused UWB TX P1.02, ready P1.03,
I2C SDA P0.26, I2C SCL P0.27, button P0.11, and the calibrated 500 ppm LFRC.
The application overlay enables UART1 as receive-only on P1.01. I2C0 remains
disabled.

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
  -d B306_Part/builds/b306-uart-rx-p1.01-v7 \
  -- -DBOARD_ROOT=/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/B306_Part/firmware
```

The explicit Python isolation avoids the incomplete packages in the user
Python site. The build consumes the private key path from `sysbuild.conf`; see
`../docs/dfu.md` before moving or replacing that key.

Primary outputs:

```text
B306_Part/builds/b306-uart-rx-p1.01-v7/merged.hex
B306_Part/builds/b306-uart-rx-p1.01-v7/firmware/zephyr/zephyr.signed.bin
B306_Part/builds/b306-uart-rx-p1.01-v7/dfu_application.zip
```

`merged.hex` is only for a human-run SWD handover. B306 updates use the signed
binary over BLE SMP. A read-only image-state query confirmed v7 in slot 0 with
`active=true` and `confirmed=true`; its MCUboot image digest is
`ebab8f7fd31c00aa5ad3272c9684e0eee210b74aa20cad874e03376b6f25eaf1`.
See `../UART_BRINGUP_REPORT.md` for the OTA and bridge evidence.

## Flash boundary

Do not flash a Fusion-PCB SWD target from this README. Fusion-PCB SWD is
human-only, and the first-flash command, probe identity, hashes, pre-flight
checks, post-flash observations, and rollback remain frozen in
`B306_Part/handover/b306-first-dfu-v1/`. Later B306 OTA is allowed only outside
a capture and only after stating the exact marker and image SHA.
