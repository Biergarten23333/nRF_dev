#include "uwb_tag_ble.h"

#include <string.h>

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

#ifndef CONFIG_BT_DEVICE_NAME
#define CONFIG_BT_DEVICE_NAME "Tag_rot"
#endif

#ifndef APP_TAG_BLE_OTA_ENABLE
#define APP_TAG_BLE_OTA_ENABLE 1U
#endif

#ifndef APP_TAG_BLE_SETTINGS_ENABLE
#define APP_TAG_BLE_SETTINGS_ENABLE 1U
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

#define UWB_TAG_BLE_MAX_STATUS_LEN 256U
/* Keep ~20% headroom so bundled NUS payloads do not sit on the limit. */
#define UWB_TAG_BLE_BUNDLE_PAYLOAD_CAP 180U
#define UWB_TAG_BLE_MAX_CMD_LEN 32U
#define UWB_TAG_BLE_TX_THREAD_STACK 1536
#define UWB_TAG_BLE_TX_THREAD_PRIO 7
#define UWB_TAG_BLE_TX_ITEM_COUNT 12U
#define UWB_TAG_BLE_BINARY_MAGIC0 0x42U
#define UWB_TAG_BLE_BINARY_MAGIC1 0x50U
#define UWB_TAG_BLE_BINARY_VERSION 1U
#define UWB_TAG_BLE_BINARY_HEADER_LEN 5U
#define UWB_TAG_BLE_BINARY_RECORD_LEN 24U
#define UWB_TAG_BLE_MAX_BINARY_RECORDS 4U

struct uwb_tag_ble_tx_item {
	void *fifo_reserved;
	size_t len;
	uint8_t payload[UWB_TAG_BLE_MAX_STATUS_LEN];
};

static const uint8_t adv_mfg_token[] = {
	0xff, 0xff, 'B', (uint8_t)APP_TAG_BLE_TOKEN_ID,
};

static const struct bt_conn_le_phy_param *const fast_phy_params = BT_CONN_LE_PHY_PARAM_2M;
static const struct bt_le_conn_param *const fast_conn_params = BT_LE_CONN_PARAM(6, 6, 0, 400);

static const struct bt_data ad[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
#if APP_TAG_BLE_OTA_ENABLE
	BT_DATA_BYTES(BT_DATA_UUID128_ALL,
		      0x84, 0xaa, 0x60, 0x74, 0x52, 0x8a, 0x8b, 0x86,
		      0xd3, 0x4c, 0xb7, 0x1d, 0x1d, 0xdc, 0x53, 0x8d),
#endif
	BT_DATA(BT_DATA_MANUFACTURER_DATA, adv_mfg_token, sizeof(adv_mfg_token)),
};

static const struct bt_data sd[] = {
	BT_DATA(BT_DATA_NAME_COMPLETE,
		CONFIG_BT_DEVICE_NAME,
		sizeof(CONFIG_BT_DEVICE_NAME) - 1U),
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
static char last_status[UWB_TAG_BLE_MAX_STATUS_LEN];
static char pending_bundle[UWB_TAG_BLE_MAX_STATUS_LEN];
static size_t pending_bundle_len;
static uint8_t pending_bundle_records;
static struct uwb_tag_ble_sample pending_samples[UWB_TAG_BLE_MAX_BINARY_RECORDS];
static uint8_t pending_sample_count;
static struct bt_nus_cb nus_cb;

static int uwb_tag_ble_start_advertising(void);
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

static void ble_reboot_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);

	printk("Tag BLE rebooting on remote command\n");
	sys_reboot(SYS_REBOOT_COLD);
}

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

	printk("Tag BLE set name skipped; using Kconfig name=%s\n",
	       CONFIG_BT_DEVICE_NAME);

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
	printk("Tag BLE advertising as %s\n", CONFIG_BT_DEVICE_NAME);
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
				  strstr(line, "TS ") != NULL);
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
	out[offset++] = (uint8_t)APP_TAG_BLE_TOKEN_ID;

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

static void uwb_tag_ble_flush_work_handler(struct k_work *work)
{
	char snapshot[UWB_TAG_BLE_MAX_STATUS_LEN];
	uint8_t binary_snapshot[UWB_TAG_BLE_MAX_STATUS_LEN];
	size_t binary_len = 0U;

	ARG_UNUSED(work);

	k_mutex_lock(&ble_mutex, K_FOREVER);
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
		printk("Tag BLE tx pool exhausted\n");
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

		k_mutex_lock(&ble_mutex, K_FOREVER);
		active_conns = ble_conn_count;
		k_mutex_unlock(&ble_mutex);

		if (active_conns > 0U) {
			err = bt_nus_send(NULL, item->payload, item->len);
			if (err && err != -ENOTCONN) {
				printk("Tag BLE notify failed: %d\n", err);
			}
		}

		k_mem_slab_free(&ble_tx_slab, (void *)item);
		k_yield();
	}
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
		if (pending_sample_count > 0U &&
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

	if (strcmp(cmd, "HELP") == 0) {
#if APP_TAG_BLE_OTA_ENABLE
		uwb_tag_ble_send_text("PING|STATUS|OTA_STATUS|OTA_PREPARE|OTA_BEGIN|OTA_CANCEL|REBOOT|HELP");
#else
		uwb_tag_ble_send_text("PING|STATUS|REBOOT|HELP");
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
		ota_ready = true;
		ota_active = false;
		uwb_tag_ble_send_text("OTA_READY");
		return;
	}

	if (strcmp(cmd, "OTA_BEGIN") == 0) {
		if (!ota_ready) {
			uwb_tag_ble_send_text("OTA_NOT_ARMED");
			return;
		}

		ota_active = true;
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
