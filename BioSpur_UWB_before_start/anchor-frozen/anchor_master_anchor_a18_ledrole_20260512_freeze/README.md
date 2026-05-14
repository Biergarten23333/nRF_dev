# Anchor / Master_Anchor Frozen Point

Freeze time: 2026-05-12

Purpose: preserve the last known Anchor + Master_Anchor recovery point so future changes can branch from this exact state instead of guessing from old build folders.

## Frozen Images

Anchor common image:

```text
build-anchor-unified-ota-alt-bcast-a18-ledrole-g1200-r1000-rebuild-20260511_204448
fw marker: alt-bcast-a18-ledrole-g1200-r1000
```

Master_Anchor carrier image:

```text
build-master-control-b120-m1-master-anchor-lfrc-anchoronly-bsblock-embed-last-a18-20260512_124723
SNR: 960148546
role: Master_Anchor
CDC port: /dev/serial/by-id/usb-Master_Anchor_BioSpur_BLE_Control_87EA2F4A526C5A02-if00
USB serial short: 87EA2F4A526C5A02
embedded OTA payload kind: anchor
embedded OTA payload marker: alt-bcast-a18-ledrole-g1200-r1000
```

## Important Policy

- Do not modify, rebuild, OTA, or flash Anchor / Master_Anchor images unless the user explicitly requests it.
- `960148546` is `Master_Anchor`.
- If `Master_Tag` is unplugged or missing from `/dev/serial/by-id`, do not
  infer anything about `Master_Anchor`; `960148546` remains `Master_Anchor`.
- `Master_Anchor` may only embed Anchor OTA payloads.
- `Master_Tag` may only embed Tag OTA payloads.
- Before any B120 flash, print and verify `SNR -> logical role -> image path`.
- Before any B120 flash, run the LFRC assertion.
- Never use `nrfjprog`; use the repo J-Link scripts with explicit SNR.

## Checksums

```text
6f64f822e4a260b525171bf96e4f6348d7d77ec833ed1d3edc7401a7e66da40e  Anchor merged.hex
76db7bfb1089043f754e83e858d4e98f56d24d9c146b42289ca6d0c9ec69625b  Anchor zephyr.signed.bin
8be1ca6edd657b16d8755efbed7d2ed469adc29781d2af4d5fd7a3151d097b31  Anchor dfu_application.zip
1b73d1f1486b4e8ca6c7f1c227fbbeb15dcbb62d2b26c358dd139284a4f61be6  Master_Anchor CPUAPP merged.hex
17600e5b4ef829615c319fa3ad51ceba3f51caf0d317da008e67d6dd0cb1f1a0  Master_Anchor CPUNET merged_CPUNET.hex
```

## Recovery Notes

To restore Master_Anchor with the frozen embedded Anchor payload, use:

```text
builds/build-master-control-b120-m1-master-anchor-lfrc-anchoronly-bsblock-embed-last-a18-20260512_124723
```

To restore Anchor A-H directly by J-Link, use:

```text
builds/build-anchor-unified-ota-alt-bcast-a18-ledrole-g1200-r1000-rebuild-20260511_204448/merged.hex
```

To restore Anchor A-H by OTA through Master_Anchor, use the Master_Anchor carrier above because it embeds the same Anchor payload.
