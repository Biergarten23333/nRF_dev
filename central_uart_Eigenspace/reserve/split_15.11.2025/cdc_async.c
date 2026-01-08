#include "cdc_async.h"
#include <zephyr/usb/usb_device.h>
#include <stdarg.h>
#include <stdio.h>

static const struct device *cdc_dev;
static bool cdc_ready;
static bool stream_enabled;
static struct k_mutex cdc_tx_lock;

int cdc_async_init(void)
{
    int err;

    if (cdc_ready) {
        return 0;
    }

    cdc_dev = DEVICE_DT_GET_ONE(zephyr_cdc_acm_uart);
    if (!device_is_ready(cdc_dev)) {
        return -ENODEV;
    }

    err = usb_enable(NULL);
    /* ⭐ 这里是关键：-EALREADY 表示已经启用，算成功 ⭐ */
    if (err && err != -EALREADY) {
        return err;
    }

    k_mutex_init(&cdc_tx_lock);
    stream_enabled = false;
    cdc_ready      = true;

    return 0;
}


void cdc_async_enable(bool enable)
{
    stream_enabled = enable;
}

bool cdc_async_is_enabled(void)
{
    return stream_enabled;
}

int cdc_async_write(const uint8_t *data, size_t len)
{
    if (!cdc_ready) {
        return -ENODEV;
    }
    if (!stream_enabled) {
        /* Streaming disabled: silently drop, report 0 written */
        return 0;
    }
    if (!data || !len) {
        return 0;
    }

    k_mutex_lock(&cdc_tx_lock, K_FOREVER);

    for (size_t i = 0; i < len; i++) {
        uart_poll_out(cdc_dev, data[i]);
    }

    k_mutex_unlock(&cdc_tx_lock);

    return (int)len;
}

void cdc_printf(const char *fmt, ...)
{
    if (!cdc_ready) {
        return;
    }

    char buf[256];

    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintk(buf, sizeof(buf), fmt, ap);
    va_end(ap);

    if (n <= 0) {
        return;
    }

    k_mutex_lock(&cdc_tx_lock, K_FOREVER);

    for (int i = 0; i < n; i++) {
        uart_poll_out(cdc_dev, buf[i]);
    }

    k_mutex_unlock(&cdc_tx_lock);
}

int cdc_async_poll_in(uint8_t *ch)
{
    if (!cdc_ready || !ch) {
        return -ENODEV;
    }

    return uart_poll_in(cdc_dev, ch);
}
