/*
 * Central UART — NUS Client for nRF Connect SDK v2.8 / Zephyr 3.7.99
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/sys/printk.h>
#include <zephyr/settings/settings.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <bluetooth/scan.h>
#include <zephyr/bluetooth/gatt.h>
#include <bluetooth/gatt_dm.h>
#include <zephyr/bluetooth/services/nus.h>
#include <bluetooth/services/nus_client.h>

#define DEVICE_NAME        "eF3254"
#define DEVICE_NAME_LEN    (sizeof(DEVICE_NAME) - 1)

/* LED aliases from devicetree */
#define LED1_NODE DT_ALIAS(led0)
#if !DT_NODE_HAS_STATUS(LED1_NODE, okay)
#error "Unsupported board: led0 alias is not defined"
#endif
#define LED2_NODE DT_ALIAS(led1)
#if !DT_NODE_HAS_STATUS(LED2_NODE, okay)
#error "Unsupported board: led1 alias is not defined"
#endif
#define LED3_NODE DT_ALIAS(led2)
#if !DT_NODE_HAS_STATUS(LED3_NODE, okay)
#error "Unsupported board: led2 alias is not defined"
#endif

static const struct gpio_dt_spec led1 = GPIO_DT_SPEC_GET(LED1_NODE, gpios);
static const struct gpio_dt_spec led2 = GPIO_DT_SPEC_GET(LED2_NODE, gpios);
static const struct gpio_dt_spec led3 = GPIO_DT_SPEC_GET(LED3_NODE, gpios);

/* Connection parameters (match peripheral) */
static const struct bt_le_conn_param conn_params = {
    .interval_min = 6,
    .interval_max = 12,
    .latency      = 0,
    .timeout      = 400,
};

static const struct device *uart_dev;
static struct bt_conn *default_conn;
static struct bt_nus_client nus_client;

/* NUS data receive callback: toggle LED3 and output to console UART */
static uint8_t nus_data_received(struct bt_nus_client *client,
                                 const uint8_t *data, uint16_t length)
{
    ARG_UNUSED(client);
    gpio_pin_toggle_dt(&led3);
    for (uint16_t i = 0; i < length; i++) {
        uart_poll_out(uart_dev, data[i]);
    }
    return BT_GATT_ITER_CONTINUE;
}

/* NUS client init parameters */
static struct bt_nus_client_init_param nus_init = {
    .cb = {
        .received     = nus_data_received,
        .sent         = NULL,
        .unsubscribed = NULL,
    },
};

/* GATT discovery complete callback: assign handles, init client, enable notifications */
static void discovery_complete(struct bt_gatt_dm *dm, void *ctx)
{
    int err;

    err = bt_nus_handles_assign(dm, &nus_client);
    if (err) {
        printk("bt_nus_handles_assign failed: %d\n", err);
        goto cleanup;
    }

    err = bt_nus_client_init(&nus_client, &nus_init);
    if (err) {
        printk("bt_nus_client_init failed: %d\n", err);
        goto cleanup;
    }

    err = bt_nus_subscribe_receive(&nus_client);
    if (err) {
        printk("bt_nus_subscribe_receive failed: %d\n", err);
    } else {
        printk("NUS notifications enabled\n");
    }

cleanup:
    bt_gatt_dm_data_release(dm);
}

static void discovery_service_not_found(struct bt_conn *conn, void *ctx)
{
    ARG_UNUSED(ctx);
    printk("NUS Service not found\n");
}

static void discovery_error_found(struct bt_conn *conn, int err, void *ctx)
{
    ARG_UNUSED(ctx);
    printk("Discovery failed (err %d)\n", err);
}

static struct bt_gatt_dm_cb gatt_dm_cb = {
    .completed         = discovery_complete,
    .service_not_found = discovery_service_not_found,
    .error_found       = discovery_error_found,
};

/* EIR parsing: on matching name, stop scan and connect; toggle LED1 each adv */
static bool eir_found_cb(struct bt_data *data, void *user_data)
{
    bt_addr_le_t *addr = user_data;

    if ((data->type == BT_DATA_NAME_COMPLETE ||
         data->type == BT_DATA_NAME_SHORTENED) &&
        data->data_len == DEVICE_NAME_LEN &&
        !memcmp(data->data, DEVICE_NAME, DEVICE_NAME_LEN)) {

        bt_le_scan_stop();
        bt_conn_le_create(addr, BT_CONN_LE_CREATE_CONN,
                          &conn_params, &default_conn);
        return false;
    }
    return true;
}

/* Scan callback (old API): toggle LED1 on each adv and parse EIR */
static void scan_cb(const bt_addr_le_t *addr, int8_t rssi,
                    uint8_t adv_type, struct net_buf_simple *ad)
{
    gpio_pin_toggle_dt(&led1);
    bt_data_parse(ad, eir_found_cb, (void *)addr);
}

/* Connected callback: stop LED1 blink, turn LED2 on, start GATT discovery */
static void connected_cb(struct bt_conn *conn, uint8_t err)
{
    if (err) {
        printk("Connection failed (0x%02x)\n", err);
        bt_le_scan_start(BT_LE_SCAN_ACTIVE, scan_cb);
        return;
    }

    default_conn = bt_conn_ref(conn);
    printk("Connected to %s\n", DEVICE_NAME);

    gpio_pin_set_dt(&led1, 0);
    gpio_pin_set_dt(&led2, 1);

    err = bt_gatt_dm_start(default_conn,
                           BT_UUID_NUS_SERVICE,
                           &gatt_dm_cb,
                           NULL);
    if (err) {
        printk("bt_gatt_dm_start failed: %d\n", err);
    }
}

static void disconnected_cb(struct bt_conn *conn, uint8_t reason)
{
    ARG_UNUSED(reason);
    if (default_conn) {
        bt_conn_unref(default_conn);
        default_conn = NULL;
    }
    printk("Disconnected, restarting scan\n");

    gpio_pin_set_dt(&led2, 0);
    /* resume scanning */
    bt_le_scan_start(BT_LE_SCAN_ACTIVE, scan_cb);
}

static struct bt_conn_cb conn_callbacks = {
    .connected    = connected_cb,
    .disconnected = disconnected_cb,
};

void main(void)
{
    int err;

    /* Initialize LEDs */
    if (!device_is_ready(led1.port) ||
        !device_is_ready(led2.port) ||
        !device_is_ready(led3.port)) {
        printk("LED device not ready\n");
        return;
    }
    gpio_pin_configure_dt(&led1, GPIO_OUTPUT_INACTIVE);
    gpio_pin_configure_dt(&led2, GPIO_OUTPUT_INACTIVE);
    gpio_pin_configure_dt(&led3, GPIO_OUTPUT_INACTIVE);

    printk("Starting NUS Central\n");

    uart_dev = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
    if (!device_is_ready(uart_dev)) {
        printk("UART console not ready\n");
        return;
    }

    err = bt_enable(NULL);
    if (err) {
        printk("bt_enable failed (err %d)\n", err);
        return;
    }

    /* Load identity & bonds to avoid "No ID address" errors */
    settings_load();

    bt_conn_cb_register(&conn_callbacks);

    /* Start scanning (LED1 will blink in scan_cb) */
    err = bt_le_scan_start(BT_LE_SCAN_ACTIVE, scan_cb);
    if (err) {
        printk("Scan start failed (err %d)\n", err);
        return;
    }
    printk("Scanning for \"%s\"...\n", DEVICE_NAME);
}
