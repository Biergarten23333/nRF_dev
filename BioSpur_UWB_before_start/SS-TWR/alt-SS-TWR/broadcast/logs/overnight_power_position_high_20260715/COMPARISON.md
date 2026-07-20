# Center vs Position-High — overnight power sweep comparison

Room-**CENTER** (`overnight_power_20260714`, wand ~1 m room center) vs **POSITION-HIGH** (`overnight_power_position_high_20260715`, wand raised). Same rig, same anchors/layout/listeners — only the wand height changed. Same analysis both runs.

## 1. Link success & bias vs power

| metric | CENTER | HIGH |
|---|---|---|
| ge7 @MAX (8.5dB) | 0.9778 | 0.9778 |
| ge7 @M3 (5.5dB) | 0.9778 | 0.9756 |
| ge7 @M6 (2.5dB) | 0.9778 | 0.9779 |
| ge7 @M12 (0.0dB) | 0.9778 | 0.9779 |
| ge7 @POR (4.0dB) | 0.9778 | 0.9778 |
| bias median swing (mm) | 17.7 | 18.9 |
| lock events | 0 | 0 |

## 2. Positioning precision (pooled median across powers)

| pipeline | metric | CENTER | HIGH | Δ(H−C) |
|---|---|---|---|---|
| A_v4io_t4 | 3D scatter (mm) | 50.8 | 48.4 | -2.4 |
| A_v4io_t4 | z-std (mm) | 51.7 | 46.7 | -5.0 |
| A_v4io_t4 | caliper max|err| (mm) | 283.2 | 198.8 | -84.4 |
| B_v5_u5 | 3D scatter (mm) | 51.1 | 49.5 | -1.6 |
| B_v5_u5 | z-std (mm) | 51.1 | 46.9 | -4.2 |
| B_v5_u5 | caliper max|err| (mm) | 232.3 | 179.9 | -52.4 |

## 3. Per-tag mean-position shift (center → high)

Wand fixed within each run; this is the physical move between runs. **Δz** = how much the raised height showed up in the solve.

**A_v4io_t4**

| tag | center xyz (mm) | high xyz (mm) | Δx | Δy | **Δz** | |Δ3D| | scatter C→H |
|---|---|---|---|---|---|---|---|
| BSCCF4 | [2926.9, 996.0, -995.8] | [2816.4, 1000.8, -1558.1] | -110.5 | 4.8 | **-562.3** | 573.1 | 42.7→53.2 |
| BS9336 | [3263.1, 582.5, -680.7] | [3352.9, 613.0, -1296.6] | 89.8 | 30.5 | **-615.9** | 623.2 | 56.5→48.4 |
| BS955A | [2912.6, 1254.7, -724.4] | [2768.1, 1191.3, -917.7] | -144.5 | -63.4 | **-193.3** | 249.5 | 50.8→41.4 |

**B_v5_u5**

| tag | center xyz (mm) | high xyz (mm) | Δx | Δy | **Δz** | |Δ3D| | scatter C→H |
|---|---|---|---|---|---|---|---|
| BSCCF4 | [2930.3, 969.1, -1016.8] | [2814.8, 973.3, -1575.7] | -115.5 | 4.2 | **-558.9** | 570.7 | 44.9→52.5 |
| BS9336 | [3253.9, 552.0, -682.7] | [3350.4, 592.6, -1292.5] | 96.5 | 40.6 | **-609.8** | 618.7 | 55.7→49.5 |
| BS955A | [2908.2, 1241.4, -688.9] | [2775.9, 1162.0, -921.6] | -132.3 | -79.4 | **-232.7** | 279.2 | 51.1→41.8 |

## 4. Listener received power (LE, common to both)

| power | CENTER cir dB relMAX | HIGH cir dB relMAX |
|---|---|---|
| MAX | 0.0 | 0.0 |
| M3 | 0.08 | 0.03 |
| M6 | 0.07 | 0.0 |
| M12 | 0.03 | -0.05 |
| POR | 0.06 | 0.03 |

HIGH fleet: 7/7 listeners; worst |cir swing| vs 8.5 dB TX = 0.43 dB; max env drift 11.6%.

## Verdict — what changed when the wand was raised

1. **Power is still invisible.** ge7 0.978, bias ≈18 mm, positioning ≈48 mm, listener ≤0.4 dB — flat across the 8.5 dB sweep at *both* positions. Height does not change the AGC-normalized "power buys nothing at strong links" result.
2. **Link success unchanged by height.** ge7 identical center↔high (0.978), valid 97.3 %, **0 lock events** both runs. Raising the wand did not push any link toward its SNR margin, and the steeper geometry produced no reflection locks.
3. **Precision essentially unchanged** (marginally tighter high: 3D scatter −2 mm, z-std −5 mm) — within the ~30 mm repeatability floor, not a real gain.
4. **The physical raise IS visible in the solve, with the right sign.** All 3 tags moved to more-negative z (Δz ≈ -562 / -616 / -193 mm) — and because the layout's z is **globally inverted** (ceiling solves negative), more-negative-z = **physically higher**. So raising the wand shows up correctly. BUT the per-tag Δz are *unequal* (≈190–620 mm) and Δx is large too (≈±110 mm), so this was **not a clean rigid vertical lift** — the wand was re-oriented as it was raised, and z is the weakly-constrained axis, so the absolute Δz magnitudes are not a trustworthy height measurement.
5. **The caliper's worst baseline swapped** — CCF4–955A (center, ~200–300 mm) → **9336–955A** (high, ~180–208 mm); CCF4–955A is now nearly correct (+2…+33 mm). Net max|err| is lower at height (A 283→199, B 232→180), but this is which tag-pair happens to project onto the layout's weak vertical axis — a **geometry artifact, not an accuracy improvement** (per the standing ruling: don't treat the wand caliper as a pass/fail gate at this layout-RMS scale).
6. **Listener / AGC identical.** LE (common node) 0.05 dB swing both runs. The high-run fleet's larger swings (LB/LF/L955A 0.37–0.43 dB, env-drift 7.6–11.6 %) are the **walking**, not power — the AGC-flat listeners stay at 0.05 dB.
7. **Conditions differ:** center ran static/unattended (env stable, 0.6 % drift); high had the operator **walking** (0.9 % movement duty, 30 events, worst POR round-1). Light enough that pooled metrics are unaffected, and timestamped so any borderline cell can be checked against the movement timeline.

**Bottom line:** raising the wand changed the *geometry* (which baseline/axis is weak, which tag scatters most, a correctly-signed but non-rigid z shift) and **nothing about the power behaviour** — power stays invisible end-to-end. Links are as healthy raised as centered.