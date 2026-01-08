/* src/main.c — Dual-NUS Central + Peripheral (USB-CDC data only)
 * nRF Connect SDK v2.8 / Zephyr 3.7.99
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>
#include <zephyr/settings/settings.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <bluetooth/scan.h>
#include <bluetooth/gatt_dm.h>
#include <bluetooth/services/nus.h>            /* peripheral/server */
#include <bluetooth/services/nus_client.h>     /* central/client */

#include <string.h>
#include <errno.h>

/* ===== 可发现的传感器（按广播 name 精确匹配） ===== */
#define MAX_PEERS 2
static const char *const peer_names[MAX_PEERS] = { "eF6A98", "eFF597" };

/* ===== LED DT alias ===== */
#define LED1_NODE DT_ALIAS(led0)   /* 扫描闪烁 */
#define LED2_NODE DT_ALIAS(led1)   /* Central conn[0] */
#define LED3_NODE DT_ALIAS(led2)   /* Central conn[1] / RX */

static const struct gpio_dt_spec led_scan = GPIO_DT_SPEC_GET(LED1_NODE, gpios);
static const struct gpio_dt_spec led_c0   = GPIO_DT_SPEC_GET(LED2_NODE, gpios);
static const struct gpio_dt_spec led_c1   = GPIO_DT_SPEC_GET(LED3_NODE, gpios);

/* ===== BLE 连接参数（1.25ms 单位）：7.5–15ms，无从属延迟 ===== */
static const struct bt_le_conn_param conn_param = {
    .interval_min = 6,
    .interval_max = 12,
    .latency      = 0,
    .timeout      = 400,
};

/* ===== 连接与客户端对象 ===== */
static struct bt_conn       *conns[MAX_PEERS];
static struct bt_nus_client  nus_clients[MAX_PEERS];
static struct bt_conn       *server_conn;   /* 手机端 (Peripheral) */
static const struct device  *uart_dev;      /* CDC_ACM_0 */

/* ===== 工具函数 ===== */
static inline bool addr_equal(const bt_addr_le_t *a, const bt_addr_le_t *b)
{
    return !bt_addr_le_cmp(a, b);
}

static bool already_connected(const bt_addr_le_t *addr)
{
    for (int i = 0; i < MAX_PEERS; i++) {
        if (conns[i] && addr_equal(bt_conn_get_dst(conns[i]), addr)) {
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

/* 等待主机端打开串口 (DTR=1)；若不支持 line_ctrl 或超时则放行 */
static void cdc_wait_dtr(const struct device *dev, int timeout_ms)
{
    if (!IS_ENABLED(CONFIG_UART_LINE_CTRL)) {
        return;
    }
    uint32_t dtr = 0;
    int waited = 0;
    while (uart_line_ctrl_get(dev, UART_LINE_CTRL_DTR, &dtr) == 0 && !dtr) {
        k_msleep(10);
        waited += 10;
        if (timeout_ms > 0 && waited >= timeout_ms) {
            break;
        }
    }
    /* 可选：向主机报告 ready 状态（忽略返回值） */
    (void)uart_line_ctrl_set(dev, UART_LINE_CTRL_DCD, 1);
    (void)uart_line_ctrl_set(dev, UART_LINE_CTRL_DSR, 1);
}

/* ===== NUS Server 回调 (Peripheral — 面向手机) ===== */
static void server_received(struct bt_conn *conn,
                            const uint8_t *data, uint16_t len)
{
    ARG_UNUSED(conn);
    printk("Phone→Board %u bytes\n", len);
}

static struct bt_nus_cb nus_srv_cb = {
    .received = server_received,
    .sent     = NULL,
};

/* ===== NUS Client RX 回调 (Central — 来自传感器) ===== */
static uint8_t nus_client_rx(struct bt_nus_client *client,
                             const uint8_t *data, uint16_t len)
{
    ARG_UNUSED(client);

    /* 指示有数据到来 */
    gpio_pin_toggle_dt(&led_c1);

    /* 转发给手机 (Peripheral)，失败不阻塞 */
    if (server_conn) {
        int err = bt_nus_send(server_conn, data, len);
        if (err) {
            printk("Forward failed: %d\n", err);
        }
    }

    /* 同时透传到 USB CDC（非 console） */
    if (uart_dev) {
        for (uint16_t i = 0; i < len; i++) {
            uart_poll_out(uart_dev, data[i]);
        }
    }

    return BT_GATT_ITER_CONTINUE;
}

static struct bt_nus_client_init_param nus_client_init = {
    .cb = { .received = nus_client_rx }
};

/* ===== GATT 发现回调 (Central) ===== */
static void disc_done(struct bt_gatt_dm *dm, void *ctx)
{
    struct bt_nus_client *cl = ctx;
    int err = bt_nus_handles_assign(dm, cl);
    if (!err) err = bt_nus_client_init(cl, &nus_client_init);
    if (!err) err = bt_nus_subscribe_receive(cl);
    printk("NUS client ready: %d\n", err);
    bt_gatt_dm_data_release(dm);
}
static void disc_not_found(struct bt_conn *conn, void *ctx)
{ ARG_UNUSED(conn); ARG_UNUSED(ctx); }
static void disc_error(struct bt_conn *conn, int err, void *ctx)
{ ARG_UNUSED(ctx); printk("Discovery error %d\n", err); }

static struct bt_gatt_dm_cb gatt_dm_cb = {
    .completed         = disc_done,
    .service_not_found = disc_not_found,
    .error_found       = disc_error,
};

/* ===== 扫描回调 (Central) ===== */
static bool eir_cb(struct bt_data *data, void *user_data)
{
    bt_addr_le_t *addr = user_data;
    if (already_connected(addr)) {
        return true;
    }
    for (int i = 0; i < MAX_PEERS; i++) {
        if ((data->type == BT_DATA_NAME_COMPLETE ||
             data->type == BT_DATA_NAME_SHORTENED) &&
            data->data_len == strlen(peer_names[i]) &&
            !memcmp(data->data, peer_names[i], data->data_len)) {

            printk("Found %s, connecting\n", peer_names[i]);
            bt_le_scan_stop();
            bt_conn_le_create(addr, BT_CONN_LE_CREATE_CONN, &conn_param, NULL);
            return false;
        }
    }
    return true;
}

static void scan_cb(const bt_addr_le_t *addr, int8_t rssi,
                    uint8_t adv_type, struct net_buf_simple *buf)
{
    ARG_UNUSED(rssi);
    ARG_UNUSED(adv_type);
    gpio_pin_toggle_dt(&led_scan);
    bt_data_parse(buf, eir_cb, (void *)addr);
}

/* ===== 连接/断开 回调 ===== */
static void connected_cb(struct bt_conn *conn, uint8_t err)
{
    if (err) {
        printk("Connect failed 0x%02x\n", err);
        goto restart_scan;
    }

    struct bt_conn_info info;
    bt_conn_get_info(conn, &info);

    if (info.role == BT_CONN_ROLE_CENTRAL) {
        /* Central: 传感器连入 */
        int idx = free_slot();
        if (idx < 0) {
            bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
            goto restart_scan;
        }
        conns[idx] = bt_conn_ref(conn);
        gpio_pin_set_dt(idx ? &led_c1 : &led_c0, 1);
        printk("Central Connected[%d]\n", idx);

        bt_gatt_dm_start(conn, BT_UUID_NUS_SERVICE, &gatt_dm_cb, &nus_clients[idx]);

    } else {
        /* Peripheral: 手机连入 */
        server_conn = bt_conn_ref(conn);
        printk("Phone connected (Peripheral)\n");
    }

restart_scan:
    bt_le_scan_start(BT_LE_SCAN_ACTIVE, scan_cb);
}

static void disconnected_cb(struct bt_conn *conn, uint8_t reason)
{
    struct bt_conn_info info;
    bt_conn_get_info(conn, &info);

    if (info.role == BT_CONN_ROLE_CENTRAL) {
        for (int i = 0; i < MAX_PEERS; i++) {
            if (conns[i] &&
                addr_equal(bt_conn_get_dst(conns[i]), bt_conn_get_dst(conn))) {

                bt_conn_unref(conns[i]);
                conns[i] = NULL;
                gpio_pin_set_dt(i ? &led_c1 : &led_c0, 0);
                printk("Central Disconnected[%d]\n", i);
            }
        }
    } else {
        if (server_conn) {
            bt_conn_unref(server_conn);
            server_conn = NULL;
            printk("Phone disconnected\n");
        }
    }
    bt_le_scan_start(BT_LE_SCAN_ACTIVE, scan_cb);
}

static struct bt_conn_cb conn_cb = {
    .connected    = connected_cb,
    .disconnected = disconnected_cb,
};

/* ===== main() ===== */
void main(void)
{
    printk("Dual-Role NUS Central+Peripheral start\n");

    /* LED 初始化 */
    gpio_pin_configure_dt(&led_scan, GPIO_OUTPUT_INACTIVE);
    gpio_pin_configure_dt(&led_c0,   GPIO_OUTPUT_INACTIVE);
    gpio_pin_configure_dt(&led_c1,   GPIO_OUTPUT_INACTIVE);

    /* 绑定 USB CDC（非 console）：通过节点标签拿到 cdc_acm0 */
    uart_dev = DEVICE_DT_GET(DT_NODELABEL(cdc_acm0));
    if (!device_is_ready(uart_dev)) {
        printk("CDC_ACM_0 not ready\n");
        return;
    }
    /* 等 DTR，避免监视器未开导致的首包丢失（最多 3 秒） */
    cdc_wait_dtr(uart_dev, 3000);

    /* 启动蓝牙 */
    if (bt_enable(NULL)) {
        printk("Bluetooth init failed\n");
        return;
    }
    settings_load();

    /* 注册连接回调 */
    bt_conn_cb_register(&conn_cb);

    /* Peripheral: NUS Server + 广播（新写法，替代 BT_LE_ADV_CONN_NAME） */
    bt_nus_init(&nus_srv_cb);
    {
        const struct bt_data ad[] = {
            BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
            BT_DATA_BYTES(BT_DATA_UUID128_ALL, BT_UUID_NUS_VAL),
        };

        const struct bt_le_adv_param *param = BT_LE_ADV_PARAM(
            BT_LE_ADV_OPT_CONNECTABLE | BT_LE_ADV_OPT_USE_IDENTITY,
            0x0020, /* 20 ms */
            0x0040, /* 40 ms */
            NULL     /* Use default own addr */
        );

        int err = bt_le_adv_start(param, ad, ARRAY_SIZE(ad), NULL, 0);
        printk("Advertising NUS service to phone… (%d)\n", err);
    }

    /* Central: 开始扫描 */
    bt_le_scan_start(BT_LE_SCAN_ACTIVE, scan_cb);
    printk("Scanning for %s & %s …\n", peer_names[0], peer_names[1]);
}
