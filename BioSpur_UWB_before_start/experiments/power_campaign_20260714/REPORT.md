# Power Campaign — 2026-07-14

Runtime TXPWR command + P1 pre/post comparison + fixed-position power sweep.
Tree: `SS-TWR/alt-SS-TWR/broadcast`. Builds on the P0 fix (TX_POWER=0x25456585,
TC_PGDELAY=0xC0, POR confirmed 0x0E080222).

---

## Phase 1 — Firmware: runtime `TXPWR` command

A BLE runtime command `TXPWR <MAX|M3|M6|M12|POR>` was added to **both** the tag and
the anchor, matching the existing command infrastructure:

- **Preset values (single source of truth):** `include/uwb_ss_twr_shared.h` defines the
  5 hex constants + a pure `uwb_tx_power_preset_lookup()` in `src/uwb_ss_twr_shared.c`
  (compiled into both tag and anchor — no divergence).
- **Tag:** `ss_twr_init_tx_power_apply()` (`src/ss_twr_init.c`) does
  `dwt_write32bitreg(TX_POWER_ID, val)` + logs `TXPWR set 0x%08X`; dispatched from
  `apps/tag/src/uwb_tag_ble.c` (`TXPWR ` → `TXPWR_OK VAL=0x…`).
- **Anchor:** `ss_twr_anchor_init_tx_power_apply()` (`src/ss_twr_anchor_init.c`);
  dispatched from `src/anchors/unified/anchor_ble_ctrl.c` (`TXPWR` token → `OK TXPWR VAL=…`).
- Writes **only** `TX_POWER` (0x1E). `DIS_STXP` (smart TX) and `TC_PGDELAY` untouched.

### Preset hex values + dB arithmetic

TX_POWER octet layout `[P125, P250, P500, NORM]`; each octet = coarse **DA** (bits 7:5,
~3 dB/step) + **mixer** (bits 4:0, ~0.5 dB/step). Total gain above the register floor in
half-dB = `DA*6 + mixer`. A reduction is subtracted (in half-dB) from **every** octet
(DA first, then mixer), floored at `0x00`, keeping the smart-TX ladder coherent. The
operative ranging bin is **BOOSTP250** (`0x45` in MAX = 8.5 dB above floor). Direction
confirmed against the empirical POR readback (P250 `0x45`=8.5 dB vs POR `0x08`=4.0 dB → the
P0 fix added the ~4.5 dB it was designed to).

| Preset | TX_POWER    | P250 byte | P250 dB (above floor) | Δ vs MAX (operative) |
|--------|-------------|-----------|-----------------------|----------------------|
| MAX    | `0x25456585`| `0x45`    | 8.5 dB                | 0 dB (ref)           |
| M3     | `0x05254565`| `0x25`    | 5.5 dB                | **−3.0 dB**          |
| M6     | `0x00052545`| `0x05`    | 2.5 dB                | **−6.0 dB**          |
| M12    | `0x00000005`| `0x00`    | 0.0 dB                | **−8.5 dB** (floor)  |
| POR    | `0x0E080222`| `0x08`    | 4.0 dB                | −4.5 dB (DW1000 POR) |

**M12 caveat (register floor):** a true −12 dB on the operative P250 byte is **physically
unreachable** with smart-TX enabled — `0x45` sits only 8.5 dB above `0x00`, so M12 clamps
to `0x00` = −8.5 dB. All 5 levels remain distinct and monotone in power:
`MAX(8.5) > M3(5.5) > POR(4.0) > M6(2.5) > M12(0.0)`.

### Build status
Both variants built clean (pre-existing unused-var warnings only). Command strings +
handlers verified linked in both binaries:
- Tag `build-tag-diagcheck-txpwr-20260714` (marker `unified-runtime-cir-agctxpwr-20260714`):
  `TXPWR set 0x%08X`, `TXPWR_OK VAL=`, `TXPWR_BAD PRESET=MAX|M3|M6|M12|POR`, HELP updated.
- Anchor `build-anchor-ota-txpwr-a19-g1200-r1000-20260714` (marker `altbcast-a19-g1200-r1000-txpwr`):
  `TXPWR`, `OK TXPWR VAL=`, `ERR:BAD_TXPWR`, HELP updated.

---

## Phase 2 — OTA deployment

**Tag path:** staged tag payload → rebuilt Master_Tag carrier
(`build-master-control-b120-m1-master-tag-lfrc-txpwr-20260714`) → flashed B120 SNR
1050070698 (dual-core) → OTA'd BS9336, BS955A, BSCCF4 (all `ota_started/reboot/reconnect/
phase_b` = True).

**Anchor path:** staged anchor payload → rebuilt Master_Anchor carrier → flashed **protected**
B120 SNR 960148546 (authorized) → OTA'd **Anchor A first = classification D**, then B–H
(all **classification D**). All 8 anchors deployed. (Anchors stay at MAX; the TXPWR command
is binary-verified for future use.)

**Verification:**
- 30 s capture: all 3 tags ranging (BS9336=1848, BS955A=2008, BSCCF4=1848 rows, 100% span,
  0 dropouts), anchors responder 8/8.
- Runtime readback on **all 3 tags**: `TXPWR MAX → 0x25456585`, `TXPWR M6 → 0x00052545`,
  `TXPWR MAX → 0x25456585` — all OK; tags restored to MAX.

> Coverage note: post-fix captures show ge7=0% / ~50% no_anchor_rx — a fixed
> wand-position/LOS artifact (the pre-fix 07-04 capture had 96.6% valid from a different
> position). Not firmware; relevant to Phase 3/4 interpretation.

---

## Phase 3 — P1: pre-fix vs post-fix range comparison

- **PRE-FIX:** `logs/ge7_test_20260704_032041` (2026-07-04, old fw, TX at POR; 12,864 rows,
  96.6% valid) — closest genuine pre-fix wand capture.
- **POST-FIX:** `logs/p0txrf_verify_bs9336_v5_20260714` (P0-fixed, TX=0x25456585; 9,064 rows,
  48.8% valid).

Per-link range deltas (post − pre), 14 comparable (tag×anchor) links:

| metric | value |
|--------|-------|
| delta mean | −86.6 mm |
| delta median | −76.0 mm |
| **delta std** | **259.7 mm** |
| delta range | −502 … +490 mm |
| per-link intra std (typical) | ~25–40 mm |

**Verdict — no measurable, isolatable power→range-bias shift.** The pre/post deltas scatter
from −502 to +490 mm with a 260 mm std — an order of magnitude larger than the per-link
noise (~30 mm). A genuine 4.5 dB bias effect would appear as a small **consistent**
common-mode offset; instead the deltas are large and inconsistent — the signature of a
**wand-position/geometry change** between 07-04 and 07-14 (corroborated by the 96.6%→48.8%
validity drop). This is physically expected: TX power sets link margin/SNR, not the
first-path timing that determines range in LOS. **Phase 4's fixed-position sweep is the
clean test of power→bias.** (`p1_results.json`)

---

## Phase 4 — Fixed-position power sweep + listener monitoring

Wand fixed (not moved/rotated); anchors at MAX; 5 levels × 3 min, **randomized order
M6, MAX, M12, M3, POR**. Each cell: `cmd_all TXPWR <preset>` (all 3 tags acked the exact
hex — n_ack=3 every cell), 10 s settle, 180 s capture (controller-reset per cell → parsed
`range_diag_joined.csv`). ~34k joined rows/cell. `results.json` + `figures/`.

### Frame drop-off (link success) vs power

| level | P250 dB | valid % | miss % |
|-------|---------|---------|--------|
| MAX   | 8.5     | **78.3**| 21.7   |
| M3    | 5.5     | **78.0**| 22.0   |
| POR   | 4.0     | 55.7*   | 44.3*  |
| M6    | 2.5     | 65.3    | 34.7   |
| M12   | 0.0     | 64.6    | 35.4   |

Clear trend: the two **high-power** levels (MAX, M3) hold ~78 % valid; dropping to M6/M12
loses ~13 pts (→ ~65 %). **Frames start dropping below ~M3 (5.5 dB).**
*POR (55.7 %) is anomalously low — POR is the DW1000 default with a **non-uniform** octet
ladder (`0x0E080222`: P125=7 dB, P250=4 dB, P500=1 dB), so its effective ranging power
depends on the poll's frame-length bin and isn't directly comparable on the P250 axis; POR
was also the last cell and had no listener coverage to rule out drift.

### Range bias vs power — essentially flat

Median range-bias shift relative to MAX, across the full 8.5 dB span:

| level | dB | median bias (mm) | mean bias (mm) | std (mm) |
|-------|----|------------------|----------------|----------|
| MAX   |8.5 | 0.0 (ref)        | 0.0            | 0.0      |
| M3    |5.5 | +0.2             | +12.8          | 41.0     |
| POR   |4.0 | −11.5            | −7.0           | 31.2     |
| M6    |2.5 | −13.6            | −4.0           | 33.9     |
| M12   |0.0 | −1.4             | +6.8           | 27.9     |

**Median bias shift ≤ 14 mm over an 8.5 dB reduction** — within the per-link range noise
(~25–40 mm). No systematic, monotone bias-vs-power trend. **TX power does not shift LOS
range bias** — consistent with Phase 3 and with the physics (power sets link margin/SNR,
not first-path timing). The mean is inflated by **one marginal link, BS955A↔anchor 0**
(+99 to +146 mm at *every* reduced level) — that weak link's LDE latches a later first path
at low SNR. So a real per-link effect exists on marginal links, but it is not common-mode.

### First-path margin / diagnostics vs power

- **First-path amplitude is AGC-normalized:** listener `fp1` is ~constant across the power
  extremes (L-955A ~7080, L-E ~7220 for M6/MAX/M12) despite the 8.5 dB TX swing — the RX
  AGC compensates, so the power effect shows up as **miss-rate**, not fp amplitude.
- Tag-side `tr_lde_thresh` (~790) and `tr_agc_stat1` (~2.86e5) are ~constant across all
  levels — expected, since they measure the tag's RX of the **fixed-power anchor responses**.
- `anchor_fp1` = 0: the anchor's reception diagnostics of the (swept) poll are not carried
  over-air in this firmware, so the direct anchor-side poll amplitude isn't available;
  link-success rate + listener fp1 are the available power proxies.

### Listener monitoring
L-955A (near wand) + L-E (anchor side), **460800 baud** (the default 115200 gave pure
garbage — a baud mismatch found and fixed mid-run), ~11.8k LPD rows each. **No within-cell
contamination** in any covered cell (fp1 std/mean < 0.5 → `contaminated=False`).
**Coverage caveat:** the sweep ran ~47 min (each cell ~10 min — slow BLE link setup under
the marginal wand-position coverage), outlasting the 35-min listener window, so the
listeners covered **cells M6/MAX/M12 (spanning the full 0–8.5 dB range)** but **not M3/POR**.
The uncovered cells are intermediate powers; since listener fp1 is AGC-flat regardless and
no contamination appeared in the extremes, this doesn't change the conclusions.

### Verdict
- **Bias shift per link:** negligible common-mode (≤14 mm median over 8.5 dB); TX power does
  not systematically bias LOS range. One marginal link (BS955A↔a0) biases +100–146 mm at
  reduced power (low-SNR first-path error) — the exception that proves margin, not power,
  drives range error.
- **First-path margin at each level:** AGC-normalized amplitude, so margin loss manifests as
  rising miss-rate: ~22 % at MAX/M3 → ~35 % at M6/M12.
- **Power level where frames drop:** below ~M3 (5.5 dB). MAX and M3 both hold ~78 % valid;
  M6/M12 fall to ~65 %. The P0 operating point (MAX, 8.5 dB) and even −3 dB (M3) keep the
  fleet at peak link success in this geometry.

---

## Constraints compliance
- Anchor TX stays at MAX (only tags swept) ✓
- Smart TX stays enabled (DIS_STXP untouched); TC_PGDELAY stays 0xC0 ✓
- Wand position/orientation fixed for the whole sweep ✓ (not moved)
- Power order randomized (non-monotonic): M6, MAX, M12, M3, POR ✓
- All 9 diagnostic columns present in tag captures ✓ (diag-enabled image)
- Listener logging during Phase 4 — **partial**: continuous but the 35-min listener window
  covered 3/5 cells (M6/MAX/M12, the power extremes); the sweep ran longer (~47 min) than
  estimated, so M3/POR uncovered. No contamination in covered cells.
- TXPWR restored to MAX on all tags after the sweep ✓ (re-confirmed: all 3 acked 0x25456585)
