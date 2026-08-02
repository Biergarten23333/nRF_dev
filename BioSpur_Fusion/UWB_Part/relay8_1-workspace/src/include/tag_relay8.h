#ifndef TAG_RELAY8_H
#define TAG_RELAY8_H

#include "biospur_link.h"
#include "tag_beacon_sync.h"
#include "uwb_tdma.h"

struct tag_relay8_epoch_label {
	uint32_t counter;
	bool valid;
};

static inline void tag_relay8_epoch_invalidate(
	struct tag_relay8_epoch_label *epoch)
{
	if (epoch == NULL) {
		return;
	}
	epoch->counter = 0U;
	epoch->valid = false;
}

static inline void tag_relay8_epoch_accept(
	struct tag_relay8_epoch_label *epoch, uint32_t counter)
{
	if (epoch == NULL) {
		return;
	}
	epoch->counter = counter;
	epoch->valid = true;
}

static inline void tag_relay8_epoch_coast(
	struct tag_relay8_epoch_label *epoch)
{
	if (epoch != NULL && epoch->valid) {
		epoch->counter++;
	}
}

static inline void tag_relay8_epoch_snapshot(
	struct tag_relay8_epoch_label *snapshot,
	const struct tag_relay8_epoch_label *epoch)
{
	if (snapshot == NULL) {
		return;
	}
	if (epoch == NULL) {
		tag_relay8_epoch_invalidate(snapshot);
		return;
	}
	*snapshot = *epoch;
}

static inline uint8_t tag_relay8_epoch_encode_flags(
	uint8_t flags, const struct tag_relay8_epoch_label *epoch)
{
	flags &= (uint8_t)~(BSL_FLAG_SUPERFRAME_MASK |
			   BSL_FLAG_SUPERFRAME_VALID);
	if (epoch != NULL && epoch->valid) {
		flags |= (uint8_t)((epoch->counter & 0x0fU) <<
				   BSL_FLAG_SUPERFRAME_SHIFT);
		flags |= BSL_FLAG_SUPERFRAME_VALID;
	}
	return flags;
}

static inline void tag_relay8_apply_idle_beacon_policy(
	struct uwb_tag_runtime_params *params)
{
	if (params == NULL) {
		return;
	}
	params->beacon_sync = false;
	params->beacon_win_n = TAG_BEACON_WINDOW_N_DEFAULT;
}

#endif /* TAG_RELAY8_H */
