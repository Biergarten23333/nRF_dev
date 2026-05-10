#include "broadcast_tdma.h"

#include <limits.h>

#include <zephyr/kernel.h>

#ifndef APP_TAG_TDMA_SLOT_PERIOD_MS
#define APP_TAG_TDMA_SLOT_PERIOD_MS 10U
#endif

#ifndef APP_TAG_TDMA_SLOT_COUNT
#define APP_TAG_TDMA_SLOT_COUNT 10U
#endif

static uint32_t broadcast_tdma_now_ms(const struct uwb_tdma_schedule *schedule)
{
	uint32_t now = k_uptime_get_32();

	if (schedule == NULL || !schedule->epoch_valid) {
		return now;
	}

	if ((int32_t)(now - schedule->sync_local_ms) < 0) {
		return 0U;
	}

	return now - schedule->sync_local_ms;
}

static uint16_t broadcast_tdma_effective_slot_mask(
	const struct uwb_tdma_schedule *schedule, uint32_t slot_count)
{
	uint16_t valid_mask;

	if (schedule == NULL || slot_count == 0U) {
		return 0U;
	}
	if (slot_count >= 16U) {
		valid_mask = 0xffffU;
	} else {
		valid_mask = (uint16_t)((1U << slot_count) - 1U);
	}
	if ((schedule->slot_mask & valid_mask) != 0U) {
		return (uint16_t)(schedule->slot_mask & valid_mask);
	}
	if (schedule->slot_index < slot_count) {
		return (uint16_t)(1U << schedule->slot_index);
	}
	return 0U;
}

uint32_t broadcast_tdma_wait_next_slot_start(
	const struct uwb_tdma_schedule *schedule)
{
	uint32_t cycle_ms;
	uint32_t phase_ms;
	uint32_t target_ms;
	uint32_t wait_ms;
	uint32_t slot_period_ms = APP_TAG_TDMA_SLOT_PERIOD_MS;
	uint32_t slot_count = APP_TAG_TDMA_SLOT_COUNT;
	uint16_t slot_mask;

	if (schedule == NULL || !schedule->enabled) {
		return k_cycle_get_32();
	}
	if (slot_period_ms == 0U) {
		slot_period_ms = schedule->slot_period_ms;
	}
	if (slot_count == 0U) {
		slot_count = schedule->slot_count;
	}
	if (slot_period_ms == 0U || slot_count == 0U) {
		return k_cycle_get_32();
	}
	slot_mask = broadcast_tdma_effective_slot_mask(schedule, slot_count);
	if (slot_mask == 0U) {
		return k_cycle_get_32();
	}

	while (schedule->epoch_valid &&
	       (int32_t)(k_uptime_get_32() - schedule->sync_local_ms) < 0) {
		uint32_t until_epoch_ms =
			schedule->sync_local_ms - k_uptime_get_32();

		if (until_epoch_ms > 1U) {
			k_msleep(until_epoch_ms - 1U);
		} else {
			k_yield();
		}
	}

	cycle_ms = slot_count * slot_period_ms;
	if (cycle_ms == 0U) {
		return k_cycle_get_32();
	}

	phase_ms = broadcast_tdma_now_ms(schedule) % cycle_ms;
	wait_ms = cycle_ms;
	target_ms = 0U;

	/*
	 * This loop is called after the previous sweep has consumed an owned
	 * slot. For multi-slot schedules, pick the next slot in the mask instead
	 * of falling back to the primary slot_index only.
	 */
	for (uint32_t slot = 0U; slot < slot_count; ++slot) {
		uint32_t candidate_ms;
		uint32_t delta_ms;

		if ((slot_mask & (uint16_t)(1U << slot)) == 0U) {
			continue;
		}
		candidate_ms = slot * slot_period_ms;
		if (phase_ms < candidate_ms) {
			delta_ms = candidate_ms - phase_ms;
		} else {
			delta_ms = cycle_ms - phase_ms + candidate_ms;
		}
		if (delta_ms < wait_ms) {
			wait_ms = delta_ms;
			target_ms = candidate_ms;
		}
	}

	if (wait_ms > 1U) {
		k_msleep(wait_ms - 1U);
	}

	while (1) {
		phase_ms = broadcast_tdma_now_ms(schedule) % cycle_ms;
		if (phase_ms >= target_ms &&
		    phase_ms < target_ms + slot_period_ms) {
			break;
		}
		k_yield();
	}

	return k_cycle_get_32();
}

uint32_t broadcast_tdma_slot_to_us(uint32_t slot_start_cycle,
				   uint32_t event_cycle)
{
	if (slot_start_cycle == 0U || event_cycle == 0U) {
		return UINT_MAX;
	}

	return k_cyc_to_us_floor32(event_cycle - slot_start_cycle);
}
