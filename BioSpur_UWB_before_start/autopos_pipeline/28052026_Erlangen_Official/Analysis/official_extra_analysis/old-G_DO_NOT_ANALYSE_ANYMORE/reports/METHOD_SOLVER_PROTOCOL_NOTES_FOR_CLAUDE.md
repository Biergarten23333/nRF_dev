# Method notes for Claude: official Erlangen AutoPos solvers and SS-TWR protocol

This file is a source-grounded handoff for writing the Method chapter. It answers what was actually used in the 2026-05-28 Erlangen official analysis, not what an older draft or reserve script might imply.

The most important corrections are:

1. The official V1 row is `v1-old`, implemented as classical MDS only after V1 pair fusion. It is not `v1_soft_iterative`.
2. The official `v4-io` layout solver in this analysis has 25 variables for 8 anchors: 18 gauge-fixed geometry variables plus 7 anchor-delay variables. I did not find a 31-variable official `v4-io` in the actual 2026-05-28 field-check path.
3. Tag/static ranging and AutoPos inter-anchor matrix ranging must be described as two different protocol uses. Tag capture uses broadcast Alt SS-TWR. AutoPos matrix/inter-anchor sweep uses rotating-master anchor-to-anchor unicast SS-TWR, not one broadcast poll producing all inter-anchor ranges.

## 0. Source map

Primary official layout wrapper:

```text
autopos_pipeline/outdoor_20260513/run_clean_full_compare.py
```

Primary official layout implementation module:

```text
autopos_pipeline/outdoor_20260513/analysis_20260513_182053/run_full_evaluation_same_pipeline_20260513.py
```

Official 2026-05-28 analysis provenance:

```text
autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/run_meta.json
autopos_pipeline/28052026_Erlangen_Official/solver/outputs/v1_to_v4_io_field_check/*/layout.json
```

Tag solver design and C implementation:

```text
biospur_tag_positioning_offline_solver/docs/t_series_design.md
biospur_tag_positioning_offline_solver/biospur_tag_positioning_offline_solver/c_solver.py
biospur_tag_positioning_offline_solver/c_core/src/tagpos_solver.c
```

SS-TWR protocol / firmware:

```text
SS-TWR/alt-SS-TWR/broadcast/include/uwb_ss_twr_shared.h
SS-TWR/alt-SS-TWR/broadcast/src/uwb_ss_twr_shared.c
SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c
SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_resp.c
SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_anchor_init.c
SS-TWR/alt-SS-TWR/broadcast/AUTOPOS_MATRIX_TRADITIONAL_SSTWR_A17_CHECKPOINT.md
SS-TWR/alt-SS-TWR/broadcast/WORK_SUMMARY_20260501_20260502.md
docs/BlackBox_20260327.md
```

Reserve / historical scripts that should not be silently treated as official 2026-05-28:

```text
scripts_reserve_nomore_change/run_autopos_solve_v1_v2_v3_v3full_from_existing.py
scripts_reserve_nomore_change/solve_anchor_layout_v3_full.py
autopos_pipeline/outdoor_v4_20260504/v1_to_v5_20260505_124031/run_v1_to_v5.py
```

## 1. Direct answer: did V1 run twice?

For this official Erlangen analysis: no, there is only one reported V1 layout row, and it is `v1-old`.

Evidence:

- `run_meta.json:22-26` points the official `v1-old` layout file to:

```text
autopos_pipeline/28052026_Erlangen_Official/solver/outputs/v1_to_v4_io_field_check/v1-old/layout.json
```

- `run_meta.json:860-866` and `run_meta.json:979-984` list the official layout versions:

```text
v1-old, v2, v3-lite, v3-full, v4-io
```

- `v1-old/layout.json:2-4` identifies the version as `v1-old` / `V1`.
- `v1-old/layout.json:21,29,37,45,53,61,69,77` show all `d_anchor_mm = 0.0`.
- `v1-old/layout.json:82-84` says:

```json
"implementation": "archive_v1_classical_mds_only",
"delay_aware": false
```

- `run_clean_full_compare.py:235-239` defines `solve_v1_old()` as:

```python
lp, _g2l, _l2g = mod.local_pairs(pair_dists, anchor_ids)
x = mod.mds_init(lp, len(anchor_ids))
dly = np.zeros(len(anchor_ids), dtype=float)
return x, dly, {"implementation": "archive_v1_classical_mds_only", "delay_aware": False}
```

So the official V1 row is MDS-only, no NLS refinement, no delay estimation.

The confusion comes from a different function name and reserve scripts:

- `run_full_evaluation_same_pipeline_20260513.py:380-381` defines `solve_autopos_v1()` as `solve_mds_nls()`.
- In the official wrapper, that function is used for `v3-lite` and for initializing `v4-io`, not for the reported `v1-old` row:

```text
run_clean_full_compare.py:249-251   v3-lite = mod.solve_autopos_v1(fused["v3"])
run_clean_full_compare.py:256-258   v4-io init = mod.solve_autopos_v1(fused["v3"])
```

There is also a reserve workflow that creates `anchor_layout_v1_soft_iterative.json`:

```text
scripts_reserve_nomore_change/run_autopos_solve_v1_v2_v3_v3full_from_existing.py:120-154
scripts_reserve_nomore_change/run_autopos_solve_v1_v2_v3_v3full_from_existing.py:314-329
scripts_reserve_nomore_change/run_autopos_solve_v1_v2_v3_v3full_from_existing.py:394-404
```

That is a reserve / historical pipeline. A search under `autopos_pipeline/28052026_Erlangen_Official` found no `v1_soft_iterative` or `anchor_layout_v1_soft_iterative` output. Therefore do not write that `v1_soft_iterative` was used for the official V1 row.

Recommended thesis wording:

> The official V1 comparison row (`v1-old`) is the archived V1 baseline: V1 pair fusion followed by classical MDS, gauge-aligned to the AutoPos local frame, with all antenna-delay terms fixed to zero. Later code also contains a function named `solve_autopos_v1` and a reserve `v1_soft_iterative` workflow, but these are not the reported official V1 row in the Erlangen 2026-05-28 analysis.

## 2. Layout solver data fusion

Before layout solving, directed inter-anchor measurements are fused into one undirected distance per anchor pair.

Source:

```text
run_full_evaluation_same_pipeline_20260513.py:116-141
```

Let pair `(i,j)` have directed samples `i->j` and `j->i`. The code implements:

### V1 fusion

Source lines `124-125`.

```python
d = mean(all directed samples)
```

Mathematically:

\[
\hat d_{ij}^{(v1)} = \frac{1}{N_{ij}}\sum_k r_{ij,k},
\]

where both directions are pooled.

### V2 fusion

Source lines `126-131`.

For each direction, compute directional means and sample variances with a variance floor of `1.0`. Then combine with inverse-variance weighting. The code writes it as:

```python
d = (var_ba * mean_ab + var_ab * mean_ba) / (var_ab + var_ba)
```

This is equivalent to weighting each directional mean by inverse variance:

\[
\hat d_{ij}^{(v2)}
=
\frac{\mu_{ij}/\sigma_{ij}^2 + \mu_{ji}/\sigma_{ji}^2}
     {1/\sigma_{ij}^2 + 1/\sigma_{ji}^2}.
\]

### V3 fusion

Source lines `132-137`.

V3 uses directional medians and MAD-based robust scale estimates, then applies the same inverse-variance-style fusion:

\[
\hat d_{ij}^{(v3)}
=
\frac{m_{ij}/s_{ij}^2 + m_{ji}/s_{ji}^2}
     {1/s_{ij}^2 + 1/s_{ji}^2},
\]

where `m` is the median and `s` is the MAD-derived sigma.

Which fusion is used by each official layout version:

Source:

```text
run_clean_full_compare.py:242-258
run_clean_full_compare.py:1245-1246
```

```text
v1-old:  fused["v1"]
v2:      fused["v2"]
v3-lite: fused["v3"]
v3-full: fused["v3"]
v4-io:   fused["v3"]
```

## 3. Gauge fixing for layout solvers

The official same-pipeline layout code fixes the local coordinate gauge as follows.

Source:

```text
run_full_evaluation_same_pipeline_20260513.py:179-204
run_full_evaluation_same_pipeline_20260513.py:207-215
```

`gauge_align_local(x)`:

1. Translate all anchors so anchor A is at the origin.
2. Use vector A->B as the local x-axis.
3. Use the component of A->C perpendicular to A->B to define the local y-axis.
4. Use `z = x cross y`.
5. Explicitly enforce:

```text
A = (0,0,0)
B_y = B_z = 0
C_z = 0
```

Thus for 8 anchors the geometry parameterization has:

```text
B_x                         1 variable
C_x, C_y                    2 variables
D,E,F,G,H each x,y,z        5 * 3 = 15 variables
total geometry variables    18
```

Important nuance: this is not just “A fixed at origin”. In the official solver code, B defines the x-axis and C lies in the xy-plane. This is the standard rigid gauge removal: 24 raw coordinates minus 6 rigid degrees of freedom equals 18 free geometry variables.

## 4. Layout solvers V1 to V4-io

### 4.1 Official V1: `v1-old`

Source:

```text
run_clean_full_compare.py:235-239
run_full_evaluation_same_pipeline_20260513.py:242-256
v1-old/layout.json:82-84
```

Inputs:

```text
fused["v1"]
```

Algorithm:

1. Build the complete pairwise distance matrix \(D\).
2. Run classical/Torgerson MDS:

\[
J = I - \frac{1}{n}\mathbf{1}\mathbf{1}^T,
\qquad
B = -\frac{1}{2} J D^{\circ 2} J.
\]

3. Eigen-decompose \(B\), keep the largest 3 nonnegative eigenvalues:

\[
X = V_3 \Lambda_3^{1/2}.
\]

4. Apply `gauge_align_local`.
5. Set all anchor delays to zero.

Cost interpretation:

Classical MDS solves the rank-3 Euclidean embedding through double-centering. In thesis language, this is the classical strain formulation:

\[
\min_{\mathrm{rank}(XX^T)\le 3}
\left\|XX^T - \left(-\frac{1}{2}JD^{\circ 2}J\right)\right\|_F^2,
\]

followed by the AutoPos local gauge transform. It is not the iterative range-residual LS used by `solve_autopos_v1()`.

Delay:

\[
d_A=\cdots=d_H=0.
\]

### 4.2 Official V2

Source:

```text
run_clean_full_compare.py:246-248
run_full_evaluation_same_pipeline_20260513.py:384-392
run_full_evaluation_same_pipeline_20260513.py:259-270
run_full_evaluation_same_pipeline_20260513.py:313-317
```

Inputs:

```text
fused["v2"]
```

Algorithm:

1. Initialize with classical MDS.
2. Run no-delay nonlinear least squares with a weak z-spread regularizer.
3. The regularization weight starts at `lam = 0.01` and is halved for three refinement rounds:

```python
lam = 0.01
for _ in range(3):
    x, result = nls_refine(x, lp, lam=lam)
    lam *= 0.5
```

Residual vector:

\[
r_{ij}(X) = \|x_i-x_j\| - \hat d_{ij}^{(v2)}.
\]

With `lam > 0`, the code appends:

\[
\sqrt{\lambda}(z_i-\bar z)
\]

for each anchor.

Optimizer:

`scipy.optimize.least_squares`, linear loss, normally TRF when the z regularizer is active.

Delay:

No delay estimation; exported delays are zero.

### 4.3 Official V3-lite

Source:

```text
run_clean_full_compare.py:249-251
run_full_evaluation_same_pipeline_20260513.py:380-381
```

Inputs:

```text
fused["v3"]
```

Algorithm:

The wrapper calls `mod.solve_autopos_v1(fused["v3"], anchor_ids)`. In this codebase, the function name means MDS initialization followed by no-delay nonlinear LS (`solve_mds_nls`), not the official reported `v1-old`.

Cost:

\[
\min_X \sum_{i<j}
\left(\|x_i-x_j\|-\hat d_{ij}^{(v3)}\right)^2
\]

under the A/B/C local gauge.

Delay:

No delay estimation; exported delays are zero.

### 4.4 Official V3-full

Source:

```text
run_clean_full_compare.py:252-255
run_full_evaluation_same_pipeline_20260513.py:395-437
```

Inputs:

```text
fused["v3"]
```

Model:

\[
r_{ij}(X,d)=\|x_i-x_j\|+d_i+d_j-\hat d_{ij}^{(v3)}.
\]

Gauge:

\[
d_A=0.
\]

Algorithm:

1. Initialize geometry with MDS.
2. Initialize all delays as zero.
3. Repeat up to 50 iterations:
   - Compute residuals \(r_{ij}\).
   - Estimate robust scale:

\[
\sigma = \max(\mathrm{MAD}(r), 5\ \mathrm{mm}).
\]

   - Use Tukey bisquare cutoff:

\[
c_T = 4.685\sigma.
\]

   - Weight each pair:

\[
w_{ij} =
\begin{cases}
\left(1-(r_{ij}/c_T)^2\right)^2, & |r_{ij}|\le c_T,\\
0, & |r_{ij}|>c_T.
\end{cases}
\]

   - Solve weighted geometry LS with current delays fixed:

\[
\min_X \sum_{i<j} w_{ij}
\left(\|x_i-x_j\|+d_i+d_j-\hat d_{ij}\right)^2.
\]

   - Update each non-A delay by the median residual balance:

\[
d_i \leftarrow
\mathrm{median}_{j:(i,j)}
\left[
\hat d_{ij}-\|x_i-x_j\|-d_j
\right].
\]

4. Stop when max anchor shift < 0.1 mm and max delay shift < 0.05 mm.

Optimizer:

`least_squares(..., loss="linear", method="trf")` inside each geometry update. Robustness is from the external Tukey IRLS weights, not SciPy's built-in robust loss.

### 4.5 Official V4-io

Source:

```text
run_clean_full_compare.py:256-258
run_full_evaluation_same_pipeline_20260513.py:440-468
run_full_evaluation_same_pipeline_20260513.py:273-292
```

Inputs:

```text
fused["v3"]
```

Initialization:

The wrapper initializes V4-io using:

```python
init, _ = mod.solve_autopos_v1(fused["v3"], anchor_ids)
x, d, res = mod.solve_v4(fused["v3"], anchor_ids, init)
```

That means V4-io is initialized from the no-delay MDS+NLS solution on V3 robust-fused distances.

Variable parameterization for 8 anchors:

```text
18 geometry variables:
  B_x
  C_x, C_y
  D/E/F/G/H x,y,z

7 delay variables:
  d_B, d_C, d_D, d_E, d_F, d_G, d_H

d_A fixed to 0
total = 25 variables
```

This is explicit in:

```text
run_full_evaluation_same_pipeline_20260513.py:445-452
run_full_evaluation_same_pipeline_20260513.py:462-465
```

The official V4-io code does not expose 31 variables. The closest older family that adds more variables is an older joint V4 script:

```text
autopos_pipeline/outdoor_v4_20260504/v1_to_v5_20260505_124031/run_v1_to_v5.py:345-375
```

That joint variant adds tag delay variables and per-frame tag positions; it is not the official `v4-io` field-check layout used in this Erlangen report.

Residual vector:

For each inter-anchor pair:

\[
u_{ij} =
\frac{\|x_i-x_j\|+d_i+d_j-\hat d_{ij}^{(v3)}}{15}.
\]

For each non-A anchor delay:

\[
u_{d,i} = \frac{d_i}{20},\qquad i\ne A.
\]

The solver also appends soft two-layer physical layout priors:

Source:

```text
run_full_evaluation_same_pipeline_20260513.py:273-292
```

Those priors are:

- D should remain near the A/B/C lower-layer z reference:

\[
u_D = \frac{z_D-\mathrm{median}(z_A,z_B,z_C)}{\sigma_D}.
\]

- E/F/G/H should remain near their upper-layer median:

\[
u_i = \frac{z_i-\mathrm{median}(z_E,z_F,z_G,z_H)}{\sigma_{\mathrm{upper}}}.
\]

- Layer gap is softly constrained to be physically plausible:

\[
u_{\min} =
\frac{\max(0,\mathrm{MIN\_LAYER\_GAP}-\mathrm{gap})}{120},
\qquad
u_{\max} =
\frac{\max(0,\mathrm{gap}-\mathrm{MAX\_LAYER\_GAP})}{250}.
\]

The exact numeric values of `LOWER_D_Z_SIGMA_MM`, `UPPER_LAYER_Z_SIGMA_MM`, `MIN_LAYER_GAP_MM`, and `MAX_LAYER_GAP_MM` should be read from the same module if needed.

Bounds:

\[
-60\ \mathrm{mm} \le d_i \le +60\ \mathrm{mm}
\quad\text{for }i\ne A.
\]

Optimizer:

```python
least_squares(
    fun,
    x0,
    loss="huber",
    f_scale=2.0,
    bounds=(lo, hi),
    max_nfev=5000,
)
```

Huber form for thesis:

Let \(u\) be a normalized residual and \(k=2.0\). Then:

\[
\rho_H(u;k)=
\begin{cases}
\frac{1}{2}u^2, & |u|\le k,\\
k(|u|-\frac{1}{2}k), & |u|>k.
\end{cases}
\]

Because range residuals are normalized by 15 mm, the range-residual Huber transition corresponds to about \(2\times 15=30\) mm in raw inter-anchor residual. Delay priors are normalized by 20 mm, so their transition is about 40 mm. The physical priors have their own normalizations.

Cost statement:

\[
\min_{\theta}
\sum_{i<j}\rho_H\!\left(
\frac{\|x_i-x_j\|+d_i+d_j-\hat d_{ij}}{15};2
\right)
+\sum_{i\ne A}\rho_H\!\left(\frac{d_i}{20};2\right)
+\sum_m\rho_H(u_{\mathrm{phys},m};2),
\]

with the A/B/C gauge and delay bounds above.

## 5. Tag solver T1 to T4

The T-series refers to tag positioning, not anchor layout. The source design document explicitly says this at:

```text
biospur_tag_positioning_offline_solver/docs/t_series_design.md:1-4
```

### Common tag model

Sources:

```text
t_series_design.md:14-19
c_solver.py:204-230
tagpos_solver.c:196-258
```

For a tag position \(p\), anchor position \(a_i\), anchor delay \(d_i\), tag delay \(d_t\), and measured range \(\rho_i\):

\[
r_i(p)=\|p-a_i\|+d_i+d_t-\rho_i.
\]

The C wrapper reads:

- `anchor_xyz` from the selected `layout.json`,
- `anchor.d_anchor_mm` as the per-anchor delay,
- `anchor.sigma_mm` as base sigma,
- per-frame range and quality,
- `tag_delay` from override or layout default.

Source:

```text
c_solver.py:217-230
```

The C core performs Gauss-Newton / iteratively reweighted least squares on the 3D tag position. At each iteration:

1. Compute range residual:

\[
r_i=\|p-a_i\|+d_i+d_t-\rho_i.
\]

2. Compute effective sigma:

\[
\sigma_i^{\mathrm{eff}} =
\max(\sigma_i,\sigma_{\min})
\cdot q_i
\cdot h_i,
\]

where quality penalty is active from T2 and residual-history penalty from T3.

3. Normalize:

\[
\tilde r_i=r_i/\sigma_i^{\mathrm{eff}}.
\]

4. Apply robust weight, usually Huber:

\[
w_i =
\begin{cases}
1, & |\tilde r_i|\le k,\\
k/|\tilde r_i|, & |\tilde r_i|>k.
\end{cases}
\]

Source:

```text
tagpos_solver.c:134-166
```

Default config:

```text
max_iters = 8
huber_k = 2.0
min_sigma_mm = 5.0
convergence_mm = 0.02
max_step_mm = 500.0
temporal_prior_sigma_mm = 180.0
robust_loss = Huber
```

Source:

```text
tagpos_solver.c:59-79
```

### T1: robust WLS multilateration

Source:

```text
t_series_design.md:6-29
tagpos_solver.c:196-258
```

T1 is per-frame range-only 3D multilateration:

\[
\min_p \sum_i \rho_H\!\left(\frac{\|p-a_i\|+d_i+d_t-\rho_i}{\sigma_i};k\right).
\]

Properties:

- Gauss-Newton iterations.
- Anchor sigma weighting.
- Huber robust residual weighting.
- Uses anchor delays from `layout.json`.
- `d_tag` normally 0 unless explicitly provided.
- Previous frame may be used as initialization by the Python wrapper, but T1 itself has no temporal prior term.

### T2: quality-aware robust WLS

Sources:

```text
t_series_design.md:31-44
tagpos_solver.c:96-117
tagpos_solver.c:134-150
```

T2 adds quality-based sigma inflation. It does not hard reject anchors.

Quality penalty in code:

\[
\mathrm{bad}_i = \max\left(0,\frac{100-q_i}{50}\right),
\]

\[
\mathrm{penalty}_q =
\mathrm{clip}
\left(
1+\alpha_q\mathrm{bad}_i^2,\ 1,\ \mathrm{cap}_q
\right),
\]

where the default scale and cap are:

```text
quality_penalty_scale = 1.5
quality_penalty_cap = 4.0
```

Then:

\[
\sigma_i^{\mathrm{eff}} = \sigma_i \cdot \mathrm{penalty}_q.
\]

### T3: dynamic-stable robust WLS

Sources:

```text
t_series_design.md:46-63
tagpos_solver.c:119-150
tagpos_solver.c:268-283
```

T3 adds:

1. Persistent-residual EMA soft downweighting.
2. Weak previous-position prior.
3. No single-frame hard anchor rejection.

Residual-history sigma inflation:

If residual EMA is below `residual_ema_start_mm`, no penalty is applied. Otherwise:

\[
\mathrm{excess}_i =
\frac{\mathrm{EMA}(|r_i|)-120}{80},
\]

\[
\mathrm{penalty}_r =
\mathrm{clip}
\left(1+0.50\cdot \mathrm{excess}_i,\ 1,\ 2.5\right).
\]

The temporal prior is appended when method >= T3 and previous position `x0` is available:

\[
u_{\mathrm{prior},a} =
\frac{p_a-p_{0,a}}{\sigma_{\mathrm{prior}}},
\qquad a\in\{x,y,z\}.
\]

Default:

\[
\sigma_{\mathrm{prior}} = 180\ \mathrm{mm}.
\]

### T4: adaptive redundancy policy

Sources:

```text
t_series_design.md:65-89
c_solver.py:155-165
c_solver.py:257-278
```

T4 is a policy wrapper:

```text
if number of valid anchors >= 8:
    solve with T1 robust WLS, no previous-position prior, x0=None
else:
    solve with T3-style dynamic-stable weighting and weak previous-position prior
```

The Python wrapper constructs a separate T1 C config for full-anchor T4:

```text
c_solver.py:155-165
```

And dispatches full-anchor T4 frames to that memory-free T1 path:

```text
c_solver.py:261-264
```

For fewer anchors, T4 runs the configured method with previous position `x0_tuple`:

```text
c_solver.py:264-278
```

### Hard rejection status

The current C code has an `exclude_index` parameter internally, but the public solver call uses `exclude_index = -1`:

```text
tagpos_solver.c:400-406
```

So in this official C-core path, there is no active leave-one-out hard rejection. This matches the design doc statement that hard leave-one-out rejection was tested and rejected because it damaged roto consistency:

```text
t_series_design.md:88-89
```

## 6. SS-TWR protocol details

There are two different protocol contexts. They should not be mixed.

### 6.1 Traditional SS-TWR timing formula

The implemented one-way time-of-flight estimate is visible in both the tag broadcast initiator and the anchor matrix initiator.

Sources:

```text
SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c:5034-5051
SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_anchor_init.c:286-307
```

Timestamp definitions:

```text
t1 = poll_tx_ts   at initiator
t2 = poll_rx_ts   at responder
t3 = resp_tx_ts   at responder
t4 = resp_rx_ts   at initiator
```

Round-trip intervals:

\[
T_{\mathrm{round}} = t_4-t_1,
\qquad
T_{\mathrm{reply}} = t_3-t_2.
\]

The initiator reads a carrier-integrator-derived clock offset ratio \(\epsilon\). The code computes:

\[
\mathrm{ToF} =
\frac{T_{\mathrm{round}} - T_{\mathrm{reply}}(1-\epsilon)}{2}
\cdot T_{\mathrm{DWT}},
\]

\[
\rho = c\cdot \mathrm{ToF}.
\]

This is the SS-TWR drift-corrected formula used by the firmware. In the tag broadcast path, this is computed separately for each anchor response.

### 6.2 Tag/static capture: broadcast Alt SS-TWR

This is the protocol used for tag-to-anchor ranging frames.

Sources:

```text
SS-TWR/alt-SS-TWR/broadcast/include/uwb_ss_twr_shared.h:37-48
SS-TWR/alt-SS-TWR/broadcast/src/uwb_ss_twr_shared.c:109-119
SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c:4232-4244
SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c:4500-4511
SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c:4761-4766
SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_resp.c:987-1003
```

Frame format:

- Destination address: broadcast short address `0xffff`.
- Source address: tag short address.
- Payload includes:
  - tag id,
  - active anchor mask,
  - scheduled poll TX timestamp.

The poll builder:

```python
build_alt_broadcast_poll_frame(seq, src_addr, anchor_mask, tag_id, poll_tx_ts)
```

writes:

```text
dst = 0xffff
tag_id
anchor_mask
poll_tx_ts
```

Protocol sequence:

1. The tag selects active anchors and builds an 8-bit anchor mask.
2. The tag sends one broadcast poll.
3. All selected anchors receive the same poll measurement instant.
4. Each selected anchor computes its response rank from the mask:

\[
\mathrm{rank}_i =
\#\{j<i : \mathrm{mask}_j=1\}.
\]

5. The response delay is:

\[
\Delta t_i =
\mathrm{guard} + \mathrm{rank}_i\cdot \mathrm{response\_spacing}.
\]

Defaults visible in code:

```text
guard = 500 us
response_spacing = 800 us
```

6. Each anchor sends one response in its rank slot. The response payload embeds:

```text
poll_rx_ts
resp_tx_ts
```

7. The tag listens through one response window, collects responses, and computes each anchor range independently using the SS-TWR formula above.

Key sentence for thesis:

> Broadcast Alt SS-TWR is not a manual pairwise measurement. It is a single tag broadcast poll containing an anchor mask; the selected anchors respond in deterministic rank slots, so the tag obtains multiple tag-to-anchor SS-TWR ranges with a shared poll epoch.

Delay interpretation:

The guard/rank response delay is a MAC scheduling delay. It is not the estimated antenna delay in the layout/tag solver. The solver-level delay terms \(d_i\) are effective endpoint range biases in millimetres, applied as additive terms in the range model:

\[
\rho_i \approx \|p-a_i\| + d_i + d_t.
\]

### 6.3 AutoPos matrix / inter-anchor sweep: rotating-master unicast SS-TWR

This is the protocol used for anchor-to-anchor ranging for AutoPos layout self-calibration in the broadcast branch after the matrix restore.

Sources:

```text
SS-TWR/alt-SS-TWR/broadcast/AUTOPOS_MATRIX_TRADITIONAL_SSTWR_A17_CHECKPOINT.md:7-10
SS-TWR/alt-SS-TWR/broadcast/AUTOPOS_MATRIX_TRADITIONAL_SSTWR_A17_CHECKPOINT.md:22-29
SS-TWR/alt-SS-TWR/broadcast/AUTOPOS_MATRIX_TRADITIONAL_SSTWR_A17_CHECKPOINT.md:125-130
SS-TWR/alt-SS-TWR/broadcast/WORK_SUMMARY_20260501_20260502.md:61-82
SS-TWR/alt-SS-TWR/broadcast/WORK_SUMMARY_20260501_20260502.md:107-120
SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_resp.c:30-44
SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_resp.c:935-939
SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_resp.c:981-1003
SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_anchor_init.c:145-205
SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_anchor_init.c:360-370
docs/BlackBox_20260327.md:431-435
docs/BlackBox_20260327.md:471-475
docs/BlackBox_20260327.md:539-542
```

This is the part that needs to be written carefully.

The AutoPos matrix sweep is not the same as the tag broadcast Alt SS-TWR path. The a17 checkpoint explicitly says:

```text
Keep the broadcast ranging baseline intact, but restore AutoPos Matrix sweep to
traditional anchor-to-anchor unicast SS-TWR.
```

The matrix responder accepts only:

```text
anchor-origin unicast poll addressed to the local anchor
```

This is implemented in `ss_twr_resp_matrix_poll_matches()`:

```text
dst must equal local anchor address
source must be an anchor address
```

The runtime switch is:

```text
allow_tag_polls=1  -> accepts tag/broadcast Alt SS-TWR poll
allow_tag_polls=0  -> accepts only anchor-origin unicast matrix poll
```

Matrix protocol sequence:

1. One anchor is in master/master-full role.
2. The current master iterates over peer anchor IDs.
3. For each peer, it sends a unicast poll:

```text
Master Anchor -> peer Anchor
```

4. The peer sends a unicast response:

```text
peer Anchor -> Master Anchor
```

5. The master computes the SS-TWR range using:

\[
\mathrm{ToF} =
\frac{T_{\mathrm{round}} - T_{\mathrm{reply}}(1-\epsilon)}{2}
\cdot T_{\mathrm{DWT}}.
\]

6. A single master round gives star coverage, not the full 28 unordered pairs for 8 anchors.
7. To obtain the full matrix, the initiator role is rotated across anchors A to H. `docs/BlackBox_20260327.md` explicitly says a single B-initiator round produced only 7 unique pairs and full 28-pair coverage requires initiator rotation.

Recommended thesis wording:

> Tag-to-anchor ranging used broadcast Alt SS-TWR: a tag broadcast poll with an anchor mask followed by rank-slotted anchor responses. In contrast, the AutoPos inter-anchor matrix used traditional anchor-to-anchor unicast SS-TWR under a rotating-master schedule. A single master produces one star row of the distance matrix; rotating the master role across A--H yields the complete unordered inter-anchor graph used by the layout solver.

Do not write:

> Broadcast SS-TWR generates the dense inter-anchor graph in one scheduled sweep.

That sentence is wrong for this firmware branch. The dense graph comes from rotating-master unicast matrix sweeps.

## 7. Suggested Method structure for the thesis

### 7.1 Ranging acquisition

Write two sub-subsections:

1. Tag/static broadcast Alt SS-TWR.
2. Anchor matrix rotating-master unicast SS-TWR.

Use the same SS-TWR timing formula for both, but distinguish the MAC schedule.

### 7.2 Inter-anchor fusion

Describe V1/V2/V3 fusion separately from layout solving:

```text
V1 fusion: pooled mean
V2 fusion: inverse-variance directional mean
V3 fusion: median/MAD robust directional fusion
```

### 7.3 Layout solvers

Use a table:

```text
v1-old   V1 fusion + classical MDS only, zero delay
v2       V2 fusion + MDS + no-delay NLS + weak z regularizer
v3-lite  V3 fusion + MDS + no-delay NLS
v3-full  V3 fusion + Tukey IRLS + per-anchor delay update
v4-io    V3 fusion + bounded-delay Huber LS + two-layer priors
```

### 7.4 Tag solvers

Use:

```text
T1 robust WLS
T2 T1 + quality sigma inflation
T3 T2 + residual-history sigma inflation + weak previous-position prior
T4 full-anchor T1, low-redundancy T3-style
```

### 7.5 Official V1 note

Add a footnote or short paragraph:

> Although some repository scripts contain a `v1_soft_iterative` layout, it was not used as the official V1 row in the 2026-05-28 Erlangen analysis. The official row is `v1-old`, whose saved layout declares `archive_v1_classical_mds_only`.

## 8. Evidence folder

I copied the most relevant source files into:

```text
autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/reports/method_source_evidence/
```

Use those copies when writing the Method chapter if you want a stable bundle separate from the live tree.
