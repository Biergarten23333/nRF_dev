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
#include <zephyr/drivers/gpio.h>
#include <zephyr/dfu/mcuboot.h>
#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>
#include <zephyr/storage/flash_map.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/reboot.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#include <bluetooth/services/nus.h>
#include <bluetooth/services/dfu_smp.h>

#include "biospur_uart_link.h"
#include "ss_twr_init.h"
#include "tag_led_policy.h"
#include "tag_run_state.h"
#include "tag_relay6.h"
#include "tag_relay8.h"

#include <hal/nrf_ficr.h>

/* TAG_RELAY6_BEACON_STATUS_FORMAT remains the byte-identical relay6 prefix. */

#define UWB_TAG_IMAGE_MAGIC UINT32_C(0x96f3b83d)
#define UWB_TAG_IMAGE_TLV_INFO_MAGIC UINT16_C(0x6907)
#define UWB_TAG_IMAGE_TLV_SHA256 UINT16_C(0x0010)
#define UWB_TAG_IMAGE_SHA_LEN 32U

#if APP_TAG_RELAY6_BEACON_STATUS_ENABLE != 0U
#define UWB_TAG_RELAY6_HELP_BEACON "|BEACON_STATUS"
#else
#define UWB_TAG_RELAY6_HELP_BEACON ""
#endif

#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
#define UWB_TAG_RELAY6_HELP_DW " DW_ANCHOR=<0|1>"
#else
#define UWB_TAG_RELAY6_HELP_DW ""
#endif
#define UWB_TAG_RELAY7_HELP_WIN " BEACON_WIN_N=<1..10>"

struct uwb_tag_image_header {
	uint32_t magic;
	uint32_t load_addr;
	uint16_t header_size;
	uint16_t protected_tlv_size;
	uint32_t image_size;
	uint32_t flags;
	uint8_t version[8];
	uint32_t pad;
} __packed;

struct uwb_tag_image_tlv_info {
	uint16_t magic;
	uint16_t total_size;
} __packed;

struct uwb_tag_image_tlv {
	uint16_t type;
	uint16_t length;
} __packed;

static int uwb_tag_ble_read_active_image_hash(
	uint8_t hash[UWB_TAG_IMAGE_SHA_LEN])
{
	const struct flash_area *area;
	struct uwb_tag_image_header header;
	struct uwb_tag_image_tlv_info info;
	struct uwb_tag_image_tlv tlv;
	uint32_t offset;
	uint32_t end;
	int err;

	/*
	 * This target uses MCUboot swap mode: after boot, the running image is
	 * always in the primary (slot0) partition.  Read its signed-image TLV
	 * rather than reporting a compile-time string.
	 */
	err = flash_area_open(FIXED_PARTITION_ID(slot0_partition), &area);
	if (err != 0) {
		return err;
	}
	err = flash_area_read(area, 0U, &header, sizeof(header));
	if (err != 0 || header.magic != UWB_TAG_IMAGE_MAGIC) {
		flash_area_close(area);
		return err != 0 ? err : -EBADMSG;
	}

	offset = header.header_size + header.image_size +
		 header.protected_tlv_size;
	err = flash_area_read(area, offset, &info, sizeof(info));
	if (err != 0 || info.magic != UWB_TAG_IMAGE_TLV_INFO_MAGIC ||
	    info.total_size < sizeof(info)) {
		flash_area_close(area);
		return err != 0 ? err : -EBADMSG;
	}
	end = offset + info.total_size;
	offset += sizeof(info);

	while (offset + sizeof(tlv) <= end) {
		err = flash_area_read(area, offset, &tlv, sizeof(tlv));
		if (err != 0) {
			break;
		}
		offset += sizeof(tlv);
		if (offset + tlv.length > end) {
			err = -EBADMSG;
			break;
		}
		if (tlv.type == UWB_TAG_IMAGE_TLV_SHA256 &&
		    tlv.length == UWB_TAG_IMAGE_SHA_LEN) {
			err = flash_area_read(area, offset, hash,
					      UWB_TAG_IMAGE_SHA_LEN);
			flash_area_close(area);
			return err;
		}
		offset += tlv.length;
	}

	flash_area_close(area);
	return err != 0 ? err : -ENOENT;
}

#ifndef CONFIG_BT_DEVICE_NAME
#define CONFIG_BT_DEVICE_NAME "BS_AUTO"
#endif

#ifndef APP_TAG_BLE_OTA_ENABLE
#define APP_TAG_BLE_OTA_ENABLE 1U
#endif

#ifndef APP_TAG_BLE_SETTINGS_ENABLE
#define APP_TAG_BLE_SETTINGS_ENABLE 1U
#endif

#ifndef APP_TAG_STREAM_FORCE_OFF_AT_BOOT
#define APP_TAG_STREAM_FORCE_OFF_AT_BOOT 0U
#endif

#ifndef APP_TAG_FW_MARKER
#define APP_TAG_FW_MARKER "unified-default"
#endif

#ifndef APP_TAG_SELF_CONFIRM_MODE
#define APP_TAG_SELF_CONFIRM_MODE 0U
#endif

#ifndef APP_TAG_SELF_CONFIRM_TIMEOUT_MS
#define APP_TAG_SELF_CONFIRM_TIMEOUT_MS 10000U
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

#ifndef APP_TAG_TDMA_SLOT_ACTIVE_US
#define APP_TAG_TDMA_SLOT_ACTIVE_US 0U
#endif

#ifndef APP_TAG_BLE_TOKEN_ID
#define APP_TAG_BLE_TOKEN_ID APP_TAG_ID
#endif

#ifndef APP_TAG_BLE_NAME_PREFIX
#define APP_TAG_BLE_NAME_PREFIX ""
#endif

#ifndef APP_TAG_CIR_FEATURE_OUTPUT_ENABLE
#define APP_TAG_CIR_FEATURE_OUTPUT_ENABLE 0U
#endif

#ifndef APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE
#define APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE 1U
#endif

#ifndef APP_TAG_CIR_FULL_OUTPUT_ENABLE
#define APP_TAG_CIR_FULL_OUTPUT_ENABLE 0U
#endif

#ifndef APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE
#define APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE 1U
#endif

#define UWB_TAG_BLE_DEVICE_NAME_LEN 32U
#define UWB_TAG_BLE_ADV_MFG_LEN 6U

#define UWB_TAG_BLE_MAX_STATUS_LEN 256U
/* Keep bundled NUS payloads below the common 247-byte ATT payload limit. */
#define UWB_TAG_BLE_BUNDLE_PAYLOAD_CAP 220U
#define UWB_TAG_BLE_MAX_CMD_LEN 192U

#define UWB_TAG_BLE_TX_THREAD_STACK 1536
#define UWB_TAG_BLE_TX_THREAD_PRIO 7
#ifndef APP_TAG_BLE_STATS_ENABLE
#define APP_TAG_BLE_STATS_ENABLE 0U
#endif
#define UWB_TAG_BLE_STATS_PERIOD_MS 5000U
#define UWB_TAG_SELF_CONFIRM_NORMAL 0U
#define UWB_TAG_SELF_CONFIRM_PROOF_NOCONFIRM 1U
#define UWB_TAG_SELF_CONFIRM_PROOF_TIMEOUT 2U
BUILD_ASSERT(APP_TAG_SELF_CONFIRM_MODE <= UWB_TAG_SELF_CONFIRM_PROOF_TIMEOUT,
	     "APP_TAG_SELF_CONFIRM_MODE must be 0, 1, or 2");
#ifndef APP_TAG_BLE_TX_ITEM_COUNT
#define APP_TAG_BLE_TX_ITEM_COUNT 10U
#endif
#define UWB_TAG_BLE_TX_ITEM_COUNT APP_TAG_BLE_TX_ITEM_COUNT
#define UWB_TAG_BLE_TX_RETRY_MAX 4U
#define UWB_TAG_BLE_TX_RETRY_DELAY_MS 2U
#define UWB_TAG_LED_RENDER_PERIOD_MS 50U
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
static struct bt_le_conn_param capture_conn_params_value = {
	.interval_min = 350,
	.interval_max = 350,
	.latency = 0,
	.timeout = 400,
};
static const struct bt_le_conn_param *const capture_conn_params =
	&capture_conn_params_value;
static char ble_device_name[UWB_TAG_BLE_DEVICE_NAME_LEN];
static uint16_t ble_identity_code;
static bool ble_identity_from_nvs;
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
static struct k_work_delayable self_confirm_guard_work;
#if APP_TAG_BLE_STATS_ENABLE != 0U
static struct k_work_delayable ble_stats_work;
#endif
static bool ble_ready;
static uint8_t ble_conn_count;
static bool ota_ready;
static bool ota_active;
static struct bt_conn *active_conn;
static uint32_t ble_tx_drop_count;
static uint32_t ble_tx_enqueue_count;
static uint32_t ble_tx_send_ok_count;
static uint32_t ble_tx_send_fail_count;
static uint32_t ble_tx_send_bytes;
static int ble_tx_last_err;
static volatile bool ble_tx_paused;
static bool self_confirm_control_plane_ready;

#define TAG_HEALTH_LED_NODE DT_ALIAS(led1)
#define TAG_TDMA_LED_NODE DT_ALIAS(led3)
#define TAG_BLE_LED_NODE DT_ALIAS(led2)

BUILD_ASSERT(DT_NODE_HAS_STATUS(TAG_HEALTH_LED_NODE, okay),
	     "DWM1001C D9 health LED alias is required");
BUILD_ASSERT(DT_NODE_HAS_STATUS(TAG_TDMA_LED_NODE, okay),
	     "DWM1001C D10 TDMA LED alias is required");
BUILD_ASSERT(DT_NODE_HAS_STATUS(TAG_BLE_LED_NODE, okay),
	     "DWM1001C D11 BLE LED alias is required");

static const struct gpio_dt_spec tag_health_led =
	GPIO_DT_SPEC_GET(TAG_HEALTH_LED_NODE, gpios);
static const struct gpio_dt_spec tag_tdma_led =
	GPIO_DT_SPEC_GET(TAG_TDMA_LED_NODE, gpios);
static const struct gpio_dt_spec tag_ble_led =
	GPIO_DT_SPEC_GET(TAG_BLE_LED_NODE, gpios);
static atomic_t tag_led_uwb_ready;
static atomic_t tag_led_ble_state;
static bool tag_led_ready;
static int8_t tag_health_led_level = -1;
static int8_t tag_tdma_led_level = -1;
static int8_t tag_ble_led_level = -1;
static uint32_t tag_led_last_render_ms;

static char last_status[UWB_TAG_BLE_MAX_STATUS_LEN];
static char pending_bundle[UWB_TAG_BLE_MAX_STATUS_LEN];
static size_t pending_bundle_len;
static uint8_t pending_bundle_records;
static struct uwb_tag_ble_sample pending_samples[UWB_TAG_BLE_MAX_BINARY_RECORDS];
static uint8_t pending_sample_count;
static struct uwb_tag_ble_cal_range pending_cal_ranges[UWB_TAG_BLE_MAX_CAL_RECORDS];
static uint8_t pending_cal_count;
static struct bt_nus_cb nus_cb;

enum uwb_tag_command_source {
	UWB_TAG_COMMAND_SOURCE_BLE = 0,
	UWB_TAG_COMMAND_SOURCE_UART = 1,
};

typedef int (*uwb_tag_reply_sink_t)(void *context, const char *text);

static K_MUTEX_DEFINE(command_dispatch_mutex);
static k_tid_t active_reply_thread;
static uwb_tag_reply_sink_t active_reply_sink;
static void *active_reply_context;
static enum uwb_tag_command_source active_command_source;

static int uwb_tag_ble_start_advertising(void);
static void uwb_tag_ble_init_identity(void);
static void uwb_tag_ble_runtime_params_reset_locked(void);
static void uwb_tag_ble_runtime_params_apply_settings_locked(void);
static const char *uwb_tag_ble_slot_source_label(uint8_t slot_source);
static bool uwb_tag_ble_parse_u32_field(const char *cmd, const char *key,
					uint32_t *value_out);
static int uwb_tag_ble_parse_cfg_command(
	const char *cmd,
	struct uwb_tag_runtime_params *params);
static const char *uwb_tag_ble_mode_label(uint8_t positioning_mode);
static bool uwb_tag_ble_parse_mode_value(const char *mode_text,
					 uint8_t *positioning_mode_out);
static void uwb_tag_ble_send_cir_status(void);
static bool uwb_tag_ble_cir_mode_supported(enum uwb_tag_cir_mode mode);
static void uwb_tag_ble_apply_mode_defaults(struct uwb_tag_runtime_params *params);
static int uwb_tag_ble_apply_mode_policy(struct uwb_tag_runtime_params *params);
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
static void uwb_tag_ble_process_command(const char *cmd);
static void uwb_tag_ble_tx_thread_entry(void *arg1, void *arg2, void *arg3);
static void ble_adv_retry_work_handler(struct k_work *work);
static void uwb_tag_ble_control_plane_ready(void);
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
static int uwb_tag_ble_request_conn_params(bool capture_interval);
static int uwb_tag_ble_set_capture_mode(bool enabled);

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

static void self_confirm_guard_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);

	if (!self_confirm_control_plane_ready || !boot_is_img_confirmed()) {
		printk("MCUboot self-confirm timeout ready=%u confirmed=%u; rebooting for rollback\n",
		       self_confirm_control_plane_ready ? 1U : 0U,
		       boot_is_img_confirmed() ? 1U : 0U);
		sys_reboot(SYS_REBOOT_COLD);
	}
}

static void uwb_tag_ble_control_plane_ready(void)
{
	int err;

	ble_ready = true;
#if APP_TAG_SELF_CONFIRM_MODE == UWB_TAG_SELF_CONFIRM_PROOF_TIMEOUT
	printk("MCUboot proof-timeout: BLE is ready; readiness flag and confirmation intentionally withheld\n");
	return;
#else
	self_confirm_control_plane_ready = true;
#endif

#if APP_TAG_SELF_CONFIRM_MODE == UWB_TAG_SELF_CONFIRM_PROOF_NOCONFIRM
	printk("MCUboot proof-noconfirm: BLE is ready; confirmation intentionally withheld\n");
	return;
#endif

	if (boot_is_img_confirmed()) {
		return;
	}

	err = boot_write_img_confirmed();
	printk("MCUboot confirm after BLE control-plane ready rc=%d\n", err);
	if (err == 0) {
		(void)k_work_cancel_delayable(&self_confirm_guard_work);
	}
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
		uwb_tag_ble_control_plane_ready();
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
		return;
	}

	uwb_tag_ble_control_plane_ready();
	printk("Tag BLE advertising as %s\n", ble_device_name);
}

static void uwb_tag_ble_init_identity(void)
{
	uint32_t seed0 = NRF_FICR->DEVICEID[0];
	uint32_t seed1 = NRF_FICR->DEVICEID[1];
	uint16_t code = bsl_identity_from_ficr(seed0, seed1);
	bool from_nvs = false;
	int len;

	k_mutex_lock(&ble_mutex, K_FOREVER);
	if (runtime_settings_record_loaded && runtime_settings_record.valid &&
	    runtime_settings_record.identity_code != 0U) {
		code = runtime_settings_record.identity_code;
		from_nvs = true;
	}
	ble_identity_code = code;
	ble_identity_from_nvs = from_nvs;
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
	if (APP_TAG_BLE_NAME_PREFIX[0] != '\0') {
		len = snprintk(ble_device_name, sizeof(ble_device_name),
			       "%s-BS%04X", APP_TAG_BLE_NAME_PREFIX, code);
	} else {
		len = snprintk(ble_device_name, sizeof(ble_device_name), "BS%04X", code);
	}
	if (len < 0) {
		memcpy(ble_device_name, "BS0000", sizeof("BS0000"));
		len = (int)(sizeof("BS0000") - 1);
	} else if ((size_t)len >= sizeof(ble_device_name)) {
		len = sizeof(ble_device_name) - 1;
		ble_device_name[len] = '\0';
	}

	sd[0].data_len = (uint8_t)len;
	printk("Tag BLE auto identity seed=%08x%08x name=%s\n",
	       (unsigned int)seed1, (unsigned int)seed0, ble_device_name);
}

uint16_t uwb_tag_ble_identity_code(void)
{
	return ble_identity_code;
}

bool uwb_tag_ble_identity_is_nvs(void)
{
	return ble_identity_from_nvs;
}

uint8_t uwb_tag_ble_tag_id(void)
{
	return ble_tag_id;
}

static uint8_t uwb_tag_ble_normalize_positioning_mode(uint8_t positioning_mode)
{
	switch (positioning_mode) {
	case UWB_TAG_MODE_IDLE:
		return positioning_mode;
	default:
		return UWB_TAG_MODE_RUN;
	}
}

#if APP_TAG_BLE_STATS_ENABLE != 0U
static void ble_stats_work_handler(struct k_work *work)
{
	char line[128];
	uint8_t active_conns;

	ARG_UNUSED(work);

	k_mutex_lock(&ble_mutex, K_FOREVER);
	active_conns = ble_conn_count;
	k_mutex_unlock(&ble_mutex);

	snprintk(line, sizeof(line),
		 "BSTAT conn=%u enq=%u ok=%u fail=%u drop=%u bytes=%u last_err=%d",
		 (unsigned int)active_conns,
		 (unsigned int)ble_tx_enqueue_count,
		 (unsigned int)ble_tx_send_ok_count,
		 (unsigned int)ble_tx_send_fail_count,
		 (unsigned int)ble_tx_drop_count,
		 (unsigned int)ble_tx_send_bytes,
		 ble_tx_last_err);
	uwb_tag_ble_send_text(line);
	(void)k_work_reschedule(&ble_stats_work,
				 K_MSEC(UWB_TAG_BLE_STATS_PERIOD_MS));
}
#endif

struct uwb_tag_ble_conn_params_status {
	bool connected;
	bool valid;
	bool capture_target;
	uint16_t interval;
	uint16_t latency;
	uint16_t timeout;
	uint16_t requested_interval;
	uint16_t requested_timeout;
};

static void uwb_tag_ble_conn_params_snapshot(
	struct uwb_tag_ble_conn_params_status *status)
{
	struct bt_conn *conn = NULL;
	struct bt_conn_info info;

	memset(status, 0, sizeof(*status));

	k_mutex_lock(&ble_mutex, K_FOREVER);
	status->capture_target = !runtime_stream_enabled && !ota_active;
	status->requested_interval =
		status->capture_target ? capture_conn_params->interval_min :
					 fast_conn_params->interval_min;
	status->requested_timeout =
		status->capture_target ? capture_conn_params->timeout :
					 fast_conn_params->timeout;
	if (active_conn != NULL) {
		conn = bt_conn_ref(active_conn);
		status->connected = true;
	}
	k_mutex_unlock(&ble_mutex);

	if (conn == NULL) {
		return;
	}

	if (bt_conn_get_info(conn, &info) == 0 &&
	    info.type == BT_CONN_TYPE_LE) {
		status->valid = true;
		status->interval = info.le.interval;
		status->latency = info.le.latency;
		status->timeout = info.le.timeout;
	}

	bt_conn_unref(conn);
}

const char *uwb_tag_ble_device_name(void)
{
	return ble_device_name;
}

bool uwb_tag_ble_tr_enabled(void)
{
	bool enabled;

	k_mutex_lock(&ble_mutex, K_FOREVER);
	enabled = runtime_stream_enabled;
	k_mutex_unlock(&ble_mutex);
	return enabled;
}

void uwb_tag_ble_publish_link_status(void)
{
	struct biospur_uart_link_stats stats;
	struct ss_twr_init_poll_tx_stats poll_tx_stats;
	struct uwb_tag_ble_conn_params_status conn_status;
	char line[256];

	biospur_uart_link_get_stats(&stats);
	ss_twr_init_poll_tx_stats_snapshot(&poll_tx_stats);
	uwb_tag_ble_conn_params_snapshot(&conn_status);
	if (conn_status.valid) {
		snprintk(line, sizeof(line),
			 "BSLSTAT;1;gen=%lu;start=%lu;done=%lu;drop=%lu;fail=%lu;"
			 "abort=%lu;strobe=%lu;last=%ld;pollfail=%lu;polllast=%ld;"
			 "slplate=%lu;spinlate=%lu;"
			 "rxcrc=NA;"
			 "ci=%u;lat=%u;sup=%u;reqci=%u;reqsup=%u;ciok=%u;supok=%u;cpmode=%s",
			 (unsigned long)stats.frames_generated,
			 (unsigned long)stats.tx_started,
			 (unsigned long)stats.tx_completed,
			 (unsigned long)stats.tx_dropped,
			 (unsigned long)stats.tx_failed,
			 (unsigned long)stats.tx_aborted,
			 (unsigned long)stats.strobe_count,
			 (long)stats.last_tx_error,
			 (unsigned long)poll_tx_stats.failures,
			 (long)poll_tx_stats.last_error,
			 (unsigned long)poll_tx_stats.slot_sleep_late_skips,
			 (unsigned long)poll_tx_stats.slot_spin_late_skips,
			 (unsigned int)conn_status.interval,
			 (unsigned int)conn_status.latency,
			 (unsigned int)conn_status.timeout,
			 (unsigned int)conn_status.requested_interval,
			 (unsigned int)conn_status.requested_timeout,
			 conn_status.interval == conn_status.requested_interval,
			 conn_status.timeout == conn_status.requested_timeout,
			 conn_status.capture_target ? "CAP" : "FAST");
	} else {
		snprintk(line, sizeof(line),
			 "BSLSTAT;1;gen=%lu;start=%lu;done=%lu;drop=%lu;fail=%lu;"
			 "abort=%lu;strobe=%lu;last=%ld;pollfail=%lu;polllast=%ld;"
			 "slplate=%lu;spinlate=%lu;"
			 "rxcrc=NA;"
			 "ci=NA;lat=NA;sup=NA;reqci=%u;reqsup=%u;ciok=NA;supok=NA;cpmode=%s",
			 (unsigned long)stats.frames_generated,
			 (unsigned long)stats.tx_started,
			 (unsigned long)stats.tx_completed,
			 (unsigned long)stats.tx_dropped,
			 (unsigned long)stats.tx_failed,
			 (unsigned long)stats.tx_aborted,
			 (unsigned long)stats.strobe_count,
			 (long)stats.last_tx_error,
			 (unsigned long)poll_tx_stats.failures,
			 (long)poll_tx_stats.last_error,
			 (unsigned long)poll_tx_stats.slot_sleep_late_skips,
			 (unsigned long)poll_tx_stats.slot_spin_late_skips,
			 (unsigned int)conn_status.requested_interval,
			 (unsigned int)conn_status.requested_timeout,
			 conn_status.capture_target ? "CAP" : "FAST");
	}
	uwb_tag_ble_send_text(line);
}

static void uwb_tag_ble_runtime_params_reset_locked(void)
{
	memset(&active_runtime_params, 0, sizeof(active_runtime_params));
	active_runtime_params.identity_code = ble_identity_code;
	active_runtime_params.logical_tag_id =
		(ble_identity_code != 0U) ? (uint8_t)(ble_identity_code & 0xFFU) :
					    ble_tag_id;
	active_runtime_params.slot_source = UWB_TAG_SLOT_SOURCE_BUILD;
	active_runtime_params.positioning_mode = UWB_TAG_MODE_RUN;
	active_runtime_params.anchor_selection_mode =
		UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2;
	active_runtime_params.beacon_sync = false;
	active_runtime_params.beacon_win_n = TAG_BEACON_WINDOW_N_DEFAULT;
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
	active_runtime_params.dw_anchor = false;
#endif
	active_runtime_params.tdma.enabled = (APP_TAG_TDMA_ENABLE != 0U);
	active_runtime_params.tdma.slot_index = APP_TAG_TDMA_SLOT_INDEX;
	active_runtime_params.tdma.slot_count = APP_TAG_TDMA_SLOT_COUNT;
	active_runtime_params.tdma.slot_mask = 0U;
	active_runtime_params.tdma.slot_period_ms = APP_TAG_TDMA_SLOT_PERIOD_MS;
	active_runtime_params.tdma.slot_active_ms = APP_TAG_TDMA_SLOT_ACTIVE_MS;
	active_runtime_params.tdma.slot_active_us = APP_TAG_TDMA_SLOT_ACTIVE_US;
	active_runtime_params.tdma.epoch_ms = 0U;
	active_runtime_params.tdma.sync_local_ms = 0U;
	active_runtime_params.tdma.sync_local_sub_ms_us = 0U;
	active_runtime_params.tdma.epoch_valid = false;
	active_runtime_params.tdma.generation = 0U;
	active_runtime_params.tdma.superframe_base = 0U;
	active_runtime_params.tdma.superframe_valid = false;
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
		uwb_tag_ble_normalize_positioning_mode(runtime_settings_record.positioning_mode);
	active_runtime_params.anchor_selection_mode =
		UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2;
	active_runtime_params.fixed_anchor_count = 0U;
	memset(active_runtime_params.fixed_anchor_ids, 0,
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
		active_runtime_params.tdma.slot_active_us =
			APP_TAG_TDMA_SLOT_ACTIVE_US;
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
	record.positioning_mode =
		uwb_tag_ble_normalize_positioning_mode(params->positioning_mode);
	record.anchor_selection_mode = UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2;
	record.fixed_anchor_count = 0U;
	memset(record.fixed_anchor_ids, 0, sizeof(record.fixed_anchor_ids));
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
	active_runtime_params.positioning_mode = record.positioning_mode;
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
	case UWB_TAG_MODE_IDLE:
		return "IDLE";
	default:
		return "RUN";
	}
}

static bool uwb_tag_ble_cir_mode_supported(enum uwb_tag_cir_mode mode)
{
	switch (mode) {
	case UWB_TAG_CIR_MODE_OFF:
		return true;
	case UWB_TAG_CIR_MODE_COMPACT:
		return APP_TAG_CIR_FEATURE_OUTPUT_ENABLE != 0U;
	case UWB_TAG_CIR_MODE_FULL:
		return APP_TAG_CIR_FULL_OUTPUT_ENABLE != 0U &&
		       APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE != 0U;
	default:
		return false;
	}
}

static void uwb_tag_ble_send_cir_status(void)
{
	char resp[160];
	enum uwb_tag_cir_mode mode = ss_twr_init_cir_mode_get();

	snprintk(resp, sizeof(resp),
		 "CIR MODE=%s CAPS=off%s%s RAW_RANGE=1 COMPACT_BLE=%u FULL_CDC=%u FULL_REQUIRES_USB=1",
		 ss_twr_init_cir_mode_label(mode),
		 (APP_TAG_CIR_FEATURE_OUTPUT_ENABLE != 0U) ? ",compact" : "",
		 (APP_TAG_CIR_FULL_OUTPUT_ENABLE != 0U &&
		  APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE != 0U) ? ",full" : "",
		 (unsigned int)(APP_TAG_CIR_FEATURE_OUTPUT_ENABLE != 0U &&
				APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE != 0U),
		 (unsigned int)(APP_TAG_CIR_FULL_OUTPUT_ENABLE != 0U &&
				APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE != 0U));
	uwb_tag_ble_send_text(resp);
}

static bool uwb_tag_ble_mode_is_idle(uint8_t positioning_mode)
{
	return positioning_mode == UWB_TAG_MODE_IDLE;
}

static bool uwb_tag_ble_parse_mode_value(const char *mode_text,
					 uint8_t *positioning_mode_out)
{
	if (mode_text == NULL || positioning_mode_out == NULL) {
		return false;
	}

	if (strcasecmp(mode_text, "RANGE") == 0 ||
	    strcasecmp(mode_text, "TR") == 0 ||
	    strcasecmp(mode_text, "TR_ONLY") == 0 ||
	    strcasecmp(mode_text, "MOTION") == 0 ||
	    strcasecmp(mode_text, "DYN") == 0 ||
	    strcasecmp(mode_text, "DYNAMIC") == 0 ||
	    strcasecmp(mode_text, "RUN") == 0) {
		*positioning_mode_out = UWB_TAG_MODE_RUN;
		return true;
	}

	if (strcasecmp(mode_text, "IDLE") == 0 ||
	    strcasecmp(mode_text, "STOP") == 0 ||
	    strcasecmp(mode_text, "HALT") == 0) {
		*positioning_mode_out = UWB_TAG_MODE_IDLE;
		return true;
	}

	if (strcasecmp(mode_text, "SOLVE") == 0 ||
	    strcasecmp(mode_text, "TS") == 0 ||
	    strcasecmp(mode_text, "TS_ENABLE") == 0 ||
	    strcasecmp(mode_text, "DEBUG") == 0 ||
	    strcasecmp(mode_text, "DIAG") == 0 ||
	    strcasecmp(mode_text, "TX_TEST") == 0) {
		*positioning_mode_out = UWB_TAG_MODE_RUN;
		return true;
	}

	return false;
}

static void uwb_tag_ble_apply_mode_defaults(struct uwb_tag_runtime_params *params)
{
	if (params == NULL) {
		return;
	}

	if (uwb_tag_ble_mode_is_idle(params->positioning_mode)) {
		tag_relay8_apply_idle_beacon_policy(params);
		params->slot_source = UWB_TAG_SLOT_SOURCE_BUILD;
		params->tdma.enabled = false;
		params->tdma.slot_index = 0U;
		params->tdma.slot_count = 1U;
		params->tdma.slot_mask = 0U;
		params->tdma.slot_period_ms = 25U;
		params->tdma.slot_active_ms = 25U;
		params->tdma.slot_active_us = 0U;
		params->tdma.epoch_valid = false;
			params->tdma.epoch_ms = 0U;
			params->tdma.generation = 0U;
			params->tdma.superframe_base = 0U;
			params->tdma.superframe_valid = false;
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
		params->tdma.slot_active_us = APP_TAG_TDMA_SLOT_ACTIVE_US;
		params->tdma.epoch_valid = false;
			params->tdma.epoch_ms = 0U;
			params->tdma.generation = 0U;
			params->tdma.superframe_base = 0U;
			params->tdma.superframe_valid = false;
	}
}

static int uwb_tag_ble_apply_mode_policy(struct uwb_tag_runtime_params *params)
{
	if (params == NULL) {
		return -EINVAL;
	}

	params->anchor_selection_mode = UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2;
	params->fixed_anchor_count = 0U;
	memset(params->fixed_anchor_ids, 0, sizeof(params->fixed_anchor_ids));
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
	if (params.tdma.slot_active_us == 0U) {
		params.tdma.slot_active_us = APP_TAG_TDMA_SLOT_ACTIVE_US;
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

static int uwb_tag_ble_parse_cfg_command(
	const char *cmd,
	struct uwb_tag_runtime_params *params)
{
	uint32_t tag_id = 0U;
	uint32_t slot = 0U;
	uint32_t count = 0U;
	uint32_t period = 0U;
	uint32_t active = 0U;
	uint32_t active_us = 0U;
	uint32_t slot_mask = 0U;
	uint32_t epoch = 0U;
	uint32_t generation = 0U;
	uint32_t superframe_base = 0U;
	bool superframe_valid;
	uint32_t run_enabled = 1U;
	uint32_t positioning_mode = UWB_TAG_MODE_RUN;
	uint32_t beacon_sync = 0U;
	uint32_t beacon_win_n = TAG_BEACON_WINDOW_N_DEFAULT;
	bool beacon_win_n_present;
	uint8_t beacon_win_n_value;
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
	uint32_t dw_anchor = 0U;
	bool dw_anchor_present;
	bool dw_anchor_enabled;
#endif

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
	superframe_valid = uwb_tag_ble_parse_u32_field(
		cmd, "SUPERFRAME_BASE=", &superframe_base);
	(void)uwb_tag_ble_parse_u32_field(cmd, "MASK=", &slot_mask);
	(void)uwb_tag_ble_parse_u32_field(cmd, "ACTIVE_US=", &active_us);
	(void)uwb_tag_ble_parse_u32_field(cmd, "RUN=", &run_enabled);
	(void)uwb_tag_ble_parse_u32_field(cmd, "PMODE=", &positioning_mode);
	(void)uwb_tag_ble_parse_u32_field(cmd, "BEACON_SYNC=", &beacon_sync);
	beacon_win_n_present = uwb_tag_ble_parse_u32_field(
		cmd, "BEACON_WIN_N=", &beacon_win_n);
	if (!tag_beacon_window_n_value(
		    beacon_win_n_present, beacon_win_n, &beacon_win_n_value)) {
		return -ERANGE;
	}
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
	dw_anchor_present =
		uwb_tag_ble_parse_u32_field(cmd, "DW_ANCHOR=", &dw_anchor);
	if (!tag_relay6_dw_anchor_value(
		    dw_anchor_present, dw_anchor, &dw_anchor_enabled)) {
		return -ERANGE;
	}
#endif

	if (tag_id >= UWB_MAX_TAGS || slot >= UINT8_MAX || count == 0U ||
	    count > UINT8_MAX || period == 0U || period > UINT16_MAX ||
	    active == 0U || active > UINT16_MAX || active > period ||
	    active_us > UINT16_MAX || active_us > (period * 1000U) ||
	    slot_mask > UINT16_MAX || run_enabled > 1U ||
	    positioning_mode > UWB_TAG_MODE_IDLE || beacon_sync > 1U) {
		return -ERANGE;
	}

	(void)uwb_tag_ble_runtime_config_get(params);
	params->logical_tag_id = (uint8_t)tag_id;
	params->slot_source = UWB_TAG_SLOT_SOURCE_MASTER;
	params->positioning_mode =
		uwb_tag_ble_normalize_positioning_mode((uint8_t)positioning_mode);
	params->anchor_selection_mode = UWB_TAG_ANCHOR_SELECTION_DYNAMIC_2P2;
	params->fixed_anchor_count = 0U;
	params->beacon_sync = beacon_sync != 0U;
	params->beacon_win_n = beacon_win_n_value;
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
	params->dw_anchor = dw_anchor_enabled;
#endif
	memset(params->fixed_anchor_ids, 0, sizeof(params->fixed_anchor_ids));
	params->tdma.enabled = (run_enabled != 0U);
	params->tdma.slot_index = (uint8_t)slot;
	params->tdma.slot_count = (uint8_t)count;
	params->tdma.slot_mask = (uint16_t)slot_mask;
	params->tdma.slot_period_ms = (uint16_t)period;
	params->tdma.slot_active_ms = (uint16_t)active;
	params->tdma.slot_active_us = (uint16_t)active_us;
	params->tdma.epoch_ms = epoch;
	params->tdma.epoch_valid = true;
	params->tdma.generation = (uint8_t)generation;
	params->tdma.superframe_base = superframe_base;
	params->tdma.superframe_valid = superframe_valid;
	if (uwb_tag_ble_apply_mode_policy(params) != 0) {
		return -EINVAL;
	}

	return 0;
}

static void uwb_tag_ble_set_cfg_run_state(bool run)
{
	struct uwb_tag_runtime_params params;
	struct uwb_tag_runtime_params reported;
	char resp[160];
	int live_err;

	(void)uwb_tag_ble_runtime_config_get(&params);
	if (!run && !tag_run_state_can_cfg_stop(&params)) {
		snprintk(resp, sizeof(resp),
			 "CFG_STOP_ERR reason=epoch_invalid action=MODE_IDLE RUN=%u STATE=%s LIVE=0",
			 params.tdma.enabled ? 1U : 0U,
			 params.tdma.enabled ? "RUNNING" : "ARMED");
		uwb_tag_ble_send_text(resp);
		return;
	}
	tag_run_state_set(&params, run);
	live_err = ss_twr_init_runtime_configure(&params);
	if (live_err == 0) {
		k_mutex_lock(&ble_mutex, K_FOREVER);
		active_runtime_params = params;
		k_mutex_unlock(&ble_mutex);
	}
	(void)uwb_tag_ble_runtime_config_get(&reported);
	snprintk(resp, sizeof(resp),
		 "CFG_%s_OK RUN=%u STATE=%s LIVE=%u EPOCH=%lu GEN=%u SUPERFRAME_BASE=%lu SF_VALID=%u",
		 run ? "RUN" : "STOP",
		 reported.tdma.enabled ? 1U : 0U,
		 reported.tdma.enabled ? "RUNNING" : "ARMED",
		 (unsigned int)((live_err == 0) ? 1U : 0U),
		 (unsigned long)reported.tdma.epoch_ms,
		 (unsigned int)reported.tdma.generation,
		 (unsigned long)reported.tdma.superframe_base,
		 reported.tdma.superframe_valid ? 1U : 0U);
	uwb_tag_ble_send_text(resp);
}

static int uwb_tag_ble_start_advertising(void)
{
	int err;
	const struct bt_le_adv_param *params = BT_LE_ADV_CONN;

	atomic_set(&tag_led_ble_state, TAG_LED_BLE_OFF);
	for (int attempt = 0; attempt < 10; ++attempt) {
		(void)bt_le_adv_stop();
		k_msleep(50);
		err = bt_le_adv_start(params, ad, ARRAY_SIZE(ad), sd, ARRAY_SIZE(sd));
		if (err == -EALREADY) {
			atomic_set(&tag_led_ble_state, TAG_LED_BLE_ADVERTISING);
			return 0;
		}

		if (err != -EAGAIN) {
			if (err == 0) {
				atomic_set(&tag_led_ble_state,
					   TAG_LED_BLE_ADVERTISING);
			}
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
	/* freeze-clean batch4e: "BS;" was a vestigial bundle token that no tag
	 * path emits (verified: no snprintk/printk produces a "BS;" line); drop it
	 * from the bundle-candidate match. */
	return (line != NULL) && (strstr(line, "TagSummary") != NULL ||
				  strstr(line, "TS ") != NULL ||
				  strstr(line, "TS;") != NULL ||
				  strstr(line, "TR;") != NULL);
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
		sys_put_le32(sample->range_mm, &out[offset]);
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

static int uwb_tag_ble_request_conn_params(bool capture_interval)
{
	struct bt_conn *conn = NULL;
	struct bt_le_conn_param params;
	int err;

	k_mutex_lock(&ble_mutex, K_FOREVER);
	params = capture_interval ? *capture_conn_params : *fast_conn_params;
	if (active_conn != NULL) {
		conn = bt_conn_ref(active_conn);
	}
	k_mutex_unlock(&ble_mutex);

	if (conn == NULL) {
		return -ENOTCONN;
	}

	err = bt_conn_le_param_update(conn, &params);
	bt_conn_unref(conn);
	return err;
}

static int uwb_tag_ble_set_capture_conn_params(uint16_t interval,
					       uint16_t timeout)
{
	/* BLE units are 1.25 ms for interval and 10 ms for supervision timeout. */
	if (interval < 6U || interval > 3200U || timeout < 10U ||
	    timeout > 3200U || interval >= (uint32_t)timeout * 4U) {
		return -EINVAL;
	}

	k_mutex_lock(&ble_mutex, K_FOREVER);
	if (!runtime_stream_enabled || ota_active) {
		k_mutex_unlock(&ble_mutex);
		return -EBUSY;
	}
	capture_conn_params_value.interval_min = interval;
	capture_conn_params_value.interval_max = interval;
	capture_conn_params_value.latency = 0U;
	capture_conn_params_value.timeout = timeout;
	k_mutex_unlock(&ble_mutex);

	return 0;
}

static int uwb_tag_ble_set_capture_mode(bool enabled)
{
	struct uwb_tag_runtime_params params;
	int err;

	(void)uwb_tag_ble_runtime_config_get(&params);
	k_mutex_lock(&ble_mutex, K_FOREVER);
	runtime_stream_enabled = !enabled;
	uwb_tag_ble_clear_pending_bundle_locked();
	uwb_tag_ble_clear_pending_samples_locked();
	uwb_tag_ble_clear_pending_cal_locked();
	k_mutex_unlock(&ble_mutex);
	uwb_tag_ble_cancel_bundle_flush();

	err = uwb_tag_ble_runtime_config_store(&params);
	if (err != 0) {
		return err;
	}

	return uwb_tag_ble_request_conn_params(enabled);
}

static void ble_connected(struct bt_conn *conn, uint8_t conn_err)
{
	char addr[BT_ADDR_LE_STR_LEN];
	int err;
	uint8_t active_conns;
	struct bt_le_conn_param requested_params;

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));

	if (conn_err) {
		printk("Tag BLE connect failed: %s err=0x%02x\n", addr, conn_err);
		return;
	}

	k_mutex_lock(&ble_mutex, K_FOREVER);
	ble_conn_count++;
	if (active_conn == NULL) {
		active_conn = bt_conn_ref(conn);
	}
	active_conns = ble_conn_count;
	bool capture_interval = !runtime_stream_enabled;
	requested_params = capture_interval ? *capture_conn_params :
					     *fast_conn_params;
	k_mutex_unlock(&ble_mutex);
	atomic_set(&tag_led_ble_state, TAG_LED_BLE_CONNECTED);

	printk("Tag BLE connected: %s active=%u\n", addr,
	       (unsigned int)active_conns);
	err = bt_conn_le_phy_update(conn, fast_phy_params);
	printk("Tag BLE PHY update request rc=%d\n", err);
	err = bt_conn_le_param_update(conn, &requested_params);
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
	if (active_conn == conn) {
		bt_conn_unref(active_conn);
		active_conn = NULL;
	}
	k_mutex_unlock(&ble_mutex);
	uwb_tag_ble_cancel_bundle_flush();

	printk("Tag BLE disconnected: %s reason=0x%02x active=%u\n", addr, reason,
	       (unsigned int)active_conns);
	atomic_set(&tag_led_ble_state, TAG_LED_BLE_OFF);
	ota_active = false;
	ota_ready = false;
	biospur_uart_link_resume();

	err = uwb_tag_ble_start_advertising();
	printk("Tag BLE adv resume rc=%d\n", err);
	if (err && err != -EALREADY) {
		(void)k_work_reschedule(&adv_retry_work, K_MSEC(250));
	}
}

static void ble_le_param_updated(struct bt_conn *conn, uint16_t interval,
				 uint16_t latency, uint16_t timeout)
{
	bool capture_target;
	uint16_t requested_interval;
	uint16_t requested_timeout;

	ARG_UNUSED(conn);

	k_mutex_lock(&ble_mutex, K_FOREVER);
	capture_target = !runtime_stream_enabled && !ota_active;
	requested_interval =
		capture_target ? capture_conn_params->interval_min :
				 fast_conn_params->interval_min;
	requested_timeout =
		capture_target ? capture_conn_params->timeout :
				 fast_conn_params->timeout;
	k_mutex_unlock(&ble_mutex);

	printk("Tag BLE conn params achieved int=%u lat=%u timeout=%u "
	       "requested_int=%u requested_timeout=%u verified=%u mode=%s\n",
	       (unsigned int)interval,
	       (unsigned int)latency,
	       (unsigned int)timeout,
	       (unsigned int)requested_interval,
	       (unsigned int)requested_timeout,
	       interval == requested_interval && timeout == requested_timeout,
	       capture_target ? "CAP" : "FAST");
}

BT_CONN_CB_DEFINE(uwb_tag_ble_conn_cb) = {
	.connected = ble_connected,
	.disconnected = ble_disconnected,
	.le_param_updated = ble_le_param_updated,
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
	ble_tx_enqueue_count++;
	k_fifo_put(&ble_tx_fifo, item);
}

static void uwb_tag_ble_send_text(const char *text)
{
	if (text == NULL || text[0] == '\0') {
		return;
	}

	if (k_current_get() == active_reply_thread &&
	    active_reply_sink != NULL) {
		(void)active_reply_sink(active_reply_context, text);
		return;
	}
	uwb_tag_ble_send_payload((const uint8_t *)text, strlen(text));
}

static void tag_led_set_if_changed(const struct gpio_dt_spec *led, bool on,
				   int8_t *rendered)
{
	int8_t requested = on ? 1 : 0;

	if (*rendered == requested) {
		return;
	}
	if (gpio_pin_set_dt(led, requested) == 0) {
		*rendered = requested;
	}
}

static int tag_led_init(void)
{
	const struct gpio_dt_spec *leds[] = {
		&tag_health_led,
		&tag_tdma_led,
		&tag_ble_led,
	};

	for (size_t i = 0U; i < ARRAY_SIZE(leds); ++i) {
		if (!gpio_is_ready_dt(leds[i])) {
			return -ENODEV;
		}
		int err = gpio_pin_configure_dt(leds[i], GPIO_OUTPUT_INACTIVE);

		if (err != 0) {
			return err;
		}
	}

	tag_health_led_level = 0;
	tag_tdma_led_level = 0;
	tag_ble_led_level = 0;
	tag_led_ready = true;
	return 0;
}

static void tag_led_render(void)
{
	struct biospur_uart_link_stats uart_stats;
	struct ss_twr_init_poll_tx_stats poll_stats;
	struct uwb_tag_runtime_params params;
	uint32_t now_ms = (uint32_t)k_uptime_get();
	bool fast_on;
	bool slow_on;
	bool health_fault;
	bool tdma_configured;
	bool tdma_running;
	atomic_val_t ble_state;
	struct tag_led_policy_input policy_input;
	struct tag_led_policy_output policy_output;

	if (!tag_led_ready ||
	    (uint32_t)(now_ms - tag_led_last_render_ms) <
		    UWB_TAG_LED_RENDER_PERIOD_MS) {
		return;
	}
	tag_led_last_render_ms = now_ms;
	fast_on = ((now_ms / 100U) & 1U) == 0U;
	slow_on = ((now_ms / 500U) & 1U) == 0U;

	biospur_uart_link_get_stats(&uart_stats);
	ss_twr_init_poll_tx_stats_snapshot(&poll_stats);
	health_fault = uart_stats.tx_dropped != 0U ||
		       uart_stats.tx_failed != 0U ||
		       uart_stats.tx_aborted != 0U ||
		       poll_stats.failures != 0U ||
		       poll_stats.slot_sleep_late_skips != 0U ||
		       poll_stats.slot_spin_late_skips != 0U;
	k_mutex_lock(&ble_mutex, K_FOREVER);
	params = active_runtime_params;
	k_mutex_unlock(&ble_mutex);
	tdma_configured = params.tdma.epoch_valid;
	tdma_running = tdma_configured && params.tdma.enabled &&
		       params.positioning_mode != UWB_TAG_MODE_IDLE &&
		       (params.tdma.slot_active_us != 0U ||
			params.tdma.slot_active_ms != 0U);
	ble_state = atomic_get(&tag_led_ble_state);
	policy_input = (struct tag_led_policy_input) {
		.uwb_ready = atomic_get(&tag_led_uwb_ready) != 0,
		.health_fault = health_fault,
		.tdma_configured = tdma_configured,
		.tdma_running = tdma_running,
		.ble_state = (uint8_t)ble_state,
		.slow_phase_on = slow_on,
		.fast_phase_on = fast_on,
	};
	policy_output = tag_led_policy_evaluate(&policy_input);

	tag_led_set_if_changed(&tag_health_led, policy_output.health_on,
			       &tag_health_led_level);
	tag_led_set_if_changed(&tag_tdma_led, policy_output.tdma_on,
			       &tag_tdma_led_level);
	tag_led_set_if_changed(&tag_ble_led, policy_output.ble_on,
			       &tag_ble_led_level);
}

void uwb_tag_ble_led_set_uwb_ready(bool ready)
{
	atomic_set(&tag_led_uwb_ready, ready ? 1 : 0);
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
			k_fifo_get(&ble_tx_fifo,
				   K_MSEC(UWB_TAG_LED_RENDER_PERIOD_MS));
		uint8_t active_conns;
		int err;

		if (item == NULL) {
			tag_led_render();
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
				ble_tx_send_fail_count++;
				ble_tx_last_err = err;
				ble_tx_drop_count++;
				if ((ble_tx_drop_count % 50U) == 1U) {
					printk("Tag BLE notify failed err=%d drops=%u\n",
					       err,
					       (unsigned int)ble_tx_drop_count);
				}
			} else if (err == 0) {
				ble_tx_send_ok_count++;
				ble_tx_send_bytes += item->len;
				ble_tx_last_err = 0;
			}
		}

		k_mem_slab_free(&ble_tx_slab, (void *)item);
		tag_led_render();
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

static void uwb_tag_ble_process_command(const char *cmd)
{
	if (strcmp(cmd, "PING") == 0) {
		uwb_tag_ble_send_text("PONG");
		return;
	}

	if (strcmp(cmd, "STATUS") == 0) {
		char snapshot[UWB_TAG_BLE_MAX_STATUS_LEN];
		char last_snapshot[97];
		struct ss_twr_init_beacon_status beacon_status;

		k_mutex_lock(&ble_mutex, K_FOREVER);
		snprintk(last_snapshot, sizeof(last_snapshot), "%.96s",
			 last_status);
		k_mutex_unlock(&ble_mutex);

		/*
		 * Put the machine-readable beacon fields first.  A TR status line
		 * can already fill the transport buffer; appending these fields
		 * made them disappear exactly when STATUS was most useful.
		 * Report them even before the first TR record.
		 */
		ss_twr_init_beacon_status_snapshot(&beacon_status);
		(void)snprintk(
			snapshot, sizeof(snapshot),
			"STATUS rx_beacon=%lu beacon_ctr=%lu beacon_gen=%u promoted=%u beacon_period_mismatch=%lu beacon_miss=%lu beacon_lock=%u beacon_sync=%u last=%s",
			(unsigned long)beacon_status.rx_beacon,
			(unsigned long)beacon_status.last_counter,
			(unsigned int)beacon_status.last_generation,
			beacon_status.promoted_source_in_use ? 1U : 0U,
			(unsigned long)beacon_status.period_mismatch,
			(unsigned long)beacon_status.missed_windows,
			beacon_status.locked ? 1U : 0U,
			beacon_status.enabled ? 1U : 0U,
			last_snapshot);
		uwb_tag_ble_send_text(snapshot);
		return;
	}

#if APP_TAG_RELAY6_BEACON_STATUS_ENABLE != 0U
	if (strcmp(cmd, "BEACON_STATUS") == 0) {
		char resp[191];
		struct ss_twr_init_beacon_status status;

		ss_twr_init_beacon_status_snapshot(&status);
		(void)snprintk(
			resp, sizeof(resp),
			TAG_RELAY7_BEACON_STATUS_FORMAT,
			status.enabled ? 1U : 0U,
			status.locked ? 1U : 0U,
			(unsigned long)status.rx_beacon,
			status.promoted_source_in_use ? 1U : 0U,
			(unsigned long)status.period_mismatch,
			(unsigned long)status.missed_windows,
			(unsigned int)status.last_generation,
			(unsigned long)status.last_counter,
			(unsigned long)status.generation_rebases,
			status.dw_anchor ? 1U : 0U,
				(unsigned long)status.dw_anchor_fallbacks,
				(unsigned int)status.beacon_win_n,
				(unsigned long)status.beacon_rx_arm_failures);
		uwb_tag_ble_send_text(resp);
		return;
	}
#endif

	if (strcmp(cmd, "IMGSTAT") == 0) {
		uint8_t hash[UWB_TAG_IMAGE_SHA_LEN];
		char resp[128];
		size_t offset;
		int err = uwb_tag_ble_read_active_image_hash(hash);

		if (err != 0) {
			snprintk(resp, sizeof(resp),
				 "IMGSTAT_ERR image=0 slot=0 rc=%d", err);
			uwb_tag_ble_send_text(resp);
			return;
		}
		offset = (size_t)snprintk(resp, sizeof(resp),
					 "IMGSTAT image=0 slot=0 hash=");
		for (size_t i = 0U; i < ARRAY_SIZE(hash) &&
		     offset + 2U < sizeof(resp); ++i) {
			offset += (size_t)snprintk(
				&resp[offset], sizeof(resp) - offset,
				"%02x", hash[i]);
		}
		(void)snprintk(&resp[offset], sizeof(resp) - offset,
			      " confirmed=%u",
			      boot_is_img_confirmed() ? 1U : 0U);
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strcmp(cmd, "BSL_STATUS") == 0) {
		uwb_tag_ble_publish_link_status();
		return;
	}

	if (strcmp(cmd, "TR?") == 0 || strcmp(cmd, "CAPTURE?") == 0) {
		bool tr_enabled = uwb_tag_ble_tr_enabled();
		struct uwb_tag_ble_conn_params_status conn_status;
		char resp[112];

		uwb_tag_ble_conn_params_snapshot(&conn_status);

		snprintk(resp, sizeof(resp),
			 "CAPTURE_STATE=%s TR=%s REQCI=%u INTERVAL_US=%lu REQSUP=%u TIMEOUT_MS=%lu",
			 tr_enabled ? "OFF" : "ON",
			 tr_enabled ? "ON" : "OFF",
			 (unsigned int)conn_status.requested_interval,
			 (unsigned long)conn_status.requested_interval * 1250UL,
			 (unsigned int)conn_status.requested_timeout,
			 (unsigned long)conn_status.requested_timeout * 10UL);
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strncmp(cmd, "CAPTURE PARAM ", 14) == 0) {
		const char *arg = cmd + 14;
		char *end = NULL;
		unsigned long interval;
		unsigned long timeout;
		int err;
		char resp[128];

		interval = strtoul(arg, &end, 10);
		if (end == arg) {
			uwb_tag_ble_send_text("CAPTURE_PARAM_BAD FORMAT=<interval_units> <timeout_units>");
			return;
		}
		while (*end == ' ' || *end == '\t') {
			end++;
		}
		arg = end;
		timeout = strtoul(arg, &end, 10);
		while (*end == ' ' || *end == '\t') {
			end++;
		}
		if (end == arg || *end != '\0' || interval > UINT16_MAX ||
		    timeout > UINT16_MAX) {
			uwb_tag_ble_send_text("CAPTURE_PARAM_BAD FORMAT=<interval_units> <timeout_units>");
			return;
		}

		err = uwb_tag_ble_set_capture_conn_params((uint16_t)interval,
						      (uint16_t)timeout);
		if (err != 0) {
			snprintk(resp, sizeof(resp),
				 "CAPTURE_PARAM_BAD CI=%lu SUP=%lu RC=%d REQUIRE=CAPTURE_OFF,CI=6..3200,SUP=10..3200",
				 interval, timeout, err);
		} else {
			snprintk(resp, sizeof(resp),
				 "CAPTURE_PARAM_OK CI=%lu INTERVAL_US=%lu SUP=%lu TIMEOUT_MS=%lu",
				 interval, interval * 1250UL, timeout,
				 timeout * 10UL);
		}
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strncmp(cmd, "TR ", 3) == 0 ||
	    strncmp(cmd, "CAPTURE ", 8) == 0) {
		const char *arg =
			(cmd[0] == 'T') ? cmd + 3 : cmd + 8;
		bool capture_enabled;
		uint16_t requested_interval;
		uint16_t requested_timeout;
		int err;
		char resp[128];

		if (uwb_tag_ble_ota_active()) {
			uwb_tag_ble_send_text("ERR:BUSY_OTA");
			return;
		}
		while (*arg == ' ' || *arg == '\t') {
			arg++;
		}
		if (cmd[0] == 'T') {
			if (strcmp(arg, "ON") == 0) {
				capture_enabled = false;
			} else if (strcmp(arg, "OFF") == 0) {
				capture_enabled = true;
			} else {
				uwb_tag_ble_send_text("TR_BAD STATE=ON|OFF");
				return;
			}
		} else if (strcmp(arg, "ON") == 0) {
			capture_enabled = true;
		} else if (strcmp(arg, "OFF") == 0) {
			capture_enabled = false;
		} else {
			uwb_tag_ble_send_text("CAPTURE_BAD STATE=ON|OFF");
			return;
		}

		err = uwb_tag_ble_set_capture_mode(capture_enabled);
		k_mutex_lock(&ble_mutex, K_FOREVER);
		requested_interval = capture_enabled ?
			capture_conn_params->interval_min : fast_conn_params->interval_min;
		requested_timeout = capture_enabled ?
			capture_conn_params->timeout : fast_conn_params->timeout;
		k_mutex_unlock(&ble_mutex);
		snprintk(resp, sizeof(resp),
			 "CAPTURE_OK STATE=%s TR=%s REQCI=%u INTERVAL_US=%lu REQSUP=%u TIMEOUT_MS=%lu RC=%d",
			 capture_enabled ? "ON" : "OFF",
			 capture_enabled ? "OFF" : "ON",
			 (unsigned int)requested_interval,
			 (unsigned long)requested_interval * 1250UL,
			 (unsigned int)requested_timeout,
			 (unsigned long)requested_timeout * 10UL,
			 err);
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strcmp(cmd, "TDMA_STATUS") == 0) {
		char resp[128];
		struct uwb_tag_runtime_params params;

		(void)uwb_tag_ble_runtime_config_get(&params);
		snprintk(resp, sizeof(resp),
			 "TDMA_SLOT=%u/%u MASK=0x%04X SOURCE=%s PERIOD=%u ACTIVE=%u ACTIVE_US=%u GEN=%u BEACON_SYNC=%u BEACON_WIN_N=%u",
			 (unsigned int)params.tdma.slot_index,
			 (unsigned int)params.tdma.slot_count,
			 (unsigned int)params.tdma.slot_mask,
			 uwb_tag_ble_slot_source_label(params.slot_source),
			 (unsigned int)params.tdma.slot_period_ms,
			 (unsigned int)params.tdma.slot_active_ms,
			 (unsigned int)params.tdma.slot_active_us,
			 (unsigned int)params.tdma.generation,
			 params.beacon_sync ? 1U : 0U,
			 (unsigned int)params.beacon_win_n);
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strncmp(cmd, "TXPWR ", 6) == 0) {
		const char *arg = cmd + 6;
		uint32_t applied = 0U;
		char resp[48];

		while (*arg == ' ' || *arg == '\t') {
			arg++;
		}
		if (ss_twr_init_tx_power_apply(arg, &applied) != 0) {
			uwb_tag_ble_send_text("TXPWR_BAD PRESET=MAX|M3|M6|M12|POR");
			return;
		}
		snprintk(resp, sizeof(resp), "TXPWR_OK VAL=0x%08X", (unsigned int)applied);
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strcmp(cmd, "DIAG?") == 0) {
		uwb_tag_ble_send_text(ss_twr_init_rf_diag_runtime_enabled() ?
					      "DIAG_OK STATE=ON" : "DIAG_OK STATE=OFF");
		return;
	}

	if (strncmp(cmd, "DIAG ", 5) == 0) {
		const char *arg = cmd + 5;

		while (*arg == ' ' || *arg == '\t') {
			arg++;
		}
		if (strcmp(arg, "ON") == 0) {
			ss_twr_init_set_rf_diag_runtime(true);
			uwb_tag_ble_send_text("DIAG_OK STATE=ON");
		} else if (strcmp(arg, "OFF") == 0) {
			ss_twr_init_set_rf_diag_runtime(false);
			uwb_tag_ble_send_text("DIAG_OK STATE=OFF");
		} else {
			uwb_tag_ble_send_text("DIAG_BAD STATE=ON|OFF");
		}
		return;
	}

	if (strcmp(cmd, "CIR?") == 0 || strcmp(cmd, "CIR_STATUS") == 0) {
		uwb_tag_ble_send_cir_status();
		return;
	}

	if (strncmp(cmd, "CIR ", 4) == 0 ||
	    strncmp(cmd, "TAG CIR ", 8) == 0) {
		const char *arg = (cmd[0] == 'C') ? cmd + 4 : cmd + 8;
		enum uwb_tag_cir_mode cir_mode;
		char resp[80];

		while (*arg == ' ' || *arg == '\t') {
			arg++;
		}
		if (ss_twr_init_cir_mode_parse(arg, &cir_mode) != 0) {
			uwb_tag_ble_send_text("CIR_BAD MODE=OFF|COMPACT|FULL");
			return;
		}
		if (!uwb_tag_ble_cir_mode_supported(cir_mode)) {
			snprintk(resp, sizeof(resp), "CIR_UNSUPPORTED MODE=%s",
				 ss_twr_init_cir_mode_label(cir_mode));
			uwb_tag_ble_send_text(resp);
			return;
		}
		if (uwb_tag_ble_ota_active()) {
			uwb_tag_ble_send_text("ERR:BUSY_OTA");
			return;
		}

		(void)ss_twr_init_cir_mode_set(cir_mode);
		k_mutex_lock(&ble_mutex, K_FOREVER);
		uwb_tag_ble_clear_pending_cal_locked();
		uwb_tag_ble_clear_pending_samples_locked();
		uwb_tag_ble_clear_pending_bundle_locked();
		k_mutex_unlock(&ble_mutex);
		uwb_tag_ble_cancel_bundle_flush();

		snprintk(resp, sizeof(resp), "CIR_OK MODE=%s RAW_RANGE=1",
			 ss_twr_init_cir_mode_label(cir_mode));
		uwb_tag_ble_send_text(resp);
		return;
	}

	/*
	 * Fusion-fork APOS was removed after the v2-clean1 zero-reader audit:
	 * layout is host-side here. The freeze fork intentionally keeps the
	 * production receiver for push_apos_layout_verified.py. If a future
	 * high-compute tag needs it, use uwb_tag_ble.c / uwb_anchor_layout.c at
	 * freeze-clean-20260716 as the reference implementation.
	 */

	if (strcmp(cmd, "VERSION") == 0) {
		char resp[176];
		struct uwb_tag_runtime_params params;
		enum uwb_tag_cir_mode cir_mode = ss_twr_init_cir_mode_get();

		(void)uwb_tag_ble_runtime_config_get(&params);
		snprintk(resp, sizeof(resp),
			 "VERSION fw=%s bs=BS%04X tag=%u mode=%s pmode=%u anchor_plan=dynamic cir=%s caps=ota,run,beacon_sync,beacon_win_n,imgstat%s%s",
			 APP_TAG_FW_MARKER,
			 (unsigned int)params.identity_code,
			 (unsigned int)params.logical_tag_id,
			 uwb_tag_ble_mode_label(params.positioning_mode),
			 (unsigned int)params.positioning_mode,
			 ss_twr_init_cir_mode_label(cir_mode),
			 (APP_TAG_CIR_FEATURE_OUTPUT_ENABLE != 0U) ? ",cir_compact" : "",
			 (APP_TAG_CIR_FULL_OUTPUT_ENABLE != 0U &&
			  APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE != 0U) ? ",cir_full_usb" : "");
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strcmp(cmd, "CFG_STATUS") == 0) {
		char resp[256];
		struct uwb_tag_runtime_params params;

		(void)uwb_tag_ble_runtime_config_get(&params);
		snprintk(resp, sizeof(resp),
			 "CFG tag=%u bs=BS%04X slot=%u/%u mask=0x%04X src=%s period=%u active=%u active_us=%u epoch=%lu gen=%u superframe_base=%lu sf_valid=%u run=%u state=%s mode=%s pmode=%u beacon_sync=%u beacon_win_n=%u anchor_plan=dynamic cir=%s",
			 (unsigned int)params.logical_tag_id,
			 (unsigned int)params.identity_code,
			 (unsigned int)params.tdma.slot_index,
			 (unsigned int)params.tdma.slot_count,
			 (unsigned int)params.tdma.slot_mask,
			 uwb_tag_ble_slot_source_label(params.slot_source),
			 (unsigned int)params.tdma.slot_period_ms,
			 (unsigned int)params.tdma.slot_active_ms,
			 (unsigned int)params.tdma.slot_active_us,
			 (unsigned long)params.tdma.epoch_ms,
			 (unsigned int)params.tdma.generation,
			 (unsigned long)params.tdma.superframe_base,
			 params.tdma.superframe_valid ? 1U : 0U,
			 params.tdma.enabled ? 1U : 0U,
			 params.tdma.enabled ? "RUNNING" : "ARMED",
			 uwb_tag_ble_mode_label(params.positioning_mode),
			 (unsigned int)params.positioning_mode,
			 params.beacon_sync ? 1U : 0U,
			 (unsigned int)params.beacon_win_n,
			 ss_twr_init_cir_mode_label(ss_twr_init_cir_mode_get()));
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strcmp(cmd, "MODE?") == 0) {
		char resp[128];
		struct uwb_tag_runtime_params params;

		(void)uwb_tag_ble_runtime_config_get(&params);
		snprintk(resp, sizeof(resp),
			 "MODE=%s PMODE=%u ANCHOR_PLAN=dynamic TDMA=%u SLOT=%u/%u MASK=0x%04X SRC=%s",
			 uwb_tag_ble_mode_label(params.positioning_mode),
			 (unsigned int)params.positioning_mode,
			 (unsigned int)params.tdma.enabled,
			 (unsigned int)params.tdma.slot_index,
			 (unsigned int)params.tdma.slot_count,
			 (unsigned int)params.tdma.slot_mask,
			 uwb_tag_ble_slot_source_label(params.slot_source));
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strncmp(cmd, "MODE ", 5) == 0) {
		struct uwb_tag_runtime_params params;
		uint8_t requested_mode = UWB_TAG_MODE_RUN;
		const char *arg = cmd + 5;
		int live_err;
		int store_err;
		int policy_err;
		char resp[96];

		/* freeze-clean batch3: MMOT removed (footgun — strncmp("MMOT",4)
		 * prefix-matched "MMOT<suffix>" into a misparse; exact duplicate of
		 * "MODE RUN"; zero senders anywhere). */
		while (*arg == ' ' || *arg == '\t') {
			arg++;
		}
		if (*arg == '\0' || !uwb_tag_ble_parse_mode_value(arg, &requested_mode)) {
			uwb_tag_ble_send_text("MODE_BAD");
			return;
		}

		if (uwb_tag_ble_ota_active()) {
			uwb_tag_ble_send_text("ERR:BUSY_OTA");
			return;
		}

		(void)uwb_tag_ble_runtime_config_get(&params);
		params.positioning_mode = requested_mode;
		policy_err = uwb_tag_ble_apply_mode_policy(&params);
		if (policy_err == -EINVAL) {
			uwb_tag_ble_send_text("ERR:MODE_POLICY");
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
			 "CFG_OK TAG=%u SLOT=%u/%u MASK=0x%04X PERIOD=%u ACTIVE=%u ACTIVE_US=%u GEN=%u BEACON_SYNC=%u BEACON_WIN_N=%u"
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
			 " DW_ANCHOR=%u"
#endif
			 " LIVE=%u RUN=%u STATE=%s",
			 (unsigned int)params.logical_tag_id,
			 (unsigned int)params.tdma.slot_index,
			 (unsigned int)params.tdma.slot_count,
			 (unsigned int)params.tdma.slot_mask,
			 (unsigned int)params.tdma.slot_period_ms,
			 (unsigned int)params.tdma.slot_active_ms,
			 (unsigned int)params.tdma.slot_active_us,
			 (unsigned int)params.tdma.generation,
			 params.beacon_sync ? 1U : 0U,
			 (unsigned int)params.beacon_win_n,
#if APP_TAG_RELAY6_DW_ANCHOR_ENABLE != 0U
			 params.dw_anchor ? 1U : 0U,
#endif
			 (unsigned int)((live_err == 0) ? 1U : 0U),
			 params.tdma.enabled ? 1U : 0U,
			 params.tdma.enabled ? "RUNNING" : "ARMED");
		uwb_tag_ble_send_text(resp);
		return;
	}

	if (strcmp(cmd, "CFG_RUN") == 0) {
		uwb_tag_ble_set_cfg_run_state(true);
		return;
	}

	if (strcmp(cmd, "CFG_STOP") == 0) {
		uwb_tag_ble_set_cfg_run_state(false);
		return;
	}

	if (strcmp(cmd, "HELP") == 0) {
#if APP_TAG_BLE_OTA_ENABLE
		uwb_tag_ble_send_text(
			"PING|STATUS" UWB_TAG_RELAY6_HELP_BEACON
			"|IMGSTAT|BSL_STATUS|TR?|TR <ON|OFF>|CAPTURE?|CAPTURE PARAM <ci_units> <sup_units>|CAPTURE <ON|OFF>|VERSION|TDMA_STATUS|CFG_STATUS|CIR?|CIR <OFF|COMPACT|FULL>|TXPWR <MAX|M3|M6|M12|POR>|DIAG <ON|OFF>|MODE?|MODE <RUN|IDLE>|TDMA_SET <slot>|CFG TAG=<id> SLOT=<slot> COUNT=<count> MASK=<hex> PERIOD=<ms> ACTIVE=<ms> EPOCH=<ms> SUPERFRAME_BASE=<n> GEN=<n> BEACON_SYNC=<0|1>"
			UWB_TAG_RELAY7_HELP_WIN
			UWB_TAG_RELAY6_HELP_DW
			" RUN=<0|1> PMODE=<0|3>|CFG_RUN|CFG_STOP|OTA_STATUS|OTA_PREPARE|OTA_BEGIN|OTA_CANCEL|REBOOT|HELP");
#else
		uwb_tag_ble_send_text(
			"PING|STATUS" UWB_TAG_RELAY6_HELP_BEACON
			"|IMGSTAT|BSL_STATUS|TR?|TR <ON|OFF>|CAPTURE?|CAPTURE PARAM <ci_units> <sup_units>|CAPTURE <ON|OFF>|VERSION|TDMA_STATUS|CFG_STATUS|CIR?|CIR <OFF|COMPACT|FULL>|TXPWR <MAX|M3|M6|M12|POR>|DIAG <ON|OFF>|MODE?|MODE <RUN|IDLE>|TDMA_SET <slot>|CFG TAG=<id> SLOT=<slot> COUNT=<count> MASK=<hex> PERIOD=<ms> ACTIVE=<ms> EPOCH=<ms> SUPERFRAME_BASE=<n> GEN=<n> BEACON_SYNC=<0|1>"
			UWB_TAG_RELAY7_HELP_WIN
			UWB_TAG_RELAY6_HELP_DW
			" RUN=<0|1> PMODE=<0|3>|CFG_RUN|CFG_STOP|REBOOT|HELP");
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
		int link_err;
		int conn_err;

		conn_err = uwb_tag_ble_request_conn_params(false);
		if (conn_err != 0) {
			char resp[48];

			snprintk(resp, sizeof(resp),
				 "OTA_PREPARE_FAIL CONN_RC=%d", conn_err);
			uwb_tag_ble_send_text(resp);
			return;
		}
		k_mutex_lock(&ble_mutex, K_FOREVER);
		ota_ready = true;
		ota_active = true;
		uwb_tag_ble_clear_pending_cal_locked();
		uwb_tag_ble_clear_pending_samples_locked();
		uwb_tag_ble_clear_pending_bundle_locked();
		k_mutex_unlock(&ble_mutex);
		uwb_tag_ble_cancel_bundle_flush();
		uwb_tag_ble_purge_tx_queue();
		link_err = biospur_uart_link_suspend();
		if (link_err != 0) {
			char resp[48];

			k_mutex_lock(&ble_mutex, K_FOREVER);
			ota_ready = false;
			ota_active = false;
			k_mutex_unlock(&ble_mutex);
			biospur_uart_link_resume();
			snprintk(resp, sizeof(resp),
				 "OTA_PREPARE_FAIL LINK_RC=%d", link_err);
			uwb_tag_ble_send_text(resp);
			return;
		}
		uwb_tag_ble_send_text("OTA_READY");
		return;
	}

	if (strcmp(cmd, "OTA_BEGIN") == 0) {
		int link_err;
		int conn_err;

		if (!ota_ready) {
			uwb_tag_ble_send_text("OTA_NOT_ARMED");
			return;
		}
		conn_err = uwb_tag_ble_request_conn_params(false);
		if (conn_err != 0) {
			char resp[48];

			snprintk(resp, sizeof(resp),
				 "OTA_BEGIN_FAIL CONN_RC=%d", conn_err);
			uwb_tag_ble_send_text(resp);
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
		link_err = biospur_uart_link_suspend();
		if (link_err != 0) {
			char resp[48];

			snprintk(resp, sizeof(resp),
				 "OTA_BEGIN_FAIL LINK_RC=%d", link_err);
			uwb_tag_ble_send_text(resp);
			return;
		}
		uwb_tag_ble_send_text("OTA_BEGIN_OK");
		return;
	}

	if (strcmp(cmd, "OTA_CANCEL") == 0) {
		bool capture_enabled;
		int conn_err;

		ota_active = false;
		ota_ready = false;
		biospur_uart_link_resume();
		capture_enabled = !uwb_tag_ble_tr_enabled();
		conn_err = uwb_tag_ble_request_conn_params(capture_enabled);
		if (conn_err == 0) {
			uwb_tag_ble_send_text("OTA_CANCELLED RUN=1");
		} else {
			char resp[48];

			snprintk(resp, sizeof(resp),
				 "OTA_CANCELLED RUN=1 CONN_RC=%d", conn_err);
			uwb_tag_ble_send_text(resp);
		}
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
		/*
		 * Path M keeps its established 150 ms behavior. Path R allows
		 * the bounded UART ACK scheduler to finish before reboot.
		 */
		(void)k_work_reschedule(
			&reboot_work,
			K_MSEC(active_command_source ==
				       UWB_TAG_COMMAND_SOURCE_UART ? 1200 : 150));
		return;
	}

	uwb_tag_ble_send_text("UNKNOWN_CMD");
}

static int uwb_tag_ble_reply_to_ble(void *context, const char *text)
{
	ARG_UNUSED(context);
	uwb_tag_ble_send_payload((const uint8_t *)text, strlen(text));
	return 0;
}

static int uwb_tag_ble_reply_to_uart(void *context, const char *text)
{
	uint16_t correlation = *(const uint16_t *)context;

	return biospur_uart_link_send_ack(correlation, text);
}

static void uwb_tag_ble_parse(const char *line,
			      enum uwb_tag_command_source source,
			      uwb_tag_reply_sink_t reply_sink,
			      void *reply_context)
{
	k_mutex_lock(&command_dispatch_mutex, K_FOREVER);
	active_reply_thread = k_current_get();
	active_reply_sink = reply_sink;
	active_reply_context = reply_context;
	active_command_source = source;
	uwb_tag_ble_process_command(line);
	active_reply_thread = NULL;
	active_reply_sink = NULL;
	active_reply_context = NULL;
	active_command_source = UWB_TAG_COMMAND_SOURCE_BLE;
	k_mutex_unlock(&command_dispatch_mutex);
}

static void uwb_tag_ble_dispatch_bytes(const uint8_t *data, size_t len,
				       enum uwb_tag_command_source source,
				       uint16_t correlation)
{
	char cmd[UWB_TAG_BLE_MAX_CMD_LEN];

	if (data == NULL || len == 0U) {
		return;
	}
	if (len >= sizeof(cmd)) {
		len = sizeof(cmd) - 1U;
	}
	memcpy(cmd, data, len);
	cmd[len] = '\0';
	while (len > 0U && (cmd[len - 1U] == '\r' || cmd[len - 1U] == '\n' ||
			    cmd[len - 1U] == ' ' || cmd[len - 1U] == '\t')) {
		cmd[--len] = '\0';
	}
	if (len == 0U) {
		return;
	}

	if (source == UWB_TAG_COMMAND_SOURCE_UART) {
		uwb_tag_ble_parse(cmd, source, uwb_tag_ble_reply_to_uart,
				  &correlation);
	} else {
		uwb_tag_ble_parse(cmd, source, uwb_tag_ble_reply_to_ble, NULL);
	}
}

static void uwb_tag_ble_uart_received(const char *line, uint16_t correlation)
{
	uwb_tag_ble_dispatch_bytes((const uint8_t *)line, strlen(line),
				   UWB_TAG_COMMAND_SOURCE_UART, correlation);
}

static void ble_received(struct bt_conn *conn, const uint8_t *const data,
			 uint16_t len)
{
	ARG_UNUSED(conn);
	uwb_tag_ble_dispatch_bytes(data, len, UWB_TAG_COMMAND_SOURCE_BLE, 0U);
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
	int err;

	k_mutex_init(&ble_mutex);
	k_mutex_lock(&ble_mutex, K_FOREVER);
	uwb_tag_ble_runtime_params_reset_locked();
	k_mutex_unlock(&ble_mutex);
	err = tag_led_init();
	if (err != 0) {
		printk("Tag status LED init failed: %d\n", err);
	}
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
	k_work_init_delayable(&self_confirm_guard_work,
			      self_confirm_guard_work_handler);
#if APP_TAG_BLE_STATS_ENABLE != 0U
	k_work_init_delayable(&ble_stats_work, ble_stats_work_handler);
	(void)k_work_reschedule(&ble_stats_work,
				 K_MSEC(UWB_TAG_BLE_STATS_PERIOD_MS));
#endif

	biospur_uart_link_set_command_handler(uwb_tag_ble_uart_received);
#if APP_TAG_SELF_CONFIRM_MODE != UWB_TAG_SELF_CONFIRM_PROOF_NOCONFIRM
	if (!boot_is_img_confirmed()) {
		(void)k_work_reschedule(&self_confirm_guard_work,
				       K_MSEC(APP_TAG_SELF_CONFIRM_TIMEOUT_MS));
	}
#else
	printk("MCUboot proof-noconfirm: rollback guard intentionally disabled for manual OS_RESET\n");
#endif
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
