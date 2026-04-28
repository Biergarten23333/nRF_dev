# Alt SS-TWR Verification Report - 2026-04-27

## Scope
All work was performed inside `alt-SS-TWR/`. Production workspace files were not edited.

## Flashed / OTA State

### Master_Tag carrier
- Device: `1050070698`
- Carrier build: `build-master-control-b120-m1-master-tag-lfrc-alt-ss-twr-tag-v4-rxrestart-carrier`
- Embedded tag payload marker: `alt-ss-twr-tag-v4-rxrestart`
- Flash method: `scripts/flash_master_control_b120_m1_noninteractive.sh` with explicit `B120_SNR=1050070698`
- Flash result: verify passed, script returned `action=ok`

### Tags OTA
- OTA log dir: `logs/alt_ss_twr_tag_ota_v4_rxrestart_direct_20260427_021611`
- `BSF66F`: success, `ota_success_observed`
- `BS2DCE`: success, `ota_success_observed`
- `BSDC91`: success, `ota_success_observed`

### Master_Anchor / Anchors
- Master_Anchor was used via serial only for runtime responder force.
- Runtime responder command result: `ready=8/8`, `rc=0`.

## Capture Results

### Main v4 capture
- Session: `logs/alt_ss_twr_capture_v4_rxrestart_skip_probe_20260427_022210_20260427_022210`
- `cm_all`: 6835
- `cs_all`: 1068
- `cr_all`: 4412
- `cf_all`: 1068
- `positions_all`: 0

### Timing verification
`cf_all.csv` shows burst-poll timing is working:

| Tag | poll_count | first_to_last_us median | first_to_last_us P95 | frame_us median |
|---|---:|---:|---:|---:|
| BSF66F | 4 | 579 us | 579 us | ~22186 us |
| BS2DCE | 4 | 579 us | 579 us | ~16678 us |
| BSDC91 | 4 | 579 us | 579 us | ~17181 us |

Baseline production `first_to_last_us` was ~8100-8200 us, so the burst-poll TX side achieved about 14x reduction.

## Failure
All current alt frames are timing out in the response phase:

- `BSF66F`: all `cal_static` anchors timeout.
- `BS2DCE`: all `cal_roto` selected anchors timeout.
- `BSDC91`: all `cal_roto` selected anchors timeout.

No positions are produced because no valid responses are received.

## Listener Evidence
- Listener/capture session: `logs/alt_ss_twr_with_listener_v4_rxrestart_20260427_022418`
- Listener saw `UF` frames only with code `0xe0` (poll): 327 frames.
- Listener saw zero `0xe1` response frames.

Conclusion: the tag is transmitting burst polls on-air, but anchors are not transmitting response frames. This is not just a tag RX-window miss.

## Root-Cause Direction
The next problem is anchor-side response scheduling / delayed TX under alt mode.

Most likely areas:
- Anchor receives poll but delayed TX is missing its response slot.
- Anchor does not match the new alt poll frame fields and ignores polls.
- Anchor runtime responder image currently on devices is not actually the alt responder despite OTA success logs.

## Attempted Diagnostic Build
Tried to build `alt-ss-twr-anchor-v2-diag` with low-rate responder summaries enabled:

- `APP_ANCHOR_RESPONDER_PRINTK_ENABLE=1`
- `APP_ANCHOR_RESPONDER_PROFILE_ENABLE=1`
- `APP_ANCHOR_RESPONDER_DIAG_PERIOD_MS=2000`

Build did not complete due NCS/sysbuild environment issues around MCUBoot configuration/includes, not because of alt source errors. This should be retried from a clean NCS shell or by reusing the exact environment used for the earlier successful anchor v1 build.

## Current Verdict
Partial success:
- Flash/OTA succeeded.
- Tag burst-poll TX path is verified and dramatically faster.
- Full alt SS-TWR ranging is not working yet because anchor response TX is absent on-air.

Next required step:
- Build/OTA an anchor diagnostic image and inspect responder counters: `tag_poll`, `tag_ok`, `tag_tx_miss`, `ignored_nonpoll`, and delayed-TX slack.

## 2026-04-28 Update

### Proven Fix
- Tag `alt-bcast-v7-tag-addrfix` fixed the earlier full response blackout.
- Evidence: `logs/alt_bcast_v7_addrfix_focus_BSF66F_20260428_165925`
  - `CM ok=242`, `timeout=136`
  - `CF first_to_last_us=0`, `poll_count=4`
  - Responses are now received by the Tag, so the previous `AFFREJ` hardware-frame-filter problem is fixed.

### Current Ranging Blocker
- Ranging is still not complete because response slots alternate OK/timeout:
  - A/C/E/G mostly OK
  - B/D/F/H mostly timeout
- This strongly points at the 800 us response spacing being too tight for the current Tag receive/restart path, or at needing a scheduled RX strategy for the expected response slots.

### Failed Diagnostic Branch
- Built and OTA'd BSF66F with `alt-bcast-v8-rxfast`, which attempted to restart RX immediately after reading a response frame.
- Evidence: `logs/alt_bcast_v8_rxfast_focus_BSF66F_20260428_172017`
  - Status pattern did not improve: `ok=240`, `timeout=134`
  - It also made `frame_us` much worse in the latest frame (`222869 us`), so this approach was rejected.
- Source patch was reverted locally after the failed test.
- BSF66F was restored by OTA to `alt-bcast-v7-tag-addrfix`:
  - `logs/tag_BSF66F_restore_alt_bcast_v7_tag_addrfix_20260428_172218`
  - post version matched.

### OTA / Carrier State
- Master_Tag was restored to `build-master-control-b120-m1-master-tag-lfrc-alt-bcast-v7-tag-addrfix-carrier`.
- Master_Anchor was restored to `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-v6-trxoff-original-ota-carrier`.
- Both B120 flashes used repo J-Link scripts with explicit SNR and LFRC assert.
- No Anchor or Tag was J-Link flashed during this update.

### Anchor v8 Spacing Attempt
- Anchor v8 `alt-bcast-v8-spacing1200` image was built and payload-verified as `anchor_ota_bundle`.
- Master_Anchor v8 carrier could connect to Anchor DFU, but Anchor OTA got stuck after `DFU SMP service ready`: image-state read commands got no SMP response.
- This happened for A and B, so the v8 carrier path was not used further.
- Anchors remain on the previously working v6 responder image.

### Next Best Step
- Do not continue the rejected rxfast Tag branch.
- Either:
  - fix the Anchor OTA/carrier SMP-ready-but-no-response issue, then OTA an Anchor build with `APP_ALT_SS_TWR_RESP_SPACING_US=1200`; or
  - implement a more precise Tag-side scheduled RX-slot receiver instead of the naive immediate restart approach.

## 2026-04-28 Anchor OTA Build Realignment

The v11 Anchor image is not trusted. After direct-flashing A with v11, A still
failed OTA at the same point: DFU SMP service ready, then no response to the
first `img state read`. This proved the problem was in the image/build path,
not only in old firmware residue.

The Anchor OTA build path in `alt-SS-TWR` has now been realigned to the outside
workspace build:

- `scripts/build_anchor_ota_control_bundle.sh` was copied from the outside
  workspace into `alt-SS-TWR/scripts/`.
- Anchor OTA runtime/control sources were copied from the outside workspace:
  - `apps/master_control/src/main.c`
  - `apps/master/src/master_multi_app.c`
  - `src/anchors/unified/anchor_ble_ctrl.c`
  - `src/anchors/unified/anchor_mcumgr_diag.c`
  - `src/anchors/unified/anchor_mcumgr_diag.h`
- `apps/anchor/prj.conf` and `apps/anchor/prj_ota.conf` match the outside
  workspace.
- `apps/anchor/CMakeLists.txt` now differs from the outside workspace only by
  the Alt SS-TWR/UWB compile definitions needed for the experiment.
- OTA/MCUmgr/BLE/DFU Kconfig comparison between outside stable
  `build-anchor-unified-ota-anchor-runtime-force-20260426_220551` and alt v12
  reports `ota_config_diff_count 0`.

Current v12 artifacts:

- Anchor build:
  `build-anchor-unified-ota-alt-bcast-v12-spacing1000-anchorbuild1to1`
- Master_Anchor B120 carrier:
  `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-v12-spacing1000-anchorbuild1to1-carrier`
- Marker:
  `alt-bcast-v12-spacing1000-anchorbuild1to1`
- Anchor compile settings:
  - `APP_ALT_SS_TWR_ENABLE=1`
  - `APP_ALT_SS_TWR_GUARD_US=2000`
  - `APP_ALT_SS_TWR_POLL_SPACING_US=200`
  - `APP_ALT_SS_TWR_RESP_SPACING_US=1000`
  - `APP_UWB_HW_FRAME_FILTER_ENABLE=0`
  - `APP_ANCHOR_RESPONDER_PRINTK_ENABLE=0`

Validation after flashing only the Master_Anchor B120 carrier:

- B120 LFRC assert passed.
- Master_Anchor `960148546` was flashed with repo J-Link script and explicit
  SNR.
- Control-plane runtime verify passed:
  `logs/v12_anchorbuild1to1_responder_verify_20260428_204834/verify.log`
  with `sent=8 ready=8/8`.

Remaining blocker:

- A is still running the earlier bad v11 Anchor image because the one-time
  direct Anchor flash authorization was already used on v11.
- OTA to A with the v12 carrier still fails at the same A1 point:
  `logs/anchor_A_ota_alt_bcast_v12_anchorbuild1to1_probe_20260428_204932/single_shot.log`
  - UUID target matched A: `4DC6B8187E33803AE8601FB0D7992B96`
  - MTU exchange completed: `mtu=498`
  - DFU SMP service found: `smp=0x001f ccc=0x0020`
  - Subscribe succeeded.
  - `img state read` sent five times with rc=0 at the BLE write layer.
  - No SMP response was notified by A.

Conclusion: v12 Master_Anchor/control/OTA payload path is aligned and can
control all 8 anchors, but A cannot OTA out of the already-flashed bad v11
Anchor image. To prove v12 Anchor OTA runtime, A needs one more explicitly
authorized direct recovery flash to the v12 Anchor build, then the next Anchor
update must be performed by OTA only.

## 2026-04-28 A Direct Recovery And v13 OTA-Safety Attempt

User explicitly authorized one more direct recovery flash for Anchor A only.

Action taken:

- Direct-flashed A only:
  - SNR: `760186071`
  - build: `build-anchor-unified-ota-alt-bcast-v12-spacing1000-anchorbuild1to1`
  - image: `merged.hex`
  - result: program/verify OK
- Phase-A strict OTA preflight passed:
  `logs/anchor_A_v12_after_direct_flash_phaseA_20260428_205422`
- Full OTA probe against A still failed A1:
  `logs/anchor_A_v12_after_direct_flash_full_ota_probe_20260428_205439`
  - DFU SMP service discovered
  - MTU 498
  - `img state read` writes returned rc=0 at BLE write layer
  - no SMP response/notify from A

This proves v12 itself still does not service MCUmgr SMP while running the
current Alt responder runtime.

Likely cause found in code:

- Anchor OTA Kconfig and MCUmgr setup are still aligned with outside stable.
- The remaining Anchor runtime source differences are only Alt SS-TWR files.
- The Alt responder RX loop can run as a tight UWB loop with
  `APP_ANCHOR_RESPONDER_COOP_SLEEP_MS=0`.
- `anchor_mcumgr_diag_ota_active()` becomes true only after an IMG/DFU event is
  processed, so it cannot protect the very first `img state read` from
  responder starvation.

Built v13 as the smallest runtime scheduling test:

- Anchor build:
  `build-anchor-unified-ota-alt-bcast-v13-spacing1000-coop1`
- Master_Anchor carrier:
  `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-v13-spacing1000-coop1-carrier`
- Marker:
  `alt-bcast-v13-spacing1000-coop1`
- Only scheduling change:
  `APP_ANCHOR_RESPONDER_COOP_SLEEP_MS=1`
- Other key Alt settings stayed:
  - `APP_ALT_SS_TWR_ENABLE=1`
  - `APP_ALT_SS_TWR_RESP_SPACING_US=1000`
  - `APP_UWB_HW_FRAME_FILTER_ENABLE=0`
  - `APP_ANCHOR_RESPONDER_PRINTK_ENABLE=0`
- Payload verification passed as `anchor_ota_bundle`.
- B120 LFRC assert passed.
- Master_Anchor `960148546` was flashed with v13 carrier successfully.

OTA attempt from current A/v12 to v13 still failed A1:

- Log: `logs/anchor_A_v12_to_v13_coop1_ota_probe_20260428_210042`
- Same failure point: DFU ready, no SMP response to `img state read`.

Conclusion:

- Current A/v12 cannot OTA itself to v13 because its own SMP request handling is
  still starved/broken before the v13 payload can run.
- To test whether v13 fixes OTA serviceability, A must be directly recovered
  once more to v13, then a following v13->v14 OTA should be tested. Do not
  direct-flash any other Anchor without explicit authorization.

## 2026-04-28 A v13 Direct Recovery Then v13->v14 OTA Verification

User explicitly authorized one direct recovery flash of Anchor A to v13, then an
immediate OTA-only update from v13 to v14.

Action taken:

- Direct-flashed Anchor A only:
  - SNR: `760186071`
  - UUID: `4DC6B8187E33803AE8601FB0D7992B96`
  - build: `build-anchor-unified-ota-alt-bcast-v13-spacing1000-coop1`
  - result: program/verify OK
- Built v14 with the same Alt/OTA settings as v13, only changing marker:
  - Anchor build: `build-anchor-unified-ota-alt-bcast-v14-spacing1000-coop1`
  - Master_Anchor carrier:
    `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-v14-spacing1000-coop1-carrier`
  - Marker: `alt-bcast-v14-spacing1000-coop1`
  - Payload verification passed as `anchor_ota_bundle`.
  - B120 LFRC assert passed.
- Flashed Master_Anchor `960148546` with the v14 carrier using the repository
  J-Link script and explicit SNR.
- Ran OTA from A/v13 to A/v14:
  `logs/anchor_A_v13_to_v14_coop1_ota_20260428_210751/single_shot.log`

Result:

- `phase_a_ok=true`
- `phase_b_ok=true`
- `ota_started=true`
- `dfu_ready_seen=true`
- `ota_upload_started_seen=true`
- `ota_upload_progress_seen=true`
- `ota_upload_complete_seen=true`
- `ota_pending_test_seen=true`
- `ota_reset_request_seen=true`
- `ota_success_seen=true`
- `classification=D`
- `reason=ota_success_observed`

Conclusion:

- `APP_ANCHOR_RESPONDER_COOP_SLEEP_MS=1` restores the Anchor A OTA path after
  direct recovery to v13.
- The earlier A1/DFU-ready stall was not caused by the copied original OTA
  bundle logic. It was caused by the already-running v12 Alt responder runtime
  starving or blocking the first MCUmgr SMP request before a new OTA image could
  run.
- Keep the coop1 setting in future Alt Anchor builds unless a better responder
  scheduling fix is proven.
- Do not direct-flash other Anchors unless explicitly authorized. Future Anchor
  updates should proceed by OTA using the restored original bundle flow.
