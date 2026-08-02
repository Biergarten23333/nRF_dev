#include <assert.h>
#include <stdint.h>

#include "tag_beacon_sync.h"

static void test_source_preference(void)
{
	struct tag_beacon_preference preference;

	tag_beacon_preference_init(&preference);
	assert(tag_beacon_preference_accept(
		&preference, UWB_BEACON_INDEX_SUB,
		UWB_BEACON_FLAG_PROMOTED));
	assert(preference.promoted_source_in_use);

	assert(tag_beacon_preference_accept(
		&preference, UWB_BEACON_INDEX_MAIN, 0U));
	assert(!preference.promoted_source_in_use);
	for (uint8_t i = 0U;
	     i < TAG_BEACON_PROMOTED_HOLDOFF_WINDOWS - 1U; ++i) {
		tag_beacon_preference_note_window(&preference);
		assert(!tag_beacon_preference_accept(
			&preference, UWB_BEACON_INDEX_SUB,
			UWB_BEACON_FLAG_PROMOTED));
	}
	tag_beacon_preference_note_window(&preference);
	assert(tag_beacon_preference_accept(
		&preference, UWB_BEACON_INDEX_SUB,
		UWB_BEACON_FLAG_PROMOTED));

	assert(tag_beacon_preference_accept(
		&preference, UWB_BEACON_INDEX_SUB, 0U));
	assert(!preference.promoted_source_in_use);
}

static void test_tracking_scheduler_wrap(void)
{
	uint64_t origin = UWB_BEACON_DW_TIME_MASK -
			  uwb_beacon_us_to_dw_ticks(50000U);
	uint64_t next = tag_beacon_next_tracking_origin_n(
		origin, 100000U, TAG_BEACON_WINDOW_N_MAX);
	uint64_t before = uwb_beacon_sub40(
		next, uwb_beacon_us_to_dw_ticks(1000U));
	uint64_t inside = uwb_beacon_add40(
		next, uwb_beacon_us_to_dw_ticks(100U));
	uint64_t after = uwb_beacon_add40(
		next, uwb_beacon_us_to_dw_ticks(700U));

	assert(next < origin);
	assert(uwb_beacon_diff40(next, origin) ==
	       (int64_t)uwb_beacon_us_to_dw_ticks(100000U));
	assert(tag_beacon_tracking_due(before, next, 1000U));
	assert(tag_beacon_tracking_due(inside, next, 1000U));
	assert(!tag_beacon_tracking_due(after, next, 1000U));
	assert(!tag_beacon_tracking_window_expired(before, next));
	assert(!tag_beacon_tracking_window_expired(inside, next));
	assert(tag_beacon_tracking_window_expired(after, next));
}

static void test_window_does_not_overlap_owned_slot(void)
{
	assert(!tag_beacon_tracking_window_precedes_slot(0U, 10U));
	assert(tag_beacon_tracking_window_precedes_slot(1U, 10U));
	assert(tag_beacon_tracking_window_precedes_slot(5U, 10U));
	assert(tag_beacon_tracking_window_precedes_slot(10U, 10U));
	assert(tag_beacon_tracking_window_fits_slot10_tail());
	assert(TAG_BEACON_SLOT10_TAIL_TO_ORIGIN_US -
	       TAG_BEACON_TRACK_START_EARLY_US == 900U);
}

static void test_post_sweep_urgent_horizon(void)
{
	uint64_t origin = UINT64_C(0x123456789a);
	uint64_t urgent = uwb_beacon_sub40(
		origin, uwb_beacon_us_to_dw_ticks(2000U));
	uint64_t ordinary = uwb_beacon_sub40(
		origin, uwb_beacon_us_to_dw_ticks(10000U));
	uint64_t expired = uwb_beacon_add40(
		origin, uwb_beacon_us_to_dw_ticks(700U));

	assert(tag_beacon_post_sweep_window_urgent(urgent, origin));
	assert(!tag_beacon_post_sweep_window_urgent(ordinary, origin));
	assert(!tag_beacon_post_sweep_window_urgent(expired, origin));
}

static void test_window_n_is_accepted_but_inert(void)
{
	uint8_t window_n = 0U;

	assert(tag_beacon_window_n_value(false, 0U, &window_n));
	assert(window_n == TAG_BEACON_WINDOW_N_DEFAULT);
	assert(tag_beacon_window_n_value(true, 10U, &window_n));
	assert(window_n == TAG_BEACON_WINDOW_N_DEFAULT);
	assert(!tag_beacon_window_n_value(true, 0U, &window_n));
	assert(!tag_beacon_window_n_value(true, 11U, &window_n));
}

static void test_local_origin_projection(void)
{
	uint64_t dw_origin = UINT64_C(1000000);
	uint64_t dw_now = uwb_beacon_add40(
		dw_origin, uwb_beacon_us_to_dw_ticks(6000U));

	assert(tag_beacon_local_origin_us(200000U, dw_now, dw_origin) ==
	       194000U);
}

int main(void)
{
	test_source_preference();
	test_tracking_scheduler_wrap();
	test_window_does_not_overlap_owned_slot();
	test_post_sweep_urgent_horizon();
	test_window_n_is_accepted_but_inert();
	test_local_origin_projection();
	return 0;
}
