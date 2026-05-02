# Broadcast Overnight Guard Bisect

## 2026-05-01 00:53:55 CEST - Stop at Step 1

Requested overnight flow stopped before b40 because Anchor a7 post-verify did not satisfy the required gate.

### Guard Tested

- Guard: `1600 us`
- Resp spacing: `1000 us`
- Anchor marker: `alt-bcast-a7-g1600-r1000-coop1`
- Tag marker: not built in this run because Step 1 failed

### Anchor OTA Result

- OTA log directory: `logs/alt_bcast_a7_g1600_anchor_ota_20260501_004144`
- A-H individual OTA stages: all reported `reason=ota_success_observed`
- Master_Anchor carrier was flashed with explicit SNR `960148546`
- No direct Anchor flash was used

### Post-Verify Result

- Post-verify summary: `post_verify_all_responder_20260501_004816/summary.json`
- Result: `success=false`
- Error: `runtime_responder_ack_failed`
- Attempts: 4
- Final role counts:
  - `matrix=0`
  - `responder=0`
  - `master=0`
  - `other=0`
- Required gate `responder runtime ready=8/8`: not met

### Capture Metrics

- No b40 tag build
- No tag OTA
- No capture
- `positions_all`: not measured
- `tf_all`: not measured
- Per-tag counts: not measured
- Rank0 present: not measured
- RXG timing summary: not measured
- Listener rank0 count: not measured

### Verdict

FAIL / STOP. Per overnight instructions, do not continue to b40 or guard bisect until Anchor runtime responder verification is recovered or explicitly waived.

Important nuance: the individual OTA uploads appear successful for A-H, but the Master_Anchor post-verify control/runtime path did not return responder ACKs. This should be treated as an Anchor runtime verification failure for this autonomous run, not as proof that the image upload failed.

## 2026-05-01 01:01:11 CEST - Direct UWB Diagnostic After a7

Ran a no-build/no-OTA 30s diagnostic using the current Tag firmware and current Anchor state, bypassing Anchor preflight on the second attempt.

### Firmware State

- Anchors: intended `alt-bcast-a7-g1600-r1000-coop1`
- Tags: current `alt-bcast-b39-ltdma-8anc-pretx-g1200-r1000`
- No firmware changes were made for this diagnostic.

### Preflight Observation

An accidental first attempt with Anchor preflight enabled showed a useful extra clue:

- Master_Anchor could connect/control `7/8` anchors repeatedly
- It never reached `8/8`
- Listener during that blocked attempt saw only broadcast polls and no UL responses

### No-Preflight Capture

- Log directory: `logs/alt_bcast_a7_b39_quick_uwb_diag_skippreflight_30s_20260501_005949`
- Duration: 30s capture, 65s listener
- Anchor preflight: skipped
- Targets: `BSF66F,BS2DCE,BSDC91`

Results:

- `positions_all=0`
- `cm_all=0`
- `cs_all=0`
- `cr_all=0`
- `cf_all=0`
- Raw Tag log:
  - `RXG=129`
  - `TS;=3`
  - `CM;=0`
  - `CF;=0`
  - `CR;=0`
- Listener:
  - `uf_rows=1481`
  - `ul_rows=0`
  - UF code: all `0xe0`
  - UF dst: all `0xffff`
  - UF src: `0xb101`, `0xb102`, `0xb103`

### Verdict

FAIL. The Tags are transmitting broadcast polls on air, but no Anchor response frames are visible to the listener and no ranging records are emitted. This points to an a7 Anchor runtime/image problem rather than a pure Master_Anchor CDC/VERSION readback problem.

Recommended recovery: rollback A-H to the known stable Anchor baseline `a5-g2000-r1000-coop1` before continuing broadcast guard work.

## 2026-05-01 01:13:01 CEST - Rollback to a5 and Re-test

Attempted recovery to the known stable Anchor baseline.

### Recovery Actions

- Master_Anchor carrier rollback:
  - Build: `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-a5-g2000-r1000-coop1-carrier`
  - LFRC assert: passed
  - Flashed with explicit B120 SNR `960148546`
  - Flash script result: `action=ok`
- Anchor OTA rollback:
  - Expected marker: `alt-bcast-a5-g2000-r1000-coop1`
  - Log directory: `logs/alt_bcast_a5_g2000_anchor_rollback_20260501_010210`
  - A-H individual OTA stages: all reported `reason=ota_success_observed`
  - No direct Anchor flash was used

### a5 Post-Verify Result

- Post-verify summary: `post_verify_all_responder_20260501_010836/summary.json`
- Result: `success=false`
- Error: `runtime_responder_ack_failed`
- Final role counts:
  - `matrix=0`
  - `responder=0`
  - `master=0`
  - `other=0`

### a5 No-Preflight UWB Capture

- Log directory: `logs/alt_bcast_a5_rollback_b39_quick_uwb_diag_skippreflight_30s_20260501_011138`
- Duration: 30s capture, 65s listener
- Anchor preflight: skipped
- Tags: still current `alt-bcast-b39-ltdma-8anc-pretx-g1200-r1000`

Results:

- `positions_all=0`
- `cm_all=0`
- `cs_all=0`
- `cr_all=0`
- `cf_all=0`
- Raw Tag log:
  - `RXG=125`
  - `TS;=0`
  - `CM;=0`
  - `CF;=0`
  - `CR;=0`
- Listener:
  - `uf_rows=1439`
  - `ul_rows=0`
  - UF code: all `0xe0`
  - UF dst: all `0xffff`
  - UF src: `0xb101`, `0xb102`, `0xb103`

### Verdict

Recovery is incomplete. A-H can still accept OTA successfully, and Tags are transmitting broadcast polls, but no Anchor responses are visible on air even after rolling back to a5.

This means the current blocker is not simply "a7 guard=1600 image bad". The system is now blocked at runtime responder activation / Master_Anchor control handoff / Anchor responder mode entry. Do not continue guard bisect until responder activation is restored and listener sees UL response frames again.

## 2026-05-01 01:20:41 CEST - Power Cycle Recovery Confirmed

After manual power cycle of Master_Anchor and all Anchors, responder activation recovered.

### Responder Verify

- Log directory: `logs/powercycle_anchor_responder_verify_20260501_011745`
- Result: `success=true`
- Runtime command:
  - `sent=8`
  - `ready=8/8`
  - `ack_ok=true`

### 30s UWB Confirmation Capture

- Log directory: `logs/powercycle_a5_b39_uwb_confirm_30s_20260501_011843`
- Anchors: recovered baseline `alt-bcast-a5-g2000-r1000-coop1`
- Tags: current `alt-bcast-b39-ltdma-8anc-pretx-g1200-r1000`
- Anchor preflight: passed `ready=8/8`

Results:

- `positions_all=221`
- Per-tag positions:
  - `BSF66F=73`
  - `BS2DCE=73`
  - `BSDC91=75`
- `tf_all=0`
- Listener:
  - `ul_rows=232`
  - `uf_rows=789`
  - UL code: `0xe1`
  - Anchor counts: `A=16`, `B=17`, `D=2`, `F=1`, `G=113`, `H=83`

### Verdict

RECOVERED. The failure was a runtime/control-link stuck state that required full power cycling. After power cycle, Master_Anchor can force all 8 anchors into responder mode and UWB responses/positions are back.

Stopped here intentionally. Do not continue guard bisect automatically until deciding whether to rebuild/retry a7/g1600 or first inspect why the previous runtime state got stuck.

## 2026-05-01 01:31:14 CEST - a7 Re-deployed After Baseline Recovery

After confirming power-cycle recovery on a5, re-deployed a7 using the revised process.

### Actions

- Master_Anchor carrier:
  - Build: `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-a7-g1600-r1000-coop1-carrier`
  - LFRC assert: passed
  - Flashed with explicit B120 SNR `960148546`
  - Flash script result: `action=ok`
- Anchor OTA:
  - Marker: `alt-bcast-a7-g1600-r1000-coop1`
  - Log directory: `logs/alt_bcast_a7_g1600_anchor_ota_after_powercycle_plan_20260501_012332`
  - A-H individual OTA stages: all reported `reason=ota_success_observed`
  - No direct Anchor flash was used

### Post-Verify

- Post-verify summary: `post_verify_all_responder_20260501_013016/summary.json`
- Result: `success=true`
- Runtime command:
  - `sent=8`
  - `ready=8/8`
  - `ack_ok=true`
- VERSION post:
  - A-H `actual=-`, `match=False`
  - This remains the known version readback/control-plane limitation and was not used as the deployment gate.

### Stop Point

Stopped here intentionally per revised overnight flow. Next required action is a manual power cycle of Master_Anchor and all Anchors, then responder verify and capture.

## 2026-05-01 b40 Tag OTA g1600

- Anchor precondition: A-H on `alt-bcast-a7-g1600-r1000-coop1`, post-power-cycle responder runtime `ready=8/8`.
- Tag marker: `alt-bcast-b40-8anc-pretx-g1600-r1000`.
- Master_Tag carrier: `build-master-control-b120-m1-master-tag-lfrc-alt-bcast-b40-8anc-pretx-g1600-r1000-carrier`, LFRC assert passed, flashed to SNR `1050070698`.
- Tag OTA directory: `logs/alt_bcast_b40_g1600_tag_ota_20260501_090924`.
- Post VERSION match:
  - BSF66F: `match=True`
  - BS2DCE: `match=True`
  - BSDC91: `match=True`
- Verdict: PASS. Proceeding to 60s probe with listener, skipping anchor preflight.

## 2026-05-01 b40 g1600 60s Probe

- Capture directory: `logs/alt_bcast_b40_g1600_probe60_skippreflight_20260501_091351`
- Anchor marker: `alt-bcast-a7-g1600-r1000-coop1`
- Tag marker: `alt-bcast-b40-8anc-pretx-g1600-r1000`
- Capture mode: 3 Tag motion, listener enabled, anchor preflight skipped, CM probe skipped.
- `positions_all`: 248
- `tf_all`: 0
- Per-tag positions:
  - BSF66F: 90
  - BS2DCE: 76
  - BSDC91: 82
- Rank0/A present: yes
  - Position rows containing A: 72
  - Anchor mentions: A=72 B=194 C=91 D=165 E=77 F=189 G=116 H=150
- RXG timing:
  - `slot_to_txdone_us`: n=169 min=335 median=366 p95=427 max=518
  - `txdone_to_rxstart_us`: n=169 min=640 median=793 p95=946 max=1007
- Listener:
  - UF rows: 647
  - UL rows: 138
  - UL code: `0xe1`
  - UL source letters seen by listener inference: E=1 F=6 G=56 H=75
- Verdict: FAIL probe threshold. Rank0 is not 100% missing, but `positions_all=248` is below the required `>500` for 60s. Stop here; do not run 120s full capture and do not bisect down to g1400.

## 2026-05-01 b43 g1600 Window-Fix Probe

- Code change: collector window was adjusted from RX-ready time by subtracting measured `txdone_to_rxready`; hidden `+500us` loop slack removed.
- Anchor marker: `alt-bcast-a7-g1600-r1000-coop1`
- Tag marker: `alt-bcast-b43-winfix-g1600-r1000`
- Tag OTA directory: `logs/alt_bcast_b43_winfix_g1600_tag_ota_20260501_092825`
- Post VERSION match: BSF66F/BS2DCE/BSDC91 all `match=True`.
- Capture directory: `logs/alt_bcast_b43_winfix_g1600_probe60_skippreflight_20260501_093245`
- `positions_all`: 403, improved from b40's 248 but still below the `>500` 60s threshold.
- `tf_all`: 0
- Per-tag positions: BSF66F=115, BS2DCE=143, BSDC91=145
- Rank0/A present: no in position output
  - Position anchor mentions: B=349 C=179 D=297 E=112 F=308 G=193 H=254
- RXG timing:
  - `win_us`: n=179 min=7619 median=7985 p95=8138 max=8138
  - `slot_to_txdone_us`: n=179 min=366 median=366 p95=427 max=488
  - `txdone_to_rxstart_us`: n=179 min=640 median=762 p95=915 max=1159
  - `txdone_to_rxend_us`: n=179 min=762 median=915 p95=1068 max=1281
- Listener: UF=930, UL=219; inferred UL letters B=18 C=12 E=1 F=2 G=46 H=140.
- Verdict: PARTIAL. Window shortening reduced slot pressure and improved position rate, but it likely became too aggressive for early/rank0 capture. Next b44 keeps the hidden `+500us` removal but does not subtract the measured RX-enable gap from the collector duration.

## 2026-05-01 b44 g1600 No-Slack Probe

- Code change relative to b40: removed the hidden `+500us` host-loop slack from the broadcast collector duration. Unlike b43, b44 does not subtract measured `txdone_to_rxready` from the collector window.
- Anchor marker: `alt-bcast-a7-g1600-r1000-coop1`
- Tag marker: `alt-bcast-b44-noslack-g1600-r1000`
- Tag OTA directory: `logs/alt_bcast_b44_noslack_g1600_tag_ota_20260501_093709`
- Post VERSION match: BSF66F/BS2DCE/BSDC91 all `match=True`.
- Capture directory: `logs/alt_bcast_b44_noslack_g1600_probe60_skippreflight_20260501_094129`
- `positions_all`: 403
- `tf_all`: 0
- Per-tag positions: BSF66F=118, BS2DCE=144, BSDC91=141
- Rank0/A present: no in position output
  - Position anchor mentions: B=346 C=163 D=309 E=111 F=320 G=168 H=261
- RXG timing:
  - `win_us`: n=176 min=8900 median=8900 p95=8900 max=8900
  - `slot_to_txdone_us`: n=176 min=366 median=366 p95=427 max=732
  - `txdone_to_rxstart_us`: n=176 min=640 median=777.5 p95=946 max=1159
  - `txdone_to_rxend_us`: n=176 min=762 median=915 p95=1068 max=1312
- Listener: UF=907, UL=166; inferred UL letters F=3 G=51 H=112.
- Verdict: PARTIAL/FAIL threshold. b44 matches b43's position count but still does not meet the `>500` 60s probe threshold and A/rank0 remains absent in position output. The hidden `+500us` removal helps compared with b40, but the remaining blocker is not just collector duration; next investigation should focus on why A/rank0 is absent and why the output caps around 6.7 Hz total instead of reaching 3-tag 10Hz class.

## 2026-05-01 b45 g1600 Full-Budget Probe

- Purpose: isolate whether A/rank0 disappearance in b43/b44 was caused by the new light-TDMA estimated-window admission/budget logic.
- Code change relative to b44: removed the estimated `window - 800us` use from TDMA `can_start` and sweep budget. Actual collector window stayed b44-style no-slack (`win=8900us`).
- Tag marker: `alt-bcast-b45-fullbudget-g1600-r1000`
- Tag OTA directory: `logs/alt_bcast_b45_fullbudget_g1600_tag_ota_20260501_095137`
- OTA result: BSF66F/BS2DCE/BSDC91 all `match=True`.
- Capture directory: `logs/alt_bcast_b45_fullbudget_g1600_probe60_skippreflight_20260501_095616`
- `positions_all`: 415
- `tf_all`: 0
- Per-tag positions: BSF66F=131, BS2DCE=142, BSDC91=142
- Anchor counts in position anchors field: A=0, B=284, C=278, D=314, E=135, F=300, G=245, H=237
- RXG: `win=8900`, `slot_to_txdone_us` median=335 p95=427 max=671, `txdone_to_rxstart_us` median=793 p95=915 max=1190, `txdone_to_rxend_us` median=915 p95=1068 max=1312
- Listener UL rows: 192; src counts: 0xa107=119, 0xa106=49, 0xa101=23, 0xa103=1
- Verdict: PARTIAL/FAIL. Full-budget admission did not bring A/rank0 back. This rules out the estimated TDMA budget as the primary A-loss cause. Next clean diagnostic is to redeploy the already-built b40 image and rerun the same 60s capture: if A returns, the regression is in b43/b44 source changes; if A stays absent, hardware/environment/Anchor-A state changed since the original b40 run.

## 2026-05-01 b40 Redeploy / Anchor-A State Check

- Purpose: distinguish b43/b44/b45 source regression from hardware/runtime state change for A/rank0 missing.
- Action: redeployed existing b40 image; no b40 rebuild.
- Master_Tag carrier: existing `build-master-control-b120-m1-master-tag-lfrc-alt-bcast-b40-8anc-pretx-g1600-r1000-carrier`, LFRC assert passed, flashed to SNR `1050070698`.
- Tag marker: `alt-bcast-b40-8anc-pretx-g1600-r1000`
- Tag OTA directory: `logs/alt_bcast_b40_redeploy_tag_ota_20260501_095837`
- OTA result: BSF66F/BS2DCE/BSDC91 all `match=True`.

### b40 redeploy 60s, before explicit Master_Anchor responder verify

- Capture: `logs/alt_bcast_b40_redeploy_probe60_skippreflight_20260501_100256`
- `positions_all`: 414
- Per-tag positions: BSF66F=131, BS2DCE=142, BSDC91=141
- Anchor counts: A=0, B=311, C=253, D=272, E=131, F=317, G=279, H=214
- Verdict: b40 itself now also has A=0. This rules out b43/b44/b45 source changes as the sole cause.

### b40 redeploy 30s with A-H serial logger, before explicit verify

- Capture: `logs/alt_bcast_b40_redeploy_anchorserial_diag30_20260501_100506`
- `positions_all`: 207
- Anchor counts: A=0, B=163, C=115, D=140, E=75, F=141, G=137, H=101
- A-H serial opened successfully but current anchor build emitted no useful low-rate responder serial diag during this run.

### Master_Anchor responder verify / force

- Verify: `logs/anchor_ready_check_after_A_missing_20260501_100658_20260501_100659/verify.log`
- Result: success, `sent_count=8`, `ready_count=8/8`.

### b40 after explicit Master_Anchor responder verify

- Capture: `logs/alt_bcast_b40_after_anchor_verify_probe30_20260501_100813`
- `positions_all`: 130
- Per-tag positions: BSF66F=43, BS2DCE=46, BSDC91=41
- Anchor counts: A=36, B=92, C=84, D=67, E=51, F=90, G=103, H=44
- Verdict: A/rank0 returns after explicit Master_Anchor responder verify/force. The A=0 issue is runtime responder state / mode handoff related, not a pure Tag collector window regression. Future captures should not skip Anchor runtime preflight when judging A/rank0 behavior, or should explicitly run responder verify/force immediately before capture.

## 2026-05-01 b44 Retest With Mandatory Anchor Responder Force

- Purpose: validate the new rule that every capture must explicitly force/verify Anchor responder runtime before judging rank0/A behavior.
- Anchor marker: `alt-bcast-a7-g1600-r1000-coop1`
- Tag marker: `alt-bcast-b44-noslack-g1600-r1000`
- Tag OTA directory: `logs/alt_bcast_b44_retest_tag_ota_20260501_101602`
- OTA result: BSF66F/BS2DCE/BSDC91 all `match=True`.
- Manual/explicit Master_Anchor force before capture: `logs/anchor_ready_force_before_b44_retest_20260501_102018/verify.log`, success `ready=8/8`.
- Capture directory: `logs/alt_bcast_b44_forceanchor_probe60_20260501_102130`
- Capture preflight: enabled, `anchor_responder_preflight_launch1_20260501_102131/verify.log`, success `ready=8/8`.
- `positions_all`: 253
- `tf_all`: 0
- Per-tag positions: BSF66F=85, BS2DCE=80, BSDC91=88
- Anchor counts in position anchors field: A=60, B=209, C=132, D=138, E=97, F=186, G=187, H=89
- A/rank0 present: yes
- RXG timing:
  - `win_us`: n=164 min=8900 median=8900 p95=8900 max=8900
  - `slot_to_txdone_us`: median=366 p95=427 max=3265
  - `txdone_to_rxstart_us`: median=793 p95=854 max=1037
  - `txdone_to_rxend_us`: median=915 p95=1037 max=1159
- Listener: UF=892, UL=178; inferred UL source counts mostly H/G plus small B/D, so listener remains incomplete for proving all anchors.
- Verdict: The forced-responder process fixes the A/rank0 absence. However, b44 no-slack does not improve throughput under a valid responder state: `positions_all=253/60s`, comparable to the original b40 class and below the `>500` probe target. The previous b44 result (`403` positions with `A=0`) was not a valid success case because the Anchor responder state was stale/incomplete. Future guard/window conclusions must be made only after explicit Anchor responder force or capture preflight success.

## 2026-05-01 Step 1 Anchor a9 g1200 Deploy

- Guard tested: g1200/r1000
- Anchor marker: `alt-bcast-a9-g1200-r1000-coop1`
- Anchor build: `build-anchor-unified-ota-alt-bcast-a9-g1200-r1000-coop1`
- Master_Anchor carrier: `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-a9-g1200-r1000-coop1-carrier`
- B120 LFRC assert: passed
- Master_Anchor flash: SNR `960148546`, repo J-Link script, passed
- Anchor OTA directory: `logs/alt_bcast_a9_g1200_anchor_ota_20260501_105324`
- OTA result: A-H upload/confirm success on attempt 1
- Runtime responder verify: `post_verify_all_responder_20260501_110022/verify.log`, success `ready=8/8`, sent=8
- VERSION readback: `actual=-` for A-H, `match=False`; accepted as known control-plane readback issue because runtime responder verify passed.
- Verdict: PASS for deployment/runtime gate. Continue to Tag b46 g1200.

## 2026-05-01 Step 2 Tag b46 g1200 Deploy

- Guard tested: g1200/r1000
- Tag marker: `alt-bcast-b46-8anc-pretx-g1200-r1000`
- Tag build: `build-alt-bcast-b46-8anc-pretx-tag-g1200-r1000-raw0-cont0`
- Master_Tag carrier: `build-master-control-b120-m1-master-tag-lfrc-alt-bcast-b46-8anc-pretx-g1200-r1000-carrier`
- B120 LFRC assert: passed
- Master_Tag flash: SNR `1050070698`, repo J-Link script, passed
- Tag OTA directory: `logs/alt_bcast_b46_g1200_tag_ota_20260501_110328`
- OTA result: BSF66F/BS2DCE/BSDC91 upload success on attempt 1
- VERSION match: BSF66F/BS2DCE/BSDC91 all `match=True`
- Verdict: PASS. Continue to g1200 60s probe with mandatory Anchor responder force/preflight.

## 2026-05-01 Step 3 b46/a9 g1200 60s Probe

- Guard tested: g1200/r1000
- Anchor marker: `alt-bcast-a9-g1200-r1000-coop1`
- Tag marker: `alt-bcast-b46-8anc-pretx-g1200-r1000`
- Mandatory Anchor force before capture: `logs/anchor_ready_force_before_b46_probe_20260501_110805/verify.log`, success `ready=8/8`
- Capture directory: `logs/alt_bcast_b46_g1200_probe60_20260501_110849`
- Capture preflight: enabled, `recv_20260501_110904/anchor_responder_preflight_launch1_20260501_110904/verify.log`, success `ready=8/8`
- Listener: enabled, but `listener-extra-s=12` was partly consumed by preflight; use a larger listener-extra for future preflight captures.
- Anchor serial: attempted on all non-listener 760* ports under `anchor_serial/`; current port set includes some non-anchor/tag serial output, so serial mapping still needs cleanup.
- `positions_all`: 410
- `tf_all`: 0
- Per-tag positions: BSF66F=134, BS2DCE=135, BSDC91=141
- Per-tag rate over 60s: BSF66F=2.23 Hz, BS2DCE=2.25 Hz, BSDC91=2.35 Hz, total=6.83 Hz
- Rank0/A present: no, A count=0
- Anchor counts in position anchors field: B=282, C=296, D=267, E=149, F=286, G=278, H=221
- RXG timing:
  - `win_us`: n=172 min=8500 median=8500 p95=8500 max=8500
  - `slot_to_txdone_us`: n=172 min=335 median=335 p95=396 max=518
  - `txdone_to_rxstart_us`: n=172 min=640 median=793 p95=946 max=1037
  - `txdone_to_rxend_us`: n=172 min=732 median=915 p95=1098 max=1159
- Listener UL rows: 97; source counts mostly H (`0xa107`=88), with sparse D/C/G/E.
- RMS stats:
  - BSF66F: median=27.5 mm, p95=65 mm, p99=75 mm, max=82 mm
  - BS2DCE: median=13 mm, p95=52 mm, p99=65 mm, max=127 mm
  - BSDC91: median=15 mm, p95=58 mm, p99=170 mm, max=187 mm
- Verdict: FAIL per overnight rule. g1200 has rank0/A missing even with explicit responder force and capture preflight `ready=8/8`. Stop guard-bisect here; do not attempt g1000/g800. Current evidence says g1200 is too aggressive for rank0 under this a9/b46 configuration. The last guard where rank0 was observed under forced responder process was g1600, but g1600 throughput was still below the >500/60s target, so the next engineering work should separate rank0 timing from throughput rather than continuing this overnight bisect.

## Final Summary

Minimum observed guard with rank0 present under forced-responder process: g1600 from b44/a7 retest (`A=60`), but that run had only `positions_all=253/60s`.
Current g1200 retest: FAIL, `positions_all=410/60s`, `A=0` despite `ready=8/8` preflight.
Best current position count in this forced-preflight sequence: g1200 with 410/60s, but it is not valid for 8-anchor rank0 coverage because A is absent.
End condition reached: g1200 rank0 fail even with responder force. Stopped before g1000/g800 as instructed.

## 2026-05-01 b50 fastrx Tag Hot-Path Check

- Goal: reduce Tag `txdone_to_rxstart_us` by opening RX immediately after TXFRS and moving bookkeeping after the collector.
- Source touched: `src/ss_twr_init.c` only.
- Anchor image: unchanged from current `alt-bcast-a9-g1200-r1000-coop1`.
- Tag marker: `alt-bcast-b50-fastrx-8anc-pretx-g1600-r1000`.
- Tag OTA:
  - First b50 deploy: `logs/alt_bcast_b50_g1600_tag_ota_20260501_222000`, all three Tags `match=True`.
  - Second b50/fastrx2 deploy after moving TX timestamp read after RX enable: `logs/alt_bcast_b50_g1600_tag_ota_fastrx2_20260501_223029`, all three Tags `match=True`.
- Capture 1: `logs/alt_bcast_b50_fastrx_g1600_probe60_20260501_222512`
  - Anchor preflight: success `ready=8/8`
  - `positions_all`: 436
  - `tf_all`: 0
  - Per-tag: BSF66F=140, BS2DCE=146, BSDC91=150
  - Position anchor counts: B=280, C=293, D=319, E=149, F=285, G=313, H=230, A=0
  - RXG: `win=8565`, `slot_to_txdone_us` median=488 p95=549 max=793
  - RXG: `txdone_to_rxstart_us` median=244 p95=335 max=579
  - Listener UL: 198, mostly H=169; A=0
  - Verdict: partial timing improvement, but not below target.
- Capture 2: `logs/alt_bcast_b50_fastrx2_g1600_probe60_20260501_223452`
  - Anchor preflight: success `ready=8/8`
  - `positions_all`: 434
  - `tf_all`: 0
  - Per-tag: BSF66F=140, BS2DCE=147, BSDC91=147
  - Position anchor counts: B=288, C=312, D=300, E=136, F=301, G=292, H=253, A=0
  - RXG: `win=8565`, `slot_to_txdone_us` median=610 p95=701 max=976
  - RXG: `txdone_to_rxstart_us` median=61 p95=91 max=122
  - RXG: `txdone_to_rxend_us` median=183 p95=213 max=274
  - Listener UL: 158, mostly H=156; A=0
  - Verdict: PASS for Tag RX hot-path timing. The target `txdone_to_rxstart_us < 200us` is met with margin.
- Important conclusion: b50 proves the Tag collector now opens RX fast enough. Rank0/A is still absent in both Tag positions and listener UL even when Anchor preflight reports `ready=8/8`, so the remaining blocker is no longer Tag `txdone_to_rxstart`. Next work should debug Anchor A/rank0 response visibility/state or Anchor-side guard/slot behavior, not keep tuning Tag hot path blindly.

## 2026-05-01 b51 nosleep/DFU-trigger checkpoint

Status: code + build only. No hardware deployment was performed after this checkpoint.

Changes:
- Anchor responder hot loop no longer calls `k_yield()` or coop sleep in normal ranging path.
- Added Anchor BLE control command `DFU` / `ENTER_DFU` / `OTA` / `ENTER_OTA`.
- Added runtime DFU request flag so responder exits, stops UWB, publishes `OK DFU_READY`, then leaves CPU to BLE/SMP until OTA reset.
- Updated `ota_single_shot_stable.py` to send best-effort `cmd DFU` before `mode ota`. Older coop images may return `ERR:BAD_CMD`; this is accepted for the first upgrade into nosleep.

Builds:
- Anchor marker: `alt-bcast-a10-nosleep-g1200-r1000`
- Anchor OTA build: `build-anchor-unified-ota-alt-bcast-a10-nosleep-g1200-r1000/dfu_application.zip`
- Master_Anchor B120 carrier: `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-a10-nosleep-g1200-r1000-carrier/zephyr/merged_domains.hex`
- B120 LFRC assert: passed.

Hardware:
- Not flashed.
- No Anchor OTA started.
- Tags unchanged on current b50.

Next hardware step, when explicitly approved:
1. Flash only Master_Anchor B120 SNR `960148546` with the b51 carrier.
2. OTA A-H using existing `ota_deploy_anchor_set.py` flow.
3. Power cycle Anchors if needed, force responder verify `ready=8/8`.
4. Run 60s 3-tag capture with anchor preflight and check rank0/A.

## 2026-05-01 a10 Anchor-A staged deployment

- Master_Anchor B120 flashed with `alt-bcast-a10-nosleep-g1200-r1000-carrier`; LFRC assert passed.
- Anchor OTA was intentionally limited to Anchor A only.
- Anchor A OTA result: upload/reset observed in `logs/alt_bcast_a10_nosleep_anchorA_ota_20260501_233232`.
- Anchor A version by UUID reported `ANCHOR_FW fw=alt-bcast-a10-nosleep-g1200-r10... label=A`, confirming the a10-nosleep image booted on A. The marker is truncated by the control field, but the prefix is correct.
- Forced responder verify after A deployment: `ready=8/8` in `logs/anchor_ready_force_after_a10_anchorA_20260501_233538`.
- 30s BSF66F-only probe: `logs/alt_bcast_a10_nosleep_anchorA_BSF66F_probe30_20260501_234059`.
  - `positions_all=70`, `tf_all=0`.
  - Position anchor letters: B=33, C=49, D=64, E=16, F=44, G=58, H=34, A=0.
  - RXG: `mask=0xff`, `pc=8`, `guard=1600`, `win=8565`.
  - RXG timing: `slot_to_txdone_us` median=579 p95=610 max=762; `txdone_to_rxstart_us` median=61 p95=91 max=122.
  - Listener UL: 20 rows, all H (`anchor_id=7`, `src=0xa107`).
- Verdict: a10-nosleep booted and system still ranges with non-A anchors, but this probe did not prove Anchor A/rank0 response recovery. Next staged step is a10 -> a11 marker-only OTA on Anchor A to prove the new BLE-triggered DFU path before touching B-H.

## 2026-05-01 Anchor-A a11 OTA / ReOTA proof

- Built marker-only Anchor image `alt-bcast-a11-nosleep-g1200-r1000` from the same nosleep/DFU-trigger code and g1200/r1000 parameters.
- Built Master_Anchor carrier with the a11 payload and LFRC assert passed.
- Fixed Master_Anchor runtime command routing for UUID-selected anchors: when `runtime_target_uuid` is set and matches a peer UUID, that peer now matches immediately instead of also requiring the default `prefix=BS` filter. Without this, `cmd DFU` was incorrectly skipped as target mismatch for Anchor control links.
- Updated `ota_single_shot_stable.py` so nosleep Anchor OTA waits for the selected Anchor control UUID to be ready before sending `cmd DFU`.
- First successful a10 -> a11 OTA for Anchor A:
  - Log: `logs/alt_bcast_a11_nosleep_anchorA_ota_fixuuid_20260501_235200`.
  - Evidence: `Anchor ctrl sent[0]: DFU uuid=4DC6B8187E33803AE8601FB0D7992B96`, `OK DFU_REQUESTED`, `OTA upload complete`.
  - Summary: `returncode=0`, `ota_success_seen=true`, `ota_upload_complete_seen=true`.
  - Post VERSION readback still reported `actual=- match=False`; this is the known Master_Anchor VERSION control-plane readback problem, not an upload failure.
- ReOTA proof using the already-running a11 image on Anchor A:
  - B120 Master_Anchor was reset only, not flashed, after CDC writes timed out.
  - Log: `logs/alt_bcast_a11_nosleep_anchorA_reota_a11_after_b120reset_20260501_235813`.
  - Result: upload reached 100%, summary `ota_success_seen=true`, `ota_upload_complete_seen=true`, `reason=ota_success_observed`.
  - This proves the a11 nosleep image can itself enter DFU via BLE trigger and accept a second OTA.
- Scope: only Anchor A was OTA-updated/reOTA-tested. B-H were not touched.

## 2026-05-02 B-H a11 nosleep deployment

- Active Anchor OTA payload guard passed for `alt-bcast-a11-nosleep-g1200-r1000`.
- Master_Anchor carrier in use: `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-a11-nosleep-g1200-r1000-carrier-fixuuid`; LFRC assert passed before flash.
- OTA directory: `logs/alt_bcast_a11_nosleep_anchor_BH_ota_20260502_000148`.
- Scope: B-H only. Anchor A was already proven on a11 by the staged OTA/ReOTA test above.
- Per-anchor OTA result:
  - B: `returncode=0`, `dfu_ready_seen=true`, `ota_upload_complete_seen=true`, `ota_success_seen=true`, `reason=ota_success_observed`.
  - C: `returncode=0`, `dfu_ready_seen=true`, `ota_upload_complete_seen=true`, `ota_success_seen=true`, `reason=ota_success_observed`.
  - D: `returncode=0`, `dfu_ready_seen=true`, `ota_upload_complete_seen=true`, `ota_success_seen=true`, `reason=ota_success_observed`.
  - E: `returncode=0`, `dfu_ready_seen=true`, `ota_upload_complete_seen=true`, `ota_success_seen=true`, `reason=ota_success_observed`.
  - F: `returncode=0`, `dfu_ready_seen=true`, `ota_upload_complete_seen=true`, `ota_success_seen=true`, `reason=ota_success_observed`.
  - G: `returncode=0`, `dfu_ready_seen=true`, `ota_upload_complete_seen=true`, `ota_success_seen=true`, `reason=ota_success_observed`.
  - H: `returncode=0`, `dfu_ready_seen=true`, `ota_upload_complete_seen=true`, `ota_success_seen=true`, `reason=ota_success_observed`.
- Post VERSION readback remains unreliable (`actual=-` / CDC disconnect behavior), but all B-H OTA upload/reset success signals were observed.
- Intended Anchor fleet state: A-H on `alt-bcast-a11-nosleep-g1200-r1000`.
- Next required step: manual full hardware power cycle, then force responder verify `ready=8/8`, then 60s g1200 nosleep capture.

## 2026-05-02 a11 nosleep fleet power-cycle + 60s probe

- Manual power cycle completed by developer.
- Post-power-cycle responder force/verify:
  - Log: `logs/anchor_ready_force_after_a11_all_powercycle_20260502_001934/verify.log`.
  - Result: `success=true`, `sent=8`, `ready=8/8`.
- 60s 3-tag motion probe:
  - Directory: `logs/alt_bcast_a11_nosleep_g1200_b50_probe60_20260502_002054`.
  - Capture preflight also passed: `ready=8/8`.
  - Tag firmware used: current b50-style fastrx image, reporting `guard=1600`, `mask=0xff`, `pc=8`. Anchor fleet intended state is a11 nosleep g1200.
  - `positions_all=427`, `tf_all=0`.
  - Per-tag positions: BSF66F=124, BS2DCE=151, BSDC91=152.
  - Position anchor counts: B=304, C=275, D=290, E=117, F=274, G=296, H=231, A=0.
  - Top tracks: CDGH=108, BDFH=84, BDFG=47, BCFG=41, BCEG=35.
  - RXG timing: `slot_to_txdone_us` median=579 p95=701 max=762; `txdone_to_rxstart_us` median=61 p95=91 max=183.
  - RXG config observed: `win=8565`, `guard=1600`, `pc=8`, `mask=0xff`.
  - Listener: `uf_rows=701`, `ul_rows=143`; UL anchors H=122, G=18, B=2, E=1, A=0.
- Verdict:
  - Anchor control/runtime state is healthy (`ready=8/8` twice).
  - Tag RX hot path is healthy (`txdone_to_rxstart_us` p95 < 100 us).
  - A/rank0 is still absent from both Tag positions and passive listener UL.
  - This probe does not prove g1200 nosleep success. The remaining blocker is Anchor A/rank0 response transmission/slot timing, not Tag-side RX startup.

## 2026-05-02 Anchor-A a12 g1200 nosleepdiag proof

- Built diagnostic Anchor image `alt-bcast-a12-nosleepdiag-g1200-r1000`.
  - Same g1200/r1000 nosleep behavior as a11.
  - Enabled low-rate responder/profile diag only:
    `APP_ANCHOR_RESPONDER_PRINTK_ENABLE=1`,
    `APP_ANCHOR_RESPONDER_PROFILE_ENABLE=1`,
    `APP_ANCHOR_RESPONDER_DIAG_PERIOD_MS=5000`.
  - No per-frame printk enabled.
- Built and flashed Master_Anchor B120 carrier:
  `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-a12-nosleepdiag-g1200-r1000-carrier`.
  - B120 LFRC assert passed.
  - Flash used explicit SNR `960148546`.
- OTA scope: Anchor A only.
  - Directory: `logs/alt_bcast_a12_nosleepdiag_anchorA_ota_20260502_003131`.
  - Upload reached 100%, `dfu_ready_seen=true`, `ota_upload_complete_seen=true`, `ota_success_seen=true`.
  - Post VERSION still `actual=-`; ignored as known readback issue.
- Post-OTA responder verify:
  - Directory: `logs/anchor_ready_force_after_a12diag_anchorA_20260502_003224`.
  - Result: `ready=8/8`.
- 30s diagnostic capture:
  - Directory: `logs/alt_bcast_a12diag_anchorA_g1200_b50_diag30_20260502_003319`.
  - `positions_all=219`, `tf_all=0`.
  - Position anchor counts: B=150, C=148, D=154, E=61, F=149, G=148, H=123, A=0.
  - Listener UL anchors: H=85, B=14, G=7, F=5, C=1, E=1, A=0.
  - Tag RXG still reports current b50 Tag config: `guard=1600`, `win=8565`, `mask=0xff`, `pc=8`, `txdone_to_rxstart_us` median=61 p95=91 max=122.
- Anchor A serial diag is decisive:
  - Anchor A matches the broadcast poll: `matched_broadcast=494`, `last_dst=0xffff`, `last_mask=0xff`, `last_resp_rank=0`.
  - Anchor A schedules rank0 delay: `last_resp_delay_uus=1200`.
  - Anchor A never transmits successfully: `tx_ok=0`, `tx_miss=494`, `starttx_ok=0`.
  - Per-tag counts: `tag_poll=0,173,165,156,0,0,0,0`; `tag_ok=0,0,0,0,0,0,0,0`; `tag_tx_miss=0,173,165,156,0,0,0,0`.
  - Profile shows Anchor A delayed-TX start path is too slow for g1200:
    examples include `avg_us ... txprog=1007 start=1595`, `max_us ... start=1831`.
- Verdict:
  - The previous A/rank0 absence is not a mask/match problem and not a Tag RX problem.
  - Anchor A receives and matches broadcast polls correctly, but `DWT_START_TX_DELAYED` is called too late for `guard=1200`.
  - Current Anchor responder hot path requires roughly 1.6-1.8 ms before delayed TX start; g1200 is below the measured implementation limit.
  - Immediate stable path is guard >= ~1800-2000, or optimize the Anchor TX hot path before retrying g1200/g800.

## 2026-05-02 a13 Anchor hot-path deployment

- Implemented Anchor responder hot-path optimization in `src/ss_twr_resp.c` only.
  - Pre-built the response frame template at responder init.
  - Removed pre-`starttx(DELAYED)` `forcetrxoff()` and broad status clear from the matched-poll path.
  - Moved stale status cleanup and diagnostic status reads until after `dwt_starttx(DWT_START_TX_DELAYED)` is issued.
  - Gated the expensive slack read behind profile/frame-diag builds.
  - Left Tag firmware, BLE DFU trigger, OTA/SMP/MCUboot, production workspace, and `unicast/` untouched.
- Built Anchor image:
  - Marker: `alt-bcast-a13-nosleep-hotpath-g1200-r1000`.
  - Build dir: `build-anchor-unified-ota-alt-bcast-a13-nosleep-hotpath-g1200-r1000`.
  - Build parameters: g1200/r1000, frame filter on, coop sleep 0, responder/profile printk disabled.
- Built and flashed Master_Anchor B120 carrier:
  - Build dir: `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-a13-nosleep-hotpath-g1200-r1000-carrier`.
  - LFRC assert passed.
  - Flash used explicit SNR `960148546`.
- Staged Anchor A OTA:
  - Directory: `logs/alt_bcast_a13_hotpath_anchorA_ota_20260502_004853`.
  - Upload reached 100%, `dfu_ready_seen=true`, `ota_upload_complete_seen=true`, `ota_success_seen=true`.
  - Post VERSION remains `actual=-`; treated as known Anchor version readback issue.
- Post-A responder verify:
  - Directory: `logs/anchor_ready_force_after_a13_anchorA_20260502_004958`.
  - Result: `ready=8/8`.
- 30s BSF66F-only staged probe:
  - Directory: `logs/alt_bcast_a13_hotpath_anchorA_BSF66F_probe30_20260502_005206`.
  - Anchor A result changed from a12 `tx_ok=0 / tx_miss=494` to Tag-side `A ok=108 timeout=2`.
  - E was still mostly timeout at this point (`E timeout=111`) because only A had a13 and E is rank0 for the E/F/G/H group.
  - Verdict: a13 hot path fixed the rank0 transmit miss for Anchor A, so staged rollout to B-H was justified.
- B-H OTA to a13:
  - Directory: `logs/alt_bcast_a13_hotpath_anchor_BH_ota_20260502_005522`.
  - B-H all reported `ota_success_observed` with upload/reset success signals.
  - Script rc remained 3 only because post VERSION readback is `actual=-`.
- Post-fleet responder verify:
  - Directory: `logs/anchor_ready_force_after_a13_all_20260502_010048`.
  - Result: `ready=8/8`.
- 60s 3-tag calibration-style probe:
  - Directory: `logs/alt_bcast_a13_hotpath_all_3tag_probe60_20260502_010247`.
  - Profiles: BSF66F static, BS2DCE roto, BSDC91 roto.
  - `positions_all=0`, `tf_all=0`; this run produced CM/CF calibration logs rather than TS position rows.
  - UWB ranging result is strong:
    - `cm_all=3344`, `ok=3282`, `timeout=62` (~98.1% ok).
    - Per-tag ok/timeout: BSF66F 1338/26, BS2DCE 880/16, BSDC91 1064/20.
    - Per-anchor ok/timeout:
      A 577/4, B 506/11, C 486/12, D 384/13,
      E 360/2, F 471/12, G 249/3, H 249/5.
    - `cf_all=981`, `solve_reason`: success=633, pending=348.
    - `first_to_last_us=0` for all CF rows, `poll_count=4`.
  - Listener in this run saw limited parsed UL (`A=1`, `C=1`, `E=1`, `H=61`), but Tag-side CM/CF proves all anchors are responding and being ranged.
- An attempted all-motion 60s run was invalid for this capture script because startup CM probe expects CM rows; it failed at startup with `ok=0/8` in motion profile before collecting data.
- Current verdict:
  - The Anchor hot-path issue is solved at g1200 for the deployed a13 image.
  - The remaining issue is not UWB response visibility; it is selecting/running the correct host/firmware mode that emits TS/position rows rather than only calibration CM/CF rows.
