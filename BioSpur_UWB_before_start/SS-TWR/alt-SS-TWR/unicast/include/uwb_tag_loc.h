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

enum uwb_tag_loc_subset_policy {
    UWB_TAG_LOC_SUBSET_POLICY_MIN4 = 0,
    UWB_TAG_LOC_SUBSET_POLICY_EXACT4 = 1,
};

int uwb_tag_loc_solve(const struct uwb_tag_measurement *measurements,
                      size_t measurement_count,
                      enum uwb_tag_loc_subset_policy subset_policy,
                      struct uwb_tag_location_result *result);

int uwb_tag_loc_evaluate_solution(const struct uwb_tag_measurement *measurements,
                                  size_t measurement_count,
                                  const uint8_t *anchor_ids,
                                  size_t anchor_id_count,
                                  int32_t x_mm,
                                  int32_t y_mm,
                                  int32_t z_mm,
                                  uint32_t *residual_rms_mm,
                                  uint32_t *residual_max_mm,
                                  uint8_t *lower_count,
                                  uint8_t *upper_count);

#endif /* UWB_TAG_LOC_H */
