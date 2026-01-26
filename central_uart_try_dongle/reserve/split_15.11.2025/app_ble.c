#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/byteorder.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/addr.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <bluetooth/scan.h>
#include <bluetooth/gatt_dm.h>
#include <zephyr/bluetooth/hci.h>

#include <bluetooth/services/nus.h>
#include <bluetooth/services/nus_client.h>

#include "cdc_async.h"
#include "app_ble.h"

/* ===== 配置参数 ===== */
#define MAX_PEERS   11
#define CAND_MAX    24

/* ===== 全局变量 ===== */
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

/* GATT discovery 状态（防止 -EALREADY） */
static int  gatt_active_slot = -1;     /* 当前哪个 slot 在跑 bt_gatt_dm_start，-1 表示空闲 */
static bool gatt_pending[MAX_PEERS];   /* 哪些 slot 还需要做 NUS 的 discovery */

/* === 前置声明 === */
static void gatt_discover_start(struct bt_conn *conn, int slot);
static void gatt_discover_try_start(void);

/* ===== 小工具 ===== */
static bool any_conn_active(void)
{
    for (int i = 0; i < MAX_PEERS; i++) {
        if (conns[i]) {
            return true;
        }
    }
    return false;
}

int app_ble_get_conn_count(void)
{
    int n = 0;
    for (int i = 0; i < MAX_PEERS; i++) {
        if (conns[i]) {
            n++;
        }
    }
    return n;
}

static int alloc_conn_slot(struct bt_conn *conn)
{
    for (int i = 0; i < MAX_PEERS; i++) {
        if (!conns[i]) {
            conns[i] = conn;
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

/* ===== NUS 控制命令 (TSYNC / RSLOT) ===== */

void app_ble_tsync_all(uint64_t host_ms)
{
    uint8_t buf[1 + 8];
    buf[0] = 'T';

    for (int i = 0; i < 8; i++) {
        buf[1 + i] = (host_ms >> (8 * i)) & 0xFF;
    }

    for (int i = 0; i < MAX_PEERS; i++) {
        if (!conns[i]) {
            continue;
        }
        int err = bt_nus_client_send(&nus_clients[i], buf, sizeof(buf));
        if (err) {
            cdc_printf("TSYNC: slot %d send failed: %d\r\n", i, err);
        } else {
            cdc_printf("TSYNC: slot %d host_ms=%llu\r\n",
                       i, (unsigned long long)host_ms);
        }
    }
}

void app_ble_rslot(int slot, uint16_t fps, uint16_t phase_ms)
{
    if (slot < 0 || slot >= MAX_PEERS) {
        cdc_printf("RSLOT: invalid slot %d\r\n", slot);
        return;
    }
    if (!conns[slot]) {
        cdc_printf("RSLOT: slot %d not connected\r\n", slot);
        return;
    }

    uint8_t buf[1 + 2 + 2];
    buf[0] = 'R';
    sys_put_le16(fps,      &buf[1]);
    sys_put_le16(phase_ms, &buf[3]);

    int err = bt_nus_client_send(&nus_clients[slot], buf, sizeof(buf));
    if (err) {
        cdc_printf("RSLOT: slot %d send failed: %d\r\n", slot, err);
    } else {
        cdc_printf("RSLOT: slot %d fps=%u phase_ms=%u\r\n",
                   slot, fps, phase_ms);
    }
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
        cands[idx].rssi = rssi;
        strncpy(cands[idx].name, name, sizeof(cands[idx].name) - 1);
        cands[idx].name[sizeof(cands[idx].name) - 1] = '\0';
        return idx;
    }
}

/* ===== 广播 name 解析 ===== */

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

/* ===== 扫描期间处理每个广播：只缓存，不连接 ===== */

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

    cdc_printf("SCAN[%d]:%s,%02X:%02X:%02X:%02X:%02X:%02X,%d\r\n",
               idx,
               name,
               v[5], v[4], v[3], v[2], v[1], v[0],
               device_info->recv_info->rssi);
}

/* ===== bt_scan 回调 ===== */

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
    cdc_printf("scan_connecting_error\r\n");
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

/* ===== NUS 收到数据：直接转发到 USB CDC（二进制） ===== */

static uint8_t nus_receive_cb(struct bt_nus_client *nus,
                              const uint8_t *data, uint16_t len)
{
    int slot = -1;

    for (int i = 0; i < MAX_PEERS; i++) {
        if (&nus_clients[i] == nus) {
            slot = i;
            break;
        }
    }

    if (slot < 0) {
        return BT_GATT_ITER_CONTINUE;
    }

    /* 第一字节作为 source ID（slot），紧接 payload 二进制 */
    uint8_t header[1] = { (uint8_t)slot };

    /* 如果关掉了 CDC streaming，就什么都不做 */
    if (!cdc_async_is_enabled()) {
        return BT_GATT_ITER_CONTINUE;
    }

    cdc_async_write(header, sizeof(header));
    cdc_async_write(data, len);

    return BT_GATT_ITER_CONTINUE;
}

static void nus_sent_cb(struct bt_nus_client *nus, uint8_t err,
                        const uint8_t *const data, uint16_t len)
{
    ARG_UNUSED(nus);
    ARG_UNUSED(data);
    ARG_UNUSED(len);

    if (err) {
        cdc_printf("NUS send error: 0x%02X\r\n", err);
    }
}

static const struct bt_nus_client_init_param nus_init_param = {
    .cb = {
        .received = nus_receive_cb,
        .sent     = nus_sent_cb,
    },
};

/* ===== GATT DM 串行调度实现 ===== */

static struct bt_gatt_dm_cb gatt_dm_cb;

static void gatt_discover_try_start(void)
{
    if (gatt_active_slot >= 0) {
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
        cdc_printf("bt_gatt_dm_start(slot=%d) -> %d\r\n", i, err);

        if (!err) {
            gatt_active_slot = i;
            gatt_pending[i]  = false;
        } else {
            cdc_printf("gatt_dm_start failed for slot=%d, err=%d\r\n", i, err);
        }

        return;
    }
}

static void gatt_discover_start(struct bt_conn *conn, int slot)
{
    ARG_UNUSED(conn);

    if (slot < 0 || slot >= MAX_PEERS) {
        return;
    }

    gatt_pending[slot] = true;
    gatt_discover_try_start();
}

/* GATT DM 回调 */

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
    cdc_printf("bt_nus_handles_assign(slot=%d) -> %d\r\n", slot, err);
    if (!err) {
        err = bt_nus_subscribe_receive(nus);
        cdc_printf("bt_nus_subscribe_receive(slot=%d) -> %d\r\n", slot, err);
    }

    bt_gatt_dm_data_release(dm);

    gatt_active_slot = -1;
    gatt_discover_try_start();
}

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
    cdc_printf("NUS service NOT found on %s (slot=%d)\r\n", addr, slot);

    gatt_active_slot = -1;
    gatt_discover_try_start();
}

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
    cdc_printf("NUS discovery error on %s (slot=%d, err=%d)\r\n",
               addr, slot, err);

    gatt_active_slot = -1;
    gatt_discover_try_start();
}

/* ===== 连接回调 ===== */

static void connected_cb(struct bt_conn *conn, uint8_t err)
{
    struct bt_conn_info info;
    bt_conn_get_info(conn, &info);

    if (err) {
        cdc_printf("Connect failed (0x%02x)\r\n", err);
        connecting = false;
        /* 当前候选失败，试下一个 */
        app_ble_connect_whitelist();
        return;
    }

    const uint8_t *v = info.le.dst->a.val;
    cdc_printf("Connected to %02X:%02X:%02X:%02X:%02X:%02X\r\n",
               v[5], v[4], v[3], v[2], v[1], v[0]);

    /* 请求 2M PHY */
    struct bt_conn_le_phy_param phy = {
        .options     = BT_CONN_LE_PHY_OPT_NONE,
        .pref_tx_phy = BT_GAP_LE_PHY_2M,
        .pref_rx_phy = BT_GAP_LE_PHY_2M,
    };

    int phy_err = bt_conn_le_phy_update(conn, &phy);
    if (phy_err) {
        cdc_printf("bt_conn_le_phy_update(2M) failed: %d\r\n", phy_err);
    } else {
        cdc_printf("Requested 2M PHY\r\n");
    }

    int slot = alloc_conn_slot(conn);
    cdc_printf("connected_cb: slot=%d\r\n", slot);

    if (slot >= 0) {
        gatt_discover_start(conn, slot);
    } else {
        cdc_printf("No free conn slot, disconnecting\r\n");
        bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
    }

    connecting = false;
    /* 自动继续连下一个候选 */
    app_ble_connect_whitelist();
}

static void disconnected_cb(struct bt_conn *conn, uint8_t reason)
{
    cdc_printf("Disconnected (0x%02x)\r\n", reason);

    for (int i = 0; i < MAX_PEERS; i++) {
        if (conns[i] == conn) {
            conns[i] = NULL;
            gatt_pending[i] = false;
            if (gatt_active_slot == i) {
                gatt_active_slot = -1;
            }
            bt_conn_unref(conn);
            break;
        }
    }

    if (!any_conn_active()) {
        /* 所有连接断开 */
    }

    gatt_discover_try_start();
}

static struct bt_conn_cb conn_cb = {
    .connected = connected_cb,
    .disconnected = disconnected_cb,
};

/* ===== 公共控制函数：扫描 / 连接 / 断开 ===== */

int app_ble_start_scan(void)
{
    if (scanning) {
        cdc_printf("SCAN: already running\r\n");
        return 0;
    }

    clear_candidates();

    struct bt_scan_init_param scan_init = {
        .connect_if_match = 0,
    };

    bt_scan_init(&scan_init);
    bt_scan_cb_register(&scan_cb);

    int err = bt_scan_start(BT_SCAN_TYPE_SCAN_ACTIVE);
    cdc_printf("bt_scan_start -> %d\r\n", err);
    if (!err) {
        scanning = true;
    }
    return err;
}

int app_ble_stop_scan(void)
{
    if (!scanning) {
        return 0;
    }

    int err = bt_scan_stop();
    cdc_printf("bt_scan_stop -> %d\r\n", err);
    if (!err) {
        scanning = false;
    }
    return err;
}

int app_ble_connect_whitelist(void)
{
    if (connecting) {
        cdc_printf("CMD conn: connect already in progress\r\n");
        return 0;
    }

    /* 连接前先停扫描，避免冲突 */
    app_ble_stop_scan();

    for (int i = 0; i < CAND_MAX; i++) {
        if (!cands[i].used) {
            continue;
        }

        const bt_addr_le_t *addr = &cands[i].addr;

        /* 如果已经连上该地址，就跳过 */
        for (int j = 0; j < MAX_PEERS; j++) {
            if (!conns[j]) {
                continue;
            }
            struct bt_conn_info info;
            if (bt_conn_get_info(conns[j], &info) == 0) {
                if (bt_addr_le_cmp(addr, info.le.dst) == 0) {
                    cdc_printf("CAND[%d]: already connected, skip\r\n", i);
                    addr = NULL;
                    break;
                }
            }
        }

        if (!addr) {
            continue;
        }

        struct bt_conn *conn = NULL;
        int err = bt_conn_le_create(addr,
                                    BT_CONN_LE_CREATE_CONN,
                                    BT_LE_CONN_PARAM_DEFAULT,
                                    &conn);
        cdc_printf("CAND[%d]: bt_conn_le_create -> %d\r\n", i, err);
        if (err) {
            continue;
        }

        connecting = true;
        bt_conn_unref(conn);
        return 0;
    }

    cdc_printf("CMD conn: no more candidates to connect\r\n");
    return 0;
}

void app_ble_disconnect_all(void)
{
    for (int i = 0; i < MAX_PEERS; i++) {
        if (conns[i]) {
            bt_conn_disconnect(conns[i], BT_HCI_ERR_REMOTE_USER_TERM_CONN);
        }
    }
}

/* ===== 初始化入口 ===== */

int app_ble_init(void)
{
    int err = bt_enable(NULL);
    cdc_printf("bt_enable -> %d\r\n", err);
    if (err) {
        return err;
    }

    bt_conn_cb_register(&conn_cb);
    settings_load();
    cdc_printf("BT initialized.\r\n");

    gatt_dm_cb.completed         = discovery_complete_cb;
    gatt_dm_cb.service_not_found = discovery_not_found_cb;
    gatt_dm_cb.error_found       = discovery_error_cb;

    for (int i = 0; i < MAX_PEERS; i++) {
        bt_nus_client_init(&nus_clients[i], &nus_init_param);
        gatt_pending[i] = false;
    }

    /* 不在这里自动启动扫描，由按钮 / 命令控制 */
    return 0;
}
