# ge7/ge8 Regression Hunt — Root Cause, Fix, and Verification

**Date:** 2026-07-14  · **Tree:** `SS-TWR/alt-SS-TWR/broadcast`

## TL;DR / Verdict

The ge7/ge8 collapse (valid% 96% → 0%) was a **build-configuration regression on
BOTH the tag and the anchor**, introduced when the 2026-07-14 "P0 TX-RF" images were
rebuilt without carrying the flags of the last-known-good builds. It was **not** a
position/layout issue and **not** the P0 `dwt_configuretxrf` change itself.

Two independent regressors, each sufficient to break ranging:

1. **TAG (dominant, proven):** the 07-14 tag was built with
   `APP_TAG_RF_DIAG_TAG_RX_ENABLE=1`. The good tags had it **0**. This adds a
   `dwt_readdiagnostics()` (~55–90 µs SPI) **inside the single-buffered RX
   collection loop**, so the tag misses every *other* anchor reply → deterministic
   **even-survives / odd-dies** split, pegged at exactly 4 valid anchors → ge7=ge8=0.
2. **ANCHOR (secondary):** the 07-14 anchor was built without the `fixed-a19`
   deferred-diag flags (`APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_ENABLE`,
   `..._POST_TX_DIAG_READ_ENABLE`, `..._POST_TX_DIAG_PAYLOAD_DELAY_ENABLE`, all
   1→0). That reverts to the "broken-a19" behaviour where a pre-TX
   `dwt_readdiagnostics()` squeezes the delayed-TX deadline (~50% per-anchor
   coin-flip, per `PROXY_DIAGON_A19_HANDOFF_20260630.md`).

**Fix (both restored to last-known-good config + only the authorized P0/TXPWR on top):**
- Tag: `dwt_readdiagnostics` and the RFD publish are now behind a **runtime `DIAG`
  flag, default OFF** (production hot path == the stable nodiag timing). `DIAG ON`
  re-enables the reads for experiments.
- Anchor: rebuilt with the three `fixed-a19` flags = 1 (post-TX deferred diag),
  plus the init-only P0 `dwt_configuretxrf` and the `TXPWR` command.

**Measured, same wand position, fixed anchor:**

| tag DIAG | ratio_ge7 | ratio_ge8 | per-sweep dist | per-anchor valid |
|---|---|---|---|---|
| **OFF** (production) | **0.978** | **0.932** | {7:76, 8:1544} | all 8 = 96–98% |
| ON | 0.0 | 0.0 | {4:1409} | even 97–98%, **odd 0%** |
| *07-14 broken (ref)* | *0.0* | *0.0* | *{4:~all}* | *even 100%, odd 0–12%* |

DIAG ON reproduces the exact 07-14 even/odd signature; DIAG OFF restores the
0.96-class baseline. **Target valid% ≥ 90% met (97.8%).**

---

## TASK 1 — Last-known-good version

Firmware id is **not** stored in capture metadata; it is correlated from OTA
`deploy_summary.json` history + operator dir-name tokens (verified via a full
`logs/` sweep of `sweep_validity_all`).

### Timeline (ge7/ge8 vs firmware)

| date | capture (representative) | ratio_ge7 | ratio_ge8 | anchor fw | tag fw |
|---|---|---|---|---|---|
| 06-27 | clean_visible3_post_a7win_norfd | 0.978 | 0.967 | a7win | compact-sampled nodiag a7win |
| 06-28 | FREEZE_4PIECE / a7win-baseline | 0.96–0.97 | — | a18 responder | **nodiag-a7win-baseline** |
| 07-01 | a18_reverify_capture_3wand | **0.978** | **0.972** | a18 (F repaired) | tempTR-rfd |
| **07-04** | **ge7_test_20260704_032041** | **0.959** | **0.910** | **fixeda19-g1200-r1000** | **tempTR-rfd-a7win** |
| **07-05** | **roto_sar_overnight** (chunks) | **0.915–0.967** | 0.83–0.93 | **fixeda19** | tempTR-rfd |
| — | *(no captures 07-05 → 07-14: regression window)* | | | | |
| 07-14 | p0txrf_verify_bs9336_v5 | **0.0** | **0.0** | altbcast-a19-**p0txrf** | diagcheck-txpwr |
| 07-14 | power_sweep_cell*_* (all 5) | **0.0** | **0.0** | a19-txpwr | diagcheck-txpwr |

**Last known good:** `logs/roto_sar_overnight_20260705_012548/` (2026-07-05, ge7
0.915–0.967 across chunks), and the cleanest single golden run
`logs/ge7_test_20260704_032041_20260704_032041/` (ge7 0.959 / ge8 0.910, all 8
anchors ~96–98%).

- **Good anchor build:** `alt-bcast-fixeda19-g1200-r1000`
  (`build-anchor-unified-ota-fixeda19-g1200-r1000-20260701`).
- **Good tag build:** `tag-tempTR-rfd-a7win-20260630`
  (`build-tag-tempTR-rfd-a7win-20260630`). Also the FREEZE nodiag tag
  `compact-sampled-tdmafix-nodiag-a7win-baseline-20260628`.

The regression coincides exactly with the 07-14 OTA of both `-p0txrf`/`-txpwr`
images; no OTA occurred 07-01 → 07-14, so 07-04/07-05 are the last runs on the
good baseline.

---

## TASK 2 — Diff / root cause

### 2A. Config diff (this is where the regression lives)

**TAG** — only 3 flags differ good vs broken (source `#defines` unchanged):

| flag | good (tempTR-rfd) | broken (diagcheck) |
|---|---|---|
| `APP_TAG_RF_DIAG_TAG_RX_ENABLE` | **0** | **1** |
| `APP_TAG_RF_DIAG_OUTPUT_PERIOD` | 10 | 1 |
| `APP_TAG_TR_RF_DIAG_COMPACT_ENABLE` | 0 | 1 |

**ANCHOR** — only 3 flags differ good vs broken:

| flag | good (fixeda19) | broken (txpwr) |
|---|---|---|
| `APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_ENABLE` | **1** | **0** |
| `APP_ANCHOR_RESP_POST_TX_DIAG_READ_ENABLE` | **1** | **0** |
| `APP_ANCHOR_RESP_POST_TX_DIAG_PAYLOAD_DELAY_ENABLE` | **1** | **0** |

These flags default to their *unsafe* value in source (`ss_twr_resp.c:99/111/115`,
`build_tag_ble_motion.sh:73-78`) and must be set via environment. The `fixeda19`
build set them; the 07-14 P0 rebuild did **not** carry the env → silent revert.

### 2B. What `APP_TAG_RF_DIAG_TAG_RX_ENABLE` gates (the killer)

`src/ss_twr_init.c` broadcast RX collection loop (single RX buffer,
`SS_TWR_INIT_BCAST_RXDBLBUF_ENABLE = 0`):

- `dwt_readdiagnostics(&tag_resp_diag)` (line ~5516) — reads RX_FQUAL/RX_TIME/RXPACC
  ≈ 5–6 SPI transactions.
- `ss_twr_init_rf_diag_from_rxdiag()` (line ~3125) — 2 extra SPI reads
  (`LDE_THRESH` + `AGC_STAT1`).

**Cost:** ~55–90 µs @ 8 MHz SPI **per anchor response, inside the collection
window**, before the receiver is re-armed for the next reply. With a single RX
buffer and ~800 µs nominal inter-anchor spacing, the read straddles the arrival of
the *next* reply → that reply is dropped → the tag catches every *other* anchor →
**even/odd, exactly 4 valid**. (Empirically reproduced — Task 5.)

### 2C. What the anchor flags gate

`fixed-a19` (flags=1) reads diagnostics **after** `dwt_starttx` and pipelines them
into the next reply (`ss_twr_resp.c:1224-1268, 1370-1391`) — the delayed-TX
deadline is always met. With flags=0 that deferral path is compiled out; per the
06-30 handoff the anchor then squeezes the pre-TX deadline → ~50% uniform
per-anchor coin-flip. (Root cause + intended fix pre-documented in
`PROXY_DIAGON_A19_HANDOFF_20260630.md §2-3` and `FREEZE_A18…md:139`.)

### 2D. Non-P0 change audit (per constraint)

`git diff <good> HEAD` on `src/` for the anchor: the only source deltas are the
authorized `dwt_configuretxrf()` (init), `EVC_EN` (init), boot readback, and the
`TXPWR` BLE command. **No** CIR-mode default change, **no** LED change
(`BLUE_LED_ENABLE=1/ACTIVE_LOW=1/PIN=31` preserved), **no** frame-format change,
**no** timing-constant change. The response frame was already V3 in the good
`fixeda19` build (not a P0 side effect).

---

## TASK 3 — Slot timing budget

| quantity | value |
|---|---|
| anchors per sweep | 8 |
| `APP_ANCHOR_RESP_DELAY_UUS` (rank-0 offset) | 1200 µs |
| `APP_ALT_SS_TWR_RESP_SPACING_US` (inter-rank) | 800 µs |
| rank-7 reply completes | ≈ 1200 + 7×800 ≈ 6.8 ms after poll TX-done |
| collector `TAIL_MARGIN` (a7win fix) | 800 µs |
| active slot | 9 ms |
| RX double-buffer | **OFF** (single buffer) |
| **tag per-response budget before next reply** | **≈ 800 µs** |
| baseline processing (readrxdata + 2 ts reads) | ~20–30 µs |
| **+ DIAG per-response reads (readdiagnostics + LDE + AGC)** | **+55–90 µs** |

The added ~55–90 µs is small vs 800 µs on paper, but with a **single RX buffer** the
receiver is not re-armed during the read; replies that arrive in that window (and
under SPI/IRQ jitter the odd-ranked ones systematically do) are lost. The empirical
even/odd result confirms the budget is violated for alternating ranks.

---

## TASK 4 — Fix

### Clean image = last-good config + ONLY {P0 txconfig, EVC_EN, TXPWR, readback}

**Tag** `unified-diaggate-p0txpwr-20260714` (`build-tag-diaggate-p0txpwr-20260714`):
- Per-response diag reads + RFD publish gated behind a **runtime flag, default OFF**
  (`ss_twr_init_rf_diag_runtime_on`). Boot = OFF ⇒ hot path identical to the stable
  nodiag timing. BLE command `DIAG ON|OFF` (and `DIAG?`), mirroring the existing
  command style. `DIAG ON` = per-frame reads for experiments (accepts the ge7 hit).
- P0 `dwt_configuretxrf()` + `EVC_EN` + `AGC_CTRL1.DIS_AM` clear are **init-only**
  (`ss_twr_init_apply_txrf_and_diag`, called once at boot — not the hot path).
- `TXPWR <MAX|M3|M6|M12|POR>` command retained unchanged.

**Anchor** `altbcast-fixeda19-p0txpwr-g1200-r1000`
(`build-anchor-fixeda19-p0txpwr-g1200-r1000-20260714`):
- Rebuilt with `APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_ENABLE=1`,
  `..._POST_TX_DIAG_READ_ENABLE=1`, `..._POST_TX_DIAG_PAYLOAD_DELAY_ENABLE=1`,
  `APP_ANCHOR_RESP_DELAY_UUS=1200` (= `fixeda19`).
- P0 `dwt_configuretxrf()` (init) + `TXPWR` command on top.

Patch: `clean_firmware.patch` (this dir). Lines changed vs stable are minimal and
scoped to {P0 txrf, EVC, agc-init, TXPWR command, runtime DIAG gate}.

### Hot-path timing: before (good) vs clean fix
- Good (nodiag / TAG_RX=0): no per-response tag diag → budget ~20–30 µs of 800 µs.
- Clean fix, **DIAG OFF (default)**: identical — the reads are runtime-skipped.
- Clean fix, **DIAG ON**: +55–90 µs (experiments only; ge7 degrades, as intended).

---

## TASK 5 — Deploy & verify

**Deploy (fully non-interactive, `-SelectEmuBySN`; both masters flashed + full OTA):**
- Master-anchor B120 carrier rebuilt (embeds new anchor payload), protected-flashed
  to SNR 960148546 (`BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH=1`).
- Master-tag B120 carrier rebuilt, flashed to SNR 1050070698 (in parallel — the two
  masters are independent BLE domains, no interference).
- OTA: 8 anchors → `altbcast-fixeda19-p0txpwr` (8/8 class D); 3 tags →
  `unified-diaggate-p0txpwr` (3/3 version match=True).
- Anchors set to responder: `ready=8/8`, conn 8/8; blue-LED config preserved
  (solid when responder).

**A/B verification (60 s @ 10 Hz, BS9336/BS955A/BSCCF4, wand fixed):**

| | ratio_ge7 | ratio_ge8 | per-sweep dist | per-anchor valid% |
|---|---|---|---|---|
| **DIAG OFF (production)** | **0.978** | **0.932** | {7:76, 8:1544} | 0:98 1:98 2:97 3:97 4:96 5:97 6:97 7:98 |
| DIAG ON | 0.000 | 0.000 | {4:1409} | even 97–98, **odd 0** |

- **DIAG OFF matches the last-known-good baseline** (07-01: 0.978/0.972; 07-04:
  0.959/0.910). Target ≥90% met.
- **DIAG ON reproduces the 07-14 even/odd collapse exactly** — this *isolates* the
  tag per-response diag as the dominant regressor (anchor is fixed in both legs,
  wand unmoved). Fleet left in production state (DIAG OFF, all 3 tags acked).

Artifacts: `logs/ge_regression_verify_DIAGOFF_20260714_*/`,
`logs/ge_regression_verify_DIAGON_20260714_*/`,
`logs/ge_regression_ota_{anchor,tag}_20260714/`.

---

## Constraints compliance
- Built from last-good config + ONLY P0 {txconfig, EVC_EN, readback} + TXPWR; DIAG
  reads deferred behind a runtime flag, **default OFF**. ✔
- Anchor blue-LED behaviour unchanged (solid = responder). ✔
- CIR modes (off/compact/full) default + selection logic unchanged; CIR boots OFF. ✔
- BLE command set: existing commands untouched; new `DIAG` follows the `TXPWR`
  pattern. ✔
- Timing constants (RESP_SPACING/GUARD/TAIL_MARGIN/RESP_DELAY), frame formats,
  operating modes: unchanged. ✔
- All J-Link ops non-interactive (`-NoGui -SelectEmuBySN`); protected guard file
  `.protec/noflash960148546` left in place. ✔
