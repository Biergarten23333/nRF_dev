#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include "app_ble.h"
#include "cdc_async.h"
#include "ota_bridge.h"
#include "tsync_master.h"
#include "wdt_monitor.h"

LOG_MODULE_REGISTER(central_main, LOG_LEVEL_INF);

int main(void)
{
	int err;

	LOG_INF("BSGR Central framework bring-up");

	err = wdt_monitor_init();
	if (err != 0) {
		return err;
	}

	tsync_master_init();

	err = cdc_async_init();
	if (err != 0) {
		LOG_ERR("cdc_async_init failed: %d", err);
		return err;
	}

	err = app_ble_init();
	if (err != 0) {
		LOG_ERR("app_ble_init failed: %d", err);
		return err;
	}

	err = ota_bridge_init();
	if (err != 0) {
		LOG_ERR("ota_bridge_init failed: %d", err);
		return err;
	}

	err = app_ble_start_scan();
	if (err != 0) {
		LOG_WRN("app_ble_start_scan degraded: %d", err);
	}

	while (1) {
		ota_bridge_process();
		app_ble_process();
		wdt_monitor_feed();
		k_sleep(K_MSEC(20));
	}

	return 0;
}
