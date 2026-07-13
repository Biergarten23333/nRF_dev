# Person-Effect Analysis — CIR + Ranging (person vs clean)

**Scans:** `logs/geiger_scan_20260711_post_aps011/scan_person.log` (person near BCFG wall, 1025 cyc) vs `logs/geiger_scan_20260711_post_aps011/scan.log` (empty room, 758 cyc). Same 8-anchor geometry (`system_calibration_20260710_233443`), same post-APS011 firmware.

**Design:** both scans carry the *same* APS011 over-correction, so the **person−clean delta cancels it**; CIR is unaffected by APS011 (it only rescales computed range, not the raw accumulator). Absolute gauge numbers are APS011-contaminated and flagged as such; deltas are clean.

**Compute:** i7-8700K, 12 logical cores. LOO trilateration on `Pool(10)` — person 6304 re-solves in 1.43s (~9.0 cores busy, 90% of 10); clean 4646 in 1.23s (~8.4 cores). No GPU used (pure CPU task).


## TL;DR

- **Person detectable from CIR?** Per-anchor CIR classifier (LDA on 12 feats, cir_aid known) CV-AUC = **0.543** mean / **0.624** best (anchor E); best single feature×anchor AUC = **0.623** (`relative_power@B`). Global pooled-across-anchor LDA collapses to **0.501** (the effect is anchor-specific in *sign*, so a single global combo cancels). **Not at the 0.7 bar.**

- **Person detectable from ranging alone?** per-cycle LOO-residual classifier CV-AUC = **0.613**. **Not at the 0.7 bar.**

- **⚠ Walk confound:** position-only (solved x,y,z) CV-AUC = **0.590** — the two scans are *different walks* (person y-median 600 mm vs clean 1161 mm, p≈3e-8). This floor is ≈ the ranging-only AUC, so most of the ranging 'detection' is trajectory, not body. Trust the *deltas* and shape-only CIR, not raw AUC.

- **Most affected anchor** (|Δ LOO mean|): **G**; **most consistently-shifted CIR feature** (mean |rank-biserial| across anchors): **early_ratio**; best single cell `relative_power@B`; CIR-shape most-affected anchor G.

- **Person common-mode range shift** (Δ gauge intercept) = **-76 mm**, Δ slope = **1.56%** — reproduces the earlier quick-look (≈76 mm, ≈1.5%).


## 1 · Per-anchor ranging

### 1a. Raw range distribution (mm) — 8 anchors × 2 conditions

| anchor | cond | n | mean | median | std | min | max | IQR |
|---|---|--:|--:|--:|--:|--:|--:|--:|
| A (far) | person | 875 | 3341 | 3238 | 884 | 1056 | 5968 | 1294 |
| A (far) | clean | 656 | 3420 | 3372 | 784 | 1556 | 5300 | 1232 |
| B (near) | person | 887 | 2834 | 2797 | 933 | 395 | 6021 | 1104 |
| B (near) | clean | 655 | 2832 | 2843 | 862 | 768 | 5521 | 986 |
| C (near) | person | 874 | 3434 | 3472 | 891 | 595 | 5643 | 1186 |
| C (near) | clean | 635 | 3172 | 3173 | 856 | 672 | 5207 | 1234 |
| D (far) | person | 910 | 3655 | 3674 | 881 | 750 | 6311 | 1181 |
| D (far) | clean | 616 | 3434 | 3526 | 884 | 459 | 5592 | 1102 |
| E (far) | person | 819 | 3068 | 2974 | 896 | 922 | 5447 | 1358 |
| E (far) | clean | 613 | 3197 | 3156 | 829 | 1179 | 5852 | 1266 |
| F (near) | person | 816 | 2658 | 2649 | 888 | 376 | 5760 | 927 |
| F (near) | clean | 602 | 2671 | 2680 | 863 | 539 | 5615 | 956 |
| G (near) | person | 801 | 3274 | 3376 | 915 | 440 | 5239 | 1115 |
| G (near) | clean | 599 | 3067 | 3082 | 894 | 729 | 5249 | 1224 |
| H (far) | person | 807 | 3348 | 3380 | 945 | 365 | 5837 | 1208 |
| H (far) | clean | 604 | 3211 | 3261 | 888 | 345 | 5423 | 1160 |

### 1b–1c. Per-anchor person−clean deltas

`Δraw` = mean(person)−mean(clean) raw range (⚠ confounded by where the Geiger walked). `ΔLOO` = mean signed LOO-residual delta (position-robust — the causal person read). `Δσ_LOO` = LOO-residual std change (multipath scatter).

| anchor | group | Δraw mm | ΔLOO mm | Δσ_LOO mm | sign(ΔLOO) |
|---|---|--:|--:|--:|---|
| A | far | -79 | -66.1 | 14.7 | shorter |
| B | near | 2 | 8.6 | -11.0 | longer |
| C | near | 263 | -83.4 | 22.1 | shorter |
| D | far | 221 | 0.3 | 28.0 | longer |
| E | far | -129 | 7.6 | 26.4 | longer |
| F | near | -14 | -2.1 | -2.7 | shorter |
| G | near | 207 | -83.6 | 1.5 | shorter |
| H | far | 137 | -18.9 | -36.3 | shorter |

### 1d. Geometry classification (near BCFG vs far ADEH)

| group | anchors | mean Δraw mm | mean ΔLOO mm | mean Δσ_LOO mm |
|---|---|--:|--:|--:|
| near_BCFG | B,C,F,G | 115 | -40.1 | 2.5 |
| far_ADEH | A,D,E,H | 37 | -19.3 | 8.2 |

**Does proximity predict the shift?** Near-wall mean ΔLOO = -40.1 mm vs far-wall -19.3 mm — but this is **not** a uniform near-wall effect: it is driven almost entirely by **G(-84), C(-83)** mm, while near-wall anchors B, F barely move. And the variance *increase* is actually larger on the **far** wall (8.2 vs 2.5 mm). So proximity is a **weak, anchor-specific** predictor (C & G, the two near-wall anchors most side-on to the seated body), not a clean 'near wall = worse' rule — consistent with the earlier quick look. Note both walls shift *shorter* on average (person-induced FP/geometry change), not the longer-range NLOS delay a straight body-blockage would add.


## 2 · Spatial analysis

### 2b. Person-effect heatmap

Full grid in `spatial_delta_grid.csv` and `figures/spatial_delta_heatmap.png` (500 mm cells, median |LOO residual|, ≥3 samples/cell). Δ = person − clean.

### 2c. Distance-to-person vs ranging degradation

Per-cycle mean |LOO residual| vs Euclidean distance to the person proxy (~(3843,1530) mm, XY). Negative ρ ⇒ *closer = worse*.

| cond | n | Spearman ρ (XY) | p | ρ (3D) | p |
|---|--:|--:|--:|--:|--:|
| person | 802 | +0.028 | 0.44 | +0.021 | 0.54 |
| clean | 592 | -0.141 | 0.00057 | -0.156 | 0.00013 |

Person ρ=+0.028 vs clean baseline ρ=-0.141 (clean has no person, so its ρ is the pure GDOP-geometry trend near that wall). Person-minus-clean = +0.169: no meaningful closer=worse trend beyond geometry.


## 3 · CIR feature analysis (Mann-Whitney U, person vs clean)

Per feature × anchor: CLES = P(person>clean) (0.5 = no effect), rank-biserial rbc = 2·CLES−1, BH-FDR q over all 96 tests. Full matrix in `report.json` / `figures/cles_heatmap.png`.

### 3d. Feature importance (ranked by mean |rank-biserial| across anchors)

| feature | shape? | mean|rbc| | mean AUC | max AUC | #sig anchors (q<.05) |
|---|:--:|--:|--:|--:|--:|
| `early_ratio` | ✓ | 0.085 | 0.543 | 0.580 | 0 |
| `pre_fp_leak` | ✓ | 0.073 | 0.536 | 0.601 | 0 |
| `rise_time` | ✓ | 0.072 | 0.536 | 0.565 | 0 |
| `relative_power` |  | 0.072 | 0.536 | 0.623 | 0 |
| `SNR_fp` |  | 0.071 | 0.536 | 0.586 | 0 |
| `fp_subtap` | ✓ | 0.067 | 0.533 | 0.565 | 0 |
| `fp_tap` | ✓ | 0.066 | 0.533 | 0.570 | 0 |
| `RMS_delay_spread` | ✓ | 0.064 | 0.532 | 0.606 | 0 |
| `fp_mag` |  | 0.063 | 0.532 | 0.562 | 0 |
| `FP_PK_ratio` | ✓ | 0.060 | 0.530 | 0.565 | 0 |
| `peak` |  | 0.056 | 0.528 | 0.569 | 0 |
| `kurtosis` | ✓ | 0.049 | 0.524 | 0.576 | 0 |

### 3c. Strongest significant feature×anchor cells (q<0.05)

_No feature×anchor cell survived BH-FDR q<0.05._

> ⚠ Power-based features (`fp_mag`,`peak`,`SNR_fp`,`relative_power`) also move with range/AGC, so a person-vs-clean shift there is partly the different walk. Shape features (✓) are range/AGC-robust and are the trustworthy body-shadow readout.


## 4 · CIR waterfall (FP-aligned mean profiles)

Each CIR is aligned on its first-path tap and peak-normalized, then averaged per anchor (window [−60,+260] taps). `Δfp-lobe` = summed diff over FP..FP+8 taps (first-path lobe); `Δlate-tail` = summed diff FP+20..end (multipath). Full vectors in `report.json`.

| anchor | n_p | n_c | Δfp-lobe (norm) | Δlate-tail (norm) | reading |
|---|--:|--:|--:|--:|---|
| A | 119 | 94 | -0.016 | 0.165 | late multipath up |
| B | 118 | 94 | 0.066 | -0.216 | FP stronger; late multipath down |
| C | 120 | 93 | 0.103 | 0.042 | FP stronger; late multipath up |
| D | 117 | 91 | -0.103 | -0.226 | FP attenuated; late multipath down |
| E | 113 | 93 | -0.036 | 0.276 | FP attenuated; late multipath up |
| F | 115 | 91 | 0.012 | 0.011 | ≈no change |
| G | 113 | 93 | 0.352 | -0.630 | FP stronger; late multipath down |
| H | 115 | 94 | 0.010 | -0.180 | late multipath down |

**Most CIR-affected anchor: G** — Δfp-lobe 0.352, Δlate-tail -0.630. Full difference CIR is in `report.json → part4_waterfall.anchors.G.diff_norm` (and raw profiles) for plotting; see `figures/diff_cir_most_affected.png`.


## 5 · Detection

### 5a. Best single CIR feature × anchor (person vs clean AUC)

| rank | feature | anchor | AUC_disc | CLES | p | q_BH |
|--:|---|---|--:|--:|--:|--:|
| 1 | `relative_power` | B | 0.623 | 0.623 | 0.002 | 0.195 |
| 2 | `RMS_delay_spread` | G | 0.606 | 0.394 | 0.0088 | 0.366 |
| 3 | `pre_fp_leak` | A | 0.601 | 0.399 | 0.011 | 0.366 |
| 4 | `SNR_fp` | A | 0.586 | 0.586 | 0.032 | 0.561 |
| 5 | `early_ratio` | H | 0.580 | 0.420 | 0.048 | 0.561 |
| 6 | `kurtosis` | A | 0.576 | 0.576 | 0.058 | 0.561 |
| 7 | `RMS_delay_spread` | E | 0.572 | 0.572 | 0.076 | 0.561 |
| 8 | `early_ratio` | D | 0.571 | 0.429 | 0.079 | 0.561 |
| 9 | `fp_tap` | H | 0.570 | 0.430 | 0.081 | 0.561 |
| 10 | `peak` | H | 0.569 | 0.431 | 0.087 | 0.561 |

### 5b. Combined CIR classifier (per-frame LDA, 5-fold CV)

**Per-anchor** LDA (12 CIR feats, one model per anchor — cir_aid is always logged so this is leakage-free and is the realistic per-frame CIR detector): mean CV-AUC = **0.543**, best = **0.624** (anchor E).

| anchor | A | B | C | D | E | F | G | H |
|---|---|---|---|---|---|---|---|---|
| CV-AUC | 0.587 | 0.522 | 0.510 | 0.516 | 0.624 | 0.442 | 0.546 | 0.595 |

**Global pooled** LDA (all 12 feats + anchor one-hot, single model) = **0.501 ± 0.010** (n=1678 frames), shape-only **0.466** — it collapses to ~chance because the person shifts a feature *up* at one anchor and *down* at another (e.g. `RMS_delay_spread` CLES 0.39 @G vs 0.57 @E), so one global linear direction cancels. The per-anchor models above are the correct upper bound.

> Each LSCAN cycle captures only **one** anchor's CIR (round-robin cir_aid), so a true multi-anchor single-cycle vector is not available in this firmware — even the per-anchor number is a per-CIR-frame bound, not an 8-anchor-snapshot bound.


### 5c. Ranging-only classifier (per-cycle LDA, 5-fold CV)

8 signed LOO residuals + median/max|e| + n_anchors → LDA CV AUC = **0.613 ± 0.010** (n=1394 cycles).

### 5·confound. Does the walk itself differ? (position control)

| axis | median person | median clean | AUC_disc | p |
|---|--:|--:|--:|--:|
| x | 2594 | 2777 | 0.508 | 0.61 |
| y | 600 | 1161 | 0.585 | 2.6e-08 |
| z | -1012 | -925 | 0.530 | 0.052 |

Position-only LDA CV AUC = **0.590** → this is the *walk-difference floor*. Any CIR/ranging AUC must clear this to be a genuine body signal rather than a different-trajectory artifact.


## 6 · Summary

| metric | person | clean | delta | note |
|---|--:|--:|--:|---|
| N cycles | 1025 | 758 | 267 | |
| N solved | 855 | 627 | | |
| trilat RMS mm (median) | 177 | 179 | -3 | |
| \|LOO residual\| median mm | 241 | 251 | -10 | |
| gauge common-mode mm | 278 | 354 | **-76** |  ⚠APS011 (abs); Δ clean |
| gauge slope % | -5.46 | -7.01 | **1.56** |  ⚠APS011 (abs); Δ clean |

_The absolute gauge slope is **negative** on both scans because APS011 (+2.77%) is already applied in firmware and **over-corrects**, leaving a residual negative range-slope — exactly the over-correction that was later rolled back on hardware. Only the person−clean delta (intercept -76 mm, slope 1.56%) is APS011-free and interpretable as the person effect._

| best single-feat AUC (person) | 0.623 | | | `relative_power@B` |
| per-anchor CIR classifier AUC | 0.543 | | | mean; best 0.624@E |
| global CIR classifier AUC | 0.501 | | | pooled, sign-cancels |
| ranging-only AUC | 0.613 | | | per-cycle |
| position-only AUC (confound) | 0.590 | | | walk-difference floor |
| most affected anchor | G | | | by \|ΔLOO\| |
| most discriminative CIR feature | early_ratio | | | by mean\|rbc\| |

## 7 · Implications for the proxy gate

- **Detectable from CIR?** **Not at 0.7** — per-anchor CIR AUC 0.543 mean / 0.624 best (anchor E), best single feature 0.623, global-pooled 0.501 (sign-cancels).

- **Detectable from ranging alone?** **Not at 0.7** — per-cycle LOO-residual AUC 0.613.

- **Confound caveat:** position-only AUC is 0.590. It is essentially equal to the ranging-only AUC and above the global CIR AUC, so a large share of any 'detection' here is the different trajectory, not the body. The shape-only / per-anchor CIR numbers and the person−clean *delta* metrics are the trustworthy readouts; an unconfounded estimate needs fixed-geometry data (tripod occlusion ladder).

- **NLOS-like signature?** Weak — neither first-path attenuation (Δ=+0.049) nor late-tail growth (Δ=-0.095) is large; the CIR change is subtle, not a textbook NLOS swing.

- **Does this change the proxy-gate verdict?** The prior UNDERPOWERED verdict (best AUC≈0.62) was measured on a **no-person** scan where |e| is dominated by geometry/solve-noise, not body shadow. Here, with a body present, the strongest CIR separation reaches AUC 0.623 (single feature) / 0.624 (per-anchor @E). This does **not** rescue the gate: even with a body present, CIR separation stays below the 0.7 bar and is not cleanly above the walk-difference floor. Consistent with UNDERPOWERED — a controlled occlusion ladder is still required.


## Artifacts

- `report.json`
- `spatial_delta_grid.csv`
- `figures/per_anchor_delta.png`
- `figures/spatial_delta_heatmap.png`
- `figures/diff_cir_most_affected.png`
- `figures/cles_heatmap.png`
