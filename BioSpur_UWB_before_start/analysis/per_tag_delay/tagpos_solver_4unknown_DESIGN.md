# Design doc — `tagpos_solver.c` 4-unknown d_tag co-estimation

**Status: PROPOSED — not applied.** This is the exact code for the C-solver change,
for review before it touches `biospur_tag_positioning_offline_solver/c_core/`.

**Read the validation first:** [REPORT.md](REPORT.md) shows the three wand tags'
d_tag medians span only ~43 mm (well within their ~18 mm per-frame std), so this
change does **not** fix the current caliper failure. Ship it with
`estimate_d_tag = 0` (default OFF); it is a correctness / optionality upgrade that
earns its keep on a tag with a genuinely miscalibrated antenna, not a lever for
the CCF4–955A miss. The default-OFF path is byte-for-byte the current V4-IO/T4
behavior (proven below).

---

## 0. Relationship to the existing `tag_delay_mm` input

The solver **already** takes a fixed per-tag `tag_delay_mm` (line 289:
`pred = dist + delay + tag_delay`) that flows in from
`layout.tag_delay_mm` / `TagPositionSolver(tag_delay_by_tag=...)`. That is the
**batch / approach-B** knob (a value locked once and applied to every frame).

This change adds an **estimated per-frame correction** `p[3]` **on top of** that
fixed input:

```
pred_i = dist_i + delay_i + tag_delay + p[3]
                            ^^^^^^^^^   ^^^^
                            fixed batch  estimated correction (new 4th unknown)
```

So the two compose cleanly: lock a batch `tag_delay`, and still let the solver
track any residual per-frame delay. `result.d_tag_mm` reports **the estimated
correction** `p[3]` (0.0 when `estimate_d_tag == 0`), not the fixed input.

---

## 1. Header — `include/biospur_tagpos/tagpos_solver.h`

Append two fields to `BiospurTagposConfig` (at the **end**, to keep the ABI/field
order append-only for the ctypes mirror):

```c
    double rf_snr_ref;         /* FP-SNR at/above which the RF sigma multiplier = 1.0 (default 10) */
    double rf_sigma_mult_cap;  /* max RF sigma inflation for a fully-NLOS link (default 10) */
    /* --- NEW: 4th-unknown per-tag antenna-delay co-estimation --- */
    double d_tag_prior_sigma_mm; /* soft prior sigma on the co-estimated tag delay (default 300, APS014 3-sigma) */
    int    estimate_d_tag;       /* 1 = co-estimate d_tag as a 4th unknown; 0 = 3-unknown (default 0) */
} BiospurTagposConfig;
```

> `int` (not C99 `bool`) mirrors the existing `method` / `robust_loss` flags and
> maps directly to `ctypes.c_int` with no `<stdbool.h>` ABI ambiguity.

Append one field to `BiospurTagposResult` (also at the end):

```c
    double max_abs_residual_mm;
    double d_tag_mm;             /* NEW: estimated per-frame tag-delay correction (0 if not estimated) */
} BiospurTagposResult;
```

The public `biospur_tagpos_solve_frame(...)` signature is **unchanged** — the
estimated value rides out through `out_result->d_tag_mm`.

---

## 2. `default_config` — set the new defaults (default OFF)

In `biospur_tagpos_default_config`, after the `rf_*` lines:

```c
    cfg->rf_snr_ref = 10.0;
    cfg->rf_sigma_mult_cap = 10.0;
    cfg->d_tag_prior_sigma_mm = 300.0;  /* APS014 uncalibrated 3-sigma */
    cfg->estimate_d_tag = 0;            /* OFF -> exact 3-unknown V4-IO/T4 behavior */
```

---

## 3. New `solve_4x4` — partial-pivot Gaussian elimination

Add next to `solve_3x3` (same algorithm, one more dimension):

```c
static int solve_4x4(double a[4][4], double b[4], double x[4]) {
    double m[4][5];
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) m[r][c] = a[r][c];
        m[r][4] = b[r];
    }
    for (int col = 0; col < 4; ++col) {
        int pivot = col;
        double best = fabs(m[col][col]);
        for (int r = col + 1; r < 4; ++r) {
            double v = fabs(m[r][col]);
            if (v > best) { best = v; pivot = r; }
        }
        if (best < 1e-12) return BIOSPUR_TAGPOS_ERR_SINGULAR;
        if (pivot != col) {
            for (int c = col; c < 5; ++c) {
                double tmp = m[col][c];
                m[col][c] = m[pivot][c];
                m[pivot][c] = tmp;
            }
        }
        double div = m[col][col];
        for (int c = col; c < 5; ++c) m[col][c] /= div;
        for (int r = 0; r < 4; ++r) {
            if (r == col) continue;
            double f = m[r][col];
            for (int c = col; c < 5; ++c) m[r][c] -= f * m[col][c];
        }
    }
    x[0] = m[0][4]; x[1] = m[1][4]; x[2] = m[2][4]; x[3] = m[3][4];
    return BIOSPUR_TAGPOS_OK;
}
```

---

## 4. `solve_once` — the 3→4 unknown generalization

The change is confined to `solve_once`. `dim` = 3 or 4 selects behavior; every
index-3 term is gated on `estimate_d_tag`, so `dim == 3` is the current code path
untouched. Below is the full rewritten body of `solve_once` with the deltas
marked `/* NEW */` / `/* CHANGED */`.

```c
static int solve_once(
    const double *anchor_xyz, const double *ranges, const double *anchor_delay,
    const double *anchor_sigma, const double *quality, const double *quality_ema,
    const double *residual_ema, const double *rf_quality, int n, const double *x0,
    double tag_delay, const BiospurTagposConfig *cfg, int exclude_index,
    double out_xyz[3], double *out_residuals, int *out_used_mask,
    BiospurTagposResult *result
) {
    int n_used = count_used(n, exclude_index);
    if (n_used < 4) return BIOSPUR_TAGPOS_ERR_TOO_FEW_ANCHORS;

    const int est = cfg->estimate_d_tag ? 1 : 0;   /* NEW */
    const int dim = est ? 4 : 3;                    /* NEW */

    double p[4] = {0.0, 0.0, 0.0, 0.0};             /* CHANGED: p[4], p[3]=d_tag init 0 */
    if (x0 && is_good(x0[0]) && is_good(x0[1]) && is_good(x0[2])) {
        p[0] = x0[0]; p[1] = x0[1]; p[2] = x0[2];
    } else {
        for (int i = 0; i < n; ++i) {
            if (i == exclude_index) continue;
            p[0] += anchor_xyz[3 * i + 0];
            p[1] += anchor_xyz[3 * i + 1];
            p[2] += anchor_xyz[3 * i + 2];
        }
        p[0] /= (double)n_used; p[1] /= (double)n_used; p[2] /= (double)n_used;
    }
    /* NOTE: x0 has only 3 elements (position). d_tag always warm-starts at 0. */

    int iterations = 0;
    for (int it = 0; it < cfg->max_iters; ++it) {
        double h[4][4] = {{1e-9,0,0,0},{0,1e-9,0,0},{0,0,1e-9,0},{0,0,0,1e-9}}; /* CHANGED 4x4 */
        double g[4] = {0.0, 0.0, 0.0, 0.0};                                     /* CHANGED */
        int rows = 0;
        for (int i = 0; i < n; ++i) {
            if (i == exclude_index) continue;
            double diff[3] = {
                p[0] - anchor_xyz[3 * i + 0],
                p[1] - anchor_xyz[3 * i + 1],
                p[2] - anchor_xyz[3 * i + 2],
            };
            double dist = vec_norm3(diff);
            if (dist < 1e-6) continue;
            double delay = (anchor_delay && is_good(anchor_delay[i])) ? anchor_delay[i] : 0.0;
            double pred = dist + delay + tag_delay + p[3];   /* CHANGED: + p[3] (0 when est==0) */
            double residual = pred - ranges[i];
            double sigma = effective_sigma(cfg->method, cfg, anchor_sigma, quality,
                                           quality_ema, residual_ema, rf_quality, i);
            double rn = residual / sigma;
            double weight = robust_weight(cfg, residual, sigma);
            if (weight <= 0.0) continue;
            double sqrt_weight = sqrt(weight);
            double scale = sqrt_weight / sigma;
            double j[4] = {                                  /* CHANGED: j[4] */
                diff[0] / dist * scale,
                diff[1] / dist * scale,
                diff[2] / dist * scale,
                est ? scale : 0.0,                           /* NEW: d_tag column = 1.0 * scale */
            };
            double r = rn * sqrt_weight;
            for (int a = 0; a < dim; ++a) {                  /* CHANGED: dim */
                g[a] += j[a] * r;
                for (int b = 0; b < dim; ++b) h[a][b] += j[a] * j[b];
            }
            ++rows;
        }
        if (rows < 3) return BIOSPUR_TAGPOS_ERR_TOO_FEW_ANCHORS;

        /* temporal prior on POSITION only (unchanged; never touches index 3) */
        if (cfg->method >= BIOSPUR_TAGPOS_T3_DYNAMIC_STABLE && x0
            && cfg->temporal_prior_sigma_mm > 0.0
            && is_good(x0[0]) && is_good(x0[1]) && is_good(x0[2])) {
            double inv_sigma = 1.0 / cfg->temporal_prior_sigma_mm;
            double inv_var = inv_sigma * inv_sigma;
            for (int a = 0; a < 3; ++a) {
                double r = (p[a] - x0[a]) * inv_sigma;
                h[a][a] += inv_var;
                g[a] += inv_sigma * r;
            }
        }

        /* NEW: soft prior on d_tag (row r_prior = d_tag / sigma_prior) */
        if (est) {
            double sp = (cfg->d_tag_prior_sigma_mm > 0.0) ? cfg->d_tag_prior_sigma_mm : 300.0;
            double inv_var = 1.0 / (sp * sp);
            h[3][3] += inv_var;
            g[3]    += p[3] * inv_var;
        }

        double rhs[4] = {-g[0], -g[1], -g[2], -g[3]};        /* CHANGED */
        double step[4] = {0.0, 0.0, 0.0, 0.0};
        int rc;
        if (dim == 4) {                                      /* CHANGED: pick solver by dim */
            double h4[4][4], b4[4];
            memcpy(h4, h, sizeof(h4));
            b4[0]=rhs[0]; b4[1]=rhs[1]; b4[2]=rhs[2]; b4[3]=rhs[3];
            rc = solve_4x4(h4, b4, step);
        } else {
            double h3[3][3] = {{h[0][0],h[0][1],h[0][2]},
                               {h[1][0],h[1][1],h[1][2]},
                               {h[2][0],h[2][1],h[2][2]}};
            double b3[3] = {rhs[0], rhs[1], rhs[2]};
            rc = solve_3x3(h3, b3, step);                    /* exact current arithmetic */
        }
        if (rc != BIOSPUR_TAGPOS_OK) return rc;

        double step_norm = 0.0;                              /* CHANGED: dim-D norm */
        for (int a = 0; a < dim; ++a) step_norm += step[a] * step[a];
        step_norm = sqrt(step_norm);
        if (step_norm > cfg->max_step_mm && step_norm > 1e-9) {
            double s = cfg->max_step_mm / step_norm;
            for (int a = 0; a < dim; ++a) step[a] *= s;
            step_norm = cfg->max_step_mm;
        }
        for (int a = 0; a < dim; ++a) p[a] += step[a];       /* CHANGED: updates p[3] too when est */
        iterations = it + 1;
        if (step_norm < cfg->convergence_mm) break;
    }

    fill_used_mask(n, exclude_index, out_used_mask);
    double sum_sq = 0.0, max_abs = 0.0;
    double abs_vals_stack[64];
    double *abs_vals = (n > 64) ? NULL : abs_vals_stack;
    int k = 0;
    for (int i = 0; i < n; ++i) {
        double diff[3] = {
            p[0] - anchor_xyz[3 * i + 0],
            p[1] - anchor_xyz[3 * i + 1],
            p[2] - anchor_xyz[3 * i + 2],
        };
        double delay = (anchor_delay && is_good(anchor_delay[i])) ? anchor_delay[i] : 0.0;
        double residual = vec_norm3(diff) + delay + tag_delay + p[3] - ranges[i]; /* CHANGED: + p[3] */
        if (out_residuals) out_residuals[i] = residual;
        if (i == exclude_index) continue;
        double ar = fabs(residual);
        sum_sq += residual * residual;
        if (ar > max_abs) max_abs = ar;
        if (abs_vals) abs_vals[k] = ar;
        ++k;
    }

    out_xyz[0] = p[0]; out_xyz[1] = p[1]; out_xyz[2] = p[2];
    if (result) {
        result->status = BIOSPUR_TAGPOS_OK;
        result->method = cfg->method;
        result->n_input = n;
        result->n_used = n_used;
        result->iterations = iterations;
        result->rejected_index = exclude_index;
        result->xyz_mm[0] = p[0]; result->xyz_mm[1] = p[1]; result->xyz_mm[2] = p[2];
        result->residual_rms_mm = (k > 0) ? sqrt(sum_sq / (double)k) : NAN;
        result->max_abs_residual_mm = max_abs;
        result->residual_p95_abs_mm = abs_vals ? percentile_abs(abs_vals, k, 95.0) : NAN;
        result->d_tag_mm = est ? p[3] : 0.0;             /* NEW */
    }
    return BIOSPUR_TAGPOS_OK;
}
```

`biospur_tagpos_solve_frame` needs no change beyond making sure the `n < 4`
early-out zeroes the new field (it already `memset`s the whole struct, so
`d_tag_mm` is 0 automatically — no edit required there).

---

## 5. ctypes mirror — `biospur_tag_positioning_offline_solver/c_solver.py`

Keep field order identical to the C structs (append-only):

```python
class CConfig(ctypes.Structure):
    _fields_ = [
        # ... existing fields, unchanged ...
        ("rf_snr_ref", ctypes.c_double),
        ("rf_sigma_mult_cap", ctypes.c_double),
        ("d_tag_prior_sigma_mm", ctypes.c_double),   # NEW
        ("estimate_d_tag", ctypes.c_int),            # NEW
    ]

class CResult(ctypes.Structure):
    _fields_ = [
        # ... existing fields, unchanged ...
        ("max_abs_residual_mm", ctypes.c_double),
        ("d_tag_mm", ctypes.c_double),               # NEW
    ]
```

In `make_c_config` (defaults come from `biospur_tagpos_default_config`; override
only when the SolverConfig asks):

```python
    cfg.estimate_d_tag = int(bool(getattr(config, "estimate_d_tag", False)))
    cfg.d_tag_prior_sigma_mm = float(getattr(config, "d_tag_prior_sigma_mm", 300.0))
```

Optional plumbing (secondary — only if you want it exposed through the Python
solver, not required by the C change):
- `models.SolverConfig`: add `estimate_d_tag: bool = False` and
  `d_tag_prior_sigma_mm: float = 300.0`.
- `models.SolveResult`: add `d_tag_mm: float = 0.0`.
- `TagPositionSolver.solve_frame`: set
  `d_tag_mm=float(out_result.d_tag_mm)` on the returned `SolveResult`.

---

## 6. Backward-compatibility guarantee (`estimate_d_tag == 0`)

With `est == 0`: `dim == 3`; `p[3]` is initialized to 0 and **never updated**
(the update loop runs `a < 3`); `j[3] == 0` and is never accumulated (`b,a < 3`);
the d_tag prior block is skipped; `pred`/`residual` add `p[3] == 0`; the solver
branch takes the `solve_3x3` path with the **same partial-pivot arithmetic** on
the same 3×3 `h`/`rhs`; `result->d_tag_mm == 0.0`. The output is therefore
identical to the current V4-IO/T4 code path — no numerical drift.

**Verify after implementing:** rebuild the `.so` and re-run
`analysis/v5u5_vs_v4iot4/compare.py`; the A (V4-io+T4) column must be unchanged
to the printed precision.

---

## 7. Test hook

Add a targeted check: run the same frame twice, `estimate_d_tag=0` vs `=1`, with a
tag whose ranges have a synthetic +150 mm common-mode offset injected. Expect
`estimate_d_tag=1` to recover `d_tag_mm ≈ +150` and return the position it would
have found without the offset; `estimate_d_tag=0` to smear the 150 mm into z.
```
