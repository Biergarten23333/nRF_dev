# Per-tag antenna delay (d_tag) co-estimation — validation

**Pure offline.** 12 cores, 9.2 s, 24914 TR rows (`system_calibration_20260710_233443`).

- Solver: **pg_lib.solve_pos (LM, plain -- no Huber/temporal prior)** (U5-analysis path, `pg_lib.solve_pos`)
- Layout: **V4-io + per-anchor delays**  ·  init x0 = anchor centroid [2475.5, 1437.5, -818.4]
- d_tag prior sigma: **300.0 mm** (APS014 uncalibrated 3-sigma)

> Every column below uses the **same** LM solver; 3-unk vs 4-unk differ only by the d_tag unknown. The plain-LM baseline is NOT the production T4 C-solver — the T4-C numbers (RMS 132.7/118.1/123.1, caliper 708/336/795) are carried as a reference row for context.

## Step 1 — per-tag d_tag (4-unknown, per frame)

| tag | n_frames | d_tag median mm | d_tag std mm | d_tag IQR mm |
|---|---|---|---|---|
| BSCCF4 | 1056 | +12.1 | 17.0 | 19.1 |
| BS9336 | 1034 | -11.4 | 18.7 | 21.0 |
| BS955A | 1061 | +31.6 | 16.6 | 17.2 |

**d_tag spread across tags = 43.0 mm.** Positive d_tag = tag reads LONG (antenna delay adds range). Expectation was ~100–300 mm spread to explain the caliper miss.

## Step 3 — caliper cross-check (truth 670 / 660 / 709 mm, tol +-50 mm)

| pair | truth | baseline 3-unk | A per-frame | B batch d_tag | T4-C ref |
|---|---|---|---|---|---|
| CCF4–9336 | 670 | 687.4 (+17.4, PASS) | 702.9 (+32.9, PASS) | 703.0 (+33, PASS) | 708.1 (+38.1) |
| CCF4–955A | 660 | 257.1 (-402.9, FAIL) | 227.1 (-432.9, FAIL) | 228.8 (-431.2, FAIL) | 336.3 (-323.7) |
| 9336–955A | 709 | 642.0 (-67, FAIL) | 651.7 (-57.3, FAIL) | 653.7 (-55.3, FAIL) | 795.0 (+86) |

**PASS count — baseline 3-unk 1/3 · A per-frame 1/3 · B batch 1/3 · T4-C ref 1/3.**

## Step 4 — per-wand solve RMS and position scatter

| tag | 3-unk RMS | 4-unk RMS | 3-unk scatter | 4-unk scatter | (T4-C RMS / scatter ref) |
|---|---|---|---|---|---|
| BSCCF4 | 124.4 | 123.3 | 49.5 | 48.0 | 132.7 / 65.3 |
| BS9336 | 105.7 | 104.9 | 44.8 | 47.3 | 118.1 / 59.0 |
| BS955A | 116.3 | 112.1 | 48.4 | 45.7 | 123.1 / 55.5 |

## Static positions (median over frames)

| tag | baseline 3-unk (mm) | A per-frame 4-unk (mm) | B batch d_tag (mm) | batch d_tag mm |
|---|---|---|---|---|
| BSCCF4 | [2776.4, 1050.1, -813.2] | [2775.8, 1054.8, -813.7] | [2775.4, 1054.5, -814.3] | +12.1 |
| BS9336 | [2801.2, 365.6, -871.4] | [2802.0, 354.8, -871.6] | [2802.1, 354.5, -873.3] | -11.4 |
| BS955A | [2797.9, 938.9, -582.4] | [2796.4, 952.5, -612.0] | [2796.0, 952.9, -610.3] | +31.6 |

## Verdict — per-tag delay is NOT the caliper root cause

1. **d_tag spread across the three tags is only 43 mm** (BSCCF4 +12, BS9336 -11, BS955A +32), each with std ~17-19 mm. The tags are within noise of one another. The hypothesis needed ~100-300 mm of spread; the data shows ~40 mm.

2. **Co-estimating d_tag barely moves the positions** (max shift 33 mm, almost all in BS955A's z) and **does not recover the caliper** — still 1/3 for the 3-unk baseline, per-frame A, and batch B alike. RMS drops only ~1-4 mm because there is almost no common-mode residual to absorb.

3. The caliper miss is dominated by **CCF4–955A = 257 mm vs 660 truth (-403)** — the two tags solve ~400 mm too CLOSE. A common-mode d_tag shifts a tag radially toward/away from the anchor cluster; it cannot open or close a relative inter-tag distance that is wrong by ~400 mm when the per-tag d_tag differences are only tens of mm. This is a per-tag **position** error (least-constrained z axis + layout geometry), not a per-tag range offset.

4. The plain-LM baseline caliper pattern (CCF4–955A −403, 9336–955A −67) differs materially from the T4-C reference (−324, +86). The caliper is highly sensitive to solver internals (robust loss, z-DOP), which again points at geometry / the z axis, not tag delay.

**Recommendation.** Keep the 4-unknown solver as an opt-in safety net (`estimate_d_tag=False` default preserves exact V4-IO/T4 behavior) — it is cheap and correct, and will earn its keep on a tag with a genuinely miscalibrated antenna. But per-tag delay is not the lever for THIS caliper failure. The lever is the layout / z-constraint (metric scale-lock, caliper-as-constraint) or a true per-tag calibration measured against a known-distance jig — not a delay co-estimated from this poor-z-DOP wand data.

> **The solver is not broken — the data has no large d_tag.** `synthetic_recovery_test.py` injects a known common-mode offset into synthetic frames: the 4-unknown solve recovers +150 mm as d_tag = +149.5 (std 9.2) with position error unchanged (~28 mm), while the 3-unknown solve smears the same +150 mm into 226 mm of position error. The machinery works; the wand tags simply differ by only ~40 mm.

