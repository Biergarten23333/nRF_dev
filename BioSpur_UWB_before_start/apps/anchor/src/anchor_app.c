#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <string.h>

#include "anchor_config.h"
#include "anchor_ble_id.h"
#include "anchor_ble_ctrl.h"
#include "uart_role_switch.h"
#include "ss_twr_anchor_init.h"
#include "ss_twr_resp.h"
#include "uwb_anchor_topology.h"
#include "uwb_bringup.h"

#ifndef APP_ANCHOR_ID
#define APP_ANCHOR_ID 4U
#endif

#ifndef APP_ANCHOR_ROLE
#define APP_ANCHOR_ROLE 0U
#endif

#ifndef APP_ANCHOR_MASTER
#define APP_ANCHOR_MASTER 0U
#endif

#ifndef APP_ANCHOR_PEER_COUNT
#define APP_ANCHOR_PEER_COUNT 3U
#endif

#ifndef APP_ANCHOR_PEER_0_ID
#define APP_ANCHOR_PEER_0_ID 5U
#endif

#ifndef APP_ANCHOR_PEER_1_ID
#define APP_ANCHOR_PEER_1_ID 6U
#endif

#ifndef APP_ANCHOR_PEER_2_ID
#define APP_ANCHOR_PEER_2_ID 7U
#endif

#ifndef APP_ANCHOR_USE_AUTO_SCHEDULE
#define APP_ANCHOR_USE_AUTO_SCHEDULE 1U
#endif

#ifndef APP_ANCHOR_SCHEDULE_MODE
#define APP_ANCHOR_SCHEDULE_MODE 1U
#endif

#ifndef APP_ANCHOR_ALLOW_TAG_POLLS
#define APP_ANCHOR_ALLOW_TAG_POLLS 1U
#endif

#ifndef APP_ANCHOR_BLE_ID_ENABLE
#define APP_ANCHOR_BLE_ID_ENABLE 1U
#endif

#ifndef APP_ANCHOR_SERIAL_CMD_ENABLE
#define APP_ANCHOR_SERIAL_CMD_ENABLE 1U
#endif

#ifndef APP_ANCHOR_SERIAL_CMD_BOOT_WINDOW_MS
#define APP_ANCHOR_SERIAL_CMD_BOOT_WINDOW_MS 5000U
#endif

static const char *anchor_role_name(uint8_t role)
{
    switch (role) {
    case ANCHOR_ROLE_MASTER:
        return "master";
    case ANCHOR_ROLE_MATRIX:
        return "matrix";
    case ANCHOR_ROLE_RESPONDER:
        return "responder";
    default:
        return "unset";
    }
}

static void bytes_to_hex(const uint8_t *src, size_t len, char *dst, size_t dst_len)
{
    static const char hex[] = "0123456789ABCDEF";
    size_t i;

    if (dst_len < (len * 2U + 1U)) {
        if (dst_len > 0U) {
            dst[0] = '\0';
        }
        return;
    }

    for (i = 0; i < len; i++) {
        dst[(i * 2U)] = hex[(src[i] >> 4) & 0x0FU];
        dst[(i * 2U) + 1U] = hex[src[i] & 0x0FU];
    }
    dst[len * 2U] = '\0';
}

int anchor_app_run(void)
{
    static const uint8_t anchor_peer_ids_static[] = {
        APP_ANCHOR_PEER_0_ID,
        APP_ANCHOR_PEER_1_ID,
        APP_ANCHOR_PEER_2_ID,
    };
    uint8_t anchor_peer_ids[UWB_MAX_ANCHORS];
    const uint8_t *anchor_peer_ids_ptr = anchor_peer_ids_static;
    size_t peer_count = APP_ANCHOR_PEER_COUNT;
    uint8_t effective_role = ANCHOR_ROLE_UNSET;
    bool unassigned_mode = false;
    uint8_t anchor_id_runtime = APP_ANCHOR_ID;
    uint8_t anchor_id_cfg = (uint8_t)(APP_ANCHOR_ID + 1U);
    uint8_t effective_master = APP_ANCHOR_MASTER;
    uint8_t effective_allow_tag_polls = APP_ANCHOR_ALLOW_TAG_POLLS;
    uint8_t mcu_uid[8];
    uint8_t device_uuid[16];
    anchor_config_t cfg = {0};
    bool cfg_valid = anchor_config_load(&cfg);
    struct uart_role_switch_runtime_info serial_info;
    char uuid_hex[33];
    char mcu_uid_hex[17];
    char bs_code[7];
    int ret = uwb_hw_bringup_and_init();
    if (ret) {
        return ret;
    }

    anchor_config_get_mcu_uid(mcu_uid);
    anchor_config_get_device_uuid(device_uuid, &cfg, cfg_valid);
    anchor_config_get_bs_code(device_uuid, bs_code);
    bytes_to_hex(device_uuid, sizeof(device_uuid), uuid_hex, sizeof(uuid_hex));
    bytes_to_hex(mcu_uid, sizeof(mcu_uid), mcu_uid_hex, sizeof(mcu_uid_hex));

    if (cfg_valid) {
        anchor_id_cfg = cfg.anchor_id;
        if (anchor_id_cfg >= 1U && anchor_id_cfg <= 8U) {
            anchor_id_runtime = (uint8_t)(anchor_id_cfg - 1U); /* config uses 1..8 => runtime 0..7 */
        } else {
            unassigned_mode = true;
        }
        effective_role = cfg.role;
    } else {
        effective_role = APP_ANCHOR_ROLE;
        anchor_id_cfg = (uint8_t)(APP_ANCHOR_ID + 1U);
        printk("anchor_config invalid/absent at 0x%08x, using build-time fallback\n",
               (unsigned int)APP_ANCHOR_CONFIG_ADDR);
    }

    if (effective_role == ANCHOR_ROLE_UNSET) {
        /* Role unset is build-time fallback for current Phase 2.1 */
        effective_master = (APP_ANCHOR_MASTER != 0U) ? 1U : 0U;
        effective_allow_tag_polls = (APP_ANCHOR_ALLOW_TAG_POLLS != 0U) ? 1U : 0U;
    } else if (effective_role == ANCHOR_ROLE_MASTER) {
        effective_master = 1U;
        effective_allow_tag_polls = 0U;
    } else if (effective_role == ANCHOR_ROLE_MATRIX) {
        effective_master = 0U;
        effective_allow_tag_polls = 0U;
    } else if (effective_role == ANCHOR_ROLE_RESPONDER) {
        effective_master = 0U;
        effective_allow_tag_polls = 1U;
    } else {
        printk("Invalid APP_ANCHOR_ROLE=%u\n", APP_ANCHOR_ROLE);
        return -1;
    }

    if (effective_role == ANCHOR_ROLE_UNSET || anchor_id_cfg == 0U) {
        unassigned_mode = true;
        effective_master = 0U;
        effective_allow_tag_polls = 0U;
    }

    printk("ANCHOR: unified; ANCHOR_ID: %c; ROLE: %s; BS_CODE: %s; DEVICE_UUID: %s; MCU_UID: %s\n",
           anchor_config_label_char(anchor_id_cfg), anchor_role_name(effective_role),
           bs_code, uuid_hex, mcu_uid_hex);
    printk("Anchor app ready anchor_id=%u role=%s master=%u allow_tag_polls=%u cfg_valid=%u\n",
           (unsigned int)anchor_id_runtime, anchor_role_name(effective_role),
           (unsigned int)effective_master, (unsigned int)effective_allow_tag_polls,
           (unsigned int)(cfg_valid ? 1U : 0U));

    if (APP_ANCHOR_BLE_ID_ENABLE != 0U) {
        ret = anchor_ble_id_start(anchor_id_cfg, effective_role, device_uuid, bs_code);
        if (ret) {
            printk("anchor_ble_id_start failed: %d\n", ret);
        }
    }

    ble_ctrl_info.active_cfg = cfg;
    if (ble_ctrl_info.active_cfg.schema_version == 0U) {
        ble_ctrl_info.active_cfg.schema_version = ANCHOR_CONFIG_SCHEMA_VERSION;
    }
    ble_ctrl_info.active_cfg_valid = cfg_valid;
    ble_ctrl_info.runtime_anchor_id_cfg = anchor_id_cfg;
    ble_ctrl_info.runtime_role = effective_role;
    memcpy(ble_ctrl_info.device_uuid, device_uuid, sizeof(device_uuid));
    memcpy(ble_ctrl_info.mcu_uid, mcu_uid, sizeof(mcu_uid));
    memcpy(ble_ctrl_info.bs_code, bs_code, sizeof(ble_ctrl_info.bs_code));
    ret = anchor_ble_ctrl_init(&ble_ctrl_info);
    if (ret != 0) {
        printk("anchor_ble_ctrl_init failed: %d\n", ret);
    }

    serial_info.active_anchor_id_cfg = anchor_id_cfg;
    serial_info.active_role = effective_role;
    memcpy(serial_info.device_uuid, device_uuid, sizeof(device_uuid));
    memcpy(serial_info.mcu_uid, mcu_uid, sizeof(mcu_uid));
    memcpy(serial_info.bs_code, bs_code, sizeof(serial_info.bs_code));
    serial_info.cfg_valid_on_boot = cfg_valid;

    if (APP_ANCHOR_SERIAL_CMD_ENABLE != 0U) {
        ret = uart_role_switch_init(&serial_info, &cfg, cfg_valid);
        if (ret != 0) {
            printk("uart_role_switch_init failed: %d\n", ret);
        } else if (APP_ANCHOR_SERIAL_CMD_BOOT_WINDOW_MS > 0U) {
            printk("serial config boot window: %u ms\n",
                   (unsigned int)APP_ANCHOR_SERIAL_CMD_BOOT_WINDOW_MS);
            k_msleep(APP_ANCHOR_SERIAL_CMD_BOOT_WINDOW_MS);
        }
    }

    uart_role_switch_set_ranging_active(!unassigned_mode);
    anchor_ble_ctrl_set_busy(!unassigned_mode);
    anchor_ble_ctrl_set_runtime(anchor_id_cfg, effective_role, cfg_valid);

    if (unassigned_mode) {
        printk("Anchor in unassigned/unset mode: control plane active, ranging not started\n");
        return 0;
    }

    if (effective_master != 0U) {
        if (APP_ANCHOR_USE_AUTO_SCHEDULE != 0U) {
            if (APP_ANCHOR_SCHEDULE_MODE == 2U) {
                peer_count = uwb_anchor_schedule_all_except_self(
                    anchor_id_runtime, anchor_peer_ids, sizeof(anchor_peer_ids));
            } else {
                peer_count = uwb_anchor_schedule_upper_triangle(
                    anchor_id_runtime, anchor_peer_ids, sizeof(anchor_peer_ids));
            }
            anchor_peer_ids_ptr = anchor_peer_ids;

            printk("Anchor master auto schedule %c mode=%u peer_count=%u\n",
                   uwb_anchor_label(anchor_id_runtime),
                   (unsigned int)APP_ANCHOR_SCHEDULE_MODE,
                   (unsigned int)peer_count);
        }

        ret = ss_twr_anchor_init_start(anchor_id_runtime, anchor_peer_ids_ptr,
                                       peer_count);
        if (ret) {
            printk("ss_twr_anchor_init_start failed: %d\n", ret);
            uart_role_switch_set_ranging_active(false);
            anchor_ble_ctrl_set_busy(false);
            return ret;
        }
        return 0;
    }

    ret = ss_twr_resp_start(anchor_id_runtime, effective_allow_tag_polls);
    if (ret) {
        printk("ss_twr_resp_start failed: %d\n", ret);
        uart_role_switch_set_ranging_active(false);
        anchor_ble_ctrl_set_busy(false);
        return ret;
    }

    return 0;
}
    struct anchor_ble_ctrl_boot_info ble_ctrl_info;
