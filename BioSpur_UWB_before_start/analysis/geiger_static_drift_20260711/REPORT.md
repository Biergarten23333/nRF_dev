# Geiger Static Overnight — Per-Anchor Bias Temporal Stability / Thermal Drift

**Capture:** `logs/geiger_overnight_static_20260711/scan.log` — **160,678 LSCAN** over **10.61 h** (2026-07-12T00:34:02+0200 → 2026-07-12T11:10:30+0200), steady **4.21 Hz**, 0 reconnects. Geiger static (fixed position), ranging to all 8 anchors A–H.

**Method:** true range to each anchor is constant (static), so range(t) variation = bias drift + noise. Decomposed into **common-mode** (robust median across anchors → shared Geiger antenna-delay/clock term) and **differential** (per-anchor → thermal/multipath/events). single-thread; CIR truncated before parse (memory-safe). Time axis: uniform-rate (no per-line timestamp; 0 reconnects so index≈time).

**Compute:** 12 logical cores; parse is single-thread I/O+regex bound (~9s total incl. Allan/trilat), numpy stages light — no GPU. CIR never loaded (each line truncated at `;cir=`), peak RAM < 200 MB.


## TL;DR

- **5/8 anchors are temporally stable** over 10.6 h: **A, C, D, F, G** — single-sample noise σ₁≈23–29 mm and <40 mm slow drift. The bias of a warmed-up link is stable to a few tens of mm all night.

- **The drift budget is NOT smooth thermal — it is dominated by a few discrete per-anchor events:** **B** = STEP/EXCURSION; **E** = BURSTY (multipath); **H** = NOISY.

- **Shared (common-mode) drift is small and slow, not a cold-start transient.** Using a robust median across anchors, the common-mode is flat (±5 mm) for the first ~6 h, then rises **12 mm** through the morning (first-2h -1 → last-2h 11 mm; slope 1.25 mm/hr) — consistent with the room warming toward midday. (An exponential warm-up does *not* fit: fit_ok=False. The 'τ≈45 min' a mean-based common-mode would show is an artifact of B's step, not real.)

- **CFO front-end warm-up is real but decoupled from range.** rxtofs settles 6 units in the first ~1.5 h then plateaus, yet it does **not** track the range common-mode (Spearman ρ=-0.02, p=0.55) — so CFO is a radio-thermal indicator, not a usable range-bias proxy here. AGC constant: True.

- **Net position wander** of the static probe: full-run p2p (x,y,z) = 129,138,**513** mm, dominated by B's step leaking into the solve. Pre-event (<6.4 h) horizontal stability is **x,y ≈ 91,121 mm**; z stays large (~381 mm) from poor z-DOP amplifying E/H multipath, not drift.


## 1 · Per-anchor stability, drift & noise

| anchor | class | n | valid% | median mm | std mm | IQR mm | drift mm/hr | total drift mm | σ₁ mm |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|
| A | STABLE | 157,281 | 97.9 | 3373 | 26 | 29 | 1.42 | 15 | 24 |
| B | STEP/EXCURSION | 157,360 | 97.9 | 2504 | 194 | 291 | -3.29 | -35 | 161 |
| C | STABLE | 157,198 | 97.8 | 2377 | 25 | 32 | 1.09 | 12 | 24 |
| D | STABLE | 157,273 | 97.9 | 3418 | 24 | 26 | 0.76 | 8 | 23 |
| E | BURSTY (multipath) | 156,807 | 97.6 | 3447 | 200 | 334 | 6.25 | 66 | 190 |
| F | STABLE | 156,715 | 97.5 | 2004 | 32 | 42 | 3.19 | 34 | 26 |
| G | STABLE | 156,480 | 97.4 | 2424 | 29 | 35 | 0.54 | 6 | 29 |
| H | NOISY | 156,295 | 97.3 | 3362 | 65 | 65 | 2.95 | 31 | 67 |

`drift` = robust Theil-Sen slope on 1-min medians × 10.6 h = `total drift`; `σ₁` = single-sample noise (√2-scaled successive differences). For **stable** anchors σ₁ and drift are the real bias-stability numbers; for BURSTY/STEP anchors these are inflated by the events (see §1b) and should not be read as smooth drift.


### 1b · Event / instability detail

| anchor | class | σ₁ mm | burst frac (\|Δ\|>100mm) | #excursions | longest excursion (min) | max \|Δ\| mm |
|---|---|--:|--:|--:|--:|--:|
| A | STABLE | 24 | 0.000 | 0 | 0 | 18 |
| **B** | **STEP/EXCURSION** | 161 | 0.228 | 2 | 137 | 353 |
| C | STABLE | 24 | 0.000 | 0 | 0 | 23 |
| D | STABLE | 23 | 0.000 | 0 | 0 | 29 |
| **E** | **BURSTY (multipath)** | 190 | 0.167 | 53 | 18 | 298 |
| F | STABLE | 26 | 0.000 | 0 | 0 | 52 |
| G | STABLE | 29 | 0.000 | 0 | 0 | 19 |
| **H** | **NOISY** | 67 | 0.000 | 0 | 0 | 44 |

_`burst frac` = fraction of 1-min bins with |differential| > 100 mm; a **BURSTY** anchor (many short excursions) is multipath/NLOS-limited (E flips between LOS and a ~+290 mm image); a **STEP/EXCURSION** anchor holds one shifted level for >30 min (B dropped ~300 mm for ~2.4 h mid-run). Neither is temperature drift — they are link-geometry / environment events._


## 2 · Common-mode vs differential decomposition

**Common-mode** = median across anchors of each anchor's deviation from its own baseline (robust, so a single stepping/bursting anchor cannot fake a shared drift). Shape: flat within ±5 mm for the first ~6 h, then a monotonic rise to +11 mm by the end (first-2h -1 → last-2h 11 mm, net 12 mm; slope 1.25 mm/hr, p2p 29 mm). This is a slow morning warming trend, **not** a power-on transient (exponential-settle fit rejected). See `figures/commonmode_differential.png`.


**Differential** (per anchor, common-mode removed = genuine per-anchor thermal/geometry):

| anchor | diff slope mm/hr | diff total mm | diff p2p mm |
|---|--:|--:|--:|
| A | -0.33 | -3 | 28 |
| B | -2.51 | -27 | 397 |
| C | -0.25 | -3 | 37 |
| D | -0.40 | -4 | 46 |
| E | 5.17 | 55 | 552 |
| F | 2.09 | 22 | 70 |
| G | -0.58 | -6 | 38 |
| H | 1.31 | 14 | 79 |

_Interpretation: if common-mode ≫ differential, the drift is mostly the **Geiger's own** warm-up (one antenna-delay recal fixes all anchors); large differential on a specific anchor points to that link's geometry/multipath or that anchor's own thermal._


## 3 · Thermal proxy (CFO) & stability

`rxtofs` (receiver carrier/timing-offset proxy) shows a genuine **cold-start settle of 6 units in the first ~1.5 h** (first-1h 115 → plateau 122), i.e. the radio front-end warming up, then flat (slope 0.121/hr). **But it does not track the range common-mode** (Spearman ρ=-0.02, p=0.546): the CFO warms up in the first hour while the range common-mode rises in the *morning* — different time courses. So CFO indicates front-end temperature but is not a usable proxy for the range bias here. AGC is constant (True) and ttcki is a fixed config word (no room thermometer was logged). Per-anchor CFO slopes: A=0.241, B=-1.372, C=-0.767, D=1.068, E=0.000, F=0.510, G=-0.476, H=0.886.


Overlapping **Allan deviation** per anchor in `figures/allan_deviation.png`: the −½-slope region is white measurement noise (averaging helps), the minimum marks the optimal averaging time, and the up-turn at long τ is the drift/thermal random-walk floor.


## 4 · Net position stability

Trilaterating the 1-min-binned ranges (636/636 bins solved, median fit residual 215 mm) gives the static probe's apparent position wander. Full-run p2p (x,y,z) = 129, 138, 513 mm looks large, but the z/x jumps coincide exactly with B's step at 6.7 h — a single bad anchor corrupting the non-robust solve. Before the B event (<6.4 h) the wander drops to **91, 121, 381 mm** (x,y,z). The horizontal **x,y ≈ 91–121 mm is the genuine drift/noise stability**; the large **z (~381 mm) is dominated by poor z-DOP amplifying the E/H multipath bursts**, not by bias drift. An outlier-rejecting solver would pull the full-run number back toward this. See `figures/cfo_and_position.png`.


## 5 · Takeaways

- **Per-anchor bias is temporally stable for a clean line-of-sight link.** 5/8 anchors (A, C, D, F, G) held to σ₁≈25 mm single-sample noise and <40 mm slow drift over 10.6 h. Temperature is a **small** term: the robust shared drift is only 1.25 mm/hr (a ~12 mm morning rise), and there is no sharp power-on transient in the *range* (the front-end CFO does settle in ~1.5 h, but that does not propagate to a range bias).

- **The real stability risk is per-anchor link events, not temperature:** **B** STEP/EXCURSION (max |Δ| 353 mm); **E** BURSTY (multipath) (max |Δ| 298 mm); **H** NOISY (max |Δ| 44 mm). These are multipath/geometry (E flips LOS↔≈+290 mm image) and environment (B held ≈−300 mm for ~2.4 h mid-run). They are **not** fixed by antenna-delay recal or a thermal model — they need anchor placement/occlusion addressed and an outlier-rejecting solver (LOO/robust trilateration).

- **Practical guidance:** per-anchor bias is stable to a few tens of mm once running; gate anchor **E** (and watch **B/H**) with a first-path/multipath-quality check rather than trusting its raw range, and use a robust solver so one stepped anchor cannot move the fix (as B did here, injecting a spurious ~400 mm z-jump). The slow morning common-mode (1.25 mm/hr) is negligible for short sessions.

- **CFO is not an actionable range-bias proxy from this run** (ρ=-0.02, p=0.55); it tracks front-end temperature on a different time course. Closing the thermal loop properly needs a logged room/board thermometer alongside the range.


## Artifacts

- `report.json`
- `figures/per_anchor_range_vs_time.png`
- `figures/commonmode_differential.png`
- `figures/allan_deviation.png`
- `figures/cfo_and_position.png`
