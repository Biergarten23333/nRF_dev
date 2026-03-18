#ifndef SS_TWR_ANCHOR_INIT_H
#define SS_TWR_ANCHOR_INIT_H

#include <stddef.h>
#include <stdint.h>

int ss_twr_anchor_init_start(unsigned int anchor_id, const uint8_t *peer_ids,
                             size_t peer_count);

#endif /* SS_TWR_ANCHOR_INIT_H */
