# Human handover: DWM1001C Task A RAM-fixed image

This procedure replaces the boot-looping Task A v2 image on the Fusion PCB's
DWM1001C nRF52832. It does not flash B306, the Fusion Master DK, or any
deployed wand tag.

The J-Link OB probe is serial `1050070698`. This target is human-only: Codex
has not run the flash or rollback commands below. Every command selects the
probe explicitly; if any J-Link probe-selection dialog appears, cancel it and
stop.

## Artifacts

Complete SWD image to flash now:

```text
/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/UWB_Part/fusion-link/tag/tag-fusion-link-v2-ramfix1.merged.hex
SHA-256 12d4c587c2fae44b1469baf5260961522119eeeffa9c74e25d330f1b0523b869
```

Application-only files for a later OTA workflow, not for this SWD command:

```text
/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/UWB_Part/fusion-link/tag/tag-fusion-link-v2-ramfix1.signed.bin
SHA-256 d8600871f402b4d5a7d0fb4df97e52d02a18f2eccf886934f3b48af70949750e

/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/UWB_Part/fusion-link/tag/tag-fusion-link-v2-ramfix1.dfu_application.zip
SHA-256 1cdf9cca2e1629d09bb7f9c44de0fecd0e780c9ef269643a3f1bd460483a6017
```

Rollback complete image:

```text
/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/UWB_Part/fusion-link/tag/tag-freeze-clean-20260716.merged.hex
SHA-256 8405cf0506400bc7085c3498d4413fe06ea2eb2e7c5836e75bc2f81ceba53186
```

## Pre-flight checklist

1. Stop capture. Leave deployed wand tags BS9336, BS955A, and BSCCF4
   untouched.
2. Confirm probe `1050070698` is wired to the Fusion PCB **DWM1001C SWD pads**,
   not B306 and not the probe's on-board target.
3. Power the Fusion PCB and share ground with the probe.
4. Confirm the target is nRF52832/DWM1001C.
5. Run the checksum command below and compare every value byte-for-byte.
6. Confirm a full erase is intended. The command contains `--erase` because
   the merged image replaces MCUboot, both application layout assumptions, and
   the current invalid application in one known state.
7. Confirm the unchanged MCUboot public-key fingerprint:
   `a14bcb1bf9bb821146ba32838217e476f5412621320534ffe490a1890c994660`
   (SHA-256 of DER SubjectPublicKeyInfo). The inherited NCS sample-key PEM SHA
   is
   `1fc912d30251b821f251e127d4daf7ba9338dd5c04e5af100abfb5b7c7d4c022`.
8. Confirm the generated internal-flash layout:

```text
MCUboot          0x00000..0x0BFFF  size 0x0C000
MCUboot pad      0x0C000..0x0C1FF  size 0x00200
primary app      0x0C200..0x45FFF  size 0x39E00
primary slot     0x0C000..0x45FFF  size 0x3A000
secondary slot   0x46000..0x7FFFF  size 0x3A000
SRAM             0x20000000..0x2000FFFF 64 KiB
```

The replacement application passed FLASH
`210944/228864 = 92.17%` and RAM `52640/65536 = 80.32%`.

Verify checksums:

```bash
cd /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion
sha256sum \
  UWB_Part/fusion-link/tag/tag-fusion-link-v2-ramfix1.merged.hex \
  UWB_Part/fusion-link/tag/tag-fusion-link-v2-ramfix1.signed.bin \
  UWB_Part/fusion-link/tag/tag-fusion-link-v2-ramfix1.dfu_application.zip \
  UWB_Part/fusion-link/tag/tag-freeze-clean-20260716.merged.hex
```

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
  -d UWB_Part/builds/tag-fusion-link-ramfix1/tag \
  -r jlink \
  --dev-id 1050070698 \
  --erase \
  -f /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/UWB_Part/fusion-link/tag/tag-fusion-link-v2-ramfix1.merged.hex
```

## Post-flash verification

Capture at least 75 seconds of RTT so application startup and the first
60-second thread-analyzer report are both visible:

```bash
cd /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion
mkdir -p UWB_Part/logs/dwm_task_a_ramfix1_boot_20260720
timeout 75s JLinkRTTLogger \
  -Device NRF52832_XXAA \
  -If SWD \
  -Speed 4000 \
  -USB 1050070698 \
  -RTTAddress 0x20000410 \
  -RTTChannel 0 \
  UWB_Part/logs/dwm_task_a_ramfix1_boot_20260720/rtt.log
```

Expected observations:

1. MCUboot boots an image instead of repeating a swap/failure loop.
2. The application reports:

```text
Tag firmware marker: tag-fusion-link-v2-ramfix1
Tag app ready bs=BS....
```

3. No early bus fault or reset loop appears.
4. The tag advertises as its FICR-derived `BS%04X` identity.
5. After a BLE connection, `BSLSTAT;1` includes `ci`, `lat`, `sup`, `reqci`,
   `ciok`, and `cpmode`. Capture-mode acceptance is `cpmode=CAP`,
   `reqci=350`, `ci=350`, `ciok=1`.
6. At about 60 seconds, the thread analyzer prints per-thread stack usage.
7. Once the Tag Master supplies a valid ranging configuration, `gen`, `done`,
   and `strobe` counters increase. UART/strobe silence before ranging is
   configured is not itself a flash failure.

After a stable boot, test B306 P1.01 first. Only if a live DWM transmitter
still produces zero electrical bytes should P1.02 be retried. The old
P1.01/P1.02 comparison is invalid because v2 never reached application code.

Any missing marker, MCUboot rejection, reset, unexpected identity, `ciok=0`,
or missing analyzer report is the handover finding. Stop and report the
complete RTT log and observations; do not immediately build or flash a revised
image.

## Rollback

Rollback mass-erases the target and restores the frozen complete image:

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
  -d UWB_Part/builds/tag-fusion-link-ramfix1/tag \
  -r jlink \
  --dev-id 1050070698 \
  --erase \
  -f /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/UWB_Part/fusion-link/tag/tag-freeze-clean-20260716.merged.hex
```

Rollback SHA-256:
`8405cf0506400bc7085c3498d4413fe06ea2eb2e7c5836e75bc2f81ceba53186`.
