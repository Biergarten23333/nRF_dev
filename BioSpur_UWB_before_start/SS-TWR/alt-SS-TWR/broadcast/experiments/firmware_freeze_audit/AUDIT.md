# Firmware Freeze Audit — Four-Piece Set, Post-5.28-Erlangen

**Audit only — no code changed, no build, no flash.** Produced 2026-07-15 from `git diff`, not from memory or prior summaries. Every raw diff hunk is mapped to a catalog change; unmapped/unauthorized lines would be flagged RED (none found).

Machine-readable catalog: [changes.json](changes.json)

---

## TASK 0 — The 5.28 Erlangen baseline commit

**Baseline = `ae3d3e391` (2026-05-26 14:30:12 +0200).** HEAD = `a30166bc6` (2026-07-15 08:30:27).

`ae3d3e391` is the last commit touching **any** of the four pieces' source before the 2026-05-28 Erlangen capture commit (`1936d01c3`). The two commits in between (`1c4954760`, `5059314b3`, both 05-26) were field-UI only — they did not touch `src/` or `apps/{tag,anchor,master,master_control,master_ota}`. `ae3d3e391` is an ancestor of HEAD, so `git diff ae3d3e391 HEAD` is a clean linear tree difference. **All four pieces share this single baseline commit** (the tree state going into 5.28).

Cross-check with capture metadata / firmware strings:

| Piece | 5.28 baseline firmware | HEAD firmware |
|---|---|---|
| TAG | `alt-bcast-b69-imu-rawxyz-tspoll-g1200-r1000` (b69 raw-IMU; the Erlangen tag line, per `BIOSPUR_USABLE_FIRMWARE_VERSIONS.md`) | `unified-diaggate-p0txpwr-20260714` |
| ANCHOR | `us-hc-exp4-tail900-start5` (**deployed**; found ×76 in `erlangen_20260528_optitrack` logs) / `us-hc-exp2` (embedded in master OTA payload at that commit) | `altbcast-fixeda19-p0txpwr-g1200-r1000` (g1200-r1000, **no** tail compression) |
| MASTER_TAG | `build-master-control-b120-m1-master-tag-lfrc-b69-...-20260526` carrier + b69 tag payload | same carrier source + `unified-diaggate-p0txpwr` tag payload |
| MASTER_ANCHOR | `build-master-control-b120-us-hc-exp4-tail900-start5` carrier + us-hc-exp anchor payload | same carrier source + `altbcast-fixeda19-p0txpwr` anchor payload |

**Uncertainty noted:** the anchor OTA payload *embedded in the master* at `ae3d3e391` was `us-hc-exp2`, while the anchor actually *deployed/flashed* for the 5.28 field test was `us-hc-exp4-tail900-start5` (both are `ss_twr_resp.c` builds differing only in the tail-compression / ultrasound build flags). This does not affect the source-level diff. Between 5.28 and now the deployed anchor was rolled back from tail-compressed (`tail900-start5`) to the plain `g1200-r1000` a18/fixeda19 timing — a **build-flag** change (`APP_ALT_SS_TWR_TAIL_COMPRESS_ENABLE`), captured in the anchor `CMakeLists` audit (A2).

### Master-app note (sharing)

The two master carriers are **one firmware built two ways.** Both MASTER_TAG and MASTER_ANCHOR are `apps/master_control` (CDC=`BioSpur_BLE_Control`, B120), which compiles in `apps/master/src/master_multi_app.c` + `apps/master_ota/src/main.c`. The two differ only by (a) build-time boot profile (`APP_MASTER_BOOT_PROFILE` tag vs anchor) and (b) the embedded OTA payload manifest (`tag_ota_manifest.*` vs `anchor_ota_manifest.*`). So every source change in `main.c` / `master_multi_app.c` affects **both** masters (rows M1–M12); only the OTA-manifest rows (M8 vs M13) are piece-specific.

---

## Scope & method

Diff scope per piece (raw diffs saved to scratchpad; `ota_image.inc`, the 26,934-line generated binary payload, excluded — it is the compiled tag/anchor image and reflects pieces 1 & 2):

| Piece | Files diffed | diff lines | hunks |
|---|---|---|---|
| TAG | `ss_twr_init.c`, `uwb_ss_twr_shared.c`†, `broadcast_tdma.c`†, `apps/tag/{CMakeLists,prj.conf,tag_app.c,uwb_tag_ble.c}`, `apps/tag_usb/CMakeLists` | 5,253 | 155 |
| ANCHOR | `ss_twr_resp.c`, `ss_twr_anchor_init.c`, `uwb_ss_twr_shared.c`†, `anchors/unified/{anchor_ble_ctrl,anchor_ble_id,anchor_cir_output(NEW),anchor_mcumgr_diag}.c`, `apps/anchor/{CMakeLists,anchor_app.c}` | 1,732 | 73 |
| MASTER (shared) | `apps/master_control/{CMakeLists,main.c}`, `apps/master/{CMakeLists,master_multi_app.c,.h}` | 1,147 | 52 |
| OTA manifests | `master_ota/generated/{tag,anchor}_ota_manifest.{h,json}`, `active_ota_payload.json` | 94 | 5 |

† `uwb_ss_twr_shared.c` and `broadcast_tdma.c` are **shared** between TAG and ANCHOR — audited under TAG, noted where the anchor consumes them (rank_offset A4, TX-power-preset A9).

**Class definitions (exactly three):** **KEEP** = proven useful / required, default ON. **RUNTIME-DEBUG** = debug/experiment only, runtime-toggleable, default OFF (or knob locked to production value). **USELESS** = proven no value; remove/revert (dead scaffolding here is already removed by the diff; falsified experiments remain compile-gated OFF).

---

## TASK 2 — Catalog

### Piece 1 — TAG (`ss_twr_init.c` + shared + tag app)

| # | change | files / lines | hot path? | default | evidence / experiment result | CLASS |
|---|--------|---------------|-----------|---------|------------------------------|-------|
| T1 | **P0 TX-RF**: `dwt_configuretxrf()` CH5/PRF64 (PGdly `0xC0`, power `0x25456585`) in `apply_txrf_and_diag()`, called **once** from `configure_radio()` | ss_twr_init.c ~3610–3753 | no (init) | ON | Registers were at silicon POR (never configured). **But** 07-10 `science_audit/AUDIT_REPORT.md` found POR = UM-recommended (PGdly write 0xC0 == POR no-op; Smart-TX 0.0 dB poll/resp diff); ge7 `REGRESSION_REPORT.md` **clears P0 of the regression**; no ranging A/B gain, `logs/p0txrf_bootreadback/` empty. **KEEP on determinism/compliance**, not a proven bugfix. | **KEEP** |
| T2 | **Tail margin `300→800 µs`** (`SS_TWR_INIT_ALT_BCAST_TAIL_MARGIN_US`) | ss_twr_init.c ~2025 | **YES** | ON | Inline: *"300 µs closed the collector window ~235 µs early, so anchor 7 was dropped systematically, capping every sweep at ge7 and making ge8 near-impossible."* The **ge8 enabler**. FREEZE_A18 ge8=0.972. | **KEEP** |
| T3 | Broadcast Alt-SSTWR as CMake defaults: `ALT_SS_TWR_ENABLE`/`BCAST` 0→1, `GUARD_US` 400→1200, `BCAST_FORCE_FULL_SWEEP` 0→1, `LIGHT_TDMA` 0→1 | apps/tag/CMakeLists; ss_twr_init.c | **YES** | ON | Encodes the deployed g1200 broadcast protocol (was -D at 5.28). Required. | **KEEP** |
| T4 | TDMA 10 Hz defaults: `SLOT_PERIOD_MS` 25→10, `SLOT_ACTIVE_MS` 20→9 | apps/tag/CMakeLists | no | ON | Production 10 Hz (overnight-power / position-high). | **KEEP** |
| T5 | Range output: `TR_BCAST_V2_ENABLE` 0→1 (TR;2/TR;3), `BCAST_SUMMARY` 1→0; TR line reformat | apps/tag/CMakeLists; ss_twr_init.c ~2646 | no | ON | TR;2 is the host-parsed production range line. | **KEEP** |
| T6 | `broadcast_tdma` slot-boundary hardening: late-tolerance / min-remain(12ms) / spin-threshold(3ms) + multi-slot mask (SHARED) | broadcast_tdma.c | **YES** | ON | Inline: an 8-anchor sweep can overrun into the next tag on a late wake; start only at an owned boundary with budget left. | **KEEP** |
| T7 | Mode model: RANGE→RUN + IDLE; removed SOLVE/DEBUG/AOTA, ANCHOR_OTA→IDLE | uwb_tag_ble.c; tag_app.c; ss_twr_init.c | no | ON | Current 2-state control model. | **KEEP** |
| T8 | Shared poll-frame **`rank_offset`** plumbing (packed in tag_id byte; 0 in production) | uwb_ss_twr_shared.c (SHARED); ss_twr_init.c | YES (offset 0) | ON | offset=0 → poll byte identical to 5.28. Only CIR-compact rotates it. | **KEEP** |
| T9 | RX collector refactor: `rx_release_frame()`/`rx_recover()` helpers | ss_twr_init.c ~4463–4499 | YES | ON | In baseline (RXAUTR/DBLBUF off, T25) helpers = exact prior behavior. Behavior-preserving. | **KEEP** |
| T10 | `#else` cal_status enum (non-BLE build) + `<strings.h>/<atomic.h>/<base64.h>` includes | ss_twr_init.c ~7–31 | no | ON | Build correctness + CIR/atomic support. | **KEEP** |
| T11 | `CONFIG_BASE64=y` | apps/tag/prj.conf | no | ON | Enables base64 for the (default-off) compact RF-diag trailer. | **KEEP** |
| T13 | **Runtime DIAG gate** — `ss_twr_init_rf_diag_runtime_on` (false) + `DIAG ON/OFF/?` + compile `APP_TAG_RF_DIAG_TAG_RX_ENABLE=0`; double-gates the per-response `dwt_readdiagnostics`+`LDE_THRESH`+`AGC_STAT1` reads | CMakeLists; uwb_tag_ble.c ~1209; ss_twr_init.c ~2188/~4680 | **YES** | **OFF** | **THE dominant ge7 regressor.** ~55–90 µs @8 MHz SPI in the single-buffer collector → drops every other anchor → even/odd 4-valid. **DIAG OFF ge7=0.978/ge8=0.932; DIAG ON ge7=0/ge8=0 {4:1409}.** `REGRESSION_REPORT.md`. | **RUNTIME-DEBUG** |
| T14 | **TXPWR** command → `ss_twr_init_tx_power_apply()` (writes TX_POWER only) + `uwb_tx_power_preset_lookup` (MAX/M3/M6/M12/POR) | uwb_tag_ble.c ~1192; ss_twr_init.c ~2208; uwb_ss_twr_shared.c | no | OFF (→ MAX in prod) | Power **irrelevant**: ge7 flat 0.978 / bias swing 17.7–18.9 mm across full 8.5 dB incl. M12 floor; *"AGC fully normalizes power end-to-end"* (`overnight_power_20260714/REPORT.md`, `overnight_power_position_high_20260715/`). Lock MAX. | **RUNTIME-DEBUG** |
| T15 | CIR mode: `CIR OFF/COMPACT/FULL/?` + `cir_mode_*` (atomic) + CRX/CIRM/CIRD/CIRE publish (chunked `dwt_readaccdata`); deferred single read after collector | uwb_tag_ble.c; ss_twr_init.c; CMakeLists (CIR_* = 0) | YES | **OFF** | CIR capture for imaging/listener-proxy. Read deferred to once-per-sweep after the collector, not per-frame. | **RUNTIME-DEBUG** |
| T16 | RF-diag over-air: `parse_resp_diag_v2`, `publish_rf_diag` (RFD), compact `;D` base64 trailer, TR ver=3 | ss_twr_init.c; CMakeLists (RF_DIAG_* = 0) | YES | **OFF** | Anchor-ΔP diagnostics; runtime-gated by T13. AGC/RF-diag proven non-actionable (`agc=0` placeholder; PROXY_DIAGON gate underpowered). | **RUNTIME-DEBUG** |
| T17 | **Tier-2 phase telemetry (TP)** + tail-RX telemetry (TQ): CPU-cycle hooks in the collector spin + publish; `PHASE_TELEMETRY_ENABLE` **default 1** | ss_twr_init.c ~3860–4175 | **YES (CPU-only)** | **ON** | BLE-phase-beat victim + tail-RX-death instrument (`docs/tier2_phase_telemetry_design_20260627.md`). No SPI; publish sparse (preempt/heartbeat). **OPEN ITEM** — default ON. | **RUNTIME-DEBUG** |
| T18 | BLE TX stats (BSTAT) counters + work; `APP_TAG_BLE_STATS_ENABLE=0` | uwb_tag_ble.c | no | **OFF** | BLE transport telemetry. | **RUNTIME-DEBUG** |
| T19 | Periodic tag temp/vbat (`dwt_readtempvbat` ~30 s) → `;T` TR trailer | ss_twr_init.c ~2614–2705 | no | **ON (unconditional)** | Radio-idle at sweep end, ~1 ms once/30 s. No compile/runtime flag. **OPEN ITEM.** | **RUNTIME-DEBUG** |
| T20 | In `apply_txrf_and_diag`: `EVC_EN` + clear `AGC_CTRL1.DIS_AM` (so AGC_STAT1 populates) + evc/temp helpers + boot readback log | ss_twr_init.c ~3581–3694 | no (init) | ON | The `DIS_AM` clear is the **genuine functional correction** in the P0 bundle (else `agc_stat1`=0). One-time, benign. **OPEN ITEM** (keep or strip). | **RUNTIME-DEBUG** |
| T21 | Boot OTP full dump (`OTP_DUMP`/`OTP_DECODE`) with spi-slow/fast wrap | tag_app.c ~291–334 | no | OFF (gated `APP_TAG_OTP_DIAG`) | Boot-only calibration readback. | **RUNTIME-DEBUG** |
| T22 | **REMOVED WAND mode** (~600 lines): BLE WAND cmds, wand ranging (set_enabled/role/peers/sweep/responder/publish), state, WAND_* macros | uwb_tag_ble.c ~957–1165; ss_twr_init.c ~3236–3705 | no | removed | Dead wand-DOA scaffolding (coherent-wand grating-limited). Already removed. | **USELESS** |
| T23 | **REMOVED fixed-anchor subset**: FIXED_MODE/FIXED_ANCHOR_*, parse_fixed_anchor_list, state + validation → forced DYNAMIC_2P2 | CMakeLists×2; tag_app.c; uwb_tag_ble.c; ss_twr_init.c | no | removed | Superseded by dynamic 2+2. Already removed. | **USELESS** |
| T24 | **REMOVED calibration modes**: CALIBRATION_MODE, CAL_STATIC/CAL_ROTO selection (static_cal_group, tetra_volume, roto_balanced, prewarm, cursors, plan codes) | tag_app.c; uwb_tag_ble.c; ss_twr_init.c ~2648–2952 | no | removed | Dead cal flows. Already removed. Master side removes matching profiles (M11). | **USELESS** |
| T25 | RXAUTR / RXDBLBUF experiments (`BCAST_RXAUTR_ENABLE=0`, `BCAST_RXDBLBUF_ENABLE=0`) + dblbuf/HRBPT plumbing | ss_twr_init.c ~4381–4499 | YES (gated off) | **OFF** | Both **FALSIFIED** in-code: single-buf RXAUTR = all 6 tags rank-0; RXAUTR+dblbuf = RXOVRR 26–85%, not a win. Inert (compile-gated 0). Not runtime-toggleable → USELESS, but zero-risk to leave. | **USELESS** |

### Piece 2 — ANCHOR (`ss_twr_resp.c` + `ss_twr_anchor_init.c` + unified + anchor app)

| # | change | files / lines | hot path? | default | evidence / experiment result | CLASS |
|---|--------|---------------|-----------|---------|------------------------------|-------|
| A1 | **P0 TX-RF** `dwt_configuretxrf()` (0xC0 / 0x25456585) in `ss_twr_resp_apply_txrf_and_diag()` **and** `ss_twr_anchor_init_apply_txrf_and_diag()`; once per `configure_radio()` | ss_twr_resp.c ~1468; ss_twr_anchor_init.c ~905 | no (init) | ON | *"MUST program the SAME value as the tag (common-mode symmetry)."* Same benign-tidy caveat as T1. Anchor does **not** clear DIS_AM (configuretxrf + EVC_EN only). | **KEEP** |
| A2 | Responder timing baseline: `RESP_DELAY_UUS` 500→1200, `GUARD_US` 500→1200, `RESP_SPACING_US` 800→1000, `ALT_SS_TWR`/`BCAST` 0→1; TAIL_COMPRESS stays 0 | apps/anchor/CMakeLists; ss_twr_resp.c | **YES** | ON | g1200-r1000 = deployed a18/fixeda19 timing (FREEZE_A18). 5.28 used tail900-start5; production reverted to no-compression. | **KEEP** |
| A3 | `RESPONDER_BLUE_LED_ENABLE` 0→1 (pulse on responder TX) | apps/anchor/CMakeLists; ss_twr_resp.c | no | ON | Visual responder-activity liveness. | **KEEP** |
| A4 | `ss_twr_resp_rank_from_offset()` — honor poll `rank_offset` (pairs with T8) | ss_twr_resp.c ~1366/~1579 | **YES** | ON (offset 0 = normal rank) | Backward-compatible; offset 0 → same rank as before. | **KEEP** |
| A5 | Response TX buffer → V3 (36 B); `resp_frame_len` defaults V1 (20 B); `writetxdata/fctrl` use it | ss_twr_resp.c ~248/~1566/~1670 | **YES** | ON (V1 20 B on air) | Production DIAG_V2 off → 20 B transmitted = a18 on-air frame (identical airtime). | **KEEP** |
| A6 | Response TS-index macros → shared `UWB_MSG_RESP_*` (values 10/14/4 unchanged) | ss_twr_resp.c ~158 | no | ON | De-dup refactor. | **KEEP** |
| A7 | **V2/V3 diag payload + fixeda19 deferral** knobs (`RESP_PAYLOAD_DIAG_V2_ENABLE`, `SKIP_RANK0`, `RANK0_FAST_TX`, `POST_TX_DIAG_READ`, `POST_TX_DIAG_PAYLOAD_DELAY`, `POST_TX_DIAG_SIDECHANNEL`) + `write_diag_v2` + pipelined post-TX read | apps/anchor/CMakeLists; ss_twr_resp.c ~833/~1219/~1367 | **YES** | source **OFF**; deployed marker = **ON** | **THE fixeda19 fix** (secondary ge7 regressor). *"NEVER read diagnostics before starttx … moved AFTER starttx, pipelined."* PROXY_DIAGON_A19 + REGRESSION_REPORT. **Polarity split** — see hot-path note. | **RUNTIME-DEBUG** |
| A8 | Anchor CIR output: **NEW** `anchor_cir_output.{c,h}` (ACRX/ACIRM/ACIRD/ACIRE, atomic mode); post-TX read at reply + matrix-init; `CIR_* = 0` | anchor_cir_output.* (NEW); ss_twr_resp.c ~1458; ss_twr_anchor_init.c ~1156 | YES | **OFF** | CIR capture (listener-proxy/imaging). Responder read is post-TX (no deadline). | **RUNTIME-DEBUG** |
| A9 | TXPWR command → `ss_twr_anchor_init_tx_power_apply()` + BLE-ctrl `TXPWR` | ss_twr_anchor_init.c ~939; anchor_ble_ctrl.c ~506 | no | OFF (→ MAX) | Same power-irrelevance as T14; anchors locked MAX. | **RUNTIME-DEBUG** |
| A10 | Responder temp/vbat periodic sample (into V3 payload) | ss_twr_resp.c ~1523/~1656 | no | OFF (gated DIAG_V2) | Before RX armed; no prod effect. | **RUNTIME-DEBUG** |
| A11 | `full_cir_quiet()` console suppression across 6 files; smp/img cb early-return | anchor_app.c, ble_ctrl.c, ble_id.c, mcumgr_diag.c, ss_twr_anchor_init.c, ss_twr_resp.c | no | ON (only bites when CIR FULL set) | Keeps raw-CIR hex dump clean. No effect unless CIR FULL (A8). | **RUNTIME-DEBUG** |
| A12 | `EVC_EN` in both apply paths + boot txrf readback log | ss_twr_resp.c; ss_twr_anchor_init.c | no (init) | ON | Diagnostic-support with A1; one-time. | **RUNTIME-DEBUG** |
| A13 | Matrix/AutoPos initiator: **unconditional** `dwt_readcarrierintegrator`+`dwt_readdiagnostics` to feed the (gated) CIR publish | ss_twr_anchor_init.c ~1101 | no (AutoPos only) | ON (unconditional) | Runs only in matrix/AutoPos (no delayed-TX deadline), **not** the responder production path. Slightly wasteful when CIR off. **OPEN ITEM.** | **RUNTIME-DEBUG** |

### Piece 3 — MASTER_TAG (shared `master_control`/`master_multi_app` + tag OTA payload)

Rows M1–M12 are **shared with MASTER_ANCHOR** (same carrier source). M8 is MASTER_TAG-specific.

| # | change | files / lines | hot path? | default | evidence / experiment result | CLASS |
|---|--------|---------------|-----------|---------|------------------------------|-------|
| M1 | TDMA 10 Hz defaults: `SLOT_PERIOD` 40→10, `SLOT_ACTIVE` 24→9, `MOTION_HZ` 5→10, `EPOCH_LEAD_MS` 3000→5000 (now cfg) | master*/CMakeLists; master_multi_app.c | no† | ON | Production 10 Hz; matches tag 10×9. †Master is BLE central, **not** in the UWB ranging path. | **KEEP** |
| M2 | **Deterministic reference-slot table** (`master_tdma_reference_slots`: the 6 BS codes → fixed tag-id + slot) when explicit-roster + 10 Hz + all-known | master_multi_app.c ~152/~1470 | no | ON | Stable slots for the 6 tags → avoids BLE-phase-beat reshuffle; the deterministic complement to reroll (M4). | **KEEP** |
| M3 | Auto-roster toggle (`tdma_auto_roster_enabled`, `set_auto_roster`, `tdma auto`/`clear`, roster-hold gating) | master_multi_app.c/.h; main.c | no | ON | Explicit vs auto-all-ready roster control. | **KEEP** |
| M4 | **`reroll <BSxxxx>`** — disconnect one tag to re-randomize its BLE↔UWB phase | main.c ~2576 | no | ON | Targeted anti-victim phase reroll (FREEZE_4PIECE_20260628); conn sweep proved the victim is phase-determined + reshuffleable. | **KEEP** |
| M5 | BLE conn params configurable: `INTERVAL_UNITS=6` (7.5 ms), `LATENCY=0`, `TIMEOUT=400` | master_multi_app.c ~97; master_control/CMakeLists | no | ON (7.5 ms) | Baseline 7.5 ms. In-code comment: CONN-INTERVAL SWEEP 06-28 **FALSIFIED** (15 ms reshuffles victims, 30 ms worse). Knob retained; baseline is the KEEP value. | **KEEP** |
| M6 | CFG_OK parse handles optional `ACTIVE_US` (two-format fallback) | master_multi_app.c ~2292 | no | ON | Parse robustness. | **KEEP** |
| M7 | Background-gate + `ota_transition_active` resets in mode/autopos handlers | main.c ~2809 | no | ON | Control-plane robustness. | **KEEP** |
| M9 | **MSTAT** BLE stats + **MCLK** clock-cal telemetry (`ble_stats_work`, 5 s; MCLK gated `CONFIG_CLOCK_CONTROL_NRF_DRIVER_CALIBRATION`) | master_multi_app.c ~2186/~3204 | no | **ON (5 s)** | Master-side telemetry (not in ranging path). Harmless. MCLK = LFRC-recal drift proxy (G0.2). **OPEN ITEM.** | **RUNTIME-DEBUG** |
| M10 | CIR passthrough: `tag cir …`, `autopos cir`, `anchor role … cir`; `normalize_anchor_cir_mode`; `CIR=` appended to RUNTIME cmds | main.c ~331/~2408/~2680 | no | OFF | Host control for tag/anchor CIR (T15/A8); no effect unless CIR commanded. | **RUNTIME-DEBUG** |
| M11 | **REMOVED STATIC/ROTO TDMA profiles** (enum, static/roto hz, cal pmode, parse, DEFAULT_HZ) | master*/CMakeLists; master_multi_app.c | no | removed | Dead cal profiles (matches T24). Motion-only. Already removed. | **USELESS** |
| M12 | **REMOVED `ble_decode_cal_packet()`** (CM cal decode) | master_multi_app.c ~1931 | no | removed | Dead CM path. Already removed. | **USELESS** |
| M8 | Embedded **tag** OTA payload → `unified-diaggate-p0txpwr-20260714` (`tag_ota_manifest.{h,json}`, `active_ota_payload.json`, `ota_image.inc`) | master_ota/generated | no | ON | Auto-generated record of the tag image the Master_Tag carrier embeds. Reflects Piece 1. | **KEEP** |

### Piece 4 — MASTER_ANCHOR (shared carrier + anchor OTA payload)

Inherits shared rows **M1–M7, M9–M12** (same carrier source). Piece-4-specific:

| # | change | files / lines | hot path? | default | evidence | CLASS |
|---|--------|---------------|-----------|---------|----------|-------|
| M13 | Embedded **anchor** OTA payload → `altbcast-fixeda19-p0txpwr-g1200-r1000` (`anchor_ota_manifest.{h,json}`) | master_ota/generated | no | ON | Auto-generated record of the anchor image the Master_Anchor carrier embeds. Reflects Piece 2. Baseline embedded `us-hc-exp2`. | **KEEP** |
| — | Boot profile = anchor (`APP_MASTER_BOOT_PROFILE`) — build-time config, not in the source diff | (build flag) | no | — | Distinguishes MASTER_ANCHOR from MASTER_TAG at build time. | — |

---

## TASK 3 — Cross-piece summary

### Count per class per piece

| Piece | KEEP | RUNTIME-DEBUG | USELESS | total |
|---|---:|---:|---:|---:|
| TAG | 11 (T1–T11) | 9 (T13–T21) | 4 (T22–T25) | 24 |
| ANCHOR | 6 (A1–A6) | 7 (A7–A13) | 0 | 13 |
| MASTER_TAG | 8 (M1–M8) | 2 (M9–M10) | 2 (M11–M12) | 12 |
| MASTER_ANCHOR | 8 (M1–M7, M13) | 2 (M9–M10) | 2 (M11–M12) | 12 |
| **distinct changes** | **26** | **18** | **6** | **50** |

(MASTER_TAG and MASTER_ANCHOR share M1–M7, M9–M12; only M8 vs M13 differ — so the two master columns overlap on 11 of 12 rows.)

### RED / UNEXPLAINED diff lines (unauthorized changes)

**None.** Every raw hunk across all four diffs maps to exactly one catalog change. The only non-functional lines are cosmetic tab/space reindentation in `ss_twr_init.c` (`print_location_if_ready`, `start_with_config`) that is adjacent to real edits (T7/T24 removals) — attributed, not RED. No mystery register writes, no unattributed timing constants, no unexplained struct/field additions.

### KEEP items that touch the ranging hot path (freeze must preserve their exact timing)

| id | piece | hot-path element |
|---|---|---|
| T2 | TAG | tail margin 800 µs (collector window close) — **ge8 enabler** |
| T3 | TAG | broadcast/g1200 guard + full-sweep + light-TDMA |
| T6 | TAG | slot-boundary start gating (late-tolerance / min-remain / spin) |
| T8 / A4 | TAG+ANCHOR | rank_offset poll-frame plumbing (0 in prod = byte-identical) |
| T9 | TAG | RX collector release/recover refactor (baseline-equivalent) |
| A2 | ANCHOR | responder g1200-r1000 delayed-TX timing |
| A5 | ANCHOR | response frame length (V1 20 B in prod) |
| T1 / A1 | TAG+ANCHOR | `dwt_configuretxrf` — **init-only**, not per-frame (listed for completeness) |

### Is the frozen tag ranging hot path timing-clean = 5.28-good + only KEEP additions?

**Yes for the regression, with two intentional KEEP timing changes and one default-ON CPU-only diagnostic to confirm.**

With all RUNTIME-DEBUG defaulted OFF (production freeze config):

- **No per-frame SPI diag reads.** The tag-side `dwt_readdiagnostics`/`LDE_THRESH`/`AGC_STAT1` reads (T13/T16) are **double-gated** (compile `APP_TAG_RF_DIAG_TAG_RX_ENABLE=0` **and** runtime `rf_diag_runtime_on=false`) → they **compile out**. CIR reads (T15) are OFF (atomic mode = OFF) and, even when on, are deferred to a single read after the collector. → The tag collector is byte-for-byte the stable **nodiag** SPI sequence that holds ge7/ge8 ≈ 0.978/0.93.
- **Anchor responder** in the source-default config sends the **V1 20-byte frame with no pre-TX diag read** = the a18 good-timing baseline. (See the polarity note below for the deployed `fixeda19` build.)
- **Two deliberate KEEP timing deltas vs the raw 5.28 collector** — these are *why ge8 now works*, not regressions: **T2** tail margin `300→800 µs` (5.28 dropped anchor-7 systematically → ge8 impossible) and **T6** slot-boundary hardening (prevents cross-tag overrun). The freeze must preserve these exact values.
- **One default-ON, CPU-only diagnostic:** **T17** phase telemetry adds `k_cycle_get_32()` reads + integer compares to the collector busy-wait (already a spin loop) — **no SPI**, publishes only on preemption/heartbeat. Strictly it makes the hot path *not byte-identical*, but the added cost is negligible. **Confirm keep-on or gate** (open item).

So: **the frozen tag hot path is clean of the DIAG regression and matches the nodiag timing, plus the two KEEP tail/slot fixes, plus a negligible default-on CPU telemetry hook.** It is intentionally *better* than raw-5.28 (ge8 fixed), not equal to it.

### Anchor `fixeda19` polarity — a real freeze decision (open item A7)

The deployed production anchor marker `altbcast-fixeda19-p0txpwr` is **built with the deferral flags = 1** (`RESP_PAYLOAD_DIAG_V2_ENABLE` + `POST_TX_DIAG_READ` + `POST_TX_DIAG_PAYLOAD_DELAY`), i.e. **V3 36-byte response with post-TX-deferred diag** (adds anchor ΔP, timing-safe). But the **source `#ifndef` defaults are 0** (V1 20-byte, no diag = strict a18 airtime). Both are validated ge7 ≥ 0.96 because the deferred read happens after `dwt_starttx`. **The freeze must pick one** and set the build flags explicitly: keep `fixeda19` V3 (current, retains anchor ΔP) or drop to pure a18 V1 (no ΔP). Either way the **tag DIAG gate (T13) must stay OFF** — the 07-14 collapse was tag `TAG_RX_ENABLE=1` while the anchor deferral flags were simultaneously 0.

### Open items for operator review (before building the freeze)

1. **T17** phase telemetry (TP/TQ): default ON, CPU-only in the collector. Confirm keep-on (negligible) or gate behind DIAG.
2. **T19** tag temp/vbat `;T` trailer: **unconditional** (no flag), ~1 ms once/30 s at radio-idle. Confirm keep or gate.
3. **T20 / A12** `EVC_EN` + `AGC_CTRL1.DIS_AM` clear: one-time diagnostic-support bundled with the required P0 write. The `DIS_AM` clear is the genuine functional correction in the bundle. Keep (benign) or strip for a strictly-minimal radio config.
4. **A13** matrix/AutoPos unconditional `dwt_readdiagnostics`: AutoPos-only (no ranging deadline). Keep or gate.
5. **T14 / A9 TXPWR**: keep as a diagnostic knob but **lock to MAX** in production (power proven irrelevant to accuracy).
6. **T25** RXAUTR/RXDBLBUF: inert falsified experiments (compile flags 0). Zero-risk to leave (documented dead-ends) or strip; not runtime-toggleable.
7. **P0 TX-RF (T1/A1)**: two prior audits disagree on severity; the ge7 report clears it of the regression and no ranging gain / boot-readback log exists. Recommend KEEP on determinism grounds but record as *benign-tidy, not a proven bugfix*; optionally capture a boot-readback log during the freeze build (`logs/p0txrf_bootreadback/` is currently empty).
8. **Anchor `fixeda19` polarity (A7)** — pick V3-deferred (flags=1, current) vs V1 (flags=0) explicitly at build.

### APS011 check (task asked to flag any remnants)

**None.** APS011 range-bias (`dwt_getrangebias`) was added and fully rolled back **entirely after** the 5.28 baseline (07-11 experiment), so it nets to zero in this diff. Grep confirms **zero call sites** in `ss_twr_init.c` or the listener; only the unused library table `deca_range_tables.c:636` remains (no callers). It was proven to make ranging **worse** (LOO 158→251 mm, +454 mm common-mode overshoot; DEAD END — `analysis/aps011_rsl_recomputation/REPORT.md`, `analysis/experiment_summary_20260712/SUMMARY.md`). No USELESS remnant to remove in the four pieces.

---

## Evidence sources cited

- ge7/ge8 DIAG regression + fix, exact A/B numbers: `experiments/power_campaign_20260714/ge_regression/REGRESSION_REPORT.md` (+ `results.json`, `SESSION_20260714_HANDOFF.md`)
- Anchor fixeda19 root cause (pre-TX `dwt_readdiagnostics` squeezes delayed-TX): `PROXY_DIAGON_A19_HANDOFF_20260630.md`
- a18 8/8 verified ge7=0.978/ge8=0.972: `FREEZE_A18_REVERIFIED_8ANCHOR_20260701.md`; predecessor `FREEZE_4PIECE_20260628.md`
- Power irrelevance + AGC normalization: `logs/overnight_power_20260714/REPORT.md`, `logs/overnight_power_position_high_20260715/{REPORT,COMPARISON}.md`
- P0 TX-RF severity (conflicting): `DW1000_REGISTER_AUDIT_20260706.md` (CRITICAL) vs `logs/science_audit_20260710/AUDIT_REPORT.md` (downgraded); tail-margin/RXAUTR/reroll: in-code comments + `docs/tier2_phase_telemetry_design_20260627.md`
- APS011 dead end: `analysis/aps011_rsl_recomputation/REPORT.md`, `logs/geiger_scan_20260711_post_aps011/APS011_FIELD_TEST_SUMMARY.md`, `analysis/experiment_summary_20260712/SUMMARY.md`
- Anchor/tag/master baselines: `BIOSPUR_USABLE_FIRMWARE_VERSIONS.md`, `BROADCAST_BASELINE_FREEZE.md`

---

## TASK 4 — No modifications made

This is an audit only. No code was changed, nothing was built, nothing was flashed. Awaiting operator confirmation of the class of each change (and resolution of the 8 open items above) before building the freeze image for the four pieces.

---

## ADDENDUM 2026-07-15 — OTA-blocker audit: preflight + corrected firmware laws

Added by the OTA-blocker deep audit (full report: `experiments/ota_blocker_audit/OTA_BLOCKER_REPORT.md`). Read-only; all claims code-anchored (file:line relative to the broadcast tree root). This addendum corrects two beliefs from the 2026-07-15 freeze/OTA incident.

### Corrections to the incident conclusions

- **`MODE IDLE` is NOT required before OTA.** `OTA_PREPARE` (`apps/tag/src/uwb_tag_ble.c:2006`) alone purges the tag TX queue and blocks all streaming via `ota_active` (`:1266`, checked at `:1281/1499/2127/2219/2279`), mode-independently. Sending `MODE IDLE` first is vestigial and persists a stopped state to tag NVS. `scripts/ota_single_tag_stable.py:576` sends a needless pre-OTA `cmd MODE IDLE` — recommend removing.
- **A "full chip erase" is NOT necessary to clear a zombie master mode.** `control_mode` is never written to flash — there is no `settings_save()` in the master sources (only `autopos_target`/`autopos_map_*` via `settings_save_one`, `main.c:679/692`). Boot mode = a `__noinit` warm-reboot cookie (`main.c:79-80`, `:3215`) + the compile-time boot profile (`control_apply_boot_profile`, `:435`, called `:3337`). **Fix a zombie mode with the correct `APP_MASTER_BOOT_PROFILE` + a power cycle**, not an erase. (Full erase only clears the power-persistent `autopos_target`/`autopos_map_*` stale-sweep NVS + stale anchor configs — neither locks an OTA.)
- **The only hard OTA-lock is a tag HELD CONNECTED by a master** (a connected BLE peripheral stops advertising). Persisted `MODE IDLE`, a forgotten `oneshot`, and a dirty capture exit all leave the tag **still advertising** (advertising is unconditional, `uwb_tag_ble.c:1017/1368`) → recoverable, not a lock. A `MASTER_TARGET_ANCHOR` (AUTOPOS) carrier *rejects* wand tags (`master_multi_app.c:2856`) — which is why the `"anchor"` boot profile is the correct fix for the Master_Anchor.

### OTA PREFLIGHT checklist

1. Inventory masters — only the OTA master may own the tag target-kind.
2. On every **non-OTA** master: `oneshot show` → `oneshot clear`, then `scan` (releases tags + stops auto-connect, `main.c:2608`) **or power it down**; confirm via `status` it holds no tags.
3. On the OTA master: `scan`, confirm SCAN hits / `device show` for **all** target BS* tags. A missing tag is still held → resolve first.
4. On the OTA master: `oneshot show` → `oneshot clear`.
5. `device kind tag|anchor` → `ota_target …` → `conn` → `mode ota`/`initiate`. **Do NOT send `MODE IDLE`.**
6. Wrong boot mode → **power-cycle** and check `status`; if still wrong, reflash with the correct `APP_MASTER_BOOT_PROFILE`. **No full erase for mode.**
7. Post-OTA: verify tags boot advertising + ranging; if a tag boots IDLE, `cmd_all MODE RUN`.

### Corrected firmware laws (code-anchored)

1. **OTA never needs `MODE IDLE`** — `OTA_PREPARE` quiesces the tag (`uwb_tag_ble.c:2006`, gate `:1266`).
2. **Tags always advertise regardless of mode** (`uwb_tag_ble.c:1017/1368`); the only hard OTA-lock is a master holding the tags.
3. **`control_mode` is not in flash** — `__noinit` warm cookie + compile-time boot profile only; **a full erase cannot change mode.** Fix with the right `APP_MASTER_BOOT_PROFILE` + power cycle.
4. **Master identity is the boot profile** — `"anchor"` rejects wand tags (`master_multi_app.c:2856`); `"tag"`/`"neutral"` can grab them; exactly one master owns the tag kind at a time.
5. **Leave tags in RUN** — persisted `MODE IDLE` silently stops ranging (`uwb_tag_ble.c:809-822`, NVS `:706`); capture/quarantine/OTA scripts must restore `MODE RUN` on every exit path (none currently do — see report §Q5).

*Addendum is documentation only. No code changed, nothing built, nothing flashed.*
