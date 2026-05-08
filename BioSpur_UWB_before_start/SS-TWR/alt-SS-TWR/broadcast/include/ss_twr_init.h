#ifndef SS_TWR_INIT_H
#define SS_TWR_INIT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "uwb_tdma.h"

int ss_twr_init_start_with_config(const struct uwb_tag_runtime_config *config);
int ss_twr_init_start(unsigned int tag_id, const uint8_t *anchor_ids,
                      size_t anchor_count);
int ss_twr_init_tdma_set_slot(uint8_t slot_index);
int ss_twr_init_runtime_configure(const struct uwb_tag_runtime_params *params);
bool ss_twr_init_runtime_config_snapshot(struct uwb_tag_runtime_params *params);

enum ss_twr_init_wand_role {
    SS_TWR_INIT_WAND_ROLE_IDLE = 0,
    SS_TWR_INIT_WAND_ROLE_INIT = 1,
    SS_TWR_INIT_WAND_ROLE_RESP = 2,
};

int ss_twr_init_wand_set_enabled(bool enabled, char label);
int ss_twr_init_wand_set_role(enum ss_twr_init_wand_role role);
int ss_twr_init_wand_set_peers(uint8_t tag_a, uint8_t tag_b, uint8_t tag_c);
int ss_twr_init_wand_request_sweep(uint16_t count);

#endif /* SS_TWR_INIT_H */
