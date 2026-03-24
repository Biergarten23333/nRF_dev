#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#include <dk_buttons_and_leds.h>

#include <bluetooth/gatt_dm.h>
#include <bluetooth/services/nus.h>
#include <bluetooth/services/nus_client.h>

#define MASTER_MAX_CONNECTIONS 5U
#define TARGET_TAG_NAME_PREFIX "Tag_rot"
#define MASTER_NAME_BUF_LEN 32U
#define BLE_SAMPLE_MAGIC0 0x42U
#define BLE_SAMPLE_MAGIC1 0x50U
#define BLE_SAMPLE_VERSION 1U
#define BLE_SAMPLE_HEADER_LEN 5U
#define BLE_SAMPLE_RECORD_LEN 24U

#ifndef APP_MASTER_ONE_SHOT_CMD
#define APP_MASTER_ONE_SHOT_CMD ""
#endif

static const struct bt_conn_le_phy_param *const fast_phy_params = BT_CONN_LE_PHY_PARAM_2M;
static const struct bt_le_conn_param fast_conn_params = {
	.interval_min = 6,
	.interval_max = 6,
	.latency = 0,
	.timeout = 400,
};

enum master_led_id {
	MASTER_LED_SCAN = DK_LED1,
	MASTER_LED_LINK = DK_LED2,
	MASTER_LED_OTA = DK_LED3,
	MASTER_LED_ERROR = DK_LED4,
};

struct master_peer {
	struct bt_conn *conn;
	struct bt_nus_client nus_client;
	struct bt_gatt_exchange_params mtu_exchange_params;
	bool connected;
	bool ready;
	bool one_shot_sent;
	bool ota_ready;
	bool ota_active;
	bt_addr_le_t addr;
	bool addr_valid;
	char adv_name[MASTER_NAME_BUF_LEN];
	uint8_t tag_id;
	bool tag_id_valid;
};

static struct master_peer peers[MASTER_MAX_CONNECTIONS];
static int connecting_slot = -1;
static uint8_t conn_count;
static bool scan_running;
static bool leds_ready;
static bool led_scan_state;
static bool led_link_state;
static bool led_ota_state;
static bool led_error_state;

static void master_leds_apply(void)
{
	if (!leds_ready) {
		return;
	}

	(void)dk_set_led(MASTER_LED_SCAN, led_scan_state);
	(void)dk_set_led(MASTER_LED_LINK, led_link_state);
	(void)dk_set_led(MASTER_LED_OTA, led_ota_state);
	(void)dk_set_led(MASTER_LED_ERROR, led_error_state);
}

static void master_leds_refresh(void)
{
	bool ota = false;

	led_scan_state = scan_running;
	led_link_state = (conn_count > 0U);
	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].connected && (peers[i].ota_ready || peers[i].ota_active)) {
			ota = true;
		}
	}
	led_ota_state = ota;
	master_leds_apply();
}

static int peer_index_from_conn(struct bt_conn *conn)
{
	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].conn == conn) {
			return (int)i;
		}
	}

	return -1;
}

static int peer_index_from_addr(const bt_addr_le_t *addr)
{
	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (!peers[i].addr_valid) {
			continue;
		}

		if (bt_addr_le_cmp(addr, &peers[i].addr) == 0) {
			return (int)i;
		}
	}

	return -1;
}

static int peer_index_from_nus(struct bt_nus_client *nus)
{
	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (&peers[i].nus_client == nus) {
			return (int)i;
		}
	}

	return -1;
}

static int peer_index_from_tag_id(uint8_t tag_id)
{
	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (!peers[i].tag_id_valid) {
			continue;
		}

		if (peers[i].tag_id == tag_id) {
			return (int)i;
		}
	}

	return -1;
}

static void peer_clear(unsigned int idx, bool unref_conn)
{
	if (idx >= ARRAY_SIZE(peers)) {
		return;
	}

	if (unref_conn && peers[idx].conn != NULL) {
		bt_conn_unref(peers[idx].conn);
	}

	peers[idx].conn = NULL;
	peers[idx].connected = false;
	peers[idx].ready = false;
	peers[idx].one_shot_sent = false;
	peers[idx].ota_ready = false;
	peers[idx].ota_active = false;
	peers[idx].addr_valid = false;
	peers[idx].adv_name[0] = '\0';
	peers[idx].tag_id = 0U;
	peers[idx].tag_id_valid = false;
}

static bool ble_payload_contains(const uint8_t *data, uint16_t len, const char *needle)
{
	size_t needle_len;

	if (data == NULL || needle == NULL) {
		return false;
	}

	needle_len = strlen(needle);
	if (needle_len == 0U || needle_len > len) {
		return false;
	}

	for (uint16_t i = 0U; i + needle_len <= len; ++i) {
		if (memcmp(&data[i], needle, needle_len) == 0) {
			return true;
		}
	}

	return false;
}

static bool scan_name_cb(struct bt_data *data, void *user_data)
{
	char *name_buf = user_data;
	size_t name_len;

	switch (data->type) {
	case BT_DATA_NAME_SHORTENED:
	case BT_DATA_NAME_COMPLETE:
		name_len = MIN(data->data_len, MASTER_NAME_BUF_LEN - 1U);
		memcpy(name_buf, data->data, name_len);
		name_buf[name_len] = '\0';
		return false;
	default:
		return true;
	}
}

static bool ad_name_matches_target(struct net_buf_simple *ad)
{
	struct net_buf_simple copy = *ad;
	char name[MASTER_NAME_BUF_LEN];
	size_t prefix_len = strlen(TARGET_TAG_NAME_PREFIX);

	memset(name, 0, sizeof(name));
	bt_data_parse(&copy, scan_name_cb, name);

	if (name[0] == '\0') {
		return false;
	}

	return strncmp(name, TARGET_TAG_NAME_PREFIX, prefix_len) == 0;
}

static bool scan_uuid128_cb(struct bt_data *data, void *user_data)
{
	bool *match = user_data;
	static const uint8_t dfu_smp_uuid_le[16] = {
		0x84, 0xaa, 0x60, 0x74, 0x52, 0x8a, 0x8b, 0x86,
		0xd3, 0x4c, 0xb7, 0x1d, 0x1d, 0xdc, 0x53, 0x8d,
	};

	if (*match) {
		return false;
	}

	if (data->type != BT_DATA_UUID128_ALL &&
	    data->type != BT_DATA_UUID128_SOME) {
		return true;
	}

	for (size_t offset = 0U; offset + sizeof(dfu_smp_uuid_le) <= data->data_len;
	     offset += sizeof(dfu_smp_uuid_le)) {
		if (memcmp(&data->data[offset], dfu_smp_uuid_le,
			   sizeof(dfu_smp_uuid_le)) == 0) {
			*match = true;
			return false;
		}
	}

	return true;
}

static bool ad_has_dfu_smp_uuid(struct net_buf_simple *ad)
{
	struct net_buf_simple copy = *ad;
	bool match = false;

	bt_data_parse(&copy, scan_uuid128_cb, &match);
	return match;
}

static bool scan_mfg_token_cb(struct bt_data *data, void *user_data)
{
	bool *match = user_data;

	if (*match) {
		return false;
	}

	if (data->type != BT_DATA_MANUFACTURER_DATA || data->data_len < 4U) {
		return true;
	}

	if (data->data[0] == 0xff &&
	    data->data[1] == 0xff &&
	    data->data[2] == 'B') {
		*match = true;
		return false;
	}

	return true;
}

static bool scan_mfg_tag_id_cb(struct bt_data *data, void *user_data)
{
	uint8_t *tag_id = user_data;

	if (data->type != BT_DATA_MANUFACTURER_DATA || data->data_len < 4U) {
		return true;
	}

	if (data->data[0] == 0xff &&
	    data->data[1] == 0xff &&
	    data->data[2] == 'B') {
		*tag_id = data->data[3];
		return false;
	}

	return true;
}

static bool ad_has_biospur_token(struct net_buf_simple *ad)
{
	struct net_buf_simple copy = *ad;
	bool match = false;

	bt_data_parse(&copy, scan_mfg_token_cb, &match);
	return match;
}

static bool ad_get_biospur_tag_id(struct net_buf_simple *ad, uint8_t *tag_id)
{
	struct net_buf_simple copy = *ad;
	uint8_t parsed = 0xffU;

	bt_data_parse(&copy, scan_mfg_tag_id_cb, &parsed);
	if (parsed == 0xffU) {
		return false;
	}

	*tag_id = parsed;
	return true;
}

static void scan_log_candidate(const struct bt_le_scan_recv_info *info,
			       struct net_buf_simple *buf,
			       bool name_match,
			       bool dfu_match,
			       bool token_match,
			       uint8_t tag_id,
			       bool tag_id_valid)
{
	char addr[BT_ADDR_LE_STR_LEN];
	char name[MASTER_NAME_BUF_LEN];
	struct net_buf_simple copy = *buf;

	bt_addr_le_to_str(info->addr, addr, sizeof(addr));
	memset(name, 0, sizeof(name));
	bt_data_parse(&copy, scan_name_cb, name);

	printk("SCAN hit: %s rssi=%d name=%s name=%u dfu=%u token=%u tag_id=%s%u props=0x%02x\n",
	       addr,
	       info->rssi,
	       name[0] != '\0' ? name : "-",
	       name_match ? 1U : 0U,
	       dfu_match ? 1U : 0U,
	       token_match ? 1U : 0U,
	       tag_id_valid ? "" : "-",
	       tag_id_valid ? tag_id : 0U,
	       info->adv_props);
}

static void start_scan(void)
{
	int err;

	if (scan_running || connecting_slot >= 0 || conn_count >= MASTER_MAX_CONNECTIONS) {
		return;
	}

	err = bt_le_scan_start(BT_LE_SCAN_ACTIVE, NULL);
	if (err) {
		printk("Failed to start scan: %d\n", err);
		return;
	}

	scan_running = true;
	master_leds_refresh();
	printk("Scanning for %s*\n", TARGET_TAG_NAME_PREFIX);
}

static const char *sample_plan_label(uint8_t code)
{
	switch (code) {
	case 0:
		return "track";
	case 1:
		return "full";
	case 2:
		return "fixed";
	default:
		return "unknown";
	}
}

static void sample_anchor_mask_to_text(uint8_t mask, char *out, size_t out_len)
{
	size_t len = 0U;

	if (out == NULL || out_len == 0U) {
		return;
	}

	for (uint8_t i = 0U; i < 8U && len + 1U < out_len; ++i) {
		if ((mask & BIT(i)) == 0U) {
			continue;
		}

		out[len++] = (char)('A' + i);
	}

	out[len] = '\0';
}

static bool ble_decode_sample_packet(const uint8_t *data, uint16_t len,
					    char *payload, size_t payload_len)
{
	uint8_t count;
	size_t offset;
	size_t used = 0U;

	if (data == NULL || payload == NULL || payload_len == 0U ||
	    len < BLE_SAMPLE_HEADER_LEN) {
		return false;
	}

	if (data[0] != BLE_SAMPLE_MAGIC0 || data[1] != BLE_SAMPLE_MAGIC1 ||
	    data[2] != BLE_SAMPLE_VERSION) {
		return false;
	}

	count = data[3];
	offset = BLE_SAMPLE_HEADER_LEN;
	if (count == 0U || len < offset + (size_t)count * BLE_SAMPLE_RECORD_LEN) {
		return false;
	}

	payload[0] = '\0';
	for (uint8_t i = 0U; i < count; ++i) {
		uint32_t sweep = sys_get_le32(&data[offset]);
		uint8_t plan_code = data[offset + 4U];
		uint8_t anchor_mask = data[offset + 5U];
		uint16_t motion_dt = sys_get_le16(&data[offset + 6U]);
		int32_t x = (int32_t)sys_get_le32(&data[offset + 8U]);
		int32_t y = (int32_t)sys_get_le32(&data[offset + 12U]);
		int32_t z = (int32_t)sys_get_le32(&data[offset + 16U]);
		uint16_t rms = sys_get_le16(&data[offset + 20U]);
		uint16_t max = sys_get_le16(&data[offset + 22U]);
		char anchors[16];
		int written;

		sample_anchor_mask_to_text(anchor_mask, anchors, sizeof(anchors));
		written = snprintk(
			&payload[used], payload_len - used,
			"%sTS s=%u p=%s xyz=%d,%d,%d r=%u m=%u a=%s %s",
			(i == 0U) ? "" : "|",
			(unsigned int)sweep,
			sample_plan_label(plan_code),
			(int)x, (int)y, (int)z,
			(unsigned int)rms,
			(unsigned int)max,
			anchors,
			(motion_dt != 0U) ? "" : "motion=na");
		if (written < 0 || (size_t)written >= payload_len - used) {
			return false;
		}
		used += (size_t)written;
		if (motion_dt != 0U) {
			written = snprintk(&payload[used], payload_len - used,
					   " d=%u", (unsigned int)motion_dt);
			if (written < 0 || (size_t)written >= payload_len - used) {
				return false;
			}
			used += (size_t)written;
		}

		offset += BLE_SAMPLE_RECORD_LEN;
	}

	return true;
}

static void stop_scan(void)
{
	if (!scan_running) {
		return;
	}

	if (bt_le_scan_stop() == 0) {
		scan_running = false;
		master_leds_refresh();
	}
}

static void exchange_func(struct bt_conn *conn, uint8_t err,
			  struct bt_gatt_exchange_params *params)
{
	ARG_UNUSED(params);

	printk("MTU exchange[%d] %s (%u)\n", peer_index_from_conn(conn),
	       err == 0U ? "done" : "failed", bt_gatt_get_mtu(conn));
}

static uint8_t ble_data_received(struct bt_nus_client *nus,
				 const uint8_t *data, uint16_t len)
{
	int idx = peer_index_from_nus(nus);
	char payload[256];
	size_t copy_len;

	if (!ble_decode_sample_packet(data, len, payload, sizeof(payload))) {
		copy_len = MIN((size_t)len, sizeof(payload) - 1U);
		for (size_t i = 0; i < copy_len; ++i) {
			char c = (char)data[i];
			payload[i] = (c >= 32 && c <= 126) ? c : '.';
		}
		payload[copy_len] = '\0';
	}

	printk("BLE[%d:%s:%s%u] notify: %s\n",
	       idx,
	       (idx >= 0 && peers[idx].adv_name[0] != '\0') ? peers[idx].adv_name : "-",
	       (idx >= 0 && peers[idx].tag_id_valid) ? "" : "-",
	       (idx >= 0 && peers[idx].tag_id_valid) ? peers[idx].tag_id : 0U,
	       payload);

	if (ble_payload_contains(data, len, "OTA_STATE=READY") ||
	    ble_payload_contains(data, len, "OTA_READY") ||
	    ble_payload_contains(data, len, "OTA_BEGIN_OK")) {
		peers[idx].ota_ready = true;
	}
	if (ble_payload_contains(data, len, "OTA_STATE=ACTIVE")) {
		peers[idx].ota_active = true;
	}
	if (ble_payload_contains(data, len, "OTA_STATE=NORMAL") ||
	    ble_payload_contains(data, len, "OTA_CANCELLED")) {
		peers[idx].ota_ready = false;
		peers[idx].ota_active = false;
	}
	if (ble_payload_contains(data, len, "UNKNOWN_CMD") ||
	    ble_payload_contains(data, len, "FAILED") ||
	    ble_payload_contains(data, len, "ERROR")) {
		led_error_state = true;
	}

	master_leds_refresh();

	return BT_GATT_ITER_CONTINUE;
}

static void ble_data_sent(struct bt_nus_client *nus, uint8_t err,
			  const uint8_t *const data, uint16_t len)
{
	ARG_UNUSED(data);
	ARG_UNUSED(len);

	if (err) {
		printk("BLE write error[%d]: 0x%02x\n", peer_index_from_nus(nus), err);
		led_error_state = true;
		master_leds_refresh();
	}
}

static int nus_client_init(void)
{
	struct bt_nus_client_init_param init = {
		.cb = {
			.received = ble_data_received,
			.sent = ble_data_sent,
		},
	};

	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		int err = bt_nus_client_init(&peers[i].nus_client, &init);

		if (err) {
			printk("NUS client init failed[%zu]: %d\n", i, err);
			return err;
		}
	}

	return 0;
}

static void discovery_complete(struct bt_gatt_dm *dm, void *context)
{
	struct master_peer *peer = context;
	int idx = (int)(peer - peers);
	int err;

	err = bt_nus_handles_assign(dm, &peer->nus_client);
	if (err) {
		printk("NUS handle assign failed[%d]: %d\n", idx, err);
		bt_gatt_dm_data_release(dm);
		return;
	}

	err = bt_nus_subscribe_receive(&peer->nus_client);
	if (err) {
		printk("NUS subscribe failed[%d]: %d\n", idx, err);
		bt_gatt_dm_data_release(dm);
		return;
	}

	peer->ready = true;
	printk("BLE[%d] link ready\n", idx);
	master_leds_refresh();

	if (strlen(APP_MASTER_ONE_SHOT_CMD) != 0U && !peer->one_shot_sent) {
		err = bt_nus_client_send(&peer->nus_client,
					 (const uint8_t *)APP_MASTER_ONE_SHOT_CMD,
					 strlen(APP_MASTER_ONE_SHOT_CMD));
		if (err) {
			printk("BLE one-shot send failed[%d]: %d\n", idx, err);
			led_error_state = true;
			master_leds_refresh();
		} else {
			peer->one_shot_sent = true;
			printk("BLE one-shot command sent[%d]: %s\n", idx, APP_MASTER_ONE_SHOT_CMD);
		}
	}

	bt_gatt_dm_data_release(dm);
}

static void discovery_service_not_found(struct bt_conn *conn, void *context)
{
	ARG_UNUSED(context);
	printk("NUS service not found[%d]\n", peer_index_from_conn(conn));
}

static void discovery_error(struct bt_conn *conn, int err, void *context)
{
	ARG_UNUSED(context);
	printk("GATT discovery error[%d]: %d\n", peer_index_from_conn(conn), err);
}

static struct bt_gatt_dm_cb discovery_cb = {
	.completed = discovery_complete,
	.service_not_found = discovery_service_not_found,
	.error_found = discovery_error,
};

static void gatt_discover(struct bt_conn *conn, struct master_peer *peer)
{
	int err = bt_gatt_dm_start(conn, BT_UUID_NUS_SERVICE, &discovery_cb, peer);

	if (err) {
		printk("Could not start GATT discovery[%d]: %d\n", peer_index_from_conn(conn), err);
	}
}

static void scan_recv(const struct bt_le_scan_recv_info *info, struct net_buf_simple *buf)
{
	int err;
	int slot;
	bool name_match;
	bool dfu_match;
	bool token_match;
	uint8_t tag_id = 0U;
	bool tag_id_valid;
	struct net_buf_simple name_copy = *buf;
	char adv_name[MASTER_NAME_BUF_LEN];

	if (!(info->adv_props & BT_GAP_ADV_PROP_CONNECTABLE)) {
		return;
	}

	name_match = ad_name_matches_target(buf);
	dfu_match = ad_has_dfu_smp_uuid(buf);
	token_match = ad_has_biospur_token(buf);
	tag_id_valid = ad_get_biospur_tag_id(buf, &tag_id);
	memset(adv_name, 0, sizeof(adv_name));
	bt_data_parse(&name_copy, scan_name_cb, adv_name);

	if (!name_match && !dfu_match && !token_match) {
		return;
	}

	scan_log_candidate(info, buf, name_match, dfu_match, token_match,
			  tag_id, tag_id_valid);

	if (peer_index_from_addr(info->addr) >= 0) {
		return;
	}

	if (tag_id_valid && peer_index_from_tag_id(tag_id) >= 0) {
		return;
	}

	if (connecting_slot >= 0 || conn_count >= MASTER_MAX_CONNECTIONS) {
		return;
	}

	slot = -1;
	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].conn == NULL && !peers[i].addr_valid) {
			slot = (int)i;
			break;
		}
	}

	if (slot < 0) {
		return;
	}

	bt_addr_le_copy(&peers[slot].addr, info->addr);
	peers[slot].addr_valid = true;
	if (adv_name[0] != '\0') {
		strncpy(peers[slot].adv_name, adv_name, sizeof(peers[slot].adv_name) - 1U);
		peers[slot].adv_name[sizeof(peers[slot].adv_name) - 1U] = '\0';
	}
	peers[slot].tag_id = tag_id;
	peers[slot].tag_id_valid = tag_id_valid;
	connecting_slot = slot;

	stop_scan();

	err = bt_conn_le_create(info->addr, BT_CONN_LE_CREATE_CONN,
				&fast_conn_params, &peers[slot].conn);
	if (err) {
		printk("Create conn failed[%d]: %d\n", slot, err);
		peer_clear((unsigned int)slot, false);
		connecting_slot = -1;
		start_scan();
	}
}

static struct bt_le_scan_cb scan_callbacks = {
	.recv = scan_recv,
};

static void connected(struct bt_conn *conn, uint8_t conn_err)
{
	char addr[BT_ADDR_LE_STR_LEN];
	int idx = connecting_slot;
	int err;

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));

	if (conn_err) {
		printk("Failed to connect[%d] to %s, err 0x%02x\n", idx, addr, conn_err);
		if (idx >= 0 && idx < (int)ARRAY_SIZE(peers)) {
			peer_clear((unsigned int)idx, true);
		}
		connecting_slot = -1;
		start_scan();
		return;
	}

	if (idx < 0 || idx >= (int)ARRAY_SIZE(peers)) {
		printk("Connected to %s but no slot was reserved\n", addr);
		return;
	}

	printk("Connected[%d]: %s name=%s tag_id=%s%u\n",
	       idx,
	       addr,
	       peers[idx].adv_name[0] != '\0' ? peers[idx].adv_name : "-",
	       peers[idx].tag_id_valid ? "" : "-",
	       peers[idx].tag_id_valid ? peers[idx].tag_id : 0U);
	conn_count++;
	connecting_slot = -1;
	peers[idx].connected = true;
	master_leds_refresh();

	peers[idx].mtu_exchange_params.func = exchange_func;
	err = bt_gatt_exchange_mtu(conn, &peers[idx].mtu_exchange_params);
	if (err) {
		printk("MTU exchange request[%d] failed: %d\n", idx, err);
	}

	err = bt_conn_le_phy_update(conn, fast_phy_params);
	printk("PHY update request[%d] rc=%d\n", idx, err);
	err = bt_conn_le_param_update(conn, &fast_conn_params);
	printk("Conn param update request[%d] rc=%d\n", idx, err);

	gatt_discover(conn, &peers[idx]);

	if (conn_count < MASTER_MAX_CONNECTIONS) {
		start_scan();
	}
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	char addr[BT_ADDR_LE_STR_LEN];
	int idx = peer_index_from_conn(conn);

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	printk("Disconnected[%d]: %s reason=0x%02x\n", idx, addr, reason);

	if (idx >= 0) {
		peer_clear((unsigned int)idx, true);
	}

	if (conn_count > 0U) {
		conn_count--;
	}

	master_leds_refresh();
	start_scan();
}

static void conn_param_updated(struct bt_conn *conn, uint16_t interval,
			       uint16_t latency, uint16_t timeout)
{
	printk("Conn param updated[%d]: int=%u lat=%u to=%u\n",
	       peer_index_from_conn(conn), interval, latency, timeout);
}

static void le_phy_updated(struct bt_conn *conn, struct bt_conn_le_phy_info *info)
{
	printk("PHY updated[%d]: tx=0x%x rx=0x%x\n", peer_index_from_conn(conn),
	       info->tx_phy, info->rx_phy);
}

static struct bt_conn_cb conn_callbacks = {
	.connected = connected,
	.disconnected = disconnected,
	.le_param_updated = conn_param_updated,
	.le_phy_updated = le_phy_updated,
};

static int init_leds(void)
{
	int err = dk_leds_init();

	if (err) {
		printk("LED init failed: %d\n", err);
		return err;
	}

	leds_ready = true;
	master_leds_refresh();
	printk("LED map: 0=scan 1=link 2=ota 3=error\n");
	return 0;
}

int master_app_run(void)
{
	int err;

	err = init_leds();
	if (err) {
		/* Continue without LEDs. */
	}

	err = bt_enable(NULL);
	if (err) {
		printk("Bluetooth init failed: %d\n", err);
		return err;
	}

	if (IS_ENABLED(CONFIG_SETTINGS)) {
		settings_load();
	}

	err = nus_client_init();
	if (err) {
		printk("NUS client init failed: %d\n", err);
		return err;
	}

	bt_conn_cb_register(&conn_callbacks);
	bt_le_scan_cb_register(&scan_callbacks);

	printk("BioSpur BLE master ready on nRF52840 DK\n");
	printk("Max connections: %u\n", MASTER_MAX_CONNECTIONS);
	if (strlen(APP_MASTER_ONE_SHOT_CMD) != 0U) {
		printk("One-shot NUS command armed: %s\n", APP_MASTER_ONE_SHOT_CMD);
	}

	start_scan();

	while (1) {
		k_sleep(K_SECONDS(5));
	}

	return 0;
}
