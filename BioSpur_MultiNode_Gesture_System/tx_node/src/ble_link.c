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

LOG_MODULE_REGISTER(tx_ble_link, LOG_LEVEL_INF);

#define BSGR_TX_FRAME_QUEUE_DEPTH 12

static struct bt_conn *current_conn;
static struct k_msgq tx_frame_msgq;
static struct bsgr_tx_frame tx_frame_buffer[BSGR_TX_FRAME_QUEUE_DEPTH];
static struct k_work tx_drain_work;

static const struct bt_data ad[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
};

static const struct bt_data sd[] = {
	BT_DATA(BT_DATA_NAME_COMPLETE, CONFIG_BT_DEVICE_NAME, sizeof(CONFIG_BT_DEVICE_NAME) - 1),
};

static int start_advertising(void)
{
	int err;

	err = bt_le_adv_start(BT_LE_ADV_CONN, ad, ARRAY_SIZE(ad), sd, ARRAY_SIZE(sd));
	if ((err == -EALREADY) || (err == -EAGAIN)) {
		return 0;
	}

	return err;
}

static void tx_drain_work_handler(struct k_work *work)
{
	struct bsgr_tx_frame frame;
	int err;

	ARG_UNUSED(work);

	if (current_conn == NULL) {
		return;
	}

	while (k_msgq_get(&tx_frame_msgq, &frame, K_NO_WAIT) == 0) {
		err = bt_nus_send(current_conn, frame.data, frame.len);
		if (err != 0) {
			LOG_WRN("bt_nus_send failed: %d", err);
			break;
		}
	}
}

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
	k_work_submit(&tx_drain_work);
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	int err;

	ARG_UNUSED(conn);

	if (current_conn != NULL) {
		bt_conn_unref(current_conn);
		current_conn = NULL;
	}

	LOG_INF("BLE disconnected: 0x%02x", reason);

	err = start_advertising();
	if (err != 0) {
		LOG_ERR("Failed to restart advertising: %d", err);
	}
}

BT_CONN_CB_DEFINE(tx_conn_callbacks) = {
	.connected = connected,
	.disconnected = disconnected,
};

static void nus_rx_cb(struct bt_conn *conn, const uint8_t *const data, uint16_t len)
{
	ARG_UNUSED(conn);

	LOG_INF("NUS RX control stub: %u bytes", len);
	if (len >= sizeof(struct bsgr_frame_header)) {
		const struct bsgr_frame_header *hdr = (const struct bsgr_frame_header *)data;

		if (bsgr_frame_header_is_valid(hdr)) {
			LOG_INF("Received frame type 0x%02x seq %u", hdr->frame_type, hdr->seq);
		}
	}
}

static struct bt_nus_cb nus_callbacks = {
	.received = nus_rx_cb,
};

int ble_link_init(uint16_t device_id)
{
	int err;

	ARG_UNUSED(device_id);

	k_msgq_init(&tx_frame_msgq, (char *)tx_frame_buffer,
		    sizeof(struct bsgr_tx_frame), ARRAY_SIZE(tx_frame_buffer));
	k_work_init(&tx_drain_work, tx_drain_work_handler);

	err = bt_enable(NULL);
	if (err != 0) {
		return err;
	}

	if (IS_ENABLED(CONFIG_SETTINGS)) {
		settings_load();
	}

	err = bt_nus_init(&nus_callbacks);
	if (err != 0) {
		return err;
	}

	err = start_advertising();
	if (err != 0) {
		return err;
	}

	LOG_INF("BSGR TX advertising with NUS and separate SMP GATT service");
	return 0;
}

int ble_link_submit_frame(const struct bsgr_tx_frame *frame)
{
	int err;

	if (frame == NULL) {
		return -EINVAL;
	}

	err = k_msgq_put(&tx_frame_msgq, frame, K_NO_WAIT);
	if (err == 0) {
		k_work_submit(&tx_drain_work);
	}

	return err;
}

void ble_link_schedule_drain(void)
{
	k_work_submit(&tx_drain_work);
}

bool ble_link_is_connected(void)
{
	return current_conn != NULL;
}
