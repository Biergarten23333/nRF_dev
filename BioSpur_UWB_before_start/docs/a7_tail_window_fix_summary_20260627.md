# A7 Tail-Responder Window Fix — Summary (2026-06-27)

Hand-off summary for syncing the other agent (Codex). The "3-tag 10Hz can't reach
high ge7/ge8" problem is **SOLVED**. Root cause was NOT TDMA capacity / "too many
tags" / RFD diagnostics. It was a single timing-budget bug in the tag's response
collector window, plus a tag/anchor responder-spacing mismatch baked into the builds.

## Result (the proof)

Clean visible-3 capture, 120 s, 10 Hz, no RFD, no CIR, TDMA CFG verified, not
interrupted (`logs/clean_visible3_post_a7win_norfd_10hz_20260627_20260627_145936`):

| metric | BEFORE (freeze r800 tag) | AFTER (a7win tag) |
|---|---|---|
| overall ge7 | 0.774 | **0.978** |
| overall ge8 | 0.245 | **0.967** |
| A7 valid rate (BSF66F/BS9336/BSCCF4) | 0.00 / 0.33 / 0.57 | **0.98 / 0.98 / 0.98** |
| per-tag sweeps | 669 / 1059 / 357 (imbalanced) | 1095 / 1050 / 1070 (balanced) |
| aggregate sweeps | 2085 (~58% of target) | 3215 (~89% of target) |

Per-tag after: BSF66F ge7 0.978 / ge8 0.965; BS9336 0.977 / 0.971; BSCCF4 0.978 / 0.965.
Clean flags: `rfd_all=0, tr_diag_all=0, tdma_config_failed=False, interrupted=False`.

## Root cause

1. **Anchor 7 is always the last responder.** Responder rank is computed
   deterministically from anchor-id bit position (`ss_twr_init_alt_mask_rank` in
   `src/ss_twr_init.c`), no rotation. With 8 anchors, anchor_id 7 = rank 7, firing at
   `ANCHOR_RESP_DELAY(1200us) + 7*spacing` after poll TX-done.

2. **Tag/anchor responder spacing mismatch.** Deployed anchors run a flat **1000 us**
   spacing (`build-anchor-...-r800` cache: `RESP_SPACING_US=1000`,
   `TAIL_COMPRESS_ENABLE=0`, so the "r800" tail-compress never activates — the freeze
   README's "800 us is just the tail spacing" is misleading: on the **tag** side
   `RESP_SPACING_US` is the base used to size the RX window). The deployed tag
   (`build-tag-...-r800`) was compiled with **800 us**.

3. **Collector window too short.** `ss_twr_init_alt_bcast_response_window_us` =
   `GUARD(1200) + (n-1)*SPACING + TAIL_MARGIN(300) - POLL_AIRTIME(335)`. With tag
   SPACING=800 it computed **6765 us**, but anchor 7's frame completes near **8.45 ms**
   (at 1000 us anchor spacing). The single continuous collector window
   (`response_window_cycles`, broadcast loop ~`ss_twr_init.c:5004`) closed ~1.4 ms
   before A7 even started → A7 dropped every sweep → ge8 impossible, ge7 capped.

## Fix (minimal, surgical)

- Source: `SS_TWR_INIT_ALT_BCAST_TAIL_MARGIN_US` 300 → **800**
  (`src/ss_twr_init.c:425`).
- Tag build: `APP_ALT_SS_TWR_RESP_SPACING_US` 800 → **1000** (match anchors; robust even
  if anchors were 800, since A7 then arrives earlier). New window ≈ **8665 us**, inside
  the 9 ms active slot.
- Anchors NOT touched. Only the tag changed.
- Verified by full CMakeCache diff vs the freeze tag: ONLY `RESP_SPACING_US` (800→1000)
  and the marker differ. RFD stays off (`RF_DIAG_OUTPUT_ENABLE=0`), TR compact diag
  forced 0 to match freeze. No diagnostics re-enabled.

New tag build / marker: `compact-sampled-tdmafix-nodiag-a7win-20260627`
(`build-tag-ble-unified-tdmafix-nodiag-a7win-20260627`). Reproduce via:
```
APP_TAG_FW_MARKER=compact-sampled-tdmafix-nodiag-a7win-20260627 \
APP_ALT_SS_TWR_RESP_SPACING_US=1000 \
APP_TAG_TR_RF_DIAG_COMPACT_ENABLE=0 \
./scripts/build_tag_ble_unified.sh 0 10 build-tag-ble-unified-tdmafix-nodiag-a7win-20260627
```
(`RESP_RX_TIMEOUT_UUS=1800` comes from `apps/tag/CMakeLists.txt:41`, no env needed.)

## Deploy pipeline that was run (and verified ok)

1. `prepare_alt_ota_payload.py --kind tag --marker <a7win> --build-dir <a7win> --signed-bin .../zephyr/zephyr.signed.bin --dfu-zip .../dfu_application.zip` → embeds payload, sets `active_ota_payload.json` (kind=tag). Guard OK.
2. `APP_TAG_FW_MARKER=<a7win> scripts/build_master_tag.sh a7win-20260627` → Master_Tag B120 LFRC build with embedded payload. Payload guard passed.
3. `scripts/assert_b120_internal_osc_build.sh <master-build>` → LFRC verified.
4. `B120_SNR=1050070698 scripts/flash_master_control_b120_m1_noninteractive.sh <master-build>/zephyr/merged_domains.hex` → J-Link reflash Master_Tag, `action=ok`.
5. `ota_deploy_tag_set.py --port <Master_Tag CDC> --targets BSF66F,BS9336,BSCCF4 --expected-fw-marker <a7win>` → all 3 OTA ok, post-version = a7win.
6. clean visible-3 recapture (result table above).

## Gotchas for the other agent

- **The 3 deployed tags were on an OLD May image** (`stable10x9-tr12-bdbs-20260512`),
  NOT the freeze r800. That explains the messy/low pre-fix baseline. They are now on
  a7win.
- **Master_Tag CDC product string changed** after this reflash to
  `usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00` (was
  `..._BioSpur_BLE_Control_...`). Use this new path for `--port`. SNR 1050070698, suffix
  6918E0384172A49F unchanged.
- Only 3 of 6 tags are powered/online (BSF66F, BS9336, BSCCF4). The other 3 (BS2DCE,
  BSDC91, BS955A) can be OTA'd identically once powered — the a7win payload is already
  embedded in Master_Tag.
- Anchors unchanged; no anchor OTA needed for this fix.
- Do NOT re-enable RFD / tag CIR / hot-path TR diag — they were confirmed OFF in this
  build and are required off for this baseline.
