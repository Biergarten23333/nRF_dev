# Listener-Proxy diag-on platform — HANDOFF / STATE (2026-06-30 night session)

**Status: PROXY EXPERIMENT BLOCKED on a firmware TODO (`fixed-a19`). Root cause fully
confirmed; fix known; deliberately NOT executed tonight.** Rig left as-is (anchors on a19).

This session brought up the "listener-proxy diag-on" experiment platform (anchor V2 ΔP +
listener cir_pwr + ranging) on the current hardware. The tag side + listener + a19 anchors
were flashed/OTA'd successfully, but a19 ranging collapsed. The collapse was root-caused to
an a19 firmware timing bug (below). The fix requires a firmware rebuild + re-OTA, which is
deferred to a fresh session (this session was long and the control chain had been through an
OTA-write-timeout / CDC-flood / power-cycle / responder-churn saga — re-OTA'ing 8 anchors at
the tail of a fatigued session = high risk, low reward).

---

## 1. THE TWO-LAYER ANCHOR STATE — do NOT conflate these

| Layer | Anchor build | Ranging (ge7) | Anchor ΔP (V2 diag) |
|---|---|---|---|
| **a18** | `altbcast-responder-a18-g1200-r1000-20260512_154806` | ✅ **GOOD, 0.96–0.97** (verified-good baseline) | ❌ none (bare V1 20-byte response) |
| **a19** | `alt-bcast-a19-rfdiag-v2-g1200-r1000` | ❌ **COLLAPSED, ge7=0 (~50% coin-flip)** | ✅ yes (V3 36-byte response w/ FP/CIR-power) |

### KEY CONCLUSION (the blocker)
**There is currently NO anchor build that has BOTH good ranging AND anchor ΔP.**
- a18 = ranging, no ΔP.
- a19 = ΔP, no ranging.
- The RotoArm + wet-towel proxy gate needs **both** (anchor ΔP vs listener cir_pwr, *and*
  working ranging). **→ The proxy experiment is BLOCKED by a firmware TODO: `fixed-a19`.
  This is not an optional optimization — the proxy cannot run cleanly until it's done.**

---

## 2. ROOT CAUSE (confirmed — not flap, not Master_Anchor, not config)

**a19's per-response `dwt_readdiagnostics()` SPI read squeezes the delayed-TX deadline →
intermittent miss → ~50% uniform per-anchor coin-flip.**

Mechanism (file:line in `src/ss_twr_resp.c` unless noted):
- A tag poll on a19 is answered as **V3 = 36 B** (a18 V1 = 20 B): `:1201-1203` promotes
  `resp_frame_len = UWB_MSG_RESP_V3_FRAME_LEN` for any tag poll when `DIAG_V2_ENABLE=1`.
  Frame lengths: `include/uwb_ss_twr_shared.h:34` (V1=20) `:48` (V2=34) `:56` (V3=36).
- The **+16 B airtime (~20 µs) is NOT the problem** — it's scheduled, absorbed by the
  1000 µs inter-rank spacing.
- **The squeeze is `dwt_readdiagnostics()` at `:1205`, run on EVERY response, BEFORE
  `dwt_setdelayedtrxtime`/`dwt_starttx` (`:1283`/`:1293`)** = **~55–90 µs extra prep at
  8 MHz SPI** (`src/uwb_port.c:160` = 8 MHz fast during ranging; `:159` = 2 MHz slow only
  at bringup). **No slack guard**: `ss_twr_resp_slack_uus` (`:420-423`) is logging-only;
  `dwt_starttx(DWT_START_TX_DELAYED)` is unconditional → if late, DW1000 HPDWARN →
  `delayed_tx_miss_count++` → `dwt_forcetrxoff()` → `continue` → response dropped that sweep.
- **Pre-0528 freeze baseline (a13, `BROADCAST_BASELINE_FREEZE.md`) = V1 20 B, NO
  `dwt_readdiagnostics()` in the response hot path → 98% CM.** a19 = identical timing budget
  (GUARD=1200 / RESP_SPACING=1000) **plus** that ~55–90 µs read on every reply.
- **The authors already knew this hazard:** `apps/anchor/CMakeLists.txt:60-63` ships
  `APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_SKIP_RANK0_ENABLE`, `..._RANK0_FAST_TX_ENABLE`,
  `..._POST_TX_DIAG_READ_ENABLE`, `..._POST_TX_DIAG_PAYLOAD_DELAY_ENABLE` — **all default 0**,
  i.e. deferral knobs for exactly the pre-TX-diag deadline, left OFF. **CAVEAT: these are
  rank0-scoped**; the full fix likely needs deferring the diag read post-TX for ALL ranks.

### Why the signature fits (8 MHz = ~55–90 µs = MARGINAL)
- ~50% coin-flip (not 0% / not 100%) = prep intermittently overruns by SPI/interrupt jitter,
  exactly what a marginal ~55–90 µs overrun produces.
- **All anchors affected equally + collapsed the instant a19 flashed** = per-response prep
  overrun, NOT the rank≥5 tail bug (every rank reads diag).
- (Flagged: if SPI were 2 MHz the read would be ~160–280 µs → near-certain miss; it's 8 MHz
  → marginal → coin-flip. Confirmed 8 MHz.)

### The supporting data (this session)
- **Timeline is decisive:** every high-ge7 run today (15:27 / 18:54 / ~20:26, ge7 0.96–0.97)
  was **a18**; a19 only landed successfully at **20:59** (the 20:07/20:18 OTA attempts failed);
  ge7 collapsed at the very next runs (21:21+). **a19 has only ever produced the collapse.**
- Current a19 capture `logs/verify_resp2_120s/recv_20260630_223637/`: tr_valid ~36%, ge7=0;
  `tag_rf_diag.csv` = 8742 rows, anchor_cir_pwr/fp populated (ΔP IS flowing for the responses
  that land); per-anchor valid ~50% coin-flip (was ~98% on a18); 6 of 8 distinct anchor_ids
  on-air, anchor 0 dead, 7 weak.
- **Anchor id 5 is a STRUCTURAL non-ranging node** (the 8th node / listener): 0% valid in
  *every* run incl. the high-ge7 ones. So the real ceiling is **7 valid of 8**; **ge8 is
  always 0**; **only ge7 matters**.

---

## 3. THE FIX (for the next session)

1. **Build `fixed-a19`:** in `src/ss_twr_resp.c`, move `dwt_readdiagnostics()` (`:1205`) to
   AFTER `dwt_starttx` (`:1293`) for **ALL ranks** (not just rank0), OR wire the existing
   `POST_TX_DIAG_READ` / `POST_TX_DIAG_PAYLOAD_DELAY` path fleet-wide. The diag is from the RX
   that already happened — it does not need to be read before TX. Keeps V2 ΔP AND restores
   ge7 timing.
2. **Re-OTA the 8 anchors** with `fixed-a19` (see §5 for the OTA recovery recipe that worked).
3. **(Optional, recommended) verify the fix:** build with
   `APP_ANCHOR_RESPONDER_FRAME_DIAG_ENABLE` to surface `min_slack_uus` / `delayed_tx_miss_count`
   (`ss_twr_resp.c:393-411`); if `min_slack` is comfortably positive and `tx_miss`→0, the
   timing-squeeze is proven gone. (This is the deferred "confirm-experiment".)
4. Expected result: a19 ΔP **and** ge7 back to ~0.96 (the 7/8 ceiling) → proxy unblocked.

---

## 4. CURRENT RIG STATE (left as-is tonight — nothing rolled back)

| Device | Resident now | Marker / build dir |
|---|---|---|
| Tags BS9336/BS955A/BSCCF4 | rfdiag-v2 (OTA 3/3 class-D) | `tag-rfdiag-v2-g1200-r1000` / `build-tag-ble-unified-rfdiag-v2-g1200-r1000-20260625` |
| Tags BS2DCE/BSDC91/BSF66F | **NOT updated** (task never reached) | still on prior fw |
| Anchors A–H (8) | **a19** (OTA 20:59, 8/8 class-D; image-confirmed, not per-anchor version-string-confirmed) | `alt-bcast-a19-rfdiag-v2-g1200-r1000` / `build-anchor-unified-ota-rfdiag-v2-g1200-r1000-20260625` |
| Master_Anchor B120 (SNR **960148546 PROTECTED**) | a19 carrier (dual-core flashed) — **its frozen a18 carrier is OVERWRITTEN** (restorable from disk) | `build-master-control-b120-m1-master-anchor-lfrc-rfdiag-v2-g1200-r1000-20260625` |
| Master_Tag B120 (SNR 1050070698) | rfdiag-v2 master-tag carrier | `build-master-control-b120-m1-master-tag-lfrc-rfdiag-v2-g1200-r1000-20260625` |
| Listener (J-Link probe 760184767) | poll-diag-generic | `build-uwb-listener-poll-diag-generic-20260625` |

### a18 rollback (if normal ranging needed BEFORE fixed-a19)
If the rig is needed for ordinary ranging (no ΔP) before the fix:
- Re-OTA anchors to a18 (`altbcast-responder-a18-g1200-r1000-20260512_154806`,
  `build-anchor-unified-ota-altbcast-responder-a18-g1200-r1000-20260512_154806`) → ge7 returns
  ~0.96 immediately.
- a18 master-anchor carrier on disk:
  `build-master-control-b120-m1-master-anchor-lfrc-anchoronly-altbcast-embed-altbcast-responder-a18-g1200-r1000-20260512_154806`.
- Since fixed-a19 will re-OTA the anchors next session anyway, leaving a19 is fine unless
  ranging is needed sooner. **Ask before rolling back.**

---

## 5. OPERATIONAL GOTCHAS LEARNED THIS SESSION (critical for the next re-OTA)

- **Master_Anchor B120 MUST be flashed with `scripts/jlink_flash_nrf5340_dualcore_by_snr.sh
  960148546 <carrier-dir>`, NOT the single-`loadfile` `flash_master_control_b120_m1_*`.** The
  single-loadfile method silently skips the **net core** → controller enumerates USB but
  rejects CDC writes (Write timeout) → hangs. Dual-core flasher writes `hci_ipc/zephyr/
  merged_CPUNET.hex` + `zephyr/merged.hex`.
- **Protected-flash override:** set `BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH=1` for the one
  flash; the guard file `.protec/noflash960148546` **stays in place** (do not delete it).
- **Anchor OTA write-timeout (Master CDC floods in AUTOPOS):** use
  `scripts/ota_deploy_anchor_set.py --port <Master_Anchor CDC> --order ABCDEFGH
  --pre-reset-first --master-reset-snr-after-pre-reset 960148546` — the per-anchor pre-reset +
  master-CDC reset-recovery is what made the a19 OTA succeed (8/8). Plain deploy write-timeouts.
- **Anchor OTA payload guard reads the GENERATED `apps/master_ota/generated/ota_image.inc`:**
  regenerate it first with `prepare_alt_ota_payload.py --kind anchor --marker <a19> --build-dir
  <anchor-build> --signed-bin <...zephyr.signed.bin> --dfu-zip <...dfu_application.zip>` (it's a
  payload-header regen, not a firmware rebuild). The b120-m1 master-anchor carrier already
  embeds the payload via that .inc at build time.
- **Post-OTA the a19 anchors boot in MATRIX mode (blue LED blink), NOT responder.** Set them to
  responder with `scripts/verify_all_anchor_responder_runtime.py --port <Master_Anchor>
  --retry-count 6` — but FIRST `bash scripts/jlink_reset_by_snr.sh 960148546 NRF5340_XXAA_APP`
  to clear the flooded CDC, then run it (it does a discovery rebuild + `anchor role all
  responder runtime`, ack `ready=8/8 rc=0`).
- **Master_Anchor stays connected to anchors during ranging BY DESIGN** (responder → low-freq
  ADV). This is normal and matches the a18 high-ge7 runs — it is NOT a "re-matrix/flap" source.
  Do NOT power off the Master_Anchor as a "fix".
- **Anchor-serial version/role query is boot-window-gated** (`APP_ANCHOR_SERIAL_CMD_BOOT_WINDOW_MS
  = 5000`, `anchor_app.c:72/572`): querying an anchor's version/role over its own J-Link VCOM
  needs a reset to enter the 5 s window (`serial_switch_role.py --boot-window-reboot`) — i.e.
  it reboots the anchor, not a clean read.
- The AutoPos Flutter UI's CDC command shape (reference, not exact for this set):
  `device kind anchor` → `anchor role all responder cir 0` (`flutter_ui_autopos/scripts/
  runtime_cir_control.py`).

---

## 6. INDEPENDENT TODO — listener captured 0 LPD rows (defer to fixed-a19 session)

In all verify captures the listener `lpd.csv` had 0 rows even when tags transmitted. Most
likely **baud**: Listener-E runs at **460800**, but `scripts/capture_uwb_poll_listener.py`
defaults to **115200** (and the wrapper `run_recv_tdma_capture_with_poll_listener.py` doesn't
forward a listener baud). The 06-25 poll-diag captures produced valid `lpd.csv`, so the build
is fine — recheck the capture baud (try 460800), and listener position, in the fixed-a19
session. **Do NOT chase tonight.** (Secondary possibility: position/RF; but anchors-down meant
little UWB traffic during these runs anyway.)

---

## 7. PROXY FLASH-BACK SET (the 3 markers — for reference; see also the 06-25 source set)
- ANCHOR: `alt-bcast-a19-rfdiag-v2-g1200-r1000` → **needs to become `fixed-a19`** (diag read
  moved post-TX) before the proxy can run.
- LISTENER: `build-uwb-listener-poll-diag-generic-20260625` (probe 760184767), poll-diag
  LPD/LRD; raw-CIR (LCIRD) was OFF on 06-25 — "listener CIR" = the per-poll `cir_pwr` scalar.
- TAG: `tag-rfdiag-v2-g1200-r1000` (RF_DIAG on → parses anchor V2 into `tag_rf_diag.csv`).

This is a **listener-line experimental set, NOT a clean freeze** — distinct from the 06-28
`FREEZE_4PIECE` nodiag restore baseline.
