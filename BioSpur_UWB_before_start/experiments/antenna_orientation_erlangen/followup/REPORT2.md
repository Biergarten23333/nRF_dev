# Erlangen Antenna-Orientation FOLLOW-UP — Elevation Hypothesis & Corrected Attribution

**Session:** `erlangen_20260528_optitrack` (Vicon, 2026-05-28) · tag BSF66F · 8 anchors A–H · IDs 13–24 (mid/low/high × 4 yaw orientations).  
**Generated:** `followup/followup.py` → `results_followup.json` (runtime 4.5 s, single core; peak RSS 192 MB). Reuses the vetted geometry, BIAS metric and anchor-map from `../results.json`; read-only on all capture data.

This follow-up re-opens the first report's aggregate verdict ("MAJOR, RMS = 128 mm, mixed mechanism"). A review argued that single number fuses two physically different effects. It does.

## TL;DR

- **The 128 mm aggregate is two layers, and elevation separates them cleanly.** Spearman(|Δ|, link elevation) = **0.54** (p = 8.5e-07, n = 72). Split at 30°: shallow links (48 pts) RMS **46 mm**, max 95 mm; steep links (24 pts) RMS **213 mm**, max 498 mm — a ~4.7× jump at the threshold.
- **Elevation, not range, is the driver.** |Δ| also rises with 3-D link length (ρ = 0.48), but elevation and length are heavily confounded here (ρ = 0.86). Rank-partialling separates them: partial(elev | dist) = **0.28** survives, partial(dist | elev) = **0.04** collapses to zero.
- **Layer 1 (smooth antenna bias):** every shallow link, all heights → RMS ≈ **46 mm** (mid-only 51 mm). This is the honest "orientation moves the range" number.
- **Layer 2 (discrete first-path locks):** 12 cells exceed |Δ| = 150 mm and **every one sits on a steep (37–42°) cross-layer link** with low per-sweep scatter — a stable lock onto a reflection, not jitter. Datasheet-consistent with the DWM1001C elevation-plane nulls.
- **Caliper attribution, revised.** Under **home geometry the wand caliper test has 0 / 24 steep links** (elevation 6–29°, median 15°), so Layer 2 does not fire there. The transferable number is the shallow-link 180° split: orientation explains **~10–18%** of the 324 mm CCF4–955A failure — **down from the first report's 36–59%.**

## Task 1 — Elevation hypothesis

For each (ID × anchor) link the elevation angle is `atan2(|z_anchor − z_tag|, horizontal_dist)` and the length is the 3-D range, both from Vicon. The 72 geometry-corrected **BIAS** orientation deltas (8 anchors × 3 non-reference orientations × 3 heights, each relative to the same-height ABEF reference) are tagged with the elevation/length of their own orientation's link.

The geometry makes the test sharp: mid tag → all links 15–28°; low tag → A–D at ~1°, E–H at ~40°; high tag → A–D at ~38°, E–H at ~2°. "Steep" and "cross-layer" therefore coincide.

### 1.1 / 1.3 Correlations

| relation | Spearman ρ | p | note |
|---|---|---|---|
| \|Δ\| vs **elevation** | **0.543** | 8.52e-07 | primary |
| \|Δ\| vs 3-D length | 0.484 | 1.65e-05 | confounded with elevation |
| elevation vs 3-D length | 0.857 | 6.97e-22 | the confound |
| partial \|Δ\| vs elevation \| length | **0.283** | — | elevation survives control |
| partial \|Δ\| vs length \| elevation | 0.043 | — | length collapses |

Elevation and length are 0.86-correlated (cross-layer links are both steeper and longer), so the raw ρ's are close. Partialling on ranks is decisive: hold length fixed and elevation still predicts |Δ| (ρ = 0.28); hold elevation fixed and length carries essentially nothing (ρ = 0.04). **Elevation is the driver; range/SNR alone is not.** See `elevation_correlation.png`.

### 1.2 Split at 30° and by layer

| bin | n | median\|Δ\| | RMS(Δ) | max\|Δ\| |
|---|---|---|---|---|
| shallow (<30°) | 48 | 39 | **46** | 95 |
| steep (≥30°) | 24 | 150 | **213** | 498 |
| same-layer | 36 | 37 | **42** | 76 |
| cross-layer | 36 | 76 | **177** | 498 |

The two binnings largely agree; they differ only for the mid-tag→low-ring links, which are cross-layer yet only moderately inclined (~27°) and thus sit in the shallow bin with small Δ — which is why the cross-layer RMS (177) is a touch below the pure steep-bin RMS (213). Shallow links carry a tight ≤95 mm effect; steep links carry a heavy-tailed 45–498 mm effect. The 150 mm median of the steep bin means *half* of steep links are already in Layer-2 territory. The elevation split is the cleaner of the two.

### 1.4 Absolute ABEF baseline bias vs elevation

The per-anchor absolute ABEF bias (24 cells, range 44–415 mm) correlates with elevation only weakly and **not significantly**: Spearman ρ = 0.286 (p = 0.18). Interpretation: steep links are *susceptible* to a large bias but do not *deterministically* carry one — whether the LDE grabs a reflection at the reference orientation is stochastic (e.g. steep ABEF cells span 44 mm at H/low up to 415 mm at D/high). So elevation gates the risk of a Layer-2 lock rather than setting a smooth elevation-dependent antenna delay. This tempers the standing "elevation-dependent antenna-delay / z-error budget" hypothesis: the z-error is real, but on this data it is driven by discrete locks on a minority of steep links, not a monotone delay-vs-elevation law.

## Task 2 — Harmonic decomposition (exact, 4 samples @ 90°)

With 4 orientations at 90° the decomposition `v(θ)=c0+a1cosθ+b1sinθ+c2cos2θ` is exact; c2 is the Nyquist term and equals a pure-cosine model's misfit. Variance explained by the first harmonic = A1²/(A1²+2·c2²). Computed per anchor × height on the **absolute BIAS** (24 fits).

| anchor | height | A1 (mm) | φ1 (°) | \|c2\| (mm) | var-expl | cosine ok? |
|---|---|---|---|---|---|---|
| A | mid | 25 | 351 | 44 | 0.14 | **poor** |
| A | low | 55 | 239 | 19 | 0.80 | ok |
| A | high | 100 | 87 | 117 | 0.27 | **poor** |
| B | mid | 18 | 249 | 32 | 0.13 | **poor** |
| B | low | 33 | 341 | 36 | 0.29 | **poor** |
| B | high | 90 | 182 | 92 | 0.32 | **poor** |
| C | mid | 30 | 252 | 28 | 0.36 | **poor** |
| C | low | 34 | 53 | 33 | 0.35 | **poor** |
| C | high | 225 | 264 | 126 | 0.62 | **poor** |
| D | mid | 23 | 287 | 22 | 0.37 | **poor** |
| D | low | 33 | 136 | 4 | 0.96 | ok |
| D | high | 103 | 359 | 98 | 0.36 | **poor** |
| E | mid | 28 | 357 | 14 | 0.66 | **poor** |
| E | low | 108 | 178 | 142 | 0.23 | **poor** |
| E | high | 10 | 213 | 16 | 0.15 | **poor** |
| F | mid | 24 | 211 | 21 | 0.39 | **poor** |
| F | low | 127 | 265 | 85 | 0.53 | **poor** |
| F | high | 8 | 116 | 9 | 0.29 | **poor** |
| G | mid | 12 | 292 | 27 | 0.09 | **poor** |
| G | low | 184 | 173 | 136 | 0.48 | **poor** |
| G | high | 16 | 195 | 13 | 0.43 | **poor** |
| H | mid | 8 | 26 | 18 | 0.10 | **poor** |
| H | low | 98 | 270 | 88 | 0.38 | **poor** |
| H | high | 7 | 255 | 8 | 0.27 | **poor** |

**2.1** — 22 / 24 cells (**92%**) have |c2| ≥ 0.5·A1. A single cosine is a poor model *almost everywhere, including mid height* (mid var-explained 0.09–0.66). Caveat: with only 4 samples c2 is a single alias-prone coefficient — read it as "large non-first-harmonic structure," not necessarily a clean 2nd lobe. Either way the orientation response is not sinusoidal. See `harmonic_heatmap.png`.

**2.2 — effect size, mid + high only** (low is confounded: the tripod moved 147–222 mm between orientations):

| set | n | RMS(Δ) | median\|Δ\| | max\|Δ\| |
|---|---|---|---|---|
| all heights | 72 | 128.4 | 51.1 | 498.4 |
| **mid + high** | 48 | **122.5** | 46.5 | 498.4 |
| mid only (clean Layer 1) | 24 | 51.5 | 48.5 | 94.6 |

The prompt-requested mid+high number is **122.5 mm** — but note it barely drops below the all-heights 128 mm because *high* height is where the A–D cross-layer Layer-2 locks live (C@high = +498 mm). mid+high is still Layer-2-inflated. The clean smooth-bias figure is **mid-only ≈ 51.5 mm** (all shallow links). Report the two layers separately, not one blended RMS.

**2.3 — φ1 vs anchor azimuth, per height** (circular concentration R of φ1−azimuth; R→1 means phase tracks azimuth):

| height | resultant R | reading |
|---|---|---|
| mid | 0.59 | moderate |
| low | 0.48 | weak |
| high | 0.52 | moderate |

Even at mid height, where Layer 1 dominates and the cosine speculation was strongest, φ1 tracks azimuth only moderately (R = 0.59) and the first harmonic explains <40% of the orientation variance for most anchors. So the first report's "phase does not cleanly track anchor azimuth" **holds with honest per-height fits** — it was not merely a Layer-2 averaging artifact. The swing is dominated by the tag's own asymmetry / phase-centre motion, not a geometry-indexed far-field pattern.

## Task 3 — Direct 180° pairs (no cosine model)

CCF4 is mounted 180° opposed to 955A, so the caliper-relevant quantity is the measured range change between 180°-opposed orientations — available directly. Pair 1 = ABEF↔CDHG, Pair 2 = BCGF↔ADHE. Values are the BIAS-metric difference within each pair (mm); elevation is the ref-link elevation.

| anchor | elev mid/low/high (°) | mid p1 | mid p2 | low p1 | low p2 | high p1 | high p2 | max\|180°\| |
|---|---|---|---|---|---|---|---|---|
| A | 28/1/39 | -50 | 8 | 56 | 94 | -11 | -199 | 199 |
| B | 28/2/38 | 13 | 33 | -62 | 21 | 181 | 6 | 181 |
| C | 28/0/38 | 18 | 57 | -40 | -54 | 46 | 448 | 448 |
| D | 27/1/38 | -14 | 45 | 47 | -46 | -206 | 5 | 206 |
| E | 17/42/2 | -55 | 3 | 217 | -7 | 16 | 11 | 217 |
| F | 17/39/2 | 42 | 25 | 24 | 254 | 7 | -15 | 254 |
| G | 18/39/4 | -9 | 23 | 365 | -46 | 32 | 8 | 365 |
| H | 15/40/1 | -15 | -8 | -0 | 196 | 4 | 14 | 196 |

**3.1 — caliper from mid-height 180° splits** (moderate elevation, closest to the wand rig):

- typical per-anchor CCF4-vs-955A split: median 21 mm, RMS 32 mm; worst anchor 57 mm.
- fraction of the 324 mm caliper failure: **10% (typical)** to **18% (worst anchor)**.

**3.2 — all heights, flagged by elevation:** pooling every height inflates the split to RMS 116 mm / worst 448 mm — but that number is carried by steep-link (≥30°) pairs (RMS 194 mm, worst 448 mm), which do not occur in the wand test. It is the wrong number to transfer.

**3.3 — revised attribution.** Under wand-like (shallow-link) geometry, antenna orientation injects a **~10–18% (≈32–57 mm)** CCF4-vs-955A split — a real but minor contributor to the 324 mm failure. This **replaces** the first report's 36–59% (which used a height-averaged cosine amplitude contaminated by steep-link Layer-2 locks); do not average the two.

**3.4 — home geometry check.** Using the wand-solve anchor layout (`logs/system_calibration_20260710_233443/anchor_layout.json`) and the three caliper tag positions (`logs/overnight_radar_20260711/wand_recapture/wand_positions_updated.json`), all 24 anchor→wand links are shallow:

| tag | steep links (≥30°) | link elevations (°) |
|---|---|---|
| BS9336 | 0 / 8 | 16.6, 28.9, 16.8, 10.3, 14.4, 18.6, 10.0, 14.3 |
| BS955A | 0 / 8 | 11.0, 13.4, 11.7, 6.1, 22.9, 22.0, 18.3, 25.6 |
| BSCCF4 | 0 / 8 | 21.2, 27.6, 21.6, 15.3, 10.9, 10.0, 7.2, 13.5 |

**0 / 24 home links are steep** (range 6–29°, median 15°). The home rig is a flat, wide layout; the wand sits mid-height between the low and high anchor rings, so no link reaches the 30° null regime. **The Erlangen mid/shallow number, not the cross-layer number, is the one to transfer to the caliper.** Layer 2 is a deployment risk for *tall/steep* geometries, not the current home caliper.

## Task 4 — Layer-2 cells (|BIAS Δ| > 150 mm)

All 12 cells over threshold, most-extreme first. Every one is a steep link (37–42°). `km sep` is the 2-means separation in pooled-σ; `gap` the mode spacing; `step` the largest rolling-mean jump. Per-cell time series + histograms are `l2_<anchor>_<height>_<orient>.png`.

| cell | ID | Δbias (mm) | elev (°) | per-sweep σ (mm) | km sep | mode gap (mm) | roll step (mm) | verdict |
|---|---|---|---|---|---|---|---|---|
| C@high/ADHE | 24 | +498 | 38 | 19 | 2.6σ | 31 | 4 | STABLE-WRONG-PATH |
| G@low/CDHG | 19 | +365 | 41 | 88 | 9.3σ | 493 | 15 | STABLE-WRONG-PATH (+sparse excursions) |
| A@high/BCGF | 22 | +328 | 40 | 20 | 2.9σ | 33 | 2 | STABLE-WRONG-PATH |
| F@low/ADHE | 20 | +309 | 39 | 94 | 7.6σ | 430 | 12 | STABLE-WRONG-PATH (+sparse excursions) |
| D@high/BCGF | 22 | -301 | 37 | 24 | 2.7σ | 39 | 3 | REF-CONTAMINATED (ABEF ref is the steep-link lock) |
| D@high/ADHE | 24 | -296 | 37 | 32 | 2.7σ | 51 | 3 | REF-CONTAMINATED (ABEF ref is the steep-link lock) |
| H@low/ADHE | 20 | +274 | 39 | 124 | 4.8σ | 258 | 13 | BIMODAL |
| E@low/CDHG | 19 | +217 | 39 | 23 | 2.8σ | 37 | 2 | STABLE-WRONG-PATH |
| D@high/CDHG | 23 | -206 | 37 | 24 | 2.6σ | 38 | 4 | REF-CONTAMINATED (ABEF ref is the steep-link lock) |
| B@high/CDHG | 23 | +181 | 39 | 20 | 2.5σ | 31 | 3 | STABLE-WRONG-PATH |
| E@low/ADHE | 20 | -178 | 40 | 30 | 2.8σ | 49 | 2 | REF-CONTAMINATED (ABEF ref is the steep-link lock) |
| E@low/BCGF | 18 | -171 | 42 | 32 | 2.9σ | 52 | 3 | REF-CONTAMINATED (ABEF ref is the steep-link lock) |

**4.1 shape / 4.2 stability.** Most positive cells are **unimodal, low-scatter, rock-stable** offsets (σ ≈ 20–35 mm, rolling-mean step < 5 mm) — a fixed wrong-path lock held for the whole 120 s, not intermittent jitter. Two (G@low, F@low) are a stable lock plus a sparse (~3–4 % of sweeps) far-excursion burst. One (H@low/ADHE) is genuinely **bimodal** (75/25 split, 258 mm gap) — sweep-to-sweep path switching.

**4.3 orientation-specificity.** The locks are orientation-specific, not graded: each fires in exactly one orientation of its (anchor, height) quartet while the other three sit near a common baseline.

**4.4 negative deltas are reference contamination — the key correction.** All five negative-Δ cells are steep links where the **ABEF reference is itself the locked orientation**, so subtracting it produces a spurious negative Δ. Absolute bias per orientation makes this explicit:

| (anchor, height) | ABEF | BCGF | CDHG | ADHE | the real anomaly |
|---|---|---|---|---|---|
| D@high | 415 | 114 | 209 | 119 | ABEF = 415 mm |
| E@low | 223 | 51 | 439 | 44 | CDHG = 439 mm |

D@high/ABEF (ID21) carries a **415 mm** absolute bias — the single largest ABEF baseline — so its three "−300 mm" deltas are the *other* orientations reading normally. E@low is similar (ABEF = 223 mm elevated, CDHG = 439 mm the full lock, BCGF/ADHE ≈ 45 mm the true baseline). Lesson: on steep links the same-orientation reference is not clean; the delta-from-ABEF framing must be replaced by absolute bias per orientation.

**4.4 reflector plausibility.** A single specular bounce off a room boundary (image-source excess path, rough Vicon room model) matches several locks within tens of mm. For ref-contaminated cells the target is the ABEF-reference lock's own excess (bias above the true baseline), so the three D@high and two E@low rows each point at the *same* underlying reference lock:

| cell | excess target (mm) | best single bounce | residual (mm) | note |
|---|---|---|---|---|
| C@high/ADHE | 498 | floor | 201 | this-orient lock |
| G@low/CDHG | 365 | floor | 74 | this-orient lock |
| A@high/BCGF | 328 | floor | 20 | this-orient lock |
| F@low/ADHE | 309 | floor | 33 | this-orient lock |
| D@high/BCGF | 296 | floor | 26 | ABEF-ref lock |
| D@high/ADHE | 296 | floor | 26 | ABEF-ref lock |
| H@low/ADHE | 274 | floor | 4 | this-orient lock |
| E@low/CDHG | 217 | floor | 64 | this-orient lock |
| D@high/CDHG | 296 | floor | 26 | ABEF-ref lock |
| B@high/CDHG | 181 | ymin | 29 | this-orient lock |
| E@low/ADHE | 171 | xmin | 18 | ABEF-ref lock |
| E@low/BCGF | 171 | xmin | 18 | ABEF-ref lock |

H@low/ADHE (floor, 3 mm), A@high/BCGF (floor, 20 mm), B@high/CDHG (wall, 29 mm) and F@low/ADHE (floor, 33 mm) are close matches; C@high/ADHE's +498 mm exceeds a clean single floor bounce (residual ~200 mm), suggesting a longer or multi-bounce path. Given the rough room model these are plausibility checks, not proofs — but they are consistent with LDE locking a floor/wall reflection once the direct path drops into an elevation null.

**4.5 verdict tally.** 6× stable wrong-path (incl. sparse-excursion), 1× BIMODAL, 5× REF-CONTAMINATED.

## Decision

**D1 — Elevation hypothesis: SUPPORTED.** Spearman(|Δ|, elevation) = 0.54 (p = 8.5e-07); the 30° split multiplies RMS from 46 mm (shallow) to 213 mm (steep); partial correlations show elevation — not link length/SNR — is the driver (partial ρ 0.28 vs 0.04); and all 12 Layer-2 cells are steep. The mechanism is elevation-plane antenna nulls promoting a first-path reflection lock, exactly as the DWM1001C datasheet warns.

**D2 — Honest effect size.** Two numbers, not one: smooth orientation bias (Layer 1, all shallow links) RMS ≈ **46 mm** (mid-only 51.5 mm); discrete steep-link locks (Layer 2) up to **498 mm** on a minority of links. The prompt's mid+high aggregate is 122.5 mm but still bundles the high-height locks, so it should not be quoted as a single antenna-bias figure.

**D3 — Revised caliper attribution.** Orientation explains **~10–18%** of the 324 mm CCF4–955A failure under wand-like geometry (was 36–59%). Decisive reason: the home caliper test is **0/24 steep links** (median 15°), so Layer 2 does not fire; only the small shallow-link split applies. The bulk of the caliper miss is elsewhere (per-tag position/z error).

**D4 — Per-cell classification.**
- C@high/ADHE (Δ +498 mm, 38°): STABLE-WRONG-PATH
- G@low/CDHG (Δ +365 mm, 41°): STABLE-WRONG-PATH (+sparse excursions)
- A@high/BCGF (Δ +328 mm, 40°): STABLE-WRONG-PATH
- F@low/ADHE (Δ +309 mm, 39°): STABLE-WRONG-PATH (+sparse excursions)
- D@high/BCGF (Δ -301 mm, 37°): REF-CONTAMINATED (ABEF ref is the steep-link lock)
- D@high/ADHE (Δ -296 mm, 37°): REF-CONTAMINATED (ABEF ref is the steep-link lock)
- H@low/ADHE (Δ +274 mm, 39°): BIMODAL
- E@low/CDHG (Δ +217 mm, 39°): STABLE-WRONG-PATH
- D@high/CDHG (Δ -206 mm, 37°): REF-CONTAMINATED (ABEF ref is the steep-link lock)
- B@high/CDHG (Δ +181 mm, 39°): STABLE-WRONG-PATH
- E@low/ADHE (Δ -178 mm, 40°): REF-CONTAMINATED (ABEF ref is the steep-link lock)
- E@low/BCGF (Δ -171 mm, 42°): REF-CONTAMINATED (ABEF ref is the steep-link lock)

**D5 — Mitigation ranking (evidence-weighted).**

1. **Anchor placement rules avoiding steep (>30°) links (d)** — prevents Layer 2 by construction and costs nothing to specify. Home already complies (0 steep links); the rule is to *keep* it that way and to avoid tall/steep tag-vs-ring geometries in new deployments. Highest leverage per effort where steep links are avoidable.
2. **Per-link first-path quality gating / robust rejection (c)** — the only mitigation that catches the *stochastic* Layer-2 locks, which (a) and (b) cannot model. Needs first-path/rxdiag metrics not present in this capture — motivates the planned HOME rxdiag capture. Generically valuable; essential wherever steep links are unavoidable.
3. **Per-orientation bias modeling (b)** — could remove the ~46 mm smooth Layer-1 bias, but a cosine LUT is a poor fit (92% of cells fail the cosine test), so it needs a full 2-D orientation table plus per-unit calibration, and it still cannot predict Layer-2 locks. Diminishing returns.
4. **CCF4 physical flip (a)** — removes only the smooth 2·A split (≈32–57 mm, ~10–18% of the caliper) and nothing of Layer 2 or the per-tag z error. A hardware change for the least benefit — lowest priority.

## Reproduce

```bash
python3 experiments/antenna_orientation_erlangen/followup/followup.py      # -> results_followup.json + PNGs (~5 s, 1 core)
python3 experiments/antenna_orientation_erlangen/followup/make_report2.py  # -> REPORT2.md
```

Read-only on all Erlangen capture data; reuses `../results.json` for the vetted geometry and BIAS metric. Home-geometry check reads `logs/system_calibration_20260710_233443/anchor_layout.json` + `logs/overnight_radar_20260711/wand_recapture/wand_positions_updated.json`.
