# V5 + U5  vs  V4-io + T4  — wand positioning (system_calibration_20260710_233443)

**Pure offline.** 12 cores, 1.1 s, 25784 TR rows.

- **A = V4-io layout + T4 (original, pristine git HEAD)**
- **B = V5 scale-lock layout + U5 (working tree; RF-sigma inert w/o FP-SNR -> uniform25 + never-drop)**

> Note: the wand TR log carries no FP-SNR, so **U5's RF-informed σ is inert here** — U5 reduces to uniform-25 mm σ + never-drop-anchor on the V5 layout. The A↔B difference is therefore driven by the **V5-vs-V4-io layout** and the never-drop behavior, not the RF metric.

## Wand positions

| wand | A: V4-io+T4 (mm) | B: V5+U5 (mm) | Δ A↔B | A rms | B rms | A scatter | B scatter |
|---|---|---|---|---|---|---|---|
| BSCCF4 | [2731.3, 1055.3, -840.2] | [2732.8, 1018.5, -859.1] | 41.4 | 132.7 | 131.1 | 65.3 | 63.9 |
| BS9336 | [2839.9, 379.0, -1019.6] | [2828.7, 361.2, -997.7] | 30.4 | 118.1 | 115.0 | 59.0 | 59.9 |
| BS955A | [2776.1, 988.8, -513.6] | [2771.1, 974.7, -486.2] | 31.2 | 123.1 | 121.4 | 55.5 | 54.0 |

## Caliper (truth 670 / 660 / 709 mm, tol ±50 mm)

| pair | truth | A: V4-io+T4 | B: V5+U5 |
|---|---|---|---|
| CCF4–9336 | 670 | 708.1 (+38.1, PASS) | 678.6 (+8.6, PASS) |
| CCF4–955A | 660 | 336.3 (-323.7, FAIL) | 377.4 (-282.6, FAIL) |
| 9336–955A | 709 | 795.0 (+86, FAIL) | 800.8 (+91.8, FAIL) |

**Caliper pass: A (V4-io+T4) 1/3, B (V5+U5) 1/3.**

## Layout parameters used

- A delays (mm): {'0': 0.0, '1': 12.8, '2': 60.0, '3': 30.9, '4': 18.6, '5': 13.0, '6': 31.6, '7': 60.0}  ·  σ: 50.0 uniform
- B delays (mm): {'0': 0.0, '1': 26.5, '2': 60.0, '3': 60.0, '4': 39.1, '5': 27.2, '6': 60.0, '7': 60.0}  ·  σ: 25.0 uniform (anchor_sigma.json)

