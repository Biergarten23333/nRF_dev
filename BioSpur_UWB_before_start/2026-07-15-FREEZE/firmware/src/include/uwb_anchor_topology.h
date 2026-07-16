#ifndef UWB_ANCHOR_TOPOLOGY_H
#define UWB_ANCHOR_TOPOLOGY_H

#include <stddef.h>
#include <stdint.h>

#include "uwb_ss_twr_shared.h"

char uwb_anchor_label(uint8_t anchor_id);
size_t uwb_anchor_schedule_upper_triangle(uint8_t anchor_id, uint8_t *peer_ids,
                                          size_t max_peers);
size_t uwb_anchor_schedule_all_except_self(uint8_t anchor_id, uint8_t *peer_ids,
                                           size_t max_peers);

#endif /* UWB_ANCHOR_TOPOLOGY_H */
