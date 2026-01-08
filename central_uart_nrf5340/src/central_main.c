/*
 * Central-only NUS Aggregator (命令控制版 + NUS client)
 * nRF Connect SDK 2.8.0 / Zephyr 3.7.x
 *
 * 职责：USB CDC + 命令线程 + 蓝牙核心初始化
 * BLE/NUS/扫描/连接逻辑已拆分到 app_ble.c / app_ble.h
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/usb/usb_device.h>
#include <zephyr/sys/util.h>
#include <zephyr/settings/settings.h>
#include <zephyr/bluetooth/bluetooth.h>

#include <dk_buttons_and_leds.h>

#include <string.h>
#include <stdarg.h>
#include <stdio.h>

#include "app_ble.h"
#include "app_cmd.h"

/* ===== 全局 CDC 设备 ===== */
static const struct device *cdc = DEVICE_DT_GET_ONE(zephyr_cdc_acm_uart);

/* CDC 命令线程同步 */
K_SEM_DEFINE(cdc_ready_sem, 0, 1);

/* ===== CDC 打印函数，供 BLE / CMD 模块使用 ===== */
void cdc_printf(const char *fmt, ...)
{
    char buf[256];

    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintk(buf, sizeof(buf), fmt, ap);
    va_end(ap);

    if (n > 0) {
        for (int i = 0; i < n; i++) {
            uart_poll_out(cdc, buf[i]);
        }
    }
}

/* ===== CDC 命令解析线程 ===== */
static void cdc_rx_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a);
    ARG_UNUSED(b);
    ARG_UNUSED(c);

    /* 等 main 里把 USB / CDC 初始化好 */
    k_sem_take(&cdc_ready_sem, K_FOREVER);

    char buf[32];
    int  pos = 0;

    while (1) {
        uint8_t ch;
        int ret = uart_poll_in(cdc, &ch);
        if (ret == 0) {
            /* 遇到换行就当一条命令结束（兼容有 \r/\n 的情况） */
            if (ch == '\r' || ch == '\n') {
                if (pos > 0) {
                    buf[pos] = '\0';
                    app_cmd_handle(buf);
                    pos = 0;
                }
                continue;
            }

            /* 普通字符累积到缓冲区 */
            if (pos < (int)sizeof(buf) - 1) {
                buf[pos++] = (char)ch;
                buf[pos]   = '\0';
            }

            /* 不依赖换行：一旦看到 "scan" 或 "conn" 就立刻触发 */
            if (pos >= 4) {
                if (strncmp(buf, "scan", 4) == 0) {
                    app_cmd_handle("scan");
                    pos = 0;
                    continue;
                }
                if (strncmp(buf, "conn", 4) == 0) {
                    app_cmd_handle("conn");
                    pos = 0;
                    continue;
                }
            }
        } else {
            k_msleep(10);
        }
    }
}

K_THREAD_DEFINE(cdc_rx_tid, 1024, cdc_rx_thread, NULL, NULL, NULL,
                5, 0, 0);

/* ===== 主函数 ===== */
int main(void)
{
    /* DK 按键初始化（如果不用按键，这行其实也可以删掉） */
    dk_buttons_init(NULL);

    /* USB CDC 初始化 */
    usb_enable(NULL);
    if (!device_is_ready(cdc)) {
        return 0;
    }
    cdc_printf("CDC ready\n");

    /* 告诉 cdc_rx_thread：CDC 可以用了 */
    k_sem_give(&cdc_ready_sem);

    /* 蓝牙初始化 */
    int berr = bt_enable(NULL);
    cdc_printf("bt_enable -> %d\n", berr);
    if (berr) {
        cdc_printf("Bluetooth init failed\n");
        return 0;
    }

    settings_load();
    cdc_printf("BT initialized.\n");

    /* 初始化我们自己的 BLE 模块（NUS client / scan / 回调等） */
    app_ble_init();

    cdc_printf("Central-only NUS aggregator CMD-mode + NUS: boot OK\n");
    cdc_printf("Use 'scan' to start scanning, 'conn' to connect all.\n");

    while (1) {
        /* 如果以后想恢复统计输出，可以在这里加一个 app_ble_stats_tick() 一类接口 */
        k_sleep(K_SECONDS(1));
    }

    return 0;
}
