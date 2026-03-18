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

struct uwb_tdma_schedule {
    bool enabled;
    uint8_t slot_index;
    uint8_t slot_count;
    uint16_t slot_period_ms;
    uint16_t slot_active_ms;
};

struct uwb_tag_runtime_config {
    uint8_t tag_id;
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

uint32_t uwb_tdma_wait_until_slot(const struct uwb_tdma_schedule *schedule);

#endif /* UWB_TDMA_H */
