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
