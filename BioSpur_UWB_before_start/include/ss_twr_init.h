#ifndef SS_TWR_INIT_H
#define SS_TWR_INIT_H

#include <stddef.h>
#include <stdint.h>

int ss_twr_init_start(unsigned int tag_id, const uint8_t *anchor_ids,
                      size_t anchor_count);

#endif /* SS_TWR_INIT_H */
