# Wand positioning vs TX power — V4io+T4 vs V5+U5 (overnight sweep, offline re-solve)

Pure offline re-solve of the overnight power sweep (~20 cells/power, wand fixed) with
**both production pipelines**, no firmware, no new data:
- **A = V4-io layout + T4** (pristine package)
- **B = V5 scale-lock layout + U5** (working package + `anchor_sigma.json` uniform-25 + never-drop;
  RF-σ inert — wand TR carries no FP-SNR)

Layouts: `logs/system_calibration_20260710_233443/{anchor_layout.json, anchor_layout_v5_scalelock.json, anchor_sigma.json}`.
Truth baselines: CCF4–9336 = 670, CCF4–955A = 660, 9336–955A = 709 mm.

## Precision + drift vs power

| power (dB) | A 3D-scatter | A z-std | A drift·vsMAX | B 3D-scatter | B z-std | B drift·vsMAX |
|---|---|---|---|---|---|---|
| MAX (8.5) | 50.8 | 51.7 | 0 | 51.1 | 51.1 | 0 |
| M3 (5.5) | 50.7 | 50.7 | 17 | 51.1 | 50.5 | 17 |
| M6 (2.5) | 51.3 | 51.9 | 34 | 51.6 | 51.4 | 33 |
| M12 (0) | 50.5 | 50.7 | 10 | 50.8 | 50.2 | 10 |
| POR (4.0) | 51.6 | 52.9 | 32 | 52.1 | 52.6 | 31 |

(all mm)

## Per-tag precision (3D-scatter RMS) vs power

**A — V4io+T4** (mm)

| power (dB) | BSCCF4 | BS9336 | BS955A |
|---|---|---|---|
| MAX (8.5) | 47.6 | 56.5 | 50.8 |
| M3 (5.5) | 43.2 | 55.8 | 50.7 |
| M6 (2.5) | 41.6 | 57.2 | 51.3 |
| M12 (0) | 42.7 | 55.5 | 50.5 |
| POR (4.0) | 42.4 | 59.9 | 51.6 |
| **swing** | **6.0** | **4.4** | **1.1** |

**B — V5+U5** (mm)

| power (dB) | BSCCF4 | BS9336 | BS955A |
|---|---|---|---|
| MAX (8.5) | 50.3 | 55.7 | 51.1 |
| M3 (5.5) | 45.0 | 54.4 | 51.1 |
| M6 (2.5) | 43.0 | 55.8 | 51.6 |
| M12 (0) | 44.9 | 55.0 | 50.8 |
| POR (4.0) | 43.9 | 59.1 | 52.1 |
| **swing** | **7.3** | **4.7** | **1.3** |

Per-tag drift vs MAX (mean-position wander, mm), A/B similar: BSCCF4 ≈ 38–46 (largest),
BS9336 ≈ 10–20, BS955A ≈ 4–34 (M6 outlier) — all non-monotonic with power.

**Per-tag verdict:**
1. **Every tag is flat vs power** — RMS swing across the full 8.5 dB is only 1–7 mm
   (BS955A 1.1, BS9336 4.4, BSCCF4 6.0), non-monotonic. No tag shows a power trend; BSCCF4 is
   even slightly *tighter* at low power (counter to "less power = worse" → noise, not a power effect).
2. **Tags differ from each other by geometry, not power:** BS9336 loosest (~56 mm), BSCCF4
   tightest (~43–48 mm), BS955A middle (~51 mm) — consistent across all powers and both solvers.
3. **BSCCF4 drifts most** (~40 mm mean-position wander vs MAX), BS955A least — per-tag,
   non-monotonic → time-window repeatability, not power.

## Rigid-baseline (caliper) error vs power

| power | A: CCF4-9336 / CCF4-955A / 9336-955A | A max\|err\| | B: CCF4-9336 / CCF4-955A / 9336-955A | B max\|err\| |
|---|---|---|---|---|
| MAX | −40 / −251 / +46 | 251 | −31 / −196 / +59 | 196 |
| M3  | −59 / −282 / +53 | 282 | −54 / −231 / +66 | 231 |
| M6  | −53 / −304 / +57 | 304 | −48 / −254 / +68 | 254 |
| M12 | −60 / −283 / +47 | 283 | −54 / −232 / +59 | 232 |
| POR | −43 / −302 / +50 | 302 | −37 / −251 / +60 | 251 |

(all mm)

## Verdict

1. **Positioning precision is power-insensitive.** 3D scatter ≈ 51 mm and z-std ≈ 51 mm at
   every power, both solvers. The 8.5 dB sweep does not change positioning jitter — and z
   (the weak axis, where a power effect would surface first) is equally flat.
2. **Mean-position drift across powers is ~10–34 mm and non-monotonic** (M6/POR high, M12 low —
   not ordered by dB) → time-window repeatability (~30 mm, within the precision floor), not a
   power effect.
3. **The dominant error is the known caliper/geometry problem, not power.** CCF4–955A is off by
   ~200–300 mm at *all* powers (truth 660) — identical at MAX and M12. Power doesn't touch it.
4. **V5+U5 is ~50 mm tighter than V4io+T4 on the worst baseline at every power** (196–254 vs
   251–304 mm) — a real, power-independent solver/layout gain.

**Bottom line:** across ranging (ge7 flat), listener amplitude (<0.1 dB), and now full
positioning (precision + baseline + z, both production solvers), the 8.5 dB power sweep changes
nothing at this strong-link position — AGC normalizes it end-to-end. The only mover is the
**solver** (V5+U5 ~50 mm tighter baseline), not the power.

Data: `positioning_vs_power.json`. Driver: `analysis/v5u5_vs_v4iot4/overnight_power_positioning.py`
(+ `worker_ext.py`).
