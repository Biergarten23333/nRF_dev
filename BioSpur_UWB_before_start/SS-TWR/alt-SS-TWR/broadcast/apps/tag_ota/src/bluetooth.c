/*
 * Minimal BLE SMP server for OTA testing.
 */

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/kernel.h>
#include <zephyr/mgmt/mcumgr/transport/smp_bt.h>
#include <zephyr/sys/printk.h>

#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(tag_ota_ble, LOG_LEVEL_INF);

static const struct bt_data ad[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
	BT_DATA_BYTES(BT_DATA_UUID128_ALL,
		      0x84, 0xaa, 0x60, 0x74, 0x52, 0x8a, 0x8b, 0x86,
		      0xd3, 0x4c, 0xb7, 0x1d, 0x1d, 0xdc, 0x53, 0x8d),
};

static const struct bt_data sd[] = {
	BT_DATA(BT_DATA_NAME_COMPLETE, CONFIG_BT_DEVICE_NAME,
		sizeof(CONFIG_BT_DEVICE_NAME) - 1U),
};

void start_smp_bluetooth_adverts(void)
{
	int rc;

	printk("Tag OTA BLE init start\n");
	rc = bt_enable(NULL);
	printk("Tag OTA bt_enable rc=%d\n", rc);

	if (rc != 0) {
		printk("Bluetooth enable failed: %d\n", rc);
		LOG_ERR("Bluetooth enable failed: %d", rc);
		return;
	}

	printk("Tag OTA bt_enable ok\n");

	printk("Tag OTA advertise start\n");
	rc = bt_le_adv_start(BT_LE_ADV_CONN_NAME, ad, ARRAY_SIZE(ad), NULL, 0);
	if (rc == -EALREADY) {
		printk("Tag OTA advertise already active\n");
		return;
	}
	if (rc) {
		printk("Tag OTA advertise failed: %d\n", rc);
		LOG_ERR("Advertising failed to start (rc %d)", rc);
		return;
	}

	printk("Tag OTA advertise success\n");
	LOG_INF("Advertising started as %s", CONFIG_BT_DEVICE_NAME);
}
