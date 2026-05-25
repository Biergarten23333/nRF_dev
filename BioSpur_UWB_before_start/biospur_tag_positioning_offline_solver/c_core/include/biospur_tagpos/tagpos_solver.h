#ifndef BIOSPUR_TAGPOS_SOLVER_H
#define BIOSPUR_TAGPOS_SOLVER_H

#ifdef __cplusplus
extern "C" {
#endif

#define BIOSPUR_TAGPOS_OK 0
#define BIOSPUR_TAGPOS_ERR_BAD_INPUT -1
#define BIOSPUR_TAGPOS_ERR_TOO_FEW_ANCHORS -2
#define BIOSPUR_TAGPOS_ERR_SINGULAR -3

#define BIOSPUR_TAGPOS_LOSS_HUBER 1
#define BIOSPUR_TAGPOS_LOSS_TUKEY 2

typedef enum BiospurTagposMethod {
    BIOSPUR_TAGPOS_T1_ROBUST_WLS = 1,
    BIOSPUR_TAGPOS_T2_QUALITY_WLS = 2,
    BIOSPUR_TAGPOS_T3_DYNAMIC_STABLE = 3,
    BIOSPUR_TAGPOS_T4_NLOS_HEALTH = 4
} BiospurTagposMethod;

typedef struct BiospurTagposConfig {
    int method;
    int max_iters;
    double huber_k;
    double min_sigma_mm;
    double convergence_mm;
    double max_step_mm;
    double quality_penalty_scale;
    double quality_penalty_cap;
    double residual_ema_start_mm;
    double residual_penalty_scale;
    double residual_penalty_cap;
    double reject_abs_threshold_mm;
    double reject_min_improvement_mm;
    double reject_improvement_ratio;
    double temporal_prior_sigma_mm;
    int robust_loss;
    double tukey_c;
} BiospurTagposConfig;

typedef struct BiospurTagposResult {
    int status;
    int method;
    int n_input;
    int n_used;
    int iterations;
    int rejected_index;
    double xyz_mm[3];
    double residual_rms_mm;
    double residual_p95_abs_mm;
    double max_abs_residual_mm;
} BiospurTagposResult;

void biospur_tagpos_default_config(BiospurTagposConfig *cfg, int method);

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
    const BiospurTagposConfig *cfg,
    double *out_xyz_mm,
    double *out_residuals_mm,
    int *out_used_mask,
    BiospurTagposResult *out_result
);

const char *biospur_tagpos_method_name(int method);

#ifdef __cplusplus
}
#endif

#endif
