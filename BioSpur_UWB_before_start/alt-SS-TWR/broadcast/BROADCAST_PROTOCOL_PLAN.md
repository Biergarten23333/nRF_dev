# Broadcast Alt SS-TWR Branch

This directory is the active workspace for the clean broadcast Alt SS-TWR
branch.

## Isolation Rules

- `../unicast/` is frozen and read-only.
- Broadcast sources must be physical copies, not symlinks.
- Production files outside `alt-SS-TWR/` remain untouched.
- Hardware scripts may be copied into `broadcast/scripts/` or referenced from
  `../unicast/scripts/`, but `../unicast/` scripts must not be modified.
- Broadcast logs go under `broadcast/logs/`.

## Design

Replace four or eight separate unicast poll frames with one broadcast poll:

```text
Tag:      [broadcast poll dst=0xffff]
Anchors:        guard | A | B | C | D | E | F | G | H
Tag RX:          one continuous response collection window
```

Poll frame fields:

- `dst_addr = 0xffff`
- `src_addr = tag short address`
- `alt_code = 0xe0`
- `tag_id`
- `anchor_mask`
- optional embedded `poll_tx_ts`

Anchor response slot:

```text
resp_delay_us = guard_us + rank * resp_spacing_us
```

Starting parameters:

- `APP_ALT_SS_TWR_GUARD_US=400`
- `APP_ALT_SS_TWR_RESP_SPACING_US=1000`
- `APP_ALT_SS_TWR_BCAST_ENABLE=1`
- `APP_ANCHOR_RESPONDER_COOP_SLEEP_MS=1`
- `APP_TAG_RANGE_RAW_DELTA_GATE_ENABLE=0`
- `APP_TAG_RANGE_CONTINUITY_ENABLE=0`

## First Milestone

Broadcast v1: single Tag, eight Anchors, static position.

Success criteria:

- BSF66F produces positions.
- Listener sees one broadcast poll and up to eight anchor responses per sweep.
- CF `first_to_last_us = 0`.

## Current b1 Build Status

- Tag b1 marker: `alt-bcast-b1-tag-g400-r1000`
- Anchor b1 marker: `alt-bcast-a1-g400-r1000-coop1`
- Broadcast poll frame is now 17 bytes:
  `dst=0xffff`, `tag_id`, `anchor_mask`, `poll_tx_ts[5]`.
- Tag b1 build succeeded:
  `build-alt-bcast-b1-tag-g400-r1000-raw0-cont0/dfu_application.zip`
- Anchor b1 OTA image build succeeded through the copied original bundle flow:
  `build-anchor-unified-ota-alt-bcast-a1-g400-r1000-coop1/dfu_application.zip`
- B120 Master_Tag carrier build succeeded with internal LFRC:
  `build-master-control-b120-m1-master-tag-lfrc-alt-bcast-b1-tag-g400-r1000-carrier/zephyr/merged_domains.hex`
- B120 Master_Anchor carrier build succeeded with internal LFRC:
  `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-a1-g400-r1000-coop1-carrier/zephyr/merged_domains.hex`
- No hardware deployment has been done from this branch yet.
