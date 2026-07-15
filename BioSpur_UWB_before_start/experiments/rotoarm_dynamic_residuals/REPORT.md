# RotoArm Dynamic Residual Analysis — Fusion Measurement-Model Validation

**Session:** `erlangen_20260528_optitrack` (Vicon, 120 fps) · roto captures **R01–R17** (= the prompt's "IDs 25–41") · two tags on the arm simultaneously at different radii: UWB **BS2DCE ↔ Vicon WandB, R ≈ 440 mm** and **BSDC91 ↔ WandC, R ≈ 554 mm** (assignment resolved per-capture by a range-vs-geometry scale test, slope ≈ 1.0, not assumed). Eight static anchors A–H (antenna markers). Tilt series 1° → 90°.
**Generated:** `analyze_roto.py` (12 workers, extract 36 s wall; full run incl. Task 5 + C1 sweep ≈ 4 min). Read-only on all capture data. Reuses the Erlangen "Model Outputs" Vicon convention from `../antenna_orientation_erlangen/analyze.py`.

This is **not** a root-cause investigation — that is closed by the Erlangen ID13–24 follow-up (Layer 1 = smooth ~46 mm orientation bias on shallow links; Layer 2 = 300–500 mm stable wrong-path locks on steep ≥ 37° links). This validates the **fusion measurement model** against dynamic data. It answers Q-A (does static Layer-1 transfer to motion?), Q-B (systematic vs stochastic residual split — the R-matrix), Q-C (intra-sweep time-skew magnitude).

## TL;DR

- **R-matrix (Q-B, primary deliverable).** Dynamic geometry-corrected residual splits **~45–55 % systematic (revolution-repeatable) / ~45–59 % stochastic (non-repeatable → R)**. Stochastic RMS is **~50–82 mm**, elevation-dependent (higher fraction on steep links). The systematic is *genuine*: a global timing fit removes **0.1 %** of its variance and a rigid antenna lever-arm removes < 6 %, so it is not an alignment artifact.
- **Layer-1 under motion (Q-A): CHANGED — larger and confounded.** The angle-periodic systematic is **~85–100 mm RMS** (flat-tilt peak-to-peak template median **395 mm**), 2–4× the static shallow-link Layer-1 (46 mm / 30–95 mm p2p), and non-sinusoidal (2nd harmonic ≥ ½ the 1st in 69 % of anchors). On a RotoArm θ sets **both** orientation and position, so this is *inseparably* orientation + position-multipath. A static per-orientation correction table does **not** transfer.
- **Time-skew (Q-C): NEGLIGIBLE.** Direct magnitude p99 **≈ 6 mm**, median ≈ 1 mm (one Vicon-derivative spike inflates the raw max to 18.6 mm). It scales with tangential speed exactly as predicted (radius ratio 1.251 ↔ skew ratio 1.239). **No per-rank Δt term is needed** in the fusion propagation model.
- **Dynamic Layer-2: YES, but a different mechanism.** 14 high-confidence stable phase-sector locks (excess 300–636 mm over the anchor baseline, sustained across 22–29 revolutions). Unlike static Layer-2 they sit predominantly on **shallow** links (53/60 flagged sectors ≤ 25° elevation; only 1 steep) and cluster on **anchor A** — i.e. they are **position/multipath-triggered** (a reflection dominates at a particular orbit location), not the static elevation-null mechanism.
- **Alignment robustness (C1).** Every headline number moves **< 1.3 %** under a ±1 Vicon-frame (±8.3 ms) global re-alignment. The only alignment-*dependent* quantities are the absolute per-link biases of Task 1, which are flagged and not treated as findings.

---

## Methodology note — time alignment (governs C1)

There is **no hardware time sync**. The UWB `host_elapsed_s` ↔ Vicon-frame map is not a constant offset: a single global best-fit τ leaves a ~180 mm residual, and per-segment fitting shows τ drifting **0.9–1.4 s over 120 s, non-linearly** — far too large (~9000 ppm) for crystal drift, so it is host-timestamp latency / small rotation-rate variation, not a clock. The fix is a **non-uniform windowed local time-warp** (one τ per 5 s window minimising the per-anchor-*demeaned* residual, then interpolated per sweep). All analysis then lives in **Vicon rotation-phase θ**, which survives an unknown global offset (C1): per-rank differentials (Task 2), angle-periodic components (Tasks 1/3/4), and circle geometry (Task 5) are all offset-invariant. Absolute per-link bias attribution (Task 1 means) is *not* offset-invariant and is flagged ALIGNMENT-DEPENDENT throughout.

Per-anchor demeaning is essential: the raw residual carries a **~150 mm per-tag common range bias** plus per-anchor delay that would dominate any naïve alignment metric.

---

## Task 0 — Capture inventory

The Erlangen R01–R17 dual-tag tilt series is the **only** RotoArm dataset with Vicon ground truth. Other roto sessions (`outdoor_v4_20260504` ID28–31, `test_18052026`, `erlangen_20260519`) have **no** mocap → usable for circle-fit only, and are not analysed here. There is **no home-rig roto+Vicon capture**.

| capture | tilt° | BS2DCE (WandB) R / rev / v_tan | BSDC91 (WandC) R / rev / v_tan | Vicon oop RMS | notes |
|---|---|---|---|---|---|
| R01 | 1 | 426 mm / 31.5 / 704 mm/s | 554 mm / 31.5 / 914 mm/s | 0.9 / 0.8 mm | flat |
| R02–R05 | 22 | 440–462 / 2–29 / 660–690 | 554 / 28–29 / 826–836 | 1.0–1.7 | |
| R06–R09 | 48 | 439–444 / 9–24 / 274–544 | 554 / 23–24 / 674–692 | 1.0–1.7 | inner-tag occlusion R06/R07 |
| R10–R13 | 72 | 441–456 / 23 / 523–545 | 554 / 23 / 657–673 | 1.3–2.0 | |
| R14,R16,R17 | 90 | 441 / 22 / 510–525 | 554 / 22–23 / 644–660 | 2.1–2.8 | vertical plane |
| **R15** | — | — | — | — | **SKIP: Vicon markers all-NaN** |

16 usable captures, ≈ **1.28 M residual samples**. Rotation ≈ 0.26 rev/s (period ≈ 3.8 s). Radius ratio WandC/WandB = **1.25** (the dual-radius lever for Task 2). Vicon out-of-plane scatter ≤ 2.8 mm confirms clean planar ground truth. The inner tag's Vicon marker (WandB) is intermittently occluded at high tilt, costing R06/R07 some valid revolutions — flagged, not fatal.

---

## Task 1 — Residual extraction

`e = range_meas − |p_tag_Vicon(t*) − a_anchor|`, absolute vs Vicon geometry (C2), every sample tagged with anchor, rank (= anchor_id), θ, revolution, radial velocity, and link elevation (C3).

**Validity** 98.6–99.8 % per (tag × anchor). **Absolute residual means 176–299 mm** (dominated by the ~150 mm common range bias + per-anchor delay + orientation). *These absolute means are ALIGNMENT/convention-dependent and are reported for completeness, not as findings (C1).*

**Phase-sector locks (candidate dynamic Layer-2).** Flag = a 30° θ-sector whose median residual exceeds the anchor's own median by > 150 mm. Caveat: the normal angle-periodic swing is already ±174 mm (see Task 3), so the 150 mm bar overlaps ordinary harmonic peaks. Filtering to **excess > 300 mm** (beyond ~2× the 1st-harmonic amplitude) isolates **14 high-confidence locks**:

| capture | tag | anchor | excess (mm) | n_rev | sector σ (mm) | elev° | tilt° |
|---|---|---|---|---|---|---|---|
| R05 | BS2DCE | A | +636 | 29 | 380 | 26 | 21 |
| R10 | BSDC91 | A | +529 | 23 | 226 | 22 | 72 |
| R06 | BSDC91 | A | +471 | 24 | 157 | 23 | 48 |
| R09 | BS2DCE | F | +449 | 23 | 274 | 10 | 47 |
| R07 | BSDC91 | A | +420 | 23 | 228 | 12 | 48 |
| R13 | BSDC91 | C | +403 | 22 | 270 | 8 | 72 |
| R11 | BS2DCE | A | +396 | 23 | 164 | 11 | 73 |
| R16 | BSDC91 | H | +394 | 22 | 206 | 7 | 90 |
| R02 | BS2DCE | G | +363 | 29 | 117 | 14 | 22 |
| … | | | (14 total > 300 mm; 32 > 250 mm; 140 > 150 mm) | | | | |

The stable ones (σ ≈ 120–270 mm, present across 22–29 revolutions) are fixed wrong-path locks that fire in one orbit sector. **Two things distinguish them from static Layer-2:** (i) 53/60 sit on **shallow** links (≤ 25°), only 1 on a steep link — the *opposite* of the static elevation-null population; (ii) **anchor A** carries 18/60. This is a **position/multipath** mechanism — a specular reflection wins the first path at a particular orbit location (and near a particular anchor) — not the elevation-plane antenna null of the static test.

---

## Task 2 — Time-skew (Q-C)

Nominal TDMA slot = 9 ms active / 8 anchors = **1.125 ms/rank**. On-device timing fields (`first_to_last_us`, `air_us`) are all zero in this capture, so rank = anchor_id and the epoch offset of rank *i* is *i*·1.125 ms.

**Direct magnitude** `|ḋ · rank · slot|` (needs no fit; ḋ from Vicon):

| tag | radius | max ḋ (mm/s) | skew median | skew p99 | skew max | adj-rank step max |
|---|---|---|---|---|---|---|
| BS2DCE | 443 mm | 3519\* | 0.9 mm | **4.8 mm** | 18.6\* mm | 4.0 mm |
| BSDC91 | 554 mm | 1105 | 1.1 mm | **5.9 mm** | 8.0 mm | 1.2 mm |

\* the 18.6 mm max and 3519 mm/s come from a single Vicon numerical-derivative spike (unphysical: exceeds v_tan). The robust figure is **p99 ≈ 6 mm**.

**2.2 Radius scaling.** The skew must scale with tangential speed (∝ radius at fixed ω). Measured: **radius ratio 1.251, skew-p99 ratio 1.239** — a clean confirmation the effect is the predicted kinematic term.

**2.3 Verdict.** p99 ≈ 6 mm, median ≈ 1 mm — **NEGLIGIBLE** against the ~50–82 mm stochastic floor and the ~85–100 mm systematic. Skew-compensating the geometry (interpolating Vicon at *t*+rank·slot) changes the residual RMS by < 3 mm. **The fusion propagation model does NOT need a per-rank Δt term (NO).**

---

## Task 3 — Angle-periodic component (Q-A, Q-B core)

Per (tag × anchor) the residual is decomposed into a **repeatable θ-template** (per-θ-bin mean over all revolutions = systematic) and **within-θ-bin scatter** (sweep-to-sweep at the same phase = stochastic). Per-revolution 5-parameter harmonic fits give the coefficient repeatability and 1st/2nd-harmonic content.

### 3.3 Variance split — THE R-MATRIX (fusion copies this)

Pooled over all captures/tags/anchors, per link-elevation bin:

| elevation bin | n(tag×anchor) | systematic RMS | stochastic RMS | systematic frac | total RMS |
|---|---|---|---|---|---|
| shallow ≤ 25° | 254 | **91 mm** | **82 mm** | 0.55 | 123 mm |
| unverified 25–37° | 223 | 68 mm | 67 mm | 0.51 | 95 mm |
| steep ≥ 37° | 70 | 43 mm | 52 mm | 0.41 | 67 mm |

By tilt group (fuller θ coverage per anchor, less bin-edge truncation):

| tilt | systematic RMS | stochastic RMS | systematic frac |
|---|---|---|---|
| 1° (flat) | 100 | 79 | 0.62 |
| 22° | 95 | 90 | 0.53 |
| 48° | 88 | 77 | 0.57 |
| 72° | 84 | 72 | 0.58 |
| 90° | 89 | 71 | 0.61 |

**Reading for fusion.** The non-repeatable term that belongs in **R is ~50–82 mm RMS** (rising in fraction on steep links, where first-path instability adds jitter). The remaining ~45–55 % is angle-repeatable and *predictable in principle* — but only if fusion indexes it by orientation/position; it is not white noise. The steep bin's lower *absolute* systematic is partly a bin-truncation artifact (steep links occur only over a narrow θ arc), so read the by-tilt table as the cleaner cut.

**Systematic is genuine, not an alignment artifact.** A global timing fit `e ≈ k·ḋ` removes **0.1 %** of the angle-periodic variance (per-anchor correlations with ḋ are mixed-sign), and a best-fit rigid antenna lever-arm removes < 6 %. So the ~90 mm systematic is real orientation + position-multipath structure.

### 3.4 Magnitude vs static (Q-A)

Flat-tilt (R01) template peak-to-peak: **median 395 mm** (range 273–531 mm) — versus the Erlangen static shallow-link Layer-1 of 30–95 mm p2p (46 mm RMS). Under motion the angle-periodic systematic is **2–4× larger**. Because θ sets orientation *and* position on a RotoArm, this bundles orientation directionality, position-multipath, and geometry inseparably (the prompt's 3.2 caveat) — I do not claim to split them. Likely the continuous 360° yaw sweep simply resolves swing that the static 4-point (90°-spaced) sampling under-sampled, but that cannot be proven here.

### 3.5 Harmonic content

Median 1st-harmonic amplitude **A1 = 174 mm**, 2nd **A2 = 89 mm**. A2 ≥ A1 in **0 %** of anchors (1st always dominant) but A2 ≥ ½·A1 in **69 %** — **not a clean cosine**, echoing the static "92 % non-cosine" finding in the same direction. Per-revolution coefficient repeatability is moderate (the harmonics are stable enough to be called deterministic, consistent with the ~55 % systematic fraction).

---

## Task 4 — Tag × anchor interaction (dual-tag)

Both tags ride the same arm → identical yaw(θ), different position (radius) and different electronics. This makes the correlation of their per-anchor θ-templates diagnostic.

**4.1 Harmonic correlation.** Raw template correlation ≈ 0 (the two tags sit at a constant arm-phase offset); after aligning that offset, **median 0.69** (range 0.37–0.87). A pattern that is shared across two tags of different radius and different electronics is driven by the variable they *share* — **yaw/orientation and anchor-azimuth geometry** — not tag-specific electronics nor radius-specific position-multipath. (This is the strongest available hint that the Task-3 systematic is orientation/geometry-led, though orientation vs position remain formally inseparable.)

**4.2 Constant term.** Fitting `c0[tag,anchor] = μ + d_tag + d_anchor` to the absolute per-link constants: μ = 152 mm, **d_tag = ±21.5 mm** (43 mm tag-to-tag spread, matching the prior per-tag d_tag estimate), d_anchor spanning −27…+64 mm. The **interaction residual (what the additive model cannot absorb) is RMS 4.2 mm, max 6.7 mm** — negligible. **The constant biases are cleanly additive; there is no meaningful tag × anchor coupling.** Fusion can carry one d_tag per tag and one d_anchor per anchor without an interaction table.

---

## Task 5 — Circle-fit radius (report-only, C4)

Per-epoch trilateration of the UWB ranges (current firmware delays, Vicon anchor positions) → circle fit → recovered radius vs the Vicon truth. **This is per-epoch trilateration, not the V4/V5 production solver** (which was out of scope); the radius is never fed back into any solver (C4).

| tag | radius (Vicon) | recovered over-radius | in mm | oop scatter (UWB) |
|---|---|---|---|---|
| BS2DCE | 443 mm | **+40 %** (median +41 %, 20–53 %) | +179 mm | 37–265 mm |
| BSDC91 | 554 mm | **+30 %** (median +31 %, 21–38 %) | +167 mm | 42–173 mm |

The over-recovery in **mm (+179 / +167) equals the tag common range bias** (μ + d_tag ≈ 174 / 131 mm), i.e. the inflation is the ~150–175 mm common range offset distorting trilateration outward — **not a solver scale factor**. This is a different quantity from the prior "V4 ≈ +4.4 %, V5 ≈ +1 %" (which is the residual scale *after* delay calibration); on raw per-epoch trilateration the absolute common bias dominates. Out-of-plane scatter (37–265 mm) grows with tilt and is pure UWB position noise (Vicon oop is < 3 mm).

---

## C1 — Alignment sensitivity

Re-extracting the R01/R08/R14 subset with the global alignment shifted **±1 Vicon frame (±8.3 ms)**:

| shift | systematic RMS | stochastic RMS | skew max | radius %err |
|---|---|---|---|---|
| −1 frame | 87.5 | 76.3 | 7.6 | 33.3 |
| 0 | 87.1 | 76.6 | 7.7 | 33.3 |
| +1 frame | 87.0 | 76.7 | 7.6 | 33.3 |

Movement: systematic 0.6 %, stochastic 0.5 %, skew 1.3 %, radius 0.0 % — **none moves > 10 %.** The angle-space analysis is robust to the residual alignment uncertainty, as intended by design. The only alignment-*dependent* numbers are the Task-1 absolute per-link means, already flagged.

---

## Limitations

Single room / single session (Erlangen mocap lab); the systematic magnitude and the position-multipath locks are environment-specific and will differ at home. RotoArm confounds orientation with position by construction — the Task-3 systematic is not decomposable into pure orientation. The inner tag's Vicon marker occludes at high tilt, thinning R06/R07. Time alignment is post-hoc; all reported numbers are the offset-surviving ones and pass the ±1-frame check.

---

## DECISION

**D-A — Layer-1 under motion: CHANGED (larger, confounded).** The angle-periodic systematic is **~85–100 mm RMS** (flat template p2p median 395 mm), 2–4× the static shallow-link Layer-1 (46 mm), and non-sinusoidal (A2 ≥ ½A1 in 69 %). It inseparably bundles orientation + position-multipath. **A static per-orientation correction table does not transfer to motion**; fusion should treat the angle/position-dependent systematic as online-estimated (or position-indexed), not a fixed LUT.

**D-B — R-matrix split (the table fusion copies):**

| elevation | systematic RMS | stochastic RMS (→ R) | systematic frac |
|---|---|---|---|
| shallow ≤ 25° | 91 mm | 82 mm | 0.55 |
| unverified 25–37° | 68 mm | 67 mm | 0.51 |
| steep ≥ 37° | 43 mm | 52 mm | 0.41 |

Stochastic (measurement-noise) RMS **≈ 50–82 mm**, with the *fraction* rising on steep links. The ~45–55 % systematic remainder is angle-repeatable (predictable only if modeled by orientation/position). Split validated genuine (global timing removes 0.1 %).

**D-C — Time-skew: NEGLIGIBLE; per-rank Δt term NOT needed.** Magnitude p99 ≈ 6 mm, median ≈ 1 mm (raw max 18.6 mm is a Vicon-derivative spike). Confirmed kinematic (radius-scales 1.25 ↔ 1.24). ≪ the 50–82 mm stochastic floor. **NO.**

**D-D — Dynamic Layer-2: YES (present, different mechanism).** 14 high-confidence stable phase-sector locks (excess 300–636 mm, sustained 22–29 revolutions; top: R05/A +636, R10/A +529, R06/A +471, R09/F +449, R07/A +420). Unlike static Layer-2 they are **position/multipath-triggered** — predominantly **shallow** links (53/60 ≤ 25°), clustered on **anchor A** — not the elevation-null mechanism. Fusion needs per-link first-path-quality gating / robust rejection to survive them; a static orientation model will not predict them.

**D-E — Alignment-sensitivity disclosure.** Under ±1-frame re-alignment, **no reported number moved > 10 %** (max 1.3 %, skew). The absolute per-link biases in Task 1 are the only ALIGNMENT-DEPENDENT quantities and are excluded from the findings per C1.

---

## Reproduce

```bash
cd experiments/rotoarm_dynamic_residuals
python3 analyze_roto.py     # -> results.json + 5 PNGs (~4 min: 36 s parallel extract on 12 workers, then Tasks 1-5 + C1 sweep)
# roto_lib.py  : Vicon/UWB parsers, tag↔marker resolver, non-uniform windowed time-warp
# extract.py   : per-capture annotated residual table (process-pool worker)
```

Read-only on all capture data. Figures: `fig_residual_vs_phase_{BS2DCE,BSDC91}.png` (angle-periodic template per anchor × tilt), `fig_variance_split.png` (R-matrix), `fig_timeskew.png` (Q-C magnitude), `fig_radius_recovery.png` (Task 5).
