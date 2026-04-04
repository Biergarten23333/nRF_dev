#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include "app_ble.h"
#include "cdc_async.h"
#include "tsync_master.h"
#include "wdt_monitor.h"

LOG_MODULE_REGISTER(central_main, LOG_LEVEL_INF);

int main(void)
{
	int err;

	LOG_INF("BSGR Central OTA-first bring-up");

	wdt_monitor_init();
	tsync_master_init();

	err = cdc_async_init();
	if (err) {
		LOG_ERR("cdc_async_init failed: %d", err);
		return err;
	}

	err = app_ble_init();
	if (err) {
		LOG_ERR("app_ble_init failed: %d", err);
		return err;
	}

	while (1) {
		wdt_monitor_feed();
		k_sleep(K_MSEC(1000));
	}

	return 0;
}
