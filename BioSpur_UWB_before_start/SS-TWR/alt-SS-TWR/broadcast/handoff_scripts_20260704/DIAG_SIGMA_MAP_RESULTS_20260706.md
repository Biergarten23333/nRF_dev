# Diagnostics → σ mapping / listener proxy-gate — RESULTS (2026-07-06)

**Data:** `roto_sar_overnight_20260705_012548` (14 good chunks) + Stage-0 joint circle fit
(`coherent_stage0_cache.npz`); held-out validation = `overnight_soak_v2_20260704_032348` (static wand).
**Scripts:** `handoff_scripts_20260704/{diag_sigma_map,diag_theta_decomp}.py`.
**Figs:** `figs_20260704/{diag_sigma_map,diag_theta_decomp}.png`. **Stdout:** scratchpad `diag_*.out`.
**Tag identity:** resolved through `scripts/tag_roster.py` (single source of truth), no hardcoded maps.

Question: does a passive listener's channel quality (Δ-P etc., overhearing a tag's poll) **predict the
anchor-side ranging error** to that same tag?

---

## TL;DR — verdict

| # | Item | Verdict |
|---|---|---|
| **(a)** | **Proxy gate** — does listener ΔP predict anchor ranging error? | **NOT ADJUDICATED.** Every channel event in stock data is **θ-locked** and thus collinear with the trajectory/power/layout confound. Per-frame predictive power at fixed geometry is **null under both hypotheses**: the confound-free static soak is a definitive null (ρ≈0.00, N=718 k), and θ-control collapses the one apparent roto signal (anchor B, ρ≤0.47) to ρ≈0.03–0.09. → **Routes to the planned wet-towel occlusion session.** |
| **(b)** | **Expected accuracy gain from σ_i weighting** | **~7 mm (5.4 % RMS) optimistic ceiling → effectively ~0 mm. NO-GO for solver injection now.** The ρ that would drive the weights does not survive θ-control or the held-out static set. |
| **firmware** | **APS011 RX-power range-bias correction applied?** | **NO** — `dwt_getrangebias` defined but **zero call sites** (tag + anchor + responder, all variants); range = raw ToF; `raw_mm==range_mm` 100 %. Real but **minor**: ≈1 % of θ-locked residual, and its scale effect **enlarges** geometry (raw reads short) → it is **not** the confound driver nor the radius-excess source (see §5b). Do **not** fit a power-bias curve from moving-tag data (launders layout error into "calibration"). |

The headline is not "no relationship" and not "yes": it is that **this dataset structurally cannot
separate a channel-quality proxy from geometry.** The decisive experiment is a fixed-geometry
occlusion ladder.

---

## Method

* **Ranging residual, two definitions (same pass):**
  * **(a) `r_circle`** = `range_measured − |p_circle(θ(t)) − anchor|`, Stage-0 smoothed circle model.
    External geometric reference, but carries the ~40–110 mm circle-model **position** noise (mostly
    common-mode across anchors) → attenuates ρ.
  * **(b) `r_postfit`** = per-sweep **8-anchor LS multilateration** post-fit residual (damped
    Gauss-Newton, 44/197 k non-convergent dropped). Absorbs ~3/8 of the true per-link error but has
    **zero tag-position confound**. `r_postfit` collapses anchor A's inflated `r_circle` (405/742 mm)
    to 120/148 mm — confirming A's large circle residual was position confound, not ranging error.
* **Diagnostics** (listener `lpd.csv`, per overheard poll): `ΔP = 10log10(cir_pwr·2¹⁷/Σfpᵢ²)`,
  `FP power = 10log10(Σfpᵢ²/rxpacc²)`, `rxpacc`, `std_noise`. **Spearman throughout** → the DW
  power-formula convention constant is irrelevant (rank-invariant). Both signed-r and |r| kept.
* **Pairing.** Primary = **co-located** proxy pairs only: {BS2DCE, BSDC91} × {LB@B, LE@E, LF@F}
  (LB/LE/LF are bolted onto anchors B/E/F — quasi-monostatic; LB's `near_anchor_id=1=B` confirms it).
  Wand-side listeners (LCCF4/L9336/L955A: channel is tag→room-center) are exploratory only.
* **Alignment.** Nearest recv-sweep to each listener frame; clock sync median |Δt| = 44 ms
  (p90 110 ms) ≪ poll interval → clean.
* **Scale.** 481 957 aligned roto frames; 718 176 soak; light compute (no CIR reassembly).

---

## Results

### 1. Per-link Spearman ρ (co-located primary)

| tag | lis@anc | N | ρ(\|rc\|,ΔP) | ρ(rc,ΔP) | ρ(\|rpf\|,ΔP) | ρ(rpf,ΔP) | ρ(\|rpf\|,FP) |
|---|---|---|---|---|---|---|---|
| BS2DCE | **LB@B** | 38.9 k | 0.375 | **0.370** | 0.318 | **0.356** | −0.320 |
| BS2DCE | LE@E | 37.8 k | 0.077 | 0.052 | 0.088 | 0.109 | −0.110 |
| BS2DCE | LF@F | 37.5 k | −0.245 | 0.187 | 0.080 | 0.209 | −0.082 |
| BSDC91 | **LB@B** | 42.3 k | 0.103 | **0.474** | 0.183 | **0.422** | −0.166 |
| BSDC91 | LE@E | 42.1 k | 0.054 | 0.047 | −0.028 | −0.067 | 0.043 |
| BSDC91 | LF@F | 41.6 k | −0.209 | 0.177 | −0.131 | 0.189 | 0.102 |

* Signal is **anchor-B-specific** (both tags, both residuals; FP agrees in sign; signed-r ≫ |r| →
  a positive-bias effect, consistent with point-3). `rxpacc` ≈ 0 everywhere; `std_noise` weak (−0.15).
* **Remote wand-side listeners: ρ ≈ 0.006** (null) — a listener whose channel is tag→room-center
  carries no information about anchor ranging. Only the tag→anchor-sharing (co-located) listener does.
* **Gate = 2/6** (both anchor B) → fails the ≥3-link bar even before controls.

### 2. θ-control — the anchor-B signal is a shared-rotation-angle artifact

Removing the per-θ-bin median from **both** r and ΔP (24 × 15° bins), then Spearman the residuals:

| link | raw ρ(rc,ΔP) | θ-ctl | raw ρ(rpf,ΔP) | θ-ctl |
|---|---|---|---|---|
| BS2DCE LB@B | 0.370 | **0.088** | 0.356 | **0.026** |
| BSDC91 LB@B | 0.474 | **0.067** | 0.422 | **0.068** |

→ **~80–85 % of the correlation is shared-θ curve alignment**, not per-frame channel info.

### 3. Held-out static soak — definitive null

Static tag ⇒ `r = range − per-anchor median` is pure ranging fluctuation (no trajectory confound).
All **9** co-located links give ρ(|r|,ΔP) ∈ [−0.008, +0.008]; **pooled ρ = −0.064 (N = 718 176)**.
In a static room ΔP predicts nothing — insufficient NLOS variation. (This null is equally consistent
with "no NLOS" and "NLOS present but collinear with power" → cannot adjudicate; see §5.)

### 4. Binned conditional σ(|r| | ΔP) — co-located

σ(|r_postfit|) rises monotonically **62 → 111 mm (1.78×)** across ΔP bins. Real, but weak per-sample
(pooled ρ(|rpf|,ΔP)=0.13); the trend is geometry-locked (see §6).

---

## 5. Confound controls (why the verdict is "not adjudicated", not "no")

* **DW1000 Smart TX Power (variable):** every overheard tag poll is `frame_len = 17` (all tags, all
  frames) → constant TX level, **no frame-length power step** → absolute-power metrics (CIR_PWR, FP)
  are on a constant-TX footing. Confound does not apply to this dataset.
* **Circle-model position noise:** handled by the two-residual design; `r_postfit` is confound-free.
* **APS011 (uncorrected) — see §5b for the full firmware / sign / slope / intervention treatment.**
  In brief: not applied, real but minor, and **wrong-signed** to be either the confound driver or the
  radius-excess source.
* **θ collinearity (the crux):** on the roto set the tag's RX power, its range to each anchor, the
  APS011 bias, the trajectory runout, and any real occlusion are **all smooth functions of the arm
  angle θ**. They are mutually collinear, so a raw ρ(residual, ΔP) cannot attribute cause. The static
  soak removes θ (no motion) — and predictive power vanishes. **Per-frame channel proxy value is
  therefore unmeasurable in stock data.**

## 5b. APS011 range-bias — firmware, sign, slope, intervention

* **Applied? NO.** `dwt_getrangebias()` is defined
  ([`drivers/dw1000/src/deca_range_tables.c:636`](drivers/dw1000/src/deca_range_tables.c#L636)) but has
  **zero call sites** across the whole firmware (tag `ss_twr_init`, anchor `ss_twr_anchor_init`,
  responder `ss_twr_resp`, all variants). No other rxpacc/cir_pwr/power-dependent range adjustment
  exists; the only in-path term is the CFO clock-offset ratio (standard). Range is raw ToF
  ([`src/ss_twr_anchor_init.c:334`](src/ss_twr_anchor_init.c#L334),
  [`src/ss_twr_init.c:913`](src/ss_twr_init.c#L913)) → `raw_distance_mm`; host `raw_mm==range_mm`
  **100.0 % of 284 226** rows. (The firmware's `deca_range_tables.c` *is* Decawave's port of the APS011
  curve; ch5/PRF64 uses the **narrow-band** row — only ch4/ch7 are "wide band" in that driver.)
* **Sign convention (pinned).** Firmware idiom is `corrected = measured − getbias`, so
  **`measured − true = getbias(R)`**, which is **negative → raw ranges read SHORT** at every room range
  (−140 mm @0.5 m → −70 mm @2.6 m → 0 @5.3 m), least-negative at long range. Port validated: 255
  sentinel terminates safely (no overflow to 99 m), monotonic non-decreasing in range.
* **Slope = the operative scale quantity.** `bias(R) ≈ a + b·R`: over the anchor-anchor span 1.6–5.3 m,
  **a = −142 mm** (a constant, absorbed by antenna-delay calibration) and **b = +2.77 %** (the
  range-dependent, scale-equivalent contamination that a per-link delay cannot absorb). Per-anchor roto
  spans give local b = **+2.1…+3.8 %**. (The earlier 20–30 mm "swing over the rotation span" answered
  the *θ-residual* question — where APS011 explains ~1 %; it is the *slope* that governs *scale*.)
* **Intervention (decisive) — re-fit Stage-0 circle on APS011-corrected ranges** (`diag_aps011_intervention.py`,
  `corrected = raw − getbias(raw)`):

  | tag | R_raw | R_corr | ΔR | vs kinematics | vs mech |
  |---|---|---|---|---|---|
  | BS2DCE | 449.3 | 469.5 | **+4.51 %** | 435 | ~400 |
  | BSDC91 | 545.8 | 563.6 | **+3.26 %** | 534 | ~510 |

  Correction **enlarges** the radius by ~b in magnitude — because raw ranges read short, the raw
  geometry is a *shrunk* version and correcting expands it. **This is the opposite direction needed to
  explain any radius "excess" over the ~400 mm mechanical guess: uncorrected APS011 is not the cause;
  correcting it moves the radius *away* from 400/435.** Anchor-anchor scale (5 600 pairs; **no
  per-measurement RX power logged in autopos → range-table/Friis-implicit only**): APS011 correction
  = **+0.58 %** on baselines (raw already reads +1.12 % long vs the noref-solved layout, which floats
  scale). Wand static-tag pairwise distances shift +4…+24 % but are an **unreliable** probe (small
  700–800 mm separations, noisy static-tag multilateration) and lack tape truth — reported as change
  only. **Net: APS011 is a real, deterministic, sub-%-to-few-% *scale* effect, not the dominant
  geometry error and not an inflation source.**
* **Do not fit a power-bias curve from this data** (instructed). The honest range-walk calibration needs
  **fixed geometry with stepped TX power** and per-frame **measured** RX power (smart-power-immune),
  which the ranging links do not log (`anchor_diag` 0 % for moving tags).

## 6. θ-locked residual decomposition per (tag, anchor) — *demonstration, not assertion*

The θ-locked signed residual (median **86 mm** RMS, up to 134 mm) is **too large for APS011 alone**.
Decomposed into **known, un-fitted** components (`diag_theta_decomp.py`; this is the outstanding
multipath-decomposition item-2 — merged, not duplicated):

| component | source | share of θ-locked variance | amplitude |
|---|---|---|---|
| **Trajectory RUNOUT** | Stage-0 measured radial+axial harmonics, projected onto each anchor LOS | median **11 %** (up to **71 %** at near anchor C; only **5–11 %** at B) | 21–58 mm |
| **APS011 swing** | shipped ch5/PRF64 table on measured range(θ), fixed coeff | median **~1 %** | 6–12 mm |
| **Mast/motor self-occlusion** | geometric (LOS vs rotation axis) | **0 %** | — |
| **Systematic, unattributed (layout-error projection = leading hypothesis)** | — | **dominant** | median **69 mm** |

* **All visible θ structure is systematic, not noise.** The θ-binned mean has a random floor of
  ≈ per-sweep RMS / √(N per bin) ≈ 130–200 mm / √1370 ≈ **4–6 mm** (up to ~16 mm for the noisiest
  anchors A/B). The θ curves swing 60–400 mm ≫ this floor → every visible feature is systematic.
* The dominant term is labelled **systematic-unattributed**: it is *not* trajectory runout (that is the
  green curve, largely orthogonal at B), *not* APS011 (flat), *not* occlusion (nil). Its **leading
  hypothesis is layout-error projection** — a wrong logged anchor position produces a reproducible
  range-residual-vs-θ signature as the tag orbits; a near-field reflector is the alternative. Stock
  moving-tag data cannot separate the two (see §8).
* **Occlusion is geometrically impossible:** the min LOS-to-rotation-axis distance is **452 mm = the
  orbit radius R** for every anchor (tags orbit outside the mast; anchors are further out, so the LOS
  never re-approaches the axis). No physical mast (~20–80 mm) can occlude.
* **Anchor B** — where the "proxy" lived — has 121–134 mm θ-locked residual with runout explaining only
  5–11 % and APS011 ~5 %: its structure is **anchor-B-specific** (layout/position error of B, or a
  near-field reflector at B), **not** trajectory and **not** channel. That is exactly what a co-located
  listener's ΔP co-varies with over θ, producing the spurious ρ=0.37/0.47.
* **Do NOT fit a custom RX-power→bias curve from this data** (instructed): the θ-locked residual is a
  mixture dominated by layout/geometry; a fit would launder layout error into "calibration". A clean
  range-walk calibration needs **fixed geometry with stepped power**.

---

## 7. Gate + expected weighting gain

* Gate (|ρ|>0.30 on ≥3 co-located links): **FAIL** (2/6, both anchor B, and those collapse under
  θ-control). Static-soak passing = **0/9**.
* If one nonetheless weighted each measurement by σ(ΔP): inverse-variance-vs-uniform variance factor
  = 1.117 → **5.4 % RMS reduction ≈ 7 mm** on the ~129 mm per-link |r_postfit| RMS. This is the
  **optimistic ceiling** and it evaporates once the confounds are removed. **NO-GO** for solver
  injection from stock listener ΔP.

---

## 8. Next step — the experiment stock data cannot substitute for

**Wet-towel occlusion ladder at FIXED geometry** (static tag, no rotation → kills the θ confound):
occlusion depth ladder **none → dry towel → wet towel → human** across a co-located
listener↔anchor pair. This makes the confound structurally impossible (geometry fixed, only the
channel changes), so any ρ(ΔP, ranging error) is a **clean** per-frame proxy measurement. Pair it with
stepped TX power at fixed geometry to obtain the honest APS011/range-walk curve for this hardware.

**Actionable side-findings (independent of the gate):**
1. APS011 range-bias is uncorrected (§5b) but is a **minor, few-% scale** effect whose sign **enlarges**
   geometry — enabling it moves the roto radius +3–4 % and anchor baselines +0.6 %, *away* from the low
   mechanical references. It is **not** the dominant geometry error. If enabled, calibrate the range-walk
   on **fixed geometry with stepped power** (and measured RX power), never from this moving-tag data.
2. Anchor **B** (and **A**) carry a large θ-locked / postfit residual **not** explained by trajectory,
   APS011, or occlusion (systematic-unattributed, median 69 mm; §6) — **re-survey their layout positions
   or check for a near-field reflector.** This is the single largest positioning-accuracy lever here and
   is independent of the listener question. A fixed-geometry session separates layout (static, repeatable)
   from channel (occlusion-dependent).
3. The radius "excess" (fitted 449/546 vs mechanical ~400/510) is **not** an APS011 artifact (correcting
   enlarges it) and is partly a soft-reference problem (kinematics 435/534 and the noref scale-freedom
   bracket it). Pin the scale with an independent known baseline before trusting absolute geometry.

> **[Side-findings #1 and #3 above are SUPERSEDED by §5b-REVISED — they relied on the full-table
> correction that re-injects the degenerate +142 mm constant. Slope-only reverses the sign: APS011
> slope IS a partial contributor to the excess and moves radii TOWARD mechanical.]**

---

## 5b-REVISED — APS011 SLOPE-ONLY (2026-07-06, supersedes the §5b intervention conclusion)

**Flaw in the original §5b.** `diag_aps011_intervention.py` applied `corrected = raw − getbias(raw)`,
i.e. the **full** table, which re-injects the constant **a = −142 mm**. That constant is (i) already
absorbed by antenna-delay calibration, (ii) degenerate with the delay parameters, (iii) EVB1000-design
specific (Sidorenko 2019 — only the *slope* transfers). Added to every range it drives an **off-centroid
expansion** (orbit center ~1.1 m from the anchor centroid) that *enlarged* the radius. Fingerprint: the
full-table radii moved by near-equal **absolute** amounts (+~20 mm) where pure slope removal predicts a
**multiplicative** −2.8 %. "Correction enlarges radius ⇒ APS011 not the excess source" does **not** follow.

**Slope-only correction** (per link, `diag_aps011_slope_only.py`): `corrected = raw −
(getbias(raw) − getbias(R̄_link))`, R̄_link = link mean operating range → zero constant, pure scale about
R̄. getbias verified vs firmware (−140@0.5m, −70@2.6m, 0@5.3m); slope b = **+2.92 %** over 0.5–5.3 m.

**1) Trajectory re-fit, BOTH methods — radii DROP toward mechanical (prediction confirmed):**

| method | tag | R_raw | R_slope-only (ΔR%) | pred R·(1−b) | R_full (old 5b) | residual excess vs mech |
|---|---|---|---|---|---|---|
| Stage-0 joint (per-sweep) | BS2DCE | 451.8 | **440.0 (−2.62 %)** | 438.6 | 475.4 ↑ | **+40.0 mm** |
| Stage-0 joint | BSDC91 | 543.0 | **528.8 (−2.63 %)** | 527.2 | 559.4 ↑ | **+18.8 mm** |
| kinematics (0.5 s bin) | BS2DCE | 434.5 | **423.7 (−2.49 %)** | 421.9 | 454.6 ↑ | **+23.7 mm** |
| kinematics | BSDC91 | 534.8 | **521.2 (−2.55 %)** | 519.2 | 547.6 ↑ | **+11.2 mm** |

→ Slope-only shrinks R by ~2.5–2.6 % onto the (1−b) prediction, **toward** mechanical, on both methods
and both tags. The full-table variant grows R (wrong sign) — that growth was the +142 mm-constant
off-centroid artifact. **Reversed verdict: the APS011 SLOPE is a real scale contamination and a genuine
(partial) contributor to the radius excess (~11–14 mm), not a non-cause.**

**Residual excess budget** (after slope removal): **+11 to +40 mm** still above mechanical (kinematics
+24/+11; Stage-0 +40/+19). APS011 slope explains ~11–14 mm of the ~35–52 mm raw excess; the remaining
**~11–40 mm is NOT APS011** — it is the systematic-unattributed / layout-error term of §6 (leading
hypothesis), consistent with the anchor-B-specific θ-locked residual.

**2) Anchor-anchor — actual bounded-delay solver (`prepare_autopos_v3_box.py`), not a scale ratio.**
Control run reproduces the shipped layout exactly (delays to 4 dp, rms 186.0). Slope-only-corrected
inter-anchor ranges (ref = global mean baseline 3955 mm) re-solved:

| | control (raw) | slope-only | full (old 5b) |
|---|---|---|---|
| solved mean inter-anchor dist | 3895.4 | **3889.7 (−0.15 %)** | 3930.7 (+0.91 %) |
| edge RMS (mm) | 186.0 | 182.7 | 180.6 |

The solver **absorbs most of the slope into per-anchor DELAYS**, not scale: Δτ = D **+0.018 ns**
(~5 mm), F −0.017, G −0.011, others ±0.003–0.006 ns; solved scale moves only −0.15 % (~6 mm). This is
the **ρ = −0.977 scale↔delay trade**: "scale alone is half the answer" — scale (~6 mm) and delay
redistribution (~5 mm) split the slope. The old §5b naive scale ratio (+0.58 %) both used the wrong
(full) correction and ignored the delay leg. Full-table's +0.91 % is the spurious +40 mm constant.

**3) Erlangen decomposition — SPEC (do NOT execute yet; Paper B critical path).**
The Vicon-campaign layout carries a **+4.36 % scale**. To split it into APS011-slope vs delay-leak:
(a) extract the Erlangen **inter-anchor span distribution** from that campaign's `pairs_all.csv`
(spans differ from this room's 1.1–5.5 m → different local b); (b) compute **b_erlangen** = getbias
slope over *those* spans (narrow-band ch5/PRF64 table, same port); (c) build slope-only-corrected
Erlangen inter-anchor ranges (ref = that campaign's global mean baseline); (d) re-solve the
Vicon-campaign layout with the **same** bounded-delay solver + priors; (e) report the decomposition
of +4.36 % into `Δscale_APS011-slope` vs `Δ(per-anchor delays)` (the leak), against Vicon truth.
Falsifiable: APS011-slope should account for only ~0.1–0.3 % of scale (per the −0.15 % here), so the
bulk of +4.36 % is delay-leak / layout — NOT APS011.

**4) Kept:** §6 finding #2 — **re-survey anchors A/B layout positions (or check for a near-field
reflector at B)** remains the **top standalone positioning lever**, independent of all the above.
**5) Downgraded:** the living-room radius cross-check → **internal diagnostics only** (layout reference
contaminated at ~69 mm systematic; mechanical radii never calipered). No further compute on it.

Scripts: `diag_aps011_slope_only.py` (trajectory); solver control/slope/full re-runs via
`prepare_autopos_v3_box.py`. Stdout: `/tmp/.../scratchpad/aps011_slope.out`, `aps011_solver/`.


---

## 5c — ANCHOR-ANCHOR RESPONSE ACCOUNTING (2026-07-06, resolves the §5b-REVISED-item-2 rejection)

**"§5b-REVISED item 2 REJECTED pending response accounting"** — the slope perturbation injects ±35–70 mm
across the 28 pairs but only ~10–15 mm was visible (scale −0.15 % + delays ±5 mm + edge-RMS −3.3 mm).
Where did the rest go? Drove the **real** V3-box solver (`solve_anchor_layout_v3_full.py`, unmodified,
shipped args) on control vs slope-only in isolated out-dirs (`diag_aps011_solver_accounting.py`; control
reproduces shipped to **0.00 mm / rms 186.0**). Injected δ: **RMS 33.2 mm, range [−40, +60], ref 3955 mm.**

**The "muted response" is a METRIC ARTIFACT, not a muted solver.** The books balance once you stop
looking only at scale:

**Item 1 — position deltas, isotropic vs shape (warm-started = clean same-basin local response):**
| | RMS \|Δpos\| | isotropic (scale) | SHAPE | shape/total |
|---|---|---|---|---|
| warm (slope seeded from control) | **38.6 mm** | 2.9 mm (**−0.075 %**) | **38.5 mm** | **1.00** |
| SDP-seeded (partly basin-hopped) | 53.9 mm | 4.2 mm (+0.107 %) | 53.8 mm | 1.00 |

The response is **~100 % SHAPE** — upper anchors E/F/G/H lift ~**+46 mm in z** coherently; B/C shift in x.
The scalar *mean inter-anchor distance* sees only the isotropic ~0.1 % and is **blind to the 38 mm shape
deformation.** Why scale barely moves: the slope-only δ = −b·(d−d̄) is an **affine compression about the
mean**, whose **pure-scale content is only −0.35 %** (not the +2.9 % slope) — it is a shape mode *by
construction*, and the height/level/plane priors pin what little scale remains. A small scale response is
therefore **expected and correct**, not evidence of muting.

**Item 2 — delays & bounds (CORRECTS §5b-item-2's "absorbed into delays"):** warm delay changes are
**≤2.5 mm** (G −2.55, F −1.65, rest sub-mm); solved \|bias\| 0–26 mm ≪ the **soft 200 mm** Tikhonov prior.
**There is NO ±60 mm hard bound in this solver** (only Tukey w_min = 0.05) → item-2's saturation premise
does not apply to this config; the June "delays 95–294 mm ≫ ±60 mm" was a different context. Delays are
**not** where the perturbation went (they absorb ~2.5 mm, not the ~5 mm §5b claimed, and ≪ the 38 mm
shape). Tukey modestly down-weights the perturbed long pairs (A-G 0.775→0.677).

**Item 3 — cold-start / init-sensitivity:** 3 jittered seeds (±250 mm) converge **21–24 mm** from the
SDP-auto control (worse basins, rms 191–192 vs 186). "Control reproduces shipped to 4 dp" is because both
use the **SDP/MDS seed** (confirmed `method=sdp`, *not* warm-start-from-shipped) → same basin; trivial
warm-start **ruled out**. But the ~24 mm basin uncertainty is comparable to the response, so the
SDP-seeded slope solve **partly basin-hopped** (54 mm, scale +0.11 %); the **warm-started** run (slope
seeded from control) is the clean local response (38.6 mm, scale **−0.075 %**) and now **reconciles with
§5b-REVISED's −0.15 % shrink direction** (the SDP +0.11 % grow was basin contamination).

**Item 4 — residual balance & distance-correlation:** injected **33.2 mm = absorbed 27.2 mm** (into
shape+delay) **+ 19.9 mm redistributed into per-pair edge residuals** (δ = absorbed + residual exactly).
The residual redistribution is **distance-correlated** (corr −0.60; absorbed −0.77), so aggregate edge-RMS
barely moves (why it *looked* muted) while per-pair residuals shift ±20 mm. **Books balanced.**

**Verdict.** The response is **not** muted: the ±35–70 mm injection is fully accounted as **~38 mm
anisotropic shape deformation + ~20 mm distance-correlated residual redistribution**, both invisible to the
scale / aggregate-edge-RMS metrics. §5b-REVISED item-2's "slope absorbed into delays" is **superseded** —
delays absorb ~2.5 mm; **shape** absorbs the rest.

**Erlangen pre-registration — UPDATE (not just "stands"):**
1. **Scale claim survives:** APS011-slope contributes only ~0.1–0.3 % of *scale* (pure-scale content of the
   perturbation is ~−0.35 %; solver realizes ~−0.075 %) → the bulk of Erlangen's +4.36 % scale is
   delay-leak/layout, **not** APS011. Good for delay attribution.
2. **MUST warm-start** the Erlangen re-solve from its shipped layout: init-sensitivity (~24 mm) ≫ the
   ~0.1–0.3 % scale signal, or basin-hop noise swamps it (as it did the SDP-seeded slope here).
3. **Report the SHAPE channel too:** a scale-only decomposition is **incomplete** — the slope induces a
   shape deformation ≫ its scale effect. Pre-register `Δscale`, `Δ(per-anchor delays)`, **and**
   `Δshape (RMS anisotropic position change)`, all warm-started, against Vicon truth.

Both worlds resolved: the muted *scale* response is **real** (APS011 scale share small), but "muted
response" as a whole was **artifactual** (it is shape, not scale) — no harness redo needed beyond
warm-starting and adding the shape metric.

Scripts: `diag_aps011_solver_accounting.py` (drives the unmodified V3-box solver; edits nothing).
Stdout: `/tmp/.../scratchpad/aps011_acct.out`; isolated solves in `scratchpad/aps011_acct/`.


---

## 5d — ERLANGEN REAL LAYOUT ERROR: ORTHOGONAL-MODE DECOMPOSITION (2026-07-06)

**Executes the §5b-REVISED-item-3 / §5c pre-registration on the _real_ (not injected) Erlangen
error field.** Decomposes the AutoPos-vs-Vicon anchor error into **isotropic scale + anisotropic
(diagonal) scale + shape**, with an exactly-closing energy budget, and cross-checks the shape mode
direction against the independent V3-box APS011-slope injection (§5c).
Script: `diag_erlangen_modes.py` (read-only; new; modifies nothing). Stdout: scratchpad `erlangen_modes.out`.
Compute: 8-point linear algebra, 1 worker (12 cores idle by design — a pool would be pure overhead);
GPU untouched (5090D reserved for dinardPCB).

### Provenance (every load-bearing number below depends on **AutoPos config = v4-io**)

* **AutoPos solve = `v4-io`** — *"current production inter-anchor solver"* (`version_summary.csv`,
  `solver/outputs/v1_to_v4_io_field_check/v4-io/layout.json`). Gauge: A@origin, B on +x, C in xy-plane.
  `physical_priors = soft_two_layer_v1`; per-anchor antenna-delay bound is **SYMMETRIC ±60 mm**
  (§5e verified this directly in solver source, `run_full_evaluation_same_pipeline_20260513.py:463-464`;
  this corrects the `[0,60]` one-sided phrasing used below and in some other reports, which described
  the *observed* all-non-negative delays, not the coded bound) with **C = D = 60.0 mm CLIPPED at the
  +60 mm edge of that bound** (delay L2 = 110 mm) — this is the delay-clipping that the individuelle
  report shows deflects range bias into geometry. Seed = MDS/NLS (`solve_autopos_v1`→`solve_mds_nls`;
  a convex-SDP path exists in the same module but is not wired into any production version).
* **Other configs on record** (for contrast; not the headline): `v1-old` (bidirectional mean, no delay,
  vs-Vicon rigid RMS 176 mm-class), `v2` (IVW pair fusion, no delay), `v3-lite` (MAD/MVUE robust, no
  delay), `v3-full` (robust + per-anchor delay, **unbounded −5.8…96.6 mm**, RMS 102 mm). The **+4.36 %
  headline is a v4-io property**; the decomposition below is v4-io only.
* **Vicon truth** = OptiTrack Erlangen 28-May-2026, `{A..H}antenna` markers, **Y vertical** (X/Z
  horizontal); `erlangen_anchor_truth_all8_v4io.json`, identical to the `truth_*` columns of
  `FULL/tables/layout_abs_errors_all8.csv`.
* **Correspondence** = label-based A↔A … H↔H. anchor_id→label `0→A … 7→H` is **decisive**
  (second/best assignment-cost ratio **1.48 > 1.20** gate; `verify_anchor_mapping`). **No ambiguity — nothing to stop on.**
* **Reflection**: rigid & similarity alignment both use **det(R) = −1**. Expected — the gauge-fixed
  range-only solve picked the mirror handedness; a reflection is a gauge choice, not a shape error.

### 1. Alignment & the "true" isotropic scale — **the +4.36 % headline IS a real isotropic scale**

| Procrustes (AutoPos→Vicon, label-based) | RMS | energy | note |
|---|---:|---:|---|
| rigid (T+R, s=1) — residual = scale+shape | **105.42 mm** | 88 907 mm² | = **total error** |
| similarity (T+R+s_iso) — residual = pure shape+aniso | 67.12 mm | 36 042 mm² | scale removed |

**s_iso = 0.95827 ⇒ AutoPos layout is +4.355 % larger than Vicon** — i.e. the headline **+4.36 % is,
to 3 significant figures, the genuine best-fit isotropic scale** (Sim(3) 43.55 mm/m). The pre-registered
"is +4.36 % mostly _not_ isotropic?" test therefore **fails in the honest direction**: s_iso ≈ 4.36 %,
so the headline is **not** a shape effect mislabeled as scale. It is a real, dominant isotropic expansion.
**Provenance-chain check:** my rigid RMS **105.42 mm reproduces the published `layout_abs_errors_all8`
v4-io err_3d RMS exactly** → the published "production error" is a **rigid (scale-inclusive)** alignment.

### 2. Anisotropic (diagonal) scale — **vertical is inflated ~2.3× the horizontal**

Diagonal scale tensor fit on the rigid-aligned residual, in Vicon axes (X, Y-vertical, Z):

| axis | scale s | AutoPos expansion |
|---|---:|---:|
| X (horiz) | 0.9704 | **+3.06 %** |
| **Y (vertical / layer-gap)** | **0.9248** | **+8.13 %** |
| Z (horiz) | 0.9601 | +4.15 % |

Horizontal geo-mean s_h = 0.9652 (+3.60 %); **vertical/horizontal anisotropy s_Y/s_h = 0.958 (−4.2 %)** →
the **vertical (layer-separation) dimension is over-expanded +8.1 %, roughly 2.3× the ~+3.6 % horizontal
inflation.** Physically consistent with range-only vertical under-observability + the clipped C/D delays +
soft two-layer height priors absorbing delay-leak into vertical scale.

### 3. Shape-PCA (SVD of the 8×3 post-isotropic displacement field) — **the "upper-layer lift" is mostly anisotropic SCALE, not shape**

Layer-normal n = [0.00, +1.00, 0.00] ≈ +Y. Modes of the similarity (post-iso) residual (E1 = 36 042 mm²):

| mode | % of shape E1 | % of total E0 | direction (X, Yv, Z) | cos vs upper-lift template | read |
|---|---:|---:|---|---:|---|
| 1 | 42.9 % | 17.4 % | [+0.90, +0.06, −0.42] | **+0.015** | **horizontal in-plane** (C↔F opposition, +H/−B) |
| 2 | 36.5 % | 14.8 % | [+0.39, +0.29, +0.88] | +0.128 | horizontal in-plane (mostly Z) |
| 3 | 20.6 % | 8.3 % | [−0.18, **+0.96**, −0.24] | **+0.624** | vertical lift ≈ the anisotropic Y over-scale |

* **The eyeball "E/F/G/H upper-layer coherent z-lift" is _not_ the first shape mode.** The two principal
  post-iso modes are **horizontal in-plane** distortions (cos ≤ 0.13 with the upper-lift template); the
  vertical-lift appears only as **mode 3** and is **largely the anisotropic vertical over-scale** (§2), not
  a genuine non-affine shape. Confirmation: the full post-iso shape has cos = +0.37 with the lift template,
  but **once the diagonal scale is removed, the post-anisotropic shape's principal mode has cos = −0.01
  with lift** — the lift vanishes with the scale. So the visible lift is ~⅔ scale, ~⅓ residual.
* **Largest shape residual sits on anchor A** (post-aniso mode-1 loading A = −0.82; A also carries the
  largest rigid residual, 185 mm), with C/F next — **cross-consistent with the standing "re-survey A/B"
  lever** (§5b-REVISED item 4 / §6 #2).
* **Sample-size caveat (8 points → exactly 3 spatial modes):** mode directions 1–2 are stable; **the
  smallest post-anisotropic mode (3.1 % of total, 18.7 mm) is at the 8-anchor sampling-noise floor — treat
  as possible noise.**

### 4. Energy account — closes to 100.0 % (no forcing; closure residual = 0.0 mm²)

| bucket | energy mm² | % of total | RMS-equiv |
|---|---:|---:|---:|
| **isotropic scale (s_iso = +4.36 %)** | 52 865 | **59.5 %** | 81.3 mm |
| **anisotropic extra (diag beyond iso)** | 6 652 | **7.5 %** | 28.8 mm |
| shape mode 1 (post-aniso) | 14 546 | 16.4 % | 42.6 mm |
| shape mode 2 (post-aniso) | 12 049 | 13.6 % | 38.8 mm |
| shape mode 3 (post-aniso, *poss. noise*) | 2 795 | 3.1 % | 18.7 mm |
| **TOTAL (rigid residual)** | **88 907** | **100.0 %** | 105.4 mm |

→ **scale (iso + aniso) = 66.9 %; non-affine shape = 33.1 %** of the total error energy. The single
"+4.36 % isotropic scale" headline captures the **dominant** mode (59.5 %) but is **incomplete**: it is
blind to the +8.1 % vertical anisotropy and to a **33 % non-affine shape residual (67 mm-class)** — the
same shape-blindness the V3-box injection warned about (§5c), now quantified on the real field.

### 5. Cross-check vs the V3-box APS011-slope injection (§5c) — **different origin**

Fields expressed in each room's physical local basis (e1 = A→B in-plane, e2 = n×e1, n = lower→upper layer
normal); identical A–H two-layer topology (A-E, B-F, C-G, D-H vertical pairs) makes the 24-D cosine meaningful.

| direction cosine | value |
|---|---:|
| injection (warm) vs upper-lift template | **+0.595** (injection ≈ pure upper-layer +46 mm n-lift, §5c) |
| Erlangen shape **mode-1** vs upper-lift template | +0.015 |
| Erlangen **full** post-iso shape vs upper-lift | +0.370 |
| **injection vs Erlangen shape mode-1** (24-D) | **+0.099** (≈ orthogonal) |
| **injection vs Erlangen full post-iso shape** (24-D) | **+0.310** (weak-moderate) |

**Verdict (report cosines, do not overclaim):** the APS011-slope injection produces an almost-pure
**upper-layer normal-lift** shape; Erlangen's **principal** real shape mode is **horizontal in-plane and
near-orthogonal to it** (cos 0.10). The only overlap (0.31 vs the full shape) is the shared **vertical-lift
tendency — which in Erlangen is mostly anisotropic _scale_, not shape.** Therefore **APS011-slope is not the
source of Erlangen's dominant shape**; the shape has another origin (delay-leak into geometry / genuine
layout error at A/C/F), consistent with the clipped C/D delays. This does **not** support "APS011-slope →
Erlangen shape"; it does not refute a minor shared vertical component.

### Bottom line (honest, both directions)

1. **The +4.36 % headline is a _real_ isotropic scale** (s_iso = +4.355 %, 59.5 % of error energy) — not a
   shape artifact. The "delay-leak → scale" narrative's scalar survives.
2. **But the scale-only headline is incomplete:** +7.5 % of the error is **anisotropic** (vertical inflated
   +8.1 %, ~2.3× horizontal) and **33 % is non-affine shape** (67 mm-class), invisible to a single scale number.
3. **The visible "upper-layer lift" is ~⅔ anisotropic vertical scale, ⅓ shape** — not a standalone shape mode.
4. **Erlangen's real shape ≠ the APS011-slope injection shape** (principal modes near-orthogonal, cos 0.10);
   the dominant real shape is a **horizontal in-plane distortion centered on anchor A** → re-survey A (and B/C)
   remains the top standalone layout lever.

> **[§5d's own "+8.1 % vertical anisotropy" and the "A-shape-is-immutable" reading are BOTH revised by
> §5e below: the anisotropy turns out to be a delay-treatment artifact (it collapses when the common-mode
> delay is freed), and the A-mode is delay-CORRECTNESS-sensitive, not delay-FREEDOM-sensitive.]**

---

## 5e — v4-io DELAY-TREATMENT ABLATION LADDER: adjudicating the 63 mm residual + A-shape attribution (2026-07-06)

**Question:** §5c/§5d left the 63 mm-class post-common-mode residual and the A-centered shape mode
unattributed. This section varies **only** how per-anchor antenna delay is parameterized/bounded/
regularized in the **actual production Erlangen solver** (not a reimplementation), freezing data,
physical priors, Huber loss, and residual σ, to adjudicate: how much of +4.36 % scale, the +8.1 %
vertical anisotropy, and the A-centered shape mode are delay-treatment artifacts vs genuine layout error.

Scripts: `erlangen_decompose_lib.py` (§5d's Procrustes/aniso/shape-PCA/energy-budget code, extracted
verbatim into a reusable function — refactor verified byte-identical to the original §5d numbers before
reuse) + `diag_erlangen_ablation.py` (new; read-only w.r.t. all production solver files; drives the
**actual** `solve_v4` / `solve_v4_common_mode` from `run_full_evaluation_same_pipeline_20260513.py` via
`importlib`, an isolated in-process module instance — never edits the file on disk). **Sanity gate
passed:** a fresh `solve_v4` call on the loaded sweep data reproduces the shipped v4-io layout to
**0.000 mm** — the harness's data pipeline is verified identical to production.

Compute: 8-anchor/28-pair `least_squares` solves are ms-scale (12 cores; loadavg 4.5–5.6; GPU untouched,
5090D reserved for dinardPCB). The arm3/arm4 chain is a genuine warm-start **dependency** chain (each
solve's input is the previous solve's output) — a process pool cannot parallelize a sequential
dependency, so it correctly runs single-threaded; the harness explicitly self-reports this rather than
force-parallelizing for its own sake. The one independent-trials case (arm5 prior-sigma sweep) was
**not** triggered (see below), so no batch pool ran this session; the harness is wired to use
`max(1,cores-2)` workers if it had been.

### Stage A — config provenance (repo archaeology; every arm below is either REUSED verbatim or a NEW
solve through the unmodified production function)

| arm | config | source | reproduction gate |
|---|---|---|---|
| **arm0** production v4-io | d_A=0, bound **±60 mm**, σ_e=20 (Tikhonov on free d_i) | `solver/outputs/v1_to_v4_io_field_check/v4-io/layout.json` | s_iso=0.95827, RMS=105.42 — **matches §5d exactly** |
| **arm1** bound-only relaxed | d_A=0, bound **±200 mm** (and ±150 secondary), σ_e=20 | `Analysis/official_extra_analysis/FULL/audit_phase1/layouts/v4io_bound{150,200}/layout.json` — **REUSED**, archaeology recovered `audit_phase1_revised.py`'s `solve_v4_bound()`, confirmed a literal copy of `solve_v4` with only the bound parameterized (regularizer/Huber/priors identical) | sim3_scale matches archived `item3_relaxed_delay_bound_summary.csv` to 4 dp |
| **arm2** common-mode free | d_i=c+e_i, c free, σ_e=20 | `Analysis/official_extra_analysis/FULL/audit_phase1c/layouts/v4io_common_mode/layout.json` — **REUSED** from `audit_phase1c_common_mode.py` | c=111.985≈112.0, scale 0.9583→**1.00978**, rigid RMSE **62.99**≈63.0, clamp-c=0 cost **+56.47**, 5 perturbed inits agree to <0.03 mm — **all June gate numbers reproduce exactly** |
| **arm3** σ_e ladder {20,60,200,~∞} | c free, σ_e swept | **NEW solves**, `mod.solve_v4_common_mode(..., e_reg_scale_mm=σ)`, warm-started chain from arm2. σ_e=∞ implemented as 1×10⁶ mm (the `(e/σ_e)` term becomes <10⁻⁴, i.e. numerically indistinguishable from unregularized within the ±100 mm e-bounds; the production function raises `ValueError` on a literal non-positive/infinite σ_e, so this is the correct config-level substitute, not a code edit) | σ_e=20 leg reproduces arm2 to full precision (fixed point, as expected) |
| **arm4** oracle delay reinjection | d_i **FIXED** = oracle (Vicon-derived) per-anchor delay; geometry-only solve | oracle values from `audit_phase1c/tables/item1_oracle_per_anchor_delay.csv` — **REUSED** (A=148.2, B=96.3, C=127.4, D=114.7, E=48.9, F=50.0, G=86.1, H=85.2 mm; mean=94.62, all positive — **matches June oracle-table gate exactly**). Solve = new isolated function in `diag_erlangen_ablation.py` (delays fixed, not free; identical /15 mm range-residual σ, identical Huber f_scale=2.0, identical `physical_layout_prior_residuals` — only the delay-freedom lever changed) | **DIAGNOSTIC ONLY, NON-DEPLOYABLE**: the oracle is fit against Vicon truth, so this is circular by construction. It measures a *floor*, not an achievable config. |
| **arm5** prior-strength sweep | conditional on arm2 anisotropy surviving | — | **NOT TRIGGERED** (see Stage C) |

**Config ambiguities resolved (Stage A, ¶4 of the task):**
- **Bound is SYMMETRIC ±60 mm**, confirmed in `solve_v4` source (`lo=-60.0, hi=+60.0`, lines 463–464)
  — not a one-sided `[0,60]`. The `[0,60]`-style phrasing elsewhere (including §5d above, now corrected)
  described the *observed* delays (which happened to land ≥0), not the coded bound.
- **Regularizer is Tikhonov `(d_i/20)²`** (`dly[1:]/20.0` inside the residual vector, σ=20 mm) — confirmed.
- **Param count 18+7 confirmed**: `pos_param_map(8)` yields exactly 18 free geometry coordinates (24 raw
  minus 6 gauge dof: A fixed at origin (3), B constrained to the +x axis (2), C constrained to the xy-plane
  (1)); delay has 7 free values (`d[1:]`) with **d_A fixed at 0 as the gauge choice** (not a bound edge).

### Stage B — per-arm decomposition (§5d code, reused verbatim)

| arm | s_iso | scale exp. | s_X exp. | **s_Y(vert) exp.** | s_Z exp. | aniso ratio | rigid RMS | scale % | shape % | **A-mode %** | mean d̄ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| arm0 production | 0.9583 | +4.36% | +3.06% | **+8.13%** | +4.15% | 0.958 | **105.42** | 66.9% | 33.1% | 16.4% | 34.4 mm |
| arm1b bound ±150 | 0.9665 | +3.46% | +2.76% | +5.86% | +3.27% | 0.973 | 103.91 | 41.7% | 58.3% | 26.6% | 50.7 mm |
| arm1 bound ±200 | 0.9662 | +3.49% | +2.66% | +5.92% | +3.37% | 0.973 | **110.83** (worse) | 37.4% | 62.6% | 17.4% | 50.8 mm |
| arm2/arm3 σ_e=20 | 1.0098 | −0.97% | −2.09% | **−1.75%** | −0.02% | 1.007 | **62.99** | 17.1% | 82.9% | 24.9% | 112.0 mm |
| arm3 σ_e=60 | 1.0069 | −0.69% | −1.81% | −1.53% | +0.28% | 1.008 | 72.74 (worse) | 9.8% | 90.2% | **53.6%** | 108.3 mm |
| arm3 σ_e=200 | 1.0044 | −0.44% | −1.65% | −1.48% | +0.63% | 1.010 | 94.86 (worse) | 5.6% | 94.4% | 49.2% | 105.4 mm |
| arm3 σ_e=∞ | 1.0042 | −0.42% | −1.66% | −1.43% | +0.66% | 1.009 | 98.59 (worst) | 5.2% | 94.8% | 44.8% | 105.3 mm |
| **arm4 oracle (floor)** | **0.9988** | **+0.12%** | −0.67% | +0.66% | +0.50% | **0.993** | **47.08 (best)** | 5.4% | 94.6% | **10.8% (lowest)** | 94.6 mm |

*(All arm0/arm1/arm2 numbers cross-checked against the independently-coded archived audit metrics —
sim3_scale and rigid_anchor_rmse agree to 4 decimal places throughout. "A-mode %" = energy share of the
post-anisotropic shape mode with the largest |A loading| — tracks the anchor-A-centered residual across
arms, per the P3 pre-registration.)*

### Stage C — P1–P4 adjudication (pre-registered, reported both ways as instructed)

**P1 (bound-only relaxation):** predicted "if aniso also doesn't move → bound isn't the route; if aniso
moves specifically → bound is vertical-specific." **Neither cleanly holds.** Scale barely moves (+4.36%→
+3.46/3.49%, confirming **"scale doesn't collapse,"** per the June record) and anisotropy shrinks only
partially (aniso ratio 0.958→0.973, vertical/horizontal gap 4.53pp→3.1–3.3pp — a ~30% reduction, not a
collapse). **But rigid RMS-vs-Vicon gets WORSE (105.4→110.8 mm at ±200)** while shape share balloons
(33%→63%). **Verdict: bound-only relaxation is a net loss** — it trades a little scale+anisotropy for a
lot more shape, worsening the truth-fit. Bound is **not** a viable standalone fix; the regularizer/
common-mode structure (arm2) matters far more than the bound value.

**P2 (common-mode freeing):** predicted "iso bucket → ≈0; aniso+shape largely survive, internal split of
the 63 mm residual is what's being tested." **Iso confirmed** (66.9%→17.1% of energy; s_iso crosses to
essentially flat, +4.36%→−0.97%). **Aniso is REFUTED as surviving** — the vertical/horizontal gap
**collapses** from +4.53 pp to **−0.69 pp** (aniso ratio 0.958→1.007, i.e. essentially isotropic again).
This is the **decisive, non-obvious finding**: **the +8.1% vertical anisotropy in production v4-io is
itself a common-mode-delay-treatment artifact, not a genuine vertical/geometric effect** — which is
exactly why arm5 (below) was correctly skipped. Shape dominates the 63 mm residual (82.9%; the internal
split the task asked for), with the A-mode carrying 24.9 percentage points of that.

**P3 (A-shape vs delay-differential freedom):** predicted testing whether `e_A` rises toward the
oracle-implied differential (`oracle_d_A − mean(oracle_d) = 148.2 − 94.6 = +53.6 mm`) as σ_e loosens,
with A-mode shrinking in lockstep (disguise) or staying stubborn (genuine error). **Neither literal
prediction holds — the actual result is more informative than either:** `e_A` moves **−12.4 → −36.7 →
−50.4 → −58.4 mm** as σ_e loosens — **magnitude converges toward |53.6| but with the OPPOSITE SIGN**, while
A-mode energy **grows** (24.9%→53.6%→49.2%→44.8%), not shrinks, and the internal fit improves
(`pair_rmse` 38.3→33.0 mm) while truth-alignment **worsens** (62.99→98.59 mm). This is **classic
overfitting**: uninformed differential-delay freedom lets the solver trade internal range-residual fit
for truth-alignment, in the wrong direction. **The clean discriminator is arm4**: when A's delay is set
to its *correct* (oracle) value rather than merely *freed*, A-mode energy **drops to its lowest value
across every arm (10.8%)** and truth RMS hits its best value (47.08 mm). **Revised verdict: A's residual
is sensitive to delay-CORRECTNESS, not delay-FREEDOM** — blind differential freedom (arm3) makes it worse;
only the true delay (arm4, non-deployable) makes it better. This reinforces, with a second independent
line of evidence, the standing **"physically re-survey anchor A"** recommendation (§5b-REVISED/§5c/§6):
no solver-side reparameterization recovers what a correct measurement would.

**P4 (oracle floor):** arm4's geometry-only, oracle-delay residual is **47.08 mm RMS, 94.6% shape** — this
is the **"consumable floor"**: the best any delay-treatment fix can do, since it assumes the (circular,
non-deployable) exactly-correct delays. Registration-sensitivity context: the task's ±6.5 mm reference
could **not** be independently located at that exact value in the repo (reported honestly per the
diligence requirement, not fabricated); the actual archived registration-sensitivity Monte Carlo
(`item4_registration_sensitivity_summary.csv`, ±5 mm radial/isotropic perturbations) moves rigid RMSE by
**±3–3.5 mm** about the 105.4 mm baseline — an order of magnitude below the 47 mm floor, so registration
noise does not explain the floor. Basin/init uncertainty for **this** pipeline (not the borrowed ~24 mm
V3-box number) is separately confirmed tight: arm2's own 5-perturbed-init spread was **<0.03 mm** (archived
`item3_perturbed_init_spread.csv`), and this session's cold-MDS-vs-warm-chain contrast at the arm3 ladder
end differed by **2.63 mm** — both well below the 47 mm floor. **The 47 mm floor is therefore real
layout/shape signal, not registration or basin noise.**

### Lever × bucket attribution matrix (Paper B core table)

| lever | Δs_iso (pp, vs arm0) | Δ aniso ratio (vs arm0/arm2) | Δ A-mode % (pp) | Δ RMS-vs-Vicon (mm) |
|---|---:|---:|---:|---:|
| **L0** baseline (d_A=0, bound ±60, σ_e=20) | ref: +4.36% | ref: 0.958 | ref: 16.4% | ref: 105.42 |
| **L1** bound ±60→±200 (arm1 vs arm0) | −0.87 (→+3.49%) | →0.973 (partial, −30% of gap) | +1.0 (→17.4%) | **+5.4 (worse)** |
| **L2** common-mode freed (arm2 vs arm0) | **−5.33 (→−0.97%)** | **→1.007 (collapses)** | +8.5 (→24.9%) | **−42.4 (much better)** |
| **L3** differential freed, uninformed (arm3 σ_e:20→∞, vs arm2) | +0.55 (→−0.42%) | →1.009 (negligible further move) | **+19.9 (→44.8%, worse)** | **+35.6 (worse)** |
| **L4** delay made CORRECT (arm4 vs arm2, oracle) | +1.09 (→+0.12%) | →0.993 (negligible) | **−14.1 (→10.8%, better)** | **−15.9 (better)** |

**Reading:** L2 (freeing the common mode) is the single largest, and only clearly *beneficial*, lever —
it fixes both scale and anisotropy and roughly halves RMS-vs-Vicon. L1 (bound alone) and L3 (uninformed
differential freedom) are both **net-negative** levers that look like they're "giving the solver more
truth" but actually let it overfit range-data noise at truth's expense. L4 (correctness, not freedom) is
the only other net-positive lever, and it is the one that's fundamentally non-deployable.

### (mean fitted delay, s_iso) trajectory — the scale↔delay near-degeneracy, empirically

| arm | mean d̄ (mm) | s_iso |
|---|---:|---:|
| arm0 (production) | 34.4 | 0.9583 |
| arm1b (bound ±150) | 50.7 | 0.9665 |
| arm1 (bound ±200) | 50.8 | 0.9662 |
| arm2 / arm3 σ_e=20 | 112.0 | 1.0098 |
| arm3 σ_e=60 | 108.3 | 1.0069 |
| arm3 σ_e=200 | 105.4 | 1.0044 |
| arm3 σ_e=∞ | 105.3 | 1.0042 |
| arm4 (oracle, TRUE mean delay) | 94.6 | 0.9988 |

As mean fitted delay rises from the constrained 34–51 mm regime into the freed ~105–112 mm regime, s_iso
swings from +4.36% through to slightly *negative* (−0.97% at its most delay-permissive), overshooting the
TRUE value (arm4: mean delay 94.6 mm ↔ s_iso +0.12%, the closest of any arm to 1.0). This is the
scale↔delay near-degeneracy the June audit flagged (ρ≈−0.977-class coupling) made visible as an empirical
curve: **no amount of delay freedom alone lands exactly on truth — only delay correctness does**, and the
production config (arm0) sits on the wrong side of that curve by under-fitting delay (34 mm mean vs the
true 94.6 mm), which is why its scale reads high.

### Honest caveats

- **Arm4 is diagnostic, not deployable** — the oracle delays are fit against Vicon truth, so using them
  to "improve" v4-io would be circular. It exists only to bound how much of the remaining residual is
  delay-attributable vs genuine layout error.
- **Arm5 was correctly skipped, not silently dropped**: the pre-registered trigger (vertical/horizontal
  anisotropy gap surviving past arm2, threshold 2.0 pp) evaluated **false** (gap −0.69 pp) — logged above,
  not assumed.
- **The ±6.5 mm registration-sensitivity figure named in the task could not be pinned to an exact archived
  source** despite a real search; the closest analogous number found (±5 mm perturbation → ±3–3.5 mm RMSE
  shift) is reported instead of guessing which figure was meant.
- **A-mode "%”** tracks the post-anisotropic shape mode with the largest |A loading|, not a hand-picked A-only
  residual — at σ_e=60/200/∞ this happens to be the dominant mode (mode 1); at arm0/arm2/arm4 it may be a
  lower-ranked mode. The metric is consistent across arms by construction (same selection rule every time).
- **8-anchor sample size** still applies to every shape-PCA claim above (3 spatial modes only per arm);
  treat low-energy modes per arm with the same "possible noise" caveat as §5d's mode 3.

Scripts: `erlangen_decompose_lib.py`, `diag_erlangen_ablation.py` (new, read-only w.r.t. all production
files). Stdout: scratchpad `erlangen_ablation.out`.
