# RF Diagnostics Overnight Plan - 2026-06-25

This plan starts from the frozen rollback set:

```text
firmware_freeze/autopos_full_system_20260625_0245
```

The freeze remains the rollback authority. Tag and Anchor bodies are still
OTA-only. Do not direct-flash deployed Tag or Anchor devices.

## Hard Operating Priority

For live positioning and positioning-quality captures, the priority order is:

1. Preserve 7/8 and 8/8 anchor availability.
2. Allow RF diagnostics only through paths that do not reduce ranging success.

This is now a hard rule, not a preference. Monte Carlo/layout sensitivity work
showed that losing two anchors causes a severe positioning-accuracy drop, so RF
diagnostics must never own the hot path at the cost of anchor coverage.

Allowed live diagnostics:

- Anchor-side poll diagnostics in the response payload, implemented with the
  A26 delayed/cached rank0 pattern.
- Compact `TR;3;...;D1,<base64>` records that carry Anchor-side diagnostics
  inside the normal range row.
- Passive listener diagnostics, only when the listener is fully out-of-band and
  does not affect the Tag/Anchor sweep.

Forbidden in normal 10 Hz full-sweep ranging:

- Legacy per-anchor Tag `RFD` rows.
- Tag-side response `dwt_readdiagnostics()` in the 1 ms response hot path.
- Any diagnostic output mode that lowers 7/8 or 8/8 anchor availability.

## Scope

Add per-link RF diagnostics without changing the solver geometry first:

- Anchor response payload v2 reports lightweight poll-path diagnostics.
- Tag parses response v1/v2 and forwards safe diagnostics in the normal `TR`
  row. The legacy per-anchor `RFD` rows are not safe for normal ranging and must
  stay disabled in live positioning captures.
- A generic co-located listener firmware records poll diagnostics, optionally
  poll CIR, and never participates in ranging.
- Host capture tools align range rows, Anchor poll diagnostics, Tag response
  diagnostics, and listener poll diagnostics for replay and later solver use.

The old listener hardware is out of scope:

```text
Legacy listener SNR: 760185886
Legacy app path: SS-TWR/alt-SS-TWR/broadcast/UWB_listener_old/
Rule: do not flash it, do not open its serial port, do not use it for this run.
```

## New Firmware Builds

All new builds keep `guard=1200`, `response_spacing=1000`, and tail compression
disabled.

| Role | Build / image | Marker / role | Check |
|---|---|---|---|
| Tag app | `SS-TWR/alt-SS-TWR/broadcast/build-tag-ble-unified-rfdiag-v2-g1200-r1000-20260625/dfu_application.zip` | `tag-rfdiag-v2-g1200-r1000` | OTA-capable; RAM `63800 / 65536` |
| Anchor app | `SS-TWR/alt-SS-TWR/broadcast/build-anchor-unified-ota-rfdiag-v2-g1200-r1000-20260625/dfu_application.zip` | `alt-bcast-a19-rfdiag-v2-g1200-r1000` | Anchor response payload v2 enabled |
| Master_Tag B120 | `SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-tag-lfrc-rfdiag-v2-g1200-r1000-20260625/zephyr/merged_domains.hex` | embeds Tag OTA payload | LFRC verified |
| Master_Anchor B120 | `SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-anchor-lfrc-rfdiag-v2-g1200-r1000-20260625/zephyr/merged_domains.hex` | embeds Anchor OTA payload | LFRC verified |
| Generic listener | `SS-TWR/alt-SS-TWR/broadcast/build-uwb-listener-poll-diag-generic-20260625/merged.hex` | generic RX-only poll diagnostics | no listener-specific hardcode |

Artifact hashes:

```text
0b5ce49de44346f37d63da68a0dd1aa7fb12d5efa69a817b06d2259af4cecab3  build-tag-ble-unified-rfdiag-v2-g1200-r1000-20260625/dfu_application.zip
ab5d28b264e1d31d976fa32bd53f2811cb172968cb343bc06b84a84946410a95  build-anchor-unified-ota-rfdiag-v2-g1200-r1000-20260625/dfu_application.zip
d2675c68bf367d37196eaa2eb6b75563c6cbf843bb9ae52b5fd820237950fb91  build-master-control-b120-m1-master-tag-lfrc-rfdiag-v2-g1200-r1000-20260625/zephyr/merged_domains.hex
b9378f661ae68cdf443a4314a3a5edee526894283d017e26cf7ee6669e86d419  build-master-control-b120-m1-master-anchor-lfrc-rfdiag-v2-g1200-r1000-20260625/zephyr/merged_domains.hex
8e5e41be236aecc2ce580273dab2a118a568495bcde908c020b6c76b8c9aa19a  build-uwb-listener-poll-diag-generic-20260625/merged.hex
```

Current generated OTA payload state after the builds is Anchor:

```bash
cd SS-TWR/alt-SS-TWR/broadcast
python3 scripts/verify_ota_payload_kind.py --expected anchor
```

This only describes the current generated payload files. The already-built
Master_Tag image embeds the Tag OTA payload it was built with.

## Wire Formats

Anchor response v1 remains length 20:

```text
poll_rx_ts[4] at offset 10
resp_tx_ts[4] at offset 14
```

Anchor response v2 remains backward compatible by appending diagnostics after
the same timestamp offsets:

```text
offset 18: diag_version = 2
offset 19: diag_flags
offset 20: FP_INDEX
offset 22: FP_AMPL1
offset 24: FP_AMPL2
offset 26: FP_AMPL3
offset 28: CIR_PWR
offset 30: RXPACC
offset 32: STD_NOISE
length 34
```

Final live Tag output for the safe v4 path:

```text
TR;3;<sweep>;<plan>;<pmode>;<active_mask>;<valid_mask>;
<raw_csv>;<range_csv>;<quality_csv>;<status_codes>;D1,<base64 compact RF diag>
```

The compact `D1` trailer is optional best-effort data. If it would exceed the
status-line size budget, firmware drops the trailer and keeps the normal `TR`
range row intact.

Legacy Tag output, disabled for normal ranging:

```text
RFD;1;<sweep>;<poll_seq>;<anchor_id>;<raw_mm>;<resp_rx_ts>;<carrier_integrator>;
<anchor_poll_diag...>;<tag_resp_diag...>
```

The legacy `RFD` rows remain parseable for old logs, but emitting one row per
anchor per sweep damaged the 10 Hz response window and must not be enabled in
positioning-quality captures.

Listener output:

```text
LPD;1;<listener_id>;<near_anchor_id>;<now_ms>;<accepted_polls>;<seq>;<tag_id>;
<src>;<dst>;<rx_ts_lo32>;<carrier_integrator>;<fp_index>;<fp1>;<fp2>;<fp3>;
<cir_pwr>;<rxpacc>;<std_noise>;<frame_len>;<poll_mask>
```

## Deploy Order

1. Flash `Master_Tag` B120 only if Tag OTA is needed:

   ```text
   SNR 1050070698
   image SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-tag-lfrc-rfdiag-v2-g1200-r1000-20260625/zephyr/merged_domains.hex
   ```

2. OTA the Tags through `Master_Tag`. Verify marker:

   ```text
   tag-rfdiag-v2-g1200-r1000
   ```

3. Flash `Master_Anchor` B120 only if Anchor OTA is needed:

   ```text
   SNR 960148546
   image SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-anchor-lfrc-rfdiag-v2-g1200-r1000-20260625/zephyr/merged_domains.hex
   ```

4. OTA Anchors A-H through `Master_Anchor`. Verify marker:

   ```text
   alt-bcast-a19-rfdiag-v2-g1200-r1000
   ```

5. Flash only the new co-located listener hardware:

   ```text
   Listener E near Anchor E
   SNR 760184767
   USB /dev/serial/by-id/usb-SEGGER_J-Link_000760184767-if00
   image SS-TWR/alt-SS-TWR/broadcast/build-uwb-listener-poll-diag-generic-20260625/merged.hex
   ```

   Do not touch `760185886`.

## Capture

Use the combined capture wrapper so the listener log and normal Tag capture are
started and stopped together:

```bash
cd SS-TWR/alt-SS-TWR/broadcast
python3 scripts/run_recv_tdma_capture_with_poll_listener.py \
  --listener-port /dev/serial/by-id/usb-SEGGER_J-Link_000760184767-if00 \
  -- \
  <normal run_recv_tdma_capture.py arguments>
```

If a listener capture is already on disk, join it offline:

```bash
cd SS-TWR/alt-SS-TWR/broadcast
python3 scripts/join_range_diag_listener.py \
  --range-diag <tag_capture>/range_diag_joined.csv \
  --listener-lpd <listener_capture>/lpd.csv \
  --listener-anchor-id 4 \
  --out <tag_capture>/range_diag_listener_E_joined.csv
```

`run_recv_tdma_capture.py` now backfills missing TR/RFD `tag_id` values from the
validated TDMA config when only one known peer is scheduled. Future captures
should therefore not need `--default-tag-id` for listener joins. The earlier
04:00 capture was generated before that parser fix, so a repaired copy was
written as `range_diag_joined_tagid_backfilled.csv`.

## Go / No-Go Gates

The first overnight is a data-visibility run. Solver behavior should not be
changed until these gates pass:

| Gate | Pass condition |
|---|---|
| Tag `RFD` output | `tag_rf_diag.csv` exists and `rfd_joined_all / tr_valid_all >= 0.90` |
| Anchor v2 parsing | Anchor-side diag fields are populated for v2 frames; v1 fallback still parses |
| Listener poll capture | `lpd.csv` contains accepted poll rows with the expected sequence/tag IDs |
| Listener E join | Anchor E rows join to Listener E rows at `>= 0.80` for the same run |
| Proxy sanity | Listener E poll features and Anchor E poll diagnostics show strong monotonic agreement, target Spearman/Pearson `>= 0.8` on controlled body-shadow runs |

If the listener proxy gate fails, do not add listener terms to the solver. First
check physical spacing, mounting, antenna orientation, and whether `20 cm` is too
large for CIR proxy use in the actual room.

## Solver Use

The first solver integration should only alter per-link range uncertainty:

```text
sigma_i_eff = sigma_base * anchor_poll_factor_i * tag_resp_factor_i
```

Listener-derived factors are a second step and should only be enabled after the
proxy gates above pass. Listener E primarily explains the Anchor E link. It may
carry weak cross-anchor body-state information for nearby azimuths, but that is
analysis data, not a first-version solver assumption.

Do not add TDoA residual rows in this plan.

## Rank Rotation Diagnostic - 2026-06-25

`BSF66F` showed an unexpected pattern in the safe v4 no-listener capture:
Anchor A, not only tail Anchor H, had a low valid ratio. To distinguish a
physical Anchor A link problem from a first-responder slot timing problem, a
temporary Tag build forced `APP_TAG_ALT_BCAST_RANK_OFFSET_OVERRIDE=1`, making
Anchor B the first responder while keeping `guard=1200` and
`response_spacing=1000`.

Temporary build:

```text
build-tag-ble-unified-rfdiag-v4tr-rank1-g1200-r1000-20260625
marker tag-rfdiag-v4tr-rank1-g1200-r1000
```

Capture:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_120s_no_listener_anchor_v2_tag_v4tr_rank1_Brank0_20260625_180213_20260625_180213/
```

Result:

| Anchor | Valid |
|---|---:|
| A / 0 | `682/1092 = 0.625` |
| B / 1 | `322/1092 = 0.295` |
| C / 2 | `1068/1092 = 0.978` |
| D / 3 | `1068/1092 = 0.978` |
| E / 4 | `1068/1092 = 0.978` |
| F / 5 | `1068/1092 = 0.978` |
| G / 6 | `1068/1092 = 0.978` |
| H / 7 | `1067/1092 = 0.977` |

Interpretation: the worst link moved from Anchor A to Anchor B when B became
the first responder. H also recovered to normal. This strongly points to a
rank-0 / early Tag RX turn-around timing issue, not a fixed Anchor A or Anchor H
physical link failure. Do not raise the 1200 us guard as the first fix; inspect
Tag RX enable timing, DW1000 state transition latency, and the first response
receive window.

Restoration after the diagnostic:

```text
Master_Tag SNR 1050070698 restored to v4 rank0 carrier
BSF66F restored to marker tag-rfdiag-v4tr-g1200-r1000
active Tag OTA payload restored to tag-rfdiag-v4tr-g1200-r1000
```

## Morning Checklist

- Record which devices were flashed and which were only built.
- Record OTA success/failure and marker verification.
- Record capture paths.
- Record `summary.json` fields:
  - `success`
  - `tr_all`
  - `tr_valid_all`
  - `rfd_all`
  - `rfd_joined_all`
  - listener `accepted_polls`
- Produce joined CSV paths.
- State whether each gate passed, failed, or was not run.

## 60 Hz Baseline Re-check - 2026-06-26

The 6-Tag, 60 Hz system baseline must be restored before any RFD path is
accepted. The authoritative high-success historical reference is:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/six_tag_stable10x9_tr12_altanchor_capture120_20260512_161135_20260512_161135/
```

That run used `period_ms=10`, `active_ms=9` for all six Tags and achieved:

```text
tr_all=48400
sweeps_total=3082
ratio_ge7=0.953277
ratio_ge8=0.626541
```

This resolves the `10/9` versus `40/24` ambiguity: `10/9` is the correct
baseline parameter set for the current 6 x 10 Hz target. `40/24` belongs to
older pressure/fallback-style experiments and is not the primary baseline for
high-ge7 60 Hz operation.

Two no-RFD 120 s captures were then run with the same runtime TDMA parameters:

| Run | Tag image | RFD/TR diag | ge7 | ge8 |
|---|---|---:|---:|---:|
| `capture_six_tags_60hz_a27_nodiag_120s_20260626_004530` | `a27-nodiag-g1200-r1000` | `0 / 0` | `0.453920` | `0.289928` |
| `capture_six_tags_60hz_stable10x9_repro_nodiag_120s_20260626_011117` | `stable10x9-repro-nodiag-20260626` | `0 / 0` | `0.599509` | `0.440418` |

The repro Tag image was built to match the 2026-05-12 style more closely:

```text
build-tag-stable10x9-repro-nodiag-20260626
marker stable10x9-repro-nodiag-20260626
APP_TAG_TDMA_SLOT_COUNT=1
APP_TAG_RF_DIAG_OUTPUT_ENABLE=0
APP_TAG_TR_RF_DIAG_COMPACT_ENABLE=0
APP_TAG_RF_DIAG_LEGACY_RFD_ENABLE=0
APP_TAG_RF_DIAG_TAG_RX_ENABLE=0
APP_TAG_CIR_FEATURE_OUTPUT_ENABLE=0
APP_TAG_CIR_FULL_OUTPUT_ENABLE=0
APP_TAG_ALT_RXG_BLE_DIAG_ENABLE=0
APP_ALT_SS_TWR_GUARD_US=1200
APP_ALT_SS_TWR_RESP_SPACING_US=1000
```

All six Tags were OTA-updated successfully to that marker before the repro
capture. Therefore the current 60 Hz failure is not caused by RFD output and is
not explained by choosing `10/9` incorrectly.

Failure shape in the repro run:

- Overall late-rank anchors are weaker: Anchor 5 `0.666`, Anchor 6 `0.606`,
  Anchor 7 `0.520`.
- `BSF66F` and `BS955A` dominate the ge7 loss.
- The 2026-05-12 reference also had weak Anchor H, but only one weak anchor is
  still compatible with high ge7. The current problem is multiple weak links in
  the same sweeps.

Next baseline test:

1. Restore A-H Anchors to the 2026-05-12 A18 baseline image by Anchor OTA.
2. Keep the current no-RFD `stable10x9-repro-nodiag-20260626` Tags.
3. Re-run the same 120 s 6 x 10 Hz capture.
4. Only if ge7 returns above the system floor, resume testing low-cost RFD
   paths.

A18 rollback artifacts:

```text
Anchor build:
SS-TWR/alt-SS-TWR/broadcast/build-anchor-unified-ota-altbcast-responder-a18-g1200-r1000-20260512_154806

Anchor marker:
altbcast-responder-a18-g1200-r1000-20260512_154806

Master_Anchor carrier:
SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-anchor-lfrc-anchoronly-altbcast-embed-altbcast-responder-a18-g1200-r1000-20260512_154806/zephyr/merged_domains.hex
```

Verified properties:

```text
APP_ALT_SS_TWR_GUARD_US=1200
APP_ALT_SS_TWR_RESP_SPACING_US=1000
APP_ANCHOR_RESP_DELAY_UUS=1200
Master_Anchor carrier LFRC check: pass
```

Hashes:

```text
b1288ef0f8f8e60dd248fb65e6cc666fdac18cb7ef2d2f2a4d1006042f746fc8  dfu_application.zip
3769f850dc065a3eccd2896ff824ecbc8cdf554dc4a1564a9befd84086c2e062  anchor/zephyr/zephyr.signed.bin
6a458cde917fad96094d24aa75a68d2d8b45d1d4cc6be31273bff299280462ad  Master_Anchor merged_domains.hex
```

Execution status:

- Active Anchor OTA payload was prepared and verified for A18.
- Flashing the protected `Master_Anchor` SNR `960148546` was not performed
  because `.protec/noflash960148546` blocked the operation.
- Continue only after explicit authorization to flash the protected
  `Master_Anchor` carrier for this A18 baseline test.

## Execution Status - 2026-06-25 04:10

Hardware flashed:

- `Master_Tag` B120 SNR `1050070698`:
  `build-master-control-b120-m1-master-tag-lfrc-rfdiag-v2-g1200-r1000-20260625/zephyr/merged_domains.hex`
- `Master_Anchor` B120 SNR `960148546`:
  `build-master-control-b120-m1-master-anchor-lfrc-rfdiag-v2-g1200-r1000-20260625/zephyr/merged_domains.hex`
- Listener E SNR `760184767`:
  `build-uwb-listener-poll-diag-generic-20260625/merged.hex`

Legacy listener SNR `760185886` was not flashed and its serial port was not used.

Tag OTA:

- `BSF66F`: OTA success, marker verified by runtime `VERSION`:
  `tag-rfdiag-v2-g1200-r1000`
- `BS2DCE`: failed before upload. The OTA logs do not show the target being
  matched/accepted in scan; this is a target visibility or advertising/matching
  problem before DFU transport, not a proven SMP upload failure.
- `BSDC91`: same failure class as `BS2DCE`; target was not accepted into the
  OTA transport path before timeout.
- `BS9336`, `BS955A`, `BSCCF4`: not attempted after the repeated phase-C
  target-discovery failure pattern.

Tag OTA logs:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/tag_ota/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/tag_ota_remaining/
```

Anchor OTA:

- No deployed Anchor was updated.
- Anchor A was tested as the gate target.
- Initial runs exposed a Master_Anchor CDC write-timeout if the controller was
  left to enter AUTOPOS discovery before OTA setup.
- After early `mode ota`, Anchor A reached UUID target selection and
  `DFU SMP service ready`.
- The upload gate then failed at the SMP image-state request. The first
  `write_req` callback returned `err=17` / rc `-5`; later `write_cmd` attempts
  timed out with rc `-116`.
- Final Anchor A blocker:
  `ota_gate_failed_after_dfu_ready`, `request did not reach anchor BLE SMP transport`.
- Master_Anchor was returned to AUTOPOS anchor-control mode afterward.

Anchor OTA logs:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_ota/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_ota_retry_after_reset/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_ota_from_ota_mode/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_ota_A_stall240/
```

Minimal capture:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_60s_listenerE/
```

Result:

- Capture success: `true`
- `tr_all=3632`
- `tr_valid_all=2815`
- `rfd_all=2821`
- `rfd_joined_all=2821`
- `tag_diag_valid=2821`
- `anchor_diag_valid=0` because Anchors remained on the old A18/v1 payload.
- Listener E `lpd_rows=241`, all for `tag_id=1`, `poll_mask=0xff`.
- Time-filtered Listener E join:
  `range_diag_listener_E_joined.csv`, `joined_rows=206`,
  `time_rejected=163`, `max_time_delta_s=0.5`.
- After the host parser tag-id backfill fix, the same run also joins without
  forcing a default tag id:
  `range_diag_listener_E_joined_tagid_backfilled.csv`, `joined_rows=206`,
  `time_rejected=163`, `default_tag_id=null`.

Joined outputs:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_60s_listenerE/recv_20260625_040049/tag_rf_diag.csv
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_60s_listenerE/recv_20260625_040049/range_diag_joined.csv
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_60s_listenerE/recv_20260625_040049/range_diag_joined_tagid_backfilled.csv
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_60s_listenerE/recv_20260625_040049/range_diag_listener_E_joined.csv
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_60s_listenerE/recv_20260625_040049/range_diag_listener_E_joined_tagid_backfilled.csv
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_60s_listenerE/listener/listener_20260625_040048/lpd.csv
```

Gate status:

| Gate | Status |
|---|---|
| Tag `RFD` output | Pass for `BSF66F`; `RFD` rows produced and joined to TR rows |
| Anchor v2 parsing | Not run on deployed Anchors; Anchor OTA did not complete |
| Listener poll capture | Pass; Listener E produced `LPD` rows |
| Listener E join | Partial pass; 206 time-consistent Anchor-E proxy joins in the single-Tag run, including the no-default-tag-id repaired join |
| Proxy sanity | Not run; requires controlled body-shadow dataset |

Next technical blockers:

- Fix Tag OTA target visibility or advertising/matching for `BS2DCE`/`BSDC91`
  class failures before attempting the remaining Tags.
- Fix Anchor BLE SMP gate after DFU-ready before attempting A-H rollout. The
  next Anchor gate should reproduce Anchor A from early `mode ota` with a reset
  before the upload gate, then inspect why image-state read gets no response.
- Preserve the working single-Tag RF diagnostics capture as the parser/listener
  regression fixture.

## Execution Status - 2026-06-25 05:05

This update supersedes the 04:10 Anchor OTA status. The earlier Anchor A SMP
gate failure was worked around by a two-step flow:

1. Put the target Anchor into DFU-ready and issue a manual pre-reset request.
2. Reset only `Master_Anchor` B120 SNR `960148546`, wait 20 s, then run the real
   upload attempt.

Anchor OTA result:

- Anchor A: uploaded successfully in
  `logs/rfdiag_v2_overnight_20260625/anchor_ota_A_prereset_masterreset_20260625_042232/`.
- Anchors B-H: uploaded successfully in
  `logs/rfdiag_v2_overnight_20260625/anchor_ota_BH_prereset_masterreset_20260625_042847/`.
- B-H per-anchor `single_shot` summaries all ended with
  `returncode=0`, `classification=D`, `reason=ota_success_observed`.
- Post-verify initially failed in the automation because
  `verify_all_anchor_responder_runtime.py` missed the
  `scan_anchor_role_counts` import. That script bug is fixed.
- After a manual reset of `Master_Anchor`, all A-H anchor control links rebuilt.
  `anchor version all` produced `ANCHOR_FW` notifications for A-H with marker
  prefix `alt-bcast-a19-rfdiag-v2-g1200-r`. The notification string is truncated
  by the firmware output buffer, but all eight anchors reported the rfdiag-v2
  marker prefix.
- `anchor role all responder` then completed with
  `sent=8 ready=8/8 total_sent=24` and `rc=0`.

Manual verification logs:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/manual_anchor_version_after_master_reset_20260625_0455.log
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/manual_anchor_role_responder_20260625_0458.log
```

Main visibility capture after Anchor v2 rollout:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_30s_listenerE_anchor_v2_full_listener/
```

Result:

- Capture success: `true`
- `tr_all=1848`
- `tr_valid_all=1134`
- `rfd_all=1139`
- `rfd_joined_all=1139`
- `anchor_diag_valid=1139`
- `tag_diag_valid=1139`
- Anchor-side poll diagnostics appeared for all A-H.
- Listener E produced `228` `LPD` rows.
- Listener E join to Anchor E rows:
  `range_diag_listener_E_joined.csv`, `joined_rows=158`,
  `time_rejected=0`, `max_time_delta_s=0.5`,
  `listener_anchor_id=4`, `default_tag_id=1`.

The 60 s run immediately before it also passed the Anchor/Tag diagnostic gate:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_60s_listenerE_after_anchor_v2/
```

- `tr_all=3576`
- `tr_valid_all=2278`
- `rfd_all=2284`
- `rfd_joined_all=2284`
- `anchor_diag_valid=2284`
- `tag_diag_valid=2284`
- Listener E `LPD` rows: `234`
- Listener E join: `joined_rows=150`, `time_rejected=134`

The 60 s listener window was too short because listener duration starts before
the Tag capture setup. Use `--listener-extra-s >= 120` for future short
validation captures, or make the wrapper start the listener after setup.

Updated gate status:

| Gate | Status |
|---|---|
| Tag `RFD` output | Pass for `BSF66F`; RFD rows produced and joined |
| Anchor v2 parsing | Pass; A-H all produced anchor-side poll diagnostics |
| Listener poll capture | Pass; Listener E produced LPD rows |
| Listener E join | Pass for data visibility; 158 Anchor-E joins with no time rejection in the 30 s run |
| Proxy sanity | Not run; requires controlled body-shadow/body-orientation data |

Remaining limitations:

- Only `BSF66F` was updated to Tag rfdiag-v2. A follow-up broad `BS*`
  visibility check at 05:10 still produced scan hits only for `BSF66F`, so the
  remaining Tags should not be forced through OTA with a missing pre-version
  gate.
- The current solver is unchanged. These captures only prove the new RF
  diagnostics are observable, aligned, and replayable.
- Do not use Listener E as a geometric/TDoA input in this plan.

Follow-up Tag visibility check:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/manual_master_tag_broad_bs_visibility_20260625_0510.log
```

Result:

- `ota_target name -`
- `ota_target prefix BS`
- `scan` / `conn`
- scan hits: `BSF66F`, `BSF66F`
- unique visible BS code: `BSF66F`
- Master_Tag was restored afterward to `ota_target name=BSF66F`,
  `prefix=-`, `tdma hold=1`, `MODE IDLE`.

## Completion Audit - 2026-06-25 05:20

Completed and verified:

- Freeze rollback artifacts preserved and rechecked:
  `firmware_freeze/autopos_full_system_20260625_0245`,
  `sha256sum -c SHA256SUMS.txt` passed.
- Generic listener path exists at `UWB_listener`; legacy listener path exists at
  `UWB_listener_old`.
- Legacy listener SNR `760185886` was not opened/flashed during this run.
- New Listener E SNR `760184767` was flashed with the generic listener image.
- Anchor response payload v2 lightweight poll diagnostics are implemented and
  validated in capture output; no Anchor full `ACC_MEM` read is part of the
  response hot path.
- Tag v1/v2 response parsing and `RFD` forwarding are implemented and validated
  on `BSF66F`.
- Master/host capture parsers produce:
  `tag_rf_diag.csv`, `range_diag_joined.csv`, and
  `range_diag_listener_E_joined.csv`.
- `Master_Tag` and `Master_Anchor` rfdiag-v2 B120 builds both pass
  `assert_b120_internal_osc_build.sh`.
- Anchor A-H OTA completed through `Master_Anchor`; all A-H were manually put
  back into responder runtime role with `ready=8/8`.
- Validation capture proves Anchor poll diag + Tag response diag + Listener E
  poll diag are observable, aligned, and replayable for the updated Tag.

Deferred by user scope clarification:

- Current overnight acceptance is `BSF66F` only. Remaining Tags are not part of
  this run's completion criteria.
- `BS2DCE` and `BSDC91` failed before upload with no transport result, and the
  follow-up broad `BS*` visibility check still saw only `BSF66F`.
- Do not force OTA for the deferred Tags until they are physically powered,
  advertising, and visible to `Master_Tag` in a pre-version or broad scan.

## RFD Hot-Path A/B - 2026-06-25 12:30

Question tested: are the many `T` timeout rows caused by Listener E, or by the
new Tag-side `RFD` diagnostic output load?

Result: Listener E is not the cause. The timeout collapse follows the Tag
`RFD` output path.

Evidence:

| Case | Path | TR valid | RFD rows | ge4 | ge7 | ge8 |
|---|---|---:|---:|---:|---:|---:|
| RFD-on + Listener E | `logs/rfdiag_v2_overnight_20260625/capture_BSF66F_120s_listenerE_anchor_v2_20260625_120531/recv_20260625_120532/` | `4453/7064 = 0.630379` | `4457` | `0.935447` | `0.124575` | `0.001133` |
| RFD-on, no Listener | `logs/rfdiag_v2_overnight_20260625/capture_BSF66F_120s_no_listener_anchor_v2_20260625_121848_20260625_121848/` | `4355/6968 = 0.625000` | `4361` | `0.924225` | `0.095293` | `0.002296` |
| RFD-off, no Listener | `logs/rfdiag_v2_overnight_20260625/capture_BSF66F_120s_no_listener_anchor_v2_tag_rfd0_20260625_122948_20260625_122948/` | `7572/8552 = 0.885407` | `0` | `0.977549` | `0.927035` | `0.291862` |

Per-anchor valid ratios in the RFD-off test:

| Anchor | Valid |
|---:|---:|
| 0 | `355/1069 = 0.332` |
| 1 | `1026/1069 = 0.960` |
| 2 | `1045/1069 = 0.978` |
| 3 | `1045/1069 = 0.978` |
| 4 | `1044/1069 = 0.977` |
| 5 | `1045/1069 = 0.978` |
| 6 | `1043/1069 = 0.976` |
| 7 | `969/1069 = 0.906` |

Interpretation:

- `range_diag_joined.csv` blank diagnostic columns mean `rfd_joined=0` for that
  TR row; they are not Listener columns.
- Listener joins are in `range_diag_listener_E_joined.csv`, and Listener E only
  populates Anchor E proxy rows (`anchor_id=4`) by design.
- Removing Listener E did not improve the timeout pattern.
- Disabling Tag `RFD` output improved `ge7` from roughly `10-12%` to `92.7%`.
  This confirms that the current `APP_TAG_RF_DIAG_OUTPUT_PERIOD=1` output path
  is too heavy for normal 10 Hz full-sweep ranging.

Historical live state immediately after this A/B, before the v4 fix below:

- `BSF66F` is now on temporary marker:
  `tag-rfdiag-v2-rfd0-g1200-r1000`.
- `Master_Tag` B120 SNR `1050070698` is flashed with:
  `build-master-control-b120-m1-master-tag-lfrc-rfdiag-v2-rfd0-g1200-r1000-20260625/zephyr/merged_domains.hex`.
- Generated active OTA payload is now the temporary Tag rfd0 payload, not the
  Anchor payload.
- Anchors A-H remain on `alt-bcast-a19-rfdiag-v2-g1200-r1000` responder runtime.
- Listener E SNR `760184767` remains on the generic listener image.
- Legacy listener SNR `760185886` was not opened or flashed.

Engineering conclusion:

- `RFD` as currently implemented is valid as a data format, but not safe at
  per-anchor/per-sweep rate in the ranging hot path.
- The next firmware iteration should either decimate `RFD` output
  (`APP_TAG_RF_DIAG_OUTPUT_PERIOD > 1`) or buffer diagnostics and publish them
  only after the response collection window. Until that is fixed, do not use the
  RFD-on image for positioning quality captures.

## RFD-Safe Tag v4 - 2026-06-25 13:25

The v2 `RFD` and v3 compact-TR experiments exposed two separate hot-path costs:

1. Legacy `RFD` emitted one extra text line per anchor and overloaded the normal
   ranging output path.
2. Even after removing those extra lines, Tag-side `dwt_readdiagnostics()` in
   every response slot still damaged the 1 ms response schedule.

Final v4 rule:

- Legacy `RFD` rows are disabled:
  `APP_TAG_RF_DIAG_LEGACY_RFD_ENABLE=0`.
- Compact RF diagnostics are appended to the normal range row:
  `APP_TAG_TR_RF_DIAG_COMPACT_ENABLE=1`, `TR;3;...;D1,<base64>`.
- Tag-side response diagnostics reads are disabled in the hot path:
  `APP_TAG_RF_DIAG_TAG_RX_ENABLE=0`.
- Anchor-side poll diagnostics from response payload v2 remain enabled and are
  carried in the compact `D1` records.
- Tag-side q8 diagnostic fields are therefore zero unless
  `APP_TAG_RF_DIAG_TAG_RX_ENABLE` is explicitly enabled for a non-performance
  experiment.

Current v4 artifacts:

```text
Tag marker:
tag-rfdiag-v4tr-g1200-r1000

Tag OTA payload:
SS-TWR/alt-SS-TWR/broadcast/build-tag-ble-unified-rfdiag-v4tr-g1200-r1000-20260625/dfu_application.zip
sha256 6017ed2ebd507956a6c8d68d2815b3040a8b08382f5d059ff4ab968667207a67

Master_Tag carrier:
SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-tag-lfrc-rfdiag-v4tr-g1200-r1000-20260625/zephyr/merged_domains.hex
sha256 9d43b329202ff28ad3e6456a23cbcf98c6a1b4429012b58fd2f08258db137e90
```

Deployment performed:

- `Master_Tag` B120 SNR `1050070698` flashed with the v4 carrier.
- `BSF66F` OTA updated and runtime marker verified as
  `tag-rfdiag-v4tr-g1200-r1000`.
- Old listener SNR `760185886` was not touched.
- No listener was used in the final v4 no-listener acceptance capture.

Final no-listener acceptance capture:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_120s_no_listener_anchor_v2_tag_v4tr_20260625_131518_20260625_131518/
```

Result:

- `success=true`
- `tr_all=8696`
- `tr_valid_all=7566`
- `tr_diag_all=8696`
- `rfd_all=0`
- `rfd_joined_all=8696`
- `sweeps_total=1087`
- `sweeps_ge7=921`, `ratio_ge7=0.847286`
- `sweeps_ge8=277`, `ratio_ge8=0.254830`
- status counts: `O=7566`, `T=1130`
- valid range rows have `anchor_diag_valid=1`, `tag_diag_valid=0`.

Interpretation:

- The `RFD` line flood is gone.
- The Tag no longer reads response diagnostics in the 1 ms response hot path.
- Normal ranging output is still one `TR` summary per sweep; RF diagnostics are a
  best-effort compact trailer and are never allowed to replace or delay the
  range summary.
- The v4 `ge7` result is lower than the earlier pure `RFD-off` A/B baseline
  (`0.927035`), but it no longer collapses like v2/v3. Treat this as the current
  safe diagnostic build and use the pure no-diagnostic freeze when a strict
  positioning baseline is required.

## Rank0 A/B Test - 2026-06-25 19:05

Question: why is Anchor A bad when A is rank0, even though later 1 ms response
slots are healthy?

Evidence:

- Baseline A19 Anchor v2 + v4 Tag, A rank0:
  `capture_BSF66F_120s_no_listener_anchor_v2_tag_v4tr_20260625_131518_20260625_131518`
  - A/0: `424/1087 = 0.390`
  - B-G: `0.969-0.977`
  - H/7: `784/1087 = 0.721`
- Rank-rotation test with B as rank0:
  `capture_BSF66F_120s_no_listener_anchor_v2_tag_v4tr_rank1_Brank0_20260625_180213_20260625_180213`
  - A/0: `682/1092 = 0.625`
  - B/1: `322/1092 = 0.295`
  - C-H: `0.977-0.978`
  - Conclusion: the poor link follows the first response slot, not a fixed
    physical A/H link.
- Temporary A20 `skipr0` test, where rank0 skipped v2 diagnostics and sent v1:
  `capture_BSF66F_120s_no_listener_anchor_A_skipr0_AB_tag_v4tr_20260625_184103_20260625_184103`
  - A/0: `0/1103 = 0.000`
  - B-G stayed about `0.971-0.978`, H/7 `0.630`
- Temporary A21 `zeror0` test, where rank0 kept v2 length but skipped the
  diagnostic read and emitted zero diagnostics:
  `capture_BSF66F_120s_no_listener_anchor_A_zeror0_AB2_tag_v4tr_20260625_185015_20260625_185015`
  - A/0: `0/1091 = 0.000`
  - B-G stayed about `0.975-0.978`, H/7 `0.683`

Important recovery finding:

- After the temporary OTA tests, Anchor A was running A19 again but its runtime
  role was `matrix`, not `responder`.
- Command sent through Master_Anchor:
  `anchor role F3BB7A04104F9CB8561DDDACB9E53714 responder`
- Confirmed state notify:
  `fw=alt-bcast-a19-rfdiag-v2-g1200-r role=responder`.
- Recovery verification:
  `capture_BSF66F_30s_after_A_role_responder_restore_20260625_190421_20260625_190421`
  - A/0: `102/262 = 0.389`
  - B/1: `256/262 = 0.977`
  - C/2: `251/262 = 0.958`
  - D/3: `256/262 = 0.977`
  - E/4: `256/262 = 0.977`
  - F/5: `256/262 = 0.977`
  - G/6: `255/262 = 0.973`
  - H/7: `179/262 = 0.683`

Current state after recovery:

- `BSF66F`: `tag-rfdiag-v4tr-g1200-r1000`
- Anchor A restored to A19 and forced to responder role.
- Master_Anchor B120 restored to the A19 carrier:
  `build-master-control-b120-m1-master-anchor-lfrc-rfdiag-v2-g1200-r1000-20260625`
- `apps/master_ota/generated/active_ota_payload.json` restored to:
  `alt-bcast-a19-rfdiag-v2-g1200-r1000`

Interpretation:

- Rank rotation still supports the rank0 timing hypothesis.
- The A20/A21 `skipr0` implementation is not a valid fix and must not be used
  as production firmware.
- The next diagnostic step should add explicit responder-side delayed-TX
  profiling/slack output around rank0, not change the response payload shape
  blindly.

## Rank0 Responder Profile - 2026-06-25 19:26

Temporary profile build:

```text
alt-bcast-a22-rfdiag-v2-prof-g1200-r1000
```

Only Anchor A was OTA-updated to this build. Master_Anchor was temporarily
flashed with:

```text
build-master-control-b120-m1-master-anchor-lfrc-rfdiag-v2-prof-g1200-r1000-20260625
```

The build kept the A19/v2 response behavior and only enabled low-rate
responder-side profile/diag printk. Anchor A was forced to responder role before
capture.

Evidence paths:

```text
Anchor A CDC profile:
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_A_prof_cdc_20260625_192627/anchor_A_cdc.log

Range capture during profile:
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_60s_anchor_A_prof_20260625_192627_20260625_192627/
```

Range result during A22 profile:

- A/0: `0/541 = 0.000`
- B/1: `529/541 = 0.978`
- C/2: `528/541 = 0.976`
- D/3: `527/541 = 0.974`
- E/4: `529/541 = 0.978`
- F/5: `529/541 = 0.978`
- G/6: `526/541 = 0.972`
- H/7: `334/541 = 0.617`

Responder-side profile result:

- Final diag line: `tx_miss=292`, `ok=0`, `rx_err=1`.
- Per-tag counters: `tag_poll=0,292,0,0,0,0,0,0`,
  `tag_ok=0,0,0,0,0,0,0,0`,
  `tag_tx_miss=0,292,0,0,0,0,0,0`.
- Profile windows: `294/294` delayed-TX attempts missed.
- `avg start_us` range: `1220-1434` us.
- `avg txprog_us` range: `976-1190` us.
- `min_slack_uus` range: `-55585` to `96`.
- Last profile window:
  `attempts=10 misses=10 avg_us frame=183 ts=335 txprog=1190 start=1434 starttx=183 min_slack_uus=-129 resp_delay_uus=1200`.

Interpretation:

- Anchor A does receive the BSF66F poll as rank0.
- The failure is not a Tag RX-window issue and not a missing-poll issue.
- The rank0 responder hot path reaches delayed TX too late or too close to the
  deadline. With `APP_ANCHOR_RESP_DELAY_UUS=1200`, all profiled delayed-TX
  attempts missed.
- This directly confirms the rank0 deadline hypothesis. The likely contributor
  is accumulated hot-path work before `dwt_starttx()`, including response-frame
  construction, timestamp handling, and diagnostic reads/packing.

Recovery after profile:

- Master_Anchor restored to:
  `build-master-control-b120-m1-master-anchor-lfrc-rfdiag-v2-g1200-r1000-20260625`.
- `apps/master_ota/generated/active_ota_payload.json` restored to:
  `alt-bcast-a19-rfdiag-v2-g1200-r1000`.
- Anchor A OTA-restored to A19 and forced back to responder role.
- Final 30 s restore verification:
  `capture_BSF66F_30s_after_A19_restore_20260625_193438_20260625_193438`
  - A/0: `100/272 = 0.368`
  - B-F: `266/272 = 0.978`
  - G/6: `264/272 = 0.971`
  - H/7: `169/272 = 0.621`

Current state after this test:

- Anchor A: `alt-bcast-a19-rfdiag-v2-g1200-r1000`, runtime role `responder`.
- Master_Anchor: A19 carrier.
- Active Anchor OTA payload: A19.
- `BSF66F`: `tag-rfdiag-v4tr-g1200-r1000`.

## Rank0 Hot-Path Reduction Tests - 2026-06-25 20:08

Two temporary Anchor A builds tested the rank0 deadline hypothesis without
changing `guard=1200` or `resp_spacing=1000`.

### A23: no diagnostics on responder hot path

Temporary build:

```text
alt-bcast-a23-nodiag-prof-g1200-r1000
```

Evidence:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_45s_A23_nodiag_prof_20260625_195807_20260625_195807/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_A_A23_nodiag_prof_cdc_20260625_195807/anchor_A_cdc.log
```

Range result:

- A/0: `370/402 = 0.920`
- B/1: `391/402 = 0.973`
- C/2: `390/402 = 0.970`
- D/3: `391/402 = 0.973`
- E/4: `389/402 = 0.968`
- F/5: `388/402 = 0.965`
- G/6: `392/402 = 0.975`
- H/7: `312/402 = 0.776`
- `ratio_ge7=0.952736`, `ratio_ge8=0.701493`

Responder profile:

- Final diag: `ok=187`, `tx_miss=8`, `tag_poll=0,195,0,0,0,0,0,0`.
- Delayed-TX miss rate: `8/(187+8) = 4.1%`.
- Full profile windows: `195` attempts, `8` misses.
- `start_us` range: `707-769`.
- `txprog_us` range: `491-552`.

### A24: rank0 fast path, non-rank0 diagnostics kept

Temporary build:

```text
alt-bcast-a24-r0fast-prof-g1200
```

This build keeps v2 response length for rank0 but skips rank0 poll diagnostics
before delayed TX. Non-rank0 responders still emit v2 diagnostics.

Evidence:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_45s_A24_r0fast_prof_20260625_200856/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_30s_A24_r0fast_prof_retry_20260625_201105/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_A_A24_r0fast_prof_cdc_retry_20260625_201105/anchor_A_cdc.log
```

45 s range result:

- A/0: `378/405 = 0.933`
- B/1: `396/405 = 0.978`
- C/2: `387/405 = 0.956`
- D/3: `394/405 = 0.973`
- E/4: `393/405 = 0.970`
- F/5: `396/405 = 0.978`
- G/6: `396/405 = 0.978`
- H/7: `307/405 = 0.758`
- `ratio_ge7=0.960494`, `ratio_ge8=0.696296`

Joined RF diagnostics matched the intended behavior:

- A/0 rank0: `anchor_diag_valid=0` for all `405/405` rows.
- B-G successful rows: `anchor_diag_valid=1`.
- H successful rows: `anchor_diag_valid=1`.

Responder profile retry:

- Final diag: `ok=604`, `tx_miss=32`, `tag_poll=0,636,0,0,0,0,0,0`.
- Delayed-TX miss rate: `32/(604+32) = 5.0%`.
- Full profile windows: `238` attempts, `14` misses.
- `start_us` range: `749-792`.
- `txprog_us` range: `552-590`.

Conclusion:

- The rank0 failure is a responder hot-path deadline problem.
- Removing or deferring diagnostics before `dwt_starttx()` recovers A/0 from
  about `0.37-0.39` validity to about `0.92-0.93`.
- A24 is the better production direction than A23 because it preserves v2
  diagnostics on non-rank0 responders while protecting rank0 timing.

Recovery after A23/A24 tests:

- Master_Anchor restored to:
  `build-master-control-b120-m1-master-anchor-lfrc-rfdiag-v2-g1200-r1000-20260625`.
- Active Anchor OTA payload restored to:
  `alt-bcast-a19-rfdiag-v2-g1200-r1000`.
- Anchor A OTA-restored to A19 and forced back to responder role.
- Final 30 s restore capture:
  `capture_BSF66F_30s_restore_A19_after_A24_20260625_201807`
  - A/0: `97/261 = 0.372`
  - B/1: `255/261 = 0.977`
  - C/2: `254/261 = 0.973`
  - D/3: `255/261 = 0.977`
  - E/4: `255/261 = 0.977`
  - F/5: `255/261 = 0.977`
  - G/6: `255/261 = 0.977`
  - H/7: `178/261 = 0.682`

Current operational state: A is A19 responder, Master_Anchor is the A19 carrier,
the active Anchor OTA payload is A19, and BSF66F remains
`tag-rfdiag-v4tr-g1200-r1000`.

## Rank0 Diagnostics Preservation Tests - 2026-06-25 22:14

Three follow-up temporary Anchor A builds tested whether rank0 timing can stay
healthy while preserving useful RF diagnostics. In all three tests the temporary
Master_Anchor carrier was used only for Anchor A OTA. For the actual range
captures, Master_Anchor was restored to the stable A19 carrier, then the normal
8/8 responder preflight was run before capture. This isolates Anchor A responder
firmware behavior from temporary carrier control-plane instability.

### A25: post-TX diagnostics read, no payload use

Temporary build:

```text
alt-bcast-a25-postread-prof-g1200
```

Evidence:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_45s_a25_A19carrier_20260625_221406/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_A_a25_A19carrier_cdc_20260625_221406/anchor_A_cdc.log
```

Range result:

- A/0: `399/412 = 0.968`
- B/1: `403/412 = 0.978`
- C/2: `403/412 = 0.978`
- D/3: `401/412 = 0.973`
- E/4: `401/412 = 0.973`
- F/5: `403/412 = 0.978`
- G/6: `403/412 = 0.978`
- H/7: `251/412 = 0.609`
- `ratio_ge7=0.978155`, `ratio_ge8=0.589806`

Diagnostic behavior:

- A/0 rank0: `anchor_diag_valid=0` for `412/412` rows.
- Non-rank0 successful rows kept normal v2 diagnostics.
- Profile window: `46` attempts, `0` misses; `start_us=781`,
  `txprog_us=578`.

Interpretation: post-TX diagnostic reading does not hurt rank0 timing, but A25
still gives no A/0 diagnostics to the Tag-side range stream.

### A26: post-TX diagnostics cached into next rank0 payload

Temporary build:

```text
alt-bcast-a26-delayed-prof-g1200
```

Evidence:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_45s_a26_A19carrier_20260625_221925/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_A_a26_A19carrier_cdc_20260625_221925/anchor_A_cdc.log
```

Range result:

- A/0: `382/392 = 0.974`
- B/1: `383/392 = 0.977`
- C/2: `382/392 = 0.974`
- D/3: `383/392 = 0.977`
- E/4: `379/392 = 0.967`
- F/5: `383/392 = 0.977`
- G/6: `383/392 = 0.977`
- H/7: `241/392 = 0.615`
- `ratio_ge7=0.974490`, `ratio_ge8=0.602041`

Diagnostic behavior:

- A/0 rank0 diagnostics present on `380/392` rows.
- A/0 flags were `3` on those rows: `VALID | DELAYED`.
- Non-rank0 successful rows kept normal flags `1`.
- Profile windows: `44` attempts, `0` misses; `start_us=786-854`,
  `txprog_us=579-671`.

Interpretation: A26 is the best tested solution. It keeps rank0 validity near
the A25/A27 fast-path level while restoring A/0 diagnostics into the normal
range stream, explicitly marked as delayed by one rank0 poll.

### A27: post-TX diagnostics side-channel

Temporary build:

```text
alt-bcast-a27-side-prof-g1200
```

Evidence:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_45s_a27_A19carrier_20260625_222439/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_A_a27_A19carrier_cdc_20260625_222439/anchor_A_cdc.log
```

Range result:

- A/0: `401/412 = 0.973`
- B/1: `400/412 = 0.971`
- C/2: `399/412 = 0.968`
- D/3: `403/412 = 0.978`
- E/4: `402/412 = 0.976`
- F/5: `403/412 = 0.978`
- G/6: `402/412 = 0.976`
- H/7: `261/412 = 0.633`
- `ratio_ge7=0.968447`, `ratio_ge8=0.616505`

Diagnostic behavior:

- A/0 rank0: `anchor_diag_valid=0` for `412/412` rows.
- Expected `APD;` side-channel lines did not appear in the captured Anchor A
  CDC log (`APD count = 0`).
- Profile window: `46` attempts, `0` misses; `start_us=793`,
  `txprog_us=592`.

Interpretation: A27 protects rank0 timing but currently does not deliver the
side-channel diagnostics, so it is not useful without additional firmware/log
plumbing.

### Decision

Among A25/A26/A27, A26 is the clear winner:

- It gives the highest A/0 validity in this test set: `0.974`.
- It restores A/0 diagnostics in the normal Tag range stream.
- It marks those diagnostics with `flags=3`, so the solver/parser can treat
  them as one-burst delayed rather than current-frame pre-TX diagnostics.
- It does not require a separate CDC side-channel join.

Production direction: implement the A26 pattern cleanly as the next Anchor
diagnostic version, with an explicit `diag_age` or `diag_seq` field if the
delayed diagnostic is used beyond controlled experiments.

Recovery after A25/A26/A27:

- Master_Anchor carrier restored to:
  `build-master-control-b120-m1-master-anchor-lfrc-rfdiag-v2-g1200-r1000-20260625`.
- Active Anchor OTA payload restored to:
  `alt-bcast-a19-rfdiag-v2-g1200-r1000`.
- Anchor A A19 OTA upload completed:
  `anchor_ota_A_restore_A19_after_A27_20260625_222755`.
- Final skip-preflight smoke capture:
  `capture_BSF66F_30s_restore_A19_after_A27_skippre_20260625_223903`
  confirmed UWB ranging still runs, but A/0 was absent because Anchor A was not
  successfully forced back into responder role after the restore.
- The attempted Anchor BLE responder preflight after restore did not recover
  8/8 links (`conn_count=0`) before it was interrupted. This needs a follow-up
  control-plane recovery step before treating the restored physical setup as
  ready for normal experiments.

## A26 Baseline 120 s Capture - 2026-06-25 23:04

A26 was promoted to the working Anchor A baseline for rank0 diagnostics.

Baseline actions:

- Active Anchor OTA payload metadata set to:
  `alt-bcast-a26-delayed-prof-g1200`.
- Anchor A OTA updated successfully through:
  `anchor_ota_A_A26_baseline_20260625_230233`.
- Master_Anchor was switched back to the stable A19 control carrier for the
  responder preflight and capture.
- Responder preflight succeeded: `sent=8`, `ready=8/8`.

120 s capture:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_120s_A26_baseline_20260625_230426/
```

Per-anchor validity:

- A/0: `1069/1100 = 0.971818`
- B/1: `1075/1100 = 0.977273`
- C/2: `1059/1100 = 0.962727`
- D/3: `1076/1100 = 0.978182`
- E/4: `1074/1100 = 0.976364`
- F/5: `1076/1100 = 0.978182`
- G/6: `1075/1100 = 0.977273`
- H/7: `715/1100 = 0.650000`

Sweep validity:

- `ratio_ge7=0.974545`
- `ratio_ge8=0.628182`
- valid-count distribution: `{6: 4, 7: 381, 8: 691}`

Diagnostics:

- A/0 diagnostics: `anchor_diag_valid=1` on `1063/1100` rows.
- A/0 flags: `3` on `1063/1100` rows, confirming delayed diagnostics.
- H/7 diagnostics: valid on `715/1100` rows, matching its successful ranges.

Current working state:

- Anchor A is running A26.
- Active Anchor OTA payload metadata is A26.
- Master_Anchor control carrier is the stable A19 carrier, used because it
  gives reliable responder preflight/control behavior.
- Tag `BSF66F` remains on the v4 compact-TR path:
  `tag-rfdiag-v4tr-g1200-r1000`.
- Legacy Tag `RFD` rows and Tag-side hot-path RX diagnostics remain disabled for
  live positioning captures.

Frozen working result:

- This A26 + v4tr combination is the current RF-diagnostic working baseline.
- A/0 recovered to `1069/1100 = 0.971818`.
- `ratio_ge7=0.974545`, `ratio_ge8=0.628182`.
- Remaining 8/8 limitation is dominated by H/7 at `715/1100 = 0.650000`, not by
  Tag-side diagnostic output or rank0 A failure.

## Wand v4tr OTA + 4-Tag Static Pressure Capture - 2026-06-26 00:12

Goal: update the three Wand Tags to the current safe Tag firmware and run a
120 s static pressure capture with `BSF66F` plus the Wand Tags.

Tag OTA payload used:

```text
tag-rfdiag-v4tr-g1200-r1000
```

Wand OTA results:

- `BSCCF4` / Wand-A:
  - OTA upload success:
    `SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/tag_ota_wand_v4tr_20260626_000025/`
  - Follow-up version verification:
    `SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/tag_ota_wand_v4tr_verify_BSCCF4_20260626_000724/`
  - post `VERSION`: `tag-rfdiag-v4tr-g1200-r1000`.
- `BS9336` / Wand-B:
  - OTA + post verification:
    `SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/tag_ota_wand_v4tr_continue_20260626_000401/`
  - post `VERSION`: `tag-rfdiag-v4tr-g1200-r1000`.
- `BS955A` / Wand-C:
  - OTA + post verification:
    `SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/tag_ota_wand_v4tr_continue_20260626_000401/`
  - post `VERSION`: `tag-rfdiag-v4tr-g1200-r1000`.

Note: the first Wand OTA run used `/dev/ttyACM18`; after `BSCCF4` rebooted, the
Master_Tag CDC re-enumerated as `/dev/ttyACM19`. The remaining OTA work used the
stable by-id path:

```text
/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00
```

120 s static pressure capture:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_wand3_static120_v4tr_A26_20260626_000844_20260626_000844/
```

Capture configuration:

- Targets: `BSF66F,BSCCF4,BS9336,BS955A`.
- `tr-hz=10`, `tag-cir=off`.
- No listener path used.
- Anchor responder preflight succeeded: `sent=8`, `ready=8/8`.
- TDMA config matched all four targets at 10 Hz.
- Cleanup succeeded; all Tags were returned to quiet/IDLE state.

Overall result:

- `success=true`
- `controller_lost=false`
- `no_tr_timeout=false`
- `tdma_config_failed=false`
- `tr_all=35272`
- `tr_valid_all=25462`
- `rfd_all=0`
- `rfd_joined_all=35272`
- `ratio_ge7=0.724427`
- `ratio_ge8=0.504650`

Per-Tag result:

| Tag | Valid rows | ge7 | ge8 | Status summary |
|---|---:|---:|---:|---|
| `BSF66F` | `8464/9024 = 0.938` | `0.968085` | `0.663121` | `O=8464, T=560` |
| `BSCCF4` | `2107/8488 = 0.248` | `0.245052` | `0.131008` | `O=2107, R=95, T=6286` |
| `BS9336` | `6264/8832 = 0.709` | `0.680254` | `0.320652` | `O=6264, R=135, T=2433` |
| `BS955A` | `8627/8928 = 0.966` | `0.977599` | `0.881720` | `O=8627, T=301` |

Per-anchor diagnosis:

- `BSF66F` is healthy; remaining loss is mostly H/7
  (`784/1128 = 0.695`).
- `BS955A` is very healthy; H/7 is still the weakest but acceptable
  (`1011/1116 = 0.906`).
- `BS9336` is medium; A/0 and H/7 are weak
  (`A=527/1104 = 0.477`, `H=620/1104 = 0.562`).
- `BSCCF4` is the main failure source; all anchors are low
  (`0.149-0.277` per-anchor valid ratio). This looks like a Tag/placement/power
  or physical RF issue, not a global scheduler failure.

State restored after OTA:

- Active generated OTA payload was restored to Anchor A26:
  `alt-bcast-a26-delayed-prof-g1200`.
- `verify_ota_payload_kind.py --expected anchor` passed.

## Latest Gate - 2026-06-26 Evening

Current live check:

- Master_Tag is visible as
  `/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00`.
- Master_Anchor is visible as
  `/dev/serial/by-id/usb-Master_Anchor_BioSpur_BLE_Control_87EA2F4A526C5A02-if00`.
- A 60 s broad `BS*` connect/listen window saw only five Tags:
  `BS2DCE`, `BSCCF4`, `BSF66F`, `BS9336`, `BS955A`.
- `BSDC91` was not seen in scan/connect/CFG/TR/TD output.

Best no-RFD candidate remains:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_six_targets_bleint12_nopreflight_nordiag_120s_20260626_132626_20260626_132626/
```

It reached all six Tags with `rfd_all=0`, `tr_diag_all=0`,
`ge7=0.939401`, and `ge8=0.421001`.

Next action after `BSDC91` is physically/BLE recovered:

```bash
bash SS-TWR/alt-SS-TWR/broadcast/scripts/run_6tag_nordiag_baseline_candidate.sh
```

Do not re-enable RFD/RF diagnostics before this no-RFD six-Tag gate passes.

### Gate Execution Result

Command:

```bash
VISIBILITY_S=45 bash scripts/run_6tag_nordiag_baseline_candidate.sh
```

Result:

```text
[6TAG-GATE] seen=BS2DCE,BS9336,BS955A,BSCCF4,BSF66F
[6TAG-GATE] missing=BSDC91
[6TAG-GATE] aborting before capture; recover missing Tag BLE visibility first
```

Interpretation:

- The script correctly refused to run a 120 s baseline capture with only five
  visible Tags.
- The current remaining blocker for the baseline gate is `BSDC91` BLE
  visibility, not RFD output and not a five-connection BLE ceiling.

## Goal Continuation Check - 2026-06-26 Evening

Current objective remains unchanged:

- Restore the 6 Tag / 60 Hz no-RFD ranging baseline to the 2026-05-12 level.
- Only after that, test how much RF diagnostic information can be reintroduced
  without materially degrading normal TR output.

Best verified no-RFD 6 Tag candidate so far:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_six_targets_bleint12_nopreflight_nordiag_120s_20260626_132626_20260626_132626/
```

Evidence:

- `success=true`
- six targets all reached TDMA CFG
- `rfd_all=0`, `tr_diag_all=0`
- `tr_all=32608`, `tr_valid_all=29063`
- `ratio_ge7=0.939401`, `ratio_ge8=0.421001`

Per-Tag ge7 in that run:

| Tag | ge7 | ge8 |
|---|---:|---:|
| `BSF66F` | `0.947309` | `0.500000` |
| `BS2DCE` | `0.916388` | `0.638796` |
| `BSDC91` | `0.963314` | `0.300592` |
| `BSCCF4` | `0.903571` | `0.567857` |
| `BS9336` | `0.910470` | `0.500759` |
| `BS955A` | `0.947321` | `0.305177` |

Important properties of the candidate:

- It used the canonical `10/9` TDMA profile.
- It did not use RFD or compact RF diagnostics.
- It used the historical six-Tag slot layout:
  `BS2DCE slot0`, `BS9336 slot2`, `BS955A slot3`,
  `BSCCF4 slot5`, `BSDC91 slot7`, `BSF66F slot8`.
- It is close to, but still below, the 2026-05-12 reference
  `ge7=0.953277`, `ge8=0.626541`.

Current live state check:

- Master_Tag USB is correctly distinguishable as:
  `/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00`
- Master_Anchor USB is correctly distinguishable as:
  `/dev/serial/by-id/usb-Master_Anchor_BioSpur_BLE_Control_87EA2F4A526C5A02-if00`
- A 60 s broad `BS*` connection/listen window saw only five Tags:
  `BS2DCE`, `BSCCF4`, `BSF66F`, `BS9336`, and `BS955A`.
- `BSDC91` had no scan hit, connection event, CFG, TR, or TD output in that
  window.
- The 2026-06-26 13:26 good candidate raw log did contain `BSDC91` scan and
  connection evidence (`D5:53:48:EF:8F:59`), so the current absence is a live
  BLE visibility/power/advertising state difference, not a parser limitation.

Current gate:

- Do not enable RFD or RF diagnostics yet.
- Recover `BSDC91` BLE visibility first.
- Then run:

```bash
bash SS-TWR/alt-SS-TWR/broadcast/scripts/run_6tag_nordiag_baseline_candidate.sh
```

This script does not flash devices. It is a capture-only reproduction of the
best no-RFD 6 Tag baseline candidate and should fail the TDMA CFG gate if any
of the six Tags is absent.

## BLE Capacity / 5-Visible-Tag Reference-Roster Check - 2026-06-26 Afternoon

Question: whether the current BLE/TDMA path is fundamentally limited to
`5 x 10 Hz`, or whether the poor 60 Hz result is caused by firmware/runtime
state and TDMA layout details.

Established reference:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/six_tag_stable10x9_tr12_altanchor_capture120_20260512_161135_20260512_161135/
```

Reference facts:

- Six Tags were active at `10 Hz` each: `BSF66F, BS2DCE, BSDC91, BSCCF4,
  BS9336, BS955A`.
- Overall `ratio_ge7=0.953277`, `ratio_ge8=0.626541`.
- Therefore the system is not inherently limited to five 10 Hz Tag links.
- Current Master firmware configuration also supports more than five links:
  `CONFIG_BT_MAX_CONN=10`, `CONFIG_BT_MAX_PAIRED=10`,
  `MASTER_MAX_CONNECTIONS=10`, and BLE 2M PHY is enabled/requested
  (`BT_CONN_LE_PHY_PARAM_2M`, `CONFIG_BT_CTLR_PHY_2M=y`).

Current obstacle:

- `BSDC91` was not BLE-visible in repeated broad and targeted scans.
- A clean discovery found exactly five Tags:
  `BS2DCE, BSCCF4, BSF66F, BS9336, BS955A`.
- This is not evidence of a 5-link Master limit; it is evidence that this one
  Tag was absent from BLE discovery at the time.

Capture with the five visible Tags, no RFD, and an attempted six-Tag reference
roster:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/restore_5visible_ref6roster_noRFD_strict_120s_20260626_163955_20260626_163955/
```

Configuration:

- Capture targets: `BSF66F,BS2DCE,BSCCF4,BS9336,BS955A`.
- TDMA roster targets requested:
  `BSF66F,BS2DCE,BSDC91,BSCCF4,BS9336,BS955A`.
- `tr-hz=10`, `tag-cir=off`, `rfd_all=0`, `tr_diag_all=0`.
- Anchor responder preflight succeeded: `8/8`.
- TDMA CFG check matched all five visible targets.

Overall result:

- `tr_all=26784`
- `tr_valid_all=16047`
- `ratio_ge7=0.633513`
- `ratio_ge8=0.304062`

Per-Tag result:

| Tag | Valid rows | ge7 | ge8 | Status summary |
|---|---:|---:|---:|---|
| `BSF66F` | `5737/6344 = 0.904` | `0.964691` | `0.443884` | `O=5737, R=3, T=604` |
| `BS2DCE` | `1477/1712 = 0.863` | `0.897196` | `0.621495` | `O=1477, T=235` |
| `BSCCF4` | `2286/2600 = 0.879` | `0.923077` | `0.535385` | `O=2286, T=314` |
| `BS9336` | `717/9608 = 0.075` | `0.065779` | `0.054954` | `O=717, R=61, T=8830` |
| `BS955A` | `5830/6520 = 0.894` | `0.963190` | `0.359509` | `O=5830, R=3, T=687` |

Important interpretation:

- BLE capacity is not the limiting explanation. Four of the five visible Tags
  were healthy or near-healthy in this run.
- The run is dominated by `BS9336`, which failed across all anchors
  (`A-H` valid ratios only about `0.060-0.091`). This points to a current
  `BS9336` Tag/runtime/slot-state problem, not a global BLE throughput ceiling.
- The attempted offline `BSDC91` roster placeholder did not preserve the exact
  2026-05-12 slot layout. Current live slots remained compressed for the
  visible Tags (`BS955A` at slot 4, `BSCCF4` at slot 6), so a disconnected
  roster entry is not sufficient to reserve a physical TDMA slot in the current
  Master logic.
- RFD remains gated off. The immediate task is still restoring a no-RFD
  high-ge7 baseline before adding any diagnostic output path.

## 6-Tag Baseline Recovery Check - 2026-06-26 Afternoon

Goal: avoid misreading the current 5 Tag runtime state as a BLE capacity limit.

Current Master_Tag port:

```text
/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00
```

Passive 30 s TR listen after charger recovery saw five active Tags:

```text
BS2DCE: 61
BS9336: 303
BS955A: 302
BSCCF4: 241
BSF66F: 233
```

`BSDC91` was absent from TR output in that window.

Targeted recovery attempt:

- Sent `ota_target token -1`
- Sent `ota_target name BSDC91`
- Sent `ota_target prefix -`
- Sent `ota_target uuid -`
- Sent `conn`
- Listened for 45 s

Result:

- Master reported `conn_count=5`.
- No `BSDC91` scan hit, connection event, rejection event, `CFG_OK`, or TR line
  appeared during the targeted 45 s window.
- Runtime filter was restored afterward to broad `prefix=BS`.

Follow-up broad `BS*` recovery window:

- Reasserted `ota_target name -`, `ota_target prefix BS`, then `conn`.
- Listened for 90 s.
- TR counts:

```text
BS2DCE: 180
BS9336: 901
BS955A: 901
BSCCF4: 803
BSF66F: 543
```

- `BSDC91_HITS=0`.
- No scan/connection/rejection events involving `BSDC91` appeared.

Interpretation:

- This does **not** prove BLE can only support `5 x 10 Hz`.
- The 2026-05-12 reference already proved `6 x 10 Hz` with
  `ge7=0.953277`.
- The current blocker is that `BSDC91` is not BLE-visible to Master_Tag in the
  present runtime state.
- The next valid gate is to recover `BSDC91` visibility, then run a strict
  no-RFD 6 Tag / 10 Hz capture that requires all six Tags to reach CFG_OK.

## 5-Visible-Tag No-RFD Strict Capture - 2026-06-26 Afternoon

Purpose: characterize the currently visible Tags without redefining the final
goal away from 6 Tags.

Capture:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/restore_5visible_noRFD_strict_120s_20260626_162112_20260626_162113/
```

Configuration:

- Targets: `BSF66F,BS2DCE,BSCCF4,BS9336,BS955A`
- `tr-hz=10`
- `tag-cir=off`
- `rfd_all=0`
- `tr_diag_all=0`
- Anchor responder preflight: `8/8`
- TDMA CFG verify: `5/5`, strict match

Overall result:

- `success=true`
- `tr_all=29528`
- `tr_valid_all=17865`
- `ratio_ge7=0.537253`
- `ratio_ge8=0.311298`

Per Tag:

| Tag | Rows | Valid rows | ge7 | ge8 | Status summary |
|---|---:|---:|---:|---:|---|
| `BSF66F` | `9400` | `2802` | `0.264681` | `0.199149` | `O=2802, R=25, T=6573` |
| `BS2DCE` | `1784` | `1539` | `0.901345` | `0.591928` | `O=1539, T=245` |
| `BSCCF4` | `4152` | `3369` | `0.701349` | `0.271676` | `O=3369, R=27, T=756` |
| `BS9336` | `6920` | `4175` | `0.582659` | `0.460116` | `O=4175, R=15, T=2730` |
| `BS955A` | `7272` | `5980` | `0.663366` | `0.268427` | `O=5980, R=42, T=1250` |

Per-anchor valid ratios:

```text
BS2DCE  A 0.897  B 0.897  C 0.897  D 0.897  E 0.901  F 0.901  G 0.901  H 0.610
BS9336  A 0.761  B 0.632  C 0.593  D 0.591  E 0.588  F 0.590  G 0.587  H 0.484
BS955A  A 0.813  B 0.725  C 0.693  D 0.925  E 0.921  F 0.941  G 0.943  H 0.618
BSCCF4  A 0.900  B 0.738  C 0.697  D 0.873  E 0.871  F 0.925  G 0.915  H 0.572
BSF66F  A 0.508  B 0.300  C 0.270  D 0.273  E 0.275  F 0.269  G 0.269  H 0.221
```

Interpretation:

- Current no-RFD baseline is still far below the 2026-05-12 reference.
- The degradation is not caused by RFD or compact RF diagnostics output in this
  run, because both are zero.
- `BS2DCE` has fewer rows, but the sweeps it does produce are mostly good.
- `BSF66F` is the strongest per-link quality failure in this run despite many
  rows.
- `BSDC91` must still be recovered before a valid 6 Tag baseline claim can be
  made.

## BSDC91 Recovery Attempts - 2026-06-26 Late Afternoon

Master-side capacity check:

- Current source config supports more than five BLE peers:
  `CONFIG_BT_MAX_CONN=10`, `CONFIG_BT_MAX_PAIRED=10`, and
  `MASTER_MAX_CONNECTIONS=10`.
- The 2026-05-12 reference raw log contains `conn_count=6` and `BSDC91 CFG_OK`,
  so `5 x 10 Hz` is not a proven BLE capacity ceiling.

Master_Tag reset:

- Reset Master_Tag B120 SNR `1050070698` using SN-pinned J-Link reset only:
  `NRF5340_XXAA_NET`, then `NRF5340_XXAA_APP`.
- No Tag or Anchor body was flashed.
- After reset, broad `BS*` scan for 90 s still produced:

```text
BS2DCE: 171
BS9336: 884
BS955A: 599
BSCCF4: 249
BSF66F: 900
BSDC91_HITS: 0
```

Clean discovery:

- Set broad OTA target filter: `token=-1`, `name=-`, `prefix=BS`, `uuid=-`.
- Sent `device kind tag`, which disconnected the five existing peers.
- Reissued `conn`.
- Master immediately rediscovered and reconnected exactly five Tags:
  `BS2DCE`, `BSCCF4`, `BSF66F`, `BS9336`, and `BS955A`.
- Scan hits were observed for those five Tags only.
- No scan hit, connection event, rejection event, `CFG_OK`, or TR line for
  `BSDC91` appeared during the 120 s window.

Current safety state after the clean discovery test:

- Sent `cmd_all MODE AOTA`.
- Observed `MODE_OK MODE=AOTA LIVE=1` from all five connected Tags.
- Sent `tdma hold 1`, acknowledged with `tdma hold rc=0 hold=1`.

Conclusion:

- `BSDC91` is not currently BLE-visible to Master_Tag.
- The failure is not explained by a five-connection Master limit or by stale
  resident links occupying all available slots.
- The valid next action is physical recovery of `BSDC91` power/advertising, or
  explicit authorization for a named direct Tag recovery path if the correct
  probe/SNR is identified.

## 6-Tag Baseline Recovery Status - 2026-06-26 12:16

Goal: restore the 6 Tag / 60 Hz no-RFD baseline before adding any RF diagnostic
output back into the stream.

Current hard rule:

- Priority 1: keep high 7/8 and 8/8 anchor success at 10 Hz per Tag.
- Priority 2: RF diagnostics may only use paths that do not degrade normal TR
  ranging output.

Reference baseline:

- `six_tag_stable10x9_tr12_altanchor_capture120_20260512_161135_20260512_161135`
- `tr_all=48400`, `tr_valid_all=40124`
- overall `ge7=0.953277`, `ge8=0.626541`
- canonical TDMA parameters are `10/9`, not `40/24`.

Restored/deployed images:

- Master_Tag carrier:
  `build-master-control-b120-m1-master-tag-lfrc-stable10x9-tr12-bdbs-embedded-20260512`
- Tag OTA payload:
  `build-tag-stable10x9-tr12-bdbs-20260512/dfu_application.zip`
- Tag marker:
  `stable10x9-tr12-bdbs-20260512`

Important diagnosis:

- RFD was not the root cause of the current failure. The capture below has
  `rfd_all=0` and `tr_diag_all=0`.
- For Tags that are actually connected to Master_Tag, UWB ranging is healthy.
- The remaining failure is BLE resident-link/admission/visibility for the three
  Wand Tags, not an anchor ranging failure and not an RFD parser/output issue.

120 s capture with six requested targets, no RFD:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_six_targets_roster6_actual3_nordiag_120s_20260626_121650_20260626_121650/
```

Overall:

- `success=false` only because TDMA CFG is missing for the disconnected Wand
  Tags.
- `tr_all=9248`
- `tr_valid_all=8309`
- `rfd_all=0`
- `tr_diag_all=0`
- overall among active rows: `ge7=0.936851`, `ge8=0.608997`

Per Tag:

| Tag | Rows | Valid rows | ge7 | ge8 | Note |
|---|---:|---:|---:|---:|---|
| `BSF66F` | `3032` | `2749` | `0.934037` | `0.683377` | connected, healthy |
| `BS2DCE` | `2904` | `2588` | `0.931129` | `0.578512` | connected, healthy |
| `BSDC91` | `3312` | `2972` | `0.944444` | `0.567633` | connected, healthy |
| `BSCCF4` | `0` | `0` | `0` | `0` | no resident link / no CFG |
| `BS9336` | `0` | `0` | `0` | `0` | no resident link / no CFG |
| `BS955A` | `0` | `0` | `0` | `0` | no resident link / no CFG |

Master_Tag state and connection evidence:

- TDMA roster contains all six requested BS codes.
- Current `tdma show` reports all six profiles at `motion target=10Hz`.
- `BSF66F`, `BS2DCE`, and `BSDC91` produce continuous TR output.
- `BSCCF4`, `BS9336`, and `BS955A` have no `CFG_OK` and no TR output.
- A 60 s `conn` refill attempt with `prefix=BS` and all six roster entries
  found no Wand traffic:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/master_conn_refill_wand_20260626_1224.log
```

Refill count:

```text
BSCCF4: 0
BS9336: 0
BS955A: 0
BSF66F: 311
BS2DCE: 295
BSDC91: 322
```

Conclusion:

- The software/RFD question is no longer the active blocker.
- The 3 connected Tags already meet the high-ge7 requirement with no RFD.
- The full 6 Tag goal requires recovering the three Wand Tags as BLE-visible
  resident links first. Their documented J-Link SNRs are not present in the
  current `/dev/serial/by-id` list, so they could not be safely reset from the
  workstation in this run.

Next gate:

1. Physically power-cycle or reconnect the three Wand Tags:
   `BSCCF4`, `BS9336`, `BS955A`.
2. Confirm Master_Tag sees them without using disruptive bare `scan`.
3. Re-run the same no-RFD 120 s capture.
4. Only after the 6 Tag no-RFD baseline returns to about `ge7 >= 0.90`, start
   testing diagnostic output paths again.

## 60 Hz No-RFD Baseline Re-check - 2026-06-26

Objective: determine whether the low 6 Tag / 10 Hz success rate is caused by
RFD output load or by a more basic ranging/runtime regression.

System floor:

- First priority: preserve high 7/8 and 8/8 anchor success at 60 Hz total
  output.
- RF diagnostics may only use paths that do not damage normal ranging output.

Historical reference:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/six_tag_stable10x9_tr12_altanchor_capture120_20260512_161135_20260512_161135/
```

Reference result:

- Actual TDMA parameters: `period_ms=10`, `active_ms=9`.
- `tr_all=48400`, `tr_valid_all=40124`.
- `ratio_ge7=0.953277`, `ratio_ge8=0.626541`.

Conclusion from the historical run:

- The correct high-capacity profile is `10/9`, not `40/24`.
- `40/24` is not the right target for 6 Tag / 10 Hz baseline reproduction.

Current no-RFD tests:

| Run | Anchor/runtime state | RFD/TR diag | ge7 | ge8 | Key result |
|---|---|---:|---:|---:|---|
| `capture_six_tags_60hz_a27_nodiag_120s_20260626_004530` | A27 no-RFD | `0/0` | `0.453920` | `0.289928` | no-RFD still poor |
| `capture_six_tags_60hz_stable10x9_repro_nodiag_120s_20260626_011117` | stable10x9 repro no-RFD | `0/0` | `0.599509` | `0.440418` | no-RFD still poor |
| `capture_six_tags_60hz_A18_restore_skippreflight_nodiag_120s_20260626_063814` | A18 uploaded but responder runtime not verified | `0/0` | `0.000000` | `0.000000` | invalid: all rows timeout |
| `capture_six_tags_60hz_A18_responder_ok_nodiag_120s_20260626_064409` | A18 responder runtime verified `8/8` | `0/0` | `0.563197` | `0.476921` | valid A18 no-RFD baseline, still poor |

A18 rollback details:

- Master_Anchor protected flash was explicitly authorized by the operator.
- Master_Anchor carrier:
  `build-master-control-b120-m1-master-anchor-lfrc-anchoronly-altbcast-embed-altbcast-responder-a18-g1200-r1000-20260512_154806/zephyr/merged_domains.hex`
- Anchor OTA payload:
  `build-anchor-unified-ota-altbcast-responder-a18-g1200-r1000-20260512_154806/dfu_application.zip`
- A-H OTA stage logs observed `ota_success_observed`.
- Initial post-verify hung because the Master_Anchor control-plane gate was
  stuck; this produced the invalid all-timeout capture above.
- Resetting Master_Anchor SNR `960148546` restored the anchor control plane.
- Runtime responder verification then succeeded:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/a18_responder_runtime_verify_after_reset_20260626_064310/
```

Responder verification evidence:

- `success=true`
- `anchor role all responder runtime sent=8 ready=8/8`
- `anchor role all responder runtime final sent=8 ready=8/8 total_sent=24`
- all eight anchors notified `OK RUNTIME_RESTART_REQUESTED`

Valid A18 no-RFD 120 s capture:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_six_tags_60hz_A18_responder_ok_nodiag_120s_20260626_064409_20260626_064409/
```

Overall:

- `tr_all=51648`
- `tr_valid_all=33898`
- `tr_diag_all=0`
- `rfd_all=0`
- `ratio_ge7=0.563197`
- `ratio_ge8=0.476921`

Per-Tag:

| Tag | Valid rows | ge7 | ge8 | Status summary |
|---|---:|---:|---:|---|
| `BSF66F` | `6255/8688 = 0.720` | `0.446593` | `0.183241` | `O=6255, R=131, T=2302` |
| `BS2DCE` | `2157/8792 = 0.245` | `0.010919` | `0.008189` | `O=2157, R=111, T=6524` |
| `BSDC91` | `7862/8456 = 0.930` | `0.933775` | `0.854305` | `O=7862, R=1, T=593` |
| `BS9336` | `8168/8416 = 0.971` | `0.977186` | `0.923954` | `O=8168, T=248` |
| `BS955A` | `8322/8632 = 0.964` | `0.974050` | `0.872104` | `O=8322, T=310` |
| `BSCCF4` | `1134/8664 = 0.131` | `0.067405` | `0.050785` | `O=1134, R=35, T=7495` |

Interpretation:

- RFD output is not the root cause of the current 60 Hz failure. Multiple
  no-RFD runs still fail to reproduce the 2026-05-12 `ge7=0.953277` baseline.
- A18 rollback alone does not restore the historical baseline once responder
  runtime is correctly verified.
- The current loss is dominated by Tag/link behavior, especially `BS2DCE` and
  `BSCCF4` in the final A18 test. Three Tags in the same run are healthy
  (`BSDC91`, `BS9336`, `BS955A`), so the failure is not a global parser,
  scheduler, or RFD-output problem.
- Do not re-enable any RFD output path until a no-RFD 6x10 Hz baseline again
  reaches the system floor. The current accepted baseline for further debugging
  is no-RFD, `10/9`, responder runtime explicitly verified `8/8`.

## ROTO v4tr OTA + 3-Tag Static Pressure Capture - 2026-06-26 00:24

Goal: update the two ROTO Tags to the current safe Tag firmware and run a
120 s static pressure capture with `BSF66F` plus the ROTO Tags.

ROTO Tags:

- `BS2DCE`
- `BSDC91`

Tag OTA payload used:

```text
tag-rfdiag-v4tr-g1200-r1000
```

OTA evidence:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/tag_ota_roto_v4tr_20260626_001737/
```

OTA results:

- `BS2DCE`: OTA success, post `VERSION` matched
  `tag-rfdiag-v4tr-g1200-r1000`.
- `BSDC91`: OTA success, post `VERSION` matched
  `tag-rfdiag-v4tr-g1200-r1000`.

120 s static pressure capture:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_roto2_static120_v4tr_A26_20260626_002045_20260626_002045/
```

Capture configuration:

- Targets: `BSF66F,BS2DCE,BSDC91`.
- `tr-hz=10`, `tag-cir=off`.
- No listener path used.
- Anchor responder preflight succeeded: `sent=8`, `ready=8/8`.
- TDMA config matched all three targets at 10 Hz.
- Cleanup succeeded; Tags were returned to quiet/IDLE state.

Overall result:

- `success=true`
- `controller_lost=false`
- `no_tr_timeout=false`
- `tdma_config_failed=false`
- `tr_all=26352`
- `tr_valid_all=17395`
- `rfd_all=0`
- `rfd_joined_all=26352`
- `ratio_ge7=0.624165`
- `ratio_ge8=0.428355`

Per-Tag result:

| Tag | Valid rows | ge7 | ge8 | Status summary |
|---|---:|---:|---:|---|
| `BSF66F` | `7795/8864 = 0.879` | `0.800542` | `0.393502` | `O=7795, R=60, T=1009` |
| `BS2DCE` | `1117/8624 = 0.130` | `0.083488` | `0.069573` | `O=1117, R=40, T=7467` |
| `BSDC91` | `8483/8864 = 0.957` | `0.973827` | `0.812274` | `O=8483, T=381` |

Per-anchor diagnosis:

- `BSDC91` is healthy; H/7 is the weakest link but still usable
  (`913/1108 = 0.824`), all other anchors are around `0.973-0.978`.
- `BSF66F` is medium in this run; B/C/H are weaker
  (`B=827/1108 = 0.746`, `C=829/1108 = 0.748`,
  `H=847/1108 = 0.764`), while F/G stay high.
- `BS2DCE` is the dominant failure source; all links are poor, especially
  C-H (`0.071-0.083`) and B (`0.171`). This points to a `BS2DCE`
  Tag/placement/power/RF-state issue rather than a global scheduler failure.

State restored after OTA:

- Active generated OTA payload was restored to Anchor A26:
  `alt-bcast-a26-delayed-prof-g1200`.
- `verify_ota_payload_kind.py --expected anchor` passed.

## Latest Gate - 2026-06-26 Evening

Current live check:

- Master_Tag is visible as
  `/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00`.
- Master_Anchor is visible as
  `/dev/serial/by-id/usb-Master_Anchor_BioSpur_BLE_Control_87EA2F4A526C5A02-if00`.
- A 60 s broad `BS*` connect/listen window saw only five Tags:
  `BS2DCE`, `BSCCF4`, `BSF66F`, `BS9336`, `BS955A`.
- `BSDC91` was not seen in scan/connect/CFG/TR/TD output.

Best no-RFD candidate remains:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_six_targets_bleint12_nopreflight_nordiag_120s_20260626_132626_20260626_132626/
```

It reached all six Tags with `rfd_all=0`, `tr_diag_all=0`,
`ge7=0.939401`, and `ge8=0.421001`.

Next action after `BSDC91` is physically/BLE recovered:

```bash
bash SS-TWR/alt-SS-TWR/broadcast/scripts/run_6tag_nordiag_baseline_candidate.sh
```

Do not re-enable RFD/RF diagnostics before this no-RFD six-Tag gate passes.

### Gate Execution Result

Command:

```bash
VISIBILITY_S=45 bash scripts/run_6tag_nordiag_baseline_candidate.sh
```

Result:

```text
[6TAG-GATE] seen=BS2DCE,BS9336,BS955A,BSCCF4,BSF66F
[6TAG-GATE] missing=BSDC91
[6TAG-GATE] aborting before capture; recover missing Tag BLE visibility first
```

Interpretation:

- The script correctly refused to run a 120 s baseline capture with only five
  visible Tags.
- The current remaining blocker for the baseline gate is `BSDC91` BLE
  visibility, not RFD output and not a five-connection BLE ceiling.
