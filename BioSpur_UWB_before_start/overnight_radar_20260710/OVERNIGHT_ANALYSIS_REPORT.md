# Overnight CIR Capture — Full Analysis Report
**Capture:** `overnight_radar_20260710` · **Analyzed:** 2026-07-10 · Ch5 6.5 GHz, 499.2 MHz BW (λ≈4.6 cm), PRF 64 MHz

---

## Executive summary

A ~10.5-hour unattended overnight capture recorded UWB channel impulse responses (CIR)
from **3 static wand tags** into **6 passive listeners**. The analysis splits cleanly
into two verdicts:

- **✅ Channel & hardware quality is excellent** — and the headline result is that the
  **coherent CIR templates do not drift beyond 1σ over the full 10.5 h** (worst channel
  0.84σ, CFO < 0.05 ppm/h). This means a *static background template captured once is
  valid for >10 h* → **static background subtraction is viable for multi-hour person
  tracking** without adaptive re-estimation.

- **⚠️ Imaging with this geometry is fundamentally range-limited.** A static wand
  aperture over an empty room yields a **range-only "bullseye"**, not a room map: the
  measured multistatic point-spread function is **6.0 × 5.2 × 4.0 m** with only **6.5 dB**
  dynamic range. This confirms (and now quantifies) the prior conclusion that clean
  spatial imaging needs a moving aperture + coherent autofocus, or a pivot to
  people-sensing.

A secondary outcome: the AutoPos anchor re-survey surfaced **two physically disturbed
anchors (B, H)**; the other six solve to a clean **39 mm** layout.

---

## Data provenance

| listener | role / position | lines | CIR frames (clean) | scalar rows |
|---|---|---:|---:|---:|
| LB | @ anchor B | 5.76 M | 49,728 | 1.43 M |
| LE | @ anchor E | 5.75 M | 49,682 | 1.42 M |
| LF | @ anchor F | 5.70 M | 49,344 | 1.40 M |
| L9336 | @ wand tag BS9336 | 5.73 M | 49,404 | 1.43 M |
| L955A | @ wand tag BS955A | 5.77 M | 49,794 | 1.43 M |
| **LCCF4** | @ wand tag BSCCF4 | 5.74 M | **0** (76% chunk corruption) | 0.31 M |

- **TX sources:** 3 wand tags — `0xb136`=BS9336, `0xb15a`=BS955A, `0xb1f4`=BSCCF4
  (a precise rigid T; pairwise 660–709 mm — never collapsed to a point).
- CIR is captured **only on tag polls**; anchors appear as scalar-only records. So every
  CIR waveform is wand-sourced → anchor antenna orientation does **not** enter the CIR.
- **15 clean CIR channels** = 5 clean listeners × 3 tags. LCCF4 excluded from all
  CIR/coherent analysis (0 assembled frames); its *scalar* stream is separately validated.

---

## Part A — Channel & hardware characterization (coordinate-free)

### Step 1 — Coherent CIR templates (15 channels)
Alignment: coarse first-path → tap 800, RCPHASE sub-tap, first-path phase referencing
(removes per-device clock), rxpacc amplitude normalization → complex median template + MAD σ.
**Direct-path SNR 13.8–40.7× (median 23.4×)** — strong, clean channels.

### Step 2 — Coherent stability / template shelf-life  ⭐ HEADLINE
30-min windows over the night; per-window template drift measured in noise-floor σ.
**All 15 channels stay below 1σ for the entire 10.5 h** (worst max-drift 0.84σ, median
0.30–0.55σ); CFO drift < 0.05 ppm/h. → *A background template has >10 h shelf-life.*
Figure: `analysis/step2_stability.png`.

### Step 3 — Multipath / reflector extraction
Per-channel reflector tables (15 files), tap spacing **1.0016 ns** (998.4 MHz accumulator;
0.30 m/tap). The direct-path main lobe spans ~11–21 taps → a **near-in blind zone of
~3.6–6.3 m bistatic excess** hides close reflectors under the direct pulse; only
well-separated reflectors are recoverable. Cleanest channel (LB←0xb1f4, SNR 40.7):
6 reflectors >5σ at 6.6–17.4 m bistatic excess, plus a diffuse reverberant tail at
~2.2σ (1.57× the causal pre-first-path noise floor).

### Step 6 — Ranging-noise / channel-quality table (55 channels)
Honest scalar-domain number: raw LDE first-path jitter (MAD) → **median range jitter
~0.9 m** (tap spacing 1.0016 ns; this is the *coarse* per-frame LDE index — the true
coherent precision is mm-class, characterized by Step 1 SNR + Step 2 phase stability,
not this scalar figure).

### Step 7 — LCCF4 scalar cross-validation
LCCF4 yields **zero CIR**, but its strict-validated *scalar* diagnostics are **physically
sane and comparable-spread to a clean listener** → **trustworthy for scalar use**
(presence / coarse timing) only; excluded from all CIR work.

### Steps 8–9 — AGC & EVC hardware health
- **AGC = 0 on every frame, all listeners** (8 M+ frames) — no gain anomalies.
- Clean-5 link health is **excellent**: CRC error rate **5.7×10⁻⁵ – 9.3×10⁻⁵** (~0.006–0.009%),
  **zero RX overflows**, ~1.4 M good frames each.
- LCCF4 is the outlier and explains its 0 CIR: **CRC rate 5.4%**, **122,880 RX overflows**.

---

## Part B — Anchor layout re-survey (AutoPos)

Re-run because anchors had moved. Outcome + hardware findings:

- **Anchor H would not promote to master** (`rc=-116` ETIMEDOUT) and hung a 1000-set
  sweep for 36 min. **An all-anchor power-cycle fixed the promotion**; three subsequent
  100-set sweeps completed cleanly (all A–H, 0 promote failures, responder mode restored).
- **Two anchors are physically disturbed** and cannot be range-recovered by re-orientation:
  - **Anchor B** — antenna rotated toward C when its cable was stepped on. Range bias
    follows boresight; rotating it back only moved the bad link (now B–A). Likely antenna
    damage or corner geometry.
  - **Anchor H** — antenna pointed at the C/D/G side; reads the A/E/B/F (low-Y) anchors
    long by 0.5–2 m. Nudged during the power-cycle.
- **Clean core A, C, D, E, F, G self-solves to 39 mm.** Best-effort layout keeps these at
  full confidence and places B, H robustly with flags (B: ~70–177 mm, B–A rejected; H:
  ~200 mm). → `analysis/autopos/layout_besteffort.json`.
- Antenna orientation does **not** affect the overnight CIR (anchors are scalar-only), so
  B/H bias is a *layout-measurement* artifact, not a data-quality issue.

### Wand position (Step-4 input)
6-DOF **rigid-body pose** fit (using the known rigid-T shape, clean-6 anchors only) →
tag world positions with **104 mm RMS** residual (wand→E and wand→G links slightly
obstructed). Centroid ≈ **(2498, 1213, 1094) mm**, mid-room. → `analysis/autopos/wand_positions_rigid.json`.
Figure: `analysis/rig_geometry.png`.

---

## Part C — Multistatic imaging (Steps 4–5)

15-channel excess-delay backprojection (each channel self-referenced to its own direct
path — no cross-listener clock; incoherent across channels). Direct path removed.

**Result — a range-only bullseye, not a room map** (`analysis/step4/step4_backprojection_mip.png`):
concentric excess-delay ellipsoid shells around the TX/RX cluster with a central
blind-zone hole; no discrete reflectors or resolved walls.

**Quantified aperture resolution (this is also the Step-5 answer):**
| metric | value | meaning |
|---|---|---|
| point-target PSF (−6 dB) | **6.0 × 5.2 × 4.0 m** | no usable spatial resolution |
| image dynamic range | **6.5 dB** | (a real image needs 20+ dB) |
| near-in blind zone | **3.6 m** | close scatterers buried under direct path |

This is the expected physics: a **static** small aperture + **specular** indoor walls +
near-in blind zone → range information only, no azimuth. An empty overnight scene has no
moving target to separate from the static background.

---

## Conclusions & recommendations

1. **Use the overnight data for what it is strong at — channel characterization.** The
   >10 h template stability is the key enabling result: **static background subtraction is
   valid for multi-hour operation**, which is exactly what person-presence/tracking needs.
2. **Do not pursue static-wand room imaging** — it is range-limited by geometry (now
   quantified: 4–6 m PSF). For spatial imaging, either (a) a **moving aperture** (RotoArm
   circular-SAR) with **coherent autofocus + a corner-reflector beacon**, or (b) retarget
   to **people-sensing** (presence / motion / fall / respiration) with a dedicated
   fast 1–2-tag capture (respiration is Nyquist-blocked on the 3-tag rate here).
3. **Fix anchors B and H before the next survey** — B likely needs an antenna
   inspection/replacement (step damage), H a re-point toward room center. The clean-6
   layout (39 mm) is trustworthy in the meantime.

---

## Outputs index

**Machine-readable**
- `analysis/parsed/` — {L}_scalar.npz, {L}_cir.npy, {L}_cir_index.npz, {L}_lstat.npz, step0_summary.json
- `analysis/templates/` — 15 × {L}_{src}_A.npy + _sigma.npy, step1_report.csv
- `analysis/stability/drift_summary.csv` · `analysis/multipath/*_reflectors.json`
- `analysis/noise/per_channel_noise.csv` · `analysis/health/step89_agc_evc.json` · `analysis/xval/step7_lccf4_xval.json`
- `analysis/autopos/layout_besteffort.json` · `layout_clean6.json` · `wand_positions_rigid.json`
- `analysis/step4/step4_stats.json` · `backprojection_volume.npy`

**Figures (150 dpi)**
- `analysis/step2_stability.png` (headline) · `analysis/rig_geometry.png`
- `analysis/step4/step4_backprojection_mip.png` · `step4_psf_mip.png`

**Scripts** — `analysis/step0_parse.py` … `step4_backprojection.py`, `autopos/best_effort_layout.py`, `autopos/wand_rigid_pose.py`
