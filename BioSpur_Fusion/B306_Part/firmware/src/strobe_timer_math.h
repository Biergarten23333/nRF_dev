#ifndef BSF_STROBE_TIMER_MATH_H
#define BSF_STROBE_TIMER_MATH_H

#include <stdbool.h>
#include <stdint.h>

/*
 * TIMER2 runs freely at 1 MHz.  COMPARE0 is programmed to UINT32_MAX and its
 * ISR increments announced_wraps one tick before the counter naturally rolls
 * to zero.  This keeps the interrupt away from the zero boundary that caused
 * the deployed first-wrap outage, without shortening the 2^32-tick epoch.
 */
static inline uint64_t bsf_timer_epoch_resolve(uint32_t announced_wraps,
					       bool compare_pending,
					       uint32_t current_low)
{
	uint64_t epoch = announced_wraps;

	/* IRQ has not run, but the free-running counter already crossed zero. */
	if (compare_pending && current_low < 0x80000000u) {
		++epoch;
	/* IRQ ran while a software capture made at UINT32_MAX was being read. */
	} else if (!compare_pending && current_low == UINT32_MAX && epoch != 0u) {
		--epoch;
	}

	return epoch;
}

static inline uint64_t bsf_timer_expand_capture(uint32_t announced_wraps,
						bool compare_pending,
						uint32_t current_low,
						uint32_t captured_low)
{
	uint64_t epoch = bsf_timer_epoch_resolve(announced_wraps,
						 compare_pending, current_low);

	/* The edge preceded current_low.  A large backwards discontinuity means
	 * its CC value belongs to the immediately preceding epoch. */
	if (captured_low > current_low &&
	    (captured_low - current_low) > 0x80000000u && epoch != 0u) {
		--epoch;
	}

	return (epoch << 32) | captured_low;
}

#endif /* BSF_STROBE_TIMER_MATH_H */
