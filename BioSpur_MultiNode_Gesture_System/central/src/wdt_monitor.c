#include <zephyr/device.h>
#include <zephyr/drivers/watchdog.h>
#include <zephyr/logging/log.h>

#include "wdt_monitor.h"

LOG_MODULE_REGISTER(central_wdt_monitor, LOG_LEVEL_INF);

static const struct device *wdt_dev;
static int wdt_channel = -1;

int wdt_monitor_init(void)
{
#if defined(BSGR_CENTRAL_SAFE_BOOT) && (BSGR_CENTRAL_SAFE_BOOT == 1)
	LOG_WRN("Safe-boot mode: watchdog arming deferred");
	return 0;
#else
#if DT_NODE_EXISTS(DT_NODELABEL(wdt0))
	static const struct wdt_timeout_cfg timeout_cfg = {
		.window = {
			.min = 0U,
			.max = 4000U,
		},
		.callback = NULL,
		.flags = WDT_FLAG_RESET_SOC,
	};

	wdt_dev = DEVICE_DT_GET(DT_NODELABEL(wdt0));
	if (!device_is_ready(wdt_dev)) {
		wdt_dev = NULL;
		return 0;
	}

	wdt_channel = wdt_install_timeout(wdt_dev, &timeout_cfg);
	if (wdt_channel < 0) {
		wdt_dev = NULL;
		return 0;
	}

	if (wdt_setup(wdt_dev, 0U) != 0) {
		wdt_dev = NULL;
		wdt_channel = -1;
		return 0;
	}

	LOG_INF("Watchdog armed");
#else
	LOG_INF("Watchdog node not present, stub mode");
#endif
	return 0;
#endif
}

void wdt_monitor_feed(void)
{
	if ((wdt_dev == NULL) || (wdt_channel < 0)) {
		return;
	}

	(void)wdt_feed(wdt_dev, wdt_channel);
}
