#include "uwb_ekf.h"

#include <math.h>
#include <string.h>

/* This module is a post-solve linear constant-velocity position filter.
 * It intentionally runs on solved XYZ measurements, not directly on the
 * nonlinear UWB range observations.
 */

#ifndef APP_TAG_EKF_ENABLE
#define APP_TAG_EKF_ENABLE 0U
#endif

#ifndef APP_TAG_EKF_MEAS_STD_MM
#define APP_TAG_EKF_MEAS_STD_MM 25U
#endif

#ifndef APP_TAG_EKF_RESIDUAL_GAIN_PCT
#define APP_TAG_EKF_RESIDUAL_GAIN_PCT 0U
#endif

#ifndef APP_TAG_EKF_PROC_ACCEL_MM_S2
#define APP_TAG_EKF_PROC_ACCEL_MM_S2 250U
#endif

#ifndef APP_TAG_EKF_INIT_POS_STD_MM
#define APP_TAG_EKF_INIT_POS_STD_MM 200U
#endif

#ifndef APP_TAG_EKF_INIT_VEL_STD_MM_S
#define APP_TAG_EKF_INIT_VEL_STD_MM_S 1000U
#endif

#ifndef APP_TAG_EKF_OUTLIER_GATE_MM
#define APP_TAG_EKF_OUTLIER_GATE_MM 0U
#endif

struct uwb_ekf_axis {
    double pos_mm;
    double vel_mm_s;
    double p00;
    double p01;
    double p10;
    double p11;
};

struct uwb_ekf_state {
    bool initialized;
    uint64_t last_timestamp_ms;
    struct uwb_ekf_axis x;
    struct uwb_ekf_axis y;
    struct uwb_ekf_axis z;
};

static struct uwb_ekf_state uwb_ekf_state_data;

static struct uwb_ekf_runtime_params uwb_ekf_default_params(void)
{
    struct uwb_ekf_runtime_params params = {
        .meas_std_mm = APP_TAG_EKF_MEAS_STD_MM,
        .residual_gain_pct = APP_TAG_EKF_RESIDUAL_GAIN_PCT,
        .proc_accel_mm_s2 = APP_TAG_EKF_PROC_ACCEL_MM_S2,
        .outlier_gate_mm = APP_TAG_EKF_OUTLIER_GATE_MM,
    };

    return params;
}

static void uwb_ekf_axis_init(struct uwb_ekf_axis *axis, int32_t measurement_mm)
{
    const double init_pos_var =
        (double)APP_TAG_EKF_INIT_POS_STD_MM * (double)APP_TAG_EKF_INIT_POS_STD_MM;
    const double init_vel_var =
        (double)APP_TAG_EKF_INIT_VEL_STD_MM_S * (double)APP_TAG_EKF_INIT_VEL_STD_MM_S;

    axis->pos_mm = (double)measurement_mm;
    axis->vel_mm_s = 0.0;
    axis->p00 = init_pos_var;
    axis->p01 = 0.0;
    axis->p10 = 0.0;
    axis->p11 = init_vel_var;
}

static double uwb_ekf_measurement_std_mm(uint32_t residual_rms_mm,
                                         uint32_t residual_max_mm,
                                         const struct uwb_ekf_runtime_params *params)
{
    double std_mm = (double)params->meas_std_mm;

    if (params->residual_gain_pct != 0U) {
        const double residual_hint =
            fmax((double)residual_rms_mm, (double)residual_max_mm * 0.5);
        std_mm += residual_hint * ((double)params->residual_gain_pct / 100.0);
    }

    if (std_mm < 1.0) {
        std_mm = 1.0;
    }

    return std_mm;
}

static void uwb_ekf_axis_predict(struct uwb_ekf_axis *axis, double dt_s,
                                 const struct uwb_ekf_runtime_params *params)
{
    const double accel_var =
        (double)params->proc_accel_mm_s2 * (double)params->proc_accel_mm_s2;
    const double dt2 = dt_s * dt_s;
    const double dt3 = dt2 * dt_s;
    const double dt4 = dt2 * dt2;
    const double q00 = 0.25 * dt4 * accel_var;
    const double q01 = 0.5 * dt3 * accel_var;
    const double q11 = dt2 * accel_var;

    const double p00 = axis->p00;
    const double p01 = axis->p01;
    const double p10 = axis->p10;
    const double p11 = axis->p11;

    axis->pos_mm += axis->vel_mm_s * dt_s;

    axis->p00 = p00 + dt_s * (p10 + p01) + dt2 * p11 + q00;
    axis->p01 = p01 + dt_s * p11 + q01;
    axis->p10 = p10 + dt_s * p11 + q01;
    axis->p11 = p11 + q11;
}

static void uwb_ekf_axis_update(struct uwb_ekf_axis *axis, int32_t measurement_mm,
                                double meas_std_mm,
                                const struct uwb_ekf_runtime_params *params)
{
    const double measurement = (double)measurement_mm;
    const double innovation = measurement - axis->pos_mm;
    const double meas_var = meas_std_mm * meas_std_mm;
    const double s = axis->p00 + meas_var;
    double k0;
    double k1;
    double p00;
    double p01;
    double p10;
    double p11;

    if (params->outlier_gate_mm != 0U &&
        fabs(innovation) > (double)params->outlier_gate_mm) {
        return;
    }

    if (s <= 0.0) {
        return;
    }

    k0 = axis->p00 / s;
    k1 = axis->p10 / s;

    axis->pos_mm += k0 * innovation;
    axis->vel_mm_s += k1 * innovation;

    p00 = axis->p00;
    p01 = axis->p01;
    p10 = axis->p10;
    p11 = axis->p11;

    axis->p00 = (1.0 - k0) * p00;
    axis->p01 = (1.0 - k0) * p01;
    axis->p10 = p10 - k1 * p00;
    axis->p11 = p11 - k1 * p01;
}

void uwb_ekf_reset(void)
{
    memset(&uwb_ekf_state_data, 0, sizeof(uwb_ekf_state_data));
}

void uwb_ekf_filter_with_params(int32_t x_mm, int32_t y_mm, int32_t z_mm,
                                uint64_t timestamp_ms,
                                uint32_t residual_rms_mm,
                                uint32_t residual_max_mm,
                                const struct uwb_ekf_runtime_params *params,
                                struct uwb_ekf_sample *sample)
{
    double dt_s = 0.0;
    double meas_std_mm;
    struct uwb_ekf_runtime_params runtime_params;

    memset(sample, 0, sizeof(*sample));

    if (APP_TAG_EKF_ENABLE == 0U) {
        sample->enabled = false;
        sample->valid = true;
        sample->x_mm = x_mm;
        sample->y_mm = y_mm;
        sample->z_mm = z_mm;
        return;
    }

    sample->enabled = true;
    runtime_params = (params != NULL) ? *params : uwb_ekf_default_params();

    if (runtime_params.meas_std_mm == 0U) {
        runtime_params.meas_std_mm = APP_TAG_EKF_MEAS_STD_MM;
    }
    if (runtime_params.proc_accel_mm_s2 == 0U) {
        runtime_params.proc_accel_mm_s2 = APP_TAG_EKF_PROC_ACCEL_MM_S2;
    }

    if (!uwb_ekf_state_data.initialized) {
        uwb_ekf_axis_init(&uwb_ekf_state_data.x, x_mm);
        uwb_ekf_axis_init(&uwb_ekf_state_data.y, y_mm);
        uwb_ekf_axis_init(&uwb_ekf_state_data.z, z_mm);
        uwb_ekf_state_data.last_timestamp_ms = timestamp_ms;
        uwb_ekf_state_data.initialized = true;
    } else if (timestamp_ms > uwb_ekf_state_data.last_timestamp_ms) {
        dt_s = (double)(timestamp_ms - uwb_ekf_state_data.last_timestamp_ms) / 1000.0;
        if (dt_s > 1.0) {
            dt_s = 1.0;
        }
        uwb_ekf_axis_predict(&uwb_ekf_state_data.x, dt_s, &runtime_params);
        uwb_ekf_axis_predict(&uwb_ekf_state_data.y, dt_s, &runtime_params);
        uwb_ekf_axis_predict(&uwb_ekf_state_data.z, dt_s, &runtime_params);
        uwb_ekf_state_data.last_timestamp_ms = timestamp_ms;
    }

    meas_std_mm = uwb_ekf_measurement_std_mm(residual_rms_mm, residual_max_mm,
                                             &runtime_params);

    uwb_ekf_axis_update(&uwb_ekf_state_data.x, x_mm, meas_std_mm,
                        &runtime_params);
    uwb_ekf_axis_update(&uwb_ekf_state_data.y, y_mm, meas_std_mm,
                        &runtime_params);
    uwb_ekf_axis_update(&uwb_ekf_state_data.z, z_mm, meas_std_mm,
                        &runtime_params);

    sample->valid = true;
    sample->x_mm = (int32_t)lround(uwb_ekf_state_data.x.pos_mm);
    sample->y_mm = (int32_t)lround(uwb_ekf_state_data.y.pos_mm);
    sample->z_mm = (int32_t)lround(uwb_ekf_state_data.z.pos_mm);
}

void uwb_ekf_filter(int32_t x_mm, int32_t y_mm, int32_t z_mm,
                    uint64_t timestamp_ms,
                    uint32_t residual_rms_mm,
                    uint32_t residual_max_mm,
                    struct uwb_ekf_sample *sample)
{
    struct uwb_ekf_runtime_params params = uwb_ekf_default_params();

    uwb_ekf_filter_with_params(x_mm, y_mm, z_mm, timestamp_ms,
                               residual_rms_mm, residual_max_mm, &params,
                               sample);
}
