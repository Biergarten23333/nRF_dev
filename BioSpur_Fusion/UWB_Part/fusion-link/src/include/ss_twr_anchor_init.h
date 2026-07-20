#ifndef SS_TWR_ANCHOR_INIT_H
#define SS_TWR_ANCHOR_INIT_H

#include <stddef.h>
#include <stdint.h>

int ss_twr_anchor_init_start(unsigned int anchor_id, const uint8_t *peer_ids,
                             size_t peer_count, uint32_t max_sweeps);

/* Apply a runtime TX_POWER preset (MAX|M3|M6|M12|POR) on the anchor. Writes
 * TX_POWER_ID via dwt_write32bitreg and logs "TXPWR set 0x%08X". Does NOT touch
 * DIS_STXP or TC_PGDELAY. Returns 0 (and sets *applied) on success, -EINVAL on
 * bad preset. Same presets/behaviour as the tag (ss_twr_init_tx_power_apply). */
int ss_twr_anchor_init_tx_power_apply(const char *preset, uint32_t *applied);

#endif /* SS_TWR_ANCHOR_INIT_H */
