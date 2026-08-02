#ifndef TAG_RELAY8_2_H
#define TAG_RELAY8_2_H

#include <stdbool.h>
#include <stdint.h>

#include "uwb_beacon.h"

#define TAG_RELAY8_2_RATE_GAIN_SHIFT 2U
#define TAG_RELAY8_2_RATE_LIMIT_PPM 100U
#define TAG_RELAY8_2_PHASE_OUTLIER_US 5000U
#define TAG_RELAY8_2_WINDOW_GROW_US 100U
#define TAG_RELAY8_2_WINDOW_MAX_EARLY_US 3000U
#define TAG_RELAY8_2_WINDOW_MAX_CLOSE_US 3000U

/*
 * Two-state local-DW-clock tracker.  next_origin40 is the phase state and
 * rate_adjust_ticks is the signed per-beacon-period rate state.  A valid
 * beacon anchors phase exactly; a quarter-gain update learns the residual
 * rate from the phase innovation accumulated since the previous reception.
 */
struct tag_relay8_2_clock_tracker {
	uint64_t next_origin40;
	int32_t rate_adjust_ticks;
	uint32_t last_counter;
	uint32_t epochs_since_valid;
	bool valid;
};

static inline int64_t tag_relay8_2_abs64(int64_t value)
{
	return value < 0 ? -value : value;
}

static inline int32_t tag_relay8_2_clamp_rate(
	int64_t rate_ticks, uint64_t nominal_period_ticks)
{
	int64_t limit = (int64_t)((nominal_period_ticks *
				  TAG_RELAY8_2_RATE_LIMIT_PPM) / 1000000U);
	if (rate_ticks > limit) {
		return (int32_t)limit;
	}
	if (rate_ticks < -limit) {
		return (int32_t)-limit;
	}
	return (int32_t)rate_ticks;
}

static inline void tag_relay8_2_tracker_reset(
	struct tag_relay8_2_clock_tracker *tracker)
{
	tracker->next_origin40 = 0U;
	tracker->rate_adjust_ticks = 0;
	tracker->last_counter = 0U;
	tracker->epochs_since_valid = 0U;
	tracker->valid = false;
}

static inline void tag_relay8_2_tracker_accept(
	struct tag_relay8_2_clock_tracker *tracker, uint64_t origin40,
	uint32_t counter, uint32_t period_us, bool generation_changed)
{
	uint64_t nominal_ticks = uwb_beacon_us_to_dw_ticks(period_us);

	if (tracker->valid && !generation_changed) {
		uint32_t counter_delta = counter - tracker->last_counter;
		int64_t phase_error = uwb_beacon_diff40(
			origin40, tracker->next_origin40);
		int64_t outlier_ticks = (int64_t)uwb_beacon_us_to_dw_ticks(
			TAG_RELAY8_2_PHASE_OUTLIER_US);

		if (counter_delta != 0U &&
		    tag_relay8_2_abs64(phase_error) <= outlier_ticks) {
			int64_t per_epoch_error = phase_error /
						  (int64_t)counter_delta;
			int64_t update = per_epoch_error /
					 (int64_t)(1U <<
					 TAG_RELAY8_2_RATE_GAIN_SHIFT);

			tracker->rate_adjust_ticks = tag_relay8_2_clamp_rate(
				(int64_t)tracker->rate_adjust_ticks + update,
				nominal_ticks);
		}
	} else {
		tracker->rate_adjust_ticks = 0;
	}

	tracker->last_counter = counter;
	tracker->epochs_since_valid = 0U;
	tracker->valid = true;
	tracker->next_origin40 = uwb_beacon_add40(
		origin40, nominal_ticks + tracker->rate_adjust_ticks);
}

static inline void tag_relay8_2_tracker_coast(
	struct tag_relay8_2_clock_tracker *tracker, uint32_t period_us)
{
	uint64_t step_ticks = uwb_beacon_us_to_dw_ticks(period_us) +
			      tracker->rate_adjust_ticks;

	tracker->next_origin40 = uwb_beacon_add40(
		tracker->next_origin40, step_ticks);
	if (tracker->epochs_since_valid != UINT32_MAX) {
		tracker->epochs_since_valid++;
	}
}

static inline uint32_t tag_relay8_2_window_early_us(uint32_t missed_epochs)
{
	uint64_t width = 500U +
			 (uint64_t)missed_epochs * TAG_RELAY8_2_WINDOW_GROW_US;

	return width > TAG_RELAY8_2_WINDOW_MAX_EARLY_US ?
		TAG_RELAY8_2_WINDOW_MAX_EARLY_US : (uint32_t)width;
}

static inline uint32_t tag_relay8_2_window_close_us(uint32_t missed_epochs)
{
	uint64_t width = 600U +
			 (uint64_t)missed_epochs * TAG_RELAY8_2_WINDOW_GROW_US;

	return width > TAG_RELAY8_2_WINDOW_MAX_CLOSE_US ?
		TAG_RELAY8_2_WINDOW_MAX_CLOSE_US : (uint32_t)width;
}

#endif /* TAG_RELAY8_2_H */
