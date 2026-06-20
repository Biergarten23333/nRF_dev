#include "biospur_tagpos/tagpos_solver.h"

#include <math.h>
#include <stddef.h>
#include <string.h>

static int is_good(double v) {
    return isfinite(v);
}

static double clamp_double(double v, double lo, double hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

static double vec_norm3(const double v[3]) {
    return sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
}

static int solve_3x3(double a[3][3], double b[3], double x[3]) {
    double m[3][4];
    for (int r = 0; r < 3; ++r) {
        for (int c = 0; c < 3; ++c) m[r][c] = a[r][c];
        m[r][3] = b[r];
    }
    for (int col = 0; col < 3; ++col) {
        int pivot = col;
        double best = fabs(m[col][col]);
        for (int r = col + 1; r < 3; ++r) {
            double v = fabs(m[r][col]);
            if (v > best) {
                best = v;
                pivot = r;
            }
        }
        if (best < 1e-12) return BIOSPUR_TAGPOS_ERR_SINGULAR;
        if (pivot != col) {
            for (int c = col; c < 4; ++c) {
                double tmp = m[col][c];
                m[col][c] = m[pivot][c];
                m[pivot][c] = tmp;
            }
        }
        double div = m[col][col];
        for (int c = col; c < 4; ++c) m[col][c] /= div;
        for (int r = 0; r < 3; ++r) {
            if (r == col) continue;
            double f = m[r][col];
            for (int c = col; c < 4; ++c) m[r][c] -= f * m[col][c];
        }
    }
    x[0] = m[0][3];
    x[1] = m[1][3];
    x[2] = m[2][3];
    return BIOSPUR_TAGPOS_OK;
}

void biospur_tagpos_default_config(BiospurTagposConfig *cfg, int method) {
    if (!cfg) return;
    memset(cfg, 0, sizeof(*cfg));
    cfg->method = method;
    cfg->max_iters = 8;
    cfg->huber_k = 2.0;
    cfg->min_sigma_mm = 5.0;
    cfg->convergence_mm = 0.02;
    cfg->max_step_mm = 500.0;
    cfg->quality_penalty_scale = 1.5;
    cfg->quality_penalty_cap = 4.0;
    cfg->residual_ema_start_mm = 120.0;
    cfg->residual_penalty_scale = 0.50;
    cfg->residual_penalty_cap = 2.5;
    cfg->reject_abs_threshold_mm = 120.0;
    cfg->reject_min_improvement_mm = 20.0;
    cfg->reject_improvement_ratio = 0.75;
    cfg->temporal_prior_sigma_mm = 180.0;
    cfg->robust_loss = BIOSPUR_TAGPOS_LOSS_HUBER;
    cfg->tukey_c = 4.685;
    cfg->huber_delta_mm = 30.0;
}

const char *biospur_tagpos_method_name(int method) {
    switch (method) {
        case BIOSPUR_TAGPOS_T1_ROBUST_WLS:
            return "T1_robust_wls";
        case BIOSPUR_TAGPOS_T2_QUALITY_WLS:
            return "T2_quality_wls";
        case BIOSPUR_TAGPOS_T3_DYNAMIC_STABLE:
            return "T3_dynamic_stable";
        case BIOSPUR_TAGPOS_T4_NLOS_HEALTH:
            return "T4_nlos_health";
        default:
            return "unknown";
    }
}

static double quality_penalty(
    int method,
    const BiospurTagposConfig *cfg,
    const double *quality,
    const double *quality_ema,
    int idx
) {
    if (method < BIOSPUR_TAGPOS_T2_QUALITY_WLS) return 1.0;
    double q = 100.0;
    int have = 0;
    if (quality && is_good(quality[idx]) && quality[idx] > 0.0) {
        q = quality[idx];
        have = 1;
    }
    if (quality_ema && is_good(quality_ema[idx]) && quality_ema[idx] > 0.0) {
        q = have ? (0.5 * q + 0.5 * quality_ema[idx]) : quality_ema[idx];
    }
    q = clamp_double(q, 0.0, 100.0);
    double bad = fmax(0.0, (100.0 - q) / 50.0);
    double penalty = 1.0 + cfg->quality_penalty_scale * bad * bad;
    return clamp_double(penalty, 1.0, cfg->quality_penalty_cap);
}

static double residual_penalty(
    int method,
    const BiospurTagposConfig *cfg,
    const double *residual_ema,
    int idx
) {
    if (method < BIOSPUR_TAGPOS_T3_DYNAMIC_STABLE) return 1.0;
    if (!residual_ema || !is_good(residual_ema[idx]) || residual_ema[idx] <= cfg->residual_ema_start_mm) {
        return 1.0;
    }
    double excess = (residual_ema[idx] - cfg->residual_ema_start_mm) / 80.0;
    double penalty = 1.0 + cfg->residual_penalty_scale * excess;
    return clamp_double(penalty, 1.0, cfg->residual_penalty_cap);
}

static double effective_sigma(
    int method,
    const BiospurTagposConfig *cfg,
    const double *anchor_sigma,
    const double *quality,
    const double *quality_ema,
    const double *residual_ema,
    int idx
) {
    double sigma = 50.0;
    if (anchor_sigma && is_good(anchor_sigma[idx]) && anchor_sigma[idx] > 0.0) {
        sigma = anchor_sigma[idx];
    }
    if (sigma < cfg->min_sigma_mm) sigma = cfg->min_sigma_mm;
    sigma *= quality_penalty(method, cfg, quality, quality_ema, idx);
    sigma *= residual_penalty(method, cfg, residual_ema, idx);
    return sigma;
}

static double robust_weight(const BiospurTagposConfig *cfg, double residual_mm, double sigma_mm) {
    if (cfg->robust_loss == BIOSPUR_TAGPOS_LOSS_LINEAR) {
        return 1.0;
    }
    double rn = residual_mm / fmax(sigma_mm, 1e-12);
    double abs_rn = fabs(rn);
    if (cfg->robust_loss == BIOSPUR_TAGPOS_LOSS_TUKEY) {
        double c = cfg->tukey_c > 0.0 ? cfg->tukey_c : 4.685;
        if (abs_rn >= c) return 0.0;
        double u = rn / c;
        double a = 1.0 - u * u;
        return a * a;
    }
    if (cfg->huber_delta_mm > 0.0) {
        double abs_r = fabs(residual_mm);
        if (abs_r > cfg->huber_delta_mm) {
            return cfg->huber_delta_mm / fmax(abs_r, 1e-12);
        }
        return 1.0;
    }
    if (abs_rn > cfg->huber_k) {
        return cfg->huber_k / fmax(abs_rn, 1e-12);
    }
    return 1.0;
}

static int count_used(int n, int exclude_index) {
    return n - ((exclude_index >= 0 && exclude_index < n) ? 1 : 0);
}

static void fill_used_mask(int n, int exclude_index, int *mask) {
    if (!mask) return;
    for (int i = 0; i < n; ++i) mask[i] = (i == exclude_index) ? 0 : 1;
}

static double percentile_abs(double *vals, int n, double pct) {
    if (n <= 0) return NAN;
    for (int i = 1; i < n; ++i) {
        double key = vals[i];
        int j = i - 1;
        while (j >= 0 && vals[j] > key) {
            vals[j + 1] = vals[j];
            --j;
        }
        vals[j + 1] = key;
    }
    double pos = (pct / 100.0) * (double)(n - 1);
    int lo = (int)floor(pos);
    int hi = (int)ceil(pos);
    if (hi >= n) hi = n - 1;
    double frac = pos - (double)lo;
    return vals[lo] * (1.0 - frac) + vals[hi] * frac;
}

static int solve_once(
    const double *anchor_xyz,
    const double *ranges,
    const double *anchor_delay,
    const double *anchor_sigma,
    const double *quality,
    const double *quality_ema,
    const double *residual_ema,
    int n,
    const double *x0,
    double tag_delay,
    const BiospurTagposConfig *cfg,
    int exclude_index,
    double out_xyz[3],
    double *out_residuals,
    int *out_used_mask,
    BiospurTagposResult *result
) {
    int n_used = count_used(n, exclude_index);
    if (n_used < 4) return BIOSPUR_TAGPOS_ERR_TOO_FEW_ANCHORS;

    double p[3] = {0.0, 0.0, 0.0};
    if (x0 && is_good(x0[0]) && is_good(x0[1]) && is_good(x0[2])) {
        p[0] = x0[0];
        p[1] = x0[1];
        p[2] = x0[2];
    } else {
        for (int i = 0; i < n; ++i) {
            if (i == exclude_index) continue;
            p[0] += anchor_xyz[3 * i + 0];
            p[1] += anchor_xyz[3 * i + 1];
            p[2] += anchor_xyz[3 * i + 2];
        }
        p[0] /= (double)n_used;
        p[1] /= (double)n_used;
        p[2] /= (double)n_used;
    }

    int iterations = 0;
    for (int it = 0; it < cfg->max_iters; ++it) {
        double h[3][3] = {{1e-9, 0.0, 0.0}, {0.0, 1e-9, 0.0}, {0.0, 0.0, 1e-9}};
        double g[3] = {0.0, 0.0, 0.0};
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
            double pred = dist + delay + tag_delay;
            double residual = pred - ranges[i];
            double sigma = effective_sigma(cfg->method, cfg, anchor_sigma, quality, quality_ema, residual_ema, i);
            double rn = residual / sigma;
            double weight = robust_weight(cfg, residual, sigma);
            if (weight <= 0.0) continue;
            double sqrt_weight = sqrt(weight);
            double scale = sqrt_weight / sigma;
            double j[3] = {diff[0] / dist * scale, diff[1] / dist * scale, diff[2] / dist * scale};
            double r = rn * sqrt_weight;
            for (int a = 0; a < 3; ++a) {
                g[a] += j[a] * r;
                for (int b = 0; b < 3; ++b) {
                    h[a][b] += j[a] * j[b];
                }
            }
            ++rows;
        }
        if (rows < 3) return BIOSPUR_TAGPOS_ERR_TOO_FEW_ANCHORS;
        if (
            cfg->method >= BIOSPUR_TAGPOS_T3_DYNAMIC_STABLE
            && x0
            && cfg->temporal_prior_sigma_mm > 0.0
            && is_good(x0[0])
            && is_good(x0[1])
            && is_good(x0[2])
        ) {
            double inv_sigma = 1.0 / cfg->temporal_prior_sigma_mm;
            double inv_var = inv_sigma * inv_sigma;
            for (int a = 0; a < 3; ++a) {
                double r = (p[a] - x0[a]) * inv_sigma;
                h[a][a] += inv_var;
                g[a] += inv_sigma * r;
            }
        }
        double rhs[3] = {-g[0], -g[1], -g[2]};
        double step[3] = {0.0, 0.0, 0.0};
        int rc = solve_3x3(h, rhs, step);
        if (rc != BIOSPUR_TAGPOS_OK) return rc;
        double step_norm = vec_norm3(step);
        if (step_norm > cfg->max_step_mm && step_norm > 1e-9) {
            double s = cfg->max_step_mm / step_norm;
            step[0] *= s;
            step[1] *= s;
            step[2] *= s;
            step_norm = cfg->max_step_mm;
        }
        p[0] += step[0];
        p[1] += step[1];
        p[2] += step[2];
        iterations = it + 1;
        if (step_norm < cfg->convergence_mm) break;
    }

    fill_used_mask(n, exclude_index, out_used_mask);
    double sum_sq = 0.0;
    double max_abs = 0.0;
    double abs_vals_stack[64];
    double *abs_vals = abs_vals_stack;
    if (n > 64) abs_vals = NULL;
    int k = 0;
    for (int i = 0; i < n; ++i) {
        double diff[3] = {
            p[0] - anchor_xyz[3 * i + 0],
            p[1] - anchor_xyz[3 * i + 1],
            p[2] - anchor_xyz[3 * i + 2],
        };
        double delay = (anchor_delay && is_good(anchor_delay[i])) ? anchor_delay[i] : 0.0;
        double residual = vec_norm3(diff) + delay + tag_delay - ranges[i];
        if (out_residuals) out_residuals[i] = residual;
        if (i == exclude_index) continue;
        double ar = fabs(residual);
        sum_sq += residual * residual;
        if (ar > max_abs) max_abs = ar;
        if (abs_vals) abs_vals[k] = ar;
        ++k;
    }

    out_xyz[0] = p[0];
    out_xyz[1] = p[1];
    out_xyz[2] = p[2];
    if (result) {
        result->status = BIOSPUR_TAGPOS_OK;
        result->method = cfg->method;
        result->n_input = n;
        result->n_used = n_used;
        result->iterations = iterations;
        result->rejected_index = exclude_index;
        result->xyz_mm[0] = p[0];
        result->xyz_mm[1] = p[1];
        result->xyz_mm[2] = p[2];
        result->residual_rms_mm = (k > 0) ? sqrt(sum_sq / (double)k) : NAN;
        result->max_abs_residual_mm = max_abs;
        result->residual_p95_abs_mm = abs_vals ? percentile_abs(abs_vals, k, 95.0) : NAN;
    }
    return BIOSPUR_TAGPOS_OK;
}

int biospur_tagpos_solve_frame(
    const double *anchor_xyz_mm,
    const double *ranges_mm,
    const double *anchor_delay_mm,
    const double *anchor_sigma_mm,
    const double *quality_percent,
    const double *quality_ema_percent,
    const double *residual_ema_abs_mm,
    int n,
    const double *x0_mm,
    double tag_delay_mm,
    const BiospurTagposConfig *cfg_in,
    double *out_xyz_mm,
    double *out_residuals_mm,
    int *out_used_mask,
    BiospurTagposResult *out_result
) {
    if (!anchor_xyz_mm || !ranges_mm || !out_xyz_mm || n <= 0) {
        return BIOSPUR_TAGPOS_ERR_BAD_INPUT;
    }
    BiospurTagposConfig cfg_local;
    if (cfg_in) {
        cfg_local = *cfg_in;
    } else {
        biospur_tagpos_default_config(&cfg_local, BIOSPUR_TAGPOS_T1_ROBUST_WLS);
    }
    if (cfg_local.method < BIOSPUR_TAGPOS_T1_ROBUST_WLS || cfg_local.method > BIOSPUR_TAGPOS_T4_NLOS_HEALTH) {
        cfg_local.method = BIOSPUR_TAGPOS_T1_ROBUST_WLS;
    }
    if (cfg_local.max_iters <= 0) cfg_local.max_iters = 8;
    if (cfg_local.huber_k <= 0.0) cfg_local.huber_k = 2.0;
    if (
        cfg_local.robust_loss != BIOSPUR_TAGPOS_LOSS_HUBER
        && cfg_local.robust_loss != BIOSPUR_TAGPOS_LOSS_TUKEY
    ) {
        cfg_local.robust_loss = BIOSPUR_TAGPOS_LOSS_HUBER;
    }
    if (cfg_local.tukey_c <= 0.0) cfg_local.tukey_c = 4.685;
    if (cfg_local.min_sigma_mm <= 0.0) cfg_local.min_sigma_mm = 5.0;
    if (cfg_local.convergence_mm <= 0.0) cfg_local.convergence_mm = 0.02;
    if (cfg_local.max_step_mm <= 0.0) cfg_local.max_step_mm = 500.0;

    if (n < 4) {
        if (out_result) {
            memset(out_result, 0, sizeof(*out_result));
            out_result->status = BIOSPUR_TAGPOS_ERR_TOO_FEW_ANCHORS;
            out_result->method = cfg_local.method;
            out_result->n_input = n;
            out_result->rejected_index = -1;
        }
        return BIOSPUR_TAGPOS_ERR_TOO_FEW_ANCHORS;
    }

    double base_xyz[3] = {0.0, 0.0, 0.0};
    BiospurTagposResult base_result;
    int rc = solve_once(
        anchor_xyz_mm, ranges_mm, anchor_delay_mm, anchor_sigma_mm, quality_percent,
        quality_ema_percent, residual_ema_abs_mm, n, x0_mm, tag_delay_mm, &cfg_local,
        -1, base_xyz, out_residuals_mm, out_used_mask, &base_result
    );
    if (rc != BIOSPUR_TAGPOS_OK) {
        if (out_result) {
            memset(out_result, 0, sizeof(*out_result));
            out_result->status = rc;
            out_result->method = cfg_local.method;
            out_result->n_input = n;
            out_result->rejected_index = -1;
        }
        return rc;
    }

    out_xyz_mm[0] = base_xyz[0];
    out_xyz_mm[1] = base_xyz[1];
    out_xyz_mm[2] = base_xyz[2];
    if (out_result) *out_result = base_result;
    return BIOSPUR_TAGPOS_OK;
}
