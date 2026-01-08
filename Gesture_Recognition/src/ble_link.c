/* src/ble_link.c — TX（Peripheral, A 组 APP_KEY=936656）
 *
 * - 不再使用蓝牙层 PIN / Passkey（Just-Works SMP 或无加密）
 * - 连接成功后周期性发送 APP_KEY 帧：'K' + 4 字节 936656（LE）
 * - NUS Server，主动发送 IMU/UWB 数据
 * - 支持 RX 发送的 TSYNC / RSLOT 指令
 * - 使用静态内存池 (k_mem_slab) 管理 BLE 发送 FIFO，避免 k_malloc 失败
 */

#include "app_config.h"
#include "device_id.h"
#include "ble_link.h"

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/settings/settings.h>
#include <zephyr/kernel.h>
#include <zephyr/device.h>

#include <bluetooth/services/nus.h>

#include <dk_buttons_and_leds.h>
#include <string.h>
#include <stdint.h>
#include <zephyr/sys/printk.h>

/* TSYNC: host_time_offset_ms = host_ms - uptime_ms */
static int64_t g_host_time_offset_ms = 0;

/* ===================================================== */
/* 可选：imu 控制入口的弱符号 stub
 *  - 如果工程里已经有真正的 imu_handle_ctrl_from_ble()，会覆盖掉这个 weak 版本
 *  - 如果没有，实现就什么都不做，但保证链接通过，也不用去改 imu_task.c
 * ===================================================== */
__attribute__((weak)) void imu_handle_ctrl_from_ble(const uint8_t *data, uint16_t len)
{
    ARG_UNUSED(data);
    ARG_UNUSED(len);
}

/* ====================== APP_KEY (A 组) ====================== */

#define APP_KEY_VALUE     936656U    /* APP 级别分组密钥（A 组） */
#define APP_KEY_INTERVAL  2000       /* 周期发送 APP_KEY 的间隔(ms) */

/* ====================== NUS FIFO & 发送 ====================== */

/* 发送 FIFO：外部通过 ble_link_send() 塞包进来，内部 TX 线程统一 bt_nus_send() */

K_FIFO_DEFINE(ble_tx_fifo);

struct ble_tx_item {
    void    *fifo_reserved;
    uint16_t len;
    uint8_t  data[BLE_BUF_SIZE];
};

/* 静态内存池：最多同时挂在 FIFO 里的包数量（根据你 IMU 频率略微留富余） */
#define BLE_TX_POOL_ITEMS 64

K_MEM_SLAB_DEFINE(ble_tx_slab,
                  sizeof(struct ble_tx_item),
                  BLE_TX_POOL_ITEMS,
                  4);

static struct bt_conn *current_conn;
static uint32_t g_ble_drop_count = 0;

/* BLE 就绪信号：init 完成后才允许发包 */
static struct k_sem ble_ready;

/* ====================== APP_KEY 周期发送逻辑 ====================== */

static struct k_work_delayable app_key_work;

/* 实际打包并发送 'K' + APP_KEY_VALUE 的函数 */
static void send_app_key_once(void)
{
    uint8_t buf[1 + 4];

    buf[0] = 'K';
    sys_put_le32(APP_KEY_VALUE, &buf[1]);

    if (!ble_link_send(buf, sizeof(buf))) {
        printk("[TX] APP_KEY enqueue failed\n");
    } else {
        /* 正常情况这里就安静地跑，不刷屏 */
        /* printk("[TX] APP_KEY queued (%u)\n", APP_KEY_VALUE); */
    }
}

/* 延时任务：每隔 APP_KEY_INTERVAL 通过 FIFO 发一次 APP_KEY 帧 */
static void app_key_work_handler(struct k_work *work)
{
    ARG_UNUSED(work);

    /* 没连上就不发，也不 reschedule，等下次 connected 再 schedule */
    if (!EN_BLE || !current_conn) {
        return;
    }

    send_app_key_once();
    k_work_schedule(&app_key_work, K_MSEC(APP_KEY_INTERVAL));
}

/* ====================== NUS FIFO 对外发送接口 ====================== */

bool ble_link_send(const uint8_t *data, uint16_t len)
{
    if (!EN_BLE) {
        return false;
    }

    if (!data) {
        g_ble_drop_count++;
        return false;
    }

    if (!current_conn) {
        g_ble_drop_count++;
        return false;
    }

    if (len == 0 || len > BLE_BUF_SIZE) {
        g_ble_drop_count++;
        return false;
    }

    void *block = NULL;
    int slab_err = k_mem_slab_alloc(&ble_tx_slab, &block, K_NO_WAIT);
    if (slab_err) {
        /* 内存池满了，丢包 */
        g_ble_drop_count++;
        return false;
    }

    struct ble_tx_item *item = (struct ble_tx_item *)block;
    item->len = len;
    memcpy(item->data, data, len);

    k_fifo_put(&ble_tx_fifo, item);
    return true;
}

/* ====================== NUS 发送后台线程 ====================== */

static K_THREAD_STACK_DEFINE(ble_thread_stack, BLE_STACK_SIZE);
static struct k_thread ble_thread_data;

static void ble_tx_thread(void *p1, void *p2, void *p3)
{
    ARG_UNUSED(p1);
    ARG_UNUSED(p2);
    ARG_UNUSED(p3);

    /* 等 BLE ready */
    k_sem_take(&ble_ready, K_FOREVER);

    while (1) {
        struct ble_tx_item *item =
            (struct ble_tx_item *)k_fifo_get(&ble_tx_fifo, K_FOREVER);

        if (item && current_conn) {
            int err = bt_nus_send(current_conn, item->data, item->len);
            if (err) {
                printk("[TX] bt_nus_send err=%d len=%u\n",
                       err, item->len);
            }
        } else {
            /* 未连接或空项，统计丢包 */
            if (item) {
                g_ble_drop_count++;
            }
        }

        if (item) {
            k_mem_slab_free(&ble_tx_slab, (void *)item);
        }
    }
}

/* ====================== 连接回调 & 广播 ====================== */

static void exchange_mtu_cb(struct bt_conn *conn, uint8_t err,
                            struct bt_gatt_exchange_params *params)
{
    ARG_UNUSED(conn);
    ARG_UNUSED(params);

    if (err) {
        printk("[TX] MTU exchange failed (err 0x%02x)\n", err);
    } else {
        printk("[TX] MTU exchange successful\n");
    }
}

static struct bt_gatt_exchange_params exch_mtu_params;

static void connected_cb(struct bt_conn *conn, uint8_t err)
{
    if (err) {
        printk("[TX] Connection failed (err 0x%02x)\n", err);
        return;
    }

    current_conn = bt_conn_ref(conn);
    dk_set_led_on(DK_LED2);

    exch_mtu_params.func = exchange_mtu_cb;
    bt_gatt_exchange_mtu(conn, &exch_mtu_params);

    /* conn interval = 50 ms, latency = 19, timeout = 10 s */
    const struct bt_le_conn_param *param = BT_LE_CONN_PARAM(40, 40, 19, 1000);
    bt_conn_le_param_update(conn, param);

    printk("[TX] Connected\n");

    /* 连接建立后启动 APP_KEY 周期发送 */
    k_work_schedule(&app_key_work, K_MSEC(APP_KEY_INTERVAL));
}

static void disconnected_cb(struct bt_conn *conn, uint8_t reason)
{
    printk("[TX] Disconnected (reason 0x%02x)\n", reason);

    if (current_conn) {
        bt_conn_unref(current_conn);
        current_conn = NULL;
    }

    dk_set_led_off(DK_LED2);

    /* 断线后重新开始广播 */
    const char *bt_name = device_bt_name_get();

    /* 沿用你原来的广播配置，只是重新启动 */
    const struct bt_data ad[] = {
        BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
        BT_DATA(BT_DATA_NAME_COMPLETE, bt_name, strlen(bt_name)),
    };
    const struct bt_data sd[] = {
        BT_DATA_BYTES(BT_DATA_UUID128_ALL, BT_UUID_NUS_VAL),
    };
    int err = bt_le_adv_start(BT_LE_ADV_CONN, ad, ARRAY_SIZE(ad),
                              sd, ARRAY_SIZE(sd));
    printk("[TX] Restart adv after disconnect: %d\n", err);
}

static struct bt_conn_cb conn_cb = {
    .connected    = connected_cb,
    .disconnected = disconnected_cb,
};

/* ======================= NUS RX（来自 RX） ======================= */

static void nus_recv_cb(struct bt_conn *conn,
                        const uint8_t *data, uint16_t len)
{
    ARG_UNUSED(conn);

    if (len == 0) {
        return;
    }

    /* 优先解析 TSYNC：AA 'T' + host_ms(u64, LE) */
    if (len == 10 && data[0] == 0xAA && data[1] == 'T') {
        uint64_t host_ms = 0;
        memcpy(&host_ms, &data[2], 8);

        uint64_t up = (uint64_t)k_uptime_get();
        g_host_time_offset_ms = (int64_t)host_ms - (int64_t)up;

        printk("[TX] TSYNC: host_ms=%llu up=%llu offset=%lld\n",
               (unsigned long long)host_ms,
               (unsigned long long)up,
               (long long)g_host_time_offset_ms);
        return;
    }

    /* 其他控制命令（例如以后扩展的 RSLOT 'R'），仍然透传给 IMU 层 */
    imu_handle_ctrl_from_ble(data, len);
}

static struct bt_nus_cb nus_cb = {
    .received = nus_recv_cb,
};

/* ====================== NUS TX 线程入口 ====================== */

static void ble_thread_start(void)
{
    k_thread_create(&ble_thread_data, ble_thread_stack,
                    K_THREAD_STACK_SIZEOF(ble_thread_stack),
                    ble_tx_thread,
                    NULL, NULL, NULL,
                    BLE_PRIO, 0, K_NO_WAIT);
}

/* ====================== 对外辅助接口实现 ====================== */

/* main.c 使用：BLE 发送丢包数 */
uint32_t ble_link_get_drop_count(void)
{
    return g_ble_drop_count;
}

/* imu_task.c / uwb_task.c 使用：返回 host 时间戳 ms
 * 头文件 ble_link.h 里是 uint32_t，这里保持一致
 */
uint32_t ble_link_now_host_ms(void)
{
    uint64_t up = (uint64_t)k_uptime_get();
    int64_t host_ms_64 = (int64_t)up + g_host_time_offset_ms;

    if (host_ms_64 < 0) {
        host_ms_64 = 0;
    }
    return (uint32_t)host_ms_64;
}

/* ====================== 对外初始化接口 ====================== */

int ble_link_init(void)
{
    if (!EN_BLE) {
        k_sem_init(&ble_ready, 0, 1);
        k_sem_give(&ble_ready);
        ble_thread_start();
        return 0;
    }

    int err = bt_enable(NULL);
    if (err) {
        printk("[TX] bt_enable error %d\n", err);
        return err;
    }

    /* 允许从 Flash 里加载历史 bonding / 配置（本次会被下面的 bt_unpair 清空） */
    settings_load();
    int unpair_err = bt_unpair(BT_ID_DEFAULT, NULL);
    printk("[TX] bt_unpair(all) -> %d\n", unpair_err);

    /* 注册连接回调（连上/断线 + 自动重启广播） */
    bt_conn_cb_register(&conn_cb);

    /* 初始化 NUS Service */
    err = bt_nus_init(&nus_cb);
    if (err) {
        printk("[TX] bt_nus_init %d\n", err);
        return err;
    }

    /* 设置广播名：eFxxxx（来自 device_id.c 的 device_bt_name_get） */
    const char *bt_name = device_bt_name_get();
    bt_set_name(bt_name);

    const struct bt_data ad[] = {
        BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
        BT_DATA(BT_DATA_NAME_COMPLETE, bt_name, strlen(bt_name)),
    };
    const struct bt_data sd[] = {
        BT_DATA_BYTES(BT_DATA_UUID128_ALL, BT_UUID_NUS_VAL),
    };

    err = bt_le_adv_start(BT_LE_ADV_CONN, ad, ARRAY_SIZE(ad),
                          sd, ARRAY_SIZE(sd));
    printk("[TX] ADV start ret=%d name=%s\n", err, bt_name);

    dk_leds_init();
    dk_set_led_on(DK_LED1);  /* 上电指示 */

    /* 初始化 APP_KEY 周期任务 */
    k_work_init_delayable(&app_key_work, app_key_work_handler);

    /* 通知 TX 线程可以开始工作（允许发包） */
    k_sem_init(&ble_ready, 0, 1);
    k_sem_give(&ble_ready);

    ble_thread_start();

    return 0;
}
