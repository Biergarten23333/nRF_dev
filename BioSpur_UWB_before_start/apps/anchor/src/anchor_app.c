#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <errno.h>
#include <string.h>
#include <zephyr/dfu/mcuboot.h>

#include "anchor_config.h"
#include "anchor_ble_id.h"
#include "anchor_ble_ctrl.h"
#include "anchor_mcumgr_diag.h"
#include "anchor_runtime_control.h"
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
#define APP_ANCHOR_SCHEDULE_MODE 2U
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

#ifndef APP_ANCHOR_FW_MARKER
#define APP_ANCHOR_FW_MARKER "anchor-unified"
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

static uint8_t persistent_role_normalize(uint8_t role)
{
    if (role == ANCHOR_ROLE_MASTER) {
        return ANCHOR_ROLE_MATRIX;
    }
    return role;
}

static void anchor_normalize_persisted_role(anchor_config_t *cfg, bool *cfg_valid)
{
    anchor_config_t local;
    int rc;

    if (cfg == NULL || cfg_valid == NULL || !(*cfg_valid)) {
        return;
    }

    if (cfg->role != ANCHOR_ROLE_MASTER) {
        return;
    }

    local = *cfg;
    local.role = persistent_role_normalize(local.role);
    local.schema_version = ANCHOR_CONFIG_SCHEMA_VERSION;
    local.generation = cfg->generation + 1U;
    local.crc32 = anchor_config_crc32((const uint8_t *)&local,
                                      offsetof(anchor_config_t, crc32));

    if (!anchor_config_is_valid(&local)) {
        printk("Persisted role normalize failed validation; keeping runtime fallback matrix only\n");
        cfg->role = ANCHOR_ROLE_MATRIX;
        return;
    }

    rc = anchor_config_write(&local);
    if (rc != 0) {
        printk("Persisted role normalize write failed rc=%d; keeping runtime fallback matrix only\n", rc);
        cfg->role = ANCHOR_ROLE_MATRIX;
        return;
    }

    *cfg = local;
    printk("Persisted master role normalized to matrix on boot gen=%lu\n",
           (unsigned long)local.generation);
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

static int anchor_role_runtime_flags(uint8_t role, uint8_t *out_master,
                                     uint8_t *out_allow_tag_polls)
{
    if (out_master == NULL || out_allow_tag_polls == NULL) {
        return -EINVAL;
    }

    switch (role) {
    case ANCHOR_ROLE_MASTER:
        *out_master = 1U;
        *out_allow_tag_polls = 0U;
        return 0;
    case ANCHOR_ROLE_MATRIX:
        *out_master = 0U;
        *out_allow_tag_polls = 0U;
        return 0;
    case ANCHOR_ROLE_RESPONDER:
        *out_master = 0U;
        *out_allow_tag_polls = 1U;
        return 0;
    default:
        return -EINVAL;
    }
}

static int anchor_run_runtime_role(uint8_t anchor_id_runtime, uint8_t anchor_id_cfg,
                                   uint8_t role, bool cfg_valid,
                                   uint32_t master_sweep_limit)
{
    uint8_t anchor_peer_ids[UWB_MAX_ANCHORS];
    const uint8_t *anchor_peer_ids_ptr = NULL;
    size_t peer_count = 0U;
    uint8_t effective_master = 0U;
    uint8_t effective_allow_tag_polls = 0U;
    int ret;

    ret = anchor_role_runtime_flags(role, &effective_master, &effective_allow_tag_polls);
    if (ret != 0) {
        printk("Invalid runtime role=%u\n", (unsigned int)role);
        return ret;
    }

    anchor_ble_ctrl_set_runtime_switch_pending(false);
    uart_role_switch_set_runtime(anchor_id_cfg, role, cfg_valid);
    anchor_ble_ctrl_set_runtime(anchor_id_cfg, role, cfg_valid);
    uart_role_switch_set_ranging_active(true);
    anchor_ble_ctrl_set_busy(true);
    anchor_runtime_clear_stop();

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

        ret = ss_twr_anchor_init_start(anchor_id_runtime, anchor_peer_ids_ptr, peer_count,
                                       master_sweep_limit);
        uart_role_switch_set_ranging_active(false);
        anchor_ble_ctrl_set_busy(false);
        if (ret != 0) {
            printk("ss_twr_anchor_init_start failed: %d\n", ret);
            return ret;
        }
        if (master_sweep_limit != 0U &&
            anchor_runtime_requested_role() == ANCHOR_ROLE_UNSET) {
            printk("Anchor master finite sweep auto-return to matrix after %lu sets\n",
                   (unsigned long)master_sweep_limit);
            anchor_runtime_request_role_switch(ANCHOR_ROLE_MATRIX, 0U);
        }
        printk("Anchor master ranging stopped; control plane remains active\n");
        return 0;
    }

    ret = ss_twr_resp_start(anchor_id_runtime, effective_allow_tag_polls);
    uart_role_switch_set_ranging_active(false);
    anchor_ble_ctrl_set_busy(false);
    if (ret != 0) {
        printk("ss_twr_resp_start failed: %d\n", ret);
        return ret;
    }
    printk("Anchor responder ranging stopped; control plane remains active\n");
    return 0;
}

int anchor_app_run(void)
{
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
    struct anchor_ble_ctrl_boot_info ble_ctrl_info;
    struct uart_role_switch_runtime_info serial_info;
    char uuid_hex[33];
    char mcu_uid_hex[17];
    char bs_code[7];
    int ret = uwb_hw_bringup_and_init();
    uint32_t effective_master_sweep_limit = 0U;
    if (ret) {
        return ret;
    }

    anchor_normalize_persisted_role(&cfg, &cfg_valid);

#if defined(CONFIG_BOOTLOADER_MCUBOOT)
    if (!boot_is_img_confirmed()) {
        ret = boot_write_img_confirmed();
        printk("MCUboot confirm rc=%d\n", ret);
        if (ret) {
            printk("MCUboot confirm failed, continuing: %d\n", ret);
        }
    }
#endif

    anchor_config_get_mcu_uid(mcu_uid);
    anchor_config_get_device_uuid(device_uuid, &cfg, cfg_valid);
    anchor_config_get_bs_code(device_uuid, bs_code);
    bytes_to_hex(device_uuid, sizeof(device_uuid), uuid_hex, sizeof(uuid_hex));
    bytes_to_hex(mcu_uid, sizeof(mcu_uid), mcu_uid_hex, sizeof(mcu_uid_hex));

    if (cfg_valid) {
        anchor_id_cfg = cfg.anchor_id;
        if (anchor_id_cfg >= 1U && anchor_id_cfg <= 8U) {
            anchor_id_runtime = (uint8_t)(anchor_id_cfg - 1U);
        } else {
            unassigned_mode = true;
        }
        if (cfg.role != ANCHOR_ROLE_MATRIX) {
            printk("Boot role %s ignored; defaulting to matrix autopos standby\n",
                   anchor_role_name(cfg.role));
        }
        cfg.role = ANCHOR_ROLE_MATRIX;
        effective_role = ANCHOR_ROLE_MATRIX;
    } else {
        effective_role = APP_ANCHOR_ROLE;
        anchor_id_cfg = (uint8_t)(APP_ANCHOR_ID + 1U);
        printk("anchor_config invalid/absent at 0x%08x, using build-time fallback\n",
               (unsigned int)APP_ANCHOR_CONFIG_ADDR);
    }

    if (effective_role == ANCHOR_ROLE_UNSET) {
        effective_master = (APP_ANCHOR_MASTER != 0U) ? 1U : 0U;
        effective_allow_tag_polls = (APP_ANCHOR_ALLOW_TAG_POLLS != 0U) ? 1U : 0U;
    } else {
        ret = anchor_role_runtime_flags(effective_role, &effective_master,
                                        &effective_allow_tag_polls);
        if (ret != 0) {
            printk("Invalid APP_ANCHOR_ROLE=%u\n", APP_ANCHOR_ROLE);
            return -1;
        }
    }

    if (effective_role == ANCHOR_ROLE_UNSET || anchor_id_cfg == 0U) {
        unassigned_mode = true;
        effective_master = 0U;
        effective_allow_tag_polls = 0U;
    }

    printk("ANCHOR: unified; FW: %s; ANCHOR_ID: %c; ROLE: %s; BS_CODE: %s; DEVICE_UUID: %s; MCU_UID: %s\n",
           APP_ANCHOR_FW_MARKER,
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

    memset(&ble_ctrl_info, 0, sizeof(ble_ctrl_info));
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
    (void)snprintk(ble_ctrl_info.fw_marker, sizeof(ble_ctrl_info.fw_marker), "%s",
                   APP_ANCHOR_FW_MARKER);
    ret = anchor_ble_ctrl_init(&ble_ctrl_info);
    if (ret != 0) {
        printk("anchor_ble_ctrl_init failed: %d\n", ret);
    }

    ret = anchor_mcumgr_diag_init();
    if (ret != 0 && ret != -ENOTSUP) {
        printk("anchor_mcumgr_diag_init failed: %d\n", ret);
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

    uart_role_switch_set_ranging_active(false);
    anchor_ble_ctrl_set_busy(false);
    anchor_ble_ctrl_set_runtime(anchor_id_cfg, effective_role, cfg_valid);
    anchor_ble_ctrl_set_runtime_switch_pending(false);
    uart_role_switch_set_runtime(anchor_id_cfg, effective_role, cfg_valid);
    ret = anchor_ble_id_update_role(effective_role);
    if (ret != 0 && ret != -EAGAIN) {
        printk("anchor_ble_id_update_role failed: %d\n", ret);
    }

    if (unassigned_mode) {
        printk("Anchor in unassigned/unset mode: control plane active, ranging not started\n");
        return 0;
    }

    while (1) {
        uint8_t next_role;

        ret = anchor_run_runtime_role(anchor_id_runtime, anchor_id_cfg, effective_role,
                                      cfg_valid, effective_master_sweep_limit);
        if (ret != 0) {
            return ret;
        }

        next_role = anchor_runtime_requested_role();
        if (next_role == ANCHOR_ROLE_UNSET) {
            printk("Anchor runtime role %s stopped without new role; restarting same role\n",
                   anchor_role_name(effective_role));
            k_msleep(50);
            continue;
        }

        effective_master_sweep_limit = anchor_runtime_requested_master_sweeps();
        anchor_runtime_clear_role_switch();
        effective_role = next_role;
        ret = anchor_role_runtime_flags(effective_role, &effective_master,
                                        &effective_allow_tag_polls);
        if (ret != 0) {
            printk("Runtime role switch invalid role=%u\n", (unsigned int)effective_role);
            return ret;
        }
        printk("Anchor runtime role switch applied: anchor=%c role=%s\n",
               anchor_config_label_char(anchor_id_cfg), anchor_role_name(effective_role));
        ret = anchor_ble_id_update_role(effective_role);
        if (ret != 0 && ret != -EAGAIN) {
            printk("anchor_ble_id_update_role failed after runtime switch: %d\n", ret);
        }
    }
}
