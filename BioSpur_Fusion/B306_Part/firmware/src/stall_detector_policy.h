#ifndef BSF_STALL_DETECTOR_POLICY_H
#define BSF_STALL_DETECTOR_POLICY_H

#include <stdbool.h>
#include <stdint.h>

struct bsf_stall_detector {
	uint32_t last_producer;
	uint32_t last_exit;
	uint32_t frozen_ms;
	uint32_t alarm_count;
	uint32_t recovery_count;
	bool snapshot_valid;
};

struct bsf_stall_decision {
	uint8_t reason;
	bool fire;
	bool take_snapshot;
	bool recover;
};

static inline bool bsf_stall_detector_retract_disconnect(
	struct bsf_stall_detector *state, uint32_t alarm_age_ms,
	uint32_t retract_window_ms, bool recovery_pending)
{
	if (!recovery_pending || alarm_age_ms > retract_window_ms ||
	    state->alarm_count == 0u || state->recovery_count == 0u) {
		return false;
	}
	state->alarm_count--;
	state->recovery_count--;
	state->snapshot_valid = false;
	state->frozen_ms = 0u;
	return true;
}

static inline struct bsf_stall_decision bsf_stall_detector_step(
	struct bsf_stall_detector *state, bool connected, bool subscribed,
	uint32_t successful_notifies, uint32_t arm_notifies,
	uint32_t producer, uint32_t exits, uint8_t in_call_stream,
	uint32_t elapsed_ms, uint32_t dwell_ms, uint32_t max_recoveries)
{
	struct bsf_stall_decision decision = {0};
	bool armed = connected && subscribed &&
		successful_notifies >= arm_notifies;

	if (!armed) {
		state->frozen_ms = 0u;
	} else if (producer != state->last_producer &&
		   exits == state->last_exit) {
		state->frozen_ms += elapsed_ms;
	} else {
		state->frozen_ms = 0u;
	}
	if (armed && state->frozen_ms >= dwell_ms &&
	    state->alarm_count == 0u) {
		decision.reason = in_call_stream != 0u ? 2u : 1u;
		decision.fire = true;
		decision.take_snapshot = !state->snapshot_valid;
		state->snapshot_valid = true;
		state->alarm_count++;
		if (state->recovery_count < max_recoveries) {
			state->recovery_count++;
			decision.recover = true;
		}
	}
	state->last_producer = producer;
	state->last_exit = exits;
	return decision;
}

#endif
