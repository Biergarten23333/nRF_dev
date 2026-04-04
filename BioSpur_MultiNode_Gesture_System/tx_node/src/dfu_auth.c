#include "dfu_auth.h"

static struct bsgr_dfu_auth_state g_dfu_auth_state;

void dfu_auth_init(void)
{
	g_dfu_auth_state.prepared = false;
	g_dfu_auth_state.authorized = false;
	g_dfu_auth_state.active = false;
	g_dfu_auth_state.session_id = 0U;
	g_dfu_auth_state.last_request_id = 0U;
	g_dfu_auth_state.last_result = BSGR_CMD_RESULT_DEFERRED;
}

void dfu_auth_prepare(uint16_t request_id)
{
	g_dfu_auth_state.prepared = true;
	g_dfu_auth_state.last_request_id = request_id;
	g_dfu_auth_state.last_result = BSGR_CMD_RESULT_DEFERRED;
}

void dfu_auth_set_authorized(bool authorized, uint16_t request_id)
{
	g_dfu_auth_state.authorized = authorized;
	g_dfu_auth_state.last_request_id = request_id;
	g_dfu_auth_state.last_result = authorized ? BSGR_CMD_RESULT_OK : BSGR_CMD_RESULT_REJECTED;
}

void dfu_auth_begin(uint16_t session_id)
{
	g_dfu_auth_state.active = g_dfu_auth_state.authorized;
	g_dfu_auth_state.session_id = session_id;
}

void dfu_auth_end(void)
{
	g_dfu_auth_state.active = false;
	g_dfu_auth_state.prepared = false;
}

bool dfu_auth_is_permitted(void)
{
	return g_dfu_auth_state.prepared && g_dfu_auth_state.authorized;
}

const struct bsgr_dfu_auth_state *dfu_auth_state_get(void)
{
	return &g_dfu_auth_state;
}
