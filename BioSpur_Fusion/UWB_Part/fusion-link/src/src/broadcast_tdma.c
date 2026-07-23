#include "broadcast_tdma.h"
#include "broadcast_tdma_math.h"

#include <limits.h>

#include <zephyr/kernel.h>
#include <zephyr/sys/util.h>

#ifndef APP_TAG_TDMA_SLOT_PERIOD_MS
#define APP_TAG_TDMA_SLOT_PERIOD_MS 10U
#endif

#ifndef APP_TAG_TDMA_SLOT_COUNT
#define APP_TAG_TDMA_SLOT_COUNT 10U
#endif

#ifndef APP_TAG_TDMA_SLOT_START_LATE_TOLERANCE_US
#define APP_TAG_TDMA_SLOT_START_LATE_TOLERANCE_US 2000U
#endif

#ifndef APP_TAG_TDMA_BROADCAST_SWEEP_BUDGET_US
/* Eight-anchor broadcast completion is approximately 8.45 ms. */
#define APP_TAG_TDMA_BROADCAST_SWEEP_BUDGET_US 8500U
#endif

#ifndef APP_TAG_TDMA_SLOT_SPIN_THRESHOLD_MS
#define APP_TAG_TDMA_SLOT_SPIN_THRESHOLD_MS 3U
#endif

static int64_t broadcast_tdma_epoch_us(
	const struct uwb_tdma_schedule *schedule,
	int64_t now_ticks)
{
	int64_t now_ms;
	int64_t epoch_ms;

	if (schedule == NULL || !schedule->epoch_valid) {
		return 0;
	}

	now_ms = k_ticks_to_ms_floor64(now_ticks);
	epoch_ms = broadcast_tdma_lift_u32_ms(schedule->sync_local_ms,
						 now_ms);
	return epoch_ms * 1000;
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

static int64_t broadcast_tdma_busy_wait_until(int64_t target_ticks)
{
	int64_t now_ticks;

	while ((now_ticks = k_uptime_ticks()) < target_ticks) {
		int64_t remaining_ticks = target_ticks - now_ticks;
		uint64_t remaining_us =
			k_ticks_to_us_floor64(remaining_ticks);

		if (remaining_us == 0U) {
			continue;
		}
		k_busy_wait((uint32_t)MIN(remaining_us, (uint64_t)UINT32_MAX));
	}

	return now_ticks;
}

uint32_t broadcast_tdma_wait_next_slot_start(
	const struct uwb_tdma_schedule *schedule,
	struct broadcast_tdma_wait_stats *stats)
{
	uint64_t cycle_us;
	uint64_t slot_period_us;
	uint32_t slot_period_ms;
	uint32_t slot_count;
	int64_t late_tolerance_ticks;
	uint16_t slot_mask;

	if (stats != NULL) {
		stats->sleep_late_skips = 0U;
		stats->spin_late_skips = 0U;
	}

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
	slot_period_us = (uint64_t)slot_period_ms * 1000U;
	late_tolerance_ticks = k_us_to_ticks_floor64(
		broadcast_tdma_late_tolerance_us(
			slot_period_us,
			APP_TAG_TDMA_BROADCAST_SWEEP_BUDGET_US,
			APP_TAG_TDMA_SLOT_START_LATE_TOLERANCE_US));
	cycle_us = (uint64_t)slot_count * slot_period_us;
	if (cycle_us == 0U) {
		return k_cycle_get_32();
	}

	while (1) {
		int64_t now_ticks = k_uptime_ticks();
		int64_t now_us = k_ticks_to_us_floor64(now_ticks);
		int64_t epoch_us = broadcast_tdma_epoch_us(schedule, now_ticks);
		int64_t cycle_base_us = broadcast_tdma_cycle_base_us(
			now_us, epoch_us, cycle_us);
		int64_t target_ticks = INT64_MAX;

		/*
		 * This function must start a broadcast burst at an owned slot
		 * boundary. Starting anywhere inside the slot is unsafe: an 8-anchor
		 * broadcast sweep can overrun into the next tag when the thread wakes
		 * late. Small wakeup latency is allowed only while enough active-slot
		 * budget remains for the broadcast response window.
		 */
		for (uint32_t slot = 0U; slot < slot_count && slot < 16U; ++slot) {
			int64_t candidate_us;
			int64_t candidate_ticks;
			int64_t lateness_ticks;

			if ((slot_mask & (uint16_t)(1U << slot)) == 0U) {
				continue;
			}

			candidate_us = cycle_base_us +
				       (int64_t)((uint64_t)slot * slot_period_us);
			candidate_ticks = k_us_to_ticks_ceil64(candidate_us);
			lateness_ticks = now_ticks - candidate_ticks;
			if (lateness_ticks >= 0 &&
			    lateness_ticks <= late_tolerance_ticks) {
				return k_cycle_get_32();
			}

			if (candidate_ticks < now_ticks - late_tolerance_ticks) {
				candidate_us += (int64_t)cycle_us;
				candidate_ticks = k_us_to_ticks_ceil64(candidate_us);
			}
			if (candidate_ticks < target_ticks) {
				target_ticks = candidate_ticks;
			}
		}

		if (target_ticks == INT64_MAX) {
			return k_cycle_get_32();
		}

		while ((now_ticks = k_uptime_ticks()) < target_ticks) {
			int64_t remaining_ticks = target_ticks - now_ticks;
			uint64_t wait_ms = k_ticks_to_ms_floor64(remaining_ticks);

			if (wait_ms <= APP_TAG_TDMA_SLOT_SPIN_THRESHOLD_MS) {
				break;
			}
			k_msleep((uint32_t)wait_ms -
				  APP_TAG_TDMA_SLOT_SPIN_THRESHOLD_MS);
			now_ticks = k_uptime_ticks();
			if (now_ticks - target_ticks > late_tolerance_ticks &&
			    stats != NULL) {
				stats->sleep_late_skips++;
				break;
			}
		}

		if (now_ticks - target_ticks > late_tolerance_ticks) {
			continue;
		}

		now_ticks = broadcast_tdma_busy_wait_until(target_ticks);

		if (now_ticks - target_ticks <= late_tolerance_ticks) {
			return k_cycle_get_32();
		}
		if (stats != NULL) {
			stats->spin_late_skips++;
		}
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
