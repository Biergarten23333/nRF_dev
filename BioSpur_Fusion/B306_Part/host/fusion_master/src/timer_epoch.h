#ifndef BIOSPUR_FUSION_TIMER_EPOCH_H
#define BIOSPUR_FUSION_TIMER_EPOCH_H

#include <stdint.h>

/*
 * Return the 64-bit value congruent with low modulo 2^32 that is nearest to
 * reference. The caller supplies a recent full-width timestamp from this node
 * (normally the preceding IMU base, otherwise UWB or telemetry).
 */
static inline uint64_t bsf_extend_low32_near(uint32_t low, uint64_t reference)
{
	const uint64_t period = UINT64_C(1) << 32;
	const uint64_t half = period >> 1;
	uint64_t candidate = (reference & ~(period - 1u)) | low;

	if (candidate < reference && reference - candidate > half) {
		candidate += period;
	} else if (candidate > reference && candidate - reference > half &&
		   candidate >= period) {
		candidate -= period;
	}
	return candidate;
}

#endif /* BIOSPUR_FUSION_TIMER_EPOCH_H */
