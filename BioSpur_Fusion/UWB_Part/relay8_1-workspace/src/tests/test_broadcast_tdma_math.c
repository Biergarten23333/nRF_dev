#include <assert.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>

#include "../src/broadcast_tdma_math.h"

static void test_lift_without_wrap(void)
{
	assert(broadcast_tdma_lift_u32_ms(900U, 1000) == 900);
	assert(broadcast_tdma_lift_u32_ms(1100U, 1000) == 1100);
}

static void test_lift_forward_across_u32_wrap(void)
{
	const int64_t reference = (int64_t)UINT32_MAX - 15;

	assert(broadcast_tdma_lift_u32_ms(16U, reference) ==
	       reference + 32);
}

static void test_lift_backward_across_u32_wrap(void)
{
	const int64_t reference = (int64_t)UINT32_MAX + 17;

	assert(broadcast_tdma_lift_u32_ms(UINT32_MAX - 15U, reference) ==
	       reference - 32);
}

static void test_half_range_contract(void)
{
	const int64_t reference = (int64_t)UINT32_MAX + 101;

	assert(broadcast_tdma_lift_u32_ms(100U, reference) == reference);
	assert(broadcast_tdma_lift_u32_ms(101U, reference) == reference + 1);
	assert(broadcast_tdma_lift_u32_ms(99U, reference) == reference - 1);
}

static void test_cycle_base(void)
{
	assert(broadcast_tdma_cycle_base_us(999999, 1000000, 100000) ==
	       1000000);
	assert(broadcast_tdma_cycle_base_us(1000000, 1000000, 100000) ==
	       1000000);
	assert(broadcast_tdma_cycle_base_us(1099999, 1000000, 100000) ==
	       1000000);
	assert(broadcast_tdma_cycle_base_us(1100000, 1000000, 100000) ==
	       1100000);
	assert(broadcast_tdma_cycle_base_us(1200123, 1000000, 100000) ==
	       1200000);
}

static void test_late_tolerance_budget(void)
{
	/* 10 ms slot - 8.5 ms sweep = the measured 1.5 ms headroom. */
	assert(broadcast_tdma_late_tolerance_us(10000, 8500, 2000) == 1500);
	assert(broadcast_tdma_late_tolerance_us(10000, 8500, 1000) == 1000);
	assert(broadcast_tdma_late_tolerance_us(8500, 8500, 2000) == 0);
	assert(broadcast_tdma_late_tolerance_us(8000, 8500, 2000) == 0);
}

static void test_superframe_is_schedule_derived(void)
{
	assert(broadcast_tdma_superframe_index(100U, 1000U, 999U, 100U) ==
	       100U);
	assert(broadcast_tdma_superframe_index(100U, 1000U, 1099U, 100U) ==
	       100U);
	assert(broadcast_tdma_superframe_index(100U, 1000U, 1100U, 100U) ==
	       101U);
	/* Four missed transmissions do not change the scheduled index. */
	assert(broadcast_tdma_superframe_index(100U, 1000U, 1500U, 100U) ==
	       105U);
}

static void test_superframe_u32_uptime_wrap(void)
{
	const uint32_t sync = UINT32_MAX - 50U;

	assert(broadcast_tdma_superframe_index(0xfffffff0U, sync, 49U, 100U) ==
	       0xfffffff1U);
	/* Public uint32 index wraps naturally. */
	assert(broadcast_tdma_superframe_index(UINT32_MAX, sync, 49U, 100U) ==
	       0U);
}

static void test_superframe_reboot_requires_new_config(void)
{
	/* A reboot has no valid runtime CFG: preserve legacy local semantics. */
	assert(broadcast_tdma_public_sweep(false, false, 900U, 1000U, 1500U,
					   100U, 0U) == 0U);
	assert(broadcast_tdma_public_sweep(true, false, 900U, 1000U, 1500U,
					   100U, 7U) == 7U);
	/* Once both pieces of CFG state are present, scheduled time wins. */
	assert(broadcast_tdma_public_sweep(true, true, 900U, 1000U, 1500U,
					   100U, 7U) == 905U);
}

static void test_sequential_cfg_delivery_shares_deadline(void)
{
	const uint32_t master_deadline = 10000U;
	const uint32_t tag_a_receive = 9000U;
	const uint32_t tag_b_receive = 9250U;
	const uint32_t tag_a_sync =
		tag_a_receive + (master_deadline - tag_a_receive);
	const uint32_t tag_b_sync =
		tag_b_receive + (master_deadline - tag_b_receive);

	assert(tag_a_sync == master_deadline);
	assert(tag_b_sync == master_deadline);
	assert(broadcast_tdma_public_sweep(true, true, 100U, tag_a_sync,
					   10499U, 100U, 0U) == 104U);
	assert(broadcast_tdma_public_sweep(true, true, 100U, tag_b_sync,
					   10499U, 100U, 0U) == 104U);
}

int main(void)
{
	test_lift_without_wrap();
	test_lift_forward_across_u32_wrap();
	test_lift_backward_across_u32_wrap();
	test_half_range_contract();
	test_cycle_base();
	test_late_tolerance_budget();
	test_superframe_is_schedule_derived();
	test_superframe_u32_uptime_wrap();
	test_superframe_reboot_requires_new_config();
	test_sequential_cfg_delivery_shares_deadline();
	puts("broadcast_tdma_math: PASS");
	return 0;
}
