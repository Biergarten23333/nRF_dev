#include "uwb_tdma.h"

#include <zephyr/kernel.h>

uint32_t uwb_tdma_wait_until_slot(const struct uwb_tdma_schedule *schedule)
{
    uint32_t cycle_ms;
    uint32_t now_ms;
    uint32_t phase_ms;
    uint32_t slot_start_ms;
    uint32_t slot_end_ms;
    uint32_t wait_ms;

    if (schedule == NULL || !schedule->enabled || schedule->slot_count == 0U ||
        schedule->slot_period_ms == 0U) {
        return 0U;
    }

    cycle_ms = (uint32_t)schedule->slot_count * (uint32_t)schedule->slot_period_ms;
    slot_start_ms =
        (uint32_t)schedule->slot_index * (uint32_t)schedule->slot_period_ms;
    slot_end_ms = slot_start_ms + (uint32_t)schedule->slot_active_ms;
    now_ms = (uint32_t)k_uptime_get();
    phase_ms = now_ms % cycle_ms;

    if (phase_ms >= slot_start_ms && phase_ms < slot_end_ms) {
        return 0U;
    }

    if (phase_ms < slot_start_ms) {
        wait_ms = slot_start_ms - phase_ms;
    } else {
        wait_ms = cycle_ms - phase_ms + slot_start_ms;
    }

    if (wait_ms > 0U) {
        k_msleep(wait_ms);
    }

    return wait_ms;
}
