#include "uwb_motion.h"

#include <math.h>
#include <string.h>

struct uwb_motion_state {
    bool has_prev;
    int32_t prev_x_mm;
    int32_t prev_y_mm;
    int32_t prev_z_mm;
    uint64_t prev_ts_ms;
};

static struct uwb_motion_state uwb_motion_state_data;

void uwb_motion_reset(void)
{
    memset(&uwb_motion_state_data, 0, sizeof(uwb_motion_state_data));
}

bool uwb_motion_update(int32_t x_mm, int32_t y_mm, int32_t z_mm,
                       uint64_t timestamp_ms,
                       struct uwb_motion_sample *sample)
{
    int32_t dx_mm;
    int32_t dy_mm;
    int32_t dz_mm;
    uint64_t dt_ms;
    double dt_s;

    memset(sample, 0, sizeof(*sample));

    if (!uwb_motion_state_data.has_prev || timestamp_ms <= uwb_motion_state_data.prev_ts_ms) {
        uwb_motion_state_data.has_prev = true;
        uwb_motion_state_data.prev_x_mm = x_mm;
        uwb_motion_state_data.prev_y_mm = y_mm;
        uwb_motion_state_data.prev_z_mm = z_mm;
        uwb_motion_state_data.prev_ts_ms = timestamp_ms;
        return false;
    }

    dx_mm = x_mm - uwb_motion_state_data.prev_x_mm;
    dy_mm = y_mm - uwb_motion_state_data.prev_y_mm;
    dz_mm = z_mm - uwb_motion_state_data.prev_z_mm;
    dt_ms = timestamp_ms - uwb_motion_state_data.prev_ts_ms;
    dt_s = (double)dt_ms / 1000.0;

    sample->valid = true;
    sample->x_mm = x_mm;
    sample->y_mm = y_mm;
    sample->z_mm = z_mm;
    sample->dt_ms = (uint32_t)dt_ms;
    sample->displacement_mm = (uint32_t)lround(sqrt(
        (double)dx_mm * (double)dx_mm +
        (double)dy_mm * (double)dy_mm +
        (double)dz_mm * (double)dz_mm));
    sample->vx_mm_s = (int32_t)lround((double)dx_mm / dt_s);
    sample->vy_mm_s = (int32_t)lround((double)dy_mm / dt_s);
    sample->vz_mm_s = (int32_t)lround((double)dz_mm / dt_s);
    sample->speed_mm_s = (uint32_t)lround(sqrt(
        (double)sample->vx_mm_s * (double)sample->vx_mm_s +
        (double)sample->vy_mm_s * (double)sample->vy_mm_s +
        (double)sample->vz_mm_s * (double)sample->vz_mm_s));

    uwb_motion_state_data.prev_x_mm = x_mm;
    uwb_motion_state_data.prev_y_mm = y_mm;
    uwb_motion_state_data.prev_z_mm = z_mm;
    uwb_motion_state_data.prev_ts_ms = timestamp_ms;
    return true;
}
