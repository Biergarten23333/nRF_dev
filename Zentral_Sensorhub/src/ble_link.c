#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <bluetooth/services/nus.h>
#include <zephyr/settings/settings.h>
#include <dk_buttons_and_leds.h>
#include <string.h>

#include "app_config.h"
#include "device_id.h"
#include "ble_link.h"

/* ========================= BLE TX path =========================
 * Dual-slab: high-priority for control/status (H/Q), normal for data (I/U/R).
 * A single FIFO carries mixed items; each item remembers its source slab.
 */

enum ble_src_lane { SRC_NORM = 0, SRC_HI = 1 };

struct ble_item_t {
    void *fifo_reserved;
    uint16_t len;
    uint8_t  src;                 /* 0=norm, 1=hi */
    uint8_t  data[BLE_BUF_SIZE];
};

static struct bt_conn *current_conn;
static struct bt_gatt_exchange_params exch_mtu_params;
static K_FIFO_DEFINE(fifo_ble);
static K_SEM_DEFINE(ble_ready, 0, 1);

/* Two slabs: most frames use norm; BPM/SigQ use hi. Tune counts per RAM. */
K_MEM_SLAB_DEFINE(ble_slab_norm, sizeof(struct ble_item_t), 144, 4);
K_MEM_SLAB_DEFINE(ble_slab_hi,   sizeof(struct ble_item_t),  16,  4);

/* NOT static: 导出给 main 做统计 */
atomic_t ble_drops_norm = ATOMIC_INIT(0);
atomic_t ble_drops_hi   = ATOMIC_INIT(0);

/* 内部 forward decl */
static inline bool enqueue_frame_ex(const uint8_t *buf, uint16_t len, bool high_prio);

/* ===== Enqueue helpers ===== */
static inline bool enqueue_frame_ex(const uint8_t *buf, uint16_t len, bool high_prio)
{
    if (!EN_BLE || len == 0 || len > BLE_BUF_SIZE) return false;

    struct ble_item_t *it = NULL;
    int ret;
    if (high_prio) {
        /* High priority lane allows a short wait to avoid drop */
        ret = k_mem_slab_alloc(&ble_slab_hi, (void **)&it, K_MSEC(5));
        if (ret != 0) {
            atomic_inc(&ble_drops_hi);
            return false;
        }
        it->src = SRC_HI;
    } else {
        /* Normal lane: best-effort, no wait to keep latency bounded */
        ret = k_mem_slab_alloc(&ble_slab_norm, (void **)&it, K_NO_WAIT);
        if (ret != 0) {
            atomic_inc(&ble_drops_norm);
            return false;
        }
        it->src = SRC_NORM;
    }

    it->len = len;
    memcpy(it->data, buf, len);
    k_fifo_put(&fifo_ble, it);
    return true;
}

bool ble_enqueue_frame_norm(const uint8_t *buf, uint16_t len)
{
    return enqueue_frame_ex(buf, len, false);
}

bool ble_enqueue_frame_hi(const uint8_t *buf, uint16_t len)
{
    return enqueue_frame_ex(buf, len, true);
}

/* ========================= BLE base ========================= */

static void exchange_mtu_cb(struct bt_conn *conn, uint8_t err,
                            struct bt_gatt_exchange_params *params)
{
    ARG_UNUSED(params);
    if (!err) {
        LOGF("MTU=%d\n", bt_gatt_get_mtu(conn));
    }
}

static void connected_cb(struct bt_conn *conn, uint8_t err)
{
    if (err) {
        LOGF("Conn fail 0x%02x\n", err);
        return;
    }
    current_conn = bt_conn_ref(conn);
    dk_set_led_on(DK_LED2);
    exch_mtu_params.func = exchange_mtu_cb;
    (void)bt_gatt_exchange_mtu(current_conn, &exch_mtu_params);
}

static void disconnected_cb(struct bt_conn *conn, uint8_t reason)
{
    ARG_UNUSED(conn);
    ARG_UNUSED(reason);
    if (current_conn) {
        bt_conn_unref(current_conn);
        current_conn = NULL;
    }
    dk_set_led_off(DK_LED2);
}

BT_CONN_CB_DEFINE(conn_cb) = {
    .connected    = connected_cb,
    .disconnected = disconnected_cb,
};

int ble_init(void)
{
    if (!EN_BLE) {
        k_sem_give(&ble_ready);
        return 0;
    }

    int err = bt_enable(NULL);
    if (err) {
        LOGF("bt_enable %d\n", err);
        return err;
    }
    settings_load();
    bt_nus_init(NULL);

    const struct bt_data ad[] = {
        BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
        BT_DATA(BT_DATA_NAME_COMPLETE, bt_name, strlen(bt_name)),
    };
    const struct bt_data sd[] = {
        BT_DATA_BYTES(BT_DATA_UUID128_ALL, BT_UUID_NUS_VAL),
    };

    err = bt_le_adv_start(BT_LE_ADV_CONN, ad, ARRAY_SIZE(ad), sd, ARRAY_SIZE(sd));
    if (err) {
        LOGF("adv start %d\n", err);
        return err;
    }

    dk_leds_init();
    dk_set_led_on(DK_LED1);
    k_sem_give(&ble_ready);
    return 0;
}

/* ========================= TX thread (retry on transient errors) ========================= */
static void ble_tx_thread(void)
{
    k_sem_take(&ble_ready, K_FOREVER);

    while (1) {
        struct ble_item_t *it = k_fifo_get(&fifo_ble, K_FOREVER);

        if (EN_BLE && current_conn) {
            int tries = 0;
            int err;
            do {
                err = bt_nus_send(current_conn, it->data, it->len);
                if (err == 0) break;

#if DEBUG_LOG_ENABLE
                LOGF("[BLE TX] nus_send err=%d (len=%u, src=%u)\n",
                     err, it->len, it->src);
#endif
                if (err == -ENOMEM || err == -EAGAIN) {
                    /* ATT TX buffer full; small backoff then retry */
                    k_sleep(K_MSEC(2));
                } else {
                    /* Permanent error, drop */
                    break;
                }
            } while (++tries < 3);
        }

        /* Return to the correct slab based on src */
        if (it->src == SRC_HI) {
            k_mem_slab_free(&ble_slab_hi, (void *)it);
        } else {
            k_mem_slab_free(&ble_slab_norm, (void *)it);
        }
    }
}

K_THREAD_DEFINE(ble_tx_tid, BLE_STACK_SIZE,
                ble_tx_thread, NULL, NULL, NULL,
                BLE_PRIO, 0, 0);
