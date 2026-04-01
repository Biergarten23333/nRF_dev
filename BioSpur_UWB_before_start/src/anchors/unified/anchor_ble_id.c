#include "anchor_ble_id.h"

#include <stddef.h>
#include <string.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/sys/printk.h>

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

    err = bt_le_adv_start(BT_LE_ADV_CONN, g_ad, ARRAY_SIZE(g_ad),
                          g_sd, ARRAY_SIZE(g_sd));
    if (err) {
        printk("anchor BLE id adv start failed: %d\n", err);
        return err;
    }

    printk("anchor BLE id adv started anchor=%c role=%u bs_code=%s\n",
           label, (unsigned int)role,
           (bs_code != NULL) ? bs_code : "N/A");
    return 0;
}
