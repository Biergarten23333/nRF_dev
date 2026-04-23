#include <errno.h>
#include <ctype.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>

#include <dk_buttons_and_leds.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#include <bluetooth/gatt_dm.h>
#include <bluetooth/scan.h>
#include <bluetooth/services/dfu_smp.h>
#include <bluetooth/services/nus.h>
#include <bluetooth/services/nus_client.h>

#include <zcbor_common.h>
#include <zcbor_decode.h>
#include <zcbor_encode.h>
#include <mgmt/mcumgr/util/zcbor_bulk.h>

#include "ota_image.inc"
#include "master_ota.h"

#define OTA_LED_SCAN DK_LED1
#define OTA_LED_LINK DK_LED2
#define OTA_LED_OTA DK_LED3
#define OTA_LED_ERROR DK_LED4

#ifndef APP_MASTER_OTA_CHUNK_SIZE
#define APP_MASTER_OTA_CHUNK_SIZE 448U
#endif
#define OTA_CHUNK_SIZE APP_MASTER_OTA_CHUNK_SIZE
#define OTA_CMD_TIMEOUT_SEC 30
#define OTA_SMP_GROUP_IMG 0x0001U
#define OTA_SMP_GROUP_OS 0x0000U
#define OTA_SMP_CMD_IMG_STATE 0x00U
#define OTA_SMP_CMD_IMG_UPLOAD 0x01U
#define OTA_SMP_CMD_IMG_ERASE 0x05U
#define OTA_SMP_CMD_OS_RESET 0x05U
#define OTA_MGMT_ERR_EBADSTATE 6
#define OTA_CBOR_DECODER_STATE_NUM 4U
#define OTA_NAME_BUF_LEN 32U
#define OTA_UUID_HEX_LEN 32U
#define OTA_DFU_READY_WATCHDOG_MS 2500U
#define OTA_DFU_READY_WATCHDOG_MAX_REDRIVE 1U
#define BLE_TS_MAGIC0 0x42U
#define BLE_TS_MAGIC1 0x50U
#define BLE_TS_VERSION 1U
#define BLE_TS_HEADER_LEN 5U
#define BLE_TS_RECORD_LEN 24U
#define BLE_CM_MAGIC0 0x43U
#define BLE_CM_MAGIC1 0x4dU
#define BLE_CM_VERSION 1U
#define BLE_CM_HEADER_LEN 5U
#define BLE_CM_RECORD_LEN 24U
#define BIOSPUR_MFG_ID_LSB 0xffU
#define BIOSPUR_MFG_ID_MSB 0xffU
#define BIOSPUR_MFG_MAGIC0 'B'
#define BIOSPUR_MFG_MAGIC1 'S'
#define BIOSPUR_MFG_MAGIC2 'A'
#define BIOSPUR_MFG_VERSION 0x01U
#define BIOSPUR_MFG_UUID_OFS 6U
#define BIOSPUR_MFG_UUID_LEN 16U
#define BIOSPUR_MFG_TOKEN_OFS 3U

#ifndef APP_MASTER_OTA_TARGET_NAME
#define APP_MASTER_OTA_TARGET_NAME ""
#endif

#ifndef APP_MASTER_OTA_TARGET_NAME_PREFIX
#define APP_MASTER_OTA_TARGET_NAME_PREFIX "BS"
#endif

#ifndef APP_MASTER_OTA_TARGET_TOKEN_ID
#define APP_MASTER_OTA_TARGET_TOKEN_ID -1
#endif

#ifndef APP_MASTER_OTA_UPLOAD_ENABLE
#define APP_MASTER_OTA_UPLOAD_ENABLE 1
#endif

#ifndef APP_MASTER_OTA_DIAG_FIRST_GATE_WRITE_REQ
#define APP_MASTER_OTA_DIAG_FIRST_GATE_WRITE_REQ 1
#endif

#define OTA_DEBUG_VERBOSE 0
#if OTA_DEBUG_VERBOSE
#define OTA_VLOG(...) printk(__VA_ARGS__)
#else
#define OTA_VLOG(...) do { } while (0)
#endif

struct smp_packet {
	struct bt_dfu_smp_header header;
	uint8_t payload[512];
};

struct ota_cmd_result {
	int status;
	size_t off;
	bool off_found;
};

enum discovery_phase {
	DISCOVERY_PHASE_NUS = 0,
	DISCOVERY_PHASE_DFU,
};

enum ota_op_state {
	OTA_OP_IDLE = 0,
	OTA_OP_TARGET_CONFIGURED,
	OTA_OP_TARGET_REJECTED,
	OTA_OP_CONNECT_PENDING,
	OTA_OP_CONNECT_FAILED,
	OTA_OP_CONNECTED_VERIFIED,
	OTA_OP_BLOCKED,
	OTA_OP_UPLOADING,
	OTA_OP_UPLOAD_COMPLETE,
	OTA_OP_REBOOT_PENDING,
	OTA_OP_VERIFY_PASSED,
	OTA_OP_VERIFY_FAILED,
};

static struct bt_conn *default_conn;
static struct bt_dfu_smp dfu_smp;
static struct bt_nus_client nus_client;
static struct k_sem ota_sem;
static struct k_sem ota_start_sem;
static struct k_sem nus_write_sem;
static struct k_sem sec_ready_sem;
static struct k_sem smp_sub_sem;
static struct k_sem smp_write_sem;
static bool leds_ready;
static const struct bt_conn_le_phy_param *const fast_phy_params =
	BT_CONN_LE_PHY_PARAM_2M;
static const struct bt_le_conn_param *const fast_conn_params =
	BT_LE_CONN_PARAM(6, 6, 0, 400);
static bool led_scan_state;
static bool led_link_state;
static bool led_ota_state;
static bool led_error_state;
static bool led_flow_state;
static struct k_work_delayable led_flow_off_work;
static bool ota_ready;
static bool ota_started;
static bool ota_done;
static bool ota_armed;
static bool ota_start_queued;
static bool mtu_ready;
static bool sec_ready;
static bool ota_security_required;
static bool nus_ready;
static int ota_status;
static uint8_t ota_seq;
static enum discovery_phase discovery_phase;
static struct bt_gatt_exchange_params exchange_params;
static uint8_t smp_rsp_buf[512];
static size_t smp_rsp_len;
static size_t smp_rsp_total;
static int runtime_target_token = APP_MASTER_OTA_TARGET_TOKEN_ID;
static char runtime_target_name[OTA_NAME_BUF_LEN] = APP_MASTER_OTA_TARGET_NAME;
static char runtime_target_prefix[OTA_NAME_BUF_LEN] = APP_MASTER_OTA_TARGET_NAME_PREFIX;
static char runtime_target_uuid[OTA_UUID_HEX_LEN + 1U];
static enum ota_op_state ota_op_state_now = OTA_OP_IDLE;
static bool selected_identity_verified;
static char selected_addr[BT_ADDR_LE_STR_LEN];
static char selected_uuid[OTA_UUID_HEX_LEN + 1U];
static char selected_name[OTA_NAME_BUF_LEN];
static int selected_token = -1;
static bool runtime_expect_nus = true;
static bool ota_session_active;
static bool ota_runtime_active;
static bool ota_upload_gate_ok;
static bool smp_notify_subscribed;
static int smp_subscribe_err;
static int smp_write_err;
static struct bt_gatt_write_params smp_write_params;
static uint16_t smp_inflight_group;
static uint8_t smp_inflight_cmd;
static uint8_t smp_inflight_seq;
static uint32_t smp_inflight_t0_ms;
static struct k_work_delayable dfu_ready_watchdog_work;
static bool dfu_ready_watchdog_armed;
static uint8_t dfu_ready_watchdog_redrive_count;
K_THREAD_STACK_DEFINE(ota_thread_stack, 3072);
static struct k_thread ota_thread;

static void ota_dfu_ready_watchdog_arm(void);
static void ota_dfu_ready_watchdog_cancel(const char *reason);
static void ota_dfu_ready_watchdog_fn(struct k_work *work);

struct scan_target_eval {
	bool has_name;
	bool has_token;
	bool has_uuid;
	bool name_match;
	bool token_match;
	bool uuid_match;
	bool strict_identity_ok;
};

static const char *ota_op_state_name(enum ota_op_state s)
{
	switch (s) {
	case OTA_OP_IDLE: return "idle";
	case OTA_OP_TARGET_CONFIGURED: return "target_configured";
	case OTA_OP_TARGET_REJECTED: return "target_rejected";
	case OTA_OP_CONNECT_PENDING: return "connect_pending";
	case OTA_OP_CONNECT_FAILED: return "connect_failed";
	case OTA_OP_CONNECTED_VERIFIED: return "connected_verified";
	case OTA_OP_BLOCKED: return "ota_blocked";
	case OTA_OP_UPLOADING: return "ota_uploading";
	case OTA_OP_UPLOAD_COMPLETE: return "ota_upload_complete";
	case OTA_OP_REBOOT_PENDING: return "reboot_pending";
	case OTA_OP_VERIFY_PASSED: return "post_verify_passed";
	case OTA_OP_VERIFY_FAILED: return "post_verify_failed";
	default: return "unknown";
	}
}

static void ota_set_state(enum ota_op_state s, const char *detail)
{
	if (ota_op_state_now == s) {
		return;
	}
	ota_op_state_now = s;
	printk("OTA_STATE:%s detail=%s\n", ota_op_state_name(s), detail != NULL ? detail : "-");
}

static void ota_session_set(bool active, const char *reason)
{
	if (ota_session_active == active) {
		return;
	}

	ota_session_active = active;
	if (active) {
		printk("OTA session acquired: reason=%s\n", reason != NULL ? reason : "-");
	} else {
		ota_dfu_ready_watchdog_cancel("session_release");
		printk("OTA session released: reason=%s\n", reason != NULL ? reason : "-");
	}
}

static void ota_dfu_ready_watchdog_cancel(const char *reason)
{
	if (!dfu_ready_watchdog_armed) {
		return;
	}

	(void)k_work_cancel_delayable(&dfu_ready_watchdog_work);
	dfu_ready_watchdog_armed = false;
	printk("OTA watchdog cancel: reason=%s\n", reason != NULL ? reason : "-");
}

static void ota_dfu_ready_watchdog_arm(void)
{
	if (!ota_session_active || ota_ready || ota_started || ota_done ||
	    default_conn == NULL || !selected_identity_verified) {
		return;
	}

	(void)k_work_reschedule(&dfu_ready_watchdog_work, K_MSEC(OTA_DFU_READY_WATCHDOG_MS));
	dfu_ready_watchdog_armed = true;
	printk("OTA watchdog armed: wait_dfu_ready timeout=%u ms uuid=%s conn=%p\n",
	       (unsigned int)OTA_DFU_READY_WATCHDOG_MS,
	       selected_uuid[0] != '\0' ? selected_uuid : "-",
	       default_conn);
}

static void ota_dfu_ready_watchdog_fn(struct k_work *work)
{
	int err;

	ARG_UNUSED(work);
	dfu_ready_watchdog_armed = false;

	if (!ota_session_active || ota_ready || ota_started || ota_done ||
	    default_conn == NULL || !selected_identity_verified ||
	    ota_op_state_now != OTA_OP_CONNECTED_VERIFIED) {
		return;
	}

	if (dfu_ready_watchdog_redrive_count >= OTA_DFU_READY_WATCHDOG_MAX_REDRIVE) {
		printk("OTA watchdog exhausted: no more re-drive attempts\n");
		return;
	}

	dfu_ready_watchdog_redrive_count++;
	printk("OTA watchdog trigger: dfu_ready timeout, re-drive #%u uuid=%s conn=%p\n",
	       (unsigned int)dfu_ready_watchdog_redrive_count,
	       selected_uuid[0] != '\0' ? selected_uuid : "-",
	       default_conn);
	ota_set_state(OTA_OP_CONNECT_PENDING, "watchdog_redrive");

	err = bt_conn_disconnect(default_conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
	if (err && err != -EALREADY && err != -ENOTCONN) {
		printk("OTA watchdog disconnect failed: %d\n", err);
		return;
	}

	if (err == -ENOTCONN) {
		if (default_conn != NULL) {
			bt_conn_unref(default_conn);
			default_conn = NULL;
		}
		(void)bt_scan_stop();
		err = bt_scan_start(BT_SCAN_TYPE_SCAN_PASSIVE);
		if (err && err != -EALREADY) {
			printk("OTA watchdog scan restart failed: %d\n", err);
			return;
		}
		printk("OTA watchdog fallback: passive scan restarted\n");
	}
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

static const char *cm_status_label(uint8_t code)
{
	switch (code) {
	case 0:
		return "ok";
	case 1:
		return "reject";
	case 2:
		return "timeout";
	case 3:
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

static bool ble_decode_ts_packet(const uint8_t *data, uint16_t len,
				 char *payload, size_t payload_len)
{
	uint8_t count;
	uint8_t version;
	size_t offset;
	size_t used = 0U;

	if (data == NULL || payload == NULL || payload_len == 0U ||
	    len < BLE_TS_HEADER_LEN) {
		return false;
	}

	if (data[0] != BLE_TS_MAGIC0 || data[1] != BLE_TS_MAGIC1) {
		return false;
	}

	version = data[2];
	count = data[3];
	if (version != BLE_TS_VERSION) {
		if (data[3] == BLE_TS_VERSION && data[2] > 0U) {
			version = data[3];
			count = data[2];
		} else {
			return false;
		}
	}

	offset = BLE_TS_HEADER_LEN;
	if (count == 0U || len < offset + (size_t)count * BLE_TS_RECORD_LEN) {
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
			"%sTS s=%u p=%s xyz=%d,%d,%d r=%u m=%u a=%s",
			(i == 0U) ? "" : "|",
			(unsigned int)sweep,
			sample_plan_label(plan_code),
			(int)x, (int)y, (int)z,
			(unsigned int)rms,
			(unsigned int)max,
			anchors);
		if (written < 0 || (size_t)written >= payload_len - used) {
			return false;
		}
		used += (size_t)written;
		if (motion_dt != 0U) {
			written = snprintk(&payload[used], payload_len - used,
					   " d=%u", (unsigned int)motion_dt);
		} else {
			written = snprintk(&payload[used], payload_len - used,
					   " motion=na");
		}
		if (written < 0 || (size_t)written >= payload_len - used) {
			return false;
		}
		used += (size_t)written;
		offset += BLE_TS_RECORD_LEN;
	}

	return true;
}

static bool ble_decode_cm_packet(const uint8_t *data, uint16_t len,
				 char *payload, size_t payload_len)
{
	uint8_t version;
	uint8_t count;
	size_t offset;
	size_t used = 0U;

	if (data == NULL || payload == NULL || payload_len == 0U ||
	    len < BLE_CM_HEADER_LEN) {
		return false;
	}

	if (data[0] != BLE_CM_MAGIC0 || data[1] != BLE_CM_MAGIC1) {
		return false;
	}

	version = data[2];
	count = data[3];
	if (version != BLE_CM_VERSION) {
		return false;
	}

	offset = BLE_CM_HEADER_LEN;
	if (count == 0U || len < offset + (size_t)count * BLE_CM_RECORD_LEN) {
		return false;
	}

	payload[0] = '\0';
	for (uint8_t i = 0U; i < count; ++i) {
		uint32_t sweep = sys_get_le32(&data[offset]);
		uint8_t anchor_id = data[offset + 4U];
		uint8_t status = data[offset + 5U];
		uint8_t quality = data[offset + 6U];
		int32_t raw_mm = (int32_t)sys_get_le32(&data[offset + 8U]);
		uint32_t filt_mm = sys_get_le32(&data[offset + 12U]);
		uint32_t ok_count = sys_get_le32(&data[offset + 16U]);
		uint32_t fail_count = sys_get_le32(&data[offset + 20U]);
		int written;

		written = snprintk(
			&payload[used], payload_len - used,
			"%sCM;1;%u;%u;%s;%d;%u;%u;%u;%u",
			(i == 0U) ? "" : "|",
			(unsigned int)sweep,
			(unsigned int)anchor_id,
			cm_status_label(status),
			(int)raw_mm,
			(unsigned int)filt_mm,
			(unsigned int)quality,
			(unsigned int)ok_count,
			(unsigned int)fail_count);
		if (written < 0 || (size_t)written >= payload_len - used) {
			return false;
		}
		used += (size_t)written;
		offset += BLE_CM_RECORD_LEN;
	}

	return true;
}

static void gatt_discover_nus(struct bt_conn *conn);
static void gatt_discover_dfu(struct bt_conn *conn);

void master_ota_target_reset(void)
{
	runtime_target_token = APP_MASTER_OTA_TARGET_TOKEN_ID;
	(void)snprintf(runtime_target_name, sizeof(runtime_target_name), "%s",
		       APP_MASTER_OTA_TARGET_NAME);
	(void)snprintf(runtime_target_prefix, sizeof(runtime_target_prefix), "%s",
		       APP_MASTER_OTA_TARGET_NAME_PREFIX);
	runtime_target_uuid[0] = '\0';
	selected_identity_verified = false;
	selected_addr[0] = '\0';
	selected_uuid[0] = '\0';
	selected_name[0] = '\0';
	selected_token = -1;
	ota_set_state(OTA_OP_IDLE, "target_reset");
}

int master_ota_target_set_token(int token_id)
{
	if (token_id < -1 || token_id > 255) {
		return -EINVAL;
	}

	runtime_target_token = token_id;
	ota_set_state(OTA_OP_TARGET_CONFIGURED, "token");
	return 0;
}

int master_ota_target_set_name(const char *name)
{
	if (name == NULL) {
		return -EINVAL;
	}

	(void)snprintf(runtime_target_name, sizeof(runtime_target_name), "%s", name);
	ota_set_state(OTA_OP_TARGET_CONFIGURED, "name");
	return 0;
}

int master_ota_target_set_prefix(const char *prefix)
{
	if (prefix == NULL) {
		return -EINVAL;
	}

	(void)snprintf(runtime_target_prefix, sizeof(runtime_target_prefix), "%s", prefix);
	ota_set_state(OTA_OP_TARGET_CONFIGURED, "prefix");
	return 0;
}

int master_ota_target_set_uuid(const char *uuid_hex)
{
	size_t n;

	if (uuid_hex == NULL) {
		return -EINVAL;
	}

	n = strlen(uuid_hex);
	if (n == 0U) {
		runtime_target_uuid[0] = '\0';
		ota_set_state(OTA_OP_TARGET_CONFIGURED, "uuid_clear");
		return 0;
	}

	if (n != OTA_UUID_HEX_LEN) {
		return -EINVAL;
	}

	for (size_t i = 0; i < OTA_UUID_HEX_LEN; ++i) {
		if (!isxdigit((unsigned char)uuid_hex[i])) {
			return -EINVAL;
		}
		runtime_target_uuid[i] = (char)toupper((unsigned char)uuid_hex[i]);
	}
	runtime_target_uuid[OTA_UUID_HEX_LEN] = '\0';
	ota_set_state(OTA_OP_TARGET_CONFIGURED, "uuid_set");
	return 0;
}

void master_ota_target_print(void)
{
	printk("OTA target filter: token=%d name=%s prefix=%s uuid=%s\n",
	       runtime_target_token,
	       runtime_target_name[0] != '\0' ? runtime_target_name : "-",
	       runtime_target_prefix[0] != '\0' ? runtime_target_prefix : "-",
	       runtime_target_uuid[0] != '\0' ? runtime_target_uuid : "-");
}

static bool scan_name_cb(struct bt_data *data, void *user_data)
{
	char *name_buf = user_data;
	size_t name_len;

	switch (data->type) {
	case BT_DATA_NAME_SHORTENED:
	case BT_DATA_NAME_COMPLETE:
		name_len = MIN(data->data_len, OTA_NAME_BUF_LEN - 1U);
		memcpy(name_buf, data->data, name_len);
		name_buf[name_len] = '\0';
		return false;
	default:
		return true;
	}
}

static void ad_extract_name(struct net_buf_simple *ad, char *name_buf, size_t len)
{
	struct net_buf_simple copy = *ad;

	if (len == 0U) {
		return;
	}

	memset(name_buf, 0, len);
	bt_data_parse(&copy, scan_name_cb, name_buf);
}

static bool str_case_eq(const char *a, const char *b)
{
	size_t i = 0U;

	if (a == NULL || b == NULL) {
		return false;
	}

	for (; a[i] != '\0' && b[i] != '\0'; ++i) {
		if (tolower((unsigned char)a[i]) != tolower((unsigned char)b[i])) {
			return false;
		}
	}
	return a[i] == '\0' && b[i] == '\0';
}

static bool str_case_startswith(const char *s, const char *prefix)
{
	size_t i = 0U;

	if (s == NULL || prefix == NULL) {
		return false;
	}

	for (; prefix[i] != '\0'; ++i) {
		if (s[i] == '\0') {
			return false;
		}
		if (tolower((unsigned char)s[i]) != tolower((unsigned char)prefix[i])) {
			return false;
		}
	}
	return true;
}

static bool scan_mfg_token_cb(struct bt_data *data, void *user_data)
{
	uint8_t *token_id = user_data;

	if (*token_id != 0xffU) {
		return false;
	}

	if (data->type != BT_DATA_MANUFACTURER_DATA || data->data_len < 4U) {
		return true;
	}

	if (data->data[0] == BIOSPUR_MFG_ID_LSB &&
	    data->data[1] == BIOSPUR_MFG_ID_MSB &&
	    data->data[2] == BIOSPUR_MFG_MAGIC0) {
		*token_id = data->data[BIOSPUR_MFG_TOKEN_OFS];
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

	if (data->data[0] != BIOSPUR_MFG_ID_LSB ||
	    data->data[1] != BIOSPUR_MFG_ID_MSB ||
	    data->data[2] != BIOSPUR_MFG_MAGIC0 ||
	    data->data[3] != BIOSPUR_MFG_MAGIC1 ||
	    data->data[4] != BIOSPUR_MFG_MAGIC2 ||
	    data->data[5] != BIOSPUR_MFG_VERSION) {
		return true;
	}

	for (size_t i = 0U; i < BIOSPUR_MFG_UUID_LEN; ++i) {
		(void)snprintk(&uuid_hex[i * 2U], 3, "%02X",
			       data->data[BIOSPUR_MFG_UUID_OFS + i]);
	}
	uuid_hex[OTA_UUID_HEX_LEN] = '\0';
	return false;
}

static bool scan_mfg_bs_code_cb(struct bt_data *data, void *user_data)
{
	uint16_t *bs_code = user_data;

	if (data->type != BT_DATA_MANUFACTURER_DATA || data->data_len < 6U) {
		return true;
	}

	if (data->data[0] == BIOSPUR_MFG_ID_LSB &&
	    data->data[1] == BIOSPUR_MFG_ID_MSB &&
	    data->data[2] == BIOSPUR_MFG_MAGIC0 &&
	    (data->data_len < 5U || data->data[3] != BIOSPUR_MFG_MAGIC1)) {
		*bs_code = sys_get_le16(&data->data[4]);
		return false;
	}

	return true;
}

static uint8_t ad_extract_token_id(struct net_buf_simple *ad)
{
	struct net_buf_simple copy = *ad;
	uint8_t token_id = 0xffU;

	bt_data_parse(&copy, scan_mfg_token_cb, &token_id);
	return token_id;
}

static bool ad_extract_bs_code(struct net_buf_simple *ad, uint16_t *bs_code)
{
	struct net_buf_simple copy = *ad;
	uint16_t parsed = 0xFFFFU;

	if (bs_code == NULL) {
		return false;
	}

	bt_data_parse(&copy, scan_mfg_bs_code_cb, &parsed);
	if (parsed == 0xFFFFU) {
		return false;
	}

	*bs_code = parsed;
	return true;
}

static bool ad_extract_uuid_hex(struct net_buf_simple *ad, char *uuid_hex, size_t uuid_hex_len)
{
	struct net_buf_simple copy = *ad;

	if (uuid_hex == NULL || uuid_hex_len < OTA_UUID_HEX_LEN + 1U) {
		return false;
	}

	uuid_hex[0] = '\0';
	bt_data_parse(&copy, scan_mfg_uuid_cb, uuid_hex);
	return uuid_hex[0] != '\0';
}

static bool ad_eval_target(struct net_buf_simple *ad, const char *name, uint8_t token_id,
			   const char *uuid_hex, struct scan_target_eval *eval)
{
	bool uuid_mode;

	memset(eval, 0, sizeof(*eval));
	eval->has_name = (name != NULL && name[0] != '\0');
	eval->has_token = (token_id != 0xffU);
	eval->has_uuid = (uuid_hex != NULL && uuid_hex[0] != '\0');
	uuid_mode = runtime_target_uuid[0] != '\0';

	if (runtime_target_token >= 0) {
		eval->token_match = eval->has_token &&
				    token_id == (uint8_t)runtime_target_token;
	} else {
		eval->token_match = true;
	}

	if (uuid_mode) {
		/*
		 * UUID-authoritative mode:
		 * - UUID match is mandatory.
		 * - Name is optional unless explicit exact name is configured.
		 * - Prefix is advisory only and not used as a hard gate.
		 */
		if (runtime_target_name[0] != '\0') {
			eval->name_match = eval->has_name && str_case_eq(name, runtime_target_name);
		} else {
			eval->name_match = true;
		}
		eval->uuid_match = eval->has_uuid &&
				   str_case_eq(uuid_hex, runtime_target_uuid);
	} else {
		if (runtime_target_name[0] != '\0') {
			eval->name_match = eval->has_name &&
					   str_case_eq(name, runtime_target_name);
		} else if (runtime_target_prefix[0] != '\0') {
			eval->name_match = eval->has_name &&
					   str_case_startswith(name, runtime_target_prefix);
		} else {
			eval->name_match = true;
		}
		eval->uuid_match = false;
	}

	/*
	 * Hard safety rule for destructive OTA: stable identity is mandatory.
	 * UUID is authoritative when configured. For tag OTA over NUS, token is
	 * the stable identity when UUID is absent from advertisements.
	 */
	if (uuid_mode) {
		eval->strict_identity_ok = eval->uuid_match;
	} else if (runtime_expect_nus && runtime_target_token >= 0) {
		eval->strict_identity_ok = eval->token_match;
	} else {
		eval->strict_identity_ok = eval->name_match && eval->token_match;
	}
	ARG_UNUSED(ad);
	return eval->strict_identity_ok && eval->name_match;
}

static void master_leds_apply(void)
{
	if (!leds_ready) {
		return;
	}

	(void)dk_set_led(OTA_LED_SCAN, led_scan_state);
	(void)dk_set_led(OTA_LED_LINK, led_link_state);
	(void)dk_set_led(OTA_LED_OTA, led_ota_state);
	(void)dk_set_led(OTA_LED_ERROR, led_error_state || led_flow_state);
}

static void led_flow_off_handler(struct k_work *work)
{
	ARG_UNUSED(work);

	led_flow_state = false;
	master_leds_apply();
}

static void master_ble_activity_pulse(void)
{
	if (!leds_ready) {
		return;
	}

	led_flow_state = true;
	master_leds_apply();
	(void)k_work_reschedule(&led_flow_off_work, K_MSEC(120));
}

static void master_leds_set(bool scan, bool link, bool ota, bool error)
{
	led_scan_state = scan;
	led_link_state = link;
	led_ota_state = ota;
	led_error_state = error;
	master_leds_apply();
}

static void nus_data_sent(struct bt_nus_client *nus, uint8_t err,
			  const uint8_t *const data, uint16_t len)
{
	ARG_UNUSED(nus);
	ARG_UNUSED(data);
	ARG_UNUSED(len);

	if (err) {
		printk("NUS write error: 0x%02x\n", err);
	}

	master_ble_activity_pulse();
	k_sem_give(&nus_write_sem);
}

static uint8_t nus_data_received(struct bt_nus_client *nus,
				 const uint8_t *data, uint16_t len)
{
	ARG_UNUSED(nus);
	char payload[1024];
	size_t copy_len;

	if (!ble_decode_cm_packet(data, len, payload, sizeof(payload)) &&
	    !ble_decode_ts_packet(data, len, payload, sizeof(payload))) {
		copy_len = MIN((size_t)len, sizeof(payload) - 1U);
		for (size_t i = 0; i < copy_len; ++i) {
			char c = (char)data[i];
			payload[i] = (c >= 32 && c <= 126) ? c : '.';
		}
		payload[copy_len] = '\0';
	}

	printk("NUS notify: %s\n", payload);
	master_ble_activity_pulse();

	return BT_GATT_ITER_CONTINUE;
}

static int nus_client_init(void)
{
	struct bt_nus_client_init_param init = {
		.cb = {
			.received = nus_data_received,
			.sent = nus_data_sent,
		},
	};

	return bt_nus_client_init(&nus_client, &init);
}

static int ota_send_nus_cmd(const char *cmd)
{
	int err;

	if (!nus_ready || default_conn == NULL) {
		return -ENOTCONN;
	}

	err = bt_nus_client_send(&nus_client, (const uint8_t *)cmd, strlen(cmd));
	if (err) {
		return err;
	}

	(void)k_sem_take(&nus_write_sem, K_MSEC(500));
	return 0;
}

static int ota_arm_target_via_nus(void)
{
	int err;

	if (!nus_ready) {
		return -ENOTSUP;
	}

	printk("OTA arm via NUS: OTA_PREPARE\n");
	err = ota_send_nus_cmd("OTA_PREPARE\n");
	if (err) {
		return err;
	}

	k_sleep(K_MSEC(300));

	printk("OTA arm via NUS: OTA_BEGIN\n");
	err = ota_send_nus_cmd("OTA_BEGIN\n");
	if (err) {
		return err;
	}

	k_sleep(K_MSEC(500));
	return 0;
}

void master_ota_set_expect_nus(bool expect_nus)
{
	runtime_expect_nus = expect_nus;
	printk("OTA NUS stage: %s\n", runtime_expect_nus ? "enabled" : "disabled");
}

static uint8_t ota_smp_notify_cb(struct bt_conn *conn,
				 struct bt_gatt_subscribe_params *params,
				 const void *data, uint16_t length)
{
	const struct bt_dfu_smp_header *header;
	size_t copy_len;

	ARG_UNUSED(params);

	if (conn != default_conn) {
		printk("OTA rsp ignored: conn mismatch got=%p expect=%p len=%u\n",
		       conn, default_conn, (unsigned int)length);
		return BT_GATT_ITER_CONTINUE;
	}

	if (data == NULL) {
		smp_notify_subscribed = false;
		return BT_GATT_ITER_STOP;
	}

	if (length == 0U) {
		return BT_GATT_ITER_CONTINUE;
	}

	if (smp_rsp_len == 0U && length >= sizeof(struct bt_dfu_smp_header)) {
		header = (const struct bt_dfu_smp_header *)data;
		smp_rsp_total = ((((uint16_t)header->len_h8) << 8) | header->len_l8) +
				sizeof(struct bt_dfu_smp_header);
		if (smp_rsp_total > sizeof(smp_rsp_buf)) {
			smp_rsp_total = sizeof(smp_rsp_buf);
		}
	}

	if (smp_rsp_len < sizeof(smp_rsp_buf)) {
		copy_len = MIN((size_t)length, sizeof(smp_rsp_buf) - smp_rsp_len);
		memcpy(&smp_rsp_buf[smp_rsp_len], data, copy_len);
		smp_rsp_len += copy_len;
	}

	printk("OTA rsp part: t=%u conn=%p grp=0x%04x cmd=0x%02x seq=%u off=%u chunk=%u acc=%u total=%u\n",
	       k_uptime_get_32(),
	       default_conn,
	       (unsigned int)smp_inflight_group,
	       (unsigned int)smp_inflight_cmd,
	       (unsigned int)smp_inflight_seq,
	       (unsigned int)(smp_rsp_len > (size_t)length ? (smp_rsp_len - (size_t)length) : 0U),
	       (unsigned int)length,
	       (unsigned int)smp_rsp_len,
	       (unsigned int)smp_rsp_total);

	if (smp_rsp_total > 0U && smp_rsp_len >= smp_rsp_total) {
		k_sem_give(&ota_sem);
	}

	return BT_GATT_ITER_CONTINUE;
}

static void ota_smp_subscribe_cb(struct bt_conn *conn, uint8_t err,
				 struct bt_gatt_subscribe_params *params)
{
	printk("OTA SMP subscribe cb: conn=%p err=%u value_handle=0x%04x ccc=0x%04x\n",
	       conn,
	       (unsigned int)err,
	       params ? (unsigned int)params->value_handle : 0U,
	       params ? (unsigned int)params->ccc_handle : 0U);

	smp_subscribe_err = (int)err;
	k_sem_give(&smp_sub_sem);
}

static void ota_smp_write_cb(struct bt_conn *conn, uint8_t err,
			     struct bt_gatt_write_params *params)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(params);

	smp_write_err = (int)err;
	k_sem_give(&smp_write_sem);
}

static int ota_smp_subscribe_if_needed(struct bt_dfu_smp *smp)
{
	int rc;

	if (smp == NULL || smp->conn == NULL || smp->handles.smp == 0U || smp->handles.smp_ccc == 0U) {
		return -ENOTCONN;
	}
	if (smp_notify_subscribed) {
		return 0;
	}

	memset(&smp->notification_params, 0, sizeof(smp->notification_params));
	smp->notification_params.value_handle = smp->handles.smp;
	smp->notification_params.ccc_handle = smp->handles.smp_ccc;
	smp->notification_params.notify = ota_smp_notify_cb;
	smp->notification_params.subscribe = ota_smp_subscribe_cb;
	smp->notification_params.value = BT_GATT_CCC_NOTIFY;
	atomic_set_bit(smp->notification_params.flags, BT_GATT_SUBSCRIBE_FLAG_VOLATILE);
	smp_subscribe_err = -EINPROGRESS;
	k_sem_reset(&smp_sub_sem);

	rc = bt_gatt_subscribe(smp->conn, &smp->notification_params);
	if (rc == -EALREADY) {
		smp_notify_subscribed = true;
		printk("OTA SMP subscribe already active: smp=0x%04x ccc=0x%04x\n",
		       (unsigned int)smp->handles.smp,
		       (unsigned int)smp->handles.smp_ccc);
		return 0;
	}
	if (rc) {
		printk("OTA SMP subscribe failed: rc=%d smp=0x%04x ccc=0x%04x\n",
		       rc,
		       (unsigned int)smp->handles.smp,
		       (unsigned int)smp->handles.smp_ccc);
		return rc;
	}
	if (k_sem_take(&smp_sub_sem, K_MSEC(3000)) != 0) {
		(void)bt_gatt_unsubscribe(smp->conn, &smp->notification_params);
		printk("OTA SMP subscribe callback timeout: smp=0x%04x ccc=0x%04x\n",
		       (unsigned int)smp->handles.smp,
		       (unsigned int)smp->handles.smp_ccc);
		return -ETIME;
	}
	if (smp_subscribe_err != 0) {
		(void)bt_gatt_unsubscribe(smp->conn, &smp->notification_params);
		printk("OTA SMP subscribe callback error: err=%d smp=0x%04x ccc=0x%04x\n",
		       smp_subscribe_err,
		       (unsigned int)smp->handles.smp,
		       (unsigned int)smp->handles.smp_ccc);
		return -EACCES;
	}
	smp_notify_subscribed = true;
	printk("OTA SMP subscribe ok: rc=%d smp=0x%04x ccc=0x%04x\n",
	       rc,
	       (unsigned int)smp->handles.smp,
	       (unsigned int)smp->handles.smp_ccc);
	return 0;
}

static int cbor_decode_uint_any(const uint8_t *buf, size_t len, uint64_t *value, size_t *used)
{
	uint8_t major;
	uint8_t ai;
	uint64_t v = 0U;
	size_t need = 0U;

	if (!buf || len == 0U || !value || !used) {
		return -EINVAL;
	}

	major = (uint8_t)(buf[0] >> 5);
	ai = (uint8_t)(buf[0] & 0x1fU);
	if (major != 0U && major != 1U) {
		return -EBADMSG;
	}

	if (ai < 24U) {
		v = ai;
		need = 1U;
	} else if (ai == 24U) {
		if (len < 2U) {
			return -EMSGSIZE;
		}
		v = buf[1];
		need = 2U;
	} else if (ai == 25U) {
		if (len < 3U) {
			return -EMSGSIZE;
		}
		v = (((uint64_t)buf[1]) << 8) | buf[2];
		need = 3U;
	} else if (ai == 26U) {
		if (len < 5U) {
			return -EMSGSIZE;
		}
		v = (((uint64_t)buf[1]) << 24) |
		    (((uint64_t)buf[2]) << 16) |
		    (((uint64_t)buf[3]) << 8) |
		    ((uint64_t)buf[4]);
		need = 5U;
	} else if (ai == 27U) {
		if (len < 9U) {
			return -EMSGSIZE;
		}
		v = (((uint64_t)buf[1]) << 56) |
		    (((uint64_t)buf[2]) << 48) |
		    (((uint64_t)buf[3]) << 40) |
		    (((uint64_t)buf[4]) << 32) |
		    (((uint64_t)buf[5]) << 24) |
		    (((uint64_t)buf[6]) << 16) |
		    (((uint64_t)buf[7]) << 8) |
		    ((uint64_t)buf[8]);
		need = 9U;
	} else {
		return -EBADMSG;
	}

	*value = v;
	*used = need;
	return 0;
}

static int ota_parse_response_fallback(struct ota_cmd_result *result,
				       const uint8_t *payload, size_t payload_len)
{
	size_t i = 0U;
	bool any = false;

	if (!result || !payload || payload_len == 0U) {
		return -EINVAL;
	}

	while (i < payload_len) {
		if (i + 4U <= payload_len &&
		    payload[i] == 0x63U &&
		    payload[i + 1] == 'o' &&
		    payload[i + 2] == 'f' &&
		    payload[i + 3] == 'f') {
			uint64_t v = 0U;
			size_t used = 0U;
			int rc = cbor_decode_uint_any(&payload[i + 4U], payload_len - (i + 4U),
						      &v, &used);

			if (rc == 0 && v <= SIZE_MAX) {
				result->off = (size_t)v;
				result->off_found = true;
				any = true;
				i += 4U + used;
				continue;
			}
		}

		if (i + 3U <= payload_len &&
		    payload[i] == 0x62U &&
		    payload[i + 1] == 'r' &&
		    payload[i + 2] == 'c') {
			uint64_t v = 0U;
			size_t used = 0U;
			int rc = cbor_decode_uint_any(&payload[i + 3U], payload_len - (i + 3U),
						      &v, &used);

			if (rc == 0) {
				uint8_t major = (uint8_t)(payload[i + 3U] >> 5);

				if (major == 0U && v <= INT32_MAX) {
					result->status = (int)v;
					any = true;
					i += 3U + used;
					continue;
				}

				if (major == 1U && v < (uint64_t)INT32_MAX) {
					result->status = -(int)(v + 1U);
					any = true;
					i += 3U + used;
					continue;
				}
			}
		}

		/* img state read/write may legitimately return an image list without
		 * a top-level rc/off pair. Treat presence of the "images" key as a
		 * successful response so OTA can continue to the reset step.
		 */
		if (i + 7U <= payload_len &&
		    payload[i] == 0x66U &&
		    payload[i + 1] == 'i' &&
		    payload[i + 2] == 'm' &&
		    payload[i + 3] == 'a' &&
		    payload[i + 4] == 'g' &&
		    payload[i + 5] == 'e' &&
		    payload[i + 6] == 's') {
			result->status = 0;
			any = true;
			i += 7U;
			continue;
		}

		i++;
	}

	return any ? 0 : -ENOMSG;
}

static int ota_parse_response(struct ota_cmd_result *result)
{
	const struct bt_dfu_smp_header *header;
	zcbor_state_t zsd[OTA_CBOR_DECODER_STATE_NUM];
	struct zcbor_map_decode_key_val map[] = {
		ZCBOR_MAP_DECODE_KEY_DECODER("off", zcbor_size_decode, &result->off),
		ZCBOR_MAP_DECODE_KEY_DECODER("rc", zcbor_int32_decode, &result->status),
	};
	size_t decoded = 0U;
	size_t payload_len;
	int rc;
	const uint8_t *payload;

	if (smp_rsp_len < sizeof(struct bt_dfu_smp_header)) {
		result->status = -ETIMEDOUT;
		return result->status;
	}

	header = (const struct bt_dfu_smp_header *)smp_rsp_buf;
	payload_len = (((uint16_t)header->len_h8) << 8) | header->len_l8;
	if (payload_len + sizeof(*header) > smp_rsp_len) {
		result->status = -EMSGSIZE;
		return result->status;
	}
	payload = smp_rsp_buf + sizeof(*header);

	if (payload_len == 2U &&
	    ((payload[0] == 0xbfU && payload[1] == 0xffU) ||
	     payload[0] == 0xa0U)) {
		result->status = 0;
		result->off = 0U;
		result->off_found = false;
		return 0;
	}

	result->status = 0;
	result->off = 0U;
	result->off_found = false;

	zcbor_new_decode_state(zsd, ARRAY_SIZE(zsd), payload, payload_len, 1, NULL, 0);
	rc = zcbor_map_decode_bulk(zsd, map, ARRAY_SIZE(map), &decoded);
	if (rc && rc != -ENOMSG) {
		/* Some MCUmgr responses are encoded as indefinite CBOR maps (bf...ff). */
		if (ota_parse_response_fallback(result, payload, payload_len) == 0) {
			return result->status;
		}
		result->status = -EBADMSG;
		return result->status;
	}

	result->off_found = zcbor_map_decode_bulk_key_found(map, ARRAY_SIZE(map), "off");
	return result->status;
}

static int ota_send_packet(struct bt_dfu_smp *smp, struct smp_packet *pkt,
			   size_t payload_len, struct ota_cmd_result *result,
			   uint16_t group_id, uint8_t command_id, uint8_t smp_op,
			   bool confirmed_write)
{
	int rc;
	size_t tx_len;
	uint32_t t0_ms;
	bt_security_t sec_level = BT_SECURITY_L1;
	static bool first_upload_dumped;

	pkt->header.op = smp_op;
	pkt->header.flags = 0U;
	pkt->header.len_h8 = (uint8_t)((payload_len >> 8) & 0xffU);
	pkt->header.len_l8 = (uint8_t)(payload_len & 0xffU);
	pkt->header.group_h8 = (uint8_t)((group_id >> 8) & 0xffU);
	pkt->header.group_l8 = (uint8_t)(group_id & 0xffU);
	pkt->header.seq = ota_seq++;
	smp_inflight_group = group_id;
	smp_inflight_cmd = command_id;
	smp_inflight_seq = pkt->header.seq;
	pkt->header.id = command_id;
	printk("OTA send: op=%u len=%u group=0x%04x cmd=0x%02x seq=%u\n",
	       (unsigned int)pkt->header.op,
	       (unsigned int)payload_len,
	       (unsigned int)group_id,
	       (unsigned int)command_id,
	       (unsigned int)pkt->header.seq);
	if (group_id == OTA_SMP_GROUP_IMG && command_id == OTA_SMP_CMD_IMG_STATE) {
		size_t total_len = sizeof(pkt->header) + payload_len;
		const uint8_t *raw = (const uint8_t *)pkt;
		size_t dump_len = MIN(total_len, 16U);

		printk("OTA first-frame raw: transport=%s handle=0x%04x total=%u hdr_op=%u hdr_flags=0x%02x hdr_len=%u hdr_group=0x%04x hdr_id=0x%02x hdr_seq=%u raw=",
		       confirmed_write ? "write_req" : "write_cmd",
		       smp != NULL ? smp->handles.smp : 0U,
		       (unsigned int)total_len,
		       (unsigned int)pkt->header.op,
		       (unsigned int)pkt->header.flags,
		       (unsigned int)payload_len,
		       (unsigned int)group_id,
		       (unsigned int)command_id,
		       (unsigned int)pkt->header.seq);
		for (size_t i = 0; i < dump_len; ++i) {
			printk("%02x", raw[i]);
		}
		printk("\n");
	}
	if (!first_upload_dumped &&
	    group_id == OTA_SMP_GROUP_IMG &&
	    command_id == OTA_SMP_CMD_IMG_UPLOAD) {
#if OTA_DEBUG_VERBOSE
		size_t total_len = sizeof(pkt->header) + payload_len;
		const uint8_t *raw = (const uint8_t *)pkt;
		OTA_VLOG("OTA tx bytes (%u):", (unsigned int)total_len);
		for (size_t i = 0; i < total_len; ++i) {
			OTA_VLOG("%02x", raw[i]);
		}
		OTA_VLOG("\n");
#endif
		first_upload_dumped = true;
	}

	k_sem_reset(&ota_sem);
	smp_rsp_len = 0U;
	smp_rsp_total = 0U;
	result->status = -ETIMEDOUT;
	result->off = 0U;
	result->off_found = false;
	tx_len = sizeof(pkt->header) + payload_len;

	rc = ota_smp_subscribe_if_needed(smp);
	if (rc) {
		printk("OTA send blocked: subscribe_not_ready rc=%d grp=0x%04x cmd=0x%02x\n",
		       rc, (unsigned int)group_id, (unsigned int)command_id);
		return rc;
	}

	sec_level = bt_conn_get_security(smp->conn);
	if (confirmed_write) {
		memset(&smp_write_params, 0, sizeof(smp_write_params));
		smp_write_params.handle = smp->handles.smp;
		smp_write_params.offset = 0U;
		smp_write_params.data = pkt;
		smp_write_params.length = tx_len;
		smp_write_params.func = ota_smp_write_cb;
		smp_write_err = -EINPROGRESS;
		k_sem_reset(&smp_write_sem);
		rc = bt_gatt_write(smp->conn, &smp_write_params);
		printk("OTA command issued(write_req): rc=%d tx_len=%u smp=0x%04x ccc=0x%04x sec=%u\n",
		       rc,
		       (unsigned int)tx_len,
		       (unsigned int)smp->handles.smp,
		       (unsigned int)smp->handles.smp_ccc,
		       (unsigned int)sec_level);
		if (rc) {
			printk("OTA command send failed(write_req): grp=0x%04x cmd=0x%02x rc=%d sec=%u\n",
			       group_id, command_id, rc, (unsigned int)sec_level);
			return rc;
		}
		if (k_sem_take(&smp_write_sem, K_MSEC(3000)) != 0) {
			printk("OTA write_req callback timeout: grp=0x%04x cmd=0x%02x conn=%p smp=0x%04x\n",
			       (unsigned int)group_id,
			       (unsigned int)command_id,
			       smp->conn,
			       (unsigned int)smp->handles.smp);
			return -ETIMEDOUT;
		}
		if (smp_write_err != 0) {
			printk("OTA write_req callback error: grp=0x%04x cmd=0x%02x err=%d conn=%p\n",
			       (unsigned int)group_id,
			       (unsigned int)command_id,
			       smp_write_err,
			       smp->conn);
			return -EIO;
		}
	} else {
		rc = bt_gatt_write_without_response(smp->conn, smp->handles.smp, pkt, tx_len, false);
		printk("OTA command issued(write_cmd): rc=%d tx_len=%u smp=0x%04x ccc=0x%04x sec=%u\n",
		       rc,
		       (unsigned int)tx_len,
		       (unsigned int)smp->handles.smp,
		       (unsigned int)smp->handles.smp_ccc,
		       (unsigned int)sec_level);
		if (rc) {
			printk("OTA command send failed(write_cmd): group=0x%04x cmd=0x%02x rc=%d sec=%u\n",
			       group_id, command_id, rc, (unsigned int)sec_level);
			return rc;
		}
	}

	t0_ms = k_uptime_get_32();
	smp_inflight_t0_ms = t0_ms;
	printk("OTA tx inflight: t=%u conn=%p grp=0x%04x cmd=0x%02x seq=%u len=%u\n",
	       t0_ms,
	       default_conn,
	       (unsigned int)group_id,
	       (unsigned int)command_id,
	       (unsigned int)pkt->header.seq,
	       (unsigned int)payload_len);
	OTA_VLOG("OTA wait thread=%p\n", k_current_get());
	if (k_sem_take(&ota_sem, K_SECONDS(OTA_CMD_TIMEOUT_SEC)) != 0) {
		uint32_t t1_ms = k_uptime_get_32();
		printk("[%u->%u] OTA command wait failed: group=0x%04x cmd=0x%02x rc=%d\n",
		       t0_ms, t1_ms, group_id, command_id, -ETIMEDOUT);
		printk("OTA wait debug: rsp_len=%u rsp_total=%u chunk=%u off=%u\n",
		       (unsigned int)smp_rsp_len,
		       (unsigned int)smp_rsp_total,
		       (unsigned int)smp->rsp_state.chunk_size,
		       (unsigned int)smp->rsp_state.offset);
		printk("OTA timeout inflight: dt=%u conn=%p grp=0x%04x cmd=0x%02x seq=%u\n",
		       (unsigned int)(t1_ms - smp_inflight_t0_ms),
		       default_conn,
		       (unsigned int)smp_inflight_group,
		       (unsigned int)smp_inflight_cmd,
		       (unsigned int)smp_inflight_seq);
		printk("OTA timeout context: subscribe=%u sec=%u smp=0x%04x ccc=0x%04x conn=%p default_conn=%p selected_uuid=%s tx=%s\n",
		       smp_notify_subscribed ? 1U : 0U,
		       (unsigned int)bt_conn_get_security(smp->conn),
		       (unsigned int)smp->handles.smp,
		       (unsigned int)smp->handles.smp_ccc,
		       smp->conn,
		       default_conn,
		       selected_uuid[0] != '\0' ? selected_uuid : "-",
		       confirmed_write ? "write_req" : "write_cmd");
		return -ETIMEDOUT;
	}
	printk("OTA rx complete: t=%u dt=%u conn=%p grp=0x%04x cmd=0x%02x seq=%u rsp_len=%u\n",
	       k_uptime_get_32(),
	       (unsigned int)(k_uptime_get_32() - smp_inflight_t0_ms),
	       default_conn,
	       (unsigned int)smp_inflight_group,
	       (unsigned int)smp_inflight_cmd,
	       (unsigned int)smp_inflight_seq,
	       (unsigned int)smp_rsp_len);

	if (group_id == OTA_SMP_GROUP_OS && command_id == 0U) {
		const struct bt_dfu_smp_header *rsp =
			(const struct bt_dfu_smp_header *)smp_rsp_buf;
		uint16_t rsp_group = (((uint16_t)rsp->group_h8) << 8) | rsp->group_l8;

		if (smp_rsp_len < sizeof(*rsp) || rsp->op != 3U ||
		    rsp_group != OTA_SMP_GROUP_OS || rsp->id != 0U) {
			return -EBADMSG;
		}
		result->status = 0;
		result->off = 0U;
		result->off_found = false;
	} else {
		ota_parse_response(result);
	}
	if (!(group_id == OTA_SMP_GROUP_IMG && command_id == OTA_SMP_CMD_IMG_UPLOAD)) {
		printk("OTA command done: group=0x%04x cmd=0x%02x status=%d off=%u\n",
		       group_id, command_id, result->status, (unsigned int)result->off);
	} else if (result->status != 0 ||
		   (result->off_found &&
		    (result->off == tag_ota_image_len ||
		     (result->off % (OTA_CHUNK_SIZE * 64U) == 0U)))) {
		printk("OTA upload ack: status=%d off=%u\n",
		       result->status, (unsigned int)result->off);
	}
	if (result->status != 0) {
		const struct bt_dfu_smp_header *rsp =
			(const struct bt_dfu_smp_header *)smp_rsp_buf;
		size_t rsp_payload_len = smp_rsp_len > sizeof(*rsp) ?
			smp_rsp_len - sizeof(*rsp) : 0U;

		printk("OTA raw rsp: op=%u flags=0x%02x len=%u group=0x%04x id=0x%02x payload=%u\n",
		       rsp->op, rsp->flags,
		       (unsigned int)(((uint16_t)rsp->len_h8 << 8) | rsp->len_l8),
		       (unsigned int)(((uint16_t)rsp->group_h8 << 8) | rsp->group_l8),
		       rsp->id, (unsigned int)rsp_payload_len);
		printk("OTA rsp bytes:");
		for (size_t i = 0; i < rsp_payload_len; i++) {
			printk(" %02x", smp_rsp_buf[sizeof(*rsp) + i]);
		}
		printk("\n");
	}
	return result->status;
}

static size_t ota_build_upload_packet(const uint8_t *image, size_t image_len,
				      const uint8_t *sha256, size_t offset,
				      struct smp_packet *pkt, size_t chunk_len,
				      bool first_chunk)
{
	zcbor_state_t zse[4];
	bool ok;
	uint32_t map_count;
	size_t payload_len;

	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), pkt->payload, sizeof(pkt->payload), 0);

	map_count = first_chunk ? 4U : 3U;

	ok = zcbor_map_start_encode(zse, map_count) &&
	     zcbor_tstr_put_lit(zse, "image") &&
	     zcbor_uint32_put(zse, 0U) &&
	     zcbor_tstr_put_lit(zse, "data") &&
	     zcbor_bstr_encode_ptr(zse, image + offset, chunk_len) &&
	     zcbor_tstr_put_lit(zse, "off") &&
	     zcbor_size_put(zse, offset);

	if (ok && first_chunk) {
		ok = zcbor_tstr_put_lit(zse, "len") &&
		     zcbor_size_put(zse, image_len) &&
		     zcbor_tstr_put_lit(zse, "sha") &&
		     zcbor_bstr_encode_ptr(zse, sha256, sizeof(tag_ota_image_sha256));
	}

	if (ok) {
		ok = zcbor_map_end_encode(zse, map_count);
	}

	if (!ok) {
		return 0U;
	}

	payload_len = (size_t)(zse->payload - pkt->payload);
	return payload_len;
}

static int ota_upload_image(struct bt_dfu_smp *smp)
{
	const int upload_timeout_retries = 3;
	const int upload_retry_delay_ms = 120;
	size_t offset = 0U;
	size_t remaining = tag_ota_image_len;
	size_t stall_count = 0U;
	bool first_chunk = true;
	unsigned int last_reported_percent = 0U;
	struct smp_packet pkt;
	struct ota_cmd_result result;
	int rc;

	printk("OTA upload starting: image_len=%u bytes\n", (unsigned int)tag_ota_image_len);
	printk("OTA upload progress: 0%% (0/%u bytes)\n", (unsigned int)tag_ota_image_len);
	master_leds_set(false, true, true, false);

	while (remaining > 0U) {
		size_t payload_len;
		size_t chunk_len = MIN(remaining, OTA_CHUNK_SIZE);
		size_t progress_bytes = offset + chunk_len;
		unsigned int percent = (unsigned int)((progress_bytes * 100U) /
						      tag_ota_image_len);
		size_t expected_next;
		bool log_progress;
		size_t max_att_payload =
			(default_conn != NULL && bt_gatt_get_mtu(default_conn) > 3U) ?
				(bt_gatt_get_mtu(default_conn) - 3U) : 20U;
		bool chunk_fit = false;

		memset(&pkt, 0, sizeof(pkt));
		while (chunk_len > 0U) {
			payload_len = ota_build_upload_packet(tag_ota_image,
							       tag_ota_image_len,
							       tag_ota_image_sha256,
							       offset, &pkt,
							       chunk_len,
							       first_chunk);
			if (payload_len != 0U &&
			    (sizeof(pkt.header) + payload_len) <= max_att_payload) {
				chunk_fit = true;
				break;
			}
			chunk_len /= 2U;
		}
		if (!chunk_fit) {
			printk("OTA upload packet build/fit failed: off=%u mtu=%u max_att=%u first=%d\n",
			       (unsigned int)offset,
			       default_conn != NULL ? (unsigned int)bt_gatt_get_mtu(default_conn) : 0U,
			       (unsigned int)max_att_payload,
			       first_chunk);
			return -EIO;
		}
		if (chunk_len != MIN(remaining, OTA_CHUNK_SIZE)) {
			printk("OTA chunk downshift: off=%u requested=%u actual=%u mtu=%u\n",
			       (unsigned int)offset,
			       (unsigned int)MIN(remaining, OTA_CHUNK_SIZE),
			       (unsigned int)chunk_len,
			       default_conn != NULL ? (unsigned int)bt_gatt_get_mtu(default_conn) : 0U);
		}
		progress_bytes = offset + chunk_len;
		percent = (unsigned int)((progress_bytes * 100U) / tag_ota_image_len);

		log_progress = first_chunk || (percent != last_reported_percent) ||
			       (remaining <= OTA_CHUNK_SIZE);
		if (log_progress) {
			printk("OTA upload progress: %u%% (%u/%u bytes)\n",
			       percent, (unsigned int)progress_bytes,
			       (unsigned int)tag_ota_image_len);
			last_reported_percent = percent;
		}
		if (first_chunk) {
			printk("OTA first IMG_UPLOAD tx prep: t=%u conn=%p uuid=%s off=%u chunk=%u\n",
			       k_uptime_get_32(),
			       default_conn,
			       selected_uuid[0] != '\0' ? selected_uuid : "-",
			       (unsigned int)offset,
			       (unsigned int)chunk_len);
#if OTA_DEBUG_VERBOSE
			size_t total_len = sizeof(pkt.header) + payload_len;
			const uint8_t *raw = (const uint8_t *)&pkt;
			OTA_VLOG("OTA first packet bytes (%u):", (unsigned int)total_len);
			for (size_t i = 0; i < total_len; ++i) {
				OTA_VLOG("%02x", raw[i]);
			}
			OTA_VLOG("\n");
#endif
		}
		rc = ota_send_packet(smp, &pkt, payload_len, &result, OTA_SMP_GROUP_IMG,
				     OTA_SMP_CMD_IMG_UPLOAD, 2U, false);
		if (rc == -ETIMEDOUT) {
			int attempt;

			for (attempt = 1; attempt <= upload_timeout_retries; ++attempt) {
				printk("OTA upload timeout at off=%u, retry %d/%d\n",
				       (unsigned int)offset, attempt, upload_timeout_retries);
				k_msleep(upload_retry_delay_ms);
				rc = ota_send_packet(smp, &pkt, payload_len, &result,
						     OTA_SMP_GROUP_IMG,
						     OTA_SMP_CMD_IMG_UPLOAD, 2U, false);
				if (rc == 0) {
					break;
				}
				if (rc != -ETIMEDOUT) {
					break;
				}
			}
		}
		if (rc) {
			return rc;
		}

		expected_next = offset + chunk_len;
		if (result.off_found && result.off != expected_next && log_progress) {
			printk("OTA upload offset mismatch: expected %u got %u\n",
			       (unsigned int)expected_next, (unsigned int)result.off);
		}
		if (result.off_found) {
			if (result.off > tag_ota_image_len) {
				printk("OTA upload invalid offset from target: %u\n",
				       (unsigned int)result.off);
				return -EPROTO;
			}
			if (result.off == offset) {
				if (++stall_count > 3U) {
					printk("OTA upload stalled at off=%u\n", (unsigned int)offset);
					return -EAGAIN;
				}
			} else {
				stall_count = 0U;
				offset = result.off;
				remaining = tag_ota_image_len - offset;
			}
		} else {
			offset = expected_next;
			remaining = tag_ota_image_len - offset;
		}
		first_chunk = false;
	}

	printk("OTA upload complete\n");
	return 0;
}

static int ota_schedule_pending(struct bt_dfu_smp *smp)
{
	struct smp_packet pkt;
	struct ota_cmd_result result;
	zcbor_state_t zse[4];
	bool ok;
	size_t payload_len;

	memset(&pkt, 0, sizeof(pkt));
	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), pkt.payload, sizeof(pkt.payload), 0);

	ok = zcbor_map_start_encode(zse, 4U) &&
	     zcbor_tstr_put_lit(zse, "hash") &&
	     zcbor_bstr_encode_ptr(zse, tag_ota_image_image_hash,
				   sizeof(tag_ota_image_image_hash)) &&
	     zcbor_tstr_put_lit(zse, "confirm") &&
	     zcbor_bool_put(zse, false) &&
	     zcbor_map_end_encode(zse, 4U);
	if (!ok) {
		return -EIO;
	}

	payload_len = (size_t)(zse->payload - pkt.payload);

	printk("OTA pending/test request\n");
	master_leds_set(false, true, true, false);
	return ota_send_packet(smp, &pkt, payload_len, &result, OTA_SMP_GROUP_IMG,
			       OTA_SMP_CMD_IMG_STATE, 2U, false);
}

static int ota_remote_reset(struct bt_dfu_smp *smp)
{
	struct smp_packet pkt;
	struct ota_cmd_result result;
	zcbor_state_t zse[4];
	bool ok;
	size_t payload_len;

	memset(&pkt, 0, sizeof(pkt));
	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), pkt.payload, sizeof(pkt.payload), 0);

	ok = zcbor_map_start_encode(zse, 0U) && zcbor_map_end_encode(zse, 0U);
	if (!ok) {
		return -EIO;
	}

	payload_len = (size_t)(zse->payload - pkt.payload);

	printk("OTA reset request\n");
	master_leds_set(false, true, true, false);
	return ota_send_packet(smp, &pkt, payload_len, &result, OTA_SMP_GROUP_OS,
			       OTA_SMP_CMD_OS_RESET, 2U, false);
}

static int ota_erase_secondary_slot(struct bt_dfu_smp *smp)
{
	struct smp_packet pkt;
	struct ota_cmd_result result;
	zcbor_state_t zse[4];
	bool ok;
	size_t payload_len;

	memset(&pkt, 0, sizeof(pkt));
	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), pkt.payload, sizeof(pkt.payload), 0);

	ok = zcbor_map_start_encode(zse, 2U) &&
	     zcbor_tstr_put_lit(zse, "slot") &&
	     zcbor_uint32_put(zse, 1U) &&
	     zcbor_map_end_encode(zse, 2U);
	if (!ok) {
		return -EIO;
	}

	payload_len = (size_t)(zse->payload - pkt.payload);

	printk("OTA erase secondary slot request\n");
	master_leds_set(false, true, true, false);
	return ota_send_packet(smp, &pkt, payload_len, &result, OTA_SMP_GROUP_IMG,
			       OTA_SMP_CMD_IMG_ERASE, 2U, false);
}

static int ota_read_image_state(struct bt_dfu_smp *smp, bool confirmed_write)
{
	struct smp_packet pkt;
	struct ota_cmd_result result;
	zcbor_state_t zse[4];
	bool ok;
	size_t payload_len;

	memset(&pkt, 0, sizeof(pkt));
	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), pkt.payload, sizeof(pkt.payload), 0);

	ok = zcbor_map_start_encode(zse, 0U) && zcbor_map_end_encode(zse, 0U);
	if (!ok) {
		return -EIO;
	}

	payload_len = (size_t)(zse->payload - pkt.payload);

	printk("OTA image state read request\n");
	return ota_send_packet(smp, &pkt, payload_len, &result, OTA_SMP_GROUP_IMG,
			       OTA_SMP_CMD_IMG_STATE, 0U, confirmed_write);
}

static int ota_prime_link(struct bt_dfu_smp *smp)
{
	struct smp_packet pkt;
	struct ota_cmd_result result;
	zcbor_state_t zse[4];
	bool ok;
	size_t payload_len;
	int rc;

	memset(&pkt, 0, sizeof(pkt));
	zcbor_new_encode_state(zse, ARRAY_SIZE(zse), pkt.payload, sizeof(pkt.payload), 0);
	ok = zcbor_map_start_encode(zse, 2U) &&
	     zcbor_tstr_put_lit(zse, "d") &&
	     zcbor_tstr_put_lit(zse, "ping") &&
	     zcbor_map_end_encode(zse, 2U);
	if (!ok) {
		return -EIO;
	}

	payload_len = (size_t)(zse->payload - pkt.payload);
	printk("OTA prime echo request\n");
	printk("OTA prime payload len=%u bytes:", (unsigned int)payload_len);
	for (size_t i = 0; i < payload_len; ++i) {
		printk("%02x", pkt.payload[i]);
	}
	printk("\n");
	rc = ota_send_packet(smp, &pkt, payload_len, &result, OTA_SMP_GROUP_OS, 0U, 2U, false);
	if (rc == -ETIMEDOUT) {
		printk("OTA prime timeout (non-fatal), continue with IMG erase/upload\n");
		return 0;
	}
	return rc;
}

static int ota_wait_upload_ready(struct bt_dfu_smp *smp)
{
	int rc;
	int attempt;
	const int max_attempts = 5;
	const bool probe_write_req = (APP_MASTER_OTA_DIAG_FIRST_GATE_WRITE_REQ != 0);

	for (attempt = 1; attempt <= max_attempts; ++attempt) {
		if (!ota_session_active) {
			printk("OTA upload gate pending: session_inactive\n");
			return -ECANCELED;
		}
		if (default_conn == NULL) {
			printk("OTA upload gate pending: no_conn\n");
			return -ENOTCONN;
		}
		if (!ota_ready) {
			printk("OTA upload gate pending: dfu_not_ready\n");
			return -EAGAIN;
		}
		if (!mtu_ready) {
			printk("OTA upload gate pending: mtu_not_ready\n");
			return -EAGAIN;
		}
		if (ota_security_required && !sec_ready) {
			printk("OTA upload gate pending: sec_not_ready\n");
			return -EAGAIN;
		}

		printk("OTA upload gate probe: attempt=%d/%d\n", attempt, max_attempts);
		rc = ota_read_image_state(smp, probe_write_req && (attempt == 1));
		if (rc == 0) {
			ota_upload_gate_ok = true;
			printk("OTA upload gate open: attempt=%d\n", attempt);
			return 0;
		}
		printk("OTA upload gate probe failed: rc=%d tx=%s\n",
		       rc,
		       (probe_write_req && attempt == 1) ? "write_req(diag)" : "write_cmd");
		k_msleep(180);
	}

	return -ETIMEDOUT;
}

static void ota_try_schedule_start(void)
{
	if (!APP_MASTER_OTA_UPLOAD_ENABLE) {
		return;
	}
	if (!ota_armed) {
		return;
	}

	if (!ota_ready || !mtu_ready || ota_started || ota_done || default_conn == NULL) {
		return;
	}
	if (!selected_identity_verified) {
		printk("OTA start blocked: identity not verified (addr=%s uuid=%s name=%s token=%d)\n",
		       selected_addr[0] != '\0' ? selected_addr : "-",
		       selected_uuid[0] != '\0' ? selected_uuid : "-",
		       selected_name[0] != '\0' ? selected_name : "-",
		       selected_token);
		ota_status = -EACCES;
		ota_done = true;
		ota_set_state(OTA_OP_BLOCKED, "identity_not_verified");
		return;
	}

	if (!ota_start_queued) {
		ota_start_queued = true;
		k_sem_give(&ota_start_sem);
	}
}

static void ota_thread_fn(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	while (1) {
		k_sem_take(&ota_start_sem, K_FOREVER);
		ota_start_queued = false;

		if (!APP_MASTER_OTA_UPLOAD_ENABLE) {
			printk("OTA upload disabled (monitor-only mode)\n");
			continue;
		}

		if (!ota_ready || !mtu_ready || ota_started || ota_done || default_conn == NULL) {
			continue;
		}

		printk("OTA start gate: mtu=%u conn=%p\n",
		       (unsigned int)bt_gatt_get_mtu(default_conn), default_conn);
		/* Some targets need a short settle window after DFU discovery/MTU setup
		 * before they start returning SMP responses reliably.
		 */
		k_msleep(300);
		ota_upload_gate_ok = false;
		ota_status = ota_wait_upload_ready(&dfu_smp);
		if (ota_status) {
			printk("OTA upload gate failed: %d\n", ota_status);
			if (ota_status == -ETIMEDOUT) {
				printk("OTA upload gate stalled after DFU ready; issuing recovery reset\n");
				ota_status = ota_remote_reset(&dfu_smp);
				if (ota_status) {
					printk("OTA gate recovery reset failed: %d\n", ota_status);
					master_leds_set(false, true, false, true);
					ota_done = true;
					ota_session_set(false, "upload_gate_recovery_reset_failed");
					continue;
				}
				printk("OTA upload-gate recovery reset request\n");
				ota_done = true;
				ota_set_state(OTA_OP_REBOOT_PENDING, "upload_gate_recovery_reset");
				master_leds_set(false, true, false, false);
				ota_session_set(false, "upload_gate_recovery_reset");
				continue;
			}
			master_leds_set(false, true, false, true);
			ota_done = true;
			ota_session_set(false, "upload_gate_failed");
			continue;
		}
		ota_set_state(OTA_OP_UPLOADING, "start");
		ota_started = true;
			ota_status = ota_prime_link(&dfu_smp);
			if (ota_status) {
				/* Some targets may drop OS-echo while IMG mgmt is still functional.
				 * Keep strict target safety, but allow upload path to proceed.
				 */
				if (ota_status == -ETIMEDOUT) {
					printk("OTA prime timeout; continuing with erase/upload path\n");
				} else {
					printk("OTA prime failed: %d\n", ota_status);
					master_leds_set(false, true, false, true);
					ota_done = true;
					continue;
				}
			}

			ota_status = ota_erase_secondary_slot(&dfu_smp);
			if (ota_status) {
				/* Some targets can execute erase but miss the SMP response under
				 * heavy radio/ATT load (observed as -ETIMEDOUT/-116). Treat timeout
				 * as non-fatal and continue upload path, same strategy as prime echo.
				 */
				if (ota_status == -ETIMEDOUT) {
					printk("OTA erase timeout; continuing with upload path\n");
					k_msleep(120);
				} else if (ota_status == OTA_MGMT_ERR_EBADSTATE) {
					printk("OTA erase blocked by pending image state; issuing recovery reset\n");
					ota_status = ota_remote_reset(&dfu_smp);
					if (ota_status) {
						printk("OTA recovery reset failed: %d\n", ota_status);
						master_leds_set(false, true, false, true);
						ota_done = true;
						ota_session_set(false, "erase_pending_recovery_reset_failed");
						continue;
					}
					printk("OTA pending-state recovery reset request\n");
					ota_done = true;
					ota_set_state(OTA_OP_REBOOT_PENDING, "erase_pending_recovery_reset");
					master_leds_set(false, true, false, false);
					ota_session_set(false, "erase_pending_recovery_reset");
					continue;
				} else {
					printk("OTA erase failed: %d\n", ota_status);
					master_leds_set(false, true, false, true);
					ota_done = true;
					continue;
				}
			}

		ota_status = ota_upload_image(&dfu_smp);
		if (ota_status) {
			printk("OTA upload failed: %d\n", ota_status);
			master_leds_set(false, true, false, true);
			ota_done = true;
			ota_session_set(false, "upload_failed");
			continue;
		}
		ota_set_state(OTA_OP_UPLOAD_COMPLETE, "upload_done");

		ota_status = ota_read_image_state(&dfu_smp, false);
		if (ota_status) {
			printk("OTA state read failed: %d\n", ota_status);
			printk("OTA continuing to pending/test despite state read failure\n");
		}

		ota_status = ota_schedule_pending(&dfu_smp);
		if (ota_status) {
			printk("OTA schedule warning: %d\n", ota_status);
			printk("OTA continuing to remote reset despite schedule warning\n");
		}

		ota_status = ota_remote_reset(&dfu_smp);
		if (ota_status) {
			printk("OTA reset failed: %d\n", ota_status);
			master_leds_set(false, true, false, true);
			ota_done = true;
			ota_session_set(false, "reset_failed");
			continue;
		}

		printk("OTA command sequence sent\n");
		ota_set_state(OTA_OP_REBOOT_PENDING, "remote_reset_sent");
		ota_done = true;
		ota_set_state(OTA_OP_VERIFY_PASSED, "sequence_done");
		master_leds_set(false, true, false, false);
		ota_session_set(false, "sequence_done");
	}
}

static void dfu_error_cb(struct bt_dfu_smp *smp, int err)
{
	ARG_UNUSED(smp);

	printk("DFU SMP error: %d\n", err);
	ota_status = err;
	master_leds_set(led_scan_state, led_link_state, false, true);
}

static const struct bt_dfu_smp_init_params init_params = {
	.error_cb = dfu_error_cb,
};

static void exchange_func(struct bt_conn *conn, uint8_t err,
			  struct bt_gatt_exchange_params *params)
{
	ARG_UNUSED(params);

	if (err) {
		printk("MTU exchange failed: %u\n", err);
	} else {
		printk("MTU exchange done, mtu=%u\n", (unsigned int)bt_gatt_get_mtu(conn));
		mtu_ready = true;
		ota_try_schedule_start();
	}
}

static void discovery_complete(struct bt_gatt_dm *dm, void *context)
{
	ARG_UNUSED(context);
	int err;
	master_ble_activity_pulse();

	if (discovery_phase == DISCOVERY_PHASE_NUS) {
		err = bt_nus_handles_assign(dm, &nus_client);
		if (err) {
			printk("NUS handle assign failed: %d\n", err);
		} else {
			err = bt_nus_subscribe_receive(&nus_client);
			if (err) {
				printk("NUS subscribe failed: %d\n", err);
			} else {
				nus_ready = true;
				printk("NUS service ready\n");
				if (APP_MASTER_OTA_UPLOAD_ENABLE) {
					err = ota_arm_target_via_nus();
					if (err) {
						printk("OTA arm via NUS failed: %d\n", err);
					}
				} else {
					printk("OTA arm skipped (monitor-only mode)\n");
				}
			}
		}

		bt_gatt_dm_data_release(dm);
		discovery_phase = DISCOVERY_PHASE_DFU;
		gatt_discover_dfu(default_conn);
		return;
	}

	err = bt_dfu_smp_handles_assign(dm, &dfu_smp);
	if (err) {
		printk("DFU SMP handle assign failed: %d\n", err);
		bt_gatt_dm_data_release(dm);
		return;
	}

	ota_ready = true;
	ota_dfu_ready_watchdog_cancel("dfu_ready");
	printk("DFU SMP service ready\n");
	printk("DFU handles: smp=0x%04x ccc=0x%04x\n",
	       dfu_smp.handles.smp, dfu_smp.handles.smp_ccc);
	master_leds_set(false, true, false, false);
	/* Give peer and ATT subscription path a conservative settle window before
	 * issuing first SMP command. On busy benches, shorter delays still caused
	 * first-command response starvation (-116) after otherwise valid connect.
	 */
	k_sleep(K_MSEC(1200));
	ota_try_schedule_start();

	bt_gatt_dm_data_release(dm);
}

static void discovery_service_not_found(struct bt_conn *conn, void *context)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(context);
	master_ble_activity_pulse();

	if (discovery_phase == DISCOVERY_PHASE_NUS) {
		printk("NUS service not found, continuing with DFU\n");
		ota_security_required = false;
		sec_ready = true;
		discovery_phase = DISCOVERY_PHASE_DFU;
		gatt_discover_dfu(default_conn);
		return;
	}

	printk("DFU SMP service not found\n");
	ota_status = -ENOENT;
	master_leds_set(true, false, false, true);
}

static void discovery_error(struct bt_conn *conn, int err, void *context)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(context);
	master_ble_activity_pulse();

	if (discovery_phase == DISCOVERY_PHASE_NUS) {
		printk("NUS discovery error: %d, continuing with DFU\n", err);
		ota_security_required = false;
		sec_ready = true;
		discovery_phase = DISCOVERY_PHASE_DFU;
		gatt_discover_dfu(default_conn);
		return;
	}

	printk("GATT discovery error: %d\n", err);
	ota_status = err;
	master_leds_set(true, false, false, true);
}

static struct bt_gatt_dm_cb discovery_cb = {
	.completed = discovery_complete,
	.service_not_found = discovery_service_not_found,
	.error_found = discovery_error,
};

static void gatt_discover_nus(struct bt_conn *conn)
{
	int err;

	err = bt_gatt_dm_start(conn, BT_UUID_NUS_SERVICE, &discovery_cb, NULL);
	if (err) {
		printk("Could not start NUS discovery: %d\n", err);
		discovery_phase = DISCOVERY_PHASE_DFU;
		gatt_discover_dfu(conn);
	}
}

static void gatt_discover_dfu(struct bt_conn *conn)
{
	int err;

	err = bt_gatt_dm_start(conn, BT_UUID_DFU_SMP_SERVICE, &discovery_cb, NULL);
	if (err) {
		printk("Could not start discovery: %d\n", err);
		ota_status = err;
		master_leds_set(true, false, false, true);
	}
}

static void connected(struct bt_conn *conn, uint8_t conn_err)
{
	char addr[BT_ADDR_LE_STR_LEN];
	struct bt_conn_info info;
	int sec_err;
	int scan_err;
	master_ble_activity_pulse();

	if (!ota_runtime_active) {
		return;
	}

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));

	if (conn_err) {
		printk("Connect failed %s err 0x%02x\n", addr, conn_err);
		ota_set_state(OTA_OP_CONNECT_FAILED, "conn_cb");
		if (default_conn == conn) {
			bt_conn_unref(default_conn);
			default_conn = NULL;
		}
		master_leds_set(true, false, false, true);
		return;
	}

	/* Only the connection explicitly created by OTA scan/connect is valid for
	 * this OTA attempt. Any unsolicited connection must be dropped to avoid
	 * corrupting strict-target state (selected identity, discovery phase).
	 */
	if (conn != default_conn) {
		printk("Connected ignored (not default_conn): %s\n", addr);
		(void)bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
		return;
	}

	printk("Connected: %s\n", addr);
	printk("Connected target evidence: verified=%u uuid=%s name=%s token=%d\n",
	       selected_identity_verified ? 1U : 0U,
	       selected_uuid[0] != '\0' ? selected_uuid : "-",
	       selected_name[0] != '\0' ? selected_name : "-",
	       selected_token);
	if (selected_identity_verified) {
		ota_set_state(OTA_OP_CONNECTED_VERIFIED, "connected");
		ota_dfu_ready_watchdog_arm();
	}
	printk("Connected MTU: %u\n", (unsigned int)bt_gatt_get_mtu(conn));
	if (bt_conn_get_info(conn, &info) == 0 && info.type == BT_CONN_TYPE_LE) {
		printk("Conn LE params: int=%u lat=%u timeout=%u\n",
		       info.le.interval, info.le.latency, info.le.timeout);
	}
	sec_err = bt_conn_le_phy_update(conn, fast_phy_params);
	printk("PHY update request rc=%d\n", sec_err);
	sec_err = bt_conn_le_param_update(conn, fast_conn_params);
	printk("Conn param update request rc=%d\n", sec_err);
	sec_ready = true;
	ota_security_required = false;
	k_sem_reset(&sec_ready_sem);
	/* Keep OTA at the current link security level for both Anchor and Tag
	 * targets. Several field Tags reject pairing/security upgrade with err=9,
	 * which disconnects before SMP DFU discovery and leaves the controller in
	 * a stale connected-but-not-ready state.
	 */
	printk("Security OTA: skip L2 upgrade, use current link level\n");
	/* Stop scan before OTA service discovery to avoid scan callbacks from
	 * unrelated peers racing with OTA state on busy benches.
	 */
	scan_err = bt_scan_stop();
	if (scan_err && scan_err != -EALREADY) {
		printk("Scan stop after connect failed: %d\n", scan_err);
	}

	exchange_params.func = exchange_func;
	(void)bt_gatt_exchange_mtu(conn, &exchange_params);
	if (runtime_expect_nus) {
		discovery_phase = DISCOVERY_PHASE_NUS;
		gatt_discover_nus(conn);
	} else {
		discovery_phase = DISCOVERY_PHASE_DFU;
		gatt_discover_dfu(conn);
	}
	master_leds_set(false, true, false, false);
}

static void security_changed(struct bt_conn *conn, bt_security_t level,
			     enum bt_security_err err)
{
	char addr[BT_ADDR_LE_STR_LEN];

	if (!ota_runtime_active) {
		return;
	}

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	printk("Security changed: %s level=%u err=%u\n", addr,
	       (unsigned int)level, (unsigned int)err);

	if (conn == default_conn && err == BT_SECURITY_ERR_SUCCESS &&
	    level >= BT_SECURITY_L2) {
		sec_ready = true;
		k_sem_give(&sec_ready_sem);
	} else if (conn == default_conn && err != BT_SECURITY_ERR_SUCCESS) {
		printk("Security fallback: err=%u level=%u, continuing without L2\n",
		       (unsigned int)err, (unsigned int)level);
		sec_ready = true;
		k_sem_give(&sec_ready_sem);
	}
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	char addr[BT_ADDR_LE_STR_LEN];
	bool was_default = (default_conn == conn);
	master_ble_activity_pulse();

	if (!ota_runtime_active) {
		return;
	}

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	printk("Disconnected: %s reason 0x%02x\n", addr, reason);
	ota_dfu_ready_watchdog_cancel("disconnect");

	if (was_default) {
		bt_conn_unref(default_conn);
		default_conn = NULL;
	} else {
		/* Ignore stale/non-target disconnects; they must not reset the
		 * active OTA attempt or restart scanning while OTA is running.
		 */
		printk("Disconnected ignored (not default_conn)\n");
		return;
	}

	ota_ready = false;
	ota_started = false;
	mtu_ready = false;
	ota_start_queued = false;
	nus_ready = false;
	smp_notify_subscribed = false;
	ota_security_required = false;
	selected_identity_verified = false;
	selected_addr[0] = '\0';
	selected_name[0] = '\0';
	selected_uuid[0] = '\0';
	selected_token = -1;
	if (ota_status < 0 && ota_done) {
		ota_set_state(OTA_OP_VERIFY_FAILED, "disconnect_after_error");
		ota_session_set(false, "disconnect_after_error");
	}
	master_leds_set(true, false, false, false);
	if (ota_session_active) {
		(void)bt_scan_start(BT_SCAN_TYPE_SCAN_ACTIVE);
	} else {
		printk("Scan restart suppressed (no active OTA session)\n");
	}
}

static struct bt_conn_cb conn_callbacks = {
	.connected = connected,
	.disconnected = disconnected,
	.security_changed = security_changed,
};

static void scan_filter_match(struct bt_scan_device_info *device_info,
			      struct bt_scan_filter_match *filter_match,
			      bool connectable)
{
	char addr[BT_ADDR_LE_STR_LEN];
	char name[OTA_NAME_BUF_LEN];
	bool accept;
	int err;
	struct bt_conn *conn = NULL;
	uint8_t token_id;
	uint16_t bs_code;
	bool bs_code_valid;
	char uuid_hex[OTA_UUID_HEX_LEN + 1U];
	char bs_name[8];
	const char *identity_name;
	struct scan_target_eval eval;

	ARG_UNUSED(filter_match);
	if (!ota_runtime_active || !ota_session_active) {
		return;
	}
	master_ble_activity_pulse();
	bt_addr_le_to_str(device_info->recv_info->addr, addr, sizeof(addr));
	ad_extract_name(device_info->adv_data, name, sizeof(name));
	token_id = ad_extract_token_id(device_info->adv_data);
	bs_code_valid = ad_extract_bs_code(device_info->adv_data, &bs_code);
	if (bs_code_valid) {
		snprintk(bs_name, sizeof(bs_name), "BS%04X", bs_code);
		identity_name = bs_name;
	} else {
		bs_name[0] = '\0';
		identity_name = name;
	}
	if (!ad_extract_uuid_hex(device_info->adv_data, uuid_hex, sizeof(uuid_hex))) {
		uuid_hex[0] = '\0';
	}
	/* Defensive strict gate before any connect attempt:
	 * when UUID target is configured, only an exact UUID match is allowed.
	 */
	if (runtime_target_uuid[0] != '\0') {
		if (uuid_hex[0] == '\0' || !str_case_eq(uuid_hex, runtime_target_uuid)) {
			printk("Scan reject: %s reason=uuid_mismatch got=%s want=%s\n",
			       addr,
			       uuid_hex[0] != '\0' ? uuid_hex : "-",
			       runtime_target_uuid);
			ota_set_state(OTA_OP_TARGET_REJECTED, "uuid_mismatch");
			return;
		}
	}
	accept = ad_eval_target(device_info->adv_data, identity_name, token_id, uuid_hex, &eval);
	printk("Scan eval: %s conn=%d token=%d uuid=%s name=%s bs=%s name_ok=%u token_ok=%u uuid_ok=%u strict_id=%u\n",
	       addr,
	       connectable,
	       token_id == 0xffU ? -1 : (int)token_id,
	       uuid_hex[0] != '\0' ? uuid_hex : "-",
	       name[0] != '\0' ? name : "-",
	       bs_name[0] != '\0' ? bs_name : "-",
	       eval.name_match ? 1U : 0U,
	       eval.token_match ? 1U : 0U,
	       eval.uuid_match ? 1U : 0U,
	       eval.strict_identity_ok ? 1U : 0U);

	accept = accept && connectable && (default_conn == NULL);
	printk("Scan decision: %s accept=%u default_conn=%p target_name=%s target_token=%d target_uuid=%s\n",
	       addr,
	       accept ? 1U : 0U,
	       default_conn,
	       runtime_target_name[0] != '\0' ? runtime_target_name : "-",
	       runtime_target_token,
	       runtime_target_uuid[0] != '\0' ? runtime_target_uuid : "-");
	if (!accept) {
		ota_set_state(OTA_OP_TARGET_REJECTED, "scan_filter");
		return;
	}

	err = bt_scan_stop();
	if (err && err != -EALREADY) {
		printk("Scan stop failed before connect %s err %d\n", addr, err);
	}
	printk("Connect start: %s token=%d name=%s bs=%s\n",
	       addr,
	       token_id == 0xffU ? -1 : (int)token_id,
	       name[0] != '\0' ? name : "-",
	       bs_name[0] != '\0' ? bs_name : "-");
	ota_set_state(OTA_OP_CONNECT_PENDING, "bt_conn_le_create");
	err = bt_conn_le_create(device_info->recv_info->addr,
				BT_CONN_LE_CREATE_CONN,
				fast_conn_params,
				&conn);
	if (err) {
		printk("Connect start failed %s err %d\n", addr, err);
		ota_set_state(OTA_OP_CONNECT_FAILED, "bt_conn_le_create");
		(void)bt_scan_start(BT_SCAN_TYPE_SCAN_PASSIVE);
		return;
	}

	default_conn = conn;
	selected_identity_verified = eval.strict_identity_ok;
	(void)snprintf(selected_addr, sizeof(selected_addr), "%s", addr);
	(void)snprintf(selected_name, sizeof(selected_name), "%s",
		       identity_name[0] != '\0' ? identity_name : "");
	(void)snprintf(selected_uuid, sizeof(selected_uuid), "%s",
		       uuid_hex[0] != '\0' ? uuid_hex : "");
	selected_token = token_id == 0xffU ? -1 : (int)token_id;
}

static void scan_filter_no_match(struct bt_scan_device_info *device_info,
				 bool connectable)
{
	static struct bt_scan_filter_match dummy;

	memset(&dummy, 0, sizeof(dummy));
	scan_filter_match(device_info, &dummy, connectable);
}

static void scan_connecting_error(struct bt_scan_device_info *device_info)
{
	ARG_UNUSED(device_info);
	printk("Connecting failed\n");
	master_ble_activity_pulse();
	ota_set_state(OTA_OP_CONNECT_FAILED, "scan_connecting_error");
}

static void scan_connecting(struct bt_scan_device_info *device_info,
			    struct bt_conn *conn)
{
	ARG_UNUSED(device_info);
	ARG_UNUSED(conn);
	/* Keep ownership of default_conn in the explicit strict-target connect
	 * path (scan_filter_match -> bt_conn_le_create). Accepting scan-layer
	 * implicit connection assignment here can poison target identity state.
	 */
}

BT_SCAN_CB_INIT(scan_cb, scan_filter_match, scan_filter_no_match, scan_connecting_error,
		scan_connecting);

static int scan_init(void)
{
	struct bt_scan_init_param scan_init = {
		.connect_if_match = 0,
	};

	bt_scan_init(&scan_init);
	bt_scan_cb_register(&scan_cb);
	printk("OTA scan mode: identity-first (no prefilter)\n");

	master_leds_set(true, false, false, false);
	return 0;
}

struct ota_disconnect_ctx {
	int requested;
};

static void ota_disconnect_each_cb(struct bt_conn *conn, void *user_data)
{
	struct ota_disconnect_ctx *ctx = user_data;
	int err;

	if (!conn || !ctx) {
		return;
	}

	err = bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
	if (err == 0 || err == -EALREADY || err == -ENOTCONN) {
		ctx->requested++;
	}
}

int master_ota_initiate(void)
{
	int err;
	struct ota_disconnect_ctx dctx = {0};

	if (ota_session_active) {
		printk("OTA initiate rejected: session already active\n");
		return -EBUSY;
	}

	/* Reset runtime state for a fresh strict-target attempt. */
	ota_dfu_ready_watchdog_cancel("initiate_reset");
	ota_ready = false;
	ota_started = false;
	ota_done = false;
	ota_armed = true;
	ota_start_queued = false;
	mtu_ready = false;
	nus_ready = false;
	ota_security_required = false;
	ota_status = 0;
	selected_identity_verified = false;
	selected_addr[0] = '\0';
	selected_name[0] = '\0';
	selected_uuid[0] = '\0';
	selected_token = -1;
	dfu_ready_watchdog_redrive_count = 0U;
	if (default_conn != NULL) {
		bt_conn_unref(default_conn);
		default_conn = NULL;
	}

	bt_conn_foreach(BT_CONN_TYPE_ALL, ota_disconnect_each_cb, &dctx);
	printk("OTA initiate: disconnect requests=%d\n", dctx.requested);
	k_sleep(K_MSEC(250));

	err = bt_scan_stop();
	if (err && err != -EALREADY) {
		printk("OTA initiate: scan stop failed: %d\n", err);
	}
	k_sleep(K_MSEC(120));

	err = bt_scan_start(BT_SCAN_TYPE_SCAN_PASSIVE);
	if (err && err != -EALREADY) {
		printk("OTA initiate: scan start failed: %d\n", err);
		ota_set_state(OTA_OP_CONNECT_FAILED, "initiate_scan_start_failed");
		return err;
	}

	ota_set_state(OTA_OP_CONNECT_PENDING, "initiate");
	ota_session_set(true, "initiate");
	printk("OTA initiate: scan restarted (passive), armed=1\n");
	return 0;
}

int master_ota_reset_target(void)
{
	int rc;

	if (!ota_runtime_active) {
		return -ENOTSUP;
	}
	if (default_conn == NULL) {
		return -ENOTCONN;
	}
	if (!ota_ready) {
		return -EAGAIN;
	}

	rc = ota_remote_reset(&dfu_smp);
	if (rc) {
		return rc;
	}

	ota_done = true;
	ota_set_state(OTA_OP_REBOOT_PENDING, "manual_reset_request");
	ota_session_set(false, "manual_reset_request");
	return 0;
}

void master_ota_prepare_mode_switch(void)
{
	int err;

	if (!ota_runtime_active) {
		return;
	}

	printk("OTA handoff: quiesce before mode switch\n");
	ota_session_set(false, "mode_handoff");
	ota_armed = false;
	ota_start_queued = false;
	ota_started = false;
	ota_ready = false;
	mtu_ready = false;
	nus_ready = false;
	sec_ready = false;

	err = bt_scan_stop();
	if (err && err != -EALREADY) {
		printk("OTA handoff: scan stop failed: %d\n", err);
	}

	if (default_conn != NULL) {
		(void)bt_conn_disconnect(default_conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
		bt_conn_unref(default_conn);
		default_conn = NULL;
	}
}

static int ota_bootstrap(void)
{
	int err;
	static bool conn_cb_registered;

	k_sem_init(&ota_sem, 0, 1);
	k_sem_init(&ota_start_sem, 0, 1);
	k_sem_init(&nus_write_sem, 0, 1);
	k_sem_init(&sec_ready_sem, 0, 1);
	k_sem_init(&smp_sub_sem, 0, 1);
	k_sem_init(&smp_write_sem, 0, 1);
	k_work_init_delayable(&dfu_ready_watchdog_work, ota_dfu_ready_watchdog_fn);
	k_work_init_delayable(&led_flow_off_work, led_flow_off_handler);
	dfu_ready_watchdog_armed = false;
	dfu_ready_watchdog_redrive_count = 0U;
	k_thread_create(&ota_thread, ota_thread_stack, K_THREAD_STACK_SIZEOF(ota_thread_stack),
			ota_thread_fn, NULL, NULL, NULL, 5, 0, K_NO_WAIT);

	err = dk_leds_init();
	if (err) {
		printk("LED init failed: %d\n", err);
	} else {
		leds_ready = true;
		master_leds_set(true, false, false, false);
		printk("LED map: 1=AUTOPOS 2=RECV 3=OTA 4=FLOW\n");
	}

	bt_dfu_smp_init(&dfu_smp, &init_params);
	ota_ready = false;
	ota_started = false;
	ota_done = false;
	ota_armed = false;
	ota_start_queued = false;
	ota_session_active = false;
	ota_upload_gate_ok = false;
	smp_notify_subscribed = false;
	smp_subscribe_err = 0;
	smp_write_err = 0;
	mtu_ready = false;
	nus_ready = false;
	ota_security_required = false;
	ota_status = 0;
	ota_seq = 1U;
	ota_runtime_active = true;

	err = bt_enable(NULL);
	if (err) {
		printk("Bluetooth init failed: %d\n", err);
		return err;
	}

	if (!conn_cb_registered) {
		bt_conn_cb_register(&conn_callbacks);
		conn_cb_registered = true;
		printk("OTA conn callbacks registered (ota mode only)\n");
	}

	if (IS_ENABLED(CONFIG_SETTINGS)) {
		settings_load();
	}

	err = nus_client_init();
	if (err) {
		printk("NUS client init failed: %d\n", err);
		return err;
	}

	err = scan_init();
	if (err) {
		return err;
	}

	printk("BioSpur BLE OTA master ready on nRF52840 DK\n");
	printk("Scanning for OTA-capable tag DFU SMP service\n");

	err = bt_scan_start(BT_SCAN_TYPE_SCAN_PASSIVE);
	if (err) {
		printk("Failed to start scan: %d\n", err);
		return err;
	}

	return 0;
}

int main(void)
{
	int err;

	ota_runtime_active = true;
	err = ota_bootstrap();
	if (err) {
		ota_runtime_active = false;
		master_leds_set(false, false, false, true);
		return err;
	}

	while (1) {
		k_sleep(K_SECONDS(1));
	}

	return 0;
}
