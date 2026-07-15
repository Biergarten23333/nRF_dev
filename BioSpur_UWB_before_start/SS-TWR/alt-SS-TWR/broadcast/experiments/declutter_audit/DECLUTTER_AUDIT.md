# Deployment Declutter Audit — Command Surface + Output Surface

**Date:** 2026-07-15 · **Type:** read-only survey + classification (no build/flash/delete/code-change)
**Anchor:** runs on the `freeze-4piece-<date>` baseline; this plans the cleanup that will produce `freeze-clean`.
**References:** `docs/COMMAND_REFERENCE.md` (command surface), `experiments/firmware_freeze_audit/AUDIT.md` (firmware-change classification), `experiments/ota_blocker_audit/OTA_BLOCKER_REPORT.md`.
**Path convention:** `file:line` relative to the broadcast tree root `/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast`.

**Buckets:** **KEEP** (production and/or reserved-for-fusion, default active) · **KEEP-DEBUG** (no production use but useful for bug-chasing; runtime-gated, default OFF, not hot-path; includes TOMBSTONES) · **DELETE** (proven-dead residue). **R1: DELETE requires proof of death** — (a) zero host callers/parsers, (b) no firmware counterpart, (c) not in any CMakeLists, (d) git shows *superseded/replaced*, not "not yet wired." Incomplete proof → downgrade to KEEP-DEBUG. **R2:** dead code carrying a falsified-experiment lesson → TOMBSTONE (keep). **R3:** an emitted output no host parser consumes is a DELETE candidate unless explicitly reserved for fusion.

---

## 0. One-page decoder — "what each mystery token means"

| token | meaning | emitted by | when | verdict |
|---|---|---|---|---|
| **`TR;1`** | **Does NOT exist in current firmware.** Historical pre-broadcast range format. | — | never (gone) | n/a — stale memory |
| **`TR;2`** | **THE production range line.** 10 fields: `TR;2;sweep;plan;pmode;active_mask;valid_mask;raw_csv;range_csv;quality_csv;status_codes` | `ss_twr_init.c:1316` (ver=2 branch, :1322) | `TR_BCAST_V2_ENABLE=1 & RF_DIAG_OUTPUT=0` (production) | **KEEP** |
| **`TR;3`** | **OVERLOADED — two layouts.** (a) production build: `TR;2` base **+ `;D1,` compact RF-diag trailer** (DIAG on); (b) legacy `#else` build: 14-field with `qf;first_to_last_us;frame_us;poll_count`. In production always (a). | (a) `ss_twr_init.c:1320`; (b) `:1338`+`:256` | (a) `RF_DIAG_OUTPUT=1`; (b) `TR_BCAST_V2_ENABLE=0` | **KEEP-DEBUG** + **contract hazard** (§3) |
| **`TR;4`** | Legacy `#else` **with IMU** (14-field). Non-production. | `ss_twr_init.c:1338`+`:254` | `TR_BCAST_V2_ENABLE=0 & IMU on` | **KEEP-DEBUG / tombstone** |
| **`;T,<die>,<vbat>`** | Tag **temperature** (SAR raw 0-255) + **battery** (SAR raw), appended to every TR. | `ss_twr_init.c:1418` | unconditional (`tag_temp_valid`) | **KEEP** |
| **`;D1,<base64>`** | **Compact per-anchor RF diagnostics** (8 bytes/anchor: flags, fp_sum_q8, cir_pwr_q8, rxpacc_q8 for anchor+tag), base64. **It is RF-diag, NOT CIR.** | `ss_twr_init.c:1203` | `RF_DIAG_OUTPUT & TR_RF_DIAG_COMPACT` | **KEEP-DEBUG** |
| **`;I,…` / `;R,…`** | IMU rolling-|a| **summary** / raw **XYZ** trailers. | `ss_twr_init.c:1367` / `:1402` | `TR_IMU_SUMMARY` / `TR_IMU_RAW` (IMU builds) | **KEEP-DEBUG** |
| **`TP;1;…`** | **Phase telemetry** — CPU-cycle timing of the collector spin (BLE-phase-beat victim instrument, 9 fields). | `ss_twr_init.c:3672` | `PHASE_TELEMETRY_ENABLE` (**default 1 = ON in production**) | **KEEP-DEBUG — GATE OFF** (§4) |
| **`RFD;1;…`** | Verbose full per-anchor RF-diagnostics line (the un-compacted `;D1`). | `ss_twr_init.c:3217` | `RF_DIAG_OUTPUT` | **KEEP-DEBUG** |
| **`CRX;1` / `ACRX;1`** | Compact CIR **features** (first-path amp etc.), one line per anchor (tag/anchor). Reserved for fusion pair-weights. | `ss_twr_init.c:761` / `anchor_cir_output.c:133` | `CIR_FEATURE_OUTPUT` | **KEEP** (reserved) |
| **`CIRM/CIRD/CIRE;1` / `ACIRM/D/E;1`** | **Full CIR** accumulator dump: header / hex-chunks / end. Reserved for imaging + fusion R-matrix. | `ss_twr_init.c:888/906/920` / `anchor_cir_output.c:201/226/243` | `CIR_FULL_OUTPUT` | **KEEP** (reserved) |
| **`TS;1` / `TagSummary`** | **On-tag SOLVE** position summary. Production solves host-side (from TR), so off. | `ss_twr_init.c:4430` | `POSITION_OUTPUT & BLE_COMPACT_STATUS` | **KEEP-DEBUG** |
| **`SW-<x>,…`** | Anchor AutoPos per-sweep matrix result stream. | `ss_twr_anchor_init.c:85` | AutoPos/matrix role | **KEEP** |
| **`MSTAT` / `MCLK`** | Master BLE-stats / LFRC clock-cal telemetry (5 s). | `master_multi_app.c:2209 / :2224` | always (master console) | **KEEP-DEBUG** |
| **`L*` (LPD/LRD/LCIR*/LSTAT/LTAG)** | Listener device outputs (separate diagnostic hardware). | `UWB_listener/src/main.c:375/410/460/701/775` | listener build flags | **KEEP** (listener contract) |

---

## Part 1 — COMMAND SURFACE (bucketed)

Buckets applied to `docs/COMMAND_REFERENCE.md`. Full per-command detail lives there; this focuses on the classification and the DELETE proofs.

| command (piece) | bucket | evidence (file:line) | if DELETE — what breaks |
|---|---|---|---|
| TAG `CFG`, `MODE`/`MODE?`, `TDMA_SET`, `CFG_RUN/STOP`, `APOS*`, `CFG_STATUS`, `REBOOT`, `VERSION`, `HELP`, `PING`, `STATUS`, `OTA_STATUS/PREPARE/BEGIN/CANCEL` | **KEEP** | production control + OTA path (uwb_tag_ble.c dispatcher 1540-2061) | core — ranging/config/OTA stop working |
| ANCHOR `R/L/G`, `PENDING *`, `VALIDATE`, `COMMIT/APPLY`, `RESET AUTOPOS/RESPONDER`, `RUNTIME …`, `REBOOT`, `STOP`, `DFU`, `SYNC`, `VERSION`, `HELP`; UART `ROLE?/STATUS/M/X/P/ID/ANCHOR SET/SAVE/RB` | **KEEP** | anchor_ble_ctrl.c 466-731; uart_role_switch.c 320-394 | provisioning/role/OTA break |
| MASTER `status/scan/conn/reroll/mode/device/tdma */autopos */anchor */ota_target */initiate/ota_reset`, `cmd/cmd_all/oneshot/APOS*` | **KEEP** | main.c 2337-3133 | capture/OTA/AutoPos orchestration break |
| LISTENER `MODE_TAG/LISTEN/IDLE/QUERY` | **KEEP** | UWB_listener/src/main.c:960 | listener calibration breaks |
| TAG `TXPWR`, `DIAG`, `CIR`, `TAG CIR` | **KEEP-DEBUG** | uwb_tag_ble.c:1604/1627/1650; default OFF (radio reg / atomic) | debug knobs only; production locks MAX/OFF |
| ANCHOR `TXPWR`, `CIR=` (RUNTIME sub-arg), `US?/USON/USOFF` | **KEEP-DEBUG** | anchor_ble_ctrl.c:536/582/471; default OFF/US-disabled | debug/ultrasound knobs |
| MASTER `tag cir`, `autopos cir`, `MSTAT`(print) | **KEEP-DEBUG** | main.c:2411/2866; master_multi_app.c:2209 | CIR debug control |
| LISTENER b120 probes (`status`,`echo`,`ota version`,`\n`) | **KEEP-DEBUG** | b120_cdc_probe/main.c:42-49; b120_ble_probe/main.c:50 | bring-up probes; not in ranging fleet |
| **`STREAM OFF` / `STREAM 0` / `STREAMON 0`** (host senders) | **DELETE** (Batch 2) | **no tag handler** (uwb_tag_ble.c:2060 → `UNKNOWN_CMD`); only build-flag `APP_TAG_STREAM_FORCE_OFF_AT_BOOT` exists; senders `run_autopos_sweep_loop.py:2405-2407`, quarantine | **nothing** — already no-ops; scripts fall back to `MODE IDLE`/`tdma clear` |
| **`cmd_all MODE AOTA`** (host sender) | **DELETE** (Batch 2) | `AOTA` **removed** from tag mode model (AUDIT T7; absent from parser 771-801); sender `run_recv_tdma_capture.py:2415/2454` | **nothing** — no-op (`MODE_BAD`); paired with `MODE IDLE`/`tdma clear` that does the stop |
| **`TDMA_STATUS`** (tag handler, no sender) | **DELETE-candidate** (Batch 3, operator confirm) | orphan handler uwb_tag_ble.c:1585; zero senders (git: 1 commit, never wired); redundant with `MODE?`/`CFG_STATUS` | nothing (unreachable); R1(d) tension — "added-then-forgotten," not named-supersession → operator rules |
| **`MMOT`** (tag handler, no sender) | **DELETE-candidate** (Batch 3) | orphan alias for `MODE RUN` uwb_tag_ble.c:1842/1851; **mis-parses `MMOT<suffix>`** (footgun); zero senders | nothing (unreachable); recommend DELETE (footgun + exact dup of `MODE RUN`) |
| **`apps/master/src/master_app.c`** (whole file) | **DELETE** (Batch 1) | not in any CMakeLists (apps/master/CMakeLists.txt:50-51 compiles `main.c`+`master_multi_app.c`); superseded single-conn demo | nothing — never compiled/linked |
| **`src/uwb_control_proto.c`** (whole file) | **KEEP-DEBUG / reserved (AMBIGUOUS)** | not in any CMakeLists, **zero callers** — but "never wired," **not superseded** → R1(d) fails → do NOT delete. Reserved UWB-airframe command skeleton for the planned BLE-removal migration (BLE_COMMAND_PATH_AUDIT §2.6) | nothing today; **DELETE only if operator confirms BLE-removal is abandoned** (present both readings) |
| `TDMA_SET` vs `CFG SLOT=` (duplicate?) | **KEEP-DEBUG** | both have handlers (uwb_tag_ble.c:1896 / :1937); `TDMA_SET` is the documented manual single-slot override, `CFG` the master path | not a duplicate to delete — manual override retained |

---

## Part 2 — OUTPUT SURFACE (the new inventory, bucketed)

Every telemetry line the four pieces can emit. **Triggering flag** = the compile/runtime gate. **Host parser** = the script that *interprets* content (raw-logging excluded). Production defaults from `apps/tag/CMakeLists.txt` (V2=1, RF_DIAG=0, CIR=0, IMU=0) and in-file defaults.

### TAG (BLE NUS notify / USB)

| line type + version | grammar | triggering flag | host parser (file:line) | bucket | evidence |
|---|---|---|---|---|---|
| **`TR;2;…`** (production) | `TR;2;sweep;plan;pmode;active_mask;valid_mask;raw_csv;range_csv;quality_csv;status_codes` (+`;T`) | `TR_BCAST_V2_ENABLE=1`+`RF_DIAG_OUTPUT=0` → ver 2 (ss_twr_init.c:1316-1322) | `run_recv_tdma_capture.py:41/62`, `experiments/run_overnight_power.py:111` | **KEEP** | the production range line; parsed by the main capture driver |
| **`;T,<die>,<vbat>`** | temp+vbat SAR-raw trailer on every TR | unconditional (ss_twr_init.c:1418) | `run_recv_tdma_capture.py:132`, `run_overnight_power.py:130` | **KEEP** | production trailer; parsed + validated 0-255 |
| **`TR;3` prod / `;D1,<b64>`** | TR;2 + `;D<ver>,<base64>` compact RF-diag | `RF_DIAG_OUTPUT & TR_RF_DIAG_COMPACT` (ss_twr_init.c:1203/1320) | `run_recv_tdma_capture.py:59/82`→decode `:247-281` | **KEEP-DEBUG** | off in prod; anchor-ΔP diag; parsed |
| **`TR;3/4` legacy** (14-field) | `…;qf;first_to_last_us;frame_us;poll_count` | `TR_BCAST_V2_ENABLE=0` (ss_twr_init.c:1338, ver `:254/256`) | `run_recv_tdma_capture.py:41` (ver `[1234]`) | **KEEP-DEBUG / tombstone** | non-production compile alt; **version-collides** with prod TR;3 (§3) |
| **`;I,…` / `;R,…`** | IMU summary / raw XYZ trailers | `TR_IMU_SUMMARY` / `TR_IMU_RAW` (=0) (ss_twr_init.c:1367/1402) | `run_recv_tdma_capture.py:120` (`;I`) | **KEEP-DEBUG** | IMU builds only; off in prod |
| **`TP;1;…`** | 9-field collector phase telemetry | `PHASE_TELEMETRY_ENABLE` **=1 (ON in prod)** (ss_twr_init.c:3575/3672) | **UNPARSED** (no host matches `TP;`) | **KEEP-DEBUG → GATE OFF** | **emitted in production but zero parsers** — top output cleanup target (§4) |
| **`RFD;1;…`** | verbose per-anchor RF-diag | `RF_DIAG_OUTPUT` (=0) (ss_twr_init.c:3217) | `run_recv_tdma_capture.py:85` | **KEEP-DEBUG** | off in prod; parsed |
| **`CRX;1;…`** | compact CIR features (per anchor) | `CIR_FEATURE_OUTPUT` (=0) (ss_twr_init.c:761) | `cir_features_to_pair_weights.py:16` | **KEEP** (reserved fusion) | off in prod; consumed by fusion pair-weights |
| **`CIRM/CIRD/CIRE;1`** | full CIR dump | `CIR_FULL_OUTPUT` (=0) (ss_twr_init.c:888/906/920) | out-of-tree `flutter_ui_autopos/scripts/cir_full_usb_capture.py` (invoked via run_recv_tdma_capture.py:593) | **KEEP** (reserved imaging) | off in prod; out-of-tree consumer |
| **`TS;1` / `TagSummary`** | on-tag SOLVE position summary | `POSITION_OUTPUT & BLE_COMPACT_STATUS` (ss_twr_init.c:4430) | `capture_master_ble_session.py:53/23`, `uwb_live_viewer.py:45` | **KEEP-DEBUG** | host-solve prod uses TR; parsed when on-tag solve enabled |
| **`BSTAT …`** | BLE TX stats | `BLE_STATS_ENABLE` (=0) (uwb_tag_ble.c:566) | **UNPARSED** | **KEEP-DEBUG** | off in prod + unparsed; low-value debug counter (R1: keep inert toggle) |
| replies: `MODE=`, `OTA_STATE=`, `CFG_OK`, `STATE=RUNNING/ARMED`, `VERSION`, `TXPWR_OK`, `CIR_OK`, `APOS_*` | command echoes | on request | `loop_test_ota_targeting.py:26`(OTA_STATE), `run_recv_tdma_capture.py:218`(CFG_OK), `ota_deploy_tag_set.py:14`(VERSION), `run_recv_tdma_capture.py:2003`(MODE=/CIR_OK) | **KEEP** | request/reply contract |
| `PONG` (reply to `PING`) | liveness reply | on `PING` | **UNPARSED** (live `PING` sender = dead master_app.c) | **KEEP-DEBUG** (orphan) | harmless; sender is the dead file — low priority |
| `BP` position samples | — | — | **UNPARSED — not emitted in broadcast tree** | **DELETE / n-a** | stale COMMAND_REFERENCE note; no emit site in `src/` → nothing to remove |
| `BS;` (bundle-candidate prefix) | listed in strstr candidate set | never emitted | **UNPARSED** | **DELETE** (Batch 4) | vestigial entry uwb_tag_ble.c:1052; superseded by `TS;`/`TagSummary` |

### ANCHOR (UART / GATT)

| line type | grammar | trigger | host parser | bucket | evidence |
|---|---|---|---|---|---|
| `SW-<label>,…` | per-sweep result triples | AutoPos/matrix role | `run_autopos_sweep_loop.py:251/270/294`; master `main.c:1073` | **KEEP** | AutoPos result stream |
| `ANCHOR_FW …` / `OK …` / `ERR:…` / GATT `STATE` | command replies | on request | `ble_anchor_control.py:114-118`, `ota_deploy_anchor_set.py:28-32`, `serial_switch_role.py:134` | **KEEP** | provisioning/role reply contract |
| `ACRX;1;…` | compact CIR features (anchor) | `CIR_FEATURE` (=0) | `cir_features_to_pair_weights.py:15` | **KEEP** (reserved fusion) | off in prod; consumed by pair-weights |
| `ACIRM/ACIRD/ACIRE;1` | full CIR (anchor) | `CIR_FULL` (=0) | out-of-tree `cir_full_usb_capture.py:58/106/142` | **KEEP** (reserved imaging) | off in prod; out-of-tree consumer |

### LISTENER (USB-CDC) — separate diagnostic hardware

| line type | trigger | host parser | bucket | evidence |
|---|---|---|---|---|
| `LPD;1` / `LRD;1` / `LSTAT;1` | `POLL_DIAG`/`RESP_DIAG`/`STATUS_PRINT` (=1) | `capture_uwb_poll_listener.py:16/39/94` | **KEEP** | listener telemetry contract; parsed |
| `LCIRM/LCIRD/LCIRE;1` | `CIR_CAPTURE` | `capture_uwb_poll_listener.py:61/80/88`, `cir_live_view.py:65`, `cir_notch_detector.py:56` | **KEEP** | listener CIR; parsed |
| `LTAG;src=…` | `MODE_TAG` runtime | **UNPARSED in-tree** | **KEEP-DEBUG** | listener self-location (out-of-tree/manual analysis); reserved |
| listener `MODE=` echo | mode cmd | **UNPARSED** | **KEEP** | command echo (reply) |
| `UL;`/`UF;` legacy | old listener build | `capture_uwb_listener.py:16-30` | **KEEP-DEBUG** | legacy listener; still parsed |
| `BADV;`/`BSTAT;` (ble_listener) | scanner build | **UNPARSED in-tree** | **KEEP-DEBUG** | BLE-scan dongle; out-of-tree/manual |

### MASTER console

| line type | trigger | host parser | bucket | evidence |
|---|---|---|---|---|
| `MSTAT …` | 5 s, always | `overnight_power_preflight.py:22`, `run_overnight_power.py:65` | **KEEP-DEBUG** | tag-readiness gate; parsed |
| `MCLK …` | 5 s, always | `run_recv_tdma_capture.py:197` | **KEEP-DEBUG** | LFRC recal telemetry; parsed |
| `CFG assigned[…] …` | on CFG send | `run_recv_tdma_capture.py:205`, `tag_roster.py:15` | **KEEP** | slot-assignment log; parsed |
| `Control mode loaded: …` | boot | `ota_single_tag_stable.py:24`, `ota_deploy_*` | **KEEP** | OTA/mode detection; parsed |
| `TDMA weighted[…] …` | on rebalance | `run_recv_tdma_capture.py:225` | **KEEP-DEBUG** | scheduler telemetry; parsed |
| `TDMA_SLOT…` (tag→master notify) | tag TDMA | **no HOST parser** (consumed by master firmware) | **KEEP** | firmware-internal telemetry, not a host-output delete candidate |

---

## Part 3 — PRODUCTION OUTPUT CONTRACT v0

**Frozen production state** = `TR_BCAST_V2_ENABLE=1`, `RF_DIAG_OUTPUT=0`, `CIR_*=0`, `IMU_*=0`, runtime `DIAG OFF` / `CIR OFF`; bundling `RECORDS=8`/`FLUSH_MS=100` (CMakeLists:128-129). **Caveat:** `PHASE_TELEMETRY_ENABLE=1` today, so `TP;1` also ships — the contract below assumes it is gated OFF (Batch 4); until then, `TP;1` lines are present-but-unparsed.

**The only production tag telemetry line (parser contract):**
```
TR;2;<sweep>;<plan>;<pmode>;<active_mask>;<valid_mask>;<raw_csv>;<range_csv>;<quality_csv>;<status_codes>;T,<tag_temp_raw>,<tag_vbat_raw>
```
Field-by-field:

| # | field | type | meaning |
|---|---|---|---|
| 1 | `TR` | literal | line id |
| 2 | `2` | int | **version (frozen)** |
| 3 | `sweep` | u32 | monotonic sweep counter |
| 4 | `plan` | char | anchor-plan code (`ss_twr_init_plan_code`) |
| 5 | `pmode` | u8 | positioning_mode (RUN/IDLE) |
| 6 | `active_mask` | hex | bitmask of anchors polled this sweep |
| 7 | `valid_mask` | hex | bitmask of anchors with a valid range |
| 8 | `raw_csv` | csv mm | per-anchor raw distances |
| 9 | `range_csv` | csv mm | per-anchor corrected ranges |
| 10 | `quality_csv` | csv % | per-anchor quality |
| 11 | `status_codes` | csv char | per-anchor status |
| trailer | `;T,<die>,<vbat>` | u8,u8 | tag die-temp + vbat, SAR raw 0-255 |

Delivered **bundled** (up to 8 TR lines per NUS notification, flushed ≥100 ms). Parser: `run_recv_tdma_capture.py` (`TR_RANGE_RE` :41, `TR_TEMP_TRAILER_RE` :132).

**Master production telemetry:** `MCLK` (5 s), `MSTAT` (5 s), `CFG assigned` (on assignment), `Control mode loaded` (boot). **Anchor production:** no stream (responder is silent; `SW-` only in AutoPos).

**What each debug toggle ADDS** (so debug output is documented, not mysterious):

| toggle | adds to the stream |
|---|---|
| `DIAG ON` + `RF_DIAG_OUTPUT` build | TR version → **3**, appends **`;D1,<b64>`** to each TR; emits **`RFD;1`** lines |
| `CIR COMPACT` | **`CRX;1`** (tag) / **`ACRX;1`** (anchor) per anchor |
| `CIR FULL` | **`CIRM/CIRD/CIRE;1`** (tag) / **`ACIRM/D/E;1`** (anchor) |
| phase-telem (currently ON) | **`TP;1`** lines (unparsed — gate OFF) |
| IMU build | **`;I,…`** / **`;R,…`** TR trailers, TR version → 3/4 (legacy path) |
| `BLE_STATS` build | **`BSTAT`** lines |
| on-tag SOLVE build | **`TS;1` / `TagSummary`** |

**Version-bump policy (frozen):** `TR;2` is the frozen production line. **Contract hazard:** `TR;3` is already **overloaded** (production-diag 10-field+`;D1` vs legacy 14-field, and `TR;4` = legacy+IMU). Therefore: (1) any future production field change must bump to a version **outside {1,2,3,4}** (next = `TR;5`), and (2) the legacy `#else` path should be retired/renumbered (Batch 4) so `TR;3` unambiguously means "production + compact RF-diag." Host parsers currently disambiguate by field count (`run_recv_tdma_capture.py:416-464`), which is fragile — the version number should carry the layout.

---

## Part 4 — CLEANUP PLAN (batched, for approval — do NOT execute)

One git commit per batch (individually revertible). Verification per batch: **build succeeds + ge7 unchanged (≥0.97) + production `TR;2` still emits with `;T` trailer**. Rollback point = `freeze-4piece-<date>`.

### Batch 1 — never-compiled dead code (zero runtime impact)
- **DELETE `apps/master/src/master_app.c`** — superseded by `master_multi_app.c`; not in any CMakeLists (apps/master/CMakeLists.txt:50-51). *Verify:* `grep` confirms no `#include`/reference; master builds byte-identical.
- **`src/uwb_control_proto.c` + `.h` — DO NOT DELETE (operator decision).** R1(d): zero callers but "never wired," **not superseded** → reserved UWB-command-channel skeleton for the planned BLE-removal. **DELETE only if operator confirms BLE-removal is abandoned.** Both readings presented; default = KEEP.

### Batch 2 — host-script orphan senders (firmware no-ops today)
- **Remove `cmd_all MODE AOTA`** at `run_recv_tdma_capture.py:2415` (and `:2454`) — `AOTA` has no tag handler (removed, AUDIT T7). *Verify:* the paired `MODE IDLE`/`tdma clear` already performs the stop; capture behaviour unchanged.
- **Remove `cmd STREAM OFF` / `STREAM 0` / `STREAMON 0`** at `run_autopos_sweep_loop.py:2405-2407` (+ quarantine path) — no tag handler; already best-effort with `MODE IDLE` fallback. *Verify:* quarantine still idles tags via `MODE IDLE`.
- **Nothing breaks** — these strings return `UNKNOWN_CMD`/`MODE_BAD` on the tag today.

### Batch 3 — orphan firmware handlers (operator confirm supersession)
- **`MMOT`** (uwb_tag_ble.c:1842/1851) — zero senders; exact dup of `MODE RUN`; **mis-parses `MMOT<suffix>`** (footgun). **Recommend DELETE.**
- **`TDMA_STATUS`** (uwb_tag_ble.c:1585) — zero senders; redundant with `MODE?`/`CFG_STATUS`. **R1(d) tension** (added-then-forgotten, not named-supersession) → **operator rules KEEP-DEBUG vs DELETE.**
- *Verify:* both are unreachable (no sender) → removal cannot change runtime; build + ge7 + `TR;2` unchanged.

### Batch 4 — unparsed / hazardous outputs
- **[TR-CLEAN — freeze-clean deliverable, added 2026-07-15]** The shipped
  `freeze-4piece-20260715` **TAG** image (`tag-freeze-20260715`) emits **`TR;3`
  with an all-zero `D1` trailer even when runtime `DIAG` is OFF** — i.e. the
  frozen image is **not** `RF_DIAG_OUTPUT=0` as Part 3 assumed; the `D1` trailer
  ships unconditionally (empty when DIAG off, filled when on — verified
  2026-07-15). **Accepted as the V1 freeze format** by operator ruling. The
  freeze-clean goal is a **clean `TR;2` (no `D1` trailer)**: build the tag with
  the compact-diag/`D1` path compiled out (the `RF_DIAG_OUTPUT=0` branch),
  re-OTA the 3 tags, re-verify (ge7/ge8 unchanged expected: baseline agg
  ge7 0.978 / ge8 0.934 / valid% 97.3), then a `freeze-clean` git tag. See
  `SS-TWR/alt-SS-TWR/broadcast/FREEZE_4PIECE_20260715.md` ("Deferred").
- **Gate `TP;1` OFF by default** — set `SS_TWR_INIT_PHASE_TELEMETRY_ENABLE 1→0` (ss_twr_init.c:3575). **Do NOT delete the code** (KEEP-DEBUG bug instrument). This removes the only *unparsed line actively shipping in production*. *Verify:* `TR;2`+`;T` still emit; no host parser referenced `TP;` so nothing breaks; hot path unaffected (CPU-only reads compile out).
- **Resolve the `TR;3` version collision** — retire or renumber the legacy `#else` TR path (`ss_twr_init.c:1338`, non-production since `V2=1` always ships) so `TR;3` unambiguously = production+compact-diag. *Verify:* production build unaffected (uses the `#if` branch); host `TR_RANGE_RE` still matches.
- **Remove vestigial `"BS;"`** from the bundle-candidate strstr set (uwb_tag_ble.c:1052) — never emitted, superseded by `TS;`. *Verify:* bundling unaffected (TR/TS still recognised).
- **`BSTAT`** (uwb_tag_ble.c:566): unparsed + compile-gated OFF → **KEEP-DEBUG** (leave as inert toggle) OR delete; operator decision.

### TOMBSTONES — keep, do NOT delete (R2)
- **RXAUTR / RXDBLBUF** falsified experiments (AUDIT T25) — carry in-code falsification conclusions; keep compile-gated OFF.
- **CONN-INTERVAL sweep** comment (AUDIT M5) — records the 15 ms/30 ms falsification.
- **`uwb_control_proto.c`** — reserved skeleton (see Batch 1).
- Legacy `TR;3/4` path — keep as tombstone unless renumbered in Batch 4.

---

## Appendix — ambiguities surfaced for operator ruling
1. **`uwb_control_proto.c`**: reserved future skeleton (KEEP) vs abandoned residue (DELETE) — hinges on whether BLE-removal is still planned.
2. **`TDMA_STATUS`**: harmless dup query — KEEP-DEBUG vs DELETE.
3. **`BSTAT`**: inert debug counter — KEEP-DEBUG vs DELETE.
4. **Legacy `TR;3/4` path**: retire vs renumber vs keep-as-tombstone.

*Read-only survey. No code changed, nothing built, flashed, or deleted. Machine-readable buckets: `declutter_audit.json`.*
