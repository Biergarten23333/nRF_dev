# Full Sweep Timing Audit — Can We Fit a 9th Anchor?

**Date:** 2026-07-13   **Scope:** read-only audit   **Subject:** broadcast Alt-SS-TWR, 8 anchors → 9?

---

## 0. Bottom line — GO / NO-GO

**NO-GO for a 9th anchor at the current 10 Hz motion profile.** Three *independent* blockers,
any one of which is sufficient:

| # | Blocker | Type | Fixable? |
|---|---------|------|----------|
| **A** | On-air anchor bitmask is a single `uint8_t` (8 bits) and `UWB_MAX_ANCHORS = 8U`. Bit 8 (the 9th anchor) is **physically unrepresentable** in the poll frame. | Structural | Yes — widen mask to `uint16_t`, bump macro; touches tag + responder + listener + all host parsers. Unavoidable regardless of rate. |
| **B** | 9-anchor collector window = **9.665 ms** > 9 ms active slot ≈ 10 ms slot period. The firmware's own slot-budget guard rejects it; the sweep overruns into the next tag's slot. | Timing | Only by dropping the rate (slot ≥ 11–12 ms → < 10 Hz) or moving to the 40 ms / 24 ms static profile. |
| **C** | BLE connection interval = **7.5 ms**, latency 0, **not** phase-locked to the TDMA slot. The 9-anchor collector (9.665 ms) now *exceeds one full BLE interval* and eats the idle margin that let the conn event hide. The new tail anchor (rank 8) becomes a guaranteed **ge8-style phase-collision victim** — the exact documented regression. | BLE co-existence | Both hardware mitigations already tried (RXAUTR, double-buffer) and **falsified**. Needs a rate drop or conn-event phase-parking (not implemented). |

**CONDITIONAL GO:** a 9th anchor *is* feasible in the **static / low-rate profile** (`PERIOD=40 ms,
ACTIVE=24 ms`, ~2.5 Hz base): the 9.665 ms collector fits inside 24 ms with ~14 ms of idle tail where
the BLE conn event can be parked away from the responders. This is contingent on doing blocker **A**
(mask widening) plus the full host-side/listener fix list in §5.

The user's recollection is confirmed: base delay **1200 µs (guard)** + **1000 µs rank spacing**; the
active slot is **9 ms**; rank-7 (the always-last responder) completes at **~8.45 ms**.

---

## 1. Timing constants (Step 3)

All values are the **as-built** values (CMake `-D` cache overrides win over the `#ifndef` fallbacks in
the `.c` files — verified below). Both the tag app and the anchor app compile the same guard/spacing, so
the tag's collector window matches the responders' transmit schedule.

| parameter | value | source (`file:line`) |
|-----------|-------|----------------------|
| Responder base delay = **GUARD** | **1200 µs** | build: [apps/anchor/CMakeLists.txt:82](../../SS-TWR/alt-SS-TWR/broadcast/apps/anchor/CMakeLists.txt#L82); default [ss_twr_resp.c:135](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_resp.c#L135) |
| Responder **rank spacing** = RESP_SPACING | **1000 µs** | build: [apps/anchor/CMakeLists.txt:83](../../SS-TWR/alt-SS-TWR/broadcast/apps/anchor/CMakeLists.txt#L83); default [ss_twr_resp.c:139](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_resp.c#L139) |
| Responder delay(rank) formula | `1200 + rank·1000` µs | [ss_twr_resp.c:596-609](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_resp.c#L596) |
| `APP_ANCHOR_RESP_DELAY_UUS` (delayed-TX base) | 1200 | [ss_twr_resp.c:51](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_resp.c#L51) |
| Tag collector GUARD | **1200 µs** | build: [apps/tag/CMakeLists.txt:139](../../SS-TWR/alt-SS-TWR/broadcast/apps/tag/CMakeLists.txt#L139) (overrides src default 500 at [ss_twr_init.c:80](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L80)) |
| Tag collector RESP_SPACING | **1000 µs** | build: [apps/tag/CMakeLists.txt:140](../../SS-TWR/alt-SS-TWR/broadcast/apps/tag/CMakeLists.txt#L140) (overrides src default 800 at [ss_twr_init.c:84](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L84)) |
| Tag TAIL_MARGIN | **800 µs** | [ss_twr_init.c:435](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L435) |
| Poll frame airtime | 335 µs | [ss_twr_init.c:436](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L436) |
| Poll delayed-TX schedule-ahead | 1000 UUS (~1026 µs) | [ss_twr_init.c:423](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L423) |
| N anchors (`UWB_MAX_ANCHORS`) | **8** | [uwb_ss_twr_shared.h:7](../../SS-TWR/alt-SS-TWR/broadcast/include/uwb_ss_twr_shared.h#L7) |
| Collector window(N) formula | `GUARD + (N-1)·SPACING + TAIL − poll_airtime` | [ss_twr_init.c:2420-2444](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L2420) |
| **UWB sweep duration, N=8** | **8665 µs** (rank-7 completes ~8.45 ms) | computed; comment [ss_twr_init.c:427-433](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L427) |
| Inter-sweep sleep `RNG_DELAY_MS` | **0** (cadence is TDMA-driven) | build: [apps/tag/CMakeLists.txt:38](../../SS-TWR/alt-SS-TWR/broadcast/apps/tag/CMakeLists.txt#L38) |
| Slot period — **motion** profile | **10 ms** | build: [master_control/CMakeLists.txt:41](../../SS-TWR/alt-SS-TWR/broadcast/apps/master_control/CMakeLists.txt#L41) |
| Slot **active** — motion | **9 ms** | build: [master_control/CMakeLists.txt:43](../../SS-TWR/alt-SS-TWR/broadcast/apps/master_control/CMakeLists.txt#L43) |
| Target rate — motion | **10 Hz** | build: [master_control/CMakeLists.txt:49](../../SS-TWR/alt-SS-TWR/broadcast/apps/master_control/CMakeLists.txt#L49) |
| Slot period / active — **static** profile | **40 ms / 24 ms** | observed in run logs + env override (`TDMA_SLOT_PERIOD_MS=40 ACTIVE_MS=24`) |
| Superframe | `slot_count × slot_period` | [broadcast_tdma.c:178](../../SS-TWR/alt-SS-TWR/broadcast/src/broadcast_tdma.c#L178) |
| Slot count (build default) | 10 | build recipe `build_tag_ble_unified.sh 0 10` |
| **BLE connection interval** | **7.5 ms** (6 × 1.25 ms) | [prj.conf:29-30](../../SS-TWR/alt-SS-TWR/broadcast/apps/tag/prj.conf#L29); [uwb_tag_ble.c:180](../../SS-TWR/alt-SS-TWR/broadcast/apps/tag/src/uwb_tag_ble.c#L180) |
| BLE slave latency | 0 | [prj.conf:31](../../SS-TWR/alt-SS-TWR/broadcast/apps/tag/prj.conf#L31) |
| BLE PHY / DLE | 2M / 251 B | [prj.conf:68-69](../../SS-TWR/alt-SS-TWR/broadcast/apps/tag/prj.conf#L68) |

### How the rate is really set
Each tag polls **once per superframe** in the slot(s) it owns
([broadcast_tdma.c:183-215](../../SS-TWR/alt-SS-TWR/broadcast/src/broadcast_tdma.c#L183)):
`rate = (slots owned) / (slot_count × slot_period)`. The master hands out extra slots to raise a tag's
Hz (`tdma freq motion <hz>`). Two regimes seen in the logs:

- **Motion (10 Hz):** `PERIOD=10 ms`, 10 slots → 100 ms superframe, 1 slot/tag → 10 Hz. Active budget **9 ms**.
- **Static (multi-Hz via multi-slot):** `PERIOD=40 ms`, `ACTIVE=24 ms`. e.g. `MASK=0x0021` = slots 0+5 → 2 polls / 400 ms superframe → 5 Hz. Active budget **24 ms**.

The **binding** constraint for a 9th anchor is the per-slot **active window** the sweep must fit inside —
9 ms in motion, 24 ms in static.

---

## 2. Complete timing of ONE sweep cycle (Step 2)

Broadcast Alt-SS-TWR = **one** delayed poll → **one** collector window → N responders arrive staggered by
rank. Confirmed: single poll frame built at
[ss_twr_init.c:5270](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L5270)
(`build_alt_broadcast_poll_frame`, carrying `anchor_mask` + `rank_offset`), single `dwt_rxenable` at
[:5324](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L5324), single collector `while` loop at
[:5343](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L5343). The main loop is
[:5801](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L5801); burst function
`ss_twr_init_alt_burst_sweep_once` at [:5034](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L5034).

Timeline for the **8-anchor motion profile** (t = 0 at poll TX-done / TXFRS; all times µs):

```
  t (µs)   event                                        blocking?  source
 ───────   ──────────────────────────────────────────  ─────────  ──────────────────────────
 -1026     delayed-TX scheduled 1000 UUS ahead                     ss_twr_init.c:5260-5279
 -335      poll frame on air (airtime 335 µs)           blocking   POLL_AIRTIME_US :436
    0      POLL TX-done (TXFRS); clear TX status                   :5322
    0      RX collector enabled (dwt_rxenable)          blocking   :5324
    0 ───► collector while-loop opens, runs 8665 µs     blocking   :5343 (response_window_cycles)
 1200      rank 0 response RX      (GUARD)                          resp delay = 1200 + 0·1000
 2200      rank 1 response RX                                       1200 + 1·1000
 3200      rank 2 response RX                                       1200 + 2·1000
 4200      rank 3 response RX                                       1200 + 3·1000
 5200      rank 4 response RX                                       1200 + 4·1000
 6200      rank 5 response RX      (tail region ≥ rank 5)           1200 + 5·1000
 7200      rank 6 response RX                                       1200 + 6·1000
 8200      rank 7 response TX starts  ← ALWAYS-LAST responder       1200 + 7·1000
 8450      rank 7 frame completes (~250 µs airtime)                 comment :429
 8665      collector window CLOSES  (= 1200+7·1000+800−335)         window_us() :2420-2444
 ───────   ── post-sweep (runs in remaining ~1.3 ms of 10 ms slot) ──
 ~8700     range compute ×8 (ToF→mm, int math)          blocking   calc_raw_distance_mm :913 (<100 µs)
 ~8750     per-anchor filter/quality/EKF update         blocking   record_sweep_anchor_* (<150 µs)
 ~8800     format TR line (snprintk into line[384])     blocking   :1274-1310 (~tens of µs)
 ~8820     enqueue TR to BLE FIFO (bt_nus_send is ASYNC) NON-block  K_FIFO ble_tx_fifo, uwb_tag_ble.c:208
 ~8850     un-pause BLE TX + k_msleep(slot remainder)    yields     release_ble_tx_after_active_slot :2602
 ~10000    slot end → next owned slot / next superframe             broadcast_tdma_wait_next_slot_start
```

**Key structural facts for the model:**
- **Post-sweep CPU work is tiny (< ~300 µs)** and *non-blocking* w.r.t. the next sweep. Range math is a
  few integer ops per anchor; TR formatting is a single `snprintk`; the NUS send is *enqueued* to
  `ble_tx_fifo` and drained by a dedicated `ble_tx_thread`
  ([uwb_tag_ble.c:208-214](../../SS-TWR/alt-SS-TWR/broadcast/apps/tag/src/uwb_tag_ble.c#L208)) — it does
  **not** stall the ranging loop.
- **BLE TX is paused during the UWB window** (`set_ble_tx_paused(true)` at
  [:5930](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L5930) / [:5095](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L5095))
  and released only *after* the active slot ([:2602](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L2602)),
  so the application-level telemetry burst drains in the idle tail — **but the BLE controller's
  connection event still fires autonomously every 7.5 ms and the app cannot suppress it** (see §7).
- The dominant, essentially *only* cost in the cycle is the **UWB collector window** itself.

### Segment durations

| segment | duration (8 anchors) | blocking | source |
|---------|----------------------|----------|--------|
| Poll airtime | 335 µs | yes | POLL_AIRTIME_US |
| **UWB collector window** | **8665 µs** | yes | window formula :2420 |
| Range compute + filter + TR format | < ~300 µs | yes | :913, :1274 |
| BLE NUS TX | 0 µs on the ranging thread (async FIFO) | no | :208 |
| Idle / slot-yield sleep | ~1.0–1.3 ms | yields | :2602 |
| **Total UWB-critical path** | **~9.0 ms** | | fits 9 ms active with ~0.3–0.5 ms margin |
| **Slot period (motion)** | **10 ms** | | superframe/10 = 10 Hz |

---

## 3. Timing parameters table (Step 3 — filled)

| parameter | value | note |
|-----------|-------|------|
| RESP_SPACING (rank) | **1000 µs** | responder + tag agree |
| GUARD (base delay) | **1200 µs** | responder + tag agree |
| TAIL_MARGIN | 800 µs | tag collector only |
| N anchors | 8 | `UWB_MAX_ANCHORS` |
| **UWB sweep duration** | **8.665 ms** | `= 1200 + (8−1)·1000 + 800 − 335` |
| Post-sweep processing | < 0.3 ms | non-blocking (async BLE) |
| BLE TX time (on ranging thread) | ~0 ms | enqueued to FIFO, drained by other thread |
| **Total cycle (UWB-critical)** | **~9.0 ms** | |
| Sweep period (motion) | **10 ms → 10 Hz** | superframe = 10 slots × 10 ms |
| Idle / margin | **~1.0–1.3 ms** | slot_period − UWB-critical |
| Sweep period (static) | 40 ms slot, 24 ms active | 9.665 ms fits with ~14 ms idle |

---

## 4. What's hardcoded to 8? (Step 4)

**Root cause — the on-air 8-bit mask.** The poll frame carries the anchor set as a single byte:
`frame[UWB_MSG_BCAST_POLL_ANCHOR_MASK_IDX] = anchor_mask;`
([uwb_ss_twr_shared.c:121](../../SS-TWR/alt-SS-TWR/broadcast/src/uwb_ss_twr_shared.c#L121)), and the tag
builds it as `uint8_t` with `1U << anchor_id`
([ss_twr_init.c:4927-4939](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L4927)). **Bit 8 does not
exist in a byte** — everything below inherits this limit.

### Firmware (macro-scaled — auto-grow when `UWB_MAX_ANCHORS` is bumped, *except the mask width*)
| item | file:line |
|------|-----------|
| `#define UWB_MAX_ANCHORS 8U` | [uwb_ss_twr_shared.h:7](../../SS-TWR/alt-SS-TWR/broadcast/include/uwb_ss_twr_shared.h#L7) |
| `#define UWB_CTRL_MAX_ANCHOR_IDS 8U` | [uwb_control_proto.h:8](../../SS-TWR/alt-SS-TWR/broadcast/include/uwb_control_proto.h#L8) |
| all per-anchor sweep arrays `[UWB_MAX_ANCHORS]` | ss_twr_init.c:589-602, 5050-5086 |
| `char status_codes[UWB_MAX_ANCHORS + 1U]` | [ss_twr_init.c:1178](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L1178) |
| TR per-anchor loop filters `anchor_id >= UWB_MAX_ANCHORS` | [ss_twr_init.c:1106](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L1106), 775 |
| inter-anchor matrix `cells[UWB_MAX_ANCHORS][UWB_MAX_ANCHORS]` | [uwb_anchor_matrix.h:20](../../SS-TWR/alt-SS-TWR/broadcast/include/uwb_anchor_matrix.h#L20) |
| layout `layout_defaults/runtime[UWB_MAX_ANCHORS]`, label `'A'+i` | [uwb_anchor_layout.c:16,27,38](../../SS-TWR/alt-SS-TWR/broadcast/src/uwb_anchor_layout.c#L16) |
| rank rotation `% UWB_MAX_ANCHORS` | [ss_twr_resp.c:623-626](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_resp.c#L623) |

### Firmware (hard 8-bit / must be widened, not just re-macro'd)
| item | file:line | problem |
|------|-----------|---------|
| **tag** `uint8_t anchor_mask` | [ss_twr_init.c:4927](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L4927) | 8-bit |
| **on-air** poll mask byte | [uwb_ss_twr_shared.c:121](../../SS-TWR/alt-SS-TWR/broadcast/src/uwb_ss_twr_shared.c#L121) | 1 byte in frame |
| **responder** `uint8_t anchor_mask` param + `1U<<anchor_id` | [ss_twr_resp.c:612-628](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_resp.c#L612) | 8-bit |
| poll safety guard `poll_count > UWB_MAX_ANCHORS → return` | [ss_twr_init.c:5089](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L5089) | rejects 9 |

### Fixed-size CSV buffers (tight for 9 — flag)
`raw_csv[64]` / `range_csv[64]` ([ss_twr_init.c:1175-1176](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L1175))
are **not** macro-scaled. 9 signed-mm entries (~7 chars each incl. comma) ≈ 63 B — right at the 64 B limit;
large distances could truncate the last anchor. `quality_csv[40]` and the `line[384]` container are fine.
**Widen these two to 72–80 B when adding anchor 9.** (`status_codes` auto-scales via the macro.)

### Host-side / peripheral (from the parallel inventory)
| # | file:line | hardcode | what breaks |
|---|-----------|----------|-------------|
| Parser | `scripts/run_recv_tdma_capture.py:375-376, 427-428` | `range(8)` over active_mask | 9th anchor's range/quality/status columns silently dropped |
| Parser | `run_recv_tdma_capture.py:1081` | `if len(ok_anchors) == 8` | "all anchors seen" preflight never satisfied |
| APOS | `scripts/push_apos_layout_verified.py:344` | regex `APOS_COMMIT_OK N=8` | fw sends `N=9` → commit verification always fails |
| Traj | `export_capture_trajectory.py:15,149,221` | `ANCHORS="ABCDEFGH"`, `>=len`, `[:len]` | 9th anchor dropped/truncated |
| Listener | `apps/ble_listener/src/main.c:283` | `anchor_id <= 8U` | 9th anchor gets no label |
| Listener | `UWB_listener/src/main.c:90` | `SCAN_ANCHOR_MASK 0xFFU` | 8-bit poll mask → 9th anchor never polled |
| Solver | `c_solver.py:282` | `if n >= 8` "full-anchor" fast path | 8-of-9 frame mis-classified as full (behavioral, not crash) |
| Solver | `layout_io.py:8` | `ANCHOR_LABELS="ABCDEFGH"` | anchor id 8 loads but gets no letter label |
| Roster | `data/anchor_layout_ah_*.json`, `inter_anchor_matrix_ah*.json` | "A..H" = 8 baked into schema/filenames | no slot for a 9th anchor |
| Eval | `run_clean_full_compare.py:253…1409`, `validate_outdoor_dataset.py:78`, `monte_carlo_failure_modes_outdoor.py:159…`, `robustness_keepk_outdoor.py`, `analyze_tag_elevation_residuals.py:143`, `solve_inter_anchor_free.py:16,187`, `solve_v5.py:475` | `range(8)` / `"ABCDEFGH"` / `keep_k==8` | under-count / mislabel |

**Not hardcoded (good news):** the AutoPos **inter-anchor pair engine is data-driven** — it enumerates
whatever `(i,j)` pairs exist and scales 28 → 36 with no code change
(`prepare_v4_data.py:85`, `analyze_inter_pairs.py:46`, `solve_v5.py` pair dict). The host **C-solver
core** builds anchor ids dynamically. The TR mask fields are `%02lx` (min-width, non-truncating) and host
regexes read `[0-9A-Fa-f]+`, so a 3-hex-digit mask parses fine.

**MTU is NOT a limiter.** This is *not* a 20-byte system: negotiated ATT MTU is large
(`CONFIG_BT_L2CAP_TX_MTU=498`, [prj.conf:59](../../SS-TWR/alt-SS-TWR/broadcast/apps/tag/prj.conf#L59)),
NUS bundle cap 220 B, `line[384]`. An 8-anchor TR line is ~150–170 B; a 9th adds ~+17 B → ~190 B. No
packet overflow.

---

## 5. What breaks if we add anchor 9 (Step 5 — consolidated fix list)

1. **On-air / mask (must widen to `uint16_t`):** poll-frame mask byte
   ([uwb_ss_twr_shared.c:121](../../SS-TWR/alt-SS-TWR/broadcast/src/uwb_ss_twr_shared.c#L121)),
   tag `anchor_mask` ([ss_twr_init.c:4927](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L4927)),
   responder `anchor_mask` ([ss_twr_resp.c:612](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_resp.c#L612)),
   listener `SCAN_ANCHOR_MASK 0xFFU`. **Frame length grows** — `UWB_MSG_ALT_POLL_FRAME_LEN` and the frame
   layout must add a second mask byte.
2. **Macros:** `UWB_MAX_ANCHORS → 9`, `UWB_CTRL_MAX_ANCHOR_IDS → 9`. Auto-scales the arrays, layout,
   matrix (28→36 cells), rank modulo, `status_codes`.
3. **Poll guard:** `poll_count > UWB_MAX_ANCHORS` check keeps working once macro is 9.
4. **CSV buffers:** widen `raw_csv`/`range_csv` 64 → ~80 B.
5. **TDMA timing (see §6):** the 9-anchor collector no longer fits the 9 ms motion slot.
6. **BLE packet format:** TR line stays well under buffers; masks widen to 3 hex digits (parsers OK).
7. **Anchor rank assignment / APOS:** firmware scales; **host** `push_apos_layout_verified.py:344`
   `N=8` regex must become `N=9`.
8. **Host TR parser:** `run_recv_tdma_capture.py` `range(8)` → `range(9)` and `==8` → `==9`.
9. **AutoPos:** pair engine fine (36 pairs); fixed-8 label/output enumerations in the eval scripts must go 9.
10. **Listener firmware:** `ble_listener` `<=8U` label bound; `UWB_listener` 8-bit scan mask.
11. **Solver / layout IO:** extend `ANCHOR_LABELS` beyond H; `c_solver.py:282` `n>=8` fast-path threshold;
    new roster/layout JSON schema for a 9th anchor (drop the "ah" naming).

---

## 6. Timing feasibility for 9 anchors (Step 6)

Collector window formula ([ss_twr_init.c:2420-2444](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L2420)):
`window = GUARD + (N−1)·SPACING + TAIL_MARGIN − poll_airtime`

```
N = 8:  1200 + 7·1000 + 800 − 335 = 8665 µs   (rank 7 completes ~8.45 ms)
N = 9:  1200 + 8·1000 + 800 − 335 = 9665 µs   (rank 8 completes ~9.45 ms)
Δ = +1000 µs  (exactly one more 1000 µs rank slot)
```

**Motion profile (10 Hz, 10 ms slot, 9 ms active):**
```
new UWB sweep      = 9.665 ms
active budget      = 9.000 ms   → OVER by 0.665 ms
slot period        = 10.00 ms   → idle margin collapses to ~0.3 ms
```
The firmware's **own guard rejects this**: `tdma_exchange_can_start` / the sweep-budget check compute
`required_ms = ceil(response_window_estimated_us) ≈ 10 ms`
([ss_twr_init.c:2512-2532](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L2512),
[:5893-5914](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L5893)) and compare against the remaining
active budget → **cut short** → the 9-anchor sweep is aborted, not merely degraded. **NO-GO on timing at 10 Hz.**

**Options to make timing fit (motion class):**
| option | effect | cost |
|--------|--------|------|
| slot_period 10 → **12 ms**, 10 slots | superframe 120 ms → **8.33 Hz** | −17% rate for *all* tags |
| slot_period 11 ms, **9 slots** | 99 ms → ~10.1 Hz | only 9 tag-slots (fewer tags) |
| **TAIL_COMPRESS**, SPACING 1000 → 875 µs | `1200 + 8·875 + 800 = 9000 µs` fits 9 ms | tightening spacing is exactly what caused the earlier tail drops — risky |
| **Static profile 40 ms / 24 ms** | 9.665 ms ≪ 24 ms, ~14 ms idle | base rate ~2.5 Hz (multi-slot for more) |

**Static profile (40 ms slot, 24 ms active):** `9.665 ms` fits with **~14 ms of idle tail** → timing GO.
This is the profile in which a 9th anchor is realistic.

---

## 7. BLE collision risk — the ge8 regression, extended to ge9 (Step 7)

### The documented mechanism
Comment block [ss_twr_init.c:4802-4870](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L4802)
(references memory `[[tdma-capacity-ble-phase-beat]]`): the multi-tag "random victim" loses **all**
responders after the first 1–3 with a *clean* non-detection — **"the manual re-arm is skipped when a BLE
connection event preempts the host in the gap right after a frame, leaving the receiver disarmed for the
rest of the slot."** It is explicitly **"a narrow per-tag BLE-event/UWB-slot phase collision, not re-arm /
readout fragility."** Both hardware mitigations were tried and **falsified**:
- **RXAUTR (single-buffer)** — catastrophic (2026-06-27): all 6 tags collapsed to rank-0.
- **RXAUTR + double-buffer** — rescued one victim but degraded healthy tags, RXOVRR 26–85% (2026-06-28).

There is **no MPSL / timeslot / radio-notification arbitration** in this app (grep: none). The application
can pause its *own* NUS TX (shortening the event) but **cannot stop or reschedule the connection event** —
the SoftDevice Controller owns that.

### The numbers
- **BLE conn interval = 7.5 ms** (6 × 1.25 ms), latency 0 → an event fires **every 7.5 ms, unconditionally**.
- **Not phase-locked** to the 10 ms slot. `gcd(7500, 10000) = 2500`, `lcm = 30000` → the conn-event phase
  relative to a tag's slot repeats every **30 ms** (3 sweeps). Each tag's slot has a **fixed** offset in the
  superframe, so its collision phase is quasi-static per connection → **chronic per-tag victims** (the
  "2 of 6 BLE-phase victims" in the logs).
- **Event duration:** empty (TX-paused) event ≈ sub-300 µs on 2M PHY; with pending telemetry up to
  ~1–2.5 ms (DLE 251 B). Either way it **preempts the CPU/SPI servicing the DW1000** for its duration.

### 8 anchors vs 9 anchors
```
                      collector span     tail-anchor window     slot idle margin
  8 anchors (10 ms)   0 → 8.665 ms       rank 7: 7.70–8.45 ms   ~1.35 ms  (event can hide here)
  9 anchors (10 ms)   0 → 9.665 ms       rank 8: 8.70–9.45 ms   ~0.34 ms  (no room to hide)
  BLE interval                                                  7.5 ms
```
- With **8 anchors**, the collector (8.665 ms) is barely longer than one BLE interval, and there is
  ~1.35 ms of idle tail where a conn event can land harmlessly. Victims occur only when a tag's fixed
  phase drops the event onto the rank-7 window (7.70–8.45 ms) — the documented "ge7 cap / ge8 near-impossible."
- With **9 anchors**, two things get worse simultaneously:
  1. The collector (**9.665 ms > 7.5 ms**) now **always contains a full BLE interval boundary** — every
     sweep is exposed, and for a large fraction of slot phases a conn event edge lands in the **new
     8.70–9.45 ms tail** where the 9th anchor (rank 8, the guaranteed last responder) lives.
  2. The **idle margin collapses to ~0.34 ms** — the conn event no longer has anywhere safe to sit; it is
     forced into the active collector.

**The 9th anchor becomes the new "always-last responder" and inherits the ge8 victim role with strictly
more exposure.** This is the same failure the RXAUTR/double-buffer work could not fix. **NO-GO at the
current BLE params in the motion profile.**

### When the BLE collision is survivable
In the **static profile (40 ms / 24 ms)** the collector (9.665 ms) occupies < 40% of the slot and leaves
~14 ms of idle tail. A 7.5 ms (or, better, a lengthened) conn event can be parked in that idle region away
from the responders. Combined with the timing headroom from §6, **the static/low-rate profile is where a
9th anchor is feasible** — assuming the structural mask fix.

### If you must have 9 anchors at high rate
The only principled path is to **stop the conn event from drifting into the collector**: phase-park BLE
connection events into the TDMA idle tail (MPSL timeslot / connection-event scheduling). That machinery is
**not present today** and is a substantial addition. Raising the conn interval (e.g. 15–30 ms + latency)
reduces the *number* of collisions but does not remove the beat unless the interval is chosen and phased to
sit in the idle tail — which at a 9.665 ms/10 ms slot there effectively isn't one.

---

## 8. Conclusion

A 9th anchor at the **current 10 Hz motion profile is a NO-GO**, blocked independently by (A) the 8-bit
on-air mask, (B) a 9.665 ms sweep that overflows the 9 ms active slot and trips the firmware's own guard,
and (C) a 7.5 ms BLE connection event that, with the idle margin gone, reproduces the ge8 phase-collision
on the new tail anchor.

A 9th anchor is a **CONDITIONAL GO at a lower rate / the static 40 ms profile**, where both the timing and
the BLE event fit with margin — **provided** the on-air mask is widened to `uint16_t` (frame +1 byte) and
the host-parser / listener / APOS / roster fixes in §5 are done.

The cheapest way to reach 9 anchors at ~10 Hz *without* new BLE-scheduling machinery is to **drop to
~8.3 Hz** (12 ms slot) — which restores the sweep headroom *and* re-opens an idle margin for the conn event
— rather than compressing the rank spacing (which historically caused the very tail drops this system was
tuned to avoid).

---

### Appendix — method & caveats
- Read-only; no files modified. All artefacts under `analysis/sweep_timing_audit/`.
- As-built values are the CMake cache `-D` overrides (`apps/tag`, `apps/anchor`, `apps/master_control`),
  which win over the `#ifndef` fallbacks in the `.c` files. Where a `.c` default disagrees (tag GUARD 500 /
  SPACING 800), the build override (1200 / 1000) is authoritative and matches the responder + the in-source
  reasoning comment at ss_twr_init.c:427-433.
- Frame airtimes (poll 335 µs, response ~250 µs) are the values the firmware itself uses/annotates; exact
  airtime depends on the DW1000 config (chan 9-ish, 128 preamble, 6.8 Mbps) but does not change any GO/NO-GO
  conclusion (all margins are ≥ several hundred µs or fail by ≥ 665 µs).
- "µs vs UUS": the code names the delay helper `..._delay_uus` but computes it from `_US` constants and the
  in-source comment treats them as plain µs (rank-7 → 8.2 ms + airtime → 8.45 ms). The audit follows the
  code's own µs bookkeeping; the ~2.6% UUS/µs distinction does not affect any threshold.
```
