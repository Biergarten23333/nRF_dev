#ifndef UWB_TDMA_H
#define UWB_TDMA_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "uwb_ss_twr_shared.h"

#define UWB_TAG_FIXED_ANCHOR_MAX 4U
#define UWB_TAG_ACTIVE_ANCHOR_MAX 4U
#define UWB_TAG_STANDBY_ANCHOR_MAX 2U
#define UWB_TAG_RESERVE_ANCHOR_MAX 2U

enum uwb_tag_positioning_mode {
	UWB_TAG_POSITIONING_MODE_DYNAMIC = 0U,
	UWB_TAG_POSITIONING_MODE_RESERVED_1 = 1U,
	UWB_TAG_POSITIONING_MODE_RESERVED_2 = 2U,
	UWB_TAG_POSITIONING_MODE_IDLE = 3U,
	UWB_TAG_POSITIONING_MODE_RESERVED_4 = 4U,
	UWB_TAG_POSITIONING_MODE_RESERVED_5 = 5U,
};

#define UWB_TAG_MODE_RUN UWB_TAG_POSITIONING_MODE_DYNAMIC
#define UWB_TAG_MODE_IDLE UWB_TAG_POSITIONING_MODE_IDLE
#define UWB_TAG_MODE_RANGE UWB_TAG_MODE_RUN

enum uwb_tag_cir_mode {
	UWB_TAG_CIR_MODE_OFF = 0U,
	UWB_TAG_CIR_MODE_COMPACT = 1U,
	UWB_TAG_CIR_MODE_FULL = 2U,
};

enum uwb_tag_anchor_selection_mode {
	UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2 = 0U,
	UWB_TAG_ANCHOR_SELECTION_RESERVED_1 = 1U,
};

enum uwb_tag_slot_source {
	UWB_TAG_SLOT_SOURCE_BUILD = 0U,
	UWB_TAG_SLOT_SOURCE_SETTINGS = 1U,
	UWB_TAG_SLOT_SOURCE_MASTER = 2U,
};

struct uwb_tdma_schedule {
	bool enabled;
	uint8_t slot_index;
	uint8_t slot_count;
	uint16_t slot_mask;
	uint16_t slot_period_ms;
	uint16_t slot_active_ms;
	uint16_t slot_active_us;
	uint32_t epoch_ms;
	uint32_t sync_local_ms;
	bool epoch_valid;
	uint8_t generation;
	uint32_t superframe_base;
	bool superframe_valid;
};

struct uwb_tag_runtime_params {
	uint16_t identity_code;
	uint8_t logical_tag_id;
	uint8_t slot_source;
	uint8_t positioning_mode;
	uint8_t anchor_selection_mode;
	uint8_t fixed_anchor_count;
	uint8_t fixed_anchor_ids[UWB_TAG_FIXED_ANCHOR_MAX];
	struct uwb_tdma_schedule tdma;
};

struct uwb_tag_runtime_config {
	uint16_t identity_code;
	uint8_t tag_id;
	uint8_t slot_source;
	uint8_t positioning_mode;
	uint8_t anchor_selection_mode;
	const uint8_t *anchor_ids;
	size_t anchor_count;
	bool fixed_anchor_mode;
	const uint8_t *fixed_anchor_ids;
	size_t fixed_anchor_count;
	bool multitag_anchor_plan_mode;
	const uint8_t *active_anchor_ids;
	size_t active_anchor_count;
	const uint8_t *standby_anchor_ids;
	size_t standby_anchor_count;
	const uint8_t *reserve_anchor_ids;
	size_t reserve_anchor_count;
	uint8_t refresh_anchor_budget;
	uint16_t refresh_interval_sweeps;
	uint16_t full_sweep_interval_sweeps;
	struct uwb_tdma_schedule tdma;
};

bool uwb_tdma_schedule_is_valid(const struct uwb_tdma_schedule *schedule);
void uwb_tdma_sync_schedule_epoch(struct uwb_tdma_schedule *schedule,
				  uint32_t epoch_ms,
				  uint8_t generation);
uint32_t uwb_tdma_schedule_now_ms(const struct uwb_tdma_schedule *schedule);
bool uwb_tdma_schedule_in_active_window(const struct uwb_tdma_schedule *schedule);
uint32_t uwb_tdma_schedule_time_remaining_ms(
	const struct uwb_tdma_schedule *schedule);
bool uwb_tdma_schedule_exchange_fits(const struct uwb_tdma_schedule *schedule,
					 uint32_t required_ms,
					 uint32_t guard_ms);
uint32_t uwb_tdma_wait_until_slot(const struct uwb_tdma_schedule *schedule);
uint32_t uwb_tdma_wait_until_next_slot(const struct uwb_tdma_schedule *schedule);

#endif /* UWB_TDMA_H */
