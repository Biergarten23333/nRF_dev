#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "tag_relay8.h"

static void test_idle_clears_beacon_state(void)
{
	struct uwb_tag_runtime_params params = {
		.beacon_sync = true,
		.beacon_win_n = TAG_BEACON_WINDOW_N_MAX,
	};

	tag_relay8_apply_idle_beacon_policy(&params);
	assert(!params.beacon_sync);
	assert(params.beacon_win_n == TAG_BEACON_WINDOW_N_DEFAULT);
	tag_relay8_apply_idle_beacon_policy(NULL);
}

static void test_epoch_label_lifecycle_and_wrap(void)
{
	struct tag_relay8_epoch_label epoch = {0};
	uint8_t flags = BSL_FLAG_STROBE_SENT | BSL_FLAG_SWEEP_PARTIAL |
			BSL_FLAG_IDENTITY_NVS;

	assert(tag_relay8_epoch_encode_flags(flags, &epoch) == flags);
	tag_relay8_epoch_accept(&epoch, 15U);
	flags = tag_relay8_epoch_encode_flags(flags, &epoch);
	assert((flags & BSL_FLAG_SUPERFRAME_VALID) != 0U);
	assert(((flags & BSL_FLAG_SUPERFRAME_MASK) >>
		BSL_FLAG_SUPERFRAME_SHIFT) == 15U);

	tag_relay8_epoch_coast(&epoch);
	flags = tag_relay8_epoch_encode_flags(flags, &epoch);
	assert(((flags & BSL_FLAG_SUPERFRAME_MASK) >>
		BSL_FLAG_SUPERFRAME_SHIFT) == 0U);
	assert((flags & 0x07U) == 0x07U);

	tag_relay8_epoch_invalidate(&epoch);
	flags = tag_relay8_epoch_encode_flags(flags, &epoch);
	assert((flags & (BSL_FLAG_SUPERFRAME_MASK |
			 BSL_FLAG_SUPERFRAME_VALID)) == 0U);
	tag_relay8_epoch_accept(NULL, 1U);
	tag_relay8_epoch_coast(NULL);
	tag_relay8_epoch_invalidate(NULL);
}

static void test_only_epoch_flag_bits_change(void)
{
	bsl_uwb_t old_body;
	bsl_uwb_t new_body;
	struct tag_relay8_epoch_label epoch = {
		.counter = 0x1234567aU,
		.valid = true,
	};
	const size_t flags_offset = offsetof(bsl_uwb_t, flags);

	memset(&old_body, 0xa5, sizeof(old_body));
	new_body = old_body;
	new_body.flags = tag_relay8_epoch_encode_flags(new_body.flags, &epoch);
	assert(memcmp(&old_body, &new_body, flags_offset) == 0);
	assert(flags_offset + 1U == sizeof(old_body));
	assert((old_body.flags & 0x07U) == (new_body.flags & 0x07U));
	assert((new_body.flags & BSL_FLAG_SUPERFRAME_VALID) != 0U);
	assert(((new_body.flags & BSL_FLAG_SUPERFRAME_MASK) >>
		BSL_FLAG_SUPERFRAME_SHIFT) == 10U);
}

static void test_epoch_snapshot_survives_next_beacon(void)
{
	struct tag_relay8_epoch_label live = {0};
	struct tag_relay8_epoch_label sweep = {0};
	uint8_t flags;

	tag_relay8_epoch_accept(&live, 10U);
	tag_relay8_epoch_snapshot(&sweep, &live);
	tag_relay8_epoch_accept(&live, 11U);
	flags = tag_relay8_epoch_encode_flags(0U, &sweep);
	assert(((flags & BSL_FLAG_SUPERFRAME_MASK) >>
		BSL_FLAG_SUPERFRAME_SHIFT) == 10U);
	assert((flags & BSL_FLAG_SUPERFRAME_VALID) != 0U);
	tag_relay8_epoch_snapshot(&sweep, NULL);
	assert(!sweep.valid);
	tag_relay8_epoch_snapshot(NULL, &live);
}

int main(void)
{
	test_idle_clears_beacon_state();
	test_epoch_label_lifecycle_and_wrap();
	test_only_epoch_flag_bits_change();
	test_epoch_snapshot_survives_next_beacon();
	puts("tag_relay8: PASS");
	return 0;
}
