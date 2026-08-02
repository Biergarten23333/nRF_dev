#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "tag_beacon_sync.h"
#include "tag_relay6.h"

static void test_beacon_window_values_and_cadence(void)
{
	uint8_t value = 0U;
	uint64_t origin = UWB_BEACON_DW_TIME_MASK -
		uwb_beacon_us_to_dw_ticks(50000U);

	assert(tag_beacon_window_n_value(false, 99U, &value));
	assert(value == TAG_BEACON_WINDOW_N_DEFAULT);
	assert(tag_beacon_window_n_value(true, 1U, &value));
	assert(value == TAG_BEACON_WINDOW_N_DEFAULT);
	assert(tag_beacon_window_n_value(true, 10U, &value));
	assert(value == TAG_BEACON_WINDOW_N_DEFAULT);
	assert(!tag_beacon_window_n_value(true, 0U, &value));
	assert(!tag_beacon_window_n_value(true, 11U, &value));
	assert(!tag_beacon_window_n_value(true, 1U, NULL));

	assert(tag_beacon_next_tracking_origin_n(origin, 100000U, 1U) ==
	       uwb_beacon_add40(origin, uwb_beacon_us_to_dw_ticks(100000U)));
	assert(tag_beacon_next_tracking_origin_n(origin, 100000U, 10U) ==
	       uwb_beacon_add40(origin, uwb_beacon_us_to_dw_ticks(100000U)));
}

static void test_beacon_status_worst_case(void)
{
	char line[191];
	int length = snprintf(
		line, sizeof(line), TAG_RELAY7_BEACON_STATUS_FORMAT,
		1U, 1U, 4294967295UL, 1U, 4294967295UL,
		4294967295UL, 255U, 4294967295UL, 4294967295UL,
		1U, 4294967295UL, 10U);

	assert(length > 0);
	assert((size_t)length < sizeof(line));
	assert(strstr(line, "dwmiss=4294967295") != NULL);
	assert(strstr(line, "win=10") != NULL);
	printf("relay7 BEACON_STATUS worst-case length=%d\n", length);
}

int main(void)
{
	test_beacon_window_values_and_cadence();
	test_beacon_status_worst_case();
	return 0;
}
