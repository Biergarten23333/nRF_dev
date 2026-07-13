# Geiger MODE_SCAN — Proxy-Gate + Sigma-Map + Gauge Report

**Capture:** `logs/geiger_scan_20260711_161258_8anchor/scan.log` — 517 cycles, 430 solved (median solve-residual 127 mm).

**Method locks:** LOO residuals only · partial Spearman controlling {solved_range, anchor_id, n_anchors} · locked 9-feature set · pre-registered GO/NO-GO · no post-hoc feature sweep.

**Compute:** LOO on Pool(6) — 3225 re-solves in 0.65s wall, ~5.5 cores busy (92% of 6). i7-8700K 6C/12T, no GPU.


## VERDICT: **UNDERPOWERED**

Best partial ρ = **-0.103** (feature `early_ratio`), best discriminative AUC = **0.623** (feature `early_ratio`): between the GO and NO-GO bars. Underpowered — needs the tripod occlusion ladder (controlled NLOS) to decide.


### Bottom line

- **Proxy gate: does not open here.** Across a freely-moving probe with mostly-LOS geometry, per-position CIR features carry only a **weak** trace of ranging error (best |ρ|≈0.10, AUC≈0.62). Not the flat null of the co-located listeners, but nowhere near the 0.30/0.70 GO bar. The one significant feature (`early_ratio`) points the **physically-correct** way (more LOS energy → less error), so the signal is real but tiny.

- **Why underpowered, not NO-GO:** this capture is ~70% LOS with |e| dominated by geometry/solve noise (~158 mm), not multipath. To move the needle you need *forced* NLOS contrast — the tripod occlusion ladder — so CIR features see a real LOS↔NLOS swing to correlate against.

- **The bigger correctable win is the gauge, not the proxy:** a common-mode **-100 mm** Geiger antenna-delay offset + an APS011-order **+3.6%** range slope explain most systematic error — both fixable in firmware (antenna-delay recal + `dwt_getrangebias()`).


## P1.4 — Spatial channel-quality map

See `figures/sigma_map_per_anchor.png` (8 per-anchor tiles) and `figures/sigma_map_aggregate.png`. Positions **inside** the anchor XY footprint (88% of samples) have median |LOO e| = **151 mm** vs **257 mm** outside it — the expected GDOP pattern (accuracy degrades past the convex hull of the cage). The per-anchor tiles show no single room region that uniquely poisons one anchor, consistent with the mostly-LOS, multipath-light channel.


## P1.3 — Gate table (pooled, n=385)

| feature | raw ρ | partial ρ | partial p | CI95 | AUC>250mm | AUC_disc |
|---|---|---|---|---|---|---|
| `early_ratio` ⭐ | -0.160 | **-0.103** | 0.0458 | [-0.21,+0.00] | 0.377 | 0.623 |
| `relative_power` | +0.110 | **+0.071** | 0.169 | [-0.03,+0.17] | 0.581 | 0.581 |
| `FP_PK_ratio` | -0.103 | **-0.062** | 0.231 | [-0.16,+0.04] | 0.428 | 0.572 |
| `friis_residual` | -0.076 | **-0.061** | 0.24 | [-0.16,+0.04] | 0.447 | 0.553 |
| `RMS_delay_spread` | +0.065 | **+0.051** | 0.321 | [-0.06,+0.16] | 0.521 | 0.521 |
| `rise_time` | +0.032 | **+0.045** | 0.384 | [-0.06,+0.14] | 0.499 | 0.501 |
| `kurtosis` | -0.054 | **-0.019** | 0.709 | [-0.12,+0.09] | 0.461 | 0.539 |
| `SNR_fp` | -0.032 | **-0.011** | 0.832 | [-0.11,+0.09] | 0.470 | 0.530 |
| `pre_fp_leak` | +0.028 | **+0.000** | 0.993 | [-0.11,+0.11] | 0.518 | 0.518 |

_Best partial ρ: `early_ratio` = -0.103 (p=0.0458). Best discriminative AUC: `early_ratio` = 0.623. Large-error class |e|>250mm: 117/385 rows (30%)._


_The only feature with p<0.05 is `early_ratio` (ρ=-0.103): higher early-tap energy concentration → **lower** error, the physically-correct LOS direction. But |ρ|≈0.10 is far below the 0.30 GO bar and the bootstrap CI straddles/just-touches zero — a hint, not a gate._


## P2.1 — Per-anchor gauge fit  e = a + b·r

| anchor | a (mm) | b (%) | b_SE (%) | R² | n |
|---|---|---|---|---|---|
| A | -144 | -0.41 | ±1.71 | 0.00 | 410 |
| B | -75 | +5.00 | ±1.50 | 0.03 | 415 |
| C | -132 | +10.34 | ±1.44 | 0.11 | 414 |
| D | -249 | +6.21 | ±1.97 | 0.02 | 409 |
| E | +245 | -1.96 | ±1.33 | 0.01 | 408 |
| F | -169 | +3.55 | ±1.68 | 0.01 | 400 |
| G | -181 | +2.29 | ±1.31 | 0.01 | 390 |
| H | -99 | +4.30 | ±1.60 | 0.02 | 379 |

`a` = common-mode Geiger antenna-delay error + anchor-i bias; `b` = range-dependent (APS011-class) slope. **Common-mode intercept ⟨a⟩ = -100 mm** — the mobile Geiger reads systematically short by ~100 mm (its own antenna-delay offset), the single largest correctable term. Per-anchor R² ≤ 0.11: the range-slope is weak against the ~158 mm LOO noise, so individual `b` values are poorly constrained (see b_SE) — do not over-read a single anchor's slope.


## P2.2 — Cross-check vs AutoPos fitted delays (`d_anchor_mm`, bound [0,60])

| anchor | gauge a (mm) | AutoPos delay (mm) | at bound? |
|---|---|---|---|
| A | -144 | 0.0 | no |
| B | -75 | 12.8 | no |
| C | -132 | 60.0 | **yes** |
| D | -249 | 30.9 | no |
| E | +245 | 18.6 | no |
| F | -169 | 13.0 | no |
| G | -181 | 31.6 | no |
| H | -99 | 60.0 | **yes** |

Spearman ρ(gauge intercept a, AutoPos delay) over 8 anchors = **-0.095** (p=0.823) — **no significant relation**.

Delay-bound saturated anchors: **C, H**.

_Interpretation:_ the gauge intercepts are dominated by the common-mode Geiger offset (⟨a⟩=-100 mm) plus ~158 mm LOO noise; the AutoPos per-anchor delays span only 0–60 mm, so the intercept cannot resolve them and the bound-saturation hypothesis is **not testable** at this SNR. _Note vs brief:_ it expected C(−126) & E(−118) most-biased with 4/8 at the bound; the V4-io layout instead pins A=0 (reference) and only **C, H** hit the +60 mm bound — reported as found, not as expected.


## P2.3 — Slope vs APS011

Primary test = **pooled within-anchor slope** (anchor fixed-effects, the well-powered estimate): b = **+3.65% ± 0.55%** (±1 SE) vs APS011 channel-5 PRF64 ≈ **+2.77%**. Median of the 8 noisy per-anchor slopes = +3.92%. → **consistent** (APS011 inside the ±2 SE band).

A positive range-dependent slope of the APS011 order is present, so enabling `dwt_getrangebias()` should remove of order ~2.8% of range-dependent error — but note the estimate's SE (±0.6%) is wide, so this is directional evidence, not a tight calibration.


## P1.5 — Rotation / low-motion segments (body-shadow)

Pre-registered gate (pos-std<300 mm **and** speed<300 mm/s, ≥15 s) qualified **0 cycles**. Reason it fails: trilateration jitter (~158 mm/frame at ~4.3 Hz) yields an apparent-speed floor of **528 mm/s** for a genuinely stationary hand — above the 300 mm/s gate. I therefore use **adaptive** low-motion windows (below the 35th percentile of rolling std & speed).

- **Seg 1** t=[0,13]s (13s): A p2p=0.44(n6), B p2p=0.25(n7), C p2p=0.25(n6), D p2p=0.22(n7), E p2p=0.24(n7), F p2p=0.29(n5), G p2p=0.24(n5), H p2p=0.21(n5)


With only ~0.6 CIR/s per anchor, each segment holds only a few frames per anchor, so oscillation **amplitude** (p2p) is reported but period is not resolvable — body-shadow is suggestive, not quantified. See figure.


## Artifacts

- `loo_residuals.npz`
- `cir_features.npz`
- `gate_verdict.json`
- `figures/gauge_fit_per_anchor.png`
- `figures/sigma_map_per_anchor.png`
- `figures/sigma_map_aggregate.png`
- `figures/rotation_shadow.png`
