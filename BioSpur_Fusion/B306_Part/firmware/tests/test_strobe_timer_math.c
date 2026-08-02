#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#include "../src/strobe_timer_math.h"

static void test_epoch(uint8_t bits, uint32_t wraps, bool pending,
		       uint32_t low, uint64_t expected)
{
	assert(bsf_timer_epoch_resolve(wraps, pending, low, bits) == expected);
}

static void test_width(uint8_t bits)
{
	uint32_t mask = bsf_timer_counter_mask(bits);

	/* Boot and ordinary positions in epochs 0, 1, and 2. */
	test_epoch(bits, 0, false, 0u, 0u);
	test_epoch(bits, 0, false, 123u, 0u);
	test_epoch(bits, 1, false, 123u, 1u);
	test_epoch(bits, 2, false, mask / 2u, 2u);

	/* IRQ pending immediately before and after each of two natural wraps. */
	test_epoch(bits, 0, true, mask, 0u);
	test_epoch(bits, 0, true, 0u, 1u);
	test_epoch(bits, 1, true, mask, 1u);
	test_epoch(bits, 1, true, 0u, 2u);

	/* IRQ won the race while software still holds the pre-wrap capture. */
	test_epoch(bits, 1, false, mask, 0u);
	test_epoch(bits, 2, false, mask, 1u);

	/* Hardware edge at the old epoch, software read just after rollover. */
	assert(bsf_timer_expand_capture(1, false, 3u, mask - 4u, bits) ==
	       (uint64_t)mask - 4u);
	assert(bsf_timer_expand_capture(2, false, 7u, mask - 2u, bits) ==
	       (1ULL << bits) + mask - 2u);

	/* Edge and software read in the new epoch. */
	assert(bsf_timer_expand_capture(2, false, 30u, 20u, bits) ==
	       (2ULL << bits) + 20u);
}

int main(void)
{
	test_width(16u);
	test_width(24u);
	test_width(32u);

	puts("STROBE_TIMER_MATH_PASS bits=16/24/32 wraps=2");
	return 0;
}
