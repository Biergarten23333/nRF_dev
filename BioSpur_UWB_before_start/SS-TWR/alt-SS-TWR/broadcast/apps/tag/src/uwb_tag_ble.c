#include "uwb_tag_ble.h"

#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/reboot.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#include <bluetooth/services/nus.h>
#include <bluetooth/services/dfu_smp.h>

#include "ss_twr_init.h"
#include "uwb_anchor_layout.h"

#include <hal/nrf_ficr.h>

#ifndef CONFIG_BT_DEVICE_NAME
#define CONFIG_BT_DEVICE_NAME "BS_AUTO"
#endif

#ifndef APP_TAG_BLE_OTA_ENABLE
#define APP_TAG_BLE_OTA_ENABLE 1U
#endif

#ifndef APP_TAG_BLE_SETTINGS_ENABLE
#define APP_TAG_BLE_SETTINGS_ENABLE 1U
#endif

#ifndef APP_TAG_CALIBRATION_MODE
#define APP_TAG_CALIBRATION_MODE 0U
#endif

#ifndef APP_TAG_STREAM_FORCE_OFF_AT_BOOT
#define APP_TAG_STREAM_FORCE_OFF_AT_BOOT 0U
#endif

#ifndef APP_TAG_FW_MARKER
#define APP_TAG_FW_MARKER "unified-default"
#endif

#ifndef APP_TAG_BLE_PACKET_BUNDLE_RECORDS
#define APP_TAG_BLE_PACKET_BUNDLE_RECORDS 1U
#endif

#ifndef APP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS
#define APP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS 0U
#endif

#ifndef APP_TAG_ID
#define APP_TAG_ID 0U
#endif

#ifndef APP_TAG_BLE_TOKEN_ID
#define APP_TAG_BLE_TOKEN_ID APP_TAG_ID
#endif

#define UWB_TAG_BLE_DEVICE_NAME_LEN 8U
#define UWB_TAG_BLE_ADV_MFG_LEN 6U

#define UWB_TAG_BLE_MAX_STATUS_LEN 256U
/* Keep ~20% headroom so bundled NUS payloads do not sit on the limit. */
#define UWB_TAG_BLE_BUNDLE_PAYLOAD_CAP 180U
#define UWB_TAG_BLE_MAX_CMD_LEN 192U
#define UWB_TAG_BLE_TX_THREAD_STACK 1536
#define UWB_TAG_BLE_TX_THREAD_PRIO 7
#define UWB_TAG_BLE_TX_ITEM_COUNT 10U
#define UWB_TAG_BLE_TX_RETRY_MAX 4U
#define UWB_TAG_BLE_TX_RETRY_DELAY_MS 2U
#define UWB_TAG_BLE_BINARY_MAGIC0 0x42U
#define UWB_TAG_BLE_BINARY_MAGIC1 0x50U
#define UWB_TAG_BLE_BINARY_VERSION 1U
#define UWB_TAG_BLE_BINARY_HEADER_LEN 5U
#define UWB_TAG_BLE_BINARY_RECORD_LEN 24U
#define UWB_TAG_BLE_MAX_BINARY_RECORDS 4U
#define UWB_TAG_BLE_CAL_MAGIC0 0x43U
#define UWB_TAG_BLE_CAL_MAGIC1 0x4dU
#define UWB_TAG_BLE_CAL_VERSION 1U
#define UWB_TAG_BLE_CAL_HEADER_LEN 5U
#define UWB_TAG_BLE_CAL_RECORD_LEN 24U
#define UWB_TAG_BLE_MAX_CAL_RECORDS 8U

#define UWB_TAG_BLE_SETTINGS_SUBTREE "tag_ble"
#define UWB_TAG_BLE_SETTINGS_CONFIG_KEY "runtime_cfg"

struct uwb_tag_ble_settings_record {
	uint8_t valid;
	uint8_t logical_tag_id;
	uint8_t positioning_mode;
	uint8_t anchor_selection_mode;
	uint8_t fixed_anchor_count;
	uint8_t fixed_anchor_ids[UWB_TAG_FIXED_ANCHOR_MAX];
	uint8_t slot_index;
	uint8_t slot_count;
	uint16_t slot_period_ms;
	uint16_t slot_active_ms;
	uint16_t slot_mask;
	uint16_t identity_code;
	uint8_t stream_enabled;
};

struct uwb_tag_ble_settings_record_v2 {
	uint8_t valid;
	uint8_t logical_tag_id;
	uint8_t positioning_mode;
	uint8_t anchor_selection_mode;
	uint8_t fixed_anchor_count;
	uint8_t fixed_anchor_ids[UWB_TAG_FIXED_ANCHOR_MAX];
	uint8_t slot_index;
	uint8_t slot_count;
	uint16_t slot_period_ms;
	uint16_t slot_active_ms;
	uint16_t identity_code;
	uint8_t stream_enabled;
};

struct uwb_tag_ble_settings_record_v1 {
	uint8_t valid;
	uint8_t logical_tag_id;
	uint8_t positioning_mode;
	uint8_t anchor_selection_mode;
	uint8_t fixed_anchor_count;
	uint8_t fixed_anchor_ids[UWB_TAG_FIXED_ANCHOR_MAX];
	uint8_t slot_index;
	uint8_t slot_count;
	uint16_t slot_period_ms;
	uint16_t slot_active_ms;
	uint16_t identity_code;
};

struct uwb_tag_ble_tx_item {
	void *fifo_reserved;
	size_t len;
	uint8_t payload[UWB_TAG_BLE_MAX_STATUS_LEN];
};

static uint8_t adv_mfg_token[UWB_TAG_BLE_ADV_MFG_LEN] = {
	0xff, 0xff, 'B', 0x00U, 0x00U, 0x00U,
};

static const struct bt_conn_le_phy_param *const fast_phy_params = BT_CONN_LE_PHY_PARAM_2M;
static const struct bt_le_conn_param *const fast_conn_params = BT_LE_CONN_PARAM(6, 6, 0, 400);
static char ble_device_name[UWB_TAG_BLE_DEVICE_NAME_LEN];
static uint16_t ble_identity_code;
static uint8_t ble_tag_id;
static struct uwb_tag_ble_settings_record runtime_settings_record;
static bool runtime_settings_record_loaded;
static struct uwb_tag_runtime_params active_runtime_params;
static bool runtime_stream_enabled = true;

static const struct bt_data ad[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
#if APP_TAG_BLE_OTA_ENABLE
	BT_DATA_BYTES(BT_DATA_UUID128_ALL,
		      0x84, 0xaa, 0x60, 0x74, 0x52, 0x8a, 0x8b, 0x86,
		      0xd3, 0x4c, 0xb7, 0x1d, 0x1d, 0xdc, 0x53, 0x8d),
#endif
	BT_DATA(BT_DATA_MANUFACTURER_DATA, adv_mfg_token, sizeof(adv_mfg_token)),
};

static struct bt_data sd[] = {
	{
		.type = BT_DATA_NAME_COMPLETE,
		.data_len = 0U,
		.data = ble_device_name,
	},
};

static struct k_mutex ble_mutex;
static K_FIFO_DEFINE(ble_tx_fifo);
K_MEM_SLAB_DEFINE_STATIC(ble_tx_slab,
			 sizeof(struct uwb_tag_ble_tx_item),
			 UWB_TAG_BLE_TX_ITEM_COUNT,
			 4);
static K_THREAD_STACK_DEFINE(ble_tx_thread_stack, UWB_TAG_BLE_TX_THREAD_STACK);
static struct k_thread ble_tx_thread;
static struct k_work_delayable reboot_work;
static struct k_work_delayable bundle_flush_work;
static struct k_work_delayable adv_retry_work;
static bool ble_ready;
static uint8_t ble_conn_count;
static bool ota_ready;
static bool ota_active;
static uint32_t ble_tx_drop_count;
static volatile bool ble_tx_paused;
static char last_status[UWB_TAG_BLE_MAX_STATUS_LEN];
static char pending_bundle[UWB_TAG_BLE_MAX_STATUS_LEN];
static size_t pending_bundle_len;
static uint8_t pending_bundle_records;
static struct uwb_tag_ble_sample pending_samples[UWB_TAG_BLE_MAX_BINARY_RECORDS];
static uint8_t pending_sample_count;
static struct uwb_tag_ble_cal_range pending_cal_ranges[UWB_TAG_BLE_MAX_CAL_RECORDS];
static uint8_t pending_cal_count;
static struct bt_nus_cb nus_cb;

static int uwb_tag_ble_start_advertising(void);
static void uwb_tag_ble_init_identity(void);
static void uwb_tag_ble_runtime_params_reset_locked(void);
static void uwb_tag_ble_runtime_params_apply_settings_locked(void);
static const char *uwb_tag_ble_slot_source_label(uint8_t slot_source);
static bool uwb_tag_ble_parse_u32_field(const char *cmd, const char *key,
					uint32_t *value_out);
static size_t uwb_tag_ble_parse_fixed_anchor_list(const char *cmd,
						  uint8_t *anchors,
						  size_t max_count);
static int uwb_tag_ble_parse_cfg_command(
	const char *cmd,
	struct uwb_tag_runtime_params *params);
static const char *uwb_tag_ble_mode_label(uint8_t positioning_mode);
static bool uwb_tag_ble_mode_is_calibration(uint8_t positioning_mode);
static bool uwb_tag_ble_parse_mode_value(const char *mode_text,
					 uint8_t *positioning_mode_out);
static void uwb_tag_ble_apply_mode_defaults(struct uwb_tag_runtime_params *params);
static int uwb_tag_ble_apply_mode_policy(struct uwb_tag_runtime_params *params,
					 bool require_fixed_list);
static bool uwb_tag_ble_bundle_enabled(void);
static bool uwb_tag_ble_line_is_bundle_candidate(const char *line);
static void uwb_tag_ble_clear_pending_bundle_locked(void);
static bool uwb_tag_ble_snapshot_pending_bundle_locked(char *snapshot,
						       size_t snapshot_len);
static bool uwb_tag_ble_append_pending_line_locked(const char *line);
static void uwb_tag_ble_schedule_bundle_flush_locked(void);
static void uwb_tag_ble_cancel_bundle_flush(void);
static void uwb_tag_ble_flush_work_handler(struct k_work *work);
static void uwb_tag_ble_send_payload(const uint8_t *payload, size_t len);
static void uwb_tag_ble_send_text(const char *text);
static void uwb_tag_ble_tx_thread_entry(void *arg1, void *arg2, void *arg3);
static void ble_adv_retry_work_handler(struct k_work *work);
static size_t uwb_tag_ble_encode_binary_packet(uint8_t *out, size_t out_len,
					       const struct uwb_tag_ble_sample *samples,
					       size_t sample_count);
static void uwb_tag_ble_clear_pending_samples_locked(void);
static bool uwb_tag_ble_snapshot_pending_samples_locked(
	uint8_t *out, size_t out_len, size_t *encoded_len);
static size_t uwb_tag_ble_encode_cal_packet(uint8_t *out, size_t out_len,
					    const struct uwb_tag_ble_cal_range *samples,
					    size_t sample_count);
static void uwb_tag_ble_clear_pending_cal_locked(void);
static bool uwb_tag_ble_snapshot_pending_cal_locked(
	uint8_t *out, size_t out_len, size_t *encoded_len);
static bool uwb_tag_ble_runtime_stream_blocked_locked(void);
static bool uwb_tag_ble_parse_i32_token(const char *text, char **end_out,
					int32_t *value_out);

static bool uwb_tag_ble_parse_i32_token(const char *text, char **end_out,
					int32_t *value_out)
{
	char *end = NULL;
	long value;

	if (text == NULL || value_out == NULL) {
		return false;
	}

	while (*text == ' ' || *text == '\t') {
		text++;
	}
	if (*text == '\0') {
		return false;
	}

	value = strtol(text, &end, 10);
	if (end == text || value < INT32_MIN || value > INT32_MAX) {
		return false;
	}

	*value_out = (int32_t)value;
	if (end_out != NULL) {
		*end_out = end;
	}
	return true;
}

static void ble_reboot_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);

	printk("Tag BLE rebooting on remote command\n");
	sys_reboot(SYS_REBOOT_COLD);
}

static int uwb_tag_ble_runtime_settings_set(const char *key, size_t len,
					    settings_read_cb read_cb, void *cb_arg)
{
	const char *next;
	struct uwb_tag_ble_settings_record record = { 0 };
	struct uwb_tag_ble_settings_record_v2 record_v2 = { 0 };
	struct uwb_tag_ble_settings_record_v1 record_v1 = { 0 };
	int err;

	if (!settings_name_steq(key, UWB_TAG_BLE_SETTINGS_CONFIG_KEY, &next) ||
	    next != NULL) {
		return -ENOENT;
	}

	if (len != sizeof(record) && len != sizeof(record_v2) && len != sizeof(record_v1)) {
		return -EINVAL;
	}

	if (len == sizeof(record_v1)) {
		err = read_cb(cb_arg, &record_v1, sizeof(record_v1));
		if (err < 0) {
			return err;
		}
		record.valid = record_v1.valid;
		record.logical_tag_id = record_v1.logical_tag_id;
		record.positioning_mode = record_v1.positioning_mode;
		record.anchor_selection_mode = record_v1.anchor_selection_mode;
		record.fixed_anchor_count = record_v1.fixed_anchor_count;
		memcpy(record.fixed_anchor_ids, record_v1.fixed_anchor_ids,
		       sizeof(record.fixed_anchor_ids));
		record.slot_index = record_v1.slot_index;
		record.slot_count = record_v1.slot_count;
		record.slot_period_ms = record_v1.slot_period_ms;
		record.slot_active_ms = record_v1.slot_active_ms;
		record.slot_mask = 0U;
		record.identity_code = record_v1.identity_code;
		record.stream_enabled = 1U;
	} else if (len == sizeof(record_v2)) {
		err = read_cb(cb_arg, &record_v2, sizeof(record_v2));
		if (err < 0) {
			return err;
		}
		record.valid = record_v2.valid;
		record.logical_tag_id = record_v2.logical_tag_id;
		record.positioning_mode = record_v2.positioning_mode;
		record.anchor_selection_mode = record_v2.anchor_selection_mode;
		record.fixed_anchor_count = record_v2.fixed_anchor_count;
		memcpy(record.fixed_anchor_ids, record_v2.fixed_anchor_ids,
		       sizeof(record.fixed_anchor_ids));
		record.slot_index = record_v2.slot_index;
		record.slot_count = record_v2.slot_count;
		record.slot_period_ms = record_v2.slot_period_ms;
		record.slot_active_ms = record_v2.slot_active_ms;
		record.slot_mask = 0U;
		record.identity_code = record_v2.identity_code;
		record.stream_enabled = record_v2.stream_enabled;
	} else {
		err = read_cb(cb_arg, &record, sizeof(record));
	}
	if (err < 0) {
		return err;
	}

	k_mutex_lock(&ble_mutex, K_FOREVER);
	runtime_settings_record = record;
	runtime_settings_record_loaded = true;
	uwb_tag_ble_runtime_params_apply_settings_locked();
	k_mutex_unlock(&ble_mutex);

	return 0;
}

static int uwb_tag_ble_runtime_settings_export(
	int (*cb)(const char *name, const void *value, size_t val_len))
{
	struct uwb_tag_ble_settings_record record;

	k_mutex_lock(&ble_mutex, K_FOREVER);
	record = runtime_settings_record;
	k_mutex_unlock(&ble_mutex);

	if (!record.valid) {
		return 0;
	}

	return cb(UWB_TAG_BLE_SETTINGS_CONFIG_KEY, &record, sizeof(record));
}

SETTINGS_STATIC_HANDLER_DEFINE(uwb_tag_ble_runtime_settings,
			       UWB_TAG_BLE_SETTINGS_SUBTREE,
			       NULL,
			       uwb_tag_ble_runtime_settings_set,
			       NULL,
			       uwb_tag_ble_runtime_settings_export);

static void ble_adv_retry_work_handler(struct k_work *work)
{
	int err;

	ARG_UNUSED(work);

	err = uwb_tag_ble_start_advertising();
	printk("Tag BLE adv retry rc=%d\n", err);
	if (err == 0 || err == -EALREADY) {
		return;
	}

	(void)k_work_reschedule(&adv_retry_work, K_MSEC(250));
}

static void ble_init_sequence(void)
{
	int err;
	size_t id_count = 0U;

	printk("Tag BLE init work start\n");
	uwb_tag_ble_init_identity();
	err = bt_enable(NULL);
	printk("Tag BLE bt_enable rc=%d\n", err);
	if (err) {
		printk("Tag BLE init failed: %d\n", err);
		return;
	}

	if (APP_TAG_BLE_SETTINGS_ENABLE && IS_ENABLED(CONFIG_SETTINGS)) {
		printk("Tag BLE settings_load start\n");
		settings_load();
		printk("Tag BLE settings_load done\n");
		uwb_tag_ble_init_identity();
	} else {
		bt_id_get(NULL, &id_count);
		if (id_count == 0U) {
			err = bt_id_create(NULL, NULL);
			printk("Tag BLE bt_id_create rc=%d\n", err);
			if (err < 0) {
				printk("Tag BLE identity init failed: %d\n", err);
				return;
			}
		}
	}

	printk("Tag BLE set name skipped; using auto name=%s\n",
	       ble_device_name);

	printk("Tag BLE NUS init start\n");
	err = bt_nus_init(&nus_cb);
	printk("Tag BLE NUS init rc=%d\n", err);
	if (err) {
		printk("Tag BLE NUS register failed: %d\n", err);
		return;
	}

	printk("Tag BLE adv start request\n");
	err = uwb_tag_ble_start_advertising();
	printk("Tag BLE adv start rc=%d\n", err);
	if (err) {
		printk("Tag BLE advertising deferred: %d\n", err);
		(void)k_work_reschedule(&adv_retry_work, K_MSEC(250));
	}

	ble_ready = true;
	printk("Tag BLE advertising as %s\n", ble_device_name);
}

static void uwb_tag_ble_init_identity(void)
{
	uint32_t seed0 = NRF_FICR->DEVICEID[0];
	uint32_t seed1 = NRF_FICR->DEVICEID[1];
	uint32_t folded = seed0 ^ seed1 ^ (seed0 >> 16) ^ (seed1 << 1);
	uint16_t code = (uint16_t)(((folded >> 16) ^ folded) & 0xFFFFU);
	int len;

	k_mutex_lock(&ble_mutex, K_FOREVER);
	if (runtime_settings_record_loaded && runtime_settings_record.valid &&
	    runtime_settings_record.identity_code != 0U) {
		code = runtime_settings_record.identity_code;
	}
	ble_identity_code = code;
	ble_tag_id = (uint8_t)(code & 0xFFU);
	adv_mfg_token[3] = ble_tag_id;
	adv_mfg_token[4] = (uint8_t)(code & 0xFFU);
	adv_mfg_token[5] = (uint8_t)((code >> 8) & 0xFFU);
	if (active_runtime_params.identity_code == 0U) {
		active_runtime_params.identity_code = code;
	}
	if (active_runtime_params.logical_tag_id == 0U) {
		active_runtime_params.logical_tag_id = ble_tag_id;
	}
	k_mutex_unlock(&ble_mutex);
	len = snprintk(ble_device_name, sizeof(ble_device_name), "BS%04X", code);
	if (len < 0) {
		memcpy(ble_device_name, "BS0000", sizeof("BS0000"));
		len = (int)(sizeof("BS0000") - 1);
	}

	sd[0].data_len = (uint8_t)len;
	printk("Tag BLE auto identity seed=%08x%08x name=%s\n",
	       (unsigned int)seed1, (unsigned int)seed0, ble_device_name);
}

uint16_t uwb_tag_ble_identity_code(void)
{
	return ble_identity_code;
}

uint8_t uwb_tag_ble_tag_id(void)
{
	return ble_tag_id;
}

const char *uwb_tag_ble_device_name(void)
{
	return ble_device_name;
}

static void uwb_tag_ble_runtime_params_reset_locked(void)
{
	memset(&active_runtime_params, 0, sizeof(active_runtime_params));
	active_runtime_params.identity_code = ble_identity_code;
	active_runtime_params.logical_tag_id =
		(ble_identity_code != 0U) ? (uint8_t)(ble_identity_code & 0xFFU) :
					    ble_tag_id;
	active_runtime_params.slot_source = UWB_TAG_SLOT_SOURCE_BUILD;
	active_runtime_params.positioning_mode = APP_TAG_CALIBRATION_MODE ?
		UWB_TAG_POSITIONING_MODE_CAL_STATIC :
		UWB_TAG_POSITIONING_MODE_DYNAMIC;
	active_runtime_params.anchor_selection_mode =
		UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2;
	active_runtime_params.tdma.enabled = (APP_TAG_TDMA_ENABLE != 0U);
	active_runtime_params.tdma.slot_index = APP_TAG_TDMA_SLOT_INDEX;
	active_runtime_params.tdma.slot_count = APP_TAG_TDMA_SLOT_COUNT;
	active_runtime_params.tdma.slot_mask = 0U;
	active_runtime_params.tdma.slot_period_ms = APP_TAG_TDMA_SLOT_PERIOD_MS;
	active_runtime_params.tdma.slot_active_ms = APP_TAG_TDMA_SLOT_ACTIVE_MS;
	active_runtime_params.tdma.epoch_ms = 0U;
	active_runtime_params.tdma.sync_local_ms = 0U;
	active_runtime_params.tdma.epoch_valid = false;
	active_runtime_params.tdma.generation = 0U;
}

static void uwb_tag_ble_runtime_params_apply_settings_locked(void)
{
	uwb_tag_ble_runtime_params_reset_locked();
	runtime_stream_enabled = true;
	if (!runtime_settings_record_loaded || !runtime_settings_record.valid) {
		if (APP_TAG_STREAM_FORCE_OFF_AT_BOOT != 0U) {
			runtime_stream_enabled = false;
		}
		return;
	}

	runtime_stream_enabled = (runtime_settings_record.stream_enabled != 0U);
	if (APP_TAG_STREAM_FORCE_OFF_AT_BOOT != 0U) {
		runtime_stream_enabled = false;
	}

	if (runtime_settings_record.identity_code != 0U) {
		active_runtime_params.identity_code =
			runtime_settings_record.identity_code;
	}
	if (runtime_settings_record.logical_tag_id != 0U) {
		active_runtime_params.logical_tag_id =
			runtime_settings_record.logical_tag_id;
	}

	active_runtime_params.positioning_mode =
		(runtime_settings_record.positioning_mode == UWB_TAG_POSITIONING_MODE_RESERVED_2) ?
			UWB_TAG_POSITIONING_MODE_CAL_STATIC :
			runtime_settings_record.positioning_mode;
	active_runtime_params.anchor_selection_mode =
		runtime_settings_record.anchor_selection_mode;
	active_runtime_params.fixed_anchor_count =
		MIN(runtime_settings_record.fixed_anchor_count,
		    UWB_TAG_FIXED_ANCHOR_MAX);
	memcpy(active_runtime_params.fixed_anchor_ids,
	       runtime_settings_record.fixed_anchor_ids,
	       sizeof(active_runtime_params.fixed_anchor_ids));

	if (runtime_settings_record.slot_count != 0U &&
	    runtime_settings_record.slot_period_ms != 0U &&
	    runtime_settings_record.slot_active_ms != 0U) {
		active_runtime_params.slot_source = UWB_TAG_SLOT_SOURCE_SETTINGS;
		active_runtime_params.tdma.enabled = true;
		active_runtime_params.tdma.slot_index =
			runtime_settings_record.slot_index;
		active_runtime_params.tdma.slot_count =
			runtime_settings_record.slot_count;
		active_runtime_params.tdma.slot_mask =
			runtime_settings_record.slot_mask;
		active_runtime_params.tdma.slot_period_ms =
			runtime_settings_record.slot_period_ms;
		active_runtime_params.tdma.slot_active_ms =
			runtime_settings_record.slot_active_ms;
	}
}

bool uwb_tag_ble_runtime_config_get(struct uwb_tag_runtime_params *params)
{
	if (params == NULL) {
		return false;
	}

	k_mutex_lock(&ble_mutex, K_FOREVER);
	*params = active_runtime_params;
	k_mutex_unlock(&ble_mutex);
	return true;
}

int uwb_tag_ble_runtime_config_store(const struct uwb_tag_runtime_params *params)
{
	struct uwb_tag_ble_settings_record record = { 0 };
	int err = 0;

	if (params == NULL) {
		return -EINVAL;
	}

	record.valid = 1U;
	record.logical_tag_id = params->logical_tag_id;
	record.positioning_mode = params->positioning_mode;
	record.anchor_selection_mode = params->anchor_selection_mode;
	record.fixed_anchor_count =
		MIN(params->fixed_anchor_count, UWB_TAG_FIXED_ANCHOR_MAX);
	memcpy(record.fixed_anchor_ids, params->fixed_anchor_ids,
	       sizeof(record.fixed_anchor_ids));
	record.slot_index = params->tdma.slot_index;
	record.slot_count = params->tdma.slot_count;
	record.slot_mask = params->tdma.slot_mask;
	record.slot_period_ms = params->tdma.slot_period_ms;
	record.slot_active_ms = params->tdma.slot_active_ms;
	record.identity_code = params->identity_code;
	record.stream_enabled = runtime_stream_enabled ? 1U : 0U;

	k_mutex_lock(&ble_mutex, K_FOREVER);
	runtime_settings_record = record;
	runtime_settings_record_loaded = true;
	active_runtime_params = *params;
	k_mutex_unlock(&ble_mutex);

	if (IS_ENABLED(CONFIG_SETTINGS)) {
		err = settings_save_one(UWB_TAG_BLE_SETTINGS_SUBTREE "/"
					UWB_TAG_BLE_SETTINGS_CONFIG_KEY,
					&record, sizeof(record));
		if (err) {
			printk("Tag BLE runtime config save skipped/failed: %d\n", err);
		}
	}

	return 0;
}

static const char *uwb_tag_ble_mode_label(uint8_t positioning_mode)
{
	switch (positioning_mode) {
	case UWB_TAG_POSITIONING_MODE_CAL_STATIC:
		return "CAL_STATIC";
	case UWB_TAG_POSITIONING_MODE_CAL_ROTO:
		return "CAL_ROTO";
	case UWB_TAG_POSITIONING_MODE_FIXED:
		return "FIXED";
	case UWB_TAG_POSITIONING_MODE_ANCHOR_OTA:
		return "AOTA";
	default:
		return "MOTION";
	}
}

static bool uwb_tag_ble_mode_is_calibration(uint8_t positioning_mode)
{
	return positioning_mode == UWB_TAG_POSITIONING_MODE_CAL_STATIC ||
	       positioning_mode == UWB_TAG_POSITIONING_MODE_CAL_ROTO;
}

static bool uwb_tag_ble_mode_is_anchor_ota(uint8_t positioning_mode)
{
	return positioning_mode == UWB_TAG_POSITIONING_MODE_ANCHOR_OTA;
}

static bool uwb_tag_ble_parse_mode_value(const char *mode_text,
					 uint8_t *positioning_mode_out)
{
	if (mode_text == NULL || positioning_mode_out == NULL) {
		return false;
	}

	if (strcasecmp(mode_text, "CAL") == 0 ||
	    strcasecmp(mode_text, "CAL_STATIC") == 0 ||
	    strcasecmp(mode_text, "STATIC_CAL") == 0 ||
	    strcasecmp(mode_text, "CALI") == 0) {
		*positioning_mode_out = UWB_TAG_POSITIONING_MODE_CAL_STATIC;
		return true;
	}

	if (strcasecmp(mode_text, "CAL_ROTO") == 0 ||
	    strcasecmp(mode_text, "ROTO_CAL") == 0 ||
	    strcasecmp(mode_text, "ROTO") == 0) {
		*positioning_mode_out = UWB_TAG_POSITIONING_MODE_CAL_ROTO;
		return true;
	}

	if (strcasecmp(mode_text, "MOTION") == 0 ||
	    strcasecmp(mode_text, "DYN") == 0 ||
	    strcasecmp(mode_text, "DYNAMIC") == 0 ||
	    strcasecmp(mode_text, "RUN") == 0) {
		*positioning_mode_out = UWB_TAG_POSITIONING_MODE_DYNAMIC;
		return true;
	}

	if (strcasecmp(mode_text, "FIXED") == 0) {
		*positioning_mode_out = UWB_TAG_POSITIONING_MODE_FIXED;
		return true;
	}

	if (strcasecmp(mode_text, "AOTA") == 0 ||
	    strcasecmp(mode_text, "ANCHOR_OTA") == 0 ||
	    strcasecmp(mode_text, "OTA_IDLE") == 0) {
		*positioning_mode_out = UWB_TAG_POSITIONING_MODE_ANCHOR_OTA;
		return true;
	}

	return false;
}

static void uwb_tag_ble_apply_mode_defaults(struct uwb_tag_runtime_params *params)
{
	if (params == NULL) {
		return;
	}

	if (uwb_tag_ble_mode_is_anchor_ota(params->positioning_mode)) {
		params->slot_source = UWB_TAG_SLOT_SOURCE_BUILD;
		params->tdma.enabled = false;
		params->tdma.slot_index = 0U;
		params->tdma.slot_count = 1U;
		params->tdma.slot_mask = 0U;
		params->tdma.slot_period_ms = 25U;
		params->tdma.slot_active_ms = 25U;
		params->tdma.epoch_valid = false;
		params->tdma.epoch_ms = 0U;
		params->tdma.generation = 0U;
		return;
	}

	if (uwb_tag_ble_mode_is_calibration(params->positioning_mode)) {
		if (params->tdma.slot_count == 0U || params->tdma.slot_period_ms == 0U ||
		    params->tdma.slot_active_ms == 0U) {
			params->tdma.enabled = (APP_TAG_TDMA_ENABLE != 0U);
			params->tdma.slot_index = APP_TAG_TDMA_SLOT_INDEX;
			params->tdma.slot_count = APP_TAG_TDMA_SLOT_COUNT;
			params->tdma.slot_mask = 0U;
			params->tdma.slot_period_ms = APP_TAG_TDMA_SLOT_PERIOD_MS;
			params->tdma.slot_active_ms = APP_TAG_TDMA_SLOT_ACTIVE_MS;
			params->tdma.epoch_valid = false;
			params->tdma.epoch_ms = 0U;
			params->tdma.generation = 0U;
		}
		return;
	}

	if (params->tdma.slot_count == 0U || params->tdma.slot_period_ms == 0U ||
	    params->tdma.slot_active_ms == 0U) {
		params->tdma.enabled = (APP_TAG_TDMA_ENABLE != 0U);
		params->tdma.slot_index = APP_TAG_TDMA_SLOT_INDEX;
		params->tdma.slot_count = APP_TAG_TDMA_SLOT_COUNT;
		params->tdma.slot_mask = 0U;
		params->tdma.slot_period_ms = APP_TAG_TDMA_SLOT_PERIOD_MS;
		params->tdma.slot_active_ms = APP_TAG_TDMA_SLOT_ACTIVE_MS;
		params->tdma.epoch_valid = false;
		params->tdma.epoch_ms = 0U;
		params->tdma.generation = 0U;
	}
}

static int uwb_tag_ble_apply_mode_policy(struct uwb_tag_runtime_params *params,
					 bool require_fixed_list)
{
	if (params == NULL) {
		return -EINVAL;
	}

	switch (params->positioning_mode) {
	case UWB_TAG_POSITIONING_MODE_CAL_STATIC:
		/* CAL_STATIC owns its sweep coverage; stale fixed lists are ignored. */
		params->anchor_selection_mode = UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2;
		params->fixed_anchor_count = 0U;
		memset(params->fixed_anchor_ids, 0, sizeof(params->fixed_anchor_ids));
		break;
	case UWB_TAG_POSITIONING_MODE_CAL_ROTO:
		/* CAL_ROTO owns anchor selection; quality affects qf, not output count. */
		params->anchor_selection_mode = UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2;
		params->fixed_anchor_count = 0U;
		memset(params->fixed_anchor_ids, 0, sizeof(params->fixed_anchor_ids));
		break;
	case UWB_TAG_POSITIONING_MODE_ANCHOR_OTA:
		/*
		 * Anchor-OTA coordination mode:
		 * - keep BLE control path alive
		 * - force runtime into no-poll behavior on the UWB loop
		 */
		params->anchor_selection_mode = UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2;
		params->fixed_anchor_count = 0U;
		memset(params->fixed_anchor_ids, 0, sizeof(params->fixed_anchor_ids));
		break;
	case UWB_TAG_POSITIONING_MODE_FIXED:
		/* Fixed mode: always 4-anchor subset policy. */
		params->anchor_selection_mode = UWB_TAG_ANCHOR_SELECTION_FIXED_SUBSET;
		if (params->fixed_anchor_count < 4U) {
			if (require_fixed_list) {
				return -EINVAL;
			}
			return -EAGAIN;
		}
		break;
	case UWB_TAG_POSITIONING_MODE_DYNAMIC:
	default:
		/* Motion dynamic: anchor plan comes from dynamic strategy (6->4 style runtime). */
		params->anchor_selection_mode = UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2;
		break;
	}

	uwb_tag_ble_apply_mode_defaults(params);
	return 0;
}

bool uwb_tag_ble_tdma_slot_override_get(uint8_t *slot_index)
{
	bool valid;

	k_mutex_lock(&ble_mutex, K_FOREVER);
	valid = active_runtime_params.tdma.enabled &&
		active_runtime_params.tdma.slot_count != 0U;
	if (valid && slot_index != NULL) {
		*slot_index = active_runtime_params.tdma.slot_index;
	}
	k_mutex_unlock(&ble_mutex);

	return valid;
}

int uwb_tag_ble_tdma_slot_override_store(uint8_t slot_index)
{
	struct uwb_tag_runtime_params params;

	(void)uwb_tag_ble_runtime_config_get(&params);
	params.slot_source = UWB_TAG_SLOT_SOURCE_SETTINGS;
	params.tdma.enabled = true;
	params.tdma.slot_index = slot_index;
	if (params.tdma.slot_count == 0U) {
		params.tdma.slot_count = APP_TAG_TDMA_SLOT_COUNT;
	}
	if (params.tdma.slot_period_ms == 0U) {
		params.tdma.slot_period_ms = APP_TAG_TDMA_SLOT_PERIOD_MS;
	}
	if (params.tdma.slot_active_ms == 0U) {
		params.tdma.slot_active_ms = APP_TAG_TDMA_SLOT_ACTIVE_MS;
	}

	return uwb_tag_ble_runtime_config_store(&params);
}

static const char *uwb_tag_ble_slot_source_label(uint8_t slot_source)
{
	switch (slot_source) {
	case UWB_TAG_SLOT_SOURCE_MASTER:
		return "MASTER";
	case UWB_TAG_SLOT_SOURCE_SETTINGS:
		return "SETTINGS";
	default:
		return "BUILD";
	}
}

static bool uwb_tag_ble_parse_u32_field(const char *cmd, const char *key,
					uint32_t *value_out)
{
	const char *pos;
	char *end = NULL;
	unsigned long value;

	if (cmd == NULL || key == NULL || value_out == NULL) {
		return false;
	}

	pos = strstr(cmd, key);
	if (pos == NULL) {
		return false;
	}

	pos += strlen(key);
	value = strtoul(pos, &end, 0);
	if (end == pos) {
		return false;
	}

	*value_out = (uint32_t)value;
	return true;
}

static size_t uwb_tag_ble_parse_fixed_anchor_list(const char *cmd,
						  uint8_t *anchors,
						  size_t max_count)
{
	const char *pos;
	size_t count = 0U;

	if (cmd == NULL || anchors == NULL || max_count == 0U) {
		return 0U;
	}

	pos = strstr(cmd, "FIXED=");
	if (pos == NULL) {
		return 0U;
	}

	pos += strlen("FIXED=");
	while (*pos != '\0' && count < max_count) {
		char *end = NULL;
		unsigned long value = strtoul(pos, &end, 10);

		if (end == pos || value > UINT8_MAX) {
			break;
		}

		anchors[count++] = (uint8_t)value;
		if (*end != ',') {
			break;
		}
		pos = end + 1;
	}

	return count;
}

static int uwb_tag_ble_parse_cfg_command(
	const char *cmd,
	struct uwb_tag_runtime_params *params)
{
	uint32_t tag_id = 0U;
	uint32_t slot = 0U;
	uint32_t count = 0U;
	uint32_t period = 0U;
	uint32_t active = 0U;
	uint32_t slot_mask = 0U;
	uint32_t epoch = 0U;
	uint32_t generation = 0U;
	uint32_t positioning_mode = UWB_TAG_POSITIONING_MODE_DYNAMIC;
	uint32_t anchor_mode = UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2;
	uint8_t fixed_anchor_ids[UWB_TAG_FIXED_ANCHOR_MAX] = { 0U };
	size_t fixed_count;

	if (cmd == NULL || params == NULL) {
		return -EINVAL;
	}

	if (!uwb_tag_ble_parse_u32_field(cmd, "TAG=", &tag_id) ||
	    !uwb_tag_ble_parse_u32_field(cmd, "SLOT=", &slot) ||
	    !uwb_tag_ble_parse_u32_field(cmd, "COUNT=", &count) ||
	    !uwb_tag_ble_parse_u32_field(cmd, "PERIOD=", &period) ||
	    !uwb_tag_ble_parse_u32_field(cmd, "ACTIVE=", &active) ||
	    !uwb_tag_ble_parse_u32_field(cmd, "EPOCH=", &epoch)) {
		return -EINVAL;
	}

	(void)uwb_tag_ble_parse_u32_field(cmd, "GEN=", &generation);
	(void)uwb_tag_ble_parse_u32_field(cmd, "MASK=", &slot_mask);
	(void)uwb_tag_ble_parse_u32_field(cmd, "PMODE=", &positioning_mode);
	(void)uwb_tag_ble_parse_u32_field(cmd, "AMODE=", &anchor_mode);
	fixed_count = uwb_tag_ble_parse_fixed_anchor_list(cmd, fixed_anchor_ids,
							  ARRAY_SIZE(fixed_anchor_ids));

	if (tag_id >= UWB_MAX_TAGS || slot >= UINT8_MAX || count == 0U ||
	    count > UINT8_MAX || period == 0U || period > UINT16_MAX ||
	    active == 0U || active > UINT16_MAX || active > period ||
	    slot_mask > UINT16_MAX ||
	    (positioning_mode != UWB_TAG_POSITIONING_MODE_DYNAMIC &&
	     positioning_mode != UWB_TAG_POSITIONING_MODE_FIXED &&
	     positioning_mode != UWB_TAG_POSITIONING_MODE_ANCHOR_OTA &&
	     positioning_mode != UWB_TAG_POSITIONING_MODE_CAL_STATIC &&
	     positioning_mode != UWB_TAG_POSITIONING_MODE_CAL_ROTO) ||
	    anchor_mode > UWB_TAG_ANCHOR_SELECTION_FIXED_SUBSET) {
		return -ERANGE;
	}

	(void)uwb_tag_ble_runtime_config_get(params);
	params->logical_tag_id = (uint8_t)tag_id;
	params->slot_source = UWB_TAG_SLOT_SOURCE_MASTER;
	params->positioning_mode = (uint8_t)positioning_mode;
	params->anchor_selection_mode = (uint8_t)anchor_mode;
	params->fixed_anchor_count = MIN(fixed_count, UWB_TAG_FIXED_ANCHOR_MAX);
	memset(params->fixed_anchor_ids, 0, sizeof(params->fixed_anchor_ids));
	memcpy(params->fixed_anchor_ids, fixed_anchor_ids,
	       sizeof(fixed_anchor_ids));
	params->tdma.enabled = true;
	params->tdma.slot_index = (uint8_t)slot;
	params->tdma.slot_count = (uint8_t)count;
	params->tdma.slot_mask = (uint16_t)slot_mask;
	params->tdma.slot_period_ms = (uint16_t)period;
	params->tdma.slot_active_ms = (uint16_t)active;
	params->tdma.epoch_ms = epoch;
	params->tdma.epoch_valid = true;
	params->tdma.generation = (uint8_t)generation;
	if (uwb_tag_ble_apply_mode_policy(
		params, params->positioning_mode == UWB_TAG_POSITIONING_MODE_FIXED) != 0) {
		return -EINVAL;
	}

	return 0;
}

static int uwb_tag_ble_start_advertising(void)
{
	int err;
	const struct bt_le_adv_param *params = BT_LE_ADV_CONN;

	for (int attempt = 0; attempt < 10; ++attempt) {
		(void)bt_le_adv_stop();
		k_msleep(50);
		err = bt_le_adv_start(params, ad, ARRAY_SIZE(ad), sd, ARRAY_SIZE(sd));
		if (err == -EALREADY) {
			return 0;
		}

		if (err != -EAGAIN) {
			return err;
		}

		k_msleep(150);
	}

	return err;
}

static bool uwb_tag_ble_bundle_enabled(void)
{
	return (APP_TAG_BLE_PACKET_BUNDLE_RECORDS > 1U) ||
	       (APP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS > 0U);
}

static bool uwb_tag_ble_line_is_bundle_candidate(const char *line)
{
	return (line != NULL) && (strstr(line, "TagSummary") != NULL ||
				  strstr(line, "TS ") != NULL ||
				  strstr(line, "TS;") != NULL);
}

static void uwb_tag_ble_schedule_bundle_flush_locked(void)
{
	if (APP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS == 0U ||
	    (pending_bundle_records == 0U && pending_sample_count == 0U)) {
		return;
	}

	(void)k_work_reschedule(&bundle_flush_work,
				K_MSEC(APP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS));
}

static void uwb_tag_ble_cancel_bundle_flush(void)
{
	if (APP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS == 0U) {
		return;
	}

	(void)k_work_cancel_delayable(&bundle_flush_work);
}

static bool uwb_tag_ble_append_pending_line_locked(const char *line)
{
	size_t line_len;

	if (line == NULL) {
		return false;
	}

	line_len = strlen(line);
	if (line_len == 0U) {
		return true;
	}

	if (pending_bundle_records > 0U) {
		if (pending_bundle_len + 1U + line_len >= sizeof(pending_bundle)) {
			return false;
		}
		pending_bundle[pending_bundle_len++] = '|';
	}

	memcpy(&pending_bundle[pending_bundle_len], line, line_len + 1U);
	pending_bundle_len += line_len;
	pending_bundle_records++;
	return true;
}

static void uwb_tag_ble_clear_pending_bundle_locked(void)
{
	pending_bundle[0] = '\0';
	pending_bundle_len = 0U;
	pending_bundle_records = 0U;
}

static void uwb_tag_ble_clear_pending_samples_locked(void)
{
	pending_sample_count = 0U;
}

static void uwb_tag_ble_clear_pending_cal_locked(void)
{
	pending_cal_count = 0U;
}

static bool uwb_tag_ble_snapshot_pending_bundle_locked(char *snapshot,
						       size_t snapshot_len)
{
	size_t copy_len;

	if (snapshot == NULL || snapshot_len == 0U ||
	    pending_bundle_records == 0U) {
		return false;
	}

	copy_len = MIN(pending_bundle_len, snapshot_len - 1U);
	memcpy(snapshot, pending_bundle, copy_len);
	snapshot[copy_len] = '\0';
	return true;
}

static size_t uwb_tag_ble_encode_binary_packet(uint8_t *out, size_t out_len,
					       const struct uwb_tag_ble_sample *samples,
					       size_t sample_count)
{
	size_t offset = 0U;

	if (out == NULL || samples == NULL || sample_count == 0U ||
	    sample_count > UWB_TAG_BLE_MAX_BINARY_RECORDS) {
		return 0U;
	}

	if (out_len < UWB_TAG_BLE_BINARY_HEADER_LEN +
			  sample_count * UWB_TAG_BLE_BINARY_RECORD_LEN) {
		return 0U;
	}

	out[offset++] = UWB_TAG_BLE_BINARY_MAGIC0;
	out[offset++] = UWB_TAG_BLE_BINARY_MAGIC1;
	out[offset++] = UWB_TAG_BLE_BINARY_VERSION;
	out[offset++] = (uint8_t)sample_count;
	out[offset++] = uwb_tag_ble_tag_id();

	for (size_t i = 0U; i < sample_count; ++i) {
		const struct uwb_tag_ble_sample *sample = &samples[i];

		sys_put_le32(sample->sweep, &out[offset]);
		offset += 4U;
		out[offset++] = sample->plan_code;
		out[offset++] = sample->anchor_mask;
		sys_put_le16(sample->motion_valid ? sample->motion_dt_ms : 0U,
			     &out[offset]);
		offset += 2U;
		sys_put_le32((uint32_t)sample->x_mm, &out[offset]);
		offset += 4U;
		sys_put_le32((uint32_t)sample->y_mm, &out[offset]);
		offset += 4U;
		sys_put_le32((uint32_t)sample->z_mm, &out[offset]);
		offset += 4U;
		sys_put_le16(sample->rms_mm, &out[offset]);
		offset += 2U;
		sys_put_le16(sample->max_mm, &out[offset]);
		offset += 2U;
	}

	return offset;
}

static bool uwb_tag_ble_snapshot_pending_samples_locked(
	uint8_t *out, size_t out_len, size_t *encoded_len)
{
	size_t len;

	if (pending_sample_count == 0U || out == NULL || encoded_len == NULL) {
		return false;
	}

	len = uwb_tag_ble_encode_binary_packet(out, out_len, pending_samples,
						 pending_sample_count);
	if (len == 0U) {
		return false;
	}

	*encoded_len = len;
	return true;
}

static size_t uwb_tag_ble_encode_cal_packet(
	uint8_t *out, size_t out_len,
	const struct uwb_tag_ble_cal_range *samples, size_t sample_count)
{
	size_t offset = 0U;

	if (out == NULL || samples == NULL || sample_count == 0U ||
	    sample_count > UWB_TAG_BLE_MAX_CAL_RECORDS) {
		return 0U;
	}

	if (out_len < UWB_TAG_BLE_CAL_HEADER_LEN +
			  sample_count * UWB_TAG_BLE_CAL_RECORD_LEN) {
		return 0U;
	}

	out[offset++] = UWB_TAG_BLE_CAL_MAGIC0;
	out[offset++] = UWB_TAG_BLE_CAL_MAGIC1;
	out[offset++] = UWB_TAG_BLE_CAL_VERSION;
	out[offset++] = (uint8_t)sample_count;
	out[offset++] = uwb_tag_ble_tag_id();

	for (size_t i = 0U; i < sample_count; ++i) {
		const struct uwb_tag_ble_cal_range *sample = &samples[i];

		sys_put_le32(sample->sweep, &out[offset]);
		offset += 4U;
		out[offset++] = sample->anchor_id;
		out[offset++] = sample->status;
		out[offset++] = sample->quality_percent;
		out[offset++] = 0U;
		sys_put_le32((uint32_t)sample->raw_mm, &out[offset]);
		offset += 4U;
		sys_put_le32(sample->filt_mm, &out[offset]);
		offset += 4U;
		sys_put_le32(sample->ok_count, &out[offset]);
		offset += 4U;
		sys_put_le32(sample->fail_count, &out[offset]);
		offset += 4U;
	}

	return offset;
}

static bool uwb_tag_ble_snapshot_pending_cal_locked(
	uint8_t *out, size_t out_len, size_t *encoded_len)
{
	size_t len;

	if (pending_cal_count == 0U || out == NULL || encoded_len == NULL) {
		return false;
	}

	len = uwb_tag_ble_encode_cal_packet(out, out_len, pending_cal_ranges,
					    pending_cal_count);
	if (len == 0U) {
		return false;
	}

	*encoded_len = len;
	return true;
}

static bool uwb_tag_ble_runtime_stream_blocked_locked(void)
{
#if APP_TAG_BLE_OTA_ENABLE
	return ota_active;
#else
	return false;
#endif
}

static void uwb_tag_ble_flush_work_handler(struct k_work *work)
{
	char snapshot[UWB_TAG_BLE_MAX_STATUS_LEN];
	uint8_t binary_snapshot[UWB_TAG_BLE_MAX_STATUS_LEN];
	size_t binary_len = 0U;

	ARG_UNUSED(work);

	k_mutex_lock(&ble_mutex, K_FOREVER);
	if (uwb_tag_ble_runtime_stream_blocked_locked()) {
		uwb_tag_ble_clear_pending_cal_locked();
		uwb_tag_ble_clear_pending_samples_locked();
		uwb_tag_ble_clear_pending_bundle_locked();
		k_mutex_unlock(&ble_mutex);
		return;
	}

	if (uwb_tag_ble_snapshot_pending_cal_locked(binary_snapshot,
						    sizeof(binary_snapshot),
						    &binary_len)) {
		uwb_tag_ble_clear_pending_cal_locked();
		k_mutex_unlock(&ble_mutex);
		uwb_tag_ble_send_payload(binary_snapshot, binary_len);
		return;
	}

	if (uwb_tag_ble_snapshot_pending_samples_locked(binary_snapshot,
							sizeof(binary_snapshot),
							&binary_len)) {
		uwb_tag_ble_clear_pending_samples_locked();
		k_mutex_unlock(&ble_mutex);
		uwb_tag_ble_send_payload(binary_snapshot, binary_len);
		return;
	}

	if (!uwb_tag_ble_snapshot_pending_bundle_locked(snapshot,
						       sizeof(snapshot))) {
		k_mutex_unlock(&ble_mutex);
		return;
	}

	uwb_tag_ble_clear_pending_bundle_locked();
	k_mutex_unlock(&ble_mutex);

	uwb_tag_ble_send_text(snapshot);
}

static void ble_connected(struct bt_conn *conn, uint8_t conn_err)
{
	char addr[BT_ADDR_LE_STR_LEN];
	int err;
	uint8_t active_conns;

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));

	if (conn_err) {
		printk("Tag BLE connect failed: %s err=0x%02x\n", addr, conn_err);
		return;
	}

	k_mutex_lock(&ble_mutex, K_FOREVER);
	ble_conn_count++;
	active_conns = ble_conn_count;
	k_mutex_unlock(&ble_mutex);

	printk("Tag BLE connected: %s active=%u\n", addr,
	       (unsigned int)active_conns);
	err = bt_conn_le_phy_update(conn, fast_phy_params);
	printk("Tag BLE PHY update request rc=%d\n", err);
	err = bt_conn_le_param_update(conn, fast_conn_params);
	printk("Tag BLE conn param update request rc=%d\n", err);
}

static void ble_disconnected(struct bt_conn *conn, uint8_t reason)
{
	char addr[BT_ADDR_LE_STR_LEN];
	int err;
	uint8_t active_conns;

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	k_mutex_lock(&ble_mutex, K_FOREVER);
	if (ble_conn_count > 0U) {
		ble_conn_count--;
	}
	active_conns = ble_conn_count;
	uwb_tag_ble_clear_pending_bundle_locked();
	uwb_tag_ble_clear_pending_samples_locked();
	uwb_tag_ble_clear_pending_cal_locked();
	k_mutex_unlock(&ble_mutex);
	uwb_tag_ble_cancel_bundle_flush();

	printk("Tag BLE disconnected: %s reason=0x%02x active=%u\n", addr, reason,
	       (unsigned int)active_conns);
	ota_active = false;
	ota_ready = false;

	err = uwb_tag_ble_start_advertising();
	printk("Tag BLE adv resume rc=%d\n", err);
	if (err && err != -EALREADY) {
		(void)k_work_reschedule(&adv_retry_work, K_MSEC(250));
	}
}

BT_CONN_CB_DEFINE(uwb_tag_ble_conn_cb) = {
	.connected = ble_connected,
	.disconnected = ble_disconnected,
};

static void uwb_tag_ble_send_payload(const uint8_t *payload, size_t len)
{
	struct uwb_tag_ble_tx_item *item;
	int alloc_err;

	if (!ble_ready || payload == NULL || len == 0U ||
	    len > UWB_TAG_BLE_MAX_STATUS_LEN) {
		return;
	}

	alloc_err = k_mem_slab_alloc(&ble_tx_slab, (void **)&item, K_NO_WAIT);
	if (alloc_err != 0) {
		ble_tx_drop_count++;
		if ((ble_tx_drop_count % 50U) == 1U) {
			printk("Tag BLE tx pool exhausted drops=%u\n",
			       (unsigned int)ble_tx_drop_count);
		}
		return;
	}

	memcpy(item->payload, payload, len);
	item->len = len;
	k_fifo_put(&ble_tx_fifo, item);
}

static void uwb_tag_ble_send_text(const char *text)
{
	if (text == NULL || text[0] == '\0') {
		return;
	}

	uwb_tag_ble_send_payload((const uint8_t *)text, strlen(text));
}

static void uwb_tag_ble_purge_tx_queue(void)
{
	struct uwb_tag_ble_tx_item *item;

	while ((item = k_fifo_get(&ble_tx_fifo, K_NO_WAIT)) != NULL) {
		k_mem_slab_free(&ble_tx_slab, (void *)item);
	}
}

static void uwb_tag_ble_tx_thread_entry(void *arg1, void *arg2, void *arg3)
{
	ARG_UNUSED(arg1);
	ARG_UNUSED(arg2);
	ARG_UNUSED(arg3);

	while (true) {
		struct uwb_tag_ble_tx_item *item =
			k_fifo_get(&ble_tx_fifo, K_FOREVER);
		uint8_t active_conns;
		int err;

		if (item == NULL) {
			continue;
		}

		while (ble_tx_paused) {
			k_msleep(1);
		}

		k_mutex_lock(&ble_mutex, K_FOREVER);
		active_conns = ble_conn_count;
		k_mutex_unlock(&ble_mutex);

		if (active_conns > 0U) {
			err = -EAGAIN;
			for (uint8_t attempt = 0U; attempt < UWB_TAG_BLE_TX_RETRY_MAX; ++attempt) {
				err = bt_nus_send(NULL, item->payload, item->len);
				if (err == 0 || err == -ENOTCONN) {
					break;
				}
				if (err == -ENOMEM || err == -EAGAIN || err == -EBUSY) {
					k_msleep(UWB_TAG_BLE_TX_RETRY_DELAY_MS);
					continue;
				}
				break;
			}
			if (err && err != -ENOTCONN) {
				ble_tx_drop_count++;
				if ((ble_tx_drop_count % 50U) == 1U) {
					printk("Tag BLE notify failed err=%d drops=%u\n",
					       err,
					       (unsigned int)ble_tx_drop_count);
				}
			}
		}

		k_mem_slab_free(&ble_tx_slab, (void *)item);
		k_yield();
	}
}

void uwb_tag_ble_set_tx_paused(bool paused)
{
	ble_tx_paused = paused;
}

static void ble_notif_enabled(enum bt_nus_send_status status)
{
	printk("Tag BLE notifications %s\n",
	       (status == BT_NUS_SEND_STATUS_ENABLED) ? "enabled" : "disabled");

	if (status == BT_NUS_SEND_STATUS_ENABLED) {
		char snapshot[UWB_TAG_BLE_MAX_STATUS_LEN];
		uint8_t binary_snapshot[UWB_TAG_BLE_MAX_STATUS_LEN];
		size_t binary_len = 0U;
		bool have_snapshot = false;

		k_mutex_lock(&ble_mutex, K_FOREVER);
		if (uwb_tag_ble_runtime_stream_blocked_locked()) {
			k_mutex_unlock(&ble_mutex);
			return;
		}
		if (pending_cal_count > 0U &&
		    uwb_tag_ble_snapshot_pending_cal_locked(binary_snapshot,
							    sizeof(binary_snapshot),
							    &binary_len)) {
			uwb_tag_ble_clear_pending_cal_locked();
			k_mutex_unlock(&ble_mutex);
			uwb_tag_ble_cancel_bundle_flush();
			uwb_tag_ble_send_payload(binary_snapshot, binary_len);
			return;
		} else if (pending_sample_count > 0U &&
		    uwb_tag_ble_snapshot_pending_samples_locked(binary_snapshot,
							       sizeof(binary_snapshot),
							       &binary_len)) {
			uwb_tag_ble_clear_pending_samples_locked();
			k_mutex_unlock(&ble_mutex);
			uwb_tag_ble_cancel_bundle_flush();
			uwb_tag_ble_send_payload(binary_snapshot, binary_len);
			return;
		} else if (pending_bundle_records > 0U &&
		    uwb_tag_ble_snapshot_pending_bundle_locked(snapshot,
							       sizeof(snapshot))) {
			uwb_tag_ble_clear_pending_bundle_locked();
			have_snapshot = true;
		} else if (last_status[0] != '\0') {
			snprintk(snapshot, sizeof(snapshot), "%s", last_status);
			have_snapshot = true;
		}
		k_mutex_unlock(&ble_mutex);

		if (have_snapshot) {
			uwb_tag_ble_cancel_bundle_flush();
			uwb_tag_ble_send_text(snapshot);
			return;
		}
	}
}

static void ble_received(struct bt_conn *conn, const uint8_t *const data,
			 uint16_t len)
{
	char cmd[UWB_TAG_BLE_MAX_CMD_LEN];

	ARG_UNUSED(conn);

	if (len == 0U) {
		return;
	}

	if (len >= sizeof(cmd)) {
		len = sizeof(cmd) - 1U;
	}

	memcpy(cmd, data, len);
	cmd[len] = '\0';

	while (len > 0U && (cmd[len - 1U] == '\r' || cmd[len - 1U] == '\n' ||
			    cmd[len - 1U] == ' ' || cmd[len - 1U] == '\t')) {
		cmd[len - 1U] = '\0';
		len--;
	}

	if (strcmp(cmd, "PING") == 0) {
		uwb_tag_ble_send_text("PONG");
		return;
	}

	if (strcmp(cmd, "STATUS") == 0) {
		k_mutex_lock(&ble_mutex, K_FOREVER);
		if (last_status[0] != '\0') {
			char snapshot[UWB_TAG_BLE_MAX_STATUS_LEN];

			snprintk(snapshot, sizeof(snapshot), "%s", last_status);
			k_mutex_unlock(&ble_mutex);
			uwb_tag_ble_send_text(snapshot);
			return;
		}
		k_mutex_unlock(&ble_mutex);

		uwb_tag_ble_send_text("NO_STATUS");
		return;
	}

	if (strcmp(cmd, "TDMA_STATUS") == 0) {
		char resp[128];
		struct uwb_tag_runtime_params params;

		(void)uwb_tag_ble_runtime_config_get(&params);
		snprintk(resp, sizeof(resp),
			 "TDMA_SLOT=%u/%u MASK=0x%04X SOURCE=%s PERIOD=%u ACTIVE=%u GEN=%u",
			 (unsigned int)params.tdma.slot_index,
			 (unsigned int)params.tdma.slot_count,
			 (unsigned int)params.tdma.slot_mask,
			 uwb_tag_ble_slot_source_label(params.slot_source),
			 (unsigned int)params.tdma.slot_period_ms,
			 (unsigned int)params.tdma.slot_active_ms,
			 (unsigned int)params.tdma.generation);
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strncmp(cmd, "APOS ", 5) == 0) {
		const char *arg = cmd + 5;
		char *end = NULL;
		int32_t id;
		int32_t x;
		int32_t y;
		int32_t z;
		int err;
		char resp[96];

		if (!uwb_tag_ble_parse_i32_token(arg, &end, &id) ||
		    !uwb_tag_ble_parse_i32_token(end, &end, &x) ||
		    !uwb_tag_ble_parse_i32_token(end, &end, &y) ||
		    !uwb_tag_ble_parse_i32_token(end, &end, &z)) {
			uwb_tag_ble_send_text("APOS_BAD");
			return;
		}
		while (end != NULL && (*end == ' ' || *end == '\t')) {
			end++;
		}
		if (end == NULL || *end != '\0' || id < 0 || id > UINT8_MAX) {
			uwb_tag_ble_send_text("APOS_BAD");
			return;
		}

		err = uwb_anchor_layout_set((uint8_t)id, x, y, z);
		if (err) {
			snprintk(resp, sizeof(resp), "APOS_FAIL ID=%ld RC=%d",
				 (long)id, err);
			uwb_tag_ble_send_text(resp);
			return;
		}

		snprintk(resp, sizeof(resp), "APOS_OK ID=%ld X=%ld Y=%ld Z=%ld",
			 (long)id, (long)x, (long)y, (long)z);
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strcmp(cmd, "APOS_COMMIT") == 0) {
		int err = uwb_anchor_layout_commit();
		char resp[64];

		if (err) {
			snprintk(resp, sizeof(resp), "APOS_COMMIT_FAIL RC=%d", err);
		} else {
			snprintk(resp, sizeof(resp), "APOS_COMMIT_OK N=%u",
				 (unsigned int)uwb_anchor_layout_count());
		}
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strcmp(cmd, "APOS_RESET") == 0) {
		int err = uwb_anchor_layout_reset();
		char resp[64];

		if (err) {
			snprintk(resp, sizeof(resp), "APOS_RESET_FAIL RC=%d", err);
		} else {
			snprintk(resp, sizeof(resp), "APOS_RESET_OK N=%u",
				 (unsigned int)uwb_anchor_layout_count());
		}
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strcmp(cmd, "APOS_STATUS") == 0) {
		char resp[96];
		const char *source =
			uwb_anchor_layout_loaded_from_settings() ? "SETTINGS" : "DEFAULT";

		for (uint8_t i = 0U; i < uwb_anchor_layout_count(); ++i) {
			const struct uwb_anchor_pose_mm *pose = uwb_anchor_layout_get(i);

			if (pose == NULL) {
				continue;
			}
			snprintk(resp, sizeof(resp), "APOS %u %ld %ld %ld",
				 (unsigned int)pose->anchor_id,
				 (long)pose->x_mm,
				 (long)pose->y_mm,
				 (long)pose->z_mm);
			uwb_tag_ble_send_text(resp);
		}
		snprintk(resp, sizeof(resp), "APOS_STATUS_DONE SRC=%s", source);
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strcmp(cmd, "VERSION") == 0) {
		char resp[128];
		struct uwb_tag_runtime_params params;

		(void)uwb_tag_ble_runtime_config_get(&params);
		snprintk(resp, sizeof(resp),
			 "VERSION fw=%s bs=BS%04X tag=%u pmode=%u amode=%u",
			 APP_TAG_FW_MARKER,
			 (unsigned int)params.identity_code,
			 (unsigned int)params.logical_tag_id,
			 (unsigned int)params.positioning_mode,
			 (unsigned int)params.anchor_selection_mode);
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strcmp(cmd, "CFG_STATUS") == 0) {
		char resp[192];
		struct uwb_tag_runtime_params params;
		char fixed_buf[32];
		size_t pos = 0U;

		(void)uwb_tag_ble_runtime_config_get(&params);
		fixed_buf[0] = '\0';
		for (size_t i = 0U; i < params.fixed_anchor_count &&
				   pos + 4U < sizeof(fixed_buf);
		     ++i) {
			pos += (size_t)snprintk(fixed_buf + pos,
						sizeof(fixed_buf) - pos,
						"%s%u",
						(i == 0U) ? "" : ",",
						(unsigned int)params.fixed_anchor_ids[i]);
		}
		snprintk(resp, sizeof(resp),
			 "CFG tag=%u bs=BS%04X slot=%u/%u mask=0x%04X src=%s period=%u active=%u epoch=%lu gen=%u pmode=%u amode=%u fixed=%s",
			 (unsigned int)params.logical_tag_id,
			 (unsigned int)params.identity_code,
			 (unsigned int)params.tdma.slot_index,
			 (unsigned int)params.tdma.slot_count,
			 (unsigned int)params.tdma.slot_mask,
			 uwb_tag_ble_slot_source_label(params.slot_source),
			 (unsigned int)params.tdma.slot_period_ms,
			 (unsigned int)params.tdma.slot_active_ms,
			 (unsigned long)params.tdma.epoch_ms,
			 (unsigned int)params.tdma.generation,
			 (unsigned int)params.positioning_mode,
			 (unsigned int)params.anchor_selection_mode,
			 fixed_buf[0] != '\0' ? fixed_buf : "-");
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strcmp(cmd, "MODE?") == 0) {
		char resp[128];
		struct uwb_tag_runtime_params params;

		(void)uwb_tag_ble_runtime_config_get(&params);
		snprintk(resp, sizeof(resp),
			 "MODE=%s PMODE=%u AMODE=%u FIXED_N=%u TDMA=%u SLOT=%u/%u MASK=0x%04X SRC=%s",
			 uwb_tag_ble_mode_label(params.positioning_mode),
			 (unsigned int)params.positioning_mode,
			 (unsigned int)params.anchor_selection_mode,
			 (unsigned int)params.fixed_anchor_count,
			 (unsigned int)params.tdma.enabled,
			 (unsigned int)params.tdma.slot_index,
			 (unsigned int)params.tdma.slot_count,
			 (unsigned int)params.tdma.slot_mask,
			 uwb_tag_ble_slot_source_label(params.slot_source));
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strncmp(cmd, "MODE ", 5) == 0 || strncmp(cmd, "MCAL", 4) == 0 ||
	    strncmp(cmd, "MROT", 4) == 0 || strncmp(cmd, "MMOT", 4) == 0) {
		struct uwb_tag_runtime_params params;
		uint8_t requested_mode = UWB_TAG_POSITIONING_MODE_DYNAMIC;
		const char *arg = cmd + 5;
		int live_err;
		int store_err;
		int policy_err;
		char resp[96];

		if (strcmp(cmd, "MCAL") == 0) {
			requested_mode = UWB_TAG_POSITIONING_MODE_CAL_STATIC;
		} else if (strcmp(cmd, "MROT") == 0) {
			requested_mode = UWB_TAG_POSITIONING_MODE_CAL_ROTO;
		} else if (strcmp(cmd, "MMOT") == 0) {
			requested_mode = UWB_TAG_POSITIONING_MODE_DYNAMIC;
		} else {
			while (*arg == ' ' || *arg == '\t') {
				arg++;
			}
			if (*arg == '\0' || !uwb_tag_ble_parse_mode_value(arg, &requested_mode)) {
				uwb_tag_ble_send_text("MODE_BAD");
				return;
			}
		}

		if (uwb_tag_ble_ota_active()) {
			uwb_tag_ble_send_text("ERR:BUSY_OTA");
			return;
		}

		(void)uwb_tag_ble_runtime_config_get(&params);
		params.positioning_mode = requested_mode;
		policy_err = uwb_tag_ble_apply_mode_policy(
			&params, requested_mode == UWB_TAG_POSITIONING_MODE_FIXED);
		if (policy_err == -EINVAL) {
			uwb_tag_ble_send_text("ERR:FIXED_NEEDS_4");
			return;
		}
		store_err = uwb_tag_ble_runtime_config_store(&params);
		live_err = ss_twr_init_runtime_configure(&params);
		if (store_err) {
			uwb_tag_ble_send_text("MODE_SAVE_FAIL");
			return;
		}

		k_mutex_lock(&ble_mutex, K_FOREVER);
		uwb_tag_ble_clear_pending_cal_locked();
		uwb_tag_ble_clear_pending_samples_locked();
		uwb_tag_ble_clear_pending_bundle_locked();
		k_mutex_unlock(&ble_mutex);
		uwb_tag_ble_cancel_bundle_flush();

		snprintk(resp, sizeof(resp), "MODE_OK MODE=%s LIVE=%u",
			 uwb_tag_ble_mode_label(requested_mode),
			 (unsigned int)((live_err == 0) ? 1U : 0U));
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strncmp(cmd, "TDMA_SET", 8) == 0) {
		const char *arg = cmd + 8;
		char *end = NULL;
		unsigned long slot_ul;
		uint8_t slot;
		int live_err;
		int store_err;
		char resp[64];

		while (*arg == ' ' || *arg == '\t') {
			arg++;
		}
		if (*arg == '\0') {
			uwb_tag_ble_send_text("TDMA_SET_BAD");
			return;
		}

		slot_ul = strtoul(arg, &end, 10);
		while (end != NULL && (*end == ' ' || *end == '\t')) {
			end++;
		}
		if (end == NULL || end == arg || *end != '\0' || slot_ul > UINT8_MAX) {
			uwb_tag_ble_send_text("TDMA_SET_BAD");
			return;
		}

		slot = (uint8_t)slot_ul;
		store_err = uwb_tag_ble_tdma_slot_override_store(slot);
		live_err = ss_twr_init_tdma_set_slot(slot);
		if (store_err) {
			uwb_tag_ble_send_text("TDMA_SET_SAVE_FAIL");
			return;
		}

		snprintk(resp, sizeof(resp), "TDMA_SET_OK SLOT=%u LIVE=%u",
			 (unsigned int)slot,
			 (unsigned int)((live_err == 0) ? 1U : 0U));
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strncmp(cmd, "CFG ", 4) == 0) {
		struct uwb_tag_runtime_params params;
		char resp[192];
		int parse_err;
		int live_err;
		int store_err;

		parse_err = uwb_tag_ble_parse_cfg_command(cmd, &params);
		if (parse_err) {
			uwb_tag_ble_send_text("CFG_BAD");
			return;
		}
		uwb_tag_ble_apply_mode_defaults(&params);

		store_err = uwb_tag_ble_runtime_config_store(&params);
		live_err = ss_twr_init_runtime_configure(&params);
		if (store_err) {
			uwb_tag_ble_send_text("CFG_SAVE_FAIL");
			return;
		}

		snprintk(resp, sizeof(resp),
			 "CFG_OK TAG=%u SLOT=%u/%u MASK=0x%04X PERIOD=%u ACTIVE=%u GEN=%u LIVE=%u",
			 (unsigned int)params.logical_tag_id,
			 (unsigned int)params.tdma.slot_index,
			 (unsigned int)params.tdma.slot_count,
			 (unsigned int)params.tdma.slot_mask,
			 (unsigned int)params.tdma.slot_period_ms,
			 (unsigned int)params.tdma.slot_active_ms,
			 (unsigned int)params.tdma.generation,
			 (unsigned int)((live_err == 0) ? 1U : 0U));
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strcmp(cmd, "HELP") == 0) {
#if APP_TAG_BLE_OTA_ENABLE
		uwb_tag_ble_send_text("PING|STATUS|VERSION|TDMA_STATUS|CFG_STATUS|APOS <id> <x> <y> <z>|APOS_COMMIT|APOS_STATUS|APOS_RESET|MODE?|MODE <CAL_STATIC|CAL_ROTO|MOTION|FIXED|AOTA>|MCAL|MROT|MMOT|TDMA_SET <slot>|CFG TAG=<id> SLOT=<slot> COUNT=<count> MASK=<hex> PERIOD=<ms> ACTIVE=<ms> EPOCH=<ms> GEN=<n> PMODE=<0|1|2|3|4|5> FIXED=a,b,c,d|OTA_STATUS|OTA_PREPARE|OTA_BEGIN|OTA_CANCEL|REBOOT|HELP");
#else
		uwb_tag_ble_send_text("PING|STATUS|VERSION|TDMA_STATUS|CFG_STATUS|APOS <id> <x> <y> <z>|APOS_COMMIT|APOS_STATUS|APOS_RESET|MODE?|MODE <CAL_STATIC|CAL_ROTO|MOTION|FIXED|AOTA>|MCAL|MROT|MMOT|TDMA_SET <slot>|CFG TAG=<id> SLOT=<slot> COUNT=<count> MASK=<hex> PERIOD=<ms> ACTIVE=<ms> EPOCH=<ms> GEN=<n> PMODE=<0|1|2|3|4|5> FIXED=a,b,c,d|REBOOT|HELP");
#endif
		return;
	}

#if APP_TAG_BLE_OTA_ENABLE
	if (strcmp(cmd, "OTA_STATUS") == 0) {
		if (ota_active) {
			uwb_tag_ble_send_text("OTA_STATE=ACTIVE");
		} else if (ota_ready) {
			uwb_tag_ble_send_text("OTA_STATE=READY");
		} else {
			uwb_tag_ble_send_text("OTA_STATE=NORMAL");
		}
		return;
	}

	if (strcmp(cmd, "OTA_PREPARE") == 0) {
		k_mutex_lock(&ble_mutex, K_FOREVER);
		ota_ready = true;
		ota_active = true;
		uwb_tag_ble_clear_pending_cal_locked();
		uwb_tag_ble_clear_pending_samples_locked();
		uwb_tag_ble_clear_pending_bundle_locked();
		k_mutex_unlock(&ble_mutex);
		uwb_tag_ble_cancel_bundle_flush();
		uwb_tag_ble_purge_tx_queue();
		uwb_tag_ble_send_text("OTA_READY");
		return;
	}

	if (strcmp(cmd, "OTA_BEGIN") == 0) {
		if (!ota_ready) {
			uwb_tag_ble_send_text("OTA_NOT_ARMED");
			return;
		}

		k_mutex_lock(&ble_mutex, K_FOREVER);
		ota_active = true;
		uwb_tag_ble_clear_pending_cal_locked();
		uwb_tag_ble_clear_pending_samples_locked();
		uwb_tag_ble_clear_pending_bundle_locked();
		k_mutex_unlock(&ble_mutex);
		uwb_tag_ble_cancel_bundle_flush();
		uwb_tag_ble_purge_tx_queue();
		uwb_tag_ble_send_text("OTA_BEGIN_OK");
		return;
	}

	if (strcmp(cmd, "OTA_CANCEL") == 0) {
		ota_active = false;
		ota_ready = false;
		uwb_tag_ble_send_text("OTA_CANCELLED");
		return;
	}
#else
	if (strcmp(cmd, "OTA_STATUS") == 0 ||
	    strcmp(cmd, "OTA_PREPARE") == 0 ||
	    strcmp(cmd, "OTA_BEGIN") == 0 ||
	    strcmp(cmd, "OTA_CANCEL") == 0) {
		uwb_tag_ble_send_text("OTA_DISABLED");
		return;
	}
#endif

	if (strcmp(cmd, "REBOOT") == 0) {
		uwb_tag_ble_send_text("REBOOTING");
		(void)k_work_reschedule(&reboot_work, K_MSEC(150));
		return;
	}

	uwb_tag_ble_send_text("UNKNOWN_CMD");
}

static struct bt_nus_cb nus_cb = {
	.send_enabled = ble_notif_enabled,
	.received = ble_received,
};

bool uwb_tag_ble_ota_active(void)
{
#if APP_TAG_BLE_OTA_ENABLE
	return ota_active;
#else
	return false;
#endif
}

int uwb_tag_ble_init(void)
{
	k_mutex_init(&ble_mutex);
	k_mutex_lock(&ble_mutex, K_FOREVER);
	uwb_tag_ble_runtime_params_reset_locked();
	k_mutex_unlock(&ble_mutex);
	k_thread_create(&ble_tx_thread,
		       ble_tx_thread_stack,
		       K_THREAD_STACK_SIZEOF(ble_tx_thread_stack),
		       uwb_tag_ble_tx_thread_entry,
		       NULL, NULL, NULL,
		       UWB_TAG_BLE_TX_THREAD_PRIO,
		       0,
		       K_NO_WAIT);
	k_thread_name_set(&ble_tx_thread, "uwb_tag_ble_tx");
	k_work_init_delayable(&reboot_work, ble_reboot_work_handler);
	k_work_init_delayable(&bundle_flush_work, uwb_tag_ble_flush_work_handler);
	k_work_init_delayable(&adv_retry_work, ble_adv_retry_work_handler);

	printk("Tag BLE init scheduled\n");
	ble_init_sequence();
	return 0;
}

int uwb_tag_ble_publish_status(const char *line)
{
	bool bundle_line;
	bool send_old_bundle = false;
	bool send_new_bundle = false;
	bool send_direct = false;
	char old_bundle[UWB_TAG_BLE_MAX_STATUS_LEN];
	char new_bundle[UWB_TAG_BLE_MAX_STATUS_LEN];
	char direct_text[UWB_TAG_BLE_MAX_STATUS_LEN];
	size_t line_len;

	if (line == NULL) {
		return -EINVAL;
	}

	k_mutex_lock(&ble_mutex, K_FOREVER);
	snprintk(last_status, sizeof(last_status), "%s", line);
	if (!ble_ready) {
		k_mutex_unlock(&ble_mutex);
		return -ENODEV;
	}
	if (uwb_tag_ble_runtime_stream_blocked_locked()) {
		k_mutex_unlock(&ble_mutex);
		return -EBUSY;
	}

	bundle_line = uwb_tag_ble_bundle_enabled() &&
		      uwb_tag_ble_line_is_bundle_candidate(last_status);
	line_len = strlen(last_status);

	if (!bundle_line) {
		if (pending_bundle_records > 0U &&
		    uwb_tag_ble_snapshot_pending_bundle_locked(old_bundle,
							       sizeof(old_bundle))) {
			uwb_tag_ble_clear_pending_bundle_locked();
			send_old_bundle = true;
		}

		snprintk(direct_text, sizeof(direct_text), "%s", last_status);
		send_direct = true;
	} else {
		if (pending_bundle_records > 0U &&
		    (pending_bundle_records >= APP_TAG_BLE_PACKET_BUNDLE_RECORDS ||
		     pending_bundle_len + 1U + line_len >
				UWB_TAG_BLE_BUNDLE_PAYLOAD_CAP)) {
			if (uwb_tag_ble_snapshot_pending_bundle_locked(old_bundle,
							      sizeof(old_bundle))) {
				uwb_tag_ble_clear_pending_bundle_locked();
				send_old_bundle = true;
			}
		}

		if (!uwb_tag_ble_append_pending_line_locked(last_status)) {
			if (pending_bundle_records > 0U &&
			    uwb_tag_ble_snapshot_pending_bundle_locked(old_bundle,
							       sizeof(old_bundle))) {
				uwb_tag_ble_clear_pending_bundle_locked();
				send_old_bundle = true;
			}

			snprintk(direct_text, sizeof(direct_text), "%s", last_status);
			send_direct = true;
		} else if (pending_bundle_records >=
			   APP_TAG_BLE_PACKET_BUNDLE_RECORDS ||
			   pending_bundle_len >= UWB_TAG_BLE_BUNDLE_PAYLOAD_CAP) {
			if (uwb_tag_ble_snapshot_pending_bundle_locked(new_bundle,
							      sizeof(new_bundle))) {
				uwb_tag_ble_clear_pending_bundle_locked();
				send_new_bundle = true;
			}
		} else if (APP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS > 0U &&
			   pending_bundle_records == 1U) {
			uwb_tag_ble_schedule_bundle_flush_locked();
		}
	}
	k_mutex_unlock(&ble_mutex);

	if (send_old_bundle) {
		uwb_tag_ble_cancel_bundle_flush();
		uwb_tag_ble_send_text(old_bundle);
	}

	if (send_new_bundle) {
		uwb_tag_ble_cancel_bundle_flush();
		uwb_tag_ble_send_text(new_bundle);
	}

	if (send_direct) {
		uwb_tag_ble_send_text(direct_text);
	}

	return 0;
}

int uwb_tag_ble_publish_sample(const struct uwb_tag_ble_sample *sample)
{
	uint8_t packet[UWB_TAG_BLE_MAX_STATUS_LEN];
	size_t packet_len = 0U;
	bool send_now = false;
	const uint8_t target_records =
		(APP_TAG_BLE_PACKET_BUNDLE_RECORDS >= 2U) ?
		 APP_TAG_BLE_PACKET_BUNDLE_RECORDS :
		 2U;

	if (sample == NULL) {
		return -EINVAL;
	}

	k_mutex_lock(&ble_mutex, K_FOREVER);
	if (!ble_ready) {
		k_mutex_unlock(&ble_mutex);
		return -ENODEV;
	}
	if (uwb_tag_ble_runtime_stream_blocked_locked()) {
		k_mutex_unlock(&ble_mutex);
		return -EBUSY;
	}

	if (uwb_tag_ble_mode_is_calibration(active_runtime_params.positioning_mode)) {
		k_mutex_unlock(&ble_mutex);
		return 0;
	}

	if (pending_sample_count >= UWB_TAG_BLE_MAX_BINARY_RECORDS) {
		if (uwb_tag_ble_snapshot_pending_samples_locked(packet,
							 sizeof(packet),
							 &packet_len)) {
			uwb_tag_ble_clear_pending_samples_locked();
			send_now = true;
		}
	}

	if (pending_sample_count < UWB_TAG_BLE_MAX_BINARY_RECORDS) {
		pending_samples[pending_sample_count++] = *sample;
	}

	if (!send_now && pending_sample_count >= target_records) {
		if (uwb_tag_ble_snapshot_pending_samples_locked(packet,
							 sizeof(packet),
							 &packet_len)) {
			uwb_tag_ble_clear_pending_samples_locked();
			send_now = true;
		}
	} else if (!send_now && APP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS > 0U &&
		   pending_sample_count == 1U) {
		uwb_tag_ble_schedule_bundle_flush_locked();
	}

	k_mutex_unlock(&ble_mutex);

	if (send_now) {
		uwb_tag_ble_cancel_bundle_flush();
		uwb_tag_ble_send_payload(packet, packet_len);
	}

	return 0;
}

int uwb_tag_ble_publish_calibration_range(
	const struct uwb_tag_ble_cal_range *sample)
{
	uint8_t packet[UWB_TAG_BLE_MAX_STATUS_LEN];
	size_t packet_len = 0U;
	bool send_now = false;
	uint8_t target_records =
		(APP_TAG_BLE_PACKET_BUNDLE_RECORDS >= 2U) ?
		 APP_TAG_BLE_PACKET_BUNDLE_RECORDS :
		 2U;

#if APP_TAG_CALIBRATION_MODE
	/*
	 * Calibration CM stream should preserve per-sweep anchor coverage.
	 * Prefer batching a full 8-anchor sweep per BLE notify when possible.
	 */
	target_records = UWB_TAG_BLE_MAX_CAL_RECORDS;
#endif

	if (sample == NULL) {
		return -EINVAL;
	}

	k_mutex_lock(&ble_mutex, K_FOREVER);
	if (!ble_ready) {
		k_mutex_unlock(&ble_mutex);
		return -ENODEV;
	}
	if (uwb_tag_ble_runtime_stream_blocked_locked()) {
		k_mutex_unlock(&ble_mutex);
		return -EBUSY;
	}

	if (pending_cal_count >= UWB_TAG_BLE_MAX_CAL_RECORDS) {
		if (uwb_tag_ble_snapshot_pending_cal_locked(packet,
							    sizeof(packet),
							    &packet_len)) {
			uwb_tag_ble_clear_pending_cal_locked();
			send_now = true;
		}
	}

	if (pending_cal_count < UWB_TAG_BLE_MAX_CAL_RECORDS) {
		pending_cal_ranges[pending_cal_count++] = *sample;
	}

	if (!send_now && pending_cal_count >= target_records) {
		if (uwb_tag_ble_snapshot_pending_cal_locked(packet,
							    sizeof(packet),
							    &packet_len)) {
			uwb_tag_ble_clear_pending_cal_locked();
			send_now = true;
		}
	} else if (!send_now && APP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS > 0U &&
		   pending_cal_count == 1U) {
		uwb_tag_ble_schedule_bundle_flush_locked();
	}

	k_mutex_unlock(&ble_mutex);

	if (send_now) {
		uwb_tag_ble_cancel_bundle_flush();
		uwb_tag_ble_send_payload(packet, packet_len);
	}

	return 0;
}
