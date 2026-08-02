#include <assert.h>
#include <stdint.h>
#include <string.h>

#include "uwb_beacon.h"
#include "uwb_ss_twr_shared.h"

static void test_frame_round_trip(void)
{
	uint8_t frame[UWB_BEACON_FRAME_LEN];
	struct uwb_beacon_payload input = {
		.superframe_counter = UINT32_C(0x89abcdef),
		.cycle_period_us = 100000U,
		.tx_offset_us = 6000U,
		.schedule_generation = 7U,
		.beacon_index = UWB_BEACON_INDEX_SUB,
		.flags = 0U,
	};
	struct uwb_beacon_payload output = { 0 };

	uwb_beacon_build_frame(frame, 0x42U, &input);
	assert(frame[UWB_MSG_CODE_IDX] == UWB_BEACON_CODE);
	assert(frame[UWB_MSG_SN_IDX] == 0x42U);
	assert(uwb_beacon_parse_frame(frame, sizeof(frame), &output));
	assert(memcmp(&input, &output, sizeof(input)) == 0);
}

static void test_exact_recognition_filter(void)
{
	uint8_t frame[UWB_BEACON_FRAME_LEN];
	struct uwb_beacon_payload input = {
		.cycle_period_us = 100000U,
	};
	struct uwb_beacon_payload output;

	uwb_beacon_build_frame(frame, 1U, &input);
	assert(!uwb_beacon_parse_frame(frame, sizeof(frame) - 1U, &output));
	frame[UWB_MSG_CODE_IDX] = UWB_MSG_POLL_CODE;
	assert(!uwb_beacon_parse_frame(frame, sizeof(frame), &output));
	frame[UWB_MSG_CODE_IDX] = UWB_BEACON_CODE;
	frame[UWB_BEACON_VERSION_IDX]++;
	assert(!uwb_beacon_parse_frame(frame, sizeof(frame), &output));
	frame[UWB_BEACON_VERSION_IDX] = UWB_BEACON_PROTOCOL_VERSION;
	frame[UWB_MSG_SRC_IDX] ^= 1U;
	assert(!uwb_beacon_parse_frame(frame, sizeof(frame), &output));
}

static void test_sub_origin_across_wrap(void)
{
	uint64_t origin = UWB_BEACON_DW_TIME_MASK - 1000U;
	uint64_t rx = uwb_beacon_add40(
		origin, uwb_beacon_us_to_dw_ticks(6000U));

	assert(rx < origin);
	assert(uwb_beacon_origin_from_rx(rx, 6000U) == origin);
}

static void test_period_math(void)
{
	assert(uwb_beacon_us_to_dw_ticks(100000U) ==
	       UINT64_C(6389760000));
	assert(uwb_beacon_us_to_dw_ticks(110000U) ==
	       UINT64_C(7028736000));
}

int main(void)
{
	test_frame_round_trip();
	test_exact_recognition_filter();
	test_sub_origin_across_wrap();
	test_period_math();
	return 0;
}
