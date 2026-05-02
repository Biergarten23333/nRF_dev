#include "anchor_ble_ctrl.h"
#include "anchor_runtime_control.h"

#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/reboot.h>

#define BLE_CTRL_MAX_TEXT 256
#define BLE_CTRL_MAX_CMD 128

static struct anchor_ble_ctrl_boot_info g_info;
static anchor_config_t g_pending_cfg;
static bool g_pending_valid;
static bool g_busy;
static bool g_reboot_required;
static bool g_runtime_switch_pending;
static struct k_mutex g_lock;

static char g_state_text[BLE_CTRL_MAX_TEXT];
static char g_active_text[BLE_CTRL_MAX_TEXT];
static char g_pending_text[BLE_CTRL_MAX_TEXT];
static char g_result_text[BLE_CTRL_MAX_TEXT];

static struct bt_conn *g_last_conn;
static bool g_result_notify_enabled;
static bool g_state_notify_enabled;

#define ANCHOR_BLE_CTRL_STATE_ATTR_INDEX 2U
#define ANCHOR_BLE_CTRL_RESULT_ATTR_INDEX 11U

/* 2f2b8f40-84e0-4be6-b6bf-2fd95f39d3f0 */
static struct bt_uuid_128 g_svc_uuid =
    BT_UUID_INIT_128(0xf0, 0xd3, 0x39, 0x5f, 0xd9, 0x2f, 0xbf, 0xb6,
                     0xe6, 0x4b, 0xe0, 0x84, 0x40, 0x8f, 0x2b, 0x2f);
/* ...f1 state, ...f2 active, ...f3 pending, ...f4 control, ...f5 result */
static struct bt_uuid_128 g_state_uuid =
    BT_UUID_INIT_128(0xf1, 0xd3, 0x39, 0x5f, 0xd9, 0x2f, 0xbf, 0xb6,
                     0xe6, 0x4b, 0xe0, 0x84, 0x40, 0x8f, 0x2b, 0x2f);
static struct bt_uuid_128 g_active_uuid =
    BT_UUID_INIT_128(0xf2, 0xd3, 0x39, 0x5f, 0xd9, 0x2f, 0xbf, 0xb6,
                     0xe6, 0x4b, 0xe0, 0x84, 0x40, 0x8f, 0x2b, 0x2f);
static struct bt_uuid_128 g_pending_uuid =
    BT_UUID_INIT_128(0xf3, 0xd3, 0x39, 0x5f, 0xd9, 0x2f, 0xbf, 0xb6,
                     0xe6, 0x4b, 0xe0, 0x84, 0x40, 0x8f, 0x2b, 0x2f);
static struct bt_uuid_128 g_control_uuid =
    BT_UUID_INIT_128(0xf4, 0xd3, 0x39, 0x5f, 0xd9, 0x2f, 0xbf, 0xb6,
                     0xe6, 0x4b, 0xe0, 0x84, 0x40, 0x8f, 0x2b, 0x2f);
static struct bt_uuid_128 g_result_uuid =
    BT_UUID_INIT_128(0xf5, 0xd3, 0x39, 0x5f, 0xd9, 0x2f, 0xbf, 0xb6,
                     0xe6, 0x4b, 0xe0, 0x84, 0x40, 0x8f, 0x2b, 0x2f);

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

static const char *role_name(uint8_t role)
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

static int role_parse(const char *tok, uint8_t *out_role)
{
    if (tok == NULL || out_role == NULL) {
        return -EINVAL;
    }
    if (strcmp(tok, "MASTER") == 0 || strcmp(tok, "M") == 0) {
        *out_role = ANCHOR_ROLE_MASTER;
        return 0;
    }
    if (strcmp(tok, "MATRIX") == 0 || strcmp(tok, "X") == 0) {
        *out_role = ANCHOR_ROLE_MATRIX;
        return 0;
    }
    if (strcmp(tok, "RESPONDER") == 0 || strcmp(tok, "P") == 0) {
        *out_role = ANCHOR_ROLE_RESPONDER;
        return 0;
    }
    if (strcmp(tok, "UNSET") == 0 || strcmp(tok, "U") == 0) {
        *out_role = ANCHOR_ROLE_UNSET;
        return 0;
    }
    return -EINVAL;
}

static int label_parse(const char *tok, uint8_t *out_label)
{
    char c;

    if (tok == NULL || out_label == NULL) {
        return -EINVAL;
    }
    if (strcmp(tok, "UNASSIGNED") == 0 || strcmp(tok, "U") == 0 || strcmp(tok, "0") == 0) {
        *out_label = 0U;
        return 0;
    }
    if (strlen(tok) != 1U) {
        return -EINVAL;
    }
    c = (char)toupper((unsigned char)tok[0]);
    if (c < 'A' || c > 'H') {
        return -EINVAL;
    }
    *out_label = (uint8_t)((c - 'A') + 1U);
    return 0;
}

static void cfg_prepare(anchor_config_t *cfg)
{
    cfg->magic = CONFIG_MAGIC;
    if (cfg->schema_version == 0U || cfg->schema_version > ANCHOR_CONFIG_SCHEMA_VERSION) {
        cfg->schema_version = ANCHOR_CONFIG_SCHEMA_VERSION;
    }
    cfg->crc32 = anchor_config_crc32((const uint8_t *)cfg, offsetof(anchor_config_t, crc32));
}

static bool cfg_validate_local(anchor_config_t *cfg)
{
    if (cfg == NULL) {
        return false;
    }
    cfg_prepare(cfg);
    return anchor_config_is_valid(cfg);
}

static void format_cfg_text(char *out, size_t out_len, const anchor_config_t *cfg, bool valid)
{
    char uuid_hex[33];
    const char *role = "unset";
    char label = 'U';

    if (cfg != NULL) {
        bytes_to_hex(cfg->device_uuid, sizeof(cfg->device_uuid), uuid_hex, sizeof(uuid_hex));
        role = role_name(cfg->role);
        label = anchor_config_label_char(cfg->anchor_id);
        snprintk(out, out_len,
                 "CFG schema=%u gen=%lu label=%c role=%s valid=%u flags=%u uuid=%s",
                 (unsigned int)cfg->schema_version,
                 (unsigned long)cfg->generation,
                 label,
                 role,
                 valid ? 1U : 0U,
                 (unsigned int)cfg->flags,
                 uuid_hex);
        return;
    }

    snprintk(out, out_len, "CFG schema=0 gen=0 label=U role=unset valid=0 flags=0 uuid=");
}

static void refresh_text_locked(void)
{
    char uuid_hex[33];

    bytes_to_hex(g_info.device_uuid, sizeof(g_info.device_uuid), uuid_hex, sizeof(uuid_hex));
    format_cfg_text(g_active_text, sizeof(g_active_text), &g_info.active_cfg, g_info.active_cfg_valid);
    format_cfg_text(g_pending_text, sizeof(g_pending_text), &g_pending_cfg, g_pending_valid);
    snprintk(g_state_text, sizeof(g_state_text),
             "STATE fw=%s bs=%s uuid=%s label=%c role=%s cfg_valid=%u busy=%u pending_valid=%u schema=%u gen=%lu reboot_required=%u",
             g_info.fw_marker,
             g_info.bs_code,
             uuid_hex,
             anchor_config_label_char(g_info.runtime_anchor_id_cfg),
             role_name(g_info.runtime_role),
             g_info.active_cfg_valid ? 1U : 0U,
             g_busy ? 1U : 0U,
             g_pending_valid ? 1U : 0U,
             (unsigned int)g_info.active_cfg.schema_version,
             (unsigned long)g_info.active_cfg.generation,
             g_reboot_required ? 1U : 0U);
    if (g_runtime_switch_pending) {
        (void)snprintk(&g_state_text[strlen(g_state_text)],
                       sizeof(g_state_text) - strlen(g_state_text),
                       " runtime_switch_pending=1");
    }
}

static void set_result_locked(const char *text)
{
    snprintk(g_result_text, sizeof(g_result_text), "%s", text);
}

static ssize_t read_text_cb(struct bt_conn *conn, const struct bt_gatt_attr *attr, void *buf,
                            uint16_t len, uint16_t offset)
{
    const char *src = attr->user_data;
    size_t src_len = strlen(src);
    ARG_UNUSED(conn);
    return bt_gatt_attr_read(conn, attr, buf, len, offset, src, src_len);
}

static void notify_result_if_possible(void);

static uint8_t persistent_role_normalize(uint8_t role)
{
    if (role == ANCHOR_ROLE_MASTER) {
        return ANCHOR_ROLE_MATRIX;
    }
    return role;
}

static void state_ccc_cfg_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
    ARG_UNUSED(attr);
    g_state_notify_enabled = (value == BT_GATT_CCC_NOTIFY);
}

static void result_ccc_cfg_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
    ARG_UNUSED(attr);
    g_result_notify_enabled = (value == BT_GATT_CCC_NOTIFY);
}

static void handle_validate_locked(void)
{
    anchor_config_t local = g_pending_cfg;
    bool ok = cfg_validate_local(&local);
    g_pending_cfg = local;
    g_pending_valid = ok;
    if (ok) {
        set_result_locked("OK VALID");
    } else {
        set_result_locked("ERR:INVALID_CONFIG");
    }
}

static void handle_commit_locked(void)
{
    anchor_config_t local;
    int rc;

    if (g_busy) {
        set_result_locked("ERR:BUSY");
        return;
    }
    local = g_pending_cfg;
    local.role = persistent_role_normalize(local.role);
    local.schema_version = ANCHOR_CONFIG_SCHEMA_VERSION;
    local.generation = g_info.active_cfg.generation + 1U;
    cfg_prepare(&local);
    if (!anchor_config_is_valid(&local)) {
        set_result_locked("ERR:INVALID_CONFIG");
        return;
    }

    rc = anchor_config_write(&local);
    if (rc == -EILSEQ) {
        set_result_locked("ERR:CRC_MISMATCH");
        return;
    }
    if (rc != 0) {
        set_result_locked("ERR:WRITE_FAIL");
        return;
    }

    g_info.active_cfg = local;
    g_info.active_cfg_valid = true;
    g_pending_cfg = local;
    g_pending_valid = true;
    g_reboot_required = true;
    set_result_locked("OK COMMIT REBOOT_REQUIRED");
}

static void handle_reboot_locked(void)
{
    set_result_locked("OK REBOOT");
    g_reboot_required = false;
}

static void handle_reset_role_locked(uint8_t role, const char *result_text)
{
    anchor_config_t local;
    int rc;

    local = g_info.active_cfg_valid ? g_info.active_cfg : g_pending_cfg;
    local.role = persistent_role_normalize(role);
    local.schema_version = ANCHOR_CONFIG_SCHEMA_VERSION;
    local.generation = g_info.active_cfg.generation + 1U;
    cfg_prepare(&local);
    if (!anchor_config_is_valid(&local)) {
        set_result_locked("ERR:INVALID_CONFIG");
        return;
    }

    rc = anchor_config_write(&local);
    if (rc == -EILSEQ) {
        set_result_locked("ERR:CRC_MISMATCH");
        return;
    }
    if (rc != 0) {
        set_result_locked("ERR:WRITE_FAIL");
        return;
    }

    g_info.active_cfg = local;
    g_info.active_cfg_valid = true;
    g_pending_cfg = local;
    g_pending_valid = true;
    g_info.runtime_role = local.role;
    g_reboot_required = false;
    set_result_locked(result_text);
}

static void handle_runtime_role_locked(uint8_t role, uint32_t master_sweeps, bool force_restart)
{
    if (role != ANCHOR_ROLE_MASTER && role != ANCHOR_ROLE_MATRIX &&
        role != ANCHOR_ROLE_RESPONDER) {
        set_result_locked("ERR:BAD_RUNTIME_ROLE");
        return;
    }

    if (role == g_info.runtime_role && master_sweeps == 0U && !force_restart) {
        set_result_locked("OK RUNTIME_ALREADY_ACTIVE");
        return;
    }

    g_runtime_switch_pending = true;
    anchor_runtime_request_role_switch(role, master_sweeps);
    if (role == ANCHOR_ROLE_MASTER && master_sweeps != 0U) {
        char result[64];

        snprintk(result, sizeof(result), "OK RUNTIME_SWITCH_REQUESTED SWEEP=%lu",
                 (unsigned long)master_sweeps);
        set_result_locked(result);
    } else {
        set_result_locked(force_restart ? "OK RUNTIME_RESTART_REQUESTED" :
                          "OK RUNTIME_SWITCH_REQUESTED");
    }
}

static void process_control_cmd_locked(char *line)
{
    char *tok;
    char *savep = NULL;
    uint8_t parsed;
    unsigned long gen_val;
    char *endp;

    if (line == NULL || line[0] == '\0') {
        set_result_locked("ERR:BAD_CMD");
        return;
    }

    tok = strtok_r(line, " ", &savep);
    if (tok == NULL) {
        set_result_locked("ERR:BAD_CMD");
        return;
    }

    if (strcmp(tok, "HELP") == 0) {
        set_result_locked("OK CMDS=VERSION|PENDING LABEL|PENDING ROLE|PENDING GEN|VALIDATE|COMMIT|REBOOT|RESET AUTOPOS|RESET RESPONDER|STOP|DFU|SYNC|RUNTIME <MASTER|MATRIX|RESPONDER> [FORCE|SWEEP N]");
        return;
    }

    if (strcmp(tok, "VERSION") == 0) {
        char uuid_hex[33];

        bytes_to_hex(g_info.device_uuid, sizeof(g_info.device_uuid), uuid_hex, sizeof(uuid_hex));
        snprintk(g_result_text, sizeof(g_result_text),
                 "ANCHOR_FW fw=%s bs=%s uuid=%s label=%c role=%s cfg_valid=%u busy=%u",
                 g_info.fw_marker,
                 g_info.bs_code,
                 uuid_hex,
                 anchor_config_label_char(g_info.runtime_anchor_id_cfg),
                 role_name(g_info.runtime_role),
                 g_info.active_cfg_valid ? 1U : 0U,
                 g_busy ? 1U : 0U);
        return;
    }

    if (strcmp(tok, "SYNC") == 0) {
        g_pending_cfg = g_info.active_cfg;
        g_pending_valid = g_info.active_cfg_valid;
        set_result_locked("OK SYNC");
        return;
    }

    if (strcmp(tok, "VALIDATE") == 0) {
        handle_validate_locked();
        return;
    }

    if (strcmp(tok, "COMMIT") == 0 || strcmp(tok, "APPLY") == 0) {
        handle_commit_locked();
        return;
    }

    if (strcmp(tok, "REBOOT") == 0) {
        handle_reboot_locked();
        return;
    }

    if (strcmp(tok, "RUNTIME") == 0) {
        uint32_t master_sweeps = 0U;
        bool force_restart = false;

        tok = strtok_r(NULL, " ", &savep);
        if (tok == NULL || role_parse(tok, &parsed) != 0) {
            set_result_locked("ERR:BAD_RUNTIME_ROLE");
            return;
        }
        tok = strtok_r(NULL, " ", &savep);
        if (tok != NULL) {
            if (strcmp(tok, "FORCE") == 0 || strcmp(tok, "RESTART") == 0) {
                force_restart = true;
                if (strtok_r(NULL, " ", &savep) != NULL) {
                    set_result_locked("ERR:BAD_RUNTIME_ARG");
                    return;
                }
            } else if (strcmp(tok, "SWEEP") == 0) {
                tok = strtok_r(NULL, " ", &savep);
                if (tok == NULL) {
                    set_result_locked("ERR:BAD_RUNTIME_SWEEP");
                    return;
                }
                gen_val = strtoul(tok, &endp, 10);
                if (endp == tok || *endp != '\0' || gen_val == 0UL ||
                    gen_val > 10000UL) {
                    set_result_locked("ERR:INVALID_RUNTIME_SWEEP");
                    return;
                }
                if (strtok_r(NULL, " ", &savep) != NULL) {
                    set_result_locked("ERR:BAD_RUNTIME_ARG");
                    return;
                }
                if (parsed != ANCHOR_ROLE_MASTER) {
                    set_result_locked("ERR:SWEEP_REQUIRES_MASTER");
                    return;
                }
                master_sweeps = (uint32_t)gen_val;
            } else {
                set_result_locked("ERR:BAD_RUNTIME_ARG");
                return;
            }
        }
        handle_runtime_role_locked(parsed, master_sweeps, force_restart);
        return;
    }

    if (strcmp(tok, "RESET") == 0) {
        tok = strtok_r(NULL, " ", &savep);
        if (tok == NULL) {
            set_result_locked("ERR:BAD_CMD");
            return;
        }
        if (strcmp(tok, "AUTOPOS") == 0) {
            handle_reset_role_locked(ANCHOR_ROLE_MATRIX, "OK RESET_AUTOPOS REBOOT");
            return;
        }
        if (strcmp(tok, "RESPONDER") == 0) {
            handle_reset_role_locked(ANCHOR_ROLE_RESPONDER, "OK RESET_RESPONDER REBOOT");
            return;
        }
        set_result_locked("ERR:UNSUPPORTED");
        return;
    }

    if (strcmp(tok, "STOP") == 0) {
        anchor_runtime_request_stop();
        set_result_locked(g_busy ? "OK STOP_REQUESTED" : "OK STOP_IDLE");
        return;
    }

    if (strcmp(tok, "DFU") == 0 || strcmp(tok, "ENTER_DFU") == 0 ||
        strcmp(tok, "OTA") == 0 || strcmp(tok, "ENTER_OTA") == 0) {
        anchor_runtime_request_dfu();
        set_result_locked("OK DFU_REQUESTED");
        return;
    }

    if (strcmp(tok, "PENDING") == 0) {
        tok = strtok_r(NULL, " ", &savep);
        if (tok == NULL) {
            set_result_locked("ERR:BAD_CMD");
            return;
        }
        if (strcmp(tok, "LABEL") == 0) {
            tok = strtok_r(NULL, " ", &savep);
            if (label_parse(tok, &parsed) != 0) {
                set_result_locked("ERR:INVALID_LABEL");
                return;
            }
            g_pending_cfg.anchor_id = parsed;
            cfg_prepare(&g_pending_cfg);
            g_pending_valid = anchor_config_is_valid(&g_pending_cfg);
            set_result_locked("OK PENDING_LABEL");
            return;
        }
        if (strcmp(tok, "ROLE") == 0) {
            tok = strtok_r(NULL, " ", &savep);
            if (role_parse(tok, &parsed) != 0) {
                set_result_locked("ERR:INVALID_ROLE");
                return;
            }
            g_pending_cfg.role = parsed;
            cfg_prepare(&g_pending_cfg);
            g_pending_valid = anchor_config_is_valid(&g_pending_cfg);
            set_result_locked("OK PENDING_ROLE");
            return;
        }
        if (strcmp(tok, "GEN") == 0) {
            tok = strtok_r(NULL, " ", &savep);
            if (tok == NULL) {
                set_result_locked("ERR:BAD_CMD");
                return;
            }
            gen_val = strtoul(tok, &endp, 10);
            if (endp == tok || *endp != '\0') {
                set_result_locked("ERR:INVALID_GEN");
                return;
            }
            g_pending_cfg.generation = (uint32_t)gen_val;
            cfg_prepare(&g_pending_cfg);
            g_pending_valid = anchor_config_is_valid(&g_pending_cfg);
            set_result_locked("OK PENDING_GEN");
            return;
        }
        set_result_locked("ERR:UNSUPPORTED");
        return;
    }

    if (strcmp(tok, "R") == 0 || strcmp(tok, "ROLE") == 0) {
        tok = strtok_r(NULL, " ", &savep);
        if (role_parse(tok, &parsed) != 0) {
            set_result_locked("ERR:INVALID_ROLE");
            return;
        }
        g_pending_cfg.role = parsed;
        cfg_prepare(&g_pending_cfg);
        g_pending_valid = anchor_config_is_valid(&g_pending_cfg);
        set_result_locked("OK PENDING_ROLE");
        return;
    }

    if (strcmp(tok, "L") == 0 || strcmp(tok, "LABEL") == 0) {
        tok = strtok_r(NULL, " ", &savep);
        if (label_parse(tok, &parsed) != 0) {
            set_result_locked("ERR:INVALID_LABEL");
            return;
        }
        g_pending_cfg.anchor_id = parsed;
        cfg_prepare(&g_pending_cfg);
        g_pending_valid = anchor_config_is_valid(&g_pending_cfg);
        set_result_locked("OK PENDING_LABEL");
        return;
    }

    if (strcmp(tok, "G") == 0 || strcmp(tok, "GEN") == 0) {
        tok = strtok_r(NULL, " ", &savep);
        if (tok == NULL) {
            set_result_locked("ERR:BAD_CMD");
            return;
        }
        gen_val = strtoul(tok, &endp, 10);
        if (endp == tok || *endp != '\0') {
            set_result_locked("ERR:INVALID_GEN");
            return;
        }
        g_pending_cfg.generation = (uint32_t)gen_val;
        cfg_prepare(&g_pending_cfg);
        g_pending_valid = anchor_config_is_valid(&g_pending_cfg);
        set_result_locked("OK PENDING_GEN");
        return;
    }

    set_result_locked("ERR:BAD_CMD");
}

static ssize_t write_control_cb(struct bt_conn *conn, const struct bt_gatt_attr *attr,
                                const void *buf, uint16_t len, uint16_t offset, uint8_t flags)
{
    char cmd[BLE_CTRL_MAX_CMD];
    size_t i;
    ARG_UNUSED(attr);
    ARG_UNUSED(flags);

    if (offset != 0U || len == 0U || len >= sizeof(cmd)) {
        return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
    }

    memcpy(cmd, buf, len);
    cmd[len] = '\0';
    for (i = 0; i < len; i++) {
        cmd[i] = (char)toupper((unsigned char)cmd[i]);
    }

    k_mutex_lock(&g_lock, K_FOREVER);
    process_control_cmd_locked(cmd);
    refresh_text_locked();
    k_mutex_unlock(&g_lock);

    notify_result_if_possible();
    if (strstr(g_result_text, "REBOOT") != NULL &&
        strncmp(g_result_text, "ERR:", 4) != 0) {
        k_msleep(40);
        sys_reboot(SYS_REBOOT_COLD);
    }
    return len;
}

BT_GATT_SERVICE_DEFINE(anchor_ble_ctrl_svc,
    BT_GATT_PRIMARY_SERVICE(&g_svc_uuid),
    BT_GATT_CHARACTERISTIC(&g_state_uuid.uuid, BT_GATT_CHRC_READ | BT_GATT_CHRC_NOTIFY,
                           BT_GATT_PERM_READ, read_text_cb, NULL, g_state_text),
    BT_GATT_CCC(state_ccc_cfg_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
    BT_GATT_CHARACTERISTIC(&g_active_uuid.uuid, BT_GATT_CHRC_READ,
                           BT_GATT_PERM_READ, read_text_cb, NULL, g_active_text),
    BT_GATT_CHARACTERISTIC(&g_pending_uuid.uuid, BT_GATT_CHRC_READ,
                           BT_GATT_PERM_READ, read_text_cb, NULL, g_pending_text),
    BT_GATT_CHARACTERISTIC(&g_control_uuid.uuid,
                           BT_GATT_CHRC_WRITE | BT_GATT_CHRC_WRITE_WITHOUT_RESP,
                           BT_GATT_PERM_WRITE, NULL, write_control_cb, NULL),
    BT_GATT_CHARACTERISTIC(&g_result_uuid.uuid, BT_GATT_CHRC_READ | BT_GATT_CHRC_NOTIFY,
                           BT_GATT_PERM_READ, read_text_cb, NULL, g_result_text),
    BT_GATT_CCC(result_ccc_cfg_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE)
);

static void notify_result_if_possible(void)
{
    int rc;

    if (g_last_conn == NULL || !g_result_notify_enabled) {
        return;
    }

    rc = bt_gatt_notify(g_last_conn,
                        &anchor_ble_ctrl_svc.attrs[ANCHOR_BLE_CTRL_RESULT_ATTR_INDEX],
                        g_result_text,
                        strlen(g_result_text));
    if (rc != 0) {
        printk("anchor BLE ctrl result notify failed: %d\n", rc);
    }
}

static void connected_cb(struct bt_conn *conn, uint8_t err)
{
    if (err == 0U) {
        g_last_conn = bt_conn_ref(conn);
        printk("anchor BLE ctrl connected\n");
    }
}

static void disconnected_cb(struct bt_conn *conn, uint8_t reason)
{
    ARG_UNUSED(reason);
    if (g_last_conn == conn) {
        bt_conn_unref(g_last_conn);
        g_last_conn = NULL;
    }
    printk("anchor BLE ctrl disconnected\n");
}

BT_CONN_CB_DEFINE(anchor_ble_ctrl_conn_cb) = {
    .connected = connected_cb,
    .disconnected = disconnected_cb,
};

int anchor_ble_ctrl_init(const struct anchor_ble_ctrl_boot_info *info)
{
    if (info == NULL) {
        return -EINVAL;
    }
    k_mutex_init(&g_lock);
    k_mutex_lock(&g_lock, K_FOREVER);
    g_info = *info;
    g_pending_cfg = info->active_cfg;
    if (g_pending_cfg.schema_version == 0U) {
        g_pending_cfg.schema_version = ANCHOR_CONFIG_SCHEMA_VERSION;
    }
    if (g_pending_cfg.magic == 0U) {
        g_pending_cfg.magic = CONFIG_MAGIC;
    }
    cfg_prepare(&g_pending_cfg);
    g_pending_valid = anchor_config_is_valid(&g_pending_cfg);
    g_busy = false;
    g_reboot_required = false;
    g_runtime_switch_pending = false;
    g_result_notify_enabled = false;
    g_state_notify_enabled = false;
    set_result_locked("OK READY");
    refresh_text_locked();
    k_mutex_unlock(&g_lock);
    printk("anchor BLE ctrl ready schema=%u gen=%lu\n",
           (unsigned int)g_info.active_cfg.schema_version,
           (unsigned long)g_info.active_cfg.generation);
    return 0;
}

void anchor_ble_ctrl_set_busy(bool busy)
{
    k_mutex_lock(&g_lock, K_FOREVER);
    g_busy = busy;
    refresh_text_locked();
    k_mutex_unlock(&g_lock);
}

void anchor_ble_ctrl_set_runtime(uint8_t runtime_anchor_id_cfg, uint8_t runtime_role,
                                 bool active_cfg_valid)
{
    k_mutex_lock(&g_lock, K_FOREVER);
    g_info.runtime_anchor_id_cfg = runtime_anchor_id_cfg;
    g_info.runtime_role = runtime_role;
    g_info.active_cfg_valid = active_cfg_valid;
    refresh_text_locked();
    k_mutex_unlock(&g_lock);
}

void anchor_ble_ctrl_set_runtime_switch_pending(bool pending)
{
    k_mutex_lock(&g_lock, K_FOREVER);
    g_runtime_switch_pending = pending;
    refresh_text_locked();
    k_mutex_unlock(&g_lock);
}

int anchor_ble_ctrl_publish_result_line(const char *text)
{
    if (text == NULL || text[0] == '\0') {
        return -EINVAL;
    }

    k_mutex_lock(&g_lock, K_FOREVER);
    set_result_locked(text);
    k_mutex_unlock(&g_lock);

    notify_result_if_possible();
    return 0;
}
