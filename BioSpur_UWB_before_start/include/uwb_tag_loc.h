#ifndef UWB_TAG_LOC_H
#define UWB_TAG_LOC_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

struct uwb_tag_measurement {
    uint8_t anchor_id;
    uint32_t range_mm;
    uint8_t quality_percent;
    bool valid;
};

struct uwb_tag_location_result {
    bool valid;
    uint8_t used_anchor_count;
    uint8_t lower_anchor_count;
    uint8_t upper_anchor_count;
    uint8_t anchor_ids[8];
    int32_t x_mm;
    int32_t y_mm;
    int32_t z_mm;
    uint32_t residual_rms_mm;
    uint32_t residual_max_mm;
};

int uwb_tag_loc_solve(const struct uwb_tag_measurement *measurements,
                      size_t measurement_count,
                      struct uwb_tag_location_result *result);

#endif /* UWB_TAG_LOC_H */
