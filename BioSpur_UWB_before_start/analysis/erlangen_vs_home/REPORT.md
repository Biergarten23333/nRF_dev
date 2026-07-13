# Erlangen (Vicon-validated firmware) vs Home (current firmware) — offline

**Pure offline — no flashing.** CPU 12 cores, 0.8 s. The Erlangen 2026-05-28 captures were recorded with the exact firmware validated against Vicon at MaD Lab, so solving them offline reproduces that firmware's output directly — nothing needs to be re-flashed.
Sanity: the home V4-IO re-solve matches the deployed `anchor_layout.json` to **0.0 mm** (identical production solver).

## 1. Anchor layout fit (classic V4-IO, each room's own sweep)

| | Erlangen (05-28) | Home (current) |
|---|---|---|
| inter-anchor pair-fit RMS | **48.04 mm** | **108.91 mm** |
| pairs | 28 | 28 |

The Erlangen room solves ~2× tighter (48 vs 109 mm) — a bigger, cleaner volume with no B/E multipath/step events. Positions are each in their own gauge, so the caliper below is the cross-room metric.

## 2. Wand-tag ranging quality at Erlangen (data limitation)

Per-tag anchor coverage (a tag needs ≥4 anchors to be positioned):

| capture | BSCCF4 | BS9336 | BS955A |
|---|---|---|---|
| Erlangen W00-test | 8 | 8 | 8 |
| Erlangen W01 | 8 | 2 | 8 |
| Erlangen W02 | 8 | 3 | 0 |
| Home | 8 | 8 | 8 |

**BS9336 ranged too poorly at Erlangen** (2–3 anchors — the documented BS9336/BS955A range collapse), so a full 3-tag triangle is not recoverable from any single Erlangen capture. Only **CCF4–955A** (both 8-anchor in W01) is cleanly measurable there.

## 3. Rigid-wand caliper (truth 670 / 660 / 709 mm, tol ±50 mm; no-delay = as deployed)

| config | CCF4–9336 (670) | CCF4–955A (660) | 9336–955A (709) | pass/3 |
| --- | --- | --- | --- | --- |
| Erlangen W00-test | 830.3 (+160.3, FAIL) | 860.5 (+200.5, FAIL) | 473.4 (-235.6, FAIL) | 0 |
| Erlangen W01 | - | 763.2 (+103.2, FAIL) | - | 0 |
| Erlangen W02 | - | - | - | 0 |
| Home | 684.1 (+14.1, PASS) | 497.4 (-162.6, FAIL) | 878.8 (+169.8, FAIL) | 1 |

### Per-wand solve RMS (no-delay, mm) — how well each tag fits its layout

| wand | W00-test | W01 | W02 | Home |
|---|---|---|---|---|
| BSCCF4 | 214.9 | 157.1 | 183.6 | 149.5 |
| BS9336 | 159.4 | None | None | 120.1 |
| BS955A | 119.5 | 79.1 | None | 129.8 |

## 4. Bottom line

- **Erlangen fails the caliper as badly as (or worse than) home.** The one Erlangen capture with full 8-anchor coverage on all three tags (W00-test) reconstructs the triangle **0/3** (pairs off +160 / +200 / −236 mm); the first real capture W01 has one clean pair (CCF4–955A) and it fails too (+103). Home passes **1/3**. So the wand triangle is **not** better in the Vicon-validated room.
- **Per-tag solve residual is ~80–160 mm even against Erlangen's clean 48 mm layout** — i.e. the wand error is set by *tag ranging* (likely an unmodeled tag-side antenna delay + orientation/body shadowing), not by the anchor layout. BSCCF4 residual is ~150 mm in **both** rooms.
- **Conclusion:** the wand-triangle caliper failure is **intrinsic to the wand ranging + multilateration, present even in the Vicon-validated Erlangen setup** — it is *not* a home-specific firmware or environment regression. The cleaner home firmware/layout would not fix it; it needs tag-side delay calibration or a metric constraint fed into the wand solve.
- **Caveat:** BS9336's Erlangen range collapse limits the Erlangen triangle to one pair; the conclusion rests on CCF4–955A plus the per-tag residuals, not a full 3/3 Erlangen caliper.

