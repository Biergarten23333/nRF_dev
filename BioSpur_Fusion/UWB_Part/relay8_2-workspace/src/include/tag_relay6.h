#ifndef TAG_RELAY6_H
#define TAG_RELAY6_H

#include <stdbool.h>
#include <stdint.h>

#include "tag_beacon_sync.h"

#define TAG_RELAY6_BEACON_STATUS_FORMAT \
	"BEACON sync=%u lock=%u rx=%lu promoted=%u mismatch=%lu miss=%lu " \
	"gen=%u counter=%lu rebase=%lu dw=%u dwmiss=%lu"

#define TAG_RELAY7_BEACON_STATUS_FORMAT \
		TAG_RELAY6_BEACON_STATUS_FORMAT " win=%u rxarm=%lu"

enum tag_relay6_arm_state {
	TAG_RELAY6_ARM_WAIT = 0,
	TAG_RELAY6_ARM_READY = 1,
	TAG_RELAY6_ARM_MISSED = 2,
};

/*
 * relay6 owns the public sweep sequence at the tag. Beacon counters identify
 * the timing epoch only and can restart without moving this value backward.
 */
static inline uint32_t tag_relay6_public_sweep(uint32_t local_sweep)
{
	return local_sweep;
}

static inline bool tag_relay6_generation_rebase(bool locked,
						 uint8_t previous,
						 uint8_t current)
{
	return locked && previous != current;
}

static inline bool tag_relay6_can_anchor(bool requested, bool locked)
{
	return requested && locked;
}

static inline bool tag_relay6_dw_anchor_value(
	bool present, uint32_t value, bool *enabled_out)
{
	if (enabled_out == NULL || value > 1U) {
		return false;
	}
	*enabled_out = present && value != 0U;
	return true;
}

static inline enum tag_relay6_arm_state tag_relay6_arm_state40(
	uint64_t target_system40, uint64_t now40, uint64_t arm_lead_ticks)
{
	int64_t remaining = uwb_beacon_diff40(target_system40, now40);

	if (remaining <= 0) {
		return TAG_RELAY6_ARM_MISSED;
	}
	if ((uint64_t)remaining > arm_lead_ticks) {
		return TAG_RELAY6_ARM_WAIT;
	}
	return TAG_RELAY6_ARM_READY;
}

/*
 * Project the first slot target strictly after now from an accepted beacon
 * origin. The calculation stays entirely in the DW1000 40-bit clock domain.
 */
static inline bool tag_relay6_next_slot_target40(
	uint64_t origin40, uint64_t now40, uint32_t slot_offset_us,
	uint32_t cycle_period_us, uint64_t *target40_out)
{
	uint64_t cycle_ticks;
	uint64_t target40;
	int64_t delta;

	if (cycle_period_us == 0U || target40_out == NULL) {
		return false;
	}

	cycle_ticks = uwb_beacon_us_to_dw_ticks(cycle_period_us);
	target40 = uwb_beacon_add40(
		origin40, uwb_beacon_us_to_dw_ticks(slot_offset_us));
	delta = uwb_beacon_diff40(target40, now40);
	if (delta <= 0) {
		uint64_t behind_ticks = (uint64_t)(-delta);
		uint64_t cycles = (behind_ticks / cycle_ticks) + 1U;

		target40 = uwb_beacon_add40(target40, cycles * cycle_ticks);
	}

	*target40_out = target40;
	return true;
}

#endif /* TAG_RELAY6_H */
