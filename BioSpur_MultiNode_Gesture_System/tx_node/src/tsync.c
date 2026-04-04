#include "tsync.h"

static struct bsgr_tsync_state g_tsync_state;

void tsync_init(void)
{
	g_tsync_state.host_offset_ms = 0;
	g_tsync_state.session_id = 1U;
	g_tsync_state.last_request_id = 0U;
}

void tsync_set_host_offset_ms(int32_t offset_ms)
{
	g_tsync_state.host_offset_ms = offset_ms;
}

int32_t tsync_get_host_offset_ms(void)
{
	return g_tsync_state.host_offset_ms;
}

uint16_t tsync_next_request_id(void)
{
	return ++g_tsync_state.last_request_id;
}

uint16_t tsync_get_session_id(void)
{
	return g_tsync_state.session_id;
}

void tsync_advance_session(void)
{
	++g_tsync_state.session_id;
}

void tsync_fill_payload(struct bsgr_tsync_payload *payload, uint8_t role, uint32_t reference_ticks)
{
	if (payload == NULL) {
		return;
	}

	payload->role = role;
	payload->reserved0 = 0U;
	payload->session_id = g_tsync_state.session_id;
	payload->host_time_offset_ms = g_tsync_state.host_offset_ms;
	payload->reference_ticks = reference_ticks;
}
