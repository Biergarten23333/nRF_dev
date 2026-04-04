#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>

#include <bluetooth/gatt_dm.h>
#include <bluetooth/services/nus_client.h>

#include "app_ble.h"

LOG_MODULE_REGISTER(central_app_ble, LOG_LEVEL_INF);

#define BSGR_CENTRAL_MAX_PEERS 4

static struct bt_nus_client nus_clients[BSGR_CENTRAL_MAX_PEERS];

static uint8_t nus_recv_cb(struct bt_nus_client *nus,
			     const uint8_t *data,
			     uint16_t len)
{
	ARG_UNUSED(nus);
	ARG_UNUSED(data);

	LOG_INF("NUS client RX stub: %u bytes", len);
	return BT_GATT_ITER_CONTINUE;
}

static void nus_sent_cb(struct bt_nus_client *nus,
			 uint8_t err,
			 const uint8_t *data,
			 uint16_t len)
{
	ARG_UNUSED(nus);
	ARG_UNUSED(err);
	ARG_UNUSED(data);
	ARG_UNUSED(len);
}

static const struct bt_nus_client_init_param nus_init_param = {
	.cb = {
		.received = nus_recv_cb,
		.sent = nus_sent_cb,
	},
};

int app_ble_init(void)
{
	int err;
	size_t i;

	err = bt_enable(NULL);
	if (err) {
		return err;
	}

	if (IS_ENABLED(CONFIG_SETTINGS)) {
		settings_load();
	}

	for (i = 0; i < ARRAY_SIZE(nus_clients); ++i) {
		err = bt_nus_client_init(&nus_clients[i], &nus_init_param);
		if (err) {
			return err;
		}
	}

	LOG_INF("BSGR Central BLE initialized");
	return 0;
}

int app_ble_start_scan(void)
{
	LOG_INF("BLE scan start stub");
	return 0;
}

int app_ble_stop_scan(void)
{
	LOG_INF("BLE scan stop stub");
	return 0;
}
