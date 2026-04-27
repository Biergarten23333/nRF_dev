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
