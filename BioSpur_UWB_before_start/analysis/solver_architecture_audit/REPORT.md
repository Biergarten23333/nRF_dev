# Solver Architecture Audit — V4-IO Layout Solver + Tag Position Solver

**Scope:** read-only audit. No code was modified.
**Date:** 2026-07-12
**Repo:** `/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start`

---

## 0. TL;DR — the map you asked for, plus two surprises

There are **two solver *layers*** as you expected, but the tag-position layer is
**not one solver — it is four distinct implementations** with different math and
different robustness. Two of them run "at runtime" (one on the nRF52 tag itself,
one on the host). Read section 3 carefully — the mental model of "one runtime tag
solver" is wrong.

```
LAYER 1 — V4-IO / AutoPos LAYOUT solver  (offline, self-calibrating, ~once per deployment)
  file:  autopos_pipeline/outdoor_20260513/analysis_20260513_182053/
             run_full_evaluation_same_pipeline_20260513.py :: solve_v4()      [L440-468]
  driver: run_clean_full_compare.py :: solve_version("v4-io")                  [L310-317]
  runner: logs/autopos_diagnostic_20260710/code/run_v4io_solve.py :: main()
  estimates: 8 anchor XYZ (gauge-fixed) + 7 per-anchor delays = 25 params
  optimizer: scipy least_squares, Huber, HARD ±60 mm box on the delays
  output:  anchor_layout.json  (== autopos/layout_v4io.json)
                    │
                    ▼  (geometry + per-anchor d_anchor_mm, all in mm)
LAYER 2 — TAG POSITION solvers  (four of them)
  2a  src/uwb_tag_loc.c              ON-DEVICE nRF52, LIVE (ss_twr_init.c:2595)
        brute-force 2^N subset search + Gauss-Newton, quality weights
        !! uses a HARDCODED, STALE layout (src/uwb_anchor_layout.c), NOT V4-IO output
        !! ignores per-anchor delays entirely
  2b  biospur_tag_positioning_offline_solver/c_core/src/tagpos_solver.c   HOST, canonical offline
        robust Gauss-Newton IRLS (Huber/Tukey) + EMA + temporal/IMU prior, delays applied
        !! its leave-one-out anchor-rejection config is DEAD CODE (never invoked)
  2c  .../run_clean_full_compare.py :: solve_position_fast()   HOST, numpy reference of 2b
  2d  logs/geiger_scan_.../analysis/pg_lib.py :: solve_pos()   HOST, scipy-LM, unweighted, delays IGNORED
```

**Two surprises to flag up front:**

1. **The on-device firmware tag solver (2a) is fed by a hardcoded anchor layout
   (`src/uwb_anchor_layout.c`) that does NOT match the current AutoPos calibration**
   — different baseline (B = 2563 mm firmware vs 4712 mm AutoPos), z-axis sign
   flipped, and no per-anchor delays. The V4-IO → tag-solver link only exists on the
   **host** side. See §4.
2. **The canonical offline C core (2b) has a full single-anchor leave-one-out
   rejection machinery (config fields + `exclude_index` plumbing) that is never
   executed** — `biospur_tagpos_solve_frame` only ever calls `solve_once(..., -1, ...)`.
   Robustness in 2b is therefore Huber down-weighting *only*. See §3f.

---

## 1. Locating both solvers

### 1a. V4-IO / AutoPos LAYOUT solver

| Role | File | Symbol |
|---|---|---|
| **Core solve math (canonical)** | `autopos_pipeline/outdoor_20260513/analysis_20260513_182053/run_full_evaluation_same_pipeline_20260513.py` | `solve_v4()` L440-468 |
| Dispatcher | `autopos_pipeline/outdoor_20260513/run_clean_full_compare.py` | `solve_version("v4-io")` L310-317 |
| **Current runner (2026-07-10)** | `logs/autopos_diagnostic_20260710/code/run_v4io_solve.py` | `main()` — docstring: *"Run the PRODUCTION v4-io anchor-layout solver."* |
| Official field-check driver | `biospur_tag_positioning_offline_solver/reference_current_implementations/official_report_field_solver_13052026/run_v4io_field_check.py` | `main()` |
| ρ=−0.977 FIM diagnostic | `autopos_pipeline/28052026_Erlangen_Official/Analysis/reports/EN/literature_search/delay_geometry_fim.py` | `couple("delay<->iso-scale")` |

**Provenance confirmation** — the deployed calibration output tags itself:
`logs/system_calibration_20260710_233443/anchor_layout.json` → `stats.solver =
"v4-io (production run_clean_full_compare.solve_version)"`. This is a self-verifying
pointer to exactly the chain above.

**NOT the target:** `scripts_reserve_nomore_change/solve_anchor_layout.py` — a frozen
geometry-only cuboid-rig solver with **no antenna-delay state**. Its coincidental
`--reference-sigma-mm 60.0` default is a ground-truth range sigma, not the ±60 mm delay bound.

### 1b. Tag POSITION solver — four implementations

| # | File | Symbol | Runs where | Layout source | Delays? |
|---|---|---|---|---|---|
| **2a** | `src/uwb_tag_loc.c` | `uwb_tag_loc_solve` L539 / `uwb_tag_loc_refine_gauss_newton` L376 | **On nRF52 tag, LIVE** (`src/ss_twr_init.c:2595`) | hardcoded `src/uwb_anchor_layout.c` (stale) | **No** |
| **2b** | `biospur_tag_positioning_offline_solver/c_core/src/tagpos_solver.c` | `biospur_tagpos_solve_frame` L359 | Host (x86 `.so` via ctypes) | AutoPos `layout.json` | **Yes** (in cost fn) |
| 2c | `.../official_report_field_solver_13052026/run_clean_full_compare.py` | `solve_position_fast` L520 / `solve_positions` L481 | Host (numpy) | AutoPos layout | Yes (in cost fn) |
| 2d | `logs/geiger_scan_20260711_161258_8anchor/analysis/pg_lib.py` | `solve_pos` L91 | Host (scipy) | `anchor_layout.json` (233443) | **No (ignored)** |

The packaged Python wrapper for 2b is
`biospur_tag_positioning_offline_solver/biospur_tag_positioning_offline_solver/`
(`c_solver.py`, `trajectory.py`, `models.py`, `layout_io.py`).

---

## 2. V4-IO Layout Solver — full characterization

### 2a. File / entry point
`autopos_pipeline/outdoor_20260513/analysis_20260513_182053/run_full_evaluation_same_pipeline_20260513.py`
→ **`solve_v4(pair_dists, anchor_ids, x_init=None)`** (L440-468), seeded by
`solve_autopos_v1` (classical MDS + NLS refine) and dispatched from
`run_clean_full_compare.py::solve_version` for `version in {"v4-io","v5"}`.

### 2b. State vector (n = 8 anchors → **25 parameters**)

- **Anchor positions — 18 params, 3D, gauge-fixed.** `pos_param_map(8)` (L207-215)
  and `gauge_align_local` (L179-204) remove the 6-DOF SE(3) gauge:
  - **A (id 0): pinned to origin (0,0,0)** — 0 free params
  - **B (id 1): on the x-axis** (y=z=0) — 1 free param (Bx)
  - **C (id 2): in the xy-plane** (z=0) — 2 free params (Cx, Cy)
  - **D–H (ids 3-7): fully free 3D** — 15 free params
- **Per-anchor antenna delays — 7 params, differential.** `d = zeros(n); d[1:] = v[...]`
  (L449-451). **Anchor A's delay is fixed at 0** as the delay gauge.
- **No scale / clock / drift parameters.** This omission is exactly what makes the
  common-mode delay ↔ isotropic layout-scale degenerate (§2f).
- Residual vector ≈ **42 rows**: 28 inter-anchor pairs + 7 delay-prior rows + ~7
  physical-layer-prior rows.

### 2c. Input data
- **Inter-anchor ranges ONLY** — the "io" in V4-IO = *inter-anchor / inter-only*
  (VERSIONS label, `run_clean_full_compare.py:55`: *"Huber bounded-delay inter-anchor"*).
- Directed anchor↔anchor SS-TWR pair medians (`load_sweep_grouped` → `fuse_from_directed`
  "v3" robust/MAD fusion), **28 pairwise distances** for the complete 8-anchor graph.
- ~1000 samples/pair (`sweep1000`), fused to one median per directed pair.
- **Fully self-calibrating — no Vicon/OptiTrack inside the solver.** Ground truth is
  used only *outside* for scoring. Ultrasound heights (F/G/H) are applied *after* the
  UWB solve as a separate step (`apply_ultrasound_height_to_layout.py`), not inside `solve_v4`.
- **Gauge break is purely internal** (fix A/B/C + A-delay=0). No procrustes-to-reference,
  no measured metric baseline → root cause of the scale/delay coupling.

### 2d. Cost function & optimizer
Robust nonlinear **weighted least-squares (Huber M-estimator)** via
`scipy.optimize.least_squares` (Trust-Region-Reflective, forced by the bounds).

```python
# run_full_evaluation_same_pipeline_20260513.py:465
result = least_squares(fun, x0, loss="huber", f_scale=2.0, bounds=(lo, hi), max_nfev=5000)
```

Residual model (L454-460): `predicted_pair = ||x_i − x_j|| + d_i + d_j`, normalized by
σ = 15 mm, plus an L2 delay prior (÷20 mm) and soft physical two-layer priors. All
distances/delays in **mm**.

### 2e. The ±60 mm delay bound
A **hard box constraint** on the 7 delay parameters (positions unbounded), passed as
scipy's `bounds=`. Not a penalty, not a clip.

```python
# run_full_evaluation_same_pipeline_20260513.py:462-465
x0 = np.r_[pack_pos(x_init), np.zeros(max(0, n - 1))]
lo = np.r_[np.full(len(pmap), -np.inf), np.full(max(0, n - 1), -60.0)]
hi = np.r_[np.full(len(pmap), np.inf), np.full(max(0, n - 1),  60.0)]
result = least_squares(fun, x0, loss="huber", f_scale=2.0, bounds=(lo, hi), max_nfev=5000)
```

The same ±60.0 box appears in the wand/roto constrained branches
(`run_clean_full_compare.py` L727-728, L790-791).

> **The bound is BINDING in the deployed layout.** In
> `logs/system_calibration_20260710_233443/anchor_layout.json`, anchors **C and H sit
> exactly at 59.99999… mm** — the solver pushed both delays hard into the +60 mm ceiling.
> That is the signature of the delay block absorbing geometry error it cannot resolve (see §2f).

### 2f. The ρ = −0.977 degeneracy and its (implicit) regularization

**What it is:** a Fisher-information diagnostic (not a solver output). In
`delay_geometry_fim.py`, after marginalizing tag positions (Schur complement) and
removing the anchor SE(3) gauge, the **common-mode anchor delay correlates with
isotropic layout scale at ρ = −0.977**, i.e. variance inflation 1/(1−ρ²) = **22.1×**.
Recorded in `logs/science_audit_20260710/AUDIT_REPORT.md:192,246` and
`track5_consistency.json:142`. (Horizontal ρ=−0.974/19.3×; vertical ρ=−0.814/3.0×.)

**Is it explicitly regularized?** **No dedicated decoupling term exists** — there is
no fixed metric baseline, no procrustes, no scale parameter, no common-mode/scale
penalty in the default `v4-io` path. The degeneracy is held in check only by three
*implicit* regularizers:

1. **Delay gauge:** A's delay ≡ 0.
2. **Soft L2 delay prior (Tikhonov → 0):** the `(dly[1:] / 20.0)` residual rows (L458),
   σ = 20 mm, pulling all delays toward zero.
3. **The ±60 mm hard box** (§2e).

The design intent is stated verbatim in `analysis/per_anchor_delay_analysis/REPORT.md:85-93`:

> *"the delay↔layout-scale degeneracy (ρ≈−0.977) plus current inter-anchor pair RMS
> 105.8 mm means a wider bound lets delays soak up real geometry error. **The ±60 bound
> + 20 mm prior exist specifically to prevent that.** Reject [widening] unless a
> metric-scale constraint (measured baseline / corner reflector / fixed-XYZ reference
> tag) is added first."*

So **yes — the ±60 mm bound *is* the intended regularizer for the coupling**, together
with the ÷20 mm soft prior and the A-delay gauge. There is a separate experimental
branch `solve_v4_common_mode` (L471-558) that reparameterizes `d_i = c + e_i`, bounds
`c` to ±150 mm and regularizes the differential `e_i` (`e / e_reg_scale_mm`, mean-tie) —
this *does* isolate the coupled common mode explicitly, but it is **not** the shipped
default (`v4-io-commonmode` / `v5-commonmode`, not `v4-io`).

### 2g. Output
- Writer: `save_layout()` (`run_clean_full_compare.py:741`).
- Frame: internal AutoPos gauge (A=origin, B on x, C in xy-plane), **mm**.
- JSON schema: `anchors:[{id,label,x_mm,y_mm,z_mm,d_anchor_mm}], tag_delay_mm, stats{}, extra{}`.
- `d_anchor_mm` = **range-equivalent millimeters** (NOT ns, NOT DW1000 ticks).
- Quality: `stats.inter_anchor_pair_rms_mm` (105.76 mm in the deployed run over 28 pairs),
  plus `tables/delay_sanity.csv` (`n_near_bounds_55mm`), `autopos_quality_summary.csv`.
- Deployed output: `logs/system_calibration_20260710_233443/anchor_layout.json`
  (byte-identical to `autopos/layout_v4io.json`). See §4b for the full numbers.

---

## 3. Tag Position Solver — full characterization

> Because there are four implementations, this section is organized per-question with a
> row for each. The **runtime** ones are **2a (on-device)** and **2b (host, canonical offline)**.

### 3a. File locations / language

- **2a — Firmware C, on nRF52832:** `src/uwb_tag_loc.c` (744 lines). LIVE: the tag
  initiator role `src/ss_twr_init.c:2595` calls `uwb_tag_loc_solve()` after collecting
  ranges. Mirror copies under `SS-TWR/alt-SS-TWR/broadcast/` and `.../unicast/`.
- **2b — Host C, x86:** `biospur_tag_positioning_offline_solver/c_core/src/tagpos_solver.c`
  (compiled `libbiospur_tagpos.so` via `gcc -O3`, driven from Python by
  `c_solver.py` through ctypes). Header `c_core/include/biospur_tagpos/tagpos_solver.h`.
- **2c — Host Python (numpy):** `solve_position_fast` — the numpy reference that 2b was
  built to match ("behavior-compatible T1").
- **2d — Host Python (scipy):** `pg_lib.py::solve_pos` — a separate lightweight
  analysis-only trilaterator.

**2a and 2b/2c are NOT the same algorithm** (see 3b). 2b and 2c *are* the same algorithm
(hand-rolled Gauss-Newton IRLS), one in C, one in numpy.

### 3b. Algorithm

| # | Method | Init / seed | Iterations |
|---|---|---|---|
| 2a | Gauss-Newton on normal equations (`solve_3x3`), **wrapped in a brute-force 2^N anchor-subset search** | per-subset linear (least-squares) seed | 8 |
| 2b | Robust Gauss-Newton **IRLS** (Huber/Tukey weights recomputed each iter); tiny Levenberg diagonal 1e-9; 500 mm step clamp | warm-start from previous frame (`last_by_tag`), else anchor centroid | 8 (`max_iters`) |
| 2c | Same as 2b, numpy (`np.linalg.lstsq`), Huber-in-normalized-units, 500 mm clamp | warm-start else centroid | 8 |
| 2d | Levenberg-Marquardt (`scipy.least_squares method="lm"`), unweighted | caller-supplied `x0` (wand centroid) | up to `max_nfev=200` |

None are closed-form (no Bancroft/Chan-Ho). All are iterative.

### 3c. Input / missing anchors / weighting / delays

- **Min anchors:** all require **≥ 4** (2a `UWB_TAG_LOC_MIN_ANCHORS`; 2b/2c `min_anchors=4`;
  2d `len(ids) < 4`). All handle < 8 anchors gracefully.
- **Weighting:**
  - 2a: `weight = 0.25 + quality_percent/100` (link-quality weighting; **no** robust loss).
  - 2b: per-anchor σ (from `anchor_sigma.json`, default 50 mm) × quality-EMA penalty ×
    residual-EMA penalty, then Huber (default, δ=30 mm) or Tukey (c=4.685) robust weight.
  - 2c: Huber in normalized residual units (`|rn|≤2 → 1`, else `2/|rn|`), σ = anchor sigma.
  - 2d: **none** (plain residuals).
- **Per-device antenna delays (`d_anchor_mm`):**
  - 2b/2c: **applied inside the cost function** — `pred = dist + delay + tag_delay`
    (`tagpos_solver.c:261`; `run_clean_full_compare.py:540`).
  - 2a: **NOT applied** (raw ranges; firmware uses only a uniform DW1000 ANT_DLY).
  - 2d: **loaded but NOT applied** — `load_geometry` reads `DLY` but `solve_pos` ignores it.

### 3d. Output

- 2a: `int32 x/y/z_mm`, `residual_rms_mm`, `residual_max_mm`, `used_anchor_count`,
  `lower/upper_anchor_count`, `anchor_ids[]` (the chosen subset).
- 2b: `SolveResult` — xyz, `residual_rms_mm`, `residual_p95_abs_mm`, `max_abs_residual_mm`,
  `anchors_used/input`, `rejected_anchor_id` (**always None in practice**, see 3f),
  per-anchor residuals + used mask, IMU prior diagnostics.
- 2c: xyz array only (metrics computed by callers).
- 2d: `(pos, ids, rms)`.
- **Filtering:** only 2b has temporal state — a soft **previous-position prior**
  (`temporal_prior_sigma_mm=180`, T3/T4 only) and quality/residual EMAs. There is **no
  Kalman filter anywhere** (README explicitly defers it). 2a/2c/2d are memoryless per-frame
  (2c warm-starts the seed but keeps no prior term).

### 3e. Performance / bottleneck (no in-repo benchmarks; complexity + design evidence)

- **2a (nRF52, real-time):** the dominant cost is the **exhaustive subset search** —
  `for mask in [0 .. 2^candidate_count)`, keeping subsets with ≥4 bits (≈163 subsets for
  8 anchors), each running a linear seed + 8 GN iterations + residual/tetra-volume/bounds
  checks. **All math is `double` on a Cortex-M4F whose FPU is single-precision only** →
  software-emulated doubles. This is the bottleneck: cost is **exponential in anchor
  count**, not linear. Order-of-magnitude estimate: hundreds of thousands of double-flops
  per fix → roughly single-digit-ms per solve, i.e. ~O(100s) solves/s on-device. (The
  `EXACT4` subset policy exists to cap this to C(n,4) subsets.)
- **2b (host C):** a single GN solve, 8 iters × ≤8 anchors × a 3×3 solve → microseconds
  of pure C. In practice the **per-frame Python↔ctypes marshalling dominates**, so
  realistic throughput is ~10³–10⁴ solves/s from Python.
- **2c (host numpy):** its own docstring says it *"replaces scipy least_squares … the
  speedup comes from a small analytic Gauss-Newton loop instead of constructing a scipy
  optimizer per frame."* → the team already found **constructing a scipy optimizer
  per-frame (2d-style) was the bottleneck**; the numpy GN is the fix, the C core (2b) is
  the further optimization.
- **2d (scipy LM):** slowest — builds a `least_squares` object per frame; suitable for
  offline analysis only.

### 3f. Robustness to a bad anchor (e.g. B's 300 mm step or E's ±290 mm multipath jump)

- **2a (on-device):** **most robust by construction** — the brute-force subset search
  scores every ≥4-anchor subset by `rms + 0.35·max_residual + size_penalty +
  volume_penalty` and picks the best. A single 300 mm outlier makes any subset containing
  it score worse, so the search naturally drops it (subject to keeping ≥4 anchors and the
  plane-observability ladder 2+2 → 1+1 → 0+0). Cost: exponential, as above.
- **2b (canonical offline):** **Huber down-weighting ONLY.** A 300 mm residual against
  δ=30 mm gets weight 30/300 = 0.1 (10× down-weight) but is **not rejected**. It still
  pulls the solution. **Critically, the leave-one-out rejection is dead code:**
  `biospur_tagpos_solve_frame` calls `solve_once(..., exclude_index=-1, ...)` exactly once
  and returns (`tagpos_solver.c:414-433`). The config fields `reject_abs_threshold_mm`,
  `reject_min_improvement_mm`, `reject_improvement_ratio` and the `rejected_index` output
  are parsed and plumbed but **never consumed** — `rejected_index` is always −1. Switching
  the loss to Tukey (redescending, zero weight beyond 4.685σ) would harden it, but that is
  not the default. **This is the single biggest robustness gap for the offline path.**
- **2c:** same Huber-only story; no subset search.
- **2d:** weakest — unweighted LM; a bad anchor pulls the fit fully. Only pre-filter is a
  static `300 ≤ range_mm ≤ 8000` gate.

So: on-device (2a) actively rejects a stepped/jumping anchor; the offline canonical path
(2b) merely down-weights it and can drift, because its rejection logic is inert.

---

## 4. The connection between the two layers

### 4a. How V4-IO output feeds the tag solver

**Host side (2b/2c/2d):** the layout is **loaded from JSON on disk**, nothing hardcoded.
`layout_io.load_layout_json` parses `anchors[].{x_mm,y_mm,z_mm,d_anchor_mm}` +
optional `anchor_sigma.json`; `c_solver.py:230,238` copies `d_anchor_mm` into the C
`delays[]` array and resolves `tag_delay`. Per-anchor delays are applied **inside the
cost function** (`pred = dist + d_anchor_mm + tag_delay`), which is algebraically
identical to pre-subtracting them from the range — there is no separate subtraction stage.

**Firmware side (2a):** the layout is a **compile-time hardcoded table** in
`src/uwb_anchor_layout.c` (see §4c) and has **no delay field**. The DW1000 registers hold
a single **uniform** `TX_ANT_DLY = RX_ANT_DLY = 16436U` for every device
(`ss_twr_init.c:25-26` etc.) — a global antenna delay, not per-anchor. So the per-anchor
`d_anchor_mm` from V4-IO is **never written back to firmware** and is invisible to the
on-device solver.

**Consequence:** the V4-IO → tag-position link is a **host-only** link. The real-time
on-device solver is architecturally disconnected from the calibration.

### 4b. `logs/system_calibration_20260710_233443/`

Newest of three (`224406` is empty/aborted; `225404` sourced a *different, z-flipped*
external layout). The `233443` bundle is the canonical "system ready" output.

Contents: `anchor_layout.json`, `system_config.json`, `listener_positions.json`,
`wand_positions.json`, `autopos/` (`layout_v4io.json`, `pairs_all.csv`, `summary.json`,
`round_A..H/master.log`), `raw/` (per-listener logs).

**`anchor_layout.json` (== `autopos/layout_v4io.json`) — the deployed V4-IO output:**

| id | label | x_mm | y_mm | z_mm | d_anchor_mm |
|---|---|---|---|---|---|
| 0 | A | 0.0 | 0.0 | 0.0 | 0.0 |
| 1 | B | 4712.81 | 0.0 | 0.0 | 12.79 |
| 2 | C | 4427.60 | 3031.55 | 0.0 | **59.99999** (at +60 bound) |
| 3 | D | 265.52 | 2853.09 | −231.11 | 30.95 |
| 4 | E | 601.70 | −230.93 | −1568.88 | 18.60 |
| 5 | F | 4740.38 | 94.25 | −1484.20 | 13.03 |
| 6 | G | 4485.08 | 3199.54 | −1484.20 | 31.57 |
| 7 | H | 570.74 | 2552.50 | −1779.03 | **59.99999** (at +60 bound) |

`tag_delay_mm = 0.0`; `stats.inter_anchor_pair_rms_mm = 105.76` (28 pairs);
`stats.solver = "v4-io (production run_clean_full_compare.solve_version)"`;
`extra.layer_order_ok = false` (the two-layer prior flags the geometry as not cleanly
separated). **No git hash embedded.** Note **C and H are pinned at the ±60 mm ceiling**
(§2e) — the coupling manifesting on hardware.

`system_config.json` re-embeds a *rounded* copy (drops `d_anchor_mm`) and records
`anchor_layout.source = "…/autopos/layout_v4io.json"`, `self_consistency_rms_mm = 105.76`,
`system_ready = true`.

`listener_positions.json` / `wand_positions.json` are downstream tag-solve products
(7 listeners solved via `pg_lib`-style trilateration, RMS 60–349 mm; 3 wand tags with a
caliper cross-check that 2 of 3 pairs FAIL by ~160 mm).

### 4c. Firmware vs AutoPos layout — the mismatch

`src/uwb_anchor_layout.c` (hardcoded, no delays):

```c
static const struct uwb_anchor_pose_mm uwb_anchor_layout[UWB_MAX_ANCHORS] = {
    {0U, 'A',     0,    0,    0},
    {1U, 'B',  2563,    0,    0},
    {2U, 'C',  2533, 4420,   -8},
    {3U, 'D',  -243, 4300,    0},
    {4U, 'E',    32,  -74, 1516},
    {5U, 'F',  2588,  137, 1512},
    {6U, 'G',  2453, 4486, 1515},
    {7U, 'H',  -245, 4290, 1518},
};
```

This is a **different deployment** than the AutoPos `233443` layout: B baseline 2563 vs
4712 mm, and the upper plane is **+z (~+1516)** in firmware vs **−z (~−1568)** in AutoPos.
The on-device solver (2a) is therefore running against a stale/independent geometry. If the
tag's own position output is being used, this is a correctness issue worth confirming
against the intended deployment.

### 4d. Is `pg_lib.solve_pos` the same as the runtime solver?

**No.** `pg_lib.solve_pos` (2d) is a separate, simpler *offline analysis* trilaterator:
scipy-LM, unweighted, delays ignored, only a static range pre-filter. It shares only the
generic "LS trilateration on ≥4 ranges" idea with the packaged solver (2b). It is used by
the geiger/static-drift/person-effect analyses, not by the field-report or UI paths.

---

## 5. Core code excerpts (verbatim)

### 5.1 V4-IO layout solver — `solve_v4` (the layout math)
`autopos_pipeline/outdoor_20260513/analysis_20260513_182053/run_full_evaluation_same_pipeline_20260513.py:440-468`

```python
def solve_v4(pair_dists, anchor_ids, x_init=None):
    lp, _g2l, _l2g = local_pairs(pair_dists, anchor_ids)
    n = len(anchor_ids)
    if x_init is None:
        x_init = mds_init(lp, n)
    pmap = pos_param_map(n)

    def unpack(v):
        x = unpack_pos(v[:len(pmap)], n)
        d = np.zeros(n)
        if n > 1:
            d[1:] = v[len(pmap):]
        return x, d

    def fun(v):
        x, dly = unpack(v)
        out = [(np.linalg.norm(x[i] - x[j]) + dly[i] + dly[j] - dist) / 15.0 for (i, j), dist in lp.items()]
        if n > 1:
            out.extend((dly[1:] / 20.0).tolist())          # <-- L2 delay prior (sigma 20 mm)
        out.extend(physical_layout_prior_residuals(x, anchor_ids))
        return np.asarray(out)

    x0 = np.r_[pack_pos(x_init), np.zeros(max(0, n - 1))]
    lo = np.r_[np.full(len(pmap), -np.inf), np.full(max(0, n - 1), -60.0)]   # <-- ±60 mm delay box
    hi = np.r_[np.full(len(pmap), np.inf), np.full(max(0, n - 1), 60.0)]
    result = least_squares(fun, x0, loss="huber", f_scale=2.0, bounds=(lo, hi), max_nfev=5000)
    x, dly = unpack(result.x)
    result.physical_diagnostics = layout_physical_diagnostics(x, anchor_ids)
    return gauge_align_local(x), dly, result
```

### 5.2 Tag solver 2b — host C core Gauss-Newton IRLS
`biospur_tag_positioning_offline_solver/c_core/src/tagpos_solver.c` — inner loop of
`solve_once` (L247-313). Note `pred = dist + delay + tag_delay` (delays applied) and the
normal-equations accumulation `h += jᵀj`, `g += jᵀr` solved by `solve_3x3`.

```c
for (int it = 0; it < cfg->max_iters; ++it) {
    double h[3][3] = {{1e-9,0,0},{0,1e-9,0},{0,0,1e-9}};   /* tiny Levenberg diagonal */
    double g[3] = {0,0,0};
    int rows = 0;
    for (int i = 0; i < n; ++i) {
        if (i == exclude_index) continue;
        double diff[3] = { p[0]-anchor_xyz[3*i+0], p[1]-anchor_xyz[3*i+1], p[2]-anchor_xyz[3*i+2] };
        double dist = vec_norm3(diff);
        if (dist < 1e-6) continue;
        double delay = (anchor_delay && is_good(anchor_delay[i])) ? anchor_delay[i] : 0.0;
        double pred = dist + delay + tag_delay;               /* <-- per-anchor + tag delay */
        double residual = pred - ranges[i];
        double sigma  = effective_sigma(cfg->method, cfg, anchor_sigma, quality, quality_ema, residual_ema, i);
        double rn     = residual / sigma;
        double weight = robust_weight(cfg, residual, sigma);  /* Huber (default) or Tukey */
        if (weight <= 0.0) continue;
        double sqrt_weight = sqrt(weight);
        double scale = sqrt_weight / sigma;
        double j[3] = { diff[0]/dist*scale, diff[1]/dist*scale, diff[2]/dist*scale };
        double r = rn * sqrt_weight;
        for (int a=0;a<3;++a){ g[a]+=j[a]*r; for(int b=0;b<3;++b) h[a][b]+=j[a]*j[b]; }
        ++rows;
    }
    if (rows < 3) return BIOSPUR_TAGPOS_ERR_TOO_FEW_ANCHORS;
    /* T3/T4 optional soft previous-position prior added to h,g here (L280-295) */
    double rhs[3] = {-g[0],-g[1],-g[2]}, step[3];
    if (solve_3x3(h, rhs, step) != BIOSPUR_TAGPOS_OK) return BIOSPUR_TAGPOS_ERR_SINGULAR;
    double step_norm = vec_norm3(step);
    if (step_norm > cfg->max_step_mm) { double s=cfg->max_step_mm/step_norm; step[0]*=s;step[1]*=s;step[2]*=s; step_norm=cfg->max_step_mm; }
    p[0]+=step[0]; p[1]+=step[1]; p[2]+=step[2];
    if (step_norm < cfg->convergence_mm) break;
}
```

**Dead rejection path** — `biospur_tagpos_solve_frame` (L412-433) calls `solve_once`
once with `exclude_index = -1` and returns; the LOO loop implied by the config is never run:

```c
int rc = solve_once(..., /*exclude_index=*/-1, base_xyz, out_residuals_mm, out_used_mask, &base_result);
if (rc != BIOSPUR_TAGPOS_OK) { /* error out */ }
out_xyz_mm[0]=base_xyz[0]; out_xyz_mm[1]=base_xyz[1]; out_xyz_mm[2]=base_xyz[2];
if (out_result) *out_result = base_result;
return BIOSPUR_TAGPOS_OK;   /* rejected_index stays -1 */
```

### 5.3 Tag solver 2a — on-device firmware Gauss-Newton + subset search
`src/uwb_tag_loc.c` — GN refine (L376-437) and the enclosing brute-force subset search (L577-657).

```c
/* --- per-subset Gauss-Newton refine (L382-434) --- */
for (uint8_t iter = 0U; iter < UWB_TAG_LOC_MAX_ITERATIONS; ++iter) {   /* 8 */
    double h[3][3] = {{0.0}}, g[3] = {0.0,0.0,0.0}, delta[3] = {0.0,0.0,0.0};
    for (size_t i = 0; i < candidate_count; ++i) {
        if ((subset_mask & (1UL << i)) == 0U) continue;
        double dx = estimate->x - candidates[i].pos_m.x, dy = ..., dz = ...;
        double predicted = sqrt(dx*dx+dy*dy+dz*dz);           /* NO delay term */
        double residual  = predicted - candidates[i].range_m;
        double jacobian[3] = { dx/predicted, dy/predicted, dz/predicted };
        double weight = 0.25 + ((double)candidates[i].quality_percent / 100.0);  /* quality weight */
        for (int r=0;r<3;++r){ g[r]+=weight*jacobian[r]*residual; for(int c=0;c<3;++c) h[r][c]+=weight*jacobian[r]*jacobian[c]; }
    }
    if (!uwb_tag_loc_solve_3x3(h, g, delta)) return false;
    estimate->x -= delta[0]; estimate->y -= delta[1]; estimate->z -= delta[2];
    if (fabs(delta[0])<1e-4 && fabs(delta[1])<1e-4 && fabs(delta[2])<1e-4) break;
}

/* --- brute-force subset search with plane-observability degradation (L571-657) --- */
const uint8_t plane_reqs[][2] = { {2U,2U}, {1U,1U}, {0U,0U} };   /* >=2/2 lower/upper, else degrade */
for (req_idx ...) {
  for (uint32_t mask = 0U; mask < (1UL << candidate_count); ++mask) {  /* 2^N subsets */
    subset_size = popcount(mask);
    if (subset_policy == EXACT4 && subset_size != 4) continue;
    if (subset_size < UWB_TAG_LOC_MIN_ANCHORS) continue;             /* >=4 */
    if (!uwb_tag_loc_linear_seed(...)) continue;
    if (!uwb_tag_loc_refine_gauss_newton(...)) continue;
    uwb_tag_loc_compute_residuals(..., &rms_m, &max_residual_m, &lower_count, &upper_count);
    if (lower_count < req_lower || upper_count < req_upper) continue;
    if (uwb_tag_loc_subset_max_tetra_volume_m3(...) < UWB_TAG_LOC_MIN_TETRA_VOLUME_M3) continue;
    score = rms_m*1000 + max_residual_m*1000*0.35 + (candidate_count-subset_size)*40.0 /* size penalty */
            + volume_penalty_m*1000*3.0;                             /* out-of-bounds penalty */
    if (!best_valid || score < best_score) { best_* = ...; }
  }
  if (best_valid) break;
}
```

### 5.4 Tag solver 2c — host numpy reference (`solve_position_fast`)
`.../official_report_field_solver_13052026/run_clean_full_compare.py:520-565`

```python
def solve_position_fast(obs, global_xyz, global_delay, anchor_sigma, x0=None, tag_delay_mm=0.0):
    if x0 is None or not np.all(np.isfinite(x0)):
        x0 = np.nanmean([global_xyz[a] for a, _r in obs], axis=0)   # centroid seed
    p = np.asarray(x0, dtype=float).copy()
    for _ in range(8):
        j_rows, r_rows, w_rows = [], [], []
        for a, measured in obs:
            diff = p - global_xyz[a]; dist = float(np.linalg.norm(diff))
            if dist < 1e-6: continue
            pred = dist + (0.0 if np.isnan(global_delay[a]) else global_delay[a]) + tag_delay_mm
            sigma = max(5.0, float(anchor_sigma.get(a, 50.0)))
            rn = (pred - measured) / sigma
            hw = 1.0 if abs(rn) <= 2.0 else 2.0 / max(abs(rn), 1e-9)  # Huber
            j_rows.append(diff/dist/sigma); r_rows.append(rn); w_rows.append(math.sqrt(hw))
        if len(j_rows) < 3: break
        j = np.asarray(j_rows) * np.asarray(w_rows)[:, None]; r = np.asarray(r_rows) * np.asarray(w_rows)
        step, *_ = np.linalg.lstsq(j, -r, rcond=None)
        norm = float(np.linalg.norm(step))
        if norm > 500.0: step *= 500.0 / norm                        # step clamp
        p += step
        if float(np.linalg.norm(step)) < 0.02: break
    return p
```

### 5.5 Tag solver 2d — analysis `pg_lib.solve_pos`
`logs/geiger_scan_20260711_161258_8anchor/analysis/pg_lib.py:91-101`

```python
def solve_pos(P, rg, x0, ids=None):
    """LM least-squares position on responding anchors (>=4). Returns (pos|None, ids, rms)."""
    if ids is None:
        ids = valid_ids(rg)
    if len(ids) < 4:
        return None, ids, np.nan
    Pi = P[ids]
    di = np.array([rg[a] for a in ids], float)
    r = least_squares(lambda x: np.linalg.norm(Pi - x, axis=1) - di, x0,   # RAW ranges, no delay
                      method="lm", max_nfev=200)
    return r.x, ids, float(np.sqrt(np.mean(r.fun ** 2)))
```

---

## 6. Cross-cutting observations (for whoever makes changes next)

1. **Delay ↔ layout coupling is a design-acknowledged, actively-managed hazard.** The
   ±60 mm bound + ÷20 mm prior + A-delay gauge are the *only* regularizers; C and H already
   sit at the bound. Widening the bound without adding a metric-scale constraint (measured
   baseline / corner reflector / fixed-XYZ reference tag) will let delays absorb geometry
   error — this is written down in `analysis/per_anchor_delay_analysis/REPORT.md:85-93`.

2. **The offline canonical tag solver (2b) has inert outlier rejection.** For B's 300 mm
   step or E's ±290 mm jumps, 2b currently only Huber-down-weights. Either activate the
   existing LOO machinery in `biospur_tagpos_solve_frame` or default to Tukey. The
   on-device solver (2a) already rejects via subset search.

3. **The firmware on-device layout is stale relative to AutoPos** (§4c) and carries no
   per-anchor delays. If on-device positions matter, the calibrated layout + delays need a
   path onto the tag; today they only reach the host solvers.

4. **Four tag solvers, three algorithms, no shared core.** The offline module README
   (`docs/current_solver_inventory.md`) states the intent to unify 2b/2c behind one
   `multilateration.py`; 2a and 2d are outside that plan. Any behavioral change must be
   applied per-implementation.

---

## Appendix — full source file index (read these for the complete picture)

**Layer 1 (V4-IO):**
- `autopos_pipeline/outdoor_20260513/analysis_20260513_182053/run_full_evaluation_same_pipeline_20260513.py` — `solve_v4` L440-468; gauge L179-215; common-mode variant L471-558; physical priors L273-292.
- `autopos_pipeline/outdoor_20260513/run_clean_full_compare.py` — `solve_version` L274-335; `save_layout` L741; wand/roto bounds L727-728/790-791.
- `logs/autopos_diagnostic_20260710/code/run_v4io_solve.py` — current runner.
- `autopos_pipeline/28052026_Erlangen_Official/Analysis/reports/EN/literature_search/delay_geometry_fim.py` — ρ=−0.977 diagnostic.
- `analysis/per_anchor_delay_analysis/REPORT.md` — ±60/÷20 design rationale.

**Layer 2 (tag position):**
- `src/uwb_tag_loc.c` (+ `include/uwb_tag_loc.h`, `src/uwb_anchor_layout.c`) — on-device (2a); called from `src/ss_twr_init.c:2595`.
- `biospur_tag_positioning_offline_solver/c_core/src/tagpos_solver.c` (+ header) — host C core (2b).
- `biospur_tag_positioning_offline_solver/biospur_tag_positioning_offline_solver/{c_solver,trajectory,models,layout_io}.py` — Python wrapper for 2b.
- `.../reference_current_implementations/official_report_field_solver_13052026/run_clean_full_compare.py` — `solve_position_fast` (2c).
- `.../reference_current_implementations/ui_realtime_trajectory_solver_20052026/export_capture_trajectory.py` — UI `solve_frame` (2c-family).
- `logs/geiger_scan_20260711_161258_8anchor/analysis/pg_lib.py` — `solve_pos` (2d).

**Connection / calibration output:**
- `logs/system_calibration_20260710_233443/{anchor_layout.json, system_config.json, listener_positions.json, wand_positions.json, autopos/}`.
- Firmware antenna delay: `src/ss_twr_init.c:25-26`, `src/ss_twr_resp.c:17-18`, `src/ss_twr_anchor_init.c:17-18` (`ANT_DLY 16436U`, uniform).
</content>
</invoke>
