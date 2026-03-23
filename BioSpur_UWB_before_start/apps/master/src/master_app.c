#include <errno.h>
#include <stdint.h>
#include <string.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>

#include <dk_buttons_and_leds.h>

#include <bluetooth/gatt_dm.h>
#include <bluetooth/scan.h>
#include <bluetooth/services/nus.h>
#include <bluetooth/services/nus_client.h>

#define MASTER_CMD_PERIOD K_SECONDS(2)

#ifndef APP_MASTER_ONE_SHOT_CMD
#define APP_MASTER_ONE_SHOT_CMD ""
#endif

static const struct bt_conn_le_phy_param *const fast_phy_params = BT_CONN_LE_PHY_PARAM_2M;
static const struct bt_le_conn_param *const fast_conn_params = BT_LE_CONN_PARAM(6, 6, 0, 400);

enum master_led_id {
	MASTER_LED_SCAN = DK_LED1,
	MASTER_LED_LINK = DK_LED2,
	MASTER_LED_OTA = DK_LED3,
	MASTER_LED_ERROR = DK_LED4,
};

static struct bt_conn *default_conn;
static struct bt_nus_client nus_client;
static struct k_work_delayable send_work;
static struct k_sem nus_write_sem;
static uint8_t command_phase;
static bool one_shot_sent;
static bool nus_ready;
static bool leds_ready;
static bool led_scan_state;
static bool led_link_state;
static bool led_ota_state;
static bool led_error_state;

static void send_work_handler(struct k_work *work);

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

struct adv_name_ctx {
	char *name;
	size_t name_len;
};

static bool adv_name_parse_cb(struct bt_data *data, void *user_data)
{
	struct adv_name_ctx *ctx = user_data;

	if (ctx == NULL || ctx->name == NULL || ctx->name_len == 0U) {
		return false;
	}

	if (data->type != BT_DATA_NAME_COMPLETE &&
	    data->type != BT_DATA_NAME_SHORTENED) {
		return true;
	}

	size_t copy_len = MIN((size_t)data->data_len, ctx->name_len - 1U);
	memcpy(ctx->name, data->data, copy_len);
	ctx->name[copy_len] = '\0';
	return false;
}

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

static void master_leds_set(bool scan, bool link, bool ota, bool error)
{
	led_scan_state = scan;
	led_link_state = link;
	led_ota_state = ota;
	led_error_state = error;
	master_leds_apply();
}

static void ble_data_sent(struct bt_nus_client *nus, uint8_t err,
			  const uint8_t *const data, uint16_t len)
{
	ARG_UNUSED(nus);
	ARG_UNUSED(data);
	ARG_UNUSED(len);

	k_sem_give(&nus_write_sem);

	if (err) {
		printk("BLE write error: 0x%02x\n", err);
		master_leds_set(false, led_link_state, led_ota_state, true);
	}
}

static uint8_t ble_data_received(struct bt_nus_client *nus,
				 const uint8_t *data, uint16_t len)
{
	ARG_UNUSED(nus);

	printk("BLE notify: ");
	for (uint16_t i = 0; i < len; ++i) {
		char c = (char)data[i];
		printk("%c", (c >= 32 && c <= 126) ? c : '.');
	}
	printk("\n");

	led_error_state = false;
	if (ble_payload_contains(data, len, "OTA_STATE=READY")) {
		led_ota_state = true;
	}
	if (ble_payload_contains(data, len, "OTA_STATE=ACTIVE")) {
		led_ota_state = true;
	}
	if (ble_payload_contains(data, len, "OTA_STATE=NORMAL")) {
		led_ota_state = false;
	}
	if (ble_payload_contains(data, len, "OTA_READY")) {
		led_ota_state = true;
	}
	if (ble_payload_contains(data, len, "OTA_BEGIN_OK")) {
		led_ota_state = true;
	}
	if (ble_payload_contains(data, len, "OTA_CANCELLED")) {
		led_ota_state = false;
	}
	if (ble_payload_contains(data, len, "UNKNOWN_CMD") ||
	    ble_payload_contains(data, len, "FAILED") ||
	    ble_payload_contains(data, len, "ERROR")) {
		led_error_state = true;
	}
	master_leds_apply();

	return BT_GATT_ITER_CONTINUE;
}

static int nus_client_init(void)
{
	struct bt_nus_client_init_param init = {
		.cb = {
			.received = ble_data_received,
			.sent = ble_data_sent,
		}
	};

	return bt_nus_client_init(&nus_client, &init);
}

static void discovery_complete(struct bt_gatt_dm *dm, void *context)
{
	ARG_UNUSED(context);

	int err = bt_nus_handles_assign(dm, &nus_client);
	if (err) {
		printk("NUS handle assign failed: %d\n", err);
		bt_gatt_dm_data_release(dm);
		return;
	}

	err = bt_nus_subscribe_receive(&nus_client);
	if (err) {
		printk("NUS subscribe failed: %d\n", err);
		bt_gatt_dm_data_release(dm);
		return;
	}

	nus_ready = true;
	printk("BLE link ready, starting command loop\n");
	master_leds_set(false, true, led_ota_state, false);
	k_work_reschedule(&send_work, K_NO_WAIT);

	bt_gatt_dm_data_release(dm);
}

static void discovery_service_not_found(struct bt_conn *conn, void *context)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(context);
	printk("NUS service not found\n");
}

static void discovery_error(struct bt_conn *conn, int err, void *context)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(context);
	printk("GATT discovery error: %d\n", err);
}

static struct bt_gatt_dm_cb discovery_cb = {
	.completed = discovery_complete,
	.service_not_found = discovery_service_not_found,
	.error_found = discovery_error,
};

static void gatt_discover(struct bt_conn *conn)
{
	int err = bt_gatt_dm_start(conn, BT_UUID_NUS_SERVICE, &discovery_cb, NULL);
	if (err) {
		printk("Could not start GATT discovery: %d\n", err);
	}
}

static void exchange_func(struct bt_conn *conn, uint8_t err,
			  struct bt_gatt_exchange_params *params)
{
	ARG_UNUSED(conn);
	ARG_UNUSED(params);

	if (err) {
		printk("MTU exchange failed: %u\n", err);
	} else {
		printk("MTU exchange done\n");
	}
}

static void connected(struct bt_conn *conn, uint8_t conn_err)
{
	char addr[BT_ADDR_LE_STR_LEN];
	int err;

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));

	if (conn_err) {
		printk("Failed to connect to %s, err 0x%02x\n", addr, conn_err);
		if (default_conn == conn) {
			bt_conn_unref(default_conn);
			default_conn = NULL;
		}
		return;
	}

	printk("Connected: %s\n", addr);
	static struct bt_gatt_exchange_params exchange_params;
	exchange_params.func = exchange_func;
	(void)bt_gatt_exchange_mtu(conn, &exchange_params);
	err = bt_conn_le_phy_update(conn, fast_phy_params);
	printk("PHY update request rc=%d\n", err);
	err = bt_conn_le_param_update(conn, fast_conn_params);
	printk("Conn param update request rc=%d\n", err);
	gatt_discover(conn);
	(void)bt_scan_stop();
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	char addr[BT_ADDR_LE_STR_LEN];

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	printk("Disconnected: %s reason 0x%02x\n", addr, reason);

	if (default_conn == conn) {
		bt_conn_unref(default_conn);
		default_conn = NULL;
	}

	nus_ready = false;
	k_work_cancel_delayable(&send_work);
	master_leds_set(true, false, false, false);
	(void)bt_scan_start(BT_SCAN_TYPE_SCAN_ACTIVE);
}

BT_CONN_CB_DEFINE(conn_callbacks) = {
	.connected = connected,
	.disconnected = disconnected,
};

static void scan_filter_match(struct bt_scan_device_info *device_info,
			      struct bt_scan_filter_match *filter_match,
			      bool connectable)
{
	char addr[BT_ADDR_LE_STR_LEN];
	char adv_name[BT_GAP_MAX_NAME_LEN + 1U];
	struct adv_name_ctx adv_ctx = {
		.name = adv_name,
		.name_len = sizeof(adv_name),
	};

	ARG_UNUSED(filter_match);
	bt_addr_le_to_str(device_info->recv_info->addr, addr, sizeof(addr));
	adv_name[0] = '\0';
	if (device_info->adv_data != NULL) {
		(void)bt_data_parse(device_info->adv_data, adv_name_parse_cb,
				    &adv_ctx);
	}

	if (adv_name[0] != '\0') {
		printk("Scan match: %s connectable=%d name=%s\n", addr, connectable,
		       adv_name);
	} else {
		printk("Scan match: %s connectable=%d\n", addr, connectable);
	}
}

static void scan_connecting_error(struct bt_scan_device_info *device_info)
{
	ARG_UNUSED(device_info);
	printk("Connecting failed\n");
}

static void scan_connecting(struct bt_scan_device_info *device_info,
			    struct bt_conn *conn)
{
	ARG_UNUSED(device_info);
	default_conn = bt_conn_ref(conn);
}

BT_SCAN_CB_INIT(scan_cb, scan_filter_match, NULL, scan_connecting_error,
		scan_connecting);

static int scan_init(void)
{
	int err;
	struct bt_scan_init_param scan_init = {
		.connect_if_match = 1,
	};

	bt_scan_init(&scan_init);
	bt_scan_cb_register(&scan_cb);

	err = bt_scan_filter_add(BT_SCAN_FILTER_TYPE_UUID, BT_UUID_NUS_SERVICE);
	if (err) {
		printk("Failed to add scan filter: %d\n", err);
		return err;
	}

	err = bt_scan_filter_enable(BT_SCAN_UUID_FILTER, false);
	if (err) {
		printk("Failed to enable scan filter: %d\n", err);
		return err;
	}

	master_leds_set(true, false, false, false);

	return 0;
}

static void send_work_handler(struct k_work *work)
{
	ARG_UNUSED(work);

	if (!nus_ready || default_conn == NULL) {
		k_work_reschedule(&send_work, K_SECONDS(1));
		return;
	}

	if (strlen(APP_MASTER_ONE_SHOT_CMD) != 0U) {
		if (one_shot_sent) {
			return;
		}

		int err = bt_nus_client_send(&nus_client,
					       (const uint8_t *)APP_MASTER_ONE_SHOT_CMD,
					       strlen(APP_MASTER_ONE_SHOT_CMD));
		if (err) {
			printk("BLE one-shot send failed: %d\n", err);
			master_leds_set(led_scan_state, led_link_state, led_ota_state, true);
		} else {
			one_shot_sent = true;
			(void)k_sem_take(&nus_write_sem, K_MSEC(500));
			printk("BLE one-shot command sent: %s\n", APP_MASTER_ONE_SHOT_CMD);
		}
		return;
	}

	static const char *const cmds[] = {
		"PING\n",
		"STATUS\n",
		"OTA_STATUS\n",
		"OTA_PREPARE\n",
	};
	const char *cmd = cmds[command_phase % ARRAY_SIZE(cmds)];
	int err;

	command_phase++;

	err = bt_nus_client_send(&nus_client, (const uint8_t *)cmd, strlen(cmd));
	if (err) {
		printk("BLE send failed: %d\n", err);
		master_leds_set(led_scan_state, led_link_state, led_ota_state, true);
	} else {
		(void)k_sem_take(&nus_write_sem, K_MSEC(500));
	}

	k_work_reschedule(&send_work, MASTER_CMD_PERIOD);
}

int master_app_run(void)
{
	int err;

	k_sem_init(&nus_write_sem, 0, 1);
	k_work_init_delayable(&send_work, send_work_handler);

	err = dk_leds_init();
	if (err) {
		printk("LED init failed: %d\n", err);
	} else {
		leds_ready = true;
		master_leds_set(true, false, false, false);
		printk("LED map: 0=scan 1=link 2=ota 3=error\n");
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

	err = scan_init();
	if (err) {
		printk("Scan init failed: %d\n", err);
		return err;
	}

	printk("BioSpur BLE master ready on nRF52840 DK\n");
	if (strlen(APP_MASTER_ONE_SHOT_CMD) != 0U) {
		printk("One-shot NUS command armed: %s\n", APP_MASTER_ONE_SHOT_CMD);
	}
	printk("Scanning for NUS service\n");

	err = bt_scan_start(BT_SCAN_TYPE_SCAN_ACTIVE);
	if (err) {
		printk("Failed to start scan: %d\n", err);
		return err;
	}

	while (1) {
		k_sleep(K_SECONDS(5));
	}
}
