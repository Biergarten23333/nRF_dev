#ifndef UWB_ANCHOR_LAYOUT_H
#define UWB_ANCHOR_LAYOUT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

struct uwb_anchor_pose_mm {
    uint8_t anchor_id;
    char label;
    int32_t x_mm;
    int32_t y_mm;
    int32_t z_mm;
    int32_t d_anchor_mm; /* per-anchor range delay correction (A-referenced, mm) */
};

const struct uwb_anchor_pose_mm *uwb_anchor_layout_get(uint8_t anchor_id);
size_t uwb_anchor_layout_count(void);
bool uwb_anchor_layout_is_lower_plane(uint8_t anchor_id);
bool uwb_anchor_layout_is_upper_plane(uint8_t anchor_id);

#endif /* UWB_ANCHOR_LAYOUT_H */
