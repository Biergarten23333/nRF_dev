/*
 * Central-only NUS Aggregator (命令控制版)
 * nRF Connect SDK 2.8.0 / Zephyr 3.7.x
 *
 * 特性:
 *  - USB CDC 输出日志 + 接收命令
 *  - 通过 "scan" / "conn" 控制：
 *      - "scan": 开始扫描，缓存所有 eFxx 设备（只扫不连）
 *      - "conn": 停止扫描，对所有已缓存的 eFxx 设备发起连接
 *  - 最多支持 MAX_PEERS 个并行连接
 *
 * 备注:
 *  - 目前只做“连上”，NUS 数据接收位置已预留 TODO（后面我们再加）
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/usb/usb_device.h>
#include <zephyr/sys/util.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/settings/settings.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/addr.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <bluetooth/scan.h>
#include <bluetooth/gatt_dm.h>
#include <bluetooth/services/nus.h> /* 之后加 NUS client 会用到 */

#include <dk_buttons_and_leds.h>
#include <string.h>
#include <stdarg.h>
#include <stdio.h>

/* ===== 配置参数 ===== */
#define MAX_PEERS   11
#define CAND_MAX    24

/* ===== LED 定义 ===== */
#define LED_SCAN_NODE DT_ALIAS(led0)
#define LED_CONN_NODE DT_ALIAS(led1)
#define LED_RX_NODE   DT_ALIAS(led2)

static const struct gpio_dt_spec led_scan = GPIO_DT_SPEC_GET(LED_SCAN_NODE, gpios);
static const struct gpio_dt_spec led_conn = GPIO_DT_SPEC_GET(LED_CONN_NODE, gpios);
static const struct gpio_dt_spec led_rx   = GPIO_DT_SPEC_GET(LED_RX_NODE, gpios);

/* ===== 全局变量 ===== */
static const struct device *cdc = DEVICE_DT_GET_ONE(zephyr_cdc_acm_uart);

/* 连接数组 */
static struct bt_conn *conns[MAX_PEERS];

/* 扫描 / 候选缓存 */
struct cand_t {
    bt_addr_le_t addr;
    char   name[32];
    int8_t rssi;
    bool   used;
};
static struct cand_t cands[CAND_MAX];
static bool scanning = false;
static bool connecting = false;   /* 是否正在发起连接 */
/* CDC 命令线程同步 */
K_SEM_DEFINE(cdc_ready_sem, 0, 1);

/* ===== 小工具函数 ===== */
static inline void led_set(const struct gpio_dt_spec *led, int v)
{
    if (device_is_ready(led->port)) {
        gpio_pin_set_dt(led, v ? 1 : 0);
    }
}

static void cdc_printf(const char *fmt, ...)
{
    char buf[256];

    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintk(buf, sizeof(buf), fmt, ap);
    va_end(ap);

    if (n > 0) {
        for (int i = 0; i < n; i++) {
            uart_poll_out(cdc, buf[i]);
        }
    }
}

/* ===== 广播解析：取 device name ===== */
static bool parse_name_cb(struct bt_data *data, void *user_data)
{
    if (data->type == BT_DATA_NAME_COMPLETE ||
        data->type == BT_DATA_NAME_SHORTENED) {

        char *name = (char *)user_data;
        size_t len = MIN(data->data_len, 31);

        memcpy(name, data->data, len);
        name[len] = '\0';
    }
    return true;
}

/* ===== 候选表操作 ===== */
static void clear_candidates(void)
{
    for (int i = 0; i < CAND_MAX; i++) {
        cands[i].used = false;
    }
}

static int find_candidate_by_addr(const bt_addr_le_t *addr)
{
    for (int i = 0; i < CAND_MAX; i++) {
        if (!cands[i].used) {
            continue;
        }
        if (bt_addr_le_cmp(addr, &cands[i].addr) == 0) {
            return i;
        }
    }
    return -1;
}

static int add_or_update_candidate(const bt_addr_le_t *addr,
                                   const char *name,
                                   int8_t rssi)
{
    int idx = find_candidate_by_addr(addr);
    if (idx < 0) {
        for (int i = 0; i < CAND_MAX; i++) {
            if (!cands[i].used) {
                cands[i].used = true;
                cands[i].addr = *addr;
                strncpy(cands[i].name, name, sizeof(cands[i].name) - 1);
                cands[i].name[sizeof(cands[i].name) - 1] = '\0';
                cands[i].rssi = rssi;
                return i;
            }
        }
        return -1;
    } else {
        /* 已存在，更新 rssi / name */
        cands[idx].rssi = rssi;
        strncpy(cands[idx].name, name, sizeof(cands[idx].name) - 1);
        cands[idx].name[sizeof(cands[idx].name) - 1] = '\0';
        return idx;
    }
}

/* ===== 连接数组操作 ===== */
static bool addr_already_connected(const bt_addr_le_t *addr)
{
    for (int i = 0; i < MAX_PEERS; i++) {
        if (!conns[i]) {
            continue;
        }

        struct bt_conn_info info;
        if (bt_conn_get_info(conns[i], &info) == 0) {
            if (bt_addr_le_cmp(addr, info.le.dst) == 0) {
                return true;
            }
        }
    }
    return false;
}

static int alloc_conn_slot(struct bt_conn *conn)
{
    for (int i = 0; i < MAX_PEERS; i++) {
        if (!conns[i]) {
            conns[i] = conn;   /* 不额外 ref，按官方 central_uart 的习惯 */
            return i;
        }
    }
    return -1;
}

static bool any_conn_active(void)
{
    for (int i = 0; i < MAX_PEERS; i++) {
        if (conns[i]) {
            return true;
        }
    }
    return false;
}

/* ===== 扫描启动 / 停止 ===== */
static void start_scan(void)
{
    if (scanning) {
        cdc_printf("SCAN: already running\n");
        return;
    }

    clear_candidates();
    int err = bt_scan_start(BT_SCAN_TYPE_SCAN_ACTIVE);
    cdc_printf("bt_scan_start -> %d\n", err);
    if (!err) {
        scanning = true;
        led_set(&led_scan, 1);
    }
}

static void stop_scan(void)
{
    if (!scanning) {
        cdc_printf("SCAN: not running\n");
        return;
    }

    int err = bt_scan_stop();
    cdc_printf("bt_scan_stop -> %d\n", err);
    if (!err) {
        scanning = false;
        led_set(&led_scan, 0);
    }
}

/* ===== 在扫描期间处理每个广播：只缓存，不连接 ===== */
static void handle_scan_device(struct bt_scan_device_info *device_info,
                               bool connectable)
{
    ARG_UNUSED(connectable);

    char name[32] = {0};

    if (device_info->adv_data) {
        bt_data_parse(device_info->adv_data, parse_name_cb, name);
    }

    /* 只关心名字以 "eF" 开头的设备 */
    if (!(name[0] == 'e' && name[1] == 'F')) {
        return;
    }

    const bt_addr_le_t *addr = device_info->recv_info->addr;
    const uint8_t *v = addr->a.val;

    int idx = add_or_update_candidate(addr, name, device_info->recv_info->rssi);

    cdc_printf("SCAN[%d]:%s,%02X:%02X:%02X:%02X:%02X:%02X,%d\n",
               idx,
               name,
               v[5], v[4], v[3], v[2], v[1], v[0],
               device_info->recv_info->rssi);
}

/* ===== 扫描回调组：match / no_match 都走同一个 handler ===== */
static void scan_filter_match(struct bt_scan_device_info *device_info,
                              struct bt_scan_filter_match *filter_match,
                              bool connectable)
{
    ARG_UNUSED(filter_match);
    handle_scan_device(device_info, connectable);
}

static void scan_filter_no_match(struct bt_scan_device_info *device_info,
                                 bool connectable)
{
    handle_scan_device(device_info, connectable);
}

static void scan_connecting_error(struct bt_scan_device_info *device_info)
{
    ARG_UNUSED(device_info);
    cdc_printf("scan_connecting_error\n");
}

static void scan_connecting(struct bt_scan_device_info *device_info,
                            struct bt_conn *conn)
{
    ARG_UNUSED(device_info);
    ARG_UNUSED(conn);
}

/* 注意参数顺序：match, no_match, error, connecting */
BT_SCAN_CB_INIT(scan_cb,
                scan_filter_match,
                scan_filter_no_match,
                scan_connecting_error,
                scan_connecting);

/* ===== 基于候选表批量发起连接（CMD: conn） ===== */
static void connect_all_candidates(void)
{
    /* 如果已经有一个连接在发起，就先别再搞新的 */
    if (connecting) {
        cdc_printf("CMD conn: connect already in progress\n");
        return;
    }

    /* 连接前先停扫描，避免 EAGAIN 一类错误 */
    stop_scan();

    cdc_printf("CMD conn: trying to connect next candidate...\n");

    for (int i = 0; i < CAND_MAX; i++) {
        if (!cands[i].used) {
            continue;
        }

        const bt_addr_le_t *addr = &cands[i].addr;

        if (addr_already_connected(addr)) {
            cdc_printf("CAND[%d]: already connected, skip\n", i);
            continue;
        }

        struct bt_conn *conn = NULL;
        int err = bt_conn_le_create(addr,
                                    BT_CONN_LE_CREATE_CONN,
                                    BT_LE_CONN_PARAM_DEFAULT,
                                    &conn);
        cdc_printf("CAND[%d]: bt_conn_le_create -> %d\n", i, err);
        if (err) {
            /* 本候选建连失败，继续试下一个 */
            continue;
        }

        int slot = alloc_conn_slot(conn);
        if (slot < 0) {
            cdc_printf("No free conn slot, dropping conn for CAND[%d]\n", i);
            bt_conn_unref(conn);
            /* 没槽位就没必要继续连别的了，直接退出 */
            return;
        }

        cdc_printf("CAND[%d]: conn slot=%d created\n", i, slot);
        connecting = true;   /* 标记：有一个连接正在发起 */
        return;              /* 一次只发起一个连接，等回调再继续 */
    }

    cdc_printf("CMD conn: no more candidates to connect\n");
}

/* ===== 蓝牙连接回调 ===== */
static void connected_cb(struct bt_conn *conn, uint8_t err)
{
    struct bt_conn_info info;
    bt_conn_get_info(conn, &info);

    if (err) {
        cdc_printf("Connect failed (0x%02x)\n", err);
        connecting = false;          /* 这次连接失败，允许下一次尝试 */
        connect_all_candidates();    /* 继续尝试下一个候选 */
        return;
    }

    const uint8_t *v = info.le.dst->a.val;
    cdc_printf("Connected to %02X:%02X:%02X:%02X:%02X:%02X\n",
               v[5], v[4], v[3], v[2], v[1], v[0]);

    led_set(&led_conn, 1);

    connecting = false;              /* 这条连上了，可以继续连下一个 */
    connect_all_candidates();        /* 自动继续连下一个 CAND */

    /* TODO: 这里之后加 NUS service 发现 + 收数 */
}


static void disconnected_cb(struct bt_conn *conn, uint8_t reason)
{
    cdc_printf("Disconnected (0x%02x)\n", reason);

    for (int i = 0; i < MAX_PEERS; i++) {
        if (conns[i] == conn) {
            conns[i] = NULL;
            bt_conn_unref(conn);
            break;
        }
    }

    if (!any_conn_active()) {
        led_set(&led_conn, 0);
    }
}

static struct bt_conn_cb conn_cb = {
    .connected = connected_cb,
    .disconnected = disconnected_cb,
};

/* ===== CDC 命令解析线程 ===== */
static void handle_cmd(const char *cmd)
{
    if (strcmp(cmd, "scan") == 0) {
        cdc_printf("CMD: scan\n");
        start_scan();
    } else if (strcmp(cmd, "conn") == 0) {
        cdc_printf("CMD: conn\n");
        connect_all_candidates();
    } else {
        cdc_printf("CMD: unknown '%s'\n", cmd);
    }
}

static void cdc_rx_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a);
    ARG_UNUSED(b);
    ARG_UNUSED(c);

    /* 等 main 里把 USB / CDC 初始化好 */
    k_sem_take(&cdc_ready_sem, K_FOREVER);

    char buf[32];
    int  pos = 0;

    while (1) {
        uint8_t ch;
        int ret = uart_poll_in(cdc, &ch);
        if (ret == 0) {
            /* 可选：把收到的字符原样回显，方便你在终端看到自己打了啥 */
            // uart_poll_out(cdc, ch);

            /* 遇到换行就当一条命令结束（兼容有 \r/\n 的情况） */
            if (ch == '\r' || ch == '\n') {
                if (pos > 0) {
                    buf[pos] = '\0';
                    handle_cmd(buf);
                    pos = 0;
                }
                continue;
            }

            /* 普通字符累积到缓冲区 */
            if (pos < (int)sizeof(buf) - 1) {
                buf[pos++] = (char)ch;
                buf[pos]   = '\0';
            }

            /* 不依赖换行：一旦看到 "scan" 或 "conn" 就立刻触发 */
            if (pos >= 4) {
                if (strncmp(buf, "scan", 4) == 0) {
                    handle_cmd("scan");
                    pos = 0;
                    continue;
                }
                if (strncmp(buf, "conn", 4) == 0) {
                    handle_cmd("conn");
                    pos = 0;
                    continue;
                }
            }
        } else {
            k_msleep(10);
        }
    }
}


K_THREAD_DEFINE(cdc_rx_tid, 1024, cdc_rx_thread, NULL, NULL, NULL,
                5, 0, 0);

/* ===== 主函数 ===== */
void main(void)
{
    /* LED / 按键初始化 */
    gpio_pin_configure_dt(&led_scan, GPIO_OUTPUT_INACTIVE);
    gpio_pin_configure_dt(&led_conn, GPIO_OUTPUT_INACTIVE);
    gpio_pin_configure_dt(&led_rx,   GPIO_OUTPUT_INACTIVE);
    dk_buttons_init(NULL);

    /* USB CDC 初始化 */
    usb_enable(NULL);
    if (!device_is_ready(cdc)) {
        return;
    }
    cdc_printf("CDC ready\n");

    /* 告诉 cdc_rx_thread：CDC 可以用了 */
    k_sem_give(&cdc_ready_sem);

    /* 蓝牙初始化 */
    int berr = bt_enable(NULL);
    cdc_printf("bt_enable -> %d\n", berr);
    if (berr) {
        cdc_printf("Bluetooth init failed\n");
        return;
    }

    bt_conn_cb_register(&conn_cb);
    settings_load();
    cdc_printf("BT initialized.\n");

    /* bt_scan 初始化（不自动连接，全靠命令控制） */
    struct bt_scan_init_param scan_init = {
        .connect_if_match = 0,
    };
    bt_scan_init(&scan_init);
    bt_scan_cb_register(&scan_cb);

    cdc_printf("Central-only NUS aggregator CMD-mode: boot OK\n");
    cdc_printf("Use 'scan' to start scanning, 'conn' to connect all.\n");

    /* 默认不自动 scan，等待 CDC 命令 */
    while (1) {
        k_msleep(1000);
    }
}
