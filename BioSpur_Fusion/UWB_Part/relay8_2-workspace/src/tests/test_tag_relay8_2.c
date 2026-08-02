#include <assert.h>
#include <stdint.h>
#include <stdio.h>

#include "tag_relay8_2.h"

static void test_rate_converges_and_coasts(void)
{
	struct tag_relay8_2_clock_tracker tracker;
	const uint32_t period_us = 110000U;
	const uint64_t nominal = uwb_beacon_us_to_dw_ticks(period_us);
	const int32_t true_adjust = (int32_t)(nominal / 50000U); /* +20 ppm */
	uint64_t origin = UINT64_C(0x1234500000);

	tag_relay8_2_tracker_reset(&tracker);
	tag_relay8_2_tracker_accept(&tracker, origin, 100U, period_us, false);
	for (uint32_t counter = 101U; counter < 141U; counter++) {
		origin = uwb_beacon_add40(origin, nominal + true_adjust);
		tag_relay8_2_tracker_accept(
			&tracker, origin, counter, period_us, false);
	}
	assert(tracker.rate_adjust_ticks > true_adjust * 9 / 10);
	assert(tracker.rate_adjust_ticks < true_adjust * 11 / 10);

	uint64_t expected = tracker.next_origin40;
	tag_relay8_2_tracker_coast(&tracker, period_us);
	assert(tracker.epochs_since_valid == 1U);
	assert(tracker.next_origin40 == uwb_beacon_add40(
		expected, nominal + tracker.rate_adjust_ticks));
}

static void test_wrap_generation_and_outlier(void)
{
	struct tag_relay8_2_clock_tracker tracker;
	const uint32_t period_us = 110000U;
	uint64_t origin = UWB_BEACON_DW_TIME_MASK - 1000U;

	tag_relay8_2_tracker_reset(&tracker);
	tag_relay8_2_tracker_accept(&tracker, origin, UINT32_MAX, period_us,
				    false);
	origin = uwb_beacon_add40(origin,
		uwb_beacon_us_to_dw_ticks(period_us));
	tag_relay8_2_tracker_accept(&tracker, origin, 0U, period_us, false);
	assert(tracker.valid);
	assert(tracker.last_counter == 0U);

	tag_relay8_2_tracker_accept(&tracker, origin, 1U, period_us, true);
	assert(tracker.rate_adjust_ticks == 0);

	tracker.rate_adjust_ticks = 123;
	origin = uwb_beacon_add40(
		tracker.next_origin40, uwb_beacon_us_to_dw_ticks(6000U));
	tag_relay8_2_tracker_accept(&tracker, origin, 2U, period_us, false);
	assert(tracker.rate_adjust_ticks == 123);
}

static void test_adaptive_window_bounds(void)
{
	assert(tag_relay8_2_window_early_us(0U) == 500U);
	assert(tag_relay8_2_window_close_us(0U) == 600U);
	assert(tag_relay8_2_window_early_us(1U) == 600U);
	assert(tag_relay8_2_window_close_us(1U) == 700U);
	assert(tag_relay8_2_window_early_us(UINT32_MAX) == 3000U);
	assert(tag_relay8_2_window_close_us(UINT32_MAX) == 3000U);
}

int main(void)
{
	test_rate_converges_and_coasts();
	test_wrap_generation_and_outlier();
	test_adaptive_window_bounds();
	puts("tag relay8.2 tests passed");
	return 0;
}
