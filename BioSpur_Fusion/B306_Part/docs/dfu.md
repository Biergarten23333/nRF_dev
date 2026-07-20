# B306 DFU plan

## Scope

The planned B306 update path is MCUboot with mcumgr SMP over BLE and two internal
flash image slots. This is documentation only: the minimal P1 build does not
enable MCUboot or change its partition map.

## NCS v2.8.0 configuration

The future sysbuild enables MCUboot with:

```ini
# sysbuild.conf
SB_CONFIG_BOOTLOADER_MCUBOOT=y
```

The application configuration needs at least:

```ini
CONFIG_BOOTLOADER_MCUBOOT=y
CONFIG_FLASH=y
CONFIG_FLASH_MAP=y
CONFIG_STREAM_FLASH=y
CONFIG_IMG_MANAGER=y
CONFIG_NET_BUF=y
CONFIG_ZCBOR=y
CONFIG_CRC=y
CONFIG_MCUMGR=y
CONFIG_MCUMGR_GRP_IMG=y
CONFIG_MCUMGR_GRP_OS=y
CONFIG_MCUMGR_TRANSPORT_BT=y
CONFIG_MCUMGR_TRANSPORT_BT_PERM_RW=y
CONFIG_MCUMGR_TRANSPORT_BT_CONN_PARAM_CONTROL=y
CONFIG_MCUMGR_TRANSPORT_BT_REASSEMBLY=y
CONFIG_BT=y
CONFIG_BT_PERIPHERAL=y
```

Production must replace unauthenticated write permission with an authenticated
policy before deployment. Buffer/stack sizes and image-signing keys are selected
and validated when DFU is enabled; private signing keys never enter this repo.

## Planned internal-flash layout

NINA-B306's nRF52840 has 1 MiB internal flash. The proposed fixed layout uses
equal slots, no scratch partition, and reserves 32 KiB for settings:

| Region | Address | Size |
|---|---:|---:|
| MCUboot | `0x00000000` | `0x0000C000` = 48 KiB |
| Primary image slot | `0x0000C000` | `0x00076000` = 472 KiB |
| Secondary image slot | `0x00082000` | `0x00076000` = 472 KiB |
| Settings/storage | `0x000F8000` | `0x00008000` = 32 KiB |

The MCUboot header/pad and trailer live inside their image-slot budgets. This
layout totals exactly `0x100000`. It is a plan, not the current build map; when
DFU is enabled, the generated Partition Manager report must match these
addresses before hardware flashing.

## State machine

`RUN -> DFU_PREPARE -> DFU -> REBOOT -> RUN`

Entering `DFU_PREPARE` stops new 200 Hz triggers, waits for any active I2C read,
forces the DWM UART receiver idle, disables GPIO-ready capture, flushes or marks
the final capture batch, and rejects new capture commands. Only then may SMP
image traffic start.

Cancellation before image activation reinitializes peripherals and enters RUN
with a new session marker. A successful upload marks the signed image pending
and requests a reboot. The new image performs self-test, confirms itself, starts
a new boot/session identity, and enters RUN. Failure to confirm invokes MCUboot
rollback; the rolled-back image also enters RUN with a new identity. Capture and
DFU are never concurrent.

## Two independent targets

| MCU | Update image/tool | Must not be used for |
|---|---|---|
| DWM1001C nRF52832 | Existing UWB BLE-OTA flow via the B120 Tag Master; frozen source/build recipes under `UWB_Part` | B306 images or B306 SMP service |
| B306 nRF52840 | Future Fusion Master/PC mcumgr BLE upload of the B306 signed image; SWD/J-Link only for bench recovery | DWM1001C images |

Target identity, image board/SoC metadata, and image signature must be verified
before erase/upload. A successful operation on one MCU says nothing about the
firmware state of the other.
