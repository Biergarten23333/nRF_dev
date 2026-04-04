#include <errno.h>
#include <string.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/util.h>

#include <bluetooth/services/nus.h>

#include "ble_link.h"
#include "packet_framer.h"

LOG_MODULE_REGISTER(tx_ble_link, LOG_LEVEL_INF);

static struct bt_conn *current_conn;
static uint16_t tx_device_id;
static uint16_t tx_seq;

static const struct bt_data ad[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
};

static const struct bt_data sd[] = {
	BT_DATA(BT_DATA_NAME_COMPLETE, CONFIG_BT_DEVICE_NAME, sizeof(CONFIG_BT_DEVICE_NAME) - 1),
};

static void connected(struct bt_conn *conn, uint8_t err)
{
	if (err != 0U) {
		LOG_WRN("Connection failed: 0x%02x", err);
		return;
	}

	if (current_conn != NULL) {
		bt_conn_unref(current_conn);
	}

	current_conn = bt_conn_ref(conn);
	LOG_INF("BLE connected");
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	ARG_UNUSED(conn);

	if (current_conn != NULL) {
		bt_conn_unref(current_conn);
		current_conn = NULL;
	}

	LOG_INF("BLE disconnected: 0x%02x", reason);
}

BT_CONN_CB_DEFINE(tx_conn_callbacks) = {
	.connected = connected,
	.disconnected = disconnected,
};

static void nus_rx_cb(struct bt_conn *conn, const uint8_t *const data, uint16_t len)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(data);

	LOG_INF("NUS RX stub: %u bytes", len);
}

static struct bt_nus_cb nus_callbacks = {
	.received = nus_rx_cb,
};

int ble_link_init(uint16_t device_id)
{
	int err;

	tx_device_id = device_id;
	tx_seq = 0U;

	err = bt_enable(NULL);
	if (err) {
		return err;
	}

	if (IS_ENABLED(CONFIG_SETTINGS)) {
		settings_load();
	}

	err = bt_nus_init(&nus_callbacks);
	if (err) {
		return err;
	}

	err = bt_le_adv_start(BT_LE_ADV_CONN_ONE_TIME, ad, ARRAY_SIZE(ad), sd, ARRAY_SIZE(sd));
	if (err) {
		return err;
	}

	LOG_INF("BSGR TX advertising with NUS and independent SMP GATT service");
	return 0;
}

int ble_link_send_status(const uint8_t *payload, uint8_t payload_len)
{
	uint8_t frame[sizeof(struct bsgr_frame_header) + 1U + BSGR_MAX_FRAME_PAYLOAD_LEN];
	uint8_t frame_len = 0U;
	int err;

	err = packet_framer_build_control(tx_device_id, tx_seq++, BSGR_CTRL_NOP, payload,
					  payload_len, frame, &frame_len);
	if (err) {
		return err;
	}

	if (current_conn == NULL) {
		return -ENOTCONN;
	}

	return bt_nus_send(current_conn, frame, frame_len);
}
