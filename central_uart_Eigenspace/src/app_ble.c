/* src/app_ble.c — BioSpur Central Multi-NUS (RX)
 *
 * - Central + 多 NUS Client（最多 11 个）
 * - 只扫描名字以 "eF" 开头的 TX
 * - 使用 Just-Works 或无加密都可以（不强制在这里提安全等级）
 * - 每个 TX 连接后必须先发 APP_KEY 帧：
 *       'K' + u32(APP_KEY_EXPECTED, little-endian)
 * - 只有 APP_KEY 正确的连接才认为是「已授权」，数据才透传到 USB CDC
 *
 * 对外 API（与 app_ble.h 对齐）：
 *   int  app_ble_init(void);
 *   int  app_ble_start_scan(void);
 *   int  app_ble_stop_scan(void);
 *   int  app_ble_connect_whitelist(void);
 *   void app_ble_disconnect_all(void);
 *   int  app_ble_get_conn_count(void);
 *   void app_ble_tsync_all(uint64_t host_ms);
 *   void app_ble_rslot(int slot, uint16_t fps, uint16_t phase_ms);
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/util.h>
#include <zephyr/sys/printk.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/hci.h>

#include <bluetooth/gatt_dm.h>
#include <bluetooth/services/nus_client.h>
#include <bluetooth/services/nus.h>

#include <string.h>

#include "cdc_async.h"
#include "app_ble.h"

/* ========================= 配置参数 ========================= */

#define MAX_PEERS          11
#define CAND_MAX           16

#define APP_KEY_EXPECTED   936656u

#define EF_PREFIX_0        'E'
#define EF_PREFIX_1        'S'

/* ========================= 名字白名单 ========================= */
/* 只允许连接这些 eFxxxx，其他 eFxxxx 直接忽略 */

static const char *whitelist_names[] = {
    "ES1D4F",// left hand
    "ES744C",//right hand
};

#define WHITELIST_COUNT (sizeof(whitelist_names) / sizeof(whitelist_names[0]))

static bool name_in_whitelist(const char *name)
{
    if (!name || name[0] == '\0') {
        return false;
    }

    for (int i = 0; i < WHITELIST_COUNT; i++) {
        if (strcmp(name, whitelist_names[i]) == 0) {
            return true;
        }
    }
    return false;
}

/* 自己定义一个 NUS Service UUID 实例（写死 NUS UUID，避开各种宏名差异） */
static const struct bt_uuid_128 nus_svc_uuid =
    BT_UUID_INIT_128(BT_UUID_128_ENCODE(0x6E400001, 0xB5A3,
                                        0xF393, 0xE0A9,
                                        0xE50E24DCCA9E));


/* ========================= 候选 + 连接表 ========================= */

struct cand_entry {
    bool         used;
    bt_addr_le_t addr;
    char         name[32];
};

static struct cand_entry    cands[CAND_MAX];

static struct bt_conn      *conns[MAX_PEERS];
static struct bt_nus_client nus_clients[MAX_PEERS];

static bool                 slot_authed[MAX_PEERS];
static bool                 gatt_pending[MAX_PEERS];
static int                  gatt_active_slot = -1;

static bool                 scanning;
static bool                 connecting;

/* ========================= 内部声明 ========================= */

static void gatt_discover_try_start(void);
int         app_ble_connect_whitelist(void);   /* 供内部/外部调用 */

/* ========================= NUS 回调 ========================= */


static uint8_t nus_recv_cb(struct bt_nus_client *nus,
                           const uint8_t *data,
                           uint16_t len)
{
    int slot = -1;

    /* 找到是哪个连接（slot） */
    for (int i = 0; i < MAX_PEERS; i++) {
        if (&nus_clients[i] == nus) {
            slot = i;
            break;
        }
    }

    if (slot < 0 || len == 0) {
        return BT_GATT_ITER_CONTINUE;
    }

    /* ========= APP_KEY 硬门禁 =========== */

    /* 若收到 'K' + 4byte key，进行校验 */
    if (len == 5 && data[0] == 'K') {
        uint32_t key = sys_get_le32(&data[1]);

        if (key == APP_KEY_EXPECTED) {
            slot_authed[slot] = true;
            cdc_printf("APP_KEY OK slot %d (key=%u)\r\n", slot, key);
        } else {
            slot_authed[slot] = false;
            cdc_printf("APP_KEY BAD slot %d (key=%u)\r\n", slot, key);
        }

        /* K 帧本身不透传 */
        return BT_GATT_ITER_CONTINUE;
    }

    /* ========== ⚠️ 硬门禁：未认证禁止任何透传 ========== */
    if (!slot_authed[slot]) {
        // cdc_printf("DROP no APP_KEY slot %d len=%d\r\n", slot, len);
        return BT_GATT_ITER_CONTINUE;
    }

    /* ========== 通过验证 → 正常透传到 USB ========== */
    (void)cdc_async_write(data, len);

    return BT_GATT_ITER_CONTINUE;
}

static void nus_sent_cb(struct bt_nus_client *nus,
                        uint8_t err,
                        const uint8_t *data,
                        uint16_t len)
{
    ARG_UNUSED(nus);
    ARG_UNUSED(err);
    ARG_UNUSED(data);
    ARG_UNUSED(len);
    /* 暂时不关心 sent 回调 */
}

/* NCS 2.8: bt_nus_client_init 需要 init_param 结构体 */
static const struct bt_nus_client_init_param nus_init_param = {
    .cb = {
        .received = nus_recv_cb,
        .sent     = nus_sent_cb,
    },
};

/* ========================= 候选管理 ========================= */

static int find_or_alloc_cand(const bt_addr_le_t *addr)
{
    for (int i = 0; i < CAND_MAX; i++) {
        if (cands[i].used &&
            bt_addr_le_cmp(&cands[i].addr, addr) == 0) {
            return i;
        }
    }

    for (int i = 0; i < CAND_MAX; i++) {
        if (!cands[i].used) {
            cands[i].used = true;
            bt_addr_le_copy(&cands[i].addr, addr);
            cands[i].name[0] = '\0';
            return i;
        }
    }

    return -ENOMEM;
}

static void scan_cb(const bt_addr_le_t *addr, int8_t rssi,
                    uint8_t type, struct net_buf_simple *ad)
{
    if (type != BT_HCI_ADV_IND &&
        type != BT_HCI_ADV_DIRECT_IND &&
        type != BT_HCI_ADV_SCAN_IND &&
        type != BT_HCI_ADV_NONCONN_IND) {
        return;
    }

    bt_addr_le_t addr_copy;
    bt_addr_le_copy(&addr_copy, addr);

    char name[32] = {0};

    while (ad->len > 1) {
        uint8_t len = net_buf_simple_pull_u8(ad);
        if (len == 0 || len > ad->len) {
            break;
        }
        uint8_t type2 = net_buf_simple_pull_u8(ad);
        len--;

        if (type2 == BT_DATA_NAME_COMPLETE ||
            type2 == BT_DATA_NAME_SHORTENED) {
            uint8_t copy_len = MIN(len, sizeof(name) - 1);
            memcpy(name, ad->data, copy_len);
            name[copy_len] = '\0';
            break;
        } else {
            net_buf_simple_pull(ad, len);
        }
    }

    if (name[0] == EF_PREFIX_0 && name[1] == EF_PREFIX_1) {
        /* ★★ 白名单过滤：只保留你指定的 5 个 eFxxxx ★★ */
        if (!name_in_whitelist(name)) {
            // cdc_printf("SCAN ignore non-whitelist: %s RSSI=%d\r\n", name, rssi);
            return;
        }

        int idx = find_or_alloc_cand(&addr_copy);
        if (idx >= 0) {
            strncpy(cands[idx].name, name, sizeof(cands[idx].name) - 1);
            cands[idx].name[sizeof(cands[idx].name) - 1] = '\0';

            const uint8_t *v = addr_copy.a.val;
            cdc_printf("SCAN[%d]: %s,%02X:%02X:%02X:%02X:%02X:%02X RSSI=%d\r\n",
                       idx, cands[idx].name,
                       v[5], v[4], v[3], v[2], v[1], v[0],
                       rssi);
        }
    }
}

/* ========================= GATT 发现 ========================= */

static void gatt_discover_complete(struct bt_gatt_dm *dm,
                                   void *context)
{
    int slot = (int)(intptr_t)context;

    int err = bt_nus_handles_assign(dm, &nus_clients[slot]);
    if (err) {
        cdc_printf("NUS assign failed slot=%d err=%d\r\n", slot, err);
        bt_gatt_dm_data_release(dm);
        gatt_pending[slot] = false;
        gatt_active_slot   = -1;
        gatt_discover_try_start();
        return;
    }

    err = bt_nus_subscribe_receive(&nus_clients[slot]);
    if (err) {
        cdc_printf("NUS subscribe failed slot=%d err=%d\r\n", slot, err);
    } else {
        cdc_printf("NUS subscribed slot=%d\r\n", slot);
    }

    bt_gatt_dm_data_release(dm);
    gatt_pending[slot] = false;
    gatt_active_slot   = -1;
    gatt_discover_try_start();
}

static void gatt_discover_not_found(struct bt_conn *conn,
                                    void *context)
{
    int slot = (int)(intptr_t)context;
    ARG_UNUSED(conn);

    cdc_printf("NUS service not found slot=%d\r\n", slot);
    gatt_pending[slot] = false;
    if (gatt_active_slot == slot) {
        gatt_active_slot = -1;
    }
    gatt_discover_try_start();
}

static void gatt_discover_error(struct bt_conn *conn,
                                int err, void *context)
{
    int slot = (int)(intptr_t)context;
    ARG_UNUSED(conn);

    cdc_printf("NUS discover error slot=%d err=%d\r\n", slot, err);
    gatt_pending[slot] = false;
    if (gatt_active_slot == slot) {
        gatt_active_slot = -1;
    }
    gatt_discover_try_start();
}

static const struct bt_gatt_dm_cb gatt_dm_cb = {
    .completed         = gatt_discover_complete,
    .service_not_found = gatt_discover_not_found,
    .error_found       = gatt_discover_error,
};

static void gatt_discover_try_start(void)
{
    if (gatt_active_slot >= 0) {
        return;
    }

    for (int i = 0; i < MAX_PEERS; i++) {
        if (conns[i] && gatt_pending[i]) {
            int err = bt_gatt_dm_start(conns[i],
                                       &nus_svc_uuid.uuid,
                                       &gatt_dm_cb,
                                       (void *)(intptr_t)i);
            if (err) {
                cdc_printf("bt_gatt_dm_start slot=%d err=%d\r\n", i, err);
                gatt_pending[i] = false;
            } else {
                cdc_printf("Start GATT DM slot=%d\r\n", i);
                gatt_active_slot = i;
                return;
            }
        }
    }
}

/* ========================= 连接表管理 ========================= */

static int count_conns_internal(void)
{
    int n = 0;
    for (int i = 0; i < MAX_PEERS; i++) {
        if (conns[i]) {
            n++;
        }
    }
    return n;
}

/* 只负责找一个空 slot，不再在这里写 conns[] */
static int alloc_conn_slot(void)
{
    for (int i = 0; i < MAX_PEERS; i++) {
        if (!conns[i]) {
            slot_authed[i]  = false;
            gatt_pending[i] = false;
            return i;
        }
    }
    return -1;
}

/* ========================= 安全回调 ========================= */

static void security_changed(struct bt_conn *conn,
                             bt_security_t level,
                             enum bt_security_err err)
{
    ARG_UNUSED(conn);

    if (err) {
        cdc_printf("Security failed (err=%d, level=%d)\n", err, level);
    } else {
        cdc_printf("Security OK (level=%d)\n", level);
    }
}

/* ========================= 连接回调 ========================= */

static void connected_cb(struct bt_conn *conn, uint8_t err)
{
    struct bt_conn_info info;
    bt_conn_get_info(conn, &info);

    if (err) {
        cdc_printf("Connect failed (0x%02x)\r\n", err);
        connecting = false;
        (void)app_ble_connect_whitelist();
        return;
    }

    const uint8_t *v = info.le.dst->a.val;

    char dev_name[32] = {0};
    bool ef_ok = false;

    for (int i = 0; i < CAND_MAX; i++) {
        if (!cands[i].used) {
            continue;
        }

        if (bt_addr_le_cmp(info.le.dst, &cands[i].addr) == 0) {
            strncpy(dev_name, cands[i].name, sizeof(dev_name) - 1);
            dev_name[sizeof(dev_name) - 1] = '\0';

            if (dev_name[0] == 'E' && dev_name[1] == 'S') {
                ef_ok = true;
            }
            break;
        }
    }

    /* 再加一层：必须是白名单名字 */
    if (!ef_ok || !name_in_whitelist(dev_name)) {
        cdc_printf("Reject non-whitelist device: %02X:%02X:%02X:%02X:%02X:%02X  name=%s\n",
                   v[5], v[4], v[3], v[2], v[1], v[0],
                   dev_name);
        bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
        return;
    }

    cdc_printf("Connected to valid TX %s (%02X:%02X:%02X:%02X:%02X:%02X)\r\n",
               dev_name,
               v[5], v[4], v[3], v[2], v[1], v[0]);

    /* 这里现在不强制提 L2 安全级别 */

    /* 请求 2M PHY（失败也没关系） */
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

    int slot = alloc_conn_slot();
    cdc_printf("connected_cb: slot=%d\r\n", slot);

    if (slot < 0) {
        cdc_printf("No free slot, disconnecting\r\n");
        bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
        return;
    }

    /* 这里才真正保存连接，并增加 ref */
    conns[slot]        = bt_conn_ref(conn);
    slot_authed[slot]  = false;
    gatt_pending[slot] = true;

    gatt_discover_try_start();

    connecting = false;
    (void)app_ble_connect_whitelist();
}

static void disconnected_cb(struct bt_conn *conn, uint8_t reason)
{
    cdc_printf("Disconnected (0x%02X)\n", reason);

    for (int i = 0; i < MAX_PEERS; i++) {
        if (conns[i] == conn) {
            bt_conn_unref(conns[i]);
            conns[i]        = NULL;
            slot_authed[i]  = false;
            gatt_pending[i] = false;
            if (gatt_active_slot == i) {
                gatt_active_slot = -1;
            }
            break;
        }
    }

    gatt_discover_try_start();
}

static struct bt_conn_cb conn_cb = {
    .connected        = connected_cb,
    .disconnected     = disconnected_cb,
    .security_changed = security_changed,
};

/* ========================= 扫描控制 ========================= */

static int scan_start_internal(void)
{
    struct bt_le_scan_param scan_param = {
        .type       = BT_HCI_LE_SCAN_ACTIVE,
        .options    = BT_LE_SCAN_OPT_FILTER_DUPLICATE,
        .interval   = 0x0010,
        .window     = 0x0010,
    };

    int err = bt_le_scan_start(&scan_param, scan_cb);
    if (err) {
        cdc_printf("scan_start -> %d\r\n", err);
        return err;
    }

    scanning = true;
    cdc_printf("scan_start -> 0\r\n");
    return 0;
}

static int scan_stop_internal(void)
{
    if (!scanning) {
        return 0;
    }

    int err = bt_le_scan_stop();
    if (err) {
        cdc_printf("scan_stop -> %d\r\n", err);
        return err;
    }

    scanning = false;
    cdc_printf("scan_stop -> 0\r\n");
    return 0;
}

/* ========================= 白名单连接 ========================= */

int app_ble_connect_whitelist(void)
{
    if (connecting) {
        cdc_printf("Connect already running\r\n");
        return -EBUSY;
    }

    /* 遍历所有候选 */
    for (int i = 0; i < CAND_MAX; i++) {
        if (!cands[i].used) {
            continue;
        }

        if (strlen(cands[i].name) < 2) {
            continue;
        }

        if (cands[i].name[0] != EF_PREFIX_0 ||
            cands[i].name[1] != EF_PREFIX_1) {
            continue;
        }

        /* ★ 再过滤一次白名单名字 */
        if (!name_in_whitelist(cands[i].name)) {
            continue;
        }

        /* 检查是否已经连上 */
        bool already = false;
        for (int j = 0; j < MAX_PEERS; j++) {
            if (!conns[j]) {
                continue;
            }

            struct bt_conn_info info;
            if (bt_conn_get_info(conns[j], &info) == 0 &&
                bt_addr_le_cmp(info.le.dst, &cands[i].addr) == 0) {
                already = true;
                break;
            }
        }

        if (already) {
            continue;
        }

        /* 建链前先停扫描，避免 HCI 忙不过来 */
        if (scanning) {
            int err2 = scan_stop_internal();
            if (err2) {
                cdc_printf("scan_stop before create err=%d\r\n", err2);
            }
        }

        struct bt_conn *conn = NULL;
        int err = bt_conn_le_create(&cands[i].addr,
                                    BT_CONN_LE_CREATE_CONN,
                                    BT_LE_CONN_PARAM_DEFAULT,
                                    &conn);
        if (err) {
            cdc_printf("CAND[%d]: create->%d\r\n", i, err);
            /* 建链失败，尝试恢复扫描，下次再按 BTN2 继续试 */
            if (!scanning) {
                (void)scan_start_internal();
            }
            continue;
        }

        cdc_printf("CAND[%d]: create->0 (%s)\r\n", i, cands[i].name);
        connecting = true;
        bt_conn_unref(conn);
        return 0;
    }

    cdc_printf("No more candidates to connect.\r\n");

    /* 没有可连的 candidate，确保在扫 */
    if (!scanning) {
        (void)scan_start_internal();
    }

    return 0;
}


/* ========================= 对外 API实现 ========================= */

int app_ble_start_scan(void)
{
    memset(cands, 0, sizeof(cands));
    return scan_start_internal();
}

int app_ble_stop_scan(void)
{
    return scan_stop_internal();
}

void app_ble_disconnect_all(void)
{
    for (int i = 0; i < MAX_PEERS; i++) {
        if (conns[i]) {
            bt_conn_disconnect(conns[i],
                               BT_HCI_ERR_REMOTE_USER_TERM_CONN);
        }
    }
}

int app_ble_get_conn_count(void)
{
    return count_conns_internal();
}

/* 实现 TSYNC具体协议对齐 TX 端格式 */

void app_ble_tsync_all(uint64_t host_ms)
{
    uint8_t buf[10];
    buf[0] = 0xAA;
    buf[1] = 'T';

    /* host_ms 写成小端格式，和 TX 里的 memcpy(u64) 一致 */
    sys_put_le64(host_ms, &buf[2]);

    for (int i = 0; i < MAX_PEERS; i++) {
        if (conns[i] && slot_authed[i]) {
            int err = bt_nus_client_send(&nus_clients[i], buf, sizeof(buf));
            cdc_printf("TSYNC -> slot %d err=%d\r\n", i, err);
        }
    }
}


void app_ble_rslot(int slot, uint16_t fps, uint16_t phase_ms)
{
    ARG_UNUSED(slot);
    ARG_UNUSED(fps);
    ARG_UNUSED(phase_ms);
    /* TODO: 同上，实现 R 帧二进制协议 */
}

int app_ble_init(void)
{
    int err = bt_enable(NULL);
    cdc_printf("bt_enable -> %d\n", err);
    if (err) {
        return err;
    }

    bt_conn_cb_register(&conn_cb);

    /* 加载 settings 后立即清空全部 bond，避免历史垃圾卡死连接 */
    settings_load();
    int unpair_err = bt_unpair(BT_ID_DEFAULT, NULL);
    cdc_printf("bt_unpair(all) -> %d\r\n", unpair_err);

    for (int i = 0; i < MAX_PEERS; i++) {
        bt_nus_client_init(&nus_clients[i], &nus_init_param);
        gatt_pending[i] = false;
        slot_authed[i]  = false;
        conns[i]        = NULL;
    }

    cdc_printf("BT initialized.\n");
    return 0;
}
