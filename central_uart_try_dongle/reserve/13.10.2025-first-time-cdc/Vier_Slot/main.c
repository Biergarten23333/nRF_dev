/*
 * Central UART — NUS Client for nRF Connect SDK v2.8 / Zephyr 3.7.99
 * Auto-scan 10s → queue “eF” devices → auto-connect sequentially → delay → forward data
 * Button 1 (sw0/P0.11) to restart scan/reset
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

#define NAME_PREFIX       "eF"
#define NAME_PREFIX_LEN   (sizeof(NAME_PREFIX) - 1)
#define MAX_PEERS         4
#define SCAN_TIMEOUT_SEC 10
#define POST_DELAY_SEC 10

/* LEDs */
static const struct gpio_dt_spec led1 = GPIO_DT_SPEC_GET(DT_ALIAS(led0), gpios);
static const struct gpio_dt_spec led2 = GPIO_DT_SPEC_GET(DT_ALIAS(led1), gpios);
static const struct gpio_dt_spec led3 = GPIO_DT_SPEC_GET(DT_ALIAS(led2), gpios);

/* Button0 to restart */
static const struct gpio_dt_spec btn0 = GPIO_DT_SPEC_GET_OR(DT_ALIAS(sw0), gpios, {0});

/* BLE params */
static const struct bt_le_conn_param conn_params = {
    .interval_min = 6, .interval_max = 12, .latency = 0, .timeout = 400,
};

struct peer {
    struct bt_conn      *conn;
    struct bt_nus_client nus;
    bool                 in_use;
};
static struct peer peers[MAX_PEERS];

/* 带去重的队列 */
static bt_addr_le_t pending_addrs[MAX_PEERS];
static size_t      pending_count;

/* 专用的串行连接列表 */
static bt_addr_le_t connect_addrs[MAX_PEERS];
static size_t      total_to_connect;
static size_t      next_to_connect;

/* UART 转发开关与设备 */
static bool forward_enabled;
static const struct device *uart_dev;

/* Zephyr 对象 */
static struct k_timer scan_timer;
static struct k_work  scan_work;
static struct k_work  post_work;
static struct k_work  btn_work;
static struct gpio_callback btn_cb;

/* Forward decls */
static void scan_cb(const bt_addr_le_t*, int8_t, uint8_t, struct net_buf_simple*);
static void scan_timeout(struct k_timer*);
static void scan_work_fn(struct k_work*);
static void post_work_fn(struct k_work*);
static void btn_isr(const struct device*, struct gpio_callback*, gpio_port_pins_t);
static void btn_work_fn(struct k_work*);
static void connected_cb(struct bt_conn*, uint8_t);
static void disconnected_cb(struct bt_conn*, uint8_t);
static void discovery_complete(struct bt_gatt_dm*, void*);
static void connect_next(void);

/* Conn callback struct */
static struct bt_conn_cb conn_cbs = {
    .connected    = connected_cb,
    .disconnected = disconnected_cb,
};

/* NUS receive */
static uint8_t nus_recv(struct bt_nus_client *c, const uint8_t *d, uint16_t l) {
    if (!forward_enabled) return BT_GATT_ITER_CONTINUE;
    gpio_pin_toggle_dt(&led3);
    for (int i = 0; i < l; i++) uart_poll_out(uart_dev, d[i]);
    return BT_GATT_ITER_CONTINUE;
}
static struct bt_nus_client_init_param nus_init = {
    .cb = { .received = nus_recv }
};

/* GATT discovery */
static void discovery_complete(struct bt_gatt_dm *dm, void *ctx) {
    struct peer *p = ctx;
    if (!bt_nus_handles_assign(dm, &p->nus)) {
        bt_nus_client_init(&p->nus, &nus_init);
        bt_nus_subscribe_receive(&p->nus);
    }
    bt_gatt_dm_data_release(dm);
}
static struct bt_gatt_dm_cb gatt_cbs = {
    .completed = discovery_complete,
};

/* 去重判断 */
static bool already_q(const bt_addr_le_t *a) {
    for (size_t i = 0; i < pending_count; i++)
        if (!bt_addr_le_cmp(a, &pending_addrs[i])) return true;
    return false;
}

/* EIR parse → pending queue */
static bool eir_cb(struct bt_data *data, void *ud) {
    bt_addr_le_t *addr = ud;
    if ((data->type == BT_DATA_NAME_COMPLETE ||
         data->type == BT_DATA_NAME_SHORTENED) &&
        data->data_len >= NAME_PREFIX_LEN &&
        !memcmp(data->data, NAME_PREFIX, NAME_PREFIX_LEN)) {
        if (pending_count < MAX_PEERS && !already_q(addr)) {
            pending_addrs[pending_count++] = *addr;
            printk("Queued %u/%u: %02x:%02x:%02x:%02x:%02x:%02x\n",
                   pending_count, MAX_PEERS,
                   addr->a.val[5],addr->a.val[4],
                   addr->a.val[3],addr->a.val[2],
                   addr->a.val[1],addr->a.val[0]);
        }
        return false;
    }
    return true;
}

/* Scan callback old API */
static void scan_cb(const bt_addr_le_t *a,
                    int8_t r, uint8_t t,
                    struct net_buf_simple *ad) {
    gpio_pin_toggle_dt(&led1);
    bt_data_parse(ad, eir_cb, (void*)a);
}

/* Timer→提交scan_work */
static void scan_timeout(struct k_timer *t) {
    k_work_submit(&scan_work);
}

/* scan_work: stop→copy队列→start first connect */
static void scan_work_fn(struct k_work *w) {
    bt_le_scan_stop();
    /* copy */
    total_to_connect = pending_count;
    for (size_t i = 0; i < total_to_connect; i++) {
        connect_addrs[i] = pending_addrs[i];
    }
    printk("Scan done, %u to connect\n", total_to_connect);
    next_to_connect = 0;
    if (total_to_connect > 0) {
        /* 第1台 */
        int err = bt_conn_le_create(&connect_addrs[0],
                                    BT_CONN_LE_CREATE_CONN,
                                    &conn_params,
                                    &peers[0].conn);
        peers[0].in_use = !err;
        if (err) printk("conn0 err %d\n", err);
    }
    /* 准备post forward */
    k_work_init(&post_work, post_work_fn);
}

/* post_work: 延时后打开forward */
static void post_work_fn(struct k_work *w) {
    k_sleep(K_SECONDS(POST_DELAY_SEC));
    forward_enabled = true;
    printk("Forward ENABLED\n");
}

/* button ISR→submit btn_work */
static void btn_isr(const struct device *d, struct gpio_callback *cb, gpio_port_pins_t p) {
    if (p & BIT(btn0.pin)) k_work_submit(&btn_work);
}

/* btn_work: 重置扫描 */
static void btn_work_fn(struct k_work *w) {
    /* disconnect all */
    for (size_t i = 0; i < MAX_PEERS; i++) {
        if (peers[i].in_use) {
            bt_conn_disconnect(peers[i].conn,
                               BT_HCI_ERR_REMOTE_USER_TERM_CONN);
            peers[i].in_use = false;
        }
    }
    pending_count = 0;
    forward_enabled = false;
    /* restart */
    k_timer_start(&scan_timer, K_SECONDS(SCAN_TIMEOUT_SEC), K_NO_WAIT);
    bt_le_scan_start(BT_LE_SCAN_ACTIVE, scan_cb);
    printk("Scan RESTART %ds\n", SCAN_TIMEOUT_SEC);
}

/* conn callbacks串行发下一台 */
static void connected_cb(struct bt_conn *c, uint8_t err) {
    if (!err) {
        gpio_pin_set_dt(&led2, 1);
        /* 找槽位做发现 */
        for (size_t i = 0; i < total_to_connect; i++) {
            if (peers[i].conn == c) {
                bt_gatt_dm_start(c, BT_UUID_NUS_SERVICE,
                                 &gatt_cbs, &peers[i]);
                break;
            }
        }
        /* 发起下一台 */
        next_to_connect++;
        if (next_to_connect < total_to_connect) {
            int err2 = bt_conn_le_create(&connect_addrs[next_to_connect],
                                         BT_CONN_LE_CREATE_CONN,
                                         &conn_params,
                                         &peers[next_to_connect].conn);
            peers[next_to_connect].in_use = !err2;
            if (err2) printk("conn%u err%d\n", next_to_connect, err2);
        } else {
            /* 全部发起后启动forward延时工作 */
            k_work_submit(&post_work);
        }
    } else {
        printk("conn err 0x%02x\n", err);
    }
}
static void disconnected_cb(struct bt_conn *c, uint8_t r) {
    ARG_UNUSED(r);
    for (size_t i = 0; i < total_to_connect; i++) {
        if (peers[i].conn == c) {
            peers[i].in_use = false;
            bt_conn_unref(peers[i].conn);
            peers[i].conn = NULL;
            gpio_pin_set_dt(&led2, 0);
            break;
        }
    }
}

int main(void) {
    int err;
    /* LEDs */
    gpio_pin_configure_dt(&led1, GPIO_OUTPUT_INACTIVE);
    gpio_pin_configure_dt(&led2, GPIO_OUTPUT_INACTIVE);
    gpio_pin_configure_dt(&led3, GPIO_OUTPUT_INACTIVE);
    /* button */
    gpio_pin_configure_dt(&btn0, GPIO_INPUT | GPIO_PULL_UP);
    gpio_pin_interrupt_configure_dt(&btn0, GPIO_INT_EDGE_TO_ACTIVE);
    gpio_init_callback(&btn_cb, btn_isr, BIT(btn0.pin));
    gpio_add_callback(btn0.port, &btn_cb);
    /* works+timer */
    k_work_init(&scan_work, scan_work_fn);
    k_work_init(&btn_work, btn_work_fn);
    k_timer_init(&scan_timer, scan_timeout, NULL);

    printk("Scan %ds for \"%s\"\n", SCAN_TIMEOUT_SEC, NAME_PREFIX);

    uart_dev = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
    __ASSERT(device_is_ready(uart_dev), "UART not ready");

    err = bt_enable(NULL);
    __ASSERT(!err, "bt_enable failed");

    settings_load();
    bt_conn_cb_register(&conn_cbs);

    k_timer_start(&scan_timer, K_SECONDS(SCAN_TIMEOUT_SEC), K_NO_WAIT);
    err = bt_le_scan_start(BT_LE_SCAN_ACTIVE, scan_cb);
    __ASSERT(!err, "scan start failed");

    /* idle */
    while (1) { k_sleep(K_SECONDS(1)); }
}