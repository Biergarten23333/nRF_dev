#include "uwb_tdma.h"

#include <zephyr/kernel.h>

static uint16_t uwb_tdma_effective_slot_mask(const struct uwb_tdma_schedule *schedule)
{
	uint16_t valid_mask;

	if (schedule == NULL || schedule->slot_count == 0U) {
		return 0U;
	}

	if (schedule->slot_count >= 16U) {
		valid_mask = 0xFFFFU;
	} else {
		valid_mask = (uint16_t)((1U << schedule->slot_count) - 1U);
	}

	if ((schedule->slot_mask & valid_mask) != 0U) {
		return (uint16_t)(schedule->slot_mask & valid_mask);
	}

	if (schedule->slot_index < schedule->slot_count) {
		return (uint16_t)(1U << schedule->slot_index);
	}

	return 0U;
}

bool uwb_tdma_schedule_is_valid(const struct uwb_tdma_schedule *schedule)
{
	if (schedule == NULL || !schedule->enabled) {
		return false;
	}

	return schedule->slot_count != 0U &&
	       schedule->slot_count <= 16U &&
	       uwb_tdma_effective_slot_mask(schedule) != 0U &&
	       schedule->slot_period_ms != 0U &&
	       schedule->slot_active_ms != 0U &&
	       schedule->slot_active_ms <= schedule->slot_period_ms;
}

void uwb_tdma_sync_schedule_epoch(struct uwb_tdma_schedule *schedule,
				  uint32_t epoch_ms,
				  uint8_t generation)
{
	if (schedule == NULL) {
		return;
	}

	schedule->epoch_ms = epoch_ms;
	schedule->sync_local_ms = k_uptime_get_32();
	schedule->epoch_valid = true;
	schedule->generation = generation;
}

uint32_t uwb_tdma_schedule_now_ms(const struct uwb_tdma_schedule *schedule)
{
	uint32_t local_now_ms = k_uptime_get_32();

	if (schedule == NULL) {
		return local_now_ms;
	}

	if (!schedule->epoch_valid) {
		return local_now_ms;
	}

	return schedule->epoch_ms + (local_now_ms - schedule->sync_local_ms);
}

static uint32_t uwb_tdma_schedule_phase_ms(const struct uwb_tdma_schedule *schedule,
					   uint32_t *cycle_ms_out)
{
	uint32_t cycle_ms;

	if (!uwb_tdma_schedule_is_valid(schedule)) {
		if (cycle_ms_out != NULL) {
			*cycle_ms_out = 0U;
		}
		return 0U;
	}

	cycle_ms = (uint32_t)schedule->slot_count *
		   (uint32_t)schedule->slot_period_ms;
	if (cycle_ms_out != NULL) {
		*cycle_ms_out = cycle_ms;
	}

	return uwb_tdma_schedule_now_ms(schedule) % cycle_ms;
}

bool uwb_tdma_schedule_in_active_window(const struct uwb_tdma_schedule *schedule)
{
	uint32_t phase_ms;
	uint32_t slot_start_ms;
	uint32_t slot_end_ms;
	uint8_t slot;
	uint16_t slot_mask;

	if (!uwb_tdma_schedule_is_valid(schedule)) {
		return true;
	}

	phase_ms = uwb_tdma_schedule_phase_ms(schedule, NULL);
	slot = (uint8_t)(phase_ms / (uint32_t)schedule->slot_period_ms);
	slot_mask = uwb_tdma_effective_slot_mask(schedule);
	if ((slot_mask & (uint16_t)(1U << slot)) == 0U) {
		return false;
	}
	slot_start_ms = (uint32_t)slot * (uint32_t)schedule->slot_period_ms;
	slot_end_ms = slot_start_ms + (uint32_t)schedule->slot_active_ms;

	return phase_ms >= slot_start_ms && phase_ms < slot_end_ms;
}

uint32_t uwb_tdma_schedule_time_remaining_ms(
	const struct uwb_tdma_schedule *schedule)
{
	uint32_t phase_ms;
	uint32_t slot_start_ms;
	uint32_t slot_end_ms;
	uint8_t slot;
	uint16_t slot_mask;

	if (!uwb_tdma_schedule_is_valid(schedule)) {
		return UINT32_MAX;
	}

	phase_ms = uwb_tdma_schedule_phase_ms(schedule, NULL);
	slot = (uint8_t)(phase_ms / (uint32_t)schedule->slot_period_ms);
	slot_mask = uwb_tdma_effective_slot_mask(schedule);
	if ((slot_mask & (uint16_t)(1U << slot)) == 0U) {
		return 0U;
	}

	slot_start_ms = (uint32_t)slot * (uint32_t)schedule->slot_period_ms;
	slot_end_ms = slot_start_ms + (uint32_t)schedule->slot_active_ms;

	if (phase_ms < slot_start_ms || phase_ms >= slot_end_ms) {
		return 0U;
	}

	return slot_end_ms - phase_ms;
}

bool uwb_tdma_schedule_exchange_fits(const struct uwb_tdma_schedule *schedule,
				     uint32_t required_ms,
				     uint32_t guard_ms)
{
	uint32_t remaining_ms;

	if (!uwb_tdma_schedule_is_valid(schedule)) {
		return true;
	}

	remaining_ms = uwb_tdma_schedule_time_remaining_ms(schedule);
	return remaining_ms >= (required_ms + guard_ms);
}

uint32_t uwb_tdma_wait_until_slot(const struct uwb_tdma_schedule *schedule)
{
	uint32_t cycle_ms;
	uint32_t phase_ms;
	uint32_t slot_start_ms;
	uint32_t slot_end_ms;
	uint32_t wait_ms;
	uint16_t slot_mask;

	if (!uwb_tdma_schedule_is_valid(schedule)) {
		return 0U;
	}

	phase_ms = uwb_tdma_schedule_phase_ms(schedule, &cycle_ms);
	slot_mask = uwb_tdma_effective_slot_mask(schedule);
	wait_ms = cycle_ms;

	for (uint8_t slot = 0U; slot < schedule->slot_count; ++slot) {
		if ((slot_mask & (uint16_t)(1U << slot)) == 0U) {
			continue;
		}

		slot_start_ms = (uint32_t)slot * (uint32_t)schedule->slot_period_ms;
		slot_end_ms = slot_start_ms + (uint32_t)schedule->slot_active_ms;

		if (phase_ms >= slot_start_ms && phase_ms < slot_end_ms) {
			return 0U;
		}

		if (phase_ms < slot_start_ms) {
			uint32_t candidate = slot_start_ms - phase_ms;

			if (candidate < wait_ms) {
				wait_ms = candidate;
			}
		} else {
			uint32_t candidate = cycle_ms - phase_ms + slot_start_ms;

			if (candidate < wait_ms) {
				wait_ms = candidate;
			}
		}
	}

	if (wait_ms > 0U) {
		k_msleep(wait_ms);
	}

	return wait_ms;
}

uint32_t uwb_tdma_wait_until_next_slot(const struct uwb_tdma_schedule *schedule)
{
	uint32_t cycle_ms;
	uint32_t phase_ms;
	uint32_t slot_start_ms;
	uint32_t wait_ms;
	uint16_t slot_mask;

	if (!uwb_tdma_schedule_is_valid(schedule)) {
		return 0U;
	}

	phase_ms = uwb_tdma_schedule_phase_ms(schedule, &cycle_ms);
	slot_mask = uwb_tdma_effective_slot_mask(schedule);
	wait_ms = cycle_ms;

	for (uint8_t slot = 0U; slot < schedule->slot_count; ++slot) {
		if ((slot_mask & (uint16_t)(1U << slot)) == 0U) {
			continue;
		}

		slot_start_ms = (uint32_t)slot * (uint32_t)schedule->slot_period_ms;

		if (phase_ms < slot_start_ms) {
			uint32_t candidate = slot_start_ms - phase_ms;

			if (candidate < wait_ms) {
				wait_ms = candidate;
			}
		} else {
			uint32_t candidate = cycle_ms - phase_ms + slot_start_ms;

			if (candidate < wait_ms) {
				wait_ms = candidate;
			}
		}
	}

	if (wait_ms > 0U) {
		k_msleep(wait_ms);
	}

	return wait_ms;
}
