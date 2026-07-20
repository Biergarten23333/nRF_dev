#include <errno.h>
#include <stdio.h>

#include <hal/nrf_ficr.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/device.h>
#include <zephyr/dfu/mcuboot.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include "biospur_link.h"

LOG_MODULE_REGISTER(biospur_fusion, LOG_LEVEL_INF);

#define LED0_NODE DT_ALIAS(led0)
#define FW_MARKER "b306-fast-ota-v4"

/*
 * The leading 0xffff is the reserved company ID used for internal testing;
 * the remaining bytes identify the fast-OTA-capable B306 image.
 */
static const uint8_t firmware_advertising_marker[] = {
	0xff, 0xff, 'B', '3', '0', '6', 'F', '1',
};

#if !DT_NODE_HAS_STATUS(LED0_NODE, okay)
#error "The first B306 image requires the board's led0 alias"
#endif

static const struct gpio_dt_spec status_led =
	GPIO_DT_SPEC_GET(LED0_NODE, gpios);
static char device_name[8];

static const struct bt_data advertising_data[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR),
	/* Zephyr SMP service UUID 8D53DC1D-1DB7-4CD3-868B-8A527460AA84. */
	BT_DATA_BYTES(BT_DATA_UUID128_ALL,
		      0x84, 0xaa, 0x60, 0x74, 0x52, 0x8a, 0x8b, 0x86,
		      0xd3, 0x4c, 0xb7, 0x1d, 0x1d, 0xdc, 0x53, 0x8d),
};

static int start_advertising(void)
{
	const struct bt_data scan_response[] = {
		BT_DATA(BT_DATA_NAME_COMPLETE, device_name,
			sizeof(device_name) - 1),
		BT_DATA(BT_DATA_MANUFACTURER_DATA, firmware_advertising_marker,
			sizeof(firmware_advertising_marker)),
	};

	return bt_le_adv_start(BT_LE_ADV_CONN,
			       advertising_data, ARRAY_SIZE(advertising_data),
			       scan_response, ARRAY_SIZE(scan_response));
}

int main(void)
{
	uint32_t deviceid0 = NRF_FICR->DEVICEID[0];
	uint32_t deviceid1 = NRF_FICR->DEVICEID[1];
	uint16_t identity = bsl_identity_from_ficr(deviceid0, deviceid1);
	int ret;

	if (!gpio_is_ready_dt(&status_led)) {
		LOG_ERR("status LED GPIO is not ready");
		return 0;
	}

	ret = gpio_pin_configure_dt(&status_led, GPIO_OUTPUT_INACTIVE);
	if (ret != 0) {
		LOG_ERR("status LED configuration failed: %d", ret);
		return 0;
	}

	snprintf(device_name, sizeof(device_name), "BSF%04X", identity);
	ret = bt_set_name(device_name);
	if (ret != 0) {
		LOG_ERR("BLE name setup failed: %d", ret);
		return 0;
	}

	LOG_INF("firmware=%s identity=0x%04X name=%s",
		FW_MARKER, identity, device_name);

	ret = bt_enable(NULL);
	if (ret != 0) {
		LOG_ERR("Bluetooth initialization failed: %d", ret);
		return 0;
	}

	ret = start_advertising();
	if (ret != 0) {
		LOG_ERR("BLE advertising failed: %d", ret);
		return 0;
	}

	LOG_INF("BLE SMP advertising started as %s", device_name);

	/*
	 * Match the established UWB OTA contract: a test image confirms itself.
	 * B306 delays confirmation until its LF clock, BLE stack, identity, SMP
	 * service, and connectable advertising have all started successfully.
	 * A failure before this point leaves the image unconfirmed so MCUboot can
	 * revert it on the next reset.
	 */
	if (!boot_is_img_confirmed()) {
		ret = boot_write_img_confirmed();
		if (ret != 0) {
			LOG_ERR("MCUboot image confirmation failed: %d", ret);
		} else {
			LOG_INF("MCUboot image confirmed after BLE health check");
		}
	}

	while (true) {
		ret = gpio_pin_toggle_dt(&status_led);
		if (ret != 0) {
			LOG_ERR("status LED toggle failed: %d", ret);
			return 0;
		}
		k_sleep(K_MSEC(500));
	}

	return 0;
}
