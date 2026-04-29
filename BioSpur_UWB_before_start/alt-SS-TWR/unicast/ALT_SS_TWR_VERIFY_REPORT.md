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

## 2026-04-28 B-H Direct Recovery And A-H OTA Verification

User explicitly authorized one direct recovery flash pass for Anchors B-H, then
a full A-H OTA verification.

Direct recovery action:

- B: `760185876`
- C: `760185878`
- D: `760186081`
- E: `760185904`
- F: `760186124`
- G: `760185889`
- H: `760186121`

All B-H direct recovery flashes used:

- build: `build-anchor-unified-ota-alt-bcast-v14-spacing1000-coop1`
- image: `merged.hex`
- tool path: repository `scripts/flash_anchor_auto.sh`
- transport: `JLinkExe -SelectEmuBySN <snr>`
- result: program/verify OK for all seven boards

Built v15 for the OTA verification:

- Anchor build: `build-anchor-unified-ota-alt-bcast-v15-spacing1000-coop1`
- Master_Anchor carrier:
  `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-v15-spacing1000-coop1-carrier`
- Marker: `alt-bcast-v15-spacing1000-coop1`
- Payload verification passed as `anchor_ota_bundle`.
- B120 LFRC assert passed.
- Master_Anchor `960148546` was flashed with the v15 carrier using the
  repository J-Link script and explicit SNR.

Full A-H OTA result:

- Log root:
  `logs/anchor_AH_v14_to_v15_full_ota_20260428_211422`
- A: `ota_success_seen=true`, `classification=D`,
  `reason=ota_success_observed`
- B: `ota_success_seen=true`, `classification=D`,
  `reason=ota_success_observed`
- C: `ota_success_seen=true`, `classification=D`,
  `reason=ota_success_observed`
- D: `ota_success_seen=true`, `classification=D`,
  `reason=ota_success_observed`
- E: `ota_success_seen=true`, `classification=D`,
  `reason=ota_success_observed`
- F: `ota_success_seen=true`, `classification=D`,
  `reason=ota_success_observed`
- G: `ota_success_seen=true`, `classification=D`,
  `reason=ota_success_observed`
- H: `ota_success_seen=true`, `classification=D`,
  `reason=ota_success_observed`

Post-OTA responder verification:

- Log:
  `logs/anchor_AH_v14_to_v15_full_ota_20260428_211422/post_verify_all_responder_20260428_212212/verify.log`
- Summary:
  - `success=true`
  - `sent_count=8`
  - `ready_count=8`
  - `ready_target=8`

Known remaining caveat:

- Final `ANCHOR VERSION post` still reports `actual=-` for A-H and the deploy
  wrapper exits non-zero because of that marker check.
- This is the same version-query/control-path observability issue already seen
  before. It does not match the OTA transport result: all eight OTA uploads,
  pending-test/reset steps, and responder runtime verification succeeded.

Conclusion:

- B-H are recovered to an OTA-serviceable coop1 baseline.
- A-H can now complete a full OTA pass with the original copied OTA bundle flow.
- Continue Alt SS-TWR ranging work from this v15 coop1 baseline. Do not perform
  more Anchor direct flash operations unless explicitly authorized again.

## 2026-04-28 Anchor OTA Version Match Fixed

Problem:

- `ANCHOR VERSION post` reported `actual=- match=False` even after successful
  OTA and responder runtime verification.
- Root cause: the previous post-version path depended on synchronous version
  result handling. Anchors did return the new marker via control notify, but the
  notify arrived after the Master-side synchronous wait had timed out.

Fix in `alt-SS-TWR/`:

- Added an Anchor runtime `VERSION` control command that reports the active
  firmware marker through the reliable result-notify path as `ANCHOR_FW ...`.
- Updated Master_Anchor version query plumbing to request that control command.
- Updated `scripts/ota_deploy_anchor_set.py` to parse both structured
  `ANCHOR_VERSION query=...` lines and late
  `ANCHOR_CTRL[...] notify: ANCHOR_FW ...` lines.

Validation:

- Anchor OTA image:
  `build-anchor-unified-ota-alt-bcast-v16-coop1-ver`
- Marker: `alt-bcast-v16-coop1-ver`
- Payload guard passed as `anchor_ota_bundle`, with no tag markers.
- Master_Anchor carrier:
  `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-v16-coop1-ver-carrier`
- B120 LFRC assert passed.
- Master_Anchor `960148546` was flashed with the repository B120 J-Link script
  and explicit SNR.
- Full A-H OTA log:
  `logs/anchor_AH_v15_to_v16_versionnotify_20260428_214016`
- A-H all completed OTA with `ota_success_seen=true`,
  `classification=D`, and `reason=ota_success_observed`.
- Post responder verify:
  `logs/anchor_AH_v15_to_v16_versionnotify_20260428_214016/post_verify_all_responder_20260428_215000/verify.log`
  reported `success=true`, `sent_count=8`, `ready_count=8`,
  `ready_target=8`.
- Re-run post version verify:
  `logs/anchor_AH_v15_to_v16_versionnotify_rerun_post_20260428_215356/post_anchor_version_verify.log`
  reported `strict_ok=True`.

Version match result:

- A-H all reported
  `actual=alt-bcast-v16-coop1-ver match=True`.

Conclusion:

- OTA transport is working.
- The version-match standard now works for A-H using the Anchor runtime marker
  notify path.
- Continue Alt SS-TWR ranging validation from v16.

## 2026-04-28 v16 3-Tag Listener Capture

Capture:

- Log root:
  `logs/alt_v16_3tag_listener_anchorserial_20260428_220007`
- Tags:
  - `BSF66F:static`
  - `BS2DCE:roto`
  - `BSDC91:roto`
- Anchor preflight:
  - `success=true`
  - `sent_count=8`
  - `ready_count=8`
  - `ready_target=8`

Result summary:

- `positions_all=0`
- `cm_all=9499`
- `cs_all=1620`
- `cr_all=6821`
- `cf_all=1632`
- The session marked `startup_failed=true` for `BSDC91`.

Listener note:

- The first listener process ended before the real Tag capture because anchor
  preflight consumed the initial listener window.
- A second late listener was started during the active capture:
  `logs/alt_v16_3tag_listener_anchorserial_20260428_220007/listener_late/listener_20260428_220235`
- Late listener result:
  - `uf_rows=1813`
  - `ul_rows=546`
  - `UF code=0xe0` count `1267`
  - `UL/response code=0xe1` count `546`
  - Responses were seen from A-H.

Important finding:

- This is no longer the previous "listener sees poll but no anchor response"
  failure. Listener now sees anchor responses on air.
- Tag-side ranging is still not solved:
  - `BSF66F`: many partial OK ranges, but no full position.
  - `BS2DCE`: mostly timeout.
  - `BSDC91`: mostly timeout and triggered startup failure.

Timing:

- `BSF66F` CF:
  - `first_to_last_us=0`
  - `poll_count=4`
- `BS2DCE` and `BSDC91` CF:
  - `first_to_last_us=579`
  - `poll_count=4`

Tag firmware check:

- `BSF66F`: `alt-bcast-v7-tag-addrfix`, tag id `3`
- `BS2DCE`: `alt-ss-twr-tag-v4-rxrestart`, tag id `1`
- `BSDC91`: `alt-ss-twr-tag-v4-rxrestart`, tag id `2`

Conclusion:

- Anchors A-H are online and responding over UWB.
- The three Tags are not running the same Alt broadcast baseline.
- Next step should be to OTA `BS2DCE` and `BSDC91` to the same broadcast Tag
  image family as `BSF66F`, then rerun the 3-tag listener capture with a longer
  listener window that starts after or spans anchor preflight.

## 2026-04-28 Tag OTA To Broadcast v9

Goal:

- Put all three online Tags on the same latest Alt broadcast Tag image before
  rerunning the 3-tag capture.

Payload:

- Tag build: `build-alt-tag-bcast-v9-spacing1000`
- Marker: `alt-bcast-v9-spacing1000`
- Settings:
  - `APP_ALT_SS_TWR_ENABLE=1`
  - `APP_ALT_SS_TWR_GUARD_US=2000`
  - `APP_ALT_SS_TWR_POLL_SPACING_US=200`
  - `APP_ALT_SS_TWR_RESP_SPACING_US=1000`
- Payload guard passed as `tag_ota_bundle`.

Master_Tag carrier:

- Build:
  `build-master-control-b120-m1-master-tag-lfrc-alt-bcast-v9-spacing1000-carrier`
- B120 LFRC assert passed.
- Master_Tag `1050070698` was flashed with the repository B120 J-Link script
  and explicit SNR.

OTA result:

- Log root:
  `logs/tag_all_to_alt_bcast_v9_spacing1000_20260428_221229`
- `BSF66F`: OTA success, post version
  `actual=alt-bcast-v9-spacing1000 match=True`
- `BS2DCE`: OTA success, post version
  `actual=alt-bcast-v9-spacing1000 match=True`
- `BSDC91`: OTA success, post version
  `actual=alt-bcast-v9-spacing1000 match=True`

Conclusion:

- All three Tags now match the same latest Alt broadcast v9 Tag image.
- Next step is to rerun 3-tag listener + anchor serial capture with a listener
  window long enough to cover anchor preflight and the active Tag capture.

## 2026-04-28 v9 3-Tag Broadcast Capture

Capture:

- Log root:
  `logs/alt_v9_3tag_listener_anchorserial_20260428_221928`
- Recv session:
  `logs/alt_v9_3tag_listener_anchorserial_20260428_221928/recv_20260428_221930`
- Listener:
  `logs/alt_v9_3tag_listener_anchorserial_20260428_221928/listener/listener_20260428_221929`
- Tags:
  - `BSF66F:static`
  - `BS2DCE:roto`
  - `BSDC91:roto`
- Listener window was extended to cover anchor preflight and active capture.

Run status:

- Capture `success=true`
- `startup_failed=false`
- Anchor preflight `success=true`, `ready_count=8`, `ready_target=8`
- `positions_all=0`
- `cm_all=6861`
- `cs_all=2180`
- `cr_all=9025`
- `cf_all=2182`

Timing objective:

- `BSF66F`: `first_to_last_us=0`, `poll_count=4`
- `BS2DCE`: `first_to_last_us=0`, `poll_count=4`
- `BSDC91`: `first_to_last_us=0`, `poll_count=4`

Tag-side range status:

- `BSF66F`:
  - `ok=748`
  - `timeout=602`
  - `reject=3`
- `BS2DCE`:
  - `ok=946`
  - `timeout=1784`
  - `reject=10`
- `BSDC91`:
  - `ok=1776`
  - `timeout=980`
  - `reject=12`

Listener:

- `UF rows=1847`
- `UL rows=19`
- `UF code=0xe0` count `1828`
- `UL/response code=0xe1` count `19`
- Listener saw response frames from A/E/G/H only in this run.

Interpretation:

- All three Tags are now using the broadcast Alt timing path.
- The timing goal is achieved for all three Tags: every CF row has
  `first_to_last_us=0`.
- Ranging is partially working at the Tag side: thousands of `CM ok` rows were
  produced after the v9 Tag OTA.
- Full positioning is still not working because each sweep still has too many
  timeout/error anchors for a stable solve.
- Listener visibility is now inconsistent with Tag-side CM: listener saw only
  19 response frames while Tags reported 3470 `CM ok` rows. This needs a
  focused next diagnostic; do not interpret listener UL count alone as Tag
  receive count.

Next technical focus:

- Investigate why per-sweep anchor subsets are asymmetric. Examples:
  - `BS2DCE` mostly gets A/E OK but C/G timeout.
  - `BSDC91` mostly gets A/E OK but C/H timeout.
  - `BSF66F` gets partial OK across more anchors but not consistently enough.
- Add/enable low-rate Anchor responder diag for v9/v10 that records recent
  matched tag src, mask, rank, delay, and tx result, then correlate with Tag
  `CM ok/timeout` by tag id and anchor id.

## 2026-04-29 A-H Direct Recovery To Alt Unicast v5 coop1

User explicitly authorized direct recovery flash for Anchors A-H before sleep.

Recovery target:

- build: `build-anchor-unified-ota-alt-unicast-v5-s1000-r2000-coop1`
- image: `merged.hex`
- marker: `alt-unicast-v5-s1000-r2000-coop1`
- key params:
  - `APP_ALT_SS_TWR_ENABLE=1`
  - `APP_ALT_SS_TWR_POLL_SPACING_US=1000`
  - `APP_ALT_SS_TWR_RESP_SPACING_US=2000`
  - `APP_ANCHOR_RESPONDER_COOP_SLEEP_MS=1`
  - `APP_UWB_HW_FRAME_FILTER_ENABLE=1`

Direct recovery flash:

- command wrapper: `scripts/flash_all_anchors.sh`
- per-board transport: `JLinkExe -NoGui 1 -SelectEmuBySN <snr>`
- no `nrfjprog`
- log root: `logs/recovery_AH_direct_flash_v5_coop1_20260429_002210`
- result: A-H all completed J-Link program/verify OK.

Post-flash verification:

- Runtime responder verify log:
  `logs/recovery_AH_v5_coop1_post_flash_responder_verify_20260429_002656`
- Result:
  - `success=true`
  - `sent_count=8`
  - `ready_count=8`
  - `ready_target=8`

OTA gate verification:

- A-only OTA gate probe after Master_Anchor reset:
  `logs/recovery_AH_v5_coop1_anchor_A_ota_gate_probe_after_master_reset_20260429_003155`
- Result:
  - `classification=D`
  - `ota_started=true`
  - `dfu_ready_seen=true`
  - `ota_upload_started_seen=true`
  - `ota_upload_complete_seen=true`
  - `ota_success_seen=true`
  - `reason=ota_success_observed`

Important interpretation:

- The earlier DFU-ready/SMP stall was caused by the already-running
  `alt-unicast-v1-s1000` Anchor image, which had
  `APP_ANCHOR_RESPONDER_COOP_SLEEP_MS=0`.
- After direct recovery to v5 coop1, A-H control links recover and A proves the
  OTA gate can pass image-state read and complete upload.
- `anchor version all` is still flaky as a control-plane query under active
  scan/connect churn; do not use that alone to judge recovery success.

Final state left for the next session:

- Master_Anchor is back in `AUTOPOS`.
- OTA target UUID was cleared back to `-`.
- Master_Anchor status after cleanup: `mode=AUTOPOS pending=0`.

## 2026-04-29 Overnight Alt Unicast Tag v12/v13 Gate Diagnosis

Goal:

- Continue Phase 1/2 Alt SS-TWR unicast burst validation with 3 online Tags.
- Keep Anchors on the recovered stable OTA base; do not direct-flash Anchors.
- Test whether the remaining ROTO imbalance comes from Tag-side range gates.

Deployed Tag images:

- v12 marker: `alt-u12-unicast-s1000-r2000-rawgate0`
  - unicast burst
  - poll spacing 1000 us
  - response spacing 2000 us
  - raw range delta gate disabled
  - continuity gate still enabled
- v13 marker: `alt-u13-unicast-s1000-r2000-raw0-cont0`
  - same as v12
  - additionally `APP_TAG_RANGE_CONTINUITY_ENABLE=0`

OTA / carrier verification:

- Master_Tag B120 carrier builds used LFRC and passed
  `scripts/assert_b120_internal_osc_build.sh`.
- Master_Tag was flashed only with explicit SNR `1050070698`.
- Tag OTA match:
  - v12: `BSF66F`, `BS2DCE`, `BSDC91` all `match=True`
  - v13: `BSF66F`, `BS2DCE`, `BSDC91` all `match=True`

Key logs:

- v12 single BS2DCE:
  `logs/alt_u12_rawgate0_BS2DCE_motion_1tag_listener_anchorserial_20260429_031032`
- v12 3 Tag:
  `logs/alt_u12_rawgate0_3tag_motion_listener_anchorserial_20260429_031347`
- v13 3 Tag:
  `logs/alt_u13_raw0_cont0_3tag_motion_listener_anchorserial_20260429_032540`

Results:

- v10 3 Tag baseline for this overnight sequence:
  - total positions: 209
  - `BSF66F=201`, `BS2DCE=5`, `BSDC91=3`
- v12 3 Tag:
  - total positions: 209
  - `BSF66F=138`, `BS2DCE=31`, `BSDC91=40`
  - raw gate removal improved ROTO Tags but cadence was still poor.
- v13 3 Tag:
  - total positions: 799 in 90 s
  - `BSF66F=201`, `BS2DCE=332`, `BSDC91=266`
  - median motion dt:
    - `BSF66F=400 ms`
    - `BS2DCE=201 ms`
    - `BSDC91=211 ms`
  - median RMS:
    - `BSF66F=96 mm`
    - `BS2DCE=40 mm`
    - `BSDC91=30.5 mm`
  - listener saw active UWB traffic: `uf=4288`, `ul=2035`

Interpretation:

- Anchor responder path is not the current blocker. A-H responder preflight was
  repeatedly `ready=8/8`, and listener saw many anchor responses.
- The v10/v12 imbalance was mainly Tag-side range plausibility / continuity
  gating under moving ROTO geometry.
- Disabling both the raw delta gate and continuity gate makes 3 Tag Alt
  unicast burst produce stable positioning data.
- Next validation should compare v13 against earlier standard SS-TWR data and
  run static/roto profile captures, then decide whether to replace the hard
  continuity disable with a motion-aware softer gate.

Static/ROTO mixed validation:

- v13 static/roto log:
  `logs/alt_u13_raw0_cont0_static_roto_3tag_listener_anchorserial_20260429_032936`
- Profiles:
  - `BSF66F:static`
  - `BS2DCE:roto`
  - `BSDC91:roto`
- Result:
  - `cm_all=4882`
  - `cf_all=1594`
  - `CM ok=4201/4882 = 86.1%`
  - per Tag CM ok:
    - `BSF66F=1111/1354 = 82.1%`
    - `BS2DCE=1562/1750 = 89.3%`
    - `BSDC91=1528/1778 = 85.9%`
  - CF median `first_to_last_us`:
    - `BSF66F=5187`
    - `BS2DCE=5279`
    - `BSDC91=5218`
  - CF success:
    - `BSF66F=89/451`
    - `BS2DCE=370/567`
    - `BSDC91=354/576`

800 us spacing experiment:

- v14 marker: `alt-u14-unicast-s800-r2000-raw0-cont0`
- v14 static/roto log:
  `logs/alt_u14_s800_raw0_cont0_static_roto_3tag_listener_anchorserial_20260429_033920`
- Result:
  - `cm_all=6644`
  - `cf_all=2101`
  - `CM ok=3866/6644 = 58.2%`
  - per Tag CM ok:
    - `BSF66F=792/1350 = 58.7%`
    - `BS2DCE=1464/2591 = 56.5%`
    - `BSDC91=1610/2703 = 59.6%`
  - CF median `first_to_last_us` improved to roughly 4.6 ms, but CF success
    collapsed:
    - `BSF66F=0/450`
    - `BS2DCE=12/824`
    - `BSDC91=4/827`

Conclusion from v14:

- 800 us poll spacing is not a safe point with the current Anchor v7 responder
  and Tag v13/v14 timing. It increases attempted frame rate but costs too many
  anchor responses.
- Current best-known stable Alt unicast point is v13:
  `s1000-r2000-raw0-cont0`.
- After the v14 test, all three Tags were restored by OTA back to v13:
  `BSF66F`, `BS2DCE`, `BSDC91` all `match=True`.

2026-04-29 Tag-only spacing compression on stable Anchor a21:

- Safety boundary:
  - Anchor A-H stayed on stable `alt-a21-s1000-affrejfast`.
  - Anchor OTA logic was not modified.
  - Tag OTA used the existing `ota_deploy_tag_set.py` path.
- Built Tag-only images:
  - `alt-u18-tagonly-s600-a21resp`
  - `alt-u18-tagonly-s300-a21resp`
- Deployed `alt-u18-tagonly-s600-a21resp` to all three Tags:
  - `BSF66F`, `BS2DCE`, `BSDC91` all post-version `match=True`.
- s600 3 Tag static/roto capture:
  - log: `logs/alt_u18_s600_a21_3tag_listener_anchorserial_20260429_140120`
  - `first_to_last_us`: median `1739`, p95 `2014`
  - `poll_count=4` for all CF rows
  - `CM ok=611/2532`, `timeout=1869`, `reject=52`
  - listener saw responses again: `UF=360`, `UL=143`
- s600 BSF66F single-Tag static capture:
  - log: `logs/alt_u18_s600_a21_BSF66F_1tag_listener_anchorserial_20260429_140249`
  - `first_to_last_us`: median `1739`, p95 `1892`
  - `CM ok=407/451`, `timeout=9`, `reject=35`
- Deployed `alt-u18-tagonly-s300-a21resp` to BSF66F only:
  - post-version `match=True`.
- s300 BSF66F single-Tag static capture:
  - log: `logs/alt_u18_s300_a21_BSF66F_1tag_listener_anchorserial_20260429_140640`
  - `first_to_last_us`: median `1678`, p95 `1922`
  - `CM ok=411/450`, `timeout=17`, `reject=22`

Interpretation:

- Lowering requested spacing from 600 us to 300 us did not reduce
  `first_to_last_us` below about 1.65-1.75 ms.
- The current unicast burst path sends each poll with `DWT_START_TX_IMMEDIATE`,
  waits for `SYS_STATUS_TXFRS`, then writes and starts the next frame. With one
  DW1000 TX buffer, this creates an observed floor of roughly 550 us per
  poll-to-poll gap for the current PHY and driver path.
- Therefore the current Tag-only unicast implementation cannot reach
  sub-1000 us for four separate unicast poll frames by changing
  `APP_ALT_SS_TWR_POLL_SPACING_US` alone.
- s600/s300 are viable single-Tag ranging experiments, but s600 3 Tag is not
  stable enough yet. Multi-Tag work needs TDMA window/slot tuning after the
  single-Tag timing path is understood.

2026-04-29 u20 unicast timing diagnostic:

- Code-only change:
  - Added build-time `APP_TAG_ALT_POLL_DIAG_PERIOD_MS`.
  - Added low-rate Tag timing summary in `ss_twr_init.c`.
  - OTA scripts and OTA logic were not changed.
- Test image:
  - `alt-u20-timingdiag-s300-a21resp`
  - Deployed to BSF66F only, then restored to stable u17 after capture.
- Capture:
  - `logs/alt_u20_timingdiag_s300_BSF66F_1tag_listener_anchorserial_20260429_144708`
  - `first_to_last_us`: latest/typical around `1708 us`.
  - `CM ok=282/301`, `timeout=12`, `reject=7`.
- Timing diagnostic examples from raw log:
  - `gap=579,549,579;write=152,183,152,152;txfrs=335,335,335,396`
  - `gap=579,549,579;write=152,152,152,152;txfrs=335,335,335,335`
  - `gap=549,579,549;write=244,152,152,152;txfrs=305,335,305,335`
- Interpretation:
  - Requested spacing was `300 us`, but the measured poll-to-poll gaps stayed
    around `549-579 us`.
  - The DW1000 TX-complete wait alone is roughly `305-335 us` per poll at the
    current PHY, and the per-poll write/control path is roughly `152-183 us`.
  - Therefore four separate unicast poll frames cannot reach sub-1000 us by
    lowering `APP_ALT_SS_TWR_POLL_SPACING_US` alone. The next options are
    protocol/PHY changes: shorter PHY preamble, lower driver/SPI overhead, or
    moving back toward a single aggregate/broadcast poll with deterministic
    anchor response slots.

    
2026-04-29 Unicast Baseline Freeze
Frozen Configuration
The most stable Alt SS-TWR unicast configuration proven in this verification cycle is:

Tag side:

Marker: alt-u13-unicast-s1000-r2000-raw0-cont0
APP_ALT_SS_TWR_ENABLE=1
APP_ALT_SS_TWR_POLL_SPACING_US=1000
APP_ALT_SS_TWR_RESP_SPACING_US=2000
APP_ALT_SS_TWR_GUARD_US=2000
APP_TAG_RANGE_RAW_DELTA_GATE_ENABLE=0
APP_TAG_RANGE_CONTINUITY_ENABLE=0
Burst type: 4× unicast poll, one per anchor subset, DWT_START_TX_IMMEDIATE per frame
Anchor side:

Marker: alt-a21-s1000-affrejfast (or any coop1 anchor build with matching spacing)
APP_ALT_SS_TWR_ENABLE=1
APP_ALT_SS_TWR_POLL_SPACING_US=1000
APP_ALT_SS_TWR_RESP_SPACING_US=2000
APP_ANCHOR_RESPONDER_COOP_SLEEP_MS=1
APP_UWB_HW_FRAME_FILTER_ENABLE=1
Proven Performance
Reference capture: logs/alt_u13_raw0_cont0_3tag_motion_listener_anchorserial_20260429_032540

Metric	BSF66F (static)	BS2DCE (roto)	BSDC91 (roto)
Positions (90 s)	201	332	266
Median motion dt	400 ms	201 ms	211 ms
Median RMS	96 mm	40 mm	30.5 mm
Static/roto mixed validation: logs/alt_u13_raw0_cont0_static_roto_3tag_listener_anchorserial_20260429_032936

Metric	BSF66F	BS2DCE	BSDC91
CM ok rate	82.1%	89.3%	85.9%
CF median first_to_last_us	5187	5279	5218
Known Limits
DW1000 single-TX-buffer unicast burst floor: ~1650–1750 µs for 4 polls (u20 timing diagnostic).
Per-poll hardware path: write ~152 µs + txfrs ~335 µs ≈ 490 µs minimum gap.
APP_ALT_SS_TWR_POLL_SPACING_US below 600 has no further effect on measured first_to_last_us.
s800 (v14) collapsed CF success to near zero; s1000 is the lowest safe spacing.
Range gating (raw_delta and continuity) must stay disabled for roto profiles under current geometry. Re-enabling requires a motion-aware soft gate.
Disposition
This unicast baseline is frozen as the Alt SS-TWR fallback. No further unicast poll-spacing compression experiments are planned on the current DW1000 PHY/driver path.

The next development direction is Alt SS-TWR broadcast poll with deterministic anchor response slots, which eliminates the 4× unicast poll overhead and targets first_to_last_us ≈ 0 on the poll side.