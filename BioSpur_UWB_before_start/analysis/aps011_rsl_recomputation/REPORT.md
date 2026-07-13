# APS011 Recomputation via CIR-derived RSL — offline, no new scans

**Data:** pre-APS011 baseline only — `logs/geiger_scan_20260711_161258_8anchor/scan.log`
(517 LSCAN cycles, 487 with CIR, 485 valid CIR→RSL points, 3225 LOO residuals).
**Method:** reuses locked `pg_lib` primitives (parse / FP-detect / trilaterate / LOO / gauge).
Script: `recompute.py` → `recompute_result.json`, `sweep.png`.
**Compute:** i7-8700K, 12 logical CPUs, Pool(10); 81-point sweep, 502 s child-CPU / 54 s wall ≈ **9.2 cores busy**; 66 s total.

---

## 1. RSL proxy from the CIR (Q1, Q3)

The LSCAN line carries no RXPACC/CIR_PWR/FP_AMPL and **`agc=0` on every line** (the
firmware emits AGC as a placeholder), so there is no gain readout to de-normalise the
accumulator. We therefore estimate first-path signal level directly from the logged
CIR accumulator, using the locked FP detector for the tap:

- **Proxy A (used):** `10·log10(mag[fp]² + mag[fp+1]² + mag[fp+2]²)` — mirrors the DW1000
  FP formula (FP_AMPL1²+2²+3²).
- Proxy B `20·log10(fp_mag)` and Proxy S `10·log10(fp_pwr/noise_pwr)` computed for cross-check.

**Correlation with distance is weak — this is the whole story:**

| proxy | Pearson(rsl, log₁₀ range) |
|-------|---------------------------|
| **A** | **−0.288** |
| B | −0.198 |
| S | −0.176 |

Per-anchor RSL-vs-range slopes: **−1.6 … −8.4 dB/decade** (A:−4.5 B:−8.4 C:−7.9 D:−7.0
E:−3.9 F:−2.4 G:−4.4 H:−1.6) — all far **flatter than free-space −20 dB/decade**. In a
~1.5–4 m room (only 0.43 decades of range) with multipath and no AGC compensation, the
accumulator FP-power barely tracks range. The sign is right (closer = stronger) but the
information content is low.

## 2. Operating RSL span (Q4)

At the min-LOO operating center the population RSL span (p5…p95) is **only 2.7 dB**
(−79.4 … −76.7 dBm; absolute dBm is arbitrary, set by the sweep offset — the *span* is
data-driven and real). Expected span for a clean 1.5–6 m link would be ~−61…−75 dBm (≈14 dB).
Because the measured span is ~2.7 dB, an RSL-indexed Table-2 lookup is **nearly a constant
offset** — it cannot supply a meaningful range-dependent (slope) correction.

## 3. Sweep optima (Q2)

Sweeping the single absolute-calibration offset (RSL operating center, −95…−55 dBm):

| target | center dBm | slope % | common-mode mm | LOO median mm |
|--------|-----------|---------|----------------|---------------|
| min \|slope\|        | −72.5 | **+0.09** | +54 | 172 |
| min \|common-mode\|  | −74.5 | +1.27 | **+2** | 163 |
| **min LOO median**   | −78.0 | +3.82 | −110 | **155** |

The three optima disagree, and the LOO-optimal point leaves the slope essentially at the
raw value (+3.82 vs +3.65) while barely moving LOO (155 vs 158). Forcing slope→0 *costs*
LOO (172). That divergence is the signature of a correction with no real range information.

## 4. Method comparison — same baseline data (Q5)

| method | slope % | common-mode mm | LOO median mm | note |
|--------|---------|----------------|---------------|------|
| raw (no correction) | +3.65 | −100 | **158** | baseline |
| naive `dwt_getrangebias` | −0.97 | +99 | 180 | firmware-exact, on *this* data |
| CIR-RSL Table 2 (min LOO) | +3.82 | −110 | 155 | this analysis |
| CIR-RSL Table 2 (slope=0) | +0.09 | +54 | 172 | this analysis |
| slope-only (remove pooled slope) | +2.32 | −45 | 157 | reference |

Two things jump out:

1. **The naive firmware, evaluated on *identical* data (no walk-to-walk confound, no +100 mm
   Geiger offset), is *not* the 3× catastrophe the field summary reported (251 mm).** On the
   same walk it nearly zeroes the slope (+3.65→−0.97) and worsens LOO only 158→180. The field
   "−7.01% / 251 mm" was inflated by comparing two *different* walks **plus** the +100 mm
   antenna-delay double-count. Corrected read: naive APS011 is net-negative but modest.
2. **No range/RSL-indexed correction beats raw by more than noise.** Best case is LOO 155 vs
   158 (~2%). Even the *statistical* slope-only correction, which zeroes the +3.65% slope by
   construction, leaves LOO at 157. **The gauge slope is not the dominant error.**

## 5. Where the error actually is (the decisive diagnostic)

Decomposing the 158 mm LOO floor per anchor:

| | A | B | C | D | E | F | G | H |
|-|---|---|---|---|---|---|---|---|
| mean resid (mm) | −156 | +68 | +214 | −49 | +194 | −74 | −108 | +24 |
| std (mm) | 291 | 238 | 286 | 287 | 232 | 238 | 240 | 245 |

- **Per-anchor CONSTANT bias RMS = 129 mm** (A −156, C +214, E +194 …). This is antenna-delay /
  AutoPos residual — a *constant* per anchor, **which APS011 does not touch at all**.
- Removing the per-anchor constant drops LOO median **158 → 134 mm** — the single biggest lever.
- The remaining ~134 mm (std ~240–290 mm/anchor) is random multipath/GDOP — **irreducible** by
  any bias correction.

Error budget: range-proportional (what APS011 targets) ≈ 0 mm of real benefit; per-anchor
constant ≈ 24 mm of removable LOO; multipath floor ≈ 134 mm.

## 6. Recommendation (Q6)

**Direct-RSL APS011 is not worth pursuing in firmware.** Three independent reasons:

1. **The data can't feed it.** The logged CIR gives only a weak (r=−0.29), ~2.7 dB RSL proxy;
   calibrated RSL needs RXPACC + CIR_PWR + AGC registers that the LSCAN path deliberately skips
   (`agc=0`). Adding them + re-walking would be real firmware+field effort.
2. **Even perfect RSL wouldn't help.** The range/slope term APS011 corrects contributes ≈0 to
   actual ranging error here (slope-only proves it: 158→157). You'd be spending effort to fix a
   3–4% slope that doesn't matter.
3. **The real lever is elsewhere.** 129 mm RMS of *per-anchor constant* bias (→158→134) is an
   antenna-delay / AutoPos recalibration, not an RSL table.

**Actions:**
- **Revert the naive APS011** on the fleet (tags re-OTA pre-APS011, Geiger `getrangebias` off and
  `GEIGER_ANTENNA_DELAY_OFFSET_MM` → 0). It's net-negative and double-counts the offset.
- **Do NOT** extend LSCAN with RSL fields or re-walk for direct-RSL. **How many more room walks
  for the RSL approach: zero — the approach is a dead end.**
- If ranging is to be improved, spend the effort on **per-anchor constant offset (antenna-delay)
  recalibration from a static known-distance tripod ladder**, not on any RSL/range-indexed term.
  That is the only correction the data says will move the needle, and the tripod ladder is the
  clean way to separate constant offset from the (negligible) slope.
