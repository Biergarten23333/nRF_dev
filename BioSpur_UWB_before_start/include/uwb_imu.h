#ifndef UWB_IMU_H
#define UWB_IMU_H

#include <stdbool.h>
#include <stdint.h>

struct uwb_imu_sample {
    bool valid;
    int32_t ax_milli_mps2;
    int32_t ay_milli_mps2;
    int32_t az_milli_mps2;
    int32_t norm_milli_mps2;
    int32_t gravity_error_milli_mps2;
    uint32_t delta_magnitude_milli_mps2;
    uint32_t timestamp_ms;
};

int uwb_imu_init(void);
bool uwb_imu_read(struct uwb_imu_sample *sample);

#endif /* UWB_IMU_H */
