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

int main(void)
{
	test_lift_without_wrap();
	test_lift_forward_across_u32_wrap();
	test_lift_backward_across_u32_wrap();
	test_half_range_contract();
	test_cycle_base();
	test_late_tolerance_budget();
	puts("broadcast_tdma_math: PASS");
	return 0;
}
