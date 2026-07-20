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

## BLE-only acceptance — passed 2026-07-20

Stage 1 used the authorized nRF52840 DK probe `683234364` as a one-shot BLE SMP
client. The Fusion PCB was never accessed through SWD. No capture process was
running when the update began.

The image pushed to B306 was:

```text
marker:               b306-stage1-ota-v2
MCUboot version:      0.1.1+0
signed binary:        B306_Part/builds/b306-stage1-ota-v2/firmware/zephyr/zephyr.signed.bin
file SHA-256:         7f821fbf26144026c0ff8912118a3d3f098ec29ce9567633b5673df12425db02
MCUboot image digest: 8c695e2d49c97aab5692c69ac8447189ecf0e4d73b8d1129917ff3cd8f36c1dc
signing key hash:     0e525dedaa7f50fb38d3c8f1792cacaa20f70204aa46ef6b50d720479c6ef5a2
```

The one-shot acceptance harness embedded that exact signed binary and refused
to configure if its file SHA differed. Its accepted `merged.hex` SHA-256 was:

```text
3def096e9fbb9b56c0c1fd31f1c29ded1e2d7fa827d6603fbe06cd2be3d5b2bf
```

The client verified the initial `0.1.0` image, uploaded `0.1.1`, test-booted it
as active and unconfirmed, and observed its advertising marker. It then reset
without confirmation and verified MCUboot restored `0.1.0` as active and
confirmed. A second upload was test-booted, explicitly confirmed, rebooted, and
listed as `0.1.1`, active and confirmed. The terminal record was:

```text
B306_STAGE1_OTA_PASS name=BSF3C79 marker=b306-stage1-ota-v2 version=0.1.1+0 file_sha=7f821fbf26144026c0ff8912118a3d3f098ec29ce9567633b5673df12425db02 image_digest=8c695e2d49c97aab5692c69ac8447189ecf0e4d73b8d1129917ff3cd8f36c1dc
```

The RTT acceptance record is under
`logs/b306_stage1_ota_20260720_155326/rtt_acceptance.log`. B306 now advertises
as `BSF3C79` with the Stage 1 marker and runs confirmed image `0.1.1+0`.

The acceptance-only implementation was removed after this proof so it cannot
become a parallel OTA stack.

## Permanent fast OTA path — migrated 2026-07-20

`host/dk_ota/` is now the only maintained B306 OTA client. It imports the exact
fast OTA source from the read-only UWB FREEZE at configure time and refuses to
build if either frozen source SHA changes:

```text
main.c       9613d746a102afa9e0ea5943e1ea0074bd24b3445051be0fc2c2a51a1a880906
master_ota.h b30d1e3635b4ab1e00c2c3cad145564c5742f24c7cc6dbd194dfb488af611012
```

The shared implementation supplies its 448-byte maximum chunk, MTU-aware
downshift, 2M PHY request, 7.5 ms connection parameters, retry handling,
secondary-slot erase, upload, test scheduling, and OS reset. The small B306
adapter only selects the exact `BSF%04X` name, skips the UWB-only NUS step, and
auto-starts the one-shot update. Target name, payload marker, signed image
path, and signed-image SHA are all mandatory build inputs. A deliberately
wrong SHA was verified to stop CMake before an updater image was produced.

The first candidate built through this unified path is not installed:

```text
target:                 BSF3C79
marker:                 b306-fast-ota-v3
MCUboot version:        0.1.2+0
signed binary:          B306_Part/builds/b306-fast-ota-v3/firmware/zephyr/zephyr.signed.bin
file SHA-256:           28461e49c5495fa2e5ff93d56e9a9ec7e2fc774f5fa362edf4ea965bf76ebd67
MCUboot image digest:   f8d6e338e4682aa4a7b8229bc6218bad524f59b62e0ec2c0de8d0fced919da78
B306 merged.hex SHA:    ea52481eaef7ea5b2cfef970eaa4a68f1eb578a8cdc00ad13924d025103f3589
Fusion Master DK SHA:   93280a8ac3b998de4d58e19d0569df8138b0dac9795e6be5aff95b05559e401a
```

The B306 candidate uses L2CAP MTU 498 and 502-byte ACL buffers. It self-confirms
only after its LF clock, BLE stack, FICR-derived identity, SMP service, and
connectable advertising have started. The currently installed v2 target
negotiates ATT MTU 247, so the first unified update will use the same core's
automatic chunk downshift; subsequent updates can negotiate the full fast
path.

No DK or B306 was flashed during this migration, and no OTA transfer was
started. The exact reproducible build and safety contract are in
`host/dk_ota/README.md`.

## Runtime state contract for later capture images

`RUN -> DFU_PREPARE -> DFU -> REBOOT -> RUN`

Entering `DFU_PREPARE` stops new IMU triggers and UWB ingest, completes or
aborts in-flight DMA safely, and closes the current capture batch. Capture and
DFU are mutually exclusive. A successful confirmed update and an MCUboot
rollback must both leave the node in RUN with a new boot/session identity.

The DWM1001C has a separate BLE-DFU route through the Tag Master. B306 SMP
images must never be sent to that MCU, and DWM1001C images must never be sent
to B306.
