#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include "wdt_monitor.h"

LOG_MODULE_REGISTER(tx_wdt_monitor, LOG_LEVEL_INF);

void wdt_monitor_init(void)
{
	LOG_INF("Watchdog monitor stub initialized");
}

void wdt_monitor_feed(void)
{
}
