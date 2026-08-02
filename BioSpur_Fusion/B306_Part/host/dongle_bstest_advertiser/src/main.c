#include <errno.h>
#include <stdint.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/reboot.h>
#include <zephyr/usb/usb_device.h>

#include <hal/nrf_power.h>

#define TEST_NAME "BSBEEF"
#define DFU_TOUCH_BAUD 1200U
#define BOOTLOADER_DFU_START 0xB1U

static const struct device *const cdc_console =
	DEVICE_DT_GET(DT_CHOSEN(zephyr_console));

static const uint8_t tag_token[] = {
	0xff, 0xff, 'B', 0xef, 0xef, 0xbe,
};

static const struct bt_data ad[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR),
	BT_DATA(BT_DATA_NAME_COMPLETE, TEST_NAME, sizeof(TEST_NAME) - 1U),
	BT_DATA(BT_DATA_MANUFACTURER_DATA, tag_token, sizeof(tag_token)),
};

static void enter_dfu_bootloader(void)
{
	printk("BSTEST_DFU 1200-baud touch\n");
	k_msleep(80);
	nrf_power_gpregret_set(NRF_POWER, 0U, BOOTLOADER_DFU_START);
	sys_reboot(SYS_REBOOT_COLD);
}

int main(void)
{
	int err;

	err = usb_enable(NULL);
	if (err != 0 && err != -EALREADY) {
		printk("BSTEST_USB_FAIL err=%d\n", err);
	}

	err = bt_enable(NULL);
	if (err) {
		printk("BSTEST_BT_FAIL err=%d\n", err);
		return err;
	}

	err = bt_le_adv_start(BT_LE_ADV_NCONN, ad, ARRAY_SIZE(ad), NULL, 0);
	printk("BSTEST_READY name=%s token=BSBEEF connectable=0 adv_rc=%d\n",
	       TEST_NAME, err);
	if (err) {
		return err;
	}

	for (;;) {
		uint32_t baud = 0U;

		if (device_is_ready(cdc_console) &&
		    uart_line_ctrl_get(cdc_console, UART_LINE_CTRL_BAUD_RATE,
				       &baud) == 0 &&
		    baud == DFU_TOUCH_BAUD) {
			enter_dfu_bootloader();
		}
		k_sleep(K_MSEC(100));
	}

	return 0;
}
