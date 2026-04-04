#include <errno.h>
#include <stdbool.h>

#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/util.h>
#include <zephyr/usb/usb_device.h>

#include "cdc_async.h"

static const struct device *cdc_devices[BSGR_CDC_CHANNEL_COUNT] = {
	DEVICE_DT_GET(DT_NODELABEL(cdc_acm_uart0)),
	DEVICE_DT_GET(DT_NODELABEL(cdc_acm_uart1)),
};

static struct k_mutex cdc_lock;
static bool cdc_ready;

int cdc_async_init(void)
{
	int err;
	size_t i;

	if (cdc_ready) {
		return 0;
	}

	for (i = 0; i < ARRAY_SIZE(cdc_devices); ++i) {
		if (!device_is_ready(cdc_devices[i])) {
			return -ENODEV;
		}
	}

	err = usb_enable(NULL);
	if ((err != 0) && (err != -EALREADY)) {
		return err;
	}

	k_mutex_init(&cdc_lock);
	cdc_ready = true;
	return 0;
}

int cdc_async_write(enum bsgr_cdc_channel channel, const uint8_t *data, size_t len)
{
	size_t i;

	if ((!cdc_ready) || (channel >= BSGR_CDC_CHANNEL_COUNT) || (data == NULL)) {
		return -EINVAL;
	}

	k_mutex_lock(&cdc_lock, K_FOREVER);
	for (i = 0; i < len; ++i) {
		uart_poll_out(cdc_devices[channel], data[i]);
	}
	k_mutex_unlock(&cdc_lock);

	return (int)len;
}

int cdc_async_poll_in(enum bsgr_cdc_channel channel, uint8_t *ch)
{
	if ((!cdc_ready) || (channel >= BSGR_CDC_CHANNEL_COUNT) || (ch == NULL)) {
		return -EINVAL;
	}

	return uart_poll_in(cdc_devices[channel], ch);
}
