#include "tsync_master.h"

static int64_t g_epoch_ms;

void tsync_master_init(void)
{
	g_epoch_ms = 0;
}

void tsync_master_set_epoch_ms(int64_t epoch_ms)
{
	g_epoch_ms = epoch_ms;
}

int64_t tsync_master_get_epoch_ms(void)
{
	return g_epoch_ms;
}
