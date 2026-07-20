# DK B306 bring-up tester

This one-shot NCS application runs only on the nRF52840 DK whose J-Link serial
is `683234364`. It actively scans for a seven-character `BSF%04X` name paired
with the Zephyr SMP service UUID, connects, requests 2M PHY and DLE, exchanges
ATT MTU, discovers the SMP service/characteristic/CCC, and subscribes.

RTT prints `B306_BRINGUP_PASS` only after the service exists, its characteristic
supports write-without-response and notify, and CCC subscription succeeds.
This is first-image BLE/SMP bring-up only. It does not test B306 UART, IMU,
ready capture, OTA image upload, or any UWB anchor.

Build from the repository root:

```bash
PYTHONNOUSERSITE=1 \
PYTHONPATH=/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/lib/python3.12/site-packages \
ZEPHYR_BASE=/home/zekaixiao/ncs/v2.8.0/zephyr \
ZEPHYR_TOOLCHAIN_VARIANT=zephyr \
ZEPHYR_SDK_INSTALL_DIR=/home/zekaixiao/ncs/toolchains/b81a7cd864/opt/zephyr-sdk \
/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/bin/python3 -m west \
  build --pristine=always -b nrf52840dk/nrf52840 \
  -s B306_Part/host/dk_bringup_tester \
  -d B306_Part/builds/dk-b306-bringup
```

Flash and observe only the authorized DK:

```bash
LD_PRELOAD=/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/lib/x86_64-linux-gnu/libffi.so.7 \
PYTHONNOUSERSITE=1 \
PYTHONPATH=/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/lib/python3.12/site-packages \
ZEPHYR_BASE=/home/zekaixiao/ncs/v2.8.0/zephyr \
ZEPHYR_TOOLCHAIN_VARIANT=zephyr \
ZEPHYR_SDK_INSTALL_DIR=/home/zekaixiao/ncs/toolchains/b81a7cd864/opt/zephyr-sdk \
/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/bin/python3 -m west \
  flash --skip-rebuild \
  -d B306_Part/builds/dk-b306-bringup/dk_bringup_tester \
  -r jlink --dev-id 683234364 --erase

LD_PRELOAD=/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/lib/x86_64-linux-gnu/libffi.so.7 \
PYTHONNOUSERSITE=1 \
PYTHONPATH=/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/lib/python3.12/site-packages \
ZEPHYR_BASE=/home/zekaixiao/ncs/v2.8.0/zephyr \
ZEPHYR_TOOLCHAIN_VARIANT=zephyr \
ZEPHYR_SDK_INSTALL_DIR=/home/zekaixiao/ncs/toolchains/b81a7cd864/opt/zephyr-sdk \
/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/bin/python3 -m west \
  rtt -d B306_Part/builds/dk-b306-bringup/dk_bringup_tester \
  -r jlink --dev-id 683234364
```
