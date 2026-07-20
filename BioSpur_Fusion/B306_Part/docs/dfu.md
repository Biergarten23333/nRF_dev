# B306 DFU contract

## First-flash image

The B306 first image is deliberately limited to signed MCUboot, mcumgr SMP over
BLE (image and OS groups), FICR-derived `BSF%04X` advertising, non-blocking RTT
logging, and an LED heartbeat. It contains no UWB UART, ready-edge capture, or
IMU behavior.

The first human SWD handover establishes two durable interfaces:

1. the MCUboot public key compiled into the bootloader; and
2. the flash partition layout frozen in `firmware/pm_static.yml`.

Changing either later requires another SWD handover. The BLE-only update cycle
must pass before feature work is installed on the Fusion PCB.

## Signing key

The private ECDSA P-256 key is stored outside the repository:

```text
/home/zekaixiao/.config/biospur/keys/b306_mcuboot_ec_p256.pem
```

Its tracked public half is:

```text
B306_Part/firmware/keys/b306_mcuboot_ec_p256.pub.pem
```

The durable key fingerprint is SHA-256 over the DER-encoded
SubjectPublicKeyInfo:

```text
0e525dedaa7f50fb38d3c8f1792cacaa20f70204aa46ef6b50d720479c6ef5a2
```

Before the first B306 flash, confirm the private key has a protected backup and
recompute this fingerprint:

```bash
openssl pkey \
  -in /home/zekaixiao/.config/biospur/keys/b306_mcuboot_ec_p256.pem \
  -pubout -outform DER |
sha256sum
```

Do not regenerate or replace this key after first flash. The repository never
contains the private key.

## Generated and frozen partition layout

Partition Manager generated this map from the first dynamic NCS v2.8.0
sysbuild. The exact generated YAML was then frozen as `firmware/pm_static.yml`,
and a pristine rebuild reproduced it.

| Region | Start | End | Size |
|---|---:|---:|---:|
| MCUboot | `0x000000` | `0x00C000` | `0x00C000` = 48 KiB |
| MCUboot pad | `0x00C000` | `0x00C200` | `0x000200` = 512 B |
| Application payload | `0x00C200` | `0x086000` | `0x079E00` = 487.5 KiB |
| Primary image slot, including pad | `0x00C000` | `0x086000` | `0x07A000` = 488 KiB |
| Secondary image slot | `0x086000` | `0x100000` | `0x07A000` = 488 KiB |
| SRAM | `0x20000000` | `0x20040000` | `0x040000` = 256 KiB |

There is no scratch or settings partition in this minimal first layout.
MCUboot uses equal 488 KiB primary and secondary slots.

## Configuration

`sysbuild.conf` enables MCUboot with ECDSA P-256 signing. The application
enables the flash map, image manager, BLE SMP transport, image group, and OS
group. MCUboot and the application both use the calibrated 500 ppm LFRC and
RTT in no-block-skip mode. Flash-patch support is disabled to preserve the
secure-boot trust boundary.

The first SMP service has unauthenticated read/write permission and is a
bench-bring-up image, not a production authorization policy.

## BLE-only acceptance, still pending

After the human reports a successful first flash, Stage 1 must:

1. discover the node by its `BSF%04X` name and SMP service UUID;
2. upload a signed image with a visibly different marker over BLE;
3. list the image and verify its hash;
4. mark it for test, reset, and verify the new marker;
5. demonstrate MCUboot revert when the test image is not confirmed;
6. upload again, confirm the image, reboot, and verify it remains active.

Record the exact host commands and image SHA used during that test. SWD must
remain untouched throughout the BLE-only cycle. The workstation's specific
mcumgr BLE transport tool has not yet been selected, so commands are not
invented here.

## Runtime state contract for later capture images

`RUN -> DFU_PREPARE -> DFU -> REBOOT -> RUN`

Entering `DFU_PREPARE` stops new IMU triggers and UWB ingest, completes or
aborts in-flight DMA safely, and closes the current capture batch. Capture and
DFU are mutually exclusive. A successful confirmed update and an MCUboot
rollback must both leave the node in RUN with a new boot/session identity.

The DWM1001C has a separate BLE-DFU route through the Tag Master. B306 SMP
images must never be sent to that MCU, and DWM1001C images must never be sent
to B306.
