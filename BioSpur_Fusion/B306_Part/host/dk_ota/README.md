# Fusion Master fast OTA

This is the only maintained B306 OTA client. It does not contain a second SMP
implementation: the build copies the frozen, proven UWB fast updater from
`UWB_Part/2026-07-15-FREEZE/firmware/src/apps/master_ota/src/main.c`, verifies
its SHA-256, and applies the small `patches/b306_profile.patch` adapter in the
build directory.

The shared core supplies the 448-byte maximum chunk, MTU-aware downshift, 2M
PHY request, 7.5 ms connection parameters, SMP response handling, retry policy,
secondary-slot erase, test scheduling, and OS reset. The B306 adapter only:

- uses the real `BSF%04X` advertising name instead of the UWB manufacturer-data
  identity format;
- skips the UWB NUS preparation stage;
- requires an exact `BSF%04X` target and auto-starts the one-shot update.

The frozen source remains read-only. Its pinned SHA-256 is:

```text
main.c       9613d746a102afa9e0ea5943e1ea0074bd24b3445051be0fc2c2a51a1a880906
master_ota.h b30d1e3635b4ab1e00c2c3cad145564c5742f24c7cc6dbd194dfb488af611012
```

Every build requires the exact target, marker, image path, and file SHA. For
example, this only compiles an updater; it does not flash or transmit:

```bash
cd /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion

B306_OTA_TARGET_NAME=BSF3C79 \
B306_OTA_MARKER=b306-fast-ota-v3 \
B306_OTA_IMAGE="$PWD/B306_Part/build-b306-fast-ota-v3/firmware/zephyr/zephyr.signed.bin" \
B306_OTA_IMAGE_SHA256=11882aa3b8cde5d1c88418002bd019832ad501a2af175cdc1b5f0f023661113b \
PYTHONNOUSERSITE=1 \
PYTHONPATH=/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/lib/python3.12/site-packages \
ZEPHYR_BASE=/home/zekaixiao/ncs/v2.8.0/zephyr \
ZEPHYR_TOOLCHAIN_VARIANT=zephyr \
ZEPHYR_SDK_INSTALL_DIR=/home/zekaixiao/ncs/toolchains/b81a7cd864/opt/zephyr-sdk \
/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/bin/python3 -m west build \
  --pristine=always \
  -b nrf52840dk/nrf52840 \
  -s B306_Part/host/dk_ota \
  -d B306_Part/build-dk-ota-v3
```

That exact pristine build passed on 2026-07-20. Its build-only DK
`merged.hex` SHA-256 is:

```text
995c34c5e388bfd94ba8bebd2075d293cdc802456af861bb04b78f513b6b0546
```

The currently installed B306 v2 image advertises an ATT MTU of 247, so the
shared core will automatically reduce the first v3 transfer from its 448-byte
maximum wherever the encoded SMP request does not fit. The v3 target raises
its L2CAP MTU and ACL buffers to 498/502 bytes; later updates can use the full
fast path after negotiation.

Before an actual update, verify no capture is active and state the same marker
and SHA to the operator. Flash only the Fusion Master DK, always with explicit
`--dev-id 683234364`. Never use this client for DWM1001C payloads or a
Fusion-PCB SWD target.
