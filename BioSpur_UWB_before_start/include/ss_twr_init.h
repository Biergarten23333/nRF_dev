#ifndef SS_TWR_INIT_H
#define SS_TWR_INIT_H

#include <stddef.h>
#include <stdint.h>

#include "uwb_tdma.h"

int ss_twr_init_start_with_config(const struct uwb_tag_runtime_config *config);
int ss_twr_init_start(unsigned int tag_id, const uint8_t *anchor_ids,
                      size_t anchor_count);
int ss_twr_init_tdma_set_slot(uint8_t slot_index);

#endif /* SS_TWR_INIT_H */
