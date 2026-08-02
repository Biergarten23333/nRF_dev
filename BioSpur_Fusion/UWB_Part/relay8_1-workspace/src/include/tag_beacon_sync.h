#ifndef TAG_BEACON_SYNC_H
#define TAG_BEACON_SYNC_H

#include <stdbool.h>
#include <stdint.h>

#include "uwb_beacon.h"
#include "uwb_tdma.h"

#define TAG_BEACON_PROMOTED_HOLDOFF_WINDOWS 10U
#define TAG_BEACON_TRACK_START_EARLY_US 500U
#define TAG_BEACON_TRACK_CLOSE_US 600U
#define TAG_BEACON_SLOT10_TAIL_TO_ORIGIN_US 1400U
#define TAG_BEACON_POST_SWEEP_ARM_HORIZON_US 3000U
#define TAG_BEACON_REACQUIRE_AFTER_MS 30000U
#define TAG_BEACON_DIRECT_CORRECTION_US 2000U

struct tag_beacon_preference {
	uint8_t windows_since_nonpromoted;
	bool promoted_source_in_use;
};

static inline void tag_beacon_preference_init(
	struct tag_beacon_preference *preference)
{
	preference->windows_since_nonpromoted =
		TAG_BEACON_PROMOTED_HOLDOFF_WINDOWS;
	preference->promoted_source_in_use = false;
}

static inline void tag_beacon_preference_note_window(
	struct tag_beacon_preference *preference)
{
	if (preference->windows_since_nonpromoted <
	    TAG_BEACON_PROMOTED_HOLDOFF_WINDOWS) {
		preference->windows_since_nonpromoted++;
	}
}

static inline bool tag_beacon_preference_accept(
	struct tag_beacon_preference *preference, uint8_t beacon_index,
	uint8_t flags)
{
	bool promoted = beacon_index == UWB_BEACON_INDEX_SUB &&
			(flags & UWB_BEACON_FLAG_PROMOTED) != 0U;

	if (promoted &&
	    preference->windows_since_nonpromoted <
		    TAG_BEACON_PROMOTED_HOLDOFF_WINDOWS) {
		return false;
	}
	if (promoted) {
		preference->promoted_source_in_use = true;
	} else {
		preference->windows_since_nonpromoted = 0U;
		preference->promoted_source_in_use = false;
	}
	return true;
}

static inline bool tag_beacon_window_n_value(
	bool present, uint32_t value, uint8_t *window_n_out)
{
	if (window_n_out == NULL) {
		return false;
	}
	if (!present) {
		*window_n_out = TAG_BEACON_WINDOW_N_DEFAULT;
		return true;
	}
	if (value < TAG_BEACON_WINDOW_N_MIN ||
	    value > TAG_BEACON_WINDOW_N_MAX) {
		return false;
	}
	/* relay8 accepts the legacy knob but always tracks every beacon. */
	*window_n_out = TAG_BEACON_WINDOW_N_DEFAULT;
	return true;
}

static inline uint64_t tag_beacon_next_tracking_origin_n(
	uint64_t origin40, uint32_t period_us, uint8_t window_n)
{
	(void)window_n;
	return uwb_beacon_add40(
		origin40,
		uwb_beacon_us_to_dw_ticks(period_us));
}

static inline bool tag_beacon_tracking_window_fits_slot10_tail(void)
{
	return TAG_BEACON_TRACK_START_EARLY_US <
	       TAG_BEACON_SLOT10_TAIL_TO_ORIGIN_US;
}

static inline bool tag_beacon_tracking_window_precedes_slot(
	uint8_t slot_index, uint16_t slot_period_ms)
{
	uint32_t slot_start_us =
		(uint32_t)slot_index * (uint32_t)slot_period_ms * 1000U;

	return slot_start_us > TAG_BEACON_TRACK_CLOSE_US;
}

static inline bool tag_beacon_tracking_due(
	uint64_t now40, uint64_t origin40, uint32_t maximum_ahead_us)
{
	uint64_t window_start = uwb_beacon_sub40(
		origin40,
		uwb_beacon_us_to_dw_ticks(TAG_BEACON_TRACK_START_EARLY_US));
	uint64_t window_close = uwb_beacon_add40(
		origin40,
		uwb_beacon_us_to_dw_ticks(TAG_BEACON_TRACK_CLOSE_US));
	int64_t start_delta = uwb_beacon_diff40(window_start, now40);
	int64_t close_delta = uwb_beacon_diff40(window_close, now40);

	return close_delta > 0 &&
	       start_delta <= (int64_t)uwb_beacon_us_to_dw_ticks(maximum_ahead_us);
}

static inline bool tag_beacon_tracking_window_expired(
	uint64_t now40, uint64_t origin40)
{
	uint64_t window_close = uwb_beacon_add40(
		origin40,
		uwb_beacon_us_to_dw_ticks(TAG_BEACON_TRACK_CLOSE_US));

	return uwb_beacon_diff40(now40, window_close) >= 0;
}

static inline bool tag_beacon_post_sweep_window_urgent(
	uint64_t now40, uint64_t origin40)
{
	return tag_beacon_tracking_due(
		now40, origin40, TAG_BEACON_POST_SWEEP_ARM_HORIZON_US);
}

static inline uint64_t tag_beacon_local_origin_us(
	uint64_t local_now_us, uint64_t dw_now40, uint64_t origin40)
{
	int64_t age_ticks = uwb_beacon_diff40(dw_now40, origin40);
	uint64_t age_us;

	if (age_ticks <= 0) {
		return local_now_us;
	}
	age_us = uwb_beacon_dw_ticks_to_us((uint64_t)age_ticks);
	return age_us < local_now_us ? local_now_us - age_us : 0U;
}

#endif /* TAG_BEACON_SYNC_H */
