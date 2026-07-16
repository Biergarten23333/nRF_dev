#ifndef UWB_EKF_H
#define UWB_EKF_H

#include <stdbool.h>
#include <stdint.h>

struct uwb_ekf_sample {
    bool enabled;
    bool valid;
    int32_t x_mm;
    int32_t y_mm;
    int32_t z_mm;
};

struct uwb_ekf_runtime_params {
    uint32_t meas_std_mm;
    uint32_t residual_gain_pct;
    uint32_t proc_accel_mm_s2;
    uint32_t outlier_gate_mm;
};

/* Legacy "EKF" name: this API currently exposes a post-solve linear
 * constant-velocity position filter, not a range-space nonlinear EKF.
 */

void uwb_ekf_reset(void);

void uwb_ekf_filter(int32_t x_mm, int32_t y_mm, int32_t z_mm,
                    uint64_t timestamp_ms,
                    uint32_t residual_rms_mm,
                    uint32_t residual_max_mm,
                    struct uwb_ekf_sample *sample);

void uwb_ekf_filter_with_params(int32_t x_mm, int32_t y_mm, int32_t z_mm,
                                uint64_t timestamp_ms,
                                uint32_t residual_rms_mm,
                                uint32_t residual_max_mm,
                                const struct uwb_ekf_runtime_params *params,
                                struct uwb_ekf_sample *sample);

#endif /* UWB_EKF_H */
