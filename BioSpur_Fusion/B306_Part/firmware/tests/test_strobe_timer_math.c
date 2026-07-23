#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#include "../src/strobe_timer_math.h"

static void test_epoch(uint32_t wraps, bool pending, uint32_t low,
		       uint64_t expected)
{
	assert(bsf_timer_epoch_resolve(wraps, pending, low) == expected);
}

int main(void)
{
	/* Boot and ordinary positions in epochs 0, 1, and 2. */
	test_epoch(0, false, 0u, 0u);
	test_epoch(0, false, 123u, 0u);
	test_epoch(1, false, 123u, 1u);
	test_epoch(2, false, 0x80000000u, 2u);

	/* IRQ pending immediately before and after each of two natural wraps. */
	test_epoch(0, true, UINT32_MAX, 0u);
	test_epoch(0, true, 0u, 1u);
	test_epoch(1, true, UINT32_MAX, 1u);
	test_epoch(1, true, 0u, 2u);

	/* IRQ won the race while software still holds the pre-wrap capture. */
	test_epoch(1, false, UINT32_MAX, 0u);
	test_epoch(2, false, UINT32_MAX, 1u);

	/* Hardware edge at the old epoch, software read just after rollover. */
	assert(bsf_timer_expand_capture(1, false, 3u, UINT32_MAX - 4u) ==
	       (uint64_t)UINT32_MAX - 4u);
	assert(bsf_timer_expand_capture(2, false, 7u, UINT32_MAX - 2u) ==
	       (1ULL << 32) + UINT32_MAX - 2u);

	/* Edge and software read in the new epoch. */
	assert(bsf_timer_expand_capture(2, false, 30u, 20u) ==
	       (2ULL << 32) + 20u);

	puts("STROBE_TIMER_MATH_PASS wraps=2");
	return 0;
}
