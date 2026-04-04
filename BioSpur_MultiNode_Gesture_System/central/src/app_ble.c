#include <stdio.h>
#include <string.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>

#include "app_ble.h"
#include "bsgr_protocol.h"
#include "cdc_async.h"
#include "tsync_master.h"

LOG_MODULE_REGISTER(central_app_ble, LOG_LEVEL_INF);

#define BSGR_CENTRAL_MAX_PEERS 4
#define BSGR_SCAN_INTERVAL 0x0060
#define BSGR_SCAN_WINDOW 0x0030

static struct bsgr_central_peer peers[BSGR_CENTRAL_MAX_PEERS];
static bool scan_running;

static struct bsgr_central_peer *peer_lookup_by_nus(struct bt_nus_client *nus)
{
	size_t i;

	for (i = 0; i < ARRAY_SIZE(peers); ++i) {
		if (&peers[i].nus_client == nus) {
			return &peers[i];
		}
	}

	return NULL;
}

static struct bsgr_central_peer *peer_alloc_from_addr(const bt_addr_le_t *addr)
{
	size_t i;

	for (i = 0; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].in_use &&
		    (bt_addr_le_cmp(&peers[i].addr, addr) == 0)) {
			return &peers[i];
		}
	}

	for (i = 0; i < ARRAY_SIZE(peers); ++i) {
		if (!peers[i].in_use) {
			memset(&peers[i], 0, sizeof(peers[i]));
			peers[i].in_use = true;
			bt_addr_le_copy(&peers[i].addr, addr);
			return &peers[i];
		}
	}

	return NULL;
}

static void peer_release(struct bt_conn *conn)
{
	size_t i;

	for (i = 0; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].conn == conn) {
			if (peers[i].conn != NULL) {
				bt_conn_unref(peers[i].conn);
			}
			memset(&peers[i], 0, sizeof(peers[i]));
			return;
		}
	}
}

static void forward_frame_summary(const struct bsgr_frame_header *hdr)
{
	char line[96];
	int len;

	len = snprintf(line, sizeof(line), "dev=%u type=0x%02x seq=%u len=%u\r\n",
		       hdr->device_id, hdr->frame_type, hdr->seq, hdr->payload_len);
	if (len > 0) {
		(void)cdc_async_write_data((const uint8_t *)line, MIN((size_t)len, sizeof(line)));
	}
}

static uint8_t nus_recv_cb(struct bt_nus_client *nus, const uint8_t *data, uint16_t len)
{
	const struct bsgr_frame_header *hdr;
	struct bsgr_central_peer *peer = peer_lookup_by_nus(nus);

	if ((peer == NULL) || (len < sizeof(*hdr))) {
		return BT_GATT_ITER_CONTINUE;
	}

	hdr = (const struct bsgr_frame_header *)data;
	if (!bsgr_frame_header_is_valid(hdr)) {
		return BT_GATT_ITER_CONTINUE;
	}

	peer->identified = true;
	peer->device_id = hdr->device_id;
	peer->last_seq = hdr->seq;
	peer->last_seen_ticks = k_uptime_ticks();
	forward_frame_summary(hdr);

	return BT_GATT_ITER_CONTINUE;
}

static void nus_sent_cb(struct bt_nus_client *nus, uint8_t err,
			const uint8_t *data, uint16_t len)
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

static void connected(struct bt_conn *conn, uint8_t err)
{
	const bt_addr_le_t *dst = bt_conn_get_dst(conn);
	struct bsgr_central_peer *peer;

	if ((err != 0U) || (dst == NULL)) {
		return;
	}

	peer = peer_alloc_from_addr(dst);
	if (peer == NULL) {
		LOG_WRN("No free peer slots");
		return;
	}

	peer->conn = bt_conn_ref(conn);
	peer->last_seen_ticks = k_uptime_ticks();
	LOG_INF("Connected peer slot armed");
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	LOG_INF("Peer disconnected: 0x%02x", reason);
	peer_release(conn);
}

BT_CONN_CB_DEFINE(central_conn_callbacks) = {
	.connected = connected,
	.disconnected = disconnected,
};

static bool ad_name_has_bsgr_prefix(struct net_buf_simple *ad)
{
	if ((ad == NULL) || (ad->len == 0U)) {
		return false;
	}

	return (ad->data[0] == 'B') && (ad->len >= 4U);
}

static bool scan_data_cb(struct bt_data *data, void *user_data)
{
	bool *matched = user_data;

	if ((data->type == BT_DATA_NAME_COMPLETE) || (data->type == BT_DATA_NAME_SHORTENED)) {
		struct net_buf_simple ad = {
			.data = data->data,
			.len = data->data_len,
			.size = data->data_len,
		};

		if (ad_name_has_bsgr_prefix(&ad)) {
			*matched = true;
			return false;
		}
	}

	return true;
}

static void device_found(const bt_addr_le_t *addr, int8_t rssi, uint8_t type,
			 struct net_buf_simple *ad)
{
	bool matched = false;

	ARG_UNUSED(addr);
	ARG_UNUSED(type);

	bt_data_parse(ad, scan_data_cb, &matched);
	if (matched) {
		LOG_INF("Discovered BSGR candidate RSSI %d", rssi);
	}
}

int app_ble_init(void)
{
	int err;
	size_t i;

	err = bt_enable(NULL);
	if (err != 0) {
		return err;
	}

	if (IS_ENABLED(CONFIG_SETTINGS)) {
		settings_load();
	}

	memset(peers, 0, sizeof(peers));
	for (i = 0; i < ARRAY_SIZE(peers); ++i) {
		err = bt_nus_client_init(&peers[i].nus_client, &nus_init_param);
		if (err != 0) {
			return err;
		}
	}

	LOG_INF("BSGR Central BLE framework initialized");
	return 0;
}

int app_ble_start_scan(void)
{
	struct bt_le_scan_param scan_param = {
		.type = BT_LE_SCAN_TYPE_ACTIVE,
		.options = BT_LE_SCAN_OPT_NONE,
		.interval = BSGR_SCAN_INTERVAL,
		.window = BSGR_SCAN_WINDOW,
	};
	int err;

	err = bt_le_scan_start(&scan_param, device_found);
	if ((err == -EALREADY) || (err == 0)) {
		scan_running = true;
		return 0;
	}

	return err;
}

int app_ble_stop_scan(void)
{
	int err;

	err = bt_le_scan_stop();
	if ((err == -EALREADY) || (err == 0)) {
		scan_running = false;
		return 0;
	}

	return err;
}

void app_ble_process(void)
{
	size_t i;

	for (i = 0; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].identified) {
			peers[i].rssi = 0;
		}
	}

	if (!scan_running) {
		(void)app_ble_start_scan();
	}
}

const struct bsgr_central_peer *app_ble_peers_get(size_t *count)
{
	if (count != NULL) {
		*count = ARRAY_SIZE(peers);
	}

	return peers;
}
