# Anchor / Master_Anchor Frozen Point

Freeze time: 2026-05-12 16:17 Europe/Berlin

Purpose: preserve the first validated Anchor + Master_Anchor recovery point after enabling broadcast responder timing on the Anchor image. This is the point to restore before outdoor experiments if Anchor sweep behavior drifts again.

## Frozen Images

Anchor common image:

```text
build-anchor-unified-ota-altbcast-responder-a18-g1200-r1000-20260512_154806
fw marker: altbcast-responder-a18-g1200-r1000-20260512_154806
APP_ALT_SS_TWR_ENABLE=1
APP_ALT_SS_TWR_BCAST_ENABLE=1
APP_ALT_SS_TWR_GUARD_US=1200
APP_ALT_SS_TWR_RESP_SPACING_US=1000
APP_ANCHOR_RESP_DELAY_UUS=1200
```

Master_Anchor carrier image:

```text
build-master-control-b120-m1-master-anchor-lfrc-anchoronly-altbcast-embed-altbcast-responder-a18-g1200-r1000-20260512_154806
SNR: 960148546
role: Master_Anchor
CDC port: /dev/serial/by-id/usb-Master_Anchor_BioSpur_BLE_Control_87EA2F4A526C5A02-if00
embedded OTA payload kind: anchor
embedded OTA payload marker: altbcast-responder-a18-g1200-r1000-20260512_154806
```

## Validation

Anchor OTA:

```text
A: logs/anchor_altbcast_direct_ota_A_20260512_160030
B-H: logs/anchor_altbcast_direct_ota_BH_reset_each_20260512_160406
```

Capture validation:

```text
logs/six_tag_stable10x9_tr12_altanchor_capture120_20260512_161135_20260512_161135
```

Result:

```text
anchor responder preflight: sent=8 ready=8/8
duration: 120s
targets: BSF66F, BS2DCE, BSDC91, BSCCF4, BS9336, BS955A
TDMA: 10 Hz, period=10ms, active=9ms
tr_all: 48400
tr_valid_all: 40124
sweeps_total: 3082
sweeps_ge4: 2967
sweeps_ge7: 2938
sweeps_ge8: 1931
ratio_ge7: 0.953277
ratio_ge8: 0.626541
```

## Checksums

```text
3769f850dc065a3eccd2896ff824ecbc8cdf554dc4a1564a9befd84086c2e062  Anchor zephyr.signed.bin
b1288ef0f8f8e60dd248fb65e6cc666fdac18cb7ef2d2f2a4d1006042f746fc8  Anchor dfu_application.zip
6aaf1a1098766b371d25c67db625f69f6d4f32de37f6ca26d7137926db9458ec  Master_Anchor CPUAPP merged.hex
17600e5b4ef829615c319fa3ad51ceba3f51caf0d317da008e67d6dd0cb1f1a0  Master_Anchor CPUNET merged_CPUNET.hex
```

## Recovery Notes

To restore Master_Anchor with the frozen embedded Anchor OTA payload, use:

```text
builds/build-master-control-b120-m1-master-anchor-lfrc-anchoronly-altbcast-embed-altbcast-responder-a18-g1200-r1000-20260512_154806
```

To restore Anchor A-H by OTA, use the Master_Anchor carrier above; A-H should not be J-Link flashed unless explicitly authorized.

Important working note: the bulk `ota_deploy_anchor_set.py` pre-version wrapper wedged CDC during this run. The successful deployment used `ota_single_shot_stable.py` directly, with a Master_Anchor app-core J-Link reset before each target.
