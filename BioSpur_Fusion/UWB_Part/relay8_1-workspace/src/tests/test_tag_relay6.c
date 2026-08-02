#include <assert.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>

#include "tag_relay6.h"

static void test_e3_counter_restart_does_not_rebase_public_sweep(void)
{
	uint32_t local = 205763U;
	uint32_t beacon_counter = 205763U;

	assert(tag_relay6_public_sweep(local) == 205763U);
	beacon_counter = 1U;
	local++;
	assert(beacon_counter == 1U);
	assert(tag_relay6_public_sweep(local) == 205764U);
}

static void test_generation_rebase_gate(void)
{
	assert(!tag_relay6_generation_rebase(false, 9U, 10U));
	assert(!tag_relay6_generation_rebase(true, 9U, 9U));
	assert(tag_relay6_generation_rebase(true, 9U, 10U));
}

static void test_anchor_enable_gate(void)
{
	assert(!tag_relay6_can_anchor(false, false));
	assert(!tag_relay6_can_anchor(false, true));
	assert(!tag_relay6_can_anchor(true, false));
	assert(tag_relay6_can_anchor(true, true));
}

static void test_dw_anchor_cfg_values(void)
{
	bool enabled = true;

	assert(tag_relay6_dw_anchor_value(false, 0U, &enabled));
	assert(!enabled);
	assert(tag_relay6_dw_anchor_value(true, 0U, &enabled));
	assert(!enabled);
	assert(tag_relay6_dw_anchor_value(true, 1U, &enabled));
	assert(enabled);
	assert(!tag_relay6_dw_anchor_value(true, 2U, &enabled));
	assert(!tag_relay6_dw_anchor_value(true, 0U, NULL));
}

static void test_dw_target_wrap_and_cycle_advance(void)
{
	uint64_t origin = UWB_BEACON_DW_TIME_MASK -
		uwb_beacon_us_to_dw_ticks(5000U);
	uint64_t target;
	uint64_t expected = uwb_beacon_add40(
		origin, uwb_beacon_us_to_dw_ticks(10000U));
	uint64_t now = uwb_beacon_add40(
		origin, uwb_beacon_us_to_dw_ticks(1000U));

	assert(tag_relay6_next_slot_target40(
		origin, now, 10000U, 100000U, &target));
	assert(target == expected);
	assert(target < origin);

	now = uwb_beacon_add40(
		expected, uwb_beacon_us_to_dw_ticks(250U));
	assert(tag_relay6_next_slot_target40(
		origin, now, 10000U, 100000U, &target));
	assert(target == uwb_beacon_add40(
		expected, uwb_beacon_us_to_dw_ticks(100000U)));
	assert(!tag_relay6_next_slot_target40(
		origin, now, 10000U, 0U, &target));
}

static void test_arm_window_states(void)
{
	uint64_t target = UINT64_C(900000000000);
	uint64_t lead = UINT64_C(3000) * UINT64_C(65536);

	assert(tag_relay6_arm_state40(
		target, uwb_beacon_sub40(target, lead + 1U),
		lead) == TAG_RELAY6_ARM_WAIT);
	assert(tag_relay6_arm_state40(
		target, uwb_beacon_sub40(target, lead),
		lead) == TAG_RELAY6_ARM_READY);
	assert(tag_relay6_arm_state40(
		target, uwb_beacon_add40(target, 1U),
		lead) == TAG_RELAY6_ARM_MISSED);
}

static void test_beacon_status_worst_case(void)
{
	char line[191];
	int length = snprintf(
		line, sizeof(line), TAG_RELAY6_BEACON_STATUS_FORMAT,
		1U, 1U, 4294967295UL, 1U, 4294967295UL,
		4294967295UL, 255U, 4294967295UL, 4294967295UL,
		1U, 4294967295UL);

	assert(length > 0);
	assert((size_t)length < sizeof(line));
	assert(strstr(line, "sync=1") != NULL);
	assert(strstr(line, "counter=4294967295") != NULL);
	assert(strstr(line, "dwmiss=4294967295") != NULL);
	printf("BEACON_STATUS worst-case length=%d\n", length);
}

int main(void)
{
	test_e3_counter_restart_does_not_rebase_public_sweep();
	test_generation_rebase_gate();
	test_anchor_enable_gate();
	test_dw_anchor_cfg_values();
	test_dw_target_wrap_and_cycle_advance();
	test_arm_window_states();
	test_beacon_status_worst_case();
	return 0;
}
