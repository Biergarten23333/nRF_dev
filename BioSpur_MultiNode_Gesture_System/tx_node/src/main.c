#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include "ble_link.h"
#include "dfu_auth.h"
#include "tsync.h"
#include "wdt_monitor.h"

LOG_MODULE_REGISTER(tx_main, LOG_LEVEL_INF);

#define BSGR_TX_DEVICE_ID 0x1001u

int main(void)
{
	int err;

	LOG_INF("BSGR TX OTA-first bring-up");

	dfu_auth_init();
	tsync_init();
	wdt_monitor_init();

	err = ble_link_init(BSGR_TX_DEVICE_ID);
	if (err) {
		LOG_ERR("ble_link_init failed: %d", err);
		return err;
	}

	while (1) {
		wdt_monitor_feed();
		k_sleep(K_MSEC(1000));
	}

	return 0;
}
