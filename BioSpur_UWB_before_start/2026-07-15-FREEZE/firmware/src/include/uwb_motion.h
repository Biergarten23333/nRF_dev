#ifndef UWB_MOTION_H
#define UWB_MOTION_H

#include <stdbool.h>
#include <stdint.h>

struct uwb_motion_sample {
    bool valid;
    int32_t x_mm;
    int32_t y_mm;
    int32_t z_mm;
    int32_t vx_mm_s;
    int32_t vy_mm_s;
    int32_t vz_mm_s;
    uint32_t speed_mm_s;
    uint32_t displacement_mm;
    uint32_t dt_ms;
};

void uwb_motion_reset(void);
bool uwb_motion_update(int32_t x_mm, int32_t y_mm, int32_t z_mm,
                       uint64_t timestamp_ms,
                       struct uwb_motion_sample *sample);

#endif /* UWB_MOTION_H */
