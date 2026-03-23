#include "uwb_tag_ble.h"

#include <string.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#include <bluetooth/services/nus.h>
#include <bluetooth/services/dfu_smp.h>

#ifndef CONFIG_BT_DEVICE_NAME
#define CONFIG_BT_DEVICE_NAME "Tag_rot"
#endif

#define UWB_TAG_BLE_MAX_STATUS_LEN 192U
#define UWB_TAG_BLE_MAX_CMD_LEN 32U

static const struct bt_data ad[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
	BT_DATA_BYTES(BT_DATA_UUID128_ALL, BT_UUID_DFU_SMP_SERVICE_VAL),
};

static const struct bt_data sd[] = {
	BT_DATA_BYTES(BT_DATA_UUID128_ALL, BT_UUID_NUS_VAL),
	BT_DATA(BT_DATA_NAME_COMPLETE, CONFIG_BT_DEVICE_NAME,
		sizeof(CONFIG_BT_DEVICE_NAME) - 1),
};

static struct k_mutex ble_mutex;
static bool ble_ready;
static bool ota_ready;
static bool ota_active;
static char last_status[UWB_TAG_BLE_MAX_STATUS_LEN];

static int uwb_tag_ble_start_advertising(void)
{
	int err;

	err = bt_le_adv_start(BT_LE_ADV_CONN, ad, ARRAY_SIZE(ad), sd,
			      ARRAY_SIZE(sd));
	if (err == -EALREADY) {
		return 0;
	}

	return err;
}

static void ble_connected(struct bt_conn *conn, uint8_t conn_err)
{
	char addr[BT_ADDR_LE_STR_LEN];

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));

	if (conn_err) {
		printk("Tag BLE connect failed: %s err=0x%02x\n", addr, conn_err);
		return;
	}

	printk("Tag BLE connected: %s\n", addr);
}

static void ble_disconnected(struct bt_conn *conn, uint8_t reason)
{
	char addr[BT_ADDR_LE_STR_LEN];
	int err;

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	printk("Tag BLE disconnected: %s reason=0x%02x\n", addr, reason);
	ota_active = false;
	ota_ready = false;

	err = uwb_tag_ble_start_advertising();
	printk("Tag BLE adv resume rc=%d\n", err);
}

BT_CONN_CB_DEFINE(uwb_tag_ble_conn_cb) = {
	.connected = ble_connected,
	.disconnected = ble_disconnected,
};

static void uwb_tag_ble_send_text(const char *text)
{
	int err;

	if (!ble_ready || text == NULL || text[0] == '\0') {
		return;
	}

	err = bt_nus_send(NULL, text, strlen(text));
	if (err && err != -ENOTCONN) {
		printk("Tag BLE notify failed: %d\n", err);
	}
}

static void ble_notif_enabled(enum bt_nus_send_status status)
{
	printk("Tag BLE notifications %s\n",
	       (status == BT_NUS_SEND_STATUS_ENABLED) ? "enabled" : "disabled");

	if (status == BT_NUS_SEND_STATUS_ENABLED) {
		k_mutex_lock(&ble_mutex, K_FOREVER);
		if (last_status[0] != '\0') {
			char snapshot[UWB_TAG_BLE_MAX_STATUS_LEN];

			snprintk(snapshot, sizeof(snapshot), "%s", last_status);
			k_mutex_unlock(&ble_mutex);
			uwb_tag_ble_send_text(snapshot);
			return;
		}
		k_mutex_unlock(&ble_mutex);
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
		uwb_tag_ble_send_text("PING|STATUS|OTA_STATUS|OTA_PREPARE|OTA_BEGIN|OTA_CANCEL|HELP");
		return;
	}

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

	uwb_tag_ble_send_text("UNKNOWN_CMD");
}

static struct bt_nus_cb nus_cb = {
	.send_enabled = ble_notif_enabled,
	.received = ble_received,
};

bool uwb_tag_ble_ota_active(void)
{
	return ota_active;
}

int uwb_tag_ble_init(void)
{
	int err;

	k_mutex_init(&ble_mutex);

	printk("Tag BLE init start\n");
	err = bt_enable(NULL);
	printk("Tag BLE bt_enable rc=%d\n", err);
	if (err) {
		printk("Tag BLE init failed: %d\n", err);
		return err;
	}

	if (IS_ENABLED(CONFIG_SETTINGS)) {
		settings_load();
	}

	err = bt_set_name(CONFIG_BT_DEVICE_NAME);
	printk("Tag BLE set name rc=%d\n", err);
	if (err) {
		printk("Tag BLE set name failed, continuing: %d\n", err);
	}

	err = bt_nus_init(&nus_cb);
	printk("Tag BLE NUS init rc=%d\n", err);
	if (err) {
		printk("Tag BLE NUS register failed: %d\n", err);
		return err;
	}

	err = uwb_tag_ble_start_advertising();
	printk("Tag BLE adv start rc=%d\n", err);
	if (err) {
		printk("Tag BLE advertising failed: %d\n", err);
		return err;
	}

	ble_ready = true;
	printk("Tag BLE advertising as %s\n", CONFIG_BT_DEVICE_NAME);
	return 0;
}

int uwb_tag_ble_publish_status(const char *line)
{
	int err;

	if (line == NULL) {
		return -EINVAL;
	}

	k_mutex_lock(&ble_mutex, K_FOREVER);
	snprintk(last_status, sizeof(last_status), "%s", line);
	k_mutex_unlock(&ble_mutex);

	if (!ble_ready) {
		return -ENODEV;
	}

	err = bt_nus_send(NULL, last_status, strlen(last_status));
	if (err && err != -ENOTCONN) {
		printk("Tag BLE status send failed: %d\n", err);
	}

	return 0;
}
