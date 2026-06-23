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
int ss_twr_init_cir_mode_set(enum uwb_tag_cir_mode mode);
enum uwb_tag_cir_mode ss_twr_init_cir_mode_get(void);
const char *ss_twr_init_cir_mode_label(enum uwb_tag_cir_mode mode);
int ss_twr_init_cir_mode_parse(const char *text, enum uwb_tag_cir_mode *mode);

#endif /* SS_TWR_INIT_H */
