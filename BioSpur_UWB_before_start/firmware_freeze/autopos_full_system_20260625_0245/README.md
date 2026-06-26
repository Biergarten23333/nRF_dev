# AutoPos Full System Firmware Freeze - 2026-06-25 02:45 CEST

This directory freezes the currently validated AutoPos firmware set for:

- Deployed Tags
- Deployed Anchors
- Master_Tag controller
- Master_Anchor controller

The freeze is artifact-first: the four build directories are copied here in full,
with `.config`, `pm.config`, maps, hex/bin images, signed OTA binaries, DFU zips,
source metadata, deployment logs, and SHA256 checksums.

## Freeze Contents

- `builds/`
  - Full copied build directories.
- `source/`
  - `.source` metadata for the four selected builds.
- `logs/`
  - OTA/deployment verification logs used for this freeze.
- `generated/`
  - Snapshot of the current `apps/master_ota/generated` manifests at freeze time.
- `git/`
  - Git HEAD, dirty status, diff stat, and firmware-relevant diff snapshot.
- `SHA256SUMS.txt`
  - Hashes for every file in this freeze.
- `SIZE.txt`
  - Freeze size summary.

## Selected Firmware Set

### Tags

- Build: `build-tag-ble-unified-tdmafix-nodiag-r800-20260624`
- Marker: `compact-sampled-tdmafix-nodiag-r800-20260624`
- Signed OTA image:
  - `builds/build-tag-ble-unified-tdmafix-nodiag-r800-20260624/tag/zephyr/zephyr.signed.bin`
  - SHA256: `830e23db700a461f1d9b15a5626f805f94614cff37e1c588acc15804f74a0ba0`
- DFU zip:
  - `builds/build-tag-ble-unified-tdmafix-nodiag-r800-20260624/dfu_application.zip`
  - SHA256: `33460ed4dc97c5d97a1aaa6e55740a03c209dee8fd2547a230abfcea9054016f`
- Build command:
  - `./scripts/build_tag_ble_unified.sh 0 10 build-tag-ble-unified-tdmafix-nodiag-r800-20260624`
- Deployment evidence:
  - `logs/ota_tag_nodiag_r800_6tag_resume_20260624_205658/deploy_summary.json`
  - Targets covered there: `BSF66F`, `BS2DCE`, `BSDC91`, `BS9336`, `BS955A`, `BSCCF4`.

### Master_Tag

- Build: `build-master-control-b120-m1-master-tag-lfrc-tdma-explicit-nodiag-r800-20260624`
- CPUAPP hex:
  - `builds/build-master-control-b120-m1-master-tag-lfrc-tdma-explicit-nodiag-r800-20260624/zephyr/zephyr.hex`
  - SHA256: `69b3927f298a2b1e634d46fdd399ef33304f2cfc67b929ae28b3c6a35aa56340`
- CPUNET hex:
  - `builds/build-master-control-b120-m1-master-tag-lfrc-tdma-explicit-nodiag-r800-20260624/hci_ipc/zephyr/zephyr.hex`
  - SHA256: `4e0e3bd9f8ca6a7a6f4c6f41e266ca6c241c2c5a14d708b20954acb28862ff82`
- Embedded Tag OTA payload manifest:
  - `builds/build-master-control-b120-m1-master-tag-lfrc-tdma-explicit-nodiag-r800-20260624/active_ota_payload.json`
- Master_Tag SNR:
  - `1050070698`
- Build command:
  - `scripts/build_master_control_b120_m1.sh build-master-control-b120-m1-master-tag-lfrc-tdma-explicit-nodiag-r800-20260624`

### Anchors

- Build: `build-anchor-unified-ota-cir-us-r800-20260624`
- Marker: `anchor-cir-us-r800-20260624`
- Important timing note:
  - This name contains `r800`, but the stable base timing in the copied configs is still guard `1200 us` and responder spacing `1000 us`; `800 us` is the tail responder spacing for later ranks.
- Ultrasound:
  - Enabled in the deployed anchor runtime; verification logs reported `US;IDLE;enabled=1;trig=P0.06;echo=P0.07;period_ms=100`.
- Signed OTA image:
  - `builds/build-anchor-unified-ota-cir-us-r800-20260624/anchor/zephyr/zephyr.signed.bin`
  - SHA256: `3c00d21cf9643d21c3a13fb910ed83c87a6da9a4b134601d87ea9822480cc8ee`
- DFU zip:
  - `builds/build-anchor-unified-ota-cir-us-r800-20260624/dfu_application.zip`
  - SHA256: `54afd1c26a57df0bf6d7a4ff12d11dddebdab5e837cf603bcced0926a9562036`
- Deployment evidence:
  - `logs/ota_anchor_cir_us_r800_20260624*`
  - `logs/ota_anchor_cir_us_r800_20260625_*`

### Master_Anchor

- Build: `build-master-control-b120-m1-master-anchor-lfrc-cir-us-r800-20260624`
- CPUAPP hex:
  - `builds/build-master-control-b120-m1-master-anchor-lfrc-cir-us-r800-20260624/zephyr/zephyr.hex`
  - SHA256: `022a199c57b2c10def890d2d1716a5498f840a93506d532053e3d7d1a1f1a41b`
- CPUNET hex:
  - `builds/build-master-control-b120-m1-master-anchor-lfrc-cir-us-r800-20260624/hci_ipc/zephyr/zephyr.hex`
  - SHA256: `4e0e3bd9f8ca6a7a6f4c6f41e266ca6c241c2c5a14d708b20954acb28862ff82`
- Embedded Anchor OTA payload manifest:
  - `builds/build-master-control-b120-m1-master-anchor-lfrc-cir-us-r800-20260624/active_ota_payload.json`
- Master_Anchor SNR:
  - `960148546`
- CDC naming note:
  - Master_Anchor should be made easier to identify later. Do not assume current by-id always contains `Master_Anchor`; identify by SNR/serial mapping if needed.
- Build command:
  - `scripts/build_master_control_b120_m1.sh build-master-control-b120-m1-master-anchor-lfrc-cir-us-r800-20260624`

## Restore Rules

- Tags: restore by BLE OTA only through Master_Tag.
- Anchor bodies: restore by BLE OTA only through Master_Anchor.
- Master_Tag and Master_Anchor: restore with the repository B120 J-Link script only when explicitly needed.
- Do not direct-flash deployed Tag or Anchor bodies as a routine restore path.

Reference Master restore commands from the repository root:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast

# Master_Tag, SNR 1050070698
B120_SNR=1050070698 scripts/flash_master_control_b120_m1_noninteractive.sh \
  /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/firmware_freeze/autopos_full_system_20260625_0245/builds/build-master-control-b120-m1-master-tag-lfrc-tdma-explicit-nodiag-r800-20260624

# Master_Anchor, SNR 960148546
B120_SNR=960148546 BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH=1 scripts/flash_master_control_b120_m1_noninteractive.sh \
  /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/firmware_freeze/autopos_full_system_20260625_0245/builds/build-master-control-b120-m1-master-anchor-lfrc-cir-us-r800-20260624
```

Before using these commands, verify the selected B120 build still passes:

```bash
scripts/assert_b120_internal_osc_build.sh <freeze-build-dir>
```

## Verification

From this directory:

```bash
sha256sum -c SHA256SUMS.txt
```

Known test status at freeze time:

- Tag OTA for six tags completed and post-version matched `compact-sampled-tdmafix-nodiag-r800-20260624`.
- Anchor OTA completed across A-H in resumed/single rounds and post-version checks matched `anchor-cir-us-r800-20260624` where queried.
- Master controller builds are LFRC B120 builds.
- Full system still has runtime validation caveats from the recovery run; see `git/status_short.txt`, `git/diff_stat.txt`, and recovery logs for dirty working-tree context.
