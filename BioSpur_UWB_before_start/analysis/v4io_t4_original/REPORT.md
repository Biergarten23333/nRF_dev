# Original V4-IO + T4 on system_calibration_20260710_233443

**Solver:** ORIGINAL V4-IO layout + ORIGINAL T4 (pristine build from git HEAD).  
CPU 12 cores, 0.3 s, 25784 TR rows. T4 note: T4 = adaptive: full-anchor frames -> T1 C least-squares, low-redundancy -> T3 EMA.

Layout anchor delays (mm) from the V4-IO `anchor_layout.json`: {0: 0.0, 1: 12.8, 2: 60.0, 3: 30.9, 4: 18.6, 5: 13.0, 6: 31.6, 7: 60.0}

## Wand positions

| wand | ORIGINAL T4 (mm) | deployed ref (Huber, Task 4) | Δ mm | T4 solve RMS | T4 scatter |
|---|---|---|---|---|---|
| BSCCF4 | [2731.3, 1055.3, -840.2] | [2718.5, 980.9, -934.1] | 120.5 | 132.7 | 65.3 |
| BS9336 | [2839.9, 379.0, -1019.6] | [2830.8, 314.9, -1043.1] | 68.9 | 118.1 | 59.0 |
| BS955A | [2776.1, 988.8, -513.6] | [2792.2, 955.6, -442.8] | 79.8 | 123.1 | 55.5 |

## Caliper (truth 670 / 660 / 709 mm, tol ±50 mm)

| pair | truth | ORIGINAL T4 | deployed ref (Task 4) |
|---|---|---|---|
| CCF4–9336 | 670.0 | 708.1 (+38.1, PASS) | 684.1 (+14.1, PASS) |
| CCF4–955A | 660.0 | 336.3 (-323.7, FAIL) | 497.4 (-162.6, FAIL) |
| 9336–955A | 709.0 | 795.0 (+86, FAIL) | 878.8 (+169.8, FAIL) |

**Caliper pass: ORIGINAL T4 1/3, deployed ref 1/3.**

