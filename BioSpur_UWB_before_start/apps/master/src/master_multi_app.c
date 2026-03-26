#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
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

#define MASTER_MAX_CONNECTIONS 10U
#define MASTER_DISCOVERY_RETRY_DELAY_MS 1000U
#define MASTER_DISCOVERY_RETRY_LIMIT 5U
#define MASTER_TDMA_SLOT_COUNT_MAX 10U
#define MASTER_TDMA_SLOT_PERIOD_MS 24U
#define MASTER_TDMA_SLOT_ACTIVE_MS 20U
#define MASTER_TDMA_EPOCH_LEAD_MS 3000U
#ifndef APP_MASTER_TAG_NAME_PREFIX
#define APP_MASTER_TAG_NAME_PREFIX "BS"
#endif
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
	bool connect_pending;
	bool one_shot_sent;
	bool ota_ready;
	bool ota_active;
	struct k_work_delayable discovery_retry_work;
	uint8_t discovery_retry_attempts;
	bt_addr_le_t addr;
	bool addr_valid;
	char adv_name[MASTER_NAME_BUF_LEN];
	uint8_t tag_id;
	bool tag_id_valid;
	uint16_t bs_code;
	bool bs_code_valid;
	uint8_t logical_tag_id;
	bool logical_tag_id_valid;
	uint8_t tdma_slot;
	bool tdma_slot_valid;
	uint8_t tdma_generation;
};

static struct master_peer peers[MASTER_MAX_CONNECTIONS];
static int connecting_slot = -1;
static uint8_t conn_count;
static bool scan_running;
static bool auto_connect_enabled = true;
static bool leds_ready;
static bool led_scan_state;
static bool led_link_state;
static bool led_ota_state;
static bool led_error_state;
static uint8_t tdma_generation;
static struct bt_gatt_dm_cb discovery_cb;
static void start_scan(void);
static void stop_scan(void);

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

static void peer_clear(unsigned int idx, bool unref_conn)
{
	if (idx >= ARRAY_SIZE(peers)) {
		return;
	}

	if (unref_conn && peers[idx].conn != NULL) {
		bt_conn_unref(peers[idx].conn);
	}

	(void)k_work_cancel_delayable(&peers[idx].discovery_retry_work);

	peers[idx].conn = NULL;
	peers[idx].connected = false;
	peers[idx].ready = false;
	peers[idx].connect_pending = false;
	peers[idx].one_shot_sent = false;
	peers[idx].ota_ready = false;
	peers[idx].ota_active = false;
	peers[idx].discovery_retry_attempts = 0U;
	peers[idx].addr_valid = false;
	peers[idx].adv_name[0] = '\0';
	peers[idx].tag_id = 0U;
	peers[idx].tag_id_valid = false;
	peers[idx].bs_code = 0U;
	peers[idx].bs_code_valid = false;
	peers[idx].logical_tag_id = 0U;
	peers[idx].logical_tag_id_valid = false;
	peers[idx].tdma_slot = 0U;
	peers[idx].tdma_slot_valid = false;
	peers[idx].tdma_generation = 0U;
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
	size_t prefix_len = strlen(APP_MASTER_TAG_NAME_PREFIX);

	memset(name, 0, sizeof(name));
	bt_data_parse(&copy, scan_name_cb, name);

	if (name[0] == '\0') {
		return false;
	}

	return strncmp(name, APP_MASTER_TAG_NAME_PREFIX, prefix_len) == 0;
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

static bool scan_mfg_bs_code_cb(struct bt_data *data, void *user_data)
{
	uint16_t *bs_code = user_data;

	if (data->type != BT_DATA_MANUFACTURER_DATA || data->data_len < 6U) {
		return true;
	}

	if (data->data[0] == 0xff &&
	    data->data[1] == 0xff &&
	    data->data[2] == 'B') {
		*bs_code = sys_get_le16(&data->data[4]);
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

static bool ad_get_biospur_bs_code(struct net_buf_simple *ad, uint16_t *bs_code)
{
	struct net_buf_simple copy = *ad;
	uint16_t parsed = 0xFFFFU;

	bt_data_parse(&copy, scan_mfg_bs_code_cb, &parsed);
	if (parsed == 0xFFFFU) {
		return false;
	}

	*bs_code = parsed;
	return true;
}

static bool master_peer_sort_before(const struct master_peer *lhs,
				    const struct master_peer *rhs)
{
	if (lhs == NULL || rhs == NULL) {
		return false;
	}

	if (lhs->bs_code_valid && rhs->bs_code_valid && lhs->bs_code != rhs->bs_code) {
		return lhs->bs_code < rhs->bs_code;
	}

	if (lhs->adv_name[0] != '\0' && rhs->adv_name[0] != '\0') {
		int cmp = strcmp(lhs->adv_name, rhs->adv_name);
		if (cmp != 0) {
			return cmp < 0;
		}
	}

	if (lhs->addr_valid && rhs->addr_valid) {
		return bt_addr_le_cmp(&lhs->addr, &rhs->addr) < 0;
	}

	return false;
}

static size_t master_collect_ready_peers(struct master_peer **ordered,
					 size_t ordered_len)
{
	size_t count = 0U;

	for (size_t i = 0U; i < ARRAY_SIZE(peers) && count < ordered_len; ++i) {
		if (!(peers[i].connected && peers[i].ready && peers[i].bs_code_valid)) {
			continue;
		}

		ordered[count++] = &peers[i];
	}

	for (size_t i = 1U; i < count; ++i) {
		struct master_peer *peer = ordered[i];
		size_t j = i;

		while (j > 0U && master_peer_sort_before(peer, ordered[j - 1U])) {
			ordered[j] = ordered[j - 1U];
			j--;
		}
		ordered[j] = peer;
	}

	return count;
}

static int master_send_runtime_config(struct master_peer *peer,
				      uint8_t logical_tag_id,
				      uint8_t slot_index,
				      uint8_t slot_count,
				      uint16_t slot_period_ms,
				      uint16_t slot_active_ms,
				      uint32_t epoch_ms,
				      uint8_t generation)
{
	char cmd[160];
	int err;

	if (peer == NULL || !peer->ready || !peer->connected) {
		return -EINVAL;
	}

	snprintk(cmd, sizeof(cmd),
		 "CFG TAG=%u SLOT=%u COUNT=%u PERIOD=%u ACTIVE=%u EPOCH=%lu GEN=%u PMODE=%u AMODE=%u",
		 (unsigned int)logical_tag_id,
		 (unsigned int)slot_index,
		 (unsigned int)slot_count,
		 (unsigned int)slot_period_ms,
		 (unsigned int)slot_active_ms,
		 (unsigned long)epoch_ms,
		 (unsigned int)generation,
		 0U,
		 0U);
	err = bt_nus_client_send(&peer->nus_client, (const uint8_t *)cmd, strlen(cmd));
	if (err) {
		printk("CFG send failed[%d]: tag=%u slot=%u err=%d\n",
		       peer_index_from_nus(&peer->nus_client),
		       (unsigned int)logical_tag_id,
		       (unsigned int)slot_index,
		       err);
		return err;
	}

	peer->logical_tag_id = logical_tag_id;
	peer->logical_tag_id_valid = true;
	peer->tdma_slot = slot_index;
	peer->tdma_slot_valid = true;
	peer->tdma_generation = generation;
	printk("CFG assigned[%d]: bs=BS%04X tag=%u slot=%u/%u period=%u active=%u gen=%u\n",
	       peer_index_from_nus(&peer->nus_client),
	       (unsigned int)peer->bs_code,
	       (unsigned int)logical_tag_id,
	       (unsigned int)slot_index,
	       (unsigned int)slot_count,
	       (unsigned int)slot_period_ms,
	       (unsigned int)slot_active_ms,
	       (unsigned int)generation);
	return 0;
}

static void master_rebalance_tdma_slots(void)
{
	struct master_peer *ordered[MASTER_MAX_CONNECTIONS];
	size_t ready_count;
	uint8_t slot_count;
	uint32_t epoch_ms;

	ready_count = master_collect_ready_peers(ordered, ARRAY_SIZE(ordered));
	if (ready_count == 0U) {
		return;
	}

	slot_count = (uint8_t)MIN(ready_count, (size_t)MASTER_TDMA_SLOT_COUNT_MAX);
	tdma_generation++;
	epoch_ms = k_uptime_get_32() + MASTER_TDMA_EPOCH_LEAD_MS;
	for (size_t i = 0U; i < slot_count; ++i) {
		(void)master_send_runtime_config(ordered[i],
						 (uint8_t)(i + 1U),
						 (uint8_t)i,
						 slot_count,
						 MASTER_TDMA_SLOT_PERIOD_MS,
						 MASTER_TDMA_SLOT_ACTIVE_MS,
						 epoch_ms,
						 tdma_generation);
	}
}

static void master_try_connect_pending(void)
{
	int slot = -1;
	int err;

	if (!auto_connect_enabled || connecting_slot >= 0 ||
	    conn_count >= MASTER_MAX_CONNECTIONS) {
		return;
	}

	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].connect_pending && peers[i].addr_valid &&
		    peers[i].conn == NULL && !peers[i].connected) {
			slot = (int)i;
			break;
		}
	}

	if (slot < 0) {
		return;
	}

	connecting_slot = slot;
	stop_scan();
	err = bt_conn_le_create(&peers[slot].addr, BT_CONN_LE_CREATE_CONN,
				&fast_conn_params, &peers[slot].conn);
	if (err) {
		printk("Create conn failed[%d]: %d\n", slot, err);
		peer_clear((unsigned int)slot, false);
		connecting_slot = -1;
		start_scan();
	}
}

static void scan_log_candidate(const struct bt_le_scan_recv_info *info,
			       struct net_buf_simple *buf,
			       bool name_match,
			       bool dfu_match,
			       bool token_match,
			       uint16_t bs_code,
			       bool bs_code_valid)
{
	char addr[BT_ADDR_LE_STR_LEN];
	char name[MASTER_NAME_BUF_LEN];
	char bs_name[8];
	struct net_buf_simple copy = *buf;

	bt_addr_le_to_str(info->addr, addr, sizeof(addr));
	memset(name, 0, sizeof(name));
	bt_data_parse(&copy, scan_name_cb, name);
	if (bs_code_valid) {
		snprintk(bs_name, sizeof(bs_name), "BS%04X", bs_code);
	} else {
		strncpy(bs_name, "-", sizeof(bs_name) - 1U);
		bs_name[sizeof(bs_name) - 1U] = '\0';
	}

	printk("SCAN hit: %s rssi=%d name=%s bs=%s name=%u dfu=%u token=%u props=0x%02x\n",
	       addr,
	       info->rssi,
	       name[0] != '\0' ? name : "-",
	       bs_name,
	       name_match ? 1U : 0U,
	       dfu_match ? 1U : 0U,
	       token_match ? 1U : 0U,
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
	printk("Scanning for %s*\n", APP_MASTER_TAG_NAME_PREFIX);
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
	uint8_t version;
	size_t offset;
	size_t used = 0U;

	if (data == NULL || payload == NULL || payload_len == 0U ||
	    len < BLE_SAMPLE_HEADER_LEN) {
		return false;
	}

	if (data[0] != BLE_SAMPLE_MAGIC0 || data[1] != BLE_SAMPLE_MAGIC1) {
		return false;
	}

	version = data[2];
	count = data[3];
	if (version != BLE_SAMPLE_VERSION) {
		/* Accept legacy packets that stored count/version swapped. */
		if (data[3] == BLE_SAMPLE_VERSION && data[2] > 0U) {
			version = data[3];
			count = data[2];
		} else {
			return false;
		}
	}

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
	char bs_name[8];
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

	if (idx >= 0 && peers[idx].bs_code_valid) {
		snprintk(bs_name, sizeof(bs_name), "BS%04X", peers[idx].bs_code);
	} else {
		strncpy(bs_name, "BS????", sizeof(bs_name) - 1U);
		bs_name[sizeof(bs_name) - 1U] = '\0';
	}

	printk("%s notify: %s\n", bs_name, payload);

	if (ble_payload_contains(data, len, "OTA_STATE=READY") ||
	    ble_payload_contains(data, len, "OTA_READY") ||
	    ble_payload_contains(data, len, "OTA_BEGIN_OK")) {
		peers[idx].ota_ready = true;
	}
	if (ble_payload_contains(data, len, "CFG_OK")) {
		unsigned int tag = 0U;
		unsigned int slot = 0U;
		unsigned int slot_count = 0U;
		unsigned int period = 0U;
		unsigned int active = 0U;
		unsigned int generation = 0U;
		unsigned int live = 0U;

		if (sscanf(payload,
			   "CFG_OK TAG=%u SLOT=%u/%u PERIOD=%u ACTIVE=%u GEN=%u LIVE=%u",
			   &tag, &slot, &slot_count, &period, &active,
			   &generation, &live) >= 3) {
			peers[idx].logical_tag_id = (uint8_t)tag;
			peers[idx].logical_tag_id_valid = true;
			peers[idx].tdma_slot = (uint8_t)slot;
			peers[idx].tdma_slot_valid = true;
			peers[idx].tdma_generation = (uint8_t)generation;
			printk("CFG confirmed[%d]: tag=%u slot=%u/%u period=%u active=%u gen=%u live=%u\n",
			       idx, tag, slot, slot_count, period, active,
			       generation, live);
		}
	}
	if (ble_payload_contains(data, len, "TDMA_SET_OK")) {
		uint8_t slot = 0U;
		uint8_t live = 0U;

		if (sscanf(payload, "TDMA_SET_OK SLOT=%hhu LIVE=%hhu", &slot, &live) >= 1) {
			peers[idx].tdma_slot = slot;
			peers[idx].tdma_slot_valid = true;
			printk("TDMA slot confirmed[%d]: slot=%u live=%u\n",
			       idx, (unsigned int)slot, (unsigned int)live);
		}
	}
	if (ble_payload_contains(data, len, "TDMA_SLOT=")) {
		uint8_t slot = 0U;
		unsigned int slot_count = 0U;
		char source[16] = {0};

		if (sscanf(payload, "TDMA_SLOT=%hhu/%u SOURCE=%15s", &slot, &slot_count, source) >= 2) {
			peers[idx].tdma_slot = slot;
			peers[idx].tdma_slot_valid = true;
			printk("TDMA status[%d]: slot=%u/%u source=%s\n",
			       idx, (unsigned int)slot, slot_count, source);
		}
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
	peer->discovery_retry_attempts = 0U;
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

	master_rebalance_tdma_slots();
	master_try_connect_pending();
	if (conn_count < MASTER_MAX_CONNECTIONS) {
		start_scan();
	}

	bt_gatt_dm_data_release(dm);
}

static void discovery_retry_work_fn(struct k_work *work)
{
	struct k_work_delayable *dwork = k_work_delayable_from_work(work);
	struct master_peer *peer = CONTAINER_OF(dwork, struct master_peer,
						discovery_retry_work);
	int idx = (int)(peer - peers);
	int err;

	if (peer->conn == NULL || !peer->connected || peer->ready) {
		return;
	}

	if (peer->discovery_retry_attempts >= MASTER_DISCOVERY_RETRY_LIMIT) {
		printk("NUS discovery retry exhausted[%d]\n", idx);
		return;
	}

	peer->discovery_retry_attempts++;
	err = bt_gatt_dm_start(peer->conn, BT_UUID_NUS_SERVICE, &discovery_cb, peer);
	if (err) {
		printk("Could not start GATT discovery[%d] retry %u: %d\n",
		       idx, peer->discovery_retry_attempts, err);
		(void)k_work_schedule(&peer->discovery_retry_work,
				      K_MSEC(MASTER_DISCOVERY_RETRY_DELAY_MS));
	} else {
		printk("NUS discovery retry armed[%d] attempt=%u\n",
		       idx, peer->discovery_retry_attempts);
	}
}

static void discovery_service_not_found(struct bt_conn *conn, void *context)
{
	struct master_peer *peer = context;
	int idx = peer_index_from_conn(conn);

	printk("NUS service not found[%d]\n", idx);

	if (peer != NULL && peer->connected && !peer->ready) {
		(void)k_work_schedule(&peer->discovery_retry_work,
				      K_MSEC(MASTER_DISCOVERY_RETRY_DELAY_MS));
	}
}

static void discovery_error(struct bt_conn *conn, int err, void *context)
{
	struct master_peer *peer = context;
	int idx = peer_index_from_conn(conn);

	printk("GATT discovery error[%d]: %d\n", idx, err);

	if (peer != NULL && peer->connected && !peer->ready) {
		(void)k_work_schedule(&peer->discovery_retry_work,
				      K_MSEC(MASTER_DISCOVERY_RETRY_DELAY_MS));
	}
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
		if (peer->connected && !peer->ready) {
			(void)k_work_schedule(&peer->discovery_retry_work,
					      K_MSEC(MASTER_DISCOVERY_RETRY_DELAY_MS));
		}
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
	uint16_t bs_code = 0U;
	bool bs_code_valid;
	struct net_buf_simple name_copy = *buf;
	char adv_name[MASTER_NAME_BUF_LEN];

	if (!(info->adv_props & BT_GAP_ADV_PROP_CONNECTABLE)) {
		return;
	}

	name_match = ad_name_matches_target(buf);
	dfu_match = ad_has_dfu_smp_uuid(buf);
	token_match = ad_has_biospur_token(buf);
	tag_id_valid = ad_get_biospur_tag_id(buf, &tag_id);
	bs_code_valid = ad_get_biospur_bs_code(buf, &bs_code);
	memset(adv_name, 0, sizeof(adv_name));
	bt_data_parse(&name_copy, scan_name_cb, adv_name);

	if (!bs_code_valid) {
		return;
	}

	scan_log_candidate(info, buf, name_match, dfu_match, token_match,
			  bs_code, bs_code_valid);

	if (peer_index_from_addr(info->addr) >= 0) {
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
	peers[slot].tag_id = 0U;
	peers[slot].tag_id_valid = false;
	peers[slot].bs_code = bs_code;
	peers[slot].bs_code_valid = bs_code_valid;
	peers[slot].connect_pending = true;

	if (auto_connect_enabled) {
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
}

static struct bt_le_scan_cb scan_callbacks = {
	.recv = scan_recv,
};

static void connected(struct bt_conn *conn, uint8_t conn_err)
{
	char addr[BT_ADDR_LE_STR_LEN];
	char bs_name[8];
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

	if (peers[idx].bs_code_valid) {
		snprintk(bs_name, sizeof(bs_name), "BS%04X", peers[idx].bs_code);
	} else {
		strncpy(bs_name, "-", sizeof(bs_name) - 1U);
		bs_name[sizeof(bs_name) - 1U] = '\0';
	}

	printk("Connected[%d]: %s name=%s bs=%s\n",
	       idx,
	       addr,
	       peers[idx].adv_name[0] != '\0' ? peers[idx].adv_name : "-",
	       bs_name);
	conn_count++;
	connecting_slot = -1;
	peers[idx].connected = true;
	peers[idx].connect_pending = false;
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
	master_rebalance_tdma_slots();
	start_scan();
	master_try_connect_pending();
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

	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		k_work_init_delayable(&peers[i].discovery_retry_work, discovery_retry_work_fn);
	}

	printk("BioSpur BLE master ready on nRF52840 DK\n");
	printk("Max connections: %u\n", MASTER_MAX_CONNECTIONS);
	if (strlen(APP_MASTER_ONE_SHOT_CMD) != 0U) {
		printk("One-shot NUS command armed: %s\n", APP_MASTER_ONE_SHOT_CMD);
	}

	start_scan();
	master_try_connect_pending();

	while (1) {
		k_sleep(K_SECONDS(5));
	}

	return 0;
}

void master_set_scan_only_mode(void)
{
	auto_connect_enabled = false;
	printk("Master discovery mode: SCAN only\n");
}

void master_set_connect_and_start_mode(void)
{
	auto_connect_enabled = true;
	printk("Master discovery mode: CONN & START\n");
	master_try_connect_pending();
}

void master_disconnect_all_peers(void)
{
	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (!peers[i].connected || peers[i].conn == NULL) {
			continue;
		}

		printk("Master disconnecting peer[%zu]: bs=BS%04X\n",
		       i, (unsigned int)peers[i].bs_code);
		(void)bt_conn_disconnect(peers[i].conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
	}
}

void master_restart_discovery(void)
{
	stop_scan();
	start_scan();
	master_try_connect_pending();
}
