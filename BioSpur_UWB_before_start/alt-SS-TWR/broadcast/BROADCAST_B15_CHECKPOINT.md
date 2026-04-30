# Broadcast b15 Checkpoint - Single-Window Collector

Date: 2026-04-29

## Build

- Tag marker: `alt-bcast-b15-collector-g2400-r1000`
- Current anchors stayed on `alt-bcast-a2-g2400-r1000-coop1`.
- No anchor image changes were made for b15.
- b15 Tag RX changed the broadcast response path from per-slot delayed RX to one continuous collector window:
  - no `DWT_START_RX_DELAYED`
  - no RX auto-reenable
  - no double buffer
  - manual immediate RX reenable after each received frame

Current deployed guard is `2400us` because A-H responder serial logs show response slots at
`2400, 3400, 4400, 5400us`.

## OTA

All three tags matched b15:

- BSF66F: `match=True`
- BS2DCE: `match=True`
- BSDC91: `match=True`

## Captures

### 1 Tag Calibration

Directory:

`logs/alt_bcast_b15_collector_BSF66F_capture_20260429_231916`

- `CM ok=504/529`
- A-H were all seen by the Tag.
- `CF first_to_last_us=0` for all rows.
- `poll_count=4`
- `frame_us median=23132us`

This confirms b15 fixed the previous rank0-only problem.

### 1 Tag Motion Positioning

Directory:

`logs/alt_bcast_b15_collector_BSF66F_motion_capture_20260429_232202`

- `positions_all=158`
- BSF66F median RMS: `109mm`
- BSF66F p95 RMS: `137mm`
- Main anchor set: `ABEF`

### 3 Tag Calibration

Directory:

`logs/alt_bcast_b15_collector_3tag_cal_capture_20260429_232655`

- `cm_all=3301`
- `cf_all=1069`
- `CF first_to_last_us=0` for all tags.
- Listener saw both broadcast polls and anchor responses:
  - `uf_rows=730`
  - `ul_rows=58`

Per-tag CM:

- BSF66F: `ok=573`, `timeout=178`
- BS2DCE: `ok=612`, `timeout=833`, `reject=13`
- BSDC91: `ok=938`, `timeout=151`, `reject=3`

BS2DCE has much higher timeout rate in mixed cal/roto profile and needs follow-up.

### 3 Tag Motion Positioning

Directory:

`logs/alt_bcast_b15_collector_3tag_motion_capture_20260429_232842`

- `positions_all=537`
- BSF66F: `184` positions, median RMS `106mm`, p95 RMS `129mm`
- BS2DCE: `180` positions, median RMS `41mm`, p95 RMS `150mm`
- BSDC91: `173` positions, median RMS `40mm`, p95 RMS `169mm`

Main anchor set for all three tags was `ABEF`.

## Current Status

b15 is the first broadcast branch version that works end-to-end:

- Broadcast poll side remains compressed: `first_to_last_us=0`.
- Anchor responses are received by the Tag beyond rank0.
- Single Tag positioning works.
- Three Tag motion positioning works.

Remaining issues:

- Motion positioning has occasional outliers, especially one BSDC91 max-RMS spike.
- Listener still sees far fewer responses than the Tag, so listener parser/capture sensitivity is useful for air evidence but should not be treated as the primary success metric.

## Overnight BS2DCE Isolation Retest

Date: 2026-04-30

Directory:

`logs/overnight_b15_diag_20260429_234317`

This was a capture-only retest. No firmware, anchor, OTA, or flash changes were made.

Per-test CM ok rates:

- Single BSF66F static: `1713/1800 = 95.2%`
- Single BS2DCE roto: `1918/2071 = 92.6%`
- Single BSDC91 roto: `1897/2034 = 93.3%`
- BSF66F + BS2DCE: BSF66F `94.2%`, BS2DCE `94.4%`
- BSF66F + BSDC91: BSF66F `94.5%`, BSDC91 `93.5%`
- BS2DCE + BSDC91: BS2DCE `93.8%`, BSDC91 `94.0%`
- Three-tag mixed cal long: BSF66F `94.8%`, BS2DCE `94.9%`, BSDC91 `94.0%`

Three-tag mixed cal long:

- `cm_all=14377`
- `cf_all=4764`
- `CF first_to_last_us=0` for all tags
- `CF solve_reason`: `success=3056`, `pending=1708`

Conclusion:

- The earlier high BS2DCE timeout rate in `logs/alt_bcast_b15_collector_3tag_cal_capture_20260429_232655` did not reproduce.
- BS2DCE is not a single-tag failure.
- BS2DCE also stayed stable in all two-tag combinations.
- b15 can continue to three-tag motion positioning validation.

## Three-Tag Motion Validation

Date: 2026-04-30

Directory:

`logs/alt_bcast_b15_collector_3tag_motion_capture_20260430_001501`

Capture:

- Duration: `180s`
- Profiles: BSF66F `motion`, BS2DCE `motion`, BSDC91 `motion`
- `positions_all=2379`
- `cm_all=0`, `cf_all=0` because motion profile emits TS positions, not calibration CM/CF rows.
- Listener saw mostly broadcast polls and only a few decoded responses:
  - `UF=2667`
  - `UL=10`

Per-tag TS output:

- BSF66F: `816` positions, RMS median `164mm`, p95 `184mm`, max `207mm`, main anchors `ABEF`
- BS2DCE: `779` positions, RMS median `37mm`, p95 `156mm`, p99 `293mm`, one large outlier, main anchors `ABEF`
- BSDC91: `784` positions, RMS median `38mm`, p95 `194mm`, p99 `1354mm`, nine `>1000mm` outliers, main anchors `ABEF`

Conclusion:

- b15 three-tag motion is running end-to-end and producing stable TS position output.
- BS2DCE is healthy in motion mode as well.
- The next quality issue is not basic ranging/link failure; it is outlier suppression / motion continuity on roto tags, especially BSDC91.

## b16 Candidate - Output Filter

Date: 2026-04-30

Implemented but not deployed in this checkpoint.

Build:

- Tag marker: `alt-bcast-b16-filter-g2400-r1000`
- Build directory: `build-alt-bcast-b16-filter-tag-g2400-r1000-raw0-cont0`
- OTA payload: `build-alt-bcast-b16-filter-tag-g2400-r1000-raw0-cont0/dfu_application.zip`
- Build parameters confirmed:
  - `APP_ALT_SS_TWR_ENABLE=1`
  - `APP_ALT_SS_TWR_BCAST_ENABLE=1`
  - `APP_ALT_SS_TWR_GUARD_US=2400`
  - `APP_ALT_SS_TWR_RESP_SPACING_US=1000`
  - `APP_TAG_OUTPUT_FILTER_RMS_MM=500`
  - `APP_TAG_OUTPUT_FILTER_SPEED_MM_S=5000`

Behavior:

- Accepted positions still emit the existing `TS;...` record, so host position parsing remains compatible.
- Rejected solved positions emit `TF;...` with `filter_reason`, `step_mm`, `motion_dt_ms`, and `speed_mm_s`.
- `run_recv_tdma_capture.py` now writes rejected output-filter rows to `tf_all.csv`.
- The filter does not change Anchor code, OTA logic, PHY, or broadcast timing.

## b18 Candidate - b15 Baseline + RMS500 Filter

Date: 2026-04-30

Important correction:

- b16/b17 accidentally drifted from the proven b15 Tag build parameters.
- b15 used `APP_TAG_RANGE_CONTINUITY_ENABLE=0` and `APP_TAG_RANGE_FILTER_OUTLIER_MM=120000`.
- b16/b17 had fallen back to script defaults `continuity=1` and `range_outlier=450`, which suppressed many Roto solves before the new TS/TF output filter layer.
- `scripts/build_tag_ble_motion.sh` default values were corrected back to the b15 raw0/cont0 baseline.

Build/deploy:

- Tag marker: `alt-bcast-b18-rms500-raw0-cont0-g2400-r1000`
- Tag build: `build-alt-bcast-b18-rms500-tag-g2400-r1000-raw0-cont0`
- Cache verified:
  - `APP_TAG_RANGE_CONTINUITY_ENABLE=0`
  - `APP_TAG_RANGE_FILTER_OUTLIER_MM=120000`
  - `APP_TAG_OUTPUT_FILTER_RMS_MM=500`
  - `APP_TAG_OUTPUT_FILTER_SPEED_MM_S=0`
- Master_Tag carrier: `build-master-control-b120-m1-master-tag-lfrc-alt-bcast-b18-rms500-raw0-cont0-g2400-r1000-carrier`
- Master_Tag LFRC assert passed.
- OTA BSF66F/BS2DCE/BSDC91 succeeded; all post VERSION matched b18.

Three-tag motion capture:

- Directory: `logs/alt_bcast_b18_rms500_3tag_motion_capture_20260430_113339`
- Duration: `180s`
- `positions_all=1353`
- `tf_all=1`
- Per-tag positions:
  - BSF66F: `445`
  - BS2DCE: `455`
  - BSDC91: `453`
- RMS:
  - BSF66F median `12mm`, p95 `37mm`, p99 `65mm`, max `99mm`
  - BS2DCE median `18mm`, p95 `128mm`, p99 `199mm`, max `321mm`
  - BSDC91 median `15mm`, p95 `91mm`, p99 `187mm`, max `243mm`
- Filtered row:
  - BSDC91 one `rms` reject at `552mm`, anchors `ADEH`

Conclusion:

- b18 restored the balanced three-tag behavior lost in b16/b17.
- The RMS500 single-frame gate is not falsely rejecting normal points in this run.
- Current stable broadcast baseline for continued work is b18, not b16/b17.

## b19 Candidate - Guard 800 + Speed5000

Date: 2026-04-30

Purpose:

- Change only the broadcast guard target from b18 `2400us` to `800us`.
- Keep response spacing at `1000us`.
- Keep raw0/cont0 baseline.
- Enable the requested output speed gate at `5000mm/s`.

Build/deploy:

- Tag marker: `alt-bcast-b19-rms500-raw0-cont0-g800-r1000`
- Tag build: `build-alt-bcast-b19-rms500-tag-g800-r1000-raw0-cont0`
- Cache verified:
  - `APP_ALT_SS_TWR_GUARD_US=800`
  - `APP_ALT_SS_TWR_RESP_SPACING_US=1000`
  - `APP_TAG_OUTPUT_FILTER_RMS_MM=500`
  - `APP_TAG_OUTPUT_FILTER_SPEED_MM_S=5000`
  - `APP_TAG_RANGE_CONTINUITY_ENABLE=0`
  - `APP_TAG_RANGE_FILTER_OUTLIER_MM=120000`
- Master_Tag carrier: `build-master-control-b120-m1-master-tag-lfrc-alt-bcast-b19-rms500-raw0-cont0-g800-r1000-carrier`
- Master_Tag LFRC assert passed.
- OTA BSF66F/BS2DCE/BSDC91 succeeded.
- Post VERSION:
  - BSF66F matched b19.
  - BSDC91 matched b19.
  - BS2DCE was missed by the first post query, then matched b19 on immediate retry in `logs/manual_b19_version_retry_BS2DCE_20260430_121104`.

Three-tag motion capture:

- Directory: `logs/alt_bcast_b19_rms500_g800_3tag_motion_capture_20260430_121147`
- Duration: `180s`
- `positions_all=1055`
- `tf_all=255`
- `tf_all` reason: all `speed`
- Per-tag accepted positions:
  - BSF66F: `408`
  - BS2DCE: `329`
  - BSDC91: `318`
- Per-tag filtered positions:
  - BSF66F: `19`
  - BS2DCE: `117`
  - BSDC91: `119`
- Accepted RMS:
  - BSF66F median `11mm`, p95 `63mm`, p99 `107mm`, max `166mm`
  - BS2DCE median `17mm`, p95 `121mm`, p99 `193mm`, max `291mm`
  - BSDC91 median `12mm`, p95 `82mm`, p99 `148mm`, max `281mm`
- Filtered RMS:
  - BSF66F median `6mm`, max `22mm`
  - BS2DCE median `26mm`, max `327mm`
  - BSDC91 median `20mm`, max `259mm`
- Listener:
  - `UF=1490`
  - `UL=138`
  - Broadcast poll sources: `0xb101`, `0xb102`, `0xb103`
  - Listener response anchors: mainly `G=68`, `H=60`, `A=9`, `B=1`

Conclusion:

- b19 as requested does not pass the `positions_all >= 1300` criterion: it produced `1055`.
- The primary loss is the `5000mm/s` output speed gate, not bad RMS. All `255` rejected rows were `speed` rejects, and many rejected rows had very low RMS.
- The accepted b19 solves are still high quality, so `g800/r1000` remains a plausible timing target.
- Next clean test should isolate guard from output filtering: either rerun `g800/r1000` with `APP_TAG_OUTPUT_FILTER_SPEED_MM_S=0`, or follow the fallback path and test `g1200/r1000` with the same filter policy chosen for b18.

## b20 Candidate - Guard 1200 + Speed5000

Date: 2026-04-30

Purpose:

- Fallback after b19 missed the `positions_all >= 1300` criterion.
- Change guard from `800us` to `1200us`.
- Keep response spacing at `1000us`.
- Keep raw0/cont0 baseline and the requested `5000mm/s` speed gate.

Build/deploy:

- Tag marker: `alt-bcast-b20-rms500-raw0-cont0-g1200-r1000`
- Tag build: `build-alt-bcast-b20-rms500-tag-g1200-r1000-raw0-cont0`
- Cache verified:
  - `APP_ALT_SS_TWR_GUARD_US=1200`
  - `APP_ALT_SS_TWR_RESP_SPACING_US=1000`
  - `APP_TAG_OUTPUT_FILTER_RMS_MM=500`
  - `APP_TAG_OUTPUT_FILTER_SPEED_MM_S=5000`
  - `APP_TAG_RANGE_CONTINUITY_ENABLE=0`
  - `APP_TAG_RANGE_FILTER_OUTLIER_MM=120000`
- Master_Tag carrier: `build-master-control-b120-m1-master-tag-lfrc-alt-bcast-b20-rms500-raw0-cont0-g1200-r1000-carrier`
- Master_Tag LFRC assert passed.
- OTA BSF66F/BS2DCE/BSDC91 succeeded; all post VERSION matched b20.

Three-tag motion capture:

- Directory: `logs/alt_bcast_b20_rms500_g1200_3tag_motion_capture_20260430_122357`
- Duration: `180s`
- `positions_all=1011`
- `tf_all=282`
- `tf_all` reasons:
  - `speed=280`
  - `rms=2`
- Per-tag accepted positions:
  - BSF66F: `379`
  - BS2DCE: `325`
  - BSDC91: `307`
- Per-tag filtered positions:
  - BSF66F: `27`
  - BS2DCE: `121`
  - BSDC91: `134`
- Accepted RMS:
  - BSF66F median `9mm`, p95 `37mm`, p99 `75mm`, max `120mm`
  - BS2DCE median `15mm`, p95 `102mm`, p99 `155mm`, max `187mm`
  - BSDC91 median `12mm`, p95 `97mm`, p99 `175mm`, max `354mm`
- Filtered RMS:
  - BSF66F median `9mm`, max includes two RMS rejects
  - BS2DCE median `23mm`, p95 `126mm`, max `269mm`
  - BSDC91 median `14mm`, p95 `122mm`, max `371mm`
- Listener:
  - `UF=1500`
  - `UL=140`
  - Broadcast poll sources: `0xb101`, `0xb102`, `0xb103`
  - Listener response anchors: mainly `H=73`, `G=62`

Conclusion:

- b20 also misses the `positions_all >= 1300` criterion: it produced `1011`.
- Raising guard from `800us` to `1200us` did not recover b18-level position rate.
- The loss again comes from the `5000mm/s` speed gate. Most rejected rows have good RMS and are therefore likely false positives from anchor-set changes / solve jumps rather than bad ranging.
- Current best conclusion: do not judge guard compression with speed gate enabled. Re-test `g800/r1000` with the b18 filter policy (`APP_TAG_OUTPUT_FILTER_SPEED_MM_S=0`) before rejecting g800.

## b21 Candidate - Guard 800 + Speed0

Date: 2026-04-30

Purpose:

- Re-test `g800/r1000` with the b18 output filter policy.
- Keep only RMS gate enabled.
- Disable speed gate so guard compression is not confounded by continuity false positives.

Build/deploy:

- Tag marker: `alt-bcast-b21-rms500-speed0-raw0-cont0-g800-r1000`
- Tag build: `build-alt-bcast-b21-rms500-speed0-tag-g800-r1000-raw0-cont0`
- Cache verified:
  - `APP_ALT_SS_TWR_GUARD_US=800`
  - `APP_ALT_SS_TWR_RESP_SPACING_US=1000`
  - `APP_TAG_OUTPUT_FILTER_RMS_MM=500`
  - `APP_TAG_OUTPUT_FILTER_SPEED_MM_S=0`
  - `APP_TAG_RANGE_CONTINUITY_ENABLE=0`
  - `APP_TAG_RANGE_FILTER_OUTLIER_MM=120000`
- Master_Tag carrier: `build-master-control-b120-m1-master-tag-lfrc-alt-bcast-b21-rms500-speed0-raw0-cont0-g800-r1000-carrier`
- Master_Tag LFRC assert passed.
- OTA BSF66F/BS2DCE/BSDC91 succeeded; all post VERSION matched b21.

Three-tag motion capture:

- Directory: `logs/alt_bcast_b21_rms500_speed0_g800_3tag_motion_capture_20260430_124214`
- Duration: `180s`
- `positions_all=1327`
- `tf_all=0`
- Per-tag positions:
  - BSF66F: `441`
  - BS2DCE: `435`
  - BSDC91: `451`
- RMS:
  - BSF66F median `10mm`, p95 `47mm`, p99 `92mm`, max `193mm`
  - BS2DCE median `17mm`, p95 `127mm`, p99 `259mm`, max `296mm`
  - BSDC91 median `15mm`, p95 `130mm`, p99 `206mm`, max `280mm`
- Listener:
  - `UF=1535`
  - `UL=147`
  - Broadcast poll sources: `0xb101`, `0xb102`, `0xb103`
  - Listener response anchors: mainly `H=73`, `G=70`

Conclusion:

- b21 passes the `positions_all >= 1300` criterion with `1327` positions.
- Per-tag output is balanced: `441/435/451`.
- There are no RMS rejects in this run.
- Tag-side collector guard compression from b18 `g2400/r1000` to b21 `g800/r1000` is accepted for this three-tag motion test.
- Important correction: at this checkpoint Anchors were still on `alt-bcast-a2-g2400-r1000-coop1`, so this did not yet prove the physical Anchor delayed-TX guard had moved to `800us`.
- Speed gate should stay disabled until AutoPos/layout calibration is refreshed and a new physically meaningful threshold is derived.

## a3 Anchor Guard 800 + b21 Tag

Date: 2026-04-30

Purpose:

- Move the actual Anchor delayed-TX guard from `2400us` to `800us`.
- Keep Tag firmware on the known-good b21 collector/filter baseline.
- Verify three-tag motion still produces stable positioning.

Build/deploy:

- Anchor marker: `alt-bcast-a3-g800-r1000-coop1`
- Anchor build: `build-anchor-unified-ota-alt-bcast-a3-g800-r1000-coop1`
- Anchor cache verified:
  - `APP_ALT_SS_TWR_GUARD_US=800`
  - `APP_ALT_SS_TWR_RESP_SPACING_US=1000`
  - `APP_ANCHOR_RESPONDER_COOP_SLEEP_MS=1`
  - `APP_UWB_HW_FRAME_FILTER_ENABLE=1`
  - `APP_ANCHOR_FW_MARKER=alt-bcast-a3-g800-r1000-coop1`
- Payload guard verified as `anchor` / `alt-bcast-a3-g800-r1000-coop1`.
- Master_Anchor carrier: `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-a3-g800-r1000-coop1-carrier`
- Master_Anchor LFRC assert passed and was flashed to B120 SNR `960148546`.
- A-H Anchor OTA directory: `logs/alt_bcast_a3_g800_anchor_ota_20260430_125207`
- A-H OTA upload result: `8/8` success, all first attempt.
- Post responder runtime immediately after OTA: `sent=8 ready=8/8`.
- Post VERSION readback did not produce marker lines because the Master_Anchor CDC/control path became write-timeout after handoff/reset. This is recorded as a control-plane readback issue, not as an OTA upload failure.

Three-tag motion capture:

- Directory: `logs/alt_bcast_b21_tag_a3_anchor_g800_3tag_motion_capture_20260430_130955`
- Duration: `180s`
- Tag firmware: b21 `alt-bcast-b21-rms500-speed0-raw0-cont0-g800-r1000`
- Anchor firmware intended/deployed by OTA: a3 `alt-bcast-a3-g800-r1000-coop1`
- `positions_all=2019`
- `tf_all=0`
- Per-tag positions:
  - BSF66F: `666`
  - BS2DCE: `675`
  - BSDC91: `678`
- RMS:
  - BSF66F median `20.5mm`, p95 `165mm`, max `289mm`
  - BS2DCE median `34mm`, p95 `165mm`, max `279mm`
  - BSDC91 median `27mm`, p95 `158mm`, max `265mm`
- Listener:
  - `UF=2171`
  - `UL=60`
  - Broadcast poll sources: `0xb101`, `0xb102`, `0xb103`
  - Listener response anchors observed: `H=41`, `E=7`, `B=6`, `C=3`, `G=2`, `D=1`

Conclusion:

- b21 Tag + a3 Anchor passes the three-tag motion test strongly: `2019` positions, balanced `666/675/678`, and no RMS rejects.
- This is the best broadcast branch run so far by position count and per-tag balance.
- The a3 image build itself is confirmed to contain `guard=800`, `resp_spacing=1000`, and the `alt-bcast-a3-g800-r1000-coop1` marker.
- Remaining gap: Anchor post-VERSION marker readback needs a Master_Anchor CDC/control-plane fix or a better-timed query. UWB behavior is good enough to continue protocol work, but marker readback is not yet clean for a3.

## b25-b29 Rank0 Diagnosis and g2000 Stabilization

Date: 2026-04-30

Problem:

- b25/b26 with 4-anchor masks showed `track;4;4 = 0`.
- Changing the mask changed which anchor was rank0, but rank0 was still missing.
- This ruled out a specific bad anchor and pointed at timing around the first response slot.

Key diagnostic results:

- b27 added low-rate Tag RXG timing:
  - `txdone_to_rxstart_us` median about `732us`, p95 about `854us`.
  - `rxenable_us` median about `122us`.
- b28 tried `DWT_RESPONSE_EXPECTED` / WAIT4RESP and was worse.
- b28 listener still did not see rank0 A on air in the ABCE mask.
- Conclusion: this was not only Tag parser/RX timing. The first Anchor delayed-TX slot was too early for the current Anchor responder path.

Stabilization build/deploy:

- Anchor marker: `alt-bcast-a5-g2000-r1000-coop1`
- Anchor build: `build-anchor-unified-ota-alt-bcast-a5-g2000-r1000-coop1`
- Anchor parameters:
  - `APP_ALT_SS_TWR_GUARD_US=2000`
  - `APP_ALT_SS_TWR_RESP_SPACING_US=1000`
  - `APP_ANCHOR_RESPONDER_COOP_SLEEP_MS=1`
  - `APP_UWB_HW_FRAME_FILTER_ENABLE=1`
- A-H Anchor OTA directory: `logs/alt_bcast_a5_g2000_anchor_ota_20260430_211934`
- A-H OTA upload completed successfully.
- Post responder runtime: `sent=8 ready=8/8`.
- Post VERSION still read `actual=-` after handoff; this remains a control-plane readback issue, not a UWB responder failure.

Tag build/deploy:

- Tag marker: `alt-bcast-b29-abce-tdma10-active9-rms500-speed0-g2000-r1000`
- Tag build: `build-alt-bcast-b29-abce-tdma10-active9-tag-g2000-r1000-raw0-cont0`
- Active mask: ABCE.
- TDMA: `period=10ms`, `active=9ms`.
- Tag OTA directory: `logs/alt_bcast_b29_g2000_tag_ota_20260430_212826`
- BSF66F/BS2DCE/BSDC91 OTA succeeded and all post VERSION matched b29.

120s three-tag motion verification:

- Directory: `logs/alt_bcast_b29_g2000_abce_listener_probe_120s_20260430_213540`
- Duration: `120s`
- `positions_all=2981`
- `tf_all=5`
- Per-tag positions:
  - BSF66F: `999`
  - BS2DCE: `995`
  - BSDC91: `987`
- Per-tag rate over the logged interval:
  - BSF66F: about `8.33Hz`
  - BS2DCE: about `8.29Hz`
  - BSDC91: about `8.23Hz`
- Main anchor set:
  - `ABCE=2729` rows.
- RMS:
  - median `118mm`
  - p95 `219mm`
  - max accepted `464mm`
- Rejected frames:
  - `5`, all `rms`.
- RXG timing:
  - `txdone_to_rxstart_us`: median `732us`, p95 `854us`, max `1098us`
  - `txdone_to_rxend_us`: median `854us`, p95 `1007us`, max `1220us`
  - `rxenable_us`: median `122us`, p95 `122us`, max `244us`
- Final TDMA line:
  - `period=10ms active=9ms max_slots=12 freq motion=10Hz static=5Hz roto=10Hz`
- Listener:
  - `UF=3124`
  - `UL=102`
  - Broadcast poll sources: `0xb101`, `0xb102`, `0xb103`
  - Response anchors observed: `A=43`, `C=24`, `B=22`, `E=12`, `H=1`

Conclusion:

- b29/a5 is a stable broadcast baseline for three online Tags with 10ms TDMA scheduling.
- The previous `rank0 100% missing` failure is broken: listener now sees rank0 A and positions are balanced across all three Tags.
- The system is not yet at the final target rate: output is about `8.2-8.3Hz/tag`, not a full `10Hz/tag`.
- The next protocol task is to reduce the required Anchor guard again, but by optimizing Anchor turnaround / response scheduling rather than blindly changing spacing.
- Keep b29/a5 as the known-good recovery point before attempting a lower-guard b30/a6 experiment.

## b30 8-Anchor Broadcast Probe

Date: 2026-04-30

Purpose:

- Switch the Tag from b29's ABCE 4-anchor active mask to full 8-anchor broadcast mask.
- Keep Anchors on a5 `g2000/r1000`.
- Keep TDMA at `period=10ms`, `active=9ms`.
- Verify whether `guard + 7*spacing + tail` fits the current scheduler in practice.

Tag-only implementation:

- Fixed broadcast response-window off-by-one:
  - old formula: `guard + count*spacing + tail`
  - new formula: `guard + (count-1)*spacing + tail`
- Changed broadcast tail margin from `500us` to `300us`.
- Added `APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP=1` for this build so full 8-anchor sweeps are not overridden by the old fast-tracking path.
- Added the new flag to `scripts/build_tag_ble_motion.sh` so it is actually present in the build cache.
- No Anchor, PHY, OTA, or production workspace changes.

Build/deploy:

- Tag marker: `alt-bcast-b30-8anc-tdma10-active9-rms500-speed0-g2000-r1000`
- Tag build: `build-alt-bcast-b30-8anc-tdma10-active9-tag-g2000-r1000-raw0-cont0`
- Verified cache:
  - `APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP=1`
  - `APP_ALT_SS_TWR_GUARD_US=2000`
  - `APP_ALT_SS_TWR_RESP_SPACING_US=1000`
  - `APP_TAG_MULTITAG_PLAN_MODE=0`
  - `APP_TAG_OUTPUT_FILTER_RMS_MM=500`
  - `APP_TAG_OUTPUT_FILTER_SPEED_MM_S=0`
- Tag OTA directory: `logs/alt_bcast_b30_8anc_tag_ota_20260430_215918`
- BSF66F/BS2DCE/BSDC91 OTA succeeded and all post VERSION matched b30.

120s three-tag motion verification:

- Directory: `logs/alt_bcast_b30_8anc_listener_probe_120s_20260430_220338`
- Duration: `120s`
- `positions_all=381`
- `tf_all=0`
- Per-tag positions:
  - BSF66F: `139`
  - BS2DCE: `123`
  - BSDC91: `119`
- Per-tag rate:
  - BSF66F: about `1.17Hz`
  - BS2DCE: about `1.03Hz`
  - BSDC91: about `1.00Hz`
- All output rows were `plan=full`.
- Some accepted positions used all eight anchors, but only rarely:
  - `ABCDEFGH=6`
- RXG:
  - `mask=0xff` for all RXG rows
  - `pc=8` for all RXG rows
  - `txdone_to_rxstart_us`: median `701us`, p95 `823us`, max `1098us`
  - `txdone_to_rxend_us`: median `823us`, p95 `1007us`, max `1220us`
- Listener:
  - `UF=776`
  - `UL=152`
  - Broadcast poll sources: `0xb101`, `0xb102`, `0xb103`
  - Response anchors observed: mainly late slots `H=90`, `G=55`, with only `F=3`, `D=2`, `C=2`.

Conclusion:

- b30 did switch into true 8-anchor broadcast mode: RXG confirms `mask=0xff`, `pc=8`.
- b30 does not meet the success criteria. `positions_all=381` is far below b29's `2981` over the same 120s duration.
- The failure is primarily throughput/scheduling, not total ranging failure: it still produces valid full-plan positions with low RMS.
- 8-anchor full broadcast at `g2000/r1000` is too close to a 10ms slot in the current firmware architecture.
- The current stable operational baseline remains b29/a5: 4-anchor ABCE, `g2000/r1000`, 10ms TDMA.
- Next useful direction is not more 8-anchor-at-10ms testing; it is either:
  - return to b29 4-anchor active masks for 10Hz operation, or
  - reduce Anchor guard/spacing through Anchor turnaround optimization before retrying full 8-anchor broadcast.
