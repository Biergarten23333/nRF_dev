#include <zephyr/kernel.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/logging/log.h>
#include <string.h>

LOG_MODULE_REGISTER(ble_scanner, LOG_LEVEL_INF);

static void scan_cb(const bt_addr_le_t *addr, int8_t rssi,
		    uint8_t adv_type, struct net_buf_simple *buf)
{
	char addr_str[BT_ADDR_LE_STR_LEN];
	char name[30] = "(unknown)";

	bt_addr_le_to_str(addr, addr_str, sizeof(addr_str));

	/* Try to extract the device name from advertisement data */
	uint8_t data_len = buf->len;
	const uint8_t *data = buf->data;

	while (data_len > 1) {
		uint8_t len = data[0];

		if (len == 0 || len > data_len - 1) {
			break;
		}

		uint8_t type = data[1];

		if ((type == BT_DATA_NAME_COMPLETE ||
		     type == BT_DATA_NAME_SHORTENED) && len > 1) {
			uint8_t name_len = len - 1;

			if (name_len > sizeof(name) - 1) {
				name_len = sizeof(name) - 1;
			}
			memcpy(name, &data[2], name_len);
			name[name_len] = '\0';
			break;
		}

		data_len -= len + 1;
		data += len + 1;
	}

	LOG_INF("[DEVICE] %s  RSSI: %d  Name: %s", addr_str, rssi, name);
}

int main(void)
{
	int err;

	LOG_INF("nRF54L15 BLE Scanner starting...");

	err = bt_enable(NULL);
	if (err) {
		LOG_ERR("Bluetooth init failed (err %d)", err);
		return 0;
	}

	LOG_INF("Bluetooth initialized");

	struct bt_le_scan_param scan_param = {
		.type     = BT_LE_SCAN_TYPE_PASSIVE,
		.options  = BT_LE_SCAN_OPT_FILTER_DUPLICATE,
		.interval = BT_GAP_SCAN_FAST_INTERVAL,
		.window   = BT_GAP_SCAN_FAST_WINDOW,
	};

	err = bt_le_scan_start(&scan_param, scan_cb);
	if (err) {
		LOG_ERR("Scanning failed to start (err %d)", err);
		return 0;
	}

	LOG_INF("Scanning started... Nearby BLE devices will appear below:");

	return 0;
}
