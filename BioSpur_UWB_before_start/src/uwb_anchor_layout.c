#include "uwb_anchor_layout.h"

#include "uwb_ss_twr_shared.h"

static const struct uwb_anchor_pose_mm uwb_anchor_layout[UWB_MAX_ANCHORS] = {
    {0U, 'A', 0, 0, 0},
    {1U, 'B', 3670, 0, 0},
    {2U, 'C', 3763, 3666, -1},
    {3U, 'D', -122, 4025, 0},
    {4U, 'E', 68, 286, 1526},
    {5U, 'F', 3613, -81, 1522},
    {6U, 'G', 3878, 3774, 1522},
    {7U, 'H', -36, 3920, 1524},
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
