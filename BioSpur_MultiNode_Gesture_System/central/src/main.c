#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include "app_ble.h"
#include "cdc_async.h"
#include "tsync_master.h"
#include "wdt_monitor.h"

LOG_MODULE_REGISTER(central_main, LOG_LEVEL_INF);

static struct k_work_delayable central_service_work;

static void central_service_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);

	app_ble_process();
	wdt_monitor_feed();
	(void)k_work_reschedule(&central_service_work, K_MSEC(100));
}

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

	err = app_ble_start_scan();
	if (err != 0) {
		LOG_WRN("app_ble_start_scan degraded: %d", err);
	}

	k_work_init_delayable(&central_service_work, central_service_work_handler);
	(void)k_work_reschedule(&central_service_work, K_NO_WAIT);

	while (1) {
		k_sleep(K_SECONDS(1));
	}

	return 0;
}
