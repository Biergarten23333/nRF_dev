#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "tag_run_state.h"

static struct uwb_tag_runtime_params configured_params(void)
{
	return (struct uwb_tag_runtime_params) {
		.identity_code = 0x065f,
		.logical_tag_id = 0x5f,
		.slot_source = UWB_TAG_SLOT_SOURCE_MASTER,
		.positioning_mode = UWB_TAG_MODE_RUN,
		.anchor_selection_mode = UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2,
		.tdma = {
			.enabled = true,
			.slot_index = 3,
			.slot_count = 10,
			.slot_mask = 0x0008,
			.slot_period_ms = 10,
			.slot_active_ms = 10,
			.slot_active_us = 8500,
			.epoch_ms = 0x89abcdef,
			.sync_local_ms = 0x12345678,
			.epoch_valid = true,
			.generation = 17,
			.superframe_base = 0xfedcba98,
			.superframe_valid = true,
		},
	};
}

static void test_stop_holds_and_preserves_schedule(void)
{
	struct uwb_tag_runtime_params params = configured_params();
	struct uwb_tag_runtime_params expected = params;

	expected.tdma.enabled = false;
	tag_run_state_set(&params, false);

	assert(memcmp(&params, &expected, sizeof(params)) == 0);
	assert(tag_run_state_holds_radio(&params));
}

static void test_run_resumes_same_schedule(void)
{
	struct uwb_tag_runtime_params params = configured_params();
	struct uwb_tag_runtime_params expected = params;

	tag_run_state_set(&params, false);
	tag_run_state_set(&params, true);

	assert(memcmp(&params, &expected, sizeof(params)) == 0);
	assert(!tag_run_state_holds_radio(&params));
}

static void test_idle_and_unconfigured_do_not_use_cfg_hold(void)
{
	struct uwb_tag_runtime_params params = configured_params();

	params.positioning_mode = UWB_TAG_MODE_IDLE;
	tag_run_state_set(&params, false);
	assert(!tag_run_state_holds_radio(&params));

	params.positioning_mode = UWB_TAG_MODE_RUN;
	params.tdma.epoch_valid = false;
	assert(!tag_run_state_holds_radio(&params));
}

static void test_cfg_stop_refuses_without_epoch(void)
{
	struct uwb_tag_runtime_params params = configured_params();

	assert(tag_run_state_can_cfg_stop(&params));
	params.tdma.epoch_valid = false;
	assert(!tag_run_state_can_cfg_stop(&params));
	assert(!tag_run_state_can_cfg_stop(NULL));
}

int main(void)
{
	test_stop_holds_and_preserves_schedule();
	test_run_resumes_same_schedule();
	test_idle_and_unconfigured_do_not_use_cfg_hold();
	test_cfg_stop_refuses_without_epoch();
	puts("tag_run_state: PASS");
	return 0;
}
