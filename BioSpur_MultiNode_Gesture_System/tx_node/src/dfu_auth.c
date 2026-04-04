#include "dfu_auth.h"

static bool g_dfu_auth_permitted;

void dfu_auth_init(void)
{
	g_dfu_auth_permitted = false;
}

bool dfu_auth_is_permitted(void)
{
	return g_dfu_auth_permitted;
}
