#include "anchor_runtime_control.h"

#include <zephyr/sys/atomic.h>

static atomic_t g_stop_requested;

void anchor_runtime_request_stop(void)
{
	atomic_set(&g_stop_requested, 1);
}

void anchor_runtime_clear_stop(void)
{
	atomic_clear(&g_stop_requested);
}

bool anchor_runtime_stop_requested(void)
{
	return atomic_get(&g_stop_requested) != 0;
}
