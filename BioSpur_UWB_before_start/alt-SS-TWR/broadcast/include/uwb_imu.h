#ifndef UWB_IMU_H
#define UWB_IMU_H

#include <stdbool.h>
#include <stdint.h>

struct uwb_imu_sample {
    bool valid;
    int32_t ax_mg;
    int32_t ay_mg;
    int32_t az_mg;
    int32_t norm_mg;
    int32_t gravity_error_mg;
    uint32_t delta_magnitude_mg;
    uint32_t timestamp_ms;
};

int uwb_imu_init(void);
bool uwb_imu_read(struct uwb_imu_sample *sample);

#endif /* UWB_IMU_H */
