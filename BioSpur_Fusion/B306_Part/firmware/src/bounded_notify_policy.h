#ifndef BIOSPUR_BOUNDED_NOTIFY_POLICY_H
#define BIOSPUR_BOUNDED_NOTIFY_POLICY_H

#include <stdbool.h>
#include <stdint.h>

struct bsf_bounded_notify_policy {
	bool worker_busy;
	bool fast_drop;
	uint32_t submitted;
	uint32_t timeout_drops;
};

static inline bool bsf_notify_submit(struct bsf_bounded_notify_policy *state)
{
	if (state->worker_busy) {
		state->timeout_drops++;
		state->fast_drop = true;
		return false;
	}
	state->worker_busy = true;
	state->submitted++;
	return true;
}

static inline void bsf_notify_complete(struct bsf_bounded_notify_policy *state)
{
	state->worker_busy = false;
	state->fast_drop = false;
}

#endif
