# Position-High Power Sweep — Report

**2026-07-15** · wand **raised** (vs the room-center run), DIAG OFF, anchors locked MAX,
only tag TX swept via runtime TXPWR. Interleaved rounds [MAX, M3, M6, M12, POR] × 3 min.
**3 loops**, 15 cells (1 excluded — see below). **All 7 listeners** recording @460800.

> ⚠ **Environment was NOT static:** the operator was **walking in the room during capture**
> (see `CONDITIONS.md`). Movement was detected and quantified (§Movement) so it can be
> attributed rather than assumed away. This is the key difference in *conditions* vs the
> static overnight center run.

> **Dead cell:** round-1 MAX collided with a leftover sweep still holding the master port
> (since killed) → 0 rows, **excluded** from pooled stats. MAX still has 2 clean cells.

## Headline

At the raised position, **TX power again has essentially zero effect** on link success,
bias, or positioning across the full 8.5 dB range — same as center. ge7 is flat at 0.978,
positioning precision ≈ 48 mm at every power. The rig is just as healthy raised as centered.

## B. Link success vs TX power

| preset | dB | cells | ge7 | ge8 | valid% |
|---|---|---|---|---|---|
| MAX | 8.5 | 2 | 0.9778 | 0.9405 | 97.3 |
| M3  | 5.5 | 3 | 0.9756 | 0.9381 | 97.1 |
| M6  | 2.5 | 3 | 0.9779 | 0.9385 | 97.3 |
| M12 | 0.0 | 3 | 0.9779 | 0.9391 | 97.3 |
| POR | 4.0 | 3 | 0.9778 | 0.9366 | 97.3 |

**Verdict:** flat — ge7 0.978 / valid 97.3 % at every power, incl. M12 (register floor).
(M3 0.9756 is fractionally low because one of its cells is the collision-shortened 345-row
partial; well within noise.) Raising the wand did **not** degrade link success.

## A. Bias vs TX power

- Median per-link swing across the 5 powers: **18.9 mm** (max 39.7). Within per-link range
  noise → no systematic power→bias. Matches center (17.7 mm).

## C. Temperature coefficient — not measurable (as center)

Chip temp span **1.0 °C** all run (raw ~121–122 LSB). < 1 °C stimulus → coefficient not
extractable (the 6.66 mm/°C fit is spurious over a 1 °C span). Needs a real thermal cycle.

## Movement (7-listener coincidence detector)

The fleet doubles as a passive motion sensor: a real person perturbs *several* listeners'
`cir_pwr` coincidentally in time. Result:

- **30 events, duty 0.9 %** (≈ 32 s of movement in 62 min). Light and sporadic — **no cell
  is movement-dominated.**
- Most-contaminated cells (movement fraction): **POR round-1 (09:44–09:46) = 3.0 %**,
  POR round-3 = 2.0 %, M6/M12 ≈ 1.5 %. Everything else < 1 %.
- Timeline written to `movement_events.json` (per-event start / duration / listeners / cell).

**Verdict:** the walking is real and localized in time, but so brief that pooled metrics are
essentially unaffected. Where a cell looks slightly worse (e.g. POR/M3 scatter), the movement
timeline lets us check it's the walk, not power or height.

## E. Lock events + elevation (is "0 locks" a pass or a null test?)

**0** cell-links with sustained >150 mm offset or >200 mm std — none. But "0 locks" only
means "Layer-2 immune" if the geometry actually produced **steep** links (the Erlangen study:
discrete reflection locks appear at **>30° elevation**, elevation being the driver, ρ=0.54).
So I computed the per-link elevation = asin(|Δz|/range) — invariant to the layout's global
z-flip — for both runs (`elevation_analysis.py` → `elevation_analysis.json`):

| run | max elev | median | links ≥30° | links ≥37° | locks |
|---|---|---|---|---|---|
| CENTER | **27.3°** | 17.0° | 0/24 | 0/24 | 0 |
| HIGH   | **41.0°** | 14.8° | 3/24 | 1/24 | 0 |

- Anchor layout is two layers: A/B/C/D ≈ z0, E/F/G/H ≈ z−1500…−1780 (~1.8 m span).
- **Center never entered the steep regime** (max 27.3°) → its "0 locks" was a *null test*,
  not a pass. Raising the wand pushed it ~1500 mm below the z≈0 anchors (A/B/C) while staying
  horizontally near B → genuinely steep links: BS9336→B **41.0°**, BSCCF4→B 36.0°.
- **HIGH reached 41°, past both 30° and 37°, with 0 locks** → for this run "0 locks" is a
  *real* Layer-2-immunity result in the steep regime, not virtual.

**Caveat (real but thin):** only **3 links ≥30° and 1 ≥37°, all to anchor B**, and elevation
carries ±a few° from the weak z. This is "immune up to ~41° on the steep links sampled," not
an exhaustive steep-angle stress test. To firm it up, use a wand position that puts *several*
anchors into ≥37° at once (raise further / shift so B, C and a mid anchor are all steep).

## F. 7-listener cross-analysis — does the fleet see the power sweep? No.

cir_pwr median per power, dB relative to that listener's MAX (tag TX swings 8.5 dB):

| listener | MAX | M3 | M6 | M12 | POR | max\|swing\| | env drift% |
|---|---|---|---|---|---|---|---|
| LB    | 0.00 | +0.17 | +0.32 | +0.09 | +0.37 | 0.37 | 7.6 |
| LE    | 0.00 | +0.03 | +0.00 | −0.05 | +0.03 | **0.05** | 1.1 |
| LF    | 0.00 | +0.19 | +0.30 | +0.13 | +0.41 | 0.41 | 7.6 |
| LA    | 0.00 | +0.03 | +0.07 | +0.06 | +0.06 | 0.07 | 1.1 |
| LCCF4 | 0.00 | −0.05 | +0.05 | +0.04 | +0.14 | 0.14 | 2.9 |
| L9336 | 0.00 | +0.12 | +0.21 | −0.00 | +0.23 | 0.23 | 6.0 |
| L955A | 0.00 | +0.04 | +0.23 | −0.06 | +0.43 | **0.43** | 11.6 |

**Verdict:** an 8.5 dB TX stimulus → **≤ 0.43 dB** response at any listener (LE = 0.05 dB,
identical to the center run's single-listener finding). **AGC fully normalizes power
end-to-end.** The listeners with the largest residual swing (LB/LF/L955A, 0.37–0.43) are also
the ones with the highest env-drift (7.6–11.6 %) — that tracks the **walking**, not a power
response; the AGC-flat links (LE/LA) sit at 0.05–0.07 dB.

## G. Positioning vs power (offline re-solve, both production pipelines)

**A = V4io + T4**, **B = V5 + U5**. 3 clean cells/power. (Caliper truth 670/660/709 mm.)

### Precision (pooled median across powers)

| pipeline | 3D scatter | z-std | caliper max\|err\| |
|---|---|---|---|
| A V4io+T4 | 48.4 mm | 46.7 mm | 198.8 mm |
| B V5+U5   | 49.5 mm | 46.9 mm | 179.9 mm |

Flat vs power (non-monotonic), just like center. z (the weak axis) is equally flat.

### Per-tag 3D-scatter RMS vs power (mm)

**A — V4io+T4**

| power | CCF4 | 9336 | 955A |
|---|---|---|---|
| MAX | 50.1 | 48.4 | 42.3 |
| M3  | 55.6 | 54.7 | 41.3 |
| M6  | 53.7 | 49.0 | 41.2 |
| M12 | 51.5 | 46.9 | 41.4 |
| POR | 53.2 | 46.5 | 41.6 |
| **swing** | **5.5** | **8.2** | **1.1** |

**B — V5+U5**

| power | CCF4 | 9336 | 955A |
|---|---|---|---|
| MAX | 49.8 | 49.5 | 43.1 |
| M3  | 56.0 | 54.1 | 42.2 |
| M6  | 53.5 | 49.7 | 41.7 |
| M12 | 50.9 | 47.4 | 41.6 |
| POR | 52.5 | 47.0 | 41.8 |
| **swing** | **6.2** | **7.1** | **1.5** |

Every tag flat vs power (swing 1–8 mm, non-monotonic). At this height BS955A is tightest
(~41 mm), CCF4 loosest (~50–56) — the opposite of center, where BS9336 was loosest and CCF4
tightest → tag ordering is set by geometry, not power.

### Rigid-baseline (caliper) vs power

| power | A: CCF4-9336 / CCF4-955A / 9336-955A | A max | B: CCF4-9336 / CCF4-955A / 9336-955A | B max |
|---|---|---|---|---|
| MAX | +33 / +23 / +199 | 199 | +36 / +33 / +180 | 180 |
| M3  | +42 / +20 / +180 | 180 | +47 / +32 / +166 | 166 |
| M6  | +46 / −6 / +194 | 194 | +50 / +7 / +180 | 180 |
| M12 | +37 / +10 / +202 | 202 | +41 / +21 / +187 | 187 |
| POR | +50 / +2 / +208 | 208 | +54 / +15 / +192 | 192 |

Power-independent. Note the **worst baseline moved** from CCF4–955A (center) to **9336–955A**
(here): raising the wand rotated which tag-pair projects onto the weakly-constrained vertical
axis. Per the standing ruling this is a geometry artifact of the layout's z-weakness, not a
real accuracy change.

## Bottom line

- **Power:** invisible at the raised position too — ge7 0.978, bias ≤19 mm, positioning ≈48 mm,
  listener ≤0.43 dB, all flat across 8.5 dB. AGC normalizes power end-to-end.
- **Height:** did not hurt anything (ge7 identical to center). Crucially it **raised the
  steepest link 27°→41°** — center never reached the ≥30° Layer-2 regime, so the raise is what
  finally *tested* it: 3 links ≥30° (1 ≥37°), **0 locks** = real (if thin) Layer-2 immunity.
  It also shifted the positioning geometry (which caliper baseline is worst; which tag
  scatters most) — expected.
- **Walking:** detected (0.9 % duty, 30 events, worst POR round-1); too brief to move the
  pooled results, and timestamped for attribution.
- **Solver:** V5+U5 again ~20 mm tighter on the worst baseline than V4io+T4 (180 vs 199).

Data: `round_*/{level}_3min_*/`, `results.json`, `listener7_results.json`,
`movement_events.json`, `positioning_v5u5_v4iot4/positioning_vs_power.json`.
Comparison vs center: `COMPARISON.md`.
