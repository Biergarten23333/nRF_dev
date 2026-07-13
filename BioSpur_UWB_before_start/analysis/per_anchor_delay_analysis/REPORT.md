# Per-anchor antenna-delay calibration analysis

Pure offline analysis on `logs/geiger_scan_20260711_161258_8anchor/scan.log` (517 cycles,
3225 LOO observations). Reuses locked `pg_lib` + `analysis/aps011_rsl_recomputation/recompute.py`.
No firmware, no hardware, no git. Artifacts: `per_anchor_delay.py`, `results.json`, this report.

**Sign convention (consistent throughout): positive = anchor reads LONG.**
Geiger bias = `measured − predicted`. Solver `d_i` enters `|x_i−x_j| + d_i + d_j − dist`
(so `d_i>0` ⇒ reads long). An antenna delay that is too *small* makes range too *long*, so
nulling a positive bias means *increasing* the delay register count.

Compute: `nproc`=12; LOO ran single-process (numpy/scipy LM), 24.3 s CPU / 24.3 s wall ⇒
**1.0 core busy** (work light enough that no Pool was needed).

---

## 1 + 2. Per-anchor bias table with AutoPos cross-reference

| Anchor | Geiger bias mean (mm) | median | std | n | AutoPos d_anchor (mm) | bound? | residual = bias − d |
|:--:|--:|--:|--:|--:|--:|:--:|--:|
| A | −156.3 | −170.7 | 291 | 410 | 0.0 | pinned (gauge) | −156.3 |
| B | +68.3 | +56.1 | 238 | 415 | +12.8 | — | +55.5 |
| C | +213.7 | +177.8 | 286 | 414 | +60.0 | **+60 BOUND** | +153.7 |
| D | −48.6 | −69.8 | 287 | 409 | +30.9 | — | −79.5 |
| E | +193.6 | +159.2 | 232 | 408 | +18.6 | — | +175.0 |
| F | −73.7 | −88.7 | 238 | 400 | +13.0 | — | −86.7 |
| G | −108.0 | −113.5 | 240 | 390 | +31.6 | — | −139.5 |
| H | +24.3 | +17.3 | 245 | 379 | +60.0 | **+60 BOUND** | −35.7 |

Constant-bias RMS = **128.6 mm**. Mean column reproduces the pre-established table exactly
(machinery confirmed).

**Cross-reference caveats:** the solver **pins d_A=0 as the gauge** (only 7 free delays), so
its delays are relative to A while Geiger biases are absolute — A's −156 is largely the gauge
choice. **Only 2/8 anchors (C, H) hit the +60 bound** (the earlier "4/8" was wrong); none hit
−60. The residual column is not purely "what the bound prevented": AutoPos delays are fit from
*anchor↔anchor* ranges, Geiger biases from *wand↔anchor* ranges at different aspect angles, and
the known B/H directional (antenna-pointing) bias means the two geometries legitimately differ.
C is the clean case — both say "long," the bound clipped it at +60 while Geiger wants +214.

## 3. LOO improvement ceiling — scenarios A/B/C

Pooled slope +3.65%, pivot 2968 mm.

| Scenario | Correction | LOO \|resid\| median (mm) |
|:--|:--|--:|
| **A** raw | — | **157.5** |
| *(residual-space demean ceiling)* | demean residuals in place | *134.4* |
| **B** per-anchor offset, in-sample | range −= round(bias), re-trilaterate | **146.3** |
| **B** per-anchor offset, **cross-validated** | split-half | **150.7** |
| **C** offset + slope | + b·(r−pivot), b=0.0365 | **140.8** |

**Important correction to the earlier "158→134" claim:** that figure was the residual-space
*demean ceiling* (post-hoc, in-sample). Doing what a real correction does — subtract the offset
from *ranges* and *re-trilaterate* — recovers only to **146 mm** in-sample / **151 mm**
cross-validated. With 8 responders, trilateration already absorbs part of each per-anchor
constant into the position solve, so nulling the ranges buys back less than the demean bound.
**Realizable A→B gain ≈ 7–11 mm median; B→C slope adds ≈5 mm** (again confirming the APS011
slope is near-worthless). The ~146 mm remainder is the irreducible multipath/GDOP floor.

## 4. Correction pathway

**mm-per-count:** DWT_TIME_UNITS = 1/(499.2e6·128) = 15.650 ps; c = 299792458 m/s ⇒
1 count = **4.6918 mm**. In SS-TWR the DW1000 computes `ToF = ToF_true − (D_I+D_R)/2` with
`D_R = TX_ANTD + RX_ANTD`. Moving *both* of an anchor's registers by Δ ⇒ ΔD_R = 2Δ ⇒
ΔToF = −Δ ⇒ **Δrange = −4.6918·Δ mm (one-way)**. Null bias b: `Δ = round(b / 4.6918)`,
`new = 16436 + Δ`.

| Anchor | bias (mm) | Δ counts | **new TX_ANTD = RX_ANTD** |
|:--:|--:|--:|:--:|
| A | −156.3 | −33 | **16403** |
| B | +68.3 | +15 | **16451** |
| C | +213.7 | +46 | **16482** |
| D | −48.6 | −10 | **16426** |
| E | +193.6 | +41 | **16477** |
| F | −73.7 | −16 | **16420** |
| G | −108.0 | −23 | **16413** |
| H | +24.3 | +5 | **16441** |

**Firmware support:** `16436U` is a **compile-time `#define`, uniform, with no runtime path** —
`src/ss_twr_resp.c:17-18` (used `:377-378`), `src/ss_twr_anchor_init.c:17-18`,
`src/ss_twr_init.c:25-26`, and the alt-SS-TWR unicast/broadcast trees. Per-anchor values would
require **8 distinct builds or per-device flash/OTA injection**.

**Option C (widen solver bound):** the solver is `least_squares(..., loss="huber", f_scale=2.0,
bounds=(lo,hi))` with `np.full(n−1, ±60.0)` — replicas at
`.../official_report_field_solver_13052026/run_clean_full_compare.py:807-808` (wand) and
`:865-866` (roto), plus a soft prior `d[1:]/20.0` at `:815`/`:873`. Widening = set ±200.0 there
and relax the prior `/20.0 → /100.0`. **Risk:** the delay↔layout-scale degeneracy (ρ≈−0.977)
plus current inter-anchor pair RMS 105.8 mm means a wider bound lets delays soak up real
geometry error. The ±60 bound + 20 mm prior exist specifically to prevent that. **Reject**
unless a metric-scale constraint (measured baseline / corner reflector / fixed-XYZ reference
tag) is added first.

## Recommendation — **Option A (analysis-side offsets)**

Store the eight `bias_mm` as a per-anchor `range_offset_mm` and subtract in the position solver
(which already carries a per-anchor delay term, so the plumbing exists). Zero firmware risk;
delivers the full realizable **157 → 146 mm**. Keep the Option B register table above as a
documented, ready-to-flash future step, executed only after a multi-geometry calibration
(tripod occlusion ladder / corner reflector) confirms the offsets are geometry-stable rather
than aspect artifacts. Reject Option C until a metric-scale anchor breaks the degeneracy.

**Bottom line:** per-anchor delay variation is the dominant *systematic* (129 mm RMS), but at
LOO-median level it is worth only ~7–11 mm after re-trilateration + cross-validation; ranging is
floored at ~146 mm by random multipath/GDOP. Take the free analysis-side fix; do not spend 8
firmware builds or a destabilized solver chasing the largely irreducible floor.
