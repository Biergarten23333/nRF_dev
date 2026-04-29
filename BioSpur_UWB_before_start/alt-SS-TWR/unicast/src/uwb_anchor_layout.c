#include "uwb_anchor_layout.h"

#include "uwb_ss_twr_shared.h"

static const struct uwb_anchor_pose_mm uwb_anchor_layout[UWB_MAX_ANCHORS] = {
    {0U, 'A', 0, 0, 0},
    {1U, 'B', 2563, 0, 0},
    {2U, 'C', 2533, 4420, -8},
    {3U, 'D', -243, 4300, 0},
    {4U, 'E', 32, -74, 1516},
    {5U, 'F', 2588, 137, 1512},
    {6U, 'G', 2453, 4486, 1515},
    {7U, 'H', -245, 4290, 1518},
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
