
/* app_cmd.c
 *
 * 字符串命令解析：scan / conn / reboot / TSYNC / RSLOT
 */

#include <string.h>
#include <stdio.h>
#include <stdint.h>

#include <zephyr/sys/reboot.h>

#include "app_ble.h"
#include "app_cmd.h"

/* 由 main.c 实现 */
void cdc_printf(const char *fmt, ...);

void app_cmd_handle(const char *cmd)
{
    if (strcmp(cmd, "scan") == 0) {
        cdc_printf("CMD: scan\n");
        app_ble_start_scan();

    } else if (strcmp(cmd, "conn") == 0) {
        cdc_printf("CMD: conn\n");
        app_ble_connect_all_candidates();

    } else if (strcmp(cmd, "reboot") == 0) {
        cdc_printf("CMD: reboot -> sys_reboot\n");
        sys_reboot(SYS_REBOOT_COLD);

    } else if (strncmp(cmd, "TSYNC", 5) == 0) {
        /* 格式：TSYNC <host_ms> */
        uint64_t host_ms = 0;
        int n = sscanf(cmd + 5, "%llu", (unsigned long long *)&host_ms);
        if (n == 1) {
            app_ble_cmd_tsync(host_ms);
        } else {
            cdc_printf("CMD TSYNC parse error. Usage: TSYNC <host_ms>\n");
        }

    } else if (strncmp(cmd, "RSLOT", 5) == 0) {
        /* 格式：RSLOT <slot> <fps> <phase_ms> */
        int slot = 0;
        unsigned int fps = 0, phase_ms = 0;
        int n = sscanf(cmd + 5, "%d %u %u", &slot, &fps, &phase_ms);
        if (n == 3) {
            app_ble_cmd_rslot(slot, (uint16_t)fps, (uint16_t)phase_ms);
        } else {
            cdc_printf("CMD RSLOT parse error. Usage: RSLOT <slot> <fps> <phase_ms>\n");
        }

    } else {
        cdc_printf("CMD: unknown '%s'\n", cmd);
    }
}
