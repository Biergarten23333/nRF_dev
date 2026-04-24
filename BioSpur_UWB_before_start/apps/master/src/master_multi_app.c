#include <errno.h>
#include <ctype.h>
#include <limits.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

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

#include "master_multi_app.h"
#include "uwb_tdma.h"

#define MASTER_MAX_CONNECTIONS 10U
#define MASTER_DISCOVERY_RETRY_DELAY_MS 1500U
#define MASTER_DISCOVERY_RETRY_LIMIT 5U
#define MASTER_DISCOVERY_START_SETTLE_MS 350U
#define MASTER_ANCHOR_DISCOVERY_START_SETTLE_MS 900U
#define MASTER_CONNECT_PENDING_TIMEOUT_MS 6000U
#define MASTER_SCAN_HIT_CONNECT_SETTLE_MS 300U
#define MASTER_ANCHOR_CONNECT_UNREADY_LIMIT 1U
#define MASTER_TDMA_SLOT_COUNT_MAX 12U
#define MASTER_TDMA_SLOT_PERIOD_MS 40U
#define MASTER_TDMA_SLOT_ACTIVE_MS 24U
#define MASTER_TDMA_EPOCH_LEAD_MS 3000U
#define MASTER_TDMA_PROFILE_MAX MASTER_MAX_CONNECTIONS
#define MASTER_TDMA_ROTO_DEFAULT_HZ 15U
#define MASTER_TDMA_STATIC_DEFAULT_HZ 5U
#define MASTER_TDMA_MOTION_DEFAULT_HZ 5U
#ifndef APP_MASTER_TAG_NAME_PREFIX
#define APP_MASTER_TAG_NAME_PREFIX "BS"
#endif

#ifndef APP_MASTER_ANCHOR_NAME_PREFIX
#define APP_MASTER_ANCHOR_NAME_PREFIX "ANCHOR-"
#endif
#define MASTER_NAME_BUF_LEN 32U
#define BLE_SAMPLE_MAGIC0 0x42U
#define BLE_SAMPLE_MAGIC1 0x50U
#define BLE_SAMPLE_VERSION 1U
#define BLE_SAMPLE_HEADER_LEN 5U
#define BLE_SAMPLE_RECORD_LEN 24U
#define BLE_CAL_MAGIC0 0x43U
#define BLE_CAL_MAGIC1 0x4dU
#define BLE_CAL_VERSION 1U
#define BLE_CAL_HEADER_LEN 5U
#define BLE_CAL_RECORD_LEN 24U
#define BLE_CAL_ANCHOR_COUNT 8U
#define MASTER_UUID_HEX_LEN 32U
#define BIOSPUR_MFG_UUID_OFS 6U
#define BIOSPUR_MFG_UUID_LEN 16U
#ifndef APP_MASTER_ONE_SHOT_CMD
#define APP_MASTER_ONE_SHOT_CMD ""
#endif
#define MASTER_RUNTIME_ONE_SHOT_CMD_LEN 160U

enum master_peer_link_type {
	MASTER_LINK_NONE = 0,
	MASTER_LINK_NUS = 1,
	MASTER_LINK_ANCHOR_CTRL = 2,
};

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
	struct bt_gatt_subscribe_params anchor_state_sub_params;
	struct bt_gatt_subscribe_params anchor_result_sub_params;
	bool connected;
	bool ready;
	bool connect_pending;
	bool setup_pending;
	bool one_shot_sent;
	bool discovery_inflight;
	bool ota_ready;
	bool ota_active;
	enum master_peer_link_type link_type;
	struct k_work_delayable discovery_retry_work;
	uint8_t discovery_retry_attempts;
	uint8_t discovery_start_failures;
	int64_t connected_at_ms;
	int64_t connect_started_at_ms;
	int64_t connect_queued_at_ms;
	bt_addr_le_t addr;
	bool addr_valid;
	char adv_name[MASTER_NAME_BUF_LEN];
	char adv_uuid[MASTER_UUID_HEX_LEN + 1U];
	uint8_t tag_id;
	bool tag_id_valid;
	uint16_t bs_code;
	bool bs_code_valid;
	uint8_t anchor_id_cfg;
	bool anchor_id_valid;
	uint16_t anchor_ctrl_handle;
	uint16_t anchor_state_handle;
	uint16_t anchor_state_ccc_handle;
	uint16_t anchor_result_handle;
	uint16_t anchor_result_ccc_handle;
	uint8_t logical_tag_id;
	bool logical_tag_id_valid;
	uint8_t tdma_slot;
	bool tdma_slot_valid;
	uint8_t tdma_generation;
};

static bool peer_matches_runtime_target(const struct master_peer *peer);

enum master_tdma_profile_kind {
	MASTER_TDMA_PROFILE_MOTION = 0,
	MASTER_TDMA_PROFILE_STATIC = 1,
	MASTER_TDMA_PROFILE_ROTO = 2,
};

struct master_tdma_profile_entry {
	bool valid;
	uint16_t bs_code;
	enum master_tdma_profile_kind kind;
};

static struct master_tdma_profile_entry tdma_profiles[MASTER_TDMA_PROFILE_MAX];
static uint8_t tdma_roto_target_hz = MASTER_TDMA_ROTO_DEFAULT_HZ;
static uint8_t tdma_static_target_hz = MASTER_TDMA_STATIC_DEFAULT_HZ;
static uint8_t tdma_motion_target_hz = MASTER_TDMA_MOTION_DEFAULT_HZ;

struct master_cal_record {
	bool present;
	uint8_t status;
	uint8_t quality_percent;
	int32_t raw_mm;
	uint32_t filt_mm;
	uint32_t ok_count;
	uint32_t fail_count;
};

struct master_cal_sweep_state {
	bool active;
	uint8_t version;
	uint8_t tag_id;
	uint32_t sweep;
	uint8_t present_count;
	struct master_cal_record records[BLE_CAL_ANCHOR_COUNT];
};

static struct master_peer peers[MASTER_MAX_CONNECTIONS];
static struct master_cal_sweep_state cal_sweep_states[MASTER_MAX_CONNECTIONS];
static int connecting_slot = -1;
static uint8_t conn_count;
static bool scan_running;
static bool auto_connect_enabled = true;
static bool recv_background_gate_open = true;
static uint8_t disconnect_restart_suppress_count;
static uint32_t recv_bg_suppressed_count;
static bool leds_ready;
static bool led_scan_state;
static bool led_link_state;
static bool led_ota_state;
static bool led_error_state;
static bool led_flow_state;
static struct k_work_delayable led_flow_off_work;
static uint8_t tdma_generation;
static char runtime_one_shot_cmd[MASTER_RUNTIME_ONE_SHOT_CMD_LEN];
static bool runtime_one_shot_cmd_set;
static enum master_runtime_target_kind runtime_target_kind = MASTER_TARGET_UNKNOWN;
static enum master_log_mode runtime_log_mode = MASTER_LOG_MODE_RECV;
static int runtime_target_token = -1;
static char runtime_target_name[MASTER_NAME_BUF_LEN];
static char runtime_target_prefix[MASTER_NAME_BUF_LEN];
static char runtime_target_uuid[MASTER_UUID_HEX_LEN + 1U];
static bool runtime_anchor_wildcard_scan;
static char anchor_adv_uuid_map[8][MASTER_UUID_HEX_LEN + 1U];
static struct k_sem anchor_read_sem;
static struct bt_gatt_read_params anchor_read_params;
static char anchor_read_buffer[256];
static size_t anchor_read_length;
static int anchor_read_status = -EAGAIN;
static bool anchor_read_inflight;
static struct k_work connect_pending_work;
static struct bt_gatt_dm_cb discovery_cb;
static bool gatt_discovery_active;
static void start_scan(void);
static void stop_scan(void);
static void master_try_connect_pending(void);
static const char *link_type_label(enum master_peer_link_type type);
static void peer_run_setup(struct master_peer *peer);
static void gatt_discover(struct bt_conn *conn, struct master_peer *peer);
static void exchange_func(struct bt_conn *conn, uint8_t err,
			  struct bt_gatt_exchange_params *params);
static struct k_work_delayable discovery_pump_work;
static void discovery_pump_schedule(uint32_t delay_ms);

static void discovery_pump_work_fn(struct k_work *work)
{
	ARG_UNUSED(work);

	if (gatt_discovery_active) {
		discovery_pump_schedule(50U);
		return;
	}

	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].setup_pending && peers[i].connected &&
		    peers[i].conn != NULL && !peers[i].ready &&
		    !peers[i].discovery_inflight) {
			peer_run_setup(&peers[i]);
			if (gatt_discovery_active) {
				return;
			}
		}
	}
}

static void discovery_pump_schedule(uint32_t delay_ms)
{
	(void)k_work_schedule(&discovery_pump_work, K_MSEC(delay_ms));
}

static void master_cal_reset_state(int idx)
{
	if (idx < 0 || idx >= (int)ARRAY_SIZE(cal_sweep_states)) {
		return;
	}

	memset(&cal_sweep_states[idx], 0, sizeof(cal_sweep_states[idx]));
}

static void connect_pending_work_fn(struct k_work *work)
{
	ARG_UNUSED(work);
	printk("CONNECT work: process pending peer queue\n");
	master_try_connect_pending();
}

static void peer_run_setup(struct master_peer *peer)
{
	int idx = (int)(peer - peers);
	int err;
	int64_t now;
	int64_t wait_ms;
	uint32_t settle_ms;
	unsigned int key;

	if (idx < 0 || idx >= (int)ARRAY_SIZE(peers)) {
		return;
	}
	if (peer->conn == NULL || !peer->connected) {
		printk("SETUP[%d] skipped: conn missing or disconnected\n", idx);
		return;
	}
	if (peer->discovery_inflight) {
		printk("SETUP[%d] skipped: discovery already inflight\n", idx);
		peer->setup_pending = false;
		return;
	}
	if (gatt_discovery_active) {
		printk("SETUP[%d] defer: global discovery busy\n", idx);
		peer->setup_pending = true;
		discovery_pump_schedule(50U);
		return;
	}

	now = k_uptime_get();
	settle_ms = (peer->link_type == MASTER_LINK_ANCHOR_CTRL) ?
		MASTER_ANCHOR_DISCOVERY_START_SETTLE_MS :
		MASTER_DISCOVERY_START_SETTLE_MS;
	wait_ms = peer->connected_at_ms + settle_ms - now;
	if (wait_ms > 0) {
		printk("SETUP[%d] defer: waiting %lld ms before discovery start\n",
		       idx, wait_ms);
		return;
	}

	key = irq_lock();
	if (peer->discovery_inflight) {
		peer->setup_pending = false;
		irq_unlock(key);
		printk("SETUP[%d] skipped: discovery claimed by another context\n", idx);
		return;
	}
	peer->setup_pending = false;
	peer->discovery_inflight = true;
	irq_unlock(key);

	printk("SETUP[%d] begin: link=%s uuid=%s conn=%p\n",
	       idx,
	       link_type_label(peer->link_type),
	       peer->adv_uuid[0] != '\0' ? peer->adv_uuid : "-",
	       peer->conn);
	peer->mtu_exchange_params.func = exchange_func;
	err = bt_gatt_exchange_mtu(peer->conn, &peer->mtu_exchange_params);
	printk("SETUP[%d] mtu_exchange rc=%d\n", idx, err);
	err = bt_conn_le_phy_update(peer->conn, fast_phy_params);
	printk("SETUP[%d] phy_update rc=%d\n", idx, err);
	err = bt_conn_le_param_update(peer->conn, &fast_conn_params);
	printk("SETUP[%d] conn_param_update rc=%d\n", idx, err);
	gatt_discover(peer->conn, peer);
}

static const char *runtime_target_kind_label(enum master_runtime_target_kind kind)
{
	switch (kind) {
	case MASTER_TARGET_ANCHOR:
		return "anchor";
	case MASTER_TARGET_TAG:
		return "tag";
	default:
		return "unknown";
	}
}

static const char *link_type_label(enum master_peer_link_type type)
{
	switch (type) {
	case MASTER_LINK_NUS:
		return "nus";
	case MASTER_LINK_ANCHOR_CTRL:
		return "anchor-ctrl";
	default:
		return "none";
	}
}

static const char *master_runtime_mode_label(void)
{
	switch (runtime_log_mode) {
	case MASTER_LOG_MODE_AUTOPOS:
		return "AUTOPOS";
	case MASTER_LOG_MODE_OTA:
		return "OTA";
	case MASTER_LOG_MODE_RECV:
	default:
		return "RECV";
	}
}

static void master_mode_printk(const char *fmt, ...)
{
	va_list ap;

	printk("[%s] ", master_runtime_mode_label());
	va_start(ap, fmt);
	vprintk(fmt, ap);
	va_end(ap);
}

#define printk master_mode_printk

static int find_ready_anchor_peer_index(void)
{
	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (!peers[i].connected || !peers[i].ready || peers[i].conn == NULL) {
			continue;
		}
		if (peers[i].link_type != MASTER_LINK_ANCHOR_CTRL) {
			continue;
		}
		if (!peer_matches_runtime_target(&peers[i])) {
			continue;
		}
		return (int)i;
	}
	return -1;
}

static bool recv_background_allowed(void)
{
	return recv_background_gate_open;
}

static const char *active_one_shot_command(void)
{
	if (runtime_one_shot_cmd_set && runtime_one_shot_cmd[0] != '\0') {
		return runtime_one_shot_cmd;
	}

	if (strlen(APP_MASTER_ONE_SHOT_CMD) != 0U) {
		return APP_MASTER_ONE_SHOT_CMD;
	}

	return NULL;
}

static bool peer_matches_runtime_target(const struct master_peer *peer)
{
	bool any_filter = false;
	char bs_name[MASTER_NAME_BUF_LEN];

	if (peer == NULL) {
		return false;
	}

	if (runtime_target_uuid[0] != '\0') {
		any_filter = true;
		if (peer->adv_uuid[0] == '\0' ||
		    strcasecmp(peer->adv_uuid, runtime_target_uuid) != 0) {
			return false;
		}
	}

	if (runtime_target_name[0] != '\0') {
		any_filter = true;
		if (peer->adv_name[0] != '\0') {
			if (strcasecmp(peer->adv_name, runtime_target_name) != 0) {
				return false;
			}
		} else if (peer->bs_code_valid) {
			snprintk(bs_name, sizeof(bs_name), "BS%04X",
				 (unsigned int)peer->bs_code);
			if (strcasecmp(bs_name, runtime_target_name) != 0) {
				return false;
			}
		} else {
			return false;
		}
	}

	if (runtime_target_prefix[0] != '\0' && runtime_target_name[0] == '\0') {
		any_filter = true;
		if (peer->adv_name[0] == '\0' ||
		    strncasecmp(peer->adv_name, runtime_target_prefix,
				strlen(runtime_target_prefix)) != 0) {
			return false;
		}
	}

	if (!any_filter) {
		return true;
	}

	return true;
}

static void master_leds_apply(void)
{
	bool autopos_mode = (runtime_log_mode == MASTER_LOG_MODE_AUTOPOS);
	bool recv_mode = (runtime_log_mode == MASTER_LOG_MODE_RECV);
	bool ota_mode = (runtime_log_mode == MASTER_LOG_MODE_OTA) || led_ota_state;

	if (!leds_ready) {
		return;
	}

	(void)dk_set_led(MASTER_LED_SCAN, autopos_mode);
	(void)dk_set_led(MASTER_LED_LINK, recv_mode);
	(void)dk_set_led(MASTER_LED_OTA, ota_mode);
	(void)dk_set_led(MASTER_LED_ERROR, led_error_state || led_flow_state);
}

static void led_flow_off_handler(struct k_work *work)
{
	ARG_UNUSED(work);

	led_flow_state = false;
	master_leds_apply();
}

static void master_led_flow_pulse(void)
{
	led_flow_state = true;
	master_leds_apply();
	(void)k_work_reschedule(&led_flow_off_work, K_MSEC(90));
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

static uint8_t master_anchor_unready_link_count(void)
{
	uint8_t count = 0U;

	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].link_type != MASTER_LINK_ANCHOR_CTRL) {
			continue;
		}
		if (peers[i].connected && !peers[i].ready) {
			count++;
		}
	}

	return count;
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

static int peer_index_from_subscribe_params(struct bt_gatt_subscribe_params *params)
{
	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (&peers[i].anchor_state_sub_params == params ||
		    &peers[i].anchor_result_sub_params == params) {
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

	(void)k_work_cancel_delayable(&peers[idx].discovery_retry_work);
	if (peers[idx].discovery_inflight) {
		gatt_discovery_active = false;
		discovery_pump_schedule(0U);
	}
	if (peers[idx].conn != NULL) {
		if (peers[idx].anchor_state_sub_params.value_handle != 0U) {
			(void)bt_gatt_unsubscribe(peers[idx].conn, &peers[idx].anchor_state_sub_params);
		}
		if (peers[idx].anchor_result_sub_params.value_handle != 0U) {
			(void)bt_gatt_unsubscribe(peers[idx].conn, &peers[idx].anchor_result_sub_params);
		}
	}

	if (unref_conn && peers[idx].conn != NULL) {
		bt_conn_unref(peers[idx].conn);
	}

	peers[idx].conn = NULL;
	peers[idx].connected = false;
	peers[idx].ready = false;
	peers[idx].connect_pending = false;
	peers[idx].setup_pending = false;
	peers[idx].one_shot_sent = false;
	peers[idx].discovery_inflight = false;
	peers[idx].ota_ready = false;
	peers[idx].ota_active = false;
	peers[idx].link_type = MASTER_LINK_NONE;
	peers[idx].discovery_retry_attempts = 0U;
	peers[idx].discovery_start_failures = 0U;
	peers[idx].connected_at_ms = 0;
	peers[idx].connect_started_at_ms = 0;
	peers[idx].connect_queued_at_ms = 0;
	peers[idx].addr_valid = false;
	peers[idx].adv_name[0] = '\0';
	peers[idx].adv_uuid[0] = '\0';
	peers[idx].tag_id = 0U;
	peers[idx].tag_id_valid = false;
	peers[idx].bs_code = 0U;
	peers[idx].bs_code_valid = false;
	peers[idx].anchor_ctrl_handle = 0U;
	peers[idx].anchor_state_handle = 0U;
	peers[idx].anchor_state_ccc_handle = 0U;
	peers[idx].anchor_result_handle = 0U;
	peers[idx].anchor_result_ccc_handle = 0U;
	memset(&peers[idx].anchor_state_sub_params, 0, sizeof(peers[idx].anchor_state_sub_params));
	memset(&peers[idx].anchor_result_sub_params, 0, sizeof(peers[idx].anchor_result_sub_params));
	peers[idx].logical_tag_id = 0U;
	peers[idx].logical_tag_id_valid = false;
	peers[idx].tdma_slot = 0U;
	peers[idx].tdma_slot_valid = false;
	peers[idx].tdma_generation = 0U;
	master_cal_reset_state((int)idx);
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

static bool ad_name_matches_prefix(struct net_buf_simple *ad, const char *prefix)
{
	struct net_buf_simple copy = *ad;
	char name[MASTER_NAME_BUF_LEN];
	size_t prefix_len;

	if (prefix == NULL || prefix[0] == '\0') {
		return false;
	}

	prefix_len = strlen(prefix);

	memset(name, 0, sizeof(name));
	bt_data_parse(&copy, scan_name_cb, name);

	if (name[0] == '\0') {
		return false;
	}

	return strncmp(name, prefix, prefix_len) == 0;
}

static bool ad_name_matches_tag_target(struct net_buf_simple *ad)
{
	return ad_name_matches_prefix(ad, APP_MASTER_TAG_NAME_PREFIX);
}

static bool ad_name_matches_anchor_target(struct net_buf_simple *ad)
{
	return ad_name_matches_prefix(ad, APP_MASTER_ANCHOR_NAME_PREFIX);
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

static bool scan_nus_uuid_cb(struct bt_data *data, void *user_data)
{
	bool *match = user_data;
	static const uint8_t nus_uuid_le[16] = {
		0x9e, 0xca, 0xdc, 0x24, 0x0e, 0xe5, 0xa9, 0xe0,
		0x93, 0xf3, 0xa3, 0xb5, 0x01, 0x00, 0x40, 0x6e,
	};

	if (*match) {
		return false;
	}

	if (data->type != BT_DATA_UUID128_ALL &&
	    data->type != BT_DATA_UUID128_SOME) {
		return true;
	}

	for (size_t offset = 0U; offset + sizeof(nus_uuid_le) <= data->data_len;
	     offset += sizeof(nus_uuid_le)) {
		if (memcmp(&data->data[offset], nus_uuid_le, sizeof(nus_uuid_le)) == 0) {
			*match = true;
			return false;
		}
	}

	return true;
}

static bool ad_has_nus_uuid(struct net_buf_simple *ad)
{
	struct net_buf_simple copy = *ad;
	bool match = false;

	bt_data_parse(&copy, scan_nus_uuid_cb, &match);
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

	/* Legacy tag MFG payload only: FF FF 'B' <token> <bs_lo> <bs_hi> */
	if (data->data[0] == 0xff &&
	    data->data[1] == 0xff &&
	    data->data[2] == 'B' &&
	    (data->data_len < 5U || data->data[3] != 'S')) {
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
	    data->data[2] == 'B' &&
	    (data->data_len < 5U || data->data[3] != 'S')) {
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
	    data->data[2] == 'B' &&
	    (data->data_len < 5U || data->data[3] != 'S')) {
		*bs_code = sys_get_le16(&data->data[4]);
		return false;
	}

	return true;
}

static bool scan_mfg_anchor_id_cb(struct bt_data *data, void *user_data)
{
	uint8_t *anchor_id = user_data;

	if (data->type != BT_DATA_MANUFACTURER_DATA || data->data_len < 23U) {
		return true;
	}

	if (data->data[0] != 0xff || data->data[1] != 0xff ||
	    data->data[2] != 'B' || data->data[3] != 'S') {
		return true;
	}

	if (data->data[22] >= 1U && data->data[22] <= 8U) {
		*anchor_id = data->data[22];
		return false;
	}

	return true;
}

static bool scan_mfg_uuid_cb(struct bt_data *data, void *user_data)
{
	char *uuid_hex = user_data;

	if (uuid_hex[0] != '\0') {
		return false;
	}

	if (data->type != BT_DATA_MANUFACTURER_DATA ||
	    data->data_len < BIOSPUR_MFG_UUID_OFS + BIOSPUR_MFG_UUID_LEN) {
		return true;
	}

	if (data->data[0] != 0xff || data->data[1] != 0xff ||
	    data->data[2] != 'B' || data->data[3] != 'S') {
		return true;
	}

	for (size_t i = 0U; i < BIOSPUR_MFG_UUID_LEN; ++i) {
		(void)snprintk(&uuid_hex[i * 2U], 3, "%02X",
			       data->data[BIOSPUR_MFG_UUID_OFS + i]);
	}
	uuid_hex[MASTER_UUID_HEX_LEN] = '\0';
	return false;
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

static bool ad_get_biospur_anchor_id(struct net_buf_simple *ad, uint8_t *anchor_id)
{
	struct net_buf_simple copy = *ad;
	uint8_t parsed = 0U;

	bt_data_parse(&copy, scan_mfg_anchor_id_cb, &parsed);
	if (parsed < 1U || parsed > 8U) {
		return false;
	}

	*anchor_id = parsed;
	return true;
}

static bool ad_extract_uuid_hex(struct net_buf_simple *ad, char *uuid_hex, size_t uuid_hex_len)
{
	struct net_buf_simple copy = *ad;

	if (uuid_hex == NULL || uuid_hex_len < MASTER_UUID_HEX_LEN + 1U) {
		return false;
	}

	uuid_hex[0] = '\0';
	bt_data_parse(&copy, scan_mfg_uuid_cb, uuid_hex);
	return uuid_hex[0] != '\0';
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

static const char *master_tdma_profile_label(enum master_tdma_profile_kind kind)
{
	switch (kind) {
	case MASTER_TDMA_PROFILE_STATIC:
		return "static";
	case MASTER_TDMA_PROFILE_ROTO:
		return "roto";
	case MASTER_TDMA_PROFILE_MOTION:
	default:
		return "motion";
	}
}

static uint8_t master_tdma_profile_pmode(enum master_tdma_profile_kind kind)
{
	switch (kind) {
	case MASTER_TDMA_PROFILE_STATIC:
		return UWB_TAG_POSITIONING_MODE_CAL_STATIC;
	case MASTER_TDMA_PROFILE_ROTO:
		return UWB_TAG_POSITIONING_MODE_CAL_ROTO;
	case MASTER_TDMA_PROFILE_MOTION:
	default:
		return UWB_TAG_POSITIONING_MODE_DYNAMIC;
	}
}

static uint8_t master_tdma_profile_target_hz(enum master_tdma_profile_kind kind)
{
	switch (kind) {
	case MASTER_TDMA_PROFILE_STATIC:
		return tdma_static_target_hz;
	case MASTER_TDMA_PROFILE_ROTO:
		return tdma_roto_target_hz;
	case MASTER_TDMA_PROFILE_MOTION:
	default:
		return tdma_motion_target_hz;
	}
}

static bool master_tdma_parse_profile_kind(const char *profile,
					   enum master_tdma_profile_kind *kind)
{
	if (profile == NULL || kind == NULL) {
		return false;
	}

	if (strcasecmp(profile, "static") == 0 ||
	    strcasecmp(profile, "cal_static") == 0) {
		*kind = MASTER_TDMA_PROFILE_STATIC;
		return true;
	}
	if (strcasecmp(profile, "roto") == 0 ||
	    strcasecmp(profile, "cal_roto") == 0) {
		*kind = MASTER_TDMA_PROFILE_ROTO;
		return true;
	}
	if (strcasecmp(profile, "motion") == 0 ||
	    strcasecmp(profile, "mmot") == 0) {
		*kind = MASTER_TDMA_PROFILE_MOTION;
		return true;
	}

	return false;
}

static bool master_tdma_parse_bs_code(const char *bs_name, uint16_t *bs_code)
{
	const char *p = bs_name;
	char *end = NULL;
	unsigned long value;

	if (bs_name == NULL || bs_code == NULL) {
		return false;
	}

	if (strncasecmp(p, "BS", 2) == 0) {
		p += 2;
	}
	if (*p == '\0') {
		return false;
	}

	value = strtoul(p, &end, 16);
	if (end == p || *end != '\0' || value > UINT16_MAX) {
		return false;
	}

	*bs_code = (uint16_t)value;
	return true;
}

static enum master_tdma_profile_kind master_tdma_profile_for_peer(
	const struct master_peer *peer)
{
	if (peer == NULL || !peer->bs_code_valid) {
		return MASTER_TDMA_PROFILE_MOTION;
	}

	for (size_t i = 0U; i < ARRAY_SIZE(tdma_profiles); ++i) {
		if (tdma_profiles[i].valid && tdma_profiles[i].bs_code == peer->bs_code) {
			return tdma_profiles[i].kind;
		}
	}

	return MASTER_TDMA_PROFILE_MOTION;
}

static bool master_tdma_any_profile_defined(void)
{
	for (size_t i = 0U; i < ARRAY_SIZE(tdma_profiles); ++i) {
		if (tdma_profiles[i].valid) {
			return true;
		}
	}

	return false;
}

static bool master_tdma_profile_has_bs_code(uint16_t bs_code)
{
	for (size_t i = 0U; i < ARRAY_SIZE(tdma_profiles); ++i) {
		if (tdma_profiles[i].valid && tdma_profiles[i].bs_code == bs_code) {
			return true;
		}
	}

	return false;
}

static int master_send_runtime_config(struct master_peer *peer,
				      uint8_t logical_tag_id,
				      uint8_t slot_index,
				      uint8_t slot_count,
				      uint16_t slot_mask,
				      uint16_t slot_period_ms,
				      uint16_t slot_active_ms,
				      uint32_t epoch_ms,
				      uint8_t generation,
				      uint8_t positioning_mode)
{
	char cmd[160];
	int err;

	if (peer == NULL || !peer->ready || !peer->connected) {
		return -EINVAL;
	}

	snprintk(cmd, sizeof(cmd),
		 "CFG TAG=%u SLOT=%u COUNT=%u MASK=0x%04X PERIOD=%u ACTIVE=%u EPOCH=%lu GEN=%u PMODE=%u AMODE=%u",
		 (unsigned int)logical_tag_id,
		 (unsigned int)slot_index,
		 (unsigned int)slot_count,
		 (unsigned int)slot_mask,
		 (unsigned int)slot_period_ms,
		 (unsigned int)slot_active_ms,
		 (unsigned long)epoch_ms,
		 (unsigned int)generation,
		 (unsigned int)positioning_mode,
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
	printk("CFG assigned[%d]: bs=BS%04X tag=%u slot=%u/%u mask=0x%04X period=%u active=%u gen=%u pmode=%u\n",
	       peer_index_from_nus(&peer->nus_client),
	       (unsigned int)peer->bs_code,
	       (unsigned int)logical_tag_id,
	       (unsigned int)slot_index,
	       (unsigned int)slot_count,
	       (unsigned int)slot_mask,
	       (unsigned int)slot_period_ms,
	       (unsigned int)slot_active_ms,
	       (unsigned int)generation,
	       (unsigned int)positioning_mode);
	return 0;
}

static uint8_t master_tdma_build_occupied_slots(uint8_t slot_count,
						 uint8_t total_assignments,
						 uint8_t *occupied_slots,
						 size_t occupied_len)
{
	uint8_t written = 0U;
	bool used[MASTER_TDMA_SLOT_COUNT_MAX] = { false };

	if (occupied_slots == NULL || occupied_len == 0U || slot_count == 0U ||
	    total_assignments == 0U) {
		return 0U;
	}

	for (uint8_t j = 0U; j < total_assignments && written < occupied_len; ++j) {
		uint8_t pos =
			(uint8_t)(((uint32_t)j * (uint32_t)slot_count +
				   (uint32_t)total_assignments / 2U) /
				  (uint32_t)total_assignments);

		if (pos >= slot_count) {
			pos = (uint8_t)(slot_count - 1U);
		}
		while (pos < slot_count && used[pos]) {
			pos++;
		}
		if (pos >= slot_count) {
			for (pos = 0U; pos < slot_count; ++pos) {
				if (!used[pos]) {
					break;
				}
			}
		}
		if (pos >= slot_count) {
			break;
		}

		used[pos] = true;
		occupied_slots[written++] = pos;
	}

	return written;
}

static uint8_t master_tdma_select_fair_owner(const uint8_t *target_slots,
					      const uint8_t *assigned_slots,
					      int32_t *credits,
					      size_t peer_count,
					      uint8_t total_assignments)
{
	int best_idx = -1;
	int32_t best_credit = INT32_MIN;

	for (size_t i = 0U; i < peer_count; ++i) {
		if (assigned_slots[i] >= target_slots[i] || target_slots[i] == 0U) {
			continue;
		}

		credits[i] += (int32_t)target_slots[i];
		if (credits[i] > best_credit) {
			best_credit = credits[i];
			best_idx = (int)i;
		}
	}

	if (best_idx < 0) {
		return UINT8_MAX;
	}

	credits[best_idx] -= (int32_t)total_assignments;
	return (uint8_t)best_idx;
}

static void master_rebalance_tdma_slots(void)
{
	struct master_peer *ordered[MASTER_MAX_CONNECTIONS];
	size_t ready_count;
	uint8_t best_slot_count = 0U;
	uint8_t target_slots[MASTER_MAX_CONNECTIONS] = { 0U };
	uint16_t slot_masks[MASTER_MAX_CONNECTIONS] = { 0U };
	enum master_tdma_profile_kind kinds[MASTER_MAX_CONNECTIONS];
	uint32_t epoch_deadline_ms;
	uint32_t best_error = UINT32_MAX;
	uint8_t total_target_slots = 0U;
	uint8_t occupied_slots[MASTER_TDMA_SLOT_COUNT_MAX] = { 0U };
	uint8_t assigned_slots[MASTER_MAX_CONNECTIONS] = { 0U };
	int32_t credits[MASTER_MAX_CONNECTIONS] = { 0 };

	ready_count = master_collect_ready_peers(ordered, ARRAY_SIZE(ordered));
	if (ready_count == 0U) {
		return;
	}

	for (size_t i = 0U; i < ready_count; ++i) {
		kinds[i] = master_tdma_profile_for_peer(ordered[i]);
	}

	for (uint8_t candidate_count = (uint8_t)ready_count;
	     candidate_count <= MASTER_TDMA_SLOT_COUNT_MAX;
	     ++candidate_count) {
		uint8_t candidate_slots[MASTER_MAX_CONNECTIONS] = { 0U };
		uint8_t total_slots = 0U;
		uint32_t error = 0U;

		for (size_t i = 0U; i < ready_count; ++i) {
			uint32_t target_hz = master_tdma_profile_target_hz(kinds[i]);
			uint32_t numerator =
				target_hz * candidate_count * MASTER_TDMA_SLOT_PERIOD_MS;
			uint8_t slots =
				(uint8_t)((numerator + 500U) / 1000U);

			if (slots == 0U) {
				slots = 1U;
			}
			candidate_slots[i] = slots;
			total_slots += slots;
		}

		if (total_slots > candidate_count) {
			continue;
		}

		for (size_t i = 0U; i < ready_count; ++i) {
			uint32_t actual_hz_x100 =
				((uint32_t)candidate_slots[i] * 100000U) /
				((uint32_t)candidate_count * MASTER_TDMA_SLOT_PERIOD_MS);
			uint32_t target_hz_x100 =
				(uint32_t)master_tdma_profile_target_hz(kinds[i]) * 100U;

			error += (actual_hz_x100 > target_hz_x100) ?
				 (actual_hz_x100 - target_hz_x100) :
				 (target_hz_x100 - actual_hz_x100);
		}

		if (error < best_error ||
		    (error == best_error && candidate_count > best_slot_count)) {
			best_error = error;
			best_slot_count = candidate_count;
			memcpy(target_slots, candidate_slots, sizeof(target_slots));
		}
	}

	if (best_slot_count == 0U) {
		best_slot_count = (uint8_t)MIN(ready_count, (size_t)MASTER_TDMA_SLOT_COUNT_MAX);
		for (size_t i = 0U; i < ready_count; ++i) {
			target_slots[i] = 1U;
		}
	}

	for (size_t i = 0U; i < ready_count; ++i) {
		total_target_slots += target_slots[i];
	}
	if (total_target_slots > best_slot_count) {
		total_target_slots = best_slot_count;
	}

	if (master_tdma_build_occupied_slots(best_slot_count,
					      total_target_slots,
					      occupied_slots,
					      ARRAY_SIZE(occupied_slots)) != total_target_slots) {
		printk("TDMA occupied-slot build failed: total=%u count=%u\n",
		       (unsigned int)total_target_slots,
		       (unsigned int)best_slot_count);
		return;
	}

	for (uint8_t j = 0U; j < total_target_slots; ++j) {
		uint8_t owner = master_tdma_select_fair_owner(target_slots,
							      assigned_slots,
							      credits,
							      ready_count,
							      total_target_slots);
		if (owner == UINT8_MAX) {
			break;
		}

		slot_masks[owner] |= (uint16_t)(1U << occupied_slots[j]);
		assigned_slots[owner]++;
	}

	tdma_generation++;
	epoch_deadline_ms = k_uptime_get_32() + MASTER_TDMA_EPOCH_LEAD_MS;
	for (size_t i = 0U; i < ready_count; ++i) {
		uint8_t first_slot = 0U;
		uint32_t now_ms;
		uint32_t epoch_delay_ms;

		for (uint8_t slot = 0U; slot < best_slot_count; ++slot) {
			if ((slot_masks[i] & (uint16_t)(1U << slot)) != 0U) {
				first_slot = slot;
				break;
			}
		}

		now_ms = k_uptime_get_32();
		epoch_delay_ms =
			((int32_t)(epoch_deadline_ms - now_ms) > 0) ?
				(epoch_deadline_ms - now_ms) : 0U;
		(void)master_send_runtime_config(ordered[i],
						 (uint8_t)(i + 1U),
						 first_slot,
						 best_slot_count,
						 slot_masks[i],
						 MASTER_TDMA_SLOT_PERIOD_MS,
						 MASTER_TDMA_SLOT_ACTIVE_MS,
						 epoch_delay_ms,
						 tdma_generation,
						 master_tdma_profile_pmode(kinds[i]));
		printk("TDMA weighted[%zu]: bs=BS%04X profile=%s target=%uHz mask=0x%04X slots=%u/%u actual_x100=%lu epoch_delay=%lu\n",
		       i,
		       (unsigned int)ordered[i]->bs_code,
		       master_tdma_profile_label(kinds[i]),
		       (unsigned int)master_tdma_profile_target_hz(kinds[i]),
		       (unsigned int)slot_masks[i],
		       (unsigned int)target_slots[i],
		       (unsigned int)best_slot_count,
		       (unsigned long)(((uint32_t)target_slots[i] * 100000U) /
				       ((uint32_t)best_slot_count *
					MASTER_TDMA_SLOT_PERIOD_MS)),
		       (unsigned long)epoch_delay_ms);
	}
}

static void master_try_connect_pending(void)
{
	int slot = -1;
	int err;
	int64_t now = k_uptime_get();

	if (!recv_background_allowed()) {
		return;
	}

	if (connecting_slot >= 0 && connecting_slot < (int)ARRAY_SIZE(peers)) {
		struct master_peer *peer = &peers[connecting_slot];

		if (!peer->connected && peer->connect_started_at_ms > 0 &&
		    now - peer->connect_started_at_ms > MASTER_CONNECT_PENDING_TIMEOUT_MS) {
			printk("CONNECT pending[%d] watchdog: waited=%lld ms uuid=%s; clearing stale pending link\n",
			       connecting_slot,
			       now - peer->connect_started_at_ms,
			       peer->adv_uuid[0] != '\0' ? peer->adv_uuid : "-");
			if (peer->conn != NULL) {
				(void)bt_conn_disconnect(peer->conn,
							 BT_HCI_ERR_REMOTE_USER_TERM_CONN);
			}
			peer_clear((unsigned int)connecting_slot, true);
			connecting_slot = -1;
			start_scan();
		}
	}

	if (!auto_connect_enabled || connecting_slot >= 0 ||
	    conn_count >= MASTER_MAX_CONNECTIONS) {
		return;
	}

	if (runtime_target_kind == MASTER_TARGET_ANCHOR &&
	    master_anchor_unready_link_count() >= MASTER_ANCHOR_CONNECT_UNREADY_LIMIT) {
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

	if (peers[slot].conn == NULL) {
		struct bt_conn *existing =
			bt_conn_lookup_addr_le(BT_ID_DEFAULT, &peers[slot].addr);

		if (existing != NULL) {
			char addr[BT_ADDR_LE_STR_LEN];

			bt_addr_le_to_str(&peers[slot].addr, addr, sizeof(addr));
			printk("CONNECT pending[%d]: found untracked existing conn for %s; disconnect and rescan\n",
			       slot, addr);
			(void)bt_conn_disconnect(existing, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
			bt_conn_unref(existing);
			peer_clear((unsigned int)slot, false);
			connecting_slot = -1;
			start_scan();
			return;
		}
	}

	if (peers[slot].connect_queued_at_ms > 0 &&
	    now - peers[slot].connect_queued_at_ms < MASTER_SCAN_HIT_CONNECT_SETTLE_MS) {
		return;
	}

	printk("CONNECT pending[%d]: addr_valid=%u link=%s uuid=%s scan_running=%u conn_count=%u\n",
	       slot,
	       peers[slot].addr_valid ? 1U : 0U,
	       link_type_label(peers[slot].link_type),
	       peers[slot].adv_uuid[0] != '\0' ? peers[slot].adv_uuid : "-",
	       scan_running ? 1U : 0U,
	       (unsigned int)conn_count);
	if (scan_running) {
		int stop_err = 0;

		for (int attempt = 0; attempt < 10; ++attempt) {
			stop_err = bt_le_scan_stop();
			if (stop_err == 0 || stop_err == -EALREADY) {
				break;
			}
			if (stop_err != -EBUSY) {
				break;
			}
			k_sleep(K_MSEC(50));
		}

		printk("CONNECT pending[%d]: bt_le_scan_stop rc=%d scan_running=%u\n",
		       slot, stop_err, scan_running ? 1U : 0U);
		if (stop_err != 0 && stop_err != -EALREADY) {
			printk("CONNECT pending[%d]: scan stop busy; retry later without reserving slot\n",
			       slot);
			return;
		}

		scan_running = false;
		master_leds_refresh();
	}

	connecting_slot = slot;
	peers[slot].connect_started_at_ms = now;
	err = bt_conn_le_create(&peers[slot].addr, BT_CONN_LE_CREATE_CONN,
				&fast_conn_params, &peers[slot].conn);
	printk("CONNECT pending[%d] rc=%d conn=%p\n", slot, err, peers[slot].conn);
	if (err) {
		printk("Create conn failed[%d]: %d\n", slot, err);
		peer_clear((unsigned int)slot, true);
		connecting_slot = -1;
		start_scan();
	}
}

void master_process_connect_pending(void)
{
	master_try_connect_pending();
}

void master_process_setup_pending(void)
{
	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (!peers[i].setup_pending || peers[i].ready) {
			continue;
		}
		if (!peers[i].connected || peers[i].conn == NULL) {
			continue;
		}
		peer_run_setup(&peers[i]);
	}
}

static void scan_log_candidate(const struct bt_le_scan_recv_info *info,
			       struct net_buf_simple *buf,
			       bool name_match,
			       bool anchor_name_match,
			       bool nus_match,
			       bool dfu_match,
			       bool token_match,
			       const char *uuid_hex,
			       bool uuid_match,
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

	printk("SCAN hit: %s rssi=%d name=%s bs=%s uuid=%s target=%s uuid_ok=%u tag_name=%u anchor_name=%u nus=%u dfu=%u token=%u props=0x%02x\n",
	       addr,
	       info->rssi,
	       name[0] != '\0' ? name : "-",
	       bs_name,
	       (uuid_hex != NULL && uuid_hex[0] != '\0') ? uuid_hex : "-",
	       runtime_target_kind_label(runtime_target_kind),
	       uuid_match ? 1U : 0U,
	       name_match ? 1U : 0U,
	       anchor_name_match ? 1U : 0U,
	       nus_match ? 1U : 0U,
	       dfu_match ? 1U : 0U,
	       token_match ? 1U : 0U,
	       info->adv_props);
}

static void start_scan(void)
{
	int err;
	const char *prefix = (runtime_target_kind == MASTER_TARGET_ANCHOR) ?
		APP_MASTER_ANCHOR_NAME_PREFIX : APP_MASTER_TAG_NAME_PREFIX;

	if (!recv_background_allowed()) {
		printk("RECV_BG suppressed: scan start skipped\n");
		return;
	}

	if (scan_running || connecting_slot >= 0 || conn_count >= MASTER_MAX_CONNECTIONS) {
		return;
	}

	printk("SCAN start req: bt_ready=%u scan_running=%u connecting_slot=%d conn_count=%u target=%s prefix=%s\n",
	       bt_is_ready() ? 1U : 0U,
	       scan_running ? 1U : 0U,
	       connecting_slot,
	       conn_count,
	       runtime_target_kind_label(runtime_target_kind),
	       prefix);

	err = bt_le_scan_start(BT_LE_SCAN_ACTIVE, NULL);
	if (err) {
		if (err == -EALREADY || err == -EBUSY) {
			scan_running = true;
			master_leds_refresh();
			printk("Scanning for %s* (already active rc=%d)\n", prefix, err);
			return;
		}
		printk("Failed to start scan: %d\n", err);
		return;
	}

	scan_running = true;
	master_leds_refresh();
	printk("Scanning for %s*\n", prefix);
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

static const char *sample_cal_status_label(uint8_t status)
{
	switch (status) {
	case 0U:
		return "ok";
	case 1U:
		return "reject";
	case 2U:
		return "timeout";
	case 3U:
		return "error";
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

static bool ble_decode_cal_packet(const uint8_t *data, uint16_t len,
				  char *payload, size_t payload_len)
{
	uint8_t count;
	uint8_t version;
	size_t offset;
	size_t used = 0U;

	if (data == NULL || payload == NULL || payload_len == 0U ||
	    len < BLE_CAL_HEADER_LEN) {
		return false;
	}

	if (data[0] != BLE_CAL_MAGIC0 || data[1] != BLE_CAL_MAGIC1) {
		return false;
	}

	version = data[2];
	count = data[3];
	if (version != BLE_CAL_VERSION) {
		return false;
	}

	offset = BLE_CAL_HEADER_LEN;
	if (count == 0U || len < offset + (size_t)count * BLE_CAL_RECORD_LEN) {
		return false;
	}

	payload[0] = '\0';
	for (uint8_t i = 0U; i < count; ++i) {
		uint32_t sweep = sys_get_le32(&data[offset]);
		uint8_t anchor_id = data[offset + 4U];
		uint8_t status = data[offset + 5U];
		uint8_t quality_percent = data[offset + 6U];
		int32_t raw_mm = (int32_t)sys_get_le32(&data[offset + 8U]);
		uint32_t filt_mm = sys_get_le32(&data[offset + 12U]);
		uint32_t ok_count = sys_get_le32(&data[offset + 16U]);
		uint32_t fail_count = sys_get_le32(&data[offset + 20U]);
		int written;

		written = snprintk(
			&payload[used], payload_len - used,
			"%sCM;%u;%u;%u;%s;%d;%u;%u;%u;%u",
			(i == 0U) ? "" : "|",
			(unsigned int)version,
			(unsigned int)sweep,
			(unsigned int)anchor_id,
			sample_cal_status_label(status),
			(int)raw_mm,
			(unsigned int)filt_mm,
			(unsigned int)quality_percent,
			(unsigned int)ok_count,
			(unsigned int)fail_count);
		if (written < 0 || (size_t)written >= payload_len - used) {
			return false;
		}
		used += (size_t)written;

		offset += BLE_CAL_RECORD_LEN;
	}

	return true;
}

static bool ble_collect_cal_packet(const uint8_t *data, uint16_t len, int idx,
				   char *payload, size_t payload_len)
{
	struct master_cal_sweep_state *state;
	uint8_t version;
	uint8_t count;
	uint8_t tag_id;
	size_t offset;
	size_t used = 0U;

	if (idx < 0 || idx >= (int)ARRAY_SIZE(cal_sweep_states) || payload == NULL ||
	    payload_len == 0U || data == NULL || len < BLE_CAL_HEADER_LEN) {
		return false;
	}

	if (data[0] != BLE_CAL_MAGIC0 || data[1] != BLE_CAL_MAGIC1) {
		return false;
	}

	version = data[2];
	count = data[3];
	tag_id = data[4];
	if (version != BLE_CAL_VERSION) {
		return true;
	}

	offset = BLE_CAL_HEADER_LEN;
	if (count == 0U || len < offset + (size_t)count * BLE_CAL_RECORD_LEN) {
		return true;
	}

	state = &cal_sweep_states[idx];
	payload[0] = '\0';

	for (uint8_t i = 0U; i < count; ++i) {
		uint32_t sweep = sys_get_le32(&data[offset]);
		uint8_t anchor_id = data[offset + 4U];
		uint8_t status = data[offset + 5U];
		uint8_t quality_percent = data[offset + 6U];
		int32_t raw_mm = (int32_t)sys_get_le32(&data[offset + 8U]);
		uint32_t filt_mm = sys_get_le32(&data[offset + 12U]);
		uint32_t ok_count = sys_get_le32(&data[offset + 16U]);
		uint32_t fail_count = sys_get_le32(&data[offset + 20U]);

		if (!state->active || state->sweep != sweep) {
			memset(state, 0, sizeof(*state));
			state->active = true;
			state->version = version;
			state->tag_id = tag_id;
			state->sweep = sweep;
		}

		if (anchor_id < BLE_CAL_ANCHOR_COUNT) {
			struct master_cal_record *record = &state->records[anchor_id];

			if (!record->present) {
				state->present_count++;
			}

			record->present = true;
			record->status = status;
			record->quality_percent = quality_percent;
			record->raw_mm = raw_mm;
			record->filt_mm = filt_mm;
			record->ok_count = ok_count;
			record->fail_count = fail_count;
		}

		offset += BLE_CAL_RECORD_LEN;
	}

	if (state->present_count == 0U) {
		return true;
	}

	for (uint8_t anchor_id = 0U; anchor_id < BLE_CAL_ANCHOR_COUNT; ++anchor_id) {
		const struct master_cal_record *record = &state->records[anchor_id];
		int written;

		if (!record->present) {
			continue;
		}

		written = snprintk(
			&payload[used], payload_len - used,
			"%sCM;%u;%u;%u;%s;%d;%u;%u;%u;%u",
			(used == 0U) ? "" : "|",
			(unsigned int)state->version,
			(unsigned int)state->sweep,
			(unsigned int)anchor_id,
			sample_cal_status_label(record->status),
			(int)record->raw_mm,
			(unsigned int)record->filt_mm,
			(unsigned int)record->quality_percent,
			(unsigned int)record->ok_count,
			(unsigned int)record->fail_count);
		if (written < 0 || (size_t)written >= payload_len - used) {
			return true;
		}

		used += (size_t)written;
	}

	memset(state, 0, sizeof(*state));
	return true;
}

static void stop_scan(void)
{
	int err = 0;

	if (!scan_running) {
		return;
	}

	for (int attempt = 0; attempt < 6; ++attempt) {
		err = bt_le_scan_stop();
		if (err == 0 || err == -EALREADY) {
			break;
		}
		if (err != -EBUSY) {
			break;
		}
		k_sleep(K_MSEC(25));
	}

	if (err == 0 || err == -EALREADY) {
		scan_running = false;
		master_leds_refresh();
	} else {
		printk("SCAN stop failed rc=%d; forcing local scan state clear\n", err);
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
	char payload[1024];
	size_t copy_len;
	bool decoded_sample = false;
	bool consumed_cal = false;

	master_led_flow_pulse();

	payload[0] = '\0';
	decoded_sample = ble_decode_sample_packet(data, len, payload, sizeof(payload));
	consumed_cal = ble_collect_cal_packet(data, len, idx, payload, sizeof(payload));
	if (!decoded_sample && !consumed_cal) {
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

	if (payload[0] != '\0') {
		printk("%s notify: %s\n", bs_name, payload);
	}

	if (ble_payload_contains(data, len, "OTA_STATE=READY") ||
	    ble_payload_contains(data, len, "OTA_READY") ||
	    ble_payload_contains(data, len, "OTA_BEGIN_OK")) {
		peers[idx].ota_ready = true;
	}
	if (ble_payload_contains(data, len, "CFG_OK")) {
		unsigned int tag = 0U;
		unsigned int slot = 0U;
		unsigned int slot_count = 0U;
		unsigned int slot_mask = 0U;
		unsigned int period = 0U;
		unsigned int active = 0U;
		unsigned int generation = 0U;
		unsigned int live = 0U;

		if (sscanf(payload,
			   "CFG_OK TAG=%u SLOT=%u/%u MASK=0x%X PERIOD=%u ACTIVE=%u GEN=%u LIVE=%u",
			   &tag, &slot, &slot_count, &slot_mask, &period, &active,
			   &generation, &live) >= 3) {
			peers[idx].logical_tag_id = (uint8_t)tag;
			peers[idx].logical_tag_id_valid = true;
			peers[idx].tdma_slot = (uint8_t)slot;
			peers[idx].tdma_slot_valid = true;
			peers[idx].tdma_generation = (uint8_t)generation;
			printk("CFG confirmed[%d]: tag=%u slot=%u/%u mask=0x%04X period=%u active=%u gen=%u live=%u\n",
			       idx, tag, slot, slot_count, slot_mask, period, active,
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

	master_led_flow_pulse();

	if (err) {
		printk("BLE write error[%d]: 0x%02x\n", peer_index_from_nus(nus), err);
		led_error_state = true;
		master_leds_refresh();
	}
}

static uint8_t anchor_ctrl_notify_cb(struct bt_conn *conn,
				      struct bt_gatt_subscribe_params *params,
				      const void *data, uint16_t length)
{
	int idx = peer_index_from_subscribe_params(params);
	char payload[BLE_SAMPLE_HEADER_LEN + BLE_SAMPLE_RECORD_LEN * 4];
	size_t copy_len;

	ARG_UNUSED(conn);

	if (data == NULL || length == 0U) {
		return BT_GATT_ITER_CONTINUE;
	}

	master_led_flow_pulse();

	copy_len = MIN((size_t)length, sizeof(payload) - 1U);
	memcpy(payload, data, copy_len);
	payload[copy_len] = '\0';
	if (strncmp(payload, "SW-", 3) == 0) {
		printk("%s\n", payload);
		return BT_GATT_ITER_CONTINUE;
	}
	printk("ANCHOR_CTRL[%d] notify: %s\n", idx, payload);
	return BT_GATT_ITER_CONTINUE;
}

static uint8_t anchor_read_cb(struct bt_conn *conn, uint8_t err,
			      struct bt_gatt_read_params *params,
			      const void *data, uint16_t length)
{
	ARG_UNUSED(conn);

	if (params != &anchor_read_params) {
		return BT_GATT_ITER_STOP;
	}

	if (err != 0U) {
		anchor_read_status = -EIO;
		anchor_read_inflight = false;
		k_sem_give(&anchor_read_sem);
		return BT_GATT_ITER_STOP;
	}

	if (data == NULL || length == 0U) {
		anchor_read_status = (anchor_read_length > 0U) ? 0 : -ENODATA;
		anchor_read_inflight = false;
		k_sem_give(&anchor_read_sem);
		return BT_GATT_ITER_STOP;
	}

	anchor_read_length = MIN((size_t)length, sizeof(anchor_read_buffer) - 1U);
	memcpy(anchor_read_buffer, data, anchor_read_length);
	anchor_read_buffer[anchor_read_length] = '\0';
	anchor_read_status = 0;
	anchor_read_inflight = false;
	k_sem_give(&anchor_read_sem);
	return BT_GATT_ITER_STOP;
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
	const char *one_shot = active_one_shot_command();
	static struct bt_uuid_128 anchor_state_uuid =
		BT_UUID_INIT_128(0xf1, 0xd3, 0x39, 0x5f, 0xd9, 0x2f, 0xbf, 0xb6,
				 0xe6, 0x4b, 0xe0, 0x84, 0x40, 0x8f, 0x2b, 0x2f);
	static struct bt_uuid_128 anchor_control_uuid =
		BT_UUID_INIT_128(0xf4, 0xd3, 0x39, 0x5f, 0xd9, 0x2f, 0xbf, 0xb6,
				 0xe6, 0x4b, 0xe0, 0x84, 0x40, 0x8f, 0x2b, 0x2f);
	static struct bt_uuid_128 anchor_result_uuid =
		BT_UUID_INIT_128(0xf5, 0xd3, 0x39, 0x5f, 0xd9, 0x2f, 0xbf, 0xb6,
				 0xe6, 0x4b, 0xe0, 0x84, 0x40, 0x8f, 0x2b, 0x2f);
	const struct bt_gatt_dm_attr *gatt_service = bt_gatt_dm_service_get(dm);
	const struct bt_gatt_dm_attr *gatt_chrc;
	const struct bt_gatt_dm_attr *gatt_desc;
	char svc_uuid_str[BT_UUID_STR_LEN];

	bt_uuid_to_str(gatt_service->uuid, svc_uuid_str, sizeof(svc_uuid_str));
	printk("DISC complete[%d]: link=%s discovered_service=%s uuid=%s\n",
	       idx,
	       link_type_label(peer->link_type),
	       svc_uuid_str,
	       peer->adv_uuid[0] != '\0' ? peer->adv_uuid : "-");
	peer->discovery_inflight = false;
	gatt_discovery_active = false;
	discovery_pump_schedule(0U);

	if (peer->link_type == MASTER_LINK_ANCHOR_CTRL) {
		printk("DISC anchor service found[%d]: uuid=%s\n",
		       idx,
		       peer->adv_uuid[0] != '\0' ? peer->adv_uuid : "-");

		gatt_chrc = bt_gatt_dm_char_by_uuid(dm, &anchor_state_uuid.uuid);
		if (gatt_chrc == NULL) {
			printk("Anchor ctrl state characteristic missing[%d]\n", idx);
			bt_gatt_dm_data_release(dm);
			return;
		}
		gatt_desc = bt_gatt_dm_desc_by_uuid(dm, gatt_chrc, &anchor_state_uuid.uuid);
		if (gatt_desc == NULL) {
			printk("Anchor ctrl state value missing[%d]\n", idx);
			bt_gatt_dm_data_release(dm);
			return;
		}
		peer->anchor_state_handle = gatt_desc->handle;
		gatt_desc = bt_gatt_dm_desc_by_uuid(dm, gatt_chrc, BT_UUID_GATT_CCC);
		if (gatt_desc != NULL) {
			peer->anchor_state_ccc_handle = gatt_desc->handle;
		}

		gatt_chrc = bt_gatt_dm_char_by_uuid(dm, &anchor_control_uuid.uuid);
		if (gatt_chrc == NULL) {
			printk("Anchor ctrl write characteristic missing[%d]\n", idx);
			bt_gatt_dm_data_release(dm);
			return;
		}
		gatt_desc = bt_gatt_dm_desc_by_uuid(dm, gatt_chrc, &anchor_control_uuid.uuid);
		if (gatt_desc == NULL) {
			printk("Anchor ctrl write value missing[%d]\n", idx);
			bt_gatt_dm_data_release(dm);
			return;
		}
		peer->anchor_ctrl_handle = gatt_desc->handle;

		gatt_chrc = bt_gatt_dm_char_by_uuid(dm, &anchor_result_uuid.uuid);
		if (gatt_chrc == NULL) {
			printk("Anchor ctrl result characteristic missing[%d]\n", idx);
			bt_gatt_dm_data_release(dm);
			return;
		}
		gatt_desc = bt_gatt_dm_desc_by_uuid(dm, gatt_chrc, &anchor_result_uuid.uuid);
		if (gatt_desc == NULL) {
			printk("Anchor ctrl result value missing[%d]\n", idx);
			bt_gatt_dm_data_release(dm);
			return;
		}
		peer->anchor_result_handle = gatt_desc->handle;
		gatt_desc = bt_gatt_dm_desc_by_uuid(dm, gatt_chrc, BT_UUID_GATT_CCC);
		if (gatt_desc != NULL) {
			peer->anchor_result_ccc_handle = gatt_desc->handle;
		}

		if (peer->anchor_state_ccc_handle != 0U) {
			peer->anchor_state_sub_params.notify = anchor_ctrl_notify_cb;
			peer->anchor_state_sub_params.value = BT_GATT_CCC_NOTIFY;
			peer->anchor_state_sub_params.value_handle = peer->anchor_state_handle;
			peer->anchor_state_sub_params.ccc_handle = peer->anchor_state_ccc_handle;
			atomic_set_bit(peer->anchor_state_sub_params.flags,
				       BT_GATT_SUBSCRIBE_FLAG_VOLATILE);
			err = bt_gatt_subscribe(peer->conn, &peer->anchor_state_sub_params);
			printk("Anchor ctrl state subscribe[%d] rc=%d\n", idx, err);
		}
		if (peer->anchor_result_ccc_handle != 0U) {
			peer->anchor_result_sub_params.notify = anchor_ctrl_notify_cb;
			peer->anchor_result_sub_params.value = BT_GATT_CCC_NOTIFY;
			peer->anchor_result_sub_params.value_handle = peer->anchor_result_handle;
			peer->anchor_result_sub_params.ccc_handle = peer->anchor_result_ccc_handle;
			atomic_set_bit(peer->anchor_result_sub_params.flags,
				       BT_GATT_SUBSCRIBE_FLAG_VOLATILE);
			err = bt_gatt_subscribe(peer->conn, &peer->anchor_result_sub_params);
			printk("Anchor ctrl result subscribe[%d] rc=%d\n", idx, err);
		}

		peer->ready = true;
		peer->discovery_retry_attempts = 0U;
		printk("ANCHOR_CTRL[%d] link ready handle=0x%04x result=0x%04x uuid=%s\n",
		       idx, peer->anchor_ctrl_handle, peer->anchor_result_handle,
		       peer->adv_uuid[0] != '\0' ? peer->adv_uuid : "-");
		master_leds_refresh();
		master_try_connect_pending();
		if (conn_count < MASTER_MAX_CONNECTIONS) {
			start_scan();
		}
		bt_gatt_dm_data_release(dm);
		return;
	}

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
	peer->discovery_inflight = false;
	peer->discovery_retry_attempts = 0U;
	printk("BLE[%d] link ready\n", idx);
	master_leds_refresh();

	if (one_shot != NULL && !peer->one_shot_sent) {
		if (!peer_matches_runtime_target(peer)) {
			printk("BLE one-shot skipped[%d]: target mismatch uuid=%s name=%s\n",
			       idx,
			       peer->adv_uuid[0] != '\0' ? peer->adv_uuid : "-",
			       peer->adv_name[0] != '\0' ? peer->adv_name : "-");
		} else {
			err = bt_nus_client_send(&peer->nus_client,
						 (const uint8_t *)one_shot,
						 strlen(one_shot));
			if (err) {
				printk("BLE one-shot send failed[%d]: %d\n", idx, err);
				led_error_state = true;
				master_leds_refresh();
			} else {
				peer->one_shot_sent = true;
				printk("BLE one-shot command sent[%d]: %s\n", idx, one_shot);
			}
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
		peer->discovery_inflight = false;
		gatt_discovery_active = false;
		discovery_pump_schedule(0U);
		return;
	}

	if (peer->discovery_retry_attempts >= MASTER_DISCOVERY_RETRY_LIMIT) {
		printk("%s discovery retry exhausted[%d]\n",
		       (peer->link_type == MASTER_LINK_ANCHOR_CTRL) ? "Anchor ctrl" : "NUS",
		       idx);
		peer->discovery_inflight = false;
		discovery_pump_schedule(0U);
		return;
	}

	if (gatt_discovery_active) {
		printk("%s discovery retry defer[%d]: global discovery busy\n",
		       (peer->link_type == MASTER_LINK_ANCHOR_CTRL) ? "Anchor ctrl" : "NUS",
		       idx);
		(void)k_work_schedule(&peer->discovery_retry_work, K_MSEC(100));
		return;
	}

	peer->discovery_retry_attempts++;
	peer->discovery_inflight = true;
	gatt_discovery_active = true;
	if (peer->link_type == MASTER_LINK_ANCHOR_CTRL) {
		static struct bt_uuid_128 anchor_svc_uuid =
			BT_UUID_INIT_128(0xf0, 0xd3, 0x39, 0x5f, 0xd9, 0x2f, 0xbf, 0xb6,
					 0xe6, 0x4b, 0xe0, 0x84, 0x40, 0x8f, 0x2b, 0x2f);
		err = bt_gatt_dm_start(peer->conn, &anchor_svc_uuid.uuid, &discovery_cb, peer);
	} else {
		err = bt_gatt_dm_start(peer->conn, BT_UUID_NUS_SERVICE, &discovery_cb, peer);
	}
	if (err) {
		peer->discovery_inflight = false;
		gatt_discovery_active = false;
		peer->discovery_start_failures++;
		printk("Could not start %s discovery[%d] retry %u: %d\n",
		       (peer->link_type == MASTER_LINK_ANCHOR_CTRL) ? "anchor-ctrl" : "NUS",
		       idx, peer->discovery_retry_attempts, err);
		printk("DISC retry failed[%d]: connected_for=%lldms ready=%u retries=%u start_failures=%u\n",
		       idx,
		       k_uptime_get() - peer->connected_at_ms,
		       peer->ready ? 1U : 0U,
		       peer->discovery_retry_attempts,
		       peer->discovery_start_failures);
		(void)k_work_schedule(&peer->discovery_retry_work,
				      K_MSEC(MASTER_DISCOVERY_RETRY_DELAY_MS));
		discovery_pump_schedule(0U);
	} else {
		printk("%s discovery retry armed[%d] attempt=%u\n",
		       (peer->link_type == MASTER_LINK_ANCHOR_CTRL) ? "Anchor ctrl" : "NUS",
		       idx, peer->discovery_retry_attempts);
	}
}

static void discovery_service_not_found(struct bt_conn *conn, void *context)
{
	struct master_peer *peer = context;
	int idx = peer_index_from_conn(conn);

	printk("%s service not found[%d]\n",
	       (peer != NULL && peer->link_type == MASTER_LINK_ANCHOR_CTRL) ?
		       "Anchor ctrl" : "NUS",
	       idx);
	if (peer != NULL) {
		peer->discovery_inflight = false;
		gatt_discovery_active = false;
		discovery_pump_schedule(0U);
	}

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
	if (peer != NULL) {
		peer->discovery_inflight = false;
		gatt_discovery_active = false;
		discovery_pump_schedule(0U);
	}

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
	static struct bt_uuid_128 anchor_svc_uuid =
		BT_UUID_INIT_128(0xf0, 0xd3, 0x39, 0x5f, 0xd9, 0x2f, 0xbf, 0xb6,
				 0xe6, 0x4b, 0xe0, 0x84, 0x40, 0x8f, 0x2b, 0x2f);
	const struct bt_uuid *svc_uuid = BT_UUID_NUS_SERVICE;
	int err;

	if (peer->link_type == MASTER_LINK_ANCHOR_CTRL) {
		svc_uuid = &anchor_svc_uuid.uuid;
	}
	if (gatt_discovery_active) {
		printk("DISC start defer[%d]: global discovery busy\n",
		       peer_index_from_conn(conn));
		peer->discovery_inflight = false;
		peer->setup_pending = true;
		discovery_pump_schedule(50U);
		return;
	}
	printk("DISC start[%d]: link=%s uuid=%s conn=%p\n",
	       peer_index_from_conn(conn),
	       link_type_label(peer->link_type),
	       peer->adv_uuid[0] != '\0' ? peer->adv_uuid : "-",
	       peer->conn);
	peer->discovery_inflight = true;
	gatt_discovery_active = true;

	err = bt_gatt_dm_start(conn, svc_uuid, &discovery_cb, peer);

	if (err) {
		peer->discovery_inflight = false;
		gatt_discovery_active = false;
		peer->discovery_start_failures++;
		printk("Could not start %s discovery[%d]: %d\n",
		       (peer->link_type == MASTER_LINK_ANCHOR_CTRL) ? "anchor-ctrl" : "NUS",
		       peer_index_from_conn(conn), err);
		printk("DISC start failed[%d]: connected_for=%lldms ready=%u retries=%u start_failures=%u\n",
		       peer_index_from_conn(conn),
		       k_uptime_get() - peer->connected_at_ms,
		       peer->ready ? 1U : 0U,
		       peer->discovery_retry_attempts,
		       peer->discovery_start_failures);
		if (peer->connected && !peer->ready) {
			(void)k_work_schedule(&peer->discovery_retry_work,
					      K_MSEC(MASTER_DISCOVERY_RETRY_DELAY_MS));
		}
		discovery_pump_schedule(0U);
	}
}

static void scan_recv(const struct bt_le_scan_recv_info *info, struct net_buf_simple *buf)
{
	int slot;
	bool name_match;
	bool anchor_name_match;
	bool nus_match;
	bool dfu_match;
	bool token_match;
	uint8_t tag_id = 0U;
	bool tag_id_valid;
	uint16_t bs_code = 0U;
	bool bs_code_valid;
	uint8_t anchor_id_cfg = 0U;
	bool anchor_id_valid;
	char uuid_hex[MASTER_UUID_HEX_LEN + 1U];
	bool uuid_match = false;
	struct net_buf_simple name_copy = *buf;
	char adv_name[MASTER_NAME_BUF_LEN];

	if (!recv_background_allowed()) {
		recv_bg_suppressed_count++;
		if ((recv_bg_suppressed_count % 32U) == 1U) {
			printk("RECV_BG suppressed: drop scan candidate while gate closed (count=%lu)\n",
			       (unsigned long)recv_bg_suppressed_count);
		}
		return;
	}

	if (!(info->adv_props & BT_GAP_ADV_PROP_CONNECTABLE)) {
		return;
	}

	name_match = ad_name_matches_tag_target(buf);
	anchor_name_match = ad_name_matches_anchor_target(buf);
	nus_match = ad_has_nus_uuid(buf);
	dfu_match = ad_has_dfu_smp_uuid(buf);
	token_match = ad_has_biospur_token(buf);
	tag_id_valid = ad_get_biospur_tag_id(buf, &tag_id);
	bs_code_valid = ad_get_biospur_bs_code(buf, &bs_code);
	anchor_id_valid = ad_get_biospur_anchor_id(buf, &anchor_id_cfg);
	if (!ad_extract_uuid_hex(buf, uuid_hex, sizeof(uuid_hex))) {
		uuid_hex[0] = '\0';
	}
	if (uuid_hex[0] != '\0' && anchor_id_valid) {
		snprintk(anchor_adv_uuid_map[anchor_id_cfg - 1U],
			 sizeof(anchor_adv_uuid_map[anchor_id_cfg - 1U]), "%s", uuid_hex);
	}
	uuid_match = (runtime_target_uuid[0] == '\0') ||
		     (uuid_hex[0] != '\0' && !strcasecmp(uuid_hex, runtime_target_uuid));
	memset(adv_name, 0, sizeof(adv_name));
	bt_data_parse(&name_copy, scan_name_cb, adv_name);

	if (runtime_target_kind == MASTER_TARGET_ANCHOR) {
		if (!runtime_anchor_wildcard_scan) {
			if (runtime_target_uuid[0] == '\0') {
				return;
			}
			if (!uuid_match) {
				return;
			}
		} else if (uuid_hex[0] == '\0') {
			return;
		}
		goto candidate_accept;
	}

	if (!bs_code_valid) {
		return;
	}

	if (runtime_target_kind == MASTER_TARGET_TAG &&
	    master_tdma_any_profile_defined() &&
	    !master_tdma_profile_has_bs_code(bs_code)) {
		char addr[BT_ADDR_LE_STR_LEN];
		char bs_name[8];

		bt_addr_le_to_str(info->addr, addr, sizeof(addr));
		snprintk(bs_name, sizeof(bs_name), "BS%04X", (unsigned int)bs_code);
		printk("RECV candidate rejected: %s bs=%s not in TDMA profile allow-list\n",
		       addr, bs_name);
		return;
	}

	/* RECV path must connect only to peers that can carry runtime data over NUS.
	 * Tag builds sometimes advertise identity only via legacy manufacturer data
	 * without exposing NUS UUID or a local-name field in the same packet.
	 * Accept those peers when the BioSpur tag/token markers are present.
	 * DFU-only advertisers (typical anchors in OTA-capable builds) still stay
	 * filtered out because they provide neither tag_id nor token markers.
	 */
	if (!(name_match || nus_match || token_match || tag_id_valid)) {
		if (dfu_match) {
			char addr[BT_ADDR_LE_STR_LEN];
			bt_addr_le_to_str(info->addr, addr, sizeof(addr));
			printk("%s candidate rejected: DFU-only peer %s (no NUS/name match)\n",
			       runtime_target_kind == MASTER_TARGET_ANCHOR ? "ANCHOR" : "RECV",
			       addr);
		}
		return;
	}

candidate_accept:
	if (peer_index_from_addr(info->addr) >= 0) {
		master_led_flow_pulse();
		return;
	}

	scan_log_candidate(info, buf, name_match, anchor_name_match, nus_match, dfu_match,
			  token_match, uuid_hex, uuid_match, bs_code, bs_code_valid);

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
	if (uuid_hex[0] != '\0') {
		strncpy(peers[slot].adv_uuid, uuid_hex, sizeof(peers[slot].adv_uuid) - 1U);
		peers[slot].adv_uuid[sizeof(peers[slot].adv_uuid) - 1U] = '\0';
	}
	peers[slot].tag_id = 0U;
	peers[slot].tag_id_valid = false;
	peers[slot].bs_code = bs_code;
	peers[slot].bs_code_valid = bs_code_valid;
	peers[slot].anchor_id_cfg = anchor_id_cfg;
	peers[slot].anchor_id_valid = anchor_id_valid;
	peers[slot].connect_pending = true;
	peers[slot].connect_queued_at_ms = k_uptime_get();
	peers[slot].link_type = (runtime_target_kind == MASTER_TARGET_ANCHOR) ?
				MASTER_LINK_ANCHOR_CTRL : MASTER_LINK_NUS;

	if (auto_connect_enabled) {
		char addr[BT_ADDR_LE_STR_LEN];

		bt_addr_le_to_str(info->addr, addr, sizeof(addr));
		printk("CONNECT queue[%d]: addr=%s link=%s uuid=%s target=%s\n",
		       slot,
		       addr,
		       link_type_label(peers[slot].link_type),
		       peers[slot].adv_uuid[0] != '\0' ? peers[slot].adv_uuid : "-",
		       runtime_target_kind_label(runtime_target_kind));
		/* Let the main loop drive connect pending. Calling into scan-stop/
		 * create-conn from the async work path has proven fragile and can
		 * leave the pending slot stuck without ever issuing bt_conn_le_create().
		 */
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

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	master_led_flow_pulse();

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
		(void)bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
		return;
	}

	if (!recv_background_allowed()) {
		printk("RECV_BG suppressed: disconnect unsolicited post-gate peer[%d]: %s\n",
		       idx, addr);
		(void)bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
		if (idx >= 0 && idx < (int)ARRAY_SIZE(peers)) {
			peer_clear((unsigned int)idx, true);
		}
		connecting_slot = -1;
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
	printk("CONNECT state[%d]: link=%s uuid=%s ready=%u conn=%p\n",
	       idx,
	       link_type_label(peers[idx].link_type),
	       peers[idx].adv_uuid[0] != '\0' ? peers[idx].adv_uuid : "-",
	       peers[idx].ready ? 1U : 0U,
	       peers[idx].conn);
	conn_count++;
	connecting_slot = -1;
	peers[idx].connected = true;
	peers[idx].connect_pending = false;
	peers[idx].setup_pending = true;
	peers[idx].discovery_inflight = false;
	peers[idx].connected_at_ms = k_uptime_get();
	master_leds_refresh();
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	char addr[BT_ADDR_LE_STR_LEN];
	int idx = peer_index_from_conn(conn);

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	master_led_flow_pulse();
	printk("Disconnected[%d]: %s reason=0x%02x link=%s ready=%u\n",
	       idx,
	       addr,
	       reason,
	       (idx >= 0) ? link_type_label(peers[idx].link_type) : "none",
	       (idx >= 0 && peers[idx].ready) ? 1U : 0U);

	if (idx >= 0) {
		peer_clear((unsigned int)idx, true);
		if (conn_count > 0U) {
			conn_count--;
		}
	} else {
		printk("Disconnected untracked peer: conn_count unchanged=%u\n",
		       (unsigned int)conn_count);
	}

	master_leds_refresh();
	master_rebalance_tdma_slots();
	if (disconnect_restart_suppress_count > 0U) {
		disconnect_restart_suppress_count--;
		printk("RECV_BG suppressed: quiesce disconnect restart blocked remaining=%u\n",
		       (unsigned int)disconnect_restart_suppress_count);
	} else if (recv_background_allowed()) {
		start_scan();
		master_try_connect_pending();
	} else {
		printk("RECV_BG suppressed: disconnect handler restart blocked\n");
	}
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
	k_work_init_delayable(&led_flow_off_work, led_flow_off_handler);
	master_leds_refresh();
	printk("LED map: 1=AUTOPOS 2=RECV 3=OTA 4=BLE-flow/error\n");
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
	printk("Bluetooth init ok: bt_ready=%u\n", bt_is_ready() ? 1U : 0U);

	if (IS_ENABLED(CONFIG_SETTINGS)) {
		err = settings_load();
		printk("Settings load rc=%d bt_ready=%u\n", err, bt_is_ready() ? 1U : 0U);
	}

	err = nus_client_init();
	if (err) {
		printk("NUS client init failed: %d\n", err);
		return err;
	}
	printk("NUS client init ok: bt_ready=%u\n", bt_is_ready() ? 1U : 0U);

	bt_conn_cb_register(&conn_callbacks);
	bt_le_scan_cb_register(&scan_callbacks);
	k_sem_init(&anchor_read_sem, 0, 1);
	k_work_init(&connect_pending_work, connect_pending_work_fn);
	k_work_init_delayable(&discovery_pump_work, discovery_pump_work_fn);

	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		k_work_init_delayable(&peers[i].discovery_retry_work, discovery_retry_work_fn);
	}

	printk("BioSpur BLE master ready on %s\n", CONFIG_BOARD_TARGET);
	printk("Max connections: %u\n", MASTER_MAX_CONNECTIONS);
	if (strlen(APP_MASTER_ONE_SHOT_CMD) != 0U) {
		printk("One-shot NUS command armed (build): %s\n", APP_MASTER_ONE_SHOT_CMD);
	}

	start_scan();
	master_try_connect_pending();

	while (1) {
		master_try_connect_pending();
		master_process_setup_pending();
		k_sleep(K_MSEC(50));
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
	if (recv_background_allowed()) {
		master_try_connect_pending();
	} else {
		printk("RECV_BG suppressed: connect/start deferred by gate\n");
	}
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

void master_disconnect_runtime_target_peers(void)
{
	uint8_t suppress_count = 0U;

	stop_scan();
	connecting_slot = -1;

	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (!peer_matches_runtime_target(&peers[i])) {
			continue;
		}

		if (peers[i].conn != NULL) {
			printk("Master disconnect target peer[%zu]: connected=%u ready=%u link=%s uuid=%s\n",
			       i,
			       peers[i].connected ? 1U : 0U,
			       peers[i].ready ? 1U : 0U,
			       link_type_label(peers[i].link_type),
			       peers[i].adv_uuid[0] != '\0' ? peers[i].adv_uuid : "-");
			if (peers[i].connected) {
				(void)bt_conn_disconnect(peers[i].conn,
							 BT_HCI_ERR_REMOTE_USER_TERM_CONN);
				if (suppress_count < UINT8_MAX) {
					suppress_count++;
				}
				peers[i].ready = false;
				peers[i].setup_pending = false;
				peers[i].discovery_inflight = false;
				peers[i].connect_pending = false;
				peers[i].one_shot_sent = false;
				continue;
			}

			peer_clear((unsigned int)i, true);
			continue;
		}

		if (peers[i].connect_pending || peers[i].setup_pending ||
		    peers[i].discovery_inflight || peers[i].addr_valid) {
			printk("Master clear target pending peer[%zu]: pending=%u setup=%u inflight=%u uuid=%s\n",
			       i,
			       peers[i].connect_pending ? 1U : 0U,
			       peers[i].setup_pending ? 1U : 0U,
			       peers[i].discovery_inflight ? 1U : 0U,
			       peers[i].adv_uuid[0] != '\0' ? peers[i].adv_uuid : "-");
			peer_clear((unsigned int)i, false);
		}
	}

	disconnect_restart_suppress_count = suppress_count;
	master_leds_refresh();
}

void master_quiesce_peers(void)
{
	uint8_t suppress_count = 0U;

	stop_scan();
	auto_connect_enabled = false;
	connecting_slot = -1;
	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].conn != NULL) {
			printk("Master quiesce peer[%zu]: connected=%u ready=%u link=%s uuid=%s\n",
			       i,
			       peers[i].connected ? 1U : 0U,
			       peers[i].ready ? 1U : 0U,
			       link_type_label(peers[i].link_type),
			       peers[i].adv_uuid[0] != '\0' ? peers[i].adv_uuid : "-");
			if (peers[i].connected) {
				(void)bt_conn_disconnect(peers[i].conn,
							 BT_HCI_ERR_REMOTE_USER_TERM_CONN);
				if (suppress_count < UINT8_MAX) {
					suppress_count++;
				}
				peers[i].ready = false;
				peers[i].setup_pending = false;
				peers[i].discovery_inflight = false;
				peers[i].connect_pending = false;
				peers[i].one_shot_sent = false;
				continue;
			}
			peer_clear((unsigned int)i, true);
			continue;
		}

		if (peers[i].connect_pending || peers[i].setup_pending ||
		    peers[i].discovery_inflight || peers[i].addr_valid) {
			printk("Master quiesce pending peer[%zu]: pending=%u setup=%u inflight=%u uuid=%s\n",
			       i,
			       peers[i].connect_pending ? 1U : 0U,
			       peers[i].setup_pending ? 1U : 0U,
			       peers[i].discovery_inflight ? 1U : 0U,
			       peers[i].adv_uuid[0] != '\0' ? peers[i].adv_uuid : "-");
			peer_clear((unsigned int)i, false);
		}
	}
	disconnect_restart_suppress_count = suppress_count;
	master_leds_refresh();
}

void master_stop_discovery(void)
{
	stop_scan();
}

void master_restart_discovery(void)
{
	if (!recv_background_allowed()) {
		printk("RECV_BG suppressed: restart discovery ignored by gate\n");
		return;
	}

	stop_scan();
	start_scan();
	master_try_connect_pending();
}

void master_set_log_mode(enum master_log_mode mode)
{
	runtime_log_mode = mode;
	master_leds_refresh();
}

enum master_log_mode master_get_log_mode(void)
{
	return runtime_log_mode;
}

void master_set_runtime_target_kind(enum master_runtime_target_kind kind)
{
	runtime_target_kind = kind;
}

void master_set_runtime_target_token(int token)
{
	runtime_target_token = token;
	ARG_UNUSED(runtime_target_token);
}

void master_set_runtime_target_name(const char *name)
{
	if (name == NULL || name[0] == '\0') {
		runtime_target_name[0] = '\0';
		return;
	}

	strncpy(runtime_target_name, name, sizeof(runtime_target_name) - 1U);
	runtime_target_name[sizeof(runtime_target_name) - 1U] = '\0';
}

void master_set_runtime_target_prefix(const char *prefix)
{
	if (prefix == NULL || prefix[0] == '\0') {
		runtime_target_prefix[0] = '\0';
		return;
	}

	strncpy(runtime_target_prefix, prefix, sizeof(runtime_target_prefix) - 1U);
	runtime_target_prefix[sizeof(runtime_target_prefix) - 1U] = '\0';
}

void master_set_runtime_target_uuid(const char *uuid_hex)
{
	if (uuid_hex == NULL || uuid_hex[0] == '\0') {
		runtime_target_uuid[0] = '\0';
		return;
	}

	strncpy(runtime_target_uuid, uuid_hex, sizeof(runtime_target_uuid) - 1U);
	runtime_target_uuid[sizeof(runtime_target_uuid) - 1U] = '\0';
}

void master_set_anchor_wildcard_scan(bool enable)
{
	runtime_anchor_wildcard_scan = enable;
}

int master_anchor_ctrl_ready_count(void)
{
	int count = 0;

	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (!peers[i].connected || !peers[i].ready || peers[i].conn == NULL) {
			continue;
		}
		if (peers[i].link_type != MASTER_LINK_ANCHOR_CTRL) {
			continue;
		}
		if (!peer_matches_runtime_target(&peers[i])) {
			continue;
		}
		count++;
	}

	return count;
}

int master_anchor_ctrl_target_peer_count(void)
{
	int count = 0;

	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (peers[i].link_type != MASTER_LINK_ANCHOR_CTRL) {
			continue;
		}
		if (!peer_matches_runtime_target(&peers[i])) {
			continue;
		}
		if (!peers[i].connected && !peers[i].connect_pending &&
		    !peers[i].setup_pending && !peers[i].discovery_inflight) {
			continue;
		}
		count++;
	}

	return count;
}

int master_connection_count(void)
{
	return (int)conn_count;
}

void master_dump_ready_state(void)
{
	printk("READY count=%d target=%s target_uuid=%s conn_count=%u connecting_slot=%d scan=%u auto=%u\n",
	       master_anchor_ctrl_ready_count(),
	       runtime_target_kind_label(runtime_target_kind),
	       runtime_target_uuid[0] != '\0' ? runtime_target_uuid : "-",
	       (unsigned int)conn_count,
	       connecting_slot,
	       scan_running ? 1U : 0U,
	       auto_connect_enabled ? 1U : 0U);

	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		if (!peers[i].addr_valid && peers[i].conn == NULL && !peers[i].connected &&
		    !peers[i].connect_pending && peers[i].adv_uuid[0] == '\0') {
			continue;
		}
		printk("READY peer[%zu]: link=%s connected=%u ready=%u pending=%u inflight=%u retries=%u start_failures=%u uuid=%s name=%s state_h=0x%04x ctrl_h=0x%04x result_h=0x%04x\n",
		       i,
		       link_type_label(peers[i].link_type),
		       peers[i].connected ? 1U : 0U,
		       peers[i].ready ? 1U : 0U,
		       peers[i].connect_pending ? 1U : 0U,
		       peers[i].discovery_inflight ? 1U : 0U,
		       peers[i].discovery_retry_attempts,
		       peers[i].discovery_start_failures,
		       peers[i].adv_uuid[0] != '\0' ? peers[i].adv_uuid : "-",
		       peers[i].adv_name[0] != '\0' ? peers[i].adv_name : "-",
		       peers[i].anchor_state_handle,
		       peers[i].anchor_ctrl_handle,
		       peers[i].anchor_result_handle);
	}
}

int master_anchor_snapshot_uuid_map(char labels_out[], size_t labels_len,
				      char uuids_out[][33], size_t uuids_len)
{
	int count = 0;

	if (labels_out == NULL || uuids_out == NULL || labels_len == 0U || uuids_len == 0U) {
		return -EINVAL;
	}

	for (size_t i = 0; i < labels_len; ++i) {
		labels_out[i] = '\0';
	}
	for (size_t i = 0; i < uuids_len; ++i) {
		uuids_out[i][0] = '\0';
	}

	for (size_t i = 0U; i < ARRAY_SIZE(anchor_adv_uuid_map) &&
	     (size_t)count < MIN(labels_len, uuids_len); ++i) {
		if (anchor_adv_uuid_map[i][0] == '\0') {
			continue;
		}
		labels_out[count] = (char)('A' + i);
		snprintk(uuids_out[count], 33U, "%s", anchor_adv_uuid_map[i]);
		count++;
	}

	for (size_t i = 0U; i < ARRAY_SIZE(peers) && (size_t)count < MIN(labels_len, uuids_len); ++i) {
		char label;

		if (peers[i].adv_uuid[0] == '\0') {
			continue;
		}

		if (peers[i].adv_name[0] != '\0' &&
		    strncasecmp(peers[i].adv_name, "ANCHOR-", 7U) == 0) {
			label = (char)toupper((unsigned char)peers[i].adv_name[7]);
		} else if (peers[i].anchor_id_valid) {
			label = (char)('A' + (peers[i].anchor_id_cfg - 1U));
		} else {
			continue;
		}
		if (label < 'A' || label > 'H') {
			continue;
		}

		for (int j = 0; j < count; ++j) {
			if (labels_out[j] == label) {
				goto next_peer;
			}
		}

		labels_out[count] = label;
		snprintk(uuids_out[count], 33U, "%s", peers[i].adv_uuid);
		count++;
next_peer:
		continue;
	}

	return count;
}

static int master_anchor_ctrl_read_text(uint16_t handle, char *out, size_t out_len)
{
	int idx;
	int err;

	if (out == NULL || out_len == 0U) {
		return -EINVAL;
	}

	idx = find_ready_anchor_peer_index();
	if (idx < 0) {
		return -ENOTCONN;
	}
	if (handle == 0U) {
		return -EINVAL;
	}
	if (anchor_read_inflight) {
		return -EBUSY;
	}

	while (k_sem_take(&anchor_read_sem, K_NO_WAIT) == 0) {
		/* drain */
	}

	memset(&anchor_read_params, 0, sizeof(anchor_read_params));
	anchor_read_params.func = anchor_read_cb;
	anchor_read_params.handle_count = 1U;
	anchor_read_params.single.handle = handle;
	anchor_read_params.single.offset = 0U;
	anchor_read_length = 0U;
	anchor_read_buffer[0] = '\0';
	anchor_read_status = -EAGAIN;
	anchor_read_inflight = true;

	err = bt_gatt_read(peers[idx].conn, &anchor_read_params);
	if (err) {
		anchor_read_inflight = false;
		return err;
	}

	if (k_sem_take(&anchor_read_sem, K_MSEC(1500)) != 0) {
		anchor_read_inflight = false;
		return -ETIMEDOUT;
	}
	if (anchor_read_status != 0) {
		return anchor_read_status;
	}

	strncpy(out, anchor_read_buffer, out_len - 1U);
	out[out_len - 1U] = '\0';
	return 0;
}

int master_anchor_ctrl_read_state(char *out, size_t out_len)
{
	int idx = find_ready_anchor_peer_index();

	if (idx < 0) {
		return -ENOTCONN;
	}
	return master_anchor_ctrl_read_text(peers[idx].anchor_state_handle, out, out_len);
}

int master_anchor_ctrl_read_result(char *out, size_t out_len)
{
	int idx = find_ready_anchor_peer_index();

	if (idx < 0) {
		return -ENOTCONN;
	}
	return master_anchor_ctrl_read_text(peers[idx].anchor_result_handle, out, out_len);
}

void master_set_background_gate(bool allow, const char *reason)
{
	if (recv_background_gate_open == allow) {
		return;
	}

	recv_background_gate_open = allow;
	printk("RECV_BG gate: %s reason=%s\n",
	       allow ? "ALLOW" : "SUPPRESS",
	       (reason != NULL && reason[0] != '\0') ? reason : "-");

	if (!allow) {
		stop_scan();
		return;
	}

	start_scan();
	master_try_connect_pending();
}

int master_send_command_now(const char *cmd)
{
	size_t cmd_len;
	int sent = 0;
	int considered = 0;

	if (cmd == NULL) {
		return -EINVAL;
	}

	while (*cmd == ' ' || *cmd == '\t') {
		cmd++;
	}
	if (*cmd == '\0') {
		return -EINVAL;
	}

	cmd_len = strlen(cmd);
	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		int err;
		bool target_match;

		if (!peers[i].connected || !peers[i].ready || peers[i].conn == NULL) {
			continue;
		}
		considered++;
		target_match = peer_matches_runtime_target(&peers[i]);
		if (!target_match) {
			char bs_name[MASTER_NAME_BUF_LEN];

			bs_name[0] = '\0';
			if (peers[i].bs_code_valid) {
				snprintk(bs_name, sizeof(bs_name), "BS%04X",
					 (unsigned int)peers[i].bs_code);
			}
			printk("BLE cmd skip[%zu]: target mismatch kind=%s target_name=%s target_uuid=%s peer_name=%s peer_bs=%s peer_uuid=%s link=%s ready=%u\n",
			       i,
			       runtime_target_kind_label(runtime_target_kind),
			       runtime_target_name[0] != '\0' ? runtime_target_name : "-",
			       runtime_target_uuid[0] != '\0' ? runtime_target_uuid : "-",
			       peers[i].adv_name[0] != '\0' ? peers[i].adv_name : "-",
			       bs_name[0] != '\0' ? bs_name : "-",
			       peers[i].adv_uuid[0] != '\0' ? peers[i].adv_uuid : "-",
			       link_type_label(peers[i].link_type),
			       peers[i].ready ? 1U : 0U);
			continue;
		}

		if (peers[i].link_type == MASTER_LINK_ANCHOR_CTRL) {
			err = bt_gatt_write_without_response(peers[i].conn,
							     peers[i].anchor_ctrl_handle,
							     cmd, cmd_len, false);
			if (err) {
				printk("Anchor ctrl send failed[%zu]: %d cmd=%s\n", i, err, cmd);
				continue;
			}
			sent++;
			printk("Anchor ctrl sent[%zu]: %s uuid=%s\n", i, cmd,
			       peers[i].adv_uuid[0] != '\0' ? peers[i].adv_uuid : "-");
			continue;
		}

		err = bt_nus_client_send(&peers[i].nus_client, (const uint8_t *)cmd, cmd_len);
		if (err) {
			printk("BLE cmd send failed[%zu]: %d cmd=%s\n", i, err, cmd);
			continue;
		}

		sent++;
		printk("BLE cmd sent[%zu]: %s\n", i, cmd);
	}

	if (sent == 0) {
		printk("BLE cmd not sent: considered=%d target_kind=%s target_name=%s target_uuid=%s target_prefix=%s\n",
		       considered,
		       runtime_target_kind_label(runtime_target_kind),
		       runtime_target_name[0] != '\0' ? runtime_target_name : "-",
		       runtime_target_uuid[0] != '\0' ? runtime_target_uuid : "-",
		       runtime_target_prefix[0] != '\0' ? runtime_target_prefix : "-");
	}

	return (sent > 0) ? sent : -ENOTCONN;
}

int master_set_one_shot_command(const char *cmd, bool send_now)
{
	size_t cmd_len;
	int send_rc = 0;

	if (cmd == NULL) {
		return -EINVAL;
	}

	while (*cmd == ' ' || *cmd == '\t') {
		cmd++;
	}
	if (*cmd == '\0') {
		return -EINVAL;
	}

	cmd_len = strlen(cmd);
	if (cmd_len >= sizeof(runtime_one_shot_cmd)) {
		return -EINVAL;
	}

	memcpy(runtime_one_shot_cmd, cmd, cmd_len + 1U);
	runtime_one_shot_cmd_set = true;
	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		peers[i].one_shot_sent = false;
	}
	printk("One-shot NUS command armed (runtime): %s\n", runtime_one_shot_cmd);

	if (send_now) {
		send_rc = master_send_command_now(runtime_one_shot_cmd);
	}

	return send_rc;
}

void master_clear_one_shot_command(void)
{
	runtime_one_shot_cmd[0] = '\0';
	runtime_one_shot_cmd_set = false;
	for (size_t i = 0U; i < ARRAY_SIZE(peers); ++i) {
		peers[i].one_shot_sent = false;
	}
	printk("One-shot NUS command cleared (runtime)\n");
}

void master_print_one_shot_command(void)
{
	if (runtime_one_shot_cmd_set && runtime_one_shot_cmd[0] != '\0') {
		printk("One-shot runtime cmd: %s\n", runtime_one_shot_cmd);
		return;
	}

	if (strlen(APP_MASTER_ONE_SHOT_CMD) != 0U) {
		printk("One-shot build cmd: %s\n", APP_MASTER_ONE_SHOT_CMD);
		return;
	}

	printk("One-shot cmd: <none>\n");
}

int master_tdma_set_profile(const char *bs_name, const char *profile)
{
	uint16_t bs_code;
	enum master_tdma_profile_kind kind;
	int free_idx = -1;

	if (!master_tdma_parse_bs_code(bs_name, &bs_code) ||
	    !master_tdma_parse_profile_kind(profile, &kind)) {
		return -EINVAL;
	}

	for (size_t i = 0U; i < ARRAY_SIZE(tdma_profiles); ++i) {
		if (tdma_profiles[i].valid && tdma_profiles[i].bs_code == bs_code) {
			tdma_profiles[i].kind = kind;
			printk("TDMA profile set: BS%04X -> %s\n",
			       (unsigned int)bs_code,
			       master_tdma_profile_label(kind));
			master_rebalance_tdma_slots();
			return 0;
		}
		if (!tdma_profiles[i].valid && free_idx < 0) {
			free_idx = (int)i;
		}
	}

	if (free_idx < 0) {
		return -ENOMEM;
	}

	tdma_profiles[free_idx].valid = true;
	tdma_profiles[free_idx].bs_code = bs_code;
	tdma_profiles[free_idx].kind = kind;
	printk("TDMA profile set: BS%04X -> %s\n",
	       (unsigned int)bs_code,
	       master_tdma_profile_label(kind));
	master_rebalance_tdma_slots();
	return 0;
}

int master_tdma_set_profile_freq(const char *profile, uint8_t hz)
{
	enum master_tdma_profile_kind kind;

	if (!master_tdma_parse_profile_kind(profile, &kind) || hz == 0U || hz > 50U) {
		return -EINVAL;
	}

	switch (kind) {
	case MASTER_TDMA_PROFILE_STATIC:
		tdma_static_target_hz = hz;
		break;
	case MASTER_TDMA_PROFILE_ROTO:
		tdma_roto_target_hz = hz;
		break;
	case MASTER_TDMA_PROFILE_MOTION:
	default:
		tdma_motion_target_hz = hz;
		break;
	}

	printk("TDMA freq set: %s=%uHz\n",
	       master_tdma_profile_label(kind),
	       (unsigned int)hz);
	master_rebalance_tdma_slots();
	return 0;
}

int master_tdma_rebalance_now(void)
{
	master_rebalance_tdma_slots();
	return 0;
}

int master_tdma_clear_profiles(void)
{
	for (size_t i = 0U; i < ARRAY_SIZE(tdma_profiles); ++i) {
		tdma_profiles[i].valid = false;
		tdma_profiles[i].bs_code = 0U;
		tdma_profiles[i].kind = MASTER_TDMA_PROFILE_MOTION;
	}

	printk("TDMA profiles cleared\n");
	master_rebalance_tdma_slots();
	return 0;
}

void master_tdma_print_status(void)
{
	printk("TDMA weighted scheduler: period=%ums active=%ums max_slots=%u freq motion=%uHz static=%uHz roto=%uHz\n",
	       MASTER_TDMA_SLOT_PERIOD_MS,
	       MASTER_TDMA_SLOT_ACTIVE_MS,
	       MASTER_TDMA_SLOT_COUNT_MAX,
	       (unsigned int)tdma_motion_target_hz,
	       (unsigned int)tdma_static_target_hz,
	       (unsigned int)tdma_roto_target_hz);
	for (size_t i = 0U; i < ARRAY_SIZE(tdma_profiles); ++i) {
		if (!tdma_profiles[i].valid) {
			continue;
		}
		printk("TDMA profile: BS%04X -> %s target=%uHz\n",
		       (unsigned int)tdma_profiles[i].bs_code,
		       master_tdma_profile_label(tdma_profiles[i].kind),
		       (unsigned int)master_tdma_profile_target_hz(tdma_profiles[i].kind));
	}
}
