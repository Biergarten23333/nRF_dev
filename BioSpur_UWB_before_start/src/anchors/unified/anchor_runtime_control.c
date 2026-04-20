#include "anchor_runtime_control.h"

#include <zephyr/sys/atomic.h>

static atomic_t g_stop_requested;
static atomic_t g_requested_role;
static atomic_t g_requested_master_sweeps;

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

void anchor_runtime_request_role_switch(uint8_t role, uint32_t master_sweeps)
{
	atomic_set(&g_requested_role, role);
	atomic_set(&g_requested_master_sweeps, (atomic_val_t)master_sweeps);
	anchor_runtime_request_stop();
}

uint8_t anchor_runtime_requested_role(void)
{
	return (uint8_t)atomic_get(&g_requested_role);
}

uint32_t anchor_runtime_requested_master_sweeps(void)
{
	return (uint32_t)atomic_get(&g_requested_master_sweeps);
}

void anchor_runtime_clear_role_switch(void)
{
	atomic_clear(&g_requested_role);
	atomic_clear(&g_requested_master_sweeps);
}
