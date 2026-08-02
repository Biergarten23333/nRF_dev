#ifndef BSF_STROBE_TIMER_MATH_H
#define BSF_STROBE_TIMER_MATH_H

#include <stdbool.h>
#include <stdint.h>

/*
 * TIMER2 runs freely at 1 MHz. COMPARE0 is programmed to the selected
 * counter-width maximum and its ISR increments announced_wraps one tick
 * before the counter naturally rolls to zero. Production uses 32 bits;
 * accelerated boundary builds use the same math at 16 and 24 bits.
 */
static inline uint32_t bsf_timer_counter_mask(uint8_t counter_bits)
{
	return counter_bits == 32u ? UINT32_MAX :
		(uint32_t)((1ULL << counter_bits) - 1ULL);
}

static inline uint64_t bsf_timer_epoch_resolve(uint32_t announced_wraps,
					       bool compare_pending,
					       uint32_t current_low,
					       uint8_t counter_bits)
{
	uint64_t epoch = announced_wraps;
	uint32_t mask = bsf_timer_counter_mask(counter_bits);
	uint32_t half = (uint32_t)(1ULL << (counter_bits - 1u));

	current_low &= mask;

	/* IRQ has not run, but the free-running counter already crossed zero. */
	if (compare_pending && current_low < half) {
		++epoch;
	/* IRQ ran while a software capture made at the maximum was being read. */
	} else if (!compare_pending && current_low == mask && epoch != 0u) {
		--epoch;
	}

	return epoch;
}

static inline uint64_t bsf_timer_expand_capture(uint32_t announced_wraps,
						bool compare_pending,
						uint32_t current_low,
						uint32_t captured_low,
						uint8_t counter_bits)
{
	uint32_t mask = bsf_timer_counter_mask(counter_bits);
	uint32_t half = (uint32_t)(1ULL << (counter_bits - 1u));
	uint64_t epoch = bsf_timer_epoch_resolve(announced_wraps,
						 compare_pending, current_low,
						 counter_bits);

	current_low &= mask;
	captured_low &= mask;

	/* The edge preceded current_low.  A large backwards discontinuity means
	 * its CC value belongs to the immediately preceding epoch. */
	if (captured_low > current_low &&
	    (captured_low - current_low) > half && epoch != 0u) {
		--epoch;
	}

	return (epoch << counter_bits) | captured_low;
}

#endif /* BSF_STROBE_TIMER_MATH_H */
