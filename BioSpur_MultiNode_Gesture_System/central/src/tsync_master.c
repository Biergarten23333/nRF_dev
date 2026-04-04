#include "tsync_master.h"

static struct bsgr_tsync_master_state g_tsync_master;

void tsync_master_init(void)
{
	g_tsync_master.epoch_ms = 0;
	g_tsync_master.session_id = 1U;
	g_tsync_master.request_id = 0U;
}

void tsync_master_set_epoch_ms(int64_t epoch_ms)
{
	g_tsync_master.epoch_ms = epoch_ms;
}

int64_t tsync_master_get_epoch_ms(void)
{
	return g_tsync_master.epoch_ms;
}

uint16_t tsync_master_next_request_id(void)
{
	return ++g_tsync_master.request_id;
}

uint16_t tsync_master_get_session_id(void)
{
	return g_tsync_master.session_id;
}

void tsync_master_fill_payload(struct bsgr_tsync_payload *payload, uint32_t reference_ticks)
{
	if (payload == NULL) {
		return;
	}

	payload->role = 0x01u;
	payload->reserved0 = 0U;
	payload->session_id = g_tsync_master.session_id;
	payload->host_time_offset_ms = (int32_t)g_tsync_master.epoch_ms;
	payload->reference_ticks = reference_ticks;
}
