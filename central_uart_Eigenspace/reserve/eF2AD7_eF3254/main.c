/*
 * src/main.c — Dual‑NUS Central
 * nRF Connect SDK v2.8 / Zephyr 3.7.99
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/printk.h>
#include <zephyr/settings/settings.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <bluetooth/scan.h>
#include <bluetooth/gatt_dm.h>
#include <zephyr/bluetooth/services/nus.h>        /* BT_UUID_NUS_SERVICE */
#include <bluetooth/services/nus_client.h>

#include <string.h>

/* ---------- target peripherals ---------- */
#define MAX_PEERS      2
static const char *const peer_names[MAX_PEERS] = { "eF3254", "eF2AD7" };

/* ---------- LEDs from DT aliases --------- */
#define LED1_NODE DT_ALIAS(led0)   /* scan status  */
#define LED2_NODE DT_ALIAS(led1)   /* conn[0]      */
#define LED3_NODE DT_ALIAS(led2)   /* conn[1]/RX   */

static const struct gpio_dt_spec led_scan = GPIO_DT_SPEC_GET(LED1_NODE, gpios);
static const struct gpio_dt_spec led_c0   = GPIO_DT_SPEC_GET(LED2_NODE, gpios);
static const struct gpio_dt_spec led_c1   = GPIO_DT_SPEC_GET(LED3_NODE, gpios);

/* ---------- BLE globals ------------------ */
static const struct bt_le_conn_param conn_param = {
	.interval_min = 6,
	.interval_max = 12,
	.latency      = 0,
	.timeout      = 400,
};

static struct bt_conn       *conns[MAX_PEERS];
static struct bt_nus_client  nus_clients[MAX_PEERS];
static const struct device  *uart_dev;

/* ---------- helpers ---------------------- */
static bool addr_equal(const bt_addr_le_t *a, const bt_addr_le_t *b)
{
	return !bt_addr_le_cmp(a, b);
}

static bool already_connected(const bt_addr_le_t *addr)
{
	for (int i = 0; i < MAX_PEERS; i++) {
		if (conns[i] && addr_equal(addr, bt_conn_get_dst(conns[i]))) {
			return true;
		}
	}
	return false;
}

static int free_slot(void)
{
	for (int i = 0; i < MAX_PEERS; i++) {
		if (!conns[i]) {
			return i;
		}
	}
	return -ENOSPC;
}

/* ---------- NUS RX callback -------------- */
static uint8_t nus_rx(struct bt_nus_client *client,
		      const uint8_t *data, uint16_t len)
{
	/* flip LED3 each packet */
	gpio_pin_toggle_dt(&led_c1);

	for (uint16_t i = 0; i < len; i++) {
		uart_poll_out(uart_dev, data[i]);
	}
	return BT_GATT_ITER_CONTINUE;
}

static const struct bt_nus_client_init_param nus_init = {
	.cb = { .received = nus_rx }
};

/* ---------- GATT discovery --------------- */
static void disc_done(struct bt_gatt_dm *dm, void *ctx)
{
	struct bt_nus_client *cl = ctx;
	int err = bt_nus_handles_assign(dm, cl);
	if (!err) err = bt_nus_client_init(cl, &nus_init);
	if (!err) err = bt_nus_subscribe_receive(cl);
	printk("NUS ready, err=%d\n", err);
	bt_gatt_dm_data_release(dm);
}
static void disc_not_found(struct bt_conn *c, void *ctx) { ARG_UNUSED(c); ARG_UNUSED(ctx); }
static void disc_error(struct bt_conn *c, int e, void *ctx) { ARG_UNUSED(c); ARG_UNUSED(ctx); printk("disc err %d\n", e); }

static const struct bt_gatt_dm_cb gatt_dm_cb = {
	.completed = disc_done,
	.service_not_found = disc_not_found,
	.error_found = disc_error,
};

/* ---------- scanning --------------------- */
static bool eir_cb(struct bt_data *d, void *user)
{
	bt_addr_le_t *addr = user;
	if (already_connected(addr)) return true;

	for (int n = 0; n < MAX_PEERS; n++) {
		if ((d->type == BT_DATA_NAME_COMPLETE || d->type == BT_DATA_NAME_SHORTENED) &&
		    d->data_len == strlen(peer_names[n]) &&
		    !memcmp(d->data, peer_names[n], d->data_len)) {

			printk("Match %s, connect\n", peer_names[n]);
			bt_le_scan_stop();
			bt_conn_le_create(addr, BT_CONN_LE_CREATE_CONN,
					  &conn_param, NULL);
			return false;
		}
	}
	return true;
}

static void scan_cb(const bt_addr_le_t *addr, int8_t rssi,
		    uint8_t type, struct net_buf_simple *ad)
{
	gpio_pin_toggle_dt(&led_scan);
	bt_data_parse(ad, eir_cb, (void *)addr);
}

/* ---------- conn callbacks --------------- */
static void connected_cb(struct bt_conn *conn, uint8_t err)
{
	if (err) {
		printk("connect fail 0x%02x\n", err);
		goto restart_scan;
	}
	int idx = free_slot();
	if (idx < 0) { /* overflow */
		bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
		goto restart_scan;
	}
	conns[idx] = bt_conn_ref(conn);

	char addr_s[BT_ADDR_LE_STR_LEN];
	bt_addr_le_to_str(bt_conn_get_dst(conn), addr_s, sizeof(addr_s));
	printk("Connected[%d] %s\n", idx, addr_s);

	gpio_pin_set_dt(idx ? &led_c1 : &led_c0, 1);

	bt_gatt_dm_start(conn, BT_UUID_NUS_SERVICE,
			 &gatt_dm_cb, &nus_clients[idx]);

restart_scan:
	/* keep scanning for remaining peers */
	bt_le_scan_start(BT_LE_SCAN_ACTIVE, scan_cb);
}

static void disconnected_cb(struct bt_conn *conn, uint8_t reason)
{
	for (int i = 0; i < MAX_PEERS; i++) {
		if (conns[i] && addr_equal(bt_conn_get_dst(conn),
					   bt_conn_get_dst(conns[i]))) {
			bt_conn_unref(conns[i]);
			conns[i] = NULL;
			gpio_pin_set_dt(i ? &led_c1 : &led_c0, 0);
			printk("Disconnected[%d] 0x%02x\n", i, reason);
		}
	}
	/* ensure scanning */
	bt_le_scan_start(BT_LE_SCAN_ACTIVE, scan_cb);
}

static struct bt_conn_cb conn_cb = {
	.connected = connected_cb,
	.disconnected = disconnected_cb
};

/* ---------- main ------------------------- */
void main(void)
{
	printk("Dual‑NUS central start\n");

	if (!device_is_ready(led_scan.port) ||
	    !device_is_ready(led_c0.port)   ||
	    !device_is_ready(led_c1.port)) {
		printk("LED GPIO not ready\n");
		return;
	}
	gpio_pin_configure_dt(&led_scan, GPIO_OUTPUT_INACTIVE);
	gpio_pin_configure_dt(&led_c0,   GPIO_OUTPUT_INACTIVE);
	gpio_pin_configure_dt(&led_c1,   GPIO_OUTPUT_INACTIVE);

	uart_dev = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
	if (!device_is_ready(uart_dev)) {
		printk("UART not ready\n");
		return;
	}

	if (bt_enable(NULL)) {
		printk("Bluetooth init failed\n");
		return;
	}
	settings_load();

	bt_conn_cb_register(&conn_cb);

	if (bt_le_scan_start(BT_LE_SCAN_ACTIVE, scan_cb)) {
		printk("scan start err\n");
		return;
	}
	printk("Scanning for %s & %s …\n", peer_names[0], peer_names[1]);
}
