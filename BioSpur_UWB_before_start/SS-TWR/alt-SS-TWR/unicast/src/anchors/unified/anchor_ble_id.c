#include "anchor_ble_id.h"

#include <errno.h>
#include <stddef.h>
#include <string.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/sys/printk.h>

#include "anchor_config.h"

#ifndef APP_ANCHOR_RESPONDER_ADV_INT_MIN_MS
#define APP_ANCHOR_RESPONDER_ADV_INT_MIN_MS 800U
#endif

#ifndef APP_ANCHOR_RESPONDER_ADV_INT_MAX_MS
#define APP_ANCHOR_RESPONDER_ADV_INT_MAX_MS 1200U
#endif

#define ADV_MS_TO_UNITS(ms) ((uint16_t)(((uint32_t)(ms) * 1000U) / 625U))

/* Manufacturer data layout:
 * [0..1]  company id (0xFFFF placeholder)
 * [2..4]  magic 'B''S''A'
 * [5]     schema version
 * [6..21] stable device_uuid[16]
 * [22]    anchor_id_cfg (0=unassigned, 1..8=A..H)
 * [23]    role (0..3)
 */
static uint8_t g_mfg_payload[24] = {
    0xFF, 0xFF, 'B', 'S', 'A', 0x01,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0
};
static uint8_t g_adv_flags = (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR);

static uint8_t g_name_buf[20];
static struct bt_data g_ad[] = {
    BT_DATA(BT_DATA_FLAGS, &g_adv_flags, sizeof(g_adv_flags)),
    BT_DATA(BT_DATA_MANUFACTURER_DATA, g_mfg_payload, sizeof(g_mfg_payload)),
};

static struct bt_data g_sd[] = {
    BT_DATA(BT_DATA_NAME_COMPLETE, g_name_buf, 0),
};
static bool g_adv_started;
static bool g_adv_responder_low_noise;

static const struct bt_le_adv_param g_adv_param_normal =
    BT_LE_ADV_PARAM_INIT(BT_LE_ADV_OPT_CONNECTABLE,
                         BT_GAP_ADV_FAST_INT_MIN_2,
                         BT_GAP_ADV_FAST_INT_MAX_2,
                         NULL);
static const struct bt_le_adv_param g_adv_param_responder =
    BT_LE_ADV_PARAM_INIT(BT_LE_ADV_OPT_CONNECTABLE,
                         ADV_MS_TO_UNITS(APP_ANCHOR_RESPONDER_ADV_INT_MIN_MS),
                         ADV_MS_TO_UNITS(APP_ANCHOR_RESPONDER_ADV_INT_MAX_MS),
                         NULL);

static const struct bt_le_adv_param *anchor_ble_id_adv_param_for_role(uint8_t role)
{
    if (role == ANCHOR_ROLE_RESPONDER) {
        return &g_adv_param_responder;
    }

    return &g_adv_param_normal;
}

static int anchor_ble_id_start_adv_for_role(uint8_t role)
{
    int err;

    err = bt_le_adv_start(anchor_ble_id_adv_param_for_role(role),
                          g_ad, ARRAY_SIZE(g_ad), g_sd, ARRAY_SIZE(g_sd));
    if (err == 0) {
        g_adv_started = true;
        g_adv_responder_low_noise = (role == ANCHOR_ROLE_RESPONDER);
    }

    return err;
}

int anchor_ble_id_start(uint8_t anchor_id_cfg, uint8_t role,
                        const uint8_t device_uuid[16],
                        const char *bs_code)
{
    int err;
    int name_len;
    char label = 'U';

    memcpy(&g_mfg_payload[6], device_uuid, 16);
    g_mfg_payload[22] = anchor_id_cfg;
    g_mfg_payload[23] = role;
    if (anchor_id_cfg >= 1U && anchor_id_cfg <= 8U) {
        label = (char)('A' + (anchor_id_cfg - 1U));
    }

    if (bs_code != NULL && bs_code[0] != '\0') {
        name_len = snprintk((char *)g_name_buf, sizeof(g_name_buf), "ANCHOR-%c-%s",
                            label, bs_code);
    } else {
        name_len = snprintk((char *)g_name_buf, sizeof(g_name_buf), "ANCHOR-%c",
                            label);
    }
    if (name_len < 0) {
        return -1;
    }
    g_sd[0].data_len = (uint8_t)name_len;

    err = bt_enable(NULL);
    if (err) {
        printk("anchor BLE id bt_enable failed: %d\n", err);
        return err;
    }

    err = anchor_ble_id_start_adv_for_role(role);
    if (err) {
        printk("anchor BLE id adv start failed: %d\n", err);
        return err;
    }

    printk("anchor BLE id adv started anchor=%c role=%u bs_code=%s int=%u-%u ms\n",
           label, (unsigned int)role,
           (bs_code != NULL) ? bs_code : "N/A",
           (role == ANCHOR_ROLE_RESPONDER) ?
               (unsigned int)APP_ANCHOR_RESPONDER_ADV_INT_MIN_MS : 30U,
           (role == ANCHOR_ROLE_RESPONDER) ?
               (unsigned int)APP_ANCHOR_RESPONDER_ADV_INT_MAX_MS : 60U);
    return 0;
}

int anchor_ble_id_update_role(uint8_t role)
{
    int err;

    if (!g_adv_started) {
        return -EAGAIN;
    }

    g_mfg_payload[23] = role;
    err = bt_le_adv_update_data(g_ad, ARRAY_SIZE(g_ad), g_sd, ARRAY_SIZE(g_sd));
    if (err) {
        printk("anchor BLE id adv role update failed: %d\n", err);
        return err;
    }

    if (g_adv_responder_low_noise != (role == ANCHOR_ROLE_RESPONDER)) {
        err = bt_le_adv_stop();
        if (err == 0) {
            g_adv_started = false;
            err = anchor_ble_id_start_adv_for_role(role);
            if (err) {
                printk("anchor BLE id adv restart failed role=%u err=%d\n",
                       (unsigned int)role, err);
                return err;
            }
        } else {
            /*
             * If a central is connected, legacy advertising may not be active.
             * Keep the manufacturer role update; advertising will use the right
             * interval after the next boot/start path.
             */
            printk("anchor BLE id adv interval change deferred role=%u err=%d\n",
                   (unsigned int)role, err);
        }
    }

    printk("anchor BLE id adv role updated role=%u low_noise=%u\n",
           (unsigned int)role, (unsigned int)(role == ANCHOR_ROLE_RESPONDER));
    return 0;
}
