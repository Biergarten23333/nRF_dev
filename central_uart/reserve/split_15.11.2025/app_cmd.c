#include <zephyr/kernel.h>
#include <zephyr/sys/reboot.h>
#include <string.h>
#include <stdio.h>

#include "cdc_async.h"
#include "app_ble.h"
#include "app_cmd.h"

K_THREAD_STACK_DEFINE(app_cmd_stack, 1024);
static struct k_thread app_cmd_thread;

static void handle_cmd(const char *cmd)
{
    if (strcmp(cmd, "scan") == 0) {
        cdc_printf("CMD: scan\r\n");
        app_ble_start_scan();

    } else if (strcmp(cmd, "conn") == 0) {
        cdc_printf("CMD: conn\r\n");
        app_ble_connect_whitelist();

    } else if (strcmp(cmd, "reboot") == 0) {
        cdc_printf("CMD: reboot -> sys_reboot\r\n");
        sys_reboot(SYS_REBOOT_COLD);

    } else if (strncmp(cmd, "TSYNC", 5) == 0) {
        uint64_t host_ms = 0;
        int n = sscanf(cmd + 5, "%llu", (unsigned long long *)&host_ms);
        if (n == 1) {
            app_ble_tsync_all(host_ms);
        } else {
            cdc_printf("CMD TSYNC parse error. Usage: TSYNC <host_ms>\r\n");
        }

    } else if (strncmp(cmd, "RSLOT", 5) == 0) {
        int slot = 0;
        unsigned int fps = 0, phase_ms = 0;
        int n = sscanf(cmd + 5, "%d %u %u", &slot, &fps, &phase_ms);
        if (n == 3) {
            app_ble_rslot(slot, (uint16_t)fps, (uint16_t)phase_ms);
        } else {
            cdc_printf("CMD RSLOT parse error. Usage: RSLOT <slot> <fps> <phase_ms>\r\n");
        }

    } else {
        cdc_printf("CMD: unknown '%s'\r\n", cmd);
    }
}

static void app_cmd_thread_fn(void *a, void *b, void *c)
{
    ARG_UNUSED(a);
    ARG_UNUSED(b);
    ARG_UNUSED(c);

    char buf[32];
    int  pos = 0;

    while (1) {
        uint8_t ch;
        int ret = cdc_async_poll_in(&ch);
        if (ret == 0) {
            if (ch == '\r' || ch == '\n') {
                if (pos > 0) {
                    buf[pos] = '\0';
                    handle_cmd(buf);
                    pos = 0;
                }
                continue;
            }

            if (pos < (int)sizeof(buf) - 1) {
                buf[pos++] = (char)ch;
                buf[pos]   = '\0';
            }

            if (pos >= 4) {
                if (strncmp(buf, "scan", 4) == 0) {
                    handle_cmd("scan");
                    pos = 0;
                    continue;
                }
                if (strncmp(buf, "conn", 4) == 0) {
                    handle_cmd("conn");
                    pos = 0;
                    continue;
                }
            }
        } else {
            k_msleep(10);
        }
    }
}

int app_cmd_init(void)
{
    k_thread_create(&app_cmd_thread,
                    app_cmd_stack,
                    K_THREAD_STACK_SIZEOF(app_cmd_stack),
                    app_cmd_thread_fn,
                    NULL, NULL, NULL,
                    5, 0, K_NO_WAIT);

    k_thread_name_set(&app_cmd_thread, "app_cmd");
    return 0;
}
