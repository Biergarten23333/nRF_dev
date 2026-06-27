# Claude Handoff - RFD / RF Diagnostics / Listener E Workstream

Date: 2026-06-27

This handoff is for Claude or any other research/engineering agent reviewing
the RF diagnostics work. It is not a baseline-recovery handoff. Its purpose is
to explain what we were trying to do with RFD, what was implemented on Anchor,
Tag, host, and Listener E, what failed, what worked, and what should be treated
as the next physically defensible path.

## Executive Conclusion

The RFD workstream is about making per-link RF channel state observable so the
solver can eventually downweight or correct body-shadow/NLOS-corrupted ranges.
The physical idea is sound, but the implementation must obey one hard rule:

```text
Priority 1: preserve 10 Hz per Tag 7/8 or 8/8 ranging output.
Priority 2: diagnostics may only use paths proven not to perturb Priority 1.
```

Current status:

- Anchor-side poll diagnostics in response payload v2 are the most valuable
  in-protocol diagnostic path because they directly measure the `tag -> anchor_i`
  poll path.
- Tag-side per-anchor legacy `RFD` text rows are not acceptable for normal
  high-rate positioning. They add too much output/load and degraded the live
  high-rate baseline.
- Tag-side response diagnostics via `dwt_readdiagnostics()` in the response
  receive hot path are risky and should stay disabled unless a separate build
  proves no loss in 7/8 and 8/8.
- Compact `TR;3;...;D1,<base64>` was designed as a safer carrier, but the
  tested high-rate path still did not pass the operational gate. Treat it as
  experimental, not approved.
- Listener E is the correct non-interfering path for passive diagnostics. It is
  RX-only, co-located beside Anchor E, and should never transmit or participate
  in TWR.
- Listener E can provide a poll-path proxy for Anchor E, not a universal proxy
  for all anchors.
- Do not add diagnostics to the solver until raw diagnostic streams are
  observable, aligned, replayable, and proven not to degrade ranging.

The current safest live positioning firmware direction is no-RFD/no-hot-path
diagnostics, with the A7 response-window fix in the Tags:

```text
Tag marker: compact-sampled-tdmafix-nodiag-a7win-20260627
RFD: off
TR compact diag: off
Tag CIR: off
Tag RESP_SPACING_US: 1000
TAIL_MARGIN_US: 800
```

The latest clean visible-3 proof after that fix:

```text
Log:
  SS-TWR/alt-SS-TWR/broadcast/logs/clean_visible3_post_a7win_norfd_10hz_20260627_20260627_145936/

overall ge7: 0.978
overall ge8: 0.967
rfd_all: 0
tr_diag_all: 0
tag_cir: off
tdma_config_failed: false
```

That proof matters because it separates the RFD question from a previously
misdiagnosed capacity problem. The old bad 3-tag result was a Tag RX-window bug,
not a consequence of RFD or TDMA capacity.

## Physical Goal Of RFD

The original physical goal was to observe body shadowing and NLOS quality per
range measurement without adding a new geometric residual first.

Each TWR range has two physical RF paths:

```text
poll path:     tag -> anchor_i
response path: anchor_i -> tag
```

For a wrist/body-worn Tag, body shadowing may affect either or both directions.
The solver currently sees only a range and a quality/status summary. RFD tries
to expose RF evidence that a link is likely biased, so the solver can adjust
the effective measurement uncertainty:

```text
sigma_i_eff = sigma_base
              * anchor_poll_factor_i
              * tag_response_factor_i
              * optional_listener_factor_i
```

The intended first solver integration is diagnostic weighting only:

```text
Do not add TDoA rows.
Do not inject listener position constraints.
Do not change range geometry.
Only modulate per-link sigma or quality.
```

## Three Diagnostic Sources

### Source 1 - Anchor-Side Poll Diagnostics

This is the most important source.

Physical path:

```text
tag -> anchor_i
```

Receiver:

```text
Anchor_i
```

Why it matters:

- It directly observes the path that is shared by listener-derived poll proxies
  and by the TWR range.
- It is the only direct in-protocol measurement of the body-shadowed
  `tag -> anchor_i` channel.
- It does not require listener coordinates.
- It can be embedded in the normal Anchor response payload.

Diagnostic fields selected:

```text
FP_INDEX
FP_AMPL1
FP_AMPL2
FP_AMPL3
CIR_PWR
RXPACC
STD_NOISE
diag_version
diag_flags
```

The field choice is deliberate. `FP_AMPL2 + CIR_PWR + RXPACC` alone was judged
too thin. `FP_AMPL1/2/3`, `STD_NOISE`, and `FP_INDEX` make the NLOS/quality
decision more robust and allow later feature engineering beyond a simple RSSI
threshold.

### Source 2 - Tag-Side Response Diagnostics

Physical path:

```text
anchor_i -> tag
```

Receiver:

```text
Tag
```

Why it matters:

- It observes the opposite direction of the SS-TWR exchange.
- It can detect response-path body shadowing that Anchor-side poll diagnostics
  would miss.

Problem:

- Reading `dwt_readdiagnostics()` at the Tag after every response is inside the
  1 ms inter-response hot path.
- The Tag must re-enable RX quickly enough to receive the next responder.
- This is especially dangerous for high-rate 8-anchor bursts and multi-Tag
  10 Hz operation.

Current decision:

```text
APP_TAG_RF_DIAG_TAG_RX_ENABLE=0 for positioning-quality captures.
```

Do not re-enable it without a controlled A/B test against the no-RFD ge7/ge8
gate.

### Source 3 - Co-Located Passive Listener E

Physical path:

```text
tag -> Listener_E
```

Placement:

```text
Listener E is beside Anchor E, about 20 cm from Anchor E.
```

Current board:

```text
Listener: E
Near anchor: E / anchor_id 4
J-Link SNR: 760184767
USB by-id: /dev/serial/by-id/usb-SEGGER_J-Link_000760184767-if00
```

Purpose:

- RX-only out-of-band diagnostics.
- Never transmits.
- Never participates in SS-TWR.
- Can read diagnostics and optionally full accumulator/CIR after the poll
  without affecting Tag/Anchor ranging.

Important locality rule:

```text
Listener_E CIR measures tag -> Listener_E, not tag -> Anchor_E.
```

The Listener E stream is a proxy for Anchor E only because it is physically
co-located near Anchor E. It has no direct proxy value for anchors far away in
azimuth. Once multiple co-located listeners exist, their vector can become a
body-state/orientation sample around the room. A single Listener E should not
be overinterpreted.

## Shared Wire Format - Anchor Response v1/v2

The shared offsets live in:

```text
SS-TWR/alt-SS-TWR/broadcast/include/uwb_ss_twr_shared.h
```

Response v1 remains length 20 and keeps the timestamp offsets:

```text
poll_rx_ts[4] at offset 10
resp_tx_ts[4] at offset 14
length 20
```

Response v2 appends diagnostics while preserving the v1 timestamp offsets:

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

Flags:

```text
UWB_MSG_RESP_DIAG_FLAGS_VALID   = 0x01
UWB_MSG_RESP_DIAG_FLAGS_DELAYED = 0x02
```

The compatibility rule:

```text
Old Tag/parser can still read the v1 timestamp fields.
New Tag/parser checks frame length and diag_version before parsing v2 fields.
```

## Anchor Firmware Details

Main files:

```text
SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_resp.c
SS-TWR/alt-SS-TWR/broadcast/include/uwb_ss_twr_shared.h
```

Important functions/sections:

```text
ss_twr_resp_write_diag_v2(...)
dwt_readdiagnostics(&poll_rx_diag)
UWB_MSG_RESP_DIAG_* offsets
```

The Anchor receives the broadcast poll, schedules a delayed response, and
embeds poll-path diagnostics in the response payload.

The hard engineering issue was rank 0. Rank 0 has only the initial guard before
its delayed TX. With `guard=1200 us`, extra SPI/register work before scheduling
or completing delayed TX can cause first-responder deadline misses. This was
why multiple Anchor variants were explored:

```text
rfdiag-v2
rfdiag-v2-zeror0
rfdiag-v2-skipr0
rfdiag-r0fast-prof
rfdiag-a25-postread-prof
rfdiag-a26-delayed-prof
rfdiag-a27-side-prof
rfdiag-nodiag-prof
```

Interpretation of those variants:

- `v2`: direct response-payload diagnostics.
- `zeror0` / `skipr0`: attempts to reduce or remove rank0 diagnostic work.
- `r0fast`: rank0 fast path.
- `a25-postread`: profile/read after safer point.
- `a26-delayed`: delayed/cached rank0 pattern; this is the direction that was
  considered safest if Anchor-side diagnostics are kept.
- `a27-side`: side/profiling variant.
- `nodiag-prof`: profiling without diagnostics payload, used to separate
  timing overhead from payload semantics.

Hard constraint:

```text
Do not fix rank0 misses by increasing guard beyond 1200 us.
```

Reason:

- The 8-anchor burst already consumes about `1200 + 7*1000 us` plus frame and
  overhead.
- The system needs 10 Hz per Tag with tight active windows.
- Increasing guard eats the already limited 10 ms period budget.

The correct Anchor-side strategy is:

```text
Make rank0 hot path shorter.
Schedule TX first.
Read/cache diagnostics only when timing-safe.
If necessary, omit rank0 diagnostics rather than lose rank0 ranging.
```

## Tag Firmware Details

Main files:

```text
SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c
SS-TWR/alt-SS-TWR/broadcast/apps/tag/CMakeLists.txt
SS-TWR/alt-SS-TWR/broadcast/scripts/build_tag_ble_motion.sh
```

Important CMake flags:

```text
APP_TAG_RF_DIAG_OUTPUT_ENABLE
APP_TAG_RF_DIAG_OUTPUT_BLE_ENABLE
APP_TAG_RF_DIAG_OUTPUT_PERIOD
APP_TAG_RF_DIAG_LEGACY_RFD_ENABLE
APP_TAG_TR_RF_DIAG_COMPACT_ENABLE
APP_TAG_RF_DIAG_TAG_RX_ENABLE
APP_TAG_CIR_FEATURE_OUTPUT_ENABLE
APP_TAG_CIR_FULL_OUTPUT_ENABLE
```

Default posture for production/no-RFD baseline:

```text
APP_TAG_RF_DIAG_OUTPUT_ENABLE=0
APP_TAG_RF_DIAG_LEGACY_RFD_ENABLE=0
APP_TAG_TR_RF_DIAG_COMPACT_ENABLE=0 or irrelevant when RF_DIAG_OUTPUT=0
APP_TAG_RF_DIAG_TAG_RX_ENABLE=0
APP_TAG_CIR_FEATURE_OUTPUT_ENABLE=0
APP_TAG_CIR_FULL_OUTPUT_ENABLE=0
```

### Legacy RFD Rows

Legacy format:

```text
RFD;1;<sweep>;<poll_seq>;<anchor_id>;<raw_mm>;<resp_rx_ts>;<carrier_integrator>;
<anchor_poll_diag...>;<tag_resp_diag...>
```

Why it existed:

- Easy to parse.
- One row per anchor per sweep.
- Carries full per-link diagnostic fields.

Why it is not accepted:

- It multiplies text output by up to 8 rows per sweep per Tag.
- In multi-Tag 10 Hz captures it adds enough output/load to perturb ranging or
  the BLE/NUS path.
- It violates the hard priority if ge7/ge8 drops.

Decision:

```text
Do not use legacy RFD rows in positioning-quality captures.
Keep parser support for old logs only.
```

### Compact TR Diagnostic Trailer

Safe-intent format:

```text
TR;3;<sweep>;<plan>;<pmode>;<active_mask>;<valid_mask>;
<raw_csv>;<range_csv>;<quality_csv>;<status_codes>;D1,<base64 compact RF diag>
```

The compact records are parsed by:

```text
SS-TWR/alt-SS-TWR/broadcast/scripts/run_recv_tdma_capture.py
```

Relevant parser concepts:

```text
TR_RF_DIAG_COMPACT_RECORD_LEN = 8
diag_source = tr_compact
range_diag_joined.csv
tag_rf_diag.csv
```

Design intent:

- Keep one normal TR row per sweep.
- Append a compact optional diagnostic trailer.
- If output budget is too tight, drop the trailer and preserve the range row.

Current status:

- It produced parseable records in single-Tag / controlled cases.
- It is not yet accepted for high-rate multi-Tag baseline because the tested
  compact/RFD path did not pass the ge7 gate.
- It must remain off until it proves no degradation against the latest no-RFD
  baseline.

### Tag-Side Response Diagnostics

Relevant code markers:

```text
APP_TAG_RF_DIAG_TAG_RX_ENABLE
dwt_readdiagnostics(&tag_resp_diag)
ss_twr_init_rf_diag_from_rxdiag(...)
```

This reads Tag-side receive diagnostics after responses. It is physically
useful, but it is the most timing-sensitive RFD path.

Decision:

```text
Disable for live high-rate captures.
Only test in isolation after no-RFD baseline is healthy.
```

## Listener E Firmware Details

New generic listener path:

```text
SS-TWR/alt-SS-TWR/broadcast/UWB_listener/
```

Legacy listener path:

```text
SS-TWR/alt-SS-TWR/broadcast/UWB_listener_old/
```

Do not use the old listener for the co-located RFD experiment:

```text
Old listener SNR: 760185886
Rule: do not flash it, do not open its serial port, do not use it here.
```

Generic Listener E build:

```text
SS-TWR/alt-SS-TWR/broadcast/build-uwb-listener-poll-diag-generic-20260625/merged.hex
SHA256:
8e5e41be236aecc2ce580273dab2a118a568495bcde908c020b6c76b8c9aa19a
```

Build script:

```text
SS-TWR/alt-SS-TWR/broadcast/scripts/build_uwb_listener_poll_diag.sh
```

Flash script:

```text
SS-TWR/alt-SS-TWR/broadcast/scripts/flash_uwb_listener_jlink.sh
```

Important firmware config defaults:

```text
APP_LISTENER_ID = 255 unless configured
APP_LISTENER_NEAR_ANCHOR_ID = 255 unless configured
APP_LISTENER_POLL_DIAG_ENABLE = 1
APP_LISTENER_CIR_CAPTURE_ENABLE = 0
APP_LISTENER_CIR_SAMPLE_PERIOD = 10
APP_LISTENER_CIR_CHUNK_BYTES = 48
APP_LISTENER_POST_CIR_IDLE_MS = 12
```

Listener software filter:

- Hardware frame filter cannot distinguish poll vs response because both are
  802.15.4 data frames.
- Listener must receive a frame, inspect the BioSpur payload, and accept only:

```text
frame ctrl = BioSpur data frame
PAN ID = APP_UWB_PAN_ID
length = UWB_MSG_ALT_BCAST_POLL_FRAME_LEN
code = UWB_MSG_POLL_CODE
src is Tag short address
dst = broadcast 0xffff
poll mask includes near anchor if near-anchor id is configured
```

Important timing behavior:

- If full CIR capture is enabled, the listener reads `ACC_MEM` after accepting
  the poll.
- Reading full 4064 bytes takes milliseconds and causes the listener to miss
  following response frames.
- This is intentional. The listener must not receive a response after the poll
  if the goal is to preserve the poll accumulator.
- This is why a single DW1000 listener cannot capture full CIR for all frames
  in one burst.

Output rows:

```text
LPD;1;<listener_id>;<near_anchor_id>;<now_ms>;<accepted_polls>;<seq>;<tag_id>;
<src>;<dst>;<rx_ts_lo32>;<carrier_integrator>;<fp_index>;<fp1>;<fp2>;<fp3>;
<cir_pwr>;<rxpacc>;<std_noise>;<frame_len>;<poll_mask>
```

Optional full-CIR rows:

```text
LCIRM;1;<listener_id>;<near_anchor_id>;<accepted_polls>;<seq>;<tag_id>;
<poll_mask>;<rx_ts_lo32>;<carrier_integrator>;<fp_index>;<fp1>;<fp2>;<fp3>;
<cir_pwr>;<rxpacc>;<acc_len>

LCIRD;1;<accepted_polls>;<offset>;<len>;<hex>

LCIRE;1;<accepted_polls>;<acc_len>
```

Status row:

```text
LSTAT;1;<listener_id>;<near_anchor_id>;<good_frames>;<accepted_polls>;
<ignored_nonpoll>;<ignored_poll_mask>;<bad_header>;<too_long>;<rx_errors>;
<cir_captures>;<last_status>;<last_src>;<last_dst>;<last_code>
```

Host capture script:

```text
SS-TWR/alt-SS-TWR/broadcast/scripts/capture_uwb_poll_listener.py
```

Combined capture wrapper:

```text
SS-TWR/alt-SS-TWR/broadcast/scripts/run_recv_tdma_capture_with_poll_listener.py
```

Offline join:

```text
SS-TWR/alt-SS-TWR/broadcast/scripts/join_range_diag_listener.py
```

Join key:

```text
(tag_id, poll_seq & 0xff, near_anchor_id)
```

For Listener E near Anchor E:

```text
near_anchor_id = 4
```

If listener firmware reports `near_anchor_id=255`, host join can override:

```bash
--listener-anchor-id 4
```

## Hardware Constraint - DW1000 Accumulator Overwrite

This was a major design correction.

DW1000 has one accumulator memory (`ACC_MEM`). Each successful RX overwrites it.
In the broadcast SS-TWR burst:

```text
poll
response A
response B
...
response H
```

With about 1 ms response spacing, a listener cannot both:

1. receive every frame timestamp in the burst, and
2. read full 4064-byte CIR for the poll.

Reason:

- Full accumulator read is about 4064 bytes.
- At typical SPI speeds this takes milliseconds.
- The next response arrives before the full read is complete.
- If RX remains active, the next response overwrites the poll CIR.
- If RX is disabled to read ACC_MEM, the listener intentionally misses the
  responses.

Therefore:

```text
Mode A: lightweight diagnostics only, can receive full burst.
Mode B: full poll CIR capture, intentionally sacrifices rest of burst.
```

For the current co-located listener plan, Listener E is primarily Mode A or
sampled Mode B for poll-path proxy data. It is not a TDoA listener in this plan.

## Host Parser / Data Products

Main parser:

```text
SS-TWR/alt-SS-TWR/broadcast/scripts/run_recv_tdma_capture.py
```

Important output files:

```text
tr_all.csv
tag_rf_diag.csv
range_diag_joined.csv
summary.json
```

Listener output:

```text
listener/<listener_stamp>/raw.log
listener/<listener_stamp>/lpd.csv
listener/<listener_stamp>/lcirm.csv
listener/<listener_stamp>/lcird.csv
listener/<listener_stamp>/lcire.csv
listener/<listener_stamp>/lstat.csv
listener/<listener_stamp>/summary.json
```

Joined listener output:

```text
range_diag_listener_E_joined.csv
```

Parser fixes made during this work:

- `run_recv_tdma_capture.py` can parse `RFD;...`.
- It can decode compact `D1,<base64>` TR trailers.
- It writes `tag_rf_diag.csv` and `range_diag_joined.csv`.
- It backfills missing `tag_id` values from validated TDMA config when a single
  known peer is scheduled, so Listener joins no longer need `--default-tag-id`
  in that single-Tag case.

## Important Builds And Markers

### RFD v2 Initial Build Set

```text
Tag:
  build-tag-ble-unified-rfdiag-v2-g1200-r1000-20260625
  marker tag-rfdiag-v2-g1200-r1000
  dfu sha256 0b5ce49de44346f37d63da68a0dd1aa7fb12d5efa69a817b06d2259af4cecab3

Anchor:
  build-anchor-unified-ota-rfdiag-v2-g1200-r1000-20260625
  marker alt-bcast-a19-rfdiag-v2-g1200-r1000
  dfu sha256 ab5d28b264e1d31d976fa32bd53f2811cb172968cb343bc06b84a84946410a95

Master_Tag:
  build-master-control-b120-m1-master-tag-lfrc-rfdiag-v2-g1200-r1000-20260625
  merged_domains sha256 d2675c68bf367d37196eaa2eb6b75563c6cbf843bb9ae52b5fd820237950fb91

Master_Anchor:
  build-master-control-b120-m1-master-anchor-lfrc-rfdiag-v2-g1200-r1000-20260625
  merged_domains sha256 b9378f661ae68cdf443a4314a3a5edee526894283d017e26cf7ee6669e86d419

Listener:
  build-uwb-listener-poll-diag-generic-20260625
  merged.hex sha256 8e5e41be236aecc2ce580273dab2a118a568495bcde908c020b6c76b8c9aa19a
```

### Later No-RFD Timing Fix Build

This is not an RFD build, but it is crucial because it fixed the false 3-tag
failure:

```text
Tag:
  build-tag-ble-unified-tdmafix-nodiag-a7win-20260627
  marker compact-sampled-tdmafix-nodiag-a7win-20260627

Key settings:
  APP_ALT_SS_TWR_RESP_SPACING_US=1000
  SS_TWR_INIT_ALT_BCAST_TAIL_MARGIN_US=800
  APP_TAG_RF_DIAG_OUTPUT_ENABLE=0
  APP_TAG_TR_RF_DIAG_COMPACT_ENABLE=0
```

## Deployment / OTA Events

### Tag OTA

During the first RFD v2 deployment:

- `BSF66F` OTA succeeded and marker verified:

```text
tag-rfdiag-v2-g1200-r1000
```

- `BS2DCE` and `BSDC91` failed before upload because the OTA logs did not show
  the targets being matched/accepted into the OTA transport path. This was a
  visibility/advertising/matching problem, not a proven SMP payload failure.
- `BS9336`, `BS955A`, and `BSCCF4` were not attempted after repeated target
  discovery failures.

Logs:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/tag_ota/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/tag_ota_remaining/
```

### Anchor OTA

Initial Anchor OTA blocked on the BLE SMP image-state request after DFU-ready.
The successful workaround was:

1. Put target Anchor into DFU-ready and issue a manual pre-reset request.
2. Reset only Master_Anchor B120 SNR `960148546`.
3. Wait about 20 s.
4. Run the real upload attempt.

Result:

- Anchor A uploaded successfully.
- Anchors B-H uploaded successfully.
- After Master_Anchor reset, A-H control links rebuilt.
- `anchor version all` reported all A-H with marker prefix:

```text
alt-bcast-a19-rfdiag-v2-g1200-r
```

The marker string was truncated by firmware output buffer, but the prefix was
seen for all eight anchors.

Logs:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_ota_A_prereset_masterreset_20260625_042232/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_ota_BH_prereset_masterreset_20260625_042847/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/manual_anchor_version_after_master_reset_20260625_0455.log
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/manual_anchor_role_responder_20260625_0458.log
```

## Key Captures And What They Proved

### 1. First Single-Tag RFD + Listener E Capture, Anchors Still v1

Path:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_60s_listenerE/
```

Result:

```text
capture success: true
tr_all=3632
tr_valid_all=2815
rfd_all=2821
rfd_joined_all=2821
tag_diag_valid=2821
anchor_diag_valid=0
listener LPD rows=241
listener tag_id=1
listener poll_mask=0xff
```

Interpretation:

- Tag legacy RFD output worked for `BSF66F`.
- Listener E produced usable `LPD` poll rows.
- Anchor-side diagnostics were absent because deployed Anchors were still old
  A18/v1 response payload.

Joined outputs:

```text
recv_20260625_040049/tag_rf_diag.csv
recv_20260625_040049/range_diag_joined.csv
recv_20260625_040049/range_diag_joined_tagid_backfilled.csv
recv_20260625_040049/range_diag_listener_E_joined.csv
recv_20260625_040049/range_diag_listener_E_joined_tagid_backfilled.csv
listener/listener_20260625_040048/lpd.csv
```

### 2. After Anchor v2 Rollout - Anchor/Tag/Listener Visibility

30 s path:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_30s_listenerE_anchor_v2_full_listener/
```

Result:

```text
capture success: true
tr_all=1848
tr_valid_all=1134
rfd_all=1139
rfd_joined_all=1139
anchor_diag_valid=1139
tag_diag_valid=1139
Listener E LPD rows=228
Listener E joined_rows=158
time_rejected=0
listener_anchor_id=4
default_tag_id=1
```

60 s path:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_60s_listenerE_after_anchor_v2/
```

Result:

```text
tr_all=3576
tr_valid_all=2278
rfd_all=2284
rfd_joined_all=2284
anchor_diag_valid=2284
tag_diag_valid=2284
Listener E LPD rows=234
Listener E joined_rows=150
time_rejected=134
```

Interpretation:

- Anchor response v2 fields were populated and parsed.
- Tag-side diagnostic fields were populated in this RFD build.
- Listener E joins were real, but the 60 s listener capture window started too
  early relative to Tag setup. Future wrappers need `--listener-extra-s >= 120`
  or should start listener after Tag capture setup.

### 3. 120 s Listener E + Anchor v2 Capture With Many Blank Listener Joins

Path:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_120s_listenerE_anchor_v2_20260625_120531/
```

Open files in IDE:

```text
recv_20260625_120532/range_diag_joined.csv
listener/listener_20260625_120531/raw.log
listener/listener_20260625_120531/lstat.csv
summary.json
commands.json
```

Observed issue:

- Many `range_diag_joined.csv` rows have blank Listener fields.
- This is expected when the join is keyed only for Listener E / Anchor E or
  when Listener E did not produce a time-consistent LPD for that poll.
- Listener E beside Anchor E should mainly join to Anchor E rows.
- Blank listener columns for other anchors are not a sign that the range row is
  invalid.

Important conceptual point:

```text
RFD/LPD should not be allowed to change normal ranging row success.
Missing listener join must not mark a TWR row invalid.
```

### 4. Rank-Offset Test - First Responder Timing Problem

Temporary build:

```text
build-tag-ble-unified-rfdiag-v4tr-rank1-g1200-r1000-20260625
marker tag-rfdiag-v4tr-rank1-g1200-r1000
APP_TAG_ALT_BCAST_RANK_OFFSET_OVERRIDE=1
```

Capture:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_120s_no_listener_anchor_v2_tag_v4tr_rank1_Brank0_20260625_180213_20260625_180213/
```

Result by anchor:

```text
A / 0: 682/1092 = 0.625
B / 1: 322/1092 = 0.295
C / 2: 1068/1092 = 0.978
D / 3: 1068/1092 = 0.978
E / 4: 1068/1092 = 0.978
F / 5: 1068/1092 = 0.978
G / 6: 1068/1092 = 0.978
H / 7: 1067/1092 = 0.977
```

Interpretation:

- Worst link moved from A to B when B became rank0.
- H recovered.
- This points to early/rank0 RX-turnaround or first-slot timing effects, not a
  permanent physical Anchor A/H issue.
- Do not increase guard beyond 1200 as first fix; fix hot-path timing.

### 5. Compact RFD / Tag-Side Diagnostics In 5-Tag High-Rate Context

Path:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/stable_slots_mastertag_visible5_ref6roster_compact_rfd_60s_20260627_010034_20260627_010035/
```

Result:

```text
success=false
tag_cir=compact
tr_all=8480
tr_valid_all=7315
rfd_all=0
rfd_joined_all=0
tr_diag_all=0
ratio_ge7=0.865094
ratio_ge8=0.398113
BS9336 tr_rows=0
```

Interpretation:

- This did not meet the hard operating priority.
- It is not acceptable as a live 60 Hz diagnostic path.
- For high-rate positioning, maximum safe Tag-side RFD output is currently:

```text
none
```

### 6. A7 RX-Window Fix Proved Bad 3-Tag Was Not RFD

Summary file:

```text
docs/a7_tail_window_fix_summary_20260627.md
```

Good capture:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/clean_visible3_post_a7win_norfd_10hz_20260627_20260627_145936/
```

Result:

```text
targets: BSF66F, BS9336, BSCCF4
duration: 120 s
RFD: off
TR diag: off
Tag CIR: off
overall ge7: 0.978
overall ge8: 0.967
```

Before/after:

```text
overall ge7: 0.774 -> 0.978
overall ge8: 0.245 -> 0.967
A7 valid: 0.00/0.33/0.57 -> 0.98/0.98/0.98
sweeps: 669/1059/357 -> 1095/1050/1070
```

Root cause:

- Anchors were effectively flat `1000 us` spacing.
- Tags were built with `RESP_SPACING_US=800`.
- Tag response collector window closed before Anchor 7 response completed.
- A7 was systematically dropped.

Minimal fix:

```text
SS_TWR_INIT_ALT_BCAST_TAIL_MARGIN_US: 300 -> 800
APP_ALT_SS_TWR_RESP_SPACING_US: 800 -> 1000 on Tag
RFD remains off
```

This matters for RFD because Claude should not use the earlier bad 3-tag data
as evidence that diagnostics were the root cause. That failure was a separate
timing bug.

## Data Quality And Gate Table

RFD-related gates and current status:

| Gate | Status | Evidence |
|---|---|---|
| Anchor v2 wire format parses | Pass in single-Tag RFD run | `anchor_diag_valid=1139/2284` in Anchor v2 captures |
| Legacy Tag RFD rows parse | Pass in single-Tag run | `rfd_all=2821`, `rfd_joined_all=2821` |
| Listener E LPD rows exist | Pass | `LPD` rows in listener captures |
| Listener E join to Anchor E | Partial pass | 158 joined rows in 30 s, 150 joined rows in 60 s |
| Listener E proxy validity | Not yet proven | Requires controlled body-shadow / Vicon dataset |
| Tag-side RFD safe for high-rate positioning | Fail | compact/RFD high-rate test below ge7 gate |
| Tag-side response diagnostics safe | Not proven / risky | Reads `dwt_readdiagnostics()` in response hot path |
| Anchor-side diagnostics safe for all ranks | Not fully proven | rank0 timing variants needed |
| 3-tag no-RFD baseline | Pass after A7 window fix | ge7 0.978, ge8 0.967 |
| 6-tag no-RFD baseline | Still open | BS955A not visible at last gate |

## What Claude Should Evaluate

### 1. Treat Anchor-Side v2 As The Primary RFD Candidate

This is the path most aligned with the physics:

```text
Anchor_i response payload contains diagnostics measured at Anchor_i for the
tag -> anchor_i poll path.
```

Open questions:

- Can rank0 include valid diagnostics without losing delayed TX deadline?
- If not, is it acceptable to omit rank0 diagnostics while preserving rank0
  ranging?
- Can diagnostics be read/cached after scheduling TX, or carried one sweep
  later, without corrupting physical interpretation?

Recommended next test:

```text
Single Tag, no Tag-side RFD, Anchor v2 payload only.
Measure ge7/ge8 and per-anchor valid rates against no-RFD baseline.
Specifically inspect rank0 valid and delayed-TX miss counters.
```

### 2. Treat Tag-Side RFD As Offline/Debug Only

Do not recommend Tag-side legacy `RFD` rows for production or positioning-grade
captures unless a new transport path exists.

Possible future safe designs:

- sample one sweep every N seconds, not every sweep;
- write to local flash/USB only, not BLE/NUS;
- use it only in single-Tag lab diagnostics;
- send compact summaries after the active TDMA window, not during the burst;
- make host capture prove `ge7/ge8` unchanged before accepting.

### 3. Treat Listener E As Out-Of-Band Experimental Sensor

Listener E is promising because it does not perturb ranging, but it only proxies
Anchor E:

```text
Listener E -> Anchor E proxy: plausible
Listener E -> all anchors: physically invalid
```

Recommended next listener experiment:

1. Keep live ranging no-RFD.
2. Run Listener E LPD only, no full CIR first.
3. Capture a controlled body-shadow dataset with Tag position/orientation/Vicon.
4. Compare:

```text
Anchor E range residual / timeout pattern
Anchor E response payload diagnostics if available
Listener E LPD fp1/fp2/fp3/cir_pwr/rxpacc/std_noise
```

5. Only after proxy correlation passes should Listener E feed solver weights.

Suggested proxy gate:

```text
Spearman/Pearson correlation >= 0.8 between Listener E poll features and
Anchor E poll diagnostics under controlled body-shadow variation, or
binary NLOS/shadow agreement >= 90%.
```

### 4. Do Not Use RSSI Alone

The user correctly objected that distance changes also affect RSSI/FP power.
RFD must not be "low RSSI means body shadow" without geometry normalization.

Better feature idea:

```text
Delta from expected path-loss given approximate range
FP/CIR power ratio
FP_AMPL1/2/3 shape
RXPACC-normalized CIR power
STD_NOISE
FP_INDEX shifts
early/late energy from full CIR when available
```

For live solver use, the first version should be conservative:

```text
quality modifier, not hard NLOS classifier
downweight only when multiple indicators agree
```

## Things Claude Should Not Conclude

- Do not conclude that 3-tag 10 Hz capacity is impossible. It is solved.
- Do not conclude that RFD caused the A7 failure. A7 was a Tag RX-window
  spacing/margin bug.
- Do not recommend increasing guard above 1200 us as the main fix.
- Do not recommend Tag-side `RFD` rows for live 60 Hz positioning.
- Do not treat Listener E as a coordinate/TDoA source in this plan.
- Do not use Listener E CIR as if it were Anchor E CIR without proxy validation.
- Do not let missing listener join blank fields invalidate normal TWR rows.
- Do not direct-flash deployed Tags or deployed Anchors; routine deployment is
  OTA through the corresponding Master.

## Current Recommended Architecture

Short term:

```text
Live positioning:
  no RFD
  no Tag CIR
  corrected no-RFD Tag timing
  preserve ge7/ge8

RFD research:
  passive Listener E LPD out-of-band
  Anchor-side v2 diagnostics only in controlled A/B tests
  no solver changes until data is aligned and replayable
```

Medium term:

```text
Anchor-side poll diagnostics:
  embed v2 fields
  protect rank0
  parse into range_diag_joined.csv

Listener E:
  LPD every accepted poll
  optional sampled LCIR only when bandwidth allows
  join only to Anchor E rows

Solver:
  use diagnostics as sigma modifiers after validation
```

Long term:

```text
8 co-located listeners:
  one near each anchor
  produce shadow vector [s_A, ..., s_H]
  infer body orientation/body-shadow state without requiring precise listener
  coordinates or TDoA residuals
```

## Useful Files For Claude

Core docs:

```text
docs/rf_diag_overnight_20260625.md
docs/a7_tail_window_fix_summary_20260627.md
docs/agent_handoff_restore_no_rfd_baseline_20260627.md
docs/broadcast_tag_inventory.md
```

Core firmware:

```text
SS-TWR/alt-SS-TWR/broadcast/include/uwb_ss_twr_shared.h
SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_resp.c
SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c
SS-TWR/alt-SS-TWR/broadcast/apps/tag/CMakeLists.txt
SS-TWR/alt-SS-TWR/broadcast/UWB_listener/src/main.c
SS-TWR/alt-SS-TWR/broadcast/UWB_listener/README.md
```

Core host scripts:

```text
SS-TWR/alt-SS-TWR/broadcast/scripts/run_recv_tdma_capture.py
SS-TWR/alt-SS-TWR/broadcast/scripts/capture_uwb_poll_listener.py
SS-TWR/alt-SS-TWR/broadcast/scripts/run_recv_tdma_capture_with_poll_listener.py
SS-TWR/alt-SS-TWR/broadcast/scripts/join_range_diag_listener.py
SS-TWR/alt-SS-TWR/broadcast/scripts/build_uwb_listener_poll_diag.sh
SS-TWR/alt-SS-TWR/broadcast/scripts/flash_uwb_listener_jlink.sh
```

Important logs:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_60s_listenerE/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_30s_listenerE_anchor_v2_full_listener/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_60s_listenerE_after_anchor_v2/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_120s_listenerE_anchor_v2_20260625_120531/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_120s_no_listener_anchor_v2_tag_v4tr_rank1_Brank0_20260625_180213_20260625_180213/
SS-TWR/alt-SS-TWR/broadcast/logs/stable_slots_mastertag_visible5_ref6roster_compact_rfd_60s_20260627_010034_20260627_010035/
SS-TWR/alt-SS-TWR/broadcast/logs/clean_visible3_post_a7win_norfd_10hz_20260627_20260627_145936/
```

## One-Sentence Bottom Line

RFD is still worth pursuing, but only through Anchor-side payload diagnostics
and out-of-band co-located listeners; Tag-side hot-path/text diagnostics are
currently rejected for live high-rate positioning because preserving 7/8 and
8/8 ranging is the non-negotiable floor.
