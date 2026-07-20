# Human handover 1: DWM1001C Task A image

This procedure flashes the DWM1001C nRF52832 on the Fusion PCB. It does not
flash B306, the Fusion Master DK, or any deployed wand tag.

The J-Link OB probe is serial `1050070698`. This target is human-only: Codex
has not executed any command in this document.

## Artifacts

First image:

```text
/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/UWB_Part/fusion-link/tag/tag-fusion-link-v2.merged.hex
SHA-256 53986bc713a5e75dcbe5b1e28d286b692250adff1faee9863041a780e4234758
```

Rollback to the frozen baseline:

```text
/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/UWB_Part/fusion-link/tag/tag-freeze-clean-20260716.merged.hex
SHA-256 8405cf0506400bc7085c3498d4413fe06ea2eb2e7c5836e75bc2f81ceba53186
```

Both are complete merged images. Do not substitute a signed application binary
for this first SWD flash.

## Pre-flight checklist

1. Stop any capture and leave deployed wand tags BS9336, BS955A, and BSCCF4
   untouched.
2. Confirm J-Link serial `1050070698` is physically wired to the Fusion PCB
   DWM1001C SWD pads, not to B306 and not to the probe's on-board target.
3. Power the Fusion PCB and share ground between probe and board.
4. Confirm the selected target is nRF52832/DWM1001C.
5. Recompute both SHA-256 values and compare them byte-for-byte with the values
   above.
6. Confirm that a full erase is intended. This is the first flash of this
   DWM1001C and the command contains `--erase`.

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
  -d UWB_Part/builds/tag-fusion-link/tag \
  -r jlink \
  --dev-id 1050070698 \
  --erase \
  -f /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/UWB_Part/fusion-link/tag/tag-fusion-link-v2.merged.hex
```

## Post-flash verification

With the same probe still connected, inspect RTT. The application must report:

```text
Tag firmware marker: tag-fusion-link-v2
Tag app ready bs=BS....
```

The tag must also advertise with its `BS%04X` identity. Once the normal tag
configuration and ranging schedule are present, a logic analyser at the
Fusion PCB's 0 Ω resistors must see:

- one nominal 10 us active-high `UWB_RDY` pulse per broadcast poll epoch; and
- fixed 96-byte UART v2 frames at 460800 8N1 on `UWB_RX1`, after completed
  sweeps.

UART/strobe silence before the Tag Master has provided a valid ranging
configuration is not by itself a flash failure. A missing marker, boot failure,
unexpected BLE identity, or wrong waveform after ranging is active is a
finding: stop and report exactly what was observed. Do not make or flash a
revised image.

## Rollback

Rollback mass-erases the target and reinstalls the frozen complete image:

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
  -d UWB_Part/builds/tag-fusion-link/tag \
  -r jlink \
  --dev-id 1050070698 \
  --erase \
  -f /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/UWB_Part/fusion-link/tag/tag-freeze-clean-20260716.merged.hex
```

Rollback artifact SHA-256:
`8405cf0506400bc7085c3498d4413fe06ea2eb2e7c5836e75bc2f81ceba53186`.
