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

void uwb_ekf_reset(void);

void uwb_ekf_filter(int32_t x_mm, int32_t y_mm, int32_t z_mm,
                    uint64_t timestamp_ms,
                    uint32_t residual_rms_mm,
                    uint32_t residual_max_mm,
                    struct uwb_ekf_sample *sample);

#endif /* UWB_EKF_H */
