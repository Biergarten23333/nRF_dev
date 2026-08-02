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

## Productization debts fixed by the first-flash boundary

Two unrelated issues must remain visible together because either can require a
physical service operation after deployment:

| Debt | Current mitigation | Closure criterion |
|---|---|---|
| Signing/DFU authorization | Preserve the existing private-key backup and use the bench-only unauthenticated SMP service only on the controlled rig. The public key embedded by the first flash cannot be replaced over OTA. | Define production key custody and authenticated update authorization before field deployment; any bootloader-key change is an SWD handover. |
| B306 TIMER2 32-bit microsecond wrap | **Power-cycle B306 before every capture session** and limit current sessions to 60 minutes. Confirm fresh `strobe_us` / `node_ms` values in preflight. | Repair and validate the 64-bit extension across multiple natural wraps without losing UWB records or telemetry. Do not change firmware merely to complete the current one-hour REDO. |

The TIMER2 debt was exposed by the 2026-07-21 one-hour attempt: the last edge
was `4,294,873,329 us`, 93,967 us before the boundary, and the next nominal
100 ms edge crossed it; telemetry ended at the same boundary. Evidence:
`UWB_Part/logs/absdeadline_1h_20260721_205638/analysis/1h-summary.md`.

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

The first candidate built through this unified path was installed and
self-confirmed over BLE on 2026-07-20:

```text
target:                 BSF3C79
marker:                 b306-fast-ota-v3
MCUboot version:        0.1.2+0
signed binary:          B306_Part/builds/b306-fast-ota-v3/firmware/zephyr/zephyr.signed.bin
file SHA-256:           28461e49c5495fa2e5ff93d56e9a9ec7e2fc774f5fa362edf4ea965bf76ebd67
MCUboot image digest:   f8d6e338e4682aa4a7b8229bc6218bad524f59b62e0ec2c0de8d0fced919da78
B306 merged.hex SHA:    ea52481eaef7ea5b2cfef970eaa4a68f1eb578a8cdc00ad13924d025103f3589
Fusion Master DK SHA:   d1e9777bf778d0db5f09691be086dfe77e3a6686f69986438296b089e74455fa
```

The B306 image uses L2CAP MTU 498 and 502-byte ACL buffers. It self-confirms
only after its LF clock, BLE stack, FICR-derived identity, SMP service, and
connectable advertising have started. The v2-to-v3 update negotiated ATT MTU
247 and used automatic chunk downshift. After v3 became active and confirmed,
a same-image re-upload negotiated ATT MTU 498 and transferred 170,420 bytes in
about 9.4 seconds with the 448-byte fast path. MCUboot rejected scheduling the
already-active identical digest with `rc=1`, so that re-upload intentionally
caused no second swap.

The subsequent genuine v3-to-v4 re-OTA also completed on 2026-07-20:

```text
target:                 BSF3C79
marker:                 b306-fast-ota-v4
MCUboot version:        0.1.3+0
signed binary:          B306_Part/builds/b306-fast-ota-v4/firmware/zephyr/zephyr.signed.bin
file SHA-256:           f23b0a12f7652f64e0154fb97238a72bcd57604913c1f5cc59b75e1d0e7bcae9
MCUboot image digest:   c86174336e8e1e1f0094e494dd398d647aaa91d2d8c9d9462caac494475721b2
B306 merged.hex SHA:    bfdc96cf3dc6c767410fb671137cfd3d84c5cddb51e465c809716e919ad9bdb9
DFU zip SHA:            bc2cb25bf1b85c3549cfafa3a4b1c9883c99a538231990c62ecde0868612834d
Fusion Master DK SHA:   c3eddb626faf4436f5b86cfe6121f9eabf470115b90a00d8588e8bb5f322b96e
```

The updater transferred all 170,419 bytes in about 9.2 seconds, read slot 0 as
confirmed v3 and slot 1 as bootable v4, then received status 0 for both the
pending/test request and remote reset. After the physical reset check, a
verify-only build issued only an image-state read. B306 reported v4 in slot 0
with `active=true` and `confirmed=true`, and v3 inactive in slot 1. This closes
the real re-OTA cycle without relying on advertising alone.

The raw records are
`logs/b306_fast_ota_v4_20260720_165305/dk-rtt.log` and
`logs/b306_fast_ota_v4_verify_20260720_170000/after-explicit-dk-reset.log`.
Only Fusion Master DK probe `683234364` was directly flashed, always with
explicit probe selection. No Fusion-PCB SWD interface was touched. The exact
reproducible builds and safety contract are in `host/dk_ota/README.md`.

## Stage 2 v8 OTA result — active image valid, secondary slot incomplete

The Stage 2/4b capture image was sent through the same permanent fast OTA path:

```text
target:              BSF3C79
marker:              b306-strobe-capture-v8
MCUboot version:     0.1.7+0
installed archive:   B306_Part/logs/strobe_attribution_5min_20260721_101455/b306-installed-v8.signed.bin
signed SHA-256:      57da2011b25bab04ccfc80ab1aa0ee7cf450984ccd4ac1277d86ee7a209a425f
updater merged.hex:  B306_Part/builds/dk-ota-strobe-capture-v8/merged.hex
updater SHA-256:     802705369862fea8093fc2d0494b2fe4efe7f492e315345d63bf6cf3f3c14ee2
```

The first update completed, but its terminal RTT output was not captured. A
second same-image updater reset was then started solely to recapture the log.
Before writing, that client read slot 0 as version `0.1.7`, bootable, active,
and confirmed; this is direct proof that the first update succeeded. The second
run disconnected during erase/upload and was stopped after partially
overwriting slot 1. The confirmed slot-0 v8 image remained intact and later
passed the 300 s strobe-attribution run.

The active image is accepted. The secondary slot is not a rollback artifact and
must not be represented as one until a later complete OTA replaces it. This was
an operator-side duplicate-run error, not evidence that the installed v8 image
or the shared OTA implementation failed.

The exact installed signed payload is retained in the accepted run directory,
not inferred from a later rebuild. A post-run isolated rebuild produced the
same MCUboot payload hash
`94cbf3b858211209f0c5b3851dcafa0cb329d0e73b013bd103164201ad658b21`
with a different valid ECDSA signature; its signed-binary SHA is
`da22a7d55bb8a24c44125249d3f5df06cc85478d271c19b599c426ebe5a18be5`.

## Runtime state contract for later capture images

`RUN -> DFU_PREPARE -> DFU -> REBOOT -> RUN`

Entering `DFU_PREPARE` stops new IMU triggers and UWB ingest, completes or
aborts in-flight DMA safely, and closes the current capture batch. Capture and
DFU are mutually exclusive. A successful confirmed update and an MCUboot
rollback must both leave the node in RUN with a new boot/session identity.

The DWM1001C has a separate BLE-DFU route through the Tag Master. B306 SMP
images must never be sent to that MCU, and DWM1001C images must never be sent
to B306.
