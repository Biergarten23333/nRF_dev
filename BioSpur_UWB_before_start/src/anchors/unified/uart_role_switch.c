#include "uart_role_switch.h"

#include <ctype.h>
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/reboot.h>

#ifndef APP_ANCHOR_SERIAL_CMD_ENABLE
#define APP_ANCHOR_SERIAL_CMD_ENABLE 1U
#endif

#ifndef APP_ANCHOR_SERIAL_CMD_BOOT_WINDOW_MS
#define APP_ANCHOR_SERIAL_CMD_BOOT_WINDOW_MS 5000U
#endif

#define UART_ROLE_STACK_SIZE 2048
#define UART_ROLE_THREAD_PRIO 12
#define UART_RX_MAX 192
#define UART_RX_IDLE_FLUSH_MS 300

static const struct device *g_uart_dev;
static struct k_thread g_uart_thread;
K_THREAD_STACK_DEFINE(g_uart_stack, UART_ROLE_STACK_SIZE);
static struct k_mutex g_cfg_lock;
static volatile bool g_ranging_active;
static volatile bool g_running;

static anchor_config_t g_working_cfg;
static bool g_working_cfg_valid;
static struct uart_role_switch_runtime_info g_runtime_info;

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

static int role_parse(const char *s, uint8_t *out_role)
{
    if (s == NULL || out_role == NULL) {
        return -EINVAL;
    }
    if (strncmp(s, "MASTER", 6) == 0) {
        *out_role = ANCHOR_ROLE_MASTER;
        return 0;
    }
    if (strncmp(s, "MATRIX", 6) == 0) {
        *out_role = ANCHOR_ROLE_MATRIX;
        return 0;
    }
    if (strncmp(s, "RESPONDER", 9) == 0) {
        *out_role = ANCHOR_ROLE_RESPONDER;
        return 0;
    }
    return -EINVAL;
}

static int anchor_parse(const char *s, uint8_t *out_anchor_id)
{
    char c;
    if (s == NULL || out_anchor_id == NULL || strlen(s) != 1U) {
        return -EINVAL;
    }
    c = (char)toupper((unsigned char)s[0]);
    if (c < 'A' || c > 'H') {
        return -EINVAL;
    }
    *out_anchor_id = (uint8_t)((c - 'A') + 1U);
    return 0;
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

static void uart_txf(const char *fmt, ...)
{
    char buf[192];
    va_list ap;
    size_t i;
    int n;

    if (g_uart_dev == NULL) {
        return;
    }

    va_start(ap, fmt);
    n = vsnprintk(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n <= 0) {
        return;
    }

    for (i = 0; i < (size_t)n && i < sizeof(buf); i++) {
        uart_poll_out(g_uart_dev, (unsigned char)buf[i]);
    }
}

static void uart_reply_ok(void)
{
    uart_txf("OK\r\n");
}

static void uart_reply_err(const char *reason)
{
    uart_txf("ERR:%s\r\n", reason);
}

static void cfg_fill_crc(anchor_config_t *cfg)
{
    cfg->magic = CONFIG_MAGIC;
    cfg->reserved[0] = 0U;
    cfg->reserved[1] = 0U;
    cfg->crc32 = anchor_config_crc32((const uint8_t *)cfg,
                                     offsetof(anchor_config_t, crc32));
}

static void cmd_role_query(void)
{
    anchor_config_t local;
    bool valid;
    char anchor_label = '?';

    k_mutex_lock(&g_cfg_lock, K_FOREVER);
    local = g_working_cfg;
    valid = g_working_cfg_valid;
    k_mutex_unlock(&g_cfg_lock);

    if (local.anchor_id >= 1U && local.anchor_id <= 8U) {
        anchor_label = (char)('A' + (local.anchor_id - 1U));
    }

    uart_txf("ROLE:%s ANCHOR:%c VALID:%u\r\n",
             role_name(local.role),
             anchor_label,
             valid ? 1U : 0U);
}

static void cmd_status(void)
{
    char uuid_hex[33];
    char uid_hex[17];

    bytes_to_hex(g_runtime_info.device_uuid, sizeof(g_runtime_info.device_uuid),
                 uuid_hex, sizeof(uuid_hex));
    bytes_to_hex(g_runtime_info.mcu_uid, sizeof(g_runtime_info.mcu_uid),
                 uid_hex, sizeof(uid_hex));

    uart_txf("ANCHOR: unified; ANCHOR_ID: %c; ROLE: %s; BS_CODE: %s; DEVICE_UUID: %s; MCU_UID: 0x%s; CONFIG_VALID:%u\r\n",
             (char)('A' + g_runtime_info.active_anchor_id_runtime),
             role_name(g_runtime_info.active_role),
             g_runtime_info.bs_code,
             uuid_hex,
             uid_hex,
             g_runtime_info.cfg_valid_on_boot ? 1U : 0U);
}

static void cmd_role_set(const char *role_s)
{
    uint8_t role;
    if (role_parse(role_s, &role) != 0) {
        uart_reply_err("BAD_ROLE");
        return;
    }

    k_mutex_lock(&g_cfg_lock, K_FOREVER);
    g_working_cfg.role = role;
    cfg_fill_crc(&g_working_cfg);
    g_working_cfg_valid = anchor_config_is_valid(&g_working_cfg);
    k_mutex_unlock(&g_cfg_lock);
    uart_reply_ok();
}

static void cmd_anchor_set(const char *anchor_s)
{
    uint8_t anchor_id;
    if (anchor_parse(anchor_s, &anchor_id) != 0) {
        uart_reply_err("BAD_ANCHOR");
        return;
    }

    k_mutex_lock(&g_cfg_lock, K_FOREVER);
    g_working_cfg.anchor_id = anchor_id;
    cfg_fill_crc(&g_working_cfg);
    g_working_cfg_valid = anchor_config_is_valid(&g_working_cfg);
    k_mutex_unlock(&g_cfg_lock);
    uart_reply_ok();
}

static void cmd_config_save(void)
{
    anchor_config_t local;
    int rc;

    if (g_ranging_active) {
        uart_reply_err("BUSY");
        return;
    }

    k_mutex_lock(&g_cfg_lock, K_FOREVER);
    local = g_working_cfg;
    k_mutex_unlock(&g_cfg_lock);

    cfg_fill_crc(&local);
    rc = anchor_config_write(&local);
    if (rc != 0) {
        uart_reply_err("WRITE_FAIL");
        return;
    }

    k_mutex_lock(&g_cfg_lock, K_FOREVER);
    g_working_cfg = local;
    g_working_cfg_valid = true;
    k_mutex_unlock(&g_cfg_lock);
    uart_reply_ok();
}

static void cmd_reboot(void)
{
    uart_reply_ok();
    k_msleep(40);
    sys_reboot(SYS_REBOOT_COLD);
}

static void process_line(char *line)
{
    char role_buf[16];
    char anchor_buf[4];
    size_t i;
    size_t w = 0U;

    for (i = 0; line[i] != '\0'; i++) {
        unsigned char c = (unsigned char)line[i];
        if (isprint(c)) {
            line[w++] = (char)c;
        }
    }
    line[w] = '\0';

    for (i = 0; line[i] != '\0'; i++) {
        line[i] = (char)toupper((unsigned char)line[i]);
    }

    if (line[0] == '\0') {
        return;
    }

    if (strcmp(line, "ROLE?") == 0 || strcmp(line, "ROLE") == 0) {
        cmd_role_query();
        return;
    }
    if (strncmp(line, "STATUS", 6) == 0) {
        cmd_status();
        return;
    }
    if (strcmp(line, "M") == 0 || strcmp(line, "MASTER") == 0) {
        cmd_role_set("MASTER");
        return;
    }
    if (strcmp(line, "X") == 0 || strcmp(line, "MATRIX") == 0) {
        cmd_role_set("MATRIX");
        return;
    }
    if (strcmp(line, "P") == 0 || strcmp(line, "RESPONDER") == 0) {
        cmd_role_set("RESPONDER");
        return;
    }
    if (strncmp(line, "ID ", 3) == 0) {
        if (sscanf(line + 3, "%3s", anchor_buf) == 1) {
            cmd_anchor_set(anchor_buf);
        } else {
            uart_reply_err("BAD_ANCHOR");
        }
        return;
    }
    if (strcmp(line, "S") == 0 || strcmp(line, "SAVE") == 0) {
        cmd_config_save();
        return;
    }
    if (strcmp(line, "RB") == 0) {
        cmd_reboot();
        return;
    }
    if (strncmp(line, "ROLE SET ", 9) == 0) {
        if (sscanf(line + 9, "%15s", role_buf) == 1) {
            cmd_role_set(role_buf);
        } else {
            uart_reply_err("BAD_ROLE");
        }
        return;
    }
    if (strncmp(line, "ANCHOR SET ", 11) == 0) {
        if (sscanf(line + 11, "%3s", anchor_buf) == 1) {
            cmd_anchor_set(anchor_buf);
        } else {
            uart_reply_err("BAD_ANCHOR");
        }
        return;
    }
    if (strncmp(line, "CONFIG SAVE", 11) == 0) {
        cmd_config_save();
        return;
    }
    if (strncmp(line, "REBOOT", 6) == 0) {
        cmd_reboot();
        return;
    }
    uart_reply_err("BAD_CMD");
}

static void uart_role_thread(void *p1, void *p2, void *p3)
{
    char line[UART_RX_MAX];
    size_t pos = 0U;
    unsigned char ch;
    int64_t last_rx_ms = 0;
    bool rx_unsupported_logged = false;
    ARG_UNUSED(p1);
    ARG_UNUSED(p2);
    ARG_UNUSED(p3);

    while (g_running) {
        int rc = uart_poll_in(g_uart_dev, &ch);
        if (rc != 0) {
            if (pos > 0U && last_rx_ms > 0 &&
                (k_uptime_get() - last_rx_ms) >= UART_RX_IDLE_FLUSH_MS) {
                line[pos] = '\0';
                process_line(line);
                pos = 0U;
                last_rx_ms = 0;
            }
            if (!rx_unsupported_logged && (rc == -ENOTSUP || rc == -ENOSYS)) {
                printk("uart role switch: UART RX not supported (rc=%d)\n", rc);
                rx_unsupported_logged = true;
            }
            k_msleep(5);
            continue;
        }

        if (ch == '\r' || ch == '\n') {
            if (pos > 0U) {
                line[pos] = '\0';
                process_line(line);
                pos = 0U;
            }
            last_rx_ms = 0;
            continue;
        }

        if (pos < (sizeof(line) - 1U)) {
            line[pos++] = (char)ch;
            last_rx_ms = k_uptime_get();
        } else {
            pos = 0U;
            last_rx_ms = 0;
            uart_reply_err("LINE_TOO_LONG");
        }
    }
}

int uart_role_switch_init(const struct uart_role_switch_runtime_info *info,
                          const anchor_config_t *initial_cfg,
                          bool initial_cfg_valid)
{
    if (APP_ANCHOR_SERIAL_CMD_ENABLE == 0U) {
        return 0;
    }
    if (info == NULL || initial_cfg == NULL) {
        return -EINVAL;
    }

    g_uart_dev = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
    if (!device_is_ready(g_uart_dev)) {
        printk("uart role switch disabled: console not ready\n");
        return -ENODEV;
    }

    k_mutex_init(&g_cfg_lock);
    g_runtime_info = *info;
    g_working_cfg = *initial_cfg;
    g_working_cfg_valid = initial_cfg_valid;
    g_ranging_active = false;
    g_running = true;

    k_thread_create(&g_uart_thread,
                    g_uart_stack,
                    K_THREAD_STACK_SIZEOF(g_uart_stack),
                    uart_role_thread,
                    NULL, NULL, NULL,
                    UART_ROLE_THREAD_PRIO, 0, K_NO_WAIT);
    k_thread_name_set(&g_uart_thread, "uart_role_switch");

    uart_txf("UART role switch ready (boot_window_ms=%u)\r\n",
             (unsigned int)APP_ANCHOR_SERIAL_CMD_BOOT_WINDOW_MS);
    return 0;
}

void uart_role_switch_set_ranging_active(bool active)
{
    g_ranging_active = active;
}
