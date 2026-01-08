/*
 * Central-only NUS Aggregator (命令控制版 + NUS client)
 * nRF Connect SDK 2.8.0 / Zephyr 3.7.x
 *
 * 特性:
 *  - USB CDC 输出日志 + 接收命令
 *  - "scan": 开始扫描，缓存所有 eFxx 设备（只扫不连）
 *  - "conn": 停止扫描，对所有已缓存的 eFxx 设备发起连接（串行）
 *  - 最多支持 MAX_PEERS 个并行连接
 *  - 每个连接上自动发现 NUS service，订阅 Notify（GATT discovery 串行排队）
 *  - 收到的 NUS 数据用 CDC 以十六进制打印出来
 *
 * 提示:
 *  - prj.conf 中请确保：
 *      CONFIG_BT_CENTRAL=y
 *      CONFIG_BT_MAX_CONN=11
 *      CONFIG_BT_GATT_CLIENT=y
 *      CONFIG_BT_NUS_CLIENT=y
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/usb/usb_device.h>
#include <zephyr/sys/util.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/reboot.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/addr.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <bluetooth/scan.h>
#include <bluetooth/gatt_dm.h>
#include <bluetooth/services/nus.h>          /* 提供 BT_UUID_NUS_SERVICE */
#include <bluetooth/services/nus_client.h>   /* NUS client 本体 */
#include <zephyr/sys/byteorder.h>  /* sys_put_le16 用 */

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

/* ★★ 每个 slot 的统计（多 TX 用） ★★ */
static volatile uint32_t g_imu_frames_slot[MAX_PEERS];    /* 'I' 帧数：IMU */
static volatile uint32_t g_imu_samples_slot[MAX_PEERS];   /* 'I' 里样本总数（cnt 累加） */
static volatile uint32_t g_uwb_frames_slot[MAX_PEERS];    /* 'U' 帧数：UWB */
static volatile uint32_t g_other_frames_slot[MAX_PEERS];  /* 其它类型，比如 'S' 心跳等 */

/* 何时开始输出统计：默认 false，至少连上一个 TX 后再置 true */
static bool g_stats_enabled = false;

static const struct gpio_dt_spec led_scan = GPIO_DT_SPEC_GET(LED_SCAN_NODE, gpios);
static const struct gpio_dt_spec led_conn = GPIO_DT_SPEC_GET(LED_CONN_NODE, gpios);
static const struct gpio_dt_spec led_rx   = GPIO_DT_SPEC_GET(LED_RX_NODE, gpios);

/* ===== 全局变量 ===== */
static const struct device *cdc = DEVICE_DT_GET_ONE(zephyr_cdc_acm_uart);

/* 连接数组 + NUS client 数组 */
static struct bt_conn       *conns[MAX_PEERS];
static struct bt_nus_client  nus_clients[MAX_PEERS];

/* 扫描 / 候选缓存 */
struct cand_t {
    bt_addr_le_t addr;
    char   name[32];
    int8_t rssi;
    bool   used;
};
static struct cand_t cands[CAND_MAX];
static bool scanning   = false;
static bool connecting = false;   /* 是否正在发起连接 */

/* CDC 命令线程同步 */
K_SEM_DEFINE(cdc_ready_sem, 0, 1);

/* ===== GATT discovery 状态（防止 -EALREADY） ===== */
/* 当前哪个 slot 正在跑 bt_gatt_dm_start，-1 表示空闲 */
static int  gatt_active_slot = -1;
/* 哪些 slot 还需要做 NUS 的 discovery（排队用） */
static bool gatt_pending[MAX_PEERS];

/* ===== 前置声明（NUS / GATT DM） ===== */
static uint8_t nus_receive_cb(struct bt_nus_client *nus,
                              const uint8_t *data, uint16_t len);
static void    nus_sent_cb(struct bt_nus_client *nus, uint8_t err,
                           const uint8_t *const data, uint16_t len);

static void gatt_discover_start(struct bt_conn *conn, int slot);
static void gatt_discover_try_start(void);
/* 提前告诉编译器有这个函数 */
static void cdc_printf(const char *fmt, ...);

static const struct bt_nus_client_init_param nus_init_param = {
    .cb = {
        .received = nus_receive_cb,
        .sent     = nus_sent_cb,
    },
};

/* GATT DM 回调 */
static void discovery_complete_cb(struct bt_gatt_dm *dm, void *context);
static void discovery_not_found_cb(struct bt_conn *conn, void *context);
static void discovery_error_cb(struct bt_conn *conn, int err, void *context);

static struct bt_gatt_dm_cb gatt_dm_cb = {
    .completed         = discovery_complete_cb,
    .service_not_found = discovery_not_found_cb,
    .error_found       = discovery_error_cb,
};

/* ====== 向所有已连接 TX 发送 'T' 时间同步命令 ======
 * 格式： 'T' + uint64 host_ms (little-endian)
 */
static void send_cmd_T_to_all(uint64_t host_ms)
{
    uint8_t buf[1 + 8];
    buf[0] = 'T';

    for (int i = 0; i < 8; i++) {
        buf[1 + i] = (host_ms >> (8 * i)) & 0xFF;  // 小端
    }

    for (int i = 0; i < MAX_PEERS; i++) {
        if (!conns[i]) {
            continue;
        }
        int err = bt_nus_client_send(&nus_clients[i], buf, sizeof(buf));
        if (err) {
            cdc_printf("send 'T' to slot %d failed: %d\n", i, err);
        } else {
            cdc_printf("send 'T' to slot %d, host_ms=%llu\n",
                       i, (unsigned long long)host_ms);
        }
    }
}

/* ====== 向某个 slot 的 TX 发送 'R' 速率/相位命令 ======
 * 格式： 'R' + uint16 fps + uint16 phase_ms （小端）
 */
static void send_cmd_R_to_slot(int slot, uint16_t fps, uint16_t phase_ms)
{
    if (slot < 0 || slot >= MAX_PEERS) {
        cdc_printf("send_cmd_R: invalid slot %d\n", slot);
        return;
    }
    if (!conns[slot]) {
        cdc_printf("send_cmd_R: slot %d not connected\n", slot);
        return;
    }

    uint8_t buf[1 + 2 + 2];
    buf[0] = 'R';
    sys_put_le16(fps,      &buf[1]);
    sys_put_le16(phase_ms, &buf[3]);

    int err = bt_nus_client_send(&nus_clients[slot], buf, sizeof(buf));
    if (err) {
        cdc_printf("send 'R' to slot %d failed: %d\n", slot, err);
    } else {
        cdc_printf("send 'R' to slot %d: fps=%u, phase_ms=%u\n",
                   slot, fps, phase_ms);
    }
}


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
static bool any_conn_active(void)
{
    for (int i = 0; i < MAX_PEERS; i++) {
        if (conns[i]) {
            return true;
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

static int find_conn_slot(struct bt_conn *conn)
{
    for (int i = 0; i < MAX_PEERS; i++) {
        if (conns[i] == conn) {
            return i;
        }
    }
    return -1;
}

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

/* ===== 扫描启动 / 停止（用 bt_scan 模块） ===== */
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

/* ===== GATT DM 串行调度实现 ===== */

/* 按 slot 轮流启动 GATT discovery，一次只跑一个，避免 -EALREADY */
static void gatt_discover_try_start(void)
{
    if (gatt_active_slot >= 0) {
        /* 已经有一个 discovery 在跑 */
        return;
    }

    for (int i = 0; i < MAX_PEERS; i++) {
        if (!conns[i]) {
            continue;
        }
        if (!gatt_pending[i]) {
            continue;
        }

        int err = bt_gatt_dm_start(conns[i], BT_UUID_NUS_SERVICE,
                                   &gatt_dm_cb, &nus_clients[i]);
        cdc_printf("bt_gatt_dm_start(slot=%d) -> %d\n", i, err);

        if (!err) {
            gatt_active_slot = i;
            gatt_pending[i]  = false;
        } else {
            cdc_printf("gatt_dm_start failed for slot=%d, err=%d\n", i, err);
        }

        /* 一次只尝试一个 slot */
        return;
    }
}

/* 启动某个连接上的 GATT DM → 找 NUS Service（排队版本） */
static void gatt_discover_start(struct bt_conn *conn, int slot)
{
    ARG_UNUSED(conn);

    if (slot < 0 || slot >= MAX_PEERS) {
        return;
    }

    /* 标记该 slot 需要做 discovery，真正的 start 由调度函数统一处理 */
    gatt_pending[slot] = true;
    gatt_discover_try_start();
}

/* ===== NUS + GATT DM 回调 ===== */

/* GATT DM: 找到 NUS Service */
static void discovery_complete_cb(struct bt_gatt_dm *dm, void *context)
{
    struct bt_nus_client *nus = context;
    int err;
    int slot = -1;

    for (int i = 0; i < MAX_PEERS; i++) {
        if (&nus_clients[i] == nus) {
            slot = i;
            break;
        }
    }

    err = bt_nus_handles_assign(dm, nus);
    cdc_printf("bt_nus_handles_assign(slot=%d) -> %d\n", slot, err);
    if (!err) {
        err = bt_nus_subscribe_receive(nus);
        cdc_printf("bt_nus_subscribe_receive(slot=%d) -> %d\n", slot, err);
    }

    bt_gatt_dm_data_release(dm);

    /* 本条 discovery 结束，释放占用，继续尝试下一个 pending slot */
    gatt_active_slot = -1;
    gatt_discover_try_start();
}

/* GATT DM: 没找到 NUS Service */
static void discovery_not_found_cb(struct bt_conn *conn, void *context)
{
    struct bt_nus_client *nus = context;
    int slot = -1;
    for (int i = 0; i < MAX_PEERS; i++) {
        if (&nus_clients[i] == nus) {
            slot = i;
            break;
        }
    }

    char addr[BT_ADDR_LE_STR_LEN];
    bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
    cdc_printf("NUS service NOT found on %s (slot=%d)\n", addr, slot);

    gatt_active_slot = -1;
    gatt_discover_try_start();
}

/* GATT DM: 出错 */
static void discovery_error_cb(struct bt_conn *conn, int err, void *context)
{
    struct bt_nus_client *nus = context;
    int slot = -1;
    for (int i = 0; i < MAX_PEERS; i++) {
        if (&nus_clients[i] == nus) {
            slot = i;
            break;
        }
    }

    char addr[BT_ADDR_LE_STR_LEN];
    bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
    cdc_printf("NUS discovery error on %s (slot=%d, err=%d)\n",
               addr, slot, err);

    gatt_active_slot = -1;
    gatt_discover_try_start();
}

/* ★★ NUS 收到数据：按 slot 统计帧率 ★★ */
static uint8_t nus_receive_cb(struct bt_nus_client *nus,
                              const uint8_t *data, uint16_t len)
{
    int slot = -1;

    /* 找一下当前 nus 对应哪个 slot */
    for (int i = 0; i < MAX_PEERS; i++) {
        if (&nus_clients[i] == nus) {
            slot = i;
            break;
        }
    }

    if (slot < 0) {
        /* 理论上不应该发生 */
        return BT_GATT_ITER_CONTINUE;
    }

    /* 统计逻辑（可以保留，之后想用统计还在） */
    if (len >= 2 && data[0] == 0xAA) {
        switch (data[1]) {
        case 'I': {  /* IMU 帧 */
            g_imu_frames_slot[slot]++;
            if (len >= 13) {
                uint8_t cnt = data[12];  // ★ 帧头第 13 字节是 cnt
                g_imu_samples_slot[slot] += cnt;
            }
            break;
        }

        case 'U':   /* UWB 帧 */
            g_uwb_frames_slot[slot]++;
            break;

        default:
            g_other_frames_slot[slot]++;
            break;
        }
    } else {
        g_other_frames_slot[slot]++;
    }

    /* 不再打印 t_global */
    // if (len >= 10 && data[0] == 0xAA && data[1] == 'I') {
    //     uint32_t t_global = sys_get_le32(&data[6]);
    //     cdc_printf("[slot %d] I-frame t_global=%u\n", slot, t_global);
    // }

    /* ★★ 这里打印原始十六进制数据 ★★ */
    cdc_printf("S[%d]:", slot);   /* 想要完全“原始”也可以去掉这个前缀 */
    for (uint16_t i = 0; i < len; i++) {
        cdc_printf("%02X", data[i]);
    }
    cdc_printf("\n");

    return BT_GATT_ITER_CONTINUE;
}


/* NUS 发送完成回调（当前不怎么用） */
static void nus_sent_cb(struct bt_nus_client *nus, uint8_t err,
                        const uint8_t *const data, uint16_t len)
{
    ARG_UNUSED(nus);
    ARG_UNUSED(data);
    ARG_UNUSED(len);

    if (err) {
        cdc_printf("NUS send error: 0x%02X\n", err);
    }
}

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

        /* 注意：这里不分配 slot，也不做 GATT 发现
         * 只标记 connecting=true，让 connected_cb 里接着处理
         */
        connecting = true;

        /* 这里可以立即 unref：connected_cb 会拿到自己的 conn 指针 */
        bt_conn_unref(conn);

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

    /* 这里才是真正「连接完成」的时刻：
     * 1) 分配 slot
     * 2) 启动（排队）GATT DM 发现 NUS
     */
    int slot = alloc_conn_slot(conn);
    cdc_printf("connected_cb: slot=%d\n", slot);

    if (slot >= 0) {
        /* 清空这个 slot 的历史统计，方便看干净数据 */
        g_imu_frames_slot[slot]   = 0;
        g_imu_samples_slot[slot]  = 0;
        g_uwb_frames_slot[slot]   = 0;
        g_other_frames_slot[slot] = 0;

        gatt_discover_start(conn, slot);
    } else {
        cdc_printf("No free conn slot in connected_cb, disconnecting\n");
        bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
    }

    connecting = false;              /* 这条连上了，可以继续连下一个 */
    connect_all_candidates();        /* 自动继续连下一个 CAND */
}

static void disconnected_cb(struct bt_conn *conn, uint8_t reason)
{
    cdc_printf("Disconnected (0x%02x)\n", reason);

    for (int i = 0; i < MAX_PEERS; i++) {
        if (conns[i] == conn) {
            conns[i] = NULL;
            gatt_pending[i] = false;
            if (gatt_active_slot == i) {
                gatt_active_slot = -1;
            }

            /* 清掉这个 slot 的统计，避免下次复用时残留 */
            g_imu_frames_slot[i]   = 0;
            g_imu_samples_slot[i]  = 0;
            g_uwb_frames_slot[i]   = 0;
            g_other_frames_slot[i] = 0;

            bt_conn_unref(conn);
            break;
        }
    }

    if (!any_conn_active()) {
        led_set(&led_conn, 0);
        g_stats_enabled = false;   /* 所有连接都断了，统计也先停掉 */
    }

    /* 断开可能释放了一个占用的 slot，尝试继续下一个 discovery */
    gatt_discover_try_start();
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
    } else if (strcmp(cmd, "reboot") == 0) {
        cdc_printf("CMD: reboot -> sys_reboot\n");
        sys_reboot(SYS_REBOOT_COLD);

    } else if (strncmp(cmd, "TSYNC", 5) == 0) {
        /* 格式：TSYNC <host_ms> */
        uint64_t host_ms = 0;
        int n = sscanf(cmd + 5, "%llu", (unsigned long long *)&host_ms);
        if (n == 1) {
            send_cmd_T_to_all(host_ms);
        } else {
            cdc_printf("CMD TSYNC parse error. Usage: TSYNC <host_ms>\n");
        }

    } else if (strncmp(cmd, "RSLOT", 5) == 0) {
        /* 格式：RSLOT <slot> <fps> <phase_ms> */
        int slot = 0;
        unsigned int fps = 0, phase_ms = 0;
        int n = sscanf(cmd + 5, "%d %u %u", &slot, &fps, &phase_ms);
        if (n == 3) {
            send_cmd_R_to_slot(slot, (uint16_t)fps, (uint16_t)phase_ms);
        } else {
            cdc_printf("CMD RSLOT parse error. Usage: RSLOT <slot> <fps> <phase_ms>\n");
        }

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
int main(void)
{
    /* LED / 按键初始化 */
    gpio_pin_configure_dt(&led_scan, GPIO_OUTPUT_INACTIVE);
    gpio_pin_configure_dt(&led_conn, GPIO_OUTPUT_INACTIVE);
    gpio_pin_configure_dt(&led_rx,   GPIO_OUTPUT_INACTIVE);
    dk_buttons_init(NULL);

    /* USB CDC 初始化 */
    usb_enable(NULL);
    if (!device_is_ready(cdc)) {
        return 0;
    }
    cdc_printf("CDC ready\n");

    /* 告诉 cdc_rx_thread：CDC 可以用了 */
    k_sem_give(&cdc_ready_sem);

    /* 蓝牙初始化 */
    int berr = bt_enable(NULL);
    cdc_printf("bt_enable -> %d\n", berr);
    if (berr) {
        cdc_printf("Bluetooth init failed\n");
        return 0;
    }

    bt_conn_cb_register(&conn_cb);
    settings_load();
    cdc_printf("BT initialized.\n");

    /* 初始化 NUS client 实例 */
    for (int i = 0; i < MAX_PEERS; i++) {
        bt_nus_client_init(&nus_clients[i], &nus_init_param);
        gatt_pending[i] = false;

        g_imu_frames_slot[i]   = 0;
        g_imu_samples_slot[i]  = 0;
        g_uwb_frames_slot[i]   = 0;
        g_other_frames_slot[i] = 0;
    }

    /* bt_scan 初始化（不自动连接，全靠命令控制） */
    struct bt_scan_init_param scan_init = {
        .connect_if_match = 0,
    };
    bt_scan_init(&scan_init);
    bt_scan_cb_register(&scan_cb);

    cdc_printf("Central-only NUS aggregator CMD-mode + NUS: boot OK\n");
    cdc_printf("Use 'scan' to start scanning, 'conn' to connect all.\n");

    /* ===== 统计输出主循环 =====
     * 需求：
     *  - 连接建立前，不打印任何 IMU/UWB 帧统计（避免混乱）
     *  - 至少有一个连接 active 后，才开始每秒输出
     *  - 每个 slot 一行输出：[0] / [1] 这种前缀
     */
    while (1) {
        // if (!g_stats_enabled) {
        //     if (any_conn_active()) {
        //         g_stats_enabled = true;
        //         cdc_printf("=== Connections ready, start stats output ===\n");
        //     } else {
        //         /* 还没连上任何 TX，就先安静一会 */
        //         k_sleep(K_SECONDS(1));
        //         continue;
        //     }
        // }

        // /* 对每个 slot 分别输出一行统计 */
        // for (int i = 0; i < MAX_PEERS; i++) {
        //     if (!conns[i]) {
        //         continue;   /* 这个 slot 没有连接，跳过 */
        //     }

        //     uint32_t imu_f   = g_imu_frames_slot[i];
        //     uint32_t imu_s   = g_imu_samples_slot[i];
        //     uint32_t uwb_f   = g_uwb_frames_slot[i];
        //     uint32_t other_f = g_other_frames_slot[i];

        //     /* 为下一秒统计清零 */
        //     g_imu_frames_slot[i]   = 0;
        //     g_imu_samples_slot[i]  = 0;
        //     g_uwb_frames_slot[i]   = 0;
        //     g_other_frames_slot[i] = 0;

        //     float avg = (imu_f > 0) ? ((float)imu_s / (float)imu_f) : 0.0f;

        //     cdc_printf("[%d] IMU_frames/s = %u, IMU_samples/s = %u, "
        //                "avg_cnt = %.2f, UWB_frames/s = %u, OTHER_frames/s = %u\n",
        //                i, imu_f, imu_s, avg, uwb_f, other_f);
        // }

        k_sleep(K_SECONDS(1));
    }

    return 0;
}
