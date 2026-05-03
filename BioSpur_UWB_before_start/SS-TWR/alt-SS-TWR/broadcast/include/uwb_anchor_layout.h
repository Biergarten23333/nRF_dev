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
};

const struct uwb_anchor_pose_mm *uwb_anchor_layout_get(uint8_t anchor_id);
size_t uwb_anchor_layout_count(void);
bool uwb_anchor_layout_is_lower_plane(uint8_t anchor_id);
bool uwb_anchor_layout_is_upper_plane(uint8_t anchor_id);
void uwb_anchor_layout_init(void);
bool uwb_anchor_layout_loaded_from_settings(void);
int uwb_anchor_layout_set(uint8_t anchor_id,
			  int32_t x_mm, int32_t y_mm, int32_t z_mm);
int uwb_anchor_layout_commit(void);
int uwb_anchor_layout_reset(void);

#endif /* UWB_ANCHOR_LAYOUT_H */
