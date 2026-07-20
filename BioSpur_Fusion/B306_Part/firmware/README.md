# B306 firmware

This directory is a minimal Zephyr application for the nRF52840 DK, used as the
development twin of the u-blox NINA-B306. It only logs a startup message over
RTT and toggles the DK's `led0` once per second.

It deliberately contains no JY61P driver, DWM1001C UART parser, GPIO capture,
BLE service, MCUboot configuration, or fusion logic.

## Installed toolchain

- nRF Connect SDK: `v2.8.0`
- user-environment west: `v1.5.0`
- isolated NCS-toolchain west used for the verified build: `v1.2.0`
- workspace: `/home/zekaixiao/ncs/v2.8.0`
- toolchain: `/home/zekaixiao/ncs/toolchains/b81a7cd864`

## Build

```bash
cd /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion
PYTHONNOUSERSITE=1 \
PYTHONPATH=/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/lib/python3.12/site-packages \
ZEPHYR_BASE=/home/zekaixiao/ncs/v2.8.0/zephyr \
ZEPHYR_TOOLCHAIN_VARIANT=zephyr \
ZEPHYR_SDK_INSTALL_DIR=/home/zekaixiao/ncs/toolchains/b81a7cd864/opt/zephyr-sdk \
/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/bin/python3 -m west \
  build --pristine=always \
  -b nrf52840dk/nrf52840 \
  -s B306_Part/firmware \
  -d build-b306-smoke
```

The older board spelling `nrf52840dk_nrf52840` is deprecated in NCS v2.8.0;
the qualified spelling above targets the same SoC.

The explicit Python isolation avoids a broken user-site combination in which
`pykwalify` is found but its `dateutil` dependency is not. The command above
completed successfully on this workstation.

## Flash and debug

Use west with an explicit probe ID when more than one J-Link is connected:

```bash
west flash -d build-b306-smoke --dev-id <DK_JLINK_SNR>
```

Read the startup log through RTT. Do not flash any DWM1001C from this build:
the DK/B306 and DWM1001C are independent MCUs and independent targets.
