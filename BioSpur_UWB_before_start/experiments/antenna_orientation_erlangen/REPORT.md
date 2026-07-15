# Erlangen Antenna-Orientation Analysis — ID13–ID24 (tag BSF66F)

**Session:** `erlangen_20260528_optitrack` (Vicon ground truth, 2026-05-28) · 8 anchors A–H · tag BSF66F rotated in yaw at 3 heights · 4 orientations each.

**Generated:** `experiments/antenna_orientation_erlangen/analyze.py` → `results.json` (runtime 30.7s, single core; peak RSS ~108 MB; 12-core host).

## TL;DR — Verdict

- **Antenna orientation has a MAJOR effect on measured range.** Geometry-corrected (bias) orientation delta **RMS = 128.4 mm** (raw/confounded RMS = 144.9 mm); threshold for MAJOR is 30 mm.

- Present at **every height** (bias per-height RMS: mid 51.5, low 139.3, high 165.5 mm) and on **all 8 anchors** (cosine amplitude 38–96 mm, every anchor > 20 mm).

- **Two caveats that reshape the naive reading:** (1) the tag is *not* a fixed point across orientations at **low height** — it physically moved **147–222 mm** between ID17→18/19/20 (mid/high moved ≤22 mm); the geometry-corrected *bias* metric removes this. (2) The per-anchor effect does **not** follow a clean far-field cosine keyed to anchor geometry (fitted phase does not track anchor azimuth), so it is an orientation-dependent range effect — antenna directionality **plus** phase-center rotation **plus** orientation-dependent multipath — not a textbook radiation pattern.

- **Wand caliper:** worst-case CCF4-vs-955A per-anchor bias split ≈ 2×amplitude ≈ **116.8 mm** (mean) / **192.5 mm** (worst anchor) → explains ~36%–59% of the observed 324 mm CCF4–955A failure. Major contributor, not the whole story.

## Task 1 — Raw data location

**One capture directory per ID** (not a combined file). UWB two-way-ranging (TR) and Vicon are stored separately:

| stream | path pattern | format |
|---|---|---|
| UWB TR | `.../captures/erlangen_20260528_optitrack/static_ID{n}_BSF66F_120s_*/tag_capture_*/BSF66F/tr.csv` | CSV, one row per (sweep, anchor_id 0–7); cols `range_mm`,`valid`,`status`(O/T) |
| Vicon | `autopos_pipeline/28052026_Erlangen_Official/opti_captures/full/ID{n}.csv` | Vicon *Model Outputs* @120 fps; tracks Responder:A–H (anchors, antenna+center markers) and Responder:I (the tag BSF66F) |
| index | `.../erlangen_20260528_optitrack/session_notes.csv` | maps ID→path, `duration_s=120` |

Notes / gotchas found:

- Captures are **120 s** each (`session_notes.csv` → `duration_s=120`), not 60 s as the prompt table stated. ~1200 sweeps × 8 anchors ≈ 9500 valid ranges per ID.

- The tag ground truth is **`Responder:I`** in the Vicon file (room-centre object, antenna+center markers); anchors A–D sit low (z≈200–270 mm), E–H high (z≈1630–1700 mm).

- **Vicon parsing gotcha:** each `ID{n}.csv` has two sections — `Model Outputs` (clean, gap-filled, modeled) then `Trajectories` (raw markers, *different* column layout). Reading past the `Trajectories` header silently corrupts every mean (it made the tag look like it moved ~900 mm). This analysis parses **Model Outputs only**.

- UWB anchor_id→letter map is the **identity** (0=A … 7=H), confirmed by a brute-force permutation search minimising UWB−geometry residual spread; per-anchor antenna-delay bias then lands at a sane 108–213 mm.

## Task 2 — Per-ID per-anchor ranges

Valid rows only (`valid==1`, `status=='O'`). Cell = **mean mm (std mm)**; per-anchor N≈1150–1200 so the standard error of each mean is ≤4 mm — the orientation deltas below are far above noise.

| ID | h | orient | N | A | B | C | D | E | F | G | H |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 13 | mid | ABEF | 9498 | 2079 (15) | 2026 (38) | 2018 (22) | 2197 (30) | 1915 (21) | 1881 (103) | 2070 (95) | 1976 (39) |
| 14 | mid | BCGF | 9510 | 2134 (22) | 1941 (21) | 2055 (22) | 2129 (28) | 1857 (30) | 1927 (73) | 2005 (114) | 2018 (28) |
| 15 | mid | CDHG | 9554 | 2031 (22) | 2021 (30) | 2029 (30) | 2196 (23) | 1866 (23) | 1909 (71) | 2060 (135) | 1979 (33) |
| 16 | mid | ADHE | 9484 | 2150 (31) | 1972 (28) | 2093 (17) | 2166 (30) | 1877 (26) | 1957 (84) | 2017 (121) | 2010 (41) |
| 17 | low | ABEF | 9500 | 1723 (31) | 1850 (28) | 1936 (28) | 1780 (25) | 2372 (22) | 2400 (107) | 2573 (80) | 2275 (45) |
| 18 | low | BCGF | 9505 | 1693 (19) | 1727 (23) | 1993 (24) | 1991 (31) | 2216 (32) | 2341 (79) | 2505 (105) | 2476 (46) |
| 19 | low | CDHG | 9485 | 1955 (23) | 1625 (21) | 1727 (22) | 1981 (27) | 2719 (23) | 2297 (104) | 2819 (88) | 2405 (39) |
| 20 | low | ADHE | 9505 | 1905 (31) | 1890 (23) | 1809 (25) | 1792 (26) | 2309 (30) | 2699 (94) | 2354 (89) | 2562 (124) |
| 21 | high | ABEF | 9503 | 2298 (35) | 2340 (23) | 2338 (19) | 2645 (30) | 1785 (24) | 1826 (104) | 1980 (94) | 1896 (41) |
| 22 | high | BCGF | 9518 | 2613 (20) | 2237 (25) | 2404 (20) | 2352 (24) | 1736 (35) | 1849 (72) | 2038 (96) | 1916 (28) |
| 23 | high | CDHG | 9533 | 2282 (19) | 2509 (20) | 2391 (28) | 2453 (24) | 1794 (31) | 1818 (94) | 2021 (91) | 1915 (39) |
| 24 | high | ADHE | 9518 | 2426 (23) | 2245 (25) | 2839 (19) | 2353 (32) | 1763 (29) | 1835 (87) | 2030 (92) | 1927 (38) |

## Task 3.1 — Per-height orientation delta

Δ = (orientation) − (ABEF reference), per anchor, in mm. **Two versions:**

- **RAW** = Δ of measured UWB mean range → *confounded* by any physical tag displacement.

- **BIAS** = Δ of (UWB − geometric range to the actual Vicon antenna position) → tag displacement removed; isolates the orientation-dependent range effect. **BIAS is the metric to trust.**

### RAW (measured UWB range Δ — confounded)
**mid height** (ref = ID13 = ABEF):

| anchor | ABEF | →BCGF Δ | →CDHG Δ | →ADHE Δ | max\|Δ\| |
|---|---|---|---|---|---|
| A | 2079 | 55 | -48 | 71 | 71 |
| B | 2026 | -85 | -6 | -54 | 85 |
| C | 2018 | 37 | 11 | 76 | 76 |
| D | 2197 | -68 | -1 | -31 | 68 |
| E | 1915 | -57 | -48 | -37 | 57 |
| F | 1881 | 46 | 28 | 76 | 76 |
| G | 2070 | -64 | -10 | -53 | 64 |
| H | 1976 | 42 | 3 | 35 | 42 |

**low height** (ref = ID17 = ABEF):

| anchor | ABEF | →BCGF Δ | →CDHG Δ | →ADHE Δ | max\|Δ\| |
|---|---|---|---|---|---|
| A | 1723 | -30 | 232 | 182 | 232 |
| B | 1850 | -123 | -226 | 40 | 226 |
| C | 1936 | 57 | -208 | -127 | 208 |
| D | 1780 | 211 | 201 | 12 | 211 |
| E | 2372 | -156 | 347 | -63 | 347 |
| F | 2400 | -60 | -103 | 299 | 299 |
| G | 2573 | -68 | 246 | -219 | 246 |
| H | 2275 | 201 | 130 | 288 | 288 |

**high height** (ref = ID21 = ABEF):

| anchor | ABEF | →BCGF Δ | →CDHG Δ | →ADHE Δ | max\|Δ\| |
|---|---|---|---|---|---|
| A | 2298 | 315 | -16 | 128 | 315 |
| B | 2340 | -103 | 169 | -95 | 169 |
| C | 2338 | 66 | 54 | 501 | 501 |
| D | 2645 | -293 | -192 | -292 | 293 |
| E | 1785 | -49 | 9 | -22 | 49 |
| F | 1826 | 23 | -7 | 10 | 23 |
| G | 1980 | 58 | 41 | 50 | 58 |
| H | 1896 | 20 | 19 | 31 | 31 |

### BIAS (geometry-corrected Δ — antenna orientation effect)
**mid height** (ref = ID13 = ABEF):

| anchor | ABEF | →BCGF Δ | →CDHG Δ | →ADHE Δ | max\|Δ\| |
|---|---|---|---|---|---|
| A | 180 | 60 | -50 | 68 | 68 |
| B | 145 | -74 | 13 | -42 | 74 |
| C | 53 | 38 | 18 | 95 | 95 |
| D | 210 | -73 | -14 | -28 | 73 |
| E | 144 | -57 | -55 | -54 | 57 |
| F | 114 | 52 | 42 | 76 | 76 |
| G | 181 | -70 | -9 | -47 | 70 |
| H | 90 | 32 | -15 | 24 | 32 |

**low height** (ref = ID17 = ABEF):

| anchor | ABEF | →BCGF Δ | →CDHG Δ | →ADHE Δ | max\|Δ\| |
|---|---|---|---|---|---|
| A | 148 | -58 | 56 | 37 | 58 |
| B | 77 | 30 | -62 | 52 | 62 |
| C | 96 | 72 | -40 | 18 | 72 |
| D | 102 | 56 | 47 | 10 | 56 |
| E | 223 | -171 | 217 | -178 | 217 |
| F | 91 | 55 | 24 | 309 | 309 |
| G | 147 | -66 | 365 | -112 | 365 |
| H | 44 | 78 | -0 | 274 | 274 |

**high height** (ref = ID21 = ABEF):

| anchor | ABEF | →BCGF Δ | →CDHG Δ | →ADHE Δ | max\|Δ\| |
|---|---|---|---|---|---|
| A | 173 | 328 | -11 | 129 | 328 |
| B | 202 | -98 | 181 | -92 | 181 |
| C | 92 | 51 | 46 | 498 | 498 |
| D | 415 | -301 | -206 | -296 | 301 |
| E | 125 | -30 | 16 | -19 | 30 |
| F | 127 | 30 | 7 | 15 | 30 |
| G | 146 | 38 | 32 | 47 | 47 |
| H | 81 | 12 | 4 | 26 | 26 |

## Task 3.2 — Orientation effect size

| metric | n | max\|Δ\| | median\|Δ\| | RMS(Δ) | mean(Δ) | verdict |
|---|---|---|---|---|---|---|
| RAW (confounded) | 72 | 501 | 59 | **144.9** | 19.5 | **MAJOR (>30mm)** |
| BIAS (geometry-corrected) | 72 | 498 | 51 | **128.4** | 18.7 | **MAJOR (>30mm)** |

**Verdict: MAJOR** — orientation moves ranges by ~128 mm RMS (bias), > 4× the 30 mm MAJOR threshold.

## Task 3.3 — Geometric consistency (cosine fit)

Per anchor, fit the 4 orientations to `value(θ)=c0 + A·cos(θ−φ)`, θ = yaw {0,90,180,270}° (fit per height, amplitude averaged). Amplitude A on the **bias** metric = orientation-driven range swing. Phase φ compared to the anchor's azimuth as seen from the tag.

| anchor | A (raw) mm | A (bias) mm | bias φ (°) | anchor azimuth (°) | φ−azimuth (°) |
|---|---|---|---|---|---|
| A | 92 | **60** | 226 | 122 | +104 |
| B | 80 | **47** | 257 | 227 | +30 |
| C | 126 | **96** | 190 | 307 | -117 |
| D | 85 | **53** | 260 | 44 | -144 |
| E | 73 | **49** | 250 | 125 | +124 |
| F | 72 | **53** | 197 | 230 | -33 |
| G | 58 | **71** | 220 | 310 | -91 |
| H | 31 | **38** | 184 | 48 | +136 |

- Bias amplitude: mean **58.4 mm**, median 53.2 mm, max 96.3 mm; **8/8 anchors exceed 20 mm** → every anchor's range depends on orientation.

- **But the pattern is not a clean far-field cosine:** fitted phases (184–260°) are loosely clustered yet do **not** track each anchor's azimuth-from-tag (φ−azimuth scatters from −144° to +136°). Interpretation: the swing is orientation-dependent and per-anchor real, but dominated by the tag's own asymmetry / phase-centre offset / orientation-dependent multipath rather than a predictable radiation pattern indexed by anchor geometry.

## Task 3.4 — Height dependence

| height | RMS(Δ) raw | RMS(Δ) bias | tag moved vs ref (max, mm) |
|---|---|---|---|
| mid | 49.6 | 51.5 | 21 |
| low | 183.6 | 139.3 | 214 |
| high | 163.7 | 165.5 | 22 |

- The effect does **not** vanish with height. On the trustworthy **bias** metric it is *largest at high* (166 mm), moderate at low (139 mm) and smallest at mid (52 mm). Because it persists where the tag barely moved (mid/high, ≤22 mm), it is a genuine orientation effect, not a floor/ceiling artefact alone — though the mid≪high gap shows the near-field environment (proximity to the low vs high anchor ring) modulates its size.

- The **raw** low-height RMS (184 mm) is inflated by the real 147–222 mm tag displacement; bias correction pulls it down to 139 mm.

## Task 3.5 — Vicon ground-truth analysis

### A. Did the tag move?

| ID | h | orient | within-capture std (mm) | Δ vs same-height ref (mm) |
|---|---|---|---|---|
| 13 | mid | ABEF | 0.08 | 0.0 |
| 14 | mid | BCGF | 0.02 | 11.6 |
| 15 | mid | CDHG | 0.06 | 18.5 |
| 16 | mid | ADHE | 0.07 | 21.5 |
| 17 | low | ABEF | 0.16 | 0.0 |
| 18 | low | BCGF | 0.35 | 155.3 |
| 19 | low | CDHG | 0.24 | 214.3 |
| 20 | low | ADHE | 0.23 | 147.2 |
| 21 | high | ABEF | 0.20 | 0.0 |
| 22 | high | BCGF | 0.17 | 22.4 |
| 23 | high | CDHG | 0.03 | 19.2 |
| 24 | high | ADHE | 0.13 | 6.3 |

- **Within a capture the tag is rock-static** (antenna-marker std 0.02–0.35 mm ≪ 5 mm). ✓

- **Between orientations it is NOT static at low height:** ID18/19/20 sit 147–222 mm from ID17. Mid (≤22 mm) and high (≤22 mm) are close to the ‘<5 mm’ expectation but not exact — the tripod was clearly re-placed between orientations. This is why the raw ΔUWB must be geometry-corrected.

### B. Position error per orientation (trilaterate 8 UWB ranges vs Vicon)

| ID | orient | h | pos_err (mm) | x_err | y_err | z_err | horiz_err | fit_rms |
|---|---|---|---|---|---|---|---|---|
| 13 | ABEF | mid | **155** | -3 | -22 | +154 | 22 | 139 |
| 14 | BCGF | mid | **137** | +9 | -26 | +134 | 28 | 129 |
| 15 | CDHG | mid | **142** | -8 | +15 | +141 | 17 | 131 |
| 16 | ADHE | mid | **210** | +0 | -8 | +210 | 8 | 145 |
| 17 | ABEF | low | **221** | +24 | -18 | -219 | 31 | 78 |
| 18 | BCGF | low | **187** | -53 | +19 | -178 | 56 | 86 |
| 19 | CDHG | low | **428** | -18 | -51 | -424 | 55 | 150 |
| 20 | ADHE | low | **334** | +42 | -4 | -331 | 42 | 135 |
| 21 | ABEF | high | **363** | -35 | -23 | +360 | 42 | 110 |
| 22 | BCGF | high | **366** | +42 | -24 | +363 | 48 | 140 |
| 23 | CDHG | high | **378** | +32 | +46 | +374 | 56 | 102 |
| 24 | ADHE | high | **462** | -79 | +78 | +449 | 111 | 144 |

- Position error is **dominated by the z axis** (|z_err| 134–449 mm; horizontal only 8–111 mm). z flips sign with height (+ at mid/high, − at low): that is the signature of a **common-mode range bias projecting onto the poorly-conditioned z (z-DOP)**, not of orientation. Trilateration fit_rms (78–150 mm) reflects the per-anchor bias spread, consistent with the 108–213 mm antenna delays.

### C. Orientation-induced position-error spread (same physical spot, 4 orientations)

| height | min err | max err | spread | >30 mm? |
|---|---|---|---|---|
| mid | 137 | 210 | **74** | YES |
| low | 187 | 428 | **241** | YES |
| high | 363 | 462 | **100** | YES |

- Spread exceeds 30 mm at **every** height (mid 74, high 100, low 241 mm) → orientation causes position-level error, not merely range-level bias. (Low is inflated by ID19's 428 mm outlier and the residual low-height geometry.)

### D. Does the error vector rotate with the tag?

| height | horiz-err harmonic coherence w/ yaw | horiz_err range (mm) |
|---|---|---|
| mid | 0.58 | 8–28 |
| low | 0.65 | 31–56 |
| high | 0.97 | 42–111 |

- The horizontal error vector **partly rotates with yaw** (coherence 0.58 mid / 0.65 low / 0.97 high), i.e. the bias is orientation-locked and to that extent *predictable*. Caveat: only 4 points per height vs a 2-parameter harmonic (few dof, coherence optimistic), and the horizontal magnitude is small (≤111 mm) next to the z-DOP error. The cleaner directional fingerprint lives at the **range-bias** level (Task 3.3), not the position level.

## Task 4 — Implication for the wand caliper

CCF4 is mounted 180° opposed to 9336/955A on the wand, so for any wand pose CCF4 sees the opposite antenna aspect. For a per-anchor bias `A·cos(θ−φ)`, the CCF4-vs-955A difference is `A·cos(θ+180−φ)−A·cos(θ−φ)`, whose peak magnitude is **2A**.

| quantity | value |
|---|---|
| bias amplitude A, mean over anchors | 58.4 mm |
| bias amplitude A, worst anchor | 96.3 mm |
| max CCF4−955A per-anchor bias split (2·A mean) | **117 mm** |
| max CCF4−955A per-anchor bias split (2·A worst) | **193 mm** |
| observed CCF4−955A caliper failure | 324 mm |
| fraction explained (mean / worst) | 36% / 59% |

- Antenna-orientation range bias can inject a **~117 mm (typical) to ~193 mm (worst-anchor)** CCF4-vs-955A per-anchor split, which propagates into the two tags' position solutions. That accounts for **roughly one-third to one-half** of the 324 mm CCF4–955A caliper failure — a **major contributor but not the sole cause**, consistent with the separate finding that part of the caliper miss is a per-tag position/z error rather than pure common-mode range.

- **Caveats on the extrapolation:** BSF66F is a different physical unit than the wand tags (same family, but per-unit directionality varies); the fitted swing bundles directionality + phase-centre rotation + room-specific multipath; and Erlangen anchor geometry differs from the wand test rig. Treat 117–193 mm as an order-of-magnitude estimate, not a calibration constant.

## Reproduce

```bash
python3 experiments/antenna_orientation_erlangen/analyze.py      # -> results.json (~30 s, 1 core)
python3 experiments/antenna_orientation_erlangen/make_report.py  # -> REPORT.md
```

Read-only on all Erlangen capture data; every number above is derived in `results.json`.

