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
- uses active scanning because B306 publishes its exact name and marker in the
  scan response, and permits that response to initiate a connection only after
  the exact identity gate passes;
- waits for the B306 MTU exchange callback before starting DFU discovery;
- skips the UWB NUS preparation stage;
- requires an exact `BSF%04X` target and auto-starts the one-shot update;
- supports `B306_OTA_VERIFY_ONLY=1`, which reads image state without erase,
  upload, pending/test, or reset commands.

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
B306_OTA_MARKER=b306-fast-ota-v4 \
B306_OTA_IMAGE="$PWD/B306_Part/builds/b306-fast-ota-v4/firmware/zephyr/zephyr.signed.bin" \
B306_OTA_IMAGE_SHA256=f23b0a12f7652f64e0154fb97238a72bcd57604913c1f5cc59b75e1d0e7bcae9 \
PYTHONNOUSERSITE=1 \
PYTHONPATH=/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/lib/python3.12/site-packages \
ZEPHYR_BASE=/home/zekaixiao/ncs/v2.8.0/zephyr \
ZEPHYR_TOOLCHAIN_VARIANT=zephyr \
ZEPHYR_SDK_INSTALL_DIR=/home/zekaixiao/ncs/toolchains/b81a7cd864/opt/zephyr-sdk \
/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/bin/python3 -m west build \
  --pristine=always \
  -b nrf52840dk/nrf52840 \
  -s B306_Part/host/dk_ota \
  -d B306_Part/builds/dk-ota-v4
```

That exact pristine build passed on 2026-07-20. Its build-only DK
`merged.hex` SHA-256 is:

```text
c3eddb626faf4436f5b86cfe6121f9eabf470115b90a00d8588e8bb5f322b96e
```

The v2-to-v3 update on 2026-07-20 negotiated ATT MTU 247, so the shared core
automatically reduced the transfer chunks. After v3 was installed and
confirmed, it negotiated ATT MTU 498 and used the 448-byte fast path. A
subsequent same-image re-upload completed in about 9.4 seconds; MCUboot
correctly refused to schedule the already-active identical digest for another
test swap.

The genuine v3-to-v4 re-OTA then transferred 170,419 bytes in about 9.2 seconds,
scheduled v4 for test, and remotely reset B306. After the physical reset
check, verify-only mode read v4 in slot 0 with `active=true` and
`confirmed=true`; v3 remained inactive in slot 1. The current verify-only DK
build is `B306_Part/builds/dk-ota-v4-verify/merged.hex`, SHA-256
`1ec475dab146efcdcc4a81e42ff5434bd97253723a455be7a0a8b3631a5fe061`.

Before an actual update, verify no capture is active and state the same marker
and SHA to the operator. Flash only the Fusion Master DK, always with explicit
`--dev-id 683234364`. Never use this client for DWM1001C payloads or a
Fusion-PCB SWD target.
