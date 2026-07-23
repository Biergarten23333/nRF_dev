#ifndef BROADCAST_TDMA_MATH_H
#define BROADCAST_TDMA_MATH_H

#include <stdint.h>

/*
 * Lift a 32-bit millisecond timestamp onto the nearest point on a 64-bit
 * uptime axis. This is wrap-safe while value_ms is within INT32_MAX ms of the
 * reference, which is the same half-range contract used by Zephyr timeouts.
 */
static inline int64_t broadcast_tdma_lift_u32_ms(uint32_t value_ms,
						 int64_t reference_ms)
{
	return reference_ms +
	       (int64_t)(int32_t)(value_ms - (uint32_t)reference_ms);
}

/* Return the current schedule-cycle base, or epoch_us while it is in future. */
static inline int64_t broadcast_tdma_cycle_base_us(int64_t now_us,
					    int64_t epoch_us,
					    uint64_t cycle_us)
{
	if (now_us <= epoch_us || cycle_us == 0U) {
		return epoch_us;
	}

	return epoch_us +
	       (int64_t)(((uint64_t)(now_us - epoch_us) / cycle_us) * cycle_us);
}

/*
 * Bound accepted start lateness by the physical slot budget.  The configured
 * tolerance is only a ceiling: the sweep must still have required_us before
 * the next slot boundary.
 */
static inline uint64_t broadcast_tdma_late_tolerance_us(
	uint64_t slot_period_us,
	uint64_t required_us,
	uint64_t configured_tolerance_us)
{
	uint64_t available_us;

	if (slot_period_us <= required_us) {
		return 0U;
	}

	available_us = slot_period_us - required_us;
	return configured_tolerance_us < available_us ?
		       configured_tolerance_us : available_us;
}

#endif /* BROADCAST_TDMA_MATH_H */
