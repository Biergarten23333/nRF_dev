/*
 * BioSpur Master_Tag carrier-v2 network-core resilience.
 *
 * This module adds no work to the radio or HCI data paths. The watchdog is
 * fed by the lowest-priority application thread, so an interrupt-locked fatal
 * loop or a scheduler/core wedge causes a network-core reset.
 */

#include <errno.h>
#include <stdint.h>

#include <hal/nrf_reset.h>
#include <zephyr/device.h>
#include <zephyr/drivers/watchdog.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/printk.h>

#define NET_WDT_TIMEOUT_MS 8000U
#define NET_WDT_FEED_MS 1000U
#define NET_WDT_STACK_SIZE 768

static const struct device *const net_wdt =
	DEVICE_DT_GET(DT_ALIAS(watchdog0));
static int net_wdt_channel = -1;
static atomic_t net_wdt_ready;

static int net_resilience_init(void)
{
	const uint32_t resetreas = nrf_reset_resetreas_get(NRF_RESET_NS);
	const struct wdt_timeout_cfg timeout_cfg = {
		.window = {
			.min = 0U,
			.max = NET_WDT_TIMEOUT_MS,
		},
		.callback = NULL,
		.flags = WDT_FLAG_RESET_SOC,
	};
	int rc;

	printk("NET_BOOT carrier=master-tag-carrier-v2 resetreas=0x%08x\n",
	       resetreas);
	nrf_reset_resetreas_clear(NRF_RESET_NS, resetreas);

	if (!device_is_ready(net_wdt)) {
		printk("NET_WDT init=FAIL reason=device_not_ready\n");
		return -ENODEV;
	}

	rc = wdt_install_timeout(net_wdt, &timeout_cfg);
	if (rc < 0) {
		printk("NET_WDT init=FAIL stage=install rc=%d\n", rc);
		return rc;
	}
	net_wdt_channel = rc;

	rc = wdt_setup(net_wdt, WDT_OPT_PAUSE_HALTED_BY_DBG);
	if (rc != 0) {
		printk("NET_WDT init=FAIL stage=setup rc=%d\n", rc);
		return rc;
	}

	atomic_set(&net_wdt_ready, 1);
	printk("NET_WDT init=OK timeout_ms=%u feeder=lowest_priority\n",
	       NET_WDT_TIMEOUT_MS);
	return 0;
}

SYS_INIT(net_resilience_init, APPLICATION, 90);

static void net_wdt_feeder(void *unused1, void *unused2, void *unused3)
{
	ARG_UNUSED(unused1);
	ARG_UNUSED(unused2);
	ARG_UNUSED(unused3);

	while (true) {
		if (atomic_get(&net_wdt_ready) != 0) {
			const int rc = wdt_feed(net_wdt, net_wdt_channel);

			if (rc != 0) {
				printk("NET_WDT feed=FAIL rc=%d\n", rc);
			}
		}
		k_sleep(K_MSEC(NET_WDT_FEED_MS));
	}
}

K_THREAD_DEFINE(net_wdt_feeder_id, NET_WDT_STACK_SIZE, net_wdt_feeder,
		NULL, NULL, NULL, K_LOWEST_APPLICATION_THREAD_PRIO, 0, 0);
