#include "tsync.h"

static int32_t g_host_offset_ms;

void tsync_init(void)
{
	g_host_offset_ms = 0;
}

void tsync_set_host_offset_ms(int32_t offset_ms)
{
	g_host_offset_ms = offset_ms;
}

int32_t tsync_get_host_offset_ms(void)
{
	return g_host_offset_ms;
}
