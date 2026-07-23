# Master_Tag relay1 OTA-carrier handover

This handover installs the Path-M carrier that embeds exactly the Fusion-tag
payload `tag-fusion-link-v2-relay1`. It does **not** update the Fusion tag by
itself. After this handover is reported successful, Codex can run the BLE OTA
to the single target `BS065F` and time it.

Do not execute this procedure on the Fusion-PCB DWM1001C. Probe `1050070698`
must be physically connected to the **Master_Tag B120 itself**. If its SWD
lead/selector still targets the Fusion PCB, stop.

## Files and SHA-256

Files written to the Master_Tag B120:

| Core | File | SHA-256 |
|---|---|---|
| CPUAPP | `UWB_Part/builds/master-control-b120-m1-master-tag-lfrc-relay1/zephyr/merged.hex` | `f5f504360bfea2e5b5fb13c76b40a5830f1bf3e83f01d4feec0865c47b1ce37a` |
| CPUNET | `UWB_Part/builds/master-control-b120-m1-master-tag-lfrc-relay1/hci_ipc/zephyr/merged_CPUNET.hex` | `9c17013e933dcccfdc611085b1154a6b3cc775e59b00da542f5fcf8a0ba94199` |

Payload embedded in the carrier, later sent over BLE/SMP to `BS065F`:

| File | SHA-256 |
|---|---|
| `UWB_Part/builds/tag-fusion-link-relay1/tag/zephyr/zephyr.signed.bin` | `3175f6b5b72258fe6da73ac89b72cfd839bba7443f2028f2b1418cf77429e97b` |
| `UWB_Part/builds/tag-fusion-link-relay1/dfu_application.zip` | `63b8127638c972a5551d8c007e0386de270cba72ea02446fcd07ca357361a8ce` |

The carrier's copied payload manifest is
`UWB_Part/builds/master-control-b120-m1-master-tag-lfrc-relay1/active_ota_payload.json`,
SHA-256
`eb0768410f0f6a981fa94712358e01259c8f80561754f8f7705f73ffd50f55eb`.
It records the same marker and signed-image SHA above.

Both carrier cores pass the production memory gate. CPUAPP uses 37.88% FLASH
and 34.45% RAM; CPUNET uses 59.50% FLASH and 66.55% RAM. Both have an explicit
zero-byte malloc arena and use the internal calibrated LFRC.

## Pre-flight

1. Confirm no capture or OTA is running.
2. Confirm the powered target is the Master_Tag B120, not DWM1001C or
   Master_Anchor.
3. Confirm J-Link serial `1050070698` is the probe attached to that B120.
4. Confirm the two SHA-256 values before flashing:

```bash
cd /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion

sha256sum \
  UWB_Part/builds/master-control-b120-m1-master-tag-lfrc-relay1/zephyr/merged.hex \
  UWB_Part/builds/master-control-b120-m1-master-tag-lfrc-relay1/hci_ipc/zephyr/merged_CPUNET.hex
```

The command below intentionally performs `recover`: it erases both nRF5340
cores and the Master_Tag settings/NVS, then programs each core in a fresh
J-Link session. Every session contains `-SelectEmuBySN 1050070698`; no probe
selection dialog is permitted.

## Flash command

```bash
cd /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion

UWB_Part/2026-07-15-FREEZE/scripts/ops/flash_b120_master_freeze.sh \
  1050070698 \
  UWB_Part/builds/master-control-b120-m1-master-tag-lfrc-relay1 \
  4000
```

Then physically power-cycle the Master_Tag B120. A J-Link reset alone is not
accepted as the cold-boot test for this nRF5340 image.

## Post-flash verification

After the cold power cycle, the stable CDC device must reappear:

```text
/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00
```

Open that CDC port with DTR/RTS disabled and issue `ota show`. Required
observations:

```text
=== MASTER BOOT: profile=tag ... target=TAG wand tags: WILL HOLD BS* ===
UART control ready: ...
OTA_BUNDLE kind=tag fw=tag-fusion-link-v2-relay1 ...
```

If the CDC port does not return, or the boot profile/payload marker differs,
do not attempt the tag OTA. Report the complete flash output and what actually
appeared after the cold power cycle.

## Rollback

The frozen dual-core Master_Tag rollback pair is:

| Core | File | SHA-256 |
|---|---|---|
| CPUAPP | `UWB_Part/2026-07-15-FREEZE/RECOVERED/master_tag/zephyr/merged.hex` | `ded58a94a4ebbab9a7cbddc408e0214b5acb91969dac75c6ece40102d2bd63c8` |
| CPUNET | `UWB_Part/2026-07-15-FREEZE/RECOVERED/master_tag/hci_ipc/zephyr/merged_CPUNET.hex` | `17600e5b4ef829615c319fa3ad51ceba3f51caf0d317da008e67d6dd0cb1f1a0` |

Rollback command:

```bash
cd /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion

UWB_Part/2026-07-15-FREEZE/scripts/ops/flash_b120_master_freeze.sh \
  1050070698 \
  UWB_Part/2026-07-15-FREEZE/RECOVERED/master_tag \
  4000
```

Cold-power-cycle again and verify the same Master_Tag CDC identity. This
rollback restores the frozen carrier; it does not revert firmware already
successfully OTA'd into `BS065F`.
