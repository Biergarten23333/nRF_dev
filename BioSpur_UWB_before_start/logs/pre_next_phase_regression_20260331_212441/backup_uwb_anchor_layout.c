#include "uwb_anchor_layout.h"

#include "uwb_ss_twr_shared.h"

static const struct uwb_anchor_pose_mm uwb_anchor_layout[UWB_MAX_ANCHORS] = {
    {0U, 'A', 0, 0, 0},
    {1U, 'B', 3785, 0, 0},
    {2U, 'C', 4187, 3695, 1},
    {3U, 'D', 248, 3771, 0},
    {4U, 'E', 214, 71, 1525},
    {5U, 'F', 3635, -70, 1521},
    {6U, 'G', 3966, 3749, 1524},
    {7U, 'H', 145, 3749, 1526},
};

const struct uwb_anchor_pose_mm *uwb_anchor_layout_get(uint8_t anchor_id)
{
    if (anchor_id >= UWB_MAX_ANCHORS) {
        return NULL;
    }

    return &uwb_anchor_layout[anchor_id];
}

size_t uwb_anchor_layout_count(void)
{
    return UWB_MAX_ANCHORS;
}

bool uwb_anchor_layout_is_lower_plane(uint8_t anchor_id)
{
    return anchor_id < 4U;
}

bool uwb_anchor_layout_is_upper_plane(uint8_t anchor_id)
{
    return anchor_id >= 4U && anchor_id < UWB_MAX_ANCHORS;
}
