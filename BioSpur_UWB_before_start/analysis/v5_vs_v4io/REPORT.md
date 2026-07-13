# V5 vs V4-IO — AutoPos layout + wand positioning + Geiger LOO

**Pure offline.** CPU 12 cores, wall-clock 10.4 s. V5 = scale-lock variant (deployable) unless noted; V5-unlocked shown for contrast only.

## 1. Layout: V4-IO vs V5 (scale-lock)

| anchor | V4-IO xyz (mm) | V5 xyz (mm) | Δpos | V4 d | V5 d | V5 at ±60? |
|---|---|---|---|---|---|---|
| A | [0.0, 0.0, 0.0] | [0.0, 0.0, 0.0] | 0.0 | 0.0 | 0.0 |  |
| B | [4712.8, 0.0, 0.0] | [4696.9, 0.0, 0.0] | 15.9 | 12.8 | 26.5 |  |
| C | [4427.6, 3031.5, 0.0] | [4438.9, 3020.3, 0.0] | 16.0 | 60.0 | 60.0 | YES |
| D | [265.5, 2853.1, -231.1] | [306.7, 2817.4, -242.5] | 55.7 | 30.9 | 60.0 | YES |
| E | [601.7, -230.9, -1568.9] | [608.6, -209.5, -1547.1] | 31.3 | 18.6 | 39.1 |  |
| F | [4740.4, 94.2, -1484.2] | [4714.9, 80.5, -1457.8] | 39.2 | 13.0 | 27.2 |  |
| G | [4485.1, 3199.5, -1484.2] | [4474.5, 3143.0, -1465.8] | 60.4 | 31.6 | 60.0 | YES |
| H | [570.7, 2552.5, -1779.0] | [579.4, 2553.6, -1774.1] | 10.0 | 60.0 | 60.0 | YES |

- **inter-anchor pair RMS:** V4-IO **105.76** mm → V5 scale-lock **100.9** mm (V5 unlocked 94.65 mm).
- **layout change from V4-IO:** V5 scale-lock max **60.4** mm (unlocked 534.2 mm).
- **Do C/H come off the +60 mm bound?** With **scale-lock: NO** — C/H (and D/G) are re-clipped at +60 by the re-imposed box (that is the price of keeping the layout ≈ V4-IO). Only the **unlocked** V5 frees them (C=+3, H=−19 differential), but it moves the layout ~534 mm (scale unidentifiable) and is not deployable.

## 2. Wand tag positions (median TR ranges; delays applied in the cost function)
(obs: BSCCF4=8352, BS9336=8191, BS955A=8371. No-delay solve reproduces deployed wand_positions.json to {'BSCCF4': 0.0, 'BS9336': 0.0, 'BS955A': 0.0} mm.)

| wand | V4-IO+delay (mm) | V5slock+delay (mm) | Δ mm | V5slock RMS |
|---|---|---|---|---|
| BSCCF4 | [2729.2, 1027.7, -876.3] | [2730.8, 1003.0, -886.8] | 26.9 | 130.4 |
| BS9336 | [2840.4, 375.4, -1039.4] | [2828.1, 359.7, -1014.1] | 32.2 | 111.8 |
| BS955A | [2779.7, 984.5, -511.7] | [2771.2, 974.1, -483.3] | 31.4 | 118.0 |

## 3. Comparison + caliper cross-check

| metric | V4-IO | V5 (scale-lock) |
|---|---|---|
| inter-anchor pair RMS mm | 105.76 | 100.9 |
| C delay mm | 60.0 | 60.0 |
| H delay mm | 60.0 | 60.0 |
| wand BSCCF4 pos | [2729.2, 1027.7, -876.3] | [2730.8, 1003.0, -886.8] |
| wand BS9336 pos | [2840.4, 375.4, -1039.4] | [2828.1, 359.7, -1014.1] |
| wand BS955A pos | [2779.7, 984.5, -511.7] | [2771.2, 974.1, -483.3] |

**Caliper cross-check** (rigid-wand truth 670/660/709 mm, tol ±50 mm):

| pair | truth | V4IO nodelay (deployed) | V4IO+delay | V5slock+delay | V5unlock+delay |
|---|---|---|---|---|---|
| CCF4_9336 | 670.0 | 684.1 (14.1, PASS) | 681.5 (11.5, PASS) | 663.0 (-7.0, PASS) | 590.4 (-79.6, FAIL) |
| CCF4_955A | 660.0 | 497.4 (-162.6, FAIL) | 370.6 (-289.4, FAIL) | 406.5 (-253.5, FAIL) | 540.6 (-119.4, FAIL) |
| 9336_955A | 709.0 | 878.8 (169.8, FAIL) | 808.2 (99.2, FAIL) | 813.9 (104.9, FAIL) | 738.0 (29.0, PASS) |

Caliper pairs passing (of 3): V4IO nodelay **1**, V4IO+delay **1**, V5slock+delay **1**, V5unlock+delay **1**.

**Does V5 fix the caliper failure?** **No.** Every configuration passes exactly **1/3**. The failure is a triangle **shape** distortion, not a uniform scale error: CCF4–955A comes out ~160 mm too short while 9336–955A comes out ~170 mm too long. That is inherited from the **per-wand solve RMS (~110–130 mm)** — each wand position is uncertain at that level, so an inter-tag distance carries ~150–200 mm of error, well above the 50 mm caliper tolerance. Scale-lock V5 does not reduce the wand RMS (its layout ≈ V4-IO), and per-anchor delays only shuffle the error around — they even make CCF4–955A WORSE (−163→−289 mm). Rescaling (unlocked V5) flips which single pair passes (9336–955A) while breaking another, still 1/3. Fixing the caliper needs lower per-wand position error (better ranging / geometry) or an external metric constraint fed INTO the solve — not achievable by V5's marginal layout change.

## 4. Geiger baseline walk — LOO median |residual|

| config | LOO median mm |
|---|---|
| V4-IO (no delays) | 158.7 |
| V4-IO + delays | 150.7 |
| V5 scale-lock + delays | 146.2 |

(over 517 Geiger frames; the Geiger tag ranges 8 anchors, wand tags are separate.)

## Bottom line
- **Scale-lock V5 is ≈ V4-IO** for deployment: layout within ~60 mm, pair RMS marginally better (105.76→100.9 mm), C/H still clipped at +60. Wand positions move only a few mm.
- **The caliper failure is NOT fixed** by V5 (all configs 1/3) — it reflects ~120 mm per-wand position uncertainty (a triangle shape distortion), not something V5's marginal layout change touches. Delays even worsen one pair.
- **Delays help the Geiger LOO** modestly (see table), consistent with the earlier per-anchor delay finding (C/E benefit most).
- The unlocked V5 (C/H off-bound, better pair RMS) remains scientifically informative but **not deployable** (534 mm layout move, scale unidentifiable).