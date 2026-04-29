#include "uwb_anchor_topology.h"

char uwb_anchor_label(uint8_t anchor_id)
{
    if (anchor_id < UWB_MAX_ANCHORS) {
        return (char)('A' + anchor_id);
    }

    return '?';
}

size_t uwb_anchor_schedule_upper_triangle(uint8_t anchor_id, uint8_t *peer_ids,
                                          size_t max_peers)
{
    size_t count = 0U;

    if (peer_ids == NULL || anchor_id >= UWB_MAX_ANCHORS) {
        return 0U;
    }

    for (uint8_t peer_id = (uint8_t)(anchor_id + 1U); peer_id < UWB_MAX_ANCHORS;
         ++peer_id) {
        if (count >= max_peers) {
            break;
        }

        peer_ids[count++] = peer_id;
    }

    return count;
}

size_t uwb_anchor_schedule_all_except_self(uint8_t anchor_id, uint8_t *peer_ids,
                                           size_t max_peers)
{
    size_t count = 0U;

    if (peer_ids == NULL || anchor_id >= UWB_MAX_ANCHORS) {
        return 0U;
    }

    for (uint8_t peer_id = 0U; peer_id < UWB_MAX_ANCHORS; ++peer_id) {
        if (peer_id == anchor_id) {
            continue;
        }

        if (count >= max_peers) {
            break;
        }

        peer_ids[count++] = peer_id;
    }

    return count;
}
