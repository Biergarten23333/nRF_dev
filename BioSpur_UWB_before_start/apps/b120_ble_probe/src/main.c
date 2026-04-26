#include <errno.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/usb/usb_device.h>
#include <string.h>

static const struct device *const cdc = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
static K_SEM_DEFINE(bt_sem, 0, 1);
static int bt_cb_err = -999;

static void write_str(const char *s)
{
	while (*s) {
		uart_poll_out(cdc, *s++);
	}
}

static void bt_ready(int err)
{
	bt_cb_err = err;
	write_str("[BLE_PROBE] bt_ready_cb\r\n");
	k_sem_give(&bt_sem);
}

int main(void)
{
	int rc = usb_enable(NULL);
	if (rc) {
		return rc;
	}
	if (!device_is_ready(cdc)) {
		return -ENODEV;
	}
	write_str("B120_BLE_PROBE_READY before_bt\r\n");
	rc = bt_enable(bt_ready);
	if (rc) {
		write_str("[BLE_PROBE] bt_enable immediate error\r\n");
	} else {
		write_str("[BLE_PROBE] bt_enable returned 0\r\n");
	}
	if (k_sem_take(&bt_sem, K_SECONDS(15)) == 0) {
		write_str(bt_cb_err == 0 ? "[BLE_PROBE] bt ok\r\n" : "[BLE_PROBE] bt cb error\r\n");
	} else {
		write_str("[BLE_PROBE] bt timeout\r\n");
	}
	while (1) {
		unsigned char ch;
		if (uart_poll_in(cdc, &ch) == 0) {
			if (ch == '\n') {
				write_str("[BLE_PROBE] alive\r\n");
			}
		} else {
			k_sleep(K_MSEC(10));
		}
	}
}
