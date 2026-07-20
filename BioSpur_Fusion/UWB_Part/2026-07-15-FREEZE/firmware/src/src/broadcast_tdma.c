#include "broadcast_tdma.h"

#include <limits.h>

#include <zephyr/kernel.h>

#ifndef APP_TAG_TDMA_SLOT_PERIOD_MS
#define APP_TAG_TDMA_SLOT_PERIOD_MS 10U
#endif

#ifndef APP_TAG_TDMA_SLOT_COUNT
#define APP_TAG_TDMA_SLOT_COUNT 10U
#endif

#ifndef APP_TAG_TDMA_SLOT_START_LATE_TOLERANCE_MS
#define APP_TAG_TDMA_SLOT_START_LATE_TOLERANCE_MS 2U
#endif

#ifndef APP_TAG_TDMA_SLOT_MIN_REMAIN_MS
#define APP_TAG_TDMA_SLOT_MIN_REMAIN_MS 12U
#endif

#ifndef APP_TAG_TDMA_SLOT_SPIN_THRESHOLD_MS
#define APP_TAG_TDMA_SLOT_SPIN_THRESHOLD_MS 3U
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

static uint32_t broadcast_tdma_slot_period_ms(
	const struct uwb_tdma_schedule *schedule)
{
	if (schedule != NULL && schedule->slot_period_ms != 0U) {
		return schedule->slot_period_ms;
	}

	return APP_TAG_TDMA_SLOT_PERIOD_MS;
}

static uint32_t broadcast_tdma_slot_count(
	const struct uwb_tdma_schedule *schedule)
{
	if (schedule != NULL && schedule->slot_count != 0U) {
		return schedule->slot_count;
	}

	return APP_TAG_TDMA_SLOT_COUNT;
}

static uint16_t broadcast_tdma_slot_mask(
	const struct uwb_tdma_schedule *schedule,
	uint32_t slot_count)
{
	uint16_t valid_mask;

	if (schedule == NULL || slot_count == 0U) {
		return 0U;
	}

	valid_mask = (slot_count >= 16U) ? 0xFFFFU :
		     (uint16_t)((1U << slot_count) - 1U);
	if ((schedule->slot_mask & valid_mask) != 0U) {
		return (uint16_t)(schedule->slot_mask & valid_mask);
	}

	if (schedule->slot_index < slot_count) {
		return (uint16_t)(1U << schedule->slot_index);
	}

	return 0U;
}

static uint32_t broadcast_tdma_slot_active_ms(
	const struct uwb_tdma_schedule *schedule,
	uint32_t slot_period_ms)
{
	uint32_t active_ms = 0U;

	if (schedule != NULL) {
		if (schedule->slot_active_us != 0U) {
			active_ms = ((uint32_t)schedule->slot_active_us + 999U) / 1000U;
		} else {
			active_ms = schedule->slot_active_ms;
		}
	}

	if (active_ms == 0U || active_ms > slot_period_ms) {
		active_ms = slot_period_ms;
	}

	return active_ms;
}

static uint32_t broadcast_tdma_late_tolerance_ms(
	const struct uwb_tdma_schedule *schedule,
	uint32_t slot_period_ms)
{
	uint32_t active_ms = broadcast_tdma_slot_active_ms(schedule, slot_period_ms);
	uint32_t max_late_ms;

	if (active_ms <= APP_TAG_TDMA_SLOT_MIN_REMAIN_MS) {
		return 0U;
	}

	max_late_ms = active_ms - APP_TAG_TDMA_SLOT_MIN_REMAIN_MS;
	return (APP_TAG_TDMA_SLOT_START_LATE_TOLERANCE_MS < max_late_ms) ?
		       APP_TAG_TDMA_SLOT_START_LATE_TOLERANCE_MS :
		       max_late_ms;
}

static void broadcast_tdma_wait_near_slot(uint32_t wait_ms)
{
	if (wait_ms > APP_TAG_TDMA_SLOT_SPIN_THRESHOLD_MS) {
		k_msleep(wait_ms - APP_TAG_TDMA_SLOT_SPIN_THRESHOLD_MS);
		return;
	}

	if (wait_ms > 0U) {
		k_busy_wait(wait_ms * 1000U);
		return;
	}

	k_yield();
}

uint32_t broadcast_tdma_wait_next_slot_start(
	const struct uwb_tdma_schedule *schedule)
{
	uint32_t cycle_ms;
	uint32_t phase_ms;
	uint32_t target_ms = 0U;
	uint32_t wait_ms = UINT_MAX;
	uint32_t slot_period_ms;
	uint32_t slot_count;
	uint32_t late_tolerance_ms;
	uint16_t slot_mask;

	if (schedule == NULL || !schedule->enabled) {
		return k_cycle_get_32();
	}

	slot_period_ms = broadcast_tdma_slot_period_ms(schedule);
	slot_count = broadcast_tdma_slot_count(schedule);
	if (slot_period_ms == 0U || slot_count == 0U) {
		return k_cycle_get_32();
	}
	slot_mask = broadcast_tdma_slot_mask(schedule, slot_count);
	if (slot_mask == 0U) {
		return k_cycle_get_32();
	}
	late_tolerance_ms = broadcast_tdma_late_tolerance_ms(schedule,
							     slot_period_ms);

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

	while (1) {
		wait_ms = UINT_MAX;
		phase_ms = broadcast_tdma_now_ms(schedule) % cycle_ms;

		/*
		 * This function must start a broadcast burst at an owned slot
		 * boundary. Starting anywhere inside the slot is unsafe: an 8-anchor
		 * broadcast sweep can overrun into the next tag when the thread wakes
		 * late. Small wakeup latency is allowed only while enough active-slot
		 * budget remains for the broadcast response window.
		 */
		for (uint32_t slot = 0U; slot < slot_count && slot < 16U; ++slot) {
			uint32_t candidate_target_ms;
			uint32_t candidate_wait_ms;

			if ((slot_mask & (uint16_t)(1U << slot)) == 0U) {
				continue;
			}

			candidate_target_ms = slot * slot_period_ms;
			if (phase_ms >= candidate_target_ms &&
			    phase_ms <= candidate_target_ms + late_tolerance_ms) {
				return k_cycle_get_32();
			}

			candidate_wait_ms = (phase_ms < candidate_target_ms) ?
					    (candidate_target_ms - phase_ms) :
					    (cycle_ms - phase_ms + candidate_target_ms);
			if (candidate_wait_ms < wait_ms) {
				wait_ms = candidate_wait_ms;
				target_ms = candidate_target_ms;
			}
		}

		if (wait_ms == UINT_MAX) {
			return k_cycle_get_32();
		}
		broadcast_tdma_wait_near_slot(wait_ms);
		if (wait_ms > APP_TAG_TDMA_SLOT_SPIN_THRESHOLD_MS) {
			continue;
		}

		phase_ms = broadcast_tdma_now_ms(schedule) % cycle_ms;
		if (phase_ms >= target_ms &&
		    phase_ms <= target_ms + late_tolerance_ms) {
			return k_cycle_get_32();
		}
		k_yield();
	}
}

uint32_t broadcast_tdma_slot_to_us(uint32_t slot_start_cycle,
				   uint32_t event_cycle)
{
	if (slot_start_cycle == 0U || event_cycle == 0U) {
		return UINT_MAX;
	}

	return k_cyc_to_us_floor32(event_cycle - slot_start_cycle);
}
