#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <dk_buttons_and_leds.h>

#include "cdc_async.h"
#include "app_ble.h"
#include "app_cmd.h"

/* BTN 映射:
 *  BTN1: 切换扫描 start/stop
 *  BTN2: 对已扫描到的 eFxx 设备批量连接
 *  BTN3: 切换二进制数据流 ON/OFF (CDC)
 *  BTN4: 断开所有连接
 */

static bool g_scan_running = false;
static bool g_stream_on    = false;

static void button_handler(uint32_t button_state, uint32_t has_changed)
{
    uint32_t pressed = button_state & has_changed;

    if (pressed & DK_BTN1_MSK) {
        if (!g_scan_running) {
            cdc_printf("BTN1: start scan\r\n");
            int err = app_ble_start_scan();
            if (!err) {
                g_scan_running = true;
                dk_set_led_on(DK_LED1);
            } else {
                cdc_printf("app_ble_start_scan err %d\r\n", err);
            }
        } else {
            cdc_printf("BTN1: stop scan\r\n");
            int err = app_ble_stop_scan();
            g_scan_running = false;
            dk_set_led_off(DK_LED1);
            if (err) {
                cdc_printf("app_ble_stop_scan err %d\r\n", err);
            }
        }
    }

    if (pressed & DK_BTN2_MSK) {
        cdc_printf("BTN2: connect all candidates\r\n");
        app_ble_connect_whitelist();
    }

    if (pressed & DK_BTN3_MSK) {
        g_stream_on = !g_stream_on;
        cdc_async_enable(g_stream_on);
        cdc_printf("BTN3: stream %s\r\n", g_stream_on ? "ON" : "OFF");
        if (g_stream_on) {
            dk_set_led_on(DK_LED2);
        } else {
            dk_set_led_off(DK_LED2);
        }
    }

    if (pressed & DK_BTN4_MSK) {
        cdc_printf("BTN4: disconnect all\r\n");
        app_ble_disconnect_all();
        dk_set_led_off(DK_LED2);
    }
}

int main(void)
{
    int err;

    err = cdc_async_init();
    if (err) {
        /* 警告一下，但不要退出，让后面的 BLE / 按钮照样跑 */
        printk("cdc_async_init failed: %d\n", err);
    }

    cdc_printf("\r\n=== BioSpur Central Multi-NUS (buttons + CMD) ===\r\n");

    err = dk_buttons_init(button_handler);
    if (err) {
        cdc_printf("dk_buttons_init err %d\r\n", err);
    }

    err = dk_leds_init();
    if (err) {
        cdc_printf("dk_leds_init err %d\r\n", err);
    }

    err = app_ble_init();
    if (err) {
        cdc_printf("app_ble_init err %d\r\n", err);
    }

    app_cmd_init();

    cdc_printf("Boot OK. BTN1=scan, BTN2=conn, BTN3=stream toggle, BTN4=disconnect.\r\n");

    while (1) {
        k_sleep(K_SECONDS(1));
    }

    return 0;
}
