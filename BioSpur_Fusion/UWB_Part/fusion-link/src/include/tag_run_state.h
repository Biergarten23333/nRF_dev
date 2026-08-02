#ifndef TAG_RUN_STATE_H
#define TAG_RUN_STATE_H

#include <stdbool.h>

#include "uwb_tdma.h"

static inline void tag_run_state_set(struct uwb_tag_runtime_params *params,
				     bool run)
{
	if (params != NULL) {
		params->tdma.enabled = run;
	}
}

static inline bool tag_run_state_can_cfg_stop(
	const struct uwb_tag_runtime_params *params)
{
	return params != NULL && params->tdma.epoch_valid;
}

static inline bool tag_run_state_holds_radio(
	const struct uwb_tag_runtime_params *params)
{
	return params != NULL &&
	       params->positioning_mode != UWB_TAG_MODE_IDLE &&
	       params->tdma.epoch_valid && !params->tdma.enabled;
}

#endif /* TAG_RUN_STATE_H */
