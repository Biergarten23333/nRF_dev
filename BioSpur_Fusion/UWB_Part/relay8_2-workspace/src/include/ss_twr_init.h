#ifndef SS_TWR_INIT_H
#define SS_TWR_INIT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "uwb_tdma.h"

struct ss_twr_init_poll_tx_stats {
	uint32_t failures;
	int32_t last_error;
	uint32_t slot_sleep_late_skips;
	uint32_t slot_spin_late_skips;
};

struct ss_twr_init_beacon_status {
	uint32_t rx_beacon;
	uint32_t last_counter;
	uint32_t period_mismatch;
	uint32_t missed_windows;
	uint32_t generation_rebases;
	uint32_t dw_anchor_fallbacks;
	uint32_t beacon_rx_arm_failures;
	uint8_t last_generation;
	bool promoted_source_in_use;
	bool locked;
	bool enabled;
	bool dw_anchor;
	uint8_t beacon_win_n;
};

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
void ss_twr_init_poll_tx_stats_snapshot(
	struct ss_twr_init_poll_tx_stats *stats);
void ss_twr_init_beacon_status_snapshot(
	struct ss_twr_init_beacon_status *status);
/* Apply a runtime TX_POWER preset (MAX|M3|M6|M12|POR). Writes TX_POWER_ID via
 * dwt_write32bitreg and logs "TXPWR set 0x%08X". Does NOT touch DIS_STXP or
 * TC_PGDELAY. Returns 0 (and sets *applied) on success, -EINVAL on bad preset. */
int ss_twr_init_tx_power_apply(const char *preset, uint32_t *applied);

/* Runtime gate for per-response RF diagnostics on the tag RX hot path. Default
 * OFF (boot) so ranging timing matches the stable nodiag build; `DIAG ON`
 * enables the diag reads/publish for experiments. Does not affect ranging. */
void ss_twr_init_set_rf_diag_runtime(bool enable);
bool ss_twr_init_rf_diag_runtime_enabled(void);

#endif /* SS_TWR_INIT_H */
